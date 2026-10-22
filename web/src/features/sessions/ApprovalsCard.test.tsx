import { afterEach, describe, expect, test, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setToken } from "@/api/client";
import type { ApprovalList, Run } from "@/api/types";
import { makeRun, mockApi } from "@/test/api";
import { ApprovalsCard } from "@/features/sessions/ApprovalsCard";

const run = makeRun({ id: "run-a", status: "running", lifecycle: "open" });
const item = { origin: "native" as const, request_id: "7", request_mid: "mid-7", tool: "shell", tool_input: "npm test",
  tool_purpose: "运行测试" };

afterEach(() => vi.unstubAllGlobals());

function show(list: ApprovalList, target: Run = run, answer: unknown = { ok: true }) {
  setToken("oct_fixture");
  const calls = mockApi({ [`GET /runs/${target.id}/approvals`]: list, [`POST /runs/${target.id}/approvals/answer`]: answer });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><ApprovalsCard run={target} canAnswer /></QueryClientProvider>);
  return { calls, client, view };
}

describe("approvals card (D38)", () => {
  test("a request replaced while its confirmation is open is not answered with that confirmation", async () => {
    const { calls, client } = show({ approvals: [item], supported: true });
    const card = await screen.findByRole("region", { name: "待批准" });
    await userEvent.click(within(card).getByRole("button", { name: "批准" }));
    expect(within(card).getByRole("button", { name: "确认批准" })).toBeInTheDocument();
    // the next poll: the same request_id now names another request (ids recur in a slot)
    act(() => client.setQueryData(["approvals", run.id],
      { approvals: [{ ...item, request_mid: "mid-new", tool_input: "rm -rf /" }], supported: true }));
    await waitFor(() => expect(within(card).queryByRole("button", { name: "确认批准" })).not.toBeInTheDocument());
    expect(calls.some((c) => c.path.endsWith("/approvals/answer"))).toBe(false);
  });

  test("confirming twice sends one answer", async () => {
    let release: (value: unknown) => void = () => {};
    const { calls } = show({ approvals: [item], supported: true }, run, new Promise((resolve) => { release = resolve; }));
    const card = await screen.findByRole("region", { name: "待批准" });
    await userEvent.click(within(card).getByRole("button", { name: "批准" }));
    const confirm = within(card).getByRole("button", { name: "确认批准" });
    await userEvent.click(confirm);
    await userEvent.click(confirm);
    expect(calls.filter((c) => c.path.endsWith("/approvals/answer"))).toHaveLength(1);
    expect(confirm).toBeDisabled();
    release({ ok: true });
  });

  test("a Session that has ended shows no card, even with an earlier list", async () => {
    const ended = { ...run, status: "completed" } as Run;
    const { client, view } = show({ approvals: [item], supported: true }, ended);
    act(() => client.setQueryData(["approvals", ended.id], { approvals: [item], supported: true }));
    view.rerender(<QueryClientProvider client={client}><ApprovalsCard run={ended} canAnswer /></QueryClientProvider>);
    expect(screen.queryByRole("region", { name: "待批准" })).not.toBeInTheDocument();
  });

  test("clipped content says it is not all of it", async () => {
    show({ approvals: [{ ...item, tool_input: "x".repeat(50) + "…", truncated: true }], supported: true });
    const card = await screen.findByRole("region", { name: "待批准" });
    expect(within(card).getByText("内容已截断：完整内容请在原生 KiroCrew 中查看")).toBeInTheDocument();
  });
});
