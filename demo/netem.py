"""Uniform network emulation: every hop costs RTT/2 per direction plus bytes/bandwidth.

Compute on both ends is real; only the wire is emulated.  A hop never finishes
earlier than the emulated wire time, and any slower local processing (TLS,
copying, parsing) stays visible because it is real work.
"""

from __future__ import annotations

import asyncio
import time

from .config import Link


def now() -> float:
    return time.perf_counter()


async def pace(started: float, link: Link, nbytes: int, *, legs: int = 1) -> float:
    """Sleep until `legs` one-way latencies plus the wire time of `nbytes` have elapsed."""
    target = started + legs * link.rtt_ms / 2000 + link.wire_seconds(nbytes)
    delay = target - now()
    if delay > 0:
        await asyncio.sleep(delay)
    return now() - started


def link_from(value: dict | None, default: Link) -> Link:
    if not value:
        return default
    return Link(float(value.get("bps", default.bps)), float(value.get("rtt_ms", default.rtt_ms)))
