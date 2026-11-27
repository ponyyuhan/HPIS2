"""HPIC 1.0 (端侧直传机密计算平台) for the "plain" lane.

HPIC 1.0 keeps no data, so the device uploads the whole collection of the asked
kind (emails with their attachments, the photo album with its pictures, the
watch or car log) in size-limited batches for every question.  The service
parses each batch as it arrives, then filters on the record fields, narrows the
candidates to a lexical shortlist (at most ``config.CLOUD_SHORTLIST``), computes
vectors for the shortlist with the same open embedding model within
``config.CLOUD_EMBED_BUDGET_S`` (stopping early and ranking what it has), ranks
with the same exact kernel as HPIS and asks the same model with the same
evidence budget as HPIC 2.0.

多端协同: only the phone talks to HPIC 1.0, so a multi-device question carries
the data of the devices synced to the phone (``/session`` with ``kinds``, one
``/upload?kind=`` stream per kind).  ``/ask_multi`` runs the per-kind search
above on each uploaded kind and makes one model call with the merged evidence.

Never hang: the vectors are computed in a worker thread, one small batch at a
time, so the event loop stays free; ``POST /cancel/{sid}`` (or the device
closing the connection) stops the loop between batches.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import os
import time
from time import perf_counter

import msgpack
import numpy as np
from aiohttp import web

from . import config, records, retrieval
from .answer import generate

EMBED_BATCH = 16
SESSION_TTL_S = 300.0


def line(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


class Session:
    """Everything HPIC 1.0 received for one question (one kind)."""

    def __init__(self, kind: str = "mail"):
        self.kind = kind if kind in records.KINDS else "mail"
        self.fields, self.dates, self.raw, self.lex, self.images = [], [], [], [], []
        self.bytes = 0
        self.parse_ms = 0.0
        self.cancelled = False
        self.created = time.monotonic()

    def add(self, blob: bytes):
        start = perf_counter()
        for item in msgpack.unpackb(blob, raw=False):
            image = None
            if isinstance(item, (list, tuple)):  # [record JSON, picture bytes]
                item, image = item[0], (item[1] if len(item) > 1 else None)
            rec = json.loads(item)
            self.fields.append(records.index_fields(self.kind, rec))
            self.dates.append(rec.get("date", ""))
            self.lex.append(records.lex_text(self.kind, rec))
            self.raw.append(item)
            self.images.append(image)
        self.bytes += len(blob)
        self.parse_ms += (perf_counter() - start) * 1000

    def record(self, i: int, *, image: bool = False) -> dict:
        rec = json.loads(self.raw[i])
        if image and self.images[i]:
            rec["image"] = self.images[i]
        return rec

    def release(self):
        """Drop the uploaded data once the evidence is chosen (the session stays cancellable)."""
        self.fields, self.dates, self.raw, self.lex, self.images = [], [], [], [], []


class MultiSession:
    """What HPIC 1.0 received for one multi-device question: one Session per uploaded kind."""

    def __init__(self, kinds):
        self.parts = {k: Session(k) for k in kinds if k in records.KINDS}
        self.cancelled = False
        self.created = time.monotonic()

    def release(self):
        for part in self.parts.values():
            part.release()


def budget_notice(budget: float, embedded: list, short: list) -> str:
    return f"HPIC 1.0 {budget:g} 秒内只现算了 {len(embedded)} 条候选的向量（共 {len(short)} 条），按已算出的部分排序"


_EMBEDDER = None
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="cloud-embed")


def embedder():
    """Same open model the device uses; the ordinary cloud runs it on what it received."""
    global _EMBEDDER
    if _EMBEDDER is None:
        from .embed import Embedder

        _EMBEDDER = Embedder()
    return _EMBEDDER


_FIELDS: dict[str, list[str]] = {}


def _text_fields(kind: str) -> list[str] | None:
    """The document recipe of ``embeddings_{kind}.json`` (the same text the device embedded)."""
    if kind == "mail":
        return None
    if kind not in _FIELDS:
        _FIELDS[kind] = _read_text_fields(kind)
    return _FIELDS[kind]


def _read_text_fields(kind: str) -> list[str]:
    path = config.DEVICES / f"embeddings_{kind}.json"
    try:
        meta = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        meta = {}
    sample = None
    try:
        with open(config.DEVICES / f"{kind}.jsonl", encoding="utf-8") as fh:
            sample = json.loads(fh.readline())
    except (OSError, ValueError):
        pass
    return records.recipe_fields(meta.get("text"), sample)


def _stored_vectors(kind: str, rows: list[int]) -> np.ndarray:
    """Offline tests only: the device's stored vectors (row i = i-th uploaded record)."""
    if kind == "mail":
        path, meta = config.DATA / "embeddings.f32", config.DATA / "embeddings.json"
    else:
        path, meta = config.DEVICES / f"embeddings_{kind}.f32", config.DEVICES / f"embeddings_{kind}.json"
    dim = int(json.loads(meta.read_text())["dim"])
    stored = np.memmap(path, dtype=np.float32, mode="r").reshape(-1, dim)
    return np.asarray(stored[rows], dtype=np.float32) if rows else np.zeros((0, dim), np.float32)


def make_cloud_app():
    sessions: dict[str, Session | MultiSession] = {}
    busy = {"asking": 0, "embedding": 0}
    app = web.Application(client_max_size=64 << 20)

    async def warm(_app):
        if os.environ.get("HPIS_DEMO_CLOUD_EMBED") != "stored":
            await asyncio.get_running_loop().run_in_executor(_POOL, embedder().encode_query, "预热")

    app.on_startup.append(warm)

    def sweep():
        now = time.monotonic()
        for sid in [s for s, v in sessions.items() if now - v.created > SESSION_TTL_S]:
            sessions.pop(sid, None)

    async def new_session(request):
        sweep()
        body = {}
        if request.can_read_body:
            with contextlib.suppress(ValueError):
                body = json.loads(await request.read() or b"{}") or {}
        sid = os.urandom(8).hex()
        if "kinds" in body:  # 多端协同: the kinds the phone uploads with this question
            sessions[sid] = MultiSession(body.get("kinds") or [])
        else:
            sessions[sid] = Session(body.get("kind") or "mail")
        return web.json_response({"sid": sid})

    async def upload(request):
        session = sessions.get(request.match_info["sid"])
        if session is None or session.cancelled:
            raise web.HTTPGone(text="session closed")
        blob = await request.read()
        if isinstance(session, MultiSession):
            part = session.parts.get(request.query.get("kind", ""))
            if part is None:
                raise web.HTTPBadRequest(text="kind not in this session")
            part.add(blob)
            return web.json_response({"rows": len(part.raw), "bytes": part.bytes})
        session.add(blob)
        return web.json_response({"rows": len(session.raw), "bytes": session.bytes})

    async def cancel(request):
        session = sessions.pop(request.match_info["sid"], None)
        if session is not None:
            session.cancelled = True
        return web.json_response({"ok": True, "cancelled": session is not None})

    async def stats(request):
        return web.json_response({"sessions": len(sessions), **busy})


    async def retrieve(session: Session, flag, plan: dict, payload: dict, qvec_hex, vision: bool, kind: str):
        """The per-kind search on what was uploaded (single and multi-device questions alike).

        Filter on the record fields, lexical shortlist, vectors for the shortlist within the budget,
        exact Top-K.  Returns None when ``flag`` (the session the device may cancel) was cancelled."""
        t0 = perf_counter()
        # The records arrived in the clear inside the service, so it filters on exact fields and dates.
        keep = [
            i
            for i, f in enumerate(session.fields)
            if retrieval.matches(f, plan) and retrieval.in_window(session.dates[i], plan)
        ]
        filter_ms = (perf_counter() - t0) * 1000
        # No index survives between questions: a lexical shortlist, then vectors for it only.
        t1 = perf_counter()
        query = " ".join(x for x in (plan.get("semantic"), payload.get("question")) if x)
        order = retrieval.lexical_order([session.lex[i] for i in keep], query, config.cloud_shortlist(kind))
        short = [keep[j] for j in order]
        lex_ms = (perf_counter() - t1) * 1000
        t2 = perf_counter()
        budget = float(payload.get("embed_budget_s") or config.CLOUD_EMBED_BUDGET_S)
        embedded, vecs, budget_hit = [], [], False
        stored = os.environ.get("HPIS_DEMO_CLOUD_EMBED") == "stored"  # offline tests only
        loop = asyncio.get_running_loop()
        if stored:
            encode = None
            q = np.frombuffer(bytes.fromhex(qvec_hex), dtype=np.float32)
        else:
            model = embedder()
            fields = _text_fields(kind)
            encode = model.encode_docs
            q = np.asarray(
                await loop.run_in_executor(_POOL, model.encode_query, plan.get("semantic") or payload["question"]),
                dtype=np.float32,
            )
        slow = float(os.environ.get("HPIS_DEMO_CLOUD_SLOW_S") or 0)  # tests: a slower CPU per batch
        deadline = perf_counter() + budget
        busy["embedding"] += 1
        try:
            for start in range(0, len(short), EMBED_BATCH):
                if flag.cancelled:
                    break
                if embedded and perf_counter() >= deadline:
                    budget_hit = True  # stop early and rank what is embedded
                    break
                chunk = short[start : start + EMBED_BATCH]
                if stored:
                    vecs.append(_stored_vectors(kind, chunk))
                else:
                    texts = [records.doc_text(kind, session.record(i), fields) for i in chunk]
                    vecs.append(await loop.run_in_executor(_POOL, encode, texts))
                if slow:
                    await asyncio.sleep(slow)
                embedded.extend(chunk)
        finally:
            busy["embedding"] -= 1
        embed_ms = (perf_counter() - t2) * 1000
        if flag.cancelled:
            return None
        matrix = np.concatenate(vecs) if vecs else np.zeros((0, q.size), np.float32)
        ids = np.asarray([int(json.loads(session.raw[i])["id"]) for i in embedded], dtype=np.int64)
        top = retrieval.top_k(ids, np.asarray(matrix, dtype=np.float32), q, config.RETRIEVE_K)
        evidence_k = int(payload.get("evidence_k") or config.evidence_k(kind))
        pairs = [(int(ids[i]), session.record(embedded[i], image=vision)) for i in top][:evidence_k]
        stats = {
            "scanned": len(session.fields),
            "candidates": len(keep),
            "shortlist": len(short),
            "embedded": len(embedded),
            "parse_ms": round(session.parse_ms, 1),
            "filter_ms": round(filter_ms, 2),
            "lex_ms": round(lex_ms, 2),
            "embed_ms": round(embed_ms, 1),
            "search_ms": round((perf_counter() - t0) * 1000, 3),
            "budget_ms": round(budget * 1000),
            "budget_hit": budget_hit,
            "bytes": session.bytes,
        }
        return {"pairs": pairs, "stats": stats, "budget": budget, "embedded": embedded, "short": short}

    async def ask(request):
        busy["asking"] += 1
        try:
            return await _ask(request)
        finally:
            busy["asking"] -= 1

    async def _ask(request):
        t_in = perf_counter()
        sid = request.match_info["sid"]
        session = sessions.get(sid)
        if session is None or session.cancelled:
            raise web.HTTPGone(text="session closed")
        payload = json.loads(await request.read())
        plan = payload.get("plan") or {}
        kind = plan.get("kind") or session.kind
        vision = bool(payload.get("vision")) and kind == "photo"
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
        await response.prepare(request)

        async def emit(obj):
            obj["cloud_ms"] = round((perf_counter() - t_in) * 1000, 3)
            await response.write(line(obj))

        try:
            found = await retrieve(session, session, plan, payload, payload.get("qvec"), vision, kind)
            if found is None:
                await emit({"ev": "done", "stopped": True})
                await response.write_eof()
                return response
            pairs, stats = found["pairs"], found["stats"]
            budget, budget_hit = found["budget"], stats["budget_hit"]
            embedded, short = found["embedded"], found["short"]
            await emit({"ev": "search", "kind": kind, **stats})
            if budget_hit:
                await emit({"ev": "notice", "text": budget_notice(budget, embedded, short)})
            cards, evidence = records.evidence(kind, pairs, vision=vision)
            await emit({"ev": "evidence", "kind": kind, "items": cards})
            session.release()  # the uploaded data is no longer needed
            if payload.get("generate", True):
                stream = generate(
                    payload["question"],
                    evidence,
                    payload.get("model", config.DEFAULT_MODEL),
                    kind=kind,
                    note=payload.get("note"),
                )
                async with contextlib.aclosing(stream):
                    async for event in stream:
                        if session.cancelled:
                            await emit({"ev": "done", "stopped": True})
                            break
                        if event["type"] == "token":
                            await emit({"ev": "token", "text": event["text"]})
                        elif event["type"] == "done":
                            if event.get("timed_out"):
                                await emit({"ev": "notice", "text": event.get("error") or "模型超时"})
                            await emit({"ev": "done", **{k: v for k, v in event.items() if k != "type"}})
            else:
                await emit({"ev": "done", "skipped": True})
            await response.write_eof()
        finally:
            sessions.pop(sid, None)
            session.release()
        return response

    async def ask_multi(request):
        busy["asking"] += 1
        try:
            return await _ask_multi(request)
        finally:
            busy["asking"] -= 1

    async def _ask_multi(request):
        """多端协同: today's per-kind search on each uploaded kind, then one answer from the merged evidence.

        Devices whose data the phone did not upload get no evidence; the prompt says so."""
        t_in = perf_counter()
        sid = request.match_info["sid"]
        session = sessions.get(sid)
        if not isinstance(session, MultiSession) or session.cancelled:
            raise web.HTTPGone(text="session closed")
        payload = json.loads(await request.read())
        plans = payload.get("plans") or {}  # device -> plan, for the devices whose data was uploaded
        devices = payload.get("devices") or list(plans)
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
        await response.prepare(request)

        async def emit(obj):
            obj["cloud_ms"] = round((perf_counter() - t_in) * 1000, 3)
            await response.write(line(obj))

        try:
            groups, scope = [], []
            for device in devices:
                plan = plans.get(device)
                kind = (plan or {}).get("kind")
                part = session.parts.get(kind) if plan else None
                if part is None:
                    scope.append({"id": device, "kind": kind, "available": False})
                    continue
                per_kind = {**payload, "evidence_k": (payload.get("evidence_k") or {}).get(device)}
                qvec = (payload.get("qvecs") or {}).get(device)
                found = await retrieve(part, session, plan, per_kind, qvec, False, kind)
                if found is None:
                    await emit({"ev": "done", "stopped": True})
                    await response.write_eof()
                    return response
                stats = found["stats"]
                await emit({"ev": "device_search", "device": device, "kind": kind, "records": stats["scanned"],
                            "rows": len(found["pairs"]), **stats})
                if stats["budget_hit"]:
                    await emit({"ev": "notice", "device": device,
                                "text": budget_notice(found["budget"], found["embedded"], found["short"])})
                groups.append((device, kind, found["pairs"]))
                scope.append({"id": device, "kind": kind, "available": True, "note": (payload.get("notes") or {}).get(device)})
            cards, evidence = records.multi_evidence(groups)
            await emit({"ev": "evidence", "mode": "multi", "items": cards,
                        "devices": {device: len(pairs) for device, _, pairs in groups}})
            session.release()  # the uploaded data is no longer needed
            if payload.get("generate", True):
                stream = generate(payload["question"], evidence, payload.get("model", config.DEFAULT_MODEL),
                                  kind="multi", devices=scope)
                async with contextlib.aclosing(stream):
                    async for event in stream:
                        if session.cancelled:
                            await emit({"ev": "done", "stopped": True})
                            break
                        if event["type"] == "token":
                            await emit({"ev": "token", "text": event["text"]})
                        elif event["type"] == "done":
                            if event.get("timed_out"):
                                await emit({"ev": "notice", "text": event.get("error") or "模型超时"})
                            await emit({"ev": "done", **{k: v for k, v in event.items() if k != "type"}})
            else:
                await emit({"ev": "done", "skipped": True})
            await response.write_eof()
        finally:
            sessions.pop(sid, None)
            session.release()
        return response

    app.router.add_post("/session", new_session)
    app.router.add_post("/upload/{sid}", upload)
    app.router.add_post("/ask/{sid}", ask)
    app.router.add_post("/ask_multi/{sid}", ask_multi)
    app.router.add_post("/cancel/{sid}", cancel)
    app.router.add_get("/stats", stats)
    return app
