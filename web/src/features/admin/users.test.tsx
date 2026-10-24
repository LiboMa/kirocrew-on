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
  await userEvent.click(within(panel).getByRole("button", { name: "用户操作：" + user }));
  return screen.findByRole("menu");
}

/** Picks ``item`` from a row's 「⋯」 menu and returns the dialog it opens, titled ``title``. */
async function fromMenu(panel: HTMLElement, user: string, item: string, title: string) {
  await userEvent.click(within(await menu(panel, user)).getByRole("menuitem", { name: item + "…" }));
  return screen.findByRole("dialog", { name: title });
}

const posted = (calls: { method: string; path: string; body?: unknown }[], path: string, method = "POST") =>
  calls.filter((c) => c.method === method && c.path === "/v1" + path).map((c) => c.body);

const refusal = (status: number, code: string, error: string) =>
  new Response(JSON.stringify({ error, code }), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

// PRD 2026-10-23 (管理 / 我的设置 in dialogs): the page holds the list; every change happens in a dialog.
describe("管理 › 用户", () => {
  test("one row per person: status, Teams, execution environment, last login and Sessions in progress", async () => {
    const panel = await (start(), openUsers());
    expect(within(panel).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["用户名", "状态", "所在 Team", "执行环境", "最近登录", "进行中的会话", "操作"]);
    const dev = within(panel).getByRole("row", { name: "dev1" });
    expect(within(dev).getAllByRole("cell").slice(0, -1).map((c) => c.textContent))
      .toEqual(["dev1", "有效", "平台组", "共享环境 · personal", "从未登录", "3"]);
    const own = within(panel).getByRole("row", { name: "fixture-owner" });
    expect(own).toHaveTextContent("（你）");
    expect(own).toHaveTextContent("管理员");
    expect(own).toHaveTextContent(formatTime(1_790_000_000, true));
    expect(within(panel).getByRole("row", { name: "ops" })).toHaveTextContent("未分配");
    expect(within(panel).getByRole("row", { name: "gone" })).toHaveTextContent("已停用");
    expect(panel).toHaveTextContent("3 位有效 · 共 4 位");
    // nothing to type into, tick or pick on the page itself: the search finds, it does not edit
    expect(within(panel).queryAllByRole("textbox")).toEqual([]);
    expect(within(panel).queryAllByRole("combobox")).toEqual([]);
    expect(within(panel).queryAllByRole("checkbox")).toEqual([]);
    expect(within(panel).getAllByRole("searchbox")).toHaveLength(1);
  });

  test("the search keeps the people whose name or Team matches", async () => {
    const panel = await (start(), openUsers());
    const search = within(panel).getByRole("searchbox", { name: "搜索用户名或 Team" });
    await userEvent.type(search, "平台");
    expect(within(panel).getAllByRole("row").slice(1).map((r) => r.getAttribute("aria-label"))).toEqual(["dev1"]);
    await userEvent.clear(search);
    await userEvent.type(search, "nobody");
    expect(panel).toHaveTextContent("没有用户名或 Team 含「nobody」的用户。");
  });

  test("新建用户 happens in a dialog; the new row lights up and a toast names it", async () => {
    let listed = ADMIN_USERS;
    const calls = start({ "GET /admin/users": () => listed,
      "POST /users": () => {
        listed = [...ADMIN_USERS, { id: "u5", username: "carol", admin: false, active: true, created: 2, last_login: null, teams: [], tokens: 0, running: 0 }];
        return { id: "u5", username: "carol" };
      } });
    const panel = await openUsers();
    await userEvent.click(within(panel).getByRole("button", { name: "新建用户" }));
    const dialog = await screen.findByRole("dialog", { name: "新建用户" });
    expect(within(panel).queryByLabelText("用户名")).toBeNull();   // the form is not on the page
    expect(within(dialog).getByLabelText("用户名")).toHaveFocus();
    await userEvent.click(within(dialog).getByRole("button", { name: "创建用户" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("请填写用户名");
    await userEvent.type(within(dialog).getByLabelText("用户名"), "carol");
    await userEvent.type(within(dialog).getByLabelText("初始密码（至少 12 位）"), "short");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建用户" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("初始密码至少 12 位");
    expect(posted(calls, "/users")).toEqual([]);
    await userEvent.clear(within(dialog).getByLabelText("初始密码（至少 12 位）"));
    await userEvent.type(within(dialog).getByLabelText("初始密码（至少 12 位）"), "Carol-password-123");
    expect(within(dialog).getByLabelText("个人空间的执行环境")).toHaveValue("w1");   // the first shared environment
    await userEvent.click(within(dialog).getByRole("button", { name: "创建用户" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(calls, "/users")).toEqual([{ username: "carol", password: "Carol-password-123", worker_id: "w1" }]);
    expect(await within(panel).findByRole("row", { name: "carol" })).toHaveAttribute("data-fresh", "true");
    expect(await screen.findByText("已创建用户 carol")).toBeInTheDocument();
  });

  test("a refusal from the server stays in the dialog", async () => {
    const calls = start({ "POST /users": refusal(409, "conflict", "用户名已存在") });
    const panel = await openUsers();
    await userEvent.click(within(panel).getByRole("button", { name: "新建用户" }));
    const dialog = await screen.findByRole("dialog", { name: "新建用户" });
    await userEvent.type(within(dialog).getByLabelText("用户名"), "dev1");
    await userEvent.type(within(dialog).getByLabelText("初始密码（至少 12 位）"), "Dev1-password-123");
    await userEvent.selectOptions(within(dialog).getByLabelText("个人空间的执行环境"), "暂不分配");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建用户" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("用户名已存在");
    expect(screen.getByRole("dialog", { name: "新建用户" })).toBeInTheDocument();
    expect(posted(calls, "/users")).toEqual([{ username: "dev1", password: "Dev1-password-123" }]);   // 暂不分配
  });

  test("重设密码 happens in a dialog: at least 12 characters, both entries must match", async () => {
    const calls = start({ "POST /users/u2/password": { ok: true } });
    const panel = await openUsers();
    await userEvent.click(within(panel).getByRole("button", { name: "重设密码：dev1" }));
    const dialog = await screen.findByRole("dialog", { name: "重设密码" });
    expect(dialog).toHaveTextContent("dev1 的全部 Token 立即失效");
    expect(within(dialog).getByLabelText("新密码（至少 12 位）")).toHaveFocus();
    await userEvent.type(within(dialog).getByLabelText("新密码（至少 12 位）"), "short");
    await userEvent.click(within(dialog).getByRole("button", { name: "重设密码" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("新密码至少 12 位");
    await userEvent.clear(within(dialog).getByLabelText("新密码（至少 12 位）"));
    await userEvent.type(within(dialog).getByLabelText("新密码（至少 12 位）"), "Another-password-456!");
    await userEvent.type(within(dialog).getByLabelText("确认新密码"), "Different-password-789!");
    await userEvent.click(within(dialog).getByRole("button", { name: "重设密码" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("两次输入不一致");
    expect(posted(calls, "/users/u2/password")).toEqual([]);
    await userEvent.clear(within(dialog).getByLabelText("确认新密码"));
    await userEvent.type(within(dialog).getByLabelText("确认新密码"), "Another-password-456!");
    await userEvent.click(within(dialog).getByRole("button", { name: "重设密码" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(calls, "/users/u2/password")).toEqual([{ password: "Another-password-456!" }]);
    expect(within(panel).getByRole("row", { name: "dev1" })).toHaveAttribute("data-fresh", "true");
    expect(await screen.findByText("已重设 dev1 的密码")).toBeInTheDocument();
  });

  test("停用 and 启用 ask first, say what follows, and do nothing before the confirmation", async () => {
    const calls = start({ "POST /users/u2/active": { id: "u2", active: false }, "POST /users/u4/active": { id: "u4", active: true } });
    const panel = await openUsers();
    let dialog = await fromMenu(panel, "dev1", "停用", "停用用户");
    expect(dialog).toHaveTextContent("dev1 立即不能登录，全部 Token 失效");
    expect(dialog).toHaveTextContent("进行中的 3 个会话照常执行");
    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(calls, "/users/u2/active")).toEqual([]);   // cancelled: nothing happened
    dialog = await fromMenu(panel, "dev1", "停用", "停用用户");
    await userEvent.click(within(dialog).getByRole("button", { name: "停用用户" }));
    await waitFor(() => expect(posted(calls, "/users/u2/active")).toEqual([{ active: false }]));
    expect(await screen.findByText("已停用用户 dev1")).toBeInTheDocument();

    dialog = await fromMenu(panel, "gone", "启用", "启用用户");
    expect(dialog).toHaveTextContent("停用时失效的 Token 不会恢复");
    expect(posted(calls, "/users/u4/active")).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "启用用户" }));
    await waitFor(() => expect(posted(calls, "/users/u4/active")).toEqual([{ active: true }]));
    expect(await screen.findByText("已启用用户 gone")).toBeInTheDocument();
  });

  test("the platform's refusal to deactivate a Team's only owner is shown in the dialog", async () => {
    start({ "POST /users/u2/active": refusal(409, "last_owner", "dev1 是 Team「平台组」唯一的 owner，请先指定另一位 owner") });
    const panel = await openUsers();
    const dialog = await fromMenu(panel, "dev1", "停用", "停用用户");
    await userEvent.click(within(dialog).getByRole("button", { name: "停用用户" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("唯一的 owner");
    expect(screen.getByRole("dialog", { name: "停用用户" })).toBeInTheDocument();
  });

  test("设为管理员 and 取消管理员 ask first; one's own row cannot lock one out", async () => {
    const calls = start({ "POST /users/u2/admin": { id: "u2", admin: true }, "POST /users/u3/admin": { id: "u3", admin: false } });
    const panel = await openUsers();
    const own = await menu(panel, "fixture-owner");
    expect(within(own).getByRole("menuitem", { name: "停用…" })).toHaveAttribute("aria-disabled", "true");
    expect(within(own).getByRole("menuitem", { name: "取消管理员…" })).toHaveAttribute("aria-disabled", "true");
    await userEvent.keyboard("{Escape}");

    let dialog = await fromMenu(panel, "dev1", "设为管理员", "设为管理员");
    expect(dialog).toHaveTextContent("重新登录后生效");
    expect(posted(calls, "/users/u2/admin")).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "设为管理员" }));
    await waitFor(() => expect(posted(calls, "/users/u2/admin")).toEqual([{ admin: true }]));
    expect(await screen.findByText("已把 dev1 设为管理员")).toBeInTheDocument();

    dialog = await fromMenu(panel, "ops", "取消管理员", "取消管理员");
    expect(dialog).toHaveTextContent("只能使用自己的空间与所在 Team");
    expect(posted(calls, "/users/u3/admin")).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "取消管理员" }));
    await waitFor(() => expect(posted(calls, "/users/u3/admin")).toEqual([{ admin: false }]));
    expect(await screen.findByText("已取消 ops 的管理员")).toBeInTheDocument();
  });

  test("a person's tokens are listed in a dialog and revoking one asks first", async () => {
    let tokens = [{ id: "t1", label: "claude MCP", scopes: ["read", "execute"], expires: 1_900_000_000, last_used: null }];
    const calls = start({ "GET /admin/users/u2/tokens": () => tokens,
      "DELETE /admin/users/u2/tokens/t1": () => { tokens = []; return { ok: true }; } });
    const panel = await openUsers();
    const list = await fromMenu(panel, "dev1", "Token（1）", "dev1 的 Token");
    await userEvent.click(await within(list).findByRole("button", { name: "撤销 Token：claude MCP" }));
    const confirm = await screen.findByRole("dialog", { name: "撤销 Token" });
    expect(confirm).toHaveTextContent("使用它的客户端会立即失去访问");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(within(confirm).getByRole("button", { name: "撤销 Token" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/admin/users/u2/tokens/t1")).toBe(true));
    expect(await screen.findByText("已撤销 Token claude MCP")).toBeInTheDocument();
    const back = await screen.findByRole("dialog", { name: "dev1 的 Token" });   // back to the list, now empty
    expect(await within(back).findByText("没有有效的 Token。")).toBeInTheDocument();
  });

  test("加入 Team happens in a dialog and offers the Teams the person is not in, never an archived one", async () => {
    const calls = start({ "POST /teams/team-2/members": { ok: true } });
    const panel = await openUsers();
    await userEvent.click(within(panel).getByRole("button", { name: "加入 Team：dev1" }));
    const dialog = await screen.findByRole("dialog", { name: "加入 Team" });
    const team = within(dialog).getByLabelText("Team") as HTMLSelectElement;
    expect([...team.options].map((o) => o.textContent)).toEqual(["Web 组"]);
    expect(posted(calls, "/teams/team-2/members")).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "加入 Team" }));
    await waitFor(() => expect(posted(calls, "/teams/team-2/members")).toEqual([{ username: "dev1", role: "member" }]));
    expect(await screen.findByText("已把 dev1 加入 Team「Web 组」")).toBeInTheDocument();
  });

  test("改执行环境 moves the personal space in a dialog; a person without one is simply given it", async () => {
    const calls = start({ "POST /workers/w3/spaces": { ok: true }, "POST /workers/w1/spaces": { ok: true }, "DELETE /workers/w1/spaces": { ok: true } });
    const panel = await openUsers();
    let dialog = await fromMenu(panel, "dev1", "改执行环境", "改执行环境");
    expect(dialog).toHaveTextContent("现在用「共享环境 · personal」");
    let target = within(dialog).getByLabelText("改到") as HTMLSelectElement;
    expect([...target.options].map((o) => o.textContent)).toEqual(["共享环境 · shared-2"]);   // not the one it already uses
    await userEvent.click(within(dialog).getByRole("button", { name: "改执行环境" }));
    await waitFor(() => expect(posted(calls, "/workers/w1/spaces", "DELETE")).toEqual([{ user_id: "u2" }]));
    expect(posted(calls, "/workers/w3/spaces")).toEqual([{ user_id: "u2" }]);
    expect(await screen.findByText("已把 dev1 的个人空间改到 shared-2")).toBeInTheDocument();

    dialog = await fromMenu(panel, "ops", "改执行环境", "改执行环境");
    target = within(dialog).getByLabelText("改到") as HTMLSelectElement;
    expect([...target.options].map((o) => o.textContent)).toEqual(["共享环境 · personal", "共享环境 · shared-2"]);
    await userEvent.selectOptions(target, "w3");
    await userEvent.click(within(dialog).getByRole("button", { name: "改执行环境" }));
    await waitFor(() => expect(posted(calls, "/workers/w3/spaces")).toEqual([{ user_id: "u2" }, { user_id: "u3" }]));
    expect(posted(calls, "/workers/w1/spaces", "DELETE")).toHaveLength(1);
  });

  test("改执行环境: if leaving the old environment fails, it says so, and trying again only leaves it (BACKLOG 2026-10-24)", async () => {
    let joined = false;
    let refuse = true;
    const calls = start({
      "GET /workers": () => WORKERS.map((w) => (w.id === "w3" && joined ? { ...w, spaces: [{ user_id: "u2" }] } : w)),
      "POST /workers/w3/spaces": () => { joined = true; return { ok: true }; },
      "DELETE /workers/w1/spaces": () => (refuse ? new Response(JSON.stringify({ error: "服务暂不可用", code: "x" }), { status: 503 })
        : { ok: true }),
    });
    const panel = await openUsers();
    const dialog = await fromMenu(panel, "dev1", "改执行环境", "改执行环境");
    await userEvent.click(within(dialog).getByRole("button", { name: "改执行环境" }));
    expect(await within(dialog).findByRole("alert"))
      .toHaveTextContent("已加入 shared-2，但没能从 personal 移出：服务暂不可用。再点一次只重试移出。");
    expect(within(dialog).getByLabelText("改到")).toHaveValue("w3");   // still the same choice, after the refresh
    refuse = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "改执行环境" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(calls, "/workers/w3/spaces")).toEqual([{ user_id: "u2" }]);   // joined once, not refused as a duplicate
    expect(posted(calls, "/workers/w1/spaces", "DELETE")).toHaveLength(2);
  });

  test("a deactivated person joins nothing until enabled", async () => {
    const panel = await (start(), openUsers());
    expect(within(panel).getByRole("button", { name: "加入 Team：gone" })).toBeDisabled();
    const items = within(await menu(panel, "gone"));
    expect(items.getByRole("menuitem", { name: "改执行环境…" })).toHaveAttribute("aria-disabled", "true");
    expect(items.getByRole("menuitem", { name: "启用…" })).not.toHaveAttribute("aria-disabled");
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
