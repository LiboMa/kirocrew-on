import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { makeRun, mockApi, workspaceRoutes } from "@/test/api";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const runs = [
  makeRun({ id: "run-a", name: "修复登录失败", status: "blocked", attention: { title: "规划任务已停止" } }),
  makeRun({ id: "run-b", name: "退款接口幂等", input: "订单退款幂等校验", status: "running", updated: 1_790_000_200 }),
  makeRun({ id: "run-c", name: "团队看板", status: "completed", team_id: "team-1" }),
];

describe("console shell", () => {
  test("login stores the shared token and opens the workspace", async () => {
    const calls = mockApi({ ...workspaceRoutes(runs), "POST /login": { token: "oct_new", id: "t1", scopes: [], expires: 0 } });
    render(<App />);
    await userEvent.type(screen.getByLabelText("用户名"), "fixture-owner");
    await userEvent.type(screen.getByLabelText("密码"), "Fixture-password-123!");
    await userEvent.click(screen.getByRole("button", { name: /登录工作台/ }));
    expect(await screen.findByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    expect(sessionStorage.getItem("octopus.token")).toBe("oct_new");
    expect(calls.find((c) => c.path === "/v1/login")?.body).toEqual({ username: "fixture-owner", password: "Fixture-password-123!" });
  });

  test("a failed login shows the server reason", async () => {
    mockApi({ "POST /login": new Response(JSON.stringify({ error: "用户名或密码错误", code: "unauthorized" }), { status: 401 }) });
    render(<App />);
    await userEvent.type(screen.getByLabelText("用户名"), "x");
    await userEvent.type(screen.getByLabelText("密码"), "y");
    await userEvent.click(screen.getByRole("button", { name: /登录工作台/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
  });

  test("sessions can be searched and filtered within the current space", async () => {
    setToken("oct_fixture");
    mockApi(workspaceRoutes(runs));
    render(<App />);
    const list = await screen.findByRole("navigation", { name: "会话列表" });
    await waitFor(() => expect(within(list).getAllByRole("button")).toHaveLength(2));
    expect(within(list).getAllByRole("button")[0]).toHaveTextContent("退款接口幂等");
    expect(within(list).getByText("规划任务已停止")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("搜索会话"), "订单");
    expect(within(list).getAllByRole("button")).toHaveLength(1);
    await userEvent.clear(screen.getByLabelText("搜索会话"));
    await userEvent.click(screen.getByRole("button", { name: "需处理" }));
    expect(within(list).getAllByRole("button").map((b) => b.dataset.run)).toEqual(["run-a"]);
  });

  test("an administrator can list every Session, labelled with its owner; others cannot", async () => {
    const theirs = makeRun({ id: "run-b", name: "别人的个人会话", owner_id: "u2", team_id: null });
    const searches: string[] = [];
    setToken("oct_fixture");
    mockApi({ ...workspaceRoutes([]), "GET /users": [{ id: "u1", username: "fixture-owner" }, { id: "u2", username: "dev1" }],
      "GET /runs": (_body: unknown, url: URL) => { searches.push(url.search); return url.search === "?all=1" ? [theirs] : []; } });
    render(<App />);
    const toggle = await screen.findByRole("button", { name: "全部会话" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle);
    const list = await screen.findByRole("navigation", { name: "会话列表" });
    expect(await within(list).findByText("别人的个人会话")).toBeInTheDocument();
    expect(list).toHaveTextContent("dev1");
    expect(searches).toContain("?all=1");
  });

  test("members have no all-sessions switch", async () => {
    setToken("oct_fixture");
    mockApi(workspaceRoutes([], { "GET /me": { id: "u2", username: "dev1", admin: 0, scopes: ["read", "execute", "manage"] } }));
    render(<App />);
    await screen.findByRole("navigation", { name: "会话列表" });
    expect(screen.queryByRole("button", { name: "全部会话" })).toBeNull();
  });

  test("selecting a session is reflected in the URL and an unreadable one is dropped", async () => {
    setToken("oct_fixture");
    window.history.replaceState(null, "", "/?run=run-gone");
    mockApi(workspaceRoutes(runs));
    render(<App />);
    const list = await screen.findByRole("navigation", { name: "会话列表" });
    await waitFor(() => expect(window.location.search).toBe(""));
    await userEvent.click(within(list).getAllByRole("button")[0]);
    expect(window.location.search).toBe("?run=run-b");
  });
});
