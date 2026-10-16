# KiroCrew 通用 Agent Prompt 与角色体系：现状与优化计划（D46，负责人 2026-10-16 已确认方向）

> 负责人 2026-10-16：「根据现在更新的 Feature，把 KiroCrew 通用型的 Agent Prompt 做一个 Plan，看看哪些点需要优化；每个 Coding 工具都可以有主角色，
> 同时也可以辅助做其它之用：Spec 分析、集成测试、安全测试、SDE 开发、Challenge Agent、Evaluator Agent、Knowledge Agent，以及 Owner 观察员（Coordinator）。」
>
> 本文盘点现状、列出问题、提出角色模型与分期；第 6 节为负责人的决定。

## 0．以 KiroCrew 为基准（负责人 2026-10-16）

> 「设计时一定要考虑到后方是 KiroCrew，它本身就有很强的 Knowledge 功能；以 KiroCrew 为基准设计，能以接口复用的方式整合；GitHub、GitLab 同样如此。」
>
> 依据：KiroCrew 0.10.0（`57df464`，EC2 部署版本）源码调研（路由 1099 条、`kirocrew-core` MCP 工具 86 个）。只在 KiroCrew 没有的地方由平台补齐。

| 领域 | KiroCrew 已有 | 平台复用方式 | 平台仍需补的 |
|---|---|---|---|
| 记忆 / 经验 | 成员（Crew Member）各自的记忆库（`POST /api/agents` 新建成员即得独立空库）；lessons（`rule`、`repo_scope` 按仓库路径机械过滤、`applies`）；spawn 的 `include_memory / lessons / project` 与 `crew=<member>`（以某成员的记忆运行模板）；`memory_mode` persistent / incognito / temporary | **每个空间一个成员**：父会话以该成员创建，任务 spawn 带 `crew=`，`include_lessons / project` 开启，`include_memory` 按角色（审查、Challenge、Evaluator 关闭） | 空间 ↔ 成员的对应与生命周期；共享环境上「路由而非保密」的提示 |
| Knowledge Agent | `POST /api/lessons`（经筛查、去重、矛盾清理后在之后的 spawn 中按 `repo_scope` 自动注入）；`learn_*`、`memory_recall` MCP；Knowledge Library（`knowledge.db`，按需检索，无空间隔离、每块一次 LLM 抽取） | 会话结束时由 Knowledge 角色把决策、约定、坑写成 lessons（`repo_scope` = 仓库），**存于 KiroCrew**；Knowledge Library 只在专属环境用于运行报告与决策 | 写入哪些、何时写、谁审核（平台记录写入了什么） |
| 角色卡片 | skills（`skill://` 挂到 Agent 模板）；`select_crew` / `route_crew`；`llm-council`（多厂商对抗、`include_memory=false`）、`security-conductor`（审计 → 独立验证 → 人同意后修复）等内置模式 | 每个角色一份 SKILL.md，挂到 `poc-*` 模板；规划者从 skill 目录与可用工具推荐，不内置对照表 | 角色 → `kind` / 门禁 / 输出契约的映射 |
| Evaluator | 工作账本（work ledger：执行者报告、指挥者写判定，执行者不能写判定）；`accept_eval`（pass / fail / pending / refused / error，不运行模型写的命令）；`eval/`、`decisions/` 是 KiroCrew 自身的基准与小判定，不适合 | 平台 `evaluated` 门禁借用这些设计：判定与执行分离、判定集合、不运行模型写的命令、换用不同工具且关闭记忆 | 门禁本身（平台的 Attempt / 证据 / 门禁记录） |
| 恢复 | 基础设施级重试梯（工具 → 后端 → ACP → gatewayd）；重启后孤儿任务分类；spawn 的 `continue` / `steer` / `retry` / `resume` | 返工与纠错用 `POST /api/spawn/{id}/continue`（保留上下文），中途纠正用 `steer`；`dispatch_unknown` 用孤儿分类判定 | 任务级诊断与决定（已有，D38–D43） |
| 用量与时间线 | `/api/usage/turns?slot=subagent:<id>`（模型、token、费用、上下文）；crew-log 投影（工具、批准、子任务时间线） | 每个 Attempt 的费用与工具时间线进报告与预算 | 汇总与展示 |
| GitHub / GitLab | Connections 是各厂商的远程 MCP（Agent 工具），授权存在每台机器的 kiro-cli 缓存、单一所有者；`gh` / `glab` 用边界 HOME 的登录；PR 侧栏只读与评论、**不能建 PR**；webhook 不兼容 GitHub / GitLab | **凭证与发布留在平台**（按用户 / Team 授权、审计、只推已验证候选，Connector 设计 C7 结论不变）；专属环境上复用 PR / CI 状态读取（`/api/source/pull-request/checks`、`monitor_watch`）给观察员与 Evaluator | — |

**调研中发现的现有问题（安全底线，先修，见 P0）**：
- 平台写入的 `"memory": {"enabled": false}` 在 0.10.0 中**不存在、不起作用**，全局记忆实际开启；父会话以模板身份、持久模式在**全局记忆库**上运行——共享环境上各空间的协调者会话互相写入、读取记忆。
- 协调者模板的 `availableAgents` 写在顶层，KiroCrew 只读 `toolsSettings.subagent.availableAgents`（顶层视为全部允许）；移动时须包含 `poc-diagnose`。
- 协调者挂载了完整的 `@kirocrew-core`（含 `monitor_*` / `autonudge_*`，可能自行追加回合）；`adaptive_concurrency` 可能在主机压力下把并发降到 1；`solo_*` 参数已被忽略。

## 1．现状：一个 Attempt 收到什么

| 层 | 位置 | 内容 | 说明 |
|---|---|---|---|
| 系统提示（KiroCrew 模板） | `octopus/prompts.py` COMMON + WORKER / COORDINATOR；`composition/templates.py` | 所有工具（kiro / claude / codex / opencode）、所有任务**同一份**「研发执行者」提示；`poc-diagnose` 也拿到这一份 | 是否送达非 Kiro 后端未经核实 |
| 原生提示 | `worker.py:795` | 只有「完整阅读 instructions.md…」 | |
| `instructions.md` | `worker.py:782-791` | 需求 → 规划补全的需求 → 任务契约 → 任务说明 → 前序交接 → 固定结尾 → 结果契约（outcome.json）→ 审查对象 / 纠错要求 / 设计提示 | **整体无长度上限** |
| 规划（plan / replan） | `planning.py`、`prompts.intent_prompt` | 意图、需求补全、1–7 个任务与 Agent 映射、契约、门禁、工具与模型、需求中指定（D45）、可用命令 | 首次规划与重新规划的规则不一致 |
| 协调者（诊断） | `recovery.coordinator_instructions` | retry / rework / switch_tool / replan / wait_for_user 的规则与完整失败上下文 | 上下文整份 JSON 内嵌，并在 context.json 中重复 |
| 父会话「用户交互协调者」 | `worker.py:671-682` | 解释计划、进度与错误；结果摘要 | 无输出契约；与 COORDINATOR 系统提示矛盾 |
| 模板 | `intent.py` CORE、`templates/workflow-templates.json` | 12 个模板，步骤**都没有 `kind`**、验收与检查命令 | 模板中的审查不绑定候选、交付不要求改动 |

角色只由任务的 `kind`（implement / review / test / deliver / design）间接体现：`kind` 改变门禁，几乎不改变提示——implement、deliver、design 没有任何
专属文字；安全、集成测试、对抗审查、评判、知识只以零散的一两句存在（见附录 A）。

## 2．问题（按影响排序）

**A．互相矛盾的指令**（Agent 只能猜）
1. 任务数：`intent_prompt`「1–8」、`plan_step`「1–7」、`replan_step`「1–6」、`domain.steps` 允许 8。
2. 规划者三种身份：「研发执行者」（系统提示）、「工作流设计者」（`intent_prompt`）、「规划者」（重新规划）。
3. `poc-diagnose` 拿到执行者提示（「按任务完成编码、测试…」），而诊断规则是「不要自己实现业务修复」。
4. 父会话：系统提示让它「经 spawn_run 执行、先准备源码、keep=true」，会话上下文又说「平台统一派发，不要自行派发」。
5. WORKER 把「增加、重排或重试」交给「主协调者」，实际路径是 outcome.json / BLOCKED → 平台协调者。
6. 模板的审查 / 交付步骤说「有缺陷时报告 BLOCKED」，结果契约说缺陷用 `changes_requested`、`blocked` 只用于缺输入、认证、权限。
7. `intent_prompt` 的「没有可靠候选时 model=auto」没有 D45 的例外；「源码准备由主协调者负责」已过时（Worker 已准备好源码）。
8. 两条交付路径：系统提示允许 Agent 在授权时直接提交、推送、建 PR；平台另有经验证候选的发布（`octopus_publish`）。

**B．角色与契约缺失**
9. 固定结尾「依据需求进行开发和真实验证」也发给了规划、重新规划、设计与诊断任务；结果契约中的「源码、测试或安全问题」也发给了规划者。
10. implement / deliver 从未被告知「必须产生改动」（`diff_present` 门禁要求）；design 类任务所有门禁跳过。
11. 交接文件应写什么只在系统提示里写过一次（是否送达非 Kiro 后端未知）。
12. 模板与工作流设计的步骤没有 `kind`、验收、检查命令：审查不绑定候选（`review_bound` 不生效），`is_review` 与 Engine 的判断不一致（前者按 ID 兜底）。
13. 审查须与被审查代码用不同工具：只有协调者知道，规划者不知道。
14. 审查 / 设计任务的 `verify` 显示给 Agent 但平台从不运行。
15. `.octopus/preview.json` 的实际格式（字段、1–4 个进程、远程预览须 `"$PORT"`）只以非正式描述给了规划者，交付任务看不到。

**C．长度与重复**
16. 纠错要求把整份恢复上下文（需求、所有任务、原始失败结果、证据）内嵌，并**漏给其后的所有任务**（只要还没重新规划）。
17. 协调者上下文内嵌一次、context.json 再一次，需求出现三次。
18. `plan_step` 中工具、模型、限制原样输出不截断（模型多时超过 8000 字符，会话无法启动）；重新规划已截断。
19. 模板步骤把目标（≤1200 字）复制进每个步骤的提示。

**D．首次规划与重新规划不一致**
20. 重新规划缺少：`intent_prompt` 的原则、preview.json、`.gitignore`、「验收项取自 summary」、在交接中说明工具选择依据；要求 summary 与 name 却丢弃。
21. 节名不一致（「需求补全」与「需求与验收标准（KiroCrew 规划补全）」）。
22. 续作改了需求后，后续任务看到的仍是首次规划的 intent / summary。

## 3．角色模型提案

**原则**：
- 角色描述「做什么、交付什么、怎样判定」，与工具无关；**由谁担任由 KiroCrew 规划者按执行环境的事实推荐**（可用工具、模型、需求中指定、
  审查独立性），平台不内置「角色 → 工具」对照表（负责人：不内置默认链）。下表的「常见担任者」只作为规划提示中的参考事实，不是代码规则。
- 每个任务带 `role`（新字段，可省略），`kind` 继续决定门禁；`role` 决定发给 Agent 的**角色卡片**（≤600 字符）与输出契约。
- 同一工具在一个会话中可担任多个角色（主角色 + 辅助）；**审查类角色（Challenge、Evaluator、安全测试）不得由被审查改动的作者工具担任**。

| 角色 | `kind` | 做什么 | 交付 / 契约 | 门禁 | 常见担任者（参考） |
|---|---|---|---|---|---|
| Spec 分析 | design | 读源码与需求，写规格：目标、范围、验收标准、风险、待确认问题；不改业务代码 | `spec.md`（或计划 JSON）、交接 | 无改动（新增：`no_diff`） | Kiro |
| SDE 开发 | implement | 按规格实现并自测；改动限定在 `paths` | 候选提交、交接、`verify` | `diff_present`、`checks_pass`、`artifacts_locked`、`no_secrets` | Claude Code；Codex 辅助 |
| 集成测试 | test | 跨模块 / 端到端：启动服务、跑真实接口与流程；不改实现 | 测试与报告、`verify` | `checks_pass`（不要求基线失败）、只改测试与报告路径 | Claude Code / Codex |
| 安全测试 | review | 依赖与配置审计、秘密、输入校验、权限；按严重度给出发现 | `findings`（含严重度、文件:行）、`reviewed_commit` | `review_bound`、`no_secrets`；高危即 `changes_requested` | Codex；Kiro 辅助 |
| Challenge Agent | review | 对候选做对抗：找反例、边界、失败场景，必要时写复现测试（不修实现） | 挑战清单（可复现步骤）、可选复现测试 | `review_bound`；作者以外的工具 | 与实现者不同的工具 |
| Evaluator Agent | review | 逐条验收项给出 pass / fail 与证据，综合实现、测试、挑战的结论做判定 | 验收表、最终 decision | `review_bound`；作者以外的工具；**判定以它为准**（取代实现者自报） | Kiro / Codex |
| Knowledge Agent | deliver | 汇总本会话的决策、约定、坑与可复用做法，更新项目知识 | `.octopus/knowledge.md`（或 KiroCrew 记忆） | 只改知识文件 | Kiro |
| Owner 观察员（Coordinator） | —（平台） | 观察全程、向用户解释；失败时诊断并决定恢复；守预算；不实现 | `decision.json`、给用户的进展说明 | 平台核对（D38 / D41 / D42） | Kiro（固定） |

## 4．Prompt 结构提案

`instructions.md` 统一为六段，每段有上限，按角色组合：

1. **角色卡片**（新）：你是谁、做什么、不做什么、何时 BLOCKED / changes_requested（≤600）。
2. **需求**：用户需求 + 当前有效的需求补全（续作后更新）；不重复粘贴。
3. **本任务**：契约（类别、验收、检查命令、可改路径、交付物）+ 任务说明（≤8000）。
4. **上下文**：前序交接（D40 结构化）、审查对象、纠错要求——纠错只发给被安排修复的任务，且只带失败摘要与要求（完整证据给文件路径）。
5. **输出契约**：按角色给出 outcome.json / decision.json / 计划 JSON 的字段与示例；交接文件的内容清单（从系统提示移到这里，所有后端都能看到）。
6. **环境事实**：可用工具、命令、限制（截断）。

系统提示（KiroCrew 模板）收敛为与角色无关的最小共同原则；Coordinator 与执行者分开（`poc-diagnose` 不再拿执行者提示），父会话只保留「观察与解释」。

## 5．分期

| 期 | 内容 | 主要改动 | 验收 |
|---|---|---|---|
| **P1 一致性与正确性**（不改行为模型） | 第 2 节 A 全部、B 9–11 与 14–15、C 16–19、D 20–22 | `prompts.py`、`planning.py`、`worker.py` 外壳与固定结尾、`recovery.feedback`（只给目标任务、摘要化）、模板补 `kind` / 验收 / 检查命令并统一 BLOCKED 用法 | 本机：每类任务的 `instructions.md` 快照测试（长度上限、无矛盾词）；EC2：规划 + 实现 + 审查 + 返工各一次 |
| **P0 KiroCrew 配置与隔离**（安全底线） | 记忆默认值改为 0.10.0 实际的键（或父会话 `memory_mode: incognito`，直到每空间成员就绪）；`availableAgents` 移到 `toolsSettings.subagent`（含 `poc-diagnose`）；协调者不挂 `monitor_*` / `autonudge_*`；并发模式固定；去掉 `solo_*` | `composition/templates.py`、`worker.py` 父会话创建 | EC2：先核实父会话是否写入全局记忆；修后各空间协调者不再读写全局记忆 |
| **P2 角色卡片（KiroCrew skills）** | `role` 字段；8 个角色各一份 SKILL.md，以 `skill://` 挂到 `poc-*` 模板；规划提示介绍角色与审查独立性；控制台 / MCP 显示角色 | `composition/`（skills 种入）、`domain.steps`、`planning`、`worker` | 规划会话中出现安全测试或集成测试角色并按其 skill 交付 |
| **P3 Challenge / Evaluator 闭环** | Challenge 的挑战清单交给 Evaluator；Evaluator（不同工具、`include_memory` 关闭）的逐项判定取代实现者自报；借用 KiroCrew 工作账本 / `accept_eval` 的判定集合与「不运行模型写的命令」 | 新门禁 `evaluated`、结果契约、报告的验收表 | 一个实现有隐患的会话：Challenge 找到问题 → Evaluator 判 fail → 返工 → 通过 |
| **P4 Knowledge（KiroCrew 成员记忆与 lessons）** | 每个空间一个 Crew Member；父会话与任务 spawn 用该成员（`crew=`）；`include_lessons / project` 开启、`include_memory` 按角色；会话结束时 Knowledge 角色把决策与约定写成 lessons（`POST /api/lessons`，`repo_scope` = 仓库） | `worker.py`（成员创建、spawn 参数）、新的 Knowledge 角色 skill；`tests/native` 合约测试 | 第二个会话的任务自动带上第一个会话沉淀的 lessons；另一空间看不到 |
| **P5 保留上下文的返工** | 返工 / 纠错的 Attempt 用 `POST /api/spawn/{id}/continue`，中途纠正用 `steer` | `worker.py` 派发、Attempt 与原生 ID 的对应 | 返工的 Agent 能引用上一轮自己的工作 |
| **P6 用量与时间线** | 每个 Attempt 的费用、token 与工具时间线进入报告与预算 | `worker.py` `/inspect`、`report.py` | 报告显示每个任务的费用与工具时间线 |

## 6．负责人决定（2026-10-16）

1. **新增 `role` 字段**：`role` 决定角色卡片与输出契约，`kind` 继续决定门禁；角色由 KiroCrew 规划者按事实推荐工具，平台不内置对照表。
2. **以 Evaluator 为准**：Evaluator（作者以外的工具）的逐项判定取代实现者自报的 pass，作为会话通过的依据；不通过即进入返工（P3）。
3. ~~Knowledge 存放在项目内 `.octopus/knowledge.md`~~ → **改为以 KiroCrew 为基准**（负责人 2026-10-16 修正）：知识存于 KiroCrew 的成员记忆与 lessons，平台只负责空间 ↔ 成员的对应、写入时机与记录（P4）。
4. **先做 P1**（只修矛盾与缺陷、不改角色模型）——已完成；接着 P0（安全底线），再 P2–P6。

## 附录 A：今天已有的「角色」痕迹

- 安全：模板「Security / Testing Review」一步、结果契约中的「安全问题」一句、`no_secrets` 门禁；没有专门的角色或提示。
- 集成测试：无（`test` 指在基线上失败的新测试）。
- 对抗审查：「双工具复核」模板（codex 审查 → claude 复核）；审查独立性只在换工具时检查。
- 评判：无 LLM 评判；门禁是代码；验收项为实现者自报。
- 知识：KiroCrew 记忆关闭（`include_memory / lessons / project: False`）。
- Owner 观察员：父会话的「用户交互协调者」，无契约、无权限；诊断由 `poc-diagnose` 完成。
