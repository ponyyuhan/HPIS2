"""Ordinary cloud for the "端侧直传普通云" lane.

The device uploads the whole collection of the asked kind (emails with their
attachments, the photo album with its pictures, the watch or car log) in
size-limited batches for every question.  The cloud parses each batch as it
arrives, then filters on the plaintext fields, narrows the candidates to a
lexical shortlist (at most ``config.CLOUD_SHORTLIST``), computes vectors for the
shortlist with the same open embedding model within ``config.CLOUD_EMBED_BUDGET_S``
(stopping early and ranking what it has), ranks with the same exact kernel as
HPIS and asks the same model with the same evidence budget as HPIC.  Everything
it receives is plaintext.

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
    """Everything the ordinary cloud received for one question (plaintext)."""

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
        """Drop the uploaded plaintext once the evidence is chosen (the session stays cancellable)."""
        self.fields, self.dates, self.raw, self.lex, self.images = [], [], [], [], []


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
    sessions: dict[str, Session] = {}
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
        kind = "mail"
        if request.can_read_body:
            with contextlib.suppress(ValueError):
                kind = (json.loads(await request.read() or b"{}") or {}).get("kind") or "mail"
        sid = os.urandom(8).hex()
        sessions[sid] = Session(kind)
        return web.json_response({"sid": sid})

    async def upload(request):
        session = sessions.get(request.match_info["sid"])
        if session is None or session.cancelled:
            raise web.HTTPGone(text="session closed")
        blob = await request.read()
        session.add(blob)
        return web.json_response({"rows": len(session.raw), "bytes": session.bytes})

    async def cancel(request):
        session = sessions.pop(request.match_info["sid"], None)
        if session is not None:
            session.cancelled = True
        return web.json_response({"ok": True, "cancelled": session is not None})

    async def stats(request):
        return web.json_response({"sessions": len(sessions), **busy})

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
            t0 = perf_counter()
            # The cloud reads everything, so it can filter on exact plaintext fields and dates.
            keep = [
                i
                for i, f in enumerate(session.fields)
                if retrieval.matches(f, plan) and retrieval.in_window(session.dates[i], plan)
            ]
            filter_ms = (perf_counter() - t0) * 1000
            # No index survives between questions: a lexical shortlist, then vectors for it only.
            t1 = perf_counter()
            query = " ".join(x for x in (plan.get("semantic"), payload.get("question")) if x)
            order = retrieval.lexical_order([session.lex[i] for i in keep], query, config.CLOUD_SHORTLIST)
            short = [keep[j] for j in order]
            lex_ms = (perf_counter() - t1) * 1000
            t2 = perf_counter()
            budget = float(payload.get("embed_budget_s") or config.CLOUD_EMBED_BUDGET_S)
            embedded, vecs, budget_hit = [], [], False
            stored = os.environ.get("HPIS_DEMO_CLOUD_EMBED") == "stored"  # offline tests only
            loop = asyncio.get_running_loop()
            if stored:
                encode = None
                q = np.frombuffer(bytes.fromhex(payload["qvec"]), dtype=np.float32)
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
                    if session.cancelled:
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
            if session.cancelled:
                await emit({"ev": "done", "stopped": True})
                await response.write_eof()
                return response
            matrix = np.concatenate(vecs) if vecs else np.zeros((0, q.size), np.float32)
            ids = np.asarray([int(json.loads(session.raw[i])["id"]) for i in embedded], dtype=np.int64)
            top = retrieval.top_k(ids, np.asarray(matrix, dtype=np.float32), q, config.RETRIEVE_K)
            evidence_k = int(payload.get("evidence_k") or config.evidence_k(kind))
            pairs = [(int(ids[i]), session.record(embedded[i], image=vision)) for i in top][:evidence_k]
            await emit(
                {
                    "ev": "search",
                    "kind": kind,
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
            )
            if budget_hit:
                await emit(
                    {
                        "ev": "notice",
                        "text": f"普通云 {budget:g} 秒内只现算了 {len(embedded)} 条候选的向量"
                        f"（共 {len(short)} 条），按已算出的部分排序",
                    }
                )
            cards, evidence = records.evidence(kind, pairs, vision=vision)
            await emit({"ev": "evidence", "kind": kind, "items": cards})
            session.release()  # the plaintext is no longer needed (an operator could have kept it)
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

    app.router.add_post("/session", new_session)
    app.router.add_post("/upload/{sid}", upload)
    app.router.add_post("/ask/{sid}", ask)
    app.router.add_post("/cancel/{sid}", cancel)
    app.router.add_get("/stats", stats)
    return app
