import { useMemo, useRef, useState } from "react";
import { ArrowUp, ChevronDown, FileUp, Plus, Trash2 } from "lucide-react";
import { api } from "@/api/client";
import type { ParsedWorkflow, Step, Template, Workflow } from "@/api/types";
import { AgentFields } from "@/components/AgentFields";
import { NativeSelect } from "@/components/NativeSelect";
import { RecoveryFields } from "@/components/RecoveryFields";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { spaceWorkers, useConsole } from "@/features/console/context";
import { generationActive, type MatchStatus } from "@/features/launch/hooks";
import { PLAN_METHODS, editTasks, fingerprint, nameFrom, newTask, type Draft } from "@/features/launch/state";
import { STATES, TOOLS } from "@/lib/labels";
import { cn } from "@/lib/utils";

type Update = (next: Draft | ((d: Draft) => Draft)) => void;

const EXAMPLES = [
  ["修复 Bug", "修复登录页刷新后状态丢失的 Bug，并补充回归测试。"],
  ["安全审查", "对现有代码进行安全审计，独立检查测试覆盖并形成审查报告。"],
  ["快速验证", "快速验证一个小功能，完成最小实现与独立测试。"],
] as const;

const REPOSITORY = /https:\/\/(?:github\.com|gitlab\.com)\/[\w.-]+\/[\w.-]+(?:\.git)?/;

export function NeedStep({ draft, update, workflowMode }: { draft: Draft; update: Update; workflowMode: boolean }) {
  const { teams, workers, me } = useConsole();
  const choices = spaceWorkers(workers, draft.space, me);
  const setInput = (input: string) => update((d) => {
    const next = { ...d, input, name: d.nameTouched ? d.name : nameFrom(input) };
    const url = !d.source && input.match(REPOSITORY);
    return url ? { ...next, source: "git", url: url[0] } : next;
  });
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">先说明这次要完成什么，再确认源码与执行位置。下一步选择编排方式。</p>
      <div className="space-y-2">
        <Label htmlFor="new-input">这次要完成什么？</Label>
        <Textarea id="new-input" rows={5} maxLength={12000} value={draft.input} onChange={(e) => setInput(e.target.value)}
          placeholder="例如：修复登录页刷新后状态丢失的 Bug，补充回归测试。也可以输入“使用我的代码审查工作流”。" />
        <div className="flex flex-wrap gap-2" aria-label="需求示例">
          {EXAMPLES.map(([label, text]) => (
            <Button key={label} type="button" size="xs" variant="outline" onClick={() => setInput(text)}>{label}</Button>
          ))}
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="new-name">{workflowMode ? "工作流名称" : "Session 名称"}</Label>
          <Input id="new-name" maxLength={80} value={draft.name} placeholder="根据需求自动填入，可修改"
            onChange={(e) => update((d) => ({ ...d, name: e.target.value, nameTouched: true }))} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-space">执行空间</Label>
          <NativeSelect id="new-space" value={draft.space} onChange={(e) => {
            const space = e.target.value;
            update((d) => ({ ...d, space, worker: spaceWorkers(workers, space, me).some((w) => w.id === d.worker) ? d.worker : "" }));
          }}>
            <option value="">我的私有空间</option>
            {teams.map((team) => <option key={team.id} value={team.id}>{team.name} · Team 共同可见</option>)}
          </NativeSelect>
        </div>
        {!workflowMode && (
          <div className="space-y-1.5">
            <Label htmlFor="new-source">代码来源</Label>
            <NativeSelect id="new-source" value={draft.source} onChange={(e) => update((d) => ({ ...d, source: e.target.value as Draft["source"] }))}>
              <option value="">请选择目标源码，不能默认使用空项目</option>
              <option value="new">明确创建一个全新项目</option>
              <option value="git">开发已有 Git 仓库</option>
            </NativeSelect>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="new-worker">执行 Worker</Label>
          <NativeSelect id="new-worker" value={draft.worker} onChange={(e) => update((d) => ({ ...d, worker: e.target.value }))}>
            <option value="">稍后分配 Worker</option>
            {choices.map((w) => <option key={w.id} value={w.id}>{w.name} · {w.capabilities.isolation === "dedicated" ? "专属环境" : "个人本机"}</option>)}
          </NativeSelect>
        </div>
      </div>
      {!workflowMode && draft.source === "git" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="new-url">Git URL</Label>
            <Input id="new-url" value={draft.url} placeholder="https://…/project.git 或 git@…"
              onChange={(e) => update((d) => ({ ...d, url: e.target.value }))} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-ref">分支 / Tag / Commit（可选）</Label>
            <Input id="new-ref" value={draft.ref} placeholder="默认分支" onChange={(e) => update((d) => ({ ...d, ref: e.target.value }))} />
          </div>
        </div>
      )}
    </div>
  );
}

interface PlanProps {
  draft: Draft;
  update: Update;
  match: MatchStatus;
  onChoice: (choice: string) => void;
  recommendWorker: string;
  setRecommendWorker: (id: string) => void;
  recommendation: { start: (worker: string) => Promise<void>; apply: () => void; stop: () => Promise<void> };
  onOpenGeneration: () => void;
  onImported: (name: string) => void;
  setError: (message: string) => void;
}

export function PlanStep(props: PlanProps) {
  const { draft, update } = props;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="编排方式">
        {PLAN_METHODS.map(([value, title, detail]) => (
          <label key={value} data-method={value}
            className={cn("relative flex cursor-pointer flex-col gap-1.5 rounded-xl border bg-card p-4 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
              draft.method === value ? "border-primary bg-primary/5 shadow-[inset_0_0_0_1px_var(--primary)]" : "hover:bg-accent")}>
            <input type="radio" name="plan-method" value={value} checked={draft.method === value} className="sr-only"
              onChange={() => { props.setError(""); update((d) => ({ ...d, method: value })); }} />
            <strong className="text-sm">{title}</strong>
            <small className="text-xs leading-5 text-muted-foreground">{detail}</small>
          </label>
        ))}
      </div>
      {draft.method === "template" && <TemplatePanel {...props} />}
      {draft.method === "recommend" && <RecommendPanel {...props} />}
      {draft.method === "import" && <ImportPanel {...props} />}
    </div>
  );
}

function MatchCard({ match, onChoice }: { match: MatchStatus; onChoice: (choice: string) => void }) {
  return (
    <div className="rounded-xl border border-primary/20 bg-primary/5 px-5 py-4" aria-live="polite">
      <strong id="match-name" className="text-base">{match.title}</strong>
      <p id="match-reason" className="mt-1 text-xs text-muted-foreground">{match.reason}</p>
      {match.alternatives.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {match.alternatives.map((alt) => (
            <Button key={alt.name} type="button" size="xs" variant="outline"
              onClick={() => onChoice(alt.selection.workflow_id ? "workflow:" + alt.selection.workflow_id : "template:" + alt.selection.template_id)}>
              {alt.name} · {alt.tasks} Tasks
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

function TemplatePanel({ draft, match, onChoice }: PlanProps) {
  const { templates, workflows } = useConsole();
  const [query, setQuery] = useState("");
  const runnable = workflows.filter((f) => f.role !== "viewer" && !f.archived);
  const chain = (steps: Step[]) => steps.map((s) => TOOLS[s.tool] ?? s.tool).join(" → ");
  const cards = useMemo(() => [
    ["auto", "按需求自动匹配", "根据这次需求的关键词，从模板与我的工作流中选出最合适的一个。", ""],
    ...runnable.map((f: Workflow) => ["workflow:" + f.id, f.name, `我的 / 共享工作流 · v${f.version}`, chain(f.steps)]),
    ...templates.map((t: Template) => ["template:" + t.id, t.name, t.description ?? "", chain(t.steps)]),
  ], [runnable, templates]);
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return (
    <div className="space-y-4">
      <MatchCard match={match} onChoice={onChoice} />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="new-workflow">工作流 / 模板</Label>
          <NativeSelect id="new-workflow" value={draft.choice} onChange={(e) => onChoice(e.target.value)}>
            <option value="auto">自动匹配 · 按这次需求</option>
            <option value="custom">自定义 · 编辑任务列表</option>
            <optgroup label="我的与共享的可运行工作流">
              {runnable.map((f) => <option key={f.id} value={"workflow:" + f.id}>{f.name} · v{f.version}</option>)}
            </optgroup>
            <optgroup label={`预定义模板 · ${templates.length} 个`}>
              {templates.map((t) => <option key={t.id} value={"template:" + t.id}>{t.name}</option>)}
            </optgroup>
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="template-search">搜索模板</Label>
          <Input id="template-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="例如：Bug、审查、Web、CLI" />
        </div>
      </div>
      <div id="template-cards" className="grid max-h-80 gap-2.5 overflow-y-auto sm:grid-cols-2">
        {cards.filter(([, ...text]) => terms.every((term) => text.join(" ").toLowerCase().includes(term))).map(([value, title, detail, agents]) => (
          <button key={value} type="button" data-pick={value} onClick={() => onChoice(value)} aria-pressed={draft.choice === value}
            className={cn("flex flex-col items-start gap-1 rounded-lg border bg-card px-4 py-3 text-left transition-colors hover:bg-accent",
              draft.choice === value && "border-primary bg-primary/5")}>
            <strong className="text-[13px]">{title}</strong>
            <small className="text-xs leading-5 text-muted-foreground">{detail}</small>
            {agents && <span className="text-[11px] text-primary/80">{agents}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

const GENERATION_LABELS: Record<string, string> = {
  submitting: "正在提交生成任务", preparing: "正在准备设计工作区", ready: "建议已生成并通过格式校验，采用后可继续编辑。",
  submitted: "KiroCrew 已收到生成任务", running: "后台 Kiro Agent 正在设计任务与提示词", ready_to_dispatch: "等待派发",
  waiting_approval: "等待原生工具权限批准", dispatch_unknown: "原生派发待核实，不重复发送", blocked: "生成任务需要处理",
  invalid: "返回内容格式无效", stopping: "正在停止生成任务", stopped: "生成任务已停止", uncertain: "提交回执不明确；可安全核实同一请求",
  failed: "生成失败",
};

function RecommendPanel({ draft, recommendWorker, setRecommendWorker, recommendation, onOpenGeneration, setError }: PlanProps) {
  const { workers, me } = useConsole();
  const choices = spaceWorkers(workers, draft.space, me);
  const job = draft.generation;
  const active = generationActive(job);
  const stale = !!job && job.fingerprint !== fingerprint(draft.input, draft.tasks, draft.space);
  const [busy, setBusy] = useState(false);
  const run = (action: () => Promise<void> | void) => async () => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const status = !job ? "后台生成会实际调用 Kiro Agent；生成结果先供你审阅。" : job.applied ? "已采用建议，可继续修改各 Task 和提示词。"
    : (GENERATION_LABELS[job.status] ?? STATES[job.status] ?? job.status) + (stale ? " 当前需求或任务已变化，旧建议不会覆盖当前草稿。" : "")
      + (job.error ? " " + job.error : "") + (job.poll_error ? " " + job.poll_error : "");
  return (
    <section className="space-y-4 rounded-xl border bg-card p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">KiroCrew 工作流建议</h3>
        <span className="text-[11px] text-muted-foreground">后台 Agent 生成 · 用户采用</span>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1 space-y-1.5">
          <Label htmlFor="recommend-worker">生成建议的 Worker</Label>
          <NativeSelect id="recommend-worker" value={recommendWorker} onChange={(e) => setRecommendWorker(e.target.value)}>
            {!choices.length && <option value="">此空间尚无可用 Worker</option>}
            {choices.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </NativeSelect>
        </div>
        <Button type="button" id="recommend-start" disabled={busy || active || (!recommendWorker && job?.status !== "uncertain")}
          onClick={run(() => recommendation.start(recommendWorker))}>
          {job?.status === "uncertain" ? "核实上次提交" : active ? "KiroCrew 正在推荐…" : "让 KiroCrew 推荐"}
        </Button>
        {job?.status === "ready" && !job.applied && (
          <Button type="button" variant="outline" id="recommend-apply" disabled={stale || busy} onClick={run(recommendation.apply)}>采用建议</Button>
        )}
        {active && job?.id && <Button type="button" variant="ghost" id="recommend-stop" disabled={busy} onClick={run(recommendation.stop)}>停止生成</Button>}
        {job?.id && job.native_parent && <Button type="button" variant="ghost" id="recommend-open" onClick={onOpenGeneration}>在 KiroCrew 查看 ↗</Button>}
      </div>
      <p id="recommend-status" role="status" className="text-sm">{status}</p>
      {job?.native_task_id && (
        <p id="recommend-native" className="font-mono text-[11px] text-muted-foreground">
          实际后端：{job.evidence?.actual_backend ?? "待核实"} · 原生 Task：{job.native_task_id} · 实际模型：{job.evidence?.actual_model ?? "未报告"}
        </p>
      )}
      {job?.config && (
        <Collapsible>
          <CollapsibleTrigger className="group flex items-center gap-1 text-xs font-medium text-primary">
            查看推荐的任务和 Agent 提示词 <ChevronDown className="size-3.5 group-data-[state=open]:rotate-180" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <pre id="recommend-preview-body" className="mt-2 max-h-72 overflow-auto rounded-md bg-muted/50 p-3 text-xs leading-5 whitespace-pre-wrap">
              {job.config.name + "\n\n" + job.config.steps.map((s, i) =>
                `${i + 1}. ${s.name} · ${TOOLS[s.tool] ?? s.tool} / ${s.model} / Effort ${s.effort || "默认"}\n${s.prompt}`).join("\n\n")}
            </pre>
          </CollapsibleContent>
        </Collapsible>
      )}
      <p className="text-xs leading-5 text-muted-foreground">以当前匹配到的任务为基础，由真实 Kiro Agent 重新设计任务与提示词。关闭窗口后可重新打开查看；采用前不会执行。</p>
    </section>
  );
}

function ImportPanel({ draft, update, onImported }: PlanProps) {
  const file = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<{ status: string; failed: boolean }>({ status: "", failed: false });
  const parse = async (content: string) => {
    if (!content.trim()) {
      setState({ status: "请先粘贴工作流内容或选择文件。", failed: true });
      return;
    }
    setState({ status: "正在解析…", failed: false });
    try {
      const result = await api<ParsedWorkflow>("/workflows/parse", "POST", { content });
      update((d) => ({ ...d, tasks: result.steps, choice: "custom", resolution: null,
        imported: { name: result.name, notes: result.notes, tasks: result.steps.length },
        name: d.nameTouched || result.name === "导入的工作流" ? d.name : result.name }));
      onImported(result.name);
      setState({ status: "", failed: false });
    } catch (error) {
      setState({ status: "解析失败：" + (error as Error).message, failed: true });
    }
  };
  return (
    <section className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="import-content">工作流内容（JSON / YAML / 含一个代码块的 Markdown）</Label>
        <Textarea id="import-content" rows={9} maxLength={100000} value={draft.importContent} className="font-mono text-xs"
          onChange={(e) => update((d) => ({ ...d, importContent: e.target.value }))}
          placeholder={'粘贴 Claude Code、Codex 或 Kiro 生成的工作流，例如 {"name": "…", "steps": [{"name": "实现", "tool": "claude", "prompt": "…"}]}'} />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => file.current?.click()}><FileUp /> 选择文件…</Button>
        <input ref={file} type="file" accept=".json,.yaml,.yml,.md,.markdown,.txt" hidden aria-label="选择工作流文件"
          onChange={async (e) => {
            const chosen = e.target.files?.[0];
            e.target.value = "";
            if (!chosen) return;
            if (chosen.size > 100_000) { setState({ status: "文件超过 100 KB。", failed: true }); return; }
            const text = await chosen.text();
            update((d) => ({ ...d, importContent: text }));
            await parse(text);
          }} />
        <Button type="button" id="import-apply" onClick={() => void parse(draft.importContent)}>解析并采用</Button>
      </div>
      <p id="import-status" role="status" className={cn("text-sm", state.failed && "text-destructive")}>
        {state.status || (draft.imported ? `已采用：${draft.imported.name} · ${draft.imported.tasks} Tasks。可在下一步检查与修改。` : "粘贴或选择文件后点击“解析并采用”。")}
      </p>
      {!!draft.imported?.notes.length && (
        <ul id="import-notes" className="list-disc space-y-1 pl-5 text-xs text-warning">
          {draft.imported.notes.map((note) => <li key={note}>{note}</li>)}
        </ul>
      )}
      <p className="text-xs leading-5 text-muted-foreground">
        由代码解析与规范化，不调用模型；兼容控制台导出与旧版 Workflow Studio。Coding 客户端也可经 MCP
        <code className="mx-1 font-mono">octopus_start</code>的<code className="mx-1 font-mono">document</code>直接提交。
      </p>
    </section>
  );
}

export function TasksStep({ draft, update, origin, models }: { draft: Draft; update: Update; origin: string; models?: Parameters<typeof AgentFields>[0]["models"] }) {
  const change = (index: number, patch: Partial<Step>) => update((d) => editTasks(d, d.tasks.map((t, i) => i === index ? { ...t, ...patch } : t)));
  const move = (index: number) => update((d) => {
    const tasks = [...d.tasks];
    [tasks[index - 1], tasks[index]] = [tasks[index], tasks[index - 1]];
    return editTasks(d, tasks);
  });
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">本次任务列表 <span className="font-normal text-muted-foreground">· {draft.tasks.length} Tasks</span></h3>
        <Button type="button" size="sm" variant="outline" id="launch-add" disabled={draft.tasks.length >= 8}
          onClick={() => update((d) => editTasks(d, [...d.tasks, newTask()]))}><Plus /> 添加任务</Button>
      </div>
      <p id="launch-origin" className="text-xs text-muted-foreground">{origin}</p>
      <ol id="launch-tasks" className="space-y-2.5" aria-label="本次任务列表">
        {draft.tasks.map((task, index) => (
          <li key={task.id}>
            <Collapsible defaultOpen={draft.tasks.length === 1} className="rounded-xl border bg-card">
              <div className="flex items-center gap-3 px-4 py-3">
                <span className="grid size-6 shrink-0 place-items-center rounded bg-muted font-mono text-[11px]">{index + 1}</span>
                <CollapsibleTrigger className="group flex min-w-0 flex-1 items-center gap-2 text-left">
                  <strong className="truncate text-sm">{task.name || "未命名任务"}</strong>
                  <small className="shrink-0 text-xs text-muted-foreground">{TOOLS[task.tool] ?? task.tool} · {task.model || "auto"}</small>
                  <ChevronDown className="ml-auto size-4 shrink-0 text-muted-foreground group-data-[state=open]:rotate-180" />
                </CollapsibleTrigger>
                <Button type="button" size="icon-sm" variant="ghost" aria-label="上移" disabled={index === 0} onClick={() => move(index)}><ArrowUp /></Button>
                <Button type="button" size="icon-sm" variant="ghost" aria-label="移除" disabled={draft.tasks.length === 1}
                  onClick={() => update((d) => editTasks(d, d.tasks.filter((_, i) => i !== index)))}><Trash2 /></Button>
              </div>
              <CollapsibleContent className="space-y-3 border-t px-4 py-4">
                <p className="font-mono text-[11px] text-muted-foreground">Task ID · {task.id}</p>
                <div className="space-y-1.5">
                  <Label htmlFor={`launch-${index}-name`}>任务名称</Label>
                  <Input id={`launch-${index}-name`} value={task.name} maxLength={80} onChange={(e) => change(index, { name: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`launch-${index}-prompt`}>Agent 提示词 / 任务说明</Label>
                  <Textarea id={`launch-${index}-prompt`} rows={5} value={task.prompt} onChange={(e) => change(index, { prompt: e.target.value })} />
                </div>
                <AgentFields idPrefix={`launch-${index}`} models={models} value={task}
                  onChange={(agent) => change(index, { ...agent, effort: agent.tool === "opencode" ? "" : agent.effort })} />
              </CollapsibleContent>
            </Collapsible>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function ConfirmStep({ draft, update, workflowMode }: { draft: Draft; update: Update; workflowMode: boolean }) {
  const { teams, workers } = useConsole();
  const source = draft.source === "git" ? `已有 Git 仓库 · ${draft.url.trim()}${draft.ref.trim() ? " @ " + draft.ref.trim() : ""}` : "全新项目";
  const rows: [string, string][] = [
    ["需求", draft.input.trim()], [workflowMode ? "工作流名称" : "Session 名称", draft.name],
    ...(workflowMode ? [] : [["代码来源", source] as [string, string]]),
    ["执行空间", teams.find((t) => t.id === draft.space)?.name ?? "我的私有空间"],
    ["执行 Worker", workers.find((w) => w.id === draft.worker)?.name ?? "稍后分配"],
    ["编排方式", PLAN_METHODS.find(([m]) => m === draft.method)![1]],
  ];
  return (
    <div className="space-y-4">
      <dl id="launch-summary" className="grid grid-cols-[7rem_1fr] gap-x-4 gap-y-2.5 rounded-xl border bg-card p-5 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="break-words whitespace-pre-wrap">{value}</dd>
          </div>
        ))}
        <dt className="text-muted-foreground">任务（{draft.tasks.length}）</dt>
        <dd>
          <ol className="list-decimal space-y-1 pl-4">
            {draft.tasks.map((t) => (
              <li key={t.id}><strong>{t.name}</strong> · {TOOLS[t.tool] ?? t.tool} · {t.model || "auto"}{t.effort ? " · Effort " + t.effort : ""}</li>
            ))}
          </ol>
        </dd>
      </dl>
      {!workflowMode && <RecoveryFields idPrefix="launch-recovery" value={draft.recovery} onChange={(recovery) => update((d) => ({ ...d, recovery }))} />}
      <p className="rounded-lg border border-warning/30 bg-warning/5 px-4 py-3 text-xs leading-6">
        {workflowMode ? "保存为可重复使用的工作流；已有工作流保存新版本，不影响正在运行的 Session。"
          : "提交时保存本次需求和任务快照，不修改工作流库或已有 Session。未选择 Worker 时先登记需求，稍后分配。"}
      </p>
    </div>
  );
}
