# M4-4 第 1 期：排队可见、看门狗不计排队（D60，设计稿）

日期：2026-10-22。负责人：「继续完成 M3、M4、M5 的任务，尽可能自动，不要停下来」。范围是看板 M4-4「排队与配额」的第 1 期，
即 MVP 1.1.0 调研报告（run-a556e00f…，REPORT.md §6）「最先实施」的任务一；配额（每人 / 每 Team 的并发与上限）是第 2 期。

## 1. 现场问题（2026-10-21 EC2）

owner 边界内存不足时，KiroCrew Gateway 推迟新的子 Agent 启动约 40 分钟。期间会话只显示「运行中」，错误是
「原生任务归属当前不可核实」；看门狗按「没有进展」计时，最后记 `never started: waiting for memory`，纠错把它当成普通失败。

## 2. KiroCrew 0.10.0 已经告诉我们的（EC2 实测源码）

- `POST /api/spawn`：被推迟的派发回 `status: "queued"`，带 `reason`（`low_memory` / `posture_critical` / `adaptive_cap_zero`）与
  `reason_detail`；只等并发槽位（`concurrency_limit`）的仍回 `spawned`（几秒内自行消失）。
- `GET /api/spawn/{id}`：排队中的回 `{"status": "queued", "queued": true, "reason", "reason_detail", "elapsed", "resuming"?}`；
  `resuming` 表示它已经开始过、在等继续（重启或重试之后），不是「没开始」。
- 普通的 `GET /api/spawn` 列表不含排队中的（`?queued=1` 才含）；这就是现在 `inspect` 找不到它、报「归属不可核实」的原因。
- 返工续接的 `/continue` 回执固定写 `spawned`，只看回执会漏掉排队：须以 `GET /api/spawn/{id}` 为准。

## 3. 做法

1. **Worker**：派发回执为 `queued` 时，`dispatch.json` 记 `queued`（原因、时间），回给平台 `status: "running"` 加 `queued` 字段；
   `inspect` 在列表与状态文件里都找不到时，改问 `GET /api/spawn/{id}`：任务文字含本 Attempt 的标记即可证明归属，排队中的回
   `{"status": "running", "queued": {"reason", "detail", "elapsed", "resuming"}}`，不再报 `native_unavailable`。
2. **平台**：Attempt 的证据记 `queued`（原因、开始排队的时间）；会话的「需要处理」显示「排队：等待执行环境资源」（使用者的文案只写
   原因的类别，不写内存数字；`detail` 只在管理员视图与事件中）；时间线事件 `attempt.queued` / `attempt.dequeued`。
3. **看门狗**：排队期间不计「没有进展」与运行时长；离开排队后从那一刻重新计时。
4. **never started**：原生任务以 `never started` 结束、且从未 `resuming` 的，视为资源不足没有开始：自动重新派发，不消耗自动重试次数
   与会话预算，事件写明原因；连续 3 次仍未开始时交给协调者（与现在一样）。带过 `resuming` 的照旧交给协调者。
5. **不做**：平台侧的容量门槛与调度（配额是第 2 期）；回收空闲进程（属主核对后另做，BACKLOG）。

## 4. 验收

- 假 Gateway：首次派发回 `queued, low_memory`；返工 `/continue` 回 `spawned` 而 `GET /api/spawn/{id}` 回 `queued`。两种情形会话都显示
  「排队：等待执行环境资源」，没有 `native_unavailable`；排队 200 分钟看门狗不判卡住；`never started` 自动重派且不计重试次数。
- EC2：在 owner 边界上制造一次排队（临时调高 `agent.spawn_min_memory_gb`，验收后恢复；这会影响该边界上的所有会话，须负责人同意后在空闲时进行），观察同样的显示与恢复。未获同意前，以 EC2 上 KiroCrew 0.10.0 源码核对的回执结构为准。
