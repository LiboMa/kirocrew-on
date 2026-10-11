# KiroCrew ON 运维手册

适用范围：M1 单机部署（EC2 Amazon Linux 2023 x86_64，一个个人执行边界 `personal`）与本机 macOS 端到端环境。
§2–§8、§12 的 EC2 命令已于 2026-10-11 在 us-east-1 实测，结果见 `docs/handover/M1-EC2-EVIDENCE.md`。
本地端到端（macOS）的步骤见 §14。

## 1．目录与端口

| 路径 | 内容 | 所属 |
|---|---|---|
| `/opt/kirocrew-on/releases/<版本>/` → `current` | 代码（按版本并存） | root |
| `/opt/kirocrew-on/venv/` | 平台与 Worker 的 Python 环境 | root |
| `/opt/kirocrew/<版本>/venv/` → `current` | 固定版本的 KiroCrew 运行环境（composition 在此解释器中运行） | root |
| `/opt/kirocrew-tools/bin/` | kiro-cli、claude、codex、opencode | root |
| `/etc/kirocrew-on/platform.toml` | 平台配置 | root:octopus 0640 |
| `/etc/kirocrew-on/boundary-personal.toml` | 执行边界配置 | root:crew-personal 0640 |
| `/var/lib/octopus/platform/` | SQLite、引擎锁、迁移前备份 | octopus 0700 |
| `/var/lib/octopus/boundaries/personal/` | `home/`（工具认证）、`crew/`（KIROCREW_HOME）、`kiro/`（KIRO_HOME）、`worker/`（Workspace）、`worker.token`、路由日志、组合标记 | crew-personal 0700 |
| `/var/log/kirocrew-on/` | 平台日志目录 | octopus |

| 端口 | 服务 | 监听 |
|---|---|---|
| 8918 | `octopus-api`（Web 控制台 + `/v1` API + Engine） | 127.0.0.1 |
| 8928 | `octopus-worker@personal` | 127.0.0.1 |
| 5476 | `octopus-gateway@personal`（KiroCrew Gateway + composition） | 127.0.0.1 |

控制台与 API 经 CloudFront 访问：`https://<分发域名>/`（`deploy/aws/deploy.sh status` 显示）。API 监听 `0.0.0.0:8918`，
安全组只允许 CloudFront 源站地址访问，平台另外校验 `X-Origin-Verify`；Worker（8928）与 Gateway（5476）只监听回环，
原生入口经 SSM 端口转发：`deploy/aws/deploy.sh tunnel`（`localhost:15476 → 5476`）。

## 2．首次部署（Shell + CloudFormation，2026-10-11 EC2 实测）

在本机 kirocrew-on 检出中执行（需要 us-east-1 的 AWS 凭据；`deploy/aws/stack.env` 中的规格为候选值）：

```bash
deploy/aws/deploy.sh up            # 校验模板；查询 CloudFront 前缀列表与可用区；在 SSM 生成源站密钥与管理员密码；部署栈
deploy/aws/deploy.sh push          # 发布已提交的 HEAD：S3 → SSM Run Command → al2023.sh（安装、原生契约检查、启用服务、注册 Worker）
deploy/aws/deploy.sh kiro-login    # 一次性：经 SSM 以 crew-personal 运行 kiro-cli 设备码登录，打印验证地址与设备码，完成后 whoami 确认
deploy/aws/deploy.sh status        # 栈输出、SSM 在线状态、https://<分发域名>/health
deploy/aws/deploy.sh admin-password  # 读取管理员密码（SSM SecureString），登录控制台
```

`al2023.sh` 可重复执行；KiroCrew 按 `deploy/versions.env` 锁定的 commit 从官方源码构建，版本不在已验证集合时须原生契约检查通过才会被接受。

注册 Worker（管理员 token 登录后）：`POST /v1/workers`，body 为
`{"name":"personal","owner_id":"<用户 ID>","connection":{"url":"http://127.0.0.1:8928","token":"<worker.token 内容>"}}`。
平台用户 `octopus` 与执行用户 `crew-personal` 不同，Worker 可登记给普通用户。

## 3．访问

```bash
aws ssm start-session --target <实例 ID> --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=8918,localPortNumber=18918
aws ssm start-session --target <实例 ID> --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=5476,localPortNumber=15476      # 仅在需要打开原生 KiroCrew 页面时
```

浏览器打开 `http://localhost:18918/`。`platform.toml` 的 `allowed_hosts` 必须包含 `localhost:18918`；
未登记的 Host 返回 `421 host_not_allowed`。Coding 客户端使用 `OCTOPUS_URL=http://localhost:18918` 与用户 Token。

### Worker 产出的应用（按 run-id 在主机上运行并转发）

```bash
deploy/aws/deploy.sh space <run-id>        # 运行并转发；Ctrl-C 只结束转发，再次执行复用已运行的应用
deploy/aws/deploy.sh space <run-id> stop   # 停止
deploy/aws/deploy.sh spaces                # 列出正在运行的应用与端口
```

经 SSM 以 crew-personal 在临时 systemd 单元 `kirocrew-space-<run-id>` 中运行 `worker/<run-id>/workspace/project` 的
`npm run dev`（缺少 `node_modules` 时先 `npm ci`；Vite 项目带 `--host 127.0.0.1 --port --strictPort`，其他项目读 `HOST`/`PORT`）。
远端从 5173 起取第一个空闲回环端口，本机端口为其加 10000（如 `http://localhost:15173/`）。需要本机安装 session-manager-plugin。
dev server 只在工作区写 `node_modules/.vite`；不需要把工作区拉回本机。

### 原生 KiroCrew 对话（第二个 CloudFront 分发）

控制台“在 KiroCrew 中打开”经 `https://<NativeDomain>/`（栈输出 `NativeUrl`）打开：CloudFront → 安全组（仅 CloudFront
前缀列表，端口 `NativePort`，默认 8919）→ 主机 nginx（`/etc/nginx/conf.d/kirocrew-native.conf`，只转发带
`X-Origin-Verify` 的请求，支持 WebSocket）→ 回环 Gateway `127.0.0.1:5476`。Gateway 仍然只监听回环，并保留自己的
token 登录、Host 与 CSRF 检查；公网来源经 `KIROCREW_CORS_ORIGINS` 被信任（由 `crew_url` 推导）。
入口登录有效期 `crew_entry_ttl = "1h"`（K03：该登录覆盖整个 Gateway；过期后在控制台重新点击打开）。
nginx 访问日志只记路径，不记 `?token=` 查询串。经代理的请求在 KiroCrew 中被识别为远程，不能修改 Gateway 配置。
SSM 隧道（`localhost:15476`）方式仍可用：`crew_url` 指向本地端口时，该端口进入 `KIROCREW_ALLOWED_LOOPBACK_PORTS`。

## 4．升级

1. 一致性备份：`sudo -u octopus /opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml backup --dest /var/lib/octopus/platform/backups`
2. 取得新版本代码后重新运行 `sudo deploy/install/al2023.sh personal`（新 release 目录与旧目录并存，`current` 指向新版本）。
3. 依次重启：`octopus-api` → `octopus-worker@personal` → `octopus-gateway@personal`；每个服务的 `ExecStartPost` 执行 `wait-ready`。
4. schema 迁移在 API 启动时执行，迁移前自动备份到 `/var/lib/octopus/platform/backups/octopus-v<旧版本>-<时间>.sqlite3`；结果见 `journalctl -u octopus-api`。

## 5．回滚

```bash
sudo ln -sfn /opt/kirocrew-on/releases/<旧版本> /opt/kirocrew-on/current
sudo systemctl restart octopus-api octopus-worker@personal octopus-gateway@personal
```

若新版本已执行 schema 迁移，旧版本会拒绝打开更高版本的数据库（“数据库 schema 版本 N 高于程序支持的 M”）。
此时先用升级前备份在独立目录执行 `restore-verify`，确认后停服替换数据库文件。
回滚记录必须写明原因与已执行的代码和数据变更；不通过回滚元数据掩盖已执行的变更。

## 6．重启与停止

- 先确认没有在途派发：控制台查看 Session 状态，或 `GET /v1/runs`。
- `sudo systemctl stop|restart <服务>` 发送 SIGTERM：API 停止接收请求、取消尚未开始的推进，在 `shutdown_grace`（默认 30 s）内等待在途推进收尾；超时则以 0 退出，在途结果由重启后的核对处理。
- 禁止 `pkill` 全局结束进程；只操作本部署的 systemd 单元。
- 重启后 Engine 从 SQLite 与 Worker 落盘记录恢复；派发回执不明的 Attempt 显示为 `dispatch_unknown`，按 Attempt 标记核对后再决定是否重派。
- EC2 实测：编码中分别重启 API 与 Worker、重启 Gateway、整机重启（`aws ec2 reboot-instances`），Session 均自动完成且无重复派发。重启 Gateway 会让原生取消运行中的任务，平台把该 Attempt 判为 blocked，自动诊断后重试；整机重启后约 30 s 服务恢复。
- 源站重启期间 CloudFront 返回 502 / 504，CLI 与 MCP 报告 `504 Gateway Timeout: API 暂不可用，稍后重试`；稍后重试即可。

## 7．日志

```bash
journalctl -u octopus-api -u octopus-worker@personal -u octopus-gateway@personal --since "1 hour ago"
sudo -u crew-personal tail -n 50 /var/lib/octopus/boundaries/personal/provider-routing.jsonl   # 实际 ACP 后端证据
cat /var/lib/octopus/boundaries/personal/gateway-runtime.json                                   # 组合标记
```

API 访问日志不记录 token、请求体或查询串；Engine 只记录异常类名。

## 8．认证与过期

- Claude Code 与 Codex 经实例角色使用 Bedrock：实例角色需要 `bedrock:InvokeModel*`（Claude）与 `bedrock-mantle:CreateInference`（Codex 的 amazon-bedrock 提供方）。
- Kiro CLI：`deploy/aws/deploy.sh kiro-login` 经 SSM Run Command 在主机上以 crew-personal 运行 `kiro-cli login --use-device-flow`（带 `stack.env` 中的 `KIRO_LICENSE`、`KIRO_IDC_START_URL`、`KIRO_IDC_REGION`）。kiro-cli 即使给齐参数仍有交互提示、无终端时失败，因此在 `script(1)` 伪终端中运行并自动回车；脚本打印验证地址与设备码，在浏览器完成授权后自动确认（设备码 15 分钟内有效，过期重新执行）。日志：`/var/lib/octopus/boundaries/personal/logs/kiro-login.log`。不需要本机安装 session-manager-plugin。

### 过期处理

现象：Attempt 失败类型为 `auth`，Session 进入等待并说明具体工具。
处理：`sudo -iu crew-personal` 重新登录对应工具；不需要重启平台，Session 在条件满足后继续（M2 起自动恢复）。
不要把个人机器上的 HOME 或凭据目录复制到实例。

## 9．停止失败

原生任务仍在运行、排队或有待批准项时，Session 保持“停止中”，不伪造已停止。
检查：原生页面（经 15476 转发）中的批准请求与任务状态；处理批准或等待原生任务结束后，Engine 下一轮核对并完成停止。

## 10．磁盘

```bash
df -h /var/lib/octopus
sudo du -sh /var/lib/octopus/boundaries/personal/worker/* | sort -h | tail
```

只清理已关闭且已备份的 Session 的 Workspace；删除通过 Session 的受控删除操作完成，不直接删除目录。

## 11．队列异常

大量 Session 停留在同一状态时，按顺序检查：

1. Worker 健康：`… wait-ready --worker --timeout 5`（`--config /etc/kirocrew-on/boundary-personal.toml`）。
2. Gateway 就绪与组合：`… wait-ready --gateway --timeout 5`；失败信息含“未加载 PocProviderRegistry 组合”时，Gateway 不是经 composition 启动。
3. 路由日志是否持续写入；缺失时所有任务会被拦为“实际 ACP 后端未核实”（K16）。
4. Worker 不可用时 Session 保持原状态并按 2 s、4 s、8 s…（上限 60 s）退避重试，恢复后自动继续。

## 12．备份恢复

```bash
P="sudo -u octopus /opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml"
$P backup --dest /var/lib/octopus/platform/backups
sudo -u crew-personal /opt/kirocrew-on/venv/bin/python -m octopus archive /var/lib/octopus/boundaries/personal \
  --dest /var/lib/octopus/boundaries/personal/backups --name boundary-personal --exclude 'home/.cache/*' --exclude 'backups/*'
$P restore-verify /var/lib/octopus/platform/backups/octopus-<时间>.json --into /tmp/restore-<日期>
```

`restore-verify` 只接受空的独立目录，校验 sha256、完整性与各表行数。
未覆盖：各 Coding 工具私有会话目录的语义一致性、远端仓库状态、原生 Gateway 内存中的任务。

## 13．批准模式

无人值守的执行边界在 `boundary-<边界>.toml` 中设置 `approval_mode = "auto"`（D14）：Worker 只对平台派发的 spawn 请求原生
`approval_mode=auto`；`KIROCREW_HOME/config.json` 的全局工具批准保持 `interactive`，owner 与父会话的原生聊天仍需人工批准
（无人处理时 600 s 后被原生拒绝）。诊断使用不能派生子任务的 `poc-diagnose` 模板。升级 KiroCrew 后先用一个冒烟任务确认
spawn 未被拒绝（K19）。

## 14．本地端到端（macOS）

用于“先在本地跑通，再部署远端”。使用本仓库代码、独立端口与 `state/local-e2e/` 数据目录（含独立 `KIROCREW_HOME`、`KIRO_HOME`），不读写 `~/.kiro`。

```bash
npm ci                                  # ACP 适配器（node_modules/.bin）
tools/local_stack.sh start              # Gateway 6476、Worker 8938、API 8948，并逐个 wait-ready
tools/local_stack.sh status
tools/local_stack.sh restart worker     # 单独重启某个服务
tools/local_stack.sh stop               # 逆序 SIGTERM，只结束本栈的进程
```

日志在 `state/local-e2e/logs/`；控制台 `http://127.0.0.1:8948/`。

### 控制台（React）

`/` 为 React 控制台（`web/` 构建到 `octopus/ui/dist/`）；未构建时 `/` 回退到旧版。旧版控制台保留在 `/classic/`，两者共用登录。

```bash
npm --prefix web ci                     # 前端依赖（lockfile 固定版本）
npm --prefix web run build              # 构建到 octopus/ui/dist（主机安装时由 al2023.sh 执行）
npm --prefix web run dev                # 开发服务器 http://localhost:5173，代理 /v1 到 OCTOPUS_DEV_API（默认 8921 临时数据服务）
npm --prefix web test                   # 前端单元与组件测试（Vitest）
npm --prefix web run test:browser       # 浏览器回归：React 与 /classic/ 共 7 个文件，每个文件独立的 8921 临时数据服务
```

浏览器回归只使用 `tests/mvp_browser_fixture.py` 的临时 HTTP/SQLite 数据，不连接 Worker、不调用 Coding 工具。
