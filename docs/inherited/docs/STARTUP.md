# 本地启动、更新与手动重启

更新：2026-10-10。适用于目前已支持的 Apple Silicon macOS 本机部署。
本页依据 `setup.sh`、`launch.py`、`workflow_launch.py` 和 `reload_gateway.py`
的实际行为编写；不涉及 EC2 部署。

## 本地统一入口

双击仓库根目录的 [`打开工作台.command`](../打开工作台.command)，或直接打开
[`入口.html`](../入口.html)，选择 KiroCrew（5476）、旧版（8917）或新版（8918）。
三个独立 macOS 网页快捷方式位于 [`入口/`](../入口/)。
这些文件打开已运行的服务；服务启动或重启仍按下面的运行目录与命令执行。
新版 Worker（8928）是后台服务，其启动说明见 [MVP-1.0.0 启动与操作](MVP-1.0.0-STARTUP.md)。

## 先确认运行目录

**所有重启命令都在原来的运行目录执行。** Git 仓库目录和运行目录可能是两个目录：

| 目录或文件 | 用途 |
|---|---|
| Git checkout | 拉取、合并和验证框架源码 |
| 运行目录下的 `state/runs/` | 已有 Workflow Runs、需求、源码检出及交接 |
| 运行目录下的 `state/workflow-versions/` | 不可变工作流版本 |
| `state/gateway-runtime.json` | 此部署管理的 Gateway 身份 |
| `state/workflow-server.json` | 此部署管理的工作台进程 |
| `~/.kiro/crew/` | 原生 Sessions、配置与 Memory 等数据 |
| `~/.kiro/agents/poc-*.json` | 原生 Agent 模板 |

可以用以下只读命令定位当前端口及进程，再查看其启动路径：

```bash
lsof -nP -iTCP:5476 -sTCP:LISTEN
lsof -nP -iTCP:8917 -sTCP:LISTEN
ps -p <上面查到的PID> -o command=
```

`5476` 是 Gateway，`8917` 是 Workflow Studio；旧 Pipeline 的 `8916` 独立保留。
切换到一个没有原 `state/` 的 checkout 会显示另一份工作台历史；
这不代表原生对话被删除，也不是迁移完成。

已核对的 KiroCrew 版本为 `0.7.1`、`0.7.2`、`0.8.0`，统一维护在 `poc.SUPPORTED_VERSIONS`。
2026-10-10 的重启发现本机已安装 0.8.0，旧接入层仅允许 0.7.x，导致进程重启后拒绝启动。
现在已在原生六项契约检查通过后增加 0.8.0 支持；其他未知版本仍需先核验。
0.8.0 的 MCP 检查返回 `NativeControlPlaneMount`，契约测试同时核对 `elements` 与 `withheld`，
不忽略原生拒绝挂载的原因。此次没有安装或降级 KiroCrew，也没有重建会话目录。

## 已部署环境：手动重启并运行

代码已更新到运行目录、Gateway 当前仍在运行时：

```bash
cd /absolute/path/to/existing-runtime
python3 reload_gateway.py --wait 30 && python3 workflow_launch.py
```

将第一行替换为本机原运行目录。两个命令按成功顺序执行：

1. `reload_gateway.py` 最多等 30 秒，检查聊天、子任务、原生队列和权限请求。
   全部空闲且进程确属此部署后，才优雅重载 Gateway；核对原有对话仍可见。
2. `workflow_launch.py` 复用已启动的 Gateway，生成工作台脚本包，重启自己管理的工作台观察器，
   登记尚未保存的新工作流定义，检查首页与脚本返回成功后打开页面。

有任务执行或待处理权限时，重载会拒绝并说明原因。先在原 App 完成或按任务要求停止工作，
再运行同一命令。不要通过删除 `state/`、清空 Sessions 或强制结束其他 Gateway 解决端口冲突。

**Gateway 已正常停止时**，直接启动：

```bash
cd /absolute/path/to/existing-runtime
python3 workflow_launch.py
```

也可以双击该目录的 `Workflow Studio.command`。它复用已有 Gateway，不保证每次
双击都重载 Gateway。需要加载 `poc.py` 等进程内修改时，使用前面的完整重启命令。
若发现缺少所需 Effort 桥接，启动器会尝试既有的空闲重载流程。

## 拉取更新与保留本地修改

在 Git checkout 中执行：

```bash
git status --short --branch
git pull --ff-only origin main
```

有本地修改时，先备份或保存到明确命名的 stash，再拉取并合并。保留 stash，
直到确认本地修改已恢复、冲突已解决且测试通过。`git pull` 不会自动把源码
同步到另一个运行目录，也不会重启已启动的 Python 进程。

如果 checkout 与运行目录分离，更新时只同步经过验证的框架代码、网页资源和文档。
保留运行目录的 `state/`、`backups/`、依赖、认证及用户配置；不要使用全目录删除式同步。
已有 `workflows/*.json` 通过 `save --expected-revision` 生成新版本，不直接覆盖。
新增定义文件可复制到运行目录的 `workflows/`，启动器会在首次启动时登记它。

工作台使用 `build_workflow_ui.py` 将 `workflow-progress.js`、`workflow-editor-store.js`、
`workflow-session-ops.js` 和 `workflow-app.js` 合并成 `ui/workflow.js`。HTML 只请求既有的主脚本路由和
`workflow-view.js`，避免旧 Python 静态路由尚未更新时发生
`WorkflowProgress is not defined`。修改这些源文件后运行：

```bash
python3 build_workflow_ui.py
```

启动器、直接 `workflow.py serve` 和打包命令也会自动生成。CI 用 `--check`
检查生成文件是否与源码一致。源码、生成脚本、HTML 和 CSS 应一并同步到运行目录。
只刷新网页不能让旧 Python 服务获得 `/api/retry`；前端会读取
`/api/runtime` 的 `workbench_features`，旧后台未公布阶段重试时禁用按钮并提示手动重启。

## 第一次在新环境启动

前提：已安装并初始化 KiroCrew 0.7.1 / 0.7.2 / 0.8.0，Python 3.12 或 3.13、
Node/npm 可用，所选 Coding 工具分别完成登录。各工具继续使用自己的认证。

```bash
git clone https://github.com/LiboMa/kirocrew-octopus.git
cd kirocrew-octopus
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-workflow.txt
./setup.sh
python workflow_launch.py
```

`setup.sh` 安装锁定版本的 ACP 适配器，备份已有 Crew 配置，
设置独立 Session 分配并加入当前运行目录的允许路径。它不是每次重启都需要执行的命令。
Gateway 使用 KiroCrew App 内置 Python；工作台使用运行启动器的 Python。
通过 `.venv` 安装依赖后，在该环境中启动工作台。

如果普通 KiroCrew 或另一份部署占用 `5476`，启动器会拒绝接管。
先识别端口归属，在原 App 正常退出或按原部署流程停止，然后再启动。
本机已有历史部署时优先使用前面的重启流程，不用新环境步骤替换历史目录。

## 启动后检查

```bash
curl --fail http://127.0.0.1:5476/api/ready
curl --fail http://127.0.0.1:8917/api/runtime
```

浏览器打开 `http://127.0.0.1:8917/`，必要时强制刷新：

- 原有 Workflow 与 Sessions 仍在。
- 中央只有一个“编辑当前工作流”入口；目标页只有一个“本次开发需求”框。
- 在两条 Session 之间切换，需求、任务草稿和生成状态各自恢复；新 Session 需求为空。
- 原 Session 续作与运维按钮需要 `/api/runtime` 公布 `session_operations_v1`。
  未加载时仅需在原运行目录执行 `python3 workflow_launch.py` 重启工作台；
  本次新增功能使用现有原生接口，无需为此重载 Gateway。
- 左侧 Session 环形进度与总览一致，按 accepted 阶段数计算。
- 新建 Run 先显示项目准备；源码就绪后使用 `workspace/project`。
- 工作流列表新增「UI 增强 · Kiro → Codex → Claude」，三个工具默认使用 `auto`。
- 失败、交接未通过或已交接阶段可以登记重试。复制续作指令，在**原 KiroCrew 对话**
  中执行；按钮本身不会直接启动 Coding Agent。
- 重试保留源码基线和已有代码，归档旧交接，并使下游结论失效。新版 Run 只有在旧会话
  工作目录与项目目录匹配时才复用会话；否则给出正确目录下的新建任务参数。

原有 Run 保持冻结配置；编辑工作流只对新 Run 生效。`auto` 使用工具默认模型，
不能据此断言使用了某个未被原生接口核实的模型偏好。

## 日志与常见问题

| 现象 | 检查 |
|---|---|
| 页面未打开 / 8917 启动失败 | `state/workflow-server.log`；确认工作台 Python 安装了 `requirements-workflow.txt` |
| Gateway 不就绪 | `state/gateway-original.log`；确认 App 内置 Python 存在、工具已登录 |
| 「5476 不是当前 PoC 的 Gateway」 | 回到原运行目录，核对进程路径和 marker；不要伪造 marker 接管 |
| `WorkflowProgress is not defined` | 核对运行目录的 `ui/workflow.js` 是否为生成包；运行构建命令并同步 HTML / JS，刷新网页 |
| 阶段重试按钮提示后台未加载 | 手动重启工作台，核对 `/api/runtime` 包含 `workbench_features: ["stage_retry_v1", ...]` |
| 新工作流未出现 | 新定义是否复制到原运行目录；重新运行启动器完成首次登记 |
| 仓库准备失败 | 原 App 内查看 Git/认证/ref 错误；保留当前 Run 后恢复准备 |
| 新代码已拉取但页面还是旧版 | 检查 Git checkout 和运行目录是否相同，及服务进程是否已经重启 |

合并后本地检查：

```bash
python3 -m unittest test_workflow test_workflow_workspace test_workflow_library test_pipeline test_reload_gateway test_workflow_review test_workflow_sessions -q
python3 build_workflow_ui.py --check
node --test tests/workflow-editor-store.test.js
node --check ui/workflow.js
node --check ui/workflow-progress.js
node --check ui/workflow-view.js
python3 build_pipeline_kit.py
```

这些回归使用临时目录与模拟原生响应；不会重启用户服务或自动运行 Coding Agent。
