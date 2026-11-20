"""Plan and generate unique LLM-written email threads (work, client, personal).

    python -m demo.corpus.gen_threads [--limit N] [--concurrency 48]

Each job is a scenario (project phase / internal topic / family matter) with
fixed participants and a start date; the model writes the whole thread as JSON.
Parsed threads go to demo/data/gen/threads.jsonl (raw replies stay in the cache).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import random
from pathlib import Path

from . import bible as B
from .calendar import END, START, rand_day, rand_workday, weekday_cn
from .llm import CHEAP, FLASH, LITE, LLM, parse_json

GEN = B.DATA / "gen"
PEOPLE = B.all_people()
ORG = {o["name"]: o for o in B.ORGS}
EMAIL2KEY = {p["email"]: k for k, p in PEOPLE.items() if p.get("email")}
EMAIL2KEY[B.OWNER["personal_email"]] = "linzhou"

TECH = ["chenmo", "jiangtao", "xielei", "luoxiaotong", "tianye", "gaoyuan", "xuzihao", "weiran", "panting", "wudi"]
IMPL = ["kongxiang", "luhao"]
TEAM = ["gucheng", "fangyu", "yezihan", "qinchuan", "tangyue", "songjia", "qiuyang", "baiyang"]

SYSTEM = """你是一位擅长还原真实中文职场与生活邮件的写作者。你写的邮件用于一个完全虚构的演示邮箱。要求：
- 真实感：像真人在工作和生活中随手写的邮件，有具体的人名、数字、日期、时间、地点、系统/设备名、金额、版本号；不同身份语气不同（对上级简洁有礼，对下属直接，对客户正式客气，家人朋友之间随意）；允许口语化，允许很短的回复（例如"收到，按这个推进。"）。
- 避免：空泛套话、口号式排比、"赋能/抓手/闭环"堆砌、每封都长篇大论、每个人说话都一个腔调。
- 不得出现任何真实存在的公司、品牌、产品或App名称（如微信、钉钉、腾讯会议、飞书、阿里云、华为、顺丰、淘宝等）。需要时用虚构名称：线上会议用"云帆会议"，即时消息说"IM上"或"群里"，网盘用"鲸云盘"。
- 不写签名档（系统会自动追加），正文末尾最多写一个署名；回复/转发的正文中不要引用之前邮件的内容（系统会自动附上）。
- 手机号一律写成脱敏形式（如 137****2235）；不得出现身份证号、完整银行卡号。
- 只输出一个 JSON 对象，不要任何解释或代码块标记。"""

FORMAT = """输出格式：
{"messages":[{"from":"发件人邮箱","to":["邮箱"],"cc":["邮箱"],"day_offset":0,"time":"09:40","subject":"主题","body":"正文","attachments":[]}]}
说明：day_offset 是相对第一封邮件的天数（0,0,1,3…），time 为24小时制；回复的主题以"Re: "开头、转发以"Fw: "开头；没有附件就写 []；有附件时写 {"name":"文件名.docx","kind":"doc|sheet|pdf|slides","text":"附件的完整文字内容"}，纪要/报告写成条理清晰的正文，表格用"|"分隔的文本表格，400-1500字。"""

# ---------------------------------------------------------------- scenarios
# (phase, title, description, n_messages, attachment hint or None, client participates)
PROJECT_SCEN = [
    ("presale", "需求初谈", "客户{c1}提出初步需求，销售{sales}约林舟和{lead}准备一次需求澄清会，邮件里确认时间、地点、参会人和要准备的材料，会后简单同步关键结论", 4, None, True),
    ("presale", "方案初稿审阅", "{lead}把技术方案初稿发给林舟审阅，林舟提出具体修改意见（架构图、报价口径、可引用的同行业案例），{lead}修改后再发一版", 3, "slides|方案初稿或修改后的方案目录与要点", False),
    ("presale", "报价审批", "销售{sales}发起报价审批，列出人天、单价、软硬件、折扣理由，财务孙浩和林舟分别给意见，最终定价", 4, "sheet|报价明细表", False),
    ("presale", "POC演示准备", "准备给客户做POC演示：演示环境、脱敏数据、演示脚本、分工和彩排时间，过程中有一个环境问题需要解决", 4, None, False),
    ("presale", "投标准备", "客户发布了招标文件，内部准备投标：分工、资质材料、技术标/商务标负责人、截止时间、封标安排", 4, "doc|投标文件分工与目录", False),
    ("presale", "客户拜访安排", "林舟和{sales}去客户现场拜访{c1}，邮件确认拜访议程、交通、带去的材料和参会人", 3, None, True),
    ("kickoff", "合同评审", "项目合同进入评审，法务郑雅提出风险条款意见（付款节点、知识产权归属、违约责任上限），{sales}与客户沟通后反馈修改结果", 4, None, False),
    ("kickoff", "项目启动会", "项目经理{pm}发出项目启动会邀请，会后发出启动会纪要（目标、范围、里程碑、双方分工、风险），客户方{c1}回复确认", 3, "doc|启动会会议纪要", True),
    ("kickoff", "人员安排", "{pm}向林舟和交付总监杜明远申请项目人员（方案、开发、实施），与另一个项目的人员时间冲突，需要协调", 3, None, False),
    ("design", "需求调研", "安排去客户现场做需求调研：日程、访谈对象、调研提纲，客户{c1}协调各部门时间", 4, "doc|需求调研计划与访谈提纲", True),
    ("design", "需求确认", "需求规格说明书提交客户确认，客户{c1}提出若干修改意见，{pm}逐条答复并确认范围", 3, "doc|需求确认意见与答复", True),
    ("design", "方案评审", "内部技术方案评审，架构师陈默等提出意见，{pm}会后发评审纪要和待办", 3, "doc|方案评审会议纪要", False),
    ("design", "接口对接", "与客户IT对接接口规范（字段、频率、鉴权、异常重试），讨论数据样例中发现的问题", 4, "sheet|接口字段清单", True),
    ("design", "技术选型", "关于技术路线的讨论（数据存储、消息队列、部署在客户机房还是云上），各方给出取舍理由，林舟拍板", 4, None, False),
    ("build", "项目周报", "{pm}发送本周项目周报：本周进展、下周计划、风险与需协调事项，有人回复跟进其中一项", 2, "doc|项目周报", True),
    ("build", "进度风险", "开发进度落后约一周，{pm}向林舟说明原因（客户数据延迟/人员变动等）并提出两套应对方案，林舟决策", 3, None, False),
    ("build", "需求变更", "客户{c1}提出需求变更，内部评估工作量与费用影响，走变更流程并回复客户", 4, "doc|需求变更申请单", True),
    ("build", "测试环境", "申请并搭建测试环境（服务器配置、网络打通、账号权限），与客户IT协调中遇到审批卡点", 3, None, True),
    ("build", "关键缺陷", "联调测试中发现关键缺陷（数据错乱/性能不达标），分析原因、给出修复时间和临时规避方案", 3, None, False),
    ("build", "资源支援", "项目临时需要算法或数据工程师支援两周，跨部门协调，梁峰或赵一鸣参与决策", 3, None, False),
    ("build", "月度沟通会", "与客户的月度沟通会：会前议程确认，会后{pm}发纪要（进展、决议、待办）", 3, "doc|月度沟通会会议纪要", True),
    ("launch", "上线方案评审", "上线/切换方案评审：切换窗口、回退预案、值守排班、客户侧配合事项", 3, "doc|上线切换方案", True),
    ("launch", "上线情况通报", "系统上线当晚和次日的情况通报，出现一两个小问题及处理结果", 3, None, True),
    ("launch", "用户培训", "安排给客户业务人员的操作培训：场次、人数、培训材料、签到", 2, None, True),
    ("accept", "验收材料", "准备验收材料清单（测试报告、用户手册、运维手册、培训记录），分工与截止时间", 3, "sheet|验收材料清单", False),
    ("accept", "验收会", "客户验收会的安排，以及会后客户出具的验收意见和遗留问题", 3, "doc|验收意见", True),
    ("accept", "回款跟进", "催收验收款：客户财务流程进度、发票信息核对、预计到账时间，孙浩关注", 4, None, True),
    ("after", "运维问题", "上线后客户报告问题（性能变慢/数据异常），运维排查并给出说明和改进", 3, None, True),
    ("after", "满意度回访", "项目满意度回访，以及案例宣传素材的授权沟通", 2, None, True),
    ("after", "二期规划", "客户初步提出二期需求，内部评估可行性、报价区间和人员", 3, None, True),
    ("after", "项目复盘", "项目复盘：做得好的地方、问题与改进项，{pm}整理复盘纪要", 2, "doc|项目复盘纪要", False),
]

# (title, description, n, attachment hint, participant picker name)
INTERNAL_SCEN = [
    ("部门周会纪要", "解决方案部周会：各人项目进展、售前资源冲突、下周重点；杨雨会后发纪要，个别人补充", 2, "doc|部门周会纪要", "team"),
    ("季度OKR", "解决方案部季度OKR制定：林舟给出部门目标，下属反馈各自的KR和顾虑", 4, "sheet|部门OKR表", "team"),
    ("年度规划", "年度工作规划和预算：赵一鸣要求各部门提交明年规划，林舟与顾城、孙浩讨论人力和费用", 4, "doc|部门年度规划要点", "mgmt"),
    ("售前资源排期", "多个商机同时需要售前支持，顾城和林舟协调方案经理和售前工程师的排期", 3, None, "team"),
    ("绩效考核", "季度/年度绩效考核：HRBP刘倩发起流程，林舟与个别下属沟通评分和改进点", 3, None, "hr"),
    ("招聘需求", "部门提出招聘需求（解决方案经理/售前工程师），与HR确认JD、薪资范围、到岗时间", 3, None, "hr"),
    ("面试安排", "张蕊安排候选人面试，林舟和顾城给出面试反馈和是否发offer的意见", 4, None, "hr"),
    ("晋升答辩", "部门同事晋升答辩安排：材料要求、评委、时间，林舟作为评委给意见", 3, None, "hr"),
    ("新人入职", "新同事入职：工位、电脑、账号、导师安排和第一周计划", 3, None, "hr"),
    ("培训报名", "公司组织的培训（大模型应用/项目管理/商务谈判）报名与名额分配", 2, None, "hr"),
    ("离职交接", "一名同事提出离职，安排工作交接清单和客户关系交接", 3, "sheet|工作交接清单", "hr"),
    ("预算编制", "部门费用预算编制：差旅、市场活动、外包，财务孙浩要求压缩，来回沟通", 4, "sheet|部门预算表", "fin"),
    ("报销退回", "林舟或下属的差旅报销被退回（发票抬头/超标准），说明情况并补充材料", 3, None, "fin"),
    ("回款通报", "财务发出各项目回款情况通报，林舟追问其中逾期的几笔", 3, "sheet|项目回款明细", "fin"),
    ("差旅标准调整", "公司调整差旅标准（住宿上限、机票舱位），林舟就部门出差频繁提出例外申请", 3, None, "fin"),
    ("办公位调整", "行政通知办公区调整/搬迁，解决方案部的工位和会议室安排", 3, None, "admin"),
    ("年会节目", "公司年会筹备：部门节目、抽奖、座位、优秀员工评选", 4, None, "admin"),
    ("团建活动", "部门团建方案（地点、预算、时间），大家投票后确定", 4, None, "team"),
    ("节日福利", "行政发节日福利领取通知，有人询问配送到家和选择", 2, None, "admin"),
    ("管理层会议", "公司月度经营会：总裁办姚佳发会议通知和会后纪要（经营数据、重点项目、决议）", 2, "doc|经营分析会纪要", "mgmt"),
    ("组织调整", "组织架构调整讨论：解决方案部是否并入大客户线、行业小组划分，赵一鸣与林舟沟通", 3, None, "mgmt"),
    ("跨部门协作", "售前与交付在项目交接上的扯皮，杜明远和林舟讨论交接规范", 4, None, "mgmt"),
    ("产品需求反馈", "解决方案部把客户现场收集的产品需求反馈给产品部欧阳晨/夏冰，讨论排期", 3, "sheet|客户需求反馈清单", "product"),
    ("调度引擎产品化", "调度引擎3.0产品化：陈默和薛凯同步进展，林舟提出方案侧需要的配置能力", 3, None, "tech"),
    ("技术分享", "组织部门内部技术分享（运筹优化/大模型Agent/数据治理），确定主讲人和时间", 3, None, "tech"),
    ("架构评审", "某个通用平台的架构评审，陈默、梁峰意见不一，林舟从客户交付角度给意见", 4, None, "tech"),
    ("安全合规", "信息部雷鸣通报客户数据安全合规要求（数据脱敏、外发审批），解决方案部落实", 3, None, "it"),
    ("大模型试点", "公司内部大模型知识助手试点，解决方案部作为种子用户反馈问题", 3, None, "tech"),
    ("知识库建设", "解决方案知识库：顾城推动方案、案例、报价模板入库，分配整理任务和截止时间", 3, "sheet|知识库入库任务表", "team"),
    ("案例宣传", "市场部白露/陶然撰写客户案例宣传稿，请林舟审核措辞和数据", 3, "doc|客户案例宣传稿", "market"),
    ("行业峰会", "行业峰会邀请林舟做主题演讲，市场部协调讲题、PPT和行程", 4, None, "market"),
    ("展会筹备", "公司参加行业展会：展位、演示内容、值守排班", 3, None, "market"),
    ("采购申请", "部门采购申请（演示用工控机/软件授权/外包服务），采购许诺询价比价", 3, None, "proc"),
    ("合作伙伴", "与合作伙伴（GIS地图/咨询公司）的合作沟通：联合方案、分成方式", 3, None, "partner"),
    ("出差安排", "林舟一周内多地出差的行程协调，杨雨帮忙订票，期间有会议时间冲突", 3, None, "assist"),
    ("客户投诉升级", "某客户高层对响应速度不满直接投诉到总裁韩振东，林舟牵头处理并汇报", 4, None, "mgmt"),
    ("加班与调休", "项目冲刺期加班安排和调休申请，HR提醒合规要求", 3, None, "hr"),
    ("述职准备", "年终述职：林舟准备述职PPT，请杨雨和顾城帮忙整理数据", 3, "slides|述职提纲", "team"),
]

PROSPECTS = [
    ("华信物流", "城配物流", "信息部经理"), ("通达冷链", "冷链运输", "IT总监"), ("江海港口集团", "港口", "信息中心主任"),
    ("鼎盛精工", "汽车零部件制造", "数字化负责人"), ("康泰医院集团", "医疗", "信息科长"), ("云岭电力", "电力", "科技部主任"),
    ("城投交通", "公交/交通", "智慧交通处处长"), ("百川食品", "食品制造", "供应链总监"), ("海纳零售", "连锁零售", "物流总监"),
    ("锦程快运", "零担快运", "运营副总"), ("德茂纺织", "纺织", "信息化主管"), ("安澜水务", "水务", "调度中心主任"),
    ("清源环保", "环保", "运营总监"), ("长风航运", "航运", "信息技术部经理"), ("宏远建材", "建材", "物流部长"),
]

PRESALE_SCEN = [
    ("商机评估", "新商机{prospect}（{industry}）的评估：客户需求、预算、竞争对手、我方胜算，决定是否投入售前资源", 3, None),
    ("方案撰写", "为{prospect}撰写解决方案建议书，分工、截止时间、可复用的案例和材料", 3, "doc|方案建议书目录与要点"),
    ("投标结果", "{prospect}项目投标结果出来（中标/未中标），复盘原因和后续动作", 3, None),
    ("演示复盘", "给{prospect}做完演示后的复盘：客户关注点、遗留问题、下一步", 2, None),
    ("报价策略", "{prospect}项目报价策略讨论：客户预算有限，是否拆分一期二期、折扣底线", 4, "sheet|报价测算"),
]

FAMILY_SCEN = [
    ("周末安排", "和{who}商量这个周末的安排（带孩子上课、看老人、聚餐、看展），确定时间和分工", 3),
    ("回老家过年", "春节回扬州老家的安排：车票/开车、给父母和亲戚的礼物、在老家住几天", 4),
    ("暑假旅行", "暑假家庭旅行规划：目的地比较、预算、酒店和机票、孩子的兴趣", 4),
    ("课外班", "林小禾的课外班（编程/英语/游泳）选择和时间安排，费用和接送", 3),
    ("家庭保险", "家庭保险续保：重疾险/医疗险/车险到期，对比方案和保费", 3),
    ("父亲体检", "{who}和林舟商量父亲林建华来北京体检或住院复查的安排（血压偏高）", 4),
    ("车辆保养", "车子保养/年检/保险理赔的安排，4S店预约时间", 2),
    ("房贷", "讨论房贷提前还款还是理财，计算利息和家庭现金流", 3),
    ("家里维修", "家里热水器/空调/漏水维修，物业和师傅预约时间", 3),
    ("纪念日", "结婚纪念日/生日的餐厅和礼物安排", 2),
    ("家庭开支", "整理上个月家庭开支，发现某些支出偏高，商量控制", 3),
    ("孩子学习", "孩子期中/期末考试情况和学习计划，老师的反馈", 3),
    ("给父母买东西", "给双方父母买保健品/家电，比较型号和价格", 2),
    ("亲戚来京", "亲戚来北京看病/旅游，住宿和陪同安排", 3),
]

FRIEND_SCEN = [
    ("同学聚会", "{who}组织大学/高中同学聚会，确定时间、地点、人数和AA方式", 4),
    ("婚礼邀请", "{who}发来自己或孩子婚礼/喜宴的邀请，确认出席和礼金方式", 2),
    ("球局", "约周末打羽毛球/跑步，确定场地和时间", 3),
    ("创业咨询", "{who}在做创业项目，请林舟帮忙看商业计划/介绍客户，林舟给出建议", 3),
    ("推荐工作", "{who}托林舟帮忙内推/介绍工作机会，交换简历和岗位信息", 3),
    ("老友来京", "{who}来北京出差，约吃饭叙旧", 3),
    ("借资料", "朋友之间分享书单/行业报告/孩子升学资料", 2),
]


SEASONAL = {
    "季度OKR": [1, 4, 7, 10], "年度规划": [11, 12], "年会节目": [12, 1], "绩效考核": [1, 4, 7, 10],
    "节日福利": [1, 2, 4, 6, 9], "述职准备": [12, 1], "预算编制": [11, 12, 1], "团建活动": [4, 5, 6, 9, 10],
}


def grade_at(d: dt.date) -> str:
    if d >= dt.date(2026, 9, 1):
        return "四年级"
    if d >= dt.date(2025, 9, 1):
        return "三年级"
    if d >= dt.date(2024, 9, 1):
        return "二年级"
    return "一年级"


def active_projects(d: dt.date) -> list[dict]:
    out = []
    for p in B.PROJECTS:
        if p["name"] in NEEDLE_GUARD or not p.get("org"):
            continue
        s, e = dt.date.fromisoformat(p["start"]), dt.date.fromisoformat(p["end"])
        if s - dt.timedelta(days=30) <= d <= e + dt.timedelta(days=45):
            out.append(p)
    return out


def _people_line(key: str, personal: bool = False) -> str:
    p = PEOPLE[key]
    email = B.OWNER["personal_email"] if (personal and key == "linzhou") else p.get("email", "")
    role = p.get("title") or p.get("relation") or ""
    org = p.get("org") or ("" if personal else B.CO_SHORT)
    if org == B.COMPANY:
        org = B.CO_SHORT
    return f"- {p['name']}｜{role}｜{org}｜{email}"


def _phase_window(proj: dict, phase: str) -> tuple[dt.date, dt.date]:
    s, e = dt.date.fromisoformat(proj["start"]), dt.date.fromisoformat(proj["end"])
    L = (e - s).days

    def f(x):
        return s + dt.timedelta(days=int(L * x))

    return {
        "presale": (s - dt.timedelta(days=75), s - dt.timedelta(days=5)),
        "kickoff": (s, f(0.12)),
        "design": (f(0.08), f(0.35)),
        "build": (f(0.30), f(0.80)),
        "launch": (f(0.75), f(0.92)),
        "accept": (f(0.88), e + dt.timedelta(days=10)),
        "after": (e + dt.timedelta(days=5), e + dt.timedelta(days=100)),
    }[phase]


PHASE_CN = {"presale": "售前/商机阶段", "kickoff": "立项启动阶段", "design": "需求调研与方案设计阶段", "build": "开发实施阶段",
            "launch": "测试上线阶段", "accept": "验收阶段", "after": "上线后运维/收尾阶段"}

# Windows where needle storylines own the topic: keep generic jobs away from them.
NEEDLE_GUARD = {"瀚海物流·华东智能调度平台（一期）", "星河数据·数据服务采购", "瀚海物流·华南推广", "天枢云·云资源采购"}


def _jid(*parts) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:10]


HOOKS = [
    "其中有人临时请假/出差，导致时间要调整", "客户方临时换了对接人", "涉及一个具体的数字指标（如准确率、时延、成本）的争论", "有人把附件发错了版本，随后补发",
    "会议时间和另一个会议冲突，需要改期", "邮件里提到一个具体的系统版本号或服务器配置", "有一封回复只有一两句话", "涉及预算或费用的具体金额",
    "有人提出一个不同意见，最后折中", "提到上一次会议遗留的事项", "有人在晚上或周末回复", "邮件中提到一个具体的地点（会议室、客户现场、城市）",
    "涉及跨部门协调（财务/法务/采购/HR中的一个）", "有人追问进度，语气略急", "林舟给出明确的决定和截止时间", "其中一封邮件是转发给第三人并加了说明",
    "提到一个风险以及对应的应对措施", "提到需要准备的材料清单", "有人感谢对方的支持", "涉及一个小失误以及补救", "提到客户的高层关注",
    "提到节假日前后的时间安排", "提到一个数据样例或截图里发现的问题", "邮件里有一个简短的待办列表", "提到和竞争对手的比较", "提到合同或付款节点",
]


def plan_jobs(seed: int = 7, scale: float = 1.0, pas: int = 1) -> list[dict]:
    rng = random.Random(seed)
    jobs = []
    # ---- project threads
    for proj in B.PROJECTS:
        if proj["name"] in NEEDLE_GUARD or not proj.get("org"):
            continue
        org = ORG[proj["org"]]
        s, e = dt.date.fromisoformat(proj["start"]), dt.date.fromisoformat(proj["end"])
        months = max(2.0, (min(e, END) - s).days / 30.0)
        weights = {"presale": 0.10, "kickoff": 0.08, "design": 0.17, "build": 0.33, "launch": 0.10, "accept": 0.12, "after": 0.10}
        total = int(round(months * 13 * scale))
        for phase, w in weights.items():
            lo, hi = _phase_window(proj, phase)
            lo, hi = max(lo, START), min(hi, END)
            if hi <= lo:
                continue
            scen = [x for x in PROJECT_SCEN if x[0] == phase]
            for i in range(max(1, int(round(total * w)))):
                ph, title, desc, n, att, client = rng.choice(scen)
                contacts = [c["key"] for c in org["contacts"]]
                c1 = rng.choice(contacts[:3])
                pm = proj.get("pm") or "zhouheng"
                sales = proj.get("sales") or "wanglei"
                lead = proj.get("lead") or "fangyu"
                ppl = ["linzhou", pm, sales, lead] + rng.sample(TECH, 2) + rng.sample(IMPL, 1)
                if client:
                    ppl += [c1] + rng.sample([c for c in contacts if c != c1], min(1, len(contacts) - 1))
                if "法务" in desc:
                    ppl.append("zhengya")
                if "孙浩" in desc:
                    ppl.append("sunhao")
                if "杜明远" in desc:
                    ppl.append("dumingyuan")
                if "陈默" in desc:
                    ppl.append("chenmo")
                if "梁峰" in desc or "赵一鸣" in desc:
                    ppl += ["liangfeng", "zhaoyiming"]
                ppl = list(dict.fromkeys(ppl))
                d = rand_workday(rng, lo, hi)
                n2 = max(2, n + rng.choice([-1, 0, 0, 1]))
                desc2 = desc.format(c1=PEOPLE[c1]["name"], sales=PEOPLE[sales]["name"], lead=PEOPLE[lead]["name"],
                                    pm=PEOPLE[pm]["name"])
                jobs.append({
                    "jid": _jid("p", proj["name"], phase, i, d), "kind": "project", "category": "client" if client else "work",
                    "date": d.isoformat(), "project": proj["name"], "org": proj["org"], "people": ppl,
                    "owner_role": rng.choices(["sender", "recipient", "cc"], weights=[3, 5, 2])[0],
                    "title": title, "scenario": desc2, "phase": phase, "n": n2, "attach": att if att and rng.random() < 0.8 else None,
                    "length": rng.choice(["简短", "适中", "适中", "详细"]), "seed": rng.randrange(1 << 30), "model": FLASH,
                })
    # ---- internal non-project threads
    pick = {
        "team": lambda r: ["linzhou", "yangyu", "gucheng"] + r.sample(TEAM[1:], 3),
        "mgmt": lambda r: ["linzhou", "zhaoyiming", r.choice(["hanzhendong", "yaojia"]), r.choice(["dumingyuan", "guowen", "fanzhiqiang"]), "gucheng"],
        "hr": lambda r: ["linzhou", "liuqian", r.choice(["zhangrui", "pangli"]), "gucheng"] + r.sample(TEAM[1:], 1),
        "fin": lambda r: ["linzhou", "sunhao", r.choice(["mengyao", "fanzhiqiang"]), "yangyu"] + r.sample(TEAM[1:], 1),
        "admin": lambda r: ["linzhou", "malin", "houjia", "yangyu"],
        "product": lambda r: ["linzhou", "ouyangchen", r.choice(["xiabing", "yuelin"])] + r.sample(TEAM[1:], 1),
        "tech": lambda r: ["linzhou", "chenmo", r.choice(["liangfeng", "zhaoyiming"]), "xuekai"] + r.sample(TECH[1:], 1),
        "it": lambda r: ["linzhou", "leiming", "wudi", "gucheng"],
        "market": lambda r: ["linzhou", "bailu", "taoran"] + r.sample(TEAM[1:], 1),
        "proc": lambda r: ["linzhou", "xunuo", "yangyu", "sunhao"],
        "partner": lambda r: ["linzhou", r.choice(["wuhaoran", "daiwen"]), "gucheng", r.choice(["wanglei", "guowen"])],
        "assist": lambda r: ["linzhou", "yangyu", r.choice(["wanglei", "zhouheng", "fengtao"])],
    }
    n_internal = int(1100 * scale)
    for i in range(n_internal):
        title, desc, n, att, who = rng.choice(INTERNAL_SCEN)
        d = rand_workday(rng, START, END - dt.timedelta(days=2))
        if title in SEASONAL:
            for _ in range(200):
                if d.month in SEASONAL[title]:
                    break
                d = rand_workday(rng, START, END - dt.timedelta(days=2))
        ppl = list(dict.fromkeys(pick[who](rng)))
        ext = any(PEOPLE[k].get("org") not in (B.COMPANY, None) for k in ppl)
        jobs.append({
            "jid": _jid("i", title, i, d), "kind": "internal", "category": "client" if ext else "work", "date": d.isoformat(),
            "project": None, "org": PEOPLE[ppl[1]]["org"] if ext else None, "people": ppl,
            "owner_role": rng.choices(["sender", "recipient", "cc"], weights=[3, 5, 2])[0], "title": title,
            "scenario": desc, "n": max(2, n + rng.choice([-1, 0, 0, 1])), "attach": att if att and rng.random() < 0.8 else None,
            "length": rng.choice(["简短", "适中", "适中", "详细"]), "seed": rng.randrange(1 << 30), "model": FLASH,
        })
    # ---- presales opportunities
    for i in range(int(360 * scale)):
        prospect, industry, ctitle = rng.choice(PROSPECTS)
        title, desc, n, att = rng.choice(PRESALE_SCEN)
        d = rand_workday(rng, START, END - dt.timedelta(days=2))
        ppl = ["linzhou", rng.choice(["wanglei", "zhongling", "shenhang", "guowen"]), rng.choice(TEAM), rng.choice(TEAM), "gucheng"]
        ppl = list(dict.fromkeys(ppl))
        jobs.append({
            "jid": _jid("s", prospect, title, i, d), "kind": "presale", "category": "work", "date": d.isoformat(),
            "project": None, "org": prospect, "people": ppl, "owner_role": rng.choices(["sender", "recipient", "cc"], weights=[3, 5, 2])[0],
            "title": title, "scenario": desc.format(prospect=prospect, industry=industry) + f"（客户方对接人为{ctitle}，本线程只有云帆内部人员）",
            "n": max(2, n + rng.choice([-1, 0, 0, 1])), "attach": att if att and rng.random() < 0.7 else None,
            "length": rng.choice(["简短", "适中", "适中", "详细"]), "seed": rng.randrange(1 << 30), "model": FLASH,
        })
    # ---- family & friends (personal mailbox)
    for i in range(int(330 * scale)):
        title, desc, n = rng.choice(FAMILY_SCEN)
        d = rand_day(rng, START, END - dt.timedelta(days=3))
        if title == "回老家过年":
            y = rng.choice([2024, 2025, 2026])
            d = {2024: dt.date(2024, 1, 20), 2025: dt.date(2025, 1, 10), 2026: dt.date(2026, 1, 28)}[y] + dt.timedelta(days=rng.randrange(10))
        if title == "暑假旅行":
            y = rng.choice([2024, 2025, 2026])
            d = dt.date(y, 5, 20) + dt.timedelta(days=rng.randrange(40))
        who = rng.choice(["suqing", "suqing", "suqing", "linman", "suhang"]) if title in ("父亲体检", "给父母买东西", "亲戚来京") else "suqing"
        ppl = ["linzhou", who] + (["suqing"] if who != "suqing" and rng.random() < 0.5 else [])
        ppl = list(dict.fromkeys(ppl))
        jobs.append({
            "jid": _jid("f", title, i, d), "kind": "family", "category": "family", "date": d.isoformat(), "project": None,
            "org": None, "people": ppl, "owner_role": rng.choice(["sender", "recipient"]), "title": title,
            "scenario": desc.format(who=PEOPLE[who]["name"]), "n": max(2, n + rng.choice([-1, 0, 1])),
            "attach": None, "length": rng.choice(["简短", "简短", "适中"]), "seed": rng.randrange(1 << 30), "model": FLASH, "personal": True,
        })
    for i in range(int(150 * scale)):
        title, desc, n = rng.choice(FRIEND_SCEN)
        d = rand_day(rng, START, END - dt.timedelta(days=3))
        who = rng.choice([f["key"] for f in B.FRIENDS])
        ppl = ["linzhou", who]
        jobs.append({
            "jid": _jid("fr", title, i, d), "kind": "friend", "category": "family", "date": d.isoformat(), "project": None,
            "org": None, "people": ppl, "owner_role": rng.choice(["sender", "recipient"]), "title": title,
            "scenario": desc.format(who=PEOPLE[who]["name"]), "n": max(2, n + rng.choice([-1, 0, 1])), "attach": None,
            "length": rng.choice(["简短", "适中"]), "seed": rng.randrange(1 << 30), "model": FLASH, "personal": True,
        })
    if pas > 1:
        for j in jobs:
            j["jid"] = f"{pas}{j['jid'][1:]}"
            if rng.random() < 0.85:
                j["hook"] = rng.choice(HOOKS)
            j["model"] = FLASH if (pas == 2 and rng.random() < 0.5) else (LITE if pas >= 3 else CHEAP)
    jobs.sort(key=lambda j: (j["date"], j["jid"]))
    return jobs


OWNER_ROLE = {
    "sender": "是第一封邮件的发件人",
    "recipient": "是主要收件人，并在线程中至少回复一次",
    "cc": "在每封邮件的抄送里，可以不回复",
}
LENGTH = {"简短": "整体偏简短（每封30-200字）", "适中": "长短结合（每封50-400字）", "详细": "包含一两封内容翔实的长邮件（400-800字）"}


def build_prompt(job: dict) -> list[dict]:
    personal = job.get("personal")
    d = dt.date.fromisoformat(job["date"])
    lines = ["【背景】"]
    if personal:
        lines.append(
            f"邮箱主人：林舟，42岁，在北京工作（云帆智能科技解决方案部总监），个人邮箱 {B.OWNER['personal_email']}。"
            f"妻子苏晴是中学语文老师；儿子林小禾当时在育才实验小学读{grade_at(d)}（3班）；父亲林建华在江苏扬州，退休教师；"
            f"妹妹林曼在上海。家住北京朝阳区望京嘉禾园小区，开一辆远途T5 SUV。"
        )
    else:
        lines.append(
            "邮箱主人：林舟，42岁，云帆智能科技有限公司（北京，为物流、制造、能源、医疗等行业做智能调度与数据智能解决方案）"
            "解决方案部总监，上级是CTO赵一鸣。工作邮箱 linzhou@yunfan-tech.cn。"
        )
    if job.get("project"):
        proj = next(p for p in B.PROJECTS if p["name"] == job["project"])
        org = ORG[proj["org"]]
        lines.append(
            f"项目：{proj['name']}（客户：{org['full']}，{org['city']}）。简介：{proj['desc']}"
            + (f" 合同额 {proj['amount']}。" if proj.get("amount") else "")
            + f" 项目周期 {proj['start']} 至 {proj['end']}，当前处于{PHASE_CN[job['phase']]}。"
        )
    elif not personal:
        act = active_projects(d)
        if act:
            lines.append("当时云帆正在进行的客户项目（需要时可提及）：" + "、".join(p["name"] for p in act[:6]) + "。")
    lines.append("【参与人】（只能使用下面这些人和邮箱）")
    for k in job["people"]:
        lines.append(_people_line(k, personal))
    lines.append(f"【场景】{job['title']}：{job['scenario']}" + (f"（可以自然地包含这个细节：{job['hook']}）" if job.get("hook") else ""))
    lines.append(f"【时间】第一封邮件发送于 {d.isoformat()}（{weekday_cn(d)}）。")
    req = f"【要求】写一个包含 {job['n']} 封邮件的线程；林舟{OWNER_ROLE[job['owner_role']]}；{LENGTH[job['length']]}。"
    if job.get("attach"):
        kind, what = job["attach"].split("|", 1)
        req += f"其中一封邮件需要带一个附件（{what}，kind={kind}）。"
    lines.append(req)
    lines.append(FORMAT)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def check_thread(text: str) -> dict:
    data = parse_json(text)
    msgs = data["messages"] if isinstance(data, dict) else data
    assert isinstance(msgs, list) and 1 <= len(msgs) <= 8
    for m in msgs:
        assert m.get("from") and m.get("subject") and m.get("body")
        assert isinstance(m.get("to", []), list)
    return {"messages": msgs}


async def run(jobs: list[dict], concurrency: int, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    lock = asyncio.Lock()
    results: dict[str, dict] = {}

    async with LLM(concurrency=concurrency) as llm:
        async def one(job):
            nonlocal done
            txt = await llm.chat(build_prompt(job), model=job["model"], temperature=0.9, max_tokens=5000,
                                 seed=job["seed"], tag=f"thread:{job['kind']}", validate=check_thread)
            if txt is None:
                return
            try:
                msgs = check_thread(txt)["messages"]
            except Exception:
                return
            async with lock:
                results[job["jid"]] = {**job, "messages": msgs}
                done += 1
                if done % 200 == 0:
                    print(f"[threads] {done}/{len(jobs)} cost=${llm.cost:.3f}", flush=True)

        await asyncio.gather(*(one(j) for j in jobs))
        print(f"[threads] finished {len(results)}/{len(jobs)} new-cost=${llm.cost:.3f} calls={llm.calls} hits={llm.hits} fail={llm.fail}")
    with open(out, "w", encoding="utf-8") as fh:
        for j in jobs:
            if j["jid"] in results:
                fh.write(json.dumps(results[j["jid"]], ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=48)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--pass", dest="pas", type=int, default=1)
    args = ap.parse_args()
    jobs = plan_jobs(seed=6 + args.pas, scale=args.scale, pas=args.pas)
    print(f"planned {len(jobs)} jobs; est emails {sum(j['n'] for j in jobs)}")
    if args.show:
        from collections import Counter
        print(Counter(j["kind"] for j in jobs), Counter(j["category"] for j in jobs))
        print(build_prompt(jobs[len(jobs) // 2])[1]["content"])
        return
    if args.limit:
        rng = random.Random(1)
        jobs = rng.sample(jobs, args.limit)
    name = "threads.jsonl" if args.pas == 1 else f"threads{args.pas}.jsonl"
    asyncio.run(run(jobs, args.concurrency, GEN / ("threads.sample.jsonl" if args.limit else name)))


if __name__ == "__main__":
    main()
