# x86 全库严格 Top‑10 的映射索引对照

20,000 个 100 维 GloVe 向量，β=0.25。HPIC 可访问预置到本地的受保护向量索引，收到一次授权后执行严格精排，HPIS 仍持有加密正文。首批和独立留出分别使用测试行 1–512 和 513–1,024，各 4 轮。每个查询的三条路径均通过两跳 TLS 请求，核对十条正文及完整排序。每组有 2,048 个三路交叉、6,144 次真实调用。实验在同一台波兰 AMD Ryzen 5 5600X Linux 依次运行。

## 同一授权策略和两种明文分母

三臂使用同一 HPIC、HPIS、热 TLS 连接，公开 issuer 票据声明复用同一缓存。每个请求仍验证到期、holder 和 owner 签名、两处 nonce。对每份原始记录检查了公开声明缓存命中标志、成功路径报告的验证项，以及对独立穷举真值的逐 ID 比对。holder 和 owner 的实际验证代码在对应服务路径中执行；原始计数是成功分支上报的检查项。

| 协议和查询批次 | 原生明文 | 同拓扑明文 | 密文 | 对原生明文 | 对同拓扑明文 |
|---|---:|---:|---:|---:|---:|
| 串行、首批 | 2.091 ms | 2.091 ms | 2.244 ms | +7.33% | +7.30% |
| 串行、独立留出 | 1.631 ms | 1.695 ms | 1.825 ms | **+11.89%** | +7.69% |
| 流式、首批 | 1.401 ms | **1.295 ms** | 1.401 ms | +0.002% | **+8.16%** |
| 流式、独立留出 | 1.489 ms | **1.401 ms** | 1.514 ms | +1.73% | **+8.10%** |

单请求流式授权使 HPIS 对 owner 的验证与 HPIC 排名部分重叠。两条 mapped 路径都使用同一流式协议，原生明文沿用原检索。前两行的串行实测包括一组相对更快原生明文超过 10%。后两行，最快分母均是同拓扑明文。与它相比，首批和留出平均开销的配对 95% 区间分别为 **+7.67% 至 +9.00%**、**+7.67% 至 +8.77%**。两组流式原始记录共 12,288 条，外部独立复核的所有有序 Top‑10 均与穷举相同。[首批结果](pipeline_primary/summary.json) · [独立留出](pipeline_holdout/summary.json) · [首批原始](pipeline_primary/samples.jsonl.gz) · [留出原始](pipeline_holdout/samples.jsonl.gz) · [串行首批](shared_auth/summary.json) · [串行留出](shared_auth_holdout/summary.json)

早期版本、已修复精确边界但原生明文尚未共享 issuer 缓存的两批，以及仅把同一 issuer 缓存回移到旧版完整原生栈的保守对照也已保留。最初一版对更快的原生明文为 +11.06%，最简旧缓存原生栈的独立留出为 +8.89%（95% 区间 +8.46% 至 +9.51%）。对应的旧服务回移补丁仅涉及公开票据缓存和成功路径检查项记录。[首版](summary.json) · [修正后首批](final/summary.json) · [修正后留出](holdout/summary.json) · [旧整栈缓存留出](legacy_cached/summary.json) · [回移补丁](legacy_cached/legacy_ticket.patch)

每次建索引与装载均有额外成本。此工作量的密文映射图像为 **16,720,256 B**，明文为 8,160,256 B。每组的 `setup` 与 `first_query_including_snapshot_us` 记录了预置和首次加载。流式数字来自采样时的完整授权版本。之后新增的 32 字节盲盐 q/rq 绑定和流尾截止时间复核，另需按最终实现复测。以上数值限定于可预置索引、已授权 HPIC、CPU 热索引和诚实执行协议的部署场景。[节点与释放记录](../vector_attribution_x86/compute.json)

为了复现此次 x86 数据，保留了[流式测量代码](pipeline_holdout/benchmark_sources.tar.gz)。其中只含本次实际采样用的 Python 和 C 源文件，便于区分后来的授权绑定版本。编译与执行入口分别为 `python -m integration.mapped_index.build_native --output /tmp/hpis-rank.so`、`HPIS_MAPPED_NATIVE=/tmp/hpis-rank.so OPENBLAS_NUM_THREADS=1 python -m integration.mapped_index.bench --dataset /path/to/glove.hdf5 --rows 20000 --queries 512 --query-offset 512 --rounds 4 --out /tmp/hpis-mapped-holdout`。
