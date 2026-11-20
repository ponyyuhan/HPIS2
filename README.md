# HPIS 2.0 密态检索

> **2026-11-20 新交付**：领导演示 [`demo/`](demo/README.md)、Linux 验收 [`acceptance/`](acceptance/README.md)、H200 模型部署 [`demo/deploy/h200/`](demo/deploy/h200/README.md)，总览见 [HPIS2交付说明.md](HPIS2交付说明.md)。以下为此前版本的说明。

终端为每个匿名标识 AID 建立独立索引。HPIS 在该 AID 内选出结果，只把命中的加密正文交给获授权的 HPIC 解密。这个 Linux 项目提供可运行的端侧 Python SDK、独立 HPIS/HPIC 服务、旧版 C1–C4 接入和验收基准。

**[Linux 验收报告](results/README.md)** 给出明密文端到端时延、召回率、正文读取量，以及实际运行原版 HPIS 1.0 的结果。

## 给领导的现场演示

[打开 HPIS 2.0 工作台](http://127.0.0.1:8769/) · [双击启动](启动演示.command) · [离线实录](showcase/static/media/hpis-briefing.mp4)

在十万封合成邮件中现场提问，并排观察普通云直传和 HPIS 密态取证。右上角的投屏模式支持现场运行、数据流回放与点击原始邮件。可从「对比口径」查看一次性预置成本，从「检索实测」核对独立基准。[演示讲解与操作](showcase/README.md) · [Qwen 27B / Gemma 4 的 H200 部署](deployment/h200/README.md)

## 在本机试一次

本工作区已安装本地虚拟环境。运行 `.venv/bin/python -m examples.demo` 会自动启动两个独立服务、执行三组查询并关闭服务。

需要 Python 3.10 或更高版本。以下命令从项目根目录运行。示例使用三条合成备忘录和三维示意向量。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[bench]'
OPENBLAS_NUM_THREADS=1 hpis init .hpis --dim 3
```

在两个终端分别启动存储和计算服务。默认仅监听回环地址。HPIC 与 HPIS 使用双向 TLS。

```bash
.venv/bin/hpis serve hpis .hpis/hpis --port 8444
```

```bash
.venv/bin/hpis serve hpic .hpis/hpic --port 8443
```

在第三个终端写入数据并查询。每次调用都经过真实服务连接。

```bash
.venv/bin/hpis put .hpis/devices/default examples/notes.jsonl
.venv/bin/hpis query .hpis/devices/default --field 城市 柏林 --word 出差
.venv/bin/hpis query .hpis/devices/default --fuzzy 火車票
.venv/bin/hpis query .hpis/devices/default --vector '[1,0,0]' -k 2
.venv/bin/hpis query .hpis/devices/default --vector '[1,0,0]' -k 2 --refine
```

前两条查询都应返回记录 1。最后一条应依次返回记录 1 和 2。响应中的 `stats.body_rows` 和 `stats.body_bytes` 统计 HPIS 为本次请求实际取出的正文条数和应用层字节数。精排另有候选向量元数据计数。初始票据有效期一天，可在签发方环境执行 `hpis renew .hpis default`。另一个独立 AID 可用 `hpis enroll .hpis second` 创建。

## 查询能力

| 路径 | 云端工作 | 数据与精度 |
|---|---|---|
| 字段与关键词 SSE | 通过租户专属 HMAC-SHA256 标签查询 SQLite 倒排表，组合交集或并集 | 规范化字符串后精确匹配。支持中文、Unicode、任意明确登记的字段与关键词 |
| 一次编辑模糊词 | 通过删除签名和带位置的替换签名查倒排表 | 对已登记的关键词精确实现 Levenshtein 距离 ≤ 1。支持插入、删除和替换 |
| 密文向量快速模式 | 对随机化的 RAP 向量表示做角度排序 | 返回近似余弦 Top-K。每次读 K 条正文，可叠加字段、关键词条件 |
| 密文向量精排 | HPIS 用扰动界限选出覆盖真实 Top-K 的候选。HPIC 用单独授权的向量密钥精排，再凭一次性句柄取正文 | 可重现明文精确 Top-K，按记录 ID 打破平局。额外开销随候选规模变化 |
| 受信计算区精确检索 | HPIS 在第一次获授权查询时打开该 AID 的 AES-GCM 向量索引，在受信内存中复用。写入和删除使内存索引失效 | `snp` 要求 AMD SEV-SNP 远程证明。`trusted-process` 用普通进程运行同一计算路径，适合测量软件开销 |

写入接口支持插入和覆盖，删除会同步清理正文、向量与倒排项。每个 SDK 写请求以 SQLite 事务提交。`--combine any` 取并集，默认取交集。可检索关键词由调用方提供。向量检索支持有限的非零输入，以归一化余弦为度量。可用 `--beta` 在开户时设置 RAP 的扰动量，已建的向量索引与该值配套。

## 端云边界

- 终端保存 AID、索引密钥、向量密钥、数据密钥和 owner 签名私钥。每次生成密文目标，把所有权证明与请求一起封给 HPIS。原生 SDK 使用 libsodium 的 X25519 密封盒。终端和 HPIC 之间使用 TLS 1.3。HPIC 验证匿名持钥票据，再转交内层请求。生产部署应把样例生成的各角色目录分别保管。[密封盒规范](https://doc.libsodium.org/public-key_cryptography/sealed_boxes)
- 正文、精排向量分别使用独立 AES-256-GCM 密钥。认证数据绑定记录号与用途。快速模式下 HPIS 持有索引与数据密文。选择 `--refine` 时，HPIC 额外获得当前查询与向量密钥。请求的防重放窗口保存在运行中的进程内，重启会清空窗口。普通票据是可链接的短期持钥票据。有历史匿名签发要求时，可启用下述 Instance-2 接口。
- 原生 2.0 索引在一次登记期间复用 AID，HPIS 可以关联同一租户的查询。旧版 /resolve 分支继续执行归档协议的 AID 和数据密钥轮换。SSE 会泄露重复标签、访问模式与命中数量。原始向量另有经过认证加密的副本。用于排序的 RAP 是一种距离比较保持哈希，同一 AID 内的表示保留近似几何结构，观察者可利用已知明密文对应关系做成员推断。参数 β 控制扰动和检索误差，没有通用的隐私位数解释。更强的在用数据保护依赖真实受信执行环境。远程证明的部署与测试见专页。[RAP 论文，第 6 节](https://eprint.iacr.org/2026/2570) · [SNP 部署与验真](docs/snp.md)
- 精排的几何界限针对按协议生成的索引和诚实执行的检索服务。AES-GCM 可检测篡改的记录，主动遗漏结果的服务器仍可影响检索完整性。当前 Linux 端侧使用受文件权限保护的软件密钥。鸿蒙 HUKS、商用 HPIC 和真机耗时需要各自的设备或接口。

## 复用原版 HPIS 和 HPIC

已复用用户私有仓库的原版 OAuth、HPIC、HPIS、客户端和匿名签发代码。旧密文可由有权的终端解开并建立新索引，普通旧请求继续按原逻辑轮换 AID 和数据密钥。原版 HPIC 使用示例模型。兼容测试同时核对交给模型的 C3 正文和端侧解开的 C4。

- [原版 C1–C4 接入、迁移与配对结果](integration/legacy/README.md)
- [归档 Instance-2 和 SubAID 身份接入](integration/instance2/README.md)
- [未修改的 HPIS 1.0 全量路径实测与复现](integration/baseline_v1/README.md)

## 复现实验

```bash
.venv/bin/python -m unittest discover -s tests -v
curl -fL https://ann-benchmarks.com/glove-100-angular.hdf5 -o /tmp/glove-100-angular.hdf5
OPENBLAS_NUM_THREADS=1 .venv/bin/python -m bench.run local \
  --dataset /tmp/glove-100-angular.hdf5 --rows 20000 --queries 512 --rounds 4 \
  --out /tmp/hpis-evaluation
```

本机还保留了可离线重跑的 [GloVe 子集](/Users/yuhan/Library/Caches/hpis-search/glove-100-angular-first100k.hdf5)，含原始训练集前 100,000 行和测试集前 1,040 行。将上述命令的 `--dataset` 指向该文件即可覆盖本次所有查询。

基准会建立临时数据库，使用两个真实 TLS 服务跳点，对每个查询随机平衡明文和密文的执行顺序。计时从 SDK 收到原始目标起，到终端取得并检查正文为止。对公开向量的独立全库真值计算在计时外。输出 `summary.json` 和压缩逐请求记录。跨机器复现使用 `python -m bench.run prepare`、`serve` 和 `external`，各子命令均提供 `--help`。
