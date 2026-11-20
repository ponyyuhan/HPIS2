"""Streaming chat client for the demo's answer models (aiohttp, no OpenAI SDK).

Two profiles, ``qwen`` and ``gemma``, talk to any OpenAI-compatible
``/v1/chat/completions`` endpoint:

* OpenRouter by default (key from ``OPENROUTER_API_KEY``), for testing without
  the GPU server;
* the user's H200 running vLLM (``demo/deploy/h200``), normally reached through
  an SSH tunnel on 127.0.0.1:18081 (Qwen) and 127.0.0.1:18082 (Gemma).

Environment, with ``P`` = ``QWEN`` or ``GEMMA``:

  HPIS_LLM_BACKEND       ``openrouter`` (default) or ``h200``: endpoint used for
                         every profile that has no explicit BASE_URL
  HPIS_LLM_P_BASE_URL    e.g. http://127.0.0.1:18081/v1
  HPIS_LLM_P_MODEL       model id (OpenRouter) or vLLM ``--served-model-name``
  HPIS_LLM_P_API_KEY     bearer token; OpenRouter falls back to OPENROUTER_API_KEY
  HPIS_LLM_P_EXTRA_BODY  JSON object merged into each request (null drops a key)
  HPIS_LLM_P_LABEL       display name

Thinking is switched off in every request so the answer starts immediately:
OpenRouter receives ``reasoning: {"effort": "none"}`` (plus throughput-first
routing that skips fp4 hosts) and vLLM receives
``chat_template_kwargs: {"enable_thinking": false}``.  A leading
``<think>…</think>`` or Gemma ``<|channel>…<channel|>`` block that still reaches
the content (server without a reasoning parser) is hidden and counted.

``stream_answer`` yields ``{"type": "token", "text": …}`` events, then one
``{"type": "done", …}`` with timings and usage, or one ``{"type": "error", …}``.
TTFT runs from sending the request to the first visible answer character.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Request fields that switch thinking off.  Checked against OpenRouter on
# 2026-11-20 (see demo/deploy/h200/VERIFIED.md): Qwen3.6/3.8 reason by default
# and accept effort "none"; Gemma 4 does not reason unless asked, and the same
# field is accepted and keeps it off.  vLLM passes chat_template_kwargs to the
# model's chat template; both templates understand enable_thinking.
#
# OpenRouter routing: default routing spreads requests over many hosts and
# gave first-token tails up to 8.4 s; sorting by throughput while skipping fp4
# hosts kept p90 first-token latency at 0.51-0.62 s (worst 1.6 s) in the same
# benchmark.
_OPENROUTER_BODY = {
    "reasoning": {"effort": "none"},
    "provider": {"sort": "throughput", "quantizations": ["bf16", "fp16", "fp8", "unknown"]},
}
_VLLM_NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}

PROFILES: dict[str, dict[str, Any]] = {
    "qwen": {
        "label": "Qwen3.6-27B",
        "openrouter_model": "qwen/qwen3.6-27b",
        "h200_model": "hpis-qwen-27b",
        "h200_port": 18081,
        "openrouter_body": _OPENROUTER_BODY,
        "h200_body": _VLLM_NO_THINK,
    },
    "gemma": {
        "label": "Gemma 4 31B",
        "openrouter_model": "google/gemma-4-31b-it",
        "h200_model": "hpis-gemma-4",
        "h200_port": 18082,
        "openrouter_body": _OPENROUTER_BODY,
        "h200_body": _VLLM_NO_THINK,
    },
}

# Display names for OpenRouter ids someone may put in HPIS_LLM_P_MODEL.
KNOWN_LABELS = {
    "qwen/qwen3.6-27b": "Qwen3.6-27B",
    "qwen/qwen3.8-27b": "Qwen3.8-27B",
    "google/gemma-4-31b-it": "Gemma 4 31B",
}

_MAX_ERROR_BODY = 64 * 1024


class LLMConfigError(ValueError):
    """The selected profile has no usable endpoint, model or key."""


class LLMError(RuntimeError):
    """Raised by complete() when the stream ends in an error event."""

    def __init__(self, event: Mapping[str, Any]):
        self.event = dict(event)
        super().__init__(self.event.get("message", "model request failed"))


@dataclass(frozen=True)
class Endpoint:
    profile: str
    base_url: str
    model: str
    api_key: str | None = None
    body: Mapping[str, Any] = field(default_factory=dict)
    label: str = ""

    @property
    def is_openrouter(self) -> bool:
        host = urlsplit(self.base_url).hostname or ""
        return host == "openrouter.ai" or host.endswith(".openrouter.ai")

    @property
    def netloc(self) -> str:
        return urlsplit(self.base_url).netloc

    def url(self, suffix: str) -> str:
        parts = urlsplit(self.base_url)
        path = parts.path.rstrip("/")
        path = path.removesuffix("/chat/completions")
        if not path:
            path = "/v1"
        return urlunsplit((parts.scheme, parts.netloc, f"{path}/{suffix}", "", ""))

    @property
    def chat_url(self) -> str:
        return self.url("chat/completions")

    def describe(self) -> dict[str, Any]:
        """Public view without the key."""
        return {
            "id": self.profile,
            "label": self.label,
            "model": self.model,
            "backend": "openrouter" if self.is_openrouter else "self-hosted",
            "endpoint": self.netloc,
            "configured": bool(self.api_key) or not self.is_openrouter,
        }


def _env_json(env: Mapping[str, str], name: str) -> dict[str, Any]:
    raw = env.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise LLMConfigError(f"{name} must be a JSON object")
    return value


def _merge(base: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; a None value in ``extra`` removes the key."""
    out = dict(base)
    for key, value in extra.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_endpoint(profile: str | Endpoint | Mapping[str, Any], env: Mapping[str, str] | None = None) -> Endpoint:
    """Turn a profile name (or an explicit dict / Endpoint) into an endpoint."""
    if isinstance(profile, Endpoint):
        return profile
    env = os.environ if env is None else env
    if isinstance(profile, Mapping):
        spec = dict(profile)
        missing = [k for k in ("base_url", "model") if not spec.get(k)]
        if missing:
            raise LLMConfigError(f"endpoint dict lacks {', '.join(missing)}")
        name = str(spec.get("profile", "custom"))
        defaults: dict[str, Any] = PROFILES.get(name, {})
    else:
        name = str(profile).strip().lower()
        if name not in PROFILES:
            raise LLMConfigError(f"unknown model profile {profile!r}; use one of {sorted(PROFILES)}")
        defaults = PROFILES[name]
        prefix = f"HPIS_LLM_{name.upper()}_"
        backend = env.get("HPIS_LLM_BACKEND", "openrouter").strip().lower() or "openrouter"
        if backend not in ("openrouter", "h200", "local", "vllm"):
            raise LLMConfigError(f"HPIS_LLM_BACKEND must be openrouter or h200, not {backend!r}")
        base = env.get(prefix + "BASE_URL", "").strip()
        if not base:
            base = OPENROUTER_BASE_URL if backend == "openrouter" else f"http://127.0.0.1:{defaults['h200_port']}/v1"
        spec = {
            "base_url": base,
            "model": env.get(prefix + "MODEL", "").strip(),
            "api_key": env.get(prefix + "API_KEY", "").strip() or None,
            "label": env.get(prefix + "LABEL", "").strip(),
            "body": _env_json(env, prefix + "EXTRA_BODY"),
        }
    parts = urlsplit(spec["base_url"])
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        raise LLMConfigError(f"invalid base URL {spec['base_url']!r}")
    on_openrouter = Endpoint(name, spec["base_url"], "-").is_openrouter
    kind = "openrouter" if on_openrouter else "h200"
    model = spec.get("model") or defaults.get(f"{kind}_model")
    if not model:
        raise LLMConfigError(f"no model configured for profile {name!r}")
    api_key = spec.get("api_key") or None
    if not api_key and on_openrouter:
        api_key = env.get("OPENROUTER_API_KEY", "").strip() or None
    body = _merge(defaults.get(f"{kind}_body", {}), spec.get("body") or spec.get("extra_body") or {})
    label = spec.get("label") or KNOWN_LABELS.get(model) or defaults.get("label") or model
    return Endpoint(name, spec["base_url"], model, api_key, body, label)


def describe_profiles(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Profiles for the UI's model picker (no secrets)."""
    out = []
    for name in PROFILES:
        try:
            out.append(resolve_endpoint(name, env).describe())
        except LLMConfigError as exc:
            out.append({"id": name, "label": PROFILES[name]["label"], "configured": False, "detail": str(exc)})
    return out


def build_payload(
    endpoint: Endpoint,
    messages: list[Mapping[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    stream: bool = True,
    extra_body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": list(messages),
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    payload = _merge(payload, endpoint.body)
    return _merge(payload, extra_body or {})


# --------------------------------------------------------------------------- SSE


@dataclass
class SSEEvent:
    event: str
    data: str
    json: Any = None  # parsed data, None for [DONE] or non-JSON payloads


def _try_json(text: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


class SSEParser:
    """Incremental ``text/event-stream`` decoder that tolerates common quirks.

    Handles CRLF, comments (``: OPENROUTER PROCESSING``), ``data:`` with or
    without a space, ``event:`` names, multi-line data, a stream that ends
    without a final newline, and servers that omit the blank line between
    events: a ``data:`` line that is already a complete JSON document (or
    ``[DONE]``) is dispatched at once, so no token waits for the separator.
    Bare JSON lines (NDJSON instead of SSE) are accepted as data too.
    """

    def __init__(self) -> None:
        self._buf = b""
        self._data: list[str] = []
        self._event = ""
        self._first = True
        self.comments = 0

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        self._buf += chunk
        out: list[SSEEvent] = []
        while True:
            end = self._buf.find(b"\n")
            if end < 0:
                break
            raw, self._buf = self._buf[:end], self._buf[end + 1 :]
            self._line(raw, out)
        return out

    def close(self) -> list[SSEEvent]:
        out: list[SSEEvent] = []
        if self._buf:
            raw, self._buf = self._buf, b""
            self._line(raw, out)
        self._dispatch(out)
        return out

    def _line(self, raw: bytes, out: list[SSEEvent]) -> None:
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        line = raw.decode("utf-8", errors="replace")
        if self._first:
            line = line.lstrip("﻿")
            self._first = False
        if not line:
            self._dispatch(out)
            return
        if line.startswith(":"):
            self.comments += 1
            return
        if line.lstrip().startswith(("{", "[")) and not self._data:
            ok, obj = _try_json(line)  # NDJSON instead of SSE
            if ok:
                self._emit(out, line, obj)
            return
        name, colon, value = line.partition(":")
        if not colon:
            return
        value = value.removeprefix(" ")
        if name == "data":
            self._data.append(value)
            text = "\n".join(self._data)
            if text.strip() == "[DONE]":
                self._emit(out, text.strip(), None)
                return
            ok, obj = _try_json(text)
            if ok:
                self._emit(out, text, obj)
        elif name == "event":
            self._event = value
        # id:, retry: and unknown fields are irrelevant here.

    def _emit(self, out: list[SSEEvent], text: str, obj: Any) -> None:
        out.append(SSEEvent(self._event or "message", text, obj))
        self._data = []
        self._event = ""

    def _dispatch(self, out: list[SSEEvent]) -> None:
        if self._data:
            text = "\n".join(self._data)
            ok, obj = _try_json(text)
            self._emit(out, text, obj if ok else None)
        self._event = ""


# ------------------------------------------------------------ hidden reasoning


class ThinkStripper:
    """Hide a leading reasoning block that leaked into ``delta.content``.

    Recognises ``<think>…</think>`` (Qwen) and ``<|channel>…<channel|>``
    (Gemma 4) at the start of the answer, and drops leading whitespace, so the
    first visible token is real answer text.
    """

    TAGS = (("<think>", "</think>"), ("<|channel>", "<channel|>"))

    def __init__(self) -> None:
        self._pending = ""
        self._close: str | None = None
        self._passthrough = False
        self.hidden_chars = 0

    def feed(self, text: str) -> str:
        if self._passthrough:
            return text
        self._pending += text
        while True:
            if self._close is None:
                head = self._pending.lstrip()
                if not head:
                    self._pending = ""
                    return ""
                for opener, closer in self.TAGS:
                    if head.startswith(opener):
                        self._close = closer
                        self._pending = head[len(opener) :]
                        break
                    if opener.startswith(head):
                        self._pending = head
                        return ""  # could still become an opener
                else:
                    self._pending = ""
                    self._passthrough = True
                    return head
                continue
            end = self._pending.find(self._close)
            if end < 0:
                keep = len(self._close) - 1
                cut = max(0, len(self._pending) - keep)
                self.hidden_chars += cut
                self._pending = self._pending[cut:]
                return ""
            self.hidden_chars += end
            self._pending = self._pending[end + len(self._close) :]
            self._close = None

    def flush(self) -> str:
        if self._passthrough:
            return ""
        rest, self._pending = self._pending, ""
        if self._close is not None:
            self.hidden_chars += len(rest)
            return ""
        self._passthrough = True
        return rest.lstrip()


# ------------------------------------------------------------------ streaming


def _is_local_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):  # content parts
        return "".join(
            str(p.get("text") or "")
            for p in value
            if isinstance(p, Mapping) and p.get("type") in (None, "text", "output_text")
        )
    return ""


def _error_message(obj: Any, fallback: str) -> str:
    if isinstance(obj, Mapping):
        err = obj.get("error", obj)
        if isinstance(err, Mapping):
            msg = err.get("message") or err.get("detail") or err.get("msg")
            meta = err.get("metadata")
            if isinstance(meta, Mapping) and meta.get("raw"):
                msg = f"{msg} ({str(meta['raw'])[:300]})"
            if msg:
                return str(msg)
        elif isinstance(err, str) and err:
            return err
        if isinstance(obj.get("detail"), str):
            return obj["detail"]
        if isinstance(obj.get("message"), str):
            return obj["message"]
    return fallback


class _Run:
    """Accumulates one request's state and turns it into events."""

    def __init__(self, ep: Endpoint, request_bytes: int, t0: float) -> None:
        self.ep = ep
        self.t0 = t0
        self.request_bytes = request_bytes
        self.response_bytes = 0
        self.strip = ThinkStripper()
        self.pieces: list[str] = []
        self.t_first: float | None = None
        self.t_last: float | None = None
        self.model: str | None = None
        self.provider: str | None = None
        self.finish_reason: str | None = None
        self.usage: Mapping[str, Any] | None = None
        self.reasoning_chars = 0
        self.content_chunks = 0
        self.error: dict[str, Any] | None = None
        self.saw_done = False

    def ms(self, t: float | None) -> float | None:
        return None if t is None else round((t - self.t0) * 1000, 1)

    def visible(self, text: str) -> list[dict[str, Any]]:
        out = self.strip.feed(text)
        if not out:
            return []
        now = time.perf_counter()
        if self.t_first is None:
            self.t_first = now
        self.t_last = now
        self.pieces.append(out)
        return [{"type": "token", "text": out}]

    def handle(self, ev: SSEEvent) -> list[dict[str, Any]]:
        if ev.data.strip() == "[DONE]":
            self.saw_done = True
            return []
        obj = ev.json
        if not isinstance(obj, Mapping):
            if ev.event == "error":
                self.error = {"kind": "stream", "message": ev.data[:500] or "stream error"}
            return []
        if ev.event == "error" or obj.get("error"):
            self.error = {"kind": "stream", "message": _error_message(obj, "stream error")}
            code = obj.get("error", {}).get("code") if isinstance(obj.get("error"), Mapping) else None
            if code is not None:
                self.error["code"] = code
        self.model = obj.get("model") or self.model
        self.provider = obj.get("provider") or self.provider
        if isinstance(obj.get("usage"), Mapping):
            self.usage = obj["usage"]
        events: list[dict[str, Any]] = []
        choices = obj.get("choices") or []
        for choice in choices[:1]:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                delta = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
            reasoning = delta.get("reasoning") or delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                self.reasoning_chars += len(reasoning)
            elif isinstance(delta.get("reasoning_details"), list):
                for item in delta["reasoning_details"]:
                    if isinstance(item, Mapping):
                        self.reasoning_chars += len(str(item.get("text") or item.get("summary") or ""))
            text = _text_of(delta.get("content"))
            if text:
                self.content_chunks += 1
                events += self.visible(text)
            reason = choice.get("finish_reason")
            if reason:
                self.finish_reason = reason
                if reason == "error" and self.error is None:
                    self.error = {"kind": "stream", "message": _error_message(obj, "provider reported an error")}
        return events

    def finish(self) -> list[dict[str, Any]]:
        out = self.strip.flush()
        events: list[dict[str, Any]] = []
        if out:
            events += self.visible(out)
        self.reasoning_chars += self.strip.hidden_chars
        return events

    def error_event(self, kind: str, message: str, **extra: Any) -> dict[str, Any]:
        event = {
            "type": "error",
            "kind": kind,
            "message": message,
            "profile": self.ep.profile,
            "model": self.model or self.ep.model,
            "endpoint": self.ep.netloc,
            "partial_text": "".join(self.pieces),
            "ttft_ms": self.ms(self.t_first),
            "total_ms": self.ms(time.perf_counter()),
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
        }
        event.update(extra)
        return event

    def done_event(self) -> dict[str, Any]:
        t_end = time.perf_counter()
        usage = self.usage or {}
        details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        output_tokens = usage.get("completion_tokens")
        estimated = output_tokens is None
        if estimated:
            output_tokens = self.content_chunks
        reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, Mapping) else None
        answer_tokens = output_tokens - (reasoning_tokens or 0) if output_tokens is not None else None
        tokens_per_s = None
        gen_ms = None
        if self.t_first is not None and self.t_last is not None:
            gen_s = self.t_last - self.t_first
            gen_ms = round(gen_s * 1000, 1)
            if answer_tokens and answer_tokens > 1 and gen_s > 0:
                tokens_per_s = round((answer_tokens - 1) / gen_s, 1)
        return {
            "type": "done",
            "ttft_ms": self.ms(self.t_first),
            "total_ms": self.ms(t_end),
            "output_tokens": output_tokens,
            "input_tokens": usage.get("prompt_tokens"),
            "tokens_per_s": tokens_per_s,
            "model": self.model or self.ep.model,
            "provider": self.provider or ("OpenRouter" if self.ep.is_openrouter else "self-hosted"),
            "finish_reason": self.finish_reason,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "profile": self.ep.profile,
            "label": self.ep.label,
            "endpoint": self.ep.netloc,
            "text": "".join(self.pieces),
            "gen_ms": gen_ms,
            "reasoning_tokens": reasoning_tokens,
            "reasoning_chars": self.reasoning_chars,
            "cached_tokens": prompt_details.get("cached_tokens") if isinstance(prompt_details, Mapping) else None,
            "cost_usd": usage.get("cost"),
            "usage_estimated": estimated,
        }


async def stream_answer(
    profile: str | Endpoint | Mapping[str, Any],
    messages: list[Mapping[str, Any]],
    *,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float = 60.0,
    connect_timeout: float = 10.0,
    session: aiohttp.ClientSession | None = None,
    extra_body: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one answer: token events, then a done event (or an error event).

    ``timeout`` bounds the whole request in seconds.  Pass a shared
    ``session`` to reuse connections (saves the TLS handshake per question).
    Closing the generator early closes the connection, which makes vLLM and
    OpenRouter stop generating.
    """
    try:
        ep = resolve_endpoint(profile, env)
    except LLMConfigError as exc:
        yield {"type": "error", "kind": "config", "message": str(exc), "profile": str(profile)}
        return
    if ep.is_openrouter and not ep.api_key:
        yield {
            "type": "error",
            "kind": "config",
            "message": "OpenRouter needs OPENROUTER_API_KEY (or HPIS_LLM_<P>_API_KEY)",
            "profile": ep.profile,
        }
        return
    payload = build_payload(ep, messages, max_tokens=max_tokens, temperature=temperature, extra_body=extra_body)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Accept-Encoding": "identity",  # compressed SSE can be buffered by proxies
    }
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"
    own = session is None
    if own:
        host = urlsplit(ep.base_url).hostname or ""
        session = aiohttp.ClientSession(trust_env=not _is_local_host(host))
    client_timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=min(connect_timeout, timeout))
    t0 = time.perf_counter()
    run = _Run(ep, len(body), t0)
    try:
        async with session.post(
            ep.chat_url, data=body, headers=headers, timeout=client_timeout, allow_redirects=False
        ) as resp:
            if resp.status != 200:
                raw = await resp.content.read(_MAX_ERROR_BODY)
                run.response_bytes += len(raw)
                ok, obj = _try_json(raw.decode("utf-8", errors="replace"))
                message = _error_message(obj if ok else None, raw.decode("utf-8", errors="replace")[:300])
                yield run.error_event("http", f"HTTP {resp.status}: {message}", status=resp.status)
                return
            if resp.content_type == "application/json":
                # The server ignored stream=true and answered in one piece.
                raw = await resp.read()
                run.response_bytes += len(raw)
                ok, obj = _try_json(raw.decode("utf-8", errors="replace"))
                if not ok:
                    yield run.error_event("protocol", "unreadable JSON response")
                    return
                for event in run.handle(SSEEvent("message", "", obj)):
                    yield event
            else:
                parser = SSEParser()
                async for chunk in resp.content.iter_any():
                    run.response_bytes += len(chunk)
                    for ev in parser.feed(chunk):
                        for event in run.handle(ev):
                            yield event
                        if run.error or run.saw_done:
                            break
                    if run.error or run.saw_done:
                        break
                if not (run.error or run.saw_done):
                    for ev in parser.close():
                        for event in run.handle(ev):
                            yield event
    except asyncio.TimeoutError as exc:
        for event in run.finish():
            yield event
        stage = "connecting" if run.response_bytes == 0 else "streaming"
        elapsed = time.perf_counter() - t0
        yield run.error_event(
            "timeout", f"timed out after {elapsed:.1f} s while {stage} ({type(exc).__name__}, limit {timeout:g} s)"
        )
        return
    except aiohttp.ClientError as exc:
        for event in run.finish():
            yield event
        yield run.error_event("connection", f"{type(exc).__name__}: {exc}")
        return
    finally:
        if own:
            await session.close()
    for event in run.finish():
        yield event
    if run.error:
        yield run.error_event(**run.error)
        return
    if not run.pieces:
        detail = f"finish_reason={run.finish_reason}" if run.finish_reason else "stream ended"
        if run.reasoning_chars:
            detail += f", {run.reasoning_chars} reasoning chars and no answer"
        yield run.error_event("empty", f"model returned no answer text ({detail})", finish_reason=run.finish_reason)
        return
    yield run.done_event()


async def complete(
    profile: str | Endpoint | Mapping[str, Any],
    messages: list[Mapping[str, Any]],
    *,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float = 60.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Collect a whole answer; returns the done event (answer in ``text``).

    Raises LLMError with the error event attached.
    """
    stream = stream_answer(profile, messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout, **kwargs)
    async with contextlib.aclosing(stream):
        async for event in stream:
            if event["type"] == "error":
                raise LLMError(event)
            if event["type"] == "done":
                return event
    raise LLMError({"type": "error", "kind": "empty", "message": "stream ended without a result"})


async def probe(
    profile: str | Endpoint | Mapping[str, Any],
    *,
    timeout: float = 5.0,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """GET /models: is the endpoint up and does it serve the configured model?"""
    try:
        ep = resolve_endpoint(profile, env)
    except LLMConfigError as exc:
        return {"ok": False, "profile": str(profile), "message": str(exc)}
    headers = {"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {}
    host = urlsplit(ep.base_url).hostname or ""
    t0 = time.perf_counter()
    try:
        async with (
            aiohttp.ClientSession(trust_env=not _is_local_host(host)) as session,
            session.get(ep.url("models"), headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            raw = await resp.read()
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
        return {**ep.describe(), "ok": False, "message": f"{type(exc).__name__}: {exc}"}
    latency = round((time.perf_counter() - t0) * 1000, 1)
    ok, obj = _try_json(raw.decode("utf-8", errors="replace"))
    if resp.status != 200 or not ok:
        return {**ep.describe(), "ok": False, "latency_ms": latency, "message": f"HTTP {resp.status}"}
    rows = obj.get("data") if isinstance(obj, Mapping) else None
    rows = rows if isinstance(rows, list) else []
    ids = [str(r.get("id")) for r in rows if isinstance(r, Mapping)]
    entry = next((r for r in rows if isinstance(r, Mapping) and r.get("id") == ep.model), None)
    return {
        **ep.describe(),
        "ok": entry is not None,
        "latency_ms": latency,
        "served": ids if len(ids) <= 20 else ids[:20] + ["…"],
        "max_model_len": entry.get("max_model_len") if entry else None,
        "message": None if entry is not None else f"{ep.model} is not listed by {ep.netloc}",
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo.llm", description="Stream one answer to stdout.")
    parser.add_argument("prompt", nargs="?", default="用一句话介绍你自己。")
    parser.add_argument("-p", "--profile", default="qwen", choices=sorted(PROFILES))
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--probe", action="store_true", help="only call GET /models")
    args = parser.parse_args(argv)

    async def run() -> int:
        if args.probe:
            print(json.dumps(await probe(args.profile), ensure_ascii=False, indent=2))
            return 0
        messages = [{"role": "user", "content": args.prompt}]
        async for event in stream_answer(args.profile, messages, max_tokens=args.max_tokens):
            if event["type"] == "token":
                print(event["text"], end="", flush=True)
            else:
                print()
                meta = {k: v for k, v in event.items() if k not in ("text", "partial_text")}
                print(json.dumps(meta, ensure_ascii=False), file=sys.stderr)
                return 0 if event["type"] == "done" else 1
        return 1

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(_main())
