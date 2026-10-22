import { afterEach, describe, expect, test, vi } from "vitest";
import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { ME, mockApi, workspaceRoutes } from "@/test/api";
import { openSection } from "@/test/pages";

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
  // An administrator sets limits under 管理; anyone else reads theirs (and sets their Teams') in 我的设置.
  const admin = ((extra["GET /me"] ?? ME) as typeof ME).admin;
  return { calls, panel: await openSection(admin ? "管理" : "我的设置", "并发上限") };
}

const posted = (calls: { method: string; path: string; body?: unknown }[]) =>
  calls.filter((c) => c.method === "POST" && c.path === "/v1/quotas").map((c) => c.body);

describe("limits on running tasks (D61)", () => {
  test("shows one's own, the Team's, the default and each person's", async () => {
    const { panel } = await openQuotas();
    expect(within(panel).getByText("运行中 1 / 上限 2")).toBeInTheDocument();
    const team = panel.querySelector('[data-quota="team:team-1"]') as HTMLElement;
    expect(within(team).getByText("Team「平台组」")).toBeInTheDocument();
    expect(within(team).getByText("运行中 0 / 不限")).toBeInTheDocument();
    expect(panel.querySelector('[data-quota="user:u2"]')).toHaveTextContent("bob");
  });

  test("an administrator sets the default and removes a Team's", async () => {
    const { calls, panel } = await openQuotas();
    const row = panel.querySelector('[data-quota="default"]') as HTMLElement;
    await userEvent.clear(within(row).getByLabelText("每人默认的上限"));
    await userEvent.type(within(row).getByLabelText("每人默认的上限"), "3");
    await userEvent.click(within(row).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(posted(calls)).toEqual([{ scope: "default", running: 3 }]));
    const team = panel.querySelector('[data-quota="user:u2"]') as HTMLElement;
    await userEvent.click(within(team).getByRole("button", { name: "取消上限" }));
    await waitFor(() => expect(posted(calls)).toContainEqual({ scope: "user", id: "u2", running: null }));
  });

  test("a person's own limit is checked before it is sent, and a refusal keeps the choice (D61 minors)", async () => {
    const { calls, panel } = await openQuotas({ "POST /quotas": new Response(JSON.stringify({ error: "用户不存在或已停用", code: "not_found" }),
      { status: 404 }) });
    await userEvent.selectOptions(within(panel).getByLabelText("为某人单独设上限"), "u1");
    await userEvent.clear(within(panel).getByLabelText("单独的上限"));
    await userEvent.type(within(panel).getByLabelText("单独的上限"), "0");
    expect(within(panel).getByRole("button", { name: "单独设置" })).toBeDisabled();
    await userEvent.clear(within(panel).getByLabelText("单独的上限"));
    await userEvent.type(within(panel).getByLabelText("单独的上限"), "3");
    await userEvent.click(within(panel).getByRole("button", { name: "单独设置" }));
    await waitFor(() => expect(posted(calls)).toEqual([{ scope: "user", id: "u1", running: 3 }]));
    expect((within(panel).getByLabelText("为某人单独设上限") as HTMLSelectElement).value).toBe("u1");
  });

  test("an administrator sees other Teams apart from their own, and a failed read says why", async () => {
    const teams = [...QUOTAS.teams, { id: "team-9", name: "别的组", limit: 2, used: 0, manage: true }];
    const { panel } = await openQuotas({ "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [] }],
      "GET /quotas": { ...QUOTAS, teams } });
    expect(within(within(panel).getByRole("region", { name: "我和我的 Team" })).queryByText("Team「别的组」")).toBeNull();
    expect(within(within(panel).getByRole("region", { name: "其他 Team" })).getByText("Team「别的组」")).toBeInTheDocument();
  });

  test("a failed read is shown, not an endless loading line", async () => {
    const { panel } = await openQuotas({ "GET /quotas": new Response(JSON.stringify({ error: "服务暂不可用", code: "x" }), { status: 503 }) });
    expect(await within(panel).findByText(/服务暂不可用/)).toBeInTheDocument();
  });

  test("a member only reads", async () => {
    const member = { ...QUOTAS, teams: [{ ...QUOTAS.teams[0], limit: 4, manage: false }], default: undefined, users: undefined };
    const { panel } = await openQuotas({ "GET /me": { ...ME, admin: 0, scopes: ["read", "execute"] }, "GET /quotas": member });
    expect(within(panel).getByText("运行中 0 / 上限 4")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(panel.querySelector('[data-quota="default"]')).toBeNull();
  });
});
