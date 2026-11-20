# 原版 HPIS 1.0 与 HPIC 的 Linux 对照

存储端收到匿名标识 AID 后，原版 HPIS/PIC 从一份完整的加密上下文取数。这个对照测量一次实际调用的搬运量和端侧解开 C4 的时延。使用用户私有仓库 `ponyyuhan/mcp_agent` 的根目录实现，版本为 `f1207dfa8c3909293e217e2961934c085869263a`。[原版 HPIS](https://github.com/ponyyuhan/mcp_agent/blob/f1207dfa8c3909293e217e2961934c085869263a/hpis_server.py) · [原版 PIC](https://github.com/ponyyuhan/mcp_agent/blob/f1207dfa8c3909293e217e2961934c085869263a/pic_server.py) · [原版客户端](https://github.com/ponyyuhan/mcp_agent/blob/f1207dfa8c3909293e217e2961934c085869263a/client_pic.py)

## 运行方式

需要 Python 3.10+、能读取上述私有仓库的 GitHub CLI，及 ANN-Benchmarks 的 GloVe 100 维 HDF5。以下命令在本项目根目录执行。可把 `source.py` 单独提取的七个源文件和来源记录传到隔离的 Linux 环境，再使用 `--source`。实验使用新生成的密钥和公开数据。[公开 GloVe 数据](https://ann-benchmarks.com/glove-100-angular.hdf5)

```bash
python3 -m venv .venv-v1
.venv-v1/bin/pip install fastapi uvicorn requests pyjwt cryptography jinja2 python-multipart h5py numpy
python3 integration/baseline_v1/source.py /tmp/hpis1-selected-source
OPENBLAS_NUM_THREADS=1 .venv-v1/bin/python integration/baseline_v1/run.py \
  --source /tmp/hpis1-selected-source --dataset /path/to/glove-100-angular.hdf5 \
  --rows 20000 --pairs 12 --warmup 2 --k 10 --out /tmp/hpis1-results
```

程序在临时目录生成全新的 RSA、X25519 密钥和用户主密钥，启动 Auth、HPIS 和 PIC 三个 HTTP 服务，使用回环地址的三个独立端口。先调用上游 `token_demo` 取得 RS256 令牌，再调用 `pre_store` 将完整数据放入一个 AID。每条记录与 HPIS 2.0 验收共用编号、字段、关键词、正文和归一化向量。向量在旧版 JSON 中由小端 float32 字节经 Base64 编码。

每次采样从调用原版 `client_pic.call_infer` 开始，到客户端解开并验证 C4 返回。PIC 在 C1 里取提示和令牌，同时把 C2 原样转发给 HPIS。HPIS 验证令牌、凭 AID 读取全量 C、解密完整上下文、把整份上下文封装为 C3，并更换 AID 和数据密钥。PIC 收到 C3 后解密并解析完整 JSON，原版模型桩只返回提示及上下文顶层键。两次预热、令牌签发、首次写入和独立的正确性核验均在采样之外。

## 实测结果

2026 年 11 月 20 日在 Ubuntu 24.04 的 AArch64 Linux 虚拟机、Python 3.12.3 下，使用公开 GloVe 前 20,000 个训练向量和前 12 个测试向量，三个服务和客户端均运行在同一台机器。字段与向量提示随机交错，每种 12 次，另作 2 次预热。下表计量两种提示发出后的全量传输与 C4 往返。原版服务的模型桩仅回显提示和上下文顶层键。

| 原版请求 | 次数 | 平均端到端时延 | P50 | P95 | 每次 HPIS /resolve 响应体 |
|---|---:|---:|---:|---:|---:|
| 带精确字段条件的提示 | 12 | 346.68 ms | 344.92 ms | 363.05 ms | 18,523,959 B |
| 带向量和 Top-10 的提示 | 12 | 367.96 ms | 350.02 ms | 455.97 ms | 18,523,959 B |

完整上下文 JSON 为 **13,892,882 B**。C3 内的二进制密文为 **13,892,898 B**，另含 12 B nonce 与 32 B 临时公钥。HTTP 响应体包含 Base64 和 JSON 封装。26 次真实 PIC 调用均有接收并解密完整 C3 的日志，客户端均成功解开 C4。字节统计取自响应体，HTTP 头和 TCP 流量未计入。

采样完成后，程序另发一次真实 `/resolve`，用本次实验的 PIC 私钥解开返回的 C3，逐字段核对 20,000 条记录。在该明文副本上独立执行 12 个字段匹配和 12 个精确 Top-10 查询，所有结果均与原始语料参考一致。这些参考结果记录于 `verification.c3_ref_check`。包含该核验请求在内，原版 HPIS 共执行 27 次 AID 和数据密钥轮换，最后用新 AID 和新数据密钥成功解开完整上下文。

1.0 采用 HTTP/JSON、逐请求公钥验证与整份数据重加密。它提供迁移前的实际搬运对照。HPIS 2.0 的 10% 性能阈值使用新检索协议下的同路径明文索引作分母。

仓库的 `results/legacy_v1/summary.json` 保存环境、统计、字节口径和核验结果，`samples.jsonl` 保存每次调用的毫秒时延及轮换计数。
