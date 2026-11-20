import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from showcase import corpus

from showcase.backend import Showcase
from showcase.server import application


class PresentationFlow(unittest.IsolatedAsyncioTestCase):
    async def test_real_cloud_comparison_and_source_links(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    f"HPIS_SHOWCASE_{m}_{k}": ""
                    for m in ("QWEN", "GEMMA")
                    for k in ("BASE_URL", "MODEL")
                },
            ),
        ):
            engine = Showcase(Path(tmp), 48)
            async with TestClient(TestServer(application(engine))) as browser:
                await asyncio.wait_for(engine.preparing, 60)
                self.assertIsNone(engine.error)
                view = await (await browser.get("/api/bootstrap")).json()
                self.assertTrue(view["ready"])
                self.assertEqual(
                    {m["id"]: m["available"] for m in view["models"]},
                    {"extractive": True, "qwen": False, "gemma": False},
                )
                for name in ("qwen", "gemma"):
                    blocked = await browser.post(
                        "/api/run", json={"question": "测试", "model": name}
                    )
                    self.assertEqual(blocked.status, 409)
                    self.assertEqual(
                        (await blocked.json())["code"], "model-unconfigured"
                    )
                self.assertEqual(view["dataset"]["indexed"], 48)
                self.assertGreater(view["dataset"]["setupBytes"], 0)
                self.assertGreater(view["dataset"]["attachments"], 0)
                initial = view["last"]
                self.assertEqual(initial["plain"]["readRows"], 48)
                self.assertEqual(initial["protected"]["readRows"], 3)
                self.assertGreater(
                    initial["plain"]["uploadBytes"], initial["protected"]["uploadBytes"]
                )
                self.assertGreater(
                    initial["protected"]["cloudWireBytes"],
                    initial["protected"]["cloudBytes"],
                )
                self.assertEqual(
                    initial["plain"]["answer"], initial["protected"]["answer"]
                )
                self.assertEqual(initial["protected"]["quality"]["matched"], 3)

                question = view["presets"][1]["question"]
                stream = await browser.post("/api/run", json={"question": question})
                events = [json.loads(line) async for line in stream.content]
                self.assertEqual(events[0]["type"], "start")
                self.assertEqual(events[-1]["type"], "complete")
                run = events[-1]["run"]
                for route in ["plain", "protected"]:
                    self.assertEqual(set(run[route]["answer"]["sourceIds"]), {5, 6, 7})
                    self.assertTrue(
                        all(
                            run[route][key] > 0
                            for key in ("elapsedMs", "uploadBytes", "downloadBytes")
                        )
                    )
                    self.assertEqual(
                        {e["stage"] for e in events if e.get("route") == route},
                        {"upload", "search", "answer", "done"},
                    )
                mail = await (await browser.get("/api/evidence/1")).json()
                self.assertTrue(mail["body"])
                att = mail["attachments"][0]
                download = await browser.get(att["downloadUrl"])
                self.assertEqual(await download.text(), att["text"])
                needle = mail["subject"]
                library = await (
                    await browser.get("/api/library", params={"q": needle})
                ).json()
                self.assertIn(1, [m["id"] for m in library["items"]])
                self.assertEqual((await browser.get("/api/evidence/99999")).status, 404)
                impossible = await browser.post(
                    "/api/run", json={"question": "月球上的太阳能车卖了几台？"}
                )
                end = [json.loads(line) async for line in impossible.content][-1]
                self.assertEqual(end["run"]["protected"]["evidence"], [])
                self.assertEqual(end["run"]["protected"]["readRows"], 0)
                self.assertEqual(end["run"]["plain"]["readRows"], 48)
                self.assertEqual(
                    (await browser.post("/api/run", json={"question": ""})).status, 400
                )
                stamp = view["dataset"]["builtAt"]
            second = Showcase(Path(tmp), 48)
            async with TestClient(TestServer(application(second))) as resumed:
                await asyncio.wait_for(second.preparing, 60)
                self.assertEqual(
                    (await (await resumed.get("/api/bootstrap")).json())["dataset"][
                        "builtAt"
                    ],
                    stamp,
                )
                self.assertEqual(second.last["protected"]["quality"]["matched"], 3)

    async def test_both_model_profiles_enter_real_data_paths(self):
        calls = []
        fixture = web.Application()
        route_question = corpus.PRESETS[0]["question"]
        expected = corpus.make_answer(
            route_question,
            [corpus.message_content(corpus.message_for_id(i)) for i in (1, 2, 3)],
        )

        async def converse(request):
            payload = await request.json()
            context = json.loads(payload["messages"][-1]["content"])
            calls.append(
                (payload["model"], [msg["id"] for msg in context["本次检索结果"]])
            )
            content = json.dumps({"points": expected["sections"]}, ensure_ascii=False)
            return web.json_response(
                {
                    "model": payload["model"],
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 45},
                }
            )

        fixture.router.add_post("/v1/chat/completions", converse)
        async with TestServer(fixture) as peer:
            profiles = {name: f"fixture-{name}" for name in ("qwen", "gemma")}
            config = {}
            for name, model in profiles.items():
                prefix = f"HPIS_SHOWCASE_{name.upper()}"
                config[prefix + "_MODEL"] = model
                config[prefix + "_BASE_URL"] = str(peer.make_url("/v1"))
                config[prefix + "_API_KEY"] = ""
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, config):
                engine = Showcase(Path(tmp), 24)
                async with TestClient(TestServer(application(engine))) as browser:
                    await asyncio.wait_for(engine.preparing, 60)
                    self.assertIsNone(engine.error)
                    self.assertEqual(calls, [])
                    self.assertEqual(engine.last["model"], "extractive")
                    for name in profiles:
                        resp = await browser.post(
                            "/api/run", json={"question": route_question, "model": name}
                        )
                        events = [json.loads(line) async for line in resp.content]
                        self.assertEqual(events[-1]["type"], "complete")
                        run = events[-1]["run"]
                        self.assertEqual(run["model"], name)
                        for route in ("plain", "protected"):
                            self.assertEqual(run[route]["model"]["status"], "verified")
                            self.assertEqual(
                                run[route]["model"]["model"], profiles[name]
                            )
                            self.assertGreater(run[route]["modelWireBytes"], 0)
            self.assertEqual(calls.count(("fixture-qwen", [1, 2, 3])), 2)
            self.assertEqual(calls.count(("fixture-gemma", [1, 2, 3])), 2)
