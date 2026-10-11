// Launch wizard state: one requirement → plan method → tasks → confirm. Pure helpers, tested without React.
import type { DesignJob, RecoveryPolicy, Resolution, Step } from "@/api/types";
import type { LaunchSeed } from "@/features/console/context";

export type PlanMethod = "template" | "recommend" | "import";

export const STEPS = ["需求", "编排方式", "任务与 Agent", "确认"] as const;

export const PLAN_METHODS: [PlanMethod, string, string][] = [
  ["template", "参考模板", "按需求自动匹配，或选择模板 / 我的工作流"],
  ["recommend", "KiroCrew 推荐", "由 Kiro Agent 设计工作流，并为每个 Task 匹配 Prompt"],
  ["import", "导入已生成的工作流", "粘贴或上传 Agent 预先生成的工作流，按提交内容运行"],
];

export interface Generation extends DesignJob {
  request?: { request_id: string; input: string; steps: Step[]; worker_id: string; team_id?: string };
  fingerprint?: string;
  applied?: boolean;
  poll_error?: string;
}

export interface Draft {
  input: string;
  name: string;
  nameTouched: boolean;
  space: string;
  worker: string;
  source: "" | "new" | "git";
  url: string;
  ref: string;
  method: PlanMethod;
  choice: string; // "auto" | "custom" | "workflow:<id>" | "template:<id>"
  tasks: Step[];
  resolution: Resolution | null;
  imported: { name: string; notes: string[]; tasks: number } | null;
  importContent: string;
  generation: Generation | null;
  recovery: RecoveryPolicy;
  /** The seed a workflow-mode draft was started from; a different workflow or task list starts fresh. */
  baseKey?: string;
}

export function draftKey(user: string, space: string, selected: string | null, seed: LaunchSeed): string {
  const scope = [user, space || "personal", selected || "new",
    ...(seed.mode === "workflow" ? ["workflow", seed.workflow?.id || "new"] : [])];
  return "octopus.react.launch." + scope.join("/");
}

export function initialDraft(seed: LaunchSeed, { worker, space = "" }: { worker: string; space?: string }): Draft {
  const explicit = !!(seed.workflow_id || seed.template_id || seed.steps);
  const workflowMode = seed.mode === "workflow";
  return {
    input: seed.input ?? "", name: seed.name ?? "", nameTouched: !!seed.workflow?.id, space, worker,
    source: workflowMode ? "new" : "", url: "", ref: "",
    method: explicit && !workflowMode ? "template" : "recommend",
    choice: seed.workflow_id ? "workflow:" + seed.workflow_id : seed.template_id ? "template:" + seed.template_id : seed.steps ? "custom" : "auto",
    tasks: structuredClone(seed.steps ?? []), resolution: null, imported: null, importContent: "", generation: null,
    recovery: { enabled: true, max_cycles: 2 },
  };
}

export const baseKeyOf = (seed: LaunchSeed) =>
  JSON.stringify({ workflow: seed.workflow ?? null, name: seed.name ?? null, steps: seed.steps ?? null });

const REQUIREMENT: (keyof Draft)[] = ["input", "name", "nameTouched", "source", "url", "ref", "space", "worker", "recovery", "generation", "importContent"];

/** Reopening the wizard: like the classic console, a workflow-mode draft resumes only for the same workflow and starting
 *  tasks; an explicit selection replaces the plan but keeps the requirement and any generation in flight. */
export function restoreDraft(seed: LaunchSeed, saved: Partial<Draft> | null, base: Draft): Draft {
  const fresh = { ...base, baseKey: baseKeyOf(seed) };
  if (!saved) return fresh;
  if (seed.mode === "workflow") return saved.baseKey === fresh.baseKey && saved.tasks?.length ? { ...fresh, ...saved } : fresh;
  if (!(seed.workflow_id || seed.template_id || seed.steps)) return { ...fresh, ...saved, baseKey: fresh.baseKey };
  const kept = Object.fromEntries(REQUIREMENT.filter((field) => saved[field] !== undefined).map((field) => [field, saved[field]]));
  return { ...fresh, ...kept };
}

export function loadDraft(key: string): Partial<Draft> | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

export function saveDraft(key: string, draft: Draft): boolean {
  try {
    sessionStorage.setItem(key, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function nameFrom(input: string): string {
  return input.trim().split(/[\n。！？!?]/)[0].slice(0, 60);
}

/** The first problem that keeps the user on this step, or "" when the step is complete. */
export function validateStep(step: number, draft: Draft, { workflowMode }: { workflowMode: boolean }): string {
  const input = draft.input.trim();
  if (step === 0) {
    if (input.length < 3) return "请填写这次要完成什么（至少 3 个字符）。";
    if (!draft.name.trim()) return "请填写名称。";
    if (!workflowMode && !draft.source) return "请明确选择新建项目或目标 Git 仓库，避免把空工作区当作已有项目。";
    if (!workflowMode && draft.source === "git" && !draft.url.trim()) return "请填写 Git URL。";
    return "";
  }
  if (step === 1) {
    if (draft.method === "recommend" && !draft.generation?.applied)
      return "请先让 KiroCrew 推荐并采用方案；也可以改用参考模板或导入已生成的工作流。";
    if (draft.method === "import" && !draft.imported) return "请先解析并采用导入的工作流。";
    if (!draft.tasks.length || (draft.choice !== "custom" && draft.resolution?.input !== input))
      return "请等待当前需求匹配完成，或选择一个模板 / 工作流。";
    return "";
  }
  if (step === 2) {
    if (!draft.tasks.length) return "至少需要一个任务。";
    const index = draft.tasks.findIndex((task) => !task.name.trim() || !task.prompt.trim());
    if (index >= 0) return `任务 ${index + 1} 需要名称和任务说明。`;
    return "";
  }
  return "";
}

export function selectionOf(draft: Draft) {
  if (draft.choice === "custom" || !draft.resolution) return { steps: draft.tasks };
  return draft.resolution.selection;
}

export function submitBody(draft: Draft) {
  const source = draft.source === "git"
    ? { type: "git" as const, url: draft.url.trim(), ...(draft.ref.trim() ? { ref: draft.ref.trim() } : {}) }
    : { type: "new" as const };
  return {
    ...selectionOf(draft), input: draft.input.trim(), name: draft.name, source, recovery: draft.recovery,
    ...(draft.space ? { team_id: draft.space } : {}), ...(draft.worker ? { worker_id: draft.worker } : {}),
  };
}

/** Editing any task makes the plan "custom": later requirement edits no longer overwrite it. */
export function editTasks(draft: Draft, tasks: Step[]): Draft {
  return { ...draft, tasks, choice: "custom", resolution: null };
}

export const fingerprint = (input: string, steps: Step[], team = "") => JSON.stringify({ input: input.trim(), steps, team_id: team });
