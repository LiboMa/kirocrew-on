"""Shared use cases for HTTP, Web, CLI and MCP. Authorization lives here."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import socket
import time
import unicodedata

from .db import dumps, uid
from .security import Error, elevated, require

TOOLS = ("kiro", "claude", "codex", "opencode")
ROLES = {"viewer": 1, "runner": 2, "editor": 3, "owner": 4}
# Statuses in which an open Session is not running on its Worker (it can be continued).
SETTLED = ("failed", "blocked", "completed", "stopped", "waiting_worker")
# What the admin environment listing shows of a live /health (no boundary or principal hashes).
HEALTH_FIELDS = ("isolation", "crew_entry", "crew_origin", "version")


def text(value, field, maximum=12000, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum
            and not any(ord(c) < 32 and c not in "\n\r\t" for c in value),
            f"{field} 须为 {minimum}–{maximum} 个字符")
    return value.strip()


def name(value):
    value = " ".join(unicodedata.normalize("NFKC", text(value, "名称", 80)).split())
    require(not any(unicodedata.category(c) in {"Cc", "Cf"} for c in value), "名称包含不可见字符")
    return value


def steps(value):
    require(isinstance(value, list) and 1 <= len(value) <= 8, "流程须包含 1–8 个顺序任务")
    seen, result = set(), []
    for step in value:
        require(isinstance(step, dict) and set(step) <= {"id", "name", "prompt", "tool", "model", "effort"},
                "不支持的 Task 字段")
        sid = step.get("id")
        require(isinstance(sid, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,47}", sid)
                and sid not in seen, "Task ID 无效或重复")
        seen.add(sid)
        tool = step.get("tool")
        require(tool in TOOLS, "不支持的 Coding 工具")
        effort = step.get("effort", "")
        require(effort in ("", "low", "medium", "high", "xhigh", "max"), "Effort 无效")
        require(tool != "opencode" or not effort, "当前 OpenCode 接入未验证 Effort 覆盖")
        result.append({"id": sid, "name": name(step.get("name")), "tool": tool,
                       "model": text(step.get("model", "auto"), "Model", 200),
                       "effort": effort, "prompt": text(step.get("prompt"), "任务说明", 8000)})
    return result


def member(db, user, team, owner=False):
    row = db.execute("SELECT role FROM members WHERE user_id=? AND team_id=?", (user, team)).fetchone()
    require(row and (not owner or row["role"] == "owner"), "无此 Team 权限", 403, "forbidden")
    return row["role"]


def workflow_access(db, user, wid, role="viewer"):
    flow = db.execute("SELECT * FROM workflows WHERE id=?", (wid,)).fetchone()
    require(flow is not None, "Workflow 不存在或无权限", 404, "not_found")
    allowed = 4 if flow["owner_id"] == user else 0
    for grant in db.execute("SELECT * FROM grants WHERE workflow_id=?", (wid,)):
        match = grant["kind"] == "user" and grant["principal"] == user
        if grant["kind"] == "team":
            match = db.execute("SELECT 1 FROM members WHERE team_id=? AND user_id=?",
                               (grant["principal"], user)).fetchone()
        if match:
            allowed = max(allowed, ROLES[grant["role"]])
    require(allowed >= ROLES[role], "Workflow 不存在或无权限", 404, "not_found")
    return dict(flow), next(k for k, v in ROLES.items() if v == allowed)


def space_workers(db, user, team):
    """Enabled Workers usable in a space: dedicated by owner/team, shared through worker_spaces; spare never (D28)."""
    if team:
        query = ("SELECT w.* FROM workers w WHERE w.enabled=1 AND ((w.scope='dedicated' AND w.team_id=?) OR "
                 "(w.scope='shared' AND EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.team_id=?)))")
    else:
        query = ("SELECT w.* FROM workers w WHERE w.enabled=1 AND ((w.scope='dedicated' AND w.owner_id=?) OR "
                 "(w.scope='shared' AND EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.user_id=?)))")
    return db.execute(query + " ORDER BY w.name", (team or user, team or user)).fetchall()


def assign_spare(db, worker, team):
    """A spare execution environment becomes the Team's dedicated one; only once (D28, T4)."""
    changed = db.execute("UPDATE workers SET scope='dedicated', team_id=?, owner_id=NULL "
                         "WHERE id=? AND scope='spare' AND enabled=1", (team, worker)).rowcount
    require(changed == 1, "该执行环境不可用或已分配", 409, "worker_unavailable")


def team_environment(db, team):
    """The Team's execution environment, enabled or not: its dedicated Worker or the shared one serving it (T6)."""
    return db.execute("SELECT w.* FROM workers w WHERE (w.scope='dedicated' AND w.team_id=?) OR (w.scope='shared' AND "
                      "EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.team_id=?)) ORDER BY w.name",
                      (team, team)).fetchone()


def give_environment(db, worker, team):
    """Give a Team an execution environment: a shared Worker serves it, a spare one becomes its own."""
    row = db.execute("SELECT scope FROM workers WHERE id=? AND enabled=1", (worker,)).fetchone()
    require(row is not None and row["scope"] in {"shared", "spare"}, "该执行环境不可用或已分配", 409, "worker_unavailable")
    require(team_environment(db, team) is None, "该 Team 已有执行环境（含已停用的）", 409, "team_has_environment")
    if row["scope"] == "shared":
        db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (worker, team))
    else:
        assign_spare(db, worker, team)


def run_access(db, user, rid, owner=False):
    row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    require(row is not None, "Session 不存在或无权限", 404, "not_found")
    shared = row["team_id"] and db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?",
                                         (row["team_id"], user)).fetchone()
    allowed = (bool(shared and (not owner or shared["role"] == "owner" or row["owner_id"] == user))
               if row["team_id"] else row["owner_id"] == user)
    require(allowed,
            "Session 不存在或无权限", 404, "not_found")
    return dict(row)


def _probe(config):
    """One Worker's live health for the admin listing; whatever it answers (or fails with) stays in its own entry."""
    from .transport import worker_request
    try:
        health = worker_request(config, "/health", timeout=4)
    except Error as exc:
        return {"reachable": False, "error": str(exc)}
    except Exception as exc:  # a non-HTTP banner, a truncated body, a config without a token
        return {"reachable": False, "error": f"Worker 健康检查失败（{type(exc).__name__}）"}
    if not isinstance(health, dict):
        return {"reachable": False, "error": "Worker 健康检查返回的不是对象"}
    return {"reachable": True, **{key: health[key] for key in HEALTH_FIELDS if key in health}}


class Service:
    def __init__(self, database):
        self.db = database

    def teams(self, actor):
        """The actor's Teams; an admin sees every Team (role None where not a member). Each carries its environment."""
        with self.db.read() as db:
            if elevated(actor):
                rows = db.execute("SELECT t.*,m.role FROM teams t LEFT JOIN members m ON m.team_id=t.id AND m.user_id=? "
                                  "ORDER BY t.created", (actor["id"],)).fetchall()
            else:
                rows = db.execute("SELECT t.*,m.role FROM teams t JOIN members m ON t.id=m.team_id "
                                  "WHERE m.user_id=? ORDER BY t.created", (actor["id"],)).fetchall()
            result = []
            for row in rows:
                entry = dict(row)
                entry["members"] = [dict(m) for m in db.execute(
                    "SELECT u.id,u.username,m.role FROM members m JOIN users u ON u.id=m.user_id WHERE m.team_id=?",
                    (row["id"],))]
                entry["environment"] = self._environment(db, row["id"])
                result.append(entry)
            return result

    @staticmethod
    def _environment(db, team):
        row = team_environment(db, team)
        return {"worker_id": row["id"], "name": row["name"], "scope": row["scope"],
                "enabled": bool(row["enabled"])} if row else None

    def workers(self, actor):
        """Workers the actor may see: an admin all; otherwise dedicated ones of their spaces and shared ones serving them."""
        with self.db.read() as db:
            teams = {r["team_id"] for r in db.execute("SELECT team_id FROM members WHERE user_id=?", (actor["id"],))}
            result = []
            for row in db.execute("SELECT * FROM workers").fetchall():
                spaces = [{"team_id": s["team_id"]} if s["team_id"] else {"user_id": s["user_id"]} for s in db.execute(
                    "SELECT team_id,user_id FROM worker_spaces WHERE worker_id=? ORDER BY rowid", (row["id"],))]
                if not elevated(actor):
                    spaces = [x for x in spaces if x.get("user_id") == actor["id"] or x.get("team_id") in teams]
                visible = elevated(actor) or (row["scope"] == "shared" and spaces) or (
                    row["scope"] == "dedicated" and (row["owner_id"] == actor["id"] or row["team_id"] in teams))
                if visible:
                    value = {k: row[k] for k in ("id", "name", "team_id", "owner_id", "enabled", "scope")}
                    value["spaces"] = spaces
                    value["capabilities"] = json.loads(row["config"]).get("capabilities", {})
                    result.append(value)
            return result

    def environments(self, actor):
        """Admin view of every execution environment (D28): bindings, Sessions running on it and live health. Health
        checks run concurrently with a short timeout: an unreachable Worker neither fails nor holds up the listing."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.read() as db:
            users = dict(db.execute("SELECT id,username FROM users").fetchall())
            teams = dict(db.execute("SELECT id,name FROM teams").fetchall())
            active = dict(db.execute(
                "SELECT worker_id,COUNT(*) FROM runs WHERE worker_id IS NOT NULL AND lifecycle='open' AND status NOT IN "
                f"({','.join('?' * len(SETTLED))}) GROUP BY worker_id", SETTLED).fetchall())
            spaces = {}
            for row in db.execute("SELECT worker_id,team_id,user_id FROM worker_spaces ORDER BY rowid"):
                spaces.setdefault(row["worker_id"], []).append(
                    {"team_id": row["team_id"], "name": teams.get(row["team_id"])} if row["team_id"]
                    else {"user_id": row["user_id"], "username": users.get(row["user_id"])})
            rows = db.execute("SELECT * FROM workers ORDER BY rowid").fetchall()
        configs = [json.loads(row["config"]) for row in rows]
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(rows)))) as pool:
            health = list(pool.map(_probe, configs))
        return [{"id": row["id"], "name": row["name"], "scope": row["scope"], "enabled": bool(row["enabled"]),
                 "owner": {"id": row["owner_id"], "username": users.get(row["owner_id"])} if row["owner_id"] else None,
                 "team": {"id": row["team_id"], "name": teams.get(row["team_id"])} if row["team_id"] else None,
                 "spaces": spaces.get(row["id"], []), "url": config.get("url"),
                 "active_runs": active.get(row["id"], 0), "health": probe}
                for row, config, probe in zip(rows, configs, health)]

    def dedicate_worker(self, actor, wid):
        """A shared Worker host serving exactly one Team becomes that Team's dedicated environment again (D30): every
        member then opens its native KiroCrew entry, as in a personal space."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = db.execute("SELECT * FROM workers WHERE id=?", (wid,)).fetchone()
            require(row is not None, "Worker 不存在", 404, "not_found")
            spaces = db.execute("SELECT team_id,user_id FROM worker_spaces WHERE worker_id=?", (wid,)).fetchall()
            require(row["scope"] == "shared" and len(spaces) == 1 and spaces[0]["team_id"],
                    "只有只服务一个 Team 的共享环境可以改回专属环境", 409, "invalid_state")
            # A Team's own Worker must be a dedicated boundary: every member's native login covers its Gateway.
            require(json.loads(row["config"]).get("capabilities", {}).get("isolation") == "dedicated",
                    "该 Worker 运行在平台主机上（trusted-local），不能成为 Team 的专属环境；请使用独立的 Worker 主机",
                    409, "isolation_required")
            team = spaces[0]["team_id"]
            db.execute("DELETE FROM worker_spaces WHERE worker_id=?", (wid,))
            db.execute("UPDATE workers SET scope='dedicated', team_id=?, owner_id=NULL WHERE id=?", (team, wid))
        return {"id": wid, "scope": "dedicated", "team_id": team}

    def share_worker(self, actor, wid, body):
        """Turn a dedicated Worker into a shared execution environment; its current space stays served (D28, T3)."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(body.get("confirm") is True, "设为共享环境需要确认：环境内所有会话的 Agent 以同一系统账号运行，"
                "能互相读取源码、工具登录与记忆", 400, "confirmation_required")
        with self.db.transaction() as db:
            row = db.execute("SELECT * FROM workers WHERE id=?", (wid,)).fetchone()
            require(row is not None, "Worker 不存在", 404, "not_found")
            require(row["scope"] == "dedicated", "只有专属 Worker 可以设为共享环境", 409, "invalid_state")
            # Teams and other users would run as the platform's own account, which reads the database and Worker tokens.
            local = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()
            require(json.loads(row["config"]).get("capabilities", {}).get("host_principal") != local,
                    "平台宿主账号上的 Worker 不能设为共享环境；请使用独立执行账号、容器或主机", 409, "isolation_required")
            if row["team_id"]:
                db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (wid, row["team_id"]))
            else:
                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, row["owner_id"]))
            # The boundary owner is kept: it opens the native entry and re-registers the Worker on reinstall.
            db.execute("UPDATE workers SET scope='shared', team_id=NULL, owner_id=? WHERE id=?",
                       (row["owner_id"] or actor["id"], wid))
        return {"id": wid, "scope": "shared"}

    def worker_space(self, actor, wid, body, remove=False):
        """Add a Team or a user's personal space to a shared execution environment, or take it away."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        team, user = body.get("team_id"), body.get("user_id")
        require(isinstance(team or user, str) and bool(team) != bool(user), "需要且只能指定 team_id 或 user_id")
        with self.db.transaction() as db:
            row = db.execute("SELECT scope FROM workers WHERE id=?", (wid,)).fetchone()
            require(row is not None, "Worker 不存在", 404, "not_found")
            # A spare environment can only be given to a Team, where it becomes that Team's own (T4).
            require(row["scope"] == "shared" or (row["scope"] == "spare" and team and not remove),
                    "只有共享环境可以调整服务空间；空闲环境只能分配给 Team", 409, "invalid_state")
            column, value = ("team_id", team) if team else ("user_id", user)
            if remove:
                changed = db.execute(f"DELETE FROM worker_spaces WHERE worker_id=? AND {column}=?", (wid, value)).rowcount
                require(changed == 1, "该空间不在此共享环境中", 404, "not_found")
            elif team:
                require(db.execute("SELECT 1 FROM teams WHERE id=?", (team,)).fetchone(), "Team 不存在", 404, "not_found")
                give_environment(db, wid, team)
            else:
                require(db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (user,)).fetchone(),
                        "用户不存在", 404, "not_found")
                require(not db.execute("SELECT 1 FROM worker_spaces WHERE worker_id=? AND user_id=?", (wid, user)).fetchone(),
                        "该用户已在此共享环境中", 409, "conflict")
                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, user))
        return {"ok": True}

    def create_team(self, actor, body):
        """Admin only; the Team gets its owner and its execution environment in the same transaction (D28)."""
        require(elevated(actor), "只有平台管理员可以创建 Team", 403, "forbidden")
        title = name(body.get("name"))
        require(isinstance(body.get("worker_id"), str) and body["worker_id"],
                "创建 Team 必须选择执行环境", 400, "worker_required")
        with self.db.transaction() as db:
            require(not db.execute("SELECT 1 FROM teams WHERE name=?", (title,)).fetchone(),
                    "Team 名称已存在", 409, "conflict")
            owner = db.execute("SELECT id FROM users WHERE username=? AND active=1",
                               (text(body.get("owner"), "Team owner", 64).lower(),)).fetchone()
            require(owner is not None, "Team owner 不存在", 404, "not_found")
            tid = uid("team")
            db.execute("INSERT INTO teams VALUES(?,?,?)", (tid, title, time.time()))
            db.execute("INSERT INTO members VALUES(?,?,?)", (tid, owner["id"], "owner"))
            give_environment(db, body["worker_id"], tid)
            role = "owner" if owner["id"] == actor["id"] else None
            return {"id": tid, "name": title, "role": role, "environment": self._environment(db, tid)}

    def membership(self, actor, tid, body, remove=False):
        with self.db.transaction() as db:
            member(db, actor["id"], tid, owner=True)
            target = db.execute("SELECT id FROM users WHERE username=? AND active=1",
                                (text(body.get("username"), "用户名", 64).lower(),)).fetchone()
            require(target is not None, "用户不存在", 404, "not_found")
            role = body.get("role", "member")
            require(role in {"owner", "member"}, "角色无效")
            current = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?",
                                 (tid, target["id"])).fetchone()
            if current and current["role"] == "owner" and (remove or role != "owner"):
                require(db.execute("SELECT count(*) FROM members WHERE team_id=? AND role='owner'",
                                   (tid,)).fetchone()[0] > 1, "不能移除最后一个 Team Owner", 409, "conflict")
            if remove:
                db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, target["id"]))
            else:
                db.execute("INSERT INTO members VALUES(?,?,?) ON CONFLICT(team_id,user_id) DO UPDATE SET role=excluded.role",
                           (tid, target["id"], role))
        return {"ok": True}

    def workflows(self, actor):
        result = []
        with self.db.read() as db:
            for row in db.execute("SELECT id FROM workflows WHERE archived=0 ORDER BY created DESC"):
                try:
                    flow, role = workflow_access(db, actor["id"], row["id"])
                except Error:
                    continue
                version = db.execute("SELECT spec FROM workflow_versions WHERE workflow_id=? AND version=?",
                                     (flow["id"], flow["version"])).fetchone()
                flow.update(role=role, steps=json.loads(version["spec"]),
                            grants=[dict(g) for g in db.execute("SELECT kind,principal,role FROM grants WHERE workflow_id=?",
                                                               (flow["id"],))] if role == "owner" else [])
                result.append(flow)
        return result

    def save_workflow(self, actor, body, wid=None):
        title, spec = name(body.get("name")), steps(body.get("steps"))
        with self.db.transaction() as db:
            if wid:
                flow, _ = workflow_access(db, actor["id"], wid, "editor")
                require(body.get("expected_version") == flow["version"], "Workflow 版本已更新", 409, "version_conflict")
                owner, version = flow["owner_id"], flow["version"] + 1
            else:
                owner, version, wid = actor["id"], 1, uid("flow")
            conflict = db.execute("SELECT id FROM workflows WHERE owner_id=? AND name_key=? AND id!=?",
                                  (owner, title.casefold(), wid)).fetchone()
            require(not conflict, "同一 Owner 下已有同名 Workflow", 409, "conflict")
            if version == 1:
                db.execute("INSERT INTO workflows(id,owner_id,name,name_key,version,created) VALUES(?,?,?,?,?,?)",
                           (wid, owner, title, title.casefold(), version, time.time()))
            else:
                db.execute("UPDATE workflows SET name=?,name_key=?,version=? WHERE id=?",
                           (title, title.casefold(), version, wid))
            db.execute("INSERT INTO workflow_versions VALUES(?,?,?,?,?)",
                       (wid, version, dumps(spec), actor["id"], time.time()))
        return {"id": wid, "name": title, "version": version, "steps": spec}

    def resolve_workflow(self, actor, body):
        """Preview only: no Session, source checkout or Coding task is created."""
        from .intent import catalog, preset_steps, ranked, selection, title_from
        goal = text(body.get("input"), "开发需求", minimum=3)
        choices = [key for key in ("workflow_id", "template_id", "steps", "document") if body.get(key) is not None]
        require(len(choices) <= 1, "只指定 workflow_id、template_id、steps 或 document 中的一项")
        if choices == ["document"]:
            # A workflow generated ahead of time by an Agent runs as submitted, after code normalization.
            from .workflow_import import import_workflow
            document = body["document"]
            require(isinstance(document, dict) and set(document) <= {"content", "format"}, "document 需要 content 与可选 format")
            body, choices = {**body, "steps": import_workflow(document.get("content"), document.get("format"))["steps"]}, ["steps"]
            del body["document"]
        require("workflow_version" not in body or "workflow_id" in choices, "版本必须对应 workflow_id")
        with self.db.read() as db:
            if body.get("team_id"):
                member(db, actor["id"], body["team_id"])
            if "workflow_id" in choices:
                flow, _ = workflow_access(db, actor["id"], body["workflow_id"], "runner")
                require(not flow["archived"], "工作流已归档", 409, "archived")
                version = body.get("workflow_version", flow["version"])
                require(isinstance(version, int) and not isinstance(version, bool) and version > 0, "版本无效")
                saved = db.execute("SELECT spec FROM workflow_versions WHERE workflow_id=? AND version=?",
                                   (flow["id"], version)).fetchone()
                require(saved is not None, "工作流版本不存在", 404, "not_found")
                row = {"kind": "workflow", "id": flow["id"], "version": version,
                       "name": flow["name"], "steps": json.loads(saved["spec"]), "reason": "手动指定工作流"}
            elif "steps" in choices:
                row = {"kind": "custom", "name": "自定义任务", "steps": steps(body["steps"]),
                       "reason": "采用用户或 Chat 编排的任务，不重新匹配"}
            elif "template_id" in choices:
                row = next((t for t in catalog() if t["id"] == body["template_id"]), None)
                require(row is not None, "模板不存在", 404, "not_found")
                row = {**row, "kind": "template", "reason": "手动指定模板"}
        recommendations = []
        if not choices:
            recommendations = ranked(goal, self.workflows(actor))
            named = [r for r in recommendations if r["kind"] == "workflow" and r["score"] >= 1000]
            require(len(named) <= 1, "多个可用工作流名称匹配，请通过 workflow_id 指定", 409, "ambiguous_workflow")
            row = recommendations[0]
        picked = {"steps": row["steps"]} if row["kind"] == "custom" else selection(row)
        return {"input": goal, "name": name(body["name"]) if "name" in body else title_from(goal),
                "workflow_name": row["name"], "steps": steps(preset_steps(row, goal) if row["kind"] == "template" else row["steps"]), "selection": picked,
                "mode": "manual" if choices else "rules", "reason": row["reason"],
                "recommendations": [{"name": r["name"], "reason": r["reason"], "selection": selection(r),
                                     "tasks": len(r["steps"])} for r in recommendations[:5]],
                "notice": "即时匹配基于模板与可访问工作流的关键词，不是 LLM 生成。提交后才准备源码并调用 ACP。"}

    def share(self, actor, wid, body, revoke=False):
        with self.db.transaction() as db:
            workflow_access(db, actor["id"], wid, "owner")
            kind, principal = body.get("kind"), body.get("principal")
            require(kind in {"user", "team"} and isinstance(principal, str), "共享对象无效")
            if kind == "team":
                member(db, actor["id"], principal)
            else:
                require(db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (principal,)).fetchone(),
                        "共享用户不存在", 404, "not_found")
            if revoke:
                db.execute("DELETE FROM grants WHERE workflow_id=? AND kind=? AND principal=?", (wid, kind, principal))
            else:
                role = body.get("role", "viewer")
                require(role in {"viewer", "runner", "editor"}, "共享角色无效")
                db.execute("INSERT INTO grants VALUES(?,?,?,?) ON CONFLICT(workflow_id,kind,principal) DO UPDATE SET role=excluded.role",
                           (wid, kind, principal, role))
        return {"ok": True}

    def archive_workflow(self, actor, wid, body):
        with self.db.transaction() as db:
            flow, _ = workflow_access(db, actor["id"], wid, "owner")
            require(body.get("expected_version") == flow["version"], "Workflow 版本已更新", 409, "version_conflict")
            archived = body.get("archived", True)
            require(isinstance(archived, bool), "archived 必须为布尔值")
            db.execute("UPDATE workflows SET archived=? WHERE id=?", (int(archived), wid))
        return {"id": wid, "archived": archived}

    def _command(self, db, actor, action, target, body, execute):
        key = body.get("request_id")
        require(isinstance(key, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{8,100}", key), "需要 8–100 字符 request_id")
        digest = hashlib.sha256(dumps([action, target, body]).encode()).hexdigest()
        old = db.execute("SELECT * FROM commands WHERE actor=? AND key=?", (actor["id"], key)).fetchone()
        if old:
            require(old["hash"] == digest, "request_id 对应不同请求", 409, "idempotency_conflict")
            return json.loads(old["response"])
        result = execute()
        db.execute("INSERT INTO commands VALUES(?,?,?,?,?)",
                   (actor["id"], key, digest, dumps(result), time.time()))
        return result

    def start(self, actor, body, *, design=None):
        from .recovery import policy
        selected = any(body.get(key) is not None for key in ("workflow_id", "template_id", "steps", "document"))
        require(body.get("plan") in (None, "agent", "match"), "plan 只能是 agent 或 match")
        require(not (selected and body.get("plan") == "agent"), "plan=agent 时不要同时指定工作流、模板、steps 或 document")
        if not body.get("worker_id"):
            # The space's only usable Worker runs it; a Team without an execution environment takes no new Sessions (D28).
            team = body.get("team_id") or None
            with self.db.read() as db:
                usable = space_workers(db, actor["id"], team)
                if team and not usable:
                    row = db.execute("SELECT name FROM teams WHERE id=?", (team,)).fetchone()
                    if row is not None and team_environment(db, team) is not None:
                        raise Error(409, "environment_disabled", f"Team「{row['name']}」的执行环境已停用，请联系平台管理员")
                    if row is not None:
                        raise Error(409, "team_without_worker", f"Team「{row['name']}」尚未分配执行环境，请联系平台管理员分配")
            if len(usable) == 1:
                body = {**body, "worker_id": usable[0]["id"]}
        planning = design is None and (body.get("plan") == "agent" or (not selected and body.get("plan") != "match"))
        if planning:
            # A real KiroCrew Agent recognises the intent, enriches the requirement and plans the tasks (planning.py).
            from .planning import plan_step
            models = {}
            if body.get("worker_id"):
                with self.db.read() as db:
                    row = db.execute("SELECT config FROM workers WHERE id=?", (body["worker_id"],)).fetchone()
                models = json.loads(row["config"]).get("capabilities", {}).get("models", {}) if row else {}
            body = {**{k: v for k, v in body.items() if k != "plan"}, "steps": [plan_step(models)]}
        resolved = self.resolve_workflow(actor, {k: v for k, v in body.items() if k != "plan"})
        with self.db.transaction() as db:
            wid = resolved["selection"].get("workflow_id")
            team = body.get("team_id") or None
            if team:
                member(db, actor["id"], team)
            if wid:
                flow, _ = workflow_access(db, actor["id"], wid, "runner")
                require(not flow["archived"], "工作流已归档，历史 Session 可继续", 409, "archived")
                version = resolved["selection"]["workflow_version"]
                saved = db.execute("SELECT spec FROM workflow_versions WHERE workflow_id=? AND version=?",
                                   (wid, version)).fetchone()
                require(saved is not None, "工作流版本不存在", 404, "not_found")
                spec = json.loads(saved["spec"])
            else:
                spec, version = resolved["steps"], None
            goal = text(body.get("input"), "开发需求", minimum=3)
            source = body.get("source")
            require(isinstance(source, dict) and set(source) <= {"type", "url", "ref"}
                    and source.get("type") in {"new", "git"}, "源码配置无效")
            if source["type"] == "git":
                from .sources import normalize_source
                try:
                    url, kind = normalize_source(text(source.get("url"), "仓库地址", 2000))
                except ValueError as exc:
                    raise Error(400, "invalid_request", str(exc)) from None
                require(kind == "git", "Team API 只接受远程 Git 来源，不接受宿主机任意路径")
                source = {**source, "url": url}
                if "ref" in source:
                    source["ref"] = text(source["ref"], "Git ref", 200)
                    require(not source["ref"].startswith("-"), "Git ref 无效")
            else:
                require(set(source) == {"type"}, "新建项目不接受 url/ref")
            worker = body.get("worker_id")
            if worker:
                self._worker(db, actor["id"], worker, team)

            def execute():
                rid, now = uid("run"), time.time()
                snapshot = {"workflow_id": wid, "workflow_version": version, "steps": spec,
                            "name": resolved["name"], "workflow_name": resolved["workflow_name"],
                            "selection": {k: v for k, v in resolved["selection"].items() if k != "steps"},
                            "match_mode": resolved["mode"], "match_reason": resolved["reason"]}
                snapshot.update(recovery=policy(body.get("recovery")) if design is None else policy({"enabled": False}),
                                execution_contract=2 if design is None else 1)
                if design is not None:
                    snapshot.update(purpose="workflow_design", design=design)
                if planning:
                    snapshot.update(planning={"mode": "agent", "applied": False}, workflow_name="KiroCrew 规划",
                                    match_mode="agent", match_reason="由 KiroCrew Agent 识别意图、补全需求并拆分任务")
                db.execute("INSERT INTO runs(id,owner_id,team_id,worker_id,workflow_id,snapshot,input,source,status,created,updated) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (rid, actor["id"], team, worker, wid, dumps(snapshot), goal, dumps(source),
                            "preparing" if worker else "waiting_worker", now, now))
                for i, step in enumerate(spec):
                    db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)",
                               (rid, step["id"], i, dumps(step)))
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                           (rid, 1, dumps({"input": goal, "steps": spec}), actor["id"], now))
                self.db.event(db, rid, "run.created", {"actor": actor["id"], "worker_assigned": bool(worker)})
                return {"id": rid, "session_id": rid, "version": 1,
                        "status": "preparing" if worker else "waiting_worker"}
            return self._command(db, actor, "design" if design is not None else "start", None,
                                 {**body, "_design": design} if design is not None else body, execute)

    @staticmethod
    def _worker(db, user, worker, team):
        row = next((r for r in space_workers(db, user, team) if r["id"] == worker), None)
        require(row is not None, "Worker 不属于本次执行空间", 403, "forbidden")
        return dict(row)

    def preview_entry(self, actor, rid):
        """The file the preview opens first (e.g. index.html), asked from the run's Worker."""
        with self.db.read() as db:
            run = run_access(db, actor["id"], rid)
            require(run["worker_id"], "此 Session 尚未分配 Worker，没有可预览的产出", 409, "no_preview")
            worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        from .transport import worker_request
        return worker_request(json.loads(worker["config"]), "/files", {"run_id": rid, "path": ""})["entry"]

    def preview_file(self, rid, relative):
        """One project file of a run, for a holder of a valid preview ticket (checked by the server)."""
        import base64
        with self.db.read() as db:
            run = db.execute("SELECT worker_id FROM runs WHERE id=?", (rid,)).fetchone()
            require(run is not None and run["worker_id"], "预览不可用", 404, "not_found")
            worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        from .transport import worker_request
        result = worker_request(json.loads(worker["config"]), "/files", {"run_id": rid, "path": relative})
        return base64.b64decode(result["content"])

    def runs(self, actor):
        with self.db.read() as db:
            result = []
            for row in db.execute("SELECT id FROM runs ORDER BY created DESC"):
                try:
                    run = run_access(db, actor["id"], row["id"])
                    result.append(self._view(db, run, detail=False))
                except Error:
                    pass
            return result

    def run(self, actor, rid):
        with self.db.read() as db:
            return self._view(db, run_access(db, actor["id"], rid))

    def _view(self, db, run, detail=True):
        value = {k: v for k, v in run.items() if k != "snapshot"}
        snapshot = json.loads(run["snapshot"])
        value.update({key: snapshot.get(key) for key in ("name", "workflow_name", "workflow_version",
                                                       "selection", "match_mode", "match_reason", "purpose", "planning")})
        value["source"] = json.loads(value["source"])
        value["session_id"] = run["id"]
        value["tasks"] = []
        for row in db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (run["id"],)):
            task = dict(row)
            task["spec"] = json.loads(task["spec"])
            task["task_id"] = run["id"] + "/" + task["id"]
            if detail:
                task["attempts"] = []
                for attempt in db.execute("SELECT * FROM attempts WHERE run_id=? AND task_id=? ORDER BY number",
                                          (run["id"], task["id"])):
                    item = dict(attempt)
                    item["spec"], item["evidence"] = json.loads(item["spec"]), json.loads(item["evidence"])
                    task["attempts"].append(item)
            value["tasks"].append(task)
        value["progress"] = {"accepted": sum(t["status"] == "accepted" for t in value["tasks"]),
                             "total": len(value["tasks"])}
        if detail:
            from .recovery import summary
            value["recovery"] = summary(db, run)
            value["attention"] = self._attention(value, self._space(db, run) if run["status"] == "waiting_worker" else None)
        return value

    @staticmethod
    def _space(db, run):
        """The run's space for the waiting_worker hint: Team name (None for a personal space) and enabled Workers."""
        workers = len(space_workers(db, run["owner_id"], run["team_id"]))
        if run["team_id"]:
            team = db.execute("SELECT name FROM teams WHERE id=?", (run["team_id"],)).fetchone()
            return {"team": team["name"] if team else run["team_id"], "workers": workers,
                    "disabled": not workers and team_environment(db, run["team_id"]) is not None}
        return {"team": None, "workers": workers, "disabled": False}

    @staticmethod
    def _attention(run, space=None):
        task = next((t for t in run["tasks"] if t["status"] not in {"accepted", "pending"}), None)
        latest = run["recovery"]["latest"]
        if run["status"] == "waiting_worker":
            space = space or {"team": None, "workers": 0}
            where = f"Team「{space['team']}」" if space["team"] else "个人空间"
            if space["workers"]:
                title, detail = "需求已保存，等待选择 Worker", f"{where}有 {space['workers']} 个可用 Worker，提交时未指定。"
                action = "选择 Worker 后开始执行。"
            elif space["team"] and space.get("disabled"):
                title, detail = f"{where}的执行环境已停用", "该 Team 的执行环境被管理员停用，暂时无法执行。"
                action = "请平台管理员重新启用该执行环境后分配 Worker；也可以改在个人空间重新提交。"
            elif space["team"]:
                title = f"{where}没有可用 Worker"
                detail = "该 Team 尚未分配执行环境（共享环境或独立的执行环境）。"
                action = "请平台管理员在控制台为该 Team 分配执行环境后分配 Worker；也可以改在个人空间重新提交。"
            else:
                title, detail = f"{where}没有可用 Worker", "尚未为你登记执行环境。"
                action = "请平台管理员为你登记执行环境后分配 Worker。"
            return {"title": title, "detail": detail, "action": action, "workers": space["workers"], "task_id": None}
        if run["status"] == "diagnosing":
            return {"title": "协调者正在诊断与安排修复", "detail": run["error"] or "正在读取失败证据，等待原生诊断结果。",
                    "action": "等待诊断结果；也可停止本 Session", "task_id": task["id"] if task else None}
        if run["status"] != "blocked":
            return None
        attempt = (task.get("attempts") or [{}])[-1] if task else {}
        failure = attempt.get("evidence", {}).get("failure_type")
        error = run.get("error") or attempt.get("error") or "尚无可核实的错误原因"
        if "原生任务已停止" in error:
            title = (task["spec"]["name"] + "：已停止") if task else "原生任务已停止，尚未完成"
            action = "可请求协调者诊断，或配置后原地重跑；停止原因未报告时不能推断。"
        elif not task and not run.get("native_parent"):
            title, action = "项目准备未完成", "检查仓库来源、访问权限和 Worker，处理具体错误后重跑。"
        elif failure == "input":
            title, action = "等待补充项目或任务信息", "补充缺少的源码来源或需求，再继续同一 Session。"
        elif failure in {"auth", "model"}:
            title, action = "等待处理认证、权限或模型配置", "在原生 KiroCrew 处理权限，或修改本任务配置后重跑。"
        elif latest and latest["status"] == "blocked":
            title, action = "协调者需要你处理具体阻塞", "按诊断说明补充条件，再请求诊断或原地重跑。"
        else:
            title, action = "任务未通过交接或质量检查", "查看失败证据，可请求协调者诊断并修复，或配置后原地重跑。"
        settings = run["recovery"]["policy"]
        if settings["enabled"] and run["recovery"]["used"] >= settings["max_cycles"]:
            action += f" 自动纠错已达到 {settings['max_cycles']} 轮上限。"
        return {"title": title, "detail": error, "action": action, "task_id": task["id"] if task else None,
                "task_name": task["spec"]["name"] if task else None}

    def operate(self, actor, rid, action, body):
        require(action in {"continue", "recover", "stop", "close", "reopen", "delete", "assign-worker"}, "不支持的操作")
        with self.db.transaction() as db:
            run = run_access(db, actor["id"], rid, owner=action == "delete")
            require(action not in {"continue", "recover"} or json.loads(run["snapshot"]).get("purpose") != "workflow_design",
                    "工作流建议请按新需求重新生成；原建议保留作为证据", 409, "invalid_state")
            def execute():
                require(body.get("expected_version") == run["version"], "Session 版本已更新", 409, "version_conflict")
                require(run["lifecycle"] != "deleted", "Session 已删除", 409, "invalid_state")
                version = run["version"] + 1
                if action == "continue":
                    require(run["lifecycle"] == "open" and run["status"] in SETTLED, "请先等待结束或停止", 409, "busy")
                    ordered = [dict(t) for t in db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
                    index = next((i for i, t in enumerate(ordered) if t["id"] == body.get("task_id")), None)
                    require(index is not None, "Task 不存在", 404, "not_found")
                    require(all(t["status"] == "accepted" for t in ordered[:index]), "先完成上游 Task", 409, "dependency")
                    override = body.get("execution", {})
                    require(isinstance(override, dict) and set(override) <= {"name", "tool", "model", "effort", "prompt"}, "任务覆盖字段无效")
                    current = steps([{**json.loads(ordered[index]["spec"]), **override}])[0]
                    goal = text(body.get("input", run["input"]), "开发需求", minimum=3)
                    if "recovery" in body:
                        from .recovery import policy
                        snapshot = json.loads(run["snapshot"])
                        snapshot["recovery"] = policy(body["recovery"])
                        if snapshot["recovery"]["enabled"]:
                            snapshot["execution_contract"] = 2
                        db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), rid))
                    for row in ordered[index:]:
                        spec = current if row["id"] == current["id"] else json.loads(row["spec"])
                        db.execute("UPDATE tasks SET spec=?,status='pending',current_attempt=NULL WHERE run_id=? AND id=?",
                                   (dumps(spec), rid, row["id"]))
                    db.execute("UPDATE runs SET input=?,desired='active',status=?,error=NULL WHERE id=?",
                               (goal, "ready" if run["worker_id"] else "waiting_worker", rid))
                    db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                               (rid, version, dumps({"input": goal, "task": current, "invalidated": [t["id"] for t in ordered[index:]],
                                                     **({"recovery": snapshot["recovery"]} if "recovery" in body else {})}),
                                 actor["id"], time.time()))
                elif action == "recover":
                    from .recovery import schedule
                    require(run["lifecycle"] == "open" and run["worker_id"] and run["native_parent"] and
                            run["status"] in {"blocked", "failed", "stopped"}, "此 Session 当前不能启动诊断", 409, "busy")
                    task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1",
                                      (rid,)).fetchone()
                    attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone() if task else None
                    require(attempt is not None, "尚无可诊断的失败执行；请补充输入并重跑", 409, "not_attempted")
                    snapshot = json.loads(run["snapshot"])
                    snapshot["execution_contract"] = 2
                    db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), rid))
                    schedule(db, self.db, run, task, attempt, manual=True)
                elif action == "assign-worker":
                    require(run["status"] == "waiting_worker" and run["lifecycle"] == "open",
                            "运行已分配 Worker 或已关闭", 409, "invalid_state")
                    self._worker(db, actor["id"], body.get("worker_id"), run["team_id"])
                    db.execute("UPDATE runs SET worker_id=?,status='preparing' WHERE id=?", (body["worker_id"], rid))
                elif action == "reopen":
                    require(run["lifecycle"] == "closed", "只有已关闭的 Session 可重新打开", 409, "invalid_state")
                    db.execute("UPDATE runs SET desired='reopen',status='reopening' WHERE id=?", (rid,))
                else:
                    require(action != "delete" or body.get("confirmation") == rid, "删除需完整 Session ID 确认")
                    db.execute("UPDATE runs SET desired=?,status='stopping' WHERE id=?", (action, rid))
                if not run["worker_id"] and action in {"stop", "close", "delete", "reopen"}:
                    lifecycle = {"close": "closed", "delete": "deleted", "reopen": "open"}.get(action, run["lifecycle"])
                    db.execute("UPDATE runs SET desired='stopped',status='stopped',lifecycle=? WHERE id=?",
                               (lifecycle, rid))
                db.execute("UPDATE runs SET version=?,updated=? WHERE id=?", (version, time.time(), rid))
                self.db.event(db, rid, "command." + action, {"actor": actor["id"], "version": version})
                return {"id": rid, "session_id": rid, "version": version, "command": action, "status": "accepted"}
            return self._command(db, actor, action, rid, body, execute)

    def events(self, actor, rid, after=0):
        with self.db.read() as db:
            run_access(db, actor["id"], rid)
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 200", (rid, after))]
