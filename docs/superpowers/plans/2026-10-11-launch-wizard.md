# 发起开发向导与原生对话入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把控制台“从需求开始构建任务”的单页弹窗改成向导，第一步提供三种编排方式（参考模板 / KiroCrew Agent 推荐并匹配 Prompt / 直接提交 Agent 预先生成的工作流）；修复服务端（CloudFront）无法打开原生 KiroCrew 对话。

**Architecture:** 在现有 `octopus/ui/launch.js` 上改造，不删除既有能力（工作流库、匹配、推荐、分享保持可用）。新增服务端工作流文档导入（`octopus/workflow_import.py`，代码完成解析与规范化，不依赖 Agent），UI、MCP、API 共用。原生入口经第二个 CloudFront 分发 → 主机 nginx（校验源站密钥、WebSocket）→ 回环 Gateway。

**Tech Stack:** Python 3.12 标准库 + PyYAML；原生 JS（无构建）；CloudFormation；nginx（AL2023 dnf）。

**Spec:** `docs/superpowers/specs/2026-10-10-kirocrew-on-design.md`（§7 Pipeline 生成、§10 控制台、§12.5 部署）；负责人 2026-10-11 指示：
“不需要删掉什么，直接修改”；用户发现需求后 ①可参考模板 ②可由 KiroCrew Agent 自动推荐工作流并匹配 Prompt ③可直接提交由 Agent 提前生成好的工作流直接运行（实际最常见）；UI 采用向导式；功能完成前不做大范围功能测试。

## Global Constraints

- 不删除现有功能、接口、数据表与测试覆盖的行为；变更只改形态与入口。
- 可以用代码实现的（解析、规范化、校验、去重）不交给 Agent（原则 5）。
- 1–8 个顺序任务；工具限 kiro / claude / codex / opencode；字段与 `domain.steps()` 一致。
- 开发期只跑单元测试与受影响的浏览器测试；不做 EC2 部署与真实 ACP 验收，直到负责人确认功能完成。
- 本地提交，不推送。

## Review Focus

1. 导入文本含多余字段、缺少 id、工具别名（Claude Code / claude-code）、超过 8 个任务、重复 id：规范化结果确定且可解释，非法输入给出中文原因。
2. 向导步骤间来回切换不丢失已编辑的任务（captureTasks 在离开第 3 步前保存）。
3. 推荐进行中切换编排方式或关闭向导：推荐任务仍可在 Session 中找到，迟到结果不覆盖用户已编辑的任务。
4. 原生入口链接指向公网分发时，`crew.js` 不再误拦；回环链接仍只在本机页面放行。
5. 第二分发只转发带源站密钥的请求；Gateway 只认回环 Host。

---

### Task 1: 工作流文档导入（后端）

**Files:** Create `octopus/workflow_import.py`、`tests/test_workflow_import.py`；Modify `octopus/server.py`（`POST /v1/workflows/parse`）、`octopus/domain.py`（`start` 接受 `document`）、`octopus/mcp.py`（`octopus_parse_workflow`；`octopus_start` 增加 `document`）、`octopus/schema.py`。

**Produces:** `import_workflow(content: str, format: str | None = None) -> {"name": str, "steps": list, "notes": list[str]}`。

- 接受：`{name, steps}`（新版导出）、`{schema: 1, id, name, revision, steps}`（旧版）、仅 `steps` 列表；JSON / YAML / Markdown（恰好一个代码块）；未给格式时按内容判断。
- 规范化：缺少 `id` 按序生成 `task-N`；非法 id 规范为小写短横线；工具别名映射；缺省 `model=auto`、`effort=""`；`description`/`instructions` 作为 `prompt` 的别名；未知字段忽略并写入 `notes`。
- 最终交给 `domain.steps()` 校验。

### Task 2: 向导式发起（UI）

**Files:** Modify `octopus/ui/launch.js`、`octopus/ui/style.css`、`octopus/ui/app.js`（如需辅助函数）；Modify `tests/mvp-intent.browser.js`、`tests/mvp-recommend.browser.js`、`tests/mvp-recovery.browser.js`、`tests/mvp-crew-entry.browser.js`（按步骤导航）；新增导入场景检查。

步骤：① 需求（需求、名称、代码来源、空间、Worker）→ ② 编排方式（参考模板 / KiroCrew 推荐 / 导入已生成）→ ③ 任务与 Agent → ④ 确认（摘要、自动恢复、提交）。工作流模式（保存工作流）同样走向导，第 ④ 步为保存。

### Task 3: 原生对话经 CloudFront 打开（Bugfix）

**Files:** Modify `deploy/aws/kirocrew-on-ec2.yaml`（第二分发、安全组端口）、`deploy/install/al2023.sh`（nginx、`crew_url`、Gateway 允许的 Origin）、`deploy/config/*.example`、`composition/environment.py`、`octopus/worker.py`（入口 token 有效期可配置，默认 1 h）、`octopus/ui/crew.js`；Tests：`tests/test_deploy_assets.py`、`tests/test_worker_*`、`tests/test_composition.py`。

### Task 4: Part B 审查未关闭项（部署前必须完成）

C1 固定 AMI / 可用区 + 变更集替换拦截；I2 管理员密码改为主机 root 文件、实例角色只读 origin-secret；I4 安装合并 `allowed_hosts`；I5 契约检查通过后才切换 `current`；M6 `secret()` 错误不再生成新值；M7 kiro-login 进程替换与每次独立日志；M10 SNS 去掉 aws/sns 托管密钥；M11 文档。
