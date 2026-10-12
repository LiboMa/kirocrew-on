import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { followRunEvents, type StreamState } from "@/api/events";
import type { Environment, Me, Observation, Run, RunEvent, Team, Template, User, Worker, Workflow } from "@/api/types";

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
};

const every = (ms: number) => ({ refetchInterval: ms, refetchIntervalInBackground: false });

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => api<Me>("/me"), staleTime: 60_000 });
}

export function useWorkspace(allSessions = false) {
  const teams = useQuery({ queryKey: keys.teams, queryFn: () => api<Team[]>("/teams"), ...every(30_000) });
  const workflows = useQuery({ queryKey: keys.workflows, queryFn: () => api<Workflow[]>("/workflows"), ...every(30_000) });
  // Switching to all Sessions keeps showing the previous list until the new one arrives (no loading screen).
  const runs = useQuery({ queryKey: [...keys.runs, allSessions], placeholderData: (previous) => previous,
    queryFn: () => api<Run[]>(allSessions ? "/runs?all=1" : "/runs"), ...every(10_000) });
  const workers = useQuery({ queryKey: keys.workers, queryFn: () => api<Worker[]>("/workers"), ...every(30_000) });
  const users = useQuery({ queryKey: keys.users, queryFn: () => api<User[]>("/users"), staleTime: 60_000 });
  const templates = useQuery({ queryKey: keys.templates, queryFn: () => api<Template[]>("/workflow-templates"), staleTime: Infinity });
  return {
    teams: teams.data ?? [],
    workflows: workflows.data ?? [],
    runs: runs.data ?? [],
    workers: workers.data ?? [],
    users: users.data ?? [],
    templates: templates.data ?? [],
    loading: runs.isPending || teams.isPending,
    /** The list shown is the previous one while another is loading. */
    refreshing: runs.isPlaceholderData,
    error: runs.error ?? teams.error ?? null,
  };
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
        void client.invalidateQueries({ queryKey: keys.runs });
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
