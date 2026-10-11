import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { makeRun, mockApi, workspaceRoutes } from "@/test/api";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

test("a replay of many events refreshes the Session once, not once per event", async () => {
  setToken("oct_fixture");
  window.history.replaceState(null, "", "/?run=run-a");
  const replay = Array.from({ length: 200 }, (_, i) => `data: {"id":${i + 1},"kind":"attempt.state","run_id":"run-a","created":1,"payload":{}}\n\n`).join("");
  const calls = mockApi({ ...workspaceRoutes([makeRun({ id: "run-a" })]), "STREAM /runs/run-a/events": () => new Response(replay) });
  render(<App />);
  expect(await screen.findByText("200 条")).toBeInTheDocument();
  await new Promise((resolve) => setTimeout(resolve, 600));
  const reads = (path: string) => calls.filter((c) => c.method === "GET" && c.path === path).length;
  await waitFor(() => expect(reads("/v1/runs/run-a")).toBeGreaterThanOrEqual(2));
  expect(reads("/v1/runs/run-a")).toBeLessThanOrEqual(3);
  expect(reads("/v1/runs")).toBeLessThanOrEqual(3);
});
