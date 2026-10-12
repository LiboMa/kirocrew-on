import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";

function start(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], extra));
  render(<App />);
  return calls;
}

async function openSetting(name: RegExp) {
  await userEvent.click(await screen.findByRole("button", { name: "设置" }));
  await userEvent.click(await screen.findByRole("menuitem", { name }));
  return screen.findByRole("dialog");
}

afterEach(() => vi.unstubAllGlobals());

describe("settings", () => {
  test("a new API token is shown exactly once with the requested scopes", async () => {
    const calls = start({ "GET /tokens": [], "POST /tokens": { id: "t1", token: "oct_secret_value", scopes: ["read", "execute"], expires: 0 } });
    const dialog = await openSetting(/API 与 Coding Agent 接入/);
    await userEvent.type(within(dialog).getByLabelText("新 Token 名称"), "Claude Code");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建 API Token" }));
    expect(await screen.findByText("oct_secret_value")).toBeInTheDocument();
    expect(screen.getByText("仅显示这一次。将它配置到对应客户端的私有环境变量中。")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/tokens")?.body).toEqual({ label: "Claude Code", scopes: ["read", "execute"], ttl: 604800 });
  });

  test("an administrator creates a Team with its owner and an execution environment", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }], capabilities: { isolation: "trusted-local" } }],
      "POST /teams": { id: "team-2", name: "Web 组", role: "owner", environment: { worker_id: "w1", name: "personal", scope: "shared" } },
    });
    const dialog = await openSetting(/团队与成员/);
    await userEvent.type(within(dialog).getByLabelText("创建 Team"), "Web 组");
    expect(within(dialog).getByLabelText("执行环境")).toHaveValue("w1");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建 Team" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/teams")?.body)
      .toEqual({ name: "Web 组", owner: "fixture-owner", worker_id: "w1" }));
  });

  test("without any shared or spare environment a Team cannot be created", async () => {
    start();
    const dialog = await openSetting(/团队与成员/);
    expect(within(dialog).getByRole("button", { name: "创建 Team" })).toBeDisabled();
    expect(within(dialog).getByText(/先在主机上开通执行环境，或把一个 Worker 设为共享环境/)).toBeInTheDocument();
  });

  test("only administrators see Team creation", async () => {
    start({ "GET /me": { id: "u1", username: "fixture-owner", admin: 0, scopes: ["read", "execute", "manage"] } });
    const dialog = await openSetting(/团队与成员/);
    expect(within(dialog).queryByRole("button", { name: "创建 Team" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("Team 由平台管理员创建，并在创建时分配执行环境。")).toBeInTheDocument();
  });

  test("a Team without an execution environment can be given the shared one", async () => {
    const calls = start({
      "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [], environment: null }],
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }], capabilities: {} }],
      "POST /workers/w1/spaces": { ok: true },
    });
    await userEvent.click(await screen.findByRole("button", { name: "设置" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /团队与成员/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("未分配执行环境")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "分配执行环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/spaces")?.body).toEqual({ team_id: "team-1" }));
  });

  test("a Team without an execution environment can be given a spare Worker host", async () => {
    const calls = start({
      "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [], environment: null }],
      "GET /workers": [
        { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared", spaces: [{ user_id: "u1" }], capabilities: {} },
        { id: "sp1", name: "w1", owner_id: "u1", team_id: null, enabled: true, scope: "spare", spaces: [], capabilities: {} },
      ],
      "POST /workers/sp1/spaces": { ok: true },
    });
    const dialog = await openSetting(/团队与成员/);
    const row = dialog.querySelector('[data-team-environment="team-1"]') as HTMLElement;
    await userEvent.selectOptions(within(row).getByLabelText("为 平台组 分配的执行环境"), "sp1");
    await userEvent.click(within(row).getByRole("button", { name: "分配执行环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/sp1/spaces")?.body).toEqual({ team_id: "team-1" }));
  });

  test("a disabled environment is shown as disabled, not as missing", async () => {
    start({ "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [],
      environment: { worker_id: "w9", name: "w9", scope: "dedicated", enabled: false } }] });
    const dialog = await openSetting(/团队与成员/);
    const row = dialog.querySelector('[data-team-environment="team-1"]') as HTMLElement;
    expect(row).toHaveTextContent("执行环境已停用 · w9");
    expect(within(row).queryByRole("button", { name: "分配执行环境" })).not.toBeInTheDocument();
  });

  test("an administrator's pickers only offer the Teams they belong to", async () => {
    start({ "GET /teams": [
      { id: "team-1", name: "平台组", role: "owner", created: 1, members: [], environment: null },
      { id: "team-x", name: "别人的组", role: null, created: 2, members: [], environment: null },
    ] });
    await userEvent.click(await screen.findByRole("button", { name: /新建开发/ }));
    const dialog = await screen.findByRole("dialog");
    const options = Array.from((within(dialog).getByLabelText("执行空间") as HTMLSelectElement).options).map((o) => o.text);
    expect(options.some((o) => o.includes("平台组"))).toBe(true);
    expect(options.some((o) => o.includes("别人的组"))).toBe(false);
  });

  test("a new user can be put on the shared environment", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }], capabilities: {} }],
      "POST /users": { id: "u2", username: "carol" },
    });
    const dialog = await openSetting(/团队与成员/);
    await userEvent.type(within(dialog).getByLabelText("用户名"), "carol");
    await userEvent.type(within(dialog).getByLabelText("初始密码（至少 12 位）"), "Carol-password-123");
    expect(within(dialog).getByLabelText("个人空间的执行环境")).toHaveValue("w1");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建用户" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/users")?.body)
      .toEqual({ username: "carol", password: "Carol-password-123", worker_id: "w1" }));
  });

  test("an administrator gives an existing user's personal space the shared environment", async () => {
    const calls = start({
      "GET /users": [{ id: "u1", username: "fixture-owner" }, { id: "u2", username: "carol" }],
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }], capabilities: {} }],
      "POST /workers/w1/spaces": { ok: true },
    });
    const dialog = await openSetting(/团队与成员/);
    const carol = within(dialog).getByText("carol", { selector: "[data-user-environment] *" }).closest("[data-user-environment]") as HTMLElement;
    expect(carol).toHaveTextContent("未分配");
    await userEvent.click(within(carol).getByRole("button", { name: "分配共享环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/spaces")?.body).toEqual({ user_id: "u2" }));
  });

  test("sharing a Worker shows what sharing means and asks for confirmation", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "dedicated",
        spaces: [], capabilities: { isolation: "trusted-local" } }],
      "POST /workers/w1/share": { id: "w1", scope: "shared" },
    });
    const dialog = await openSetting(/执行环境/);
    await userEvent.click(within(dialog).getByRole("button", { name: "设为共享环境" }));
    expect(within(dialog).getByText(/能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围/)).toBeInTheDocument();
    expect(within(dialog).getByText(/只有边界所有者能打开原生 KiroCrew 入口/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/workers/w1/share")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "确认设为共享环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/share")?.body).toEqual({ confirm: true }));
  });

  const environments = {
    "GET /workers": [
      { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }, { team_id: "team-1" }], capabilities: { isolation: "trusted-local" } },
      { id: "w2", name: "w1", owner_id: "u1", team_id: null, enabled: true, scope: "spare", spaces: [],
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
        health: { reachable: true, version: "1.0.0", isolation: "trusted-local", crew_entry: true, crew_origin: "https://d1.cloudfront.net" } },
      { id: "w2", name: "w1", scope: "spare", enabled: true, owner: { id: "u1", username: "fixture-owner" }, team: null, spaces: [],
        url: "https://10.42.1.74:8943", active_runs: 0, health: { reachable: false, error: "Worker 未返回可核实回执" } },
    ],
  };

  test("the environment dialog shows each environment's health, load and the spaces it serves", async () => {
    start(environments);
    const dialog = await openSetting(/执行环境/);
    const shared = await within(dialog).findByRole("group", { name: "personal" });
    expect(shared).toHaveTextContent("可达 · v1.0.0");
    expect(shared).toHaveTextContent("原生入口 d1.cloudfront.net");
    expect(shared).toHaveTextContent("进行中会话 2");
    expect(within(shared).getByText("个人 · fixture-owner")).toBeInTheDocument();
    expect(within(shared).getByText("Team · 平台组")).toBeInTheDocument();
    const spare = within(dialog).getByRole("group", { name: "w1" });
    expect(spare).toHaveTextContent("不可达：Worker 未返回可核实回执");
  });

  test("a shared environment is given to a user and a space taken away from it, in the environment dialog", async () => {
    const calls = start({ ...environments, "POST /workers/w1/spaces": { ok: true }, "DELETE /workers/w1/spaces": { ok: true } });
    const dialog = await openSetting(/执行环境/);
    const shared = await within(dialog).findByRole("group", { name: "personal" });
    const picker = within(shared).getByLabelText("分配到");
    // Spaces it already serves are not offered again.
    expect(within(picker).queryByRole("option", { name: "Team · 平台组" })).toBeNull();
    expect(within(picker).queryByRole("option", { name: "个人 · fixture-owner" })).toBeNull();
    await userEvent.selectOptions(picker, "user:u2");
    await userEvent.click(within(shared).getByRole("button", { name: "分配" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/workers/w1/spaces")?.body)
      .toEqual({ user_id: "u2" }));
    await userEvent.click(within(shared).getByRole("button", { name: "移出 Team · 平台组" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE" && c.path === "/v1/workers/w1/spaces")?.body)
      .toEqual({ team_id: "team-1" }));
  });

  test("a spare environment is offered only to Teams without one", async () => {
    const calls = start({ ...environments, "POST /workers/w2/spaces": { ok: true } });
    const dialog = await openSetting(/执行环境/);
    const spare = await within(dialog).findByRole("group", { name: "w1" });
    const picker = within(spare).getByLabelText("分配到");
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual(["选择 Team", "Team · Web应用软件研发组"]);
    await userEvent.selectOptions(picker, "team:team-2");
    await userEvent.click(within(spare).getByRole("button", { name: "分配" }));
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
    const dialog = await openSetting(/执行环境/);
    const host = await within(dialog).findByRole("group", { name: "w1" });
    // On the platform host (trusted-local) a Team cannot have its own environment.
    expect(within(within(dialog).getByRole("group", { name: "personal" })).queryByRole("button", { name: "改回专属环境" })).toBeNull();
    await userEvent.click(within(host).getByRole("button", { name: "改回专属环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w2/dedicate")?.method).toBe("POST"));
  });

  const adminUsers = [
    { id: "u1", username: "fixture-owner", admin: true, active: true, created: 1, last_login: 1_790_000_000, teams: [], tokens: 2 },
    { id: "u2", username: "dev1", admin: false, active: true, created: 1, last_login: null,
      teams: [{ id: "team-1", name: "平台组", role: "member" }], tokens: 1 },
  ];

  test("an administrator manages users: deactivate, reset a password and revoke a token", async () => {
    const calls = start({ "GET /admin/users": adminUsers, "POST /users/u2/active": { id: "u2", active: false },
      "POST /users/u2/password": { ok: true },
      "GET /admin/users/u2/tokens": [{ id: "t1", label: "claude MCP", scopes: ["read", "execute"], expires: 1_900_000_000, last_used: null }],
      "DELETE /admin/users/u2/tokens/t1": { ok: true } });
    const dialog = await openSetting(/用户管理/);
    const row = await within(dialog).findByRole("group", { name: "dev1" });
    expect(row).toHaveTextContent("平台组（member）");
    expect(row).toHaveTextContent("从未登录");
    expect(within(dialog).getByRole("group", { name: "fixture-owner" })).toHaveTextContent("管理员");
    await userEvent.click(within(row).getByRole("button", { name: "停用" }));
    expect(calls.some((c) => c.path === "/v1/users/u2/active")).toBe(false);   // asks first
    await userEvent.click(within(row).getByRole("button", { name: "确认停用" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/users/u2/active")?.body).toEqual({ active: false }));
    await userEvent.click(within(row).getByRole("button", { name: "重置密码" }));
    await userEvent.type(within(row).getByLabelText("新密码"), "Another-password-456!");
    await userEvent.type(within(row).getByLabelText("确认新密码"), "Different-password-789!");
    await userEvent.click(within(row).getByRole("button", { name: "确认重置" }));
    expect(within(row).getByRole("alert")).toHaveTextContent("两次输入不一致");
    await userEvent.clear(within(row).getByLabelText("确认新密码"));
    await userEvent.type(within(row).getByLabelText("确认新密码"), "Another-password-456!");
    await userEvent.click(within(row).getByRole("button", { name: "确认重置" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/users/u2/password")?.body)
      .toEqual({ password: "Another-password-456!" }));
    await userEvent.click(within(row).getByRole("button", { name: "Token" }));
    await userEvent.click(await within(row).findByRole("button", { name: "吊销 claude MCP" }));
    await userEvent.click(within(row).getByRole("button", { name: "确认吊销" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE" && c.path === "/v1/admin/users/u2/tokens/t1")).toBeTruthy());
  });

  test("revoking a token asks first, and an administrator cannot lock themselves out from their own row", async () => {
    const calls = start({ "GET /admin/users": adminUsers,
      "GET /admin/users/u2/tokens": [{ id: "t1", label: "claude MCP", scopes: ["read"], expires: 1_900_000_000, last_used: null }],
      "DELETE /admin/users/u2/tokens/t1": { ok: true } });
    const dialog = await openSetting(/用户管理/);
    const own = await within(dialog).findByRole("group", { name: "fixture-owner" });
    expect(within(own).getByRole("button", { name: "停用" })).toBeDisabled();
    expect(within(own).getByRole("button", { name: "取消管理员" })).toBeDisabled();
    const row = within(dialog).getByRole("group", { name: "dev1" });
    await userEvent.click(within(row).getByRole("button", { name: "Token" }));
    await userEvent.click(await within(row).findByRole("button", { name: "吊销 claude MCP" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(within(row).getByRole("button", { name: "确认吊销" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/admin/users/u2/tokens/t1")).toBe(true));
  });

  test("a member sees neither user management nor anything that needs the admin scope", async () => {
    start({ "GET /me": { id: "u2", username: "dev1", admin: 0, scopes: ["read", "execute", "manage"] } });
    await userEvent.click(await screen.findByRole("button", { name: "设置" }));
    expect(screen.queryByRole("menuitem", { name: /用户管理/ })).toBeNull();
  });

  test("anyone changes their own password from the account menu", async () => {
    let attempts = 0;
    const calls = start({ "POST /me/password": () => (attempts++ === 0
      ? new Response(JSON.stringify({ error: "原密码不正确", code: "forbidden" }), { status: 403 }) : { ok: true }) });
    await userEvent.click(await screen.findByRole("button", { name: /账户/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "修改密码" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("原密码"), "wrong-password-0");
    await userEvent.type(within(dialog).getByLabelText("新密码"), "Another-password-456!");
    await userEvent.type(within(dialog).getByLabelText("确认新密码"), "Another-password-456!");
    await userEvent.click(within(dialog).getByRole("button", { name: "修改密码" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("原密码不正确");
    await userEvent.clear(within(dialog).getByLabelText("原密码"));
    await userEvent.type(within(dialog).getByLabelText("原密码"), "Test-password-123!");
    await userEvent.click(within(dialog).getByRole("button", { name: "修改密码" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.path === "/v1/me/password").at(-1)?.body)
      .toEqual({ current: "Test-password-123!", password: "Another-password-456!" });
  });

  test("tokens show when they were last used", async () => {
    start({ "GET /tokens": [{ id: "t1", label: "claude MCP", scopes: "[\"read\"]", expires: 1_900_000_000, last_used: null },
      { id: "t2", label: "Web login", scopes: "[\"read\"]", expires: 1_900_000_000, last_used: 1_790_000_000 }] });
    const dialog = await openSetting(/API 与 Coding Agent 接入/);
    expect(await within(dialog).findByText(/claude MCP/)).toBeInTheDocument();
    expect(dialog).toHaveTextContent("从未使用");
    expect(dialog).toHaveTextContent("最近使用");
  });

  test("an administrator can disable a Worker", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, capabilities: { isolation: "trusted-local" } }],
      "POST /workers/w1/enabled": { ok: true },
    });
    const dialog = await openSetting(/执行环境/);
    await userEvent.click(within(dialog).getByRole("button", { name: "停用" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/enabled")?.body).toEqual({ enabled: false }));
  });
});
