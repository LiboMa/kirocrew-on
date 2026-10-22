# 进度与完成记录

> 由 `tools/progress.py` 生成（2026-10-22 14:39 CST）：任务状态取自 `docs/progress.json`，提交记录取自 git。看板：`docs/progress-dashboard.html`；实时看板：`python3 tools/progress.py --serve --ec2`（http://127.0.0.1:8765/）。

- **本轮完成度**：87%　**MVP 完成度**：59%
- **分支**：HEAD `c887aac`；自 `origin/main` 起 423 个提交——已推送 335、已部署 EC2 88、仅本机 0；EC2 运行 `c887aac`
- **回归**：1412 后端 + 92 + 205 控制台（connect codex / kiro-ide 本机）；EC2 运行 95e5aa9

## 模块

| 模块 | 完成度 | 已验收 | 已部署 · 待验收 | 本机完成 · 待部署 | 进行中 | 卡点 · 等外部 | 未开始 |
|---|---|---|---|---|---|---|---|
| MVP 里程碑（M1–M5） | 59% | 12 | 6 | 0 | 1 | 0 | 10 |
| D46 Agent 角色与提示体系 | 87% | 8 | 3 | 0 | 0 | 1 | 0 |
| D49 每人一个执行边界 | 80% | 2 | 2 | 0 | 1 | 0 | 0 |
| D50 编码技能包 | 100% | 2 | 0 | 0 | 0 | 0 | 0 |
| D51 模型列表与自动发现 | 100% | 3 | 0 | 0 | 0 | 0 | 0 |
| D53 管理与使用分开 | 80% | 0 | 1 | 0 | 0 | 0 | 0 |
| 验收与发布 | 86% | 12 | 0 | 0 | 0 | 0 | 2 |
| 以后（已排期） | 40% | 0 | 1 | 0 | 1 | 0 | 1 |

## 下一步

1. 【需要负责人】D52 EC2 验收：指定一个可写入的测试仓库与写凭证（会在其上开 octopus/knowledge-… 分支与 Draft PR）
2. 【需要负责人】可选：建一个测试成员账号（我不经 MCP 建用户），以便在 EC2 实测 D49 第 2 期的成员回退与 R10
3. 【需要负责人】规划质量：默认 admission 改为 confirm（计划先给人看），和 / 或在规划后加一个不同工具的计划审查
4. 【需要负责人】下一个大项的先后：M2 剩余、M3 剩余、M4 剩余、M5 仓库闭环、D49 第 3 期、返工自动附技能（skills-next）

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

### D63（1 个提交：已部署 EC2 1）

- `c887aac` 2026-10-22 〔已部署 EC2〕 fix(composition): 边界的 KiroCrew 配置关掉 session.eager_spawn（D63）——KiroCrew 0.10 默认为每个 chat slot 预先起会话；平台的 slot 从不认领，白占进程，内存吃紧时被回收还连带 SIGKILL 正在运行的子 Agent（EC2 run-16848466…，parent-end teardown）；新配置与已有配置都写 false，明确开启的保留

### D62（3 个提交：已部署 EC2 3）

- `4eaf293` 2026-10-22 〔已部署 EC2〕 docs: D62 已在 EC2 验收（ab29b40）——7 天 Token 7 天到期、永不过期的列表为 null 且能认证、非法取值 400
- `ab29b40` 2026-10-22 〔已部署 EC2〕 fix(tokens): 终审修正（D62）——octopus connect 完成时按平台实际给的有效期提示（永不过期 / 有效期至某日），不再写死 30 天；旧版控制台的 Token 列表显示「永不过期」而不是 1970 年
- `d75e34f` 2026-10-22 〔已部署 EC2〕 feat(tokens): API Token 有效期可选 7 / 30 / 365 天或永不过期（D62，负责人 2026-10-22）——POST /v1/tokens 的 expires_in；永不过期以远期日期保存、API 返回 expires null、列表显示「永不过期」；带 admin 的最长 30 天；控制台「有效期」（默认 30 天，修正写死的「7 天后到期」）；octopus connect / login --expires（同时发 ttl，旧平台照旧 30 天）；OpenAPI 与运维说明

### D61（8 个提交：已部署 EC2 8）

- `ff33ad5` 2026-10-22 〔已部署 EC2〕 docs: D61 已在 EC2 验收（d4ae66f）——Team 上限 1 时第二个会话排队、第一个结束后自动开始；BACKLOG 记 KiroCrew 回收未认领父会话时杀掉子 Agent
- `d4ae66f` 2026-10-22 〔已部署 EC2〕 fix(quotas): 终审修正（D61）——只数任务当前的 Attempt（续作留下的仍显示运行中的记录不再让会话等自己）；排队期间又等执行环境恢复的，等待时间只计一次，run.quota_go 照常记下
- `aa2a40c` 2026-10-22 〔已部署 EC2〕 docs: D61 并发上限——运维说明（限什么、谁设、到了上限、不等待）；MVP-RELEASE、BACKLOG（第 3 期用量上限）与进度
- `9193bc1` 2026-10-22 〔已部署 EC2〕 feat(console): 设置 →「并发上限」（D61 Task 3）——看自己的与所在 Team 的上限与占用；平台管理员设每人默认与单独上限，Team owner 设本 Team 的；成员只读
- `adbc2a2` 2026-10-22 〔已部署 EC2〕 feat(mcp): 只读 octopus_quotas 与 Skill 说明（D61 Task 3 的一部分；控制台「并发上限」待做）
- `3d2bd37` 2026-10-22 〔已部署 EC2〕 feat(engine): 按并发上限排队（D61 Task 2）——会话的下一个任务开始前，在创建 Attempt 的同一写事务里数个人与 Team 的占用；满了就等待（run.quota_wait 只记一次，「需要处理」写明哪个范围、上限与占用，无需操作），有空位按等待先后开始（run.quota_go，等待不计时长预算）；久未被看到的等待不挡别人；自动重试照常不等待
- `c76ca8d` 2026-10-22 〔已部署 EC2〕 feat(quotas): 并发上限的存储与接口（D61 Task 1）——迁移 10 quotas（每人默认 / 某人 / 某 Team，1–50）；占用按仍在推进的会话中运行的 Attempt 与协调者诊断计（执行环境停用留下的不计）；GET / POST /v1/quotas（管理员设默认与个人、Team owner 设本 Team），写审计 quota.set；OpenAPI
- `02198d3` 2026-10-22 〔已部署 EC2〕 docs(spec,plan): D61 M4-4 第 2 期——每人 / 每 Team 的并发上限（设计稿与 4 个任务的实施计划；默认不设、到了上限按等待先后排队）

### D60（7 个提交：已部署 EC2 7）

- `e223182` 2026-10-22 〔已部署 EC2〕 docs: D60 已部署 EC2（290718f）——部署后会话照常完成；看板 M4-4 第 1 期已部署、第 2 期（D61）进行中
- `290718f` 2026-10-22 〔已部署 EC2〕 fix(queue): 终审修正（D60）——已开始过又排队的任务（有 state.json、resuming）也认作排队；排队文字被别的错误盖住后恢复；排队中每 20 秒才看一次；会话的「需要处理」显示排队（无需操作），不再是红色错误
- `117e2fb` 2026-10-22 〔已部署 EC2〕 docs: D60 排队可见——运维说明（排队、不计、没有开始、处理）；MVP-RELEASE 与进度看板 M4-4 第 1 期
- `ef0e4b2` 2026-10-22 〔已部署 EC2〕 feat(engine): 排队可见（D60 Task 2）——KiroCrew 推迟的原生任务在会话显示「排队：等待执行环境资源（原因类别）」，事件 attempt.queued / attempt.dequeued；看门狗不计排队，从排队结束起计时；排队时间不计时间预算；never started 自动重新派发（不占自动重试与预算，连续第 3 次交给协调者，排队前已开始过的照旧交给协调者）
- `ea26a8a` 2026-10-22 〔已部署 EC2〕 feat(worker): inspect 认出被 KiroCrew 推迟的原生任务（D60）——列表与状态文件都没有时改问 GET /api/spawn/{id}，排队中且任务文字带本 Attempt 标记的回 queued（原因、说明、已等待、是否 resuming），不再报「原生任务归属当前不可核实」
- `8c8bbe3` 2026-10-22 〔已部署 EC2〕 docs(plan): D60 排队可见的实施计划（3 个任务）
- `c943720` 2026-10-22 〔已部署 EC2〕 docs(spec): D60 M4-4 第 1 期——排队可见、看门狗不计排队、never started 自动重派（MVP 1.1.0 调研报告任务一；以 EC2 上 KiroCrew 0.10.0 的 spawn 回执与 GET /api/spawn/{id} 为准）

### D59（10 个提交：已部署 EC2 10）

- `6243bb5` 2026-10-22 〔已部署 EC2〕 docs: D59 EC2 核对（30ca5b7）——Worker 只剩 token_sealed、数据库文件无明文、3 个 Worker 能力刷新与一个会话走通
- `30ca5b7` 2026-10-22 〔已部署 EC2〕 fix(workers): 终审修正（D59）——远程预览的校验头从密封的 Token 计算（升级后远程 Worker 不再 500）；主密钥文件丢失或不可读时一律 503 worker_secret；主密钥不可读时平台照常启动（使用密封 Token 的调用 503，跳过本次密封）；每个进程只解开一次（不再每次调用都运行 openssl）；OPERATIONS 写明恢复步骤与 D59 之前的备份含明文
- `69eaec6` 2026-10-22 〔已部署 EC2〕 fix(transport): 密封 Token 的密钥改由 configure_key 设定，不再覆盖 Worker TLS 证书的 configure（D59，部署前发现）
- `324e3e7` 2026-10-22 〔已部署 EC2〕 docs: D59 Worker Token 密封——OPERATIONS 主密钥说明、MVP-RELEASE K10、进度
- `c252a3b` 2026-10-22 〔已部署 EC2〕 test(bootstrap): 重新注册后 Worker Token 以密封形式保存（D59）
- `880733d` 2026-10-22 〔已部署 EC2〕 feat(workers): 平台启动时把已有的明文 Worker Token 密封（D59 K10）——迁移之后执行一次、可重复执行，没有配置密钥时按 D35 在数据目录生成
- `2e22515` 2026-10-22 〔已部署 EC2〕 feat(workers): 注册与更新连接时把 Worker Token 密封后存入（D59 K10）——数据库只有 token_sealed；平台启动时把 D35 的密钥交给 transport；列表与环境视图不含任何 Token 字段；能力刷新保留密封值
- `3278287` 2026-10-22 〔已部署 EC2〕 feat(workers): transport 接受密封的 Worker Token（token_sealed）并只在发请求时解开（D59 K10）——解不开时 503 worker_secret 并请平台管理员重新连接，不回退到明文；明文 token 照旧可用；diff 与 sync 传给 Worker 的连接保留 token_sealed
- `a878ce0` 2026-10-22 〔已部署 EC2〕 docs(plan): D59 Worker Token 移出数据库的实施计划（4 个任务）
- `b3228ca` 2026-10-22 〔已部署 EC2〕 docs(spec): D59 M4-2 Worker Token 移出数据库（K10）——用 D35 的 vault 与密钥密封为 token_sealed，只在 transport 发请求时解封，写入即密封，启动时把已有的明文密封（可重复执行）

### D58（11 个提交：已部署 EC2 11）

- `361b7fb` 2026-10-22 〔已部署 EC2〕 docs: D58 已部署 EC2 c4469aa 并验收（Claude Code MCP 与 CLI 走通 submit → pull → sync → 复审；.env 被拒）；§7 验收 1 记录（Codex、Kiro IDE 待负责人核对）；进度
- `c4469aa` 2026-10-22 〔已部署 EC2〕 fix(sync,local): 终审修正（D58）——Worker 的 sync 把上传的 bundle 当不可信输入：头部只接受一个分支提交、对象先解包到隔离目录，核查通过才迁入（被拒绝的同步不留任何对象、不写任何 ref），用 fast-forward merge 移动（不覆盖未跟踪文件），按会话加锁，同一同步重放时给出同样的结果；平台在调用 Worker 前校验 request_id，与续作一致要求上游任务已验收、执行环境可用，管理员代为同步写审计，同步后停止预览；检查未能启动时照样记录 Attempt、由引擎重新启动检查；同步的 Attempt 写交接摘要（含说明）；CLI 在仓库内 --allow-unpushed 不再崩溃；当前分支不在 origin 上时 submit 停下；两边都有新提交时 pull 把会话的最新提交取为 refs/octopus/<run>/remote 并提示 rebase，只有本地领先时不报错；SSH 地址的端口与非 SSH 地址正确处理；无法识别的仓库不算本仓库；保存的 Token 只发给它的平台；git 调用不等待终端、有超时
- `7dc4f09` 2026-10-22 〔已部署 EC2〕 fix(server): 拉回的下载里不用 name 作循环变量（它是模块的名称校验函数，tests/test_server_shadowing 拦下）（D58）
- `3d18672` 2026-10-22 〔已部署 EC2〕 docs: D58 本地 / 远端联动与使用者命令行——OPERATIONS、CONNECT、MVP-RELEASE §14.3、BACKLOG、进度
- `f90374e` 2026-10-22 〔已部署 EC2〕 feat(console): 本地同步的 Attempt、修订与事件，客户端 CLI（D58）
- `5426f37` 2026-10-22 〔已部署 EC2〕 feat(mcp): octopus_link、octopus_pull、octopus_sync 与提交前的未推送检测（D58）——在用户本机的仓库执行，与 CLI 同一套本地逻辑；octopus_start 提交本仓库时若有 origin 没有的提交先停下（push_base 经用户同意后推送，或 allow_unpushed），成功后关联仓库；octopus_status 缺 run_id 时读关联；Skill 写明联动流程与拒绝原因
- `4e0642d` 2026-10-22 〔已部署 EC2〕 feat(cli): 使用者的 octopus 命令（login、submit、plan、confirm、status、watch、report、pull、sync、continue、approvals、search）（D58 M3-2）——login 用用户名与密码登录后改存一枚 30 天的「octopus CLI」Token（0600），并吊销登录用的那枚；submit 在 Git 仓库内取 origin 与当前分支，本地有未推送提交时停下并提示 --push-base / --allow-unpushed，成功后关联仓库；其余命令默认用本仓库关联的会话、与 MCP 同样的调用，输出中文摘要或 --json；运维命令不变
- `3960d03` 2026-10-22 〔已部署 EC2〕 feat(local): 本地仓库与会话的关联、pull、sync 与未推送提交检测（D58）——.octopus/session.json 与 .octopus/sessions/ 写入 .git/info/exclude；pull 把会话分支的 bundle 取为本地 octopus/<run>（缺基线时自动取完整历史，从不覆盖本地分支自己的提交），并写报告、计划与各任务交接；sync 只在工作区已提交、当前分支包含会话 head 时把其后的提交打成 bundle 上传，同一同步的 request_id 固定；源码取 origin（SSH 地址按 HTTPS）与当前分支；未推送提交计数与 --push-base；Client 增加 upload 与 /bundle/ 下载
- `79143dc` 2026-10-22 〔已部署 EC2〕 feat(sync): POST /v1/runs/<id>/sync——本地提交成为新候选，重新检查并让下游任务重新审查；pull 的 bundle 下载（D58）——二进制上传（≤ 20 MB）经 Worker 快进；最后一个写代码的任务记「本地同步」Attempt（候选、synced、门禁），有检查命令时照常运行与判定，其后的任务重新执行；修订「本地同步」、事件 run.synced；同一 request_id 只同步一次；权限与续作相同；POST /v1/runs/<id>/bundle 给出 10 分钟的拉回链接；CLI 的 Token 记为 client cli
- `4b7c9c7` 2026-10-22 〔已部署 EC2〕 feat(worker): 会话分支的 pull bundle 与 sync（D58）——POST /pull/bundle 按发布同样核查疑似凭证与 .env 后给出分支（自基线或完整历史）；POST /sync 导入本地提交的 bundle，只在会话分支仍在 old、工作区没有未纳入候选的改动、head 是快进且新内容没有疑似凭证时把分支与工作区移到 head，返回与候选相同的字段；上限 20 MB
- `a522f79` 2026-10-22 〔已部署 EC2〕 docs(spec,plan): D58 M3-2 用户 CLI 与 M3-4 本地 / 远端联动——git bundle 交换提交（不经托管方）、会话关联 .octopus/session.json、pull 拉回分支与上下文、sync 只接受快进并让最后一个写代码的任务重新检查、下游重新审查、未推送提交检测与 --push-base；CLI 与 MCP 共用 octopus/local.py；6 个任务的实施计划

### D57（11 个提交：已部署 EC2 11）

- `316afb8` 2026-10-22 〔已部署 EC2〕 merge: D57 第一批（门禁 warn、五个门禁降为提示、审查改动由平台撤回）合入 feat/m11-gates
- `66f1976` 2026-10-22 〔已部署 EC2〕 fix(review): 撤回记录写明只撤回已跟踪的文件；rewind 的说明列出撤回不到的情形（D57）
- `04af334` 2026-10-22 〔已部署 EC2〕 fix(review): 没有通过的审查（拒绝、出错、看门狗停止）改动的文件同样由平台撤回，返工的候选不再收进审查者的改动（D57）
- `c559ca7` 2026-10-22 〔已部署 EC2〕 feat(review): 审查改动的文件由平台撤回、会话分支回到候选，结论照常采用（D57）
- `30a6696` 2026-10-22 〔已部署 EC2〕 fix(gates): 结果文件不合法时识别常见的拒绝写法；上游任务的提示随前序交接交给后续审查（D57）
- `bb71beb` 2026-10-22 〔已部署 EC2〕 fix(gates): 结果文件不合法但写明拒绝时仍按拒绝判定；平台生成的交接存入 evidence 并交给后续任务；提示词与新判定一致（D57）
- `75a6adf` 2026-10-22 〔已部署 EC2〕 feat(gates): diff_present、artifacts_locked、tests_fail_on_baseline、handoff_present、outcome_valid 降为提示（D57）
- `7611a6c` 2026-10-22 〔已部署 EC2〕 fix(gates): 报告列出「提示 N 项」，Knowledge 跳过的验收也写入 warnings（D57）
- `9111f99` 2026-10-22 〔已部署 EC2〕 feat(gates): 门禁新增「提示」状态 warn，不使任务 blocked、不触发纠错（D57）
- `ee09921` 2026-10-22 〔已部署 EC2〕 docs(plan): D57 门禁减负与安全底线实施计划（20 个任务）
- `fdf2f90` 2026-10-21 〔已部署 EC2〕 docs(spec): D57 门禁减负与安全底线（负责人已确认；编号原暂定 D56）——补充实现时的 10 条设计者决定

### D56（8 个提交：已部署 EC2 8）

- `789cada` 2026-10-22 〔已部署 EC2〕 docs: D56 已部署 EC2 8cfef39，§7 验收 3 勾选（经 API 实测全部过滤、全文与翻页，候选 diff 可读）；BACKLOG 记终审延后项与 .kiro/settings 进入候选；进度
- `8cfef39` 2026-10-22 〔已部署 EC2〕 fix(console,diff): 终审修正（D56）——「进行中」只发送会话状态（dispatching 是任务状态，曾使整组 400），控制台的会话状态清单与服务端一致并有测试；列表读取失败时显示原因，不再看起来像空列表；创建时间按使用者本地的日换算；失去访问权的会话即使读过也会移出；正在输入的仓库 / 分支不因其他筛选变化而清空；全文恢复匹配工作流名称；diff 不显示下载同样不含的文件（.env 等，只给数量），不用仓库自己的 textconv 与前缀设置；工作区已不在的会话返回 409 而不是 500
- `b5cf2bf` 2026-10-22 〔已部署 EC2〕 docs: D56 会话列表的过滤、全文、分页与代码改动——OPERATIONS、MVP-RELEASE §14.3、BACKLOG、进度
- `501b519` 2026-10-22 〔已部署 EC2〕 feat(diff): Attempt 详情可看候选的代码改动（D56）——Worker 的 /candidate 新增只读的 mode: diff（相对上一个候选或会话基线，每个文件的增删行数与统一 diff，200 KB / 4000 行截断）；GET /v1/runs/<id>/attempts/<attempt>/diff 按会话的访问检查读取，没有候选 404 no_candidate，被凭证检查挡在分支外的候选 409 candidate_secret（从不显示），旧 Worker 409 worker_outdated 并请平台管理员升级；控制台每次 Attempt 多一个「代码改动」折叠区，展开才请求，点开文件看它的 diff
- `5abedab` 2026-10-22 〔已部署 EC2〕 feat(console): 会话列表服务端过滤、全文搜索与分页，筛选写进地址栏（D56）——搜索停顿 300 ms 后查询，状态分组按状态与生命周期发给服务端，「筛选」面板（仓库、分支、阶段、工具、客户端、提交者、创建时间）与可移除的标签，「加载更多」按游标翻页，标签页与概况的数字取服务端总数；选中的会话不在已加载的页里时按 ID 读取，只有 404 / 403 才提示不可访问；服务端列表不再返回已删除的会话，全文也匹配来源客户端的名称（原控制台的 D55 搜索）
- `8636d63` 2026-10-21 〔已部署 EC2〕 feat(mcp): octopus_runs 按空间、仓库、分支、阶段、工具、客户端、提交者、时间过滤与全文搜索（D56）；Skill 同步
- `bce2261` 2026-10-21 〔已部署 EC2〕 feat(runs): 会话列表按空间、状态（可多个）、生命周期、仓库、分支、阶段、工具、客户端、提交者、时间与全文过滤，服务端分页（D56 M3-5）——仓库按 主机/所有者/仓库 比较（https、ssh、git@ 同一结果）；全文覆盖需求、规划、各任务名称与说明、最近一次结论与错误，多词全部命中；page=1 返回 {items, next, total}，游标按（创建时间, ID），翻页时新建的会话不重复不遗漏；不带 page 照旧返回数组
- `0dcc989` 2026-10-21 〔已部署 EC2〕 docs(spec,plan): D56 M3-5 控制台——服务端按空间、状态、仓库、分支、阶段、工具、客户端、提交者、时间与全文过滤，分页（不带 page 时照旧返回数组），MCP octopus_runs 同步；Attempt 详情的代码改动（Worker mode diff）；5 个任务的实施计划

### D55（11 个提交：已部署 EC2 11）

- `b840dbb` 2026-10-21 〔已部署 EC2〕 docs: M2 §6 验收 1 勾选（D55，EC2 80bc0c4：Request 登记、默认先确认、按准入规则直接执行）；BACKLOG 记 EC2 现场的 Harness 问题（内存不足不可见、deliver 不入库产物与 diff_present、评审写文件与 review_bound）；进度
- `bea5a71` 2026-10-21 〔已部署 EC2〕 docs(progress): D55 已部署 EC2 80bc0c4——迁移 9 生效；§6 验收 1 第 1 步通过（不带 admission 停在计划待确认、回执带 request_id、octopus_requests 可见）；准入规则在 EC2 命中两种仓库写法；owner 边界内存不足致子 Agent 排队，观察中
- `80bc0c4` 2026-10-21 〔已部署 EC2〕 fix(policies): 终审修正（D55）——最具体的准入规则按字面字符多少取胜（较早的 github.com/o/* 不再被较新的 * 盖过）；MCP 精简状态带 admission、admission_basis 与 request，Coding Agent 能说明会话为何等待；报告抬头写明准入依据；管理员能在列表中看到并删除他为非所在 Team 建的规则；管理员读他人的 Request 写审计（与会话一致）；BACKLOG 记延后的次要项
- `e36ac62` 2026-10-21 〔已部署 EC2〕 docs: D55 Request、准入规则与映射偏好——OPERATIONS 新节（默认已改为 confirm）、MVP-RELEASE §14.2 第 1、4、5 行、进度、BACKLOG 延后项
- `02e581d` 2026-10-21 〔已部署 EC2〕 feat(console): 会话页写明来自哪个请求与准入依据；设置「准入与映射偏好」（D55）——头部一行「来自请求 request-xxxxxx · Codex；按准入规则「github.com/o/*」自动开始 / 按平台默认等待确认」（提交时自己指定的不重复）；新对话框分「准入规则」「映射偏好」两组，显示作用域与内容，可删除自己能维护的，新增时选作用域（个人、本人是 owner 的 Team）、仓库模式与准入方式，或按类别 / 角色的工具与模型，服务端的拒绝原样显示在表单下；会话搜索也匹配来源客户端（输入「Codex」）
- `4e37c28` 2026-10-21 〔已部署 EC2〕 feat(mcp): octopus_requests 与只读的 octopus_policies；octopus_start 回执带 request_id；Skill 写明默认先确认、准入依据与按偏好的映射来源（D55）
- `4703429` 2026-10-21 〔已部署 EC2〕 feat(policies): 个人 / Team 映射偏好进入规划提示与映射来源（D55 M2-4）——偏好按类别或角色对应工具 / 模型，同一类别或角色上个人覆盖 Team；提交时写入会话快照，首次规划（600）与重新规划（200）的提示多一节「用户的映射偏好」，每条一行：优先采纳、不采纳写明理由，需求中指定的优先，不可用的工具不选；规划者的任务与偏好一致时映射来源写「按个人偏好 / 按 Team 偏好」；重新规划的最坏情况仍 ≤ 7800（保留任务检查 800→600、检查命令 300→250）；设计 §6 记 tool: auto 本期不做
- `37197fb` 2026-10-21 〔已部署 EC2〕 feat(policies): 按仓库范围的准入规则，平台默认改为先确认（D55 M2-5）——个人 / Team 规则 {repos: 主机/所有者/仓库（可 *）, mode: auto|confirm}；判定顺序为提交时指定 → 个人规则 → Team 规则 → 平台默认，同层取最具体（通配最少、再按最新）；https、ssh、git@host:path 三种写法同一结果，新建项目不命中；会话快照与视图带 admission_basis，Request 的策略快照记命中的规则；Team 规则只有 owner 或管理员能增删、归档的 Team 不能新增，增删写审计；GET/POST /v1/policies、DELETE /v1/policies/<id>；default_admission 默认 confirm
- `8cff53b` 2026-10-21 〔已部署 EC2〕 feat(requests): 每次提交登记 Request（D55 M2-1）——迁移 9 建 requests 与 policies 表、runs.request_id；来源客户端取自 Token 标签（控制台 / claude / kiro / codex / kiro-ide / api）；记需求原文、源码、显式选择（只记个数与格式）与准入 / 自动纠错 / 发布策略快照；会话视图带 request，旧会话为 null；GET /v1/requests（status、q、client、limit）与 /v1/requests/<id>，看不到其会话的人得 404
- `c39be8b` 2026-10-21 〔已部署 EC2〕 docs(plan): D55 M2 剩余的实施计划（6 个任务：Request、准入规则与默认先确认、映射偏好进入规划、MCP 与 Skill、控制台、文档与 EC2 验收）；进度：connect codex 在 EC2 以真实 Codex 核对
- `9879cc4` 2026-10-21 〔已部署 EC2〕 feat(connect): octopus connect codex（合并 ~/.codex/config.toml，只替换 [mcp_servers.kirocrew-on]）与 connect kiro-ide（合并 <项目>/.kiro/settings/mcp.json，不需要 kiro-cli）——备份、0600、合并后解析核对；合并失败时只说原因并吊销刚签发的 Token（M3-1）；docs(spec): D55 M2 剩余（Request、按仓库准入、映射偏好）及负责人的三项决定

### D54（5 个提交：已部署 EC2 5）

- `245d2ea` 2026-10-21 〔已部署 EC2〕 feat(recovery): 协调者的返工可扩大可改动路径（D54）——artifacts_locked 的失败记录带被拒文件，协调者在 context.locked_files 中看到；重试 / 返工失败的任务时可加 paths_add（只取 locked_files 中的文件，至多 10 个，合并后至多 20 项），平台核对后扩大该任务的 paths，记在这次自动纠错的修订与时间线 task.paths_added；不符时整份决定被拒并写明原因；OPERATIONS、Skill 与设计同步
- `ef47d6d` 2026-10-21 〔已部署 EC2〕 docs: 进度——run-25c7aaa7 完成；D54 主体待负责人确认
- `17687ec` 2026-10-21 〔已部署 EC2〕 feat(replan): 重新规划复核保留任务的检查命令（负责人 2026-10-21，D54 §9 a）——提示列出保留的已验收任务的 verify；计划可带 verify_fixes，平台只接受指向保留任务且不清空 implement / test 检查的修正，写入该任务（不重跑）并记在这次重新规划的修订里，时间线 task.verify_revised；重新规划提示的各节限长下调以容下（最坏约 7720 / 8000）
- `82fadb4` 2026-10-21 〔已部署 EC2〕 docs(spec): D54 记下同类问题——保留任务的过时检查命令（run-25c7aaa7 第 15 轮后 checks_pass 因 backend/.venv 失败），不交给协调者，列出可选做法待负责人决定
- `3325522` 2026-10-21 〔已部署 EC2〕 docs(spec): D54 协调者的返工可扩大可改动路径——只限门禁锁住的、此前已验收任务的文件，记修订与时间线（EC2 run-25c7aaa7 卡在 artifacts_locked 13 轮）

### D49 第 3 期 · 每人一个执行边界（2 个提交：已部署 EC2 2）

- `c5c4615` 2026-10-21 〔已部署 EC2〕 docs: 进度——owner 边界的公开原生入口已开通（D49 第 3 期）；run-25c7aaa7 的 artifacts_locked 待负责人决定
- `d02325d` 2026-10-21 〔已部署 EC2〕 feat(deploy): 平台主机上边界的公开原生入口（D49 第 3 期）——deploy.sh boundary native <名称> 建只含 CloudFront 分发的栈、nginx 中转到该边界的 Gateway、写入 crew_url 并排空重启；控制台在入口未开通时按 D53 说明（会话照常执行，请平台管理员开通）；自动纠错换工具时 test 任务不算作者（负责人 2026-10-21）

### D46 P3 · Agent 角色与提示体系（8 个提交：已推送 8）

- `6fed40b` 2026-10-21 〔已推送〕 docs: 进度——次要问题第 1–5 批与 D46 P3 返工按钮已部署 EC2（f5c41b7），根卷扩到 500 GiB
- `90ca3f2` 2026-10-20 〔已推送〕 feat(console): 验收横幅「不通过，返工…」——续作指向 Evaluator 之前改代码的任务，未通过的验收项预填进任务说明；会话提示与 Skill 同步（D46 P3）
- `b6f72df` 2026-10-18 〔已推送〕 docs: MVP-RELEASE 记录 D46 P3 Evaluator 门禁与 EC2 验收（7540427、649d30a）
- `7540427` 2026-10-18 〔已推送〕 fix(evaluation): D46 P3 终审修复——只有创建者、Team owner、管理员可确认验收；Evaluator 的通用说明改为 pass / fail / pending 且须附证据；没有验收项的 Evaluator 与平台追加的 Evaluator 在运行前按当前验收项判定；按文字对上的项保留自己的证据，其余按顺序补；Evaluator 报告的任何 fail 即不通过；确认后 MCP 显示确认者而非待确认；等待人判断时可启动预览（控制台与服务端状态集合由测试保持一致）；OpenAPI 登记 /accept；BACKLOG 记延后项
- `a19e3e8` 2026-10-18 〔已推送〕 feat(console): 「验收待确认」提示与「确认通过」、MCP octopus_accept 与精简状态的待判定项、报告「验收（以 Evaluator 为准）」；Skill 与 OPERATIONS（D46 P3）
- `8c0bfbe` 2026-10-18 〔已推送〕 feat(evaluation): 改代码的会话缺少 Evaluator 时平台在完成前追加（验收项取此前任务、工具按环境事实与审查独立性，没有作者以外的工具时记「审查不独立」），映射来源与时间线可见；规划提示要求安排 Evaluator；Evaluator 卡片要求 pending 与对挑战的逐条回应（D46 P3）
- `1614598` 2026-10-18 〔已推送〕 feat(evaluation): Evaluator 门禁（D46 P3）——改代码的新会话（契约 4、Worker 支持角色卡片）以 Evaluator 对每条验收项的 pass / fail / pending 与证据为准：缺项或缺证据交给协调者，fail 返工，pending 停在「验收待确认」，经确认（记确认者，不计预算）后继续
- `c1fb892` 2026-10-18 〔已推送〕 docs(spec,plan): D46 P3 Evaluator 门禁——改代码的会话以 Evaluator 的逐项判定为准（负责人 2026-10-18：都要经过 Evaluator；pending 停在「验收待确认」）；缺少时平台追加；适用于契约 4 且 Worker 支持角色卡片

### D37 · 候选提交、门禁与报告（5 个提交：已推送 4、已部署 EC2 1）

- `889fa00` 2026-10-21 〔已部署 EC2〕 feat(runs): 会话列表在服务端过滤（M3-5 第一步）——GET /v1/runs 支持 status / q / limit，最新的在前，不带参数时不变；MCP octopus_runs 改为精简列表 {items, more}（默认 20、至多 50，只含状态、进度、需要处理的事与错误首行），EC2 上完整列表约 55 万字符超出 MCP 结果上限（M3-3）；OpenAPI、Skill、OPERATIONS 同步；进度：M2-3 已由 D37 / D46 P3 覆盖
- `fccf081` 2026-10-15 〔已推送〕 feat(gates): artifacts_locked（已验收任务的交付物只能由 paths 覆盖它们的任务修改）与 tests_fail_on_baseline（测试任务的测试须在会话基线上失败）（D37 §11.1、§11.2）
- `d9b0a0d` 2026-10-15 〔已推送〕 feat(worker): 基线检查模式（标记、切换、必然恢复）；遗留的基线在下一次操作前恢复，运行中的基线不被其他请求打断（D37 §11.1）
- `b9cf48d` 2026-10-15 〔已推送〕 feat(sources,checks): 候选记录改动的文件名；在项目目录原地切到会话基线并叠加测试文件、必然恢复（D37 §11.1）
- `132dd3a` 2026-10-14 〔已推送〕 docs(spec): D37 候选提交、命名门禁与 Feature 报告（M2）——平台运行检查命令、审查绑定候选、只发布已验证候选；分三期

### D53 · 管理与使用分开（3 个提交：已推送 3）

- `c36ebad` 2026-10-20 〔已推送〕 docs: 进度——D53 管理与使用分开本机完成（10405c9），待部署 EC2 与推送
- `10405c9` 2026-10-20 〔已推送〕 feat(usage): 管理与使用分开（D53）——使用者的提示只写原因与「请平台管理员……」，不给运维命令；会话页的本机预览命令只对管理员显示；OPERATIONS 列出每条提示对应的管理员处理
- `2557963` 2026-10-20 〔已推送〕 docs: 原则 8 管理与使用分开设计（D53，负责人 2026-10-20）——管理操作与使用流程在控制台、MCP / Skill、提示与文档中分开；使用者的提示写明原因与「请管理员……」，不交给运维命令；两类人配合的步骤写明各自做什么

### D52 · 团队层知识（9 个提交：已推送 9）

- `2fd800c` 2026-10-20 〔已推送〕 docs: 进度——D52 团队层知识终审修复并部署 EC2（3ece2e6，迁移 8 生效，route 冒烟 5/5）；EC2 验收待负责人指定测试仓库与写凭证
- `3ece2e6` 2026-10-20 〔已推送〕 fix(knowledge): 终审修复（D52）——没有打开的知识 PR 时从默认分支开新分支（不沿用已合并 / 被拒的提议分支），去重对照默认分支；条目都已在打开的 PR 中时给出其编号；任务说明只在源码来自仓库、且知识文件与克隆基线逐字节一致时指向它，措辞为参考；GitHub 不返回 1 MB 以上内容时不提出；PR 说明里的规则以代码格式列出；时间线补「提出仓库知识」
- `386df5d` 2026-10-20 〔已推送〕 feat(knowledge): 会话页、报告与 MCP 写明本会话的仓库知识 PR 或未提出的原因；OPERATIONS「团队层知识（D52）」
- `6b5c329` 2026-10-20 〔已推送〕 feat(knowledge): 任务说明指向仓库中经人审核合并的 .octopus/knowledge.md（D52）
- `69d5ba7` 2026-10-20 〔已推送〕 feat(knowledge): 会话发布时把 Knowledge 条目提到仓库 .octopus/knowledge.md 的 Draft PR——固定分支 octopus/knowledge、与已有条目去重、只写这一个文件；提议失败不影响代码 PR，记录、审计与时间线（D52）
- `932ad53` 2026-10-20 〔已推送〕 feat(knowledge): 共享执行环境也追加 Knowledge 任务，只用于仓库层——条目记为 kept，不写、不注入 lessons；迁移 8 让 knowledge.result 接受 kept（D52）
- `7390837` 2026-10-20 〔已推送〕 docs(plan): D52 团队层知识——共享环境仅仓库层、发布时的知识 PR、派发时指向知识文件、可见与终审
- `e6803a9` 2026-10-20 〔已推送〕 docs: 进度——D52 团队层知识 spec 待审阅
- `1d5a409` 2026-10-20 〔已推送〕 docs(spec): D52 团队层知识——会话发布时把 Knowledge 条目提到仓库 .octopus/knowledge.md 的 Draft PR（固定分支 octopus/knowledge，人审核合并）；任务派发时指向经评审的知识文件（负责人 2026-10-19：P4 验收后排入）

### D51 · 模型列表与自动发现（4 个提交：已推送 4）

- `af47e9b` 2026-10-20 〔已推送〕 docs: 进度更新——D51 在 EC2 完整验收（claude 探测、kiro 目录各一次）；续接沿用首次模型实测通过；owner 已登录 kiro，P4 验收开始
- `0eca0b9` 2026-10-20 〔已推送〕 fix(models): D51 专项审查修复——kiro 的模型改由 KiroCrew 目录 /api/models 取得（0.10.0 中会话不为 kiro 记录模型，原探测每 2 小时白花一轮）；每个工具至多 3 次后放弃并在 health.model_discovery 报告；非 YOLO 且非自动批准的边界不开探测会话；部署排空时跳过；每轮至多一个探测会话；控制台 default 不标未公布、codex 始终写明不公布列表
- `71d9467` 2026-10-20 〔已推送〕 feat(models): 模型自动发现（D51）——Worker 在后台对尚无模型的可用工具各做一轮固定探测，KiroCrew 记下公布的模型，平台 10 分钟内带上；codex 报告配置的默认模型，控制台写成「自动（…）」（负责人 2026-10-20：自动刷新、无须人为干预）
- `d5ff390` 2026-10-20 〔已推送〕 docs(spec): D51 模型自动发现——Worker 后台对冷且可用的工具做一轮固定探测，KiroCrew 记下公布的模型；codex 报告配置的默认模型（负责人 2026-10-20：自动刷新、无须人为干预）

### D50 · 编码技能包（7 个提交：已推送 7）

- `78639f0` 2026-10-20 〔已推送〕 docs: 进度更新——p5、steer、d50 验收通过（P5a、P5b、P6a、D50 验收）；续接沿用首次模型的修复待部署
- `317f8b9` 2026-10-20 〔已推送〕 docs: MVP-RELEASE 记 30a377f（P5b、D50、续接修复）与 d9f57c3（D49 第 2 期含 R10、无头浏览器依赖）已部署 EC2
- `73cc198` 2026-10-19 〔已推送〕 fix(skills): 终审修复——规划者给出格式不对的 skills 不再拒掉整份计划；修订历史记录「方法」的变化；只发 Worker 技能包里有的技能（D50）
- `35de84c` 2026-10-19 〔已推送〕 feat(skills): 任务卡片、报告与 MCP 显示所用的方法；OPERATIONS「编码技能包（D50）」与 Skill 说明
- `407b012` 2026-10-19 〔已推送〕 feat(skills): 任务契约 skills——规划者按任务推荐，Worker 随 Attempt 交付并记录（D50）
- `b9127eb` 2026-10-19 〔已推送〕 feat(skills): 平台维护的编码技能包——测试先行、系统化调试、完成前验证、按评审返工、前端设计（改编自 superpowers 与 Anthropic frontend-design，附许可证；tools/build_skill_pack.py 可重建）（D50）
- `5ef5804` 2026-10-19 〔已推送〕 docs(spec,plan): D50 编码技能包——方法类技能（测试先行、系统化调试、完成前验证、按评审返工、前端设计）随任务交给三个后端，规划者按任务推荐；流程类由平台承担（负责人 2026-10-19 问）

### D49 第 2 期 · 每人一个执行边界（12 个提交：已推送 12）

- `0baf87c` 2026-10-20 〔已推送〕 docs: 进度更新——P4 Knowledge 验收（6/6 + forget）、D49 第 2 期 Team 会话落在本人边界（8/8）；本轮待推送
- `e882e10` 2026-10-20 〔已推送〕 fix(placement): 本人的执行边界停用时提示写明「已停用，请重新启用」而非「还没有边界」（以免再建第二个而使会话等待选择）；分配执行环境后记下新的执行位置（D49 第 2 期终审延后项）
- `d9f57c3` 2026-10-20 〔已推送〕 fix(placement): R10 专项审查修复——去掉「改派停下的会话」（状态卡住、已完成任务的改动留在原边界）；把会话放到某人的边界上只接受此人（等待执行环境时其他成员可借分配绕过 R10）（D49 第 2 期）
- `b50359e` 2026-10-20 〔已推送〕 feat(placement): 只有边界主人在自己的边界上下达指令（R10）——续作、诊断、确认计划、补充说明与批准只接受创建者；Team owner / 管理员可把停下的会话改派到 Team 的环境（D49 第 2 期，终审 Important 2，按推荐的 A）
- `7e03c72` 2026-10-20 〔已推送〕 docs: BACKLOG 记录 D49 第 2 期终审延后项与待裁定的 R3
- `9a8d145` 2026-10-20 〔已推送〕 fix(placement): 终审修复——Team 没有环境而成员有自己的边界时，发起向导不再拦住提交（按平台的 placement 判断）；Skill 的分配步骤按 run_id 列出可分配环境（D49 第 2 期）
- `6c2bc7c` 2026-10-20 〔已推送〕 fix(console): 发起向导切换执行空间时不再引用已删除的 spaceWorkers（运行时 ReferenceError，构建的 tsc -b 发现），改为交回平台选择（D49 第 2 期）
- `89135d1` 2026-10-19 〔已推送〕 feat(console): 发起向导默认交给平台选择执行位置并显示；会话页的位置提示；分配对话框按创建者列出可选环境（D49 第 2 期）
- `d5c563d` 2026-10-19 〔已推送〕 feat(placement): GET /v1/placement——默认位置与可选环境；MCP octopus_environments 标出默认、可按会话列出可分配的环境；Skill 说明（D49 第 2 期）
- `e1b7f08` 2026-10-19 〔已推送〕 feat(placement): Team 会话在提交者自己的边界执行，没有时回退到 Team 的环境并提示；会话记执行位置（D49 第 2 期）
- `8cd2d01` 2026-10-19 〔已推送〕 docs(plan): D49 第 2 期会话的执行位置——位置规则、/v1/placement 与 MCP、控制台、文档与 EC2 四个任务
- `1c8f2aa` 2026-10-19 〔已推送〕 docs(spec): D49 第 2 期——会话的执行位置：Team 会话在提交者自己的边界执行，无边界时回退到 Team 的环境并提示（负责人 2026-10-19 选 A）；服务端统一回答默认位置，客户端不再各自选择

### D49 第 1 期 · 每人一个执行边界（3 个提交：已推送 3）

- `2ee8d15` 2026-10-20 〔已推送〕 docs: 进度更新——e0d8791 已部署；route 验收通过、D49 第 1 期验收；模型自动发现在 owner 上实测写入 claude 的 14 个模型
- `607aba0` 2026-10-19 〔已推送〕 docs: BACKLOG 记录 D49 第 1 期终审延后项
- `9716878` 2026-10-19 〔已推送〕 docs: OPERATIONS「每人一个边界（D49 第 1 期）」；WORKERS 的边界层与同主机边界差距更新

### D46 P5a · Agent 角色与提示体系（9 个提交：已推送 9）

- `e3123aa` 2026-10-20 〔已推送〕 fix(continue): 「自动」的续接返工沿用对话首次运行实际用的模型——带上 auto 时 KiroCrew 续接仍解析为全局默认（p5 验收：claude 的 default 续接成 Opus）；首次模型不是可直接派发的 ID 时（如 codex 的 …[low]、kiro 的空值）仍发 auto（D46 P5a，EC2 run-e333a1eb… 2026-10-20）
- `c9a3469` 2026-10-20 〔已推送〕 fix(continue): 续接的返工总是带上任务的模型（含 auto）——不带时 KiroCrew 改用其全局默认模型续接：claude 的 default 变成 Opus、codex 被要求用 Claude 模型、kiro 的 auto 变成 claude-opus-5.5（D46 P5a，EC2 run-9507ea17… 核对 2026-10-20）
- `8a7c3f6` 2026-10-19 〔已推送〕 fix(continue): 续接的返工按所属对话核实实际 ACP 后端——此前每次续接都以「实际 ACP 后端未核实」被 identity_match 拦下（D46 P5a，EC2 2026-10-19）
- `89efe0c` 2026-10-19 〔已推送〕 docs: BACKLOG 记录 D46 P5a 终审延后项与 P5b
- `1e16ac5` 2026-10-19 〔已推送〕 fix(continue): 终审修复——approval_mode auto 而 YOLO 关闭时、显式 Effort 时不续接（/continue 不继承它们）；对话不属于本会话时全新派发而不卡住；对账恢复续接标记，对话链不断；返工说明按任务类别（复验不修改代码）（D46 P5a）
- `564bcd7` 2026-10-19 〔已推送〕 docs: OPERATIONS「保留上下文的返工（D46 P5a）」
- `66d48ba` 2026-10-19 〔已推送〕 feat(continue): 平台安排的返工续接上一轮的原生对话（同工具、模型与 Effort，未经用户续作），任务卡片、MCP 与报告显示（D46 P5a）
- `b68158b` 2026-10-19 〔已推送〕 feat(continue): Worker 在原生对话上续接返工，被拒时全新派发并记录；契约加 /api/spawn/{id}/continue（D46 P5a）
- `f8a0ec5` 2026-10-19 〔已推送〕 docs(spec,plan): D46 P5a 保留上下文的返工——平台安排的返工在上一轮的原生对话上续接（KiroCrew /api/spawn/{id}/continue），不能续接时全新派发；steer 与 release 为 P5b

### D46 · Agent 角色与提示体系（4 个提交：已推送 4）

- `75b59e8` 2026-10-20 〔已推送〕 docs: 进度看板 docs/progress-dashboard.html——本轮（D46–D51、验收与发布）与 MVP 完成度、模块进度、依赖关系图与下一步（负责人 2026-10-20）
- `dc4b428` 2026-10-16 〔已推送〕 docs(spec): D46 以 KiroCrew 为基准修订——能力基准表（记忆、lessons、skills、工作账本、spawn continue、用量、GitHub/GitLab）；Knowledge 改为 KiroCrew 成员记忆与 lessons；新增 P0（记忆默认值无效、availableAgents 位置等）与 P5、P6
- `1cd8e31` 2026-10-16 〔已推送〕 docs(spec): D46 负责人决定——新增 role 字段、以 Evaluator 为准、知识存于 .octopus/knowledge.md、先做 P1
- `d40e279` 2026-10-16 〔已推送〕 docs(spec): D46 草案——KiroCrew 通用 Agent Prompt 与角色体系的现状盘点、问题与分期计划（待负责人确认）

### D49 · 每人一个执行边界（5 个提交：已推送 5）

- `d3493ba` 2026-10-19 〔已推送〕 fix(boundaries): 终审修复——其余边界在平台 API 与 personal 之后逐个升级、失败不阻塞其余并最后报告，排空中断不留维护标记；资源上限 OOMPolicy=continue、Worker 4G；开通前先核对用户；同主机预览核对监听者属于本边界；Worker 主机不会接管同名的个人边界（D49）
- `3cb8480` 2026-10-19 〔已推送〕 feat(workers): 边界可登记为某个用户的专属 Worker（bootstrap-worker --owner）；个人会话优先选择唯一的专属 Worker（D49）
- `8ffe8ca` 2026-10-19 〔已推送〕 feat(deploy): deploy.sh boundary add 在平台主机开通专属于一个人的边界（独立端口、资源上限、登记为其专属 Worker）；boundaries、按边界的 kiro-login / space / tunnel（D49）
- `8149c54` 2026-10-19 〔已推送〕 feat(deploy): 每个边界的安装步骤抽为 boundary-lib.sh；升级对主机上全部边界做原生契约检查后才切换，再逐个排空重启（D49）
- `11e78a2` 2026-10-19 〔已推送〕 docs(spec,plan): D49 每人一个执行边界第 1 期——平台主机上开通专属于一个人的 KiroCrew 边界（独立端口、资源上限、登记为其专属 Worker），升级覆盖全部边界，个人会话优先选择自己的边界（负责人 2026-10-19）

### D46 P4 · Agent 角色与提示体系（6 个提交：已推送 6）

- `30a377f` 2026-10-19 〔已推送〕 docs: MVP-RELEASE 刷新到 2026-10-19——里程碑表与 M2 状态按 §14 的 EC2 验收记录更新，新增 §14.33–14.38（D46 P4 / P5a / P5b / P6a、D49、D50，均待 EC2 验收）；BACKLOG 记 D50 终审延后项
- `c9a765d` 2026-10-19 〔已推送〕 fix(knowledge): 终审修复——平台追加的 Knowledge 任务从不阻止会话完成（结论无效、派发被拒、登录过期、看门狗停止都记为沉淀未完成）；只读核对，改了的已跟踪文件还原到候选；写入超时覆盖 Worker 逐条写入；续作重跑时重新整理（D46 P4）
- `155e360` 2026-10-19 〔已推送〕 feat(console): 会话页「本次沉淀的知识」与删除、共享环境提示、MCP 与文档（D46 P4）
- `0b3b86b` 2026-10-19 〔已推送〕 feat(knowledge): 平台侧——专属环境中来自仓库、改了代码的会话完成前追加 Knowledge 任务，核对条目后写成限定仓库的 KiroCrew lessons 并记台账；派发时按环境注入；共享环境不沉淀不注入（D46 P4）
- `e0e6212` 2026-10-19 〔已推送〕 feat(knowledge): Worker 侧——仓库标记、按需注入 lessons、写入与删除 KiroCrew lessons；协调者不能自行写 lessons；契约加 /api/lessons（D46 P4）
- `2460869` 2026-10-19 〔已推送〕 docs(spec,plan): D46 P4 Knowledge——专属执行环境中来自仓库、改了代码的会话完成时追加 Knowledge 任务，平台核对后写成按仓库标记限定的 KiroCrew lessons，后续同仓库任务注入；共享环境不沉淀不注入（负责人 2026-10-18）

### D46 P5b · Agent 角色与提示体系（6 个提交：已推送 6）

- `40720ea` 2026-10-19 〔已推送〕 docs: BACKLOG 记录 D46 P5b 完成与终审延后项
- `e8d1526` 2026-10-19 〔已推送〕 fix(steer): 终审修复——去掉 follow_up（KiroCrew 以平台不跟踪的续接运行送达）；只支持 Kiro 后端的任务，codex / claude 如实拒绝；上游 steer_unsupported / shutting_down 转为 409 / 503（D46 P5b）
- `d8a34ab` 2026-10-19 〔已推送〕 docs: OPERATIONS 运行中补充说明与释放对话（D46 P5b）
- `6b8bccc` 2026-10-19 〔已推送〕 feat(steer): 运行中任务的补充说明——控制台、MCP 与 API，写审计与时间线（D46 P5b）
- `9da80f8` 2026-10-19 〔已推送〕 feat(steer): Worker 转发运行中任务的补充说明（KiroCrew steer），删除会话时释放其可续接的对话（D46 P5b）
- `d14c540` 2026-10-19 〔已推送〕 docs(spec,plan): D46 P5b 运行中补充说明（KiroCrew steer）与删除会话时释放可续接的对话（release）

### D46 P6a · Agent 角色与提示体系（7 个提交：已推送 7）

- `8b7c0ed` 2026-10-19 〔已推送〕 docs: BACKLOG 记录 D46 P6a 终审延后项
- `d99e16f` 2026-10-19 〔已推送〕 fix(usage): 终审修复——运行刚结束而用量行未到时稍等再查，不把「无 token」永久缓存；超大或异常数值不再让检查失败；续接测试核对对话的 slot 与各轮自己的那一行（D46 P6a）
- `6a6c65f` 2026-10-19 〔已推送〕 docs: OPERATIONS「用量（D46 P6a）」；BACKLOG 记录工具时间线、按人汇总与费用预算等后续
- `4b70245` 2026-10-19 〔已推送〕 feat(usage): 会话汇总每个 Attempt 的用量，任务卡片、会话页、报告与 MCP 显示（D46 P6a）
- `de65fbc` 2026-10-19 〔已推送〕 feat(usage): Worker 在原生任务结束时记下 Credit、token、费用与耗时（D46 P6a）
- `635319e` 2026-10-19 〔已推送〕 feat(usage): Worker 在原生任务结束时记下 Credit、token、费用与耗时（D46 P6a）
- `87e44de` 2026-10-19 〔已推送〕 docs(spec,plan): D46 P6a 用量——每个 Attempt 的 Credit、token、费用与耗时（KiroCrew /api/spawn/{id} 与 /api/usage/turns），会话汇总并显示；工具时间线待 KiroCrew 提供

### D48 · 发布到新仓库（11 个提交：已推送 11）

- `417bbed` 2026-10-18 〔已推送〕 docs: MVP-RELEASE 记录 D48 发布到新仓库与 EC2 验收（a044fb8）
- `a044fb8` 2026-10-17 〔已推送〕 docs: BACKLOG 记录 D48 终审延后的次要问题
- `0d59297` 2026-10-17 〔已推送〕 fix(publish): D48 终审修复——Worker 的发布包通过全部检查后才建仓库（被拒时不留空仓库）；会话自己的基线已在 main 时视为已完成（推送成功但未记下时不再永久卡住）；新建源码自动发布失败的提示指向「发布到新仓库」且不被建议遮住；「私有」总是默认勾选、建议公开只提示；向导缺少新仓库信息或名称不合法时提示而非悄悄丢弃，切换源码不带走「完成后自动发布」
- `ebcf4bf` 2026-10-17 〔已推送〕 feat(console): 新建源码的会话页「发布到新仓库」（owner、仓库名、私有、写凭证；带入规划者的建议），发布后显示仓库并可再次发布；向导「完成后发布到新仓库」（D48）
- `a852079` 2026-10-17 〔已推送〕 feat(mcp): D48 的 API / MCP / Skill——octopus_publish、octopus_start.publish、octopus_publish_policy 接受 repository 与 credential，精简状态含 publish_target 与 publish_suggestion；OpenAPI 增加 Publish 请求体与路由；OPERATIONS 与 Skill 写明新建仓库的发布方式
- `47c3182` 2026-10-17 〔已推送〕 feat(planning): 需求要求新建仓库时规划者只记建议 publish_suggestion（不合法的仓库名丢弃并说明），不开启自动发布、不创建；完成后「需要处理」为「发布到新仓库待确认」；新建源码的推送失败提示改为在会话页新建仓库并发布（D48 R7）
- `aa00714` 2026-10-17 〔已推送〕 feat(publish): 新建源码的会话发布到新仓库（D48）——发布时给出 repository 与写凭证，平台建仓库（或用已有的空仓库）、基线到 main、工作走 Draft PR；只建一次并记 publish_target，再次发布与自动发布沿用；非空仓库与范围不覆盖时拒绝；建仓库写审计与时间线
- `4842a99` 2026-10-17 〔已推送〕 feat(publish): 托管方的仓库操作（D48）——查找、建空仓库（GitHub 用户 / 组织、GitLab 命名空间）、空仓库的 main 从会话基线开始，非空仓库拒绝；名称校验，缺建仓库权限时说明需要哪一项
- `f0e439b` 2026-10-17 〔已推送〕 docs(plan): D48 发布时新建仓库的实施计划（6 个任务）
- `9fd9228` 2026-10-17 〔已推送〕 docs(spec): D48 负责人审阅通过（基线到 main、工作走 Draft PR；规划者识别到建仓库只提示）
- `2bde009` 2026-10-17 〔已推送〕 docs(spec): D48 发布时新建仓库——新建源码的会话发布到新仓库（待负责人审阅：基线到 main、工作走 Draft PR；规划者识别到建仓库只提示不自动创建）

### D46 P2 · Agent 角色与提示体系（6 个提交：已推送 6）

- `66de5b3` 2026-10-18 〔已推送〕 docs: MVP-RELEASE 记录 D46 P2 角色卡片与 EC2 验收（15a2ca2）
- `15a2ca2` 2026-10-18 〔已推送〕 fix(roles): D46 P2 终审修复——Worker 公布 role_cards 后平台才发送 role（未升级的 Worker 照常运行、无卡片）；规划给出的角色无效或与类别不相容时丢弃并说明，不再让整个计划被拒；续作把审查者改到作者工具上时拒绝（规划时已记说明的不阻挡续作）；OpenAPI 的 Step 接受 role；控制台修订对比写「角色」；BACKLOG 记延后项
- `3f85446` 2026-10-18 〔已推送〕 feat(console): 任务卡片、MCP 精简状态与报告显示角色（D46 P2）；Skill 与 OPERATIONS 写明 role 与审查独立性
- `f941d8c` 2026-10-18 〔已推送〕 feat(planning): 规划提示介绍 6 个角色（id、类别、用途）与审查独立性，示例带 role；规划或重新规划的审查类任务与作者同工具时照常应用并记「审查不独立」（重新规划把保留的已验收任务算作作者）（D46 P2）
- `b3b5e91` 2026-10-18 〔已推送〕 feat(roles): 角色卡片（D46 P2）——任务可带 role（6 个角色，KiroCrew SKILL.md 格式），任务说明开头内联对应卡片、契约写明角色；角色与类别须相容、省略类别时推断；提交的审查类任务不得使用被审查改动的作者工具
- `6acff91` 2026-10-18 〔已推送〕 docs(spec,plan): D46 P2 角色卡片——6 个角色以 KiroCrew SKILL.md 格式存于 octopus/roles，按任务内联进任务说明（负责人 2026-10-18）；role 字段、类别相容、审查独立性、规划提示与可见

### D47 · 自动发布 Draft PR（1 个提交：已推送 1）

- `ed78aa6` 2026-10-17 〔已推送〕 feat(publish): 会话完成后自动发布 Draft PR / MR（用户启动或会话页开启、需求中要求提 PR 时规划者标注），默认由人确认发布；自动发布做不到时会话仍完成并写明原因、同一版本只试一次；规划中的推送任务记说明，推送失败的任务提示「Agent 不能推送或创建 PR」而非权限问题（D47，EC2 run-361cd491…）

### D46 P0 · Agent 角色与提示体系（2 个提交：已推送 2）

- `faa4b22` 2026-10-17 〔已推送〕 docs: MVP-RELEASE 记录 D46 P0、D47 自动发布与协调者可选目标的 EC2 验收（ed78aa6）
- `3ec713b` 2026-10-16 〔已推送〕 fix(kirocrew): 以 KiroCrew 0.10.0 为准修正边界配置与隔离——新建父会话以 incognito 运行、不读写边界全局记忆（此前写入的 memory.enabled 无效）；协调者派发白名单移到 toolsSettings.subagent（含 poc-diagnose）并迁移旧位置；协调者排除 monitor_* 工具；新边界固定并发上限；不再发送已忽略的 solo_*（D46 P0）

### D46 P1 · Agent 角色与提示体系（7 个提交：已推送 7）

- `7ef1797` 2026-10-16 〔已推送〕 docs: D46 P1 的 EC2 验收
- `7e48c7f` 2026-10-16 〔已推送〕 fix(prompts): P1 终审修复——旧版工作流运行时保留原有提示、校验接受模板的契约字段（CI 回归恢复）；分析任务不被要求输出 JSON、模板中的 plan 步骤不当作规划；纠错说明按任务性质，返工后重跑的任务知道上轮原因；设计提示带工具可用性；交付步骤写明交付文件；重新规划在最大输入下仍在 8000 字符内；规划与诊断的结尾与结论契约各自适用；Owner 观察员的会话说明（D46 P1）
- `c7441e6` 2026-10-16 〔已推送〕 fix(templates): 内置模板补 kind 与验收项、审查用 changes_requested、不再用已停用的 OpenCode、不再要求 Agent 保存 diff、步骤提示不重复目标；运维文档「Agent 提示」（D46 P1）
- `7e1c928` 2026-10-16 〔已推送〕 fix(recovery): 纠错要求只给被安排修复的任务并摘要化（决定与失败摘要），其后的审查 / 测试只收到复验说明；协调者上下文只在 context.json，不再内嵌（D46 P1）
- `639a50f` 2026-10-16 〔已推送〕 fix(worker): 派发外壳按任务性质给出结尾——规划、设计与诊断不再被要求开发，实现与交付须产生改动，审查只读、测试不改实现；交接清单写进说明；检查命令写明平台是否运行；交付任务带 preview.json 格式；续作改了需求时标明规划补全已过时（D46 P1）
- `9de5ca8` 2026-10-16 〔已推送〕 fix(prompts): 共同原则与规划提示一致——统一称呼与任务数、去掉默认工具组合与过时说明、不自行推送或建 PR、诊断与观察员各有提示、首次与重新规划共用规则并截断环境事实（D46 P1）
- `2ba9af6` 2026-10-16 〔已推送〕 docs(plan): D46 P1——Agent 提示的一致性与正确性（共同原则与规划、派发外壳、纠错与协调者上下文、内置模板）

### D45 · 需求中指定的工具与模型（4 个提交：已推送 4）

- `8ad07f8` 2026-10-16 〔已推送〕 docs: D45 需求中指定的 EC2 验收；M2 验收 2 与 5 勾选
- `0668bc8` 2026-10-16 〔已推送〕 fix(mapping): 需求中指定终审修复——规划者写法不完整的 requested 被清理或忽略而不使计划失败；Worker 拒绝指定模型时同样如实停下、协调者不得换用指定的工具或模型；requested 只来自 KiroCrew 规划；改回指定工具后任务仍有效；两份规划提示写明例外；确认模式下预先说明不可用（D45）
- `aa52dc4` 2026-10-16 〔已推送〕 feat(mapping): 需求中指定的工具与模型——规划者标注 requested，平台照用（改回规划者的替换、不把指定模型改为 auto）；不可用时派发前拒绝并写明，不诊断不换工具；来源「需求中指定」；续作改工具时用户的新选择取代（D45）
- `197c41b` 2026-10-16 〔已推送〕 docs(spec,plan): D45 需求中指定的工具与模型——规划者标注、平台照用、不可用时如实拒绝不替换

### D44 · 计划确认（5 个提交：已推送 5）

- `a9c3c2e` 2026-10-16 〔已推送〕 docs: D44 计划确认的 EC2 验收
- `68ad2f3` 2026-10-16 〔已推送〕 fix(admission): 计划确认终审修复——只有 KiroCrew 规划会话的 plan 与重新规划任务在确认前运行；已归档 Team 的会话不能确认；等待确认不计入预算、确认重新计算并停止预览；会话视图、报告与时间线写明确认者；MCP 精简状态在等待确认时带出验收项与检查命令（D44）
- `d6c1266` 2026-10-16 〔已推送〕 feat(mcp,console),docs: 计划确认的 MCP 工具 octopus_confirm 与 octopus_start.admission、Skill 说明、控制台「计划待确认」与「确认并开始」、时间线标签；运维与进度（D44 §3.2–§3.3）
- `c4f5a5d` 2026-10-16 〔已推送〕 feat(admission): 会话可设确认后执行（admission: confirm）——规划照常完成，第一个执行任务前停在「计划待确认」，确认或修改后续作即开始；平台默认 default_admission = auto，不改变现有行为（D44 §3.1–§3.2）
- `adae986` 2026-10-16 〔已推送〕 docs(spec,plan): D44 准入第 1 期——计划确认（规划后、第一个执行任务前停下，确认或续作后执行；默认 auto 不变）

### D43 · 等待与自动恢复（6 个提交：已推送 6）

- `fcade99` 2026-10-16 〔已推送〕 docs: D43 等待与自动恢复的 EC2 验收
- `a0e3a27` 2026-10-16 〔已推送〕 fix(waiting): 终审修复——只有原生错误中的登录过期才等 Kiro 登录（Agent 报告的权限、额度不算）；失败后见到登录正常即交给人处理并不再每分钟检查；自动继续时等待的时长不计入预算；健康检查在宿主账号或隔离方式变化时也不保存（D43）
- `68e50f1` 2026-10-16 〔已推送〕 feat(console),docs: 时间线「条件满足，自动继续」、执行环境的「最近检查」；运维与进度（D43 §3.3）
- `da72dc2` 2026-10-16 〔已推送〕 feat(engine): Worker 重新启用、执行环境重新服务此空间与仓库凭证重新可用（且仍覆盖该仓库）后自动继续；可自动恢复的等待写明在等什么（D43 §3.2–§3.3）
- `f607150` 2026-10-16 〔已推送〕 feat(engine): 定期读取 Worker 健康并保存能力（边界不变时；有会话等 Kiro 登录的每分钟，其余每 10 分钟）；Kiro 登录在失败后见过过期、又恢复时，自动继续因登录失败的任务（D43 §3.1–§3.2）
- `f9810b3` 2026-10-16 〔已推送〕 docs(spec,plan): D43 恢复阶梯第 4 期——等待与条件满足后自动恢复（Kiro 登录、Worker 重新启用、执行环境、凭证；定期读取 Worker 健康）

### D42 · 会话预算（5 个提交：已推送 5）

- `5610dc2` 2026-10-16 〔已推送〕 docs: D42 会话预算的 EC2 验收
- `ffc2969` 2026-10-16 〔已推送〕 fix(recovery): 预算终审修复——会话停下后时长不再累计、上限为 0 不算用完；提示只说真正拒绝了自动纠错的那一项（记在失败 Attempt 的 budget_refused）；分配 Worker 也重新计算预算；重置预算不记作会话设置的修改；控制台与报告只在有纠错记录或因预算停下时显示预算行（D42）
- `ebe8759` 2026-10-16 〔已推送〕 feat(console),docs: 预算消耗可见——纠错摘要的 budget、「需要处理」写明哪一项用完、控制台纠错记录的预算行、MCP 精简状态与报告；OpenAPI 与 MCP 的设置增加 max_retries / budget_minutes，更正 attempt_minutes 默认值；运维与进度（D42 §3.3）
- `6e7382a` 2026-10-16 〔已推送〕 feat(recovery): 会话预算——协调者轮数、自动重试次数与时长，从会话开始或最近一次续作 / 请求诊断起计算；自动重试与诊断据此放行（D42 §3.1–§3.2）
- `ac9e4bf` 2026-10-16 〔已推送〕 docs(spec,plan): D42 恢复阶梯第 3 期——会话预算（协调者轮数、自动重试次数、时长，从续作起重新计算）

### D41 · 重新规划（5 个提交：已推送 5）

- `4e32e09` 2026-10-16 〔已推送〕 docs: D41 重新规划的 EC2 验收
- `c08c333` 2026-10-16 〔已推送〕 fix(replan): 终审修复——重新规划的提示引用已派发的上下文并分节限长（不超过 Worker 的 8000 字符）；改名后 depends_on 随之改写、可依赖已保留的任务；之后的任务不再带纠错要求；返工不重跑已生效的规划；被替换的 ID 不再复用；审查绑定实现者而非规划任务；用户任务名 replan-* 不受影响（D41）
- `6f76c48` 2026-10-15 〔已推送〕 feat(console),docs: 重新规划可见（映射来源、控制台纠错记录）；被替换的任务经变更记录删除；运维与进度（D41 §3.3）
- `5290339` 2026-10-15 〔已推送〕 feat(recovery,planning): 协调者 replan——替换失败任务及其后的任务、插入 KiroCrew 重新规划任务，其结果经校验追加，新任务 ID 避开历史；每个会话一次（D41 §3.1–§3.2）
- `96a494c` 2026-10-15 〔已推送〕 docs(spec,plan): D41 恢复阶梯第 2 期——协调者 replan，KiroCrew 重新规划剩余工作

### D40 · 交接精简与自适应轮询（2 个提交：已推送 2）

- `e1369c2` 2026-10-15 〔已推送〕 docs: D40 交接精简的 EC2 验收
- `43035d0` 2026-10-15 〔已推送〕 feat(engine): 前序交接精简为结构化摘要（结论、发现、候选、检查与完整交接文件路径，不再附原始结果与证据）；运行中的 Attempt 按时长降低查看频率，命令不受影响（D40）

### D39 · 看门狗（5 个提交：已推送 5）

- `855d65c` 2026-10-15 〔已推送〕 docs: D39 看门狗的 EC2 验收（两次运行）
- `9462dc0` 2026-10-15 〔已推送〕 feat(console,mcp,report),docs: 看门狗的记录可见；运维与进度（D39）
- `46c0fa7` 2026-10-15 〔已推送〕 feat(engine): 看门狗——原生任务没有进展或运行超时时停止、自动重试一次，仍卡住交给诊断；会话可设 idle_minutes / attempt_minutes（D39 §3.2）
- `098485a` 2026-10-15 〔已推送〕 feat(worker): 运行中 Attempt 的进展（state.json）与停止单个原生任务（D39 §3.1）
- `2e81045` 2026-10-15 〔已推送〕 docs(spec,plan): D39 恢复阶梯第 1 期——看门狗（原生任务没有进展或运行超时时停止、自动重试、再交给诊断）

### D38 第 2 期 · 换工具与批准代理（4 个提交：已推送 4）

- `c029dee` 2026-10-15 〔已推送〕 docs: D38 第 2 期批准代理的 EC2 部分验收（列表、拒绝、MCP）；执行边界处于 YOLO 时没有待批准项
- `2f23e9d` 2026-10-14 〔已推送〕 docs: D38 第 2 期批准代理——运维与进度；访问测试含 permissions.publish
- `0bcb5da` 2026-10-14 〔已推送〕 docs(plan): D38 第 2 期批准代理实施计划；spec 按 EC2 实测写明两类待批准项、答复的映射与 octopus_approve（A6）
- `567e331` 2026-10-14 〔已推送〕 docs: BACKLOG 记录换工具审查延后项；D38 第 2 期按 EC2 实测的 KiroCrew 批准接口修订 §4.1

### D37 第 3 期 · 候选提交、门禁与报告（3 个提交：已推送 3）

- `511ca5e` 2026-10-15 〔已推送〕 docs: D37 第 3 期门禁的 EC2 验收（四个会话）与审查延后项
- `05625c3` 2026-10-15 〔已推送〕 feat(prompts),docs: Agent 的任务说明与规划提示写明 artifacts_locked 与 tests_fail_on_baseline；D37 第 3 期运维与进度
- `47b7775` 2026-10-15 〔已推送〕 docs(plan): D37 第 3 期门禁（tests_fail_on_baseline、artifacts_locked）的设计细化与实施计划

### D38 · 换工具与批准代理（11 个提交：已推送 11）

- `6ac9d51` 2026-10-14 〔已推送〕 feat(console): 会话页「待批准」卡片（工具、命令与用途，确认后批准或拒绝；不能答复的人只看到内容）；会话视图的 permissions.publish（D38 §4.3）
- `3187da9` 2026-10-14 〔已推送〕 feat(mcp): octopus_approvals 与 octopus_approve（答复前须得到用户同意）；Skill 说明（D38 §4.3、A6）
- `684c5de` 2026-10-14 〔已推送〕 feat(approvals): 平台列出与答复会话的待批准项（创建者、Team owner、管理员；审计与变更记录；旧 Worker 说明不支持）；等待批准时的提示（D38 §4.2）
- `2f212f2` 2026-10-14 〔已推送〕 feat(worker): 会话的待批准项与答复（只读本会话父 slot；答复前重新核对；按来源转交 Gateway）（D38 §4.2）
- `04904b0` 2026-10-14 〔已推送〕 feat(console,mcp,report): 换工具记录可见——任务的 Attempt、MCP 状态与报告写明由协调者从哪个工具换来及原因；自动纠错设置可关闭换工具；Skill 说明（D38 §3.4、§3.5）
- `0e06b8d` 2026-10-14 〔已推送〕 feat(planning): 去掉 OpenCode→Claude 的内置替换；规划提示列出各工具在执行环境中是否可用与已知限制，由 Kiro 自行选择（D38 A5）
- `34d511a` 2026-10-14 〔已推送〕 feat(recovery): 协调者 switch_tool——平台按诊断时的执行环境核对（可用、已发布、与失败不同、审查独立、未关闭），改写任务并重跑；新 Attempt 记录 switched_from，变更记录与修订写明（D38 §3.3）
- `1c38871` 2026-10-14 〔已推送〕 feat(engine): 工具层失败交给诊断——平台记录中不可用的工具不派发、模型被拒、实际后端不符均为 failure_type tool（D38 §3.2）
- `f78fe1e` 2026-10-14 〔已推送〕 feat(recovery): 诊断上下文带上执行环境（工具、模型、已知限制、是否允许换工具）；协调者提示可推荐 switch_tool；recovery.switch_tools 开关（D38 §3.1、§3.5）
- `ce47710` 2026-10-14 〔已推送〕 feat(worker): /health 报告各 Coding 工具能否启动（配置路径或 Gateway 搜索路径；Kiro 另报登录）与 K01 已知限制（D38 §3.1）
- `caa7c7e` 2026-10-14 〔已推送〕 docs(spec): D38 换工具与批准代理——由 KiroCrew 协调者推荐换工具（平台核对并记录，无内置回退链）；批准代理（K04）

### D38 第 1 期 · 换工具与批准代理（3 个提交：已推送 3）

- `df79d43` 2026-10-14 〔已推送〕 docs: D38 第 1 期换工具的 EC2 验收记录
- `79bf010` 2026-10-14 〔已推送〕 docs: D38 第 1 期换工具——进度、运维（刷新 Worker 能力）与后续排期
- `f63693d` 2026-10-14 〔已推送〕 docs(plan): D38 第 1 期换工具实施计划；spec 补充核对规则（与失败的配置不同即可、Kiro 登录另报、平台按保存的能力派发前检查）

### D37 第 2 期 · 候选提交、门禁与报告（2 个提交：已推送 2）

- `7c6a64c` 2026-10-14 〔已推送〕 docs: D37 第 2 期 Feature 报告的 EC2 验收记录
- `45c2b13` 2026-10-14 〔已推送〕 docs(plan): D37 第 2 期 Feature 报告实施计划

### D37 第 1 期 · 候选提交、门禁与报告（4 个提交：已推送 4）

- `ef8bc28` 2026-10-14 〔已推送〕 docs: BACKLOG 记录会话独立预览域名与 D37 第 1 期的审查延后项
- `e5d61da` 2026-10-14 〔已推送〕 docs: D37 第 1 期候选提交与命名门禁的 EC2 验收记录
- `c9881d3` 2026-10-14 〔已推送〕 fix(gates): D37 第 1 期审查修复——检查命令留下的后台进程一并结束、不带 Worker 的 PYTHONPATH、无法启动时失败而不挂起；Worker 拒绝生成候选或运行检查按门禁失败进入纠错；按时间取会话分支所在的候选；检查留下的未跟踪文件不算改动；已验证候选允许审查后的交付改动；新会话没有已验证候选时不发布
- `a061fc9` 2026-10-14 〔已推送〕 docs(plan): D37 第 1 期候选提交与命名门禁实施计划

### D36 第 3 期 · 开发成果预览（1 个提交：已推送 1）

- `70256b7` 2026-10-14 〔已推送〕 docs: D36 第 3 期会话独立预览域名的 EC2 验收记录

### D36 第 2 期 · 开发成果预览（3 个提交：已推送 3）

- `d7fff04` 2026-10-14 〔已推送〕 docs: D36 第 2 期远程预览与 D35 第 1 期凭证的 EC2 验收记录；第 2 期发布已部署，实际推送待负责人同意
- `d90945d` 2026-10-14 〔已推送〕 docs: MVP-RELEASE 状态行更新——origin 已推送 a57b30e；D36 第 2 期与 D35 已部署到 EC2，验收待管理员登录
- `a7df6cd` 2026-10-13 〔已推送〕 docs: D36 第 2 期「浏览器内远程预览」实施计划

### D35 第 2 期 · 代码仓库凭证（3 个提交：已推送 3）

- `9a0e65b` 2026-10-14 〔已推送〕 docs: D35 第 2 期发布的 EC2 验收记录——公开测试仓库的分支与 Draft PR #1、再次发布沿用、审计与变更记录
- `bf8c2b2` 2026-10-13 〔已推送〕 docs: D35 第 2 期发布到仓库
- `79cca8a` 2026-10-13 〔已推送〕 docs: D35 第 2 期「平台代发布」实施计划

### D36 第 1 期 · 开发成果预览（2 个提交：已推送 2）

- `26f6e72` 2026-10-13 〔已推送〕 docs: D36 第 1 期 EC2 验收记录、控制台域名；BACKLOG 补充第 1 期审查延后项
- `4bbaaf9` 2026-10-13 〔已推送〕 docs(plan): D36 第 1 期——下载会话源码

### D35 · 代码仓库凭证（2 个提交：已推送 2）

- `0e1866d` 2026-10-13 〔已推送〕 docs: D35 代码仓库凭证第 1 期实施计划
- `d97b3b4` 2026-10-13 〔已推送〕 docs(spec): D35 代码仓库凭证（作用域、工作流槽位与会话绑定、平台代发布与可选注入）；D36 开发成果快速预览（下载与浏览器内远程预览）

### D34 · 会话变更记录与运行优化（4 个提交：已推送 4）

- `69ee3f9` 2026-10-13 〔已推送〕 docs: D34 EC2 验收记录（平台与 w1，不执行任务）
- `7bb14c3` 2026-10-13 〔已推送〕 docs: D34 会话变更记录与运行优化（运维、发布记录、BACKLOG、README、架构）；控制台测试等待上限 3 s
- `e3a1095` 2026-10-12 〔已推送〕 docs(plan): D34 实施计划——变更记录、稳定性、任务列精确化、续作只重跑受影响任务
- `1e5c8a0` 2026-10-12 〔已推送〕 docs(spec): D34 会话变更记录与运行优化——全部变更可审计、续作只重跑受影响任务、任务列精确化、稳定性

### D33 · Team 与 Worker 管理（1 个提交：已推送 1）

- `732fa0d` 2026-10-12 〔已推送〕 docs: D33 访问管理第 2 期——Team 与 Worker 管理、派发前核对、管理操作审计（运维、Skill、架构、BACKLOG）

### D32 · 会话修订历史（2 个提交：已推送 2）

- `ec02d73` 2026-10-12 〔已推送〕 docs: D32 会话修订历史；EC2 验收记录与能力清单
- `c3c0f4e` 2026-10-12 〔已推送〕 feat(revisions): 会话修订历史与 Prompt 差异（后端，D32）——每次创建 / 规划 / 续作 / 自动纠错记录完整快照与摘要；旧修订按记录还原；GET /v1/runs/<id>/revisions[/<version>|/diff]；MCP octopus_revisions 只返回变化；OpenAPI 与 Skill

### 未标记 · 功能（67 个提交：已推送 64、已部署 EC2 3）

- `ccd91e0` 2026-10-21 〔已部署 EC2〕 feat(dashboard): MVP 里程碑面板——M1–M5 各一条按状态分段的进度条（未开始显示为空轨）、完成度与已验收数；点一行展开其条目，「展开全部」开关（role=switch）；展开状态存 localStorage，实时页刷新后保留；实时页不重播动画；筛选与搜索同样作用于里程碑（负责人 2026-10-21）
- `e069a5f` 2026-10-21 〔已部署 EC2〕 feat(tools): 实时进度看板——tools/progress.py --serve [PORT] [--ec2]：只在 127.0.0.1，每次请求现读 docs/progress.json 与 git，页面每 15 秒刷新、不写文件；--ec2 每 5 分钟经 SSM 只读读取 EC2 运行的提交（负责人 2026-10-21）
- `b733fc6` 2026-10-21 〔已部署 EC2〕 feat(console): 凭证卡片「编辑范围」——已有凭证可改仓库范围（PATCH repos）
- `e0d8791` 2026-10-20 〔已推送〕 feat(tools): 完成记录机制——tools/progress.py 从 git 列出自 origin/main 起的全部提交，按决策分组并标明已推送 / 已部署 EC2 / 仅本机，连同 docs/progress.json 的任务状态写入看板与 docs/PROGRESS.md（负责人 2026-10-20）
- `092a19a` 2026-10-20 〔已推送〕 feat(console): 任务的模型改为下拉选择该执行环境公布的模型——「自动」时也列出全部（原输入框按当前值过滤建议，自动时只剩 auto）；模型取自将要执行的环境（平台的默认位置或所选环境），工作流编辑器不再取空间里的第一个环境
- `fc75650` 2026-10-18 〔已推送〕 feat(deploy): Gateway 需要重启时先排空——Worker 有 .maintenance 标记时不接受新的准备与派发（503 native_unavailable，平台自动退避重试），安装脚本等 Gateway 中运行的原生任务结束（最多 DRAIN_MINUTES，默认 30 分钟）后重启，Worker 重启后移除标记（复盘 #8，EC2 run-f7670bfb…）
- `ac3144f` 2026-10-16 〔已推送〕 feat(worker): 停用 OpenCode（K01，负责人 2026-10-16）——Worker 配置 disabled_tools（默认 ["opencode"]）中的工具报告为不可用并写明原因，规划者不选、协调者不换用、派发前停下；disabled_tools = [] 可恢复
- `b3574d7` 2026-10-15 〔已推送〕 feat(mapping): 每个任务写明工具与模型的来源（提交的 steps、工作流、模板 / 规则匹配、导入的文档、KiroCrew 规划、续作时的用户修改、协调者换用），控制台、MCP 与报告可见（M2 验收 2）
- `eb28bc0` 2026-10-15 〔已推送〕 feat(composition): 执行边界默认保持 KiroCrew 常驻 YOLO（crew_yolo，默认开启）——每次 Gateway 启动写入 agent.dangerously_skip_permissions，不随重启失效；crew_yolo = false 恢复逐项批准
- `50843ff` 2026-10-14 〔已推送〕 feat(console): 会话页「报告」（不允许脚本的框中显示 HTML、下载 Markdown / HTML）；MCP octopus_report；文档
- `819ada1` 2026-10-14 〔已推送〕 feat(sessions): 会话报告接口 GET /v1/runs/<id>/report?format=md|html
- `b04b3ca` 2026-10-14 〔已推送〕 feat(report): 会话的 Feature 报告（Markdown 与 HTML）——需求与计划、各任务门禁与候选、审查结论、纠错与发布；转义会话文字、只链接 https 的 PR
- `878257b` 2026-10-14 〔已推送〕 feat(console): 任务的门禁与候选、会话的已验证候选；MCP 状态带门禁摘要；文档
- `7f17a17` 2026-10-14 〔已推送〕 feat(credentials): 发布只推送已验证的候选，之后有改动时 409 publish_unverified；会话视图带 verified
- `f670d8d` 2026-10-14 〔已推送〕 feat(engine): 代码任务的候选与检查命令、审查绑定候选——门禁失败进入自动纠错；新会话执行契约 3，旧会话与旧 Worker 行为不变
- `a2bad0d` 2026-10-14 〔已推送〕 feat(worker): 运行任务的检查命令——顺序执行、每条与合计超时并结束进程组、保留隐去凭证的输出末尾、停止会话时结束
- `eb9338f` 2026-10-14 〔已推送〕 feat(worker): 任务候选提交——临时索引提交、检查新增内容、通过才移动会话分支；审查核对 HEAD 与未提交改动
- `10e8d5b` 2026-10-14 〔已推送〕 feat(gates): 为现有的五项验收检查命名并逐条记录（行为不变）
- `3567a65` 2026-10-14 〔已推送〕 feat(deploy): 会话独立预览域名的部署——预览分发加 *.<PreviewSessionDomain> 别名、通配证书与 A / AAAA 记录；up 找到或申请 us-east-1 通配证书并在托管区完成 DNS 验证；stack.env 设为 preview.tinyboat.blog
- `3b83c8b` 2026-10-14 〔已推送〕 feat(deploy): 预览 nginx 把访问的主机名交给 API、应用为上级域设置的 Cookie 改回本会话主机；安装脚本写入 preview_domain，push 传入 PREVIEW_SESSION_DOMAIN
- `40465bf` 2026-10-14 〔已推送〕 feat(preview): 会话独立预览域名——https://<run-id>.<preview_domain>/ 由主机名指明会话，Cookie 与打开链接须属同一会话；会话自己的域名不再清除站点数据；cloudfront.net 地址保留单一域名模式
- `232b7fc` 2026-10-13 〔已推送〕 feat(console): 会话页「发布到仓库」与 PR / MR 链接；MCP octopus_publish
- `a69f2f4` 2026-10-13 〔已推送〕 feat(credentials): 平台代发布——校验 bundle、只快进推送 octopus/<run>、创建或沿用 Draft PR / MR、写入会话与审计
- `6f58590` 2026-10-13 〔已推送〕 feat(worker): 导出待发布的提交——自动提交未提交改动（排除 .env 与依赖）、拒绝疑似凭证、以基线为前置的 bundle
- `f963713` 2026-10-13 〔已推送〕 feat(deploy): 平台主机生成代码仓库凭证的主密钥（root:octopus 0640，不进 SSM 与备份），切换后写入 credentials_key_file；文档写明保管、恢复与使用
- `bca0456` 2026-10-13 〔已推送〕 feat(console): 代码仓库凭证的设置页、向导中的凭证选择与会话页显示；MCP octopus_credentials
- `390bd22` 2026-10-13 〔已推送〕 feat(credentials): 用绑定的凭证克隆私有仓库——GIT_ASKPASS 临时文件、push 地址不可用、错误去除凭证、使用写审计
- `499e609` 2026-10-13 〔已推送〕 feat(credentials): 提交时解析并固定绑定凭证（明确指定 → 工作流默认 → 按仓库自动匹配），会话显示创建者与凭证，可改绑
- `9612613` 2026-10-13 〔已推送〕 feat(credentials): 代码仓库凭证——个人 / Team / 平台作用域、授权、只写秘密、托管方测试与审计
- `9bd5e24` 2026-10-13 〔已推送〕 feat(credentials): 凭证加密存储——openssl AES-256-CBC（主密钥从文件读取）+ HMAC 先加密后认证
- `3c4aaab` 2026-10-13 〔已推送〕 feat(deploy): 远程预览分发——平台 nginx auth_request 与 Worker 主机预览端口（私网 TLS + 校验头）
- `60050e4` 2026-10-13 〔已推送〕 feat(console): 会话页「远程预览」——启动、打开、停止，状态与最近日志
- `2a29f78` 2026-10-13 〔已推送〕 feat(mcp): octopus_app 启动、查看、打开与停止会话的远程预览；Skill 说明使用方式
- `f40cd7b` 2026-10-13 〔已推送〕 feat(sessions): 远程预览——平台记录与回收、一次性打开链接与预览域 Cookie、auth_request 每次复核查看权限
- `2dbe0cd` 2026-10-13 〔已推送〕 feat(worker): 在执行环境中运行会话应用——按启动声明安装与启动、独立进程组、47000–47999 端口、就绪检测、最多 3 个
- `a57b30e` 2026-10-13 〔已推送〕 feat(deploy): 控制台自定义域名 kirocrew-on.tinyboat.blog——别名、us-east-1 证书与 Route 53 别名记录
- `73012f1` 2026-10-13 〔已推送〕 feat(sources): 新建项目也初始化为 Git 仓库——octopus/<run> 分支与空的首个 commit
- `320c2c0` 2026-10-13 〔已推送〕 feat(console): 会话页「下载源码」；规划要求交付任务写出启动声明；运维与发布文档
- `991cba1` 2026-10-13 〔已推送〕 feat(mcp): octopus_download 下载并解压会话源码到本机，返回启动步骤
- `c448ce3` 2026-10-13 〔已推送〕 feat(sessions): 下载会话源码——10 分钟签名链接、每次复核权限、流式转发不落盘、下载写入变更记录
- `7799092` 2026-10-13 〔已推送〕 feat(worker): 流式打包会话源码（含 .git，排除依赖、.env 与符号链接，200 MB 上限）
- `32bab55` 2026-10-13 〔已推送〕 feat(preview): 启动声明 .octopus/preview.json 的校验与自动识别
- `fd51b42` 2026-10-13 〔已推送〕 feat(mcp): 管理员工具——用户启停与升降、Team 管理、执行环境管理（需 admin Token，不创建用户、不设密码）
- `09de3a0` 2026-10-13 〔已推送〕 feat(mcp): 身份、执行环境、分配 Worker 与变更记录工具；提交不再在客户端预选 Worker；按 Token scope 列出工具
- `72d910f` 2026-10-13 〔已推送〕 feat(connect): 以本人账号签发 30 天 MCP Token、可选 --admin（7 天），完成后登出临时登录
- `68c62e6` 2026-10-13 〔已推送〕 feat(mcp): API 错误以 {status, code, error} 结构返回
- `f726d83` 2026-10-13 〔已推送〕 feat(console): 续作对话框选择要重跑的后续任务（规则补全不可取消），任务列表标注沿用的结果，修订对比显示任务契约字段
- `f23157e` 2026-10-13 〔已推送〕 feat(sessions): 续作只重跑所选与必须重跑的任务（依赖、审查 / 测试与未验收规则补全），其余沿用上一版结果；OpenAPI、MCP 与 Skill
- `7141dba` 2026-10-13 〔已推送〕 feat(engine): 规划补全的需求与任务契约传给每个任务与协调者，结论逐项对照验收项并单列建议
- `4be3032` 2026-10-13 〔已推送〕 feat(planning): 任务结构字段（类别、交付物、验收项、验证命令、依赖、可改动路径）与 KiroCrew 规划校验；修订对比包含任务契约
- `c43a17f` 2026-10-13 〔已推送〕 feat(deploy): Gateway 依赖未变时部署不重启，进行中的原生任务不中断
- `96d7e6e` 2026-10-12 〔已推送〕 feat(worker): 派发前检查 kiro-cli 登录，过期时会话等待而不失败；执行环境显示登录状态
- `5c5c9c3` 2026-10-12 〔已推送〕 feat(engine): 运行时取消与结果丢失自动重试一次、不占纠错轮次；登录过期归为认证失败；回复丢失但结论与交接有效时照常判定
- `c57c593` 2026-10-12 〔已推送〕 feat(console): 会话页「变更记录」——按人 / 系统筛选，显示操作者、客户端、字段前后值与原因
- `63978d7` 2026-10-12 〔已推送〕 feat(journal): 会话变更查询接口（按人 / 系统与动作筛选）与导出
- `07f074a` 2026-10-12 〔已推送〕 feat(journal): Engine、Worker 回执、KiroCrew 规划与纠错的写入经变更记录；守护测试禁止绕过
- `d094342` 2026-10-12 〔已推送〕 feat(journal): 会话命令经变更记录写入——操作者、客户端、命令参数与字段前后值
- `816dbad` 2026-10-12 〔已推送〕 feat(journal): 会话变更记录模块——比较前后值、记录操作者与客户端；Token 识别控制台 / Coding Agent / API
- `013ab16` 2026-10-12 〔已推送〕 feat(console): 执行环境改名、更新连接与退役；创建平台账户与个人空间执行环境并入「用户管理」
- `bc568d3` 2026-10-12 〔已推送〕 feat(console): 「团队与成员」每个 Team 一页——改名、归档 / 恢复、成员与角色、退出 Team、管理员更换执行环境；归档 Team 不出现在提交与共享选择器中
- `4ed3e01` 2026-10-12 〔已推送〕 feat(engine): 派发前核对 Worker 仍服务会话所在空间（已更换 / 已移出 / 已停用 / 已退役时进入等待）；续作同样核对；改派到新环境时重新准备
- `ab3cbd8` 2026-10-12 〔已推送〕 feat(workers): Worker 改名与退役（退役后从列表与选择器消失、历史会话保留引用）；服务的全部用户可刷新模型；执行环境操作写入审计
- `fba2ac4` 2026-10-12 〔已推送〕 feat(teams): 管理员更换 Team 执行环境——共享环境移除服务关系、专属主机改为空闲并只回到原 Team，重新部署后才可给其他 Team
- `096a206` 2026-10-12 〔已推送〕 feat(teams): 管理员托管 Team 成员与角色、成员自行退出；成员变更写入审计
- `7876698` 2026-10-12 〔已推送〕 feat(teams): Team 改名与归档（归档后拒绝新会话与新成员，旧会话可查看与停止）；Team 创建与管理写入审计
- `ff7768c` 2026-10-12 〔已推送〕 feat(migrations): 迁移 4——Team 归档、Worker 退役与空闲主机曾服务的 Team
- `d5cf9ba` 2026-10-12 〔已推送〕 feat(console): 会话页「修订历史」——按时间列出创建 / 规划 / 续作 / 自动纠错，任选两版对比需求与各任务（字段前后值、Prompt 逐行增删，未变化的任务折叠）

### 未标记 · 修复（49 个提交：已推送 47、已部署 EC2 2）

- `39458e2` 2026-10-21 〔已部署 EC2〕 fix(deploy): 边界运维——重复 boundary add 不给 --memory / --cpu 时保留现有资源上限（不再重置并排空重启 Gateway）；--owner 按小写匹配平台用户；boundary add 传 AWS_REGION；systemctl disable 的边界部署时不检查、不重启
- `ff7e42a` 2026-10-21 〔已部署 EC2〕 fix(deploy): 边界的中转配置名为 kirocrew-relay-boundary-<名称>.conf——nginx 按文件名读 conf.d，须在定义 log_format kirocrew_native 的 kirocrew-native.conf 之后（EC2 2026-10-21：nginx -t 报 unknown log format，已回退、crew_url 未改）
- `8db5430` 2026-10-21 〔已推送〕 fix: 次要问题第 7 批——Worker 删除知识如实报告 KiroCrew 的拒绝与错误；多个平台 Knowledge 任务各写一次；等待执行环境时每 30 秒重试知识写入；基线测试按条件等待（高负载下的偶发失败）
- `2cd7a88` 2026-10-21 〔已推送〕 fix: 次要问题第 6 批——安装脚本不再以命令行参数传管理员密码；重新规划用精简的角色与技能目录（最长约 7350 / 8000）；placement 按创建者回答写入 OPERATIONS
- `f5c41b7` 2026-10-21 〔已推送〕 fix: 次要问题第 5 批——预览的本机步骤给 cwd 加引号、拒绝控制字符与符号链接的声明；知识标记与 exclude 文件拒绝符号链接；角色卡片读一次、格式错时说明是哪张
- `c4e1c7d` 2026-10-20 〔已推送〕 fix: 次要问题第 4 批——续作可去掉任务的角色；已归档 Team 不给新会话的执行位置；任务卡片显示实际交付的方法；知识提议「已含这些条目」时也审计凭证使用
- `6db7e47` 2026-10-20 〔已推送〕 fix: 次要问题第 3 批与磁盘——上一轮对话本身出错时返工全新派发；平台追加的 Evaluator / Knowledge 任务各记一次修订；安装时自动清理旧发布（EC2 根卷曾被 73 个旧发布占满，已扩到 500 GiB，OPERATIONS §10）
- `c2ca670` 2026-10-20 〔已推送〕 fix: 终审延后的次要问题第 2 批——调试技能不再写「Ask for help」；boundary.sh 进入 bash 语法检查；Knowledge 任务完成而无条目时如实说明；续接被拒时变更记录写明原因
- `cb4e636` 2026-10-20 〔已推送〕 fix: 终审延后的次要问题第 1 批——修订对比显示角色标题；报告容忍坏的 skills 证据；octopus/knowledge… 分支留给知识提议；删除过的知识不再写回；共享环境的时间线计入「发布时提到仓库」
- `3ad6379` 2026-10-20 〔已推送〕 fix(deploy): kiro-login 先核对主机上有这个边界——不存在时立即列出现有边界并退出，不再空等设备码（2026-10-20 kiro-login --boundary kirocrew-on）
- `f5954f1` 2026-10-20 〔已推送〕 fix(placement): 分配执行环境时在预算重置之后写入执行位置——此前用旧 snapshot 覆盖了 restart_budget 写的 budget_since（全量回归 test_budget 发现）
- `3f9352d` 2026-10-20 〔已推送〕 fix(deploy): 主机安装 Agent 的 Playwright Chromium 所需的 16 个系统库——审查与前端验证此前无法打开页面（exit 127，EC2 run-48f02d32…）
- `9f820ac` 2026-10-18 〔已推送〕 fix(kirocrew): 现有边界的 config.json 去掉平台以前写入、KiroCrew 从未读取的键（memory.enabled、dashboard.host / port、workspaces.default.path），原生测试核对现有边界；协调者另以 KiroCrew 的 managedToolPolicy.exclude 屏蔽 monitor 工具（与 kiro-cli 的 excludedTools 并用）
- `87332a1` 2026-10-18 〔已推送〕 fix: 延后项第二批——新仓库的凭证下拉按会话空间过滤并显示范围（与启动向导共用规则）；Evaluator 缺项或缺证据记为 contract、判定不通过记为 implementation；追加的 Evaluator 不复用历史 id；报告的 Evaluator 一节只在门禁生效的会话出现
- `6e853db` 2026-10-18 〔已推送〕 fix: 延后项第一批——会话页开启自动发布时按创建者核对凭证；新建源码启动时要求自动发布须给出仓库；已记录的发布仓库不区分大小写；已有空仓库记为「发布到已有的空仓库」，run.published 带新仓库地址；停止后仍等待验收的会话不送诊断
- `649d30a` 2026-10-18 〔已推送〕 fix(evaluation): Evaluator 把只有 pending 的结论写成 blocked 时，按其逐项判定进入「验收待确认」（含 BLOCKED 行）；说明与卡片写明有 pending 而无 fail 时 decision 写 pass（EC2 run-346afc30…）
- `4bdd105` 2026-10-18 〔已推送〕 fix(publish): 推送判定排除否定说法——「不推送、不提交 PR」「do not push」不再被记为「要求推送或创建 PR」，推送失败的提示与规划说明同用一处判定（EC2 run-f897a462…）
- `ed5b0a6` 2026-10-17 〔已推送〕 fix(worker): KiroCrew 已没有父会话的活动 slot 时（incognito 的 slot 在 Gateway 重启后不恢复，EC2 run-6d7c132b… 删除反复 404），停止照常结束，关闭与删除记 missing-session-observation.json 后照常完成；删除原生历史时 404 视为已不存在
- `7d0d733` 2026-10-17 〔已推送〕 fix(worker): KiroCrew 不导出 incognito / temporary 会话（export_slot_not_persistent）时，关闭与删除记一份标明的观察记录 restricted-session-observation.json（native_restricted_observation），不伪造备份，照常关闭、删除（负责人 2026-10-17）；其他导出错误仍阻止关闭与删除。3ec713b 期间以 incognito 建立的会话（run-6d7c… 删除卡在 stopping）由此可以关闭与删除
- `d417ee2` 2026-10-17 〔已推送〕 fix(publish): 发布只到会话启动时的仓库——新建或上传源码的会话推送失败时提示「下载源码」或以已有仓库新建会话，不再指向「发布到仓库」；规划者标注「要求提交 PR」时只对来自仓库的会话开启自动发布，否则记说明；OPERATIONS 更正记忆模式说明（EC2 run-f7670bfb…）
- `c9bb2ac` 2026-10-17 〔已推送〕 fix(engine): KiroCrew 的 AcpProcessDied「Runtime process died …」与「ACP process exited」同为运行时进程退出，按 L0 自动重试一次（runtime_exited），不交给协调者（EC2 run-f7670bfb…，部署重启运行时）
- `6077f92` 2026-10-17 〔已推送〕 fix(worker): 父会话改回以 persistent 建立——KiroCrew 只导出 persistent 的会话（session_export.py:380），incognito 的父会话无法备份，3ec713b 后新建的会话不能关闭或删除（EC2 run-6d7c132b… 复现）；incognito 仍读记忆、模式只能收紧。记忆按执行环境分隔（每人独立 Worker，Team 共用）。测试改用严格的假 KiroCrew：按会话的记忆模式拒绝导出，断言平台新建的父会话能备份并关闭
- `5efe8a3` 2026-10-17 〔已推送〕 fix(engine): Agent 答复后 KiroCrew 未能清理残留进程组（kill failed … process group still has members）按运行时故障 L0 自动重试一次（runtime_cleanup），不交给协调者、不消耗纠错轮次（EC2 run-6d7c132b…）
- `754d3e6` 2026-10-17 〔已推送〕 fix(console): 时间线为后端写入的 15 种事件补上标题（run.published、run.credentials、远程预览、审批答复、检查命令等）；契约测试——从 octopus/ 的 journal 调用读出每种事件，控制台必须都有标题，运行时拼出的种类须登记（EC2 run-d098543e… 时间线显示原始的 run.published）
- `6fdcaf2` 2026-10-16 〔已推送〕 fix(recovery): 协调者不再原地打转（EC2 run-40a1…）——诊断上下文列出可选目标 allowed_targets（失败任务及其上游），此前各轮带结果与平台拒绝的原因（refused）；拒绝写明目标排在失败任务之后、可选哪些、应返工上游；协调者说明写明审查后的「修复」任务不能作为目标；规划者不再在审查之后另设修复任务
- `eaba137` 2026-10-16 〔已推送〕 fix(engine): Agent 的 ACP 进程退出（Gateway 重启等）按瞬时失败自动重试一次，不经诊断、不占纠错轮次（设计 §8.1 L0；EC2 故障注入发现）
- `f65187e` 2026-10-16 〔已推送〕 fix(replan,worker): 重新规划的提示补回任务字段示例（EC2：规划结果缺少 prompt 未通过校验）；Worker 上报检查命令可用的常见命令，首次规划、重新规划与协调者据此写 verify（EC2：主机只有 python3，verify 用 python 退出码 127）；协调者把 127 当作 verify 写错
- `2439571` 2026-10-15 〔已推送〕 fix(mapping): 协调者换用的来源只带原因前 40 字（完整原因在 Attempt 的 switched_from）
- `b545803` 2026-10-15 〔已推送〕 fix(watchdog): 看门狗审查修复——停止前写意图标记，停止超时或重启后仍按看门狗判定；等待批准的时间不算没有进展；没有进展信号时只按总时长；已自行结束的任务保留结果；空闲时长按 Worker 时钟、最后工具取 KiroCrew 隐去后的值；停止失败一分钟后再试；默认总时长 170 分钟（低于 KiroCrew 的 180 分钟期限）；协调者区分不会结束的命令与工具挂起
- `e7db020` 2026-10-15 〔已推送〕 fix(gates): 第 3 期门禁审查修复——任务从起点计算（重试中的撤回不算改动）；基线为三方合并（冲突跳过）；为已有代码补测试、检查命令安装依赖、工作区有未进入候选的改动或同名本地文件时跳过；paths 支持 **/ 与整个项目；检查命令留下的文件不算交付物；基线运行期间预览、下载与发布返回 409；停止 / 超时的基线不判定
- `a402caa` 2026-10-14 〔已推送〕 fix(approvals): 批准代理审查修复——控制台按完整标识区分请求（被替换的请求不沿用已打开的确认）、答复中防重复、会话结束后不显示；读取失败不再把 401 传给浏览器；答复失败如实说明未答复；Worker 的「已不在等待中」为 409；缺少标识时说明缺什么；MCP 说明不支持与截断、octopus_approve 标记为需确认
- `e0a45de` 2026-10-14 〔已推送〕 fix(recovery): 换工具审查修复——换用后的实现不能与其后的审查同一工具（模板中未标 kind 的 review 也算审查）；重试后仍标注换用来源；协调者提示如实写明原生错误重试后再现的识别方式；平台保存的能力过期时提示刷新；未核实的后端仍等待用户；控制台纠错记录写明换工具
- `2aa3941` 2026-10-14 〔已推送〕 fix(report): Feature 报告审查修复——Markdown 中多行文字放入足够长的代码块、单行文字转义标点（不再被 Agent 文字改变结构或伪造链接）；显示任务契约并标出未报告的验收项；请求的工具取自该次执行、未决定的纠错轮次、需求不过早截断、HTML 结论保留换行、无法运行的命令、改写过的历史与推送的提交；Safari 下载
- `be34901` 2026-10-14 〔已推送〕 fix(preview): 会话独立预览域名审查修复——未设置时不写 preview_domain（旧版本可回滚）、通配证书等待最多 40 分钟并沿用待验证的证书、证书查询用 JSON 并优先已签发的、申请令牌按域名生成；配置了会话域名时其他主机名与缺失的主机头不再退回共用模式
- `937ba64` 2026-10-14 〔已推送〕 fix(credentials): 令牌能读不能推送时说明缺少的权限（credential_permission：GitHub Contents 读写 / GitLab write_repository），而不是原样的 git 403（EC2 实测）
- `b894363` 2026-10-14 〔已推送〕 fix(credentials): 第 2 期审查修复——发布前检查克隆以来的全部历史，拒绝时分支不动；排除的目录不读入 Git；平台先限大小；推送后 PR / MR 失败也记录与审计
- `d66550b` 2026-10-13 〔已推送〕 fix(credentials): 发布沿用第 1 期审查的加固——使用时复核凭证仍可用、拒绝内网主机、托管方 API 不跟随跳转且限制回答大小、推送只用 https 且不读全局 git 配置
- `bfc5ccb` 2026-10-13 〔已推送〕 fix(worker): 发布的 bundle 带完整历史（平台没有仓库副本可满足基线前置；推送时只传新增对象）
- `9be0d91` 2026-10-13 〔已推送〕 fix(credentials): 第 1 期审查修复——使用时复核会话仍可用该凭证、托管方检查不跟随跳转且拒绝内网地址、带凭证的 git 不读全局配置且 askpass 只答本主机、撤下凭证留痕并阻止匿名克隆、会话页与 MCP 可更换凭证
- `a3f70b4` 2026-10-13 〔已推送〕 fix(worker): 自动识别的项目已有依赖时直接启动，不再以 npm ci 覆盖（声明的安装步骤仍在锁文件变化时重跑）
- `a3c7739` 2026-10-13 〔已推送〕 fix(deploy): 远程预览审查修复（nginx）——访问 API 带 gate 头、拒绝 Service Worker 脚本、应用响应一律 no-cache、忽略应用的 X-Accel-*；文档写明同源防护与剩余风险
- `6a48ee4` 2026-10-13 〔已推送〕 fix(sessions): 远程预览审查修复（平台侧）——Worker 重启后旧记录在新预览启动前与每分钟回收时停止、关闭中的会话立即失效、退役 Worker 不再调用、Worker 报告的端口与名称受校验、状态与停止调用短超时、本机接口需 nginx 的 gate 头、打开时一并清除缓存与页面
- `7c54029` 2026-10-13 〔已推送〕 fix(worker): 远程预览审查修复（Worker 侧）——回答带进程实例、静态服务不给点文件与项目外链接、ready.path 只收可见 ASCII、意外错误也判为失败、依赖锁文件变化时重新安装
- `bf90e7e` 2026-10-13 〔已推送〕 fix(download): 打包出错不再产生「完整」的残缺 zip；保留空目录与可用的 .git、清除会执行命令的 git 设置；说明文件含依赖安装；下载前先试算
- `c9f889c` 2026-10-13 〔已推送〕 fix(server): 下载路由的局部变量 name 遮蔽了名称校验函数（Worker 登记 500）；新增检查：请求处理函数不得重新绑定模块导入的名字
- `913945f` 2026-10-13 〔已推送〕 fix(mcp): 审查修复——参数类型错误不再使 MCP 服务崩溃；--admin 不与已有 Token 同用；写入客户端配置失败时不打印 Token 并吊销刚签发的 Token；登出失败不掩盖结果；工具列表探测 5 秒且未知时不缓存；设置成员必须给出角色；Skill 说明 client_error 与 worker_unavailable
- `2ea7dd0` 2026-10-13 〔已推送〕 fix(d34): 审查修复——导出含全部变更、验收结果格式错误不再判任务失败、MCP 状态含沿用任务、自动重试标记在重试本身、原生页面的停止不重试、kiro-cli 登录只认可识别的结果且不占锁、派发冲突转为核对、Gateway 指纹含 pipeline_spec 与 drop-in、管理员读取变更记录写审计、运行中的部分回复不逐次记录
- `23396a9` 2026-10-12 〔已推送〕 fix(access): 审查修复——自动诊断同样核对执行环境；退役 Worker 上的会话在本地关闭 / 删除、不能重新打开，重新打开中的会话计入退役检查；归档 Team 可调整已有成员角色；可移除已停用的成员；归档 Team 的会话停用续作 / 诊断 / 重新打开 / 分配并说明原因；退出唯一的 Team 后回到个人空间
- `436233b` 2026-10-12 〔已推送〕 fix(console): 修订差异只在改动处保留 2 行上下文、其余折叠；修订列表不重复类型（EC2 实测长 Prompt）

### 未标记 · 文档（32 个提交：已推送 28、已部署 EC2 4）

- `904618b` 2026-10-21 〔已部署 EC2〕 docs: 进度——M2 / M3 / M4 / M5 按 MVP-RELEASE §6 / §7 与 BACKLOG 拆成 21 个条目（各写现状与是否需设计），看板可见（负责人 2026-10-21 问）
- `b4f74ee` 2026-10-21 〔已部署 EC2〕 docs: 进度看板刷新（EC2 17687ec）
- `8540e96` 2026-10-21 〔已部署 EC2〕 docs: 进度——重新规划复核保留检查已部署 EC2（17687ec）
- `68a6b12` 2026-10-21 〔已部署 EC2〕 docs: 进度看板刷新（EC2 d02325d）
- `f27c24d` 2026-10-21 〔已推送〕 docs: 进度——次要问题第 1–7 批已部署 EC2（8db5430）
- `a5eb554` 2026-10-21 〔已推送〕 docs(skill): 写明 latest.continued 是自动纠错接上了上一轮对话，与 octopus_continue 无关；进度记下待负责人决定的三项
- `b06b843` 2026-10-20 〔已推送〕 docs: 进度——推送前安全扫描通过（c36ebad，git-defender scan / scan-history 均无发现）
- `3ef5761` 2026-10-20 〔已推送〕 docs: OPERATIONS 写明 R10（只有边界主人下达指令、改派到 Team 的环境）与主机的无头浏览器依赖；BACKLOG 标记 R3 已裁定
- `89d3d54` 2026-10-19 〔已推送〕 docs: OPERATIONS「每人一个边界」第 2 期规则（Team 会话在提交者的边界、回退与提示、固定与改派、/v1/placement、原生入口）；MVP-RELEASE §14.37
- `edc6102` 2026-10-19 〔已推送〕 docs: BACKLOG 记录执行环境方向（每人一个边界、Team 会话在提交者边界执行，负责人 2026-10-19）、团队层知识排在 P4 验收后，以及 P4 终审延后项
- `4724b9a` 2026-10-18 〔已推送〕 docs: MVP-RELEASE 记录部署排空的端到端验收与 KiroCrew 契约后续（9f820ac）
- `b4c820f` 2026-10-18 〔已推送〕 docs: MVP-RELEASE 记录延后项第二批与 EC2 核对（87332a1）
- `8f2e49e` 2026-10-18 〔已推送〕 docs: MVP-RELEASE 记录 BACKLOG 防线项（部署维护期、HTTP 字段契约、延后项第一批）与 EC2 验收
- `5d806a4` 2026-10-18 〔已推送〕 docs: 复盘 2026-10-16 至 10-18 的 Agent Harness 事故（现象、根因、修复、防线与共性对策）；BACKLOG 记部署维护期与 KiroCrew 报错词表契约
- `d75639c` 2026-10-17 〔已推送〕 docs: MVP-RELEASE 记录 incognito 会话的关闭与删除（7d0d733、ed5b0a6）与 EC2 验收（2bde009）
- `4d98724` 2026-10-17 〔已推送〕 docs: MVP-RELEASE 记录 incognito 回归与修复、新建源码的发布提示、AcpProcessDied 的 L0 重试与 EC2 验收（d417ee2）
- `901609d` 2026-10-16 〔已推送〕 docs: M2 验收 3（已有仓库：确认 → 克隆 → 候选 → 门禁 → 审查 → 返工 → 复审）的 EC2 证据；门禁目录一行更新
- `322cd67` 2026-10-16 〔已推送〕 docs: M2 故障注入的 EC2 验收（Gateway 重启与失败测试在预算内自动恢复并完成）
- `e7225b5` 2026-10-15 〔已推送〕 docs: 映射来源的 EC2 核实
- `12e98ab` 2026-10-15 〔已推送〕 docs: YOLO 常驻（crew_yolo）在 EC2 两台主机核实生效
- `c498859` 2026-10-14 〔已推送〕 docs: BACKLOG 记录 Feature 报告的审查延后项
- `f5d65b7` 2026-10-14 〔已推送〕 docs: 会话独立预览域名的运维说明——部署（通配证书的查找与申请）、主机名与 Cookie 的对应、剩余风险与回退
- `a9a8a4c` 2026-10-14 〔已推送〕 docs: M1 运行底座经负责人确认验收（2026-10-14）；下一步：会话独立预览域名，其后 M2 候选与分支、命名门禁与 Feature 报告
- `0b424cb` 2026-10-14 〔已推送〕 docs: BACKLOG 记录远程预览第 2 期、仓库凭证第 1 / 2 期审查延后项与升级依赖管理员密码的问题
- `2a872eb` 2026-10-13 〔已推送〕 docs: 下载源码的 .git 清理、试算与跳过规则
- `7627ecd` 2026-10-13 〔已推送〕 docs: 访问管理第 3 期审查修复后的验收记录
- `3c1881d` 2026-10-13 〔已推送〕 docs: 访问管理第 3 期 EC2 验收记录；BACKLOG 记录 Token 列表 scopes 格式不一致
- `1f44ffc` 2026-10-13 〔已推送〕 docs: 访问管理第 3 期——Skill（新工具、结构化错误、管理员 Token）、CONNECT、发布记录与 BACKLOG
- `36a7df6` 2026-10-13 〔已推送〕 docs(plan): 访问管理第 3 期——MCP 与角色调用
- `dd17611` 2026-10-12 〔已推送〕 docs: 访问管理第 2 期 EC2 验收记录（28/28，不执行任务）；控制台改管理员密码后须同步主机密码文件
- `b57cba1` 2026-10-12 〔已推送〕 docs: BACKLOG 记录访问管理第 2 期审查延后项
- `638e4e6` 2026-10-12 〔已推送〕 docs(plan): 访问管理第 2 期——Team 与 Worker 管理、派发前核对、管理操作审计

### 未标记 · 测试（5 个提交：已推送 5）

- `02f44e3` 2026-10-18 〔已推送〕 test(kirocrew): HTTP 字段契约——平台发给 KiroCrew 的每个请求体只许用其处理函数读取的键（tests/kirocrew_contract.json http：spawn、chat slots 及 context / note / resume / approve、stop-all）；本机回归以 AST 扫描 Worker 的全部请求体，部署时原生测试核对已安装 KiroCrew 的处理模块仍读取这些键；OPERATIONS 与 BACKLOG 更新
- `34d6443` 2026-10-18 〔已推送〕 test(kirocrew): 报错词表契约——平台 L0 自动重试依赖的 KiroCrew 报错文字（取消、ACP 进程退出、AcpProcessDied、进程组清理失败）列入 tests/kirocrew_contract.json 并附 EC2 原样报错；本机回归核对每条按其种类重试，部署时原生测试核对已安装的 KiroCrew 仍含这些文字
- `d138dec` 2026-10-17 〔已推送〕 test(kirocrew): 配置契约——平台写入 config.json 的键列在 tests/kirocrew_contract.json（版本与 versions.env 一致）；本机回归核对 seed() 只写清单内的键，部署时原生测试在主机上核对清单中的键都是已安装 KiroCrew 的配置字段，并用 KiroCrew 自己的加载器读回新配置、不得有被忽略的键。seed() 去掉 KiroCrew 不读的 dashboard.host / port 与 workspaces.default.path（EC2 上加载器如实报出，连同旧的 memory.enabled），去掉随之无用的 port 参数；HTTP 字段契约等后续记入 BACKLOG
- `bd552c4` 2026-10-16 〔已推送〕 test(credentials): 测试中固定 github.com / gitlab.example.com 的解析结果——本机代理的 fake-IP（198.18.0.0/15）不再使凭据测试被 refused_address 拒绝
- `fd9f1f4` 2026-10-12 〔已推送〕 test(access): 权限矩阵覆盖 Team 与 Worker 管理；OpenAPI 记录第 2 期接口与 admin scope

### 未标记 · 其他（1 个提交：已部署 EC2 1）

- `95e5aa9` 2026-10-21 〔已部署 EC2〕 perf(runs): 会话列表的 view=list——任务只带 ID、状态、名称、工具、类别与角色，规划不带补全的需求；React 控制台（每 10 秒）与 MCP 的列表改用它，会话页照旧取完整会话（EC2 上任务说明占完整列表的三分之二）；旧版 /classic/ 不变（M3-5）
