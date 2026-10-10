"use strict";
const run = new URLSearchParams(location.search).get("run") || "";
const design = new URLSearchParams(location.search).get("design") || "";
const status = document.getElementById("entry-status");
const retry = document.getElementById("entry-retry");
document.getElementById("entry-back").href = "/?run=" + encodeURIComponent(run);
async function enterCrew() {
  retry.hidden = true;
  try {
    const response = await fetch(design ? "/api/design/app-entry" : "/api/app-entry", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Workflow-MVP": "1"},
      body: JSON.stringify(design ? {id: design} : {run}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error("bootstrap");
    const target = new URL(data.url);
    if (target.origin !== "http://localhost:5476" || target.pathname !== "/chat"
        || target.searchParams.get("sid") !== (design ? "wf-" + design : run) || !target.searchParams.get("token")) {
      throw new Error("invalid target");
    }
    // Native Crew consumes the link token and manages its own auth cookies.
    // No token enters localStorage, sessionStorage or the workflow snapshot.
    location.replace(target.href);
  } catch {
    status.textContent = "未能完成本机认证。请确认 KiroCrew Gateway 已启动，再重试。原 Session 保持不变。";
    retry.hidden = false;
  }
}
retry.onclick = enterCrew;
enterCrew();
