import { ChevronDown } from "lucide-react";
import type { Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { StatusBadge } from "@/components/StatusBadge";
import { TOOLS, formatTime } from "@/lib/labels";
import { canRetryTask } from "@/lib/session";

function Meta({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 font-mono break-all">{value || "未报告"}</dd>
    </>
  );
}

export function TaskList({ run, onContinue }: { run: Run; onContinue: (taskId: string) => void }) {
  return (
    <ol id="task-list" className="space-y-3" aria-label="开发任务">
      {run.tasks.map((task, index) => {
        const attempts = task.attempts ?? [];
        const spec = task.spec;
        return (
          <li key={task.id}>
            <article className="rounded-xl border bg-card p-4 shadow-xs sm:p-5" data-task={task.id}>
              <header className="flex items-start gap-3">
                <span className="grid size-7 shrink-0 place-items-center rounded-md bg-muted font-mono text-xs">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-semibold">{spec.name}</h3>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {TOOLS[spec.tool] ?? spec.tool} · {spec.model} · Effort {spec.effort || "继承默认"}
                  </p>
                </div>
                {run.kept?.tasks.includes(task.id) && (
                  <span className="rounded bg-muted px-1.5 text-[11px] text-muted-foreground">沿用 v{run.kept.version} 结果</span>
                )}
                <StatusBadge status={task.status} />
              </header>
              <p className="mt-3 line-clamp-4 text-sm leading-6 whitespace-pre-wrap text-foreground/85">{spec.prompt}</p>
              <footer className="mt-3 flex flex-wrap items-center gap-3">
                <Button type="button" size="sm" variant="outline" disabled={!canRetryTask(run, index)}
                  onClick={() => onContinue(task.id)} data-retry={task.id}>配置并续作</Button>
                <small className="text-xs text-muted-foreground">Task {task.id} · {attempts.length} 次执行</small>
              </footer>
              {attempts.length > 0 && (
                <Collapsible className="mt-3 border-t pt-3">
                  <CollapsibleTrigger className="group flex items-center gap-1.5 text-xs font-medium text-primary">
                    执行证据与历史 Attempt
                    <ChevronDown className="size-3.5 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
                  </CollapsibleTrigger>
                  <CollapsibleContent className="mt-3 space-y-3">
                    {[...attempts].reverse().map((attempt) => (
                      <section key={attempt.id} className="rounded-lg border bg-muted/30 p-3 text-xs">
                        <div className="mb-2 flex items-center gap-2">
                          <strong>Attempt {attempt.number}</strong>
                          <StatusBadge status={attempt.status} />
                          <span className="ml-auto text-muted-foreground">{formatTime(attempt.updated, true)}</span>
                        </div>
                        <dl className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-1">
                          <Meta label="请求" value={`${TOOLS[attempt.spec.tool] ?? attempt.spec.tool} / ${attempt.spec.model} / ${attempt.spec.effort || "默认"}`} />
                          {attempt.evidence.switched_from && (
                            <Meta label="换工具" value={`由协调者从 ${attempt.evidence.switched_from.tool} / ${attempt.evidence.switched_from.model} `
                              + `换为 ${attempt.spec.tool} / ${attempt.spec.model}：${attempt.evidence.switched_from.reason ?? "未说明原因"}`} />
                          )}
                          {attempt.evidence.watchdog?.reason && <Meta label="看门狗" value={attempt.evidence.watchdog.reason} />}
                          <Meta label="实际后端" value={attempt.evidence.actual_backend} />
                          <Meta label="实际模型" value={attempt.evidence.actual_model} />
                          <Meta label="实际 Effort" value={attempt.evidence.actual_effort} />
                          <Meta label="交接结论" value={attempt.evidence.outcome?.decision} />
                          <Meta label="原生 Task" value={attempt.native_id ?? "待核实"} />
                          <Meta label="工具 Session" value={attempt.evidence.native_session_id} />
                          <Meta label="Context SHA-256" value={attempt.context_hash ?? "未形成"} />
                          {attempt.evidence.candidate && (
                            <Meta label="候选" value={`${attempt.evidence.candidate.sha.slice(0, 7)} · ${attempt.evidence.candidate.files} 个文件 `
                              + `+${attempt.evidence.candidate.insertions} −${attempt.evidence.candidate.deletions}`} />
                          )}
                        </dl>
                        {attempt.evidence.checks && !("check_results" in attempt.evidence) && (
                          <p className="mt-2 text-muted-foreground">正在运行检查命令（{attempt.evidence.checks.commands} 条）</p>
                        )}
                        {!!attempt.evidence.gates?.length && (
                          <ul aria-label="门禁" className="mt-2 space-y-0.5">
                            {attempt.evidence.gates.map((gate, index) => (
                              <li key={index} className={gate.status === "fail" ? "text-destructive" : gate.status === "skip" ? "text-muted-foreground" : undefined}>
                                {{ pass: "✓", fail: "✗", skip: "–" }[gate.status]} {gate.name}{gate.detail ? " · " + gate.detail : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                        {!!attempt.evidence.outcome?.acceptance?.length && (
                          <ul aria-label="验收项" className="mt-2 space-y-0.5">
                            {attempt.evidence.outcome.acceptance.map((check, index) => (
                              <li key={index} className={check.result === "fail" ? "text-destructive" : undefined}>
                                {{ pass: "✓", fail: "✗", "n/a": "—" }[check.result]} {check.item}{check.evidence ? " · " + check.evidence : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                        {!!attempt.evidence.outcome?.suggestions?.length && (
                          <ul aria-label="建议" className="mt-2 list-inside list-disc text-muted-foreground">
                            {attempt.evidence.outcome.suggestions.map((item, index) => <li key={index}>{item}</li>)}
                          </ul>
                        )}
                        {attempt.error && <p className="mt-2 text-destructive">{attempt.error}</p>}
                        {attempt.result && (
                          <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-card p-3 font-mono text-[11px] leading-5 whitespace-pre-wrap">
                            {attempt.result}
                          </pre>
                        )}
                      </section>
                    ))}
                  </CollapsibleContent>
                </Collapsible>
              )}
            </article>
          </li>
        );
      })}
    </ol>
  );
}
