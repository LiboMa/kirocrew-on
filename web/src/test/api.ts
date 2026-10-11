import { vi } from "vitest";
import type { Run } from "@/api/types";

type Handler = (body: unknown, url: URL, init: RequestInit) => unknown;

/** Routes fetch("/v1/...") to handlers keyed by "METHOD /path"; unmatched calls fail the test loudly. */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = new URL(input, "http://console.test");
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ method, path: url.pathname, body });
    if (url.pathname.endsWith("/events") && url.searchParams.get("stream")) return new Promise<Response>(() => {});
    const key = `${method} ${url.pathname.replace(/^\/v1/, "")}`;
    if (!(key in routes)) return new Response(JSON.stringify({ error: "unmocked " + key, code: "test" }), { status: 599 });
    const route = routes[key];
    const value = typeof route === "function" ? (route as Handler)(body, url, init) : route;
    if (value instanceof Response) return value;
    return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export const ME = { id: "u1", username: "fixture-owner", admin: 1, scopes: ["read", "execute", "manage"] };

export function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-a", name: "修复登录失败", input: "修复登录失败并补充回归测试", status: "blocked", lifecycle: "open",
    desired: "active", version: 2, owner_id: "u1", team_id: null, worker_id: null, workflow_id: null,
    workflow_name: "Bug 修复", workflow_version: 1, native_parent: null, workspace: "/w/a", purpose: null, error: null,
    source: { type: "new" }, progress: { accepted: 0, total: 2 }, tasks: [], created: 1_790_000_000, updated: 1_790_000_100,
    attention: null, recovery: null,
    ...overrides,
  };
}

export function workspaceRoutes(runs: Run[], extra: Record<string, unknown> = {}) {
  return {
    "GET /me": ME,
    "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [] }],
    "GET /workflows": [],
    "GET /runs": runs,
    "GET /workers": [],
    "GET /users": [{ id: "u1", username: "fixture-owner" }],
    "GET /workflow-templates": [],
    ...Object.fromEntries(runs.map((run) => [`GET /runs/${run.id}`, run])),
    ...extra,
  };
}
