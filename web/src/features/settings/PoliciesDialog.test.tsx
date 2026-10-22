import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";

const policy = (extra: Record<string, unknown> = {}) => ({
  id: "policy-1", kind: "admission", scope: "personal", team_id: null, team_name: null,
  rule: { repos: "github.com/o/*", mode: "auto" }, created_by: "u1", created: 1, deletable: true, ...extra,
});
const preference = policy({ id: "policy-2", kind: "mapping", scope: "team", team_id: "team-1", team_name: "平台组",
  rule: { role: "evaluator", tool: "codex", model: "gpt-5" }, deletable: false });

afterEach(() => vi.unstubAllGlobals());

async function openPolicies(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], { "GET /policies": [policy(), preference], ...extra }));
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "设置" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "准入与映射偏好" }));
  return { calls, dialog: await screen.findByRole("dialog") };
}

describe("admission rules and mapping preferences (D55)", () => {
  test("lists the two groups with their scope and content", async () => {
    const { dialog } = await openPolicies();
    const admission = within(dialog).getByRole("region", { name: "准入规则" });
    expect(within(admission).getByText(/github\.com\/o\/\*/)).toBeInTheDocument();
    expect(within(admission).getByText(/自动开始/)).toBeInTheDocument();
    expect(within(admission).getByText("个人")).toBeInTheDocument();
    const mapping = within(dialog).getByRole("region", { name: "映射偏好" });
    expect(within(mapping).getByText("Team「平台组」")).toBeInTheDocument();
    expect(within(mapping).getByText(/Evaluator → codex（模型 gpt-5）/)).toBeInTheDocument();
    expect(within(mapping).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
  });

  test("adds an admission rule and deletes one", async () => {
    const { calls, dialog } = await openPolicies({ "POST /policies": policy({ id: "policy-3" }), "DELETE /policies/policy-1": { id: "policy-1", deleted: true } });
    await userEvent.type(within(dialog).getByLabelText("仓库"), "github.com/o/*");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/policies")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/policies")?.body).toEqual({
      kind: "admission", scope: "personal", rule: { repos: "github.com/o/*", mode: "auto" } });
    const row = within(dialog).getByText(/github\.com\/o\/\*/).closest("[data-policy]") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/policies/policy-1")).toBe(true));
  });

  test("adds a mapping preference by role", async () => {
    const { calls, dialog } = await openPolicies({ "POST /policies": preference });
    await userEvent.selectOptions(within(dialog).getByLabelText("类型"), "mapping");
    await userEvent.selectOptions(within(dialog).getByLabelText("对应"), "role");
    await userEvent.selectOptions(within(dialog).getByLabelText("角色"), "evaluator");
    await userEvent.selectOptions(within(dialog).getByLabelText("工具"), "codex");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/policies")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/policies")?.body).toEqual({
      kind: "mapping", scope: "personal", rule: { role: "evaluator", tool: "codex" } });
  });

  test("shows the server's reason under the form", async () => {
    const { dialog } = await openPolicies({
      "POST /policies": () => new Response(JSON.stringify({ error: "只有 Team owner 或平台管理员可以维护 Team 的规则", code: "forbidden" }), { status: 403 }) });
    await userEvent.type(within(dialog).getByLabelText("仓库"), "github.com/o/*");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("只有 Team owner 或平台管理员可以维护 Team 的规则");
  });
});
