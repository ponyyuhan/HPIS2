"""Dates, working days and Chinese public holidays for the mailbox timeline."""

from __future__ import annotations

import datetime as dt
import random

TZ = dt.timezone(dt.timedelta(hours=8))
START = dt.date(2024, 1, 1)
END = dt.date(2026, 11, 20)
AS_OF = dt.datetime(2026, 11, 20, 18, 0, tzinfo=TZ)
WEEKDAY_CN = "一二三四五六日"


def _span(a: str, b: str) -> list[dt.date]:
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)]


HOLIDAYS = set()
for a, b in [
    ("2024-01-01", "2024-01-01"), ("2024-02-10", "2024-02-17"), ("2024-04-04", "2024-04-06"),
    ("2024-05-01", "2024-05-05"), ("2024-06-10", "2024-06-10"), ("2024-09-15", "2024-09-17"),
    ("2024-10-01", "2024-10-07"),
    ("2025-01-01", "2025-01-01"), ("2025-01-28", "2025-02-04"), ("2025-04-04", "2025-04-06"),
    ("2025-05-01", "2025-05-05"), ("2025-05-31", "2025-06-02"), ("2025-10-01", "2025-10-08"),
    ("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"), ("2026-04-04", "2026-04-06"),
    ("2026-05-01", "2026-05-05"), ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
]:
    HOLIDAYS.update(_span(a, b))

MAKEUP = {dt.date.fromisoformat(x) for x in [
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


def days(lo: dt.date = START, hi: dt.date = END) -> list[dt.date]:
    return [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]


ALL_DAYS = days()
WORKDAYS = [d for d in ALL_DAYS if is_workday(d)]


def clamp(d: dt.date, lo: dt.date = START, hi: dt.date = END) -> dt.date:
    return max(lo, min(hi, d))


def rand_workday(rng: random.Random, lo: dt.date, hi: dt.date) -> dt.date:
    lo, hi = clamp(lo), clamp(hi)
    if hi < lo:
        lo, hi = hi, lo
    pool = [d for d in days(lo, hi) if is_workday(d)]
    if not pool:
        pool = days(lo, hi)
    return rng.choice(pool)


def rand_day(rng: random.Random, lo: dt.date, hi: dt.date) -> dt.date:
    lo, hi = clamp(lo), clamp(hi)
    return lo + dt.timedelta(days=rng.randrange((hi - lo).days + 1))


def next_workday(d: dt.date, n: int = 1) -> dt.date:
    step = 1 if n >= 0 else -1
    k = abs(n)
    while k:
        d += dt.timedelta(days=step)
        if is_workday(d):
            k -= 1
    return d


def work_time(rng: random.Random) -> tuple[int, int]:
    """A plausible office-hours send time (with some early/late outliers)."""
    r = rng.random()
    if r < 0.04:
        h = rng.choice([7, 8])
    elif r < 0.90:
        h = rng.choices([9, 10, 11, 12, 13, 14, 15, 16, 17, 18], weights=[8, 14, 12, 4, 5, 11, 12, 11, 9, 5])[0]
    elif r < 0.97:
        h = rng.choice([19, 20, 21])
    else:
        h = rng.choice([22, 23])
    return h, rng.randrange(60)


def leisure_time(rng: random.Random) -> tuple[int, int]:
    h = rng.choices([7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23],
                    weights=[2, 3, 4, 5, 5, 6, 5, 4, 4, 4, 4, 5, 7, 9, 9, 6, 2])[0]
    return h, rng.randrange(60)


def at(d: dt.date, h: int, m: int, s: int = 0) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, h, m, s, tzinfo=TZ)


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def cn_date(d: dt.date | dt.datetime) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def cn_full(t: dt.datetime) -> str:
    return f"{t.year}年{t.month}月{t.day}日（星期{WEEKDAY_CN[t.weekday()]}） {t.hour:02d}:{t.minute:02d}"


def weekday_cn(d: dt.date) -> str:
    return "周" + WEEKDAY_CN[d.weekday()]
