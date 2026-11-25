"""Grounded question answering over a few decrypted emails (Chinese prompt).

``build_messages(question, evidence)`` returns the chat messages for any model
profile; the prompt is identical for Qwen and Gemma.  Evidence items look like
``{"n", "id", "date", "from", "subject", "body", "attachments": [{"name",
"text"}]}``; ``n`` is the number the answer cites as ``[n]``.  Bodies and
attachments are trimmed so system prompt, evidence and question stay within
about 6k tokens (estimated conservatively, see ``estimate_tokens``).

``parse_citations`` / ``citation_report`` read the ``[n]`` markers back.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MAX_CONTEXT_TOKENS = 6000
_SAFETY_TOKENS = 150  # chat-template overhead and estimator error

SYSTEM_PROMPT = """你是个人邮件问答助手。系统已在用户设备上解密并检索出与问题相关的几封邮件，按编号作为证据提供给你。

回答规则：
1. 只依据证据回答，不使用证据以外的知识，不猜测。证据中没有的信息写“邮件中未提及”，可顺带给出证据里最相关的信息。
2. 每个事实后紧跟来源编号，如“12月15日[2]”；依据多封邮件时写[1][2]。只引用给出的编号。
3. 同一事项前后有变化（改期、金额更新、问题已解决等）时，以日期最新的邮件为准，并写明“原定…，后改为…”。
4. 需要比较或计算（如最大一笔、合计、先后）时，逐条核对证据中的原始数字后再下结论。
5. 开门见山给出结论，不寒暄，不复述问题，不写推理过程。
6. 问题涉及多个方面时，每个方面单独一行，以加粗标签开头，如“**交付时间**：…”；只问一件事时用一两句话回答。
7. 日期、金额、数量、编号照抄原文，不换算，不四舍五入。
8. 全文一般不超过200字。"""

_SPACE_RUN = re.compile(r"[ \t　]+\n")
_BLANKS = re.compile(r"\n{3,}")
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯　-〿]")


def _char_tokens(ch: str) -> float:
    if _CJK.match(ch):
        return 0.85
    if ch.isdigit():
        return 1.0  # both tokenizers split numbers into single digits
    if ch.isspace():
        return 0.3
    if ch.isascii() and ch.isalpha():
        return 0.35
    return 1.0


def estimate_tokens(text: str) -> float:
    """Slightly high token estimate for mixed Chinese/ASCII text.

    Weights fitted on the demo fixtures against the prompt_tokens that
    Qwen3.6-27B and Gemma 4 31B report: the estimate lands 2–5 % above both.
    """
    return sum(_char_tokens(ch) for ch in text)


def _clean(text: Any) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _SPACE_RUN.sub("\n", value)
    return _BLANKS.sub("\n\n", value).strip()


def _cut(text: str, budget: float) -> str:
    """Keep the head of ``text`` within ``budget`` estimated tokens."""
    if estimate_tokens(text) <= budget:
        return text
    used = 0.0
    end = 0
    for i, ch in enumerate(text):
        used += _char_tokens(ch)
        if used > budget:
            break
        end = i + 1
    head = text[:end]
    # Prefer ending on a line or sentence boundary when one is close.
    for mark in ("\n", "。", "；", "，", " "):
        pos = head.rfind(mark)
        if pos >= max(0, end - 80):
            head = head[: pos + (0 if mark == "\n" else 1)]
            break
    return f"{head.rstrip()}……（后略{len(text) - len(head)}字）"


def _fair_caps(sizes: Sequence[float], budget: float) -> list[float]:
    """Water-filling: short parts stay whole, long parts share what is left."""
    if sum(sizes) <= budget:
        return list(sizes)
    caps = [0.0] * len(sizes)
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    left = max(0.0, budget)
    for rank, i in enumerate(order):
        share = left / (len(order) - rank)
        caps[i] = min(sizes[i], share)
        left -= caps[i]
    return caps


def _header(item: Mapping[str, Any], n: int) -> str:
    lines = [f"[{n}] 日期：{_clean(item.get('date')) or '未知'}"]
    sender = _clean(item.get("from"))
    if sender:
        lines.append(f"发件人：{sender}")
    subject = _clean(item.get("subject"))
    if subject:
        lines.append(f"主题：{subject[:120]}")
    return "\n".join(lines)


def format_evidence(evidence: Sequence[Mapping[str, Any]], budget_tokens: float) -> str:
    """Render numbered evidence, trimming bodies/attachments to the budget."""
    numbered = []
    for index, item in enumerate(evidence, 1):
        n = item.get("n", index)
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = index
        numbered.append((n, item))
    headers = [_header(item, n) for n, item in numbered]
    parts: list[tuple[int, str, str]] = []  # (item index, label, text)
    for k, (_, item) in enumerate(numbered):
        body = _clean(item.get("body"))
        parts.append((k, "正文：", body or "（无正文）"))
        for att in item.get("attachments") or []:
            if isinstance(att, Mapping):
                name, text = _clean(att.get("name")) or "附件", _clean(att.get("text") or att.get("content"))
            else:
                name, text = _clean(att), ""
            parts.append((k, f"附件《{name}》：", text or "（附件无文本内容）"))
    fixed = sum(estimate_tokens(h) for h in headers) + sum(estimate_tokens(label) + 1 for _, label, _ in parts)
    caps = _fair_caps([estimate_tokens(text) for _, _, text in parts], budget_tokens - fixed)
    blocks = [[h] for h in headers]
    for (k, label, text), cap in zip(parts, caps):
        blocks[k].append(label + "\n" + _cut(text, cap))
    return "\n\n".join("\n".join(block) for block in blocks)


def build_messages(
    question: str,
    evidence: Sequence[Mapping[str, Any]],
    *,
    max_context_tokens: int = MAX_CONTEXT_TOKENS,
    kind: str = "mail",
    note: str | None = None,
) -> list[dict[str, Any]]:
    """Chat messages (system + user) for one grounded answer.

    ``kind`` picks the prompt for the device's data (mail, photo, sport, car);
    ``note`` (a relaxed search) is placed before the question.  Photo evidence
    that carries ``image`` bytes becomes an image part right after its text,
    so the user message is then a list of parts (see demo.llm for the wire forms).
    """
    if kind != "mail":
        return _build_kind_messages(question, evidence, kind, note, max_context_tokens)
    question = _clean(question)
    intro = f"证据（共{len(evidence)}封邮件，按编号引用）：" if evidence else "证据：无（本次检索没有找到相关邮件）。"
    outro = f"问题：{question}"
    extra = _clean(note) if note else ""
    overhead = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(intro) + estimate_tokens(outro) + _SAFETY_TOKENS
    overhead += estimate_tokens(extra)
    body = format_evidence(evidence, max_context_tokens - overhead) if evidence else ""
    user = "\n\n".join(part for part in (intro, body, extra, outro) if part)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


# ------------------------------------------------------------ other devices

_COMMON_RULES = """1. 只依据证据回答，不使用证据以外的知识，不猜测。证据中没有的信息写“{missing}”，可顺带给出证据里最接近的信息。
2. 每个事实后紧跟来源编号，如“{example}”；依据多条证据时写[1][2]。只引用给出的编号。
3. 开门见山给出结论，不寒暄，不复述问题，不写推理过程。
4. 问题涉及多个方面时，每个方面单独一行，以加粗标签开头，如“**{label}**：…”；只问一件事时用一两句话回答。
5. 日期、地点、数字和单位照抄证据，不换算，不四舍五入。
{extra}
全文一般不超过200字。"""

KIND_PROMPTS = {
    "photo": (
        "你是个人相册问答助手。系统已在用户设备上解密并检索出与问题相关的几张照片，按编号作为证据提供给你，"
        "每张附拍摄时间、地点和画面描述{images}。\n\n回答规则：\n"
        + _COMMON_RULES.format(
            missing="相册中未找到",
            example="玛利亚广场的街景[2]",
            label="9月16日 慕尼黑",
            extra="6. 按时间或地点归纳照片，只描述画面中确实有的内容。",
        )
    ),
    "sport": (
        "你是个人运动健康问答助手。系统已在用户设备上解密并检索出与问题相关的几条手表记录"
        "（单次运动、睡眠、日常数据和周报、月报），按编号作为证据提供给你。\n\n回答规则：\n"
        + _COMMON_RULES.format(
            missing="记录中未找到",
            example="跑步12次[1]",
            label="跑步",
            extra="6. 有周报、月报时优先引用其中的汇总数字；需要比较或合计时，逐条核对原始数字后再下结论。",
        )
    ),
    "car": (
        "你是个人用车出行问答助手。系统已在用户设备上解密并检索出与问题相关的几条车机记录"
        "（行程、充电、导航、保养、提醒和周报、月报），按编号作为证据提供给你。\n\n回答规则：\n"
        + _COMMON_RULES.format(
            missing="记录中未找到",
            example="充电3次[1]",
            label="去过的地方",
            extra="6. 有周报、月报时优先引用其中的汇总数字；需要比较或合计时，逐条核对原始数字后再下结论。",
        )
    ),
}
KIND_UNITS = {"photo": ("张照片", "照片"), "sport": ("条手表记录", "记录"), "car": ("条车机记录", "记录")}
_ITEM_TOKENS = 700  # cap per evidence item (photo captions and watch/car records are short)


def system_prompt(kind: str, *, images: bool = False) -> str:
    if kind == "mail":
        return SYSTEM_PROMPT
    text = KIND_PROMPTS[kind]
    return text.replace("{images}", "；照片本身也一并给出，顺序与编号一致" if images else "")


def _kind_block(item: Mapping[str, Any], n: int, kind: str, budget: float) -> str:
    date = _clean(item.get("date")) or "未知"
    if kind == "photo":
        lines = [f"[{n}] 拍摄时间：{date}"]
        if _clean(item.get("place")):
            lines.append(f"地点：{_clean(item.get('place'))}")
        if _clean(item.get("title")):
            lines.append(f"标题：{_clean(item.get('title'))[:80]}")
        caption = _clean(item.get("caption"))
        body = _clean(item.get("body"))
        if caption:
            lines.append("画面：" + _cut(caption, budget / 2))
        if body and body != caption:
            lines.append("说明：" + _cut(body, budget / 2))
        tags = [str(t) for t in item.get("tags") or [] if t]
        if tags:
            lines.append("标签：" + "、".join(tags[:8]))
        return "\n".join(lines)
    lines = [f"[{n}] 日期：{date}"]
    if _clean(item.get("title")):
        lines.append(f"标题：{_clean(item.get('title'))[:80]}")
    body = _clean(item.get("body"))
    if body:
        lines.append("内容：" + _cut(body, budget))
    for fact in item.get("facts") or []:
        lines.append(_clean(fact)[:400])
    return "\n".join(lines)


def _build_kind_messages(question, evidence, kind, note, max_context_tokens) -> list[dict[str, Any]]:
    if kind not in KIND_PROMPTS:
        raise ValueError(f"unknown kind {kind!r}")
    question = _clean(question)
    count, noun = KIND_UNITS[kind]
    images = [item.get("image") for item in evidence]
    has_images = kind == "photo" and any(isinstance(x, (bytes, bytearray)) and x for x in images)
    system = system_prompt(kind, images=has_images)
    intro = f"证据（共{len(evidence)}{count}，按编号引用）：" if evidence else f"证据：无（本次检索没有找到相关{noun}）。"
    outro = f"问题：{question}"
    extra = _clean(note) if note else ""
    overhead = estimate_tokens(system) + estimate_tokens(intro) + estimate_tokens(outro + extra) + _SAFETY_TOKENS
    per_item = min(_ITEM_TOKENS, (max_context_tokens - overhead) / max(1, len(evidence)))
    blocks = []
    for index, item in enumerate(evidence, 1):
        try:
            n = int(item.get("n", index))
        except (TypeError, ValueError):
            n = index
        blocks.append(_kind_block(item, n, kind, per_item))
    tail = "\n\n".join(part for part in (extra, outro) if part)
    if not has_images:
        user = "\n\n".join(part for part in (intro, "\n\n".join(blocks), tail) if part)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
    parts: list[dict[str, Any]] = [{"type": "text", "text": intro}]
    for block, image in zip(blocks, images):
        parts.append({"type": "text", "text": "\n\n" + block})
        if isinstance(image, (bytes, bytearray)) and image:
            parts.append({"type": "image", "data": bytes(image), "mime": "image/jpeg"})
    parts.append({"type": "text", "text": "\n\n" + tail})
    return [{"role": "system", "content": system}, {"role": "user", "content": parts}]


def message_text(messages: Sequence[Mapping[str, Any]]) -> str:
    """All text of a message list (image parts left out), e.g. for logging or estimates."""
    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            out.extend(str(p.get("text") or "") for p in content if isinstance(p, Mapping) and p.get("type") == "text")
    return "\n".join(out)


# ----------------------------------------------------------------- citations

_BRACKETS = re.compile(r"[\[［【]\s*(\d+(?:\s*(?:[,，、;；]|[-–~～至])\s*\d+)*)\s*[\]］】]")


def parse_citations(text: str) -> list[int]:
    """Evidence numbers cited as [1], [1][3], [1,3], [1-3], 【2】 … in order."""
    seen: list[int] = []
    for match in _BRACKETS.finditer(text or ""):
        group = match.group(1)
        for piece in re.split(r"\s*[,，、;；]\s*", group):
            span = re.split(r"\s*[-–~～至]\s*", piece)
            numbers = [int(x) for x in span if x]
            if len(numbers) == 2 and 0 < numbers[1] - numbers[0] <= 20:
                numbers = list(range(numbers[0], numbers[1] + 1))
            for number in numbers:
                if number not in seen:
                    seen.append(number)
    return seen


def citation_report(text: str, evidence: Iterable[Mapping[str, Any]] | Iterable[int]) -> dict[str, Any]:
    """Which cited numbers exist in the evidence and which do not."""
    valid: list[int] = []
    for index, item in enumerate(evidence, 1):
        valid.append(int(item.get("n", index)) if isinstance(item, Mapping) else int(item))
    cited = parse_citations(text)
    invalid = [n for n in cited if n not in valid]
    return {
        "cited": cited,
        "invalid": invalid,
        "uncited": [n for n in valid if n not in cited],
        "ok": bool(cited) and not invalid,
    }
