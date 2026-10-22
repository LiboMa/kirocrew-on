// D67 (owner 2026-10-22: 「增加 Session 持久化，这个是 Web 应用最基础的功能了」): the login's HttpOnly session cookie
// signs a new tab or a reopened browser in; 「保持登录」 is the default; the token is kept in this tab only when the
// browser refuses the cookie.
import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { ME, makeRun, mockApi, workspaceRoutes } from "@/test/api";

const runs = [makeRun({ id: "run-a", name: "修复登录失败" })];
const signedOut = () => new Response(JSON.stringify({ error: "需要 API Token", code: "unauthorized" }), { status: 401 });
const issued = { token: "oct_new", id: "t1", scopes: [], expires: 0 };
const sentToken = () =>
  vi.mocked(fetch).mock.calls.some(([, init]) => new Headers((init as RequestInit | undefined)?.headers).has("Authorization"));

afterEach(() => vi.unstubAllGlobals());

async function login(remember = true) {
  await userEvent.type(await screen.findByLabelText("用户名"), "fixture-owner");
  await userEvent.type(screen.getByLabelText("密码"), "Fixture-password-123!");
  if (!remember) await userEvent.click(screen.getByRole("checkbox", { name: /保持登录/ }));
  await userEvent.click(screen.getByRole("button", { name: /登录工作台/ }));
}

describe("login session", () => {
  test("a new tab is signed in by the session cookie and sends no token", async () => {
    mockApi(workspaceRoutes(runs));
    render(<App />);
    expect(await screen.findByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    expect(sentToken()).toBe(false);
  });

  test("without a valid cookie the login page shows", async () => {
    mockApi({ "GET /me": signedOut });
    render(<App />);
    expect(await screen.findByLabelText("用户名")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /保持登录/ })).toBeChecked();
  });

  test("a login stays signed in by default and keeps no token when the cookie works", async () => {
    let cookie = false;
    const calls = mockApi({ ...workspaceRoutes(runs), "GET /me": () => (cookie ? ME : signedOut()),
      "POST /login": () => { cookie = true; return issued; } });
    render(<App />);
    await login();
    expect(await screen.findByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/login")?.body).toEqual(
      { username: "fixture-owner", password: "Fixture-password-123!", remember: true });
    expect(sessionStorage.getItem("octopus.token")).toBeNull();
  });

  test("unchecked, the login ends with the browser", async () => {
    let cookie = false;
    const calls = mockApi({ ...workspaceRoutes(runs), "GET /me": () => (cookie ? ME : signedOut()),
      "POST /login": () => { cookie = true; return issued; } });
    render(<App />);
    await login(false);
    expect(await screen.findByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/v1/login")?.body).toMatchObject({ remember: false });
  });

  test("a browser that refuses the cookie keeps the token for this tab", async () => {
    mockApi({ ...workspaceRoutes(runs), "POST /login": issued,
      "GET /me": (_body: unknown, _url: URL, init: RequestInit) => (new Headers(init.headers).has("Authorization") ? ME : signedOut()) });
    render(<App />);
    await login();
    expect(await screen.findByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    expect(sessionStorage.getItem("octopus.token")).toBe("oct_new");
  });

  test("signing out ends the cookie session and returns to the login page", async () => {
    let cookie = true;
    const calls = mockApi({ ...workspaceRoutes(runs), "GET /me": () => (cookie ? ME : signedOut()),
      "POST /logout": () => { cookie = false; return { ok: true }; } });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /账户/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "退出登录" }));
    expect(await screen.findByLabelText("用户名")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/v1/logout")).toBe(true);
  });
});
