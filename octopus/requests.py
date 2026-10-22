"""Requests (D55 M2-1): each submission as it was made — its client, the requirement and source, what the submitter
chose explicitly and the policy the Session started under. A Request has no state of its own: it shows its latest
Session's, so the two never disagree. One Request, one Session for now; the table allows more (a later restart)."""
import json

from .db import dumps, uid

CLIENTS = ("console", "cli", "claude", "kiro", "codex", "kiro-ide", "api")
NAMES = {"console": "控制台", "cli": "CLI", "claude": "Claude Code", "kiro": "Kiro CLI", "codex": "Codex", "kiro-ide": "Kiro IDE",
         "api": "API"}
CLI_LABEL = "octopus CLI"   # the label of the token `octopus login` keeps (D58)


def client_of(label):
    """The client a token was issued for: the console's login, a Coding Agent `octopus connect` set up ("<client> MCP",
    octopus/connect.py), or anything else that calls the API."""
    if label == "Web login":
        return "console"
    if label == CLI_LABEL:
        return "cli"
    first, _, rest = (label or "").partition(" ")
    return first if first in CLIENTS[2:-1] and rest.split(" ")[0] == "MCP" else "api"


def explicit(body):
    """What the submitter chose, not its content: a workflow or template by id, how many steps, a document's format,
    the planning mode."""
    chosen = {key: body[key] for key in ("workflow_id", "workflow_version", "template_id", "plan") if body.get(key) is not None}
    if isinstance(body.get("steps"), list):
        chosen["steps"] = len(body["steps"])
    if isinstance(body.get("document"), dict):
        chosen["document"] = body["document"].get("format") or "auto"
    return chosen


def register(db, actor, key, team, goal, source, explicit, policy, now):
    rid = uid("request")
    db.execute("INSERT INTO requests(id,owner_id,team_id,key,client,input,source,explicit,policy,created) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)",
               (rid, actor["id"], team, key, client_of(actor.get("token_label")), goal, dumps(source), dumps(explicit),
                dumps(policy), now))
    return rid


def sessions(db, request_id):
    rows = db.execute("SELECT id,snapshot,status,updated FROM runs WHERE request_id=? ORDER BY created DESC", (request_id,))
    return [{"id": r["id"], "name": json.loads(r["snapshot"]).get("name"), "status": r["status"], "updated": r["updated"]}
            for r in rows]


def view(db, row, runs):
    owner = db.execute("SELECT username FROM users WHERE id=?", (row["owner_id"],)).fetchone()
    return {"id": row["id"], "client": row["client"], "input": row["input"], "source": json.loads(row["source"]),
            "explicit": json.loads(row["explicit"]), "policy": json.loads(row["policy"]), "created": row["created"],
            "owner": {"id": row["owner_id"], "username": owner["username"] if owner else None}, "team_id": row["team_id"],
            "status": runs[0]["status"] if runs else None, "sessions": runs}
