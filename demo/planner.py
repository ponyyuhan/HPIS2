"""On-device query understanding: question -> kind + structured filters + semantic text.

The device turns the filters into SSE tokens (HMAC labels) and the semantic text
into an embedding.  Every lane applies the *same* plan so that the evidence (and
therefore the answer) differs only by where and how the search runs.

Routing: a question goes to the device whose data it asks about (photo 手机,
sport 手表, car 车机, otherwise mail on the PC).  Filters come from relative or
absolute time expressions, a place gazetteer and the field values present in
the device's own data.  A question with no recognisable subject goes to mail as
a pure vector search.  If the filters would leave nothing, ``relax`` drops them
in a fixed order (on the device, which holds its own data) and says so.
"""

from __future__ import annotations

import datetime as dt
import re

from . import config
from .retrieval import FILTER_KEYS

CATEGORY_WORDS = {
    "health": ("体检", "化验", "血脂", "尿酸", "血糖", "病历", "复查", "医生"),
    "finance": ("账单", "信用卡", "还款", "消费", "流水"),
    "school": ("学校", "家长会", "班主任", "孩子", "老师通知"),
    "travel": ("航班", "机票", "酒店", "行程", "出差"),
    "client": ("客户",),
}
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0}
_NUM = r"[0-9一二两三四五六七八九十]{1,3}"

DEVICE_OF_KIND = {"mail": "pc", "photo": "phone", "sport": "watch", "car": "car"}
KIND_LABEL = {"mail": "邮件", "photo": "照片", "sport": "运动健康记录", "car": "用车记录"}
# Filter keys a kind's records carry as SSE labels (a key outside this list would match nothing).
KIND_KEYS = {
    "mail": ("orgs", "categories", "people"),
    "photo": ("countries", "cities", "trips", "types"),
    "sport": ("activities",),
    "car": ("events", "cities"),
}

# Words that say which device's data a question is about: (weight, words).
KIND_CUES = {
    "photo": (
        (3, ("照片", "相片", "相册", "图片", "拍的", "拍了", "拍摄", "拍照", "合影", "合照", "自拍", "拍过", "拍下", "相机", "照相")),
        (1, ("风景", "留念", "打卡", "旅游", "旅行", "游玩", "景点", "度假")),
    ),
    "sport": (
        (3, ("运动", "锻炼", "健身", "跑步", "骑行", "游泳", "羽毛球", "力量训练", "步数", "心率", "睡眠", "卡路里",
             "配速", "手表", "马拉松", "徒步", "体能")),
        (1, ("跑了", "慢跑", "夜跑", "晨跑", "骑车", "走路", "散步", "睡得", "睡觉", "深睡", "千卡", "消耗", "训练",
             "公里", "步行")),
    ),
    "car": (
        (3, ("开车", "驾车", "自驾", "用车", "车机", "充电", "导航", "保养", "续航", "车辆", "汽车", "我的车", "电车",
             "停车", "胎压", "充了", "开了多少")),
        (1, ("电量", "里程", "行驶", "高速", "出行", "加油", "年检", "违章", "能耗", "电耗", "公里", "行程")),
    ),
    "mail": (
        (3, ("邮件", "邮箱", "来信", "回信", "抄送", "附件", "发件", "收件", "来邮")),
        (1, ("通知", "账单", "合同", "纪要", "会议", "报价", "发票", "体检", "学校", "老师", "家长", "航班", "酒店",
             "机票", "客户", "项目", "同事", "信用卡", "银行", "还款", "预算", "报告", "方案", "出差", "工资", "报销",
             "订单", "快递", "领导", "老板", "花了", "多少钱", "费用", "金额", "价格")),
    ),
}
KIND_ORDER = ("mail", "photo", "sport", "car")  # tie-break

# Place gazetteer: country -> cities (Chinese names), plus aliases.
GAZETTEER = {
    "德国": ("慕尼黑", "柏林", "法兰克福", "汉堡", "科隆", "斯图加特", "杜塞尔多夫", "海德堡", "纽伦堡", "德累斯顿",
             "新天鹅堡", "不来梅", "莱比锡", "汉诺威", "波恩"),
    "韩国": ("首尔", "釜山", "济州", "仁川", "庆州", "江陵", "大邱"),
    "日本": ("东京", "京都", "大阪", "奈良", "札幌", "北海道", "横滨", "名古屋", "福冈", "冲绳", "神户", "箱根", "富士山"),
    "法国": ("巴黎", "尼斯", "里昂", "马赛", "波尔多", "斯特拉斯堡"),
    "意大利": ("罗马", "米兰", "威尼斯", "佛罗伦萨", "那不勒斯", "比萨"),
    "英国": ("伦敦", "爱丁堡", "曼彻斯特", "牛津", "剑桥", "利物浦"),
    "西班牙": ("巴塞罗那", "马德里", "塞维利亚", "瓦伦西亚"),
    "瑞士": ("苏黎世", "日内瓦", "卢塞恩", "因特拉肯", "伯尔尼"),
    "奥地利": ("维也纳", "萨尔茨堡", "因斯布鲁克", "哈尔施塔特"),
    "荷兰": ("阿姆斯特丹", "鹿特丹", "海牙"),
    "比利时": ("布鲁塞尔", "布鲁日"),
    "捷克": ("布拉格",),
    "美国": ("纽约", "旧金山", "洛杉矶", "西雅图", "波士顿", "芝加哥", "拉斯维加斯", "夏威夷", "华盛顿"),
    "加拿大": ("温哥华", "多伦多", "蒙特利尔"),
    "新加坡": ("新加坡",),
    "泰国": ("曼谷", "清迈", "普吉岛", "芭提雅"),
    "马来西亚": ("吉隆坡", "槟城"),
    "越南": ("河内", "胡志明市", "岘港"),
    "印度尼西亚": ("巴厘岛", "雅加达"),
    "澳大利亚": ("悉尼", "墨尔本", "布里斯班"),
    "新西兰": ("奥克兰", "皇后镇"),
    "阿联酋": ("迪拜", "阿布扎比"),
    "中国": ("北京", "上海", "深圳", "广州", "杭州", "苏州", "南京", "成都", "重庆", "西安", "武汉", "厦门", "青岛",
             "三亚", "天津", "宁波", "长沙", "昆明", "大理", "丽江", "拉萨", "哈尔滨", "郑州", "扬州", "香港", "澳门",
             "桂林", "黄山", "张家界", "嘉兴", "无锡", "合肥", "济南", "大连", "沈阳", "福州", "珠海", "佛山", "东莞",
             "海口", "贵阳", "南宁", "兰州", "乌鲁木齐", "呼和浩特", "石家庄", "太原", "南昌", "秦皇岛", "北戴河",
             "承德", "张家口", "崇礼", "廊坊", "保定", "雄安", "怀柔", "密云", "延庆", "台北"),
}
COUNTRY_ALIASES = {"南韩": "韩国", "大韩民国": "韩国", "德意志": "德国", "美利坚": "美国", "英格兰": "英国", "国内": "中国"}
CITY_ALIASES = {"济州岛": "济州", "普吉": "普吉岛", "京城": "北京", "魔都": "上海", "帝都": "北京", "鹏城": "深圳"}
CITY_COUNTRY = {city: country for country, cities in GAZETTEER.items() for city in cities}

# Synonyms -> canonical field values (used only when the value exists in the device's data).
PHOTO_TYPES = {
    "美食": ("美食", "吃的", "好吃的", "小吃", "料理", "甜点", "大餐", "餐厅"),
    "风景": ("风景", "景色", "自然风光", "风光", "山景", "湖景", "海景", "日落", "日出"),
    "街景": ("街景", "街道", "街头", "街拍"),
    "建筑": ("建筑", "教堂", "城堡", "宫殿", "古建"),
    "夜景": ("夜景", "夜晚", "晚上拍"),
    "人像": ("人像", "自拍", "合影", "合照"),
    "会议": ("会议", "展会", "会场", "开会"),
    "宠物": ("宠物", "小猫", "小狗"),
}
SPORT_ACTIVITIES = {
    "跑步": ("跑步", "跑了", "慢跑", "夜跑", "晨跑", "马拉松", "配速"),
    "骑行": ("骑行", "骑车", "单车", "自行车"),
    "游泳": ("游泳", "泳池"),
    "步行": ("步行", "走路", "散步", "徒步"),
    "羽毛球": ("羽毛球",),
    "力量训练": ("力量训练", "健身房", "撸铁", "举铁", "力量"),
    "睡眠": ("睡眠", "睡觉", "睡得", "深睡", "入睡", "失眠", "熬夜"),
    "日常": ("步数", "静息心率", "心率", "卡路里", "血氧", "压力"),
}
CAR_EVENTS = {
    "行程": ("行程", "开车去", "去过", "去了哪", "开去", "自驾", "驾车", "路线", "开到", "出行", "跑了多少公里", "开了多少"),
    "充电": ("充电", "充了", "补电", "充电桩", "快充", "慢充"),
    "导航": ("导航",),
    "保养": ("保养", "维修", "4S", "机油", "检修"),
    "提醒": ("提醒", "胎压", "年检", "违章", "故障", "告警"),
}
OVERVIEW_WORDS = ("情况", "总结", "统计", "汇总", "一共", "总共", "多少", "几次", "怎么样", "变化", "趋势", "整体",
                  "概况", "总计", "合计", "加起来")
SUMMARY_VALUES = ("周报", "月报")

# Relaxation order when the filters leave nothing (PLAN-v3 §3).
RELAX_ORDER = {
    "photo": (("cities", "trips"), ("countries",), ("types",), ("time",)),
    "sport": (("activities",), ("time",)),
    "car": (("cities",), ("events",), ("time",)),
    "mail": (("people",), ("orgs",), ("categories",), ("time",)),
}
FILTER_NAMES = {
    "orgs": "机构", "people": "联系人", "categories": "类别", "countries": "国家", "cities": "城市", "trips": "旅程",
    "types": "类型", "activities": "运动", "events": "事件", "time": "时间",
}
CATEGORY_NAMES = {"health": "健康", "finance": "财务", "school": "学校", "travel": "出行", "work": "工作", "client": "客户"}


def _num(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CN_NUM.get(text[1:], 0)
    if text.endswith("十"):
        return _CN_NUM.get(text[0], 1) * 10
    if len(text) == 3 and text[1] == "十":
        return _CN_NUM.get(text[0], 1) * 10 + _CN_NUM.get(text[2], 0)
    return _CN_NUM.get(text, 1)


def _month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def _add_months(day: dt.date, months: int) -> dt.date:
    y, m = divmod(day.month - 1 + months, 12)
    return dt.date(day.year + y, m + 1, 1)


def _month_span(year: int, month: int) -> tuple[dt.date, dt.date]:
    first = dt.date(year, month, 1)
    return first, _add_months(first, 1) - dt.timedelta(days=1)


def _clip(start: dt.date, end: dt.date, today: dt.date) -> tuple[dt.date, dt.date]:
    return start, min(end, today) if start <= today else end


def _year_word(word: str | None, today: dt.date) -> int | None:
    if not word:
        return None
    word = word.replace(" ", "")
    if word.startswith("今年") or word.startswith("本年"):
        return today.year
    if word.startswith("去年"):
        return today.year - 1
    if word.startswith("前年"):
        return today.year - 2
    m = re.match(r"(20[0-9]{2})", word)
    return int(m.group(1)) if m else None


_HOLIDAYS = {  # (start month, day) -> (end month, day)
    "国庆": ((10, 1), (10, 7)),
    "十一假期": ((10, 1), (10, 7)),
    "五一": ((5, 1), (5, 5)),
    "劳动节": ((5, 1), (5, 5)),
    "元旦": ((1, 1), (1, 3)),
    "暑假": ((7, 1), (8, 31)),
    "寒假": ((1, 15), (2, 28)),
    "圣诞": ((12, 24), (12, 26)),
}
_YEAR_PREFIX = r"(今年|本年|去年|前年|20[0-9]{2}\s*年)?\s*"


_VAGUE = ("下周", "明天", "后天", "下个月", "下月", "即将", "最近", "近期", "这几天", "近来")


def time_window(question: str, today: dt.date) -> tuple[dt.date | None, dt.date | None]:
    start, end, _ = time_rule(question, today)
    return start, end


def time_rule(question: str, today: dt.date) -> tuple[dt.date | None, dt.date | None, str | None]:
    """(start, end, rule); rule "vague" marks the guess for 最近 / 下周 … (recent messages)."""
    start, end = _time_window(question, today)
    if start is None:
        return None, None, None
    if _time_window(question, today, vague=False) == (None, None):
        return start, end, "vague"
    return start, end, "explicit"


def _time_window(question: str, today: dt.date, *, vague: bool = True) -> tuple[dt.date | None, dt.date | None]:
    q = question
    # 2025年9月 / 2025-09
    m = re.search(r"(20[0-9]{2})\s*年\s*(" + _NUM + r")\s*月", q) or re.search(r"(20[0-9]{2})[-/.](\d{1,2})(?!\d)", q)
    if m and 1 <= _num(m.group(2)) <= 12:
        return _month_span(int(m.group(1)), _num(m.group(2)))
    # 今年3月 / 去年九月
    m = re.search(r"(今年|本年|去年|前年)\s*(" + _NUM + r")\s*月", q)
    if m and 1 <= _num(m.group(2)) <= 12:
        return _month_span(_year_word(m.group(1), today), _num(m.group(2)))
    # 上半年 / 下半年
    m = re.search(_YEAR_PREFIX + r"(上半年|下半年)", q)
    if m:
        year = _year_word(m.group(1), today) or today.year
        if m.group(2) == "上半年":
            return _clip(dt.date(year, 1, 1), dt.date(year, 6, 30), today)
        return _clip(dt.date(year, 7, 1), dt.date(year, 12, 31), today)
    # Holidays: the latest one that has started (or the named year's).
    for word, ((m1, d1), (m2, d2)) in _HOLIDAYS.items():
        m = re.search(_YEAR_PREFIX + word, q)
        if m:
            year = _year_word(m.group(1), today)
            if year is None:
                year = today.year if dt.date(today.year, m1, d1) <= today else today.year - 1
            return _clip(dt.date(year, m1, d1), dt.date(year, m2, d2), today)
    # 过去三个月 / 最近一周 / 近半年 / 最近30天
    m = re.search(r"(?:过去|最近|近)\s*(" + _NUM + r"|半)\s*个?\s*(天|日|周|星期|礼拜|月|年)", q)
    if m:
        n, unit = m.group(1), m.group(2)
        days = {"天": 1, "日": 1, "周": 7, "星期": 7, "礼拜": 7, "月": 31, "年": 366}[unit]
        days = days / 2 if n == "半" else days * max(_num(n), 1)
        return today - dt.timedelta(days=int(round(days))), today
    weekday = today.weekday()
    monday = today - dt.timedelta(days=weekday)
    if re.search(r"上(?:个)?(?:周|星期|礼拜)末", q):
        return monday - dt.timedelta(days=2), monday - dt.timedelta(days=1)
    if re.search(r"(?:这|本)(?:个)?(?:周|星期|礼拜)末", q):
        return monday + dt.timedelta(days=5), monday + dt.timedelta(days=6)
    if re.search(r"上(?:个)?(?:周|星期|礼拜)", q):
        return monday - dt.timedelta(days=7), monday - dt.timedelta(days=1)
    if re.search(r"(?:这|本)(?:个)?(?:周|星期|礼拜)", q):
        return monday, today
    if "上个月" in q or "上月" in q:
        first = _add_months(_month_start(today), -1)
        return first, _month_start(today) - dt.timedelta(days=1)
    if "本月" in q or "这个月" in q or "这月" in q:
        return _month_start(today), today
    if "前天" in q:
        day = today - dt.timedelta(days=2)
        return day, day
    if "昨天" in q or "昨晚" in q:
        day = today - dt.timedelta(days=1)
        return day, day
    if "今天" in q or "今晚" in q or "今早" in q:
        return today, today
    if any(w in q for w in _VAGUE):
        # Messages about upcoming events were received recently.
        return (today - dt.timedelta(days=31), today) if vague else (None, None)
    years = [int(y) for y in re.findall(r"(20[0-9]{2})\s*年", q)]
    if years:
        return dt.date(min(years), 1, 1), dt.date(max(years), 12, 31)
    if "今年" in q or "本年" in q:
        return dt.date(today.year, 1, 1), today
    if "去年" in q:
        return dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)
    if "前年" in q:
        return dt.date(today.year - 2, 1, 1), dt.date(today.year - 2, 12, 31)
    # A bare month (九月 / 9月份): the latest one that has begun.
    m = re.search(r"(?<![0-9年个])(" + _NUM + r")\s*月", q)
    if m and 1 <= _num(m.group(1)) <= 12 and not re.search(r"(" + _NUM + r")\s*个\s*月", q):
        month = _num(m.group(1))
        year = today.year if month <= today.month else today.year - 1
        return _month_span(year, month)
    return None, None


def months_between(start: dt.date, end: dt.date) -> list[str]:
    out, cur = [], _month_start(start)
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = _add_months(cur, 1)
    return out


def _entities(bible: dict) -> tuple[list[str], list[str]]:
    orgs, people = set(), set()
    for key, value in (bible or {}).items():
        items = value if isinstance(value, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "org" in key or "client" in key or "partner" in key or "vendor" in key or "service" in key:
                name = item.get("name") or item.get("org")
                if name:
                    orgs.add(name)
            if "people" in key or "colleague" in key or "contact" in key or "family" in key:
                name = item.get("name")
                if name and 2 <= len(name) <= 4:
                    people.add(name)
    return sorted(orgs, key=len, reverse=True), sorted(people, key=len, reverse=True)


def _clean(text: str) -> str:
    return "".join(text.split()).strip("？?。.!！")


def _hits(question: str, words) -> list[str]:
    return [w for w in words if w in question]


def _canonical(question: str, table: dict, present: list[str] | None) -> list[str]:
    """Canonical values whose synonyms occur in the question, restricted to values in the data."""
    found = []
    for value, words in table.items():
        if present is not None and value not in present:
            continue
        if any(w in question for w in words) and value not in found:
            found.append(value)
    for value in present or []:  # the data's own value named verbatim
        if value in question and value not in found and value not in SUMMARY_VALUES:
            found.append(value)
    return found


def detect_kind(
    question: str, available=("mail",), entities: tuple[list, list] = ([], []), vocabulary: dict | None = None
) -> tuple[str, dict]:
    """(kind, scores): which device's data the question is about; mail when nothing says otherwise."""
    scores = {}
    for kind, groups in KIND_CUES.items():
        scores[kind] = sum(weight * len(_hits(question, words)) for weight, words in groups)
    orgs, people = entities
    scores["mail"] += 3 * (len(_hits(question, orgs)) + len(_hits(question, people)))
    # A place that occurs in a device's own data (a trip's city, a car destination) hints at that device.
    for kind, fields in (("photo", ("country", "city", "trip")), ("car", ("city",))):
        vocab = (vocabulary or {}).get(kind) or {}
        if any(v and v in question for f in fields for v in vocab.get(f) or []):
            scores[kind] += 1
    best = max(KIND_ORDER, key=lambda k: (scores[k] if k in available else -1, -KIND_ORDER.index(k)))
    if best not in available or scores.get(best, 0) <= 0:
        best = "mail"
    return best, scores


def places(question: str, vocab: dict | None = None) -> tuple[list[str], list[str]]:
    """(countries, cities) named in the question; data values first, then the gazetteer."""
    vocab = vocab or {}
    q = question
    for alias, city in CITY_ALIASES.items():
        q = q.replace(alias, city) if alias in q and city not in q else q
    cities: list[str] = []
    for city in list(vocab.get("city") or []) + sorted(CITY_COUNTRY, key=len, reverse=True):
        if city and city in q and city not in cities and not any(city in c for c in cities):
            cities.append(city)
    countries: list[str] = []
    for alias, country in COUNTRY_ALIASES.items():
        if alias in q and country not in countries:
            countries.append(country)
    for country in list(vocab.get("country") or []) + list(GAZETTEER):
        if country and country in q and country not in countries:
            countries.append(country)
    data_cities = set(vocab.get("city") or [])
    for city in cities:  # a city implies its country (kept so relaxation can fall back to it)
        if city in data_cities:  # the data's own pairing; none if its records carry no country
            country = (vocab.get("city_country") or {}).get(city)
        else:
            country = CITY_COUNTRY.get(city)
        if country and country not in countries and not (country == "中国" and countries):
            countries.append(country)

    def where(name: str) -> int:
        pos = q.find(name)
        return pos if pos >= 0 else len(q)

    cities.sort(key=where)  # in the order the question names them
    named = sorted([c for c in countries if c in q], key=where)
    countries = named + [c for c in countries if c not in named]
    return countries[:3], cities[:3]


def plan_question(question: str, presets: list[dict], bible: dict, catalog=None) -> dict:
    """The device's plan for one question (``catalog``: the device data, None = mail only)."""
    clean = _clean(question)
    for preset in presets:
        if _clean(preset["question"]) == clean:
            plan = dict(preset["plan"])
            plan.setdefault("semantic", preset["question"])
            plan.setdefault("kind", preset.get("kind") or "mail")
            plan["preset"] = preset["id"]
            return finalize(plan)
    today = config.AS_OF.date()
    orgs, people = _entities(bible)
    available = tuple(catalog.kinds) if catalog is not None else ("mail",)
    vocabulary = catalog.vocabulary if catalog is not None else {}
    kind, scores = detect_kind(question, available, (orgs, people), vocabulary)
    start, end = time_window(question, today)
    plan: dict = {"kind": kind, "semantic": question}
    if kind == "mail":
        plan.update(
            orgs=[o for o in orgs if o in question][:3],
            people=[p for p in people if p in question][:3],
            categories=[c for c, words in CATEGORY_WORDS.items() if any(w in question for w in words)][:2],
        )
        if plan["orgs"]:
            # An explicit organisation is a much sharper filter than a topical category.
            plan["categories"] = []
        known = scores["mail"] > 0 or plan["orgs"] or plan["people"] or plan["categories"]
        if not known:
            # Nothing says what the question is about: search everything by meaning, keeping
            # only a period the user named explicitly (not the vague "最近" fallback).
            plan["unknown"] = True
            if not _explicit_period(question):
                start = end = None
    else:
        vocab = dict(catalog.vocabulary.get(kind, {})) if catalog is not None else {}
        if kind == "photo":
            vocab["city_country"] = _city_country(catalog)
            countries, cities = places(question, vocab)
            plan["countries"], plan["cities"] = countries, cities
            plan["trips"] = [t for t in vocab.get("trip") or [] if t and t in question][:2]
            plan["types"] = _canonical(question, PHOTO_TYPES, vocab.get("type"))[:2]
        elif kind == "sport":
            acts = _canonical(question, SPORT_ACTIVITIES, vocab.get("activity"))[:3]
            if acts and any(w in question for w in OVERVIEW_WORDS):
                acts += [v for v in SUMMARY_VALUES if v in (vocab.get("activity") or SUMMARY_VALUES)]
            plan["activities"] = acts
        elif kind == "car":
            events = _canonical(question, CAR_EVENTS, vocab.get("event"))[:3]
            if events and any(w in question for w in OVERVIEW_WORDS):
                events += [v for v in SUMMARY_VALUES if v in (vocab.get("event") or SUMMARY_VALUES)]
            plan["events"] = events
            plan["cities"] = places(question, {"city": vocab.get("city") or []})[1]
    plan["date_from"] = start.isoformat() if start else None
    plan["date_to"] = end.isoformat() if end else None
    return finalize(plan)


def _explicit_period(question: str) -> bool:
    return time_rule(question, config.AS_OF.date())[2] == "explicit"


def _city_country(catalog) -> dict:
    out = {}
    if catalog is not None and "photo" in getattr(catalog, "sets", {}):
        for rec in catalog.sets["photo"].records:
            place = rec.get("place") or {}
            fields = rec.get("fields") or {}
            city = fields.get("city") or place.get("city")
            country = fields.get("country") or place.get("country")
            if city and country:
                out.setdefault(city, country)
    return out


def date_cover(start: dt.date, end: dt.date, today: dt.date | None = None) -> list[list[str]]:
    """Few year / quarter / month / day labels whose union is exactly [start, end].

    Every record carries all four labels, so a 23-month window costs a handful of
    SSE tokens; partial months at the edges use day labels, so HPIS selects
    exactly the window (no in-window record is lost to a coarse label).  Nothing
    is dated after today, so a window that reaches today may end with a coarse
    label that runs past it.
    """
    today = today or config.AS_OF.date()
    open_end = end >= today
    last = min(end, today)
    cover, day = [], start
    while day <= last:
        units = []
        if day.month == 1 and day.day == 1:
            units.append(("year", dt.date(day.year, 12, 31), str(day.year)))
        if day.day == 1 and day.month in (1, 4, 7, 10):
            units.append(("quarter", _add_months(day, 3) - dt.timedelta(days=1), f"{day.year}Q{(day.month - 1) // 3 + 1}"))
        if day.day == 1:
            units.append(("month", _add_months(day, 1) - dt.timedelta(days=1), day.strftime("%Y-%m")))
        for field, unit_end, value in units:  # the largest aligned unit that fits
            if unit_end <= end or open_end:
                cover.append([field, value])
                day = unit_end + dt.timedelta(days=1)
                break
        else:
            cover.append(["day", day.isoformat()])
            day += dt.timedelta(days=1)
    return cover


def finalize(plan: dict) -> dict:
    plan = {k: v for k, v in plan.items()}
    kind = plan.get("kind") or "mail"
    if kind not in KIND_KEYS:
        kind = "mail"
    plan["kind"] = kind
    plan["device"] = DEVICE_OF_KIND[kind]
    for key in ("orgs", "people", "categories"):
        plan[key] = list(plan.get(key) or [])
    for key in KIND_KEYS[kind]:
        plan[key] = list(plan.get(key) or [])
    for key, _ in FILTER_KEYS:  # a filter the kind does not index would match nothing
        if key not in KIND_KEYS[kind] and key not in ("orgs", "categories") and key in plan:
            plan.pop(key)
    if kind != "mail":
        plan["orgs"], plan["categories"], plan["people"] = [], [], []
    plan.setdefault("relaxed", [])
    start = plan.get("date_from")
    end = plan.get("date_to")
    if start and end:
        plan["date_cover"] = date_cover(dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10]))
    elif start or end:
        # A one-sided window: close it with the data's own bounds.
        lo = dt.date.fromisoformat(start[:10]) if start else dt.date(2000, 1, 1)
        hi = dt.date.fromisoformat(end[:10]) if end else config.AS_OF.date()
        plan["date_from"], plan["date_to"] = lo.isoformat(), hi.isoformat()
        plan["date_cover"] = date_cover(lo, hi)
    else:
        plan["date_cover"] = []
    return plan


def has_filters(plan: dict) -> bool:
    return bool(plan.get("date_cover")) or any(plan.get(key) for key, _ in FILTER_KEYS) or bool(plan.get("people"))


def _values(plan: dict, key: str) -> str:
    if key == "time":
        return f"{plan.get('date_from', '')[:10]} 至 {plan.get('date_to', '')[:10]}"
    values = plan.get(key) or []
    if key == "categories":
        values = [CATEGORY_NAMES.get(v, v) for v in values]
    return "、".join(values)


def _without(plan: dict, names: list[str]) -> dict:
    plan = dict(plan)
    for key in names:
        if key == "time":
            plan["date_from"] = plan["date_to"] = None
        else:
            plan[key] = []
    return finalize(plan)


def relax(plan: dict, count) -> dict:
    """Drop filters in a fixed order until ``count(plan)`` finds at least one record.

    Filters dropped on the way that were not the reason for the empty result are
    put back (the 2023 photos of Munich become Munich photos of any year, not
    every photo of 2023).  Returns the final plan with ``matched`` (records it
    selects on the device), ``relaxed`` (dropped filter names) and ``dropped``
    ([name, label, values]).
    """
    kind = plan.get("kind") or "mail"
    original = plan
    n = count(plan)
    steps: list[list[str]] = []
    for step in RELAX_ORDER.get(kind, ()):
        if n > 0:
            break
        names = [k for k in step if (plan.get("date_from") if k == "time" else plan.get(k))]
        if not names:
            continue
        plan = _without(plan, names)
        steps.append(names)
        n = count(plan)
    if n > 0 and len(steps) > 1:
        keep = list(steps)
        for step in steps[:-1]:  # most specific first
            trial = _without(original, [k for s in keep if s is not step for k in s])
            m = count(trial)
            if m > 0:
                keep.remove(step)
                plan, n = trial, m
        steps = keep
    relaxed = [k for s in steps for k in s]
    plan = dict(plan)
    plan["relaxed"] = list(original.get("relaxed") or []) + relaxed
    plan["dropped"] = [[key, FILTER_NAMES[key], _values(original, key)] for key in relaxed]
    plan["matched"] = int(n)
    return plan


def relax_notice(plan: dict) -> str | None:
    """Banner text for a relaxed plan (None when nothing was dropped)."""
    dropped = plan.get("dropped") or []
    if not dropped:
        return None
    label = KIND_LABEL.get(plan.get("kind") or "mail", "记录")
    what = "，".join(f"{name}：{values}" for _, name, values in dropped if values)
    names = "、".join(name for _, name, _ in dropped)
    if plan.get("matched"):
        return f"没有找到符合「{what}」的{label}，已去掉{names}条件，按语义找最接近的结果。"
    return f"没有找到符合「{what}」的{label}，本地数据中也没有可用的记录。"


def model_note(plan: dict) -> str | None:
    """What the model is told besides the evidence (both lanes get the same text).

    Photos, watch and car records: the conditions the device searched with, so the model
    can judge dates and places.  Any kind: when filters were relaxed, say plainly what
    was not found.  Mail without relaxation gets nothing (its prompt is unchanged).
    """
    kind = plan.get("kind") or "mail"
    notes = []
    if kind != "mail":
        parts = []
        if plan.get("date_from") and plan.get("date_to"):
            parts.append(f"时间 {plan['date_from'][:10]} 至 {plan['date_to'][:10]}")
        for key, label in (("countries", "国家"), ("cities", "城市"), ("trips", "旅程"), ("types", "类型"),
                           ("activities", "运动"), ("events", "事件")):
            if plan.get(key):
                parts.append(f"{label} {'、'.join(plan[key])}")
        if parts:
            notes.append("检索条件：设备已按" + "；".join(parts) + " 筛选，下面的证据都符合这些条件。")
    dropped = plan.get("dropped") or []
    if dropped:
        label = KIND_LABEL.get(kind, "记录")
        what = "，".join(f"{name}：{values}" for _, name, values in dropped if values)
        notes.append(
            f"检索说明：没有找到符合「{what}」的{label}。下面的证据是去掉这些条件后按语义检索到的最接近结果。"
            f"回答时先明确告诉用户没有找到符合这些条件的{label}，再简要介绍这些结果。"
        )
    return "\n".join(notes) or None


def describe(plan: dict) -> list[dict]:
    """Human-readable filter chips (shown in the UI as what the device encrypted)."""
    chips = []
    kind = plan.get("kind") or "mail"
    if kind != "mail":
        from .devices import BY_KIND

        info = BY_KIND[kind]
        chips.append({"kind": "数据", "value": f"{info.label} · {info.kind_label}"})
    for org in plan.get("orgs", []):
        chips.append({"kind": "机构", "value": org})
    for person in plan.get("people", []):
        chips.append({"kind": "联系人", "value": person})
    for key, label in (("countries", "国家"), ("cities", "城市"), ("trips", "旅程"), ("types", "类型"),
                       ("activities", "运动"), ("events", "事件")):
        for value in plan.get(key) or []:
            chips.append({"kind": label, "value": value})
    if plan.get("date_from") and plan.get("date_to"):
        chips.append({"kind": "时间", "value": f"{plan['date_from'][:10]} 至 {plan['date_to'][:10]}"})
    for cat in plan.get("categories", []):
        chips.append({"kind": "类别", "value": CATEGORY_NAMES.get(cat, cat)})
    chips.append({"kind": "语义", "value": plan.get("semantic", "")[:40]})
    return chips
