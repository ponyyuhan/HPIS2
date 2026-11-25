"""Preset questions for the device data -> demo/data/devices/presets.json, plus stats.json.

    python -m demo.corpus.devices.presets

Gold facts are read off the generated records, so they are true by construction;
validate.py checks that every fact's ``match`` string occurs in an evidence record.
Schema = demo/data/presets.json (+ ``kind`` and ``device``); ``evidence_ids`` is given
both at the top level (PLAN-v3) and inside ``gold`` (existing preset schema).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter

from demo.corpus.devices.common import AS_OF, OUT, cn_md, read_jsonl, write_json

D = dt.date.fromisoformat


def _plan(kind, semantic, date_from=None, date_to=None, **kw) -> dict:
    p = {"kind": kind, "countries": [], "cities": [], "trips": [], "types": [], "activities": [], "events": [],
         "date_from": date_from, "date_to": date_to, "semantic": semantic}
    p.update(kw)
    return p


def _preset(pid, chip, kind, device, question, plan, facts, evidence) -> dict:
    ev = list(evidence)
    return {"id": pid, "chip": chip, "kind": kind, "device": device, "question": question, "plan": plan,
            "gold": {"facts": facts, "evidence_ids": ev}, "evidence_ids": ev, "distractor_ids": []}


def _fact(label, value, *match) -> dict:
    return {"label": label, "value": value, "match": list(match)}


def _span(recs) -> tuple[dt.date, dt.date]:
    ds = sorted(D(r["date"][:10]) for r in recs)
    return ds[0], ds[-1]


def _cn_range(a: dt.date, b: dt.date) -> str:
    return f"{a.year}年{a.month}月{a.day}日—{b.month}月{b.day}日" if a.year == b.year else f"{a}—{b}"


def photo_presets(photos: list[dict]) -> list[dict]:
    out = []
    de = [r for r in photos if r["fields"]["country"] == "德国"]
    a, b = _span(de)
    cities = list(dict.fromkeys(r["fields"]["city"] for r in de))
    counts = Counter(r["fields"]["city"] for r in de)
    facts = [_fact("时间", f"{_cn_range(a, b)}（德国出差）", f"{a.year}年{a.month}月", f"{a.month}月{a.day}日")]
    facts += [_fact(c, f"{c}：{counts[c]} 张", c) for c in cities]
    places = Counter(r["place"]["name"] for r in de)
    top = [p for p, _ in places.most_common() if p not in cities][:2]
    facts += [_fact("地点", p, p) for p in top]
    out.append(_preset(
        "photo_germany", "德国出差照片", "photo", "phone", "整理我在德国出差时拍的照片",
        _plan("photo", "德国 出差 照片 慕尼黑 柏林 法兰克福", countries=["德国"]), facts, [r["id"] for r in de]))

    kr = [r for r in photos if r["fields"]["country"] == "韩国" and r["fields"]["year"] == "2024"]
    a, b = _span(kr)
    counts = Counter(r["fields"]["city"] for r in kr)
    facts = [_fact("时间", f"{_cn_range(a, b)}（韩国旅游）", f"{a.month}月{a.day}日", f"{a.year}年{a.month}月")]
    facts += [_fact(c, f"{c}：{counts[c]} 张", c) for c in counts]
    places = Counter(r["place"]["name"] for r in kr)
    facts += [_fact("地点", p, p) for p, _ in places.most_common(2)]
    out.append(_preset(
        "photo_korea", "韩国旅游照片", "photo", "phone", "给我 2024 年去韩国旅游的照片",
        _plan("photo", "2024年 韩国 旅游 照片 首尔 釜山", "2024-01-01", "2024-12-31", countries=["韩国"]),
        facts, [r["id"] for r in kr]))

    night = [r for r in photos if r["fields"]["city"] == "首尔" and r["fields"]["type"] == "夜景"]
    if len(night) >= 2:
        facts = [_fact("时间", f"{_cn_range(*_span(night))}", "2024年7月", "7月")]
        facts += [_fact("地点", p, p) for p in dict.fromkeys(r["place"]["name"] for r in night)]
        out.append(_preset(
            "photo_seoul_night", "首尔夜景", "photo", "phone", "找出我在首尔拍的夜景照片",
            _plan("photo", "首尔 夜景 夜晚 灯光 照片", countries=["韩国"], cities=["首尔"], types=["夜景"]),
            facts, [r["id"] for r in night]))

    jp = [r for r in photos if r["fields"]["country"] == "日本" and "樱花" in (r["caption"] + " ".join(r["tags"]))]
    if len(jp) >= 3:
        a, b = _span(jp)
        cities = list(dict.fromkeys(r["fields"]["city"] for r in jp))
        facts = [_fact("时间", f"{_cn_range(a, b)}", "2025年4月", "4月")]
        facts += [_fact(c, c, c) for c in cities]
        out.append(_preset(
            "photo_sakura", "日本赏樱", "photo", "phone", "去年春天去日本看樱花，拍了哪些照片？",
            _plan("photo", "日本 樱花 赏樱 照片 东京 京都", "2025-01-01", "2025-12-31", countries=["日本"]),
            facts, [r["id"] for r in jp]))
    return out


def sport_presets(sport: list[dict]) -> list[dict]:
    out = []
    lo = AS_OF.date() - dt.timedelta(days=31)
    window = [r for r in sport if r["activity"] in ("周报", "月报") and D(r["date"][:10]) >= lo]
    month_now = [r for r in window if r["activity"] == "月报" and r["detail"]["partial"]][0]
    month_prev = [r for r in window if r["activity"] == "月报" and not r["detail"]["partial"]][-1]
    m, p = month_now["metrics"], month_prev["metrics"]
    facts = [
        _fact("本月跑步", f"11月1日—20日跑步 {m['runs']} 次共 {m['run_km']:.2f} 公里", f"{m['run_km']:.2f}",
              f"{m['run_km']:.1f}"),
        _fact("上月跑步", f"10月跑步 {p['runs']} 次共 {p['run_km']:.2f} 公里", f"{p['run_km']:.2f}", f"{p['run_km']:.1f}"),
        _fact("锻炼目标", f"11月周均中高强度锻炼 {m['exercise_min_per_week']} 分钟，达到每周≥150分钟目标",
              str(m["exercise_min_per_week"]), "150"),
        _fact("静息心率", f"11月平均静息心率 {m['resting_hr_avg']} 次/分", f"静息心率 {m['resting_hr_avg']}",
              f"静息心率{m['resting_hr_avg']}", f"{m['resting_hr_avg']} 次/分", f"{m['resting_hr_avg']}次/分"),
    ]
    if "weight_kg" in m:
        facts.append(_fact("体重", f"最近一次体重 {m['weight_kg']:.1f} 千克", f"{m['weight_kg']:.1f}"))
    mt = re.search(r"本月最长跑步：(\d+月\d+日) (.+?) ([\d.]+) 公里", month_now["text"])
    if mt:
        facts.append(_fact("最长跑步", f"{mt.group(1)} {mt.group(2)} {mt.group(3)} 公里", mt.group(3)))
    out.append(_preset(
        "sport_month", "近一个月运动", "sport", "watch", "我最近一个月的运动情况怎么样？",
        _plan("sport", "最近一个月 运动 周报 月报 跑步 锻炼 汇总", lo.isoformat(), AS_OF.date().isoformat(),
              activities=["周报", "月报"]),
        facts, [r["id"] for r in window]))

    hm = [r for r in sport if r.get("subtype") == "半程马拉松" and r["date"].startswith("2026")]
    if hm:
        r = hm[0]
        mm = r["metrics"]
        net = r["title"].split("完赛 ")[1]
        pace = re.search(r"平均配速 (\S+)/公里", r["text"]).group(1)
        prev = [x for x in sport if x.get("subtype") == "半程马拉松" and x["date"].startswith("2024")]
        facts = [_fact("日期", "2026年4月19日 北京半程马拉松", "4月19日"),
                 _fact("净成绩", f"净成绩 {net}", net),
                 _fact("配速", f"平均配速 {pace}/公里", pace),
                 _fact("心率", f"平均心率 {mm['avg_hr']} 次/分", f"{mm['avg_hr']}")]
        ev = [r["id"]]
        if prev:
            pnet = prev[0]["title"].split("完赛 ")[1]
            facts.append(_fact("对比", f"2024年北京半马净成绩 {pnet}", pnet))
            ev.append(prev[0]["id"])
        out.append(_preset(
            "sport_halfmarathon", "半马成绩", "sport", "watch", "我今年北京半马跑了多长时间？和上次比怎么样？",
            _plan("sport", "北京半程马拉松 完赛 净成绩 配速", activities=["跑步"]), facts, ev))

    last_week = [r for r in sport if r["activity"] == "周报" and r["detail"]["week_start"] == "2026-11-09"]
    if last_week:
        w = last_week[0]
        nights = [r for r in sport if r["activity"] == "睡眠" and "2026-11-09" <= r["date"][:10] <= "2026-11-15"]
        worst = min(nights, key=lambda r: (r["metrics"]["sleep_min"], r["date"]))
        best = max(nights, key=lambda r: (r["metrics"]["sleep_min"], r["date"]))
        avg = re.search(r"平均睡眠 (\S+?)，", w["text"]).group(1)
        wmin = re.search(r"实际睡眠 (\S+?)（", worst["text"]).group(1)
        bmin = re.search(r"实际睡眠 (\S+?)（", best["text"]).group(1)
        facts = [_fact("平均睡眠", f"上周（11月9日—15日）平均睡眠 {avg}", avg),
                 _fact("最短一晚", f"{cn_md(D(worst['date'][:10]) - dt.timedelta(days=1))}夜间 {wmin}", wmin),
                 _fact("最长一晚", f"{cn_md(D(best['date'][:10]) - dt.timedelta(days=1))}夜间 {bmin}", bmin)]
        out.append(_preset(
            "sport_sleep", "上周睡眠", "sport", "watch", "我上周睡得怎么样？",
            _plan("sport", "上周 睡眠 时长 评分 深睡 周报", "2026-11-09", "2026-11-15", activities=["睡眠", "周报"]),
            facts, [w["id"]] + [r["id"] for r in nights]))
    return out


def car_presets(car: list[dict]) -> list[dict]:
    out = []
    oct_month = [r for r in car if r["event"] == "月报" and r["detail"]["month"] == "2026-10"][0]
    m = oct_month["metrics"]
    oct_recs = [r for r in car if r["date"].startswith("2026-10")]
    charges = [r for r in oct_recs if r["event"] == "充电"]
    assert len(charges) == m["charges"]
    facts = [_fact("充电次数", f"10月充电 {m['charges']} 次", f"{m['charges']} 次", f"{m['charges']}次")]
    seen = []
    for r in oct_recs:
        if r["event"] == "行程" and r["purpose"] == "周末出游" and r["to"] != "望京 嘉禾园小区":
            key = "北戴河" if r["to"].startswith("北戴河") else r["to"]
            if key not in seen:
                seen.append(key)
    for k in seen[:6]:
        dates = sorted({x["date"][5:10] for x in oct_recs if x["event"] == "行程" and k in x["to"]})
        when = "、".join(f"{int(d[:2])}月{int(d[3:])}日" for d in dates[:3])
        facts.append(_fact("目的地", f"{k}（{when}）", k))
    if any("大兴国际机场" in d for d in oct_month["detail"]["destinations"]):
        facts.append(_fact("目的地", "北京大兴国际机场（10月13日上海出差送机）", "大兴机场", "大兴国际机场"))
    facts.append(_fact("充电量", f"共充入 {m['charge_kwh']:.1f} kWh，费用 ¥{m['charge_cost_yuan']:.2f}",
                       f"{m['charge_kwh']:.1f}", f"{m['charge_cost_yuan']:.2f}"))
    weeks = [r for r in car if r["event"] == "周报" and "2026-10-01" <= r["detail"]["week_end"] <= "2026-10-31"]
    out.append(_preset(
        "car_month", "上月用车", "car", "car", "上个月我开车去过哪些地方，充了几次电？",
        _plan("car", "上个月 用车 月报 去过的地方 目的地 充电 次数", "2026-10-01", "2026-10-31", events=["月报", "周报"]),
        facts, [oct_month["id"]] + [r["id"] for r in weeks]))

    nov = [r for r in car if r["event"] == "月报" and r["detail"]["month"] == "2026-11"][0]
    n = nov["metrics"]
    nov_charges = [r for r in car if r["event"] == "充电" and r["date"].startswith("2026-11")]
    if nov_charges:
        facts = [_fact("充电次数", f"11月（截至20日）充电 {n['charges']} 次", f"{n['charges']} 次", f"{n['charges']}次"),
                 _fact("费用", f"充电费用 ¥{n['charge_cost_yuan']:.2f}", f"{n['charge_cost_yuan']:.2f}"),
                 _fact("电量", f"共充入 {n['charge_kwh']:.1f} kWh", f"{n['charge_kwh']:.1f}")]
        types = Counter(r["station_type"] for r in nov_charges)
        for k in types:
            facts.append(_fact("方式", f"{k} {types[k]} 次", k))
        out.append(_preset(
            "car_charging", "本月充电", "car", "car", "这个月给车充了几次电？一共花了多少钱？",
            _plan("car", "本月 充电 次数 充电费用 电量", "2026-11-01", AS_OF.date().isoformat(), events=["充电", "月报"]),
            facts, [nov["id"]] + [r["id"] for r in nov_charges]))
    return out


def build_presets() -> list[dict]:
    photos = read_jsonl(OUT / "photo.jsonl")
    sport = read_jsonl(OUT / "sport.jsonl")
    car = read_jsonl(OUT / "car.jsonl")
    return photo_presets(photos) + sport_presets(sport) + car_presets(car)


def build_stats() -> dict:
    out = {"as_of": AS_OF.isoformat(), "persona": "林舟（虚构）", "kinds": {}}
    for kind, device in (("photo", "phone"), ("sport", "watch"), ("car", "car")):
        recs = read_jsonl(OUT / f"{kind}.jsonl")
        jsonl_bytes = (OUT / f"{kind}.jsonl").stat().st_size
        emb = OUT / f"embeddings_{kind}.f32"
        k = {"device": device, "records": len(recs), "jsonl_bytes": jsonl_bytes,
             "embedding_bytes": emb.stat().st_size if emb.exists() else 0,
             "date_range": [min(r["date"] for r in recs), max(r["date"] for r in recs)]}
        if kind == "photo":
            img = sum(p.stat().st_size for p in (OUT / "photos" / "img").glob("*.jpg"))
            th = sum(p.stat().st_size for p in (OUT / "photos" / "thumb").glob("*.jpg"))
            k.update(image_bytes=img, thumb_bytes=th, by_trip=dict(Counter(r["fields"]["trip"] for r in recs)),
                     by_country=dict(Counter(r["fields"]["country"] for r in recs)),
                     by_city=dict(Counter(r["fields"]["city"] for r in recs)),
                     by_type=dict(Counter(r["fields"]["type"] for r in recs)))
            k["total_bytes"] = jsonl_bytes + img + th
        elif kind == "sport":
            k["by_activity"] = dict(Counter(r["activity"] for r in recs))
            k["total_bytes"] = jsonl_bytes
        else:
            k["by_event"] = dict(Counter(r["event"] for r in recs))
            k["by_city"] = dict(Counter(r["fields"]["city"] for r in recs))
            k["total_bytes"] = jsonl_bytes
        k["by_year"] = dict(sorted(Counter(r["fields"]["year"] for r in recs).items()))
        out["kinds"][kind] = k
    return out


def llm_cost() -> dict:
    """Caption cost from the response cache; falls back to the previous stats.json on other machines."""
    from demo.corpus.devices.photo_caption import MODEL, total_cost

    n, usd = total_cost(MODEL)
    if n:
        return {"model": MODEL, "purpose": "photo captions and scene labels (all candidates)", "calls": n,
                "usd": round(usd, 4)}
    prev = OUT / "stats.json"
    if prev.exists():
        import json

        return json.loads(prev.read_text("utf-8")).get("llm", {})
    return {}


def main() -> None:
    presets = build_presets()
    write_json(OUT / "presets.json", presets)
    stats = build_stats()
    stats["llm"] = llm_cost()
    write_json(OUT / "stats.json", stats)
    for p in presets:
        print(f"[presets] {p['id']}: {len(p['gold']['facts'])} facts, {len(p['evidence_ids'])} evidence")


if __name__ == "__main__":
    main()
