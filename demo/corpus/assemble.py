"""Assemble demo/data/mailbox.jsonl from all generators (deterministic given the LLM cache).

    python -m demo.corpus.assemble [--target 100000]
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import bible as B
from . import templates as T
from .calendar import AS_OF, END, START, TZ, WORKDAYS, at, cn_full, days, is_workday, leisure_time, next_workday, rand_day, work_time

DATA = B.DATA
GEN = DATA / "gen"
OUT = Path(__import__("os").environ.get("CORPUS_OUT") or DATA)
PEOPLE = B.all_people()
EMAIL2KEY = {p["email"]: k for k, p in PEOPLE.items() if p.get("email")}
EMAIL2KEY[B.OWNER["personal_email"]] = "linzhou"
OWNER_EMAILS = {B.OWNER["email"], B.OWNER["personal_email"]}
ORGFULL = {o["name"]: o["full"] for o in B.ORGS}

# ================================================================== sanitising
BRANDS = [
    (r"企业微信|钉钉|飞书", "企业IM"), (r"微信群", "群"), (r"微信", "IM"), (r"腾讯会议|Zoom|zoom|Teams", "云帆会议"),
    (r"淘宝|天猫|京东商城|京东|拼多多", "云购商城"), (r"美团外卖|饿了么|美团", "饭点外卖"), (r"滴滴出行|滴滴", "速达出行"),
    (r"顺丰速运|顺丰|中通|圆通|韵达|申通", "极速快递"), (r"支付宝", "手机银行"), (r"12306", "畅行火车票"), (r"携程|去哪儿|飞猪|同程", "差旅平台"),
    (r"亚朵|全季|汉庭|如家|希尔顿|万豪|洲际|喜来登|香格里拉|锦江之星|维也纳酒店|桔子酒店", "悦庭"),
    (r"阿里云|腾讯云|华为云|AWS|Azure|百度智能云", "天枢云"), (r"中国国航|国航|东航|南航|海航|东方航空|南方航空", "蓝鲸航空"),
    (r"菜鸟驿站", "快递驿站"), (r"望京SOHO|望京soho", "望京科技园"), (r"三里屯太古里|太古里", "三里屯商圈"), (r"环球影城", "主题乐园"),
    (r"迪士尼", "主题乐园"), (r"宜家", "家居商场"), (r"盒马", "生鲜超市"), (r"山姆", "会员超市"), (r"Kindle", "电子书"), (r"iPhone|苹果手机", "手机"), (r"iPad", "平板"), (r"MacBook", "笔记本"),
    (r"ChatGPT|GPT-4o|GPT-4|GPT-5|DeepSeek|文心一言|通义千问|Kimi|豆包", "大模型"), (r"星巴克|瑞幸", "咖啡店"), (r"海底捞", "火锅店"),
    (r"麦当劳|肯德基", "快餐店"), (r"喜茶|奈雪", "茶饮店"), (r"抖音|快手|小红书|B站|哔哩哔哩|知乎|微博", "网上"), (r"百度地图|高德地图|高德", "地图App"),
    (r"腾讯|阿里巴巴|字节跳动", "某大厂"), (r"华为", "某厂商"), (r"中国移动|中国联通|中国电信", "畅联通信"), (r"国家电网|国网", "城市电力"),
    (r"招商银行|工商银行|建设银行|农业银行|中国银行|交通银行|平安银行", "星辰银行"), (r"平安保险|中国人寿|太平洋保险", "康宁人寿"),
    (r"李强|习近平|李克强|王毅|马云|马化腾|雷军|任正非|刘强东|张一鸣|王兴", "陈某"),
    (r"(?<![A-Za-z])SAP(?![A-Za-z])", "ERP"), (r"Oracle数据库|Oracle", "商用数据库"), (r"(?<![A-Za-z])(?:Jira|JIRA)(?![A-Za-z])", "项目管理平台"), (r"海康威视|海康", "安防厂商"),
    (r"大华股份|大华(?=摄像|设备|相机|的)", "安防厂商"), (r"NVIDIA|Nvidia|英伟达", "GPU厂商"), (r"(?<![A-Za-z])Intel(?![A-Za-z])|英特尔", "芯片厂商"), (r"微软", "软件厂商"),
    (r"阿里(?!山)", "某大厂"), (r"百度", "搜索引擎"), (r"格力|美的空调|海尔", "家电品牌"), (r"戴尔|联想笔记本|ThinkPad", "笔记本品牌"), (r"万达广场|万达", "商业广场"),
    (r"飞天茅台|茅台|五粮液", "白酒"), (r"全聚德", "烤鸭店"), (r"SOHO", "科技园"), (r"GitLab|GitHub|Gitlab|Github", "代码平台"), (r"Confluence", "知识库"),
    (r"Tableau|PowerBI|Power BI", "BI工具"), (r"(?<![A-Za-z])Visio(?![A-Za-z])", "绘图工具"), (r"CCTV|央视", "电视台"), (r"三星|索尼|佳能|尼康|优衣库|耐克|阿迪达斯", "某品牌"),
    (r"大悦城|万象城|合生汇|荟聚", "购物中心"), (r"金杯车|金杯", "中巴车"), (r"(?<![A-Za-z])Costco(?![A-Za-z])|开市客", "会员超市"),
    (r"中石化|中石油", "石化企业"), (r"大众汽车", "某车企"), (r"(?<![A-Za-z])EMS(?![A-Za-z])", "邮政快递"),
]
BRAND_RE = [(re.compile(a), b) for a, b in BRANDS]
FLIGHT_RE = re.compile(r"(?<![A-Za-z0-9])(?:MU|CA|CZ|HU|ZH|3U|FM|MF|SC|9C|HO|KN|GS|8L)(\d{3,4})(?![0-9])")
TRAIN_OK = re.compile(r"\b[GDCKTZ]\d{1,4}\b")


ID_RE = re.compile(r"(?<![\dA-Za-z])(\d{3})\d{3}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}([\dXx])(?![\dA-Za-z])")
CARD_RE = re.compile(r"(?<![\dA-Za-z])((?:62|4\d|5[1-5])\d{2})\d{8,11}(\d{4})(?![\dA-Za-z])")
FULLPHONE_RE = re.compile(r"(?<![\dA-Za-z])(1[3-9]\d)(\d{4})(\d{4})(?![\dA-Za-z])")


MD_SEP = re.compile(r"(?m)^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$\n?")
MD_HEAD = re.compile(r"(?m)^#{1,6}\s*")
MD_RULE = re.compile(r"(?m)^\s*(-{3,}|\*{3,}|_{3,})\s*$")
MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__")
MD_BULLET = re.compile(r"(?m)^(\s*)[*+]\s+")


def demarkdown(text: str) -> str:
    """Attachment text should read like text extracted from Office/PDF files, not Markdown."""
    if not text:
        return text
    text = MD_SEP.sub("", text)
    text = MD_HEAD.sub("", text)
    text = MD_RULE.sub("", text)
    text = MD_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = MD_BULLET.sub(lambda m: m.group(1) + "• ", text)
    text = re.sub(r"(?m)^\|\s*", "", text)
    text = re.sub(r"\s*\|\s*$", "", text, flags=re.M)
    text = re.sub(r"\s*\|\s*", " | ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean(text: str) -> str:
    if not text:
        return text
    if "\\n" in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "  ")
    if "**" in text or "__" in text:
        text = MD_BOLD.sub(lambda mm: mm.group(1) or mm.group(2), text)
    for rx, rep in BRAND_RE:
        text = rx.sub(rep, text)
    text = FLIGHT_RE.sub(lambda m: "JW" + m.group(1), text)
    text = ID_RE.sub(lambda m: m.group(1) + "*" * 14 + m.group(2), text)
    text = CARD_RE.sub(lambda m: m.group(1) + " **** **** " + m.group(2), text)
    text = FULLPHONE_RE.sub(lambda m: m.group(1) + "****" + m.group(3), text)
    return text


# ================================================================== identities & signatures
PERSONAL_KEYS = {f["key"] for f in B.FAMILY + B.FRIENDS}


def ident(key: str, personal: bool = False) -> dict:
    p = PEOPLE[key]
    email = B.OWNER["personal_email"] if (personal and key == "linzhou") else p["email"]
    org = "" if key in PERSONAL_KEYS else (p.get("org") or "")
    if personal and key == "linzhou":
        org = ""
    return {"name": p["name"], "email": email, "org": org}


def signature(key: str, rng: random.Random, personal: bool) -> str:
    if personal:
        return ""
    p = PEOPLE[key]
    h = int(hashlib.md5(key.encode()).hexdigest()[:4], 16)
    if key == "linzhou":
        return ("\n\n--\n林舟 | 解决方案部总监\n云帆智能科技有限公司\nM: 138****5521  E: linzhou@yunfan-tech.cn\n" + B.CO_ADDR)
    if p.get("org") == B.COMPANY:
        style = h % 4
        if style == 0:
            return ""
        mob = f"  M: {p['mobile']}" if p.get("mobile") else ""
        if style == 1:
            return f"\n\n--\n{p['name']}\n{p['dept']} {p['title']}\n云帆智能科技有限公司{mob}"
        if style == 2:
            return f"\n\n————————————\n{p['name']} | {p['title']}\n云帆智能科技有限公司 {p['dept']}\n{('手机：' + p['mobile']) if p.get('mobile') else ''}\n{B.CO_ADDR}"
        return f"\n\n{p['name']}  {p['title']}\n云帆智能 · 让调度更聪明{mob}"
    org = p.get("org")
    if org in ORGFULL:
        mob = f"\n手机：{p['mobile']}" if p.get("mobile") else ""
        return f"\n\n--\n{p['name']}\n{p.get('title', '')}\n{ORGFULL[org]}{mob}"
    return ""


def quote_block(prev: dict, fwd: bool = False) -> str:
    t = dt.datetime.fromisoformat(prev["date"])
    head = "-------- 转发邮件信息 --------" if fwd else "------------------ 原始邮件 ------------------"
    to = "; ".join(f"{x['name']} <{x['email']}>" for x in prev["to"])
    cc = "; ".join(f"{x['name']} <{x['email']}>" for x in prev["cc"])
    lines = [head, f"发件人: {prev['from']['name']} <{prev['from']['email']}>", f"发送时间: {t.year}年{t.month}月{t.day}日 {t:%H:%M}", f"收件人: {to}"]
    if cc:
        lines.append(f"抄送: {cc}")
    lines.append(f"主题: {prev['subject']}")
    return "\n\n" + "\n".join(lines) + "\n\n" + prev["body"]


def make_att(a, rng: random.Random) -> dict | None:
    if not isinstance(a, dict):
        return None
    name = clean(str(a.get("name") or "附件.docx")).strip() or "附件.docx"
    text = demarkdown(clean(str(a.get("text") or "")))
    kind = a.get("kind") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    kind = {"docx": "doc", "doc": "doc", "xlsx": "sheet", "xls": "sheet", "csv": "sheet", "pdf": "pdf", "pptx": "slides", "ppt": "slides",
            "png": "image", "jpg": "image", "jpeg": "image"}.get(ext, kind if kind in ("doc", "sheet", "pdf", "slides", "image") else "doc")
    if not ext:
        name += {"doc": ".docx", "sheet": ".xlsx", "pdf": ".pdf", "slides": ".pptx", "image": ".png"}[kind]
    if len(text) < 30:
        return None
    return T.att(name, kind, text, rng)


SUBJ_PREFIX = re.compile(r"^\s*((?:re|RE|Re|回复|答复|fw|FW|Fw|Fwd|FWD|转发)\s*[:：]\s*)+")


def norm_subject(s: str, base: str | None, kind: str | None) -> str:
    s = clean(s or "").strip()
    core = SUBJ_PREFIX.sub("", s).strip() or (base or "")
    if kind == "re":
        return "Re: " + core
    if kind == "fw":
        return "Fw: " + core
    return core


# ================================================================== LLM threads -> mails
NAME2EMAIL = {p["name"]: p["email"] for k, p in PEOPLE.items() if p.get("email")}


def _fix_email(e: str, allowed: list[str]) -> str | None:
    e = (e or "").strip()
    for nm, em in NAME2EMAIL.items():
        if e == nm or (e.startswith(nm) and "@" not in e):
            if em in allowed or nm != "林舟":
                return em if not (nm == "林舟" and B.OWNER["personal_email"] in allowed) else B.OWNER["personal_email"]
    e = e.lower()
    m = re.search(r"[\w.\-+]+@[\w\-]+(?:\.[\w\-]+)+", e)
    e = m.group(0) if m else e
    if e in EMAIL2KEY:
        return e
    close = difflib.get_close_matches(e, allowed, n=1, cutoff=0.75)
    return close[0] if close else None


ATTACH: dict[str, dict] = {}
LLM_IDS: set[int] = set()
USED_JIDS: set[str] = set()


def load_attach():
    path = GEN / "attach.jsonl"
    if path.exists() and not ATTACH:
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            ATTACH[r["key"]] = r


def thread_to_mails(job: dict, rng: random.Random) -> list[dict]:
    personal = bool(job.get("personal"))
    allowed = [ident(k, personal)["email"] for k in job["people"]]
    owner_email = B.OWNER["personal_email"] if personal else B.OWNER["email"]
    start = dt.date.fromisoformat(job["date"])
    out = []
    prev_t = None
    base_subject = None
    msgs = job["messages"]
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        fe = _fix_email(str(m.get("from") or ""), allowed)
        if not fe:
            continue
        to = [x for x in (_fix_email(e, allowed) for e in (m.get("to") or [])) if x and x != fe]
        cc = [x for x in (_fix_email(e, allowed) for e in (m.get("cc") or [])) if x and x != fe and x not in to]
        if not to:
            to = [owner_email] if fe != owner_email else [a for a in allowed if a != owner_email][:1]
            if not to:
                continue
        if owner_email not in [fe] + to + cc:
            cc.append(owner_email)
        try:
            off = int(m.get("day_offset") or 0)
        except Exception:
            off = 0
        d = start + dt.timedelta(days=max(0, min(off, 30)))
        if not personal and not is_workday(d) and rng.random() < 0.8:
            d = next_workday(d)
        hm = re.match(r"(\d{1,2})[:：](\d{2})", str(m.get("time") or ""))
        h, mi = (int(hm.group(1)), int(hm.group(2))) if hm else (work_time(rng) if not personal else leisure_time(rng))
        h, mi = min(h, 23), min(mi, 59)
        t = at(d, h, mi, rng.randrange(60))
        if prev_t and t <= prev_t:
            t = prev_t + dt.timedelta(minutes=rng.randint(7, 95))
        if t > AS_OF:
            break
        prev_t = t
        raw_subj = str(m.get("subject") or "")
        kind = "fw" if re.match(r"^\s*(fw|fwd|转发)", raw_subj, re.I) else ("re" if (i > 0 and (SUBJ_PREFIX.match(raw_subj) or True)) else None)
        if i == 0 and not SUBJ_PREFIX.match(raw_subj):
            kind = None
        elif i == 0:
            kind = "re" if re.match(r"^\s*(re|回复|答复)", raw_subj, re.I) else kind
        subj = norm_subject(raw_subj, base_subject, kind)
        if base_subject is None:
            base_subject = SUBJ_PREFIX.sub("", subj).strip()
        fkey = EMAIL2KEY.get(fe, "linzhou")
        if not isinstance(m, dict):
            continue
        body = MD_HEAD.sub("", MD_SEP.sub("", clean(str(m.get("body") or "")))).strip()
        body = MD_BOLD.sub(lambda mm: mm.group(1) or mm.group(2), body)
        body += signature(fkey, rng, personal)
        if out and kind in ("re", "fw"):
            q = quote_block(out[-1], fwd=(kind == "fw"))
            body += q[:9000]
        raw_atts = [a for a in (m.get("attachments") or []) if isinstance(a, dict)]
        extra = ATTACH.get(f"{job['jid']}:{i}")
        if extra:
            if raw_atts:
                raw_atts[0] = {"name": raw_atts[0].get("name") or extra["name"], "kind": raw_atts[0].get("kind") or extra["kind"], "text": extra["text"]}
            else:
                raw_atts = [{"name": extra["name"], "kind": extra["kind"], "text": extra["text"]}]
        atts = [x for x in (make_att(a, rng) for a in raw_atts) if x]
        ext = any(PEOPLE[EMAIL2KEY[e]].get("org") not in (B.COMPANY, None, "") and not personal for e in [fe] + to + cc if e in EMAIL2KEY)
        cat = job["category"] if personal else ("client" if ext else "work")
        names = []
        for e in [fe] + to + cc:
            k = EMAIL2KEY.get(e)
            if k and k != "linzhou" and PEOPLE[k]["name"] not in names:
                names.append(PEOPLE[k]["name"])
        out.append({
            "date": t.isoformat(timespec="seconds"), "from": ident(fkey, personal) if fkey in PEOPLE else {"name": fe, "email": fe, "org": ""},
            "to": [ident(EMAIL2KEY[e], personal) for e in to], "cc": [ident(EMAIL2KEY[e], personal) for e in cc],
            "subject": subj, "body": body, "category": cat, "attachments": atts,
            "facets": {"org": job.get("org"), "project": job.get("project"), "people": names}, "thread": "t-" + job["jid"],
            "kind": "thread:" + job["kind"],
        })
    return out


# ================================================================== needle specs -> mails
def needle_mails(rng: random.Random) -> list[dict]:
    path = GEN / "needles.json"
    if not path.exists():
        return []
    from .needles import who

    data = json.loads(path.read_text("utf-8"))
    out = []
    fix = [("于2026年4月20日完成的年度体检", "于2026年4月10日完成的年度体检"), ("星河数据科技有限公司", "星河数据技术有限公司"),
           ("若逾期超过30日，甲方有权单方解除合同，乙方应退还已收取的全部款项，并支付合同总额20%的赔偿金。", ""),
           ("法定代表人：张伟", "法定代表人：韩振东"), ("法定代表人：李强", "法定代表人：邵启航"), ("联系人：王芳", "联系人：葛明"),
           ("北京市海淀区中关村软件园二期A座12层", B.CO_ADDR), ("，并向甲方支付合同总额20%的赔偿金", ""),
           ("上海市浦东新区张江高科技园区科苑路88号", "北京市朝阳区酒仙桥路10号 星河大厦")]
    for nid, s in data.items():
        pers = s["personal"]
        frm = who(s["from"], pers)
        to = [who(k, pers) for k in s["to"]]
        cc = [who(k, pers) for k in s["cc"]]
        body = s["body"]
        for x, y in fix:
            body = body.replace(x, y)
        body = MD_BOLD.sub(lambda mm: mm.group(1) or mm.group(2), MD_SEP.sub("", clean(body))).strip()
        if s["from"] in PEOPLE:
            body += signature(s["from"], rng, pers)
        atts = []
        for a in s["attachments"]:
            txt = a["text"]
            for x, y in fix:
                txt = txt.replace(x, y)
            atts.append(T.att(a["name"], a["kind"], demarkdown(clean(txt)), rng))
        t = dt.datetime.fromisoformat(s["date"]).replace(tzinfo=TZ, second=rng.randrange(60))
        names = [x["name"] for x in [frm] + to + cc if x["email"] not in OWNER_EMAILS and x["name"] not in ("育才实验小学家校通",)]
        if s["story"] == "q5":
            names = ["林小禾", "何静"]
        if s["story"] == "q2":
            names = ["林舟"]
        thread = None
        if s["subject"].startswith("Re: "):
            thread = "t-n-" + hashlib.md5(s["subject"][4:].encode()).hexdigest()[:8]
        else:
            thread = "t-n-" + hashlib.md5(s["subject"].encode()).hexdigest()[:8]
        out.append({"date": t.isoformat(timespec="seconds"), "from": frm, "to": to, "cc": cc, "subject": s["subject"], "body": body,
                    "category": s["category"], "attachments": atts,
                    "facets": {"org": s["org"], "project": s["project"], "people": names}, "thread": thread, "nid": nid, "kind": "needle"})
    # quote the parent mail in replies that belong to a needle thread
    by_subj = {m["subject"]: m for m in out}
    for m in out:
        if m["subject"].startswith("Re: "):
            parent = by_subj.get(m["subject"][4:])
            if parent and parent["date"] < m["date"]:
                m["body"] += quote_block(parent)
                m["thread"] = parent["thread"]
    return out


# ================================================================== trips
def trips(rng: random.Random) -> list[dict]:
    T_ = []
    fixed = [
        # Q4 needles and distractors
        dict(city="深圳", go=dt.date(2026, 8, 11), back=dt.date(2026, 8, 13), booked=dt.date(2026, 8, 4), purpose="2026智慧物流峰会（深圳）演讲及拜访瀚海华南区",
             org="瀚海物流", conf="YT2608110215", rate=680, order="JW260804517320", nid_flight="q4.aug_flight", nid_hotel="q4.aug_hotel"),
        dict(city="上海", go=dt.date(2026, 9, 2), back=dt.date(2026, 9, 4), booked=dt.date(2026, 8, 27), purpose="瀚海物流华东智能调度平台项目启动会", org="瀚海物流"),
        dict(city="上海", go=dt.date(2026, 10, 13), back=dt.date(2026, 10, 16), booked=dt.date(2026, 10, 9), booked_time=(9, 40), hotel_time=(10, 5),
             purpose="瀚海物流华东智能调度平台方案评审", org="瀚海物流", pay="星辰银行信用卡（尾号6411）", price=1780, conf="YT2610130522", rate=1280,
             rate_note="会展期间价格，含双早", folio_extra=440, settle="星辰银行信用卡（尾号6411）", guarantee="星辰银行信用卡（尾号6411）担保",
             order="JW261009204417", nid_flight="q4.oct_flight", nid_hotel="q4.oct_hotel"),
        dict(city="深圳", go=dt.date(2026, 11, 24), back=dt.date(2026, 11, 26), booked=dt.date(2026, 11, 16), booked_time=(9, 12), hotel_time=(9, 47),
             purpose="瀚海物流华南推广方案沟通", org="瀚海物流", change=((8, 0), (8, 30), dt.date(2026, 11, 19)), conf="YT2611240387", rate=680,
             room="高级大床房", order="JW261116382714", nid_flight="q4.flight", nid_change="q4.change", nid_hotel="q4.hotel", nid_approval="q4.approval"),
        dict(city="深圳", go=dt.date(2026, 11, 24), back=dt.date(2026, 11, 26), booked=dt.date(2026, 11, 10), hotel_time=(16, 20), hotel_only=True,
             hotel="悦庭酒店深圳福田店（深圳市福田区深南大道6008号）", conf="YT2611240122", rate=720, hotel_cancel=dt.date(2026, 11, 16),
             cancel_only=True, booked_by="助理杨雨（电话预订）", purpose="瀚海物流华南推广方案沟通",
             nid_hotel="q4.futian", nid_hotel_cancel="q4.futian_cancel", org="瀚海物流"),
        # family trips
        dict(city="扬州", go=dt.date(2024, 2, 8), back=dt.date(2024, 2, 16), booked=dt.date(2024, 1, 12), family=True, pax=["林舟", "苏晴", "林小禾"], pay="星辰银行信用卡（尾号6411）"),
        dict(city="扬州", go=dt.date(2025, 1, 26), back=dt.date(2025, 2, 3), booked=dt.date(2025, 1, 2), family=True, pax=["林舟", "苏晴", "林小禾"], pay="星辰银行信用卡（尾号6411）"),
        dict(city="扬州", go=dt.date(2026, 2, 13), back=dt.date(2026, 2, 21), booked=dt.date(2026, 1, 20), family=True, pax=["林舟", "苏晴", "林小禾"], pay="星辰银行信用卡（尾号6411）"),
        dict(city="成都", go=dt.date(2024, 7, 20), back=dt.date(2024, 7, 26), booked=dt.date(2024, 6, 15), family=True, pax=["林舟", "苏晴", "林小禾"], pay="星辰银行信用卡（尾号6411）"),
        dict(city="三亚", go=dt.date(2025, 7, 25), back=dt.date(2025, 7, 31), booked=dt.date(2025, 6, 20), family=True, pax=["林舟", "苏晴", "林小禾"], pay="星辰银行信用卡（尾号6411）"),
        dict(city="西安", go=dt.date(2026, 7, 18), back=dt.date(2026, 7, 23), booked=dt.date(2026, 6, 22), family=True, pax=["林舟", "苏晴", "林小禾"], pay="星辰银行信用卡（尾号6411）"),
    ]
    T_.extend(fixed)
    busy = [(x["go"] - dt.timedelta(days=3), x["back"] + dt.timedelta(days=3)) for x in fixed]
    city_of = {"瀚海物流": "上海", "华川制造": "苏州", "青禾医疗": "杭州", "北辰能源": "西安", "金穗农业": "郑州", "南湖文旅": "嘉兴", "远洋港务": "天津"}
    for p in B.PROJECTS:
        c = city_of.get(p.get("org"))
        if not c or p["name"] in ("瀚海物流·华东智能调度平台（一期）", "瀚海物流·华南推广"):
            continue
        s, e = dt.date.fromisoformat(p["start"]), min(dt.date.fromisoformat(p["end"]), END)
        n = max(2, int((e - s).days / 32))
        for i in range(n):
            go = rand_day(rng, s - dt.timedelta(days=30), e)
            while not is_workday(go) or go.weekday() == 4:
                go += dt.timedelta(days=1)
            back = go + dt.timedelta(days=rng.choice([1, 1, 2, 2, 3]))
            if back >= dt.date(2026, 10, 20) and go <= END:
                continue
            if back >= AS_OF.date() - dt.timedelta(days=2) or any(a <= go <= b or a <= back <= b for a, b in busy):
                continue
            busy.append((go - dt.timedelta(days=2), back + dt.timedelta(days=2)))
            T_.append(dict(city=c, go=go, back=back, booked=go - dt.timedelta(days=rng.randint(3, 12)), org=p["org"],
                           purpose=rng.choice(["客户需求调研", "方案汇报", "项目启动会", "项目验收", "客户高层拜访", "现场问题处理"]),
                           go_time=rng.choice([(7, 30), (8, 0), (9, 15), (10, 40), (13, 20)]), back_time=rng.choice([(16, 50), (18, 40), (19, 55), (21, 10)])))
    T_.sort(key=lambda x: x["go"])
    return T_


def train_mail(rng, tr) -> list[dict]:
    rail = T.svc("畅行火车票")
    out = []
    st = {"天津": ("北京南", "天津", "C2"), "淄博": ("北京南", "淄博", "G")}
    a, b, pre = st.get(tr["city"], ("北京南", tr["city"], "G"))
    for d, x, y in [(tr["go"], a, b), (tr["back"], b, a)]:
        no = f"{pre}{rng.randint(1, 2999)}" if pre != "C2" else f"C{rng.randint(2001, 2299)}"
        h = rng.choice([7, 8, 9, 14, 16, 18])
        t = at(tr["booked"], *work_time(rng))
        if t <= AS_OF:
            out.append(T.mail(t, rail, [T.ME_P], f"【畅行火车票】购票成功：{d.month}月{d.day}日 {no} {x}-{y}",
                              f"尊敬的林舟先生：您已成功购买{d:%Y-%m-%d} {no}次 {x}{h:02d}:{rng.choice(['05', '20', '35', '50'])}开往{y}的车票，"
                              f"{rng.randint(1, 16):02d}车{rng.randint(1, 18):02d}{rng.choice('ABCDF')}号，二等座，票价¥{rng.choice([54.5, 59.0, 249.0, 315.5])}。"
                              f"请携带购票时使用的有效身份证件乘车。\n畅行火车票", "travel", "畅行火车票", kind="train"))
    return out


# ================================================================== credit-card plans (Q3)
def card_plan(rng: random.Random) -> dict:
    plan = {}
    top = [(dt.date(2026, 10, 9), "蓝鲸航空", 3560.00), (dt.date(2026, 10, 16), "悦庭酒店（上海）", 4280.00), (dt.date(2026, 10, 22), "云购商城", 2999.00)]
    target = 18642.37
    r = random.Random(20261105)
    others = []
    lo, hi = dt.date(2026, 10, 6), dt.date(2026, 11, 5)
    pool = [m for m in T.MERCH if m[0] not in ("云购商城",)]
    while True:
        others = []
        for _ in range(27):
            m, a, b, _c = r.choice(pool)
            amt = round(r.uniform(a, b), 2) if a != b else float(a)
            others.append((T.rand_day(r, lo, hi), m, amt))
        rest = round(target - sum(x[2] for x in top) - sum(x[2] for x in others[:-1]), 2)
        if 30 <= rest <= 1500:
            d0, m0, _ = others[-1]
            others[-1] = (d0, "云购商城", rest) if False else (d0, "湘味小馆", rest)
            break
    # a few more 云购 orders (small) appear as other lines; keep top-3 unambiguous
    txns = top + others
    assert abs(sum(x[2] for x in txns) - target) < 0.005
    assert sorted(txns, key=lambda x: -x[2])[3][2] < 2000
    plan[("xc", "2026-11")] = {"txns": txns, "total": target, "nid": "q3.statement", "nid_remind": "q3.remind"}
    sep_extra = [(dt.date(2026, 9, 12), "云购商城", 1899.00), (dt.date(2026, 9, 20), "动力健身", 2680.00), (dt.date(2026, 9, 27), "湘味小馆", 836.00)]
    plan[("xc_extra", "2026-10")] = sep_extra
    return plan


# ================================================================== newsletters / promos / school / weekly
NL_TO_PERSONAL = {"晨读财经", "亲子成长周刊", "跑者周报", "健康生活指南", "管理者读书会", "智聘网职位推荐"}
NL_FOOT = "\n\n————————————————\n您收到此邮件是因为您订阅了《{nl}》。如不想继续接收，可点击邮件底部「退订」。\n© {y} {nl} 编辑部 版权所有"


def newsletter_mails(rng) -> list[dict]:
    path = GEN / "newsletters.jsonl"
    if not path.exists():
        return []
    meta = {n["name"]: n for n in B.NEWSLETTERS}
    out = []
    issue_no = Counter()
    for line in open(path, encoding="utf-8"):
        j = json.loads(line)
        nl = meta[j["nl"]]
        d = dt.date.fromisoformat(j["date"])
        issue_no[j["nl"]] += 1
        items = j["items"]
        h = {"daily": 7, "weekly": 17, "biweekly": 16, "monthly": 10}[nl["freq"]]
        t = at(d, h, rng.randint(0, 50))
        if t > AS_OF:
            continue
        n = issue_no[j["nl"]] + {"daily": 1200, "weekly": 260, "biweekly": 80, "monthly": 60}[nl["freq"]]
        toc = "\n".join(f"{i + 1}. {clean(it['title'])}" for i, it in enumerate(items))
        parts = "\n\n".join(f"【{clean(it['title'])}】\n{clean(it['text'])}" for it in items)
        body = f"{nl['name']} · 第{n}期 · {d.year}年{d.month}月{d.day}日\n\n本期导读\n{toc}\n\n{parts}" + NL_FOOT.format(nl=nl["name"], y=d.year)
        frm = {"name": nl["name"], "email": nl["email"], "org": nl["name"]}
        to = [T.ME_P if nl["name"] in NL_TO_PERSONAL else T.ME_W]
        cat = "work" if nl["name"] == "云帆内刊" else "newsletter"
        subj = f"《{nl['name']}》第{n}期｜{clean(items[0]['title'])}"
        out.append(T.mail(t, frm, to, subj, body, "newsletter" if cat == "newsletter" else "newsletter", nl["name"], kind="newsletter"))
    return out


PROMO_RATE = {  # emails per week
    "云购商城": 6, "饭点外卖": 3, "蓝鲸航空": 1.5, "悦庭酒店": 1, "星辰银行": 2, "海川银行": 1, "书香书城": 1.5, "启明星少儿编程": 1, "乐思英语": 1,
    "动力健身": 0.7, "远途汽车": 0.5, "安康体检中心": 0.4, "康宁人寿": 0.5, "畅联通信": 0.8, "青柚视频": 1.5, "速达出行": 1, "星河数据": 0.5, "天枢云": 1,
    "智图科技": 0.3,
}
B2B = {"星河数据": ("星河数据", "marketing@xinghe-data.com"), "天枢云": ("天枢云", "events@tianshu-cloud.com"), "智图科技": ("智图科技", "news@zhitu-tech.com")}


def promo_mails(rng, scale: float = 1.0) -> list[dict]:
    path = GEN / "promos.jsonl"
    if not path.exists():
        return []
    pools = defaultdict(list)
    for line in open(path, encoding="utf-8"):
        j = json.loads(line)
        for it in j["items"]:
            pools[j["sender"]].append(it)
    out = []
    all_days = days()
    for sender, items in pools.items():
        if sender in B2B:
            nm, em = B2B[sender]
            frm = {"name": nm, "email": em, "org": sender}
            to = [T.ME_W]
        else:
            frm = T.svc(sender)
            to = [T.ME_P]
        n = int(PROMO_RATE.get(sender, 1) * len(all_days) / 7 * scale)
        r = random.Random(sender)
        for i in range(n):
            d = all_days[int(i * len(all_days) / n) + 0] if n else all_days[0]
            d = d + dt.timedelta(days=r.randint(0, max(0, int(7 / max(PROMO_RATE.get(sender, 1), 0.1)) - 1)))
            if d > END:
                continue
            # season-aware pick
            cand = [it for it in items if str(it.get("season", "all")).strip() in ("all", "", str(d.month)) or str(d.month) in str(it.get("season"))]
            it = r.choice(cand or items)
            h = r.choice([10, 11, 12, 18, 19, 20, 21])
            t = at(d, h, r.randint(0, 59))
            if t > AS_OF:
                continue
            body = clean(it["text"]) + f"\n\n——\n本邮件由{frm['name']}发送。如不希望再收到此类邮件，请点击「退订」。"
            out.append(T.mail(t, frm, to, clean(it["title"]), body, "promotion", sender, kind="promo"))
    return out


def school_mails(rng) -> list[dict]:
    path = GEN / "school.jsonl"
    if not path.exists():
        return []
    frm = {"name": "育才实验小学家校通", "email": "notice@yucai-school.edu.cn", "org": "育才实验小学"}
    out = []
    for line in open(path, encoding="utf-8"):
        j = json.loads(line)
        m = dt.date.fromisoformat(j["month"])
        for it in j["items"]:
            try:
                dd = int(re.sub(r"\D", "", str(it.get("day"))) or 1)
            except Exception:
                dd = 1
            dd = max(1, min(dd, 28))
            d = dt.date(m.year, m.month, dd)
            if d > dt.date(2026, 10, 18):
                continue
            t = at(d, rng.choice([8, 12, 16, 17, 18, 20]), rng.randint(0, 59))
            body = clean(it["text"]) + "\n\n（本通知由育才实验小学家校通发送，请勿直接回复。家长可在App内查看历史通知。）"
            out.append(T.mail(t, frm, [T.ME_P], clean(it["title"]), body, "school", "育才实验小学", people=["林小禾", "何静"], kind="school"))
    return out


HW = {
    "语文": ["背诵课文《{t}》第{n}自然段", "完成《语文练习册》第{p}页", "阅读打卡30分钟（{book}）", "抄写第{u}单元生字词各两遍", "写一篇日记，题目自拟（{theme}）",
             "预习第{l}课，圈出生字并组词", "完成第{u}单元基础练习", "听写第{l}课词语，自己订正"],
    "数学": ["完成《数学口算本》第{p}页", "练习册第{p}-{p2}页", "订正今天的课堂小测", "预习第{u}单元第{l}节", "完成课本第{p}页练一练{a}-{b}题", "整理错题本（本周错题）"],
    "英语": ["听读Unit {u} 课文3遍", "抄写Unit {u} 单词各三遍", "完成英语练习册Unit {u} Part {a}", "英语跟读打卡10分钟"],
    "其他": ["跳绳1分钟×3组并记录", "准备明天美术课材料：彩纸、剪刀、胶棒", "科学课：观察并记录豆芽生长", "整理书包，带好红领巾", "音乐课复习歌曲《{song}》", "体育课穿运动鞋"],
}
TEXTS = ["荷花", "爬山虎的脚", "观潮", "繁星", "精卫填海", "延安，我把你追寻", "牛和鹅", "麻雀", "一个豆荚里的五粒豆", "西门豹治邺"]
BOOKS = ["《夏洛的网》", "《草房子》", "《西游记》青少版", "《昆虫记》", "《小王子》", "《城南旧事》", "《中国神话传说》", "《细菌世界历险记》"]


def homework_mails(rng) -> list[dict]:
    from .gen_threads import grade_at

    frm = {"name": "育才实验小学家校通", "email": "notice@yucai-school.edu.cn", "org": "育才实验小学"}
    out = []
    for d in WORKDAYS:
        md = (d.month, d.day)
        if (1, 15) <= md <= (2, 28) or (7, 5) <= md <= (8, 31):
            continue
        if rng.random() < 0.15:
            continue
        g = grade_at(d)
        lines = []
        for subj, pool in HW.items():
            if subj == "英语" and g in ("一年级", "二年级"):
                continue
            if subj == "其他" and rng.random() < 0.6:
                continue
            item = rng.choice(pool).format(t=rng.choice(TEXTS), n=rng.randint(2, 6), p=rng.randint(10, 80), p2=rng.randint(81, 90), u=rng.randint(1, 8),
                                           l=rng.randint(1, 26), book=rng.choice(BOOKS), theme=rng.choice(["秋天的发现", "我的周末", "一件难忘的事", "我的好朋友"]),
                                           a=rng.randint(1, 3), b=rng.randint(4, 8), song=rng.choice(["送别", "雪绒花", "我们的田野"]))
            lines.append(f"{subj}：{item}")
        tail = "请家长督促孩子按时完成，早点休息。" if not (dt.date(2026, 10, 19) <= d <= END) else "请孩子按时完成，早点休息。"
        if rng.random() < 0.2 and not (dt.date(2026, 10, 19) <= d <= END):
            tail = "数学练习册完成后请家长检查签字。早点休息。"
        body = f"各位家长：\n今天（{d.month}月{d.day}日）{g}3班的作业如下：\n" + "\n".join(lines) + f"\n{tail}\n——{g}3班 何静\n\n（本消息由育才实验小学家校通发送）"
        t = at(d, rng.choice([16, 17, 17, 18]), rng.randint(0, 59))
        out.append(T.mail(t, frm, [T.ME_P], f"【今日作业】{d.month}月{d.day}日 {g}3班", body, "school", "育才实验小学", people=["林小禾", "何静"], kind="homework"))
    return out


def weekly_mails(rng) -> list[dict]:
    path = GEN / "weekly.jsonl"
    if not path.exists():
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        j = json.loads(line)
        k = j["who"]
        for it in j["items"]:
            try:
                d = dt.date.fromisoformat(str(it.get("date"))[:10])
            except Exception:
                continue
            t = at(d, rng.choice([17, 17, 18, 18, 19, 20, 21]), rng.randint(0, 59))
            if t > AS_OF or not (START <= d <= END):
                continue
            cc = [ident("gucheng")] if k != "gucheng" and rng.random() < 0.3 else []
            subj = clean(it["title"])
            if "周报" not in subj:
                subj = f"【周报】{PEOPLE[k]['name']} {d:%m.%d} " + subj
            out.append({"date": t.isoformat(timespec="seconds"), "from": ident(k), "to": [ident("linzhou")], "cc": cc, "subject": subj,
                        "body": clean(it["text"]) + signature(k, rng, False), "category": "work", "attachments": [],
                        "facets": {"org": None, "project": None, "people": [PEOPLE[k]["name"]]}, "thread": None, "kind": "weekly"})
    return out


# ================================================================== service footers (templated mail)
FOOTERS = {
    "云购商城": "\n\n————————————————\n【温馨提示】云购商城不会以任何理由要求您提供银行卡密码、短信验证码，谨防诈骗。订单相关问题请通过App「我的-客服」联系在线客服（7×24小时）。"
                "\n本邮件由系统自动发送，请勿直接回复。您可在「设置-消息通知」中管理邮件订阅。\n云购商城（北京）电子商务有限公司 | 客服热线 400-8xx-xxxx | 隐私政策 | 用户协议\n© 2016-{y} 云购商城 版权所有",
    "极速快递": "\n\n——\n极速快递 · 准时必达\n如有疑问请致电客服 95xxx 或在极速快递App内咨询。请勿将取件码告知他人，快递员不会以任何理由索要验证码。\n本邮件为系统自动发送，请勿回复。",
    "速达出行": "\n\n——\n行程中如遗失物品，可在App「我的行程-联系司机」处理；如对费用有疑问，请在7天内发起申诉。\n速达出行 · 安全每一程 | 客服 400-6xx-xxxx | 本邮件由系统发送，请勿回复",
    "饭点外卖": "\n\n——\n饭点外卖 · 好吃不贵准时达\n如对订单有任何问题，请在App内「订单-联系客服」处理，订单完成后24小时内可申请售后。\n本邮件为系统自动发送，请勿直接回复。",
    "蓝鲸航空": "\n\n————————————————\n蓝鲸航空股份有限公司 | 客服热线 95xxx | 鲸享会会员服务\n温馨提示：请警惕“航班取消退改签”诈骗短信与电话，蓝鲸航空不会要求您向个人账户转账。\n本邮件由系统自动发送，请勿回复。",
    "悦庭酒店": "\n\n————————————————\n悦庭酒店集团 | 全国预订热线 400-1xx-xxxx | 悦庭会员App\n为保障您的隐私，本邮件中的个人信息已做部分隐藏处理。如您未进行相关操作，请联系我们。",
    "海川银行": "\n\n——\n海川银行信用卡中心 | 客服热线 95xxx\n风险提示：请妥善保管您的卡片、密码和验证码，切勿向他人透露。本邮件仅为通知，具体以银行系统记录为准。",
    "畅联通信": "\n\n——\n畅联通信 | 客服热线 10xxx | 网上营业厅\n本邮件为系统自动发送的账单通知，请勿直接回复。如需纸质发票，请前往营业厅或在网上营业厅申请电子发票。",
    "嘉禾物业": "\n\n嘉禾物业服务中心 24小时值班电话：010-8xxx-xxxx\n地址：嘉禾园小区东门物业楼一层",
    "鲸云盘": "\n\n——\n鲸云盘 · 安全存储每一份回忆\n本邮件为系统通知，请勿回复。",
    "城市电力服务": "\n\n——\n城市电力服务 | 客服热线 95xxx | 可通过手机银行或电力服务App缴费。逾期未缴将按规定收取违约金。",
    "京华燃气": "\n\n——\n京华燃气 | 24小时抢修电话 96xxx\n安全提示：发现燃气泄漏请立即关闭阀门、开窗通风，切勿开关电器。",
}


def add_footer(m: dict):
    org = (m.get("facets") or {}).get("org")
    f = FOOTERS.get(org)
    if f and m.get("kind") not in ("needle",) and m.get("category") in ("notification", "finance", "travel"):
        m["body"] = m["body"] + f.replace("{y}", m["date"][:4])


# ================================================================== assembly
def thread_mails(work_budget: int, family_budget: int) -> list[dict]:
    """LLM threads, pass by pass, until each category budget (in emails) is filled."""
    files = [GEN / "threads.jsonl"] + sorted(GEN.glob("threads[0-9]*.jsonl"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    used = {"work": 0, "family": 0}
    budget = {"work": work_budget, "family": family_budget}
    out = []
    for f in files:
        if not f.exists():
            continue
        jobs = [json.loads(line) for line in open(f, encoding="utf-8")]
        conv = [(j, thread_to_mails(j, random.Random(j["jid"]))) for j in jobs]
        for key in ("work", "family"):
            part = [(j, ms) for j, ms in conv if (key == "family") == bool(j.get("personal"))]
            total = sum(len(ms) for _, ms in part)
            if used[key] + total <= budget[key]:
                for j, ms in part:
                    out += ms
                    USED_JIDS.add(j["jid"])
                used[key] += total
                continue
            part.sort(key=lambda x: hashlib.md5(x[0]["jid"].encode()).hexdigest())
            for j, ms in part:
                if used[key] + len(ms) > budget[key]:
                    continue
                out += ms
                used[key] += len(ms)
                USED_JIDS.add(j["jid"])
            continue
    print(f"[assemble] thread emails used: {used}")
    (GEN / "used_jids.json").write_text(json.dumps(sorted(USED_JIDS)), "utf-8")
    return out


def build(target: int | None = None, seed: int = 20261120, work_budget: int = 43000, family_budget: int = 9500):
    rng = random.Random(seed)
    mails: list[dict] = []
    # templated notifications
    trip_list = trips(random.Random(seed + 1))
    plan = card_plan(rng)
    mails += T.bank_mail(random.Random(seed + 2), plan)
    mails += T.savings_mail(random.Random(seed + 3))
    for tr in trip_list:
        r = random.Random(f"{tr['city']}{tr['go']}{tr.get('conf', '')}")
        if tr.get("hotel_only"):
            mails += T.hotel_mail(r, tr)
            continue
        if tr["city"] in ("天津", "淄博"):
            mails += train_mail(r, tr)
        else:
            mails += T.flight_mail(r, tr)
            if not tr.get("family"):
                mails += T.hotel_mail(r, tr)
    mails += T.expense_mail(random.Random(seed + 4), trip_list)
    forced = [(dt.date(2026, 10, 22), ["儿童护眼学习平板 12英寸（含笔）"], [2999.00], "YG26102288103725", "q3.order")]
    mails += T.shopping_mail(random.Random(seed + 5), forced=forced)
    mails += T.daily_life_mail(random.Random(seed + 6))
    mails += T.health_mail(random.Random(seed + 7))
    mails += T.insurance_mail(random.Random(seed + 15))
    mails += T.system_mail(random.Random(seed + 8))
    # LLM content
    load_attach()
    mails += thread_mails(work_budget, family_budget)
    mails += needle_mails(random.Random(seed + 9))
    mails += newsletter_mails(random.Random(seed + 10))
    mails += school_mails(random.Random(seed + 12))
    mails += weekly_mails(random.Random(seed + 13))
    mails += homework_mails(random.Random(seed + 14))
    # promotions fill the remaining volume (real inboxes repeat campaigns)
    base = promo_mails(random.Random(seed + 11), 1.0)
    scale = 1.0
    if target:
        inwin = sum(1 for m in mails if START.isoformat() <= m["date"][:10] and m["date"] <= AS_OF.isoformat())
        need = target + 300 - inwin
        scale = min(6.0, max(1.0, need / max(len(base), 1)))
    mails += base if scale == 1.0 else promo_mails(random.Random(seed + 11), scale)
    print(f"[assemble] promo scale {scale:.2f}")
    mails = [m for m in mails if START.isoformat() <= m["date"][:10] and m["date"] <= AS_OF.isoformat()]
    for m in mails:
        add_footer(m)
    # trim to the exact target by dropping standalone templated mail (never needles or LLM threads)
    if target and len(mails) > target:
        drop_kinds = ["otp", "promo", "ride", "food", "cc_alert", "calendar", "oa", "pm", "docs", "express", "homework", "newsletter"]
        r2 = random.Random(seed + 99)
        excess = len(mails) - target
        pool = [i for i, m in enumerate(mails) if m.get("kind") in drop_kinds and not m.get("nid") and m["date"] < "2026-08-01"]
        r2.shuffle(pool)
        drop = set(pool[:excess])
        mails = [m for i, m in enumerate(mails) if i not in drop]
    mails.sort(key=lambda m: (m["date"], m["subject"]))
    nids = {}
    final = []
    for i, m in enumerate(mails, 1):
        frm = m["from"]
        sent = frm.get("email") in OWNER_EMAILS
        d = m["date"][:10]
        if sent:
            folder = "sent"
        elif d < "2026-06-01" and int(hashlib.md5(f"{i}{m['subject']}".encode()).hexdigest()[:2], 16) < 200:
            folder = "archive"
        else:
            folder = "inbox"
        facets = m.get("facets") or {"org": None, "project": None, "people": []}
        if not facets.get("org") and frm.get("org") and frm.get("org") != B.COMPANY:
            facets["org"] = frm.get("org")
        thread = m.get("thread") or ("t-" + hashlib.md5(f"{frm.get('email')}|{m['subject']}|{m['date']}".encode()).hexdigest()[:10])
        if not thread.startswith("t-"):
            thread = "t-" + thread
        llm_kinds = ("thread:", "needle", "newsletter", "school", "weekly")
        rec_llm = str(m.get("kind") or "").startswith(llm_kinds) and not (m.get("kind") == "needle" and m.get("nid", "").startswith(("q3.", "q4.flight", "q4.change", "q4.hotel")))
        rec = {"id": i, "thread": thread, "date": m["date"], "folder": folder, "category": m["category"], "from": frm, "to": m["to"], "cc": m.get("cc") or [],
               "subject": m["subject"], "body": m["body"],
               "attachments": [{k: a[k] for k in ("name", "mime", "size", "kind", "text")} for a in m.get("attachments") or []],
               "facets": {"org": facets.get("org"), "project": facets.get("project"), "people": facets.get("people") or []}}
        if m.get("nid"):
            nids[m["nid"]] = i
        if rec_llm:
            LLM_IDS.add(i)
        final.append(rec)
    return final, nids


def write(final, nids):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "mailbox.jsonl.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in final:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(OUT / "mailbox.jsonl")
    (GEN / "nid_map.json").write_text(json.dumps(nids, ensure_ascii=False, indent=1), "utf-8")
    cats = Counter(r["category"] for r in final)
    n_att = sum(len(r["attachments"]) for r in final)
    body_b = sum(len(r["body"].encode()) for r in final)
    att_b = sum(len((a["text"] or "").encode()) for r in final for a in r["attachments"])
    stats = {
        "emails": len(final), "attachments": n_att, "emails_with_attachments": sum(1 for r in final if r["attachments"]),
        "attachment_binary_bytes": sum(a["size"] for r in final for a in r["attachments"]),
        "body_bytes": body_b, "attachment_text_bytes": att_b, "text_bytes": body_b + att_b,
        "file_bytes": (OUT / "mailbox.jsonl").stat().st_size,
        "date_range": [final[0]["date"], final[-1]["date"]], "as_of": AS_OF.isoformat(),
        "categories": dict(sorted(cats.items(), key=lambda x: -x[1])),
        "folders": dict(Counter(r["folder"] for r in final)),
        "threads": len({r["thread"] for r in final}),
        "orgs": dict(Counter(r["facets"]["org"] for r in final if r["facets"]["org"]).most_common(40)),
        "attachment_kinds": dict(Counter(a["kind"] for r in final for a in r["attachments"])),
        "by_year": dict(Counter(r["date"][:4] for r in final)),
        "llm_written_emails": len(LLM_IDS) + len({r["subject"] for r in final if r["category"] == "promotion"}),
        "llm_written_note": "unique LLM-written emails: threads + needle storylines + newsletter issues + school notices + weekly reports + distinct promotion texts (promotions are re-sent several times)",
    }
    from .llm import total_cost

    stats["llm_cost"] = total_cost()
    (OUT / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), "utf-8")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=0)
    ap.add_argument("--dry", action="store_true", help="count only; do not write files")
    args = ap.parse_args()
    final, nids = build(args.target or None)
    if args.dry:
        print(len(final), dict(Counter(r["category"] for r in final).most_common()))
        print("text MB", round(sum(len(r["body"].encode()) + sum(len(a["text"].encode()) for a in r["attachments"]) for r in final) / 1e6, 1),
              "attachments", sum(len(r["attachments"]) for r in final))
        return
    stats = write(final, nids)
    print(json.dumps({k: stats[k] for k in ("emails", "attachments", "text_bytes", "categories")}, ensure_ascii=False, indent=1))
    from . import presets

    presets.write(nids)


if __name__ == "__main__":
    main()
