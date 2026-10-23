import { ACTIVE_STATES, ATTENTION_STATES } from "@/lib/labels";
import type { RunFilter } from "@/lib/session";

/** D56: what the session list asks the server for (GET /v1/runs?page=1, octopus/listing.py). */
export interface RunListFilters {
  q: string;
  group: RunFilter;
  repo: string;
  branch: string;
  stage: string;
  tool: string;
  client: string;
  submitter: string;
  since: string;
  until: string;
}

/** The filters kept in the address bar, besides the search and the state group. */
export const FILTER_KEYS = ["repo", "branch", "stage", "tool", "client", "submitter", "since", "until"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

export const FILTER_LABELS: Record<FilterKey, string> = {
  repo: "仓库", branch: "分支", stage: "阶段", tool: "工具", client: "客户端", submitter: "提交者", since: "创建于起", until: "创建于止",
};

export const STAGE_LABELS: Record<string, string> = {
  plan: "规划", implement: "实现", review: "审查", test: "测试", deliver: "交付", design: "设计", done: "已全部验收",
};

export const EMPTY_FILTERS: RunListFilters = {
  q: "", group: "all", repo: "", branch: "", stage: "", tool: "", client: "", submitter: "", since: "", until: "",
};

const GROUPS: RunFilter[] = ["all", "active", "attention", "done", "closed", "starred"];

export function filtersFromUrl(search: string): RunListFilters {
  const params = new URLSearchParams(search);
  const group = params.get("group") as RunFilter | null;
  return {
    ...EMPTY_FILTERS, q: params.get("q") ?? "", group: group && GROUPS.includes(group) ? group : "all",
    ...Object.fromEntries(FILTER_KEYS.map((key) => [key, params.get(key) ?? ""])),
  };
}

/** Changes some parameters of the address bar (null removes one); the others are left alone. */
export function writeUrl(updates: Record<string, string | null>) {
  const params = new URLSearchParams(window.location.search);
  Object.entries(updates).forEach(([key, value]) => (value ? params.set(key, value) : params.delete(key)));
  const search = params.toString();
  window.history.replaceState(null, "", search ? "?" + search : "/");
}

/** The filters as address-bar parameters (the defaults removed). */
export function filterParams(filters: RunListFilters): Record<string, string | null> {
  return { q: filters.q || null, group: filters.group === "all" ? null : filters.group,
    ...Object.fromEntries(FILTER_KEYS.map((key) => [key, filters[key] || null])) };
}

export function activeFilters(filters: RunListFilters): FilterKey[] {
  return FILTER_KEYS.filter((key) => filters[key]);
}

/** The query for one page: the space ("" personal, a Team id, null every space), the state group as statuses and the
 * lifecycle, the search and the other filters. */
export function listParams(filters: RunListFilters, space: string | null, limit = 50): string {
  const params = new URLSearchParams({ page: "1", view: "list", limit: String(limit) });
  if (space !== null) params.set("space", space || "personal");
  const statuses = { active: [...ACTIVE_STATES], attention: [...ATTENTION_STATES], done: ["completed"] }[filters.group as string];
  if (statuses) params.set("status", statuses.filter((s) => SESSION_STATES.has(s)).join(","));
  if (filters.group === "closed") params.set("lifecycle", "closed");
  else if (filters.group === "starred") params.set("starred", "1");   // one's own stars, open or closed
  else if (filters.group !== "all") params.set("lifecycle", "open");
  if (filters.q.trim()) params.set("q", filters.q.trim());
  activeFilters(filters).forEach((key) => params.set(key, key === "since" || key === "until" ? day(filters[key], key) : filters[key].trim()));
  return params.toString();
}

/** A day the user picked, as Unix seconds of their own day: its start for ``since``, its last second for ``until`` (the
 * server reads YYYY-MM-DD as a UTC day; the console shows local times). Anything else is passed as it is. */
function day(value: string, key: "since" | "until"): string {
  const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!found) return value.trim();
  const [year, month, date] = found.slice(1).map(Number);
  const start = new Date(year, month - 1, key === "since" ? date : date + 1).getTime() / 1000;
  return String(key === "since" ? start : start - 1);
}

/** The statuses a Session has (octopus/domain.py RUN_STATES, kept equal by tests/test_run_search.py); the state groups
 * send only these — task states such as "dispatching" are refused by the server. */
export const SESSION_STATES = new Set([
  "failed", "blocked", "completed", "stopped", "waiting_worker", "awaiting_confirmation", "awaiting_acceptance",
  "preparing", "ready", "running", "waiting_approval", "dispatch_unknown", "stopping", "reopening", "diagnosing",
]);
