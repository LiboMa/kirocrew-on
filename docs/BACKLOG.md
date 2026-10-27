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
- 映射策略的个人 / Team 偏好（映射来源已记录并展示；回退由 KiroCrew 协调者推荐，不内置回退链）。
- 完整恢复阶梯 L0–L4、预算、看门狗、条件满足后自动恢复。
- 原生权限批准代理（控制台 / CLI / MCP 可见并可处理，K04）。

## 客户端与控制台（原 M3）
- `octopus connect` 扩展到 Codex 与 Kiro IDE；CLI 命令集（submit / plan / confirm / status / watch / report / approvals / search）。
- 控制台：服务端分页、按仓库 / 分支 / 阶段 / 工具 / 客户端 / 提交者 / 时间过滤、全文搜索；详情中的 diff、门禁与报告。
- ~~Worker 自助接入（一次性接入码，Worker 启动时自行登记 URL 与 Token）~~——MVP 0.2.0 P1 Task 6 已做（接口、`worker enroll`、控制台「用接入码」与接入码列表、`boundary add` / `worker-host register` 首次登记改用接入码）。
  - 仍待做：本地全栈（`tools/local_stack.sh`）启动后自动建管理员并用接入码登记本机 Worker（N §5 最后一条）；用接入码更换连接（轮换 Token、换主机，N 第 2 期以后）。
- 旧版控制台 `/classic/` 在 React 版功能对齐并经负责人确认后移除。

## 共享与组织（原 M4）
- 组织层，Session 共享给个人 / Team / 公司；Team 执行隔离。
- **KiroCrew 回收未认领的父会话时杀掉子 Agent**（EC2 2026-10-22，run-16848466…）：Worker 建的 chat slot 被 KiroCrew 急切起了 coordinator 会话但从未认领；内存吃紧（posture=tight）时预取上限为 0，同一边界上新会话建 slot 就回收最旧的未认领父会话（parent-end teardown），正在运行的子 Agent 被 SIGKILL（「killed by Crew (forced stop)」），平台记 native_stopped、不重试。平台把这种停止视为瞬时失败：D57 Task 13（已做：没人经平台打开原生入口时自动重试一次，见 OPERATIONS「门禁与安全底线（D57）」）。根因：KiroCrew 0.10 的 `session.eager_spawn` 默认开启（sections.py:2216）——D63 在边界的 KiroCrew 配置里关掉它（新配置与已有配置都写 false，明确开启的保留）。
- ~~**子 Agent 结束时父会话仍跑一轮 coordinator**~~（EC2 2026-10-22，D63 之后的核对）：Worker 派发时已带 `silent: true`，但 KiroCrew 0.10 只按它关 Slack / Dashboard 通知（slack/gateway.py:10820 起），结果照样注入父 chat slot（`Subagent … → _run_chat in octopus-run-…`，gateway.py:10457），为平台的 slot 起一个 poc-coordinator（kiro 后端）进程并跑一轮——每个任务结束都多一轮 Kiro 用量，进程留到 TTL。待：查 KiroCrew 有无不回报父会话的派发方式（平台以 parent_session 证明归属，须一起设计），或向上游提需求。
  2026-10-23 修复：KiroCrew 0.10 已有 `POST /api/spawn/mark-collected {ids, parent_session}`（它自己的阻塞式 spawn 工具用来表示父会话已拿到结果），Worker 每次派发、续接与模型探测拿到原生 ID 后立即调用，结束时不再注入父会话、不跑这一轮，波次的汇总轮也不再触发；结果照旧从 `GET /api/spawn/{id}` 读取，交付标记与保留时长不变。标记只在 Gateway 内存中：运行中 Gateway 重启后，结束时会照旧跑一轮。
- 用量上限（M4-4 第 3 期）：每人 / 每 Team 按周期（月）的 Kiro credits、token 或分钟数上限（D46 P6a 已有用量记录）；D61 只限同时运行的任务数。
- 原生入口改为单 Session 票据（K03）。~~Worker Token 移出数据库（K10）~~：D59 已做（写入即用凭证主密钥密封，只在请求时解开）。

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
- **方向（负责人 2026-10-19）：每人一个 KiroCrew 边界，一台主机可放多人的边界；Team 会话在提交者自己的边界执行，Team 只共享平台层**
  （会话、工作流、凭证、审查与发布）。原因：KiroCrew 是个人助手——共享 Gateway 时所有人共用 `max_subagents: 3`、同一个 kiro 账号的 Credit 与
  限流（无法按人归属）、只按路由分隔的记忆，一人的长任务、登录过期或部署排空会停住所有人。共享的 `personal` 环境仅作试用、逐步退役。
  待做：边界放置（按主机容量）、每个边界的端口与资源上限（systemd slice）、空闲停 Gateway、Team 会话的执行边界选择、用量按人归属（D46 P6）。
- 扩容 `w2`、`w3`（`deploy.sh worker-host add <名称>`，负责人确认后）。
- 同主机新 OS 用户的专属边界（设计 §4.1–4.3：`boundary add`、`boundaries`、`boundary.sh`）：按上面的方向实现（P4 的 EC2 验收先用一个）。
- 共享环境的记忆分隔（T7）：等待 KiroCrew 提供成员创建与子任务继承成员记忆的接口。
- `deploy.sh space` 定位 Worker 主机上的会话（目前只能到平台主机的边界）。

## KiroCrew 契约后续（2026-10-17 盘点）
- ~~HTTP 请求字段的契约~~（2026-10-18 完成：`tests/kirocrew_contract.json` 的 `http`；查询参数仍未纳入）。
- ~~已有边界的 config.json 仍带旧的无效键~~（2026-10-18 修复：`seed()` 迁移移除，原生测试核对现有边界）。原文：已有边界的 config.json 仍带旧的无效键（`memory.enabled`、`dashboard.host` / `port`、`workspaces.default.path`；KiroCrew 加载器在 EC2 上如实报出），
  无害；需要时在 `seed()` 中迁移移除，原生测试即可同时核对现有边界的配置。
- ~~协调者屏蔽 monitor 工具目前只靠 kiro-cli 执行 `excludedTools`~~（2026-10-18 修复：同时写 `managedToolPolicy.exclude`）。原文：KiroCrew 自己的 kirocrew-core 按 `managedToolPolicy.exclude` 过滤，可再加一层。
- `includeCrewContext` 不在 kiro-cli 文档字段内但被 KiroCrew 读取（默认即 true）；kiro-cli 2.29.1 接受未知键（EC2 以 `agent validate` 对照核实），无须改动。

## D48 发布到新仓库的终审延后项（2026-10-17）
- ~~会话页开启自动发布时按设置者核对凭证~~（2026-10-18 修复：同时按创建者核对）。原文：自动发布以创建者执行：Team owner 用自己的个人凭证开启时之后每次失败（「需要处理」会说明）；应按创建者核对。
- ~~启动时新建源码 `publish: {auto: true}` 未给仓库时被接受~~（2026-10-18 修复：启动时 409）。原文：完成后才失败；会话页同样的设置返回 409——启动时也应拒绝。
- ~~凭证下拉只按写权限与停用过滤~~（2026-10-18 修复：按空间过滤并显示范围）。原文：会列出服务端会拒绝的凭证（其他 Team 的 Team 凭证、未授权的平台凭证），也不显示 `repos` 范围。
- 记下发布目标后会话页不能换凭证；建议只在组件挂载时读取（规划完成前打开的页面不会预填）。
- ~~「发布到已有的空仓库」在时间线也显示为「已新建仓库」~~（2026-10-18 修复：`run.repository_used`，`run.published` 带仓库地址）。原文：`run.published` 不含新仓库地址，已有空仓库的事件没有 url（R8）。
- 建仓库的审计与 `publish_target` 分两个事务（两者之间退出会丢审计）。
- 托管方错误：`/orgs/{owner}/repos` 对他人账户返回 404 时只说「HTTP 404」；改名仓库的 301 为笼统的 publish_failed；`find_repository` 的 403 提示 Pull requests 权限。
- ~~`publish_target_fixed` 区分 owner / 仓库名的大小写~~（2026-10-18 修复）（GitHub、GitLab 不区分）。
- git 源码发布在多项核对同时失败时先报出的错误代码与此前不同（只有顺序）。

## 运维与 Harness 防线（复盘 2026-10-17 的后续）
- ~~部署维护期~~（2026-10-18 完成：Gateway 需要重启时先排空，见 OPERATIONS）。原文：`deploy.sh push` 先让平台进入维护（新会话照常登记但暂不派发，运行中的任务等待完成或在重启后由 L0 重试），部署完成后恢复；
  空闲检查只是时间点（`run-f7670…` 在部署中创建）。
- KiroCrew 报错词表的契约：L0 依赖的报错文字（`ACP process exited`、`AcpProcessDied`、`process group … still has members`、取消）在部署时的原生测试中
  核对已安装 KiroCrew 源码仍含这些文字，升级后措辞变化即提示。

## D46 P2 角色卡片的终审延后项（2026-10-18）
- ~~审查独立性的「作者」口径与恢复换工具的 `reviewed_backend` 不同~~（负责人 2026-10-21：test 任务不算作者，换工具的检查已统一）：没写 kind 的任务不算作者；`reviewed_backend` 取最新已验收的非审查任务的
  实际后端，并把 test 任务算作作者。P3 的 Evaluator 门禁前统一。
- ~~卡片文件格式错时在派发时才报 ValueError / AttributeError，且每次调用重读文件~~（2026-10-20 修复：读一次、报出是哪张卡片）。
- ~~续作不能去掉任务的角色（`null` / `""` 被拒）~~（2026-10-20 修复：去掉角色，类别不变）；改类别时须同时换成相容的角色。
- ~~控制台修订对比中角色的取值显示为 id（security_test → evaluator），未用中文标题~~（2026-10-20 修复：显示角色标题）。

## D46 P3 Evaluator 门禁的终审延后项（2026-10-18）
- ~~平台追加的 Evaluator 被重新规划替换后再次追加时可能复用 id~~（2026-10-18 修复）。原文：可能复用 id `evaluator`（旧任务的 Attempt 显示在新任务下）：应像 `planning.expand` 一样排除历史 id。
- ~~追加 Evaluator 不记修订、不升版本：下一次续作的修订差异会把它算作用户的改动~~（2026-10-20 修复：平台追加的 Evaluator 与 Knowledge 任务各记一次修订「平台追加」并升版本）；仍不进规划说明（时间线有记录）。
- ~~停在「验收待确认」时停止、再请求诊断~~（2026-10-18 修复：诊断入口拒绝并说明）。原文：Evaluator 任务仍为 awaiting_acceptance，被当作 native_error 交给协调者；应要求首个未通过任务为 blocked / failed，或停止时重置。
- ~~缺项、缺证据记为 failure_type gate；报告一节在门禁不适用的会话也出现~~（2026-10-18 修复：contract，fail 为 implementation；报告只在门禁生效时）。原文：报告的「验收（以 Evaluator 为准）」在门禁不适用的会话也会出现。
- 「验收待确认」时控制台的发布区不显示发布按钮（服务端允许手动发布）：保持保守，待负责人决定是否在确认前允许发布。

## D46 P6 用量的后续（2026-10-19）
- 工具时间线：KiroCrew 0.10.0 的子任务不写 crew log，`state.json` 只有 `turns` / `last_tool`；需要 KiroCrew 为 subagent 记录工具调用，或平台以 PostToolUse
  hook（带 `subagent_id`）自记时间。
- KiroCrew 只把最后一次尝试的用量写成一行（`_RunCreditAccounting.total` 未持久化）：同一运行内的瞬时重试的 token 不在其中；credits 已含全部。
- 按人 / 按 Team 的用量汇总（管理员视图，每人一个边界后即按人的 Kiro credit）；按费用设预算（负责人决定）。
- 协调者诊断（recoveries）的用量未计入会话合计，报告未注明。
- 终审延后：Python 与控制台在 token / 费用的进位边界上取整不同（1250 → 1.2k / 1.3k）；结束后的 `turns` 来自 state.json（受批准门控的工具请求数），
  文档写成 /api/spawn/{id}；用量查询 `days=2` 而文档写 7 天；每次视图（含会话列表）重复解析证据；会话「用量」的时长是执行时长之和，易误读；
  续接的 claude 对话的 cost 是每轮还是累计未确认（EC2 核对，以免合计重复计算）。

## D46 P5a 保留上下文的返工的终审延后项（2026-10-19）
- ~~P5b：运行中补充说明与释放对话~~（2026-10-19 完成：只支持 Kiro 任务的 `interrupt`；删除会话时 `release`）。后续：
  `follow_up` 需由平台跟踪 KiroCrew 另起的续接运行（或改为下一次返工时附上）；claude / codex 的补充说明待 KiroCrew 支持；超时时说「送达情况未确认」；
  `queued_not_started` 单独提示；`task_id` 类型与控制字符校验、schema 的长度约束；控制台在会话刷新后保留错误、归档 Team 不显示；
  release 用短超时、记录未释放的对话；补测试（续接后的 Attempt、Team 成员 403、waiting_approval、上游 502 经平台）。
- ~~时间线不说明续接被拒（只在事件内容中有 `continue_refused`）~~（2026-10-20 修复：`attempt.dispatched` / `attempt.running` 带原因，变更记录可见）。
- `health()["continue"]` 恒为 true：前置的 KiroCrew 早于 0.10 时每次返工都记 `continue_refused: HTTP 404`，可与接受的 KiroCrew 版本挂钩。
- `from` 只校验格式，未核对它的 dispatch.json 确有该对话。
- ~~原生错误或看门狗停止之后的返工也会续接坏掉的那个对话~~（2026-10-20 修复：上一轮对话本身出错时全新派发，工作没做到位时才续接）。
- 文案在 spec、任务卡片与报告间略有不同；~~MCP `latest.continued` 易与 `octopus_continue` 混淆；Skill 未提及~~（2026-10-21 Skill 写明二者无关）。
- 测试缺口：引擎侧 `continue_refused` 的证据 / MCP / 报告、`rerun` 类的续接、经引擎的外来对话。

## D49 每人一个边界第 1 期的终审延后项（2026-10-19）
- ~~再次 `boundary add` 未带 `--memory` / `--cpu` 时资源上限回到默认值，并因 drop-in 变化排空重启 Gateway~~（2026-10-21 修复：未给出的沿用现有值）。
- ~~`al2023.sh` 以 `env` 参数传管理员密码（短暂出现在 `/proc/<pid>/cmdline`，同主机现有多个 `crew-*` 用户）~~（2026-10-21 修复：赋值前缀，测试防回退）。
- ~~`--owner` 区分大小写（用户名存为小写）；`boundary add` 未传 `AWS_REGION`~~（2026-10-21 修复）。
- ~~启动向导的 KiroCrew 建议（`/designs`）用 `eligible[0]`（共享的 personal），没有优先此人自己的边界~~（D49 第 2 期已改为平台的默认位置 `placement.default`）。
- 边界在 `kiro-login --boundary` 之前就登记并开始接收此人的会话（Kiro 任务等待登录）：可先以停用状态登记、登录后启用。
- ~~每次 `push` 都 enable 每个边界（有意停掉的边界会回来）~~（2026-10-21 修复：`systemctl disable` 的边界不检查、不重启）；`push` 与 `boundary add` 之间没有 `flock`；`toml_set` 迁移只作用于主边界配置；
  库的变量名（ETC、DATA、SYSTEMD）过于通用。
- 测试缺口：Team 会话不受 R6 影响；登记测试 patch 了 `validate_worker`（与 personal 的 host_principal 交叉检查未覆盖）；~~`boundary.sh` 不在 `bash -n` 列表~~（2026-10-20 已加入）。
- 边界的移除目前为手工步骤（OPERATIONS）：可加 `deploy.sh boundary remove`。

## D46 P4 Knowledge 的终审延后项（2026-10-19）
- 团队层知识（负责人 2026-10-19：P4 验收后排入）：Knowledge 任务的条目以 Draft PR 提到仓库的知识文件（如 `.octopus/knowledge.md`），由人审核合并；
  跨人、跨执行环境，补上 KiroCrew lessons（个人层）自动写入、无人审核的缺口。
- ~~一个会话有两个平台追加的 Knowledge 任务时，只写第一个（`_knowledge` 取首个已通过的）~~（2026-10-21 修复：每个 Attempt 各写一次）。
- ~~删除过的条目以后被列出时会再次写入（`known()` 不算已删除的）~~（2026-10-20 修复：同一执行环境、同一仓库不再写入，记为 skipped）。
- ~~Worker 删除时忽略 KiroCrew 的 `{"ok": false}`，`GatewayError` 未转译~~（2026-10-21 修复：拒绝报 409、出错报 502、KiroCrew 已没有时视为已删除）；写入时 KiroCrew 5xx / 401 记为最终的 refused，不重试。
- ~~等待执行环境恢复期间没有退避（每个 tick 重发，最多 600 秒）~~（2026-10-21 修复：每 30 秒重试一次）。
- 仓库标识可能碰撞（`github.com/o/r_x` 与 `github.com/o_r/x`）：可对 host/path 一并散列。
- ~~`_mark` 不拒绝符号链接的标记文件（仓库提交了 `.octopus-kb/<key>` 符号链接时 `touch` 会跟随）~~（2026-10-20 修复：标记文件与 `.git/info/exclude` 也拒绝符号链接）。
- 测试缺口：共享 → 专属（改派后重新准备写标记、`lessons: true`）、第二个 Knowledge 任务的写入。
- OPERATIONS「专属环境内属于同一空间」没有考虑先共享、后改为专属的 Worker（带着当时的 KiroCrew 自动 lessons）。
- 控制台：~~任务已完成而没有可沉淀的条目时仍写「Knowledge 任务完成后…」~~（2026-10-20 修复）；`steps()` 接受用户提交的 `role: knowledge`（不沉淀）；
  KiroCrew 报告替代了旧条目时，旧的台账行仍为 written；会话删除后其写入的 lessons 只能在 KiroCrew 中删除（没有执行环境级的列表）。

## D50 编码技能包的终审延后项（2026-10-19）
- ~~报告：Attempt 证据里的 `skills` 不是列表（如被改坏的 dispatch.json）时 `report._attempt` 抛 TypeError~~（2026-10-20 修复）。
- ~~重新规划的提示最长约 7838 / 8000 字符（各输入取上限时）~~（2026-10-21 修复：重新规划用精简的角色与技能目录，最长约 7350，测试要求 ≤ 7400）。
- ~~任务卡片显示的是计划的 `skills`（旧 Worker 未交付时也显示）~~（2026-10-20 修复：卡片取最近 Attempt 的证据，结束而未交付时写明）；MCP 精简状态仍是计划的；报告取证据。
- ~~`systematic-debugging` 的 SKILL.md 仍有一处「Ask for help」~~（2026-10-20 修复：改为写 blocked，测试禁止该说法）。
- 丢失回执的重派发跨 Worker 升级时（请求体多了 `skills`）按请求散列判为 409 conflict：与 `role_cards` 同一个已有问题。
- 以后（规格 §3）：KiroCrew builtin skill 按后端评估后纳入；返工自动附 `systematic-debugging` / `receiving-code-review`（需负责人认可）；
  要求交接写明读过的技能（使用证据）。

## D49 第 2 期会话的执行位置的终审延后项（2026-10-20）
- ~~待负责人裁定：其他成员在创建者边界上下达指令~~（2026-10-20 按推荐的 A 实现为 R10：只有边界主人下达指令与把工作放到其边界上）。
- R10 专项审查延后项：~~「改派停下的会话」需要把已完成任务的改动带到新环境（如从原 Worker 的 `/publish/bundle` 播种）并重置任务与候选引用后
  才能提供~~（2026-10-26 由 D74「接手」解决：同一会话连同完整历史搬到接手人的边界，提交 ID 不变，候选与审查照旧对得上）；`accept` 仍可由其他成员确认（会在创建者边界上启动 Evaluator / Knowledge 平台任务，规格允许）；`/classic/` 不认识 `direct`；
  普通成员分配时的 409 文案；MCP `octopus_continue` 带 `credentials` 时先重绑再被 403 拒绝（非原子）；Team owner 删除创建者边界上的 Team
  lessons、任一成员启动预览（以创建者身份运行 `.octopus/preview.json`）未在 R10 中列出。
- ~~改派后 placement 不更新；本人边界停用时提示说「没有边界」~~（2026-10-20 修复：分配后记新的位置；停用时提示「已停用，请重新启用」）。
- 向导：TasksStep 的模型优先取「生成建议的 Worker」而非默认位置；切换空间不重置建议 Worker（原有问题）；localStorage 中的旧草稿恢复原先预选的
  Worker（记为 requested）。
- ~~`/v1/placement?run_id` 向其他成员列出创建者的边界名称与模型，`own` 指「创建者的」：在接口说明中写清~~（2026-10-21 写入 OPERATIONS）。
- ~~`Service.placement(team)` 不看归档~~（2026-10-20 修复：与新建会话一样 409 team_archived）；其他成员打开原生入口时得到「Worker 不属于本次执行空间」而非 R7 的原因；测试替身 `placementOf` 不含成员边界规则；
  「KiroCrew 建议默认用 default」没有测试。

## MVP 0.2.0 P1 打底的终审延后项（2026-10-26）

终审的严重与重要项已在 P1 修完。次要项 2026-10-27 处理如下：

- ~~接入码失败计数可被绕开~~：Worker 回的任何错误都映射成 503 `worker_unavailable` 并计数，只有「被别的请求抢先用掉」不计；`/health` 不是对象时也计数。
- ~~Worker URL 的回环、重定向与大小~~：回环地址只在平台主机本机发起的登记中允许；平台对 Worker 的请求一律不跟随重定向；`/health` 响应最多 1 MB；URL 不是字符串时返回 400。
- ~~`check` 的类型~~：必须是布尔值。
- ~~签发人失效~~：签发人停用或不再是管理员，他签的码立即失效；码因失败 5 次作废时记审计 `worker.enrollment_retired`。
- **N 的「连续」失败 5 次**：不用改。码只能用一次，成功即用掉，所以它的失败一定是连续的。
- ~~Worker 容量的 `running_attempts`~~：只计 24 小时内派发、还没有结果的尝试，结果缓存 30 秒。
- ~~控制台接入码列表~~：每 30 秒重新读一次；用接入码自行登记的 Worker 出现在列表里时会高亮；撤销时码恰好被用掉，会显示原因并刷新列表。
- ~~测试覆盖~~：GET 审计的固定清单里，每条路由要么在本测试里请求，要么明确记为由预览网关的测试覆盖；伪造的 `CloudFront-Viewer-Address` 拆不开限流，有测试。
- ~~`kirocrew-on-worker-pool.yaml` 引用已删的输出~~：说明已改，池模板在 P3 重做。
- **不改**：wheel 用户不给配置时，指标日志写到当前目录下的 `state/logs`，这与其他日志的默认目录一致（`log_dir` 可配）。

## D74 接手共享来的会话的延后项（2026-10-26）

- 接手前提交的「对话中的改动」不加版本，在新环境也不补跑检查（提示里写明「接手前提交，没有运行检查」）；接手失败时它仍留在会话里（原环境的提交是真的）。
- 不在会话分支上的旧候选（被改写的、因疑似凭证未移动的）不随 bundle 带走：绑定到它们的审查在新环境里取不到那次提交。
- 旧环境的工作区与原生会话的清理策略；接回时留下的 `<run>.before-<时间>-<随机>` 目录。
- 真实的跨边界接手要在 EC2 用两个人的个人执行环境实测（需要负责人指定第二个账号）。

## D52 团队层知识的终审延后项（2026-10-20）
- ~~engine 的结果计数（约 346 行）不计 `kept`；`knowledge.py` 模块说明仍写「共享环境不沉淀」~~（2026-10-20 修复）；`kept` 条目在控制台不能删除（删除只针对 lessons）。
- ~~`publish.check_branch` 未保留 `octopus/knowledge*` 前缀：代码发布可以指定这个分支名，被误认为打开的知识提议~~（2026-10-20 修复：拒绝并说明）。
- ~~「已含本会话的条目」路径不记凭证使用与审计（只有时间线）~~（2026-10-20 修复：记 credential.use，result unchanged）；GitLab 并发写同一提议分支时，`last_commit_id` 只在读到文件时带上。
- 迁移 8 重建表依 rowid 顺序复制；发布时知识提议同步调用托管方 API，增加一次发布的耗时（数个请求）。

## D58 本地 / 远端联动的延后项（2026-10-22）

- 双向合并：sync 之后远端又有新提交时，本地须先 pull 再 sync，平台不合并。
- 控制台上传 bundle；控制台「复制 CLI 命令」。
- 多个本地仓库关联同一会话（后写的生效）。
- §7 验收 1 的四个客户端：Codex、Kiro IDE 待负责人在各自客户端核对 pull / sync。
- 终审延后的次要项：~~`.pull.bundle` 固定路径（并发拉回互相干扰）~~（2026-10-26 修复：每次拉回各用一个文件，接手与拉回可同时进行）；~~API 允许 `head == old`（0 个文件的同步）；bundle 链接文档写 Read 而
  `required_scope` 要求 execute（只读 Token 不能拉回）；20 MB 原始请求体在鉴权前读入内存~~（2026-10-26 修复：409 `nothing_to_sync`；拉回为 read；先鉴权再读）；提交是否本仓库只比仓库不比 ref；~~login 在
  已存在且权限较宽的文件上先写后 chmod、吊销登录 Token 失败时留下未保存的 30 天 Token~~（2026-10-26 修复：先收窄再写；先保存再吊销）；~~`report --json` 无效；`octopus -h` 不列使用者命令~~（2026-10-26 修复）。

## D56 控制台过滤与 diff 的延后项（2026-10-22）

- 会话数上千后为过滤与全文建索引（SQLite FTS5 或摘要列）；现在逐行扫描并解析快照。
- 列表按创建时间排序；按最近更新排序需要另一种游标（更新时间会变）。
- diff 的逐行评论、跨 Attempt 的累计 diff（相对会话基线的总改动）。
- EC2 实测（2026-10-22）：run-b0b3d23c 的候选含 `.kiro/settings/.kirocrew-cli-settings.lock` 与 `.kiro/settings/cli.json`（KiroCrew 的工具配置）；考虑把 `.kiro/settings/` 加入候选与下载排除（left_out）。
- 终审延后的次要项：已加载的各页都每 10 秒刷新（设计只要求首页）；已跟踪文件中被删掉的硬编码密钥仍出现在 diff 的删除行；含 tab / 换行 / 引号的路径在 AttemptDiff 中匹配不到；「清除筛选」不清除搜索与状态分组、空状态无此按钮；context 的 runTotal 未用；没有行级条件时仍解析快照；工具筛选只看任务当前的 spec；切换空间时短暂显示上一空间的列表。

## EC2 现场发现的 Harness 问题（2026-10-21）

- ~~执行环境内存不足时用户看不见~~（D60 已做：排队时「需要处理」写「排队：等待执行环境资源（执行环境内存不足）」）：owner 边界的 KiroCrew Gateway 因可用内存低于派发门槛推迟所有子 Agent 启动约 40 分钟，会话只显示「运行中」与「原生任务归属当前不可核实」。根因：`/tmp`（tmpfs 7.7 GB）被 Agent 遗留的下载（Playwright / Chrome 库、venv）占 4.7 GB，另有 7 个空闲的 kiro-cli-chat 进程链（约 2.3 GB）未被回收（均经负责人同意清理）。仍未做：Agent 临时文件改放磁盘（D57 Task 16，暂缓，见下）；回收空闲运行时；Gateway 的「可用」与 MemAvailable 口径不一致需核对。
- ~~交付任务只产出不入库的产物（patch、预览页、本地分支）时 `diff_present` 必然失败~~（D57 已做：没有入库改动只记提示）（run-9dce9610，负责人以 kind=test 绕过）：给 deliver 加「不入库产物」的存在性验收，候选自动提交时排除这些产物；或在规划阶段就按门禁校验契约。
- ~~评审任务把评审写成仓库文件，`review_bound` 连续三次拦下~~（D57 已做：平台撤回审查改的文件、移出新建的文件，记提示，审查结论照常采用）（run-b0b3d23c）：重试时明确要求写进交接、删除文件，或由平台撤销评审任务的文件改动后按结论判定。

## D57 门禁减负与安全底线的延后项（2026-10-23）

已做的与已知局限见 OPERATIONS「门禁与安全底线（D57）」。
- **暂缓，等负责人决定**（负责人 2026-10-22：新增的拦截、等待或确认须先经负责人同意，不能卡用户）：安全阻塞状态与人的判断（Task 8）、误报记忆（Task 9，迁移 11 `secret_allowances` 留给它）、判断后复扫（Task 10）、预览 / 下载 / 不入库交付物的扫描与拒绝（Task 11，含合同的 `untracked` 字段与候选排除）、有提示就不自动发布（Task 12）。需要先出一版不卡用户的方案，经负责人同意再做。
- **Task 16 执行边界的临时目录改放磁盘**：在分支 `park/d57-t16-tmpdir`，未部署。2026-10-23 EC2 实测 `/tmp` 31 GB 只用了 687 MB，部署要重启每个 Gateway，暂不值得。部署前必须先改：`deploy.sh space` 在 crew 安装失败时也设了 `TMPDIR`，应只在成功时设。
- **打码的展示局限**（Task 6b / 7b 之后仍在，判定不受影响）：看不到私钥 BEGIN / END 时正文不认（pytest 截断的 diff、`git diff -U0`）；不足 40 个字符的片段；一行里的列表 repr 与 PHP / Perl `.` 拼接逐行打码；行号宽度变化处拆成两处；`docker compose -t` 约 40 字节的前缀与交错的容器日志；`cat -n` 下 BEGIN 片段里的 END 被一并打码（此前就有）。
- **Task 15b 提示文案**：恢复被拒的原因改成写给人看的话（RecoveryPanel 里的 `paths_add`、retry / rework）；报告（`report.py`）的门禁用中文名；不合法结果的提示把「要求修改」和英文决定列表混在一起；TaskList 的说明改为「平台不会运行以上检查（…）」；停止提示的上限取自当前预算；`gates.py` 结论词列表漏了 fail / rejected；ApprovalsCard 的升级提示只在会话 `waiting_approval` 时出现。
- **Task 3b 的嵌套仓库情形**：快照把嵌套仓库记为一项 `sub/`，审查又删了 `sub/.git` 时，其中的文件被逐个当作新建文件移出（可以拷回）。`review-removed` 也不自动清理，每个审查 Attempt 都可能留下一份。
- 终审延后的次要项：
  - 检查：环境失败的识别只看输出尾部，断言里引用的「Committer identity unknown」也会被当作环境原因；Worker 中途退出会留下临时 gitconfig；只有项目根与 `src/` 下的模块算项目自己的；建议检查较慢时可能占满 20 分钟总时限，挤掉必过检查；`verify_basis` 只引一个字也算需求原文。
  - 重试与审查：重试后的诊断丢了第一次的原生错误；Agent 自报的 `failure_type: contract` 也能拿到重新规划豁免（至多 2 次）；撤回成功但回执丢失时没有 ⚠；会话停止时取消的审查 Attempt 不撤回审查改动。

## D55 M2 剩余（Request、准入规则、映射偏好）的延后项（2026-10-21）

- 「重新发起」：同一 Request 产生第二个会话（控制台与 MCP）；表结构已允许一对多（负责人：先一对一）。
- 模板 / 工作流中 `tool: auto` 的步骤按偏好补上（设计 §4 第 2 点）：现有契约的工具只能是 kiro / claude / codex / opencode。
- 用户 CLI 的 Request 与规则命令（M3-2）。
- 预算按个人 / Team 配置（设计 §8.1，可放进 `policies`）。
- 平台追加 Evaluator 时参考用户的 evaluator 偏好（工具可用且不是作者工具时采用）：现有契约里唯一由平台自己选工具的地方，最接近设计 §4 第 2 点。
- 终审延后的次要项：`https://…/R.GIT` 的大写 `.GIT` 不去掉（规则侧先小写再去后缀，两边应一致）；没有仓库段的模式（`github.com/LiboMa`）
  永不命中却能保存，应提示或要求 `*`；偏好的 `model` 限制字符集；偏好一节按整行截断；`plan=match` 选中工作流时 `explicit` 不应记
  `workflow_version`；规格 §2 的「用户给出的检查命令」与规划后的 `requested` 尚未进 Request；重试期间偏好变化使同一 `request_id`
  得到 409；每个作用域的规则数无上限；Skill 写明回执的 `request_id` 不是幂等键；`octopus_start` 说明写默认先确认；控制台删除规则无二次确认、
  按钮无区分的无障碍名称、已归档 Team 出现在作用域下拉、opencode 默认禁用仍列出；`_followed` 不比模型；OPERATIONS「Coding Agent 不能改准入」
  应改为「MCP 不提供写入工具」（connect 的 Token 带 manage scope）。

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
- 开发成果下载（D36 第 1 期审查延后项）：~~`preview_spec` 的 `cwd` 未拒绝 shell 元字符且在 `cd` 步骤中未加引号~~（2026-10-20 修复：本机步骤加引号、拒绝控制字符）、`install` 中非对象项被静默丢弃、
  ~~符号链接的 `.octopus/preview.json` 被跟随~~（2026-10-20 修复）、允许两个 `$PORT` 进程、`next dev` 的本机端口提示为 5173（实为 3000）；`filename*` 未转义 `/`；
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
- 重新规划（D41 审查延后项）：`snapshot.replan.notes`（改名的 ID、去掉的依赖、截到 6 个任务）控制台不显示，`types.ts` 无 `replan` 字段；
  `_task_start` 把被删除的失败任务的候选算作其他任务的工作，只由失败 Attempt 改过的文件不受 `artifacts_locked` 保护；
  缺少「重新规划之后续作依赖任务」的端到端测试（`depends_on` 改名与返工后不重跑规划已有单测）。
- 会话预算（D42 审查延后项）：被阻塞的会话每次轮询都重新计算预算（逐条解析 Attempt 证据与纠错记录，可改为 SQL `json_extract` 计数）；
  部署时已有会话按创建时间起算（可退而取最近一次续作的修订时间或手动诊断）；控制台续作对话框不能调整 `max_retries` / `budget_minutes`，
  且续作带 `recovery` 时整体替换原设置。
- 等待与自动恢复（D43 审查延后项）：健康检查改写 `workers.config`（`checked_at`）时恰好打开原生入口会得 409「请重试」（入口比对整个 config）；
  手动「刷新模型」与重新登记（含部署后 API 启动时的登记）不写 `checked_at`，「最近检查」最多消失 10 分钟；停用 Worker 上 `waiting_worker` 的会话提示未写「重新启用后自动继续」；`resume` 每个周期逐条解析证据 JSON
  （可改 SQL `json_extract` 过滤）；检查间隔用墙钟、`/health` 非对象时中止本轮、边界不同的警告每 10 分钟重复。
- 计划确认（D44 审查延后项）：旧版控制台 `/classic/` 显示原始状态 `awaiting_confirmation` 且无确认操作；控制台 `AppPreviewPanel` / `PublishControl`
  的已停下状态集合未含该状态；看门狗停止失败后 KiroCrew 杀掉进程（`ACP process exited`）时按 `runtime_exited` 重试，看门狗原因丢失；
  模板中名为 `plan` 的 Kiro 步骤仍被当作规划任务跳过 D37 门禁（与 D44 修复前同源）。
- 需求中指定（D45 审查延后项）：修订快照与差异不显示 `requested` 的变化（续作改工具后去掉指定只能从「工具 A → B」间接看出）。
- 源码准备失败时控制台只显示 `Worker 操作未完成 (ValueError)`：`octopus/worker.py:488` 只透传 `security.Error`，`octopus/sources.py` 的提示均为 `ValueError`（Connector 设计 §7 P1）。
- 向导：导入 / 推荐后切换到模板时来源提示未更新；切换空间未重置推荐 Worker；推荐轮询遇 403 / 404 不停止；提交瞬间在途轮询可能回写草稿。
- 原生入口弹窗缺少账户切换保护；`crew_url` 公网来源未规范化 `:443` 写法。
- 导入说明未列出 ID 改写、Effort 大小写、失败任务序号；document 的 `name` 未用于 Session 名称。
- 经典版步骤条跳转跳过隐藏任务字段校验。（构建产物的 no-store 已改：带哈希的 /assets/ 长期缓存，见 test_static_cache；2026-10-23 起 GET 的文本响应 ≥ 1 KB 时按 Accept-Encoding 以 gzip 发送，POST 不压缩，见 test_compression）
- `default_url` 由 `0.0.0.0` 拼接（应使用 `probe_host`）；systemd 单元可恢复 `NoNewPrivileges` 等加固项；
  `bootstrap-worker` 不比对已有 Worker 的 URL / Token；记录接受的 KiroCrew commit 而不只是版本号。
- OpenCode 执行未通过（K01）：EC2 上 shell 写文件被 KiroCrew 工具门禁拒绝，需查明命中的门禁层；替代配置 deliver→claude 可用。

## 延后（负责人 2026-10-23：不是强依赖就之后做）

- 需求改了，其他任务的说明里仍嵌着旧需求（EC2 run-588bc3d5 复测）：续作只改会话需求与所选任务，协调者靠重新规划解决（多一轮）。可选：让规划者写任务说明时引用需求而非复述需求，或续作改了需求时提示哪些任务的说明含旧口径。
- ~~部署 5fd7976（GET 文本响应 gzip）与 2c3206e（D57 写入时打码）~~（2026-10-23 随 3af7331 部署；EC2 核对：控制台脚本 731 → 219 KB、会话列表 205 → 50.5 KB）。
- 上游简报工具：每周列出 KiroCrew 自我们钉的版本以来的发版、P0 变化、与派发 / 子 Agent / 成员 / Gateway 相关的 PR（`docs/research/2026-10-23-kirocrew-release-cadence.md` §5）。
- 向 KiroCrew #11576（Gateway 事件循环卡住）提交我们在 EC2 的记录：对外操作，须负责人同意。
- D72 的服务层草稿（`octopus/follow_up.py`、`tests/test_follow_up.py`，未接入）放在本地分支 `park/d72-follow-up-draft`；D73 的 P4 再取用。

## MVP 0.2.0 输入：KiroCrew 原生能力（负责人 2026-10-23）

- 调研：`docs/research/2026-10-23-kirocrew-native-capabilities.md`（成员与小组为主）。候选：会话内并行（独立 spawn + `/api/spawn/lanes`）、角色成员化试点（先在测试边界核实成员设置是否作用于 `crew=` 派发）、频道用于小规模多 Agent 讨论、导出用于证据归档。不接：Conductor、KiroCrew 动态工作流、并行波次、steer `follow_up`。改变体验的须负责人确认。

## MVP 0.2.0 重要更新：两种部署模式与 Worker 自助申请（负责人 2026-10-24）

负责人在对话中确定的方向，作为 0.2.0 部署与扩缩容设计的输入。「〔设计者决定〕」是负责人未逐条确认、由设计者定的，可调整。

- **两种部署模式，都一键部署。** 安装方式对标 KiroCrew：一行安装脚本（`curl -fsSL <下载地址>/install.sh | sh`）、pip（指向频道索引）、Docker、源码构建。
- **单机版：控制面与数据面在同一台 EC2 上。**
  - 用户先在自己的 AWS 账号里开一台 EC2、登录进去，再执行一行安装脚本；装在这台机器上，不从用户电脑远程创建。
  - 脚本自己检测系统与架构，开的是什么就装在什么上；检测到 x86_64 时照装，但明确提示推荐 ARM64（Graviton），开机器前的说明也把 Graviton 写成首选。
  - 支持 Amazon Linux 2023 与 Ubuntu 22.04 / 24.04，ARM64 与 x86_64；其他系统说明受支持的系统后退出，不硬装。〔设计者决定〕
  - 装好 KiroCrew 与 KiroCrew ON（平台 API、Engine、数据库、Worker、Gateway，systemd 常驻），用这台机器的 IAM Role 或用户提供的 AK/SK 在其账号里建 CloudFront 回源到本机（回源密钥保护，同现有 EC2），建管理员，打印控制台地址与初始密码。
  - 以后可以升级成集群版：这台作为控制面，再按向导加数据面，不必重装。〔设计者决定〕
- **集群版：控制面与数据面分开部署，向导分两步，部署套件统一掌控。**
  - 用户用自己的账号登录控制台，自助「申请 Worker」，申请时填入自己的认证信息：Kiro CLI、Claude Code、Codex，一人一 Key。
  - 认证信息不另设集中存放（负责人 2026-10-25：不需要 Secrets Manager）：直接写进该用户执行边界里各工具自己的凭证位置，随用户的 EBS 卷走。
  - 后台自动分配 Graviton Worker，自动加入并纳管：开机、安装、注册、证书、健康检查对用户不可见；控制面完成初始化与集成注册。
  - Kiro CLI 用设备码登录，由用户自己登录自己的：申请 Worker 时控制台显示设备码，用户在浏览器里确认一次（负责人 2026-10-24 已同意）；登录状态存在用户的 EBS 卷上，换机器不用重登。
  - 参照业界控制面 / 数据面做法：Kubernetes 节点加入（一次性 bootstrap token + 证书签发）、GitHub Actions 自托管 Runner 注册令牌、EKS 托管节点组；控制面持有期望状态并调度，数据面只认短期凭证、可随时替换。
  - 已有的输入：`docs/superpowers/specs/2026-10-24-worker-enrollment-design.md`（一次性接入码、`worker enroll`、平台照旧带 Token 核实 `/health`、Token 不经人手）。负责人 2026-10-24 决定把它放进 0.2.0 一起设计；里面待定的三点——谁能签发、有效期、部署脚本何时改——届时随「Worker 自助申请」一起定。
- **共享 Worker 与专用 Worker，两种申请都支持，用户自选。**
  - 共享 Worker：不独占 EC2，多人共用主机，每人一个隔离的执行边界（各自的执行用户与端口）；后台由 Auto Scaling 配合。
  - **每人独占一块 EBS 卷作为工作空间**（加密）：工作区，以及各 Agent 的家目录、登录凭证、本地数据库；KiroCrew 记忆也放卷上，换机器不用重新登录〔设计者决定〕。
  - 池整体 CPU / 内存压力过高时，ASG 开新 Worker 节点，挂上独立的 EBS 开始工作。
  - 专用 Worker：独占一台 EC2，同样挂用户自己的 EBS。
  - 共享与专用之间切换：只是把 EBS 改挂到另一台 EC2；在两次尝试之间进行（停旧主机上该用户的进程 → 卸卷 → 挂到新主机 → 启动），会话、记忆、登录状态不变。
- **缩容（负责人 2026-10-25）：按评估的选项 A，尽可能不缩容。** 评估见 `docs/research/2026-10-25-ebs-volume-move-assessment.md`（同可用区卸挂可行、不复制数据，一次迁移用户可见约 20–60 秒；m8g / c8g / r8g 每台最多 31 人、m7g 系 26 人；gp3 每人 30 GiB 约 $2.4/月，卸挂不收费）。
  - 不为省钱搬动用户：不做整理装箱式迁移，不做夜间卸卷（评估的选项 C 不采用），专用 Worker 不自动停机。
  - 只回收已经没有任何用户卷的主机（例如用户转为专用 Worker 或注销后空出来的），空置时长阈值取长，具体值进设计〔设计者决定〕。
  - 新用户优先放进已较满的主机（装箱放置，只影响新放置、不搬已有用户）；迁移只在用户切换共享 / 专用或主机故障时发生，且在两次尝试之间、用户空闲时进行，期间新任务进平台现有排队。
  - 规模化前置：gp3 存储配额（本账号当前 50 TiB）按用户规模提前申请提额；小规模实测（12 项，约 $5–8，上限 $20，含换主机后 kiro-cli / Claude Code / Codex 登录是否仍有效）在实施前进行，需负责人配合登录。
- **设计须写明的 AWS 限制**〔设计者补充〕：
  - EBS 只能挂在同一可用区的实例上：用户的卷、共享池与专用 Worker 默认同一可用区；跨可用区要先快照再恢复，只用于故障转移。
  - 单台实例可挂的卷有上限（Nitro 一般二十几块，扣除根卷与网卡）：它决定一台共享主机最多放几人，扩容信号要同时看 CPU、内存与剩余挂载位。
  - 各主机上执行用户的 UID 须全局统一，由控制面分配，否则卷上文件属主对不上。
- **待负责人确认**：见统一设计 §17 与路线图第 3 节。
- **与已有草稿的关系：** `feat/stateless-worker-pool`（`.worktrees/worker-pool`，未提交：会话租约、S3 检查点、Worker 池、Litestream）与 `docs/mvp-0.2.0-ha-scaling`（`.worktrees/mvp-020-ha`，e268b69、7bc7b73：平台高可用、按负载扩容、控制面 / 数据面分工）。本节的「每人一块 EBS、改挂即迁移」取代其中「工作区靠 git bundle 搬家」作为主路径（bundle 保留为跨可用区与备份手段）〔设计者决定〕；三者合并成一份设计与一份实施计划后再开工——2026-10-25 合并为统一设计（`docs/superpowers/specs/2026-10-25-mvp-0.2.0-deployment-and-worker-pool-design.md`），2026-10-26 写成路线图与 P1 实施计划（`docs/superpowers/plans/2026-10-26-mvp-0.2.0-roadmap.md`、`…-p1-foundation.md`），在 `feat/mvp-0.2.0` 上实施。

## 稳定性（2026-10-22 发现）

- ~~**会话列表每行过重**~~（2026-10-24 在 feat/mvp-0.1.1 处理，未部署）：核对过侧栏、空状态页、MCP `octopus_runs` 与 CLI `search` 用到的字段后，`view=list` 只留需求的前 200 字，规划去掉说明文字（instructions、reason、intent），不再返回、也不再计算 usage、knowledge、credentials、verified；tasks 按约定保留。按 EC2 的 50 行估算：223 KB → 119 KB，gzip 后 53 KB → 15 KB。

- **Gateway 事件循环卡住后被看门狗结束**：EC2 2026-10-22 13:25–13:30，owner 边界的 Gateway 事件循环多次卡住（最长 18 秒），KiroCrew 的 loop_watchdog 以 SIGALRM 结束进程，两个子 Agent 中途断开（「Orphaned: gateway_restart」），由自动纠错救回。
  - 已处理（21495ea）：Worker 检查运行中的尝试改读自己的那一行，不再每次读完整列表（310 KB / 150 ms → 7 KB / 16 ms）。
  - 仍待：
    - 持久化记录超过 50 条后，KiroCrew 每次列表都报截断（日志约每 1.5 秒一条），疑似 KiroCrew 原生页面在轮询；
    - 事件循环其余的阻塞来源在上游 KiroCrew，崩溃转储在 `<边界>/crew/logs/crash-dumps/`。
  - 2026-10-23 复查（bc651cc）：17:21 部署重启之后两个多小时内没有再卡顿，截断告警只在派发与结束时出现（此前约每 1.5 秒一条）；17:21 的 loopstall 文件是每次启动新开的空转储。
- ~~**浏览器测试截图写到主仓库的绝对路径**~~（2026-10-23 修复：运行器把 `$BROWSER_ARTIFACTS` 作为 `artifactRoot` 传入，没设就不截图）。


## 青岩改版终审延后项（2026-10-24）

- ~~**对话框关闭后焦点回到页面开头**~~（2026-10-24 修复，未部署）：根因比「⋯」更广——Radix 只把焦点还给 `DialogTrigger`，而 `FormDialog` 都由代码打开，所以任何对话框关闭后焦点都落到页面开头。现在 `FormDialog` 打开时记下焦点、关闭时还回去（那个按钮已随行删除时除外），两屏的 Token 对话框把最初的按钮传给第二屏；`RowActions` 的菜单项等菜单关闭、焦点回到「⋯」后才执行，对话框于是把焦点还给「⋯」。
- **侧栏会话行与会话页的「⋯」菜单打开的对话框**（2026-10-24 终审）：它们在菜单项里直接打开对话框，记下的焦点是菜单项，关闭后仍落到页面开头；改为与 `RowActions` 相同的做法即可。
- ~~**「管理 › 用户」改执行环境分两步**~~（2026-10-24 在控制台处理，未部署）：移出旧环境失败时对话框说明「已加入 X，但没能从 Y 移出」并重新读取列表；再点一次只重试移出，不再重复加入（此前返回 409）。中间状态仍可能出现（两个共享环境同时服务这个人），服务器一次完成的「改为」接口留待需要时再做。
- **不可撤回的删除没有要求输入名称**：PRD §3.2 要求不可撤回的操作输入名称确认，现在 Token 撤销、仓库凭证删除、Worker 退役都只是普通确认框（写明「不能撤回」）。要不要加这一步，由负责人决定（新增的确认步骤要负责人同意）。

## 执行环境登记的两处不一致（2026-10-24 设计 Worker 自助接入时发现）

- ~~`GET /v1/workers/<id>/capabilities`（刷新模型）只核对 `boundary`~~（2026-10-25 修复，PR #9：改为 POST，同时核对 `host_principal` 与 `isolation`，在事务里重读后只更新能力字段，不再覆盖同时发生的「更新连接」）。
- ~~非 spare 的 Worker 登记时只与**已启用**的 Worker 比对 `boundary`：已停用 Worker 的边界可以再登记给另一个人或 Team。~~（2026-10-26 修复，MVP 0.2.0 P1 Task 1：`workers.registration_conflicts` 与全部 Worker 比对，含已停用、已退役的）

## 预览域名与控制台同站（2026-10-25，0.2.0 设计审阅时发现）

**问题**：会话预览 `<run-id>.preview.tinyboat.blog` 与控制台 `kirocrew-on.tinyboat.blog` 同属可注册域名 `tinyboat.blog`，浏览器把两者当作同站（same-site）。

**现在挡得住的**：
- 登录 Cookie 是 `__Host-octopus_session`，不带 Domain、`SameSite=Strict`，预览页覆盖不了它；
- 用 Cookie 登录的写入必须带控制台自己的 Origin（`octopus/server.py` 的 `identity`），预览页发起的写入会被拒绝；
- 控制台不返回 CORS 头，预览页读不到控制台的响应。

**挡不住的**：
- `SameSite=Strict` 对同站请求不起作用：预览页发起的 GET 会带上登录 Cookie。因此，任何有副作用的 GET 都是跨站请求伪造的入口，需要逐一核对。
- 预览页可以写 `Domain=tinyboat.blog` 的 Cookie，写多了会让控制台的请求头过大而失败（OPERATIONS「会话独立预览域名」已记）。
- 预览页可以写 `Domain=preview.tinyboat.blog` 的 Cookie，覆盖其他会话的预览 Cookie。

~~**修法**：预览改用独立的可注册域名。~~ **负责人 2026-10-27：不另购域名。** 同站问题在源站一级处理（设计 §8.2a）：控制台一侧 PR #9 与 P1 的 GET 审计已做到；原生入口在 P3 按 N1–N4 做。

**2026-10-25 已做（PR #9，域名换之前的缓解）**：
- 排查了所有 GET：没有 GET 签发票据或写 Cookie，真正改业务数据的只有「刷新模型」，已改为 POST；
- 只凭 Cookie 的请求，浏览器标明来自同站或跨站的子资源请求（`Sec-Fetch-Site`、`Sec-Fetch-Mode`）一律拒绝，顶层打开页面不受影响；
- 报告页与平台的提示页加 `frame-ancestors 'none'`，不能被嵌入。

**仍待**：
- 事件流（SSE）按 Token 限制同时打开的条数；`export`、`chat-changes`、尝试 diff 加频率限制。有效 Token 的持有者仍可以大量发起这些请求，消耗资源。
- Cookie 灌满 `.tinyboat.blog` 让控制台请求头过大，以及不同会话的演示之间互相覆盖演示自己的 Cookie：不换域名时无法根治，按设计 §8.2a 接受。影响只是可用性，清除浏览器里该域名的站点数据即可恢复（OPERATIONS「会话独立预览域名」）。

