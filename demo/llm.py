"""Streaming chat client for the demo's answer models (aiohttp, no OpenAI SDK).

Two profiles, ``qwen`` and ``gemma``, talk to one of:

* OpenRouter by default (key from ``OPENROUTER_API_KEY``), for testing without
  the GPU server;
* vLLM on the H200 (``demo/deploy/h200``) at 127.0.0.1:18081 (Qwen) and
  127.0.0.1:18082 (Gemma), through ``/v1/chat/completions``;
* Ollama (``HPIS_LLM_BACKEND=ollama``), through its native ``/api/chat``, the
  one request format that switches thinking off on every Ollama since 0.9.

Environment, with ``P`` = ``QWEN`` or ``GEMMA``:

  HPIS_LLM_BACKEND       ``openrouter`` (default), ``h200`` (vLLM) or ``ollama``:
                         endpoint used for every profile that has no explicit BASE_URL
  HPIS_LLM_OLLAMA_URL    Ollama server [http://127.0.0.1:11434]
  HPIS_LLM_OLLAMA_KEEP_ALIVE   how long Ollama keeps a model loaded after a request [30m]
  HPIS_LLM_OLLAMA_NUM_CTX      context length to request; only for an Ollama the demo
                               owns, since a different value reloads a shared model
  HPIS_LLM_P_BASE_URL    e.g. http://127.0.0.1:18081/v1
  HPIS_LLM_P_MODEL       model id (OpenRouter) or vLLM ``--served-model-name``
  HPIS_LLM_P_API_KEY     bearer token; OpenRouter falls back to OPENROUTER_API_KEY
  HPIS_LLM_P_EXTRA_BODY  JSON object merged into each request (null drops a key)
  HPIS_LLM_P_LABEL       display name

Thinking is switched off in every request so the answer starts immediately:
OpenRouter receives ``reasoning: {"effort": "none"}`` (plus throughput-first
routing that skips fp4 hosts), vLLM receives
``chat_template_kwargs: {"enable_thinking": false}`` and Ollama ``think: false``.  A leading
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
import re
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
_OLLAMA_NO_THINK = {"think": False}
OLLAMA_URL = "http://127.0.0.1:11434"

PROFILES: dict[str, dict[str, Any]] = {
    "qwen": {
        "label": "Qwen3.6-27B",
        "openrouter_model": "qwen/qwen3.6-27b",
        "h200_model": "hpis-qwen-27b",
        "h200_port": 18081,
        "ollama_model": "qwen3.6:27b",
        "openrouter_body": _OPENROUTER_BODY,
        "h200_body": _VLLM_NO_THINK,
        "ollama_body": _OLLAMA_NO_THINK,
    },
    "gemma": {
        "label": "Gemma 4 31B",
        "openrouter_model": "google/gemma-4-31b-it",
        "h200_model": "hpis-gemma-4",
        "h200_port": 18082,
        "ollama_model": "gemma4:31b",
        "openrouter_body": _OPENROUTER_BODY,
        "h200_body": _VLLM_NO_THINK,
        "ollama_body": _OLLAMA_NO_THINK,
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
    kind: str = ""  # "openrouter", "h200" (vLLM) or "ollama"

    @property
    def is_openrouter(self) -> bool:
        host = urlsplit(self.base_url).hostname or ""
        return host == "openrouter.ai" or host.endswith(".openrouter.ai")

    @property
    def is_ollama(self) -> bool:
        return self.kind == "ollama"

    def native_url(self, path: str) -> str:
        """Ollama's own API (``/api/...``) on the same server."""
        parts = urlsplit(self.base_url)
        root = parts.path.rstrip("/").removesuffix("/v1")
        return urlunsplit((parts.scheme, parts.netloc, f"{root}/{path.lstrip('/')}", "", ""))

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
            "backend": "openrouter" if self.is_openrouter else ("ollama" if self.is_ollama else "self-hosted"),
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


def _ollama_settings(env: Mapping[str, str]) -> dict[str, Any]:
    keep: Any = env.get("HPIS_LLM_OLLAMA_KEEP_ALIVE", "").strip() or "30m"
    if re.fullmatch(r"-?\d+", keep):
        keep = int(keep)  # seconds; negative keeps the model loaded indefinitely
    out: dict[str, Any] = {"keep_alive": keep}
    ctx = env.get("HPIS_LLM_OLLAMA_NUM_CTX", "").strip()
    if ctx:
        if not ctx.isdigit():
            raise LLMConfigError("HPIS_LLM_OLLAMA_NUM_CTX must be a whole number of tokens")
        out["options"] = {"num_ctx": int(ctx)}
    return out


def _ollama_label(model: str) -> str:
    """qwen3.6:27b -> Qwen3.6-27B, gemma4:31b -> Gemma 4 31B; anything else as is."""
    last = model.rsplit("/", 1)[-1]
    base, _, tag = last.partition(":")
    family = re.fullmatch(r"(qwen|gemma)[-_]?(\d+(?:\.\d+)?)", base.lower())
    if not family:
        return model
    size = re.match(r"(\d+(?:\.\d+)?)b(?![a-z])", tag.lower())
    if family.group(1) == "qwen":
        return f"Qwen{family.group(2)}" + (f"-{size.group(1)}B" if size else "")
    return f"Gemma {family.group(2)}" + (f" {size.group(1)}B" if size else "")


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
        backend = str(spec.get("backend", "")).strip().lower()
    else:
        name = str(profile).strip().lower()
        if name not in PROFILES:
            raise LLMConfigError(f"unknown model profile {profile!r}; use one of {sorted(PROFILES)}")
        defaults = PROFILES[name]
        prefix = f"HPIS_LLM_{name.upper()}_"
        backend = env.get("HPIS_LLM_BACKEND", "openrouter").strip().lower() or "openrouter"
        if backend not in ("openrouter", "h200", "local", "vllm", "ollama"):
            raise LLMConfigError(f"HPIS_LLM_BACKEND must be openrouter, h200 or ollama, not {backend!r}")
        base = env.get(prefix + "BASE_URL", "").strip()
        if not base and backend == "ollama":
            base = env.get("HPIS_LLM_OLLAMA_URL", "").strip() or OLLAMA_URL
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
    kind = "openrouter" if on_openrouter else ("ollama" if backend == "ollama" else "h200")
    model = spec.get("model") or defaults.get(f"{kind}_model")
    if not model:
        raise LLMConfigError(f"no model configured for profile {name!r}")
    api_key = spec.get("api_key") or None
    if not api_key and on_openrouter:
        api_key = env.get("OPENROUTER_API_KEY", "").strip() or None
    body = _merge(defaults.get(f"{kind}_body", {}), spec.get("body") or spec.get("extra_body") or {})
    if kind == "ollama":
        body = _merge(_ollama_settings(env), body)
        label = spec.get("label") or KNOWN_LABELS.get(model) or _ollama_label(model)
    else:
        label = spec.get("label") or KNOWN_LABELS.get(model) or defaults.get("label") or model
    return Endpoint(name, spec["base_url"], model, api_key, body, label, kind)


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


def build_ollama_payload(
    endpoint: Endpoint,
    messages: list[Mapping[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    stream: bool = True,
    extra_body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Body for Ollama's native POST /api/chat."""
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": list(messages),
        "stream": stream,
        "options": {"temperature": float(temperature), "num_predict": int(max_tokens)},
    }
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
    if ep.is_ollama:
        stream = _stream_ollama(
            ep,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            connect_timeout=connect_timeout,
            session=session,
            extra_body=extra_body,
        )
        async with contextlib.aclosing(stream):
            async for event in stream:
                yield event
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


def _ollama_line(run: _Run, line: bytes) -> list[dict[str, Any]]:
    """One NDJSON object from /api/chat, fed to _Run as an OpenAI-style chunk."""
    ok, obj = _try_json(line.decode("utf-8", errors="replace").strip() or "null")
    if not ok or not isinstance(obj, Mapping):
        return []
    if obj.get("error"):
        return run.handle(SSEEvent("message", "", {"error": {"message": str(obj["error"])}}))
    message = obj.get("message") if isinstance(obj.get("message"), Mapping) else {}
    done = bool(obj.get("done"))
    chunk: dict[str, Any] = {
        "model": obj.get("model"),
        "choices": [
            {
                "delta": {"content": message.get("content") or "", "reasoning": message.get("thinking") or ""},
                "finish_reason": (obj.get("done_reason") or "stop") if done else None,
            }
        ],
    }
    if done:
        chunk["usage"] = {"prompt_tokens": obj.get("prompt_eval_count"), "completion_tokens": obj.get("eval_count")}
        run.saw_done = True
    return run.handle(SSEEvent("message", "", chunk))


async def _stream_ollama(
    ep: Endpoint,
    messages: list[Mapping[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
    connect_timeout: float,
    session: aiohttp.ClientSession | None,
    extra_body: Mapping[str, Any] | None,
) -> AsyncIterator[dict[str, Any]]:
    """stream_answer for Ollama's native /api/chat (NDJSON, ``think: false``)."""
    payload = build_ollama_payload(ep, messages, max_tokens=max_tokens, temperature=temperature, extra_body=extra_body)
    headers = {"Content-Type": "application/json", "Accept": "application/x-ndjson", "Accept-Encoding": "identity"}
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"
    own = session is None
    if own:
        session = aiohttp.ClientSession(trust_env=not _is_local_host(urlsplit(ep.base_url).hostname or ""))
    client_timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=min(connect_timeout, timeout))
    t0 = time.perf_counter()
    run = _Run(ep, 0, t0)
    try:
        for attempt in range(2):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            run = _Run(ep, len(body), t0)
            run.provider = "Ollama"
            async with session.post(
                ep.native_url("api/chat"), data=body, headers=headers, timeout=client_timeout, allow_redirects=False
            ) as resp:
                if resp.status != 200:
                    raw = await resp.content.read(_MAX_ERROR_BODY)
                    run.response_bytes += len(raw)
                    text = raw.decode("utf-8", errors="replace")
                    ok, obj = _try_json(text)
                    message = _error_message(obj if ok else None, text[:300])
                    if attempt == 0 and resp.status == 400 and "think" in payload and "thinking" in message.lower():
                        # Some Ollama releases refuse ``think`` for models without a thinking mode.
                        payload = {k: v for k, v in payload.items() if k != "think"}
                        continue
                    yield run.error_event("http", f"HTTP {resp.status}: {message}", status=resp.status)
                    return
                pending = b""
                async for piece in resp.content.iter_any():
                    run.response_bytes += len(piece)
                    *lines, pending = (pending + piece).split(b"\n")
                    for line in lines:
                        for event in _ollama_line(run, line):
                            yield event
                        if run.error or run.saw_done:
                            break
                    if run.error or run.saw_done:
                        break
                if not (run.error or run.saw_done) and pending.strip():
                    for event in _ollama_line(run, pending):
                        yield event
            break
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
    url = ep.native_url("api/tags") if ep.is_ollama else ep.url("models")
    t0 = time.perf_counter()
    try:
        async with (
            aiohttp.ClientSession(trust_env=not _is_local_host(host)) as session,
            session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            raw = await resp.read()
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
        return {**ep.describe(), "ok": False, "message": f"{type(exc).__name__}: {exc}"}
    latency = round((time.perf_counter() - t0) * 1000, 1)
    ok, obj = _try_json(raw.decode("utf-8", errors="replace"))
    if resp.status != 200 or not ok:
        return {**ep.describe(), "ok": False, "latency_ms": latency, "message": f"HTTP {resp.status}"}
    rows = obj.get("models" if ep.is_ollama else "data") if isinstance(obj, Mapping) else None
    rows = rows if isinstance(rows, list) else []
    key = "name" if ep.is_ollama else "id"
    ids = [str(r.get(key)) for r in rows if isinstance(r, Mapping)]
    entry = next(
        (r for r in rows if isinstance(r, Mapping) and _same_model(str(r.get(key)), ep.model, ep.is_ollama)), None
    )
    return {
        **ep.describe(),
        "ok": entry is not None,
        "latency_ms": latency,
        "served": ids if len(ids) <= 20 else ids[:20] + ["…"],
        "max_model_len": entry.get("max_model_len") if entry else None,
        "message": None if entry is not None else f"{ep.model} is not listed by {ep.netloc}",
    }


def _same_model(listed: str, wanted: str, ollama: bool) -> bool:
    if not ollama:
        return listed == wanted

    def norm(name: str) -> str:
        return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"

    return norm(listed) == norm(wanted)


async def warm(
    profile: str | Endpoint | Mapping[str, Any],
    *,
    keep_alive: Any = None,
    timeout: float = 300.0,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load an Ollama model, or keep it loaded, without generating anything.

    Ollama unloads a model once it has been idle for keep_alive (5 minutes by
    default), and the next question would then wait for the load.  Other
    backends keep their model loaded; for them this is a no-op.
    """
    try:
        ep = resolve_endpoint(profile, env)
    except LLMConfigError as exc:
        return {"ok": False, "profile": str(profile), "message": str(exc)}
    if not ep.is_ollama:
        return {"ok": True, "profile": ep.profile, "skipped": True}
    body: dict[str, Any] = {"model": ep.model, "messages": [], "stream": False}
    body["keep_alive"] = ep.body.get("keep_alive", "30m") if keep_alive is None else keep_alive
    options = ep.body.get("options")
    if isinstance(options, Mapping) and options.get("num_ctx"):
        body["options"] = {"num_ctx": options["num_ctx"]}  # same as the chats, or Ollama reloads
    headers = {"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {}
    host = urlsplit(ep.base_url).hostname or ""
    t0 = time.perf_counter()
    try:
        async with (
            aiohttp.ClientSession(trust_env=not _is_local_host(host)) as session,
            session.post(
                ep.native_url("api/chat"), json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp,
        ):
            raw = await resp.read()
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
        return {"ok": False, "profile": ep.profile, "model": ep.model, "message": f"{type(exc).__name__}: {exc}"}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if resp.status != 200:
        text = raw.decode("utf-8", errors="replace")
        ok, obj = _try_json(text)
        message = _error_message(obj if ok else None, text[:300])
        return {"ok": False, "profile": ep.profile, "model": ep.model, "ms": ms, "message": f"HTTP {resp.status}: {message}"}
    return {"ok": True, "profile": ep.profile, "model": ep.model, "ms": ms}


async def warm_all(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    return [await warm(name, env=env) for name in PROFILES]


# ------------------------------------------------------------------- check

_CHECK_SYSTEM = "你是个人邮件问答助手。只依据给出的编号证据回答，每个事实后标注来源编号如[1]。不写推理过程，回答不超过60字。"
_CHECK_NEEDLE = (
    "[1] 日期：2026-11-17 18:05\n主题：天枢项目交付时间调整\n正文：\n"
    "受 HUKS 接口联调影响，天枢项目 SDK 2.0 的交付时间由 12 月 8 日推迟到 12 月 15 日。"
)
_CHECK_FILLER = "本周完成了接口文档评审和联调环境部署，测试用例编写进度符合预期，下周继续推进性能测试和问题单闭环。"
_CHECK_PARALLEL = [{"role": "user", "content": "请用大约200字介绍杭州西湖的历史和主要景点。"}]
_CHECK_KEEP_ALIVE = "5m"  # the check must not pin models on a server the demo may end up not using
_CHECK_LONG_TOKENS = 5800  # about the largest prompt the demo sends (prompts.MAX_CONTEXT_TOKENS)


def _check_messages(long: bool = False) -> list[dict[str, str]]:
    """The answer is in evidence [1], so a server that cuts a long prompt from the front fails."""
    from .prompts import estimate_tokens

    parts = [_CHECK_NEEDLE]
    while long and estimate_tokens("\n\n".join(parts)) < _CHECK_LONG_TOKENS:
        n = len(parts) + 1
        parts.append(f"[{n}] 日期：2026-10-{n % 28 + 1:02d} 09:30\n主题：例会纪要（第{n}期）\n正文：\n" + _CHECK_FILLER * 6)
    user = "证据：\n\n" + "\n\n".join(parts) + "\n\n问题：天枢项目 SDK 2.0 最后定的交付日期是哪天？"
    return [{"role": "system", "content": _CHECK_SYSTEM}, {"role": "user", "content": user}]


def _answer_ok(text: str) -> bool:
    from .prompts import parse_citations

    flat = "".join((text or "").split())
    return any(s in flat for s in ("12月15", "12-15", "12/15")) and 1 in parse_citations(text)


async def _timed(
    ep: Endpoint,
    messages: list[Mapping[str, Any]],
    max_tokens: int,
    *,
    after: asyncio.Event | None = None,
    started: asyncio.Event | None = None,
):
    """(sent, first token, end, final event) on the perf_counter clock.

    ``after``: send only once this event is set; ``started``: set at the first token.
    """
    if after is not None:
        await after.wait()
    sent, first = time.perf_counter(), None
    extra = {"keep_alive": _CHECK_KEEP_ALIVE} if ep.is_ollama else None
    stream = stream_answer(ep, messages, max_tokens=max_tokens, timeout=300.0, extra_body=extra)
    async with contextlib.aclosing(stream):
        async for event in stream:
            if event["type"] == "token" and first is None:
                first = time.perf_counter()
                if started is not None:
                    started.set()
            elif event["type"] in ("done", "error"):
                if started is not None:
                    started.set()  # never leave the second request waiting
                return sent, first, time.perf_counter(), event
    return sent, first, time.perf_counter(), {"type": "error", "message": "stream ended without a result"}


async def _ollama_version(ep: Endpoint) -> str | None:
    host = urlsplit(ep.base_url).hostname or ""
    try:
        async with (
            aiohttp.ClientSession(trust_env=not _is_local_host(host)) as session,
            session.get(ep.native_url("api/version"), timeout=aiohttp.ClientTimeout(total=5)) as resp,
        ):
            obj = await resp.json(content_type=None)
        return str(obj.get("version")) if isinstance(obj, Mapping) and obj.get("version") else None
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError):
        return None


def _short(text: str, n: int = 40) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


async def check(profiles: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """What the live demo needs from each model server; prints a report, returns an exit code.

    1. a correct, cited answer with thinking off;
    2. no truncation of a prompt as long as the demo's largest;
    3. two requests answered at the same time: both lanes ask the same model at
       once, and if the second waited for the first, the plaintext lane's first
       token would be late for a reason that has nothing to do with HPIS.
    """
    failures: set[str] = set()
    for name in profiles or list(PROFILES):
        try:
            ep = resolve_endpoint(name, env)
        except LLMConfigError as exc:
            print(f"[{name}] 配置错误：{exc}")
            failures.add("config")
            continue
        server = "Ollama" if ep.is_ollama else ("OpenRouter" if ep.is_openrouter else "vLLM")
        version = await _ollama_version(ep) if ep.is_ollama else None
        print(f"[{name}] {ep.model} · {server}{' ' + version if version else ''} @ {ep.netloc} · 页面显示为 {ep.label}")
        if ep.is_ollama:
            loaded = await warm(ep, keep_alive=_CHECK_KEEP_ALIVE)
            if not loaded["ok"]:
                print(f"  加载     失败：{loaded['message']}")
                failures.add("load")
                continue
            print(f"  加载     {loaded['ms'] / 1000:.1f} s")

        _, _, _, event = await _timed(ep, _check_messages(), 160)
        if event["type"] != "done":
            print(f"  回答     失败：{event.get('message')}")
            failures.add("answer")
            continue
        speed = f" · {event['tokens_per_s']:.0f} tok/s" if event.get("tokens_per_s") else ""
        verdict = "通过" if _answer_ok(event["text"]) else "不通过"
        print(f"  回答     {verdict} · 首字 {event['ttft_ms'] / 1000:.2f} s{speed} · 「{_short(event['text'])}」")
        if verdict != "通过":
            failures.add("answer")
        if event.get("reasoning_chars"):
            print(f"  思考     未关闭：先输出了 {event['reasoning_chars']} 字思考内容，首字会变慢")
            failures.add("thinking")
        else:
            print("  思考     已关闭")

        _, _, _, event = await _timed(ep, _check_messages(long=True), 160)
        if event["type"] == "done" and _answer_ok(event["text"]):
            print(f"  长提示词 通过（约 {_CHECK_LONG_TOKENS} token，首字 {event['ttft_ms'] / 1000:.2f} s）")
        else:
            detail = event.get("message") if event["type"] != "done" else f"「{_short(event['text'])}」"
            print(f"  长提示词 不通过：开头的证据丢了，上下文可能太短（{detail}）")
            failures.add("context")

        # The second request goes out as soon as the first starts answering, like the
        # plaintext lane asking while the HPIS lane's answer is still streaming.
        a_started = asyncio.Event()
        (_, a_first, a_end, a_ev), (b_sent, b_first, _, b_ev) = await asyncio.gather(
            _timed(ep, _CHECK_PARALLEL, 220, started=a_started),
            _timed(ep, _CHECK_PARALLEL, 220, after=a_started),
        )
        if a_ev["type"] != "done" or b_ev["type"] != "done" or a_first is None or b_first is None:
            bad = a_ev if a_ev["type"] != "done" else b_ev
            print(f"  并行     失败：{bad.get('message')}")
            failures.add("parallel")
        elif a_end - b_sent < 0.3:
            print("  并行     无法判断：第一个回答太短，第二个请求发出时它已经结束")
        elif b_first < a_end - 0.1:
            print(f"  并行     通过：两个请求同时生成（第二个 {b_first - b_sent:.2f} s 出首字）")
        else:
            print(f"  并行     不通过：第二个请求等第一个生成完才开始（多等了 {b_first - b_sent:.1f} s）")
            failures.add("parallel")

    if not failures:
        print("CHECK PASS")
        return 0
    print("CHECK FAIL")
    hints = {
        "parallel": "两条通道会排队：这个服务每个模型一次只处理一个请求（Ollama 默认 OLLAMA_NUM_PARALLEL=1），"
        "明文通道的首字时间会被拉长，对比就不公平了。不要改动或重启它，"
        "按 H200部署步骤.md 第 4 步用 --ollama 为演示单独起一个 Ollama。",
        "context": "上下文太短，长邮件会被截断。--ollama 起的演示专用 Ollama 固定使用 16k 上下文。",
        "thinking": "模型没有关闭思考。Ollama 需要 0.9 或更新版本（ollama --version）。",
        "load": "模型加载失败：核对 HPIS_LLM_QWEN_MODEL / HPIS_LLM_GEMMA_MODEL 与 ollama list 的 NAME 是否一致。",
    }
    for key in ("load", "thinking", "context", "parallel"):
        if key in failures:
            print("提示：" + hints[key])
    return 1


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo.llm", description="Stream one answer to stdout.")
    parser.add_argument("prompt", nargs="?", default="用一句话介绍你自己。")
    parser.add_argument("-p", "--profile", choices=sorted(PROFILES), help="default: qwen (with --check: both)")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--probe", action="store_true", help="only call GET /models")
    parser.add_argument("--check", action="store_true", help="check what the live demo needs from the model servers")
    parser.add_argument("--warm", action="store_true", help="load the Ollama models now")
    args = parser.parse_args(argv)
    profile = args.profile or "qwen"

    async def run() -> int:
        if args.check:
            return await check([args.profile] if args.profile else None)
        if args.warm:
            results = await warm_all()
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0 if all(r.get("ok") for r in results) else 1
        if args.probe:
            print(json.dumps(await probe(profile), ensure_ascii=False, indent=2))
            return 0
        messages = [{"role": "user", "content": args.prompt}]
        async for event in stream_answer(profile, messages, max_tokens=args.max_tokens):
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
