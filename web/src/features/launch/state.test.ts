import { describe, expect, test } from "vitest";
import type { Resolution, Step } from "@/api/types";
import { draftKey, initialDraft, nameFrom, restoreDraft, selectionOf, submitBody, validateStep, type Draft } from "@/features/launch/state";

const steps: Step[] = [{ id: "code", name: "实现", tool: "claude", model: "auto", effort: "", prompt: "实现" }];
const resolved = (input: string): Resolution => ({ input, name: "修复登录", workflow_name: "Bug 修复", reason: "匹配", steps,
  selection: { template_id: "bugfix" } });

const draft = (overrides: Partial<Draft> = {}): Draft => ({
  ...initialDraft({}, { worker: "" }), method: "template", input: "修复登录失败的 Bug", name: "修复登录", source: "new", ...overrides,
});

describe("launch draft", () => {
  test("drafts belong to a user, space and originating Session, and never collide with the classic console", () => {
    expect(draftKey("u1", "", null, {})).toBe("octopus.react.launch.u1/personal/new");
    expect(draftKey("u1", "team-1", "run-a", { mode: "workflow", workflow: { id: "flow-1", version: 2 } }))
      .toBe("octopus.react.launch.u1/team-1/run-a/workflow/flow-1");
  });

  test("a new requirement defaults to KiroCrew planning; a seeded selection starts from templates", () => {
    expect(initialDraft({}, { worker: "" }).method).toBe("agent");
    expect(initialDraft({ template_id: "proof" }, { worker: "" })).toMatchObject({ method: "template", choice: "template:proof" });
    expect(initialDraft({ steps }, { worker: "" })).toMatchObject({ method: "template", choice: "custom", tasks: steps });
    expect(initialDraft({ mode: "workflow", steps }, { worker: "" }).method).toBe("recommend");
  });

  test("the Session name follows the first sentence of the requirement", () => {
    expect(nameFrom("修复登录失败。并补充测试")).toBe("修复登录失败");
    expect(nameFrom("a".repeat(90))).toHaveLength(60);
  });
});

describe("wizard validation", () => {
  test("the requirement and an explicit code source come first", () => {
    expect(validateStep(0, draft({ input: "ab" }), { workflowMode: false })).toContain("至少 3");
    expect(validateStep(0, draft({ source: "" }), { workflowMode: false })).toContain("Git 仓库");
    expect(validateStep(0, draft({ source: "git", url: "" }), { workflowMode: false })).toContain("Git URL");
    expect(validateStep(0, draft({ source: "" }), { workflowMode: true })).toBe("");
  });

  test("each plan method has its own completion rule", () => {
    const ctx = { workflowMode: false };
    expect(validateStep(1, draft({ method: "recommend", tasks: steps, choice: "custom" }), ctx)).toContain("采用");
    expect(validateStep(1, draft({ method: "recommend", tasks: steps, choice: "custom", generation: { status: "ready", applied: true } }), ctx)).toBe("");
    expect(validateStep(1, draft({ method: "import", tasks: steps, choice: "custom" }), ctx)).toContain("导入");
    expect(validateStep(1, draft({ method: "template", choice: "auto", tasks: steps, resolution: resolved("旧需求") }), ctx)).toContain("匹配");
    expect(validateStep(1, draft({ method: "template", choice: "auto", tasks: steps, resolution: resolved("修复登录失败的 Bug") }), ctx)).toBe("");
  });

  test("tasks need a name and a prompt", () => {
    expect(validateStep(2, draft({ tasks: [] }), { workflowMode: false })).toContain("至少");
    expect(validateStep(2, draft({ tasks: [{ ...steps[0], prompt: " " }] }), { workflowMode: false })).toContain("任务说明");
  });
});

describe("submission", () => {
  test("KiroCrew planning submits the requirement with plan=agent and no tasks", () => {
    const body = submitBody(draft({ method: "agent", tasks: [] }));
    expect(body).toMatchObject({ plan: "agent", input: "修复登录失败的 Bug" });
    expect("steps" in body).toBe(false);
    expect(validateStep(1, draft({ method: "agent", tasks: [] }), { workflowMode: false })).toBe("");
  });

  test("a matched template is submitted by its selection; edited tasks are submitted as steps", () => {
    expect(selectionOf(draft({ choice: "auto", resolution: resolved("修复登录失败的 Bug"), tasks: steps }))).toEqual({ template_id: "bugfix" });
    expect(selectionOf(draft({ choice: "custom", tasks: steps }))).toEqual({ steps });
  });

  test("the run body carries source, space, worker and recovery", () => {
    const body = submitBody(draft({ choice: "custom", tasks: steps, source: "git", url: " https://x/y.git ", ref: "main",
      space: "team-1", worker: "w1" }));
    expect(body).toMatchObject({ steps, input: "修复登录失败的 Bug", name: "修复登录",
      source: { type: "git", url: "https://x/y.git", ref: "main" }, team_id: "team-1", worker_id: "w1",
      recovery: { enabled: true, max_cycles: 2 } });
  });
});

describe("reopening the wizard", () => {
  const running = { status: "running", id: "run-design", request: { request_id: "r1", input: "设计退款流程", steps, worker_id: "w1" } };

  test("the workflow editor's recommendation resumes for the same workflow and starting tasks", () => {
    const seed = { mode: "workflow" as const, workflow: { id: "flow-1", version: 2 }, steps, name: "退款流程" };
    const first = restoreDraft(seed, null, initialDraft(seed, { worker: "" }));
    const saved = { ...first, input: "设计退款流程", generation: running };
    const reopened = restoreDraft(seed, saved, initialDraft(seed, { worker: "" }));
    expect(reopened.input).toBe("设计退款流程");
    expect(reopened.generation?.id).toBe("run-design");
    const other = { ...seed, workflow: { id: "flow-2", version: 1 } };
    expect(restoreDraft(other, saved, initialDraft(other, { worker: "" })).generation).toBeNull();
  });

  test("an explicit template replaces the plan but keeps the requirement and a generation in flight", () => {
    const saved = { ...draft({ choice: "custom", tasks: steps, method: "import" as const }), generation: running };
    const seed = { template_id: "proof" };
    const reopened = restoreDraft(seed, saved, initialDraft(seed, { worker: "" }));
    expect(reopened).toMatchObject({ input: "修复登录失败的 Bug", source: "new", choice: "template:proof", method: "template", tasks: [] });
    expect(reopened.generation?.id).toBe("run-design");
  });

  test("without a seed the whole draft resumes", () => {
    const saved = draft({ choice: "custom", tasks: steps, method: "import" as const });
    expect(restoreDraft({}, saved, initialDraft({}, { worker: "" }))).toMatchObject({ method: "import", tasks: steps });
  });
});
