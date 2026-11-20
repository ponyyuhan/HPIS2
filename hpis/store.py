import hmac
import sqlite3
from dataclasses import dataclass
from time import perf_counter_ns

import numpy as np

from .codec import F32, context_key, decrypt_item, nearest, record_id
from .exact_order import exact_nearest

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS tenants (
 aid BLOB PRIMARY KEY, owner BLOB NOT NULL, mode TEXT NOT NULL,
 dim INTEGER NOT NULL, beta REAL NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS docs (
 aid BLOB NOT NULL REFERENCES tenants(aid), rid INTEGER NOT NULL, body BLOB NOT NULL,
 PRIMARY KEY (aid, rid)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS vectors (
 aid BLOB NOT NULL, rid INTEGER NOT NULL, sketch BLOB NOT NULL, original BLOB NOT NULL,
 PRIMARY KEY (aid, rid), FOREIGN KEY (aid,rid) REFERENCES docs(aid,rid) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS postings (
 aid BLOB NOT NULL, tag BLOB NOT NULL, rid INTEGER NOT NULL, PRIMARY KEY (aid,tag,rid),
 FOREIGN KEY (aid,rid) REFERENCES docs(aid,rid) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS postings_by_record ON postings(aid,rid);
"""


def cosine_distances(vectors, query):
    """Squared chord distances on the unit sphere, calculated on the stored inputs."""
    rows = np.asarray(vectors, dtype=np.float64)
    q = np.asarray(query, dtype=np.float64)
    lengths = np.linalg.norm(rows, axis=1, keepdims=True)
    query_length = np.linalg.norm(q)
    if (
        not np.isfinite(query_length)
        or query_length == 0
        or not np.all(np.isfinite(lengths) & (lengths > 0))
    ):
        raise ValueError("cosine distance requires finite nonzero vectors")
    diff = rows / lengths - q / query_length
    return np.einsum("ij,ij->i", diff, diff)


def cap_angles(lengths, radius):
    # ||s u - x||² = (s-1)² + 4s sin²(angle(u,x)/2), where ||x||=||u||=1.
    s = np.asarray(lengths)
    valid = (s > 0) & (np.abs(s - 1) <= radius)
    chord = np.sqrt(np.maximum(0.0, radius**2 - (s - 1) ** 2) / np.where(valid, s, 1.0))
    return np.where(valid, 2 * np.arcsin(np.minimum(1.0, chord / 2)), np.pi)


class RefinementIndex:
    """Encrypted originals and public bounds derived solely from the stored RAP sketches."""

    def __init__(self, sketches, ciphertexts, beta):
        dim = sketches.shape[1]
        self.dim = dim
        self.ciphertexts = ciphertexts
        # Float32 output cast, rounded orthogonal matrix and dot accumulation.
        # A componentwise dot error <= gamma_dim |x|^T |M| gives an l2 error
        # <= gamma_dim * ||M||_F for unit inputs, with ||M||_F ~= sqrt(dim).
        eps = np.finfo(np.float32).eps
        self.radius = beta / 4 + 2 * eps * (2 + dim * np.sqrt(dim))
        lengths = np.sqrt(np.einsum("ij,ij->i", sketches, sketches, dtype=np.float64))
        self.angles = cap_angles(lengths, self.radius)
        self.inv_length = 1 / np.where(lengths > 0, lengths, 1.0)
        self.dot_error = (2 * dim + 8) * eps

    def candidates(self, ids, sketches, positions, query, k):
        # Positions map a filtered tenant-local search back to its ciphertext rows.
        inverse = self.inv_length[positions]
        doc_angle = self.angles[positions]
        query_length = np.linalg.norm(query.astype(np.float64))
        query_angle = cap_angles(query_length, self.radius)
        cosine = (
            (sketches @ query).astype(np.float64) * inverse / max(query_length, 1e-60)
        )
        low_angle = np.arccos(np.clip(cosine + self.dot_error, -1, 1))
        high_angle = np.arccos(np.clip(cosine - self.dot_error, -1, 1))
        lower = np.maximum(0.0, low_angle - doc_angle - query_angle)
        upper = np.minimum(np.pi, high_angle + doc_angle + query_angle)
        count = min(k, len(ids))
        cutoff = np.partition(upper, count - 1)[count - 1]
        idx = np.flatnonzero(lower <= cutoff)

        # Use the same uncertain query for a pairwise score comparison. A row can
        # be eliminated as soon as k other rows are certainly better. For large k
        # the cap-only certificate avoids the quadratic witness work.
        if k <= 32 and len(idx) > 2 * k and query_angle < np.pi / 2:
            nw = min(len(ids), 2 * k)
            witnesses = np.argpartition(upper, nw - 1)[:nw]
            cos_pair = (sketches[idx] @ sketches[witnesses].T).astype(np.float64)
            cos_pair *= inverse[idx, None] * inverse[witnesses]
            # For nominal unit directions g,u_i,u_j, Cauchy-Schwarz gives
            # q·(u_j-u_i) >= g·(u_j-u_i) - ||q-g|| ||u_j-u_i||.
            # Rotating a document by a changes its score by at most
            # 2 sin(a/2) times the largest possible sine of the midpoint angle.
            spread = query_angle + doc_angle / 2
            mid = np.clip(
                np.pi / 2,
                np.maximum(0.0, low_angle - spread),
                np.minimum(np.pi, high_angle + spread),
            )
            doc_error = 2 * np.sin(doc_angle / 2) * np.sin(mid)
            gap = (cosine[witnesses] - doc_error[witnesses]) - (
                cosine[idx] + doc_error[idx] + 2 * self.dot_error + 1e-12
            )[:, None]
            norm_sq = np.maximum(0.0, 2 * (1 - cos_pair + self.dot_error))
            dominates = (
                np.maximum(gap, 0.0) ** 2 > (2 * np.sin(query_angle / 2)) ** 2 * norm_sq
            )
            idx = idx[np.count_nonzero(dominates, axis=1) < k]
        # Lower bounds are transmitted to the trusted recipient for local early
        # stopping. The storage host sends all candidates independent of exact scores.
        idx = idx[np.argsort(lower[idx], kind="stable")]
        bounds = np.asarray((2 * np.sin(lower[idx] / 2)) ** 2, dtype=F32)
        bounds = np.nextafter(bounds, np.float32(-np.inf))
        absolute = idx if isinstance(positions, slice) else positions[idx]
        return {
            "ids": ids[idx].astype("<i8", copy=False).tobytes(),
            "lower": bounds.tobytes(),
            "sealed": memoryview(self.ciphertexts[absolute]).cast("B"),
        }


@dataclass
class Matrix:
    ids: np.ndarray
    sketches: np.ndarray
    inverse_norms: np.ndarray | None
    key: bytes | None
    refinement: RefinementIndex | None = None


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.matrices = {}

    def close(self):
        self.db.close()

    def tenant(self, aid):
        row = self.db.execute(
            "SELECT owner,mode,dim,beta FROM tenants WHERE aid=?", (aid,)
        ).fetchone()
        if row is None:
            raise PermissionError("unknown tenant")
        return row

    def write(self, aid, mode, dim, records):
        with self.db:
            for rid, body, tags, sketch, original in records:
                record_id(rid)
                if sketch is not None:
                    v = np.frombuffer(sketch, dtype=F32)
                    expected = 0 if mode in ("snp", "trusted-process") else dim
                    if (
                        len(v) != expected
                        or not np.isfinite(v).all()
                        or not isinstance(original, bytes)
                        or len(original) != 4 * dim + (0 if mode == "plain" else 28)
                    ):
                        raise ValueError("invalid vector record")
                self.db.execute("DELETE FROM docs WHERE aid=? AND rid=?", (aid, rid))
                self.db.execute("INSERT INTO docs VALUES(?,?,?)", (aid, rid, body))
                self.db.executemany(
                    "INSERT INTO postings VALUES(?,?,?)",
                    ((aid, t, rid) for t in set(tags)),
                )
                if sketch is not None:
                    self.db.execute(
                        "INSERT INTO vectors VALUES(?,?,?,?)",
                        (aid, rid, sketch, original),
                    )
        self.matrices.pop(aid, None)

    def delete(self, aid, ids):
        with self.db:
            self.db.executemany(
                "DELETE FROM docs WHERE aid=? AND rid=?",
                ((aid, record_id(i)) for i in ids),
            )
        self.matrices.pop(aid, None)

    def matrix(self, aid, dim, mode, key):
        protected = mode in ("snp", "trusted-process")
        if protected and (not isinstance(key, bytes) or len(key) != 32):
            raise PermissionError("vector key required")
        if aid not in self.matrices:
            column = "original" if protected else "sketch"
            rows = self.db.execute(
                f"SELECT rid,{column} FROM vectors WHERE aid=? ORDER BY rid", (aid,)
            ).fetchall()
            ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
            if protected:
                cipher = context_key(key)
                values = (
                    decrypt_item(cipher, b"vector", rid, value) for rid, value in rows
                )
            else:
                values = (value for _, value in rows)
            data = np.frombuffer(b"".join(values), dtype=F32).reshape(-1, dim)
            inverse_norms = None
            if mode == "encrypted":
                lengths = np.sqrt(np.einsum("ij,ij->i", data, data))
                inverse_norms = 1 / np.where(lengths > 0, lengths, 1.0)
            self.matrices[aid] = Matrix(
                ids, data, inverse_norms, key if protected else None
            )
        matrix = self.matrices[aid]
        if protected and not hmac.compare_digest(key, matrix.key):
            raise PermissionError("vector key mismatch")
        return matrix

    def matches(self, aid, groups, combine):
        if not groups or len(groups) > 32 or combine not in ("all", "any"):
            raise ValueError("invalid predicate group")
        parts, args = [], []
        for tags in groups:
            if not tags or len(tags) > 1024:
                raise ValueError("invalid number of labels")
            marks = ",".join("?" for _ in tags)
            parts.append(f"SELECT rid FROM postings WHERE aid=? AND tag IN ({marks})")
            args.extend([aid, *tags])
        joiner = " INTERSECT " if combine == "all" else " UNION "
        sql = "SELECT DISTINCT rid FROM (" + joiner.join(parts) + ") ORDER BY rid"
        return [r[0] for r in self.db.execute(sql, args)]

    def read_items(self, aid, ids):
        by_id = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            sql = f"SELECT rid,body FROM docs WHERE aid=? AND rid IN ({marks})"
            by_id.update(self.db.execute(sql, [aid, *chunk]))
        return [(rid, by_id[rid]) for rid in ids]

    def selected_vectors(self, aid, ids, *, compact):
        """Read the bodies and authenticated vector records for known members in one query."""
        rows = []
        index_columns = (
            "substr(v.original,1,12),substr(v.original,-16)"
            if compact
            else "v.original"
        )
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            rows.extend(
                self.db.execute(
                    f"SELECT d.rid,d.body,{index_columns} FROM docs AS d JOIN vectors AS v "
                    f"ON v.aid=d.aid AND v.rid=d.rid WHERE d.aid=? AND d.rid IN ({marks})",
                    [aid, *chunk],
                )
            )
        return rows

    def original_records(self, aid, ids):
        rows = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            rows.update(
                self.db.execute(
                    f"SELECT rid,original FROM vectors WHERE aid=? AND rid IN ({marks})",
                    [aid, *chunk],
                )
            )
        return rows

    def known_top(self, aid, members, mode, dim, query, stats, start):
        # A hit with no embedding is excluded by the vector join. All remaining
        # members fit within k, or have already been certified by the public RAP bounds.
        selected = self.selected_vectors(aid, members, compact=mode == "encrypted")
        rows = [(row[0], row[1]) for row in selected]
        if not stats["vector_rows"]:
            stats["vector_rows"] = len(rows)
        result = {"rows": rows, "stats": stats}
        if selected and mode == "plain":
            ids = np.asarray([rid for rid, _ in rows], dtype=np.int64)
            originals = b"".join(row[2] for row in selected)
            vectors = np.frombuffer(originals, dtype=F32).reshape(-1, dim)
            distances = cosine_distances(vectors, query)
            order = exact_nearest(ids, distances, len(ids), query, vectors)
            result["rows"] = [rows[j] for j in order]
        elif selected:
            # The delivered body already holds the vector. Its original GCM nonce
            # and tag allow HPIC to authenticate that copy under the vector key.
            proofs = b"".join(value for row in selected for value in row[2:])
            result["selected_proofs"] = proofs
            stats.update(
                candidate_rows=len(rows),
                candidate_bytes=len(proofs),
                candidate_wire_data_bytes=len(proofs),
                certified_one_round=True,
            )
        stats.update(
            body_rows=len(rows),
            body_bytes=sum(len(value) for _, value in rows),
            store_us=(perf_counter_ns() - start) / 1000,
        )
        return result

    def search(self, aid, mode, dim, beta, op, *, allow_certified_response=False):
        start = perf_counter_ns()
        kind = op["kind"]
        groups, combine = op.get("groups", []), op.get("combine", "all")
        stats = dict(
            body_rows=0,
            body_bytes=0,
            vector_rows=0,
            candidate_rows=0,
            candidate_bytes=0,
        )
        if kind == "scan":
            rows = self.db.execute(
                "SELECT rid,body FROM docs WHERE aid=? ORDER BY rid", (aid,)
            ).fetchall()
        elif kind == "terms":
            rows = self.read_items(aid, self.matches(aid, groups, combine))
        elif kind == "vector":
            k = op["k"]
            query = np.frombuffer(op["target"], dtype=F32)
            if (
                type(k) is not int
                or not 1 <= k <= 10000
                or query.size != dim
                or not np.isfinite(query).all()
            ):
                raise ValueError("invalid vector query")
            matched = self.matches(aid, groups, combine) if groups else None
            if (
                matched is not None
                and len(matched) <= k
                and (
                    mode == "plain"
                    or (
                        mode == "encrypted"
                        and op.get("refine")
                        and allow_certified_response
                    )
                )
            ):
                return self.known_top(aid, matched, mode, dim, query, stats, start)
            matrix = self.matrix(aid, dim, mode, op.get("vek"))
            ids, data, inverse_norms = matrix.ids, matrix.sketches, matrix.inverse_norms
            pos = slice(None)
            if matched is not None:
                matched_ids = np.asarray(matched, dtype=np.int64)
                loc = np.searchsorted(ids, matched_ids)
                valid = loc < len(ids)
                loc = loc[valid]
                pos = loc[ids[loc] == matched_ids[valid]]
                ids, data = ids[pos], data[pos]
                inverse_norms = (
                    inverse_norms[pos] if inverse_norms is not None else None
                )
            stats["vector_rows"] = int(len(ids))
            if op.get("refine") and mode == "encrypted" and len(ids):
                if allow_certified_response and len(ids) <= k:
                    return self.known_top(
                        aid, ids.tolist(), mode, dim, query, stats, start
                    )
                if matrix.refinement is None:
                    records = self.db.execute(
                        "SELECT original FROM vectors WHERE aid=? ORDER BY rid", (aid,)
                    )
                    blob = b"".join(row[0] for row in records)
                    sealed = np.frombuffer(blob, np.uint8).reshape(
                        len(matrix.ids), 4 * dim + 28
                    )
                    matrix.refinement = RefinementIndex(matrix.sketches, sealed, beta)
                    stats["refinement_index_load_bytes"] = len(blob)
                bundle = matrix.refinement.candidates(ids, data, pos, query, k)
                members = np.frombuffer(bundle["ids"], dtype="<i8")
                if allow_certified_response and len(members) == k:
                    return self.known_top(
                        aid, members.tolist(), mode, dim, query, stats, start
                    )
                stats.update(
                    candidate_rows=len(bundle["ids"]) // 8,
                    candidate_bytes=len(bundle["sealed"]),
                    candidate_wire_data_bytes=sum(map(len, bundle.values())),
                )
                stats["store_us"] = (perf_counter_ns() - start) / 1000
                return {"candidates": bundle, "stats": stats}
            dots = data @ query
            if inverse_norms is None:
                # Unit vectors: the dot product finds the shortlist. Resolve the
                # numerical boundary and ties in float64 on original float32 inputs.
                errors = 8 * dim * np.finfo(np.float32).eps
                if len(dots) > k:
                    kth = np.partition(dots, len(dots) - k)[-k]
                    shortlist = np.flatnonzero(dots >= kth - errors)
                    original_ids, original = ids[shortlist], data[shortlist]
                else:
                    original_ids, original = ids, data
                distances = cosine_distances(original, query)
                top = exact_nearest(original_ids, distances, k, query, original)
            else:
                # Projection back to the unit sphere removes radial perturbation.
                # The query norm is common to all candidates and cancels in rank.
                distances = -dots * inverse_norms
                original_ids = ids
                top = nearest(original_ids, distances, k)
            rows = self.read_items(aid, original_ids[top].tolist())
        else:
            raise ValueError("unknown search operation")
        stats.update(
            body_rows=len(rows),
            body_bytes=sum(len(v) for _, v in rows),
            store_us=(perf_counter_ns() - start) / 1000,
        )
        return {"rows": rows, "stats": stats}
