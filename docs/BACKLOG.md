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

## 会话运行优化后续（D34）
- 交接精简：上游交接改为摘要 + 文件路径（目前每个任务约 4.7K 字符全文传给其后所有任务）。
- 按 `depends_on` 并行执行互不依赖的任务（同一工作区，按 `paths` 判断冲突；默认关闭）。
- 自适应轮询：长时间运行的原生任务降低 `/inspect` 频率。
- KiroCrew 自动影响评估：续作时由 Kiro 判定重跑范围并调整后续任务的 Prompt。
- 工作流编辑器编辑任务契约字段；把各工具实测耗时与通过率提供给规划。
- D34 审查延后项：「沿用 v<N> 结果」显示的是续作的版本而非被沿用结果的版本；控制台续作时被规则补全的任务也作为勾选项发送，
  修订的 `reasons` 为空；命令事件对 stop / close 也抄录请求体中未校验的 `task_id` / `worker_id` / `rerun`；500 字符内的结果与证据在
  变更记录中原样保存（规格要求只记长度与哈希）；守护测试按行、区分大小写匹配；「变更记录」面板只加载最近 200 条、无「加载更多」、
  打开时不自动刷新、切换筛选时短暂显示上一筛选的结果；MCP 状态只读前 200 条事件；`_kept` 每次轮询解析全部修订快照；
  规划改名冲突的任务 ID 时不重映射 `depends_on`；控制台默认 Token 标签「Coding client」被归为 API；OpenAPI 的 `depends_on` 长度上限与服务端不一致；
  outcome 为 pass 但有验收项为 fail 时仍按 pass（S4：审查者裁定）。

## 执行环境后续（D28–D30）
- 扩容 `w2`、`w3`（`deploy.sh worker-host add <名称>`，负责人确认后）。
- 同主机新 OS 用户的专属边界（设计 §4.1–4.3：`boundary add`、`boundaries`、`boundary.sh`），按需实现。
- 共享环境的记忆分隔（T7）：等待 KiroCrew 提供成员创建与子任务继承成员记忆的接口。
- `deploy.sh space` 定位 Worker 主机上的会话（目前只能到平台主机的边界）。

## 已知的次要问题（审查延后项）
- 访问管理第 1 期（审查延后项）：预览复核按账号的管理员身份而非签发 Token 的 scope
  （≤ 10 分钟）；被降级的管理员仍能签发含 admin 的 Token（降级期间无效）；「全部会话」中已停用所有者显示原始 id、选中自己不在的 Team
  会话会切到未列出的空间；用户管理不显示创建时间、管理员降级自己后未刷新 `me`；无变化的启用 / 升级也写审计、审计 `before=0` 视为无游标、
  OpenAPI 缺 `DELETE /v1/admin/users/{id}/tokens/{tid}`；停用用户不结束已签发的
  原生 Gateway 登录 Cookie（≤ 1 小时，K03）。
- 访问管理第 2 期（审查延后项）：跨 Worker 改派时，崩溃遗留的 `dispatching` Attempt 以原 id 发往新 Worker（应把未验收任务重置为待执行）；
  主机重新部署后改派回同一 Worker 时保留旧原生父会话、派发得 `not_prepared`（连接更新改变执行边界时应清除其未关闭会话的绑定，主机替换时原已存在）；
  空闲主机「只回原 Team」可被「以非空闲方式重新登记同一边界」绕过、控制台不显示 `last_team`（预留的空闲环境点击后才得 409）、
  退役主机重新登记因边界已登记而失败；`POST /v1/users {worker_id}` 分配个人空间未写 `worker.space.add`、同名改名与重复归档也写审计；
  退役 Worker 的观察提示为「已停用」；Skill 未说明 `environment_changed`，并在 MCP 尚无分配工具时建议「选择 Worker」（第 3 期）；
  `WorkerTools` 按 `me.admin` 而非 `elevated(me)` 显示；更换执行环境的确认未写出目标环境。
- 执行环境（D28 阶段 A / B 审查）：新用户的个人空间默认用共享环境（需手动改选）；
  `team_without_worker` 检查早于成员检查（非成员可得知 Team 名），环境变化后的重试被拒绝而不是重放；`mcp.py` 仍按 owner / team 预选 Worker
  （服务端已自动选择，属冗余）；已分配给 Team 的专属 Worker 也显示「设为共享环境」；`bootstrap-worker --spare` 按名称匹配（名称不唯一）；
  `openssl` 不在 dnf 依赖列表、只生成了私钥的 CA 会让重跑在 chmod 处失败；`worker-host tls` 在 `nginx -t` 失败时留下 conf.d 文件；
  `kiro-login` 只识别 `--host` 参数。
- 执行环境（D28 阶段 C 审查）：中转端口 8920–8929 接受任意 CloudFront 分发，未被 Worker 主机占用前，平台主机上监听 0.0.0.0 的进程
  可从公网访问（可选：nginx 以 444 占住未分配端口）；Worker `/health` 的 version 恒为 1.0.0，管理员列表看不出旧版本主机；
  「执行环境」对话框移出空间无确认；Worker 不可达时不显示进行中会话数；`worker-host native` 的实测以 `Host: 127.0.0.1` 发出，
  链路正常时读到 Gateway 的 4xx；环境列表并发测试的时间余量偏小。
- 源码准备失败时控制台只显示 `Worker 操作未完成 (ValueError)`：`octopus/worker.py:488` 只透传 `security.Error`，`octopus/sources.py` 的提示均为 `ValueError`（Connector 设计 §7 P1）。
- 向导：导入 / 推荐后切换到模板时来源提示未更新；切换空间未重置推荐 Worker；推荐轮询遇 403 / 404 不停止；提交瞬间在途轮询可能回写草稿。
- 原生入口弹窗缺少账户切换保护；`crew_url` 公网来源未规范化 `:443` 写法。
- 导入说明未列出 ID 改写、Effort 大小写、失败任务序号；document 的 `name` 未用于 Session 名称。
- 构建产物 `no-store`（584 KB 包每次重新下载）；经典版步骤条跳转跳过隐藏任务字段校验。
- `default_url` 由 `0.0.0.0` 拼接（应使用 `probe_host`）；systemd 单元可恢复 `NoNewPrivileges` 等加固项；
  `bootstrap-worker` 不比对已有 Worker 的 URL / Token；记录接受的 KiroCrew commit 而不只是版本号。
- OpenCode 执行未通过（K01）：EC2 上 shell 写文件被 KiroCrew 工具门禁拒绝，需查明命中的门禁层；替代配置 deliver→claude 可用。
