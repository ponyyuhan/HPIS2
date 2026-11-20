"""The fictional world of the demo mailbox (persona, people, organisations, projects).

Everything here is invented. ``python -m demo.corpus.bible`` writes demo/data/bible.json.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
AS_OF = "2026-11-20T18:00:00+08:00"
START = "2024-01-01T00:00:00+08:00"
COMPANY = "云帆智能科技有限公司"
CO_SHORT = "云帆智能"
CO_DOMAIN = "yunfan-tech.cn"
CO_ADDR = "北京市海淀区中关村软件园二期 云帆大厦6层"

OWNER = {
    "key": "linzhou",
    "name": "林舟",
    "age": 42,
    "born": 1984,
    "title": "解决方案部总监",
    "dept": "解决方案部",
    "org": COMPANY,
    "email": "linzhou@yunfan-tech.cn",
    "personal_email": "linzhou84@lanmail.cn",
    "mobile": "138****5521",
    "city": "北京",
    "home": "北京市朝阳区望京街道 嘉禾园小区7号楼2单元1102",
    "car": "远途 T5 SUV（京N·8K2**）",
    "cards": {
        "星辰银行信用卡": "**** 6411",
        "海川银行信用卡": "**** 2087",
        "星辰银行储蓄卡": "**** 3390",
    },
    "frequent_flyer": "蓝鲸航空 鲸享会 金卡 JW88****215",
}

FAMILY = [
    {
        "key": "suqing",
        "name": "苏晴",
        "relation": "妻子",
        "job": "北京市朝阳区育英中学 语文老师",
        "email": "suqing_1986@lanmail.cn",
        "mobile": "139****2716",
    },
    {
        "key": "linxiaohe",
        "name": "林小禾",
        "relation": "儿子",
        "age": 10,
        "school": "育才实验小学",
        "class": "四年级3班",
        "note": "2024年9月读二年级，2025年9月三年级，2026年9月升入四年级",
    },
    {"key": "linman", "name": "林曼", "relation": "妹妹", "email": "linman_sh@lanmail.cn", "city": "上海"},
    {"key": "linfather", "name": "林建华", "relation": "父亲", "city": "江苏扬州", "note": "退休教师，高血压"},
    {"key": "suhang", "name": "苏杭", "relation": "妻弟", "email": "suhang@lanmail.cn"},
]

FRIENDS = [
    {"key": "weidong", "name": "韦东", "relation": "大学室友", "email": "weidong1984@lanmail.cn", "org": "某设计院"},
    {"key": "huosiyuan", "name": "霍思远", "relation": "高中同学", "email": "huosiyuan@lanmail.cn", "org": "扬州"},
    {"key": "qiaoyu", "name": "乔宇", "relation": "前同事/好友", "email": "qiaoyu.pm@lanmail.cn", "org": "创业公司"},
    {"key": "dingning", "name": "丁宁", "relation": "邻居/羽毛球球友", "email": "dingning88@lanmail.cn"},
]


def P(key, name, title, dept, mobile_tail=None, **kw):
    d = {"key": key, "name": name, "title": title, "dept": dept, "email": f"{key}@{CO_DOMAIN}", "org": COMPANY}
    if mobile_tail:
        d["mobile"] = mobile_tail
    d.update(kw)
    return d


COLLEAGUES = [
    P("hanzhendong", "韩振东", "总裁", "总裁办", "139****0601"),
    P("yaojia", "姚佳", "总裁助理", "总裁办", "136****2203"),
    P("zhaoyiming", "赵一鸣", "CTO", "技术中心", "138****7310"),
    P("chenmo", "陈默", "首席架构师", "技术中心", "137****4482"),
    P("liangfeng", "梁峰", "研发总监", "技术中心", "135****9017"),
    P("jiangtao", "蒋涛", "后端开发组长", "技术中心"),
    P("xielei", "谢磊", "高级开发工程师", "技术中心"),
    P("luoxiaotong", "罗晓彤", "前端开发工程师", "技术中心"),
    P("tianye", "田野", "数据工程师", "技术中心"),
    P("gaoyuan", "高远", "数据开发组长", "技术中心"),
    P("xuzihao", "徐子豪", "算法负责人", "技术中心", "186****3325"),
    P("weiran", "魏然", "算法工程师", "技术中心"),
    P("panting", "潘婷", "测试经理", "技术中心"),
    P("wudi", "吴迪", "运维/SRE负责人", "技术中心", "158****6670"),
    P("gucheng", "顾城", "解决方案部副总监", "解决方案部", "139****8826", reports_to="linzhou"),
    P("fangyu", "方宇", "高级解决方案经理", "解决方案部", "137****1290", reports_to="linzhou"),
    P("yezihan", "叶子涵", "解决方案经理", "解决方案部", "152****4471", reports_to="linzhou"),
    P("qinchuan", "秦川", "解决方案经理", "解决方案部", "135****6638", reports_to="linzhou"),
    P("tangyue", "唐悦", "解决方案经理", "解决方案部", "188****2054", reports_to="linzhou"),
    P("songjia", "宋佳", "解决方案经理（医疗行业）", "解决方案部", reports_to="linzhou"),
    P("qiuyang", "邱阳", "售前工程师", "解决方案部", reports_to="linzhou"),
    P("baiyang", "白杨", "售前工程师", "解决方案部", reports_to="linzhou"),
    P("yangyu", "杨雨", "部门助理", "解决方案部", "131****7759", reports_to="linzhou"),
    P("dumingyuan", "杜明远", "交付总监", "交付中心", "139****5160"),
    P("zhouheng", "周衡", "高级项目经理", "交付中心", "138****2417"),
    P("fengtao", "冯涛", "项目经理", "交付中心", "136****8803"),
    P("shaomin", "邵敏", "项目经理", "交付中心", "159****3328"),
    P("kongxiang", "孔祥", "实施工程师", "交付中心"),
    P("luhao", "卢昊", "实施工程师", "交付中心"),
    P("guowen", "郭文", "销售副总裁", "销售部", "139****1188"),
    P("wanglei", "王蕾", "大客户总监（物流行业）", "销售部", "137****6025"),
    P("zhongling", "钟灵", "大客户经理（制造/能源）", "销售部", "135****0942"),
    P("shenhang", "沈航", "大客户经理（医疗/文旅）", "销售部", "186****7713"),
    P("bailu", "白露", "市场总监", "市场部"),
    P("taoran", "陶然", "品牌经理", "市场部"),
    P("pangli", "庞丽", "人力资源总监", "人力资源部"),
    P("liuqian", "刘倩", "HRBP", "人力资源部", "150****3361"),
    P("zhangrui", "张蕊", "招聘专员", "人力资源部"),
    P("fanzhiqiang", "范志强", "财务总监", "财务部"),
    P("sunhao", "孙浩", "财务经理", "财务部", "138****9042"),
    P("mengyao", "孟瑶", "会计", "财务部"),
    P("zhengya", "郑雅", "法务经理", "法务部", "139****6610"),
    P("xunuo", "许诺", "采购经理", "采购部", "137****2235"),
    P("malin", "马琳", "行政主管", "行政部"),
    P("houjia", "侯佳", "行政专员", "行政部"),
    P("leiming", "雷鸣", "信息部经理", "信息部"),
    P("ouyangchen", "欧阳晨", "产品总监", "产品部"),
    P("xiabing", "夏冰", "产品经理", "产品部"),
    P("yuelin", "岳琳", "产品经理", "产品部"),
    P("xuekai", "薛凯", "PMO经理", "交付中心"),
]


def C(key, name, title, domain, org, mobile=None):
    d = {"key": key, "name": name, "title": title, "email": f"{key}@{domain}", "org": org}
    if mobile:
        d["mobile"] = mobile
    return d


ORGS = [
    {
        "name": "瀚海物流",
        "full": "瀚海物流集团股份有限公司",
        "type": "client",
        "domain": "hanhai-logistics.com",
        "industry": "物流",
        "city": "上海",
        "note": "核心客户。2024年运输可视化项目，2026年上半年WMS升级，2026年8月起华东智能调度平台（一期），11月起洽谈华南推广。",
        "contacts": [
            C("maguodong", "马国栋", "信息技术部总经理", "hanhai-logistics.com", "瀚海物流", "139****3011"),
            C("yanning", "严宁", "IT项目经理", "hanhai-logistics.com", "瀚海物流", "137****5598"),
            C("chengyuan", "程远", "运营副总裁", "hanhai-logistics.com", "瀚海物流"),
            C("huangzhen", "黄振", "数据平台负责人", "hanhai-logistics.com", "瀚海物流"),
            C("lvqiang", "吕强", "华东区运营总监", "hanhai-logistics.com", "瀚海物流"),
            C("chenlixin", "陈立新", "华南区总经理", "hanhai-logistics.com", "瀚海物流", "138****6620"),
            C("caiqi", "蔡琪", "华南区总经理助理", "hanhai-logistics.com", "瀚海物流", "135****0817"),
            C("luomin", "罗敏", "采购经理", "hanhai-logistics.com", "瀚海物流"),
        ],
    },
    {
        "name": "远洋港务",
        "full": "远洋港务集团有限公司",
        "type": "client",
        "domain": "yuanyang-port.com",
        "industry": "港口",
        "city": "天津",
        "note": "2026年9月起智慧堆场规划项目。",
        "contacts": [
            C("caofeng", "曹锋", "信息部主任", "yuanyang-port.com", "远洋港务"),
            C("gongxue", "龚雪", "信息部项目主管", "yuanyang-port.com", "远洋港务"),
        ],
    },
    {
        "name": "华川制造",
        "full": "华川精密制造股份有限公司",
        "type": "client",
        "domain": "huachuan-mfg.com",
        "industry": "制造",
        "city": "苏州",
        "contacts": [
            C("dengkai", "邓凯", "数字化部部长", "huachuan-mfg.com", "华川制造"),
            C("liuqing", "柳青", "设备工程师", "huachuan-mfg.com", "华川制造"),
        ],
    },
    {
        "name": "青禾医疗",
        "full": "青禾医疗集团",
        "type": "client",
        "domain": "qinghe-medical.com",
        "industry": "医疗",
        "city": "杭州",
        "contacts": [
            C("fulan", "付兰", "信息科科长", "qinghe-medical.com", "青禾医疗"),
            C("yubo", "余波", "医务处副处长", "qinghe-medical.com", "青禾医疗"),
        ],
    },
    {
        "name": "北辰能源",
        "full": "北辰新能源集团有限公司",
        "type": "client",
        "domain": "beichen-energy.com",
        "industry": "能源",
        "city": "西安",
        "contacts": [
            C("yinzhigang", "尹志刚", "CIO", "beichen-energy.com", "北辰能源"),
            C("tangjing", "汤静", "数字化项目经理", "beichen-energy.com", "北辰能源"),
        ],
    },
    {
        "name": "金穗农业",
        "full": "金穗农业科技集团",
        "type": "client",
        "domain": "jinsui-agri.com",
        "industry": "农业/冷链",
        "city": "郑州",
        "contacts": [C("jijun", "季军", "信息总监", "jinsui-agri.com", "金穗农业")],
    },
    {
        "name": "南湖文旅",
        "full": "南湖文旅集团",
        "type": "client",
        "domain": "nanhu-culture.com",
        "industry": "文旅",
        "city": "嘉兴",
        "contacts": [C("qiyue", "齐悦", "数字化中心主任", "nanhu-culture.com", "南湖文旅")],
    },
    {
        "name": "明德教育",
        "full": "明德教育集团",
        "type": "client",
        "domain": "mingde-edu.com",
        "industry": "教育",
        "city": "北京",
        "contacts": [C("shilei", "史磊", "信息中心主任", "mingde-edu.com", "明德教育")],
    },
    {
        "name": "东岳化工",
        "full": "东岳化工股份有限公司",
        "type": "client",
        "domain": "dongyue-chem.com",
        "industry": "化工",
        "city": "淄博",
        "contacts": [C("hejun", "贺军", "设备部部长", "dongyue-chem.com", "东岳化工")],
    },
    {
        "name": "星河数据",
        "full": "星河数据技术有限公司",
        "type": "vendor",
        "domain": "xinghe-data.com",
        "industry": "数据服务",
        "city": "北京",
        "note": "数据服务供应商。2026年签订数据服务合同 YF-XH-2026-031。",
        "contacts": [
            C("geming", "葛明", "销售总监", "xinghe-data.com", "星河数据", "139****4458"),
            C("jiangwen", "姜雯", "商务经理", "xinghe-data.com", "星河数据"),
            C("luobin", "骆斌", "交付项目经理", "xinghe-data.com", "星河数据"),
        ],
    },
    {
        "name": "天枢云",
        "full": "天枢云计算有限公司",
        "type": "vendor",
        "domain": "tianshu-cloud.com",
        "industry": "云服务",
        "city": "杭州",
        "note": "云资源供应商。2026年5月签订云资源服务合同 YF-TS-2026-012。",
        "contacts": [
            C("pengbo", "彭博", "客户经理", "tianshu-cloud.com", "天枢云"),
            C("wanli", "万里", "技术支持工程师", "tianshu-cloud.com", "天枢云"),
        ],
    },
    {
        "name": "智图科技",
        "full": "智图空间信息科技有限公司",
        "type": "partner",
        "domain": "zhitu-tech.com",
        "industry": "GIS/地图",
        "city": "武汉",
        "contacts": [C("wuhaoran", "伍浩然", "渠道合作经理", "zhitu-tech.com", "智图科技")],
    },
    {
        "name": "远见咨询",
        "full": "远见管理咨询有限公司",
        "type": "partner",
        "domain": "yuanjian-consulting.com",
        "industry": "咨询",
        "city": "上海",
        "contacts": [C("daiwen", "戴文", "合伙人", "yuanjian-consulting.com", "远见咨询")],
    },
]

SERVICES = [
    {"name": "星辰银行", "kind": "bank", "sender": "星辰银行信用卡中心", "email": "creditcard@xingchen-bank.com",
     "note": "主力信用卡 **** 6411，账单日每月5日，到期还款日每月25日；另有储蓄卡 **** 3390（工资卡）"},
    {"name": "海川银行", "kind": "bank", "sender": "海川银行信用卡", "email": "service@haichuan-bank.com",
     "note": "副卡信用卡 **** 2087，账单日每月12日，到期还款日次月1日；以及房贷还款账户"},
    {"name": "安康体检中心", "kind": "health", "sender": "安康体检中心", "email": "report@ankang-health.com",
     "note": "公司年度体检合作机构，每年4月体检"},
    {"name": "蓝鲸航空", "kind": "airline", "sender": "蓝鲸航空", "email": "noreply@bluewhale-air.com", "code": "JW"},
    {"name": "悦庭酒店", "kind": "hotel", "sender": "悦庭酒店集团", "email": "reservation@yueting-hotels.com"},
    {"name": "云购商城", "kind": "ecommerce", "sender": "云购商城", "email": "service@yungou-mall.com"},
    {"name": "极速快递", "kind": "express", "sender": "极速快递", "email": "notice@jisu-express.com"},
    {"name": "育才实验小学", "kind": "school", "sender": "育才实验小学家校通", "email": "notice@yucai-school.edu.cn",
     "note": "林小禾所在学校；2026-2027学年 四年级3班，班主任何静（语文），数学老师黄晓东"},
    {"name": "康宁人寿", "kind": "insurance", "sender": "康宁人寿保险", "email": "service@kangning-life.com",
     "note": "家庭重疾险、医疗险；车险也在康宁财险"},
    {"name": "畅联通信", "kind": "telecom", "sender": "畅联通信", "email": "bill@changlian-tel.com"},
    {"name": "京华燃气", "kind": "utility", "sender": "京华燃气", "email": "service@jinghua-gas.com"},
    {"name": "城市电力服务", "kind": "utility", "sender": "城市电力服务", "email": "ebill@city-power.cn"},
    {"name": "嘉禾物业", "kind": "property", "sender": "嘉禾物业服务中心", "email": "notice@jiahe-pm.com"},
    {"name": "速达出行", "kind": "ride", "sender": "速达出行", "email": "receipt@suda-go.com"},
    {"name": "饭点外卖", "kind": "food", "sender": "饭点外卖", "email": "noreply@fandian-waimai.com"},
    {"name": "青柚视频", "kind": "media", "sender": "青柚视频", "email": "vip@qingyou-video.com"},
    {"name": "畅行火车票", "kind": "rail", "sender": "畅行火车票", "email": "ticket@changxing-rail.com"},
    {"name": "云盘", "kind": "app", "sender": "鲸云盘", "email": "no-reply@jingyunpan.com"},
    {"name": "启明星少儿编程", "kind": "training", "sender": "启明星少儿编程", "email": "promo@qimingxing-kids.com"},
    {"name": "乐思英语", "kind": "training", "sender": "乐思英语", "email": "hello@lesi-english.com"},
    {"name": "动力健身", "kind": "fitness", "sender": "动力健身", "email": "club@dongli-fitness.com"},
    {"name": "远途汽车", "kind": "auto", "sender": "远途汽车北京望京4S店", "email": "service@yuantu-auto.com"},
    {"name": "书香书城", "kind": "books", "sender": "书香书城", "email": "promo@shuxiang-books.com"},
]

NEWSLETTERS = [
    {"name": "智运观察", "email": "newsletter@zhiyun-guancha.com", "freq": "weekly", "topic": "物流科技、智能调度、仓储自动化、供应链"},
    {"name": "CIO内参", "email": "weekly@cio-neican.com", "freq": "weekly", "topic": "企业数字化转型、CIO管理、IT预算与采购"},
    {"name": "AI前沿速递", "email": "digest@ai-frontier.cn", "freq": "weekly", "topic": "大模型、AI工程化、AI应用落地"},
    {"name": "晨读财经", "email": "morning@chendu-finance.com", "freq": "daily", "topic": "宏观经济、资本市场、行业财经新闻"},
    {"name": "极客晚报", "email": "news@geek-evening.com", "freq": "daily", "topic": "科技公司动态、开发者工具、开源"},
    {"name": "云帆内刊", "email": "neikan@yunfan-tech.cn", "freq": "biweekly", "topic": "公司内部新闻、项目喜报、员工风采"},
    {"name": "华夏物流科技联盟", "email": "info@hxlst.org.cn", "freq": "monthly", "topic": "行业会议、标准发布、会员活动"},
    {"name": "供应链早报", "email": "daily@scm-zaobao.com", "freq": "daily", "topic": "供应链、货运价格、港口与航运、仓储地产"},
    {"name": "运筹与优化", "email": "letter@or-youhua.org", "freq": "weekly", "topic": "运筹优化算法、车辆路径问题、求解器、学术动态"},
    {"name": "数据智能周报", "email": "weekly@data-zhineng.com", "freq": "weekly", "topic": "数据平台、数据治理、实时计算、向量数据库"},
    {"name": "管理者读书会", "email": "club@guanli-dushu.com", "freq": "weekly", "topic": "管理类书籍解读、领导力、组织管理"},
    {"name": "亲子成长周刊", "email": "weekly@qinzi-chengzhang.com", "freq": "weekly", "topic": "小学教育、亲子沟通、儿童阅读、升学资讯"},
    {"name": "跑者周报", "email": "news@paozhe-weekly.com", "freq": "weekly", "topic": "跑步训练、赛事报名、运动健康"},
    {"name": "智聘网职位推荐", "email": "jobs@zhipin-wang.com", "freq": "weekly", "topic": "高端职位推荐（解决方案总监、售前VP等）、薪酬报告"},
    {"name": "健康生活指南", "email": "tips@jiankang-zhinan.com", "freq": "weekly", "topic": "饮食营养、慢病预防、体检解读、睡眠"},
]

# Internal systems that send templated mail.
SYSTEMS = [
    {"name": "云帆OA", "email": "oa@yunfan-tech.cn"},
    {"name": "云帆日历", "email": "calendar@yunfan-tech.cn"},
    {"name": "IT服务台", "email": "itservice@yunfan-tech.cn"},
    {"name": "项目管理平台", "email": "pm-system@yunfan-tech.cn"},
    {"name": "云帆差旅平台", "email": "travel@yunfan-tech.cn"},
    {"name": "费用报销系统", "email": "expense@yunfan-tech.cn"},
    {"name": "云帆人力系统", "email": "hr-system@yunfan-tech.cn"},
]

# Client / internal projects. Dates are ISO; phases are coarse.
PROJECTS = [
    {"name": "瀚海物流·运输可视化平台", "org": "瀚海物流", "pm": "fengtao", "sales": "wanglei", "lead": "fangyu",
     "start": "2024-03-01", "end": "2024-12-20", "amount": "¥2,450,000",
     "desc": "为瀚海物流干线运输车辆搭建在途可视化与异常预警平台，接入约3200台车载终端。"},
    {"name": "瀚海物流·WMS升级", "org": "瀚海物流", "pm": "shaomin", "sales": "wanglei", "lead": "fangyu",
     "start": "2026-02-20", "end": "2026-07-31", "amount": "¥1,980,000",
     "desc": "瀚海物流仓储管理系统升级：波次策略优化、库位推荐、与自动化分拣线对接。"},
    {"name": "瀚海物流·华东智能调度平台（一期）", "org": "瀚海物流", "pm": "zhouheng", "sales": "wanglei", "lead": "linzhou",
     "start": "2026-08-17", "end": "2026-12-31", "amount": "¥3,280,000", "needle": True,
     "desc": "华东区运输智能调度：订单-车辆匹配、路径优化、空驶率控制；一期覆盖上海嘉定、苏州昆山、杭州萧山三仓。"},
    {"name": "远洋港务·智慧堆场规划", "org": "远洋港务", "pm": "fengtao", "sales": "wanglei", "lead": "gucheng",
     "start": "2026-09-01", "end": "2027-03-31", "amount": "¥2,760,000",
     "desc": "集装箱堆场智能规划：堆位分配、翻箱率优化、岸桥-集卡协同调度。"},
    {"name": "华川制造·MES数据中台", "org": "华川制造", "pm": "shaomin", "sales": "zhongling", "lead": "qinchuan",
     "start": "2025-03-10", "end": "2025-11-28", "amount": "¥2,150,000",
     "desc": "整合华川5条产线MES、ERP、质检数据，建设数据中台与生产驾驶舱。"},
    {"name": "华川制造·设备预测性维护", "org": "华川制造", "pm": "fengtao", "sales": "zhongling", "lead": "qinchuan",
     "start": "2026-03-16", "end": "2026-10-30", "amount": "¥1,620,000",
     "desc": "基于振动与温度传感器的关键设备故障预警，覆盖二号、三号产线共46台设备。"},
    {"name": "青禾医疗·智能分诊问答", "org": "青禾医疗", "pm": "shaomin", "sales": "shenhang", "lead": "songjia",
     "start": "2025-06-02", "end": "2026-02-27", "amount": "¥1,380,000",
     "desc": "门诊智能分诊与患者问答助手，接入HIS预约挂号，覆盖青禾医疗3家院区。"},
    {"name": "北辰能源·能耗监测平台", "org": "北辰能源", "pm": "zhouheng", "sales": "zhongling", "lead": "tangyue",
     "start": "2024-03-18", "end": "2024-10-31", "amount": "¥1,760,000",
     "desc": "集团12个厂站能耗数据采集、分析与碳排放报表。"},
    {"name": "北辰能源·场站巡检AI", "org": "北辰能源", "pm": "zhouheng", "sales": "zhongling", "lead": "tangyue",
     "start": "2026-06-22", "end": "2027-01-29", "amount": "¥2,040,000",
     "desc": "光伏/风电场站无人机巡检图像缺陷识别与工单闭环。"},
    {"name": "金穗农业·冷链溯源", "org": "金穗农业", "pm": "fengtao", "sales": "zhongling", "lead": "yezihan",
     "start": "2025-01-06", "end": "2025-08-29", "amount": "¥980,000",
     "desc": "生鲜冷链温控数据采集与批次溯源系统。"},
    {"name": "南湖文旅·智慧景区", "org": "南湖文旅", "pm": "shaomin", "sales": "shenhang", "lead": "yezihan",
     "start": "2024-06-03", "end": "2025-04-30", "amount": "¥1,250,000",
     "desc": "景区客流预测、票务分析与智能导览。"},
    {"name": "明德教育·智能排课", "org": "明德教育", "pm": "zhouheng", "sales": "shenhang", "lead": "tangyue",
     "start": "2025-04-14", "end": "2025-09-30", "amount": "¥860,000",
     "desc": "集团18所学校的智能排课与教室资源调度。"},
    {"name": "东岳化工·安全巡检", "org": "东岳化工", "pm": "fengtao", "sales": "zhongling", "lead": "qinchuan",
     "start": "2024-09-02", "end": "2025-03-31", "amount": "¥1,120,000",
     "desc": "化工园区巡检路线优化与隐患识别。"},
    {"name": "星河数据·数据服务采购", "org": "星河数据", "pm": "zhouheng", "sales": None, "lead": "linzhou",
     "start": "2026-07-06", "end": "2027-06-30", "amount": "¥1,860,000", "needle": True,
     "desc": "采购星河数据的道路路网与货运时效数据服务，用于调度引擎训练与评估（合同 YF-XH-2026-031）。"},
    {"name": "天枢云·云资源采购", "org": "天枢云", "pm": None, "sales": None, "lead": "linzhou",
     "start": "2026-04-13", "end": "2027-05-31", "amount": "¥960,000",
     "desc": "解决方案部演示与压测环境云资源（合同 YF-TS-2026-012）。"},
    {"name": "云帆调度引擎3.0产品化", "org": None, "pm": "xuekai", "sales": None, "lead": "chenmo",
     "start": "2025-09-01", "end": "2026-06-30", "amount": None,
     "desc": "把各物流项目中的调度算法沉淀为标准产品，统一API与配置中心。"},
    {"name": "解决方案知识库建设", "org": None, "pm": None, "sales": None, "lead": "gucheng",
     "start": "2024-04-01", "end": "2026-12-31", "amount": None,
     "desc": "部门方案、案例、报价模板的知识库与检索。"},
    {"name": "瀚海物流·华南推广", "org": "瀚海物流", "pm": "zhouheng", "sales": "wanglei", "lead": "linzhou",
     "start": "2026-11-01", "end": "2027-06-30", "amount": None, "needle": True,
     "desc": "华东调度平台模式向瀚海华南区复制的前期沟通。"},
]

AIRPORTS = {"北京大兴": "PKX", "北京首都": "PEK", "深圳宝安": "SZX", "上海虹桥": "SHA", "上海浦东": "PVG",
            "杭州萧山": "HGH", "西安咸阳": "XIY", "天津滨海": "TSN", "郑州新郑": "CGO", "扬州泰州": "YTY", "成都天府": "TFU"}

HOTELS = {
    "深圳": "悦庭酒店深圳南山店（深圳市南山区科技园科苑南路2188号）",
    "上海": "悦庭酒店上海虹桥店（上海市闵行区申长路899号）",
    "杭州": "悦庭酒店杭州滨江店（杭州市滨江区江南大道1166号）",
    "苏州": "悦庭酒店苏州园区店（苏州市工业园区星湖街328号）",
    "西安": "悦庭酒店西安高新店（西安市雁塔区锦业路66号）",
    "天津": "悦庭酒店天津滨海店（天津市滨海新区第二大街18号）",
    "郑州": "悦庭酒店郑州郑东店（郑州市郑东新区商务外环路20号）",
}


def all_people() -> dict:
    out = {OWNER["key"]: {**OWNER, "dept": OWNER["dept"]}}
    for p in COLLEAGUES:
        out[p["key"]] = p
    for o in ORGS:
        for c in o["contacts"]:
            out[c["key"]] = c
    for f in FAMILY + FRIENDS:
        out[f["key"]] = f
    return out


def build() -> dict:
    bible = {
        "as_of": AS_OF,
        "start": START,
        "windows": {
            "过去三个月": ["2026-08-20", "2026-11-20"],
            "上个月": ["2026-10-01", "2026-10-31"],
            "下周": ["2026-11-23", "2026-11-29"],
            "本周": ["2026-11-16", "2026-11-22"],
        },
        "owner": OWNER,
        "company": {"name": COMPANY, "short": CO_SHORT, "domain": CO_DOMAIN, "address": CO_ADDR},
        "family": FAMILY,
        "friends": FRIENDS,
        "colleagues": COLLEAGUES,
        "orgs": ORGS,
        "services": SERVICES,
        "newsletters": NEWSLETTERS,
        "systems": SYSTEMS,
        "projects": PROJECTS,
        "hotels": HOTELS,
        "airports": AIRPORTS,
        "note": "全部人物、机构、品牌、项目、号码均为虚构；卡号与手机号已脱敏。",
    }
    return bible


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "bible.json").write_text(json.dumps(build(), ensure_ascii=False, indent=1), "utf-8")
    print("wrote", DATA / "bible.json", len(all_people()), "people")
