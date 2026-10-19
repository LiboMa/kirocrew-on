# 每人一个执行边界：同一主机上的多个 KiroCrew 边界（D49 第 1 期）

## 1．背景

负责人 2026-10-19：KiroCrew 是个人助手——一个 Gateway 对应一个主人、一份记忆、一个 kiro 登录、一个原生入口。多人共用一个 Worker 时，
所有人共用 `max_subagents: 3`、同一个 kiro 账号的 Credit 与限流（无法按人归属）、只按路由分隔的记忆；一人的长任务、登录过期或部署排空会停住所有人。
**方向：每人一个 KiroCrew 边界，一台主机可放多人的边界；Team 会话在提交者自己的边界执行，Team 只共享平台层。** 共享的 `personal` 环境仅作试用、逐步退役。

现状（2026-10-19 核对）：
- 平台已支持「专属于一个人的 Worker」：`POST /v1/workers {name, owner_id, connection}` 写 `scope = dedicated`、`owner_id`；
  个人会话按 `space_workers` 选择（只有一个可用时自动选）。不同系统用户的边界通过 `boundary` / `host_principal` 校验。
- 主机层只装一个边界：`al2023.sh <边界>` 照抄示例端口（`worker_port` 8928、`gateway_port` 5476）；原生入口只有一个 `kirocrew-native.conf`
  （`default_server`，`NATIVE_PORT` 8919）；发布切换只核对一个边界；`kiro-login` / `space` / `tunnel` 都按 `stack.env` 的单个 `BOUNDARY`；
  没有任何资源上限（slice、`MemoryMax`、`CPUQuota`）。
- 平台主机 m7i.xlarge（4 vCPU / 16 GiB）；kiro-cli 经 IAM Identity Center 设备码登录（`KIRO_LICENSE=pro`）：每人用自己的 IdC 账号登录即按人计 Credit。
- 一个人同时有专属 Worker 与共享 `personal` 的空间时，`space_workers` 返回 2 个，自动选择失效，会话停在 `waiting_worker`。

## 2．已确定

| 编号 | 决定 |
|---|---|
| R1 | 边界清单：主机上的边界即 `/etc/kirocrew-on/boundary-*.toml`；`stack.env` 的 `BOUNDARY`（`personal`）仍是平台主机的首个边界。 |
| R2 | `deploy.sh boundary add <名称> --owner <用户名>`：经 SSM 在平台主机开通边界——OS 用户 `crew-<名称>`、目录、工具环境（Claude / Codex 经 Bedrock）、边界配置（R3 端口）、原生契约检查、启动 `octopus-gateway@` / `octopus-worker@`、登记为该用户的专属 Worker（`bootstrap-worker --owner`）。名称 `[a-z][a-z0-9-]{1,19}`；再次执行只补缺（幂等）。不重启平台 API。 |
| R3 | 端口：新边界的 `gateway_port` 取 5481–5580、`worker_port` 取 9001–9100 中第一个未被已有边界配置占用、且主机上未在监听的端口；不落入 CloudFront 中转段 8918–8929 与 5476。`personal` 保持原端口。 |
| R4 | 升级覆盖全部边界：`deploy.sh push` 对每个 `boundary-*.toml` 做原生契约检查，全部通过才切换 `current`；之后逐个边界排空（`.maintenance`）并重启 Gateway 与 Worker、写 `kirocrew_versions`。任一检查失败则保持旧发布。 |
| R5 | 资源上限：新边界的 Gateway 由 systemd drop-in 加 `MemoryHigh`、`MemoryMax`、`CPUQuota`、`TasksMax`（默认 6 GiB、8 GiB、300%、4096；`boundary add --memory <GiB> --cpu <%>` 可改，MemoryHigh 取 MemoryMax 的 3/4），Worker 加 `MemoryMax=2G`；写入 `octopus-gateway@<名称>.service.d/limits.conf` 与 `octopus-worker@<名称>.service.d/limits.conf`（Gateway 指纹已包含 drop-in）。`personal`（目前服务多个空间）本期不加，避免在用的会话因上限被终止；需要时 `boundary limits personal` 另行加上。 |
| R6 | 自动选择：个人会话在一个人有**恰好一个启用的专属 Worker**时选它（即使共享环境也服务此人）；否则维持现规则（只有一个可用时自动选，多个时等待选择）。Team 会话本期不变。 |
| R7 | 运维命令按边界：`kiro-login --boundary <名称>`（平台主机上的边界；`--host` 不变）、`space <run-id>` 在 `boundaries/*/worker/<run-id>` 中定位、`tunnel --boundary <名称>`（本机端口 = 10000 + 该边界 `gateway_port`）、`boundaries` 列出名称、所有者、端口、服务状态与平台登记。 |
| R8 | 原生入口：本期新边界的 `crew_url` 为本机回环（`http://localhost:<10000+gateway_port>`），经 `deploy.sh tunnel --boundary` 打开；会话页「KiroCrew 会话」（observe）照常可用。每个边界独立的 CloudFront 原生入口为第 2 期。 |

## 3．第 2 期（另行排期）

- Team 会话在提交者自己的边界执行（Team 只共享平台层）；Team 专属环境退为可选。
- 每个边界的公开原生入口（nginx server + CloudFront 分发，复用 `kirocrew-relay.conf` 的模式）。
- 跨主机放置（按主机容量选择主机，含 Worker 主机）、空闲停 Gateway、用量按人归属（D46 P6）。
- 退役共享 `personal`：其服务的用户逐个迁到自己的边界。

## 4．测试与验收

- **本机**：`boundary add` 的 dry-run（SSM 脚本内容：用户、目录、端口、drop-in、单元、登记带 owner）；端口分配（跳过已占用、已监听、中转段）；
  `push` 对多个边界做检查与切换（任一失败不切换）；`kiro-login --boundary`、`space`、`tunnel --boundary`、`boundaries` 的 dry-run；
  `bootstrap-worker --owner` 登记与重复执行；R6 自动选择（专属优先、多个专属时等待、Team 不变）。
- **EC2**：在平台主机 `boundary add` 一个边界给负责人（负责人确认后执行，并用自己的 IdC 账号 `kiro-login --boundary`）；
  `crew-<名称>` 读不到 `personal` 的 Worker Token、Gateway 密钥与工作区；负责人的新会话自动落到自己的边界并完成；`deploy.sh push` 后两个边界都运行新版本；
  随后在该边界上完成 D46 P4 的验收（第一个会话沉淀并写入 lessons，第二个同仓库会话收到，删除后不再注入）。
