"""Per-kind record helpers shared by the device, HPIC and the ordinary cloud.

Four kinds of personal data, one per device (PLAN-v3 §1):

  mail   PC     100,000 emails      demo/data/mailbox.jsonl
  photo  手机   the photo album     demo/data/devices/photo.jsonl + photos/img|thumb/{id}.jpg
  sport  手表   watch records       demo/data/devices/sport.jsonl
  car    车机   car records         demo/data/devices/car.jsonl

Every helper takes the kind first.  HPIC and the ordinary cloud call the very
same helpers, so the two lanes hand the model identical evidence; only where
and how the search runs differs.
"""

from __future__ import annotations

import re
from typing import Any

from . import retrieval

KINDS = ("mail", "photo", "sport", "car")
KIND_LABEL = {"mail": "邮件", "photo": "照片", "sport": "运动健康", "car": "用车出行"}
UNIT = {"mail": "封", "photo": "张", "sport": "条", "car": "条"}
OWNER = "林舟"

# SSE label fields per kind, besides the date labels (year / quarter / month).
KIND_FIELDS = {
    "photo": ("country", "city", "type", "trip"),
    "sport": ("activity",),
    "car": ("event", "city"),
}
FILTER_KEYS = retrieval.FILTER_KEYS  # plan key -> SSE field


def when(date: str) -> str:
    """'2025-09-16T08:12:00+02:00' -> '2025-09-16 08:12' (local time of the record)."""
    return (date or "")[:16].replace("T", " ")


def _squeeze(text: Any) -> str:
    return " ".join(str(text or "").split())


def date_fields(date: str) -> dict:
    year, month = date[:4], int(date[5:7])
    return {"year": year, "quarter": f"{year}Q{(month - 1) // 3 + 1}", "month": date[:7], "day": date[:10]}


def index_fields(kind: str, rec: dict) -> dict:
    """What SSE indexes as HMAC labels for one record (plain strings here)."""
    if kind == "mail":
        return retrieval.index_fields(rec)
    out = date_fields(rec["date"])
    fields = rec.get("fields") or {}
    place = rec.get("place") or {}
    for name in KIND_FIELDS.get(kind, ()):
        value = fields.get(name)
        if value in (None, "") and name in ("country", "city"):
            value = place.get(name)
        if value in (None, "") and name in ("activity", "event"):
            value = rec.get(name)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value not in (None, ""):
            out[name] = str(value)
    return out


def place_text(rec: dict) -> str:
    place = rec.get("place") or {}
    where = " ".join(x for x in (place.get("country"), place.get("city")) if x)
    name = place.get("name") or ""
    if name and name not in where:
        where = f"{where} · {name}" if where else name
    return where


def photo_url(rid: int, size: str = "thumb") -> str:
    return f"/api/photo/{int(rid)}?size={size}"


def summary(kind: str, rec: dict, chars: int = 90) -> str:
    if kind == "photo":
        text = rec.get("caption") or rec.get("text") or ""
    else:
        text = rec.get("text") or rec.get("title") or ""
    text = _squeeze(text)
    return text if len(text) <= chars else text[: chars - 1] + "…"


def card(kind: str, rec: dict) -> dict:
    """Compact evidence item for the UI (mail keeps its own card)."""
    if kind == "mail":
        from .mailbox import card as mail_card

        return mail_card(rec)
    out = {
        "id": int(rec["id"]),
        "kind": kind,
        "title": rec.get("title") or "",
        "date": when(rec.get("date", "")),
        "summary": summary(kind, rec),
    }
    if kind == "photo":
        place = rec.get("place") or {}
        out["place"] = {k: place.get(k) for k in ("name", "country", "city", "lat", "lon") if place.get(k) is not None}
        out["place_text"] = place_text(rec)
        out["thumb"] = photo_url(rec["id"], "thumb")
        out["tags"] = list(rec.get("tags") or [])[:6]
    elif kind == "sport":
        out["activity"] = rec.get("activity") or (rec.get("fields") or {}).get("activity") or ""
    elif kind == "car":
        out["event"] = rec.get("event") or (rec.get("fields") or {}).get("event") or ""
    return out


_CAR_KEYS = (
    ("from", "起点"),
    ("to", "终点"),
    ("distance_km", "里程（公里）"),
    ("duration_min", "用时（分钟）"),
    ("energy_kwh", "电量（千瓦时）"),
)


def _metrics_line(metrics: Any) -> str:
    if not isinstance(metrics, dict) or not metrics:
        return ""
    parts = []
    for key, value in metrics.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        parts.append(f"{key}={value}")
    return "，".join(parts[:24])


def model_item(kind: str, rec: dict, n: int, *, image: bytes | None = None) -> dict:
    """Evidence item for the prompt builder (mail keeps its own layout)."""
    if kind == "mail":
        from .mailbox import model_text

        item = model_text(rec)
        item["n"] = n
        return item
    item = {
        "n": n,
        "id": int(rec["id"]),
        "kind": kind,
        "date": when(rec.get("date", "")),
        "title": rec.get("title") or "",
        "body": rec.get("text") or "",
    }
    if kind == "photo":
        item["place"] = place_text(rec)
        item["caption"] = rec.get("caption") or ""
        item["tags"] = list(rec.get("tags") or [])
        if image:
            item["image"] = image
    else:
        lines = []
        if kind == "car":
            for key, label in _CAR_KEYS:
                if rec.get(key) not in (None, ""):
                    lines.append(f"{label}：{rec[key]}")
        metrics = _metrics_line(rec.get("metrics"))
        if metrics:
            lines.append(f"数据：{metrics}")
        item["facts"] = lines
    return item


def evidence(kind: str, pairs: list[tuple[int, dict]], *, vision: bool = False) -> tuple[list[dict], list[dict]]:
    """(UI cards, model evidence) for the ranked records a lane hands the model."""
    cards, items = [], []
    for n, (_, rec) in enumerate(pairs, 1):
        cards.append(card(kind, rec))
        image = rec.get("image") if (kind == "photo" and vision) else None
        items.append(model_item(kind, rec, n, image=image if isinstance(image, (bytes, bytearray)) else None))
    return cards, items


# ----------------------------------------------------------------- embedding text

_FIELD_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")


def _lookup(rec: dict, path: str) -> Any:
    value: Any = rec
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def recipe_fields(recipe: Any, sample: dict | None = None) -> list[str]:
    """Record fields named by ``embeddings_{kind}.json`` "text" (a list, "title + text", or "text")."""
    if isinstance(recipe, (list, tuple)):
        names = [str(x) for x in recipe]
    elif isinstance(recipe, str):
        names = _FIELD_TOKEN.findall(recipe)
    else:
        names = []
    if sample is not None:
        names = [n for n in names if _lookup(sample, n) is not None]
    return names or ["text"]


def doc_text(kind: str, rec: dict, fields: list[str] | None = None) -> str:
    """The document text embedded for one record (identical on the device and in the cloud)."""
    if kind == "mail":
        from .embed import doc_text as mail_doc_text

        return mail_doc_text(rec)
    parts = []
    for name in fields or ["text"]:
        value = _lookup(rec, name)
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        if value not in (None, ""):
            parts.append(str(value))
    return "\n".join(parts) or rec.get("title") or ""


def lex_text(kind: str, rec: dict) -> str:
    """Text the ordinary cloud's lexical shortlist reads (headers + the start of the content)."""
    if kind == "mail":
        atts = " ".join(a.get("name", "") for a in (rec.get("attachments") or [])[:3])
        sender = rec.get("from") or {}
        head = " ".join(x for x in (sender.get("org"), sender.get("name")) if x)
        return f"{rec.get('subject', '')} {head} {_squeeze((rec.get('body') or '')[:300])} {atts}"
    fields = rec.get("fields") or {}
    parts = [rec.get("title"), rec.get("caption"), rec.get("text"), place_text(rec) if kind == "photo" else ""]
    parts += [" ".join(map(str, rec.get("tags") or []))]
    parts += [str(v) for v in fields.values() if isinstance(v, str)]
    for key in ("activity", "event", "from", "to"):
        if isinstance(rec.get(key), str):
            parts.append(rec[key])
    return " ".join(_squeeze(p) for p in parts if p)


# ----------------------------------------------------------------- real-name rows

DEVICE_LABEL = {"mail": ("pc", "PC"), "photo": ("phone", "手机"), "sport": ("watch", "手表"), "car": ("car", "车机")}
SOURCE_LABEL = {"photo": "手机相册", "sport": "手表", "car": "车机"}


def plain_row(kind: str, rec: dict, *, snippet_chars: int = 120) -> dict:
    """What an operator of the ordinary cloud reads for one uploaded record (real name)."""
    device, device_label = DEVICE_LABEL[kind]
    if kind == "mail":
        from .mailbox import card as mail_card

        row = mail_card(rec, snippet_chars=snippet_chars)
    else:
        text = rec.get("caption") if kind == "photo" else rec.get("text")
        snippet = _squeeze(text or rec.get("text") or "")
        if kind == "photo" and place_text(rec):
            snippet = f"{place_text(rec)} · {snippet}"
        row = {
            "id": int(rec["id"]),
            "date": when(rec.get("date", "")),
            "from": SOURCE_LABEL[kind],
            "subject": rec.get("title") or "",
            "snippet": snippet[:snippet_chars],
        }
        if kind == "photo":
            row["thumb"] = photo_url(rec["id"], "thumb")
    row.update(owner=OWNER, device=device, device_label=device_label, kind=kind, kind_label=KIND_LABEL[kind])
    return row
