# M1 EC2 验收记录（us-east-1，经 CloudFront）

日期：2026-10-11　·　栈：`kirocrew-on-m1`　·　决策：D15–D18　·　脱敏摘要，不含密钥、Token 与需求以外的内容。

## 环境

| 项 | 值 |
|---|---|
| 入口 | `https://d1atltv3sl7jdu.cloudfront.net/`（CloudFront 默认证书）→ 安全组（CloudFront origin-facing 前缀列表）→ EC2 `i-0f2cc505786160cc8`（EIP 35.175.129.161） |
| 实例 | m7i.xlarge（4 vCPU / 15 GiB，候选规格），100 GiB gp3 加密卷，Amazon Linux 2023.12，内核 6.18，glibc 2.34 |
| 沙箱前提（K12） | `user.max_user_namespaces = 62702`，`unshare -Ur true` 成功 |
| KiroCrew | 0.10.0，官方源码 `57df464b2b4ce3fe4e71d108281ff6c3ad22c40f`，在实例上构建（含 dashboard 前端），原生契约 6/6，边界配置接受 0.10.0 |
| 工具 | Node v22.23.3；kiro-cli 2.29.1（官方清单 sha256 校验）；Claude Code、Codex、OpenCode（npm 固定版本）；ACP 适配器按 `package-lock.json` |
| 认证 | Claude Code 与 Codex 经实例角色使用 Amazon Bedrock（无需登录）；Kiro CLI 待设备码登录 |

## 结果

| # | 场景 | 结果 | 证据 |
|---|---|---|---|
| 1 | 栈创建 | 通过 | `CREATE_COMPLETE`；后续一次增量更新（IAM） |
| 2 | 安装（`deploy.sh push`） | 通过（第 4 次） | 前 3 次暴露并修复了 E1–E3；最终：路由检查 6 条、原生契约 6/6、三个服务 active、Worker 注册；重复执行幂等（管理员 / Worker 已存在时跳过） |
| 3 | 入口与安全边界 | 通过 | CloudFront `/health` 200、控制台 200；HTTP→HTTPS 301；直连 `EIP:8918` 超时（安全组）；实例内非回环直连：无密钥 403、错误密钥 403、未知 Host 421 |
| 4 | 经 CloudFront 登录与 API | 通过 | 管理员登录、`/v1/workers`（`spawn_approval = auto`，`routing_evidence = true`） |
| 5 | Claude 编码 → Codex 审查（经 CloudFront） | 通过 | `run-1fd6e7ac…`：claude pass → codex（`openai.gpt-6-astra[low]`）pass |
| 6 | SSE 经 CloudFront | 通过 | 单连接约 31 s 完整保持，推送 17 条事件 |
| 7 | 编码中重启 API 与 Worker | 通过 | `run-150e3b11…`：两次重启后完成，每个 Task 1 次 Attempt |
| 8 | 备份与独立目录恢复 | 通过 | 数据库备份 integrity ok；`restore-verify` ok；Worker 归档 67 个文件；备份上传 S3 `backups/` |
| 9 | Kiro 冒烟、Coordinator 返工、三阶段流水线、Gateway 中途重启 | 待执行 | 需要 kiro-cli 设备码登录 |
| 10 | OpenCode 单独核实 | 待执行 | 本地未通过（K01） |
| 11 | 实例重启后自动恢复 | 待执行 | — |

## EC2 上发现并修复的问题

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| E1 | 原生契约：协调者控制面挂载为空 | 全新 `KIRO_HOME` 尚无 `poc-*` 模板 | 安装时先以执行用户运行 `composition --check`（写模板并核对路由），再跑契约 |
| E2 | Worker 换取原生 token 403 `member_owner_token_refused` | KiroCrew 0.10.0 在 Linux 上要求调用方与 Gateway 的 user + mount 命名空间一致；`PrivateTmp` 为每个单元新建挂载命名空间 | Gateway / Worker 单元移除 `PrivateTmp`（与无效的 `ReadWritePaths`） |
| E3 | Gateway 拒绝启动（`live_target.json` 存在） | 0.10.0 的 Gateway 启动时自行写入 `{"checkout": null}`，原检查误判 | 改为设置 KiroCrew 原生 exec 链标记 `KIROCREW_LIVE_EXECED`，从根本上禁止 re-exec |
| E4 | Codex 审查失败（“not signed in”） | Codex 的 amazon-bedrock 提供方鉴权走 `bedrock-mantle:CreateInference` | 实例角色授予该权限（限定本账号 us-east-1 的 `project/*`） |

## 观察

- Codex 提示未找到系统 bubblewrap，使用自带版本运行；KiroCrew 的沙箱独立生效。
- Claude Code 经 Bedrock 时原生报告的模型为 `default`（请求为 `auto`）。
