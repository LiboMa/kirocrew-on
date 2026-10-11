import { createContext, useContext } from "react";
import type { Me, Run, Step, Team, Template, User, Worker, Workflow } from "@/api/types";

export interface LaunchSeed {
  mode?: "workflow";
  workflow?: { id: string; version: number } | null;
  workflow_id?: string;
  template_id?: string;
  steps?: Step[];
  name?: string;
  input?: string;
}

export type Dialog =
  | { kind: "launch"; seed: LaunchSeed }
  | { kind: "workflow"; flow: Workflow | null; template?: string }
  | { kind: "share"; flowId: string }
  | { kind: "team" }
  | { kind: "tokens" }
  | { kind: "workers" }
  | { kind: "account" }
  | { kind: "continue"; taskId?: string }
  | { kind: "assign" }
  | { kind: "control"; action: "close" | "reopen" | "delete" };

export interface ConsoleState {
  me: Me;
  teams: Team[];
  users: User[];
  workers: Worker[];
  workflows: Workflow[];
  templates: Template[];
  runs: Run[];
  space: string;
  setSpace: (space: string) => void;
  selected: string | null;
  select: (id: string | null) => void;
  open: (dialog: Dialog) => void;
  close: () => void;
}

export const ConsoleContext = createContext<ConsoleState | null>(null);

export function useConsole(): ConsoleState {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole outside ConsoleContext");
  return value;
}

/** Workers that may run in a space: the team's workers, or the user's personal ones. */
export function spaceWorkers(workers: Worker[], space: string, me: Me): Worker[] {
  return workers.filter((w) => !!w.enabled && (space ? w.team_id === space : w.owner_id === me.id));
}
