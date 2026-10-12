# 会话变更记录与运行优化（D34）

## 1．目标

1. **会话的每一次变更都可审计**：人（控制台、Coding Agent、API）与系统（Engine、Worker 回执、KiroCrew 规划、自动纠错）对会话、任务、执行与纠错记录的每次写入，都留下操作者、客户端、字段前后值与原因，可查询、筛选、导出。
2. **续作只重跑受影响的任务**：用户或 Coding Agent 指定要重跑的任务，平台按依赖与审查规则补全必须重跑的任务，其余沿用上一版结果。
3. **任务列精确**：KiroCrew 规划给出结构化任务（交付物、验收项、验证命令、依赖、可改动路径），规划补全的需求与验收标准传给每个任务，结论逐项对照验收项。
4. **运行稳定**：运行时取消与结果丢失这类瞬时失败自动重试、不占纠错轮次；kiro-cli 登录过期在派发前发现并等待，不让任务失败；部署时 KiroCrew 未变不重启 Gateway。

## 2．依据（2026-10-12 线上数据，28 个会话、170 次执行）

| 观察 | 数据 |
|---|---|
| 重试与返工是耗时主因 | 77 个任务中 33 个一次通过；最多一个任务执行 10 次；24 次自动纠错中 14 次返工 |
| 续作重跑全部下游 | 「修订 2（纯前端主题）」中后端任务执行 340 s、未改任何代码 |
| 审查自定要求 | 规划补全的需求（`summary`，含验收标准）不传给后续任务；审查以「缺少 CHANGELOG.md」等任务说明之外的要求判失败 |
| 瞬时失败占用纠错 | 运行时取消 5 次、「工作者未完成任务或报告 BLOCKED」11 次（其中有已确认的误报）、kiro 会话过期 1 次 |
| 调度不是瓶颈 | 任务完成到下一次派发中位约 1 s；单任务执行中位 claude 257 s、kiro 128 s、codex 87 s |
| 现有记录有缺口 | 派发回执、Attempt 进入执行、纠错进展等写入不产生事件；命令事件不带参数（如分配了哪个 Worker）；系统变更无操作者与前后值 |

## 3．已确定（负责人，2026-10-12）

| 编号 | 决定 |
|---|---|
| S1 | 审计覆盖全部变更（人与系统），不做哈希链 |
| S2 | 本轮实施：续作只重跑受影响任务、任务列精确化、稳定性；交接精简、独立任务并行、自适应轮询作为后续 |
| S3 | 重跑范围：显式选择（控制台勾选或 Coding Agent 传 `rerun`）+ 平台按规则补全；KiroCrew 自动影响评估作为后续 |
| S4 | 审查尺度保持现状：审查 / 测试仍可因必须修复的问题判不通过；验收项作为明确依据传给它们，结论逐项列出，验收标准之外的发现写为「建议」 |
| S5 | 访问管理第 2 期的 P2 / P3 保持现状；本轮不改 Worker 管理 |

## 4．会话变更记录

### 4.1 记录模型

沿用 `events` 表，不新增表、不迁移。每条事件的 `payload` 增加：

| 字段 | 内容 |
|---|---|
| `actor` | `{"kind": "user", "id", "username", "client"}`；系统为 `{"kind": "engine" \| "worker" \| "kirocrew" \| "recovery"}` |
| `client` | 用户操作的来源：`console`（标签为 `Web login` 的 Token）、`mcp`（标签含 `MCP`）、`api`（其他） |
| `changes` | `{字段: [前值, 后值]}`；多行写入为 `[{"id", "changes"}]`。长文本（> 500 字符）只记 `{"chars", "sha256"}`；结果、证据只记长度与哈希 |
| `reason` | 可选：触发原因（如「派发前核对：执行环境已更换」「运行时取消，自动重试」） |

现有事件种类（`command.*`、`run.state`、`attempt.*`、`recovery.*`、`plan.applied`、`session.*`）保留，控制台时间线不受影响；此前没有事件的写入新增种类：`attempt.dispatched`、`attempt.running`、`run.binding`（原生父会话与工作区）、`run.settings`（纠错策略等快照变化）、`task.reset`、`recovery.state`、`attempt.retry`。

### 4.2 写入入口

新模块 `octopus/journal.py` 提供唯一的写入函数：`change(db, table, key, actor, kind, reason=None, **fields)`（单行：读出前值、只写变化的字段、记一条事件）与 `change_rows(db, table, where, params, actor, kind, reason=None, **fields)`（多行）。`domain`、`engine`、`planning`、`recovery` 中对 `runs`、`tasks`、`attempts`、`recoveries` 的 `UPDATE` 全部改用它。插入（会话、任务、执行、纠错的创建）已有事件，保持原样并补上 `actor`。

守护测试：扫描 `octopus/*.py`，`journal.py` 之外出现对这四张表的 `UPDATE` / `DELETE` 即失败，新代码无法绕过记录。

### 4.3 接口

- `GET /v1/runs/<id>/changes?who=user|system&kind=<前缀>&before=<id>&limit=<1–200>`：read，可见性同会话；按时间倒序；旧事件没有 `actor` 时标为系统（旧记录）。
- 导出（`GET /v1/runs/<id>/export`）增加 `changes`。
- 控制台会话页「变更记录」面板：倒序列出，筛选全部 / 人 / 系统；每行显示时间、操作者（用户名 · 控制台 / Coding Agent / API，或 Engine / Worker / KiroCrew / 协调者）、动作、字段前后值（长文本显示字符数）、原因。
- 修订历史（D32）保留，作为内容快照；MCP 查询工具并入访问管理第 3 期。
- 不记录：Worker Token、原生登录链接、原生输出全文。

## 5．稳定性

### 5.1 瞬时失败自动重试

Engine 在原生执行结束时归类：

| 类别 | 判定 | 处理 |
|---|---|---|
| 运行时取消 | 原生错误含 `stop_reason=cancelled` / `turn cancelled by the runtime`，且会话期望状态仍为 active（不是平台发出的停止） | 同一任务自动新建一次 Attempt 重试，不派发诊断、不计入纠错轮次 |
| 结果文本丢失 | 最终回复为空或 `_No response._`（不是单独一行 `BLOCKED`） | 执行契约 ≥ 2 且交接文件存在、`outcome.json` 有效且为 pass：照常判定（证据标注 `result_missing`）；否则同上自动重试一次 |
| 登录过期 | 原生错误含 `session has expired` 等认证提示 | `failure_type=auth`（需人工处理），不进入自动纠错循环 |

自动重试只做一次：本身就是自动重试的 Attempt（证据含 `transient_retry_of`）再次瞬时失败时，按现有规则进入纠错。归类与原生 `stop_reason` 写入证据与变更记录。

### 5.2 kiro-cli 登录预检

- Worker 以 `kiro-cli whoami` 检查登录（缓存 60 s，超时 5 s），健康信息增加 `tool_auth: {"kiro": "ok" | "expired" | "unknown"}`；管理员「执行环境」对话框显示。
- 派发使用 Kiro 后端的任务（规划、kiro 任务、协调者诊断、工作流设计）前，登录过期时 Worker 在写入派发记录之前拒绝，返回 503 `tool_auth_expired`；Engine 保持会话状态与 Attempt 不变，按退避重试，`error` 为「Worker 上 kiro-cli 登录已过期：请管理员执行 deploy.sh kiro-login」，登录恢复后自动继续。

### 5.3 部署不打断原生任务

安装脚本对 Gateway 计算指纹：KiroCrew 实际版本目录、本发布的 `composition/`、`octopus/config.py`、`octopus/prompts.py`、`octopus/__init__.py`、边界配置与环境文件、unit 文件。指纹未变且服务在运行时不重启 Gateway（Worker 与 API 照常重启，Engine 按派发记录核对）；变化时照旧重启并记录新指纹。

## 6．任务列精确化

### 6.1 任务结构

`steps()` 接受以下可选字段（旧工作流、模板不变）：

| 字段 | 约束 |
|---|---|
| `kind` | `implement` \| `review` \| `test` \| `deliver` \| `design` |
| `deliverables` | 字符串列表，≤ 20 项、每项 ≤ 500 字符 |
| `acceptance` | 可检查的验收项，同上 |
| `verify` | 验证命令，≤ 10 项、每项 ≤ 500 字符 |
| `depends_on` | 本流程中前序任务的 ID |
| `paths` | 本任务可改动的路径或 glob，≤ 20 项 |

KiroCrew 规划的任务（`planning.expand`）额外要求：每个任务有 `kind` 与至少 1 条 `acceptance`；`implement` / `test` 至少 1 条 `verify`；不满足时规划判为未通过并说明缺项，按现有纠错重新规划。

### 6.2 传给每个任务

- 派发请求增加 `requirement`（规划补全的意图与需求，含验收标准）与 `contract`（本任务的结构化字段）；`spec` 仍只含原有 6 个字段，未升级的 Worker 忽略新增内容、照常执行。
- Worker 指令增加「需求与验收标准（KiroCrew 规划补全）」与「本任务契约」两节；有 `depends_on` 时前序交接只含所依赖的任务。
- 质量契约：`outcome.json` 增加 `acceptance: [{"item", "result": "pass" | "fail" | "n/a", "evidence"}]` 与 `suggestions: [字符串]`；验收项逐项填写；验收标准之外、非必须修复的发现写入 `suggestions`（S4）。控制台在执行证据中显示逐项结果与建议。
- 协调者诊断的上下文同样包含需求与任务契约。

### 6.3 修订与对比

修订快照包含任务结构字段；修订对比显示这些字段的前后变化。

## 7．续作只重跑受影响的任务

- `continue` 增加可选 `rerun: [任务 ID]`：所选任务之后、要一并重跑的任务。省略时保持现有行为（所选任务及其后全部重跑）。
- 平台补全（不可取消）：所选任务一定重跑；`depends_on` 含有重跑任务的任务重跑；任何重跑任务之后的 `review` / `test` 任务重跑。
- 其余任务保持已验收状态与结果，后续任务的前序交接沿用它们的结果；会话视图对这些任务标注「沿用 v<N> 结果」。
- 修订记录 `rerun`（实际重跑）与 `kept`（沿用）列表，摘要如「续作「前端」：重跑 前端、测试，沿用 后端」；变更记录同时记下。
- 控制台续作对话框列出下游任务的勾选框（默认全选），被规则补全的任务勾选且不可取消并说明原因；MCP `octopus_continue` 增加 `rerun`，Skill 提示 Coding Agent 只选受影响的任务并说明理由。

## 8．后续（本轮不做）

交接精简（摘要 + 文件路径）；按依赖并行执行独立任务；自适应轮询；KiroCrew 自动影响评估（续作时由 Kiro 判定重跑范围并调整 Prompt）；MCP 变更查询工具（第 3 期）；工作流编辑器编辑任务结构字段；把各工具实测耗时与通过率提供给规划。

## 9．测试与验收

- Python：守护测试（四张表的写入只经 `journal`）；每类写入都有带操作者与前后值的事件；变更查询的筛选与可见性；瞬时失败重试一次且不计纠错；结果丢失但 outcome 有效时照常判定；登录过期时派发等待、不失败；Gateway 指纹；任务结构校验；规划缺项判未通过；派发请求携带需求与契约；`rerun` 补全规则与修订记录。
- 控制台：变更记录面板与筛选；续作对话框勾选与补全说明；「沿用」标注；验收逐项结果与建议。
- 回归：`tools/regress.sh`。
- EC2：平台与 `w1` 升级（各自确认无进行中的会话后部署）；用测试会话验证变更记录、续作只重跑所选任务、Gateway 未变时部署不重启；需要执行任务的验收先征得负责人同意。
