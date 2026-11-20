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
UPLOAD_BATCH_BYTES = 16 * 1024 * 1024  # single-upload limit of the ordinary cloud
INGEST_BATCH = 400

DEFAULT_MODEL = os.environ.get("HPIS_DEMO_MODEL", "qwen")
WEB_PORT = int(os.environ.get("HPIS_DEMO_PORT", "8780"))
# Browsers record a page (screen capture) only over https or on localhost, so the demo
# also serves https here; 0 turns it off.
WEB_HTTPS_PORT = int(os.environ.get("HPIS_DEMO_HTTPS_PORT", "8781"))
