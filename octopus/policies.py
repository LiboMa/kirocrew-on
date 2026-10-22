"""A person's or a Team's policies (D55): mapping preferences. The platform keeps no default chain of its own
(no-hardcoded-chains): a rule is what someone wrote down for their own Sessions. Admission rules by repository are gone
(D70): every Session starts once planned; old ones stay listed, without effect, so they can be deleted."""
import json
import re
from urllib.parse import urlsplit

from .security import require

KINDS = ("admission", "mapping")


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
    require(kind in KINDS, "kind 须为 mapping")
    # D70 (owner 2026-10-22): no plan waits for confirmation, so there is nothing for an admission rule to decide. Ones
    # written before stay listed so they can be deleted; they have no effect.
    require(kind != "admission", "准入规则已取消：规划完成后会话总是直接开始，不再等待确认", 400, "admission_removed")
    return mapping_rule(value)


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


def basis_text(basis):
    """Why a Session started at once or waits for its plan's confirmation; "" when the submission said so itself."""
    if not basis or basis.get("basis") == "submitted":
        return ""
    mode = "自动开始" if basis.get("mode") == "auto" else "等待确认"
    return f"按准入规则「{basis.get('pattern') or ''}」{mode}" if basis.get("basis") == "rule" else f"按平台默认{mode}"


def view(row, team_name=None, deletable=False):
    return {"id": row["id"], "kind": row["kind"], "scope": "team" if row["team_id"] else "personal",
            "team_id": row["team_id"], "team_name": team_name, "rule": json.loads(row["rule"]),
            "created_by": row["created_by"], "created": row["created"], "deletable": deletable}
