import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, useRunApp } from "@/api/hooks";
import type { AppPreview, Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import { formatTime } from "@/lib/labels";

const LABELS: Record<AppPreview["status"], string> = {
  stopped: "未启动", installing: "安装依赖中", starting: "启动中", running: "运行中", failed: "失败", unsupported: "执行环境不支持",
};
const LIVE = new Set(["installing", "starting", "running"]);
// The platform starts a preview only once the Session's tasks have ended (D36 §5.1).
// Kept equal to octopus/domain.py SETTLED by tests/test_evaluator.py (a person judging appearance needs the preview, D46 P3).
const SETTLED = new Set(["failed", "blocked", "completed", "stopped", "waiting_worker", "awaiting_confirmation", "awaiting_acceptance"]);

/** Why the preview cannot be started (or, for a viewer, stopped) here; "" when it can. */
export function previewReason(run: Run): string {
  if (run.permissions?.operate === false) return "你以查看身份访问此会话，不能启动或停止预览。";   // D64
  if (!run.worker_id || !run.workspace) return "源码尚未准备，暂不能启动预览。";
  if (run.lifecycle !== "open") return "会话已关闭，请先重新打开。";
  if (run.permissions?.resume === false) return "Team 已归档，不能启动预览。";
  if (!SETTLED.has(run.status)) return "会话正在执行任务，完成后再启动预览。";
  return "";
}

/** The Session's app running in its execution environment, opened through the preview domain (D36 §5.3). */
export function AppPreviewPanel({ run, onOpen }: { run: Run; onOpen: () => void }) {
  const client = useQueryClient();
  const { data: app } = useRunApp(run.id, !!run.worker_id);
  const [busy, setBusy] = useState<"start" | "stop" | null>(null);
  const status = app?.status ?? "stopped";
  const live = LIVE.has(status);
  const reason = previewReason(run);

  const act = async (action: "start" | "stop") => {
    setBusy(action);
    try {
      const view = await api<Partial<AppPreview>>(`/runs/${encodeURIComponent(run.id)}/app`, "POST", { action });
      client.setQueryData<AppPreview>(keys.app(run.id), (old) => ({ ...(old as AppPreview), ...view,
        ...(action === "stop" ? { openable: false } : {}) }));
      await client.invalidateQueries({ queryKey: keys.app(run.id) });
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-label="远程预览" id="app-preview" className="space-y-3 rounded-xl border bg-card p-4 text-xs">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">远程预览</h2>
        <span className="ml-auto text-muted-foreground" id="app-status">{LABELS[status]}</span>
      </div>
      {status === "unsupported" ? (
        <p className="text-muted-foreground">执行环境需要升级后才支持远程预览；可以先「下载源码」在本机运行。</p>
      ) : (
        <>
          <p className="text-muted-foreground">在执行环境中运行此会话的应用，经独立的预览域名在浏览器中打开；能查看此会话的人都可以使用。</p>
          {app?.error && status === "failed" && <p className="text-destructive" id="app-error">{app.error}</p>}
          {live && app?.started && (
            <p id="app-started">
              {app.started_by?.username ?? "—"} 于 {formatTime(app.started)} 启动 · 空闲 30 分钟后自动停止
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {!live && (
              <Button size="sm" id="app-start" disabled={!!reason || !!busy} onClick={() => act("start")}>
                {busy === "start" ? "正在启动…" : "启动"}
              </Button>
            )}
            {status === "running" && <Button size="sm" id="app-open" onClick={onOpen}>打开</Button>}
            {live && (
              <Button size="sm" variant="outline" id="app-stop" disabled={!!busy || run.permissions?.operate === false}
                onClick={() => act("stop")}>停止</Button>
            )}
          </div>
          {reason && !live && <p className="text-muted-foreground" id="app-reason">{reason}</p>}
          {app?.log && app.log.length > 0 && (
            <details>
              <summary className="cursor-pointer text-muted-foreground">最近日志</summary>
              <pre className="mt-2 max-h-64 overflow-auto rounded bg-muted p-2 font-mono text-[11px] whitespace-pre-wrap">
                {app.log.join("\n")}
              </pre>
            </details>
          )}
        </>
      )}
    </section>
  );
}
