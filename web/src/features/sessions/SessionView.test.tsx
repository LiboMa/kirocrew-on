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
  test("every HTML page the Session produced can be previewed (EC2 2026-10-22: reports in report/ and artifacts/)", async () => {
    const run = makeRun({ id: "run-h", status: "completed", worker_id: "w1" });
    const pages = [{ path: "artifacts/report/index.html", url: "/preview/t.k.e.n/artifacts/report/index.html" },
      { path: "report/summary.html", url: "/preview/t.k.e.n/report/summary.html" }];
    open(run, { "POST /runs/run-h/preview": { url: pages[0].url, entry: pages[0].path, expires_in: 600, pages } });
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    await userEvent.click(await screen.findByRole("button", { name: "选择预览页面" }));
    expect(await screen.findByRole("menuitem", { name: /artifacts\/report\/index\.html/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /report\/summary\.html/ }));
    expect(opened).toHaveBeenCalledWith(pages[1].url, "_blank", "noopener");
    opened.mockRestore();
  });

  test("the local preview command is for administrators only (D53)", async () => {
    const run = makeRun({ id: "run-l", status: "completed" });
    open(run, { "GET /me": { id: "u2", username: "dev1", admin: 0, scopes: ["read", "execute", "manage"] } });
    await screen.findByText("Workspace 与原生会话");
    expect(screen.queryByText(/deploy\.sh/)).toBeNull();
    window.history.replaceState(null, "", "/");
    document.body.innerHTML = "";
    open(run);
    expect(await screen.findByText("deploy/aws/deploy.sh space run-l")).toBeInTheDocument();
  });

  test("says which request it came from, and nothing about admission (D55; D70)", async () => {
    open(makeRun({ id: "run-r", status: "running", request: { id: "request-0123456789abcdef", client: "codex" },
      admission_basis: { mode: "auto", basis: "rule", pattern: "github.com/o/*", scope: "personal", policy_id: "policy-1" } }));
    expect(await screen.findByText(/来自请求 request-012345 · Codex/)).toBeInTheDocument();
    expect(screen.queryByText(/准入|自动开始|等待确认/)).not.toBeInTheDocument();
  });

  test("a synced Attempt says whose local commits it carries, and the CLI is named (D58)", async () => {
    const synced = makeRun({ id: "run-s", status: "completed", request: { id: "request-cli-000001", client: "cli" },
      tasks: [{ id: "code", run_id: "run-s", ordinal: 0, status: "accepted", current_attempt: "att-2", spec: spec("code", "实现", "claude"),
        attempts: [{ id: "att-2", number: 2, status: "accepted", spec: spec("code", "实现", "claude"), native_id: null, context_hash: null,
          evidence: { synced: { commits: 2, note: "改了样式", by: { id: "u1", username: "fixture-owner" }, client: "cli", head: "b".repeat(40),
            from: "a".repeat(40) } }, result: null, error: null, created: 1, updated: 2 }] }] });
    open(synced);
    expect(await screen.findByText(/来自请求 request-cli-00 · CLI/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /执行证据与历史 Attempt/ }));
    expect(screen.getByText(/2 个本地提交 · fixture-owner · 改了样式/)).toBeInTheDocument();
  });

  test("shows what the Session wrote to the environment's knowledge (D46 P4)", async () => {
    open(makeRun({ id: "run-k", status: "completed", knowledge: { note: null, items: [{ id: "knowledge-1", task_id: "knowledge",
      rule: "依赖用 pnpm 安装", avoid: null, result: "written", reason: null, created: 1, removed: null, removed_by: null }] } }));
    const panel = await screen.findByRole("region", { name: "本次沉淀的知识" });
    expect(within(panel).getByText("依赖用 pnpm 安装")).toBeInTheDocument();
  });

  test("a finished Knowledge task with nothing to keep says so (D46 P4)", async () => {
    const knowledgeTask = { id: "knowledge", run_id: "run-k0", ordinal: 1, status: "accepted", current_attempt: null,
      spec: { ...spec("knowledge", "沉淀项目知识", "claude"), kind: "design", role: "knowledge" }, attempts: [] };
    open(makeRun({ id: "run-k0", status: "completed", tasks: [knowledgeTask], knowledge: { note: null, items: [] } } as Partial<Run>));
    const panel = await screen.findByRole("region", { name: "本次沉淀的知识" });
    expect(within(panel).getByText("Knowledge 任务已完成：本次没有可沉淀的项目知识。")).toBeInTheDocument();
  });

  test("names the methods a task works by (D50)", async () => {
    const skilled = { ...spec("code", "实现界面", "claude"), skills: ["frontend-design", "test-driven-development"] };
    open(makeRun({ id: "run-m", status: "running", tasks: [
      { id: "code", run_id: "run-m", ordinal: 0, status: "running", current_attempt: null, spec: skilled, attempts: [] },
      { id: "review", run_id: "run-m", ordinal: 1, status: "pending", current_attempt: null, spec: spec("review", "独立审查", "codex"),
        attempts: [] }] }));
    await screen.findByText("实现界面");
    expect(document.querySelector('[data-task="code"]')).toHaveTextContent("方法：frontend-design、test-driven-development");
    expect(document.querySelector('[data-task="review"]')).not.toHaveTextContent("方法：");
  });

  test("the methods shown are those the Worker delivered, once an attempt ran (D50)", async () => {
    const skilled = { ...spec("code", "实现界面", "claude"), skills: ["frontend-design", "test-driven-development"] };
    const attempt = (id: string, evidence: Record<string, unknown>) => ({ id, number: 1, status: "running", spec: skilled,
      native_id: "n-" + id, context_hash: "h", evidence, result: null, error: null, created: 1, updated: 2 });
    open(makeRun({ id: "run-d", status: "running", tasks: [
      { id: "code", run_id: "run-d", ordinal: 0, status: "running", current_attempt: "a1", spec: skilled,
        attempts: [attempt("a1", { skills: [{ name: "frontend-design", sha256: "x" }] })] },
      { id: "old", run_id: "run-d", ordinal: 1, status: "blocked", current_attempt: "a2", spec: { ...skilled, id: "old", name: "旧 Worker" },
        attempts: [{ ...attempt("a2", {}), status: "blocked" }] },
      { id: "new", run_id: "run-d", ordinal: 2, status: "running", current_attempt: "a3", spec: { ...skilled, id: "new", name: "刚派发" },
        attempts: [attempt("a3", {})] }] } as Partial<Run>));
    await screen.findByText("实现界面");
    expect(document.querySelector('[data-task="code"]')).toHaveTextContent("方法：frontend-design");
    expect(document.querySelector('[data-task="code"]')).not.toHaveTextContent("test-driven-development");
    expect(document.querySelector('[data-task="old"]')).toHaveTextContent("方法（计划，执行环境未交付）：frontend-design、test-driven-development");
    expect(document.querySelector('[data-task="new"]')).toHaveTextContent("方法：frontend-design、test-driven-development");   // evidence to come
  });

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

  test("the report opens in a sandbox without scripts and downloads as Markdown", async () => {
    const formats: string[] = [];
    const create = vi.fn(() => "blob:report");
    Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
    const revoke = vi.fn();
    Object.defineProperty(URL, "revokeObjectURL", { value: revoke, configurable: true });
    open(blocked, { "GET /runs/run-a/report": (_body: unknown, url: URL) => {
      const format = url.searchParams.get("format") ?? "";
      formats.push(format);
      return new Response(format === "html" ? "<h1>修复登录失败</h1>" : "# 修复登录失败\n", { status: 200 });
    } });
    await userEvent.click(await screen.findByRole("button", { name: /报告/ }));
    const frame = await screen.findByTitle("会话报告");
    expect(frame).toHaveAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
    await waitFor(() => expect(frame.getAttribute("srcdoc")).toContain("<h1>修复登录失败</h1>"));
    await userEvent.click(screen.getByRole("button", { name: "下载 Markdown" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(revoke).not.toHaveBeenCalled();   // Safari starts the download after click(): the URL must outlive it
    expect(formats).toEqual(["html", "md"]);
  });

  test("an attempt lists its gates and the candidate it left (D37)", async () => {
    const evidence = { ...blocked.tasks[0].attempts![0].evidence,
      candidate: { sha: "abc1234def5678abc1234def5678abc1234def56", parent: "b".repeat(40), changed: true, files: 2, insertions: 12, deletions: 3 },
      gates: [{ name: "native_completed", status: "pass" }, { name: "diff_present", status: "pass", detail: "2 个文件" },
        { name: "checks_pass", status: "fail", detail: "检查命令失败：npm test（退出码 1）" }, { name: "review_bound", status: "skip", detail: "此前没有候选" }] };
    const task = blocked.tasks[0];
    open({ ...blocked, tasks: [{ ...task, attempts: [{ ...task.attempts![0], evidence }] }, blocked.tasks[1]] } as Run);
    await userEvent.click(await screen.findByText("执行证据与历史 Attempt"));
    const gates = screen.getByRole("list", { name: "门禁" });
    expect(within(gates).getAllByRole("listitem").map((item) => item.textContent)).toEqual(
      ["✓ native_completed", "✓ diff_present · 2 个文件", "✗ checks_pass · 检查命令失败：npm test（退出码 1）", "– review_bound · 此前没有候选"]);
    expect(screen.getByText("abc1234 · 2 个文件 +12 −3")).toBeInTheDocument();
  });

  test("a warned gate is marked ⚠ and a completed Session counts its latest attempts' warnings (D57)", async () => {
    const task = blocked.tasks[0];
    const warned = { ...task.attempts![0], id: "att-2", number: 2, status: "accepted", error: null,
      evidence: { ...task.attempts![0].evidence, gates: [{ name: "native_completed", status: "pass" },
        { name: "diff_present", status: "warn", detail: "本任务没有改动入库文件" }] } };
    const earlier = { ...task.attempts![0], evidence: { gates: [{ name: "outcome_valid", status: "warn" }] } };   // not counted
    open(makeRun({ id: "run-a", status: "completed", progress: { accepted: 1, total: 1 },
      tasks: [{ ...task, status: "accepted", current_attempt: "att-2", attempts: [earlier, warned] }] } as Partial<Run>));
    const header = (await screen.findByRole("heading", { level: 1 })).closest("header")!;
    expect(within(header).getByText("完成 · 1 项提示")).toBeInTheDocument();   // beside the title
    await userEvent.click(screen.getByText("执行证据与历史 Attempt"));
    const [latest] = screen.getAllByRole("list", { name: "门禁" });
    expect(within(latest).getAllByRole("listitem").map((item) => item.textContent)).toEqual(
      ["✓ native_completed", "⚠ diff_present · 本任务没有改动入库文件"]);
    expect(within(latest).getAllByRole("listitem")[1]).toHaveClass("text-amber-600");
  });

  test("a completed Session without warnings says only that it completed (D57)", async () => {
    open(makeRun({ id: "run-a", status: "completed", progress: { accepted: 1, total: 1 } }));
    const header = (await screen.findByRole("heading", { level: 1 })).closest("header")!;
    expect(within(header).getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByText(/项提示/)).toBeNull();
  });

  test("an attempt the Coordinator switched says from which tool and why (D38)", async () => {
    const task = blocked.tasks[0];
    const evidence = { ...task.attempts![0].evidence,
      switched_from: { tool: "opencode", model: "auto", effort: "", reason: "opencode 不可用", by: "coordinator", recovery: 1 } };
    open({ ...blocked, tasks: [{ ...task, attempts: [{ ...task.attempts![0], evidence }] }, blocked.tasks[1]] } as Run);
    await userEvent.click(await screen.findByText("执行证据与历史 Attempt"));
    expect(screen.getByText("由协调者从 opencode / auto 换为 claude / auto：opencode 不可用")).toBeInTheDocument();
  });

  test("what the Session and each attempt used (D46 P6a)", async () => {
    const task = blocked.tasks[0];
    const attempt = { ...task.attempts![0], evidence: { ...task.attempts![0].evidence,
      usage: { input_tokens: 120000, output_tokens: 8000, cost_usd: 0.84, seconds: 200 } } };
    open({ ...blocked, usage: { credits: 12.4, input_tokens: 120000, output_tokens: 8000, cost_usd: 0.84, seconds: 325.5,
      attempts: 2, tokens_unavailable: 1 }, tasks: [{ ...task, attempts: [attempt] }, blocked.tasks[1]] } as Run);
    expect(await screen.findByText("本会话用量：12.4 credits · 输入 120k / 输出 8k token · $0.84 · 5 分 26 秒")).toBeInTheDocument();
    await userEvent.click(screen.getByText("执行证据与历史 Attempt"));
    expect(screen.getByText("输入 120k / 输出 8k token · $0.84 · 3 分 20 秒")).toBeInTheDocument();
  });

  test("a rework says whether it continued its conversation (D46 P5a)", async () => {
    const task = blocked.tasks[0];
    const attempt = task.attempts![0];
    const continued = { ...attempt, evidence: { ...attempt.evidence, continued: { conversation: "aaaa1111", from: "att-0" } } };
    const refused = { ...attempt, id: "att-2", number: 2, evidence: { ...attempt.evidence, continue_refused: "conversation_gone" } };
    open({ ...blocked, tasks: [{ ...task, attempts: [continued, refused] }, blocked.tasks[1]] } as Run);
    await userEvent.click(await screen.findByText("执行证据与历史 Attempt"));
    expect(screen.getByText("续接上一轮的对话（保留上下文）")).toBeInTheDocument();
    expect(screen.getByText("续接被拒，已重新派发：conversation_gone")).toBeInTheDocument();
  });

  test("a recovery round that switched tools says so, and a refused switch shows why (D38)", async () => {
    const switched = { action: "switch_tool", target_task_id: "code", tool: "codex", model: "auto", reason: "claude 不可用",
      instructions: "以 codex 重新执行", switched_from: { tool: "claude", model: "auto", effort: "" } };
    open({ ...blocked, status: "running", recovery: { ...blocked.recovery!, used: 2,
      latest: { number: 2, status: "applied", failed_task_id: "code", decision: switched },
      history: [{ number: 1, status: "blocked", failed_task_id: "code", decision: { ...switched, tool: "opencode" },
                  error: "协调者换工具的推荐未通过核对：opencode 在此执行环境不可用" },
                { number: 2, status: "applied", failed_task_id: "code", decision: switched }] } } as Run);
    expect(await screen.findByText("第 2 轮 · 已换用 codex / auto 重跑")).toBeInTheDocument();
    await userEvent.click(screen.getByText("第 2 轮 · 已换用 codex / auto 重跑"));
    expect(screen.getByText("协调者换工具的推荐未通过核对：opencode 在此执行环境不可用")).toBeInTheDocument();
    expect(screen.getAllByText(/换工具 Task：code（claude \/ auto → codex \/ auto）/)).toHaveLength(1);
  });

  describe("pending approvals (D38)", () => {
    const item = { origin: "native", request_id: "7", request_mid: "mid-7", tool: "shell",
      tool_input: "npm test -- --watch=false " + "x".repeat(300), tool_purpose: "运行测试" };

    test("a pending approval shows what it would run and is approved after confirming", async () => {
      const run = { ...blocked, status: "waiting_approval" } as Run;
      const calls = open(run, { [`GET /runs/${run.id}/approvals`]: { approvals: [item], supported: true },
        [`POST /runs/${run.id}/approvals/answer`]: { ok: true } });
      const card = await screen.findByRole("region", { name: "待批准" });
      expect(within(card).getByText("shell")).toBeInTheDocument();
      expect(within(card).getByText("运行测试")).toBeInTheDocument();
      await userEvent.click(within(card).getByRole("button", { name: "批准" }));
      expect(within(card).getByText(item.tool_input)).toBeInTheDocument();   // the full input before confirming
      await userEvent.click(within(card).getByRole("button", { name: "确认批准" }));
      await waitFor(() => expect(calls.find((c) => c.path.endsWith("/approvals/answer"))?.body).toEqual(
        { origin: "native", request_id: "7", request_mid: "mid-7", decision: "approve" }));
    });

    test("nothing pending shows no card, and an old Worker says it cannot list them", async () => {
      open({ ...blocked, status: "running" } as Run);
      await screen.findByText("实现与测试");
      expect(screen.queryByRole("region", { name: "待批准" })).not.toBeInTheDocument();
    });

    test("an old Worker waiting for approval says where to answer it", async () => {
      const run = { ...blocked, status: "waiting_approval" } as Run;
      open(run, { [`GET /runs/${run.id}/approvals`]: { approvals: [], supported: false } });
      expect(await screen.findByText("此执行环境的 Worker 尚未升级，请在原生 KiroCrew 中处理批准。")).toBeInTheDocument();
    });

    test("someone who may not answer sees the request without the buttons", async () => {
      const run = { ...blocked, status: "waiting_approval", permissions: { operate: true, delete: false, publish: false } } as Run;
      open(run, { [`GET /runs/${run.id}/approvals`]: { approvals: [item], supported: true } });
      const card = await screen.findByRole("region", { name: "待批准" });
      expect(within(card).getByText("只有会话创建者、Team owner 或管理员可以答复。")).toBeInTheDocument();
      expect(within(card).queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
    });
  });

  test("an attempt the watchdog stopped says why (D39)", async () => {
    const task = blocked.tasks[0];
    const evidence = { ...task.attempts![0].evidence, watchdog: { reason: "原生任务 31 分钟没有进展，看门狗已停止" } };
    open({ ...blocked, tasks: [{ ...task, attempts: [{ ...task.attempts![0], evidence }] }, blocked.tasks[1]] } as Run);
    await userEvent.click(await screen.findByText("执行证据与历史 Attempt"));
    expect(screen.getByText("原生任务 31 分钟没有进展，看门狗已停止")).toBeInTheDocument();
  });

  test("each task says where its tool came from", async () => {
    const task = blocked.tasks[0];
    open({ ...blocked, tasks: [{ ...task, mapping: { source: "planner", label: "KiroCrew 规划" } }, blocked.tasks[1]] } as Run);
    expect(await screen.findByText("来源：KiroCrew 规划")).toBeInTheDocument();
  });

  test("a recovery round that replanned says so (D41)", async () => {
    const replan = { action: "replan", reason: "计划缺少测试步骤", instructions: "加入测试任务" };
    open({ ...blocked, status: "running", recovery: { ...blocked.recovery!, used: 1,
      latest: { number: 1, status: "applied", failed_task_id: "review", decision: replan },
      history: [{ number: 1, status: "applied", failed_task_id: "review", decision: replan }] } } as Run);
    expect(await screen.findByText("第 1 轮 · 已安排重新规划")).toBeInTheDocument();
    await userEvent.click(screen.getByText("第 1 轮 · 已安排重新规划"));
    expect(screen.getByText(/失败 Task：review → 重新规划其后的工作/)).toBeInTheDocument();
  });

  test("the recovery record shows the budget and what ran out (D42)", async () => {
    const budget = { since: 1, reset: true, cycles: { used: 2, limit: 2 }, retries: { used: 0, limit: 4 },
      minutes: { used: 35, limit: 720 }, exhausted: "cycles" as const };
    open({ ...blocked, recovery: { ...blocked.recovery!, budget } } as Run);
    expect(await screen.findByText("预算（自上次续作 / 请求诊断起）：纠错 2/2 轮（已用完） · 自动重试 0/4 次 · 35/720 分钟"))
      .toBeInTheDocument();
  });

  test("a Session that recovered nothing and is not held by the budget shows no budget (D42)", async () => {
    const budget = { since: 1, reset: false, cycles: { used: 0, limit: 2 }, retries: { used: 0, limit: 4 },
      minutes: { used: 780, limit: 720 }, exhausted: "minutes" as const };
    open({ ...blocked, status: "completed", attention: null,
      recovery: { ...blocked.recovery!, used: 0, latest: null, history: [], budget } } as Run);
    expect(await screen.findByText(/自动纠错已开启/)).toBeInTheDocument();   // the detail has rendered
    expect(screen.queryByText(/预算（/)).not.toBeInTheDocument();
  });

  test("no plan is offered for confirmation (D70)", async () => {   // an earlier version's wait, until the Engine starts it
    open({ ...blocked, status: "awaiting_confirmation", error: null,
      attention: { title: "即将开始", detail: "计划确认已取消，平台正在直接开始。", action: "无需操作。" } } as Run);
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认并开始/ })).not.toBeInTheDocument();
  });

  test("a task says its phase when it is not plain converge work (D69)", async () => {
    const research = { ...blocked.tasks[0], spec: { ...blocked.tasks[0].spec, kind: "research" as const } };
    const design = { ...blocked.tasks[1], spec: { ...blocked.tasks[1].spec, kind: "implement" as const, phase: "design" as const } };
    open({ ...blocked, tasks: [research, design] } as Run);
    expect(await screen.findByText("阶段：发散（按类别）")).toBeInTheDocument();
    expect(screen.getByText("阶段：设计")).toBeInTheDocument();
  });

  test("a Session without a repository offers its output, not publishing (D65)", async () => {
    open(makeRun({ id: "run-n", status: "completed", worker_id: "w1", workspace: "/w/n", source: { type: "none" } }));
    expect(await screen.findByRole("button", { name: /下载产出/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /下载源码/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/发布到/)).not.toBeInTheDocument();
  });

  test("nothing is left for a person to accept (D71)", async () => {   // an earlier version's wait, until the Engine accepts it
    open({ ...blocked, status: "awaiting_acceptance", error: null,
      attention: { title: "即将继续", detail: "验收确认已取消，待人判断的项记为提示。", action: "无需操作。" } } as Run);
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认通过/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /不通过，返工/ })).not.toBeInTheDocument();
  });

  test("a Session that may not switch tools says so", async () => {
    open({ ...blocked, recovery: { ...blocked.recovery!, policy: { enabled: true, max_cycles: 2, switch_tools: false } } } as Run);
    expect(await screen.findByText("自动纠错已开启 · 最多 2 轮 · 不换工具")).toBeInTheDocument();
  });

  test("an attempt whose checks are running says so", async () => {
    const task = blocked.tasks[0];
    const evidence = { ...task.attempts![0].evidence, checks: { job: "j", started: 1, commands: 3 } };
    open({ ...blocked, tasks: [{ ...task, attempts: [{ ...task.attempts![0], status: "running", evidence }] }, blocked.tasks[1]] } as Run);
    await userEvent.click(await screen.findByText("执行证据与历史 Attempt"));
    expect(screen.getByText("正在运行检查命令（3 条）")).toBeInTheDocument();
  });

  test("continue lets the user choose which later tasks rerun, the rules adding those that must", async () => {
    const step = (id: string, name: string, extra: Record<string, unknown>) => ({ ...spec(id, name, "claude"), ...extra });
    const run = makeRun({ id: "run-k", status: "completed", worker_id: "w1", version: 3, tasks: [
      { id: "code", run_id: "run-k", ordinal: 0, status: "accepted", current_attempt: null, spec: step("code", "实现", { kind: "implement" }), attempts: [] },
      { id: "docs", run_id: "run-k", ordinal: 1, status: "accepted", current_attempt: null, spec: step("docs", "文档", { kind: "deliver" }), attempts: [] },
      { id: "check", run_id: "run-k", ordinal: 2, status: "accepted", current_attempt: null, spec: step("check", "核对", { kind: "test" }), attempts: [] },
    ] });
    const calls = open(run, { "POST /runs/run-k/continue": { id: "run-k", version: 4 } });
    await userEvent.click((await screen.findAllByRole("button", { name: "配置并续作" }))[0]);
    const dialog = await screen.findByRole("dialog");
    const docs = within(dialog).getByRole("checkbox", { name: /文档/ });
    const check = within(dialog).getByRole("checkbox", { name: /核对/ });
    expect(check).toBeChecked();
    expect(check).toBeDisabled();
    expect(within(dialog).getByText(/审查 \/ 测试在改动后必须复验/)).toBeInTheDocument();
    await userEvent.click(docs);
    expect(docs).not.toBeChecked();
    await userEvent.click(within(dialog).getByRole("button", { name: "保存并续作" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/runs/run-k/continue")?.body).toMatchObject({ task_id: "code", rerun: ["check"] }));
  });

  test("continue reminds to update the requirement when the native chat changed the workspace (D73, a reminder only)", async () => {
    const run = makeRun({ id: "run-chat", status: "completed", worker_id: "w1", tasks: blocked.tasks.map((t) => ({ ...t, status: "accepted" })) });
    const calls = open(run, { "GET /runs/run-chat/chat-changes": { files: ["hello.py", "README.md"], count: 2, commits: false },
      "POST /runs/run-chat/continue": { id: "run-chat", version: 3 } });
    await userEvent.click((await screen.findAllByRole("button", { name: "配置并续作" }))[0]);
    const dialog = await screen.findByRole("dialog");
    const reminder = await within(dialog).findByRole("note", { name: "对话中的改动" });
    expect(reminder).toHaveTextContent("工作区里有对话中的改动（2 个文件：hello.py、README.md）");
    expect(reminder).toHaveTextContent("请同步修改上面的「本次完整需求」");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存并续作" }));   // it never stops the continue
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/runs/run-chat/continue")).toBe(true));
  });

  test("continue shows no reminder when the workspace holds nothing new", async () => {
    const run = makeRun({ id: "run-clean", status: "completed", worker_id: "w1", tasks: blocked.tasks.map((t) => ({ ...t, status: "accepted" })) });
    const calls = open(run, { "GET /runs/run-clean/chat-changes": { files: [], count: 0, commits: false } });
    await userEvent.click((await screen.findAllByRole("button", { name: "配置并续作" }))[0]);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/runs/run-clean/chat-changes")).toBe(true));
    expect(within(dialog).queryByRole("note", { name: "对话中的改动" })).toBeNull();
  });

  test("tasks the latest continue left as they were say so", async () => {
    const run = makeRun({ id: "run-kept", status: "completed", kept: { version: 3, tasks: ["review"] }, tasks: blocked.tasks.map((t) => ({ ...t, status: "accepted" })) });
    open(run);
    const review = await waitFor(() => {
      const card = document.querySelector('[data-task="review"]');
      expect(card).not.toBeNull();
      return card as HTMLElement;
    });
    expect(review).toHaveTextContent("沿用 v3 结果");
    expect(document.querySelector('[data-task="code"]')).not.toHaveTextContent("沿用");
  });

  test("downloading the source asks for a link and follows it", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const calls = open(blocked, { "POST /runs/run-a/archive": { url: "/archive/t/run-a.zip", expires_in: 600 } });
    await userEvent.click(await screen.findByRole("button", { name: /下载源码/ }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect((click.mock.contexts[0] as HTMLAnchorElement).getAttribute("href")).toBe("/archive/t/run-a.zip");
    expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs/run-a/archive")).toBe(true);
    click.mockRestore();
  });

  describe("remote preview", () => {
    const done = makeRun({ id: "run-p", status: "completed", worker_id: "w1", native_parent: "octopus-run-p", version: 3 });
    const view = (extra: Record<string, unknown>) => ({ status: "stopped", port: null, error: null, log: [], started: null,
      started_by: null, last_access: null, idle_stop_at: null, openable: false, ...extra });

    test("starts the Session's app", async () => {
      const calls = open(done, { "GET /runs/run-p/app": view({}), "POST /runs/run-p/app": view({ status: "starting" }) });
      const panel = await screen.findByRole("region", { name: "远程预览" });
      await userEvent.click(within(panel).getByRole("button", { name: "启动" }));
      await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/runs/run-p/app")?.body)
        .toEqual({ action: "start" }));
    });

    test("a running preview opens in a new tab through a single-use link", async () => {
      const replace = vi.fn();
      vi.stubGlobal("open", vi.fn(() => ({ closed: false, opener: {}, location: { replace }, close: vi.fn() })));
      const calls = open(done, {
        "GET /runs/run-p/app": view({ status: "running", port: 47001, openable: true, started: 1_790_000_000,
          started_by: { id: "u1", username: "fixture-owner" }, idle_stop_at: 1_790_001_800 }),
        "POST /runs/run-p/app/open": { url: "https://preview.example/_kirocrew/open/t", expires_in: 600 },
      });
      await userEvent.click(await screen.findByRole("button", { name: /打开远程预览/ }));
      await waitFor(() => expect(replace).toHaveBeenCalledWith("https://preview.example/_kirocrew/open/t"));
      expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs/run-p/app/open")).toBe(true);
      const panel = screen.getByRole("region", { name: "远程预览" });
      expect(panel).toHaveTextContent("运行中");
      expect(panel).toHaveTextContent("fixture-owner");
      expect(panel).toHaveTextContent("空闲 30 分钟后自动停止");
    });

    test("a failed preview shows why, with its log", async () => {
      open(done, { "GET /runs/run-p/app": view({ status: "failed", error: "进程 web 已退出（代码 1）", log: ["npm ERR! boom"] }) });
      const panel = await screen.findByRole("region", { name: "远程预览" });
      expect(await within(panel).findByText("进程 web 已退出（代码 1）")).toBeInTheDocument();
      await userEvent.click(within(panel).getByText("最近日志"));
      expect(within(panel).getByText(/npm ERR! boom/)).toBeInTheDocument();
    });

    test("a Session running tasks cannot start one, and says why", async () => {
      open(makeRun({ id: "run-r", status: "running", worker_id: "w1" }), { "GET /runs/run-r/app": view({}) });
      const panel = await screen.findByRole("region", { name: "远程预览" });
      expect(within(panel).getByRole("button", { name: "启动" })).toBeDisabled();
      expect(panel).toHaveTextContent("会话正在执行任务");
    });

    test("an environment without remote preview says it needs an upgrade", async () => {
      open(done, { "GET /runs/run-p/app": view({ status: "unsupported" }) });
      const panel = await screen.findByRole("region", { name: "远程预览" });
      expect(await within(panel).findByText(/执行环境需要升级/)).toBeInTheDocument();
      expect(within(panel).queryByRole("button", { name: "启动" })).not.toBeInTheDocument();
    });
  });

  test("shows who created the Session and the credential it clones with", async () => {
    const source = { id: "cred-1", name: "bob 的 GitHub", kind: "github_pat", scope: "user", owner: { id: "u2", username: "bob" },
      hint: "…1a2b", bound_by: { id: "u2", username: "bob" }, at: 1 };
    open(makeRun({ id: "run-c", status: "completed", worker_id: "w1", created_by: { id: "u2", username: "bob" },
      credentials: { source } } as Partial<Run>));
    const line = await screen.findByText(/创建者 bob/);
    expect(line).toHaveTextContent("创建者 bob · 凭证：bob 的个人 GitHub Token（…1a2b）");
    expect(line).not.toHaveTextContent("不可用");
  });

  test("the creator can change or remove the Session's credential", async () => {
    const source = { id: "cred-1", name: "旧的", kind: "github_pat", scope: "user", owner: { id: "u1", username: "fixture-owner" },
      hint: "…1a2b", bound_by: { id: "u1", username: "fixture-owner" }, at: 1, unavailable: true };
    const calls = open(makeRun({ id: "run-e", status: "blocked", worker_id: "w1", owner_id: "u1",
      source: { type: "git", url: "https://github.com/kirocrew/app.git" }, created_by: { id: "u1", username: "fixture-owner" },
      credentials: { source } } as Partial<Run>), {
      "GET /credentials": [{ id: "cred-2", name: "新的", kind: "github_pat", host: "github.com", scope: "user", access: "read",
        hint: "…9z9z", owner: { id: "u1", username: "fixture-owner" }, repos: [], disabled: false, can_manage: true }],
      "POST /runs/run-e/credentials": { source: { ...source, id: "cred-2", name: "新的", hint: "…9z9z", unavailable: undefined } },
    });
    await userEvent.click(await screen.findByRole("button", { name: "更换凭证" }));
    await userEvent.selectOptions(screen.getByLabelText("会话凭证"), "cred-2");
    await userEvent.click(screen.getByRole("button", { name: "保存凭证" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/runs/run-e/credentials")?.body)
      .toEqual({ source: "cred-2" }));
  });

  describe("publishing", () => {
    const writable = { id: "cred-1", name: "bob 写", kind: "github_pat", scope: "user", owner: { id: "u1", username: "fixture-owner" },
      hint: "…1a2b", bound_by: { id: "u1", username: "fixture-owner" }, at: 1, access: "write" };
    const done = (extra: Partial<Run> = {}) => makeRun({ id: "run-pub", status: "completed", worker_id: "w1", owner_id: "u1",
      source: { type: "git", url: "https://github.com/kirocrew/app.git" }, created_by: { id: "u1", username: "fixture-owner" },
      credentials: { source: writable }, ...extra } as Partial<Run>);

    test("a finished Session with a write credential is published as a draft pull request", async () => {
      const calls = open(done(), { "POST /runs/run-pub/publish": { branch: "octopus/run-pub", url: "https://github.com/kirocrew/app/pull/7",
        number: 7, kind: "pull_request", reused: false } });
      await userEvent.click(await screen.findByRole("button", { name: /发布到仓库/ }));
      await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs/run-pub/publish")).toBe(true));
    });

    test("a published Session links its pull request", async () => {
      open(done({ publish: { branch: "octopus/run-pub", url: "https://github.com/kirocrew/app/pull/7", number: 7,
        kind: "pull_request", reused: false, commit: "abc", at: 1, by: { id: "u1", username: "fixture-owner" } } } as Partial<Run>));
      const link = await screen.findByRole("link", { name: /PR #7/ });
      expect(link).toHaveAttribute("href", "https://github.com/kirocrew/app/pull/7");
      expect(document.querySelector("#run-publish")).toHaveTextContent("已发布：octopus/run-pub · PR #7");
    });

    test("a branch pushed without its pull request says so instead of linking nothing", async () => {
      open(done({ publish: { branch: "octopus/run-pub", url: null, number: null, kind: null, reused: false, commit: "abc", at: 1,
        by: { id: "u1", username: "fixture-owner" }, request_error: "凭证没有此操作的权限（HTTP 403）" } } as Partial<Run>));
      await screen.findByRole("button", { name: /再次发布到仓库/ });
      expect(document.querySelector("#run-publish")).toHaveTextContent("已推送：octopus/run-pub（拉取请求未创建：凭证没有此操作的权限（HTTP 403））");
      expect(screen.queryByRole("link", { name: /#/ })).not.toBeInTheDocument();
    });

    test("the automatic publish is switched on from the session page (D47)", async () => {
      const calls = open(done(), { "POST /runs/run-pub/publish-policy": { id: "run-pub", publish_policy: { auto: true, source: "user" } } });
      await userEvent.click(await screen.findByRole("checkbox", { name: /完成后自动发布 Draft PR/ }));
      await waitFor(() => expect(calls.find((c) => c.path === "/v1/runs/run-pub/publish-policy")?.body).toEqual({ auto: true }));
    });

    test("an automatic publish that did not happen says why (D47)", async () => {
      open(done({ publish_policy: { auto: true, source: "requested", quote: "完成后提交 PR" },
        publish_auto: { ok: false, code: "credential_read_only", error: "凭证「bob 写」为只读，无法发布", version: 3 } } as Partial<Run>));
      expect(await screen.findByText(/自动发布未完成：凭证「bob 写」为只读，无法发布/)).toBeInTheDocument();
      expect(screen.getByText(/需求中要求提交 PR/)).toBeInTheDocument();
    });

    test("the verified candidate is what will be pushed", async () => {
      open(done({ verified: { sha: "abc1234def5678abc1234def5678abc1234def56", task_id: "review" } } as Partial<Run>));
      await screen.findByRole("button", { name: /发布到仓库/ });
      expect(document.querySelector("#run-publish")).toHaveTextContent("将推送已验证的候选 abc1234");
    });

    test("a read-only credential cannot publish, and says so", async () => {
      open(done({ credentials: { source: { ...writable, access: "read" } } } as Partial<Run>));
      expect(await screen.findByText(/凭证为只读，无法发布/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /发布到仓库/ })).not.toBeInTheDocument();
    });
  });

  test("a credential that was disabled or deleted is shown as unavailable", async () => {
    const source = { id: "cred-2", name: "Team GitLab", kind: "gitlab_token", scope: "team", owner: { id: "t1", name: "平台组" },
      hint: "…0000", bound_by: { id: "u1", username: "fixture-owner" }, at: 1, unavailable: true };
    open(makeRun({ id: "run-d", status: "blocked", worker_id: "w1", created_by: { id: "u1", username: "fixture-owner" },
      credentials: { source } } as Partial<Run>));
    expect(await screen.findByText(/凭证：Team「平台组」的 GitLab Token（…0000）/)).toHaveTextContent("已停用或删除");
  });

  test("a Session without a Worker has no source to download yet", async () => {
    open(makeRun({ id: "run-n", status: "waiting_worker", worker_id: null, workspace: null }));
    expect(await screen.findByRole("button", { name: /下载源码/ })).toBeDisabled();
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
    // D49 R10: dev-own is the owner's boundary — only they put the work on it; admin-own is not the Session's space
    await waitFor(() => expect(dialog).toHaveTextContent("此空间尚无可用 Worker"));
    expect(dialog).not.toHaveTextContent("dev-own");
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
        { id: "code", name: "实现", status: "changed", fields: { tool: ["claude", "codex"], role: ["sde", "integration_test"],
          skills: [["frontend-design"], ["frontend-design", "test-driven-development"]] },
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
    expect(panel).toHaveTextContent("方法 frontend-design → frontend-design；test-driven-development");   // D50
    expect(within(panel).getByText("角色 SDE 开发 → 集成测试")).toBeInTheDocument();   // D46 P2: titles, not ids
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

  test("a Team Session on the Team's environment for want of one's own boundary says so (D49)", async () => {
    open(makeRun({ id: "run-p", status: "running", worker_id: "w1", team_id: "team-1",
      placement: { kind: "team", hint: "你还没有自己的执行边界：本次在 Team 的环境执行" } } as Partial<Run>));
    expect(await screen.findByText(/你还没有自己的执行边界/, { selector: "#placement-hint" })).toBeInTheDocument();
  });

  test("the environments a waiting Session may move to come from the platform (D49)", async () => {
    const waiting = makeRun({ id: "run-m2", status: "waiting_worker", version: 1, team_id: "team-1" });
    const asked: string[] = [];
    open(waiting, { "GET /placement": (_b: unknown, url: URL) => { asked.push(url.search); return { space: "team-1", hint: null, default: null,
      options: [{ id: "teamenv", name: "研发组环境", scope: "dedicated", own: false, isolation: "dedicated", models: {} },
        { id: "alice-b", name: "alice", scope: "dedicated", own: true, isolation: "dedicated", models: {} }] }; } });
    await userEvent.click(await screen.findByRole("button", { name: "分配 Worker" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog).toHaveTextContent("研发组环境"));
    expect(dialog).toHaveTextContent("alice");
    expect(asked).toContain("?run_id=run-m2");
  });

  test("on another member's boundary the instructions stay with its owner (D49 R10)", async () => {
    open(makeRun({ id: "run-r10", status: "blocked", version: 3, worker_id: "alice-b", team_id: "team-1", native_parent: "octopus-x",
      permissions: { operate: true, delete: false, native: false, publish: true, direct: false,
        direct_reason: "此会话在 alice 的执行边界上运行：只有边界主人能在上面下达指令" } } as Partial<Run>));
    expect(await screen.findByText(/只有边界主人能在上面下达指令/, { selector: "#direct-hint" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /续作/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /改派/ })).toBeNull();   // a move left the work behind (R10 review)
  });

  test("only its creator is offered their own boundary for a waiting Session (D49 R10)", async () => {
    const waiting = makeRun({ id: "run-w2", status: "waiting_worker", version: 1, team_id: "team-1", owner_id: "u2" });
    open(waiting, { "GET /placement": { space: "team-1", hint: null, default: null,
      options: [{ id: "alice-b", name: "alice", scope: "dedicated", own: true, isolation: "dedicated", models: {} },
        { id: "teamenv", name: "研发组环境", scope: "dedicated", own: false, isolation: "dedicated", models: {} }] } });
    await userEvent.click(await screen.findByRole("button", { name: "分配 Worker" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog).toHaveTextContent("研发组环境"));
    expect(within(dialog).queryByRole("option", { name: /alice/ })).toBeNull();
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
