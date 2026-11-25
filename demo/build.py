"""One-time provisioning: encrypt and upload each device's data into HPIS, and prepare
the plain-upload batches used by the ordinary-cloud lane.

Two deployments (``cluster.DEPLOYMENTS``) hold the same tenants:
  tee    HPIS 2.0: AES-256-GCM bodies, HMAC-SHA256 SSE labels, AES-GCM vectors opened
         only inside HPIS' trusted region
  plain  the same service stack with plaintext storage (baseline of the paired check)

Tenants in each deployment, one anonymous AID with its own keys per device folder:
  default   PC, 100,000 emails (body = email JSON + embedding)
  phone     手机, the photo album (body = record + the JPEG; caption vector in the TEE index)
  watch     手表, watch records
  car       车机, car records
  decoy-NN  other people's data (re-encrypted samples), so the store is visibly multi-tenant

Records written per item (HPIS 2.0 tenant):
  * body       AES-256-GCM(record JSON [+ picture] + embedding), key DEK held by the device
  * SSE labels HMAC-SHA256(the kind's fields, year / quarter / month), key held by the device
  * vector     AES-256-GCM(512-d embedding), key VEK, opened only in HPIS' trusted region

The build is incremental: the mailbox (minutes to encrypt) is kept while its files are
unchanged; a device kind is re-ingested when its files change; decoys are added once.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import numpy as np

from hpis.provision import enroll, read_config
from hpis.provision import init as provision

from . import config, records, retrieval, uploads
from .cluster import DEPLOYMENTS, Cluster, deploy_root, device_folder
from .devices import BY_KIND, OTHER_KINDS, Catalog, RecordSet
from .mailbox import Mailbox

BUILD_FILE = config.RUNTIME / "build.json"
UPLOAD_DIR = config.RUNTIME / "upload"  # stored batches of format 2 (removed on update)
BETA = 0.25
BUILD_VERSION = 3  # 3: four device tenants + decoy tenants (v2 and older: mail only)
INDEX_VERSION = 2  # SSE labels per record; 2 adds day labels (exact date windows)
UPLOAD_FORMAT = uploads.FORMAT  # batches packed on the fly from the data files
DECOY_VERSION = 1
DECOY_SIZES = (2400, 1300, 3100, 900, 1800, 2700, 1100, 1600)  # records per decoy tenant
DECOY_PREFIX = "decoy-"


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


def _read_build() -> dict | None:
    if BUILD_FILE.exists():
        try:
            return json.loads(BUILD_FILE.read_text("utf-8"))
        except ValueError:
            return None
    return None


def current_build(catalog: Catalog | None = None):
    """The build on disk if it matches the data exactly (else None: ``build`` will update it)."""
    build = _read_build()
    if not build or build.get("version") != BUILD_VERSION or build.get("signature") != data_signature():
        return None
    if build.get("index") != INDEX_VERSION:
        return None
    if (build.get("upload") or {}).get("format") != UPLOAD_FORMAT:
        return None
    if (build.get("decoys") or {}).get("version") != DECOY_VERSION:
        return None
    catalog = catalog or Catalog(Mailbox())
    kinds = build.get("kinds") or {}
    if set(kinds) != set(catalog.sets):
        return None
    for kind, rs in catalog.sets.items():
        if kinds[kind].get("signature") != rs.signature():
            return None
        if (build.get("uploads") or {}).get(kind, {}).get("format") != UPLOAD_FORMAT:
            return None
    return build


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


# ------------------------------------------------------------------ tenants


def tenant_names(name: str) -> list[str]:
    folder = deploy_root(name) / "devices"
    return sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.exists() else []


def drop_tenant(name: str, tenant: str) -> bool:
    """Remove one device's tenant (rows and keys) from a stopped deployment."""
    folder = device_folder(name, tenant)
    if not folder.exists():
        return False
    aid = read_config(folder)["aid"]
    with closing(sqlite3.connect(deploy_root(name) / "hpis" / "store.sqlite")) as db, db:
        for table in ("postings", "vectors", "docs", "tenants"):
            db.execute(f"DELETE FROM {table} WHERE aid=?", (aid,))
    shutil.rmtree(folder)
    return True


def tenant_rows(name: str, tenant: str) -> int:
    folder = device_folder(name, tenant)
    if not folder.exists():
        return -1
    aid = read_config(folder)["aid"]
    path = deploy_root(name) / "hpis" / "store.sqlite"
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return int(db.execute("SELECT count(*) FROM docs WHERE aid=?", (aid,)).fetchone()[0])


async def put_all(device, items, progress, key: str) -> int:
    """Encrypt and upload records in batches bounded by count and bytes (photos are large)."""
    batch, size, sent = [], 0, 0
    for item in items:
        batch.append(item)
        size += item.pop("_bytes", 4096)
        if len(batch) >= config.INGEST_BATCH or size >= config.INGEST_BATCH_BYTES:
            await device.put(batch)
            sent += len(batch)
            batch, size = [], 0
            progress(key, sent)
    if batch:
        await device.put(batch)
        sent += len(batch)
        progress(key, sent)
    return sent


def mail_items(mailbox: Mailbox, vectors: np.ndarray):
    for i, raw in enumerate(mailbox.iter_lines()):
        mail = json.loads(raw)
        yield {
            "id": int(mail["id"]),
            "fields": retrieval.index_fields(mail),
            "keywords": [],
            "vector": vectors[i],
            "content": mail,
            "_bytes": len(raw) + 2048,
        }


def kind_vectors(rs: RecordSet, dim: int) -> np.ndarray:
    """The device's vectors of one kind: the data's own file, else computed here once."""
    vectors = rs.vectors()
    if vectors is None:
        import hashlib

        tag = hashlib.sha256(json.dumps([rs.signature(), rs.text_fields]).encode()).hexdigest()[:16]
        cache = config.RUNTIME / "vectors" / f"{rs.kind}-{tag}.f32"
        if cache.exists() and cache.stat().st_size == rs.count * dim * 4:
            vectors = np.fromfile(cache, dtype=np.float32).reshape(rs.count, dim)
        else:
            from .embed import Embedder

            texts = [records.doc_text(rs.kind, rec, rs.text_fields) for rec in rs.records]
            vectors = Embedder().encode_docs(texts)
            cache.parent.mkdir(parents=True, exist_ok=True)
            vectors.astype(np.float32).tofile(cache)
    if vectors.shape[1] != dim:
        raise ValueError(f"{rs.kind} vectors are {vectors.shape[1]}-d, the deployment is {dim}-d")
    return vectors


def kind_items(rs: RecordSet, vectors: np.ndarray):
    for i, rec in enumerate(rs.records):
        content = dict(rec)
        extra = 0
        if rs.kind == "photo":
            image = rs.image_bytes(rec["id"], "full")
            if image:
                content["image"] = image
                extra = len(image)
        yield {
            "id": int(rec["id"]),
            "fields": records.index_fields(rs.kind, rec),
            "keywords": [],
            "vector": vectors[i],
            "content": content,
            "_bytes": extra + 4096,
        }


def decoy_size(i: int, count: int) -> int:
    """Records of decoy tenant i, scaled down for small (test) mailboxes."""
    return max(20, min(count, round(DECOY_SIZES[i] * min(1.0, count / 100_000))))


def decoy_items(mailbox: Mailbox, vectors: np.ndarray, size: int, seed: int):
    """Another person's records: a sample of the mailbox, trimmed, under that tenant's own keys."""
    rng = np.random.default_rng(seed)
    picks = np.sort(rng.choice(mailbox.count, size=min(size, mailbox.count), replace=False))
    ids = mailbox.ids
    keep = 200 + int(rng.integers(0, 900))
    for rid, i in enumerate(picks.tolist(), 1):
        mail = mailbox.get(int(ids[i]))
        content = {k: mail.get(k) for k in ("date", "category", "from", "to", "subject")}
        content["body"] = (mail.get("body") or "")[:keep]
        yield {
            "id": rid,
            "fields": retrieval.index_fields(mail),
            "keywords": [],
            "vector": vectors[i],
            "content": content,
            "_bytes": keep * 3 + 2048,
        }


# ------------------------------------------------------------------ uploads


def build_upload(mailbox: Mailbox, progress) -> dict:
    """Plain-cloud upload of the mailbox: exactly the emails (headers, body, attachments as text)."""
    return uploads.plan_mail(mailbox.path, progress)


def uploads_plan_kind(rs: RecordSet, progress) -> dict:
    """The whole collection of one kind as the device uploads it to the ordinary cloud."""
    return uploads.plan_kind(rs, progress)


# ------------------------------------------------------------------ build


async def _deployment(name: str, plan: dict, mailbox: Mailbox, catalog: Catalog, vectors, progress) -> dict:
    """Bring one deployment up to date: mail (fresh only), device kinds, decoys."""
    root = deploy_root(name)
    out = {"kinds": {}, "decoys": {}}
    if plan["fresh"]:
        if root.exists():
            shutil.rmtree(root)
        provision(root, DEPLOYMENTS[name], int(vectors.shape[1]), BETA)
    for kind in plan["drop"]:
        drop_tenant(name, BY_KIND[kind].tenant)
    if plan["decoys"]:
        for tenant in tenant_names(name):
            if tenant.startswith(DECOY_PREFIX):
                drop_tenant(name, tenant)
    for kind in plan["kinds"]:
        drop_tenant(name, BY_KIND[kind].tenant)
        enroll(root, BY_KIND[kind].tenant)
    decoys = [f"{DECOY_PREFIX}{i + 1:02d}" for i in range(len(DECOY_SIZES))] if plan["decoys"] else []
    for tenant in decoys:
        enroll(root, tenant)
    if not (plan["fresh"] or plan["kinds"] or decoys):
        return out
    cluster = await Cluster((name,), cloud=False).start()
    try:
        if plan["fresh"]:
            t0 = time.perf_counter()
            async with cluster.device(name) as device:
                rows = await put_all(device, mail_items(mailbox, vectors), progress, name)
            out["mail"] = {"mode": DEPLOYMENTS[name], "rows": rows, "seconds": round(time.perf_counter() - t0, 2)}
        for kind in plan["kinds"]:
            rs = catalog.sets[kind]
            t0 = time.perf_counter()
            async with cluster.device(name, BY_KIND[kind].tenant) as device:
                rows = await put_all(device, kind_items(rs, plan["vectors"][kind]), progress, f"{name}:{kind}")
            out["kinds"][kind] = {"rows": rows, "seconds": round(time.perf_counter() - t0, 2)}
        t0 = time.perf_counter()
        for i, tenant in enumerate(decoys):
            size = decoy_size(i, mailbox.count)
            async with cluster.device(name, tenant) as device:
                await put_all(device, decoy_items(mailbox, vectors, size, 1000 + i), progress, "decoys")
        if decoys:
            out["decoys"] = {"seconds": round(time.perf_counter() - t0, 2)}
    finally:
        await cluster.stop()
    return out


async def build(force: bool = False, progress=None) -> dict:
    mailbox = Mailbox()
    try:
        return await _build(mailbox, force, progress or (lambda stage, n: None))
    finally:
        mailbox.close()


async def _build(mailbox: Mailbox, force: bool, progress) -> dict:
    catalog = Catalog(mailbox)
    if not force:
        ready = current_build(catalog)
        if ready:
            return ready
    previous = None if force else _read_build()
    signature = data_signature()
    stores = all((deploy_root(n) / "hpis" / "store.sqlite").exists() for n in DEPLOYMENTS)
    fresh = (
        force
        or not previous
        or previous.get("signature") != signature
        or previous.get("index") != INDEX_VERSION
        or not stores
        or any(tenant_rows(n, "default") != mailbox.count for n in DEPLOYMENTS)
    )
    vectors = load_embeddings(mailbox.count)
    dim = int(vectors.shape[1])
    known = {} if fresh else dict((previous or {}).get("kinds") or {})
    kinds, kind_vecs, skipped = [], {}, dict(catalog.errors)
    for kind, rs in catalog.sets.items():
        sig = rs.signature()
        present = all(device_folder(n, BY_KIND[kind].tenant).exists() for n in DEPLOYMENTS)
        if not fresh and present and (known.get(kind) or {}).get("signature") == sig:
            continue
        try:
            kind_vecs[kind] = kind_vectors(rs, dim)
            kinds.append(kind)
        except (OSError, ValueError) as exc:
            skipped[kind] = f"{type(exc).__name__}: {exc}"
    usable = set(catalog.sets) - set(skipped)
    drop = [k for k in OTHER_KINDS if k not in usable and not fresh]
    decoys = fresh or ((previous or {}).get("decoys") or {}).get("version") != DECOY_VERSION
    plan = {"fresh": fresh, "kinds": kinds, "vectors": kind_vecs, "drop": drop, "decoys": decoys}

    result = {} if fresh else {k: v for k, v in (previous or {}).items()}
    result.update(signature=signature, emails=mailbox.count, dim=dim, beta=BETA)
    result.setdefault("deployments", {})
    kinds_info = {k: v for k, v in known.items() if k in usable}
    for name in DEPLOYMENTS:
        done = await _deployment(name, plan, mailbox, catalog, vectors, progress)
        if "mail" in done:
            result["deployments"][name] = done["mail"]
        for kind, info in done["kinds"].items():
            entry = kinds_info.setdefault(kind, {})
            entry.setdefault("seconds", {})[name] = info["seconds"]
            entry["rows"] = info["rows"]
        if done.get("decoys"):
            result.setdefault("decoys", {}).setdefault("seconds", {})[name] = done["decoys"]["seconds"]
    for kind in kinds:
        rs = catalog.sets[kind]
        kinds_info[kind].update(signature=rs.signature(), tenant=BY_KIND[kind].tenant, records=rs.count)
    result["kinds"] = kinds_info
    if decoys:
        result["decoys"] = {
            "version": DECOY_VERSION,
            "tenants": [f"{DECOY_PREFIX}{i + 1:02d}" for i in range(len(DECOY_SIZES))],
            "records": [decoy_size(i, mailbox.count) for i in range(len(DECOY_SIZES))],
            "seconds": (result.get("decoys") or {}).get("seconds", {}),
        }
    for name in DEPLOYMENTS:
        result["deployments"].setdefault(name, {"mode": DEPLOYMENTS[name]})
        result["deployments"][name]["store_bytes"] = dir_bytes(deploy_root(name) / "hpis")

    plans = {} if fresh else dict((previous or {}).get("uploads") or {})
    upload = None if fresh else (previous or {}).get("upload")
    if not upload or upload.get("format") != UPLOAD_FORMAT:
        upload = await asyncio.to_thread(build_upload, mailbox, progress)
    plans["mail"] = upload
    for kind in OTHER_KINDS:
        if kind not in usable:
            plans.pop(kind, None)
            continue
        old = plans.get(kind) or {}
        if kind in kinds or old.get("format") != UPLOAD_FORMAT:
            plans[kind] = await asyncio.to_thread(uploads_plan_kind, catalog.sets[kind], progress)
    shutil.rmtree(UPLOAD_DIR, ignore_errors=True)  # stored batches of older builds
    result["upload"] = upload
    result["uploads"] = plans
    result["skipped"] = skipped
    result["summary"] = mailbox.summary
    result["version"] = BUILD_VERSION
    result["index"] = INDEX_VERSION
    result["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    BUILD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BUILD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(BUILD_FILE)
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
    shown = {k: v for k, v in result.items() if k not in ("summary", "upload", "uploads")}
    shown["uploads"] = {k: {"batches": len(v["batches"]), "bytes": v["bytes"]} for k, v in result["uploads"].items()}
    print(json.dumps(shown, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
