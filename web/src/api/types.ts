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
  /** Optional task contract (D34): what the task delivers and how it is checked. */
  kind?: "implement" | "review" | "test" | "deliver" | "design" | "research" | "write";
  /** D69: which failures stop the Session; omitted, inferred from kind (octopus/phases.py). */
  phase?: "diverge" | "design" | "converge";
  /** D46 P2: the role the task's Agent works in (its card); kind decides the gates. */
  role?: "spec" | "sde" | "integration_test" | "security_test" | "challenge" | "evaluator" | "knowledge";
  deliverables?: string[];
  acceptance?: string[];
  verify?: string[];
  /** D57 §5: those of verify (by their text) that must pass; the others only warn. */
  verify_required?: string[];
  /** D57: the KiroCrew planner wrote this task (and its checks). */
  origin?: "planner";
  depends_on?: string[];
  paths?: string[];
  /** D50: methods from the platform's skill pack the task's Agent reads first (at most 4). */
  skills?: string[];
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
  /** An archived Team takes no new Sessions or members; its Sessions stay readable (spec §5). */
  archived?: boolean | number;
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
  health: { reachable: true; version?: string; isolation?: string; crew_entry?: boolean; crew_origin?: string;
    /** kiro-cli's login on the boundary (D34): "expired" holds Kiro dispatches until an administrator logs in again. */
    tool_auth?: { kiro?: "ok" | "expired" | "unknown" } }
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
  outcome?: { decision?: string; acceptance?: AcceptanceCheck[]; suggestions?: string[]; [key: string]: unknown } | null;
  /** Named gates (D37 §4), in the order they were judged. */
  gates?: Gate[];
  /** The task's candidate commit on the Session's branch (D37 §3). */
  candidate?: Candidate | null;
  /** D58: local commits a user synced; this Attempt is theirs, not an Agent's. */
  synced?: { commits?: number; note?: string | null; by?: { id: string; username?: string | null }; client?: string; head?: string; from?: string } | null;
  /** The watchdog stopped this Attempt's native task for making no progress or running too long (D39). */
  watchdog?: { reason?: string; unsupported?: boolean; [key: string]: unknown } | null;
  /** The Coordinator recommended this Attempt's tool instead of the one that failed (D38 §3.3). */
  switched_from?: { tool: string; model: string; effort: string; reason?: string | null; by: string; recovery: number } | null;
  /** D46 P6a: what this Attempt's native run used. */
  usage?: import("@/lib/usage").Usage | null;
  /** D46 P5a: this rework continued the task's native conversation, or a refused continue was spawned afresh. */
  continued?: { conversation: string; from: string } | null;
  continue_refused?: string | null;
  /** Verify commands still running (D37 §5). */
  checks?: { job: string; started: number; commands: number } | null;
  /** What the Evaluator left to a person (D46 P3). */
  evaluation?: { pending?: { item: string; result?: string; evidence: string }[] } | null;
  [key: string]: unknown;
}

export interface Gate {
  name: string;
  /** D57 §4: a warn passed with something to note — shown, never blocking. */
  status: "pass" | "warn" | "fail" | "skip";
  detail?: string;
}

/** D56: GET /v1/runs/<id>/attempts/<attempt>/diff — what the candidate changed against the one before it (or the baseline). */
export interface AttemptDiff {
  sha: string;
  parent: string | null;
  files: { path: string; insertions: number; deletions: number; binary: boolean }[];
  diff: string;
  truncated: boolean;
  /** How many changed files are not shown: what a download leaves out too (.env and the like). */
  hidden?: number;
}

export interface Candidate {
  sha: string;
  parent: string;
  changed: boolean;
  files: number;
  insertions: number;
  deletions: number;
  rewritten?: boolean;
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
  /** Where its tool and model came from (M2: steps, workflow, template, document, planner, continue, coordinator). */
  mapping?: { source: string; label: string };
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
  decision?: {
    reason?: string; instructions?: string; target_task_id?: string; action?: string;
    /** switch_tool (D38): the recommended tool and model, and what they replaced once applied. */
    tool?: string; model?: string; switched_from?: { tool: string; model: string; effort: string };
    [key: string]: unknown;
  } | null;
}

export interface RecoveryPolicy {
  enabled: boolean;
  max_cycles: number;
  /** false: the Coordinator may not recommend another tool (D38 §3.5); absent means allowed. */
  switch_tools?: boolean;
  /** The watchdog's limits in minutes (D39); absent means the defaults (30 and 170). */
  idle_minutes?: number;
  attempt_minutes?: number;
  /** The automatic recovery budget (D42); absent means the defaults (4 retries, 720 minutes). */
  max_retries?: number;
  budget_minutes?: number;
}

/** What automatic recovery used since the Session began or the user's latest continue / requested diagnosis (D42). */
export interface RecoveryBudget {
  since: number;
  reset: boolean;
  cycles: { used: number; limit: number };
  retries: { used: number; limit: number };
  minutes: { used: number; limit: number };
  exhausted: "cycles" | "retries" | "minutes" | null;
}

export interface Recovery {
  policy: RecoveryPolicy;
  used: number;
  budget?: RecoveryBudget;
  latest: RecoveryRecord | null;
  history: RecoveryRecord[];
}

export interface RunSource {
  type: "new" | "git" | "none";   // none: no repository (D65)
  url?: string;
  ref?: string;
}

/** D46 P4: one item a Session's Knowledge task listed, and what became of it in the execution environment's lessons. */
export interface KnowledgeItem {
  id: string;
  task_id: string;
  rule: string;
  avoid: string | null;
  result: "written" | "refused" | "superseded" | "skipped" | "kept";
  reason: string | null;
  created: number;
  removed: number | null;
  removed_by: string | null;
}

/** D49: where a new Session would run, or where a Session may move (GET /v1/placement). */
export type PlacementKind = "own" | "team" | "only" | "requested";

/** D56: one page of GET /v1/runs?page=1 — the matching Sessions after the cursor, the next cursor and how many match. */
export interface RunPage {
  items: Run[];
  next: string | null;
  total: number;
}

/** D55: the client a Request was submitted from (the token's label; octopus/requests.py). */
export type RequestClient = "console" | "cli" | "claude" | "kiro" | "codex" | "kiro-ide" | "api";

/** D55: how a new Session was admitted and why — the submission's own word, a person's or Team's rule, or the default. */
export interface AdmissionBasis {
  mode: "auto" | "confirm";
  basis: "submitted" | "rule" | "default";
  policy_id?: string;
  pattern?: string;
  scope?: "personal" | "team";
}

/** D55: an admission rule by repository, or a mapping preference for the planner to weigh. */
export interface Policy {
  id: string;
  kind: "admission" | "mapping";
  scope: "personal" | "team";
  team_id: string | null;
  team_name: string | null;
  rule: { repos?: string; mode?: "auto" | "confirm"; kind?: string; role?: string; tool?: string; model?: string };
  created_by: string;
  created: number;
  deletable: boolean;
}
export interface PlacementOption {
  id: string;
  name: string;
  scope: string;
  own: boolean;
  isolation?: string | null;
  models: Record<string, string[]>;
  /** D51: the configured model "auto" runs where the tool publishes no list (codex). */
  defaults?: Record<string, string>;
}
export interface Placement {
  space: string;
  default: { id: string; name: string; scope: string; placement: PlacementKind | null } | null;
  hint: string | null;
  options: PlacementOption[];
}

/** D52: this Session's items proposed to the repository's reviewed knowledge when it was published. */
export interface KnowledgeProposal {
  result: "proposed" | "unchanged" | "failed";
  url?: string;
  number?: number;
  kind?: "pull_request" | "merge_request";
  items?: number;
  reused?: boolean;
  reason?: string;
}

export interface Run {
  /** D55: the submission this Session came from; none for a Session from before. */
  request?: { id: string; client: RequestClient } | null;
  admission_basis?: AdmissionBasis | null;
  /** D49: where it runs and, on a Team's environment for want of one's own boundary, why. */
  placement?: { kind: PlacementKind; hint?: string } | null;
  /** D46 P6a: what the Session's Attempts used in total. */
  usage?: import("@/lib/usage").Usage | null;
  /** D46 P4: this Session's 「本次沉淀的知识」, and why a shared environment gathers none. */
  knowledge?: { items: KnowledgeItem[]; note: string | null; proposal?: KnowledgeProposal } | null;
  /** Who submitted the Session (D35 §4). */
  created_by?: { id: string; username?: string | null };
  /** The source credential pinned at submission (D35 §4); never the secret. */
  publish?: PublishRecord | null;
  /** D47: publish a Draft PR / MR on completion (the user's setting, or the requirement asked for it); off: a person approves. */
  publish_policy?: { auto: boolean; source?: "user" | "requested"; quote?: string; branch?: string; title?: string;
    repository?: { owner: string; name: string; private: boolean }; credential?: string } | null;
  /** D48: the repository a new-source Session was first published to; later publishes go there. */
  publish_target?: { host: string; path: string; url: string; web?: string | null; created: boolean; baseline?: string | null;
    credential: { id: string; name: string }; at: number; by: { id: string; username?: string | null } } | null;
  /** D48 R7: a new repository the requirement named, for the person to confirm (never created on its own). */
  publish_suggestion?: { repository: { owner: string; name: string; private: boolean }; quote?: string } | null;
  /** The automatic publish's outcome for the version it was tried at. */
  publish_auto?: { ok: boolean; url?: string | null; code?: string; error?: string; version: number } | null;
  /** What a publish pushes: the candidate that passed the gates and the review (D37 §7). */
  verified?: { sha: string; task_id: string } | null;
  credentials?: { source: CredentialBinding | null; removed?: { id: string; name: string; hint: string; by: { username?: string | null } } } | null;
  /** Tasks the latest continue left as they were, still carrying that version's result (D34). */
  kept?: { version: number; tasks: string[] };
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
  /** resume: false for a Session of an archived Team (readable and stoppable only). */
  /** publish: may publish the Session and answer its approvals (creator, Team owner, admin; D38 A3). */
  /** One's own pin and star (owner 2026-10-22: 收藏、置顶). */
  marks?: { pinned: boolean; starred: boolean };
  permissions?: { operate: boolean; delete: boolean; native?: boolean; resume?: boolean; publish?: boolean;
    /** D49 R7: why the native entry stays closed (e.g. another member's boundary). */
    native_reason?: string;
    /** D49 R10: whether the actor may continue, diagnose, confirm, add a note or answer an approval, and why not. */
    direct?: boolean; direct_reason?: string;
    /** D64: who may share it (its creator, a Team owner, an admin) and move it to another environment (never a share). */
    share?: boolean; assign?: boolean };
  /** D64: how the user reaches it — their own, their Team's, a share (viewer / collaborator) or an admin's oversight. */
  access?: { via: "owner" | "team" | "share" | "admin"; role?: string; by?: string | null };
  /** KiroCrew Agent planning: intent recognition, requirement enrichment and the generated tasks. */
  planning?: { mode: "agent"; applied: boolean; intent?: string; summary?: string; name?: string; tasks?: string[]; notes?: string[] } | null;
  /** How the Session's workflow was chosen: "agent" when the KiroCrew planner wrote its tasks. */
  match_mode?: string | null;
  /** D41: the Coordinator's latest replan and the tasks it wrote. */
  replan?: { tasks?: string[] } | null;
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
  expires: number | null;   // D62: null never expires
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
  /** Sessions of theirs that are open and not settled; absent from older platforms. */
  running?: number;
}

export interface UserToken {
  id: string;
  label: string;
  scopes: string[];
  expires: number | null;   // D62: null never expires
  last_used: number | null;
}

/** D64: who a Session is shared with. */
export interface RunShare {
  id: string;
  kind: "user" | "team" | "everyone";
  subject: string | null;
  name: string;
  role: "viewer" | "collaborator";
  by: string | null;
  created: number;
}

/** D61: limits on running tasks — one's own, one's Teams'; an administrator also sees the default and each person's. */
export interface Quotas {
  mine: { limit: number | null; used: number };
  teams: { id: string; name: string; limit: number | null; used: number; manage: boolean }[];
  default?: number | null;
  users?: { id: string; name: string; limit: number }[];
}

export interface IssuedToken {
  id: string;
  token: string;
  scopes: string[];
  expires: number | null;   // D62: null never expires
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
  /** What KiroCrew answered, as it answered (D66: shown when it is not a valid workflow, to import and fix). */
  raw_result?: string;
  /** D66: the last recommendation was not a valid workflow and KiroCrew is fixing it (once). */
  repairing?: boolean;
}

/** One write to a Session (D34): who made it (a user and their client, or a part of the system) and what changed. */
export interface ChangeEntry {
  id: number;
  at: number;
  kind: string;
  actor: { kind: "user"; id: string; username?: string | null; client?: "console" | "mcp" | "api" }
    | { kind: "engine" | "worker" | "kirocrew" | "recovery" | "legacy" };
  /** {field: [before, after]}; a write to many rows lists each row's changes. Long text is {chars, sha256}. */
  changes: Record<string, [unknown, unknown]> | { id: string; changes: Record<string, [unknown, unknown]> }[] | null;
  reason: string | null;
  detail: Record<string, unknown>;
}

/** One acceptance item as an Attempt's outcome reports it (D34 §6.2). */
export interface AcceptanceCheck {
  item: string;
  result: "pass" | "fail" | "n/a";
  evidence?: string;
}

/** A Session's remote preview (D36 §5): its app running in the execution environment, reached through the preview domain. */
export interface AppPreview {
  status: "stopped" | "installing" | "starting" | "running" | "failed" | "unsupported";
  port: number | null;
  error: string | null;
  /** The last lines of the app's log (install steps and every process). */
  log: string[];
  started: number | null;
  started_by: { id: string; username?: string | null } | null;
  last_access: number | null;
  idle_stop_at: number | null;
  openable: boolean;
  worker_error?: string;
}

export type CredentialKind = "github_pat" | "gitlab_token";
export type CredentialScope = "user" | "team" | "platform";
export type CredentialOwner = { id: string; username?: string | null; name?: string | null } | null;

/** A repository credential as the API lists it (D35 §3): metadata only — the secret is write-only. */
export interface Credential {
  id: string;
  name: string;
  kind: CredentialKind;
  host: string;
  scope: CredentialScope;
  owner: CredentialOwner;
  repos: string[];
  access: "read" | "write";
  hint: string;
  expires: number | null;
  created: number;
  updated: number;
  last_used: number | null;
  disabled: boolean;
  can_manage: boolean;
  grants?: ({ team_id: string } | { user_id: string })[];
}

/** What a Session pins: the credential's identity and who bound it. */
export interface CredentialBinding {
  id: string;
  name: string;
  kind: CredentialKind;
  scope: CredentialScope;
  owner: CredentialOwner;
  hint: string;
  bound_by: { id: string; username?: string | null };
  at: number;
  unavailable?: boolean;
  /** What the credential may do now (publishing needs write). */
  access?: "read" | "write" | null;
}

/** Where a Session's work was published (D35 §5.2). */
export interface PublishRecord {
  branch: string;
  commit: string;
  /** null when the branch was pushed but the pull / merge request was not created (see request_error). */
  url: string | null;
  number: number | null;
  kind: "pull_request" | "merge_request" | null;
  request_error?: string;
  /** D48: the new repository a new-source Session was published to. */
  repository?: string | null;
  reused: boolean;
  at: number;
  by: { id: string; username?: string | null };
}

/** A native approval pending on the Session (D38 §4.2). */
export interface PendingApproval {
  origin: "native" | "coordinator";
  request_id: string;
  request_mid?: string;
  instance?: string;
  source?: string;
  tool: string;
  tool_input: string;
  tool_kind?: string;
  tool_purpose?: string;
  ts?: number;
  /** The content was clipped by the Worker: what is shown is not all of it. */
  truncated?: boolean;
}
export interface ApprovalList { approvals: PendingApproval[]; supported: boolean }
