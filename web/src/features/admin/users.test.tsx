import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";
import { openSection } from "@/test/pages";
import { formatTime } from "@/lib/labels";

const ADMIN_USERS = [
  { id: "u1", username: "fixture-owner", admin: true, active: true, created: 1, last_login: 1_790_000_000, teams: [], tokens: 2, running: 0 },
  { id: "u2", username: "dev1", admin: false, active: true, created: 1, last_login: null,
    teams: [{ id: "team-1", name: "平台组", role: "member" }], tokens: 1, running: 3 },
  { id: "u3", username: "ops", admin: true, active: true, created: 1, last_login: null, teams: [], tokens: 0, running: 0 },
  { id: "u4", username: "gone", admin: false, active: false, created: 1, last_login: null, teams: [], tokens: 0, running: 0 },
];
const WORKERS = [
  { id: "w1", name: "personal", owner_id: "u1", team_id: null, enabled: true, scope: "shared",
    spaces: [{ user_id: "u1" }, { user_id: "u2" }], capabilities: { isolation: "trusted-local" } },
  { id: "w3", name: "shared-2", owner_id: "u1", team_id: null, enabled: true, scope: "shared", spaces: [],
    capabilities: { isolation: "dedicated" } },
];
const TEAMS = [
  { id: "team-1", name: "平台组", role: "owner", created: 1, members: [] },
  { id: "team-2", name: "Web 组", role: null, created: 2, members: [] },
  { id: "team-3", name: "旧组", role: null, created: 3, members: [], archived: 1 },
];

function start(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], { "GET /admin/users": ADMIN_USERS, "GET /workers": WORKERS, "GET /teams": TEAMS,
    "GET /admin/environments": [], ...extra }));
  render(<App />);
  return calls;
}

async function openUsers() {
  const panel = await openSection("管理", "用户");
  await within(panel).findByRole("row", { name: "dev1" });
  return panel;
}

/** Opens a row's 「⋯」 menu. */
async function menu(panel: HTMLElement, user: string) {
  await userEvent.click(within(panel).getByRole("button", { name: `${user} 的操作` }));
  return screen.findByRole("menu");
}

const posted = (calls: { method: string; path: string; body?: unknown }[], path: string, method = "POST") =>
  calls.filter((c) => c.method === method && c.path === "/v1" + path).map((c) => c.body);

afterEach(() => vi.unstubAllGlobals());

describe("管理 › 用户", () => {
  test("one row per person: role, Teams, execution environment, last login and Sessions in progress", async () => {
    const panel = await (start(), openUsers());
    expect(within(panel).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["用户名", "角色", "所在 Team", "执行环境", "最近登录", "运行中的会话", "操作"]);
    const dev = within(panel).getByRole("row", { name: "dev1" });
    expect(within(dev).getAllByRole("cell").map((c) => c.textContent))
      .toEqual(["dev1", "成员", "平台组", "共享环境 · personal", "从未登录", "3", ""]);
    const own = within(panel).getByRole("row", { name: "fixture-owner" });
    expect(own).toHaveTextContent("（你）");
    expect(own).toHaveTextContent("管理员");
    expect(own).toHaveTextContent(formatTime(1_790_000_000, true));
    expect(within(panel).getByRole("row", { name: "ops" })).toHaveTextContent("未分配");
    expect(within(panel).getByRole("row", { name: "gone" })).toHaveTextContent("已停用");
  });

  test("the search keeps the people whose name or Team matches", async () => {
    const panel = await (start(), openUsers());
    await userEvent.type(within(panel).getByRole("searchbox", { name: "搜索用户" }), "平台");
    expect(within(panel).getAllByRole("row").slice(1).map((r) => r.getAttribute("aria-label"))).toEqual(["dev1"]);
    await userEvent.clear(within(panel).getByRole("searchbox", { name: "搜索用户" }));
    await userEvent.type(within(panel).getByRole("searchbox", { name: "搜索用户" }), "nobody");
    expect(within(panel).getByText("没有匹配的用户。")).toBeInTheDocument();
  });

  test("deactivating asks once; enabling and making someone an administrator take one step", async () => {
    const calls = start({ "POST /users/u2/active": { id: "u2", active: false }, "POST /users/u4/active": { id: "u4", active: true },
      "POST /users/u2/admin": { id: "u2", admin: true } });
    const panel = await openUsers();
    await userEvent.click(within(await menu(panel, "dev1")).getByRole("menuitem", { name: "停用" }));
    expect(posted(calls, "/users/u2/active")).toEqual([]);   // asks first
    expect(within(panel).getByRole("alert")).toHaveTextContent("其全部 Token 会立即失效");
    await userEvent.click(within(panel).getByRole("button", { name: "确认停用" }));
    await waitFor(() => expect(posted(calls, "/users/u2/active")).toEqual([{ active: false }]));
    await userEvent.click(within(await menu(panel, "gone")).getByRole("menuitem", { name: "启用" }));
    await waitFor(() => expect(posted(calls, "/users/u4/active")).toEqual([{ active: true }]));
    await userEvent.click(within(await menu(panel, "dev1")).getByRole("menuitem", { name: "设为管理员" }));
    await waitFor(() => expect(posted(calls, "/users/u2/admin")).toEqual([{ admin: true }]));
    expect(within(panel).queryByRole("alert")).toBeNull();
  });

  test("removing an administrator asks once; one's own row cannot lock one out", async () => {
    const calls = start({ "POST /users/u3/admin": { id: "u3", admin: false } });
    const panel = await openUsers();
    const own = await menu(panel, "fixture-owner");
    expect(within(own).getByRole("menuitem", { name: "停用" })).toHaveAttribute("aria-disabled", "true");
    expect(within(own).getByRole("menuitem", { name: "取消管理员" })).toHaveAttribute("aria-disabled", "true");
    await userEvent.keyboard("{Escape}");
    await userEvent.click(within(await menu(panel, "ops")).getByRole("menuitem", { name: "取消管理员" }));
    expect(posted(calls, "/users/u3/admin")).toEqual([]);
    await userEvent.click(within(panel).getByRole("button", { name: "确认取消管理员" }));
    await waitFor(() => expect(posted(calls, "/users/u3/admin")).toEqual([{ admin: false }]));
  });

  test("a password is reset from the row: both entries must match", async () => {
    const calls = start({ "POST /users/u2/password": { ok: true } });
    const panel = await openUsers();
    await userEvent.click(within(await menu(panel, "dev1")).getByRole("menuitem", { name: "重置密码" }));
    await userEvent.type(within(panel).getByLabelText("dev1 的新密码"), "Another-password-456!");
    await userEvent.type(within(panel).getByLabelText("确认新密码"), "Different-password-789!");
    await userEvent.click(within(panel).getByRole("button", { name: "确认重置" }));
    expect(within(panel).getByRole("alert")).toHaveTextContent("两次输入不一致");
    await userEvent.clear(within(panel).getByLabelText("确认新密码"));
    await userEvent.type(within(panel).getByLabelText("确认新密码"), "Another-password-456!");
    await userEvent.click(within(panel).getByRole("button", { name: "确认重置" }));
    await waitFor(() => expect(posted(calls, "/users/u2/password")).toEqual([{ password: "Another-password-456!" }]));
  });

  test("a person's tokens are listed from the row and revoking one asks first", async () => {
    const calls = start({
      "GET /admin/users/u2/tokens": [{ id: "t1", label: "claude MCP", scopes: ["read", "execute"], expires: 1_900_000_000, last_used: null }],
      "DELETE /admin/users/u2/tokens/t1": { ok: true } });
    const panel = await openUsers();
    await userEvent.click(within(await menu(panel, "dev1")).getByRole("menuitem", { name: "Token（1）" }));
    await userEvent.click(await within(panel).findByRole("button", { name: "吊销 claude MCP" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(within(panel).getByRole("button", { name: "确认吊销" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/admin/users/u2/tokens/t1")).toBe(true));
  });

  test("加入 Team is one step and offers the Teams the person is not in, never an archived one", async () => {
    const calls = start({ "POST /teams/team-2/members": { ok: true } });
    const panel = await openUsers();
    await userEvent.click(within(await menu(panel, "dev1")).getByRole("menuitem", { name: "加入 Team" }));
    const teams = (await screen.findAllByRole("menu")).at(-1)!;
    expect(within(teams).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Web 组"]);
    await userEvent.click(within(teams).getByRole("menuitem", { name: "Web 组" }));
    await waitFor(() => expect(posted(calls, "/teams/team-2/members")).toEqual([{ username: "dev1", role: "member" }]));
  });

  test("改执行环境 moves the personal space in one step; a person without one is simply given it", async () => {
    const calls = start({ "POST /workers/w3/spaces": { ok: true }, "DELETE /workers/w1/spaces": { ok: true } });
    const panel = await openUsers();
    await userEvent.click(within(await menu(panel, "dev1")).getByRole("menuitem", { name: "改执行环境" }));
    const environments = (await screen.findAllByRole("menu")).at(-1)!;
    expect(within(environments).getByRole("menuitem", { name: "共享环境 · personal" })).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(within(environments).getByRole("menuitem", { name: "共享环境 · shared-2" }));
    await waitFor(() => expect(posted(calls, "/workers/w1/spaces", "DELETE")).toEqual([{ user_id: "u2" }]));
    expect(posted(calls, "/workers/w3/spaces")).toEqual([{ user_id: "u2" }]);
    await userEvent.click(within(await menu(panel, "ops")).getByRole("menuitem", { name: "改执行环境" }));
    await userEvent.click(within((await screen.findAllByRole("menu")).at(-1)!).getByRole("menuitem", { name: "共享环境 · shared-2" }));
    await waitFor(() => expect(posted(calls, "/workers/w3/spaces")).toEqual([{ user_id: "u2" }, { user_id: "u3" }]));
    expect(posted(calls, "/workers/w1/spaces", "DELETE")).toHaveLength(1);
  });

  test("a deactivated person joins nothing until enabled", async () => {
    const panel = await (start(), openUsers());
    const items = within(await menu(panel, "gone"));
    expect(items.getByRole("menuitem", { name: "加入 Team" })).toHaveAttribute("aria-disabled", "true");
    expect(items.getByRole("menuitem", { name: "改执行环境" })).toHaveAttribute("aria-disabled", "true");
    expect(items.getByRole("menuitem", { name: "启用" })).not.toHaveAttribute("aria-disabled");
  });

  test("新建用户 asks for a username and an initial password; the personal space takes the shared environment", async () => {
    const calls = start({ "POST /users": { id: "u5", username: "carol" } });
    const panel = await openUsers();
    await userEvent.click(within(panel).getByRole("button", { name: "新建用户" }));
    const form = within(panel).getByRole("region", { name: "新建用户" });
    expect(within(form).getByRole("button", { name: "创建用户" })).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("用户名"), "carol");
    await userEvent.type(within(form).getByLabelText("初始密码（至少 12 位）"), "Carol-password-123");
    expect(within(form).getByLabelText("个人空间的执行环境")).toHaveValue("w1");
    await userEvent.click(within(form).getByRole("button", { name: "创建用户" }));
    await waitFor(() => expect(posted(calls, "/users")).toEqual([{ username: "carol", password: "Carol-password-123", worker_id: "w1" }]));
    await waitFor(() => expect(within(panel).queryByRole("region", { name: "新建用户" })).toBeNull());
  });

  test("the Team section no longer creates users", async () => {
    start();
    await openUsers();
    const nav = screen.getByRole("navigation", { name: "管理" });
    await userEvent.click(within(nav).getByRole("link", { name: "Team" }));
    const team = (await screen.findByRole("heading", { level: 2, name: "Team" })).closest("section") as HTMLElement;
    expect(within(team).queryByRole("button", { name: /用户/ })).toBeNull();
  });
});
