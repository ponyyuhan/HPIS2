"""Validate demo/data/devices against PLAN-v3 §2.

    .venv-demo/bin/python -m demo.corpus.devices.validate [--no-embed-check]

Checks: record schema and canonical field values, ids 1..N in line order, dates
inside 2024-01-01 → 2026-11-20 18:00 +08:00, photo files (JPEG size, long side
960 / 360, byte counts), exact weekly/monthly aggregates (recomputed), car battery
and odometer continuity, embedding shapes / norms / row alignment (re-encodes a
sample), presets schema and every gold fact present in an evidence record, file
sizes (≤ 90 MB each), stats.json. Exits non-zero on any error.
"""

from __future__ import annotations

import datetime as dt
import json
import struct
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from demo.corpus.devices import car as carmod
from demo.corpus.devices import sport as sportmod
from demo.corpus.devices.common import AS_OF, OUT, START, read_jsonl

ERR: list[str] = []
WARN: list[str] = []
LO = dt.datetime(START.year, START.month, START.day, tzinfo=dt.timezone(dt.timedelta(hours=8)))
ACTIVITIES = {"跑步", "骑行", "游泳", "步行", "羽毛球", "力量训练", "睡眠", "日常", "周报", "月报"}
EVENTS = {"行程", "充电", "导航", "保养", "提醒", "周报", "月报"}
FIELD_KEYS = {"photo": {"year", "month", "country", "city", "type", "trip"},
              "sport": {"year", "month", "activity"}, "car": {"year", "month", "event", "city"}}
DEVICE = {"photo": "phone", "sport": "watch", "car": "car"}


def err(msg: str) -> None:
    ERR.append(msg)


def jpeg_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return w, h
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + seg
    raise ValueError("no SOF marker")


def check_common(kind: str, recs: list[dict]) -> None:
    if [r.get("id") for r in recs] != list(range(1, len(recs) + 1)):
        err(f"{kind}: ids are not 1..N in line order")
    for r in recs:
        rid = f"{kind}#{r.get('id')}"
        for k in ("id", "kind", "device", "date", "title", "text", "fields"):
            if k not in r:
                err(f"{rid}: missing {k}")
        if r.get("kind") != kind or r.get("device") != DEVICE[kind]:
            err(f"{rid}: kind/device {r.get('kind')}/{r.get('device')}")
        try:
            t = dt.datetime.fromisoformat(r["date"])
            if t.tzinfo is None:
                err(f"{rid}: date without offset")
            elif not (LO <= t <= AS_OF):
                err(f"{rid}: date {r['date']} outside the period")
        except Exception as e:  # noqa: BLE001
            err(f"{rid}: bad date {r.get('date')}: {e}")
            continue
        f = r.get("fields") or {}
        if set(f) != FIELD_KEYS[kind]:
            err(f"{rid}: fields keys {sorted(f)}")
        if any(not isinstance(v, str) or not v.strip() for v in f.values()):
            err(f"{rid}: empty or non-string field value {f}")
        if f.get("year") != r["date"][:4] or f.get("month") != r["date"][:7]:
            err(f"{rid}: year/month {f.get('year')}/{f.get('month')} vs date {r['date']}")
        if len(r.get("text", "")) < 20:
            err(f"{rid}: text too short")


def check_photos(recs: list[dict]) -> None:
    total = 0
    for r in recs:
        rid = f"photo#{r['id']}"
        for k in ("file", "thumb", "width", "height", "bytes", "place", "caption", "tags", "source"):
            if k not in r:
                err(f"{rid}: missing {k}")
        p, th = OUT / r["file"], OUT / r["thumb"]
        if r["file"] != f"photos/img/{r['id']}.jpg" or r["thumb"] != f"photos/thumb/{r['id']}.jpg":
            err(f"{rid}: unexpected paths {r['file']} {r['thumb']}")
        if not p.exists() or not th.exists():
            err(f"{rid}: image file missing")
            continue
        w, h = jpeg_size(p)
        tw, thh = jpeg_size(th)
        if (w, h) != (r["width"], r["height"]) or max(w, h) != 960:
            err(f"{rid}: size {w}x{h} vs record {r['width']}x{r['height']}")
        if max(tw, thh) != 360:
            err(f"{rid}: thumb size {tw}x{thh}")
        if p.stat().st_size != r["bytes"]:
            err(f"{rid}: bytes {r['bytes']} vs file {p.stat().st_size}")
        total += p.stat().st_size + th.stat().st_size
        pl = r["place"]
        if set(pl) != {"name", "lat", "lon", "country", "city"} or pl["country"] != r["fields"]["country"] or \
                pl["city"] != r["fields"]["city"]:
            err(f"{rid}: place {pl}")
        if not (-90 <= pl["lat"] <= 90 and -180 <= pl["lon"] <= 180):
            err(f"{rid}: lat/lon {pl}")
        s = r["source"]
        if not {"dataset", "id", "photographer", "url"} <= set(s) or not s["url"].startswith("https://unsplash.com/"):
            err(f"{rid}: source {s}")
        if not r["caption"] or r["caption"] not in r["text"]:
            err(f"{rid}: caption missing from text")
        if not r["tags"] or not all(isinstance(t, str) and t for t in r["tags"]):
            err(f"{rid}: tags {r['tags']}")
        off = r["date"][-6:]
        want = {"德国": "+02:00", "韩国": "+09:00", "日本": "+09:00", "中国": "+08:00"}[r["fields"]["country"]]
        if off != want:
            err(f"{rid}: offset {off} for {r['fields']['country']}")
    ids = Counter(r["source"]["id"] for r in recs)
    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        err(f"photo: duplicate source photos {dup[:5]}")
    att = (OUT / "photos" / "ATTRIBUTION.md").read_text("utf-8")
    for r in recs:
        if r["source"]["url"] not in att:
            err(f"photo#{r['id']}: not in ATTRIBUTION.md")
    if total > 75_000_000:
        WARN.append(f"photo images total {total / 1e6:.1f} MB (> 70 MB target)")
    print(f"[validate] photo: {len(recs)} records, images {total / 1e6:.1f} MB")


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def check_sport(recs: list[dict]) -> None:
    base = [r for r in recs if r["activity"] not in ("周报", "月报")]
    for r in recs:
        if r["activity"] not in ACTIVITIES or r["fields"]["activity"] != r["activity"]:
            err(f"sport#{r['id']}: activity {r['activity']}")
        if not isinstance(r.get("metrics"), dict) or not all(
                isinstance(v, (int, float, type(None))) and not isinstance(v, bool) for v in r["metrics"].values()):
            err(f"sport#{r['id']}: metrics must be numbers")
    regen = sportmod.summaries(base)
    got = [r for r in recs if r["activity"] in ("周报", "月报")]
    if len(regen) != len(got):
        err(f"sport: {len(got)} summaries, recomputed {len(regen)}")
    for a, b in zip(sorted(got, key=lambda r: (r["date"], r["activity"])),
                    sorted(regen, key=lambda r: (r["date"], r["activity"]))):
        if a["text"] != b["text"] or any(not _same(a["metrics"][k], b["metrics"][k]) for k in b["metrics"]) \
                or a.get("detail") != b.get("detail"):
            err(f"sport#{a['id']}: summary differs from recomputation ({a['title']})")
    print(f"[validate] sport: {len(recs)} records, {len(got)} summaries recomputed exactly"
          if len(regen) == len(got) else "[validate] sport: summary count mismatch")


def check_car(recs: list[dict]) -> None:
    for r in recs:
        if r["event"] not in EVENTS or r["fields"]["event"] != r["event"]:
            err(f"car#{r['id']}: event {r['event']}")
        for k in ("from", "to", "distance_km", "duration_min", "energy_kwh"):
            if k not in r:
                err(f"car#{r['id']}: missing {k}")
    trips = [r for r in recs if r["event"] in ("行程", "充电")]
    soc, odo = None, None
    for r in trips:
        if r["event"] == "行程":
            if soc is not None and abs(r["soc_start"] - soc) > 1:
                err(f"car#{r['id']}: SOC jumps {soc} -> {r['soc_start']}")
            if odo is not None and r["odometer_km"] < odo - 0.5:
                err(f"car#{r['id']}: odometer goes back")
            soc, odo = r["soc_end"], r["odometer_km"]
        else:
            if soc is not None and abs(r["soc_start"] - soc) > 1:
                err(f"car#{r['id']}: charge SOC jumps {soc} -> {r['soc_start']}")
            soc = r["soc_end"]
    prev_to = None
    for r in [r for r in recs if r["event"] == "行程"]:
        if prev_to and prev_to != r["from"] and "服务区" not in prev_to:
            err(f"car#{r['id']}: starts at {r['from']} but the car was at {prev_to}")
        prev_to = r["to"]
    base = [r for r in recs if r["event"] not in ("周报", "月报")]
    regen = carmod.summaries(base)
    got = [r for r in recs if r["event"] in ("周报", "月报")]
    if len(regen) != len(got):
        err(f"car: {len(got)} summaries, recomputed {len(regen)}")
    for a, b in zip(sorted(got, key=lambda r: (r["date"], r["event"])),
                    sorted(regen, key=lambda r: (r["date"], r["event"]))):
        if a["text"] != b["text"] or a["metrics"] != b["metrics"] or a.get("detail") != b.get("detail"):
            err(f"car#{a['id']}: summary differs from recomputation ({a['title']})")
    for r in recs:
        if "metrics" in r and not all(isinstance(v, (int, float, type(None))) and not isinstance(v, bool)
                                      for v in r["metrics"].values()):
            err(f"car#{r['id']}: metrics must be numbers")
    print(f"[validate] car: {len(recs)} records, {len(got)} summaries recomputed exactly")


def check_embeddings(kind: str, recs: list[dict], sample: bool) -> None:
    f32, meta_p = OUT / f"embeddings_{kind}.f32", OUT / f"embeddings_{kind}.json"
    if not f32.exists() or not meta_p.exists():
        err(f"{kind}: embeddings missing")
        return
    meta = json.loads(meta_p.read_text("utf-8"))
    if not {"model", "dim", "count", "text"} <= set(meta) or meta["dim"] != 512 or meta["count"] != len(recs):
        err(f"{kind}: embeddings meta {meta}")
    if f32.stat().st_size != len(recs) * 512 * 4:
        err(f"{kind}: embeddings size {f32.stat().st_size} != {len(recs)}*512*4")
        return
    v = np.fromfile(f32, dtype="<f4").reshape(-1, 512)
    norms = np.linalg.norm(v, axis=1)
    if not np.all(np.abs(norms - 1) < 1e-3):
        err(f"{kind}: embeddings not L2-normalised (min {norms.min():.4f}, max {norms.max():.4f})")
    if sample:
        from demo.corpus.devices.embed import embed_text
        from demo.embed import Embedder

        rng = np.random.default_rng(7)
        idx = sorted(set(rng.choice(len(recs), size=min(24, len(recs)), replace=False).tolist()))
        enc = Embedder().encode_docs([embed_text(recs[i]) for i in idx])
        cos = (enc * v[idx]).sum(axis=1)
        if cos.min() < 0.999:
            err(f"{kind}: embedding rows do not match their records (min cos {cos.min():.4f})")
    print(f"[validate] {kind}: embeddings {v.shape}, norms {norms.min():.4f}–{norms.max():.4f}")


def check_presets(data: dict[str, list[dict]]) -> None:
    presets = json.loads((OUT / "presets.json").read_text("utf-8"))
    ids = [p["id"] for p in presets]
    for need in ("photo_germany", "photo_korea", "sport_month", "car_month"):
        if need not in ids:
            err(f"presets: missing {need}")
    if len(presets) < 4 or len(set(ids)) != len(ids):
        err("presets: need ≥ 4 unique presets")
    for p in presets:
        for k in ("id", "chip", "kind", "device", "question", "plan", "gold", "evidence_ids"):
            if k not in p:
                err(f"preset {p.get('id')}: missing {k}")
        if DEVICE.get(p["kind"]) != p["device"]:
            err(f"preset {p['id']}: kind/device")
        plan = p["plan"]
        for k in ("kind", "countries", "cities", "trips", "types", "date_from", "date_to", "semantic"):
            if k not in plan:
                err(f"preset {p['id']}: plan missing {k}")
        recs = {r["id"]: r for r in data[p["kind"]]}
        ev = p["evidence_ids"]
        if not ev or any(i not in recs for i in ev) or ev != p["gold"].get("evidence_ids"):
            err(f"preset {p['id']}: bad evidence ids")
            continue
        texts = [recs[i]["text"] + " " + recs[i]["title"] for i in ev]
        for f in p["gold"]["facts"]:
            if not f.get("match") or not any(m in t for m in f["match"] for t in texts):
                err(f"preset {p['id']}: fact {f['label']}={f['value']} not found in evidence ({f['match']})")
        # evidence must satisfy the plan's filters
        for i in ev:
            r = recs[i]
            fl = r["fields"]
            if plan["countries"] and fl.get("country") not in plan["countries"]:
                err(f"preset {p['id']}: evidence {i} country {fl.get('country')}")
            if plan["cities"] and fl.get("city") not in plan["cities"]:
                err(f"preset {p['id']}: evidence {i} city {fl.get('city')}")
            if plan.get("activities") and fl.get("activity") not in plan["activities"]:
                err(f"preset {p['id']}: evidence {i} activity {fl.get('activity')}")
            if plan.get("events") and fl.get("event") not in plan["events"]:
                err(f"preset {p['id']}: evidence {i} event {fl.get('event')}")
            if plan["types"] and fl.get("type") not in plan["types"]:
                err(f"preset {p['id']}: evidence {i} type {fl.get('type')}")
            d = r["date"][:10]
            if plan["date_from"] and d < plan["date_from"] or plan["date_to"] and d > plan["date_to"]:
                err(f"preset {p['id']}: evidence {i} date {d} outside plan window")
    print(f"[validate] presets: {len(presets)} ({', '.join(ids)})")


def retrieval_report(data: dict[str, list[dict]]) -> None:
    """Informational: plan filters + query embedding -> where the evidence lands in the ranking."""
    from demo.embed import Embedder

    emb = Embedder()
    presets = json.loads((OUT / "presets.json").read_text("utf-8"))
    for p in presets:
        recs = data[p["kind"]]
        vecs = np.fromfile(OUT / f"embeddings_{p['kind']}.f32", dtype="<f4").reshape(-1, 512)
        plan = p["plan"]
        keep = []
        for i, r in enumerate(recs):
            f = r["fields"]
            d = r["date"][:10]
            if plan["countries"] and f.get("country") not in plan["countries"]:
                continue
            if plan["cities"] and f.get("city") not in plan["cities"]:
                continue
            if plan["types"] and f.get("type") not in plan["types"]:
                continue
            if plan.get("activities") and f.get("activity") not in plan["activities"]:
                continue
            if plan.get("events") and f.get("event") not in plan["events"]:
                continue
            if plan["date_from"] and d < plan["date_from"] or plan["date_to"] and d > plan["date_to"]:
                continue
            keep.append(i)
        q = emb.encode_query(p["question"])
        order = sorted(keep, key=lambda i: -float(vecs[i] @ q))
        top = [recs[i]["id"] for i in order[:10]]
        ev = set(p["evidence_ids"])
        hit = len(ev & set(top))
        print(f"[validate] retrieval {p['id']}: {len(keep)} candidates after filters, "
              f"{hit}/{min(len(ev), 10)} evidence in top-10; top-3: "
              + " | ".join(recs[i]["title"] for i in order[:3]))


def check_sizes_and_stats(data: dict[str, list[dict]]) -> None:
    for p in OUT.rglob("*"):
        if p.is_file() and p.stat().st_size > 90_000_000:
            err(f"{p}: larger than 90 MB")
        if p.is_file() and p.name.startswith("._"):
            err(f"{p}: macOS sidecar file")
    stats = json.loads((OUT / "stats.json").read_text("utf-8"))
    for kind, recs in data.items():
        k = stats["kinds"][kind]
        if k["records"] != len(recs) or k["jsonl_bytes"] != (OUT / f"{kind}.jsonl").stat().st_size:
            err(f"stats: {kind} counts/bytes out of date")
        if k["date_range"] != [min(r["date"] for r in recs), max(r["date"] for r in recs)]:
            err(f"stats: {kind} date range out of date")


def main() -> None:
    sample = "--no-embed-check" not in sys.argv
    data = {k: read_jsonl(OUT / f"{k}.jsonl") for k in ("photo", "sport", "car")}
    for k, recs in data.items():
        check_common(k, recs)
    check_photos(data["photo"])
    check_sport(data["sport"])
    check_car(data["car"])
    for k, recs in data.items():
        check_embeddings(k, recs, sample)
    check_presets(data)
    check_sizes_and_stats(data)
    if sample and not ERR:
        retrieval_report(data)
    for w in WARN:
        print("[validate] WARN", w)
    if ERR:
        for e in ERR[:60]:
            print("[validate] ERROR", e)
        print(f"[validate] {len(ERR)} errors")
        sys.exit(1)
    print("[validate] OK")


if __name__ == "__main__":
    main()
