import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";
import { openSection } from "@/test/pages";

const policy = (extra: Record<string, unknown> = {}) => ({
  id: "policy-1", kind: "admission", scope: "personal", team_id: null, team_name: null,
  rule: { repos: "github.com/o/*", mode: "auto" }, created_by: "u1", created: 1, deletable: true, ...extra,
});
const preference = policy({ id: "policy-2", kind: "mapping", scope: "team", team_id: "team-1", team_name: "平台组",
  rule: { role: "evaluator", tool: "codex", model: "gpt-5" }, deletable: false });
const review = policy({ id: "policy-3", kind: "mapping", scope: "team", team_id: "team-1", team_name: "平台组",
  rule: { kind: "review", tool: "claude" }, deletable: true });

afterEach(() => vi.unstubAllGlobals());

async function openPolicies(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], { "GET /policies": [policy(), preference], ...extra }));
  render(<App />);
  return { calls, panel: await openSection("我的设置", "映射偏好") };
}

const sent = (calls: { method: string; path: string; body?: unknown }[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === "/v1" + path).map((c) => c.body);

// PRD 2026-10-23 (管理 / 我的设置 in dialogs): the page lists the rules; adding, editing and deleting one happen in dialogs.
describe("mapping preferences (D55); admission rules are gone (D70)", () => {
  test("lists the preferences in a table with their scope; an old admission rule is shown as without effect, to delete", async () => {
    const { panel } = await openPolicies();
    const mapping = within(panel).getByRole("region", { name: "映射偏好列表" });
    const row = within(mapping).getByText("Team「平台组」").closest("[data-policy]") as HTMLElement;
    expect(within(row).getAllByRole("cell").slice(0, 4).map((c) => c.textContent)).toEqual(["Team「平台组」", "角色 Evaluator", "codex", "gpt-5"]);
    expect(within(row).queryByRole("button")).toBeNull();   // a Team's, kept by its owner
    const old = within(panel).getByRole("region", { name: "已取消的准入规则" });
    expect(within(old).getByText(/github\.com\/o\/\*（已取消，不再生效，可删除）/)).toBeInTheDocument();
    expect(within(panel).queryAllByRole("combobox")).toEqual([]);   // nothing to edit on the page itself (PRD §5)
    expect(within(panel).queryAllByRole("textbox")).toEqual([]);
    expect(within(panel).queryByLabelText("类型")).not.toBeInTheDocument();
    expect(within(panel).queryByLabelText("仓库")).not.toBeInTheDocument();
    expect(panel).toHaveTextContent("规划完成后直接开始");
  });

  test("without old admission rules there is no such group; without preferences the list says what happens", async () => {
    const { panel } = await openPolicies({ "GET /policies": [] });
    expect(within(panel).queryByRole("region", { name: "已取消的准入规则" })).not.toBeInTheDocument();
    expect(within(panel).getByRole("region", { name: "映射偏好列表" })).toHaveTextContent("由 KiroCrew 规划者按任务选择工具");
  });

  test("deleting an old admission rule asks first", async () => {
    let listed: unknown[] = [policy(), preference];
    const { calls, panel } = await openPolicies({ "GET /policies": () => listed,
      "DELETE /policies/policy-1": () => { listed = [preference]; return { id: "policy-1", deleted: true }; } });
    await userEvent.click(within(panel).getByRole("button", { name: "删除规则：准入规则 github.com/o/*" }));
    const confirm = await screen.findByRole("dialog", { name: "删除规则" });
    expect(confirm).toHaveTextContent("不再生效");
    expect(sent(calls, "DELETE", "/policies/policy-1")).toEqual([]);   // nothing happens before the confirmation
    await userEvent.click(within(confirm).getByRole("button", { name: "删除规则" }));
    await waitFor(() => expect(within(panel).queryByRole("region", { name: "已取消的准入规则" })).toBeNull());
    expect(sent(calls, "DELETE", "/policies/policy-1")).toHaveLength(1);
    expect(await screen.findByText("已删除规则：准入规则 github.com/o/*")).toBeInTheDocument();
  });

  test("adds a mapping preference by role in a dialog", async () => {
    let listed: unknown[] = [policy()];
    const made = policy({ id: "policy-9", kind: "mapping", rule: { role: "evaluator", tool: "codex" } });
    const { calls, panel } = await openPolicies({ "GET /policies": () => listed,
      "POST /policies": () => { listed = [policy(), made]; return made; } });
    expect(within(panel).queryByLabelText("对应")).toBeNull();
    await userEvent.click(within(panel).getByRole("button", { name: "新增规则" }));
    const dialog = await screen.findByRole("dialog", { name: "新增规则" });
    expect(within(dialog).getByLabelText("作用域")).toHaveFocus();
    await userEvent.selectOptions(within(dialog).getByLabelText("对应"), "role");
    await userEvent.selectOptions(within(dialog).getByLabelText("角色"), "evaluator");
    await userEvent.selectOptions(within(dialog).getByLabelText("工具"), "codex");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加规则" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "POST", "/policies")).toEqual([{ kind: "mapping", scope: "personal", rule: { role: "evaluator", tool: "codex" } }]);
    const row = (await within(panel).findByText("角色 Evaluator")).closest("[data-policy]");
    expect(row).toHaveAttribute("data-fresh", "true");   // the new row is lit up
    expect(await screen.findByText("已添加规则：角色 Evaluator → codex")).toBeInTheDocument();
  });

  test("shows the server's reason in the dialog, which stays open", async () => {
    const { panel } = await openPolicies({
      "POST /policies": () => new Response(JSON.stringify({ error: "只有 Team owner 或平台管理员可以维护 Team 的规则", code: "forbidden" }), { status: 403 }) });
    await userEvent.click(within(panel).getByRole("button", { name: "新增规则" }));
    const dialog = await screen.findByRole("dialog", { name: "新增规则" });
    await userEvent.click(within(dialog).getByRole("button", { name: "添加规则" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("只有 Team owner 或平台管理员可以维护 Team 的规则");
    expect(screen.getByRole("dialog", { name: "新增规则" })).toBeInTheDocument();
  });

  // The API has no PATCH for a rule: editing writes the rule as it now reads, then removes the old one.
  test("edits a rule: the dialog starts from it, and saving replaces it", async () => {
    let listed: unknown[] = [review];
    const saved = { ...review, id: "policy-4", rule: { kind: "review", tool: "codex", model: "gpt-5" } };
    const { calls, panel } = await openPolicies({ "GET /policies": () => listed,
      "POST /policies": () => { listed = [review, saved]; return saved; },
      "DELETE /policies/policy-3": () => { listed = [saved]; return { id: "policy-3", deleted: true }; } });
    await userEvent.click(within(panel).getByRole("button", { name: "编辑规则：类别 审查 → claude" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑规则" });
    expect(within(dialog).getByLabelText("作用域")).toHaveValue("team-1");
    expect(within(dialog).getByLabelText("对应")).toHaveValue("kind");
    expect(within(dialog).getByLabelText("任务类别")).toHaveValue("review");
    expect(within(dialog).getByLabelText("工具")).toHaveValue("claude");
    await userEvent.selectOptions(within(dialog).getByLabelText("工具"), "codex");
    await userEvent.type(within(dialog).getByLabelText("模型（可空）"), "gpt-5");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存规则" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const writes = calls.filter((c) => c.method !== "GET" && c.path.startsWith("/v1/policies"));
    expect(writes.map((c) => [c.method, c.path, c.body])).toEqual([
      ["POST", "/v1/policies", { kind: "mapping", scope: "team", team_id: "team-1", rule: { kind: "review", tool: "codex", model: "gpt-5" } }],
      ["DELETE", "/v1/policies/policy-3", undefined]]);
    expect((await within(panel).findByText("gpt-5")).closest("[data-policy]")).toHaveAttribute("data-fresh", "true");
    expect(await screen.findByText("已保存规则：类别 审查 → codex（模型 gpt-5）")).toBeInTheDocument();
  });

  test("if the old rule cannot be removed after an edit, the dialog says so and trying again only removes it", async () => {
    let refuse = true;
    const saved = { ...review, id: "policy-4", rule: { kind: "review", tool: "codex" } };
    const { calls, panel } = await openPolicies({ "GET /policies": [review], "POST /policies": saved,
      "DELETE /policies/policy-3": () => (refuse ? new Response(JSON.stringify({ error: "服务暂不可用", code: "x" }), { status: 503 })
        : { id: "policy-3", deleted: true }) });
    await userEvent.click(within(panel).getByRole("button", { name: "编辑规则：类别 审查 → claude" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑规则" });
    await userEvent.selectOptions(within(dialog).getByLabelText("工具"), "codex");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存规则" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("新规则已保存，旧规则没能删除：服务暂不可用");
    refuse = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "保存规则" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "POST", "/policies")).toHaveLength(1);
    expect(sent(calls, "DELETE", "/policies/policy-3")).toHaveLength(2);
  });

  test("a change made after the old rule could not be removed is saved too, and nothing is left behind (review 2026-10-24)", async () => {
    let refuse = true;
    let next = 4;
    const { calls, panel } = await openPolicies({ "GET /policies": [review],
      "POST /policies": (body: unknown) => ({ ...review, id: `policy-${next++}`, rule: (body as { rule: unknown }).rule }),
      "DELETE /policies/policy-3": () => (refuse ? new Response(JSON.stringify({ error: "服务暂不可用", code: "x" }), { status: 503 })
        : { id: "policy-3", deleted: true }),
      "DELETE /policies/policy-4": { id: "policy-4", deleted: true } });
    await userEvent.click(within(panel).getByRole("button", { name: "编辑规则：类别 审查 → claude" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑规则" });
    await userEvent.selectOptions(within(dialog).getByLabelText("工具"), "codex");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存规则" }));
    await within(dialog).findByRole("alert");
    refuse = false;
    await userEvent.type(within(dialog).getByLabelText("模型（可空）"), "gpt-5");   // changed before trying again
    const toasts = () => screen.queryAllByText("已保存规则：类别 审查 → codex（模型 gpt-5）").length;   // sonner's are global
    const before = toasts();
    await userEvent.click(within(dialog).getByRole("button", { name: "保存规则" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "POST", "/policies").map((b) => (b as { rule: unknown }).rule)).toEqual([
      { kind: "review", tool: "codex" }, { kind: "review", tool: "codex", model: "gpt-5" }]);
    expect(sent(calls, "DELETE", "/policies/policy-4")).toHaveLength(1);   // the first write, superseded
    expect(sent(calls, "DELETE", "/policies/policy-3")).toHaveLength(2);
    await waitFor(() => expect(toasts()).toBe(before + 1));
  });

  test("an old rule already gone counts as removed", async () => {
    const saved = { ...review, id: "policy-4", rule: { kind: "review", tool: "codex" } };
    const { panel } = await openPolicies({ "GET /policies": [review], "POST /policies": saved,
      "DELETE /policies/policy-3": () => new Response(JSON.stringify({ error: "规则不存在", code: "not_found" }), { status: 404 }) });
    await userEvent.click(within(panel).getByRole("button", { name: "编辑规则：类别 审查 → claude" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑规则" });
    await userEvent.selectOptions(within(dialog).getByLabelText("工具"), "codex");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存规则" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
