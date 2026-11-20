# 复用 HPIS 1.0 与原版 HPIC

这一入口让原版 `pic_server.py` 使用 HPIS 2.0 检索出的上下文。Linux 联调运行了原版 OAuth、HPIC、HPIS 和客户端。每次仅把七个 Python 源文件复制到临时目录，再用原版密钥生成器创建本次测试的密钥。原版基于 RS256 的匿名访问令牌和 HPIS 2.0 的匿名票据、AID 所有权证明均参与新请求。[原版 HPIC](https://github.com/ponyyuhan/mcp_agent/blob/f1207dfa8c3909293e217e2961934c085869263a/pic_server.py) · [原版 HPIS](https://github.com/ponyyuhan/mcp_agent/blob/f1207dfa8c3909293e217e2961934c085869263a/hpis_server.py) · [原版客户端](https://github.com/ponyyuhan/mcp_agent/blob/f1207dfa8c3909293e217e2961934c085869263a/client_pic.py)

## 请求如何走

1. 旧客户端继续发送原格式 C1/C2。HPIC 验证 C1 中的 AT，把 C2 交给适配器。适配器把普通 C2 转交给原版 HPIS。原版 HPIS 读取该 AID 的完整上下文，生成 C3，并按其协议轮换 AID 与 K_Data。
2. 新客户端 `InferenceDevice` 复用 `Device.terms()` 和 `Device.vector()` 生成查询。C1 仍使用原版 X25519、HKDF 和 AES-GCM。扩展的 C2 使用 `hpis2-search/1` 标记，包含普通 HPIS 2.0 已签名请求包，以适配器公钥加密。C2 的 AEAD 关联数据包含完整 C1。
3. 适配器位于受信 HPIC 一侧。原版 HPIC 验证 AT 成功后回调 `/resolve`。这次回调使用适配器和原版 HPIC 之间的一次性句柄。适配器经验证证书的 TLS 连接把授权包转交 HPIS 2.0 的 HPIC 服务。HPIS 2.0 存储端收到自己的公钥密封请求，验证 AID 所有权，读取命中的加密正文。
4. 受信 HPIC 取出命中内容。适配器按原版 `PIC-C3` 格式生成 C3，其中 `ContextData.records` 包含命中的 ID 和正文。原版 HPIC 解开 C3，用 C1 会话密钥生成 C4。原版示例模型回显提示词和上下文键名，业务模型可在该处读取 `ContextData.records`。Linux 验证还使用临时 HPIC 密钥独立打开了同次请求的 C3，逐条核对实际正文。

扩展 C2 的接收者、适配器和两个 HPIC 进程属于同一授权计算域。数据 DEK 随授权包进入该域。HPIS 2.0 存储端仅持有自己的请求解封密钥和相应索引。原版 C1/C3/C4 的算法、序列化和 JWT 验证直接使用上游实现。[适配器实现](bridge.py) · [SDK 入口](client.py)

## Linux 实测

使用 20,000 条 100 维 GloVe 记录。128 个测试查询各重复三轮，每个场景得到 384 对明文和密文时延。每次查询实际通过原版 HPIC 处理。百分比按两组均值之比减一计算。

| 查询 | 明文均值 | 密文均值 | 均值开销 | P95 开销 | 结果 | 每次读取正文 |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| 字段精确匹配 | 2.664 ms | 2.699 ms | +1.33% | +1.72% | 384/384 完全一致 | 1 条 |
| 向量 Top-10，β=0.25 | 3.239 ms | 3.259 ms | +0.63% | −0.38% | Recall@10 为 88.20% | 10 条 |

按查询编号对三轮结果整体重采样 2,000 次，均值开销的 95% 百分位区间分别为 [−0.21%, +2.93%] 和 [−1.04%, +2.44%]。每份 C4 均由设备成功解开，分别核对同次 C3 的结果 ID 和正文。C3 的独立校验在计时结束后执行。[运行环境和统计](results/linux/summary.json) · [1,536 条原始测量](results/linux/samples.jsonl.gz)

## 复现实测

从项目根目录运行。将 `LEGACY` 设为包含 `pic_server.py`、`hpis_server.py`、`client_pic.py`、`crypto_primitives.py`、`oauth_server.py`、`scripts/gen_keys.py` 的原版 checkout。本文的 Git 版本还使用 `demo_logging.py`。命令会启动临时进程，完成后回收。

```sh
python -m pip install -e '.[bench]' fastapi uvicorn PyJWT requests jinja2 python-multipart
LEGACY=/path/to/mcp_agent
python -m integration.legacy.smoke --legacy-source "$LEGACY" --out /tmp/legacy-functional
python -m integration.legacy.smoke --legacy-source "$LEGACY" \
  --dataset /path/to/glove-100-angular.hdf5 --dim 100 --rows 20000 \
  --queries 128 --rounds 3 --k 10 --beta 0.25 --out /tmp/legacy-paired
```

配对实验在同一临时服务中登记明文对照账户和密文账户。两侧分别导入相同记录，使用同一原版 OAuth、原版 HPIC、适配器、HPIS 2.0 计算与存储进程。每轮打乱查询和先后顺序，从 SDK 接收目标字段或原始向量开始计时，直到设备完成 C4 解密。两端索引预热，令牌签发、建库、事后打开 C3 和明文穷举校验的耗时另计。SDK 和适配器在同一个 Linux 进程，仍通过回环 HTTP 通信。单次原版 HPIC 推理按原代码从 OAuth 读取公钥。公开 GloVe 用于相似度检索，字段、正文和密钥由本次实验生成。[公开向量数据](https://ann-benchmarks.com/glove-100-angular.hdf5)

## 接入已有部署

下例的 HTTP 地址用于受信计算主机内的回环调用。给新 SDK 分发适配器公钥和已登记的设备配置。原版 HPIC 的 `HPIS_URL` 指向适配器。适配器分别知道原版 HPIS 和 HPIS 2.0 HPIC 的地址。

```sh
python -m integration.legacy.bridge keygen /path/to/new-adapter-keys
python -m integration.legacy.bridge serve --legacy-source "$LEGACY" \
  --private /path/to/new-adapter-keys/private.bin \
  --pic-public "$LEGACY/secrets/pic_x25519_pub.bin" \
  --pic-url http://127.0.0.1:8002 --legacy-store-url http://127.0.0.1:8010 \
  --search-url https://127.0.0.1:8443 --search-ca /path/to/device/ca.pem --port 8091
```

在原版目录启动原服务，设置 HPIC 进程环境变量 `HPIS_URL=http://127.0.0.1:8091` 与其原有 `OAUTH_URL`。SDK 的使用方式如下。

```python
from pathlib import Path
from integration.legacy.bridge import primitives
from integration.legacy.client import InferenceDevice


async def query(device_folder, old_checkout, bridge_pub_path, token):
    old_checkout = Path(old_checkout)
    async with InferenceDevice(
        device_folder,
        "https://127.0.0.1:8443",
        bridge_url="http://127.0.0.1:8091",
        crypto=primitives(old_checkout),
        pic_public=(old_checkout / "secrets/pic_x25519_pub.bin").read_bytes(),
        bridge_public=Path(bridge_pub_path).read_bytes(),
        token=token,
        prompt="查找此次请求的上下文",
    ) as client:
        return await client.terms([["field", "ref", "item-1"]])
```

## 迁移与边界

`import_context()` 在终端用原版 `anonym_id(MK_user, counter_id)` 定位已有 AID，用 `k_data(MK_user, counter_k)` 解开原版 AES-GCM 对象，再通过普通 `Device.put()` 分批建立新索引。输入文件以只读方式打开。默认处理 `ContextData.records`。其他应用结构可传 `records=callable` 映射到包含 `id`、`content`、可选 `fields`、`keywords`、`vector` 的记录。稳定 ID 允许重试写入。导入后数据属于目标设备登记的 HPIS 2.0 AID。旧协议的读后轮换仍发生在旧 `/resolve` 路径上。[导入函数](import_context.py)

Linux 结果验证了归档的 C1–C4 Python 原型，覆盖 JWT 拒绝、C1/C2 错配、其他租户、错误所有者、直接回调、有效期内重复请求。当前 HPIS 2.0 运行中的重放窗口随服务进程一同重启。向量快速模式使用有扰动的几何表示，需同时查看延迟与 Recall@10。该联调环境使用回环网络和测试 RSA/X25519 密钥。商业 HPIC、鸿蒙真机及硬件 TEE 的验收需要相应部署环境。
