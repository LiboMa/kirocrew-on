# 模型自动发现：每个执行环境公布 Coding 工具真正能用的模型（D51）

## 1．背景

负责人 2026-10-20：定义工作流时模型可以在 Prompt 中写明，也可以在界面中指定；「自动」时下拉菜单没有模型列表。界面已改为列出执行环境公布的
模型（092a19a）。负责人：「自动刷新模型，不需要任何人为干预！」

EC2 核对（2026-10-20）：
- Worker 的模型列表取自 KiroCrew 的 `provider_models.json`——KiroCrew 只在某个后端**真正开过一次会话**（ACP `session/new`）后才记下它公布的模型
  （`model_registry.seed_available_models`：冷缓存返回空，不回退到静态表）。personal / w1 有 claude 10 个、kiro 21 个；新边界 owner 全部只有 `auto`。
- Worker 派发时只接受列表内的模型（`worker.py` dispatch）：冷边界上指定任何具体模型都会被拒绝。
- codex 后端从不公布模型；它的「自动」就是边界 `~/.codex/config.toml` 的 `model`（`openai.gpt-6-astra`，Bedrock）。
- 平台每 10 分钟读取 Worker 健康并保存为执行环境能力（D43 `Engine.probe`）。

## 2．已确定

| 编号 | 决定 |
|---|---|
| R1 | Worker 进程（`octopus worker`）启动一个后台线程：启动 60 秒后、之后每 15 分钟检查一次。无须任何人操作。 |
| R2 | 对每个**可用**（未停用、命令存在）、KiroCrew 尚未公布模型的工具（kiro、claude、opencode；codex 不公布，不探测）跑一次探测：在专用父会话 `octopus-models` 下 `POST /api/spawn`，固定提示「模型发现探测：不要读取或修改文件，不要调用工具，只回复 OK」、`max_turns: 1`、`keep: false`、不带记忆 / lessons / 项目、`silent`。KiroCrew 在会话开始时记下该后端公布的模型，下一次健康检查即可见。 |
| R3 | kiro 只在 kiro-cli 登录正常（`tool_auth == ok`）时探测：登录过期或尚未登录时不打开登录流程，登录后的下一轮自动探测。 |
| R4 | 不与真实任务争用：该 Gateway 有任何运行中的子任务时本轮跳过。每个工具一次探测后 2 小时内不重复（成功后已有列表，不再探测）；结果记在 Worker 数据目录 `model-discovery.json`（时间、结果、原生 ID 或错误类别）。 |
| R5 | 健康信息增加 `model_defaults`：`{codex: <config.toml 的 model>}`（读不到时省略）；平台的 `/v1/placement` 选项带上 `defaults`，控制台在「自动」后写明它实际对应的模型（如「自动（openai.gpt-6-astra）」）。 |
| R6 | 探测在某人的边界上以此人的身份、用此人的额度运行（每个工具约一轮）：属平台固定的维护动作，不是他人下达的指令（D49 R10 不适用）。 |

## 3．测试与验收

- **本机**：冷且可用的工具各探测一次（codex 不探测，停用的不探测）；已有模型的工具不探测；kiro 登录不正常时不探测；有运行中的子任务时不探测；
  2 小时内不重复；探测失败记下原因、不抛出；`model_defaults` 读自 codex 配置；`/v1/placement` 选项带 `defaults`；控制台「自动（…）」。
- **EC2**：部署后不做任何操作，owner 边界的 claude 在 25 分钟内（60 秒 + 一次 10 分钟健康检查）出现模型列表；kiro 在 `kiro-login --boundary owner`
  之后的下一轮出现；personal / w1 不发生探测（已有列表）。
