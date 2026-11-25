# 设备数据（demo/corpus/devices）

虚构人物林舟（42 岁，云帆智能科技 解决方案部总监，住北京望京，妻子苏晴、儿子林小禾）在三台设备上的个人数据，
时间范围 2024-01-01 至 2026-11-20 18:00（+08:00），与 PC 上的十万封邮件（`demo/data/mailbox.jsonl`）同属一人。
人物、机构、品牌、号码均为虚构。格式约定见 `demo/PLAN-v3.md` §2。

| 设备 | kind | 文件（`demo/data/devices/`） | 规模 |
|---|---|---|---|
| 手机 `phone` | `photo` | `photo.jsonl`、`photos/img/{id}.jpg`（长边 960，质量 80）、`photos/thumb/{id}.jpg`（长边 360）、`photos/ATTRIBUTION.md` | 397 张，图片 40.7 MB + 缩略图 6.7 MB |
| 手表 `watch` | `sport` | `sport.jsonl` | 2,834 条：跑步 319、骑行 32、游泳 42、步行 80、羽毛球 43、力量训练 23、睡眠 1,055、日常 1,054、周报 151、月报 35 |
| 车机 `car` | `car` | `car.jsonl` | 2,850 条：行程 2,183、充电 224、导航 212、保养 5、提醒 40、周报 151、月报 35 |
| — | — | `embeddings_{kind}.f32` / `.json`、`presets.json`（9 个预置问题）、`stats.json` | 向量、预置问题、统计 |

所有记录：`id`（= 行号）、`kind`、`device`、`date`（带时区；境外照片与手表记录用当地时间和当地时区 +02:00 / +09:00）、
`title`、`text`（模型直接阅读的中文）、`fields`（SSE 标签：照片 year/month/country/city/type/trip，运动 year/month/activity，
用车 year/month/event/city）。运动记录的 `metrics` 只放数字，非数字信息（入睡时间、周期起止、`partial` 等）放在 `detail`；
用车汇总同样有 `metrics` / `detail`。截至 11 月 20 日 18:00 尚未结束的本周、本月也有汇总（`detail.partial = true`，
时间戳 2026-11-20T18:00:00+08:00）。字段取值的完整分布见 `stats.json`。

## 时间线（`life.py`，三台设备共用）

- 国内出差、家庭出行和春节回扬州：取自邮箱 `category == "travel"` 的机票、火车票和酒店邮件（日期、航班、起降时间），
  车机记录据此开车去机场、停车、接机回家；手表记录据此在出差城市晨跑；照片放在对应城市。其中家庭出行有成都
  2024-07-20—07-26（JW1405/JW1410，邮件 id 14723、17880、18499；设备上只有手表和车机记录，相册里没有成都照片）和三亚
  2025-07-25—07-31（JW1953/JW1958，邮件 id 51942、55372、55909）；2026 年春节在扬州（JW1861/JW1864，2-13—2-21，
  只有一张早茶照片）。天津出差（2026-09-14—16）按邮件是高铁往返，车机没有记录。
- 新增行程（只存在于设备数据）：
  - 韩国旅游 2024-10-01—10-06（国庆假期，一家三口；首尔 10-01—03、釜山 10-04—05、10-06 回首尔；+09:00；
    首都机场 T3 往返，08:30 起飞、20:30 落地；10 月 2 日汉江晨跑、10 月 3 日北岳山城郭路徒步、10 月 5 日海云台晨跑）。
  - 日本赏樱 2025-04-03—04-08（东京、京都、奈良，+09:00，4 月 4 日皇居外苑晨跑）。
  - 德国出差 2025-09-14—09-20（慕尼黑、柏林、法兰克福，+02:00，9 月 15 日英国花园晨跑；9 月 14 日开车去首都机场 T3，
    9 月 20 日上午落地开车回家）。
- 韩国旅游与邮箱的出入：国庆假期里没有一段干净的 5—6 天窗口。邮箱里的家庭邮件写到 10-02 林舟去扬州出差（id 24936）、
  10-03 计划去通州运河公园放风筝（id 24912、24917）、10-04 林曼从上海来家里（id 25034）、10-05 周六和苏杭带爸体检
  （id 25038）以及"正好我也在京"（id 25136）；学校通知 10-07 返校（id 24667）。速达出行的乘车收据在 10-03、10-06、10-07
  也有北京行程，但收据在 1,055 天里出现 840 天，邮箱自己的航班显示林舟在外地的 71 个整天里也有 55 天有北京收据，
  因此不当作位置证据。如需换窗口，只改 `life.py` 的 `TRIPS` 和 `_RNG_REPLAY`（见下），以及 `sport.TRIP_RUNS`、
  `photo_build.foreign_and_family_scenes` 里的日期，然后重跑。
- 周末出游（`FIXED_OUTINGS` + 按季节随机）：长城、颐和园、香山、北戴河、天津、古北水镇等；体检日（2025-04-11、
  2026-04-10，邮箱里的体检）、保养日、两次北京半程马拉松（2024-04-14 净成绩 1:55:29，2026-04-19 净成绩 2:14:41）。
  随机出游按日期顺序抽签；`_RNG_REPLAY` 让韩国旅游占用的国庆假期日仍然照旧抽签（结果丢弃），所以之后的出游保持不变。

手表的体能曲线与邮箱体检一致：2025-04 体重 72.5 kg，2025 下半年运动很少，2026-04 体检 77.8 kg，此后按医生建议
每周≥150 分钟中等强度运动，体重与静息心率回落（2026-11 体重 74.3 kg、静息心率 57）。周报 / 月报的每个数字都由当期
记录算出（`sport.aggregate`、`car.aggregate`），`validate.py` 会重新计算并逐字比对。车机按电池电量和里程表连续模拟
（78 kWh 电池；家充 7 kW、公司快充、高速与目的地充电），每段行程的起点等于上一段的终点。

## 预置问题（`presets.json`）

格式同 `demo/data/presets.json`，另加 `kind`、`device`；`evidence_ids` 同时放在顶层（PLAN-v3）和 `gold` 里（原格式）。
`plan` 的键：`kind countries cities trips types activities events date_from date_to semantic`（没用到的为空列表 / null）。
金标准事实全部从生成的记录里读出，`validate.py` 检查每条事实的 `match` 能在证据记录中按子串找到，并检查证据记录满足 `plan` 的过滤条件。

| id | 问题 | 要点 |
|---|---|---|
| `photo_germany` | 整理我在德国出差时拍的照片 | 2025 年 9 月 14—19 日；慕尼黑 / 柏林 / 法兰克福；英国花园、柏林电视塔等 |
| `photo_korea` | 给我 2024 年去韩国旅游的照片 | 2024 年 10 月 1—6 日（国庆）；首尔 17 张、釜山 13 张；海云台、汉江公园 |
| `photo_seoul_night` | 找出我在首尔拍的夜景照片 | 2024 年 10 月 1—2 日；南山 首尔塔、汉江公园 |
| `photo_sakura` | 去年春天去日本看樱花，拍了哪些照片？ | 2025 年 4 月；东京、京都、奈良 |
| `sport_month` | 我最近一个月的运动情况怎么样？ | 11 月跑步 9 次 67.47 公里，10 月 17 次 139.94 公里，静息心率 57，体重 74.3 kg |
| `sport_halfmarathon` | 我今年北京半马跑了多长时间？和上次比怎么样？ | 2:14:41（2024 年 1:55:29） |
| `sport_sleep` | 我上周睡得怎么样？ | 平均 6 小时 39 分，最短 / 最长一晚 |
| `car_month` | 上个月我开车去过哪些地方，充了几次电？ | 充电 8 次；北戴河、慕田峪长城、故宫、天坛、大兴机场等 |
| `car_charging` | 这个月给车充了几次电？一共花了多少钱？ | 4 次，¥86.83 |

（具体数值以 `presets.json` 为准；重新生成后会随数据一起更新。）

## 重新生成

在项目根目录（本 worktree）执行。下载、缓存和中间文件放在 `$HPIS_DEVICES_BUILD`（默认 `~/.cache/hpis2-devices-build`；
本次构建用的是外置盘 `/Volumes/ssd/hpis2-work/devices-build`），仓库里只放最终输出。手表与车机只需标准库；
照片的抽取、下载和图片处理在一个一次性虚拟环境里运行（pyarrow、Pillow），不往 `.venv-demo` 装任何东西；
运行时（`python -m demo`）不需要新依赖。

```bash
export HPIS_DEVICES_BUILD=/Volumes/ssd/hpis2-work/devices-build      # 或任意空目录
PY=.venv-demo/bin/python
uv venv --python 3.12 --link-mode copy $HPIS_DEVICES_BUILD/venv
uv pip install --python $HPIS_DEVICES_BUILD/venv/bin/python --link-mode copy pyarrow pillow numpy
VPY="$HPIS_DEVICES_BUILD/venv/bin/python -I"                          # -I：不从当前目录或下载目录加载代码

# 1. 下载数据（不可信数据，单独目录，只当数据读）
#    HF 镜像 1aurent/unsplash-lite：data/train-0000{0,1,2}-of-00003.parquet → $HPIS_DEVICES_BUILD/downloads/parquet/
#    Unsplash 官方 Lite 包（含地点列）：https://unsplash.com/data/lite/latest → $HPIS_DEVICES_BUILD/downloads/unsplash-official/lite.zip
$VPY demo/corpus/devices/photo_extract.py        # 可选：HF 镜像元数据摘要（镜像的地点列为空，仅作对照）
python3 -I demo/corpus/devices/photo_pool.py     # 候选池 work/pool.jsonl（目标国家 / 城市 + 无地点的通用场景，共 2,400 张）
$VPY demo/corpus/devices/photo_fetch.py          # 960 px 下载（4 并发、重试、通用 UA）→ Pillow 重新编码、去 EXIF
python3 -I demo/corpus/devices/photo_caption.py  # 视觉模型标注（需 OPENROUTER_API_KEY；按图缓存，重跑不重复计费）

# 2. 手表、车机、相册
$PY -m demo.corpus.devices.sport                 # sport.jsonl
$PY -m demo.corpus.devices.car                   # car.jsonl（读取手表计划：去奥森 / 朝阳公园跑步要开车）
$VPY demo/corpus/devices/photo_build.py --total 440   # photo.jsonl、photos/…（增量；加 --fresh 从头重建，440 是上限）

# 3. 向量、预置问题、统计、校验
$PY -m demo.corpus.devices.embed                 # embeddings_{photo,sport,car}.f32/.json（demo.embed，文档侧，约 30 秒）
$PY -m demo.corpus.devices.presets               # presets.json、stats.json
$PY -m demo.corpus.devices.validate              # 模式、数量、图片尺寸、汇总复算、向量对齐、金标准事实、检索报告
```

随机数全部固定种子（按日期派生），同样的输入得到同样的输出（已验证两次运行的 jsonl 与图片字节一致）。
相册是增量构建：`photo.jsonl` 里已有的照片只要所属行程、出游或日期仍在时间线上，就保留原来的日期、地点和文字；
整段被移走的行程（例如韩国旅游改到国庆）重新选图，落到外出日子上的日常照片一换一补上。`--fresh` 则按当前规则整本重建
（会重新分配所有日常照片的日期）。
外置 exFAT 盘上 macOS 会生成 `._*` 文件：代码读目录时会跳过它们；uv 的缓存目录不要放在 exFAT 上（会破坏 wheel 安装）。

## 照片来源与授权

- 照片来自 Unsplash，逐张适用 Unsplash License（可免费商用 / 非商用，无需许可；不得原样出售，不得汇编成与 Unsplash
  相似或竞争的服务）。每张照片的摄影师与原始页面写在 `photos/ATTRIBUTION.md` 和记录的 `source` 字段里。
- 候选来自 Unsplash Lite 数据集（HF 镜像 `1aurent/unsplash-lite`；镜像里地点列全是 "nan"，地点信息改用 Unsplash
  官方 Lite 下载包的 `photos.tsv000` 与 `keywords.tsv000`，照片 id 相同）。注意：官方 Lite 包的 TERMS.md 只授权在内部使用
  数据集（下载、存储、训练），并禁止再分发数据集内容；所以数据集只用来挑选候选，仓库里不包含数据集的任何元数据文本——
  图片说明由视觉模型看图重写，地点、时间、行程是虚构的。如果仓库要公开发布，请先确认这种用法可以接受。
- 挑选规则：不选以清晰人脸为主体的照片，不选带醒目品牌标志的照片，不选星空、抽象、影棚纯色背景、动物特写等不像手机随手拍的照片；
  场景与地点、季节、昼夜一致（视觉模型判断的地区 / 可见文字语种 / 地标 / 季节 / 时段作为硬约束，数据集自带地点的照片只用于
  同一城市或同一国家的通用场景）。数据集以自然风光为主，所以北京日常照片以公园、花木、四季、餐食、家中和办公室为主。

## 视觉模型与费用

`qwen/qwen3.8-flash`（OpenRouter，$0.15/M 输入、$0.47/M 输出，640 px 图片，关闭推理），每张候选一次调用（约 $0.00017/张）。
本次共标注 2,398 张候选，花费 **US$0.41**（`stats.json` 的 `llm` 字段从缓存累计）。没有用数据集自带的英文描述作兜底：
调用失败就重试，仍失败的照片直接不用。

## 模块

- `common.py` 路径与工具；`life.py` 共享时间线（行程、周末出游、地点、节假日）
- `sport.py` 手表；`car.py` 车机（电量 / 里程模拟、周报月报）
- `photo_extract.py`、`photo_pool.py`、`photo_fetch.py`、`photo_caption.py`、`photo_build.py` 相册流水线
- `embed.py` 向量；`presets.py` 预置问题与统计；`validate.py` 校验
