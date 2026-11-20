"""A role-limited projection of the existing database. Only HPIS publishes; HPIC maps read-only."""

import hashlib
import os
from pathlib import Path

import numpy as np

from hpis.codec import F32, decrypt_item, nearest, pack, unpack
from hpis.store import cosine_distances
from . import native
from hpis.exact_order import exact_nearest, order_margin

HEADER_BYTES = 256
MAGIC = b"HPIM1"
PROFILE = "hpis-mapped-v1"


def locator(aid, owner):
    return hashlib.sha256(b"HPIS vector index\0" + aid + owner).digest()[:24]


def install_revisions(db):
    # The file and database are read by different processes. The version is
    # advanced in the original write transaction so an old ranking cannot fetch new bodies.
    db.executescript("""
    CREATE TABLE IF NOT EXISTS mapped_revision (aid BLOB PRIMARY KEY, revision INTEGER NOT NULL) WITHOUT ROWID;
    CREATE TRIGGER IF NOT EXISTS mapped_docs_insert AFTER INSERT ON docs BEGIN
      INSERT INTO mapped_revision VALUES(NEW.aid,1) ON CONFLICT(aid) DO UPDATE SET revision=revision+1;
    END;
    CREATE TRIGGER IF NOT EXISTS mapped_docs_delete AFTER DELETE ON docs BEGIN
      INSERT INTO mapped_revision VALUES(OLD.aid,1) ON CONFLICT(aid) DO UPDATE SET revision=revision+1;
    END;
    """)


def current_revision(db, aid):
    result = db.execute(
        "SELECT revision FROM mapped_revision WHERE aid=?", (aid,)
    ).fetchone()
    return 0 if result is None else result[0]


def image_path(directory, index_id):
    if not isinstance(index_id, bytes) or len(index_id) != 24:
        raise ValueError("invalid index locator")
    return Path(directory) / (index_id.hex() + ".hpi")


def publish(db, directory, aid, tenant, revision):
    """Caller has an active SQLite read transaction spanning revision and vectors."""
    owner, mode, dim, beta = tenant
    if mode not in ("plain", "encrypted"):
        raise ValueError("this layout supports plaintext control and RAP")
    dst = image_path(directory, locator(aid, owner))
    rows = db.execute(
        "SELECT rid,sketch,original FROM vectors WHERE aid=? ORDER BY rid", (aid,)
    ).fetchall()
    header = pack([MAGIC, revision, owner, mode, dim, beta, len(rows)])
    temp = dst.with_suffix("." + os.urandom(8).hex())
    try:
        with temp.open("xb") as out:
            out.write(len(header).to_bytes(2, "little"))
            out.write(header.ljust(HEADER_BYTES - 2, b"\0"))
            out.write(np.asarray([r[0] for r in rows], dtype="<i8").tobytes())
            out.writelines(r[1] for r in rows)
            if mode == "encrypted":
                out.writelines(r[2] for r in rows)
        os.replace(temp, dst)
    finally:
        temp.unlink(missing_ok=True)
    return dst.stat().st_size


def header_owner(path):
    """Read just the small catalog header before verifying delegation."""
    with Path(path).open("rb") as source:
        header = source.read(HEADER_BYTES)
    nhead = int.from_bytes(header[:2], "little")
    fields = unpack(header[2 : 2 + nhead])
    if len(fields) != 7 or fields[0] != MAGIC:
        raise ValueError("bad vector catalog header")
    return fields[2]


class MappedImage:
    def __init__(self, path):
        self.q8_kernel = None
        q8_path = os.environ.get("HPIS_MAPPED_Q8")
        if q8_path:
            from .q8 import load_kernel
            self.q8_kernel = load_kernel(q8_path)
        self.native_rank = None if self.q8_kernel else native.load_library()
        # A single guest-side allocation keeps BLAS alignment independent of
        # file offsets and Python's bytes allocator in *both* benchmark modes.
        with Path(path).open("rb") as source:
            header = source.read(HEADER_BYTES)
            nhead = int.from_bytes(header[:2], "little")
            magic, self.revision, self.owner, self.mode, self.dim, beta, self.size = (
                unpack(header[2 : 2 + nhead])
            )
            if (
                magic != MAGIC
                or self.mode not in ("encrypted", "plain")
                or not 0 < self.dim <= 4096
            ):
                raise ValueError("invalid vector index")
            n, d = self.size, self.dim
            self.ids = np.frombuffer(source.read(n * 8), "<i8", count=n)
            buf = np.empty(n * d + 16, dtype=F32)
            at = (-buf.ctypes.data % 64) // 4
            self.sketch = buf[at : at + n * d].reshape(n, d)
            if source.readinto(self.sketch) != 4 * n * d:
                raise ValueError("invalid vector index length")
            self.sketch.setflags(write=False)
            sealed = source.read()
        self.sealed = None
        if self.mode == "encrypted":
            if len(sealed) != n * (4 * d + 28):
                raise ValueError("incorrect encrypted row width")
            self.sealed = memoryview(sealed)
            self.sealed_ptr = np.frombuffer(sealed, np.uint8).ctypes.data
            # Covers float32 storage, rounded orthogonal matrix, multiplication
            # at both endpoints, and dot-product accumulation.
            self.fp_slack = 8 * np.finfo(np.float32).eps * (d * np.sqrt(d) + 4)
            self.radius = beta / 4 + self.fp_slack
            lens = np.sqrt(
                np.einsum("ij,ij->i", self.sketch, self.sketch, dtype=np.float64)
            )
            self.inv_len = 1 / np.where(lens > 0, lens, 1.0)
            valid = (lens > 0) & (np.abs(lens - 1) <= self.radius)
            chord = np.sqrt(
                np.maximum(0, self.radius**2 - (lens - 1) ** 2)
                / np.where(valid, lens, 1.0)
            )
            angles = np.where(valid, 2 * np.arcsin(np.clip(chord / 2, 0, 1)), np.pi)
            self.cap_sin, self.cap_cos = np.sin(angles), np.cos(angles)
        elif sealed:
            raise ValueError("incorrect plain row width")
        self.native_data_ptr = self.sketch.ctypes.data
        self.native_ids_ptr = self.ids.ctypes.data
        self.native_controls = (
            (
                self.inv_len.ctypes.data,
                self.cap_sin.ctypes.data,
                self.cap_cos.ctypes.data,
            )
            if self.sealed is not None
            else (None, None, None)
        )

    def resolve_ties(self, unit, rotated, k, key):
        # Rare path only: use a conservative scalar RAP bound to include every
        # potential boundary tie, then compare exact dyadic cosines.
        secure = self.mode == "encrypted"
        scores = self.sketch @ (rotated if secure else unit)
        kth = np.partition(scores, len(scores) - k)[len(scores) - k]
        radius = self.radius if secure else 8 * self.dim * np.finfo(np.float32).eps
        idx = np.flatnonzero(scores >= kth - 2 * radius)
        if secure:
            from hpis.codec import context_key

            cipher = context_key(key)
            width = self.dim * 4 + 28
            raw = b"".join(
                decrypt_item(
                    cipher,
                    b"vector",
                    int(self.ids[j]),
                    self.sealed[int(j) * width : (int(j) + 1) * width],
                )
                for j in idx
            )
            original = np.frombuffer(raw, dtype=F32).reshape(-1, self.dim)
        else:
            original = self.sketch[idx]
        ds = cosine_distances(original, unit)
        selected = exact_nearest(self.ids[idx], ds, k, unit, original)
        return self.ids[idx[selected]].tolist(), len(idx) if secure else 0

    def exact(self, unit, rotated, k, key):
        unit = np.ascontiguousarray(unit, dtype=F32)
        rotated = np.ascontiguousarray(rotated, dtype=F32)
        if (
            unit.ndim != 1
            or rotated.ndim != 1
            or unit.size != self.dim
            or not np.isfinite(unit).all()
            or not 1 <= k <= 10000
        ):
            raise ValueError("invalid vector query")
        n = self.size
        if not n:
            self.diagnostic = {}
            return [], 0
        k = min(k, n)
        if self.q8_kernel:
            if self.mode == "encrypted" and (rotated.size != self.dim or not np.isfinite(rotated).all()):
                raise ValueError("invalid rotated query")
            from .q8 import rank as rank_q8
            return rank_q8(self.q8_kernel,self,unit,rotated,k,key)
        if self.native_rank:
            if self.mode == "encrypted" and (
                rotated.size != self.dim or not np.isfinite(rotated).all()
            ):
                raise ValueError("invalid rotated query")
            return native.rank(self.native_rank, self, unit, rotated, k, key)
        if self.mode == "plain":
            scores = self.sketch @ unit
            t = (
                np.partition(scores, n - k)[n - k]
                - 8 * self.dim * np.finfo(np.float32).eps
            )
            idx = np.flatnonzero(scores >= t)
            dist = cosine_distances(self.sketch[idx], unit)
            return self.ids[
                idx[exact_nearest(self.ids[idx], dist, k, unit, self.sketch[idx])]
            ].tolist(), 0
        if rotated.size != self.dim or not np.isfinite(rotated).all():
            raise ValueError("invalid rotated query")
        from hpis.codec import context_key

        cipher = context_key(key)
        width = self.dim * 4 + 28
        scores = self.sketch @ rotated
        # First k score estimates supply a valid exact kth lower bound after opening.
        seed = np.argpartition(scores, n - k)[-k:]

        def exact_dist(pos):
            values = b"".join(
                decrypt_item(
                    cipher,
                    b"vector",
                    int(self.ids[i]),
                    self.sealed[int(i) * width : (int(i) + 1) * width],
                )
                for i in pos
            )
            return cosine_distances(
                np.frombuffer(values, F32).reshape(-1, self.dim), unit
            )

        seed_dist = exact_dist(seed)
        cutoff = 1 - float(np.max(seed_dist)) / 2
        # The blind sketch xR+e obeys |s-cos(x,q)| <= r apart from the
        # floating-point allowances. Refine the resulting uncertainty set
        # further with each noisy row's spherical-cap bound.
        scores[seed] = -np.inf
        idx = np.flatnonzero(scores >= cutoff - self.radius)
        if len(idx):
            c = np.clip(scores[idx] * self.inv_len[idx] + self.fp_slack, -1, 1)
            bound = (
                c * self.cap_cos[idx]
                + np.sqrt(np.maximum(0.0, 1 - c * c)) * self.cap_sin[idx]
            )
            bound = np.where(c >= self.cap_cos[idx], 1.0, bound)
            idx = idx[bound >= cutoff - self.fp_slack]
        if len(idx):
            all_pos = np.concatenate((seed, idx))
            all_dist = np.concatenate((seed_dist, exact_dist(idx)))
        else:
            all_pos, all_dist = seed, seed_dist
        if len(all_dist) > 1 and np.any(
            np.diff(np.sort(all_dist)[: k + 1]) <= order_margin(self.dim)
        ):
            chosen, extra = self.resolve_ties(unit, rotated, k, key)
            return chosen, len(all_pos) + extra
        return self.ids[all_pos[nearest(self.ids[all_pos], all_dist, k)]].tolist(), len(
            all_pos
        )
