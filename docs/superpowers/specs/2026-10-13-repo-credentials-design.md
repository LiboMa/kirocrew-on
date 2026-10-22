# 代码仓库凭证（D35）

## 1．目标

1. 个人、Team 与平台各自管理 Git 托管凭证（GitHub、GitLab 含自建实例），用于私有仓库的拉取、推送分支与创建 PR / MR，参照 Jenkins Credentials 的作用域与「按 ID 引用」方式。
2. 工作流可以声明需要的凭证（槽位）并给默认值；每个会话固定记录**创建者**与**所用凭证**，Team 与个人都能拥有使用自己凭证的会话。
3. 凭证秘密不出现在 API 响应、Prompt、日志、工作区（含 `.git/config`）、事件、变更记录与审计中；每次使用都可审计。

成功标准：私有仓库（如 `run-63b33288…` 因缺少凭证失败的那个）在控制台选好凭证即可克隆；会话页写明「创建者 alice · 凭证：alice 的个人 GitHub Token（…1a2b）」；平台代为推送 `octopus/<run>` 分支并创建 Draft PR / MR；删除或停用凭证后，后续拉取与发布立即被拒绝。

## 2．已确定（负责人，2026-10-13）

| 编号 | 决定 |
|---|---|
| R1 | 绑定方式：**混合**——工作流声明凭证槽位并可给默认值；提交时解析为具体凭证（默认值在提交者有权使用时生效，否则按仓库自动匹配或请用户选择）；会话固定记录结果 |
| R2 | 写操作：**默认由平台代执行**（推送与 Draft PR / MR，写凭证不进入执行环境）；单条凭证可标「允许注入」，只在专属环境中交给 Agent 直接使用 |
| R3 | 第一版类型：GitHub fine-grained PAT、GitLab Token（Personal / Project / Group，含自建实例）、GitHub App（组织安装） |
| R4 | 个人凭证可以用于 Team 会话；会话记录凭证归属，续作沿用 |
| R5 | 修订 GitHub Connector 草案（`2026-10-11-github-connector-design.md`）的 C1：GitHub App 用于组织，PAT 与 GitLab Token 作为正式类型；C4（读写分离）保持为默认；草案的 App 设计并入本设计第 3 期 |

## 3．凭证

### 3.1 字段

| 字段 | 说明 |
|---|---|
| `id`、`name` | 引用用的 ID 与显示名称 |
| `kind` | `github_pat` \| `gitlab_token` \| `github_app` |
| `host` | `github.com`，或 GitLab 实例地址（如 `gitlab.example.com`）；只接受 HTTPS |
| `scope` | `user`（个人）\| `team` \| `platform` |
| `owner` | 个人为用户 ID，Team 为 Team ID，平台为空 |
| `repos` | 可用仓库范围：`owner/repo`、`owner/*`、`group/sub/*`；空表示该主机全部 |
| `access` | `read` \| `write`（write 含 read） |
| `inject` | 是否允许注入专属环境（R2，默认否） |
| `hint` | 秘密末 4 位，仅用于辨认 |
| `expires` | 到期时间（创建时填写或经平台测试读出）；到期前 7 天在控制台提示 |
| 其他 | `created_by`、`created`、`last_used`、`disabled` |

秘密**只写**：创建或轮换时提交，之后任何接口都只返回上述元数据。

### 3.2 作用域与权限（对应 Jenkins 的 System / Folder）

| 作用域 | 管理（创建、修改、轮换、删除） | 可使用 |
|---|---|---|
| 个人 | 本人 | 本人的个人会话，以及本人提交或绑定的 Team 会话（R4） |
| Team | Team owner、平台管理员 | 该 Team 的会话 |
| 平台 | 平台管理员 | 被授权的 Team（其会话）与用户 |

- 看不到的凭证返回 404，能看到但不能管理返回 403（沿用访问管理第 1 期语义）。
- 停用或删除立即生效：已绑定的会话在下一次拉取或发布时得到 409 `credential_unavailable`，可改绑。
- 轮换秘密不改变 ID，已绑定的会话继续使用新秘密。

### 3.3 存储

- 平台库中保存密文：AES-256-CBC 加密（主机 `openssl`），HMAC-SHA256 校验完整性（先加密后认证），每条独立的随机 IV；主密钥 `/etc/kirocrew-on/credentials.key`（`root:octopus 0640`，与 GitHub App 私钥同等保护，不放实例角色可读的位置）。不新增 Python 依赖。
- 解密只发生在平台进程中、使用前的一刻，明文不落盘、不写日志；交给 Worker 的只有本次需要的那一枚（§5）。
- 备份包含密文；恢复到另一台主机需要同一主密钥（写入运维手册）。

## 4．工作流槽位与会话绑定（R1、R4）

- 工作流（及其版本）可声明槽位：`credentials: [{"slot": "source", "host": "github.com", "access": "write", "default": "<凭证 ID>"}]`。第一版只有 `source`（会话的源码仓库）一个槽位；默认值只是引用，工作流共享出去后，其他人只有在自己有权使用该凭证时才会用到它。
- 提交时解析 `source`：
  1. 提交请求明确给出 `credentials: {"source": "<ID>"}`（须有权使用）；
  2. 否则用工作流的默认值（提交者有权使用时）；
  3. 否则按仓库主机与路径自动匹配可用凭证：Team 会话依次为 Team 凭证、授权给该 Team 的平台凭证、提交者的个人凭证；个人会话依次为个人凭证、授权给本人的平台凭证。`repos` 更具体者优先；仍有多个时返回 409 `credential_ambiguous` 并列出候选，由用户选择；
  4. 公开仓库可以不绑定；需要推送时要求 `write` 凭证。
- 会话快照与视图记录绑定：`{"source": {"id", "name", "kind", "scope", "owner", "hint", "bound_by", "at"}}`；控制台会话页显示创建者与凭证；绑定与改绑写入变更记录（D34）。
- 续作沿用绑定：Team 中任何有权操作该会话的成员续作时都使用已绑定的凭证（个人凭证的所有者在绑定时已同意），每次使用在变更记录中记下操作者。
- 改绑：`POST /v1/runs/<id>/credentials {"source": "<ID>"}`，由会话创建者、Team owner 或平台管理员执行（须有权使用新凭证）；个人凭证的所有者可随时把自己的凭证从会话撤下（之后需要改绑才能继续拉取或发布）。

## 5．使用

### 5.1 拉取（准备与续作时的 fetch）

- 平台把本次凭证交给 Worker 的 `/prepare`（`git_auth`：类型、主机与秘密；GitHub App 为当场签发的单仓库、只读、1 小时 Token）。
- Worker 用 `GIT_ASKPASS` 读取 Attempt 目录内 0600 的临时文件完成 clone / fetch，随即删除；remote 保持不含凭证的 `https://<host>/<repo>.git`；工作区的 push 地址设为不可用（`DISABLED_NO_PUSH`），Agent 无法直接推送（注入模式除外）。
- git 报错写入错误信息前去除可能的凭证片段。

### 5.2 发布（默认：平台代执行）

- Agent 只在工作区提交 commit。发布由所有者在控制台点「发布」，或工作流的 `deliver` 类任务验收通过后自动发起（工作流可选）。
- Worker 把 `octopus/<run>`（或指定分支名）导出为以克隆基线为前置的 bundle；平台在自己的临时裸仓库中核对并推送单个 ref（不推默认分支、不 force push），再以 REST 创建 Draft PR（GitHub）或 Draft MR（GitLab）；结果（分支、PR / MR 链接）写入会话与变更记录。
- 写凭证只在平台进程中使用，不进入执行环境。

### 5.3 注入（可选，R2）

- 条件：凭证 `inject=true`、`access=write`，且会话的执行环境是专属环境（个人专属或 Team 专属主机）；共享环境一律拒绝（409 `credential_injection_unsafe`）。
- 方式：每个 Attempt 开始时 Worker 在工作区写入只对该仓库生效的 git credential helper，读取 Attempt 目录内 0600 文件，Attempt 结束即删除；`gh` / `glab` 需要的环境变量在 KiroCrew spawn 支持时注入，不支持时 PR / MR 仍由平台创建（实施时核实）。
- 风险说明（控制台在绑定时显示）：注入期间，同一执行环境中所有会话的 Agent 都能读取这枚凭证；把个人凭证注入 Team 专属主机时，该 Team 其他会话的 Agent 也能读取。

## 6．接口

| 接口 | 权限 | 说明 |
|---|---|---|
| `GET /v1/credentials` | read | 本人可使用与可管理的凭证（元数据），可按 `host`、`scope` 过滤 |
| `POST /v1/credentials` | manage（平台作用域需 admin） | 创建；请求体含秘密 |
| `PATCH /v1/credentials/<id>` | manage / admin | 改名、仓库范围、用途、注入、停用；轮换秘密 |
| `DELETE /v1/credentials/<id>` | manage / admin | 删除（已绑定的会话见 §3.2） |
| `POST /v1/credentials/<id>/test` | manage | 以主机 API 验证（GitHub `GET /user` 或仓库可见性，GitLab `GET /api/v4/user`），返回账号、权限与到期时间，不回传秘密 |
| `POST|DELETE /v1/credentials/<id>/grants` | admin | 平台凭证授权给 Team 或用户 |
| `POST /v1/runs`（`credentials`）、`POST /v1/runs/<id>/credentials` | execute | 提交时指定、改绑 |
| `POST /v1/runs/<id>/publish` | execute | 发布（§5.2） |

- MCP：`octopus_credentials`（列出可用凭证，无秘密）；`octopus_start` / `octopus_continue` 接受 `credentials`；`octopus_publish`。**MCP 不创建凭证、不接受秘密**（与 B7 同理，秘密不进入 Agent 对话）。
- 控制台：设置 → 「代码仓库凭证」（个人、各 Team、平台三栏；创建表单的秘密输入框只写）；新建开发向导的源码一步按仓库自动选好凭证并可更换；会话页显示创建者、凭证与「发布」。

## 7．安全

- 威胁模型沿用 Connector 草案 §4：执行环境内所有 Agent 同一 OS 用户；默认只有拉取用的凭证进入执行环境，且只在 clone / fetch 的那一刻。
- 防混淆代理：解析、拉取、发布每一步都校验「这个用户（或这个会话）能否使用这条凭证访问这个仓库」，`repos` 范围在平台侧强制。
- 审计：每次使用写审计 `credential.use`（凭证、会话、仓库、读 / 写、操作者），创建、轮换、授权、删除写 `credential.*`；变更记录记下绑定与使用；均不含秘密。
- 拒绝在仓库地址中嵌入凭证（现有 `normalize_source`）。

## 8．分期

| 期 | 内容 | 验收 |
|---|---|---|
| 1 | 凭证库（GitHub PAT、GitLab Token）、个人 / Team / 平台作用域与授权、加密存储、测试接口；工作流槽位与会话绑定；私有仓库拉取；审计；控制台与 MCP 列出 | 私有 GitHub 与自建 GitLab 测试仓库各一，经个人与 Team 凭证克隆；越权与停用被拒；秘密不出现在任何输出与工作区 |
| 2 | 平台代发布：推送分支与 Draft PR / MR（GitHub、GitLab） | 两个测试仓库各完成一次发布；重复发布不重复建 PR；分支被他人改动时提示冲突 |
| 3 | GitHub App（组织安装、按仓库签发短期 Token、列出仓库）、专属环境注入、到期提醒 | 组织测试仓库经 App 克隆与发布；注入只在专属环境生效 |

## 9．待实施时确认

1. KiroCrew spawn 是否支持按任务注入环境变量（影响 §5.3 的 `gh` / `glab`）。
2. 自建 GitLab 使用私有 CA 时的证书配置。
3. 发布分支命名（默认 `octopus/<run-id>`，是否允许工作流指定）。
4. 与 D34 任务契约的衔接：`deliver` 任务验收项中加入「发布到 <仓库>」时自动发布。
