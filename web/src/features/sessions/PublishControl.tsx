import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import type { PublishRecord, Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useRefreshRun } from "@/features/sessions/useSessionActions";

const SETTLED = new Set(["failed", "blocked", "completed", "stopped", "waiting_worker"]);

/** Publish the Session's work to its repository as a branch and a draft pull / merge request (D35 §5.2). */
export function PublishControl({ run }: { run: Run }) {
  const refresh = useRefreshRun();
  const [busy, setBusy] = useState(false);
  const source = run.credentials?.source;
  const published = run.publish;
  if (run.source.type !== "git" || !source) return null;
  const label = (record: PublishRecord) => `${record.kind === "merge_request" ? "MR" : "PR"} #${record.number}`;
  const publish = async () => {
    setBusy(true);
    try {
      const record = await api<PublishRecord>("/runs/" + encodeURIComponent(run.id) + "/publish", "POST", {});
      await refresh(run.id);
      toast.success(`已发布到 ${record.branch}：${label(record)}${record.reused ? "（沿用已有的）" : ""}`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <p id="run-publish" className="text-xs text-muted-foreground">
      {published && (
        <>已发布：{published.branch} · <a className="underline" href={published.url} target="_blank" rel="noreferrer">{label(published)}</a>{" "}</>
      )}
      {source.access === "read" ? <span>凭证为只读，无法发布。</span>
        : !source.unavailable && run.lifecycle === "open" && SETTLED.has(run.status) && (
          <Button type="button" size="xs" variant="outline" disabled={busy} onClick={publish}>
            {busy ? "正在发布…" : published ? "再次发布到仓库" : "发布到仓库"}
          </Button>
        )}
    </p>
  );
}
