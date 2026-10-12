"""Shared use cases for HTTP, Web, CLI and MCP. Authorization lives here."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import socket
import sqlite3
import threading
import time
import unicodedata

from . import journal
from .db import dumps, uid
from .security import Error, check_password, elevated, password_hash, require

TOOLS = ("kiro", "claude", "codex", "opencode")
ROLES = {"viewer": 1, "runner": 2, "editor": 3, "owner": 4}
ROLE_LABELS = {"viewer": "查看", "runner": "运行", "editor": "编辑", "owner": "所有者"}
# Statuses in which an open Session is not running on its Worker (it can be continued).
SETTLED = ("failed", "blocked", "completed", "stopped", "waiting_worker")
# What the admin environment listing shows of a live /health (no boundary or principal hashes).
HEALTH_FIELDS = ("isolation", "crew_entry", "crew_origin", "version", "tool_auth")


def text(value, field, maximum=12000, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum
            and not any(ord(c) < 32 and c not in "\n\r\t" for c in value),
            f"{field} 须为 {minimum}–{maximum} 个字符")
    return value.strip()


def name(value):
    value = " ".join(unicodedata.normalize("NFKC", text(value, "名称", 80)).split())
    require(not any(unicodedata.category(c) in {"Cc", "Cf"} for c in value), "名称包含不可见字符")
    return value


# Optional task structure (D34 §6.1): what a task delivers and how it is checked, kept in its spec only when given.
CONTRACT = ("kind", "deliverables", "acceptance", "verify", "depends_on", "paths")
KINDS = ("implement", "review", "test", "deliver", "design")
LIMITS = {"deliverables": (20, 500), "acceptance": (20, 500), "verify": (10, 500), "paths": (20, 500), "depends_on": (8, 48)}


def strings(value, field, items, chars):
    require(isinstance(value, list) and len(value) <= items and all(isinstance(x, str) for x in value),
            f"{field} 须为不超过 {items} 项的字符串列表")
    result = list(dict.fromkeys(x.strip() for x in value if x.strip()))
    require(all(len(x) <= chars for x in result), f"{field} 每项不超过 {chars} 个字符")
    return result


def steps(value, known=()):
    """Validated tasks; ``known`` are the ids of tasks before ``value`` (a continued task depends on them)."""
    require(isinstance(value, list) and 1 <= len(value) <= 8, "流程须包含 1–8 个顺序任务")
    seen, earlier, result = set(), set(known), []
    for step in value:
        require(isinstance(step, dict) and set(step) <= {"id", "name", "prompt", "tool", "model", "effort", *CONTRACT},
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
        item = {"id": sid, "name": name(step.get("name")), "tool": tool, "model": text(step.get("model", "auto"), "Model", 200),
                "effort": effort, "prompt": text(step.get("prompt"), "任务说明", 8000)}
        if "kind" in step:
            require(step["kind"] in KINDS, "任务类别只能是 implement、review、test、deliver 或 design")
            item["kind"] = step["kind"]
        for field, (items, chars) in LIMITS.items():
            if field in step:
                item[field] = strings(step[field], field, items, chars)
        # Only earlier tasks: dependencies follow the order the Engine runs them in.
        require(all(dep in (seen | earlier) - {sid} for dep in item.get("depends_on", [])),
                f"任务「{item['name']}」的 depends_on 只能指向前序任务")
        result.append(item)
    return result


def member(db, user, team, owner=False):
    """The user's role in the Team: 404 to a non-member (the Team stays unknown to them), 403 to a member who is not
    the owner that the action needs."""
    row = db.execute("SELECT role FROM members WHERE user_id=? AND team_id=?", (user, team)).fetchone()
    require(row is not None, "Team 不存在或无权限", 404, "not_found")
    require(not owner or row["role"] == "owner", "只有 Team owner 可以执行此操作", 403, "forbidden")
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
    require(allowed > 0, "Workflow 不存在或无权限", 404, "not_found")
    require(allowed >= ROLES[role], f"需要该工作流的 {role} 权限（{ROLE_LABELS[role]}）", 403, "forbidden")
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


def unserved(db, run):
    """Why the run's Worker no longer runs it (spec §6.2): None while it is enabled and still serves the run's space."""
    row = db.execute("SELECT enabled,retired FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
    if row is None or row["retired"]:
        return "执行环境已退役"
    if not row["enabled"]:
        return "执行环境已停用"
    if all(w["id"] != run["worker_id"] for w in space_workers(db, run["owner_id"], run["team_id"])):
        return "执行环境已更换或已移出该空间"
    return None


def assign_spare(db, worker, team):
    """A spare execution environment becomes the Team's dedicated one (D28, T4; P2 for a host that served a Team)."""
    changed = db.execute("UPDATE workers SET scope='dedicated', team_id=?, owner_id=NULL, last_team=NULL "
                         "WHERE id=? AND scope='spare' AND enabled=1 AND retired=0", (team, worker)).rowcount
    require(changed == 1, "该执行环境不可用或已分配", 409, "worker_unavailable")


def team_environment(db, team):
    """The Team's execution environment, enabled or not: its dedicated Worker or the shared one serving it (T6)."""
    return db.execute("SELECT w.* FROM workers w WHERE (w.scope='dedicated' AND w.team_id=?) OR (w.scope='shared' AND "
                      "EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.team_id=?)) ORDER BY w.name",
                      (team, team)).fetchone()


def active_team(db, team):
    """A Team that takes new Sessions and members: 409 once archived (spec §5)."""
    row = db.execute("SELECT * FROM teams WHERE id=?", (team,)).fetchone()
    require(row is not None, "Team 不存在或无权限", 404, "not_found")
    require(not row["archived"], f"Team「{row['name']}」已归档：会话可查看与停止，不能提交新会话或添加成员",
            409, "team_archived")
    return row


def give_environment(db, worker, team):
    """Give a Team an execution environment: a shared Worker serves it, a spare one becomes its own."""
    row = db.execute("SELECT scope,last_team FROM workers WHERE id=? AND enabled=1 AND retired=0", (worker,)).fetchone()
    require(row is not None and row["scope"] in {"shared", "spare"}, "该执行环境不可用或已分配", 409, "worker_unavailable")
    require(team_environment(db, team) is None, "该 Team 已有执行环境（含已停用的）", 409, "team_has_environment")
    if row["scope"] == "shared":
        db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (worker, team))
        return
    if row["last_team"] not in (None, team):
        served = db.execute("SELECT name FROM teams WHERE id=?", (row["last_team"],)).fetchone()
        raise Error(409, "environment_reuse", f"该空闲环境曾服务 Team「{served['name'] if served else row['last_team']}」，"
                    "主机上留有其源码、工作区与记忆；请重新部署该主机（新的执行边界）后再分配给其他 Team")
    assign_spare(db, worker, team)


def run_access(db, actor, rid, owner=False, admin=True):
    """The run if the actor may see it (404 otherwise, never revealing that it exists). owner=True also requires the
    right to delete it (403 with the reason). An elevated admin sees and operates every run unless admin=False (A2)."""
    row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    require(row is not None, "Session 不存在或无权限", 404, "not_found")
    if admin and elevated(actor):
        return dict(row)
    if row["team_id"]:
        role = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?", (row["team_id"], actor["id"])).fetchone()
        require(role is not None, "Session 不存在或无权限", 404, "not_found")
        require(not owner or role["role"] == "owner" or row["owner_id"] == actor["id"],
                "只有 Team owner 可以删除他人创建的会话", 403, "forbidden")
    else:
        require(row["owner_id"] == actor["id"], "Session 不存在或无权限", 404, "not_found")
    return dict(row)


def audit(db, actor, action, kind, target, detail=None):
    """Who did what to which object: administration and admin oversight of others' Sessions (spec §3.4)."""
    db.execute("INSERT INTO audit(at,actor_id,action,kind,target,detail) VALUES(?,?,?,?,?,?)",
               (time.time(), actor["id"], action, kind, target, dumps(detail or {})))


_password_attempts, _password_lock = {}, threading.Lock()   # per user: failed current-password checks in the last minute


def revoke_tokens(db, user, keep=None):
    db.execute("DELETE FROM tokens WHERE user_id=? AND (? IS NULL OR id != ?)", (user, keep, keep))


def _active_admins(db):
    return db.execute("SELECT count(*) FROM users WHERE admin=1 AND active=1").fetchone()[0]


def run_permissions(db, actor, run):
    """What the console may offer on a run it shows (the server enforces the same rules). native: whether the
    actor's own access (never admin oversight, K03) lets them open its native KiroCrew entry."""
    def allowed(**kwargs):
        try:
            run_access(db, actor, run["id"], **kwargs)
            return True
        except Error:
            return False
    native = allowed(admin=False)
    if native and run["worker_id"]:
        worker = db.execute("SELECT scope,owner_id FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        native = worker is None or worker["scope"] != "shared" or worker["owner_id"] == actor["id"]
    # An archived Team's Sessions can be read and stopped, not continued, diagnosed, reopened or reassigned (P3).
    archived = bool(run["team_id"]) and bool(
        (db.execute("SELECT archived FROM teams WHERE id=?", (run["team_id"],)).fetchone() or {"archived": 0})["archived"])
    return {"operate": True, "delete": allowed(owner=True), "native": native, "resume": not archived}


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
            for row in db.execute("SELECT * FROM workers WHERE retired=0").fetchall():
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
            rows = db.execute("SELECT * FROM workers WHERE retired=0 ORDER BY rowid").fetchall()
        configs = [json.loads(row["config"]) for row in rows]
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(rows)))) as pool:
            health = list(pool.map(_probe, configs))
        return [{"id": row["id"], "name": row["name"], "scope": row["scope"], "enabled": bool(row["enabled"]),
                 "owner": {"id": row["owner_id"], "username": users.get(row["owner_id"])} if row["owner_id"] else None,
                 "team": {"id": row["team_id"], "name": teams.get(row["team_id"])} if row["team_id"] else None,
                 "spaces": spaces.get(row["id"], []), "url": config.get("url"),
                 "active_runs": active.get(row["id"], 0), "health": probe}
                for row, config, probe in zip(rows, configs, health)]

    @staticmethod
    def _live_worker(db, wid):
        row = db.execute("SELECT * FROM workers WHERE id=?", (wid,)).fetchone()
        require(row is not None, "Worker 不存在", 404, "not_found")
        require(not row["retired"], "执行环境已退役", 409, "retired")
        return row

    def rename_worker(self, actor, wid, body):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            title = name(body.get("name"))
            db.execute("UPDATE workers SET name=? WHERE id=?", (title, wid))
            audit(db, actor, "worker.rename", "worker", wid, {"from": row["name"], "to": title})
        return {"id": wid, "name": title}

    def retire_worker(self, actor, wid):
        """Retire an execution environment (spec §6.1, P6): no Team may still use it and nothing may be in progress on
        it; the personal spaces it served lose it; historical Sessions keep their reference."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            team = row["team_id"] or (db.execute("SELECT team_id FROM worker_spaces WHERE worker_id=? AND team_id IS NOT NULL",
                                                 (wid,)).fetchone() or [None])[0]
            if team:
                title = db.execute("SELECT name FROM teams WHERE id=?", (team,)).fetchone()
                raise Error(409, "in_use", f"仍是 Team「{title['name'] if title else team}」的执行环境：先为该 Team 更换执行环境")
            # Whatever the Engine still drives on it, a closed Session being reopened included.
            busy = db.execute("SELECT count(*) FROM runs WHERE worker_id=? AND lifecycle!='deleted' AND status NOT IN "
                              f"({','.join('?' * len(SETTLED))})", (wid, *SETTLED)).fetchone()[0]
            require(busy == 0, f"还有 {busy} 个进行中的会话：先等待结束或停止", 409, "busy")
            db.execute("DELETE FROM worker_spaces WHERE worker_id=?", (wid,))
            db.execute("UPDATE workers SET retired=1, enabled=0 WHERE id=?", (wid,))
            audit(db, actor, "worker.retire", "worker", wid)
        return {"id": wid, "retired": True}

    def dedicate_worker(self, actor, wid):
        """A shared Worker host serving exactly one Team becomes that Team's dedicated environment again (D30): every
        member then opens its native KiroCrew entry, as in a personal space."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
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
            audit(db, actor, "worker.dedicate", "worker", wid, {"team_id": team})
        return {"id": wid, "scope": "dedicated", "team_id": team}

    def share_worker(self, actor, wid, body):
        """Turn a dedicated Worker into a shared execution environment; its current space stays served (D28, T3)."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(body.get("confirm") is True, "设为共享环境需要确认：环境内所有会话的 Agent 以同一系统账号运行，"
                "能互相读取源码、工具登录与记忆", 400, "confirmation_required")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
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
            audit(db, actor, "worker.share", "worker", wid)
        return {"id": wid, "scope": "shared"}

    def worker_space(self, actor, wid, body, remove=False):
        """Add a Team or a user's personal space to a shared execution environment, or take it away."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        team, user = body.get("team_id"), body.get("user_id")
        require(isinstance(team or user, str) and bool(team) != bool(user), "需要且只能指定 team_id 或 user_id")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
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
            audit(db, actor, "worker.space.remove" if remove else "worker.space.add", "worker", wid, {column: value})
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
            db.execute("INSERT INTO teams(id,name,created) VALUES(?,?,?)", (tid, title, time.time()))
            db.execute("INSERT INTO members VALUES(?,?,?)", (tid, owner["id"], "owner"))
            give_environment(db, body["worker_id"], tid)
            audit(db, actor, "team.create", "team", tid, {"owner": owner["id"], "worker_id": body["worker_id"]})
            role = "owner" if owner["id"] == actor["id"] else None
            return {"id": tid, "name": title, "role": role, "environment": self._environment(db, tid)}

    @staticmethod
    def _team(db, actor, tid, owner=False):
        """The Team an admin (any Team) or its members (owner=True: its owners) may manage; 404 / 403 as member()."""
        row = db.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
        if elevated(actor):
            require(row is not None, "Team 不存在", 404, "not_found")
        else:
            member(db, actor["id"], tid, owner=owner)
        return dict(row)

    def rename_team(self, actor, tid, body):
        with self.db.transaction() as db:
            team = self._team(db, actor, tid, owner=True)
            title = name(body.get("name"))
            require(not db.execute("SELECT 1 FROM teams WHERE name=? AND id!=?", (title, tid)).fetchone(),
                    "Team 名称已存在", 409, "conflict")
            db.execute("UPDATE teams SET name=? WHERE id=?", (title, tid))
            audit(db, actor, "team.rename", "team", tid, {"from": team["name"], "to": title})
        return {"id": tid, "name": title}

    def archive_team(self, actor, tid, body):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(isinstance(body.get("archived"), bool), "archived 必须为布尔值")
        with self.db.transaction() as db:
            self._team(db, actor, tid)
            db.execute("UPDATE teams SET archived=? WHERE id=?", (int(body["archived"]), tid))
            audit(db, actor, "team.archive" if body["archived"] else "team.restore", "team", tid)
        return {"id": tid, "archived": body["archived"]}

    def change_environment(self, actor, tid, body):
        """Move a Team to another execution environment (spec §5). Old Sessions are not moved (T6): their next dispatch
        finds the Worker no longer serves the Team and waits to be reassigned (§6.2)."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        wid = body.get("worker_id")
        require(isinstance(wid, str) and wid, "需要 worker_id")
        with self.db.transaction() as db:
            self._team(db, actor, tid)
            require(db.execute("SELECT 1 FROM workers WHERE id=? AND retired=0", (wid,)).fetchone(),
                    "Worker 不存在", 404, "not_found")
            old = team_environment(db, tid)
            require(old is None or old["id"] != wid, "该 Team 已在使用这个执行环境", 409, "conflict")
            if old is not None and old["scope"] == "shared":
                db.execute("DELETE FROM worker_spaces WHERE worker_id=? AND team_id=?", (old["id"], tid))
            elif old is not None:
                # The Team's own host waits as a spare that remembers it: its code and memories are still there (P2).
                db.execute("UPDATE workers SET scope='spare', team_id=NULL, owner_id=?, last_team=? WHERE id=?",
                           (actor["id"], tid, old["id"]))
            give_environment(db, wid, tid)
            audit(db, actor, "team.environment", "team", tid, {"from": old["id"] if old else None, "to": wid})
            return {"id": tid, "environment": self._environment(db, tid)}

    def membership(self, actor, tid, body, remove=False):
        """Owners and admins (elevated, members or not) add, remove and re-role members; a member may remove only
        themselves (leave). The last active owner stays (B4)."""
        with self.db.transaction() as db:
            username = text(body.get("username"), "用户名", 64).lower()
            leaving = remove and username == actor["username"]
            self._team(db, actor, tid, owner=not leaving)
            # A deactivated member can still be removed; only active users join or change role.
            target = db.execute("SELECT id FROM users WHERE username=?" + ("" if remove else " AND active=1"),
                                (username,)).fetchone()
            require(target is not None, "用户不存在", 404, "not_found")
            role = body.get("role", "member")
            require(role in {"owner", "member"}, "角色无效")
            current = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?",
                                 (tid, target["id"])).fetchone()
            if not remove and current is None:
                active_team(db, tid)   # an archived Team takes no new members; roles may still change
            if current and current["role"] == "owner" and (remove or role != "owner"):
                # Another *active* owner must remain: a deactivated owner cannot manage the Team (B4).
                require(db.execute("SELECT count(*) FROM members m JOIN users u ON u.id=m.user_id WHERE m.team_id=? "
                                   "AND m.role='owner' AND u.active=1 AND m.user_id!=?", (tid, target["id"])).fetchone()[0] > 0,
                        "不能移除最后一位有效的 Team Owner", 409, "conflict")
            if remove:
                require(current is not None, "该用户不是 Team 成员", 404, "not_found")
                db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, target["id"]))
                audit(db, actor, "team.member.leave" if leaving else "team.member.remove", "team", tid,
                      None if leaving else {"user": target["id"]})
            else:
                db.execute("INSERT INTO members VALUES(?,?,?) ON CONFLICT(team_id,user_id) DO UPDATE SET role=excluded.role",
                           (tid, target["id"], role))
                audit(db, actor, "team.member.set", "team", tid, {"user": target["id"], "role": role})
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
                if team:
                    member(db, actor["id"], team)   # a non-member learns nothing about the Team
                    active_team(db, team)
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
                active_team(db, team)
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
                from .revisions import record
                record(db, rid, 1, "created", f"创建：{len(spec)} 个任务", actor["id"], now, input=goal, steps=spec)
                journal.record(db, rid, "run.created", journal.person(actor), worker_assigned=bool(worker))
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
            run = run_access(db, actor, rid)
            require(run["worker_id"], "此 Session 尚未分配 Worker，没有可预览的产出", 409, "no_preview")
            worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        from .transport import worker_request
        entry = worker_request(json.loads(worker["config"]), "/files", {"run_id": rid, "path": ""})["entry"]
        self.audit_read(actor, rid, "preview")
        return entry

    def preview_file(self, rid, relative, holder):
        """One project file of a run for the holder of a valid preview ticket (checked by the server), who must still
        be allowed to read the run: revoked access stops an issued link at once (B6)."""
        import base64
        with self.db.read() as db:
            run = db.execute("SELECT worker_id FROM runs WHERE id=?", (rid,)).fetchone()
            require(run is not None and run["worker_id"], "预览不可用", 404, "not_found")
            user = db.execute("SELECT id,admin FROM users WHERE id=? AND active=1", (holder,)).fetchone()
            try:
                require(user is not None, "预览不可用", 404, "not_found")
                run_access(db, {"id": user["id"], "admin": user["admin"],
                                "scopes": ["read"] + (["admin"] if user["admin"] else [])}, rid)
            except Error:
                raise Error(403, "preview_forbidden", "预览权限已撤销，请在控制台重新打开") from None
            worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        from .transport import worker_request
        result = worker_request(json.loads(worker["config"]), "/files", {"run_id": rid, "path": relative})
        return base64.b64decode(result["content"])

    def runs(self, actor, all=False):
        """The actor's own and Team Sessions; with all=True an elevated admin gets every Session (B5)."""
        oversee = all and elevated(actor)
        with self.db.read() as db:
            result = []
            for row in db.execute("SELECT id FROM runs ORDER BY created DESC"):
                try:
                    run = run_access(db, actor, row["id"], admin=oversee)
                    result.append(self._view(db, run, actor, detail=False))
                except Error:
                    pass
            return result

    def run(self, actor, rid):
        with self.db.read() as db:
            value = self._view(db, run_access(db, actor, rid), actor)
        self.audit_read(actor, rid, "view")
        return value

    def _view(self, db, run, actor, detail=True):
        value = {k: v for k, v in run.items() if k != "snapshot"}
        value["permissions"] = run_permissions(db, actor, run)
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
            if run.get("worker_id"):
                # It ran on an environment that no longer runs it (§6.2).
                title = f"{where}的{run.get('error') or '执行环境不可用'}"
                detail = "已完成的任务保留；换到另一个执行环境时，工作区在新环境中重新准备，原环境中的产出不会迁移。"
                action = ("选择 Worker 后继续执行。" if space["workers"]
                          else "请平台管理员为该空间分配执行环境后重新分配 Worker。")
                return {"title": title, "detail": detail, "action": action, "workers": space["workers"], "task_id": None}
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
            run = run_access(db, actor, rid, owner=action == "delete")
            try:
                run_access(db, actor, rid, owner=action == "delete", admin=False)
                oversight = False
            except Error:
                oversight = True   # allowed only as an elevated admin (A2): audited
            if run["team_id"] and action in {"continue", "recover", "reopen", "assign-worker"}:
                active_team(db, run["team_id"])   # an archived Team's Sessions stay readable and stoppable (P3)
            require(action not in {"continue", "recover"} or json.loads(run["snapshot"]).get("purpose") != "workflow_design",
                    "工作流建议请按新需求重新生成；原建议保留作为证据", 409, "invalid_state")
            # A retired Worker is never called again (P6): its Sessions settle here, and cannot be reopened on it.
            retired = bool(run["worker_id"]) and bool(
                db.execute("SELECT retired FROM workers WHERE id=?", (run["worker_id"],)).fetchone()["retired"])
            require(not (retired and action == "reopen"), "执行环境已退役，会话不能重新打开；可以按此任务列表在其他空间发起新开发",
                    409, "retired")
            def execute():
                require(body.get("expected_version") == run["version"], "Session 版本已更新", 409, "version_conflict")
                require(run["lifecycle"] != "deleted", "Session 已删除", 409, "invalid_state")
                version = run["version"] + 1
                who = journal.person(actor)
                # This command's writes to the Session row, recorded as one command event with its parameters (D34).
                fields, done = {}, []
                def commit():
                    if done:
                        return
                    done.append(True)
                    fields.update(version=version, updated=time.time())
                    journal.change(db, "runs", rid, who, "command." + action, event={
                        "version": version, **{k: body[k] for k in ("task_id", "worker_id", "rerun") if k in body},
                        **({"oversight": True, "by": {"id": actor["id"], "username": actor["username"]}} if oversight else {})},
                        **fields)
                if action == "continue":
                    require(run["lifecycle"] == "open" and run["status"] in SETTLED, "请先等待结束或停止", 409, "busy")
                    ordered = [dict(t) for t in db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
                    index = next((i for i, t in enumerate(ordered) if t["id"] == body.get("task_id")), None)
                    require(index is not None, "Task 不存在", 404, "not_found")
                    require(all(t["status"] == "accepted" for t in ordered[:index]), "先完成上游 Task", 409, "dependency")
                    override = body.get("execution", {})
                    require(isinstance(override, dict) and set(override) <= {"name", "tool", "model", "effort", "prompt", *CONTRACT},
                            "任务覆盖字段无效")
                    before = json.loads(ordered[index]["spec"])
                    current = steps([{**before, **override}], known=[t["id"] for t in ordered[:index]])[0]
                    goal = text(body.get("input", run["input"]), "开发需求", minimum=3)
                    if "recovery" in body:
                        from .recovery import policy
                        snapshot = json.loads(run["snapshot"])
                        old = snapshot.get("recovery")
                        snapshot["recovery"] = policy(body["recovery"])
                        if snapshot["recovery"]["enabled"]:
                            snapshot["execution_contract"] = 2
                        journal.change(db, "runs", rid, who, "run.settings", event={"recovery": [old, snapshot["recovery"]]},
                                       snapshot=dumps(snapshot))
                    reset = [row["id"] for row in ordered[index:]]
                    journal.change_rows(db, "tasks", f"run_id=? AND id IN ({','.join('?' * len(reset))})", (rid, *reset),
                                        who, "task.reset", status="pending", current_attempt=None)
                    journal.change(db, "tasks", (rid, current["id"]), who, "task.spec", spec=dumps(current))
                    reason = unserved(db, run) if run["worker_id"] else None
                    fields.update(input=goal, desired="active", status="ready" if run["worker_id"] and not reason else "waiting_worker",
                                  error=reason)
                    commit()   # before the revision: its snapshot reads the new requirement
                    from .revisions import changes, record
                    detail = "、".join(filter(None, ["需求" if goal != run["input"] else "", changes(before, current)]))
                    record(db, rid, version, "continued", f"续作「{current['name']}」" + (f"：{detail}" if detail else ""),
                           actor["id"], time.time(), input=goal, task=current, invalidated=reset,
                           **({"recovery": snapshot["recovery"]} if "recovery" in body else {}))
                elif action == "recover":
                    from .recovery import schedule
                    require(run["lifecycle"] == "open" and run["worker_id"] and run["native_parent"] and
                            run["status"] in {"blocked", "failed", "stopped"}, "此 Session 当前不能启动诊断", 409, "busy")
                    reason = unserved(db, run)
                    require(not reason, f"{reason}：请续作后重新分配 Worker", 409, "environment_changed")
                    task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1",
                                      (rid,)).fetchone()
                    attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone() if task else None
                    require(attempt is not None, "尚无可诊断的失败执行；请补充输入并重跑", 409, "not_attempted")
                    snapshot = json.loads(run["snapshot"])
                    old = snapshot.get("execution_contract", 1)
                    snapshot["execution_contract"] = 2
                    journal.change(db, "runs", rid, who, "run.settings", event={"execution_contract": [old, 2]},
                                   snapshot=dumps(snapshot))
                    schedule(db, self.db, run, task, attempt, manual=True, actor=who)
                elif action == "assign-worker":
                    require(run["status"] == "waiting_worker" and run["lifecycle"] == "open",
                            "运行已分配 Worker 或已关闭", 409, "invalid_state")
                    # The Session's own space, not the operator's (an admin may assign for someone else).
                    self._worker(db, run["owner_id"], body.get("worker_id"), run["team_id"])
                    if run["worker_id"] not in (None, body["worker_id"]):
                        # Another environment has no native parent of this Session: prepare there afresh (P5).
                        fields.update(worker_id=body["worker_id"], native_parent=None, workspace=None, status="preparing", error=None)
                    else:
                        fields.update(worker_id=body["worker_id"], status="ready" if run["native_parent"] else "preparing", error=None)
                elif action == "reopen":
                    require(run["lifecycle"] == "closed", "只有已关闭的 Session 可重新打开", 409, "invalid_state")
                    fields.update(desired="reopen", status="reopening")
                else:
                    require(action != "delete" or body.get("confirmation") == rid, "删除需完整 Session ID 确认")
                    fields.update(desired=action, status="stopping")
                if (not run["worker_id"] or retired) and action in {"stop", "close", "delete", "reopen"}:
                    lifecycle = {"close": "closed", "delete": "deleted", "reopen": "open"}.get(action, run["lifecycle"])
                    fields.update(desired="stopped", status="stopped", lifecycle=lifecycle)
                commit()
                if oversight:
                    audit(db, actor, "run." + action, "run", rid, {"version": version})
                return {"id": rid, "session_id": rid, "version": version, "command": action, "status": "accepted"}
            return self._command(db, actor, action, rid, body, execute)

    # --- users (spec §4): only an elevated admin manages others; everyone changes their own password ---

    def change_password(self, actor, body):
        # Throttled per user, and checked outside the write lock: a limited token must not brute-force the password
        # (which would yield every scope of the account) or stall other writes with scrypt.
        now = time.time()
        with _password_lock:
            recent = [t for t in _password_attempts.get(actor["id"], []) if now - t < 60]
            require(len(recent) < 5, "原密码错误次数过多，请 1 分钟后再试", 429, "rate_limited")
        with self.db.read() as db:
            saved = db.execute("SELECT password FROM users WHERE id=?", (actor["id"],)).fetchone()["password"]
        if not check_password(body.get("current"), saved):
            with _password_lock:
                _password_attempts[actor["id"]] = recent + [now]
            raise Error(403, "forbidden", "原密码不正确")
        with _password_lock:
            _password_attempts.pop(actor["id"], None)
        hashed = password_hash(body.get("password"))
        with self.db.transaction() as db:
            db.execute("UPDATE users SET password=? WHERE id=?", (hashed, actor["id"]))
            revoke_tokens(db, actor["id"], keep=actor["token_id"])
        return {"ok": True}

    def audit_read(self, actor, rid, action):
        """An elevated admin reading someone else's Session (A2): one audit row per admin, Session and action an hour
        (the console polls). Best effort: never fails the read."""
        if not elevated(actor):
            return
        try:
            with self.db.transaction() as db:
                try:
                    run_access(db, actor, rid, admin=False)
                    return
                except Error:
                    pass
                seen = db.execute("SELECT 1 FROM audit WHERE actor_id=? AND action=? AND target=? AND at>?",
                                  (actor["id"], "run." + action, rid, time.time() - 3600)).fetchone()
                if not seen:
                    audit(db, actor, "run." + action, "run", rid)
        except sqlite3.OperationalError:
            pass

    def _target(self, db, actor, uid):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        require(row is not None, "用户不存在", 404, "not_found")
        return row

    def reset_password(self, actor, uid, body):
        with self.db.transaction() as db:
            self._target(db, actor, uid)
            db.execute("UPDATE users SET password=? WHERE id=?", (password_hash(body.get("password")), uid))
            revoke_tokens(db, uid)
            audit(db, actor, "user.reset_password", "user", uid)
        return {"ok": True}

    def set_active(self, actor, uid, body):
        require(isinstance(body.get("active"), bool), "active 必须为布尔值")
        with self.db.transaction() as db:
            row = self._target(db, actor, uid)
            if not body["active"] and row["active"]:
                sole = [r["name"] for r in db.execute(
                    "SELECT t.name FROM members m JOIN teams t ON t.id=m.team_id WHERE m.user_id=? AND m.role='owner' "
                    "AND NOT EXISTS(SELECT 1 FROM members o JOIN users u ON u.id=o.user_id WHERE o.team_id=m.team_id "
                    "AND o.role='owner' AND o.user_id!=? AND u.active=1) ORDER BY t.name", (uid, uid))]
                require(not sole, f"{row['username']} 是 Team「{'」「'.join(sole)}」唯一的 owner，请先指定另一位 owner",
                        409, "last_owner")
                require(not row["admin"] or _active_admins(db) > 1, "至少保留一位有效的平台管理员", 409, "last_admin")
                revoke_tokens(db, uid)
            db.execute("UPDATE users SET active=? WHERE id=?", (int(body["active"]), uid))
            audit(db, actor, "user.activate" if body["active"] else "user.deactivate", "user", uid)
        return {"id": uid, "active": body["active"]}

    def set_admin(self, actor, uid, body):
        require(isinstance(body.get("admin"), bool), "admin 必须为布尔值")
        with self.db.transaction() as db:
            row = self._target(db, actor, uid)
            if not body["admin"] and row["admin"] and row["active"]:
                require(_active_admins(db) > 1, "至少保留一位有效的平台管理员", 409, "last_admin")
            db.execute("UPDATE users SET admin=? WHERE id=?", (int(body["admin"]), uid))
            audit(db, actor, "user.promote" if body["admin"] else "user.demote", "user", uid)
        return {"id": uid, "admin": body["admin"]}

    def admin_users(self, actor):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        now = time.time()
        with self.db.read() as db:
            result = []
            for row in db.execute("SELECT * FROM users ORDER BY username"):
                result.append({"id": row["id"], "username": row["username"], "admin": bool(row["admin"]),
                               "active": bool(row["active"]), "created": row["created"], "last_login": row["last_login"],
                               "teams": [dict(t) for t in db.execute(
                                   "SELECT t.id,t.name,m.role FROM members m JOIN teams t ON t.id=m.team_id "
                                   "WHERE m.user_id=? ORDER BY t.name", (row["id"],))],
                               "tokens": db.execute("SELECT count(*) FROM tokens WHERE user_id=? AND expires>?",
                                                    (row["id"], now)).fetchone()[0]})
            return result

    def user_tokens(self, actor, uid):
        with self.db.read() as db:
            self._target(db, actor, uid)
            return [{**dict(r), "scopes": json.loads(r["scopes"])} for r in db.execute(
                "SELECT id,label,scopes,expires,last_used FROM tokens WHERE user_id=? AND expires>? ORDER BY expires DESC",
                (uid, time.time()))]

    def revoke_user_token(self, actor, uid, tid):
        with self.db.transaction() as db:
            self._target(db, actor, uid)
            changed = db.execute("DELETE FROM tokens WHERE id=? AND user_id=?", (tid, uid)).rowcount
            require(changed == 1, "Token 不存在", 404, "not_found")
            audit(db, actor, "user.revoke_token", "user", uid, {"token_id": tid})
        return {"ok": True}

    # --- revisions (D32) ---

    def revisions(self, actor, rid):
        """Every revision of a run, oldest first, without snapshots (see revision())."""
        from .revisions import history
        with self.db.read() as db:
            run_access(db, actor, rid)
            entries = history(db, rid)
        self.audit_read(actor, rid, "view")
        return [{k: v for k, v in entry.items() if k not in {"snapshot", "content"}} for entry in entries]

    def changes(self, actor, rid, who=None, kind=None, before=None, limit=50):
        """The Session's change journal, newest first (D34 §4.3): who changed what, from what, to what and why. Events
        written before the journal carry a user id string (commands) or no actor (system, listed as legacy)."""
        require(who in (None, "user", "system"), "who 只能是 user 或 system")
        require(kind is None or (isinstance(kind, str) and len(kind) <= 40), "kind 不超过 40 个字符")
        require(type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200 的整数")
        person = "coalesce(json_type(payload,'$.actor')='text' OR json_extract(payload,'$.actor.kind')='user', 0)"
        sql, params = "SELECT * FROM events WHERE run_id=? AND id<?", [rid, before or 2 ** 62]
        if who:
            sql += f" AND {'' if who == 'user' else 'NOT '}{person}"
        if kind:
            sql += " AND kind LIKE ? ESCAPE '\\'"
            params.append(kind.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        with self.db.read() as db:
            run_access(db, actor, rid)
            users = dict(db.execute("SELECT id,username FROM users").fetchall())
            rows = db.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload"])
            by = payload.pop("actor", None)
            if isinstance(by, str):
                by = {"kind": "user", "id": by, "username": users.get(by)}
            result.append({"id": row["id"], "at": row["created"], "kind": row["kind"], "actor": by or {"kind": "legacy"},
                           "changes": payload.pop("changes", None), "reason": payload.pop("reason", None), "detail": payload})
        return result

    def revision(self, actor, rid, version):
        from .revisions import history
        with self.db.read() as db:
            run_access(db, actor, rid)
            entry = next((e for e in history(db, rid) if e["version"] == version), None)
        require(entry is not None, "修订不存在", 404, "not_found")
        self.audit_read(actor, rid, "view")
        return entry

    def revision_diff(self, actor, rid, before=None, after=None):
        """Differences between two revisions; by default the latest and the one before it."""
        from .revisions import diff, history
        with self.db.read() as db:
            run_access(db, actor, rid)
            entries = {e["version"]: e for e in history(db, rid)}
        versions = sorted(entries)
        after = versions[-1] if after is None else after
        require(after in entries, "修订不存在", 404, "not_found")
        if before is None:
            earlier = [v for v in versions if v < after]
            before = earlier[-1] if earlier else after
        require(before in entries, "修订不存在", 404, "not_found")
        old, new = entries[before]["snapshot"], entries[after]["snapshot"]
        require(old is not None and new is not None, "该修订没有可比较的内容", 409, "no_snapshot")
        self.audit_read(actor, rid, "view")
        return {"from": before, "to": after, "from_summary": entries[before]["summary"],
                "to_summary": entries[after]["summary"],
                "reconstructed": entries[before]["reconstructed"] or entries[after]["reconstructed"], **diff(old, new)}

    def audit_log(self, actor, before=None, limit=50):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200 的整数")
        with self.db.read() as db:
            rows = db.execute("SELECT a.*, u.username FROM audit a JOIN users u ON u.id=a.actor_id "
                              "WHERE a.id < ? ORDER BY a.id DESC LIMIT ?", (before or 2 ** 62, limit)).fetchall()
        return [{"id": r["id"], "at": r["at"], "actor": {"id": r["actor_id"], "username": r["username"]},
                 "action": r["action"], "kind": r["kind"], "target": r["target"], "detail": json.loads(r["detail"])}
                for r in rows]

    def events(self, actor, rid, after=0):
        with self.db.read() as db:
            run_access(db, actor, rid)
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 200", (rid, after))]
