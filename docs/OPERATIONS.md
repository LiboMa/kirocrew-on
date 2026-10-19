# KiroCrew ON 运维手册

适用范围：M1 单机部署（EC2 Amazon Linux 2023 x86_64，一个个人执行边界 `personal`）与本机 macOS 端到端环境。
§2–§8、§12 的 EC2 命令已于 2026-10-11 在 us-east-1 实测，结果见 `docs/handover/M1-EC2-EVIDENCE.md`。
本地端到端（macOS）的步骤见 §14。

## 1．目录与端口

| 路径 | 内容 | 所属 |
|---|---|---|
| `/opt/kirocrew-on/releases/<版本>/` → `current` | 代码（按版本并存） | root |
| `/opt/kirocrew-on/venv/` | 平台与 Worker 的 Python 环境 | root |
| `/opt/kirocrew/<版本>/venv/` → `current` | 固定版本的 KiroCrew 运行环境（composition 在此解释器中运行） | root |
| `/opt/kirocrew-tools/bin/` | kiro-cli、claude、codex、opencode | root |
| `/etc/kirocrew-on/platform.toml` | 平台配置 | root:octopus 0640 |
| `/etc/kirocrew-on/boundary-personal.toml` | 执行边界配置 | root:crew-personal 0640 |
| `/var/lib/octopus/platform/` | SQLite、引擎锁、迁移前备份 | octopus 0700 |
| `/var/lib/octopus/boundaries/personal/` | `home/`（工具认证）、`crew/`（KIROCREW_HOME）、`kiro/`（KIRO_HOME）、`worker/`（Workspace）、`worker.token`、路由日志、组合标记 | crew-personal 0700 |
| `/var/log/kirocrew-on/` | 平台日志目录 | octopus |

| 端口 | 服务 | 监听 |
|---|---|---|
| 8918 | `octopus-api`（Web 控制台 + `/v1` API + Engine） | 127.0.0.1 |
| 8928 | `octopus-worker@personal` | 127.0.0.1 |
| 5476 | `octopus-gateway@personal`（KiroCrew Gateway + composition） | 127.0.0.1 |
| 8919 / 8920–8928 / 8929 | 主机 nginx：平台原生入口 / 各 Worker 主机原生入口的中转 / 远程预览（D36；只接受 CloudFront 且校验源站密钥） | 0.0.0.0 |
| 47000–47999 | 远程预览的会话应用（执行环境内的进程） | 127.0.0.1 |

控制台与 API 经 CloudFront 访问：`https://<分发域名>/`（`deploy/aws/deploy.sh status` 显示），配置了自定义域名时也可经
`https://<CONSOLE_DOMAIN>/` 访问（见下）。API 监听 `0.0.0.0:8918`，
安全组只允许 CloudFront 源站地址访问，平台另外校验 `X-Origin-Verify`；Worker（8928）与 Gateway（5476）只监听回环，
原生入口经 SSM 端口转发：`deploy/aws/deploy.sh tunnel`（`localhost:15476 → 5476`）。

## 2．首次部署（Shell + CloudFormation，2026-10-11 EC2 实测）

在本机 kirocrew-on 检出中执行（需要 us-east-1 的 AWS 凭据；`deploy/aws/stack.env` 中的规格为候选值）：

```bash
deploy/aws/deploy.sh up            # 校验模板；查询前缀列表；在 SSM 生成源站密钥；创建变更集，含资源替换时拒绝，否则执行
deploy/aws/deploy.sh push          # 发布已提交的 HEAD：S3 → SSM Run Command → al2023.sh（安装、原生契约检查、启用服务、注册 Worker）
deploy/aws/deploy.sh kiro-login    # 一次性：经 SSM 以 crew-personal 运行 kiro-cli 设备码登录，打印验证地址与设备码，完成后 whoami 确认
deploy/aws/deploy.sh status        # 栈输出、SSM 在线状态、https://<分发域名>/health
deploy/aws/deploy.sh admin-password  # 经 SSM 读取主机上的 /etc/kirocrew-on/admin-password（root 0600），登录控制台
```

`al2023.sh` 可重复执行；KiroCrew 按 `deploy/versions.env` 锁定的 commit 从官方源码构建，版本不在已验证集合时须原生契约检查通过才会被接受。

注册 Worker（管理员 token 登录后）：`POST /v1/workers`，body 为
`{"name":"personal","owner_id":"<用户 ID>","connection":{"url":"http://127.0.0.1:8928","token":"<worker.token 内容>"}}`。
平台用户 `octopus` 与执行用户 `crew-personal` 不同，Worker 可登记给普通用户。

## 3．访问

```bash
aws ssm start-session --target <实例 ID> --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=8918,localPortNumber=18918
aws ssm start-session --target <实例 ID> --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=5476,localPortNumber=15476      # 仅在需要打开原生 KiroCrew 页面时
```

浏览器打开 `http://localhost:18918/`。`platform.toml` 的 `allowed_hosts` 必须包含 `localhost:18918`；
未登记的 Host 返回 `421 host_not_allowed`。Coding 客户端使用 `OCTOPUS_URL=http://localhost:18918` 与用户 Token。

### Worker 产出的应用（按 run-id 在主机上运行并转发）

```bash
deploy/aws/deploy.sh space <run-id>        # 运行并转发；Ctrl-C 只结束转发，再次执行复用已运行的应用
deploy/aws/deploy.sh space <run-id> stop   # 停止
deploy/aws/deploy.sh spaces                # 列出正在运行的应用与端口
```

经 SSM 以 crew-personal 在临时 systemd 单元 `kirocrew-space-<run-id>` 中运行 `worker/<run-id>/workspace/project` 的
`npm run dev`（缺少 `node_modules` 时先 `npm ci`；Vite 项目带 `--host 127.0.0.1 --port --strictPort`，其他项目读 `HOST`/`PORT`）。
远端从 5173 起取第一个空闲回环端口，本机端口为其加 10000（如 `http://localhost:15173/`）。需要本机安装 session-manager-plugin。
dev server 只在工作区写 `node_modules/.vite`；不需要把工作区拉回本机。

### 执行环境：共享环境、创建 Team 与分配（D28）

每个 Team 创建时必须分配执行环境；用户的个人空间也可以使用共享环境，不必每人一个 Worker。
会话详情页的「KiroCrew 会话」经 Worker 只读显示该会话的原生对话（`GET /v1/runs/<id>/observe`，MCP `octopus_observe`），
不发原生登录，凡能查看该会话的用户都能用，包括共享环境上的 Team 成员。「在 KiroCrew 中打开」需要整个 Gateway 的登录（K03），
只对个人空间的本人、Team 专属环境的成员、共享环境的边界所有者开放；只服务一个 Team 的共享 Worker 主机可在控制台「改回专属环境」
（`POST /v1/workers/<id>/dedicate`），之后该 Team 的成员都能直接打开。
控制台「执行环境 · Worker」是统一管理入口：每个环境显示实时健康（可达、版本、原生入口）、进行中会话数与服务的空间，
并在此设为共享、分配给 Team 或用户、移出空间、停用或启用（下面 1、3、4 步也都可以在这里完成）。

1. **设为共享环境**：控制台「执行环境 · Worker」，对专属 Worker 点「设为共享环境」并确认（环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围）。原所属的个人空间继续由它服务。
2. **创建 Team**（仅管理员）：「团队与成员」，填写名称、Team owner，选择执行环境（共享环境，或已登记的空闲专属环境）。没有可选环境时无法创建。
3. **为已有 Team 分配或更换**：「团队与成员」中每个 Team 一页，管理员在「执行环境」一行选择共享或空闲环境后「分配执行环境」/「更换执行环境」（`POST /v1/teams/<id>/environment {"worker_id"}`）。未分配前，向该 Team 提交会被拒绝（`team_without_worker`）。
   更换时旧会话不迁移：原环境为共享时不再服务该 Team；原环境为该 Team 的专属 Worker 主机时改为空闲，**只能再分配回这个 Team**（主机上留有其源码、工作区与记忆），要给其他 Team 须先重新部署主机，执行边界变化后（`deploy.sh worker-host add`，或「更新连接」）才可分配。
4. **为用户分配**：「设置 → 用户管理」创建用户时选择「个人空间的执行环境」，或在用户行中「分配共享环境」。

**派发前核对**（访问管理第 2 期）：Engine 每次准备工作区或派发新任务前核对会话的 Worker 仍启用、未退役且仍服务该会话的空间；
否则会话进入「等待 Worker」，说明「执行环境已更换或已移出该空间 / 已停用 / 已退役」，已在执行的任务不中断。成员或管理员在会话页「分配 Worker」：
分配回原环境时直接继续；换到另一个环境时在新环境重新准备工作区，已完成的任务保留，原环境中的产出不迁移。续作同样核对；诊断在环境不服务时返回 409。

**Team 管理**：Team owner 或管理员可改名、管理成员与角色（管理员不必是成员）；成员可「退出 Team」（唯一的有效 owner 不能退出）；
管理员可归档 / 恢复：归档后不能提交新会话或添加成员，已有会话可查看与停止，执行环境保留；归档的 Team 在顶栏标「已归档」，
不出现在提交、共享与分配的选择器中。不提供删除 Team。

**Worker 管理**（管理员，「执行环境 · Worker」）：改名、「更新连接」（主机替换或 Token 轮换，新地址按登记规则重新核验）、
「退役」（仍是某 Team 的执行环境或有进行中的会话时拒绝；退役后不再出现在列表与选择器中，它服务的个人空间失去该环境，历史会话保留引用）。
该环境服务的全部用户（含共享环境上的个人与 Team 成员）都可以「刷新模型」。

Team 与执行环境的管理操作都写入审计（`GET /v1/admin/audit`）：`team.create|rename|archive|restore|environment|member.set|member.remove|member.leave`、
`worker.register|rename|retire|enabled|share|dedicate|space.add|space.remove|connection`（不记录 Worker URL 与 Token）。

API 对应（OpenAPI 分组「执行环境（管理员）」）：`GET /v1/admin/environments`（全部环境的绑定、进行中会话与实时健康，
单个 Worker 不可达不影响列表）、`POST /v1/workers/<id>/share {"confirm": true}`、`POST|DELETE /v1/workers/<id>/spaces {"team_id"}|{"user_id"}`、`POST /v1/teams {"name", "owner", "worker_id"}`、`POST /v1/users {..., "worker_id"}`、
`POST /v1/workers/<id>/enabled {"enabled"}`、`POST /v1/workers/<id>/connection {"connection"}`、
`PATCH /v1/workers/<id> {"name"}`、`POST /v1/workers/<id>/retire`、`POST /v1/teams/<id>/archive {"archived"}`、`POST /v1/teams/<id>/environment {"worker_id"}`；
Team owner 也可调用 `PATCH /v1/teams/<id> {"name"}` 与 `POST|DELETE /v1/teams/<id>/members`。

### 每人一个边界（D49 第 1 期）

KiroCrew 是个人助手：一个 Gateway 对应一个主人、一份记忆、一个 kiro 登录（负责人 2026-10-19）。平台主机上可以为每个人开通自己的边界：

```bash
deploy/aws/deploy.sh boundary add alice --owner alice [--memory 8] [--cpu 300]   # 经 SSM 以 root 运行 deploy/install/boundary.sh
deploy/aws/deploy.sh kiro-login --boundary alice   # alice 用自己的 IdC 账号设备码登录（Kiro Credit 按人计）
deploy/aws/deploy.sh boundaries                    # 各边界的端口与服务状态
deploy/aws/deploy.sh tunnel --boundary alice       # 该边界的原生 KiroCrew：localhost:<10000+gateway_port>
```

- `boundary add` 在**当前发布**上开通：OS 用户 `crew-alice`、目录、工具环境、`/etc/kirocrew-on/boundary-alice.toml`（Gateway 5481–5580、
  Worker 9001–9100 中第一个空闲端口；`worker_isolation = "dedicated"`；`crew_url` 为本机回环，经 `tunnel --boundary` 打开）、资源上限
  （`octopus-gateway@alice.service.d/limits.conf`：`MemoryHigh` = ¾ × `MemoryMax`、`CPUQuota`、`TasksMax=4096`；Worker `MemoryMax=4G`，
  它也运行会话预览；两者 `OOMPolicy=continue`：超限时只结束那个进程，不停掉整个服务）、原生契约检查、启动两个服务，最后
  `bootstrap-worker --owner alice` 登记为 alice 的专属 Worker。**开通前先核对用户**（存在、尚无自己的执行环境；`--check-only`），不符时主机上什么也不留下。
  不重启平台 API；重复执行只补缺（端口与配置不变）。主机须已 `push` 过含 `boundary.sh` 的发布。登记后此人的会话即落到新边界：先完成 `kiro-login --boundary`。
- 之后每次 `deploy.sh push`：`al2023.sh` 对主机上**全部** `boundary-*.toml` 做原生契约检查，全部通过才切换 `current`；重启顺序为 personal →
  平台 API → 其余边界逐个排空、重启（一个边界未能重启时其余照常，命令最后以非零退出并列出）。SSM 时限 3 小时（每个边界排空至多 `DRAIN_MINUTES`）。
- 同一主机的会话预览共用回环端口 47000–47999：就绪检查核对监听者是本边界的用户（`/proc/net/tcp`），否则判失败、重新启动即换端口。
- 移除一个边界（手工，root）：`systemctl disable --now octopus-gateway@alice octopus-worker@alice`；
  `mv /etc/kirocrew-on/boundary-alice.toml /var/lib/octopus/boundaries/alice/boundary.toml.removed`（之后的 `push` 不再检查它）；在控制台停用其 Worker。
  数据目录与 `crew-alice` 保留，确认不再需要后再删。
- 自动选择（第 1 期个人会话，第 2 期 Team 会话同样）：有**恰好一个**启用的专属边界时选它，即使共享的 `personal` 也服务此人；两个以上时等待选择。
- `deploy.sh space <run-id>` 在 `boundaries/*/worker/<run-id>` 中找会话所在的边界。
- `personal`（共享）本期不加资源上限，避免在用的会话被终止。平台主机 m7i.xlarge（4 vCPU / 16 GiB）上放两三个边界；更多人时换更大的主机或增加 Worker 主机。
- 第 2 期（会话的执行位置）：
  - Team 会话在**提交者自己的边界**执行（须为该 Team 成员）；没有边界的成员回退到 Team 的环境，会话页与 MCP 写明「你还没有自己的执行边界…」
    （负责人 2026-10-19 选 A）；Team 没有环境而提交者有边界时照常执行，两者都没有时 409 `team_without_worker`。
  - 会话固定在所选环境：其他成员续作、返工、诊断都在创建者的边界。创建者离开 Team、边界停用或退役后，下次派发前进入「等待执行环境」，
    由 Team owner 或管理员在会话页「分配 Worker」改到 Team 的环境（可选项按创建者：Team 的环境与创建者的边界）。
  - 位置由平台统一回答：`GET /v1/placement?team_id=`（新会话的默认位置、原因 `own` / `team` / `only` / `requested` 与提示、可选环境），
    `?run_id=`（该会话可分配的环境）；控制台向导默认「自动：将在 X 执行」，MCP `octopus_environments` 标出 `default`。会话的 `placement`
    记于视图、精简状态与时间线 `run.created`。
  - Team 成员看得到会话的状态、报告、预览与下载；原生 KiroCrew 入口只开放给边界主人（`permissions.native_reason` 说明原因）。
    Team 会话的 lessons 存于提交者的 Gateway、按 Team 空间标识区分；用量计入提交者的 kiro 账号。
- 以后（第 3 期）：每个边界的公开原生入口、跨主机放置、空闲停 Gateway、退役共享 `personal`、按人的用量视图。

### Worker 主机：独立的执行环境（D28）

一台 Worker 主机 = 一个专属执行边界（`crew-<名称>`、Gateway 与 Worker），与平台同 VPC、同子网，只有平台主机可访问。

前提：平台栈与平台主机已是当前版本（`deploy.sh up`，再 `deploy.sh push`）——中转端口范围与 `kirocrew-relay.conf` 都随之下发。

```bash
deploy/aws/deploy.sh worker-host add w1     # up → push → tls → native → register → kiro-login，任一步失败即停止
```

`add` 的各步都可以单独重跑（幂等）：

| 步骤 | 作用 |
|---|---|
| `worker-host up <名称>` | 栈 `<平台栈>-worker-<名称>`：实例、安全组（仅平台安全组可访问 8943 / 8944）、该主机的原生入口 CloudFront 分发（源站 = 平台主机的中转端口，注入同一个源站密钥）。中转端口在平台的 `NativePort + 1`–`NATIVE_RELAY_END`（`stack.env`，默认 8920–8929）中分配，已有栈保持原端口 |
| `worker-host push <名称>` | 安装已提交的 HEAD（Worker 主机模式）；写入 `crew_url = https://<该主机的分发域名>` 与 nginx 配置 |
| `worker-host tls <名称>` | 私钥留在 Worker 主机，平台私有 CA 签发证书（SAN：私网 IP 与 `<名称>.worker.kirocrew-on`）。旧证书没有 DNS 名称时重跑本步（私钥不变） |
| `worker-host native <名称>` | 在平台主机写入 `/etc/nginx/conf.d/kirocrew-relay-<名称>.conf`（0640 root:nginx，密钥只从主机上的文件读取），`nginx -t` 失败即撤回；最后经中转实测一次 |
| `worker-host register <名称>` | 登记为空闲执行环境；已登记时更新连接（重新核验健康与隔离） |
| `kiro-login --host <名称>` | `crew-<名称>` 的 kiro-cli 设备码登录；看到 “Logged in successfully” 即结束本次登录，以 `kiro-cli whoami` 为准 |

原生入口链路：浏览器 → 该主机的 CloudFront 分发 → 平台主机 nginx `:<中转端口>`（校验 `X-Origin-Verify`，规则与 8919 相同）
→ 私网 TLS（只信任平台私有 CA，校验 `<名称>.worker.kirocrew-on`）→ Worker 主机 nginx `:8944` → 回环 Gateway `:5476`。
Worker 主机本身不对公网开放。控制台提示“该执行环境尚未开通原生 KiroCrew 入口”时，执行 `worker-host add`（或依次
`push`——写入 `crew_url`——与 `native`）。中转的访问与错误日志在 `/var/log/nginx/kirocrew-relay/`（仅 root；
错误日志会记下含一次性 `?token=` 的请求行）。两台主机分到同一中转端口时 `native` 拒绝写入：对后建的一台重新 `up` 换端口。
更换源站密钥后，每台 Worker 主机都要重新执行 `up`（分发注入的密钥）与 `native`（平台中转校验的密钥）。

登记后在控制台「执行环境 · Worker」把它分配给 Team（见上一节）；`worker-host status <名称>` 查看栈、SSM 与 TLS 连通性。

### 原生 KiroCrew 对话（第二个 CloudFront 分发）

控制台“在 KiroCrew 中打开”经 `https://<NativeDomain>/`（栈输出 `NativeUrl`）打开：CloudFront → 安全组（仅 CloudFront
前缀列表，端口 `NativePort`，默认 8919）→ 主机 nginx（`/etc/nginx/conf.d/kirocrew-native.conf`，只转发带
`X-Origin-Verify` 的请求，支持 WebSocket）→ 回环 Gateway `127.0.0.1:5476`。Gateway 仍然只监听回环，并保留自己的
token 登录、Host 与 CSRF 检查；公网来源经 `KIROCREW_CORS_ORIGINS` 被信任（由 `crew_url` 推导）。
入口登录有效期 `crew_entry_ttl = "1h"`（K03：该登录覆盖整个 Gateway；过期后在控制台重新点击打开）。
nginx 访问日志只记路径，不记 `?token=` 查询串。经代理的请求在 KiroCrew 中被识别为远程，不能修改 Gateway 配置。
SSM 隧道（`localhost:15476`）方式仍可用：`crew_url` 指向本地端口时，该端口进入 `KIROCREW_ALLOWED_LOOPBACK_PORTS`。

### 更新栈与管理员密码

- `deploy.sh up` 先创建变更集，含资源替换（尤其 Host）时拒绝执行并删除变更集；AMI 与可用区固定在 `stack.env`
  （`IMAGE_ID`、`AVAILABILITY_ZONE`），已有栈留空时沿用运行中的值。确需替换时以 `ALLOW_REPLACEMENT=1` 执行并先规划数据迁移。
- 管理员密码在主机首次安装时生成，只存于 `/etc/kirocrew-on/admin-password`（root 0600）；实例角色只能读取源站密钥。
  读取：`deploy/aws/deploy.sh admin-password`（经 SSM Run Command，输出保留在 SSM 命令历史中，仅账户运维人员可见）。
  **在控制台「修改密码」改了管理员密码后，须以 root 把新密码写入该文件**（0600），否则下一次 `push` 在「注册执行边界 Worker」一步以 401 失败
  （此前各步已完成、服务已重启）；写入后重新 `push` 即可。
  从旧版本迁移：在实例角色收窄之前，以 root 把现有密码写入该文件后再执行 `up` 与 `push`。

## 4．升级

1. 一致性备份：`sudo -u octopus /opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml backup --dest /var/lib/octopus/platform/backups`
2. 取得新版本代码后重新运行 `sudo deploy/install/al2023.sh personal`（新 release 目录与旧目录并存，`current` 指向新版本）。
3. 依次重启：`octopus-api` → `octopus-worker@personal` → `octopus-gateway@personal`；每个服务的 `ExecStartPost` 执行 `wait-ready`。
   Gateway 只在它加载的内容变化时重启（D34）：KiroCrew 版本目录、本发布的 `composition/`、`octopus/{config,prompts,__init__}.py`、
   边界配置与环境文件、unit 文件的指纹保存在 `/var/lib/octopus/boundaries/<边界>/gateway.fingerprint`；未变化时安装日志显示
   「Gateway 未变化，保持运行」，进行中的原生任务不中断。需要强制重启时删除该文件后重新部署，或直接 `systemctl restart`。
   需要重启时先排空（复盘 #8，`run-f7670bfb…` 的首个任务被部署中的重启结束）：安装脚本在 Worker 数据目录写 `.maintenance`，Worker 暂不接受
   新的准备与派发（503 `native_unavailable`，平台自动退避重试，会话显示「执行环境维护中（部署），稍后自动继续」），并等待 Gateway 中运行的原生任务
   结束（最多 `DRAIN_MINUTES`，默认 30 分钟）后重启；Worker 重启后移除标记。部署中断导致标记残留时手动删除该文件即可。
4. schema 迁移在 API 启动时执行，迁移前自动备份到 `/var/lib/octopus/platform/backups/octopus-v<旧版本>-<时间>.sqlite3`；结果见 `journalctl -u octopus-api`。

## 5．回滚

```bash
sudo ln -sfn /opt/kirocrew-on/releases/<旧版本> /opt/kirocrew-on/current
sudo systemctl restart octopus-api octopus-worker@personal octopus-gateway@personal
```

旧版本不认识新版本写入 `platform.toml` 的配置项时拒绝启动（“未知配置项”）：回滚到会话独立预览域名（D36 第 3 期）之前的版本，
先删除 `platform.toml` 中的 `preview_domain` 一行。

若新版本已执行 schema 迁移，旧版本会拒绝打开更高版本的数据库（“数据库 schema 版本 N 高于程序支持的 M”）。
此时先用升级前备份在独立目录执行 `restore-verify`，确认后停服替换数据库文件。
回滚记录必须写明原因与已执行的代码和数据变更；不通过回滚元数据掩盖已执行的变更。

## 6．重启与停止

- 先确认没有在途派发：控制台查看 Session 状态，或 `GET /v1/runs`。
- `sudo systemctl stop|restart <服务>` 发送 SIGTERM：API 停止接收请求、取消尚未开始的推进，在 `shutdown_grace`（默认 30 s）内等待在途推进收尾；超时则以 0 退出，在途结果由重启后的核对处理。
- 禁止 `pkill` 全局结束进程；只操作本部署的 systemd 单元。
- 重启后 Engine 从 SQLite 与 Worker 落盘记录恢复；派发回执不明的 Attempt 显示为 `dispatch_unknown`，按 Attempt 标记核对后再决定是否重派。
- EC2 实测：编码中分别重启 API 与 Worker、重启 Gateway、整机重启（`aws ec2 reboot-instances`），Session 均自动完成且无重复派发。重启 Gateway 会让原生取消运行中的任务，平台把该 Attempt 判为 blocked，自动诊断后重试；整机重启后约 30 s 服务恢复。
- 源站重启期间 CloudFront 返回 502 / 504，CLI 与 MCP 报告 `504 Gateway Timeout: API 暂不可用，稍后重试`；稍后重试即可。

## 7．日志

```bash
journalctl -u octopus-api -u octopus-worker@personal -u octopus-gateway@personal --since "1 hour ago"
sudo -u crew-personal tail -n 50 /var/lib/octopus/boundaries/personal/provider-routing.jsonl   # 实际 ACP 后端证据
cat /var/lib/octopus/boundaries/personal/gateway-runtime.json                                   # 组合标记
```

API 访问日志不记录 token、请求体或查询串；Engine 只记录异常类名。

## 8．认证与过期

- Claude Code 与 Codex 经实例角色使用 Bedrock：实例角色需要 `bedrock:InvokeModel*`（Claude）与 `bedrock-mantle:CreateInference`（Codex 的 amazon-bedrock 提供方）。
- Kiro CLI：`deploy/aws/deploy.sh kiro-login` 经 SSM Run Command 在主机上以 crew-personal 运行 `kiro-cli login --use-device-flow`（带 `stack.env` 中的 `KIRO_LICENSE`、`KIRO_IDC_START_URL`、`KIRO_IDC_REGION`）。kiro-cli 即使给齐参数仍有交互提示、无终端时失败，因此在 `script(1)` 伪终端中运行并自动回车；脚本打印验证地址与设备码，在浏览器完成授权后自动确认（设备码 15 分钟内有效，过期重新执行）。日志：`/var/lib/octopus/boundaries/personal/logs/kiro-login.log`。不需要本机安装 session-manager-plugin。

### 控制台自定义域名

`deploy/aws/stack.env` 的 `CONSOLE_DOMAIN`（如 `kirocrew-on.tinyboat.blog`）为控制台分发加上自定义域名；cloudfront.net 域名继续可用。
`deploy.sh up` 在 Route 53 中找到包含该名称的公有托管区，并在 us-east-1 找到覆盖它的已签发 ACM 证书（同名或 `*.上级域名`；
CloudFront 只使用 us-east-1 的证书）：分发加上别名与证书（SNI、TLS 1.2 以上），托管区写入 A / AAAA 别名记录。找不到托管区或
证书时 `up` 在任何变更前停止。之后执行 `deploy.sh push`，把该域名加入 `platform.toml` 的 `allowed_hosts`（Host 校验）。
托管区与证书 ID 不写入仓库，每次 `up` 时查询。浏览器中的登录按域名分开保存：改用新域名后需要重新登录；MCP 的 `--url`
可以继续使用 cloudfront.net 地址，也可以换成新域名。清空 `CONSOLE_DOMAIN` 后再执行 `up` 会移除别名与 DNS 记录。

### 代码仓库凭证（D35）

控制台「设置 → 代码仓库凭证」或 API `/v1/credentials`：GitHub fine-grained Token 与 GitLab Token（含自建实例），用于克隆私有仓库。

- **作用域**：
  - 个人：本人管理与使用，可用于本人提交的 Team 会话。
  - Team：Team owner 与管理员管理，该 Team 的会话使用。
  - 平台：管理员管理，并授权给 Team 或用户（`POST|DELETE /v1/credentials/<id>/grants`）。
  - 看不到为 404，不能管理为 403。
- **秘密只写**：创建或轮换时提交，之后只返回元数据与末 4 位，审计 `credential.*` 不含秘密。MCP 不创建凭证。
- **主密钥**：`/etc/kirocrew-on/credentials.key`（root:octopus 0640，安装脚本首次生成，不在 SSM、不进备份）。数据库备份只含密文：
  恢复到另一台主机时，须把同一个主密钥文件放到同一路径（`platform.toml` 的 `credentials_key_file`），否则现有凭证无法解密，
  需要逐个轮换。主密钥丢失后，现有凭证无法恢复，只能逐个轮换（重新录入 Token）。
- **会话绑定**：提交时依次按明确指定、工作流默认值、按仓库自动匹配（Team → 授权给 Team 的平台 → 提交者个人；同层取仓库范围
  更具体者，并列时 409 `credential_ambiguous`）解析，并固定在会话中；会话页显示创建者与凭证。改绑：`POST /v1/runs/<id>/credentials`。
- **使用**：只在准备工作区（首次与改派到其他 Worker）时由 Engine 解密，交给 Worker 的 `/prepare`。Worker 以 `GIT_ASKPASS` 读取
  运行目录内 0600 的临时文件，克隆完立即删除；remote 不含凭证，push 地址为 `DISABLED_NO_PUSH`；git 报错去除凭证后显示。
  每次使用审计 `credential.use`。停用、删除或范围不再覆盖时会话停在「仓库凭证不可用」，改绑后续作。

### 候选提交与命名门禁（D37 第 1 期）

新会话（`execution_contract` 3）中，每个 Attempt 由命名门禁判定，结果逐条写入 Attempt 证据 `gates`，控制台任务列表与 MCP 状态展示它们。
- **前五个门禁**：`native_completed`、`identity_match`、`handoff_present`、`outcome_valid`、`outcome_pass`，即原有的五项检查。
- **候选**（`implement` / `test` / `deliver` 与未标类别的任务）：
  - Worker 以发布同样的方式（临时索引、排除 `.env` 与依赖目录）在 `octopus/<run>` 上提交 `KiroCrew ON：<任务>（<run>/<task> 第 n 次）`。
  - 检查自上一个候选以来新增的内容：有疑似凭证或 `.env` 时分支不动，`no_secrets` 失败。
  - `implement` / `deliver` 没有改动时 `diff_present` 失败；`test` 与未标类别的任务没有改动时记为跳过。
- **检查命令**（`checks_pass`）：
  - Worker 在会话的项目目录中以执行用户运行任务的 `verify`：`bash -lc`，`PATH` 同远程预览，不带 Worker 自己的 `PYTHONPATH`。
  - 顺序执行，第一条失败即停；每条 10 分钟、合计 20 分钟，超时结束整个进程组。命令留在后台的进程（如为检查而启动的服务）
    在命令结束时一并结束。
  - 每条记录退出码、耗时与输出末尾 4 KB（隐去凭证）。
  - 运行期间 Attempt 仍为 `running`，控制台显示「正在运行检查命令」。停止、关闭或删除会话时结束；Worker 重启后重新运行一次。
- **审查**（`review_bound`）：审查任务派发时带上会话分支当前所在的候选（按时间，而非任务顺序）；`outcome.json` 须写
  `reviewed_commit`，审查结束时 HEAD 不变、已跟踪的文件（排除的除外）没有改动。检查与测试留下的未跟踪文件（`.coverage`、
  `reports/`、`dist/`）不算改动。
- **失败与恢复**：任何门禁失败即 Attempt `blocked`（`failure_type: gate`），照常进入自动纠错，协调者看到门禁详情与命令输出。
  Worker 拒绝生成候选或运行检查（如 Agent 切换了分支）也按门禁失败处理。
- **发布**：只推送已验证的候选，即会话分支当前所在、自身门禁全部通过的候选；会话有审查任务时，还须在最近一次代码改动之后
  有一次通过的审查（审查之后只有交付类任务的改动时仍可发布）。否则，或之后已跟踪的文件有改动时，409 `publish_unverified`，
  请续作会话。
- **兼容**：已有会话与旧 Worker（对新接口回答 404）行为不变：新门禁记为跳过。
- **第 3 期**：
  - `artifacts_locked`（声明了 `paths` 的任务）：候选记录从任务起点以来改动的文件名（重试中撤回的改动不算）；此前已验收任务交付的文件，
    只能由 `paths` 覆盖它们的任务修改；平台检查命令留下的文件（`.coverage`、报告）不算交付物。
    `paths` 支持目录（`src/`、`src/**`）、glob（`src/**/*.ts`、`**/*.py`）与整个项目（`.`）。未声明 `paths` 的任务（模板）跳过。
  - `tests_fail_on_baseline`（`test` 任务，`checks_pass` 通过之后）：Worker 把本任务从起点（另一个任务最近的候选）到候选的改动
    三方合并到会话基线（`git merge-tree`），在项目目录中原地切过去（`git read-tree -u --reset`，未跟踪的依赖不动），再运行一次 `verify`；
    全部通过即失败（测试在改动之前也通过），有命令失败即通过。运行结束后必定恢复到候选。
    - 跳过而不判定：会话此前没有源码改动（为已有代码补测试）、测试任务没有改动、检查命令会安装依赖、合并冲突、
      工作区有未进入候选的已跟踪改动或与基线同名的本地文件、运行中断 / 停止 / 超时。
    - 运行期间（最长与检查命令相同）该会话的预览启动、下载与发布返回 409「正在会话基线上运行测试，稍后再试」。
    - Worker 中途重启时留下 `<run>/baseline.json`；该会话的下一次派发、候选、检查、发布、下载或预览之前先恢复到候选。
  - Agent 的任务说明与规划提示写明这两条门禁，避免先失败再纠错。

### Feature 报告（D37 第 2 期）

会话页「报告」、`GET /v1/runs/<id>/report?format=md|html` 或 MCP `octopus_report`：
- **内容**：需求与计划（任务表）、每个任务最终一次 Attempt（请求与实际的工具 / 模型、结论、门禁表、候选、检查命令的退出码与耗时、
  验收项与建议、审查绑定）、纠错记录、已验证候选与发布的分支 / PR / MR。
- **大小**：其余 Attempt 只计次数；结论最多 2000 字符，门禁详情与命令只取首行；检查命令的输出不进入报告。
- **安全**：
  - 会话中的所有文字都转义（Markdown 中多行文字放在代码块里、单行文字转义标点，不会变成标题、链接或图片）。HTML 不含脚本与外部资源（`Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; img-src data:`），
    只有 https 的 PR 链接是链接。
  - 控制台在不允许脚本的 iframe 中显示，可下载 Markdown / HTML。
- **权限**：与查看会话相同（`read`），看不到会话时 404。

### 任务的映射来源

会话视图中每个任务带 `mapping: {source, label}`：`steps`（提交时指定）、`workflow`（工作流名称与版本）、`template`（模板，
规则匹配时注明）、`document`（导入的工作流文档）、`platform`（规划任务本身）、`planner`（KiroCrew 规划）、`continue`
（续作时由用户修改了工具 / 模型 / Effort）、`coordinator`（协调者换用，写明从哪个工具换来与原因）。控制台任务行、MCP 状态与报告的计划表显示它。

### 交接与轮询（D40）

- **前序交接**：派发时每个已验收的前序任务（按 `depends_on`）只带名称、类别、实际后端、结论、摘要（≤ 1500 字符）、
  发现（≤ 10 条）、建议（≤ 5 条）、候选（短 sha 与改动的文件）、检查命令及退出码，以及完整交接文件的路径 `handoff`；
  不再附原始结果与证据。Agent 需要细节时读取交接文件（同一执行环境）。
- **轮询**：运行中的 Attempt 越久越少查看：2 分钟内每 2 秒，10 分钟内每 5 秒，30 分钟内每 10 秒，之后每 20 秒；
  停止、关闭等命令不受影响（立即生效）。

### 重新规划（D41）

- 协调者判断计划本身有问题（任务拆分不当、缺少步骤、顺序错误、任务目标与需求不符）时决定 `replan`：平台保留已验收的任务，
  删除失败的任务及其后的任务（其 Attempt 留作历史，修订历史保存原计划），插入 KiroCrew 重新规划任务 `replan-1`。
- `replan-1` 的提示列出被替换的任务、失败原因与协调者的要求（各节限长），用户需求、规划补全的需求与已验收任务的交接引用派发时已带的上下文，
  不重复抄写（Worker 的任务说明上限 8000 字符）；失败任务的改动仍在会话分支上。
- 其结果与首次规划相同地校验后追加（1–6 个任务）：新任务 ID 不与历史上任何任务相同，改名后 `depends_on` 随之改写，可以依赖已保留的已验收任务。
  之后的任务不再带「纠错后的重新执行」要求；返工更早的任务时 `replan-1` 不再重新运行。
- **检查命令可用的命令**：Worker `/health` 的 `commands`（常见命令中 `available` / `missing`，按检查命令的搜索路径），
  写入首次规划、重新规划与协调者的上下文，`verify` 只用可用的命令（EC2 两台主机只有 `python3`，没有 `python`、`node`、`npm`、`pip`）。
  与 `tools` 相同，由平台的健康检查保存（D43，最多 10 分钟）。检查命令以退出码 127 失败时，协调者按「verify 写错」处理：
  未重新规划过用 `replan`，否则 `wait_for_user`，由用户续作时修改该任务的 `verify`。
- 每个会话至多一次，消耗一轮纠错；工作流设计会话与规划任务本身不重新规划。
- 可见：修订「自动纠错：重新规划，替换「…」」与「重新规划：追加 N 个任务」；任务来源「KiroCrew 重新规划」；控制台纠错记录「已安排重新规划」。

### 需求中指定的工具与模型（D45）

- KiroCrew 规划时识别需求中点名的工具或模型（如「审查用 codex」），任务带 `requested`（工具、模型与需求原文片段）；映射来源显示「需求中指定：「…」」。
- 平台照指定执行：规划者改了工具会被改回，指定的模型未公布时不改为 `auto`（规划说明中写明）。
- 指定的工具不可用或模型未公布：派发前拒绝，会话停下并写明「需求指定的 …」，不交给协调者、也不换用其他工具。
  修复执行环境（安装、登录该工具或公布该模型）后续作，或在「配置并续作」中改用可用的工具或模型——用户的新选择取代需求中的指定。
- Worker 因平台记录过期而拒绝指定的模型时同样如实停下；协调者不能换用指定的工具或模型。确认模式下，指定的工具不可用会在规划说明中预先写明。
- 只有 KiroCrew 规划的任务带 `requested`：显式提交的 steps、模板、导入的文档与保存的工作流中的 `requested` 一律去掉，
  其工具不可用时仍按 D38 由协调者推荐换用。规划者写法不完整的 `requested`（空值、缺工具与模型）被忽略并记说明，不使计划失败。

### 计划确认（D44，准入第 1 期）

- 会话的 `admission`：`confirm`（确认后执行）或 `auto`（立即执行）；未写时取平台配置 `default_admission`（`platform.toml`，默认 `auto`，
  可改为 `confirm` 让所有新会话先确认）。
- `confirm` 的会话：规划（`plan`、`replan-*`）照常完成，第一个执行任务派发前停在「计划待确认」（`awaiting_confirmation`）；
  控制台会话页「确认并开始」、API `POST /v1/runs/<id>/confirm` 或 MCP `octopus_confirm` 确认后执行；修改任务后续作也视为确认；也可停止或关闭。
- 只确认一次：之后的返工、重试、换工具与重新规划不再停下。确认者与时间记在会话的 `confirmed`、时间线「已确认计划」与报告抬头。
- 只有 KiroCrew 规划会话的规划任务（`plan`）与重新规划任务在确认前运行；提交或模板中名为 `plan` 的任务同样等待确认。
- 等待确认的时间不计入会话预算；确认与续作一样重新计算预算，并停止正在运行的远程预览。已归档 Team 的会话不能确认。
- MCP 精简状态在等待确认时带出各任务的 `acceptance` 与 `verify`，供客户端展示给用户。

### 等待与自动恢复（D43）

- **健康检查**：平台每 10 分钟读取一次各已启用 Worker 的 `/health`（有会话在等 Kiro 登录的 Worker 每分钟一次），执行边界未变时保存为执行环境能力
  （工具、检查命令、Kiro 登录、模型；「执行环境」中显示「最近检查」）。升级 Worker 后不必再手动「刷新模型」，最多 10 分钟内生效；急用时仍可手动刷新。
  边界、宿主账号或隔离方式变了不保存（日志「another execution boundary」），由管理员核查。
- **条件满足后自动继续**（不计入、也不重置会话预算；时间线「条件满足，自动继续」）：
  - Kiro 任务因登录过期失败：「等待 Kiro 登录恢复」——管理员执行 `deploy.sh kiro-login`；平台在失败后先看到登录过期、再看到恢复时重跑该任务。
    只认原生错误中的登录过期（Agent 自己报告的权限、额度问题不算）；失败后检查到登录正常（未见过期，或检查前已恢复）时提示改为
    「Kiro 登录正常，需要处理」，不自动重跑；其他工具的登录平台读不到，也等人处理。等待的时间不计入会话预算的时长。
  - Worker 被停用：运行中的会话「执行环境已停用」，重新启用后回到停用前的状态继续查看（原生任务可能已在此期间完成）；
    准备中的会话 `waiting_worker`，重新启用或执行环境重新给到该空间后继续准备。
  - 仓库凭证不可用：凭证重新启用、授权恢复或在会话页更换后（且仍覆盖该仓库）自动重新准备。
- 派发时的 Kiro 登录过期与 Worker / Gateway 暂不可用仍由退避自动重试（M1，不变）。

### 会话预算（D42）

- 自动纠错的预算有三项：协调者轮数 `max_cycles`（默认 2，1–5）、自动重试次数 `max_retries`（D34 瞬时重试与看门狗重试合计，默认 4，0–20）、
  时长 `budget_minutes`（默认 720，30–4320）。从会话开始或用户最近一次「续作」/「请求诊断」/「分配 Worker」起计算；手动请求的那一轮诊断不计入；
  会话停下后时长不再累计。
- 只限制**自动**的工作：自动重试的次数或时长用完时不再重试、交给协调者诊断；轮数或时长用完时不再诊断，会话 `blocked`，
  「需要处理」写明是哪一项拒绝了这次自动纠错（记在失败 Attempt 的 `budget_refused`）。计划内的任务照常执行，正在运行的任务不因预算停止。重新规划仍是每个会话一次，不随续作重置。
- 可见：控制台纠错记录下的「预算（…）：纠错 a/b 轮 · 自动重试 c/d 次 · e/f 分钟」，MCP 精简状态的 `recovery.budget`，报告的纠错一节。
- 调整：启动或续作时的 `recovery` 设置（API / MCP）；续作本身就重新开始计算。

### 看门狗（D39）

- **判定**：运行中的 Attempt，安静时长（KiroCrew 子任务 `state.json` 最后写入至今，Worker 按自己的时钟计算；从派发或最后一次
  等待批准时起算）超过 `idle_minutes`（默认 30），或总时长超过 `attempt_minutes`（默认 170）时，平台以 `DELETE /api/spawn/{id}`
  停止该原生任务。等待批准与检查命令运行期间不判定；没有 `state.json` 时只按总时长判定。
  KiroCrew 自己在 180 分钟收回子任务（不经平台的自动重试），`attempt_minutes` 大于它时不起作用。
- **之后**：Attempt `blocked`（原因写明分钟数与最后工具），自动重试一次（不消耗纠错轮次）；重试仍卡住时交给协调者诊断（可换工具）。
  自动纠错关闭的会话只停止、不重试。
- **长命令**：单个命令运行期间 `updated_at` 不更新；大型构建或完整测试较慢的项目，启动或续作时设 `recovery.idle_minutes`（5–240）。
- **旧 Worker**（不支持停止单个任务）：不行动，任务继续，避免两个 Agent 同时修改同一工作区。停止失败时一分钟后再试，同样不重试。
- 重试在被停止任务留下的工作区上开始（未提交的部分改动仍在）：与 D34 的自动重试相同；D37 的候选按任务起点计算改动。

### 发布：自动或人工确认（D47）

- 默认：会话完成后由人确认发布——会话页「发布到仓库」或 MCP `octopus_publish`（创建者、Team owner、管理员）。
- **自动发布**：会话的 `publish_policy.auto` 为 true 时，会话完成且满足发布条件后，平台以创建者的身份推送 `octopus/<run>` 分支并创建或沿用 Draft PR / MR
  （与手动发布相同的核对：写权限凭证、只推已验证候选、秘密扫描、不推默认分支）。开启方式：启动时 `publish: {auto: true}`（控制台向导「完成后自动发布 Draft PR」、
  MCP `octopus_start.publish`）；会话页的同名开关或 `POST /v1/runs/<id>/publish-policy`（MCP `octopus_publish_policy`）；需求中明确要求提交 PR / MR 时由 KiroCrew 规划者标注
  （来源显示「需求中要求提交 PR」），用户自己的设置优先。
- 自动发布做不到时（没有写凭证、改动未通过审查、发现秘密、远端冲突）会话仍为「已完成」，「需要处理」为「自动发布未完成」并写明原因；同一版本只自动尝试一次，处理后手动发布。
- Agent 没有仓库写凭证（设计如此），规划不安排推送 / 建 PR 的任务；任务因尝试推送失败时，「需要处理」为「Agent 不能推送或创建 PR」并指向发布按钮（EC2 `run-361cd491…`）。
- 来自仓库的会话（`source.type = git`）发布到它启动时的仓库。新建源码的会话可发布到新仓库（D48，EC2 `run-f7670bfb…` 的需求）：发布时给出
  `repository: {owner, name, private?}`（默认私有）与有写权限、范围覆盖该仓库、能建仓库的凭证（GitHub classic PAT `repo` 或细粒度令牌的
  Administration 写；GitLab `api`），平台建仓库（或用已有的空仓库）、基线推到 `main`、工作走 Draft PR；只建一次（`publish_target`），之后再次发布
  与自动发布沿用，不能改到另一个仓库；非空的同名仓库拒绝（`repository_not_empty`），不碰其默认分支。规划者识别到建仓库只记建议
  （`publish_suggestion`），不开启自动发布、不创建，完成后「需要处理」为「发布到新仓库待确认」；自动创建只在用户启动时（或会话页）自己给出仓库与凭证。
  建仓库写审计 `credential.use`（`action: repo.create`）与时间线「已新建仓库」。

### 协调者的可选目标（EC2 `run-40a1f6d1…` 修复）

- 诊断上下文的 `allowed_targets` 列出失败任务及其上游；`previous_decisions` 带每轮的结果与平台拒绝的原因（`refused`）。
- 目标排在失败任务之后（例如计划中审查之后的「修复」任务）会被拒绝，原因写明可选的目标与「返工上游，其后的任务自动重跑」；协调者不再重复被拒的决定。
- 规划者不在审查之后另设「按审查修复」的任务：审查要求修改时平台自动安排上游返工与复审。

### KiroCrew 配置与隔离（D46 P0，以 KiroCrew 0.10.0 为准）

- **记忆**：0.10.0 没有 `memory.enabled`（此前写入的该键不起作用，边界的全局记忆一直开着）。平台新建的父会话以 `memory_mode: "persistent"` 运行：
  KiroCrew 只导出 persistent 的会话（`session_export.py:380`），关闭与删除需要这份备份；`incognito` 仍读记忆（只是不写），`temporary` 不读不写，
  且已建会话的模式只能收紧、不能放宽。记忆按执行环境分隔：每人独立 Worker，只有 Team 共用一个 Worker 空间（负责人 2026-10-17）；
  一个 Worker 服务多个空间时记忆不分隔，控制台已标明「记忆未分隔」。不要改 `memory.persistence_enabled`：它会同时关掉 lessons。
  KiroCrew 不导出 incognito / temporary 的会话（`export_slot_not_persistent`）：关闭与删除时记一份标明的观察记录
  `restricted-session-observation.json`（`backup_kind: native_restricted_observation`，与只有说明性 note 的 `empty-session-observation.json` 同理），
  不伪造备份，然后照常关闭、删除（负责人 2026-10-17）；KiroCrew 已没有这个父会话的活动 slot 时（incognito 的 slot 在 Gateway 重启后不恢复，
  或在原生页面删掉了）同样处理，记 `missing-session-observation.json`；其他导出错误仍阻止关闭与删除。2026-10-17 部署的 3ec713b 曾以 incognito 建立父会话
  （`run-d098…`、`run-6d7c…`、`run-f7670bfb…`），按此处理。
- **派发白名单**：协调者模板的 `toolsSettings.subagent.availableAgents`（KiroCrew 读的位置；此前写在顶层，等于全部允许）列出平台派发的
  `poc-kiro`、`poc-claude`、`poc-codex`、`poc-opencode`、`poc-diagnose`；种入时迁移旧位置，用户的其他工具设置保留。
- **协调者**排除 `@kirocrew-core/monitor_*`（不自行布置监视循环、追加回合）。新边界的 `agent.adaptive_concurrency_mode` 为 `fixed`（并发上限即 `max_subagents`）。
- 不再发送 KiroCrew 已忽略的 `solo_reason` / `solo_details`。
- **配置契约**：平台写入 `config.json` 的键列在 `tests/kirocrew_contract.json`（版本须等于 `deploy/versions.env` 的 `KIROCREW_ACCEPTED_VERSION`）。
  本机回归核对 `seed()` 只写清单内的键；部署时的原生测试在主机上核对清单中每个键都是已安装 KiroCrew 的配置字段，并用 KiroCrew 自己的加载器读回
  新生成的配置，不得有被忽略的键（`_extra_keys` / `_extra_sections`）。升级 KiroCrew 时先在新版本上核实清单并改版本号，原生测试不过则部署中止。
  同一清单的 `runtime_errors` 列出平台自动重试（L0）所依赖的 KiroCrew 报错文字，各附一条 EC2 原样报错：本机回归核对每条都按其种类重试，
  部署时的原生测试核对已安装 KiroCrew 的源码仍含这些文字——升级后措辞变了，运行时故障不会悄悄改由协调者处理。
  `http` 列出平台发给 KiroCrew 的每个请求体所到的处理模块与其读取的键：本机回归以 AST 扫描 `octopus/worker.py` 的每个请求体，只许用清单内的键
  （KiroCrew 静默忽略未知键）；部署时原生测试核对已安装 KiroCrew 的这些模块仍读取这些键。

### Agent 提示（D46 P1）

- 系统提示：所有 Coding 工具执行任务时为「研发执行者」；父会话的 Agent 为「Owner 观察员」（解释进展，不派发、不改源码）；
  诊断 Agent（`poc-diagnose`）只写 `decision.json`，不修改源码。Agent 不自行推送或建 PR / MR：平台提交候选，审查通过后由用户发布。
- 规划（首次与重新规划）共用同一份规则（契约、门禁、preview.json、.gitignore、结论用法、工具选择依据与审查独立性），环境事实截断，不超过 8000 字符；
  不再写默认工具组合，按执行环境中可用的工具选择。
- 任务说明（`instructions.md`）按任务性质结尾：规划、设计与诊断不被要求开发；实现与交付须产生改动；审查只读；测试不改实现；
  写明交接内容清单、检查命令是否由平台运行；交付任务带 `.octopus/preview.json` 的格式；续作改了需求时标明规划补全已过时。
- 纠错要求只发给被安排修复的任务（决定与失败摘要）；其后的审查 / 测试只收到「复验」说明；协调者上下文只在 `context.json`。
- 内置模板的每个步骤带 `kind` 与验收项（审查绑定候选、实现与交付须有改动），审查用 `changes_requested`，不再使用已停用的 OpenCode，步骤提示不再重复目标。

### 角色卡片（D46 P2）

- 任务可带 `role`：`spec`（Spec 分析，kind design）、`sde`（SDE 开发，implement / deliver）、`integration_test`（集成测试，test）、
  `security_test`（安全测试）、`challenge`、`evaluator`（review）。`kind` 仍决定门禁；`role` 决定 Agent 拿到的角色卡片。省略时不发卡片、行为不变；
  给了 `role` 没给 `kind` 时取该角色的首个类别，两者不相容时拒绝。
- 卡片按 KiroCrew 的 SKILL.md 格式存于 `octopus/roles/<role>/SKILL.md`（正文 ≤600 字符），平台把它内联到任务说明开头（「你的角色：…」），
  契约中写明角色——三个后端行为一致（KiroCrew 的 skill 只能按 Agent 配置挂载、不能按任务选，负责人 2026-10-18 选定内联）。
- 审查独立性：`security_test` / `challenge` / `evaluator` 不得使用被审查改动的作者工具（此前 implement / deliver 任务的 tool；重新规划时含保留的
  已验收任务）。用户提交的违反时拒绝；KiroCrew 规划的照常应用并在规划说明中记「审查不独立」。
- 规划提示列出 6 个角色（取自卡片的 description）与独立性规则；控制台任务卡片「角色：…」、MCP 精简状态的任务 `role`、报告的类别列「review（安全测试）」。

### Evaluator 门禁（D46 P3）

- 适用：执行契约 4 的新会话，且 Worker 支持角色卡片（`role_cards`）；之前的会话与未升级的 Worker 不变。
- 改代码的会话（有 implement / deliver 任务）须有一个 `role: evaluator` 的任务排在最后一次改动之后（负责人 2026-10-18：都要经过 Evaluator）。
  规划者被要求安排；缺少时平台在会话即将完成时追加「Evaluator 验收」：验收项取此前 implement / deliver / test 的验收项，工具按环境事实与审查独立性
  （计划中审查用过且非作者的工具 → 第一个非作者的可用工具 → 没有时用可用工具并记「审查不独立」），映射来源「平台追加（改代码的会话须经 Evaluator）」。
- Evaluator 对每条验收项给出 pass / fail / pending 与证据（门禁 `evaluated`）：缺项或缺证据 → 交给协调者；fail → 照审查流程返工上游、复审；
  全部 pass → 通过。实现者自报的 pass 不再是会话通过的依据。
- pending（需要人判断，如观感、文案）且无 fail → 会话停在「验收待确认」（`awaiting_acceptance`）：会话页「确认通过」或 MCP `octopus_accept`
  （创建者、Team owner、管理员；已归档 Team 不可），记确认者，不计入预算；也可续作返工。报告「验收（以 Evaluator 为准）」列出判定与确认者。

### 用量（D46 P6a）

- 原生任务结束时 Worker 为 Attempt 记 `evidence.usage`：`credits`（kiro 的 Kiro credit；claude / codex 经 Bedrock 为 0）、`seconds`（墙钟）、`turns`
  （来自 `GET /api/spawn/{id}`），以及 KiroCrew 用量行（`GET /api/usage/turns?slot=subagent:<对话>`，每次运行结束时一行）中这次运行的
  `input_tokens / output_tokens / cache_read_tokens / cache_write_tokens`、`cost_usd`、`model`。续接的各轮共用对话的 slot，按运行时间窗匹配各自的一行；
  没有那一行（因回合上限或异常结束、超过 7 天）时只有前三项并标 `tokens: "unavailable"`。结果缓存在 Attempt 目录的 `usage.json`；读不到不影响判定。
- 会话视图 `usage` 汇总全部 Attempt（重试、返工、平台追加的任务；协调者诊断不计）；任务卡片的 Attempt 行「用量」、会话页「本会话用量」、
  报告「用量」一节（每个任务一行与合计）、MCP `session.usage` 与 `latest.usage`。
- 预算（D42）不变：用量只显示。按费用设预算、按人 / Team 汇总与工具时间线（KiroCrew 子任务没有可用的工具记录）见 BACKLOG。

### 保留上下文的返工（D46 P5a）

- 适用：执行契约 4 的会话、Worker 报告 `continue`。平台安排的返工——协调者修复目标任务、返工后的审查 / 测试复验、上游返工后重新执行的任务——
  在该任务上一个 Attempt 的原生对话上续接（KiroCrew `POST /api/spawn/{conversation}/continue`：沿用原会话的上下文与 cwd，不带 Effort 与批准方式），
  消息另加一句返工说明（按任务类别：修复「在已有改动上修复，不要从头重写」；审查 / 测试复验「只针对本轮重新核对，不修改代码」；分析「不修改业务代码」）；
  instructions.md 照常写本轮要求。
- 不续接（全新派发）：第一次派发、用户续作之后、协调者换了工具、工具 / 模型 / Effort 与上一轮不同、任务设了显式 Effort、瞬时失败的自动重试；
  `approval_mode = "auto"` 而 YOLO 关闭（`crew_yolo = false`）的边界（续接不能要求自动批准，记 `continue_refused: approval_mode`）；
  对话不是这个 Worker 上本会话的（如改派到另一执行环境之后，记 `not_this_session`）。
- 对话 ID 是这一串中第一个 Attempt 的原生 ID；Worker 只续接本会话 Attempt 的对话。续接被拒（对话不存在、正忙、不可续接）时改为全新派发，
  证据记 `continue_refused`；容量已满与全新派发相同（核对后再派发）。
- 可见：任务卡片「返工：续接上一轮的对话（保留上下文）」或「续接被拒，已重新派发：原因」；MCP `latest.continued` / `continue_refused`；报告「返工：」。
- 运行中补充说明（D46 P5b）：运行中（`running` / `waiting_approval`）的 **Kiro** 任务卡片「补充说明」、MCP `octopus_steer`、
  `POST /v1/runs/<id>/steer {task_id, message ≤4000}`（创建者、Team owner、管理员；已归档 Team 不可）。经 Worker `POST /steer` → KiroCrew
  `POST /api/spawn/{id}/steer`（`interrupt`）插入正在运行的这一轮。写审计 `task.steer` 与时间线「已补充说明」。
  只支持 Kiro 后端（KiroCrew 0.10.0：codex 拒绝，Claude Code 会丢弃）；不提供 `follow_up`（KiroCrew 会在这一轮结束后另起一次平台不跟踪的续接运行）。
  任务已结束 409 `not_running`；原生任务正在启动或结束 503（稍后再试）；非 Kiro 任务或 Worker 未升级 409 `steer_unsupported`。
- 释放对话（D46 P5b）：会话**删除**时 Worker 释放本会话的可续接对话（`POST /api/spawn/{conversation}/release`，各续接链的第一个），尽力而为；
  lifecycle 记 `released`。关闭不释放（重新打开后仍可续接）。

### Knowledge（D46 P4）

- 适用：执行契约 4 的会话、源码来自仓库、执行环境为专属（`dedicated`）且 Worker 报告 `role_cards` 与 `knowledge`。
  共享执行环境不沉淀、不注入（KiroCrew 的成员只是路由，不能按空间隔离 lessons；负责人 2026-10-18），会话页写明。
- 仓库标记：准备检出时 Worker 在项目根写空文件 `.octopus-kb/<仓库标识>.<空间散列>`（`host_owner_repo` 小写，空间散列为 Team 或个人 ID 的
  sha256 前 12 位：执行环境换到另一个 Team 后，不把前一个空间同一仓库的知识注入新空间），并写入 `.git/info/exclude`（不提交、不发布）。
- 沉淀：有 implement / deliver 任务的会话即将完成时（Evaluator 之后），平台追加「Knowledge 沉淀」（`role: knowledge`、`kind: design`、只读；
  工具优先 kiro，否则第一个可用工具）。它在 outcome.json 写 `knowledge: [{rule ≤300, avoid? ≤200}]`，至多 5 条。续作有任务重跑时它也重跑
  （重跑原因「改动后重新整理项目知识」）并再写一次。
- 它从不阻止会话完成：结论无效或 blocked、派发被拒（工具不可用、kiro-cli 登录过期、模型未公布）、看门狗停止，都记为「Knowledge 沉淀未完成：原因」
  （会话页与时间线），任务记为通过，不进入自动纠错；瞬时失败仍自动重试一次。它是只读的（门禁 `read_only`）：改了已跟踪文件时 Worker 把这些文件
  还原到候选（`/candidate {mode: restore}`，不移动分支），本次不沉淀——发布的仍是门禁与 Evaluator 通过的候选。
- 写入：平台丢弃超长、含疑似秘密（与发布相同的扫描，文本不保存）、超过 5 条、此执行环境同一仓库已记录的条目；其余经 Worker
  `POST /knowledge/write` → KiroCrew `POST /api/lessons`（`category: knowledge`、`applies: always`、`repo_scope` 为标记、`negative` 为 avoid，
  `X-Session-Key: dashboard:ui`，写入该 Gateway 的全局存储）。每条记入台账表 `knowledge`（结果 written / superseded / refused / skipped 与原因）。
  Worker 未升级（404）记 skipped；执行环境暂不可用时最多等 10 分钟再记 skipped——写入从不阻止会话完成。时间线 `run.knowledge`。
- 注入：适用的会话派发任务时 `include_lessons: true`（`include_memory` 仍为 false），按当前执行环境判断；KiroCrew 只注入 `repo_scope` 标记存在于
  检出目录的 lessons。协调者的 `excludedTools` 与 `managedToolPolicy.exclude` 含 `learn_add`、`learn_remove`：平台是唯一的写入方。
- 删除：会话页「本次沉淀的知识」或 `POST /v1/runs/<id>/knowledge/forget {id}`（创建者、Team owner、管理员，写审计）：经写入它的执行环境
  `DELETE /api/lessons {rule, exact: true, repo_scope}`，台账记删除者。MCP 精简状态 `session.knowledge`。
- 前提：KiroCrew `memory.persistence_enabled` 与 `memory.inject_lessons` 为 true（0.10.0 默认；关闭前者时写入被拒，台账记
  `persistence_disabled`）。KiroCrew 自身的自动 lessons（task-runner、整理提取）不经平台，`include_lessons` 时同样注入——专属环境内属于同一空间。

### 编码技能包（D50）

- 平台维护五个**方法类**技能，随发布固定版本：`octopus/skills/<名称>/SKILL.md`（头部 `name`、`description`、`source`、`license`）——
  `test-driven-development`、`systematic-debugging`、`verification-before-completion`、`receiving-code-review`（改编自 superpowers 6.4.1，MIT，
  Jesse Vincent）、`frontend-design`（改编自 Anthropic claude-plugins-official，Apache-2.0）。改编只去掉与无人值守任务冲突的部分（询问用户、
  提交 / 推送、开 worktree、写计划文件），改为平台的说法（交接、outcome.json、blocked + `failure_type: input`），每个文件开头写明改动；
  原许可证 `octopus/skills/LICENSE-*`。重新生成：`SKILL_SOURCES=<claude-plugins-official 插件缓存> python3 tools/build_skill_pack.py`。
- **流程类**（brainstorming、写计划、执行计划、worktree、收尾）不交给任务：由平台的规划、计划确认、派发、门禁与 Evaluator 承担。
- 选择：任务契约 `skills`（至多 4 个、取自技能包、去重保序）。KiroCrew 规划者按任务推荐（规划提示列出技能与用途）；提交者也可在 steps 里写——
  名称不在技能包中或超过 4 个时拒绝；规划者给出的则丢弃并在规划说明中记下。平台不内置「角色 → 技能」对照。
- 交付：Worker 把所选技能复制到 Attempt 目录 `skills/<名称>.md`（与 instructions.md 同处，三个后端读同一份），instructions.md 增「## 方法」：
  名称、用途与路径，「开始前完整阅读并遵循；与本任务契约冲突时以契约为准」。每个 Attempt 各自一份，返工（含保留上下文的续作）同样附上。
- 兼容：Worker `/health` 的 `skills` 列出它带的技能包；对没有该能力的 Worker 平台不发 `skills`（与 `role` 相同），任务照常执行。
- 可审计：dispatch.json 与 Attempt 证据 `skills: [{name, sha256}]`；控制台任务卡片「方法：…」（计划的）、报告任务一节「方法：…」（实际交付的，
  取自证据）、MCP 精简状态任务的 `skills`。

### 停用的 Coding 工具（K01）

- Worker 配置 `disabled_tools`（默认 `["opencode"]`）：所列工具在 `/health` 的 `tools` 中报告为不可用，原因「已停用（K01…）」。
  规划者不会选用、协调者不会换用；提交的 steps、模板或工作流中用到它的任务在派发前停下并写明原因，按 D38 由协调者推荐换用可用的工具。
- 恢复：在 Worker 配置中写 `disabled_tools = []`（或去掉 opencode）后重启 Worker，平台 10 分钟内的健康检查即更新（急用时「刷新模型」）。
- K01 复测（2026-10-16，YOLO 常驻）：OpenCode（`opencode/big-pickle`）两次 Attempt 均只返回 `_No response._`，仍未修复。

### 换工具（D38 第 1 期）

- **工具是否可用**：Worker `/health` 的 `tools`（`available`、`reason`；Kiro 另有 `login`）。平台只在登记 Worker 或「执行环境 · Worker」
  中「刷新模型」时保存它，此外平台每 10 分钟自动读取一次（D43）。尚未读取到时派发照旧，协调者的换工具推荐因「未报告可用性」被拒。
- **何时换**：任务因工具本身失败（平台记录中不可用、模型未公布或不受支持、实际后端不符、原生错误在自动重试后再现）时，
  自动纠错请 KiroCrew 协调者诊断；协调者可推荐 `switch_tool`，平台核对通过后改写该任务的工具 / 模型并重跑。
  Kiro 登录过期仍等待管理员执行 `deploy.sh kiro-login`，不换工具。
- **关闭**：启动或续作时 `recovery.switch_tools: false`（控制台自动纠错设置中的对应选项）。
- **记录**：Attempt 证据的 `switched_from`；变更记录 `task.tool_switched`；修订摘要「自动纠错：「任务」由 X 换为 Y（原因）」。

### 批准代理（D38 第 2 期）

- **来源**：会话原生父 slot 中等待批准的工具请求（`GET /api/chat/slots` 的 `pending_approval_info`），以及 KiroCrew 状态级批准中属于该 slot 的记录
  （`GET /api/approvals`）。平台派发的 spawn 在 `approval_mode = "auto"` 的边界上自动批准，不出现在这里；全局批准为 interactive 时，
  原生父会话想运行的工具（K20）与 interactive 边界上的任务会出现。
- **答复**：会话页「待批准」卡片，或 MCP `octopus_approve`（客户端须先征得用户同意）。只有会话创建者、Team owner、管理员可以答复；
  答复前 Worker 重新核对该请求仍在等待且属于本会话。
- **记录**：审计 `approval.answer`（来源、工具、命令前 200 字符、决定、答复人），会话变更记录 `approval.answered`。
- **旧 Worker**：列表为空并标明不支持（`supported: false`），在原生 KiroCrew 中处理。
- **YOLO（默认开启）**：执行边界配置 `crew_yolo` 默认为 `true`（负责人 2026-10-15）。每次 Gateway 启动时，平台把
  `agent.dangerously_skip_permissions` 写入 `crew/config.json`，KiroCrew 据此建立常驻 YOLO，不随重启失效，期间所有工具与派生都直接执行，
  这里不会出现待批准项。
  - 关闭：边界配置写 `crew_yolo = false`（或环境变量 `OCTOPUS_CREW_YOLO=false`），重启该边界的 Gateway 后恢复逐项批准。
  - 在 Dashboard 中切回 Normal 会立即结束 YOLO（对该 Gateway 上的所有会话生效），到下次 Gateway 启动时按 `crew_yolo` 恢复。

### 发布到仓库（D35 第 2 期）

会话页「发布到仓库」或 MCP `octopus_publish`：会话结束后，把成果推送到源仓库的分支（默认 `octopus/<run-id>`），并创建或沿用
Draft PR（GitHub）/ Draft MR（GitLab）。

- **前提**：会话绑定一枚写权限（`access: write`）、仍可用于该会话的凭证。只有会话创建者、Team owner 或管理员可以发布。
- **Worker**：在临时索引中把工作区未提交的改动做成一个提交（作者 `KiroCrew ON`；不含 `.env` 文件或目录与依赖、缓存目录，即使未被
  `.gitignore` 忽略——这些目录也不会被读入 Git）。bundle 带克隆以来的全部提交，所以检查的是克隆以来新增的每个对象的原始内容
  （任何文件名与文件类型、二进制、提交说明）和新提交引入的每个路径：有疑似凭证（GitHub / GitLab Token、AWS Key、私钥）或提交了
  `.env` 时拒绝并列出文件（不回显内容），会话分支与工作区索引都不动；在已提交历史中的，需改写这些提交后再发布。检查通过后才把
  提交放到会话分支上，导出带完整历史的 bundle（≤ 100 MB）。写凭证不交给 Worker。
- **平台**：先按 Content-Length 拒绝超过 100 MB 的 bundle（413，不写盘）；在数据目录的临时裸仓库中核对 bundle（完整、基于会话
  克隆时的提交、正是 Worker 检查过的提交），只推送这一个分支：不 force，远端分支被他人改动时拒绝（409 `publish_conflict`）。
  推送与托管方 API 用同一套加固：不读全局 git 配置、只走 HTTPS、不跟随跳转。
  结果写入会话（`publish`）与变更记录 `run.published`；每次使用写凭证都审计 `credential.use`（`access: write`，`result`：
  `published` / `pushed` / 失败的错误码）。令牌能读不能推送时为 409 `credential_permission`（GitHub 细粒度令牌需 Contents 读写，GitLab 需 `write_repository`）。
  分支已推送而 PR / MR 未创建时（如令牌缺 Pull requests 权限，同为 409 `credential_permission`），
  会话记下分支与原因（`publish.request_error`），处理后再次发布即沿用该分支。

### 远程预览（D36 第 2 期）

会话页「远程预览」或 MCP `octopus_app`：在执行环境中运行会话的应用，经第三个 CloudFront 分发（`deploy.sh status` 的 `PreviewUrl`）
在浏览器中打开。

- **运行**：Worker 按 `.octopus/preview.json`（或自动识别 `package.json` / `index.html`）以执行环境的 OS 用户、在项目目录中启动。
  - 每个进程独立进程组，入口端口取 47000–47999。
  - 依赖安装只执行一次，日志写入会话目录的 `app.log`。
  - 每个执行环境最多 3 个；会话执行任务时不能启动。
  - 空闲 30 分钟、会话关闭 / 删除 / 续作 / 诊断时停止；Worker 重启（含每次部署）后预览随之结束。
- **访问**：浏览器 → CloudFront（源站密钥）→ 平台主机 nginx `:8929` → `auth_request` 问 API `/v1/preview-auth`（校验预览域
  Cookie、复核查看权限、记录最近访问）→ 平台主机回环端口，或经私网 TLS 到 Worker 主机 nginx `:8945` → 回环端口。
  - Worker 主机端口只接受平台安全组，并校验由该边界 Worker Token 派生的请求头（`HMAC-SHA256(token, "kirocrew-preview")`）。
  - 打开链接 10 分钟内有效、只能用一次，写入 8 小时的 `__Host-kirocrew_preview`。共用的预览域上打开时先清除旧 Cookie 与存储；
    会话自己的预览域（见下「会话独立预览域名」）不清除，应用保留自己的存储。
  - `/v1/preview-auth` 与 `/_kirocrew/*` 只接受本机 nginx 的请求。
- **部署**（一次）：
  1. `deploy.sh up`：新增预览分发；端口取现有安全组规则的最后一个，规则本身不变。
  2. `deploy.sh worker-host up <名称>`：Worker 主机加一条只对平台安全组开放的入站规则。
  3. `deploy.sh push` 与 `deploy.sh worker-host push <名称>`。
  - 安装脚本渲染 nginx 配置（`nginx -t` 不通过时移除并退出），平台 `platform.toml` 写入 `preview_url`，Worker 主机边界配置写入
    `preview_tls_port` / `preview_tls_name`。
  - 首次写入边界配置会让该主机的 Gateway 重启一次（指纹包含边界配置），部署前确认没有进行中的会话。
- **会话独立预览域名**（D36 第 3 期，`stack.env` 的 `PREVIEW_SESSION_DOMAIN`，现为 `preview.tinyboat.blog`）：
  - 每个会话的预览在 `https://<run-id>.preview.tinyboat.blog/`：同一浏览器可同时打开多个会话的预览，各应用读不到其他会话的
    Cookie、存储与缓存（但应用的 JavaScript 可以写 `Domain=preview.tinyboat.blog` 的 Cookie，其他会话的应用会收到；写给
    `.tinyboat.blog` 的大量 Cookie 也会让控制台的请求头过大而失败）。控制台与 MCP 取得的打开链接直接指向该会话的域名。
  - `deploy.sh up` 在 Route 53 找到托管区，在 us-east-1 找到 `*.preview.tinyboat.blog` 通配证书；没有时申请，把 DNS 验证记录写入
    托管区并等待签发（几分钟）。随后为预览分发加通配别名与 A / AAAA 别名记录。`push` 把 `preview_domain` 写入 `platform.toml`。
  - CloudFront 把访问的主机名交给源站，nginx 以 `X-Preview-Host` 转给 API：主机名指明会话，Cookie 与打开链接必须属于同一会话，
    其他会话的 Cookie 或链接得 403。预览分发的 cloudfront.net 域名保留为共用预览域。
  - 应用为上级域设置的 Cookie（`Domain=tinyboat.blog` 等）由 nginx 改回该会话的主机名。**剩余风险**：应用在页面里用 JavaScript
    设置的上级域 Cookie 不经过 nginx，会被浏览器发给 `tinyboat.blog` 的其他站点（如 `keycloak.tinyboat.blog`）。控制台的 Token
    存在 `sessionStorage`，不受影响。负责人 2026-10-14 选择沿用 `tinyboat.blog`；改用专用域名只需改 `PREVIEW_SESSION_DOMAIN`
    并重新 `up`、`push`。
- **同源防护**（共用的 cloudfront.net 预览域：所有会话的应用同源）：
  - nginx 拒绝 Service Worker 脚本请求（`Service-Worker` 头），所以应用无法注册 Service Worker。
  - 代理的响应一律为 `Cache-Control: no-cache`，并忽略应用发出的 `X-Accel-*`。
  - 打开链接时清除预览域的缓存、Cookie、存储与已打开的页面（`Clear-Site-Data`）。
  - 本机接口要求 nginx 带上由源站密钥派生的 `X-Preview-Gate`，因此平台主机上的其他进程（如个人空间的 Agent）拿不到上游
    与 Worker 主机的校验头。
  - Worker 重启后，旧预览的记录在该执行环境下一次启动预览时，以及每分钟回收时停止（按 Worker 进程实例判断）。
  - 剩余风险：在共用预览域上，浏览器中仍开着的旧预览页面，在打开另一个会话的预览后仍能以新 Cookie 访问预览域。会话独立预览域名
    消除了这一点（控制台与 MCP 默认使用它）。
  - 两种域名都拒绝 Service Worker：它会在预览停止、权限撤销后继续留在浏览器中。
- **限制**：不限 CPU / 内存；共用预览域上同一浏览器同时只预览一个会话；`/_kirocrew/` 路径保留给平台。

### 下载会话源码（D36）

控制台会话页「下载源码」或 MCP `octopus_download`：源码打包为 zip（含 `.git`，不含 `node_modules`、`.venv`、`target` 等依赖与缓存目录、
`.env`、符号链接；压缩前 200 MB 上限），经 10 分钟签名链接 `GET /archive/<票据>/<run>.zip` 流式下载，每次下载复核持有人仍能查看
该会话；包内 `OCTOPUS-ARCHIVE.txt` 写明 HEAD、未打包的内容与本机启动步骤（来自交付任务写的 `.octopus/preview.json`，或按
`package.json` 自动识别）。下载写入变更记录（`run.downloaded`），管理员下载他人会话写审计（`run.download`）。
- `.git` 可直接使用（含空目录）；`.git/config` 只保留格式、remote 与 branch 设置，不打包 hooks（`*.sample` 除外），打开下载的仓库
  不会运行会话中设置的命令（如 `core.fsmonitor`、别名、hooks）。
- 签发链接前先在 Worker 试算（`POST /archive/plan`）：超限（413 `archive_too_large`）、Worker 不可用（503）、Worker 未升级
  （409 `archive_unsupported`）直接作为错误返回，响应含 `bytes_estimate`。
- 读不到的文件（权限、打包中被删除、FIFO 等）跳过并列在说明文件中；其他打包错误使 zip 没有结尾记录，平台不完成下载、不写记录。

### 会话变更记录（D34）

会话的每一次写入都记录操作者（用户名与来源：控制台 / Coding Agent / API，或 Engine / Worker / KiroCrew / 协调者）、
字段前后值（长文本只记字符数与哈希）与原因。控制台会话页「变更记录」按人 / 系统筛选；API：
`GET /v1/runs/<id>/changes?who=user|system&kind=<前缀>&before=<id>&limit=<1–200>`；导出（`/export`）含 `changes`。
此前的事件按记录列出（命令归为人，其余标为「系统（旧记录）」）。

### 用户、Token 与管理员权限（访问管理第 1 期）

- **scope**：`read`（查看）、`execute`（会话操作）、`manage`（自己的配置：工作流、本人 Token 与密码、作为 Team owner 管理本 Team）、
  `admin`（平台管理：用户、Team 创建、执行环境、`/v1/admin/*`，以及查看与操作他人会话）。管理员网页登录自动带 `admin`；
  **已签发、不含 `admin` 的长期 Token（如 `claude MCP`）调管理接口返回 403**，查看与提交本人会话不受影响，需要时以管理员重新签发。
  **部署后管理员需重新登录一次**：此前的网页登录不含 `admin`，控制台顶部会提示「重新登录后才能使用管理功能」。
- **用户**：控制台「设置 → 用户管理」创建账户（可同时为个人空间分配共享环境）、停用 / 恢复、设为 / 取消管理员、重置密码、查看与吊销 Token；停用与重置立即吊销该用户全部 Token，
  会话照常执行；至少保留一位有效管理员，Team 唯一的有效 owner 不能被停用。账户菜单「修改密码」后，本人其他登录与 Token 失效。
  API：`POST /v1/me/password`、`POST /v1/users/<id>/password|active|admin`、`GET /v1/admin/users`、`GET|DELETE /v1/admin/users/<id>/tokens[/<tid>]`。
- **管理员越权**：可查看并操作全部会话（控制台侧栏「全部会话」，`GET /v1/runs?all=1`），原生 KiroCrew 入口不因管理员身份开放；
  越权操作与用户管理写入审计（`GET /v1/admin/audit`）。
- **错误语义**：看不到返回 404，看得到但不能做返回 403 并说明原因（例如普通成员删除他人创建的 Team 会话）。
- **预览链接** 10 分钟有效，每次取文件都核对持有人仍能查看该会话。

### 过期处理

现象：Attempt 失败类型为 `auth`，Session 进入等待并说明具体工具。
处理：`sudo -iu crew-personal` 重新登录对应工具；不需要重启平台，Session 在条件满足后继续（M2 起自动恢复）。
kiro-cli（D34）：Worker 每分钟以 `kiro-cli whoami` 检查登录，「执行环境 · Worker」显示「kiro-cli 登录已过期」；过期时使用 Kiro 的
任务（规划、kiro 任务、协调者诊断）不派发，会话保持原状态并显示「Worker 上 kiro-cli 登录已过期：请管理员执行 deploy.sh kiro-login」，
登录恢复后自动继续，不计为失败。运行时取消原生任务、Agent 的 ACP 进程退出（如 Gateway 重启，M2 故障注入）、或原生任务没有返回结果时，同一任务自动重试一次，不占纠错轮次。
不要把个人机器上的 HOME 或凭据目录复制到实例。

## 9．停止失败

原生任务仍在运行、排队或有待批准项时，Session 保持“停止中”，不伪造已停止。
检查：原生页面（经 15476 转发）中的批准请求与任务状态；处理批准或等待原生任务结束后，Engine 下一轮核对并完成停止。

## 10．磁盘

```bash
df -h /var/lib/octopus
sudo du -sh /var/lib/octopus/boundaries/personal/worker/* | sort -h | tail
```

只清理已关闭且已备份的 Session 的 Workspace；删除通过 Session 的受控删除操作完成，不直接删除目录。

## 11．队列异常

大量 Session 停留在同一状态时，按顺序检查：

1. Worker 健康：`… wait-ready --worker --timeout 5`（`--config /etc/kirocrew-on/boundary-personal.toml`）。
2. Gateway 就绪与组合：`… wait-ready --gateway --timeout 5`；失败信息含“未加载 PocProviderRegistry 组合”时，Gateway 不是经 composition 启动。
3. 路由日志是否持续写入；缺失时所有任务会被拦为“实际 ACP 后端未核实”（K16）。
4. Worker 不可用时 Session 保持原状态并按 2 s、4 s、8 s…（上限 60 s）退避重试，恢复后自动继续。

## 12．备份恢复

```bash
P="sudo -u octopus /opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml"
$P backup --dest /var/lib/octopus/platform/backups
sudo -u crew-personal /opt/kirocrew-on/venv/bin/python -m octopus archive /var/lib/octopus/boundaries/personal \
  --dest /var/lib/octopus/boundaries/personal/backups --name boundary-personal --exclude 'home/.cache/*' --exclude 'backups/*'
$P restore-verify /var/lib/octopus/platform/backups/octopus-<时间>.json --into /tmp/restore-<日期>
```

`restore-verify` 只接受空的独立目录，校验 sha256、完整性与各表行数。
未覆盖：各 Coding 工具私有会话目录的语义一致性、远端仓库状态、原生 Gateway 内存中的任务。

## 13．批准模式

无人值守的执行边界在 `boundary-<边界>.toml` 中设置 `approval_mode = "auto"`（D14）：Worker 只对平台派发的 spawn 请求原生
`approval_mode=auto`；`KIROCREW_HOME/config.json` 的全局工具批准保持 `interactive`，owner 与父会话的原生聊天仍需人工批准
（无人处理时 600 s 后被原生拒绝）。诊断使用不能派生子任务的 `poc-diagnose` 模板。升级 KiroCrew 后先用一个冒烟任务确认
spawn 未被拒绝（K19）。

## 14．本地端到端（macOS）

用于“先在本地跑通，再部署远端”。使用本仓库代码、独立端口与 `state/local-e2e/` 数据目录（含独立 `KIROCREW_HOME`、`KIRO_HOME`），不读写 `~/.kiro`。

```bash
npm ci                                  # ACP 适配器（node_modules/.bin）
tools/local_stack.sh start              # Gateway 6476、Worker 8938、API 8948，并逐个 wait-ready
tools/local_stack.sh status
tools/local_stack.sh restart worker     # 单独重启某个服务
tools/local_stack.sh stop               # 逆序 SIGTERM，只结束本栈的进程
```

日志在 `state/local-e2e/logs/`；控制台 `http://127.0.0.1:8948/`。

### 控制台（React）

`/` 为 React 控制台（`web/` 构建到 `octopus/ui/dist/`）；未构建时 `/` 回退到旧版。旧版控制台保留在 `/classic/`，两者共用登录。

```bash
npm --prefix web ci                     # 前端依赖（lockfile 固定版本）
npm --prefix web run build              # 构建到 octopus/ui/dist（主机安装时由 al2023.sh 执行）
npm --prefix web run dev                # 开发服务器 http://localhost:5173，代理 /v1 到 OCTOPUS_DEV_API（默认 8921 临时数据服务）
npm --prefix web test                   # 前端单元与组件测试（Vitest）
npm --prefix web run test:browser       # 浏览器回归：React 与 /classic/ 共 7 个文件，每个文件独立的 8921 临时数据服务
```

浏览器回归只使用 `tests/mvp_browser_fixture.py` 的临时 HTTP/SQLite 数据，不连接 Worker、不调用 Coding 工具。
