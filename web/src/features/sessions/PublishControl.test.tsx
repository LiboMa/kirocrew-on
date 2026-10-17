import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setToken } from "@/api/client";
import type { Run } from "@/api/types";
import { makeRun, mockApi } from "@/test/api";
import { PublishControl } from "@/features/sessions/PublishControl";

afterEach(() => vi.unstubAllGlobals());

const writer = { id: "cred-w", name: "我的 GitHub", kind: "github_pat", host: "github.com", scope: "user", owner: { id: "u1" },
  repos: ["LiboMa/*"], access: "write", hint: "…f20h", expires: null, created: 1, updated: 1, last_used: null, disabled: false,
  can_manage: true };

function show(run: Run, routes: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi({ "GET /credentials": [writer], [`GET /runs/${run.id}`]: run, ...routes });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PublishControl run={run} /></QueryClientProvider>);
  return calls;
}

describe("publishing a new project to a new repository (D48)", () => {
  test("the suggested repository is filled in and published with the chosen credential", async () => {
    const run = makeRun({ status: "completed", source: { type: "new" },
      publish_suggestion: { repository: { owner: "LiboMa", name: "demo", private: true }, quote: "新建仓库" } });
    const calls = show(run, { [`POST /runs/${run.id}/publish`]: { branch: "octopus/run-a", commit: "c",
      url: "https://github.com/LiboMa/demo/pull/1", number: 1, kind: "pull_request", reused: false, at: 1, by: { id: "u1" },
      repository: "https://github.com/LiboMa/demo" } });
    expect(await screen.findByLabelText("owner")).toHaveValue("LiboMa");
    expect(screen.getByLabelText("仓库名")).toHaveValue("demo");
    await screen.findByRole("option", { name: /我的 GitHub/ });
    await userEvent.selectOptions(screen.getByLabelText("凭证"), "cred-w");
    await userEvent.click(screen.getByRole("button", { name: "新建仓库并发布" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/publish"))).toBe(true));
    const sent = calls.find((c) => c.method === "POST" && c.path.endsWith("/publish"))!.body;
    expect(sent).toEqual({ repository: { owner: "LiboMa", name: "demo", private: true }, credential: "cred-w" });
  });

  test("once published, the repository is shown and publishing again sends no target", async () => {
    const run = makeRun({ status: "completed", source: { type: "new" },
      publish_target: { host: "github.com", path: "LiboMa/demo", url: "https://github.com/LiboMa/demo.git",
        web: "https://github.com/LiboMa/demo", created: true, baseline: "b", credential: { id: "cred-w", name: "我的 GitHub" },
        at: 1, by: { id: "u1" } } });
    const calls = show(run, { [`POST /runs/${run.id}/publish`]: { branch: "octopus/run-a", commit: "c", url: null, number: null,
      kind: null, reused: true, at: 1, by: { id: "u1" } } });
    expect(await screen.findByRole("link", { name: "LiboMa/demo" })).toHaveAttribute("href", "https://github.com/LiboMa/demo");
    await userEvent.click(screen.getByRole("button", { name: "再次发布到仓库" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/publish"))?.body).toEqual({}));
  });
});
