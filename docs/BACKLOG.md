# 后续排期（Backlog）

整理日期：2026-10-11（2026-10-13、10-14 补充）。当前目标是**最小 MVP**：Coding Agent 连接后台 → 提交任务 → 查看任务 → 更新优化应用。
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
- 映射策略的个人 / Team 偏好（映射来源已记录并展示；回退由 KiroCrew 协调者推荐，不内置回退链）；`replan` 决定的代码校验。
- 完整恢复阶梯 L0–L4、预算、看门狗、条件满足后自动恢复。
- 原生权限批准代理（控制台 / CLI / MCP 可见并可处理，K04）。

## 客户端与控制台（原 M3）
- `octopus connect` 扩展到 Codex 与 Kiro IDE；CLI 命令集（submit / plan / confirm / status / watch / report / approvals / search）。
- 控制台：服务端分页、按仓库 / 分支 / 阶段 / 工具 / 客户端 / 提交者 / 时间过滤、全文搜索；详情中的 diff、门禁与报告。
- Worker 自助接入（一次性接入码，Worker 启动时自行登记 URL 与 Token），本地全栈启动时自动登记 Worker。
- 旧版控制台 `/classic/` 在 React 版功能对齐并经负责人确认后移除。

## 共享与组织（原 M4）
- 组织层，Session 共享给个人 / Team / 公司；Team 执行隔离；排队与配额。
- 原生入口改为单 Session 票据（K03）；Worker Token 移出数据库（K10）。

## 会话运行优化后续（D34）
- 按 `depends_on` 并行执行互不依赖的任务（同一工作区，按 `paths` 判断冲突；默认关闭）。
- KiroCrew 自动影响评估：续作时由 Kiro 判定重跑范围并调整后续任务的 Prompt。
- 工作流编辑器编辑任务契约字段；把各工具实测耗时与通过率提供给规划。
- 访问管理第 3 期审查延后项：`tools_for` 只看 scope、未看账号是否仍为管理员（被降级者的旧 admin Token 仍列出管理员工具）；
  管理员工具的 ID 用 `quote` 但未禁止 `/`；`add_space` 同时给 team_id 与 user_id 时只发 team_id；API 错误体缺 `code` / `error`
  时结构化字段为 null；有 `--username` 但无终端且未设密码环境变量时 `getpass` 报堆栈、交互输入空用户名到登录才失败；
  README 的 connect 示例未带 `--username`；`octopus_environments` 对看不到的 Team 返回空列表，管理员为他人个人空间的会话分配时
  列出的是管理员本人的环境。
- `GET /v1/tokens` 的 `scopes` 是 JSON 字符串（`/v1/admin/users/<id>/tokens` 是数组；控制台已兼容），统一为数组时同步控制台。
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
- 开发成果下载（D36 第 1 期审查延后项）：`preview_spec` 的 `cwd` 未拒绝 shell 元字符且在 `cd` 步骤中未加引号、`install` 中非对象项被静默丢弃、
  符号链接的 `.octopus/preview.json` 被跟随、允许两个 `$PORT` 进程、`next dev` 的本机端口提示为 5173（实为 3000）；`filename*` 未转义 `/`；
  `.envrc`、`.npmrc`（可能含 `_authToken`）与名为 `.env/` 的虚拟环境目录会被打包，已提交到 Git 的 `.env` 仍在 `.git` 对象中；
  同一链接 10 分钟内可反复下载、每次重新打包并写记录；`run.downloaded` 未记 commit；`OCTOPUS-ARCHIVE.txt` 在解压的仓库中显示为未跟踪；
  不支持 HEAD 与 Range；`test_errors_before_streaming_stay_json_and_auth_is_required` 的第一条断言恒成立；此前已准备的新建项目没有 `.git`。
- 远程预览（D36 第 2 期审查延后项）：预览 Cookie 会转发给应用；只有 WebSocket 的页面 30 分钟后按空闲停止（只计 HTTP 请求）；
  以 `setsid` 启动的子进程在停止后存活（直到 Worker 的 cgroup 结束）；`app.log` 不限大小；打开链接的一次性票据进入预览访问日志（`$uri`）；
  管理员预览他人会话未审计；Worker 主机安装在没有 Token 时静默跳过，且 `nginx -t` 失败发生在切换之后；两个会话声明同一固定后端端口时，
  后一个只显示「已退出（代码 1）」。
- 代码仓库凭证（D35 第 1 期审查延后项）：开发默认的密钥文件非原子创建（安装脚本的是原子的）；HMAC 不覆盖行的 id / kind / host
  （能写数据库者可互换行）；克隆中被 SIGKILL 时 `.askpass` 不清理；`git_auth` 也发给不支持它的旧 Worker（其匿名克隆）；
  更换凭证没有 `expected_version`、不写监督审计、不检查已归档 Team；`covers()` 把 `a/b/c/*` 与 `a/b/*` 视为同级、匹配区分大小写；
  `_git` 先截断 stderr 再隐去，跨截断点的秘密可能留下片段；向导列出该空间不可用的凭证；凭证对话框（自动填充、轮换失败清空输入、
  无授权界面、无到期字段）；工作流 GET 不返回其默认凭证（控制台编辑会丢失）；授予用户的平台凭证在 Team 会话中只能显式指定，
  停用后对被授权者不可见；内网自建 GitLab 在允许列表外被 SSRF 防护拒绝（设计 §9.2）。
- 代码仓库凭证（D35 第 2 期「发布」审查延后项）：
  - 没有按会话的发布锁，发布期间可续作 / 关闭 / 删除；发布是同步请求（CloudFront 60 秒）。
  - 发布不检查退役 Worker（`unserved`），已归档的 Team 仍可发布；`X-Publish-Base` 取自 Agent 可写的 `project.json`（应存平台侧的准备基线）。
  - 进行中的合并、未合并条目与新的 gitlink 按原样提交；`ls-remote` 按尾部匹配 ref；主分支前进后仍发送完整历史。
  - GitLab 沿用 MR 时不核对 `source_project_id`；PR URL 未校验为该主机的 https 地址；分支名上限 120（计划为 100），`octopus/a.lock/b` 可通过检查。
  - 控制台对会得到 403 的成员也显示「发布」，未绑定凭证时没有提示；发布中的 `git add` / `reset` 遵从项目本地 git 配置（clean filter、fsmonitor），
    只在 Worker 与 Agent 不是同一系统用户时构成越权。
  - 测试：`test_the_write_credential_never_reaches_the_worker` 的临时仓库检查在删除之后；没有真实的截断 / 篡改 / 外来基线 bundle 测试。
  - 平台升级的最后一步「注册执行边界 Worker」以管理员密码登录：控制台改密码后须同步 `/etc/kirocrew-on/admin-password`，否则每次 `push`
    以 401 结束（其余各步已完成）；可改为不依赖管理员密码的本机注册。
- 会话独立预览域名（D36 第 3 期审查延后项）：没有测试固定 `__main__` 把 `preview_domain` 传给 API（EC2 验收覆盖）。
- 候选提交与命名门禁（D37 第 1 期审查延后项）：
  - 已停止的 Attempt 仍显示「正在运行检查命令」；检查进度只显示条数，不显示「2/3」。
  - 「已验证候选」只在发布按钮旁显示；控制台与 MCP 不显示检查命令的输出末尾；带 `verify` 的审查任务不记录 `checks_pass`。
  - Worker 上的 `/candidate` 与发布没有按会话的锁（180 秒超时后两次调用可能共用 `.publish-index`）。
  - 已验证候选等于克隆基线时不按 `nothing_to_publish` 拒绝；平台不核对推送的 `X-Publish-Commit` 等于已验证候选。
  - 检查命令路径上不设置 `result_missing`；列表视图（`detail=False`）也逐行计算已验证候选。
  - 测试：没有对真实 Worker 运行 Engine 的端到端测试；`check_unknown` 与返工后审查重新绑定只由探针覆盖。
- Feature 报告（D37 第 2 期审查延后项）：OpenAPI 中 `/report` 写成 JSON 回答且缺少 `format` 参数；HTML 中执行次数未经转义
  （整数，纵深防御）；测试未覆盖多任务、验收项 / 建议的上限、转义带来的体积增长与控制台的出错路径。
- 换工具（D38 第 1 期审查延后项）：协调者 `switch_tool` 决定的 `tool` / `model` / `effort` 没有长度上限，被拒时原样写入错误；
  MCP 精简状态中的 `switched_from.reason` 未截断；`switch()` 接受以规划任务（`plan`）为目标、把它换离 Kiro（规划模型恒为 auto，罕见）。
- 批准代理（D38 第 2 期审查延后项）：
  - 控制台每 10 秒读取一次待批准项，每次都向 Gateway 发两次请求（每次签发一个本机 Token，各写一条 KiroCrew 审计）。
    可改为 Worker 端 3–5 秒的共享缓存、缓存 Token，列表读取的超时缩短为 5 秒。
  - 不能答复的人无法展开被截断（160 字符）的内容。
  - `approval.answered` 在时间线与变更记录中没有中文标签，管理员答复他人会话时也未单独标明。
  - 平台在 15 秒后放弃答复时，Worker 可能仍已送达，此时缺少审计。
- 门禁第 3 期（D37 审查延后项）：遗留基线恢复失败（如 `index.lock`）时只显示通用的「Worker 操作未完成」；控制台与报告不显示基线运行
  （进度与命令结果）；派发时未列出本任务不可修改的已验收文件（Agent 只知道规则，不知道具体文件）。
- 看门狗（D39 审查延后项）：报告与 MCP 只显示最终 Attempt 的看门狗记录（重试后恢复的卡住不出现）；旧 Worker 的 `unsupported`
  标记不显示；控制台的自动纠错设置与会话的设置行不显示 `idle_minutes` / `attempt_minutes`；协调者诊断的 Attempt 不受看门狗监视；
  自动重试不带「上次在 N 分钟时于某工具处被停止」的说明。
- 源码准备失败时控制台只显示 `Worker 操作未完成 (ValueError)`：`octopus/worker.py:488` 只透传 `security.Error`，`octopus/sources.py` 的提示均为 `ValueError`（Connector 设计 §7 P1）。
- 向导：导入 / 推荐后切换到模板时来源提示未更新；切换空间未重置推荐 Worker；推荐轮询遇 403 / 404 不停止；提交瞬间在途轮询可能回写草稿。
- 原生入口弹窗缺少账户切换保护；`crew_url` 公网来源未规范化 `:443` 写法。
- 导入说明未列出 ID 改写、Effort 大小写、失败任务序号；document 的 `name` 未用于 Session 名称。
- 构建产物 `no-store`（584 KB 包每次重新下载）；经典版步骤条跳转跳过隐藏任务字段校验。
- `default_url` 由 `0.0.0.0` 拼接（应使用 `probe_host`）；systemd 单元可恢复 `NoNewPrivileges` 等加固项；
  `bootstrap-worker` 不比对已有 Worker 的 URL / Token；记录接受的 KiroCrew commit 而不只是版本号。
- OpenCode 执行未通过（K01）：EC2 上 shell 写文件被 KiroCrew 工具门禁拒绝，需查明命中的门禁层；替代配置 deliver→claude 可用。
