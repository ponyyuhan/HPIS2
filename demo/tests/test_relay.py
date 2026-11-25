"""Event timing of the relay that forwards HPIC's / the cloud's NDJSON stream to the page.

The page's lane times are the gaps between event times, so the relay must keep the sender's
spacing: a search that finishes within the downlink latency (a few ms on a fast server) must not
be stamped with the same time as the ticket check before it (the page showed 密态检索 0.00 s).

Run:  .venv-demo/bin/python -m unittest demo.tests.test_relay -v
"""

from __future__ import annotations

import contextlib
import json
import unittest

from demo import config
from demo.engine import Engine, Run


class _Content:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_any(self):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, events):
        # everything in one chunk: the sender was done before the relay read anything
        self.content = _Content([b"".join(json.dumps(e).encode() + b"\n" for e in events)])

    def raise_for_status(self):
        pass


@contextlib.asynccontextmanager
async def _request(events):
    yield _Response(events)


async def _relay(events, rtt_ms=30.0):
    run = Run()
    await Engine._relay(None, run.put, run, "v2", _request(events), config.Link(1e9, rtt_ms), 3000)
    out = []
    while not run.queue.empty():
        out.append(run.queue.get_nowait())
    return out


def _at(events, kind):
    return next(e["t"] for e in events if e.get("type") == kind)


class RelayTimingTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_fast_search_keeps_its_duration(self):
        sent = [
            {"ev": "auth", "hpic_ms": 0.4},
            {"ev": "hpis", "hpic_ms": 8.1, "rows": 10},
            {"ev": "decrypt", "hpic_ms": 8.7, "rows": 10, "ms": 0.5},
            {"ev": "evidence", "hpic_ms": 12.4, "items": []},
            {"ev": "token", "hpic_ms": 40.0, "text": "好"},
            {"ev": "done", "hpic_ms": 41.0},
        ]
        got = await _relay(sent)
        auth, evidence = _at(got, "auth"), _at(got, "evidence")
        self.assertGreaterEqual(auth, 14)  # one downlink latency (30 ms RTT / 2) after the sender wrote it
        # 12 ms at the sender, all of it inside the 15 ms latency; a timer may overshoot a millisecond or two
        self.assertGreaterEqual(evidence - auth, 9)
        self.assertLess(evidence - auth, 20)
        first = next(e for e in got if e.get("type") == "stage" and e.get("state") == "first")
        self.assertGreaterEqual(first["t"] - auth, 38)  # the first token keeps its place too
        times = [e["t"] for e in got]
        self.assertEqual(times, sorted(times))
        self.assertEqual(got[-1]["type"], "finish")

    async def test_cloud_stamps_are_used_too(self):
        got = await _relay([{"ev": "search", "cloud_ms": 1.0}, {"ev": "evidence", "cloud_ms": 9.0, "items": []}, {"ev": "done", "cloud_ms": 9.5}])
        self.assertGreaterEqual(_at(got, "evidence") - _at(got, "search"), 7.0)

    async def test_an_unstamped_stream_waits_one_latency_only(self):
        sent = [{"ev": "token", "text": str(i)} for i in range(40)] + [{"ev": "done"}]
        got = await _relay(sent)
        self.assertGreaterEqual(got[0]["t"], 14)
        self.assertLess(got[-1]["t"], 15 + 200)  # not 40 latencies


if __name__ == "__main__":
    unittest.main()
