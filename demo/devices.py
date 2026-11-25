"""The four devices of the story, their local data, and the device-side field index.

Each device keeps its own data and uploads it to HPIS under its own anonymous
AID with its own keys (PLAN-v3 §1):

  pc     PC    mail    demo/data/mailbox.jsonl (always present)
  phone  手机  photo   demo/data/devices/photo.jsonl + photos/
  watch  手表  sport   demo/data/devices/sport.jsonl
  car    车机  car     demo/data/devices/car.jsonl

The device folder is optional: without it the demo runs mail-only, exactly as
before.  ``LocalIndex`` is the device's own copy of the SSE fields; the planner
uses it to count matches before it seals a query, so a filter that would leave
nothing can be relaxed on the device (the cloud never sees the count).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from . import config, records
from .retrieval import FILTER_KEYS, PEOPLE_SLOTS


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    label: str
    kind: str
    kind_label: str
    tenant: str  # device folder name inside each HPIS deployment


DEVICES = (
    DeviceInfo("pc", "PC", "mail", "邮件", "default"),
    DeviceInfo("phone", "手机", "photo", "照片", "phone"),
    DeviceInfo("watch", "手表", "sport", "运动健康", "watch"),
    DeviceInfo("car", "车机", "car", "用车出行", "car"),
)
BY_ID = {d.id: d for d in DEVICES}
BY_KIND = {d.kind: d for d in DEVICES}
OTHER_KINDS = ("photo", "sport", "car")


def _stat(path: Path) -> list[int]:
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


class RecordSet:
    """One non-mail kind: ``{kind}.jsonl`` records and ``embeddings_{kind}.f32`` vectors."""

    def __init__(self, kind: str, folder: Path | None = None):
        if kind not in OTHER_KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        self.kind = kind
        self.folder = Path(folder or config.DEVICES)
        self.path = self.folder / f"{kind}.jsonl"
        self.records: list[dict] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    rec.setdefault("kind", kind)
                    self.records.append(rec)
        if not self.records:
            raise ValueError(f"{self.path.name} is empty")
        self.by_id = {int(r["id"]): r for r in self.records}
        meta = self.folder / f"embeddings_{kind}.json"
        self.meta = json.loads(meta.read_text("utf-8")) if meta.exists() else {}
        self.text_fields = records.recipe_fields(self.meta.get("text"), self.records[0])

    @staticmethod
    def available(kind: str, folder: Path | None = None) -> bool:
        path = Path(folder or config.DEVICES) / f"{kind}.jsonl"
        return path.is_file() and path.stat().st_size > 0

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def ids(self) -> list[int]:
        return [int(r["id"]) for r in self.records]

    def get(self, rid: int) -> dict:
        return self.by_id[int(rid)]

    def iter_lines(self):
        with open(self.path, "rb") as fh:
            for line in fh:
                if line.strip():
                    yield line

    def vectors(self) -> np.ndarray | None:
        """float32 [N, dim], row i = line i; None when the file is missing."""
        path = self.folder / f"embeddings_{self.kind}.f32"
        if not path.exists():
            return None
        raw = np.fromfile(path, dtype=np.float32)
        dim = int(self.meta.get("dim") or (raw.size // max(1, self.count)))
        if dim <= 0 or raw.size != self.count * dim:
            raise ValueError(f"{path.name} holds {raw.size} floats, expected {self.count} x {dim}")
        return raw.reshape(self.count, dim)

    def image_path(self, rid: int, size: str = "full") -> Path | None:
        if self.kind != "photo":
            return None
        rec = self.get(rid)
        rel = rec.get("thumb" if size == "thumb" else "file")
        candidates = [self.folder / rel] if rel else []
        candidates.append(self.folder / "photos" / ("thumb" if size == "thumb" else "img") / f"{int(rid)}.jpg")
        root = self.folder.resolve()
        for path in candidates:
            path = path.resolve()
            if path.is_file() and root in path.parents:
                return path
        return None

    def image_bytes(self, rid: int, size: str = "full") -> bytes | None:
        path = self.image_path(rid, size)
        return path.read_bytes() if path else None

    def signature(self) -> dict:
        sig = {"records": _stat(self.path)}
        for name in (f"embeddings_{self.kind}.f32", f"embeddings_{self.kind}.json"):
            if (self.folder / name).exists():
                sig[name] = _stat(self.folder / name)
        if self.kind == "photo":
            total = files = 0
            for rid in self.ids:
                path = self.image_path(rid, "full")
                if path:
                    total += path.stat().st_size
                    files += 1
            sig["images"] = [files, total]
        return sig

    @cached_property
    def summary(self) -> dict:
        dates = sorted(r["date"] for r in self.records if r.get("date"))
        text_bytes = sum(len(line) for line in self.iter_lines())
        image_bytes = 0
        if self.kind == "photo":
            for rid in self.ids:
                path = self.image_path(rid, "full")
                image_bytes += path.stat().st_size if path else 0
        return {
            "records": self.count,
            "text_bytes": text_bytes,
            "image_bytes": image_bytes,
            "bytes": text_bytes + image_bytes,
            "first": dates[0][:10] if dates else None,
            "last": dates[-1][:10] if dates else None,
        }

    def vocabulary(self) -> dict[str, list[str]]:
        """Distinct SSE field values of this kind (the planner matches questions against them)."""
        out: dict[str, set] = {}
        for rec in self.records:
            for key, value in records.index_fields(self.kind, rec).items():
                if key not in ("year", "quarter", "month"):
                    out.setdefault(key, set()).add(value)
        return {k: sorted(v, key=len, reverse=True) for k, v in out.items()}


class LocalIndex:
    """Columnar copy of one kind's SSE fields on the device: count what a plan matches."""

    def __init__(self, fields: list[dict], dates: list[str]):
        self.n = len(fields)
        names = sorted({k for f in fields for k in f})
        self.cols = {name: np.asarray([f.get(name, "") for f in fields], dtype=str) for name in names}
        self.days = np.asarray([(d or "")[:10] for d in dates], dtype="U10")

    @classmethod
    def from_arrays(cls, cols: dict, days: np.ndarray) -> "LocalIndex":
        self = cls.__new__(cls)
        self.cols, self.days, self.n = dict(cols), days, len(days)
        return self

    def mask(self, plan: dict) -> np.ndarray:
        mask = np.ones(self.n, dtype=bool)
        for key, field in FILTER_KEYS:
            values = plan.get(key) or []
            if values:
                col = self.cols.get(field)
                mask &= np.isin(col, list(values)) if col is not None else False
        cover = plan.get("date_cover") or []
        if cover:
            hit = np.zeros(self.n, dtype=bool)
            for field, value in cover:
                col = self.cols.get(field)
                if col is not None:
                    hit |= col == value
            mask &= hit
        people = plan.get("people") or []
        if people:
            hit = np.zeros(self.n, dtype=bool)
            for i in range(PEOPLE_SLOTS):
                col = self.cols.get(f"p{i}")
                if col is not None:
                    hit |= np.isin(col, list(people))
            mask &= hit
        if plan.get("date_from"):
            mask &= self.days >= plan["date_from"][:10]
        if plan.get("date_to"):
            mask &= self.days <= plan["date_to"][:10]
        return mask

    def count(self, plan: dict) -> int:
        return int(self.mask(plan).sum())


def _mail_index(mailbox) -> LocalIndex:
    """Mail fields for 100,000 emails take about a second to parse; cache them next to the mailbox index."""
    from .retrieval import index_fields

    st = mailbox.path.stat()
    cache = config.RUNTIME / "mailbox-index" / f"{st.st_size}-{int(st.st_mtime)}-fields-v1.npz"
    if cache.exists():
        try:
            data = np.load(cache, allow_pickle=False)
            cols = {k.removeprefix("col_"): data[k] for k in data.files if k.startswith("col_")}
            return LocalIndex.from_arrays(cols, data["days"])
        except (OSError, ValueError, KeyError):
            pass
    fields, dates = [], []
    for line in mailbox.iter_lines():
        mail = json.loads(line)
        fields.append(index_fields(mail))
        dates.append(mail["date"])
    index = LocalIndex(fields, dates)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp.npz")
        np.savez(tmp, days=index.days, **{f"col_{k}": v for k, v in index.cols.items()})
        tmp.replace(cache)
    except OSError:
        pass
    return index


class Catalog:
    """All data on the user's devices: the mailbox plus whichever device kinds are present."""

    def __init__(self, mailbox, folder: Path | None = None):
        self.mailbox = mailbox
        self.folder = Path(folder or config.DEVICES)
        self.sets: dict[str, RecordSet] = {}
        self.errors: dict[str, str] = {}
        for kind in OTHER_KINDS:
            if RecordSet.available(kind, self.folder):
                try:
                    self.sets[kind] = RecordSet(kind, self.folder)
                except (OSError, ValueError, KeyError) as exc:
                    self.errors[kind] = f"{type(exc).__name__}: {exc}"
        self._indexes: dict[str, LocalIndex] = {}
        self._lock = threading.Lock()

    @property
    def kinds(self) -> list[str]:
        return ["mail", *self.sets]

    @property
    def devices(self) -> list[DeviceInfo]:
        return [BY_KIND[k] for k in self.kinds]

    def has(self, kind: str) -> bool:
        return kind == "mail" or kind in self.sets

    def get(self, kind: str, rid: int) -> dict:
        if kind == "mail":
            return self.mailbox.get(int(rid))
        return self.sets[kind].get(int(rid))

    def count(self, kind: str) -> int:
        return self.mailbox.count if kind == "mail" else self.sets[kind].count

    def index(self, kind: str) -> LocalIndex:
        with self._lock:
            if kind not in self._indexes:
                if kind == "mail":
                    self._indexes[kind] = _mail_index(self.mailbox)
                else:
                    rs = self.sets[kind]
                    fields = [records.index_fields(kind, r) for r in rs.records]
                    self._indexes[kind] = LocalIndex(fields, [r.get("date", "") for r in rs.records])
            return self._indexes[kind]

    def matching(self, kind: str, plan: dict) -> int:
        return self.index(kind).count(plan)

    @cached_property
    def vocabulary(self) -> dict[str, dict[str, list[str]]]:
        return {kind: rs.vocabulary() for kind, rs in self.sets.items()}

    def presets(self) -> list[dict]:
        """Mail presets (kind mail, device pc) followed by the device presets, if any."""
        out = []
        for p in self.mailbox.presets:
            p = dict(p)
            p.setdefault("kind", "mail")
            p.setdefault("device", "pc")
            out.append(p)
        path = self.folder / "presets.json"
        if path.exists():
            try:
                extra = json.loads(path.read_text("utf-8"))
            except ValueError:
                extra = []
            seen = {p["id"] for p in out}
            for p in extra if isinstance(extra, list) else []:
                if not isinstance(p, dict) or not p.get("question") or p.get("id") in seen:
                    continue
                p = dict(p)
                kind = p.get("kind") or (p.get("plan") or {}).get("kind") or "mail"
                if not self.has(kind):
                    continue
                p["kind"] = kind
                p.setdefault("device", BY_KIND[kind].id)
                out.append(p)
        return out
