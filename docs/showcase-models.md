# 演示工作台的问答引擎

工作台默认使用本地证据抽取。它从实际命中的邮件与附件整理答案。Qwen 27B 和 Gemma 4 是另外两个可选引擎，各自使用管理员配置的 Chat Completions 接口。面向观众的浏览器只获得引擎名称、可选状态和本轮实际使用的引擎。

## 配置已部署的模型

在启动工作台的进程环境中设置对应变量。两个模型可指向同一个兼容服务，也可使用不同地址。模型名填写服务实际暴露的标识。

| 选项 | 服务根地址 | 服务模型名 | 可选 Bearer 密钥 |
|---|---|---|---|
| Qwen 27B | `HPIS_SHOWCASE_QWEN_BASE_URL` | `HPIS_SHOWCASE_QWEN_MODEL` | `HPIS_SHOWCASE_QWEN_API_KEY` |
| Gemma 4 | `HPIS_SHOWCASE_GEMMA_BASE_URL` | `HPIS_SHOWCASE_GEMMA_MODEL` | `HPIS_SHOWCASE_GEMMA_API_KEY` |

根地址可带 `/v1`，也可填写完整 `/v1/chat/completions` 地址。启动工作台后在页面切换引擎。程序也接受 `POST /api/run` 的 JSON 参数 `{"question":"星澜项目何时上线？","model":"qwen"}`。另外两个选项是 `"gemma"` 和 `"extractive"`。两个对照流程分别调用同一模型，均使用本次请求各自取回的证据。用户数据一侧只有命中的邮件正文及附件随问题送到该端点，云端部署时需将推理服务视为获授权阅读这些证据的一方。

## 回答与引用

适配器采用非流式 JSON 回答，温度为 0。模型读取问题、所需栏目和已检索的原文，自行选出对应的逐字引文。每条引文绑定邮件编号和附件名或正文。展示前会核对引文确实存在，以及确认事项是否得到完整覆盖。例如，合同付款安排的三次付款均须有原文支持。页面显示来自原文的文字，避免将模型补写的金额、日期或条件当作记录事实。[vLLM 结构化输出](https://docs.vllm.ai/en/stable/features/structured_outputs/)

`status=verified` 表示本路使用了模型回答并通过引文核对。超时、HTTP 错误、响应不完整或引文核对失败时，本路返回本地证据抽取的答案，并附相应状态及原因。返回元数据包括本次 API 应用请求体和响应体的字节数、调用用时，以及服务提供的 token 用量。模型 API 字节与 HPIC–HPIS 检索消息分别计数。若模型因长度上限停止，元数据保留 finishReason 和服务返回的用量，便于判断是否消耗在推理 tokens 上。仅当用户明确把 Qwen 端点设为 OpenRouter 时，短引文请求会指定 `reasoning.effort="none"`。自建兼容服务可在服务端配置聊天模板。[OpenRouter 推理参数](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)

## 在本机验证接口

```bash
.venv/bin/python -m unittest discover -s tests -p test_showcase_model.py -v
```

测试使用回环 HTTP fixture。它检查两套独立端点和 served model 路由、可选 Bearer 凭据、无配置时的本地回答、应用字节计数，以及伪造金额、错引附件和上游故障时的显式回退。这项测试验证协议行为。用户 H200 上的实际模型名称、可用性、回答质量和推理时延须在其服务部署后测量。
