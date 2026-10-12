# 用户、Team、权限、Worker 与 MCP 角色调用（M4 子项）设计

日期：2026-10-12　·　状态：设计待负责人审阅　·　范围：`docs/MVP-RELEASE.md` M4「共享与组织」中的管理与权限部分

## 1．目标

让平台管理员与 Team owner 能日常管理用户、Team、执行环境与 Token，不再需要直接改数据库；
所有入口（控制台、`/v1` API、MCP）使用同一套角色与 scope 规则；Coding Agent 经 MCP 按调用者的角色使用 API。

现状与缺口见 §8（2026-10-12 盘点，均有代码位置）。

## 2．已确定（负责人，2026-10-12）

| 编号 | 决定 |
|---|---|
| A1 | 本期覆盖四项：用户与 Token、Team、Worker、MCP 与角色调用；权限模型与 403 / 404 语义贯穿各项 |
| A2 | 平台管理员可**查看并操作**全部会话（续作、诊断、停止、关闭、重开、删除、分配 Worker、观察、预览、导出）；**原生 KiroCrew 入口不因管理员身份放开**，仍按 K03 规则（个人空间本人、Team 专属环境成员、共享环境边界所有者） |
| A3 | 管理类操作开放给 MCP，但只接受带新 scope `admin` 的 Token；普通 `octopus connect` 不带 `admin` |

## 3．角色与 scope

### 3.1 角色

| 角色 | 来源 | 范围 |
|---|---|---|
| 平台管理员 | `users.admin = 1` | 平台：用户、Team、执行环境；A2 下的全部会话 |
| Team owner | `members.role = owner` | 本 Team：成员与角色、改名；Team 内全部会话（含删除） |
| Team member | `members.role = member` | 本 Team：提交与操作 Team 会话（删除限本人创建的） |
| 个人 | 会话创建者 | 本人个人空间的会话 |

### 3.2 scope

现有 `read`、`execute`、`manage`，新增 `admin`：

| scope | 允许 |
|---|---|
| `read` | 所有 GET，及只读 POST（`workflows/resolve`、`workflows/parse`、`runs/<id>/preview`） |
| `execute` | 会话与工作流推荐的写操作（`/v1/runs*`、`/v1/designs`） |
| `manage` | **自己的**配置：工作流保存 / 共享 / 归档、自己的 Token 与密码、作为 Team owner 管理本 Team 成员与改名 |
| `admin` | **平台管理**：用户、Team 创建 / 归档 / 托管成员 / 更换环境、全部 `/v1/workers*` 与 `/v1/admin/*`；以及 A2 中对他人会话的操作 |

- 管理员身份与 Token scope 同时满足才生效：越过他人资源的操作需要 `users.admin = 1` **且** Token 含 `admin`。
- 网页登录：管理员得到 `read, execute, manage, admin`，其他用户 `read, execute, manage`。签发 Token 时请求的 scope 不能超过本人可得的范围。
- 迁移：现有 Token 不改；管理员已签发的长期 Token 若含 `manage` 而不含 `admin`，管理接口将返回 403，需重新签发（发布说明中列出）。`bootstrap-worker`、`deploy.sh worker-host register` 经登录取得完整 scope，不受影响。

### 3.3 错误语义

- 401：未认证。404：资源不存在，或调用者**无权看到**它（不泄露存在性）。403：调用者能看到该资源，但角色或 scope 不允许此操作，消息说明缺什么（例如「只有 Team owner 可以删除他人创建的会话」「Token 缺少 admin scope」）。
- 按此统一：会话、工作流、Team、Worker 现有的 404 / 403 混用；`team_without_worker` 改为先查成员资格（不向非成员泄露 Team 名）。
- 控制台只启用调用者能执行的操作；不能执行时按钮停用并说明原因（例如普通成员的「删除会话」）。

### 3.4 审计

管理员对他人会话的操作（A2）、用户与 Team 的管理操作写入事件，记录操作者（id 与用户名）与动作；会话事件中可见「由管理员 xxx 停止」。新增 `audit` 表（时间、操作者、动作、对象类型与 id、摘要），`GET /v1/admin/audit`（admin）分页查看。

## 4．用户与 Token

| 操作 | 接口 | 权限 | 说明 |
|---|---|---|---|
| 改自己的密码 | `POST /v1/me/password {current, password}` | manage | 校验原密码；成功后吊销本人其他 Token（当前 Token 保留） |
| 重置他人密码 | `POST /v1/users/<id>/password {password}` | admin | 吊销该用户全部 Token |
| 停用 / 恢复 | `POST /v1/users/<id>/active {active}` | admin | 停用即吊销全部 Token、拒绝登录；历史会话、工作流与成员关系保留；是某 Team 唯一的有效 owner 时拒绝（先指定另一位 owner） |
| 升降管理员 | `POST /v1/users/<id>/admin {admin}` | admin | 至少保留一位有效管理员 |
| 管理员用户列表 | `GET /v1/admin/users` | admin | 用户名、管理员、有效、创建与最近登录时间、所在 Team 与角色、Token 数 |
| 普通用户列表 | `GET /v1/users` | read | 保持只返回有效用户的 id 与用户名（添加成员需要） |
| 用户的 Token | `GET /v1/admin/users/<id>/tokens`、`DELETE …/tokens/<tid>` | admin | 管理员查看与吊销他人 Token |

- 不提供删除用户（保留历史可追溯）；停用即可。
- Token：新增 `last_used`（同一 Token 每分钟最多更新一次）；登录时清理已过期 Token；列表不再显示过期 Token；登出删除当前 Token；`DELETE /v1/tokens/<id>` 不再要求请求体（2026-10-12 实测：不带请求体时返回 400）。
- 数据：迁移 3 增加 `users.last_login`、`tokens.last_used`。

## 5．Team

| 操作 | 接口 | 权限 |
|---|---|---|
| 改名 | `PATCH /v1/teams/<id> {name}` | owner（manage）或管理员（admin） |
| 归档 / 恢复 | `POST /v1/teams/<id>/archive {archived}` | admin |
| 托管成员与角色 | 现有 `POST / DELETE /v1/teams/<id>/members` | owner（manage）；管理员（admin）不必是成员 |
| 成员退出 | `DELETE /v1/teams/<id>/members {username: 自己}` | 成员本人（manage）；唯一 owner 不能退出 |
| 更换执行环境 | `POST /v1/teams/<id>/environment {worker_id}` | admin |

- 归档：拒绝新提交与新成员，从选择器中隐藏；已有会话可读、可停止；环境绑定保留，恢复后可继续用。不提供删除 Team。
- 更换执行环境：原环境为共享时移除服务关系；原环境为该 Team 的专属 Worker 主机时改为空闲（`spare`）；新环境为共享或空闲。旧会话不迁移（T6），按 §6.2 进入等待并可重新分配。
- 控制台：「团队与成员」中每个 Team 一页：成员与角色（增删、升降 owner）、执行环境（管理员可更换）、改名、归档；成员可「退出 Team」。
- 旧版控制台 `/classic/` 的「新建 Team」只传名称、现已必然失败：改为提示到新版控制台创建。

## 6．Worker

### 6.1 管理操作

| 操作 | 接口 | 说明 |
|---|---|---|
| 改名 | `PATCH /v1/workers/<id> {name}` | admin |
| 退役 | `POST /v1/workers/<id>/retire` | admin；仍是某 Team 的环境或有进行中的会话时拒绝；退役后不再出现在列表与选择器，历史会话保留引用（迁移 3 增加 `workers.retired`） |
| 更新连接 | 现有 `POST /v1/workers/<id>/connection`，控制台增加表单 | 用于 Worker 主机替换或 Token 轮换；`deploy.sh worker-host register` 已走此路径 |
| 刷新模型 | 现有 `GET /v1/workers/<id>/capabilities` | 放开给该环境服务的全部用户（含共享环境的个人与 Team 成员） |

### 6.2 派发前核对

Engine 每次派发前核对会话的 Worker 仍服务该会话所在空间且已启用；否则会话进入 `waiting_worker`，attention 说明「执行环境已更换 / 已移出 / 已停用」，由成员或管理员重新分配。已在执行的 Attempt 不中断，下一次派发时生效。`continue` 同样核对。

Worker Token 移出数据库（K10）不在本期，保留在 M4 后续。

## 7．MCP 与角色调用

### 7.1 `octopus connect`

- 以本人账号登录（不再默认 `admin`；未指定 `--username` 时交互询问），签发 `read, execute, manage`、30 天的 Token，完成后登出临时登录 Token。
- 管理员可加 `--admin` 额外签发含 `admin` 的 Token（A3）；Skill 提示管理 Token 的风险与用途。

### 7.2 工具

| 工具 | 接口 | scope | 说明 |
|---|---|---|---|
| `octopus_me` | `GET /v1/me` | read | 身份、是否管理员、Token scope、所在 Team 与角色 |
| `octopus_environments` | `GET /v1/workers`（按空间过滤） | read | 某空间可用的执行环境，供提交与分配 |
| `octopus_assign_worker` | `POST /v1/runs/<id>/assign-worker` | execute | `waiting_worker` 时由 Agent 分配 |
| `octopus_admin_users` | `GET /v1/admin/users`、`…/active`、`…/admin` | admin | 列表、停用 / 恢复、升降管理员；**不经 MCP 创建用户或设置密码**（密码不进入 Agent 对话） |
| `octopus_admin_teams` | Team 创建、改名、归档、成员、更换环境 | admin | |
| `octopus_admin_environments` | `GET /v1/admin/environments`、share / spaces / dedicate / enabled / retire | admin | |

- `octopus_start` 不再在客户端预选 Worker（服务端已自动选择，现有预选会选到空闲环境而失败）。
- 错误：MCP 返回结构化的 `{status, code, error}`；Skill 说明 401（重新连接）、403（缺 scope 或角色：告诉用户需要谁授权）、404（不存在或无权查看）、409（按 attention 处理）。

## 8．现状缺口（2026-10-12 盘点）

1. 无法改密码、重置、停用用户或吊销其 Token（`server.py:232-246`，`security.py:68` 的 `active` 无人设置）。
2. MCP `save_workflow` / `share_workflow` 在 `connect` 签发的 read+execute Token 下必然 403（`__main__.py:200-201`，`server.py:225-227`）。
3. 管理员不是 owner 时无法管理该 Team 成员，owner 离开后 Team 无法恢复（`domain.py:301`）。
4. Team 不能改名 / 归档，Worker 不能删除 / 改名，更换 Team 环境只能绕道（`server.py:260-265, 298-405`）。
5. Agent 无法处理 `waiting_worker`：没有分配 Worker 的 MCP 工具（`mcp.py:157-162`，`SKILL.md:67`）。
6. `connect` 默认 admin 账号并遗留 24 小时全权限 Token（`__main__.py:60, 199-202`）。
7. Token 从不清理、无最近使用时间、管理员不能吊销他人 Token（`server.py:254-258`）。
8. `manage` 同时包含个人配置与平台管理，无法签发“只能改工作流”的 Token（`server.py:225-227`）。
9. 403 / 404 不一致，控制台显示服务端必然拒绝的操作（`lib/session.ts:32-34`，`server.py:350-352`）。
10. 移出空间后会话仍派往原环境；预览链接在权限撤销后最多 1 小时仍有效（`domain.py:676-702`，`engine.py:187`，`server.py:71-84`）。

## 9．默认值（待确认）

| 编号 | 默认 |
|---|---|
| B1 | 普通用户仍可列出有效用户名（Team owner 添加成员需要）；管理员视图另见 §4 |
| B2 | 不删除用户、不删除 Team、Worker「删除」为退役；均保留历史 |
| B3 | 改自己的密码后吊销本人其他 Token；管理员重置后吊销该用户全部 Token |
| B4 | 停用某 Team 唯一的有效 owner 时拒绝 |
| B5 | 管理员的会话列表默认仍是本人与所在 Team；控制台提供「全部会话」切换（admin），API 为 `GET /v1/runs?all=1` |
| B6 | 预览链接改为 10 分钟有效，并在每次取文件时复核访问权限 |
| B7 | MCP 不创建用户、不设置密码 |

## 10．分期与验收

| 期 | 内容 | 验收 |
|---|---|---|
| 1 权限底座 | `admin` scope 与登录 scope、403 / 404 统一、管理员越权操作与审计、Token 清理与 `last_used`、用户生命周期（§4）、迁移 3 | 表驱动的权限矩阵测试（角色 × 操作 × scope → 状态码）；EC2 上以管理员与一个专用测试用户（验收后停用）逐项核对 |
| 2 Team 与 Worker | §5、§6（含派发前核对） | 测试 + EC2：改名、归档、管理员托管成员、成员退出、更换环境后旧会话进入等待并重新分配 |
| 3 MCP | §7 | 测试 + EC2：经 MCP 以普通 Token 与 admin Token 各完成一轮；403 时 Agent 给出正确说明 |

每期：spec → plan → 实施 → EC2 验收 → 负责人确认后推送。

## 11．测试

- Python：权限矩阵（参数化：管理员 / owner / 成员 / 非成员 / 个人 × 各接口 × scope 组合）、迁移 3 与备份、用户与 Token 生命周期、Team 归档与环境更换、Worker 退役与派发前核对、审计记录、MCP 工具与 scope 门禁。
- 控制台：管理员用户页、Team 页、按权限停用的操作与原因说明、全部会话切换。
- 回归：`tools/regress.sh`。
