// Launch wizard state: one requirement → plan method → tasks → confirm. Pure helpers, tested without React.
import type { DesignJob, RecoveryPolicy, Resolution, Step } from "@/api/types";
import type { LaunchSeed } from "@/features/console/context";

export type PlanMethod = "agent" | "template" | "recommend" | "import";

export const STEPS = ["需求", "编排方式", "任务与 Agent", "确认"] as const;

export const PLAN_METHODS: [PlanMethod, string, string][] = [
  ["agent", "KiroCrew 自动规划", "Kiro Agent 识别意图、补全需求、拆分任务并分配 Agent，直接开始执行"],
  ["template", "参考模板", "按需求自动匹配，或选择模板 / 我的工作流"],
  ["recommend", "KiroCrew 推荐（先审阅）", "由 Kiro Agent 设计工作流与 Prompt，审阅并采用后再提交"],
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
  /** A source credential named by the user (D35); "" leaves it to matching by repository. */
  credential: string;
  /** D47: publish a Draft PR / MR on its own once the Session completes (a git source; a new project with newRepo*). */
  autoPublish: boolean;
  /** D48: a new project's new repository to publish to on completion (with autoPublish). */
  newRepoOwner: string;
  newRepoName: string;
  newRepoCredential: string;
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
    source: workflowMode ? "new" : "", url: "", ref: "", credential: "", autoPublish: false, newRepoOwner: "", newRepoName: "", newRepoCredential: "",
    method: explicit && !workflowMode ? "template" : workflowMode ? "recommend" : "agent",
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
/** A repository owner or name the platform accepts (octopus/publish.py check_repository). */
const REPOSITORY_PART = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/;

/** A source change starts its publish choice afresh: the git and new-project options share the flag (final review). */
export function withSource(draft: Draft, source: Draft["source"]): Draft {
  return { ...draft, source, autoPublish: draft.source === source ? draft.autoPublish : false };
}

export function validateStep(step: number, draft: Draft,
  { workflowMode, teamWithoutEnvironment, archivedTeam }: { workflowMode: boolean; teamWithoutEnvironment?: string; archivedTeam?: string }): string {
  const input = draft.input.trim();
  if (step === 0) {
    if (input.length < 3) return "请填写这次要完成什么（至少 3 个字符）。";
    if (archivedTeam) return `Team「${archivedTeam}」已归档，不能提交新会话；请选择其他执行空间。`;
    if (!workflowMode && teamWithoutEnvironment)
      return `Team「${teamWithoutEnvironment}」当前没有可用的执行环境（未分配或已停用），你也还没有自己的执行边界，请联系平台管理员。`;
    if (!draft.name.trim()) return "请填写名称。";
    if (!workflowMode && !draft.source) return "请明确选择新建项目或目标 Git 仓库，避免把空工作区当作已有项目。";
    if (!workflowMode && draft.source === "git" && !draft.url.trim()) return "请填写 Git URL。";
    if (!workflowMode && draft.source === "new" && draft.autoPublish) {   // D48: never dropped without a word
      if (!draft.newRepoOwner.trim() || !draft.newRepoName.trim() || !draft.newRepoCredential)
        return "完成后发布到新仓库：请填写新仓库的 owner、仓库名并选择凭证，或取消勾选。";
      if (![draft.newRepoOwner.trim(), draft.newRepoName.trim()].every((part) => REPOSITORY_PART.test(part) && !part.includes("..")))
        return "新仓库的 owner 与仓库名只能含字母、数字、. _ -（以字母、数字或 _ 开头）。";
    }
    return "";
  }
  if (draft.method === "agent" && (step === 1 || step === 2)) return workflowMode ? "保存工作流需要任务列表，请选择其他编排方式。" : "";
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
  const plan = draft.method === "agent" ? { plan: "agent" as const } : selectionOf(draft);
  const source = draft.source === "git"
    ? { type: "git" as const, url: draft.url.trim(), ...(draft.ref.trim() ? { ref: draft.ref.trim() } : {}) }
    : { type: "new" as const };
  return {
    ...plan, input: draft.input.trim(), name: draft.name, source, recovery: draft.recovery,
    ...(draft.source === "git" && draft.credential ? { credentials: { source: draft.credential } } : {}),
    ...(draft.source === "git" && draft.autoPublish ? { publish: { auto: true } } : {}),
    ...(draft.source === "new" && draft.autoPublish && draft.newRepoOwner.trim() && draft.newRepoName.trim() && draft.newRepoCredential
      ? { publish: { auto: true, repository: { owner: draft.newRepoOwner.trim(), name: draft.newRepoName.trim(), private: true },
                     credential: draft.newRepoCredential } } : {}),
    ...(draft.space ? { team_id: draft.space } : {}), ...(draft.worker ? { worker_id: draft.worker } : {}),
  };
}

/** Editing any task makes the plan "custom": later requirement edits no longer overwrite it. */
export function editTasks(draft: Draft, tasks: Step[]): Draft {
  return { ...draft, tasks, choice: "custom", resolution: null };
}

export const fingerprint = (input: string, steps: Step[], team = "") => JSON.stringify({ input: input.trim(), steps, team_id: team });
