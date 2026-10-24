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

/** The table row of the environment named ``name``, found by its name cell (a button that shows its detail). */
async function rowOf(list: HTMLElement, name: string) {
  return (await within(list).findByRole("button", { name })).closest("tr") as HTMLElement;
}

/** 管理 › 执行环境, with the detail of the environment named ``name`` chosen from its row. */
async function openEnvironment(name: string) {
  const list = await openSection("管理", "执行环境");
  const row = await rowOf(list, name);
  await userEvent.click(within(row).getByRole("button", { name }));
  return { list, row, detail: within(list).getByRole("region", { name }) };
}

/** Picks ``item`` in the 「⋯」 menu of the row of the environment ``name``. */
async function fromMenu(row: HTMLElement, name: string, item: string) {
  await userEvent.click(within(row).getByRole("button", { name: "执行环境操作：" + name }));
  await userEvent.click(await screen.findByRole("menuitem", { name: new RegExp("^" + item) }));
}

/** What the 「⋯」 menu of the row of the environment ``name`` offers; the menu is closed again. */
async function menuItems(row: HTMLElement, name: string) {
  await userEvent.click(within(row).getByRole("button", { name: "执行环境操作：" + name }));
  const items = (await screen.findAllByRole("menuitem")).map((item) => item.textContent);
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  return items;
}

afterEach(() => vi.unstubAllGlobals());

describe("管理 › 执行环境", () => {
  test("each environment is one row with its live status, type, version, Kiro login, tools, spaces, Sessions in progress and actions", async () => {
    start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    expect(within(list).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["状态", "名称", "类型", "版本", "Kiro 登录", "可用工具", "服务空间", "进行中会话", "操作"]);
    const cells = async (name: string) => within(await rowOf(list, name)).getAllByRole("cell").slice(0, 8).map((c) => c.textContent);
    await waitFor(async () => expect(await cells("personal"))
      .toEqual(["可达", "personal", "共享", "v1.0.0", "已登录", "Kiro CLI · Claude Code", "2", "2"]));
    expect(await cells("w1")).toEqual(["不可达", "w1", "空闲专属", "—", "—", "—", "—", "0"]);
    expect(await cells("old-host")).toEqual(["已停用", "old-host", "专属", "—", "—", "—", "—", "0"]);
    expect(within(list).getByRole("region", { name: "执行环境列表" })).toHaveTextContent("共 3 个");
  });

  // PRD 2026-10-23 (管理 / 我的设置 in dialogs): the page holds the list and its status; every change opens a dialog.
  test("nothing on the page is typed into, picked or ticked: only the search", async () => {
    start(ENVIRONMENTS);
    const { list } = await openEnvironment("personal");   // a shared one: its detail lists the spaces it serves
    expect(list.querySelectorAll("input:not([type=search]), select, textarea")).toHaveLength(0);
    expect(within(list).queryAllByRole("checkbox")).toEqual([]);
    expect(within(list).getByRole("searchbox", { name: "搜索执行环境名称" })).toBeInTheDocument();
  });

  test("编辑 and 分配到空间 sit beside a row; the rest are in its 「⋯」 menu, the dangerous ones last", async () => {
    start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    const shared = await rowOf(list, "personal");
    expect(within(shared).getByRole("button", { name: "编辑：personal" })).toBeInTheDocument();
    expect(within(shared).getByRole("button", { name: "分配到空间：personal" })).toBeInTheDocument();
    expect(await menuItems(shared, "personal")).toEqual(["刷新模型", "停用…", "退役…"]);
    // A disabled one takes no spaces; it is enabled again from the menu.
    const disabled = await rowOf(list, "old-host");
    expect(within(disabled).queryByRole("button", { name: "分配到空间：old-host" })).toBeNull();
    expect(await menuItems(disabled, "old-host")).toEqual(["刷新模型", "启用", "退役…"]);
  });

  test("the list is searched by name", async () => {
    start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    await rowOf(list, "old-host");
    await userEvent.type(within(list).getByRole("searchbox", { name: "搜索执行环境名称" }), "OLD");
    expect(within(list).queryByRole("button", { name: "personal" })).toBeNull();
    expect(within(list).getByRole("button", { name: "old-host" })).toBeInTheDocument();
    await userEvent.clear(within(list).getByRole("searchbox", { name: "搜索执行环境名称" }));
    await userEvent.type(within(list).getByRole("searchbox", { name: "搜索执行环境名称" }), "nothing");
    expect(list).toHaveTextContent("没有名称含「nothing」的执行环境。");
  });

  test("the chosen environment shows its health, the spaces it serves and where it is; one that needs attention is chosen first", async () => {
    start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    const spare = await within(list).findByRole("region", { name: "w1" });   // unreachable: shown first
    await waitFor(() => expect(spare).toHaveTextContent("不可达：Worker 未返回可核实回执"));
    expect(spare).toHaveTextContent("https://10.42.1.74:8943");
    await userEvent.click(within(await rowOf(list, "personal")).getByRole("button", { name: "personal" }));
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
    const { row, detail } = await openEnvironment("personal");
    await waitFor(() => expect(detail).toHaveTextContent("kiro-cli 登录已过期"));
    expect(within(row).getByText("已过期")).toBeInTheDocument();
  });

  test("刷新 reads every environment's health again", async () => {
    const calls = start(ENVIRONMENTS);
    const list = await openSection("管理", "执行环境");
    await within(list).findByRole("region", { name: "w1" });
    const reads = () => calls.filter((c) => c.path === "/v1/admin/environments").length;
    const before = reads();
    await userEvent.click(within(within(list).getByRole("region", { name: "执行环境列表" })).getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  test("刷新模型 reads the environment's model catalogue again, at once", async () => {
    const calls = start({ ...ENVIRONMENTS, "GET /workers/w1/capabilities": { models: {} } });
    const list = await openSection("管理", "执行环境");
    await fromMenu(await rowOf(list, "personal"), "personal", "刷新模型");
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/workers/w1/capabilities")).toBe(true));
    expect(await screen.findByText("已重新读取 personal 的模型目录")).toBeInTheDocument();
  });

  test("a shared environment is given to a user in a dialog, and a space it serves is taken away after confirming", async () => {
    const calls = start({ ...ENVIRONMENTS, "POST /workers/w1/spaces": { ok: true }, "DELETE /workers/w1/spaces": { ok: true } });
    const { row, detail } = await openEnvironment("personal");
    await userEvent.click(within(row).getByRole("button", { name: "分配到空间：personal" }));
    const dialog = await screen.findByRole("dialog", { name: "分配到空间" });
    const picker = within(dialog).getByLabelText("空间");
    expect(picker).toHaveFocus();
    // Spaces it already serves are not offered again.
    expect(within(picker).queryByRole("option", { name: "Team · 平台组" })).toBeNull();
    expect(within(picker).queryByRole("option", { name: "个人 · fixture-owner" })).toBeNull();
    await userEvent.selectOptions(picker, "user:u2");
    await userEvent.click(within(dialog).getByRole("button", { name: "分配" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/workers/w1/spaces")?.body)
      .toEqual({ user_id: "u2" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("已把 personal 分配到 个人 · dev1")).toBeInTheDocument();
    expect(row).toHaveAttribute("data-fresh", "true");   // the changed row lights up
    // Taking a space away asks first and says what happens to its Sessions.
    await userEvent.click(within(detail).getByRole("button", { name: "移出 Team · 平台组" }));
    const confirm = await screen.findByRole("dialog", { name: "移出空间" });
    expect(confirm).toHaveTextContent("Team · 平台组 的新会话不再派到「personal」");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(within(confirm).getByRole("button", { name: "移出" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE" && c.path === "/v1/workers/w1/spaces")?.body)
      .toEqual({ team_id: "team-1" }));
    expect(await screen.findByText("已把 Team · 平台组 移出 personal")).toBeInTheDocument();
  });

  test("a spare environment is offered only to Teams without one", async () => {
    const calls = start({ ...ENVIRONMENTS, "POST /workers/w2/spaces": { ok: true } });
    const list = await openSection("管理", "执行环境");
    await userEvent.click(within(await rowOf(list, "w1")).getByRole("button", { name: "分配到空间：w1" }));
    const dialog = await screen.findByRole("dialog", { name: "分配到空间" });
    const picker = within(dialog).getByLabelText("空间");
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual(["选择 Team", "Team · Web应用软件研发组"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "分配" }));   // nothing chosen: refused in the dialog
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("先选择一个空间");
    expect(calls.some((c) => c.path === "/v1/workers/w2/spaces")).toBe(false);
    await userEvent.selectOptions(picker, "team:team-2");
    await userEvent.click(within(dialog).getByRole("button", { name: "分配" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w2/spaces")?.body).toEqual({ team_id: "team-2" }));
  });

  test("a Worker host shared with one Team can be made that Team's own environment again, after confirming", async () => {
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
    const list = await openSection("管理", "执行环境");
    expect(await menuItems(await rowOf(list, "personal"), "personal")).not.toContain("改回专属环境…");
    await fromMenu(await rowOf(list, "w1"), "w1", "改回专属环境");
    const dialog = await screen.findByRole("dialog", { name: "改回专属环境" });
    expect(dialog).toHaveTextContent("成为 Team「平台组」的专属环境");
    expect(calls.some((c) => c.path === "/v1/workers/w2/dedicate")).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "改回专属环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w2/dedicate")?.method).toBe("POST"));
    expect(await screen.findByText("已把 w1 改回 平台组 的专属环境")).toBeInTheDocument();
  });

  test("sharing a Worker shows what sharing means and asks for confirmation", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "dedicated",
        spaces: [], capabilities: { isolation: "trusted-local" } }],
      "POST /workers/w1/share": { id: "w1", scope: "shared" },
    });
    const list = await openSection("管理", "执行环境");
    await fromMenu(await rowOf(list, "personal"), "personal", "设为共享环境");
    const dialog = await screen.findByRole("dialog", { name: "设为共享环境" });
    expect(within(dialog).getByText(/能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围/)).toBeInTheDocument();
    expect(within(dialog).getByText(/只有边界所有者能打开原生 KiroCrew 入口/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/workers/w1/share")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "设为共享环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/share")?.body).toEqual({ confirm: true }));
    expect(await screen.findByText("已把 personal 设为共享环境")).toBeInTheDocument();
  });

  test("disabling a Worker asks first and says what happens to its Sessions; enabling it again is direct", async () => {
    const calls = start({ ...ENVIRONMENTS, "POST /workers/w1/enabled": { ok: true }, "POST /workers/w3/enabled": { ok: true } });
    const list = await openSection("管理", "执行环境");
    await fromMenu(await rowOf(list, "personal"), "personal", "停用");
    const dialog = await screen.findByRole("dialog", { name: "停用执行环境" });
    expect(dialog).toHaveTextContent("新的会话不会再派到「personal」");
    expect(dialog).toHaveTextContent("重新启用后自动继续");
    expect(calls.some((c) => c.path === "/v1/workers/w1/enabled")).toBe(false);   // nothing happens before the confirmation
    await userEvent.click(within(dialog).getByRole("button", { name: "停用" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/enabled")?.body).toEqual({ enabled: false }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("已停用 personal")).toBeInTheDocument();
    await fromMenu(await rowOf(list, "old-host"), "old-host", "启用");
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w3/enabled")?.body).toEqual({ enabled: true }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await screen.findByText("已启用 old-host")).toBeInTheDocument();
  });

  test("编辑 renames a Worker and replaces its connection in one dialog", async () => {
    const calls = start({ ...ONE_WORKER, "PATCH /workers/w1": { id: "w1", name: "共享-1" },
      "POST /workers/w1/connection": { id: "w1", capabilities: {} } });
    const list = await openSection("管理", "执行环境");
    const row = await rowOf(list, "personal");
    await userEvent.click(within(row).getByRole("button", { name: "编辑：personal" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑 Worker" });
    const title = within(dialog).getByLabelText("Worker 名称");
    expect(title).toHaveFocus();
    expect(title).toHaveValue("personal");
    expect(within(dialog).getByLabelText("Worker Token")).toHaveAttribute("type", "password");
    await userEvent.clear(title);
    await userEvent.type(title, "共享-1");
    await userEvent.type(within(dialog).getByLabelText("Worker URL"), "https://10.0.0.9:8943");
    await userEvent.type(within(dialog).getByLabelText("Worker Token"), "t".repeat(40));
    await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/v1/workers/w1")?.body).toEqual({ name: "共享-1" }));
    expect(calls.find((c) => c.path === "/v1/workers/w1/connection")?.body)
      .toEqual({ connection: { url: "https://10.0.0.9:8943", token: "t".repeat(40) } });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("已保存 Worker 共享-1")).toBeInTheDocument();
    expect(row).toHaveAttribute("data-fresh", "true");
  });

  test("编辑 keeps the connection unless a new token is given; a new address needs one", async () => {
    const calls = start({ ...ENVIRONMENTS, "POST /workers/w1/connection": { id: "w1", capabilities: {} } });
    const list = await openSection("管理", "执行环境");
    const row = await rowOf(list, "personal");
    await waitFor(() => expect(within(row).getAllByRole("cell")[0]).toHaveTextContent("可达"));
    await userEvent.click(within(row).getByRole("button", { name: "编辑：personal" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑 Worker" });
    const url = within(dialog).getByLabelText("Worker URL");
    expect(url).toHaveValue("http://127.0.0.1:8928");   // where it is now
    await userEvent.clear(url);
    await userEvent.type(url, "https://10.0.0.9:8943");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("更换连接需要同时填写 Worker URL 与 Worker Token");
    expect(calls.some((c) => c.path === "/v1/workers/w1/connection")).toBe(false);
    // Rotating the token alone keeps the address; the name, unchanged, is not sent.
    await userEvent.clear(url);
    await userEvent.type(url, "http://127.0.0.1:8928");
    await userEvent.type(within(dialog).getByLabelText("Worker Token"), "n".repeat(40));
    await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/connection")?.body)
      .toEqual({ connection: { url: "http://127.0.0.1:8928", token: "n".repeat(40) } }));
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  test("retiring a Worker asks first and shows why the server refuses, in the dialog", async () => {
    const calls = start({ ...ONE_WORKER, "POST /workers/w1/retire": () => new Response(
      JSON.stringify({ error: "仍是 Team「平台组」的执行环境：先为该 Team 更换执行环境", code: "in_use" }), { status: 409 }) });
    const list = await openSection("管理", "执行环境");
    await fromMenu(await rowOf(list, "personal"), "personal", "退役");
    const dialog = await screen.findByRole("dialog", { name: "退役执行环境" });
    expect(within(dialog).getByText(/退役后不再出现在列表与选择器中/)).toBeInTheDocument();
    expect(dialog).toHaveTextContent("这一步不能撤回");
    expect(calls.find((c) => c.path === "/v1/workers/w1/retire")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "退役" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/retire")?.body).toEqual({}));
    expect(await within(dialog).findByText(/仍是 Team「平台组」的执行环境/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "退役执行环境" })).toBeInTheDocument();   // stays open
  });

  test("a retired Worker leaves the list", async () => {
    let listed = ONE_WORKER["GET /workers"];
    start({ ...ONE_WORKER, "GET /workers": () => listed,
      "POST /workers/w1/retire": () => { listed = []; return { id: "w1", retired: true }; } });
    const list = await openSection("管理", "执行环境");
    await fromMenu(await rowOf(list, "personal"), "personal", "退役");
    await userEvent.click(within(await screen.findByRole("dialog", { name: "退役执行环境" })).getByRole("button", { name: "退役" }));
    await waitFor(() => expect(within(list).queryByRole("button", { name: "personal" })).toBeNull());
    expect(await screen.findByText("已退役 personal")).toBeInTheDocument();
    expect(list).toHaveTextContent("还没有执行环境");
  });

  test("a Worker shows when the platform last read its health (D43)", async () => {
    start({ ...ONE_WORKER, "GET /workers": [{ ...ONE_WORKER["GET /workers"][0],
      capabilities: { isolation: "trusted-local", checked_at: 1_792_100_000 } }] });
    const { detail } = await openEnvironment("personal");
    expect(detail).toHaveTextContent("最近检查" + formatTime(1_792_100_000, true));
  });

  test("登记 Worker binds a started Gateway to a space, in a dialog", async () => {
    const made = { id: "w9", name: "team-a-worker", owner_id: "u1", team_id: null, enabled: true, scope: "spare", spaces: [],
      capabilities: { isolation: "dedicated" } };
    let listed: unknown[] = ONE_WORKER["GET /workers"];
    const calls = start({ ...ONE_WORKER, "GET /workers": () => listed,
      "POST /workers": () => { listed = [...listed, made]; return { id: "w9", name: "team-a-worker", scope: "spare", capabilities: {} }; } });
    const list = await openSection("管理", "执行环境");
    await userEvent.click(within(list).getByRole("button", { name: "登记 Worker" }));
    const dialog = await screen.findByRole("dialog", { name: "登记 Worker" });
    expect(within(list).queryByLabelText("Worker URL")).toBeNull();   // nothing to type into on the page itself
    expect(within(dialog).getByLabelText("名称")).toHaveFocus();
    expect(within(dialog).getByLabelText("Worker Token")).toHaveAttribute("type", "password");
    await userEvent.type(within(dialog).getByLabelText("名称"), "team-a-worker");
    await userEvent.selectOptions(within(dialog).getByLabelText("绑定空间"), "spare:");
    await userEvent.type(within(dialog).getByLabelText("Worker URL"), "https://10.0.0.9:8943");
    await userEvent.type(within(dialog).getByLabelText("Worker Token"), "t".repeat(40));
    await userEvent.click(within(dialog).getByRole("button", { name: "登记" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/workers")?.body)
      .toEqual({ name: "team-a-worker", spare: true, connection: { url: "https://10.0.0.9:8943", token: "t".repeat(40) } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("已登记 Worker team-a-worker")).toBeInTheDocument();
    expect(await rowOf(list, "team-a-worker")).toHaveAttribute("data-fresh", "true");   // the new row lights up
  });

  test("a registration the server refuses keeps the dialog open with its reason", async () => {
    start({ ...ONE_WORKER, "POST /workers": () => new Response(
      JSON.stringify({ error: "该 Gateway 边界已登记", code: "worker_conflict" }), { status: 409 }) });
    const list = await openSection("管理", "执行环境");
    await userEvent.click(within(list).getByRole("button", { name: "登记 Worker" }));
    const dialog = await screen.findByRole("dialog", { name: "登记 Worker" });
    await userEvent.type(within(dialog).getByLabelText("名称"), "again");
    await userEvent.type(within(dialog).getByLabelText("Worker URL"), "https://10.0.0.9:8943");
    await userEvent.type(within(dialog).getByLabelText("Worker Token"), "t".repeat(40));
    await userEvent.click(within(dialog).getByRole("button", { name: "登记" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("该 Gateway 边界已登记");
    expect(screen.getByRole("dialog", { name: "登记 Worker" })).toBeInTheDocument();
  });

  test("a member refreshes models in 我的设置 but cannot edit, assign, disable or retire a Worker", async () => {
    const calls = start({ ...ONE_WORKER, "GET /me": { id: "u1", username: "fixture-owner", admin: 0, scopes: ["read", "execute", "manage"] },
      "GET /workers/w1/capabilities": { models: {} } });
    const mine = await openSection("我的设置", "执行环境");
    expect(within(mine).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["名称", "类型", "状态", "归属", "可用工具", "最近检查", "操作"]);
    const row = (await within(mine).findByText("personal")).closest("tr") as HTMLElement;
    expect(within(row).getAllByRole("cell").slice(0, 4).map((c) => c.textContent)).toEqual(["personal", "共享", "可用", "共享环境"]);
    await userEvent.click(within(row).getByRole("button", { name: "刷新模型：personal" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/v1/workers/w1/capabilities")).toBe(true));
    for (const name of [/^编辑/, /^分配到空间/, /^退役/, /^停用/, /^执行环境操作/]) expect(within(row).queryByRole("button", { name })).toBeNull();
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
    expect(await within(await panel("执行环境")).findByRole("region", { name: "w1" })).toBeInTheDocument();   // and shows it first
  });
});
