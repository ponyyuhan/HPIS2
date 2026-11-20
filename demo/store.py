"""HPIS storage used by the demo: one identical exact vector path for plaintext and TEE.

The stock ``hpis.store.Store`` gives the plaintext tenant a shortcut when the SSE
filter leaves at most K records (it reads them with a join and skips the cached
matrix), while the trusted-process (TEE) tenant always takes the general path.
That makes the plaintext baseline artificially fast on small candidate sets.
Here both modes run the same steps:

    SSE match -> cached vector matrix (plaintext, or decrypted inside the
    trusted region) -> exact cosine Top-K with id tie-break -> read K bodies

The only remaining differences are the ones the comparison is meant to measure:
HMAC labels instead of plaintext labels, the vector key check, and AES-GCM
bodies.  Both modes also get the same SQLite page cache and memory mapping.
"""

from __future__ import annotations

from time import perf_counter_ns

import numpy as np

from hpis.codec import F32
from hpis.exact_order import exact_nearest
from hpis.store import Store, cosine_distances

CACHE_KIB = 256 * 1024
MMAP_BYTES = 2 << 30


class DemoStore(Store):
    """Adds an in-memory inverted index (label -> sorted record ids) per tenant.

    SQLite's INTERSECT over long posting lists depends on how labels sort in the
    B-tree: plaintext labels of the same field sit next to each other (and month
    labels even sort chronologically, like the record ids), while HMAC labels are
    scattered.  That made identical queries 0.5-1.4 ms slower on the encrypted
    tenant for reasons unrelated to cryptography.  Like a production SSE server,
    HPIS keeps the posting lists in memory and intersects sorted arrays, which
    costs the same for any label bytes.  Writes and deletes drop the cache.
    """

    def __init__(self, path):
        super().__init__(path)
        self.db.execute(f"PRAGMA cache_size=-{CACHE_KIB}")
        self.db.execute(f"PRAGMA mmap_size={MMAP_BYTES}")
        self._postings: dict[bytes, dict[bytes, np.ndarray]] = {}

    def write(self, aid, mode, dim, records):
        self._postings.pop(aid, None)
        return super().write(aid, mode, dim, records)

    def delete(self, aid, ids):
        self._postings.pop(aid, None)
        return super().delete(aid, ids)

    def postings(self, aid) -> dict[bytes, np.ndarray]:
        index = self._postings.get(aid)
        if index is None:
            index, tag, buf = {}, None, []
            for t, rid in self.db.execute("SELECT tag, rid FROM postings WHERE aid=? ORDER BY tag, rid", (aid,)):
                if t != tag:
                    if tag is not None:
                        index[tag] = np.asarray(buf, dtype=np.int64)
                    tag, buf = t, []
                buf.append(rid)
            if tag is not None:
                index[tag] = np.asarray(buf, dtype=np.int64)
            self._postings[aid] = index
        return index

    def matches(self, aid, groups, combine):
        if not groups or len(groups) > 32 or combine not in ("all", "any"):
            raise ValueError("invalid predicate group")
        index = self.postings(aid)
        empty = np.empty(0, dtype=np.int64)
        result = None
        for tags in groups:
            if not tags or len(tags) > 1024:
                raise ValueError("invalid number of labels")
            lists = [index[t] for t in tags if t in index]
            part = lists[0] if len(lists) == 1 else (np.unique(np.concatenate(lists)) if lists else empty)
            if result is None:
                result = part
            elif combine == "all":
                result = np.intersect1d(result, part, assume_unique=True)
            else:
                result = np.union1d(result, part)
        return result.tolist()

    def search(self, aid, mode, dim, beta, op, *, allow_certified_response=False):
        if op.get("kind") != "vector" or mode not in ("plain", "trusted-process") or op.get("refine"):
            return super().search(aid, mode, dim, beta, op, allow_certified_response=allow_certified_response)
        start = perf_counter_ns()
        k = op["k"]
        query = np.frombuffer(op["target"], dtype=F32)
        if type(k) is not int or not 1 <= k <= 10000 or query.size != dim or not np.isfinite(query).all():
            raise ValueError("invalid vector query")
        groups, combine = op.get("groups", []), op.get("combine", "all")
        matched = self.matches(aid, groups, combine) if groups else None
        matrix = self.matrix(aid, dim, mode, op.get("vek"))
        ids, data = matrix.ids, matrix.sketches
        if matched is not None:
            wanted = np.asarray(matched, dtype=np.int64)
            loc = np.searchsorted(ids, wanted)
            ok = loc < len(ids)
            loc = loc[ok]
            pos = loc[ids[loc] == wanted[ok]]
            ids, data = ids[pos], data[pos]
        stats = dict(body_rows=0, body_bytes=0, vector_rows=int(len(ids)), candidate_rows=0, candidate_bytes=0)
        if len(ids):
            dots = data @ query
            if len(dots) > k:
                errors = 8 * dim * np.finfo(np.float32).eps
                kth = np.partition(dots, len(dots) - k)[-k]
                shortlist = np.flatnonzero(dots >= kth - errors)
            else:
                shortlist = np.arange(len(dots))
            cand_ids, cand = ids[shortlist], data[shortlist]
            top = exact_nearest(cand_ids, cosine_distances(cand, query), k, query, cand)
            rows = self.read_items(aid, cand_ids[top].tolist())
        else:
            rows = []
        stats.update(
            body_rows=len(rows),
            body_bytes=sum(len(v) for _, v in rows),
            store_us=(perf_counter_ns() - start) / 1000,
        )
        return {"rows": rows, "stats": stats}
