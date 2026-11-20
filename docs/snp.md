# Linux 上的 SEV-SNP 精确向量检索

`snp` 模式把 HPIS 检索进程放在 AMD SEV-SNP 虚拟机中。HPIS 在启动时生成 X25519 密钥。端侧每次建立 SDK 会话时生成 32 字节随机挑战，经 HPIC 转发到 HPIS。HPIS 请求硬件把挑战和接收公钥的 64 字节串写入签名报告。端侧使用 AMD 的信任链核对签名、撤销列表、启动测量和这两个值，成功后才用该公钥封装查询与向量密钥。正文密钥单独授予 HPIC，HPIC 通过已有 TLS 身份获得端侧信任。本接口证明的组件是 HPIS 虚拟机。[上游验证器说明](https://github.com/google/go-sev-guest/blob/v0.15.0/tools/check/README.md)

运维方需要为运行 HPIS 的完整启动链提供可信参考测量。可用包含应用的受测初始文件系统，或由受测内核验证固定根文件系统的构建方式。若平台测量范围只覆盖固件，仍需把应用镜像纳入同一条可信启动链。宿主能修改的启动后文件不能作为应用身份依据。[AMD SEV-SNP 固件 ABI](https://www.amd.com/system/files/TechDocs/56860.pdf)

## 部署和验收

在 HPIS 虚拟机安装 `attest`，在端侧设备或独立 Linux 客户端安装 `check`，让对应二进制可由进程的 `PATH` 找到。端侧需能访问 AMD KDS 获取撤销列表。收集器使用 extended 格式，保留客体提供的 VCEK 或 VLEK 证书表。[AWS 的 VLEK 证明流程](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/snp-attestation.html)

```sh
go install github.com/google/go-sev-guest/tools/attest@v0.15.0
go install github.com/google/go-sev-guest/tools/check@v0.15.0
```

准备登记材料时，将 `HPIS_EXPECTED_MEASUREMENT` 设为从可信构建或已审核的部署清单独立获得的 96 位十六进制值。以下示例选用 Milan 产品线。只有部署政策允许 SMT 时才附加 `--snp-allow-smt`。默认禁止 SMT、调试和迁移代理。可用 `--minimum-tcb` 指定该产品批准的固件安全版本下限，检查涵盖当前、已提交、报告和启动 TCB。初始化完成后，将 `hpis/`、`hpic/`、`devices/default/` 分别交付各自的角色，签发方保管 `issuer/`。

```sh
hpis init snp-demo --mode snp --dim 3 --measurement "$HPIS_EXPECTED_MEASUREMENT" --snp-product Milan
```

HPIS 和 HPIC 分别执行对应命令。示例中的 `HPIS_URL` 是 HPIC 可达的 HTTPS 地址，`HPIC_URL` 是端侧可达的 HTTPS 地址。进程间通过初始化签发的证书认证。可在一个专用测试租户内检查修改前后的排序、比对命中的正文和读取行数。

```sh
hpis serve hpis snp-demo/hpis --host 0.0.0.0 --port 8444
hpis serve hpic snp-demo/hpic --host 0.0.0.0 --port 8443 --store-url "$HPIS_URL"

cat > demo-records.jsonl <<'EOF'
{"id":1,"content":"第一条","vector":[1,0,0]}
{"id":2,"content":"第二条","vector":[0,1,0]}
EOF
hpis put snp-demo/devices/default demo-records.jsonl --url "$HPIC_URL"
hpis query snp-demo/devices/default --url "$HPIC_URL" --vector '[1,0,0]' -k 1
```

预期结果是记录 1 和 `body_rows=1`。性能验收应在同一数据、相同查询和网络拓扑上与明文精确向量检索配对测量，同时记录建立证明会话的费用和首次解开向量索引的费用。

## 已执行的验证

下表使用 Linux 上的实际 `attest/check` 程序。样本来自 VirTEE 发布的 Milan 报告，使用其公开的 VCEK、ASK 和 ARK 组装为 extended 报告。报告自身为 1,184 字节，含证书表共 5,956 字节。测试以当前系统时间访问 AMD 撤销列表。报告中固定的两段公开数据用来检验 HPIS 的绑定规则。该样本的启动测量和数据属于上游测试客体。[VirTEE 原始材料](https://github.com/virtee/sev/tree/feaf142ec916c23e7658f088802921177ee290f9/tests/certs_data)

| 检查 | 结果 |
| --- | --- |
| 签名、启动测量、64 字节数据和允许 SMT 的 Milan 策略相符 | `check` 返回 0 |
| 替换期望挑战、接收公钥或启动测量，分别测试 | 三次均返回策略错误 5 |
| 签名改变一位 | 返回签名错误 2 |
| 要求关闭 SMT，遇到开启 SMT 的报告 | 返回策略错误 5 |
| 普通 Linux 虚拟机尝试收集新报告 | `attest` 找不到 SNP 设备，SDK 经 HPIC 收到 HTTP 403，终止会话 |

项目包含[公开 extended 报告](../results/snp/virtee-milan-extended.bin)和[逐项实测记录](../results/snp/validation.json)。在没有 SNP 设备的 Linux 上，使用以下命令可重跑整组测试。在已有 SNP 硬件的机器上，可以省略 `HPIS_TEST_NO_SNP=1` 来单独回放公开样本。

```sh
HPIS_SNP_MILAN_REPORT=results/snp/virtee-milan-extended.bin HPIS_TEST_NO_SNP=1 \
    python -m unittest discover -s tests -p test_snp_integration.py -v
```

本次 Linux 验收提供了软件检索实测和签名报告校验实测。真正运行本项目镜像的 SNP 虚拟机还需完成一次新鲜挑战、端到端数据查询和同机明密文时延对照。
