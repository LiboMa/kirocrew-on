import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { App } from "@/App";
import { setToken } from "@/api/client";
import type { Run } from "@/api/types";
import { makeRun, mockApi, workspaceRoutes } from "@/test/api";

const spec = (id: string, name: string, tool: "claude" | "codex") => ({ id, name, tool, model: "auto", effort: "", prompt: name + "的说明" });

const blocked: Run = makeRun({
  id: "run-a", status: "blocked", worker_id: "w1", native_parent: "octopus-run-a", version: 4,
  attention: { title: "任务未通过交接或质量检查", detail: "模拟交接缺失", task_name: "实现与测试", action: "请求协调者诊断" },
  recovery: { policy: { enabled: true, max_cycles: 2 }, used: 1, latest: { number: 1, status: "blocked", failed_task_id: "code" },
    history: [{ number: 1, status: "blocked", failed_task_id: "code", native_id: "diag-1", decision: { reason: "缺少测试命令" } }] },
  tasks: [
    { id: "code", run_id: "run-a", ordinal: 0, status: "blocked", current_attempt: "att-1", spec: spec("code", "实现与测试", "claude"),
      attempts: [{ id: "att-1", number: 1, status: "blocked", spec: spec("code", "实现与测试", "claude"), native_id: "native-77",
        context_hash: "abc", evidence: { actual_backend: "claude", actual_model: "global.anthropic.claude-sonnet-4-6" },
        result: "交接说明", error: "模拟交接缺失", created: 1, updated: 2 }] },
    { id: "review", run_id: "run-a", ordinal: 1, status: "pending", current_attempt: null, spec: spec("review", "独立审查", "codex"), attempts: [] },
  ],
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function open(run: Run, extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  window.history.replaceState(null, "", "/?run=" + run.id);
  const calls = mockApi(workspaceRoutes([run], extra));
  render(<App />);
  return calls;
}

describe("session detail", () => {
  test("shows why the Session stopped, the evidence of each attempt and the recovery record", async () => {
    open(blocked);
    expect(await screen.findByText("模拟交接缺失", { selector: "#attention-detail" })).toBeInTheDocument();
    expect(screen.getAllByText("任务未通过交接或质量检查").length).toBeGreaterThan(0);
    const tasks = screen.getByRole("list", { name: "开发任务" });
    expect(within(tasks).getAllByRole("listitem")).toHaveLength(2);
    const [code, review] = within(tasks).getAllByRole("button", { name: "配置并续作" });
    expect(code).toBeEnabled();
    expect(review).toBeDisabled();
    await userEvent.click(screen.getByText("执行证据与历史 Attempt"));
    expect(screen.getByText("global.anthropic.claude-sonnet-4-6")).toBeInTheDocument();
    expect(screen.getByText("native-77")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /请求协调者诊断/ })).toBeEnabled();
    await userEvent.click(screen.getByText(/纠错记录/));
    expect(screen.getByText("缺少测试命令")).toBeInTheDocument();
  });

  test("continue keeps the Session and submits the edited execution with the expected version", async () => {
    const calls = open(blocked, { "POST /runs/run-a/continue": { id: "run-a", version: 5 } });
    await userEvent.click(await screen.findByRole("button", { name: "续作" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.clear(within(dialog).getByLabelText("任务说明"));
    await userEvent.type(within(dialog).getByLabelText("任务说明"), "补充回归测试");
    await userEvent.selectOptions(within(dialog).getByLabelText("Coding 工具"), "codex");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存并续作" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const body = calls.find((c) => c.path === "/v1/runs/run-a/continue")?.body as Record<string, unknown>;
    expect(body).toMatchObject({ expected_version: 4, task_id: "code",
      execution: { name: "实现与测试", prompt: "补充回归测试", tool: "codex", model: "auto" } });
    expect(body.request_id).toEqual(expect.any(String));
  });

  test("delete is only sent after the full Session ID is typed", async () => {
    const calls = open(blocked, { "POST /runs/run-a/delete": { id: "run-a", version: 5 } });
    await userEvent.click(await screen.findByRole("button", { name: "更多操作" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /删除会话/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("输入完整 Session ID 确认"), "run-x");
    await userEvent.click(within(dialog).getByRole("button", { name: "确认删除会话" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("完整 Session ID");
    expect(calls.some((c) => c.path === "/v1/runs/run-a/delete")).toBe(false);
    await userEvent.clear(within(dialog).getByLabelText("输入完整 Session ID 确认"));
    await userEvent.type(within(dialog).getByLabelText("输入完整 Session ID 确认"), "run-a");
    await userEvent.click(within(dialog).getByRole("button", { name: "确认删除会话" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/runs/run-a/delete")?.body).toMatchObject({ confirmation: "run-a", expected_version: 4 }));
  });

  test("a Session waiting for a Worker can be assigned one from its space", async () => {
    const waiting = makeRun({ id: "run-w", status: "waiting_worker", version: 1 });
    const calls = open(waiting, {
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, capabilities: { isolation: "trusted-local" } }],
      "POST /runs/run-w/assign-worker": { id: "run-w", version: 2 },
    });
    await userEvent.click(await screen.findByRole("button", { name: "分配 Worker" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "开始执行" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/runs/run-w/assign-worker")?.body).toMatchObject({ worker_id: "w1", expected_version: 1 }));
  });
});

describe("session dialogs act on the Session as it was when they opened", () => {
  test("a concurrent change makes the server reject instead of retargeting the continue", async () => {
    setToken("oct_fixture");
    window.history.replaceState(null, "", "/?run=run-a");
    let current: Run = blocked;
    const routes = workspaceRoutes([blocked], { "POST /runs/run-a/continue": { id: "run-a", version: 5 } });
    const calls = mockApi({ ...routes, "GET /runs/run-a": () => current });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<App client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "续作" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("任务名称")).toHaveValue("实现与测试");
    // A teammate's continue finishes meanwhile: task 1 is accepted, task 2 is now the first unfinished one.
    current = { ...blocked, version: 9, tasks: [{ ...blocked.tasks[0], status: "accepted" }, { ...blocked.tasks[1], status: "blocked" }] };
    await client.invalidateQueries({ queryKey: ["run", "run-a"] });
    await waitFor(() => expect((client.getQueryData(["run", "run-a"]) as Run).version).toBe(9));
    await userEvent.click(within(dialog).getByRole("button", { name: "保存并续作" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/runs/run-a/continue")).toBe(true));
    const body = calls.find((c) => c.path === "/v1/runs/run-a/continue")?.body as Record<string, unknown>;
    expect(body).toMatchObject({ expected_version: 4, task_id: "code", execution: { name: "实现与测试" } });
  });
});
