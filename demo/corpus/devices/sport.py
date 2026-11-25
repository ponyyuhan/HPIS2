"""Watch records (kind ``sport``, device ``watch``) for 林舟, 2024-01-01 → 2026-11-20 18:00.

    python -m demo.corpus.devices.sport          # writes demo/data/devices/sport.jsonl

Deterministic (seeded per day). Record types (``activity``): 跑步 骑行 游泳 步行 羽毛球
力量训练 睡眠 日常 周报 月报. Weekly / monthly reports are computed from the
records themselves (``aggregate``), so every number in a summary can be re-derived
exactly; ``validate.py`` does that.

``plan()`` returns the workouts without writing anything; the car and photo
generators use it to stay consistent (drives to 奥森, photos after a run).
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from demo.corpus.devices import life
from demo.corpus.devices.common import (
    AS_OF, END, OUT, START, cn_date, cn_md, daterange, hm, hms, iso, month_end, pace, tz, week_start, weekday_cn,
    write_jsonl,
)

D = dt.date.fromisoformat
SEED = "linzhou-watch-v1"


def rnd(x: float, nd: int = 0):
    """Round half up (not banker's rounding); int for nd == 0."""
    q = Decimal(1).scaleb(-nd)
    v = Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP)
    return int(v) if nd == 0 else float(v)


def curve(points: list[tuple[str, float]], d: dt.date) -> float:
    pts = [(D(a), v) for a, v in points]
    if d <= pts[0][0]:
        return pts[0][1]
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if a <= d <= b:
            w = (d - a).days / max((b - a).days, 1)
            return va + (vb - va) * w
    return pts[-1][1]


# Fitness story (consistent with the mailbox check-ups: 72.5 kg on 2025-04-11, 77.8 kg on 2026-04-10):
# fit in 2024, a busy second half of 2025 with little exercise, a half-marathon block in early 2026,
# then steady training after the April 2026 report (doctor: ≥150 minutes a week).
FITNESS = [("2024-01-01", 0.70), ("2024-04-14", 0.80), ("2024-08-31", 0.76), ("2024-12-31", 0.72),
           ("2025-04-30", 0.68), ("2025-07-31", 0.62), ("2025-10-31", 0.50), ("2025-12-31", 0.44),
           ("2026-02-28", 0.48), ("2026-04-19", 0.52), ("2026-05-31", 0.56), ("2026-08-31", 0.65),
           ("2026-11-20", 0.71)]
WEIGHT = [("2024-01-01", 71.2), ("2024-04-14", 70.3), ("2024-09-30", 70.8), ("2024-12-31", 71.6),
          ("2025-04-11", 72.5), ("2025-07-31", 73.4), ("2025-10-31", 75.2), ("2026-01-31", 77.0),
          ("2026-04-10", 77.8), ("2026-06-30", 76.4), ("2026-08-31", 75.3), ("2026-11-20", 74.3)]
RHR = [("2024-01-01", 58), ("2024-04-14", 56), ("2024-12-31", 58), ("2025-04-30", 59), ("2025-08-31", 61),
       ("2025-12-31", 64), ("2026-04-10", 65), ("2026-06-30", 62), ("2026-09-30", 59), ("2026-11-20", 57)]
RUNS_PER_WEEK = [("2024-01-01", 2.4), ("2024-03-01", 3.2), ("2024-04-14", 3.0), ("2024-06-30", 2.8),
                 ("2024-12-31", 2.3), ("2025-04-30", 2.2), ("2025-07-31", 1.5), ("2025-10-31", 0.8),
                 ("2025-12-20", 0.9), ("2026-01-05", 2.0), ("2026-04-19", 2.4), ("2026-04-25", 2.6),
                 ("2026-05-31", 3.0), ("2026-11-20", 3.3)]
POST_CHECKUP = D("2026-04-25")

PLACES = {
    "wangjing": "望京 大望京公园",
    "chaoyang": "朝阳公园",
    "olympic": "奥林匹克森林公园",
    "zgc": "中关村软件园 环湖绿道",
    "wenyu": "温榆河绿道",
    "pool": "望京全民健身中心 游泳馆",
    "court": "望京全民健身中心 羽毛球馆",
    "gym": "动力健身 望京店",
    "neighborhood": "望京 社区周边",
}
TRIP_RUNS = {  # fixed runs on trips: date -> (local time, place, city, km)
    D("2024-07-21"): ("06:20", "首尔 汉江公园（盘浦—汝矣岛段）", "首尔", 6.53),
    D("2024-07-24"): ("06:10", "釜山 海云台海滨步道", "釜山", 5.08),
    D("2025-04-04"): ("06:30", "东京 皇居外苑环线", "东京", 5.12),
    D("2025-04-07"): ("06:40", "京都 鸭川河畔", "京都", 6.02),
    D("2025-07-27"): ("06:10", "三亚 三亚湾海滨步道", "三亚", 6.36),
    D("2025-07-30"): ("06:15", "三亚 三亚湾海滨步道", "三亚", 5.41),
    D("2025-09-15"): ("06:45", "慕尼黑 英国花园", "慕尼黑", 7.25),
    D("2025-09-18"): ("06:50", "柏林 蒂尔加滕公园", "柏林", 5.64),
}
BUSINESS_RUN_SPOTS = {
    "上海": "上海 徐汇滨江跑道", "苏州": "苏州 金鸡湖环湖步道", "杭州": "杭州 钱塘江滨江跑道",
    "西安": "西安 曲江池遗址公园", "郑州": "郑州 如意湖公园", "深圳": "深圳 深圳湾公园", "天津": "天津 海河沿岸步道",
}
HIKES = {"mutianyu": (6.8, 420), "badaling": (5.6, 380), "xiangshan": (6.1, 510), "hongluo": (5.3, 260)}
STROLLS = {"summer_palace", "temple_heaven", "beihai", "yuanmingyuan", "botanical", "yanqi", "expo", "yeyahu",
           "gubei", "palace", "jingshan", "yuyuantan", "universal", "shougang"}


@dataclass
class Workout:
    start: dt.datetime  # local, tz-aware
    activity: str  # 跑步 骑行 游泳 步行 羽毛球 力量训练
    place: str
    city: str
    country: str = "中国"
    km: float = 0.0  # distance in km (swims too)
    secs: int = 0
    avg_hr: int = 0
    max_hr: int = 0
    kcal: int = 0
    elev: int = 0
    cadence: int = 0
    pool_len: int = 0
    laps: int = 0
    stroke: str = ""
    subtype: str = ""  # 半程马拉松 / 长距离 / 室内 / 健走 / 徒步 / 城市漫步 / 双打 …
    note: str = ""
    key: str = ""  # place key for Beijing venues (car / photo generators)

    @property
    def end(self) -> dt.datetime:
        return self.start + dt.timedelta(seconds=self.secs)

    @property
    def minutes(self) -> int:
        return rnd(self.secs / 60)


@dataclass
class Sleep:
    bed: dt.datetime
    wake: dt.datetime
    deep: int
    rem: int
    awake: int
    score: int
    avg_hr: int
    note: str = ""

    @property
    def total(self) -> int:
        return rnd((self.wake - self.bed).total_seconds() / 60)

    @property
    def minutes(self) -> int:
        return self.total - self.awake

    @property
    def light(self) -> int:
        return self.minutes - self.deep - self.rem


@dataclass
class Day:
    date: dt.date
    tz: int
    city: str
    country: str
    steps: int = 0
    active_kcal: int = 0
    exercise_min: int = 0
    stand_h: int = 0
    rhr: int = 0
    weight: float | None = None
    floors: int = 0
    km: float = 0.0


def _rng(*parts) -> random.Random:
    return random.Random("|".join([SEED, *map(str, parts)]))


def _at(d: dt.date, hhmm: str, offset: int, jitter: int = 0, rng: random.Random | None = None) -> dt.datetime:
    h, m = life.hhmm(hhmm)
    t = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz(offset))
    if jitter and rng:
        t += dt.timedelta(minutes=rng.randint(-jitter, jitter))
    return t


# ---------------------------------------------------------------- workout factories
def _run(rng, start, place, city, km, f, w, country="中国", subtype="", race=False, note="", key="") -> Workout:
    p = 480 - 180 * f - (8 if race else 0) + rng.uniform(-9, 12) + (6 if km >= 12 and not race else 0)
    km = rnd(km, 2)
    secs = int(round(p * km))
    hr = int(round(155 - 20 * f + rng.uniform(-4, 4) + (12 if race else 0) + (3 if km >= 12 else 0)))
    return Workout(start, "跑步", place, city, country, km, secs, hr, hr + rng.randint(11, 22),
                   int(round(w * km * 1.03)), 46 if race else rng.randint(6, 28),
                   int(round(162 + 14 * f + rng.uniform(-3, 3))), subtype=subtype, note=note, key=key)


def _ride(rng, start, km, f, w) -> Workout:
    v = 19.5 + 5 * f + rng.uniform(-1.2, 1.2)  # km/h
    km = rnd(km, 2)
    secs = int(round(km / v * 3600))
    hr = int(round(136 - 8 * f + rng.uniform(-4, 4)))
    return Workout(start, "骑行", PLACES["wenyu"], "北京", "中国", km, secs, hr, hr + rng.randint(15, 28),
                   int(round(w * km * 0.36)), rng.randint(20, 90), key="wenyu")


def _swim(rng, start, place, city, meters, f, country="中国", pool=25, key="") -> Workout:
    p100 = 205 - 30 * f + rng.uniform(-6, 8)
    secs = int(round(p100 * meters / 100))
    hr = int(round(132 - 6 * f + rng.uniform(-4, 4)))
    return Workout(start, "游泳", place, city, country, rnd(meters / 1000, 3), secs, hr, hr + rng.randint(14, 24),
                   int(round(secs / 60 * 9.2)), 0, 0, pool, meters // pool, rng.choice(["蛙泳", "自由泳", "自由泳"]),
                   key=key)


def _badminton(rng, start, minutes) -> Workout:
    hr = rng.randint(126, 138)
    return Workout(start, "羽毛球", PLACES["court"], "北京", "中国", 0.0, minutes * 60, hr, hr + rng.randint(25, 34),
                   int(round(minutes * 7.1)), subtype="双打", key="court")


def _strength(rng, start, minutes) -> Workout:
    hr = rng.randint(104, 118)
    return Workout(start, "力量训练", PLACES["gym"], "北京", "中国", 0.0, minutes * 60, hr, hr + rng.randint(25, 38),
                   int(round(minutes * 5.2)), subtype=rng.choice(["上肢+核心", "下肢+核心", "全身循环"]), key="gym")


def _walk(rng, start, place, city, km, w, country="中国", subtype="健走", elev=0, pace_s=620, key="") -> Workout:
    km = rnd(km, 2)
    secs = int(round(km * (pace_s + rng.uniform(-25, 30))))
    hr = rng.randint(98, 114) + (12 if elev > 150 else 0)
    kcal = int(round(w * km * (0.62 if elev < 150 else 0.85) + elev * 0.6))
    return Workout(start, "步行", place, city, country, km, secs, hr, hr + rng.randint(14, 26), kcal, elev,
                   subtype=subtype, key=key)


# ---------------------------------------------------------------- planning
def _trip_workouts(d, trip, rng, f, w) -> list[Workout]:
    out = []
    city = trip.city_on(d)
    rate = curve(RUNS_PER_WEEK, d)
    if d in TRIP_RUNS:
        hhmm, place, c, km = TRIP_RUNS[d]
        out.append(_run(rng, _at(d, hhmm, trip.tz), place, c, km, f, w, trip.country))
    elif (trip.purpose == "出差" and trip.country == "中国" and trip.start < d and city in BUSINESS_RUN_SPOTS
          and rng.random() < 0.22 + 0.25 * (rate / 3.6)):
        out.append(_run(rng, _at(d, "06:35", 8, 15, rng), BUSINESS_RUN_SPOTS[city], city, rng.uniform(4.6, 7.2), f, w))
    if d == D("2025-04-07"):
        out.append(_walk(rng, _at(d, "13:10", 9), "奈良公园—东大寺", "奈良", 6.4, w, "日本", "城市漫步", 0, 720))
    if d == D("2025-04-05"):
        out.append(_walk(rng, _at(d, "09:40", 9), "东京 上野公园—浅草", "东京", 5.9, w, "日本", "城市漫步", 0, 720))
    if d == D("2024-07-22"):
        out.append(_walk(rng, _at(d, "09:20", 9), "首尔 北岳山城郭路", "首尔", 5.2, w, "韩国", "徒步", 260, 760))
    if d == D("2025-07-28"):
        out.append(_swim(rng, _at(d, "17:30", 8), "三亚 酒店室外泳池", "三亚", 800, f))
    if d == D("2025-09-16"):
        out.append(_walk(rng, _at(d, "18:40", 2), "慕尼黑 老城区（玛利亚广场—王宫花园）", "慕尼黑", 4.3, w, "德国",
                         "城市漫步", 0, 700))
    return out


def _beijing_workouts(d, rng, f, w) -> list[Workout]:
    out: list[Workout] = []
    outing = life.outing_on(d)
    work = life.is_workday(d)
    weekend = not work
    rate = curve(RUNS_PER_WEEK, d)
    multi_day = bool(outing and outing.nights > 0)
    if d in life.HALF_MARATHONS:
        hmr = life.HALF_MARATHONS[d]
        return [_run(rng, _at(d, "07:30", 8), "天安门广场—奥林匹克公园（北京半程马拉松）", "北京",
                     hmr["km"], f, w, race=True, subtype="半程马拉松", note=hmr["name"])]
    if outing and outing.date == d and outing.place in HIKES:
        km, elev = HIKES[outing.place]
        out.append(_walk(rng, _at(d, "09:50", 8, 20, rng), life.P[outing.place].name, "北京",
                         km + rng.uniform(-0.6, 0.8), w, "中国", "徒步", elev, 800, key=outing.place))
    elif multi_day and outing.place == "beidaihe" and d > outing.date:
        out.append(_run(rng, _at(d, "06:20", 8, 10, rng), "北戴河 海滨木栈道", "秦皇岛", rng.uniform(5.0, 7.5), f, w,
                        key="beidaihe"))
    elif outing and outing.date == d and outing.place in STROLLS and rng.random() < 0.55:
        out.append(_walk(rng, _at(d, "10:10", 8, 30, rng), life.P[outing.place].name, "北京",
                         rng.uniform(4.2, 7.5), w, "中国", "城市漫步", 0, 720, key=outing.place))
    if multi_day or (outing and outing.date == d and (outing.place in HIKES or outing.place == "tianjin")):
        return out
    # ---- regular week in Beijing
    p_run = rate / 7 * (1.25 if weekend else 0.9)
    if d.month in (12, 1, 2):
        p_run *= 0.8
    long_block = D("2026-01-05") <= d <= D("2026-04-12") or D("2024-02-20") <= d <= D("2024-04-07")
    if long_block and d.weekday() == 5:
        p_run = 0.85
    ran = False
    if rng.random() < p_run:
        ran = True
        if weekend:
            key = "olympic" if rng.random() < 0.6 else "chaoyang"
            km = rng.uniform(13, 18.5) if long_block else rng.uniform(8.5, 13.5)
            if d.month in (12, 1, 2):
                km *= 0.8
            out.append(_run(rng, _at(d, "07:00", 8, 25, rng), PLACES[key], "北京", km, f, w,
                            subtype="长距离" if km >= 12 else "", key=key))
        else:
            r = rng.random()
            key = "wangjing" if r < 0.55 else ("chaoyang" if r < 0.75 else "zgc")
            if d.month in (12, 1, 2) and key != "zgc" and rng.random() < 0.4:
                run = _run(rng, _at(d, "19:30", 8, 20, rng), PLACES["gym"] + "（跑步机）", "北京",
                           rng.uniform(4.5, 6.5), f, w, subtype="室内", key="gym")
                run.elev = 0
                out.append(run)
            else:
                km = rng.uniform(4.6, 6.2) if key == "zgc" else rng.uniform(4.6, 7.6)
                start = _at(d, "12:15" if key == "zgc" else "06:25", 8, 12, rng)
                out.append(_run(rng, start, PLACES[key], "北京", km, f, w, key=key))
    if weekend and not ran and 4 <= d.month <= 10 and not outing:
        if rng.random() < (0.45 if d.year < 2026 else 0.4 if d >= POST_CHECKUP else 0.12):
            start = _at(d, "07:30" if d.month in (6, 7, 8) else "08:40", 8, 20, rng)
            out.append(_ride(rng, start, rng.uniform(24, 46), f, w))
    p_swim = 0.18 if d.year == 2024 else (0.12 if d < POST_CHECKUP else 0.32)
    if d.month in (6, 7, 8, 9):
        p_swim *= 1.4
    if work and d.weekday() in (1, 3) and rng.random() < p_swim:
        meters = rng.choice([1000, 1100, 1200, 1250, 1300, 1400, 1500])
        out.append(_swim(rng, _at(d, "20:10", 8, 15, rng), PLACES["pool"], "北京", meters, f, key="pool"))
    p_str = 0.30 if d.year == 2024 else (0.15 if d < POST_CHECKUP else 0.30)
    if d.weekday() in (1, 5) and not ran and not outing and rng.random() < p_str:
        start = _at(d, "19:40" if d.weekday() == 1 else "16:30", 8, 15, rng)
        out.append(_strength(rng, start, rng.choice([40, 45, 50, 55, 60])))
    if d >= POST_CHECKUP and not any(x.activity in ("跑步", "游泳") for x in out) and rng.random() < 0.38:
        out.append(_walk(rng, _at(d, "20:30", 8, 20, rng), PLACES["neighborhood"], "北京", rng.uniform(3.6, 5.8), w,
                         key="neighborhood"))
    return out


def plan():
    """Return (workouts, sleeps, days) for the whole period; deterministic."""
    workouts: list[Workout] = []
    sleeps: list[Sleep] = []
    days: list[Day] = []
    last_badminton = D("2023-12-20")
    for d in daterange(START, END):
        rng = _rng("day", d)
        f = curve(FITNESS, d)
        w = curve(WEIGHT, d) + rng.uniform(-0.25, 0.25)
        city, country, off = life.where(d)
        trip = life.trip_on(d)
        overnight_back = bool(trip and d == trip.end and trip.extra.get("overnight_return"))
        if overnight_back:
            today = []  # landed in the morning after a night flight
        elif trip:
            today = _trip_workouts(d, trip, rng, f, w)
        else:
            today = _beijing_workouts(d, rng, f, w)
            # badminton with the neighbour, Thursday evening, roughly every other week
            if (life.is_workday(d) and d.weekday() == 3 and (d - last_badminton).days >= 13
                    and not any(x.activity == "游泳" for x in today) and not life.outing_on(d)
                    and rng.random() < (0.75 if d.year < 2026 else 0.45)):
                today.append(_badminton(rng, _at(d, "20:00", 8, 5, rng), rng.choice([75, 85, 90, 95, 100, 110])))
                last_badminton = d
        today.sort(key=lambda x: x.start)
        workouts.extend(today)

        # ---------------- sleep: the night before, ending on the morning of d
        srng = _rng("sleep", d)
        prev_trip = life.trip_on(d - dt.timedelta(days=1))
        note = ""
        if overnight_back:
            bed = dt.datetime(d.year, d.month, d.day, 1, 10, tzinfo=tz(8))
            wake = bed + dt.timedelta(minutes=srng.randint(250, 300))
            note = "航班上（法兰克福—北京）"
        else:
            early = any(x.start.hour < 7 for x in today)
            if trip and d == trip.start:
                dh, dm = life.hhmm(trip.out_dep)
                wake = min(dt.datetime(d.year, d.month, d.day, dh, dm, tzinfo=tz(8)) - dt.timedelta(minutes=150),
                           dt.datetime(d.year, d.month, d.day, 7, 10, tzinfo=tz(8)))
            elif trip and trip.country == "德国" and trip.start < d <= trip.start + dt.timedelta(days=3):
                wake = _at(d, "04:40", off, 20, srng)  # jet lag
            else:
                base_wake = "05:55" if early else ("06:40" if life.is_workday(d) else "07:25")
                wake = _at(d, base_wake, off, 14, srng)
            mins = srng.gauss(415 if d.year < 2025 else (402 if d < D("2026-05-01") else 418), 24)
            if trip and trip.country == "德国" and trip.start < d <= trip.start + dt.timedelta(days=3):
                mins -= 85
                note = "时差影响，凌晨早醒"
            if prev_trip and prev_trip.end == d - dt.timedelta(days=1) and not trip:
                mins -= 25  # late flight home the evening before
            mins = max(255, min(505, mins))
            bed = wake - dt.timedelta(minutes=mins + srng.randint(6, 22))
        total = rnd((wake - bed).total_seconds() / 60)
        awake = srng.randint(5, 24)
        asleep = total - awake
        deep = int(asleep * srng.uniform(0.14, 0.21))
        rem = int(asleep * srng.uniform(0.18, 0.24))
        score = int(max(48, min(95, 52 + (asleep - 300) * 0.22 + srng.uniform(-6, 6) - (8 if note else 0))))
        sleeps.append(Sleep(bed, wake, deep, rem, awake, score, int(round(curve(RHR, d) + srng.uniform(-2, 3))), note))

        # ---------------- daily summary (the current day is not finished yet)
        if d >= END:
            continue
        drng = _rng("daily", d)
        outing = life.outing_on(d)
        work = life.is_workday(d)
        base = (6200 if work else 8600) + drng.randint(-1500, 1800)
        if trip and trip.purpose in ("旅游", "度假", "探亲") and not overnight_back:
            base = drng.randint(12000, 19000) if trip.purpose != "探亲" else drng.randint(7000, 12500)
        elif trip and trip.country != "中国" and not overnight_back:
            base = drng.randint(11000, 17500)
        elif trip and not overnight_back:
            base = drng.randint(7600, 11800)
        elif outing:
            base = drng.randint(12000, 18500)
        wsteps = sum(int(x.cadence * x.secs / 60) if x.activity == "跑步" else int(x.km * 1320) if x.activity == "步行"
                     else x.minutes * 45 if x.activity == "羽毛球" else 0 for x in today)
        wkm = sum(x.km for x in today if x.activity in ("跑步", "步行"))
        steps = base + wsteps
        brisk = drng.randint(0, 12) + (12 if outing or (trip and trip.purpose != "出差") else 0)
        active = sum(x.kcal for x in today) + int(base * 0.034) + drng.randint(40, 110)
        rhr = int(round(curve(RHR, d) + drng.uniform(-1.6, 1.8) + (2 if trip and trip.country == "德国" else 0)))
        weight = None
        if d.weekday() == 6 and life.in_beijing(d):
            weight = rnd(curve(WEIGHT, d) + drng.uniform(-0.2, 0.2), 1)
        floors = drng.randint(4, 14) + sum(x.elev for x in today) // 3
        days.append(Day(d, off, city, country, steps, active, sum(x.minutes for x in today) + brisk,
                        drng.randint(8, 13), rhr, weight, floors, rnd(wkm + base * 0.00072, 1)))
    return workouts, sleeps, days


# ---------------------------------------------------------------- record text
def _where(x: Workout) -> str:
    if x.country != "中国":
        return f"{x.country}{x.place}" if x.place.startswith(x.city) else f"{x.country}{x.city} {x.place}"
    if x.city == "北京":
        return f"北京{x.place}"
    return x.place if x.place.startswith(x.city) else f"{x.city} {x.place}"


def _tlabel(t: dt.datetime) -> str:
    local = "" if t.utcoffset() == dt.timedelta(hours=8) else "（当地时间）"
    return f"{t.year}年{t.month}月{t.day}日（{weekday_cn(t.date())}）{t.hour:02d}:{t.minute:02d}{local}"


def brief(x: Workout) -> str:
    if x.activity in ("跑步", "骑行", "步行"):
        return f"{x.activity} {x.km:.2f} 公里"
    if x.activity == "游泳":
        return f"游泳 {round(x.km * 1000)} 米"
    return f"{x.activity} {x.minutes} 分钟"


def _workout_record(x: Workout) -> dict:
    t = x.start
    m: dict = {"duration_s": x.secs, "duration_min": rnd(x.secs / 60, 1), "avg_hr": x.avg_hr, "max_hr": x.max_hr,
               "kcal": x.kcal}
    where = _where(x)
    if x.activity == "跑步":
        p = x.secs / x.km
        m.update(distance_km=x.km, pace_s_per_km=rnd(p), cadence_spm=x.cadence, elev_gain_m=x.elev)
        if x.subtype == "半程马拉松":
            title = f"跑步 · {x.note} · 完赛 {hms(x.secs)}"
            text = (f"{_tlabel(t)} 参加{x.note}（天安门广场—奥林匹克公园），完成半程马拉松 {x.km:.2f} 公里，"
                    f"净成绩 {hms(x.secs)}，平均配速 {pace(p)}/公里，平均心率 {x.avg_hr} 次/分，最高心率 {x.max_hr} 次/分，"
                    f"步频 {x.cadence} 步/分，消耗 {x.kcal} 千卡。")
        else:
            title = f"跑步 · {x.place} · {x.km:.2f} 公里"
            kind = {"长距离": "长距离跑", "室内": "室内跑步（跑步机）"}.get(x.subtype, "跑步")
            text = (f"{_tlabel(t)} 在{where}{kind} {x.km:.2f} 公里，用时 {hms(x.secs)}，平均配速 {pace(p)}/公里，"
                    f"平均心率 {x.avg_hr} 次/分，最高心率 {x.max_hr} 次/分，步频 {x.cadence} 步/分，"
                    f"累计爬升 {x.elev} 米，消耗 {x.kcal} 千卡。")
    elif x.activity == "骑行":
        v = x.km / (x.secs / 3600)
        m.update(distance_km=x.km, speed_kmh=rnd(v, 1), elev_gain_m=x.elev)
        title = f"骑行 · {x.place} · {x.km:.2f} 公里"
        text = (f"{_tlabel(t)} 在{where}户外骑行 {x.km:.2f} 公里，用时 {hms(x.secs)}，平均速度 {v:.1f} 公里/小时，"
                f"平均心率 {x.avg_hr} 次/分，最高心率 {x.max_hr} 次/分，累计爬升 {x.elev} 米，消耗 {x.kcal} 千卡。")
    elif x.activity == "游泳":
        meters = round(x.km * 1000)
        p100 = x.secs / meters * 100
        m.update(distance_m=meters, pace_s_per_100m=rnd(p100), pool_m=x.pool_len, laps=x.laps)
        title = f"游泳 · {x.place} · {meters} 米"
        text = (f"{_tlabel(t)} 在{where}泳池游泳 {meters} 米（{x.pool_len} 米泳池，{x.laps} 趟），用时 {hms(x.secs)}，"
                f"平均配速 {pace(p100)}/100米，主泳姿 {x.stroke}，平均心率 {x.avg_hr} 次/分，消耗 {x.kcal} 千卡。")
    elif x.activity == "羽毛球":
        title = f"羽毛球 · {x.place} · {x.minutes} 分钟"
        text = (f"{_tlabel(t)} 在{where}打羽毛球（{x.subtype}）{x.minutes} 分钟，平均心率 {x.avg_hr} 次/分，"
                f"最高心率 {x.max_hr} 次/分，消耗 {x.kcal} 千卡。")
    elif x.activity == "力量训练":
        title = f"力量训练 · {x.place} · {x.minutes} 分钟"
        text = (f"{_tlabel(t)} 在{where}力量训练（{x.subtype}）{x.minutes} 分钟，平均心率 {x.avg_hr} 次/分，"
                f"消耗 {x.kcal} 千卡。")
    else:  # 步行
        p = x.secs / x.km
        m.update(distance_km=x.km, pace_s_per_km=rnd(p), elev_gain_m=x.elev)
        title = f"步行 · {x.place} · {x.km:.2f} 公里"
        kind = {"徒步": "徒步", "城市漫步": "步行游览", "健走": "健走"}.get(x.subtype, "步行")
        text = (f"{_tlabel(t)} 在{where}{kind} {x.km:.2f} 公里，用时 {hms(x.secs)}，平均配速 {pace(p)}/公里，"
                f"累计爬升 {x.elev} 米，平均心率 {x.avg_hr} 次/分，消耗 {x.kcal} 千卡。")
    rec = {
        "kind": "sport", "device": "watch", "date": iso(t), "title": title, "text": text,
        "fields": {"year": str(t.year), "month": f"{t.year}-{t.month:02d}", "activity": x.activity},
        "activity": x.activity, "metrics": m,
        "place": {"name": x.place, "city": x.city, "country": x.country},
    }
    if x.subtype:
        rec["subtype"] = x.subtype
    if x.stroke:
        rec["detail"] = {"stroke": x.stroke}
    return rec


def _sleep_record(s: Sleep) -> dict:
    w = s.wake
    night = w.date() - dt.timedelta(days=1)
    local = "（当地时间）" if w.utcoffset() != dt.timedelta(hours=8) else ""
    text = (f"{cn_date(night)}夜间睡眠：{s.bed.hour:02d}:{s.bed.minute:02d} 入睡，{cn_md(w.date())}（{weekday_cn(w.date())}）"
            f"{w.hour:02d}:{w.minute:02d} 醒来{local}，实际睡眠 {hm(s.minutes)}（深睡 {hm(s.deep)}，浅睡 {hm(s.light)}，"
            f"快速眼动 {hm(s.rem)}，清醒 {s.awake} 分钟），睡眠评分 {s.score}，睡眠平均心率 {s.avg_hr} 次/分。")
    if s.note:
        text += f"备注：{s.note}。"
    return {
        "kind": "sport", "device": "watch", "date": iso(w), "title": f"睡眠 · {cn_md(night)}夜间 · {hm(s.minutes)}",
        "text": text, "fields": {"year": str(w.year), "month": f"{w.year}-{w.month:02d}", "activity": "睡眠"},
        "activity": "睡眠",
        "metrics": {"sleep_min": s.minutes, "deep_min": s.deep, "light_min": s.light, "rem_min": s.rem,
                    "awake_min": s.awake, "score": s.score, "avg_hr": s.avg_hr},
        "detail": {"bedtime": s.bed.strftime("%H:%M"), "wake": w.strftime("%H:%M")},
    }


def _daily_record(day: Day, todays: list[Workout]) -> dict:
    t = dt.datetime(day.date.year, day.date.month, day.date.day, 23, 30, tzinfo=tz(day.tz))
    dist = day.km
    where = "" if day.city == "北京" else f"（在{day.city if day.country == '中国' else day.country + day.city}）"
    text = (f"{cn_date(day.date)}（{weekday_cn(day.date)}）全天活动{where}：步数 {day.steps:,} 步，步行距离 {dist:.1f} 公里，"
            f"活动热量 {day.active_kcal} 千卡，锻炼时长 {day.exercise_min} 分钟，站立 {day.stand_h} 小时，"
            f"爬楼 {day.floors} 层，静息心率 {day.rhr} 次/分。")
    text += f"当天运动：{'、'.join(brief(x) for x in todays)}。" if todays else "当天无专项运动记录。"
    m = {"steps": day.steps, "distance_km": dist, "active_kcal": day.active_kcal, "exercise_min": day.exercise_min,
         "stand_hours": day.stand_h, "floors": day.floors, "resting_hr": day.rhr}
    if day.weight is not None:
        text += f"体重 {day.weight:.1f} 千克（体脂秤同步）。"
        m["weight_kg"] = day.weight
    return {
        "kind": "sport", "device": "watch", "date": iso(t), "title": f"日常 · {cn_date(day.date)} · {day.steps:,} 步",
        "text": text, "fields": {"year": str(t.year), "month": f"{t.year}-{t.month:02d}", "activity": "日常"},
        "activity": "日常", "metrics": m, "place": {"city": day.city, "country": day.country},
    }


# ---------------------------------------------------------------- summaries
WORKOUT_TYPES = ("跑步", "骑行", "游泳", "步行", "羽毛球", "力量训练")


def local_date(rec: dict) -> dt.date:
    return dt.date.fromisoformat(rec["date"][:10])


def aggregate(recs: list[dict]) -> dict:
    """Exact aggregates over sport records (summaries and the validator use the same function)."""
    out: dict = {"workouts": 0, "workout_s": 0, "kcal": 0}
    for a in WORKOUT_TYPES:
        out[a] = {"n": 0, "km": 0.0, "s": 0, "m": 0}
    steps, sleeps, rhrs, weights, runs = [], [], [], [], []
    ex = 0
    for r in recs:
        a, m = r["activity"], r["metrics"]
        if a in WORKOUT_TYPES:
            out["workouts"] += 1
            out["workout_s"] += m["duration_s"]
            out["kcal"] += m["kcal"]
            out[a]["n"] += 1
            out[a]["s"] += m["duration_s"]
            out[a]["km"] += m.get("distance_km", 0.0)
            out[a]["m"] += m.get("distance_m", 0)
            if a == "跑步":
                runs.append(r)
        elif a == "日常":
            steps.append(m["steps"])
            rhrs.append(m["resting_hr"])
            ex += m["exercise_min"]
            if "weight_kg" in m:
                weights.append((r["date"], m["weight_kg"]))
        elif a == "睡眠":
            sleeps.append(m["sleep_min"])
    for a in WORKOUT_TYPES:
        out[a]["km"] = rnd(out[a]["km"], 2)
        out[a]["min"] = rnd(out[a]["s"] / 60)
    out["workout_min"] = rnd(out["workout_s"] / 60)
    out["days"] = len(steps)
    out["steps_avg"] = rnd(sum(steps) / len(steps)) if steps else None
    out["sleep_avg"] = rnd(sum(sleeps) / len(sleeps)) if sleeps else None
    out["rhr_avg"] = rnd(sum(rhrs) / len(rhrs)) if rhrs else None
    out["exercise_min"] = ex
    out["weight_last"] = weights[-1] if weights else None
    out["longest"] = out["fastest"] = out["run_pace"] = None
    if runs:
        out["longest"] = max(runs, key=lambda r: (r["metrics"]["distance_km"], r["date"]))
        out["fastest"] = min((r for r in runs if r["metrics"]["distance_km"] >= 5),
                             key=lambda r: (r["metrics"]["pace_s_per_km"], r["date"]), default=None)
        out["run_pace"] = rnd(sum(r["metrics"]["duration_s"] for r in runs) / out["跑步"]["km"])
    return out


def summary_parts(agg: dict) -> list[str]:
    parts = []
    if agg["跑步"]["n"]:
        parts.append(f"跑步 {agg['跑步']['n']} 次共 {agg['跑步']['km']:.2f} 公里（平均配速 {pace(agg['run_pace'])}/公里）")
    if agg["骑行"]["n"]:
        parts.append(f"骑行 {agg['骑行']['n']} 次共 {agg['骑行']['km']:.2f} 公里")
    if agg["游泳"]["n"]:
        parts.append(f"游泳 {agg['游泳']['n']} 次共 {agg['游泳']['m']} 米")
    if agg["步行"]["n"]:
        parts.append(f"步行/徒步 {agg['步行']['n']} 次共 {agg['步行']['km']:.2f} 公里")
    if agg["羽毛球"]["n"]:
        parts.append(f"羽毛球 {agg['羽毛球']['n']} 次共 {agg['羽毛球']['min']} 分钟")
    if agg["力量训练"]["n"]:
        parts.append(f"力量训练 {agg['力量训练']['n']} 次共 {agg['力量训练']['min']} 分钟")
    return parts


def _body(agg: dict) -> str:
    parts = summary_parts(agg)
    text = f"共锻炼 {agg['workouts']} 次，累计 {hm(agg['workout_min'])}，运动消耗 {agg['kcal']:,} 千卡"
    text += ("；" + "；".join(parts) + "。") if parts else "，本期没有专项运动记录。"
    if agg["steps_avg"] is not None:
        text += f"日均步数 {agg['steps_avg']:,} 步（{agg['days']} 天），"
    if agg["sleep_avg"] is not None:
        text += f"平均睡眠 {hm(agg['sleep_avg'])}，"
    if agg["rhr_avg"] is not None:
        text += f"平均静息心率 {agg['rhr_avg']} 次/分，"
    return text + f"中高强度锻炼 {agg['exercise_min']} 分钟"


def _metrics(agg: dict) -> dict:
    m = {"workouts": agg["workouts"], "workout_min": agg["workout_min"], "kcal": agg["kcal"],
         "runs": agg["跑步"]["n"], "run_km": agg["跑步"]["km"], "rides": agg["骑行"]["n"], "ride_km": agg["骑行"]["km"],
         "swims": agg["游泳"]["n"], "swim_m": agg["游泳"]["m"], "walks": agg["步行"]["n"], "walk_km": agg["步行"]["km"],
         "badminton": agg["羽毛球"]["n"], "badminton_min": agg["羽毛球"]["min"], "strength": agg["力量训练"]["n"],
         "strength_min": agg["力量训练"]["min"], "steps_avg": agg["steps_avg"], "sleep_avg_min": agg["sleep_avg"],
         "resting_hr_avg": agg["rhr_avg"], "exercise_min": agg["exercise_min"], "days": agg["days"]}
    if agg["run_pace"]:
        m["run_pace_s_per_km"] = agg["run_pace"]
    if agg["weight_last"]:
        m["weight_kg"] = agg["weight_last"][1]
    return m


def _trip_labels(a: dt.date, b: dt.date) -> list[str]:
    seen = []
    for d in daterange(a, b):
        t = life.trip_on(d)
        if t and t.label not in seen:
            seen.append(t.label)
    return seen


def summaries(recs: list[dict]) -> list[dict]:
    """Weekly (Mon–Sun) and monthly reports, plus the unfinished current week and month."""
    by_day: dict[dt.date, list[dict]] = {}
    for r in recs:
        by_day.setdefault(local_date(r), []).append(r)
    out = []
    wk = week_start(START)
    prev_km = None
    while wk <= END:
        we = wk + dt.timedelta(days=6)
        first, last = max(wk, START), min(we, END)
        partial = we > END
        agg = aggregate([r for d in daterange(first, last) for r in by_day.get(d, [])])
        span = f"{first.year}年{cn_md(first)}—{cn_md(last)}"
        iso_y, iso_w, _ = wk.isocalendar()
        text = (f"{iso_y}年第{iso_w}周运动周报（{span}{'，本周进行中，截至11月20日18:00' if partial else ''}）："
                + _body(agg))
        ok = "已达成" if agg["exercise_min"] >= 150 else "未达成"
        text += f"（每周≥150分钟目标{ok}）。"
        if prev_km is not None and not partial:
            dk = rnd(agg["跑步"]["km"] - prev_km, 2)
            text += f"跑量较上周{'增加' if dk >= 0 else '减少'} {abs(dk):.2f} 公里。"
        if agg["longest"]:
            lr = agg["longest"]
            text += f"本周最长一次跑步：{cn_md(local_date(lr))} {lr['metrics']['distance_km']:.2f} 公里。"
        trips = _trip_labels(first, last)
        if trips:
            text += f"本周外出行程：{'、'.join(trips)}。"
        date = AS_OF if partial else dt.datetime(we.year, we.month, we.day, 23, 45, tzinfo=tz(8))
        out.append({
            "kind": "sport", "device": "watch", "date": iso(date),
            "title": f"运动周报 · {span}" + ("（本周）" if partial else ""),
            "text": text,
            "fields": {"year": str(date.year), "month": f"{date.year}-{date.month:02d}", "activity": "周报"},
            "activity": "周报",
            "metrics": _metrics(agg),
            "detail": {"week_start": first.isoformat(), "week_end": last.isoformat(), "partial": partial},
        })
        prev_km = agg["跑步"]["km"]
        wk += dt.timedelta(days=7)
    m0 = dt.date(START.year, START.month, 1)
    while m0 <= END:
        me = month_end(m0)
        last = min(me, END)
        partial = me > END
        sel = [r for d in daterange(m0, last) for r in by_day.get(d, [])]
        agg = aggregate(sel)
        weeks = ((last - m0).days + 1) / 7
        per_week = rnd(agg["exercise_min"] / weeks)
        text = (f"{m0.year}年{m0.month}月运动月报（{cn_md(m0)}—{cn_md(last)}"
                f"{'，本月进行中，截至11月20日18:00' if partial else ''}）：" + _body(agg)
                + f"（周均 {per_week} 分钟，{'达到' if per_week >= 150 else '未达到'}每周≥150分钟的目标）。")
        if agg["longest"]:
            lr = agg["longest"]
            text += (f"本月最长跑步：{cn_md(local_date(lr))} {lr['place']['name']} {lr['metrics']['distance_km']:.2f} 公里"
                     f"（用时 {hms(lr['metrics']['duration_s'])}）。")
        if agg["fastest"]:
            fr = agg["fastest"]
            text += f"5 公里以上跑步最快配速 {pace(fr['metrics']['pace_s_per_km'])}/公里（{cn_md(local_date(fr))}）。"
        for r in sel:
            if r.get("subtype") == "半程马拉松":
                text += f"本月完成{r['title'].split(' · ')[1]}，净成绩 {hms(r['metrics']['duration_s'])}。"
        if agg["weight_last"]:
            wd, wv = agg["weight_last"]
            text += f"月内最近一次体重 {wv:.1f} 千克（{cn_md(D(wd[:10]))}）。"
        vo2 = rnd(36 + 12 * curve(FITNESS, last))
        text += f"最大摄氧量估算 {vo2}。"
        trips = _trip_labels(m0, last)
        if trips:
            text += f"本月外出行程：{'、'.join(trips)}。"
        date = AS_OF if partial else dt.datetime(me.year, me.month, me.day, 23, 50, tzinfo=tz(8))
        out.append({
            "kind": "sport", "device": "watch", "date": iso(date),
            "title": f"运动月报 · {m0.year}年{m0.month}月" + ("（本月）" if partial else ""),
            "text": text,
            "fields": {"year": str(m0.year), "month": f"{m0.year}-{m0.month:02d}", "activity": "月报"},
            "activity": "月报",
            "metrics": _metrics(agg) | {"vo2max": vo2, "exercise_min_per_week": per_week},
            "detail": {"month": f"{m0.year}-{m0.month:02d}", "period_end": last.isoformat(), "partial": partial},
        })
        m0 = me + dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------- build
def instant(rec: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(rec["date"])


def build() -> list[dict]:
    workouts, sleeps, days = plan()
    by_day: dict[dt.date, list[Workout]] = {}
    for x in workouts:
        by_day.setdefault(x.start.date(), []).append(x)
    recs = [_workout_record(x) for x in workouts]
    recs += [_sleep_record(s) for s in sleeps]
    recs += [_daily_record(dd, by_day.get(dd.date, [])) for dd in days]
    recs = [r for r in recs if instant(r) <= AS_OF]
    recs += summaries(recs)
    order = {"周报": 1, "月报": 2}
    recs.sort(key=lambda r: (instant(r), order.get(r["activity"], 0)))
    return [{"id": i, **r} for i, r in enumerate(recs, 1)]


def main() -> None:
    from collections import Counter

    recs = build()
    write_jsonl(OUT / "sport.jsonl", recs)
    print(f"[sport] {len(recs)} records -> {OUT / 'sport.jsonl'}", dict(Counter(r["activity"] for r in recs)))


if __name__ == "__main__":
    main()
