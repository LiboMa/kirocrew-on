import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import type { Run } from "@/api/types";
import { makeRun, mockApi, workspaceRoutes } from "@/test/api";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const spec = { id: "code", name: "实现登录", tool: "claude" as const, model: "auto", effort: "", prompt: "实现" };
const candidate = { sha: "a".repeat(40), parent: "b".repeat(40), changed: true, files: 2, insertions: 3, deletions: 1 };
const run: Run = makeRun({ id: "run-d", status: "completed", tasks: [{ id: "code", run_id: "run-d", ordinal: 0, status: "accepted",
  current_attempt: "att-1", spec, attempts: [{ id: "att-1", number: 1, status: "accepted", spec, native_id: "n", context_hash: "h",
    evidence: { candidate }, result: "完成", error: null, created: 1, updated: 2 }] }] });
const DIFF = ["diff --git a/src/login.ts b/src/login.ts", "--- a/src/login.ts", "+++ b/src/login.ts", "@@ -1 +1,2 @@",
  "-old", "+new", "+more", "diff --git a/README.md b/README.md", "--- a/README.md", "+++ b/README.md", "@@ -0,0 +1 @@", "+doc", ""].join("\n");

function open(answer: unknown) {
  setToken("oct_fixture");
  window.history.replaceState(null, "", "/?run=run-d");
  const calls = mockApi(workspaceRoutes([run], { "GET /runs/run-d/events": [], "GET /runs/run-d/attempts/att-1/diff": answer }));
  render(<App />);
  return calls;
}

describe("what a candidate changed (D56)", () => {
  test("the files and, for the one opened, its diff; asked for only when opened", async () => {
    const calls = open({ sha: candidate.sha, parent: candidate.parent, truncated: false, diff: DIFF, files: [
      { path: "src/login.ts", insertions: 2, deletions: 1, binary: false }, { path: "README.md", insertions: 1, deletions: 0, binary: false }] });
    await userEvent.click(await screen.findByRole("button", { name: /执行证据与历史 Attempt/ }));
    expect(calls.some((c) => c.path.endsWith("/diff"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /代码改动/ }));
    const files = await screen.findByRole("list", { name: "改动的文件" });
    expect(within(files).getByText("src/login.ts")).toBeInTheDocument();
    expect(within(files).getByText("+2 −1")).toBeInTheDocument();
    await userEvent.click(within(files).getByRole("button", { name: /src\/login\.ts/ }));
    const shown = screen.getByLabelText("src/login.ts 的改动");
    expect(shown).toHaveTextContent("+new");
    expect(shown).not.toHaveTextContent("+doc");
  });

  test("a clipped diff says so, and the server's reason is shown as it is", async () => {
    open({ sha: candidate.sha, parent: null, truncated: true, diff: DIFF, files: [], hidden: 1 });
    await userEvent.click(await screen.findByRole("button", { name: /执行证据与历史 Attempt/ }));
    await userEvent.click(screen.getByRole("button", { name: /代码改动/ }));
    expect(await screen.findByText(/改动较多，只显示了前一部分/)).toBeInTheDocument();
    expect(screen.getByText(/另有 1 个文件（\.env 等/)).toBeInTheDocument();
  });

  test("an older environment's reason is shown", async () => {
    open(() => new Response(JSON.stringify({ error: "这个执行环境的 Worker 版本较旧，还不能显示代码改动。请平台管理员升级执行环境。",
      code: "worker_outdated" }), { status: 409 }));
    await userEvent.click(await screen.findByRole("button", { name: /执行证据与历史 Attempt/ }));
    await userEvent.click(screen.getByRole("button", { name: /代码改动/ }));
    expect(await screen.findByText(/请平台管理员升级执行环境/)).toBeInTheDocument();
  });
});
