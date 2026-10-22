import { vi } from "vitest";
import type { Placement, Run, Worker } from "@/api/types";
import { spaceWorkers } from "@/features/console/context";
import { SESSION_STATES } from "@/lib/runList";

type Handler = (body: unknown, url: URL, init: RequestInit) => unknown;

/** Routes fetch("/v1/...") to handlers keyed by "METHOD /path"; unmatched calls fail the test loudly. */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; path: string; body: unknown; query: string }[] = [];
  const fetchMock = vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = new URL(input, "http://console.test");
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ method, path: url.pathname, body, query: url.search });
    if (url.pathname.endsWith("/events") && url.searchParams.get("stream")) {
      // A "STREAM /runs/<id>/events" route answers the first connection; later reconnects stay pending.
      const stream = routes[`STREAM ${url.pathname.replace(/^\/v1/, "")}`] as (() => Response) | undefined;
      if (stream && url.searchParams.get("after") === "0") return stream();
      return new Promise<Response>(() => {});
    }
    const key = `${method} ${url.pathname.replace(/^\/v1/, "")}`;
    if (!(key in routes)) return new Response(JSON.stringify({ error: "unmocked " + key, code: "test" }), { status: 599 });
    const route = routes[key];
    // A handler may answer later (a Promise), e.g. to observe the console while a request is in flight.
    const value = await (typeof route === "function" ? (route as Handler)(body, url, init) : route);
    if (value instanceof Response) return value;
    return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export const ME = { id: "u1", username: "fixture-owner", admin: 1, scopes: ["read", "execute", "manage", "admin"] };

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

/** What the platform's GET /v1/placement would answer for the fixture's Workers (octopus/domain.py placement). */
function placementOf(workers: Worker[], runs: Run[], url: URL): Placement {
  const run = runs.find((r) => r.id === url.searchParams.get("run_id"));
  const space = run ? run.team_id ?? "" : url.searchParams.get("team_id") ?? "";
  const user = run ? run.owner_id : ME.id;
  const options = spaceWorkers(workers, space, { id: user });
  const mine = options.filter((w) => w.scope === "dedicated" && !w.team_id && w.owner_id === user);
  const chosen = run ? options.find((w) => w.id === run.worker_id) : mine.length === 1 ? mine[0] : options.length === 1 ? options[0] : undefined;
  return { space: space || "personal", hint: null,
    default: chosen ? { id: chosen.id, name: chosen.name, scope: chosen.scope ?? "", placement: run ? null : mine.length === 1 ? "own" : space ? "team" : "only" } : null,
    options: options.map((w) => ({ id: w.id, name: w.name, scope: w.scope ?? "", own: w.scope === "dedicated" && !w.team_id && w.owner_id === user,
      isolation: w.capabilities?.isolation ?? null, models: w.capabilities?.models ?? {} })) };
}

/** What GET /v1/runs?page=1 would answer (octopus/listing.py, D56) for the filters the console sends: space, status,
 * lifecycle and q (in id, name, requirement); newest first; one page. Without page=1, the list as before. */
export function runPage(runs: Run[], url: URL): unknown {
  const params = url.searchParams;
  if (params.get("page") !== "1") return runs;
  const space = params.get("space");
  const status = params.get("status")?.split(",");
  if (status?.some((s) => !SESSION_STATES.has(s)))   // the server refuses a status a Session never has (final review)
    return new Response(JSON.stringify({ error: "status 不是会话的状态", code: "invalid_request" }), { status: 400 });
  const words = (params.get("q") ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  const items = runs.filter((run) => (!space || (space === "personal" ? !run.team_id : run.team_id === space))
    && (!status || status.includes(run.status)) && (!params.get("lifecycle") || run.lifecycle === params.get("lifecycle"))
    && words.every((w) => [run.id, run.name, run.input].join(" ").toLowerCase().includes(w)))
    .sort((a, b) => b.created - a.created || (a.id < b.id ? 1 : -1));
  const limit = Number(params.get("limit") ?? 50);
  return { items: items.slice(0, limit), next: null, total: items.length };
}

export function workspaceRoutes(runs: Run[], extra: Record<string, unknown> = {}) {
  const workers = () => (extra["GET /workers"] ?? []) as Worker[];
  return {
    "GET /placement": (_body: unknown, url: URL) => placementOf(workers(), runs, url),
    "GET /me": ME,
    "GET /teams": [{ id: "team-1", name: "平台组", role: "owner", created: 1, members: [] }],
    "GET /workflows": [],
    "GET /runs": (_body: unknown, url: URL) => runPage(runs, url),
    "GET /workers": [],
    "GET /users": [{ id: "u1", username: "fixture-owner" }],
    "GET /workflow-templates": [],
    "GET /credentials": [],
    ...Object.fromEntries(runs.map((run) => [`GET /runs/${run.id}`, run])),
    ...Object.fromEntries(runs.map((run) => [`GET /runs/${run.id}/app`, { status: "stopped", log: [], openable: false }])),
    ...Object.fromEntries(runs.map((run) => [`GET /runs/${run.id}/approvals`, { approvals: [], supported: true }])),
    ...extra,
  };
}
