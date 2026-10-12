import { ChevronDown } from "lucide-react";
import type { Run } from "@/api/types";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { RECOVERY_STATES } from "@/lib/labels";

export function RecoveryPanel({ run }: { run: Run }) {
  const data = run.recovery;
  if (!data?.history?.length) return null;
  const last = data.latest;
  const summary = run.status === "diagnosing" ? `第 ${last?.number ?? 1} 轮 · Coordinator 正在诊断`
    : last?.status === "applied" && run.status !== "completed" ? `第 ${last.number} 轮 · 已安排返工与复审`
      : `纠错记录 · ${data.used} 轮`;
  return (
    <Collapsible className="rounded-xl border bg-card px-4 py-3" id="recovery-history">
      <CollapsibleTrigger className="group flex w-full items-center justify-between text-sm font-semibold text-primary">
        <span id="recovery-summary">{summary}</span>
        <ChevronDown className="size-4 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent id="recovery-records" className="mt-2 divide-y">
        {data.history.map((row) => (
          <article key={row.number} className="py-3 text-sm">
            <strong>第 {row.number} 轮 · {RECOVERY_STATES[row.status] ?? row.status}</strong>
            <p className="mt-1 text-muted-foreground">{row.decision?.reason ?? row.error ?? "等待真实诊断结果"}</p>
            {row.decision?.instructions && <p className="mt-1 whitespace-pre-wrap">{row.decision.instructions}</p>}
            <small className="mt-1 block text-xs text-muted-foreground">
              失败 Task：{row.failed_task_id}{row.decision?.target_task_id ? " → 返工 Task：" + row.decision.target_task_id : ""}
              {" · "}原生诊断 Task：{row.native_id ?? "待派发"}
            </small>
          </article>
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}
