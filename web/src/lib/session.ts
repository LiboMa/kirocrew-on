// Session rules shared by the list and the detail view; they mirror the classic console so both behave alike.
import type { Run } from "@/api/types";
import { ACTIVE_STATES, ATTENTION_STATES, STATES } from "@/lib/labels";

const open = (run: Run) => run.lifecycle === "open";
const design = (run: Run) => run.purpose === "workflow_design";
const resumable = (run: Run) => run.permissions?.resume !== false;

export function canContinue(run: Run): boolean {
  return !ACTIVE_STATES.has(run.status) && open(run) && !design(run) && resumable(run);
}

/** Why continuing, diagnosing, reopening and reassigning are not offered (an archived Team, P3). */
export function resumeReason(run: Run): string {
  return resumable(run) ? "" : "Team 已归档：会话可查看与停止，不能续作、诊断、确认计划、重新打开或重新分配 Worker。";
}

export const RERUN_REASONS = { depends: "依赖重跑的任务", review: "审查 / 测试在改动后必须复验", pending: "尚未验收",
  knowledge: "改动后重新整理项目知识" } as const;

/** The tasks a continue from ``taskId`` reruns when the user chose ``chosen`` among the later ones (D34 §7): the rules
 * the server applies add those not yet accepted, those depending on a rerun task and later review / test tasks. */
export function rerunPlan(run: Pick<Run, "tasks">, taskId: string, chosen: Set<string>) {
  const index = run.tasks.findIndex((t) => t.id === taskId);
  const rerun = new Set([taskId]);
  const forced = new Map<string, string>();
  for (const task of run.tasks.slice(index + 1)) {
    const why = task.status !== "accepted" ? "pending"
      : (task.spec.depends_on ?? []).some((dep) => rerun.has(dep)) ? "depends"
      : task.spec.kind === "review" || task.spec.kind === "test" ? "review" : null;
    if (why) forced.set(task.id, RERUN_REASONS[why]);
    if (why || chosen.has(task.id)) rerun.add(task.id);
  }
  return { rerun, forced };
}

export function canRetryTask(run: Run, index: number): boolean {
  return canContinue(run) && run.tasks.slice(0, index).every((task) => task.status === "accepted");
}

export function canRecover(run: Run): boolean {
  return !design(run) && !ACTIVE_STATES.has(run.status) && open(run) && resumable(run) &&
    ["blocked", "failed", "stopped"].includes(run.status) && !!run.native_parent &&
    run.tasks.some((task) => task.status !== "accepted" && (task.attempts?.length ?? 0) > 0);
}

export function canStop(run: Run): boolean {
  return open(run) && run.status !== "stopped" && run.status !== "stopping";
}

export function closeAction(run: Run): { action: "close" | "reopen"; label: string; disabled: boolean } {
  const closed = run.lifecycle === "closed";
  return { action: closed ? "reopen" : "close", label: closed ? "重新打开" : "关闭",
    disabled: run.lifecycle === "deleted" || run.status === "stopping" || (closed && !resumable(run)) };
}

export function canDelete(run: Run): boolean {
  return run.lifecycle !== "deleted" && run.permissions?.delete !== false;
}

/** Why deleting is not offered, when the server says the user may not delete this Session. */
export function deleteReason(run: Run): string {
  return run.permissions?.delete === false ? "只有 Team owner 可以删除他人创建的会话" : "";
}

export function crewEntryReason(run: Run): string {
  if (!open(run) || ["close", "delete"].includes(run.desired) || run.status === "reopening")
    return "请先重新打开此 Session，再进入 KiroCrew。";
  if (!run.worker_id || !run.native_parent) return "原生 Session 尚未建立，等待 Worker 准备完成后可打开。";
  return "";
}

export function statusText(run: Run): string {
  if (run.lifecycle === "closed") return "会话已关闭";
  if (run.status === "blocked" && run.attention) return run.attention.title;
  if (["ready", "running", "dispatching"].includes(run.status) && run.recovery?.latest?.status === "applied")
    return "正在返工与复审";
  return STATES[run.status] ?? run.status;
}

export type RunFilter = "all" | "active" | "attention" | "done" | "closed";

export const RUN_FILTERS: [RunFilter, string][] = [
  ["all", "全部"], ["active", "进行中"], ["attention", "需处理"], ["done", "已完成"], ["closed", "已关闭"],
];

function matches(run: Run, filter: RunFilter): boolean {
  if (filter === "all") return true;
  if (filter === "closed") return run.lifecycle === "closed";
  if (run.lifecycle !== "open") return false;
  if (filter === "active") return ACTIVE_STATES.has(run.status);
  if (filter === "attention") return ATTENTION_STATES.has(run.status);
  return run.status === "completed";
}

/** space null: every space (an administrator's all-Sessions view). */
export function filterRuns(runs: Run[], { space, query, filter }: { space: string | null; query: string; filter: RunFilter }): Run[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return runs.filter((run) => {
    if ((space !== null && (run.team_id ?? "") !== space) || run.lifecycle === "deleted" || !matches(run, filter)) return false;
    const text = [run.name, run.input, run.id, run.workflow_name].filter(Boolean).join(" ").toLowerCase();
    return terms.every((term) => text.includes(term));
  });
}
