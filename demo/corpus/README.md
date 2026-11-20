# 演示邮箱语料（demo/corpus）

为「HPIS 2.0 密态智能问答」演示生成的虚构中文个人邮箱：林舟（42 岁，云帆智能科技 解决方案部总监）在 2024-01-01 至 2026-11-20 18:00（周五）之间的约十万封邮件和附件文本。人物、机构、品牌、号码均为虚构，卡号和手机号均为脱敏写法。

## 输出（demo/data/）

| 文件 | 内容 |
|---|---|
| `mailbox.jsonl` | 每行一封邮件，按时间排序，`id` = 1..N |
| `presets.json` | 6 个预置问题：查询计划（结构化过滤 + 语义串）、标准答案要点、证据与干扰邮件 id |
| `bible.json` | 人物、机构、服务商、项目等世界设定 |
| `stats.json` | 分类计数、附件数、字节数、日期范围 |
| `embeddings.f32` / `embeddings.json` | bge-small-zh-v1.5 向量（第 i 行 = id i+1，float32，已归一化）及文本拼接方式 |

邮件字段：`id, thread, date(+08:00), folder(inbox|sent|archive), category, from{name,email,org}, to[], cc[], subject, body, attachments[{name,mime,size,kind,text}], facets{org,project,people[]}`。
附件只保存抽取出的文字（`text`），`size` 是虚拟的原文件字节数。`facets.org` 是邮件所涉的主要外部机构（客户、供应商、银行、学校等），内部事务为 `null`。

## 重新生成

在项目根目录使用 `.venv-demo/bin/python`，需要环境变量 `OPENROUTER_API_KEY`。所有 LLM 回复缓存在 `demo/data/cache/llm/`，重跑只会补齐缺失部分；随机数全部固定种子。

```bash
PY=.venv-demo/bin/python
$PY -m demo.corpus.bible                              # bible.json
$PY -m demo.corpus.needles                            # 6 条故事线的金标准/干扰邮件（qwen3.6-plus，必含事实校验）
$PY -m demo.corpus.gen_threads --scale 1.5            # 第 1 批线程（qwen3.6-flash）
$PY -m demo.corpus.gen_threads --pass 2 --scale 2.0   # qwen3.6-flash / gemma-4-26b 各半
$PY -m demo.corpus.gen_threads --pass 3 --scale 2.0   # qwen3.5-flash
$PY -m demo.corpus.gen_threads --pass 4 --scale 2.0   # qwen3.5-flash（按预算只取一部分）
$PY -m demo.corpus.gen_misc all                       # 学校通知、周报、促销、电子刊物
$PY -m demo.corpus.assemble --dry --target 100000     # 先跑一次，确定实际采用的线程（gen/used_jids.json）
$PY -m demo.corpus.gen_attach threads.jsonl --cap 2400 --lite
$PY -m demo.corpus.gen_attach threads2.jsonl threads3.jsonl threads4.jsonl --cap 4000 --lite
# 先写到 staging，向量算完、校验通过后再整体替换 demo/data 下的文件
export CORPUS_OUT=demo/data/staging
$PY -m demo.corpus.assemble --target 100000           # mailbox.jsonl, stats.json, presets.json
$PY -m demo.corpus.embed_all 2                        # 2 进程；按文本哈希缓存，首次约 16 分钟
$PY -m demo.corpus.validate                           # 校验 + 检索 top-5
mv demo/data/staging/{mailbox.jsonl,presets.json,stats.json,embeddings.f32,embeddings.json} demo/data/
```

`assemble --dry` 只统计不写文件。模板类邮件（银行、航旅、电商、OA、日历等）由 `templates.py` 用代码生成；金标准账单、航班、酒店、体检报告与同类邮件出自同一模板。

## 模块

- `bible.py` 世界设定；`calendar.py` 工作日与节假日
- `llm.py` 异步 OpenRouter 客户端（并发、重试、磁盘缓存、费用累计）
- `gen_threads.py` 场景规划与线程生成；`gen_misc.py` 刊物/促销/学校/周报素材；`gen_attach.py` 附件全文
- `needles.py` 6 条故事线（强模型 + 必含事实校验）；`templates.py` 模板化通知
- `assemble.py` 清洗（虚构品牌替换、证件号/卡号脱敏）、引用回复、签名、文件夹、编号与裁剪
- `presets.py` 预置问题；`embed_all.py` 向量；`validate.py` 校验
