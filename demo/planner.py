"""On-device query understanding: question -> structured filters + semantic text.

The device turns the filters into SSE tokens (HMAC labels) and the semantic text
into an embedding.  Every lane applies the *same* plan so that the evidence (and
therefore the answer) differs only by where and how the search runs.
"""

from __future__ import annotations

import datetime as dt
import re

from . import config

CATEGORY_WORDS = {
    "health": ("体检", "化验", "血脂", "尿酸", "血糖", "病历", "复查", "医生"),
    "finance": ("账单", "信用卡", "还款", "消费", "流水"),
    "school": ("学校", "家长会", "班主任", "孩子", "老师通知"),
    "travel": ("航班", "机票", "酒店", "行程", "出差"),
}
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0}


def _num(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CN_NUM.get(text[1:], 0)
    if text.endswith("十"):
        return _CN_NUM.get(text[0], 1) * 10
    return _CN_NUM.get(text, 1)


def _month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def _add_months(day: dt.date, months: int) -> dt.date:
    y, m = divmod(day.month - 1 + months, 12)
    return dt.date(day.year + y, m + 1, 1)


def time_window(question: str, today: dt.date) -> tuple[dt.date | None, dt.date | None]:
    q = question
    m = re.search(r"(?:过去|最近|近)([0-9一二两三四五六七八九十半]+)个?(天|周|星期|月|年)", q)
    if m:
        n, unit = _num(m.group(1)), m.group(2)
        days = {"天": 1, "周": 7, "星期": 7, "月": 31, "年": 366}[unit] * max(n, 1)
        return today - dt.timedelta(days=days), today
    if "上个月" in q or "上月" in q:
        first = _add_months(_month_start(today), -1)
        return first, _month_start(today) - dt.timedelta(days=1)
    if "本月" in q or "这个月" in q:
        return _month_start(today), today
    if any(w in q for w in ("下周", "明天", "后天", "下个月", "下月", "即将", "最近")):
        # Messages about upcoming events were received recently.
        return today - dt.timedelta(days=31), today
    years = [int(y) for y in re.findall(r"(20[0-9]{2})\s*年", q)]
    if years:
        return dt.date(min(years), 1, 1), dt.date(max(years), 12, 31)
    if "今年" in q:
        return dt.date(today.year, 1, 1), today
    if "去年" in q:
        return dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)
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


def plan_question(question: str, presets: list[dict], bible: dict) -> dict:
    clean = "".join(question.split()).strip("？?。.!！")
    for preset in presets:
        if "".join(preset["question"].split()).strip("？?。.!！") == clean:
            plan = dict(preset["plan"])
            plan.setdefault("semantic", preset["question"])
            plan["preset"] = preset["id"]
            return finalize(plan)
    today = config.AS_OF.date()
    start, end = time_window(question, today)
    orgs, people = _entities(bible)
    plan = {
        "orgs": [o for o in orgs if o in question][:3],
        "people": [p for p in people if p in question][:3],
        "categories": [c for c, words in CATEGORY_WORDS.items() if any(w in question for w in words)][:2],
        "date_from": start.isoformat() if start else None,
        "date_to": end.isoformat() if end else None,
        "semantic": question,
    }
    if plan["orgs"]:
        # An explicit organisation is a much sharper filter than a topical category.
        plan["categories"] = []
    return finalize(plan)


def date_cover(start: dt.date, end: dt.date) -> list[list[str]]:
    """Fewest year / quarter / month labels whose union covers [start, end].

    Every email carries all three labels, so a 23-month window costs 6 SSE tokens
    instead of 23.  The cover may include a few extra days at the edges; exact
    dates are re-checked after decryption.
    """
    by_year: dict[str, list[int]] = {}
    for m in months_between(start, end):
        by_year.setdefault(m[:4], []).append(int(m[5:]))
    cover = []
    for year, months in by_year.items():
        if len(months) == 12:
            cover.append(["year", year])
            continue
        left = set(months)
        for q in range(1, 5):
            span = {3 * q - 2, 3 * q - 1, 3 * q}
            if span <= left:
                cover.append(["quarter", f"{year}Q{q}"])
                left -= span
        cover.extend(["month", f"{year}-{m:02d}"] for m in sorted(left))
    return cover


def finalize(plan: dict) -> dict:
    plan = {k: v for k, v in plan.items()}
    for key in ("orgs", "people", "categories"):
        plan[key] = list(plan.get(key) or [])
    start = plan.get("date_from")
    end = plan.get("date_to")
    if start and end:
        plan["date_cover"] = date_cover(dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10]))
    else:
        plan["date_cover"] = []
    return plan


def describe(plan: dict) -> list[dict]:
    """Human-readable filter chips (shown in the UI as what the device encrypted)."""
    chips = []
    for org in plan.get("orgs", []):
        chips.append({"kind": "机构", "value": org})
    for person in plan.get("people", []):
        chips.append({"kind": "联系人", "value": person})
    if plan.get("date_from") and plan.get("date_to"):
        chips.append({"kind": "时间", "value": f"{plan['date_from'][:10]} 至 {plan['date_to'][:10]}"})
    names = {"health": "健康", "finance": "财务", "school": "学校", "travel": "出行", "work": "工作", "client": "客户"}
    for cat in plan.get("categories", []):
        chips.append({"kind": "类别", "value": names.get(cat, cat)})
    chips.append({"kind": "语义", "value": plan.get("semantic", "")[:40]})
    return chips
