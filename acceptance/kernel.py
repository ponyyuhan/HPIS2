"""The single vector ranking kernel used by every fast arm, installed by subclassing.

Kernel (plaintext, TEE, PPE alike):
    scores = data @ query                 # FP32 GEMV, single-thread BLAS
    top    = hpis.codec.nearest(ids, -scores, k)
             # introselect (np.partition) for the K-th score, keep every row tied at
             # the cutoff, then order by (score, record id) -> stable id tie-break
This is the exhaustive inner-product search a plaintext vector DB performs on unit
vectors (FAISS IndexFlatIP semantics). PPE runs the *unmodified* production branch of
hpis.store.Store.search, which is the same GEMV + nearest() with one extra step that is
part of PPE itself: the norm projection of RAP sketches (scores * 1/||h||).

Plaintext and TEE ("trusted-process") vector queries are served by the branch below.
In production those two modes take an FP64 re-check path (exact_nearest); an op flag
``acceptance_kernel = "production"`` selects that untouched path for the appendix.
"""

from time import perf_counter_ns

import numpy as np

from hpis.codec import F32, nearest
from hpis.store import Store

KERNEL_DESCRIPTION = (
    "FP32 GEMV (data @ q, single-thread BLAS) + np.partition top-K + (score, id) "
    "stable tie-break via hpis.codec.nearest; PPE adds RAP norm projection"
)

# Warm, in-memory storage for every tenant alike: the SQLite file is memory-mapped
# (no read syscalls) with a large page cache. This only removes cost that plaintext
# and ciphertext share, so it makes the ratio stricter, never looser.
SQLITE_PRAGMAS = ("PRAGMA mmap_size=2147418112", "PRAGMA cache_size=-262144")


def fp32_topk(ids, data, query, k, inverse_norms=None):
    """Reference implementation of the kernel (used by tests and the oracle checks)."""
    scores = data @ query
    if inverse_norms is not None:
        scores = scores * inverse_norms
    return nearest(ids, -scores, k)


def hpic_rerank(records, where):
    """HPIC-side exact re-rank of PPE candidates (scenario vec_ppe_rerank).

    records are the bodies HPIC has just decrypted with the DEK; each body carries the
    normalized FP32 vector the SDK stored at ingest. The same kernel as the plaintext arm
    (FP32 dot + nearest(): score, then record id) picks the final Top-k.
    """
    with_vec = [(rid, body) for rid, body in records if body.get("vector") is not None]
    if not with_vec:
        return []
    q = np.frombuffer(where["q"], dtype=F32)
    ids = np.fromiter((rid for rid, _ in with_vec), dtype=np.int64, count=len(with_vec))
    data = np.frombuffer(b"".join(body["vector"] for _, body in with_vec), dtype=F32).reshape(-1, len(q))
    return [with_vec[j] for j in fp32_topk(ids, data, q, where["k"])]


def install_hpic_rerank():
    """Register filter kind 'rerank' in the HPIC process. make_hpic_app() resolves
    hpis.services.scan_filter at call time and applies it to decrypted bodies when the
    holder grant carries grant["scan"]; every other kind keeps the production filter."""
    from hpis import services

    production = services.scan_filter

    def scan_filter(rows, where):
        if where.get("kind") == "rerank":
            return hpic_rerank(rows, where)
        return production(rows, where)

    services.scan_filter = scan_filter


class AcceptanceStore(Store):
    """Production Store, except plaintext/TEE fast vector search uses the shared kernel."""

    def __init__(self, path):
        super().__init__(path)
        for pragma in SQLITE_PRAGMAS:
            self.db.execute(pragma)

    def search(self, aid, mode, dim, beta, op, *, allow_certified_response=False):
        self._sql_ns = 0
        start = perf_counter_ns()
        unified = (
            op.get("kind") == "vector"
            and not op.get("refine")
            and not op.get("groups")
            and mode in ("plain", "trusted-process")
            and op.get("acceptance_kernel", "unified") == "unified"
        )
        if unified:
            result = self._unified_vector(aid, mode, dim, op, start)
        else:
            result = super().search(
                aid, mode, dim, beta, op, allow_certified_response=allow_certified_response
            )
        stats = result.get("stats")
        if stats is not None:
            stats["sql_us"] = self._sql_ns / 1000
        return result

    def read_items(self, aid, ids):
        # Times the body read (SQLite) for both branches; identical work otherwise.
        t0 = perf_counter_ns()
        rows = super().read_items(aid, ids)
        self._sql_ns = getattr(self, "_sql_ns", 0) + perf_counter_ns() - t0
        return rows

    def _unified_vector(self, aid, mode, dim, op, start):
        # Mirrors the production fast branch line by line, minus the norm projection.
        k = op["k"]
        query = np.frombuffer(op["target"], dtype=F32)
        if (
            type(k) is not int
            or not 1 <= k <= 10000
            or query.size != dim
            or not np.isfinite(query).all()
        ):
            raise ValueError("invalid vector query")
        # trusted-process: AES-GCM index decrypted once inside the trusted process,
        # key re-checked on every query (production Store.matrix).
        matrix = self.matrix(aid, dim, mode, op.get("vek"))
        ids, data = matrix.ids, matrix.sketches
        stats = dict(
            body_rows=0,
            body_bytes=0,
            vector_rows=int(len(ids)),
            candidate_rows=0,
            candidate_bytes=0,
        )
        dots = data @ query
        top = nearest(ids, -dots, k)
        rows = self.read_items(aid, ids[top].tolist())
        stats.update(
            body_rows=len(rows),
            body_bytes=sum(len(v) for _, v in rows),
            store_us=(perf_counter_ns() - start) / 1000,
        )
        return {"rows": rows, "stats": stats}
