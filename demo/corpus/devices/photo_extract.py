"""Stage 1 (photos): compact metadata of every Unsplash Lite row -> work/unsplash_meta.jsonl.

Runs in the throwaway image venv (pyarrow), isolated from the downloads folder:

    $BUILD/venv/bin/python -I demo/corpus/devices/photo_extract.py

The three parquet files of the Hugging Face dataset ``1aurent/unsplash-lite``
(https://huggingface.co/datasets/1aurent/unsplash-lite, data/train-0000{0,1,2}-of-00003.parquet)
must be in $HPIS_DEVICES_BUILD/downloads/parquet/. They are read as data only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.corpus.devices.common import DOWNLOADS, WORK, visible_files  # noqa: E402

COLUMNS = ["photo", "photographer", "location", "stats", "ai", "keywords", "collections"]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def compact(row: dict) -> dict:
    p = row.get("photo") or {}
    who = row.get("photographer") or {}
    loc = row.get("location") or {}
    ai = row.get("ai") or {}
    st = row.get("stats") or {}
    kws = []
    for k in row.get("keywords") or []:
        kw = (k.get("keyword") or "").strip()
        if not kw:
            continue
        c1, c2 = _f(k.get("ai_service_1_confidence")), _f(k.get("ai_service_2_confidence"))
        conf = max([c for c in (c1, c2) if c is not None], default=None)
        kws.append([kw, conf, bool(k.get("suggested_by_user"))])
    kws.sort(key=lambda t: (-(t[1] if t[1] is not None else (60.0 if t[2] else 0.0))))
    return {
        "id": p.get("id"),
        "url": p.get("url"),
        "image_url": p.get("image_url"),
        "w": p.get("width"),
        "h": p.get("height"),
        "submitted_at": p.get("submitted_at"),
        "desc": p.get("description"),
        "user": who.get("username"),
        "name": " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x),
        "loc": {
            "name": loc.get("name"),
            "lat": _f(loc.get("latitude")),
            "lon": _f(loc.get("longitude")),
            "country": loc.get("country"),
            "city": loc.get("city"),
        },
        "views": st.get("views"),
        "downloads": st.get("downloads"),
        "ai": ai.get("description"),
        "landmark": ai.get("primary_landmark_name"),
        "landmark_conf": _f(ai.get("primary_landmark_confidence")),
        "kw": kws[:60],
        "coll": [c.get("collection_title") for c in (row.get("collections") or [])][:12],
    }


def main() -> None:
    import pyarrow.parquet as pq

    src = DOWNLOADS / "parquet"
    files = visible_files(src, ".parquet")
    if not files:
        sys.exit(f"no parquet files in {src}")
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / "unsplash_meta.jsonl"
    n = 0
    with open(out.with_suffix(".tmp"), "w", encoding="utf-8") as fh:
        for f in files:
            pf = pq.ParquetFile(f)
            for batch in pf.iter_batches(batch_size=512, columns=COLUMNS):
                for row in batch.to_pylist():
                    rec = compact(row)
                    if rec["id"] and rec["image_url"]:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        n += 1
            print(f"[extract] {f.name}: total {n}", flush=True)
    out.with_suffix(".tmp").replace(out)
    print(f"[extract] wrote {n} rows -> {out}")


if __name__ == "__main__":
    main()
