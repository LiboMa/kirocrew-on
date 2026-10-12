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
    expect(calls.find((c) => c.path === "/v1/workers/w1/share")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "确认设为共享环境" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workers/w1/share")?.body).toEqual({ confirm: true }));
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
