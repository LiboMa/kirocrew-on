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
  role: "owner" | "member" | string;
  created: number;
  members: Member[];
}

export interface WorkerCapabilities {
  isolation?: "trusted-local" | "dedicated" | string;
  models?: Partial<Record<Tool, string[]>>;
  workflow_design?: boolean;
  crew_entry?: boolean;
  crew_origin?: string;
  [key: string]: unknown;
}

export interface Worker {
  id: string;
  name: string;
  owner_id: string | null;
  team_id: string | null;
  enabled: boolean | number;
  capabilities: WorkerCapabilities;
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
