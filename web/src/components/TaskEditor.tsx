import type { ReactNode } from "react";
import { ArrowUp, ChevronDown, Plus, Trash2 } from "lucide-react";
import type { Step, Tool } from "@/api/types";
import { AgentFields } from "@/components/AgentFields";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { TOOLS } from "@/lib/labels";

export function newStep(): Step {
  return { id: "task-" + crypto.randomUUID().slice(0, 8), name: "新任务", tool: "claude", model: "auto", effort: "", prompt: "" };
}

/** Ordered tasks (at most 8): name, prompt and agent per task; reorder, add and remove. */
export function TaskEditor({ tasks, onChange, models, defaults, idPrefix, title, hint, readOnly = false }: {
  tasks: Step[]; onChange: (tasks: Step[]) => void; models?: Partial<Record<Tool, string[]>>;
  defaults?: Partial<Record<Tool, string>>; idPrefix: string;
  title: string; hint?: ReactNode; readOnly?: boolean;
}) {
  const change = (index: number, patch: Partial<Step>) => onChange(tasks.map((t, i) => (i === index ? { ...t, ...patch } : t)));
  const move = (index: number) => {
    const next = [...tasks];
    [next[index - 1], next[index]] = [next[index], next[index - 1]];
    onChange(next);
  };
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{title} <span className="font-normal text-muted-foreground">· {tasks.length} Tasks</span></h3>
        {!readOnly && (
          <Button type="button" size="sm" variant="outline" id={idPrefix + "-add"} disabled={tasks.length >= 8}
            onClick={() => onChange([...tasks, newStep()])}><Plus /> 添加任务</Button>
        )}
      </div>
      {hint}
      <ol id={idPrefix + "-tasks"} className="space-y-2.5" aria-label={title}>
        {tasks.map((task, index) => (
          <li key={task.id}>
            <Collapsible defaultOpen={tasks.length === 1} className="rounded-xl border bg-card">
              <div className="flex items-center gap-3 px-4 py-3">
                <span className="grid size-6 shrink-0 place-items-center rounded bg-muted font-mono text-[11px]">{index + 1}</span>
                <CollapsibleTrigger className="group flex min-w-0 flex-1 items-center gap-2 text-left">
                  <strong className="truncate text-sm">{task.name || "未命名任务"}</strong>
                  <small className="shrink-0 text-xs text-muted-foreground">{TOOLS[task.tool] ?? task.tool} · {task.model || "auto"}</small>
                  <ChevronDown className="ml-auto size-4 shrink-0 text-muted-foreground group-data-[state=open]:rotate-180" />
                </CollapsibleTrigger>
                {!readOnly && (
                  <>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label="上移" disabled={index === 0} onClick={() => move(index)}><ArrowUp /></Button>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label="移除" disabled={tasks.length === 1}
                      onClick={() => onChange(tasks.filter((_, i) => i !== index))}><Trash2 /></Button>
                  </>
                )}
              </div>
              <CollapsibleContent className="space-y-3 border-t px-4 py-4">
                <fieldset disabled={readOnly} className="space-y-3">
                  <p className="font-mono text-[11px] text-muted-foreground">Task ID · {task.id}</p>
                  <div className="space-y-1.5">
                    <Label htmlFor={`${idPrefix}-${index}-name`}>任务名称</Label>
                    <Input id={`${idPrefix}-${index}-name`} value={task.name} maxLength={80} onChange={(e) => change(index, { name: e.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`${idPrefix}-${index}-prompt`}>Agent 提示词 / 任务说明</Label>
                    <Textarea id={`${idPrefix}-${index}-prompt`} rows={5} value={task.prompt} onChange={(e) => change(index, { prompt: e.target.value })} />
                  </div>
                  <AgentFields idPrefix={`${idPrefix}-${index}`} models={models} defaults={defaults} value={task}
                    onChange={(agent) => change(index, { ...agent, effort: agent.tool === "opencode" ? "" : agent.effort })} />
                </fieldset>
              </CollapsibleContent>
            </Collapsible>
          </li>
        ))}
      </ol>
    </div>
  );
}
