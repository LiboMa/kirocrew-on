import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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
const row = (panel: HTMLElement, quota: string) => panel.querySelector(`[data-quota="${quota}"]`) as HTMLElement;
/** A row's first cells: who, and its values. */
const cells = (element: HTMLElement, count: number) => within(element).getAllByRole("cell").slice(0, count).map((c) => c.textContent);

// PRD 2026-10-23 (管理 / 我的设置 in dialogs): the page shows the values; changing one happens in a dialog.
describe("limits on running tasks (D61)", () => {
  test("shows one's own, the Team's, the default and each person's — values, nothing to type into", async () => {
    const { panel } = await openQuotas();
    expect(cells(row(panel, "mine"), 3)).toEqual(["我", "1", "2"]);
    expect(cells(row(panel, "team:team-1"), 3)).toEqual(["Team「平台组」", "0", "不限"]);
    expect(cells(row(panel, "default"), 2)).toEqual(["每人默认", "2"]);
    expect(row(panel, "user:u2")).toHaveTextContent("bob");
    expect(cells(row(panel, "user:u2"), 2)[1]).toBe("1");
    expect(within(panel).queryAllByRole("spinbutton")).toEqual([]);   // PRD §5
    expect(within(panel).queryAllByRole("combobox")).toEqual([]);
  });

  test("an administrator changes the default in a dialog, and a Team's", async () => {
    const { calls, panel } = await openQuotas();
    await userEvent.click(within(row(panel, "default")).getByRole("button", { name: "修改上限：每人默认" }));
    const dialog = await screen.findByRole("dialog", { name: "修改上限" });
    const field = within(dialog).getByLabelText("上限");
    expect(field).toHaveValue(2);
    expect(field).toHaveFocus();
    await userEvent.clear(field);
    await userEvent.type(field, "3");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存上限" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(calls)).toEqual([{ scope: "default", running: 3 }]);
    expect(row(panel, "default")).toHaveAttribute("data-fresh", "true");
    expect(await screen.findByText("已保存上限：每人默认 3")).toBeInTheDocument();
    await userEvent.click(within(row(panel, "team:team-1")).getByRole("button", { name: "修改上限：Team「平台组」" }));
    const team = await screen.findByRole("dialog", { name: "修改上限" });
    expect(within(team).getByLabelText("上限")).toHaveValue(null);   // none yet
    await userEvent.type(within(team).getByLabelText("上限"), "4");
    await userEvent.click(within(team).getByRole("button", { name: "保存上限" }));
    await waitFor(() => expect(posted(calls)).toContainEqual({ scope: "team", id: "team-1", running: 4 }));
  });

  test("removing a person's limit asks first and says it falls back to the default", async () => {
    const { calls, panel } = await openQuotas();
    await userEvent.click(within(row(panel, "user:u2")).getByRole("button", { name: "上限操作：bob" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "移除上限…" }));
    const confirm = await screen.findByRole("dialog", { name: "移除上限" });
    expect(confirm).toHaveTextContent("改用每人默认的上限（2）");
    expect(posted(calls)).toEqual([]);
    await userEvent.click(within(confirm).getByRole("button", { name: "移除上限" }));
    await waitFor(() => expect(posted(calls)).toEqual([{ scope: "user", id: "u2", running: null }]));
    expect(await screen.findByText("已移除上限：bob")).toBeInTheDocument();
  });

  test("removing a person's limit that the server refuses keeps the dialog open with the reason (review 2026-10-24)", async () => {
    const { panel } = await openQuotas({ "POST /quotas": () => new Response(JSON.stringify({ error: "需要 admin 权限", code: "forbidden" }),
      { status: 403 }) });
    await userEvent.click(within(row(panel, "user:u2")).getByRole("button", { name: "上限操作：bob" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "移除上限…" }));
    const confirm = await screen.findByRole("dialog", { name: "移除上限" });
    const toasts = () => screen.queryAllByText("已移除上限：bob").length;   // sonner's toasts are global: one may remain from before
    const before = toasts();
    await userEvent.click(within(confirm).getByRole("button", { name: "移除上限" }));
    expect(await within(confirm).findByRole("alert")).toHaveTextContent("需要 admin 权限");
    expect(screen.getByRole("dialog", { name: "移除上限" })).toBeInTheDocument();
    expect(toasts()).toBe(before);   // no success claimed
    expect(row(panel, "user:u2")).toBeInTheDocument();
  });

  test("a person's own limit is checked before it is sent, and a refusal keeps the choice (D61 minors)", async () => {
    const { calls, panel } = await openQuotas({ "POST /quotas": new Response(JSON.stringify({ error: "用户不存在或已停用", code: "not_found" }),
      { status: 404 }) });
    await userEvent.click(within(panel).getByRole("button", { name: "单独设置上限" }));
    const dialog = await screen.findByRole("dialog", { name: "单独设置上限" });
    const person = within(dialog).getByLabelText("用户") as HTMLSelectElement;
    expect([...person.options].map((o) => o.value)).toEqual(["", "u1"]);   // bob has one already
    await userEvent.selectOptions(person, "u1");
    await userEvent.clear(within(dialog).getByLabelText("上限"));
    await userEvent.type(within(dialog).getByLabelText("上限"), "0");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存上限" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("上限须为 1–50 的整数");
    expect(posted(calls)).toEqual([]);
    await userEvent.clear(within(dialog).getByLabelText("上限"));
    await userEvent.type(within(dialog).getByLabelText("上限"), "3");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存上限" }));
    await waitFor(() => expect(posted(calls)).toEqual([{ scope: "user", id: "u1", running: 3 }]));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("用户不存在或已停用");
    expect(person.value).toBe("u1");
  });

  test("an administrator sees other Teams apart from their own", async () => {
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
    expect(cells(row(panel, "team:team-1"), 3)).toEqual(["Team「平台组」", "0", "4"]);
    expect(within(panel).queryAllByRole("button")).toEqual([]);
    expect(row(panel, "default")).toBeNull();
  });
});
