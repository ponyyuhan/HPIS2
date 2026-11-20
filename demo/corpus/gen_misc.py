"""LLM pools for newsletters, promotions, school notices and weekly reports.

    python -m demo.corpus.gen_misc [newsletters|promos|school|weekly|all]

Outputs JSONL files under demo/data/gen/ (raw replies are cached).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import random
import sys

from . import bible as B
from .calendar import END, START, WORKDAYS, days
from .gen_threads import NEEDLE_GUARD, PEOPLE, SYSTEM, grade_at
from .llm import CHEAP, FLASH, LLM, parse_json

GEN = B.DATA / "gen"
STYLE = ("内容必须是虚构的：不得出现任何真实存在的公司、品牌、产品、App、媒体或真实人物姓名（可以用虚构的公司名、人名）；"
         "数字、地点、时间要具体；语言自然，不要空洞套话。只输出JSON，不要代码块标记。")


def _seed(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _items_ok(n):
    def check(text):
        d = parse_json(text)
        items = d["items"] if isinstance(d, dict) else d
        assert isinstance(items, list) and len(items) >= max(1, n - 1)
        for it in items:
            assert it.get("title") and len(it.get("text") or "") > 40
        return items
    return check


# ---------------------------------------------------------------- newsletters
def newsletter_jobs():
    jobs = []
    for nl in B.NEWSLETTERS:
        f = nl["freq"]
        if nl["name"] in ("云帆内刊", "华夏物流科技联盟"):
            if f == "biweekly":
                dates = [d for d in WORKDAYS if d.weekday() == 3][::2]
            else:
                dates = [d for d in WORKDAYS if d.day <= 7 and d.weekday() == 1]
            k = 4
        elif f == "daily":
            dates = [d for d in WORKDAYS]
            k = 3
        else:
            wd = _seed(nl["name"]) % 5
            dates = [d for d in days() if d.weekday() == wd]
            k = 4
        for d in dates:
            jobs.append({"nl": nl["name"], "topic": nl["topic"], "date": d.isoformat(), "k": k})
    # appended later (keeps earlier job indices/seeds stable): weekend editions of the dailies
    for nl in B.NEWSLETTERS:
        if nl["freq"] == "daily":
            for d in days():
                if d not in set(WORKDAYS):
                    jobs.append({"nl": nl["name"], "topic": nl["topic"], "date": d.isoformat(), "k": 3})
    return jobs


def newsletter_prompt(j):
    d = dt.date.fromisoformat(j["date"])
    extra = ""
    if j["nl"] == "云帆内刊":
        extra = ("这是云帆智能科技有限公司（北京，做智能调度与数据智能解决方案）的内部刊物，栏目可包括：公司要闻、项目喜报、技术分享、员工风采、制度速递、活动预告；"
                 "可提及公司真实存在的同事：韩振东（总裁）、赵一鸣（CTO）、林舟（解决方案部总监）、杜明远（交付总监）、白露（市场总监）等，以及客户华川制造、青禾医疗、北辰能源、远洋港务等。")
    if j["nl"] == "智聘网职位推荐":
        extra = "这是招聘平台给一位解决方案总监推送的职位推荐，每条是一个虚构公司的职位（职位名、公司、城市、年薪范围、要求）。"
    if j["nl"] == "亲子成长周刊":
        extra = "面向小学家长。"
    u = (f"你是电子刊物《{j['nl']}》的编辑，刊物主题：{j['topic']}。请为{d.year}年{d.month}月{d.day}日这一期写{j['k']}条内容，"
         f"每条有标题和120-320字正文，符合当时（{d.year}年{d.month}月）的时间背景。{extra}{STYLE}\n"
         '格式：{"items":[{"title":"标题","text":"正文"}]}')
    return [{"role": "user", "content": u}]


# ---------------------------------------------------------------- promotions
PROMO_SENDERS = [
    ("云购商城", "电商平台的促销：大促、品类日、优惠券、会员日、限时秒杀、以旧换新", 160),
    ("饭点外卖", "外卖平台的红包、会员、新店推荐、天气关怀券", 90),
    ("蓝鲸航空", "航空公司特价机票、里程活动、会员升级、新航线、行李优惠", 70),
    ("悦庭酒店", "酒店会员促销、周末度假套餐、积分兑换、新店开业", 50),
    ("星辰银行", "信用卡分期、积分商城、境外返现、理财产品推荐、刷卡有礼", 80),
    ("海川银行", "信用卡账单分期、加油优惠、商户折扣", 40),
    ("书香书城", "图书促销、新书推荐、读书节满减、儿童绘本专场", 50),
    ("启明星少儿编程", "少儿编程课程、试听课、寒暑假集训营、比赛报名", 45),
    ("乐思英语", "少儿英语课程、外教试听、分级阅读、测评活动", 45),
    ("动力健身", "健身房会员续费、私教体验、团课、体测活动", 35),
    ("远途汽车", "汽车4S店保养套餐、冬季检测、置换补贴、新车试驾", 30),
    ("安康体检中心", "体检套餐促销（父母体检、肿瘤筛查、深度体检）、健康讲座", 25),
    ("康宁人寿", "保险产品推荐、健康险升级、保单服务提醒、客户活动", 30),
    ("畅联通信", "宽带升级、5G套餐、家庭融合套餐、流量包", 35),
    ("青柚视频", "会员折扣、新剧上线、亲子频道、年卡促销", 40),
    ("速达出行", "打车券、会员活动、机场接送优惠", 30),
    ("星河数据", "B2B营销：数据产品发布会、行业白皮书下载、线上研讨会邀请", 25),
    ("天枢云", "B2B营销：云产品新功能、技术大会、资源包促销、迁云补贴", 35),
    ("智图科技", "B2B营销：地图与位置服务新品、合作伙伴大会", 15),
]


def promo_jobs():
    jobs = []
    for name, desc, n in PROMO_SENDERS:
        for i in range(0, n, 3):
            jobs.append({"sender": name, "desc": desc, "batch": i // 3, "k": 3})
    # second round (appended so earlier seeds stay stable)
    for name, desc, n in PROMO_SENDERS:
        for i in range(n, 2 * n, 3):
            jobs.append({"sender": name, "desc": desc, "batch": i // 3, "k": 3})
    return jobs


def promo_prompt(j):
    u = (f"你是「{j['sender']}」的营销邮件文案。请写{j['k']}封互不相同的营销邮件（{j['desc']}），每封有主题和正文（200-700字，"
         "包含具体的活动名称、时间、价格/折扣、使用规则、退订说明），收件人是一位住在北京的42岁男性会员林先生。"
         f"不同批次请写不同季节/节日的活动（本批编号{j['batch']}）。{STYLE}\n"
         '格式：{"items":[{"title":"邮件主题","text":"正文","season":"适用月份如11或all"}]}')
    return [{"role": "user", "content": u}]


# ---------------------------------------------------------------- school notices (non-needle months)
def school_jobs():
    jobs = []
    m = dt.date(2024, 1, 1)
    while m <= dt.date(2026, 10, 1):
        if m.month not in (8,) and not (m.month == 2 and False):
            jobs.append({"month": m.isoformat(), "grade": grade_at(m + dt.timedelta(days=14)), "k": 6 if m.month not in (1, 7, 2) else 3})
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return jobs


def school_prompt(j):
    d = dt.date.fromisoformat(j["month"])
    u = (f"你是北京育才实验小学{j['grade']}3班的班主任何静，通过「家校通」给家长发通知。请写{d.year}年{d.month}月的{j['k']}条班级/学校通知，"
         "类型可包括：作业与阅读打卡、考试安排、活动通知（运动会、艺术节、科技节、春游、社会实践）、缴费、安全提醒、放假通知、家委会事务、表扬与班级动态、"
         "健康与卫生、课后服务报名、意见征集等；有的需要家长回执或缴费（写明截止日期和金额），有的只是告知。"
         f"内容要符合{j['grade']}学生和{d.month}月的校历（寒暑假期间只写1-3条假期相关通知）。{STYLE}\n"
         '格式：{"items":[{"day":日期数字,"title":"通知标题","text":"通知正文（100-400字，以“各位家长：”开头）","action":"需要家长做的事或无"}]}')
    return [{"role": "user", "content": u}]


# ---------------------------------------------------------------- weekly reports (team -> 林舟)
TEAM = ["gucheng", "fangyu", "yezihan", "qinchuan", "tangyue", "songjia", "qiuyang", "baiyang"]


def weekly_jobs():
    jobs = []
    for k in TEAM:
        m = dt.date(2024, 1, 1)
        while m <= END:
            fridays = [d for d in WORKDAYS if d.year == m.year and d.month == m.month and d.weekday() == 4]
            if fridays:
                jobs.append({"who": k, "month": m.isoformat(), "fridays": [d.isoformat() for d in fridays]})
            m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return jobs


def weekly_prompt(j):
    p = PEOPLE[j["who"]]
    d = dt.date.fromisoformat(j["month"])
    projs = []
    for pr in B.PROJECTS:
        if pr["name"] in NEEDLE_GUARD or not pr.get("org"):
            continue
        s, e = dt.date.fromisoformat(pr["start"]), dt.date.fromisoformat(pr["end"])
        if s - dt.timedelta(days=60) <= d <= e + dt.timedelta(days=30):
            projs.append(pr["name"])
    rng = random.Random(_seed(j["who"], j["month"]))
    prospects = rng.sample(["华信物流", "通达冷链", "江海港口集团", "鼎盛精工", "康泰医院集团", "云岭电力", "城投交通", "百川食品", "海纳零售", "锦程快运", "安澜水务", "长风航运"], 3)
    u = (f"你是云帆智能科技解决方案部的{p['name']}（{p['title']}），每周五给部门总监林舟发工作周报邮件。请写{d.year}年{d.month}月的{len(j['fridays'])}份周报，"
         f"对应这几个周五：{'、'.join(j['fridays'])}。可涉及的项目：{'、'.join(projs) or '（无交付项目，以售前为主）'}；可涉及的售前商机：{'、'.join(prospects)}。"
         "每份周报包含：本周完成、下周计划、需要支持/风险，条目具体（客户名、方案名、日期、数字），每份150-400字，周与周之间有连续性，语气是下属对上级。"
         "不要出现真实品牌。只输出JSON，不要代码块标记。\n"
         '格式：{"items":[{"date":"YYYY-MM-DD","title":"邮件主题","text":"周报正文"}]}')
    return [{"role": "user", "content": u}]


KINDS = {
    "newsletters": (newsletter_jobs, newsletter_prompt, CHEAP, lambda j: j["k"], 2500),
    "promos": (promo_jobs, promo_prompt, CHEAP, lambda j: j["k"], 4000),
    "school": (school_jobs, school_prompt, FLASH, lambda j: j["k"], 4000),
    "weekly": (weekly_jobs, weekly_prompt, FLASH, lambda j: len(j["fridays"]), 4000),
}


async def run_kind(name, llm):
    jobs_fn, prompt_fn, model, k_fn, max_tokens = KINDS[name]
    jobs = jobs_fn()
    out = {}

    async def one(i, j):
        check = _items_ok(k_fn(j))
        txt = await llm.chat(prompt_fn(j), model=model, temperature=0.95, max_tokens=max_tokens, seed=_seed(name, i, json.dumps(j, ensure_ascii=False)),
                             tag=f"misc:{name}", validate=check, attempts=3)
        if txt:
            out[i] = {**j, "items": check(txt)}

    await asyncio.gather(*(one(i, j) for i, j in enumerate(jobs)))
    path = GEN / f"{name}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for i in sorted(out):
            fh.write(json.dumps(out[i], ensure_ascii=False) + "\n")
    print(f"[misc] {name}: {len(out)}/{len(jobs)} cost=${llm.cost:.3f}", flush=True)


async def main(kinds):
    GEN.mkdir(parents=True, exist_ok=True)
    async with LLM(concurrency=72, timeout=150, log_every=200) as llm:
        await asyncio.gather(*(run_kind(k, llm) for k in kinds))


if __name__ == "__main__":
    ks = sys.argv[1:] or ["school", "weekly", "promos", "newsletters"]
    if ks == ["all"]:
        ks = ["school", "weekly", "promos", "newsletters"]
    asyncio.run(main(ks))
