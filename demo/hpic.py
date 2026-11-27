"""HPIC for the demo: the stock HPIS/HPIC relay plus an /ask route that runs the model.

/ask receives the same holder-signed envelope as /request (anonymous ticket,
sealed owner-signed operation for HPIS, grant with the data key).  HPIC checks
the ticket, relays the sealed operation to HPIS over mutual TLS, opens only the
returned records with the DEK, builds the prompt and streams the model answer
back to the device as NDJSON events.

/ask_multi (多端协同) takes several such envelopes, one per device: each carries that
device's anonymous ticket, its sealed search for HPIS and its own data key.  HPIC
checks every ticket, runs one encrypted search per device in HPIS, opens only
the returned records of each, merges them tagged by device and makes one model
call for the whole question.

Two retrieval semantics are served from the same encrypted store:
  * "v2"  HPIS 2.0: SSE filter + encrypted vector Top-K evaluated by HPIS; only K
          ciphertexts cross the cloud link.
  * "v1"  HPIS 1.0 style: HPIS returns every ciphertext of the AID; HPIC decrypts
          all of them and searches in plaintext.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import aiohttp
import numpy as np
from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.signing import VerifyKey

from hpis.codec import F32, decrypt_item, pack, unpack
from hpis.network import tls_client
from hpis.services import PUBLIC_TICKETS, Window, check_deadline, make_hpic_app

from . import config, records, retrieval
from .answer import generate
from .netem import link_from, now, pace


def line(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


async def post_raw(session, base, route, value, role):
    body = pack(value)
    async with session.post(
        base.rstrip("/") + route,
        data=body,
        server_hostname=role + ".internal",
        headers={"Content-Type": "application/msgpack"},
        allow_redirects=False,
    ) as response:
        response.raise_for_status()
        raw = await response.read()
    return len(body), len(raw), unpack(raw)


def make_demo_hpic_app(folder, store_url):
    app = make_hpic_app(folder, store_url, cache_public_ticket=True)
    issuer = app[PUBLIC_TICKETS]
    seen = Window()
    state = {}

    async def connect(_):
        state["upstream"] = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls_client(folder, client_cert=True)),
            timeout=aiohttp.ClientTimeout(total=600),
        )
        try:
            yield
        finally:
            await state["upstream"].close()

    def open_envelope(ticket, signed):
        """Check the anonymous ticket and the holder's signature: (nonce, sealed op, grant, owner binding)."""
        claims, _ = issuer.claims(ticket)
        audience, expiration, holder, *commitment = claims
        if time.time() >= expiration or audience not in ("hpic-search-v1", "hpic-owner-v1"):
            raise PermissionError("ticket")
        bound_owner = [holder, commitment[0]] if audience == "hpic-owner-v1" and commitment else []
        nonce, exp, sealed, grant = unpack(VerifyKey(holder).verify(signed))
        check_deadline(nonce, exp)
        seen.add((holder, nonce), True)
        return nonce, sealed, grant, bound_owner

    async def ask(request):
        t_in = now()
        ticket, signed = unpack(await request.read())
        nonce, sealed, grant, bound_owner = open_envelope(ticket, signed)
        ask = grant["ask"]
        lane = ask.get("lane", "v2")
        plan = ask.get("plan") or {}
        kind = plan.get("kind") or "mail"
        vision = bool(ask.get("vision")) and kind == "photo"
        cloud = link_from(ask.get("cloud"), config.Network().cloud)

        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
        await response.prepare(request)

        async def emit(obj):
            obj["hpic_ms"] = round((now() - t_in) * 1000, 3)
            await response.write(line(obj))

        await emit({"ev": "auth", "ok": True, "ticket": "匿名票据有效", "us": round((now() - t_in) * 1e6)})

        # ---- cloud hop: HPIC -> HPIS -> HPIC (mutual TLS) -------------------
        envelope = [nonce, sealed, *bound_owner]
        t0 = now()
        up, down, result = await post_raw(state["upstream"], store_url, "/execute", envelope, "hpis")
        await pace(t0, cloud, up + down, legs=2)
        stats = result.get("stats", {})
        rows = result.get("rows", [])
        await emit(
            {
                "ev": "hpis",
                "lane": lane,
                "up_bytes": up,
                "down_bytes": down,
                "rows": len(rows),
                "store_ms": round(stats.get("store_us", 0) / 1000, 3),
                "vector_rows": stats.get("vector_rows", 0),
                "body_bytes": stats.get("body_bytes", 0),
                "hop_ms": round((now() - t0) * 1000, 3),
                "cipher_preview": rows[0][1][:48].hex() if rows else "",
            }
        )

        # ---- open the records that crossed the link ---------------------------
        t1 = now()
        dek = AESGCM(grant["dek"])
        opened = []
        for rid, value in rows:
            body = unpack(decrypt_item(dek, b"body", rid, value))
            opened.append((rid, body))
        decrypt_ms = (now() - t1) * 1000
        await emit({"ev": "decrypt", "rows": len(opened), "ms": round(decrypt_ms, 3)})

        t2 = now()
        if lane == "v1":
            # HPIS 1.0: the whole mailbox is in HPIC memory; search it in plaintext.
            keep = [(rid, b) for rid, b in opened if retrieval.matches(b.get("fields") or {}, plan)]
            ids = np.asarray([rid for rid, _ in keep], dtype=np.int64)
            vecs = (
                np.frombuffer(b"".join(b["vector"] for _, b in keep), dtype=F32).reshape(len(keep), -1)
                if keep
                else np.zeros((0, 1), F32)
            )
            q = np.frombuffer(ask["qvec"], dtype=F32)
            order = retrieval.top_k(ids, vecs, q, config.RETRIEVE_K)
            ranked = [(int(ids[i]), keep[i][1]["content"]) for i in order]
            candidates = len(keep)
        else:
            ranked = [(rid, b["content"]) for rid, b in opened]
            candidates = stats.get("vector_rows", len(opened))
        evidence_k = int(ask.get("evidence_k") or config.evidence_k(kind))
        evidence_pairs = retrieval.post_filter(ranked, plan, evidence_k)
        cards, evidence = records.evidence(kind, evidence_pairs, vision=vision)
        await emit(
            {
                "ev": "evidence",
                "kind": kind,
                "candidates": int(candidates),
                "rank_ms": round((now() - t2) * 1000, 3),
                "items": cards,
            }
        )

        # ---- business model inside HPIC -----------------------------------
        if ask.get("generate", True):
            stream = generate(
                ask["question"], evidence, ask.get("model", config.DEFAULT_MODEL), kind=kind, note=ask.get("note")
            )
            async with contextlib.aclosing(stream):
                async for event in stream:
                    if event["type"] == "token":
                        await emit({"ev": "token", "text": event["text"]})
                    elif event["type"] == "done":
                        if event.get("timed_out"):
                            await emit({"ev": "notice", "text": event.get("error") or "模型超时"})
                        await emit({"ev": "done", **{k: v for k, v in event.items() if k != "type"}})
        else:
            await emit({"ev": "done", "skipped": True})
        await response.write_eof()
        return response

    async def ask_multi(request):
        """Several devices' sealed searches, one answer (多端协同)."""
        t_in = now()
        opened = [open_envelope(ticket, signed) for ticket, signed in unpack(await request.read())]
        if not opened:
            raise ValueError("no device request")
        asks = [grant["ask"] for _, _, grant, _ in opened]
        lead = asks[0]
        cloud = link_from(lead.get("cloud"), config.Network().cloud)
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
        await response.prepare(request)
        lock = asyncio.Lock()

        async def emit(obj):
            obj["hpic_ms"] = round((now() - t_in) * 1000, 3)
            async with lock:
                await response.write(line(obj))

        await emit({"ev": "auth", "ok": True, "ticket": "匿名票据有效", "tickets": len(opened),
                    "us": round((now() - t_in) * 1e6)})

        async def search(part, ask):
            nonce, sealed, _, bound_owner = part
            t0 = now()
            up, down, result = await post_raw(state["upstream"], store_url, "/execute", [nonce, sealed, *bound_owner], "hpis")
            await pace(t0, cloud, up + down, legs=2)
            stats, rows = result.get("stats", {}), result.get("rows", [])
            await emit(
                {
                    "ev": "device_search",
                    "device": ask.get("device"),
                    "kind": ask.get("kind"),
                    "rows": len(rows),
                    "vector_rows": stats.get("vector_rows", 0),
                    "store_ms": round(stats.get("store_us", 0) / 1000, 3),
                    "up_bytes": up,
                    "down_bytes": down,
                    "body_bytes": stats.get("body_bytes", 0),
                    "hop_ms": round((now() - t0) * 1000, 3),
                }
            )
            return rows

        # one encrypted search per device AID, side by side
        found = await asyncio.gather(*(search(part, ask) for part, ask in zip(opened, asks)))

        t1 = now()
        groups = []
        for (_, _, grant, _), ask, rows in zip(opened, asks, found):
            dek = AESGCM(grant["dek"])
            ranked = [(rid, unpack(decrypt_item(dek, b"body", rid, value))["content"]) for rid, value in rows]
            kind = ask.get("kind") or "mail"
            k = int(ask.get("evidence_k") or config.evidence_k(kind))
            groups.append((ask.get("device"), kind, retrieval.post_filter(ranked, ask.get("plan") or {}, k)))
        await emit({"ev": "decrypt", "rows": sum(len(r) for r in found), "ms": round((now() - t1) * 1000, 3),
                    "devices": {ask.get("device"): len(r) for ask, r in zip(asks, found)}})
        cards, evidence = records.multi_evidence(groups)
        await emit({"ev": "evidence", "mode": "multi", "items": cards,
                    "devices": {device: len(pairs) for device, _, pairs in groups}})

        scope = [{"id": a.get("device"), "kind": a.get("kind"), "available": True, "note": a.get("note")} for a in asks]
        if ask_generate(lead):
            stream = generate(lead["question"], evidence, lead.get("model", config.DEFAULT_MODEL), kind="multi",
                              devices=scope)
            async with contextlib.aclosing(stream):
                async for event in stream:
                    if event["type"] == "token":
                        await emit({"ev": "token", "text": event["text"]})
                    elif event["type"] == "done":
                        if event.get("timed_out"):
                            await emit({"ev": "notice", "text": event.get("error") or "模型超时"})
                        await emit({"ev": "done", **{k: v for k, v in event.items() if k != "type"}})
        else:
            await emit({"ev": "done", "skipped": True})
        await response.write_eof()
        return response

    app.cleanup_ctx.append(connect)
    app.router.add_post("/ask", ask)
    app.router.add_post("/ask_multi", ask_multi)
    return app


def ask_generate(ask: dict) -> bool:
    return bool(ask.get("generate", True))
