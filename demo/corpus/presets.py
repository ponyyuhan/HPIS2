"""The six preset questions with on-device query plans, gold facts and evidence ids.

Written by assemble.py (needs demo/data/gen/nid_map.json and mailbox.jsonl).
"""

from __future__ import annotations

import json
import re

from . import bible as B

DATA = __import__("pathlib").Path(__import__("os").environ.get("CORPUS_OUT") or B.DATA)


def F(label, value, *match):
    return {"label": label, "value": value, "match": list(match)}


PRESETS = [
    {
        "id": "minutes", "chip": "会议总结",
        "question": "帮我找到过去三个月与瀚海物流讨论项目规划的会议纪要，总结最终确定的目标、交付时间和还没解决的问题。",
        "plan": {"orgs": ["瀚海物流"], "people": [], "date_from": "2026-08-20", "date_to": "2026-11-20", "categories": ["client", "work"],
                 "semantic": "瀚海物流 华东智能调度平台 项目规划 会议纪要 目标 交付时间 未解决问题"},
        "facts": [
            F("范围", "一期覆盖上海嘉定、苏州昆山、杭州萧山3个仓（南京江宁、宁波北仑移入二期）", "上海嘉定"),
            F("效率目标", "调度效率提升15%", "调度效率提升15%"),
            F("空驶率目标", "车辆空驶率降低8%", "空驶率降低8%"),
            F("可用性", "系统可用性99.9%", "99.9%"),
            F("交付时间", "2026年12月15日 20:00切换上线（原定12月8日，因约1.2亿条历史运单迁移需多一周而推迟）", "12月15日", "12-15"),
            F("未解决①", "约1.2亿条历史运单数据迁移方案待瀚海IT确认", "1.2亿"),
            F("未解决②", "与瀚海TMS接口权限审批未完成", "TMS接口权限"),
            F("未解决③", "双十二峰值压测环境未就绪", "压测环境"),
        ],
        "evidence": ["q1.final", "q1.sched", "q1.scheme", "q1.kickoff"],
        "distractors": ["q1.plan_v12", "q1.wms", "q1.port", "q1.annual", "q1.quote2", "q1.cost"],
    },
    {
        "id": "checkup", "chip": "体检对比", "scene": "health",
        "question": "对比我2025年和2026年的体检报告，哪些指标变化最明显？医生给了什么建议？",
        "plan": {"orgs": ["安康体检中心"], "people": [], "date_from": "2025-01-01", "date_to": "2026-11-20", "categories": ["health"],
                 "semantic": "林舟 2025年 2026年 年度体检报告 指标变化 低密度脂蛋白 尿酸 空腹血糖 体重 医生建议"},
        "facts": [
            F("LDL-C", "低密度脂蛋白胆固醇 3.12 → 4.26 mmol/L", "4.26"),
            F("尿酸", "尿酸 386 → 468 μmol/L", "468"),
            F("空腹血糖", "空腹血糖 5.4 → 6.3 mmol/L", "6.3"),
            F("体重", "体重 72.5 → 77.8 kg", "77.8"),
            F("饮食建议", "低脂低嘌呤饮食", "低脂低嘌呤"),
            F("运动建议", "每周≥150分钟中等强度运动", "150分钟"),
            F("复查建议", "3个月后复查血脂、尿酸、血糖", "3个月后复查"),
        ],
        "evidence": ["q2.r2026", "q2.r2025", "q2.interp"],
        "distractors": ["q2.r2024", "q2.su2026", "q2.recheck"],
    },
    {
        "id": "card", "chip": "信用卡账单",
        "question": "我上个月的信用卡账单一共多少钱？最大的三笔消费是什么？",
        "plan": {"orgs": [], "people": [], "date_from": "2026-11-01", "date_to": "2026-11-20", "categories": ["finance"],
                 "semantic": "信用卡 10月账单 本期应还金额 交易明细"},
        "facts": [
            F("本期应还", "¥18,642.37（星辰银行信用卡尾号6411，10月账单，账单日11月5日）", "18,642.37"),
            F("还款日", "最后还款日2026年11月25日", "11月25日", "2026/11/25", "2026年11月25日"),
            F("第一大", "10月16日 悦庭酒店（上海） ¥4,280.00", "4,280.00"),
            F("第二大", "10月9日 蓝鲸航空 ¥3,560.00", "3,560.00"),
            F("第三大", "10月22日 云购商城 ¥2,999.00", "2,999.00"),
        ],
        "evidence": ["q3.statement", "q3.remind"],
        "distractors": [],
    },
    {
        "id": "trip", "chip": "出差行程",
        "question": "下周去深圳出差，航班、酒店和会议分别是怎么安排的？",
        "plan": {"orgs": [], "people": [], "date_from": "2026-11-01", "date_to": "2026-11-20", "categories": ["travel", "client"],
                 "semantic": "下周深圳出差 航班 酒店 会议 安排"},
        "facts": [
            F("去程", "JW1305 11月24日 08:30 北京大兴 → 11:55 深圳宝安（原定08:00，11月19日航司通知变更）", "JW1305"),
            F("到达", "11:55 抵达深圳宝安", "11:55"),
            F("改期", "起飞时间由08:00调整为08:30", "08:30"),
            F("返程", "JW1308 11月26日 18:40 深圳宝安 → 北京大兴", "JW1308"),
            F("酒店", "悦庭酒店深圳南山店，11月24日入住、26日退房，确认号YT2611240387", "YT2611240387"),
            F("会议", "11月25日14:00–16:30 瀚海物流华南区运营中心（南山科技园），华南推广方案沟通，华南区总经理陈立新", "陈立新"),
            F("会议时间", "14:00–16:30", "14:00"),
        ],
        "evidence": ["q4.change", "q4.hotel", "q4.meeting"],
        "distractors": ["q4.flight", "q4.aug_flight", "q4.aug_hotel", "q4.oct_flight", "q4.oct_hotel", "q4.futian_cancel", "q4.hn_ask"],
    },
    {
        "id": "school", "chip": "学校通知",
        "question": "孩子学校最近有哪些需要家长处理的事？截止时间分别是什么时候？",
        "plan": {"orgs": ["育才实验小学"], "people": [], "date_from": "2026-10-20", "date_to": "2026-11-20", "categories": ["school"],
                 "semantic": "学校通知 需要家长处理 缴费 签字回传 家长会 截止时间"},
        "facts": [
            F("研学缴费", "冬季研学缴费¥680，11月27日（周五）17:00前", "¥680"),
            F("研学截止", "11月27日", "11月27日"),
            F("疫苗同意书", "流感疫苗接种知情同意书签字回传，11月25日前", "知情同意书"),
            F("同意书截止", "11月25日", "11月25日"),
            F("家长会", "11月28日（周五）19:00 学校报告厅", "11月28日"),
            F("家长会地点", "学校报告厅", "报告厅"),
        ],
        "evidence": ["q5.yanxue", "q5.vaccine", "q5.ptm"],
        "distractors": ["q5.open", "q5.lunch", "q5.autumn", "q5.y2025"],
    },
]

# Extra distractors located by subject (programmatic siblings without a nid).
EXTRA = {
    "card": [r"星辰银行信用卡2026年9月电子账单", r"海川银行信用卡电子对账单（2026年11月）", r"海川银行信用卡电子对账单（2026年10月）",
             r"2026年9月账单还款提醒", r"消费提醒：您尾号6411信用卡消费¥4,280.00", r"消费提醒：您尾号6411信用卡消费¥3,560.00"],
    "checkup": [r"^【安康体检中心】体检预约成功：4月10日", r"^【康宁人寿】保费缴纳成功及保单年度报告（重大疾病保险）"],
    "school": [],
}


def write(nids: dict):
    mailbox = DATA / "mailbox.jsonl"
    subj = {}
    with open(mailbox, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            subj[r["id"]] = (r["subject"], r["category"], r["facets"].get("org"))
    out = []
    for p in PRESETS:
        ev = [nids[n] for n in p["evidence"] if n in nids]
        ds = [nids[n] for n in p["distractors"] if n in nids]
        for pat in EXTRA.get(p["id"], []):
            rx = re.compile(pat)
            ds += [i for i, (s, c, o) in subj.items() if rx.search(s) and i not in ev and i not in ds]
        if p["id"] == "school":
            ads = [i for i, (s, c, o) in subj.items() if o in ("启明星少儿编程", "乐思英语") and ("冬令营" in s or "寒假" in s)]
            if not ads:
                ads = [i for i, (s, c, o) in subj.items() if o in ("启明星少儿编程", "乐思英语")]
            ds += ads[-3:]
        if p["id"] == "checkup":
            ds += [i for i, (s, c, o) in subj.items() if o == "安康体检中心" and c == "promotion"][:3]
        out.append({
            "id": p["id"], "chip": p["chip"], **({"scene": p["scene"]} if p.get("scene") else {}),
            "question": p["question"], "plan": p["plan"],
            "gold": {"facts": p["facts"], "evidence_ids": ev, "evidence_nids": p["evidence"]},
            "distractor_ids": sorted(set(ds)),
        })
    (DATA / "presets.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print("presets:", [(p["id"], p["gold"]["evidence_ids"], len(p["distractor_ids"])) for p in out])
    return out
