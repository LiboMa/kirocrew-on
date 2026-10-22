"""A Session shared with a person, a Team or the whole company — every user of the platform (D64, M4-1). A viewer reads;
a collaborator is what an ordinary Team member is. Who controls the Session (its creator, a Team owner, an administrator)
shares it; the company only views."""
import time

from .db import uid
from .security import elevated, require

KINDS = ("user", "team", "everyone")
ROLES = ("viewer", "collaborator")
RANK = {"viewer": 1, "collaborator": 2}


def best(db, actor, run):
    """The share that lets ``actor`` into ``run`` with the most rights — to them, to a Team they are in, or to everyone."""
    teams = [r[0] for r in db.execute("SELECT team_id FROM members WHERE user_id=?", (actor["id"],))]
    rows = db.execute(
        "SELECT s.*, u.username AS by_name FROM shares s LEFT JOIN users u ON u.id=s.created_by WHERE s.run_id=? AND ("
        "(s.kind='user' AND s.subject=?) OR s.kind='everyone'"
        + (f" OR (s.kind='team' AND s.subject IN ({','.join('?' * len(teams))}))" if teams else "") + ")",
        (run["id"], actor["id"], *teams)).fetchall()
    return max(rows, key=lambda row: RANK[row["role"]], default=None)


def parse(db, actor, body):
    """``{"kind": "user", "username"} | {"kind": "team", "id"} | {"kind": "everyone"}`` with ``role`` → (kind, subject,
    role, name). A Team is one the sharer is in (an administrator: any), not archived; a person is an active user."""
    require(isinstance(body, dict) and set(body) <= {"kind", "username", "id", "role"},
            "共享须为 {\"kind\": \"user|team|everyone\", \"username\" 或 \"id\", \"role\": \"viewer|collaborator\"}")
    kind, role = body.get("kind"), body.get("role")
    require(kind in KINDS, "kind 须为 user（某个人）、team（某个 Team）或 everyone（全公司）")
    require(role in ROLES, "role 须为 viewer（查看）或 collaborator（协作）")
    require(kind != "everyone" or role == "viewer", "共享给全公司只能是查看（viewer）")
    if kind == "everyone":
        require("username" not in body and "id" not in body, "共享给全公司不带 username 或 id")
        return kind, "", role, "全公司"
    if kind == "user":
        require(isinstance(body.get("username"), str) and "id" not in body, "共享给个人须写 username")
        row = db.execute("SELECT id, username FROM users WHERE username=? AND active=1", (body["username"],)).fetchone()
        require(row is not None, "用户不存在或已停用", 404, "not_found")
        return kind, row["id"], role, row["username"]
    require(isinstance(body.get("id"), str) and "username" not in body, "共享给 Team 须写 id")
    row = db.execute("SELECT id, name, archived FROM teams WHERE id=?", (body["id"],)).fetchone()
    member = row is not None and db.execute("SELECT 1 FROM members WHERE team_id=? AND user_id=?",
                                            (row["id"], actor["id"])).fetchone()
    require(row is not None and (member or elevated(actor)), "Team 不存在或无权限", 404, "not_found")
    require(not row["archived"], f"Team「{row['name']}」已归档", 409, "team_archived")
    return kind, row["id"], role, row["name"]


def store(db, actor, run_id, kind, subject, role):
    """One share per Session and recipient: sharing again changes its role. Returns its id."""
    found = db.execute("SELECT id FROM shares WHERE run_id=? AND kind=? AND subject=?", (run_id, kind, subject)).fetchone()
    if found:
        db.execute("UPDATE shares SET role=?, created_by=?, created=? WHERE id=?", (role, actor["id"], time.time(), found["id"]))
        return found["id"]
    sid = uid("share")
    db.execute("INSERT INTO shares(id,run_id,kind,subject,role,created_by,created) VALUES(?,?,?,?,?,?,?)",
               (sid, run_id, kind, subject, role, actor["id"], time.time()))
    return sid


def view(db, row):
    if row["kind"] == "user":
        found = db.execute("SELECT username FROM users WHERE id=?", (row["subject"],)).fetchone()
        name = found["username"] if found else row["subject"]
    elif row["kind"] == "team":
        found = db.execute("SELECT name FROM teams WHERE id=?", (row["subject"],)).fetchone()
        name = found["name"] if found else row["subject"]
    else:
        name = "全公司"
    by = db.execute("SELECT username FROM users WHERE id=?", (row["created_by"],)).fetchone()
    return {"id": row["id"], "kind": row["kind"], "subject": row["subject"] or None, "name": name, "role": row["role"],
            "by": by["username"] if by else None, "created": row["created"]}


def listed(db, run_id):
    return [view(db, row) for row in db.execute("SELECT * FROM shares WHERE run_id=? ORDER BY created", (run_id,))]
