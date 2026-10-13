"""Discoverable HTTP inputs for coding clients."""
from . import __version__


def openapi():
    def obj(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    def ref(name):
        return {"$ref": "#/components/schemas/" + name}
    text, integer = {"type": "string"}, {"type": "integer", "minimum": 1}
    execution = {"tool": {"enum": ["kiro", "claude", "codex", "opencode"]},
                 "model": {"type": "string", "default": "auto"},
                 "effort": {"enum": ["", "low", "medium", "high", "xhigh", "max"]}, "prompt": text}
    command = {"request_id": {"type": "string", "minLength": 8, "maxLength": 100}, "expected_version": integer}
    step_list = {"type": "array", "items": ref("Step"), "minItems": 1, "maxItems": 8}
    listed = lambda items: {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": items}
    contract = {"kind": {"enum": ["implement", "review", "test", "deliver", "design"]}, "deliverables": listed(20),
                "acceptance": listed(20), "verify": listed(10), "depends_on": listed(8), "paths": listed(20)}
    schemas = {
        "Step": obj({"id": text, "name": text, **execution, **contract}, ["id", "name", "tool", "prompt"]),
        "Execution": obj({"name": text, **execution}),
        "Source": obj({"type": {"enum": ["new", "git"]}, "url": text, "ref": text}, ["type"]),
        "Login": obj({"username": text, "password": {"type": "string", "format": "password"}}, ["username", "password"]),
        "User": obj({"username": text, "password": {"type": "string", "format": "password"}, "admin": {"type": "boolean"},
                     "worker_id": text}, ["username", "password"]),
        "Team": obj({"name": text, "owner": text, "worker_id": text}, ["name", "owner", "worker_id"]),
        "Member": obj({"username": text, "role": {"enum": ["owner", "member"]}}, ["username"]),
        "Workflow": obj({"name": text, "steps": step_list, "expected_version": integer}, ["name", "steps"]),
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
                       "items": {"enum": ["read", "execute", "manage", "admin"]}}}),
        "Resolve": obj({"input": text, "name": text, "workflow_id": text, "workflow_version": integer,
                        "template_id": text, "steps": step_list, "team_id": text}, ["input"]),
        "Design": obj({"request_id": command["request_id"], "input": text, "worker_id": text,
                       "workflow_id": text, "workflow_version": integer, "template_id": text,
                                  "steps": step_list, "team_id": text}, ["request_id", "input", "worker_id"]),
        "RecoveryPolicy": obj({"enabled": {"type": "boolean", "default": True},
                               "max_cycles": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2}}),
        "WorkflowDocument": obj({"content": text, "format": {"enum": ["json", "yaml", "markdown"]}}, ["content"]),
        "Start": obj({"request_id": command["request_id"], "input": text, "name": text, "workflow_id": text,
                      "template_id": text, "document": ref("WorkflowDocument"), "plan": {"enum": ["agent", "match"]},
                      "workflow_version": integer, "steps": step_list, "team_id": text, "worker_id": text,
                       "source": ref("Source"), "recovery": ref("RecoveryPolicy")}, ["request_id", "input", "source"]),
        "Continue": obj({**command, "task_id": text, "input": text, "execution": ref("Execution"),
                         "recovery": ref("RecoveryPolicy"), "rerun": {"type": "array", "items": text, "maxItems": 8}},
                        ["request_id", "expected_version", "task_id"]),
        "Control": obj({**command, "confirmation": text}, ["request_id", "expected_version"]),
        "AssignWorker": obj({**command, "worker_id": text}, ["request_id", "expected_version", "worker_id"]),
        "Empty": obj({}), "Error": obj({"error": text, "code": text}, ["error", "code"]),
    }
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
        "/v1/admin/environments": {"get": None}, "/v1/admin/audit": {"get": None},
        "/v1/me/password": {"post": "PasswordChange"}, "/v1/users/{id}/password": {"post": "PasswordReset"},
        "/v1/users/{id}/active": {"post": "Active"}, "/v1/users/{id}/admin": {"post": "AdminFlag"},
        "/v1/admin/users": {"get": None}, "/v1/admin/users/{id}/tokens": {"get": None},
        "/v1/workers/{id}/enabled": {"post": "Enabled"},
        "/v1/workers/{id}/share": {"post": "Share"},
        "/v1/workers/{id}/spaces": {"post": "Space", "delete": "Space"},
        "/v1/workers/{id}/connection": {"post": "ConnectionUpdate"},
        "/v1/workers/{id}/dedicate": {"post": "Empty"},
        "/v1/workers/{id}": {"patch": "Rename"}, "/v1/workers/{id}/retire": {"post": "Empty"},
        "/v1/workers/{id}/capabilities": {"get": None},
        "/v1/tokens": {"get": None, "post": "Token"}, "/v1/tokens/{id}": {"delete": "Empty"},
        "/v1/credentials": {"get": None, "post": "Credential"},
        "/v1/credentials/{id}": {"patch": "CredentialUpdate", "delete": "Empty"},
        "/v1/credentials/{id}/test": {"post": "Empty"},
        "/v1/credentials/{id}/grants": {"post": "CredentialGrant", "delete": "CredentialGrant"},
        "/v1/runs": {"get": None, "post": "Start"}, "/v1/runs/{id}": {"get": None},
        "/v1/runs/{id}/events": {"get": None}, "/v1/runs/{id}/export": {"get": None},
        "/v1/runs/{id}/crew-entry": {"post": "Empty"}, "/v1/runs/{id}/preview": {"post": "Empty"},
        "/v1/runs/{id}/observe": {"get": None},
        "/v1/runs/{id}/revisions": {"get": None}, "/v1/runs/{id}/revisions/diff": {"get": None},
        "/v1/runs/{id}/revisions/{version}": {"get": None},
        "/v1/runs/{id}/changes": {"get": None},
        "/v1/runs/{id}/archive": {"post": "Empty"},
        "/v1/runs/{id}/app": {"get": None, "post": "AppAction"}, "/v1/runs/{id}/app/open": {"post": "Empty"},
        "/v1/runs/{id}/recover": {"post": "Control"},
        "/v1/runs/{id}/continue": {"post": "Continue"}, "/v1/runs/{id}/assign-worker": {"post": "AssignWorker"},
        **{"/v1/runs/{id}/" + action: {"post": "Control"} for action in ["stop", "close", "reopen", "delete"]},
    }
    # Execution environment management (D28): one place for administrators, also in the console's 「执行环境」 dialog.
    admin = {("/v1/admin/environments", "get"), ("/v1/admin/audit", "get"), ("/v1/workers", "post"), ("/v1/workers/{id}/enabled", "post"),
             ("/v1/workers/{id}/share", "post"), ("/v1/workers/{id}/spaces", "post"),
             ("/v1/workers/{id}/spaces", "delete"), ("/v1/workers/{id}/connection", "post"),
             ("/v1/workers/{id}/dedicate", "post"), ("/v1/teams", "post"), ("/v1/teams/{id}/archive", "post"),
             ("/v1/teams/{id}/environment", "post"), ("/v1/workers/{id}", "patch"), ("/v1/workers/{id}/retire", "post")}
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
            if path == "/v1/login":
                op["security"] = []
            if (path, method) in admin:
                op["tags"] = ["执行环境（管理员）"]
            if (path, method) in users_admin:
                op["tags"] = ["用户与权限（管理员）"]
                op["description"] = ("Administrator only (admin scope). Deactivating or resetting a user revokes all of "
                                     "their tokens; the last active admin and a Team's only active owner are protected.")
            if path == "/v1/admin/environments":
                op["description"] = "Administrator only. Every execution environment with its scope, owner, Team or shared spaces (with names), enabled, active_runs and live health (reachable=false when its Worker does not answer within a few seconds; never fails the listing). No tokens."
            if path.endswith("/crew-entry"):
                op["description"] = "Execute scope. Opens the existing native parent Session through its authorized Worker. On-demand sensitive login URL; no-store. Does not create, resume, mutate or dispatch a Run. Native login covers the Worker's Gateway boundary, not a single run."
            if path == "/v1/runs/{id}/archive":
                op["description"] = ("Read scope. A 10-minute link to download the Session's source as a zip: every file and "
                                     ".git, without dependency or cache directories, .env files or symlinks (200 MB limit); "
                                     "OCTOPUS-ARCHIVE.txt inside lists what was left out and how to start the project locally. "
                                     "Each download re-checks that the link's holder may still read the Session.")
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
                                     "description": "Elevated admins only: every Session instead of their own and their Teams'."}]
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
            "security": [{"bearer": []}], "servers": [{"url": "/"}], "paths": paths,
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}, "schemas": schemas}}
