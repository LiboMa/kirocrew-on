# Knowledge：会话中学到的知识沉淀为 KiroCrew lessons（D46 P4）

## 1．背景

D46 §6（负责人 2026-10-16 修正）：Knowledge 以 KiroCrew 为基准——知识存于 KiroCrew 的记忆与 lessons，平台只负责对应关系、写入时机与记录。
负责人 2026-10-17：每人独立 Worker，只有 Team 共用一个 Worker 空间；记忆按执行环境分隔。

调研（2026-10-18，KiroCrew 0.10.0）：
- `POST /api/lessons {rule ≤500, category, negative?, repo_scope?, applies: always|on_topic}`（需 `X-Session-Key`，`dashboard:ui` 直接信任），
  写入该 Gateway 的全局存储；不返回 id，以 `(rule, repo_scope)` 识别，`DELETE /api/lessons {rule, exact, repo_scope}` 删除。
- `repo_scope` 是「检出目录中存在的相对路径」，不是仓库地址；没有 `repo_scope` 的 lessons 注入一切任务。
- 派发 `include_lessons: true` 时注入作用域内的 lessons；`on_topic` 一层按首条消息相关性筛选（平台的首条消息只有一句），故写 `applies: always`。
- 共享执行环境中 KiroCrew 无法按空间隔离（成员只是路由，不是保密边界）。

负责人决定（2026-10-18）：**改代码的会话完成时自动整理**；**共享执行环境不沉淀、不注入**。

## 2．已确定

| 编号 | 决定 |
|---|---|
| R1 | 适用：执行契约 ≥4 的会话、源码来自仓库（`source.type = git`）、执行环境不是共享的（`scope ≠ shared`）且 Worker 支持角色卡片。共享环境的会话页写明「共享执行环境不沉淀、不注入知识」。 |
| R2 | 仓库标记：准备检出时，Worker 在项目根写空文件 `.octopus-kb/<仓库标识>`（标识 = `host_owner_repo`，只含字母数字与 `._-`），并写入 `.git/info/exclude`（不提交、不发布）；lessons 的 `repo_scope` 即该相对路径——只注入同一仓库的任务。 |
| R3 | 沉淀：适用的会话中有 implement / deliver 任务时，会话即将完成（在 Evaluator 之后）平台追加「Knowledge 沉淀」任务：`role: knowledge`、`kind: design`、只读；工具取执行环境报告的可用工具中的 kiro（平台的规划与诊断工具），没有时取第一个可用工具。验收项「列出可复用的项目知识（可为空）」。改动之后已有 Knowledge 任务则不再追加。 |
| R4 | 结论契约：`outcome.knowledge = [{rule, avoid?}]`，至多 5 条，`rule` ≤300 字符、`avoid` ≤200：只写长期有效的项目知识（约定、决策、踩过的坑、可复用的做法）；不写本次进度、个人信息、秘密、具体模型身份。可为空。 |
| R5 | 平台核对后写入：逐条检查长度与秘密（与发布相同的扫描），去重（同一执行环境同一仓库已记录的不再写）；经 Worker 调 `POST /api/lessons`（`category: knowledge`、`applies: always`、`repo_scope` 为 R2 标记、`negative` 为 `avoid`）。每条记入平台台账（会话、任务、执行环境、仓库、文本、结果：written / refused / superseded / skipped 及原因）。写入失败不影响任务与会话的完成。 |
| R6 | 注入：适用的会话派发任务时 `include_lessons: true`（`include_memory` 仍为 false）；不适用时保持 false。 |
| R7 | 唯一写入方：协调者的 `managedToolPolicy.exclude` 加入 `learn_add`、`learn_remove`（平台是 KiroCrew lessons 唯一的显式写入方）。 |
| R8 | 可见与更正：会话页「本次沉淀的知识」列出台账条目与结果；创建者、Team owner、管理员可「删除」一条（经 Worker 调 `DELETE /api/lessons`，台账记删除者）；时间线 `run.knowledge`；MCP 精简状态含本次沉淀的条目。 |

## 3．测试与验收

- **本机**：适用条件（契约、源码类型、共享环境、角色卡片）；标记文件与 exclude；追加 Knowledge 任务（工具选择、不重复追加、无改动不追加）；
  结论契约（超长、超量、秘密、空列表）；写入请求体（契约测试的 `POST /api/lessons` 键）、结果与台账；去重；`include_lessons` 随适用条件；
  协调者排除 `learn_*`；删除；会话页、时间线、MCP。
- **EC2**（需要一个专属执行环境）：第一个会话完成后沉淀知识并写入 KiroCrew；第二个同仓库会话的任务说明之外，其原生会话收到这些 lessons；
  删除后不再注入。
