import { useEffect, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { followRunEvents, type StreamState } from "@/api/events";
import type { ApprovalList, AppPreview, ChangeEntry, Credential, Environment, Me, Observation, Placement, Policy, RevisionDiff, RevisionEntry, Run, RunEvent, RunPage, Team, Template, User, Worker, Workflow } from "@/api/types";

export const keys = {
  me: ["me"] as const,
  teams: ["teams"] as const,
  workflows: ["workflows"] as const,
  runs: ["runs"] as const,
  workers: ["workers"] as const,
  users: ["users"] as const,
  templates: ["templates"] as const,
  tokens: ["tokens"] as const,
  environments: ["admin", "environments"] as const,
  run: (id: string) => ["run", id] as const,
  observe: (id: string) => ["observe", id] as const,
  app: (id: string) => ["app", id] as const,
  credentials: ["credentials"] as const,
  policies: ["policies"] as const,
  placement: (space: string, run?: string) => ["placement", space, run ?? ""] as const,
};

const every = (ms: number) => ({ refetchInterval: ms, refetchIntervalInBackground: false });

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => api<Me>("/me"), staleTime: 60_000 });
}

export function useWorkspace() {
  const teams = useQuery({ queryKey: keys.teams, queryFn: () => api<Team[]>("/teams"), ...every(30_000) });
  const workflows = useQuery({ queryKey: keys.workflows, queryFn: () => api<Workflow[]>("/workflows"), ...every(30_000) });
  const workers = useQuery({ queryKey: keys.workers, queryFn: () => api<Worker[]>("/workers"), ...every(30_000) });
  const users = useQuery({ queryKey: keys.users, queryFn: () => api<User[]>("/users"), staleTime: 60_000 });
  const templates = useQuery({ queryKey: keys.templates, queryFn: () => api<Template[]>("/workflow-templates"), staleTime: Infinity });
  return {
    teams: teams.data ?? [],
    workflows: workflows.data ?? [],
    workers: workers.data ?? [],
    users: users.data ?? [],
    templates: templates.data ?? [],
    loading: teams.isPending,
    error: teams.error ?? null,
  };
}

/** D56: the session list, filtered and paged on the server (``params`` from lib/runList listParams); the loaded pages
 * refresh every 10 s and keep showing while another query loads (no loading screen). */
export function useRunPages(params: string, allSessions: boolean) {
  const base = "/runs?" + params + (allSessions ? "&all=1" : "");
  return useInfiniteQuery({
    queryKey: [...keys.runs, "pages", base], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => api<RunPage>(base + (pageParam ? "&cursor=" + encodeURIComponent(pageParam) : "")),
    getNextPageParam: (last) => last.next ?? undefined, placeholderData: (previous) => previous, ...every(10_000),
  });
}

/** How many Sessions (and the first few) a query matches, for counts: refreshed every 30 s. */
export function useRunCount(params: string, allSessions: boolean) {
  const path = "/runs?" + params + (allSessions ? "&all=1" : "");
  return useQuery({ queryKey: [...keys.runs, "count", path], queryFn: () => api<RunPage>(path),
    placeholderData: (previous) => previous, ...every(30_000) });
}

/** Administrators only. Live health makes it slower than /workers; platforms without the endpoint leave it undefined. */
export function useEnvironments(enabled: boolean) {
  return useQuery({ queryKey: keys.environments, queryFn: () => api<Environment[]>("/admin/environments"), enabled,
    retry: false, ...every(30_000) });
}

/** The newest `limit` messages of a run's native Session; refreshed every 5 s while the run is active. */
export function useObservation(id: string | null, live: boolean, limit = 50) {
  return useQuery({
    queryKey: [...keys.observe(id ?? ""), limit],
    queryFn: () => api<Observation>(`/runs/${encodeURIComponent(id!)}/observe?limit=${limit}`),
    enabled: !!id, retry: false, placeholderData: (previous) => previous,
    refetchInterval: live ? 5_000 : false, refetchIntervalInBackground: false,
  });
}

/** The repository credentials the user may use or manage (D35); metadata only. */
export function useCredentials(enabled = true) {
  return useQuery({ queryKey: keys.credentials, queryFn: () => api<Credential[]>("/credentials"), enabled, retry: false });
}

/** D55: the user's own admission rules and mapping preferences, and those of their Teams. */
export function usePolicies() {
  return useQuery({ queryKey: keys.policies, queryFn: () => api<Policy[]>("/policies"), retry: false });
}

/** The run's remote preview: every 3 s while it installs or starts, every 15 s while it runs. */
/** D49: the platform's own answer to where a Session runs: the console keeps no copy of the rule. */
export function usePlacement(space: string, run?: string) {
  const query = run ? "?run_id=" + encodeURIComponent(run) : space ? "?team_id=" + encodeURIComponent(space) : "";
  return useQuery({ queryKey: keys.placement(space, run), queryFn: () => api<Placement>("/placement" + query), ...every(30_000) });
}

export function useRunApp(id: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.app(id), enabled, retry: false, placeholderData: (previous) => previous,
    queryFn: () => api<AppPreview>(`/runs/${encodeURIComponent(id)}/app`),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "installing" || status === "starting" ? 3_000 : status === "running" ? 15_000 : false;
    },
    refetchIntervalInBackground: false,
  });
}

/** The Session's pending native approvals, every 10 s while it is open and not finished (D38). */
export function useApprovals(id: string, live: boolean) {
  return useQuery({ queryKey: ["approvals", id], enabled: live, retry: false, placeholderData: (previous) => previous,
    queryFn: () => api<ApprovalList>(`/runs/${encodeURIComponent(id)}/approvals`), ...every(10_000) });
}

/** The run's revisions; refetched when the run's version changes. */
export function useChanges(id: string, who: "" | "user" | "system", enabled: boolean) {
  return useQuery({ queryKey: ["changes", id, who], enabled, retry: false, placeholderData: (previous) => previous,
    queryFn: () => api<ChangeEntry[]>(`/runs/${encodeURIComponent(id)}/changes?${who ? "who=" + who + "&" : ""}limit=200`) });
}

export function useRevisions(id: string, version: number) {
  return useQuery({ queryKey: ["revisions", id, version], retry: false,
    queryFn: () => api<RevisionEntry[]>(`/runs/${encodeURIComponent(id)}/revisions`) });
}

export function useRevisionDiff(id: string, from: number | null, to: number | null) {
  return useQuery({ queryKey: ["revisions", id, "diff", from, to], enabled: from !== null && to !== null && from !== to,
    retry: false, placeholderData: (previous) => previous,
    queryFn: () => api<RevisionDiff>(`/runs/${encodeURIComponent(id)}/revisions/diff?from=${from}&to=${to}`) });
}

export function useRun(id: string | null) {
  return useQuery({
    queryKey: keys.run(id ?? ""),
    queryFn: () => api<Run>("/runs/" + encodeURIComponent(id!)),
    enabled: !!id,
    ...every(15_000),
  });
}

/** Live events for one run; every event refreshes the run detail and list. A new id cancels the previous stream. */
export function useRunEvents(id: string | null) {
  const client = useQueryClient();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [stream, setStream] = useState<StreamState>("connecting");
  useEffect(() => {
    setEvents([]);
    if (!id) return;
    const controller = new AbortController();
    // Opening a long Session replays its history: refresh once per burst of events, not once per event.
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const scheduleRefresh = () => {
      if (refresh) return;
      refresh = setTimeout(() => {
        refresh = undefined;
        void client.invalidateQueries({ queryKey: keys.run(id) });
        void client.invalidateQueries({ queryKey: [...keys.runs, "pages"] });   // D56: the list; counts follow every 30 s
      }, 300);
    };
    void followRunEvents(id, {
      signal: controller.signal,
      onState: setStream,
      onEvent: (event) => {
        setEvents((current) => (current.some((e) => e.id === event.id) ? current : [...current, event]));
        scheduleRefresh();
      },
    });
    return () => {
      controller.abort();
      clearTimeout(refresh);
    };
  }, [id, client]);
  return { events, stream };
}
