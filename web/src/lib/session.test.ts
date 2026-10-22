import { describe, expect, test } from "vitest";
import type { Run, RunTask } from "@/api/types";
import { EMPTY_FILTERS, filtersFromUrl, listParams, type RunListFilters } from "@/lib/runList";
import {
  canContinue,
  canDelete,
  canRecover,
  canRetryTask,
  canStop,
  closeAction,
  crewEntryReason,
  rerunPlan,
  statusText,
} from "@/lib/session";

const task = (id: string, status: string, attempts = 0): RunTask => ({
  id, run_id: "run-1", ordinal: 0, status, current_attempt: null,
  spec: { id, name: id, tool: "claude", model: "auto", effort: "", prompt: "p" },
  attempts: Array.from({ length: attempts }, (_, i) => ({
    id: `a-${id}-${i}`, number: i + 1, status: "blocked", spec: { id, name: id, tool: "claude", model: "auto", effort: "", prompt: "p" },
    native_id: null, context_hash: null, evidence: {}, result: null, error: null, created: 0, updated: 0,
  })),
});

const run = (overrides: Partial<Run> = {}): Run => ({
  id: "run-1", name: "修复登录", input: "修复登录失败", status: "blocked", lifecycle: "open", desired: "active", version: 3,
  owner_id: "u1", team_id: null, worker_id: "w1", workflow_id: null, workflow_name: "Bug 修复", workflow_version: 1,
  native_parent: "octopus-run-1", workspace: "/w", purpose: null, error: null, source: { type: "new" },
  progress: { accepted: 0, total: 2 }, tasks: [task("code", "blocked", 1), task("review", "pending")], created: 1, updated: 2,
  ...overrides,
});

describe("session actions follow the classic console rules", () => {
  test("continue is blocked while active, when not open, and for workflow-design runs", () => {
    expect(canContinue(run())).toBe(true);
    expect(canContinue(run({ status: "running" }))).toBe(false);
    expect(canContinue(run({ lifecycle: "closed" }))).toBe(false);
    expect(canContinue(run({ purpose: "workflow_design" }))).toBe(false);
  });

  test("a task can be re-run only when every earlier task is accepted", () => {
    const r = run({ tasks: [task("code", "accepted"), task("review", "blocked", 1)] });
    expect(canRetryTask(r, 0)).toBe(true);
    expect(canRetryTask(r, 1)).toBe(true);
    expect(canRetryTask(run(), 1)).toBe(false);
  });

  test("recovery needs a stopped or failed run with a native parent and an attempted unfinished task", () => {
    expect(canRecover(run())).toBe(true);
    expect(canRecover(run({ status: "running" }))).toBe(false);
    expect(canRecover(run({ native_parent: null }))).toBe(false);
    expect(canRecover(run({ tasks: [task("code", "blocked", 0)] }))).toBe(false);
    expect(canRecover(run({ purpose: "workflow_design" }))).toBe(false);
  });

  test("stop, close/reopen and delete availability", () => {
    expect(canStop(run())).toBe(true);
    expect(canStop(run({ status: "stopping" }))).toBe(false);
    expect(closeAction(run())).toEqual({ action: "close", label: "关闭", disabled: false });
    expect(closeAction(run({ lifecycle: "closed" }))).toEqual({ action: "reopen", label: "重新打开", disabled: false });
    expect(closeAction(run({ status: "stopping" })).disabled).toBe(true);
    expect(canDelete(run({ lifecycle: "deleted" }))).toBe(false);
  });

  test("the native entry explains why it is unavailable", () => {
    expect(crewEntryReason(run())).toBe("");
    expect(crewEntryReason(run({ lifecycle: "closed" }))).toContain("重新打开");
    expect(crewEntryReason(run({ native_parent: null }))).toContain("尚未建立");
  });

  test("status text prefers the attention title and rework state", () => {
    expect(statusText(run({ attention: { title: "规划任务已停止" } }))).toBe("规划任务已停止");
    expect(statusText(run({ lifecycle: "closed" }))).toBe("会话已关闭");
    expect(statusText(run({ status: "running", recovery: {
      policy: { enabled: true, max_cycles: 2 }, used: 1, history: [],
      latest: { number: 1, status: "applied", failed_task_id: "review" } } }))).toBe("正在返工与复审");
    expect(statusText(run({ status: "waiting_worker" }))).toBe("待分配 Worker");
  });
});

describe("session list query (D56)", () => {
  const params = (filters: Partial<RunListFilters>, space: string | null = "") =>
    Object.fromEntries(new URLSearchParams(listParams({ ...EMPTY_FILTERS, ...filters }, space)));

  test("the space, the page and the list view", () => {
    expect(params({})).toEqual({ page: "1", view: "list", limit: "50", space: "personal" });
    expect(params({}, "team-1").space).toBe("team-1");
    expect(params({}, null).space).toBeUndefined();
  });

  test("a state group is statuses and the lifecycle", () => {
    expect(params({ group: "attention" }).status?.split(",")).toContain("blocked");
    expect(params({ group: "attention" }).lifecycle).toBe("open");
    expect(params({ group: "done" })).toMatchObject({ status: "completed", lifecycle: "open" });
    expect(params({ group: "closed" })).toMatchObject({ lifecycle: "closed" });
    expect(params({ group: "closed" }).status).toBeUndefined();
  });

  test("the search and the filters, trimmed; empty ones are left out", () => {
    expect(params({ q: " 订单 缓存 ", tool: "codex", repo: "github.com/o/r", since: "2026-10-20" }))
      .toMatchObject({ q: "订单 缓存", tool: "codex", repo: "github.com/o/r", since: String(new Date(2026, 9, 20).getTime() / 1000) });
    expect(params({ tool: "" }).tool).toBeUndefined();
  });

  test("the filters come back from the address bar", () => {
    expect(filtersFromUrl("?run=run-a&q=%E8%AE%A2%E5%8D%95&group=attention&tool=codex&group2=x"))
      .toEqual({ ...EMPTY_FILTERS, q: "订单", group: "attention", tool: "codex" });
    expect(filtersFromUrl("?group=nope").group).toBe("all");
  });
});

describe("rerunPlan", () => {
  const spec = (id: string, extra: Record<string, unknown> = {}) => ({ id, name: id, tool: "claude" as const, model: "auto", effort: "", prompt: "p", ...extra });
  const run = { tasks: [
    { ...task("design", "accepted"), spec: spec("design", { kind: "design" }) },
    { ...task("backend", "accepted"), spec: spec("backend", { kind: "implement", depends_on: ["design"] }) },
    { ...task("frontend", "accepted"), spec: spec("frontend", { kind: "implement", depends_on: ["design"] }) },
    { ...task("check", "accepted"), spec: spec("check", { kind: "test", depends_on: ["backend"] }) },
    { ...task("deliver", "pending"), spec: spec("deliver", { kind: "deliver" }) },
  ] } as unknown as Run;

  test("dependents, later review / test tasks and unaccepted tasks always rerun", () => {
    const { rerun, forced } = rerunPlan(run, "frontend", new Set());
    expect([...rerun]).toEqual(["frontend", "check", "deliver"]);
    expect(Object.fromEntries(forced)).toEqual({ check: "审查 / 测试在改动后必须复验", deliver: "尚未验收" });
    const fromDesign = rerunPlan(run, "design", new Set());
    expect(fromDesign.forced.get("backend")).toBe("依赖重跑的任务");
  });
});
