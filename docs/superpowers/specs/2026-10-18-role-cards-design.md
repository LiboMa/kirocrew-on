# 角色卡片（D46 P2）

## 1．背景

D46（`2026-10-16-agent-prompts-roles-plan.md` §3、§5）：每个 Coding 工具可担任主角色并辅助其他角色；任务带 `role`（新字段，可省略），
`kind` 继续决定门禁，`role` 决定发给 Agent 的角色卡片（≤600 字符）。P1（提示的矛盾与缺陷）与 P0（KiroCrew 配置与隔离）已完成。

调研（2026-10-18）：KiroCrew 0.10.0 的 skill 对三个后端都生效（由 KiroCrew 注入），但只能按 Agent 配置固定挂载，`/api/spawn` 不能按任务选择；
KiroCrew 自带的 code_review_sage 也是在任务文本中指定用哪个 skill。**负责人决定（2026-10-18）：角色卡片按任务内联进任务说明。**

## 2．已确定

| 编号 | 决定 |
|---|---|
| R1 | 本期 6 个角色：`spec`（Spec 分析，kind design）、`sde`（SDE 开发，implement / deliver）、`integration_test`（集成测试，test）、`security_test`（安全测试，review）、`challenge`（Challenge，review）、`evaluator`（Evaluator，review）。Knowledge 在 P4（KiroCrew 成员记忆与 lessons），Owner 观察员即协调者（已有提示）。 |
| R2 | 角色卡片按 KiroCrew 的 SKILL.md 格式（frontmatter `name` 与目录同名、`description`）存于 `octopus/roles/<role>/SKILL.md`，正文 ≤600 字符；平台按任务的 `role` 把正文内联到 `instructions.md` 开头（「你的角色：…」），契约中写明角色。三个后端行为一致，内容随任务说明留档。 |
| R3 | `role` 可省略：省略时不发卡片（行为与现在相同，不按 kind 推断角色）。给出 `role` 而省略 `kind` 时取该角色的首个类别；两者都给出时须相容，否则拒绝。 |
| R4 | 审查独立性：审查类角色（`security_test`、`challenge`、`evaluator`）不得使用被审查改动的作者工具（同一计划中此前 implement / deliver 任务的 `tool`）。用户提交的任务违反时拒绝（400）；KiroCrew 规划的违反时照常应用并记说明（不阻断会话，P3 的 Evaluator 门禁再据此判定）。 |
| R5 | 规划提示介绍 6 个角色（id、类别、一句话用途，取自卡片的 description）与审查独立性，示例任务带 `role`；规划提示在最大输入下仍 ≤8000 字符。 |
| R6 | 可见：控制台任务卡片写「角色：安全测试」；MCP 精简状态的任务带 `role`；报告的计划表类别列写「review（安全测试）」。控制台的角色标题与平台的角色列表由契约测试保持一致。 |

## 3．测试与验收

- **本机**：每张卡片存在、frontmatter 合规、正文 ≤600；`role` 校验（未知角色、类别不相容、类别推断）；独立性（提交的拒绝、规划的记说明）；
  有 `role` 的任务说明以角色卡片开头，没有的不变；规划提示含角色列表与独立性且 ≤8000；MCP、报告、控制台显示角色。
- **EC2**：KiroCrew 规划的会话（需求含安全审查），规划者为审查任务标注 `security_test`，该任务的 `instructions.md` 以安全测试卡片开头，
  按卡片交付带严重度的发现。
