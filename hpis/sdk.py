"""Terminal-side ingestion, query construction and HPIC client."""

import os
import time
from pathlib import Path

import aiohttp
import numpy as np
from nacl.public import PublicKey, SealedBox
from nacl.signing import SigningKey

from . import attestation
from .codec import (
    F32,
    Labels,
    VectorCodec,
    context_key,
    encrypt_item,
    normalized_text,
    pack,
    record_id,
    unit_vectors,
)
from .network import post, tls_client
from .provision import read_config


class Device:
    def __init__(self, folder, hpic_url):
        self.folder, self.url = Path(folder), hpic_url
        if not hpic_url.startswith("https://"):
            raise ValueError("HPIC must use HTTPS")
        cfg = read_config(folder)
        self.aid, self.dim, self.mode = cfg["aid"], cfg["dim"], cfg["mode"]
        self.ticket, self.holder = cfg["ticket"], SigningKey(cfg["holder"])
        self.owner = SigningKey(cfg["owner"])
        self.snp = cfg.get("snp")
        self.box = SealedBox(PublicKey(cfg["box"])) if self.mode != "snp" else None
        self.labels = Labels(cfg.get("sse"))
        matrix = (
            np.frombuffer(cfg["rotation"], dtype=F32).reshape(self.dim, self.dim)
            if "rotation" in cfg
            else None
        )
        self.vectors = VectorCodec(self.dim, cfg["beta"], matrix)
        self.dek, self.vek = cfg.get("dek"), cfg.get("vek")
        self.body_cipher, self.vector_cipher = (
            context_key(self.dek),
            context_key(self.vek),
        )
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls_client(self.folder))
        )
        if self.mode == "snp":
            try:
                challenge = os.urandom(32)
                result = await post(
                    self.session, self.url, "/attest", challenge, "hpic"
                )
                self.box = await attestation.verify(result, challenge, self.snp)
            except BaseException:
                await self.session.close()
                self.session = None
                raise
        return self

    async def __aexit__(self, *args):
        await self.session.close()
        self.session = None
        if self.mode == "snp":
            self.box = None

    def envelope(self, op, grant):
        nonce, exp = os.urandom(16), int(time.time()) + 45
        proof = self.owner.sign(pack(["hpis-v1", self.aid, nonce, exp, op]))
        sealed = self.box.encrypt(pack([self.aid, bytes(proof)]))
        outer = self.holder.sign(pack([nonce, exp, sealed, grant]))
        return [self.ticket, bytes(outer)]

    async def request(self, op, grant):
        for attempt in range(2):
            try:
                return await post(
                    self.session, self.url, "/request", self.envelope(op, grant), "hpic"
                )
            except aiohttp.ClientResponseError as exc:
                if exc.status != 409 or attempt:
                    raise

    async def put(self, records):
        records = list(records)
        vectors = [r["vector"] for r in records if r.get("vector") is not None]
        pairs = iter(())
        if vectors:
            originals = unit_vectors(vectors, self.dim)
            sketches = self.vectors.sketch(originals)
            pairs = iter(zip(originals, sketches))
        rows = []
        for record in records:
            rid = record_id(record["id"])
            fields = {
                normalized_text(f): v for f, v in record.get("fields", {}).items()
            }
            words = record.get("keywords", [])
            tags = self.labels.index(fields, words)
            raw, sketch, exact = None, None, None
            if record.get("vector") is not None:
                original, target = next(pairs)
                raw = original.tobytes()
                sketch = (
                    b"" if self.mode in ("snp", "trusted-process") else target.tobytes()
                )
                exact = (
                    encrypt_item(self.vector_cipher, b"vector", rid, raw)
                    if self.vector_cipher
                    else raw
                )
            body = pack(
                {
                    "fields": fields,
                    "keywords": words,
                    "vector": raw,
                    "content": record["content"],
                }
            )
            if self.body_cipher:
                body = encrypt_item(self.body_cipher, b"body", rid, body)
            rows.append([rid, body, tags, sketch, exact])
        return await self.request({"kind": "put", "records": rows}, {})

    async def delete(self, ids):
        return await self.request(
            {"kind": "delete", "ids": [record_id(i) for i in ids]}, {}
        )

    def groups(self, predicates):
        return [self.labels.predicate(*args) for args in predicates]

    async def terms(self, predicates, combine="all"):
        op = {"kind": "terms", "groups": self.groups(predicates), "combine": combine}
        return await self.request(op, {"dek": self.dek})

    async def vector(self, vector, k=10, *, refine=False, predicates=(), combine="all"):
        unit = unit_vectors(vector, self.dim)
        op = {
            "kind": "vector",
            "target": self.vectors.sketch(unit).tobytes(),
            "k": k,
            "refine": refine,
            "groups": self.groups(predicates),
            "combine": combine,
        }
        if self.mode in ("snp", "trusted-process"):
            # Included only inside the HPIS public-key envelope; the payload DEK stays at HPIC.
            op["vek"] = self.vek
        grant = {"dek": self.dek}
        if refine and self.mode == "encrypted":
            grant.update(vek=self.vek, q=unit.tobytes(), k=k)
        return await self.request(op, grant)

    async def full_scan(self, *, field=None, value=None, vector=None, k=10):
        if vector is not None:
            where = {
                "kind": "vector",
                "q": unit_vectors(vector, self.dim).tobytes(),
                "k": k,
            }
        else:
            where = {"kind": "field", "field": normalized_text(field), "value": value}
        return await self.request({"kind": "scan"}, {"dek": self.dek, "scan": where})
