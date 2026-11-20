"""The six preset storylines: gold evidence, consistent background mail and distractors.

LLM-written needle emails use the strong model with the exact facts given in the
spec; every spec lists strings that must appear verbatim (validated, retried).
Programmatic needles (statements, flights, hotels, health reports) come from
templates.py so that they look exactly like their non-needle siblings.

    python -m demo.corpus.needles      # generate/refresh demo/data/gen/needles.json
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from . import bible as B
from .gen_threads import SYSTEM
from .llm import LLM, STRONG, parse_json

PEOPLE = B.all_people()
OUT = B.DATA / "gen" / "needles.json"

HX = "瀚海物流"
HXP = "瀚海物流·华东智能调度平台（一期）"
HNP = "瀚海物流·华南推广"
XHP = "星河数据·数据服务采购"


def S(nid, story, role, date, frm, to, cc, subject, category, org, project, brief, must=(), attach=None, ctx="", personal=False,
      sender=None):
    return {
        "nid": nid, "story": story, "role": role, "date": date, "from": frm, "to": list(to), "cc": list(cc),
        "subject": subject, "category": category, "org": org, "project": project, "brief": brief, "must": list(must),
        "attach": attach, "ctx": ctx, "personal": personal, "sender": sender,
    }


HX_CTX = ("项目：瀚海物流·华东智能调度平台（一期），客户瀚海物流集团（上海总部），合同额¥3,280,000，2026年8月中标。"
          "云帆侧：林舟（解决方案部总监，方案总负责）、周衡（高级项目经理）、陈默（首席架构师）、王蕾（大客户总监）、吴迪（运维/SRE）、徐子豪（算法）。"
          "瀚海侧：程远（运营副总裁，项目发起人）、马国栋（信息技术部总经理）、严宁（IT项目经理，日常对接）、吕强（华东区运营总监）、黄振（数据平台负责人）。")

SPECS = [
    # ------------------------------------------------------------------ Q1 项目纪要
    S("q1.award", "q1", "bg", "2026-08-17T10:26", "wanglei", ["linzhou", "zhouheng", "chenmo"], ["guowen", "dumingyuan"],
      "瀚海华东智能调度平台（一期）中标确认及项目组组建", "work", HX, HXP,
      "王蕾通知：瀚海物流华东智能调度平台（一期）已确认由云帆承接，合同额¥3,280,000，合同本周走完盖章；瀚海希望9月初开项目启动会。"
      "建议项目经理周衡、架构陈默，林舟负责整体方案；请周衡先和瀚海IT项目经理严宁约启动会时间。不要提及具体交付日期。",
      ["¥3,280,000"], ctx=HX_CTX),
    S("q1.kick_inv", "q1", "bg", "2026-08-26T15:10", "zhouheng", ["yanning", "chengyuan"], ["linzhou", "wanglei", "chenmo", "maguodong"],
      "【会议邀请】华东智能调度平台（一期）项目启动会｜9月3日 上海", "client", HX, HXP,
      "周衡发出启动会邀请：9月3日（周四）14:00-17:00，瀚海物流上海总部18楼第一会议室；议程：项目背景与目标、范围与里程碑、双方组织与分工、调研安排；请严宁确认瀚海参会人和会议室投屏。",
      ["9月3日"], ctx=HX_CTX),
    S("q1.kickoff", "q1", "gold", "2026-09-03T19:42", "zhouheng", ["chengyuan", "maguodong", "yanning", "lvqiang", "huangzhen"],
      ["linzhou", "wanglei", "chenmo", "wudi"],
      "【会议纪要】瀚海物流·华东智能调度平台（一期）项目启动会（9月3日）", "client", HX, HXP,
      "周衡发出启动会会议纪要（正文简要列出结论，详细内容见附件）。会议确定的初步目标：一期覆盖华东5个仓（上海嘉定、苏州昆山、杭州萧山、南京江宁、宁波北仑），"
      "调度效率提升20%，计划交付时间为2026年12月8日。双方分工：瀚海由程远牵头、严宁日常对接，云帆由周衡负责项目管理、林舟负责方案。"
      "下一步：9月下旬前完成5个仓现场调研；瀚海提供近年历史运单样例与TMS接口文档；每周五发项目周报。风险：历史运单数据量大，TMS接口权限需走瀚海内部审批。",
      ["5个仓", "调度效率提升20%", "12月8日", "南京江宁", "宁波北仑", "上海嘉定"],
      {"name": "华东智能调度平台一期_启动会纪要_20260903.docx", "kind": "doc",
       "brief": "正式的会议纪要文档：会议名称、时间（2026年9月3日 14:00-17:00）、地点、参会人（双方）、会议议题、会议结论（初步目标：5个仓、调度效率提升20%、计划2026年12月8日交付）、双方分工、下一步工作（文本表格：事项|责任人|完成时间）、风险。"},
      ctx=HX_CTX),
    S("q1.cost", "q1", "distractor", "2026-09-10T16:05", "sunhao", ["linzhou", "wanglei"], ["fanzhiqiang"],
      "华东调度一期 成本复核：外包与云资源预算", "work", HX, HXP,
      "财务孙浩复核瀚海华东调度一期的项目成本：合同额¥3,280,000，预计毛利率32%；其中外包数据标注预算¥180,000、压测云资源预算¥96,000、差旅¥120,000；请林舟和王蕾确认，周五前提交经营分析会。",
      ["32%", "¥180,000"], ctx=HX_CTX),
    S("q1.weekly3", "q1", "bg", "2026-09-25T18:05", "zhouheng", ["yanning", "lvqiang"], ["linzhou", "chenmo", "wanglei"],
      "华东智能调度平台（一期）项目周报（第3周｜9.21-9.25）", "client", HX, HXP,
      "周报：本周完成上海嘉定、苏州昆山、杭州萧山三个仓的现场调研；南京江宁、宁波北仑两个仓因仓库旺季推迟到10月上旬调研；"
      "已收到瀚海提供的历史运单样例，全量约1.2亿条（2019年至今）；风险：TMS接口权限申请尚未提交瀚海信息安全部审批；下周计划：调研报告初稿、数据质量评估。不要写交付日期。",
      ["1.2亿", "TMS"], ctx=HX_CTX),
    S("q1.scheme", "q1", "gold", "2026-10-15T20:16", "zhouheng", ["chengyuan", "maguodong", "yanning", "lvqiang"],
      ["linzhou", "wanglei", "chenmo", "xuzihao"],
      "【会议纪要】华东智能调度平台（一期）方案评审会（10月15日）", "client", HX, HXP,
      "10月15日在瀚海上海总部召开方案评审会，林舟汇报整体方案。会议决议：一期范围由5个仓调整为3个仓：上海嘉定、苏州昆山、杭州萧山；南京江宁、宁波北仑纳入二期；"
      "一期目标调整为：调度效率提升15%，车辆空驶率降低8%（以2026年三季度运营数据为基线）；交付时间暂按12月8日，最终排期在10月28日排期评审会上确认。"
      "待办：云帆10月20日前提交调整后的项目计划；陈默评估约1.2亿条历史运单的迁移方案；严宁启动TMS接口权限申请。",
      ["3个仓", "上海嘉定", "苏州昆山", "杭州萧山", "调度效率提升15%", "车辆空驶率降低8%", "二期"],
      {"name": "华东智能调度平台一期_方案评审会纪要_20261015.docx", "kind": "doc",
       "brief": "会议纪要文档：时间、地点、参会人、评审内容摘要、会议决议（范围调整为3个仓、目标为调度效率提升15%和车辆空驶率降低8%、南京江宁与宁波北仑纳入二期、排期待10月28日评审确认）、专家意见、待办事项表格。"},
      ctx=HX_CTX),
    S("q1.quote2", "q1", "distractor", "2026-10-19T14:20", "wanglei", ["linzhou", "sunhao"], ["zhouheng"],
      "南京江宁、宁波北仑二期报价初稿（内部）", "work", HX, HXP,
      "方案评审后南京江宁、宁波北仑两个仓移入二期，王蕾做了二期报价初稿，约¥1,450,000，附人天测算；计划明年一季度和瀚海采购经理罗敏沟通；请林舟看口径。",
      ["¥1,450,000"],
      {"name": "华东调度二期_报价测算_v0.3.xlsx", "kind": "sheet", "brief": "报价测算表：模块|人天|单价|小计，含合计¥1,450,000，以及说明。"},
      ctx=HX_CTX),
    S("q1.plan_v12", "q1", "distractor", "2026-10-20T11:32", "zhouheng", ["linzhou", "chenmo"], ["wudi", "xuzihao"],
      "华东智能调度平台一期 项目计划 v1.2（草稿，请review）", "work", HX, HXP,
      "周衡发出调整范围后的项目计划草稿v1.2供内部review，里程碑仍按12月8日上线：11月13日联调完成、11月24日开始UAT、12月1日峰值压测、12月8日上线；请大家周四前反馈。"
      "（这是一个后来被替代的草稿。）",
      ["12月8日", "v1.2"],
      {"name": "华东调度一期_项目计划_v1.2_草稿.xlsx", "kind": "sheet", "brief": "项目计划表：阶段|任务|负责人|开始|结束|状态，最后一行是12月8日上线。"},
      ctx=HX_CTX),
    S("q1.migr", "q1", "bg", "2026-10-26T22:08", "chenmo", ["zhouheng", "linzhou"], ["gaoyuan", "tianye"],
      "历史运单迁移评估：按现有排期有风险，建议增加一周", "work", HX, HXP,
      "陈默评估：约1.2亿条历史运单需要清洗、迁移并做新旧双轨比对，按v1.2计划时间不够，建议整体交付后延一周；方案：按年份分批迁移+上线前增量同步；需要瀚海IT确认迁移方案和停写窗口。",
      ["1.2亿", "一周"], ctx=HX_CTX),
    S("q1.sched", "q1", "gold", "2026-10-28T18:47", "zhouheng", ["maguodong", "yanning", "lvqiang", "huangzhen"],
      ["chengyuan", "linzhou", "wanglei", "chenmo"],
      "【会议纪要】华东智能调度平台（一期）排期评审会（10月28日）", "client", HX, HXP,
      "排期评审会纪要：经双方确认，一期交付时间由原定的12月8日调整为2026年12月15日，原因是约1.2亿条历史运单数据的迁移与校验需要多一周；"
      "调整后里程碑：11月20日完成系统联调，11月27日开始UAT，12月7日至11日峰值压测，12月15日上线切换；范围和目标不变（3个仓，调度效率提升15%，车辆空驶率降低8%）。"
      "待办：瀚海IT确认迁移方案；TMS接口权限审批加快。",
      ["12月15日", "12月8日", "历史运单", "多一周"],
      {"name": "华东智能调度平台一期_排期评审会纪要_20261028.docx", "kind": "doc",
       "brief": "会议纪要文档：时间、地点（云帆会议线上+瀚海总部）、参会人、议题、结论（交付时间由12月8日调整为2026年12月15日及原因）、调整后的里程碑表格、待办事项。"},
      ctx=HX_CTX),
    S("q1.stress", "q1", "bg", "2026-11-04T10:12", "wudi", ["zhouheng", "linzhou"], ["chenmo", "xunuo"],
      "双十二峰值压测环境资源申请进度", "work", HX, HXP,
      "吴迪说明：双十二峰值压测需要在天枢云临时扩容（压测机64核×12台、模拟TMS回放节点），采购审批还在走流程，资源预计11月下旬才能到位，目前压测环境未就绪，对12月7日开始的压测有风险，请林舟帮忙催采购。",
      ["压测环境"], ctx=HX_CTX),
    S("q1.tms", "q1", "bg", "2026-11-06T16:40", "yanning", ["zhouheng"], ["linzhou", "maguodong"],
      "Re: TMS接口权限申请进度", "client", HX, HXP,
      "严宁回复周衡：TMS接口权限申请已提交瀚海信息安全部，需要三级审批，目前在第二级（信息安全部负责人），预计还要一到两周；测试环境的只读接口可以先用。",
      ["TMS"], ctx=HX_CTX),
    S("q1.final", "q1", "gold", "2026-11-12T19:05", "zhouheng", ["chengyuan", "maguodong", "yanning", "lvqiang", "huangzhen"],
      ["linzhou", "wanglei", "chenmo", "wudi", "dumingyuan"],
      "【会议纪要】华东智能调度平台（一期）项目规划最终确认会（11月12日）", "client", HX, HXP,
      "最终确认会会议纪要（双方确认版）。一、最终目标：一期覆盖上海嘉定、苏州昆山、杭州萧山3个仓；调度效率提升15%；车辆空驶率降低8%；系统可用性99.9%。"
      "二、交付时间：2026年12月15日20:00切换上线，12月16日起试运行两周。"
      "三、未解决问题：①约1.2亿条历史运单数据迁移方案待瀚海IT确认（责任人：马国栋、严宁，目标11月25日前）；②与瀚海TMS接口权限审批未完成（责任人：严宁）；③双十二峰值压测环境未就绪（责任人：吴迪）。"
      "四、其他决议：每周二、五召开项目例会；上线切换方案11月30日前评审。请各方11月14日前反馈意见，无反馈视为确认。正文中写清以上要点，附件为正式纪要。",
      ["99.9%", "12月15日", "20:00", "1.2亿", "TMS接口权限", "压测环境", "调度效率提升15%", "车辆空驶率降低8%", "上海嘉定", "苏州昆山", "杭州萧山"],
      {"name": "华东智能调度平台一期_项目规划最终确认纪要_20261112.docx", "kind": "doc",
       "brief": "正式会议纪要：时间（2026年11月12日 14:00-16:30）、地点、参会人、最终确认的目标、交付时间（12月15日20:00切换上线）、未解决问题清单（文本表格：编号|问题|责任人|目标日期）、其他决议、签发。关键结论放在前半部分。"},
      ctx=HX_CTX),
    S("q1.ack", "q1", "bg", "2026-11-13T09:31", "yanning", ["zhouheng"], ["linzhou", "maguodong"],
      "Re: 【会议纪要】华东智能调度平台（一期）项目规划最终确认会（11月12日）", "client", HX, HXP,
      "严宁简短回复：纪要内容无异议；数据迁移方案瀚海IT内部还在评审，预计11月下旬给书面确认；TMS权限审批我这边在催。三四句话即可。",
      [], ctx=HX_CTX),
    S("q1.wms", "q1", "distractor", "2026-06-18T18:20", "shaomin", ["yanning", "maguodong"], ["linzhou", "wanglei", "fangyu"],
      "【会议纪要】瀚海物流WMS升级项目 阶段评审会（6月18日）", "client", HX, "瀚海物流·WMS升级",
      "WMS升级项目阶段评审会纪要：波次策略优化与库位推荐已在上海嘉定仓试点上线；目标：拣货效率提升12%、库位利用率提升6%；计划7月31日全部仓完成上线；"
      "遗留问题：自动化分拣线接口联调未完成、部分RF手持终端兼容性问题。",
      ["7月31日", "拣货效率提升12%"],
      {"name": "瀚海WMS升级_阶段评审会纪要_20260618.docx", "kind": "doc", "brief": "会议纪要文档：时间、地点、参会人、进展、目标、计划、遗留问题表格。"},
      ctx="项目：瀚海物流·WMS升级（2026年2月-7月），云帆项目经理邵敏，瀚海对接人严宁、马国栋。"),
    S("q1.port", "q1", "distractor", "2026-10-22T17:55", "fengtao", ["caofeng", "gongxue"], ["linzhou", "wanglei", "gucheng"],
      "【会议纪要】远洋港务智慧堆场项目规划会（10月22日）", "client", "远洋港务", "远洋港务·智慧堆场规划",
      "远洋港务智慧堆场项目规划会纪要：目标：翻箱率降低10%，集卡平均等待时间缩短15%；一期范围：东港区2号、3号堆场；计划2027年3月31日交付；"
      "未解决问题：码头操作系统（TOS）接口文档尚未提供、岸桥作业数据采集方案待定、堆场GIS底图需更新。",
      ["2027年3月31日", "翻箱率降低10%"],
      {"name": "远洋港务智慧堆场_项目规划会纪要_20261022.docx", "kind": "doc", "brief": "会议纪要文档：时间、地点（天津）、参会人、目标、范围、计划、未解决问题表格。"},
      ctx="项目：远洋港务·智慧堆场规划（2026年9月启动，天津），云帆项目经理冯涛，客户方信息部主任曹锋、项目主管龚雪。"),
    S("q1.annual", "q1", "distractor", "2025-12-16T18:30", "wanglei", ["chengyuan", "maguodong"], ["linzhou", "guowen"],
      "【会议纪要】瀚海物流 × 云帆智能 2026年度合作规划会（12月16日）", "client", HX, None,
      "2026年度合作规划会纪要：上半年做WMS升级；下半年启动华东运输智能调度（初步设想覆盖华东主要仓库，目标调度效率提升20%以上，具体范围和时间待立项确定）；全年合作预算约600万元；季度高层沟通机制。",
      ["2026年度", "600万"], ctx="瀚海物流是云帆的核心客户，2024年合作过运输可视化平台。"),

    # ------------------------------------------------------------------ Q4 出差行程（会议部分）
    S("q4.hn_ask", "q4", "distractor", "2026-11-12T15:22", "caiqi", ["linzhou", "wanglei"], ["chenlixin"],
      "华南推广方案沟通会 时间征询", "client", HX, HNP,
      "蔡琪（瀚海华南区总经理助理）：陈总（陈立新，华南区总经理）希望与云帆当面沟通把华东调度经验推广到华南的方案；提供两个可选时间：11月25日（周三）下午、11月26日（周四）上午；"
      "地点在瀚海物流华南区运营中心（南山科技园）；请林总回复方便的时间和参会人。",
      ["11月25日", "11月26日"]),
    S("q4.hn_reply", "q4", "bg", "2026-11-12T17:05", "linzhou", ["caiqi"], ["wanglei", "chenlixin"],
      "Re: 华南推广方案沟通会 时间征询", "client", HX, HNP,
      "林舟回复：选11月25日下午，云帆这边我、王蕾、周衡参加，我们24日中午到深圳；请蔡琪发正式邀请。两三句话。", ["11月25日"]),
    S("q4.meeting", "q4", "gold", "2026-11-17T10:40", "caiqi", ["linzhou", "wanglei", "zhouheng"], ["chenlixin"],
      "【会议邀请】11月25日（周三）14:00-16:30 华南推广方案沟通会｜深圳·瀚海华南区运营中心", "client", HX, HNP,
      "正式会议邀请。时间：2026年11月25日（周三）14:00–16:30；地点：瀚海物流华南区运营中心（南山科技园，深圳市南山区科技中二路18号瀚海大厦12层1201会议室）；"
      "议题：华南推广方案沟通——华东智能调度一期经验介绍、华南区三个重点仓的试点范围、推广节奏与预算；"
      "瀚海参会：华南区总经理陈立新、华南区运营总监、华南区IT负责人；云帆参会：林舟、王蕾、周衡；到达后在一楼访客台登记，可停车2小时；联系人蔡琪 135****0817。",
      ["11月25日", "14:00", "16:30", "南山科技园", "陈立新", "华南推广方案沟通"]),
    S("q4.prep", "q4", "bg", "2026-11-19T09:50", "wanglei", ["linzhou", "zhouheng"], [],
      "下周深圳华南推广沟通会 材料准备分工", "work", HX, HNP,
      "王蕾：下周三下午和瀚海华南区陈总的沟通会，材料分工：林舟讲华东调度一期方案与价值，周衡讲实施节奏，王蕾准备华南三仓试点报价区间；24日中午到深圳后先在酒店碰一下；材料周一前发群里。",
      ["陈总"]),

    # ------------------------------------------------------------------ Q5 学校通知
    S("q5.yanxue", "q5", "gold", "2026-11-13T16:20", "school", ["linzhou"], [],
      "【缴费通知】四年级冬季研学活动费用缴纳（截止11月27日）", "school", "育才实验小学", None,
      "班主任何静通过家校通发给四年级3班家长：学校将于12月10日至11日组织四年级冬季研学（北京郊区科技馆+冰雪营地，一晚住宿），费用每人¥680（含交通、住宿、餐费、保险、门票）；"
      "请于11月27日（周五）17:00前在家校通App「缴费」栏目完成缴费；不参加的学生请在同一截止时间前提交不参加回执；有特殊情况联系班主任。署名：四年级3班 班主任 何静。",
      ["¥680", "11月27日", "冬季研学"], personal=True, sender="school"),
    S("q5.vaccine", "q5", "gold", "2026-11-16T12:10", "school", ["linzhou"], [],
      "【请签字回传】流感疫苗接种知情同意书（截止11月25日）", "school", "育才实验小学", None,
      "学校与望京社区卫生服务中心合作，12月2日在学校卫生室为自愿接种的学生接种流感疫苗（自费¥0，免费）；请家长阅读附件《流感疫苗接种知情同意书》，"
      "勾选是否同意接种并签字，于11月25日（周三）前拍照上传家校通「回执」栏目或交回纸质版给班主任；过期视为不接种。",
      ["知情同意书", "11月25日", "签字"],
      {"name": "育才实验小学_流感疫苗接种知情同意书.pdf", "kind": "pdf",
       "brief": "知情同意书文本：疫苗名称（四价流感疫苗）、接种时间地点、禁忌症、可能的不良反应、家长意见（□同意 □不同意）、家长签字、日期、回传截止11月25日。"},
      personal=True, sender="school"),
    S("q5.ptm", "q5", "gold", "2026-11-18T17:45", "school", ["linzhou"], [],
      "【家长会通知】四年级3班家长会：11月28日（周五）19:00 学校报告厅", "school", "育才实验小学", None,
      "家长会通知：时间11月28日（周五）19:00-20:30，地点学校报告厅（东教学楼一层），先全年级大会后分班交流；内容：期中学情分析、冬季研学说明、寒假安全；"
      "请每户至少一位家长参加，凭家校通签到码入校；因故不能参加的请提前向班主任何静请假。",
      ["11月28日", "19:00", "报告厅"], personal=True, sender="school"),
    S("q5.exam", "q5", "bg", "2026-10-23T15:30", "school", ["linzhou"], [],
      "【通知】四年级期中考试安排（11月4日-5日）", "school", "育才实验小学", None,
      "期中考试安排：11月4日（周三）语文、数学，11月5日（周四）英语、科学；考试期间正常放学；提醒孩子带好文具、早睡。仅告知，无需回执。",
      ["11月4日"], personal=True, sender="school"),
    S("q5.cold", "q5", "bg", "2026-11-06T08:15", "school", ["linzhou"], [],
      "降温及流感高发期健康提醒", "school", "育才实验小学", None,
      "学校卫生室提醒：本周起气温明显下降，流感进入高发期；请家长每天早上为孩子测体温，发热、咳嗽请在家休息并向班主任请假；勤洗手、开窗通风。仅提醒，无需回执。",
      ["流感"], personal=True, sender="school"),
    S("q5.midterm", "q5", "bg", "2026-11-10T18:20", "school", ["linzhou"], [],
      "四年级3班期中学情反馈已发布", "school", "育才实验小学", None,
      "何静：期中考试成绩和学情反馈已发布到家校通「成长档案」，家长可登录查看；本次不公布排名；具体情况家长会上再交流。无需回执。",
      ["家校通"], personal=True, sender="school"),
    S("q5.open", "q5", "distractor", "2026-08-28T10:00", "school", ["linzhou"], [],
      "【开学通知】2026年秋季学期开学安排", "school", "育才实验小学", None,
      "秋季学期开学通知：9月1日（周二）8:00返校报到；新学年孩子升入四年级3班，班主任仍为何静；请于9月5日前在家校通缴纳校服费¥360；开学第一周作息安排；需带的物品清单。",
      ["9月1日", "¥360", "9月5日"], personal=True, sender="school"),
    S("q5.lunch", "q5", "distractor", "2026-09-02T16:40", "school", ["linzhou"], [],
      "【缴费通知】秋季学期午餐费（截止9月10日）", "school", "育才实验小学", None,
      "秋季学期午餐费每人¥1,250（按83个就餐日计），请于9月10日前在家校通缴费；有饮食过敏的学生请在缴费时备注。",
      ["¥1,250", "9月10日"], personal=True, sender="school"),
    S("q5.autumn", "q5", "distractor", "2026-10-09T17:10", "school", ["linzhou"], [],
      "【秋游通知】10月16日四年级秋游（缴费及回执截止10月13日）", "school", "育才实验小学", None,
      "四年级秋游：10月16日（周五）去奥林匹克森林公园，费用¥120（含大巴、保险、午餐）；请于10月13日前缴费并提交安全回执；当天7:50到校集合。",
      ["¥120", "10月13日", "10月16日"], personal=True, sender="school"),
    S("q5.y2025", "q5", "distractor", "2025-11-14T16:05", "school", ["linzhou"], [],
      "【缴费通知】三年级冬季研学活动费用缴纳（截止11月26日）", "school", "育才实验小学", None,
      "去年（2025年）的通知：三年级冬季研学（12月9日，北京自然博物馆+农业科技园，当天往返），费用¥620，请于2025年11月26日前在家校通缴费。",
      ["¥620", "11月26日"], personal=True, sender="school"),

    # ------------------------------------------------------------------ Q6 合同条款
    S("q6.need", "q6", "bg", "2026-07-08T10:15", "linzhou", ["geming"], ["xunuo", "chenmo"],
      "关于道路路网与货运时效数据服务的采购需求", "client", "星河数据", XHP,
      "林舟向星河数据销售总监葛明说明采购需求：为调度引擎训练与评估，需要华东/华南城配路网数据、干线路网限行与收费数据、近三年货运时效样本；服务期12个月；希望两周内给方案和报价。",
      ["路网"]),
    S("q6.quote", "q6", "bg", "2026-07-21T17:40", "geming", ["linzhou"], ["xunuo"],
      "星河数据｜数据服务方案及报价", "client", "星河数据", XHP,
      "葛明发来数据服务方案和报价：总价¥1,980,000（12个月，含数据接口、季度更新、驻场支持2人月）；附件为方案及报价单。",
      ["¥1,980,000"],
      {"name": "星河数据_数据服务方案及报价_20260721.pdf", "kind": "pdf", "brief": "方案及报价单：数据内容清单、更新频率、交付方式、服务级别、分项报价表（文本表格）、合计¥1,980,000。"}),
    S("q6.bargain", "q6", "bg", "2026-08-05T15:30", "xunuo", ["linzhou"], ["sunhao"],
      "星河数据报价比价及议价结果", "work", "星河数据", XHP,
      "采购许诺：已对比另外两家供应商（报价分别为¥2,150,000和¥1,720,000但缺少华南城配数据），与星河议价后总价降至¥1,860,000，建议选星河；合同由星河先出草稿，法务郑雅审核。",
      ["¥1,860,000"]),
    S("q6.v2", "q6", "distractor", "2026-08-28T18:10", "jiangwen", ["xunuo", "linzhou"], ["zhengya", "geming"],
      "数据服务合同（草稿v2）请审阅", "client", "星河数据", XHP,
      "星河商务经理姜雯发来合同草稿v2：合同总额¥1,860,000；付款：签约后10个工作日内支付40%（¥744,000），中期验收后支付30%（¥558,000），终验后支付30%（¥558,000）；"
      "逾期交付违约金：每逾期一日按合同总额的千分之二支付，累计不超过合同总额的15%；请云帆一周内反馈修改意见。",
      ["草稿v2", "40%", "千分之二"],
      {"name": "数据服务合同_草稿v2_20260828.docx", "kind": "doc",
       "brief": "合同草稿v2（标注“草稿”）：合同双方、服务内容、合同价款与支付（40%/30%/30%）、交付与验收、违约责任（每日千分之二，累计不超过15%）、保密、争议解决。1200-2000字，价款和违约条款放在前面。"}),
    S("q6.legal", "q6", "distractor", "2026-09-02T11:20", "zhengya", ["linzhou", "xunuo"], ["sunhao"],
      "Re: 数据服务合同（草稿v2）法务审核意见", "work", "星河数据", XHP,
      "法务郑雅的审核意见：1）首付款40%偏高，建议降为30%，中期验收后支付40%，终验后30%；2）星河对千分之二的违约金比例有异议，我方可接受的底线是每日千分之一、累计上限10%；"
      "3）必须补充：逾期超过30日我方有权解除合同并要求退还已付款项；4）数据安全与保密、知识产权条款修改建议。这是审核意见，不是最终条款。",
      ["千分之一", "30日", "首付款"]),
    S("q6.accept", "q6", "bg", "2026-09-08T16:45", "geming", ["linzhou", "xunuo"], ["jiangwen", "zhengya"],
      "Re: 合同条款修改意见反馈", "client", "星河数据", XHP,
      "葛明回复：星河同意贵方意见——付款比例调整为30%、40%、30%；违约金改为每日千分之一、累计不超过10%；接受逾期超过30日可解除合同条款；希望9月中旬完成签署，终稿由姜雯发出。",
      ["千分之一"]),
    S("q6.signed", "q6", "gold", "2026-09-18T17:26", "zhengya", ["linzhou", "xunuo", "sunhao"], ["zhouheng", "chenmo"],
      "【已签署】星河数据·数据服务合同（YF-XH-2026-031）", "work", "星河数据", XHP,
      "法务郑雅通知：与星河数据的数据服务合同已双方盖章生效（签署日期2026年9月18日，合同编号YF-XH-2026-031），签署版扫描件见附件，原件存法务部。正文列出关键条款摘要："
      "合同总额¥1,860,000（含税）；付款分3期：签约后10个工作日内支付30%（¥558,000），中期验收后支付40%（¥744,000），终验后支付30%（¥558,000）；"
      "逾期交付：每逾期一日按合同总额的千分之一支付违约金，累计不超过合同总额的10%；逾期超过30日，我方有权解除合同；服务期12个月（2026年9月18日至2027年9月17日）。",
      ["YF-XH-2026-031", "¥1,860,000", "30%", "¥558,000", "40%", "¥744,000", "千分之一", "10%", "30日"],
      {"name": "YF-XH-2026-031_数据服务合同_签署版.pdf", "kind": "pdf",
       "brief": "合同正文（签署版）：标题、合同编号YF-XH-2026-031、甲方云帆智能科技有限公司、乙方星河数据技术有限公司；第一条 服务内容（简短）；第二条 合同价款与支付（总额¥1,860,000，3期：30%/40%/30%及金额与触发条件）；"
                "第三条 交付与验收；第四条 违约责任（逾期交付每日千分之一，累计不超过10%，逾期超过30日甲方可解除合同）；第五条 保密；第六条 其他；签章栏（已盖章）。1500-2500字，价款与违约责任必须在前1200字内出现。"}),
    S("q6.pay1", "q6", "gold", "2026-09-24T10:08", "sunhao", ["linzhou"], ["xunuo", "mengyao"],
      "星河数据合同首期款付款申请（YF-XH-2026-031）待您审批", "work", "星河数据", XHP,
      "财务孙浩：按合同约定，首期款为签约后10个工作日内支付合同总额的30%，即¥558,000，最晚付款日为10月10日（遇国庆顺延计算）；已在OA发起付款申请，请林总审批；"
      "后续两期：中期验收后支付40%（¥744,000），终验后支付30%（¥558,000），财务会按验收单据安排。",
      ["¥558,000", "10个工作日", "30%", "40%", "¥744,000"]),
    S("q6.recv", "q6", "bg", "2026-10-12T14:30", "jiangwen", ["sunhao", "xunuo"], ["linzhou"],
      "首期款到账确认及发票寄送", "client", "星河数据", XHP,
      "姜雯确认已收到首期款¥558,000，增值税专用发票已通过极速快递寄出（单号JS7720******3381）。", ["¥558,000"]),
    S("q6.deliv", "q6", "bg", "2026-10-20T18:00", "luobin", ["zhouheng", "chenmo"], ["linzhou"],
      "第一批数据交付说明（华东城配路网+历史时效样本）", "client", "星河数据", XHP,
      "星河项目经理骆斌说明第一批数据已通过数据接口交付：华东城配路网（沪苏浙皖）、2023-2026年货运时效样本约3800万条；附数据字典；第二批（华南）计划11月下旬交付。",
      ["第一批"]),
    S("q6.mid", "q6", "bg", "2026-11-10T11:00", "luobin", ["zhouheng"], ["linzhou", "xunuo"],
      "中期验收安排（拟11月30日）", "client", "星河数据", XHP,
      "骆斌：第二批数据预计11月下旬交付，建议11月30日进行中期验收，请周衡确认验收标准和参加人员。", ["中期验收"]),
    S("q6.tianshu", "q6", "distractor", "2026-05-12T16:30", "zhengya", ["linzhou", "xunuo", "sunhao"], ["wudi"],
      "【已签署】天枢云·云资源服务合同（YF-TS-2026-012）", "work", "天枢云", "天枢云·云资源采购",
      "法务郑雅通知：与天枢云的云资源服务合同已签署（合同编号YF-TS-2026-012），合同总额¥960,000，服务期12个月；付款：按季度预付，每季度¥240,000；"
      "服务等级：可用性99.95%，未达标按当月服务费的10%-30%给予服务抵扣；资源交付延迟每日按当期费用千分之五抵扣。扫描件见附件。",
      ["YF-TS-2026-012", "¥960,000", "¥240,000"],
      {"name": "YF-TS-2026-012_云资源服务合同_签署版.pdf", "kind": "pdf", "brief": "云资源服务合同正文：双方、资源清单、价款与支付（按季度预付）、服务等级与赔偿、保密、其他。1200-2000字。"}),

    # ------------------------------------------------------------------ Q2 体检解读
    S("q2.interp", "q2", "gold", "2026-04-24T10:30", "ankang", ["linzhou"], [],
      "林舟先生：2026年度体检报告解读及健康建议", "health", "安康体检中心", None,
      "安康体检中心健康管理师邹敏给林舟的个人报告解读：与2025年相比变化最明显的指标——低密度脂蛋白胆固醇（LDL-C）由3.12升至4.26 mmol/L，尿酸由386升至468 μmol/L，"
      "空腹血糖由5.4升至6.3 mmol/L（空腹血糖受损），体重由72.5 kg增至77.8 kg（BMI 25.4，超重）；其余指标基本稳定。"
      "主检医生建议：低脂低嘌呤饮食（少吃红肉、动物内脏、海鲜，少喝含糖饮料，限酒）；每周≥150分钟中等强度运动；3个月后复查血脂、尿酸、血糖（7月下旬），必要时内分泌科就诊。"
      "语气专业温和，可提供预约复查的方式。",
      ["4.26", "468", "6.3", "77.8", "150分钟", "3个月后复查"], personal=True, sender="ankang_hm"),
]

SENDERS = {
    "school": {"name": "育才实验小学家校通", "email": "notice@yucai-school.edu.cn", "org": "育才实验小学"},
    "ankang": {"name": "安康体检中心", "email": "report@ankang-health.com", "org": "安康体检中心"},
    "ankang_hm": {"name": "安康体检中心 健康管理师 邹敏", "email": "health@ankang-health.com", "org": "安康体检中心"},
}


def who(key: str, personal: bool = False) -> dict:
    if key in SENDERS:
        return dict(SENDERS[key])
    p = PEOPLE[key]
    org = p.get("org") or ""
    email = B.OWNER["personal_email"] if (personal and key == "linzhou") else p["email"]
    return {"name": p["name"], "email": email, "org": org}


def _line(key: str, personal: bool) -> str:
    w = who(key, personal)
    p = PEOPLE.get(key, {})
    title = p.get("title", "")
    return f"{w['name']}（{w['org']}{' ' + title if title else ''}）<{w['email']}>"


def build_prompt(spec: dict) -> list[dict]:
    pers = spec["personal"]
    lines = ["请写下面这封邮件的正文" + ("以及附件内容" if spec["attach"] else "") + "。", "【邮件信息】"]
    lines.append("发件人：" + _line(spec["from"], pers))
    lines.append("收件人：" + "；".join(_line(k, pers) for k in spec["to"]))
    if spec["cc"]:
        lines.append("抄送：" + "；".join(_line(k, pers) for k in spec["cc"]))
    lines.append(f"发送时间：{spec['date'].replace('T', ' ')}")
    lines.append(f"主题：{spec['subject']}")
    if spec["ctx"]:
        lines.append(f"【背景】{spec['ctx']}")
    lines.append(f"【内容要求】{spec['brief']}")
    if spec["must"]:
        lines.append("【必须逐字出现的字符串】（可在正文或附件中，原样写出，不要改写数字格式）：" + "、".join(f"「{m}」" for m in spec["must"]))
    if spec["attach"]:
        a = spec["attach"]
        lines.append(f"【附件】文件名：{a['name']}（kind={a['kind']}）。内容要求：{a['brief']} 附件文本要像从真实文件中提取出的文字。")
    lines.append("正文要求：真实自然，有称呼；不写签名档（系统会追加），结尾最多写署名；不要编造与上述事实矛盾的信息；不要出现真实品牌名。")
    lines.append('只输出JSON：{"body":"正文","attachments":[' + ('{"name":"文件名","kind":"doc|sheet|pdf","text":"附件全文"}' if spec["attach"] else "") + "]}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def _norm(s: str) -> str:
    return s.replace("，", ",").replace(" ", "")


def make_validator(spec):
    def check(text):
        d = parse_json(text)
        body = d.get("body") or ""
        atts = [a for a in (d.get("attachments") or []) if isinstance(a, dict)]
        assert len(body) > 20
        if spec["attach"]:
            assert atts and len(atts[0].get("text") or "") > 200
        blob = body + "\n".join(a.get("text") or "" for a in atts)
        miss = [m for m in spec["must"] if m not in blob]
        assert not miss, miss
        return d

    return check


async def generate(specs=SPECS):
    out = {}
    async with LLM(concurrency=24) as llm:
        async def one(spec):
            check = make_validator(spec)
            txt = await llm.chat(build_prompt(spec), model=STRONG, temperature=0.7, max_tokens=6000, seed=int(hashlib.md5(spec["nid"].encode()).hexdigest()[:6], 16),
                                 tag=f"needle:{spec['nid']}", validate=check, attempts=8)
            if txt is None:
                print("FAILED", spec["nid"])
                return
            d = check(txt)
            atts = []
            for a in d.get("attachments") or []:
                if not isinstance(a, dict):
                    continue
                if spec["attach"]:
                    a["name"] = spec["attach"]["name"]
                    a["kind"] = spec["attach"]["kind"]
                atts.append({"name": a.get("name"), "kind": a.get("kind", "doc"), "text": a.get("text", "")})
            out[spec["nid"]] = {**spec, "body": d["body"], "attachments": atts[:1] if spec["attach"] else []}

        await asyncio.gather(*(one(s) for s in specs))
        print(f"needles: {len(out)}/{len(specs)} cost=${llm.cost:.3f}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ordered = {s["nid"]: out[s["nid"]] for s in specs if s["nid"] in out}
    OUT.write_text(json.dumps(ordered, ensure_ascii=False, indent=1), "utf-8")
    return ordered


if __name__ == "__main__":
    asyncio.run(generate())
