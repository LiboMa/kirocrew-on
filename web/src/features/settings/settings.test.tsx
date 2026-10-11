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

  test("creating a Team switches the console to its space", async () => {
    const calls = start({ "POST /teams": { id: "team-1", name: "平台组" } });
    const dialog = await openSetting(/团队与成员/);
    await userEvent.type(within(dialog).getByLabelText("创建 Team"), "平台组");
    await userEvent.click(within(dialog).getByRole("button", { name: "创建 Team" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/teams")?.body).toEqual({ name: "平台组" }));
    expect(await within(dialog).findByRole("heading", { name: "平台组" })).toBeInTheDocument();
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
