"""Stage 2 (photos): download candidates and make clean JPEG copies.

    $HPIS_DEVICES_BUILD/venv/bin/python -I demo/corpus/devices/photo_fetch.py [--ids work/selected_ids.txt]

Downloads ``image_url?w=960&h=960&fit=max&q=80&fm=jpg`` for every pool candidate
(sequential-ish: 4 workers, retries, generic User-Agent) into
``$BUILD/downloads/img/{photo_id}.jpg`` (untrusted). Each file is then decoded
with Pillow and re-encoded (EXIF orientation applied, metadata dropped) to
``$BUILD/work/img/{photo_id}.jpg`` (long side 960, quality 80) and
``$BUILD/work/cap/{photo_id}.jpg`` (long side 640, for the captioning model).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.corpus.devices.common import DOWNLOADS, WORK, read_jsonl  # noqa: E402

UA = "Mozilla/5.0 (compatible; photo-fetch/1.0)"
PARAMS = "w=960&h=960&fit=max&q=80&fm=jpg"


def _url(image_url: str) -> str:
    if not image_url.startswith("https://images.unsplash.com/"):
        raise ValueError(f"unexpected image host: {image_url[:60]}")
    sep = "&" if "?" in image_url else "?"
    return f"{image_url}{sep}{PARAMS}"


def download(rec: dict) -> tuple[str, str]:
    dst = DOWNLOADS / "img" / f"{rec['id']}.jpg"
    if dst.exists() and dst.stat().st_size > 2000:
        return rec["id"], "cached"
    try:
        url = _url(rec["image_url"])
    except ValueError as e:
        return rec["id"], f"failed: {e}"
    delay = 2.0
    for _attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/jpeg"})
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status != 200:
                    raise OSError(f"http {r.status}")
                ctype = r.headers.get("Content-Type", "")
                data = r.read(8_000_000)
            if not ctype.startswith("image/") or len(data) < 2000:
                raise OSError(f"bad payload {ctype} {len(data)}")
            tmp = dst.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dst)
            return rec["id"], "ok"
        except Exception as e:  # noqa: BLE001 - retry any network error
            err = str(e)[:80]
            time.sleep(delay)
            delay *= 2
    return rec["id"], f"failed: {err}"


def process(pid: str) -> str:
    from PIL import Image, ImageOps

    src = DOWNLOADS / "img" / f"{pid}.jpg"
    out_full = WORK / "img" / f"{pid}.jpg"
    out_cap = WORK / "cap" / f"{pid}.jpg"
    if out_full.exists() and out_cap.exists():
        return "cached"
    Image.MAX_IMAGE_PIXELS = 4_000_000
    with Image.open(src) as im:
        im.load()
        im = ImageOps.exif_transpose(im).convert("RGB")
        full = im.copy()
        full.thumbnail((960, 960), Image.Resampling.LANCZOS)
        full.save(out_full, "JPEG", quality=80, optimize=True)
        cap = im.copy()
        cap.thumbnail((640, 640), Image.Resampling.LANCZOS)
        cap.save(out_cap, "JPEG", quality=82)
    return "ok"


def main() -> None:
    pool = read_jsonl(WORK / "pool.jsonl")
    if "--ids" in sys.argv:
        want = set(Path(sys.argv[sys.argv.index("--ids") + 1]).read_text().split())
        pool = [r for r in pool if r["id"] in want]
    (DOWNLOADS / "img").mkdir(parents=True, exist_ok=True)
    (WORK / "img").mkdir(parents=True, exist_ok=True)
    (WORK / "cap").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    status = {}
    with ThreadPoolExecutor(4) as ex:
        for i, (pid, st) in enumerate(ex.map(download, pool), 1):
            status[pid] = st
            if i % 100 == 0:
                print(f"[fetch] {i}/{len(pool)} {time.time() - t0:.0f}s", flush=True)
    failed = {k: v for k, v in status.items() if v.startswith("failed")}
    done = 0
    for pid, st in status.items():
        if st.startswith("failed"):
            continue
        try:
            process(pid)
            done += 1
        except Exception as e:  # noqa: BLE001 - a broken file just drops out
            failed[pid] = f"decode: {str(e)[:60]}"
    (WORK / "fetch_status.json").write_text(json.dumps({"ok": done, "failed": failed}, indent=1), "utf-8")
    print(f"[fetch] processed {done}, failed {len(failed)} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
