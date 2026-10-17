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
  const policy = run.publish_policy;
  const automatic = async (auto: boolean) => {   // D47: publish on completion, or wait for a person
    setBusy(true);
    try {
      await api("/runs/" + encodeURIComponent(run.id) + "/publish-policy", "POST", { auto });
      await refresh(run.id);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
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
      {published?.url ? (
        <>已发布：{published.branch} · <a className="underline" href={published.url} target="_blank" rel="noreferrer">{label(published)}</a>{" "}</>
      ) : published && <>已推送：{published.branch}（拉取请求未创建：{published.request_error}）{" "}</>}
      {source.access === "read" ? <span>凭证为只读，无法发布。</span>
        : !source.unavailable && run.lifecycle === "open" && SETTLED.has(run.status) && (
          <>
            <Button type="button" size="xs" variant="outline" disabled={busy} onClick={publish}>
              {busy ? "正在发布…" : published ? "再次发布到仓库" : "发布到仓库"}
            </Button>
            {run.verified && <span> 将推送已验证的候选 {run.verified.sha.slice(0, 7)}</span>}
          </>
        )}
      {run.lifecycle === "open" && run.permissions?.publish !== false && (
        <label className="ml-2 inline-flex items-center gap-1">
          <input type="checkbox" checked={!!policy?.auto} disabled={busy} onChange={(e) => automatic(e.target.checked)} />
          完成后自动发布 Draft PR{policy?.source === "requested" && "（需求中要求提交 PR）"}
        </label>
      )}
      {run.publish_auto && !run.publish_auto.ok && <span className="ml-2 text-destructive">自动发布未完成：{run.publish_auto.error}</span>}
    </p>
  );
}
