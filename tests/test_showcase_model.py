"""Local HTTP contract checks for the selectable model adapters; no GPU is used."""

import json
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from showcase.local_model import model_answer, model_profiles


BUDGET = "一期获批预算为 36 万元。"
WINDOW = "2026 年 12 月 15 日 20:00 正式上线。"
DOCS = [
    {
        "id": 17,
        "subject": "预算批复",
        "date": "2026-11-10",
        "body": BUDGET,
        "attachments": [],
        "owner_key": "must-stay-on-device",
    },
    {
        "id": 38,
        "subject": "排期会签",
        "date": "2026-11-15",
        "body": "具体排期见附件。",
        "attachments": [{"name": "计划.csv", "content": "事项,说明\n切换," + WINDOW}],
    },
]
POINTS = [
    {
        "label": "批准金额",
        "sourceIds": [17],
        "text": BUDGET,
        "quotes": [{"id": 17, "attachment": None, "text": BUDGET}],
    },
    {
        "label": "上线时间",
        "sourceIds": [38],
        "text": WINDOW,
        "quotes": [{"id": 38, "attachment": "计划.csv", "text": WINDOW}],
    },
]
ANSWER = {
    "headline": "项目确认事项",
    "points": POINTS,
    "sourceIds": [17, 38],
    "matched": True,
    "mode": "extractive",
}
QUESTION = "这个项目批了多少钱、何时上线？"


class ShowcaseModelContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []
        self.reply_mode = "valid"

        async def completion(request):
            wire = await request.read()
            payload = json.loads(wire)
            self.sent.append(
                (request.path, request.headers.get("Authorization"), payload, len(wire))
            )
            if self.reply_mode == "unavailable":
                return web.Response(status=503)
            picked = [
                {"label": p["label"], "quotes": deepcopy(p["quotes"])} for p in POINTS
            ]
            if self.reply_mode == "invented":
                picked[0]["quotes"][0]["text"] = "一期获批预算为 66 万元。"
            if self.reply_mode == "format-only":
                picked[1]["quotes"][0]["text"] = WINDOW.replace(" ", "").rstrip("。")
            if self.reply_mode == "split":
                picked[1]["quotes"] = [
                    {"id": 38, "attachment": "计划.csv", "text": "2026 年 12 月 15 日"},
                    {"id": 38, "attachment": "计划.csv", "text": "20:00 正式上线。"},
                ]
            if self.reply_mode == "truncated":
                return web.json_response(
                    {
                        "model": "fixture/qwen-27b",
                        "choices": [
                            {"finish_reason": "length", "message": {"content": ""}}
                        ],
                        "usage": {"prompt_tokens": 91, "completion_tokens": 1024},
                    }
                )
            if self.reply_mode == "wrong-attachment":
                picked[1]["quotes"][0]["attachment"] = None
            content = json.dumps({"points": picked}, ensure_ascii=False)
            return web.json_response(
                {
                    "model": "fixture/" + payload["model"],
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                    "usage": {"prompt_tokens": 91, "completion_tokens": 37},
                }
            )

        app = web.Application()
        app.router.add_post("/private/v1/chat/completions", completion)
        app.router.add_post("/alternate/v1/chat/completions", completion)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        origin = str(self.server.make_url("/")).rstrip("/")
        env = {
            "HPIS_SHOWCASE_QWEN_BASE_URL": origin + "/private",
            "HPIS_SHOWCASE_QWEN_MODEL": "qwen-27b-fixture",
            "HPIS_SHOWCASE_QWEN_API_KEY": "fixture-key",
            "HPIS_SHOWCASE_GEMMA_BASE_URL": origin + "/alternate/v1",
            "HPIS_SHOWCASE_GEMMA_MODEL": "gemma-4-fixture",
            "HPIS_SHOWCASE_GEMMA_API_KEY": "",
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_both_profiles_use_their_own_endpoint_and_alias(self):
        profiles = model_profiles()
        self.assertEqual(
            [p["id"] for p in profiles if p["configured"]],
            ["extractive", "qwen", "gemma"],
        )
        self.assertNotIn("fixture-key", str(profiles))
        self.assertNotIn("127.0.0.1", str(profiles))
        for profile, alias, path, bearer in [
            (
                "qwen",
                "qwen-27b-fixture",
                "/private/v1/chat/completions",
                "Bearer fixture-key",
            ),
            ("gemma", "gemma-4-fixture", "/alternate/v1/chat/completions", None),
        ]:
            answer, meta = await model_answer(QUESTION, DOCS, ANSWER, profile=profile)
            route, token, request, length = self.sent[-1]
            self.assertEqual((route, token, request["model"]), (path, bearer, alias))
            message = json.loads(request["messages"][1]["content"])
            self.assertEqual(message["问题"], QUESTION)
            self.assertEqual([e["id"] for e in message["本次检索结果"]], [17, 38])
            self.assertNotIn("must-stay-on-device", str(message))
            self.assertEqual(request["temperature"], 0)
            self.assertEqual(answer["points"], POINTS)
            self.assertEqual(
                (answer["mode"], meta["status"], meta["model"]),
                (profile + "-api", "verified", "fixture/" + alias),
            )
            self.assertEqual((meta["requestBytes"], meta["outputTokens"]), (length, 37))
            self.assertGreater(meta["responseBytes"], 0)
        self.assertEqual(len(self.sent), 2)

    async def test_original_wording_is_restored_and_length_is_reported(self):
        for kind in ("split", "format-only"):
            self.reply_mode = kind
            answer, meta = await model_answer(QUESTION, DOCS, ANSWER, profile="qwen")
            self.assertEqual(meta["status"], "verified")
            quote = answer["points"][1]["quotes"][0]["text"]
            self.assertIn(quote, DOCS[1]["attachments"][0]["content"])
            self.assertIn("2026 年 12 月 15 日 20:00", quote)
        self.reply_mode = "truncated"
        answer, meta = await model_answer(QUESTION, DOCS, ANSWER, profile="qwen")
        self.assertEqual(answer, ANSWER)
        self.assertEqual(
            (meta["status"], meta["finishReason"], meta["outputTokens"]),
            ("evidence-fallback", "length", 1024),
        )

    async def test_default_and_unconfigured_modes_send_no_request(self):
        result, meta = await model_answer(QUESTION, DOCS, ANSWER)
        self.assertEqual((result["mode"], meta["status"]), ("extractive", "disabled"))
        with patch.dict(os.environ, {"HPIS_SHOWCASE_QWEN_BASE_URL": ""}):
            _, meta = await model_answer(QUESTION, DOCS, ANSWER, profile="qwen")
            self.assertEqual(meta["status"], "unconfigured")
        self.assertEqual(self.sent, [])

    async def test_bad_citations_or_upstream_failure_fall_back_explicitly(self):
        for mode, expected in (
            ("invented", "evidence-fallback"),
            ("wrong-attachment", "evidence-fallback"),
            ("unavailable", "unavailable"),
        ):
            self.reply_mode = mode
            answer, meta = await model_answer(QUESTION, DOCS, ANSWER, profile="gemma")
            self.assertEqual(meta["status"], expected)
            self.assertEqual(meta["engine"], "本地证据抽取")
            self.assertEqual(answer, ANSWER)
