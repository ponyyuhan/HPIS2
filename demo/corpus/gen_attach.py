"""Write full attachment documents for thread emails that mention or carry an attachment.

    python -m demo.corpus.gen_attach [threads.jsonl threads2.jsonl ...] [--cap N]

The thread writer tends to produce short attachments (or none, while the body says
"见附件"). This pass asks a cheap model for the full extracted text of the file.
Results go to demo/data/gen/attach.jsonl keyed by "<jid>:<message index>".
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re

from . import bible as B
from .gen_threads import PEOPLE, EMAIL2KEY
from .llm import CHEAP, LITE, LLM, parse_json

GEN = B.DATA / "gen"
MENTION = re.compile(r"(见附件|附件是|附件为|附件中|附件里|附上|随附|附件《|附件：|详见附件|请查收|附件已|附件请|附件内|附件包含)")
NAME_IN_BODY = re.compile(r"《([^》]{3,40})》")


def candidates(paths):
    out = []
    for p in paths:
        for line in open(GEN / p, encoding="utf-8"):
            j = json.loads(line)
            if j.get("personal"):
                continue
            for i, m in enumerate(j["messages"]):
                if not isinstance(m, dict):
                    continue
                atts = [a for a in (m.get("attachments") or []) if isinstance(a, dict)]
                body = str(m.get("body") or "")
                if atts:
                    if len(str(atts[0].get("text") or "")) < 900:
                        out.append(("expand", j, i, m, atts[0]))
                elif MENTION.search(body):
                    out.append(("create", j, i, m, None))
    return out


def prompt(kind, job, i, m, att):
    sender = EMAIL2KEY.get(str(m.get("from", "")).strip().lower())
    p = PEOPLE.get(sender, {}) if sender else {}
    who = f"{p.get('name', '')}（{p.get('title', '')}，{p.get('org', '')}）" if p else "发件人"
    proj = f"相关项目：{job['project']}。" if job.get("project") else ""
    body = str(m.get("body") or "")[:900]
    if kind == "expand":
        name = str(att.get("name") or "附件.docx")
        task = (f"邮件附件《{name}》目前只有摘要。请写出这个附件文件的完整内容（像从真实文件中提取出的文字），以下是已有摘要，可在其基础上扩展：\n{str(att.get('text') or '')[:600]}\n")
        fmt = '只输出JSON：{"name":"' + name + '","kind":"doc|sheet|pdf|slides","text":"附件全文"}'
    else:
        hint = NAME_IN_BODY.search(body)
        task = "邮件正文提到了附件，但附件内容缺失。请确定附件文件名（与正文一致" + (f"，正文中提到《{hint.group(1)}》" if hint else "") + "），并写出附件文件的完整内容。\n"
        fmt = '只输出JSON：{"name":"文件名（含扩展名 .docx/.xlsx/.pdf/.pptx）","kind":"doc|sheet|pdf|slides","text":"附件全文"}'
    u = (f"背景：云帆智能科技有限公司（北京，智能调度与数据智能解决方案）。{proj}\n"
         f"下面是 {who} 在{job['date']}前后发出的一封邮件：\n主题：{m.get('subject', '')}\n正文：\n{body}\n\n"
         f"{task}要求：内容与邮件一致、具体专业（数字、日期、人名、条目），结构清晰（标题、章节编号；表格用“|”分隔的文本表格；PPT写成“第N页：标题+要点”）；"
         "长度1500-3000字；不得出现任何真实公司、品牌或产品名；手机号脱敏。\n" + fmt)
    return [{"role": "user", "content": u}]


def check(text):
    d = parse_json(text)
    if isinstance(d, list):
        d = d[0]
    assert isinstance(d, dict) and len(str(d.get("text") or "")) > 600 and d.get("name")
    return d


MODEL = CHEAP


async def run(cands, out_path, conc):
    done = {}
    if out_path.exists():
        for line in open(out_path, encoding="utf-8"):
            r = json.loads(line)
            done[r["key"]] = r
    todo = [c for c in cands if f"{c[1]['jid']}:{c[2]}" not in done]
    print(f"[attach] {len(cands)} candidates, {len(todo)} to do", flush=True)
    lock = asyncio.Lock()
    fh = open(out_path, "a", encoding="utf-8")
    async with LLM(concurrency=conc, timeout=180, log_every=200) as llm:
        async def one(c):
            kind, job, i, m, att = c
            key = f"{job['jid']}:{i}"
            seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
            txt = await llm.chat(prompt(kind, job, i, m, att), model=MODEL, temperature=0.8, max_tokens=4500, seed=seed, tag="attach",
                                 validate=check, attempts=3)
            if not txt:
                return
            d = check(txt)
            rec = {"key": key, "mode": kind, "name": str(d["name"]).strip(), "kind": str(d.get("kind") or "doc"), "text": str(d["text"])}
            async with lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        await asyncio.gather(*(one(c) for c in todo))
        print(f"[attach] done cost=${llm.cost:.3f}", flush=True)
    fh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=["threads.jsonl"])
    ap.add_argument("--cap", type=int, default=3000)
    ap.add_argument("--concurrency", type=int, default=48)
    ap.add_argument("--lite", action="store_true", help="use qwen3.5-flash instead of gemma")
    args = ap.parse_args()
    global MODEL
    if args.lite:
        MODEL = LITE
    cands = candidates(args.files)
    used = GEN / "used_jids.json"
    if used.exists():
        keep = set(json.loads(used.read_text("utf-8")))
        cands = [c for c in cands if c[1]["jid"] in keep]
    rng = random.Random(42 + len(args.files))
    creates = [c for c in cands if c[0] == "create"]
    expands = [c for c in cands if c[0] == "expand"]
    rng.shuffle(creates)
    rng.shuffle(expands)
    n_create = min(len(creates), int(args.cap * 0.6))
    sel = creates[:n_create] + expands[: args.cap - n_create]
    asyncio.run(run(sel, GEN / "attach.jsonl", args.concurrency))


if __name__ == "__main__":
    main()
