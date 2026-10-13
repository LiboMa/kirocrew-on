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
4. schema 迁移在 API 启动时执行，迁移前自动备份到 `/var/lib/octopus/platform/backups/octopus-v<旧版本>-<时间>.sqlite3`；结果见 `journalctl -u octopus-api`。

## 5．回滚

```bash
sudo ln -sfn /opt/kirocrew-on/releases/<旧版本> /opt/kirocrew-on/current
sudo systemctl restart octopus-api octopus-worker@personal octopus-gateway@personal
```

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

### 发布到仓库（D35 第 2 期）

会话页「发布到仓库」或 MCP `octopus_publish`：会话结束后，把成果推送到源仓库的分支（默认 `octopus/<run-id>`），并创建或沿用
Draft PR（GitHub）/ Draft MR（GitLab）。

- **前提**：会话绑定一枚写权限（`access: write`）、仍可用于该会话的凭证。只有会话创建者、Team owner 或管理员可以发布。
- **Worker**：把工作区未提交的改动提交到会话分支（作者 `KiroCrew ON`；不含 `.env` 与依赖、缓存目录，即使未被 `.gitignore` 忽略），
  新增内容中有疑似凭证（GitHub / GitLab Token、AWS Key、私钥）时拒绝并列出文件，然后导出带完整历史的 bundle（≤ 100 MB）。
  写凭证不交给 Worker。
- **平台**：在数据目录的临时裸仓库中核对 bundle（完整、基于会话克隆时的提交），只推送这一个分支：不 force，远端分支被他人改动时
  拒绝（409 `publish_conflict`）。推送与托管方 API 用同一套加固：不读全局 git 配置、只走 HTTPS、不跟随跳转。
  结果写入会话（`publish`）与变更记录 `run.published`，审计 `credential.use`（`access: write`）。

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
  - 打开链接 10 分钟内有效、只能用一次。打开时先清除预览域的旧 Cookie 与存储，再写入 8 小时的 `__Host-kirocrew_preview`。
  - `/v1/preview-auth` 与 `/_kirocrew/*` 只接受本机 nginx 的请求。
- **部署**（一次）：
  1. `deploy.sh up`：新增预览分发；端口取现有安全组规则的最后一个，规则本身不变。
  2. `deploy.sh worker-host up <名称>`：Worker 主机加一条只对平台安全组开放的入站规则。
  3. `deploy.sh push` 与 `deploy.sh worker-host push <名称>`。
  - 安装脚本渲染 nginx 配置（`nginx -t` 不通过时移除并退出），平台 `platform.toml` 写入 `preview_url`，Worker 主机边界配置写入
    `preview_tls_port` / `preview_tls_name`。
  - 首次写入边界配置会让该主机的 Gateway 重启一次（指纹包含边界配置），部署前确认没有进行中的会话。
- **同源防护**：所有会话的应用共用预览域。
  - nginx 拒绝 Service Worker 脚本请求（`Service-Worker` 头），所以应用无法注册 Service Worker。
  - 代理的响应一律为 `Cache-Control: no-cache`，并忽略应用发出的 `X-Accel-*`。
  - 打开链接时清除预览域的缓存、Cookie、存储与已打开的页面（`Clear-Site-Data`）。
  - 本机接口要求 nginx 带上由源站密钥派生的 `X-Preview-Gate`，因此平台主机上的其他进程（如个人空间的 Agent）拿不到上游
    与 Worker 主机的校验头。
  - Worker 重启后，旧预览的记录在该执行环境下一次启动预览时，以及每分钟回收时停止（按 Worker 进程实例判断）。
  - 剩余风险：浏览器中仍开着的旧预览页面，在打开另一个会话的预览后仍能以新 Cookie 访问预览域。只打开可信会话的预览，或预览
    完关闭页面；按会话分开的预览域（`<会话>.preview.<域名>`，需要泛域名证书）可以彻底消除，待定。
- **限制**：第一版不限 CPU / 内存；同一浏览器同时只预览一个会话；`/_kirocrew/` 路径保留给平台。

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
登录恢复后自动继续，不计为失败。运行时取消原生任务、或原生任务没有返回结果时，同一任务自动重试一次，不占纠错轮次。
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
