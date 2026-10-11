# 后续排期（Backlog）

整理日期：2026-10-11。当前目标是**最小 MVP**：Coding Agent 连接后台 → 提交任务 → 查看任务 → 更新优化应用。
以下事项不阻塞该闭环，按负责人要求延后（与分支开发相关的 M2 / M3 内容整体后排）。

## 分支与仓库闭环（原 M2 / M3 / M5）
- 候选 commit、Feature 分支创建与唯一性；只推送已验证候选，不推默认分支、不 force push；推送前后核对远端 ref。
- 本地 / 远端联动：未推送提交检测与 `--push-base`、`pull` 拉回分支与上下文、`sync` 快进与重新门禁审查、`.octopus/session.json` 关联。
- Draft PR / MR、Webhook / Issue 输入、写回去重与冲突、检查状态同步（M5）。
- GitHub Connector（平台级 GitHub App，读写分离）：设计草案见 `docs/superpowers/specs/2026-10-11-github-connector-design.md`。
  其中 CONN-01（连接、列仓库、只读克隆）可前移：私有仓库的 Session 目前无法准备源码（如 run-63b33288…）。

## 核心闭环的完整版（原 M2）
- Request 数据表与 API、准入（确认模式 / 按仓库范围自动准入）与策略快照。
- 门禁目录（`tests_fail_on_baseline`、`diff_present`、`checks_pass`、`review_bound` 等）与 Pipeline schema 2。
- 映射策略（显式 → Workflow → 个人 / Team 策略 → 平台默认）与回退链；`fallback` / `replan` 决定的代码校验。
- 完整恢复阶梯 L0–L4、预算、看门狗、条件满足后自动恢复。
- 原生权限批准代理（控制台 / CLI / MCP 可见并可处理，K04）。
- Feature 报告（模板化 Markdown / HTML）。

## 客户端与控制台（原 M3）
- `octopus connect` 扩展到 Codex 与 Kiro IDE；CLI 命令集（submit / plan / confirm / status / watch / report / approvals / search）。
- 控制台：服务端分页、按仓库 / 分支 / 阶段 / 工具 / 客户端 / 提交者 / 时间过滤、全文搜索；详情中的 diff、门禁与报告。
- Worker 自助接入（一次性接入码，Worker 启动时自行登记 URL 与 Token），本地全栈启动时自动登记 Worker。
- 旧版控制台 `/classic/` 在 React 版功能对齐并经负责人确认后移除。

## 共享与组织（原 M4）
- 组织层，Session 共享给个人 / Team / 公司；Team 执行隔离；排队与配额。
- 原生入口改为单 Session 票据（K03）；Worker Token 移出数据库（K10）。

## 已知的次要问题（审查延后项）
- 源码准备失败时控制台只显示 `Worker 操作未完成 (ValueError)`：`octopus/worker.py:488` 只透传 `security.Error`，`octopus/sources.py` 的提示均为 `ValueError`（Connector 设计 §7 P1）。
- 向导：导入 / 推荐后切换到模板时来源提示未更新；切换空间未重置推荐 Worker；推荐轮询遇 403 / 404 不停止；提交瞬间在途轮询可能回写草稿。
- 原生入口弹窗缺少账户切换保护；`crew_url` 公网来源未规范化 `:443` 写法。
- 导入说明未列出 ID 改写、Effort 大小写、失败任务序号；document 的 `name` 未用于 Session 名称。
- 构建产物 `no-store`（584 KB 包每次重新下载）；经典版步骤条跳转跳过隐藏任务字段校验。
- `default_url` 由 `0.0.0.0` 拼接（应使用 `probe_host`）；systemd 单元可恢复 `NoNewPrivileges` 等加固项；
  `bootstrap-worker` 不比对已有 Worker 的 URL / Token；记录接受的 KiroCrew commit 而不只是版本号。
- OpenCode 执行未通过（K01）：EC2 上 shell 写文件被 KiroCrew 工具门禁拒绝，需查明命中的门禁层；替代配置 deliver→claude 可用。
