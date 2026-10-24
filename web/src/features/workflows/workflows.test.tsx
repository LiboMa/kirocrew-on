import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { workbench } from "@/test/pages";
import { setToken } from "@/api/client";
import type { Workflow } from "@/api/types";
import { mockApi, workspaceRoutes } from "@/test/api";

const steps = [{ id: "code", name: "实现与测试", tool: "claude" as const, model: "auto", effort: "", prompt: "实现" }];
const flow = (overrides: Partial<Workflow> = {}): Workflow => ({ id: "flow-1", name: "我的开发流程", version: 3, owner_id: "u1", role: "owner",
  archived: 0, steps, grants: [], created: 1, ...overrides });
const templates = [{ id: "chain", name: "完整研发链", description: "", steps }, { id: "proof", name: "最小实现与审查", description: "", steps }];

function start(workflows: Workflow[], extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], {
    "GET /workflows": workflows, "GET /workflow-templates": templates,
    "GET /users": [{ id: "u1", username: "fixture-owner" }, { id: "u2", username: "fixture-reader" }], ...extra,
  }));
  render(<App />);
  return calls;
}

async function openLibrary() {
  await userEvent.click(await workbench("工作流"));   // a page of the workbench (owner 2026-10-24, 方案一)
}

afterEach(() => vi.unstubAllGlobals());

describe("工作流 as a page of the workbench (owner 2026-10-24, 方案一)", () => {
  test("lists the workflows with their task chain; a row starts a Session from it; only its owner shares it", async () => {
    start([flow(), flow({ id: "flow-2", name: "别人的流程", role: "viewer", owner_id: "u2" })]);
    await openLibrary();
    const library = await screen.findByRole("region", { name: "工作流库" });
    const mine = within(library).getByRole("button", { name: "我的开发流程" }).closest("tr") as HTMLElement;
    expect(mine).toHaveTextContent("1 个 · Claude Code");
    expect(mine).toHaveTextContent("v3");
    expect(within(library).getByRole("button", { name: "别人的流程" }).closest("tr")).toHaveTextContent("fixture-reader");
    await userEvent.click(within(mine).getByRole("button", { name: "工作流操作：我的开发流程" }));
    expect(await screen.findByRole("menuitem", { name: "共享…" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(within(library).queryByRole("button", { name: "工作流操作：别人的流程" })).toBeNull();   // a viewer: nothing more to do
    await userEvent.click(within(mine).getByRole("button", { name: "用它新建开发：我的开发流程" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  test("the library is searched by name", async () => {
    start([flow(), flow({ id: "flow-2", name: "退款流程", role: "viewer", owner_id: "u2" })]);
    await openLibrary();
    const library = await screen.findByRole("region", { name: "工作流库" });
    await userEvent.type(within(library).getByRole("searchbox", { name: "搜索工作流名称" }), "退款");
    expect(within(library).queryByRole("button", { name: "我的开发流程" })).toBeNull();
    expect(within(library).getByRole("button", { name: "退款流程" })).toBeInTheDocument();
  });
});

describe("workflow library", () => {
  test("a new workflow starts from a template and is saved by name", async () => {
    const calls = start([], { "POST /workflows": flow() });
    await openLibrary();
    await userEvent.click(screen.getByRole("button", { name: /新建工作流/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("工作流名称")).toHaveValue("完整研发链");
    await userEvent.clear(within(dialog).getByLabelText("工作流名称"));
    await userEvent.type(within(dialog).getByLabelText("工作流名称"), "退款流程");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/workflows")?.body).toEqual({ name: "退款流程", steps }));
  });

  test("the model list is the one of where Sessions of this space run, on auto too", async () => {
    start([], { "GET /workers": [{ id: "b-cold", name: "a-cold", owner_id: "u1", team_id: null, enabled: true, scope: "dedicated",
      capabilities: { models: { claude: ["auto"] } } }], "GET /placement": { space: "personal", hint: null,
      default: { id: "w-personal", name: "personal", scope: "shared", placement: "only" },
      options: [{ id: "w-personal", name: "personal", scope: "shared", own: false, isolation: "trusted-local",
        models: { claude: ["auto", "us.anthropic.claude-opus-5-5", "us.anthropic.claude-sonnet-5-5"] } }] } });
    await openLibrary();
    await userEvent.click(screen.getByRole("button", { name: /新建工作流/ }));
    const dialog = await screen.findByRole("dialog");
    const select = within(dialog).getAllByLabelText("模型")[0];
    await waitFor(() => expect(within(select).getAllByRole("option").map((o) => o.getAttribute("value")))
      .toEqual(["auto", "us.anthropic.claude-opus-5-5", "us.anthropic.claude-sonnet-5-5"]));
    expect(select).toHaveValue("auto");
  });

  test("editing creates a new version against the expected version; viewers only read", async () => {
    const calls = start([flow(), flow({ id: "flow-2", name: "别人的流程", role: "viewer", owner_id: "u2" })],
      { "POST /workflows/flow-1/versions": flow({ version: 4 }) });
    await openLibrary();
    await userEvent.click(screen.getByRole("button", { name: "我的开发流程" }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("WORKFLOW · V3 · OWNER")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/v1/workflows/flow-1/versions")?.body).toMatchObject({ expected_version: 3 }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "别人的流程" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("工作流名称")).toBeDisabled();
    expect(within(dialog).queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /共享/ })).not.toBeInTheDocument();
  });

  test("an imported file is normalized by the server before it replaces the tasks", async () => {
    const parsed = { name: "导入的流程", notes: [], steps: [{ ...steps[0], id: "task-1", name: "导入任务" }] };
    const calls = start([], { "POST /workflows/parse": parsed });
    await openLibrary();
    await userEvent.click(screen.getByRole("button", { name: /新建工作流/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.upload(within(dialog).getByLabelText("导入工作流文件"), new File(['{"steps":[]}'], "flow.json", { type: "application/json" }));
    expect(await within(dialog).findByText("导入任务")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("工作流名称")).toHaveValue("导入的流程");
    expect(calls.find((c) => c.path === "/v1/workflows/parse")?.body).toEqual({ content: '{"steps":[]}' });
  });

  test("the owner shares with a user and can revoke", async () => {
    const calls = start([flow({ grants: [{ kind: "team", principal: "team-1", role: "viewer" }] })], {
      "POST /workflows/flow-1/shares": { ok: true }, "DELETE /workflows/flow-1/shares": { ok: true },
    });
    await openLibrary();
    await userEvent.click(screen.getByRole("button", { name: "我的开发流程" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /共享/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("平台组")).toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByLabelText("共享给"), "user:u2");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加授权" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/workflows/flow-1/shares")?.body)
      .toEqual({ kind: "user", principal: "u2", role: "runner" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ kind: "team", principal: "team-1", role: "viewer" }));
  });
});
