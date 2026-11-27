"""End-to-end check of multi-device runs (多端协同) on fixture data, with a fake model server.

Builds both deployments, starts every service as a real TLS process and checks:

  * status.multi: the presets (devices, facts) and the HPIC 1.0 options;
  * HPIC 2.0 (v2): one encrypted search per device AID, evidence of every needed device,
    one model call;
  * HPIC 1.0 (plain): only the devices synced to the phone are uploaded and searched, the
    others are reported missing and the prompt says their data was not received; one model call;
  * a device's evidence is the same in both lanes when both have its data;
  * stop: engine.stop() during the upload, POST /api/stop while the answers stream.

Run:  .venv-demo/bin/python -m unittest demo.tests.test_multi -v
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from demo.tests.fakes import FakeModel, _user_text
from demo.tests.fixtures import MULTI_PRESETS, make_devices
from demo.tests.test_devices import ENV_KEYS, answer_text, by_type
from demo.tests.test_pipeline import FakeEmbedder, make_mailbox

DEVICES = ["pc", "phone", "watch", "car"]
QUESTION = MULTI_PRESETS[0]["question"]


class MultiTest(unittest.TestCase):
    def test_multi_devices(self):
        saved = {k: os.environ.get(k) for k in ENV_KEYS}
        saved.update({k: v for k, v in os.environ.items() if k.startswith("HPIS_LLM_PLAIN_")})
        try:
            with tempfile.TemporaryDirectory() as tmp:
                asyncio.run(self._main(Path(tmp)))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    async def _main(self, tmp: Path):
        (tmp / "data").mkdir()
        vectors = make_mailbox(tmp / "data")
        make_devices(tmp / "data" / "devices", dim=64)
        model = FakeModel()
        base = await model.start()
        for k in [k for k in os.environ if k.startswith("HPIS_LLM_PLAIN_")]:
            os.environ.pop(k)
        os.environ.pop("HPIS_DEMO_DEVICES", None)
        os.environ.pop("HPIS_DEMO_V1", None)
        os.environ.update(
            HPIS_DEMO_DATA=str(tmp / "data"),
            HPIS_DEMO_RUNTIME=str(tmp / "runtime"),
            HPIS_DEMO_OFFLINE="0",
            HPIS_DEMO_CLOUD_EMBED="stored",
            HPIS_LLM_BACKEND="h200",
            HPIS_LLM_QWEN_BASE_URL=base,
            HPIS_LLM_QWEN_MODEL="fake",
            HPIS_LLM_VISION="1",
            HPIS_DEMO_FIRST_TOKEN_S="2",
            HPIS_DEMO_ANSWER_LIMIT_S="4",
            HPIS_DEMO_CLOUD_SLOW_S="0",
            HPIS_DEMO_CLOUD_EMBED_BUDGET_S="6",
        )
        from demo import config

        importlib.reload(config)
        from demo import answer, build, cluster, engine, mailbox, server

        for mod in (cluster, build, mailbox, answer, engine, server):
            importlib.reload(mod)
        engine.Embedder._instance = FakeEmbedder(vectors)
        self.config = config
        try:
            info = await build.build(force=True)
            clu = await cluster.Cluster().start()
            try:
                eng = await engine.Engine(clu, mailbox.Mailbox(), info).open()
                try:
                    await eng.warmup()
                    await self.watch_only(eng, model)
                    await self.phone_and_watch(eng)
                    await self.direct_watch(eng)
                    await self.free_question(eng)
                    await self.stop_during_upload(eng)
                    await self.http(eng, info, server)
                finally:
                    await eng.close()
            finally:
                await clu.stop()
        finally:
            await model.stop()

    async def ask(self, eng, question, hpic1, **kw):
        return [ev async for ev in eng.run(question, "qwen", multi={"hpic1": hpic1}, **kw)]

    def lane_evidence(self, events, lane):
        """{device: [ids]} of a lane's evidence event (its items carry device and kind)."""
        out = {}
        for item in by_type(events, "evidence", lane)[0]["items"]:
            self.assertIn(item["kind"], ("mail", "photo", "sport", "car"))
            out.setdefault(item["device"], []).append(item["id"])
        return out

    def check_common(self, events, hpic1, *, same=True):
        self.assertFalse(by_type(events, "error"), by_type(events, "error"))
        plan = by_type(events, "plan")[0]
        self.assertEqual(plan["mode"], "multi")
        self.assertEqual(plan["hpic1"], hpic1)
        self.assertEqual(set(plan["plans"]), set(plan["devices"]))
        self.assertTrue(all(p["chips"] and p["kind"] for p in plan["plans"].values()))
        finish = {e["lane"]: e for e in by_type(events, "finish")}
        self.assertEqual(set(finish), {"plain", "v2"})
        for lane in ("plain", "v2"):
            self.assertFalse(finish[lane]["stopped"], finish[lane])
            self.assertFalse(finish[lane]["timed_out"], finish[lane])
            self.assertTrue(answer_text(events, lane))
            times = [e["t"] for e in events if e.get("lane") == lane]
            self.assertEqual(times, sorted(times), lane)
        end = events[-1]
        self.assertEqual(end["type"], "end")
        self.assertFalse(end["stopped"])
        # HPIC 2.0: one encrypted search per needed device, each in its own AID
        searches = {e["device"]: e for e in by_type(events, "device_search", "v2")}
        self.assertEqual(set(searches), set(plan["devices"]))
        aids = [e["aid"] for e in searches.values()]
        self.assertTrue(all(isinstance(a, str) and len(a) == 8 for a in aids))
        self.assertEqual(len(set(aids)), len(aids))
        self.assertEqual(by_type(events, "decrypt", "v2")[0]["rows"], sum(e["rows"] for e in searches.values()))
        self.assertEqual(by_type(events, "auth", "v2")[0]["tickets"], len(plan["devices"]))
        sealed = by_type(events, "sealed", "v2")[0]
        self.assertEqual([d["device"] for d in sealed["devices"]], plan["devices"])
        v2 = self.lane_evidence(events, "v2")
        self.assertEqual(set(v2), set(plan["devices"]))  # every needed device produced evidence
        # HPIC 1.0: only what was synced to the phone
        plain = self.lane_evidence(events, "plain")
        have = [d for d in plan["devices"] if d in hpic1]
        self.assertLessEqual(set(plain), set(have))
        self.assertEqual([e["device"] for e in by_type(events, "device_search", "plain")], have)
        self.assertEqual(by_type(events, "missing", "plain")[0]["devices"], [d for d in plan["devices"] if d not in hpic1])
        self.assertEqual([e["device"] for e in by_type(events, "sync", "plain")], [d for d in DEVICES if d in hpic1 and d != "phone"])
        for device in plain if same else ():  # identical code paths: the same records from the same data
            self.assertEqual(plain[device], v2[device], device)  # (HPIC 1.0's lexical shortlist may differ when
            # more records pass the filters than it embeds on the fly, as in a single-device run)
        start = [e for e in by_type(events, "stage", "plain") if e["stage"] == "upload" and e["state"] == "start"][0]
        uploads = by_type(events, "upload", "plain")
        self.assertEqual(len(uploads), start["batches"])
        self.assertEqual({e["device"] for e in uploads}, set(have))
        self.assertLess(finish["v2"]["edge_up"] * 10, finish["plain"]["edge_up"])
        return plan, plain, v2

    async def watch_only(self, eng, model):
        before = len(model.requests)
        events = await self.ask(eng, QUESTION, ["watch"])
        plan, plain, v2 = self.check_common(events, ["watch"])
        self.assertEqual(plan["preset"], "multi_fixture")
        self.assertEqual(plan["devices"], DEVICES)
        self.assertEqual(plan["facts"], MULTI_PRESETS[0]["facts"])
        self.assertEqual(set(plain), {"watch"})
        sync = by_type(events, "sync", "plain")[0]
        self.assertEqual((sync["via"], sync["records"]), ("phone", eng.catalog.count("sport")))
        self.assertGreater(sync["bytes"], 0)
        search = by_type(events, "device_search", "plain")[0]
        self.assertEqual(search["records"], eng.catalog.count("sport"))
        self.assertEqual(search["rows"], len(plain["watch"]))
        # one model call per lane; HPIC 1.0 is told which devices' data it did not receive
        sent = [_user_text(body) for body in model.requests[before:]]
        self.assertEqual(len(sent), 2)
        missing = [t for t in sent if "本次没有收到这台设备的数据" in t]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].count("本次没有收到这台设备的数据"), 3)
        complete = next(t for t in sent if t is not missing[0])
        for label in ("【PC · 邮件】", "【手机 · 照片】", "【手表 · 运动健康】", "【车机 · 用车出行】"):
            self.assertIn(label, complete)

    async def phone_and_watch(self, eng):
        events = await self.ask(eng, QUESTION, ["phone", "watch"])
        plan, plain, v2 = self.check_common(events, ["phone", "watch"])
        self.assertEqual(set(plain), {"phone", "watch"})
        self.assertEqual(len(by_type(events, "sync", "plain")), 1)  # the phone's own data needs no sync

    async def direct_watch(self, eng):
        """场景二: the watch sends only its own data straight to HPIC 1.0 (no phone, no sync)."""
        events = [ev async for ev in eng.run(QUESTION, "qwen", multi={"hpic1": ["watch"], "route": "direct"})]
        plan = by_type(events, "plan")[0]
        self.assertEqual((plan["route"], plan["hpic1"]), ("direct", ["watch"]))
        self.assertEqual(by_type(events, "sync", "plain"), [])
        direct = by_type(events, "direct", "plain")
        self.assertEqual([(e["device"], e["records"]) for e in direct], [("watch", eng.catalog.count("sport"))])
        self.assertGreater(direct[0]["bytes"], 0)
        self.assertEqual(by_type(events, "missing", "plain")[0]["devices"], [d for d in plan["devices"] if d != "watch"])
        self.assertEqual(set(self.lane_evidence(events, "plain")), {"watch"})
        self.assertEqual(set(self.lane_evidence(events, "v2")), set(plan["devices"]))
        route = by_type(events, "plan")[0]
        self.assertNotIn("phone", route["hpic1"])

    async def free_question(self, eng):
        events = await self.ask(eng, "最近的工作、照片、运动和用车情况", ["pc"])
        plan, plain, v2 = self.check_common(events, ["pc"], same=False)
        self.assertIsNone(plan["preset"])
        self.assertEqual(plan["devices"], DEVICES)
        self.assertEqual(plan["facts"], [])

    async def stop_during_upload(self, eng):
        net = self.config.Network.from_dict({"edge": {"gbps": 0.0002, "rtt_ms": 30}})  # ~10 s upload
        events, hit = [], asyncio.Event()

        async def consume():
            async for ev in eng.run(QUESTION, "qwen", net=net, multi={"hpic1": ["pc", "watch"]}):
                events.append(ev)
                if ev.get("type") == "upload":
                    hit.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(hit.wait(), 30)
        self.assertTrue(await eng.stop())
        await asyncio.wait_for(task, 10)
        self.assertTrue(events[-1]["stopped"])
        finish = {e["lane"]: e for e in by_type(events, "finish")}
        self.assertEqual(set(finish), {"plain", "v2"})
        self.assertTrue(finish["plain"]["stopped"])
        nxt = await self.ask(eng, QUESTION, ["watch"])  # the next run works at once
        self.check_common(nxt, ["watch"])

    async def http(self, eng, info, server):
        from aiohttp.test_utils import TestClient, TestServer

        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        app[server.STATE].update(
            phase="ready", mailbox=eng.mailbox, catalog=eng.catalog, build=info, engine=eng, cluster=eng.cluster,
            progress={}, error=None,
        )
        async with TestClient(TestServer(app)) as client:
            st = await (await client.get("/api/status")).json()
            multi = st["multi"]
            self.assertEqual([p["id"] for p in multi["presets"]], ["multi_fixture"])
            preset = multi["presets"][0]
            self.assertEqual(preset["devices"], DEVICES)
            self.assertEqual(preset["facts"], MULTI_PRESETS[0]["facts"])
            self.assertEqual([o["id"] for o in multi["hpic1_options"]], ["watch", "pc", "phone_watch"])
            self.assertEqual([(sc["id"], sc["route"]) for sc in multi["scenarios"]], [("sync", "phone"), ("direct", "direct")])
            self.assertEqual(multi["scenarios"][1]["default"], ["watch"])
            self.assertTrue(all("可读取" not in d["exposure"] for d in st["devices"]))
            # a multi run over HTTP, stopped while both answers stream
            resp = await client.post("/api/run", json={"question": "慢慢说：德国的照片和十月的跑步", "model": "qwen",
                                                        "mode": "multi", "hpic1": ["watch"]})
            events, stopped = [], None
            async for raw in resp.content:
                ev = json.loads(raw)
                events.append(ev)
                if ev.get("type") == "token" and stopped is None and by_type(events, "token", "v2") \
                        and by_type(events, "token", "plain"):
                    stopped = await (await client.post("/api/stop")).json()
            self.assertEqual(stopped, {"ok": True, "stopped": True})
            self.assertEqual(by_type(events, "plan")[0]["mode"], "multi")
            self.assertTrue(events[-1]["stopped"])
            self.assertTrue(all(f["stopped"] for f in by_type(events, "finish")))
            # a single-device run keeps today's body and events
            resp = await client.post("/api/run", json={"question": "我在慕尼黑拍的照片", "model": "qwen"})
            events = [json.loads(raw) async for raw in resp.content]
            plan = by_type(events, "plan")[0]
            self.assertNotIn("mode", plan)
            self.assertEqual(plan["device"], "phone")
            self.assertFalse(by_type(events, "device_search"))


if __name__ == "__main__":
    unittest.main()
