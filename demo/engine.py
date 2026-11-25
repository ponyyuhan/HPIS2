"""Runs one question through the lanes concurrently and yields UI events.

Lanes
  plain  端侧直传普通云: upload the whole collection of the asked kind in 16 MB batches;
         the cloud filters plaintext, shortlists lexically and computes vectors on the fly.
  v1     HPIS 1.0 (mail only, HPIS_DEMO_V1=1): HPIC fetches and decrypts every record.
  v2     端侧+HPIC+HPIS: SSE + encrypted vector search inside HPIS, HPIC opens only K records.

The question goes to the device whose data it asks about (PC mail, 手机 photos,
手表 watch records, 车机 car records); each device queries HPIS under its own
anonymous AID with its own keys.

Event times (`t`, ms) are measured from the click on one clock (the terminal's).
Compute is real; device-cloud and cloud-cloud wires are emulated by bandwidth + RTT
(config.Network).

Never hang: a run is a task of its own that never waits for the page reading it.
It ends within ``config.RUN_LIMIT_S``; ``stop()`` (POST /api/stop) or a new
question ends it at once.  Either way every unfinished lane gets
``finish {stopped | timed_out}``, the stream gets ``end``, the plain lane's
thread and the cloud worker's vector loop are cancelled, and the next run can
start immediately.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import threading
import time
import traceback

import aiohttp
import numpy as np

from hpis.codec import pack, unit_vectors, unpack

from . import config, planner
from .cluster import Cluster, device_tenants
from .devices import BY_KIND, Catalog
from .mailbox import Mailbox
from .netem import now, pace
from .uploads import Uploader


class Embedder:
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            from .embed import Embedder as Model

            cls._instance = Model()
        return cls._instance


class Run:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.t0 = now()

    def t(self) -> float:
        return round((now() - self.t0) * 1000, 2)

    def put(self, event: dict):
        event.setdefault("t", self.t())
        self.queue.put_nowait(event)


class Control:
    """One run in progress and the handles needed to stop it from anywhere."""

    def __init__(self, run: Run):
        self.run = run
        self.out: asyncio.Queue = asyncio.Queue()  # events for the page
        self.stop_event = asyncio.Event()
        self.done = asyncio.Event()
        self.reason: str | None = None  # stopped | timeout | preempted | closed
        self.cancelled = False  # read by the plain lane's thread
        self.plain = None  # (event loop, task) of the plain lane's thread
        self.plain_sid = None  # the ordinary cloud's session of this run
        self.lock = threading.Lock()
        self.task: asyncio.Task | None = None

    def stop(self, reason: str = "stopped"):
        if self.reason is None:
            self.reason = reason
        with self.lock:
            self.cancelled = True
            plain = self.plain
        if plain is not None:
            loop, task = plain
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:  # that loop has already finished
                pass
        self.stop_event.set()


async def _iter_ndjson(response: aiohttp.ClientResponse):
    buffer = b""
    async for chunk in response.content.iter_any():
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            if raw.strip():
                yield len(raw) + 1, json.loads(raw)
    if buffer.strip():
        yield len(buffer), json.loads(buffer)


class Engine:
    def __init__(self, cluster: Cluster, mailbox: Mailbox, build: dict, catalog: Catalog | None = None):
        self.cluster = cluster
        self.mailbox = mailbox
        self.build = build
        self.catalog = catalog or Catalog(mailbox)
        built = set((build.get("kinds") or {})) & set((build.get("uploads") or {}))
        for kind in list(self.catalog.sets):
            if kind not in built:
                self.catalog.sets.pop(kind)  # data present but not provisioned: not queryable
        self.devices = {}  # deployment key -> the PC (mail) device, as before
        self.tenants = {}  # (deployment key, tenant) -> device
        self.active: Control | None = None
        self.uploads = {"mail": Uploader("mail", build["upload"], mailbox.path)}
        for kind, upload in (build.get("uploads") or {}).items():
            if kind != "mail" and kind in self.catalog.sets:
                rs = self.catalog.sets[kind]
                self.uploads[kind] = Uploader(kind, upload, rs.path, rs)

    async def open(self):
        for key in self.cluster.hpic_url:
            names = ["default"] if key.endswith("-v1") else device_tenants(key.split("-")[0])
            for tenant in names:
                device = self.cluster.device(key, tenant)
                await device.__aenter__()
                self.tenants[(key, tenant)] = device
            self.devices[key] = self.tenants[(key, "default")]
        Embedder.get()  # load the on-device embedding model once
        loop = asyncio.get_running_loop()
        for kind in self.catalog.kinds:  # the device-side field index used for planning
            await loop.run_in_executor(None, self.catalog.index, kind)
        return self

    async def close(self):
        ctl = self.active
        if ctl is not None and not ctl.done.is_set():
            ctl.stop("closed")
            await asyncio.wait({ctl.task}, timeout=5)
        for device in self.tenants.values():
            await device.__aexit__(None, None, None)
        self.mailbox.close()

    def tenant(self, key: str, kind: str):
        return self.tenants[(key, BY_KIND[kind].tenant)]

    def refresh_tickets(self):
        """Pick up tickets renewed on disk (they are valid for one day)."""
        from nacl.signing import SigningKey

        from hpis.provision import read_config

        for device in self.tenants.values():
            cfg = read_config(device.folder)
            device.ticket, device.holder = cfg["ticket"], SigningKey(cfg["holder"])

    # ------------------------------------------------------------------ shared
    @property
    def presets(self) -> list[dict]:
        return self.catalog.presets()

    def plan(self, question: str) -> dict:
        plan = planner.plan_question(question, self.presets, self.mailbox.bible, catalog=self.catalog)
        kind = plan["kind"]
        if not self.catalog.has(kind):  # a preset of a device that is not provisioned
            plan = planner.finalize({"kind": "mail", "semantic": plan.get("semantic") or question})
            kind = "mail"
        plan = planner.relax(plan, lambda p: self.catalog.matching(kind, p))
        plan["total"] = self.catalog.count(kind)
        return plan

    def embed(self, plan: dict) -> np.ndarray:
        vec = Embedder.get().encode_query(plan.get("semantic") or "")
        return unit_vectors(np.asarray(vec, dtype=np.float64), len(vec))

    async def vision(self, model: str) -> bool:
        """Both lanes' model servers accept images (else both get captions only)."""
        from . import llm

        try:
            hpis = llm.resolve_endpoint(model)
            plain_env = llm.lane_env("plain")
            plain = llm.resolve_endpoint(model, plain_env)
            if llm.vision_probed(hpis) and llm.vision_probed(plain, plain_env):
                # Known (the server probes at start and every 10 min): no network on the question's path.
                known = (llm.vision_cached(hpis), llm.vision_cached(plain, plain_env))
                if None in known:
                    # The last probe could not tell (server slow or down): ask again in the background,
                    # so the next photo question gets the answer without waiting for the 10-minute refresh.
                    llm.reprobe_vision((hpis, None), (plain, plain_env))
                return all(v is True for v in known)
            results = await asyncio.wait_for(
                asyncio.gather(llm.probe_vision(hpis), llm.probe_vision(plain, env=plain_env)), 4
            )
            return all(r is True for r in results)
        except (asyncio.TimeoutError, llm.LLMConfigError, ValueError):
            return False

    async def warmup(self):
        """Open the encrypted vector index inside HPIS once per tenant (first-query cost)."""
        # A filtered query also loads HPIS' in-memory posting lists.
        plan = planner.finalize({"semantic": "项目会议纪要", "date_from": "2026-11-01", "date_to": "2026-11-20"})
        unit = self.embed(plan)
        for (key, tenant), device in self.tenants.items():
            if key.endswith("-v1"):
                continue  # the 1.0 lane never uses the vector index
            op = device.vector_op(unit, config.RETRIEVE_K, plan)
            await device.request(op, {"dek": device.dek})

    # ------------------------------------------------------------------ lanes
    async def lane_plain(self, ctl: Control, question, plan, unit, model, net, extras):
        """Runs on its own thread + event loop: TLS-encrypting hundreds of MB of
        uploads must not stall the event loop that relays the other lanes."""
        loop = asyncio.get_running_loop()
        run = ctl.run

        def emit(event):
            event.setdefault("t", run.t())
            try:
                loop.call_soon_threadsafe(run.queue.put_nowait, event)
            except RuntimeError:
                pass  # the server is shutting down

        def worker():
            inner = asyncio.new_event_loop()
            try:
                task = inner.create_task(self._plain(emit, ctl, question, plan, unit, model, net, extras))
                with ctl.lock:
                    ctl.plain = (inner, task)
                    cancelled = ctl.cancelled
                if cancelled:
                    task.cancel()
                try:
                    inner.run_until_complete(task)
                except asyncio.CancelledError:
                    pass
            finally:
                with ctl.lock:
                    ctl.plain = None
                try:
                    inner.run_until_complete(inner.shutdown_asyncgens())
                    inner.run_until_complete(inner.shutdown_default_executor())
                finally:
                    inner.close()

        await asyncio.to_thread(worker)

    async def _plain(self, emit, ctl: Control, question, plan, unit, model, net, extras):
        lane = "plain"
        kind = plan["kind"]
        batches = self.uploads[kind]
        edge = net.edge
        sent = 0
        loop = asyncio.get_running_loop()
        # Batches are packed from the data files; the next one is packed while this one is sent.
        pending = loop.run_in_executor(None, batches.load, 0) if len(batches) else None
        async with self.cluster.cloud_session() as cloud:
            emit({"lane": lane, "type": "stage", "stage": "upload", "state": "start", "batches": len(batches)})
            t0 = now()
            async with cloud.post(
                self.cluster.cloud_url + "/session", json={"kind": kind}, server_hostname="hpic.internal"
            ) as r:
                ctl.plain_sid = (await r.json())["sid"]
            sid = ctl.plain_sid
            await pace(t0, edge, 64, legs=2)
            for i in range(len(batches)):
                if ctl.cancelled:
                    raise asyncio.CancelledError()
                blob = await pending
                pending = loop.run_in_executor(None, batches.load, i + 1) if i + 1 < len(batches) else None
                t0 = now()
                async with cloud.post(
                    f"{self.cluster.cloud_url}/upload/{sid}", data=blob, server_hostname="hpic.internal"
                ) as r:
                    r.raise_for_status()
                    await r.read()
                await pace(t0, edge, len(blob), legs=2)
                sent += len(blob)
                emit({"lane": lane, "type": "upload", "batch": i + 1, "batches": len(batches), "edge_up": sent})
            emit({"lane": lane, "type": "stage", "stage": "upload", "state": "end", "edge_up": sent})
            body = {
                "question": question,
                "plan": plan,
                "model": model,
                "evidence_k": config.evidence_k(kind),
                "vision": extras["vision"],
                "note": extras["note"],
            }
            if os.environ.get("HPIS_DEMO_CLOUD_EMBED") == "stored":
                body["qvec"] = unit.tobytes().hex()  # offline tests only
            payload = json.dumps(body).encode()
            await asyncio.sleep(edge.one_way_seconds(len(payload)))
            sent += len(payload)
            emit({"lane": lane, "type": "stage", "stage": "retrieve", "state": "start"})
            await self._relay(
                emit,
                ctl.run,
                lane,
                cloud.post(f"{self.cluster.cloud_url}/ask/{sid}", data=payload, server_hostname="hpic.internal"),
                edge,
                sent,
            )

    async def lane_hpis(self, ctl: Control, lane: str, question, plan, unit, model, net, extras):
        run = ctl.run
        kind = plan["kind"]
        if lane == "v2":
            device = self.tenant("tee", kind)
            op = device.vector_op(unit, config.RETRIEVE_K, plan)
            ask = {"lane": "v2", "question": question, "plan": plan, "model": model}
        else:
            device = self.devices["tee-v1"]
            op = device.scan_op()
            ask = {"lane": "v1", "question": question, "plan": plan, "model": model, "qvec": unit.tobytes()}
        ask["cloud"] = {"bps": net.cloud.bps, "rtt_ms": net.cloud.rtt_ms}
        ask["evidence_k"] = config.evidence_k(kind)
        ask["vision"] = extras["vision"]
        ask["note"] = extras["note"]
        envelope = device.ask_envelope(op, ask)
        body = pack(envelope)
        if lane == "v2":
            sealed = unpack(envelope[1][64:])[2]
            run.put(
                {
                    "lane": lane,
                    "type": "sealed",
                    "bytes": len(body),
                    "sealed_bytes": len(sealed),
                    "preview": sealed[:40].hex(),
                    "tokens": [g[0].hex()[:24] for g in op["groups"]],
                    "aid": device.aid.hex()[:8],
                }
            )
        run.put({"lane": lane, "type": "stage", "stage": "request", "state": "start", "edge_up": len(body)})
        await asyncio.sleep(net.edge.one_way_seconds(len(body)))
        await self._relay(
            run.put,
            run,
            lane,
            device.session.post(
                device.url + "/ask",
                data=body,
                server_hostname="hpic.internal",
                headers={"Content-Type": "application/msgpack"},
            ),
            net.edge,
            len(body),
        )

    async def _relay(self, emit, run: Run, lane: str, request_cm, edge, edge_up: int):
        """Read the NDJSON stream from HPIC / the cloud and forward normalized events."""
        received = 0
        first_token = None
        timed_out = stopped = False
        async with request_cm as response:
            response.raise_for_status()
            delayed = False
            async for size, ev in _iter_ndjson(response):
                if not delayed:
                    await asyncio.sleep(edge.rtt_ms / 2000)  # downlink latency
                    delayed = True
                received += size
                kind = ev.pop("ev")
                if kind == "token":
                    if first_token is None:
                        first_token = run.t()
                        emit({"lane": lane, "type": "stage", "stage": "generate", "state": "first", "ttft_ms": first_token})
                    emit({"lane": lane, "type": "token", "text": ev["text"]})
                    continue
                if kind == "done":
                    timed_out = bool(ev.get("timed_out"))
                    stopped = bool(ev.get("stopped"))
                ev.update({"lane": lane, "type": kind, "edge_up": edge_up, "edge_down": received})
                emit(ev)
        end = run.t()  # the lane's total is cut on the run's one clock
        emit(
            {
                "lane": lane,
                "type": "finish",
                "ttft_ms": first_token,
                "total_ms": end,
                "edge_up": edge_up,
                "edge_down": received,
                "stopped": stopped,
                "timed_out": timed_out,
                "t": end,
            }
        )

    # ------------------------------------------------------------------ verify
    async def verify(self, plan, unit, pairs: int = 60, *, limit_s: float | None = None) -> dict:
        """Paired plaintext vs encrypted retrieval on the identical service stack.

        Same question, same filters, same K; AB/BA alternated; no emulated wire.
        A laptop running a browser adds millisecond-scale jitter to a ~4 ms request,
        so the comparison uses the lower quartile of each arm (the noise-free
        envelope of the cost) besides the medians that are displayed.  The pairs
        stop early after ``limit_s`` (photo bodies are large).
        """
        kind = plan.get("kind") or "mail"
        try:
            plain, tee = self.tenant("plain", kind), self.tenant("tee", kind)
        except KeyError:
            return {}
        times = {"plain": [], "tee": []}
        ids = {}
        devices = {"plain": plain, "tee": tee}
        deadline = time.perf_counter() + (limit_s if limit_s is not None else config.VERIFY_LIMIT_S)

        async def once(name):
            device = devices[name]
            op = device.vector_op(unit, config.RETRIEVE_K, plan)
            t0 = time.perf_counter()
            result = await device.request(op, {"dek": device.dek})
            ids[name] = [rid for rid, _ in result["records"]]
            return (time.perf_counter() - t0) * 1000

        for _ in range(4):  # warm both paths
            await once("plain")
            await once("tee")
        done = 0
        for i in range(pairs):
            for name in (("plain", "tee") if i % 2 == 0 else ("tee", "plain")):
                times[name].append(await once(name))
            done += 1
            if time.perf_counter() > deadline and done >= 10:
                break

        def q(values, p):
            return float(np.percentile(np.asarray(values), p))

        overhead = (q(times["tee"], 25) / q(times["plain"], 25) - 1) * 100
        return {
            "kind": kind,
            "plain_ms": round(statistics.median(times["plain"]), 3),
            "cipher_ms": round(statistics.median(times["tee"]), 3),
            "plain_p25_ms": round(q(times["plain"], 25), 3),
            "cipher_p25_ms": round(q(times["tee"], 25), 3),
            "overhead_pct": round(overhead, 2),
            "identical": ids["plain"] == ids["tee"],
            "k": config.RETRIEVE_K,
            "pairs": done,
            "ids": ids["tee"],
        }

    # ------------------------------------------------------------------ run
    async def run(self, question: str, model: str, lanes=("plain", "v2"), net: config.Network | None = None):
        """NDJSON events of one run; closing this generator early stops the run."""
        ctl = await self.start(question, model, lanes, net)
        try:
            while True:
                event = await ctl.out.get()
                yield event
                if event.get("type") == "end":
                    break
        finally:
            if not ctl.done.is_set():
                ctl.stop("closed")  # the page went away: stop the work it started

    async def start(self, question: str, model: str, lanes=("plain", "v2"), net=None) -> Control:
        """Begin a run (after stopping one still in progress) and return its handle."""
        old = self.active
        if old is not None and not old.done.is_set():
            old.stop("preempted")
            try:
                await asyncio.wait_for(old.done.wait(), 10)
            except asyncio.TimeoutError:
                print("previous run did not end within 10 s; starting the next one anyway", flush=True)
        ctl = Control(Run())
        self.active = ctl
        ctl.task = asyncio.create_task(self._produce(ctl, question, model, tuple(lanes), net or config.Network()))
        return ctl

    async def stop(self, wait: float = 5.0) -> bool:
        """Stop the run in progress (POST /api/stop); True if there was one."""
        ctl = self.active
        if ctl is None or ctl.done.is_set():
            return False
        ctl.stop("stopped")
        try:
            await asyncio.wait_for(ctl.done.wait(), wait)
        except asyncio.TimeoutError:
            pass
        return True

    async def _produce(self, ctl: Control, question, model, lanes, net):
        run = ctl.run

        def out(event):
            event.setdefault("t", run.t())
            ctl.out.put_nowait(event)

        ended = False
        try:
            ended = await self._produce_events(ctl, out, question, model, lanes, net)
        except Exception as exc:  # never leave the page without an end event
            traceback.print_exc()
            out({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            if not ended:
                out({"type": "end", "stopped": ctl.reason is not None, "timed_out": ctl.reason == "timeout",
                     "reason": ctl.reason})
            ctl.done.set()
            if self.active is ctl:
                self.active = None

    async def _produce_events(self, ctl: Control, out, question, model, lanes, net) -> bool:
        run = ctl.run
        deadline = run.t0 + config.RUN_LIMIT_S
        loop = asyncio.get_running_loop()
        plan = await loop.run_in_executor(None, self.plan, question)
        t0 = now()
        unit = await loop.run_in_executor(None, self.embed, plan)
        embed_ms = (now() - t0) * 1000
        kind = plan["kind"]
        info = BY_KIND[kind]
        vision = await self.vision(model) if kind == "photo" else False
        out(
            {
                "type": "plan",
                "question": question,
                "plan": plan,
                "chips": planner.describe(plan),
                "embed_ms": round(embed_ms, 2),
                "net": net.describe(),
                "model": model,
                "preset": plan.get("preset"),
                "kind": kind,
                "kind_label": info.kind_label,
                "device": info.id,
                "device_label": info.label,
                "total": plan["total"],
                "matched": plan.get("matched"),
                "relaxed": plan.get("relaxed") or [],
                "vision": vision,
            }
        )
        notice = planner.relax_notice(plan)
        if notice:
            out({"type": "notice", "lane": None, "text": notice, "relaxed": plan.get("relaxed") or []})
        extras = {"vision": vision, "note": planner.model_note(plan)}

        if kind != "mail":
            lanes = tuple(lane for lane in lanes if lane != "v1")  # HPIS 1.0 covers the mailbox only
        state = {lane: {"edge_up": 0, "edge_down": 0, "ttft_ms": None, "over": False} for lane in lanes}
        if ctl.stop_event.is_set():  # stopped while planning: no lane has started
            for lane in lanes:
                out({"lane": lane, "type": "finish", "ttft_ms": None, "total_ms": run.t(), "edge_up": 0,
                     "edge_down": 0, "stopped": True, "timed_out": False})
            out({"type": "end", "stopped": True, "timed_out": False, "reason": ctl.reason})
            return True

        async def guarded(coro, lane):
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # surface failures in the UI, keep other lanes alive
                run.put({"lane": lane, "type": "error", "message": f"{type(exc).__name__}: {exc}"})

        tasks = []
        if "plain" in lanes:
            coro = self.lane_plain(ctl, question, plan, unit, model, net, extras)
            tasks.append(asyncio.create_task(guarded(coro, "plain")))
        for lane in ("v1", "v2"):
            if lane in lanes:
                coro = self.lane_hpis(ctl, lane, question, plan, unit, model, net, extras)
                tasks.append(asyncio.create_task(guarded(coro, lane)))

        def forward(event):
            lane = event.get("lane")
            st = state.get(lane)
            if st is not None:
                if st["over"]:
                    return  # the lane was already closed (stopped); drop late events
                for key in ("edge_up", "edge_down"):
                    if event.get(key) is not None:
                        st[key] = max(st[key], event[key])
                if event.get("type") == "stage" and event.get("state") == "first":
                    st["ttft_ms"] = event.get("ttft_ms")
                if event.get("type") in ("finish", "error"):
                    st["over"] = True
            out(event)

        while True:
            if all(t.done() for t in tasks) and run.queue.empty():
                break
            if ctl.stop_event.is_set():
                break
            remaining = deadline - now()
            if remaining <= 0:
                ctl.stop("timeout")
                break
            try:
                event = await asyncio.wait_for(run.queue.get(), min(0.1, remaining))
            except asyncio.TimeoutError:
                continue
            forward(event)

        if ctl.reason is not None:
            t_stop = run.t()  # the lanes end when the stop arrived, not when the teardown is done
            await self._abort(ctl, tasks)
            timeout = ctl.reason == "timeout"
            for lane, st in state.items():
                if st["over"]:
                    continue
                st["over"] = True
                out(
                    {
                        "lane": lane,
                        "type": "finish",
                        "ttft_ms": st["ttft_ms"],
                        "total_ms": t_stop,
                        "edge_up": st["edge_up"],
                        "edge_down": st["edge_down"],
                        "stopped": not timeout,
                        "timed_out": timeout,
                        "t": t_stop,
                    }
                )
            if timeout:
                limit = f"{config.RUN_LIMIT_S:g}"
                out({"type": "notice", "lane": None, "text": f"本次运行超过 {limit} 秒，已结束未完成的通道"})
            out({"type": "end", "stopped": not timeout, "timed_out": timeout, "reason": ctl.reason, "t": t_stop})
            return True

        if "v2" in lanes:
            remaining = deadline - now()
            if remaining > 1:
                check = asyncio.create_task(self.verify(plan, unit, limit_s=min(config.VERIFY_LIMIT_S, remaining - 1)))
                waiter = asyncio.create_task(ctl.stop_event.wait())
                finished, _ = await asyncio.wait({check, waiter}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                if check in finished:
                    try:
                        result = check.result()
                        if result:
                            out({"type": "verify", **result, "t": run.t()})
                    except Exception as exc:
                        out({"type": "verify", "error": str(exc), "t": run.t()})
                else:
                    check.cancel()
        stopped = ctl.reason is not None
        out({"type": "end", "stopped": stopped, "timed_out": ctl.reason == "timeout", "reason": ctl.reason})
        return True

    async def _abort(self, ctl: Control, tasks):
        """Cancel every lane of a stopped run: HPIC request, plain thread, cloud session."""
        ctl.stop(ctl.reason or "stopped")
        for task in tasks:
            task.cancel()
        if ctl.plain_sid:
            asyncio.create_task(self._cancel_cloud(ctl.plain_sid))
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)

    async def _cancel_cloud(self, sid: str):
        """Tell the ordinary cloud to stop that session's vector loop and answer."""
        try:
            async with self.cluster.cloud_session() as cloud:
                async with cloud.post(
                    f"{self.cluster.cloud_url}/cancel/{sid}",
                    server_hostname="hpic.internal",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as r:
                    await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
