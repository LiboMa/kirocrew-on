"""Discoverable HTTP inputs for coding clients."""
from . import __version__


def openapi():
    def obj(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    def ref(name):
        return {"$ref": "#/components/schemas/" + name}
    text, integer = {"type": "string"}, {"type": "integer", "minimum": 1}
    # D48: a new repository a new-source Session is published to
    repository = obj({"owner": text, "name": text, "private": {"type": "boolean", "default": True}}, ["owner", "name"])
    execution = {"tool": {"enum": ["kiro", "claude", "codex", "opencode"]},
                 "model": {"type": "string", "default": "auto"},
                 "effort": {"enum": ["", "low", "medium", "high", "xhigh", "max"]}, "prompt": text}
    command = {"request_id": {"type": "string", "minLength": 8, "maxLength": 100}, "expected_version": integer}
    step_list = {"type": "array", "items": ref("Step"), "minItems": 1, "maxItems": 12}   # D66
    listed = lambda items: {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": items}
    from . import roles, skills
    contract = {"kind": {"enum": ["implement", "review", "test", "deliver", "design", "research", "write"]},
                "role": {"enum": list(roles.KINDS)},
                # D69: which failures stop the Session; omitted, inferred from kind (research diverge, design design, else converge)
                "phase": {"enum": ["diverge", "design", "converge"]},
                "deliverables": listed(20),
                "acceptance": listed(20), "verify": listed(10), "depends_on": listed(8), "paths": listed(20),
                "skills": {"type": "array", "items": {"enum": list(skills.CATALOG)}, "maxItems": skills.LIMIT},
                # D57 §5: those of verify that must pass; omitted, every check a person writes must pass
                "verify_required": {**listed(10), "description": "Checks of verify (by their text) that must pass; the "
                                    "others only report a warning. Omitted in steps or a continue's verify: all of them."}}
    schemas = {
        "Step": obj({"id": text, "name": text, **execution, **contract}, ["id", "name", "tool", "prompt"]),
        "Execution": obj({"name": text, **execution, **contract}),   # a continue may change the task contract too
        "Source": obj({"type": {"enum": ["new", "git", "none"]}, "url": text, "ref": text}, ["type"]),   # D65: none
        "Login": obj({"username": text, "password": {"type": "string", "format": "password"},
                      # D67: true keeps the login (its token and session cookie) 30 days; otherwise 1 day, cookie until the browser closes
                      "remember": {"type": "boolean", "default": False}}, ["username", "password"]),
        "User": obj({"username": text, "password": {"type": "string", "format": "password"}, "admin": {"type": "boolean"},
                     "worker_id": text}, ["username", "password"]),
        "Team": obj({"name": text, "owner": text, "worker_id": text}, ["name", "owner", "worker_id"]),
        "Member": obj({"username": text, "role": {"enum": ["owner", "member"]}}, ["username"]),
        "Workflow": obj({"name": text, "steps": step_list, "expected_version": integer, "credentials": obj({"source": text})},
                        ["name", "steps"]),
        "Grant": obj({"kind": {"enum": ["user", "team"]}, "principal": text,
                     "role": {"enum": ["viewer", "runner", "editor"]}}, ["kind", "principal"]),
        "Archive": obj({"expected_version": integer, "archived": {"type": "boolean"}}, ["expected_version"]),
        "Worker": obj({"name": text, "owner_id": text, "team_id": text, "spare": {"type": "boolean"},
                       "connection": ref("Connection")}, ["name", "connection"]),
        "Connection": obj({"url": text, "token": {"type": "string", "format": "password"}}, ["url", "token"]),
        "Enabled": obj({"enabled": {"type": "boolean"}}, ["enabled"]),
        "PasswordChange": obj({"current": {"type": "string", "format": "password"},
                               "password": {"type": "string", "format": "password", "minLength": 12}}, ["current", "password"]),
        "PasswordReset": obj({"password": {"type": "string", "format": "password", "minLength": 12}}, ["password"]),
        "Active": obj({"active": {"type": "boolean"}}, ["active"]),
        "AdminFlag": obj({"admin": {"type": "boolean"}}, ["admin"]),
        "ConnectionUpdate": obj({"connection": ref("Connection")}, ["connection"]),
        "Share": obj({"confirm": {"const": True}}, ["confirm"]),
        "PublicShare": obj({"kind": {"type": "string", "enum": ["pages", "app"]}, "mode": {"type": "string", "enum": ["code", "open"]},
                            "expires": {"type": "string", "enum": ["1d", "7d", "30d", "never"]}, "entry": text}),
        "Space": obj({"team_id": text, "user_id": text}),
        "Rename": obj({"name": text}, ["name"]),
        "AppAction": obj({"action": {"type": "string", "enum": ["start", "stop"]}}, ["action"]),
        "Credential": obj({"name": text, "kind": {"type": "string", "enum": ["github_pat", "gitlab_token"]}, "host": text,
                           "scope": {"type": "string", "enum": ["user", "team", "platform"]}, "team_id": text,
                           "repos": {"type": "array", "items": text, "maxItems": 50},
                           "access": {"type": "string", "enum": ["read", "write"]},
                           "secret": {"type": "string", "writeOnly": True, "minLength": 8, "maxLength": 500}},
                          ["name", "kind", "host", "secret"]),
        "CredentialUpdate": obj({"name": text, "repos": {"type": "array", "items": text, "maxItems": 50},
                                 "access": {"type": "string", "enum": ["read", "write"]}, "disabled": {"type": "boolean"},
                                 "secret": {"type": "string", "writeOnly": True, "minLength": 8, "maxLength": 500}}),
        "CredentialGrant": obj({"team_id": text, "username": text}),
        "TeamArchive": obj({"archived": {"type": "boolean"}}, ["archived"]),
        "TeamEnvironment": obj({"worker_id": text}, ["worker_id"]),
        "Token": obj({"label": text, "ttl": integer, "scopes": {"type": "array", "minItems": 1,
                       "items": {"enum": ["read", "execute", "manage", "admin"]}},
                      # D62: how long it lasts; never → expires null. With admin at most 30d. ttl (seconds, ≤ 30 days) as before.
                      "expires_in": {"enum": ["7d", "30d", "365d", "never"]}}),
        "Resolve": obj({"input": text, "name": text, "workflow_id": text, "workflow_version": integer,
                        "template_id": text, "steps": step_list, "team_id": text}, ["input"]),
        "Design": obj({"request_id": command["request_id"], "input": text, "worker_id": text,
                       "workflow_id": text, "workflow_version": integer, "template_id": text,
                                  "steps": step_list, "team_id": text}, ["request_id", "input", "worker_id"]),
        "RecoveryPolicy": obj({"enabled": {"type": "boolean", "default": True},
                               "max_cycles": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
                               "switch_tools": {"type": "boolean", "default": True},
                               "idle_minutes": {"type": "integer", "minimum": 5, "maximum": 240, "default": 30},
                               "attempt_minutes": {"type": "integer", "minimum": 30, "maximum": 720, "default": 170},
                               "max_retries": {"type": "integer", "minimum": 0, "maximum": 20, "default": 4},
                               "budget_minutes": {"type": "integer", "minimum": 30, "maximum": 4320, "default": 720}}),
        "PublishPolicy": obj({"auto": {"type": "boolean"}, "branch": text, "title": text, "repository": repository,
                              "credential": text}, ["auto"]),
        "Publish": obj({"branch": text, "title": text, "repository": repository, "credential": text}),
        "ApprovalAnswer": obj({"origin": {"enum": ["native", "coordinator"]}, "request_id": text, "request_mid": text,
                               "instance": text, "decision": {"enum": ["approve", "reject"]}}, ["origin", "request_id", "decision"]),
        "WorkflowDocument": obj({"content": text, "format": {"enum": ["json", "yaml", "markdown"]}}, ["content"]),
        "Start": obj({"request_id": command["request_id"], "input": text, "name": text, "workflow_id": text,
                      "template_id": text, "document": ref("WorkflowDocument"), "plan": {"enum": ["agent", "match"]},
                      "workflow_version": integer, "steps": step_list, "team_id": text, "worker_id": text,
                       "source": ref("Source"), "recovery": ref("RecoveryPolicy"),
                       # D70: accepted from older clients and not used — no plan waits for confirmation
                       "admission": {"enum": ["confirm", "auto"], "deprecated": True},
                       "publish": obj({"auto": {"type": "boolean", "default": False}, "branch": text, "title": text,
                                       "repository": repository, "credential": text}),
                       "credentials": obj({"source": text})}, ["request_id", "input", "source"]),
        "Rebind": obj({"source": {"type": ["string", "null"]}}, ["source"]),
        # D55: a mapping preference (admission rules are gone, D70)
        "Policy": obj({"kind": {"enum": ["mapping"]}, "scope": {"enum": ["personal", "team"]}, "team_id": text,
                       "rule": {"type": "object"}}, ["kind", "scope", "rule"]),
        "Marks": obj({"pinned": {"type": "boolean"}, "starred": {"type": "boolean"}}),   # one's own pin and star
        "Share": obj({"kind": {"enum": ["user", "team", "everyone"]}, "username": text, "id": text,
                      "role": {"enum": ["viewer", "collaborator"]}}, ["kind", "role"]),   # D64
        "Quota": obj({"scope": {"enum": ["default", "user", "team"]}, "id": text,
                      "running": {"type": ["integer", "null"], "minimum": 1, "maximum": 50}}, ["scope", "running"]),   # D61
        "KnowledgeForget": obj({"id": text}, ["id"]),   # D46 P4: one item of the Session's knowledge.items
        "Steer": obj({"task_id": text, "message": text, "mode": {"enum": ["interrupt"]}},
                     ["task_id", "message"]),   # D46 P5b: into the running turn only
        "Continue": obj({**command, "task_id": text, "input": text, "execution": ref("Execution"),
                         "recovery": ref("RecoveryPolicy"), "rerun": {"type": "array", "items": text, "maxItems": 12}},
                        ["request_id", "expected_version", "task_id"]),
        "Control": obj({**command, "confirmation": text}, ["request_id", "expected_version"]),
        "Recover": obj({**command, "rediagnose": {"type": "boolean", "deprecated": True}}, ["request_id", "expected_version"]),
        "AssignWorker": obj({**command, "worker_id": text}, ["request_id", "expected_version", "worker_id"]),
        "Empty": obj({}), "Error": obj({"error": text, "code": text}, ["error", "code"]),
        # MVP 0.2.0 (N §5): a one-time enrollment code, and the Worker registering itself with it
        "ProvisionBoundary": obj({"owner_id": text, "name": text}, ["owner_id"]),
        "Enrollment": obj({"name": text, "owner_id": text, "team_id": text, "spare": {"type": "boolean"},
                           "expires_in": {"type": "integer", "minimum": 60, "maximum": 86400, "default": 1800}}, ["name"]),
        "Enroll": obj({"code": text, "url": text, "token": {"type": "string", "minLength": 32},
                       "check": {"type": "boolean", "default": False}}, ["code", "url", "token"]),
    }
    schemas["Recover"]["description"] = ("Execute scope. Starts a Coordinator diagnosis of the Session's failed task, every time "
                                         "it is asked (D71: no hold after earlier waits; rediagnose is accepted and not needed).")
    schemas["Resolve"]["description"] = "Read-only preview. Explicit workflow_id, template_id or steps wins. If omitted, match permitted workflows/templates using explainable rules. No native tasks or workspace are created."
    schemas["Design"]["description"] = "Execute scope. Starts a real Kiro workflow-design task via the chosen Worker and native ACP. Only recommends; does not run or save the recommended workflow. Poll /designs/{id}, then adopt and edit config.steps."
    schemas["WorkflowDocument"]["description"] = "A workflow generated ahead of time (console export, legacy schema 1 or a bare step list) as JSON, YAML or Markdown with one code block. Parsed and normalized by code; /workflows/parse previews the result and its notes."
    schemas["Start"]["description"] = "Optionally specify exactly one of workflow_id, template_id, custom steps or a workflow document (runs as submitted); otherwise a KiroCrew Agent plans the tasks (plan=agent, the default; plan=match uses template rules). Preview via workflows/resolve and pin its selection/version before submit. Team runs are shared with current members. Without worker_id the run waits for assignment."
    schemas["Continue"]["description"] = "Retains Session and Workspace. Creates immutable Attempts; selected Task and downstream Tasks rerun. Earlier tasks must be accepted. rerun (optional): the later tasks this change affects; tasks depending on a rerun task, later review/test tasks and unaccepted tasks are added; the rest keep their accepted results (session.kept)."
    schemas["Worker"]["description"] = "Administrator only. Exactly one owner_id/team_id, or spare=true (a dedicated environment no space uses until it is assigned to a Team). Team requires a dedicated OS boundary; a declaration is not a sandbox."
    schemas["Team"]["description"] = "Administrator only. owner is a username; worker_id is the Team's execution environment (shared, or spare which becomes the Team's own), assigned in the same transaction."
    schemas["User"]["description"] = "Administrator only. worker_id: optional shared execution environment for the user's personal space."
    schemas["Share"]["description"] = "Every Session's agents in a shared environment run as one OS account and can read each other's sources, tool logins and memory."
    schemas["ConnectionUpdate"]["description"] = "Administrator only. A replaced Worker host keeps serving its spaces; the new endpoint is re-validated like a registration (same isolation, boundary not used by another Worker)."
    schemas["Enrollment"]["description"] = ("Administrator only. A one-time code that registers one Worker under name, bound "
                                            "to exactly one owner_id / team_id, or spare=true. The code (kce_…) is returned "
                                            "only once; only its SHA-256 is kept. Lasts expires_in seconds (default 30 min, "
                                            "at most a day); five failed registrations retire it.")
    schemas["Enroll"]["description"] = ("No login: the code is the authority (octopus worker enroll). The platform checks "
                                        "the Worker's /health with its token and applies every rule of POST /v1/workers. A "
                                        "wrong, expired, used or revoked code is 404 enrollment_invalid; 10 requests a "
                                        "minute per source. check=true verifies everything and registers nothing.")
    schemas["Space"]["description"] = "Exactly one of team_id or user_id. A spare environment can only be given to a Team."
    schemas["Rename"]["description"] = "A Team: its owners or an administrator. A Worker: administrator only."
    schemas["TeamArchive"]["description"] = "Administrator only. An archived Team takes no new Sessions or members; its Sessions stay readable and stoppable; its execution environment is kept."
    schemas["TeamEnvironment"]["description"] = "Administrator only. A shared or spare environment. The old shared one stops serving the Team; the Team's own host becomes spare and can serve only this Team until it is redeployed. Old Sessions are not moved: they wait to be reassigned at their next dispatch."
    routes = {
        "/v1/login": {"post": "Login"}, "/v1/logout": {"post": "Empty"}, "/v1/me": {"get": None},
        "/v1/users": {"get": None, "post": "User"}, "/v1/teams": {"get": None, "post": "Team"},
        "/v1/teams/{id}": {"patch": "Rename"},
        "/v1/teams/{id}/members": {"post": "Member", "delete": "Member"},
        "/v1/teams/{id}/archive": {"post": "TeamArchive"}, "/v1/teams/{id}/environment": {"post": "TeamEnvironment"},
        "/v1/workflows": {"get": None, "post": "Workflow"},
        "/v1/workflow-templates": {"get": None}, "/v1/workflows/resolve": {"post": "Resolve"},
        "/v1/workflows/parse": {"post": "WorkflowDocument"},
        "/v1/designs": {"post": "Design"}, "/v1/designs/{id}": {"get": None},
        "/v1/workflows/{id}/versions": {"post": "Workflow"},
        "/v1/workflows/{id}/shares": {"post": "Grant", "delete": "Grant"},
        "/v1/workflows/{id}/archive": {"post": "Archive"},
        "/v1/workers": {"get": None, "post": "Worker"},
        "/v1/admin/boundaries/provision": {"get": None, "post": "ProvisionBoundary"},
        "/v1/workers/enrollments": {"get": None, "post": "Enrollment"}, "/v1/workers/enrollments/{id}": {"delete": "Empty"},
        "/v1/workers/enroll": {"post": "Enroll"},
        "/v1/placement": {"get": None},
        "/v1/admin/environments": {"get": None}, "/v1/admin/audit": {"get": None}, "/v1/admin/metrics": {"get": None},
        "/v1/me/password": {"post": "PasswordChange"}, "/v1/users/{id}/password": {"post": "PasswordReset"},
        "/v1/users/{id}/active": {"post": "Active"}, "/v1/users/{id}/admin": {"post": "AdminFlag"},
        "/v1/admin/users": {"get": None}, "/v1/admin/users/{id}/tokens": {"get": None},
        "/v1/workers/{id}/enabled": {"post": "Enabled"},
        "/v1/workers/{id}/share": {"post": "Share"},
        "/v1/workers/{id}/spaces": {"post": "Space", "delete": "Space"},
        "/v1/workers/{id}/connection": {"post": "ConnectionUpdate"},
        "/v1/workers/{id}/dedicate": {"post": "Empty"},
        "/v1/workers/{id}": {"patch": "Rename"}, "/v1/workers/{id}/retire": {"post": "Empty"},
        "/v1/workers/{id}/capabilities": {"post": None},
        "/v1/workers/{id}/kiro-sign-in": {"get": None, "post": "Empty"},
        "/v1/runs/{id}/takeover": {"post": None},
        "/v1/tokens": {"get": None, "post": "Token"}, "/v1/tokens/{id}": {"delete": "Empty"}, "/v1/tokens/{id}/regenerate": {"post": None},
        "/v1/credentials": {"get": None, "post": "Credential"},
        "/v1/credentials/{id}": {"patch": "CredentialUpdate", "delete": "Empty"},
        "/v1/credentials/{id}/test": {"post": "Empty"},
        "/v1/credentials/{id}/grants": {"post": "CredentialGrant", "delete": "CredentialGrant"},
        "/v1/runs": {"get": None, "post": "Start"}, "/v1/runs/{id}": {"get": None},
        "/v1/requests": {"get": None}, "/v1/requests/{id}": {"get": None},
        "/v1/policies": {"get": None, "post": "Policy"}, "/v1/policies/{id}": {"delete": "Empty"},
        "/v1/quotas": {"get": None, "post": "Quota"},
        "/v1/runs/{id}/marks": {"post": "Marks"},
        "/v1/runs/{id}/shares": {"get": None, "post": "Share"}, "/v1/runs/{id}/shares/{share}": {"delete": "Empty"},
        "/v1/runs/{id}/public-shares": {"get": None, "post": "PublicShare"},
        "/v1/runs/{id}/public-shares/{share}": {"delete": "Empty"}, "/v1/runs/{id}/public-shares/{share}/code": {"post": "Empty"},
        "/v1/runs/{id}/events": {"get": None}, "/v1/runs/{id}/export": {"get": None},
        "/v1/runs/{id}/crew-entry": {"post": "Empty"}, "/v1/runs/{id}/preview": {"post": "Empty"},
        "/v1/runs/{id}/observe": {"get": None}, "/v1/runs/{id}/chat-changes": {"get": None},
        "/v1/runs/{id}/revisions": {"get": None}, "/v1/runs/{id}/revisions/diff": {"get": None},
        "/v1/runs/{id}/report": {"get": None}, "/v1/runs/{id}/attempts/{attempt}/diff": {"get": None},
        "/v1/runs/{id}/bundle": {"post": "Empty"}, "/v1/runs/{id}/sync": {"post": None},
        "/v1/runs/{id}/approvals": {"get": None}, "/v1/runs/{id}/approvals/answer": {"post": "ApprovalAnswer"},
        "/v1/runs/{id}/revisions/{version}": {"get": None},
        "/v1/runs/{id}/changes": {"get": None},
        "/v1/runs/{id}/archive": {"post": "Empty"},
        "/v1/runs/{id}/credentials": {"post": "Rebind"},
        "/v1/runs/{id}/app": {"get": None, "post": "AppAction"}, "/v1/runs/{id}/app/open": {"post": "Empty"},
        "/v1/runs/{id}/recover": {"post": "Recover"},

        "/v1/runs/{id}/publish": {"post": "Publish"},
        "/v1/runs/{id}/publish-policy": {"post": "PublishPolicy"},
        "/v1/runs/{id}/knowledge/forget": {"post": "KnowledgeForget"},
        "/v1/runs/{id}/steer": {"post": "Steer"},
        "/v1/runs/{id}/continue": {"post": "Continue"}, "/v1/runs/{id}/assign-worker": {"post": "AssignWorker"},
        **{"/v1/runs/{id}/" + action: {"post": "Control"} for action in ["stop", "close", "reopen", "delete"]},
    }
    # Execution environment management (D28): one place for administrators, also in the console's 「执行环境」 dialog.
    admin = {("/v1/admin/environments", "get"), ("/v1/admin/audit", "get"), ("/v1/workers", "post"), ("/v1/workers/{id}/enabled", "post"),
             ("/v1/workers/{id}/share", "post"), ("/v1/workers/{id}/spaces", "post"),
             ("/v1/workers/{id}/spaces", "delete"), ("/v1/workers/{id}/connection", "post"),
             ("/v1/workers/{id}/dedicate", "post"), ("/v1/teams", "post"), ("/v1/teams/{id}/archive", "post"),
             ("/v1/teams/{id}/environment", "post"), ("/v1/workers/{id}", "patch"), ("/v1/workers/{id}/retire", "post"),
             ("/v1/workers/enrollments", "get"), ("/v1/workers/enrollments", "post"), ("/v1/workers/enrollments/{id}", "delete"),
             ("/v1/admin/boundaries/provision", "get"), ("/v1/admin/boundaries/provision", "post")}
    users_admin = {("/v1/users/{id}/password", "post"), ("/v1/users/{id}/active", "post"), ("/v1/users/{id}/admin", "post"),
                   ("/v1/admin/users", "get"), ("/v1/admin/users/{id}/tokens", "get")}
    paths = {}
    for path, methods in routes.items():
        paths[path] = {}
        for method, schema in methods.items():
            op = {"operationId": method + "_" + path.strip("/").replace("/", "_").replace("{id}", "id"),
                  "responses": {"200": {"description": "Result. Accepted is not execution completion.",
                                       "content": {"application/json": {"schema": {}}}},
                                "201": {"description": "Created"},
                  **{str(code): {"description": label, "content": {"application/json": {"schema": ref("Error")}}}
                     for code, label in [(400, "Invalid input"), (401, "Unauthorized"), (403, "Forbidden"),
                                         (404, "Inaccessible"), (409, "Conflict"), (429, "Rate limited"), (503, "Unavailable")]}}}
            if "{id}" in path:
                op["parameters"] = [{"in": "path", "name": "id", "required": True, "schema": text}]
            if schema:
                op["requestBody"] = {"required": True, "content": {"application/json": {"schema": ref(schema)}}}
            if path in ("/v1/login", "/v1/workers/enroll"):
                op["security"] = []
            if (path, method) in admin:
                op["tags"] = ["执行环境（管理员）"]
            if (path, method) in users_admin:
                op["tags"] = ["用户与权限（管理员）"]
                op["description"] = ("Administrator only (admin scope). Deactivating or resetting a user revokes all of "
                                     "their tokens; the last active admin and a Team's only active owner are protected.")
            if path == "/v1/admin/metrics":
                op["description"] = ("Administrator only. The platform's counts now (Sessions running, waiting for a Worker or "
                                     "a quota, Attempts running, Workers enabled and unhealthy, demos running, takeovers "
                                     "moving, public shares active), as the API's EMF log line carries them every minute "
                                     "(namespace KiroCrewOn).")
            if path == "/v1/admin/environments":
                op["description"] = "Administrator only. Every execution environment with its scope, owner, Team or shared spaces (with names), enabled, active_runs and live health (reachable=false when its Worker does not answer within a few seconds; never fails the listing). No tokens."
            if path.endswith("/crew-entry"):
                op["description"] = "Execute scope. Opens the existing native parent Session through its authorized Worker. On-demand sensitive login URL; no-store. Does not create, resume, mutate or dispatch a Run. Native login covers the Worker's Gateway boundary, not a single run."
            if path == "/v1/runs/{id}/archive":
                op["description"] = ("Read scope. A 10-minute link to download the Session's source as a zip: every file and "
                                     ".git, without dependency or cache directories, .env files or symlinks (200 MB limit); "
                                     "OCTOPUS-ARCHIVE.txt inside lists what was left out and how to start the project locally. "
                                     "Each download re-checks that the link's holder may still read the Session.")
            if path == "/v1/runs/{id}/credentials":
                op["tags"] = ["代码仓库凭证"]
                op["description"] = ("Execute scope. Change (or, with null, remove) the Session's source credential: its creator, "
                                     "a Team owner or an admin, with a credential they may use for the Session's repository.")
            if path.startswith("/v1/credentials"):
                op["tags"] = ["代码仓库凭证"]
                op["description"] = {
                    "/v1/credentials": "GitHub / GitLab credentials the caller may use or manage (personal, Team, platform). "
                                       "The secret is write-only: no response, export or audit row ever carries it.",
                    "/v1/credentials/{id}": "Manage scope: rename, change the repository range or access, disable, or rotate "
                                            "the secret (same id).",
                    "/v1/credentials/{id}/test": "Manage scope. Asks the host (GitHub GET /user, GitLab GET /api/v4/user) "
                                                 "whose token it is and when it expires.",
                    "/v1/credentials/{id}/grants": "Administrator only. Grant a platform credential to a Team or a user."}[path]
            if path == "/v1/runs/{id}/app":
                op["description"] = ({"get": "Read scope. The Session's remote preview: status (stopped / installing / starting / "
                                             "running / failed / unsupported), port, error, the last 200 log lines, who started it, "
                                             "last access and when it stops for idleness (30 minutes).",
                                      "post": "Execute scope. start runs the Session's app in its execution environment "
                                              "(.octopus/preview.json, or detected from package.json / index.html); at most "
                                              "3 per environment; refused while the Session runs tasks. stop ends it."}[method])
            if path == "/v1/runs/{id}/app/open":
                op["description"] = ("Read scope. A single-use 10-minute link to the preview domain; following it sets an "
                                     "8-hour cookie for that domain only. Every request is re-checked against the holder's "
                                     "access to the Session.")
            if path == "/v1/runs/{id}/changes":
                op["description"] = ("Read scope. The Session's change journal, newest first: every write by a user (console, "
                                     "Coding Agent via MCP, API) or the system (engine, worker receipt, kirocrew planning, "
                                     "recovery) with actor, changes {field: [before, after]} (long text as chars + sha256) "
                                     "and reason. Query: who=user|system, kind=<prefix>, before=<id>, limit=1-200.")
            if path == "/v1/runs/{id}/chat-changes":
                op["description"] = ("Read scope (D73). What the workspace holds that the branch's candidate has not — files changed or "
                                     "created in the native chat since the platform last looked — as {files, count, commits}. It only "
                                     "looks: a continue commits them first; the console's continue dialog shows a reminder from it.")
            if "/revisions" in path:
                op["description"] = {
                    "/v1/runs/{id}/revisions": "Read scope. Every revision of the Session, oldest first: kind (created / planned / continued / recovered), summary, actor; older revisions are rebuilt from what they recorded (reconstructed).",
                    "/v1/runs/{id}/revisions/diff": "Read scope. Requirement and per-task differences between two revisions (default: the latest and the one before it): changed fields as [before, after] and prompt line operations ' ', '-', '+'.",
                    "/v1/runs/{id}/revisions/{version}": "Read scope. One revision with the full snapshot of the requirement and every task."}[path]
                if path.endswith("/diff"):
                    op["parameters"] += [{"in": "query", "name": key, "schema": {"type": "integer", "minimum": 1}} for key in ("from", "to")]
                if path.endswith("{version}"):
                    op["parameters"].append({"in": "path", "name": "version", "required": True, "schema": {"type": "integer", "minimum": 1}})
                    op["operationId"] = "get_v1_runs_id_revisions_version"
            if path.endswith("/observe"):
                op["description"] = "Read scope. The run's native KiroCrew Session (coordinator and sub-agent messages, running state) read through its Worker; no native login is issued, so Team members on a shared environment can watch it too. Newest page first; pass before=next_before for older messages."
                op["parameters"] += [{"in": "query", "name": "before", "schema": {"type": "integer", "minimum": 0}},
                                     {"in": "query", "name": "limit", "schema": {"type": "integer", "minimum": 1, "maximum": 100}}]
            if path == "/v1/admin/audit":
                op["description"] = "Administrator only (admin scope). Administration and admin oversight of others' Sessions, newest first."
                op["parameters"] = [{"in": "query", "name": "before", "schema": {"type": "integer", "minimum": 0}},
                                    {"in": "query", "name": "limit", "schema": {"type": "integer", "minimum": 1, "maximum": 200}}]
            if path == "/v1/runs" and method == "get":
                op["parameters"] = [{"in": "query", "name": "all", "schema": {"enum": ["1"]},
                                     "description": "Elevated admins only: every Session instead of their own and their Teams'."},
                                    {"in": "query", "name": "pinned", "schema": {"enum": ["1"]},
                                     "description": "Only the Sessions the caller pinned (each item's marks say pinned / starred)."},
                                    {"in": "query", "name": "starred", "schema": {"enum": ["1"]},
                                     "description": "Only the Sessions the caller starred."},
                                    {"in": "query", "name": "status", "schema": {"type": "string"},
                                     "description": "One Session status, or several separated by commas."},
                                    {"in": "query", "name": "q", "schema": {"type": "string", "minLength": 1, "maxLength": 200},
                                     "description": "Full text, every word: id, name, requirement, the plan, task names and prompts, "
                                                    "each task's latest conclusion and error (D56)."},
                                    {"in": "query", "name": "lifecycle", "schema": {"enum": ["open", "closed"]}},
                                    {"in": "query", "name": "space", "schema": {"type": "string"},
                                     "description": "personal, or a Team id."},
                                    {"in": "query", "name": "repo", "schema": {"type": "string", "maxLength": 200},
                                     "description": "Part of host/owner/repo; https, ssh and git@host:path addresses compare alike."},
                                    {"in": "query", "name": "branch", "schema": {"type": "string", "maxLength": 200},
                                     "description": "Part of the source ref or the publish branch."},
                                    {"in": "query", "name": "stage", "schema": {"enum": ["plan", "implement", "review", "test", "deliver",
                                                                                         "design", "done"]},
                                     "description": "The kind of the first task not yet accepted; done when all are."},
                                    {"in": "query", "name": "tool", "schema": {"enum": ["kiro", "claude", "codex", "opencode"]},
                                     "description": "Any task's tool."},
                                    {"in": "query", "name": "client", "schema": {"enum": ["console", "cli", "claude", "kiro", "codex", "kiro-ide", "api"]},
                                     "description": "The Request's client (D55); Sessions from before have none."},
                                    {"in": "query", "name": "submitter", "schema": {"type": "string"}, "description": "Username or user id."},
                                    {"in": "query", "name": "since", "schema": {"type": "string"},
                                     "description": "Created at or after: Unix seconds or YYYY-MM-DD (UTC)."},
                                    {"in": "query", "name": "until", "schema": {"type": "string"},
                                     "description": "Created at or before: Unix seconds, or through YYYY-MM-DD (UTC)."},
                                    {"in": "query", "name": "page", "schema": {"enum": ["1"]},
                                     "description": "Answer {items, next, total} instead of a list; pass next back as cursor."},
                                    {"in": "query", "name": "cursor", "schema": {"type": "string"}},
                                    {"in": "query", "name": "limit", "schema": {"type": "integer", "minimum": 1, "maximum": 200},
                                     "description": "At most this many, newest first."},
                                    {"in": "query", "name": "view", "schema": {"enum": ["list"]},
                                     "description": "What a list needs: tasks by name and state, the plan without its prose, the requirement's first "
                                                    "200 characters; no usage, knowledge, credentials or verification (the session "
                                                    "page has them)."}]
            if path == "/v1/runs/{id}/bundle":
                op["description"] = ("Read scope. A 10-minute link to the Session's branch as a git bundle (D58 pull): GET it, "
                                     "add ?full=1 for the whole history; headers X-Pull-Head / -Base / -Branch / -Full. 409 "
                                     "bundle_secret_found when a commit on the branch looks like a credential or adds a .env file.")
            if path == "/v1/runs/{id}/sync":
                op["description"] = ("Execute scope. Body: a git bundle (application/octet-stream, up to 20 MB) of local commits on top "
                                     "of the Session's head. Query: request_id, expected_version, old (the Session head the commits "
                                     "build on), head, note. The Worker moves the branch only for a fast-forward with nothing that "
                                     "looks like a credential; the last task that writes code gets a synced Attempt whose checks run, "
                                     "and the tasks after it run again. 409 sync_not_fast_forward, sync_secret_found, sync_dirty, busy, "
                                     "worker_outdated.")
                op["requestBody"] = {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
                op["parameters"] += [{"in": "query", "name": key, "required": key != "note", "schema": text}
                                     for key in ("request_id", "expected_version", "old", "head", "note")]
            if path == "/v1/runs/{id}/attempts/{attempt}/diff":
                op["description"] = ("Read scope. What the Attempt's candidate changed against the one before it (or the Session's "
                                     "baseline): files with line counts, and the unified diff clipped at 200 KB / 4000 lines "
                                     "(truncated). 404 no_candidate; 409 candidate_secret for a candidate the secret check kept off "
                                     "the branch, worker_outdated for an older Worker.")
                op["parameters"].append({"in": "path", "name": "attempt", "required": True, "schema": text})
                op["operationId"] = "get_v1_runs_id_attempts_attempt_diff"
            if path.startswith("/v1/runs/{id}/public-shares"):   # D75
                op["description"] = {
                    ("/v1/runs/{id}/public-shares", "get"): "Read scope. The Session's public links (D75) — with their access codes "
                    "— for its creator, a Team owner or an administrator; available is false without a preview domain.",
                    ("/v1/runs/{id}/public-shares", "post"): "Execute scope. A link anyone on the Internet can open, on the Session's "
                    "own preview origin: its pages (kind pages; entry, else the first page) or its running demo (kind app), with a "
                    "four-digit access code (mode code) or open; expires 1d, 7d (default), 30d or never. Answers the link (url) and "
                    "code. Wrong codes: 5 per visitor in 10 minutes, 30 per share in an hour locks it for an hour. Audited.",
                    ("/v1/runs/{id}/public-shares/{share}", "delete"): "Execute scope. Revoke a public link: it ends at once. Audited.",
                    ("/v1/runs/{id}/public-shares/{share}/code", "post"): "Execute scope. A new access code: what the old one opened asks "
                    "again, and a lock ends. Audited."}[(path, method)]
                if "{share}" in path:
                    op["parameters"].append({"in": "path", "name": "share", "required": True, "schema": text})
            if path.startswith("/v1/runs/{id}/shares"):   # D64
                op["description"] = {
                    ("/v1/runs/{id}/shares", "get"): "Read scope. Who the Session is shared with — for its creator, a Team owner or an administrator.",
                    ("/v1/runs/{id}/shares", "post"): "Manage scope. Share the Session with a person (username), a Team the caller is in (id) or "
                    "the whole company (everyone, viewer only), as a viewer (reads) or a collaborator (what an ordinary Team member is). "
                    "Sharing again changes the role. Audited.",
                    ("/v1/runs/{id}/shares/{share}", "delete"): "Manage scope. Stop sharing; it takes effect on the next request (an open "
                    "event stream closes within a second). Audited."}[(path, method)]
                if "{share}" in path:
                    op["parameters"].append({"in": "path", "name": "share", "required": True, "schema": text})
            if path.startswith("/v1/policies"):
                op["description"] = {
                    ("/v1/policies", "get"): "Read scope. The caller's own rules and those of their Teams (team_id: one Team's): mapping preferences, and admission rules written before D70 (no effect; listed so they can be deleted); deletable says whether the caller may remove it.",
                    ("/v1/policies", "post"): "Manage scope. A rule for the caller's own Sessions (scope personal) or a Team's (scope team, Team owner or administrator; not an archived Team). Only mapping preferences: admission rules are refused (400 admission_removed, D70 — every Session starts once planned). Audited.",
                    ("/v1/policies/{id}", "delete"): "Manage scope. Remove one of the caller's rules, or a Team's as its owner or an administrator. Audited."}[(path, method)]
                if path == "/v1/policies":
                    op["parameters"] = [{"in": "query", "name": "team_id", "schema": text}] if method == "get" else op.get("parameters", [])
            if path == "/v1/quotas":
                op["description"] = {
                    "get": "Read scope. The caller's limit on running tasks and how many run (mine), their Teams' (teams; manage says "
                           "whether the caller may change it); an administrator also sees the default per person and each person's own.",
                    "post": "Manage scope. Set or remove (running null) a limit on running tasks: the default per person or a person's "
                            "own (administrators), a Team's (its owner or an administrator). Over a limit, a Session's next task waits "
                            "its turn. Audited."}[method]
            if path.startswith("/v1/requests"):
                op["description"] = {
                    "/v1/requests": "Read scope. Every submission with a Session the caller can read, newest first: client (console / claude / kiro / codex / kiro-ide / api), requirement, source, explicit choices, the policy it started under (admission mode and basis) and its Sessions; status is its latest Session's.",
                    "/v1/requests/{id}": "Read scope. One Request with its Sessions (404 unless the caller can read one of them)."}[path]
                if path == "/v1/requests":
                    op["parameters"] = [{"in": "query", "name": "status", "schema": {"type": "string"}, "description": "Its latest Session's status."},
                                        {"in": "query", "name": "q", "schema": {"type": "string", "minLength": 1, "maxLength": 200},
                                         "description": "Part of the Request id or requirement."},
                                        {"in": "query", "name": "client", "schema": {"enum": ["console", "cli", "claude", "kiro", "codex", "kiro-ide", "api"]}},
                                        {"in": "query", "name": "limit", "schema": {"type": "integer", "minimum": 1, "maximum": 200}}]
            if path.endswith("/dedicate"):
                op["description"] = "Administrator only. A shared Worker host serving exactly one Team becomes that Team's dedicated environment; every member then opens its native KiroCrew entry."
            if path.endswith("/events"):
                op["description"] = "SSE connections last up to 30 seconds. Reconnect with last event ID; access rechecked each batch."
                op["parameters"] += [{"in": "query", "name": "after", "schema": {"type": "integer", "minimum": 0}},
                                     {"in": "query", "name": "stream", "schema": {"enum": ["1"]}},
                                     {"in": "header", "name": "Last-Event-ID", "schema": text}]
                op["responses"]["200"]["content"]["text/event-stream"] = {"schema": text}
            paths[path][method] = op
    return {"openapi": "3.1.0", "info": {"title": "Octopus Team API", "version": __version__},
            "security": [{"bearer": []}, {"session": []}], "servers": [{"url": "/"}], "paths": paths,
            "components": {"securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer"},
                "session": {"type": "apiKey", "in": "cookie", "name": "__Host-octopus_session",
                            "description": "Set by POST /v1/login (D67). Used only without an Authorization header; "
                                           "a write on it alone needs this console's Origin."}}, "schemas": schemas}}
