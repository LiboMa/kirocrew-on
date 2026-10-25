# 当前事实清单

整理日期：2026-10-11。每条附来源；行号以迁入基线 `3a84485` 为准，另有说明的除外。

| # | 事实 | 来源 |
|---|---|---|
| F01 | 迁入来源：`kirocrew-octopus`，分支 `codex/coordinator-recovery`，HEAD `b1ba98f`，迁入时 78 处未提交改动；迁入后来源仓库未被修改 | `docs/PROVENANCE.md`；`git status --porcelain \| wc -l` |
| F02 | 迁入 105 个文件（代码 88、继承文档 17）；9 个与 `kirocrew-app-poc` 同名文件内容不同，以 octopus 版为准 | `docs/PROVENANCE.md` |
| F03 | 本机离线回归实测：旧版 92 项、新版 66 项（42 + 16 + 8）通过，Python 3.12.13 / 3.13 | Task 1 `tools/regress.sh` |
| F04 | 支持的 KiroCrew 版本集合 `{0.7.1, 0.7.2, 0.8.0}`，启动时不在集合内即报错；本机已安装 0.8.0 | `poc.py:19, 201-202`；`/Applications/KiroCrew.app` |
| F05 | 组合标记：Gateway 启动写 `gateway-runtime.json`，含 `composition: PocProviderRegistry` 与 `composition_features: [codex_effort_pair_v1]` | `poc.py:216-221`；`workflow.py:require_runtime 167-170` 读取 |
| F06 | ACP 适配器：`@agentclientprotocol/claude-agent-acp` 0.84.0（内含 claude-agent-sdk 0.3.284）、`@agentclientprotocol/codex-acp` 1.11.0（锁定 `@openai/codex` 0.153.4） | `package-lock.json:15-40, 273-274` |
| F07 | 端口：Gateway 5476、旧 Pipeline 8916、旧 Workflow Studio 8917、Team API 8918、Worker 8928；当前 HEAD 的 CLI 默认值改从 `octopus/config.py` 读取 | `poc.py:18`；`pipeline.py:844`；`workflow.py:36`；`server.py:22`；`worker.py:430`；`config.py` |
| F08 | 新版 SQLite 16 张表（users、tokens、teams、members、workflows、workflow_versions、grants、workers、runs、tasks、attempts、revisions、commands、events、recoveries、schema_version），索引 run_events、run_owner、recovery_run | `octopus/db.py:19-86` |
| F09 | `schema_version` 只写不读：每次启动执行 `CREATE ... IF NOT EXISTS` 与 `INSERT OR IGNORE VALUES(1)`，没有迁移；以后新增的列不会出现在已有数据库中 | `octopus/db.py:84-96` |
| F10 | Engine 单线程：`engine.lock` 独占租约 + 一个 daemon 线程；`tick` 串行推进所有 Run，`/prepare` 最长 240 s，其他 Worker 请求 35 s 超时 | `engine.py:20-25, 62-64, 113`；`transport.py:11` |
| F11 | Worker 到 Gateway 写死 `127.0.0.1`，Worker 必须与 Gateway 同机；只有浏览器 origin 可通过 `--crew-url` 配置 | `api.py:37, 54, 73, 77, 90`；`worker.py:49, 395` |
| F12 | 原生 token 每次调用现取：`request()` 每次调用 `token()`，读取 `.local_secret` 并请求 `/api/token/local`；一次 inspect 至少换 2–3 次 token；只有旧版观察器缓存 180 s | `api.py:50-57, 76`；`worker.py:native 54-55` |
| F13 | crew-entry 链接中的原生 token `ttl=8h`（EC2 设为 1h），是整个 Gateway 的 owner 浏览器登录，不是单 Session 票据；换取登录时 Gateway 另发 30 天的刷新凭据（`mc_refresh_<端口>`，Path=/api/auth），公网中转拦下 `/api/auth/refresh`（2026-10-26） | `api.py:chat_entry_url 60-68`；`test_workflow.py:231` |
| F14 | 路由日志：旧版 `poc.py` 写 `ROOT/evidence/provider-routing.jsonl`；Worker `inspect` 按 `session_key == "subagent:<id>"` 读取 `actual_backend`；缺失时验收拦截为“实际 ACP 后端未核实” | `poc.py:183`；`worker.py:287-295`；`engine.py:180-182` |
| F15 | Team API 只接受远程 Git 来源（https / ssh / `user@host:path`），不接受宿主机路径 | `domain.py:271-283` |
| F16 | Worker 注册规则：与平台同一宿主账号的 Worker 仅允许管理员个人空间以 `trusted-local` 使用；Team Worker 必须声明 `dedicated`；不同空间不能共用同一宿主账号 | `server.py:202-228` |
| F17 | kiro-cli 的登录态不依赖 `KIRO_HOME`；Agent 定义从 `KIRO_HOME/agents` 读取（另查找工作区 `.kiro/agents`） | 2026-10-11 本机只读探测 `kiro-cli whoami` / `agent list` |
| F18 | 本机 `codex` 不在 PATH；可用的 Codex CLI 是 codex-acp 依赖的 `node_modules/.bin/codex` | 2026-10-11 本机探测 |
