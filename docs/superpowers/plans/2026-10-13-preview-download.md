# 开发成果快速预览 第 1 期「下载」（D36） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话的源码（含 `.git`、排除依赖与秘密）能以 10 分钟签名链接下载为 zip，控制台一键下载、MCP 直接下载解压到本机目录；包内说明与 `.octopus/preview.json` 给出本机启动步骤。

**Architecture:** 新模块 `octopus/preview_spec.py` 解析与自动识别启动方式（第 2 期远程预览共用）。Worker 新增流式的 `POST /archive`：先按排除规则统计体积（≤ 200 MB），再边打包边发送（不先落盘，避开 CloudFront 源站 30 秒首字节超时）。平台签发用途为 `archive` 的票据，`GET /archive/<票据>/<run>.zip` 复核权限后把 Worker 的流以 chunked 转发给浏览器。MCP 用同一链接下载并安全解压。

**Tech Stack:** Python 3 标准库（`zipfile`、`http.server`）、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-13-session-preview-design.md`（§3、§4、§6 第 1 期）

## Global Constraints

- 不新增依赖；面向用户与 Agent 的文字使用中文。
- 下载链接 10 分钟有效，每次下载复核持有人仍能查看该会话（B6）；预览票据与下载票据互不通用。
- 打包排除：`node_modules`、`.venv`、`venv`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`target`、`.gradle`、`.next/cache`、`.turbo`、`.parcel-cache`、`.cache`；`.env`、`.env.*`（保留 `.env.example`、`.env.sample`）；所有符号链接（不跟随、不打包，列入说明）。
- 体积上限 200 MB（压缩前）；平台不在磁盘落下 zip。
- 提交规则：功能分支 `feat/m7-credentials-preview` 本地提交；EC2 验收并经负责人确认后推送。部署前确认没有进行中的会话。
- Python：`$PY -m unittest <模块>`；控制台 `npm --prefix web test`；完整回归 `PYTHON=$PY tools/regress.sh`。

## Review Focus

1. 项目内的符号链接指向 `/etc/passwd` 或其他会话目录：不被打包（Task 2 测试 `test_symlinks_are_never_followed`）。
2. 打包中途 Worker 出错或连接断开：平台不把残缺文件当作成功（中断连接、不写「已下载」记录）（Task 3 测试 `test_a_broken_stream_is_not_recorded_as_downloaded`）。
3. `/v1/workflows/<id>/archive`（归档工作流，写操作）不能因新增的下载路由被当作只读（Task 3 测试 `test_workflow_archive_still_needs_manage`）。
4. zip 中有 `../` 或绝对路径的条目（被篡改的包）：MCP 解压时不越出目标目录（Task 4 测试 `test_extraction_stays_inside_the_target`）。
5. 下载者在链接签发后被移出 Team：下载被拒（403 `archive_forbidden`）（Task 3 测试）。

---

### Task 1: 启动声明与自动识别

**Files:** Create `octopus/preview_spec.py`、`tests/test_preview_spec.py`

**Interfaces:**
- Produces:
  - `preview_spec.load(project: Path) -> dict | None`：读取并校验 `.octopus/preview.json`（结构见设计 §3）；无文件返回 None；无效时 `ValueError`（说明哪一项不对）。校验：`processes` 1–4 个；恰好一个 `entry: true`（只有一个进程时可省略，视为入口）；`command` 非空字符串 ≤ 500；`cwd` 相对路径且不越出项目目录；`port` 为 1024–65535 的整数或 `"$PORT"`；`install` 可选、≤ 4 条；`ready.timeout` 1–600（默认 120）。
  - `preview_spec.detect(project: Path) -> dict | None`：没有声明时的自动识别（`package.json` 有 `dev` → `npm ci`（无 `node_modules`）+ `npm run dev`，Vite 追加 `-- --host 127.0.0.1 --port $PORT --strictPort`；否则 `start`；只有 `index.html` → `{"static": "."}`）；都不符合返回 None。
  - `preview_spec.resolve(project) -> (spec | None, source: "declared" | "detected" | None, error: str | None)`。
  - `preview_spec.local_steps(spec) -> list[str]`：本机启动步骤文字（`$PORT` 换成入口的建议端口 5173 / 8080 等，按 `cwd` 写出 `cd`）。

- [ ] **Step 1: 写失败测试** — 合法的前后端声明（两个进程、入口为前端）；省略 `entry` 的单进程；各种无效（无进程、两个入口、`cwd: "../x"`、端口 80、`port: "abc"`、命令为空、5 个进程）；自动识别 Vite / 普通 dev / start / 静态 / 无；`local_steps` 对前后端声明给出「cd backend && go run .」与「cd frontend && npm ci」「npm run dev -- --host 127.0.0.1 --port 5173」等。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_preview_spec`）。
- [ ] **Step 5: 提交** — `feat(preview): 启动声明 .octopus/preview.json 的校验与自动识别`

### Task 2: Worker 流式打包

**Files:** Modify `octopus/worker.py`（`archive_plan`、`write_archive`、`serve` 中的 `/archive` 流式分支）；Test `tests/test_archive.py`（新建）

**Interfaces:**
- Produces:
  - `Worker.archive_plan(run_id) -> {"files": [(relative, path)], "bytes": int, "excluded": [str], "links": [str]}`：遍历项目目录（`os.walk`，`followlinks=False`），按排除规则跳过目录与文件；符号链接记入 `links`；`bytes` 超过 200 MB 时 `Error(413, "archive_too_large", "…（最大的目录：a 120 MB、b 60 MB）")`。
  - `Worker.write_archive(stream, run_id, meta)`：`zipfile.ZipFile(stream, "w", ZIP_DEFLATED)` 逐个写入，最后写 `OCTOPUS-ARCHIVE.txt`（会话、创建者、打包时间、HEAD commit（`git rev-parse HEAD`，非 Git 时写「无」）、排除项与符号链接、`preview_spec.local_steps`）。
  - HTTP：`POST /archive {"run_id", "meta": {"name", "creator"}}`：先 `archive_plan`（出错仍是 JSON 错误响应），然后 `200`、`Content-Type: application/zip`、无 `Content-Length`、`Connection: close`，边写边发。

- [ ] **Step 1: 写失败测试** — 在临时工作区建 `src/a.js`、`.git/HEAD`、`node_modules/x`、`.venv/y`、`.env`、`.env.example`、`dist/app.js`、指向 `/etc/passwd` 与工作区内文件的两个符号链接：`archive_plan` 的文件集合（含 `.git`、`dist`、`.env.example`，不含 `node_modules`、`.venv`、`.env`、链接）；`test_symlinks_are_never_followed`；超过上限（测试中用 `ARCHIVE_LIMIT` 调小）得 413 并列出最大目录；经真实 `serve` 请求 `/archive` 得到可被 `zipfile` 读取的包，含 `OCTOPUS-ARCHIVE.txt` 且写明 HEAD 与启动步骤；未认证 401。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_archive tests.test_worker_approval`）。
- [ ] **Step 5: 提交** — `feat(worker): 流式打包会话源码（含 .git，排除依赖、.env 与符号链接，200 MB 上限）`

### Task 3: 平台下载接口

**Files:** Modify `octopus/server.py`（`archive_ticket` / `archive_run`、`POST /v1/runs/<id>/archive`、`GET /archive/<票据>/<run>.zip`、`required_scope`）、`octopus/transport.py`（`worker_stream`）、`octopus/domain.py`（`Service.archive_source`、下载记录）、`octopus/schema.py`；Test `tests/test_archive.py`

**Interfaces:**
- Produces:
  - `archive_ticket(rid, user, key)` / `archive_run(ticket, key) -> (rid, user)`：同预览票据格式，签名载荷加用途前缀 `archive.`，预览票据不能用于下载（反之亦然）；过期 403 `archive_expired`，无效 403 `archive_invalid`。
  - `POST /v1/runs/<id>/archive`（read；`run_access`；会话须已分配 Worker 且有原生工作区，否则 409 `no_archive`）→ `{"url": "/archive/<票据>/<rid>.zip", "expires_in": 600}`。
  - `transport.worker_stream(config, path, body, timeout=60)`：与 `worker_request` 同样认证与 TLS，返回响应对象（调用方逐块读取）；非 200 时按 JSON 错误抛 `Error`。
  - `Service.archive_source(rid, holder) -> (config, meta)`：复核持有人能查看会话（否则 403 `archive_forbidden`），返回 Worker 配置与 `{"name", "creator"}`。
  - `GET /archive/<票据>/<rid>.zip`：`archive_run` → `archive_source` → `worker_stream("/archive")` → `200`、`Content-Type: application/zip`、`Content-Disposition: attachment; filename="<rid>.zip"; filename*=UTF-8''<会话名>.zip`、`Cache-Control: no-store`、`Transfer-Encoding: chunked`，逐块（64 KB）转发；完整结束后 `journal.record(run.downloaded, person(holder), bytes=…)` 并 `audit_read(holder, rid, "download")`；中途出错直接断开、不写记录。
  - `required_scope`：`POST /v1/runs/<id>/archive` 为 read（按路径段判断，**不**使用 `endswith("/archive")`，工作流归档仍为 manage）。

- [ ] **Step 1: 写失败测试** — 票据格式与互不通用、过期；`POST …/archive` 的权限（成员 200、外人 404、只读 Token 可用）；`test_workflow_archive_still_needs_manage`；端到端：假 Worker（`ThreadingHTTPServer` 返回分块 zip）→ 平台下载得到同样字节、头部正确、变更记录有 `run.downloaded`；`test_a_broken_stream_is_not_recorded_as_downloaded`（假 Worker 中途关闭连接）；签发后移出 Team → 403 `archive_forbidden`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_archive tests.test_preview tests.test_access tests.test_journal`）。
- [ ] **Step 5: 提交** — `feat(sessions): 下载会话源码——10 分钟签名链接、每次复核权限、流式转发不落盘、下载写入变更记录`

### Task 4: MCP 下载与 Skill

**Files:** Modify `octopus/client.py`（`Client.download(path, file, timeout=300)`）、`octopus/mcp.py`（`octopus_download`）、`skills/kirocrew-on/SKILL.md`；Test `tests/test_mcp_roles.py` 或 `tests/test_archive.py`

**Interfaces:**
- `octopus_download {run_id, dest?}`：`POST /v1/runs/<id>/archive` → `Client.download(url)` 存到临时文件 → 解压到 `dest`（默认当前目录下 `kirocrew-<run-id 前 12 位>`）；`dest` 已存在且非空时 `ValueError`；逐个条目校验解压后的路径在 `dest` 内（`test_extraction_stays_inside_the_target`）；返回 `{"dir", "bytes", "files", "head", "start": local_steps 或说明, "notes": OCTOPUS-ARCHIVE.txt 的排除项}`。
- Skill：完成后想在本机看效果时用 `octopus_download`；按返回的 `start` 步骤在用户同意后安装依赖并启动，告诉用户 `localhost` 地址；不要自行把凭证写进下载的项目。

- [ ] **Step 1–4:** 测试（假 Client 返回 zip 字节；默认目录名；非空目录拒绝；恶意条目 `../evil`、`/abs` 不越界；返回的启动步骤）→ 实现 → 通过。
- [ ] **Step 5: 提交** — `feat(mcp): octopus_download 下载并解压会话源码到本机，返回启动步骤`

### Task 5: 控制台、规划提示与文档

**Files:** Modify `web/src/features/sessions/SessionView.tsx`（「下载源码」）、`web/src/features/sessions/useSessionActions.ts`、`octopus/planning.py`（交付任务写 `.octopus/preview.json` 的要求）、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`（D36、§14.10）、`README.md`；Test `web/src/features/sessions/SessionView.test.tsx`、`tests/test_planning.py`

- [ ] **Step 1: 写失败测试** — 会话页「下载源码」在已分配 Worker 时可用，点击发出 `POST /v1/runs/<id>/archive` 并跳转到返回的链接；未分配 Worker 时停用并说明；`plan_step` 的提示中要求可运行应用的交付任务写 `.octopus/preview.json` 并把它作为验收项。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`npm --prefix web test`、`$PY -m unittest tests.test_planning`）。
- [ ] **Step 5: 提交** — `feat(console): 会话页「下载源码」；规划要求交付任务写出启动声明`

### Task 6: 回归与 EC2 验收

- [ ] **Step 1:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 2:** 确认平台与 `w1` 没有进行中的会话后部署平台与 `w1`（Worker 有改动）。
- [ ] **Step 3: 验收**（不执行任务）：对线上已完成的会话（平台主机与 `w1` 上各一个，如 medshop）`POST …/archive` → 下载 zip（经 CloudFront）：可解压、含 `.git` 且 `git log` 可读、不含 `node_modules` 与 `.env`、`OCTOPUS-ARCHIVE.txt` 有 HEAD 与启动步骤；链接 10 分钟后 403；外人 404；变更记录有 `run.downloaded`；本机 MCP `octopus_download` 解压到临时目录。
- [ ] **Step 4:** 向负责人报告；确认后推送。
