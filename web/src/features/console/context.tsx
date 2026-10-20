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
  | { kind: "credentials" }
  | { kind: "workers" }
  | { kind: "users" }
  | { kind: "password" }
  // Session dialogs carry the Session as it was when they opened: commands then send that expected_version, and a
  // concurrent change is rejected by the server instead of silently retargeting the dialog.
  | { kind: "continue"; run: Run; taskId?: string; note?: string }
  | { kind: "assign"; run: Run }
  | { kind: "control"; run: Run; action: "close" | "reopen" | "delete" };

export interface ConsoleState {
  me: Me;
  /** Teams the user belongs to: spaces they can switch to and submit in. */
  teams: Team[];
  /** Every Team the user can see; for an administrator this includes Teams they are not a member of. */
  allTeams: Team[];
  users: User[];
  workers: Worker[];
  workflows: Workflow[];
  templates: Template[];
  runs: Run[];
  space: string;
  setSpace: (space: string) => void;
  /** An administrator's view of every Session, in every space (B5). */
  allSessions: boolean;
  setAllSessions: (all: boolean) => void;
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

/** Workers that may run in a space: dedicated to it, or a shared environment that serves it; never a spare one. */
export function spaceWorkers(workers: Worker[], space: string, me: Pick<Me, "id">): Worker[] {
  return workers.filter((w) => {
    if (!w.enabled || w.scope === "spare") return false;
    if (w.scope === "shared") return (w.spaces ?? []).some((s) => (space ? s.team_id === space : s.user_id === me.id));
    return space ? w.team_id === space : w.owner_id === me.id;
  });
}
