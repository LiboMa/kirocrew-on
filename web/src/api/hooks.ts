import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { followRunEvents, type StreamState } from "@/api/events";
import type { Me, Run, RunEvent, Team, Template, User, Worker, Workflow } from "@/api/types";

export const keys = {
  me: ["me"] as const,
  teams: ["teams"] as const,
  workflows: ["workflows"] as const,
  runs: ["runs"] as const,
  workers: ["workers"] as const,
  users: ["users"] as const,
  templates: ["templates"] as const,
  tokens: ["tokens"] as const,
  run: (id: string) => ["run", id] as const,
};

const every = (ms: number) => ({ refetchInterval: ms, refetchIntervalInBackground: false });

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => api<Me>("/me"), staleTime: 60_000 });
}

export function useWorkspace() {
  const teams = useQuery({ queryKey: keys.teams, queryFn: () => api<Team[]>("/teams"), ...every(30_000) });
  const workflows = useQuery({ queryKey: keys.workflows, queryFn: () => api<Workflow[]>("/workflows"), ...every(30_000) });
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api<Run[]>("/runs"), ...every(10_000) });
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
    error: runs.error ?? teams.error ?? null,
  };
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
    void followRunEvents(id, {
      signal: controller.signal,
      onState: setStream,
      onEvent: (event) => {
        setEvents((current) => (current.some((e) => e.id === event.id) ? current : [...current, event]));
        void client.invalidateQueries({ queryKey: keys.run(id) });
        void client.invalidateQueries({ queryKey: keys.runs });
      },
    });
    return () => controller.abort();
  }, [id, client]);
  return { events, stream };
}
