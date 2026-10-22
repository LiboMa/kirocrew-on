# 开发成果快速预览 第 2 期「浏览器内远程预览」（D36） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 能查看会话的人在浏览器中打开该会话的应用：应用在执行环境中启动（声明优先、自动识别兜底），经独立的预览分发转出，每个请求复核查看权限，支持开发服务器、前后端两个进程与 WebSocket。

**Architecture:**
- **Worker 侧**：新模块 `octopus/apps.py` 在执行环境内启动、监视、停止每个会话的应用。启动方式取自 `.octopus/preview.json` 或 `preview_spec` 的自动识别（第 1 期已有）。每个进程有独立的进程组；入口端口从 47000–47999 分配。Worker 以 `POST /app` 暴露 start / stop / status。
- **平台侧**：新表 `apps` 记录每个会话的预览（所在 Worker、端口、上游、最近访问）；Service 负责启动、停止、打开、空闲回收，并在会话关闭、删除或续作时停止预览。
- **访问链路**：浏览器访问 `https://<预览分发>/…`，经 CloudFront 到平台主机 nginx 的预览端口。nginx 以 `auth_request` 请求 API 的 `/v1/preview-auth`，由 API 校验 Cookie、签名、过期与查看权限，并返回上游。上游有两种：
  - 平台主机：回环 `127.0.0.1:<端口>`。
  - Worker 主机：私网 TLS 到该主机 nginx 的预览端口，携带由 Worker Token 派生的校验头，再转到回环 `<端口>`。
- **入口**：一次性票据 `/_kirocrew/open/<票据>` 先清除预览域上的旧数据，再写入只对预览域有效的 HttpOnly Cookie。

**Tech Stack:** Python 3 标准库（`subprocess`、`http.client`、`hmac`）、nginx（`auth_request`、WebSocket）、CloudFormation、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-13-session-preview-design.md`（§3、§5、§6 第 2 期）

## Global Constraints

- **依赖与文字**
  - 不新增 Python / npm 依赖。
  - 面向用户与 Agent 的文字使用中文。
- **预览数量与生命周期**
  - 每个会话最多一个预览；每个执行环境同时最多 3 个。
  - 空闲 30 分钟（以平台记录的最近访问时间为准）自动停止。
  - 会话关闭、删除或续作开始时停止。
  - Worker 重启后如实显示为已停止。
- **端口与进程**
  - 入口端口 47000–47999。
  - 进程最多 4 个（`preview_spec` 已校验）。
  - 命令以执行环境的 OS 用户、在项目目录内执行，每个进程独立进程组。
  - 第一版不做 CPU / 内存配额。
- **票据与 Cookie**
  - 打开链接 10 分钟内有效、一次性。
  - Cookie 只对预览域有效，HttpOnly、Secure、8 小时。
  - 同一浏览器同时只预览一个会话。
  - 每个请求复核查看权限；撤销访问或停止预览后，下一个请求即失效。
  - 预览票据、下载票据、静态预览票据互不通用。
- **请求改写与只限本机的接口**
  - `Host` 改写为 `localhost:<端口>`。
  - 预览域不设置控制台的任何 Cookie。
  - `/v1/preview-auth` 与 `/_kirocrew/*` 只接受平台主机本机 nginx 的请求：经 CloudFront 访问控制台分发时返回 404。
- **基础设施**
  - 平台主机的预览端口放在现有安全组规则的端口范围内（见 Task 5 的 Ruling），不改动引用 CloudFront 前缀列表的那条规则。
  - Worker 主机的预览端口只接受平台主机的安全组。
- **提交、部署与测试命令**
  - 在功能分支 `feat/m7-credentials-preview` 本地提交；EC2 验收并经负责人确认后推送。
  - 部署前确认没有进行中的会话。
  - Python 用 `$PY -m unittest <模块>`；控制台用 `npm --prefix web test`；完整回归用 `PYTHON=$PY tools/regress.sh`。

## Review Focus

1. **权限撤销后的旧 Cookie**：持有 Cookie 的人被移出 Team 或会话被删除后，下一次请求 `/v1/preview-auth` 返回 403，nginx 显示失效说明页（Task 2 `test_a_cookie_stops_working_when_access_is_revoked`）。
2. **经控制台分发访问本机接口**：直接经控制台分发（非本机、带源站密钥）访问 `/v1/preview-auth` 或 `/_kirocrew/open/…`，得到 404，响应中不出现上游地址与校验头（Task 2 `test_preview_auth_is_only_for_the_local_proxy`）。
3. **进程树残留**：`sh -c` → 子进程 → 孙进程的命令在停止后整棵树退出，端口释放（Task 1 `test_stop_ends_the_whole_process_tree`）。
4. **票据重用**：同一打开链接第二次使用被拒；下载票据与静态预览票据不能用作打开票据（Task 2 `test_open_tickets_are_single_use_and_purpose_bound`）。
5. **启动失败不留残局**：
   - 涵盖的情形：入口声明了固定端口、`cwd` 越界、进程启动即退出、就绪超时、安装步骤失败。
   - 期望：状态为 `failed`，错误说明具体原因（指向 `.octopus/preview.json`），不留进程，端口释放，配额不被占用。
   - 测试：Task 1 `test_failures_leave_nothing_running`。

---

### Task 1: Worker 运行会话应用（`octopus/apps.py`、Worker `POST /app`）

**Files:**
- Create: `octopus/apps.py`、`tests/test_apps.py`
- Modify: `octopus/worker.py`（`Worker.__init__` 接收 `apps`，`Worker.app(body)`，`health()` 增加 `"app_preview": True`，`serve` 的方法表加入 `/app`，退出时 `stop_all`）、`octopus/__main__.py`（把 `settings.path_prepend`、`preview_tls_port`、`preview_tls_name` 交给 Worker）、`octopus/config.py`（边界配置新增 `preview_tls_port`（int）与 `preview_tls_name`（str））

**Interfaces:**
- Consumes: `preview_spec.resolve(project) -> (spec, source, error)`、`preview_spec.entry(spec)`（第 1 期）。
- Produces:
  - `apps.PORTS = range(47000, 48000)`、`apps.LIMIT = 3`、`apps.LOG_LINES = 200`。
  - `apps.Apps(path_prepend=(), limit=LIMIT, ports=PORTS, install_timeout=900, poll=1.0)`：
    - `start(run_id, project: Path, folder: Path) -> dict`：立即返回 `{"status": "installing" | "starting", "port": int, "path": ready.path, "source": "declared" | "detected"}`，后台线程完成安装、启动与就绪检测。已在运行时返回当前状态，不重复启动。出错时抛 `Error`：
      - 409 `app_unavailable`：启动方式无效或找不到，消息来自 `resolve`。
      - 409 `app_entry_port`：入口进程的 `port` 不是 `"$PORT"`，消息为「远程预览要求入口进程的 port 为 "$PORT"（.octopus/preview.json）」。
      - 409 `app_limit`：此执行环境已有 3 个远程预览在运行。
      - 409 `app_no_port`：47000–47999 没有空闲端口。
    - `stop(run_id) -> {"status": "stopped"}`：对每个进程组先 SIGTERM，5 秒后 SIGKILL；安装中的命令同样终止。
    - `status(run_id, folder) -> {"status", "port", "path", "error", "started", "log": [最近 200 行]}`：
      - 运行中任一进程退出 → `failed`，错误为「进程 <name> 已退出（代码 N）」，并停止其余进程。
      - 不在内存中的会话（包括 Worker 重启后）→ `stopped`；日志文件仍在时也返回日志。
    - `running() -> [run_id]`；`stop_all()`。
    - 状态：`installing`（执行 `install`，按 `<folder>/app-install.json` 中的命令摘要只执行一次）→ `starting`（启动所有进程，轮询 `GET http://127.0.0.1:<端口><ready.path>`，收到任何 HTTP 响应即就绪）→ `running`；失败为 `failed`；停止为 `stopped`。
    - 进程：`subprocess.Popen(["/bin/sh", "-c", command], cwd=项目内 cwd, stdin=DEVNULL, stdout=日志, stderr=STDOUT, start_new_session=True)`。环境变量为 Worker 的环境，外加：
      - `PATH` 前置 `path_prepend`；
      - `PORT`：入口进程用分配的端口；其他进程用其固定端口，或另行分配的端口；
      - `HOST=127.0.0.1`、`BROWSER=none`。
    - 静态站点（`{"static": "."}`）：`[sys.executable, "-m", "http.server", <端口>, "--bind", "127.0.0.1", "--directory", <目录>]`。
    - 日志：`<folder>/app.log` 每次启动时清空，写入「== 安装：<命令>」「== 启动 <name>：<命令>（端口 N）」等分隔行。
  - `Worker.app(body)`：`{"run_id", "action": "start" | "stop" | "status"}`。`start` 与 `status` 的结果附 `"via"`：Worker 主机为 `{"port": preview_tls_port, "name": preview_tls_name}`，未配置时为 `None`。

- [ ] **Step 1: 写失败测试**（`tests/test_apps.py`，真实进程，`poll=0.1`，端口范围用测试自己选的空闲小范围）
  - 声明式单进程：`{"processes": [{"command": "python3 -m http.server $PORT --bind 127.0.0.1"}]}` → 很快 `running`；`urllib` 能取到项目内文件；`status` 的日志含「== 启动」。
  - 两个进程（后端固定端口、前端 `$PORT` 入口）：两个端口都在监听；入口端口在范围内。
  - `install` 只执行一次：第二次 `start` 不再写「== 安装」（日志只含一次，或由 `app-install.json` 判定）。
  - 自动识别静态站点：只有 `index.html` 的项目 → `running` 并返回该文件。
  - `test_stop_ends_the_whole_process_tree`：命令 `sh -c 'python3 -m http.server $PORT --bind 127.0.0.1 & wait'`；`stop` 后孙进程不存在（`os.kill(pid, 0)` 抛 `ProcessLookupError`，pid 由测试从 `ps -o pid= -g <pgid>` 或日志读取），端口可再次绑定。
  - `test_failures_leave_nothing_running`：以下每种情形都没有残留进程，配额不被占用：
    - 入口 `port: 3000` → 409 `app_entry_port`；
    - `cwd: "../x"` → 409 `app_unavailable`，消息含 `.octopus/preview.json`；
    - `command: "exit 3"` → `failed`，消息含「已退出（代码 3）」；
    - 不监听端口的 `sleep 30` 且 `ready.timeout: 1` → `failed`，消息含「未就绪」；
    - `install` 中 `false` → `failed`，消息含「安装步骤失败」。
  - 上限：`limit=2` 时第三个会话启动 → 409 `app_limit`；停止其中一个后可以启动。
  - 运行中进程退出（命令 `python3 -m http.server … & sleep 1; kill %1`，或测试中直接 kill 入口进程）→ `status` 为 `failed`。
  - 新的 `Apps()`（模拟 Worker 重启）对旧会话 `status` → `stopped`。
  - 经真实 `serve`：`POST /app` 的 start / status / stop；未认证 401；`health()["app_preview"] is True`；配置 `preview_tls_port` 后 `start` 的结果有 `via`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_apps tests.test_worker_approval tests.test_config tests.test_archive`）。
- [ ] **Step 5: 提交** — `feat(worker): 在执行环境中运行会话应用——按启动声明安装与启动、独立进程组、47000–47999 端口、就绪检测、最多 3 个`

### Task 2: 平台预览记录、打开票据与 `auth_request` 接口

**Files:**
- Create: `octopus/remote_preview.py`（票据、Cookie、上游组装、校验头派生）、`tests/test_remote_preview.py`
- Modify:
  - `octopus/migrations.py`（第 5 版 `apps`）；
  - `octopus/domain.py`：`Service.app` / `app_start` / `app_stop` / `app_open` / `app_enter` / `app_auth` / `sweep_apps`；在关闭、删除、续作时停止预览；
  - `octopus/server.py`：路由、`required_scope`、本机判断、`httpd.service`；
  - `octopus/__main__.py`：`serve` 启动每 60 秒一次的回收线程；
  - `octopus/config.py`：平台配置 `preview_url`；
  - `octopus/schema.py`。

**Interfaces:**
- Consumes: Worker `POST /app`（Task 1）；`journal.record`、`journal.person`；`run_access`；`preview_signature(key, payload)`、`preview_key(root)`。
- Produces:
  - 迁移 5 `apps`：`CREATE TABLE apps(run_id TEXT PRIMARY KEY REFERENCES runs(id), worker_id TEXT NOT NULL REFERENCES workers(id), status TEXT NOT NULL, port INTEGER, path TEXT NOT NULL DEFAULT '/', upstream TEXT NOT NULL DEFAULT '{}', error TEXT, started_by TEXT REFERENCES users(id), started REAL, last_access REAL, stopped REAL)`。
  - `remote_preview.verify_value(worker_token) -> str`：`hmac.new(token.encode(), b"kirocrew-preview", sha256).hexdigest()`，与 Worker 主机安装脚本的计算一致（Task 5）。
  - `remote_preview.upstream(config, port, via) -> dict`：
    - Worker 地址为回环时 → `{"url": "http://127.0.0.1:<port>", "port": port}`；
    - Worker 主机且有 `via` → `{"url": "https://<worker host>:<via.port>", "name": via.name, "verify": verify_value(token), "port": port}`；
    - 没有 `via` → `Error(409, "app_unreachable", "此 Worker 主机尚未配置预览端口：执行 deploy.sh worker-host push <名称>")`。
  - `remote_preview.open_ticket(rid, user, key, now=None)` → `"<rid>.<user>.<nonce>.<exp>.<sig>"`，签名载荷带用途前缀 `app.`。
  - `remote_preview.open_run(ticket, key, now=None) -> (rid, user, nonce)`：错误为 403 `app_link_invalid` 或 `app_link_expired`。
  - `remote_preview.cookie(rid, user, key, now=None)` → `"<rid>.<user>.<exp>.<sig>"`（用途 `app-cookie.`，8 小时）。
  - `remote_preview.cookie_run(value, key, now=None) -> (rid, user)`：错误为 401 `app_cookie_invalid`。
  - `COOKIE = "__Host-kirocrew_preview"`。
  - `Service.app(actor, rid) -> view`（read）：
    - 视图字段：`{"status", "port", "error", "log", "started", "started_by": {"id", "username"}, "last_access", "idle_stop_at", "openable"}`。
    - 有记录时向 Worker 查询状态与日志；Worker 报告已停止或失败时同步记录，并写 `app.stopped`（`reason: "worker"`）。
    - Worker 不支持 `/app`（404）时返回 `{"status": "unsupported"}`。
  - `Service.app_start(actor, rid)`：
    - 前置条件：`run_access`；会话 `lifecycle == "open"`；状态已结束（SETTLED），否则 409 `app_busy`「会话正在执行任务，完成后再启动预览」；`run_permissions(...)["resume"]`；会话已有 Worker 与工作区，否则 409 `app_unavailable`「此会话尚未准备源码，无法启动预览」。
    - 平台侧计数：同一 Worker 上运行中的记录已有 3 个 → 409 `app_limit`。
    - 调用 Worker `start`，写入记录与 `app.started`（`person(actor)`、端口、来源）。旧 Worker 返回 404 时，转为 409 `app_unsupported`「执行环境的 Worker 尚未升级，暂不支持远程预览」。
  - `Service.app_stop(actor, rid, reason="user")`：调用 Worker `stop`；记录改为 `stopped`；写 `app.stopped`。
  - `Service.app_open(actor, rid) -> {"url", "expires_in": 600}`（read）：
    - 前置条件：记录为 `running`（先向 Worker 刷新），否则 409 `app_not_ready`；`preview_url` 已配置，否则 409 `app_preview_unconfigured`。
    - 返回的链接为 `<preview_url>/_kirocrew/open/<票据>`。
  - `Service.app_enter(rid, holder, nonce)`：
    - 一次性：内存中已用过的 nonce 直到过期都拒绝，再次使用为 403 `app_link_used`。
    - 复核持有人仍能查看会话、预览为 `running`。
    - 返回 `(cookie 值, path)`。
  - `Service.app_auth(rid, holder) -> upstream`：
    - 复核持有人账号有效、能查看会话（同 `preview_file` 的做法），否则 403 `app_forbidden`；预览为 `running`，否则 403 `app_stopped`。
    - 每个会话每 60 秒最多写一次 `last_access`。
  - `Service.sweep_apps(now=None)`：停止以下预览，写 `app.stopped`，操作者为系统，原因为 `idle` 或 `session`：
    - `running` / `installing` / `starting` 且 `last_access` 早于 30 分钟；
    - 会话已关闭或删除。
  - 停止钩子：`operate` 的 `close` / `delete` 与续作在提交后调用 `_stop_app(rid, reason)`，Worker 不可达时只记日志，不阻断原操作。
  - HTTP：
    - `GET /v1/runs/<id>/app`：read。
    - `POST /v1/runs/<id>/app {"action": "start" | "stop"}`：execute。
    - `POST /v1/runs/<id>/app/open`：read。`required_scope` 按路径段判断。
    - `GET /v1/preview-auth`：不需 Token；只在本机请求（回环对端 + 回环 Host，且不带源站密钥）时处理，否则 404。读取 Cookie，成功时返回 204 与以下头：`X-Preview-Upstream`（`url`）、`X-Preview-Port`、`X-Preview-Name`（Worker 主机时）、`X-Preview-Verify`（Worker 主机时）、`Cache-Control: no-store`；失败返回 401 或 403，无正文。
    - `GET /_kirocrew/open/<票据>`：只限本机。先 `open_run` 校验（不消耗 nonce），返回 200 HTML，带 `Clear-Site-Data: "cookies", "storage"`、`Cache-Control: no-store` 与 `<meta http-equiv="refresh" content="0;url=/_kirocrew/enter/<票据>">`。
    - `GET /_kirocrew/enter/<票据>`：只限本机。`app_enter` 后返回 302 到 `path`，并设置 `Set-Cookie: __Host-kirocrew_preview=<值>; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=28800`。
    - `GET /_kirocrew/denied`：只限本机。返回 HTML 说明「远程预览未打开、已停止或无权查看：请回到控制台会话页点「打开远程预览」」，状态码为 403。
- [ ] **Step 1: 写失败测试**（`tests/test_remote_preview.py`，沿用 `tests/test_archive.py` 的 `AccessCase`；假 Worker 以 `ThreadingHTTPServer` 回答 `/app`）
  - 票据与 Cookie：
    - 格式与签名；过期；
    - `test_open_tickets_are_single_use_and_purpose_bound`：第二次 `enter` → 403 `app_link_used`；下载票据与静态预览票据用于 `open` → 403；打开票据用于下载 → 403。
  - `upstream`：回环 Worker 与 Worker 主机两种；没有 `via` → 409 `app_unreachable`；`verify_value` 与独立计算的 HMAC 一致。
  - 启动权限：
    - 成员 200；外人 404；只读 Token 启动 → 403；
    - 执行中的会话 → 409 `app_busy`；
    - 同一 Worker 第 4 个 → 409 `app_limit`；
    - 旧 Worker（假 Worker 对 `/app` 回 404）→ 409 `app_unsupported`。
  - 变更记录有 `app.started`（操作者、端口）与 `app.stopped`。
  - 打开：
    - 未就绪 → 409 `app_not_ready`；
    - 未配置 `preview_url` → 409 `app_preview_unconfigured`；
    - 成功 → 链接以 `preview_url + "/_kirocrew/open/"` 开头。
  - 经真实 `server` 的本机请求：
    - `open` 页带 `Clear-Site-Data`；
    - `enter` 返回 302，`Set-Cookie` 含 `__Host-kirocrew_preview`、`HttpOnly`、`Secure`、`SameSite=Lax`、`Max-Age=28800`；
    - 带该 Cookie 请求 `/v1/preview-auth` → 204，`X-Preview-Upstream` 为 `http://127.0.0.1:<port>`。
  - `test_preview_auth_is_only_for_the_local_proxy`：
    - 服务器配置源站密钥；以携带源站密钥的请求访问 `/v1/preview-auth` 与 `/_kirocrew/open/x` → 404，响应头无 `X-Preview-*`；
    - 非回环 Host（在 `allowed_hosts` 中的公网名）→ 404。
  - `test_a_cookie_stops_working_when_access_is_revoked`：
    - 把 bob 移出 Team 后，同一 Cookie → 403；
    - 删除会话后 → 403；
    - 停止预览后 → 403。
  - 回收：`sweep_apps(now=+31 分钟)` 停止空闲预览（假 Worker 收到 stop，记录为 `stopped`，`app.stopped` 原因为 `idle`）；29 分钟时不停止。
  - `auth` 访问后 `last_access` 更新；60 秒内重复访问不再写入。
  - 关闭会话与续作 → 假 Worker 收到 stop。
  - `required_scope`：`GET …/app` 为 read；`POST …/app` 为 execute；`POST …/app/open` 为 read。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_remote_preview tests.test_archive tests.test_preview tests.test_access tests.test_journal tests.test_migrations tests.test_server_shadowing tests.test_config`）。
- [ ] **Step 5: 提交** — `feat(sessions): 远程预览——平台记录与回收、一次性打开链接与预览域 Cookie、auth_request 每次复核查看权限`

### Task 3: MCP `octopus_app` 与 Skill

**Files:** Modify `octopus/mcp.py`、`skills/kirocrew-on/SKILL.md`；Test `tests/test_mcp_roles.py`

**Interfaces:**
- 工具 `octopus_app {run_id, action: "start" | "stop" | "status" | "open"}`：
  - `start` / `stop` → `POST /v1/runs/<id>/app`；
  - `status` → `GET`，只返回日志最后 40 行；
  - `open` → `POST …/app/open`，返回 `{"url", "expires_in", "note": "链接 10 分钟内有效、只能打开一次；打开后浏览器保持 8 小时，可直接刷新"}`。
  - 错误沿用结构化 `{status, code, error}`。
- Skill「远程预览」一节：
  - 用户想在浏览器里看到运行效果时，先 `start`，每 10 秒左右 `status` 一次，直到 `running` 或 `failed`。
  - `failed` 时把日志要点与 `.octopus/preview.json` 的修正建议告诉用户。
  - `running` 后 `open`，把链接交给用户本人；不要转发到公开渠道。
  - 看完后 `stop`；同一执行环境最多 3 个。
  - 想在本机运行时改用 `octopus_download`。

- [ ] **Step 1–4:** 测试（假 Client：四个动作的请求路径与方法；`status` 截断日志；未知动作 → ValueError；工具出现在普通 Token 的列表中）→ 实现 → 通过（`$PY -m unittest tests.test_mcp_roles tests.test_mcp_status`）。
- [ ] **Step 5: 提交** — `feat(mcp): octopus_app 启动、查看、打开与停止会话的远程预览`

### Task 4: 控制台「远程预览」

**Files:**
- Create: `web/src/features/sessions/AppPreviewPanel.tsx`
- Modify: `web/src/features/sessions/SessionView.tsx`、`web/src/features/sessions/useSessionActions.ts`、`web/src/api/hooks.ts`（`useRunApp`）、`web/src/api/types.ts`
- Test: `web/src/features/sessions/SessionView.test.tsx`

**Interfaces:**
- `useRunApp(id, enabled)`：`GET /v1/runs/<id>/app`。`installing` / `starting` 时每 3 秒刷新，`running` 时每 15 秒刷新，其余不刷新。
- 面板 `#app-preview`：
  - 显示状态（未启动 / 安装依赖中 / 启动中 / 运行中 / 失败 / 已停止 / 执行环境不支持），以及启动者、启动时间、「空闲 30 分钟后自动停止」。
  - 按钮：「启动」（`#app-start`，会话执行中或无工作区时停用并说明）、「打开」（`#app-open`，仅 `running`）、「停止」（`#app-stop`）。
  - 「最近日志」可展开，显示 200 行 `<pre>`。
- 打开方式同静态预览：点击时先打开空白标签页，再 `POST …/app/open` 并 `location.replace(url)`；被拦截时提示。
- 页头：`running` 时在「预览产出」前增加主按钮「打开远程预览」（`#run-open-app`）。静态预览保留。

- [ ] **Step 1: 写失败测试**
  - 未启动时「启动」发出 `POST …/app {"action": "start"}`。
  - `running` 时页头出现「打开远程预览」，点击后发出 `POST …/app/open`，标签页跳转到返回的链接。
  - `failed` 时显示错误与日志。
  - 执行中的会话「启动」停用并说明原因。
  - `unsupported` 时说明执行环境需要升级。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`npm --prefix web test`）。
- [ ] **Step 5: 提交** — `feat(console): 会话页「远程预览」——启动、打开、停止，状态与最近日志`

### Task 5: 部署——预览分发、平台与 Worker 主机的 nginx

**Files:**
- Create: `deploy/nginx/kirocrew-preview.conf`（平台主机）、`deploy/nginx/kirocrew-worker-preview.conf`（Worker 主机）
- Modify:
  - `deploy/aws/kirocrew-on-ec2.yaml`：参数 `PreviewPort`（默认 8929）；`PreviewDistribution`；输出 `PreviewDomain`、`PreviewUrl`。
  - `deploy/aws/kirocrew-on-worker.yaml`：参数 `PreviewTlsPort`（默认 8945）；只允许平台安全组的入站规则。
  - `deploy/aws/deploy.sh`：
    - `up` 传 `PreviewPort`；
    - `push` 传 `PREVIEW_DOMAIN`、`PREVIEW_PORT`；
    - `relay_port` 不分配 `PREVIEW_PORT`；
    - `worker_up` 传 `PreviewTlsPort`；
    - `worker_push` 传 `WORKER_PREVIEW_TLS_PORT`。
  - `deploy/aws/stack.env`：`PREVIEW_PORT=8929`、`WORKER_PREVIEW_TLS_PORT=8945`。
  - `deploy/install/al2023.sh`：
    - 平台主机：渲染预览 nginx，切换后写入 `preview_url`。
    - Worker 主机：写入 `preview_tls_port` / `preview_tls_name`；Worker 生成 Token 后渲染预览 nginx。
  - `deploy/config/*.example`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`（D36 第 2 期、§14.10）、`README.md`。
- Test: `tests/test_deploy_assets.py`、`tests/test_deploy_safety.py`

**Ruling（记入 ledger）：** 设计 §5.2 写的平台预览端口为 8917。但平台安全组只有一条引用 CloudFront 前缀列表的规则（ApiPort–NativeRelayEnd），这种规则按前缀列表条目数计入安全组规则配额。改动这条规则的端口范围，或再加一条同类规则，都可能超出配额，而且会在更新期间中断现有入口。因此预览端口取现有范围的最后一个端口 `NativeRelayEnd`（8929），中转端口改为 `NativePort+1 … NativeRelayEnd-1`（仍可容纳 9 台 Worker 主机）。代价：Worker 主机中转端口少一个。

**Interfaces:**
- **平台 `kirocrew-preview.conf`**（mode 0640 root:nginx，内含源站密钥）。占位符：`__PREVIEW_PORT__`、`__API_PORT__`、`__ORIGIN_SECRET__`。
  - `server` 监听 `__PREVIEW_PORT__`（IPv4 / IPv6）；不带源站密钥 → 403；日志只记路径。
  - `location ^~ /_kirocrew/` → `http://127.0.0.1:__API_PORT__`：`Host 127.0.0.1:__API_PORT__`，清空 `X-Origin-Verify`。
  - `location = /_kirocrew_auth`：`internal`，指向 `…/v1/preview-auth`；`proxy_pass_request_body off`；Host 同上。
  - `location /`：
    - 鉴权与上游：`auth_request /_kirocrew_auth`；`auth_request_set` 取 `$kc_upstream`、`$kc_port`、`$kc_name`、`$kc_verify`；`error_page 401 403 = @kirocrew_denied`；`proxy_pass $kc_upstream`。
    - TLS 校验：`proxy_ssl_verify on`，信任 `/etc/kirocrew-on/worker-ca.pem`，`proxy_ssl_name $kc_name`，`proxy_ssl_server_name on`。
    - 改写的头：`Host localhost:$kc_port`、`X-Preview-Port $kc_port`、`X-Preview-Verify $kc_verify`，并清空 `X-Origin-Verify`。
    - WebSocket：`Upgrade`、`Connection`（使用 `kirocrew-native.conf` 的 `$connection_upgrade`）。
    - 传输：`proxy_buffering off`，超时 3600s。
  - `location @kirocrew_denied` → API `/_kirocrew/denied`。
- **Worker 主机 `kirocrew-worker-preview.conf`**（0640 root:nginx，内含校验值）。占位符：`__PREVIEW_TLS_PORT__`、`__PREVIEW_VERIFY__`。
  - `listen … ssl`，证书同 `kirocrew-worker.conf`。
  - `map $http_x_preview_port`：只接受 `~^47[0-9]{3}$`。
  - 校验头不符 → 403；端口不符 → 404。
  - 转发到 `http://127.0.0.1:$port`：`Host localhost:$port`，清空两个预览头，支持 WebSocket，超时 3600s。
- **安装脚本**
  - 平台主机：`PREVIEW_DOMAIN` 非空时，从文件渲染预览配置，密钥不上命令行。设置 SELinux 端口；`nginx -t` 失败时删除新文件并退出。切换后执行 `toml_set platform.toml preview_url "https://$PREVIEW_DOMAIN"`。
  - Worker 主机：
    - 写入边界配置 `preview_tls_port`、`preview_tls_name = "<边界>.worker.kirocrew-on"`；
    - Worker 重启后等待 `worker.token`；
    - 以 Python 计算 `HMAC-SHA256(token, "kirocrew-preview")` 并渲染配置；
    - 证书存在时加载并 reload nginx。
- [ ] **Step 1: 写失败测试**
  - 平台模板：
    - 有 `PreviewDistribution`，源站端口为 `PreviewPort`，带源站密钥头，不缓存；
    - `PreviewPort` 位于安全组规则的端口范围内，且安全组规则数量不变；
    - 有输出 `PreviewDomain`。
  - Worker 模板：`PreviewTlsPort` 只允许平台安全组。
  - 两个 nginx 模板渲染后没有残留的 `__X__`，并含上述关键指令（`auth_request`、`proxy_ssl_verify on`、`Host localhost:`、`47[0-9]{3}`、Upgrade）。
  - 预览配置的日志格式只记 `$uri`。
  - 安装脚本：
    - 平台分支在 `PREVIEW_DOMAIN` 下渲染预览配置，失败时删除；
    - 切换后写入 `preview_url`；
    - Worker 主机分支写入 `preview_tls_*` 并在 Token 存在后渲染；
    - 源站密钥与 Token 不上命令行。
  - `deploy.sh`：
    - dry-run 的 `up` 参数含 `PreviewPort`；
    - `push` 命令含 `PREVIEW_DOMAIN=` 与 `PREVIEW_PORT=`；
    - `relay_port` 在 8920–8928 用尽时报错，不返回 8929（沿用 `test_relay_ports_stay_unique_across_worker_hosts` 的假 AWS）；
    - `worker_push` 含 `WORKER_PREVIEW_TLS_PORT=`。
  - `bash -n`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_deploy_assets tests.test_deploy_safety tests.test_config`）。
- [ ] **Step 5: 提交** — `feat(deploy): 远程预览分发——平台 nginx auth_request 与 Worker 主机预览端口（私网 TLS + 校验头）`

### Task 6: 回归与 EC2 验收

- [ ] **Step 1:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 2: 部署前检查**
  - 确认平台与 `w1` 没有进行中的会话。
  - 在平台主机上，于临时目录渲染两份 nginx 配置并执行 `nginx -t -p <临时目录> -c …`（只读检查，不加载）。
- [ ] **Step 3: 部署**
  - `deploy.sh up`：新建预览分发，栈更新不替换任何资源。
  - `deploy.sh worker-host up w1`：新增安全组规则。
  - `deploy.sh push`。
  - `deploy.sh worker-host push w1`。
- [ ] **Step 4: 验收**（不执行任务；启动预览会在执行环境中安装依赖并运行项目自身的命令）
  - 平台主机与 `w1` 上各选一个已完成的会话（前后端或 Vite 项目），经控制台启动远程预览，状态依次为安装中 → 启动中 → 运行中。
  - 在浏览器打开预览链接：页面渲染；前后端项目的接口请求成功；Vite 开发服务器的 WebSocket 连接成功（浏览器控制台出现 `[vite] connected` 或同类提示）。
  - 打开链接第二次使用被拒。
  - 未带 Cookie 的浏览器看到失效说明页。
  - 外人得到 404。
  - 停止后刷新页面，看到失效说明页。
  - 同一执行环境第 4 个预览被拒。
  - 变更记录有 `app.started` 与 `app.stopped`。
  - MCP `octopus_app` 走一遍 start → status → open → stop。
- [ ] **Step 5:** 向负责人报告；确认后推送。
