"""Vision probe, image input for both backends, kind prompts and model deadlines.

    .venv-demo/bin/python -m unittest demo.tests.test_vision -v
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web

from demo import answer, llm, prompts
from demo.tests.fakes import FakeModel

JPEG = b"\xff\xd8\xff\xe0fake-picture\xff\xd9"


class FakeProbeServer:
    """Ollama /api/show and OpenRouter /models/{id}/endpoints, /models."""

    def __init__(self) -> None:
        self.vision_models = {"qwen3.8:27b", "qwen/qwen3.8-27b"}

    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/api/show", self.show)
        app.router.add_get("/api/v1/models/{author}/{slug}/endpoints", self.endpoints)
        app.router.add_get("/api/v1/models", self.models)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{self.runner.addresses[0][1]}"

    async def stop(self) -> None:
        await self.runner.cleanup()

    async def show(self, request: web.Request) -> web.Response:
        name = (await request.json()).get("model")
        caps = ["completion", "vision"] if name in self.vision_models else ["completion"]
        return web.json_response({"capabilities": caps, "modelfile": ""})

    async def endpoints(self, request: web.Request) -> web.Response:
        model = f"{request.match_info['author']}/{request.match_info['slug']}"
        if model == "old/listed-only":
            return web.json_response({"error": "not found"}, status=404)
        mods = ["text", "image"] if model in self.vision_models else ["text"]
        return web.json_response({"data": {"id": model, "architecture": {"input_modalities": mods}}})

    async def models(self, request: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "old/listed-only", "architecture": {"modality": "text+image->text"}}]})


class VisionProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeProbeServer()
        self.base = await self.server.start()
        llm._VISION.clear()
        self.env = {"HPIS_LLM_BACKEND": "ollama", "HPIS_LLM_OLLAMA_URL": self.base}

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        llm._VISION.clear()

    async def test_ollama_capabilities(self) -> None:
        env = {**self.env, "HPIS_LLM_QWEN_MODEL": "qwen3.8:27b", "HPIS_LLM_GEMMA_MODEL": "gemma4:12b"}
        self.assertTrue(await llm.probe_vision("qwen", env=env))
        self.assertFalse(await llm.probe_vision("gemma", env=env))
        self.assertTrue(llm.vision_cached("qwen", env=env))
        rows = {r["id"]: r for r in llm.describe_profiles(env)}
        self.assertTrue(rows["qwen"]["vision"])
        self.assertFalse(rows["gemma"]["vision"])
        self.assertEqual(await llm.probe_vision_all(env), {"qwen": True, "gemma": False})

    async def test_openrouter_input_modalities(self) -> None:
        def ep(model):
            return llm.Endpoint("qwen", self.base + "/api/v1", model, "key", {}, model, "openrouter")

        self.assertTrue(await llm.probe_vision(ep("qwen/qwen3.8-27b"), env={}))
        self.assertFalse(await llm.probe_vision(ep("text/only-model"), env={}))
        self.assertTrue(await llm.probe_vision(ep("old/listed-only"), env={}))  # falls back to the model list

    async def test_override_and_unknown(self) -> None:
        env = {**self.env, "HPIS_LLM_GEMMA_MODEL": "gemma4:12b", "HPIS_LLM_VISION": "1"}
        self.assertTrue(await llm.probe_vision("gemma", env=env))
        env["HPIS_LLM_VISION"] = "0"
        self.assertFalse(await llm.probe_vision("qwen", env=env))
        vllm = {"HPIS_LLM_BACKEND": "h200"}
        self.assertIsNone(await llm.probe_vision("qwen", env=vllm))  # vLLM does not say: captions only
        down = {"HPIS_LLM_BACKEND": "ollama", "HPIS_LLM_OLLAMA_URL": "http://127.0.0.1:9"}
        self.assertIsNone(await llm.probe_vision("qwen", env=down, timeout=1))


class ImagePayloadTests(unittest.TestCase):
    def messages(self):
        return [
            {"role": "system", "content": "s"},
            {"role": "user", "content": [
                {"type": "text", "text": "看图"},
                {"type": "image", "data": JPEG, "mime": "image/jpeg"},
                {"type": "text", "text": "问题"},
            ]},
        ]

    def test_openai_form(self) -> None:
        ep = llm.resolve_endpoint("qwen", {"HPIS_LLM_BACKEND": "h200"})
        payload = llm.build_payload(ep, self.messages(), max_tokens=10, temperature=0.1)
        parts = payload["messages"][1]["content"]
        self.assertEqual([p["type"] for p in parts], ["text", "image_url", "text"])
        url = parts[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), JPEG)
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "s"})
        self.assertTrue(llm.has_images(self.messages()))

    def test_ollama_form(self) -> None:
        ep = llm.resolve_endpoint("qwen", {"HPIS_LLM_BACKEND": "ollama"})
        payload = llm.build_ollama_payload(ep, self.messages(), max_tokens=10, temperature=0.1)
        user = payload["messages"][1]
        self.assertEqual(base64.b64decode(user["images"][0]), JPEG)
        self.assertIn("看图", user["content"])
        self.assertIn("第1张图", user["content"])
        self.assertNotIn("images", payload["messages"][0])

    def test_text_messages_unchanged(self) -> None:
        msgs = [{"role": "user", "content": "你好"}]
        ep = llm.resolve_endpoint("qwen", {"HPIS_LLM_BACKEND": "h200"})
        self.assertEqual(llm.build_payload(ep, msgs, max_tokens=5, temperature=0)["messages"], msgs)
        self.assertFalse(llm.has_images(msgs))


class KindPromptTests(unittest.TestCase):
    PHOTO = {"n": 1, "id": 3, "kind": "photo", "date": "2025-09-16 08:12", "title": "慕尼黑 · 玛利亚广场",
             "body": "钟楼下的人群。", "place": "德国 慕尼黑 · 玛利亚广场", "caption": "钟楼下的人群。", "tags": ["广场"]}

    def test_photo_with_and_without_images(self) -> None:
        with_img = prompts.build_messages("德国的照片", [{**self.PHOTO, "image": JPEG}], kind="photo")
        parts = with_img[1]["content"]
        self.assertIsInstance(parts, list)
        self.assertEqual([p["type"] for p in parts], ["text", "text", "image", "text"])
        self.assertIn("照片本身也一并给出", with_img[0]["content"])
        captions = prompts.build_messages("德国的照片", [self.PHOTO], kind="photo")
        self.assertIsInstance(captions[1]["content"], str)
        self.assertIn("地点：德国 慕尼黑", captions[1]["content"])
        self.assertNotIn("照片本身", captions[0]["content"])

    def test_sport_car_and_note(self) -> None:
        item = {"n": 1, "id": 9, "date": "2026-11-02 08:00", "title": "10月运动月报", "body": "跑步12次",
                "facts": ["数据：runs=12"]}
        msgs = prompts.build_messages("运动情况", [item], kind="sport", note="检索说明：没有找到网球记录。")
        self.assertIn("手表记录", msgs[0]["content"])
        self.assertIn("数据：runs=12", msgs[1]["content"])
        self.assertLess(msgs[1]["content"].index("检索说明"), msgs[1]["content"].index("问题："))
        car = prompts.build_messages("充电", [item], kind="car")
        self.assertIn("车机记录", car[0]["content"])

    def test_mail_prompt_is_unchanged(self) -> None:
        mail = {"n": 1, "id": 1, "date": "2026-11-17 18:05", "from": "a <a@b>", "subject": "s", "body": "b"}
        self.assertEqual(prompts.build_messages("q", [mail]), prompts.build_messages("q", [mail], kind="mail", note=None))


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.model = FakeModel()
        base = await self.model.start()
        self.patch = mock.patch.dict(
            os.environ,
            {"HPIS_LLM_BACKEND": "h200", "HPIS_LLM_QWEN_BASE_URL": base, "HPIS_LLM_QWEN_MODEL": "fake",
             "HPIS_DEMO_OFFLINE": "0"},
        )
        self.patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.replay = mock.patch.object(answer, "REPLAY_DIR", Path(self.tmp.name))  # never the real cache
        self.replay.start()
        self.evidence = [{"n": 1, "id": 1, "date": "2026-11-17 18:05", "from": "a <a@b>", "subject": "交付时间", "body": "b"}]

    async def asyncTearDown(self) -> None:
        await answer.close_sessions()
        self.replay.stop()
        self.tmp.cleanup()
        self.patch.stop()
        await self.model.stop()

    async def collect(self, question, **kw):
        out = []
        stream = answer.generate(question, self.evidence, "qwen", **kw)
        async for event in stream:
            out.append(event)
        return out

    async def test_first_token_deadline_gives_an_extract(self) -> None:
        t0 = asyncio.get_running_loop().time()
        events = await self.collect("卡住了吗", first_token_s=0.6, answer_s=5)
        self.assertLess(asyncio.get_running_loop().time() - t0, 3)
        done = events[-1]
        self.assertTrue(done["timed_out"])
        self.assertTrue(done["fallback"])
        self.assertIn("证据摘录", "".join(e["text"] for e in events if e["type"] == "token"))
        await asyncio.sleep(0.2)
        self.assertGreaterEqual(self.model.hangups, 1)  # the model request was closed

    async def test_answer_deadline_keeps_what_was_generated(self) -> None:
        events = await self.collect("慢慢说", first_token_s=3, answer_s=1.2)
        done = events[-1]
        self.assertTrue(done["timed_out"])
        self.assertTrue(done["partial"])
        text = "".join(e["text"] for e in events if e["type"] == "token")
        self.assertIn("第0段", text)
        self.assertNotIn("证据摘录", text)

    async def test_normal_answer_is_not_timed_out(self) -> None:
        events = await self.collect("正常的问题", first_token_s=3, answer_s=5)
        self.assertEqual(events[-1]["type"], "done")
        self.assertFalse(events[-1]["timed_out"])

    async def test_photo_extract_when_the_model_is_down(self) -> None:
        with mock.patch.dict(os.environ, {"HPIS_LLM_QWEN_BASE_URL": "http://127.0.0.1:9/v1"}):
            photo = [{**KindPromptTests.PHOTO}]
            events = [e async for e in answer.generate("照片", photo, "qwen", kind="photo")]
        text = "".join(e["text"] for e in events if e["type"] == "token")
        self.assertIn("德国 慕尼黑", text)
        self.assertTrue(events[-1]["fallback"])


if __name__ == "__main__":
    unittest.main()
