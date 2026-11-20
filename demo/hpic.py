"""HPIC for the demo: the stock HPIS/HPIC relay plus an /ask route that runs the model.

/ask receives the same holder-signed envelope as /request (anonymous ticket,
sealed owner-signed operation for HPIS, grant with the data key).  HPIC checks
the ticket, relays the sealed operation to HPIS over mutual TLS, opens only the
returned records with the DEK, builds the prompt and streams the model answer
back to the device as NDJSON events.

Two retrieval semantics are served from the same encrypted store:
  * "v2"  HPIS 2.0: SSE filter + encrypted vector Top-K evaluated by HPIS; only K
          ciphertexts cross the cloud link.
  * "v1"  HPIS 1.0 style: HPIS returns every ciphertext of the AID; HPIC decrypts
          all of them and searches in plaintext.
"""

from __future__ import annotations

import json
import time
from time import perf_counter

import aiohttp
import numpy as np
from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.signing import VerifyKey

from hpis.codec import F32, decrypt_item, pack, unpack
from hpis.network import tls_client
from hpis.services import PUBLIC_TICKETS, Window, check_deadline, make_hpic_app

from . import config, retrieval
from .answer import generate
from .mailbox import card, model_text
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

    async def ask(request):
        t_in = now()
        ticket, signed = unpack(await request.read())
        claims, _ = issuer.claims(ticket)
        audience, expiration, holder, *commitment = claims
        if time.time() >= expiration or audience not in ("hpic-search-v1", "hpic-owner-v1"):
            raise PermissionError("ticket")
        bound_owner = [holder, commitment[0]] if audience == "hpic-owner-v1" and commitment else []
        nonce, exp, sealed, grant = unpack(VerifyKey(holder).verify(signed))
        check_deadline(nonce, exp)
        seen.add((holder, nonce), True)
        ask = grant["ask"]
        lane = ask.get("lane", "v2")
        plan = ask.get("plan") or {}
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
        evidence_pairs = retrieval.post_filter(ranked, plan, int(ask.get("evidence_k", config.EVIDENCE_K)))
        await emit(
            {
                "ev": "evidence",
                "candidates": int(candidates),
                "rank_ms": round((now() - t2) * 1000, 3),
                "items": [card(m) for _, m in evidence_pairs],
            }
        )

        # ---- business model inside HPIC -----------------------------------
        evidence = []
        for n, (_, mail) in enumerate(evidence_pairs, 1):
            item = model_text(mail)
            item["n"] = n
            evidence.append(item)
        if ask.get("generate", True):
            async for event in generate(ask["question"], evidence, ask.get("model", config.DEFAULT_MODEL)):
                if event["type"] == "token":
                    await emit({"ev": "token", "text": event["text"]})
                elif event["type"] == "done":
                    await emit({"ev": "done", **{k: v for k, v in event.items() if k != "type"}})
        else:
            await emit({"ev": "done", "skipped": True})
        await response.write_eof()
        return response

    app.cleanup_ctx.append(connect)
    app.router.add_post("/ask", ask)
    return app
