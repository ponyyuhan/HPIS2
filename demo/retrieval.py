"""Index fields and the plaintext search used by the comparison lanes.

All lanes apply the same plan: exact filters (organisation, month window,
category, people involved) followed by cosine ranking over the same
512-d embeddings.  HPIS 2.0 evaluates the filters as SSE tokens and the ranking
over the encrypted vector index; the plaintext lanes evaluate them directly.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from hpis.codec import Labels, field_label

OWNER_NAMES = {"林舟"}


PEOPLE_SLOTS = 4


def people_of(mail: dict) -> list[str]:
    """Up to four people involved in the email (owner excluded), in a stable order."""
    names = []
    facets = mail.get("facets") or {}
    for name in facets.get("people") or []:
        if isinstance(name, dict):
            name = name.get("name")
        if name and name not in OWNER_NAMES and name not in names:
            names.append(name)
    for person in [mail.get("from") or {}] + (mail.get("to") or []) + (mail.get("cc") or []):
        name = (person or {}).get("name") or ""
        if name and name not in OWNER_NAMES and name not in names:
            names.append(name)
    return names[:PEOPLE_SLOTS]


def org_of(mail: dict) -> str:
    facets = mail.get("facets") or {}
    return facets.get("org") or (mail.get("from") or {}).get("org") or ""


def index_fields(mail: dict) -> dict:
    year, month = mail["date"][:4], int(mail["date"][5:7])
    fields = {
        "org": org_of(mail),
        "year": year,
        "quarter": f"{year}Q{(month - 1) // 3 + 1}",
        "month": mail["date"][:7],
        "category": mail.get("category") or "",
    }
    for i, name in enumerate(people_of(mail)):
        fields[f"p{i}"] = name
    return fields


def sse_groups(labels: Labels, plan: dict) -> list[list[bytes]]:
    """One OR-group per filter dimension; HPIS intersects the groups."""
    groups = []
    for field, key in (("org", "orgs"), ("category", "categories")):
        values = plan.get(key) or []
        if values:
            groups.append([labels.encode(field_label(field, v)) for v in values])
    cover = plan.get("date_cover") or []
    if cover:
        groups.append([labels.encode(field_label(field, value)) for field, value in cover])
    people = plan.get("people") or []
    if people:
        groups.append([labels.encode(field_label(f"p{i}", p)) for p in people for i in range(PEOPLE_SLOTS)])
    return groups


def in_window(date_text: str, plan: dict) -> bool:
    start, end = plan.get("date_from"), plan.get("date_to")
    day = date_text[:10]
    if start and day < start[:10]:
        return False
    if end and day > end[:10]:
        return False
    return True


def matches(fields: dict, plan: dict) -> bool:
    for field, key in (("org", "orgs"), ("category", "categories")):
        values = plan.get(key) or []
        if values and fields.get(field) not in values:
            return False
    cover = plan.get("date_cover") or []
    if cover and not any(fields.get(field) == value for field, value in cover):
        return False
    people = plan.get("people") or []
    if people and not any(fields.get(f"p{i}") in people for i in range(PEOPLE_SLOTS)):
        return False
    return True


def top_k(ids: np.ndarray, vectors: np.ndarray, query: np.ndarray, k: int) -> np.ndarray:
    """Exact cosine Top-K with id tie-break — the very kernel HPIS runs on unit vectors.

    Reusing hpis' own shortlist + exact ordering guarantees that the plaintext
    lanes and the encrypted (TEE) lane return byte-identical evidence lists.
    """
    from hpis.exact_order import exact_nearest
    from hpis.store import cosine_distances

    if len(ids) == 0:
        return np.empty(0, dtype=np.int64)
    data = np.ascontiguousarray(vectors, dtype=np.float32)
    q = np.ascontiguousarray(query, dtype=np.float32)
    dots = data @ q
    dim = data.shape[1]
    if len(dots) > k:
        errors = 8 * dim * np.finfo(np.float32).eps
        kth = np.partition(dots, len(dots) - k)[-k]
        shortlist = np.flatnonzero(dots >= kth - errors)
    else:
        shortlist = np.arange(len(dots))
    distances = cosine_distances(data[shortlist], q)
    order = exact_nearest(ids[shortlist], distances, k, q, data[shortlist])
    return shortlist[np.asarray(order, dtype=np.int64)]


def post_filter(records: list[tuple[int, dict]], plan: dict, limit: int) -> list[tuple[int, dict]]:
    kept = [(rid, mail) for rid, mail in records if in_window(mail.get("date", ""), plan)]
    return kept[:limit]


def today() -> dt.date:
    from . import config

    return config.AS_OF.date()
