"""Index fields and the plaintext search used by the comparison lanes.

All lanes apply the same plan: exact filters (organisation, month window,
category, people involved; for photos country / city / trip / type, for watch
records the activity, for car records the event and city) followed by cosine
ranking over the same 512-d embeddings.  HPIS 2.0 evaluates the filters as SSE
tokens and the ranking over the encrypted vector index; the plaintext lanes
evaluate them directly.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from hpis.codec import Labels, field_label

OWNER_NAMES = {"林舟"}


PEOPLE_SLOTS = 4

# Plan key -> index field, in the order the device builds its SSE token groups
# (mail plans only use the first two, so their requests are unchanged).
FILTER_KEYS = (
    ("orgs", "org"),
    ("categories", "category"),
    ("countries", "country"),
    ("cities", "city"),
    ("trips", "trip"),
    ("types", "type"),
    ("activities", "activity"),
    ("events", "event"),
)


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
        "day": mail["date"][:10],
        "category": mail.get("category") or "",
    }
    for i, name in enumerate(people_of(mail)):
        fields[f"p{i}"] = name
    return fields


def sse_groups(labels: Labels, plan: dict) -> list[list[bytes]]:
    """One OR-group per filter dimension; HPIS intersects the groups."""
    groups = []
    for key, field in FILTER_KEYS:
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
    for key, field in FILTER_KEYS:
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


# ----------------------------------------------------------------- lexical shortlist
# The ordinary cloud receives the raw collection with every question, so it has
# no standing index: it narrows the filtered records to a short list by word
# overlap (BM25 over character bigrams, the usual approach for Chinese) and
# computes vectors only for that list.

_STOP_CHARS = set("的了吗呢吧么我你他她它们这那哪啊呀")
_STOP_TERMS = {
    "一下", "总结", "最近", "有没", "没有", "情况", "多少", "时候", "分别", "一共", "是否", "告诉", "找到", "找一",
    "一封", "相关", "关于", "提到", "怎样", "如何", "哪里", "什么", "整理", "给出", "列出", "看看", "帮忙", "需要",
}
# Question filler that separates the content words (split first, so no bigram spans two words).
_FILLER = (
    "帮我", "帮忙", "请问", "给我", "告诉我", "一下", "总结", "整理", "列出", "看看", "查一下", "找一下", "有没有",
    "有哪些", "哪些", "什么", "怎么样", "怎么", "怎样", "如何", "多少", "最近", "近期", "关于", "有关", "相关",
    "提到", "分别", "一共", "是否", "以及", "或者", "邮件", "邮箱", "照片", "相片", "图片", "记录",
    "的", "了", "吗", "呢", "吧", "和", "与", "及", "我", "你",
    "他", "她", "它", "们", "在", "把", "被", "这", "那", "哪", "么", "啊", "呀",
)
_FILLER_RE = None


def query_terms(text: str) -> list[str]:
    """Content words of a question as character bigrams (plus short whole phrases and ASCII words)."""
    import re

    global _FILLER_RE
    if _FILLER_RE is None:
        _FILLER_RE = re.compile("|".join(map(re.escape, sorted(_FILLER, key=len, reverse=True))))
    terms: list[str] = []
    for run in re.findall(r"[一-鿿]+|[A-Za-z0-9][A-Za-z0-9.\-]*", text or ""):
        if run[0].isascii():
            if len(run) >= 2:
                terms.append(run.lower())
            continue
        for chunk in _FILLER_RE.split(run):
            if len(chunk) < 2:
                continue
            if 2 < len(chunk) <= 6:
                terms.append(chunk)  # the whole phrase scores extra when it occurs as is
            for i in range(len(chunk) - 1):
                pair = chunk[i : i + 2]
                if pair not in _STOP_TERMS and not (_STOP_CHARS & set(pair)):
                    terms.append(pair)
    return list(dict.fromkeys(terms))


def lexical_scores(texts: list[str], query: str, *, k1: float = 1.2, b: float = 0.75) -> np.ndarray:
    """BM25 score per text: term frequency saturates, rare terms weigh more, long texts less."""
    n = len(texts)
    scores = np.zeros(n, dtype=np.float64)
    terms = query_terms(query)
    if not n or not terms:
        return scores
    lengths = np.fromiter((len(t) for t in texts), dtype=np.float64, count=n)
    norm = k1 * (1 - b + b * lengths / max(1.0, float(lengths.mean())))
    blob = "\n".join(texts).lower()
    starts = np.cumsum([0] + [len(t) + 1 for t in texts[:-1]], dtype=np.int64)
    for term in terms:
        found = []
        pos = blob.find(term)
        while pos >= 0:
            found.append(pos)
            pos = blob.find(term, pos + 1)
        if not found:
            continue
        docs = np.searchsorted(starts, np.asarray(found, dtype=np.int64), side="right") - 1
        tf = np.bincount(docs, minlength=n).astype(np.float64)
        df = int(np.count_nonzero(tf))
        idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
        scores += idf * tf * (k1 + 1) / (tf + norm)
    return scores


def lexical_order(texts: list[str], query: str, limit: int) -> list[int]:
    """Indices of the best ``limit`` texts by lexical score, ties (and no match) newest first."""
    scores = lexical_scores(texts, query)
    order = np.lexsort((-np.arange(len(texts)), -scores))
    return [int(i) for i in order[:limit]]


def post_filter(records: list[tuple[int, dict]], plan: dict, limit: int) -> list[tuple[int, dict]]:
    kept = [(rid, mail) for rid, mail in records if in_window(mail.get("date", ""), plan)]
    return kept[:limit]


def today() -> dt.date:
    from . import config

    return config.AS_OF.date()
