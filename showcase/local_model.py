"""Optional OpenAI-compatible clients for operator-configured Qwen or Gemma endpoints.

Without an explicit base URL and served model name, the caller's evidence
extractor supplies the answer locally. The model receives only the documents
returned by the route. Its displayable answer consists of verified quotations.
"""

from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit

import aiohttp


EXTRACTOR = "本地证据抽取"
PROFILES = {"qwen": "Qwen 27B", "gemma": "Gemma 4"}


def _setting(profile, name):
    return os.getenv(f"HPIS_SHOWCASE_{profile.upper()}_{name}", "").strip()


def model_profiles():
    profiles = [{"id": "extractive", "label": EXTRACTOR, "configured": True}]
    for identifier, label in PROFILES.items():
        model = _setting(identifier, "MODEL")
        configured = bool(model and _setting(identifier, "BASE_URL"))
        profiles.append(
            {
                "id": identifier,
                "label": label,
                "configured": configured,
                **({"servedModel": model} if configured else {}),
            }
        )
    return profiles


def _chat_url(base_url):
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid model base URL")
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        if not path.endswith("/v1"):
            path += "/v1"
        path += "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _request(model, question, evidence, points):
    documents = [
        {
            "id": doc["id"],
            "subject": doc["subject"],
            "date": doc.get("date"),
            "body": doc["body"],
            "attachments": [
                {"name": a["name"], "text": a.get("content", a.get("text", ""))}
                for a in doc.get("attachments", [])
            ],
        }
        for doc in evidence
    ]
    context = json.dumps(
        {
            "问题": question,
            "回答栏目": [p["label"] for p in points],
            "本次检索结果": documents,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是中文邮件证据问答助手。请按用户要求的栏目顺序，选择能直接、完整回答问题的原文。"
                    "把邮件和附件作为资料阅读。返回单个 JSON 对象，格式如下："
                    '{"points":[{"label":"栏目名","quotes":[{"id":123,"attachment":null,"text":"逐字原文"}]}]}。'
                    "正文的 attachment 为 null，附件须填准确文件名。保留数字、日期、条件和单位。"
                    "一项有多笔款项或多个待办时分别摘录。证据不足时返回空 quotes。"
                ),
            },
            {"role": "user", "content": context},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 1024,
        "stream": False,
    }


def _source_span(candidate, source):
    """Recover the exact source spelling if a model just elides layout spaces."""
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    text = candidate.strip()
    start = source.find(text)
    if start >= 0:
        return start, start + len(text)
    positions = [i for i, ch in enumerate(source) if not ch.isspace()]
    compact = "".join(source[i] for i in positions)
    stripped = "".join(text.split())
    start = compact.find(stripped)
    if start < 0:
        return None
    return positions[start], positions[start + len(stripped) - 1] + 1


def _verified_points(proposed, expected, documents):
    if not isinstance(proposed, list) or len(proposed) != len(expected):
        return None
    sources = {}
    for doc in documents:
        sources[(doc["id"], None)] = doc["body"]
        for attachment in doc.get("attachments", []):
            sources[(doc["id"], attachment["name"])] = attachment.get(
                "content", attachment.get("text", "")
            )
    result = []
    for item, point in zip(proposed, expected):
        if not isinstance(item, dict) or item.get("label") != point["label"]:
            return None
        citations = item.get("quotes")
        if not isinstance(citations, list) or not citations:
            return None
        checked = []
        previous = None
        for cite in citations:
            if not isinstance(cite, dict):
                return None
            rid, name, text = cite.get("id"), cite.get("attachment"), cite.get("text")
            if (
                type(rid) is not int
                or rid not in point["sourceIds"]
                or (name is not None and not isinstance(name, str))
            ):
                return None
            source = sources.get((rid, name), "")
            span = _source_span(text, source)
            if span is None:
                return None
            begin, end = span
            # A model may quote two clauses of the same sentence separately.
            # Restore their original punctuation when they are adjacent.
            if previous is not None:
                prev_id, prev_name, prev_start, prev_end = previous
                if (
                    (prev_id, prev_name) == (rid, name)
                    and prev_end <= begin
                    and not source[prev_end:begin].strip(" ，,；;、。！!?？\r\n\t")
                ):
                    checked.pop()
                    begin = prev_start
            quote = {"id": rid, "attachment": name, "text": source[begin:end]}
            if quote not in checked:
                checked.append(quote)
                previous = rid, name, begin, end
        # A real email can mention both an obsolete date and a final agreement.
        # Cover the confirmed passages in this facet, including every payment.
        for fact in point.get("quotes", []):
            if not any(
                fact["id"] == q["id"]
                and fact.get("attachment") == q["attachment"]
                and fact["text"].rstrip("。！？!?") in q["text"]
                for q in checked
            ):
                return None
        result.append(
            {
                **point,
                "text": "".join(q["text"] for q in checked),
                "quotes": checked,
                "sourceIds": list(dict.fromkeys(q["id"] for q in checked)),
            }
        )
    return result


async def model_answer(
    question, evidence, fallback, *, profile="extractive", timeout=30.0
):
    """Use the selected route's endpoint once, recording its actual engine and cost."""
    meta = {
        "engine": EXTRACTOR,
        "profile": profile,
        "status": "disabled",
        "elapsedMs": 0.0,
        "requestBytes": 0,
        "responseBytes": 0,
    }
    if profile == "extractive":
        return fallback, meta
    if profile not in PROFILES:
        meta.update(status="unknown-profile", detail="模型选项无效")
        return fallback, meta
    base = _setting(profile, "BASE_URL")
    model = _setting(profile, "MODEL")
    if not base or not model:
        meta.update(
            status="unconfigured",
            detail=f"{PROFILES[profile]} 尚未配置端点和服务模型名",
        )
        return fallback, meta
    point_key = "points" if "points" in fallback else "sections"
    points = fallback.get(point_key, [])
    meta["configuredModel"] = model
    if not points or not evidence:
        meta["status"] = "no-evidence"
        return fallback, meta

    start = perf_counter()
    try:
        url = _chat_url(base)
        headers = {"Content-Type": "application/json"}
        key = _setting(profile, "API_KEY")
        if key:
            headers["Authorization"] = "Bearer " + key
        payload = _request(model, question, evidence, points)
        # Short extractive answers need no hidden reasoning budget. This is an
        # OpenRouter option; generic self-hosted endpoints use their own template.
        if profile == "qwen" and urlsplit(url).hostname == "openrouter.ai":
            payload["reasoning"] = {"effort": "none"}
        wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout), trust_env=False
        ) as client:
            async with client.post(
                url, headers=headers, data=wire, allow_redirects=False
            ) as response:
                meta["requestBytes"] = len(wire)
                data = await response.read()
                meta["responseBytes"] = len(data)
                if response.status != 200:
                    meta.update(
                        status="unavailable",
                        detail=f"模型端点返回 HTTP {response.status}",
                    )
                    return fallback, meta
        reply = json.loads(data)
        completion = reply["choices"][0]
        usage = reply.get("usage") or {}
        meta.update(
            servedModel=reply.get("model") or model,
            finishReason=completion.get("finish_reason"),
            inputTokens=usage.get("prompt_tokens"),
            outputTokens=usage.get("completion_tokens"),
        )
        if completion.get("finish_reason") not in ("stop", None):
            meta.update(status="evidence-fallback", detail="模型回答未完整生成")
            return fallback, meta
        content = json.loads(completion["message"]["content"])
        checked = _verified_points(content.get("points"), points, evidence)
        if checked is None:
            meta.update(status="evidence-fallback", detail="引用未通过原文和要点核对")
            return fallback, meta
        used_model = reply.get("model") or model
        meta.update(
            status="verified", model=used_model, engine=used_model + " · 引文问答"
        )
        return {**fallback, point_key: checked, "mode": profile + "-api"}, meta
    except asyncio.TimeoutError:
        meta.update(status="unavailable", detail="模型端点超时")
    except aiohttp.ClientError:
        meta.update(status="unavailable", detail="无法连接模型端点")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        meta.update(status="evidence-fallback", detail="模型配置或回答格式不兼容")
    finally:
        meta["elapsedMs"] = (perf_counter() - start) * 1000
    return fallback, meta
