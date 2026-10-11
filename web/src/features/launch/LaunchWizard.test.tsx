import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import type { Resolution, Step } from "@/api/types";
import { makeRun, mockApi, workspaceRoutes } from "@/test/api";

const bugfix: Step[] = [
  { id: "reproduce", name: "复现与定位", tool: "kiro", model: "auto", effort: "", prompt: "复现问题" },
  { id: "fix", name: "修复与测试", tool: "claude", model: "auto", effort: "", prompt: "修复并测试" },
];
const proof: Step[] = [{ id: "code", name: "最小实现", tool: "claude", model: "auto", effort: "", prompt: "最少依赖实现" }];
const templates = [
  { id: "bugfix", name: "缺陷定位与修复", description: "定位缺陷，完成修复与回归。", steps: bugfix },
  { id: "proof", name: "最小实现与审查", description: "先实现最小功能。", steps: proof },
];

function resolve(body: { input: string; template_id?: string }): Resolution {
  const template = templates.find((t) => t.id === (body.template_id ?? "bugfix"))!;
  return { input: body.input, name: body.input.slice(0, 10), workflow_name: template.name, reason: body.template_id ? "手动指定模板" : "匹配词：bug",
    steps: template.steps, selection: { template_id: template.id } };
}

const worker = { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true,
  capabilities: { isolation: "trusted-local", workflow_design: true, models: { claude: ["auto", "sonnet"] } } };

function start(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const created = makeRun({ id: "run-new", name: "新会话", status: "waiting_worker" });
  let submitted = false;
  const calls = mockApi(workspaceRoutes([], {
    "GET /workflow-templates": templates,
    "POST /workflows/resolve": (body: unknown) => resolve(body as { input: string }),
    "GET /runs": () => (submitted ? [created] : []),
    "POST /runs": () => { submitted = true; return created; },
    [`GET /runs/${created.id}`]: created,
    ...extra,
  }));
  render(<App />);
  return calls;
}

async function openWizard() {
  await userEvent.click(await screen.findByRole("button", { name: /新建开发/ }));
  return screen.findByRole("dialog");
}

const next = (dialog: HTMLElement) => userEvent.click(within(dialog).getByRole("button", { name: "下一步" }));

async function fillNeed(dialog: HTMLElement) {
  await userEvent.type(within(dialog).getByLabelText("这次要完成什么？"), "修复登录失败的 Bug，并补充回归测试。");
  await userEvent.selectOptions(within(dialog).getByLabelText("代码来源"), "new");
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("launch wizard", () => {
  test("each step explains what is missing before moving on", async () => {
    start();
    const dialog = await openWizard();
    await next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("至少 3 个字符");
    await userEvent.type(within(dialog).getByLabelText("这次要完成什么？"), "修复登录失败的 Bug。补测试");
    expect(within(dialog).getByLabelText("Session 名称")).toHaveValue("修复登录失败的 Bug");
    await next(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Git 仓库");
    await userEvent.selectOptions(within(dialog).getByLabelText("代码来源"), "new");
    await next(dialog);
    expect(within(dialog).getByRole("radio", { name: /KiroCrew 推荐/ })).toBeChecked();
    await next(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("采用");
  });

  test("reference template: a picked card is submitted as that template", async () => {
    const calls = start();
    const dialog = await openWizard();
    await fillNeed(dialog);
    await next(dialog);
    await userEvent.click(within(dialog).getByRole("radio", { name: /参考模板/ }));
    expect(await within(dialog).findByText("已匹配：缺陷定位与修复")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /最小实现与审查/ }));
    expect(await within(dialog).findByText("已指定：最小实现与审查")).toBeInTheDocument();
    await next(dialog);
    expect(within(within(dialog).getByRole("list", { name: "本次任务列表" })).getAllByRole("listitem")).toHaveLength(1);
    await next(dialog);
    expect(within(dialog).getByText("参考模板")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "确认任务并提交" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const body = calls.find((c) => c.method === "POST" && c.path === "/v1/runs")?.body as Record<string, unknown>;
    expect(body).toMatchObject({ template_id: "proof", input: "修复登录失败的 Bug，并补充回归测试。", source: { type: "new" } });
    expect(body.steps).toBeUndefined();
    expect(window.location.search).toBe("?run=run-new");
  });

  test("import: a workflow generated ahead of time runs as submitted after review", async () => {
    const parsed = { name: "Claude Code 生成", notes: ["任务 2：忽略不支持的字段 depends_on"], steps: [
      { id: "task-1", name: "定位问题", tool: "kiro", model: "auto", effort: "", prompt: "复现登录失败" },
      { id: "task-2", name: "修复与测试", tool: "claude", model: "auto", effort: "", prompt: "修复并补充回归测试" }] };
    const calls = start({ "POST /workflows/parse": parsed });
    const dialog = await openWizard();
    await fillNeed(dialog);
    await next(dialog);
    await userEvent.click(within(dialog).getByRole("radio", { name: /导入已生成的工作流/ }));
    await next(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("导入");
    await userEvent.click(within(dialog).getByLabelText(/工作流内容/));
    await userEvent.paste("```json\n{\"steps\": []}\n```");
    await userEvent.click(within(dialog).getByRole("button", { name: "解析并采用" }));
    expect(await within(dialog).findByText(/已采用：Claude Code 生成 · 2 Tasks/)).toBeInTheDocument();
    expect(within(dialog).getByText(/depends_on/)).toBeInTheDocument();
    await next(dialog);
    expect(within(dialog).getByText(/来自导入的工作流/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByText("修复与测试"));
    const prompt = within(dialog).getByLabelText("Agent 提示词 / 任务说明");
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "修复并覆盖会话过期");
    await next(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "确认任务并提交" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs")).toBe(true));
    const body = calls.find((c) => c.method === "POST" && c.path === "/v1/runs")?.body as { steps: Step[] };
    expect(body.steps.map((s) => s.tool)).toEqual(["kiro", "claude"]);
    expect(body.steps[1].prompt).toBe("修复并覆盖会话过期");
    expect(calls.find((c) => c.path === "/v1/workflows/parse")?.body).toEqual({ content: "```json\n{\"steps\": []}\n```" });
  });

  test("KiroCrew recommendation: the Agent's tasks replace the draft only when adopted", async () => {
    const config = { name: "AI 建议", steps: [{ id: "plan", name: "Agent 设计的任务", tool: "codex", model: "auto", effort: "high", prompt: "按建议执行" }] };
    const calls = start({
      "GET /workers": [worker],
      "POST /designs": { id: "run-design", version: 1, status: "preparing" },
      "GET /designs/run-design": { id: "run-design", version: 1, status: "ready", config, native_task_id: "native-9",
        evidence: { actual_backend: "kiro" } },
    });
    const dialog = await openWizard();
    await fillNeed(dialog);
    await next(dialog);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "让 KiroCrew 推荐" })).toBeEnabled());
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/workflows/resolve")).toBe(true));
    await userEvent.click(within(dialog).getByRole("button", { name: "让 KiroCrew 推荐" }));
    await userEvent.click(await within(dialog).findByRole("button", { name: "采用建议" }));
    expect(within(dialog).getByText(/已采用建议/)).toBeInTheDocument();
    const request = calls.find((c) => c.path === "/v1/designs")?.body as Record<string, unknown>;
    expect(request).toMatchObject({ input: "修复登录失败的 Bug，并补充回归测试。", worker_id: "w1" });
    await next(dialog);
    expect(within(dialog).getByText("Agent 设计的任务")).toBeInTheDocument();
    expect(within(dialog).getByText(/来自已采用的 KiroCrew 建议/)).toBeInTheDocument();
  });
});
