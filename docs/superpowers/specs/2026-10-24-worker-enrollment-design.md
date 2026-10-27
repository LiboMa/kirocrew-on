# Worker 自助接入：一次性接入码（设计草案，放到 MVP 0.2.0）

日期：2026-10-24　·　状态：负责人 2026-10-24 决定放到 MVP 0.2.0，与 BACKLOG「MVP 0.2.0 重要更新」里的 Worker 自助申请一起设计，第 7 节的三点届时再定　·　来源：BACKLOG「客户端与控制台」——「Worker 自助接入（一次性接入码，Worker 启动时自行登记 URL 与 Token），本地全栈启动时自动登记 Worker」

## 1．为什么做

今天登记一个 Worker，管理员要做这些事：

1. 在 Worker 主机上，找到 Worker 自己生成的 `worker.token`；
2. 把 Token 和 Worker 地址抄进控制台「登记 Worker」，或者交给 `bootstrap-worker`；
3. 平台带着这个 Token 调一次 Worker 的 `/health`，核实之后，把 Token 用凭证主密钥密封入库。

这套做法有三个问题：

- **Token 要经人手**：Token 是能完全代表平台去调用 Worker 的密钥，却要靠人复制粘贴。`deploy.sh worker-host add` 会经 SSM 读取 Token，所以 Token 留在了 SSM 的命令历史里（`deploy.sh:588` 的注释也承认这一点）。
- **步骤多、容易错**：每人一个边界（D49）之后，执行环境会越来越多，每加一个都要重复抄一次。
- **本地全栈不能直接用**：`tools/local_stack.sh` 启动后，还要手工建管理员、手工登记 Worker。

## 2．目标与不做的事

**目标**

- 管理员在控制台签发一个**一次性接入码**，签发时就定好登记到哪里、叫什么名字。Worker 主机上执行一条命令，Worker 用接入码完成登记。Token 不经过人手，也不出现在任何命令历史里。
- 登记规则与今天完全相同：隔离、唯一性、宿主账号、空间绑定。平台照旧带 Token 调 Worker 的 `/health` 核实。
- 本地全栈启动后，自动登记本机 Worker。

**不做**

- Worker 主动与平台保持长连接，或做反向隧道：平台仍然经私网调用 Worker。
- 用接入码轮换 Token、替换主机：仍走现有的「编辑 › 更换连接」，留到第 2 期再考虑。
- mTLS、KMS、Worker 证书自动签发：TLS 仍走现有的 `deploy.sh worker-host tls`。

## 3．流程

| # | 谁 | 做什么 | 平台做什么 |
|---|---|---|---|
| 1 | 管理员 | 「管理 › 执行环境 › 登记 Worker」选「用接入码」，填名称，选登记到哪里（某人的个人空间 / 某个 Team / 空闲主机）和有效期 | 生成接入码，只存它的哈希，以及绑定、有效期和签发人。接入码只显示一次，同时给出一条可直接复制的命令 |
| 2 | 管理员或主机脚本 | 在 Worker 主机上运行 `octopus --config <边界>.toml worker enroll --platform https://<控制台域名> --url https://<私网地址>:8943`，接入码从标准输入或环境变量读入 | — |
| 3 | Worker | 读取本机的 `worker.token`，把接入码、自己的地址和 Token 发给 `POST /v1/workers/enroll` | 核对接入码：存在、未过期、未使用。然后按 `POST /v1/workers` 的全部规则校验，并带 Token 调 Worker 的 `/health` 核实 |
| 4 | 平台 | 校验通过 | 密封 Token 入库；接入码标为已用；写审计 `worker.enroll`（记签发人、接入码编号、绑定，不记 URL 和 Token）；控制台里的新行高亮 |
| 5 | 管理员 | 在控制台看到新的执行环境 | — |

**失败时**：接入码保持未使用，可以修正后重试，直到过期。同一个接入码连续失败 5 次后作废，防止被拿来试探。

## 4．安全

| 今天的保证 | 接入码之后 |
|---|---|
| 只有管理员能建立或改变绑定（账号是管理员，且 Token 带 admin，A3） | 接入码只能由管理员签发，绑定在签发时定死。Worker 自己不能选空间，也不能改名 |
| 平台带 Token 调 `/health`，确认 Worker 确实持有它 | 不变：自报的 URL 和 Token 同样要经过这一步。接入码只代替「谁有权登记」，不代替「Worker 是真的」 |
| URL 规则：远程必须 HTTPS，并只信任平台私有 CA；HTTP 只允许回环地址；URL 不带用户名密码、路径和 query；Token 至少 32 字符 | 不变，直接复用 `validate_worker` |
| 隔离、唯一性、宿主账号规则 | 不变，与 `POST /v1/workers` 走同一段代码 |
| Token 入库前密封，API 和审计都不回显 | 不变。另外 Token 不再出现在 SSM 的命令历史和人的剪贴板里 |
| 平台 API 只接受经 CloudFront 且带源站密钥的请求 | 不变：`enroll` 也是一次普通的 HTTPS API 调用，经 CloudFront 进来。它不带 Bearer Token，只凭接入码 |

接入码本身的要求：

- **生成与保存**：32 字节随机数，前缀 `kce_`，库里只存 SHA-256；
- **一次性**：只能使用一次；
- **有效期**：默认 30 分钟，最长 24 小时；
- **撤销**：可以撤销，在控制台的「接入码」列表里查看与撤销；
- **防试探**：错误的接入码一律返回 404，并按来源限速。

## 5．接口与数据

- 迁移：新表 `worker_enrollments`，字段为 `id, code_hash, name, binding_kind (user|team|spare), binding_id, created_by, created, expires, used, used_by_worker, failures`。
- 新增接口：
  - `POST /v1/workers/enrollments`（admin）：签发接入码，body 为 `{name, owner_id | team_id | spare, expires_in}`，返回 `{id, code, expires, command}`。`code` 只在这一次返回。
  - `GET /v1/workers/enrollments`（admin）：列出未用和最近用过的接入码，不含码本身。
  - `DELETE /v1/workers/enrollments/<id>`（admin）：撤销。
  - `POST /v1/workers/enroll`（不需要登录，凭接入码）：body 为 `{code, url, token}`，返回 `{id, name, scope}`，不回显 Token。
- CLI：
  - `octopus worker enroll --platform <URL> --url <Worker 地址> [--code-stdin]`：默认从环境变量 `OCTOPUS_ENROLL_CODE` 读取接入码，避免它出现在 `ps` 或 shell 历史里；
  - `--check` 只核对、不登记。
- 控制台：在「登记 Worker」对话框里加第二种方式「用接入码」，提交后同一个对话框切到一次性显示的那一屏，显示接入码和命令，带「复制」和「我已保存」。执行环境页增加「接入码」小节，列出未用的接入码，可以撤销。
- 部署脚本：
  - `deploy.sh boundary add` 和 `worker-host add` 改为签发接入码后在主机上执行 `worker enroll`，不再读取 `worker.token`；
  - `bootstrap-worker` 保留，供旧流程和回滚使用。
- 本地全栈：`tools/local_stack.sh start` 在 API 就绪后，没有管理员就先建一个（随机密码写进 `state/local-e2e/admin-password`，权限 0600），再签发接入码登记本机 Worker。按现有规则，它只能登记为管理员个人空间的 trusted-local。

## 6．分期

| 期 | 内容 |
|---|---|
| 第 1 期 | 接入码表与四个接口、`worker enroll` 命令、控制台「用接入码」与接入码列表、本地全栈自动登记、测试（接入码的一次性、过期、撤销、失败计数；登记规则与 `POST /v1/workers` 一致；Token 不回显） |
| 第 2 期 | `deploy.sh boundary add` 与 `worker-host add` 改用接入码；OPERATIONS 与 WORKERS 文档更新；EC2 上新加一个边界做实测 |
| 以后 | 用接入码更换连接（轮换 Token、替换主机）；成员为自己的个人空间签发接入码（见第 7 节第 1 点） |

## 7．需要负责人决定

1. **谁能签发接入码**：
   - 我的默认：只有管理员（与今天的 A3 一致）。
   - 另一种：每个人也能为**自己的个人空间**签发，只能用于 dedicated 隔离，更接近「每人一个执行环境、自己接入」。这样做等于放宽了今天「绑定只由管理员建立」的底线，需要你点头。
2. **有效期**：我建议默认 30 分钟，最长 24 小时。
3. **部署脚本什么时候改**：第 2 期再改；也可以并进第 1 期，那样 Token 立刻不再出现在 SSM 的命令历史里。

## 8．顺带发现、与本设计无关的两处不一致（先记 BACKLOG）

- `GET /v1/workers/<id>/capabilities` 只核对 `boundary`，没有像引擎和原生入口那样同时核对 `host_principal` 和 `isolation`。
- 非 spare 的 Worker 登记时，只和**已启用**的 Worker 比对 `boundary`，所以一个已停用 Worker 的边界，可以再登记给另一个人或 Team。
