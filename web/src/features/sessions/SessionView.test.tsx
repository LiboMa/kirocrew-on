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

  test("an attempt's conclusion lists each acceptance item and the suggestions", async () => {
    const outcome = { decision: "pass", summary: "完成", acceptance: [
      { item: "登录成功", result: "pass", evidence: "npm test 0" }, { item: "错误提示清晰", result: "fail" },
      { item: "移动端", result: "n/a" }], suggestions: ["补 CHANGELOG"] };
    const task = blocked.tasks[0];
    open({ ...blocked, tasks: [{ ...task, attempts: [{ ...task.attempts![0], evidence: { ...task.attempts![0].evidence, outcome } }] },
      blocked.tasks[1]] } as Run);
    await userEvent.click(await screen.findByText("执行证据与历史 Attempt"));
    const checks = screen.getByRole("list", { name: "验收项" });
    expect(within(checks).getAllByRole("listitem").map((item) => item.textContent)).toEqual(
      ["✓ 登录成功 · npm test 0", "✗ 错误提示清晰", "— 移动端"]);
    expect(screen.getByRole("list", { name: "建议" })).toHaveTextContent("补 CHANGELOG");
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

  test("a Session of an archived Team cannot be continued, diagnosed or reopened, and says why", async () => {
    open({ ...blocked, team_id: "team-1", permissions: { operate: true, delete: true, native: true, resume: false } } as Run);
    expect(await screen.findByRole("button", { name: "续作" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /请求协调者诊断/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /停止/ })).toBeEnabled();
    expect(screen.getByText(/Team 已归档：会话可查看与停止/)).toBeInTheDocument();
  });

  test("deleting is offered only when the server allows it, with the reason otherwise", async () => {
    open({ ...blocked, permissions: { operate: true, delete: false } } as Run);
    await userEvent.click(await screen.findByRole("button", { name: "更多操作" }));
    const item = await screen.findByRole("menuitem", { name: /删除会话/ });
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveTextContent("只有 Team owner 可以删除他人创建的会话");
  });

  test("an administrator assigns a Worker from the space of the Session's owner, not their own", async () => {
    const waiting = makeRun({ id: "run-x", status: "waiting_worker", version: 1, owner_id: "u2" });
    open(waiting, {
      "GET /workers": [
        { id: "w-admin", name: "admin-own", owner_id: "u1", team_id: null, enabled: true, scope: "dedicated", capabilities: {} },
        { id: "w-dev", name: "dev-own", owner_id: "u2", team_id: null, enabled: true, scope: "dedicated", capabilities: {} },
      ],
      "POST /runs/run-x/assign-worker": { id: "run-x", version: 2 },
    });
    await userEvent.click(await screen.findByRole("button", { name: "分配 Worker" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("dev-own");
    expect(dialog).not.toHaveTextContent("admin-own");
  });

  test("an administrator watching someone else's Session is told why the native entry stays closed", async () => {
    const run = makeRun({ id: "run-o2", status: "running", worker_id: "w1", native_parent: "octopus-run-o2", version: 1,
      owner_id: "u2", permissions: { operate: true, delete: true, native: false } } as Partial<Run>);
    open(run, { "GET /workers": [{ id: "w1", name: "w1", owner_id: null, team_id: "t9", enabled: true, scope: "dedicated",
      capabilities: { crew_entry: true } }], "GET /runs/run-o2/observe": { native_parent: "octopus-run-o2", title: null,
      running: true, total: 0, has_more: false, next_before: null, context_pct: null, messages: [] } });
    expect(await screen.findByText("原生 KiroCrew 入口不因管理员身份开放；可在本页「KiroCrew 会话」查看。",
      { selector: "#crew-entry-hint" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /在 KiroCrew 中打开/ })).toBeDisabled();
  });

  test("the timeline names the administrator who acted on someone else's Session", async () => {
    const run = makeRun({ id: "run-t2", status: "stopped", version: 2 });
    const event = { id: 1, run_id: "run-t2", kind: "command.stop", created: 1_790_000_000,
      payload: { actor: "u1", version: 2, oversight: true, by: { id: "u1", username: "alice" } } };
    open(run, { "STREAM /runs/run-t2/events": () => new Response(`data: ${JSON.stringify(event)}\n\n`) });
    expect(await screen.findByText("由管理员 alice 停止")).toBeInTheDocument();
  });

  test("a Team Session the administrator is not in still shows the Team's name", async () => {
    open(makeRun({ id: "run-h", team_id: "team-x", name: "别人的 Team 会话" }), {
      "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [] },
        { id: "team-x", name: "Hardware-Dev", role: null, created: 2, members: [] }] });
    expect(await screen.findByText(/Hardware-Dev \/ /)).toBeInTheDocument();
  });

  const revisions = [
    { version: 1, kind: "created", summary: "创建：2 个任务", created: 1_790_000_000, actor: { id: "u1", username: "fixture-owner" }, reconstructed: false },
    { version: 2, kind: "planned", summary: "规划：追加 1 个任务", created: 1_790_000_100, actor: { id: "u1", username: "fixture-owner" }, reconstructed: false },
    { version: 3, kind: "continued", summary: "续作「实现」：工具 claude → codex、提示词", created: 1_790_000_200,
      actor: { id: "u1", username: "fixture-owner" }, reconstructed: false },
  ];
  const same = (text: string) => ({ op: " ", text });
  const diffs: Record<string, unknown> = {
    "?from=2&to=3": { from: 2, to: 3, from_summary: "", to_summary: "", reconstructed: false, input: [same("做一个数字时钟")],
      tasks: [{ id: "plan", name: "规划", status: "same", fields: {}, prompt: [same("规划")] },
        { id: "code", name: "实现", status: "changed", fields: { tool: ["claude", "codex"] },
          prompt: [same("实现数字时钟"), { op: "+", text: "12 小时制补零" }, same("不引入外部资源")] },
        { id: "review", name: "审查", status: "same", fields: {}, prompt: [same("审查")] }] },
    "?from=1&to=3": { from: 1, to: 3, from_summary: "", to_summary: "", reconstructed: false, input: [same("做一个数字时钟")],
      tasks: [{ id: "review", name: "审查", status: "added", fields: {}, prompt: [{ op: "+", text: "审查" }] }] },
  };

  test("revision history lists every change and shows what changed between any two revisions", async () => {
    const searches: string[] = [];
    open(makeRun({ id: "run-r", version: 3 }), { "GET /runs/run-r/revisions": revisions,
      "GET /runs/run-r/revisions/diff": (_b: unknown, url: URL) => { searches.push(url.search); return diffs[url.search]; } });
    const panel = await screen.findByRole("region", { name: "修订历史" });
    expect(await within(panel).findByText("续作「实现」：工具 claude → codex、提示词")).toBeInTheDocument();
    expect(await within(panel).findByText("工具 claude → codex")).toBeInTheDocument();
    const added = within(panel).getByText("12 小时制补零").closest("[data-op]");
    expect(added).toHaveAttribute("data-op", "+");
    expect(panel).toHaveTextContent("另有 2 个任务未变化");
    await userEvent.selectOptions(within(panel).getByLabelText("对比起点"), "1");
    expect(await within(panel).findByText("新增")).toBeInTheDocument();
    expect(searches).toEqual(["?from=2&to=3", "?from=1&to=3"]);
    await userEvent.click(within(panel).getByRole("button", { name: /v2 · 规划/ }));
    await waitFor(() => expect(searches.at(-1)).toBe("?from=1&to=2"));
  });

  test("long prompts fold unchanged lines around each change, and the kind is not repeated", async () => {
    const body = Array.from({ length: 20 }, (_, i) => same(`第 ${i + 1} 行`));
    body.splice(10, 0, { op: "+", text: "新增的要求" });
    open(makeRun({ id: "run-l", version: 2 }), {
      "GET /runs/run-l/revisions": [revisions[0], { ...revisions[2], version: 2, summary: "续作「实现」：提示词" }],
      "GET /runs/run-l/revisions/diff": { from: 1, to: 2, from_summary: "", to_summary: "", reconstructed: false,
        input: [same("需求")], tasks: [{ id: "code", name: "实现", status: "changed", fields: {}, prompt: body }] } });
    const panel = await screen.findByRole("region", { name: "修订历史" });
    expect(await within(panel).findByText("新增的要求")).toBeInTheDocument();
    expect(within(panel).getByText("第 9 行")).toBeInTheDocument();          // two lines of context
    expect(within(panel).queryByText("第 3 行")).toBeNull();
    expect(panel).toHaveTextContent("… 8 行未变化 …");
    expect(within(panel).getByRole("button", { name: /v2 · 续作「实现」/ })).not.toHaveTextContent("续作 · 续作");
  });

  test("the change log lists who changed what, filtered by people or the system", async () => {
    const entries = [
      { id: 9, at: 1_790_000_200, kind: "command.continue", actor: { kind: "user", id: "u1", username: "alice", client: "mcp" },
        changes: { status: ["stopped", "ready"], input: [{ chars: 25, sha256: "a" }, { chars: 31, sha256: "b" }] },
        reason: null, detail: { version: 3, task_id: "code" } },
      { id: 8, at: 1_790_000_100, kind: "run.state", actor: { kind: "engine" }, changes: { status: ["ready", "waiting_worker"] },
        reason: "派发前核对：执行环境已更换", detail: { status: "waiting_worker" } },
      { id: 7, at: 1_790_000_050, kind: "task.reset", actor: { kind: "recovery" },
        changes: [{ id: "code", changes: { status: ["blocked", "pending"] } }], reason: null, detail: {} },
    ];
    const searches: string[] = [];
    open(makeRun({ id: "run-c" }), { "GET /runs/run-c/changes": (_b: unknown, url: URL) => {
      searches.push(url.search);
      return url.searchParams.get("who") === "user" ? [entries[0]] : entries;
    } });
    const panel = await screen.findByRole("region", { name: "变更记录" });
    expect(searches).toEqual([]);   // loaded only when opened
    await userEvent.click(within(panel).getByRole("button", { name: "显示变更记录" }));
    const user = (await within(panel).findByText("已提交原地续作")).closest("[data-change]") as HTMLElement;
    expect(user).toHaveTextContent("alice · Coding Agent");
    expect(user).toHaveTextContent("状态：stopped → ready");
    expect(user).toHaveTextContent("需求：25 字符 → 31 字符");
    const engine = within(panel).getByText("派发前核对：执行环境已更换").closest("[data-change]") as HTMLElement;
    expect(engine).toHaveTextContent("Engine");
    expect(within(panel).getByText(/code：状态 blocked → pending/)).toBeInTheDocument();
    expect(within(panel).getByText("协调者")).toBeInTheDocument();
    await userEvent.selectOptions(within(panel).getByLabelText("变更来源"), "user");
    await waitFor(() => expect(searches.at(-1)).toBe("?who=user&limit=200"));
    expect(searches[0]).toBe("?limit=200");
  });

  test("a Session with only its first revision says nothing has changed yet", async () => {
    open(makeRun({ id: "run-r1", version: 1 }), { "GET /runs/run-r1/revisions": [revisions[0]] });
    const panel = await screen.findByRole("region", { name: "修订历史" });
    expect(await within(panel).findByText("尚无后续修订：续作、KiroCrew 规划与自动纠错都会在这里留下可对比的版本。")).toBeInTheDocument();
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

  test("a Session on a shared execution environment says so", async () => {
    const run = makeRun({ id: "run-s", status: "running", worker_id: "w-shared", version: 1 });
    open(run, { "GET /workers": [{ id: "w-shared", name: "personal", owner_id: "u1", team_id: null, enabled: true,
      scope: "shared", spaces: [{ user_id: "u1" }], capabilities: {} }] });
    expect(await screen.findByText("共享执行环境：环境内会话的 Agent 互相可读，记忆未分隔。")).toBeInTheDocument();
  });

  test("the native KiroCrew conversation is shown on the session page, older messages on request", async () => {
    const run = makeRun({ id: "run-o", status: "running", worker_id: "w1", native_parent: "octopus-run-o", version: 1 });
    const pages: string[] = [];
    open(run, { "GET /runs/run-o/observe": (_body: unknown, url: URL) => {
      pages.push(url.search);
      return url.searchParams.get("before") === "40"
        ? { native_parent: "octopus-run-o", title: "番茄钟", running: true, total: 42, has_more: false, next_before: null,
            context_pct: 12.5, messages: [{ role: "user", content: "最早的需求", truncated: false, ts: "2026-10-12T03:00:00Z" }] }
        : { native_parent: "octopus-run-o", title: "番茄钟", running: true, total: 42, has_more: true, next_before: 40,
            context_pct: 12.5, messages: [{ role: "assistant", content: "子 Agent 已完成实现", truncated: false, ts: "2026-10-12T03:05:00Z" },
              { role: "assistant", content: "长内容", truncated: true, ts: null },
              { role: "subagent", content: "子任务回报", truncated: false, ts: null },
              { role: "inject", content: "平台派发说明", truncated: false, ts: null },
              { role: "tool", content: "🔧 read", truncated: false, ts: null },
              { role: "error", content: "工具批准超时", truncated: false, ts: null }] };
    } });
    const panel = await screen.findByRole("region", { name: "KiroCrew 会话" });
    expect(await within(panel).findByText("子 Agent 已完成实现")).toBeInTheDocument();
    expect(panel).toHaveTextContent("运行中");
    expect(panel).toHaveTextContent("已截断");
    // Roles as KiroCrew names them (observed on EC2): the coordinator, its sub-agents and platform-injected notes.
    expect(within(panel).getByText("子任务回报").closest("article")).toHaveTextContent("子 Agent");
    expect(within(panel).getByText("平台派发说明").closest("article")).toHaveTextContent("平台消息");
    expect(within(panel).getByText("🔧 read").closest("article")).toHaveTextContent("工具");
    expect(within(panel).getByText("工具批准超时").closest("article")).toHaveTextContent("错误");
    expect(within(panel).getByText("子 Agent 已完成实现").closest("article")).toHaveTextContent("KiroCrew 协调者");
    await userEvent.click(within(panel).getByRole("button", { name: "加载更早的消息" }));
    expect(await within(panel).findByText("最早的需求")).toBeInTheDocument();
    expect(pages).toContain("?limit=50&before=40");
    expect(within(panel).queryByRole("button", { name: "加载更早的消息" })).toBeNull();
  });

  test("before the native Session exists the panel says so instead of asking the Worker", async () => {
    const calls = open(makeRun({ id: "run-p", status: "preparing", worker_id: "w1", native_parent: null, version: 1 }));
    const panel = await screen.findByRole("region", { name: "KiroCrew 会话" });
    expect(panel).toHaveTextContent("原生会话尚未建立");
    expect(calls.some((c) => c.path.endsWith("/observe"))).toBe(false);
  });

  test("on someone else's shared environment the native entry is unavailable", async () => {
    const run = makeRun({ id: "run-n", status: "running", worker_id: "w-shared", native_parent: "octopus-run-n", version: 1 });
    open(run, { "GET /workers": [{ id: "w-shared", name: "personal", owner_id: "admin-1", team_id: null, enabled: true,
      scope: "shared", spaces: [{ user_id: "u1" }], capabilities: { crew_entry: true } }] });
    expect(await screen.findByText("共享执行环境不开放原生 KiroCrew 入口；可在本页「KiroCrew 会话」查看。",
      { selector: "#crew-entry-hint" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /在 KiroCrew 中打开/ })).toBeDisabled();
  });

  test("a Team Session without any Worker explains why instead of offering an empty assignment", async () => {
    const waiting = makeRun({ id: "run-t", status: "waiting_worker", version: 1, team_id: "t1", attention: {
      title: "Team「Web 组」没有可用 Worker", detail: "该 Team 尚未分配执行环境。", workers: 0,
      action: "请平台管理员在控制台为该 Team 分配执行环境后分配 Worker；也可以改在个人空间重新提交。" } });
    open(waiting);
    const notice = await screen.findByText("Team「Web 组」没有可用 Worker", { selector: "#worker-notice *" });
    expect(notice.closest("#worker-notice")).toHaveTextContent("也可以改在个人空间重新提交");
    expect(screen.queryByRole("button", { name: "分配 Worker" })).not.toBeInTheDocument();
    expect(document.querySelector("#run-attention")).toBeNull();
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
