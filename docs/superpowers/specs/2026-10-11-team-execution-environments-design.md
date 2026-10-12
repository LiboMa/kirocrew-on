# Team 执行环境设计

状态：**已批准**（2026-10-11；同日修订为共享与专属两种环境，MVP 先实现共享环境；个人空间可共用共享环境；专属环境可使用第二台主机）。
依据 D28（修订）与 `docs/WORKERS.md`（连接、归属规则、隔离、记忆与 `al2023.sh` 的现状）。

## 1．目标与分期

创建 Team 时**必须**选择执行环境，不会再产生没有执行环境的 Team。执行环境有两种：

| | 共享环境 | 专属环境 |
|---|---|---|
| 是什么 | 管理员指定为「共享」的 Worker，同时服务多个空间（Team 与用户的个人空间） | 一个 Team 独占的执行边界：同一主机上的独立 OS 用户，或第二台主机 |
| 隔离 | 环境内所有会话的 Agent 以同一 OS 用户运行，互相能读源码、工具登录与记忆 | 不同边界之间隔开源码、工具登录、记忆与 Token |
| 适用 | 同一信任范围；MVP 阶段 | 有保密要求的 Team |
| 分期 | **第一期（MVP）** | 第二期（MVP 之后） |

第一期成功标准：
1. 管理员在控制台把现有 personal Worker 指定为共享环境（界面给出风险说明并要求确认）。
2. 新建 Team 必须选择执行环境；没有可选环境时表单禁用。为已存在的「Web应用软件研发组」分配共享环境后，该组成员经 Skill 提交的会话在共享 Worker 上执行到 `completed`；该组已在 `waiting_worker` 的会话（run-b74e9f0d…）分配后继续执行。
3. 管理员的个人会话不受影响，个人空间照常自动选用 Worker；管理员把共享环境分配给某个普通用户后，该用户的个人会话也在共享 Worker 上执行。
4. 未分配执行环境的 Team 拒绝新提交，并说明请管理员分配。
5. 共享环境中不同空间（Team、个人）的会话使用各自的 KiroCrew 成员，lessons 不互相注入；Gateway 不支持时，按 T7 如实标注「记忆未分隔」。

第二期成功标准见 §4.4。

## 2．已确定决策

| ID | 决策 |
|---|---|
| T1 | 两种执行环境并存（D28 修订）；创建 Team 时必须选择其一，创建 Team 与分配环境在同一事务中完成 |
| T2 | 只有平台管理员能创建 Team；创建时指定 Team owner 与执行环境，两者必填 |
| T3 | 共享环境：管理员把一个 Worker 指定为共享，并登记它服务的空间：Team，以及用户的个人空间（含原所有者；每个用户不必有自己的 Worker）。MVP 使用现有 personal Worker；生产环境建议单独开通一个 `shared` 边界，避免管理员个人会话与登录暴露给各 Team 的 Agent |
| T4 | 专属环境（第二期）：管理员开通空闲执行环境（同一主机上的新 OS 用户，或第二台主机），创建 Team 时分配；只分配一次，不改绑、不回收 |
| T5 | 已有但未分配执行环境的 Team 拒绝新提交（409），由管理员在 Web 补分配 |
| T6 | 一个 Team 同一时间只有一个执行环境。更换环境（如共享改为专属）不迁移已有会话：旧会话留在原环境，仍可查看与继续；新会话进入新环境 |
| T7 | 记忆：共享环境内每个空间（Team 或个人）使用自己的 KiroCrew 成员，分开积累记忆（只是分隔，不是保密）。Gateway 无法按会话绑定成员时，共享环境不做记忆分隔，并在控制台的环境说明中注明 |
| T8 | 不在本次：Team 的原生对话入口（仍只对执行边界所有者开放）；跨主机或容器的 Worker；kiro-cli 账号策略（共享环境中所有 Team 使用同一 kiro-cli 登录）；Team 解散与环境回收 |

## 3．第一期：共享环境（平台层）

### 3.1 数据（迁移 2）

- `workers` 增加列 `scope`（`dedicated` / `shared` / `spare`，默认 `dedicated`，现有行即为 `dedicated`）。不重建表（`runs.worker_id` 引用它）；现有约束「`owner_id` 与 `team_id` 恰有一个」保留：`shared` 与 `spare` 的 `owner_id` 记录登记或管理它的管理员，不表示服务范围。
- 新表 `worker_spaces(worker_id, team_id, user_id)`：共享 Worker 服务的空间，`team_id` 与 `user_id` 恰有一个，(worker_id, team_id)、(worker_id, user_id) 唯一；`team_id` 另设部分唯一索引，一个 Team 只出现在一个共享环境中（T6）。
- 迁移前按现有机制自动备份。总体设计中 M2（schema 2）与 M4（schema 3）的迁移编号顺延一位。

### 3.2 归属与选择规则

- Worker 属于某个空间：`dedicated` 且 `owner_id` / `team_id` 匹配；或 `shared` 且 `worker_spaces` 中有该空间。替换 `domain.py:337-341` 的判断。
- 共享 Worker 不受「Team 需要 `dedicated` Worker」的限制（`server.py:315-316`）：共享本身即声明不隔离，由管理员在设为共享时确认（T3）。该限制继续适用于专属环境。
- **服务端自动选择**：提交时未指定 Worker，且空间内恰好一个可用 Worker 时，由服务端选用（不再只依赖 MCP 客户端，`mcp.py:95-102` 保留为兼容）。
- Team 没有可用执行环境时，`POST /v1/runs` 返回 409 `team_without_worker`。

### 3.3 API

| 接口 | 权限 | 行为 |
|---|---|---|
| `POST /v1/workers/<id>/share`，`{"confirm": true}` | 管理员 | 把 `dedicated` Worker 改为 `shared`，原所有空间写入 `worker_spaces`；必须显式确认 |
| `POST /v1/teams`，`{"name", "owner", "worker_id"}` | 管理员 | 一个事务：创建 Team；`owner`（用户名）设为 Team owner；`worker_id` 为共享 Worker 时写入 `worker_spaces`（第二期：为空闲 Worker 时改为该 Team 的专属）；缺失或不可用时 409 |
| `POST /v1/workers/<id>/spaces`，`{"team_id"}` 或 `{"user_id"}` | 管理员 | 把空间加入共享 Worker 的服务范围；Team 已有执行环境时 409（T6）。`DELETE` 同一路径移出（移出后该空间没有执行环境，旧会话不迁移） |
| `POST /v1/users`，增加可选 `worker_id` | 管理员 | 创建用户时把其个人空间加入共享 Worker |
| `GET /v1/teams` | 登录用户 | 现有行为；管理员额外返回全部 Team 及其执行环境（Worker、`shared` / `dedicated`） |
| `GET /v1/workers` | 现有 | 增加 `scope`；管理员可见共享 Worker 服务的空间 |

### 3.4 记忆分隔（T7）

- 核实结果（2026-10-11，KiroCrew 0.10.0）：Gateway 创建会话时支持 `memory_mode`（`dashboard/chat_handlers.py:810-817`）；绑定成员的会话只能经 `POST /api/members/<slug>/thread` 为**已配置**的成员开启（`member-*` 键保留），路由中没有创建成员的接口，平台派生的子任务是否继承成员记忆也无法确认。
- 因此第一期按 T7 回退：共享环境不做记忆分隔，控制台的共享说明注明「记忆未分隔」。KiroCrew 提供稳定的成员创建与继承接口后另行实现。

### 3.5 控制台

- 「执行环境 · Worker」：「设为共享环境」，确认框说明「共享环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆，只用于同一信任范围」。
- 「团队与成员」：管理员的用户列表显示每个用户个人空间的执行环境，可「分配共享环境」；创建用户时可选择共享环境。「新建 Team」只对管理员显示；表单为名称、Team owner、执行环境（共享环境与第二期的空闲环境，必选）。没有可选环境时表单禁用并说明。
- Team 列表显示执行环境类型；未分配的显示「分配执行环境」（管理员）。
- 会话详情与发起向导：Team 使用共享环境时显示「共享执行环境」标记；未分配环境的 Team 在向导中禁止提交并说明原因。

### 3.6 Coding Agent

`octopus_start` 提交到未分配执行环境的 Team 时返回 409 与原因；Skill 指示原样告诉用户。

## 4．第二期：专属环境（主机层）

专属环境有两种形态：
- **独立 Worker 主机**（2026-10-11 确定先实现，§4.0）：独立 EC2（独立实例角色与内核）运行 Gateway 与 Worker。
- **同一主机上的新 OS 用户**（§4.1–4.3）：开通快，但与其他边界共享实例角色与内核；后续按需实现。

### 4.0 独立 Worker 主机

- **规模**：先开 1 台 m7i.xlarge 跑通全链路，再扩到 3 台；上线后登记为空闲专属环境，由管理员在控制台分配（第一台先分配给「Web应用软件研发组」验收）。
- **栈**：每台一个 CloudFormation 栈 `<平台栈>-worker-<名称>`（`deploy/aws/kirocrew-on-worker.yaml`），位于平台的 VPC 与公有子网（出站用公有 IP，无入站公网访问）；安全组只允许平台主机安全组访问 Worker 的 TLS 端口；实例角色为 SSM、Bedrock 与读取发布包。平台栈增加输出 `VpcId`、`SubnetId`、`HostSecurityGroupId`、`ImageId`（只加输出，不替换资源）。
- **安装**：`al2023.sh` 增加 Worker 主机模式：安装 KiroCrew、kiro-cli、Coding 工具与一个执行边界（`dedicated`），不安装平台 API、管理员与原生对话入口；nginx 在 TLS 端口终结 TLS 并转发到回环 Worker。
- **TLS**：平台主机持有私有 CA（私钥 root 0600）；Worker 主机生成私钥与 CSR，私钥不离开主机；平台主机签发证书（SAN 为 Worker 主机私网 IP）；平台以 `worker_ca_file` 只信任该 CA。保持「非本机 Worker 必须 HTTPS」（`transport.py:33-34`）。
- **登记**：Worker 主机启动时生成 Worker Token；`deploy.sh` 经 SSM 取得 Token，在平台主机上以 `bootstrap-worker --spare` 登记为空闲专属环境（Token 会留在 SSM 命令历史中，与管理员密码读取的取舍相同）。
- **kiro-cli**：每台 Worker 主机需单独完成一次设备码登录（`deploy.sh kiro-login` 支持指定 Worker 主机）。

### 4.0.1 原生 KiroCrew 入口（经平台主机中转，2026-10-12 确定）

```
浏览器 → <Worker 主机> 的原生入口 CloudFront 分发（源站 = 平台主机:中转端口，注入平台源站密钥）
       → 平台主机 nginx 中转端口（校验源站密钥，与 :NativePort 相同）
       → 私网 TLS（proxy_ssl_verify，只信任平台私有 CA）→ Worker 主机 nginx :8944 → 回环 Gateway
Worker 主机边界 crew_url = https://<该分发域名>
```

- Worker 主机仍只接受平台主机安全组的访问；源站密钥只在平台主机上（CloudFront 与平台 nginx 持有）。
- 中转端口从 NativePort + 1 起按 Worker 主机分配（记录在 Worker 栈参数中，更新时不变）；平台安全组 CloudFront 规则的上限为参数 `NativeRelayEnd`（默认 NativePort + 10）。
- nginx 按 DNS 名校验上游证书，Worker 主机证书的 SAN 同时包含私网 IP 与 `<名称>.worker.kirocrew-on`。
- 打开权限不变：Team 专属环境的成员；共享环境只有边界所有者（§3.4 之后的 C1 修复）。

### 4.0.2 管理收口

- **运维一条命令**：`deploy.sh worker-host add <名称>` 依次完成栈、安装、TLS、原生入口中转与登记，并最后给出 kiro-cli 设备码；分步命令保留用于排障。
- **管理员 API**：`GET /v1/admin/environments` 在一处给出全部执行环境：范围、服务的 Team 与用户（含名称）、启用状态、实时健康（可达、版本、隔离、原生入口来源）、进行中会话数；设为共享、服务空间、连接更新、启停的接口在 OpenAPI 中归入同一分组「执行环境（管理员）」。
- **控制台**：「执行环境」对话框基于该接口，成为执行环境的统一管理入口（共享、分配给 Team 或用户、启停、健康）；新建 Team 时选择环境保留。

### 4.1 命令（`deploy/aws/deploy.sh`）

| 命令 | 作用 |
|---|---|
| `boundary add <名称>` | 经 SSM 开通执行边界并登记为空闲执行环境（`scope = spare`）；名称 `[a-z][a-z0-9-]{1,19}`，OS 用户 `crew-<名称>` |
| `boundaries` | 列出边界、端口、服务状态与平台登记状态 |
| `kiro-login [<边界>]` | 以 `crew-<边界>` 运行 kiro-cli 设备码登录；省略时为 `stack.env` 的 `BOUNDARY` |
| `space <run-id>` | 按 `boundaries/*/worker/<run-id>` 定位会话所在边界 |

### 4.2 安装脚本拆分

- 新增 `deploy/install/boundary.sh <名称> [--spare]`：OS 用户与目录 → 边界环境 → 边界配置 → 原生契约检查 → 启动 Gateway 与 Worker → 登记（`--spare` 为空闲执行环境，否则为管理员个人空间）。
- 端口取已有 `boundary-*.toml` 中 `worker_port`、`gateway_port` 的最大值加 1；空闲边界写入 `worker_isolation = "dedicated"`。
- `al2023.sh` 保留主机层，对 `stack.env` 的 `BOUNDARY` 调用 `boundary.sh`；`boundary add` 只运行 `boundary.sh --spare`，不重启平台 API。
- 升级：对全部 `boundary-*.toml` 做原生契约检查，全部通过才切换 `current` 并重启各边界；任一失败则保持旧发布。

### 4.3 平台

- `POST /v1/workers`，`{"name", "spare": true, "connection"}`（管理员）：必须 `dedicated`，执行边界未登记过（含停用），写入 `scope = spare`。
- 分配空闲环境：`scope` 改为 `dedicated` 并写入 `team_id`；只分配一次（T4）。

### 4.4 第二期成功标准

1. `deploy.sh boundary add team-a` 后控制台出现空闲环境；新建 Team 时选择它即成为该 Team 的专属环境，成员提交的会话在 `team-a` 上完成。
2. `crew-team-a` 读不到 personal 边界的 Worker Token、Gateway 密钥与工作区；不带 Token 调用 personal 的 Worker 返回 401。
3. `deploy.sh push` 后所有边界运行新版本；任一边界契约检查失败时不切换。

## 5．错误与边界情况

| 情况 | 处理 |
|---|---|
| 两个管理员同时把同一空闲环境分配给不同 Team（第二期） | 事务内校验 `scope = spare`，后到者 409「该执行环境已分配」 |
| 共享 Worker 被停用 | 它服务的所有空间视为没有可用 Worker：拒绝新提交，提示联系管理员 |
| 未确认就把 Worker 设为共享 | 400，要求 `confirm` |
| 成员创建或绑定失败（T7） | 会话照常执行，环境说明标注「记忆未分隔」，事件中记录原因 |
| Team 更换执行环境 | 按 T6，旧会话不迁移 |
| `boundary add` 时平台 API 不可用（第二期） | 服务已启动、登记失败：命令以非零退出；再次执行只补登记 |

## 6．测试

- **Python（第一期）**：迁移 2（现有 Worker 迁为 `dedicated`、备份）；设为共享须确认、原空间写入 `worker_spaces`；归属判断（专属 / 共享）；服务端自动选择；只有管理员能建 Team 且必须带环境；补分配；未分配 Team 拒绝提交；管理员 Team 列表。
- **控制台（第一期）**：设为共享的确认、新建 Team 表单（必选环境、无可选时禁用）、补分配、共享标记、向导拦截。
- **第二期**：空闲登记规则；`boundary add` / `boundaries` / `kiro-login <边界>` 的 dry-run；`boundary.sh` 端口分配与 `dedicated` 静态检查；升级覆盖全部边界。
- **EC2 验收**：第一期按 §1 标准 1–5；第二期按 §4.4。

## 7．交付顺序

1. 核实 T7 所需的 Gateway 能力（成员创建、子任务继承成员记忆）。
2. 平台：迁移 2、归属规则、服务端自动选择、API、Python 测试。
3. 控制台：共享设置、Team 表单、补分配、标记与拦截。
4. EC2：发布 → personal 设为共享 → 为「Web应用软件研发组」分配 → 成员经 Skill 提交并完成 → run-b74e9f0d 继续 → 新建 Team 流程 → 为一个普通用户分配共享环境并完成一个个人会话。
5. 第二期另行排期：主机层与专属环境。
