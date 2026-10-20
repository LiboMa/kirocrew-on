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

const credentials = [
  { id: "cred-1", name: "bob 的 GitHub", kind: "github_pat", host: "github.com", scope: "user", access: "read", hint: "…1a2b",
    owner: { id: "u1", username: "fixture-owner" }, repos: [], disabled: false, can_manage: true },
  { id: "cred-2", name: "GitLab", kind: "gitlab_token", host: "gitlab.example.com", scope: "user", access: "read", hint: "…0000",
    owner: { id: "u1", username: "fixture-owner" }, repos: [], disabled: false, can_manage: true },
];

async function submitGit(dialog: HTMLElement, calls: { method: string; path: string; body: unknown }[], credential?: string) {
  await userEvent.type(within(dialog).getByLabelText("这次要完成什么？"), "修复私有仓库里的登录 Bug。");
  await userEvent.selectOptions(within(dialog).getByLabelText("代码来源"), "git");
  await userEvent.type(within(dialog).getByLabelText("Git URL"), "https://github.com/kirocrew/app.git");
  const select = await within(dialog).findByLabelText("仓库凭证");
  expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["自动匹配（按仓库选择可用凭证）", "bob 的 GitHub · 个人 · …1a2b"]);
  if (credential) await userEvent.selectOptions(select, credential);
  await next(dialog);
  await next(dialog);
  await next(dialog);
  await userEvent.click(within(dialog).getByRole("button", { name: "确认任务并提交" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs")).toBe(true));
  return calls.find((c) => c.method === "POST" && c.path === "/v1/runs")?.body as Record<string, unknown>;
}

describe("launch wizard", () => {
  test("a Git source can name the credential for its host", async () => {
    const calls = start({ "GET /credentials": credentials });
    const body = await submitGit(await openWizard(), calls, "cred-1");
    expect(body).toMatchObject({ source: { type: "git", url: "https://github.com/kirocrew/app.git" }, credentials: { source: "cred-1" } });
  });

  test("left to matching, no credential is named", async () => {
    const calls = start({ "GET /credentials": credentials });
    const body = await submitGit(await openWizard(), calls);
    expect(body.credentials).toBeUndefined();
  });

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
    expect(within(dialog).getByRole("radio", { name: /KiroCrew 自动规划/ })).toBeChecked();
    await userEvent.click(within(dialog).getByRole("radio", { name: /KiroCrew 推荐/ }));
    await next(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("采用");
  });

  test("KiroCrew planning: the requirement is submitted for the Kiro Agent to plan", async () => {
    const calls = start();
    const dialog = await openWizard();
    await fillNeed(dialog);
    await next(dialog);
    await next(dialog);
    expect(within(dialog).getByText(/任务将由 KiroCrew 规划后自动生成/)).toBeInTheDocument();
    await next(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "确认任务并提交" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs")).toBe(true));
    const body = calls.find((c) => c.method === "POST" && c.path === "/v1/runs")?.body as Record<string, unknown>;
    expect(body).toMatchObject({ plan: "agent", input: "修复登录失败的 Bug，并补充回归测试。", source: { type: "new" } });
    expect(body.steps).toBeUndefined();
  });

  test("the platform chooses where the Session runs unless one is picked (D49)", async () => {
    const own = { ...worker, id: "b-own", name: "owner", scope: "dedicated", capabilities: { ...worker.capabilities, isolation: "dedicated" } };
    const calls = start({ "GET /workers": [own, { ...worker, id: "w-shared", scope: "shared" }], "GET /placement": {
      space: "personal", hint: "你还没有自己的执行边界：示例提示",
      default: { id: "b-own", name: "owner", scope: "dedicated", placement: "own" },
      options: [{ id: "b-own", name: "owner", scope: "dedicated", own: true, isolation: "dedicated", models: {} },
        { id: "w-shared", name: "personal", scope: "shared", own: false, isolation: "trusted-local", models: {} }] } });
    const dialog = await openWizard();
    const select = await within(dialog).findByLabelText("执行 Worker");
    await waitFor(() => expect(within(select).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["自动：将在 owner 执行", "owner · 专属环境", "personal · 个人本机"]));
    expect(select).toHaveValue("");
    expect(dialog.querySelector("#placement-hint")).toHaveTextContent("示例提示");
    await fillNeed(dialog);
    await next(dialog);
    await next(dialog);
    await next(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "确认任务并提交" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/runs")?.body).not.toHaveProperty("worker_id");
  });

  test("a Team without an environment still takes a member who has an own boundary (D49 R2, final review)", async () => {
    const calls = start({ "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [], environment: null }],
      "GET /placement": (_b: unknown, url: URL) => url.searchParams.get("team_id") === "team-1"
        ? { space: "team-1", hint: null, default: { id: "b-own", name: "owner", scope: "dedicated", placement: "own" },
          options: [{ id: "b-own", name: "owner", scope: "dedicated", own: true, isolation: "dedicated", models: {} }] }
        : { space: "personal", hint: null, default: null, options: [] } });
    const dialog = await openWizard();
    await userEvent.selectOptions(within(dialog).getByLabelText("执行空间"), "team-1");
    await waitFor(() => expect(within(within(dialog).getByLabelText("执行 Worker")).getAllByRole("option")[0])
      .toHaveTextContent("自动：将在 owner 执行"));
    expect(dialog.querySelector("#space-environment")).toBeNull();
    await fillNeed(dialog);
    await next(dialog);
    expect(within(dialog).queryByRole("alert")).toBeNull();
    expect(within(dialog).getByRole("radio", { name: /KiroCrew 自动规划/ })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/v1/placement")).toBe(true);
  });

  test("changing the space leaves the choice to the platform again (D49)", async () => {
    start({ "GET /workers": [worker] });
    const dialog = await openWizard();
    const select = await within(dialog).findByLabelText("执行 Worker");
    await waitFor(() => expect(within(select).getAllByRole("option")[0]).toHaveTextContent("自动：将在 personal 执行"));
    await userEvent.selectOptions(select, "w1");
    await userEvent.selectOptions(within(dialog).getByLabelText("执行空间"), "team-1");
    expect(select).toHaveValue("");
    await waitFor(() => expect(within(select).getAllByRole("option")[0]).toHaveTextContent("稍后分配 Worker"));   // the Team's own answer
  });

  test("the task models are those of where it will run: the platform's default, then the one picked (D49)", async () => {
    const cold = { ...worker, id: "b-own", name: "owner", scope: "dedicated", capabilities: { models: { claude: ["auto"] } } };
    const warm = { ...worker, id: "w-shared", name: "personal", scope: "shared" };
    start({ "GET /workers": [cold, warm], "GET /placement": { space: "personal", hint: null,
      default: { id: "b-own", name: "owner", scope: "dedicated", placement: "own" },
      options: [{ id: "b-own", name: "owner", scope: "dedicated", own: true, isolation: "dedicated", models: { claude: ["auto", "opus-from-placement"] } },
        { id: "w-shared", name: "personal", scope: "shared", own: false, isolation: "trusted-local", models: { claude: ["auto", "sonnet"] } }] } });
    const dialog = await openWizard();
    await fillNeed(dialog);
    await next(dialog);
    await userEvent.click(within(dialog).getByRole("radio", { name: /参考模板/ }));
    await userEvent.click(await within(dialog).findByRole("button", { name: /最小实现与审查/ }));
    await next(dialog);
    const model = () => within(dialog).getAllByLabelText("模型")[0];
    // the platform's own answer for the default environment, not a copy kept elsewhere in the console
    expect(within(model()).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["auto", "opus-from-placement"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "上一步" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "上一步" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("执行 Worker"), "w-shared");
    await next(dialog);
    await next(dialog);
    expect(within(model()).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["auto", "sonnet"]);
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
    await userEvent.click(within(dialog).getByRole("radio", { name: /KiroCrew 推荐/ }));
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
