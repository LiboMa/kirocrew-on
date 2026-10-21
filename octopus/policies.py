"""A person's or a Team's policies (D55): admission rules by repository and mapping preferences. The platform keeps no
default chain of its own (no-hardcoded-chains): a rule is what someone wrote down for their own Sessions."""
from fnmatch import fnmatchcase
import json
import re
from urllib.parse import urlsplit

from .security import require

KINDS = ("admission", "mapping")
MODES = ("auto", "confirm")
PATTERN = re.compile(r"[a-z0-9._*/-]{1,300}")


def repo_of(source):
    """``host/owner/repo`` of a Session's source, lower case and without ``.git``: the HTTPS, ssh:// and scp-like
    (git@host:path) forms of one repository give one answer. None for a new project."""
    if not isinstance(source, dict) or source.get("type") != "git" or not isinstance(source.get("url"), str):
        return None
    url = source["url"].strip()
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:
        found = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", url)
        if not found:
            return None
        host, path = found.groups()
    path = path.strip("/").removesuffix(".git").strip("/")
    return f"{host}/{path}".lower() if host and path else None


def admission_rule(rule):
    require(isinstance(rule, dict) and set(rule) == {"repos", "mode"}, "准入规则须为 {\"repos\": \"<主机/所有者/仓库>\", \"mode\": \"auto|confirm\"}")
    repos = rule["repos"].strip().lower().removesuffix(".git") if isinstance(rule["repos"], str) else ""
    require(PATTERN.fullmatch(repos) is not None, "repos 须为 主机/所有者/仓库，可用 * 通配，例如 github.com/o/*")
    require(rule["mode"] in MODES, "mode 须为 auto（直接开始）或 confirm（等待确认）")
    return {"repos": repos, "mode": rule["mode"]}


def mapping_rule(rule):
    """A preference: a task kind or a role, the tool for it and optionally a model — for the planner to weigh (D55 §6.2)."""
    from . import roles
    from .domain import KINDS as TASK_KINDS, TOOLS
    require(isinstance(rule, dict) and set(rule) <= {"kind", "role", "tool", "model"} and ("kind" in rule) != ("role" in rule),
            "映射偏好须为 {\"kind\" 或 \"role\"（二选一）, \"tool\", \"model\"?}")
    if "kind" in rule:
        require(rule["kind"] in TASK_KINDS, "kind 须为 " + " / ".join(TASK_KINDS))
    else:
        require(rule["role"] in roles.KINDS, "role 不是平台的角色")
    require(rule.get("tool") in TOOLS, "tool 须为 " + " / ".join(TOOLS))
    require("model" not in rule or isinstance(rule["model"], str) and 0 < len(rule["model"].strip()) <= 200, "model 须为 1–200 个字符")
    return {k: v.strip() if k == "model" else v for k, v in rule.items()}


def rule(kind, value):
    require(kind in KINDS, "kind 须为 admission 或 mapping")
    return admission_rule(value) if kind == "admission" else mapping_rule(value)


def preferences(db, actor_id, team):
    """The mapping preferences a new Session is planned with: the submitter's own first, then the Team's (for a Team
    Session); on one kind or role the submitter's own wins, and within a scope the newest."""
    result, taken = [], set()
    layers = [("personal", "owner_id", actor_id)] + ([("team", "team_id", team)] if team else [])
    for scope, column, holder in layers:
        for row in db.execute(f"SELECT * FROM policies WHERE kind='mapping' AND {column}=? ORDER BY created DESC", (holder,)):
            value = json.loads(row["rule"])
            key = ("role", value["role"]) if "role" in value else ("kind", value["kind"])
            if key not in taken:
                taken.add(key)
                result.append({"id": row["id"], "scope": scope, **value})
    return result


def _best(rows, repo):
    """The most specific rule that matches: the most literal characters (github.com/o/* over github.com/* over *), then
    the fewest wildcards, then the newest."""
    patterns = {r["id"]: json.loads(r["rule"])["repos"] for r in rows}
    matched = [r for r in rows if fnmatchcase(repo, patterns[r["id"]])]
    return min(matched, key=lambda r: (-len(patterns[r["id"]].replace("*", "")), patterns[r["id"]].count("*"), -r["created"]),
               default=None)


def basis_text(basis):
    """Why a Session started at once or waits for its plan's confirmation; "" when the submission said so itself."""
    if not basis or basis.get("basis") == "submitted":
        return ""
    mode = "自动开始" if basis.get("mode") == "auto" else "等待确认"
    return f"按准入规则「{basis.get('pattern') or ''}」{mode}" if basis.get("basis") == "rule" else f"按平台默认{mode}"


def admission(db, actor_id, team, source, submitted, default):
    """How a new Session is admitted and why: what the submitter said, else their own rule for the repository, else the
    Team's (for a Team Session), else the platform default."""
    if submitted:
        return {"mode": submitted, "basis": "submitted"}
    repo = repo_of(source)
    if repo is not None:
        layers = [("personal", "owner_id", actor_id)] + ([("team", "team_id", team)] if team else [])
        for scope, column, holder in layers:
            rows = db.execute(f"SELECT * FROM policies WHERE kind='admission' AND {column}=?", (holder,)).fetchall()
            best = _best(rows, repo)
            if best is not None:
                value = json.loads(best["rule"])
                return {"mode": value["mode"], "basis": "rule", "policy_id": best["id"], "pattern": value["repos"],
                        "scope": scope}
    return {"mode": default, "basis": "default"}


def view(row, team_name=None, deletable=False):
    return {"id": row["id"], "kind": row["kind"], "scope": "team" if row["team_id"] else "personal",
            "team_id": row["team_id"], "team_name": team_name, "rule": json.loads(row["rule"]),
            "created_by": row["created_by"], "created": row["created"], "deletable": deletable}
