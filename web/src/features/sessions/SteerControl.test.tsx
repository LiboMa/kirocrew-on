import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setToken } from "@/api/client";
import type { Run } from "@/api/types";
import { makeRun, mockApi } from "@/test/api";
import { SteerControl } from "@/features/sessions/SteerControl";

afterEach(() => vi.unstubAllGlobals());

const spec = { id: "code", name: "实现", tool: "claude" as const, model: "auto", effort: "", prompt: "实现" };
const running = (status: string, publish = true): Run => makeRun({ id: "run-s", status: "running",
  permissions: { operate: true, delete: true, native: true, resume: true, publish },
  tasks: [{ id: "code", run_id: "run-s", ordinal: 0, status, current_attempt: "att-1", spec, attempts: [] }] });

function show(run: Run) {
  setToken("oct_fixture");
  const calls = mockApi({ "POST /runs/run-s/steer": { ok: true, status: "steered", task_id: "code" } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SteerControl run={run} task={run.tasks[0]} /></QueryClientProvider>);
  return calls;
}

describe("steer a running task (D46 P5b)", () => {
  test("a note reaches the running task as written", async () => {
    const calls = show(running("running"));
    await userEvent.click(screen.getByRole("button", { name: "补充说明" }));
    await userEvent.type(screen.getByRole("textbox", { name: "补充说明" }), "先只改 README");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "送达方式" }), "follow_up");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/steer"))?.body)
      .toEqual({ task_id: "code", message: "先只改 README", mode: "follow_up" }));
  });

  test("only a running task of someone who may publish offers it", () => {
    show(running("accepted"));
    expect(screen.queryByRole("button", { name: "补充说明" })).not.toBeInTheDocument();
  });

  test("someone who may not publish is not offered it", () => {
    show(running("running", false));
    expect(screen.queryByRole("button", { name: "补充说明" })).not.toBeInTheDocument();
  });
});
