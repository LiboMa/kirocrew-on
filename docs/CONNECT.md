# 让 Coding Agent 连接 KiroCrew ON

Coding Agent 经 MCP 服务器 `kirocrew-on` 提交任务、查看进度、迭代与预览；Skill `kirocrew-on` 告诉 Agent 怎么用这些工具。
Claude Code 与 Kiro CLI 用 `octopus connect` 自动配置（§2），其他客户端按 §3 手动配置。

## 1．共同部分

- **平台地址**：`https://<分发域名>.cloudfront.net`（`deploy/aws/deploy.sh status` 显示）、配置的控制台自定义域名（如
  `https://kirocrew-on.tinyboat.blog`），或 SSM 隧道 `http://localhost:18918`。
- **Token**：`octopus connect --username <你的用户名>` 提示输入密码，以本人账号登录后创建一枚 30 天有效的 Token
  （`read, execute, manage`：查看、提交、管理自己的工作流），随后登出这次登录。未给 `--username` 时交互询问。
  也可以先在控制台顶栏的「我的设置 › API 与 Coding Agent 接入」创建，设为环境变量 `OCTOPUS_API_TOKEN`。
  平台管理员需要经 Agent 管理用户、Team、执行环境时加 `--admin`：Token 另含 `admin`、7 天有效，MCP 才会列出管理员工具；
  这枚 Token 能停用用户、更换执行环境，只在本人设备上使用；它不能新建账号、重设密码、设为管理员或恢复已停用的账号，这些只能在控制台登录后做。
  日常使用的连接建议不带 `--admin`，需要管理时再单独连一个，用完撤销。
- **MCP 服务器**（stdio，所有客户端相同）：

  | 字段 | 值 |
  |---|---|
  | command | `<kirocrew-on>/.venv/bin/python` |
  | args | `-m octopus mcp --url <平台地址>` |
  | env | `OCTOPUS_API_TOKEN=<Token>`、`PYTHONPATH=<kirocrew-on>` |

- **Skill**：模板在 `skills/kirocrew-on/SKILL.md`，渲染后写入客户端的 skills 目录（已有不同内容时先备份）：

  ```bash
  .venv/bin/python -m octopus skill --url <平台地址> --dest <skills 目录>   # 写入 <dest>/kirocrew-on/SKILL.md
  .venv/bin/python -m octopus skill --url <平台地址>                        # 输出到标准输出
  ```

## 2．Claude Code、Kiro CLI、Codex 与 Kiro IDE（自动）

```bash
# Claude Code：MCP 服务器写入 user 范围，Skill 写入 ~/.claude/skills/kirocrew-on/
.venv/bin/python -m octopus connect claude --url <平台地址> --username <你的用户名>
# Kiro CLI：默认 workspace 范围，MCP 写入 <项目>/.kiro/settings/mcp.json，Skill 写入 <项目>/.kiro/skills/kirocrew-on/
.venv/bin/python -m octopus connect kiro --url <平台地址> --username <你的用户名> --project <项目目录>
# Codex：MCP 服务器合并进 ~/.codex/config.toml（只替换 [mcp_servers.kirocrew-on]，其余原样保留），Skill 写入 ~/.codex/skills/kirocrew-on/
.venv/bin/python -m octopus connect codex --url <平台地址> --username <你的用户名>
# Kiro IDE：MCP 服务器合并进 <项目>/.kiro/settings/mcp.json（不需要 kiro-cli），Skill 写入 <项目>/.kiro/skills/kirocrew-on/
.venv/bin/python -m octopus connect kiro-ide --url <平台地址> --username <你的用户名> --project <项目目录>
```

Kiro 默认用 workspace 范围：本机上 KiroCrew 自己的 kiro Agent 共用 `~/.kiro`，全局配置会让它们也加载这个服务器。
`--dry-run` 只显示将执行的操作；Token 在输出中显示为 `****`。被替换的文件先备份为 `<文件>.bak-<时间>`；写入的 Codex 与 Kiro IDE 配置含 Token，权限为 0600。合并不了（如 config.toml 本身有语法错误）时命令报错并吊销刚签发的 Token，改按 §3 手动配置。

## 3．其他客户端（手动）

Codex 与 Kiro IDE 的手动格式留作参考（§2 的命令写的就是它们）。以下格式按各客户端文档整理；配置后以客户端能列出 `octopus_status`、`octopus_start` 等工具为准。

**Codex CLI**：`~/.codex/config.toml`；Skill 用 `--dest ~/.codex/skills`。

```toml
[mcp_servers.kirocrew-on]
command = "<kirocrew-on>/.venv/bin/python"
args = ["-m", "octopus", "mcp", "--url", "<平台地址>"]
env = { OCTOPUS_API_TOKEN = "<Token>", PYTHONPATH = "<kirocrew-on>" }
```

**OpenCode**：项目 `opencode.json` 或 `~/.config/opencode/opencode.json`。

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "kirocrew-on": {
      "type": "local",
      "command": ["<kirocrew-on>/.venv/bin/python", "-m", "octopus", "mcp", "--url", "<平台地址>"],
      "environment": { "OCTOPUS_API_TOKEN": "<Token>", "PYTHONPATH": "<kirocrew-on>" },
      "enabled": true
    }
  }
}
```

**Kiro IDE**：工作区 `.kiro/settings/mcp.json`（与 Kiro CLI 相同，可直接用 §2 的命令生成）；Skill 用 `--dest <项目>/.kiro/skills`。

```json
{ "mcpServers": { "kirocrew-on": {
  "command": "<kirocrew-on>/.venv/bin/python",
  "args": ["-m", "octopus", "mcp", "--url", "<平台地址>"],
  "env": { "OCTOPUS_API_TOKEN": "<Token>", "PYTHONPATH": "<kirocrew-on>" } } } }
```

**其他 MCP 客户端**：按 §1 的 command / args / env 配置 stdio 服务器。不支持 Skill 的客户端，
把 `octopus skill --url <平台地址>` 的输出放进该客户端的说明文件（如 `AGENTS.md`）。

## 3a．命令行（D58）

不用 Coding Agent 时，可以直接用 `octopus` 命令（安装方式同 MCP 服务）：

```bash
octopus login --url https://<平台地址>        # 用户名与密码；保存一枚 30 天的 CLI Token
cd <本地仓库> && octopus submit "实现登录页"   # 源码取 origin 与当前分支；有未推送提交时提示 --push-base
octopus status                                 # 或 plan / confirm / watch / report
octopus pull --checkout                        # 会话分支到本地 octopus/<run>，上下文在 .octopus/sessions/<run>/
# 修改并 git commit 之后
octopus sync --note "改了样式"                 # 远端重新检查，审查重新进行
```

## 4．验证

1. 新开一个会话（或重启客户端）：已运行的会话不会加载新的 MCP 服务器。
2. 让 Agent 调用 `octopus_runs`，应返回你可见的会话列表。
3. 不经客户端检查 Token 与地址：`OCTOPUS_API_TOKEN=<Token> .venv/bin/python -m octopus request GET /v1/runs --url <平台地址>`。

返回 401 时 Token 无效或已过期，重新执行 §2 的命令或换一枚 Token；返回「API 暂不可用」时平台不可达。

## 5．安全

- Token 以明文保存在客户端配置中。项目内的配置（`.kiro/settings/mcp.json`、`opencode.json`）要加入项目的 `.gitignore`。
- Token 30 天有效（`--admin` 为 7 天），可在控制台「API 与 Coding Agent 接入」撤销；不再使用的客户端及时撤销其 Token。
- MCP 工具出错时返回结构化的 `{status, code, error}`（401 重新连接、403 缺角色或 scope、404 不存在或无权查看、409 状态不允许）。
