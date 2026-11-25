"""Stage 4 (photos): assemble 林舟's phone album -> demo/data/devices/photo.jsonl + images.

    $HPIS_DEVICES_BUILD/venv/bin/python -I demo/corpus/devices/photo_build.py

Inputs: work/pool.jsonl (candidates), the vision answers cached by photo_caption.py
and the clean 960 px copies from photo_fetch.py. Every candidate is matched to a
scene of the timeline (trips, weekend outings, runs, everyday Beijing life) under
hard constraints taken from what the vision model saw: region and script of any
visible text, landmark, season, time of day, no identifiable faces, no logos.
Capture times are set inside the trip windows (local time and UTC offset).

Writes photo.jsonl, photos/img/{id}.jpg (long side 960), photos/thumb/{id}.jpg
(long side 360) and photos/ATTRIBUTION.md. Needs Pillow (the throwaway venv).

Incremental by default: photos already in photo.jsonl keep their date, place and
text as long as their trip, outing or day still exists in the timeline; scenes of a
trip that lost all its photos (e.g. a trip moved to other dates) are filled again,
and every everyday photo that lands on a day away from Beijing is replaced by
exactly one new one. ``--fresh`` rebuilds the whole album from scratch.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.corpus.devices import life, sport  # noqa: E402
from demo.corpus.devices.common import (  # noqa: E402
    OUT, WORK, cn_date, daterange, iso, read_jsonl, tz, weekday_cn, write_jsonl,
)
from demo.corpus.devices.photo_caption import MODEL, TYPES, cache_path  # noqa: E402

D = dt.date.fromisoformat
SEED = "linzhou-phone-v1"
QUALITY_FULL = 80
QUALITY_THUMB = 78

# ---------------------------------------------------------------- gazetteer
CITY_INFO = {  # city -> (country, lat, lon)
    "北京": ("中国", 39.9042, 116.4074), "上海": ("中国", 31.2304, 121.4737), "苏州": ("中国", 31.2990, 120.5853),
    "杭州": ("中国", 30.2741, 120.1551), "西安": ("中国", 34.3416, 108.9398), "郑州": ("中国", 34.7466, 113.6253),
    "深圳": ("中国", 22.5431, 114.0579), "天津": ("中国", 39.1255, 117.1901), "三亚": ("中国", 18.2528, 109.5119),
    "扬州": ("中国", 32.3942, 119.4129), "秦皇岛": ("中国", 39.8270, 119.4860),
    "慕尼黑": ("德国", 48.1374, 11.5755), "柏林": ("德国", 52.5200, 13.4050), "法兰克福": ("德国", 50.1109, 8.6821),
    "首尔": ("韩国", 37.5665, 126.9780), "釜山": ("韩国", 35.1796, 129.0756),
    "东京": ("日本", 35.6762, 139.6503), "京都": ("日本", 35.0116, 135.7681), "奈良": ("日本", 34.6851, 135.8048),
}
POI = {  # place name -> (lat, lon)
    "英国花园": (48.1642, 11.6056), "慕尼黑展览中心": (48.1361, 11.6923), "玛利亚广场": (48.1374, 11.5755),
    "老城区": (48.1371, 11.5754), "王宫花园": (48.1424, 11.5810), "慕尼黑机场": (48.3538, 11.7861),
    "勃兰登堡门": (52.5163, 13.3777), "柏林电视塔": (52.5208, 13.4094), "米特区": (52.5200, 13.4050), "钟路区": (37.5729, 126.9794), "蒂尔加滕公园": (52.5145, 13.3501),
    "施普雷河畔": (52.5186, 13.4011), "普伦茨劳尔贝格": (52.5389, 13.4246), "胜利纪念柱": (52.5145, 13.3501),
    "美因河畔": (50.1072, 8.6826), "棕榈园": (50.1225, 8.6573), "罗马广场": (50.1105, 8.6822),
    "法兰克福机场": (50.0379, 8.5622), "法兰克福 金融区": (50.1100, 8.6710),
    "汉江公园": (37.5113, 126.9960), "益善洞": (37.5744, 126.9895), "北村韩屋村": (37.5826, 126.9830),
    "南山 首尔塔": (37.5512, 126.9882), "北岳山城郭路": (37.5937, 126.9714), "明洞": (37.5636, 126.9826),
    "弘大": (37.5563, 126.9236), "仁川机场": (37.4602, 126.4407), "汝矣岛": (37.5258, 126.9338),
    "海云台": (35.1587, 129.1604), "广安里": (35.1532, 129.1186), "釜山塔": (35.1008, 129.0324),
    "甘川洞文化村": (35.0975, 129.0106), "札嘎其市场": (35.0968, 129.0306), "釜山站": (35.1151, 129.0422),
    "上野恩赐公园": (35.7156, 139.7745), "东京晴空塔": (35.7101, 139.8107), "隅田川": (35.7100, 139.8000),
    "浅草": (35.7148, 139.7967), "涩谷": (35.6595, 139.7005), "银座": (35.6717, 139.7650),
    "皇居外苑": (35.6852, 139.7528), "新宿御苑": (35.6852, 139.7101), "墨田水族馆": (35.7100, 139.8107),
    "羽田机场": (35.5494, 139.7798), "砧公园": (35.6386, 139.6237), "羽根木公园": (35.6602, 139.6577),
    "岚山竹林": (35.0170, 135.6713), "鸭川": (35.0050, 135.7710), "祇园": (35.0037, 135.7788),
    "醍醐寺": (34.9511, 135.8215), "京都府立植物园": (35.0490, 135.7640), "京都站": (34.9858, 135.7588),
    "奈良公园": (34.6851, 135.8430), "东大寺": (34.6890, 135.8398), "关西机场": (34.4320, 135.2304),
    "三亚湾": (18.2600, 109.4900), "亚龙湾": (18.2270, 109.6370), "大东海": (18.2190, 109.5170),
    "椰梦长廊": (18.2690, 109.4780), "鹿回头": (18.2160, 109.4980), "第一市场": (18.2530, 109.5050),
    "凤凰机场": (18.3029, 109.4122),
    "东关街": (32.3989, 119.4398), "瘦西湖": (32.4083, 119.4207), "家中": (32.3942, 119.4129),
    "陆家嘴": (31.2397, 121.4998), "外滩": (31.2400, 121.4900), "徐汇滨江": (31.1834, 121.4630),
    "虹桥": (31.1979, 121.3363), "金鸡湖": (31.3157, 120.6715), "平江路": (31.3146, 120.6286),
    "苏州园区": (31.3200, 120.7000), "西湖": (30.2500, 120.1500), "滨江": (30.2084, 120.2120),
    "钱塘江": (30.2350, 120.2010), "曲江": (34.2050, 108.9820), "高新区": (34.2240, 108.8860),
    "郑东新区": (34.7600, 113.7300), "南山区": (22.5333, 113.9304), "深圳湾": (22.5110, 113.9500),
    "海河": (39.1360, 117.2030), "五大道": (39.1132, 117.1918),
}
LANDMARKS = {  # tokens that may appear in the model's landmark / caption for photos of that city
    "北京": ["长城", "故宫", "天坛", "颐和园", "北海", "景山", "鸟巢", "水立方", "国家大剧院", "央视", "国贸", "天安门", "八达岭", "慕田峪"],
    "上海": ["外滩", "东方明珠", "陆家嘴", "上海中心", "金茂", "环球金融", "黄浦江"],
    "杭州": ["西湖", "雷峰塔", "断桥", "钱塘江"], "苏州": ["金鸡湖", "拙政园", "平江", "虎丘"],
    "西安": ["大雁塔", "钟楼", "城墙"], "深圳": ["深圳湾"], "扬州": ["瘦西湖", "东关街", "个园"],
    "三亚": ["亚龙湾", "天涯海角", "南山", "蜈支洲"], "秦皇岛": ["北戴河", "鸽子窝", "老虎石"], "天津": ["天津之眼", "海河", "五大道"],
    "慕尼黑": ["英国花园", "玛利亚", "圣母教堂", "宁芬堡", "慕尼黑"], "柏林": ["勃兰登堡", "电视塔", "胜利", "国会", "柏林墙", "施普雷", "蒂尔加滕", "柏林"],
    "法兰克福": ["美因", "罗马广场", "法兰克福"],
    "首尔": ["首尔塔", "南山", "景福宫", "汉江", "北村", "益善洞", "明洞", "首尔"], "釜山": ["釜山塔", "海云台", "广安", "甘川", "釜山"],
    "东京": ["晴空塔", "东京塔", "浅草", "上野", "涩谷", "银座", "皇居", "隅田川", "东京", "台场"],
    "京都": ["伏见", "清水寺", "岚山", "金阁", "银阁", "鸭川", "祇园", "醍醐", "竹林", "京都"], "奈良": ["东大寺", "奈良"],
}
ALL_LANDMARK_TOKENS = {tok: city for city, toks in LANDMARKS.items() for tok in toks}
FOREIGN_FAMOUS = re.compile(r"埃菲尔|自由女神|金门大桥|帝国大厦|伦敦|巴黎|纽约|悉尼|威尼斯|罗马|富士山|大本钟|泰姬|迪拜|香港|台北|新加坡|"
                            r"布拉格|阿姆斯特丹|巴塞罗那|维也纳|佛罗伦萨|伊斯坦布尔|曼谷|旧金山|芝加哥|多伦多|温哥华")
BAD_WORDS = re.compile(r"圣诞|万圣|国旗|婚礼|新娘|十字架|墓|骷髅|血|枪|烟|酒吧|啤酒瓶|葡萄酒|鸡尾酒|裸|比基尼|纹身|涂鸦|抗议|标语|"
                       r"广告牌|商标|品牌|logo|Logo|LOGO|文字为|写着|字样")
LOCATED_MAP = [  # dataset location / landmark text -> Chinese place name (for photos located in the scene city)
    (r"englischer garten", "英国花园"), (r"hellabrunn", "慕尼黑动物园"), (r"botanischer garten", "慕尼黑植物园"),
    (r"palmengarten", "棕榈园"), (r"terminal 1|flughafen|airport", "法兰克福机场"),
    (r"siegess", "胜利纪念柱"), (r"spree", "施普雷河畔"), (r"plänterwald", "普伦特森林公园"), (r"mauerweg", "柏林墙步道"),
    (r"kreuzberg", "克罗伊茨贝格"), (r"skalitzer", "克罗伊茨贝格"), (r"prenzlauer|rykestra", "普伦茨劳尔贝格"),
    (r"potsdam", "波茨坦"), (r"großer weg", "蒂尔加滕公园"), (r"fernsehturm", "柏林电视塔"),
    (r"namsan", "南山公园"), (r"익선동", "益善洞"), (r"부암동", "付岩洞"), (r"busan tower", "釜山塔"),
    (r"hangang|한강", "汉江公园"), (r"bundang", "盆唐"),
    (r"kinuta", "砧公园"), (r"hanegi", "羽根木公园"), (r"ueno|uenok|台東|taitō", "上野恩赐公园"),
    (r"skytree", "东京晴空塔"), (r"sumida aquarium", "墨田水族馆"), (r"asakusa", "浅草"), (r"shibuya", "涩谷"),
    (r"ginza", "银座"), (r"shinjuku", "新宿"), (r"odaiba", "台场"), (r"toyosu", "丰洲"),
    (r"daigo", "醍醐寺"), (r"kyoto botanical", "京都府立植物园"), (r"fushimi", "伏见稻荷大社"),
    (r"tōdai|todai", "东大寺"), (r"奈良", "奈良公园"),
    (r"滨江大道|pudong", "陆家嘴"), (r"chenshan", "辰山植物园"), (r"gucun", "顾村公园"), (r"botanical garden", "上海植物园"),
    (r"蠡园", "蠡园"), (r"ba da ling|badaling", "八达岭长城"), (r"鉴真|jianzhen", "鉴真路"), (r"binjiang|滨江", "滨江"),
]

GENERIC_TYPES = {"自然风光", "海边", "花卉", "美食", "咖啡馆", "室内", "公园"}
SEASON_OF_MONTH = {12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏", 9: "秋", 10: "秋",
                   11: "秋"}
SLOT_TIMES = {  # slot -> (start, end, compatible photo times)
    "morning": ("06:40", "08:30", {"白天", "清晨或傍晚"}),
    "day": ("09:10", "16:50", {"白天", "室内"}),
    "noon": ("11:40", "13:30", {"白天", "室内"}),
    "dusk": ("17:10", "19:20", {"白天", "清晨或傍晚", "夜晚"}),
    "night": ("19:40", "22:20", {"夜晚"}),
    "indoor": ("10:00", "21:00", {"室内", "白天", "夜晚"}),
    "dinner": ("18:20", "20:40", {"室内", "夜晚", "白天", "清晨或傍晚"}),
    "evening": ("18:00", "22:00", {"清晨或傍晚", "夜晚"}),
    "breakfast": ("07:30", "09:20", {"室内", "白天", "清晨或傍晚"}),
}


AVOID = re.compile(r"银河|星空|繁星|星轨|星云|月球|月面|满月|月亮|抽象|光轨|光斑|背景为|背景是纯|纯白色背景|白色背景|"
                   r"黑色背景|纯色背景|米色背景|浅色背景|木纹背景|粉色背景|蓝色背景|灰色背景|墙面背景|插画|绘画|海报|"
                   r"微距|显微|标本|X光|水下|潜水|鲨鱼|鲸|极光|沙漠|冰川|火山|雪山|峡谷|瀑布|悬崖|草原|闪电|轿车|跑车|越野车|"
                   r"汽车|女子|男子|女性|男性|女孩|男孩|小孩|儿童|新娘|新郎|模特|情侣|夫妇|一位身穿|自拍|纹理背景|影棚|"
                   r"加油站|造型的?霓虹|造型灯|灯饰|举着手机|手持手机|菠萝|冰山|热气球|风力发电|孔明灯|星球|太空|宇宙|"
                   r"篝火|谷仓|麦穗|麦田|麦浪|鞋内|摆成|心形|爱心|字母装置|空白的|卡片|信封|礼盒|人群|洞穴|冰洞|火焰|"
                   r"[\u4e00-\u9fff]{0,4}色的?背景(?:上|中|前|下)")
URBAN_AVOID = re.compile(r"村庄|乡间|山脊|郊野|田野|农田|牧场|山谷|山坡|森林|悬崖|城堡|麦田|麦穗|雪|山脉|山峦|群山|"
                         r"山峰|远山|山顶|雪原|针叶林|松林|苔藓|云海")
TROPICAL = re.compile(r"棕榈|椰子树|椰树|热带|仙人掌林")
TROPICAL_CITIES = {"三亚", "深圳"}
VASE = re.compile(r"花瓶|花束|插在|插着")
RURAL_OK = {"田野", "山", "森林", "城墙", "长城", "湿地", "芦苇", "红叶", "秋", "竹林", "竹子", "鹿", "寺", "窗外", "冬", "海",
            "沙滩", "海边", "海滩", "礁石", "海水", "日落"}
ANIMAL_TXT = re.compile(r"狗|猫|宠物|熊|猴|松鼠|兔子|马匹|奶牛|绵羊|鳄鱼|蜥蜴|蛇|青蛙|蛙|动物|日食")
DAILY_FOOD = re.compile(r"饭|面|菜|汤|饺|包子|火锅|烤|炒|粥|早餐|午餐|晚餐|餐|咖啡|茶|蛋糕|面包|甜点|披萨|寿司|沙拉|饮品")
DAILY_AVOID = re.compile(r"山脉|山峦|群山|山峰|雪原|苔藓|针叶林|松林|森林|棕榈|椰子树|椰树|热带|纯白背景|背景洁白|"
                         r"背景是干净的|白色台面|薄纱|静物|高调|白墙前|山谷|小屋|木屋|丘陵|旷野|原野|草甸")
DAILY_QUOTA = {"花卉": 50, "自然风光": 72, "公园": 40, "美食": 45, "咖啡馆": 15, "室内": 24, "夜景": 16, "街景": 16,
               "建筑": 16, "会议展览": 8, "运动": 8, "交通出行": 5, "地标": 6}
ANIMAL_OK = {"鹿", "鸟", "鱼", "水族馆", "海鸥"}
AERIAL = re.compile(r"航拍|俯瞰|鸟瞰|高空")
SYN = {  # scene tag -> what must be visible (any of)
    "夜景": r"城市|街道|街|建筑|高楼|大楼|楼|灯光|霓虹|广场|商店|餐厅|招牌|桥|塔|江|河|码头|夜市|灯笼",
    "灯光": r"灯光|霓虹|灯笼|路灯|照明",
    "城市": r"城市|高楼|大楼|建筑群|天际线|街道",
    "机场": r"飞机|机翼|机场|舷窗|航站|登机|跑道|云海|云层",
    "飞机": r"飞机|机翼|舷窗|云海|云层",
    "会议": r"会议|会场|演讲|讲台|屏幕|报告厅|礼堂|座椅|投影|展台|展厅|展览|大厅",
    "展览": r"展览|展厅|展台|展馆|大厅",
    "美食": r"美食|菜|饭|面|汤|肉|鱼|虾|蟹|贝|饺|包子|点心|料理|寿司|拉面|烤|炸|甜点|蛋糕|面包|水果|早餐|餐盘|碗",
    "啤酒": r"啤酒|香肠|椒盐卷饼|德式|烤肘|猪肘|牛排|面包篮",
    "香肠": r"香肠|烤肠",
    "烤肉": r"烤肉|烤串|泡菜|炸鸡|石锅|韩式|部队锅|冷面|年糕|紫菜包饭",
    "拌饭": r"拌饭|石锅|小菜",
    "海鲜": r"海鲜|虾|蟹|贝类|扇贝|生蚝|鱿鱼|章鱼|海产|刺身",
    "拉面": r"拉面|面条|汤面", "寿司": r"寿司|刺身|生鱼片", "料理": r"料理|定食|便当|天妇罗|日式",
    "抹茶": r"抹茶|和果子|丸子|日式", "茶": r"茶",
    "中餐": r"面条|拉面|饺子|包子|米饭|炒饭|炒菜|火锅|烤串|小笼|馒头|粥|豆浆|油条|麻辣|红烧|凉皮|肉夹馍|烩面|中式|筷子",
    "年夜饭": r"年夜饭|菜肴|一桌|饭菜|鱼|饺子|红烧|火锅", "饺子": r"饺子|水饺",
    "早茶": r"早茶|点心|包子|烧卖|汤包|蒸笼", "包子": r"包子|汤包|蒸笼", "点心": r"点心|蒸笼|烧卖|汤包|小笼",
    "水果": r"水果|芒果|椰子|菠萝|西瓜|榴莲", "椰子": r"椰子|椰",
    "咖啡": r"咖啡|拿铁|咖啡馆|咖啡杯",
    "河": r"河|江|水面|溪", "江": r"江|河|水面", "湖": r"湖|水面|池塘", "桥": r"桥",
    "树": r"树|林|绿荫", "草地": r"草地|草坪", "公园": r"公园|草地|树|长椅|步道|小路", "小路": r"小路|步道|小径",
    "道路": r"道路|小路|大道|林荫道", "步道": r"步道|小路|栈道", "清晨": r"清晨|晨光|日出|朝阳",
    "海": r"海|海水|海面|海浪|海岸", "海滩": r"海滩|沙滩", "沙滩": r"沙滩|海滩", "海边": r"海边|海滩|沙滩|海岸",
    "遮阳伞": r"遮阳伞|躺椅|伞", "礁石": r"礁石|岩石", "海水": r"海水|海浪",
    "日落": r"日落|夕阳|落日|晚霞|黄昏", "椰子树": r"椰子树|椰树|棕榈", "棕榈": r"棕榈|椰子树|椰树",
    "泳池": r"泳池|游泳池", "酒店": r"酒店|客房|泳池",
    "樱花": r"樱花", "花": r"花", "植物": r"植物|绿植|叶", "温室": r"温室|玻璃房", "郁金香": r"郁金香",
    "竹林": r"竹", "竹子": r"竹", "鹿": r"鹿", "寺": r"寺|神社|鸟居|佛|殿", "寺庙": r"寺|神社|鸟居|佛|殿",
    "庭园": r"庭园|庭院|园林|池塘|假山", "池塘": r"池塘|池", "护城河": r"护城河|城墙|石墙",
    "晴空塔": r"晴空塔|塔", "塔": r"塔", "灯笼": r"灯笼", "霓虹": r"霓虹|招牌|灯光", "街道": r"街道|街|小巷|巷|路口",
    "招牌": r"招牌|店铺|商店", "餐厅": r"餐厅|饭馆|料理店|居酒屋",
    "建筑": r"建筑|楼|房屋|屋顶|教堂|大厦|宫|殿|塔|墙",
    "古建筑": r"古建筑|宫殿|殿|亭|阁楼|琉璃|飞檐|斗拱|红墙|牌楼|古塔|宝塔|寺",
    "屋顶": r"屋顶|瓦", "门": r"门", "地标": r"地标|塔|门|纪念", "广场": r"广场", "老城": r"老城|古城|石板|街",
    "彩色": r"彩色|色彩|五颜六色|缤纷", "房屋": r"房屋|房子|民居|建筑",
    "列车": r"列车|火车|高铁|新干线|车站|站台|铁轨|车厢", "车站": r"车站|站台|站", "新干线": r"新干线|列车|高铁",
    "窗外": r"窗外|车窗|窗", "田野": r"田野|农田|田地|麦田",
    "水族馆": r"水族|鱼|海洋馆|水母", "鱼": r"鱼",
    "山": r"山|山峦|群山|山坡", "森林": r"森林|树林", "城墙": r"城墙|长城", "长城": r"长城|城墙|烽火台",
    "秋": r"秋|红叶|黄叶|金黄|落叶", "红叶": r"红叶|枫|秋", "宫殿": r"宫|殿|红墙|琉璃", "红墙": r"红墙|红色墙",
    "柳树": r"柳", "荷花": r"荷", "白塔": r"白塔|塔", "柏树": r"柏|松",
    "古镇": r"古镇|水乡|老街|灯笼", "亭": r"亭|廊|阁", "长廊": r"长廊|回廊", "遗址": r"遗址|石柱|废墟", "小巷": r"小巷|巷|胡同", "湿地": r"湿地|芦苇|水草", "芦苇": r"芦苇", "鸟": r"鸟",
    "工业": r"工业|钢|厂|高炉|管道|冷却塔|烟囱", "钢铁": r"钢|铁|高炉", "艺术": r"艺术|展|雕塑",
    "展馆": r"展馆|展厅|展览|博物馆", "科技": r"科技|模型|展品", "文物": r"文物|展品|青铜|瓷",
    "红色": r"红色|红", "烟花": r"烟花|焰火", "冬": r"冬|雪|冰|枯", "高楼": r"高楼|大楼|摩天|大厦",
    "云": r"云", "海鸥": r"海鸥|鸟", "喷泉": r"喷泉", "大桥": r"大桥|桥",
}


def need_regex(tags) -> re.Pattern | None:
    alts = [SYN.get(t, re.escape(t)) for t in tags]
    return re.compile("|".join(alts)) if alts else None


# local sunset (hh, mm) by month for the latitude band of Beijing; corrections per city below
SUNSET = {1: (17, 15), 2: (17, 50), 3: (18, 20), 4: (18, 50), 5: (19, 20), 6: (19, 45), 7: (19, 45), 8: (19, 20),
          9: (18, 35), 10: (17, 50), 11: (17, 5), 12: (16, 55)}
SUNSET_SHIFT = {"首尔": 45, "釜山": 25, "东京": -45, "京都": -20, "奈良": -20, "慕尼黑": 55, "柏林": 50, "法兰克福": 65,
                "上海": -50, "苏州": -45, "杭州": -40, "深圳": 0, "三亚": 30, "西安": 35, "郑州": 15, "扬州": -35,
                "天津": -5, "秦皇岛": -15}
# visible landmark / caption -> place name, per city
PLACE_HINTS = {
    "东京": [(r"晴空塔|钢结构塔", "东京晴空塔"), (r"东京塔|红色的?塔|红白相间的?塔", "东京塔"), (r"浅草|雷门", "浅草")],
    "柏林": [(r"电视塔|球体", "柏林电视塔"), (r"勃兰登堡", "勃兰登堡门"), (r"胜利纪念柱|金色天使", "胜利纪念柱")],
    "首尔": [(r"首尔塔|N首尔", "南山 首尔塔")], "釜山": [(r"釜山塔", "釜山塔")],
    "上海": [(r"东方明珠|陆家嘴|上海中心|金茂|环球金融", "陆家嘴"), (r"外滩", "外滩")],
    "杭州": [(r"西湖|雷峰塔|断桥", "西湖")],
    "北京": [(r"国家大剧院", "国家大剧院"), (r"央视", "国贸CBD"), (r"天安门", "天安门广场")],
}


# ---------------------------------------------------------------- scenes
@dataclass
class Scene:
    trip: str
    country: str
    city: str
    day: dt.date
    offset: int
    place: str
    n: int
    types: tuple
    slot: str = "day"
    tags: tuple = ()
    located_only: bool = False
    note: str = ""
    filled: list = field(default_factory=list)

    @property
    def season(self) -> str:
        return SEASON_OF_MONTH[self.day.month]


def _sc(trip, city, day, place, n, types, slot="day", tags=(), off=None, located_only=False, note=""):
    country = CITY_INFO[city][0]
    if off is None:
        off = {"德国": 2, "韩国": 9, "日本": 9}.get(country, 8)
    return Scene(trip, country, city, D(day), off, place, n, tuple(types), slot, tuple(tags), located_only, note)


def foreign_and_family_scenes() -> list[Scene]:
    g, k, j, s, y = "德国出差", "韩国旅游", "日本赏樱", "三亚度假", "春节回扬州"
    S = [
        # 德国出差 2025-09-14 → 09-20 (+02:00)
        _sc(g, "慕尼黑", "2025-09-14", "慕尼黑机场", 1, ["交通出行"], "dusk", ("机场", "飞机")),
        _sc(g, "慕尼黑", "2025-09-14", "老城区", 2, ["街景", "建筑", "夜景"], "night", ("城市", "街道", "建筑", "广场")),
        _sc(g, "慕尼黑", "2025-09-15", "英国花园", 2, ["公园", "自然风光"], "morning", ("河", "树", "草地", "公园")),
        _sc(g, "慕尼黑", "2025-09-15", "慕尼黑展览中心", 2, ["会议展览", "室内", "建筑"], "day", ("会议", "展览", "大厅")),
        _sc(g, "慕尼黑", "2025-09-15", "老城区", 2, ["街景", "建筑"], "dusk", ("街道", "建筑", "老城")),
        _sc(g, "慕尼黑", "2025-09-15", "老城区", 1, ["美食"], "dinner", ("啤酒", "香肠")),
        _sc(g, "慕尼黑", "2025-09-16", "玛利亚广场", 2, ["建筑", "地标", "街景"], "noon", ("广场", "建筑")),
        _sc(g, "慕尼黑", "2025-09-16", "王宫花园", 1, ["公园", "花卉"], "dusk", ("花园", "花")),
        _sc(g, "慕尼黑", "2025-09-16", "老城区", 2, ["夜景"], "evening", ("城市", "街道", "建筑", "广场")),
        _sc(g, "慕尼黑", "2025-09-16", "老城区", 1, ["咖啡馆", "美食"], "day", ("咖啡",)),
        _sc(g, "柏林", "2025-09-17", "柏林", 1, ["自然风光", "交通出行"], "day", ("列车", "田野", "窗外")),
        _sc(g, "柏林", "2025-09-17", "米特区", 2, ["建筑", "地标", "街景"], "day", ("建筑", "地标", "门"), located_only=True),
        _sc(g, "柏林", "2025-09-17", "柏林电视塔", 1, ["地标", "建筑", "街景"], "dusk", ("塔",), located_only=True),
        _sc(g, "柏林", "2025-09-17", "普伦茨劳尔贝格", 3, ["街景", "建筑", "咖啡馆"], "day", ("街道", "建筑", "咖啡")),
        _sc(g, "柏林", "2025-09-17", "施普雷河畔", 1, ["夜景", "街景"], "night", ("夜景", "街道")),
        _sc(g, "柏林", "2025-09-18", "蒂尔加滕公园", 2, ["公园", "自然风光", "地标"], "morning", ("树", "道路", "公园")),
        _sc(g, "柏林", "2025-09-18", "施普雷河畔", 2, ["建筑", "街景"], "day", ("建筑", "河")),
        _sc(g, "法兰克福", "2025-09-18", "法兰克福 金融区", 2, ["夜景", "建筑"], "evening", ("城市", "高楼")),
        _sc(g, "法兰克福", "2025-09-19", "美因河畔", 2, ["建筑", "街景", "地标"], "day", ("河", "城市", "高楼")),
        _sc(g, "法兰克福", "2025-09-19", "棕榈园", 2, ["花卉", "公园", "自然风光"], "noon", ("植物", "花", "温室")),
        _sc(g, "法兰克福", "2025-09-19", "法兰克福机场", 1, ["交通出行"], "day", ("机场", "飞机", "航拍")),
        # 韩国旅游 2024-10-01 → 10-06（国庆假期，+09:00）
        _sc(k, "首尔", "2024-10-01", "仁川机场", 1, ["交通出行"], "day", ("飞机", "机场", "海鸥")),
        _sc(k, "首尔", "2024-10-01", "南山 首尔塔", 2, ["夜景", "地标"], "evening", ("城市", "高楼", "塔")),
        _sc(k, "首尔", "2024-10-02", "汉江公园", 2, ["公园", "自然风光"], "morning", ("江", "河", "公园", "桥")),
        _sc(k, "首尔", "2024-10-02", "钟路区", 2, ["建筑", "街景"], "day", ("建筑", "街道", "屋顶")),
        _sc(k, "首尔", "2024-10-02", "益善洞", 2, ["街景", "咖啡馆"], "day", ("街道", "门", "咖啡")),
        _sc(k, "首尔", "2024-10-02", "汉江公园", 2, ["夜景"], "evening", ("江", "桥", "喷泉", "城市")),
        _sc(k, "首尔", "2024-10-03", "北岳山城郭路", 2, ["自然风光"], "day", ("山", "森林", "城墙", "步道")),
        _sc(k, "首尔", "2024-10-03", "明洞", 1, ["美食"], "noon", ("烤肉", "拌饭")),
        _sc(k, "首尔", "2024-10-03", "弘大", 2, ["街景", "建筑"], "dusk", ("街道", "招牌", "建筑")),
        _sc(k, "首尔", "2024-10-03", "汝矣岛", 1, ["夜景"], "evening", ("高楼", "城市")),
        _sc(k, "釜山", "2024-10-04", "釜山站", 1, ["交通出行"], "day", ("列车", "车站")),
        _sc(k, "釜山", "2024-10-04", "釜山塔", 1, ["地标", "建筑", "街景"], "day", ("城市", "航拍", "塔"), located_only=True),
        _sc(k, "釜山", "2024-10-04", "海云台", 3, ["海边", "自然风光"], "day", ("海滩", "海")),
        _sc(k, "釜山", "2024-10-04", "海云台", 2, ["海边", "自然风光"], "dusk", ("海滩", "海边", "沙滩", "日落")),
        _sc(k, "釜山", "2024-10-05", "海云台", 2, ["海边"], "morning", ("海滩", "沙滩", "清晨")),
        _sc(k, "釜山", "2024-10-05", "甘川洞文化村", 1, ["街景", "建筑"], "day", ("彩色", "房屋", "街道")),
        _sc(k, "釜山", "2024-10-05", "札嘎其市场", 1, ["美食"], "noon", ("海鲜",)),
        _sc(k, "釜山", "2024-10-05", "广安里", 2, ["夜景"], "evening", ("大桥", "城市", "高楼")),
        _sc(k, "首尔", "2024-10-06", "弘大", 1, ["咖啡馆", "街景"], "day", ("咖啡", "街道")),
        _sc(k, "首尔", "2024-10-06", "仁川机场", 1, ["交通出行"], "dusk", ("机场", "飞机")),
        # 日本赏樱 2025-04-03 → 04-08 (+09:00)
        _sc(j, "东京", "2025-04-03", "羽田机场", 1, ["交通出行"], "day", ("飞机", "机翼")),
        _sc(j, "东京", "2025-04-03", "上野恩赐公园", 3, ["花卉", "公园"], "dusk", ("樱花",)),
        _sc(j, "东京", "2025-04-03", "浅草", 1, ["夜景", "街景"], "night", ("夜景", "灯笼", "街道")),
        _sc(j, "东京", "2025-04-04", "皇居外苑", 2, ["花卉", "公园", "自然风光"], "morning", ("樱花", "护城河", "树")),
        _sc(j, "东京", "2025-04-04", "东京晴空塔", 3, ["地标", "花卉", "建筑"], "day", ("晴空塔", "樱花", "塔")),
        _sc(j, "东京", "2025-04-04", "隅田川", 2, ["花卉", "自然风光", "街景"], "day", ("樱花", "晴空塔")),
        _sc(j, "东京", "2025-04-04", "涩谷", 3, ["夜景", "街景"], "evening", ("城市", "霓虹", "街道")),
        _sc(j, "东京", "2025-04-05", "上野恩赐公园", 3, ["花卉", "公园"], "day", ("樱花",)),
        _sc(j, "东京", "2025-04-05", "墨田水族馆", 2, ["室内"], "indoor", ("水族馆", "鱼")),
        _sc(j, "东京", "2025-04-05", "新宿御苑", 2, ["花卉", "公园"], "day", ("樱花", "花")),
        _sc(j, "东京", "2025-04-05", "银座", 2, ["夜景", "街景", "美食"], "night", ("夜景", "街道", "餐厅")),
        _sc(j, "京都", "2025-04-06", "京都站", 1, ["交通出行"], "day", ("列车", "新干线")),
        _sc(j, "京都", "2025-04-06", "岚山竹林", 2, ["自然风光"], "day", ("竹林", "竹子")),
        _sc(j, "京都", "2025-04-06", "鸭川", 2, ["花卉", "自然风光"], "dusk", ("樱花",)),
        _sc(j, "京都", "2025-04-06", "祇园", 2, ["夜景", "街景"], "night", ("灯笼", "街道", "夜景")),
        _sc(j, "京都", "2025-04-07", "鸭川", 1, ["花卉", "自然风光"], "morning", ("樱花", "河")),
        _sc(j, "奈良", "2025-04-07", "奈良公园", 2, ["自然风光", "公园"], "day", ("鹿",)),
        _sc(j, "奈良", "2025-04-07", "奈良公园", 1, ["建筑", "地标"], "day", ("寺庙",)),
        _sc(j, "京都", "2025-04-07", "祇园", 1, ["美食"], "dinner", ("料理", "拉面", "寿司")),
        _sc(j, "京都", "2025-04-08", "醍醐寺", 2, ["自然风光", "建筑", "花卉"], "morning", ("庭园", "池塘", "樱花", "寺")),
        _sc(j, "京都", "2025-04-08", "京都府立植物园", 1, ["花卉"], "day", ("花",)),
        _sc(j, "京都", "2025-04-08", "祇园", 1, ["美食", "咖啡馆"], "noon", ("抹茶", "甜点", "茶")),
        # 三亚度假 2025-07-25 → 07-31 (mailbox family trip)
        _sc(s, "三亚", "2025-07-25", "凤凰机场", 1, ["交通出行"], "day", ("飞机", "机翼")),
        _sc(s, "三亚", "2025-07-25", "三亚湾", 1, ["海边", "自然风光"], "dusk", ("日落", "海")),
        _sc(s, "三亚", "2025-07-26", "亚龙湾", 3, ["海边"], "day", ("海滩", "沙滩", "海水")),
        _sc(s, "三亚", "2025-07-26", "亚龙湾", 1, ["室内", "海边"], "dusk", ("泳池",)),
        _sc(s, "三亚", "2025-07-27", "三亚湾", 1, ["海边"], "morning", ("海滩", "清晨")),
        _sc(s, "三亚", "2025-07-27", "椰梦长廊", 2, ["海边", "自然风光"], "dusk", ("椰子树", "棕榈", "日落")),
        _sc(s, "三亚", "2025-07-27", "第一市场", 1, ["美食"], "dinner", ("海鲜",)),
        _sc(s, "三亚", "2025-07-28", "亚龙湾", 2, ["海边", "自然风光"], "day", ("海", "礁石", "海水")),
        _sc(s, "三亚", "2025-07-29", "鹿回头", 1, ["自然风光", "海边"], "dusk", ("海", "日落")),
        _sc(s, "三亚", "2025-07-30", "大东海", 2, ["海边"], "day", ("海滩", "海")),
        _sc(s, "三亚", "2025-07-31", "三亚湾", 1, ["美食"], "noon", ("水果", "椰子")),
        # 春节回扬州 2026-02-13 → 02-21 (mailbox family trip)
        _sc(y, "扬州", "2026-02-16", "家中", 2, ["美食"], "dinner", ("饺子", "中餐")),
        _sc(y, "扬州", "2026-02-16", "家中", 1, ["夜景"], "evening", ("烟花", "灯笼")),
        _sc(y, "扬州", "2026-02-17", "东关街", 2, ["街景", "夜景"], "evening", ("灯笼",)),
        _sc(y, "扬州", "2026-02-18", "瘦西湖", 1, ["自然风光", "公园"], "day", ("柳树", "古建筑", "亭")),
        _sc(y, "扬州", "2026-02-19", "东关街", 1, ["美食"], "breakfast", ("早茶", "包子", "点心")),
    ]
    return S


BIZ_PLACES = {  # city -> [(place, types, slot, required tags)]
    "上海": [("陆家嘴", ["夜景", "地标", "建筑"], "evening", ("高楼", "江", "城市")),
             ("外滩", ["夜景", "地标"], "evening", ("建筑", "江", "水面")),
             ("徐汇滨江", ["自然风光", "街景"], "morning", ("江", "步道", "清晨")),
             ("虹桥", ["咖啡馆", "美食"], "day", ("咖啡",)),
             ("陆家嘴", ["建筑", "地标"], "day", ("高楼", "城市"))],
    "苏州": [("金鸡湖", ["自然风光", "夜景"], "dusk", ("湖", "城市")),
             ("平江路", ["街景", "夜景"], "evening", ("灯笼", "古镇", "小巷")),
             ("苏州园区", ["会议展览", "室内"], "day", ("会议", "展览")),
             ("金鸡湖", ["花卉", "公园"], "day", ("花",))],
    "杭州": [("西湖", ["夜景", "自然风光"], "evening", ("湖", "塔")), ("滨江", ["街景", "建筑"], "day", ("街道", "高楼", "城市")),
             ("钱塘江", ["自然风光"], "morning", ("江", "清晨"))],
    "西安": [("曲江", ["夜景", "建筑"], "night", ("夜景", "灯光", "古建筑")), ("高新区", ["美食"], "noon", ("中餐",)),
             ("高新区", ["会议展览", "室内"], "day", ("会议",))],
    "郑州": [("郑东新区", ["夜景", "建筑"], "evening", ("高楼", "城市")), ("郑东新区", ["美食"], "noon", ("中餐",))],
    "深圳": [("南山区", ["建筑", "街景"], "day", ("高楼", "城市")), ("深圳湾", ["自然风光", "海边"], "dusk", ("日落", "海")),
             ("南山区", ["花卉"], "day", ("花",))],
    "天津": [("海河", ["夜景"], "evening", ("河", "桥", "城市")), ("天津 市区", ["街景", "建筑"], "day", ("高楼", "街道"))],
}


def business_scenes() -> list[Scene]:
    rng = random.Random(f"{SEED}|biz")
    out = []
    for t in life.TRIPS:
        if t.purpose != "出差" or t.country != "中国":
            continue
        city = t.segments[0][0]
        if city not in BIZ_PLACES:
            continue
        p = 0.75 if city in ("上海", "苏州") else 0.9
        if rng.random() > p:
            continue
        days = [d for d in t.days()]
        k = rng.choice([1, 1, 2, 2, 3])
        opts = BIZ_PLACES[city][:]
        rng.shuffle(opts)
        for place, types, slot, tags in opts[:k]:
            d = rng.choice(days if slot not in ("night", "dusk") or len(days) == 1 else days[:-1])
            if slot == "morning" and d == t.start:
                d = days[min(1, len(days) - 1)]
            out.append(_sc(t.label, city, d.isoformat(), place, 1, types, slot, tags))
    return out


OUTING_SCENES = {  # place key -> (photo place, n, types, slot, required tags, months or None)
    "mutianyu": ("慕田峪长城", 2, ["地标", "自然风光", "建筑"], "day", ("长城",), None),
    "badaling": ("八达岭长城", 2, ["地标", "自然风光", "建筑"], "day", ("长城",), None),
    "xiangshan": ("香山公园", 2, ["自然风光", "公园"], "day", ("红叶",), (10, 11)),
    "summer_palace": ("颐和园", 1, ["地标", "建筑", "公园", "自然风光"], "day", ("古建筑", "柳树", "亭", "长廊"), None),
    "palace": ("故宫博物院", 1, ["地标", "建筑"], "day", ("宫殿", "红墙", "古建筑"), None),
    "jingshan": ("景山公园", 1, ["地标", "建筑", "公园"], "day", ("古建筑", "宫殿"), None),
    "beihai": ("北海公园", 1, ["公园", "自然风光", "建筑"], "day", ("白塔", "荷花", "古建筑"), None),
    "temple_heaven": ("天坛公园", 1, ["地标", "建筑", "公园"], "day", ("古建筑", "宫殿"), None),
    "yuanmingyuan": ("圆明园", 1, ["公园", "自然风光"], "day", ("荷花", "遗址"), None),
    "botanical": ("北京植物园", 1, ["花卉", "公园"], "day", ("郁金香", "花"), (4, 5)),
    "yuyuantan": ("玉渊潭公园", 1, ["花卉", "公园"], "day", ("樱花",), (3, 4)),
    "yanqi": ("雁栖湖", 1, ["自然风光"], "day", ("湖", "山"), (4, 5, 6, 7, 8, 9, 10)),
    "hongluo": ("红螺寺", 1, ["自然风光", "建筑"], "day", ("寺", "山"), (4, 5, 6, 7, 8, 9, 10)),
    "gubei": ("古北水镇", 2, ["夜景", "街景"], "evening", ("灯笼", "古镇"), None),
    "beidaihe": ("北戴河 海滨", 2, ["海边", "自然风光"], "day", ("海", "沙滩", "海边"), (6, 7, 8, 9, 10)),
    "tianjin": ("天津 市区", 1, ["街景", "建筑", "夜景"], "day", ("建筑", "街道", "河", "桥"), None),
    "expo": ("北京世园公园", 1, ["花卉", "公园"], "day", ("花",), (4, 5, 6, 7, 8, 9, 10)),
    "yeyahu": ("野鸭湖国家湿地公园", 1, ["自然风光"], "day", ("湿地", "芦苇", "鸟"), (4, 5, 6, 7, 8, 9, 10)),
    "shougang": ("首钢园", 1, ["建筑", "地标"], "day", ("工业", "钢铁"), None),
    "cstm": ("中国科学技术馆", 1, ["室内"], "indoor", ("展馆", "科技", "模型"), None),
    "museum": ("国家博物馆", 1, ["室内"], "indoor", ("展馆", "文物"), None),
}


def outing_scenes() -> list[Scene]:
    out = []
    for o in life.OUTINGS:
        if o.place not in OUTING_SCENES:
            continue
        place, n, types, slot, tags, months = OUTING_SCENES[o.place]
        if months and o.date.month not in months:
            continue
        city = life.P[o.place].city if o.place in life.P else "北京"
        days = [o.date + dt.timedelta(days=i) for i in range(o.nights + 1)]
        for i, d in enumerate(days):
            nn = n if i == 0 else max(1, n - 1)
            out.append(_sc("周末出游", city, d.isoformat(), place, nn, types, slot, tags))
    return out


# ---------------------------------------------------------------- candidates
@dataclass
class Cand:
    pid: str
    pool: dict
    v: dict  # vision answer

    @property
    def text(self) -> str:
        return f"{self.v.get('caption', '')} {' '.join(self.v.get('tags') or [])} {self.v.get('landmark') or ''}"


def canon_type(v: dict) -> str:
    """The model's scene type, mapped onto the closed list used for fields.type."""
    t = str(v.get("type") or "").strip()
    if t in TYPES:
        return t
    txt = f"{t} {' '.join(map(str, v.get('tags') or []))}"
    for pat, name in ((r"夜", "夜景"), (r"食|菜|餐", "美食"), (r"咖啡", "咖啡馆"), (r"海|沙滩", "海边"), (r"花", "花卉"),
                      (r"公园", "公园"), (r"建筑|楼", "建筑"), (r"街", "街景"), (r"车|机场|飞机|列车", "交通出行"),
                      (r"室内|房间", "室内")):
        if re.search(pat, txt):
            return name
    return "自然风光"


def load_candidates() -> list[Cand]:
    out = []
    for rec in read_jsonl(WORK / "pool.jsonl"):
        cp = cache_path(rec["id"], MODEL)
        if not cp.exists() or not (WORK / "img" / f"{rec['id']}.jpg").exists():
            continue
        v = json.loads(cp.read_text("utf-8")).get("parsed")
        if not v:
            continue
        cap = str(v.get("caption") or "").strip()
        if not (8 <= len(cap) <= 90) or v.get("brand") or int(v.get("quality") or 0) < 3:
            continue
        if v.get("people") == "清晰可辨的人脸":
            continue
        if BAD_WORDS.search(cap) or FOREIGN_FAMOUS.search(cap + str(v.get("landmark") or "")):
            continue
        if AVOID.search(cap + " " + " ".join(map(str, v.get("tags") or []))):
            continue
        v["type"] = canon_type(v)
        out.append(Cand(rec["id"], rec, v))
    return out


def _region_ok(c: Cand, country: str) -> bool:
    if c.pool.get("city") and CITY_INFO.get(c.pool["city"], ("",))[0] == country:
        return True  # the dataset location is authoritative
    region = c.v.get("region") or "无法判断"
    lang = c.v.get("text_lang") or "无"
    allowed = {
        "德国": ({"德国", "欧洲其他", "无法判断"}, {"无", "德文", "英文"}),
        "韩国": ({"韩国", "无法判断"}, {"无", "韩文", "英文"}),
        "日本": ({"日本", "无法判断"}, {"无", "日文"}),
        "中国": ({"中国", "无法判断"}, {"无", "中文"}),
    }[country]
    if region not in allowed[0] or lang not in allowed[1]:
        return False
    loc_country = c.pool.get("country")
    return loc_country in (None, country)


def _landmark_city(c: Cand) -> str | None:
    txt = f"{c.v.get('landmark') or ''} {c.v.get('caption') or ''}"
    for tok, city in ALL_LANDMARK_TOKENS.items():
        if tok in txt and len(tok) >= 2:
            return city
    return None


def _season_ok(c: Cand, sc: Scene) -> bool:
    ps = c.v.get("season") or "无法判断"
    txt = c.text
    m = sc.day.month
    if "樱花" in txt and m not in (3, 4):
        return False
    if re.search(r"雪|冰", txt) and m not in (12, 1, 2):
        return False
    if re.search(r"红叶|秋叶|银杏|落叶|金黄的树|黄叶", txt) and m not in (9, 10, 11):
        return False
    if ps == "无法判断":
        return True
    target = SEASON_OF_MONTH[m]
    opposite = {"春": "秋", "秋": "春", "夏": "冬", "冬": "夏"}[target]
    return ps != opposite


def _time_ok(c: Cand, slot: str) -> bool:
    return (c.v.get("time") or "白天") in SLOT_TIMES[slot][2]


def score(c: Cand, sc: Scene) -> float | None:
    if not _region_ok(c, sc.country) or not _season_ok(c, sc) or not _time_ok(c, sc.slot):
        return None
    lm_city = _landmark_city(c)
    if lm_city and lm_city != sc.city:
        return None
    lm = str(c.v.get("landmark") or "").strip()
    if lm and not lm_city:
        return None  # a landmark we cannot place
    typ = c.v.get("type") or ""
    loc_city = c.pool.get("city")
    if loc_city and loc_city != sc.city and typ not in GENERIC_TYPES:
        return None
    if sc.located_only and loc_city != sc.city:
        return None
    txt = f"{c.v.get('caption') or ''} {' '.join(map(str, c.v.get('tags') or []))}"
    need = need_regex(sc.tags)
    if need and not need.search(txt):
        return None
    if AERIAL.search(txt) and loc_city != sc.city and "交通出行" not in sc.types:
        return None
    if URBAN_AVOID.search(txt) and not (set(sc.tags) & RURAL_OK):
        return None
    if ANIMAL_TXT.search(txt) and not (set(sc.tags) & ANIMAL_OK):
        return None
    if TROPICAL.search(txt) and sc.city not in TROPICAL_CITIES and loc_city != sc.city:
        return None
    if VASE.search(txt) and sc.place != "家中":
        return None
    if "夜景" in sc.types and not re.search(SYN["夜景"], txt):
        return None
    s = 0.0
    if loc_city == sc.city:
        s += 60
    elif c.pool.get("country") == sc.country:
        s += 20
    if lm_city == sc.city:
        s += 45
    if typ in sc.types:
        s += 30 if typ == sc.types[0] else 18
    elif sc.types and loc_city != sc.city:
        return None
    tags = " ".join(c.v.get("tags") or []) + " " + (c.v.get("caption") or "")
    s += 9 * sum(1 for t in sc.tags if t in tags)
    s += 5 * int(c.v.get("quality") or 3)
    s += 2 * math.log10(c.pool.get("downloads", 0) + 10)
    people = c.v.get("people") or "无"
    s -= {"有人物但看不清脸": 18, "远处小人物": 4}.get(people, 0)
    if (c.v.get("region") or "") == sc.country:
        s += 10
    ps = c.v.get("season")
    if ps in ("春", "夏", "秋", "冬") and ps != sc.season:
        s -= 12
    return s


def assign(scenes: list[Scene], cands: list[Cand], used: set) -> None:
    """Greedy: scarce scenes first (fewest compatible candidates per wanted photo)."""
    options = []
    for sc in scenes:
        opts = [(s, c) for c in cands if c.pid not in used and (s := score(c, sc)) is not None and s >= 40]
        opts.sort(key=lambda t: -t[0])
        options.append((sc, opts))
    options.sort(key=lambda t: (len(t[1]) / max(t[0].n, 1), t[0].day))
    for sc, opts in options:
        for _s, c in opts:
            if len(sc.filled) >= sc.n:
                break
            if c.pid in used:
                continue
            sc.filled.append(c)
            used.add(c.pid)


# ---------------------------------------------------------------- everyday Beijing
DAILY_PLACES = {
    "公园": ["朝阳公园", "奥林匹克森林公园", "大望京公园", "温榆河公园", "亮马河"],
    "自然风光": ["奥林匹克森林公园", "朝阳公园", "温榆河公园", "大望京公园"],
    "花卉": ["朝阳公园", "元大都城垣遗址公园", "大望京公园", "望京 家中"],
    "美食": ["望京 家中", "望京", "三里屯", "中关村软件园"],
    "咖啡馆": ["中关村软件园", "望京SOHO", "三里屯"],
    "会议展览": ["中关村软件园 云帆大厦"],
    "室内": ["望京 家中", "中关村软件园 云帆大厦"],
    "夜景": ["国贸CBD", "望京", "亮马河"],
    "街景": ["望京", "三里屯", "国贸CBD"],
    "建筑": ["国贸CBD", "中关村软件园", "望京SOHO"],
    "交通出行": ["望京"],
    "运动": ["奥林匹克森林公园", "朝阳公园"],
    "地标": ["国贸CBD"],
    "海边": [],
}
DAILY_POI = {
    "朝阳公园": (39.9440, 116.4790), "奥林匹克森林公园": (40.0165, 116.3920), "大望京公园": (39.9887, 116.4886),
    "温榆河公园": (40.0326, 116.5200), "亮马河": (39.9502, 116.4728), "元大都城垣遗址公园": (39.9757, 116.3900),
    "望京 家中": (39.9963, 116.4705), "望京": (39.9935, 116.4807), "三里屯": (39.9365, 116.4547),
    "中关村软件园": (40.0565, 116.2786), "中关村软件园 云帆大厦": (40.0565, 116.2786), "望京SOHO": (39.9962, 116.4806),
    "国贸CBD": (39.9087, 116.4605), "国家大剧院": (39.9037, 116.3890), "天安门广场": (39.9055, 116.3976),
    "东京塔": (35.6586, 139.7454),
}
RUN_PLACE = {"olympic": "奥林匹克森林公园", "chaoyang": "朝阳公园", "wangjing": "大望京公园"}


def daily_assign(cands: list[Cand], used: set, target: int, runs, kept=()) -> list[tuple]:
    """Everyday Beijing photos: (cand, local datetime, place, trip label, note).

    ``kept`` are (cand, datetime) pairs of everyday photos that stay; their days and
    types count against the per-day and per-type limits.
    """
    rng = random.Random(f"{SEED}|daily")
    pool = []
    for c in cands:
        if c.pid in used or not _region_ok(c, "中国"):
            continue
        if c.pool.get("city") not in (None, "北京"):
            continue
        typ = c.v.get("type") or ""
        if typ in ("海边",) or _landmark_city(c) not in (None, "北京") or str(c.v.get("landmark") or "").strip():
            continue
        txt0 = f"{c.v.get('caption') or ''} {' '.join(map(str, c.v.get('tags') or []))}"
        if re.search(r"山峰|山峦|群山|雪山|峡谷|海|岛|悬崖|荒野|森林深处|湖面倒映着山|郊野|乡间|田野|农田|牧场", txt0):
            continue
        if AERIAL.search(txt0) and c.pool.get("city") != "北京":
            continue
        da = DAILY_AVOID.search(txt0)
        if da and not (da.group(0) == "静物" and typ in ("美食", "咖啡馆")):
            continue
        if ANIMAL_TXT.search(txt0) or re.search(r"村庄|城堡|麦田|麦穗|牧场", txt0):
            continue
        if typ == "美食" and not DAILY_FOOD.search(txt0):
            continue
        if "泳池" in txt0 and "室内" not in txt0:
            continue
        if int(c.v.get("quality") or 0) < 4:
            continue
        s = 5 * int(c.v.get("quality") or 0) + 2 * math.log10(c.pool.get("downloads", 0) + 10)
        s -= {"有人物但看不清脸": 18, "远处小人物": 4}.get(c.v.get("people") or "无", 0)
        s += {"美食": 6, "公园": 6, "花卉": 4, "咖啡馆": 6, "自然风光": 3, "夜景": 4, "室内": 3, "运动": 6}.get(typ, 0)
        if c.pool.get("city") == "北京":
            s += 30
        pool.append((s, c))
    pool.sort(key=lambda t: -t[0])
    days = [d for d in daterange(life.START, life.END - dt.timedelta(days=1)) if life.in_beijing(d)]
    taken = Counter(t.date() for _c, t in kept)
    out = []
    run_days = [(x, RUN_PLACE[x.key]) for x in runs if x.key in RUN_PLACE and x.start.utcoffset() == dt.timedelta(hours=8)]
    per_type = Counter(c.v.get("type") or "自然风光" for c, _t in kept)
    sakura = sum("樱花" in c.text for c, _t in kept)
    for _s, c in pool:
        if len(out) >= target:
            break
        typ = c.v.get("type") or "自然风光"
        tod = c.v.get("time") or "白天"
        txt = c.text
        if per_type[typ] >= DAILY_QUOTA.get(typ, 10):
            continue
        if "樱花" in txt:
            if sakura >= 12:
                continue
        months = list(range(1, 13))
        ps = c.v.get("season")
        if ps in ("春", "夏", "秋", "冬"):
            months = {"春": [3, 4, 5], "夏": [6, 7, 8], "秋": [9, 10, 11], "冬": [12, 1, 2]}[ps]
        if "樱花" in txt:
            months = [3, 4]
        if re.search(r"雪|冰", txt):
            months = [12, 1, 2]
        if re.search(r"红叶|银杏|落叶|黄叶|秋叶", txt):
            months = [10, 11]
        # running scenery: attach to a real run on the watch
        if typ in ("公园", "自然风光", "运动") and tod in ("白天", "清晨或傍晚") and rng.random() < 0.45:
            opts = [(x, p) for x, p in run_days if x.start.month in months and taken[x.start.date()] == 0]
            if opts:
                x, place = rng.choice(opts)
                t = (x.end + dt.timedelta(minutes=rng.randint(2, 9))).replace(second=0)
                taken[t.date()] += 1
                per_type[typ] += 1
                sakura += "樱花" in txt
                out.append((c, t, place, "日常", f"跑步后拍摄（{x.km:.2f} 公里）"))
                continue
        cand_days = [d for d in days if d.month in months and taken[d] < 1]
        if typ in ("会议展览",):
            cand_days = [d for d in cand_days if life.is_workday(d)]
        elif typ in ("公园", "自然风光", "花卉"):
            cand_days = [d for d in cand_days if not life.is_workday(d)] or cand_days
        if not cand_days:
            continue
        d = rng.choice(cand_days)
        taken[d] += 1
        places = DAILY_PLACES.get(typ) or ["望京"]
        if typ == "花卉":
            places = ["望京 家中"] if VASE.search(txt) else [x for x in places if x != "望京 家中"]
        if not life.is_workday(d):
            places = [x for x in places if not x.startswith("中关村软件园")] or ["望京"]
        place = rng.choice(places)
        if typ == "室内" and life.is_workday(d) and rng.random() < 0.5:
            place = "中关村软件园 云帆大厦"
        if tod == "夜晚":
            hhmm = (rng.randint(19, 22), rng.randint(0, 59))
        elif tod == "清晨或傍晚":
            hhmm = rng.choice([(6, rng.randint(20, 59)), (17 if d.month in (11, 12, 1, 2) else 18, rng.randint(0, 50))])
        elif typ == "美食":
            hhmm = rng.choice([(12, rng.randint(0, 45)), (18, rng.randint(20, 59)), (19, rng.randint(0, 40))])
        elif typ == "咖啡馆":
            hhmm = (rng.choice([10, 14, 15, 16]), rng.randint(0, 59))
        else:
            hhmm = (rng.randint(9, 16), rng.randint(0, 59))
        if life.is_workday(d) and place not in ("中关村软件园", "中关村软件园 云帆大厦") and 9 <= hhmm[0] < 18:
            place = "中关村软件园" if typ in ("咖啡馆", "美食", "公园", "自然风光", "花卉") else place
        t = dt.datetime(d.year, d.month, d.day, hhmm[0], hhmm[1], tzinfo=tz(8))
        per_type[typ] += 1
        sakura += "樱花" in txt
        out.append((c, t, place, "日常", ""))
    return out


# ---------------------------------------------------------------- times
def sunset(city: str, month: int) -> int:
    h, m = SUNSET[month]
    return h * 60 + m + SUNSET_SHIFT.get(city, 0)


def slot_time(sc: Scene, c: Cand, i: int, rng: random.Random) -> dt.datetime:
    a, b, _ = SLOT_TIMES[sc.slot]
    ah, am = life.hhmm(a)
    bh, bm = life.hhmm(b)
    lo, hi = ah * 60 + am, bh * 60 + bm
    tod = c.v.get("time")
    if sc.slot in ("evening", "dusk", "night") and tod in ("清晨或傍晚", "夜晚"):
        ss = sunset(sc.city, sc.day.month)
        lo, hi = (ss - 25, ss + 15) if tod == "清晨或傍晚" else (max(lo, ss + 35), max(hi, ss + 120))
        hi = min(hi, 23 * 60 + 20)
    if sc.slot == "dusk" and tod == "夜晚":
        lo = max(lo, 18 * 60 + 40)
    if sc.slot == "dusk" and tod == "白天":
        hi = min(hi, 18 * 60)
    m = rng.randint(lo, hi)
    d = sc.day
    return dt.datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=tz(sc.offset))


def place_for(sc_city: str, place: str, c: Cand) -> tuple[str, float, float]:
    """Place name and coordinates; a visible landmark or a photo located in the city decides the spot."""
    seen = f"{c.v.get('landmark') or ''} {c.v.get('caption') or ''}"
    for pat, name in PLACE_HINTS.get(sc_city, []):
        if re.search(pat, seen):
            place = name
            break
    loc = c.pool.get("loc") or {}
    if c.pool.get("city") == sc_city and loc.get("lat") is not None and loc.get("lon") is not None:
        src = f"{loc.get('name') or ''} {loc.get('city') or ''}".lower()
        name = place
        for pat, zh in LOCATED_MAP:
            if re.search(pat, src):
                name = zh
                break
        return name, round(loc["lat"], 5), round(loc["lon"], 5)
    lat, lon = POI.get(place) or DAILY_POI.get(place) or CITY_INFO[sc_city][1:]
    h = int(c.pid.encode().hex(), 16)
    return place, round(lat + ((h % 41) - 20) * 0.00012, 5), round(lon + ((h // 41 % 41) - 20) * 0.00012, 5)


# ---------------------------------------------------------------- records
def _text(caption: str, t: dt.datetime, country: str, city: str, place: str, trip: str, extra: str) -> str:
    local = "" if t.utcoffset() == dt.timedelta(hours=8) else "（当地时间）"
    where = f"{city} · {place}" if country == "中国" else f"{country} {city} · {place}"
    if place == city:
        where = city if country == "中国" else f"{country} {city}"
    ctx = {"日常": "", "周末出游": "周末出游。"}.get(trip, f"行程：{trip}。")
    cap = caption.rstrip("。") + "。"
    return (f"{cap}拍摄于 {cn_date(t.date())}（{weekday_cn(t.date())}）{t:%H:%M}{local}，地点：{where}。{ctx}"
            + (f"{extra}。" if extra else ""))


def _tags(c: Cand, typ: str) -> list[str]:
    tags = []
    for x in [typ, *(c.v.get("tags") or [])]:
        x = str(x).strip()
        if x and x not in tags and len(x) <= 8:
            tags.append(x)
    return tags[:6]


def _run_ok(text: str, t: dt.datetime, runs) -> bool:
    """An everyday photo taken after a run stays only if that run is still on the watch."""
    if "跑步后拍摄" not in text:
        return True
    return any(x.activity == "跑步" and x.start.utcoffset() == dt.timedelta(hours=8) and x.start.date() == t.date()
               and x.end.replace(second=0) <= t <= x.end + dt.timedelta(minutes=10) for x in runs)


def keep_previous(prev: list[dict], by_pid: dict, scenes: list[Scene], runs, used: set):
    """Previous photos that are still valid: scene photos go back into their scene, everyday ones are listed.

    Returns (kept, kept_daily, dropped_daily) with kept = {pid: previous record}.
    """
    groups: dict[tuple, list[Scene]] = defaultdict(list)
    for sc in scenes:
        groups[(sc.trip, sc.city, sc.day)].append(sc)
    kept, kept_daily, dropped_daily = {}, [], 0
    for r in prev:
        c = by_pid.get(r["source"]["id"])
        t = dt.datetime.fromisoformat(r["date"])
        trip = r["fields"]["trip"]
        if trip == "日常":
            ok = c is not None and life.in_beijing(t.date()) and _run_ok(r["text"], t, runs)
            if ok and c.pid not in used:
                kept[c.pid] = r
                kept_daily.append((c, t))
                used.add(c.pid)
            else:
                dropped_daily += 1
            continue
        if c is None or c.pid in used:
            continue
        options = groups.get((trip, r["fields"]["city"], t.date()), [])
        options = [sc for sc in options if len(sc.filled) < sc.n]
        if not options:
            continue  # the trip / outing is no longer on that day
        same = [sc for sc in options if sc.place == r["place"]["name"]]
        sc = (same or options)[0]
        sc.filled.append(c)
        kept[c.pid] = r
        used.add(c.pid)
    return kept, kept_daily, dropped_daily


def build() -> list[dict]:
    from PIL import Image

    cands = load_candidates()
    by_pid = {c.pid: c for c in cands}
    fresh = "--fresh" in sys.argv or not (OUT / "photo.jsonl").exists()
    prev = [] if fresh else read_jsonl(OUT / "photo.jsonl")
    used: set = set()
    scenes = foreign_and_family_scenes()
    biz = business_scenes()
    outs = outing_scenes()
    workouts, _, _ = sport.plan()
    kept, kept_daily, dropped_daily = keep_previous(prev, by_pid, scenes + biz + outs, workouts, used)
    if fresh:
        assign(scenes, cands, used)
        assign(biz, cands, used)
        assign(outs, cands, used)
    else:
        # only trips without any kept photo (new or moved trips) are filled again
        kept_trips = {r["fields"]["trip"] for r in kept.values()}
        for group in (scenes, biz, outs):
            assign([sc for sc in group if sc.trip not in kept_trips], cands, used)
    rows = []  # (cand, datetime, country, city, place, trip, extra)
    for sc in scenes + biz + outs:
        for i, c in enumerate(sc.filled):
            if c.pid in kept:
                t = dt.datetime.fromisoformat(kept[c.pid]["date"])
            else:
                rng = random.Random(f"{SEED}|time|{sc.trip}|{sc.city}|{sc.day}|{sc.place}|{c.pid}")
                t = slot_time(sc, c, i, rng)
            rows.append((c, t, sc.country, sc.city, sc.place, sc.trip, sc.note))
    for c, t in kept_daily:
        rows.append((c, t, "中国", "北京", None, "日常", ""))
    target_total = int(sys.argv[sys.argv.index("--total") + 1]) if "--total" in sys.argv else 440
    n_new = max(0, target_total - len(rows)) if fresh else dropped_daily
    daily = daily_assign(cands, used, n_new, workouts, kept_daily)
    for c, t, place, trip, extra in daily:
        rows.append((c, t, "中国", "北京", place, trip, extra))
        used.add(c.pid)
    # unique timestamps (kept photos keep theirs)
    rows.sort(key=lambda r: (r[1].astimezone(dt.timezone.utc), r[0].pid not in kept, r[0].pid))
    seen = set()
    fixed = []
    for r in rows:
        t = r[1]
        while (t.astimezone(dt.timezone.utc)) in seen:
            t += dt.timedelta(minutes=1)
        seen.add(t.astimezone(dt.timezone.utc))
        fixed.append((r[0], t, *r[2:]))
    rows = sorted(fixed, key=lambda r: (r[1].astimezone(dt.timezone.utc), r[0].pid))
    # write images and records
    img_dir, th_dir = OUT / "photos" / "img", OUT / "photos" / "thumb"
    img_dir.mkdir(parents=True, exist_ok=True)
    th_dir.mkdir(parents=True, exist_ok=True)
    for p in [q for q in list(img_dir.glob("*.jpg")) + list(th_dir.glob("*.jpg")) if not q.name.startswith("._")]:
        p.unlink(missing_ok=True)
    recs = []
    for i, (c, t, country, city, place, trip, extra) in enumerate(rows, 1):
        src = WORK / "img" / f"{c.pid}.jpg"
        with Image.open(src) as im:
            im = im.convert("RGB")
            full = im.copy()
            full.thumbnail((960, 960), Image.Resampling.LANCZOS)
            full.save(img_dir / f"{i}.jpg", "JPEG", quality=QUALITY_FULL, optimize=True, progressive=False)
            th = im.copy()
            th.thumbnail((360, 360), Image.Resampling.LANCZOS)
            th.save(th_dir / f"{i}.jpg", "JPEG", quality=QUALITY_THUMB, optimize=True)
            w, h = full.size
        files = {"file": f"photos/img/{i}.jpg", "thumb": f"photos/thumb/{i}.jpg", "width": w, "height": h,
                 "bytes": (img_dir / f"{i}.jpg").stat().st_size}
        if c.pid in kept:
            old = kept[c.pid]
            rec = {"id": i, **{k: v for k, v in old.items() if k != "id"}} | files
            recs.append(rec)
            continue
        name, lat, lon = place_for(city, place, c)
        typ = c.v.get("type") or "自然风光"
        if c.v.get("time") == "夜晚" and typ in ("街景", "建筑", "地标"):
            typ = "夜景"
        cap = str(c.v["caption"]).strip()
        title = f"{city} · {name}" if name != city else city
        rec = {
            "id": i, "kind": "photo", "device": "phone", "date": iso(t), "title": title,
            "text": _text(cap, t, country, city, name, trip, extra),
            "fields": {"year": str(t.year), "month": f"{t.year}-{t.month:02d}", "country": country, "city": city,
                       "type": typ, "trip": trip},
            **files,
            "place": {"name": name, "lat": lat, "lon": lon, "country": country, "city": city},
            "caption": cap, "tags": _tags(c, typ),
            "source": {"dataset": "Unsplash Lite", "id": c.pid,
                       "photographer": f"{c.pool.get('name') or c.pool.get('user')} (@{c.pool.get('user')})",
                       "url": c.pool.get("url") or f"https://unsplash.com/photos/{c.pid}",
                       "license": "Unsplash License"},
        }
        recs.append(rec)
    write_jsonl(OUT / "photo.jsonl", recs)
    write_attribution(recs)
    print(f"[photo] {'fresh build' if fresh else 'incremental'}: kept {len(kept)} previous photos "
          f"({len(kept_daily)} everyday), added {len(rows) - len(kept)}")
    return recs


def write_attribution(recs: list[dict]) -> None:
    lines = [
        "# 照片来源与授权（Photo attribution）",
        "",
        "本目录中的照片用于演示虚构人物林舟的手机相册，全部来自 Unsplash（https://unsplash.com），",
        "按照片逐张适用 Unsplash License（https://unsplash.com/license）：可免费用于商业与非商业用途，无需许可；",
        "不得原样出售，也不得汇编成与 Unsplash 相似或竞争的图库服务。下表按 id 列出每张照片的摄影师与原始页面。",
        "",
        "候选照片取自 Unsplash Lite 数据集（Hugging Face 镜像 `1aurent/unsplash-lite`，地点信息取自 Unsplash 官方 Lite",
        "数据集下载包，因为镜像中的地点列为空）。数据集只用于挑选候选；本仓库不包含数据集的任何元数据文本：",
        "图片说明由视觉模型根据画面重新生成，拍摄时间、地点与行程均为演示虚构。图片已缩放（长边 960 / 360 像素）并去除 EXIF。",
        "",
        "Photos: Unsplash, each under the Unsplash License. Selection used the Unsplash Lite dataset (HF mirror",
        "`1aurent/unsplash-lite`; locations from the official Lite download). No dataset metadata is redistributed;",
        "captions are model-generated, and dates/places/trips are fictional.",
        "",
        "| id | 文件 | 摄影师 (photographer) | Unsplash 页面 |",
        "|---:|---|---|---|",
    ]
    for r in recs:
        s = r["source"]
        who = s["photographer"].replace("|", "/")
        lines.append(f"| {r['id']} | `{r['file']}` | {who} | {s['url']} |")
    (OUT / "photos" / "ATTRIBUTION.md").write_text("\n".join(lines) + "\n", "utf-8")


def main() -> None:
    recs = build()
    by_trip = Counter(r["fields"]["trip"] for r in recs)
    by_city = Counter(r["fields"]["city"] for r in recs)
    size = sum(p.stat().st_size for p in (OUT / "photos").rglob("*.jpg"))
    print(f"[photo] {len(recs)} photos, {size / 1e6:.1f} MB images", dict(by_trip))
    print("[photo] cities:", dict(by_city))


if __name__ == "__main__":
    main()
