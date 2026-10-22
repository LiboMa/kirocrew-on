import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { ME, mockApi, workspaceRoutes } from "@/test/api";

const QUOTAS = {
  mine: { limit: 2, used: 1 },
  teams: [{ id: "team-1", name: "平台组", limit: null, used: 0, manage: true }],
  default: 2,
  users: [{ id: "u2", name: "bob", limit: 1 }],
};

afterEach(() => vi.unstubAllGlobals());

async function openQuotas(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], { "GET /quotas": QUOTAS, "POST /quotas": QUOTAS, ...extra }));
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "设置" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "并发上限" }));
  return { calls, dialog: await screen.findByRole("dialog") };
}

const posted = (calls: { method: string; path: string; body?: unknown }[]) =>
  calls.filter((c) => c.method === "POST" && c.path === "/v1/quotas").map((c) => c.body);

describe("limits on running tasks (D61)", () => {
  test("shows one's own, the Team's, the default and each person's", async () => {
    const { dialog } = await openQuotas();
    expect(within(dialog).getByText("运行中 1 / 上限 2")).toBeInTheDocument();
    const team = dialog.querySelector('[data-quota="team:team-1"]') as HTMLElement;
    expect(within(team).getByText("Team「平台组」")).toBeInTheDocument();
    expect(within(team).getByText("运行中 0 / 不限")).toBeInTheDocument();
    expect(dialog.querySelector('[data-quota="user:u2"]')).toHaveTextContent("bob");
  });

  test("an administrator sets the default and removes a Team's", async () => {
    const { calls, dialog } = await openQuotas();
    const row = dialog.querySelector('[data-quota="default"]') as HTMLElement;
    await userEvent.clear(within(row).getByLabelText("每人默认的上限"));
    await userEvent.type(within(row).getByLabelText("每人默认的上限"), "3");
    await userEvent.click(within(row).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(posted(calls)).toEqual([{ scope: "default", running: 3 }]));
    const team = dialog.querySelector('[data-quota="user:u2"]') as HTMLElement;
    await userEvent.click(within(team).getByRole("button", { name: "取消上限" }));
    await waitFor(() => expect(posted(calls)).toContainEqual({ scope: "user", id: "u2", running: null }));
  });

  test("a member only reads", async () => {
    const member = { ...QUOTAS, teams: [{ ...QUOTAS.teams[0], limit: 4, manage: false }], default: undefined, users: undefined };
    const { dialog } = await openQuotas({ "GET /me": { ...ME, admin: 0, scopes: ["read", "execute"] }, "GET /quotas": member });
    expect(within(dialog).getByText("运行中 0 / 上限 4")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(dialog.querySelector('[data-quota="default"]')).toBeNull();
  });
});
