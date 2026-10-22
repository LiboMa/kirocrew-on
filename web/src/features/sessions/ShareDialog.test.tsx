import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import type { Run } from "@/api/types";
import { makeRun, mockApi, workspaceRoutes } from "@/test/api";

const PERMS = { operate: true, delete: true, native: false, resume: true, publish: true, direct: true, share: true, assign: true };

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function open(run: Run, extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  window.history.replaceState(null, "", "/?run=" + run.id);
  const calls = mockApi(workspaceRoutes([run], extra));
  render(<App />);
  return calls;
}

describe("sharing a Session (D64)", () => {
  test("a viewer is told how they reach it and cannot operate", async () => {
    const run = makeRun({ id: "run-v", status: "blocked", access: { via: "share", role: "viewer", by: "bob" },
      permissions: { ...PERMS, operate: false, delete: false, publish: false, share: false, assign: false } });
    open(run);
    expect(await screen.findByText("你以查看身份访问此会话（由 bob 共享）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /停止/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /续作/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "共享" })).toBeNull();
  });

  test("its creator shares it with a person and the company, and stops sharing", async () => {
    const run = makeRun({ id: "run-s", status: "completed", access: { via: "owner" }, permissions: PERMS });
    const share = { id: "share-1", kind: "user", subject: "u2", name: "eve", role: "viewer", by: "fixture-owner", created: 1 };
    const calls = open(run, { "GET /runs/run-s/shares": [share], "POST /runs/run-s/shares": share,
      "DELETE /runs/run-s/shares/share-1": { id: "share-1", deleted: true } });
    await userEvent.click(await screen.findByRole("button", { name: "共享" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("eve")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("用户名"), "carol");
    await userEvent.selectOptions(within(dialog).getByLabelText("角色"), "collaborator");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加共享" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/v1/runs/run-s/shares")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ kind: "user", username: "carol", role: "collaborator" });
    await userEvent.selectOptions(within(dialog).getByLabelText("共享给"), "everyone");
    expect(within(dialog).getByLabelText("角色")).toBeDisabled();   // the company only views
    await userEvent.click(within(dialog).getByRole("button", { name: "添加共享" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({ kind: "everyone", role: "viewer" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "取消共享" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/v1/runs/run-s/shares/share-1")).toBe(true));
  });

  test("the list shows what is shared with me on request", async () => {
    const calls = open(makeRun({ id: "run-x" }));
    await userEvent.click(await screen.findByRole("button", { name: "共享给我" }));
    await waitFor(() => expect(calls.some((c) => c.method === "GET" && c.path === "/v1/runs" && c.query.includes("shared=1"))).toBe(true));
  });
});
