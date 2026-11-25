"""The plain-cloud upload of one kind, packed batch by batch from the device's own files.

With every question the device uploads its whole collection of the asked kind to
the ordinary cloud in batches of at most ``config.UPLOAD_BATCH_BYTES`` (a msgpack
list of record JSON lines; a photo is ``[JSON, JPEG]``).  The build only records
where each batch starts and ends in the data file and how many bytes it packs to;
the batch itself is packed when it is sent, so the runtime folder holds no second
plaintext copy of the data (the bytes on the wire are the same).
"""

from __future__ import annotations

import json
from pathlib import Path

import msgpack

from . import config

FORMAT = 3  # 3: packed on the fly from the data files (2: mail batches stored as files)


def _pack(items: list) -> bytes:
    return msgpack.packb(items, use_bin_type=True)


def plan_mail(path: Path, progress=lambda stage, n: None) -> dict:
    """Batch boundaries of mailbox.jsonl (the rule of the old stored batches, so the same bytes)."""
    batches, items, size, start, pos, total, text_bytes = [], [], 0, 0, 0, 0, 0
    with open(path, "rb") as fh:
        for i, raw in enumerate(fh):
            text = raw.rstrip(b"\n")
            text_bytes += len(text)
            size += len(text) + 8
            items.append(text)
            pos += len(raw)
            if size >= config.UPLOAD_BATCH_BYTES - 64 * 1024:
                packed = len(_pack(items))
                batches.append({"start": start, "end": pos, "count": len(items), "bytes": packed})
                total += packed
                items, size, start = [], 0, pos
                progress("upload", i + 1)
    if items:
        packed = len(_pack(items))
        batches.append({"start": start, "end": pos, "count": len(items), "bytes": packed})
        total += packed
    return {"format": FORMAT, "source": "mailbox.jsonl", "batches": batches, "bytes": total, "text_bytes": text_bytes,
            "vector_bytes": 0}


def plan_kind(rs, progress=lambda stage, n: None) -> dict:
    """Batch boundaries of {kind}.jsonl; photos carry their JPEG next to the JSON."""
    batches, size, start, pos, total, text_bytes, image_bytes = [], 0, 0, 0, 0, 0, 0
    current: list = []
    with open(rs.path, "rb") as fh:
        lines = [raw for raw in fh]
    i = 0
    for raw in lines:
        if not raw.strip():
            pos += len(raw)
            continue
        text = raw.rstrip(b"\n")
        rec = rs.records[i]
        image = rs.image_bytes(rec["id"], "full") if rs.kind == "photo" else None
        extra = len(image) if image else 0
        if current and size + len(text) + extra + 16 > config.UPLOAD_BATCH_BYTES - 64 * 1024:
            packed = len(_pack(current))
            batches.append({"start": start, "end": pos, "count": len(current), "bytes": packed})
            total += packed
            current, size, start = [], 0, pos
        current.append([text, image] if image else text)
        size += len(text) + extra + 16
        text_bytes += len(text)
        image_bytes += extra
        pos += len(raw)
        i += 1
        progress(f"upload:{rs.kind}", i)
    if current:
        packed = len(_pack(current))
        batches.append({"start": start, "end": pos, "count": len(current), "bytes": packed})
        total += packed
    return {"format": FORMAT, "source": rs.path.name, "batches": batches, "bytes": total, "records": rs.count,
            "text_bytes": text_bytes, "image_bytes": image_bytes}


class Uploader:
    """Packs batch i of a kind exactly as planned (``load`` runs in a worker thread)."""

    def __init__(self, kind: str, plan: dict, path: Path, records=None):
        self.kind, self.plan, self.path, self.records = kind, plan, Path(path), records
        self.batches = plan["batches"]

    def __len__(self) -> int:
        return len(self.batches)

    def load(self, i: int) -> bytes:
        batch = self.batches[i]
        with open(self.path, "rb") as fh:
            fh.seek(batch["start"])
            chunk = fh.read(batch["end"] - batch["start"])
        items = []
        for raw in chunk.split(b"\n"):
            if not raw.strip():
                continue
            if self.kind == "photo" and self.records is not None:
                image = self.records.image_bytes(json.loads(raw)["id"], "full")
                items.append([raw, image] if image else raw)
            else:
                items.append(raw)
        blob = _pack(items)
        if len(blob) != batch["bytes"]:
            raise ValueError(f"{self.kind} upload batch {i} changed on disk; restart to rebuild")
        return blob
