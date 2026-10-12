# 旧版自动生成与编排参考（M2 Pipeline 生成的设计输入）

整理日期：2026-10-11。依据：`experiments/kirocrew-app-poc`（旧版运行目录）与本仓库迁入代码的只读核对。
决策依据：D13（自动生成任务参考旧版实现）。本文只描述结构与机制，不引用任何用户需求原文。

## 0．两份代码的一致性

| 文件 | 旧版运行目录 vs 本仓库迁入版本 |
|---|---|
| `workflow_library.py`、`development_prompts.py`、`pipeline_spec.py`、`pipeline.py`、`workflow.py`、`workflow_files.py`、`workflow_sessions.py`、`workflow_workspace.py`、`poc.py`、`ui/workflow-templates.json`、`ui/workflow-editor-store.js`、`ui/workflow-app.js`、`examples/generated-review-workflow.{md,yaml}` | 逐字节相同 |
| `api.py` | 本仓库版 `chat_entry_url` 多 `home`、`origin` 参数（新版 crew-entry 需要） |
| `test_workflow_library.py` | 本仓库版新增 `TemplateTests`（12 个模板逐个校验并三格式往返） |
| `workflows/*.json` | 旧版运行目录另有 9 个运行期 `flow-*.json`（未迁入）；`development.json`、`ui-optimize-three-stage.json` 版本不同 |

新版已有 `octopus/designs.py`，复用了 `intent_prompt` 与 `parse_document`，把生成任务作为单阶段 kiro Run 交给 Engine。

## 1．需求 → 工作流生成（Workflow Studio 8917）

**入口**：`workflow.py` 路由 `POST /api/design {intent, request_id}` → `workflow_library.start_design`；`GET /api/design?id=` → `design_status`；`POST /api/design/app-entry` 打开生成对话。

**`workflow_library.start_design`（:239-263）**
- 输入校验：intent 3–12000 字符、无 NUL；`request_id` 为 UUID。
- 幂等：`design_id = "design-" + uuid.hex`，在 `.lock` 上加锁；同 ID 同 intent 返回原 job，不同 intent 报冲突。
- 落盘 `state/workflow-designs/<id>/job.json`：`{id, intent, status:"preparing", mode:"assistant_json", created_at, slot, workflow_id}`；后台线程执行 `_generate`。

**`_generate`（:266-284）**
- 原生调用：`POST /api/chat/slots {agent:"poc-kiro", agent_kind:"template", model:"auto"}` → `POST /api/chat?ws=1 {slot, message}`。
- 这是一个**顶层原生聊天 slot**，不是 `spawn_run`；`poc-kiro` 是 worker 模板（read / write / shell，无 kirocrew-core）。
- 成功置 `submitted`，异常置 `uncertain`。

**`development_prompts.intent_prompt`（:55-77）的关键规则**
- 把意图转换成可实施、可验证的研发任务，不缩减为演示页面；未知信息写为待核实条件，不臆造。
- 可在设计目录检出用户明确提供的仓库辅助设计。
- 最终回复只输出一个完整 JSON 代码块，按给定骨架；1–8 个顺序任务，Task id 唯一（小写字母、数字、短横线）；先定义任务再选工具。
- 工具只能是 kiro / claude / codex / opencode；未指定时默认 Kiro 分析、Claude 编码、Codex 审查、OpenCode 交付。
- 没有可靠的模型候选时 `model=auto`、`effort=""`，不编造模型名。

**`design_status`（:287-332）**
- 已有 `workflow.json`：校验非符号链接、≤100 KB → `parse_document` → `validate`，强制 `id=job.workflow_id, revision=0`，返回 `ready`；失败返回 `invalid` 且不回退到模板。
- `submitted`：读取 slot，对话结束后取最后一条非空 assistant 消息，以 `{` 开头按 JSON 解析，否则按 Markdown（恰好一个代码块）解析，校验通过才写 `workflow.json`。
- 派生状态：待批准 → `awaiting_approval`；运行中 → `running` / `waiting`；超过 180 s 未运行 → `needs_attention`。
- **缺陷**：派生状态不回写 `job.json`（旧版运行目录中 14 个 job 全部停在 `submitted`）；后端没有迟到结果保护，`workflow.json` 以第一份通过校验的结果为准。

**解析与校验**
- `workflow_files.parse_document`（:30-74）：非空、≤100 KB；Markdown 恰好一个 yaml / yml / json / workflow 代码块；JSON 拒绝重复键；YAML 使用 SafeLoader，禁止 alias / anchor，键必须是唯一字符串。
- `workflow.validate`（:120-153）：顶层键 ⊆ `{schema,id,name,revision,steps}`、`schema==1`、1–8 个 step；step 键 ⊆ `{id,name,tool,model,effort,prompt}`；model ≤200 且不含空白和方括号；effort 必须在该工具允许列表；`ident` 为 `^[a-z][a-z0-9-]{0,47}$`；name ≤80（NFKC、无控制字符）；prompt ≤4000；step id 不重复。
- 生成阶段不检查模型候选（到 prepare / submit 才查）与名称唯一（到 save 才查）。

**采用与草稿隔离（前端）**
- 采用（`ui/workflow-app.js:537-545`）：仅当 `ready`、未采用且生成时的 intent 与当前需求框一致；采用后保留当前草稿的 id / revision，仍需用户 `save(expected_revision)`。
- 隔离（`ui/workflow-editor-store.js:30-35` `updateGeneration`）：scope 当前的 generation id 不同则丢弃结果；`receiveGeneration`（`workflow-app.js:502-507`）只刷新匹配的 scope；需求改动后旧结果不得覆盖（:513）；`uncertain` 且 intent 相同时复用原 request_id（:529）。

| 项 | 处理 | 理由 |
|---|---|---|
| `intent_prompt` 规则 | 复用（新版 `designs.py` 已复用） | 规则成熟；去掉 PoC 措辞 |
| `parse_document` | 复用 | 纯函数，安全约束完整 |
| `validate` 规则 | 并入 `octopus/pipelines.py` 的 schema 校验 | 规则保留，载体换成平台 Pipeline schema |
| request_id 幂等 + 内容冲突 | 复用语义 | 由平台命令回执承担 |
| 后台线程 + 聊天 slot + 抓最后一条消息 | 丢弃 | Engine 是唯一派发者；结果来自 Attempt 的 outcome 与交接文件 |
| `job.json` 文件状态机 | 丢弃 | 状态不回写、无终态；改存 SQLite（Request 与 Design） |
| 前端 scope / id 守卫与迟到判断 | 复用 | 草稿隔离模式仍然有效 |

## 2．Pipeline 规划阶段（固定四阶段，8916）

**`pipeline_spec.STAGES`（:3-10）**：plan / Kiro CLI → code / Claude Code → review / Codex → deliver / OpenCode；Agent 为 `poc-pipeline-<stage>`。

**`requested_tasks(run)`（:51-94）生成各阶段任务正文**
- plan：写 SPEC.md（行为、导出函数的输入输出、UI、验收）与 plan.md；写 `tests/acceptance.test.mjs`，至少 3 个带断言的测试，禁止 skip / todo，且必须在空基线上失败。
- code：实现核心模块，不改规格与测试。
- review：输出 `review.json {verdict, candidate_sha256, tests_passed, findings[{severity, description}], summary}` 与 review.md，可新增 `tests/review.test.mjs`。
- deliver：生成界面文件与 delivery.md，界面必须引用已审查的核心模块。

**`pipeline.new_run`（:178-243）与 `capture_request`（:156-175）**
- 建立 `workspace/`、`baseline/`、`tasks/`、`gates/`；基线与工作区核心文件写为空实现；`manifest.json`（schema 2）记录基线哈希。
- `capture_request` 写 `workspace/REQUEST.md` 并改写 `TASK.md`，manifest 记录 `request_sha256`、`task_sha256`；冻结后再传入不同文本被拒绝。
- 每个阶段写 `tasks/<stage>.txt`（`[PIPELINE:<id> STAGE:<s>]` 标记 + 任务正文）。
- `/intake` 幂等：request_id 16–64 位，按请求体 sha256 指纹去重，指纹不同拒绝。
- `submit_run`（:246-262）：一次用户动作对应一次聊天 turn，状态 prepared → sending → accepted / uncertain，不自动重试。

**规划后锁定**：plan gate 通过时把 SPEC.md、plan.md、验收测试的哈希写入 `gates/plan.json` 的 `frozen`（:396-399）；后续每个阶段对照 `frozen`（:368-372），并校验 REQUEST.md、TASK.md 与基线哈希（:363-367）。

| 项 | 处理 | 理由 |
|---|---|---|
| 规划 → 冻结哈希 → 下游校验 | 复用 | 防止下游修改规格或测试来“通过” |
| 规划阶段“测试先失败” | 复用为 `tests_fail_on_baseline` 门禁 | 低成本、可确定性验证 |
| REQUEST / TASK 冻结与指纹去重 | 复用 | 与平台幂等语义一致 |
| 固定 Web 文件布局（`WEB_SCOPE`） | 改写为 Pipeline 声明的产物契约 | 原实现只适用于零依赖 Web 演示 |
| `CONTRACT / BASELINE / TESTS`、`new_demo_run` | 丢弃 | 只服务固定演示 |
| `COORDINATOR_PROMPT`、`app-prompt.txt`、context 注入 | 丢弃 | 编排由 Engine 负责，不交给 LLM 遵守协议 |
| `WORKER_PROMPTS` 与各阶段任务正文 | 复用为角色提示词模板 | 职责边界清楚 |

## 3．确定性门禁（`pipeline.checkpoint`，:342-465）

**所有阶段的前置检查**
1. `native_tasks`（:265-278）：`live-state.json` 的 manifest 与 parent 匹配、`connected`、`last_success_at` 在 15 s 内；只保留同 parent 且带标记的任务。
2. 任务的 Agent 等于该阶段 Agent，任务文本含阶段标记；状态 done、`outcome == completed`、无错误或停止。
3. `route_for`（:281-298）：原生 subagent 状态与路由日志中 `actual_backend` 与 Agent 匹配，且有原生 session id。
4. 前序 gate 均已通过；基线哈希不变；REQUEST / TASK 哈希不变；非 plan 阶段 `frozen` 文件不变。

**各阶段判定**
- plan：plan.md 与 SPEC.md 各 >100 B；核心仍等于基线（规划者未实现）；验收测试含 `node:test`、`node:assert` 与核心模块引用、至少 3 个测试、无 skip / todo、语法检查通过；`node --test` **必须失败**（输出写 `plan-tests.txt`）；记录 `frozen` 与基线测试退出码。
- code：上次 review 记录的测试哈希不变（修复时不得改回归测试）；`node --test` 40 s 内 exit 0（`code-tests.txt`）；`git diff --no-index` 对基线有真实变化（`candidate.diff`、`diff_sha256`）。
- review：先记录测试哈希（失败也保留）；候选哈希等于 code gate 的候选、diff 不变；`validate_review`（:331-339）要求 `candidate_sha256` 匹配、`verdict == pass`、`tests_passed is True`、findings 为列表且严重度合法、无 critical / high、summary 非空；review.md >80 B。
- deliver：候选、diff、review 检查同上；review.json 与测试文件哈希等于 review gate 记录；交付文件各 >40 B；界面引用核心模块。

**失败与证据**
- 任何异常（含文件缺失）写入 `entry.error`、`passed=False`，CLI exit 1。
- 下游作废（:449-460）：plan 的 `frozen` 或 code 的候选变化且本次通过时，已有下游 gate 改写为 `passed:false` 并记录 `invalidated_by`。
- `gates/<stage>.json` 每次覆盖；`gate-history.jsonl` 每次尝试追加（含失败）；`report()`（:468-559）要求四个 gate 都通过才 `pipeline_passed`。

| 项 | 处理 | 理由 |
|---|---|---|
| 文件、测试、哈希、review schema 判定 | 复用，抽成 `octopus/gates.py` 纯函数 | 不依赖 LLM，可单测 |
| 下游作废与 gate 历史追加 | 复用 | 修复与重审可追溯 |
| `native_tasks` / `route_for` / 15 s 新鲜度 / 标记字符串匹配 | 改写 | Engine 自身持有 Attempt 身份与后端证据，不必从快照反推 |
| 由 LLM 调用 `checkpoint` CLI 的 exit code 协议 | 丢弃 | Engine 在 Attempt 完成后直接执行门禁 |
| 内联生成 report.html | 移到报告模块 | 与门禁逻辑解耦 |

## 4．模板

**`ui/workflow-templates.json`（12 个）**：字段 `id, name, category, description, prerequisite, output, input`（示例需求）与 `steps[{id,name,tool,model,effort,prompt}]`；**没有** acceptance / contract / gate 字段；全部 `model=auto`、`effort=""`。

| id | 分类 | 步骤 |
|---|---|---|
| kiro-quickstart | quick | prototype:kiro |
| coding-smoke | quick | code:claude → review:codex |
| idea-validation | quick | plan:kiro |
| product-landing | web | code:claude → deliver:opencode |
| team-kanban | web | plan:kiro → code:claude → review:codex → deliver:opencode |
| csv-insights | web | code:claude → review:codex → deliver:opencode |
| python-cli | engineering | code:claude → review:codex |
| node-api | engineering | plan:kiro → code:claude → review:codex |
| web-delivery | web | plan:kiro → code:claude → review:codex → deliver:opencode |
| bugfix | existing | reproduce:kiro → fix:claude → review:codex → report:opencode |
| review | existing | review:codex → verify:claude |
| go-web | engineering | plan:kiro → code:claude → review:codex → deliver:opencode |

**新版 `octopus/intent.py`**：CORE 3 个（chain：plan:kiro → code:claude → review:codex → delivery:opencode；proof：code:claude → review:codex；fix：diagnose:kiro → fix:codex），与 12 个旧模板合并为 15 个。
`SIGNALS` 关键词匹配：ASCII 词边界、中文子串；每命中 20 分，模板名出现 +500，chain +1 兜底；用户 Workflow 被点名 1000 分。`fix`、`kiro-quickstart`、`coding-smoke` 没有关键词，只能按名称匹配。

模板的阶段名（prototype / plan / reproduce / code / fix / review / verify / deliver / report）可以直接映射为 Pipeline schema 2 的 `role`，作为 M2 规则匹配与 Kiro 生成的骨架。

## 5．生成样例与运行期统计

- `examples/generated-review-workflow.yaml`：`schema:1, id, name, steps[], revision:0`，2 个 step（claude / codex，`model:auto`）。`.md` 版本由 `emit_document` 生成：标题、带 id 的说明、步骤列表、`## 可导入配置` 与一个 yaml 代码块。
- 旧版运行目录 `state/workflow-designs/`（只统计结构）：14 个设计目录，12 个产出了 `workflow.json`；步骤数 2–6，最常见 4 步；工具出现次数 kiro 13、claude 15、codex 13、opencode 8。

## 6．与新 Engine 的对照（旧版 `workflow.py` 编排）

- `_next_step`（:506-610）：父 LLM 每轮调用，选出第一个没有有效验收的 step，写 `<id>-instructions.md`（需求、任务、cwd、契约、前序交接），返回 spawn 参数（任务正文 ≤5000 字符）或 continue / wait。**它本身不派发。**
- `_accept`（:619-684）：要求 20 s 内的 live 快照、task 属于当前 step 与 parent、done 且无错误、Agent / 实际后端 / 模型 / Effort / cwd 匹配、无 BLOCKED 行、handoff 存在且晚于重试时间；只检查身份与交接存在，代码中明确“不是自动质量认证”。
- `retry_stage`（:244-386）：请求 ID + 更新哈希去重；前置条件为观测新鲜、全部 Attempt 结束、上游已验收；从该阶段起记录排除的 task、归档旧结果，并以一次原子写入使旧结果失效。

| 旧版 | 新版对应 | 处理 |
|---|---|---|
| `next` 选步 | Engine 内部调度 | 已由新版实现 |
| `accept` 身份核对 | Engine 验收门禁（`native_completed` / `identity_match` / `handoff_present`） | 已由新版实现，M2 并入门禁目录 |
| `retry_stage` 的“排除旧 task + 原子屏障” | 续作修订与下游失效（SQLite 事务） | 复用思路 |
| `_next_step` 的任务正文拼装与长度上限 | Worker 的 `instructions.md` | 复用 |
| `control_prompt` 父 LLM 协议文本 | — | 丢弃 |

## 7．M2 采用结论

1. **生成**：规则匹配（15 模板）→ Kiro 生成（`intent_prompt` 规则 + 单 JSON 块）→ `parse_document` → schema 2 校验与规范化 → 映射与门禁插入 → 用户确认或自动准入。生成任务由 Engine 作为普通 Attempt 执行，结果与状态存 SQLite，不再依赖聊天 slot 与文件状态机。
2. **门禁**：把 `checkpoint` 的判定拆成 `octopus/gates.py` 中按角色组合的纯函数（`tests_fail_on_baseline`、`artifacts_locked`、`diff_present`、`checks_pass`、`review_bound`、`candidate_unchanged`），保留下游作废与门禁历史；测试命令由 Pipeline 的 `checks` 声明，不再写死 `node --test`。
3. **契约文件**：保留 REQUEST.md 冻结与指纹去重、规划阶段 SPEC / plan / 验收测试与哈希锁定、review.json 结构（增加候选 commit SHA）。
4. **提示词**：复用 `WORKER_PROMPTS` 与 `requested_tasks` 的角色边界，去掉固定 Web 范围；`COORDINATOR_PROMPT` 不再使用。
5. **前端**：生成结果的 scope / id 守卫、迟到丢弃与“需求变更后禁止采用旧结果”沿用到新控制台与 CLI 计划确认。
