"""Embeddings for the device records: demo/data/devices/embeddings_{kind}.f32 + .json.

    .venv-demo/bin/python -m demo.corpus.devices.embed [photo sport car]

Uses the repository's own model (demo.embed: BAAI/bge-small-zh-v1.5, ONNX, CPU,
CLS pooling, L2-normalised, 512-d), document side (no query prefix).
Row i = line i of {kind}.jsonl (record id i+1), little-endian float32, C order.
"""

from __future__ import annotations

import sys
import time

import numpy as np

from demo.corpus.devices.common import OUT, read_jsonl, write_json
from demo.embed import DIM, MODEL_ID, ONNX_REPO, QUERY_PREFIX, Embedder

RECIPES = {
    "photo": "title + caption + '拍摄地点：' country city place.name + '拍摄时间：' 年月日与时段 + '行程：' trip "
             "+ '类别：' type + '标签：' tags",
    "sport": "title + text (date, place, activity and all numbers as written in the record)",
    "car": "title + text (date, from/to or station, distance, energy, charging and summary numbers as written)",
}


def _period(hour: int) -> str:
    if hour < 5:
        return "深夜"
    if hour < 9:
        return "清晨"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 20:
        return "傍晚"
    return "夜晚"


def embed_text(rec: dict) -> str:
    kind = rec["kind"]
    if kind == "photo":
        p = rec["place"]
        d = rec["date"]
        y, m, day, hh = int(d[:4]), int(d[5:7]), int(d[8:10]), int(d[11:13])
        where = p["city"] if p["country"] == "中国" else f"{p['country']} {p['city']}"
        return "\n".join([
            rec["title"],
            rec["caption"],
            f"拍摄地点：{where} {p['name']}",
            f"拍摄时间：{y}年{m}月{day}日 {_period(hh)}",
            f"行程：{rec['fields']['trip']}",
            f"类别：{rec['fields']['type']}；标签：{'、'.join(rec['tags'])}",
        ])
    return f"{rec['title']}\n{rec['text']}"


def build(kind: str, emb: Embedder | None = None) -> dict:
    recs = read_jsonl(OUT / f"{kind}.jsonl")
    assert [r["id"] for r in recs] == list(range(1, len(recs) + 1)), f"{kind}: ids must be 1..N in line order"
    texts = [embed_text(r) for r in recs]
    emb = emb or Embedder()
    t0 = time.time()
    vecs = emb.encode_docs(texts).astype(np.float32)
    assert vecs.shape == (len(recs), DIM)
    tmp = OUT / f"embeddings_{kind}.f32.tmp"
    vecs.astype("<f4").tofile(tmp)
    tmp.replace(OUT / f"embeddings_{kind}.f32")
    meta = {
        "model": MODEL_ID, "dim": DIM, "count": len(recs), "text": RECIPES[kind],
        "onnx": ONNX_REPO, "dtype": "float32", "normalized": True, "pooling": "CLS",
        "layout": f"row i = line i of {kind}.jsonl (record id i+1), little-endian float32, C order",
        "builder": "demo.corpus.devices.embed.embed_text", "query_prefix": QUERY_PREFIX,
        "seconds": round(time.time() - t0, 1),
    }
    write_json(OUT / f"embeddings_{kind}.json", meta)
    print(f"[embed] {kind}: {len(recs)} x {DIM} in {meta['seconds']}s", flush=True)
    return meta


def main() -> None:
    kinds = sys.argv[1:] or ["photo", "sport", "car"]
    emb = Embedder()
    for k in kinds:
        if (OUT / f"{k}.jsonl").exists():
            build(k, emb)
        else:
            print(f"[embed] skip {k}: no {k}.jsonl", file=sys.stderr)


if __name__ == "__main__":
    main()
