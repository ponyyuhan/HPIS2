# x86 AVX2 候选筛选、严格纠边和真实检索

在单请求流式 HPIC/HPIS 协议中同时替换明文与密文映射的排名内核。Q8 只用于保守初筛，判定所需的边界向量继续进行高精度计算。原生明文仍是标准实现，下面的分母使用更快的、同样启用 Q8 的明文映射路径。测试使用公开 GloVe 前 20,000 条、100 维、K=10、β=0.25，AMD Ryzen 5 5600X CPU。各查询均独立核对严格有序 Top‑10。[上层流式协议口径](../mapped_exact_x86/README.md)

| 每批 512 个查询 × 4 轮 | 原生明文 | Q8 映射明文 | Q8 映射密文 | 对最快明文的平均开销 | 配对 95% 区间 |
|---|---:|---:|---:|---:|---:|
| 前 512 个 | 1.914 ms | 1.762 ms | 1.837 ms | **+4.26%** | +4.00% 至 +4.54% |
| 独立后 512 个 | 1.752 ms | 1.615 ms | 1.691 ms | **+4.70%** | +4.46% 至 +4.96% |

两批共 4,096 个三路配对、12,288 次完整调用；每条返回的 ID 和顺序均由独立的 float64 全库穷举再核对。近并列、租户隔离和索引更新的程序级回归也已通过。仅在两条映射路径中启用的 Q8 内核不能用于解释相对未启用它的原生明文的负开销。记录中的 `select_us` 包含合并后的筛选和精排，`gemv_us` 为零。[首批汇总](pipeline_primary/summary.json) · [独立留出汇总](pipeline_holdout/summary.json) · [首批原始](pipeline_primary/samples.jsonl.gz) · [独立留出原始](pipeline_holdout/samples.jsonl.gz) · [集成测试](pipeline_holdout/pipeline-q8-tests.txt)

## 单独的数值内核对照

256 个独立测试向量，各重复 2 次，每个条件做 512 对，明密两条件累计 1,024 对。随机交换调用顺序，使用同一数据和相同原生密码库。相对原浮点初筛内核，明文计算由 126.75 µs 降至 63.61 µs，PPE 由 160.10 µs 降至 100.51 µs。全部结果同时与参照内核和全库真值一致。这部分计时限于进程内计算，前表来自另外运行的真实端到端服务。[内核摘要](summary.json) · [逐对结果](q8-raw.jsonl.gz) · [测试输出](q8-probe.txt)

独立实验使用 Debian 12、GCC 12.2、OpenSSL 3.0.22，启用 `-march=native`、单线程 BLAS；修复了 AVX2 回填归约中的 128 位加法 intrinsic 宽度。附上实验用源码、用于收集样本与核对独立真值的驱动。[源码存档](source.tar.gz) · [带验证的驱动](probe.py)

这些 x86 数字包含当时的 AID、K、holder、owner、nonce、期限检查和共享公开票据缓存。采样完成后新增的 q/rq 盲盐绑定与流尾截止时间复核没有在该实体记录中计时。冷启动、新索引预置和持续更新成本见各组汇总中的 `first_query_including_snapshot_us` 和 `setup`。

可在[该次流式协议源码](../mapped_exact_x86/pipeline_holdout/benchmark_sources.tar.gz)上重建端到端测量。解开上面的内核源码存档后，运行 `HPIS_REPO="$PWD" python /path/to/kernel/linux_build.py`。执行映射检索测试时设置 `PYTHONPATH="/path/to/kernel/activate:$PWD"`、`HPIS_MAPPED_NATIVE=/path/to/kernel/baseline.so`、`HPIS_Q8_PROBE=/path/to/kernel/fused.so` 和 `OPENBLAS_NUM_THREADS=1`，再使用 `python -m integration.mapped_index.bench` 的相同参数。该实验适配器只替换进程内排名实现。
