"""林舟's timeline shared by the phone, watch and car generators.

Domestic trips come from the flight / rail / hotel emails of the demo mailbox
(``demo/data/mailbox.jsonl``, category ``travel``); they are frozen here so the
generators do not need the mailbox. That includes the family trips 成都
2024-07-20 → 07-26 (JW1405/JW1410), 三亚 2025-07-25 → 07-31 (JW1953/JW1958) and the
Spring Festivals in 扬州. Two family trips and the Germany business trip are new
and exist only on the devices:

* 韩国旅游 2024-10-01 → 10-06, the National Day holiday (首尔、釜山, +09:00, 首都机场).
  NOTE: mailbox threads place the family in Beijing on some of these days (see README).
* 日本赏樱 2025-04-03 → 04-08 (东京、京都、奈良, +09:00).
* 德国出差 2025-09-14 → 09-20 (慕尼黑、柏林、法兰克福, +02:00).

Everything is fictional.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from demo.corpus.devices.common import END, START, daterange

D = dt.date.fromisoformat

# ---------------------------------------------------------------- people & car
OWNER = "林舟"
HOME = "望京 嘉禾园小区"
OFFICE = "中关村软件园二期 云帆大厦"
SCHOOL = "育才实验小学"
CAR = "远途 T5 SUV 纯电版（京N·8K2**）"
CAR_SHORT = "远途 T5"
BATTERY_KWH = 78.0  # usable
HEIGHT_CM = 175.0

# ---------------------------------------------------------------- holidays (same table as demo/corpus/calendar.py)
_HOL = [
    ("2024-01-01", "2024-01-01"), ("2024-02-10", "2024-02-17"), ("2024-04-04", "2024-04-06"),
    ("2024-05-01", "2024-05-05"), ("2024-06-10", "2024-06-10"), ("2024-09-15", "2024-09-17"),
    ("2024-10-01", "2024-10-07"),
    ("2025-01-01", "2025-01-01"), ("2025-01-28", "2025-02-04"), ("2025-04-04", "2025-04-06"),
    ("2025-05-01", "2025-05-05"), ("2025-05-31", "2025-06-02"), ("2025-10-01", "2025-10-08"),
    ("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"), ("2026-04-04", "2026-04-06"),
    ("2026-05-01", "2026-05-05"), ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
]
HOLIDAYS: set[dt.date] = set()
for _a, _b in _HOL:
    HOLIDAYS.update(daterange(D(_a), D(_b)))
MAKEUP = {D(x) for x in [
    "2024-02-04", "2024-02-18", "2024-04-07", "2024-04-28", "2024-05-11", "2024-09-14", "2024-09-29", "2024-10-12",
    "2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11",
    "2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10",
]}


def is_workday(d: dt.date) -> bool:
    if d in MAKEUP:
        return True
    if d in HOLIDAYS:
        return False
    return d.weekday() < 5


def school_day(d: dt.date) -> bool:
    """育才实验小学 term days (winter break ~Jan 20 – Feb 28, summer break Jul 10 – Aug 31)."""
    if not is_workday(d):
        return False
    md = (d.month, d.day)
    if (1, 20) <= md <= (2, 28) or (7, 10) <= md <= (8, 31):
        return False
    return True


# ---------------------------------------------------------------- places (Beijing)
@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    km: float  # road distance from home
    mins: int  # typical drive time from home (free-flowing)
    area: str = "朝阳区"
    city: str = "北京"


P = {
    "home": Place(HOME, 39.9963, 116.4705, 0.0, 0),
    "office": Place(OFFICE, 40.0565, 116.2786, 27.6, 46, "海淀区"),
    "school": Place(SCHOOL, 39.9921, 116.4588, 2.6, 9),
    "mall": Place("望京购物中心", 39.9935, 116.4807, 2.1, 8),
    "market": Place("望京 生鲜超市", 40.0002, 116.4690, 1.5, 6),
    "gym": Place("动力健身 望京店", 39.9897, 116.4761, 2.3, 8),
    "pool": Place("望京全民健身中心", 40.0041, 116.4789, 3.0, 10),
    "badminton": Place("望京全民健身中心 羽毛球馆", 40.0041, 116.4789, 3.0, 10),
    "4s": Place("远途汽车北京望京4S店", 40.0117, 116.4925, 4.0, 12),
    "checkup": Place("安康体检中心 朝阳门店", 39.9246, 116.4339, 12.4, 30, "东城区"),
    "chaoyang_park": Place("朝阳公园", 39.9440, 116.4790, 6.3, 18),
    "olympic": Place("奥林匹克森林公园", 40.0165, 116.3920, 9.2, 22),
    "wenyu": Place("温榆河公园", 40.0326, 116.5200, 8.1, 20),
    "798": Place("798艺术区", 39.9840, 116.4950, 4.6, 14),
    "sanlitun": Place("三里屯", 39.9365, 116.4547, 9.8, 26),
    "liangma": Place("亮马河 蓝色港湾", 39.9502, 116.4728, 7.9, 22),
    "cstm": Place("中国科学技术馆", 40.0034, 116.3922, 9.6, 24),
    "museum": Place("国家博物馆", 39.9050, 116.4014, 16.5, 40, "东城区"),
    "palace": Place("故宫博物院", 39.9163, 116.3972, 15.8, 42, "东城区"),
    "jingshan": Place("景山公园", 39.9254, 116.3967, 15.2, 40, "西城区"),
    "beihai": Place("北海公园", 39.9254, 116.3888, 15.9, 42, "西城区"),
    "temple_heaven": Place("天坛公园", 39.8822, 116.4066, 18.9, 45, "东城区"),
    "summer_palace": Place("颐和园", 39.9999, 116.2755, 23.4, 45, "海淀区"),
    "yuanmingyuan": Place("圆明园", 40.0081, 116.2982, 20.6, 40, "海淀区"),
    "xiangshan": Place("香山公园", 39.9916, 116.1881, 32.5, 58, "海淀区"),
    "botanical": Place("北京植物园", 40.0021, 116.2072, 30.1, 55, "海淀区"),
    "yuyuantan": Place("玉渊潭公园", 39.9157, 116.3197, 21.8, 45, "海淀区"),
    "shougang": Place("首钢园", 39.9139, 116.1666, 38.2, 60, "石景山区"),
    "universal": Place("北京环球度假区", 39.8550, 116.6791, 33.4, 45, "通州区"),
    "mutianyu": Place("慕田峪长城", 40.4319, 116.5704, 72.5, 75, "怀柔区"),
    "hongluo": Place("红螺寺", 40.3881, 116.6261, 58.3, 65, "怀柔区"),
    "yanqi": Place("雁栖湖", 40.4093, 116.6697, 55.7, 60, "怀柔区"),
    "badaling": Place("八达岭长城", 40.3599, 116.0200, 68.4, 70, "延庆区"),
    "expo": Place("北京世园公园", 40.4337, 115.9500, 78.6, 80, "延庆区"),
    "yeyahu": Place("野鸭湖国家湿地公园", 40.4210, 115.8130, 86.2, 88, "延庆区"),
    "gubei": Place("古北水镇", 40.6560, 117.2500, 124.8, 105, "密云区"),
    "pkx": Place("北京大兴国际机场 P2停车楼", 39.5098, 116.4105, 62.4, 68, "大兴区"),
    "pek": Place("北京首都国际机场 T3停车楼", 40.0532, 116.6114, 21.3, 28, "顺义区"),
    "bjs": Place("北京南站", 39.8652, 116.3786, 21.6, 45, "丰台区"),
    "tianjin": Place("天津 五大道", 39.1132, 117.1918, 136.5, 125, "和平区", "天津"),
    "beidaihe": Place("北戴河 海滨", 39.8270, 119.4860, 287.0, 205, "北戴河区", "秦皇岛"),
    "gezi": Place("北戴河 鸽子窝公园", 39.8494, 119.5302, 293.0, 215, "北戴河区", "秦皇岛"),
    "laohushi": Place("北戴河 老虎石海上公园", 39.8169, 119.4895, 289.0, 210, "北戴河区", "秦皇岛"),
}

# charging stations: key -> (name, kind, kW, price ¥/kWh incl. service fee)
CHARGERS = {
    "home": ("家用充电桩（嘉禾园小区地下车库）", "家充", 7.0, 0.49),
    "office": ("中关村软件园 地下车库快充站", "公共快充", 60.0, 1.18),
    "wangjing": ("望京SOHO 地下快充站", "公共快充", 90.0, 1.35),
    "jingcheng": ("京承高速 怀柔服务区快充站", "高速快充", 120.0, 1.62),
    "jingha": ("京哈高速 香河服务区快充站", "高速快充", 120.0, 1.62),
    "jinghu": ("京津高速 服务区快充站", "高速快充", 120.0, 1.58),
    "beidaihe": ("北戴河 海滨酒店 停车场交流桩", "目的地充电", 7.0, 1.05),
    "tianjin": ("天津 五大道 公共快充站", "公共快充", 60.0, 1.42),
    "yanqing": ("延庆 妫水公园 公共快充站", "公共快充", 60.0, 1.38),
}


# ---------------------------------------------------------------- trips
@dataclass
class Trip:
    key: str
    label: str  # fields.trip for photos, e.g. "德国出差"
    purpose: str  # 出差 / 旅游 / 探亲 / 度假
    family: bool
    country: str
    start: dt.date
    end: dt.date
    tz: int  # local UTC offset (hours)
    segments: list  # [(city, first_day, last_day)]
    mode: str = "air"  # air / rail
    airport: str | None = "pkx"  # car parks here (pkx / pek) or None
    out_dep: str = "08:00"  # Beijing time of departure from Beijing
    back_arr: str = "21:00"  # Beijing time of arrival in Beijing
    flight_out: str = ""
    flight_back: str = ""
    hotel: str | None = None
    note: str = ""
    extra: dict = field(default_factory=dict)

    def city_on(self, d: dt.date) -> str:
        for city, a, b in self.segments:
            if a <= d <= b:
                return city
        return self.segments[-1][0]

    def days(self):
        return list(daterange(self.start, self.end))


def _seg(city, a, b):
    return (city, D(a), D(b))


# (out date, dep, arr, flight, back date, dep, arr, flight, airport city, hotel city) — from the mailbox travel emails
_MAIL_TRIPS = [
    ("2024-02-08", "08:00", "09:55", "JW1861", "2024-02-16", "18:40", "20:40", "JW1864", "扬州", None),
    ("2024-03-18", "07:30", "09:45", "JW1501", "2024-03-19", "19:55", "22:15", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2024-03-26", "13:20", "15:25", "JW1203", "2024-03-28", "21:10", "23:20", "JW1206", "西安", "悦庭酒店西安高新店"),
    ("2024-04-01", "10:40", "12:45", "JW1203", "2024-04-04", "16:50", "19:00", "JW1206", "西安", "悦庭酒店西安高新店"),
    ("2024-04-07", "13:20", "15:35", "JW1501", "2024-04-09", "19:55", "22:15", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2024-04-22", "08:00", "10:15", "JW1501", "2024-04-24", "19:55", "22:15", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2024-05-06", "08:00", "10:05", "JW1203", "2024-05-07", "16:50", "19:00", "JW1206", "西安", "悦庭酒店西安高新店"),
    ("2024-05-20", "13:20", "15:35", "JW1505", "2024-05-21", "21:10", "23:30", "JW1512", "上海", None),
    ("2024-07-03", "10:40", "12:45", "JW1203", "2024-07-05", "16:50", "19:00", "JW1206", "西安", "悦庭酒店西安高新店"),
    ("2024-07-15", "07:30", "09:35", "JW1203", "2024-07-16", "19:55", "22:05", "JW1206", "西安", "悦庭酒店西安高新店"),
    ("2024-07-20", "08:00", "11:00", "JW1405", "2024-07-26", "18:40", "21:45", "JW1410", "成都", None),
    ("2024-08-19", "07:30", "09:45", "JW1501", "2024-08-21", "18:40", "21:00", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2024-08-27", "09:15", "11:30", "JW1501", "2024-08-28", "18:40", "21:00", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2024-09-12", "07:30", "09:45", "JW1505", "2024-09-14", "21:10", "23:30", "JW1512", "上海", None),
    ("2024-09-23", "07:30", "09:35", "JW1203", "2024-09-25", "21:10", "23:20", "JW1206", "西安", "悦庭酒店西安高新店"),
    ("2024-10-23", "08:00", "10:15", "JW1501", "2024-10-25", "18:40", "21:00", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2024-10-28", "13:20", "15:35", "JW1501", "2024-10-29", "16:50", "19:10", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2024-11-04", "10:40", "12:55", "JW1505", "2024-11-05", "19:55", "22:15", "JW1512", "上海", None),
    ("2024-11-21", "08:00", "10:15", "JW1505", "2024-11-22", "21:10", "23:30", "JW1512", "上海", None),
    ("2024-12-16", "08:00", "10:15", "JW1505", "2024-12-18", "21:10", "23:30", "JW1512", "上海", None),
    ("2025-01-26", "08:00", "09:55", "JW1861", "2025-02-03", "18:40", "20:40", "JW1864", "扬州", None),
    ("2025-02-24", "10:40", "12:55", "JW1503", "2025-02-26", "18:40", "21:00", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-03-31", "09:15", "11:30", "JW1503", "2025-04-02", "18:40", "21:00", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-04-16", "07:30", "09:10", "JW1631", "2025-04-19", "21:10", "22:55", "JW1634", "郑州", "悦庭酒店郑州郑东店"),
    ("2025-05-14", "09:15", "10:55", "JW1631", "2025-05-16", "21:10", "22:55", "JW1634", "郑州", "悦庭酒店郑州郑东店"),
    ("2025-05-27", "07:30", "09:10", "JW1631", "2025-05-28", "16:50", "18:35", "JW1634", "郑州", "悦庭酒店郑州郑东店"),
    ("2025-06-16", "08:00", "10:15", "JW1503", "2025-06-17", "18:40", "21:00", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-06-23", "09:15", "11:30", "JW1503", "2025-06-25", "16:50", "19:10", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-07-25", "08:00", "11:50", "JW1953", "2025-07-31", "18:40", "22:35", "JW1958", "三亚", None),
    ("2025-08-19", "10:40", "12:20", "JW1631", "2025-08-22", "18:40", "20:25", "JW1634", "郑州", "悦庭酒店郑州郑东店"),
    ("2025-08-28", "09:15", "11:30", "JW1503", "2025-08-30", "19:55", "22:15", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-09-08", "09:15", "11:35", "JW1711", "2025-09-10", "18:40", "21:05", "JW1716", "杭州", "悦庭酒店杭州滨江店"),
    ("2025-10-09", "07:30", "09:45", "JW1503", "2025-10-12", "21:10", "23:30", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-10-20", "09:15", "11:30", "JW1503", "2025-10-23", "16:50", "19:10", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-11-03", "08:00", "10:15", "JW1503", "2025-11-06", "21:10", "23:30", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2025-11-17", "07:30", "09:50", "JW1711", "2025-11-19", "18:40", "21:05", "JW1716", "杭州", "悦庭酒店杭州滨江店"),
    ("2026-01-21", "09:15", "11:35", "JW1711", "2026-01-22", "19:55", "22:20", "JW1716", "杭州", "悦庭酒店杭州滨江店"),
    ("2026-02-13", "08:00", "09:55", "JW1861", "2026-02-21", "18:40", "20:40", "JW1864", "扬州", None),
    ("2026-03-04", "08:00", "10:15", "JW1503", "2026-03-05", "18:40", "21:00", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2026-03-30", "13:20", "15:35", "JW1501", "2026-04-02", "18:40", "21:00", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2026-04-13", "10:40", "12:55", "JW1503", "2026-04-16", "19:55", "22:15", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2026-04-27", "10:40", "12:55", "JW1501", "2026-04-29", "21:10", "23:30", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2026-05-09", "08:00", "10:15", "JW1503", "2026-05-11", "16:50", "19:10", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2026-05-20", "10:40", "12:55", "JW1501", "2026-05-23", "21:10", "23:30", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2026-05-28", "08:00", "10:15", "JW1503", "2026-05-29", "16:50", "19:10", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2026-06-22", "07:30", "09:45", "JW1501", "2026-06-24", "21:10", "23:30", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2026-07-18", "08:00", "10:05", "JW1203", "2026-07-23", "18:40", "20:50", "JW1206", "西安", None),
    ("2026-07-27", "13:20", "15:35", "JW1503", "2026-07-28", "18:40", "21:00", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2026-08-11", "08:00", "11:25", "JW1305", "2026-08-13", "18:40", "22:10", "JW1308", "深圳", "悦庭酒店深圳南山店"),
    ("2026-09-02", "08:00", "10:15", "JW1501", "2026-09-04", "18:40", "21:00", "JW1508", "上海", "悦庭酒店上海虹桥店"),
    ("2026-09-20", "07:30", "09:45", "JW1503", "2026-09-21", "18:40", "21:00", "JW1510", "苏州", "悦庭酒店苏州园区店"),
    ("2026-10-13", "08:00", "10:15", "JW1501", "2026-10-16", "18:40", "21:00", "JW1508", "上海", "悦庭酒店上海虹桥店"),
]


def _mail_trip(row) -> Trip:
    out_d, dep, _arr, fo, back_d, bdep, barr, fb, city, hotel = row
    if city == "扬州":
        label, purpose, family = "春节回扬州", "探亲", True
    elif city == "三亚":
        label, purpose, family = "三亚度假", "度假", True
    elif city == "成都":
        label, purpose, family = "成都旅游", "旅游", True
    else:
        label, purpose, family = f"{city}出差", "出差", False
    return Trip(
        key=f"{out_d}-{city}", label=label, purpose=purpose, family=family, country="中国",
        start=D(out_d), end=D(back_d), tz=8, segments=[_seg(city, out_d, back_d)],
        airport="pkx", out_dep=dep, back_arr=barr, flight_out=fo, flight_back=fb, hotel=hotel,
    )


TRIPS: list[Trip] = [_mail_trip(r) for r in _MAIL_TRIPS]
TRIPS.append(Trip(
    key="2026-09-14-天津", label="天津出差", purpose="出差", family=False, country="中国",
    start=D("2026-09-14"), end=D("2026-09-16"), tz=8, segments=[_seg("天津", "2026-09-14", "2026-09-16")],
    mode="rail", airport=None, out_dep="08:05", back_arr="18:40", flight_out="C2215", flight_back="C2260",
    note="高铁往返（北京南—天津），未开车",
))
TRIPS += [
    Trip(
        key="2024-10-01-韩国", label="韩国旅游", purpose="旅游", family=True, country="韩国",
        start=D("2024-10-01"), end=D("2024-10-06"), tz=9,
        segments=[_seg("首尔", "2024-10-01", "2024-10-03"), _seg("釜山", "2024-10-04", "2024-10-05"),
                  _seg("首尔", "2024-10-06", "2024-10-06")],
        airport="pek", out_dep="08:30", back_arr="20:30", flight_out="北京首都—首尔仁川",
        flight_back="首尔仁川—北京首都", note="国庆假期一家三口：首尔3晚、釜山2晚（KTX往返）",
    ),
    Trip(
        key="2025-04-03-日本", label="日本赏樱", purpose="旅游", family=True, country="日本",
        start=D("2025-04-03"), end=D("2025-04-08"), tz=9,
        segments=[_seg("东京", "2025-04-03", "2025-04-05"), _seg("京都", "2025-04-06", "2025-04-08")],
        airport="pek", out_dep="08:25", back_arr="17:40", flight_out="北京首都—东京羽田",
        flight_back="大阪关西—北京首都", note="清明假期赏樱：东京3晚、京都2晚（4月7日奈良一日游）",
        extra={"nara_day": D("2025-04-07")},
    ),
    Trip(
        key="2025-09-14-德国", label="德国出差", purpose="出差", family=False, country="德国",
        start=D("2025-09-14"), end=D("2025-09-20"), tz=2,
        segments=[_seg("慕尼黑", "2025-09-14", "2025-09-16"), _seg("柏林", "2025-09-17", "2025-09-18"),
                  _seg("法兰克福", "2025-09-19", "2025-09-19")],
        airport="pek", out_dep="13:50", back_arr="09:55", flight_out="北京首都—慕尼黑",
        flight_back="法兰克福—北京首都", note="欧洲客户拜访与行业展会：慕尼黑、柏林、法兰克福",
        extra={"overnight_return": True},  # FRA 9/19 17:55 local -> PEK 9/20 09:55
    ),
]
TRIPS.sort(key=lambda t: t.start)

# Day -> trip (the whole span, including travel days)
_BY_DAY: dict[dt.date, Trip] = {}
for _t in TRIPS:
    for _d in _t.days():
        if _d in _BY_DAY:
            raise ValueError(f"overlapping trips on {_d}: {_BY_DAY[_d].key} / {_t.key}")
        _BY_DAY[_d] = _t


def trip_on(d: dt.date) -> Trip | None:
    return _BY_DAY.get(d)


def away_all_day(d: dt.date) -> bool:
    """Away from Beijing for the whole day (not the departure or return day)."""
    t = _BY_DAY.get(d)
    return bool(t) and t.start < d < t.end


def hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


# ---------------------------------------------------------------- special days
HALF_MARATHONS = {
    D("2024-04-14"): {"name": "2024北京半程马拉松", "km": 21.0975, "goal": "1:58"},
    D("2026-04-19"): {"name": "2026北京半程马拉松", "km": 21.0975, "goal": "2:15"},
}
CHECKUPS = {D("2025-04-11"): (72.5, "2025年度体检"), D("2026-04-10"): (77.8, "2026年度体检")}
CAR_SERVICE = {  # date -> (type, details)
    D("2024-03-23"): ("年度保养", "更换空调滤芯、检查制动液和冷却液、四轮换位、底盘及高压线束检查"),
    D("2024-09-21"): ("轮胎换位", "四轮换位与动平衡，检查胎纹深度（约5.8毫米）"),
    D("2025-03-22"): ("年度保养", "更换空调滤芯和雨刮片、制动液含水量检测、四轮换位、电池健康检测（SOH 96%）"),
    D("2025-11-01"): ("更换轮胎", "更换四条全季节轮胎 235/55 R19，四轮定位"),
    D("2026-03-28"): ("年度保养", "更换制动液和空调滤芯、四轮换位、电池健康检测（SOH 94%）、底盘检查"),
}
INSURANCE_DUE = [D("2024-03-17"), D("2025-03-17"), D("2026-03-17")]


# ---------------------------------------------------------------- outings by car (weekends and holidays)
@dataclass
class Outing:
    date: dt.date
    place: str  # key into P
    nights: int = 0  # 0 = day trip
    family: bool = True
    note: str = ""


FIXED_OUTINGS = [
    Outing(D("2024-04-27"), "botanical", note="植物园看郁金香"),
    Outing(D("2024-05-02"), "gubei", 1, note="五一 古北水镇住一晚"),
    Outing(D("2024-06-09"), "universal", note="端午 带小禾去环球度假区"),
    Outing(D("2024-08-10"), "beidaihe", 2, note="北戴河海边避暑"),
    Outing(D("2024-10-19"), "mutianyu", note="慕田峪长城看红叶"),
    Outing(D("2024-11-02"), "xiangshan", note="香山红叶"),
    Outing(D("2024-12-28"), "badaling", note="八达岭长城看残雪"),
    Outing(D("2025-01-04"), "cstm", note="带小禾去科技馆"),
    Outing(D("2025-05-02"), "yanqi", 1, note="五一 雁栖湖住一晚"),
    Outing(D("2025-06-01"), "universal", note="儿童节 环球度假区"),
    Outing(D("2025-08-16"), "expo", note="延庆世园公园"),
    Outing(D("2025-10-02"), "beidaihe", 2, note="国庆 北戴河"),
    Outing(D("2025-10-25"), "badaling", note="八达岭长城"),
    Outing(D("2026-01-03"), "shougang", note="首钢园看冬奥滑雪大跳台"),
    Outing(D("2026-05-02"), "tianjin", 1, note="五一 天津两日游"),
    Outing(D("2026-06-20"), "yeyahu", note="端午 野鸭湖观鸟"),
    Outing(D("2026-08-22"), "hongluo", note="红螺寺避暑"),
    Outing(D("2026-10-02"), "beidaihe", 2, note="国庆 北戴河自驾"),
    Outing(D("2026-10-24"), "mutianyu", note="慕田峪长城看红叶"),
    Outing(D("2026-11-14"), "summer_palace", note="颐和园秋景"),
]

# local weekend destinations (key, weight by season: (spring, summer, autumn, winter))
_LOCAL = [
    ("summer_palace", (3, 2, 3, 2)), ("yuanmingyuan", (2, 1, 2, 1)), ("beihai", (2, 2, 2, 1)),
    ("jingshan", (1, 1, 2, 1)), ("temple_heaven", (2, 1, 2, 1)), ("palace", (1, 1, 2, 1)),
    ("museum", (1, 2, 1, 2)), ("cstm", (1, 2, 1, 2)), ("798", (2, 1, 2, 2)), ("sanlitun", (1, 1, 1, 2)),
    ("liangma", (2, 2, 2, 1)), ("wenyu", (2, 2, 2, 1)), ("olympic", (2, 2, 2, 1)), ("chaoyang_park", (2, 2, 2, 1)),
    ("yuyuantan", (3, 1, 1, 1)), ("botanical", (3, 1, 1, 0)), ("xiangshan", (1, 1, 3, 0)),
    ("yanqi", (1, 2, 1, 0)), ("hongluo", (1, 2, 1, 0)), ("badaling", (1, 1, 2, 0)), ("mutianyu", (1, 1, 2, 0)),
]


def season(d: dt.date) -> int:
    return {12: 3, 1: 3, 2: 3, 3: 0, 4: 0, 5: 0, 6: 1, 7: 1, 8: 1, 9: 2, 10: 2, 11: 2}[d.month]


# National Day 2024 used to be free holiday days that drew random outings (and 10-03 held a fixed day
# trip to 天津) before the Korea trip moved there. Keep drawing for those days and drop the result,
# so every later random outing stays exactly as it was.
_RNG_REPLAY = {D("2024-10-01"), D("2024-10-02"), D("2024-10-04"), D("2024-10-05"), D("2024-10-06")}


def _gen_outings() -> list[Outing]:
    rng = random.Random(20261120)
    fixed = {o.date for o in FIXED_OUTINGS}
    blocked = set()
    for o in FIXED_OUTINGS:
        for i in range(o.nights + 1):
            blocked.add(o.date + dt.timedelta(days=i))
    out = list(FIXED_OUTINGS)
    for d in daterange(START, END):
        replay = d in _RNG_REPLAY
        if is_workday(d) or d in blocked or (trip_on(d) and not replay) or d in HALF_MARATHONS or d in CAR_SERVICE:
            continue
        if d.weekday() not in (5, 6) and d not in HOLIDAYS:
            continue
        if d in fixed:
            continue
        if rng.random() < 0.30:
            keys = [k for k, _ in _LOCAL]
            weights = [w[season(d)] for _, w in _LOCAL]
            k = rng.choices(keys, weights=weights)[0]
            family = rng.random() < 0.85
            if not replay:
                out.append(Outing(d, k, 0, family=family))
    out.sort(key=lambda o: o.date)
    return out


OUTINGS: list[Outing] = _gen_outings()
OUTING_BY_DAY: dict[dt.date, Outing] = {}
for _o in OUTINGS:
    for _i in range(_o.nights + 1):
        OUTING_BY_DAY.setdefault(_o.date + dt.timedelta(days=_i), _o)


def outing_on(d: dt.date) -> Outing | None:
    return OUTING_BY_DAY.get(d)


def in_beijing(d: dt.date) -> bool:
    """Sleeps in Beijing that night and spends the day there."""
    if trip_on(d):
        return False
    o = OUTING_BY_DAY.get(d)
    return not (o and o.nights > 0 and o.date <= d)


def where(d: dt.date) -> tuple[str, str, int]:
    """(city, country, utc offset) where 林舟 spends most of day d."""
    t = trip_on(d)
    if t:
        if d == t.end and t.extra.get("overnight_return"):
            return "北京", "中国", 8  # landed in the morning
        return t.city_on(d), t.country, t.tz
    o = OUTING_BY_DAY.get(d)
    if o and o.nights > 0 and o.date < d:
        return P[o.place].city, "中国", 8
    return "北京", "中国", 8
