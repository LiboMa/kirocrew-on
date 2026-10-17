import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useCredentials } from "@/api/hooks";
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
  if (run.source.type === "new") return <NewRepositoryPublish run={run} />;
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

/** D48: a new project's work published to a repository the platform creates (or an empty one): owner, name, private,
 *  and a write credential; once published, the repository is kept and later publishes go there. */
function NewRepositoryPublish({ run }: { run: Run }) {
  const refresh = useRefreshRun();
  const target = run.publish_target;
  const suggested = run.publish_suggestion?.repository;
  const [owner, setOwner] = useState(suggested?.owner ?? "");
  const [name, setName] = useState(suggested?.name ?? "");
  const [isPrivate, setPrivate] = useState(suggested?.private ?? true);
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const { data = [] } = useCredentials(!target);
  const writers = data.filter((c) => c.access === "write" && !c.disabled);
  const published = run.publish;
  const settled = run.lifecycle === "open" && SETTLED.has(run.status) && run.permissions?.publish !== false;
  const send = async (body: Record<string, unknown>) => {
    setBusy(true);
    try {
      const record = await api<PublishRecord>("/runs/" + encodeURIComponent(run.id) + "/publish", "POST", body);
      await refresh(run.id);
      toast.success(`已发布到 ${record.branch}${record.number ? `：PR #${record.number}` : ""}`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (target) {
    return (
      <p id="run-publish" className="text-xs text-muted-foreground">
        仓库：<a className="underline" href={target.web ?? target.url} target="_blank" rel="noreferrer">{target.path}</a>
        {published?.url && <> · <a className="underline" href={published.url} target="_blank" rel="noreferrer">PR #{published.number}</a></>}{" "}
        {settled && (
          <Button type="button" size="xs" variant="outline" disabled={busy} onClick={() => send({})}>
            {busy ? "正在发布…" : "再次发布到仓库"}
          </Button>
        )}
      </p>
    );
  }
  if (!settled) return null;
  return (
    <div id="run-publish" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>发布到新仓库：</span>
      <label className="inline-flex items-center gap-1">owner
        <input aria-label="owner" className="h-6 w-28 rounded border px-1" value={owner} onChange={(e) => setOwner(e.target.value)} />
      </label>
      <label className="inline-flex items-center gap-1">仓库名
        <input aria-label="仓库名" className="h-6 w-32 rounded border px-1" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="inline-flex items-center gap-1">
        <input type="checkbox" checked={isPrivate} onChange={(e) => setPrivate(e.target.checked)} />私有
      </label>
      <label className="inline-flex items-center gap-1">凭证
        <select aria-label="凭证" className="h-6 rounded border px-1" value={credential} onChange={(e) => setCredential(e.target.value)}>
          <option value="">选择有写权限的凭证</option>
          {writers.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.host} · {c.hint}</option>)}
        </select>
      </label>
      <Button type="button" size="xs" variant="outline" disabled={busy || !owner.trim() || !name.trim() || !credential}
        onClick={() => send({ repository: { owner: owner.trim(), name: name.trim(), private: isPrivate }, credential })}>
        {busy ? "正在发布…" : "新建仓库并发布"}
      </Button>
      {run.publish_suggestion?.quote && <span>（需求中要求：{run.publish_suggestion.quote}）</span>}
    </div>
  );
}
