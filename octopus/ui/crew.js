"use strict";

// Native login URLs exist only during this click, never in drafts or run state.
const crewEntriesOpening = new Set();
function crewEntryReason(run) {
  if (run.lifecycle !== "open" || ["close", "delete"].includes(run.desired) || run.status === "reopening")
    return "请先重新打开此 Session，再进入 KiroCrew。";
  if (!run.worker_id || !run.native_parent) return "原生 Session 尚未建立，等待 Worker 准备完成后可打开。";
  return "";
}
async function openCrewSession(rid, parent) {
  if (!rid || !parent || crewEntriesOpening.has(rid)) return;
  // Open synchronously with the user gesture; async window.open is often blocked.
  const popup = window.open("/crew-entry.html", "_blank");
  if (!popup) { toast("浏览器拦截了新标签页，请允许此工作台打开弹出窗口后重试。"); return; }
  popup.opener = null;
  const account = state.me?.id, token = state.token;
  crewEntriesOpening.add(rid);renderRun();
  try {
    const result = await api("/runs/" + encodeURIComponent(rid) + "/crew-entry", "POST", {});
    if (state.me?.id !== account || state.token !== token) { popup.close(); return; }
    const target = new URL(result.url);
    const keys = [...target.searchParams.keys()].sort().join(",");
    if (!["http:", "https:"].includes(target.protocol) || target.origin !== result.origin ||
        target.username || target.password || target.pathname !== "/chat" || target.hash ||
        result.native_parent !== parent || target.searchParams.get("sid") !== parent ||
        keys !== "sid,token" || !target.searchParams.get("token"))
      throw new Error("原生入口与所选 Session 不一致，请刷新后重试。");
    const local = host => ["localhost", "127.0.0.1", "[::1]"].includes(host);
    if (local(target.hostname) && !local(location.hostname))
      throw new Error("这个执行环境的原生 KiroCrew 入口还没有对外开通（只能在平台主机上经隧道打开）：请平台管理员为它开通。会话照常执行，不受影响。");
    if (!popup.closed) popup.location.replace(target.href);
  } catch (error) {
    toast(error.message);
    if (!popup.closed) {
      const explain = () => {
        try {
          popup.document.title = "KiroCrew 连接未完成";
          const message = popup.document.getElementById("crew-entry-status");
          if (message) message.textContent = error.message + " 请回到原工作台重试，Session 保持不变。";
        } catch (_) { /* User may have navigated this tab themselves. */ }
      };
      explain();popup.addEventListener("load", explain, {once:true});
    }
  } finally {
    crewEntriesOpening.delete(rid);renderRun();
  }
}
