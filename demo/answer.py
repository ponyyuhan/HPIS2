"""Model answer stream with deadlines and a recorded-replay safety net for live presentations.

Every successful generation is recorded (token texts + timing).  If the model
endpoint is unreachable during a presentation (or HPIS_DEMO_OFFLINE=1), the
same question/evidence/model triple is replayed with its recorded timing and
explicitly labelled as a replay.  With nothing recorded, a plain extractive
listing of the evidence is returned and labelled as such.

Never hang: the model must show its first token within ``config.FIRST_TOKEN_S``
(25 s) and finish within ``config.ANSWER_LIMIT_S`` (60 s).  On a timeout the
answer ends with what was generated, or with the extractive listing if nothing
was, and the done event carries ``timed_out: true``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time

from . import config

REPLAY_DIR = config.RUNTIME / "answers"


def _key(question: str, model: str, evidence: list[dict], *, kind: str = "mail", note: str | None = None) -> str:
    ids = ",".join(str(e["id"]) for e in evidence)
    raw = f"{model}|{' '.join(question.split())}|{ids}"
    if kind != "mail":
        raw += f"|{kind}"
    if note:
        raw += f"|{note}"
    if any(e.get("image") for e in evidence):
        raw += "|images"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _load(key: str):
    path = REPLAY_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except ValueError:
            return None
    return None


def _save(key: str, record: dict):
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REPLAY_DIR / f".{key}.tmp"
    tmp.write_text(json.dumps(record, ensure_ascii=False), "utf-8")
    os.replace(tmp, REPLAY_DIR / f"{key}.json")


async def _replay(record: dict, speed: float = 1.0):
    start = time.perf_counter()
    for offset, text in record["tokens"]:
        delay = start + offset / 1000 / speed - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        yield {"type": "token", "text": text}
    done = dict(record["done"])
    done["replay"] = True
    yield {"type": "done", **done}


def _extractive(question: str, evidence: list[dict], *, kind: str = "mail", reason: str | None = None):
    lines = [reason or "（模型服务不可用，以下为检索到的证据摘录）"]
    if not evidence:
        lines.append("本次检索没有找到相关记录。")
    for item in evidence:
        n = item.get("n", "")
        if kind == "mail":
            sender = str(item.get("from") or "").split("<")[0].strip()
            lines.append(f"- {item.get('subject', '')}（{str(item.get('date', ''))[:10]}，{sender}）[{n}]")
        elif kind == "photo":
            where = item.get("place") or item.get("title") or ""
            what = item.get("caption") or item.get("body") or ""
            lines.append(f"- {str(item.get('date', ''))[:16]} {where}：{what[:60]}[{n}]")
        else:
            body = " ".join(str(item.get("body") or "").split())
            lines.append(f"- {str(item.get('date', ''))[:10]} {item.get('title', '')}：{body[:60]}[{n}]")
    return "\n".join(lines)


_SESSIONS: dict[int, object] = {}


def _session():
    """One keep-alive HTTP session per event loop (saves a TLS handshake per answer)."""
    import aiohttp

    loop = asyncio.get_running_loop()
    s = _SESSIONS.get(id(loop))
    if s is None or s.closed:
        s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        _SESSIONS[id(loop)] = s
    return s


async def close_sessions():
    """Close this event loop's keep-alive session (tests, shutdown)."""
    s = _SESSIONS.pop(id(asyncio.get_running_loop()), None)
    if s is not None and not s.closed:
        await s.close()


def _extra_body(model: str):
    """Provider routing for OpenRouter is configured in demo.llm (throughput-first,
    no fp4 hosts), which also keeps concurrent lanes on comparable hosts."""
    return None


async def generate(
    question: str,
    evidence: list[dict],
    model: str,
    *,
    max_tokens: int = 700,
    kind: str = "mail",
    note: str | None = None,
    first_token_s: float | None = None,
    answer_s: float | None = None,
):
    """Yield {'type':'token'} events and finally one {'type':'done'} event."""
    key = _key(question, model, evidence, kind=kind, note=note)
    offline = os.environ.get("HPIS_DEMO_OFFLINE") == "1"
    record = _load(key)
    if offline and record:
        async for event in _replay(record):
            yield event
        return
    first_limit = first_token_s if first_token_s is not None else config.FIRST_TOKEN_S
    total_limit = answer_s if answer_s is not None else config.ANSWER_LIMIT_S
    tokens, start, failure, timed_out = [], time.perf_counter(), None, False
    try:
        if offline:
            raise RuntimeError("offline mode")
        from .llm import stream_answer
        from .prompts import build_messages

        # The model must resolve "上个月 / 下周 / 最近" against the data's "today".
        weekday = "一二三四五六日"[config.AS_OF.weekday()]
        dated = f"（今天是{config.AS_OF:%Y年%m月%d日}，星期{weekday}。）{question}"
        messages = build_messages(dated, evidence, kind=kind, note=note)
        stream = stream_answer(
            model,
            messages,
            max_tokens=max_tokens,
            session=_session(),
            extra_body=_extra_body(model),
            timeout=total_limit + 5,
        )
        async with contextlib.aclosing(stream):
            events = stream.__aiter__()
            while True:
                limit = (first_limit if not tokens else total_limit) - (time.perf_counter() - start)
                if limit <= 0:
                    timed_out = True
                    break
                try:
                    # No yield inside the timeout: the wait covers the model only, never the reader.
                    event = await asyncio.wait_for(events.__anext__(), limit)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                kind_ = event.get("type")
                if kind_ == "token":
                    tokens.append([round((time.perf_counter() - start) * 1000, 1), event["text"]])
                    yield event
                elif kind_ == "done":
                    if tokens:
                        _save(key, {"tokens": tokens, "done": {k: v for k, v in event.items() if k != "type"}})
                    yield {**event, "timed_out": False}
                    return
                elif kind_ == "error":
                    failure = event.get("message") or event.get("error") or "model error"
                    break
    except Exception as exc:  # network, import or provider failure
        failure = f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - start) * 1000
    if timed_out:
        if tokens:
            failure = f"模型 {total_limit:g} 秒内没有答完，已截断"
            yield {"type": "done", "partial": True, "timed_out": True, "error": failure, "total_ms": elapsed}
            return
        failure = f"模型 {first_limit:g} 秒内没有开始回答"
        text = _extractive(question, evidence, kind=kind, reason=f"（{failure}，以下为检索到的证据摘录）")
        yield {"type": "token", "text": text}
        yield {"type": "done", "fallback": True, "timed_out": True, "error": failure, "total_ms": elapsed}
        return
    if tokens:
        # Partial stream: finish honestly.
        yield {"type": "done", "partial": True, "timed_out": False, "error": failure, "total_ms": elapsed}
        return
    if record:
        async for event in _replay(record):
            yield event
        return
    text = _extractive(question, evidence, kind=kind)
    yield {"type": "token", "text": text}
    yield {"type": "done", "fallback": True, "timed_out": False, "error": failure, "total_ms": elapsed}
