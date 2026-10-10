# 已知故障清单

整理日期：2026-10-11。K01–K15 来自现有项目文档的历史报告记录（`docs/MVP-RELEASE.md` §9），K16 起为本仓库迁入后发现。
“处理任务”指本仓库计划中负责复现或处理的任务；尚未实测的写明“待复现”。

| # | 问题 | 复现方式 | 现有缓解 | 处理任务 |
|---|---|---|---|---|
| K01 | OpenCode 执行曾返回空结果或不完整交付 | 单阶段 deliver（opencode，`model=auto`） | 新版判为 blocked，不宣称完成；旧版靠原生续接补齐 | M1 Task 14 / 18 复现；M2 回退链 |
| K02 | 含 `/`、`:`、`[]` 的模型 ID 被原生 spawn 格式拒绝 | 指定 `provider/model` 形式的 OpenCode 模型 | Worker 公布能力时过滤，返回 `model_transport_unsupported` | M1 Task 14 / 18 记录；上游最小方案另行提交 |
| K03 | 原生浏览器登录覆盖整个 Gateway，平台撤权不使其失效 | 生成 crew-entry 链接后撤销成员 | 仅对执行边界所有者开放 | M4 |
| K04 | 新版 Web 未代理原生权限批准；待批准时停止可能挂起 | 触发需要批准的工具调用 | 回 KiroCrew 原生页面处理 | M2 批准代理 |
| K05 | Kiro 不报告实际模型；公布的模型不代表有配额 | 查看 Kiro Attempt 证据 | 显示“未报告” | M1 记录 |
| K06 | 父会话 note 上限约 4000 字符，Worker 截断到 3800 | 长交接摘要 | 交接以文件为准 | M2 报告与上下文 |
| K07 | 原生 continue 不读 `reasoning_effort`；原生 spawn 无 HTTP 幂等键 | — | 新 Attempt 使用新原生任务；Attempt 标记核对 | 维持；M2 恢复阶梯沿用 |
| K08 | Coordinator “不派生任务”只靠提示词 | — | Engine 校验决定 | M2 |
| K09 | Engine 单线程串行，慢 Run 阻塞其他 Run | 一个 Run 的 `/prepare` 长时间运行 | 无 | **M1 Task 7** |
| K10 | 无 schema 迁移（M1）；Worker Token 明文存于 `workers.config`（M4） | — | 数据库文件 0600 | **M1 Task 6**；M4 |
| K11 | 组合只在单进程内，re-exec 会丢失 `PocProviderRegistry` | — | 路由日志缺失时验收拦截 | M1 Task 5 / 9（就绪检查核对组合标记） |
| K12 | Linux 沙箱依赖非特权 user namespace；非官方 launcher 启动需核实 | AL2023 上 `unshare -Ur true` | — | M1 Task 15 / 16 |
| K13 | 启动器与路径依赖 macOS（`/Applications`、App 内置 Python、zsh、Electron、`lsof`、Homebrew） | — | `poc.py` 保留为 macOS 兼容层 | **M1 Task 3 / 5 / 9** |
| K14 | 旧版 Host 校验固定为 `127.0.0.1:<port>`；新版无 Host 白名单 | 经隧道或代理访问 | — | **M1 Task 8** |
| K15 | Gateway 重启后不再列出已结束的内存任务；`/context` 背景重启后丢失 | 运行中重启 Gateway | Worker 落盘记录核对 | M1 Task 14 / 19 实测 |
| K16 | 路由日志路径不一致风险：配置默认 `state/provider-routing.jsonl`，旧版 `poc.py` 写 `evidence/provider-routing.jsonl`；若旧版 Gateway 与新 Worker 混用，Worker 读不到路由证据，所有任务被拦为“实际 ACP 后端未核实” | 用 `poc.py` 起 Gateway、用默认配置起 Worker | 新栈的 Gateway 与 Worker 读同一份边界配置（`routing_log` 一致）；`poc.py` 兼容层显式传 `evidence/` 路径 | M1 Task 5（composition 使用 `settings.routing_log`）；运维手册写明 |
| K17 | 原生 token 每次调用现取（一次 inspect 2–3 次 `/api/token/local`） | — | 无功能影响 | M2 视负载评估缓存 |
| K18 | 旧版意图生成的派生状态不回写 `job.json`，迟到结果无后端保护 | 旧版运行目录 14 个设计 job 全部停在 `submitted` | 前端 scope / id 守卫 | M2 生成任务改由 Engine 执行并存 SQLite |
