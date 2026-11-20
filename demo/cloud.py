"""Ordinary cloud for the "端侧直传普通云" lane.

The device uploads its mailbox (body text, attachment text and the device's own
embeddings) in size-limited batches for every question.  The cloud parses each
batch as it arrives, then filters, ranks and asks the same model with the same
evidence budget as HPIC.  Everything it receives is plaintext.
"""

from __future__ import annotations

import json
import os
from time import perf_counter

import msgpack
import numpy as np
from aiohttp import web

from . import config, retrieval
from .answer import generate
from .mailbox import card, model_text


def line(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


class Session:
    """Everything the ordinary cloud received for one question (plaintext)."""

    def __init__(self):
        self.fields, self.dates, self.raw = [], [], []
        self.bytes = 0
        self.parse_ms = 0.0

    def add(self, blob: bytes):
        start = perf_counter()
        for raw in msgpack.unpackb(blob, raw=False):
            mail = json.loads(raw)
            self.fields.append(retrieval.index_fields(mail))
            self.dates.append(mail["date"])
            self.raw.append(raw)
        self.bytes += len(blob)
        self.parse_ms += (perf_counter() - start) * 1000


_EMBEDDER = None


def embedder():
    """Same open model the device uses; the ordinary cloud runs it on what it received."""
    global _EMBEDDER
    if _EMBEDDER is None:
        from .embed import Embedder

        _EMBEDDER = Embedder()
    return _EMBEDDER


def make_cloud_app():
    sessions: dict[str, Session] = {}
    app = web.Application(client_max_size=64 << 20)

    async def warm(_app):
        if os.environ.get("HPIS_DEMO_CLOUD_EMBED") != "stored":
            embedder().encode_query("预热")

    app.on_startup.append(warm)

    async def new_session(request):
        sid = os.urandom(8).hex()
        sessions[sid] = Session()
        return web.json_response({"sid": sid})

    async def upload(request):
        session = sessions[request.match_info["sid"]]
        blob = await request.read()
        session.add(blob)
        return web.json_response({"rows": len(session.raw), "bytes": session.bytes})

    async def ask(request):
        t_in = perf_counter()
        session = sessions.pop(request.match_info["sid"])
        payload = json.loads(await request.read())
        plan = payload.get("plan") or {}
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
        await response.prepare(request)

        async def emit(obj):
            obj["cloud_ms"] = round((perf_counter() - t_in) * 1000, 3)
            await response.write(line(obj))

        t0 = perf_counter()
        # The cloud reads everything, so it can filter on exact plaintext fields and dates.
        keep = [
            i
            for i, f in enumerate(session.fields)
            if retrieval.matches(f, plan) and retrieval.in_window(session.dates[i], plan)
        ]
        filter_ms = (perf_counter() - t0) * 1000
        t1 = perf_counter()
        mails = [json.loads(session.raw[i]) for i in keep]
        if os.environ.get("HPIS_DEMO_CLOUD_EMBED") == "stored":
            # Offline tests only: reuse the stored vectors and the device's query vector.
            stored = np.fromfile(config.DATA / "embeddings.f32", dtype=np.float32)
            stored = stored.reshape(-1, json.loads((config.DATA / "embeddings.json").read_text())["dim"])
            vecs = stored[[int(m["id"]) - 1 for m in mails]] if mails else np.zeros((0, stored.shape[1]), np.float32)
            q = np.frombuffer(bytes.fromhex(payload["qvec"]), dtype=np.float32)
        else:
            from .embed import doc_text

            model = embedder()
            vecs = model.encode_docs([doc_text(m) for m in mails]) if mails else np.zeros((0, 1), np.float32)
            q = np.asarray(model.encode_query(plan.get("semantic") or payload["question"]), dtype=np.float32)
        embed_ms = (perf_counter() - t1) * 1000
        ids = np.asarray([int(m["id"]) for m in mails], dtype=np.int64)
        order = retrieval.top_k(ids, np.asarray(vecs, dtype=np.float32), q, config.RETRIEVE_K)
        ranked = [(int(ids[i]), mails[i]) for i in order]
        evidence_pairs = ranked[: int(payload.get("evidence_k", config.EVIDENCE_K))]
        await emit(
            {
                "ev": "search",
                "scanned": len(session.fields),
                "candidates": len(keep),
                "parse_ms": round(session.parse_ms, 1),
                "filter_ms": round(filter_ms, 2),
                "embed_ms": round(embed_ms, 1),
                "search_ms": round((perf_counter() - t0) * 1000, 3),
                "bytes": session.bytes,
            }
        )
        await emit({"ev": "evidence", "items": [card(m) for _, m in evidence_pairs]})
        evidence = []
        for n, (_, mail) in enumerate(evidence_pairs, 1):
            item = model_text(mail)
            item["n"] = n
            evidence.append(item)
        del session
        if payload.get("generate", True):
            async for event in generate(payload["question"], evidence, payload.get("model", config.DEFAULT_MODEL)):
                if event["type"] == "token":
                    await emit({"ev": "token", "text": event["text"]})
                elif event["type"] == "done":
                    await emit({"ev": "done", **{k: v for k, v in event.items() if k != "type"}})
        else:
            await emit({"ev": "done", "skipped": True})
        await response.write_eof()
        return response

    app.router.add_post("/session", new_session)
    app.router.add_post("/upload/{sid}", upload)
    app.router.add_post("/ask/{sid}", ask)
    return app
