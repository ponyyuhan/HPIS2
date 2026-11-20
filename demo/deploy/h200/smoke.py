#!/usr/bin/env python3
"""Check a running answer-model endpoint: /v1/models plus streamed chats.

Standard library only, so it runs on the GPU host as well as on the demo laptop.

    python3 smoke.py qwen                  # http://127.0.0.1:18081/v1, hpis-qwen-27b
    python3 smoke.py gemma --runs 5
    python3 smoke.py --base-url http://127.0.0.1:18081/v1 --model hpis-qwen-27b

Each run streams one grounded Chinese question and reports time to first
answer token (TTFT), decode speed and token counts.  The last run omits the
per-request ``enable_thinking: false`` to confirm the server-side default from
serve.sh.  Exit status 0 means every run answered correctly, cited evidence
[2] and produced no reasoning text.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

PROFILES = {"qwen": ("hpis-qwen-27b", 18081), "gemma": ("hpis-gemma-4", 18082)}

SYSTEM = (
    "你是个人邮件问答助手。只依据给出的编号证据回答，每个事实后标注来源编号如[1]。"
    "同一事项有变化时以日期最新的邮件为准，并写明原定和调整后的情况。不写推理过程，回答不超过80字。"
)
USER = (
    "证据：\n\n[1] 日期：2026-11-10 17:42\n主题：天枢项目周会纪要\n正文：\nSDK 2.0 计划于 12 月 8 日交付测试部。\n\n"
    "[2] 日期：2026-11-17 18:05\n主题：天枢项目交付时间调整\n正文：\n受 HUKS 接口联调影响，交付时间由 12 月 8 日推迟到 12 月 15 日。\n\n"
    "问题：天枢项目最后定的交付日期是哪天？"
)
EXPECTED = ("12月15日", "12-15", "12/15", "12月15号")


def opener_for(url: str) -> urllib.request.OpenerDirector:
    host = urlsplit(url).hostname or ""
    try:
        local = host == "localhost" or ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    # A tunnelled endpoint must never go through an HTTP proxy from the environment.
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()


def request(url: str, key: str | None, body: dict | None = None) -> urllib.request.Request:
    headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    return urllib.request.Request(url, data=data, headers=headers, method="GET" if body is None else "POST")


def list_models(base: str, key: str | None, timeout: float) -> list[dict]:
    with opener_for(base).open(request(base + "/models", key), timeout=timeout) as resp:
        return json.load(resp).get("data", [])


def stream_chat(base: str, key: str | None, model: str, template_kwargs: bool, timeout: float) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
        "max_tokens": 200,
        "temperature": 0.2,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if template_kwargs:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    text, reasoning, usage, finish = [], 0, {}, None
    t0 = time.perf_counter()
    t_first = t_last = None
    with opener_for(base).open(request(base + "/chat/completions", key, body), timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("error"):
                raise RuntimeError(f"stream error: {chunk['error']}")
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                reasoning += len(delta.get("reasoning_content") or delta.get("reasoning") or "")
                piece = delta.get("content") or ""
                if piece:
                    now = time.perf_counter()
                    if t_first is None and piece.strip():
                        t_first = now
                    t_last = now
                    text.append(piece)
                finish = choice.get("finish_reason") or finish
    t_end = time.perf_counter()
    answer = "".join(text).strip()
    out_tokens = usage.get("completion_tokens")
    tps = None
    if out_tokens and out_tokens > 1 and t_first and t_last and t_last > t_first:
        tps = (out_tokens - 1) / (t_last - t_first)
    compact = answer.replace(" ", "")
    return {
        "template_kwargs": template_kwargs,
        "ttft_ms": None if t_first is None else round((t_first - t0) * 1000, 1),
        "total_ms": round((t_end - t0) * 1000, 1),
        "tokens_per_s": None if tps is None else round(tps, 1),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": out_tokens,
        "reasoning_chars": reasoning + (len(answer) if "</think>" in answer else 0),
        "finish_reason": finish,
        "answer": answer,
        "ok": bool(answer)
        and any(e in compact for e in EXPECTED)
        and "[2]" in compact
        and reasoning == 0
        and "<think>" not in answer
        and "</think>" not in answer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("profile", nargs="?", choices=sorted(PROFILES))
    parser.add_argument("--base-url", help="default http://127.0.0.1:<18081|18082>/v1")
    parser.add_argument("--model", help="served model name (default from the profile)")
    parser.add_argument("--api-key", help="default: $VLLM_API_KEY")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if not args.profile and not (args.base_url and args.model):
        parser.error("give a profile (qwen/gemma) or both --base-url and --model")
    model, port = PROFILES.get(args.profile, (None, None))
    base = (args.base_url or f"http://127.0.0.1:{port}/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    model = args.model or model
    key = args.api_key or os.environ.get("VLLM_API_KEY")

    try:
        rows = list_models(base, key, timeout=15)
    except urllib.error.HTTPError as exc:
        print(f"GET {base}/models -> HTTP {exc.code} (wrong or missing API key?)")
        return 1
    except OSError as exc:
        print(f"GET {base}/models failed: {exc}\n  Is the server up and the SSH tunnel open?")
        return 1
    ids = [r.get("id") for r in rows]
    entry = next((r for r in rows if r.get("id") == model), None)
    print(f"endpoint {base}: serves {ids}")
    if entry is None:
        print(f"FAIL: {model!r} is not served here")
        return 1
    print(f"model {model}: max_model_len={entry.get('max_model_len')} root={entry.get('root')}")

    results = []
    for i in range(max(1, args.runs)):
        with_kwargs = i < max(1, args.runs) - 1 or args.runs == 1
        try:
            res = stream_chat(base, key, model, with_kwargs, args.timeout)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"run {i + 1}: FAIL {type(exc).__name__}: {exc}")
            return 1
        results.append(res)
        tag = "per-request enable_thinking=false" if with_kwargs else "server default only"
        print(
            f"run {i + 1} ({tag}): {'PASS' if res['ok'] else 'FAIL'} ttft={res['ttft_ms']} ms "
            f"total={res['total_ms']} ms decode={res['tokens_per_s']} tok/s "
            f"tokens in/out={res['input_tokens']}/{res['output_tokens']} reasoning_chars={res['reasoning_chars']} "
            f"finish={res['finish_reason']}\n    {res['answer']}"
        )
    passed = all(r["ok"] for r in results)
    print("SMOKE " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
