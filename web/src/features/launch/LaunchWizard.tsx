import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Check } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, command } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { spaceWorkers, useConsole, type LaunchSeed } from "@/features/console/context";
import { useDraft, useMatch, useRecommendation } from "@/features/launch/hooks";
import { STEPS, draftKey, initialDraft, loadDraft, restoreDraft, submitBody, validateStep } from "@/features/launch/state";
import { ConfirmStep, NeedStep, PlanStep, TasksStep } from "@/features/launch/steps";
import { openCrewSession } from "@/lib/crew";
import { cn } from "@/lib/utils";

export function LaunchWizard({ seed }: { seed: LaunchSeed }) {
  const { me, space, selected, workers, teams, templates, workflows, close, select } = useConsole();
  // A Team without an execution environment takes no new Sessions (D28); null means none, absent means an older platform.
  const unassigned = (target: string) =>
    teams.find((t) => t.id === target && (t.environment === null || t.environment?.enabled === false))?.name;
  const archived = (target: string) => teams.find((t) => t.id === target && !!t.archived)?.name;
  const client = useQueryClient();
  const workflowMode = seed.mode === "workflow";
  const key = draftKey(me.id, space, selected, seed);
  const { draft, ref, update, storageError } = useDraft(key, () => {
    const eligible = spaceWorkers(workers, space, me);
    return restoreDraft(seed, loadDraft(key), initialDraft(seed, { worker: eligible.length === 1 ? eligible[0].id : "", space }));
  });
  const { status: matchStatus, schedule, settle, describe } = useMatch(ref, update, { templates, workflows });
  const recommendation = useRecommendation(ref, update);
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const eligible = spaceWorkers(workers, draft.space, me);
  const [recommendWorker, setRecommendWorker] = useState(() => draft.generation?.request?.worker_id ?? draft.worker ?? eligible[0]?.id ?? "");
  const models = workers.find((w) => w.id === (draft.worker || recommendWorker))?.capabilities.models;

  useEffect(() => {
    if (!recommendWorker && eligible[0]) setRecommendWorker(eligible[0].id);
  }, [recommendWorker, eligible]);

  // Matching follows the requirement, the choice and the space; a custom plan is never re-matched.
  useEffect(() => {
    if (draft.choice === "custom") return;
    return schedule();
  }, [draft.input, draft.choice, draft.space, schedule]);

  useEffect(() => {
    if (seed.steps) describe("使用当前任务列表的副本", "编辑此草稿不修改原 Session。");
  }, [seed.steps, describe]);

  const origin = useMemo(() => draft.method === "import" && draft.imported ? `来自导入的工作流“${draft.imported.name}”。`
    : draft.generation?.applied ? "来自已采用的 KiroCrew 建议。" : matchStatus.title + "。", [draft, matchStatus.title]);

  function onChoice(choice: string) {
    if (choice === "custom") {
      update((d) => ({ ...d, choice, resolution: null, tasks: d.tasks.length ? d.tasks
        : structuredClone(templates.find((t) => t.id === "proof")?.steps ?? []) }));
      describe("自定义工作流 · 已保留你的任务配置", "修改需求不会覆盖这些任务；需要重新匹配时，选择“自动匹配”。");
    } else {
      update((d) => ({ ...d, choice, resolution: null }));
    }
  }

  async function go(target: number) {
    setError("");
    if (target <= step) { setStep(target); return; }
    if (target > 1 && ref.current.method !== "agent") await settle();
    for (let index = step; index < target; index++) {
      const problem = validateStep(index, ref.current, { workflowMode, teamWithoutEnvironment: unassigned(ref.current.space),
        archivedTeam: archived(ref.current.space) });
      if (problem) {
        setStep(index);
        setError(problem);
        return;
      }
    }
    setStep(target);
  }

  async function submit() {
    if (ref.current.method !== "agent") await settle();
    for (let index = 0; index < STEPS.length - 1; index++) {
      const problem = validateStep(index, ref.current, { workflowMode, teamWithoutEnvironment: unassigned(ref.current.space),
        archivedTeam: archived(ref.current.space) });
      if (problem) { setStep(index); throw new Error(problem); }
    }
    const current = ref.current;
    if (workflowMode) {
      const existing = seed.workflow;
      await api("/workflows" + (existing?.id ? "/" + existing.id + "/versions" : ""), "POST",
        { name: current.name, steps: current.tasks, ...(existing?.id ? { expected_version: existing.version } : {}) });
      sessionStorage.removeItem(key);
      close();
      await client.invalidateQueries({ queryKey: keys.workflows });
      toast.success("工作流已保存，可从工作流库重复使用。");
      return;
    }
    const run = await command<Run>("/runs", submitBody(current));
    sessionStorage.removeItem(key);
    close();
    await client.invalidateQueries({ queryKey: keys.runs });
    select(run.id);
    toast.success("需求与工作流已提交，任务快照已保存。");
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (step < STEPS.length - 1) { await go(step + 1); return; }
    setBusy(true);
    setError("");
    try {
      await submit();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const last = step === STEPS.length - 1;
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && close()}>
      <DialogContent className="max-h-[94svh] gap-0 overflow-hidden p-0 sm:max-w-4xl">
        <form onSubmit={onSubmit} noValidate className="flex max-h-[94svh] flex-col" id="launch-wizard">
          <DialogHeader className="space-y-4 border-b px-6 pt-6 pb-4 text-left">
            <div>
              <p className="text-[11px] font-semibold tracking-[0.18em] text-muted-foreground">发起开发 · 向导</p>
              <DialogTitle className="text-xl">{workflowMode ? "由需求设计工作流" : "从需求开始构建任务"}</DialogTitle>
              <DialogDescription className="sr-only">需求、编排方式、任务与 Agent、确认</DialogDescription>
            </div>
            <ol className="grid grid-cols-4 gap-1" aria-label="向导步骤">
              {STEPS.map((label, index) => (
                <li key={label}>
                  <button type="button" onClick={() => void go(index)} data-goto={index} aria-current={index === step ? "step" : undefined}
                    className={cn("flex w-full flex-col items-center gap-1 rounded-md py-1.5 text-[11px] text-muted-foreground sm:flex-row sm:gap-2 sm:px-2 sm:text-xs",
                      index === step && "font-semibold text-foreground")}>
                    <span className={cn("grid size-6 shrink-0 place-items-center rounded-full border bg-card text-[11px]",
                      index === step && "border-primary bg-primary text-primary-foreground",
                      index < step && "border-primary/30 bg-primary/10 text-primary")}>
                      {index < step ? <Check className="size-3.5" /> : index + 1}
                    </span>
                    {label}
                  </button>
                </li>
              ))}
            </ol>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5" data-page={step}>
            {step === 0 && <NeedStep draft={draft} update={update} workflowMode={workflowMode} />}
            {step === 1 && (
              <PlanStep draft={draft} update={update} match={matchStatus} onChoice={onChoice} setError={setError}
                recommendWorker={recommendWorker} setRecommendWorker={setRecommendWorker} recommendation={recommendation}
                onImported={(name) => describe("已导入：" + name, "按提交的工作流运行；可在任务列表中继续修改。")}
                onOpenGeneration={() => {
                  const job = ref.current.generation;
                  if (job?.id && job.native_parent) openCrewSession(job.id, job.native_parent).catch((e: Error) => toast.error(e.message));
                }} />
            )}
            {step === 2 && <TasksStep draft={draft} update={update} origin={origin} models={models} />}
            {step === 3 && <ConfirmStep draft={draft} update={update} workflowMode={workflowMode} />}
          </div>
          {error && <p role="alert" className="px-6 pb-2 text-sm text-destructive" id="wizard-error">{error}</p>}
          <DialogFooter className="items-center gap-2 border-t bg-muted/40 px-6 py-4 sm:justify-between">
            <span className="text-xs text-muted-foreground">
              {storageError ? "浏览器无法保存草稿；当前输入仍可提交。" : "草稿仅属于当前用户、开发空间与来源 Session。"}
            </span>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={close} disabled={busy}>取消</Button>
              <Button type="button" variant="outline" onClick={() => void go(step - 1)} disabled={step === 0 || busy} id="wizard-back">上一步</Button>
              {last ? (
                <Button type="submit" disabled={busy} id="wizard-submit">
                  {busy ? "正在提交…" : workflowMode ? "保存工作流" : "确认任务并提交"}
                </Button>
              ) : (
                <Button type="submit" id="wizard-next">下一步</Button>
              )}
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
