# Instance-2 / SubAID 接入

已有 HPIS 2.0 租户可以用历史 Instance-2 凭据兑换一张最长五分钟的 HPIC 票据。兑换使用归档真实签发的 RSA 盲签名、Ed25519 属性签名和服务 SubAID。每次查询仍由当前设备的 owner 密钥签名。旧凭据仅交给受信票据签发方，HPIC 和 HPIS 的查询接口接收短期票据或其绑定信息。

## 来源与授权

联调读取 `ponyyuhan/aid` 的提交 `31147dfa3e9b718f2906a0836e7d326ae5e51243`。客户端通过 `/anon/init` 和 `/anon/request` 完成盲化、签发和去盲，SubAID 按服务和更新代号派生。测试将五个 Python 源文件复制到本地临时目录，让原版签发服务生成新的密钥。 [签发客户端](https://github.com/ponyyuhan/aid/blob/31147dfa3e9b718f2906a0836e7d326ae5e51243/mcp_agent/hpic_rag/anon_token.py#L60) · [签发服务](https://github.com/ponyyuhan/aid/blob/31147dfa3e9b718f2906a0836e7d326ae5e51243/mcp_agent/hpic_rag/oauth_auth_server.py#L304) · [SubAID 派生](https://github.com/ponyyuhan/aid/blob/31147dfa3e9b718f2906a0836e7d326ae5e51243/mcp_agent/hpic_rag/aid_subaid.py#L28)

历史服务对调用方提交的属性签名，六位字段只检查格式。旧 bundle 含演示使用的用户名、密码与六位字段，应用真实账户应使用经授权的独立身份服务。接入侧使用管理方预先信任的两把公钥，复用归档的属性验证、用户字段检查和 RSA 消息编码。SubAID 与本地 AID 的对应关系由管理员事先登记，设备须对具体上游凭据、服务、更新代号、短期持钥公钥和到期时间签名。原客户端每次调用会生成新的主密钥，因此重新签发得到的新 SubAID 需要重新登记。 [原版验证器](https://github.com/ponyyuhan/aid/blob/31147dfa3e9b718f2906a0836e7d326ae5e51243/mcp_agent/hpic_rag/anon_verify.py#L96)

既有 HPIC 票据持钥签名与 HPIS 的租户所有权签名独立验证。两名已登记用户若把一张票据与另一名用户签好的查询拼接，两处签名单独检查都可能通过。兑换票据的签名覆盖短期持钥公钥和 `SHA256(msgpack(["hpis-owner-v1", holder, aid, owner]))`。HPIC 通过现有双向 TLS 转发这两个值，HPIS 解封后用本地租户重新计算并核对，再验证该租户的 owner 签名。每次兑换生成独立的短期密钥。HPIC 可以关联同一张票据有效期内的请求。

## 在 Linux 重跑

从项目根目录执行。下例的 `ARCHIVE` 是可信只读归档内包含 `anon_verify.py` 的目录。安装组件和启动一次本机端到端联调可执行下列命令。

```bash
python -m pip install -e . fastapi uvicorn requests
ARCHIVE='/Volumes/ssd/aid/mcp_agent/hpic_rag'
python -m integration.instance2.smoke "$ARCHIVE"
```

联调让两名用户分别取得原版签发令牌，通过 TLS 写入相同记录号、不同正文，各自执行密文关键词查询和向量精排。随后验证错误凭据、换绑与重放的拒绝行为。此项测试覆盖功能和授权。项目性能结果另见主验收报告。 [Linux 实测记录](linux_smoke.json)

## 使用已有设备

管理端登记当前 HPIS 租户的 AID（十六进制）和来自已授权登记流程的 SubAID（base64url）。`BINDING`、新票据签发私钥目录 `ISSUER_DIR`、旧签发方公钥 `RSA_PEM` 和 `MAC_PEM` 由签发方控制。经办人需要独立核实授权关系后再登记。

```bash
python -m integration.instance2 bind "$HPIS_DB" "$AID_HEX" hpis v1 "$SUBAID_B64" "$BINDING"
```

在设备上使用已登记的 owner 私钥创建兑换请求和新的持钥私钥。请求包含敏感的上游 bundle，只能通过受保护的本地交接或认证加密通道交付给受信兑换端。持钥私钥保存在该设备。

```bash
python -m integration.instance2 prepare "$DEVICE" "$BUNDLE_JSON" hpis v1 "$REQUEST" "$HOLDER"
```

兑换端检查两把固定公钥、绑定、owner 签名和到期时间，返回短期票据。请求中携带的 RSA 公钥必须与固定公钥一致。兑换和查询可跨进程进行。

```bash
python -m integration.instance2 redeem "$ARCHIVE" "$RSA_PEM" "$MAC_PEM" "$ISSUER_DIR" "$BINDING" "$REQUEST" "$TICKET"
```

历史签发窗口约束的是盲签的签发过程。既有 bundle 可由同一已登记 owner 再次兑换。撤回登记会阻止后续兑换，已发票据最长再有效五分钟。

设备安装票据后即可使用原来的 `hpis query` 命令。票据到期后用新的短期持钥对再次兑换。首次部署专用 HPIC 时可启用 `require-bound` 并启动或重启该服务，届时它只接受绑定租户的票据。默认 HPIC 同时接受原有票据。

```bash
python -m integration.instance2 install "$DEVICE" "$HOLDER" "$TICKET"
python -m integration.instance2 require-bound "$HPIC_DIR"
```
