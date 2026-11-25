"""Random access to the demo mailbox (mailbox.jsonl) plus presets and summary facts."""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
from functools import cached_property
from pathlib import Path

import numpy as np

from . import config

SENSITIVE = {
    "health": ("体检", "化验", "血脂", "尿酸", "血糖", "病历", "诊断", "复查", "门诊", "处方"),
    "finance": ("账单", "信用卡", "还款", "转账", "工资", "余额", "交易", "发票", "报销"),
    "contract": ("合同", "违约", "付款", "协议", "报价", "金额"),
    "family": ("家长", "学校", "孩子", "班主任", "老婆", "老公", "妈", "爸"),
    "travel": ("航班", "酒店", "行程", "登机", "入住", "订单"),
    "identity": ("手机", "地址", "身份证", "卡号", "尾号"),
}
SENSITIVE_LABEL = {
    "health": "体检与病历",
    "finance": "账单与交易",
    "contract": "合同与报价",
    "family": "家庭与学校",
    "travel": "行程与住址",
    "identity": "联系方式与卡号",
}
_PHONE = re.compile(r"1[3-9]\d[\d*]{4}\d{4}")
_CARD = re.compile(r"(?:尾号|\*{4}\s?)\d{4}")
_MONEY = re.compile(r"[¥￥]\s?[\d,]+(?:\.\d+)?")


def parse_date(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def month_of(value: str) -> str:
    return value[:7]


class Mailbox:
    """Line-offset index over mailbox.jsonl; one email is parsed only when needed."""

    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder or config.DATA)
        self.path = self.folder / "mailbox.jsonl"
        self._lock = threading.Lock()
        self._fh = None
        self._offsets = None
        self._cache: dict[int, dict] = {}

    # ------------------------------------------------------------------ index
    def _index_path(self) -> Path:
        st = self.path.stat()
        tag = f"{st.st_size}-{int(st.st_mtime)}"
        return config.RUNTIME / "mailbox-index" / f"{tag}.npz"

    def _load_index(self):
        if self._offsets is not None:
            return
        cache = self._index_path()
        if cache.exists():
            data = np.load(cache, allow_pickle=False)
            self._offsets = data["offsets"]
            self._ids = data["ids"]
            self._ts = data["ts"]
            self._orgs = data["orgs"]
            self._cats = data["cats"]
            self._months = data["months"]
            self._sizes = data["sizes"]
            self._attach = data["attach"]
            self._sens = data["sens"]
            return
        offsets, ids, ts, orgs, cats, months, sizes, attach, sens = ([] for _ in range(9))
        sens_keys = list(SENSITIVE)
        with open(self.path, "rb") as fh:
            pos = 0
            for line in fh:
                offsets.append(pos)
                pos += len(line)
                mail = json.loads(line)
                ids.append(int(mail["id"]))
                ts.append(parse_date(mail["date"]).timestamp())
                facets = mail.get("facets") or {}
                orgs.append(facets.get("org") or (mail.get("from") or {}).get("org") or "")
                cats.append(mail.get("category") or "")
                months.append(month_of(mail["date"]))
                text_size = len(line)
                sizes.append(text_size)
                attach.append(len(mail.get("attachments") or []))
                blob = mail.get("subject", "") + mail.get("body", "")[:600]
                for a in mail.get("attachments") or []:
                    blob += a.get("name", "") + (a.get("text") or "")[:300]
                bits = 0
                for i, key in enumerate(sens_keys):
                    if any(word in blob for word in SENSITIVE[key]):
                        bits |= 1 << i
                sens.append(bits)
        self._offsets = np.asarray(offsets, dtype=np.int64)
        self._ids = np.asarray(ids, dtype=np.int64)
        self._ts = np.asarray(ts, dtype=np.float64)
        self._orgs = np.asarray(orgs, dtype=object).astype("U")
        self._cats = np.asarray(cats, dtype=object).astype("U")
        self._months = np.asarray(months, dtype="U7")
        self._sizes = np.asarray(sizes, dtype=np.int64)
        self._attach = np.asarray(attach, dtype=np.int32)
        self._sens = np.asarray(sens, dtype=np.int32)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            cache,
            offsets=self._offsets,
            ids=self._ids,
            ts=self._ts,
            orgs=self._orgs,
            cats=self._cats,
            months=self._months,
            sizes=self._sizes,
            attach=self._attach,
            sens=self._sens,
        )

    @property
    def count(self) -> int:
        self._load_index()
        return len(self._offsets)

    @property
    def ids(self):
        self._load_index()
        return self._ids

    @property
    def timestamps(self):
        self._load_index()
        return self._ts

    @property
    def orgs(self):
        self._load_index()
        return self._orgs

    @property
    def categories(self):
        self._load_index()
        return self._cats

    @property
    def months(self):
        self._load_index()
        return self._months

    # ------------------------------------------------------------------ access
    def get(self, mail_id: int) -> dict:
        mail_id = int(mail_id)
        if mail_id in self._cache:
            return self._cache[mail_id]
        self._load_index()
        pos = int(np.searchsorted(self._ids, mail_id))
        if pos >= len(self._ids) or self._ids[pos] != mail_id:
            raise KeyError(mail_id)
        with self._lock:
            if self._fh is None:
                self._fh = open(self.path, "rb")
            self._fh.seek(int(self._offsets[pos]))
            line = self._fh.readline()
        mail = json.loads(line)
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[mail_id] = mail
        return mail

    def iter_lines(self):
        with open(self.path, "rb") as fh:
            yield from fh

    def close(self):
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    # ------------------------------------------------------------------ facts
    @cached_property
    def presets(self) -> list[dict]:
        path = self.folder / "presets.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else []

    @cached_property
    def bible(self) -> dict:
        path = self.folder / "bible.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else {}

    @cached_property
    def summary(self) -> dict:
        self._load_index()
        sens_keys = list(SENSITIVE)
        sensitive = {}
        for i, key in enumerate(sens_keys):
            sensitive[key] = {
                "label": SENSITIVE_LABEL[key],
                "count": int(np.count_nonzero(self._sens & (1 << i))),
            }
        first = dt.datetime.fromtimestamp(float(self._ts.min()), config.TZ)
        last = dt.datetime.fromtimestamp(float(self._ts.max()), config.TZ)
        return {
            "emails": int(self.count),
            "attachments": int(self._attach.sum()),
            "text_bytes": int(self._sizes.sum()),
            "first": first.date().isoformat(),
            "last": last.date().isoformat(),
            "as_of": config.AS_OF.isoformat(),
            "sensitive": sensitive,
            "orgs": int(len(set(self._orgs.tolist()))),
        }


def model_text(mail: dict, *, body_chars: int = 1800, attachment_chars: int = 1600) -> dict:
    """Evidence item for the prompt builder."""
    sender = mail.get("from") or {}
    return {
        "id": int(mail["id"]),
        "date": mail["date"][:16].replace("T", " "),
        "from": f"{sender.get('name', '')} <{sender.get('email', '')}>"
        + (f"（{sender.get('org')}）" if sender.get("org") else ""),
        "subject": mail.get("subject", ""),
        "body": (mail.get("body") or "")[:body_chars],
        "attachments": [
            {"name": a.get("name", ""), "text": (a.get("text") or "")[:attachment_chars]}
            for a in (mail.get("attachments") or [])[:3]
        ],
    }


def card(mail: dict, *, snippet_chars: int = 90) -> dict:
    """Compact view of one email for evidence chips and lists."""
    sender = mail.get("from") or {}
    body = " ".join((mail.get("body") or "").split())
    return {
        "id": int(mail["id"]),
        "date": mail["date"][:16].replace("T", " "),
        "from": sender.get("name", ""),
        "org": sender.get("org", ""),
        "subject": mail.get("subject", ""),
        "snippet": body[:snippet_chars],
        "category": mail.get("category", ""),
        "attachments": [a.get("name", "") for a in mail.get("attachments") or []],
    }


def sensitive_spans(text: str) -> list[str]:
    hits = _PHONE.findall(text) + _CARD.findall(text) + _MONEY.findall(text)
    return hits[:6]
