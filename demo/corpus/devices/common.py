"""Paths, time helpers and small utilities shared by the device-data builders.

Only the standard library is used here, so every stage (including the ones that
run in the throwaway image venv under ``python -I``) can import it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # repository root
OUT = Path(os.environ.get("HPIS_DEVICES_OUT") or ROOT / "demo" / "data" / "devices")
# Downloads (untrusted), the image venv, LLM response cache and intermediate files.
BUILD = Path(os.environ.get("HPIS_DEVICES_BUILD") or Path.home() / ".cache" / "hpis2-devices-build")
DOWNLOADS = BUILD / "downloads"
WORK = BUILD / "work"
LLM_CACHE = BUILD / "cache" / "llm"

AS_OF = dt.datetime(2026, 11, 20, 18, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
START = dt.date(2024, 1, 1)
END = dt.date(2026, 11, 20)
CN = dt.timezone(dt.timedelta(hours=8))
WEEKDAY_CN = "一二三四五六日"

KINDS = {"photo": "phone", "sport": "watch", "car": "car"}


def tz(hours: int) -> dt.timezone:
    return dt.timezone(dt.timedelta(hours=hours))


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def weekday_cn(d: dt.date) -> str:
    return "周" + WEEKDAY_CN[d.weekday()]


def cn_date(d: dt.date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def cn_md(d: dt.date) -> str:
    return f"{d.month}月{d.day}日"


def cn_dt(t: dt.datetime) -> str:
    return f"{t.year}年{t.month}月{t.day}日（{weekday_cn(t.date())}）{t.hour:02d}:{t.minute:02d}"


def hm(minutes: float) -> str:
    """Minutes as 'X小时Y分' / 'Y分钟'."""
    m = int(round(minutes))
    h, m = divmod(m, 60)
    if h and m:
        return f"{h}小时{m}分"
    if h:
        return f"{h}小时"
    return f"{m}分钟"


def hms(seconds: float) -> str:
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pace(sec_per_km: float) -> str:
    s = int(round(sec_per_km))
    return f"{s // 60}'{s % 60:02d}\""


def daterange(a: dt.date, b: dt.date):
    for i in range((b - a).days + 1):
        yield a + dt.timedelta(days=i)


def week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def month_end(d: dt.date) -> dt.date:
    nxt = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def fmt_num(x: float, nd: int = 1) -> str:
    """Fixed decimals without trailing noise: 12.0 -> '12.0', 1234.5 -> '1234.5'."""
    return f"{x:.{nd}f}"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", "utf-8")
    tmp.replace(path)


def visible_files(folder: Path, suffix: str) -> list[Path]:
    """Files with a suffix, skipping macOS '._*' sidecars (exFAT/SMB volumes)."""
    return sorted(p for p in folder.glob(f"*{suffix}") if not p.name.startswith("._"))
