import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, FileUp } from "lucide-react";
import { api } from "@/api/client";
import { useCredentials, usePlacement } from "@/api/hooks";
import type { ParsedWorkflow, Step, Template, Workflow } from "@/api/types";
import { AgentFields } from "@/components/AgentFields";
import { TaskEditor } from "@/components/TaskEditor";
import { NativeSelect } from "@/components/NativeSelect";
import { RecoveryFields } from "@/components/RecoveryFields";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useConsole } from "@/features/console/context";
import { generationActive, type MatchStatus } from "@/features/launch/hooks";
import { PLAN_METHODS, editTasks, fingerprint, nameFrom, withSource, type Draft } from "@/features/launch/state";
import { SCOPE_LABELS, credentialLabel, httpsHost, usableIn } from "@/lib/credentials";
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
  const { teams } = useConsole();
  const placement = usePlacement(draft.space).data;   // D49: the platform's choice and the alternatives
  const choices = placement?.options ?? [];
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
            update((d) => ({ ...d, space, worker: "" }));   // D49: the new space's own answer, from the platform
          }}>
            <option value="">我的私有空间</option>
            {teams.filter((team) => !team.archived).map((team) => <option key={team.id} value={team.id}>{team.name} · Team 共同可见</option>)}
          </NativeSelect>
          {(() => {   // D49: as it will run — the member's own boundary may stand in for a Team without an environment
            if (!draft.space || !placement) return null;
            if (!placement.default && !placement.options.length)
              return <p id="space-environment" className="text-xs text-destructive">该 Team 当前没有可用的执行环境（未分配或已停用），你也还没有自己的执行边界，请联系平台管理员。</p>;
            if (placement.default?.scope === "shared")
              return <p id="space-environment" className="text-xs text-muted-foreground">共享执行环境：环境内会话的 Agent 互相可读，记忆未分隔。</p>;
            return null;
          })()}
        </div>
        {!workflowMode && (
          <div className="space-y-1.5">
            <Label htmlFor="new-source">代码来源</Label>
            <NativeSelect id="new-source" value={draft.source} onChange={(e) => update((d) => withSource(d, e.target.value as Draft["source"]))}>
              <option value="">请选择目标源码，不能默认使用空项目</option>
              <option value="new">明确创建一个全新项目</option>
              <option value="git">开发已有 Git 仓库</option>
            </NativeSelect>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="new-worker">执行 Worker</Label>
          <NativeSelect id="new-worker" value={draft.worker} onChange={(e) => update((d) => ({ ...d, worker: e.target.value }))}>
            <option value="">{placement?.default ? `自动：将在 ${placement.default.name} 执行` : "稍后分配 Worker"}</option>
            {choices.map((w) => <option key={w.id} value={w.id}>{w.name} · {w.isolation === "dedicated" ? "专属环境" : "个人本机"}</option>)}
          </NativeSelect>
          {!draft.worker && placement?.hint && <p id="placement-hint" className="text-xs text-muted-foreground">{placement.hint}</p>}
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
          <CredentialChoice draft={draft} update={update} />
        </div>
      )}
      {!workflowMode && draft.source === "new" && <NewRepositoryChoice draft={draft} update={update} />}
    </div>
  );
}

/** D48: publish a new project to a new repository once it completes (private; the credential must be able to create it). */
function NewRepositoryChoice({ draft, update }: { draft: Draft; update: Update }) {
  const { data = [] } = useCredentials(draft.autoPublish);
  const writers = data.filter((c) => c.access === "write" && usableIn(c, draft.space));
  return (
    <div className="space-y-1.5 text-xs">
      <label className="flex items-center gap-2">
        <input type="checkbox" id="new-repo-publish" checked={draft.autoPublish}
          onChange={(e) => update((d) => ({ ...d, autoPublish: e.target.checked }))} />
        完成后发布到新仓库（平台新建私有仓库，基线进 main，工作走 Draft PR；凭证须能建仓库）
      </label>
      {draft.autoPublish && (
        <div className="grid gap-2 sm:grid-cols-3">
          <Input aria-label="新仓库 owner" placeholder="owner（账户或组织）" value={draft.newRepoOwner}
            onChange={(e) => update((d) => ({ ...d, newRepoOwner: e.target.value }))} />
          <Input aria-label="新仓库名" placeholder="仓库名" value={draft.newRepoName}
            onChange={(e) => update((d) => ({ ...d, newRepoName: e.target.value }))} />
          <NativeSelect aria-label="新仓库凭证" value={draft.newRepoCredential}
            onChange={(e) => update((d) => ({ ...d, newRepoCredential: e.target.value }))}>
            <option value="">选择有写权限的凭证</option>
            {writers.map((c) => <option key={c.id} value={c.id}>{credentialLabel(c)}</option>)}
          </NativeSelect>
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
      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="编排方式">
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
      {draft.method === "agent" && (
        <section className="space-y-2 rounded-xl border border-primary/20 bg-primary/5 p-5 text-sm leading-7">
          <h3 className="font-semibold">由 KiroCrew 的 Kiro Agent 规划</h3>
          <p>提交后，Session 的第 1 个任务“需求分析与任务规划”由 Kiro Agent 完成：识别意图、补全需求（目标、范围、验收标准、待确认问题），
            拆分为 1–7 个任务并为每个任务分配 Coding 工具与 Prompt。规划经代码校验后自动追加到同一个 Session 并开始执行。</p>
          <p className="text-xs text-muted-foreground">规划结果、补全后的需求与每个任务都可在会话详情中查看；不满意时可对任意任务续作迭代。</p>
        </section>
      )}
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
  invalid: "推荐未通过平台校验", stopping: "正在停止生成任务", stopped: "生成任务已停止", uncertain: "提交回执不明确；可安全核实同一请求",
  failed: "生成失败",
};

function RecommendPanel({ draft, update, recommendWorker, setRecommendWorker, recommendation, onOpenGeneration, setError }: PlanProps) {
  const choices = usePlacement(draft.space).data?.options ?? [];
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
  // D66: an invalid recommendation is told by its reason alone (it already says what was wrong and what to do)
  const label = job?.status === "invalid" && job.error ? job.error
    : job?.repairing && active ? "上一版未通过平台校验，KiroCrew 正在按原因修正（自动一次）。"
    : job ? (GENERATION_LABELS[job.status] ?? STATES[job.status] ?? job.status) + (job.error ? " " + job.error : "") : "";
  const status = !job ? "后台生成会实际调用 Kiro Agent；生成结果先供你审阅。" : job.applied ? "已采用建议，可继续修改各 Task 和提示词。"
    : label + (stale ? " 当前需求或任务已变化，旧建议不会覆盖当前草稿。" : "") + (job.poll_error ? " " + job.poll_error : "");
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
      {job?.status === "invalid" && !!job.raw_result && (
        <div id="recommend-invalid" className="space-y-2 rounded-md border border-warning/40 bg-warning/5 p-3">
          <p className="text-xs leading-5">
            KiroCrew 已给出方案，但它没有通过工作流校验（原因见上）。可以导入后在下一步修改，或重新推荐。
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" id="recommend-import"
              onClick={() => update((d) => ({ ...d, method: "import", importContent: job.raw_result ?? "" }))}>导入并修改</Button>
          </div>
          <Collapsible>
            <CollapsibleTrigger className="group flex items-center gap-1 text-xs font-medium text-primary">
              查看 KiroCrew 返回的原始内容 <ChevronDown className="size-3.5 group-data-[state=open]:rotate-180" />
            </CollapsibleTrigger>
            <CollapsibleContent>
              <pre id="recommend-raw" className="mt-2 max-h-72 overflow-auto rounded-md bg-muted/50 p-3 text-xs leading-5 whitespace-pre-wrap">
                {job.raw_result}
              </pre>
            </CollapsibleContent>
          </Collapsible>
        </div>
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

export function TasksStep({ draft, update, origin, models, defaults }: { draft: Draft; update: Update; origin: string;
  models?: Parameters<typeof AgentFields>[0]["models"]; defaults?: Parameters<typeof AgentFields>[0]["defaults"] }) {
  if (draft.method === "agent") {
    return (
      <p className="rounded-xl border bg-card p-5 text-sm leading-7 text-muted-foreground">
        任务将由 KiroCrew 规划后自动生成，无需在这里编排。提交后可在会话详情中查看规划结果与每个任务的 Agent。
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <TaskEditor tasks={draft.tasks} models={models} defaults={defaults} idPrefix="launch" title="本次任务列表"
        hint={<p id="launch-origin" className="text-xs text-muted-foreground">{origin}</p>}
        onChange={(tasks) => update((d) => editTasks(d, tasks))} />
    </div>
  );
}

export function ConfirmStep({ draft, update, workflowMode }: { draft: Draft; update: Update; workflowMode: boolean }) {
  const { teams, workers } = useConsole();
  const placement = usePlacement(draft.space).data;
  const source = draft.source === "git" ? `已有 Git 仓库 · ${draft.url.trim()}${draft.ref.trim() ? " @ " + draft.ref.trim() : ""}` : "全新项目";
  const rows: [string, string][] = [
    ["需求", draft.input.trim()], [workflowMode ? "工作流名称" : "Session 名称", draft.name],
    ...(workflowMode ? [] : [["代码来源", source] as [string, string]]),
    ["执行空间", teams.find((t) => t.id === draft.space)?.name ?? "我的私有空间"],
    ["执行 Worker", workers.find((w) => w.id === draft.worker)?.name ??
      (placement?.default ? `自动（${placement.default.name}）` : "稍后分配")],
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
        <dt className="text-muted-foreground">任务（{draft.method === "agent" ? "由 KiroCrew 规划" : draft.tasks.length}）</dt>
        <dd>
          {draft.method === "agent" && <span className="text-muted-foreground">第 1 个任务：需求分析与任务规划（Kiro）；其余任务规划后自动生成</span>}
          <ol className="list-decimal space-y-1 pl-4">
            {draft.method !== "agent" && draft.tasks.map((t) => (
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

/** The source credential for an HTTPS repository (D35 §4): matched by the platform unless the user names one. */
function CredentialChoice({ draft, update }: { draft: Draft; update: Update }) {
  const host = httpsHost(draft.url);
  const { data = [] } = useCredentials(!!host);
  const options = data.filter((c) => c.host === host && usableIn(c, draft.space));
  const known = options.some((c) => c.id === draft.credential);
  useEffect(() => {
    if (draft.credential && !known) update((d) => ({ ...d, credential: "" }));
  }, [draft.credential, known, update]);
  if (!host) return null;
  return (
    <div className="space-y-1.5 sm:col-span-2">
      <Label htmlFor="new-credential">仓库凭证</Label>
      <NativeSelect id="new-credential" value={known ? draft.credential : ""}
        onChange={(e) => update((d) => ({ ...d, credential: e.target.value }))}>
        <option value="">自动匹配（按仓库选择可用凭证）</option>
        {options.map((c) => <option key={c.id} value={c.id}>{c.name} · {SCOPE_LABELS[c.scope]} · {c.hint}</option>)}
      </NativeSelect>
      <p className="text-xs text-muted-foreground">私有仓库需要凭证：在「设置 → 代码仓库凭证」中添加；公开仓库可以不选。</p>
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" id="new-auto-publish" checked={draft.autoPublish}
          onChange={(e) => update((d) => ({ ...d, autoPublish: e.target.checked }))} />
        完成后自动发布 Draft PR（需要有写权限的凭证；不勾选时完成后由你确认发布）
      </label>
    </div>
  );
}
