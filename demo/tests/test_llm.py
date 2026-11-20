"""Offline tests for demo/llm.py (streaming client) and demo/prompts.py.

A local aiohttp server plays vLLM / OpenRouter, including their SSE quirks.

    .venv-demo/bin/python -m unittest demo.tests.test_llm -v
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web

from demo import llm, prompts


def chunk(content=None, *, model="hpis-qwen-27b", finish=None, reasoning=None, **extra) -> dict:
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    body.update(extra)
    return body


def sse(obj) -> bytes:
    data = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


class FakeServer:
    """Serves /v1/chat/completions with whatever coroutine ``self.script`` is."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.sent = 0
        self.disconnected = asyncio.Event()
        self.script = None

    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_get("/v1/models", self.models)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = self.runner.addresses[0][1]
        return f"http://127.0.0.1:{port}/v1"

    async def stop(self) -> None:
        await self.runner.cleanup()

    async def models(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"object": "list", "data": [{"id": "hpis-qwen-27b", "object": "model", "max_model_len": 32768}]}
        )

    async def chat(self, request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        self.requests.append({"headers": dict(request.headers), "raw": raw, "json": json.loads(raw)})
        return await self.script(request)

    async def stream(self, request: web.Request, parts, delay: float = 0.002) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        try:
            for part in parts:
                if isinstance(part, (int, float)):
                    await asyncio.sleep(part)
                    continue
                await resp.write(part)
                self.sent += len(part)
                await asyncio.sleep(delay)
            await resp.write_eof()
        except ConnectionResetError:  # the client went away, as some tests intend
            self.disconnected.set()
        except asyncio.CancelledError:
            self.disconnected.set()
            raise
        return resp


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeServer()
        self.base = await self.server.start()
        self.env = {"HPIS_LLM_QWEN_BASE_URL": self.base, "HPIS_LLM_QWEN_API_KEY": "test-key"}
        self.messages = [{"role": "user", "content": "交付时间？"}]

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def collect(self, **kwargs) -> list[dict]:
        kwargs.setdefault("env", self.env)
        kwargs.setdefault("max_tokens", 64)
        return [e async for e in llm.stream_answer("qwen", self.messages, **kwargs)]

    async def test_vllm_stream_with_split_utf8_comments_and_usage(self) -> None:
        line = sse(chunk("时间是12月15日[2]。"))
        cut = line.index("间".encode()) + 1  # split inside a multi-byte character
        usage = {
            "id": "x",
            "model": "hpis-qwen-27b",
            "choices": [],
            "usage": {"prompt_tokens": 321, "completion_tokens": 9, "total_tokens": 330},
        }

        async def script(request):
            return await self.server.stream(
                request,
                [
                    sse(chunk("", choices=[{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}])),
                    b": keep-alive\r\n\r\n",
                    sse(chunk("")),
                    sse(chunk("最终")).replace(b"\n\n", b"\r\n\r\n"),
                    sse(chunk("交付")),
                    line[:cut],
                    line[cut:],
                    sse(chunk(None, finish="stop")),
                    sse(usage),
                    b"data: [DONE]\n\n",
                ],
            )

        self.server.script = script
        events = await self.collect()
        tokens = [e["text"] for e in events if e["type"] == "token"]
        done = events[-1]
        self.assertEqual("".join(tokens), "最终交付时间是12月15日[2]。")
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["text"], "最终交付时间是12月15日[2]。")
        self.assertEqual((done["input_tokens"], done["output_tokens"]), (321, 9))
        self.assertFalse(done["usage_estimated"])
        self.assertEqual(done["finish_reason"], "stop")
        self.assertEqual(done["model"], "hpis-qwen-27b")
        self.assertEqual(done["provider"], "self-hosted")
        self.assertEqual(done["request_bytes"], len(self.server.requests[0]["raw"]))
        self.assertEqual(done["response_bytes"], self.server.sent)
        self.assertGreater(done["ttft_ms"], 0)
        self.assertLessEqual(done["ttft_ms"], done["total_ms"])
        self.assertGreater(done["tokens_per_s"], 0)
        self.assertEqual(done["reasoning_chars"], 0)

        sent = self.server.requests[0]
        body = sent["json"]
        self.assertEqual(sent["headers"]["Authorization"], "Bearer test-key")
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(body["model"], "hpis-qwen-27b")
        self.assertEqual(body["max_tokens"], 64)
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("reasoning", body)

    async def test_openrouter_style_comments_reasoning_provider_no_blank_lines(self) -> None:
        final = chunk(
            None,
            model="qwen/qwen3.6-27b",
            provider="FakeProvider",
            choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
            usage={
                "prompt_tokens": 50,
                "completion_tokens": 7,
                "completion_tokens_details": {"reasoning_tokens": 4},
                "prompt_tokens_details": {"cached_tokens": 32},
                "cost": 0.00012,
            },
        )
        packed = b"".join(
            f"data:{json.dumps(c, ensure_ascii=False)}\n".encode()  # no space, no blank line
            for c in (
                chunk(reasoning="先想", model="qwen/qwen3.6-27b", provider="FakeProvider"),
                chunk("答案", model="qwen/qwen3.6-27b", provider="FakeProvider"),
                chunk("在此", model="qwen/qwen3.6-27b", provider="FakeProvider"),
                final,
            )
        )

        async def script(request):
            return await self.server.stream(
                request, [b": OPENROUTER PROCESSING\n\n", b": OPENROUTER PROCESSING\n\n", packed, b"data: [DONE]\n"]
            )

        self.server.script = script
        events = await self.collect()
        done = events[-1]
        self.assertEqual([e["text"] for e in events if e["type"] == "token"], ["答案", "在此"])
        self.assertEqual(done["provider"], "FakeProvider")
        self.assertEqual(done["model"], "qwen/qwen3.6-27b")
        self.assertEqual(done["reasoning_tokens"], 4)
        self.assertEqual(done["reasoning_chars"], 2)
        self.assertEqual(done["cached_tokens"], 32)
        self.assertEqual(done["cost_usd"], 0.00012)

    async def test_leaked_think_block_is_hidden(self) -> None:
        parts = ["<thi", "nk>\n我先推理一下", "……</thi", "nk>\n\n", "答案", "是A[1]"]

        async def script(request):
            return await self.server.stream(request, [sse(chunk(p)) for p in parts] + [b"data: [DONE]\n\n"])

        self.server.script = script
        events = await self.collect()
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "答案是A[1]")
        self.assertEqual(events[-1]["text"], "答案是A[1]")
        self.assertGreater(events[-1]["reasoning_chars"], 0)

    async def test_http_error_is_reported(self) -> None:
        async def script(request):
            return web.json_response({"error": {"message": "invalid key", "code": 401}}, status=401)

        self.server.script = script
        events = await self.collect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["kind"], "http")
        self.assertEqual(events[0]["status"], 401)
        self.assertIn("invalid key", events[0]["message"])

    async def test_mid_stream_error_after_tokens(self) -> None:
        err = chunk(
            "",
            error={"code": 502, "message": "Provider disconnected unexpectedly"},
            choices=[{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
        )

        async def script(request):
            return await self.server.stream(request, [sse(chunk("部分")), sse(err), sse(chunk("不应出现"))])

        self.server.script = script
        events = await self.collect()
        self.assertEqual([e["type"] for e in events], ["token", "error"])
        self.assertEqual(events[-1]["kind"], "stream")
        self.assertEqual(events[-1]["code"], 502)
        self.assertEqual(events[-1]["partial_text"], "部分")
        self.assertIn("Provider disconnected", events[-1]["message"])

    async def test_timeout_keeps_partial_text(self) -> None:
        async def script(request):
            return await self.server.stream(request, [sse(chunk("先到")), 3.0, sse(chunk("太晚"))])

        self.server.script = script
        events = await self.collect(timeout=0.5)
        self.assertEqual(events[0], {"type": "token", "text": "先到"})
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["kind"], "timeout")
        self.assertEqual(events[-1]["partial_text"], "先到")

    async def test_eof_without_done_marker_or_final_newline(self) -> None:
        async def script(request):
            tail = f"data: {json.dumps(chunk('尾', finish='stop'), ensure_ascii=False)}".encode()
            return await self.server.stream(request, [sse(chunk("头")), tail])

        self.server.script = script
        events = await self.collect()
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["text"], "头尾")
        self.assertEqual(events[-1]["finish_reason"], "stop")
        self.assertTrue(events[-1]["usage_estimated"])  # no usage chunk: counted chunks
        self.assertEqual(events[-1]["output_tokens"], 2)

    async def test_server_answering_plain_json(self) -> None:
        async def script(request):
            return web.json_response(
                {
                    "model": "hpis-qwen-27b",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "整段回答[1]"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                }
            )

        self.server.script = script
        events = await self.collect()
        self.assertEqual([e["type"] for e in events], ["token", "done"])
        self.assertEqual(events[-1]["text"], "整段回答[1]")
        self.assertEqual(events[-1]["output_tokens"], 4)

    async def test_reasoning_only_is_an_error(self) -> None:
        async def script(request):
            return await self.server.stream(
                request, [sse(chunk(reasoning="想了很久" * 10)), sse(chunk(None, finish="length")), b"data: [DONE]\n\n"]
            )

        self.server.script = script
        events = await self.collect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "empty")
        self.assertEqual(events[0]["finish_reason"], "length")
        self.assertIn("reasoning", events[0]["message"])

    async def test_closing_early_disconnects_server(self) -> None:
        async def script(request):
            return await self.server.stream(request, [sse(chunk(f"{i} ")) for i in range(200)], delay=0.02)

        self.server.script = script
        stream = llm.stream_answer("qwen", self.messages, env=self.env)
        got = [await stream.__anext__(), await stream.__anext__()]
        await stream.aclose()
        self.assertEqual([g["type"] for g in got], ["token", "token"])
        await asyncio.wait_for(self.server.disconnected.wait(), 2.0)

    async def test_complete_and_probe(self) -> None:
        async def script(request):
            return await self.server.stream(
                request, [sse(chunk("好")), sse(chunk("的", finish="stop")), b"data: [DONE]\n\n"]
            )

        self.server.script = script
        done = await llm.complete("qwen", self.messages, env=self.env)
        self.assertEqual(done["text"], "好的")

        async def failing(request):
            return web.json_response({"error": {"message": "model not found"}}, status=404)

        self.server.script = failing
        with self.assertRaises(llm.LLMError) as ctx:
            await llm.complete("qwen", self.messages, env=self.env)
        self.assertEqual(ctx.exception.event["status"], 404)

        status = await llm.probe("qwen", env=self.env)
        self.assertTrue(status["ok"])
        self.assertEqual(status["max_model_len"], 32768)
        missing = await llm.probe("qwen", env={**self.env, "HPIS_LLM_QWEN_MODEL": "other"})
        self.assertFalse(missing["ok"])

    async def test_config_errors_are_events(self) -> None:
        events = [e async for e in llm.stream_answer("llama", self.messages, env={})]
        self.assertEqual(events[0]["kind"], "config")
        events = [e async for e in llm.stream_answer("qwen", self.messages, env={})]  # OpenRouter, no key
        self.assertEqual(events[0]["kind"], "config")
        self.assertIn("OPENROUTER_API_KEY", events[0]["message"])


class ConfigTests(unittest.TestCase):
    def test_openrouter_default(self) -> None:
        ep = llm.resolve_endpoint("qwen", env={"OPENROUTER_API_KEY": "k"})
        self.assertTrue(ep.is_openrouter)
        self.assertEqual(ep.chat_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(ep.model, "qwen/qwen3.6-27b")
        self.assertEqual(ep.api_key, "k")
        payload = llm.build_payload(ep, [], max_tokens=10, temperature=0.2)
        self.assertEqual(payload["reasoning"], {"effort": "none"})
        self.assertEqual(payload["provider"]["sort"], "throughput")
        self.assertNotIn("fp4", payload["provider"]["quantizations"])
        self.assertNotIn("chat_template_kwargs", payload)
        unrouted = llm.build_payload(ep, [], max_tokens=10, temperature=0.2, extra_body={"provider": None})
        self.assertNotIn("provider", unrouted)
        self.assertEqual(llm.resolve_endpoint("gemma", env={}).model, "google/gemma-4-31b-it")

    def test_h200_backend_and_overrides(self) -> None:
        env = {"HPIS_LLM_BACKEND": "h200", "OPENROUTER_API_KEY": "k"}
        qwen, gemma = llm.resolve_endpoint("qwen", env=env), llm.resolve_endpoint("gemma", env=env)
        self.assertEqual(qwen.chat_url, "http://127.0.0.1:18081/v1/chat/completions")
        self.assertEqual((qwen.model, gemma.model), ("hpis-qwen-27b", "hpis-gemma-4"))
        self.assertEqual(gemma.netloc, "127.0.0.1:18082")
        self.assertIsNone(qwen.api_key)  # the OpenRouter key never goes to the H200
        self.assertEqual(qwen.body, {"chat_template_kwargs": {"enable_thinking": False}})
        custom = llm.resolve_endpoint(
            "gemma",
            env={
                "HPIS_LLM_GEMMA_BASE_URL": "http://10.0.0.5:9000",
                "HPIS_LLM_GEMMA_MODEL": "g",
                "HPIS_LLM_GEMMA_API_KEY": "s",
                "HPIS_LLM_GEMMA_EXTRA_BODY": '{"chat_template_kwargs": null, "top_k": 64}',
            },
        )
        self.assertEqual(custom.chat_url, "http://10.0.0.5:9000/v1/chat/completions")
        self.assertEqual((custom.model, custom.api_key), ("g", "s"))
        self.assertEqual(custom.body, {"top_k": 64})
        bare = llm.resolve_endpoint({"base_url": "http://h:1/v1/chat/completions", "model": "m"})
        self.assertEqual(bare.chat_url, "http://h:1/v1/chat/completions")
        self.assertEqual(bare.body, {})
        named = llm.resolve_endpoint({"profile": "qwen", "base_url": "http://h:1", "model": "m"})
        self.assertEqual(named.body, {"chat_template_kwargs": {"enable_thinking": False}})
        with self.assertRaises(llm.LLMConfigError):
            llm.resolve_endpoint("qwen", env={"HPIS_LLM_QWEN_BASE_URL": "ftp://x"})
        with self.assertRaises(llm.LLMConfigError):
            llm.resolve_endpoint("qwen", env={"HPIS_LLM_QWEN_EXTRA_BODY": "[1]"})

    def test_ollama_backend(self) -> None:
        env = {"HPIS_LLM_BACKEND": "ollama", "OPENROUTER_API_KEY": "k"}
        qwen, gemma = llm.resolve_endpoint("qwen", env=env), llm.resolve_endpoint("gemma", env=env)
        self.assertEqual(qwen.native_url("api/chat"), "http://127.0.0.1:11434/api/chat")
        self.assertEqual((qwen.model, gemma.model), ("qwen3.6:27b", "gemma4:31b"))
        self.assertEqual((qwen.label, gemma.label), ("Qwen3.6-27B", "Gemma 4 31B"))
        self.assertEqual(qwen.body, {"think": False, "keep_alive": "30m"})
        self.assertIsNone(qwen.api_key)
        own = llm.resolve_endpoint("qwen", env={
            **env, "HPIS_LLM_OLLAMA_URL": "http://127.0.0.1:11435/v1", "HPIS_LLM_QWEN_MODEL": "qwen3.5:27b-q8_0",
            "HPIS_LLM_OLLAMA_KEEP_ALIVE": "-1", "HPIS_LLM_OLLAMA_NUM_CTX": "16384",
        })
        self.assertEqual(own.native_url("/api/tags"), "http://127.0.0.1:11435/api/tags")
        self.assertEqual(own.label, "Qwen3.5-27B")
        self.assertEqual(own.body, {"think": False, "keep_alive": -1, "options": {"num_ctx": 16384}})
        payload = llm.build_ollama_payload(own, [], max_tokens=9, temperature=0.2)
        self.assertEqual(payload["options"], {"temperature": 0.2, "num_predict": 9, "num_ctx": 16384})
        self.assertEqual(llm._ollama_label("hf.co/x/Qwen3.6-27B-GGUF:Q4_K_M"), "hf.co/x/Qwen3.6-27B-GGUF:Q4_K_M")
        with self.assertRaises(llm.LLMConfigError):
            llm.resolve_endpoint("qwen", env={**env, "HPIS_LLM_OLLAMA_NUM_CTX": "16k"})

    def test_describe_profiles_has_no_secrets(self) -> None:
        rows = llm.describe_profiles(env={"OPENROUTER_API_KEY": "secret", "HPIS_LLM_QWEN_MODEL": "qwen/qwen3.8-27b"})
        self.assertEqual([r["id"] for r in rows], ["qwen", "gemma"])
        self.assertEqual(rows[0]["label"], "Qwen3.8-27B")
        self.assertNotIn("secret", json.dumps(rows))


class ParserTests(unittest.TestCase):
    def feed_all(self, pieces: list[bytes]) -> list[llm.SSEEvent]:
        parser = llm.SSEParser()
        out = []
        for piece in pieces:
            out += parser.feed(piece)
        return out + parser.close()

    def test_bytewise_feeding(self) -> None:
        raw = b"\xef\xbb\xbf: hi\r\n" + sse(chunk("中文")) + b"event: ping\ndata: {}\n\n" + b"data: [DONE]\n\n"
        events = self.feed_all([raw[i : i + 1] for i in range(len(raw))])
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].json["choices"][0]["delta"]["content"], "中文")
        self.assertEqual(events[1].event, "ping")
        self.assertEqual(events[2].data, "[DONE]")

    def test_multiline_data_and_error_event(self) -> None:
        events = self.feed_all([b'data: {"a":\ndata: 1}\n\n', b'event: error\ndata: {"message": "boom"}\n\n'])
        self.assertEqual(events[0].json, {"a": 1})
        self.assertEqual(events[1].event, "error")

    def test_ndjson_lines(self) -> None:
        events = self.feed_all([b'{"choices": []}\n{"choices": []}\n'])
        self.assertEqual(len(events), 2)

    def test_think_stripper(self) -> None:
        s = llm.ThinkStripper()
        self.assertEqual(s.feed("<|chan"), "")
        self.assertEqual(s.feed("nel>thought\n<chan"), "")
        self.assertEqual(s.feed("nel|>答"), "答")
        self.assertEqual(s.feed("<think>不是开头"), "<think>不是开头")
        s = llm.ThinkStripper()
        self.assertEqual(s.feed("  \n"), "")
        self.assertEqual(s.feed("<"), "")
        self.assertEqual(s.flush(), "<")
        s = llm.ThinkStripper()
        self.assertEqual(s.feed("<think>没有闭合"), "")
        self.assertEqual(s.flush(), "")
        self.assertGreater(s.hidden_chars, 0)


class PromptTests(unittest.TestCase):
    evidence: ClassVar[list[dict]] = [
        {
            "n": 1,
            "id": 10,
            "date": "2026-11-10 17:42",
            "from": "陈思远 <c@example.com>",
            "subject": "周会纪要",
            "body": "交付时间 12 月 8 日。",
            "attachments": [],
        },
        {
            "n": 2,
            "id": 11,
            "date": "2026-11-17 18:05",
            "from": "陈思远 <c@example.com>",
            "subject": "交付时间调整",
            "body": "交付时间由 12 月 8 日推迟到 12 月 15 日。",
            "attachments": [{"name": "排期.csv", "text": "里程碑,日期\n交付,2026-12-15"}],
        },
    ]

    def test_messages_layout(self) -> None:
        messages = prompts.build_messages("最终什么时候交付？", self.evidence)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(messages[0]["content"], prompts.SYSTEM_PROMPT)
        user = messages[1]["content"]
        self.assertTrue(user.endswith("问题：最终什么时候交付？"))
        self.assertLess(user.index("[1] 日期：2026-11-10 17:42"), user.index("[2] 日期：2026-11-17 18:05"))
        self.assertIn("附件《排期.csv》：\n里程碑,日期", user)
        self.assertIn("主题：交付时间调整", user)
        empty = prompts.build_messages("问题？", [])
        self.assertIn("没有找到相关邮件", empty[1]["content"])

    def test_budget_trims_long_parts_fairly(self) -> None:
        long_body = "这是一段很长的邮件正文。" * 2000
        evidence = [
            {**self.evidence[0], "body": long_body},
            {**self.evidence[1], "attachments": [{"name": "大附件.txt", "text": "附件内容，" * 3000}]},
        ]
        messages = prompts.build_messages("问题？", evidence, max_context_tokens=6000)
        total = sum(prompts.estimate_tokens(m["content"]) for m in messages)
        self.assertLessEqual(total, 6000)
        self.assertGreater(total, 5000)
        user = messages[1]["content"]
        self.assertIn("后略", user)
        self.assertIn("交付时间由 12 月 8 日推迟到 12 月 15 日。", user)  # short body kept whole
        self.assertIn("问题：问题？", user)

    def test_estimate_tokens(self) -> None:
        self.assertAlmostEqual(prompts.estimate_tokens("中文"), 1.7)
        self.assertEqual(prompts.estimate_tokens("2026"), 4.0)
        self.assertLess(prompts.estimate_tokens("hello world"), 5)

    def test_parse_citations(self) -> None:
        text = "原定12月8日[1]，后改为12月15日[2]。另见【3】、[1, 4]和[5-7]，［8］。链接[文档](http://x)"
        self.assertEqual(prompts.parse_citations(text), [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(prompts.parse_citations("没有引用"), [])
        report = prompts.citation_report("见[2][9]", self.evidence)
        self.assertEqual(report, {"cited": [2, 9], "invalid": [9], "uncited": [1], "ok": False})
        self.assertTrue(prompts.citation_report("见[1][2]", [1, 2])["ok"])


class FakeOllama:
    """Ollama's native API as the demo uses it: /api/chat (NDJSON), /api/tags, /api/version.

    Like Ollama: a thinking model thinks unless the request says ``think: false``;
    each model answers ``parallel`` requests at once (OLLAMA_NUM_PARALLEL, default 1);
    a prompt longer than ``context`` characters loses its beginning.
    """

    MODELS = ("qwen3.6:27b", "gemma4:31b")

    def __init__(self, parallel: int = 1, context: int = 100_000, refuse_think: tuple[str, ...] = ()) -> None:
        self.parallel, self.context, self.refuse_think = parallel, context, refuse_think
        self.requests: list[dict] = []
        self.slots: dict[str, asyncio.Semaphore] = {}

    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/api/chat", self.chat)
        app.router.add_get("/api/tags", self.tags)
        app.router.add_get("/api/version", self.version)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{self.runner.addresses[0][1]}"

    async def stop(self) -> None:
        await self.runner.cleanup()

    async def tags(self, request: web.Request) -> web.Response:
        return web.json_response({"models": [{"name": m, "model": m} for m in self.MODELS]})

    async def version(self, request: web.Request) -> web.Response:
        return web.json_response({"version": "0.40.16"})

    async def chat(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        model = body["model"]
        if model not in self.MODELS:
            return web.json_response({"error": f"model '{model}' not found"}, status=404)
        if "think" in body and model in self.refuse_think:
            return web.json_response({"error": f'"{model}" does not support thinking'}, status=400)
        if not body.get("messages"):  # load request
            return web.json_response({"model": model, "message": {"role": "assistant", "content": ""}, "done": True})
        async with self.slots.setdefault(model, asyncio.Semaphore(self.parallel)):
            resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
            await resp.prepare(request)

            async def send(obj: dict) -> None:
                await resp.write(json.dumps(obj, ensure_ascii=False).encode() + b"\n")

            text = "".join(m["content"] for m in body["messages"])[-self.context :]
            if body.get("think", True):
                await send({"model": model, "message": {"role": "assistant", "content": "", "thinking": "先看证据。"}})
            if "西湖" in text:
                pieces, delay = ["西湖"] * 30, 0.05
            elif "12 月 15 日" in text:
                pieces, delay = ["天枢项目", "最后定在", "12月15日", "[1]", "。"], 0.005
            else:
                pieces, delay = ["证据里", "没有这件事。"], 0.005
            for piece in pieces:
                await asyncio.sleep(delay)
                await send({"model": model, "message": {"role": "assistant", "content": piece}, "done": False})
            await send(
                {"model": model, "message": {"role": "assistant", "content": ""}, "done": True,
                 "done_reason": "stop", "prompt_eval_count": 120, "eval_count": len(pieces)}
            )
            await resp.write_eof()
            return resp


class OllamaTests(unittest.IsolatedAsyncioTestCase):
    async def serve(self, **kwargs) -> dict[str, str]:
        self.server = FakeOllama(**kwargs)
        url = await self.server.start()
        self.addAsyncCleanup(self.server.stop)
        return {"HPIS_LLM_BACKEND": "ollama", "HPIS_LLM_OLLAMA_URL": url}

    async def collect(self, profile: str, env: dict[str, str]) -> list[dict]:
        messages = [{"role": "user", "content": "天枢项目最后定的交付日期？证据：12 月 15 日"}]
        return [e async for e in llm.stream_answer(profile, messages, max_tokens=64, env=env)]

    async def test_stream_turns_thinking_off_and_keeps_the_model_loaded(self) -> None:
        env = await self.serve()
        events = await self.collect("qwen", env)
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "天枢项目最后定在12月15日[1]。")
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual((done["output_tokens"], done["input_tokens"], done["reasoning_chars"]), (5, 120, 0))
        self.assertEqual(done["provider"], "Ollama")
        sent = self.server.requests[0]
        self.assertIs(sent["think"], False)
        self.assertEqual(sent["keep_alive"], "30m")
        self.assertEqual(sent["options"]["num_predict"], 64)
        self.assertNotIn("num_ctx", sent["options"])  # a shared model must not be reloaded

    async def test_retries_without_think_when_the_model_has_no_thinking_mode(self) -> None:
        env = await self.serve(refuse_think=("gemma4:31b",))
        events = await self.collect("gemma", env)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual([("think" in r) for r in self.server.requests], [True, False])

    async def test_unknown_model_is_an_http_error(self) -> None:
        env = await self.serve()
        events = await self.collect("qwen", {**env, "HPIS_LLM_QWEN_MODEL": "qwen9:1b"})
        self.assertEqual((events[-1]["type"], events[-1]["status"]), ("error", 404))
        self.assertIn("not found", events[-1]["message"])

    async def test_warm_loads_without_generating(self) -> None:
        env = await self.serve()
        result = await llm.warm("gemma", env={**env, "HPIS_LLM_OLLAMA_NUM_CTX": "16384"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.server.requests[-1], {
            "model": "gemma4:31b", "messages": [], "stream": False, "keep_alive": "30m", "options": {"num_ctx": 16384},
        })
        self.assertTrue((await llm.probe("qwen", env=env))["ok"])
        self.assertTrue((await llm.warm("qwen", env={"HPIS_LLM_BACKEND": "h200"}))["skipped"])

    async def run_check(self, env: dict[str, str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = await llm.check(["qwen"], env=env)
        return code, out.getvalue()

    async def test_check_passes_on_a_parallel_server(self) -> None:
        code, report = await self.run_check(await self.serve(parallel=2))
        self.assertEqual(code, 0, report)
        self.assertIn("并行     通过", report)
        self.assertIn("思考     已关闭", report)
        self.assertTrue(report.rstrip().endswith("CHECK PASS"))

    async def test_check_catches_queueing_and_a_short_context(self) -> None:
        code, report = await self.run_check(await self.serve(parallel=1, context=4000))
        self.assertEqual(code, 1, report)
        self.assertIn("长提示词 不通过", report)
        self.assertIn("并行     不通过", report)
        self.assertIn("--ollama", report)


if __name__ == "__main__":
    unittest.main()
