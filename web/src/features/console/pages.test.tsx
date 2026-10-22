import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { ME, makeRun, mockApi, workspaceRoutes } from "@/test/api";
import { panel } from "@/test/pages";

const MEMBER = { ...ME, admin: 0, scopes: ["read", "execute", "manage"] };

function start(extra: Record<string, unknown> = {}, address = "/") {
  window.history.replaceState(null, "", address);
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([makeRun()], { "GET /tokens": [], "GET /policies": [], "GET /quotas": { mine: { limit: null, used: 0 }, teams: [] },
    "GET /admin/users": [], "GET /admin/environments": [], ...extra }));
  render(<App />);
  return calls;
}

const links = (nav: HTMLElement) => within(nav).getAllByRole("link").map((link) => link.textContent);

afterEach(() => vi.unstubAllGlobals());

describe("the 管理 and 我的设置 pages", () => {
  test("an administrator opens 管理 from the top bar; its section is kept in the address bar, the Sessions are one click away", async () => {
    start();
    expect(screen.queryByRole("button", { name: "设置" })).toBeNull();   // the old settings menu is gone
    await userEvent.click(await screen.findByRole("button", { name: "管理" }));
    const nav = await screen.findByRole("navigation", { name: "管理" });
    expect(links(nav)).toEqual(["概览", "执行环境", "用户", "Team", "接入与凭证", "并发上限"]);
    expect(await panel("概览")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "管理" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(within(nav).getByRole("link", { name: "用户" }));
    expect(within(nav).getByRole("link", { name: "用户" })).toHaveAttribute("aria-current", "page");
    expect(new URLSearchParams(window.location.search).get("view")).toBe("admin");
    expect(new URLSearchParams(window.location.search).get("section")).toBe("users");
    await userEvent.click(screen.getByRole("button", { name: "返回会话" }));
    expect(await screen.findByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  test("a member sees 我的设置 but no 管理, and a 管理 link shows them their Sessions", async () => {
    start({ "GET /me": MEMBER }, "/?view=admin&section=users");
    expect(await screen.findByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "管理" })).toBeNull();
    expect(screen.getByRole("button", { name: "我的设置" })).toBeInTheDocument();
  });

  test("a refresh opens the same section", async () => {
    start({}, "/?view=settings&section=password");
    expect(await panel("修改密码")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "我的设置" })).toHaveAttribute("aria-current", "page");
  });

  test("我的设置 holds one's own settings; an administrator's Teams, environments and limits are under 管理", async () => {
    start();
    await userEvent.click(await screen.findByRole("button", { name: "我的设置" }));
    const nav = await screen.findByRole("navigation", { name: "我的设置" });
    expect(links(nav)).toEqual(["API 与 Coding Agent 接入", "代码仓库凭证", "映射偏好", "修改密码"]);
    expect(await panel("API 与 Coding Agent 接入")).toBeInTheDocument();
    await userEvent.click(within(nav).getByRole("link", { name: "代码仓库凭证" }));
    expect(await panel("代码仓库凭证")).toBeInTheDocument();
    await userEvent.click(within(nav).getByRole("link", { name: "映射偏好" }));
    expect(await panel("映射偏好")).toBeInTheDocument();
  });

  test("a member's 我的设置 also holds their Team, execution environments and limits", async () => {
    start({ "GET /me": MEMBER });
    await userEvent.click(await screen.findByRole("button", { name: "我的设置" }));
    const nav = await screen.findByRole("navigation", { name: "我的设置" });
    expect(links(nav)).toEqual(["API 与 Coding Agent 接入", "代码仓库凭证", "映射偏好", "Team", "执行环境", "并发上限", "修改密码"]);
    await userEvent.click(within(nav).getByRole("link", { name: "并发上限" }));
    expect(await panel("并发上限")).toBeInTheDocument();
  });

  test("the account menu opens 修改密码 and keeps the classic console", async () => {
    start();
    await userEvent.click(await screen.findByRole("button", { name: /账户/ }));
    expect(screen.getByRole("menuitem", { name: "打开经典控制台" })).toHaveAttribute("href", "/classic/");
    await userEvent.click(screen.getByRole("menuitem", { name: "修改密码" }));
    expect(await panel("修改密码")).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("section")).toBe("password");
  });

  test("the open Session is still open after a visit to 我的设置", async () => {
    start({}, "/?run=run-a&view=settings");
    await panel("API 与 Coding Agent 接入");
    await userEvent.click(screen.getByRole("button", { name: "返回会话" }));
    expect(await screen.findByRole("heading", { level: 1, name: /修复登录失败/ })).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("run")).toBe("run-a");
    expect(new URLSearchParams(window.location.search).get("view")).toBeNull();
  });
});
