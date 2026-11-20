"""End-to-end check of the demo pipeline on a tiny synthetic mailbox.

Builds both HPIS deployments, starts every service as a real TLS process, runs
one question through both lanes (model replaced by the offline extractive
fallback) and checks the properties the presentation relies on:

  * both lanes hand the model the same evidence (same filters, same exact kernel);
  * the live plaintext-vs-ciphertext check returns identical Top-K;
  * HPIS 2.0 moves far fewer bytes than the plain upload and opens only K records.

Run:  .venv-demo/bin/python -m unittest demo.tests.test_pipeline -v
"""

import asyncio
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np


def make_mailbox(folder: Path, n: int = 600):
    rng = np.random.default_rng(7)
    tz = dt.timezone(dt.timedelta(hours=8))
    orgs = ["瀚海物流", "远洋港务", "星辰银行", "云帆智能科技有限公司"]
    cats = {"瀚海物流": "client", "远洋港务": "client", "星辰银行": "finance", "云帆智能科技有限公司": "work"}
    base = rng.standard_normal((len(orgs), 64))
    mails, vecs = [], []
    start = dt.datetime(2026, 1, 1, 9, tzinfo=tz)
    for i in range(n):
        org = orgs[i % len(orgs)]
        date = start + dt.timedelta(hours=7 * i)
        mails.append(
            {
                "id": i + 1,
                "thread": f"t{i // 3}",
                "date": date.isoformat(),
                "folder": "inbox",
                "category": cats[org],
                "from": {"name": f"联系人{i % 9}", "email": f"p{i % 9}@x.cn", "org": org},
                "to": [{"name": "林舟", "email": "linzhou@yunfan-tech.cn", "org": "云帆智能科技有限公司"}],
                "cc": [],
                "subject": f"{org} 事项 {i}",
                "body": f"关于{org}的第{i}号事项。" * 5,
                "attachments": [],
                "facets": {"org": org, "project": None, "people": [f"联系人{i % 9}"]},
            }
        )
        vecs.append(base[i % len(orgs)] + 0.9 * rng.standard_normal(64))
    v = np.asarray(vecs, dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    with open(folder / "mailbox.jsonl", "w", encoding="utf-8") as f:
        for m in mails:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    v.tofile(folder / "embeddings.f32")
    (folder / "embeddings.json").write_text(json.dumps({"model": "test", "dim": 64, "count": n}))
    (folder / "bible.json").write_text(json.dumps({"orgs": [{"name": o} for o in orgs]}, ensure_ascii=False))
    preset = {
        "id": "t",
        "chip": "测试",
        "question": "过去三个月瀚海物流的事项有哪些？",
        "plan": {
            "orgs": ["瀚海物流"],
            "people": [],
            "categories": [],
            "date_from": "2026-03-01",
            "date_to": "2026-06-30",
            "semantic": "瀚海物流 事项",
        },
        "gold": {"facts": [], "evidence_ids": []},
    }
    (folder / "presets.json").write_text(json.dumps([preset], ensure_ascii=False))
    return v


class FakeEmbedder:
    def __init__(self, vectors):
        self.vectors = vectors

    def encode_query(self, text):
        return self.vectors[0]


class PipelineTest(unittest.TestCase):
    def test_three_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "data").mkdir()
            vectors = make_mailbox(tmp / "data")
            os.environ["HPIS_DEMO_DATA"] = str(tmp / "data")
            os.environ["HPIS_DEMO_RUNTIME"] = str(tmp / "runtime")
            os.environ["HPIS_DEMO_OFFLINE"] = "1"
            os.environ["HPIS_DEMO_CLOUD_EMBED"] = "stored"
            import importlib

            from demo import config

            importlib.reload(config)
            from demo import build, cluster, engine, mailbox

            for mod in (cluster, build, mailbox, engine):
                importlib.reload(mod)
            engine.Embedder._instance = FakeEmbedder(vectors)
            asyncio.run(self._run(build, cluster, engine, mailbox))

    async def _run(self, build, cluster, engine, mailbox):
        info = await build.build(force=True)
        self.assertEqual(info["emails"], 600)
        clu = await cluster.Cluster().start()
        try:
            eng = await engine.Engine(clu, mailbox.Mailbox(), info).open()
            events = []
            async for ev in eng.run("过去三个月瀚海物流的事项有哪些？", "qwen"):
                events.append(ev)
            # Long-running deployments renew the one-day tickets; queries must keep working.
            from hpis.provision import renew

            old = {k: d.ticket for k, d in eng.devices.items()}
            for name in clu.deployments:
                renew(cluster.deploy_root(name), "default")
            eng.refresh_tickets()
            self.assertTrue(all(eng.devices[k].ticket != old[k] for k in old))
            again = [ev async for ev in eng.run("过去三个月瀚海物流的事项有哪些？", "qwen", lanes=("v2",))]
            self.assertFalse([e for e in again if e["type"] == "error"])
            self.assertTrue(next(e for e in again if e["type"] == "verify")["identical"])
            await eng.close()
        finally:
            await clu.stop()
        errors = [e for e in events if e["type"] == "error"]
        self.assertFalse(errors, errors)
        evidence = {e["lane"]: [c["id"] for c in e["items"]] for e in events if e["type"] == "evidence"}
        self.assertEqual(set(evidence), {"plain", "v2"})
        self.assertTrue(evidence["v2"], "no evidence retrieved")
        self.assertEqual(evidence["plain"], evidence["v2"])
        verify = next(e for e in events if e["type"] == "verify")
        self.assertTrue(verify["identical"])
        finish = {e["lane"]: e for e in events if e["type"] == "finish"}
        hpis = {e["lane"]: e for e in events if e["type"] == "hpis"}
        self.assertLess(finish["v2"]["edge_up"] * 100, finish["plain"]["edge_up"])
        self.assertLessEqual(hpis["v2"]["rows"], 10)


if __name__ == "__main__":
    unittest.main()
