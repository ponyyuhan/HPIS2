import hashlib
import hmac
import os
import time
from collections import OrderedDict, defaultdict
from functools import lru_cache
from pathlib import Path
from time import perf_counter_ns

import aiohttp
import numpy as np
from aiohttp import web
from nacl.public import PrivateKey, SealedBox
from nacl.signing import VerifyKey

from . import attestation
from .codec import (
    F32,
    context_key,
    decrypt_item,
    normalized_text,
    pack,
    unpack,
)
from .network import RetrySearch, errors, post, reply, tls_client, tls_server
from .provision import read_config
from .exact_order import exact_nearest
from .store import Store, cosine_distances
from .refine_stream import (
    MEDIA_TYPE,
    apply_fetch,
    authenticate_body_vectors,
    buffered_ranking as refine_candidates,
    opened_vectors,
    send_candidates,
    streamed_request,
)


class Window:
    """Nonces and one-use refinement handles live for the request validity window."""

    def __init__(self):
        self.entries = OrderedDict()

    def expire(self):
        now = time.monotonic()
        while self.entries and next(iter(self.entries.values()))[0] <= now:
            self.entries.popitem(last=False)

    def add(self, key, value):
        self.expire()
        if key in self.entries:
            raise PermissionError("replay")
        self.entries[key] = time.monotonic() + 61, value

    def take(self, key):
        self.expire()
        if key not in self.entries:
            raise RetrySearch()
        return self.entries.pop(key)[1]


def check_deadline(nonce, exp):
    if (
        not isinstance(nonce, bytes)
        or len(nonce) != 16
        or not time.time() < exp <= time.time() + 60
    ):
        raise PermissionError("expired request")


def owner_binding(holder, aid, owner):
    """Tie an issuer-endorsed ticket to its sealed tenant without revealing the AID to HPIC."""
    return hashlib.sha256(pack(["hpis-owner-v1", holder, aid, owner])).digest()


def make_store_app(folder):
    folder = Path(folder)
    store = Store(folder / "store.sqlite")
    cfg = read_config(folder)
    hardware = "box" not in cfg
    receiver = PrivateKey.generate() if hardware else PrivateKey(cfg["box"])
    box = SealedBox(receiver)
    seen, selections, proof_fallbacks = Window(), Window(), Window()
    versions = defaultdict(int)
    app = web.Application(middlewares=[errors], client_max_size=64 << 20)

    async def execute(request):
        outer_nonce, sealed, *bound_owner = unpack(await request.read())
        aid, signed = unpack(box.decrypt(sealed))
        owner, mode, dim, beta = store.tenant(aid)
        if bound_owner:
            holder, commitment = bound_owner
            if not hmac.compare_digest(owner_binding(holder, aid, owner), commitment):
                raise PermissionError("ticket belongs to a different owner")
        if (mode == "snp") != hardware:
            raise PermissionError("incompatible runtime")
        domain, signed_aid, nonce, exp, op = unpack(VerifyKey(owner).verify(signed))
        if domain != "hpis-v1" or aid != signed_aid or nonce != outer_nonce:
            raise PermissionError()
        check_deadline(nonce, exp)
        seen.add((aid, nonce), True)
        kind = op["kind"]
        if kind in ("put", "delete"):
            if kind == "put":
                store.write(aid, mode, dim, op["records"])
            else:
                store.delete(aid, op["ids"])
            versions[aid] += 1
            return reply({"ok": True})
        streamed = request.headers.get("Accept") == MEDIA_TYPE
        result = store.search(
            aid, mode, dim, beta, op, allow_certified_response=streamed
        )
        if "selected_proofs" in result:
            # Rare legacy representations may carry an original separate from the body's vector.
            # Their continuation is restricted to the same already-authorized winners and version.
            handle = os.urandom(24)
            members = np.asarray(
                [rid for rid, _ in result["rows"]], dtype="<i8"
            ).tobytes()
            proof_fallbacks.add(handle, (aid, versions[aid], members))
            result["originals_handle"] = handle
        if "candidates" in result:
            handle = os.urandom(24)
            eligible = np.frombuffer(result["candidates"]["ids"], dtype="<i8")
            selections.add(
                handle, (aid, versions[aid], set(eligible.tolist()), op["k"])
            )
            result["handle"] = handle
            if streamed and len(result["candidates"]["sealed"]) >= 65536:
                # A small candidate set already fits in one transport chunk.
                return await send_candidates(request, result)
        return reply(result)

    async def fetch(request):
        handle, ids = unpack(await request.read())
        aid, version, allowed, k = selections.take(handle)
        if versions[aid] != version:
            raise RetrySearch()
        if len(ids) > k or len(set(ids)) != len(ids) or not allowed.issuperset(ids):
            raise PermissionError()
        start = perf_counter_ns()
        rows = store.read_items(aid, ids)
        return reply({"rows": rows, "store_us": (perf_counter_ns() - start) / 1000})

    async def originals(request):
        handle = unpack(await request.read())
        aid, version, members = proof_fallbacks.take(handle)
        if versions[aid] != version:
            raise RetrySearch()
        ids = np.frombuffer(members, dtype="<i8").tolist()
        values = store.original_records(aid, ids)
        return reply(b"".join(values[rid] for rid in ids))

    async def cleanup(_):
        store.close()

    if hardware:

        async def quote(request):
            return reply(
                await attestation.evidence(
                    unpack(await request.read()), bytes(receiver.public_key)
                )
            )

        app.router.add_post("/attest", quote)
    app.router.add_post("/execute", execute)
    app.router.add_post("/fetch", fetch)
    app.router.add_post("/selected-originals", originals)
    app.on_cleanup.append(cleanup)
    return app


def scan_filter(rows, where):
    if where["kind"] == "field":
        f, value = where["field"], normalized_text(where["value"])
        return [
            (i, r)
            for i, r in rows
            if f in r["fields"] and normalized_text(r["fields"][f]) == value
        ]
    if where["kind"] == "vector":
        with_vec = [(i, r) for i, r in rows if r["vector"] is not None]
        if not with_vec:
            return []
        ids = np.asarray([i for i, _ in with_vec], dtype=np.int64)
        q = np.frombuffer(where["q"], dtype=F32)
        matrix = np.frombuffer(
            b"".join(r["vector"] for _, r in with_vec), dtype=F32
        ).reshape(-1, len(q))
        dist = cosine_distances(matrix, q)
        return [with_vec[j] for j in exact_nearest(ids, dist, where["k"], q, matrix)]
    raise ValueError("unknown legacy predicate")


class PublicTicketVerifier:
    """Cache only the authenticated issuer statements, never a holder authorization."""

    def __init__(self, key, *, cache):
        issuer = VerifyKey(key)

        def verify(ticket):
            return unpack(issuer.verify(ticket))

        self.verify = lru_cache(maxsize=256)(verify) if cache else verify
        self.cached = cache

    def claims(self, ticket):
        if not self.cached:
            return self.verify(ticket), False
        before = self.verify.cache_info().hits
        claims = self.verify(ticket)
        return claims, self.verify.cache_info().hits != before


PUBLIC_TICKETS = web.AppKey("hpis_public_ticket_verifier", PublicTicketVerifier)


def make_hpic_app(folder, store_url, *, cache_public_ticket=False):
    if not store_url.startswith("https://"):
        raise ValueError("HPIS must use HTTPS")
    config = read_config(folder)
    issuer = PublicTicketVerifier(config["verify"], cache=cache_public_ticket)
    owner_ticket_only = config.get("owner_ticket_only", False)
    seen = Window()
    app = web.Application(middlewares=[errors], client_max_size=64 << 20)
    app[PUBLIC_TICKETS] = issuer
    upstream = None

    async def connect(_):
        nonlocal upstream
        upstream = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls_client(folder, client_cert=True))
        )
        try:
            yield
        finally:
            await upstream.close()

    async def forward(request):
        ticket, signed = unpack(await request.read())
        claims, cache_hit = issuer.claims(ticket)
        audience, expiration, holder, *commitment = claims
        if time.time() >= expiration:
            raise PermissionError()
        if audience == "hpic-owner-v1" and len(commitment) == 1:
            bound_owner = [holder, commitment[0]]
        elif audience == "hpic-search-v1" and not commitment and not owner_ticket_only:
            bound_owner = []
        else:
            raise PermissionError("invalid ticket type")
        nonce, exp, sealed, grant = unpack(VerifyKey(holder).verify(signed))
        check_deadline(nonce, exp)
        seen.add((holder, nonce), True)
        envelope = [nonce, sealed, *bound_owner]
        if "vek" in grant:
            result = await streamed_request(upstream, store_url, envelope, grant)
        else:
            result = await post(upstream, store_url, "/execute", envelope, "hpis")
        if "stats" not in result:
            return reply(result)
        stats = result["stats"]
        if "candidates" in result:
            start = perf_counter_ns()
            q = np.frombuffer(grant["q"], dtype=F32)
            vek = context_key(grant["vek"])
            candidates = result.pop("candidates")
            top, opened = refine_candidates(candidates, q, vek, grant["k"])
            stats["refined_rows"] = opened
            stats["refine_us"] = (perf_counter_ns() - start) / 1000
            fetched = await post(
                upstream, store_url, "/fetch", [result["handle"], top], "hpis"
            )
            apply_fetch(result, fetched)
        start = perf_counter_ns()
        dek = context_key(grant.get("dek"))
        records = []
        for rid, value in result["rows"]:
            body = decrypt_item(dek, b"body", rid, value) if dek is not None else value
            records.append((rid, unpack(body)))
        if "scan" in grant:
            records = scan_filter(records, grant["scan"])
        stats["open_and_filter_us"] = (perf_counter_ns() - start) / 1000
        if cache_public_ticket:
            stats.update(
                issuer_cache_hit=cache_hit,
                ticket_expiry_checks=1,
                holder_signature_checks=1,
                owner_signature_checks=1,
                nonce_checks=2,
            )
        if "selected_proofs" in result:
            start = perf_counter_ns()
            query = np.frombuffer(grant["q"], dtype=F32)
            vek = context_key(grant["vek"])
            ids = np.asarray([rid for rid, _ in records], dtype=np.int64)
            vectors = authenticate_body_vectors(
                records, result["selected_proofs"], len(query), vek
            )
            if vectors is None:
                # Preserve the independently authenticated authoritative index record if an
                # older/custom record carries a different representation inside its body.
                original = await post(
                    upstream,
                    store_url,
                    "/selected-originals",
                    result["originals_handle"],
                    "hpis",
                )
                vectors = opened_vectors(ids, original, query, vek)
                stats["candidate_bytes"] += len(original)
                stats["candidate_wire_data_bytes"] += len(original)
                stats["certified_one_round"] = False
            distances = cosine_distances(vectors, query)
            order = exact_nearest(ids, distances, len(ids), query, vectors)
            records = [records[j] for j in order]
            stats.update(
                refined_rows=len(records), refine_us=(perf_counter_ns() - start) / 1000
            )
        return reply(
            {"records": [(rid, obj["content"]) for rid, obj in records], "stats": stats}
        )

    async def quote(request):
        challenge = unpack(await request.read())
        if not isinstance(challenge, bytes) or len(challenge) != 32:
            raise ValueError("invalid challenge")
        return reply(await post(upstream, store_url, "/attest", challenge, "hpis"))

    app.router.add_post("/attest", quote)
    app.cleanup_ctx.append(connect)
    app.router.add_post("/request", forward)
    return app


def serve(role, folder, host, port, store_url):
    app = make_store_app(folder) if role == "hpis" else make_hpic_app(folder, store_url)
    web.run_app(
        app,
        host=host,
        port=port,
        ssl_context=tls_server(folder, role == "hpis"),
        access_log=None,
    )
