"""Runs one question through the three lanes concurrently and yields UI events.

Lanes
  plain  端侧直传普通云: upload the mailbox in 16 MB batches, the cloud searches plaintext.
  v1     HPIS 1.0: anonymous encrypted storage, but HPIC fetches and decrypts every record.
  v2     HPIS 2.0: SSE + encrypted vector search inside HPIS, HPIC opens only K records.

Event times (`t`, ms) are measured from the click.  Compute is real; device-cloud
and cloud-cloud wires are emulated by bandwidth + RTT (config.Network).
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import aiohttp
import numpy as np

from hpis.codec import pack, unit_vectors, unpack

from . import config, planner
from .build import UPLOAD_DIR
from .cluster import Cluster
from .mailbox import Mailbox, card
from .netem import now, pace


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
    def __init__(self, cluster: Cluster, mailbox: Mailbox, build: dict):
        self.cluster = cluster
        self.mailbox = mailbox
        self.build = build
        self.devices = {}
        self.lock = asyncio.Lock()
        self.batches = [UPLOAD_DIR / name for name in build["upload"]["batches"]]

    async def open(self):
        for name in self.cluster.hpic_url:
            device = self.cluster.device(name)
            await device.__aenter__()
            self.devices[name] = device
        Embedder.get()  # load the on-device embedding model once
        return self

    async def close(self):
        for device in self.devices.values():
            await device.__aexit__(None, None, None)

    # ------------------------------------------------------------------ shared
    def plan(self, question: str) -> dict:
        return planner.plan_question(question, self.mailbox.presets, self.mailbox.bible)

    def embed(self, plan: dict) -> np.ndarray:
        vec = Embedder.get().encode_query(plan.get("semantic") or "")
        return unit_vectors(np.asarray(vec, dtype=np.float64), len(vec))

    async def warmup(self):
        """Open the encrypted vector index inside HPIS once (first-query cost)."""
        # A filtered query also loads HPIS' in-memory posting lists.
        plan = planner.finalize({"semantic": "项目会议纪要", "date_from": "2026-11-01", "date_to": "2026-11-20"})
        unit = self.embed(plan)
        for name, device in self.devices.items():
            if name == "tee-v1":
                continue  # the 1.0 lane never uses the vector index
            op = device.vector_op(unit, config.RETRIEVE_K, plan)
            await device.request(op, {"dek": device.dek})

    # ------------------------------------------------------------------ lanes
    async def lane_plain(self, run: Run, question, plan, unit, model, net):
        """Runs on its own thread + event loop: TLS-encrypting hundreds of MB of
        uploads must not stall the event loop that relays the other lanes."""
        loop = asyncio.get_running_loop()

        def emit(event):
            event.setdefault("t", run.t())
            loop.call_soon_threadsafe(run.queue.put_nowait, event)

        def worker():
            asyncio.run(self._plain(emit, run, question, plan, unit, model, net))

        await asyncio.to_thread(worker)

    async def _plain(self, emit, run, question, plan, unit, model, net):
        lane = "plain"
        edge = net.edge
        sent = 0
        async with self.cluster.cloud_session() as cloud:
            emit({"lane": lane, "type": "stage", "stage": "upload", "state": "start", "batches": len(self.batches)})
            t0 = now()
            async with cloud.post(self.cluster.cloud_url + "/session", server_hostname="hpic.internal") as r:
                sid = (await r.json())["sid"]
            await pace(t0, edge, 64, legs=2)
            for i, path in enumerate(self.batches):
                blob = await asyncio.to_thread(path.read_bytes)
                t0 = now()
                async with cloud.post(
                    f"{self.cluster.cloud_url}/upload/{sid}", data=blob, server_hostname="hpic.internal"
                ) as r:
                    r.raise_for_status()
                    await r.read()
                await pace(t0, edge, len(blob), legs=2)
                sent += len(blob)
                emit({"lane": lane, "type": "upload", "batch": i + 1, "batches": len(self.batches), "edge_up": sent})
            emit({"lane": lane, "type": "stage", "stage": "upload", "state": "end", "edge_up": sent})
            body = {"question": question, "plan": plan, "model": model}
            if os.environ.get("HPIS_DEMO_CLOUD_EMBED") == "stored":
                body["qvec"] = unit.tobytes().hex()  # offline tests only
            payload = json.dumps(body).encode()
            await asyncio.sleep(edge.one_way_seconds(len(payload)))
            sent += len(payload)
            emit({"lane": lane, "type": "stage", "stage": "retrieve", "state": "start"})
            await self._relay(
                emit,
                run,
                lane,
                cloud.post(f"{self.cluster.cloud_url}/ask/{sid}", data=payload, server_hostname="hpic.internal"),
                edge,
                sent,
            )

    async def lane_hpis(self, run: Run, lane: str, question, plan, unit, model, net):
        device = self.devices["tee" if lane == "v2" else "tee-v1"]
        if lane == "v2":
            op = device.vector_op(unit, config.RETRIEVE_K, plan)
            ask = {"lane": "v2", "question": question, "plan": plan, "model": model}
        else:
            op = device.scan_op()
            ask = {"lane": "v1", "question": question, "plan": plan, "model": model, "qvec": unit.tobytes()}
        ask["cloud"] = {"bps": net.cloud.bps, "rtt_ms": net.cloud.rtt_ms}
        ask["evidence_k"] = config.EVIDENCE_K
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
                ev.update({"lane": lane, "type": kind, "edge_up": edge_up, "edge_down": received})
                emit(ev)
        emit(
            {
                "lane": lane,
                "type": "finish",
                "ttft_ms": first_token,
                "total_ms": run.t(),
                "edge_up": edge_up,
                "edge_down": received,
            }
        )

    # ------------------------------------------------------------------ verify
    async def verify(self, plan, unit, pairs: int = 60) -> dict:
        """Paired plaintext vs encrypted retrieval on the identical service stack.

        Same question, same filters, same K; AB/BA alternated; no emulated wire.
        A laptop running a browser adds millisecond-scale jitter to a ~4 ms request,
        so the comparison uses the lower quartile of each arm (the noise-free
        envelope of the cost) besides the medians that are displayed.
        """
        plain, tee = self.devices.get("plain"), self.devices.get("tee")
        if not plain or not tee:
            return {}
        times = {"plain": [], "tee": []}
        ids = {}

        async def once(name):
            device = self.devices[name]
            op = device.vector_op(unit, config.RETRIEVE_K, plan)
            t0 = time.perf_counter()
            result = await device.request(op, {"dek": device.dek})
            ids[name] = [rid for rid, _ in result["records"]]
            return (time.perf_counter() - t0) * 1000

        for _ in range(4):  # warm both paths
            await once("plain")
            await once("tee")
        for i in range(pairs):
            for name in (("plain", "tee") if i % 2 == 0 else ("tee", "plain")):
                times[name].append(await once(name))

        def q(values, p):
            return float(np.percentile(np.asarray(values), p))

        overhead = (q(times["tee"], 25) / q(times["plain"], 25) - 1) * 100
        return {
            "plain_ms": round(statistics.median(times["plain"]), 3),
            "cipher_ms": round(statistics.median(times["tee"]), 3),
            "plain_p25_ms": round(q(times["plain"], 25), 3),
            "cipher_p25_ms": round(q(times["tee"], 25), 3),
            "overhead_pct": round(overhead, 2),
            "identical": ids["plain"] == ids["tee"],
            "k": config.RETRIEVE_K,
            "pairs": pairs,
            "ids": ids["tee"],
        }

    # ------------------------------------------------------------------ run
    async def run(self, question: str, model: str, lanes=("plain", "v2"), net: config.Network | None = None):
        net = net or config.Network()
        run = Run()
        plan = self.plan(question)
        t0 = now()
        unit = self.embed(plan)
        embed_ms = (now() - t0) * 1000
        run.put(
            {
                "type": "plan",
                "question": question,
                "plan": plan,
                "chips": planner.describe(plan),
                "embed_ms": round(embed_ms, 2),
                "net": net.describe(),
                "model": model,
                "preset": plan.get("preset"),
            }
        )

        async def guarded(coro, lane):
            try:
                await coro
            except Exception as exc:  # surface failures in the UI, keep other lanes alive
                run.put({"lane": lane, "type": "error", "message": f"{type(exc).__name__}: {exc}"})

        tasks = []
        async with self.lock:
            if "plain" in lanes:
                tasks.append(asyncio.create_task(guarded(self.lane_plain(run, question, plan, unit, model, net), "plain")))
            for lane in ("v1", "v2"):
                if lane in lanes:
                    tasks.append(
                        asyncio.create_task(guarded(self.lane_hpis(run, lane, question, plan, unit, model, net), lane))
                    )
            done = asyncio.gather(*tasks)
            while True:
                if done.done() and run.queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(run.queue.get(), 0.1)
                except asyncio.TimeoutError:
                    continue
                yield event
            if "v2" in lanes:
                try:
                    check = await self.verify(plan, unit)
                    if check:
                        yield {"type": "verify", **check, "t": run.t()}
                except Exception as exc:
                    yield {"type": "verify", "error": str(exc), "t": run.t()}
        yield {"type": "end", "t": run.t()}
