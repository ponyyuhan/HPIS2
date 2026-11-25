"""End-to-end check of the four-device demo on fixture data, with a fake model server.

Builds both deployments (PC mail, phone photos, watch and car records, eight
decoy tenants), starts every service as a real TLS process and checks:

  * routing: each preset reaches its device; both lanes hand the model the same evidence;
    photos reach the model as images; the paired plain/cipher check is identical;
  * relaxation: a filter that leaves nothing is dropped, with a notice;
  * never hang: first-token and whole-answer deadlines, the 90 s cap, the plain cloud's
    vector budget, POST /api/stop during upload / retrieval / generation, a new question
    stopping the old one, and the next run starting at once;
  * the HTTP endpoints of PLAN-v3 §3 (status, stop, photo, record, cipher, cloudview, spy).

Run:  .venv-demo/bin/python -m unittest demo.tests.test_devices -v
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import aiohttp

from demo.tests.fakes import FakeModel
from demo.tests.fixtures import make_devices
from demo.tests.test_pipeline import FakeEmbedder, make_mailbox

ENV_KEYS = (
    "HPIS_DEMO_DATA", "HPIS_DEMO_RUNTIME", "HPIS_DEMO_DEVICES", "HPIS_DEMO_OFFLINE", "HPIS_DEMO_CLOUD_EMBED",
    "HPIS_LLM_BACKEND", "HPIS_LLM_QWEN_BASE_URL", "HPIS_LLM_QWEN_MODEL", "HPIS_LLM_VISION", "HPIS_DEMO_FIRST_TOKEN_S",
    "HPIS_DEMO_ANSWER_LIMIT_S", "HPIS_DEMO_CLOUD_SLOW_S", "HPIS_DEMO_CLOUD_EMBED_BUDGET_S", "HPIS_DEMO_V1",
)


def by_type(events, kind, lane=None):
    return [e for e in events if e.get("type") == kind and (lane is None or e.get("lane") == lane)]


def answer_text(events, lane):
    return "".join(e["text"] for e in by_type(events, "token", lane))


class DevicesTest(unittest.TestCase):
    def test_four_devices(self):
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
        from demo import answer, build, cluster, devices, engine, mailbox, server

        for mod in (cluster, build, mailbox, answer, engine, server):
            importlib.reload(mod)
        engine.Embedder._instance = FakeEmbedder(vectors)
        self.config, self.engine_mod, self.cluster_mod = config, engine, cluster
        try:
            info = await build.build(force=True)
            self.check_build(info, build)
            again = await build.build()  # nothing changed: the build is reused as is
            self.assertEqual(again["built_at"], info["built_at"])
            clu = await cluster.Cluster().start()
            try:
                eng = await engine.Engine(clu, mailbox.Mailbox(), info).open()
                try:
                    await eng.warmup()
                    await self.routing(eng, model)
                    await self.relaxation(eng)
                    await self.deadlines(eng, model)
                    await self.stops(eng, model)
                    await self.endpoints(eng, info, server, devices)
                    await self.slow_cloud(eng, clu, cluster)
                finally:
                    await eng.close()
            finally:
                await clu.stop()
        finally:
            await model.stop()

    # ------------------------------------------------------------------ build
    def check_build(self, info, build):
        self.assertEqual(set(info["kinds"]), {"photo", "sport", "car"})
        self.assertEqual(info["version"], build.BUILD_VERSION)
        self.assertEqual(len(info["decoys"]["tenants"]), 8)
        for kind in ("mail", "photo", "sport", "car"):
            upload = info["uploads"][kind]
            self.assertTrue(upload["batches"])
            self.assertTrue(all((build.UPLOAD_DIR / b).exists() for b in upload["batches"]))
        self.assertGreater(info["uploads"]["photo"]["image_bytes"], 0)
        from demo.tests.fixtures import car_records, photo_records, sport_records

        for name in ("tee", "plain"):
            tenants = build.tenant_names(name)
            self.assertEqual(len(tenants), 12, tenants)  # four devices + eight decoys
            self.assertEqual(build.tenant_rows(name, "phone"), len(photo_records()))
            self.assertEqual(build.tenant_rows(name, "watch"), len(sport_records()))
            self.assertEqual(build.tenant_rows(name, "car"), len(car_records()))
            self.assertEqual(build.tenant_rows(name, "default"), 600)

    # ------------------------------------------------------------------ runs
    async def ask(self, eng, question, **kw):
        return [ev async for ev in eng.run(question, "qwen", **kw)]

    def check_run(self, events, kind, device):
        errors = by_type(events, "error")
        self.assertFalse(errors, errors)
        plan = by_type(events, "plan")[0]
        self.assertEqual((plan["kind"], plan["device"], plan["plan"]["kind"]), (kind, device, kind))
        self.assertGreater(plan["total"], 0)
        evidence = {e["lane"]: [c["id"] for c in e["items"]] for e in by_type(events, "evidence")}
        self.assertEqual(set(evidence), {"plain", "v2"})
        self.assertTrue(evidence["v2"])
        self.assertEqual(evidence["plain"], evidence["v2"])
        finish = {e["lane"]: e for e in by_type(events, "finish")}
        for lane in ("plain", "v2"):
            self.assertFalse(finish[lane]["stopped"])
            self.assertFalse(finish[lane]["timed_out"])
            self.assertTrue(answer_text(events, lane))
        self.assertLess(finish["v2"]["edge_up"] * 10, finish["plain"]["edge_up"])
        verify = by_type(events, "verify")[0]
        self.assertTrue(verify["identical"], verify)
        self.assertEqual(verify["kind"], kind)
        end = events[-1]
        self.assertEqual(end["type"], "end")
        self.assertFalse(end["stopped"])
        for lane in ("plain", "v2"):  # one clock: each lane's times only move forward, and the
            times = [e["t"] for e in events if e.get("lane") == lane]  # finish is the last of them
            self.assertEqual(times, sorted(times), lane)
            self.assertEqual(finish[lane]["total_ms"], max(times))
        self.assertGreaterEqual(end["t"], max(f["total_ms"] for f in finish.values()))
        return plan, evidence

    async def routing(self, eng, model):
        cases = [
            ("整理我在德国出差时拍的照片", "photo", "phone"),
            ("给我 2024 年去韩国旅游的照片", "photo", "phone"),
            ("我最近一个月的运动情况怎么样？", "sport", "watch"),
            ("上个月我开车去过哪些地方，充了几次电？", "car", "car"),
            ("过去三个月瀚海物流的事项有哪些？", "mail", "pc"),
            ("我在慕尼黑拍的照片", "photo", "phone"),
        ]
        for question, kind, device in cases:
            before = len(model.requests)
            events = await self.ask(eng, question)
            plan, evidence = self.check_run(events, kind, device)
            items = by_type(events, "evidence", "v2")[0]["items"]
            if kind == "photo":
                self.assertLessEqual(len(items), 6)
                self.assertTrue(all(i["thumb"].startswith("/api/photo/") and i["place"].get("city") for i in items))
                sent = model.requests[before:]
                self.assertEqual(len(sent), 2)  # one request per lane, both with the pictures
                for body in sent:
                    parts = body["messages"][1]["content"]
                    self.assertEqual(sum(p["type"] == "image_url" for p in parts), len(items))
                self.assertTrue(by_type(events, "plan")[0]["vision"])
            else:
                self.assertLessEqual(len(items), 5)
                if kind != "mail":
                    self.assertTrue(all(i["kind"] == kind and i["title"] and i["date"] for i in items))
            if question == "我在慕尼黑拍的照片":
                self.assertTrue(all(i["place"]["city"] == "慕尼黑" for i in items))

    async def relaxation(self, eng):
        events = await self.ask(eng, "我在巴黎拍的照片")
        plan = by_type(events, "plan")[0]
        self.assertEqual(plan["relaxed"], ["cities", "countries"])
        notices = by_type(events, "notice")
        self.assertTrue(any(n["lane"] is None and "巴黎" in n["text"] for n in notices), notices)
        self.check_run(events, "photo", "phone")

    async def deadlines(self, eng, model):
        # The model never starts: both lanes end with an extract after the 2 s first-token limit.
        t0 = time.perf_counter()
        events = await self.ask(eng, "卡住：瀚海物流的事项有哪些？")
        self.assertLess(time.perf_counter() - t0, 15)
        for lane in ("plain", "v2"):
            fin = by_type(events, "finish", lane)[0]
            self.assertTrue(fin["timed_out"], fin)
            self.assertFalse(fin["stopped"])
            self.assertIn("证据摘录", answer_text(events, lane))
            self.assertTrue([n for n in by_type(events, "notice", lane) if "没有开始回答" in n["text"]])
        # The model never stops: the answer is cut at 4 s with what was generated.
        events = await self.ask(eng, "慢慢说：瀚海物流的事项")
        for lane in ("plain", "v2"):
            fin = by_type(events, "finish", lane)[0]
            self.assertTrue(fin["timed_out"])
            self.assertIn("第0段", answer_text(events, lane))
        # The whole run is capped: here at 2.5 s, while the model is still talking.
        limit = self.config.RUN_LIMIT_S
        self.config.RUN_LIMIT_S = 2.5
        try:
            t0 = time.perf_counter()
            events = await self.ask(eng, "慢慢说：瀚海物流的项目")
            self.assertLess(time.perf_counter() - t0, 6)
        finally:
            self.config.RUN_LIMIT_S = limit
        end = events[-1]
        self.assertTrue(end["timed_out"])
        self.assertEqual({e["lane"] for e in by_type(events, "finish") if e["timed_out"]}, {"plain", "v2"})
        self.assertTrue(any("超过" in n["text"] for n in by_type(events, "notice")))

    async def stop_when(self, eng, question, ready, **kw):
        """Run, call engine.stop() once ``ready(event)`` is true; return (events, stop latency)."""
        events, hit = [], asyncio.Event()

        async def consume():
            async for ev in eng.run(question, "qwen", **kw):
                events.append(ev)
                if ready(ev):
                    hit.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(hit.wait(), 30)
        t0 = time.perf_counter()
        self.assertTrue(await eng.stop())
        latency = time.perf_counter() - t0
        await asyncio.wait_for(task, 10)
        return events, latency

    def check_stopped(self, events, latency, lanes=("plain", "v2")):
        self.assertLess(latency, 3.5)
        end = events[-1]
        self.assertEqual(end["type"], "end")
        self.assertTrue(end["stopped"])
        finish = {e["lane"]: e for e in by_type(events, "finish")}
        self.assertEqual(set(finish), {"plain", "v2"})
        for lane in lanes:
            self.assertTrue(finish[lane]["stopped"], finish[lane])

    async def stops(self, eng, model):
        # Mid-upload: 200 kbit/s makes the plain upload last ~10 s.
        slow_net = {"edge": {"gbps": 0.0002, "rtt_ms": 30}}
        net = self.config.Network.from_dict(slow_net)
        events, latency = await self.stop_when(
            eng, "过去三个月瀚海物流的事项有哪些？", lambda e: e.get("stage") == "upload" and e.get("state") == "start",
            net=net,
        )
        self.check_stopped(events, latency, lanes=("plain",))
        t0 = time.perf_counter()
        nxt = await self.ask(eng, "过去三个月瀚海物流的事项有哪些？")  # the next run starts at once
        self.assertLess(by_type(nxt, "plan")[0]["t"], 1500)
        self.assertLess(time.perf_counter() - t0, 20)
        self.check_run(nxt, "mail", "pc")
        # Mid-generation: both lanes are streaming a long answer.
        hangups = model.hangups
        events, latency = await self.stop_when(
            eng, "慢慢说：德国出差的照片", lambda e: e.get("type") == "token" and e.get("lane") == "plain"
        )
        self.check_stopped(events, latency)
        await asyncio.sleep(0.5)
        self.assertGreaterEqual(model.hangups - hangups, 2)  # HPIC and the cloud closed their model requests
        # A new question stops the one in progress.
        first, second = [], []

        async def consume(q, out):
            async for ev in eng.run(q, "qwen"):
                out.append(ev)

        a = asyncio.create_task(consume("慢慢说：韩国的照片", first))
        while not by_type(first, "token"):
            await asyncio.sleep(0.05)
        await consume("我最近一个月的运动情况怎么样？", second)
        await asyncio.wait_for(a, 5)
        self.assertTrue(first[-1]["stopped"])
        self.assertEqual(first[-1]["reason"], "preempted")
        self.check_run(second, "sport", "watch")
        self.assertFalse(await eng.stop())  # nothing left to stop

    async def slow_cloud(self, eng, clu, cluster):
        """A second ordinary cloud on a slow CPU: the vector budget and a stop during retrieval."""
        os.environ.update(HPIS_DEMO_CLOUD_SLOW_S="0.6", HPIS_DEMO_CLOUD_EMBED_BUDGET_S="1.0")
        port = cluster.free_port()
        proc = await clu._spawn("cloud", cluster.deploy_root("tee") / "hpic", port)
        await cluster.wait_port(port, proc=proc)
        clu.cloud_url = f"https://127.0.0.1:{port}"
        events = await self.ask(eng, "我信用卡上个月花了多少钱", lanes=("plain",))
        search = by_type(events, "search", "plain")[0]
        self.assertTrue(search["budget_hit"], search)
        self.assertLess(search["embedded"], search["shortlist"])
        self.assertLessEqual(search["shortlist"], 100)
        self.assertTrue(any("只现算了" in n["text"] for n in by_type(events, "notice", "plain")))
        self.assertFalse(by_type(events, "finish", "plain")[0]["stopped"])
        events, latency = await self.stop_when(
            eng, "我信用卡上个月花了多少钱", lambda e: e.get("stage") == "retrieve" and e.get("state") == "start",
            lanes=("plain", "v2"),
        )
        self.check_stopped(events, latency, lanes=("plain",))
        async with clu.cloud_session() as s:
            for _ in range(20):  # the cloud's vector loop for that session ended
                async with s.get(clu.cloud_url + "/stats", server_hostname="hpic.internal") as r:
                    stats = await r.json()
                if stats["asking"] == 0 and stats["embedding"] == 0:
                    break
                await asyncio.sleep(0.1)
        self.assertEqual((stats["asking"], stats["embedding"]), (0, 0), stats)

    # ------------------------------------------------------------------ http
    async def endpoints(self, eng, info, server, devices):
        from aiohttp.test_utils import TestClient, TestServer

        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        state = app[server.STATE]
        state.update(
            phase="ready", mailbox=eng.mailbox, catalog=eng.catalog, build=info, engine=eng, cluster=eng.cluster,
            store=server.store_view(eng), progress={}, error=None,
        )
        async with TestClient(TestServer(app)) as client:
            st = await (await client.get("/api/status")).json()
            self.assertEqual([d["id"] for d in st["devices"]], ["pc", "phone", "watch", "car"])
            for d in st["devices"]:
                self.assertEqual(set(d) >= {"id", "label", "kind", "kind_label", "aid", "records", "bytes", "exposure"}, True)
                self.assertEqual(len(d["aid"]), 32)
                self.assertIn("普通云可读取", d["exposure"])
            self.assertEqual(len({d["aid"] for d in st["devices"]}), 4)
            presets = {p["id"]: p for p in st["presets"]}
            self.assertEqual((presets["t"]["kind"], presets["t"]["device"]), ("mail", "pc"))
            self.assertEqual((presets["photo_germany"]["kind"], presets["photo_germany"]["device"]), ("photo", "phone"))
            self.assertTrue(all("vision" in m for m in st["models"]))
            self.assertFalse(st["running"])

            self.assertEqual(await (await client.post("/api/stop")).json(), {"ok": True, "stopped": False})

            r = await client.get("/api/photo/1?size=thumb")
            self.assertEqual((r.status, r.content_type), (200, "image/jpeg"))
            thumb = await r.read()
            self.assertEqual(thumb, (Path(os.environ["HPIS_DEMO_DATA"]) / "devices/photos/thumb/1.jpg").read_bytes())
            full = await (await client.get("/api/photo/1?size=full")).read()
            self.assertGreater(len(full), len(thumb))
            self.assertEqual((await client.get("/api/photo/999")).status, 404)

            rec = await (await client.get("/api/record/photo/1")).json()
            self.assertEqual((rec["kind"], rec["device"]), ("photo", "phone"))
            self.assertEqual(rec["thumb_url"], "/api/photo/1?size=thumb")
            self.assertIn("text", await (await client.get("/api/record/sport/2")).json())
            self.assertIn("subject", await (await client.get("/api/record/mail/3")).json())
            self.assertEqual((await client.get("/api/record/fax/1")).status, 404)

            phone_aid = next(d["aid"] for d in st["devices"] if d["id"] == "phone")
            c = await (await client.get("/api/cipher/1?kind=photo")).json()
            self.assertEqual(c["aid"], phone_aid)
            self.assertEqual(c["imageBytes"], len(full))
            self.assertGreater(c["bodyBytes"], len(full))  # the picture is inside the AES-GCM body
            self.assertTrue(c["tags"])
            self.assertEqual(c["vectorBytes"], 64 * 4 + 28)
            m = await (await client.get("/api/cipher/1")).json()  # default: mail, as before
            self.assertEqual(m["aid"], next(d["aid"] for d in st["devices"] if d["id"] == "pc"))

            plain = await (await client.get("/api/cloudview?kind=plain&limit=24")).json()
            self.assertEqual(len(plain["rows"]), 24)
            self.assertEqual({r["kind"] for r in plain["rows"]}, {"mail", "photo", "sport", "car"})
            self.assertTrue(all(r["owner"] == "林舟" and r["subject"] and r["date"] for r in plain["rows"]))
            self.assertTrue(all(r["thumb"] for r in plain["rows"] if r["kind"] == "photo"))
            self.assertEqual(plain["total"], sum(d["records"] for d in st["devices"]))
            sens = await (await client.get("/api/cloudview?kind=plain&limit=10&seed=3&sensitive=1")).json()
            self.assertEqual(len(sens["rows"]), 10)

            hp = await (await client.get("/api/cloudview?kind=hpis&limit=24")).json()
            self.assertEqual(len(hp["tenants"]), 12)
            self.assertEqual(sum(t["mine"] for t in hp["tenants"]), 4)
            self.assertGreaterEqual(len({r["aid"] for r in hp["rows"]}), 10)
            self.assertTrue(all({"aid", "aid_short", "tenant", "rid", "body", "tags", "vector"} <= set(r) for r in hp["rows"]))
            self.assertEqual(hp["docs"], sum(t["records"] for t in hp["tenants"]))

            s1 = await (await client.get("/api/spy", params={"q": "慕尼黑"})).json()
            s2 = await (await client.get("/api/spy", params={"q": "慕尼黑"})).json()
            self.assertGreaterEqual(s1["plain_hits"], 4)
            self.assertTrue(any(r["kind"] == "photo" for r in s1["samples"]))
            self.assertEqual((s1["hpis_hits"], s1["tenants"]), (0, 12))
            self.assertEqual(s1["hpis_scanned"], hp["docs"])
            self.assertFalse(any(r["matched"] for r in s1["hpis_rows"]))
            self.assertNotEqual([(r["aid"], r["rid"]) for r in s1["hpis_rows"]], [(r["aid"], r["rid"]) for r in s2["hpis_rows"]])
            self.assertIn("命中 0 条", s1["note"])

            # POST /api/run, then POST /api/stop while the answer streams.
            resp = await client.post("/api/run", json={"question": "慢慢说：德国的照片", "model": "qwen"})
            events, stopped = [], None
            async for raw in resp.content:
                ev = json.loads(raw)
                events.append(ev)
                if ev.get("type") == "token" and stopped is None:
                    stopped = await (await client.post("/api/stop")).json()
            self.assertEqual(stopped, {"ok": True, "stopped": True})
            self.assertTrue(events[-1]["stopped"])
            self.assertTrue(all(f["stopped"] for f in by_type(events, "finish")))


if __name__ == "__main__":
    unittest.main()
