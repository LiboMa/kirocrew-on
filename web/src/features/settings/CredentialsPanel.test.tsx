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

const row = (panel: HTMLElement, name: string) => within(panel).getByText(name).closest("[data-credential]") as HTMLElement;
/** The row's 「⋯」 menu, then one of its items (「删除…」 opens a dialog; 「测试」 runs at once). */
async function pick(panel: HTMLElement, name: string, item: string) {
  await userEvent.click(within(panel).getByRole("button", { name: "凭证操作：" + name }));
  await userEvent.click(await screen.findByRole("menuitem", { name: item === "测试" ? item : item + "…" }));
}
const sent = (calls: { method: string; path: string; body?: unknown }[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === "/v1" + path).map((c) => c.body);

// PRD 2026-10-23 (管理 / 我的设置 in dialogs): the page lists the credentials; adding, changing and removing one happen in dialogs.
describe("repository credentials", () => {
  test("lists credentials in a table, never a secret, and nothing to edit on the page itself", async () => {
    const { panel } = await openCredentials({});
    const mine = row(panel, "bob 的 GitHub");
    expect(within(mine).getByText("…1a2b")).toBeInTheDocument();
    expect(within(mine).getByText("该主机全部")).toBeInTheDocument();
    const used = row(panel, "Team GitLab");
    expect(within(used).getByText("Team「平台组」")).toBeInTheDocument();
    expect(within(used).getByText(/GitLab Token/)).toBeInTheDocument();
    expect(within(used).getByText("gitlab.example.com")).toBeInTheDocument();
    expect(within(used).getByText("platform/*")).toBeInTheDocument();
    expect(within(panel).getAllByRole("row").slice(1).map((r) => r.getAttribute("data-credential"))).toEqual(["cred-1", "cred-2"]);
    // a search is all there is to type into (PRD §5)
    expect(within(panel).queryAllByRole("textbox")).toEqual([]);
    expect(within(panel).queryAllByRole("combobox")).toEqual([]);
    expect(within(panel).queryByLabelText("名称")).toBeNull();
  });

  test("a credential is added in a dialog of small sections, its token write-only", async () => {
    let listed = [cred(), team];
    const { calls, panel } = await openCredentials({ "GET /credentials": () => listed,
      "POST /credentials": () => { listed = [cred(), cred({ id: "cred-3", name: "新的", repos: ["kirocrew/*", "other/app"] }), team];
        return listed[1]; } });
    await userEvent.click(within(panel).getByRole("button", { name: "添加凭证" }));
    const dialog = await screen.findByRole("dialog", { name: "添加凭证" });
    for (const name of ["是什么", "能访问哪些仓库", "Token"]) expect(within(dialog).getByRole("group", { name })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("名称")).toHaveFocus();
    await userEvent.type(within(dialog).getByLabelText("名称"), "新的");
    await userEvent.type(within(dialog).getByLabelText("仓库范围（可选）"), "kirocrew/*\nother/app");
    const secret = within(dialog).getByLabelText("Token") as HTMLInputElement;
    expect(secret.type).toBe("password");
    await userEvent.type(secret, "ghp_test_secret_value");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存凭证" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "POST", "/credentials")).toEqual([{
      name: "新的", kind: "github_pat", host: "github.com", scope: "user", repos: ["kirocrew/*", "other/app"], access: "read",
      secret: "ghp_test_secret_value" }]);
    expect(screen.queryByText("ghp_test_secret_value")).toBeNull();
    await waitFor(() => expect(row(panel, "新的")).toHaveAttribute("data-fresh", "true"));   // the new row is lit up
    expect(await screen.findByText("已添加凭证 新的")).toBeInTheDocument();
  });

  test("a Team owner may add one for the Team; changing the kind moves an untouched host along", async () => {
    const { calls, panel } = await openCredentials({ "POST /credentials": cred({ id: "cred-3" }) });
    await userEvent.click(within(panel).getByRole("button", { name: "添加凭证" }));
    const dialog = await screen.findByRole("dialog", { name: "添加凭证" });
    await userEvent.type(within(dialog).getByLabelText("名称"), "组里的 GitLab");
    await userEvent.selectOptions(within(dialog).getByLabelText("类型"), "gitlab_token");
    expect(within(dialog).getByLabelText("主机")).toHaveValue("gitlab.com");
    await userEvent.selectOptions(within(dialog).getByLabelText("归属"), "team-1");
    await userEvent.selectOptions(within(dialog).getByLabelText("权限"), "write");
    await userEvent.type(within(dialog).getByLabelText("Token"), "glpat_value");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存凭证" }));
    await waitFor(() => expect(sent(calls, "POST", "/credentials")).toEqual([{
      name: "组里的 GitLab", kind: "gitlab_token", host: "gitlab.com", scope: "team", team_id: "team-1", repos: [], access: "write",
      secret: "glpat_value" }]));
  });

  test("a refused credential keeps the dialog open with the server's reason", async () => {
    await openCredentials({ "POST /credentials": () => new Response(
      JSON.stringify({ error: "主机不可达或不是公开地址", code: "invalid_request" }), { status: 400 }) });
    await userEvent.click(screen.getByRole("button", { name: "添加凭证" }));
    const dialog = await screen.findByRole("dialog", { name: "添加凭证" });
    await userEvent.type(within(dialog).getByLabelText("名称"), "内网");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存凭证" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("主机不可达或不是公开地址");
    expect(within(dialog).getByLabelText("名称")).toHaveValue("内网");
  });

  test("testing a credential shows the account; one only used offers no actions", async () => {
    const { panel } = await openCredentials({ "POST /credentials/cred-1/test": { ok: true, account: "gh-bob", expires: null } });
    await pick(panel, "bob 的 GitHub", "测试");
    expect(await within(row(panel, "bob 的 GitHub")).findByText(/gh-bob/)).toBeInTheDocument();
    expect(within(row(panel, "Team GitLab")).queryByRole("button")).toBeNull();
    expect(within(row(panel, "bob 的 GitHub")).getByRole("button", { name: "仓库范围：bob 的 GitHub" })).toBeInTheDocument();
  });

  test("the repository range is changed in a dialog that starts from the current one, and saves only repos", async () => {
    const { calls, panel } = await openCredentials({
      "GET /credentials": [cred({ repos: ["LiboMa/bedrock-claude-call"] }), team],
      "PATCH /credentials/cred-1": cred({ repos: ["LiboMa/bedrock-claude-call", "LiboMa/*"] }) });
    expect(within(row(panel, "Team GitLab")).queryByRole("button", { name: "仓库范围：Team GitLab" })).toBeNull();
    await userEvent.click(within(panel).getByRole("button", { name: "仓库范围：bob 的 GitHub" }));
    const dialog = await screen.findByRole("dialog", { name: "修改仓库范围" });
    const field = within(dialog).getByLabelText("仓库范围") as HTMLTextAreaElement;
    expect(field.value).toBe("LiboMa/bedrock-claude-call");
    expect(field).toHaveFocus();
    await userEvent.type(field, "{Enter}LiboMa/*");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存范围" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "PATCH", "/credentials/cred-1")).toEqual([{ repos: ["LiboMa/bedrock-claude-call", "LiboMa/*"] }]);
    expect(row(panel, "bob 的 GitHub")).toHaveAttribute("data-fresh", "true");
    expect(await screen.findByText("已保存仓库范围：bob 的 GitHub")).toBeInTheDocument();
  });

  test("a refused range keeps the dialog open with what was typed", async () => {
    const { panel } = await openCredentials({ "PATCH /credentials/cred-1": () => new Response(
      JSON.stringify({ error: "仓库范围须为 owner/repo、owner/* 或 group/sub/*", code: "invalid_request" }), { status: 400 }) });
    await userEvent.click(within(panel).getByRole("button", { name: "仓库范围：bob 的 GitHub" }));
    const dialog = await screen.findByRole("dialog", { name: "修改仓库范围" });
    await userEvent.type(within(dialog).getByLabelText("仓库范围"), "not a range");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存范围" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("仓库范围须为 owner/repo、owner/* 或 group/sub/*");
    expect((within(dialog).getByLabelText("仓库范围") as HTMLTextAreaElement).value).toBe("not a range");
  });

  test("the token is replaced in a dialog, write-only; an empty one is not sent", async () => {
    const { calls, panel } = await openCredentials({ "PATCH /credentials/cred-1": cred() });
    await userEvent.click(within(panel).getByRole("button", { name: "更换 Token：bob 的 GitHub" }));
    const dialog = await screen.findByRole("dialog", { name: "更换 Token" });
    const secret = within(dialog).getByLabelText("新的 Token") as HTMLInputElement;
    expect(secret.type).toBe("password");
    expect(secret).toHaveFocus();
    await userEvent.click(within(dialog).getByRole("button", { name: "保存 Token" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("请输入新的 Token");
    expect(sent(calls, "PATCH", "/credentials/cred-1")).toEqual([]);
    await userEvent.type(secret, "ghp_rotated");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存 Token" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "PATCH", "/credentials/cred-1")).toEqual([{ secret: "ghp_rotated" }]);
    expect(screen.queryByText("ghp_rotated")).toBeNull();
    expect(await screen.findByText("已更换 Token：bob 的 GitHub")).toBeInTheDocument();
  });

  test("disabling asks first and says what stops; a disabled one is enabled the same way", async () => {
    const off = cred({ id: "cred-3", name: "旧的 GitHub", disabled: true });
    const { calls, panel } = await openCredentials({ "GET /credentials": [cred(), off, team],
      "PATCH /credentials/cred-1": cred({ disabled: true }), "PATCH /credentials/cred-3": cred({ id: "cred-3" }) });
    expect(within(row(panel, "旧的 GitHub")).getByText("已停用")).toBeInTheDocument();
    await pick(panel, "bob 的 GitHub", "停用");
    const confirm = await screen.findByRole("dialog", { name: "停用凭证" });
    expect(confirm).toHaveTextContent("会话不能再用它克隆或发布");
    expect(sent(calls, "PATCH", "/credentials/cred-1")).toEqual([]);   // nothing happens before the confirmation
    await userEvent.click(within(confirm).getByRole("button", { name: "停用凭证" }));
    await waitFor(() => expect(sent(calls, "PATCH", "/credentials/cred-1")).toEqual([{ disabled: true }]));
    expect(await screen.findByText("已停用凭证 bob 的 GitHub")).toBeInTheDocument();
    await pick(panel, "旧的 GitHub", "启用");
    const enable = await screen.findByRole("dialog", { name: "启用凭证" });
    await userEvent.click(within(enable).getByRole("button", { name: "启用凭证" }));
    await waitFor(() => expect(sent(calls, "PATCH", "/credentials/cred-3")).toEqual([{ disabled: false }]));
  });

  test("deleting asks first, says what follows, then removes the row", async () => {
    let listed = [cred(), team];
    const { calls, panel } = await openCredentials({ "GET /credentials": () => listed,
      "DELETE /credentials/cred-1": () => { listed = [team]; return {}; } });
    await pick(panel, "bob 的 GitHub", "删除");
    const confirm = await screen.findByRole("dialog", { name: "删除凭证" });
    expect(confirm).toHaveTextContent("绑定它的会话");
    expect(confirm).toHaveTextContent("不能撤回");
    expect(sent(calls, "DELETE", "/credentials/cred-1")).toEqual([]);
    await userEvent.click(within(confirm).getByRole("button", { name: "删除凭证" }));
    await waitFor(() => expect(within(panel).queryByText("bob 的 GitHub")).toBeNull());
    expect(sent(calls, "DELETE", "/credentials/cred-1")).toHaveLength(1);
    expect(await screen.findByText("已删除凭证 bob 的 GitHub")).toBeInTheDocument();
  });

  test("the list is searched by name or host", async () => {
    const { panel } = await openCredentials({});
    await userEvent.type(within(panel).getByRole("searchbox", { name: "搜索凭证名称或主机" }), "gitlab.example");
    expect(within(panel).queryByText("bob 的 GitHub")).toBeNull();
    expect(within(panel).getByText("Team GitLab")).toBeInTheDocument();
    await userEvent.clear(within(panel).getByRole("searchbox", { name: "搜索凭证名称或主机" }));
    await userEvent.type(within(panel).getByRole("searchbox", { name: "搜索凭证名称或主机" }), "nothing");
    expect(panel).toHaveTextContent("没有名称或主机含「nothing」的凭证。");
  });
});
