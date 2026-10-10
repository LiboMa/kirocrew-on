# 主要调用链

整理日期：2026-10-11。行号以迁入基线 `3a84485` 为准。

## 1．新版提交与执行（8918 → 8928 → 原生）

1. `server.py:dispatch 229-231`：`POST /v1/runs` → `service.start`。
2. `domain.py:start 249`：`resolve_workflow 167`（模板经 `intent.preset_steps`）→ `normalize_source 272` 校验来源 → `_command 236` 按 `request_id` 幂等 → `execute 288-309` 写 runs（有 Worker 时 `preparing`）、tasks、revisions，事件 `run.created`。
3. `engine.py:start 20-25` → `loop 27-34`（每秒）→ `tick 54`：`recovery.reconcile 55`，查询活跃 Run（56-61），逐个 `advance`（62-64）。
4. `advance 90`：无 `native_parent` 时 `/prepare`（113，超时 240 s）→ 状态 `ready`（114）。
   Worker `prepare 83`：`workspace.prepare_project 98` → `POST /api/chat/slots`（poc-coordinator，108-109）→ `/context 111` → `/note 120`。
5. 找第一个未验收的 Task（120）→ 新建 Attempt（128-135）→ 上游结果（137-139）→ `recovery.feedback 140` → `/dispatch`（144-148，契约 ≥2 时带 quality）→ 写结果（150-156）。
   Worker `dispatch 153`：模型校验 158-164 → `dispatch.json` 幂等 167-174 → 写 instructions / context 200-210 → **先落盘再产生副作用**（216）→ 原生 `/api/spawn` 218-226 → 回执分类 229-241 → 必要时按 Attempt 标记核对（245）。
6. 下一周期 `/inspect`（158）。Worker `inspect 256`：`/api/spawn` 265 → subagents `state.json` 267-272 → `/api/spawn/<id>` 277 → 路由日志 287-295 → handoff 298-303 → 父会话 note 304-319 → outcome / decision 320-323。
7. 验收门禁 `engine.py:170-201`：原生错误或停止 → 结果为空或含 BLOCKED → 缺 handoff → 实际后端不符 → 实际模型不符 → Effort 不符 → `outcome.json` 校验（`recovery.outcome 24-43`）→ `accepted` / `blocked`，写库 202-213。
8. `blocked` → `recovery.automatic`（215-216，定义于 `recovery.py:129-138`）→ `schedule`。

## 2．新版纠错

1. 入口：自动 `recovery.automatic`；手动 `domain.py:operate("recover") 432-443`。
2. `recovery.py:schedule 98`：读策略（99）；已有进行中纠错则返回（101-103）；自动模式下纠错关闭、超过 `max_cycles`、失败类型属于 `NEEDS_INPUT`、同一 Attempt 已排过时跳过（105-108）；组装 context（111-118）→ 写 recoveries（pending，120-122）→ Run 置 `diagnosing`（123）。
3. `engine.py:110-111` 见 `diagnosing` → `Coordinator.advance`（`recovery.py:175`）。
4. pending / dispatching：`/dispatch` 带 coordination（185-191），Worker 选 `poc-coordinator`（`worker.py:181-185, 220`）；被拒或 blocked 直接 `finish`（192-203）。
5. `/inspect`（205）：未完成更新 running / waiting_approval（207-212）；完成后保存结果（213-215）。
6. `decision.json` 校验（217-226）：原生无错、有 handoff、`actual_backend == kiro`、≤64000 字符合法 JSON、字段只允许 action / target_task_id / reason / instructions、action ∈ retry / rework / wait_for_user、reason 与 instructions 非空且 ≤8000。
7. `finish 231`：Run 已不活跃或失败 Attempt 已被替换 → cancelled（237-240）；目标必须存在且为失败 Task 或其上游，retry 只能针对当前 Task（242-247）；出错或 `wait_for_user` → blocked（248-254）；否则从目标起重置为 pending（255-258）、Run 置 ready 且 version+1（259-261）、写 revisions 与 `recovery.applied`（262-268）。
8. 下一次派发时 `recovery.feedback 161-168` 把修复要求放入 `repair`，Worker 在 190-193 拼入提示词。

## 3．旧版 Workflow（8917）

1. `workflow.py:serve 973` 启动 `observe` 线程（976）；`POST /api/start 1110-1143`：请求 ID 回执幂等（1115-1121）、校验（1124-1129）。
2. `prepare 455`：运行目录与 manifest → `POST /api/chat/slots`（poc-coordinator，469-471）→ `control_prompt 405` 写入 `/context`（475）→ intake `prepared`（478）。
3. `submit 482`：`capture 394` → `/api/chat?ws=1`（491）。
4. 父 Agent 执行 `workflow.py next`（`main 1206` → `next_step 501` → `_next_step 506`）：未准备源码时返回 `workspace_required`（516-520），父 Agent 执行 `workflow.py workspace`（1203-1205）；生成指令（546-577），任务正文 ≤5000 字符（580-592），返回 spawn 字段（594-598），写 `dispatch-<step>.json`（605-607）。
5. 父 Agent 调用原生 MCP `spawn_run`；组合层 `poc.py:create 151` 路由并写路由日志（173-184）。
6. `observe 895`：只读 `/api/spawn`、`/api/approvals`、`/api/chat/slots`（896-917）→ `_project 797`（经 `pipeline.route_for 813`）→ `workflow-live.json`（920）。
7. 父 Agent 执行 `workflow.py accept --task-id`（`accept 687` → `_check_and_accept 692` → `_accept 619`）：快照 20 s 内（621-623），核对归属、Agent、后端、模型、cwd、Effort、BLOCKED、handoff（624-669），写 `accepted/<step>.json`（670-684）与 `checks/<task>.json`（699-702）。
8. 重复 `next` 直到 done（609-610）。

## 4．旧版 Pipeline（8916）

1. `pipeline.py:serve 675`，`POST /intake 745-785`：Origin 与自定义请求头（707-717）、request_id（757-758）、`validate_request 148`、回执去重（767-774）。
2. `new_run 178`：目录（184-187）→ manifest schema 2（188-195）→ `capture_request 156`（197）→ `tasks/<stage>.txt`（198-199）→ 四阶段协议提示（201-228）→ slot（poc-pipeline-coordinator，230-233）→ `/context`（236-237）→ `intake.json`（239）。
3. `Observer.refresh 630` 写 `live-state.json`（643）；`submit_run 246` → `/api/chat?ws=1`（255）。
4. 父 Agent 按 STAGES 依次 `spawn_run`，每阶段后执行 `pipeline.py checkpoint --stage --task-id`（`main 864-869`，exit 0 / 1）。
5. `checkpoint 342`：快照 15 s 内（265-278）→ Agent 与阶段标记（351-352）→ completed（353-354）→ `route_for` 实际后端（355-358）→ 前序通过（360-362）→ 基线 / REQUEST / TASK / frozen 哈希（363-372）→ 各阶段判定（plan 测试先失败 376-400；code / review / deliver：`tests 321`、真实 diff 414-419、`validate_review 331`、交付物 436-440）→ 下游失效（449-460）。
6. 写 `gates/<stage>.json` 与 `gate-history.jsonl`（461-463）→ `report 468`（report.json 484、report.md 510、report.html 520）。

## 新旧对照的关键差异

| 维度 | 旧版 | 新版 |
|---|---|---|
| 谁推进阶段 | 父 LLM 遵守 `next / accept` 或 `checkpoint` 协议 | Engine（唯一业务派发者） |
| 状态来源 | 文件快照 + 15–20 s 新鲜度窗口 | SQLite 事务 + Worker 落盘记录 + 原生核对 |
| 质量门禁 | Pipeline：确定性产物门禁；Workflow：只核对身份与交接 | 通用验收 + `outcome.json`；确定性产物门禁待 M2 |
| 失败处理 | 提示词约定的续接 | Coordinator 诊断 + Engine 校验 |
