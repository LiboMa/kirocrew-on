/* Presentation only: native workflow state remains the source of progress. */
const WorkflowView = (() => {
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const byId = id => document.getElementById(id);
  const attention = new Set(["awaiting_approval", "awaiting_check", "check_failed", "failed"]);
  const names = {accepted:"已交接",running:"执行中",awaiting_approval:"待授权",
    awaiting_check:"待核对",check_failed:"交接未通过",failed:"执行失败",
    pending:"待执行",submitted:"待调度"};
  let state = null, offline = false, reportKey = "", activityKey = "";
  const previous = new Map();

  function fresh() {
    return !!state?.at && !offline && state.connected !== false &&
      Date.now() / 1000 - state.at < 15;
  }
  function tick() {
    const live = fresh();
    document.body.dataset.live = String(live);
    const health = byId("report-health");
    health.classList.toggle("stale", !live);
    const age = state?.at ? Math.max(0, Math.floor(Date.now() / 1000 - state.at)) : null;
    byId("report-freshness").textContent = !state ? "选择 Session 查看报告" :
      live ? "已连接 · 跟随实际执行状态更新" : "同步暂停 · 保留最后收到的状态";
    byId("report-updated").textContent = age === null ? "" :
      `最近同步 ${age < 60 ? age + " 秒前" : Math.floor(age / 60) + " 分钟前"}`;
    byId("report-error").hidden = live || !state?.error;
    byId("report-error").textContent = state?.error ? "同步原因：" + state.error : "";
    if (state && !live) {
      byId("connection").classList.remove("connected");
      byId("connection").textContent = "状态同步暂停";
    }
  }
  function segmentBar(id, rows) {
    const target = byId(id), key = JSON.stringify(rows.map(s => [s.id, s.status]));
    if (target.dataset.key === key) return;
    target.dataset.key = key;
    target.replaceChildren(...rows.map(s => {
      const part = node("span", undefined, "status-segment " + (s.status || "pending"));
      part.title = `${s.name}：${names[s.status] || "待执行"}`;
      return part;
    }));
  }
  function duration(step) {
    const elapsed = step.task?.elapsed, start = step.task?.started, end = step.accepted?.at;
    let seconds, prefix;
    if (step.status === "running" && Number.isFinite(elapsed) && elapsed >= 0) {
      seconds = elapsed; prefix = "已运行 ";
    } else if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
      seconds = end - start; prefix = "至交接 ";
    } else return ["pending", "submitted"].includes(step.status) ? "尚未开始" : "耗时未报告";
    const value = Math.floor(seconds);
    return prefix + (value < 60 ? value + " 秒" : value < 3600 ?
      `${Math.floor(value / 60)} 分 ${value % 60} 秒` :
      `${Math.floor(value / 3600)} 时 ${Math.floor(value % 3600 / 60)} 分`);
  }
  function render(data, rows, toolLabels, select) {
    if (data !== state) offline = false;
    state = data; tick();
    segmentBar("overview-segments", rows);
    segmentBar("report-segments", rows);
    const counts = [
      ["accepted", "已交接", rows.filter(s => s.status === "accepted").length],
      ["running", "执行中", rows.filter(s => s.status === "running").length],
      ["attention", "待处理", rows.filter(s => attention.has(s.status)).length],
      ["pending", "待执行", rows.filter(s => !s.status || ["pending", "submitted"].includes(s.status)).length],
    ];
    const permissions = data?.approvals?.length || 0;
    const key = JSON.stringify([data?.manifest?.id, counts, permissions,
      rows.map(s => [s.id, s.status, s.name, s.tool, s.task?.elapsed, s.task?.started, s.accepted?.at])]);
    if (key === reportKey) return;
    reportKey = key;
    const done = counts[0][2];
    byId("report-summary").textContent = rows.length ?
      `${done} / ${rows.length} 项任务完成交接${counts[2][2] ? `，${counts[2][2]} 项需要处理` : ""}。` +
      (permissions ? ` ${permissions} 项权限请求等待在 KiroCrew 中处理。` : "") : "等待任务状态。";
    byId("report-counts").replaceChildren(...counts.map(([status, label, count]) => {
      const item = node("div", undefined, "report-count count-" + status);
      item.append(node("strong", String(count)), node("span", label));
      return item;
    }));
    byId("report-legend").replaceChildren(...[
      ["accepted","已交接"],["running","执行中"],["awaiting_approval","待授权 / 核对"],
      ["failed","失败"],["pending","待执行"]
    ].map(([status,label]) => node("span", label, status)));
    byId("report-rows").replaceChildren(...rows.map((s, index) => {
      const item = node("button", undefined, "report-row " + (s.status || "pending"));
      item.type = "button"; item.onclick = () => select(s.id);
      item.setAttribute("aria-label", `查看 ${s.name} 的任务详情`);
      const title = node("span", undefined, "report-task");
      title.append(node("strong", s.name),
        node("small", `${toolLabels[s.tool] || s.tool} · ${s.id}`));
      const result = node("span", undefined, "report-result");
      result.append(node("span", names[s.status] || "待执行", "status-pill " + (s.status || "pending")),
        node("small", duration(s)));
      item.append(node("span", s.status === "accepted" ? "✓" : String(index + 1), "report-index"),
        title, result, node("span", "›", "report-arrow"));
      return item;
    }));
    byId("report-detail").textContent = data?.manifest ?
      `固定配置 r${data.manifest.workflow.revision} · ${data.manifest.id}` : "";
  }
  function activities(runId, rows, describe, toolInitials, timeLabel, select) {
    const key = JSON.stringify([runId, rows.map(s => [s.id, s.status, describe(s)])]);
    if (key === activityKey) return;
    activityKey = key;
    const last = previous.get(runId);
    const changed = new Set(rows.filter(s => last?.has(s.id) && last.get(s.id) !== s.status).map(s => s.id));
    previous.set(runId, new Map(rows.map(s => [s.id, s.status])));
    byId("activity-list").replaceChildren(...rows.map(s => {
      const row = node("button", undefined, "activity-item " + (s.status || "pending") +
        (changed.has(s.id) ? " status-changed" : ""));
      row.type = "button"; row.onclick = () => select(s.id);
      const text = node("div", undefined, "activity-copy");
      text.append(node("strong", `${s.name} · ${names[s.status] || "待执行"}`), node("p", describe(s)));
      row.append(node("span", s.status === "accepted" ? "✓" : toolInitials[s.tool] || "·", "activity-icon"),
        text, node("span", timeLabel(s.accepted?.at || s.task?.started), "activity-time"));
      return row;
    }));
    byId("activity-updates").textContent = changed.size ? `${changed.size} 项任务状态已更新` : "来自实际执行状态";
  }
  setInterval(tick, 1000);
  return {render, activities, disconnect() {offline = true; tick();}};
})();

/* Both handles are local layout controls, independent of sessions/configs. */
(() => {
  const key = "kirocrew.workflow.panes.v1";
  const root = document.documentElement;
  const defaults = {sessions:264,tasks:278};
  let sizes = {...defaults}, drag = null;
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "{}");
    for (const side of Object.keys(sizes)) if (Number.isFinite(saved[side])) sizes[side] = saved[side];
  } catch { /* Retain usable defaults when storage is unavailable. */ }
  const desktop = () => window.innerWidth > 1000;
  const min = {sessions:214,tasks:224}, max = {sessions:420,tasks:440};
  function apply(preferred = "sessions") {
    for (const side of Object.keys(sizes)) sizes[side] = Math.max(min[side], Math.min(max[side], sizes[side]));
    if (desktop()) {
      let excess = sizes.sessions + sizes.tasks + 12 + 380 - window.innerWidth;
      for (const side of [preferred, preferred === "sessions" ? "tasks" : "sessions"]) {
        const reduction = Math.max(0, Math.min(excess, sizes[side] - min[side]));
        sizes[side] -= reduction; excess -= reduction;
      }
    }
    for (const side of Object.keys(sizes)) {
      root.style.setProperty(`--${side}-width`, sizes[side] + "px");
      const handle = document.getElementById("resize-" + side);
      handle.setAttribute("aria-valuenow", String(Math.round(sizes[side])));
      handle.setAttribute("aria-valuemin", String(min[side]));
      handle.setAttribute("aria-valuemax", String(max[side]));
      handle.setAttribute("aria-valuetext", `${Math.round(sizes[side])} 像素`);
    }
  }
  function save() { try { localStorage.setItem(key, JSON.stringify(sizes)); } catch {} }
  for (const side of Object.keys(sizes)) {
    const handle = document.getElementById("resize-" + side);
    handle.addEventListener("pointerdown", e => {
      if (!desktop() || e.button !== 0) return;
      drag = {side, x:e.clientX, size:sizes[side], pointer:e.pointerId};
      handle.setPointerCapture(e.pointerId); document.body.classList.add("resizing"); e.preventDefault();
    });
    handle.addEventListener("pointermove", e => {
      if (!drag || drag.side !== side || drag.pointer !== e.pointerId) return;
      sizes[side] = drag.size + (e.clientX - drag.x) * (side === "sessions" ? 1 : -1);
      apply(side);
    });
    const finish = () => {drag = null; document.body.classList.remove("resizing"); save();};
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
    handle.addEventListener("lostpointercapture", finish);
    handle.addEventListener("dblclick", () => {sizes[side] = defaults[side]; apply(side); save();});
    handle.addEventListener("keydown", e => {
      if (!["ArrowLeft","ArrowRight","Home","End"].includes(e.key)) return;
      e.preventDefault();
      sizes[side] = e.key === "Home" ? min[side] : e.key === "End" ? max[side] :
        sizes[side] + (e.key === "ArrowRight" ? 1 : -1) * (side === "sessions" ? 1 : -1) * (e.shiftKey ? 40 : 10);
      apply(side); save();
    });
  }
  window.addEventListener("resize", () => apply());
  apply();
})();
