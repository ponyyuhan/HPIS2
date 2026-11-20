"""A synthetic Chinese project mailbox and its evidence-only question reader.

The ordinary and encrypted paths share this module. The query planner selects
search labels; the reader derives the answer only from material returned by the
selected path, including actual attachment text.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import datetime, timedelta

TOTAL = 100_000

PRESETS = [
    {
        "id": "delivery",
        "label": "项目交付",
        "question": "星澜项目的交付目标、上线时间和待解问题是什么？",
        "expectedIds": [1, 2, 3],
    },
    {
        "id": "budget",
        "label": "预算审批",
        "question": "星澜试点批了多少预算，如何分期付款，哪些费用要另行审批？",
        "expectedIds": [5, 6, 7],
    },
    {
        "id": "travel",
        "label": "差旅安排",
        "question": "杭州客户现场走访定在哪两天，住哪里，需要带哪些材料？",
        "expectedIds": [9, 10, 11],
    },
]

# Index tokens name a topic/facet. Values belong to particular messages below.
FACETS = {
    "D1": (
        "delivery",
        "交付目标",
        (
            "交付目标",
            "目标",
            "交付范围",
            "范围",
            "哪些数据",
            "数据源",
            "接入",
            "交付什么",
            "要交付",
            "交付内容",
            "要做什么",
            "驾驶舱",
        ),
    ),
    "D2": (
        "delivery",
        "上线时间",
        (
            "上线",
            "交付时间",
            "交付日期",
            "交付日",
            "交期",
            "截止",
            "什么时候交付",
            "何时交付",
            "哪天交付",
            "交付定在",
            "试点时间",
            "排期",
            "切换时间",
        ),
    ),
    "D3": (
        "delivery",
        "待解问题",
        (
            "待解",
            "未决",
            "遗留",
            "未解决",
            "风险",
            "受阻",
            "阻塞",
            "卡点",
            "开放问题",
            "依赖",
            "白名单",
            "映射",
            "未关闭",
            "未定",
            "跟进",
            "待确认",
            "隐患",
        ),
    ),
    "D4": (
        "delivery",
        "验收口径",
        (
            "验收标准",
            "验收条件",
            "验收口径",
            "如何验收",
            "怎么验收",
            "怎么签收",
            "谁来验收",
        ),
    ),
    "D5": ("delivery", "值守安排", ("值守", "值班", "回退")),
    "B1": (
        "budget",
        "批准金额",
        (
            "多少钱",
            "多少预算",
            "预算总额",
            "预算多少",
            "预算是多少",
            "经费多少",
            "批了多少",
            "获批多少",
            "金额",
            "预算上限",
            "预算额度",
            "批复额度",
            "批准额度",
            "总价",
            "含税",
            "报价",
            "总额",
            "额度",
        ),
    ),
    "B2": (
        "budget",
        "付款安排",
        (
            "分期",
            "付款",
            "首款",
            "尾款",
            "首付",
            "支付",
            "账期",
            "结算节点",
            "付款节点",
            "付几次",
        ),
    ),
    "B3": (
        "budget",
        "审批边界",
        (
            "另行",
            "单独",
            "哪些费用",
            "超支",
            "追加",
            "另批",
            "报销",
            "超预算",
            "审批边界",
            "预算外",
            "超出",
            "费用边界",
        ),
    ),
    "T1": (
        "travel",
        "到访行程",
        (
            "哪两天",
            "差旅时间",
            "出差时间",
            "到访时间",
            "出行日期",
            "到访日期",
            "走访日期",
            "行程",
            "几点集合",
            "集合地点",
            "会面地点",
            "在哪里见",
            "哪天出发",
            "几点出发",
            "什么时候到",
            "到访地点",
            "现场日期",
            "哪个园区",
        ),
    ),
    "T2": (
        "travel",
        "住宿安排",
        ("住哪", "住在", "住宿", "住几晚", "酒店", "入住", "订房", "退房"),
    ),
    "T3": (
        "travel",
        "入场材料",
        (
            "带哪些",
            "带什么",
            "携带",
            "访客",
            "材料",
            "入场",
            "准备什么",
            "预约码",
            "门禁",
            "怎么登记",
        ),
    ),
}
TOPICS = {
    "delivery": ("星澜试点交付", ("D1", "D2", "D3")),
    "budget": ("星澜专项预算", ("B1", "B2", "B3")),
    "travel": ("杭州客户走访", ("T1", "T2", "T3")),
}
HISTORIC = {"D2": "HD2", "B1": "HB1", "T1": "HT1"}
FACET_BY_LABEL = {label: key for key, (_, label, _) in FACETS.items()}
HISTORY_WORDS = (
    "原定",
    "以前",
    "最初",
    "原来",
    "原先",
    "初版",
    "讨论稿",
    "草案",
    "早期",
    "旧版",
    "调整前",
)
PRINCIPALS = {
    "林芷": ("lin.zhi", "项目经理"),
    "周衡": ("zhou.heng", "交付负责人"),
    "顾闻": ("gu.wen", "集成工程师"),
    "宋予": ("song.yu", "财务经理"),
    "陈澈": ("chen.che", "采购负责人"),
    "陆遥": ("lu.yao", "客户经理"),
    "方宁": ("fang.ning", "行政协调"),
    "许澄": ("xu.cheng", "客户运营"),
    "韩舟": ("han.zhou", "设计师"),
    "沈禾": ("shen.he", "运营专员"),
    "苏言": ("su.yan", "供应协调"),
    "罗新": ("luo.xin", "客户研究员"),
}
TEAM = tuple(PRINCIPALS)
TEXT_TYPES = {"md": "text/markdown", "csv": "text/csv", "txt": "text/plain"}


def _attachment(name, content):
    return {
        "name": name,
        "mime": TEXT_TYPES[name.rsplit(".", 1)[-1]],
        "content": content,
    }


def _message(
    rid,
    subject,
    sender,
    date,
    time,
    recipients,
    body,
    attachments,
    project,
    category,
    status,
    fields,
    keywords,
):
    handle, role = PRINCIPALS[sender]
    return {
        "id": rid,
        "subject": subject,
        "sender": sender,
        "role": role,
        "email": handle + "@qiyue.example",
        "date": date,
        "time": time,
        "recipients": recipients,
        "body": body,
        "attachments": attachments,
        "project": project,
        "category": category,
        "status": status,
        "fields": fields,
        "keywords": keywords,
    }


def _case(
    rid, topic, subject, sender, stamp, body, attachments, keywords, *, draft=False
):
    date, time = stamp.split()
    status = "讨论稿" if draft else "已确认"
    return _message(
        rid,
        subject,
        sender,
        date,
        time,
        ["项目办公室", "星澜试点组"],
        body,
        attachments,
        "星澜科技",
        TOPICS[topic][0],
        status,
        {"项目": "星澜科技", "专题": topic, "发件人": sender, "编号": f"XL-{rid:06d}"},
        keywords,
    )


CASES = [
    _case(
        1,
        "delivery",
        "星澜一期试点交付范围确认",
        "林芷",
        "2026-11-04 10:18",
        "各位好，\n\n今天的双周会上，客户运营组确认了首批试点范围。请数据组按附件准备样本，本轮验收只覆盖已签字的三个来源。移动端看板留待二期评估。\n\n采购、库存两条链路的回访安排我会另发。\n\n林芷",
        [
            _attachment(
                "一期交付范围.md",
                "# 星澜科技 · 一期交付范围\n\n版本 V1.2 · 2026 年 11 月 4 日双方确认\n\n## 交付目标\n接入采购、库存和订单三类数据，交付统一的运营驾驶舱。\n\n## 验收口径\n客户运营组抽样核对三类数据，并签收试点验收单。\n\n## 范围备注\n移动端视图和供应商专项看板列入二期候选。",
            )
        ],
        ["D1", "D4"],
    ),
    _case(
        2,
        "delivery",
        "星澜试点上线窗口最终确认",
        "周衡",
        "2026-11-13 16:40",
        "项目组好，\n\n变更委员会已批准本次试点的上线窗口。附件是双方会签后的排期，替代十月讨论稿。请各组按表预留值守人员。\n\n如需调整，请在周会前发起变更申请。\n\n周衡",
        [
            _attachment(
                "试点上线排期.csv",
                "事项,说明,负责人\n上线时间,2026 年 12 月 15 日 20:00（北京时间）启动试点上线。,周衡\n值守安排,上线当日 18:00 由交付组完成切换前检查并开启值守。,周衡\n",
            )
        ],
        ["D2", "D5"],
    ),
    _case(
        3,
        "delivery",
        "星澜接口联调仍待关闭的两项依赖",
        "顾闻",
        "2026-11-17 09:12",
        "周衡、林芷好，\n\n本周联调记录已同步。新版库存接口昨天通过连通性检查，剩下两项需要客户侧配合。\n\n待解问题\n历史订单的状态字段映射待客户数据组书面确认，生产接口白名单待客户运维开放。\n\n我会在周五联调会上逐项核对。\n\n顾闻",
        [],
        ["D3"],
    ),
    _case(
        4,
        "delivery",
        "星澜上线窗口初版讨论稿",
        "周衡",
        "2026-10-20 14:32",
        "供周会讨论，以下日期尚待变更委员会批准。\n\n上线时间\n十月讨论稿暂定 12 月 8 日启动试点上线，需等待双方会签。\n\n周衡",
        [],
        ["HD2"],
        draft=True,
    ),
    _case(
        5,
        "budget",
        "星澜试点专项预算正式批复",
        "宋予",
        "2026-11-10 11:06",
        "林芷好，\n\n财务已完成专项审议，请采购组按本次批复发起合同流程。\n\n批准金额\n本次批准的含税预算总额为 36 万元，覆盖试点开发、部署和两个月的支持服务。\n\n金额调整请引用本封批复重新提交。\n\n宋予",
        [],
        ["B1"],
    ),
    _case(
        6,
        "budget",
        "星澜采购合同付款节点会签完成",
        "陈澈",
        "2026-11-12 15:21",
        "财务和交付组好，\n\n合同付款条款已会签，三个节点见附件。每次请款均需附相应的签署或验收文件，请保留原件。\n\n陈澈",
        [
            _attachment(
                "付款里程碑.csv",
                "事项,节点,说明\n付款安排,首款,合同签署后支付 30%。\n付款安排,试点款,试点上线验收后支付 50%。\n付款安排,尾款,终验签字后支付 20%。\n",
            )
        ],
        ["B2"],
    ),
    _case(
        7,
        "budget",
        "星澜专项的费用归属及追加审批",
        "宋予",
        "2026-11-16 10:45",
        "各位好，\n\n为避免在月底重复归集，项目和行政已确认费用归属。\n\n审批边界\n现场差旅费用单独走差旅成本中心，超出专项预算的采购须重新提交审批。\n\n请在费用申请里注明成本中心。\n\n宋予",
        [],
        ["B3"],
    ),
    _case(
        8,
        "budget",
        "星澜预算测算草案",
        "陈澈",
        "2026-10-07 10:36",
        "财务组好，以下为采购摸底时的估算，尚未经财务会签。\n\n批准金额\n采购初稿估算为 42 万元，供内部讨论。\n\n陈澈",
        [],
        ["HB1"],
        draft=True,
    ),
    _case(
        9,
        "travel",
        "杭州客户走访行程确认",
        "陆遥",
        "2026-11-16 17:05",
        "项目组好，\n\n客户已确认本次现场访问的两天安排。请按会面地点集合，乘车方式由各位自行选择。\n\n到访行程\n11 月 26 日 09:30 在杭州滨江园区一楼集合，11 月 27 日 16:00 结束现场走访。\n\n园区内的会议室由客户统一安排。\n\n陆遥",
        [],
        ["T1"],
    ),
    _case(
        10,
        "travel",
        "杭州走访住宿预订已确认",
        "方宁",
        "2026-11-17 15:26",
        "陆遥好，\n\n项目组房间已确认，请通知同行同事凭姓名到前台办理。订单摘要见附件。\n\n方宁",
        [
            _attachment(
                "住宿确认单.txt",
                "项目组住宿确认单\n2026 年 11 月 17 日\n\n住宿安排\n11 月 26 日入住滨江云栖酒店一晚，单间含早餐，由项目组统一结算。\n\n办理入住时请向前台提供登记姓名。",
            )
        ],
        ["T2"],
    ),
    _case(
        11,
        "travel",
        "杭州滨江园区访客入场提醒",
        "许澄",
        "2026-11-18 09:43",
        "各位来访同事好，\n\n星澜运营组已为本次走访发起园区预约。请在出发前核对预约信息。\n\n入场材料\n携带访客预约码和盖章的试点验收清单，提前 15 分钟到一楼前台登记。\n\n园区接待同事会在登记后领大家到会议室。\n\n许澄",
        [],
        ["T3"],
    ),
    _case(
        12,
        "travel",
        "杭州走访日期征询",
        "陆遥",
        "2026-10-23 10:15",
        "需向客户确认后才能订房，先征询大家的档期。\n\n到访行程\n最初征询的备选日期为 11 月 19 至 20 日，客户当时尚未确认。\n\n陆遥",
        [],
        ["HT1"],
        draft=True,
    ),
]

# A few recurring threads, contributors, locations and dispatch types create a
# reproducible background mailbox. Each message carries its own ticket and detail.
PROJECTS = (
    "云帆",
    "澄野",
    "青简",
    "远岑",
    "行舟",
    "南屿",
    "望舒",
    "长桥",
    "和光",
    "海岚",
)
PLACES = ("上海", "苏州", "成都", "广州", "合肥", "西安", "厦门", "武汉")
DISPATCHES = (
    (
        "设计评审",
        "交互稿评审记录",
        "本轮已核对{qty}处交互说明，待设计组确认移动端的字号和对齐方式。",
        "评审清单",
        "交互说明,待核对",
    ),
    (
        "到货清点",
        "样品到货回执",
        "仓储组已签收{qty}件样品，外包装完整。请核对样品编号后到前台领取。",
        "到货明细",
        "签收,待领取",
    ),
    (
        "回访记录",
        "客户回访纪要",
        "本次回访记录了{qty}条使用反馈，客户希望下次访谈重点讨论筛选和导出体验。",
        "回访摘要",
        "使用反馈,待回访",
    ),
    (
        "往来对账",
        "月度对账回函",
        "已核对本批{qty}笔往来记录，供应方将补发签收单。收齐后请财务归档。",
        "对账明细",
        "签收凭据,待归档",
    ),
    (
        "评测反馈",
        "可用性评测归档",
        "本轮走查完成{qty}个页面的操作记录。请体验组在例会上确认下一轮参与名单。",
        "评测条目",
        "页面走查,已记录",
    ),
    (
        "资料校订",
        "手册校订稿会签",
        "编辑组已完成{qty}处术语校订。请业务代表在共享稿里确认示例和截图顺序。",
        "校订记录",
        "术语校订,待会签",
    ),
    (
        "会议跟进",
        "小组例会行动记录",
        "会上登记了{qty}项跟进动作。请各组将更新写入共享会议记录，周会集中核对。",
        "行动清单",
        "跟进动作,待更新",
    ),
    (
        "样件复核",
        "样件复核情况同步",
        "检验组抽查了{qty}个样件，复核单已经归档。后续批次将沿用这份清单。",
        "复核条目",
        "抽样检查,已归档",
    ),
)
_END = datetime(2026, 11, 19, 17, 45)


def _archive_mail(rid):
    p = (rid * 13 + rid // 17) % len(PROJECTS)
    t = (rid * 11 + rid // 29) % len(DISPATCHES)
    city = PLACES[(rid * 7 + rid // 11) % len(PLACES)]
    project = PROJECTS[p]
    subject_type, headline, detail, file_title, summary = DISPATCHES[t]
    sender = TEAM[(rid * 17 + rid // 37) % len(TEAM)]
    recipient = TEAM[(TEAM.index(sender) + 1 + rid % 10) % len(TEAM)]
    stamp = _END - timedelta(minutes=(rid * 2717) % 2_360_000)
    ticket = f"FY-{rid:06d}"
    quantity = 2 + (rid * 19 + rid // 31) % 26
    subject = f"{project} · {headline}（{ticket}）"
    body = (
        f"{recipient}好，\n\n{city}组提交的{project}{subject_type}事项已更新。"
        f"{detail.format(qty=quantity)}\n\n后续回信请保留跟踪编号 {ticket}。\n\n{sender}"
    )
    files = []
    if rid % 9 == 0:
        files = [
            _attachment(
                f"{project}{file_title}.csv",
                f"跟踪编号,地点,事项,数量,当前进度\n{ticket},{city},{summary.split(',')[0]},{quantity},{summary.split(',')[1]}\n",
            )
        ]
    return _message(
        rid,
        subject,
        sender,
        stamp.date().isoformat(),
        stamp.strftime("%H:%M"),
        [recipient, "项目办公室"],
        body,
        files,
        project,
        subject_type,
        "已归档",
        {"项目": project, "专题": subject_type, "发件人": sender, "编号": ticket},
        [],
    )


def message_for_id(rid):
    """Return one original mailbox record, including its indexing metadata."""
    if type(rid) is not int or not 1 <= rid <= TOTAL:
        raise KeyError(rid)
    if rid <= len(CASES):
        import copy

        return copy.deepcopy(CASES[rid - 1])
    return _archive_mail(rid)


def iter_messages(total=TOTAL):
    if not 0 <= total <= TOTAL:
        raise ValueError("mailbox size is outside the available range")
    for rid in range(1, total + 1):
        yield message_for_id(rid)


def message_content(message):
    """The complete visible email, with the same bytes available to both paths."""
    return {k: v for k, v in message.items() if k not in ("fields", "keywords")}


def _norm(value):
    return unicodedata.normalize("NFKC", value).casefold()


def _ticket(question):
    result = re.search(
        r"(?<![a-z])(?:fy|xl)[\s－-]?(\d{1,6})(?!\d)", question, re.IGNORECASE
    )
    if not result:
        return None
    kind = _norm(result.group(0))[:2].upper()
    return f"{kind}-{int(result.group(1)):06d}"


def _source_plan(fields):
    return {
        "intent": "read",
        "predicates": [["field", name, value] for name, value in fields],
        "combine": "all",
        "facets": [],
        "historical": False,
        "topic": "archive",
    }


def _unsupported():
    return {
        "intent": "none",
        "predicates": [["word", "无对应演示证据"]],
        "combine": "all",
        "facets": [],
        "historical": False,
        "topic": "none",
    }


def _summary_request(text, domain):
    terms = {
        "delivery": (
            "星澜",
            "星澜科技",
            "星澜项目",
            "星澜试点",
            "项目交付",
            "客户a",
            "项目计划",
            "项目进度",
            "项目进展",
        ),
        "budget": (
            "预算",
            "预算审批",
            "项目预算",
            "星澜预算",
            "星澜试点预算",
            "经费",
            "预算批复",
        ),
        "travel": (
            "差旅",
            "差旅安排",
            "杭州出差",
            "杭州走访",
            "客户走访",
            "出差",
            "现场走访",
            "出差安排",
        ),
    }[domain]
    if (
        domain == "delivery"
        and ("项目" in text or "客户a" in text)
        and any(
            word in text
            for word in ("怎么样", "进展", "进度", "概况", "概括", "总结", "目前情况")
        )
    ):
        return True
    return text in terms or (
        any(t in text for t in terms)
        and any(
            t in text
            for t in (
                "总结",
                "概括",
                "汇总",
                "情况",
                "说说",
                "进展",
                "进度",
                "有哪些安排",
                "相关邮件",
            )
        )
    )


def _unrelated_entity(text):
    """A named foreign project must not borrow the showcase project's facts."""
    if any(
        term in text
        for term in (
            "星澜",
            "客户a",
            "本项目",
            "该项目",
            "我们项目",
            "我们的项目",
            "这个项目",
            "这个客户",
            "这个试点",
        )
    ):
        return False
    # Broad prompts may omit a project name ("项目什么时候上线"). A name just
    # before an explicit project/company noun identifies a different context.
    for prefix in re.findall(
        r"([a-z0-9\u4e00-\u9fff]{1,12})(?:项目|科技|集团|公司|计划)", text
    ):
        for filler in (
            "帮我看看",
            "帮我查一下",
            "请帮我",
            "请问",
            "查询",
            "关于",
            "我想了解",
            "你知道",
            "可以给我",
            "讲一下",
            "请查",
            "看一下",
            "帮我查",
            "总结",
            "介绍",
            "这个",
            "那个",
            "我们的",
            "当前",
            "本次",
            "这次",
            "最初",
            "原先",
            "之前",
            "该",
            "本",
            "新",
            "现有",
        ):
            prefix = prefix.replace(filler, "")
        if prefix in {
            "项目",
            "试点",
            "交付",
            "上线",
            "采购",
            "付款",
            "差旅",
            "走访",
            "工作",
            "客户",
        }:
            continue
        if len(prefix) >= 2 and not prefix.endswith(
            (
                "一下",
                "哪些",
                "所有",
                "过的",
                "相关的",
                "这项",
                "那项",
                "这个",
                "那个",
                "与",
                "就",
                "与客户",
                "邮件中",
            )
        ):
            return True
    return bool(re.search(r"客户[b-z](?![a-z])", text))


def _travel_context_unknown(text):
    if not any(
        word in text
        for word in ("走访", "差旅", "到访", "出差", "酒店", "住宿", "入场")
    ):
        return False
    if any(
        city in text
        for city in (
            *PLACES,
            "北京",
            "深圳",
            "南京",
            "天津",
            "香港",
            "巴黎",
            "伦敦",
            "东京",
            "纽约",
        )
    ):
        return "杭州" not in text
    # The available receipt confirms one night and payer; no per-night price or
    # reservation code is present in the underlying emails.
    return any(
        word in text for word in ("酒店", "住宿", "房费", "出差", "差旅", "走访费用")
    ) and any(
        word in text
        for word in (
            "多少钱",
            "价格",
            "单价",
            "预订号",
            "订单号",
            "预算多少",
            "多少预算",
            "花费多少",
        )
    )


def plan_query(question):
    """Translate user wording into a union of facet labels or archive predicates.

    No answer, result row id, or sender address is embedded in a search target.
    """
    if not isinstance(question, str) or not question.strip():
        return _unsupported()
    q = _norm(question)
    compact = re.sub(r"[\s！？?。，,.、！!]", "", q)
    ticket = _ticket(q)
    if ticket:
        return _source_plan([("编号", ticket)])
    generic_projects = [p for p in PROJECTS if p in q]
    generic_topics = [d[0] for d in DISPATCHES if d[0] in q]
    senders = [name for name in TEAM if name in q]

    # A typed reference or named archive project takes priority over a coincidental
    # business word such as "付款" in an unrelated thread.
    if generic_projects or generic_topics:
        fields = [("项目", p) for p in generic_projects] + [
            ("专题", t) for t in generic_topics
        ]
        fields += [("发件人", name) for name in senders]
        return _source_plan(fields)

    # All case correspondence is from autumn 2026. Named contexts absent from
    # this mailbox do not qualify for the positive topic-based search.
    if _unrelated_entity(compact) or _travel_context_unknown(compact):
        return _unsupported()
    years = set(re.findall(r"(?<![0-9])(20[0-9]{2})\s*年", q))
    if years and years != {"2026"}:
        return _unsupported()
    topic_hints = {
        "delivery": any(
            t in q
            for t in ("星澜", "项目", "试点", "交付", "客户a", "客户 a", "纪要", "上线")
        ),
        "budget": any(
            t in q for t in ("预算", "经费", "采购合同", "款", "财务", "专项")
        ),
        "travel": any(
            t in q
            for t in ("杭州", "出差", "差旅", "走访", "住宿", "来访", "到访", "访客")
        ),
    }
    facets = [
        key
        for key, (_, _, aliases) in FACETS.items()
        if any(word in q for word in aliases)
    ]
    if topic_hints["budget"] and not any(key.startswith("B") for key in facets):
        if any(t in q for t in ("多少", "批了", "批复了", "多少钱", "获批", "获准")):
            facets.append("B1")
    if topic_hints["travel"] and not any(key.startswith("T") for key in facets):
        if any(
            t in q
            for t in (
                "何时",
                "什么时候",
                "哪天",
                "几号",
                "几点",
                "定于",
                "在哪",
                "日期",
                "时间",
            )
        ):
            facets.append("T1")
    if not facets:
        for domain in ("budget", "travel", "delivery"):
            if topic_hints[domain] and _summary_request(compact, domain):
                facets.extend(TOPICS[domain][1])
                break
    if not facets:
        if senders and any(
            word in q for word in ("邮件", "来信", "发来", "发过", "写过", "发的")
        ):
            return _source_plan([("发件人", name) for name in senders])
        return _unsupported()

    historical = any(word in q for word in HISTORY_WORDS)
    compare_versions = historical and any(
        word in q for word in ("最终", "最后", "现在", "对比", "分别", "调整后")
    )
    if historical and not compare_versions:
        facets = [key for key in facets if key in HISTORIC]
        if not facets:
            return _unsupported()
    target_labels = []
    for key in facets:
        if historical and key in HISTORIC:
            target_labels.append(HISTORIC[key])
        if not historical or compare_versions:
            target_labels.append(key)
    domains = {FACETS[key][0] for key in facets}
    return {
        "intent": "facts",
        "predicates": [["word", key] for key in target_labels],
        "combine": "any",
        "facets": facets,
        "historical": historical,
        "compareVersions": compare_versions,
        "topic": next(iter(domains)) if len(domains) == 1 else "mixed",
    }


def matches_plan(message, plan):
    fields = message["fields"]
    words = message["keywords"]

    def match(part):
        if part[0] == "field":
            return part[1] in fields and _norm(fields[part[1]]) == _norm(part[2])
        return part[0] == "word" and any(_norm(w) == _norm(part[1]) for w in words)

    operations = (match(part) for part in plan["predicates"])
    return all(operations) if plan["combine"] == "all" else any(operations)


def _paragraphs(text, label):
    """Read a labelled subsection of an email, Markdown document, or text attachment."""
    lines = text.splitlines()
    for pos, line in enumerate(lines):
        if line.strip().lstrip("#").strip() == label:
            selected = []
            for following in lines[pos + 1 :]:
                value = following.strip()
                if not value:
                    if selected:
                        break
                    continue
                if value.startswith("#") or value in FACET_BY_LABEL:
                    break
                selected.append(value)
            yield from selected


def _evidence(message, label):
    for value in _paragraphs(message["body"], label):
        yield {"id": message["id"], "text": value, "attachment": None}
    for att in message["attachments"]:
        if att["mime"] == "text/csv":
            values = (
                r["说明"]
                for r in csv.DictReader(io.StringIO(att["content"]))
                if r.get("事项") == label and r.get("说明")
            )
        else:
            values = _paragraphs(att["content"], label)
        for value in values:
            yield {"id": message["id"], "text": value, "attachment": att["name"]}


def _archive_excerpt(message):
    paragraphs = [p.strip() for p in message["body"].split("\n\n") if p.strip()]
    if len(paragraphs) > 1 and paragraphs[0].endswith(("好，", "好。", "好！")):
        return paragraphs[1]
    return paragraphs[0] if paragraphs else ""


def make_answer(question, messages, plan=None):
    """Extract displayable claims from the returned messages and their attachments."""
    plan = plan or plan_query(question)
    ranked = sorted(
        messages, key=lambda m: (m["date"], m.get("time", ""), m["id"]), reverse=True
    )
    sections = []
    if plan["intent"] == "read":
        for msg in ranked[:3]:
            quote = _archive_excerpt(msg)
            sections.append(
                {
                    "label": msg["subject"],
                    "text": quote,
                    "sourceIds": [msg["id"]],
                    "quotes": [{"id": msg["id"], "text": quote, "attachment": None}],
                }
            )
    if plan["intent"] == "facts":
        for key in plan["facets"]:
            label = FACETS[key][1]
            versions = (
                (True, False) if plan.get("compareVersions") else (plan["historical"],)
            )
            for draft in versions:
                for msg in ranked:
                    if (msg["status"] == "讨论稿") != draft:
                        continue
                    quotes = list(_evidence(msg, label))
                    if quotes:
                        display = label
                        if plan.get("compareVersions"):
                            display = ("早期" if draft else "最终") + label
                        elif draft and key == "B1":
                            display = "早期估算"
                        sections.append(
                            {
                                "label": display,
                                "text": "".join(item["text"] for item in quotes),
                                "sourceIds": [msg["id"]],
                                "quotes": quotes,
                            }
                        )
                        break

    source_ids = list(
        dict.fromkeys(rid for section in sections for rid in section["sourceIds"])
    )
    if sections and plan["intent"] == "read":
        lead = "匹配邮件摘录"
    elif sections:
        lead = TOPICS.get(plan["topic"], ("项目邮件摘录", ()))[0]
        if plan.get("compareVersions"):
            lead += " · 版本对照"
        elif plan["historical"]:
            lead += " · 早期讨论稿"
    else:
        lead = "当前邮箱没有可引用的证据"
    return {
        "lead": lead,
        "sections": sections,
        "sourceIds": source_ids,
        "mode": "extractive",
        "matched": bool(sections),
    }
