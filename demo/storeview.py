"""What an operator of HPIS can read: the store file of the trusted deployment, read-only.

HPIS holds many anonymous tenants (the four devices of the story under four
unlinkable AIDs, plus other people's data).  Every row is an AID, a record
number, an AES-256-GCM body, HMAC-SHA256 tokens and an AES-GCM vector; nothing
says whose it is.  ``mine`` (which AIDs belong to the presenter's devices) is
only used for an optional reveal on the page.
"""

from __future__ import annotations

import hashlib
import json
import mmap
import random
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np


class StoreView:
    def __init__(self, path: Path, mine: dict[bytes, str] | None = None, cache: Path | None = None):
        self.path = Path(path)
        self.mine = dict(mine or {})  # aid -> device id
        aids, stats, self.postings, self.rids = self._load(cache)
        # A stable but meaningless order: tenants are numbered by a hash of their AID.
        aids = [a for a in aids if stats.get(a, (0, 0))[0] > 0]
        aids.sort(key=lambda a: hashlib.sha256(b"tenant-order" + a).digest())
        self.order = aids
        self.label = {aid: f"租户 {i + 1:02d}" for i, aid in enumerate(aids)}
        self.stats = stats
        self.docs = sum(n for n, _ in stats.values())
        self.bytes = sum(b for _, b in stats.values())

    def _load(self, cache: Path | None):
        """Tenants, their record counts and bytes, posting count and record ids.

        Counting reads the whole store (seconds for 1.3 GB), so the result is kept next to
        it, keyed by the store file's size and time, and reused on the next start."""
        st = self.path.stat()
        stamp = f"{st.st_size}-{int(st.st_mtime)}"
        if cache is not None and cache.exists():
            try:
                data = np.load(cache, allow_pickle=False)
                if str(data["stamp"]) == stamp:
                    aids = [bytes.fromhex(a) for a in data["aids"].tolist()]
                    counts, sizes = data["counts"].tolist(), data["sizes"].tolist()
                    stats = {a: (int(n), int(s)) for a, n, s in zip(aids, counts, sizes)}
                    rids = {a: data[f"rids_{a.hex()}"] for a in aids}
                    return aids, stats, int(data["postings"]), rids
            except (OSError, ValueError, KeyError):
                pass
        with closing(self.connect()) as db:
            aids = [row[0] for row in db.execute("SELECT aid FROM tenants")]
            stats = {
                aid: (int(n), int(size or 0))
                for aid, n, size in db.execute("SELECT aid, count(*), sum(length(body)) FROM docs GROUP BY aid")
            }
            postings = int(db.execute("SELECT count(*) FROM postings").fetchone()[0])
            rids = {
                aid: np.fromiter((r[0] for r in db.execute("SELECT rid FROM docs WHERE aid=? ORDER BY rid", (aid,))),
                                 dtype=np.int64)
                for aid in aids
            }
        if cache is not None:
            try:
                tmp = cache.with_suffix(".tmp.npz")
                np.savez(
                    tmp,
                    stamp=np.asarray(stamp),
                    aids=np.asarray([a.hex() for a in aids]),
                    counts=np.asarray([stats.get(a, (0, 0))[0] for a in aids], dtype=np.int64),
                    sizes=np.asarray([stats.get(a, (0, 0))[1] for a in aids], dtype=np.int64),
                    postings=np.asarray(postings),
                    **{f"rids_{a.hex()}": rids[a] for a in aids},
                )
                tmp.replace(cache)
            except OSError:
                pass
        return aids, stats, postings, rids

    def connect(self):
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def tenants(self) -> list[dict]:
        out = []
        for aid in self.order:
            n, size = self.stats.get(aid, (0, 0))
            row = {
                "aid": aid.hex(),
                "aid_short": aid.hex()[:8],
                "tenant": self.label[aid],
                "records": n,
                "bytes": size,
                "mine": aid in self.mine,
            }
            if aid in self.mine:
                row["device"] = self.mine[aid]
            out.append(row)
        return out

    def _row(self, db, aid: bytes, rid: int, *, body_bytes: int = 48, tags: int = 4, vector_bytes: int = 24) -> dict:
        head, size = db.execute(
            "SELECT substr(body,1,?), length(body) FROM docs WHERE aid=? AND rid=?", (body_bytes, aid, rid)
        ).fetchone()
        labels = [t[0].hex() for t in db.execute("SELECT tag FROM postings WHERE aid=? AND rid=? LIMIT ?", (aid, rid, tags))]
        vec = db.execute(
            "SELECT substr(original,1,?), length(original) FROM vectors WHERE aid=? AND rid=?", (vector_bytes, aid, rid)
        ).fetchone()
        return {
            "aid": aid.hex(),
            "aid_short": aid.hex()[:8],
            "tenant": self.label.get(aid, "租户"),
            "rid": int(rid),
            "body": head.hex(),
            "bodyBytes": int(size),
            "tags": labels,
            "vector": vec[0].hex() if vec else "",
            "vectorBytes": int(vec[1]) if vec else 0,
        }

    def sample(self, limit: int, seed: int | None = None) -> list[dict]:
        """Rows interleaved from many AIDs (a fresh draw unless ``seed`` is given)."""
        rng = random.Random(seed)
        tenants = list(self.order)
        rng.shuffle(tenants)
        picks: list[tuple[bytes, int]] = []
        per = max(1, -(-limit // max(1, len(tenants))))
        pools = {}
        for aid in tenants:
            ids = self.rids[aid]
            pools[aid] = [int(ids[i]) for i in rng.sample(range(len(ids)), min(per, len(ids)))]
        while len(picks) < limit and any(pools.values()):
            for aid in tenants:
                if pools[aid] and len(picks) < limit:
                    picks.append((aid, pools[aid].pop()))
        with closing(self.connect()) as db:
            return [self._row(db, aid, rid) for aid, rid in picks]

    def cipher(self, aid: bytes, rid: int) -> dict | None:
        with closing(self.connect()) as db:
            if db.execute("SELECT 1 FROM docs WHERE aid=? AND rid=?", (aid, rid)).fetchone() is None:
                return None
            row = self._row(db, aid, rid, body_bytes=192, tags=1000, vector_bytes=96)
        return row

    def _file_contains(self, patterns: list[bytes]) -> bool:
        """One native-speed pass over the store file (and its WAL, if any) for any of the byte patterns."""
        for path in (self.path, Path(f"{self.path}-wal")):
            if not path.exists() or path.stat().st_size == 0:
                continue
            with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                if any(mm.find(p) != -1 for p in patterns):
                    return True
        return False

    def scan(self, q: str) -> dict:
        """Grep the whole store for the plaintext word, as an operator would.

        A single pass over the store file at native speed settles the usual case (the word occurs nowhere,
        so no record can contain it); only if it occurs somewhere are the bodies counted one by one.
        Words shorter than 4 bytes are not byte-scanned: in 1.5 GB of ciphertext such short byte strings
        occur by pure chance, and a chance byte match is not a plaintext hit."""
        raw = q.encode()
        escaped = json.dumps(q)[1:-1].encode()
        if len(raw) < 4:
            return {"hits": 0, "scanned": self.docs, "bytes": self.bytes, "short": True}
        try:
            if not self._file_contains([raw] if escaped == raw else [raw, escaped]):
                return {"hits": 0, "scanned": self.docs, "bytes": self.bytes}
        except (OSError, ValueError):
            pass  # cannot map the file: count row by row below
        hits = scanned = size = 0
        with closing(self.connect()) as db:
            for (body,) in db.execute("SELECT body FROM docs"):
                scanned += 1
                size += len(body)
                if raw in body or escaped in body:
                    hits += 1
        return {"hits": hits, "scanned": scanned, "bytes": size}

    def probe_rows(self, q: str, limit: int, seed: int | None = None) -> list[dict]:
        """A fresh sample of stored rows, each checked for the word (it never matches ciphertext)."""
        raw = q.encode()
        escaped = json.dumps(q)[1:-1].encode()
        rows = self.sample(limit, seed)
        if len(raw) < 4:  # see scan(): a chance byte match in ciphertext is not a plaintext hit
            for row in rows:
                row["matched"] = False
            return rows
        with closing(self.connect()) as db:
            for row in rows:
                body = db.execute(
                    "SELECT body FROM docs WHERE aid=? AND rid=?", (bytes.fromhex(row["aid"]), row["rid"])
                ).fetchone()[0]
                row["matched"] = bool(raw in body or escaped in body)
        return rows
