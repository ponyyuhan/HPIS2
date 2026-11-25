"""Paths, network conditions and run parameters for the HPIS 2.0 demo."""

from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
DATA = Path(os.environ.get("HPIS_DEMO_DATA", DEMO / "data"))
# Photo / watch / car data of the other three devices (optional: without it the demo is mail-only).
DEVICES = Path(os.environ.get("HPIS_DEMO_DEVICES", DATA / "devices"))
WEB = DEMO / "web"
ACCEPTANCE = Path(os.environ.get("HPIS_DEMO_ACCEPTANCE", ROOT / "acceptance" / "results" / "summary.json"))


def _cache_root() -> Path:
    if "HPIS_DEMO_RUNTIME" in os.environ:
        return Path(os.environ["HPIS_DEMO_RUNTIME"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "hpis2-demo"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hpis2-demo"


RUNTIME = _cache_root()

# The mailbox snapshot is fixed in time so that relative questions
# ("过去三个月", "上个月", "下周") always resolve to the same window.
AS_OF = dt.datetime.fromisoformat(os.environ.get("HPIS_DEMO_AS_OF", "2026-11-20T18:00:00+08:00"))
TZ = AS_OF.tzinfo


@dataclass
class Link:
    """One emulated network segment: bandwidth in bit/s and round-trip time in ms."""

    bps: float
    rtt_ms: float

    def wire_seconds(self, nbytes: int) -> float:
        return nbytes * 8 / self.bps

    def one_way_seconds(self, nbytes: int) -> float:
        return self.rtt_ms / 2000 + self.wire_seconds(nbytes)


@dataclass
class Network:
    edge: Link = field(default_factory=lambda: Link(1e9, 30.0))  # 端云
    cloud: Link = field(default_factory=lambda: Link(50e9, 0.5))  # 云云

    def describe(self):
        return {
            "edge": {"gbps": self.edge.bps / 1e9, "rtt_ms": self.edge.rtt_ms},
            "cloud": {"gbps": self.cloud.bps / 1e9, "rtt_ms": self.cloud.rtt_ms},
        }

    @classmethod
    def from_dict(cls, value: dict | None) -> "Network":
        net = cls()
        if not value:
            return net
        for name in ("edge", "cloud"):
            part = value.get(name) or {}
            link = getattr(net, name)
            if "gbps" in part:
                link.bps = max(1e6, float(part["gbps"]) * 1e9)
            if "rtt_ms" in part:
                link.rtt_ms = max(0.0, float(part["rtt_ms"]))
        return net

    def to_dict(self):
        return {"edge": asdict(self.edge), "cloud": asdict(self.cloud)}


# Retrieval and generation sizes shared by all lanes (same evidence budget).
RETRIEVE_K = 10  # ranked candidates fetched by the encrypted search
EVIDENCE_K = 5  # evidence items handed to the model
EVIDENCE_K_BY_KIND = {"photo": 6, "sport": 8, "car": 8}  # six pictures; watch/car records are short
UPLOAD_BATCH_BYTES = 16 * 1024 * 1024  # single-upload limit of the ordinary cloud
INGEST_BATCH = 400
INGEST_BATCH_BYTES = 8 * 1024 * 1024  # photo bodies are large; HPIC/HPIS accept 64 MB per request


def evidence_k(kind: str | None) -> int:
    return EVIDENCE_K_BY_KIND.get(kind or "mail", EVIDENCE_K)


def cloud_shortlist(kind: str | None) -> int:
    """Plain cloud: candidates kept for on-the-fly vectors. Emails are long; photo, watch and car
    records are a sentence or two, so a few hundred of them still embed within the time budget."""
    return CLOUD_SHORTLIST if (kind or "mail") == "mail" else CLOUD_SHORTLIST_SHORT_RECORDS


def _seconds(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, default)))
    except ValueError:
        return default


# Never hang: limits on every stage that waits for something outside the demo.
RUN_LIMIT_S = _seconds("HPIS_DEMO_RUN_LIMIT_S", 90.0)  # a whole run, click to end
FIRST_TOKEN_S = _seconds("HPIS_DEMO_FIRST_TOKEN_S", 25.0)  # model: first visible token
ANSWER_LIMIT_S = _seconds("HPIS_DEMO_ANSWER_LIMIT_S", 60.0)  # model: whole answer
CLOUD_EMBED_BUDGET_S = _seconds("HPIS_DEMO_CLOUD_EMBED_BUDGET_S", 6.0)  # plain cloud, on-the-fly vectors
CLOUD_SHORTLIST = 100  # plain cloud: lexical shortlist before the on-the-fly vectors (emails)
CLOUD_SHORTLIST_SHORT_RECORDS = 400  # the same for photo captions and watch/car records
VERIFY_LIMIT_S = _seconds("HPIS_DEMO_VERIFY_LIMIT_S", 6.0)  # the paired plain/cipher re-check

DEFAULT_MODEL = os.environ.get("HPIS_DEMO_MODEL", "qwen")
WEB_PORT = int(os.environ.get("HPIS_DEMO_PORT", "8780"))
# Browsers record a page (screen capture) only over https or on localhost, so the demo
# also serves https here; 0 turns it off.
WEB_HTTPS_PORT = int(os.environ.get("HPIS_DEMO_HTTPS_PORT", "8781"))
