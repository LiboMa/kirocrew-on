# 开发成果的快速预览（D36）

## 1．目标

会话完成后，创建者与 Team 成员能尽快看到成果运行起来的样子：

1. **下载到本机**：一键下载源码（含 `.git`），或由 Coding Agent 下载解压到本机目录后按声明的方式启动，在 `localhost` 打开。
2. **浏览器内远程预览**：在执行环境中启动应用，经平台转出，任何能查看该会话的人点开链接即可使用（含开发服务器、前后端两个进程、WebSocket 热更新）。

现状与不足：

| 方式 | 不足 |
|---|---|
| 静态预览（`octopus_preview`，10 分钟票据，沙箱） | 只能打开静态文件；不能运行开发服务器或后端；沙箱中 `localStorage` 等不可用 |
| `deploy.sh space <run-id>`（主机上 `npm run dev` + SSM 端口转发） | 只有持 AWS 权限的运维能用；只认 `dev` 脚本；不支持前后端两个进程 |
| 导出 | 只有会话记录，拿不到源码 |

## 2．已确定（负责人，2026-10-13）

| 编号 | 决定 |
|---|---|
| V1 | 两种都做：先下载（第 1 期），紧接着浏览器内远程预览（第 2 期，独立预览分发 + 签名 Cookie） |
| V2 | 启动方式：声明优先（`.octopus/preview.json`），没有声明时自动识别（`package.json` 的 `dev` / `start` 等） |
| V3 | 下载内容：源码与 `.git`，排除依赖与缓存目录 |

## 3．启动声明 `.octopus/preview.json`

由交付任务写在项目根目录（KiroCrew 规划时作为交付任务的验收项；D34 任务契约）：

```json
{"processes": [{"name": "backend", "cwd": "backend", "command": "go run .", "port": 8080},
               {"name": "frontend", "cwd": "frontend", "command": "npm run dev -- --host 127.0.0.1 --port $PORT",
                "port": "$PORT", "entry": true}],
 "install": [{"cwd": "frontend", "command": "npm ci"}],
 "ready": {"path": "/", "timeout": 120}}
```

- `processes`：一个或多个进程；`entry: true` 的进程是对外入口（只能有一个）；`port` 为固定端口或 `$PORT`（由平台分配后作为环境变量传入）。其余进程只在执行环境内部可见（例如前端开发服务器把 `/api` 代理到后端）。
- `install`：首次启动前执行一次的依赖安装。
- 本机运行（第 1 期）与远程预览（第 2 期）共用这份声明；下载包中的说明文件据此写出本机启动步骤。
- 没有声明时自动识别：`package.json` 中有 `dev` 则 `npm ci`（无 `node_modules` 时）+ `npm run dev`（Vite 传 `--host --port --strictPort`），否则 `start`；静态站点（只有 `index.html`）用内置静态服务；都不符合时说明「未找到启动方式，请在交付任务中写 .octopus/preview.json」。
- 校验：命令只在该会话的项目目录内执行；`cwd` 不得越出项目目录；进程最多 4 个。

## 4．第 1 期：下载

### 4.1 接口

- `POST /v1/runs/<id>/archive` → `{"url": "/archive/<票据>/<会话名>.zip", "expires_in": 600, "bytes_estimate"}`；票据与静态预览同法签名（会话、用户、过期时间），**每次下载复核**持有人仍能查看该会话（B6）。
- `GET /archive/<票据>/<名称>.zip`：平台从 Worker 以流式取回 zip 并转发，不在平台落盘；`Content-Disposition: attachment`；与控制台不同的缓存与安全头（`no-store`）。
- Worker `GET /archive?run_id=`：在执行环境内打包，以流的形式返回。

### 4.2 内容

- 项目目录全部文件与 `.git`；排除依赖与缓存目录：`node_modules`、`.venv`、`venv`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`target`、`.gradle`、`.next/cache`、`.turbo`、`.parcel-cache`、`.cache`。
- 默认排除可能含秘密的 `.env`、`.env.*`（保留 `.env.example`、`.env.sample`），以及 KiroCrew / 平台写在项目内的临时凭证文件（D35 §5.1 不在项目目录写凭证，此为兜底）。
- 不跟随指向项目目录之外的符号链接（打包为链接本身）。
- 上限 200 MB（压缩前）；超出时拒绝并列出最大的目录，提示在交付任务中清理产物或改用远程预览。
- 包内附 `OCTOPUS-ARCHIVE.txt`：会话 ID 与名称、创建者、打包时间、HEAD commit、排除了哪些内容、本机启动步骤（来自 `.octopus/preview.json` 或自动识别）。

### 4.3 入口

- 控制台会话页「下载源码」。
- MCP `octopus_download {run_id, dest}`：在本机下载并解压到 `dest`（默认 `./kirocrew-<会话名>`，目录已存在且非空时拒绝），返回目录、HEAD 与启动步骤；Skill 指导 Agent 安装依赖、按步骤在本机启动并告诉用户 `localhost` 地址（不在用户未同意时执行安装）。
- 记录：下载写入会话的变更记录（`run.downloaded`：操作者、字节数、commit），管理员下载他人会话写审计（`run.download`）。

## 5．第 2 期：浏览器内远程预览

### 5.1 运行

- Worker 新增 `/app`：`start` / `stop` / `status`。按 §3 启动进程（以执行环境的 OS 用户，在项目目录内，进程组独立），入口端口从 47000–47999 分配，等待 `ready` 后返回；日志写入会话目录，控制台可查看最近 200 行。
- 每个会话最多一个预览；每个执行环境同时最多 3 个；空闲 30 分钟（以平台记录的最近访问时间为准）自动停止；会话关闭、删除或续作开始执行时停止。
- Worker 重启后预览进程随之结束，状态如实显示为已停止。
- 第一版不做 CPU / 内存硬配额，只限数量与空闲时长（风险：失控进程影响同一执行环境的其他会话；后续用 cgroup 限额）。

### 5.2 访问

```text
浏览器 ──https──> 预览分发（CloudFront，与控制台不同源）──> 平台 nginx :8917
   nginx auth_request ──> API /v1/preview-auth（校验 Cookie：签名、过期、查看权限；记最近访问；返回上游）
   ├─ 平台主机上的执行环境：127.0.0.1:<端口>
   └─ Worker 主机：私网 TLS ──> Worker 主机 nginx（预览端口）──> 回环 <端口>
```

- 打开方式：控制台或 MCP 取得 `https://<预览分发>/_open/<票据>`（10 分钟内有效、一次性），访问后写入只对预览域有效的 HttpOnly Cookie（会话、用户、过期 8 小时），再跳转到 `/`；之后整个根路径都转发给该会话的应用，绝对路径的应用（Vite、Next.js 等）不受影响。
- 同一浏览器同时只预览一个会话（打开另一个会话的预览即替换 Cookie）。
- 每个请求经 `auth_request` 复核查看权限；撤销访问或停止预览后立即失效。
- 转发 HTTP 与 WebSocket；`Host` 改写为 `localhost:<端口>`（开发服务器的 Host 校验）；预览域不设置控制台的任何 Cookie，应用无法读取控制台的 Token。
- 部署：新增一个 CloudFront 分发（源站为平台主机的预览端口，带源站密钥，与现有两个分发同法）；Worker 主机 nginx 增加预览端口，只接受平台主机经私网 TLS 访问，并只转发到 47000–47999。

### 5.3 入口

- 控制台会话页「远程预览」：启动、打开、停止，显示状态、入口地址与最近日志。
- MCP `octopus_app {run_id, action: start|stop|status}` 返回状态与打开链接。
- 静态预览保留；远程预览可用时控制台优先展示远程预览。

## 6．测试与验收

- 第 1 期：打包排除规则、符号链接、`.env` 排除、上限、说明文件；票据过期与权限复核（撤销后 403）；流式转发不落盘；MCP 下载与解压（目录已存在时拒绝）；EC2 以 medshop 类前后端项目下载并在本机按说明启动。
- 第 2 期：声明解析与自动识别；端口分配与上限；空闲停止；`auth_request` 的签名、过期与权限；WebSocket 热更新；平台主机与 Worker 主机两条路径；EC2 在浏览器中打开前后端项目的远程预览。

## 7．不在本轮

公网分享链接（无需登录）、多个预览并存于同一浏览器、CPU / 内存配额、数据库等附加服务、生产构建的托管部署。
