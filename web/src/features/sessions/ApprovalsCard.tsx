import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useApprovals } from "@/api/hooks";
import type { PendingApproval, Run } from "@/api/types";
import { Button } from "@/components/ui/button";

const short = (text: string, n = 160) => (text.length > n ? text.slice(0, n) + "…" : text);

function Item({ run, item, canAnswer }: { run: Run; item: PendingApproval; canAnswer: boolean }) {
  const client = useQueryClient();
  const [confirm, setConfirm] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const answer = async (decision: "approve" | "reject") => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/runs/${encodeURIComponent(run.id)}/approvals/answer`, "POST", {
        origin: item.origin, request_id: item.request_id, decision,
        ...(item.origin === "native" ? { request_mid: item.request_mid } : { instance: item.instance }) });
      setConfirm(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      await client.invalidateQueries({ queryKey: ["approvals", run.id] });
    }
  };
  return (
    <li className="rounded-lg border bg-background p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <strong>{item.tool}</strong>
        <span className="text-xs text-muted-foreground">{item.origin === "native" ? "会话内工具请求" : `状态级批准 · ${item.source ?? ""}`}</span>
      </div>
      {item.tool_purpose && <p className="mt-1">{item.tool_purpose}</p>}
      <pre className="mt-2 max-h-80 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">
        {confirm ? item.tool_input : short(item.tool_input)}
      </pre>
      {item.truncated && <p className="mt-1 text-xs text-warning">内容已截断：完整内容请在原生 KiroCrew 中查看</p>}
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      {canAnswer && (confirm ? (
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant={confirm === "approve" ? "default" : "destructive"} disabled={busy} onClick={() => answer(confirm)}>
            {confirm === "approve" ? "确认批准" : "确认拒绝"}</Button>
          <Button size="sm" variant="outline" onClick={() => setConfirm(null)}>取消</Button>
        </div>
      ) : (
        <div className="mt-2 flex gap-2">
          <Button size="sm" onClick={() => setConfirm("approve")}>批准</Button>
          <Button size="sm" variant="outline" onClick={() => setConfirm("reject")}>拒绝</Button>
        </div>
      ))}
    </li>
  );
}

export function ApprovalsCard({ run, canAnswer }: { run: Run; canAnswer: boolean }) {
  const live = run.lifecycle === "open" && !["completed", "failed", "stopped"].includes(run.status);
  const { data } = useApprovals(run.id, live);
  if (!live || !data) return null;   // an ended Session's last list would offer buttons that can only fail
  if (!data.supported) {
    return run.status === "waiting_approval"
      ? <p className="text-xs text-muted-foreground">此执行环境的 Worker 尚未升级，请在原生 KiroCrew 中处理批准。</p> : null;
  }
  if (!data.approvals.length) return null;
  return (
    <section aria-label="待批准" className="rounded-xl border border-warning/50 bg-warning/5 px-4 py-3">
      <h2 className="text-sm font-semibold">待批准（{data.approvals.length}）</h2>
      {!canAnswer && <p className="mt-1 text-xs text-muted-foreground">只有会话创建者、Team owner 或管理员可以答复。</p>}
      <ul className="mt-2 space-y-2">
        {/* The full identity: a request id can recur in a slot, and a replacement must not inherit an open confirmation. */}
        {data.approvals.map((item) => (
          <Item key={`${item.origin}:${item.request_id}:${item.request_mid ?? item.instance}`} run={run} item={item} canAnswer={canAnswer} />
        ))}
      </ul>
    </section>
  );
}
