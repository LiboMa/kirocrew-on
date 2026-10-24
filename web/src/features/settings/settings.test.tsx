import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";
import { openSection, openSessions, panel } from "@/test/pages";

function start(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], extra));
  render(<App />);
  return calls;
}

/** A Team section: an administrator's under 管理, anyone else's in 我的设置. */
const openTeams = (admin = true) => openSection(admin ? "管理" : "我的设置", "Team");
/** An administrator's 「新建 Team」 dialog, opened from the page. */
async function newTeam() {
  const section = await openTeams();
  await userEvent.click(within(section).getByRole("button", { name: "新建 Team" }));
  return { section, dialog: await screen.findByRole("dialog", { name: "新建 Team" }) };
}
/** Opens the dialog behind a button on the Team page (「改名：平台组」, 「移出：bob」) and returns it. */
async function teamDialog(section: HTMLElement, button: string, title: string) {
  await userEvent.click(within(section).getByRole("button", { name: button }));
  return screen.findByRole("dialog", { name: title });
}
/** Opens the dialog behind an item of the Team's 「⋯」 menu. */
async function teamMenu(section: HTMLElement, team: string, item: string, title: string) {
  await userEvent.click(within(section).getByRole("button", { name: "Team 操作：" + team }));
  await userEvent.click(await screen.findByRole("menuitem", { name: item + "…" }));
  return screen.findByRole("dialog", { name: title });
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
    await userEvent.click(await within(section).findByRole("button", { name: "撤销 Token：claude MCP" }));
    const confirm = await screen.findByRole("dialog", { name: "撤销 Token" });
    expect(confirm).toHaveTextContent("使用它的客户端会立即失去访问");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);   // nothing happens before the confirmation
    await userEvent.click(within(confirm).getByRole("button", { name: "撤销 Token" }));
    await waitFor(() => expect(within(section).queryByText("claude MCP")).toBeNull());
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/tokens/t1")).toBe(true);
    expect(await screen.findByText("已撤销 Token claude MCP")).toBeInTheDocument();
  });

  // Owner 2026-10-24: a token that exists can be regenerated — the same entry, a new value shown once, the old one void.
  test("regenerating a token keeps its expiry by default and shows the new value once", async () => {
    const listed = [{ id: "t1", label: "claude MCP", scopes: "[\"read\",\"execute\"]", expires: 1_900_000_000, last_used: 1_790_000_000 }];
    const calls = start({ "GET /tokens": () => listed,
      "POST /tokens/t1/regenerate": { id: "t1", token: "oct_new_value", label: "claude MCP", scopes: ["read", "execute"], expires: 1_900_000_000 } });
    const section = await openSection("我的设置", "API 与 Coding Agent 接入");
    await userEvent.click(await within(section).findByRole("button", { name: "重新生成 Token：claude MCP" }));
    const dialog = await screen.findByRole("dialog", { name: "重新生成 Token" });
    expect(dialog).toHaveTextContent("旧值立即失效");
    const lifetime = within(dialog).getByLabelText("有效期") as HTMLSelectElement;
    expect(lifetime.value).toBe("");
    expect(lifetime.options[0].textContent).toMatch(/^保持原到期时间/);
    expect(calls.some((c) => c.path === "/v1/tokens/t1/regenerate")).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "重新生成" }));
    const issued = await screen.findByRole("dialog", { name: "Token 已重新生成" });
    expect(within(issued).getByText("oct_new_value")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/tokens/t1/regenerate")?.body).toEqual({});
    await userEvent.click(within(issued).getByRole("button", { name: "我已保存" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText("oct_new_value")).toBeNull();
    expect((await within(section).findByText("claude MCP")).closest("[data-token-row]")).toHaveAttribute("data-fresh", "true");
    expect(await screen.findByText("已重新生成 Token claude MCP")).toBeInTheDocument();
  });

  test("a regenerated token may be given a new lifetime; one with admin lasts at most 30 days", async () => {
    const calls = start({ "GET /tokens": [{ id: "t1", label: "ops", scopes: "[\"read\",\"admin\"]", expires: 1_900_000_000, last_used: null }],
      "POST /tokens/t1/regenerate": { id: "t1", token: "oct_new_value", label: "ops", scopes: ["read", "admin"], expires: 1_800_000_000 } });
    const section = await openSection("我的设置", "API 与 Coding Agent 接入");
    await userEvent.click(await within(section).findByRole("button", { name: "重新生成 Token：ops" }));
    const dialog = await screen.findByRole("dialog", { name: "重新生成 Token" });
    const lifetime = within(dialog).getByLabelText("有效期") as HTMLSelectElement;
    expect([...lifetime.options].slice(1).map((o) => o.value)).toEqual(["7d", "30d"]);
    await userEvent.selectOptions(lifetime, "7d");
    await userEvent.click(within(dialog).getByRole("button", { name: "重新生成" }));
    await screen.findByRole("dialog", { name: "Token 已重新生成" });
    expect(calls.find((c) => c.path === "/v1/tokens/t1/regenerate")?.body).toEqual({ expires_in: "7d" });
  });

  test("the token this login uses is marked and offers neither regenerating nor revoking", async () => {
    start({ "GET /tokens": [{ id: "t0", label: "Web login", scopes: "[\"read\"]", expires: 1_900_000_000, last_used: 1_790_000_000, current: true },
      { id: "t1", label: "claude MCP", scopes: "[\"read\"]", expires: null, last_used: null }] });
    const section = await openSection("我的设置", "API 与 Coding Agent 接入");
    const row = (await within(section).findByText("Web login")).closest("[data-token-row]") as HTMLElement;
    expect(row).toHaveTextContent("当前登录");
    expect(within(row).queryByRole("button")).toBeNull();
    expect(within(section).getByRole("button", { name: "重新生成 Token：claude MCP" })).toBeInTheDocument();
  });

  test("the token list is searched by name", async () => {
    start({ "GET /tokens": [{ id: "t1", label: "claude MCP", scopes: "[\"read\"]", expires: null, last_used: null },
      { id: "t2", label: "Kiro CLI", scopes: "[\"read\"]", expires: null, last_used: null }] });
    const section = await openSection("我的设置", "API 与 Coding Agent 接入");
    await within(section).findByText("Kiro CLI");
    await userEvent.type(within(section).getByRole("searchbox", { name: "搜索 Token 名称" }), "kiro");
    expect(within(section).queryByText("claude MCP")).toBeNull();
    expect(within(section).getByText("Kiro CLI")).toBeInTheDocument();
    await userEvent.clear(within(section).getByRole("searchbox", { name: "搜索 Token 名称" }));
    await userEvent.type(within(section).getByRole("searchbox", { name: "搜索 Token 名称" }), "nothing");
    expect(section).toHaveTextContent("没有名称含「nothing」的 Token。");
  });

  // PRD 2026-10-23 (我的设置 › Team): the page holds the Team and its members; every change happens in a dialog.
  test("an administrator creates a Team with its owner and an execution environment", async () => {
    const made = { id: "team-2", name: "Web 组", role: "owner", created: 2, members: [{ id: "u1", username: "fixture-owner", role: "owner" }],
      environment: { worker_id: "w1", name: "personal", scope: "shared", enabled: true } };
    let listed: unknown[] = [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [] }];
    const calls = start({
      "GET /workers": [{ id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
        spaces: [{ user_id: "u1" }], capabilities: { isolation: "trusted-local" } }],
      "GET /teams": () => listed,
      "POST /teams": () => { listed = [...listed, made]; return made; },
    });
    const { section, dialog } = await newTeam();
    expect(within(section).queryByRole("textbox")).toBeNull();   // nothing to type into on the page itself
    const name = within(dialog).getByLabelText("Team 名称");
    expect(name).toHaveFocus();
    await userEvent.type(name, "Web 组");
    expect(within(dialog).getByLabelText("Team owner")).toHaveValue("fixture-owner");
    expect(within(dialog).getByLabelText("执行环境")).toHaveValue("w1");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建 Team" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/teams")?.body).toEqual({ name: "Web 组", owner: "fixture-owner", worker_id: "w1" });
    expect(await screen.findByText("已创建 Team Web 组")).toBeInTheDocument();
    expect(section.querySelector('[data-team="team-2"]')).toHaveAttribute("data-fresh", "true");   // the new Team is shown, lit up
  });

  test("without any shared or spare environment a Team cannot be created", async () => {
    start();
    const { dialog } = await newTeam();
    expect(within(dialog).queryByRole("button", { name: "创建 Team" })).not.toBeInTheDocument();
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
    const section = await openTeams();
    expect(within(section).getByText("未分配执行环境")).toBeInTheDocument();
    const dialog = await teamDialog(section, "分配执行环境：平台组", "分配执行环境");
    expect(within(dialog).getByLabelText("执行环境")).toHaveValue("w1");
    await userEvent.click(within(dialog).getByRole("button", { name: "分配执行环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")?.body).toEqual({ worker_id: "w1" }));
    expect(await screen.findByText("已为 平台组 分配执行环境")).toBeInTheDocument();
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
    const section = await openTeams();
    const dialog = await teamDialog(section, "分配执行环境：平台组", "分配执行环境");
    const picker = within(dialog).getByLabelText("执行环境") as HTMLSelectElement;
    expect([...picker.options].map((o) => o.textContent)).toEqual(["共享环境 · personal", "空闲专属环境 · w1"]);   // shared first
    await userEvent.selectOptions(picker, "sp1");
    await userEvent.click(within(dialog).getByRole("button", { name: "分配执行环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")?.body).toEqual({ worker_id: "sp1" }));
  });

  test("a disabled environment is shown as disabled, not as missing", async () => {
    start({ "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [],
      environment: { worker_id: "w9", name: "w9", scope: "dedicated", enabled: false } }] });
    const section = await openTeams();
    expect(section.querySelector('[data-team-environment="team-1"]')).toHaveTextContent("执行环境已停用 · w9");
    expect(within(section).queryByRole("button", { name: /执行环境：平台组$/ })).not.toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Team 操作：平台组" }));
    expect((await screen.findAllByRole("menuitem")).map((m) => m.textContent)).toEqual(["归档…"]);   // no other environment to move to
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
    const section = await openTeams();
    expect(within(section).queryByRole("textbox")).toBeNull();
    expect(within(section).queryByRole("combobox")).toBeNull();   // one Team: no switcher, and nothing inline to edit

    const rename = await teamDialog(section, "改名：平台组", "给 Team 改名");
    const title = within(rename).getByLabelText("Team 名称");
    expect(title).toHaveValue("平台组");
    await userEvent.clear(title);
    await userEvent.type(title, "平台二组");
    await userEvent.click(within(rename).getByRole("button", { name: "改名" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.path === "/v1/teams/team-1")?.body).toEqual({ name: "平台二组" }));
    expect(await screen.findByText("已将 Team 改名为 平台二组")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    expect(within(section).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["成员", "角色", "操作"]);
    const role = await teamDialog(section, "改角色：bob", "改角色");
    expect(within(role).getByLabelText("角色")).toHaveValue("owner");   // the other role, as the old toggle did
    await userEvent.click(within(role).getByRole("button", { name: "改为 Owner" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/teams/team-1/members")?.body)
      .toEqual({ username: "bob", role: "owner" }));
    expect(await screen.findByText("已将 bob 改为 Owner")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const remove = await teamDialog(section, "移出：bob", "移出成员");
    expect(remove).toHaveTextContent("其创建的 Team 会话保留在 Team 中");
    expect(calls.find((c) => c.method === "DELETE")).toBeUndefined();   // nothing happens before the confirmation
    await userEvent.click(within(remove).getByRole("button", { name: "移出" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ username: "bob" }));
    expect(await screen.findByText("已移出 bob")).toBeInTheDocument();
  });

  test("an existing user is added in a dialog and the new member's row lights up", async () => {
    let members = [{ id: "u2", username: "bob", role: "member" }, { id: "u3", username: "carol", role: "owner" }];
    const calls = start({ ...teamPage,
      "GET /teams": () => [{ ...teamPage["GET /teams"][0], members }],
      "POST /teams/team-1/members": (body: unknown) => {
        const { username, role } = body as { username: string; role: string };
        members = [...members, { id: "u1", username, role }];
        return { ok: true };
      } });
    const section = await openTeams();
    const dialog = await teamDialog(section, "添加成员：平台组", "添加成员");
    const user = within(dialog).getByLabelText("用户") as HTMLSelectElement;
    expect(user).toHaveFocus();
    expect([...user.options].map((o) => o.value)).toEqual(["fixture-owner"]);   // only who is not in the Team yet
    expect(within(dialog).getByLabelText("角色")).toHaveValue("member");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加成员" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/teams/team-1/members")?.body).toEqual({ username: "fixture-owner", role: "member" });
    expect(await screen.findByText("已添加成员 fixture-owner")).toBeInTheDocument();
    const row = within(section).getByText("fixture-owner").closest("[data-member-row]");
    expect(row).toHaveAttribute("data-fresh", "true");
    expect(within(section).getByText("共 3 位成员")).toBeInTheDocument();
  });

  test("an administrator changes a Team's execution environment after confirming that old Sessions stay", async () => {
    const calls = start({ ...teamPage, "POST /teams/team-1/environment": { id: "team-1" } });
    const section = await openTeams();
    expect(section.querySelector('[data-team-environment="team-1"]')).toHaveTextContent("共享环境 · personal");
    const dialog = await teamMenu(section, "平台组", "更换执行环境", "更换执行环境");
    expect(dialog).toHaveTextContent("旧会话不迁移");
    const picker = within(dialog).getByLabelText("执行环境") as HTMLSelectElement;
    expect([...picker.options].map((o) => o.value)).toEqual(["w2"]);   // the current one is not offered
    expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "更换执行环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/environment")?.body).toEqual({ worker_id: "w2" }));
    expect(await screen.findByText("已为 平台组 更换执行环境")).toBeInTheDocument();
  });

  test("an administrator archives a Team after confirming", async () => {
    const calls = start({ ...teamPage, "POST /teams/team-1/archive": { id: "team-1", archived: true } });
    const section = await openTeams();
    expect(within(section).getByRole("button", { name: "添加成员：平台组" })).toBeInTheDocument();
    const confirm = await teamMenu(section, "平台组", "归档", "归档 Team");
    expect(confirm).toHaveTextContent("归档后不能提交新会话或添加成员");
    expect(calls.find((c) => c.path === "/v1/teams/team-1/archive")).toBeUndefined();
    await userEvent.click(within(confirm).getByRole("button", { name: "归档 Team" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/archive")?.body).toEqual({ archived: true }));
    expect(await screen.findByText("已归档 Team 平台组")).toBeInTheDocument();
  });

  test("an archived Team shows as archived and offers restore instead of new members", async () => {
    const calls = start({ ...teamPage, "GET /teams": [{ ...teamPage["GET /teams"][0], archived: 1 }],
      "POST /teams/team-1/archive": { id: "team-1", archived: false } });
    const section = await openTeams();
    expect(within(section).getByText("已归档")).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "添加成员：平台组" })).not.toBeInTheDocument();
    const confirm = await teamDialog(section, "恢复：平台组", "恢复 Team");
    expect(calls.find((c) => c.path === "/v1/teams/team-1/archive")).toBeUndefined();
    await userEvent.click(within(confirm).getByRole("button", { name: "恢复 Team" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/teams/team-1/archive")?.body).toEqual({ archived: false }));
    expect(await screen.findByText("已恢复 Team 平台组")).toBeInTheDocument();
  });

  test("a member leaves a Team and sees none of its management", async () => {
    const calls = start({
      "GET /me": { id: "u1", username: "fixture-owner", admin: 0, scopes: ["read", "execute", "manage"] },
      "GET /teams": [{ id: "team-1", name: "平台组", role: "member", created: 1, members: [
        { id: "u1", username: "fixture-owner", role: "member" }, { id: "u3", username: "carol", role: "owner" }],
      environment: { worker_id: "w1", name: "personal", scope: "shared", enabled: true } }],
      "DELETE /teams/team-1/members": { ok: true },
    });
    const section = await openTeams(false);
    for (const name of [/^改名/, /^移出/, /^改角色/, /^归档/, /^恢复/, /执行环境：/, /^添加成员/, /^Team 操作/])
      expect(within(section).queryByRole("button", { name })).not.toBeInTheDocument();
    const confirm = await teamDialog(section, "退出 Team：平台组", "退出 Team");
    expect(confirm).toHaveTextContent("唯一的 owner 不能退出");
    expect(calls.find((c) => c.method === "DELETE")).toBeUndefined();
    await userEvent.click(within(confirm).getByRole("button", { name: "退出 Team" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ username: "fixture-owner" }));
    expect(await screen.findByText("已退出 Team 平台组")).toBeInTheDocument();
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
    const section = await openTeams(false);
    const confirm = await teamDialog(section, "退出 Team：平台组", "退出 Team");
    await userEvent.click(within(confirm).getByRole("button", { name: "退出 Team" }));
    await within(await panel("Team")).findByText("你还不在任何 Team 中。");
    await openSessions();
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
    expect(await screen.findByRole("navigation", { name: "我的设置" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "管理" })).toBeNull();
    expect(screen.queryByRole("link", { name: "用户" })).toBeNull();
  });

  test("anyone changes their own password from the account menu", async () => {
    let attempts = 0;
    const calls = start({ "POST /me/password": () => (attempts++ === 0
      ? new Response(JSON.stringify({ error: "原密码不正确", code: "forbidden" }), { status: 403 }) : { ok: true }) });
    await userEvent.click(await screen.findByRole("button", { name: /账户/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "修改密码" }));
    const section = await panel("修改密码");
    expect(within(section).queryByLabelText("原密码")).toBeNull();   // the page explains; the dialog holds the fields
    await userEvent.click(within(section).getByRole("button", { name: "修改密码" }));
    const dialog = await screen.findByRole("dialog", { name: "修改密码" });
    expect(within(dialog).getByLabelText("原密码")).toHaveFocus();
    await userEvent.type(within(dialog).getByLabelText("原密码"), "wrong-password-0");
    await userEvent.type(within(dialog).getByLabelText("新密码"), "Another-password-456!");
    await userEvent.type(within(dialog).getByLabelText("确认新密码"), "Another-password-456!");
    await userEvent.click(within(dialog).getByRole("button", { name: "修改密码" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("原密码不正确");   // the dialog stays open with the reason
    await userEvent.clear(within(dialog).getByLabelText("原密码"));
    await userEvent.type(within(dialog).getByLabelText("原密码"), "Test-password-123!");
    await userEvent.click(within(dialog).getByRole("button", { name: "修改密码" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());   // done: the dialog closes
    expect(await screen.findByText("已修改密码")).toBeInTheDocument();
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
