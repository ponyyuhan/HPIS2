"""A fake OpenAI-compatible model server for the deadline and stop tests.

Behaviour follows the question text in the last user message:
  "卡住"  never sends a token (the first-token deadline must fire)
  "慢慢"  first token after 0.2 s, then one token every 0.25 s for 60 s (until the client hangs up)
  else    a short cited answer

``requests`` keeps every request body (to check images reached the model);
``hangups`` counts streams the client closed before the end.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web


def _user_text(body: dict) -> str:
    for msg in reversed(body.get("messages") or []):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _chunk(text: str | None = None, finish: str | None = None) -> bytes:
    delta = {"content": text} if text is not None else {}
    body = {"model": "fake", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()


class FakeModel:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.hangups = 0
        self.runner: web.AppRunner | None = None

    async def start(self) -> str:
        app = web.Application(client_max_size=64 << 20)
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_get("/v1/models", self.models)
        self.runner = web.AppRunner(app, handler_cancellation=True)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{self.runner.addresses[0][1]}/v1"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    async def models(self, request: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "fake"}]})

    async def chat(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        text = _user_text(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            if "卡住" in text:
                await asyncio.sleep(120)
            elif "慢慢" in text:
                await asyncio.sleep(0.2)
                for i in range(240):
                    await response.write(_chunk(f"第{i}段。"))
                    await asyncio.sleep(0.25)
            else:
                for piece in ("根据证据，", "结论如下", "[1]。"):
                    await response.write(_chunk(piece))
                await response.write(_chunk(finish="stop"))
                await response.write(b"data: [DONE]\n\n")
        except (asyncio.CancelledError, ConnectionResetError):
            self.hangups += 1
            raise
        return response
