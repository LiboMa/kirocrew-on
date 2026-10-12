/* Shared stage-based progress for the Session list and overview. */
const WorkflowProgress = (() => {
  function calculate(data) {
    const total = data?.manifest?.workflow?.steps?.length || 0;
    const done = Math.min(total, (data?.steps || []).filter(s => s.status === "accepted").length);
    return {total, done, percent: total ? Math.round(done / total * 100) : 0};
  }
  function cycle(progress, state, label) {
    const ring = document.createElement("span");
    ring.className = "session-cycle " + state;
    ring.setAttribute("role", "img");
    ring.setAttribute("aria-label", "开发进度 " + progress.percent + "%，" + label + "，按阶段完成度估算");
    ring.title = "按阶段完成度估算：" + progress.done + "/" + progress.total;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 48 48");
    svg.setAttribute("aria-hidden", "true");
    for (const name of ["cycle-track", "cycle-value"]) {
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      for (const [key, value] of Object.entries({cx:24, cy:24, r:20, pathLength:100, class:name})) {
        circle.setAttribute(key, String(value));
      }
      if (name === "cycle-value") circle.setAttribute("stroke-dasharray", progress.percent + " 100");
      svg.append(circle);
    }
    const percent = document.createElement("span");
    percent.textContent = progress.percent + "%";
    percent.setAttribute("aria-hidden", "true");
    ring.append(svg, percent);
    return ring;
  }
  return {calculate, cycle};
})();
if (typeof module !== "undefined") module.exports = WorkflowProgress;
