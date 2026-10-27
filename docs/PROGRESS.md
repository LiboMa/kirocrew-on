# 进度与完成记录

> 由 `tools/progress.py` 生成（2026-10-27 09:13 CST）：任务状态取自 `docs/progress.json`，提交记录取自 git。看板：`docs/progress-dashboard.html`；实时看板：`python3 tools/progress.py --serve --ec2`（http://127.0.0.1:8765/）。

- **本轮完成度**：80%　**MVP 完成度**：73%
- **分支**：HEAD `879dc48`；自 `origin/main` 起 74 个提交——已推送 0、已部署 EC2 73、仅本机 1；EC2 运行 `c80dae6`
- **回归**：2036 后端 + 92 + 342 控制台 + 7 个浏览器测试；EC2 运行 c80dae6（MVP 0.2.0 P1，PR #15）

## 模块

| 模块 | 完成度 | 已验收 | 已部署 · 待验收 | 本机完成 · 待部署 | 进行中 | 卡点 · 等外部 | 未开始 |
|---|---|---|---|---|---|---|---|
| MVP 里程碑（M1–M5） | 73% | 22 | 17 | 0 | 2 | 0 | 9 |
| MVP 0.2.0 部署模式、自助 Worker 与 Worker 池 | 56% | 0 | 12 | 0 | 1 | 2 | 3 |
| D46 Agent 角色与提示体系 | 87% | 8 | 3 | 0 | 0 | 1 | 0 |
| D49 每人一个执行边界 | 80% | 2 | 2 | 0 | 1 | 0 | 0 |
| D50 编码技能包 | 100% | 2 | 0 | 0 | 0 | 0 | 0 |
| D51 模型列表与自动发现 | 100% | 3 | 0 | 0 | 0 | 0 | 0 |
| D53 管理与使用分开 | 80% | 0 | 1 | 0 | 0 | 0 | 0 |
| 验收与发布 | 100% | 14 | 0 | 0 | 0 | 0 | 0 |
| 以后（已排期） | 40% | 0 | 1 | 0 | 1 | 0 | 1 |

## 下一步

1. 【需要负责人】合并 PR #8：MVP 0.2.0 输入（两种部署模式、Worker 自助申请、每人一块 EBS、认证随卷、缩容待评估），Worker 自助接入并入 0.2.0
2. 【需要负责人】0.2.0 的设计合并（worker-pool 与 mvp-020-ha 两份草案并成一份设计 + 计划）完成后，定开工顺序
3. 【需要负责人】下一个大项的先后：M5 仓库闭环（CONN-01…05）、M3-6 移除 /classic/、M4-3 原生入口单 Session 票据（K03）、D49 第 3 期
4. 【需要负责人】确认后删除本机备份分支 backup/main-before-rewrite（改写前的 main）

## 本轮修复的缺陷

- `8a7c3f6` 续接返工都被判『实际 ACP 后端未核实』：KiroCrew 按对话 key 记 routing，Worker 按本次运行查。已部署，run-9507ea17 的续接返工实测通过。
- `c9a3469` 续接时不带模型，KiroCrew 换成全局默认（Opus）：claude 的 default 变 Opus、codex 被要求用 Claude 模型。已部署，p5 重跑核实。
- `d9f57c3` R10 可被『等待执行环境时的分配』绕过；『改派停下的会话』会卡死并丢失已完成任务的改动——已去掉。
- `6c2bc7c` 发起向导切换执行空间时 ReferenceError（构建的 tsc -b 发现）。
- `f5954f1` 分配执行环境时覆盖了预算重置（全量回归发现）。
- `0eca0b9` kiro 的模型探测在 0.10.0 中永远填不出列表、每 2 小时白花一轮——改走目录 API 并设上限。
- `3f9352d` 主机缺 16 个系统库，审查与前端验证无法启动浏览器。
- `3ad6379` kiro-login 填错边界名时空等设备码（--boundary kirocrew-on）：现先核对主机上有这个边界，并列出现有边界。
- `e3123aa` 续接时「自动」仍被 KiroCrew 解析为全局默认（p5 验收：claude default → Opus）：改为沿用对话首次运行的模型。已部署，p5 重跑核实。
- `c2ca670` EC2 根卷被 73 个旧发布（77 GB）占满：SSM 命令失败、c2ca670 安装中止。负责人批准后清理 70 个旧发布、根卷原地扩到 500 GiB；安装脚本此后自动清理旧发布（OPERATIONS §10）。
- `ff7e42a` 自 10-20 会话落在本人的 owner 边界后，「在 KiroCrew 中打开」远程不可用（owner 的 crew_url 只是本机隧道）：新增 deploy.sh boundary native 并为 owner 开通；首次执行因 nginx 按文件名读 conf.d、日志格式未定义而被 nginx -t 拒绝（已回退），改名后通过。
- `17687ec` EC2 run-25c7aaa7（M0 脚手架）两次停在契约层：协调者让 task-3-2 修复其他任务的文件却不能扩大 paths（路径 A 续作解开），保留任务的检查扫到后来的 backend/.venv（修正 verify 续作解开）；之后 Evaluator 的 3 项高危发现经返工修复，会话 2026-10-21 14:07 完成。两类问题分别由 D54（待确认）与重新规划复核保留检查（已做）处理。

## 完成记录（按决策，最近的在前）

### D75（1 个提交：已部署 EC2 1）

- `724da31` 2026-10-26 〔已部署 EC2〕 feat(share): 公开分享——把会话的页面或运行中的演示发给互联网用户，四位授权码或无授权两种模式（D75）。预览域名上的不可猜链接（不放控制台域名，免得挂出仿冒登录页）；授权码防爆破（每访客 10 分钟 5 次、每分享 1 小时 30 次锁 1 小时，计数入库）；授权 Cookie 只对链接路径有效、随授权码失效；页面沙箱、noindex；演示以分享为持有人进入远程预览，访客不能启动；撤销、到期、会话关闭、分享人停用即失效；迁移 15、API、控制台「公开分享」、MCP octopus_public_share、OpenAPI 与文档

### D74（10 个提交：已部署 EC2 10）

- `222b437` 2026-10-26 〔已部署 EC2〕 fix(worker): 每次拉回各用一个 bundle 文件——接手（D74）与 octopus pull 可能同时拉回同一会话；BACKLOG 记 D74 延后项，R10「改派停下的会话」由接手解决
- `8650930` 2026-10-26 〔已部署 EC2〕 docs(progress): K03 过渡修复与 D74 用量拆分已部署 EC2（decdbad）
- `8d67242` 2026-10-26 〔已部署 EC2〕 feat(usage): 接手后的会话用量按人拆开（D74）——每个尝试算在它创建时的执行人名下（按时间线里 executor_id 的变更），视图 usage.by，会话页列出各人用量
- `2f49c9e` 2026-10-26 〔已部署 EC2〕 feat(mcp): octopus_takeover 与状态里的接手信息（D74）——octopus_status 带 permissions（direct / takeover 及原因）、executed_by 与 takeover；octopus_takeover 发起接手；Skill 写明先征得用户同意、等搬完再续作、失败原样转告
- `dbb91ee` 2026-10-26 〔已部署 EC2〕 docs(progress): D74 接手已部署 EC2（53b35a1）与端到端结果
- `e38f198` 2026-10-26 〔已部署 EC2〕 feat(takeover): 后台搬迁与控制台（D74 第 4、5 步）——接手请求起后台线程：停预览、先提交原环境里对话中的改动、取完整 bundle 与尝试文件（遮蔽凭证）、新 Worker /adopt，一个事务里换执行人、工作区、原生父 Session、交接路径与凭证；失败留在原处可重试，重启后每分钟接着做；会话页「接手到我的执行环境」确认框、进行中与失败提示、执行人与列表「已由 X 接手」；执行人可更换凭证、自动发布以执行人身份；补上 __main__ 缺失的 logging 导入；OPERATIONS「接手共享来的会话」、Skill、CONTINUE 与进度
- `1808ae3` 2026-10-25 〔已部署 EC2〕 feat(takeover): Worker 端接手——/pull/attempts 取出各尝试下游会读的文件；/adopt 用会话分支的完整 bundle 建同一个工作区（同一批提交、分支、基线），展开尝试文件、建原生父 Session；接回时旧工作区移到一旁不覆盖（D74 第 3 步）
- `396ed95` 2026-10-25 〔已部署 EC2〕 feat(takeover): 接手命令（D74 第 2 步）——POST /v1/runs/<id>/takeover 登记接手（202，由引擎在后台搬迁）：能续作的人可以把跑在别人个人执行环境上的会话接手到自己唯一的执行环境；查看者、单凭管理员身份、会话已在自己环境上、没有自己的环境、会话正在执行时都拒绝并说明原因；幂等；搬迁中续作、诊断、同步、发布、预览返回 409 takeover_in_progress；权限给出 takeover / takeover_reason
- `586ece7` 2026-10-25 〔已部署 EC2〕 feat(takeover): 执行人（D74 第 1 步）——runs.executor_id（迁移 14），会话以谁的身份运行与创建者分开；执行环境是否服务、并发额度、知识空间、个人凭证、补充说明 / 审批 / 发布、原生入口改按执行人；共享与删除知识仍归创建者
- `3db3a15` 2026-10-25 〔已部署 EC2〕 docs(design): 协作者怎样续作共享来的会话——三种方案（负责人选方案一「接手」）与接手的技术设计（D74：执行人、接手命令、引擎后台搬迁、/adopt、接回、分期）

### D64（1 个提交：已部署 EC2 1）

- `78f3d5a` 2026-10-26 〔已部署 EC2〕 fix(share): 公开分享不提供 MCP 工具——与 D64「Coding Agent 没有共享工具」一致，公开到互联网只由人在控制台操作；Skill 请用户去会话页点「公开分享」

### D58（3 个提交：已部署 EC2 3）

- `1543555` 2026-10-26 〔已部署 EC2〕 fix(cli): octopus report --json 输出 JSON（含报告正文）；octopus -h 列出使用者命令（D58 延后项）
- `07c0dad` 2026-10-26 〔已部署 EC2〕 docs(progress): D58 延后项已部署 EC2（f2735ca）
- `f2735ca` 2026-10-26 〔已部署 EC2〕 fix(local): D58 终审延后项——同步先鉴权再读 20 MB 请求体；拉回是读（只读 Token 与查看者可拉回）；没有新提交的同步 409 nothing_to_sync；octopus login 先把已有文件收窄为 0600 再写 Token，先保存 CLI Token 再吊销登录用的临时 Token

### D53 · 管理与使用分开（1 个提交：已部署 EC2 1）

- `c80dae6` 2026-10-26 〔已部署 EC2〕 fix(workers): 接入码命令不在代码文本里写 sudo（D53），控制台标注「以 root 运行」

### D49 · 每人一个执行边界（1 个提交：已部署 EC2 1）

- `092cbb9` 2026-10-26 〔已部署 EC2〕 fix(boundaries): 全局 UID 改为 70000 + 序号并以系统账号创建；分配可重复；找回被覆盖的 D49 主机测试（审查 2026-10-26）

### D59（1 个提交：已部署 EC2 1）

- `df72489` 2026-10-24 〔已部署 EC2〕 docs(design): Worker 自助接入——管理员签发一次性接入码（定好登记到哪里），Worker 主机执行 worker enroll 自行登记，平台照旧带 Token 核实 /health；Token 不经人手、不进 SSM 命令历史；本地全栈自动登记；待负责人确认三点。BACKLOG 记录登记的两处不一致、K10 已由 D59 完成

### 未标记 · 文档（27 个提交：已部署 EC2 26、仅本机 1）

- `879dc48` 2026-10-26 〔仅本机〕 docs: MVP 0.2.0 P1 已部署到 EC2（c80dae6）与端到端结果；进度看板更新
- `4258cd1` 2026-10-26 〔已部署 EC2〕 docs: BACKLOG 记录 MVP 0.2.0 P1 终审的次要延后项
- `babf39f` 2026-10-26 〔已部署 EC2〕 docs(research): KiroCrew 升到正式版 v0.10.0 的调研（P1 Task 12，S32）——会改变原生页面体验，拆为 P1b 待负责人确认
- `2953304` 2026-10-26 〔已部署 EC2〕 docs: MVP 0.2.0 进度（P1 11/12 完成）与 P0 实测结果
- `f1fbff7` 2026-10-26 〔已部署 EC2〕 docs(research): 有副作用的 GET 请求审计（MVP 0.2.0 P1 Task 7，CE12）
- `97acd34` 2026-10-26 〔已部署 EC2〕 docs(plan): 记录负责人 2026-10-26 的决定——批准 P0 实测资源、可用性 99.5%（方案 A）、温备密钥材料 KMS 托管、独立域名稍后提供
- `4e0cfa6` 2026-10-26 〔已部署 EC2〕 docs(plan): MVP 0.2.0 路线图与 P1 打底实施计划；版本文档 MVP-0.2.0 与 Presentation；进度看板加 0.2.0 各期；BACKLOG 注明草稿已合并成一份设计与计划
- `b69b19c` 2026-10-26 〔已部署 EC2〕 docs(design): 0.2.0 统一设计的出处草稿入库——无状态 Worker 池设计（P）与状态栈、Worker 池 CloudFormation 草稿（未部署，供 P1 状态栈与 Litestream、P3 数据面栈参照）
- `a826ad1` 2026-10-24 〔已部署 EC2〕 docs(plan): 0.2.0 规划补上「控制面 + 数据面」的分工、不变式与现在偏离的五处（负责人 2026-10-24 的归纳）
- `cfb3ebb` 2026-10-23 〔已部署 EC2〕 docs(plan): MVP 0.2.0 核心——高可用与自动扩容规划草案（待负责人评审）：现状单点、平台层三方案（推荐先做温备 + Litestream）、执行环境改为一组 Worker 并按平台的积压信号扩缩、M0–M5 分期与验收、不新增等待的做法、待决定问题
- `54d9a40` 2026-10-25 〔已部署 EC2〕 docs(design): 0.2.0 统一设计按 kirocrew-on-ce 复审补三点（CE11、CE12、Token 措辞）
- `7202a58` 2026-10-25 〔已部署 EC2〕 docs(design): 0.2.0 统一设计并入 kirocrew-on-ce 审阅意见（2026-10-25，CE1–CE10）
- `bbd3e5a` 2026-10-25 〔已部署 EC2〕 docs(design): 0.2.0 统一设计按实施者视角检查修订（设计者决定 S29–S35）
- `0d68764` 2026-10-25 〔已部署 EC2〕 docs(design): MVP 0.2.0 部署模式、Worker 自助申请与 Worker 池统一设计（合并 worker-pool、HA 规划、enrollment 与负责人决定）
- `1eb4a26` 2026-10-26 〔已部署 EC2〕 docs(progress): 公开分享入口并入预览产出，已部署 EC2（5a78786）
- `4aa58db` 2026-10-26 〔已部署 EC2〕 docs(progress): 公开分享已部署 EC2（78f3d5a）与公网实测
- `4ee44e2` 2026-10-26 〔已部署 EC2〕 docs(progress): 拉回各用一个文件已部署 EC2（6b686d1）
- `ef81f01` 2026-10-26 〔已部署 EC2〕 docs(design): 原生入口改为单会话票据（K03 / M4-3）——现状（含 30 天刷新与过渡修复）与三种方案：撤权即结束、请 KiroCrew 提供按会话限定的登录、平台票据加过滤代理；建议先一后二
- `894ce39` 2026-10-25 〔已部署 EC2〕 docs: BACKLOG 记录同站缓解已做（PR #9：GET 排查、Sec-Fetch-Site、刷新模型改 POST、frame-ancestors）与仍待的限流
- `4570a19` 2026-10-25 〔已部署 EC2〕 docs: BACKLOG 记录预览域名与控制台同站（tinyboat.blog）——现有的防护、挡不住的情形与修法（0.2.0 设计审阅时发现）
- `9fae8d5` 2026-10-25 〔已部署 EC2〕 docs: MVP 0.2.0——EBS 卷迁移评估（可行性与成本），缩容按选项 A、尽可能不缩容（负责人 2026-10-25）
- `79d1636` 2026-10-25 〔已部署 EC2〕 docs: 进度看板——发布两项已完成（PR #5–#7 已合并到 main）；「下一步」改为当前待负责人决定的事
- `9140e8c` 2026-10-25 〔已部署 EC2〕 docs: MVP 0.2.0 重要更新——认证信息不用 Secrets Manager、随用户的 EBS 卷走；缩容待评估卷移动的可行性与成本（负责人 2026-10-25）
- `22192cd` 2026-10-24 〔已部署 EC2〕 docs: Worker 自助接入放到 MVP 0.2.0（负责人 2026-10-24），并入 0.2.0 的 Worker 自助申请；设计状态、BACKLOG 与进度看板
- `7086085` 2026-10-24 〔已部署 EC2〕 docs: MVP 0.2.0 重要更新——Kiro CLI 由用户自己用设备码登录（负责人 2026-10-24 确认）
- `d3273f8` 2026-10-24 〔已部署 EC2〕 docs: 进度看板加 Worker 自助接入（设计待确认）
- `4d5c0eb` 2026-10-24 〔已部署 EC2〕 docs: MVP 0.2.0 重要更新——两种部署模式（单机版、集群版）与 Worker 自助申请、共享 / 专用 Worker、每人一块 EBS 改挂即迁移（负责人 2026-10-24）

### 未标记 · 修复（12 个提交：已部署 EC2 12）

- `9875cf5` 2026-10-26 〔已部署 EC2〕 fix(workers): 签发接入码时给出主机上能直接运行的命令（审查 2026-10-26）
- `b99fd16` 2026-10-26 〔已部署 EC2〕 fix(release): wheel 只打包 git 跟踪的文件，不带本部署的 stack.env（审查 2026-10-26）
- `c20d101` 2026-10-26 〔已部署 EC2〕 fix(workers): 接入码限流每个来源只记最近 10 次、每分钟清一次空闲来源（审查 2026-10-26）
- `672d5c2` 2026-10-26 〔已部署 EC2〕 fix(deploy): Litestream 改由独立的 litestream.sh 安装，有执行边界的主机上拒绝执行（审查 2026-10-26）
- `7ff843b` 2026-10-26 〔已部署 EC2〕 fix(deploy): Litestream 已装同版本时不再重装（0.5.17 的 version 不带 v）
- `eb1571a` 2026-10-26 〔已部署 EC2〕 fix(workers): 登记时边界唯一性看全部 Worker（含已停用、已退役的）——一块卷的身份不能再登记给另一个人；登记规则抽成 workers.registration_conflicts，供接入码共用（MVP 0.2.0 P1 Task 1，CE9）
- `b10f299` 2026-10-26 〔已部署 EC2〕 fix(share): 公开分享安全审查的修复——授权码页改为 same-origin（no-referrer 让浏览器带 Origin: null，表单必被拒）；重新生成授权码也结束演示 Cookie（持有人带授权码摘要）；一个授权码累计错 100 次作废；页面分享只提供网页文件且不含疑似凭证；限次在写事务前判断、打开次数合并写入、入口页建时确定、每访客每分钟 600 请求；会话关闭即删除分享；分享人失去控制权即失效；访客只看平台提示；Cookie 逐段解析；主机核对一致；读授权码记审计；预览日志不记令牌；MCP 先核对 action
- `5b24b98` 2026-10-26 〔已部署 EC2〕 fix(native): 刷新拦截改答「没有刷新凭据」，不再答「刷新链已吊销」——原生页面在访问 Cookie 到期前 1 小时刷新（1 小时的 Cookie 一打开就刷新），收到「已吊销」会跳回首页，每 7–8 秒整页重载一次；改答 no_refresh_cookie 后它按 1/4/16/60 分钟退避，到期时显示登录过期提示
- `decdbad` 2026-10-26 〔已部署 EC2〕 fix(security): 原生入口的登录在访问 Cookie 到期时结束（K03）——Gateway 换取登录时另发 30 天的刷新凭据，原生页面用 /api/auth/refresh 续成新登录，平台既不能缩短也不能结束；所有公网中转包含 kirocrew-native-guard.conf，把刷新答成「刷新链已吊销」（401），页面回到需要登录的提示；部署时给此前渲染的中转补上 include，nginx 校验不过就还原并警告
- `53b35a1` 2026-10-26 〔已部署 EC2〕 fix(takeover): 终审修复——搬迁开始与落地时再核对接手资格；接手期间引擎不恢复、不核对该会话，落地时已在原环境又开始执行就不接手；续作的对话改动提交不在搬迁中进行；尝试文件也遮蔽原 Worker 的 Token；改派到自己的边界即以自己为执行人；未被接手的 Team 会话仍可用 Team owner 绑定的个人凭证；凭证使用审计与自动发布策略按执行人；回收线程不再被搬迁阻塞
- `55efa79` 2026-10-26 〔已部署 EC2〕 fix(takeover): 交接路径改写经 journal（只记会话级事件）；时间线标题补上接手的三类事件；访问权限测试含 takeover
- `7adb992` 2026-10-25 〔已部署 EC2〕 fix(security): 同站的会话预览不能借控制台的登录 Cookie——只凭 Cookie 的请求拒绝同站 / 跨站的子资源请求（Sec-Fetch-Site / -Mode，顶层打开与旧浏览器照旧）；「刷新模型」改为 POST（仍为 read 权限），核对 boundary、host_principal、isolation，事务内重读只更新能力字段；报告页与提示页 frame-ancestors 'none'

### 未标记 · 功能（10 个提交：已部署 EC2 10）

- `4092056` 2026-10-26 〔已部署 EC2〕 feat(console): 状态配色——已完成为蓝、运行中为绿、有问题为红（失败、暂停）或黄（等待、待核实）（负责人 2026-10-26）
- `bbb106f` 2026-10-26 〔已部署 EC2〕 feat(metrics): 平台指标，每分钟一行 CloudWatch EMF（MVP 0.2.0 P1 Task 11，设计 §12.1）
- `f4ff0ae` 2026-10-26 〔已部署 EC2〕 feat(deploy): 状态栈与 Litestream（MVP 0.2.0 P1 Task 10，P §5.1–5.2，S31）
- `9d9478e` 2026-10-26 〔已部署 EC2〕 feat(release): wheel、清单与签名（MVP 0.2.0 P1 Task 9，S1）
- `06b2b0a` 2026-10-26 〔已部署 EC2〕 feat(deploy): 安装脚本与 AMI 选择支持 ARM64（MVP 0.2.0 P1 Task 8，P §5.6）
- `5c7bf67` 2026-10-26 〔已部署 EC2〕 feat(workers): 一次性接入码登记 Worker（MVP 0.2.0 P1 Task 6，N §3–§5）
- `f4f347c` 2026-10-26 〔已部署 EC2〕 feat(worker): 健康信息补容量字段——内存、CPU、负载、运行中的尝试数与 max_subagents，读不到的为 null 不影响健康检查（MVP 0.2.0 P1 Task 4）
- `4f442d8` 2026-10-26 〔已部署 EC2〕 feat(boundaries): 全局 UID 分配器——新边界的 boundary_id 与 UID = GID = 100000 + 序号由平台分配、永不复用（迁移 16、POST /v1/admin/boundaries、bootstrap-worker --allocate）；boundary.sh 只对新边界用它 groupadd -g / useradd -u 并把 boundary_id 写进 toml，旧边界不变（MVP 0.2.0 P1 Task 3，S9）
- `9726d25` 2026-10-26 〔已部署 EC2〕 feat(worker): boundary_id 进入 Worker 配置与健康信息——有 boundary_id 时边界身份为 sha256(boundary_id:uid)，换主机不变；只从渲染出的配置读、不从用户卷读；没有它的旧边界身份逐字节不变（MVP 0.2.0 P1 Task 2，S8、CE3）
- `5a78786` 2026-10-26 〔已部署 EC2〕 feat(console): 公开分享并入「预览产出」的下拉菜单——与预览产出是同一批内容，给没有账号的人看；不再单独占一个工具栏按钮，只对能分享的人显示

### 未标记 · 其他（5 个提交：已部署 EC2 5）

- `900d6b3` 2026-10-26 〔已部署 EC2〕 refactor(workers): Worker 登记的绑定规则与登记事务抽成 workers.check_binding / workers.register——控制台登记与接入码走同一段代码（MVP 0.2.0 P1 Task 6 前置）
- `f48b416` 2026-10-26 〔已部署 EC2〕 refactor(engine): 单主改为租约接口——FileLease 即原来的 engine.lock 文件锁，Engine 可注入别的租约（温备用，P5）；行为不变（MVP 0.2.0 P1 Task 5）
- `b0469c9` 2026-10-26 〔已部署 EC2〕 Merge remote-tracking branch 'origin/docs/mvp-020-inputs' into feat/mvp-0.2.0
- `8cfdc37` 2026-10-26 〔已部署 EC2〕 Merge branch 'fix/native-refresh-guard' into fix/d58-minors
- `6b686d1` 2026-10-26 〔已部署 EC2〕 Merge branch 'feat/session-takeover' into fix/native-refresh-guard

### 未标记 · 测试（2 个提交：已部署 EC2 2）

- `8dd7ad4` 2026-10-26 〔已部署 EC2〕 test(p0): MVP 0.2.0 P0 实测脚本与结果（T1–T4、T7–T10）及 Litestream 云上验证
- `40177df` 2026-10-26 〔已部署 EC2〕 test(deploy): deploy.sh 的安全测试带上 arch-lib.sh，并覆盖新栈按机型架构选 AMI
