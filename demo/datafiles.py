"""Restore the demo data files that are stored compressed / split in git.

GitHub rejects files over 100 MB, so the repository keeps
  demo/data/mailbox.jsonl.gz        (mailbox, gzip)
  demo/data/embeddings.f32.partNN   (embeddings, split raw float32)
and this module rebuilds mailbox.jsonl / embeddings.f32 on first start.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
from pathlib import Path

from . import config

PART_BYTES = 70 * 1000 * 1000


def ensure_data(folder: Path | None = None) -> None:
    folder = Path(folder or config.DATA)
    mailbox, packed = folder / "mailbox.jsonl", folder / "mailbox.jsonl.gz"
    if not mailbox.exists() and packed.exists():
        tmp = folder / ".mailbox.jsonl.tmp"
        with gzip.open(packed, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 16 << 20)
        os.replace(tmp, mailbox)
    emb = folder / "embeddings.f32"
    parts = sorted(folder.glob("embeddings.f32.part*"))
    if not emb.exists() and parts:
        tmp = folder / ".embeddings.f32.tmp"
        with open(tmp, "wb") as dst:
            for part in parts:
                with open(part, "rb") as src:
                    shutil.copyfileobj(src, dst, 16 << 20)
        meta = json.loads((folder / "embeddings.json").read_text("utf-8"))
        expected = int(meta["count"]) * int(meta["dim"]) * 4
        if tmp.stat().st_size != expected:
            tmp.unlink()
            raise ValueError(f"embeddings parts add up to the wrong size (expected {expected} bytes)")
        os.replace(tmp, emb)


def pack_data(folder: Path | None = None) -> list[Path]:
    """Write the git-friendly copies (used when publishing the repository)."""
    folder = Path(folder or config.DATA)
    out = []
    packed = folder / "mailbox.jsonl.gz"
    with open(folder / "mailbox.jsonl", "rb") as src, gzip.open(packed, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst, 16 << 20)
    out.append(packed)
    for old in folder.glob("embeddings.f32.part*"):
        old.unlink()
    with open(folder / "embeddings.f32", "rb") as src:
        index = 0
        while chunk := src.read(PART_BYTES):
            part = folder / f"embeddings.f32.part{index:02d}"
            part.write_bytes(chunk)
            out.append(part)
            index += 1
    return out


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["pack"]:
        for path in pack_data():
            print(path, path.stat().st_size)
    else:
        ensure_data()
        print("data ready")
