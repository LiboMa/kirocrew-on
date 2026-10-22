# M3-2 用户 CLI 与 M3-4 本地 / 远端联动（D58，设计稿）

日期：2026-10-22。负责人：「继续完成 M3、M4、M5 的任务，尽可能自动，不要停下来」。范围是看板上的 M3-2、M3-4，以及 M3-3 剩下的「会话关联」。
另一会话的「Harness」项目（同一工作单元既能本地做、也能随时交给远端）将在 D57 之后以本设计为基础对齐，不另做一套联动。

## 1. 现状与差距

MVP-RELEASE §7 第 2、4 项与验收 1：「四个客户端各自完成：connect → submit → status → pull → 本地修改并推送 → sync → 远端复审通过」。

| 要求 | 现在 | 差距 |
|---|---|---|
| 用户 CLI（login、submit、plan、confirm、status、watch、report、pull、sync、continue、approvals、search） | `octopus` 只有运维命令；使用者经 MCP 与控制台操作 | 没有给使用者的命令行 |
| pull：把会话分支与上下文拉回本地 | `octopus_download` 解压一份工作区副本（含 .git）到新目录 | 不能拉进使用者已有的本地仓库，也没有上下文目录 |
| sync：远端快进到本地提交，重新门禁与审查 | 没有；本地改动只能经续作的文字说明交给 Agent | 远端看不到本地提交 |
| 未推送提交检测与 `--push-base` | 没有；本地有未推送提交时，远端克隆不到 | — |
| 会话关联 `.octopus/session.json` | 没有 | 每条命令都要写会话 ID |

## 2. 原则（我的取舍）

1. **本地与远端之间用 git bundle 交换提交，不经过代码托管方。** pull 下载会话分支的 bundle，sync 上传本地提交的 bundle。这样不要求使用者有源仓库的写权限，新建项目（没有远端仓库）也能用；执行环境也不需要源仓库的写凭证。发布（推送到托管方、建 PR）仍按 D35 / D47。
2. **同一套逻辑给 CLI 与 MCP。** 本地的 git 操作写在一个模块里（`octopus/local.py`），CLI 命令与 MCP 工具都调用它；MCP 服务本来就在使用者本机运行。
3. **sync 复用现有门禁与审查。** 本地提交成为会话的新候选，由平台为最后一个写代码的任务记一个「本地同步」Attempt，运行它的检查命令（checks_pass）；它之后的任务重新执行，审查绑定新的候选（review_bound）。这样发布、报告与修订历史都不必另写。
4. **安全底线不变。** bundle 里的提交与发布同样核查疑似凭证与 `.env`（`unpublishable`），不通过就拒绝；pull 同样核查，避免把会话中的秘密带回本地。

## 3. 会话关联

- 仓库根目录的 `.octopus/session.json`：`{"run_id", "request_id", "url", "branch", "head", "updated"}`。由 submit、pull 写入，由 status、watch、report、continue、sync、approvals 等默认读取。
- 它与 `.octopus/sessions/<run-id>/`（pull 拉回的上下文：报告、计划、各任务的交接摘要）都是个人的：写入 `.git/info/exclude`，不进入提交。`.octopus/preview.json` 照旧可以提交。
- MCP `octopus_link {run_id, dir?}` 把本地仓库与一个会话关联；`octopus_status` 等在缺 `run_id` 时读取关联。

## 4. submit：从本地仓库提交

- 在 Git 仓库内提交时，源码默认取 `origin` 的地址与当前分支（`ref`）；不在仓库内或 `--new` 时为新建项目。
- **未推送提交检测**：当前分支在 `origin` 上不存在，或本地比 `origin/<分支>` 多出提交时，远端克隆不到这些提交。CLI 拒绝提交并列出提交数；`--push-base` 先执行 `git push -u origin HEAD`（使用者自己的凭证），再提交。也可以 `--allow-unpushed` 按远端已有的提交提交。
- 提交成功后写 `.octopus/session.json`。

## 5. pull

- 平台 `POST /v1/runs/<id>/bundle` 返回一次性下载链接（与归档下载相同的票据与 10 分钟有效期）；`GET /bundle/<票据>/<id>.bundle` 由 Worker 生成会话分支的 bundle：Git 来源的会话为「基线..分支」，新建项目为完整分支；`full=1` 时总是完整分支（本地缺基线提交时用）。
- 本地：`git fetch <bundle> octopus/<run>:refs/heads/octopus/<run>`，不切换当前分支（`--checkout` 时切换）。已存在且有本地提交的同名分支不覆盖，提示改用 sync。
- 同时写 `.octopus/sessions/<id>/`：`report.md`（Feature 报告）、`plan.json`（需求、任务与各任务的验收、检查）、`handoffs/<task>.md`（各任务最近一次的交接摘要）。
- 核查：分支上有疑似凭证或 `.env` 的提交时，平台拒绝生成 bundle（409 `bundle_secret_found`，列出文件）。

## 6. sync

- 本地：当前分支须包含会话分支的当前提交（`session.json` 的 `head`，或 `--from`）；CLI 生成 `<会话 head>..HEAD` 的 bundle（不超过 20 MB）上传。工作区有未提交的改动时拒绝，提示先提交。
- 平台 `POST /v1/runs/<id>/sync`（`application/octet-stream`，查询参数 `request_id`、`expected_version`、`head`，可选 `note`）：
  1. 会话须为可续作的状态（与续作相同：结束或停止后、未关闭、未归档的 Team；D49 R10 的边界主人规则相同）。
  2. Worker `/sync`：在会话工作区导入 bundle，核对它只是会话分支的**快进**（不改写历史），核查疑似凭证与 `.env`，移动会话分支并把工作区置为新提交；返回与候选相同的字段（sha、parent、文件数、增删行数、改动的文件名）。
  3. 平台为**最后一个写代码的任务**（implement / deliver；没有时为最后一个任务）记一个新的 Attempt：状态为检查中，证据含候选（新提交）、`synced`（谁、从哪个客户端、几个提交、说明）与门禁 `native_completed`（跳过：本地提交）、`diff_present`、`no_secrets`；有检查命令时由 Worker 开始运行，引擎照常轮询并判定；没有检查命令时直接验收。
  4. 它之后的任务重置为待执行（审查、测试、Evaluator 重新进行，审查绑定新候选）；之前的任务不变。
  5. 修订历史记一版「本地同步」：提交数、新 head、说明。时间线事件 `run.synced`。
- 失败：不是快进 409 `sync_not_fast_forward`（提示先 pull）；有疑似凭证 409 `sync_secret_found`；会话忙 409 `busy`；Worker 早于 D58 时 409 `worker_outdated`。

## 7. 用户 CLI

`octopus` 增加使用者的命令（运维命令不变）：

| 命令 | 作用 |
|---|---|
| `login [--url]` | 用户名与密码登录，Token 保存在 `~/.config/kirocrew-on/cli.json`（0600），标签 `octopus CLI` |
| `submit <需求> [--plan agent\|match] [--admission auto\|confirm] [--team] [--new] [--push-base]` | 提交；写会话关联 |
| `plan` / `confirm` | 查看待确认的计划（任务、工具、映射来源、检查）/ 确认 |
| `status [--detail]`、`watch` | 精简状态；跟随事件直到会话停下 |
| `report [--html]` | Feature 报告 |
| `pull [--checkout] [--full]`、`sync [--note]` | 见 §5、§6 |
| `continue <task> [--note]` | 续作 |
| `approvals [--answer <id> allow\|deny]` | 待批准 |
| `search [条件…] [q]` | 会话列表（D56 的全部过滤条件） |

- 输出默认是给人看的中文摘要；`--json` 输出与 MCP 相同的 JSON。
- 来源客户端：CLI 的 Token 标签 `octopus CLI`，D55 的 `client` 记为 `cli`（控制台显示「CLI」）。

## 8. MCP

新增 `octopus_link`、`octopus_pull`、`octopus_sync`（在使用者本机的仓库目录执行，参数 `dir` 默认当前目录）；`octopus_start` 在仓库内调用时同样做未推送提交检测（`push_base: true` 时代为推送，须用户同意）。Skill 写明完整的联动流程。

## 9. 不做

- 双向合并：远端在 sync 之后又有新的提交时，本地须先 pull 再 sync，平台不合并。
- 经托管方（origin）同步；推送仍只由发布完成。
- 控制台上传 bundle。
- 多个本地仓库同时关联同一会话的冲突处理（后写的生效）。

## 10. 验收

- Claude Code（MCP）与 CLI 各走一遍：submit → status → pull → 本地修改并提交 → sync → 远端检查与复审通过，会话页显示「本地同步」修订与新候选的代码改动。
- 不是快进、含 `.env` 的 sync 被拒绝；本地有未推送提交时 submit 给出提示，`--push-base` 后成功。
- `.octopus/session.json` 与上下文目录不进入提交。
