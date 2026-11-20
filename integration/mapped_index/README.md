# 在获授权的 HPIC 上做严格向量检索

这个可选部署将 HPIS 中已有的 RAP 向量索引和逐行 AES-GCM 向量密文预置给 HPIC。终端只向获授权的 HPIC 发送归一化查询、未经查询噪声扰动的旋转方向、VEK 与 DEK。HPIC 本地排名，HPIS 在验证所有权后一次取出最多 K 条正文。默认在同一条双向 TLS 请求内使用两帧，第一帧携带授权，第二帧携带选中的记录 ID。HPIS 开始核对 owner 的同时，已经验证持票人及本地委托的 HPIC 执行排名。适用于读多写少、HPIC 可通过受保护通道取得密态向量索引副本的部署。

## 安全边界与更新

- HPIS 仍只有 RAP 表示和密文，复用原本的 β 和原始记录格式。它接收 owner 签名的索引定位符、K、nonce 及已选 ID，始终按 AID 读取正文。HTTP 仍使用真实两跳 TLS，HPIC 到 HPIS 使用双向认证。为防同一 AID 与 K 下换题，终端以每次新生成的秘密盲盐对查询和旋转方向做承诺。owner 签名覆盖承诺，HPIC 从实际收到的查询重算，HPIS 在读正文之前复核。盲盐和向量仅在授权的 HPIC 一侧出现。
- HPIC 跨查询保留原始 RAP、AES-GCM 密文、公开的筛选辅助量和公开授权声明。查询方向、VEK、DEK、打开的原向量与正文不进入跨请求缓存。原生 OpenSSL 内核逐条认证选中的向量，清理本次计算的明文工作区。
- 新部署对 HPIC 的授权范围有实质变化。它持有全部向量密文和可关联本租户的索引定位符，每次查询同时获得原始方向及其无查询噪声的旋转方向。长期记录足够多的独立向量对可能推断旋转矩阵。要将它作为用户授权、受保护的计算区部署。普通 Linux 进程间的演示尚未验证硬件隔离。HPIS 观察最终访问模式。主动恶意存储服务的遗漏并不由此协议验证。
- 正常 SDK 写入或删除会在同一 SQLite 写事务内更新 AID 的版本。HPIS 在一次读事务中核对 HPIC 排名所用版本，并按该版本读取正文。如果发现旧版本，HPIS 原子发布新索引，本次不返回正文，SDK 以新签名和 nonce 重试。旧版密态文件会在下一次重建时被替换。备份与物理介质上的擦除属于额外的存储管理问题。尾帧可能因网络或排名而延迟，HPIS 收齐后复核请求期限，HPIC 在交付结果前再次核对到期时间。
- 实际部署中将共享目录设为 HPIS 可写、HPIC 只读。独立主机需要复制当代索引，发布开销按一个受影响租户的索引大小计。Linux 单机试验包含建文件和首次载入，跨机实际分发耗时需单独计量。无可用副本或需要原管线时，可继续使用原有 `Device.vector(..., refine=True)`。新 SDK 的带字段筛选路径仍执行原有严格协议。

## 为什么能保持精确

设 HPIS 的行表示为 `h = xR + e`，其中 `x`、`q` 为单位向量，`||e|| ≤ β/4`。HPIC 单独取得 `qR` 后，`h · (qR)` 与真实余弦至多相差一次文档扰动。先打开 K 个分数种子，它们的真实第 K 名为合法门槛。只有上界还可能越过门槛的项才需要认证解密。逐行球冠上界还能继续减少候选。代码在计算非线性球冠函数前扩展输入余弦误差，保护接近反向的极端样本。

普通和密文的本地精排共用同一选择器以及同一种稳定全序。通常用 FP64 核对归一化向量。当内部邻接项或第 K／K+1 名浮点区间相交时，利用存储 FP32 坐标的精确二进制有理表示比较内积符号以及平方内积与对方范数的交叉积，数学上相等时按 ID 递增。回归覆盖普通坐标排列产生的真平局、低于单个浮点间隔的真实分差、反向球冠、密钥错误、跨租户凭据混合和更新后的再查询。[数值复核](../../tests/test_mapped_numerics.py) · [授权与更新测试](tests.py)

## 运行

在项目根目录执行。需要 Python 3.10+ 和项目依赖。原生内核需要 C 编译器、`pkg-config` 和 OpenSSL 开发包。在 Debian/Ubuntu 可安装 `build-essential pkg-config libssl-dev`。省略 `HPIS_MAPPED_NATIVE` 会使用 Python 精排实现。已有的 `hpis init`、`hpis put` 和 `hpis delete` 继续使用。

```bash
.venv/bin/hpis init .hpis-mapped --dim 3
.venv/bin/python -m integration.mapped_index.build_native --output /tmp/hpis-mapped-rank.so
```

分别启动 HPIS 和 HPIC。HPIC 使用原生选择器，HPIS 只发布密态索引和读取正文。

```bash
.venv/bin/python -m integration.mapped_index hpis .hpis-mapped/hpis \
  --shared .hpis-mapped/vector-index --port 8444
```

```bash
HPIS_MAPPED_NATIVE=/tmp/hpis-mapped-rank.so \
  .venv/bin/python -m integration.mapped_index hpic .hpis-mapped/hpic \
  --shared .hpis-mapped/vector-index --port 8443 --store-url https://127.0.0.1:8444
```

```bash
.venv/bin/hpis put .hpis-mapped/devices/default examples/notes.jsonl
.venv/bin/python -m integration.mapped_index query .hpis-mapped/devices/default \
  --vector '[1,0,0]' -k 2
```

在 Python 中使用 `integration.mapped_index.protocol.MappedDevice(folder, url, pipeline=True)`。设置 `pipeline=False`（CLI `--serial`）可使 HPIC 先完成排名再向 HPIS 发送一次请求。两种选择的严格向量查询入口相同。第二次和之后的热查询只需一次 HPIC 到 HPIS 的调用。响应中的 `body_rows` 统计读取的正文，`opened_vectors` 统计查询中实际解开了多少条向量元数据，`preposition_bytes` 与 `preposition_us` 单列冷启动或更新后的重新发布成本。一个查询可以在退化、并列情况下打开很多向量元数据。

```python
async with MappedDevice(device_folder, "https://127.0.0.1:8443") as client:
    reply = await client.vector(query_vector, 10, refine=True)
```

## 配对测量

```bash
HPIS_MAPPED_NATIVE=/tmp/hpis-mapped-rank.so OPENBLAS_NUM_THREADS=1 \
  .venv/bin/python -m integration.mapped_index.bench \
  --dataset /tmp/glove-100-angular.hdf5 --rows 20000 --queries 512 --rounds 4 \
  --out /tmp/hpis-mapped-evaluation
```

三组请求共享同一个终端 TLS 连接、HPIC 和 HPIS 进程、相同数据库与授权流程。默认测量授权与排名重叠的分帧请求。为两种映射模式一起添加 `--serial` 可复现串行对照。每个问题以随机顺序执行三种模式。三者共用同一个 issuer 签名公开票据验证缓存。每次仍校验到期时间、持钥签名、HPIS owner 签名及两个 nonce。计时从 SDK 收到原始向量到终端核对 ID 和正文，独立数据集真值在计时外计算。真实应用部署需要分别考虑操作系统调度、索引预置及写入频率。[ARM64 共用验票结果](../../results/mapped_exact_archive/arm64_shared_auth/summary.json) · [x86 共用验票](../../results/mapped_exact_x86/shared_auth/summary.json) · [x86 独立留出集](../../results/mapped_exact_x86/shared_auth_holdout/summary.json)

### 单请求内重叠授权与排名

每格耗时是 SDK 输入到校验正文的平均端到端时间。两种明文分别是 HPIS 原生排序和同部署 HPIC 排序，均含相同的 issuer 验证复用。开销分母取二者中更快者。

| Linux 平台和查询 | 原生明文 | 同拓扑明文 | 密文精确 Top-10 | 对最快明文的平均开销 | 配对 95% 区间 |
|---|---:|---:|---:|---:|---:|
| x86，前 512 | 1.401 ms | 1.295 ms | 1.401 ms | +8.16% | 7.67% 至 9.00% |
| x86，独立后 512 | 1.489 ms | 1.401 ms | 1.514 ms | +8.10% | 7.67% 至 8.77% |
| ARM64，前 512 | 0.827 ms | 0.706 ms | 0.768 ms | +8.74% | 7.98% 至 9.79% |
| ARM64，独立后 512 | 0.851 ms | 0.730 ms | 0.797 ms | +9.13% | 8.13% 至 10.38% |

每行每种模式使用 512 个不同查询，各 4 轮，均与独立的全库结果完整同序。x86 两行来自真实分帧版，含索引、K、持钥与 owner 授权验证。测量后交付代码增加了同账户目标查询的盲盐绑定以及尾帧后的期限复核。ARM64 两行使用含上述验证的交付协议，单窗口点估计均低于 10%，独立留出集区间上端为 10.38%。原始阶段计数中的 `ticket_expiry_checks` 在这些记录中统计准入检查。最终分帧版还在响应放行前再次验证，现计数包含两次。该计算边界仅适用于 HPIC 可以预置受保护索引的部署。[x86 前 512](../../results/mapped_exact_x86/pipeline_primary/summary.json) · [x86 独立后 512](../../results/mapped_exact_x86/pipeline_holdout/summary.json) · [ARM64 前 512](../../results/mapped_exact_archive/arm64_bound_pipeline_primary/summary.json) · [ARM64 独立后 512](../../results/mapped_exact_archive/arm64_bound_pipeline_holdout/summary.json)

### 串行对照

| 20,000 × 100 维，β=0.25 | 原生明文 | 映射明文 | 映射严格密文 | 对同拓扑明文 | 对两种中更快的明文 |
|---|---:|---:|---:|---:|---:|
| Linux ARM64，测试 1–512 | 0.881 ms | 0.812 ms | 0.875 ms | +7.80% | +7.80% |
| Linux x86，测试 1–512 | 2.091 ms | 2.091 ms | 2.244 ms | +7.30% | +7.32% |
| Linux x86，测试 513–1,024 | 1.631 ms | 1.695 ms | 1.825 ms | +7.69% | +11.89% |

每行每种模式各 2,048 次，三个数据组的严格有序 Top-10 均为 2,048/2,048 一致。ARM64 上对较快明文的固定基线配对区间为 6.57% 至 9.06%。x86 留出集对较快的原生明文为 11.23% 至 12.73%，因此跨配置稳定小于 10% 的结论仍有例外。密态索引文件 16,720,256 B，明文 8,160,256 B。ARM64 热查询平均打开 67.66 条经过认证的向量密文，只取最终 10 条正文。密态索引冷发布为 25.90 ms，含建连、发布与重试的首次观察请求为 32.22 ms。
