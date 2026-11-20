# 有副作用的 GET 请求审计（MVP 0.2.0 P1 Task 7，CE12）

日期：2026-10-26　·　范围：`octopus/server.py` 处理的全部 GET 路由，跟进到 `domain.py`、各辅助模块和 Worker（`worker.py`）的处理　·　固定方式：`tests/test_get_side_effects.py` 读本文末尾的「固定清单」

## 1．为什么审

会话预览域名 `preview.tinyboat.blog` 与控制台 `kirocrew-on.tinyboat.blog` 同属一个可注册域名（CE12）。根治办法是改用独立的可注册域名（设计 §17 #8，负责人稍后提供域名）。在那之前，同站页面能做的事情是：

- 登录 Cookie 是 `SameSite=Strict`，同站请求照样会带上它；
- PR #9 起，带 Cookie 的 GET 只在三种情况下放行：`Sec-Fetch-Site` 缺失、为 `same-origin` 或 `none`；或者是顶层文档导航（`Sec-Fetch-Mode: navigate` 且 `Sec-Fetch-Dest: document`）。见 `server.py` 的 `identity()`；
- 所以，同站页面只能用 `window.open`、`location=`、链接或 GET 表单，以控制台用户的身份触发 GET。`<img>`、`fetch`、`<iframe>` 都会被拒绝。

本审计列出：哪些 GET 会改变状态、改哪里；能否被这样的顶层导航触发；建议保留，还是改为 POST。设计 §15 P1 定的范围是**只出清单，不改路由**。要改的路由另行评审。

## 2．全局说明

| # | 说明 | 位置 |
|---|---|---|
| G1 | **`tokens.last_used`**：凡经 `identity()` 的请求，每个 Token 每分钟最多写一次，单独开一个尽力而为的短事务。未知路径和方法不对的请求也会在返回 401 / 403 / 404 / 405 前写这一下 | `server.py` `identity()` |
| G2 | **Cookie 规则**：见第 1 节。控制台网页登录签发的 Token 带 `admin` 范围，所以管理员在控制台里算 elevated（G4） | `server.py` `identity()`、`security.py` `issue_token` |
| G3 | **Worker Token 解封**：每个进程第一次用某个密封的 Token 时，会运行一次 `openssl enc -d`，之后明文缓存在内存里 | `vault.py`、`transport.py` `_OPENED` |
| G4 | **管理员监督审计**：elevated 管理员读一个自己本无权访问（不经本人、Team 或共享）的会话时，每人、每会话、每种操作每小时写一行 `audit`（`run.<操作>`）。对所有 elevated 调用者都会开 `BEGIN IMMEDIATE`，即使最后什么也没写。Request 的 `_audit_oversight` 同理 | `domain.py` `audit_read`、`_audit_oversight` |
| G5 | **Worker 的读接口顺带做重启恢复**：`/files`、`/app`、`/candidate`、`/archive`、`/archive/plan`、`/pull/bundle` 都会调 `_undo_baseline`。如果重启后留下了 `<run>/baseline.json`，且没有正在跑的基线任务，就执行 `git read-tree -u --reset <candidate>`，重写已跟踪文件和索引，然后删掉这个标记 | `worker.py` `_undo_baseline` |

## 3．有副作用的 GET（G1 以外）

「同站触发」指：同站页面能否用顶层导航，以登录用户的 Cookie 触发它。

### 3.1 写数据库（`events`、`audit` 以外的表）——固定清单所列

| 路由 | 副作用 | 同站触发 | 建议 |
|---|---|---|---|
| `GET /v1/runs/{id}/app` | 按 Worker 报告的状态改 `apps.status / error / stopped`；变为 failed 或 stopped 时写一条 `events`（`app.stopped`）；Worker 已退役时同样处理。Worker `/app status` 做 G5；进程已退出的演示，Worker 会把它标为 failed，并结束它残留的进程组 | 能（共享的查看者也能触发） | **保留**：这是把记录对齐到实际状态，Worker 只清理已经退出的演示。演示还在运行时，这一请求不改任何东西 |
| `GET /_kirocrew/s/{token}`（公开分享，D75） | `public_shares.views / last_viewed`，每个分享最多每 30 秒写一次；演示类分享另写 `apps.last_access`。内存里的限速计数 `_share_tries` 与浏览计数 `_share_views` 也会变 | 不经控制台 Cookie：链接本身就是凭据 | **保留**：浏览计数本来就由打开链接产生 |
| `GET /v1/preview-auth`（预览域名 nginx 的 `auth_request`） | `apps.last_access`，每分钟最多一次 | 不经控制台 Cookie，只经预览网关：回环地址、`X-Preview-Gate` HMAC，并凭 `__Host-` 预览 Cookie | **保留**：nginx 的 `auth_request` 只能发 GET；这里记的是「演示仍有人在看」，空闲回收靠它 |
| `GET /_kirocrew/enter/{ticket}` | `apps.last_access`；一次性 nonce 记入内存 `_opened`；设置 `__Host-kirocrew_preview` Cookie | 不经控制台 Cookie，凭链接里的票据 | **保留**：这一步就是跳转进演示的入口 |

后两条要在预览网关后面才能请求，所以测试没有覆盖，由 `tests/test_remote_preview*.py` 覆盖。

### 3.2 只写 `events` / `audit`（读这件事本身的记录）

| 路由 | 副作用 | 同站触发 | 建议 |
|---|---|---|---|
| `GET /archive/{ticket}/{rid}.zip` | zip 完整下载后写 `events`（`run.downloaded`）与 `audit`（`run.download`）。Worker `/archive` 做 G5，zip 流式生成，不落临时文件 | 凭票据（10 分钟），不经 Cookie | 保留：下载必须用 GET |
| `GET /v1/runs/{id}`、`/report`、`/revisions`、`/revisions/{n}`、`/revisions/diff` | `audit` `run.view`（G4） | 能 | 保留：监督审计是有意为之 |
| `GET /v1/runs/{id}/export` | `audit` `run.view` 与 `run.export`（G4） | 能 | 保留 |
| `GET /v1/runs/{id}/changes` | `audit` `run.changes`（G4） | 能 | 保留 |
| `GET /v1/runs/{id}/attempts/{aid}/diff` | `audit` `run.diff`（G4）；Worker `/candidate diff` 做 G5，其余只读 git | 能 | 保留路由；G5 见第 4 节 |
| `GET /v1/runs/{id}/observe` | `audit` `run.observe`（G4）；Worker `/observe` 只读 Gateway 的会话 | 能 | 保留 |
| `GET /v1/runs/{id}/public-shares` | `audit` `run.public_shares`（G4） | 能 | 保留 |
| `GET /v1/requests/{id}` | `audit` `request.view`（管理员监督时） | 能 | 保留 |
| `GET /v1/runs/{id}/events?stream=1` | `audit` `run.view`（G4）；占用一个服务线程和连接约 30 秒，每秒重新跑一次 `identity()` | 能 | 保留；同站页面能占住的线程数，受浏览器能开的顶层窗口数限制 |

### 3.3 不写数据库、但会调 Worker 或改内存

| 路由 | 副作用 | 同站触发 | 建议 |
|---|---|---|---|
| `GET /v1/admin/environments` | 并发（最多 8 个、超时 4 秒）调**每个**未退役 Worker 的 `/health`。每个 Worker 会运行 `kiro-cli whoami`，结果在内存缓存 60 秒。不写 `workers.config` | 能（管理员） | 保留；独立域名之后同站问题消失。若要更稳，可在平台侧把探测结果缓存几秒 |
| `GET /v1/runs/{id}/chat-changes` | Worker `/candidate check` 做 G5，并运行 `git status`（git 可能刷新索引的状态缓存） | 能 | 保留路由；G5 见第 4 节 |
| `GET /v1/runs/{id}/approvals` | Worker `/approvals`，只读 Gateway | 能 | 保留 |
| `GET /preview/{ticket}/{path}`（预览产出） | Worker `/files` 做 G5 | 凭票据，不经 Cookie | 保留 |
| `GET /_kirocrew/s/{token}/{file}` | 内存限速计数；Worker `/files` 做 G5 | 凭分享链接 | 保留 |
| `GET /bundle/{ticket}/{rid}.bundle` | Worker `/pull/bundle` 做 G5，生成临时文件 `.pull-<hex>.bundle`，传完即删；`run.pull` 的审计在签发票据的 POST 时已写 | 凭票据 | 保留 |
| `GET /_kirocrew/open/{ticket}` | 在共享预览源上发 `Clear-Site-Data`，不写库 | 凭票据 | 保留 |
| `GET /v1/users`、`GET /v1/tokens` | 什么也不写，但用 `database.transaction()` 读，会占一下 SQLite 的写锁 | 能 | 建议改用 `database.read()`：不改变行为，只是不再无谓地占锁（另行评审） |

其余 GET 都没有 G1 以外的副作用，包括：控制台静态文件、`/health`、`/v1/openapi.json`、`/v1/me`、`/v1/teams`、`/v1/workflows`、`/v1/workflow-templates`、`/v1/designs/{id}`、`/v1/workers`、`/v1/workers/enrollments`、`/v1/placement`、`/v1/runs`、`/v1/policies`、`/v1/quotas`、`/v1/requests`、`/v1/runs/{id}/shares`、`/v1/runs/{id}/events`（不带 stream）、`/v1/credentials`，以及 `/v1/admin/users`、`/v1/admin/users/{id}/tokens`、`/v1/admin/audit`。只支持写方法的路径收到 GET 时，在调用 Service 之前就返回 405 / 404 / 403。

## 4．结论与建议

1. **没有一条经控制台 Cookie 的 GET 会替用户做主动操作**：不会启动或停止会话、改设置、签发凭据或分享。会改数据库的只有三类：
   - 对齐演示状态（`/app`）；
   - 浏览计数与演示的「最近访问」（公开分享、预览网关）；
   - 读操作本身的审计和日志。

   因此，在独立域名到位之前，**不需要为 CE12 改任何路由**。
2. **G5 值得另行评审**。Worker 的读接口会顺带做重启恢复（`git read-tree -u --reset`）。它只在异常重启后、标记文件还在时才发生，作用是把工作区恢复到候选提交。但「读请求会改工作区」违背直觉。建议：Worker 启动时统一做一次这个恢复，读接口不再做。这只改 Worker 内部，不改路由。
3. **小的改进**（另行评审，不改变体验）：
   - `GET /v1/users` 与 `GET /v1/tokens` 改用只读连接；
   - `GET /v1/admin/environments` 在平台侧加几秒的探测缓存。
4. **根治**：控制台与预览改用不同的可注册域名（设计 §17 #8）。之后同站页面不再存在，第 3 节「同站触发」一列整体失效。

## 5．固定清单（测试读取）

下面是会写 `events`、`audit`、`tokens.last_used` 以外表的 GET 路由，以及它们写的表。`tests/test_get_side_effects.py` 会实际请求每条 GET 路由，比对改动了哪些表，要求与这里一致。有新的 GET 带来副作用，或者原有的副作用没了，测试都会失败，直到本文同步更新。

```side-effects
GET /v1/runs/{id}/app -> apps
GET /_kirocrew/s/{token} -> public_shares
GET /v1/preview-auth -> apps
GET /_kirocrew/enter/{ticket} -> apps
```
