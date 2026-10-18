# Evaluator 门禁（D46 P3）

## 1．背景

D46 §6 负责人决定（2026-10-16）：**以 Evaluator 为准**——Evaluator（作者以外的工具）的逐项判定取代实现者自报的 pass，作为会话通过的依据；
不通过即进入返工。P2 已有 `evaluator` 角色卡片与审查独立性。KiroCrew 的 `accept_eval`（goal-conductor）把判定写成固定的结果集合
（pass / fail / pending / refused / error），由脚本而非模型的印象决定。

负责人决定（2026-10-18）：**改代码的会话都要经过 Evaluator**；Evaluator 判为 pending（需要人判断）的项**停在「验收待确认」**。

## 2．已确定

| 编号 | 决定 |
|---|---|
| R1 | 适用：新会话（执行契约 4）中只要有 `implement` / `deliver` 任务，就须有一个 `role: evaluator` 的任务排在最后一个 implement / deliver 之后。已有会话（契约 ≤3）不变；执行环境的 Worker 不支持角色卡片（未公布 `role_cards`，Evaluator 拿不到卡片）时也不要求。 |
| R2 | 规划者被告知 R1 并安排 Evaluator（工具按审查独立性选择）。规划、提交的 steps、工作流或模板中缺少时，平台在末尾追加一个 Evaluator 任务：验收项取此前 implement / deliver / test 任务的验收项（去重、至多 20 条）；工具取执行环境报告的可用工具中，计划里已用于审查且不是作者工具的一个，否则第一个不是作者工具的可用工具（按环境事实，不是固定对照表）；没有作者以外的可用工具时用第一个可用工具追加，并记「审查不独立」说明（不阻断会话）。映射来源「平台追加（改代码的会话须经 Evaluator）」。 |
| R3 | Evaluator 的结论（outcome）须对自己的每一条验收项给出 `result`（pass / fail / pending）与非空 `evidence`。新门禁 `evaluated`：缺项、缺证据或结果不在集合中 → 失败（contract，交给协调者）；任一 fail → `changes_requested`（按现有审查流程，协调者安排上游返工、复审）；全部 pass → 通过。 |
| R4 | 无 fail 但有 pending → 会话停在 `awaiting_acceptance`（「验收待确认」），列出待判定项与证据；创建者、Team owner、管理员经控制台或 MCP `octopus_accept`（调用前须向用户展示待判定项并得到同意）确认通过——Evaluator 任务记为通过（记录确认者），会话继续；或用续作返工（照常）。等待不计入预算。 |
| R5 | 实现者自报的 pass 不再是会话通过的依据：会话完成需要 Evaluator 任务通过（R3 / R4）；报告的验收表以 Evaluator 的判定为准，列出 pending 的确认者。 |
| R6 | Challenge → Evaluator：Evaluator 的卡片要求对上游 Challenge / 安全测试的每条发现给出成立或不成立与依据（通过已有的结构化交接传入，不新增门禁）。 |

## 3．可见

- 会话页：「验收待确认」卡片列出待判定项与证据，按钮「确认通过」；时间线 `run.awaiting_acceptance`、`run.accepted`。
- MCP：`octopus_accept`；精简状态在 `awaiting_acceptance` 时列出 Evaluator 任务的待判定项。
- 规划说明：平台追加 Evaluator 时记说明。

## 4．测试与验收

- **本机**：契约 4 的会话缺 Evaluator 时追加（工具选择、验收项、来源）、契约 ≤3 不变、有 Evaluator 不追加；`evaluated` 门禁（缺项、缺证据、fail、pending、全 pass）；
  pending → `awaiting_acceptance` → 确认通过后完成；续作返工；权限；预算不计等待；报告；MCP。
- **EC2**：一个实现任务的会话（未写 Evaluator）→ 平台追加 Evaluator（作者以外的工具）→ Evaluator 逐项判定 → 会话完成；另一会话的验收项含需人判断的项
  （如「页面观感符合品牌」）→ 停在「验收待确认」→ 确认后完成。
