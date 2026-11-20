# 在 H200 上部署演示问答模型（Qwen 27B / Gemma 4）

演示程序通过 OpenAI 兼容接口 `/v1/chat/completions` 流式获取回答，代码在 `demo/llm.py`。没有配置 H200 时默认走 OpenRouter。H200 上用 vLLM 提供同样的接口，演示电脑经 SSH 隧道访问。实测结果见 [VERIFIED.md](VERIFIED.md)。

| 演示档位 | 默认权重 | 许可与下载 | BF16 权重 | API 模型名 | H200 端口 |
|---|---|---|---:|---|---:|
| `qwen` | [`Qwen/Qwen3.6-27B`](https://huggingface.co/Qwen/Qwen3.6-27B) | Apache-2.0，公开，无需 token | 55.6 GB | `hpis-qwen-27b` | 18081 |
| `gemma` | [`google/gemma-4-31B-it`](https://huggingface.co/google/gemma-4-31B-it) | Apache-2.0，公开，无需 token，也无需在网页上接受许可 | 62.6 GB | `hpis-gemma-4` | 18082 |

Qwen 27B 目前最新的是 [`Qwen/Qwen3.8-27B`](https://huggingface.co/Qwen/Qwen3.8-27B)（2026-08-05 发布）。它与 3.6 的架构、尺寸和 vLLM 参数完全相同，在我们的测试中表现更好。改用它只需在 H200 上设置 `HPIS_MODEL_REPO=Qwen/Qwen3.8-27B`，并在演示电脑设置 `HPIS_LLM_QWEN_LABEL=Qwen3.8-27B`。如果仍用 OpenRouter，则设置 `HPIS_LLM_QWEN_MODEL=qwen/qwen3.8-27b`。两个模型都带视觉编码器，本演示只用文本。

## 1. 在 H200 上启动

需要 Linux、NVIDIA 驱动，以及 Docker 和 NVIDIA Container Toolkit（也可以用已经装好的 vLLM）。把本目录复制到 H200 上，用到的只有 `serve.sh` 和 `smoke.py`。

```bash
./serve.sh qwen            # 前台运行，Ctrl+C 停止
./serve.sh gemma --detach  # 后台容器 hpis-gemma；docker logs -f hpis-gemma 看日志
./serve.sh qwen --print    # 只打印将要执行的命令
```

第一次运行会拉取约 10 GB 的镜像，并把权重下载到 `~/.cache/huggingface`。日志出现 `Application startup complete` 后，`curl -s 127.0.0.1:18081/v1/models` 应能列出 `hpis-qwen-27b`。

脚本按驱动版本自动选择镜像。`nvidia-smi` 显示的驱动 ≥ 580 时用 `vllm/vllm-openai:v0.34.0`，这是 2026-11-16 发布的最新稳定版，基于 CUDA 13.0。驱动低于 580 时用 `vllm/vllm-openai:v0.32.0-cu129`，这是基于 CUDA 12.9 的最新版本。CUDA 12.x 镜像借助次版本兼容，在 525 及以上的驱动上都能运行。也可以用 `HPIS_VLLM_IMAGE=...` 指定镜像。

已经装有 vLLM 时（虚拟环境、conda 或 Slurm 作业），运行 `./serve.sh qwen --native`。脚本会先检查版本：Qwen 需要 vLLM ≥ 0.17.0，Gemma 4 需要 ≥ 0.19.1。若权重已经下载到本地，用 `HPIS_MODEL_DIR=/data/models/Qwen3.6-27B ./serve.sh qwen`，路径须为绝对路径，容器内以只读方式挂载。

| vLLM 参数 | 取值 | 作用 |
|---|---|---|
| `--dtype` | `bfloat16` | 保持原始精度 |
| `--max-model-len` | 32768 | 演示提示词不超过约 6k token，留有充足余量；可用 `HPIS_MAX_MODEL_LEN` 调整 |
| `--max-num-seqs` | 8 | 演示并发很低；用 `HPIS_MAX_NUM_SEQS` 调整 |
| `--gpu-memory-utilization` | 0.90 | 单卡独占时的稳妥值；用 `HPIS_GPU_MEM_UTIL` 调整 |
| `--enable-prefix-caching` | 开启 | 每次请求的系统提示词相同，可复用其 KV cache |
| `--language-model-only` | 开启 | 不加载视觉编码器，也不为图像输入预留显存 |
| `--reasoning-parser` | `qwen3` / `gemma4` | 万一模型输出思考内容，将其放进 `reasoning_content`，不混入回答 |
| `--default-chat-template-kwargs` | `{"enable_thinking": false}` | 服务端默认关闭思考；演示客户端每次请求也会显式关闭 |
| `--served-model-name` | `hpis-qwen-27b` / `hpis-gemma-4` | 演示端使用的固定别名，与权重版本无关 |
| 监听地址 | 127.0.0.1 | 只能经 SSH 隧道访问；用 `HPIS_HOST` 修改 |

如需 API 鉴权，先在 H200 上 `export VLLM_API_KEY=<随机串>` 再启动，脚本会把它传进容器。演示电脑上设置同样的值到 `HPIS_LLM_QWEN_API_KEY` 和 `HPIS_LLM_GEMMA_API_KEY`。

### 一张 H200 同时运行两个模型（估算，未在 H200 上实测）

两个模型的文本权重合计约 108 GiB，H200 可用显存约 140 GiB。按下面的比例分配后可以同时运行，Qwen 启动完成后再启动 Gemma：

```bash
HPIS_GPU_MEM_UTIL=0.44 HPIS_MAX_NUM_SEQS=4 ./serve.sh qwen --detach
HPIS_GPU_MEM_UTIL=0.50 HPIS_MAX_NUM_SEQS=4 ./serve.sh gemma --detach
```

这样每个模型约有 8–10 GiB 可用于 KV cache。Qwen 只有 16 层全注意力，每 token 约 64 KiB。Gemma 的全局层每 token 约 40–80 KiB，滑窗层每个请求固定占用约 0.8 GiB。这足够支撑演示所需的一两个并发。如果启动时报显存不足，可以降低 `HPIS_MAX_MODEL_LEN`，或者换用公开的 FP8 权重：`HPIS_MODEL_REPO=Qwen/Qwen3.6-27B-FP8` 或 `HPIS_MODEL_REPO=RedHatAI/gemma-4-31B-it-FP8-dynamic`。

## 2. 演示电脑连接 H200

把 `my-h200` 换成自己的 SSH 主机别名。只部署了一个模型时，保留对应的那条 `-L` 即可。

```bash
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 18081:127.0.0.1:18081 -L 18082:127.0.0.1:18082 my-h200
```

在启动演示的终端里设置：

```bash
export HPIS_LLM_BACKEND=h200   # qwen → 127.0.0.1:18081 / hpis-qwen-27b；gemma → 127.0.0.1:18082 / hpis-gemma-4
# 仅当 H200 设置了 VLLM_API_KEY 时需要：
export HPIS_LLM_QWEN_API_KEY=... HPIS_LLM_GEMMA_API_KEY=...
```

也可以逐项指定，这样能让一个模型走 H200、另一个走 OpenRouter：

```bash
export HPIS_LLM_QWEN_BASE_URL=http://127.0.0.1:18081/v1 HPIS_LLM_QWEN_MODEL=hpis-qwen-27b
export HPIS_LLM_GEMMA_BASE_URL=http://127.0.0.1:18082/v1 HPIS_LLM_GEMMA_MODEL=hpis-gemma-4
```

如果不设置任何 `HPIS_LLM_*` 变量，演示走 OpenRouter，需要设置 `OPENROUTER_API_KEY`。注意 `HPIS_LLM_*_MODEL` 对当前生效的后端都起作用，切换到 H200 前要先清掉为 OpenRouter 设置的模型 id。其他可选变量：`HPIS_LLM_*_EXTRA_BODY` 是合并进每次请求的 JSON，值写 `null` 表示删除该字段；`HPIS_LLM_*_LABEL` 用于界面显示名称。

## 3. 验证

```bash
python3 smoke.py qwen              # H200 本机或经隧道运行均可，只依赖标准库
.venv-demo/bin/python -m demo.llm --probe -p qwen
HPIS_LLM_BACKEND=h200 .venv-demo/bin/python -m demo.deploy.h200.bench qwen gemma --runs 3
```

`smoke.py` 会列出服务的模型和 `max_model_len`，然后流式提问三次。最后一次不在请求里关闭思考，以此检查服务端默认值是否生效。每次输出首字延迟和解码速度。只有回答正确、引用了 [2] 且没有思考内容时才记为 PASS。`bench.py` 用演示程序自己的客户端和提示词跑 `fixtures.json` 中的 4 个中文问题，并逐条判分。

H200 上的速度尚未实测，下面是估算。单请求解码受显存带宽限制，4.8 TB/s 除以权重大小，可得 Qwen 的上限约 88 token/s，Gemma 约 78 token/s，实际预计分别为 60–75 和 50–65 token/s。1–2k token 的提示词预填约需 0.1–0.2 s。一段约 150 token 的回答总耗时约 2–3 s。OpenRouter 上的实测数字取决于各服务商的硬件，与 H200 不可直接比较。

## 4. 故障排查

| 现象 | 处理 |
|---|---|
| `could not select device driver "" with capabilities: [[gpu]]` | 安装 NVIDIA Container Toolkit，然后 `sudo systemctl restart docker` |
| `CUDA driver version is insufficient` | 驱动低于 580。设置 `HPIS_VLLM_IMAGE=vllm/vllm-openai:v0.32.0-cu129` 或升级驱动 |
| 拉镜像或下载权重很慢、失败（中国大陆网络） | 镜像可配置 Docker Hub 加速。权重可用 `HF_ENDPOINT=https://hf-mirror.com ./serve.sh qwen`；或先运行 `modelscope download --model Qwen/Qwen3.6-27B --local_dir /data/models/Qwen3.6-27B`，再设置 `HPIS_MODEL_DIR`。ModelScope 上两个模型的仓库名与 Hugging Face 相同 |
| 启动时 OOM，或报 `No available memory for the cache blocks` | 用 `nvidia-smi` 查看是否有其他进程；降低 `HPIS_MAX_MODEL_LEN`、`HPIS_MAX_NUM_SEQS` 或 `HPIS_GPU_MEM_UTIL` |
| 演示报 `HTTP 404 … model … does not exist` | 演示端的模型名必须等于 `--served-model-name`，可用 `curl 127.0.0.1:18081/v1/models` 核对 |
| 演示报 `HTTP 401` | H200 上的 `VLLM_API_KEY` 与演示端的 `HPIS_LLM_*_API_KEY` 不一致 |
| `Connection refused`（127.0.0.1:18081） | 隧道未建立或服务尚未就绪。SSH 报 `Address already in use` 说明本机端口已被占用，用 `lsof -i :18081` 查看 |
| 回答前出现思考内容，或回答为空 | 确认 vLLM 带着本脚本的 `--reasoning-parser` 启动，`smoke.py` 输出的 `reasoning_chars` 应为 0。演示客户端也会隐藏开头的 `<think>` 块 |
| 第一次提问明显较慢 | 服务启动后先跑一次 `smoke.py` 预热 |
| 演示过程中隧道断开 | 保留 `ServerAliveInterval=30`，或改用 `autossh -M 0` 建立隧道 |

## 5. 隐私

- vLLM 默认不在日志里记录请求正文，不要加 `--enable-log-requests`。
- `serve.sh` 设置了 `VLLM_NO_USAGE_STATS=1` 和 `DO_NOT_TRACK=1`，关闭 vLLM 的使用统计上报。
- 服务默认只监听 127.0.0.1。

## 6. 相对旧版 `deployment/h200/` 的修正

- 镜像不再固定为 `v0.32.0-cu129`，改为按驱动自动选择，默认用最新稳定版 v0.34.0。
- 上下文从 16k 提高到 32k，并发从 4 提高到 8，并显式设置显存比例和 prefix caching。
- 演示端变量从旧 showcase 的 `HPIS_SHOWCASE_*` 改为 `HPIS_LLM_*`。新的 `smoke.py` 不依赖 showcase 包，只用标准库。
- 旧版使用的 `--language-model-only`、`--reasoning-parser qwen3/gemma4`、`--default-chat-template-kwargs` 是正确的，已在 vLLM 源码中核对后保留。
- 新增 `--detach`、`--native` 模式下的版本检查，以及关闭使用统计。
- 补充说明：Gemma 4 采用 Apache-2.0 许可，不需要 HF token。

## 来源（2026-11-20 核对）

- 模型元数据（许可、是否 gated、参数量）：Hugging Face API [Qwen3.6-27B](https://huggingface.co/api/models/Qwen/Qwen3.6-27B)、[Qwen3.8-27B](https://huggingface.co/api/models/Qwen/Qwen3.8-27B)、[gemma-4-31B-it](https://huggingface.co/api/models/google/gemma-4-31B-it)；未登录下载 `config.json` 均返回 200
- 思考开关：[Qwen3.8-27B 模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)（默认开启思考，`chat_template_kwargs.enable_thinking=false` 关闭）；[Gemma 4 模型卡](https://huggingface.co/google/gemma-4-31B-it)（系统提示中的 `<|think|>` 开启思考，默认关闭）；两者的 `chat_template.jinja`
- vLLM 版本：[GitHub releases](https://github.com/vllm-project/vllm/releases)（v0.34.0，2026-11-16）；[Docker Hub 标签](https://hub.docker.com/r/vllm/vllm-openai/tags)（从镜像配置读到 v0.34.0 为 `CUDA_VERSION=13.0.2`，v0.32.0-cu129 为 12.9.1）
- 参数核对（v0.34.0 源码，并与 v0.32.0、v0.17.0、v0.19.1 比对）：[`engine/arg_utils.py`](https://github.com/vllm-project/vllm/blob/v0.34.0/vllm/engine/arg_utils.py)、[`entrypoints/common/cli_args.py`](https://github.com/vllm-project/vllm/blob/v0.34.0/vllm/entrypoints/common/cli_args.py)、[`reasoning/__init__.py`](https://github.com/vllm-project/vllm/blob/v0.34.0/vllm/reasoning/__init__.py)、[`config/cache.py`](https://github.com/vllm-project/vllm/blob/v0.34.0/vllm/config/cache.py)、[`parser/qwen3.py`](https://github.com/vllm-project/vllm/blob/v0.34.0/vllm/parser/qwen3.py)、[`parser/gemma4.py`](https://github.com/vllm-project/vllm/blob/v0.34.0/vllm/parser/gemma4.py)
- vLLM 官方部署配方：[Qwen3.8-27B](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)、[gemma-4-31B-it](https://recipes.vllm.ai/Google/gemma-4-31B-it)
- 驱动要求：[CUDA Toolkit release notes](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html)（CUDA 13.x 需驱动 ≥ 580；12.9 U1 需 ≥ 575.57.08；12.x 次版本兼容需 ≥ 525）
- ModelScope：[Qwen/Qwen3.6-27B](https://modelscope.cn/models/Qwen/Qwen3.6-27B)、[google/gemma-4-31B-it](https://modelscope.cn/models/google/gemma-4-31B-it)
- OpenRouter：[reasoning 参数](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)、[流式格式](https://openrouter.ai/docs/api/reference/streaming)、[模型列表 API](https://openrouter.ai/api/v1/models)
