# 无状态 Worker 池：会话检查点、租约与基础设施升级（设计稿）

状态：草案，待负责人与 kirocrew-on-ce 评审（2026-10-23）。本稿不改变任何现有行为；落地按第 7 节分期，每期单独评审、测试、确认后再部署。

配套 IaC（架构层，`cfn-lint` 1.58 与 `aws cloudformation validate-template` 均通过，**未部署**；Automation 文档的内容要在 SSM 里创建时才会被完整校验）：
- `deploy/aws/kirocrew-on-state.yaml`：会话状态桶（检查点、数据库副本）、KMS 密钥、CodeArtifact 依赖源、给平台与 Worker 用的托管策略。
- `deploy/aws/kirocrew-on-worker-pool.yaml`：Worker 池（启动模板 + Auto Scaling Group + 生命周期钩子 + 加入 / 排空自动化）。

## 1 目标与非目标

**目标**
1. 会话全局唯一（已是）：会话、Team、权限、共享、事件只在平台数据库里；加减 Worker 不影响任何会话与 Team 操作。
2. Worker 在**两次尝试之间无状态**：任一会话的下一次尝试可以放到池中任一台 Worker 上，换机器不丢已验收的工作。
3. Worker 可水平扩缩：用 IaC 定义池，新主机开机自动加入、缩容前自动排空。
4. 平台数据库可在主机故障后秒级 RPO 恢复。

**非目标**
- 不用 EFS / FSx 等共享 NFS（理由见 3.1）。
- 不在进行中的尝试里迁移（进程状态不可迁移）；尝试中途主机丢失时，该尝试按现有规则重试。
- 本期不迁 PostgreSQL（理由与触发条件见 3.3）。

## 2 现状（2026-10-23 EC2 只读实测）

| 项 | 实测 |
|---|---|
| 平台数据库 | SQLite（WAL），40 MB；141 个会话、795 次尝试、9,889 条事件，近 24 h 新增 980 条；用户 9、Team 6、Worker 3 |
| 锁冲突 | API 日志自 10/16 起 `database is locked` 0 次 |
| 平台数据库备份 | DLM 每日 EBS 快照保留 7 天 + 每次迁移前的副本 → 最坏丢 24 h |
| 会话与 Worker 的绑定 | `runs.worker_id` + 原生父会话 `native_parent` + 工作区路径；`assign-worker` 只在 `waiting_worker` 时可换，换到别处是「从头准备」（丢父会话与工作区） |
| 工作区 | 实测 101 MB / 155 MB，其中 95% 文件在 `node_modules`（4450/4708、5664/5778） |
| 主机本地单写者存储 | KiroCrew crew 目录（会话索引、记忆库、全文索引、`.lock` 文件）；codex SQLite（WAL，带 `-wal/-shm`）；kiro-cli `data.sqlite3`；平台引擎单实例租约 `engine.lock`（`flock`） |
| 执行用户 UID | 各主机各自分配：`crew-personal`（主机）与 `crew-w1`（w1）都是 uid 992 |
| 原生对话入口 | 每台 Worker 主机一个 CloudFront 分发 + 平台主机一个中转端口（8920–8928，共 9 个，已用 2） |
| Worker 主机 TLS | 平台私有 CA 签发（CSR 在 Worker 上生成，私钥不出主机） |
| Kiro CLI 登录 | 每台主机一次 IAM Identity Center 设备码登录（需要人在浏览器完成） |
| Worker 实例角色 | 「本机所有进程（包括 Agent）都能用这个角色」——不能放任何跨会话的密钥或数据访问权 |

已有、可直接复用的部件：候选提交（每次尝试后）、D58 `/pull/bundle`（导出会话分支 bundle）与 `/sync`（把 bundle 作为「本地同步」尝试应用）、发布 bundle 校验（`publish.receive`，100 MB 上限）、D40 已验收任务结论传给后续任务、`bootstrap-worker --spare`、`.maintenance` 维护标记、`SKIP_DIRS`（可重建目录清单）、合同 `untracked`（Task 11，不提交的交付物）。

## 3 决策

### 3.1 不用 EFS / 共享 NFS
1. 进程状态（Gateway 内存里的原生父会话、ACP Agent 子进程、预览进程与端口）不能靠文件共享迁移。
2. 单写者存储多主机共享会损坏：SQLite WAL 不支持跨主机网络文件系统（SQLite 官方说明），KiroCrew crew 目录默认单 Gateway 写入，`flock` 在 NFS 上不可靠。
3. 隔离被破坏：各主机 UID 相同（992）的不同边界会互相可读。
4. 性能：工作区 95% 是小文件，`npm install` / git / 测试每次文件操作都多一次网络往返；inotify 收不到其它客户端的改动。
5. 凭证共享：多主机刷新同一组 OAuth 令牌会互相作废。
6. 成本与故障域：EFS 标准存储 $0.30/GB·月（S3 $0.023），弹性吞吐读 $0.03/GB、写 $0.06/GB；一个共享盘成为所有 Worker 的共同故障点。

### 3.2 存储分层

| 层 | 内容 | 选型 |
|---|---|---|
| 热数据 | 工作区、`node_modules`、构建产物与缓存 | Worker 本地盘：EBS gp3（$0.08/GB·月）；检查点落地后可换 m8gd 等本地 NVMe 机型（950 GB，随实例免费） |
| 会话持久状态 | 每次尝试后的会话分支 git bundle、合同 `untracked` 交付物包、清单 | S3 会话状态桶（版本控制、KMS、按空间分前缀、生命周期）；**只有平台能读写** |
| 共享依赖缓存 | npm、PyPI（以后可加 Maven） | CodeArtifact（代理公共源），Worker 只读 |
| 主机本地状态 | 系统、各智能体家目录与凭证、KiroCrew crew 目录 | 本机 EBS 根卷（同现在） |
| 平台数据库 | SQLite | 平台主机 EBS + Litestream 持续复制到 S3（`db/` 前缀） |

### 3.3 数据库：保持 SQLite + Litestream
- 现规模（40 MB、日增约 1k 事件、0 锁冲突、单引擎写入）SQLite 绰绰有余；Worker 不直连数据库，租约在单引擎里用 `BEGIN IMMEDIATE` 即可原子完成。
- 真正的缺口是 RPO 24 h：加 Litestream（WAL 持续复制到 S3，默认 1 s 同步）→ RPO 秒级，平台主机坏了可在新主机从 S3 恢复。
- 迁 PostgreSQL 的触发条件（任一）：平台层要多副本 / 不停机替换；要求跨可用区自动切换；实测锁等待或写延迟；需要重报表。届时选 Aurora PostgreSQL Serverless v2（$0.12/ACU·h，最低 0.5 ACU）或 RDS PostgreSQL db.t4g.medium Multi-AZ（$0.129/h）。
- 现在就做的准备：新代码不再引入 SQLite 专有写法（统一 `INSERT … ON CONFLICT`）；引擎选主锁抽象成接口（现 `flock`，将来 advisory lock）。

## 4 P2：平台设计（会话可移动）

总开关 `session_mobility`（平台配置，默认关）。关时行为与现在完全一致（会话粘在一台 Worker 上）。

### 4.1 会话租约
- `runs.worker_id` 含义改为「当前租约持有者」；新增 `runs.lease_epoch INTEGER NOT NULL DEFAULT 0`。
- 只有引擎写租约，在 `BEGIN IMMEDIATE` 事务里：换持有者时 `lease_epoch += 1`、清空 `native_parent`、`workspace` 改为「待重建」。
- 平台对 Worker 的每个会话级调用带 `X-Lease-Epoch`；Worker 在 `<run>/lease.json` 记下见过的最大值，**收到更小的值一律 409 `lease_stale`**（旧持有者恢复后不能再写）。
- 何时可以换持有者：会话没有进行中的尝试（`ready` / `waiting_worker` / `blocked` / `completed` / `awaiting_acceptance`），且最近一次检查点已成功；或持有者失联（健康探测连续失败超过阈值，现有「Worker 重启：再试一次，然后失败」逻辑之上）。
- 放置策略：沿用现有空间规则（个人边界 / Team 环境），在可用 Worker 中选负载最低的；个人边界上的会话不进池（D49 R10：放到某人的边界上即以其身份运行）。

### 4.2 检查点
- **时机**：每次候选提交被接受后（`task_candidate` 成功、门禁判定后）；会话结算时（completed / blocked）再补一次。
- **内容**：
  - `branch.bundle`：会话分支自基线到当前候选的完整 bundle（复用 D58 `pull_bundle` 的导出，`full=1`）。
  - `untracked.tar.zst`：合同 `untracked` 路径的交付物（Task 11），先按 `SECRET_RULES` 打码扫描，命中走安全阻塞。
  - `manifest.json`：`{run_id, seq, epoch, base, head, branch, bundle_sha256, untracked_sha256, created}`。
- **传输与存放**：Worker 只把 bundle 流给平台（同 `/publish/bundle` 的限长与校验）；**平台**校验后用自己的角色写 S3：`s3://<state>/checkpoints/<space>/<run_id>/<seq>-<head12>/…`（`<space>` = `user-<id>` 或 `team-<id>`）。Worker 实例角色对状态桶**没有任何权限**（实例角色本机所有进程可用，见第 2 节）。
- **大小**：沿用 100 MB 上限；超过则该会话不可移动（标记 `mobility: pinned`，提示原因），仍按现在的粘性方式运行。
- **保留**：同一会话保留最近 5 个检查点；会话删除时删除其前缀；桶生命周期另有兜底（30 天转低频、旧版本 30 天过期）。

### 4.3 在新 Worker 上重建
1. `/prepare`：按会话来源克隆基线（现有逻辑），得到同一个基线提交。
2. 新接口 `POST /restore`（带 `X-Lease-Epoch`）：平台从 S3 取最新检查点流给 Worker；Worker 校验 bundle 完整、基于同一基线、终点等于清单 `head`，`fetch` 到会话分支并检出；解开 `untracked`；写 `lease.json`。
3. 依赖不在检查点里：首次检查或预览时按 `preview.json` 的 `install` / 项目锁文件安装，走 CodeArtifact（Worker 环境里写 `npm config registry`、`pip.conf`），可选再加「按锁文件哈希的 `node_modules` 压缩包」缓存（平台写 S3，第二期）。
4. 新原生父会话：下一次派发照常新建父会话；D40 把已验收任务的结论、候选与检查交给它。旧主机上的原生对话历史不迁移（控制台照旧能看平台保存的结果与交接）。

### 4.4 预览、下载、发布
- 预览进程只在租约持有者上；换持有者时应用状态记为 `stopped`，用户下次打开预览时在新持有者上按 `preview.json` 重启（预览本来就要能从零启动）。
- 下载、发布、拉取（D58）都在持有者上执行，内容与检查点一致。

### 4.5 失败与边界情况
| 情况 | 处理 |
|---|---|
| 尝试进行中持有者失联 | 尝试按现有规则判 blocked 并重试一次（不占纠错轮次）；新持有者从最近检查点重建；该尝试中途的改动丢失 |
| 旧持有者恢复 | 所有会话级调用因 `lease_stale` 被拒；其上的旧工作区由 Worker 的维护任务按 `lease.json` 清理 |
| 检查点上传失败 | 尝试仍按门禁判定；会话标记「最近检查点缺失」，下次结算重试；缺失期间不移动 |
| 重建后 `head` 不一致 | 拒绝移动，会话回到原持有者（若在）或进入等待环境，提示原因 |
| 检查点内容命中疑似密钥 | 与候选、预览同一安全阻塞（D57），不上传 |

### 4.6 接口与数据变更
- 迁移：`runs.lease_epoch`、`runs.checkpoint`（最近清单 JSON）、`runs.mobility`（`mobile` / `pinned` + 原因）。
- Worker：`POST /checkpoint/bundle`（导出，复用 `pull_bundle`）、`POST /restore`（应用）、所有会话级接口校验 `X-Lease-Epoch`。
- 事件：`run.checkpoint`、`run.lease_moved`（含旧 / 新 Worker、epoch、原因）。
- 配置：`state_bucket`、`state_kms_key`、`session_mobility`。

### 4.7 测试
- 单元：租约递增与 409、清单校验、bundle 基线 / 终点校验、`untracked` 打码扫描。
- 集成：测试里起两个本地 Worker（现有夹具），把一个会话在两次尝试之间从 A 移到 B，比对工作树与分支完全一致；A 恢复后写入被拒。
- EC2 端到端：w1 + 池中一台主机，真实会话跨主机继续。

## 5 P1：基础设施（IaC）

### 5.1 状态栈 `kirocrew-on-state.yaml`
- `StateKey`（KMS，自动轮换）、`SessionStateBucket`（版本控制、SSE-KMS + Bucket Key、禁止公开、只允许 TLS、生命周期）。
- CodeArtifact：域 `kirocrew-on`，仓库 `npm-store`（上游 npmjs）、`pypi-store`（上游 PyPI）、`packages`（以前两者为上游，Worker 只用它）。
- 托管策略：`PlatformStateAccess`（平台主机角色挂载：读写 `checkpoints/*`、`db/*`，用 KMS 密钥）、`WorkerPackagesRead`（Worker 角色挂载：CodeArtifact 只读）。

### 5.2 Litestream（平台主机）
`al2023.sh` 装固定版本的 Litestream 并写 systemd 单元，复制 `octopus.sqlite3` 到 `s3://<state>/db/`；平台主机角色挂 `PlatformStateAccess`。恢复流程写进 OPERATIONS（新主机 `litestream restore` 后再启动 API）。

### 5.3 Worker 池栈 `kirocrew-on-worker-pool.yaml`
- 启动模板：按参数取 AL2023 AMI（默认 arm64，Graviton），IMDSv2，加密 gp3 根卷（池主机可丢弃：`DeleteOnTermination: true`），实例角色同 w1（Bedrock、读发布包）+ `WorkerPackagesRead`，**无状态桶权限**。
- Auto Scaling Group：默认 `Min=0 / Desired=0`（部署本身不起任何实例），`Max` 默认 4（见 5.4）。缩容不靠实例保护，而靠终止钩子：每次心跳最长 2 小时，更长的排空由排空步骤记录心跳续期（P1b 实现）。
- SSM Automation 只能对带 `kirocrew-on-pool=<栈名>-pool` 标签的实例和平台主机执行命令；命令只带实例 ID、IP、发布版本等标识；Worker Token 由脚本自己交接（例如经 KMS 加密的 SSM 参数），不经 Automation 输出（执行记录对有读权限的人可见）。
- 生命周期钩子：
  - 启动：EventBridge → SSM Automation「加入」：取平台当前发布 → 在新主机安装（`WORKER_HOST=1`，同 `worker_push`）→ 生成 CSR → 平台 CA 签发 → 安装证书 → 以 `--spare` 登记 → 完成钩子；任一步失败则 `ABANDON`（实例被回收）。
  - 终止：EventBridge → SSM Automation「排空」：平台把该 Worker 标为排空（不再放置新尝试）→ 等其上会话结算（P2 之后：检查点并移走）→ 退役登记 → 完成钩子。
- 两个 Automation 文档调用的脚本是 P1 的实现项（模板里已写明契约）：主机侧 `deploy/install/pool-host.sh <join|csr|install-cert>`，平台侧 `python -m octopus pool <sign|admit|drain|retire>`。

### 5.4 原生对话入口（池化前必须改）
现在每台主机一个 CloudFront 分发 + 平台一个中转端口：创建分发要数分钟，端口只剩 7 个，不适合自动扩缩。方案：池共用一个原生分发 → 平台 nginx 按 `/w/<worker-id>/` 路由到对应主机的 Gateway TLS 端口（路由表由「加入 / 排空」维护）。**待确认**：KiroCrew Gateway 是否支持在子路径下运行（否则改为按 Host 头路由 + 通配证书）。在此之前池主机不提供「在 KiroCrew 中打开」，模板的 `MaxSize` 默认 4。

### 5.5 Kiro CLI 登录（自动扩容的阻塞项）
设备码登录需要人参与，自动加入的主机无法完成。选项：
- A（推荐，先做）：池主机只承接 Bedrock 驱动的工具（Claude Code、Codex、OpenCode，走实例角色，无需登录）；需要 Kiro 的任务放置时优先固定主机（w1 / w2）。
- B：与 Kiro 团队确认非交互登录方式（服务账号 / 令牌下发），确认前不采用。
- C（不推荐）：经 Secrets Manager 下发同一组刷新令牌——多主机刷新会互相作废。

### 5.6 Graviton 支持（w2 与池的前提）
`al2023.sh` 按 `uname -m` 选 Node（`linux-arm64`）与 kiro-cli（`aarch64`）；`deploy.sh` 新建 Worker 主机时按实例类型的架构选 AMI，已有主机沿用其栈里的 AMI（避免 `worker-host up` 把它换成另一架构而重建）。已核实：kiro-cli 2.33.1 有 `kirocli-aarch64-linux.zip`，Node v22.23.3 有 `linux-arm64`，us-east-1a 有 m8g / m8gd / r8g 等 64 GiB 机型。

## 6 安全
- 检查点只经平台读写；Worker 实例角色无状态桶权限；按空间分前缀，审计里记录谁触发移动。
- 检查点与候选同样经过拦截扫描（`SUSPICIOUS`）与打码扫描（`SECRET_RULES`）；`.env` 等 `left_out` 路径从不进入检查点。
- KMS 加密、只允许 TLS、禁止公开；会话删除即删除检查点。
- 池主机按 Team 分配（同 w1），不同 Team 的会话不共用一台主机，除非管理员显式共享该环境。

## 7 分期与上线顺序
| 期 | 内容 | 行为变化 | 回滚 |
|---|---|---|---|
| P0 | Graviton 支持；加 w2（m8g.4xlarge，EBS gp3），分配给 Team | 多一台可用环境 | 退役 w2 |
| P1a | 部署状态栈；平台主机装 Litestream | 数据库 RPO 24 h → 秒级 | 停 Litestream |
| P1b | 实现加入 / 排空脚本；部署池栈（Desired=0）→ 用 Desired=1 验证加入与排空 | 可按需扩出空闲环境（仅 Bedrock 工具） | Desired=0 |
| P1c | 原生入口改为池共用分发 + 路由 | 池主机可「在 KiroCrew 中打开」 | 回到每主机分发 |
| P2 | 租约、检查点、重建（`session_mobility` 默认关，逐步打开） | 会话可在两次尝试之间换 Worker | 关开关 |
| P3 | 池换本地 NVMe 机型（m8gd）；逐步把固定主机并入池 | 更快的工作区 | 换回 gp3 |

## 8 成本（us-east-1 按需价，2026-10-23 Pricing API）
- m8g.4xlarge $0.718/h（约 $524/月）；m8gd.4xlarge $0.923/h（约 $674/月，含 950 GB NVMe）。
- S3 $0.023/GB·月：检查点只含 bundle 与交付物（不含 `node_modules`），体积远小于工作区（未实测 bundle 大小）。
- CodeArtifact：按存储与请求计费，主要是缓存的包体积。
- Litestream：S3 写请求为主，量级很小。

## 9 待评审的决定
1. 是否接受「两次尝试之间无状态」作为目标（进行中的尝试不迁移）。
2. Kiro 登录走 5.5 的哪个选项（推荐 A）。
3. 原生入口改为池共用分发的路由方式（子路径还是 Host 头），需要 kirocrew-on-ce 确认 KiroCrew 是否支持子路径。
4. 检查点保留数量（默认 5）与 100 MB 上限是否合适。
5. P0–P3 的顺序与每期的验收方式。
