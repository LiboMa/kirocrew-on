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
| 认证 | Claude Code 与 Codex 经实例角色使用 Amazon Bedrock（无需登录）；Kiro CLI 以 `crew-personal` 完成 IAM Identity Center 设备码登录（Pro） |

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
| 9 | Kiro 冒烟 | 通过 | `run-5eb6dacc…`：kiro，原生 `4e5a49c6…`，pass |
| 10 | Kiro 规划 → Claude 编码 → Codex 审查 | 通过 | `run-e433100d…`：kiro pass → claude pass → codex（`openai.gpt-6-astra[low]`）pass |
| 11 | 审查退回 → Coordinator 返工 → 复审 | 通过 | `run-317da209…`（确定性场景：编码提示禁止创建 CHANGELOG.md，审查要求存在）：review#1 `changes_requested` → `recovery.applied` 目标 code → code#2 → review#2 pass，Session 版本 2。首次尝试 `run-cf09c02a…` 一次通过、未触发返工 |
| 12 | 编码中重启 Gateway | 通过（自动恢复） | `run-e4c033fb…`：重启后 10 s 内 Worker 报告“Gateway 暂不可用，稍后自动重试”，原生将运行中任务标为 cancelled → code#1 blocked → 诊断 → code#2 accepted → review accepted → completed |
| 13 | 编码中整机重启（`aws ec2 reboot-instances`） | 通过（自动恢复） | `run-ff93cab2…`：重启后约 30 s 经 CloudFront `/health` 恢复 200，三个服务 enabled 且 active；code#1 cancelled → 诊断 → code#2 accepted → review accepted → completed |
| 14 | 无重复派发 | 通过 | #11–#13 的 10 个 Attempt 在原生 `subagents/` 中各恰好 1 个带 `OCTOPUS-ATTEMPT` 标记的子 Agent；#7 同样每个 Task 1 次 Attempt |
| 15 | 并发 | 通过 | Kiro 冒烟 `run-5eb6dacc…` 与 OpenCode `run-b2445891…` 的首个 Attempt 同时开始、并行执行（39 s 与 13 s 区间重叠），互不阻塞 |
| 16 | OpenCode 单独核实 | **未通过** | `run-b2445891…`：`opencode/big-pickle` 三个 Attempt 均 blocked（“工作者未完成任务或报告 BLOCKED”）。原生审计显示 read / ls 被只读分类器自动批准，而用 shell heredoc 写 `index.html` 的调用被 KiroCrew 工具门禁以 `hook_deny` 拒绝；OpenCode 只收到“rejected permission”而无原因，随即以 `_No response._` 结束。复现 K01；替换配置（deliver→claude）本地已验证 |
| 17 | DLM 快照 | 待核 | 策略 ENABLED；栈创建于 03:52 UTC，晚于当日 03:00 执行点，首个快照在 2026-10-12 03:00 UTC 后出现 |
| 18 | 原生入口（`deploy.sh tunnel`） | 未执行 | 需要本机 Session Manager 插件与浏览器；平台入口已覆盖 M1 研发流程 |

## EC2 上发现并修复的问题

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| E1 | 原生契约：协调者控制面挂载为空 | 全新 `KIRO_HOME` 尚无 `poc-*` 模板 | 安装时先以执行用户运行 `composition --check`（写模板并核对路由），再跑契约 |
| E2 | Worker 换取原生 token 403 `member_owner_token_refused` | KiroCrew 0.10.0 在 Linux 上要求调用方与 Gateway 的 user + mount 命名空间一致；`PrivateTmp` 为每个单元新建挂载命名空间 | Gateway / Worker 单元移除 `PrivateTmp`（与无效的 `ReadWritePaths`） |
| E3 | Gateway 拒绝启动（`live_target.json` 存在） | 0.10.0 的 Gateway 启动时自行写入 `{"checkout": null}`，原检查误判 | 改为设置 KiroCrew 原生 exec 链标记 `KIROCREW_LIVE_EXECED`，从根本上禁止 re-exec |
| E4 | Codex 审查失败（“not signed in”） | Codex 的 amazon-bedrock 提供方鉴权走 `bedrock-mantle:CreateInference` | 实例角色授予该权限（限定本账号 us-east-1 的 `project/*`） |
| E5 | 整机重启期间 CLI / MCP 输出 JSON 解析堆栈 | 源站不可用时 CloudFront 返回 HTML 504，客户端按 JSON 解析错误响应 | 非 JSON 错误响应报告为 `504 Gateway Timeout: API 暂不可用，稍后重试`（`tests/test_client_errors.py`） |
| E6 | `kiro-cli login` 无终端时失败（region 校验报错） | 即使给齐 `--license` / `--identity-provider` / `--region`，kiro-cli 仍有交互提示 | `deploy.sh kiro-login` 经 SSM 在主机上以 `script(1)` 提供伪终端、自动回车，读取设备码并等待完成，最后以 `kiro-cli whoami` 确认 |

## 观察

- Codex 提示未找到系统 bubblewrap，使用自带版本运行；KiroCrew 的沙箱独立生效。
- Claude Code 经 Bedrock 时原生报告的模型为 `default`（请求为 `auto`）。
- Kiro 不报告实际模型（K05），证据中为空。
- 原生 `subagents/<id>/state.json` 在任务结束后仍为 `running`（结论以 tombstone 与平台回执为准），仅记录。
