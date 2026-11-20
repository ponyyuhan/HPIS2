# 在 H200 上提供演示问答模型

Demo 在演示电脑上运行，H200 只提供兼容 `POST /v1/chat/completions` 的文本问答服务。用户只指定了模型家族和 Qwen 参数规模，以下选用 Qwen3.6-27B 和 Gemma 4 31B 指令版。已有权重可直接挂载。

| Demo 配置 | 默认权重 | API 中的模型名 | H200 主机端口 |
|---|---|---|---:|
| Qwen 27B | `Qwen/Qwen3.6-27B` | `hpis-qwen-27b` | 18081 |
| Gemma 4 | `google/gemma-4-31B-it` | `hpis-gemma-4` | 18082 |

两者的模型配置均支持 262,144 token。演示采用 **单 H200、BF16、16,384 token 上下文、至多 4 个并发序列**，留出空间处理两条路径的短问答。16,384 是本项目的启动配置。实际可用 KV cache 和首 token 延迟以目标机器启动日志和实测为准。[Qwen 模型卡](https://huggingface.co/Qwen/Qwen3.6-27B) · [Gemma 模型卡](https://huggingface.co/google/gemma-4-31B-it) · [vLLM Qwen H200 配置](https://recipes.vllm.ai/Qwen/Qwen3.6-27B) · [vLLM Gemma H200 配置](https://recipes.vllm.ai/Google/gemma-4-31B-it)

## 1. 在自己的 H200 主机启动

在有 Docker 和 NVIDIA Container Toolkit 的 Linux GPU 主机运行。将本目录复制到主机，进入目录，任选一个命令。第一次使用公开权重会下载到 Hugging Face 缓存。脚本使用官方 `vllm/vllm-openai:v0.32.0-cu129` 镜像。可用 `HPIS_VLLM_IMAGE` 指向该主机驱动支持的兼容镜像。[官方镜像标签](https://hub.docker.com/r/vllm/vllm-openai/tags?name=v0.32.0-cu129) · [vLLM 服务参数](https://docs.vllm.ai/en/v0.32.0/cli/serve/)

```bash
./serve.sh qwen
```

```bash
./serve.sh gemma
```

已有权重时，可直接指定主机上的绝对目录。只有一张卡时可在前台按 Ctrl+C 停止当前模型后切换。若两张卡都可用，在两个终端各启一项即可。

```bash
HPIS_GPU=0 HPIS_MODEL_DIR=/models/Qwen3.6-27B ./serve.sh qwen
HPIS_GPU=1 HPIS_MODEL_DIR=/models/gemma-4-31B-it ./serve.sh gemma
```

如果 GPU 作业内已装兼容的 vLLM，使用 `./serve.sh qwen --native` 或 `./serve.sh gemma --native`。原生启动尊重分配给作业的 `CUDA_VISIBLE_DEVICES`。若要查看将执行的参数，可加 `--print`。若要调整上下文，设置 `HPIS_MAX_MODEL_LEN`。两个默认端口都只监听主机回环地址。需要为共享主机设置 API 鉴权时，在启动前从自己的凭据来源导出 `VLLM_API_KEY`，并在演示电脑设置同值的 `HPIS_SHOWCASE_QWEN_API_KEY` 或 `HPIS_SHOWCASE_GEMMA_API_KEY`。

## 2. 在演示电脑连接和启用模型

将下例的 `my-h200` 换成**自己的 H200 登录别名**。该命令只在本机公开回环端口。使用一个模型时，保留它对应的 `-L` 即可。已经有固定 HTTPS 推理地址时，可直接将下面的 BASE_URL 改为该地址。

```bash
ssh -NT -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18081:127.0.0.1:18081 \
  -L 127.0.0.1:18082:127.0.0.1:18082 my-h200
```

在项目根目录的新终端设置所需模型的参数。已有其他兼容服务时，用其 `GET /v1/models` 返回的模型名替换下列别名。

```bash
export HPIS_SHOWCASE_QWEN_BASE_URL=http://127.0.0.1:18081/v1
export HPIS_SHOWCASE_QWEN_MODEL=hpis-qwen-27b
export HPIS_SHOWCASE_GEMMA_BASE_URL=http://127.0.0.1:18082/v1
export HPIS_SHOWCASE_GEMMA_MODEL=hpis-gemma-4
```

等已启动的服务加载完权重后，从演示电脑先验证一次。可在命令末尾仅保留 `qwen` 或 `gemma`。出现 `verified_model_answer: true` 表示 Demo 的真实模型适配器已核对本次回答的原文、附件和来源 ID。

```bash
.venv/bin/python -m deployment.h200.smoke qwen gemma
.venv/bin/python -m showcase --port 8770
```

打开 [模型演示工作台](http://127.0.0.1:8770/)，在对比口径中选用该模型并重新提问。程序启动时读取模型端点配置。未配置端点时，工作台仍可用本地证据抽取运行。模型收到的是当次检索命中的合成邮件与附件，原文和来源核验失败时界面标注回退。

### 整套工作台与模型同机运行

如果 H200 主机也承载演示后台，请将完整项目放到该机，在项目根目录安装 Python 3.10 或更高版本的依赖，并在同一终端设置上节的回环 BASE_URL 和模型别名。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[bench]'
.venv/bin/python -m showcase --port 8770
```

在演示电脑上只需转发 Web 端口，即可用浏览器打开工作台。

```bash
ssh -NT -o ExitOnForwardFailure=yes -L 127.0.0.1:8770:127.0.0.1:8770 my-h200
```

## 已实测的接口

2026-11-20，使用演示应用自己的 `model_answer` 发送一封合成邮件及其 CSV 附件给 OpenRouter，以“星澜项目最终的上线时间是什么？”做功能验证。两种模型最终均给出邮件 2 的《试点上线排期.csv》中关于 2026 年 12 月 15 日 20:00（北京时间）的完整引文。下表是各自单次成功调用的往返观测。

| OpenRouter 模型 | 输入 / 输出 token | 该次往返 | 来源校验 |
|---|---:|---:|---|
| `qwen/qwen3.6-27b` | 315 / 83 | 2.852 s | 通过 |
| `google/gemma-4-31b-it` | 337 / 55 | 1.462 s | 通过 |

Qwen 首次按网关默认推理方式未完整生成，应用如实回退到本地摘录。为短引文任务关闭 Qwen 的网关推理后，同题复核通过。H200 脚本在服务端使用模型自带聊天模板并设置 `enable_thinking=false`。保存的结果含失败和成功两轮。该测试验证远端接口和引用，H200 上的模型加载、GPU 占用、生成速度仍需在目标硬件测量。[首轮调用](openrouter-smoke.json) · [Qwen 复核](openrouter-qwen-retest.json) · [OpenRouter 推理参数](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)

若要重跑同样的云端验证，可在本地程序环境提供 `OPENROUTER_API_KEY` 并显式选择 `--openrouter`，每个列出的模型各发一次请求。查询、来源和计量会写入所选结果文件。

```bash
.venv/bin/python -m deployment.h200.smoke qwen gemma --openrouter --output /tmp/hpis-model-smoke.json
```
