import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";
import { openSection, panel } from "@/test/pages";

function start(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], extra));
  render(<App />);
  return calls;
}

/** A Team section: an administrator's under 管理, anyone else's in 我的设置. */
const openTeams = (admin = true) => openSection(admin ? "管理" : "我的设置", "Team");
/** An administrator's 「新建 Team」 form. */
async function newTeam() {
  const dialog = await openTeams();
  await userEvent.click(within(dialog).getByRole("button", { name: "新建 Team" }));
  return dialog;
}

afterEach(() => vi.unstubAllGlobals());

describe("settings", () => {
  // PRD 2026-10-23 (admin & settings in dialogs): the page holds the list; creating and revoking happen in dialogs.
  async function newToken() {
    const section = await openSection("我的设置", "API 与 Coding Agent 接入");
    await userEvent.click(within(section).getByRole("button", { name: "新建 Token" }));
    return { section, dialog: await screen.findByRole("dialog", { name: "新建 Token" }) };
  }

  test("a new API token is made in a dialog and shown exactly once", async () => {
    const made = { id: "t1", label: "Claude Code", scopes: "[\"read\",\"execute\"]", expires: 0, last_used: null };
    let listed: unknown[] = [];
    const calls = start({ "GET /tokens": () => listed,
      "POST /tokens": () => { listed = [made]; return { id: "t1", token: "oct_secret_value", scopes: ["read", "execute"], expires: 0 }; } });
    const { section, dialog } = await newToken();
    expect(within(section).queryByLabelText("Token 名称")).toBeNull();   // nothing to type into on the page itself
    await userEvent.type(within(dialog).getByLabelText("Token 名称"), "Claude Code");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建 Token" }));
    const issued = await screen.findByRole("dialog", { name: "Token 已创建" });
    expect(within(issued).getByText("oct_secret_value")).toBeInTheDocument();
    expect(within(issued).getByText("只显示这一次。把它配置到对应客户端的私有环境变量中。")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/tokens")?.body).toEqual({ label: "Claude Code", scopes: ["read", "execute"], expires_in: "30d" });
    await userEvent.click(within(issued).getByRole("button", { name: "我已保存" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText("oct_secret_value")).toBeNull();   // never shown again
    const row = await within(section).findByText("Claude Code");
    expect(row.closest("[data-token-row]")).toHaveAttribute("data-fresh", "true");   // the new row is lit up
    expect(await screen.findByText("已创建 Token Claude Code")).toBeInTheDocument();
  });

  test("a token's lifetime is chosen: 7, 30 or 365 days, or never (D62)", async () => {
    const calls = start({ "GET /tokens": [], "POST /tokens": { id: "t1", token: "oct_secret_value", scopes: ["read", "execute"], expires: null } });
    const { dialog } = await newToken();
    const lifetime = within(dialog).getByLabelText("有效期");
    expect([...(lifetime as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(["7 天", "30 天", "365 天", "永不过期"]);
    await userEvent.selectOptions(lifetime, "never");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建 Token" }));
    expect(await screen.findByText("永不过期，可随时撤销。")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/tokens")?.body).toMatchObject({ expires_in: "never" });
  });

  test("revoking a token asks first, then removes it", async () => {
    let listed = [{ id: "t1", label: "claude MCP", scopes: "[\"read\"]", expires: null, last_used: null }];
    const calls = start({ "GET /tokens": () => listed, "DELETE /tokens/t1": () => { listed = []; return { ok: true }; } });
    const section = await openSection("我的设置", "API 与 Coding Agent 接入");
    await userEvent.click(await within(section).findByRole("button", { name: "Token 操作：claude MCP" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "撤销…" }));
    const confirm = await screen.findByRole("dialog", { name: "撤销 Token" });
    expect(confirm).toHaveTextContent("使用它的客户端会立即失去访问");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);   // nothing happens before the confirmation
    await userEvent.click(within(confirm).getByRole("button", { name: "撤销 Token" }));
    await waitFor(() => expect(within(section).queryByText("claude MCP")).toBeNull());
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/tokens/t1")).toBe(true);
    expect(await screen.findByText("已撤销 Token claude MCP")).toBeInTheDocument();
  });

  test("an administrator creates a Team with its owner and an execution environment", async () => {
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }], capabilities: { isolation: "trusted-local" } }],
      "POST /teams": { id: "team-2", name: "Web 组", role: "owner", environment: { worker_id: "w1", name: "personal", scope: "shared" } },
    });
    const dialog = await newTeam();
    await userEvent.type(within(dialog).getByLabelText("新 Team 名称"), "Web 组");
    expect(within(dialog).getByLabelText("执行环境")).toHaveValue("w1");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建 Team" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/teams")?.body)
      .toEqual({ name: "Web 组", owner: "fixture-owner", worker_id: "w1" }));
  });

  test("without any shared or spare environment a Team cannot be created", async () => {
    start();
    const dialog = await newTeam();
    expect(within(dialog).getByRole("button", { name: "创建 Team" })).toBeDisabled();
    expect(within(dialog).getByText(/先在主机上开通执行环境，或把一个 Worker 设为共享环境/)).toBeInTheDocument();
  });

  test("only administrators see Team creation", async () => {
    start({ "GET /me": { id: "u1", username: "fixture-owner", admin: 0, scopes: ["read", "execute", "manage"] } });
    const dialog = await openTeams(false);
    expect(within(dialog).queryByRole("button", { name: "新建 Team" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "创建 Team" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("Team 由平台管理员创建，并在创建时分配执行环境。")).toBeInTheDocument();
  });

  test("a Team without an execution environment can be given the shared one", async () => {
    const calls = start({
      "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [], environment: null }],
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }], capabilities: {} }],
      "POST /teams/team-1/environment": { id: "team-1" },
    });
    const dialog = await openTeams();
    expect(within(dialog).getByText("未分配执行环境")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "分配执行环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")?.body).toEqual({ worker_id: "w1" }));
  });

  test("a Team without an execution environment can be given a spare Worker host", async () => {
    const calls = start({
      "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [], environment: null }],
      "GET /workers": [
        { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared", spaces: [{ user_id: "u1" }], capabilities: {} },
        { id: "sp1", name: "w1", owner_id: "u1", team_id: null, enabled: true, scope: "spare", spaces: [], capabilities: {} },
      ],
      "POST /teams/team-1/environment": { id: "team-1" },
    });
    const dialog = await openTeams();
    const row = dialog.querySelector('[data-team-environment="team-1"]') as HTMLElement;
    await userEvent.selectOptions(within(row).getByLabelText("为 平台组 分配的执行环境"), "sp1");
    await userEvent.click(within(row).getByRole("button", { name: "分配执行环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")?.body).toEqual({ worker_id: "sp1" }));
  });

  test("a disabled environment is shown as disabled, not as missing", async () => {
    start({ "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [],
      environment: { worker_id: "w9", name: "w9", scope: "dedicated", enabled: false } }] });
    const dialog = await openTeams();
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

  const teamPage = {
    "GET /teams": [{ id: "team-1", name: "平台组", role: null, created: 1, members: [
      { id: "u2", username: "bob", role: "member" }, { id: "u3", username: "carol", role: "owner" }],
    environment: { worker_id: "w1", name: "personal", scope: "shared", enabled: true } }],
    "GET /users": [{ id: "u1", username: "fixture-owner" }, { id: "u2", username: "bob" }, { id: "u3", username: "carol" }],
    "GET /workers": [
      { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared", spaces: [{ team_id: "team-1" }], capabilities: {} },
      { id: "w2", name: "w2", owner_id: "u1", team_id: null, enabled: true, scope: "spare", spaces: [], capabilities: { isolation: "dedicated" } },
    ],
  };

  test("an administrator renames a Team they are not in and hosts its members", async () => {
    const calls = start({ ...teamPage, "PATCH /teams/team-1": { id: "team-1", name: "平台二组" },
      "POST /teams/team-1/members": { ok: true }, "DELETE /teams/team-1/members": { ok: true } });
    const dialog = await openTeams();
    const title = within(dialog).getByLabelText("Team 名称");
    await userEvent.clear(title);
    await userEvent.type(title, "平台二组");
    await userEvent.click(within(dialog).getByRole("button", { name: "改名" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/v1/teams/team-1")?.body).toEqual({ name: "平台二组" }));
    const bob = within(dialog).getByRole("group", { name: "bob" });
    await userEvent.click(within(bob).getByRole("button", { name: "设为 owner" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/teams/team-1/members")?.body)
      .toEqual({ username: "bob", role: "owner" }));
    await userEvent.click(within(bob).getByRole("button", { name: "移除" }));
    expect(calls.find((c) => c.method === "DELETE")).toBeUndefined();
    await userEvent.click(within(bob).getByRole("button", { name: "确认移除" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ username: "bob" }));
  });

  test("an administrator changes a Team's execution environment after confirming that old Sessions stay", async () => {
    const calls = start({ ...teamPage, "POST /teams/team-1/environment": { id: "team-1" } });
    const dialog = await openTeams();
    const row = dialog.querySelector('[data-team-environment="team-1"]') as HTMLElement;
    expect(row).toHaveTextContent("共享环境 · personal");
    expect(within(row).getByLabelText("为 平台组 分配的执行环境")).toHaveValue("w2");   // the current one is not offered
    await userEvent.click(within(row).getByRole("button", { name: "更换执行环境" }));
    expect(within(dialog).getByText(/旧会话不迁移/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "确认更换" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")?.body).toEqual({ worker_id: "w2" }));
  });

  test("an administrator archives a Team after confirming", async () => {
    const calls = start({ ...teamPage, "POST /teams/team-1/archive": { id: "team-1", archived: true } });
    const dialog = await openTeams();
    expect(within(dialog).getByRole("button", { name: "添加 / 更新" })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "归档 Team" }));
    expect(calls.find((c) => c.path === "/v1/teams/team-1/archive")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "确认归档" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/archive")?.body).toEqual({ archived: true }));
  });

  test("an archived Team shows as archived and offers restore instead of new members", async () => {
    const calls = start({ ...teamPage, "GET /teams": [{ ...teamPage["GET /teams"][0], archived: 1 }],
      "POST /teams/team-1/archive": { id: "team-1", archived: false } });
    const dialog = await openTeams();
    expect(within(dialog).getByText("已归档")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "添加 / 更新" })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "恢复 Team" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/archive")?.body).toEqual({ archived: false }));
  });

  test("a member leaves a Team and sees none of its management", async () => {
    const calls = start({
      "GET /me": { id: "u1", username: "fixture-owner", admin: 0, scopes: ["read", "execute", "manage"] },
      "GET /teams": [{ id: "team-1", name: "平台组", role: "member", created: 1, members: [
        { id: "u1", username: "fixture-owner", role: "member" }, { id: "u3", username: "carol", role: "owner" }],
      environment: { worker_id: "w1", name: "personal", scope: "shared", enabled: true } }],
      "DELETE /teams/team-1/members": { ok: true },
    });
    const dialog = await openTeams(false);
    for (const name of ["改名", "移除", "设为 owner", "归档 Team", "更换执行环境", "添加 / 更新"])
      expect(within(dialog).queryByRole("button", { name })).not.toBeInTheDocument();
    const self = within(dialog).getByRole("group", { name: "fixture-owner" });
    await userEvent.click(within(self).getByRole("button", { name: "退出 Team" }));
    await userEvent.click(within(self).getByRole("button", { name: "确认退出" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ username: "fixture-owner" }));
  });

  test("leaving one's only Team brings the console back to the personal space", async () => {
    let left = false;
    start({
      "GET /me": { id: "u1", username: "fixture-owner", admin: 0, scopes: ["read", "execute", "manage"] },
      "GET /teams": () => left ? [] : [{ id: "team-1", name: "平台组", role: "member", created: 1, members: [
        { id: "u1", username: "fixture-owner", role: "member" }, { id: "u3", username: "carol", role: "owner" }], environment: null }],
      "DELETE /teams/team-1/members": () => { left = true; return { ok: true }; },
    });
    await userEvent.click(await screen.findByRole("combobox", { name: "开发空间" }));
    await userEvent.click(await screen.findByRole("option", { name: "平台组 · Team" }));
    expect(screen.getByRole("combobox", { name: "开发空间" })).toHaveTextContent("平台组");
    const dialog = await openTeams(false);
    const self = within(dialog).getByRole("group", { name: "fixture-owner" });
    await userEvent.click(within(self).getByRole("button", { name: "退出 Team" }));
    await userEvent.click(within(self).getByRole("button", { name: "确认退出" }));
    await within(await panel("Team")).findByText("你还不在任何 Team 中。");
    await userEvent.click(screen.getByRole("button", { name: "返回会话" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "开发空间" })).toHaveTextContent("我的开发空间"));
  });

  test("archived Teams are marked in the space switcher and not offered for new Sessions", async () => {
    start({ "GET /teams": [
      { id: "team-1", name: "平台组", role: "owner", created: 1, members: [], environment: null },
      { id: "team-2", name: "旧组", role: "owner", created: 2, members: [], archived: 1, environment: null },
    ] });
    await userEvent.click(await screen.findByRole("combobox", { name: "开发空间" }));
    expect(await screen.findByRole("option", { name: "旧组 · Team · 已归档" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: /新建开发/ }));
    const dialog = await screen.findByRole("dialog");
    const options = Array.from((within(dialog).getByLabelText("执行空间") as HTMLSelectElement).options).map((o) => o.text);
    expect(options.some((o) => o.includes("旧组"))).toBe(false);
  });

  test("a member sees neither user management nor anything that needs the admin scope", async () => {
    start({ "GET /me": { id: "u2", username: "dev1", admin: 0, scopes: ["read", "execute", "manage"] } });
    expect(await screen.findByRole("button", { name: "我的设置" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "管理" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /账户/ }));
    expect(screen.queryByRole("menuitem", { name: "管理" })).toBeNull();
  });

  test("anyone changes their own password from the account menu", async () => {
    let attempts = 0;
    const calls = start({ "POST /me/password": () => (attempts++ === 0
      ? new Response(JSON.stringify({ error: "原密码不正确", code: "forbidden" }), { status: 403 }) : { ok: true }) });
    await userEvent.click(await screen.findByRole("button", { name: /账户/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "修改密码" }));
    const dialog = await panel("修改密码");
    await userEvent.type(within(dialog).getByLabelText("原密码"), "wrong-password-0");
    await userEvent.type(within(dialog).getByLabelText("新密码"), "Another-password-456!");
    await userEvent.type(within(dialog).getByLabelText("确认新密码"), "Another-password-456!");
    await userEvent.click(within(dialog).getByRole("button", { name: "修改密码" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("原密码不正确");
    await userEvent.clear(within(dialog).getByLabelText("原密码"));
    await userEvent.type(within(dialog).getByLabelText("原密码"), "Test-password-123!");
    await userEvent.click(within(dialog).getByRole("button", { name: "修改密码" }));
    await waitFor(() => expect(within(dialog).getByLabelText("原密码")).toHaveValue(""));   // done: the form is cleared
    expect(calls.filter((c) => c.path === "/v1/me/password").at(-1)?.body)
      .toEqual({ current: "Test-password-123!", password: "Another-password-456!" });
  });

  test("tokens show when they were last used", async () => {
    start({ "GET /tokens": [{ id: "t1", label: "claude MCP", scopes: "[\"read\"]", expires: null, last_used: null },
      { id: "t2", label: "Web login", scopes: "[\"read\"]", expires: 1_900_000_000, last_used: 1_790_000_000 }] });
    const dialog = await openSection("我的设置", "API 与 Coding Agent 接入");
    expect(await within(dialog).findByText(/claude MCP/)).toBeInTheDocument();
    expect(dialog).toHaveTextContent("从未使用");
    expect(dialog).toHaveTextContent("最近使用");
    expect(dialog).toHaveTextContent("永不过期");   // D62: expires null
  });
});
