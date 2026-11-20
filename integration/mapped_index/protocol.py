"""Independent deployment profile: same TLS/holder/owner protocol with HPIC-side exact ranking."""

import hmac
import os
import time
from pathlib import Path
from time import perf_counter_ns

import aiohttp
import numpy as np
from aiohttp import web
from nacl.public import PrivateKey, SealedBox
from nacl.signing import VerifyKey

from hpis.codec import F32, context_key, decrypt_item, pack, unit_vectors, unpack
from hpis.network import post, reply, tls_client, tls_server
from hpis.provision import read_config
from hpis.sdk import Device
from hpis.services import (
    PUBLIC_TICKETS,
    Window,
    check_deadline,
    make_hpic_app,
    make_store_app,
    owner_binding,
)
from hpis.store import Store

from .index import (
    PROFILE,
    MappedImage,
    current_revision,
    image_path,
    install_revisions,
    locator,
    publish,
    header_owner,
)


def target_binding(blind, index_id, k, q, rq):
    # HPIS must bind its owner-signed target to HPIC's separate holder grant.
    # A fresh secret blind prevents the digest at HPIS from linking or guessing queries.
    if not isinstance(blind, bytes) or len(blind) != 32:
        raise ValueError("invalid query blind")
    return hmac.digest(blind, pack([PROFILE, index_id, k, q, rq]), "sha256")


class MappedDevice(Device):
    def __init__(self, folder, url, *, pipeline=True):
        super().__init__(folder, url)
        self.pipeline = pipeline
        self.index_id = locator(self.aid, bytes(self.owner.verify_key))
        self.delegation = bytes(
            self.owner.sign(
                pack([PROFILE, self.index_id, bytes(self.holder.verify_key)])
            )
        )

    async def vector(self, vector, k=10, *, refine=False, predicates=(), combine="all"):
        if not refine or predicates or self.mode not in ("plain", "encrypted"):
            return await super().vector(
                vector, k, refine=refine, predicates=predicates, combine=combine
            )
        if type(k) is not int or not 1 <= k <= 10000:
            raise ValueError("invalid k")
        unit = unit_vectors(vector, self.dim)
        if unit.ndim != 1:
            raise ValueError("one query vector expected")
        raw = unit.tobytes()
        # This value reaches the delegated HPIC only. HPIS receives no query direction.
        rotated = (
            (unit @ self.vectors.matrix).tobytes() if self.mode == "encrypted" else None
        )
        op = {"kind": PROFILE, "id": self.index_id, "k": k}
        grant = {
            "streaming": self.pipeline,
            "id": self.index_id,
            "k": k,
            "q": raw,
            "rq": rotated,
            "dek": self.dek,
            "vek": self.vek,
            "delegate": self.delegation,
        }
        preposition_bytes = 0
        preposition_us = 0
        for attempt in range(3):
            grant["blind"] = os.urandom(32)
            op["target_binding"] = target_binding(
                grant["blind"], self.index_id, k, raw, rotated
            )
            try:
                result = await post(
                    self.session,
                    self.url,
                    "/mapped/query",
                    self.envelope(op, grant),
                    "hpic",
                )
                if result.get("index_changed"):
                    preposition_bytes += result["snapshot_bytes"]
                    preposition_us += result["build_us"]
                    if attempt == 2:
                        raise RuntimeError(
                            "index changed repeatedly during ranking; retry the request"
                        )
                    continue
                result["stats"]["preposition_bytes"] = preposition_bytes
                result["stats"]["preposition_us"] = preposition_us
                return result
            except aiohttp.ClientResponseError as exc:
                if exc.status != 409 or attempt == 2:
                    raise


def mapped_store(folder, directory):
    folder, directory = Path(folder), Path(directory)
    app = make_store_app(folder)
    dbstore = Store(folder / "store.sqlite")
    install_revisions(dbstore.db)
    box = SealedBox(PrivateKey(read_config(folder)["box"]))
    seen = Window()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    async def fetch(request):
        stream = request.content_type == "application/octet-stream"
        if stream:
            header_size = int.from_bytes(await request.content.readexactly(4), "big")
            if header_size > 65536:
                raise ValueError("invalid metadata header size")
            (
                nonce,
                inner,
                owner_bound,
                index_id,
                selected_k,
                expected_target,
                revision,
            ) = unpack(await request.content.readexactly(header_size))
        else:
            (
                nonce,
                inner,
                owner_bound,
                index_id,
                selected_k,
                expected_target,
                revision,
                ids,
            ) = unpack(await request.read())
        aid, signed = unpack(box.decrypt(inner))
        tenant = dbstore.tenant(aid)
        owner, mode, dim, beta = tenant
        domain, signed_aid, signed_nonce, exp, op = unpack(
            VerifyKey(owner).verify(signed)
        )
        if domain != "hpis-v1" or aid != signed_aid or nonce != signed_nonce:
            raise PermissionError()
        check_deadline(nonce, exp)
        if owner_bound and not hmac.compare_digest(
            owner_binding(owner_bound[0], aid, owner), owner_bound[1]
        ):
            raise PermissionError()
        if (
            op.get("kind") != PROFILE
            or op["id"] != locator(aid, owner)
            or op["id"] != index_id
            or op["k"] != selected_k
            or not hmac.compare_digest(op["target_binding"], expected_target)
            or mode not in ("plain", "encrypted")
        ):
            raise PermissionError()
        k = op["k"]
        if type(k) is not int or not 1 <= k <= 10000:
            raise ValueError("invalid selection budget")
        seen.add((aid, nonce), True)
        if stream:
            expected = int.from_bytes(await request.content.readexactly(4), "big")
            if expected > 10 * k + 16:
                raise ValueError("oversized index result")
            ids = unpack(await request.content.readexactly(expected))
            if await request.content.read(1):
                raise ValueError("unexpected trailing frame data")
        if (
            type(k) is not int
            or not 1 <= k <= 10000
            or not isinstance(ids, list)
            or len(ids) > k
            or len(set(ids)) != len(ids)
        ):
            raise PermissionError()
        if any(type(i) is not int for i in ids):
            raise ValueError("invalid record ID")
        # A streamed sender can delay its final ID frame past the signed deadline.
        check_deadline(nonce, exp)
        start = perf_counter_ns()
        # Both version validation and selected body lookup read one consistent SQLite version.
        dbstore.db.execute("BEGIN")
        try:
            current = current_revision(dbstore.db, aid)
            if current != revision:
                bytes_written = publish(dbstore.db, directory, aid, tenant, current)
                return reply(
                    {
                        "retry": True,
                        "snapshotBytes": bytes_written,
                        "snapshotUs": (perf_counter_ns() - start) / 1000,
                    }
                )
            # HPIC is authorized to choose at most k IDs within this tenant.
            # Validate that every returned ID belongs to the tenant's vector relation.
            by_id = {}
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                marks = ",".join("?" for _ in chunk)
                sql = (
                    "SELECT d.rid,d.body FROM docs d JOIN vectors v USING(aid,rid) "
                    f"WHERE d.aid=? AND d.rid IN ({marks})"
                )
                by_id.update(dbstore.db.execute(sql, (aid, *chunk)))
            if len(by_id) != len(ids):
                raise PermissionError()
            rows = [(rid, by_id[rid]) for rid in ids]
        finally:
            dbstore.db.rollback()
        return reply({"rows": rows, "store_us": (perf_counter_ns() - start) / 1000})

    async def close(_):
        dbstore.close()

    app.router.add_post("/mapped/fetch", fetch)
    app.on_cleanup.append(close)
    return app


def mapped_hpic(folder, directory, store_url):
    app = make_hpic_app(folder, store_url, cache_public_ticket=True)
    cfg = read_config(folder)
    verifier = app[PUBLIC_TICKETS]
    directory = Path(directory)
    windows = Window()
    state = {}
    images = {}

    async def connect(_):
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls_client(folder, client_cert=True))
        ) as upstream:
            state["upstream"] = upstream
            yield
        images.clear()

    app.cleanup_ctx.append(connect)

    async def search(request):
        ticket, signed = unpack(await request.read())
        claims, cache_hit = verifier.claims(ticket)
        aud, until, holder, *commitment = claims
        if time.time() >= until:
            raise PermissionError()
        if aud == "hpic-owner-v1" and len(commitment) == 1:
            bound = [holder, commitment[0]]
        elif (
            aud == "hpic-search-v1"
            and not commitment
            and not cfg.get("owner_ticket_only")
        ):
            bound = []
        else:
            raise PermissionError()
        nonce, exp, sealed, grant = unpack(VerifyKey(holder).verify(signed))
        check_deadline(nonce, exp)
        windows.add((holder, nonce), True)
        idx, k = grant["id"], grant["k"]
        path = image_path(directory, idx)
        entry = images.get(idx)
        if entry is None:
            try:
                public = header_owner(path)
                scope = unpack(VerifyKey(public).verify(grant["delegate"]))
                if scope != [PROFILE, idx, holder]:
                    raise PermissionError()
                image = MappedImage(path)
                if image.owner != public:
                    raise PermissionError()
                entry = (image, {holder: grant["delegate"]})
                images[idx] = entry
            except FileNotFoundError:
                pass
        image = None
        if entry is not None:
            image, verified = entry
            delegate = grant["delegate"]
            if verified.get(holder) != delegate:
                scope = unpack(VerifyKey(image.owner).verify(delegate))
                if scope != [PROFILE, idx, holder]:
                    raise PermissionError()
                # A single last delegation per AID is enough. All entries here are public.
                verified.clear()
                verified[holder] = delegate
        rank_us = 0.0
        rank_diag = {}
        opened = 0
        rank_error = None
        approved = target_binding(grant["blind"], idx, k, grant["q"], grant["rq"])
        frame = [
            nonce,
            sealed,
            bound,
            idx,
            k,
            approved,
            None if image is None else image.revision,
        ]
        header = pack(frame)

        def rank_rows():
            nonlocal rank_us, rank_diag, opened, rank_error
            rows = []
            t0 = perf_counter_ns()
            try:
                if image is not None:
                    q = np.frombuffer(grant["q"], F32)
                    rq = (
                        np.frombuffer(grant["rq"], F32)
                        if grant["rq"] is not None
                        else q
                    )
                    rows, opened = image.exact(q, rq, k, grant["vek"])
            except Exception as exc:
                rank_error = exc
            rank_us = (perf_counter_ns() - t0) / 1000
            rank_diag = dict(getattr(image, "diagnostic", {}))
            return rows

        async def payload():
            yield len(header).to_bytes(4, "big") + header
            tail = pack(rank_rows())
            yield len(tail).to_bytes(4, "big") + tail

        if grant["streaming"]:
            async with state["upstream"].post(
                store_url.rstrip("/") + "/mapped/fetch",
                data=payload(),
                server_hostname="hpis.internal",
                headers={"Content-Type": "application/octet-stream"},
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.status != 200:
                    raise ValueError("unexpected storage response")
                result = unpack(await response.read())
        else:
            result = await post(
                state["upstream"],
                store_url,
                "/mapped/fetch",
                [*frame, rank_rows()],
                "hpis",
            )
        if rank_error is not None:
            raise rank_error
        if result.get("retry"):
            if images.get(idx) is entry:
                images.pop(idx, None)
            return reply(
                {
                    "index_changed": True,
                    "snapshot_bytes": result["snapshotBytes"],
                    "build_us": result["snapshotUs"],
                }
            )
        if image is None:
            raise ValueError("missing index")
        check_deadline(nonce, exp)
        if time.time() >= until:
            raise PermissionError()
        start = perf_counter_ns()
        cipher = context_key(grant["dek"])
        outputs = []
        for rid, data in result["rows"]:
            raw = decrypt_item(cipher, b"body", rid, data) if cipher else data
            outputs.append([rid, unpack(raw)["content"]])
        stats = {
            "body_rows": len(outputs),
            "body_bytes": sum(len(v) for _, v in result["rows"]),
            "vector_rows": image.size,
            "opened_vectors": opened,
            "rank_us": rank_us,
            "open_and_filter_us": (perf_counter_ns() - start) / 1000,
            "store_us": result["store_us"],
            "revision": image.revision,
            "online_cloud_calls": 1,
            "pipelined": bool(grant["streaming"]),
        }
        stats.update(rank_diag)
        stats.update(
            issuer_cache_hit=cache_hit,
            # Admission and response release each compare the current clock.
            ticket_expiry_checks=2,
            holder_signature_checks=1,
            owner_signature_checks=1,
            nonce_checks=2,
        )
        return reply({"records": outputs, "stats": stats})

    app.router.add_post("/mapped/query", search)
    return app


def serve(role, folder, shared, port, store_url):
    app = (
        mapped_store(folder, shared)
        if role == "hpis"
        else mapped_hpic(folder, shared, store_url)
    )
    web.run_app(
        app,
        host="127.0.0.1",
        port=port,
        ssl_context=tls_server(folder, role == "hpis"),
        access_log=None,
    )
