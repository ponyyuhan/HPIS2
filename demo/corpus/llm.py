"""Async OpenRouter chat client with an on-disk response cache.

Every raw response is stored under demo/data/cache/llm/<model>/<hh>/<sha>.json,
keyed by (model, messages, params), so generation is resumable and repeatable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "demo" / "data" / "cache" / "llm"
URL = "https://openrouter.ai/api/v1/chat/completions"

STRONG = "qwen/qwen3.6-plus"
FLASH = "qwen/qwen3.6-flash"
CHEAP = "google/gemma-4-26b-a4b-it"
LITE = "qwen/qwen3.5-flash-02-23"


def _key(model: str, messages: list, params: dict) -> str:
    blob = json.dumps({"m": model, "msg": messages, "p": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_path(model: str, key: str) -> Path:
    return CACHE / model.replace("/", "__") / key[:2] / f"{key}.json"


class LLM:
    def __init__(self, concurrency: int = 48, timeout: float = 240.0, log_every: int = 50):
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = timeout
        self.session: aiohttp.ClientSession | None = None
        self.cost = 0.0
        self.calls = 0
        self.hits = 0
        self.fail = 0
        self.t0 = time.time()
        self.log_every = log_every

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "Content-Type": "application/json",
                "X-Title": "demo-corpus",
            },
        )
        return self

    async def __aexit__(self, *exc):
        await self.session.close()

    async def chat(
        self,
        messages: list[dict],
        model: str = FLASH,
        temperature: float = 0.9,
        max_tokens: int = 4000,
        seed: int | None = None,
        tag: str = "",
        validate=None,
        attempts: int = 6,
    ) -> str | None:
        """Return assistant text (cached). ``validate(text)`` may raise to force a retry (new seed)."""
        params = {"temperature": temperature, "max_tokens": max_tokens, "seed": seed}
        for attempt in range(attempts):
            p = dict(params)
            if attempt:
                p["retry"] = attempt  # distinct cache key for validation retries
            key = _key(model, messages, p)
            path = cache_path(model, key)
            if path.exists():
                try:
                    rec = json.loads(path.read_text("utf-8"))
                    text = rec["text"]
                    if validate:
                        validate(text)
                    self.hits += 1
                    return text
                except Exception:
                    continue  # cached but invalid -> try next attempt key
            text = await self._call(messages, model, p, path, tag)
            if text is None:
                continue
            try:
                if validate:
                    validate(text)
                return text
            except Exception:
                continue
        self.fail += 1
        return None

    async def _call(self, messages, model, p, path, tag):
        body = {
            "model": model,
            "messages": messages,
            "temperature": p["temperature"],
            "max_tokens": p["max_tokens"],
            "usage": {"include": True},
        }
        if p.get("seed") is not None:
            body["seed"] = int(p["seed"]) + int(p.get("retry", 0))
        if model.startswith("qwen/"):
            body["reasoning"] = {"enabled": False}
        delay = 2.0
        for net_try in range(6):
            async with self.sem:
                try:
                    async with self.session.post(URL, json=body) as r:
                        status = r.status
                        raw = await r.text()
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    status, raw = -1, str(e)
            if status == 200:
                try:
                    data = json.loads(raw)
                    choice = data["choices"][0]
                    text = choice["message"].get("content") or ""
                    usage = data.get("usage") or {}
                    cost = float(usage.get("cost") or 0.0)
                    self.cost += cost
                    self.calls += 1
                    path.parent.mkdir(parents=True, exist_ok=True)
                    rec = {
                        "model": model,
                        "provider": data.get("provider"),
                        "tag": tag,
                        "params": p,
                        "messages": messages,
                        "text": text,
                        "finish": choice.get("finish_reason"),
                        "usage": usage,
                        "cost": cost,
                        "ts": time.time(),
                    }
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(rec, ensure_ascii=False), "utf-8")
                    tmp.replace(path)
                    if self.calls % self.log_every == 0:
                        el = time.time() - self.t0
                        print(
                            f"[llm] calls={self.calls} hits={self.hits} fail={self.fail} cost=${self.cost:.3f} {el:.0f}s",
                            flush=True,
                        )
                    if choice.get("finish_reason") == "length" and not text.strip():
                        return None
                    return text
                except Exception:
                    pass
            await asyncio.sleep(delay + random.random() * delay)
            delay = min(delay * 2, 60)
        return None


# ---------------------------------------------------------------- JSON helpers
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.M)


def parse_json(text: str):
    """Parse the first JSON object/array in an LLM reply (tolerates code fences)."""
    t = text.strip()
    t = _FENCE.sub("", t).strip()
    try:
        return json.loads(t, strict=False)
    except Exception:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no json")
    s = min(starts)
    closer = "}" if t[s] == "{" else "]"
    e = t.rfind(closer)
    try:
        return json.loads(t[s : e + 1], strict=False)
    except Exception:
        import json_repair  # tolerant fallback for unescaped quotes etc.

        out = json_repair.loads(t[s:])
        if not out:
            raise ValueError("unparseable json")
        return out


def total_cost() -> dict:
    """Sum the cost recorded in every cached response."""
    out: dict[str, float] = {}
    n: dict[str, int] = {}
    for f in CACHE.rglob("*.json"):
        try:
            rec = json.loads(f.read_text("utf-8"))
        except Exception:
            continue
        m = rec.get("model", "?")
        out[m] = out.get(m, 0.0) + float(rec.get("cost") or 0.0)
        n[m] = n.get(m, 0) + 1
    return {"by_model": {m: {"calls": n[m], "usd": round(out[m], 4)} for m in out}, "usd": round(sum(out.values()), 4)}
