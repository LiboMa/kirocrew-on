# 新旧功能对照表

整理日期：2026-10-11。行号以迁入基线 `3a84485` 为准（之后的提交未改动下列模块）。
状态：`已复用`（新版已有）· `部分实现`（新版只有一部分）· `缺失`（只有旧版有）· `计划淘汰及替代`。
“新增”表示旧版没有、新版独有的能力。

| 功能 | 旧版（8917 / 8916） | 新版（8918 / 8928） | 状态 | 归属里程碑 |
|---|---|---|---|---|
| Web 引导构建 | `ui/workflow-app.js:showStage 323-327`（三步向导）、`showBuilder 328` | `octopus/ui/app.js:flowEditor 149-178`（单个模态框）、`octopus/ui/launch.js:launchComposer 4` | 部分实现 | M3 控制台（以查看为主，编辑保留入口） |
| JSON / YAML / Markdown 导入导出 | `workflow_files.py:parse_document 30 / emit_document 77`；`workflow_library.py:import_workflow 112 / portable 122`；`workflow.py` `/api/workflow/import 1086`、`/export 1088` | `octopus/ui/app.js:175-176` 只在浏览器端导入导出 JSON，无服务端接口；`designs.py:parse_recommendation 31` 复用解析 | 部分实现 | M3（CLI 与 API 支持三种格式） |
| 工作流版本库 | `workflow.py:save 173-196`、`latest 156`、`change 199`；`workflow_library.py:delete_workflow 50` | `domain.py:save_workflow 145-165`、`archive_workflow 227-234`、`resolve_workflow 180-186`；`db.py` workflows / workflow_versions | 已复用 | — |
| Session 列表与搜索 | `workflow.py` `/api/runs 1053-1056`；`workflow_library.py:session_meta 69 / archive_sessions 76`；`ui/workflow-app.js:renderSessions 92-140`（搜索、按工作流筛选、归档） | `domain.py:Service.runs 320-329`（逐行权限检查，无分页）；`app.js:renderNav 57-62`（只有列表） | 部分实现 | M3（服务端过滤、全文检索、分页） |
| 1–8 阶段与阶段级工具 / 模型 / Effort | `workflow.py:validate 120-153`、`validate_model_selection 56`、`require_runtime 167-170` | `domain.py:steps 28-46`；`worker.py:dispatch 158-164`（按公布能力校验模型） | 已复用（规则差异：旧版设 Effort 时要求具体模型，新版无此条） | M2 并入 Pipeline schema 2 |
| 意图生成与采纳 | `workflow_library.py:start_design 239 / _generate 266 / design_status 287`；`ui/workflow-app.js:521-545` | `designs.py:create 10 / parse_recommendation 31 / status 41 / instructions 62`；`ui/recommend.js` | 已复用 | M2 扩展为 Pipeline 自动生成（见 GENERATION-REFERENCE） |
| 模板 | `ui/workflow-templates.json`（12 个） | `intent.py` CORE 3 个 + 旧版 12 个 = 15 个（`catalog 54-58`）；编辑器只给 chain / proof / fix | 已复用 | M2 映射为 Pipeline 角色骨架 |
| 固定四阶段 Pipeline 与确定性门禁 | `pipeline_spec.py:STAGES 3-10`；`pipeline.py:checkpoint 342-465 / tests 321 / validate_review 331` | 无；只有 `engine.advance 168-201` 的通用验收 | 缺失 | M2 `octopus/gates.py` |
| 规划锁定哈希 | `pipeline.py` 校验 364-372、生成 frozen 396-399、下游失效 449-460 | 无 | 缺失 | M2 `artifacts_locked` |
| 源码准备 | `workflow_workspace.py:prepare_project 85-148 / normalize_source 44` | `worker.py:prepare 98` 复用；`domain.py:start 271-283` 只接受远程 git | 已复用 | M2 加入 Feature 分支 |
| 原 Session 续作 | `workflow_sessions.py:continue_session 137-211` | `domain.py:operate("continue") 404-431`；`mcp.py:40`；`app.js:continueRun 186` | 已复用 | — |
| 阶段重试 | `workflow.py:retry_stage 244-386`、`/api/retry 1097-1099` | 并入续作（`domain.py:404-431`），新 Attempt 见 `engine.py:128-135` | 已复用 | — |
| 停止 / 关闭 / 重开 / 删除 / 导出 | `workflow_sessions.py:control 213-282 / backup 118` | `domain.py:operate 394-462`；`engine.py:92-107`；`worker.py:_control 330-389 / backup 391-427`；`server.py` export 245-251 | 已复用（导出只剩 JSON） | — |
| Session ZIP 文档（会话、开发手册、Release Notes、功能清单） | `workflow_library.py:session_documents 136-186 / export_sessions 189-224` | 无；`server.py:245-251` 只输出 `octopus.session/1` JSON | 缺失 | M2 Feature 报告吸收 |
| 原生入口 crew-entry | `api.py:chat_entry_url 60-68`；`workflow.py:app_entry 77-80` | `crew_entry.py:create 34-72`；`worker.py:crew_entry 134-151`；`ui/crew.js` | 已复用 | — |
| 用户 / Token / Team / 共享 | 无（单机 Host / Origin 校验，`workflow.py:1002-1006`） | `security.py`；`server.py:91-143`；`domain.py:55-225` | 新增 | M4 扩展 Session 共享与组织 |
| SSE 事件流 | 无（轮询，`ui/workflow-app.js:poll 265`） | `server.py:events 269-292`；`client.py:events 32-45` | 新增 | — |
| Coordinator 纠错 | 只在提示词约定 `spawn_continue` 最多两次（`pipeline.py:221-226`） | `recovery.py`；`engine.py:110-111, 215-216` | 新增 | M2 扩展为 L0–L4 恢复阶梯 |
| MCP 工具 | 无（App Agent 调 `workflow.py` CLI；原生 kirocrew-core） | `mcp.py:TOOLS 16-59`（13 个） | 新增 | M3 扩充并接入四个客户端 |
| Python client | 无 | `client.py:Client 10-45`；`octopus request / events / mcp` | 新增 | M3 扩展为 `octopus` CLI |
| `build_pipeline_kit.py` 分发包 | 旧版发布脚本 | 未迁入 | 计划淘汰及替代 | 由本仓库源码与部署材料替代 |
| `*.command`、`入口.html` 启动器 | macOS Finder 启动器 | 未迁入 | 计划淘汰及替代 | `deploy/systemd/` + `tools/local_stack.sh` + 控制台 |
