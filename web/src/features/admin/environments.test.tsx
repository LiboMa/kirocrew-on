import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";
import { openSection, panel } from "@/test/pages";
import { formatTime } from "@/lib/labels";

const TOOLS = { kiro: { available: true, login: "ok" }, claude: { available: true }, codex: { available: false, reason: "codex 命令不存在" } };
const ENVIRONMENTS = {
  "GET /workers": [
    { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
      spaces: [{ user_id: "u1" }, { team_id: "team-1" }], capabilities: { isolation: "trusted-local", tools: TOOLS } },
    { id: "w2", name: "w1", owner_id: "u1", team_id: null, enabled: true, scope: "spare", spaces: [],
      capabilities: { isolation: "dedicated" } },
    { id: "w3", name: "old-host", owner_id: "u1", team_id: null, enabled: false, scope: "dedicated", spaces: [],
      capabilities: { isolation: "dedicated" } },
  ],
  "GET /teams": [
    { id: "team-1", name: "平台组", role: "owner", created: 1, members: [],
      environment: { worker_id: "w1", name: "personal", scope: "shared", enabled: true } },
    { id: "team-2", name: "Web应用软件研发组", role: null, created: 2, members: [], environment: null },
  ],
  "GET /users": [{ id: "u1", username: "fixture-owner" }, { id: "u2", username: "dev1" }],
  "GET /admin/environments": [
    { id: "w1", name: "personal", scope: "shared", enabled: true, owner: { id: "u1", username: "fixture-owner" }, team: null,
      spaces: [{ user_id: "u1", username: "fixture-owner" }, { team_id: "team-1", name: "平台组" }],
      url: "http://127.0.0.1:8928", active_runs: 2,
      health: { reachable: true, version: "1.0.0", isolation: "trusted-local", crew_entry: true, crew_origin: "https://d1.cloudfront.net",
        tool_auth: { kiro: "ok" } } },
    { id: "w2", name: "w1", scope: "spare", enabled: true, owner: { id: "u1", username: "fixture-owner" }, team: null, spaces: [],
      url: "https://10.42.1.74:8943", active_runs: 0, health: { reachable: false, error: "Worker 未返回可核实回执" } },
    { id: "w3", name: "old-host", scope: "dedicated", enabled: false, owner: { id: "u1", username: "fixture-owner" }, team: null,
      spaces: [], url: "https://10.42.1.80:8943", active_runs: 0, health: { reachable: false, error: "连接超时" } },
  ],
  "GET /admin/users": [
    { id: "u1", username: "fixture-owner", admin: true, active: true, created: 1, last_login: 1, teams: [], tokens: 1, running: 2 },
    { id: "u2", username: "dev1", admin: false, active: true, created: 1, last_login: null, teams: [], tokens: 0, running: 0 },
    { id: "u4", username: "gone", admin: false, active: false, created: 1, last_login: null, teams: [], tokens: 0, running: 0 },
  ],
};
const ONE_WORKER = { "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
  spaces: [{ user_id: "u1" }], capabilities: { isolation: "trusted-local" } }], "GET /admin/environments": [] };

function start(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], { "GET /admin/environments": [], ...extra }));
  render(<App />);
  return calls;
}

/** 管理 › 执行环境, with the detail of the environment named ``name`` chosen from its row. */
async function openEnvironment(name: string) {
  const list = await openSection("管理", "执行环境");
  const row = await within(list).findByRole("row", { name });
  await userEvent.click(within(row).getByRole("button", { name }));
  return { list, detail: within(list).getByRole("region", { name }) };
}

afterEach(() => vi.unstubAllGlobals());

describe("管理 › 执行环境", () => {
  test("each environment is one row with its live status, type, version, Kiro login, tools, spaces and Sessions in progress", async () => {
    start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    expect(within(list).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["状态", "名称", "类型", "版本", "Kiro 登录", "可用工具", "服务空间", "进行中会话"]);
    const cells = async (name: string) => within(await within(list).findByRole("row", { name })).getAllByRole("cell").map((c) => c.textContent);
    await waitFor(async () => expect(await cells("personal"))
      .toEqual(["可达", "personal", "共享", "v1.0.0", "已登录", "Kiro CLI · Claude Code", "2", "2"]));
    expect(await cells("w1")).toEqual(["不可达", "w1", "空闲专属", "—", "—", "—", "—", "0"]);
    expect(await cells("old-host")).toEqual(["已停用", "old-host", "专属", "—", "—", "—", "—", "0"]);
  });

  test("the chosen environment shows its health, the spaces it serves and where it is; one that needs attention is chosen first", async () => {
    start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    const spare = await within(list).findByRole("region", { name: "w1" });   // unreachable: shown first
    await waitFor(() => expect(spare).toHaveTextContent("不可达：Worker 未返回可核实回执"));
    expect(spare).toHaveTextContent("https://10.42.1.74:8943");
    await userEvent.click(within(within(list).getByRole("row", { name: "personal" })).getByRole("button", { name: "personal" }));
    const shared = within(list).getByRole("region", { name: "personal" });
    expect(within(list).queryByRole("region", { name: "w1" })).toBeNull();
    expect(shared).toHaveTextContent("可达 · v1.0.0");
    expect(shared).toHaveTextContent("原生入口 d1.cloudfront.net");
    expect(shared).toHaveTextContent("进行中会话 2");
    expect(shared).toHaveTextContent("共享环境 · 服务 2 个空间");
    expect(within(shared).getByText("个人 · fixture-owner")).toBeInTheDocument();
    expect(within(shared).getByText("Team · 平台组")).toBeInTheDocument();
  });

  test("an environment whose kiro-cli login has expired says so", async () => {
    const expired = ENVIRONMENTS["GET /admin/environments"].map((e) => e.id === "w1"
      ? { ...e, health: { ...e.health, tool_auth: { kiro: "expired" } } } : e);
    start({ ...ENVIRONMENTS, "GET /admin/environments": expired });
    const { list, detail } = await openEnvironment("personal");
    await waitFor(() => expect(detail).toHaveTextContent("kiro-cli 登录已过期"));
    expect(within(within(list).getByRole("row", { name: "personal" })).getByText("已过期")).toBeInTheDocument();
  });

  test("刷新状态 reads every environment's health again", async () => {
    const calls = start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    await within(list).findByRole("region", { name: "w1" });
    const reads = () => calls.filter((c) => c.path === "/v1/admin/environments").length;
    const before = reads();
    await userEvent.click(within(list).getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  test("a shared environment is given to a user and a space taken away from it", async () => {
    const calls = start({ ...ENVIRONMENTS, "POST /workers/w1/spaces": { ok: true }, "DELETE /workers/w1/spaces": { ok: true } });
    const { detail } = await openEnvironment("personal");
    const picker = within(detail).getByLabelText("分配到");
    // Spaces it already serves are not offered again.
    expect(within(picker).queryByRole("option", { name: "Team · 平台组" })).toBeNull();
    expect(within(picker).queryByRole("option", { name: "个人 · fixture-owner" })).toBeNull();
    await userEvent.selectOptions(picker, "user:u2");
    await userEvent.click(within(detail).getByRole("button", { name: "分配" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/workers/w1/spaces")?.body)
      .toEqual({ user_id: "u2" }));
    await userEvent.click(within(detail).getByRole("button", { name: "移出 Team · 平台组" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE" && c.path === "/v1/workers/w1/spaces")?.body)
      .toEqual({ team_id: "team-1" }));
  });

  test("a spare environment is offered only to Teams without one", async () => {
    const calls = start({ ...ENVIRONMENTS, "POST /workers/w2/spaces": { ok: true } });
    const { detail } = await openEnvironment("w1");
    const picker = within(detail).getByLabelText("分配到");
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual(["选择 Team", "Team · Web应用软件研发组"]);
    await userEvent.selectOptions(picker, "team:team-2");
    await userEvent.click(within(detail).getByRole("button", { name: "分配" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w2/spaces")?.body).toEqual({ team_id: "team-2" }));
  });

  test("a Worker host shared with one Team can be made that Team's own environment again", async () => {
    const calls = start({
      "GET /workers": [
        { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
          spaces: [{ team_id: "team-1" }], capabilities: { isolation: "trusted-local" } },
        { id: "w2", name: "w1", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
          spaces: [{ team_id: "team-1" }], capabilities: { isolation: "dedicated" } },
      ],
      "POST /workers/w2/dedicate": { id: "w2", scope: "dedicated", team_id: "team-1" },
    });
    // On the platform host (trusted-local) a Team cannot have its own environment.
    const { detail: local } = await openEnvironment("personal");
    expect(within(local).queryByRole("button", { name: "改回专属环境" })).toBeNull();
    const list = await panel("执行环境");
    await userEvent.click(within(within(list).getByRole("row", { name: "w1" })).getByRole("button", { name: "w1" }));
    await userEvent.click(within(within(list).getByRole("region", { name: "w1" })).getByRole("button", { name: "改回专属环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w2/dedicate")?.method).toBe("POST"));
  });

  test("sharing a Worker shows what sharing means and asks for confirmation", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "dedicated",
        spaces: [], capabilities: { isolation: "trusted-local" } }],
      "POST /workers/w1/share": { id: "w1", scope: "shared" },
    });
    const { detail } = await openEnvironment("personal");
    await userEvent.click(within(detail).getByRole("button", { name: "设为共享环境" }));
    expect(within(detail).getByText(/能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围/)).toBeInTheDocument();
    expect(within(detail).getByText(/只有边界所有者能打开原生 KiroCrew 入口/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/workers/w1/share")).toBeUndefined();
    await userEvent.click(within(detail).getByRole("button", { name: "确认设为共享环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/share")?.body).toEqual({ confirm: true }));
  });

  test("an administrator can disable a Worker", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, capabilities: { isolation: "trusted-local" } }],
      "POST /workers/w1/enabled": { ok: true },
    });
    const { detail } = await openEnvironment("personal");
    await userEvent.click(within(detail).getByRole("button", { name: "停用" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/enabled")?.body).toEqual({ enabled: false }));
  });

  test("an administrator renames a Worker and updates its connection", async () => {
    const calls = start({ ...ONE_WORKER, "PATCH /workers/w1": { id: "w1", name: "共享-1" },
      "POST /workers/w1/connection": { id: "w1", capabilities: {} } });
    const { detail } = await openEnvironment("personal");
    await userEvent.click(within(detail).getByRole("button", { name: "改名" }));
    const title = within(detail).getByLabelText("Worker 名称");
    await userEvent.clear(title);
    await userEvent.type(title, "共享-1");
    await userEvent.click(within(detail).getByRole("button", { name: "保存名称" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/v1/workers/w1")?.body).toEqual({ name: "共享-1" }));
    await userEvent.click(within(detail).getByRole("button", { name: "更新连接" }));
    expect(within(detail).getByLabelText("新的 Worker Token")).toHaveAttribute("type", "password");
    await userEvent.type(within(detail).getByLabelText("新的 Worker URL"), "https://10.0.0.9:8943");
    await userEvent.type(within(detail).getByLabelText("新的 Worker Token"), "t".repeat(40));
    await userEvent.click(within(detail).getByRole("button", { name: "保存连接" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/connection")?.body)
      .toEqual({ connection: { url: "https://10.0.0.9:8943", token: "t".repeat(40) } }));
  });

  test("retiring a Worker asks first and shows why the server refuses", async () => {
    const calls = start({ ...ONE_WORKER, "POST /workers/w1/retire": () => new Response(
      JSON.stringify({ error: "仍是 Team「平台组」的执行环境：先为该 Team 更换执行环境", code: "in_use" }), { status: 409 }) });
    const { detail } = await openEnvironment("personal");
    await userEvent.click(within(detail).getByRole("button", { name: "退役" }));
    expect(within(detail).getByText(/退役后不再出现在列表与选择器中/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/workers/w1/retire")).toBeUndefined();
    await userEvent.click(within(detail).getByRole("button", { name: "确认退役" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/retire")?.body).toEqual({}));
    expect(await screen.findByText(/仍是 Team「平台组」的执行环境/)).toBeInTheDocument();
  });

  test("a Worker shows when the platform last read its health (D43)", async () => {
    start({ ...ONE_WORKER, "GET /workers": [{ ...ONE_WORKER["GET /workers"][0],
      capabilities: { isolation: "trusted-local", checked_at: 1_792_100_000 } }] });
    const { detail } = await openEnvironment("personal");
    expect(detail).toHaveTextContent("最近检查" + formatTime(1_792_100_000, true));
  });

  test("登记 Worker binds a started Gateway to a space", async () => {
    const calls = start({ ...ONE_WORKER, "POST /workers": { id: "w9", capabilities: {} } });
    const list = await openSection("管理", "执行环境");
    await userEvent.click(within(list).getByRole("button", { name: "登记 Worker" }));
    const form = within(list).getByRole("form", { name: "登记 Worker" });
    await userEvent.type(within(form).getByLabelText("名称"), "team-a-worker");
    await userEvent.selectOptions(within(form).getByLabelText("绑定空间"), "spare:");
    await userEvent.type(within(form).getByLabelText("Worker URL"), "https://10.0.0.9:8943");
    await userEvent.type(within(form).getByLabelText("Worker Token"), "t".repeat(40));
    await userEvent.click(within(form).getByRole("button", { name: "登记" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/workers")?.body)
      .toEqual({ name: "team-a-worker", spare: true, connection: { url: "https://10.0.0.9:8943", token: "t".repeat(40) } }));
    await waitFor(() => expect(within(list).queryByRole("form", { name: "登记 Worker" })).toBeNull());
  });

  test("a member refreshes models in 我的设置 but cannot rename, reconnect or retire a Worker", async () => {
    const calls = start({ ...ONE_WORKER, "GET /me": { id: "u1", username: "fixture-owner", admin: 0, scopes: ["read", "execute", "manage"] },
      "GET /workers/w1/capabilities": { models: {} } });
    const mine = await openSection("我的设置", "执行环境");
    const row = within(mine).getByRole("group", { name: "personal" });
    await userEvent.click(within(row).getByRole("button", { name: "刷新模型" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/workers/w1/capabilities")).toBe(true));
    for (const name of ["改名", "更新连接", "退役", "停用"]) expect(within(row).queryByRole("button", { name })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path === "/v1/admin/environments")).toBe(false);   // administrators only
  });
});

describe("管理 › 概览", () => {
  test("shows reachable environments, Sessions in progress, active users and Teams; each opens its section", async () => {
    start(ENVIRONMENTS);
    const overview = await openSection("管理", "概览");
    await waitFor(() => expect(within(overview).getByRole("button", { name: /^执行环境/ })).toHaveTextContent("1 / 2"));
    expect(within(overview).getByRole("button", { name: /^进行中的会话/ })).toHaveTextContent("2");
    await waitFor(() => expect(within(overview).getByRole("button", { name: /^用户/ })).toHaveTextContent("2有效 · 共 3 位"));
    expect(within(overview).getByRole("button", { name: /^Team/ })).toHaveTextContent("2");
    const issues = within(overview).getByRole("region", { name: "需要处理" });
    expect(issues).toHaveTextContent("w1：不可达（Worker 未返回可核实回执）");
    expect(issues).not.toHaveTextContent("old-host");   // a disabled one is not a problem to fix
    await userEvent.click(within(overview).getByRole("button", { name: /^用户/ }));
    expect(await panel("用户")).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("section")).toBe("users");
  });

  test("a problem opens the environments", async () => {
    start(ENVIRONMENTS);
    const overview = await openSection("管理", "概览");
    await userEvent.click(await within(overview).findByRole("button", { name: /w1：不可达/ }));
    expect(await within(await panel("执行环境")).findByRole("region", { name: "w1" })).toBeInTheDocument();
  });
});
