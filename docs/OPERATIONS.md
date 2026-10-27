# KiroCrew ON 运维手册

适用范围：M1 单机部署（EC2 Amazon Linux 2023 x86_64，一个个人执行边界 `personal`）与本机 macOS 端到端环境。
安装脚本自 MVP 0.2.0 P1 起也支持 aarch64（Graviton）：`al2023.sh` 按 `uname -m` 选 Node 与 kiro-cli 的构建（`deploy/install/arch-lib.sh`），其他架构在改动主机前停止；`deploy.sh up` 与 `worker-host up` 按机型的 CPU 架构（`describe-instance-types`）选 AL2023 AMI，已有主机保持原 AMI。arm64 主机的实机验收与 P0 一起做。
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

**登录会话（D67，负责人 2026-10-22）**：
- **Cookie**：控制台登录时，平台写入会话 Cookie `__Host-octopus_session`，属性为 HttpOnly、Secure、SameSite=Strict、Path=/。因为带 `__Host-` 前缀，预览子域名无法设置或覆盖它。
- **有效期**：
  - 勾选「保持登录（30 天）」（默认勾选）：Cookie 与它的 Token 都保持 30 天；
  - 不勾选：Token 1 天，Cookie 在浏览器关闭时结束。
  - 新标签页、重开的浏览器凭 Cookie 保持登录，两个控制台共用这次登录。
- **识别顺序**：
  - API 先看 `Authorization` 头（CLI、MCP、API Token）；没有这个头时才看 Cookie。
  - 只凭 Cookie 的写入必须带本站 `Origin`，否则 403 `origin`。
  - 只凭 Cookie 的请求，浏览器标明来自同站或跨站的子资源请求（`Sec-Fetch-Site` 为 `same-site` 或 `cross-site`，且不是顶层页面打开）一律 403 `origin`：会话预览 `*.preview.tinyboat.blog` 与控制台同站，`SameSite=Strict` 挡不住它们的图片、fetch、iframe 与事件流（2026-10-25）。控制台自己的请求、地址栏打开、从别处点链接打开不受影响；没有这个头的旧浏览器照旧；带 `Authorization` 头的 CLI、MCP 不受影响。
- **退出**：「退出登录」同时撤销 Token、清除 Cookie。
- **不接受 Cookie 的浏览器**（例如 Safari 经 `http://localhost` 隧道访问）：退回到只在当前标签页保存 Token。
- **命令行不变**：`octopus login`、bootstrap、connect 不传 `remember`，仍是 1 天的 Token。
- **API Token 页**：Web 登录列为「Web login」，可以在这里撤销。

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
每次执行都安装 Agent 的 Playwright Chromium 所需的系统库（libX11、mesa-libgbm、pango 等 16 个），审查与前端验证才能打开页面（2026-10-19 起）。

注册 Worker（管理员 token 登录后）：`POST /v1/workers`，body 为
`{"name":"personal","owner_id":"<用户 ID>","connection":{"url":"http://127.0.0.1:8928","token":"<worker.token 内容>"}}`。
平台用户 `octopus` 与执行用户 `crew-personal` 不同，Worker 可登记给普通用户。

**用一次性接入码登记（MVP 0.2.0 P1，N §3–§5）**：Token 不经人手，也不进命令历史。

- **签发**：控制台「管理 › 执行环境 › 登记 Worker」选「用接入码」，填名称、绑定空间（某人 / 某个 Team / 空闲专属环境）与有效期（30 分钟、2 小时、24 小时；
  API 为 `POST /v1/workers/enrollments {name, owner_id | team_id | spare, expires_in}`，60–86400 秒，默认 1800）。
  接入码（`kce_` 开头）与要运行的命令只显示这一次；库里只存 SHA-256。
- **登记**：在 Worker 主机上以 root 运行 `PYTHONPATH=/opt/kirocrew-on/current /opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/boundary-<边界名>.toml worker enroll --code-stdin --platform https://<控制台域名> --url https://<私网地址>:8943`（它要读 `worker.token`），
  粘贴接入码后回车（脚本用环境变量 `OCTOPUS_ENROLL_CODE`；`--code` 参数被拒绝，避免出现在 `ps` 与 shell 历史里；`--check` 只核对不登记）。
  Worker 读本边界的 `worker.token`，与地址一起交给 `POST /v1/workers/enroll`（不需登录）。平台照旧带 Token 调 `/health` 核实，
  按 `POST /v1/workers` 的全部规则校验，审计 `worker.enroll`（记签发人、接入码编号与绑定，不记地址与 Token）。
- **规则**：接入码只能用一次（同时使用也只有一个成功）；错误、过期、已用或已撤销的码一律 404 `enrollment_invalid`，不会去调 Worker；
  每个来源每分钟最多 10 次（429）。登记失败（Worker 不可达、规则拒绝）时码保持未用，可修正后重试；失败 5 次即作废，并记审计 `worker.enrollment_retired`。
  签发人被停用或不再是管理员时，他签的码立即失效。回环地址（`127.0.0.1` 等）的 Worker 只能由平台主机本机发起登记（`boundary add`），
  经控制台域名来的请求不能让平台去访问它自己的回环端口；平台对 Worker 的请求一律不跟随重定向，`/health` 响应最多 1 MB。
- **撤销**：执行环境页的「接入码」小节列出未用的码，可撤销（`DELETE /v1/workers/enrollments/<id>`）；`GET /v1/workers/enrollments` 不含码本身。
- **脚本**：`deploy.sh boundary add` 与 `worker-host register` 的**首次**登记改用接入码（`bootstrap-worker --issue-code` 签发，Worker 自己 `worker enroll`），
  `worker-host register` 不再经 SSM 读取 Token，SSM 命令历史里只剩一次性的接入码。已登记过的（重跑、换主机）仍由 `bootstrap-worker` 更新连接。

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
控制台「管理 › 执行环境」是统一管理入口：每个环境一行，显示实时状态（可达 / 不可达 / 已停用）、版本、Kiro 登录、可用工具、服务空间数与进行中会话，选中一行看详情（原生入口、地址、服务的空间）与操作，
并在此设为共享、分配给 Team 或用户、移出空间、停用或启用（下面 1、3、4 步也都可以在这里完成）。

1. **设为共享环境**：控制台「管理 › 执行环境」，选中专属 Worker 点「设为共享环境」并确认（环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围）。原所属的个人空间继续由它服务。
2. **创建 Team**（仅管理员）：「管理 › Team」点「新建 Team」，填写名称、Team owner，选择执行环境（共享环境，或已登记的空闲专属环境）。没有可选环境时无法创建。
3. **为已有 Team 分配或更换**：「管理 › Team」中选择 Team，管理员在「执行环境」一行选择共享或空闲环境后「分配执行环境」/「更换执行环境」（`POST /v1/teams/<id>/environment {"worker_id"}`）。未分配前，向该 Team 提交会被拒绝（`team_without_worker`）。
   更换时旧会话不迁移：原环境为共享时不再服务该 Team；原环境为该 Team 的专属 Worker 主机时改为空闲，**只能再分配回这个 Team**（主机上留有其源码、工作区与记忆），要给其他 Team 须先重新部署主机，执行边界变化后（`deploy.sh worker-host add`，或「更新连接」）才可分配。
4. **为用户分配**：「管理 › 用户」新建用户时选择「个人空间的执行环境」（默认第一个共享环境），或在用户行「⋯ → 改执行环境」。

**派发前核对**（访问管理第 2 期）：Engine 每次准备工作区或派发新任务前核对会话的 Worker 仍启用、未退役且仍服务该会话的空间；
否则会话进入「等待 Worker」，说明「执行环境已更换或已移出该空间 / 已停用 / 已退役」，已在执行的任务不中断。成员或管理员在会话页「分配 Worker」：
分配回原环境时直接继续；换到另一个环境时在新环境重新准备工作区，已完成的任务保留，原环境中的产出不迁移。续作同样核对；诊断在环境不服务时返回 409。

**Team 管理**：Team owner 或管理员可改名、管理成员与角色（管理员不必是成员）；成员可「退出 Team」（唯一的有效 owner 不能退出）；
管理员可归档 / 恢复：归档后不能提交新会话或添加成员，已有会话可查看与停止，执行环境保留；归档的 Team 在顶栏标「已归档」，
不出现在提交、共享与分配的选择器中。不提供删除 Team。

**Worker 管理**（管理员，「管理 › 执行环境」选中的那一行）：改名、「更新连接」（主机替换或 Token 轮换，新地址按登记规则重新核验）、
「退役」（仍是某 Team 的执行环境或有进行中的会话时拒绝；退役后不再出现在列表与选择器中，它服务的个人空间失去该环境，历史会话保留引用）。
该环境服务的全部用户（含共享环境上的个人与 Team 成员）都可以「刷新模型」（`POST /v1/workers/<id>/capabilities`，2026-10-25 起不再接受 GET；执行边界、主机或隔离变了返回 409 `worker_changed`，由管理员「更新连接」）。

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
deploy/aws/deploy.sh boundary native alice         # 对外开通它的原生入口（D49 第 3 期）：控制台「在 KiroCrew 中打开」可远程使用
```

- `boundary add` 在**当前发布**上开通：OS 用户 `crew-alice`、目录、工具环境、`/etc/kirocrew-on/boundary-alice.toml`（Gateway 5481–5580、
  Worker 9001–9100 中第一个空闲端口；`worker_isolation = "dedicated"`；`crew_url` 为本机回环，经 `tunnel --boundary` 打开）、资源上限
  （`octopus-gateway@alice.service.d/limits.conf`：`MemoryHigh` = ¾ × `MemoryMax`、`CPUQuota`、`TasksMax=4096`；Worker `MemoryMax=4G`，
  它也运行会话预览；两者 `OOMPolicy=continue`：超限时只结束那个进程，不停掉整个服务）、原生契约检查、启动两个服务，最后
  登记为 alice 的专属 Worker（MVP 0.2.0：首次用一次性接入码，Worker 自己 `worker enroll`；重跑时 `bootstrap-worker --owner alice` 更新连接）。**开通前先核对用户**（存在、尚无自己的执行环境；`--check-only`），不符时主机上什么也不留下。
  新边界的 OS 用户 ID 与身份由平台分配（MVP 0.2.0 P1，S9）：`bootstrap-worker --allocate` 经 `POST /v1/admin/boundaries`
  取得 `boundary_id` 与 UID = GID = 70000 + 序号（至多 99999，永不复用），`groupadd --system -g` / `useradd --system -u` 用它（系统账号不分从属 ID；这个 UID 若落在 `/etc/subuid`、`/etc/subgid` 某段内则停止），`boundary_id` 写在 toml 第一行。分配可重复：重跑时平台返回同一身份，上次停在写配置之前的，重跑会补上 `boundary_id`；
  本机该 UID 已被占用时停止并说明。此前建的边界保留主机分配的 UID，P5 搬到用户卷时一次性改属主。
  不重启平台 API；重复执行只补缺（端口与配置不变；未给 `--memory` / `--cpu` 时资源上限沿用现有值）。`--owner` 按小写匹配平台用户。主机须已 `push` 过含 `boundary.sh` 的发布。登记后此人的会话即落到新边界：先完成 `kiro-login --boundary`。
- `boundary native <名称>`（D49 第 3 期，2026-10-21）：建栈 `<平台栈>-native-<名称>`（`kirocrew-on-native.yaml`，只有一个 CloudFront 分发，带源站密钥），中转端口与 Worker 主机共用 `NativePort + 1`–`NativeRelayEnd`（安全组只放行 CloudFront）；在平台主机渲染
  `/etc/nginx/conf.d/kirocrew-relay-boundary-<名称>.conf`（只转发带源站密钥的请求到该边界的回环 Gateway），把边界的 `crew_url` 改为该分发，排空后重启它的 Gateway 与 Worker（Gateway 只信任 `crew_url` 这个来源）。
  之后的 `push` 保留它（只改主边界的 `crew_url`）；重复执行沿用端口。未开通时使用者点「在 KiroCrew 中打开」看到「入口还没有对外开通」，会话照常执行。
- 之后每次 `deploy.sh push`：`al2023.sh` 对主机上**全部** `boundary-*.toml` 做原生契约检查，全部通过才切换 `current`；重启顺序为 personal →
  平台 API → 其余边界逐个排空、重启（一个边界未能重启时其余照常，命令最后以非零退出并列出）。SSM 时限 3 小时（每个边界排空至多 `DRAIN_MINUTES`）。
- 同一主机的会话预览共用回环端口 47000–47999：就绪检查核对监听者是本边界的用户（`/proc/net/tcp`），否则判失败、重新启动即换端口。
- 停用一个边界：`systemctl disable --now octopus-gateway@alice octopus-worker@alice`——之后的 `push` 不检查、不重启它。移除（手工，root）：先停用；
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
    `?run_id=`（该会话可分配的环境：按**会话创建者**回答——其他成员查询时也列出创建者的边界名称与模型，`own` 指「创建者的」）；已归档 Team 的新会话位置返回 409 `team_archived`；
    控制台向导默认「自动：将在 X 执行」，MCP `octopus_environments` 标出 `default`。会话的 `placement`
    记于视图、精简状态与时间线 `run.created`。
  - Team 成员看得到会话的状态、报告、预览与下载；原生 KiroCrew 入口只开放给边界主人（`permissions.native_reason` 说明原因）。
  - 在某人边界上的会话只有边界主人下达指令（R10）：续作、诊断、确认计划、补充说明与答复批准请求对其他成员与管理员返回 403
    `boundary_owner_only`（控制台停用并写明原因，`permissions.direct_reason`）；他们可以查看、停止、关闭、发布与确认验收。把等待执行环境的
    会话分配到某人的边界也只接受此人（其他人分配 Team 的环境）。停下的会话不改派（新环境会重新克隆，已完成任务的改动留在原边界）：
    由创建者续作，或由能续作它的人「接手」到自己的边界（D74，见「接手共享来的会话」）；创建者离开 Team 或边界停用后会话进入等待执行环境，
    再分配 Team 的环境。
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
| `worker-host up <名称>` | 栈 `<平台栈>-worker-<名称>`（机型 `WORKER_INSTANCE_TYPE`；AMI：`WORKER_IMAGE_ID`，否则沿用栈自己的，否则与平台同架构时用平台的，否则取该架构最新的 AL2023，如 Graviton 机型）：实例、安全组（仅平台安全组可访问 8943 / 8944）、该主机的原生入口 CloudFront 分发（源站 = 平台主机的中转端口，注入同一个源站密钥）。中转端口在平台的 `NativePort + 1`–`NATIVE_RELAY_END`（`stack.env`，默认 8920–8929）中分配，已有栈保持原端口 |
| `worker-host push <名称>` | 安装已提交的 HEAD（Worker 主机模式）；写入 `crew_url = https://<该主机的分发域名>` 与 nginx 配置 |
| `worker-host tls <名称>` | 私钥留在 Worker 主机，平台私有 CA 签发证书（SAN：私网 IP 与 `<名称>.worker.kirocrew-on`）。旧证书没有 DNS 名称时重跑本步（私钥不变） |
| `worker-host native <名称>` | 在平台主机写入 `/etc/nginx/conf.d/kirocrew-relay-<名称>.conf`（0640 root:nginx，密钥只从主机上的文件读取），`nginx -t` 失败即撤回；最后经中转实测一次 |
| `worker-host register <名称>` | 登记为空闲执行环境：平台主机签发一次性接入码，Worker 主机经控制台域名 `worker enroll` 自行登记（MVP 0.2.0，Token 不经 SSM）；已登记时更新连接（重新核验健康与隔离） |
| `kiro-login --host <名称>` | `crew-<名称>` 的 kiro-cli 设备码登录；看到 “Logged in successfully” 即结束本次登录，以 `kiro-cli whoami` 为准 |

原生入口链路：浏览器 → 该主机的 CloudFront 分发 → 平台主机 nginx `:<中转端口>`（校验 `X-Origin-Verify`，规则与 8919 相同）
→ 私网 TLS（只信任平台私有 CA，校验 `<名称>.worker.kirocrew-on`）→ Worker 主机 nginx `:8944` → 回环 Gateway `:5476`。
Worker 主机本身不对公网开放。控制台提示“该执行环境尚未开通原生 KiroCrew 入口”时，执行 `worker-host add`（或依次
`push`——写入 `crew_url`——与 `native`）。中转的访问与错误日志在 `/var/log/nginx/kirocrew-relay/`（仅 root；
错误日志会记下含一次性 `?token=` 的请求行）。两台主机分到同一中转端口时 `native` 拒绝写入：对后建的一台重新 `up` 换端口。
更换源站密钥后，每台 Worker 主机都要重新执行 `up`（分发注入的密钥）与 `native`（平台中转校验的密钥）。

登记后在控制台「管理 › 执行环境」把它分配给 Team（见上一节）；`worker-host status <名称>` 查看栈、SSM 与 TLS 连通性。

### 原生 KiroCrew 对话（第二个 CloudFront 分发）

控制台“在 KiroCrew 中打开”经 `https://<NativeDomain>/`（栈输出 `NativeUrl`）打开：CloudFront → 安全组（仅 CloudFront
前缀列表，端口 `NativePort`，默认 8919）→ 主机 nginx（`/etc/nginx/conf.d/kirocrew-native.conf`，只转发带
`X-Origin-Verify` 的请求，支持 WebSocket）→ 回环 Gateway `127.0.0.1:5476`。Gateway 仍然只监听回环，并保留自己的
token 登录、Host 与 CSRF 检查；公网来源经 `KIROCREW_CORS_ORIGINS` 被信任（由 `crew_url` 推导）。
入口登录有效期 `crew_entry_ttl = "1h"`（K03：该登录覆盖整个 Gateway；过期后在控制台重新点击打开）。
Gateway 在发访问 Cookie 的同时还发一条 30 天的刷新凭据（`mc_refresh_<端口>`，2026-10-26 在 EC2 实测确认），原生页面会用
`/api/auth/refresh` 把它换成新的登录；所有公网中转（`kirocrew-native.conf`、`kirocrew-relay-*.conf`）都包含
`/etc/nginx/kirocrew-native-guard.conf`，把这个请求答成「没有刷新凭据」（401 `no_refresh_cookie`），登录因此在访问 Cookie 到期时结束，
原生页面显示登录已过期的提示。不能答成「刷新链已吊销」：原生页面在访问 Cookie 到期前 1 小时就刷新（1 小时的 Cookie 等于一打开就刷新），
收到「已吊销」会跳回首页重新加载，每 7–8 秒一次（2026-10-26 实际发生，当天改正）。`deploy.sh push` 安装这个片段，并给此前渲染的中转补上 include（nginx 校验不过就还原并警告）。
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

### 会话列表（M3-3 / M3-5，2026-10-21；D56 过滤、全文与分页）

- `GET /v1/runs` 可带 `status`（会话状态）、`q`（会话 ID、名称或需求中的片段，1–200 字符）与 `limit`（1–200），在服务端过滤，最新的在前；不带参数时与之前相同（旧版 `/classic/`）。
  `view=list` 只给列表要的：任务的 ID、状态、名称、工具、类别与角色，规划不带说明文字（摘要、补全的需求、重新规划的说明与原因），需求只留前 200 字；不返回、也不计算用量、知识、凭证可用性与核验（2026-10-24，会话页 `GET /v1/runs/<id>` 照旧完整）。React 控制台的会话列表（每 10 秒）与 MCP 用它——EC2 上任务说明曾占完整列表的三分之二，瘦身后 50 行约 119 KB（gzip 15 KB）。
- MCP `octopus_runs` 返回精简列表 `{items, more}`：默认最近 20 个（`limit` 至多 50），每个只有 ID、名称、状态、时间、进度、需要处理的事（`attention` 标题）、错误首行、执行位置与 Worker；EC2 上 101 个会话的完整列表约 55 万字符，超出了 MCP 结果的上限。
- D56 过滤（可组合）：`status` 可逗号分隔多个；`lifecycle`（open / closed）；`space`（personal 或 Team ID）；`repo`（`主机/所有者/仓库` 的片段，https、ssh、`git@host:path` 同一结果，新建项目不命中）；`branch`（源码 ref 或发布分支）；`stage`（第一个未验收任务的类别，`plan` 为规划，`done` 为全部验收）；`tool`；`client`（Request 的来源客户端，D55 之前的会话没有）；`submitter`（用户名或 ID）；`since` / `until`（Unix 秒或 `YYYY-MM-DD`，UTC）。`q` 为全文：会话 ID、名称、需求、规划的意图与补全、各任务名称与说明、各任务最近一次结论的摘要与错误、来源客户端的名称；多个词须全部命中。已删除的会话从不列出。
- `page=1` 返回 `{items, next, total}`，把 `next` 作为 `cursor` 取下一页（`limit` 默认 50）；游标按（创建时间, ID），翻页时新建的会话不重复不遗漏。不带 `page` 时照旧返回数组。实现是逐行核对访问权限的扫描，几百个会话为毫秒级；会话数上千后再建索引（BACKLOG）。
- 控制台侧栏：搜索停顿 300 ms 后查询；状态分组、「筛选」面板（仓库、分支、阶段、工具、客户端、提交者、创建时间）与可移除的标签写进地址栏，可分享；「加载更多」翻页；数字取服务端总数。列表按创建时间排序（不再按最近更新）。
- 代码改动（D56）：`GET /v1/runs/<id>/attempts/<attempt>/diff` 经会话的 Worker 读取候选相对上一个候选（或会话基线）的改动——每个文件的增删行数与统一 diff，200 KB / 4000 行截断；控制台在每次 Attempt 的「代码改动」中显示。没有候选 404 `no_candidate`；凭证检查挡在分支外的候选 409 `candidate_secret`，从不显示；Worker 早于 D56 时 409 `worker_outdated`。

### 使用者看到的提示与管理员的处理（D53）

使用者的提示（错误、等待原因、MCP 工具说明、Skill）只写原因与「请平台管理员……」，不给运维命令；对应的处理在这里。

| 使用者看到 | 代码 | 管理员的处理 |
|---|---|---|
| 你还没有自己的执行边界 / 你的执行边界目前已停用（会话在 Team 的环境执行） | `placement.hint` | `deploy.sh boundary add <名称> --owner <用户名>`；已停用的在控制台重新启用（不要再建第二个） |
| 等待 Kiro 登录恢复 / Worker 上 kiro-cli 登录已过期 | `tool_auth_expired` | `deploy.sh kiro-login --boundary <边界>`（Worker 主机用 `--host <名称>`）；边界主人用自己的 IdC 账号在浏览器授权设备码（15 分钟内有效） |
| 这个执行环境的 Worker 版本较旧，还不能显示代码改动 | `worker_outdated` | 该环境的主机执行 `deploy.sh push`（Worker 主机为 `deploy.sh worker-host add <名称>` 重装），Worker 随平台同一版本 |
| 该执行环境尚未开通原生 KiroCrew 入口 | `crew_origin_required` | `deploy.sh worker-host add <名称>`（或依次 `push`、`native`；见「Worker 主机」） |
| 这个执行环境的原生 KiroCrew 入口还没有对外开通（控制台） | 浏览器端判断：`crew_url` 为本机回环 | 平台主机上的边界：`deploy.sh boundary native <名称>`；临时用 `deploy.sh tunnel --boundary <名称>` 在本机打开 |
| 此执行环境的 Worker 版本尚不支持原生会话入口 / 工作流建议 | `worker_upgrade_required` | 更新该 Worker：`deploy.sh push`（平台主机）或 `deploy.sh worker-host push <名称>` |
| 此 Worker 主机尚未配置预览端口 | `app_unreachable` | `deploy.sh worker-host push <名称>` |
| 未找到可预览的 index.html | `no_preview` | 无需处理；管理员要在本机运行开发服务器时用 `deploy.sh space <run-id>`（会话页只对管理员显示这条命令） |

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

**平台指标（MVP 0.2.0 P1，设计 §12.1）**：API 每分钟向 `/var/log/kirocrew-on/metrics.emf.log` 追加一行 CloudWatch EMF（命名空间 `KiroCrewOn`，维度 `Service=kirocrew-on`，单位 Count）。

- 指标：`sessions_running`、`sessions_waiting_worker`、`sessions_quota_wait`、`attempts_running`、`workers_enabled`、`workers_unhealthy`（健康信息 15 分钟以上未更新）、`apps_running`、`takeovers_moving`、`public_shares_active`。
- 文件超过 10 MB 时改名为 `.1`，只保留一代。
- 管理员可以读 `GET /v1/admin/metrics`，内容相同。
- 由主机上的 CloudWatch Agent 收这个文件；平台进程不调 AWS API，主机角色因此不需要 `PutMetricData`。告警（SNS）随 P3 的数据面栈一起做。

## 8．认证与过期

- Claude Code 与 Codex 经实例角色使用 Bedrock：实例角色需要 `bedrock:InvokeModel*`（Claude）与 `bedrock-mantle:CreateInference`（Codex 的 amazon-bedrock 提供方）。
- **在控制台登录 Kiro（MVP 0.2.0，设计 §6.4）**：执行环境的主人在「我的设置 › 执行环境」该行点「登录 Kiro」，对话框显示验证地址与设备码，在浏览器完成授权后自动确认（设备码 15 分钟内有效）。
  - Worker 本就以边界用户运行，自己在一个伪终端里执行 `kiro-cli login --use-device-flow`，不需要 root；同一时间每个边界只有一个登录，再点一次显示同一个码；已登录时直接返回。
  - 只有该执行环境的主人（`workers.owner_id`）能发起和看到设备码，管理员也不行；地址与设备码只在内存里，不写审计和日志，审计 `worker.kiro_login` 只记发起。
  - 接口：`POST` / `GET /v1/workers/<id>/kiro-sign-in`（Worker 侧 `/kiro/login`、`/kiro/login/status`）。
  - 登录方式取自边界配置的 `kiro_license`、`kiro_identity_provider`、`kiro_idc_region`；`deploy.sh push` 与 `worker-host push` 把 `stack.env` 的 `KIRO_LICENSE`、`KIRO_IDC_START_URL`、`KIRO_IDC_REGION` 写进主机上每个边界的配置；`boundary add` 新建的边界沿用同机其他边界的这三项。
  - 下面的 `deploy.sh kiro-login` 仍然可用。
- Kiro CLI：`deploy/aws/deploy.sh kiro-login` 经 SSM Run Command 在主机上以 crew-personal 运行 `kiro-cli login --use-device-flow`（带 `stack.env` 中的 `KIRO_LICENSE`、`KIRO_IDC_START_URL`、`KIRO_IDC_REGION`）。kiro-cli 即使给齐参数仍有交互提示、无终端时失败，因此在 `script(1)` 伪终端中运行并自动回车；脚本打印验证地址与设备码，在浏览器完成授权后自动确认（设备码 15 分钟内有效，过期重新执行）。日志：`/var/lib/octopus/boundaries/personal/logs/kiro-login.log`。不需要本机安装 session-manager-plugin。

### 控制台自定义域名

`deploy/aws/stack.env` 的 `CONSOLE_DOMAIN`（如 `kirocrew-on.tinyboat.blog`）为控制台分发加上自定义域名；cloudfront.net 域名继续可用。
`deploy.sh up` 在 Route 53 中找到包含该名称的公有托管区，并在 us-east-1 找到覆盖它的已签发 ACM 证书（同名或 `*.上级域名`；
CloudFront 只使用 us-east-1 的证书）：分发加上别名与证书（SNI、TLS 1.2 以上），托管区写入 A / AAAA 别名记录。找不到托管区或
证书时 `up` 在任何变更前停止。之后执行 `deploy.sh push`，把该域名加入 `platform.toml` 的 `allowed_hosts`（Host 校验）。
托管区与证书 ID 不写入仓库，每次 `up` 时查询。浏览器中的登录按域名分开保存：改用新域名后需要重新登录；MCP 的 `--url`
可以继续使用 cloudfront.net 地址，也可以换成新域名。清空 `CONSOLE_DOMAIN` 后再执行 `up` 会移除别名与 DNS 记录。

### 代码仓库凭证（D35）

控制台「我的设置 › 代码仓库凭证」（管理员也在「管理 › 接入与凭证」）或 API `/v1/credentials`：GitHub fine-grained Token 与 GitLab Token（含自建实例），用于克隆私有仓库。

- **作用域**：
  - 个人：本人管理与使用，可用于本人提交的 Team 会话。
  - Team：Team owner 与管理员管理，该 Team 的会话使用。
  - 平台：管理员管理，并授权给 Team 或用户（`POST|DELETE /v1/credentials/<id>/grants`）。
  - 看不到为 404，不能管理为 403。
- **秘密只写**：创建或轮换时提交，之后只返回元数据与末 4 位，审计 `credential.*` 不含秘密。MCP 不创建凭证。
- **Worker Token（D59，K10）**：各执行环境的访问 Token 也用这把主密钥密封（`workers.config.token_sealed`），数据库里不再有明文；平台只在调用 Worker 时解开（每个进程解开一次后留在内存）。升级到 D59 的那次启动会把已有的明文一次性密封（可重复执行）。D59 之前的数据库备份（每日备份与迁移前备份）仍含明文：确认新版本运行正常后删除它们，或重新注册各执行环境以轮换 Token。主密钥丢失、不可读或被换时平台照常启动，但调用使用密封 Token 的 Worker 返回 503 `worker_secret`：放回原密钥后重启平台；原密钥找不回时，在各 Worker 主机上重新注册（`deploy.sh worker-host register <名称>`，本机边界 `octopus bootstrap-worker`），平台用新密钥重新密封；代码仓库凭证需逐个轮换。
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
  - `implement` / `deliver`（与 D65 的 `research` / `write`）没有改动时 `diff_present` 记提示（D57；此前为失败）；`test` 与未标类别的任务没有改动时记为跳过。
- **检查命令**（`checks_pass`）：
  - Worker 在会话的项目目录中以执行用户运行任务的 `verify`：`bash -lc`，`PATH` 同远程预览，不带 Worker 自己的 `PYTHONPATH`；
    项目、`-c` 与执行用户自己的 git 配置都没有身份时，作者与提交者默认为 `KiroCrew ON <kirocrew-on@localhost>`（D57；
    经 `GIT_CONFIG_GLOBAL` 指向的临时文件给出，文件再引入用户自己的全局配置，任务结束即删除）。
  - 失败的命令另记 `unrunnable`：因环境无法执行的原因（命令不存在、缺少模块、没有 git 身份等），否则为空；缺的模块
    在项目根目录或 `src/` 下就有、或调用的脚本路径（`./run_tests.sh`）不存在时，算项目自己的错误。
  - 顺序执行，前面的失败不挡后面的命令，每条都运行（D57；会话基线上的运行仍在第一条失败时停下）；每条 10 分钟、合计
    20 分钟，超时结束整个进程组，合计时限用完后余下的命令记为跳过。命令留在后台的进程（如为检查而启动的服务）
    在命令结束时一并结束。
  - 每条记录退出码、耗时与输出末尾 4 KB（隐去凭证）。
  - 运行期间 Attempt 仍为 `running`，控制台显示「正在运行检查命令」。停止、关闭或删除会话时结束；Worker 重启后重新运行一次。
- **审查**（`review_bound`）：审查任务派发时带上会话分支当前所在的候选（按时间，而非任务顺序）；`outcome.json` 应写
  `reviewed_commit`，审查结束时 HEAD 不变、已跟踪的文件（排除的除外）没有改动。检查与测试留下的未跟踪文件（`.coverage`、
  `reports/`、`dist/`）不算改动。D57 起审查改了文件时由平台撤回、新建的文件移出；`reviewed_commit` 缺失或与候选不符也只记提示
  （见「门禁与安全底线（D57）」）。
- **失败与恢复**：任何门禁记为失败即 Attempt `blocked`（`failure_type: gate`），照常进入自动纠错，协调者看到门禁详情与命令输出；记为提示（D57）的不拦。
  Worker 拒绝生成候选或运行检查（如 Agent 切换了分支）也按门禁失败处理。
- **发布**：只推送已验证的候选，即会话分支当前所在、自身门禁全部通过的候选；会话有审查任务时，还须在最近一次代码改动之后
  有一次通过的审查（审查之后只有交付类任务的改动时仍可发布）。否则，或之后已跟踪的文件有改动时，409 `publish_unverified`，
  请续作会话。
- **兼容**：已有会话与旧 Worker（对新接口回答 404）行为不变：新门禁记为跳过。
- **第 3 期**（D57 起这两条都只记提示，下文说的「失败」即记提示）：
  - `artifacts_locked`（声明了 `paths` 的任务）：候选记录从任务起点以来改动的文件名（重试中撤回的改动不算）；此前已验收任务交付的文件，
    只能由 `paths` 覆盖它们的任务修改；平台检查命令留下的文件（`.coverage`、报告）不算交付物。
    `paths` 支持目录（`src/`、`src/**`）、glob（`src/**/*.ts`、`**/*.py`）与整个项目（`.`）。未声明 `paths` 的任务（模板）跳过。
    失败记录带被拒的文件（`files`），协调者在 `context.locked_files` 中看到；这些修改确应由失败的任务完成时，协调者可在重试 / 返工**失败的那个任务**
    的决定中加 `paths_add`（只取 `locked_files` 中的文件，至多 10 个，合并后至多 20 项，D54，负责人 2026-10-21）。平台核对后扩大该任务的 `paths`，
    记在这次自动纠错的修订里（「扩大「X」的可改动路径：…」，修订对比可见）与时间线 `task.paths_added`；不符时整份决定被拒并写明原因。
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
- **保留任务的检查命令一并复核**（负责人 2026-10-21，D54 §9 a）：提示列出保留的已验收任务的 `verify`；规划者发现会因会话后来的变化而误判的检查
  （如扫描到后来建的 `.venv`、用了没有的命令）时，在计划顶层写 `verify_fixes: [{task_id, verify}]`。平台只接受指向保留任务、且不把 implement / test 任务的检查清空的修正，写入该任务（不重跑）并记在这次重新规划的修订里（修订对比可见 `verify` 前后值），
  时间线 `task.verify_revised`，计划说明「保留任务「X」的检查命令由重新规划修正」；不符的修正忽略并写明。只修正检查范围与可执行性，不放宽它要证明的事。
- **检查命令可用的命令**：Worker `/health` 的 `commands`（常见命令中 `available` / `missing`，按检查命令的搜索路径），
  写入首次规划、重新规划与协调者的上下文，`verify` 只用可用的命令（EC2 两台主机只有 `python3`，没有 `python`、`node`、`npm`、`pip`）。
  与 `tools` 相同，由平台的健康检查保存（D43，最多 10 分钟）。检查命令以退出码 127 失败时，协调者按「verify 写错」处理：
  未重新规划过用 `replan`，否则 `wait_for_user`（`missing: input`），由用户续作时修改该任务的 `verify`。
- 每个会话至多一次，消耗一轮纠错；工作流设计会话与规划任务本身不重新规划。
- 可见：修订「自动纠错：重新规划，替换「…」」与「重新规划：追加 N 个任务」；任务来源「KiroCrew 重新规划」；控制台纠错记录「已安排重新规划」。

### 需求中指定的工具与模型（D45）

- KiroCrew 规划时识别需求中点名的工具或模型（如「审查用 codex」），任务带 `requested`（工具、模型与需求原文片段）；映射来源显示「需求中指定：「…」」。
- 平台照指定执行：规划者改了工具会被改回，指定的模型未公布时不改为 `auto`（规划说明中写明）。
- 指定的工具不可用或模型未公布：派发前拒绝，会话停下并写明「需求指定的 …」，不交给协调者、也不换用其他工具。
  修复执行环境（安装、登录该工具或公布该模型）后续作，或在「配置并续作」中改用可用的工具或模型——用户的新选择取代需求中的指定。
- Worker 因平台记录过期而拒绝指定的模型时同样如实停下；协调者不能换用指定的工具或模型。指定的工具不可用时，规划说明中会预先写明。
- 只有 KiroCrew 规划的任务带 `requested`：显式提交的 steps、模板、导入的文档与保存的工作流中的 `requested` 一律去掉，
  其工具不可用时仍按 D38 由协调者推荐换用。规划者写法不完整的 `requested`（空值、缺工具与模型）被忽略并记说明，不使计划失败。

### 规划完成后直接开始（D70，负责人 2026-10-22，取代 D44 的计划确认与 D55 的准入规则）

负责人原话：「计划完成后，不要找我确认……把这个垃圾的过程删掉！直接开始！」

- 会话规划完成后直接执行，没有「计划待确认」。
- 以下设置都不再让会话停下：
  - 提交时的 `admission`：旧客户端仍可以传，取值照旧校验，但不再使用；
  - 个人 / Team 的准入规则：已写的仍列在设置里，标为「已取消，不再生效」，可以删除；新建时返回 400 `admission_removed`；
  - 平台配置 `default_admission`：已删除，写了会使配置校验失败。
- 已删除的入口：
  - `POST /v1/runs/<id>/confirm`；
  - MCP `octopus_confirm`，以及 `octopus_start` 的 `admission` 参数；
  - CLI `octopus confirm` 与 `submit --admission`；
  - 控制台的「确认并开始」与准入规则表单。
- 升级前已停在「计划待确认」的会话：引擎的下一轮把它记为由平台确认（快照 `confirmed.via = "platform"`，时间线「已确认计划」），并直接开始。
- 验收：Evaluator 判为「待人确认」的项一律记为提示（D71，`default_acceptance` 已删除）。

### 一般任务：不需要代码仓库（D65，负责人 2026-10-22「任务类型并不和 Git 做强绑定」）

设计稿：`docs/superpowers/specs/2026-10-22-general-tasks-design.md`。

- **来源**：`source` 写 `{"type": "none"}`。入口有三处：
  - 控制台向导「代码来源 → 不需要代码仓库」；
  - `octopus submit --no-repo`；
  - MCP `octopus_start`。
- **工作目录**：Worker 收到的是 `new`，旧 Worker 也能执行，准备一个空的工作目录。会话页不显示发布，按钮写「下载产出」；发布返回 409 `no_repository`。
- **类别**：`research`（调研）、`write`（撰写），门禁与 `deliver` 同档：
  - 没写文件只提示；
  - 秘密照旧阻断；
  - 只有写了 `paths` / `verify` 才检查改动范围、运行检查。
  - D69：`research` 推断为发散阶段，自身的失败只记提示。
- **措辞**：
  - 平台对没有仓库的会话与 research / write 任务在派发中写 `work: "general"`，Worker 据此写「本次任务」「工作目录」以及对应的结尾和交接要求。研发任务的指令逐字不变，旧 Worker 忽略该字段。
  - 没有仓库时，规划提示多一节「本会话没有代码仓库」。
  - 没有仓库时，规划提示也不提「源码检出」：原则一节写「所有任务在同一个工作目录中完成」，一般任务的契约写「不写 role」，因为 research / write 没有对应的角色卡片。
- **EC2 验收**（2026-10-23，bc651cc，run-656be0a4）：Agent 规划、不需要仓库，从提交到完成约 3 分钟，中间没有停下：
  - 计划为 1 个 write 任务；
  - 指令以「本次任务」开头，并写明工作目录；
  - 产出 compare.md，标题加 3 段正文。
  - 当时的规划提示仍写着「源码已由平台准备在…源码检出」，计划给写作任务配了 sde，平台忽略并记提示；上一条已修。
- **Evaluator**：只在有 implement / deliver 的会话追加，一般任务的验收由计划中的 review 承担。

### 在 KiroCrew 聊天里直接对话（D73，负责人 2026-10-23）

设计稿：`docs/superpowers/specs/2026-10-23-chat-and-structured-iteration-design.md`。

- **怎么用**：会话页「在 KiroCrew 中打开」进入父会话，直接对它说。可以问进展，也可以让它改工作区，或派 `poc-kiro` / `poc-claude` / `poc-codex` 子 Agent。
  聊天里已有平台的建立说明（含工作区路径），以及每个任务的开始与结果说明，它从这些历史里找。
- **谁在写**：
  - 平台开始执行一个任务时，聊天里出现一行「KiroCrew ON 开始执行：<任务>（<工具>，Attempt …）」；结果说明出现之前，Agent 不动手。
  - 这条说明由 Worker 在派发成功后写入（`note`，`source: octopus-start`，不触发模型），同一个 Attempt 只写一次。
- **回到平台**：在控制台续作。如果工作区里有不在候选提交里的改动（聊天改的），平台在续作前先把它们提交成新的候选：
  - 记为最后一个写代码任务上的一次「对话中的改动」尝试，照常做秘密检查；
  - 这个任务没被续作重置、又有检查命令时，先跑检查命令；
  - 它之后的任务重新执行，审查针对新的候选；
  - 修订摘要以「对话中的改动：N 个文件」开头，事件 `run.chat_changes`。
- **为什么要先提交**：否则下一个审查结束时的「放回」会把这些文件还原（`sources.rewind`），下一个实现任务又会把它们算成自己的改动。
- **拒绝的情况**：
  - 对话中的改动里有疑似凭证或 `.env`：续作返回 `409 chat_secret`，分支不动，列出文件；
  - 工作区里有尚未提交的对话改动时请求诊断：`409 chat_changes`，请改用续作。
- **认出哪些改动**：已跟踪文件的改动、候选之后的提交，以及新建的文件，即 Worker `/candidate` `check` 带 `untracked` 时返回的「平台最后一次查看之后新出现的未跟踪文件」。
  - Worker 在每次查看时，把当时的全部未跟踪文件原子地写进 `run/untracked-seen.json`：审查开始、每次候选提交、每个检查或基线任务被确认结束；
  - 所以平台自己的检查留下的 `.coverage`、`reports/` 等不会被算成聊天里的改动；
  - 每个审查自己的 `untracked.json`（D57 §4 放回用的基线）保持原样。
- **续作对话框的提醒**：`GET /v1/runs/<id>/chat-changes` 返回 `{files, count, commits}`，只看不改；有改动时，对话框在需求下面列出文件，提醒同步修改需求，不拦截（负责人 2026-10-23）。
- **用法要点**：聊天里改了行为，续作时要把需求改成新的；否则审查按原需求判定，返工会把改动改回去。父会话 Agent 的总结会对照需求原文（最近一次尝试的 `instructions.md` 开头）写出「需求有变化」。
- **EC2 验证**（2026-10-23，3af7331，run-588bc3d5）：问进展、改代码、派 `poc-claude` 写 README、总结都在聊天里完成；续作把 3 个文件提交为 07fdc05，审查按原需求判为需要修改，返工改回，复审与 Evaluator 通过。报告：`docs/reports/2026-10-23-d73-chat-collaboration.md`。
- **没做的**：
  - 平台在跑时，Agent 仍动手的情况，只靠提示约束；
  - 只聊不续作的改动不会经过审查，也不会被发布（发布要求门禁通过，`publish_unverified`）；
  - 用户在聊天里自己派的子 Agent，平台不逐个把关。

### 历史会话：置顶、收藏、共享、删除（负责人 2026-10-22）

- **入口**：会话列表每一行右上角的「⋯」：置顶 / 取消置顶、收藏 / 取消收藏、共享…（有共享权限时）、删除…（有删除权限时；仍要输入完整会话 ID，并先备份）。
- **只对自己有效**：置顶与收藏是个人的标记，能看到这个会话的人都可以标，别人看不到。数据在迁移 13 的 `run_marks` 表。
- **置顶**：显示在列表最上面的「置顶」分组里，下面的分页列表不重复显示。
- **收藏**：筛选组里的「收藏」，开放与已关闭的会话都列出。
- **API**：
  - `POST /v1/runs/<id>/marks {"pinned"?: bool, "starred"?: bool}`；
  - 列表项带 `marks`；
  - `GET /v1/runs?pinned=1` / `?starred=1` 只列出自己置顶或收藏的会话。

### 按阶段把关（D69，负责人 2026-10-22「按阶段把关」）

设计稿：`docs/superpowers/specs/2026-10-22-phase-gating-design.md`。

- **阶段**：任务的 `phase` 为 `diverge`（发散：头脑风暴、调研、多方案）、`design`（设计）或 `converge`（收敛：实现、撰写、测试、交付）。
  - 写法：规划者在计划里写，人在任务编辑器里改，导入的工作流也可以带（「阶段：发散 / 设计 / 收敛」也认）。
  - 没写时按类别推断：`research` 为发散，`design` 为设计，审查取它所审任务的阶段，其余为收敛。研发会话因此全部是收敛，判定与以前相同。
- **发散任务与设计阶段中途的任务**：
  - 失败如果在白名单内，就记为带前缀的提示（「发散阶段不阻断：」「设计阶段中途不阻断（阶段末再把关）：」），任务按完成接受，不诊断、不进纠错，结论交给下一个任务。白名单：原生出错（自动重试之后）、工具与配置不符、执行者或审查者写的 blocked / changes_requested。
  - 证据记录：`phase`、`phase_relaxed`（被放行的门禁）、`phase_relaxed_error`（原来的错误）。
  - 原生任务没跑完时，平台生成交接并写明「任务未完成」。
  - 发散任务不运行检查命令。
- **照旧阻断，任何阶段都一样**：
  - 安全底线 `no_secrets`；
  - 被停止、登录失效、产出归属不可核实；
  - 结论写明缺输入、认证、模型或环境；
  - 必过检查、Evaluator 判不通过、规划校验，以及以后新增的失败类型。
- **设计阶段末与收敛任务**：照旧判定。设计阶段末，指计划中下一个任务不再是设计阶段的那个设计任务；只有一个设计任务时，它就是阶段末。
- **只有调研的会话**：重新规划后，必过检查只因为候选任务都是发散任务而没有可运行的任务时，记提示，不判计划无效（有可运行的任务但放不下时照旧 409）。首次规划中，落在不运行检查的任务上的必过检查，会写明它不会运行。
- **D57 Task 12「有提示就不自动发布」**：负责人在检讨中没有批准去掉它，目前暂缓、等负责人决定（见「门禁与安全底线（D57）」）。实施时，阶段放行的提示同样会让自动发布等负责人确认。

### 不再停下等人（D71，负责人 2026-10-22 关卡检讨）

完整清单与根因见 `docs/postmortems/2026-10-22-gates-retrospective.md`。负责人逐项决定后，以下改动已经实施：

- **验收**：
  - 没有「验收待确认」：Evaluator 的 pending 项记为提示，会话照常完成，报告写「自动通过，记为提示」；
  - 删除了确认验收的操作（`POST /v1/runs/<id>/accept`）、MCP `octopus_accept`、控制台「确认通过 / 不通过，返工」；
  - 升级前停在「验收待确认」的会话，由引擎下一轮按提示接受并继续。
- **协调者**：
  - 只有决定写明 `missing: credentials`（缺仓库凭证、授权或认证）或 `missing: input`（缺只有用户能提供的需求信息）时，才停下等用户；
  - 其余的等待、平台拒绝的决定（目标不在允许范围、换工具不合规则、扩大路径不合规则、重新规划次数已用完）、没形成可用决定的诊断，都改为按协调者所说重试失败的任务：
    - 拒绝原因记在该次决定的 `refused`；
    - 下一次诊断能看到 `fallback_from`；
  - 仍受自动纠错轮次上限约束（默认 2 轮）。有人手动停止的诊断照旧停下。
- **取消「连续两次等待后不再自动诊断」（D57 §6.2）与 `rediagnose`**：每次请求诊断都会诊断；旧客户端传 `rediagnose` 时忽略。
- **`identity_match`**：执行环境没有报告实际的后端或模型时，只记提示、照常执行；报告了且与配置不符时，仍是工具的失败，协调者可以换工具。
- **Skill 与 MCP**：只在不可逆或对外的操作前征得用户同意（发布与推送、删除、批准工具调用、管理员操作）；分配执行环境、换工具、补充说明、同步、停止或关闭等，按用户的要求直接做，做完告诉用户。

### 门禁与安全底线（D57，负责人 2026-10-21 / 10-22）

设计稿：`docs/superpowers/specs/2026-10-21-gate-relief-design.md`。实施中负责人另定（2026-10-22）：任何新增的拦截、等待或确认，都须先经负责人同意，不能卡用户。设计稿中会让会话停下的安全阻塞几项因此暂缓（见本节末「暂缓」）。下面写的是现在的行为。

- **门禁的四种状态**：通过 ✓、提示 ⚠（`warn`）、失败 ✗、跳过 –。
  - 提示照常记录和展示，但任务照常验收，不 blocked，也不进纠错；已验收的 Attempt 在证据 `warnings` 中列出它们。
  - 哪里看得到：控制台任务行（门禁用中文名、英文名在悬停提示里，Task 15，待部署；此前显示英文名）、会话页「完成 · N 项提示」、报告「提示 N 项」、MCP 精简状态的 `latest.gates.warned` 与 `session.warnings`。
  - 只有失败才让 Attempt `blocked` 并进入纠错。发布要求的「已验证候选」只看有没有失败，提示不影响。
- **哪些拦、哪些只提示**：

| 门禁（控制台名，Task 15，待部署） | 现在 |
|---|---|
| `no_secrets`（密钥检查） | **拦**：候选新增的内容里有疑似凭证或 `.env`，会话分支不动。规则与 D57 之前相同（见下「安全底线」）；D69 的阶段放行也不放它 |
| `native_completed`（原生执行） | **拦**：原生任务出错、被停止、没有结果，或回复 BLOCKED。平台自身的故障先自动重试（见下） |
| `identity_match`（实际工具与模型） | **拦**：执行环境报告的后端、模型或 Effort 与配置不符；没有报告时只提示（D71） |
| `outcome_pass`（任务结论） | **拦**：执行者或审查者的结论是 `changes_requested`、`blocked` 等 |
| `checks_pass`（检查命令） | 只有**必过检查**失败才拦；建议检查失败、因环境无法执行的检查，都只提示（见下） |
| `evaluated`（Evaluator） | **拦**：Evaluator 判不通过；只有待人确认的项时记提示、自动通过（D71） |
| `read_only`（知识沉淀只读） | Knowledge 任务改了文件或移动了会话分支时记失败，但不卡会话：该 Knowledge 任务被跳过，这次不沉淀知识（D46 P4） |
| `diff_present`（入库改动） | 提示：「本任务没有改动入库文件。如果它本来就只产出补丁或报告，可以忽略。」Worker 拒绝生成候选（如 Agent 切换了分支）仍拦 |
| `artifacts_locked`（已验收的文件） | 提示：改动保留，列出被改的已验收任务文件（`files`），交给后续审查。D54 的 `paths_add` 仍兼容 |
| `tests_fail_on_baseline`（测试是否针对改动） | 提示：「测试在改动前也能通过，可能没测到改动」 |
| `review_bound`（审查对象） | 提示：审查改的文件由平台撤回，审查结论照常采用；无法核对或撤回不成功时仍拦（见下） |
| `handoff_present`（交接文件） | 提示：缺交接时平台用原生结果生成一份 |
| `outcome_valid`（结果文件） | 提示：结果文件不合法时，按文件与回复中写明的结论判断；写明了「要求修改」「受阻」「不通过」或「拒绝」（`changes_requested` / `blocked` / `fail` / `rejected`）时仍按拒绝处理 |
| `secrets_masked`（已打码的疑似密钥） | 提示（D57 新增）：本次保存的内容里有 N 处疑似密钥已打码。从不拦 |

- **安全底线：拦什么**（与 D57 之前完全相同；负责人批准之前，不拦更多）：
  - 拦截点：候选的 `no_secrets`；发布前的扫描，含发布带出的历史（`publish_secret_found`）；本地同步与拉回（`sync_secret_found` / `bundle_secret_found`）；续作前提交聊天里的改动（D73 `chat_secret`）；Knowledge 条目（「含疑似秘密，未写入」）。
  - 拦截规则都是同一套 `sources.SUSPICIOUS`：GitHub Token（fine-grained、classic、OAuth、App）、GitLab Token、以 `AKIA` 开头的 AWS Access Key ID、私钥头。候选、发布、同步与拉回另拦 `.env` 文件（`.env.example` / `.env.sample` 除外）。
  - 下面打码多认的几类（`ASIA` 临时凭证、Anthropic / OpenAI / Slack、平台 Token 等）**只打码，不拦**。
- **打码**（D57 §3.3，写入时）：
  - **认什么**（`sources.SECRET_RULES`）：
    - 上面的拦截规则；
    - AWS 临时凭证 `ASIA…`，以及跟在 `aws_secret_access_key`、`SessionToken` 一类标签后的密钥与会话 Token；
    - Anthropic、OpenAI 的 API Key，Slack Token；
    - 平台签发的 API Token（`oct_…`），以及本会话 Worker 的 Token（按值认）。
  - **怎么打码**：只显示首 4 位与末 2 位（不足 12 个字符时只显示 `…`）。同一把私钥的多行合成一处，最多连成 8 KB。
    - 露出的首尾不切开转义（`\/`、`\uXXXX`）：打码后的 JSON 文本仍能解析。
    - 紧挨着的两个密钥（如 `sk-…sk-…`、`ghp_…ghp_…`）打成一处；打过码的文本再打一次，结果不变。
  - **判定按原文**：门禁、Knowledge 等判定都在打码之前、按原文做出，平台只保存打码后的副本。打码从不改变判定结果。
    - 工作流推荐也一样：结束时按 KiroCrew 的原文解析，解析出的任务（或不合格的原因）随证据保存（`recommendation`），推荐页读它。
    - 打码在写入事务之前做完，事务里只写入。
  - **哪里打码**：
    - Attempt 的 result、error 与证据：结论、交接文字、门禁详情、检查命令本身与输出末尾 4 KB、基线检查的输出；
    - 会话状态里的错误，时间线（journal 的每条事件）；
    - 协调者诊断保存的记录与决定；
    - Knowledge 条目及写入结果的原因；
    - MCP `octopus_observe` 与 Worker 的 observe：在截断之前打码；控制台的会话观察由平台再打码一次（早于 D57 的 Worker 不打码）；
    - 平台写进原生父对话的开始说明与结果说明。
  - **命中后**：
    - Attempt 证据 `secrets` 记 `{kind, location, masked, hash}`，位置如 `checks:task-2`，不存原文；
    - 加一条提示 `secrets_masked`「已打码 N 处疑似密钥：…」。不改任务状态，不设 `gate` / `failure_type`，会话照常继续；
    - 是真密钥时，由负责人吊销或轮换。平台不会停下，也不会自动清理。
  - **不打码的**：
    - 用户自己写的需求、steps 与续作内容；
    - 规划者生成的计划与会话快照（含修订历史）；
    - 原生 KiroCrew 的实时对话与页面（子 Agent 的对话、工具输出）：平台控制不了；
    - Worker 主机上执行边界内的原始文件（`handoff.md`、`outcome.json` 等）：作为证据原样保留，权限不变；
    - 平台自己判定用的提交、文件与作业记录。
  - **已知局限**（只影响展示）：
    - 私钥的 BEGIN / END 行都不在文本里时，私钥正文认不出来：如 pytest 默认截断的 diff、`git diff -U0` 只露出中间几行。
    - 有 BEGIN / END 的文本里，私钥正文按「40 个以上的 base64 字符」认。更短的片段不打码，包括 pytest 截掉开头后剩下的第一行。
    - 一行里的列表 repr（`['…\n', '…\n']`），以及 PHP / Perl 用 `.` 拼接的多行字符串：每行各打一处码，各露出首 4 位与末 2 位。
    - 行号前缀宽度变化处（如 `cat -n` 从第 9 行到第 10 行），私钥会被拆成两处。
    - `docker compose logs -t` 的前缀约 40 字节，超过合并上限 32 字节，私钥的各行分开打码；多个容器的日志交错时也一样。
- **检查命令（`checks_pass`）**：
  - **必过与建议**：任务合同 `verify_required` 按命令原文列出必过的几条，其余为建议检查。控制台任务行分开列出两类。
    - 人写的检查一律算必过，写了 `verify_required` 时只有其中几条必过。人写的检查指：经 MCP、API、控制台提交的 steps，保存的工作流，续作时改的 `verify`（与任务原有的不同才算改；原样带回的不改必过 / 建议）。
    - 规划者写的检查：在 `verify_basis` 中引了需求原文的才算必过（规范空白后须是需求原文的子串），否则按建议检查，计划说明里写明。
    - D57 之前的任务没有 `verify_required`：规划者写的检查都算建议，人写的都算必过。控制台任务行也按此列出。
    - 只有用户续作能改必过 / 建议。换工具时标记不变。
  - **判定**：
    - 所有检查都执行；
    - 必过检查失败（含超时、没来得及运行）：失败；
    - 建议检查失败：提示「建议检查未通过：…」；
    - 任何一条因环境无法执行，必过检查也算在内：提示「无法执行（环境）：…。请确认环境，或把这条检查改写成可执行的形式。」，不进纠错。环境原因指命令不存在（退出码 127）、缺少 Python / Node 模块、没有 git 提交者身份、Docker 不可用。
  - **重新规划**：
    - 被替换任务的必过检查须由新任务原样承接。重新规划漏了的，平台补到合适的新任务上，计划说明写「平台把原任务「X」的必过检查补到了 …」；没有能运行检查的任务可放时，计划不合法（409 `plan_invalid`；只有发散任务时只提示）。
    - `verify_fixes` 不能删除或改写保留任务的必过检查。
  - **git 身份默认值**（见「候选提交与命名门禁」）的副作用：
    - `git config --global <key>` 只读平台的临时文件，不读其中的 include：`user.name` / `user.email` 返回平台默认的 `KiroCrew ON` 身份，其他键（`init.defaultBranch`、`core.editor` 等）什么也不返回，而不是执行用户自己的全局配置。不带 `--global` 的查找照常：用户、仓库与 `-c` 的配置优先。
    - 项目测试用 `HOME=$tmp` 隔离 git 配置时，`GIT_CONFIG_GLOBAL` 仍指向平台的临时文件，该文件以绝对路径引入执行用户真实的 `~/.gitconfig`，所以测试会读到真实配置。测试里 `git config --global` 的写入进了这个临时文件：同一次检查作业的各条命令共用，作业结束即删除。要完全隔离的测试应自己设置 `GIT_CONFIG_GLOBAL`。
    - 主机的系统级身份（`/etc/gitconfig`）会被平台默认值盖过。git 早于 2.32 时没有默认值（与以前相同）。
    - Worker 在检查中途退出时，临时目录里可能留下 `octopus-checks-*.gitconfig`。
- **审查改动的撤回（`review_bound`）**：
  - 审查任务首次派发时，Worker 记下工作区里未跟踪、也未被忽略的文件（依赖目录与 `.env` 除外），写入 `<run>/attempts/<attempt>/untracked.json`。
  - 审查结束时，无论通过与否：
    - 已跟踪文件的改动由平台撤回，审查在候选之上做的提交一并去掉，会话分支回到候选；
    - 审查新建的文件移到 `<run>/attempts/<attempt>/review-removed/`，不删除；
    - 撤回之前，已跟踪文件的改动（暂存的、工作区里的、被去掉的提交里的）先存成同一目录下的 `tracked.patch`（已有时 `tracked-2.patch`…），在候选上 `git apply` 即可恢复。D73 让人和审查用同一个工作区，审查期间有人改的文件也在里面。存不下时不撤回（记失败，改动留在工作区）；
    - 记提示，列出撤回与移出的文件。审查结论照常采用，`reviewed` 记为该候选，计入发布所需的审查。
  - 仍拦的情况：
    - Worker 拒绝核对审查的工作区（「无法核对审查的工作区」）；
    - 会话分支已不在候选之上、撤回后工作区仍不是候选、已跟踪的改动撤回失败；
    - Worker 早于 D57、不能撤回，而审查改了已跟踪的文件（照旧判为「审查不应修改代码」）。
  - 只提示的情况：新建的文件移不出；`reviewed_commit` 缺失或与候选不符。
  - 局限：
    - `review-removed` 不自动清理，每个审查 Attempt 都可能留下一份（如没被忽略的 `dist/`、覆盖率报告），清理磁盘时一并处理；
    - 快照把嵌套仓库记为一项 `sub/`，审查又删了 `sub/.git` 时，其中的文件会被逐个当作新建文件移出（可以拷回）。
- **平台自身故障的自动重试**（D57 §6.3）：
  - **哪些故障**：原生运行时的沙箱没能初始化（`AcpSandboxInitFailed` / `sandbox failed to initialize`）、运行时取消、ACP 进程退出、残留进程没能清理，以及 KiroCrew 回收了子任务（见下）。沙箱与回收是 D57 新增的，其余三类任务上沿用 D34。
  - **谁适用**：任务与协调者诊断（`poc-diagnose`）。诊断遇到这些故障也重试，是 D57 新增的。
  - **怎么重试**：自动重试一次，不占纠错轮次，计入自动重试预算（D42，默认 4 次、720 分钟）。
  - **再次失败**：
    - KiroCrew 回收（`runtime_teardown`）的那次重试又被停止：记为原生任务被停止（`native_stopped`，属于要人处理的一类），不做自动诊断，会话停下等用户决定（`test_a_second_teardown_is_not_retried`）。
    - 其余几类：任务交给协调者诊断。诊断不再重试时（它已是重试的那次，或自动重试预算不允许再试），按 D71 改为重试失败的任务；只有这次诊断是被停止的（如被 KiroCrew 回收）才停下，提示「诊断员没能完成」（Task 15 的文案，待部署）。
  - 原生任务 `never started`（排队一直没轮到）的重新派发属 D60，见「排队」。
  - 合同问题的重新规划（§6.1）：失败证据显示问题出在任务合同上（`failure_type: contract`）时，这次重新规划不占每会话一次的额度，至多 2 次。
  - 防空转（§6.2）已由 D71 取消，见上一节。
- **KiroCrew 回收与人工停止的区分**：
  - **问题**：KiroCrew 回收子任务（parent-end teardown）与人在原生页面停止任务，报告的都是 `stopped`，没有字段能区分。
  - **平台的判断**：同时满足以下三条时，按 KiroCrew 回收（`runtime_teardown`）自动重试一次。
    1. 原生任务 `stopped`；
    2. 平台自己没有停它：会话没有被停止或关闭，也不是看门狗停的；
    3. 从该 Attempt（或诊断）创建前 8 小时起，没有人经平台在该 Worker 上打开过原生 KiroCrew 入口。打开入口指 `POST /v1/runs/<id>/crew-entry`，记审计 `run.crew_entry` 与时间线 `crew.entry_opened`。
  - **其余的停止**：
    - 会话被停止或关闭：不重试；
    - 看门狗停的：按 D39 自动重试一次，见「看门狗」；
    - 有人可能经入口停的：按人工停止处理，不重试。
  - **为什么是 8 小时**：入口给的原生登录最长 8 小时（`crew_entry_ttl` 的上限；Gateway 的 30 天刷新在公网中转上被拦下，见「原生对话入口」），而且覆盖该 Worker 的整个 Gateway（K03）。打开过入口的人，能停止该 Gateway 上的任何原生任务。
  - **已知缺口**：不经平台、直接连到 Gateway 的停止平台看不到（如经 SSM 端口转发打开原生页面后停止），会被当作回收，最多多重试一次。运维这样停了任务又不想让它重跑时，在平台上停止该会话。
  - **部署注意**：这个判断看的是审计里的 `run.crew_entry` 记录，D57 起才写。首次部署 D57 后的 8 小时内，部署前打开的入口没有记录：经这样的入口人工停止的任务，可能被当作回收自动重试一次。
  - D63 已关闭 eager spawn，回收应很少见，这条重试是兜底。
- **暂缓**（负责人 2026-10-22：新增的拦截或等待须先经负责人同意）：
  - 暂缓的五项：
    - 安全阻塞状态与人的判断（计划 Task 8）；
    - 误报记忆（Task 9；迁移 11 仍空着，留给它）；
    - 判断后复扫（Task 10）；
    - 预览、下载与不入库交付物的扫描和拒绝（Task 11，含合同的 `untracked` 字段）；
    - 「有提示就不自动发布」（Task 12）。
  - 因此现在：
    - 疑似密钥只打码、记提示，会话照常继续；
    - 预览与下载不做密钥扫描（下载照旧排除 `.env` 与依赖目录）；
    - 有 ⚠ 时自动发布（D47）照常进行，发布前的扫描仍会拦。
  - 执行边界的临时目录改放磁盘（Task 16）：在分支 `park/d57-t16-tmpdir`，没有部署。2026-10-23 EC2 实测 `/tmp` 31 GB 只用了 687 MB，而部署要重启每个 Gateway。
  - 内存不足时用户看不见（§7.2）：由 D60 解决，见「排队」。

### 本地与远端联动、使用者命令行（D58）

- **交换方式**：本地与会话之间用 git bundle 交换提交，不经过代码托管方；使用者不需要源仓库的写权限，新建项目也能用。发布（推送与 PR）照旧由 D35 / D47 完成。
- **会话关联**：仓库根目录的 `.octopus/session.json`（与上下文目录 `.octopus/sessions/<run>/`）写入 `.git/info/exclude`，不进入提交。
  submit、pull 时写入；status、pull、sync 等省略会话 ID 时读取。MCP `octopus_link` 可手动关联。
- **pull**：`POST /v1/runs/<id>/bundle` 给出 10 分钟的链接，`GET /bundle/<票据>/<id>.bundle`（`?full=1` 为完整历史）由 Worker 的
  `/pull/bundle` 生成，按发布同样核查疑似凭证与 `.env`（`bundle_secret_found`）。本地取为 `octopus/<run>` 分支，不覆盖该分支上本地自己的提交；
  缺会话基线时自动取完整历史；同时写报告、计划与各任务交接。拉回是读：只读（`read`）Token 与查看身份的共享对象都可以拉回（2026-10-26）。
- **sync**：`POST /v1/runs/<id>/sync`（`application/octet-stream` 的 bundle，≤ 20 MB；查询参数 `request_id`、`expected_version`、`old`、`head`、
  `note`）。Worker 的 `/sync` 只在会话分支仍在 `old`、工作区没有未纳入候选的改动、`head` 是快进且新内容没有疑似凭证时移动分支与工作区；
  拒绝码 `sync_not_fast_forward`、`sync_dirty`、`sync_secret_found`、`sync_too_large`，`head` 等于 `old`（没有新提交）为 409 `nothing_to_sync`；
  平台先鉴权（须 `execute`）再读请求体。平台为最后一个写代码的任务（implement / deliver）
  记一个「本地同步」Attempt（证据 `synced`：谁、几个提交、说明），有检查命令时照常运行与判定，其后的任务重新执行（审查绑定新候选）；
  修订「本地同步」、事件 `run.synced`。权限与续作相同；同一 `request_id` 只同步一次。Worker 早于 D58 时 409 `worker_outdated`。
- **未推送提交**：在仓库内提交且源码是这个仓库的 origin 时，本地有 origin 没有的提交会先停下；`--push-base`（MCP `push_base`，须用户同意）
  先 `git push -u origin HEAD`，`--allow-unpushed` 按 origin 已有的提交提交。SSH 形式的 origin 按 HTTPS 提交（执行环境只用 HTTPS 克隆）。
- **使用者命令行**：`octopus login --url <平台地址>`（Token 保存在 `~/.config/kirocrew-on/cli.json`，0600——已有的文件先收窄权限再写入；先保存、再吊销登录用的临时 Token，标签「octopus CLI」，默认 30 天，`--expires 7d|30d|365d|never` 另选；
  Request 的 client 记为 `cli`），之后 `octopus submit / plan / status / watch / report / pull / sync / continue / approvals / search`，
  `--json` 输出 JSON，`--run` 指定会话。运维命令（serve、worker、connect、backup 等）不变。

### Request 与映射偏好（D55；准入规则已于 D70 取消）

- **Request**：每次提交（控制台、API、MCP `octopus_start`）登记一条 Request（迁移 9 的 `requests` 表），记提交者、Team、来源客户端
  （取自 Token 标签：控制台登录为 `console`，`octopus connect` 签发的为 `claude` / `kiro` / `codex` / `kiro-ide`，其余为 `api`）、需求原文、源码、
  显式选择（工作流 / 模板 ID、steps 个数、文档格式、规划方式，不复制正文）与策略快照（自动纠错、发布；D70 之前的还有准入）。会话的 `request`
  指向它；状态取其最近一个会话的。同一 `request_id` 重复提交返回原有的 Request 与会话。工作流设计会话与升级前的会话没有 Request。
  `GET /v1/requests`（`status`、`q`、`client`、`limit`）、`GET /v1/requests/<id>`；看不到其会话的人得 404。MCP `octopus_requests`。
- **映射偏好**：`{"kind": "review"}` 或 `{"role": "evaluator"}`（二选一）加 `"tool"`、可选 `"model"`（kind `mapping`）。提交时把提交者与
  Team 的偏好写进会话快照（同一类别或角色上个人覆盖 Team），首次规划与重新规划的提示多一节「用户的映射偏好」：规划者优先采纳、
  不采纳写明理由；需求中指定的（D45）优先；不可用的工具不选。平台不按偏好改写任务，也不把偏好当失败时的回退链。规划者的任务与偏好
  一致时映射来源写「KiroCrew 规划（按个人偏好 / 按 Team 偏好）」。
- **维护**：控制台「我的设置 › 映射偏好」；个人偏好只有本人能增删，Team 的规则只有 Team owner 与平台管理员能增删，已归档的 Team 不能新增；
  每次增删写审计 `policy.create` / `policy.delete`。API：`GET /v1/policies`（`team_id`）、`POST /v1/policies`、`DELETE /v1/policies/<id>`。
  MCP 只读（`octopus_policies`）。

### 等待与自动恢复（D43）

- **健康检查**：平台每 10 分钟读取一次各已启用 Worker 的 `/health`（有会话在等 Kiro 登录的 Worker 每分钟一次），执行边界未变时保存为执行环境能力
  （工具、检查命令、Kiro 登录、模型；「执行环境」中显示「最近检查」）。升级 Worker 后不必再手动「刷新模型」，最多 10 分钟内生效；急用时仍可手动刷新。
  边界、宿主账号或隔离方式变了不保存（日志「another execution boundary」），由管理员核查。
- **容量**（MVP 0.2.0 P1）：`/health` 的 `capacity` 报主机内存（`mem_total_mb`、`mem_available_mb`）、`cpus`、`load1`、本边界运行中的尝试数与 KiroCrew 的 `max_subagents`；读不到的为 null，不影响健康检查。控制面用它放置与扩容（P3）。
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
  等待批准或排队结束时起算）超过 `idle_minutes`（默认 30），或总时长超过 `attempt_minutes`（默认 170）时，平台以 `DELETE /api/spawn/{id}`
  停止该原生任务。等待批准、排队与检查命令运行期间不判定；没有 `state.json` 时只按总时长判定。
  KiroCrew 自己在 180 分钟收回子任务（不经平台的自动重试），`attempt_minutes` 大于它时不起作用。
- **之后**：Attempt `blocked`（原因写明分钟数与最后工具），自动重试一次（不消耗纠错轮次）；重试仍卡住时交给协调者诊断（可换工具）。
  自动纠错关闭的会话只停止、不重试。
- **长命令**：单个命令运行期间 `updated_at` 不更新；大型构建或完整测试较慢的项目，启动或续作时设 `recovery.idle_minutes`（5–240）。
- **旧 Worker**（不支持停止单个任务）：不行动，任务继续，避免两个 Agent 同时修改同一工作区。停止失败时一分钟后再试，同样不重试。
- 重试在被停止任务留下的工作区上开始（未提交的部分改动仍在）：与 D34 的自动重试相同；D37 的候选按任务起点计算改动。

### 排队（D60，M4-4 第 1 期）

- **从哪来**：执行环境内存不足、负载过高或暂停接收新任务时，KiroCrew Gateway 推迟新的子 Agent（`POST /api/spawn` 回 `queued`，
  `reason` 为 `low_memory` / `posture_critical` / `adaptive_cap_zero`）；只等并发槽位的照常 `spawned`。Worker 在列表里找不到任务时改问
  `GET /api/spawn/{id}`，任务文字带本 Attempt 标记的才算本 Attempt 的排队（返工续接的回执写 `spawned` 也以它为准）。
- **会话上**：「需要处理」写「排队：等待执行环境资源（执行环境内存不足 / 负载过高 / 暂停接收新任务）」，只写原因的类别；
  内存数字等明细只在事件 `attempt.queued` 的 `detail`。开始执行时事件 `attempt.dequeued`（`waited` 秒），提示清空。
- **不计**：排队期间看门狗不判定，排队结束起重新计时；排队的时间不计会话的时长预算（与等人相同）。
- **没有开始**：原生任务以 `never started` 结束（排队一直没轮到）时自动重新派发，不占自动重试次数与预算（事件 `attempt.retry`，
  `transient: never_started`）；连续第 3 次仍未开始，或排队前已开始过（`resuming`）的，交给协调者。
- **处理**：长时间排队说明该执行环境资源不足：结束空闲的原生进程或扩容；需要时把会话改派到别的执行环境。

### 并发上限（D61，M4-4 第 2 期）

- **限什么**：同时运行的任务数——运行中、派发中、等待批准、派发未确认的 Attempt，加上协调者的诊断；只数仍在推进的会话
  （停止、关闭、`blocked`、`waiting_worker` 的会话留下的记录不占位）。个人范围是提交者的全部会话（含 Team 会话），Team 范围是该 Team 的会话。
- **谁设**：控制台「管理 › 并发上限」（不是管理员的人在「我的设置 › 并发上限」查看，Team owner 在那里设 Team 的上限）或 `POST /v1/quotas`（`{"scope": "default"|"user"|"team", "id", "running": 1–50 | null}`）。
  每人默认与个人的上限：平台管理员；Team 的上限：Team owner 或平台管理员。没有设的不限制（默认都不设）。每次修改写审计 `quota.set`。
  `GET /v1/quotas` 与 MCP 只读 `octopus_quotas` 返回自己的与所在 Team 的上限与占用。
- **到了上限**：会话保持「就绪」，下一个任务不派发；「需要处理」写「排队：已达到并发上限（你 / 提交者 / Team「X」同时运行的任务已达 N 个，
  上限 N）」，事件 `run.quota_wait`。有任务结束后按等待先后自动开始（`run.quota_go`，`waited` 秒）；等待的时间不计会话的时长预算。
- **不等待**：自动重试与 never started 的重新派发、协调者的诊断、D58 本地同步的检查命令与手动「请求诊断」——它们计入占用但不排队，
  所以占用可能短暂超过上限。上限调小时运行中的任务不受影响。
- **用量上限**（每月 credits / token / 分钟）不在本期，见 BACKLOG。

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
- **父会话不跑多余的一轮**（2026-10-23）：每次派发、续接与模型探测拿到原生 ID 后，Worker 调用 `POST /api/spawn/mark-collected`（`{ids, parent_session}`，KiroCrew 自己的阻塞式 spawn 工具用来表示父会话已拿到结果）。结束时 KiroCrew 不再把结果注入平台的父会话、不起协调者跑一轮，也不触发波次汇总；平台照旧从 `GET /api/spawn/{id}` 读结果。标记失败（旧 Gateway）不影响派发，`dispatch.json` 的 `collected: true` 表示已标记。标记只在 Gateway 内存中：运行中重启后，该任务结束时照旧跑一轮。原生页面的父会话因此不再逐个任务出现回复。
  EC2 核对（2026-10-23，a183d84，run-d62631d5）：两个 Attempt 都记 `collected: true`，Gateway 日志两次「skipping injection」，父会话一轮也没跑；部署前同样两个任务的 run-656be0a4 跑了 2 轮。
- **配置契约**：平台写入 `config.json` 的键列在 `tests/kirocrew_contract.json`（版本须等于 `deploy/versions.env` 的 `KIROCREW_ACCEPTED_VERSION`）。
  本机回归核对 `seed()` 只写清单内的键；部署时的原生测试在主机上核对清单中每个键都是已安装 KiroCrew 的配置字段，并用 KiroCrew 自己的加载器读回
  新生成的配置，不得有被忽略的键（`_extra_keys` / `_extra_sections`）。升级 KiroCrew 时先在新版本上核实清单并改版本号，原生测试不过则部署中止。
  同一清单的 `runtime_errors` 列出平台自动重试（L0）所依赖的 KiroCrew 报错文字，各附一条 EC2 原样报错：本机回归核对每条都按其种类重试，
  部署时的原生测试核对已安装 KiroCrew 的源码仍含这些文字——升级后措辞变了，运行时故障不会悄悄改由协调者处理。
  `http` 列出平台发给 KiroCrew 的每个请求体所到的处理模块与其读取的键：本机回归以 AST 扫描 `octopus/worker.py` 的每个请求体，只许用清单内的键
  （KiroCrew 静默忽略未知键）；部署时原生测试核对已安装 KiroCrew 的这些模块仍读取这些键。

### Agent 提示（D46 P1）

- 系统提示：所有 Coding 工具执行任务时为「研发执行者」；父会话的 Agent 为「会话协作者」（D73：用户在原生聊天里直接与它对话，平台不在执行时可以按要求改工作区或派子 Agent，见下文「在 KiroCrew 聊天里直接对话」）；
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
- pending（需要人判断，如观感、文案）且无 fail → **自动通过**（D68；D71 起没有别的选项）：门禁 `evaluated` 记为提示（warn），写明待人确认的
  各项与证据，会话照常完成，报告与提示中列出。

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
  上一轮的对话本身出了问题（原生错误、被停止、看门狗停止、登录过期、实际后端或模型不对：failure_type 为 native_error、native_stopped、auth、tool、model）——
  只有工作没做到位（implementation、contract、gate、input 等）才续接；
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
  `DELETE /api/lessons {rule, exact: true, repo_scope}`，台账记删除者。删除过的条目在同一执行环境、同一仓库不再写入（以后的会话再列出它时记为 skipped「有人删除过同一条知识」）。MCP 精简状态 `session.knowledge`。
- 前提：KiroCrew `memory.persistence_enabled` 与 `memory.inject_lessons` 为 true（0.10.0 默认；关闭前者时写入被拒，台账记
  `persistence_disabled`）。KiroCrew 自身的自动 lessons（task-runner、整理提取）不经平台，`include_lessons` 时同样注入——专属环境内属于同一空间。

### 团队层知识（D52）

- 个人层（P4）：lessons 存在某个人的 Gateway 上、自动写入、无人审核。团队层：会话**发布到仓库时**，平台把本会话通过校验的 Knowledge
  条目（written、superseded、kept，未删除）提到仓库的 `.octopus/knowledge.md`：已有打开的「项目知识更新（KiroCrew ON）」Draft PR / MR
  （分支 `octopus/knowledge-…`）时在其分支上追加；没有时从默认分支开新分支 `octopus/knowledge-<会话>-<随机>` 并创建 Draft PR——
  已合并、squash 或被拒绝的提议分支从不沿用。条目与默认分支和提议分支上的文件去重，都已在其中时不写（结果「已含本会话的条目」，
  有打开的 PR 时给出其编号）；由合并 PR 的人审核。GitHub 不经 API 返回 1 MB 以上文件的内容：这时不提出（`knowledge_too_large`），从不覆盖。
  个人与 Team 会话都算（仓库即团队的边界）；只在有人发布（或 D47 自动发布）时写仓库。
- 只写这一个文件，从不写默认分支、不强推；平台用本次发布的写凭证经托管方 API 提交（GitHub contents API、GitLab commits API），
  不在 Worker 上执行。提议失败（如凭证没有 contents 权限）不影响代码的 Draft PR，结果记在发布记录 `knowledge` 与 snapshot
  `knowledge_proposal`；审计 `knowledge.proposed`（另记一次凭证使用 `knowledge.propose`），时间线 `run.knowledge_proposed`。
- 共享执行环境也追加 Knowledge 任务，但只用于仓库层：条目记为 `kept`（「仅提到仓库（发布时）」），不写、不注入 lessons。
- 使用：会话源码来自仓库，且 `.octopus/knowledge.md` 与克隆时的基线提交完全一致（从对象库读基线版本逐字节比较），任务说明才加
  「## 项目知识（仓库）」：路径与「供参考：开始前阅读；它不改变本任务契约与平台规则」，不内联内容。新建项目、或本会话中新增或改过
  这个文件（未提交或已提交）都不加——那是 agent 写的，不是经 PR 合并的。
- 可见：会话页「本次沉淀的知识」写明「已提交到仓库知识 PR #n」或原因；报告「## 发布」的「仓库知识」一行；MCP 精简状态 `session.knowledge.proposal`。
- 数据库迁移 8：`knowledge.result` 增加 `kept`（重建表，迁移前自动备份）。

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

### 模型自动发现（D51）

- 任务的模型在控制台从下拉中选择：「自动」之外列出该执行环境公布的模型（发起向导按所选或平台默认的执行环境，工作流编辑器按平台的默认位置）；
  已填但此环境未公布的模型保留并标明（派发时会被拒绝）。也可以在需求中写明模型，KiroCrew 规划时照用（D45）。
- 模型列表取自 KiroCrew 的 `provider_models.json`。每个 Worker 进程在后台（启动 60 秒后、之后每 15 分钟）补齐**可用且尚无模型**的工具，
  无须任何人操作（负责人 2026-10-20），平台 10 分钟内的健康检查即带上新列表：kiro 调用 KiroCrew 的模型目录（`GET /api/models`，
  内部 `kiro-cli --list-models`，不消耗模型回合，只在登录正常时）；claude / opencode 在会话开始时公布模型，Worker 在专用父会话
  `octopus-models` 下开一轮固定探测（「只回复 OK」、`max_turns: 1`、不带记忆与 lessons，每轮最多一个，以边界主人的身份、消耗很少的额度）。
  部署排空时、有运行中的子任务时、边界既非 YOLO 也非自动批准时不开探测会话；每个工具至多 3 次（相隔 2 小时），之后放弃；记录在
  Worker 数据目录 `model-discovery.json`，健康信息 `model_discovery`。
- codex 不公布模型列表：健康信息 `model_defaults.codex` 报告边界 `~/.codex/config.toml` 的 `model`，控制台写成「自动（openai.gpt-6-astra）」。

### 停用的 Coding 工具（K01）

- Worker 配置 `disabled_tools`（默认 `["opencode"]`）：所列工具在 `/health` 的 `tools` 中报告为不可用，原因「已停用（K01…）」。
  规划者不会选用、协调者不会换用；提交的 steps、模板或工作流中用到它的任务在派发前停下并写明原因，按 D38 由协调者推荐换用可用的工具。
- 恢复：在 Worker 配置中写 `disabled_tools = []`（或去掉 opencode）后重启 Worker，平台 10 分钟内的健康检查即更新（急用时「刷新模型」）。
- K01 复测（2026-10-16，YOLO 常驻）：OpenCode（`opencode/big-pickle`）两次 Attempt 均只返回 `_No response._`，仍未修复。

### 换工具（D38 第 1 期）

- **工具是否可用**：Worker `/health` 的 `tools`（`available`、`reason`；Kiro 另有 `login`）。平台只在登记 Worker 或「管理 › 执行环境」
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
- **不预先起会话（D63）**：KiroCrew 0.10 默认为每个 chat slot 预先起一个会话（`session.eager_spawn`）。平台的 slot 只作子 Agent 的父会话、
  从不发消息，这个预备会话白占一个进程；内存吃紧时 KiroCrew 回收它，会连带停掉 slot 下正在运行的子 Agent（日志「parent-end teardown」，
  报错「killed by Crew (forced stop)」）。每次 Gateway 启动时平台在 `crew/config.json` 写 `session.eager_spawn = false`（已有配置里没有这个键时
  补上；明确设为 `true` 的保留）。代价：在原生 KiroCrew 页面里新开对话的第一条消息要多等几秒。

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
  - **不另购域名**（负责人 2026-10-14 已定，2026-10-27 重申；0.2.0 的原生入口也沿用 `tinyboat.blog`，隔离按设计 §8.2a）。
    Cookie 被灌满时，这个浏览器打开 `tinyboat.blog` 下的页面会失败（请求超过 CloudFront 的大小上限）。
    恢复：在浏览器设置里清除 `tinyboat.blog` 的站点数据，或用无痕窗口，再重新登录控制台。读不到、也改不了任何人的数据。
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

### 会话共享（D64，M4-1）

- **共享给谁**：某个人（按用户名）、某个 Team（共享人自己所在、未归档的；管理员任意未归档的——该 Team 当时与之后的成员）、全公司（平台上全部有效用户）。
- **角色**：查看（viewer）——会话详情、时间线与实时事件流、「KiroCrew 会话」观察、修订、变更记录、代码改动、报告与导出、远程预览、`octopus pull`、
  待批准列表；协作（collaborator）——与该会话 Team 的普通成员相同：再加续作、停止、请求诊断、确认计划、关闭 / 重新打开、开关预览、`octopus sync`，
  仍受 D49 R10（别人的执行边界上只有边界主人能下达指令）约束——会话在别人的个人执行边界上时，协作者可以把它「接手」到自己的边界后续作（D74）。
  全公司只能是查看。
- **不对共享开放**：删除、发布与发布策略、补充说明、答复审批、验收（这三项本来就只属于创建者、Team owner、管理员）、改派执行环境、更换凭证、
  删除沉淀的知识、原生 KiroCrew 入口；D55 的提交记录（Request）也不因共享可见。
- **谁能共享**：创建者、Team 会话的 Team owner、平台管理员——会话页「共享」或 `GET|POST /v1/runs/<id>/shares`、`DELETE /v1/runs/<id>/shares/<share>`；
  同一对象再次共享即改角色；审计 `share.create` / `share.delete`；已归档 Team 的会话不能新增共享（可取消）。Coding Agent 没有共享工具。
- **看到哪里**：侧栏「共享给我」（`GET /v1/runs?shared=1`，MCP `octopus_runs` 的 `shared: true`）；会话页写明「你以 查看 / 协作 身份访问（由 X 共享）」，
  会话视图与 MCP 状态的 `access`。默认列表不含共享来的。
- **取消**：下一个请求起生效，打开着的实时事件流一秒内收到 `access_revoked` 并关闭，预览按文件核对。**收不回**：已下载的导出与源码、已 `pull` 到本地的分支。

### 公开分享：给互联网用户看页面与演示（D75）

把会话产出的页面或运行中的演示发给**没有平台账号**的人看。设计：`docs/superpowers/specs/2026-10-26-public-share-design.md`。

- **入口**：会话页「预览产出」旁的下拉菜单（列出本会话产出的页面）最下面的「公开分享…」——与预览产出是同一批内容，只是给没有账号的人看
  （创建者、Team owner、管理员可见，与「共享」相同）。选内容（页面 / 运行中的演示）、访问方式（需要四位授权码 / 无需授权码）、
  有效期（1 天 / 7 天，默认 / 30 天 / 永久），点「生成链接」：链接（与授权码）已复制，发给对方即可。对话框里列出已有的分享：链接、授权码、到期、打开次数、
  是否锁定；可以重新生成授权码（此前输入过的浏览器要重新输入，锁定解除）或撤销（立即失效）。
- **链接**：`https://<会话 ID>.<preview_domain>/_kirocrew/s/<令牌>`（未配置按会话的预览域名时用共享的 `preview_url`）。放在预览域名而不是控制台域名：
  否则任何能分享的人都能在控制台地址下挂出仿冒登录页。令牌 144 位随机，不可猜。没有预览域名时不能公开分享（409 `share_unavailable`）。
- **授权码**：四位数字，由平台生成。输入正确后，这个浏览器 12 小时内不必再输（Cookie 只对这条链接的路径有效）。防爆破：同一访客对同一分享 10 分钟内
  最多错 5 次（429 `share_rate_limited`）；同一分享 1 小时内累计错 30 次锁定 1 小时（429 `share_locked`）；一个授权码累计错 100 次即作废
  （410 `share_code_retired`，猜中的机会约 1%）。重新生成授权码解除锁定与作废，此前输入过的浏览器与进入演示的访客都要重新输入。
  每个访客对公开链接每分钟最多 600 个请求。
- **页面**：按会话**当前**的工作区提供，只提供网页会加载的文件（HTML、CSS、JS、JSON、图片、字体、音视频、WASM 等；不含源码、配置、数据库，
  `package.json`、`tsconfig*.json` 与名字里带 secret / credential / password / private 的 JSON 也不提供；内容里有疑似凭证的文件不提供），
  点开头的路径、单文件 > 5 MB 同「预览产出」不提供，以 `/` 开头的绝对路径引用不到。入口页在生成链接时确定（未指定时取会话的第一个页面）。
  页面在沙箱中运行，带 `noindex`。会话续作后，链接看到的是新版本。
- **演示**：需要远程预览正在运行（由会话的人在会话页启动）；访客不能启动它，空闲 30 分钟无人访问照常停止。没运行时链接显示「演示当前没有在运行」。
- **失效**：撤销、到期、分享人账号停用或不再能控制这个会话（离开 Team、不再是 Team owner）；每次请求都核对。会话关闭或删除时它的公开分享一并删除，
  重新打开会话也不会恢复。
- **API**：`GET|POST /v1/runs/<id>/public-shares`、`DELETE /v1/runs/<id>/public-shares/<share>`、`POST …/<share>/code`。新建、撤销、重新生成授权码
  都记审计（`public_share.create` / `.delete` / `.code`）。Coding Agent 没有公开分享的工具（与 D64「Coding Agent 没有共享工具」一致）：由人在控制台操作，
  Skill 会请用户去会话页点「公开分享」。
- **运维**：预览域名的 `/_kirocrew/` 本就转给 API；预览 nginx 的访问日志把 `/_kirocrew/s|open|enter/` 后面的令牌或票据记为 `-`。
  打开次数在内存里累计、每条分享至多每 30 秒写一次。

### 接手共享来的会话（D74，负责人 2026-10-25 选方案一）

会话在别人的个人执行边界上时（R10 只让边界主人下达指令），能续作它的人——协作者、Team 成员、想接回的创建者——可以把**同一个会话**
搬到自己的执行边界，此后以自己的身份续作。设计：`docs/superpowers/specs/2026-10-25-session-takeover-design.md`。

- **入口**：会话页「续作」旁的「接手到我的执行环境」，确认一次（负责人选方案一时已同意这一步）；对话框写明会搬到哪、之后以谁的身份运行、
  原生聊天的上下文不跟过去、创建者可以接回，原执行环境里有对话中的改动时列出文件名。API：`POST /v1/runs/<id>/takeover {request_id, expected_version}`，
  返回 202 与 `takeover: {state: "moving", to, from}`。
- **条件**：会话已停下（完成、阻塞、失败、停止）、没关闭；操作者有**恰好一个**自己的专属边界（否则 409 `no_own_environment`）；会话不在 Team 或共享的环境上
  （那里能续作的人都能直接续作，409 `not_needed`），也不在操作者自己的边界上（409 `already_yours`）。查看身份不能接手；管理员不因管理员身份接手（403）。
  会话视图的 `permissions.takeover` / `takeover_reason` 给出能否接手与原因。
- **搬迁**（平台后台进行，不占请求）：停掉原环境上的远程预览 → 原执行环境里对话中的改动先提交为「对话中的改动」候选（不跑检查；有疑似凭证时
  接手失败，409 `chat_secret`，请创建者先在聊天里移除）→ 原 Worker 打出会话分支的完整 bundle（上限 100 MB，同 `pull` 的凭证检查）与各尝试的
  交接、结论等文件（`/pull/attempts`，上限 20 MB，经平台遮蔽凭证）→ 新 Worker `/adopt` 建出同一个工作区（同一批提交、分支与基线；只接受这个会话分支
  一个 head）并建原生父 Session → 一个事务里改为在新边界、以接手人运行：工作区、原生父 Session、各尝试的交接路径、`executor_id`，
  `placement: {kind: "taken_over", from, by}`，版本加 1，修订 `taken_over`、事件 `run.taken_over`。
  从未准备过工作区的会话只换执行人与环境，下次续作时在新环境准备。服务重启打断的接手每分钟自动接着做。
- **接手期间**：续作、诊断、停止、关闭、开关预览、发布、同步、更换凭证一律 409 `takeover_in_progress`；会话页顶部显示「正在接手到 X 的执行环境…」。
- **失败**：会话留在原环境、状态不变，`takeover: {state: "failed", reason, code}`，事件 `run.takeover_failed`；会话页写明原因并可「再试一次」。
  搬迁开始与落地时都再核对接手资格（共享被取消、离开 Team、边界不再属于接手人：`takeover_forbidden`）；接手期间引擎不恢复这个会话，
  落地时它若已在原环境又开始执行或已关闭则不接手（`takeover_conflict`）。
  新 Worker 暂不可用时自动重试 3 次；Worker 版本较旧时 409 `worker_outdated`（部署后两边都升级即可）。
- **之后归谁**：执行人（`executor_id`，视图 `executed_by`）决定会话在哪个边界上跑、用谁的并发额度（D61）与知识空间（个人会话）、
  自动发布以谁的身份；执行人可以续作、诊断、补充说明、答复审批、发布与更换凭证，原生 KiroCrew 入口在他自己的边界上开放。
  创建者仍是会话的主人（删除、共享、列表里「我的」），列表与会话页写明「已由 X 接手」，想接回就对会话再「接手」一次。
- **凭证**：创建者的个人凭证不跟过去。仍可用的 Team / 平台凭证照旧；否则换成接手人可用的同一仓库凭证；没有就记为「已撤下」，发布前由执行人更换。
- **不搬**：原生聊天的上下文与记忆、被 `.gitignore` 忽略的文件（`node_modules`、`.env`、构建产物，新环境的检查会重新生成）。
  原执行环境里的工作区与原生会话保留不删（属于创建者）；接回时，原边界上旧的工作区目录改名为 `<run>.before-<时间>` 留存。
- **MCP**：`octopus_status` 带 `permissions`（`direct` / `takeover` 及原因）、`executed_by` 与 `takeover`；`octopus_takeover {run_id, request_id, expected_version}`
  发起接手（Skill 写明先征得用户同意，与控制台的确认一致）。
- **用量**：会话用量（D46 P6a）在经手不止一人时按人拆开（`usage.by`，会话页「本会话用量：…（alice：…；bob：…）」）：每个尝试算在它创建时的执行人名下，
  依据时间线里 `executor_id` 的变更（接手、改派）。
- **第 2 期**：不在分支上的旧候选随 bundle 带走；旧工作区的清理策略。

### 用户、Token 与管理员权限（访问管理第 1 期）

- **scope**：`read`（查看）、`execute`（会话操作）、`manage`（自己的配置：工作流、本人 Token 与密码、作为 Team owner 管理本 Team）、
  `admin`（平台管理：用户、Team 创建、执行环境、`/v1/admin/*`，以及查看与操作他人会话）。管理员网页登录自动带 `admin`；
  **已签发、不含 `admin` 的长期 Token（如 `claude MCP`）调管理接口返回 403**，查看与提交本人会话不受影响，需要时以管理员重新签发。
  **部署后管理员需重新登录一次**：此前的网页登录不含 `admin`，控制台顶部会提示「重新登录后才能使用管理功能」。
- **用户**：控制台「管理 › 用户」是一张表（用户名与管理员标记、状态、所在 Team、执行环境、最近登录、进行中的会话），工具栏可按用户名或 Team 搜索；「新建用户」在对话框里只填用户名与初始密码（可同时为个人空间分配共享环境）；每行「重设密码」「加入 Team」在行内，「⋯」里有改执行环境、Token（查看与撤销）、设为 / 取消管理员、停用 / 启用，每项都在对话框里完成并先说明后果（2026-10-24 改版，见 docs/design/2026-10-24-qingyan.md）；停用与重设密码立即撤销该用户全部 Token，
  会话照常执行；至少保留一位有效管理员，Team 唯一的有效 owner 不能被停用。「我的设置 › 修改密码」（账户菜单也可进入）后，本人其他登录与 Token 失效。
  API：`POST /v1/me/password`、`POST /v1/users/<id>/password|active|admin`、`GET /v1/admin/users`、`GET|DELETE /v1/admin/users/<id>/tokens[/<tid>]`。
- **给出权限的账号操作只认控制台登录（2026-10-27）**：
  - 每枚 Token 记下签发方式（`tokens.kind`）：只有 `POST /v1/login` 发出的是 `login`，其余都是 `api`，包括 `octopus connect`、控制台「API 与 Coding Agent 接入」与 `octopus login`。
  - 新建账号（`POST /v1/users`）、重设密码（`POST /v1/users/<id>/password`）、设为管理员（`…/admin {"admin": true}`）、恢复已停用的账号（`…/active {"active": true}`）用 `api` Token 调用时返回 403 `console_login_required`。原因：Agent 持有的管理员 Token 原本能借此新建一个自己知道密码的管理员、改掉别人的密码后登录，或把别人掌握密码的账号提为管理员。
  - 重设自己的密码只能走「我的设置 › 修改密码」（`/v1/me/password`，要输入原密码），否则返回 400 `use_me_password`。
  - 收回权限的操作仍可经 API：停用、取消管理员、撤销他人 Token。出事时用 Agent 或脚本也能及时收权。MCP 的 `octopus_admin_users` 因此只保留列出、停用、取消管理员。
  - 用 `api` Token 重新生成一枚 `login` Token，得到的是 `api` Token。
  - 控制台里的「来自控制台」也按签发方式判断，不再看 Token 名称；名称「Web login」保留给登录，签发时会被拒绝（400 `label_reserved`）。
  - **部署后需要重新登录一次控制台**：此前的登录 Token 没有记录签发方式，一律算作 `api`。
- **管理员越权**：可查看并操作全部会话（控制台侧栏「全部会话」，`GET /v1/runs?all=1`），原生 KiroCrew 入口不因管理员身份开放；
  越权操作与用户管理写入审计（`GET /v1/admin/audit`）。
- **Token 有效期（D62）**：签发时选 7 天、30 天、365 天或永不过期——控制台「API 与 Coding Agent 接入」的「有效期」（默认 30 天）、
  `POST /v1/tokens` 的 `expires_in`（`7d` / `30d` / `365d` / `never`）、`octopus connect … --expires`、`octopus login --expires`。
  永不过期的 Token 列表中显示「永不过期」（API 的 `expires` 为 `null`），只能撤销或随停用 / 重置密码失效。带 `admin` 的 Token 最长 30 天（403）。
  旧客户端只发 `ttl`（秒）时照旧，最长 30 天；新的 `connect` / `login` 同时发 `ttl`（所选天数，最长 30 天；未选时 connect 为 30 天、`--admin` 为 7 天），连到旧平台时按它签发。`--expires` 不能与 `--token` 同用（不签发新 Token）。
- **重新生成 Token（2026-10-24）**：控制台「API 与 Coding Agent 接入」每行「重新生成」，或 `POST /v1/tokens/<id>/regenerate`（可选 `expires_in`）：
  同一条 Token（名称、权限不变）换一个新值，只显示一次，旧值立即失效（401）；默认保持原到期时间，也可改有效期（带 `admin` 的最长 30 天）。
  当前登录正在用的 Token 返回 `409 token_in_use`，列表里标「当前登录」、不提供重新生成与撤销（退出登录即失效）；`GET /v1/tokens` 的 `current` 标出这一条。
- **错误语义**：看不到返回 404，看得到但不能做返回 403 并说明原因（例如普通成员删除他人创建的 Team 会话）。
- **预览链接** 10 分钟有效，每次取文件都核对持有人仍能查看该会话。

### 过期处理

现象：Attempt 失败类型为 `auth`，Session 进入等待并说明具体工具。
处理：`sudo -iu crew-personal` 重新登录对应工具；不需要重启平台，Session 在条件满足后继续（M2 起自动恢复）。
kiro-cli（D34）：Worker 每分钟以 `kiro-cli whoami` 检查登录，「管理 › 执行环境」的 Kiro 登录一列显示「已过期」（概览列在「需要处理」）；过期时使用 Kiro 的
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

- 发布目录：每次安装先删除 `/opt/kirocrew-on/releases/` 里的旧版本，保留当前、正在安装的与最新的 3 个（`prune_releases`），
  并删除 `incoming/` 里一天前的安装包；更早的版本要回滚时从构件桶 `releases/<sha>.tar.gz` 取回。2026-10-20 之前不清理：73 个版本（77 GB）
  占满了 100 GB 根卷，SSM 命令随之失败（`ipc messaging received timeout signal`）、安装中止。
- 扩容根卷（不中断服务）：`aws ec2 modify-volume --volume-id <根卷> --size <GiB>`，进入 optimizing 后经 SSM 执行
  `growpart /dev/nvme0n1 1 && xfs_growfs -d /`。不要改 `stack.env` 的 `VOLUME_SIZE_GIB`：卷大小在实例的 BlockDeviceMappings 里，
  CloudFormation 改它会替换实例。kirocrew-on-m1 的根卷 2026-10-20 由 100 GiB 扩到 500 GiB。

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

### 平台数据的持续备份与恢复（集群版控制面，MVP 0.2.0 P1）

只用于集群版控制面主机（不跑任何边界）；单机版与 m1 不跑 Litestream（S31）：Litestream 要持续写 S3，而跑 Agent 的主机，其实例角色同机进程都能用。

- **状态栈** `deploy/aws/kirocrew-on-state.yaml`：状态桶（版本控制、KMS 加密、只允许 TLS、禁止公开），`db/` 放数据库副本，P5 起 `escrow/` 放托管的密钥材料；托管策略 `ControlPlaneStateAccess` 只给控制面主机的角色，栈本身不附加给任何角色。
- **安装**：`STATE_BUCKET=<StateBucketName> bash deploy/install/litestream.sh`（root）。主机上有任何执行边界（`/etc/kirocrew-on/boundary-*.toml`）时拒绝执行；`al2023.sh` 不装 Litestream。它装上 Litestream（`deploy/versions.env` 固定版本，按 checksums 校验），写 `/etc/kirocrew-on/litestream.yml`（模板 `deploy/litestream/litestream.yml.template`：只复制 `/var/lib/octopus/platform/octopus.sqlite3`，`sync-interval: 1s`，快照每日一次，保留 72 h），启用 `octopus-litestream.service`（以 `octopus` 身份运行）。
- **看复制**：`journalctl -u octopus-litestream`；`litestream ltx -config /etc/kirocrew-on/litestream.yml /var/lib/octopus/platform/octopus.sqlite3` 列出副本里的 LTX 文件。
- **恢复**（新主机或温备上，先停 `octopus-api`）：

```bash
sudo -u octopus litestream restore -config /etc/kirocrew-on/litestream.yml -o /var/lib/octopus/restore/octopus.sqlite3 \
  /var/lib/octopus/platform/octopus.sqlite3
$P backup --source /var/lib/octopus/restore/octopus.sqlite3 --dest /var/lib/octopus/restore/check
$P restore-verify /var/lib/octopus/restore/check/octopus-<时间>.json --into /var/lib/octopus/restore/verified
# 校验通过后：把 verified/octopus.sqlite3 放到 /var/lib/octopus/platform/（属主 octopus，0600），再启动 octopus-api
```

云上验证（2026-10-26，临时 arm64 主机，没有 Agent）：每 20 ms 写一行，主机 `sysrq` 宕机后在另一台主机上恢复，用时 4.4 s，`restore-verify` 通过，RPO 0.69 s（`docs/research/2026-10-26-p0-results.md` 第 4 节）。

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

布局（负责人 2026-10-24，见 `docs/design/2026-10-24-qingyan.md` 第 4 节）：

- **顶栏中间是工作台**：开发空间切换和「会话 / 工作流」两个页签，工作流页的地址是 `?view=workflows`。
- **顶栏右侧**：「新建开发」，以及「管理」（只有管理员）和「我的设置」两个文字入口。手机上这两个入口收进账户菜单。
- **管理与「我的设置」**：页面里有自己的分区导航，地址是 `?view=admin&section=…`、`?view=settings&section=…`；顶栏左侧有「返回工作台」。
- **深色模式**：在账户菜单里切换。

```bash
npm --prefix web ci                     # 前端依赖（lockfile 固定版本）
npm --prefix web run build              # 构建到 octopus/ui/dist（主机安装时由 al2023.sh 执行）
npm --prefix web run dev                # 开发服务器 http://localhost:5173，代理 /v1 到 OCTOPUS_DEV_API（默认 8921 临时数据服务）
npm --prefix web test                   # 前端单元与组件测试（Vitest）
npm --prefix web run test:browser       # 浏览器回归：React 与 /classic/ 共 7 个文件，每个文件独立的 8921 临时数据服务
```

浏览器回归只使用 `tests/mvp_browser_fixture.py` 的临时 HTTP/SQLite 数据，不连接 Worker、不调用 Coding 工具。

## 15．发布物（MVP 0.2.0 P1、P2）

`npm --prefix web run build && python3 tools/build_release.py --out dist/release [--sign-key <PEM 私钥>] [--channel preview]`
只用标准库生成：

- `kirocrew_on-<版本>-py3-none-any.whl`：`octopus`、`composition`、控制台构建 `octopus/ui/dist`，以及放在 `octopus/_release/` 下的 `deploy/` 与 `skills/`（只打包 git 跟踪的文件，不带本部署自己的 `deploy/aws/stack.env`；代码经 `octopus.release.path()` 找到它们：检出里用检出的，装了 wheel 用 wheel 里的）。命令为 `kirocrew-on`（即 `python -m octopus`）；没有运行依赖。文件顺序与时间戳固定，同一提交构建出同一个哈希。
- `manifest.json`（结构见 `deploy/release/manifest.schema.json`）：版本、频道、wheel 的 sha256，以及这一版测过的组合，取自 `deploy/versions.env`：KiroCrew 的提交与版本、Node 版本及 x64 / arm64 包的 sha256（从 nodejs.org 的 `SHASUMS256.txt` 读，可用 `--node-sums` 指定文件）、kiro-cli、Claude Code、Codex、OpenCode。
- `SHA256SUMS`；给了 `--sign-key` 时另有 `manifest.sig`，用 `openssl dgst -sha256 -verify <公钥> -signature manifest.sig manifest.json` 验证。

频道（MVP 0.2.0 P2）：再加 `--index <feed 目录> --downloads https://<下载域名>/releases --feed https://<更新域名>/feed`（必须同时给 `--sign-key`），另出：

- `<feed>/<频道>/latest.json`（install.sh 不带 `--version` 时读它）与 `versions.json`；
- `<feed>/<频道>/simple/kirocrew-on/index.html`：PEP 503 索引，链接带 `#sha256=`（pip 渠道：`pip install --pre kirocrew-on --extra-index-url https://<更新域名>/feed/<频道>/simple/`）；
- 版本目录里的 `install.sh`：写入了两个地址与签名公钥的那一份（仓库里的 `deploy/install/install.sh` 没有，只能 `--check` 或 `--source`）。

目录布局：`<根>/releases/<版本>/`（字节，签过名）与 `<根>/feed/<频道>/`（可变的指针）。托管（S3 + CloudFront）与 KMS 签名随 CI 一起做；在那之前只发内部 `preview` 频道（设计 §17 #9 的默认）。

## 16．单机版（MVP 0.2.0 P2，设计 §4.2）

### 一行安装

在一台 EC2 上（推荐 Graviton、AL2023 arm64、根卷 ≥ 50 GiB）：

```
curl -fsSL https://<下载域名>/install.sh | sudo sh                        # stable 频道的最新版
curl -fsSL https://<下载域名>/install.sh | sudo sh -s -- --channel preview  # 或 --version 0.2.0rc1
curl -fsSL https://<下载域名>/install.sh | sudo sh -s -- --check            # 只做预检
```

- **预检**（`install.sh` 本身，POSIX sh，经管道运行时 Ubuntu 上是 dash）：
  - 支持 AL2023 与 Ubuntu 22.04 / 24.04，aarch64 或 x86_64；x86_64 照装，开头和结尾各提示一次推荐 ARM64；
  - 其他系统列出受支持的系统后以退出码 2 结束；
  - 一次列出所有不满足的项（root、systemd、空闲磁盘 ≥ 40 GiB——已装过的主机重跑只要 5 GiB、内存 ≥ 7.5 GiB、IMDSv2 可达），退出码 2，什么也不改。
- **取发布物**：先用内嵌公钥验 `manifest.sig`（没有签名不装），再核对清单里的版本与 wheel 的 sha256；任何一项不符都以退出码 3 停在改动之前。验过的 wheel 展开在 `/opt/kirocrew-on/incoming/<版本>/`，然后执行同一套安装步骤（`al2023.sh`，按 `os-lib.sh` 分 dnf / apt；Ubuntu 22.04 的 Python 3.12 由 uv 管理）。
- **CloudFront**：默认建小栈 `kirocrew-on-single`（`deploy/install/single-stack.sh up`），包括：
  - Elastic IP；
  - 主机安全组上一条只放行 CloudFront 源站前缀列表的规则；
  - 控制台、原生、预览三个分发，以及给更多边界用的 9 个原生槽位；
  - DLM 每日快照，按实例标签 `Backup=kirocrew-on-single`。
  - 源站密钥在主机上生成，只写本机 `/etc/kirocrew-on/origin-secret`，重跑不换。
  - `--no-cloudfront` 不建栈，控制台只经 SSM 端口转发打开（`portNumber=8918`，`http://localhost:8918`）。
- **安装用的权限**：给实例角色临时加上 `deploy/aws/kirocrew-on-single-install-policy.json`，或用有这些权限的 AK/SK（`sudo -E sh` 或 `sudo AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=… sh`，只在本次安装的进程里使用，不写盘）。缺权限时脚本列出需要的操作后以退出码 4 结束。
- **装完核对主机角色**（`single-stack.sh check-role`，S29）：用 `iam:SimulatePrincipalPolicy` 查角色是否还有写权限；有则打印要卸下的策略（保留 Bedrock 调用与 `AmazonSSMManagedInstanceCore`）。安装照常完成，结尾提示「主机角色的核对没有通过」。
- **结尾**打印控制台地址、管理员 `admin`、初始密码（只打印这一次，另存在 root 0600 的 `/etc/kirocrew-on/admin-password`）与下一步：在「我的设置 › 执行环境」里点「登录 Kiro」。
- **重跑**：每一步只补缺；栈没有变化时不执行变更集，含资源替换的变更集一律拒绝。
- **pip 渠道**：`pip install kirocrew-on` 后 `sudo kirocrew-on setup`：把装好的包排成同样的布局，交给 `install.sh --source` 执行预检与同一套步骤（不下载，pip 已按索引的 `#sha256=` 校验）。
- 主机上的 `kirocrew-on` 命令（`/usr/local/bin/kirocrew-on`）运行当前发布，例如 `sudo kirocrew-on --config /etc/kirocrew-on/platform.toml backup --dest <目录>`。

### 新建边界（控制台）

「管理 › 执行环境」右上角的「新建边界」（只在单机版上出现，需要控制台登录的管理员）：选属主，名称留空时用属主的用户名。

- 平台 API 以 `NoNewPrivileges` 运行，从不提权：它校验属主（存在、未停用、还没有自己的专属执行环境）与名称（同 `boundary.sh`），只把名称与用户名写进 `/var/lib/octopus/platform/boundary-requests/`。
- `octopus-boundary-provision.path` 发现请求后启动 root 的一次性服务（`deploy/install/boundary-provision.py`）：
  - 不跟随链接，只读 octopus 用户的普通文件；
  - 按 `boundary.sh` 的规则再校验一遍，只以这两个参数运行 `boundary.sh`，一次一个；
  - 进度写进 `/var/lib/octopus/boundary-provision/`（root 所有、octopus 组只读），像令牌的字符串先遮掉。
- 控制台显示排队 / 进行中 / 已完成 / 没有完成、脚本输出与下一步；失败的「再试一次」只补缺。新边界的原生入口先只经本机端口转发；要公网入口时运行下面的 `kirocrew-on single native <边界>`。
- 审计：`boundary.provision`（名称与属主）。

### 部署套件（`kirocrew-on single`，在你的电脑或 CloudShell 上运行）

装完后主机角色只留 Bedrock 调用权限，之后要改 AWS 的事由管理员在主机外用自己的凭证（aws CLI 的）执行；要在主机上做的经 SSM，管理员凭证不去主机。

| 命令 | 做什么 |
|---|---|
| `kirocrew-on single native <边界>` | 在栈里给该边界开一个原生分发（槽位 1–9，中转端口 8920–8928，源站密钥沿用栈里的值），再经 SSM 让主机渲染中转、改写 `crew_url` 并重启该边界（`deploy/install/boundary-native.sh`） |
| `kirocrew-on single backup --bucket <桶>` | 本机为一个对象签一条 15 分钟内有效的 PUT 预签名 URL，经 SSM 让主机做一致性备份并上传到 `s3://<桶>/kirocrew-on/backups/<实例>/<时间>.tar.gz`；用 `kirocrew-on restore-verify` 在别处核对 |
| `kirocrew-on single up` | 按这个版本的模板更新栈，保留栈里所有值 |

公共参数：`--region`（或 `AWS_REGION`）、`--stack`（默认 `kirocrew-on-single`）。「管理 › 部署」记下待执行的变更（`aws_requests`，参数里只有名称）并给出要运行的命令；命令带 `--request <编号> --platform <控制台地址> --token-file <管理员 API Token 文件>` 时，执行完回报，控制台不再显示命令。

### Docker 渠道（试用：一个容器、一个边界）

```
docker build -t kirocrew-on:<版本> --build-arg VERSION=<版本> .
docker run -d --name kirocrew-on -p 127.0.0.1:8918:8918 -v kirocrew-on:/var/lib/octopus -e AWS_REGION=us-east-1 \
  --cap-add SYS_ADMIN --security-opt apparmor=unconfined kirocrew-on:<版本>
```

- 依赖与主机相同，全部来自 `deploy/versions.env`、每个下载都校验；
- API 以 octopus 运行，Gateway、Worker 与所有 Agent 以 crew-personal 运行；
- 配置与数据在卷里，首次启动在 `docker logs` 打印一次管理员密码；
- Claude Code 与 Codex 经 Bedrock，需要给容器 AWS 凭证（EC2 上把实例元数据的跳数设为 2）；
- 原生 KiroCrew 入口不从容器里提供；
- 多架构镜像由 CI 构建。
