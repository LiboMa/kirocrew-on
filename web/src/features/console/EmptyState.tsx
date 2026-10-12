import { ArrowRight, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useConsole } from "@/features/console/context";
import { ACTIVE_STATES, ATTENTION_STATES, TOOLS } from "@/lib/labels";
import { filterRuns } from "@/lib/session";

export function EmptyState() {
  const { runs, space, templates, open, select } = useConsole();
  const inSpace = filterRuns(runs, { space, query: "", filter: "all" });
  const attention = inSpace.filter((r) => r.lifecycle === "open" && ATTENTION_STATES.has(r.status));
  const counts = [
    ["进行中", inSpace.filter((r) => r.lifecycle === "open" && ACTIVE_STATES.has(r.status)).length],
    ["需处理", attention.length],
    ["已完成", inSpace.filter((r) => r.status === "completed").length],
  ] as const;
  const quick = ["chain", "proof"].map((id) => templates.find((t) => t.id === id)).filter((t) => !!t);

  return (
    <div className="mx-auto max-w-4xl space-y-8 px-6 py-10 sm:px-10">
      <section className="space-y-4">
        <p className="text-xs font-semibold tracking-[0.2em] text-muted-foreground">DEVELOPMENT WORKSPACE</p>
        <h1 className="text-3xl font-bold tracking-tight">从一个需求开始</h1>
        <p className="max-w-xl text-sm leading-7 text-muted-foreground">
          描述要完成的开发任务，选择参考模板、让 KiroCrew 推荐，或直接提交 Agent 预先生成的工作流。
          执行过程、证据与返工都在会话中可查。
        </p>
        <Button size="lg" id="intent-start" onClick={() => open({ kind: "launch", seed: {} })}>
          <Sparkles /> 输入需求，开始开发 <ArrowRight />
        </Button>
      </section>
      <section className="grid gap-3 sm:grid-cols-3" aria-label="当前空间概况">
        {counts.map(([label, count]) => (
          <Card key={label} className="gap-1 px-5 py-4">
            <span className="text-xs text-muted-foreground">{label}</span>
            <strong className="text-2xl">{count}</strong>
          </Card>
        ))}
      </section>
      {attention.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">需要处理的会话</h2>
          {attention.slice(0, 5).map((run) => (
            <button key={run.id} type="button" onClick={() => select(run.id)}
              className="flex w-full items-center justify-between rounded-lg border bg-card px-4 py-3 text-left text-sm hover:bg-accent">
              <span className="truncate">{run.name || run.input}</span>
              <span className="ml-4 shrink-0 text-xs text-destructive">{run.attention?.title ?? "需要处理"}</span>
            </button>
          ))}
        </section>
      )}
      <section className="grid gap-3 sm:grid-cols-2">
        {quick.map((template) => (
          <button key={template.id} type="button" onClick={() => open({ kind: "launch", seed: { template_id: template.id } })}
            id={"quick-" + template.id}
            className="rounded-xl border bg-card p-5 text-left transition-colors hover:border-primary/40 hover:bg-accent">
            <strong className="block">{template.name}</strong>
            <small className="mt-1 block text-xs leading-6 text-muted-foreground">{template.description}</small>
            <span className="mt-3 block text-[11px] text-primary">{template.steps.map((s) => TOOLS[s.tool]).join(" → ")}</span>
          </button>
        ))}
      </section>
    </div>
  );
}
