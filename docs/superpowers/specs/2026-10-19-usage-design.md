# 用量：每个 Attempt 的 Credit、token、费用与耗时（D46 P6a）

## 1．背景

D46 §5 P6：每个 Attempt 的费用、token 与工具时间线进入报告与预算。负责人 2026-10-19 关心 Credit 能否支撑多人：每人一个边界（D49）之后，
用量天然按人分开，平台需要把它看得见。

KiroCrew 0.10.0（2026-10-19 核对源码）：
- `GET /api/spawn/{id}`（运行结束后）：`credits`（kiro 按 Kiro credit 计；claude / codex 经 Bedrock 为 0）、`elapsed`（秒，墙钟）；不在内存时取
  `tombstone.json` 的同名字段。
- `GET /api/usage/turns?slot=subagent:<对话>`：每次运行结束时写一行（只含最后一次尝试）：`ts, model, input, output, cache_create, cache_read,
  credits, cost, duration_ms`；续接的各轮共用对话的 slot；因回合上限或异常结束的运行没有这一行；查询窗口至多 7 天。`cost` 只在适配器上报时非零
  （claude-agent-acp 上报；codex-acp 未确认）。
- **没有可用的工具时间线**：子任务不写 crew log，`state.json` 只有 `turns`（受批准门控的工具请求数）与 `last_tool`。

## 2．已确定（第 1 期：用量；工具时间线待 KiroCrew 提供）

| 编号 | 决定 |
|---|---|
| R1 | Worker 在原生任务结束时为 Attempt 记 `usage`：`credits`、`seconds`（`elapsed`）、`turns`、以及 `/api/usage/turns` 中这次运行的那一行的 `input / output / cache_read / cache_create`（token）、`cost_usd`、`model`。匹配：同一 slot（`subagent:<对话 ID>`，续接为这一串第一个的原生 ID）中 `ts` 落在运行开始至结束后 5 分钟内的最后一行；没有时只记前三项并注明 `tokens: "unavailable"`。结果写入 Attempt 目录的 `usage.json`，之后的检查不再查询。读不到时不影响任务判定。 |
| R2 | 平台：Attempt 证据保留 `usage`；会话视图 `usage` 汇总全部 Attempt（含重试、返工、诊断之外的平台任务）：`credits`、`input_tokens`、`output_tokens`、`cost_usd`、`seconds`，以及 token 不可得的 Attempt 数。 |
| R3 | 可见：任务卡片的 Attempt 行「用量：12.4 credits · 3 分 20 秒」或「输入 120k / 输出 8k token · $0.84 · 3 分 20 秒」；会话页顶部合计；报告「用量」一节（每个任务一行与合计）；MCP 精简状态 `session.usage` 与 `latest.usage`。 |
| R4 | 预算（D42）不变：用量只显示，不作为停止条件（按费用设预算由负责人另定）。 |

## 3．以后

- 工具时间线：需要 KiroCrew 为子任务记录工具调用（如为 subagent 会话开 crew log），或平台用 PostToolUse hook 自记；记 BACKLOG。
- 按人 / 按 Team 的用量汇总（管理员视图）与费用预算。

## 4．测试与验收

- **本机**：Worker 组装 usage（kiro credits、claude token 与 cost、续接共用 slot 的匹配、没有那一行、查询失败）、只查询一次；平台汇总、任务卡片、报告、MCP。
- **EC2**：一个 kiro 任务与一个 claude 任务的会话：报告中各有 credits 或 token 与费用，合计正确。
