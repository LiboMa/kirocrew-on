"""Ephemeral browser fixture; deliberately no Engine, Worker or native calls."""
import tempfile
import time

from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.security import authenticate, create_user, issue_token
from octopus.server import server

with tempfile.TemporaryDirectory(prefix="octopus-browser-") as root:
    database = Database(root)
    with database.transaction() as db:
        owner = create_user(db, "fixture-owner", "Fixture-password-123!", True)
        create_user(db, "fixture-reader", "Fixture-password-123!")
        actor = authenticate(db, issue_token(db, owner["id"])["token"])
    service = Service(database)
    team = service.create_team(actor, {"name": "Fixture Team"})
    service.membership(actor, team["id"], {"username": "fixture-reader"})
    spec = [
        {"id": "code", "name": "实现与测试", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现用户功能并记录真实验证"},
        {"id": "review", "name": "独立审查", "tool": "codex", "model": "auto", "effort": "", "prompt": "读取前序交接，独立审查代码"},
    ]
    flow = service.save_workflow(actor, {"name": "Fixture 开发流程", "steps": spec})
    for index in (1, 2):
        run = service.start(actor, {"request_id": f"fixture-run-{index}", "workflow_id": flow["id"],
                                   "input": f"Fixture Session {index} 的独立需求", "source": {"type": "new"}})
        with database.transaction() as db:
            db.execute("UPDATE runs SET status='blocked',workspace=? WHERE id=?",
                       (f"/fixture/session-{index}", run["id"]))
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,result,error,created,updated)"
                       " VALUES(?,?,'code',1,?,'blocked',?,'Fixture only','模拟交接缺失',?,?)",
                       (f"fixture-attempt-{index}", run["id"], dumps(spec[0]), dumps({}), time.time(), time.time()))
            db.execute("UPDATE tasks SET status='blocked',current_attempt=? WHERE run_id=? AND id='code'",
                       (f"fixture-attempt-{index}", run["id"]))
    print("Ephemeral MVP browser fixture on 8921; no native execution.", flush=True)
    server(database, 8921).serve_forever()
