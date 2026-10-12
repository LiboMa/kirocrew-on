// Shapes of the platform's /v1 API as served by octopus/server.py (read from real responses; the backend is unchanged).

export type Tool = "kiro" | "claude" | "codex" | "opencode";
export type Effort = "" | "low" | "medium" | "high" | "xhigh" | "max";

export interface Step {
  id: string;
  name: string;
  tool: Tool;
  model: string;
  effort: Effort | string;
  prompt: string;
}

export interface Me {
  id: string;
  username: string;
  admin: number | boolean;
  scopes: string[];
}

export interface User {
  id: string;
  username: string;
}

export interface Member {
  id: string;
  username: string;
  role: "owner" | "member" | string;
}

export interface Team {
  id: string;
  name: string;
  /** null for an administrator viewing a Team they are not a member of. */
  role: "owner" | "member" | string | null;
  created: number;
  members: Member[];
  /** The Team's execution environment (D28); null when none is assigned. Absent from older platforms. */
  environment?: { worker_id: string; name: string; scope: WorkerScope; enabled?: boolean } | null;
}

export interface WorkerCapabilities {
  isolation?: "trusted-local" | "dedicated" | string;
  models?: Partial<Record<Tool, string[]>>;
  workflow_design?: boolean;
  crew_entry?: boolean;
  crew_origin?: string;
  [key: string]: unknown;
}

export type WorkerScope = "dedicated" | "shared" | "spare";

export interface Worker {
  id: string;
  name: string;
  owner_id: string | null;
  team_id: string | null;
  enabled: boolean | number;
  /** dedicated to its owner or Team; shared by the spaces listed in `spaces`; spare until assigned to a Team. */
  scope?: WorkerScope;
  spaces?: { team_id?: string; user_id?: string }[];
  capabilities: WorkerCapabilities;
}

/** GET /v1/admin/environments (administrators, D28): one execution environment with its bindings, load and live health. */
export interface Environment {
  id: string;
  name: string;
  scope: WorkerScope;
  enabled: boolean;
  owner: { id: string; username: string | null } | null;
  team: { id: string; name: string | null } | null;
  spaces: ({ team_id: string; name: string | null } | { user_id: string; username: string | null })[];
  url: string | null;
  active_runs: number;
  health: { reachable: true; version?: string; isolation?: string; crew_entry?: boolean; crew_origin?: string }
    | { reachable: false; error: string };
}

export interface Grant {
  kind: "user" | "team" | string;
  principal: string;
  role: "viewer" | "runner" | "editor" | string;
}

export interface Workflow {
  id: string;
  name: string;
  version: number;
  owner_id: string;
  role: "owner" | "editor" | "runner" | "viewer" | string;
  archived: number | boolean;
  steps: Step[];
  grants: Grant[];
  created: number;
}

export interface Template {
  id: string;
  name: string;
  description?: string;
  steps: Step[];
}

export interface AttemptEvidence {
  actual_backend?: string | null;
  actual_model?: string | null;
  actual_effort?: string | null;
  native_session_id?: string | null;
  outcome?: { decision?: string; [key: string]: unknown } | null;
  [key: string]: unknown;
}

export interface Attempt {
  id: string;
  number: number;
  status: string;
  spec: Step;
  native_id: string | null;
  context_hash: string | null;
  evidence: AttemptEvidence;
  result: string | null;
  error: string | null;
  created: number;
  updated: number;
}

export interface RunTask {
  id: string;
  run_id: string;
  ordinal: number;
  status: string;
  spec: Step;
  current_attempt: string | null;
  attempts?: Attempt[];
}

export interface Attention {
  title: string;
  detail?: string;
  action?: string;
  task_id?: string;
  task_name?: string;
  /** waiting_worker only: enabled Workers in the Session's space. */
  workers?: number;
}

export interface RecoveryRecord {
  number: number;
  status: string;
  failed_task_id: string;
  native_id?: string | null;
  error?: string | null;
  decision?: { reason?: string; instructions?: string; target_task_id?: string; [key: string]: unknown } | null;
}

export interface RecoveryPolicy {
  enabled: boolean;
  max_cycles: number;
}

export interface Recovery {
  policy: RecoveryPolicy;
  used: number;
  latest: RecoveryRecord | null;
  history: RecoveryRecord[];
}

export interface RunSource {
  type: "new" | "git";
  url?: string;
  ref?: string;
}

export interface Run {
  id: string;
  name: string | null;
  input: string;
  status: string;
  lifecycle: "open" | "closed" | "deleted" | string;
  desired: string;
  version: number;
  owner_id: string;
  team_id: string | null;
  worker_id: string | null;
  workflow_id: string | null;
  workflow_name: string | null;
  workflow_version: number | null;
  native_parent: string | null;
  workspace: string | null;
  purpose: string | null;
  error: string | null;
  source: RunSource;
  progress: { accepted: number; total: number };
  tasks: RunTask[];
  created: number;
  updated: number;
  attention?: Attention | null;
  recovery?: Recovery | null;
  /** What the server allows the current user on this Session; absent from older platforms. */
  permissions?: { operate: boolean; delete: boolean; native?: boolean };
  /** KiroCrew Agent planning: intent recognition, requirement enrichment and the generated tasks. */
  planning?: { mode: "agent"; applied: boolean; intent?: string; summary?: string; name?: string; tasks?: string[]; notes?: string[] } | null;
}

/** GET /v1/runs/<id>/observe (D30): the run's native KiroCrew Session, read through its Worker. Messages are oldest
 * first; `next_before` is the index of the first message of this page, for the previous page. */
export interface ObservedMessage {
  role: string;
  content: string;
  truncated: boolean;
  ts: string | null;
}

export interface Observation {
  native_parent: string;
  title: string | null;
  running: boolean;
  total: number | null;
  has_more: boolean;
  next_before: number | null;
  context_pct: number | null;
  messages: ObservedMessage[];
}

/** Session revisions (D32): GET /v1/runs/<id>/revisions and …/revisions/diff. */
export interface RevisionEntry {
  version: number;
  kind: "created" | "planned" | "continued" | "recovered" | string;
  summary: string;
  created: number;
  actor: { id: string; username: string | null };
  /** Written before snapshots existed: rebuilt from what it recorded. */
  reconstructed: boolean;
}

export interface DiffLine {
  op: " " | "-" | "+";
  text: string;
}

export interface TaskDiff {
  id: string;
  name: string;
  status: "added" | "removed" | "changed" | "same";
  fields: Record<string, [string | null, string | null]>;
  prompt: DiffLine[];
}

export interface RevisionDiff {
  from: number;
  to: number;
  from_summary: string;
  to_summary: string;
  reconstructed: boolean;
  input: DiffLine[];
  tasks: TaskDiff[];
}

export interface RunEvent {
  id: number;
  run_id: string;
  kind: string;
  created: number;
  payload: Record<string, unknown> & { task_id?: string; status?: string; error?: string };
}

export interface ApiToken {
  id: string;
  label: string;
  scopes: string;
  expires: number;
  last_used?: number | null;
}

/** GET /v1/admin/users (elevated administrators). */
export interface AdminUser {
  id: string;
  username: string;
  admin: boolean;
  active: boolean;
  created: number;
  last_login: number | null;
  teams: { id: string; name: string; role: string }[];
  tokens: number;
}

export interface UserToken {
  id: string;
  label: string;
  scopes: string[];
  expires: number;
  last_used: number | null;
}

export interface IssuedToken {
  id: string;
  token: string;
  scopes: string[];
  expires: number;
}

export interface Resolution {
  input: string;
  name: string;
  workflow_name: string;
  reason: string;
  steps: Step[];
  selection: { workflow_id?: string; workflow_version?: number; template_id?: string; steps?: Step[] };
  recommendations?: { name: string; tasks: number; selection: { workflow_id?: string; template_id?: string } }[];
}

export interface ParsedWorkflow {
  name: string;
  steps: Step[];
  notes: string[];
}

export interface DesignJob {
  id?: string;
  version?: number;
  status: string;
  input?: string;
  native_parent?: string | null;
  native_task_id?: string | null;
  evidence?: AttemptEvidence;
  config?: { name: string; steps: Step[] } | null;
  error?: string | null;
}
