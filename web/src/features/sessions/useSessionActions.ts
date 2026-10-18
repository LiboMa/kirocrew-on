import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, command } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Run } from "@/api/types";
import { openCrewSession } from "@/lib/crew";
import { downloadJson, followDownload } from "@/lib/download";

export function useRefreshRun() {
  const client = useQueryClient();
  return useCallback(async (id: string) => {
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.run(id) }),
      client.invalidateQueries({ queryKey: keys.runs }),
    ]);
  }, [client]);
}

/** Session commands that need no form; dialogs (continue, assign, close, delete) live in dialogs.tsx. */
export function useSessionActions(run: Run | undefined) {
  const refresh = useRefreshRun();
  const [opening, setOpening] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const guarded = useCallback(async (name: string, action: () => Promise<void>) => {
    if (!run || busy) return;
    setBusy(name);
    try {
      await action();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(null);
    }
  }, [run, busy]);

  const stop = () => guarded("stop", async () => {
    await command("/runs/" + run!.id + "/stop", { expected_version: run!.version });
    await refresh(run!.id);
    toast.success("停止请求已接收；以原生状态确认完成。");
  });

  const recover = () => guarded("recover", async () => {
    await command("/runs/" + encodeURIComponent(run!.id) + "/recover", { expected_version: run!.version });
    await refresh(run!.id);
    toast.success("Coordinator 已收到诊断请求，将在原工作区安排修复或说明缺失条件。");
  });

  // D44: the user has looked at the plan, its tools and checks, and starts it.
  const confirm = () => guarded("confirm", async () => {
    await command("/runs/" + encodeURIComponent(run!.id) + "/confirm", { expected_version: run!.version });
    await refresh(run!.id);
    toast.success("计划已确认，开始执行。");
  });

  // D46 P3: the user has looked at what the Evaluator left to a person, and accepts it.
  const accept = () => guarded("accept", async () => {
    await command("/runs/" + encodeURIComponent(run!.id) + "/accept", { expected_version: run!.version });
    await refresh(run!.id);
    toast.success("已确认验收，会话继续。");
  });

  // D36: the source as a zip, through a ten-minute link that is checked again when it is followed.
  const downloadSource = () => guarded("download", async () => {
    const result = await api<{ url: string }>("/runs/" + encodeURIComponent(run!.id) + "/archive", "POST", {});
    followDownload(result.url);
    toast.success("源码开始下载（含 .git，不含依赖目录与 .env）；解压后按 OCTOPUS-ARCHIVE.txt 在本机启动。");
  });

  const exportRun = () => guarded("export", async () => {
    downloadJson(run!.id + ".json", await api("/runs/" + run!.id + "/export"));
  });

  // Not wrapped in an async gap: the popup must open inside the click.
  const openCrew = () => {
    if (!run?.native_parent || opening) return;
    setOpening(true);
    openCrewSession(run.id, run.native_parent)
      .catch((error: Error) => toast.error(error.message))
      .finally(() => setOpening(false));
  };

  // Like the native entry: the tab opens inside the click, then follows the short-lived preview link.
  const preview = () => {
    if (!run) return;
    const tab = window.open("", "_blank");
    if (!tab) { toast.error("浏览器拦截了新标签页，请允许此工作台打开弹出窗口后重试。"); return; }
    tab.opener = null;
    api<{ url: string }>("/runs/" + encodeURIComponent(run.id) + "/preview", "POST", {})
      .then((result) => { if (!tab.closed) tab.location.replace(result.url); })
      .catch((error: Error) => { tab.close(); toast.error(error.message); });
  };

  // D36 §5: the preview domain's single-use link, opened like the static preview (the tab opens inside the click).
  const openApp = () => {
    if (!run) return;
    const tab = window.open("", "_blank");
    if (!tab) { toast.error("浏览器拦截了新标签页，请允许此工作台打开弹出窗口后重试。"); return; }
    tab.opener = null;
    api<{ url: string }>("/runs/" + encodeURIComponent(run.id) + "/app/open", "POST", {})
      .then((result) => { if (!tab.closed) tab.location.replace(result.url); })
      .catch((error: Error) => { tab.close(); toast.error(error.message); });
  };

  return { stop, recover, confirm, accept, exportRun, downloadSource, openCrew, preview, openApp, opening, busy };
}
