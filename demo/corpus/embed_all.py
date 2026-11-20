"""Compute demo/data/embeddings.f32 (row i = email id i+1) and embeddings.json.

    python -m demo.corpus.embed_all
"""

from __future__ import annotations

import hashlib
import json
import sys
import time

import numpy as np

from demo.embed import DIM, MODEL_ID, ONNX_REPO, RECIPE, Embedder, doc_text

from . import bible as B

DATA = __import__("pathlib").Path(__import__("os").environ.get("CORPUS_OUT") or B.DATA)


def _work(args):
    texts, threads, tag = args
    emb = Embedder(threads=threads)
    t0 = time.time()
    last = [0.0]

    def prog(done, total):
        if time.time() - last[0] > 30:
            last[0] = time.time()
            el = time.time() - t0
            print(f"[embed:{tag}] {done}/{total} {el:.0f}s eta {el / max(done, 1) * (total - done):.0f}s", flush=True)

    return emb.encode_docs(texts, progress=prog)


def main():
    mails = []
    with open(DATA / "mailbox.jsonl", encoding="utf-8") as fh:
        for line in fh:
            mails.append(json.loads(line))
    assert [m["id"] for m in mails] == list(range(1, len(mails) + 1))
    texts = [doc_text(m) for m in mails]
    procs = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    t0 = time.time()
    # content-addressed cache: re-runs only encode texts that changed
    cache_path = B.DATA / "cache" / "emb_cache.npz"
    keys = [hashlib.md5(t.encode("utf-8")).hexdigest() for t in texts]
    cached = {}
    if cache_path.exists():
        z = np.load(cache_path, allow_pickle=False)
        cached = dict(zip(z["keys"].tolist(), z["vecs"]))
    todo = sorted({k: i for i, k in enumerate(keys) if k not in cached}.values())
    print(f"[embed] {len(texts)} docs, {len(texts) - len(todo)} cached, {len(todo)} to encode", flush=True)
    new = {}
    if todo:
        sub = [texts[i] for i in todo]
        if procs <= 1 or len(sub) < 2000:
            out = _work((sub, None, 0))
        else:
            from concurrent.futures import ProcessPoolExecutor

            chunks = [sub[i::procs] for i in range(procs)]
            with ProcessPoolExecutor(procs) as ex:
                parts = list(ex.map(_work, [(c, max(1, 10 // procs), i) for i, c in enumerate(chunks)]))
            out = np.zeros((len(sub), DIM), dtype=np.float32)
            for i, part in enumerate(parts):
                out[i::procs] = part
        new = {keys[i]: out[j] for j, i in enumerate(todo)}
        cached.update(new)
        ck = np.array(list(cached.keys()))
        cv = np.stack(list(cached.values())).astype(np.float32)
        tmpc = cache_path.with_name("emb_cache.tmp.npz")
        np.savez(tmpc, keys=ck, vecs=cv)
        tmpc.replace(cache_path)
    vecs = np.stack([cached[k] for k in keys]).astype(np.float32)
    assert vecs.shape == (len(mails), DIM)
    tmp = DATA / "embeddings.f32.tmp"
    vecs.astype(np.float32).tofile(tmp)
    tmp.replace(DATA / "embeddings.f32")
    meta = {
        "model": MODEL_ID, "onnx": ONNX_REPO, "dim": DIM, "count": len(mails), "dtype": "float32", "normalized": True,
        "layout": "row i = email id i+1, little-endian float32, C order", "pooling": "CLS",
        "recipe": RECIPE, "builder": "demo.embed.doc_text", "seconds": round(time.time() - t0, 1),
        "encoded_this_run": len(todo), "reused_from_cache": len(texts) - len(todo),
        "note": "a full CPU run (2 processes x 5 threads, Apple M-series) takes ~15 min; re-runs reuse demo/data/cache/emb_cache.npz",
    }
    (DATA / "embeddings.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    print(f"[embed] wrote {len(mails)} x {DIM} in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
