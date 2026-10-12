// "Open in KiroCrew": the native login URL exists only during this click — never in state, storage or the address bar.
import { api } from "@/api/client";

const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);

export interface CrewEntry {
  url: string;
  origin: string;
  native_parent: string;
}

/** Validates the entry the platform returned against the Session the user picked; throws a readable reason. */
export function validateCrewEntry(result: CrewEntry, parent: string, pageHost = window.location.hostname): URL {
  const target = new URL(result.url);
  const keys = [...target.searchParams.keys()].sort().join(",");
  if (!["http:", "https:"].includes(target.protocol) || target.origin !== result.origin || target.username ||
      target.password || target.pathname !== "/chat" || target.hash || result.native_parent !== parent ||
      target.searchParams.get("sid") !== parent || keys !== "sid,token" || !target.searchParams.get("token"))
    throw new Error("原生入口与所选 Session 不一致，请刷新后重试。");
  if (LOCAL.has(target.hostname) && !LOCAL.has(pageHost))
    throw new Error("此 Worker 的入口指向本机。远程访问时，请为 Worker 配置可访问的 KiroCrew 地址。");
  return target;
}

/** Must be called synchronously from the click handler: an asynchronous window.open is usually blocked. */
export async function openCrewSession(runId: string, parent: string): Promise<void> {
  const popup = window.open("/crew-entry.html", "_blank");
  if (!popup) throw new Error("浏览器拦截了新标签页，请允许此工作台打开弹出窗口后重试。");
  popup.opener = null;
  try {
    const result = await api<CrewEntry>("/runs/" + encodeURIComponent(runId) + "/crew-entry", "POST", {});
    const target = validateCrewEntry(result, parent);
    if (!popup.closed) popup.location.replace(target.href);
  } catch (error) {
    const message = (error as Error).message;
    if (!popup.closed) {
      const explain = () => {
        try {
          popup.document.title = "KiroCrew 连接未完成";
          const status = popup.document.getElementById("crew-entry-status");
          if (status) status.textContent = message + " 请回到原工作台重试，Session 保持不变。";
        } catch {
          // The user may already have navigated this tab elsewhere.
        }
      };
      explain();
      popup.addEventListener("load", explain, { once: true });
    }
    throw error;
  }
}
