"""Small device datasets in the PLAN-v3 §2 schema (photo / sport / car), standing in for
``demo/data/devices/`` until the real data is merged.

    make_devices(folder, dim=64)            # tests: random vectors, tiny fake JPEGs
    make_devices(folder, embed=fn, images=fn)  # rehearsal: real vectors and pictures

Everything is deterministic.  "Today" is 2026-11-20 (config.AS_OF).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np

CN = dt.timezone(dt.timedelta(hours=8))

# (trip, country, city, tz hours, start date, [(place, type, caption, tags, lat, lon)])
PHOTO_TRIPS = [
    ("德国出差", "德国", "慕尼黑", 2, "2025-09-14", [
        ("玛利亚广场", "街景", "新市政厅的哥特式钟楼下，广场上人群在等报时钟表演。", ["广场", "钟楼", "人群"], 48.1374, 11.5755),
        ("宝马博物馆", "建筑", "银色碗状的宝马博物馆和四缸大楼，天空晴朗。", ["博物馆", "汽车", "现代建筑"], 48.1769, 11.5560),
        ("皇家啤酒屋", "美食", "木桌上摆着一升装啤酒、烤猪肘和椒盐卷饼。", ["啤酒", "猪肘", "餐厅"], 48.1376, 11.5797),
        ("英国花园", "风景", "英国花园的湖边草地，秋天的树叶开始变黄。", ["公园", "湖", "秋天"], 48.1642, 11.6055),
    ]),
    ("德国出差", "德国", "法兰克福", 2, "2025-09-17", [
        ("罗马广场", "街景", "罗马广场上半木结构的老房子，正面是阶梯形山墙。", ["老城", "半木结构"], 50.1106, 8.6821),
        ("美因河畔", "夜景", "夜里从铁桥上看美因河和对岸亮灯的金融区高楼。", ["夜景", "河", "高楼"], 50.1074, 8.6836),
        ("法兰克福展览中心", "会议", "展会大厅里的工业软件展台，屏幕上是物流调度系统。", ["展会", "展台"], 50.1119, 8.6497),
    ]),
    ("德国出差", "德国", "柏林", 2, "2025-09-19", [
        ("勃兰登堡门", "建筑", "勃兰登堡门的砂岩立柱和顶部的四马战车雕像。", ["地标", "雕像"], 52.5163, 13.3777),
        ("东边画廊", "街景", "柏林墙遗址上色彩鲜艳的大幅涂鸦，游客在合影。", ["涂鸦", "柏林墙"], 52.5050, 13.4397),
        ("博物馆岛", "建筑", "施普雷河边的博物馆岛，柏林大教堂的绿色穹顶。", ["教堂", "河"], 52.5194, 13.4013),
    ]),
    ("韩国旅游", "韩国", "首尔", 9, "2024-07-26", [
        ("景福宫", "建筑", "景福宫勤政殿前的广场，穿韩服的游客在拍照。", ["宫殿", "韩服"], 37.5796, 126.9770),
        ("明洞", "美食", "明洞夜市摊位上的炒年糕、鱼饼串和鸡蛋面包。", ["小吃", "夜市"], 37.5636, 126.9850),
        ("南山首尔塔", "夜景", "南山上的首尔塔亮着灯，远处是城市夜景。", ["塔", "夜景"], 37.5512, 126.9882),
    ]),
    ("韩国旅游", "韩国", "釜山", 9, "2024-07-29", [
        ("海云台海水浴场", "风景", "海云台沙滩上的遮阳伞和远处的高楼，海水很蓝。", ["海滩", "大海"], 35.1587, 129.1604),
        ("甘川洞文化村", "街景", "山坡上彩色的小房子层层叠叠，巷子里有小王子雕塑。", ["彩色房子", "巷子"], 35.0975, 129.0106),
    ]),
    ("韩国旅游", "韩国", "济州", 9, "2024-07-31", [
        ("城山日出峰", "风景", "清晨的城山日出峰，火山口边缘被阳光照亮。", ["日出", "火山"], 33.4581, 126.9425),
        ("汉拿山", "风景", "汉拿山步道上的木栈道和低矮的灌木，云雾缭绕。", ["登山", "云雾"], 33.3617, 126.5292),
        ("济州黑猪肉餐厅", "美食", "炭火上烤着厚切黑猪肉，旁边是生菜和泡菜。", ["烤肉", "黑猪"], 33.5104, 126.5219),
    ]),
    ("京都家庭旅行", "日本", "京都", 9, "2025-04-03", [
        ("清水寺", "建筑", "清水寺的木结构舞台，周围的樱花正在盛开。", ["寺庙", "樱花"], 34.9949, 135.7850),
        ("岚山竹林", "风景", "岚山竹林小径两侧是高高的竹子，光线从顶上透下来。", ["竹林", "小路"], 35.0170, 135.6713),
    ]),
    ("京都家庭旅行", "日本", "东京", 9, "2025-04-06", [
        ("浅草寺", "建筑", "浅草寺雷门的大红灯笼，下面挤满游客。", ["寺庙", "灯笼"], 35.7148, 139.7967),
        ("涩谷十字路口", "街景", "涩谷十字路口绿灯时行人从四面八方穿过马路。", ["路口", "人群"], 35.6595, 139.7005),
    ]),
    ("国庆杭州", "中国", "杭州", 8, "2026-10-02", [
        ("西湖断桥", "风景", "断桥边的西湖，湖面上有游船，远处是保俶塔。", ["湖", "桥", "游船"], 30.2590, 120.1500),
        ("河坊街", "美食", "河坊街小吃店的葱包桧和定胜糕。", ["小吃", "老街"], 30.2420, 120.1690),
    ]),
    ("", "中国", "北京", 8, "2026-06-13", [
        ("奥林匹克森林公园", "风景", "奥森公园的跑道旁，夏天的树林很茂密。", ["公园", "跑道"], 40.0170, 116.3920),
        ("家", "人像", "一家三口在客厅的合影，孩子拿着奖状。", ["家人", "合影", "奖状"], 39.9960, 116.4810),
        ("国贸", "夜景", "国贸桥上车流的光轨和中国尊的夜景。", ["夜景", "车流"], 39.9087, 116.4605),
        ("育才实验小学", "人像", "学校运动会上孩子在跑道上冲刺。", ["运动会", "孩子"], 39.9950, 116.4700),
    ]),
]

SPORT_START = dt.date(2026, 8, 1)
TODAY = dt.date(2026, 11, 20)


def _vector(text: str, dim: int, topic: int = 0) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")
    rng = np.random.default_rng(seed)
    base = np.random.default_rng(1000 + topic).standard_normal(dim)
    v = base + 0.8 * rng.standard_normal(dim)
    return (v / np.linalg.norm(v)).astype(np.float32)


def fake_jpeg(label: str, size: int = 2048) -> bytes:
    body = hashlib.sha256(label.encode()).digest() * (size // 32)
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + body + b"\xff\xd9"


def photo_records() -> list[dict]:
    out = []
    for trip, country, city, tz, start, shots in PHOTO_TRIPS:
        day = dt.date.fromisoformat(start)
        for j, (place, kind, caption, tags, lat, lon) in enumerate(shots):
            rid = len(out) + 1
            when = dt.datetime(day.year, day.month, day.day, 9 + 2 * j, 10 + 7 * j, tzinfo=dt.timezone(dt.timedelta(hours=tz)))
            when += dt.timedelta(days=j // 2)
            title = f"{city} · {place}"
            text = f"{caption} 拍摄于{country}{city}{place}，{when:%Y年%m月%d日 %H:%M}。"
            out.append(
                {
                    "id": rid,
                    "kind": "photo",
                    "device": "phone",
                    "date": when.isoformat(),
                    "title": title,
                    "text": text,
                    "fields": {
                        "year": f"{when:%Y}", "month": f"{when:%Y-%m}", "country": country, "city": city,
                        "type": kind, **({"trip": trip} if trip else {}),
                    },
                    "file": f"photos/img/{rid}.jpg",
                    "thumb": f"photos/thumb/{rid}.jpg",
                    "width": 960,
                    "height": 720,
                    "bytes": 0,
                    "place": {"name": place, "lat": lat, "lon": lon, "country": country, "city": city},
                    "caption": caption,
                    "tags": tags,
                    "source": {"dataset": "fixture", "id": str(rid), "photographer": "fixture", "url": ""},
                }
            )
    return out


def sport_records() -> list[dict]:
    out = []

    def add(day: dt.date, hour: int, activity: str, title: str, text: str, metrics: dict):
        when = dt.datetime(day.year, day.month, day.day, hour, 5, tzinfo=CN)
        out.append(
            {
                "id": len(out) + 1,
                "kind": "sport",
                "device": "watch",
                "date": when.isoformat(),
                "title": title,
                "text": text,
                "fields": {"year": f"{day:%Y}", "month": f"{day:%Y-%m}", "activity": activity},
                "activity": activity,
                "metrics": metrics,
            }
        )

    day = SPORT_START
    week_km, week_runs, month_km, month_runs, month_rides = 0.0, 0, 0.0, 0, 0
    while day <= TODAY:
        n = (day - SPORT_START).days
        if day.day == 1 and n:
            prev = day - dt.timedelta(days=1)
            add(day, 8, "月报", f"{prev:%Y年%m月}运动月报",
                f"{prev:%m月}共跑步{month_runs}次，累计{month_km:.1f}公里；骑行{month_rides}次；平均每晚睡眠7小时12分。",
                {"runs": month_runs, "run_km": round(month_km, 1), "rides": month_rides})
            month_km, month_runs, month_rides = 0.0, 0, 0
        if day.weekday() == 0 and n:
            add(day, 8, "周报", f"{day:%m月%d日}运动周报",
                f"上周跑步{week_runs}次，共{week_km:.1f}公里，力量训练2次，平均静息心率58次/分。",
                {"runs": week_runs, "run_km": round(week_km, 1), "resting_hr": 58})
            week_km, week_runs = 0.0, 0
        if day.weekday() in (1, 3, 5):
            km = round(5 + (n % 5) * 0.8, 1)
            pace = f"5'{30 + n % 20:02d}\""
            add(day, 7, "跑步", f"晨跑 {km} 公里", f"{day:%m月%d日}早上在奥森公园晨跑{km}公里，配速{pace}，平均心率{148 + n % 9}。",
                {"distance_km": km, "pace": pace, "avg_hr": 148 + n % 9})
            week_km += km
            month_km += km
            week_runs += 1
            month_runs += 1
        if day.weekday() == 6:
            km = 30 + n % 15
            add(day, 9, "骑行", f"骑行 {km} 公里", f"{day:%m月%d日}沿温榆河骑行{km}公里，用时{90 + n % 30}分钟。",
                {"distance_km": km, "duration_min": 90 + n % 30})
            month_rides += 1
        if day.weekday() in (0, 4):
            add(day, 20, "力量训练", "力量训练 45 分钟", f"{day:%m月%d日}晚上在健身房做力量训练45分钟，消耗{310 + n % 40}千卡。",
                {"duration_min": 45, "kcal": 310 + n % 40})
        if day.weekday() == 2 and (n // 7) % 2 == 0:
            add(day, 19, "游泳", "游泳 1500 米", f"{day:%m月%d日}游泳1500米，用时38分钟。", {"distance_m": 1500, "duration_min": 38})
        if day.weekday() == 5 and (n // 7) % 3 == 1:
            add(day, 15, "羽毛球", "羽毛球 1 小时", f"{day:%m月%d日}和同事打羽毛球1小时。", {"duration_min": 60})
        if n % 3 == 0:
            hours = 6 + (n % 4) * 0.5
            add(day, 6, "睡眠", f"睡眠 {hours} 小时", f"{day:%m月%d日}夜间睡眠{hours}小时，深睡{80 + n % 30}分钟。",
                {"sleep_h": hours, "deep_min": 80 + n % 30})
        if n % 5 == 0:
            add(day, 22, "日常", "日常数据", f"{day:%m月%d日}步数{8000 + n * 37 % 5000}步，静息心率{56 + n % 5}。",
                {"steps": 8000 + n * 37 % 5000, "resting_hr": 56 + n % 5})
        day += dt.timedelta(days=1)
    return out


CAR_PLACES = [("望京家", "北京"), ("中关村软件园", "北京"), ("怀柔雁栖湖", "北京"), ("天津滨海", "天津"),
              ("雄安市民服务中心", "保定"), ("北戴河", "秦皇岛"), ("首都机场", "北京")]


def car_records() -> list[dict]:
    out = []

    def add(when: dt.datetime, event: str, title: str, text: str, city: str, **extra):
        out.append(
            {
                "id": len(out) + 1,
                "kind": "car",
                "device": "car",
                "date": when.isoformat(),
                "title": title,
                "text": text,
                "fields": {"year": f"{when:%Y}", "month": f"{when:%Y-%m}", "event": event, "city": city},
                "event": event,
                **extra,
            }
        )

    day = SPORT_START
    month_trips = month_km = month_charges = 0
    week_km = week_charges = 0
    places: list[str] = []
    while day <= TODAY:
        n = (day - SPORT_START).days
        if day.day == 1 and n:
            prev = day - dt.timedelta(days=1)
            where = "、".join(dict.fromkeys(places)) or "无"
            add(dt.datetime(day.year, day.month, day.day, 8, tzinfo=CN), "月报", f"{prev:%Y年%m月}用车月报",
                f"{prev:%m月}共行驶{month_km}公里，行程{month_trips}次，充电{month_charges}次；去过：{where}。", "北京",
                metrics={"km": month_km, "trips": month_trips, "charges": month_charges})
            month_trips = month_km = month_charges = 0
            places = []
        if day.weekday() == 0 and n:
            add(dt.datetime(day.year, day.month, day.day, 8, 5, tzinfo=CN), "周报", f"{day:%m月%d日}用车周报",
                f"上周共行驶{week_km}公里，充电{week_charges}次。", "北京", metrics={"km": week_km, "charges": week_charges})
            week_km = week_charges = 0
        if day.weekday() < 5:
            add(dt.datetime(day.year, day.month, day.day, 8, 20, tzinfo=CN), "行程", "望京家 → 中关村软件园",
                f"{day:%m月%d日}从望京家开车到中关村软件园，28公里，用时52分钟。", "北京",
                **{"from": "望京家", "to": "中关村软件园", "distance_km": 28, "duration_min": 52, "energy_kwh": 4.6})
            month_trips += 1
            month_km += 28
            week_km += 28
            places.append("中关村软件园")
        if day.weekday() == 5 and (n // 7) % 2 == 1:
            dest, city = CAR_PLACES[2 + (n // 14) % 5]
            km = 60 + ((n // 14) % 4) * 45
            add(dt.datetime(day.year, day.month, day.day, 9, 30, tzinfo=CN), "行程", f"望京家 → {dest}",
                f"{day:%m月%d日}周末自驾从望京家到{dest}，{km}公里。", city,
                **{"from": "望京家", "to": dest, "distance_km": km, "duration_min": km, "energy_kwh": round(km * 0.17, 1)})
            month_trips += 1
            month_km += km
            week_km += km
            places.append(dest)
        if day.weekday() in (2, 6):
            add(dt.datetime(day.year, day.month, day.day, 21, tzinfo=CN), "充电", "家用充电桩充电",
                f"{day:%m月%d日}晚上在小区充电桩充电，从32%充到90%，充入{38 + n % 6}千瓦时。", "北京",
                energy_kwh=38 + n % 6)
            month_charges += 1
            week_charges += 1
        if n % 30 == 9:
            add(dt.datetime(day.year, day.month, day.day, 10, tzinfo=CN), "保养", "常规保养",
                f"{day:%m月%d日}在4S店做常规保养，更换空调滤芯，费用680元。", "北京")
        if n % 23 == 4:
            add(dt.datetime(day.year, day.month, day.day, 7, 50, tzinfo=CN), "提醒", "胎压提醒",
                f"{day:%m月%d日}左后轮胎压偏低（2.1 bar），已提醒补气。", "北京")
        if n % 17 == 3:
            add(dt.datetime(day.year, day.month, day.day, 18, tzinfo=CN), "导航", "导航到首都机场",
                f"{day:%m月%d日}导航从中关村软件园到首都机场T3，预计55分钟。", "北京")
        day += dt.timedelta(days=1)
    return out


PRESETS = [
    {"id": "photo_germany", "chip": "德国出差照片", "kind": "photo", "device": "phone", "question": "整理我在德国出差时拍的照片",
     "plan": {"kind": "photo", "countries": ["德国"], "cities": [], "trips": [], "types": [], "date_from": None,
              "date_to": None, "semantic": "德国 出差 照片"},
     "gold": {"facts": [{"label": "城市", "value": "慕尼黑、法兰克福、柏林", "match": ["慕尼黑"]}]}},
    {"id": "photo_korea", "chip": "韩国旅游照片", "kind": "photo", "device": "phone", "question": "给我 2024 年去韩国旅游的照片",
     "plan": {"kind": "photo", "countries": ["韩国"], "cities": [], "trips": [], "types": [], "date_from": "2024-01-01",
              "date_to": "2024-12-31", "semantic": "2024 韩国 旅游 照片"},
     "gold": {"facts": [{"label": "城市", "value": "首尔、釜山、济州", "match": ["首尔"]}]}},
    {"id": "sport_month", "chip": "近一个月运动", "kind": "sport", "device": "watch", "question": "我最近一个月的运动情况怎么样？",
     "plan": {"kind": "sport", "activities": [], "date_from": "2026-10-20", "date_to": "2026-11-20",
              "semantic": "最近一个月 运动 情况 月报 周报 跑步"},
     "gold": {"facts": [{"label": "跑步", "value": "每周三次晨跑", "match": ["跑步"]}]}},
    {"id": "car_month", "chip": "上月用车", "kind": "car", "device": "car", "question": "上个月我开车去过哪些地方，充了几次电？",
     "plan": {"kind": "car", "events": [], "cities": [], "date_from": "2026-10-01", "date_to": "2026-10-31",
              "semantic": "上个月 开车 去过的地方 行程 充电 次数 月报"},
     "gold": {"facts": [{"label": "充电", "value": "10月月报", "match": ["充电"]}]}},
]


def make_devices(folder: Path, *, dim: int = 64, embed=None, images=None) -> dict:
    """Write the device folder.  ``embed(texts) -> [n, dim]`` (default: deterministic random
    vectors), ``images(record) -> (full_jpeg, thumb_jpeg)`` (default: tiny fake JPEGs)."""
    folder = Path(folder)
    (folder / "photos" / "img").mkdir(parents=True, exist_ok=True)
    (folder / "photos" / "thumb").mkdir(parents=True, exist_ok=True)
    data = {"photo": photo_records(), "sport": sport_records(), "car": car_records()}
    for rec in data["photo"]:
        full, thumb = images(rec) if images else (fake_jpeg(f"full{rec['id']}"), fake_jpeg(f"thumb{rec['id']}", 512))
        (folder / rec["file"]).write_bytes(full)
        (folder / rec["thumb"]).write_bytes(thumb)
        rec["bytes"] = len(full)
    topics = {"photo": 1, "sport": 2, "car": 3}
    stats = {}
    for kind, rows in data.items():
        with open(folder / f"{kind}.jsonl", "w", encoding="utf-8") as fh:
            for rec in rows:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        texts = [rec["text"] for rec in rows]
        vecs = embed(texts) if embed else np.stack([_vector(t, dim, topics[kind]) for t in texts])
        np.asarray(vecs, dtype=np.float32).tofile(folder / f"embeddings_{kind}.f32")
        meta = {"model": "fixture" if embed is None else "BAAI/bge-small-zh-v1.5", "dim": int(vecs.shape[1]),
                "count": len(rows), "text": "text"}
        (folder / f"embeddings_{kind}.json").write_text(json.dumps(meta, ensure_ascii=False))
        dates = sorted(r["date"] for r in rows)
        stats[kind] = {"count": len(rows), "first": dates[0], "last": dates[-1]}
    presets = []
    for p in PRESETS:
        p = json.loads(json.dumps(p))
        p["evidence_ids"] = []
        presets.append(p)
    (folder / "presets.json").write_text(json.dumps(presets, ensure_ascii=False, indent=1))
    (folder / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    return data
