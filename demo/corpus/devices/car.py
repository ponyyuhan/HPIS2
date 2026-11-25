"""Car records (kind ``car``, device ``car``) for 林舟's 远途 T5 (battery EV), 2024-01-01 → 2026-11-20 18:00.

    python -m demo.corpus.devices.car            # writes demo/data/devices/car.jsonl

Events (``event``): 行程 充电 导航 保养 提醒 周报 月报. A small simulation keeps the
battery state of charge and the odometer consistent from record to record:
commutes 望京 ⇄ 中关村软件园, school runs, drives to the park for the runs planned
on the watch, weekend outings, airport parking for every flight in the mailbox
and on the new trips, home wallbox charging, public / highway fast charging.
Weekly and monthly reports are computed from the records (``aggregate``).
"""

from __future__ import annotations

import datetime as dt
import random
from collections import Counter, defaultdict
from dataclasses import dataclass

from demo.corpus.devices import life, sport
from demo.corpus.devices.common import (
    AS_OF, END, OUT, START, cn_date, cn_md, daterange, hm, iso, month_end, tz, week_start, weekday_cn, write_jsonl,
)
from demo.corpus.devices.sport import rnd

D = dt.date.fromisoformat
SEED = "linzhou-car-v1"
CN8 = tz(8)
P = life.P
CAP = life.BATTERY_KWH
ODO_START = 21356.0
SOC_START = 78.0

# office-based client sites: key -> (name, km from office, minutes, area)
CLIENTS = {
    "hanhai": ("瀚海物流 北京分公司（亦庄）", 44.8, 62, "大兴区"),
    "huachuan": ("华川制造 北京办事处（望京）", 26.9, 50, "朝阳区"),
    "beichen": ("北辰能源 总部（国贸）", 25.3, 52, "朝阳区"),
    "qinghe": ("青禾医疗 信息中心（中关村）", 9.6, 24, "海淀区"),
    "mingde": ("明德教育集团（学院路）", 12.8, 30, "海淀区"),
}
ROUTES = {
    "office": ["京承高速 → 北五环 → 后厂村路", "北四环 → 京藏高速 → 后厂村路", "北五环 → 上清桥 → 西北旺"],
    "pkx": ["京承高速 → 东四环 → 大兴机场高速", "机场北线 → 京台高速 → 大兴机场高速"],
    "pek": ["机场高速 → 机场二高速", "京密路 → 机场南线"],
    "mutianyu": ["京承高速 → 怀丰公路"], "hongluo": ["京承高速 → 范崎路"], "yanqi": ["京承高速 → 范崎路"],
    "badaling": ["京藏高速 → 八达岭出口"], "expo": ["京藏高速 → 延庆城区 → 世园公园"],
    "yeyahu": ["京藏高速 → 延康路"], "gubei": ["京承高速 → 司马台出口"],
    "tianjin": ["京津高速 → 天津外环 → 五大道"], "beidaihe": ["京哈高速 → 京秦高速 → 北戴河出口"],
    "universal": ["京通快速路 → 东六环"], "shougang": ["长安街 → 阜石路"], "xiangshan": ["北五环 → 香泉环岛"],
    "summer_palace": ["北四环 → 万泉河路"], "botanical": ["北五环 → 香泉环岛"],
}
FAR = {"mutianyu": "jingcheng", "hongluo": "jingcheng", "yanqi": "jingcheng", "gubei": "jingcheng",
       "badaling": "yanqing", "expo": "yanqing", "yeyahu": "yanqing", "tianjin": "jinghu", "beidaihe": "jingqin"}
CHARGERS = dict(life.CHARGERS)
CHARGERS["jingqin"] = ("京秦高速 玉田服务区快充站", "高速快充", 120.0, 1.62)
OTA = {D("2024-02-28"): "V4.6", D("2024-06-18"): "V4.8", D("2024-11-12"): "V5.0", D("2025-03-11"): "V5.2",
       D("2025-07-08"): "V5.4", D("2025-12-02"): "V5.6", D("2026-04-21"): "V6.0", D("2026-08-25"): "V6.2",
       D("2026-11-10"): "V6.3"}
OTA_NOTE = {"V4.6": "优化冬季续航估算", "V4.8": "新增哨兵模式", "V5.0": "导航支持充电站实时空闲状态",
            "V5.2": "优化自适应巡航跟车", "V5.4": "新增露营模式", "V5.6": "电池预热策略升级",
            "V6.0": "语音助手支持连续对话", "V6.2": "新增代客泊车记录", "V6.3": "优化低温充电速度"}
BASE_RATE = {1: 21.5, 2: 20.6, 3: 17.6, 4: 16.1, 5: 15.6, 6: 16.2, 7: 17.1, 8: 17.0, 9: 15.9, 10: 16.3, 11: 18.4,
             12: 21.1}
PARK_FEE = {"pkx": 60, "pek": 60}
RUN_PARKS = {"olympic": "olympic", "chaoyang": "chaoyang_park"}  # watch place key -> life.P key


def _rng(*parts) -> random.Random:
    return random.Random("|".join([SEED, *map(str, parts)]))


def place_name(key: str) -> str:
    if key in P:
        return P[key].name
    if key in CLIENTS:
        return CLIENTS[key][0]
    raise KeyError(key)


def place_city(key: str) -> str:
    return P[key].city if key in P else "北京"


def leg_km(a: str, b: str) -> tuple[float, int]:
    """Road distance (km) and free-flow minutes between two place keys."""
    if a == b:
        return 0.0, 0
    if "home" in (a, b):
        k = b if a == "home" else a
        if k in P:
            return P[k].km, P[k].mins
    if "office" in (a, b):
        k = b if a == "office" else a
        if k in CLIENTS:
            return CLIENTS[k][1], CLIENTS[k][2]
        if k == "school":
            return 26.4, 44
        if k == "checkup":
            return 24.1, 50
    if {a, b} in ({"beidaihe", "gezi"}, {"beidaihe", "laohushi"}):
        return (6.8, 16) if "gezi" in (a, b) else (3.9, 11)
    # fall back: via home
    ka, ma = leg_km("home", a)
    kb, mb = leg_km("home", b)
    return abs(ka - kb) + min(ka, kb) * 0.6, int(abs(ma - mb) + min(ma, mb) * 0.6)


@dataclass
class Leg:
    depart: dt.datetime
    a: str
    b: str
    purpose: str
    nav: bool = False
    note: str = ""
    km: float | None = None  # override distance (local drives at a destination)
    mins: int | None = None


class Car:
    def __init__(self):
        self.soc = SOC_START
        self.odo = ODO_START
        self.at = "home"
        self.recs: list[dict] = []
        self.low_warned: dt.date | None = None

    # ---------------------------------------------------------- records
    def _rec(self, t: dt.datetime, event: str, title: str, text: str, city: str, **kw) -> dict:
        rec = {"kind": "car", "device": "car", "date": iso(t), "title": title, "text": text,
               "fields": {"year": str(t.year), "month": f"{t.year}-{t.month:02d}", "event": event, "city": city},
               "event": event, "from": kw.pop("frm", None), "to": kw.pop("to", None),
               "distance_km": kw.pop("distance_km", None), "duration_min": kw.pop("duration_min", None),
               "energy_kwh": kw.pop("energy_kwh", None)}
        rec.update(kw)
        self.recs.append(rec)
        return rec

    # ---------------------------------------------------------- driving
    def drive(self, leg: Leg, rng: random.Random) -> dt.datetime:
        km0, mins0 = leg_km(leg.a, leg.b)
        if leg.km is not None:
            km0, mins0 = leg.km, leg.mins or int(leg.km * 2.2)
        if km0 <= 0:
            return leg.depart
        km = rnd(km0 * rng.uniform(0.97, 1.04), 1)
        h = leg.depart.hour + leg.depart.minute / 60
        weekday = leg.depart.date().weekday() < 5 and life.is_workday(leg.depart.date())
        if weekday and 7.2 <= h <= 9.6:
            traffic = rng.uniform(1.25, 1.6)
        elif weekday and 17.2 <= h <= 19.8:
            traffic = rng.uniform(1.3, 1.7)
        elif h < 6.5 or h >= 21:
            traffic = rng.uniform(0.8, 0.92)
        else:
            traffic = rng.uniform(0.95, 1.2)
        mins = max(3, int(round(mins0 * traffic * (km / km0))))
        m = leg.depart.month
        rate = BASE_RATE[m] * (1.12 if km > 45 else 1.0) * rng.uniform(0.94, 1.07)
        if km < 6 and m in (12, 1, 2):
            rate *= 1.22
        energy = rnd(km * rate / 100, 1)
        # en-route fast charge when the battery would get too low
        if self.soc - energy / CAP * 100 < 12 and km > 40 and leg.b in FAR:
            part = rnd(km * 0.55, 1)
            e1 = rnd(part * rate / 100, 1)
            t1 = leg.depart + dt.timedelta(minutes=int(mins * 0.55))
            self._trip_record(leg, leg.depart, t1, part, int(mins * 0.55), e1, via=CHARGERS[FAR[leg.b]][0])
            t2 = self.charge(t1 + dt.timedelta(minutes=3), FAR[leg.b], 80, rng, city=place_city(leg.b) if leg.b == "tianjin" else "北京")
            rest = rnd(km - part, 1)
            e2 = rnd(energy - e1, 1)
            mins2 = mins - int(mins * 0.55)
            leg2 = Leg(t2 + dt.timedelta(minutes=2), leg.a, leg.b, leg.purpose, note=leg.note)
            self._trip_record(leg2, leg2.depart, leg2.depart + dt.timedelta(minutes=mins2), rest, mins2, e2,
                              resumed=CHARGERS[FAR[leg.b]][0])
            self.at = leg.b
            return leg2.depart + dt.timedelta(minutes=mins2)
        arrive = leg.depart + dt.timedelta(minutes=mins)
        self._trip_record(leg, leg.depart, arrive, km, mins, energy)
        self.at = leg.b
        return arrive

    def _trip_record(self, leg: Leg, t0, t1, km, mins, energy, via=None, resumed=None):
        s0 = self.soc
        self.soc = max(3.0, self.soc - energy / CAP * 100)
        self.odo += km
        a, b = place_name(leg.a), place_name(leg.b)
        to = via or b
        frm = resumed or a
        speed = rnd(km / (mins / 60), 1) if mins else 0.0
        city = "北京"
        if not via:
            city = place_city(leg.b)
        elif leg.b == "tianjin":
            city = "北京"
        text = (f"{cn_date(t0.date())}（{weekday_cn(t0.date())}）{t0:%H:%M} 从{frm}出发，{t1:%H:%M} 到达{to}，"
                f"行驶 {km:.1f} 公里，用时 {hm(mins)}，平均车速 {speed:.1f} 公里/小时，耗电 {energy:.1f} kWh"
                f"（{energy / km * 100:.1f} kWh/百公里），电量 {rnd(s0)}% → {rnd(self.soc)}%，里程表 {self.odo:,.0f} 公里。")
        if via:
            text += f"途中在{via}补电。"
        if leg.note and not via:
            text += leg.note
        self._rec(t0, "行程", f"行程 · {frm} → {to} · {km:.1f} 公里", text, city,
                  frm=frm, to=to, distance_km=km, duration_min=mins, energy_kwh=energy,
                  purpose=leg.purpose, soc_start=rnd(s0), soc_end=rnd(self.soc), odometer_km=rnd(self.odo),
                  avg_speed_kmh=speed, arrive=f"{t1:%H:%M}")
        if self.soc < 20 and self.low_warned != t1.date():
            self.low_warned = t1.date()
            self._rec(t1 + dt.timedelta(minutes=1), "提醒", "提醒 · 电量偏低",
                      f"{cn_date(t1.date())} {t1:%H:%M} 车辆提醒：剩余电量 {rnd(self.soc)}%，预计续航约 "
                      f"{int(self.soc / 100 * CAP / 0.18)} 公里，请尽快充电。", city, kind_detail="电量偏低",
                      soc=rnd(self.soc))

    def navigate(self, leg: Leg, rng: random.Random):
        km0, mins0 = leg_km(leg.a, leg.b)
        route = rng.choice(ROUTES.get(leg.b, ROUTES.get(leg.a, ["城市道路"])))
        t = leg.depart - dt.timedelta(minutes=2)
        eta_m = int(mins0 * rng.uniform(1.0, 1.2))
        eta = t + dt.timedelta(minutes=eta_m + 2)
        a, b = place_name(leg.a), place_name(leg.b)
        text = (f"{cn_date(t.date())}（{weekday_cn(t.date())}）{t:%H:%M} 车机导航：从{a}前往{b}，全程约 {km0:.1f} 公里，"
                f"预计用时 {hm(eta_m)}，推荐路线 {route}，预计 {eta:%H:%M} 到达。")
        if km0 > 60:
            text += "导航已提示沿途充电站。"
        self._rec(t, "导航", f"导航 · 前往{b}", text, place_city(leg.b), frm=a, to=b, distance_km=rnd(km0, 1),
                  duration_min=eta_m, route=route)

    # ---------------------------------------------------------- charging
    def charge(self, t: dt.datetime, station: str, target: float, rng: random.Random, city: str = "北京") -> dt.datetime:
        name, kind, kw, price = CHARGERS[station]
        s0 = self.soc
        target = max(target, s0 + 5)
        target = min(target, 100.0)
        add = (target - s0) / 100 * CAP
        eff = 0.92 if kw <= 11 else 0.95
        energy = rnd(add / eff, 1)
        if kw <= 11:
            mins = int(round(add / (kw * 0.94) * 60))
        else:
            mins = int(round(add / (kw * rng.uniform(0.55, 0.7)) * 60)) + 3
        end = t + dt.timedelta(minutes=mins)
        cost = rnd(energy * price, 2)
        self.soc = target
        end_txt = f"{end:%H:%M}" if end.date() == t.date() else f"次日 {end:%H:%M}"
        text = (f"{cn_date(t.date())}（{weekday_cn(t.date())}）{t:%H:%M} 在{name}开始充电（{kind}，{kw:g} kW），"
                f"{end_txt} 结束，电量 {rnd(s0)}% → {rnd(target)}%，充入 {energy:.1f} kWh，用时 {hm(mins)}，"
                f"费用 ¥{cost:.2f}（{price:.2f} 元/kWh）。")
        self._rec(t, "充电", f"充电 · {name} · {energy:.1f} kWh", text, city, frm=None, to=name, distance_km=None,
                  duration_min=mins, energy_kwh=energy, station=name, station_type=kind, power_kw=kw,
                  soc_start=rnd(s0), soc_end=rnd(target), cost_yuan=cost, price_per_kwh=price,
                  odometer_km=rnd(self.odo))
        return end


# ---------------------------------------------------------------- day plans
def _t(d: dt.date, hhmm: str, rng: random.Random | None = None, jitter: int = 0) -> dt.datetime:
    h, m = life.hhmm(hhmm)
    t = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=CN8)
    if rng and jitter:
        t += dt.timedelta(minutes=rng.randint(-jitter, jitter))
    return t


def _workouts_by_day():
    workouts, _, _ = sport.plan()
    out = defaultdict(list)
    for x in workouts:
        if x.start.utcoffset() == dt.timedelta(hours=8):
            out[x.start.date()].append(x)
    return out


def plan_day(d: dt.date, wk: list, rng: random.Random) -> list[Leg]:
    """Drives for one day in Beijing (airport drives are added by simulate())."""
    legs: list[Leg] = []
    trip = life.trip_on(d)
    outing = life.outing_on(d)
    if trip or d in life.HALF_MARATHONS:
        return legs
    park_runs = [x for x in wk if x.key in RUN_PARKS and x.activity == "跑步"]
    evening = [x for x in wk if x.key in ("pool", "gym", "court") and x.start.hour >= 18]
    for x in park_runs:  # drives to the park for runs planned on the watch
        park = RUN_PARKS[x.key]
        legs.append(Leg(x.start - dt.timedelta(minutes=P[park].mins + 3), "home", park, "运动"))
        legs.append(Leg(x.end + dt.timedelta(minutes=rng.randint(6, 15)), park, "home", "运动"))

    def evening_legs():
        for x in evening:
            if rng.random() < 0.6:
                venue = "pool" if x.key in ("pool", "court") else "gym"
                legs.append(Leg(x.start - dt.timedelta(minutes=P[venue].mins + 4), "home", venue, "运动"))
                legs.append(Leg(x.end + dt.timedelta(minutes=rng.randint(8, 20)), venue, "home", "运动"))

    if outing:
        o = outing
        far = o.place in FAR
        if o.date == d:
            go = _t(d, "07:20" if far else "09:10", rng, 35)
            legs.append(Leg(go, "home", o.place, "周末出游", nav=o.place not in ("chaoyang_park", "olympic", "798"),
                            note=f"（{o.note}）" if o.note else ""))
            if o.nights == 0:
                back = _t(d, "16:10" if far else "15:20", rng, 50)
                legs.append(Leg(back, o.place, "home", "周末出游", nav=far))
        elif d == o.date + dt.timedelta(days=o.nights):
            if o.place == "beidaihe":
                t0 = _t(d, "08:40", rng, 15)
                legs.append(Leg(t0, "beidaihe", "gezi", "周末出游"))
                legs.append(Leg(t0 + dt.timedelta(minutes=rng.randint(95, 130)), "gezi", "beidaihe", "周末出游"))
            legs.append(Leg(_t(d, "14:20", rng, 40), o.place, "home", "周末出游", nav=True))
        elif o.place == "beidaihe":
            t0 = _t(d, "09:50", rng, 20)
            legs.append(Leg(t0, "beidaihe", "laohushi", "周末出游"))
            legs.append(Leg(t0 + dt.timedelta(minutes=rng.randint(120, 170)), "laohushi", "beidaihe", "周末出游"))
    elif life.is_workday(d):
        if rng.random() < 0.86:
            morning_run = any(x.start.hour < 8 for x in park_runs)
            if d in life.CHECKUPS:
                legs.append(Leg(_t(d, "07:20", rng, 5), "home", "checkup", "体检", nav=True,
                                note=f"（{life.CHECKUPS[d][1]}，空腹）"))
                legs.append(Leg(_t(d, "09:40", rng, 10), "checkup", "office", "通勤"))
            elif life.school_day(d) and rng.random() < 0.33 and not morning_run:
                legs.append(Leg(_t(d, "07:15", rng, 4), "home", "school", "接送孩子", note="（送林小禾上学）"))
                legs.append(Leg(_t(d, "07:30", rng, 3), "school", "office", "通勤"))
            else:
                legs.append(Leg(_t(d, "08:05", rng, 25), "home", "office", "通勤"))
            if rng.random() < 0.12:
                c = rng.choice(list(CLIENTS))
                t1 = _t(d, rng.choice(["10:00", "13:40", "14:10"]), rng, 15)
                legs.append(Leg(t1, "office", c, "客户拜访", nav=True))
                legs.append(Leg(t1 + dt.timedelta(minutes=CLIENTS[c][2] + rng.randint(80, 140)), c, "office",
                                "客户拜访"))
            back = _t(d, rng.choice(["18:20", "18:45", "19:10", "19:30", "20:15"]), rng, 15)
            if evening:
                back = min(back, evening[0].start - dt.timedelta(minutes=100))
            legs.append(Leg(back, "office", "home", "通勤"))
        evening_legs()
    else:
        if d in life.CAR_SERVICE:
            legs.append(Leg(_t(d, "09:20", rng, 10), "home", "4s", "保养"))
            legs.append(Leg(_t(d, "11:40", rng, 15), "4s", "home", "保养"))
        n = rng.choices([0, 1, 2], weights=[4, 4, 2])[0]
        slots = ["10:40", "15:30", "17:20"]
        rng.shuffle(slots)
        for i in range(n):
            k = rng.choice(["market", "market", "mall", "mall", "798", "sanlitun", "liangma"])
            t = _t(d, slots[i], rng, 20)
            legs.append(Leg(t, "home", k, "购物" if k in ("market", "mall") else "休闲"))
            legs.append(Leg(t + dt.timedelta(minutes=P[k].mins + rng.randint(50, 110)), k, "home",
                            "购物" if k in ("market", "mall") else "休闲"))
        evening_legs()
    legs.sort(key=lambda x: x.depart)
    return legs


def _energy_need(legs: list[Leg]) -> float:
    return sum((leg.km if leg.km is not None else leg_km(leg.a, leg.b)[0]) * 0.2 for leg in legs) / CAP * 100


def simulate() -> list[dict]:
    car = Car()
    wk_by_day = _workouts_by_day()
    plans = {d: plan_day(d, wk_by_day.get(d, []), _rng("plan", d)) for d in daterange(START, END)}
    parked_since: dt.date | None = None
    for d in daterange(START, END):
        rng = _rng("sim", d)
        trip = life.trip_on(d)
        legs = list(plans[d])
        # ---- airport: departure / return
        if trip and trip.airport and d == trip.start:
            dh, dm = life.hhmm(trip.out_dep)
            dep = dt.datetime(d.year, d.month, d.day, dh, dm, tzinfo=CN8)
            lead = 170 if trip.country != "中国" else 125
            go = dep - dt.timedelta(minutes=lead + rng.randint(-8, 8))
            legs.insert(0, Leg(go, "home", trip.airport, "送机", nav=True,
                               note=f"车辆停放于{place_name(trip.airport)}（{trip.label}）。"))
            parked_since = d
        if trip and trip.airport and d == trip.end and car.at == trip.airport:
            ah, am = life.hhmm(trip.back_arr)
            arr = dt.datetime(d.year, d.month, d.day, ah, am, tzinfo=CN8)
            back = arr + dt.timedelta(minutes=(50 if trip.country != "中国" else 28) + rng.randint(0, 12))
            days = (d - parked_since).days + 1 if parked_since else 1
            fee = min(days * PARK_FEE[trip.airport], 480)
            legs.append(Leg(back, trip.airport, "home", "接机",
                            note=f"（{trip.label}返程，机场停车 {days} 天，停车费 ¥{fee}）"))
        # mid-trip remote check for long airport parking
        if trip and trip.airport and trip.start < d < trip.end and (d - trip.start).days == 3:
            t = _t(d, "20:30", rng, 30)
            car._rec(t, "提醒", "提醒 · 车辆停放状态",
                     f"{cn_date(d)} {t:%H:%M} 远程车况：车辆已在{place_name(trip.airport)}停放 {(d - trip.start).days} 天，"
                     f"电量 {rnd(car.soc)}%，车门已上锁，车窗已关闭，12V 蓄电池正常。", "北京", kind_detail="停放状态",
                     soc=rnd(car.soc))
        # ---- reminders
        for due in life.INSURANCE_DUE:
            if d == due - dt.timedelta(days=30):
                t = _t(d, "09:05")
                car._rec(t, "提醒", "提醒 · 车险续保",
                         f"{cn_date(d)} 车辆提醒：车险（康宁财险）将于 {cn_date(due)} 到期，请在到期前完成续保。",
                         "北京", kind_detail="车险续保")
        for sd, (stype, _items) in life.CAR_SERVICE.items():
            if d == sd - dt.timedelta(days=14):
                t = _t(d, "08:40")
                car._rec(t, "提醒", f"提醒 · {stype}预约",
                         f"{cn_date(d)} 车辆提醒：距离建议的{stype}还有约 14 天（当前里程表 {car.odo:,.0f} 公里），"
                         f"已为您推荐远途汽车北京望京4S店 {cn_md(sd)}（{weekday_cn(sd)}）09:30 的预约时段。", "北京",
                         kind_detail=f"{stype}预约")
        if d in OTA:
            v = OTA[d]
            t = _t(d, "02:30")
            car._rec(t, "提醒", f"提醒 · 车机升级 {v}",
                     f"{cn_date(d)} 02:30 车机系统已在夜间完成 OTA 升级至 {v}：{OTA_NOTE[v]}。", "北京",
                     kind_detail="系统升级", version=v)
        if d.month in (12, 1, 2) and not trip and rng.random() < 0.06:
            t = _t(d, "07:35", rng, 20)
            wheel = rng.choice(["左前", "右前", "左后", "右后"])
            car._rec(t, "提醒", "提醒 · 胎压偏低",
                     f"{cn_date(d)} {t:%H:%M} 车辆提醒：{wheel}轮胎压 2.2 bar，低于建议值 2.5 bar（气温较低），请检查并补气。",
                     "北京", kind_detail="胎压偏低")
        # ---- drive (a leg that would start before the previous one ends is pushed back)
        last_arrival = None
        legs.sort(key=lambda x: x.depart)
        for leg in legs:
            if leg.a != car.at:
                continue  # the car is elsewhere (e.g. parked at the airport)
            if last_arrival and leg.depart < last_arrival + dt.timedelta(minutes=3):
                leg = Leg(last_arrival + dt.timedelta(minutes=rng.randint(3, 9)), leg.a, leg.b, leg.purpose, leg.nav,
                          leg.note, leg.km, leg.mins)
            if leg.depart > AS_OF:
                break
            if leg.nav:
                car.navigate(leg, rng)
            arrive = car.drive(leg, rng)
            last_arrival = arrive
            if leg.b == "4s" and d in life.CAR_SERVICE:
                stype, items = life.CAR_SERVICE[d]
                cost = {"年度保养": 680, "轮胎换位": 120, "更换轮胎": 4360}[stype] + rng.choice([0, 40, 80])
                t = arrive + dt.timedelta(minutes=8)
                nxt = (d + dt.timedelta(days=365)).strftime("%Y年%m月")
                car._rec(t, "保养", f"保养 · {stype} · 远途汽车北京望京4S店",
                         f"{cn_date(d)}（{weekday_cn(d)}）在远途汽车北京望京4S店完成{stype}：{items}。里程表 "
                         f"{car.odo:,.0f} 公里，费用 ¥{cost}。下次年度保养建议 {nxt} 或里程表 {car.odo + 20000:,.0f} 公里。",
                         "北京", frm=None, to="远途汽车北京望京4S店", distance_km=None, duration_min=120,
                         energy_kwh=None, service=stype, items=items, cost_yuan=float(cost),
                         odometer_km=rnd(car.odo))
            # opportunistic workplace fast charging at lunch
            if (leg.b == "office" and leg.purpose == "通勤" and car.soc < 55 and rng.random() < 0.12
                    and not any(x.purpose == "客户拜访" for x in legs)):
                car.charge(max(_t(d, "12:05", rng, 10), arrive + dt.timedelta(minutes=20)), "office", 85, rng)
            # destination charging on the first evening at 北戴河 / 天津 / 古北水镇
            o = life.outing_on(d)
            if o and o.nights > 0 and leg.b == o.place and o.date == d and car.soc < 70:
                st = "beidaihe" if o.place == "beidaihe" else ("tianjin" if o.place == "tianjin" else None)
                if st:
                    car.charge(_t(d, "19:30", rng, 30), st, 100 if st == "beidaihe" else 90, rng,
                               city=place_city(o.place))
        # ---- overnight charging at home
        if car.at == "home":
            nxt = d + dt.timedelta(days=1)
            need = _energy_need(plans.get(nxt, []))
            ntrip = life.trip_on(nxt)
            if ntrip and ntrip.start == nxt and ntrip.airport:
                need += leg_km("home", ntrip.airport)[0] * 0.2 / CAP * 100 + 15
            t = _t(d, "22:10", rng, 50)
            if last_arrival:
                t = max(t, last_arrival + dt.timedelta(minutes=rng.randint(8, 25)))
            if t <= AS_OF and (car.soc < 42 or car.soc - need < 30):
                car.charge(t, "home", 100 if need > 45 else 90, rng)
    car.recs = [r for r in car.recs if dt.datetime.fromisoformat(r["date"]) <= AS_OF]
    return car.recs


# ---------------------------------------------------------------- summaries
ROUTINE = {place_name("home"), place_name("office")}


def aggregate(recs: list[dict]) -> dict:
    trips = [r for r in recs if r["event"] == "行程"]
    charges = [r for r in recs if r["event"] == "充电"]
    km = rnd(sum(r["distance_km"] for r in trips), 1)
    energy = rnd(sum(r["energy_kwh"] for r in trips), 1)
    out = {
        "trips": len(trips), "km": km, "minutes": sum(r["duration_min"] for r in trips), "energy": energy,
        "rate": rnd(energy / km * 100, 1) if km else None,
        "charges": len(charges), "charge_kwh": rnd(sum(r["energy_kwh"] for r in charges), 1),
        "charge_cost": rnd(sum(r["cost_yuan"] for r in charges), 2),
        "charge_types": Counter(r["station_type"] for r in charges),
        "navs": sum(r["event"] == "导航" for r in recs),
        "services": [r for r in recs if r["event"] == "保养"],
        "reminders": [r for r in recs if r["event"] == "提醒"],
        "commute_days": len({r["date"][:10] for r in trips if r["to"] == place_name("office")}),
        "odometer": max((r["odometer_km"] for r in recs if r.get("odometer_km") is not None), default=None),
        "longest": max(trips, key=lambda r: (r["distance_km"], r["date"])) if trips else None,
    }
    major: dict[str, list[str]] = {}
    routine: dict[str, set] = {}
    for r in trips:
        name = r["to"]
        if name in ROUTINE or "服务区" in name or r["purpose"] == "通勤":
            continue
        if r["purpose"] in MAJOR_PURPOSES:
            major.setdefault(name, []).append(r["date"][:10])
        else:
            routine.setdefault(name, set()).add(r["date"][:10])
    out["destinations"] = major
    out["routine"] = {k: len(v) for k, v in routine.items()}
    return out


MAJOR_PURPOSES = ("周末出游", "送机", "接机", "客户拜访", "体检", "保养")


def _dest_text(dest: dict[str, list[str]]) -> str:
    parts = []
    for name, dates in sorted(dest.items(), key=lambda kv: kv[1][0]):
        uniq = sorted(set(dates))
        if len(uniq) == 1:
            ds = cn_md(D(uniq[0]))
        elif len(uniq) <= 3 and (D(uniq[-1]) - D(uniq[0])).days == len(uniq) - 1:
            ds = f"{cn_md(D(uniq[0]))}—{cn_md(D(uniq[-1]))}"
        elif len(uniq) <= 3:
            ds = "、".join(cn_md(D(x)) for x in uniq)
        else:
            ds = f"{len(uniq)} 天"
        parts.append(f"{name}（{ds}）")
    return "、".join(parts)


def _routine_text(routine: dict[str, int]) -> str:
    items = sorted(routine.items(), key=lambda kv: (-kv[1], kv[0]))
    return "、".join(f"{k} {v} 天" for k, v in items)


def _ctype_text(c: Counter) -> str:
    return "、".join(f"{k} {v} 次" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _body(agg: dict) -> str:
    text = (f"出行 {agg['trips']} 次，总里程 {agg['km']:,.1f} 公里，驾驶时长 {hm(agg['minutes'])}，"
            f"耗电 {agg['energy']:.1f} kWh")
    if agg["rate"] is not None:
        text += f"（平均 {agg['rate']:.1f} kWh/百公里）"
    text += "；"
    if agg["charges"]:
        text += (f"充电 {agg['charges']} 次（{_ctype_text(agg['charge_types'])}），共充入 {agg['charge_kwh']:.1f} kWh，"
                 f"充电费用 ¥{agg['charge_cost']:,.2f}；")
    else:
        text += "没有充电；"
    if agg["commute_days"]:
        text += f"通勤往返云帆大厦 {agg['commute_days']} 天；"
    if agg["destinations"]:
        text += f"主要目的地：{_dest_text(agg['destinations'])}；"
    if agg["routine"]:
        text += f"日常往返：{_routine_text(agg['routine'])}；"
    if agg["navs"]:
        text += f"使用导航 {agg['navs']} 次；"
    for s in agg["services"]:
        text += f"{cn_md(D(s['date'][:10]))} {s['service']}（¥{s['cost_yuan']:.0f}）；"
    rem = Counter(r["kind_detail"] for r in agg["reminders"])
    if rem:
        text += "车辆提醒：" + "、".join(f"{k} {v} 次" for k, v in rem.items()) + "；"
    if agg["longest"]:
        lr = agg["longest"]
        text += f"最长单次行程 {cn_md(D(lr['date'][:10]))} {lr['from']} → {lr['to']} {lr['distance_km']:.1f} 公里；"
    if agg["odometer"] is not None:
        text += f"期末里程表 {agg['odometer']:,.0f} 公里。"
    return text.rstrip("；") + ("" if text.endswith("。") else "。")


def _metrics(agg: dict) -> dict:
    """Numbers only; lists and maps go to ``detail``."""
    return {"trips": agg["trips"], "distance_km": agg["km"], "drive_min": agg["minutes"], "energy_kwh": agg["energy"],
            "kwh_per_100km": agg["rate"], "charges": agg["charges"], "charge_kwh": agg["charge_kwh"],
            "charge_cost_yuan": agg["charge_cost"], "navigations": agg["navs"], "commute_days": agg["commute_days"],
            "odometer_km": agg["odometer"]}


def _detail(agg: dict) -> dict:
    return {"charge_types": dict(agg["charge_types"]), "destinations": dict(agg["destinations"]),
            "routine": agg["routine"]}


def summaries(recs: list[dict]) -> list[dict]:
    by_day = defaultdict(list)
    for r in recs:
        by_day[D(r["date"][:10])].append(r)
    out = []
    wk = week_start(START)
    while wk <= END:
        we = wk + dt.timedelta(days=6)
        first, last = max(wk, START), min(we, END)
        partial = we > END
        agg = aggregate([r for d in daterange(first, last) for r in by_day[d]])
        span = f"{first.year}年{cn_md(first)}—{cn_md(last)}"
        iso_y, iso_w, _ = wk.isocalendar()
        text = (f"{iso_y}年第{iso_w}周用车周报（{span}{'，本周进行中，截至11月20日18:00' if partial else ''}）："
                + _body(agg))
        date = AS_OF if partial else dt.datetime(we.year, we.month, we.day, 23, 45, tzinfo=CN8)
        out.append({"kind": "car", "device": "car", "date": iso(date),
                    "title": f"用车周报 · {span}" + ("（本周）" if partial else ""), "text": text,
                    "fields": {"year": str(date.year), "month": f"{date.year}-{date.month:02d}", "event": "周报",
                               "city": "北京"},
                    "event": "周报", "from": None, "to": None, "distance_km": agg["km"], "duration_min": agg["minutes"],
                    "energy_kwh": agg["energy"],
                    "metrics": _metrics(agg),
                    "detail": _detail(agg) | {"week_start": first.isoformat(), "week_end": last.isoformat(),
                                              "partial": partial}})
        wk += dt.timedelta(days=7)
    m0 = dt.date(START.year, START.month, 1)
    while m0 <= END:
        me = month_end(m0)
        last = min(me, END)
        partial = me > END
        agg = aggregate([r for d in daterange(m0, last) for r in by_day[d]])
        text = (f"{m0.year}年{m0.month}月用车月报（{cn_md(m0)}—{cn_md(last)}"
                f"{'，本月进行中，截至11月20日18:00' if partial else ''}）：" + _body(agg))
        date = AS_OF if partial else dt.datetime(me.year, me.month, me.day, 23, 50, tzinfo=CN8)
        out.append({"kind": "car", "device": "car", "date": iso(date),
                    "title": f"用车月报 · {m0.year}年{m0.month}月" + ("（本月）" if partial else ""), "text": text,
                    "fields": {"year": str(m0.year), "month": f"{m0.year}-{m0.month:02d}", "event": "月报",
                               "city": "北京"},
                    "event": "月报", "from": None, "to": None, "distance_km": agg["km"], "duration_min": agg["minutes"],
                    "energy_kwh": agg["energy"],
                    "metrics": _metrics(agg),
                    "detail": _detail(agg) | {"month": f"{m0.year}-{m0.month:02d}", "period_end": last.isoformat(),
                                              "partial": partial}})
        m0 = me + dt.timedelta(days=1)
    return out


def build() -> list[dict]:
    recs = simulate()
    recs += summaries(recs)
    order = {"周报": 1, "月报": 2}
    recs.sort(key=lambda r: (dt.datetime.fromisoformat(r["date"]), order.get(r["event"], 0)))
    return [{"id": i, **r} for i, r in enumerate(recs, 1)]


def main() -> None:
    recs = build()
    write_jsonl(OUT / "car.jsonl", recs)
    print(f"[car] {len(recs)} records -> {OUT / 'car.jsonl'}", dict(Counter(r["event"] for r in recs)))


if __name__ == "__main__":
    main()
