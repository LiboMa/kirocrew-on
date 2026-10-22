import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";
import { openSection } from "@/test/pages";

const cred = (extra: Record<string, unknown> = {}) => ({
  id: "cred-1", name: "bob 的 GitHub", kind: "github_pat", host: "github.com", scope: "user", access: "read", hint: "…1a2b",
  expires: null, created: 1, updated: 1, last_used: null, owner: { id: "u1", username: "fixture-owner" }, repos: [],
  disabled: false, inject: false, can_manage: true, ...extra,
});
const team = cred({ id: "cred-2", name: "Team GitLab", kind: "gitlab_token", host: "gitlab.example.com", scope: "team",
  owner: { id: "team-1", name: "平台组" }, repos: ["platform/*"], can_manage: false, hint: "…0000" });

afterEach(() => vi.unstubAllGlobals());

async function openCredentials(extra: Record<string, unknown>) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], { "GET /credentials": [cred(), team], ...extra }));
  render(<App />);
  return { calls, panel: await openSection("我的设置", "代码仓库凭证") };
}

describe("repository credentials", () => {
  test("lists credentials by scope, never a secret, and creates one write-only", async () => {
    const { calls, panel } = await openCredentials({ "POST /credentials": cred({ id: "cred-3", name: "新的" }) });
    expect(within(panel).getByText("bob 的 GitHub")).toBeInTheDocument();
    expect(within(panel).getByText(/…1a2b/)).toBeInTheDocument();
    expect(within(panel).getByText("Team GitLab")).toBeInTheDocument();
    expect(within(panel).getByText(/Team「平台组」的 GitLab Token/)).toBeInTheDocument();
    await userEvent.type(within(panel).getByLabelText("名称"), "新的");
    await userEvent.type(within(panel).getByLabelText("仓库范围（可选）"), "kirocrew/*\nother/app");
    const secret = within(panel).getByLabelText("Token") as HTMLInputElement;
    expect(secret.type).toBe("password");
    await userEvent.type(secret, "ghp_test_secret_value");
    await userEvent.click(within(panel).getByRole("button", { name: "保存凭证" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/credentials")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/v1/credentials")?.body).toEqual({
      name: "新的", kind: "github_pat", host: "github.com", scope: "user", repos: ["kirocrew/*", "other/app"], access: "read",
      secret: "ghp_test_secret_value" });
    await waitFor(() => expect(secret.value).toBe(""));
  });

  test("testing a credential shows the account; one only used has no management buttons", async () => {
    const { panel } = await openCredentials({ "POST /credentials/cred-1/test": { ok: true, account: "gh-bob", expires: null } });
    const mine = within(panel).getByText("bob 的 GitHub").closest("[data-credential]") as HTMLElement;
    await userEvent.click(within(mine).getByRole("button", { name: "测试" }));
    expect(await within(mine).findByText(/gh-bob/)).toBeInTheDocument();
    const used = within(panel).getByText("Team GitLab").closest("[data-credential]") as HTMLElement;
    expect(within(used).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(within(mine).getByRole("button", { name: "删除" })).toBeInTheDocument();
  });

  test("editing the repository range starts from the current one and saves only repos", async () => {
    const { calls, panel } = await openCredentials({
      "GET /credentials": [cred({ repos: ["LiboMa/bedrock-claude-call"] }), team],
      "PATCH /credentials/cred-1": cred({ repos: ["LiboMa/bedrock-claude-call", "LiboMa/*"] }) });
    const mine = within(panel).getByText("bob 的 GitHub").closest("[data-credential]") as HTMLElement;
    await userEvent.click(within(mine).getByRole("button", { name: "编辑范围" }));
    const field = within(mine).getByLabelText("仓库范围") as HTMLTextAreaElement;
    expect(field.value).toBe("LiboMa/bedrock-claude-call");
    await userEvent.type(field, "{Enter}LiboMa/*");
    await userEvent.click(within(mine).getByRole("button", { name: "保存范围" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === "/v1/credentials/cred-1")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ repos: ["LiboMa/bedrock-claude-call", "LiboMa/*"] });
    await waitFor(() => expect(within(mine).queryByLabelText("仓库范围")).not.toBeInTheDocument());
    const used = within(panel).getByText("Team GitLab").closest("[data-credential]") as HTMLElement;
    expect(within(used).queryByRole("button", { name: "编辑范围" })).not.toBeInTheDocument();
  });

  test("a refused range keeps the editor open with what was typed", async () => {
    const { panel } = await openCredentials({ "PATCH /credentials/cred-1": () => new Response(
      JSON.stringify({ error: "仓库范围须为 owner/repo、owner/* 或 group/sub/*", code: "invalid_request" }), { status: 400 }) });
    const mine = within(panel).getByText("bob 的 GitHub").closest("[data-credential]") as HTMLElement;
    await userEvent.click(within(mine).getByRole("button", { name: "编辑范围" }));
    await userEvent.type(within(mine).getByLabelText("仓库范围"), "not a range");
    await userEvent.click(within(mine).getByRole("button", { name: "保存范围" }));
    expect(await screen.findByText("仓库范围须为 owner/repo、owner/* 或 group/sub/*")).toBeInTheDocument();
    expect((within(mine).getByLabelText("仓库范围") as HTMLTextAreaElement).value).toBe("not a range");
  });
});
