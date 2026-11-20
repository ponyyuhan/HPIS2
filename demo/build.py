"""One-time provisioning: encrypt and upload the mailbox into HPIS, and prepare the
plain-upload batches used by the ordinary-cloud lane.

Records written per email (HPIS 2.0 tenant):
  * body       AES-256-GCM(email JSON + embedding), key DEK held by the device
  * SSE labels HMAC-SHA256(org / month / category / people), key held by the device
  * vector     AES-256-GCM(512-d embedding), key VEK, opened only in HPIS' trusted region
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import msgpack
import numpy as np

from hpis.provision import init as provision

from . import config, retrieval
from .cluster import DEPLOYMENTS, Cluster, deploy_root, device_folder
from .mailbox import Mailbox

BUILD_FILE = config.RUNTIME / "build.json"
UPLOAD_DIR = config.RUNTIME / "upload"
BETA = 0.25


def load_embeddings(count: int) -> np.ndarray:
    path = config.DATA / "embeddings.f32"
    meta = json.loads((config.DATA / "embeddings.json").read_text("utf-8"))
    dim = int(meta["dim"])
    data = np.fromfile(path, dtype=np.float32).reshape(-1, dim)
    if len(data) < count:
        raise ValueError(f"embeddings cover {len(data)} rows, mailbox has {count}")
    return data[:count]


def data_signature() -> dict:
    files = {}
    for name in ("mailbox.jsonl", "embeddings.f32"):
        st = (config.DATA / name).stat()
        files[name] = [st.st_size, int(st.st_mtime)]
    return files


def current_build():
    if BUILD_FILE.exists():
        try:
            build = json.loads(BUILD_FILE.read_text("utf-8"))
        except ValueError:
            return None
        if build.get("signature") == data_signature():
            return build
    return None


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


async def ingest(name: str, mailbox: Mailbox, vectors: np.ndarray, progress) -> dict:
    root = deploy_root(name)
    if root.exists():
        shutil.rmtree(root)
    provision(root, DEPLOYMENTS[name], vectors.shape[1], BETA)
    cluster = await Cluster((name,), cloud=False).start()
    t0 = time.perf_counter()
    sent = 0
    try:
        async with cluster.device(name) as device:
            batch = []
            for i, raw in enumerate(mailbox.iter_lines()):
                mail = json.loads(raw)
                batch.append(
                    {
                        "id": int(mail["id"]),
                        "fields": retrieval.index_fields(mail),
                        "keywords": [],
                        "vector": vectors[i],
                        "content": mail,
                    }
                )
                if len(batch) == config.INGEST_BATCH:
                    await device.put(batch)
                    sent += len(batch)
                    batch = []
                    progress(name, sent)
            if batch:
                await device.put(batch)
                sent += len(batch)
                progress(name, sent)
    finally:
        await cluster.stop()
    seconds = time.perf_counter() - t0
    return {
        "mode": DEPLOYMENTS[name],
        "rows": sent,
        "seconds": round(seconds, 2),
        "store_bytes": dir_bytes(root / "hpis"),
    }


UPLOAD_FORMAT = 2  # 2 = email JSON only (content + attachments), no device vectors


def build_upload(mailbox: Mailbox, progress) -> dict:
    """Plain-cloud upload batches: exactly the emails (headers, body, attachments as text)."""
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    UPLOAD_DIR.mkdir(parents=True)
    batch, size, files, total, text_bytes = [], 0, [], 0, 0

    def flush():
        nonlocal batch, size, total
        if not batch:
            return
        blob = msgpack.packb(batch, use_bin_type=True)
        path = UPLOAD_DIR / f"batch-{len(files):04d}.bin"
        path.write_bytes(blob)
        files.append(path.name)
        total += len(blob)
        batch, size = [], 0

    for i, raw in enumerate(mailbox.iter_lines()):
        text = raw.rstrip(b"\n")
        text_bytes += len(text)
        size += len(text) + 8
        batch.append(text)
        if size >= config.UPLOAD_BATCH_BYTES - 64 * 1024:
            flush()
            progress("upload", i + 1)
    flush()
    return {"format": UPLOAD_FORMAT, "batches": files, "bytes": total, "text_bytes": text_bytes, "vector_bytes": 0}


async def build(force: bool = False, progress=None) -> dict:
    progress = progress or (lambda stage, n: None)
    existing = None if force else current_build()
    if existing:
        if existing.get("upload", {}).get("format") != UPLOAD_FORMAT:
            existing["upload"] = build_upload(Mailbox(), progress)
            BUILD_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), "utf-8")
        return existing
    mailbox = Mailbox()
    vectors = load_embeddings(mailbox.count)
    result = {"signature": data_signature(), "emails": mailbox.count, "dim": int(vectors.shape[1]), "beta": BETA}
    result["deployments"] = {}
    for name in DEPLOYMENTS:
        result["deployments"][name] = await ingest(name, mailbox, vectors, progress)
    result["upload"] = build_upload(mailbox, progress)
    result["summary"] = mailbox.summary
    result["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    BUILD_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUILD_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Provision the HPIS 2.0 demo deployments")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    last = {}

    def progress(stage, n):
        if n - last.get(stage, 0) >= 5000 or stage not in last:
            print(f"  {stage}: {n:,}", flush=True)
            last[stage] = n

    from .datafiles import ensure_data

    ensure_data()
    result = asyncio.run(build(args.force, progress))
    print(json.dumps({k: v for k, v in result.items() if k != "summary"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
