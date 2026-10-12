"""Team authorization, immutable attempts, native boundaries and HTTP clients."""
import copy
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from octopus.server import server
from octopus.worker import Worker
from octopus import mcp
from octopus import designs
from octopus.worker import write
from octopus.schema import openapi
import api as crew_api


SPEC = [{"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现加法并运行真实测试"},
        {"id": "review", "name": "审查", "tool": "codex", "model": "auto", "effort": "", "prompt": "读取真实 diff，检查测试结果"}]


class MVPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        self.users, self.tokens = {}, {}
        with self.db.transaction() as db:
            for name in ["alice", "bob", "eve"]:
                user = create_user(db, name, "Test-password-123!", admin=name == "alice")
                token = issue_token(db, user["id"])["token"]
                self.users[name], self.tokens[name] = authenticate(db, token), token
        self.a, self.b, self.e = (self.users[k] for k in ["alice", "bob", "eve"])
        self.flow = self.service.save_workflow(self.a, {"name": "研发流程", "steps": SPEC})

    def tearDown(self):
        self.tmp.cleanup()

    def team(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('team-env','team-env',?,?,'shared')",
                       (self.a["id"], dumps({"capabilities": {}})))
        t = self.service.create_team(self.a, {"name": "研发 Team", "owner": "alice", "worker_id": "team-env"})
        self.service.membership(self.a, t["id"], {"username": "bob"})
        return t

    def legacy_team(self, name="研发 Team", members=("bob",)):
        """A Team created before execution environments (D28): no Worker; alice is its owner."""
        tid = "team-" + name.encode().hex()[:12]
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams VALUES(?,?,0)", (tid, name))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (tid, self.a["id"]))
            for username in members:
                user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, user["id"]))
        return {"id": tid, "name": name}

    def start(self, actor=None, **extra):
        return self.service.start(actor or self.a, {"request_id": "start-00000001", "workflow_id": self.flow["id"],
                                                  "input": "研发需求与真实验证", "source": {"type": "new"},
                                                  "recovery":{"enabled":False,"max_cycles":2}, **extra})

    def test_intent_preview_reuses_templates_without_side_effects(self):
        from octopus.intent import catalog
        cases = {"修复登录失败的 Bug": "bugfix", "进行安全审计与代码审查": "review",
                 "快速验证一个最小想法": "proof", "做一个团队任务看板": "team-kanban",
                 "先只做规划，不编码": "idea-validation"}
        for goal, template in cases.items():
            with self.subTest(goal=goal):
                preview = self.service.resolve_workflow(self.b, {"input": goal})
                self.assertEqual(preview["selection"], {"template_id": template})
                self.assertEqual(preview["input"], goal)
                self.assertEqual(preview["mode"], "rules")
                self.assertTrue(preview["steps"])
        self.assertEqual(len(catalog()), 15)
        with self.db.read() as db:
            for table in ("runs", "tasks", "attempts", "commands"):
                self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        fallback = self.service.resolve_workflow(self.b, {"input": "一个尚未分类的需求"})
        self.assertIn("通用", fallback["reason"])

    def design_worker(self):
        config={"url":"http://127.0.0.1:1","token":"x"*40,"capabilities":{"boundary":"fixture"}}
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('design-worker','Design',?,?)",
                       (self.a["id"],dumps(config)))
        return {"request_id":"design-fixture-001","input":"为订单退款接口设计编码和独立审查工作流",
                "template_id":"proof","worker_id":"design-worker"}

    def test_design_uses_durable_engine_and_does_not_save_recommendation(self):
        body=self.design_worker()
        with patch("octopus.designs.worker_request",return_value={"boundary":"fixture","workflow_design":True}):
            created=designs.create(self.service,self.a,body)
            self.assertEqual(designs.create(self.service,self.a,body),created)
        self.dispatched=[]
        reply="```json\n"+dumps({"name":"退款接口工作流","steps":SPEC})+"\n```"
        def request(config,path,body,**kw):
            result=self.fake_worker(path,body)
            if path=="/inspect":result["native"]["result"]=reply
            return result
        engine=Engine(self.db,request)
        engine.tick()
        self.assertEqual(designs.status(self.service,self.a,created["id"])["status"],"ready_to_dispatch")
        engine.tick();engine.tick();engine.tick()
        result=designs.status(self.service,self.a,created["id"])
        self.assertEqual(result["status"],"ready")
        self.assertEqual(result["config"]["name"],"退款接口工作流")
        self.assertEqual(result["evidence"]["actual_backend"],"kiro")
        self.assertIn("订单退款",self.dispatched[0]["design"]["basis"][0]["prompt"])
        self.assertEqual(len(self.service.workflows(self.a)),1)
        self.assertEqual(len(self.service.run(self.a,created["id"])["tasks"]),1)

    def test_design_worker_permissions_upgrade_and_no_worker_errors(self):
        body=self.design_worker()
        with self.assertRaises(Error):
            designs.create(self.service,self.b,body)
        with self.assertRaises(Error):
            designs.create(self.service,self.a,{**body,"worker_id":None})
        with patch("octopus.designs.worker_request",return_value={"boundary":"fixture"}):
            with self.assertRaises(Error) as error:designs.create(self.service,self.a,body)
            self.assertEqual(error.exception.code,"worker_upgrade_required")
        with patch("octopus.designs.worker_request",return_value={"boundary":"different","workflow_design":True}):
            with self.assertRaises(Error) as error:designs.create(self.service,self.a,body)
            self.assertEqual(error.exception.code,"worker_changed")
        with self.db.read() as db:self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0],0)

    def test_invalid_generated_workflow_is_not_replaced_with_a_template(self):
        body=self.design_worker()
        with patch("octopus.designs.worker_request",return_value={"boundary":"fixture","workflow_design":True}):
            created=designs.create(self.service,self.a,body)
        self.dispatched=[]
        engine=Engine(self.db,lambda config,path,body,**kw:self.fake_worker(path,body))
        for _ in range(4):engine.tick()
        invalid=designs.status(self.service,self.a,created["id"])
        self.assertEqual(invalid["status"],"invalid")
        self.assertNotIn("config",invalid)
        self.assertEqual(invalid["raw_result"],"Implemented and verified")
        with self.assertRaises(Error):
            designs.status(self.service,self.b,created["id"])
        with self.assertRaises(Error):
            self.service.operate(self.a,created["id"],"continue",
                {"request_id":"wrong-design-retry","expected_version":1,"task_id":"design"})
        with self.assertRaises(Error):
            designs.parse_recommendation(dumps({"name":"Bad","steps":[SPEC[0],SPEC[0]]}))

    def test_template_prompts_change_with_goal_and_keep_manual_steps(self):
        first=self.service.resolve_workflow(self.a,{"input":"实现订单退款","template_id":"proof"})
        second=self.service.resolve_workflow(self.a,{"input":"实现会议议程","template_id":"proof"})
        repair=self.service.resolve_workflow(self.a,{"input":"修复退款异常","template_id":"bugfix"})
        self.assertIn("实现订单退款",first["steps"][0]["prompt"])
        self.assertIn("实现会议议程",second["steps"][0]["prompt"])
        self.assertNotEqual(first["steps"][0]["prompt"],repair["steps"][0]["prompt"])
        manual=self.service.resolve_workflow(self.a,{"input":"实现会议议程","steps":SPEC})
        self.assertEqual(manual["steps"],SPEC)

    def test_design_worker_builds_legacy_prompt_and_native_spawn_contract(self):
        node,body=self.native_worker()
        body["spec"]={**SPEC[0],"tool":"kiro"}
        body["design"]={"input":body["input"],"basis_name":"退款设计","basis":SPEC}
        calls=[]
        node.native=lambda path,payload=None,method=None:(calls.append((path,payload)) or {"id":"abc12345"})
        node.dispatch(body)
        self.assertEqual(calls[0][0],"/api/spawn")
        self.assertEqual(calls[0][1]["agent"],"poc-kiro")
        folder=node.path(body["run_id"])/"attempts"/body["attempt_id"]
        prompt=(folder/"instructions.md").read_text()
        self.assertIn("你是 KiroCrew Octopus 研发平台的工作流设计者",prompt)
        self.assertIn("当前模板 / 用户草稿基线",prompt)
        self.assertIn("不要只返回",prompt)
        self.assertEqual(json.loads((folder/"context.json").read_text())["design"],body["design"])

    def test_parent_note_limit_does_not_truncate_native_result(self):
        node,body=self.native_worker()
        node.native=lambda path,payload=None,method=None:{"id":"abc12345"}
        node.dispatch(body)
        complete="完整生成结果"*1800
        notes=[]
        def native(path,payload=None,method=None):
            if path=="/api/spawn":
                return {"agents":[{"id":"abc12345","parent":"dashboard:octopus-"+body["run_id"],"done":True}]}
            if path=="/api/spawn/abc12345":return {"done":True,"result":complete}
            if path.endswith("/note"):
                notes.append(payload["content"])
                self.assertLessEqual(len(payload["content"]),4000)
                return {"ok":True,"visibleDeferred":True}
            raise AssertionError(path)
        node.native=native
        result=node.inspect(body)
        self.assertEqual(result["native"]["result"],complete)
        self.assertIn("完整结果保留",notes[0])

    def test_recovery_worker_preserves_context_and_uses_native_coordinator(self):
        node, body = self.native_worker()
        calls = []
        node.native = lambda path,payload=None,method=None: (calls.append((path,payload)) or {"id":"abc12345"})
        body["spec"] = {**SPEC[0], "tool":"kiro"}
        body["coordination"] = {"failed_task_id":"review","failed_result":"assert 43 == 42 failed",
                                "tasks":[{"id":"code","spec":SPEC[0]},{"id":"review","spec":SPEC[1]}]}
        first = node.dispatch(body)
        self.assertEqual(first, node.dispatch(body))
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1]["agent"],"poc-diagnose")  # non-delegating diagnosis template (review I1)
        self.assertEqual(calls[0][1]["parent_session"],"dashboard:octopus-"+body["run_id"])
        folder = node.path(body["run_id"])/"attempts"/body["attempt_id"]
        context = json.loads((folder/"context.json").read_text())
        self.assertEqual(context["coordination"],body["coordination"])
        self.assertIn(str(folder/"decision.json"),(folder/"instructions.md").read_text())
        self.assertEqual(first["context_hash"],hashlib.sha256(dumps(context).encode()).hexdigest())
        body["attempt_id"] = "attempt-"+"3"*32
        body.pop("coordination")
        body.update(spec=SPEC[0],quality=True,repair={"decision":{"instructions":"修复边界"},
                    "failure":{"failed_result":"review detected incorrect sum"}})
        node.dispatch(body)
        folder = node.path(body["run_id"])/"attempts"/body["attempt_id"]
        context = json.loads((folder/"context.json").read_text())
        self.assertEqual(context["repair"],body["repair"])
        self.assertEqual(context["quality_contract"],2)
        self.assertIn(str(folder/"outcome.json"),(folder/"instructions.md").read_text())
        self.assertEqual(calls[1][1]["agent"],"poc-claude")
        self.assertEqual(calls[0][1]["cwd"],calls[1][1]["cwd"])

    def test_recover_http_scope_version_and_no_cross_user_access(self):
        body = self.design_worker()
        run = self.start(worker_id=body["worker_id"])
        with self.db.transaction() as db:
            self.tokens["read"] = issue_token(db,self.a["id"],["read"])["token"]
            db.execute("UPDATE runs SET status='blocked',native_parent='fixture-parent' WHERE id=?",(run["id"],))
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,created,updated) "
                       "VALUES('failed-attempt',?,'code',1,?,'blocked',1,1)",(run["id"],dumps(SPEC[0])))
            db.execute("UPDATE tasks SET status='blocked',current_attempt='failed-attempt' WHERE run_id=? AND id='code'",
                       (run["id"],))
        command={"request_id":"http-recover-1","expected_version":1}
        with self.http() as (_,call):
            path="/v1/runs/"+run["id"]+"/recover"
            self.assertEqual(call("POST",path,"read",command)[0],403)
            self.assertEqual(call("POST",path,"bob",command)[0],404)
            self.assertEqual(call("POST",path,body={**command,"expected_version":0})[0],409)
            first=call("POST",path,body=command)
            self.assertEqual(first[0],200)
            self.assertEqual(first,call("POST",path,body=command))
        result=self.service.run(self.a,run["id"])
        self.assertEqual(result["recovery"]["used"],1)
        self.assertEqual(result["status"],"diagnosing")

    def test_design_http_and_mcp_scope(self):
        body=self.design_worker()
        with self.db.transaction() as db:
            self.tokens["read"]=issue_token(db,self.a["id"],["read"])["token"]
        with self.http() as (_,call), patch("octopus.designs.worker_request",
                                          return_value={"boundary":"fixture","workflow_design":True}):
            self.assertEqual(call("POST","/v1/designs","read",body)[0],403)
            code,result=call("POST","/v1/designs",body=body)
            self.assertEqual(code,201)
            self.assertEqual(call("GET","/v1/designs/"+result["id"],"read")[1]["status"],"preparing")
            self.assertEqual(call("GET","/v1/designs/"+result["id"],"bob")[0],404)
        class Client:
            def request(inner,method,path,body=None):return {"method":method,"path":path}
        self.assertEqual(mcp.call(Client(),"octopus_recommend_workflow",body)["path"],"/v1/designs")
        self.assertEqual(mcp.call(Client(),"octopus_workflow_recommendation",{"id":result["id"]})["path"],
                         "/v1/designs/"+result["id"])

    def test_intent_manual_workflow_and_custom_tasks_win(self):
        goal = "修复登录失败的 Bug"
        preview = self.service.resolve_workflow(self.a, {"input": goal, "workflow_id": self.flow["id"]})
        self.assertEqual(preview["selection"]["workflow_id"], self.flow["id"])
        self.assertEqual(preview["steps"], SPEC)
        custom = self.service.resolve_workflow(self.b, {"input": goal, "steps": SPEC})
        self.assertEqual(custom["mode"], "manual")
        self.assertEqual(custom["selection"]["steps"], SPEC)
        with self.assertRaises(Error):
            self.service.resolve_workflow(self.a, {"input": goal, "steps": SPEC, "workflow_id": self.flow["id"]})

    def test_intent_matching_filters_roles_and_revoked_team_access(self):
        goal = "使用研发流程完成本次代码修改"
        self.assertNotIn("workflow_id", self.service.resolve_workflow(self.b, {"input": goal})["selection"])
        self.service.share(self.a, self.flow["id"], {"kind": "user", "principal": self.b["id"], "role": "viewer"})
        self.assertNotIn("workflow_id", self.service.resolve_workflow(self.b, {"input": goal})["selection"])
        with self.assertRaises(Error):
            self.service.resolve_workflow(self.b, {"input": goal, "workflow_id": self.flow["id"]})
        team = self.team()
        self.service.share(self.a, self.flow["id"], {"kind": "team", "principal": team["id"], "role": "runner"})
        self.assertEqual(self.service.resolve_workflow(self.b, {"input": goal})["selection"]["workflow_id"], self.flow["id"])
        self.service.membership(self.a, team["id"], {"username": "bob"}, remove=True)
        self.assertNotIn("workflow_id", self.service.resolve_workflow(self.b, {"input": goal})["selection"])
        with self.assertRaises(Error):
            self.service.resolve_workflow(self.b, {"input": goal, "team_id": team["id"]})

    def test_preview_pins_workflow_version_and_run_name(self):
        preview = self.service.resolve_workflow(self.a, {"input": "使用研发流程完成本次需求"})
        changed = copy.deepcopy(SPEC);changed[0]["model"] = "new-version-model"
        self.service.save_workflow(self.a, {"name": "研发流程", "steps": changed, "expected_version": 1}, self.flow["id"])
        result = self.service.start(self.a, {"request_id": "pinned-preview", "input": preview["input"],
            "name": "手动命名的需求", "source": {"type": "new"}, **preview["selection"]})
        run = self.service.run(self.a, result["id"])
        self.assertEqual(run["workflow_version"], 1)
        self.assertEqual(run["tasks"][0]["spec"]["model"], "auto")
        self.assertEqual(run["name"], "手动命名的需求")
        self.assertEqual(run["input"], preview["input"])

    def test_start_from_intent_without_precreating_workflow(self):
        body = {"request_id": "intent-no-workflow", "input": "快速验证一个最小想法", "source": {"type": "new"}, "plan": "match"}
        started = self.service.start(self.b, body)
        self.assertEqual(started, self.service.start(self.b, body))
        run = self.service.run(self.b, started["id"])
        self.assertEqual(run["selection"], {"template_id": "proof"})
        self.assertEqual(run["match_mode"], "rules")
        self.assertEqual(run["status"], "waiting_worker")
        self.assertEqual(run["progress"]["total"], 2)
        self.assertEqual(self.service.workflows(self.b), [])
        self.assertIsNone(run["workspace"])

    def test_waiting_worker_attention_for_a_personal_space(self):
        run = self.service.run(self.b, self.start(self.b, workflow_id=None, template_id="proof")["id"])
        self.assertEqual(run["attention"]["title"], "个人空间没有可用 Worker")
        self.assertEqual(run["attention"]["workers"], 0)
        with self.db.transaction() as db:
            for wid in ("w1", "w2"):
                db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES(?,?,?,?)", (wid, wid, self.a["id"], dumps({})))
            db.execute("INSERT INTO workers(id,name,owner_id,config,enabled) VALUES('w3','w3',?,?,0)", (self.a["id"], dumps({})))
        run = self.service.run(self.a, self.start()["id"])
        self.assertEqual(run["status"], "waiting_worker")
        self.assertEqual(run["attention"]["workers"], 2)
        self.assertEqual(run["attention"]["title"], "需求已保存，等待选择 Worker")
        self.assertIn("2 个可用 Worker", run["attention"]["detail"])

    def test_intent_http_preview_uses_read_scope_and_mcp_same_api(self):
        with self.db.transaction() as db:
            self.tokens["read"] = issue_token(db, self.b["id"], ["read"])["token"]
        with self.http() as (_, call):
            status, preview = call("POST", "/v1/workflows/resolve", "read", {"input": "修复一个 Bug"})
            self.assertEqual(status, 200)
            self.assertEqual(preview["selection"]["template_id"], "bugfix")
            self.assertEqual(len(call("GET", "/v1/workflow-templates", "read")[1]), 15)
            self.assertEqual(call("POST", "/v1/runs", "read", {"input": "修复一个 Bug"})[0], 403)
        class Client:
            def request(inner, method, path, body):
                self.assertEqual((method, path), ("POST", "/v1/workflows/resolve"))
                return self.service.resolve_workflow(self.b, body)
        result = mcp.call(Client(), "octopus_match_workflow", {"input": "修复一个 Bug"})
        self.assertEqual(result["selection"], preview["selection"])
        self.assertIn("/v1/workflows/resolve", openapi()["paths"])

    def test_ambiguous_workflow_names_require_an_explicit_id(self):
        other = self.service.save_workflow(self.b, {"name": "研发流程", "steps": SPEC})
        self.service.share(self.b, other["id"], {"kind": "user", "principal": self.a["id"], "role": "runner"})
        with self.assertRaises(Error) as ctx:
            self.service.resolve_workflow(self.a, {"input": "使用研发流程完成本次需求"})
        self.assertEqual(ctx.exception.code, "ambiguous_workflow")
        chosen = self.service.resolve_workflow(self.a, {"input": "使用研发流程完成本次需求", "workflow_id": other["id"]})
        self.assertEqual(chosen["selection"]["workflow_id"], other["id"])

    def test_private_and_individual_sharing_with_roles(self):
        self.assertEqual(self.service.workflows(self.b), [])
        with self.assertRaises(Error):
            self.start(self.b)
        self.service.share(self.a, self.flow["id"], {"kind": "user", "principal": self.b["id"], "role": "viewer"})
        self.assertEqual(self.service.workflows(self.b)[0]["role"], "viewer")
        with self.assertRaises(Error):
            self.start(self.b)
        self.service.share(self.a, self.flow["id"], {"kind": "user", "principal": self.b["id"], "role": "runner"})
        run = self.start(self.b)
        with self.assertRaises(Error):  # Workflow ownership does not reach the recipients' private runs ...
            self.service.run({**self.a, "scopes": ["read", "execute"]}, run["id"])
        self.assertEqual(self.service.run(self.a, run["id"])["id"], run["id"])  # ... admin oversight does (A2).
        with self.assertRaises(Error):
            self.service.save_workflow(self.b, {"name": "窃改", "steps": SPEC, "expected_version": 1}, self.flow["id"])
        with self.assertRaises(Error):
            self.service.share(self.b, self.flow["id"], {"kind": "user", "principal": self.e["id"]})

    def test_team_share_and_revocation_apply_to_history_and_events(self):
        team = self.team()
        self.service.share(self.a, self.flow["id"], {"kind": "team", "principal": team["id"], "role": "runner"})
        run = self.start(self.b, team_id=team["id"])
        self.assertEqual(self.service.run(self.a, run["id"])["team_id"], team["id"])
        self.assertTrue(self.service.events(self.b, run["id"]))
        self.service.membership(self.a, team["id"], {"username": "bob"}, remove=True)
        self.assertEqual(self.service.workflows(self.b), [])
        for method in [self.service.run, self.service.events]:
            with self.assertRaises(Error):
                method(self.b, run["id"])
        self.assertEqual(self.service.runs(self.e), [])

    def test_last_team_owner_and_nonowner_membership(self):
        team = self.team()
        with self.assertRaises(Error):
            self.service.membership(self.a, team["id"], {"username": "alice"}, remove=True)
        with self.assertRaises(Error):
            self.service.membership(self.b, team["id"], {"username": "eve"})

    def test_versions_and_name_uniqueness(self):
        self.service.share(self.a, self.flow["id"], {"kind": "user", "principal": self.b["id"], "role": "editor"})
        updated = copy.deepcopy(SPEC)
        updated[0]["model"] = "explicit-model"
        new = self.service.save_workflow(self.b, {"name": "研发流程", "steps": updated, "expected_version": 1}, self.flow["id"])
        self.assertEqual(new["version"], 2)
        with self.assertRaises(Error):
            self.service.save_workflow(self.a, {"name": "研发流程", "steps": SPEC, "expected_version": 1}, self.flow["id"])
        with self.db.read() as db:
            self.assertEqual(json.loads(db.execute("SELECT spec FROM workflow_versions WHERE version=1").fetchone()[0]), SPEC)
        with self.assertRaises(Error):
            self.service.save_workflow(self.a, {"name": " 研发流程 ", "steps": SPEC})

    def test_idempotency_and_concurrent_start(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.start())) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len({r["id"] for r in results}), 1)
        with self.assertRaises(Error):
            self.start(input="不同的请求内容")

    def test_custom_tasks_and_source_validation(self):
        run = self.service.start(self.a, {"request_id": "custom-00001", "input": "定制开发任务",
                                         "source": {"type": "new"}, "steps": SPEC[:1]})
        self.assertEqual(self.service.run(self.a, run["id"])["tasks"][0]["spec"], SPEC[0])
        for bad in [SPEC + SPEC, [{**SPEC[0], "tool": "gemini"}], [{**SPEC[0], "effort": "invalid"}]]:
            with self.assertRaises(Error):
                self.service.save_workflow(self.a, {"name": "无效", "steps": bad})
        for source in [None, {"type":"new","url":"https://example.com/r.git"},
                       {"type":"git","url":"/private/host-repo"},
                       {"type":"git","url":"https://user:secret@example.com/r.git"}]:
            with self.assertRaises(Error):
                self.start(source=source)

    def test_continue_preserves_session_and_old_attempt_configuration(self):
        run = self.start()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='blocked',workspace='/same/workspace' WHERE id=?", (run["id"],))
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,created,updated) VALUES('old',?,'code',1,?,'failed',?,?)",
                       (run["id"], dumps(SPEC[0]), time.time(), time.time()))
            db.execute("UPDATE tasks SET status='blocked',current_attempt='old' WHERE run_id=? AND id='code'", (run["id"],))
        result = self.service.operate(self.a, run["id"], "continue", {
            "request_id": "continue-0001", "expected_version": 1, "task_id": "code",
            "execution": {"model": "new-model", "effort": "high"}, "input": "修订后的完整需求"})
        detail = self.service.run(self.a, run["id"])
        self.assertEqual(result["id"], run["id"])
        self.assertEqual(detail["workspace"], "/same/workspace")
        self.assertEqual(detail["tasks"][0]["spec"]["model"], "new-model")
        self.assertEqual(detail["tasks"][0]["attempts"][0]["spec"]["model"], "auto")
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(json.loads(db.execute("SELECT snapshot FROM runs").fetchone()[0])["steps"], SPEC)

    def test_unassigned_stop_close_reopen_delete_are_not_stuck(self):
        run = self.start()
        for version, action in enumerate(["stop", "close", "reopen", "delete"], 1):
            result = self.service.operate(self.a, run["id"], action, {
                "request_id": f"operate-{version:08}", "expected_version": version, "confirmation": run["id"]})
            self.assertEqual(result["version"], version + 1)
            self.assertEqual(self.service.run(self.a, run["id"])["status"], "stopped")
        self.assertEqual(self.service.run(self.a, run["id"])["lifecycle"], "deleted")

    def test_cannot_continue_running_or_skip_dependency(self):
        run = self.start()
        payload = {"request_id": "continue-0001", "expected_version": 1, "task_id": "review"}
        with self.assertRaises(Error):
            self.service.operate(self.a, run["id"], "continue", payload)
        with self.db.transaction() as db: db.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
        with self.assertRaises(Error):
            self.service.operate(self.a, run["id"], "continue", {**payload, "task_id": "code"})

    def fake_worker(self, path, body):
        if path == "/prepare":
            return {"native_parent": "native-" + body["run_id"], "workspace": "/same/workspace"}
        if path == "/dispatch":
            self.dispatched.append(body)
            return {"status": "running", "native_id": "abc12345", "context_hash": "hash"}
        if path == "/inspect":
            tool = next(c["spec"]["tool"] for c in self.dispatched if c["attempt_id"] == body["attempt_id"])
            return {"status": "running", "native": {"done": True, "result": "Implemented and verified", "error": ""},
                    "evidence": {"actual_backend": tool}, "handoff": "Real fixture handoff",
                    "outcome":dumps({"decision":"pass","summary":"Fixture implementation verified"})}
        if path == "/control":
            return {"status": "stopped", "lifecycle": "open"}
        raise AssertionError(path)

    def test_engine_native_chain_and_recovery(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','Test',?,?)",
                       (self.a["id"], dumps({"url": "fixture", "token": "fake"})))
        run = self.start(worker_id="worker")
        self.dispatched = []
        engine = Engine(self.db, request=lambda config, path, body, **kw:self.fake_worker(path, body))
        for _ in range(10): engine.tick()
        detail = self.service.run(self.a, run["id"])
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(len(self.dispatched), 2)
        Engine(self.db, request=lambda *a, **kw: self.fail("completed run redispatched")).tick()
        self.assertEqual(detail["progress"], {"accepted": 2, "total": 2})

    def test_dispatch_unknown_never_resubmits(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','Test',?,?)",
                       (self.a["id"], dumps({})))
        run = self.start(worker_id="worker")
        calls = []
        def request(config, path, body, **kw):
            calls.append(path)
            if path == "/prepare": return {"native_parent": "parent", "workspace": "/same"}
            return {"status": "dispatch_unknown", "error": "no receipt"}
        engine = Engine(self.db, request=request)
        for _ in range(6): engine.tick()
        self.assertEqual(calls.count("/dispatch"), 1)
        self.assertEqual(self.service.run(self.a, run["id"])["status"], "dispatch_unknown")

    def test_http_scopes_and_token_revocation(self):
        http = server(self.db, 0)
        thread = threading.Thread(target=http.serve_forever, daemon=True);thread.start()
        port = http.server_address[1]
        def call(method, path, token, body=None):
            request = urllib.request.Request(f"http://127.0.0.1:{port}" + path, method=method,
                data=dumps(body).encode() if body is not None else None,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=3) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as exc:
                with exc:return exc.code,json.load(exc)
        try:
            self.assertEqual(call("GET", "/v1/workflows", "")[0], 401)
            self.assertEqual(call("GET", "/v1/workflows", self.tokens["bob"])[1], [])
            with self.db.transaction() as db:
                read_only = issue_token(db, self.a["id"], ["read"])["token"]
            self.assertEqual(call("POST", "/v1/teams", read_only, {"name": "Forbidden"})[0], 403)
            self.assertEqual(call("POST", "/v1/users", self.tokens["bob"], {"username": "hacker", "password": "Test-password-123!"})[0], 403)
            with self.db.transaction() as db:
                db.execute("DELETE FROM tokens WHERE hash=?", (hashlib.sha256(read_only.encode()).hexdigest(),))
            self.assertEqual(call("GET", "/v1/me", read_only)[0], 401)
        finally:
            http.shutdown();http.server_close();thread.join(2)

    def test_mcp_uses_the_same_api_and_does_not_expose_token(self):
        class Client:
            def request(inner, method, path, body=None):
                return {"method": method, "path": path, "body": body}
        output = io.StringIO()
        mcp.serve(Client(), io.StringIO(dumps({"jsonrpc":"2.0", "id":1, "method":"tools/call",
                                              "params":{"name":"octopus_status", "arguments":{"run_id":"run-example", "detail":True}}}) + "\n"), output)
        response = json.loads(output.getvalue())
        self.assertEqual(response["result"]["structuredContent"]["session"]["path"], "/v1/runs/run-example")
        self.assertNotIn("Bearer", output.getvalue())

    def worker_run(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','Test',?,?)",
                       (self.a["id"], dumps({})))
        return self.start(worker_id="worker")

    def test_stop_during_prepare_retains_native_binding(self):
        run = self.worker_run()
        def request(config, path, body, **kw):
            if path == "/prepare":
                self.service.operate(self.a, run["id"], "stop", {"request_id":"stop-00001", "expected_version":1})
                return {"native_parent":"created-after-stop", "workspace":"/retained"}
            self.assertEqual(path, "/control")
            return {"status":"stopped", "lifecycle":"open"}
        engine = Engine(self.db, request=request)
        engine.tick()
        detail = self.service.run(self.a, run["id"])
        self.assertEqual(detail["status"], "stopping")
        self.assertEqual(detail["native_parent"], "created-after-stop")
        engine.tick()
        self.assertEqual(self.service.run(self.a, run["id"])["status"], "stopped")

    def test_lost_worker_ack_reuses_same_attempt(self):
        run = self.worker_run()
        self.dispatched = []
        attempts = []
        def request(config, path, body, **kw):
            if path == "/dispatch":
                attempts.append(body["attempt_id"])
                if len(attempts) == 1:
                    raise Error(503, "worker_unavailable", "Lost ACK after submission")
            return self.fake_worker(path, body)
        engine = Engine(self.db, request=request)
        engine.tick();engine.tick()
        self.assertEqual(self.service.run(self.a, run["id"])["status"], "dispatch_unknown")
        with self.assertRaises(Error):
            self.service.operate(self.a, run["id"], "continue", {
                "request_id":"retry-00001","expected_version":1,"task_id":"code"})
        engine.tick()
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(len(self.service.run(self.a, run["id"])["tasks"][0]["attempts"]), 1)

    def test_late_result_does_not_complete_task_after_stop(self):
        run = self.worker_run()
        self.dispatched = []
        def request(config, path, body, **kw):
            if path == "/inspect":
                self.service.operate(self.a, run["id"], "stop", {"request_id":"stop-00001","expected_version":1})
            return self.fake_worker(path, body)
        engine = Engine(self.db, request=request)
        for _ in range(3):engine.tick()
        result = self.service.run(self.a, run["id"])
        self.assertEqual(result["status"], "stopping")
        self.assertNotEqual(result["tasks"][0]["status"], "accepted")

    @contextmanager
    def http(self):
        app = server(self.db, 0)
        thread = threading.Thread(target=app.serve_forever, daemon=True);thread.start()
        base = f"http://127.0.0.1:{app.server_address[1]}"
        def call(method, path, actor="alice", body=None):
            request = urllib.request.Request(base+path, method=method,
                data=dumps(body).encode() if body is not None else None,
                headers={"Authorization":"Bearer "+self.tokens[actor], "Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as response:
                with response:return response.code,json.load(response)
        try:yield base,call
        finally:app.shutdown();app.server_close();thread.join(2)

    def test_worker_team_boundary_and_client_visibility(self):
        team = self.legacy_team()
        health = {"isolation":"trusted-local","boundary":"a","host_principal":"host-1","models":{}}
        connection = {"url":"http://127.0.0.1:8928","token":"private-worker-secret"+"x"*32}
        with self.http() as (_, call), patch("octopus.server.validate_worker", return_value=health):
            body = {"name":"Worker","team_id":team["id"],"connection":connection}
            self.assertEqual(call("POST","/v1/workers","bob",body)[0],403)
            self.assertEqual(call("POST","/v1/workers",body=body)[0],409)
            health["isolation"]="dedicated"
            status,worker=call("POST","/v1/workers",body=body)
            self.assertEqual(status,201)
            for actor in ["alice","bob"]:
                listed=call("GET","/v1/workers",actor)[1]
                self.assertEqual(listed[0]["id"],worker["id"])
                self.assertNotIn(connection["token"],dumps(listed))
            self.assertEqual(call("GET","/v1/workers","eve")[1],[])
            self.assertEqual(call("POST","/v1/workers",body=body)[0],409)
            other = self.legacy_team("Other", members=())
            health["boundary"]="b"  # another HOME under same host account is not isolation
            self.assertEqual(call("POST","/v1/workers",body={**body,"team_id":other["id"]})[0],409)

    def test_sse_revokes_open_stream_and_replay_is_scoped(self):
        team=self.team()
        self.service.share(self.a,self.flow["id"],{"kind":"team","principal":team["id"],"role":"runner"})
        run=self.start(self.b,team_id=team["id"])
        with self.http() as (base,call):
            self.assertEqual(call("GET","/v1/runs/"+run["id"]+"/events","eve")[0],404)
            request=urllib.request.Request(base+"/v1/runs/"+run["id"]+"/events?stream=1",
                headers={"Authorization":"Bearer "+self.tokens["bob"]})
            with urllib.request.urlopen(request,timeout=5) as response:
                event_id=response.readline().decode().strip()
                self.assertTrue(event_id.startswith("id: "))
                self.service.membership(self.a,team["id"],{"username":"bob"},remove=True)
                lines=[]
                while True:
                    line=response.readline()
                    if not line:break
                    lines.append(line.decode())
                    if b"access_revoked" in line:break
                self.assertIn("access_revoked","".join(lines))
            self.assertEqual(call("GET","/v1/runs/"+run["id"]+"/export","bob")[0],404)

    def test_empty_scope_cannot_escalate_and_wrong_verbs_do_not_mutate(self):
        with self.http() as (_,call):
            self.assertEqual(call("POST","/v1/tokens",body={"scopes":[]})[0],400)
            self.assertEqual(call("DELETE","/v1/teams",body={"name":"must-not-create"})[0],405)
            self.assertEqual(call("POST","/v1/me",body={})[0],405)
            self.assertEqual(call("POST","/v1/users",body={"username":"new-user","password":"long-password-test","admin":"false"})[0],400)
            self.assertEqual(call("POST","/v1/logout",body={})[0],200)
            self.assertEqual(call("GET","/v1/me")[0],401)

    def native_worker(self):
        node=Worker(Path(self.tmp.name)/"worker","x"*40,crew_home=Path(self.tmp.name)/"crew")
        rid="run-"+"1"*32
        def native(path,body=None,method=None):
            if path=="/api/chat/slots/octopus-"+rid and body is None:
                raise crew_api.GatewayError(404,'{"error":"not found"}')
            if path=="/api/chat/slots":return {"key":"octopus-"+rid}
            return {"ok":True}
        node.native=native
        node.prepare({"run_id":rid,"source":{"type":"new"}})
        return node, {"run_id":rid,"attempt_id":"attempt-"+"2"*32,"input":"test requirement","spec":SPEC[0],"upstream":[]}

    def test_native_dispatch_contract_and_receipt_recovery(self):
        node,body=self.native_worker()
        spawned=[]
        def native(path,payload=None,method=None):
            if path=="/api/spawn" and payload:
                spawned.append(payload)
                raise TimeoutError("accepted but response lost")
            if path=="/api/spawn":
                return {"agents":[{"id":"aabb1234","parent":"dashboard:octopus-"+body["run_id"],"task":spawned[0]["task"]}]}
            raise AssertionError(path)
        node.native=native
        self.assertEqual(node.dispatch(body)["status"],"dispatch_unknown")
        result=node.dispatch(body)
        self.assertEqual(result["native_id"],"aabb1234")
        self.assertEqual(len(spawned),1)
        self.assertEqual(spawned[0]["agent"],"poc-claude")
        self.assertFalse(spawned[0]["include_memory"])
        self.assertTrue(spawned[0]["silent"])
        with self.assertRaises(Error):node.dispatch({**body,"input":"different"})

    def test_native_refusal_is_not_misreported_as_accepted(self):
        node,body=self.native_worker()
        def refused(*args,**kwargs):raise crew_api.GatewayError(400,'{"error":"model rejected"}')
        node.native=refused
        result=node.dispatch(body)
        self.assertEqual(result["status"],"blocked")
        self.assertIn("model rejected",result["error"])
        self.assertEqual(node.dispatch(body)["status"],"blocked")

    def test_advertised_model_gate_and_requested_not_actual(self):
        node,body=self.native_worker()
        with self.assertRaises(Error):node.dispatch({**body,"spec":{**SPEC[0],"model":"unadvertised"}})
        parent="dashboard:octopus-"+body["run_id"]
        record={"id":"aabb1234","parent":parent,"done":True,"result":"done"}
        def native(path,payload=None,method=None):
            if path=="/api/spawn":return {"id":record["id"]} if payload else {"agents":[record]}
            if path=="/api/spawn/aabb1234":return record
            return {"ok":True}
        node.native=native
        node.dispatch(body)
        write(node.home/"subagents/aabb1234/state.json",{"id":"aabb1234","parent_session":parent,
              "requested_model":"fake-requested-model","session_id":"session-real"})
        value=node.inspect(body)
        self.assertIsNone(value["evidence"]["actual_model"])
        self.assertIsNone(value["evidence"]["actual_backend"])
        self.assertEqual(value["evidence"]["native_session_id"],"session-real")
        write(node.home/"subagents/aabb1234/state.json",{"id":"aabb1234","parent_session":parent,"resolved_model":"actual[high]"})
        value=node.inspect(body)
        self.assertEqual(value["evidence"]["actual_model"],"actual[high]")
        self.assertEqual(value["evidence"]["actual_effort"],"high")

    def test_bad_backup_never_closes_native_session(self):
        node,body=self.native_worker()
        calls=[]
        def native(path,payload=None,method=None):
            calls.append((path,method))
            if path=="/api/spawn":return {"agents":[]}
            return {"ok":True,"running":False,"queue_depth":0}
        node.native=native
        with patch("api.token",return_value="test-only"),patch("urllib.request.urlopen",return_value=io.BytesIO(b"not gzip")):
            with self.assertRaises(OSError):node.control({"run_id":body["run_id"],"action":"close"})
        self.assertFalse(any(method=="DELETE" for _,method in calls))

    def test_empty_native_bundle_has_labelled_observation_not_fake_export(self):
        node,body=self.native_worker()
        calls=[]
        def native(path,payload=None,method=None):
            calls.append((path,method))
            return {"agents":[]} if path=="/api/spawn" else {"ok":True,"running":False,"queue_depth":0}
        node.native=native
        refusal=urllib.error.HTTPError("fixture",400,"empty",{},io.BytesIO(b'{"code":"export_bundle_empty"}'))
        with patch("api.token",return_value="fixture"),patch("urllib.request.urlopen",side_effect=refusal):
            result=node.control({"run_id":body["run_id"],"action":"close"})
        self.assertEqual(result["lifecycle"],"closed")
        self.assertEqual(result["backup_kind"],"native_empty_observation")
        root=node.path(body["run_id"])
        self.assertFalse((root/"native-session-backup.gz").exists())
        self.assertEqual(json.loads((root/"empty-session-observation.json").read_text())["slot"],"octopus-"+body["run_id"])

    def test_incognito_export_refusal_never_closes_or_makes_fake_backup(self):
        node,body=self.native_worker()
        calls=[]
        def native(path,payload=None,method=None):
            calls.append((path,method))
            return {"agents":[]} if path=="/api/spawn" else {"ok":True,"running":False}
        node.native=native
        refusal=urllib.error.HTTPError("fixture",400,"private",{},io.BytesIO(b'{"code":"export_slot_not_persistent"}'))
        with patch("api.token",return_value="fixture"),patch("urllib.request.urlopen",side_effect=refusal):
            with self.assertRaises(Error):node.control({"run_id":body["run_id"],"action":"close"})
        self.assertFalse(any(method=="DELETE" for _,method in calls))
        self.assertFalse((node.path(body["run_id"])/"empty-session-observation.json").exists())

    def test_openapi_inputs_cover_direct_custom_tasks(self):
        spec=openapi()
        self.assertEqual(spec["openapi"],"3.1.0")
        self.assertIn("steps",spec["components"]["schemas"]["Start"]["properties"])
        self.assertIn("execution",spec["components"]["schemas"]["Continue"]["properties"])
        self.assertIn("text/event-stream",spec["paths"]["/v1/runs/{id}/events"]["get"]["responses"]["200"]["content"])
        self.assertIn("/v1/workflows/{id}/shares",spec["paths"])


class EnvironmentTests(unittest.TestCase):
    """Execution environments (D28): dedicated, shared through worker_spaces, spare. Reuses MVPTests' fixtures
    without inheriting (and re-running) its tests."""
    setUp, tearDown, start, http, worker_run = (MVPTests.setUp, MVPTests.tearDown, MVPTests.start, MVPTests.http,
                                               MVPTests.worker_run)

    def shared(self, wid="shared"):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?, 'shared')",
                       (wid, wid, self.a["id"], dumps({"capabilities": {}})))
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, self.a["id"]))
        return wid

    def shared_team(self, wid, name="共享 Team"):
        tid = "team-" + name.encode().hex()[:12]
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams VALUES(?,?,0)", (tid, name))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (tid, self.a["id"]))
            db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, self.b["id"]))
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (wid, tid))
        return tid

    def environment_fixture(self):
        """A shared environment serving alice and a Team (one active Session on it), and an unreachable spare host."""
        wid = self.shared()
        tid = self.shared_team(wid)
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id=?", (dumps({"url": "http://127.0.0.1:8928", "token": "s" * 40,
                       "capabilities": {"isolation": "trusted-local"}}), wid))
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('sp','w1',?,?,'spare')",
                       (self.a["id"], dumps({"url": "https://10.0.0.9:8943", "token": "t" * 40, "capabilities": {}})))
        self.start(team_id=tid)
        return wid, tid

    def test_admin_environments_list_bindings_load_and_health_without_secrets(self):
        wid, tid = self.environment_fixture()
        def health(config, path, body=None, timeout=35):
            self.assertEqual((path, body), ("/health", None))
            self.assertLessEqual(timeout, 5)
            if "10.0.0.9" in config["url"]:
                raise Error(503, "worker_unavailable", "Worker 未返回可核实回执")
            return {"isolation": "trusted-local", "boundary": "b" * 64, "crew_entry": True,
                    "crew_origin": "https://d1.cloudfront.net", "version": "1.0.0"}
        with patch("octopus.transport.worker_request", side_effect=health):
            listing = {e["id"]: e for e in self.service.environments(self.a)}
        shared = listing[wid]
        self.assertEqual((shared["scope"], shared["enabled"], shared["active_runs"]), ("shared", True, 1))
        self.assertEqual(shared["owner"], {"id": self.a["id"], "username": "alice"})
        self.assertEqual(shared["spaces"], [{"user_id": self.a["id"], "username": "alice"},
                                            {"team_id": tid, "name": "共享 Team"}])
        self.assertEqual(shared["url"], "http://127.0.0.1:8928")
        self.assertEqual(shared["health"], {"reachable": True, "isolation": "trusted-local", "crew_entry": True,
                                            "crew_origin": "https://d1.cloudfront.net", "version": "1.0.0"})
        spare = listing["sp"]
        self.assertEqual((spare["scope"], spare["spaces"], spare["active_runs"]), ("spare", [], 0))
        self.assertEqual(spare["health"], {"reachable": False, "error": "Worker 未返回可核实回执"})
        printed = dumps(listing)
        self.assertNotIn("s" * 40, printed)
        self.assertNotIn("t" * 40, printed)
        self.assertNotIn('"token"', printed)
        with self.assertRaises(Error) as caught:
            self.service.environments(self.b)
        self.assertEqual(caught.exception.code, "forbidden")

    def test_one_misbehaving_worker_never_fails_the_environment_listing(self):
        import http.client
        self.environment_fixture()
        for failure in (http.client.BadStatusLine("SSH-2.0-OpenSSH"), http.client.IncompleteRead(b"{"), KeyError("token"),
                        ["not", "an", "object"], "text"):
            def answer(config, path, body=None, timeout=35, failure=failure):
                if "10.0.0.9" not in config["url"]:
                    return {"isolation": "trusted-local"}
                if isinstance(failure, Exception):
                    raise failure
                return failure
            with patch("octopus.transport.worker_request", side_effect=answer):
                listing = {e["id"]: e for e in self.service.environments(self.a)}
            self.assertEqual(listing["sp"]["health"]["reachable"], False, failure)
            self.assertTrue(listing["sp"]["health"]["error"], failure)
            self.assertEqual(listing["shared"]["health"], {"reachable": True, "isolation": "trusted-local"})

    def test_admin_environment_health_checks_run_concurrently(self):
        self.environment_fixture()
        def slow(config, path, body=None, timeout=35):
            time.sleep(0.4)
            return {"isolation": "dedicated"}
        began = time.monotonic()
        with patch("octopus.transport.worker_request", side_effect=slow):
            listing = self.service.environments(self.a)
        self.assertEqual(len(listing), 2)
        self.assertLess(time.monotonic() - began, 0.75)

    def test_admin_environments_route_and_openapi_tag(self):
        self.environment_fixture()
        with self.http() as (_, call), patch("octopus.transport.worker_request", return_value={"isolation": "dedicated"}):
            status, listing = call("GET", "/v1/admin/environments")
            self.assertEqual((status, len(listing)), (200, 2))
            self.assertEqual(call("GET", "/v1/admin/environments", "bob")[0], 403)
        paths = openapi()["paths"]
        tag = "执行环境（管理员）"
        for path, method in (("/v1/admin/environments", "get"), ("/v1/workers", "post"),
                             ("/v1/workers/{id}/enabled", "post"), ("/v1/workers/{id}/share", "post"),
                             ("/v1/workers/{id}/spaces", "post"), ("/v1/workers/{id}/spaces", "delete"),
                             ("/v1/workers/{id}/connection", "post"), ("/v1/workers/{id}/dedicate", "post"),
                             ("/v1/teams", "post")):
            self.assertEqual(paths[path][method].get("tags"), [tag], (path, method))
        self.assertNotIn("tags", paths["/v1/runs"]["post"])
        observe = paths["/v1/runs/{id}/observe"]["get"]
        self.assertEqual({p["name"] for p in observe["parameters"]}, {"id", "before", "limit"})
        schemas = openapi()["components"]["schemas"]
        self.assertEqual(schemas["Team"]["required"], ["name", "owner", "worker_id"])
        self.assertIn("worker_id", schemas["User"]["properties"])

    def test_space_workers_cover_dedicated_shared_and_spare(self):
        from octopus.domain import space_workers
        wid = self.shared()
        tid = self.shared_team(wid)
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('sp','spare',?,'{}','spare')", (self.a["id"],))
        with self.db.read() as db:
            self.assertEqual([r["id"] for r in space_workers(db, self.a["id"], tid)], [wid])
            self.assertEqual([r["id"] for r in space_workers(db, self.a["id"], None)], [wid])
            self.assertEqual(space_workers(db, self.e["id"], None), [])

    def test_server_picks_the_only_usable_worker(self):
        wid = self.shared()
        with self.db.transaction() as db:
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, self.b["id"]))
        run = self.service.run(self.b, self.start(self.b, workflow_id=None, template_id="proof")["id"])
        self.assertEqual((run["worker_id"], run["status"]), (wid, "preparing"))

    def test_team_without_environment_is_refused(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams VALUES('t-legacy','旧 Team',0)")
            db.execute("INSERT INTO members VALUES('t-legacy',?,'owner')", (self.a["id"],))
        with self.assertRaises(Error) as caught:
            self.start(team_id="t-legacy")
        self.assertEqual(caught.exception.code, "team_without_worker")
        self.assertIn("旧 Team", str(caught.exception))

    def test_shared_worker_id_from_old_client_is_accepted(self):
        wid = self.shared()
        tid = self.shared_team(wid)
        self.assertEqual(self.service.run(self.a, self.start(team_id=tid, worker_id=wid)["id"])["worker_id"], wid)

    def test_disabled_shared_worker_counts_as_none(self):
        wid = self.shared()
        tid = self.shared_team(wid)
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id=?", (wid,))
        with self.assertRaises(Error) as caught:
            self.start(team_id=tid)
        self.assertEqual(caught.exception.code, "environment_disabled")
        self.assertIn("已停用", str(caught.exception))
        self.assertFalse({t["id"]: t for t in self.service.teams(self.a)}[tid]["environment"]["enabled"])

    def test_one_environment_per_team_counts_disabled_ones(self):
        wid, other = self.shared(), self.shared("shared-2")
        tid = self.shared_team(wid)
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id=?", (wid,))
        with self.assertRaises(Error) as caught:   # not an IntegrityError turned into a 500
            self.service.worker_space(self.a, other, {"team_id": tid})
        self.assertEqual(caught.exception.code, "team_has_environment")
        health = {"isolation": "dedicated", "boundary": "b-team", "host_principal": "hp-team"}
        body = {"name": "team-host", "team_id": tid, "connection": {"url": "https://10.0.0.8:8943", "token": "t" * 40}}
        with patch("octopus.server.validate_worker", return_value=health), self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers", "alice", body)[0], 409)

    def test_a_spare_can_be_assigned_to_an_existing_team(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams VALUES('t-legacy','旧 Team',0)")
            db.execute("INSERT INTO members VALUES('t-legacy',?,'owner')", (self.a["id"],))
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('sp','team-host-1',?,'{}','spare')",
                       (self.a["id"],))
        with self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers/sp/spaces", "alice", {"team_id": "t-legacy"})[0], 200)
            self.assertEqual(call("POST", "/v1/workers/sp/spaces", "alice", {"user_id": self.b["id"]})[0], 409)
        with self.db.read() as db:
            row = db.execute("SELECT scope,team_id FROM workers WHERE id='sp'").fetchone()
        self.assertEqual(tuple(row), ("dedicated", "t-legacy"))

    def test_legacy_waiting_run_can_use_shared_worker(self):
        wid = self.shared()
        tid = self.shared_team(wid)
        started = self.start(team_id=tid)
        with self.db.transaction() as db:   # as left by the platform before environments existed
            db.execute("UPDATE runs SET worker_id=NULL, status='waiting_worker' WHERE id=?", (started["id"],))
        self.service.operate(self.a, started["id"], "assign-worker",
                             {"request_id": "assign-00001", "expected_version": 1, "worker_id": wid})
        self.assertEqual(self.service.run(self.a, started["id"])["status"], "preparing")

    def test_only_admins_create_teams_and_an_environment_is_required(self):
        wid = self.shared()
        with self.assertRaises(Error) as caught:
            self.service.create_team(self.b, {"name": "B", "owner": "bob", "worker_id": wid})
        self.assertEqual(caught.exception.code, "forbidden")
        with self.assertRaises(Error) as caught:
            self.service.create_team(self.a, {"name": "B", "owner": "bob"})
        self.assertEqual(caught.exception.code, "worker_required")
        t = self.service.create_team(self.a, {"name": "B", "owner": "bob", "worker_id": wid})
        self.assertEqual(t["environment"]["scope"], "shared")
        self.assertEqual({x["id"]: x["role"] for x in self.service.teams(self.b)}[t["id"]], "owner")
        mine = {x["id"]: x for x in self.service.teams(self.a)}     # the admin sees every Team
        self.assertIsNone(mine[t["id"]]["role"])
        self.assertEqual(mine[t["id"]]["environment"]["worker_id"], wid)

    def test_spare_is_assigned_once(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('sp','team-host-1',?,?,'spare')",
                       (self.a["id"], dumps({"capabilities": {"isolation": "dedicated"}})))
        t1 = self.service.create_team(self.a, {"name": "T1", "owner": "alice", "worker_id": "sp"})
        with self.db.read() as db:
            row = db.execute("SELECT scope,team_id,owner_id FROM workers WHERE id='sp'").fetchone()
        self.assertEqual(tuple(row), ("dedicated", t1["id"], None))
        with self.assertRaises(Error) as caught:
            self.service.create_team(self.a, {"name": "T2", "owner": "alice", "worker_id": "sp"})
        self.assertEqual(caught.exception.code, "worker_unavailable")
        self.assertNotIn("T2", {x["name"] for x in self.service.teams(self.a)})   # nothing half-created

    def test_admin_creates_a_user_on_the_shared_environment(self):
        from octopus.domain import space_workers
        wid = self.shared()
        with self.http() as (_, call):
            status, user = call("POST", "/v1/users", "alice",
                                {"username": "carol", "password": "Test-password-123!", "worker_id": wid})
        self.assertEqual(status, 201)
        with self.db.read() as db:
            self.assertEqual([r["id"] for r in space_workers(db, user["id"], None)], [wid])

    def test_share_requires_confirmation_and_keeps_existing_runs(self):
        run = self.worker_run()
        with self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers/worker/share", "alice", {})[0], 400)
            self.assertEqual(call("POST", "/v1/workers/worker/share", "bob", {"confirm": True})[0], 403)
            self.assertEqual(call("POST", "/v1/workers/worker/share", "alice", {"confirm": True})[0], 200)
            self.assertEqual(call("POST", "/v1/workers/worker/share", "alice", {"confirm": True})[0], 409)
            listed = {w["id"]: w for w in call("GET", "/v1/workers", "alice")[1]}
        self.assertEqual(listed["worker"]["scope"], "shared")
        self.assertIn({"user_id": self.a["id"]}, listed["worker"]["spaces"])
        self.assertEqual(self.service.run(self.a, run["id"])["worker_id"], "worker")

    def test_sharing_keeps_the_boundary_owner(self):
        self.worker_run()                      # dedicated Worker 'worker' of alice
        with self.db.transaction() as db:
            other = create_user(db, "root2", "Test-password-123!", admin=True)
            other = authenticate(db, issue_token(db, other["id"])["token"])
        self.service.share_worker(other, "worker", {"confirm": True})
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT owner_id FROM workers WHERE id='worker'").fetchone()[0], self.a["id"])

    def test_a_worker_on_the_platform_host_account_cannot_be_shared(self):
        import hashlib, os, socket
        local = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('local','local',?,?)",
                       (self.a["id"], dumps({"capabilities": {"host_principal": local, "isolation": "trusted-local"}})))
        with self.assertRaises(Error) as caught:
            self.service.share_worker(self.a, "local", {"confirm": True})
        self.assertEqual(caught.exception.code, "isolation_required")

    def test_spaces_add_and_remove_with_one_environment_per_team(self):
        wid = self.shared()
        t = self.service.create_team(self.a, {"name": "T1", "owner": "bob", "worker_id": wid})
        with self.http() as (_, call):
            self.assertEqual(call("POST", f"/v1/workers/{wid}/spaces", "bob", {"user_id": self.e["id"]})[0], 403)
            self.assertEqual(call("POST", f"/v1/workers/{wid}/spaces", "alice", {"user_id": self.e["id"]})[0], 200)
            self.assertEqual(call("POST", f"/v1/workers/{wid}/spaces", "alice", {"team_id": t["id"]})[0], 409)
            self.assertEqual(call("DELETE", f"/v1/workers/{wid}/spaces", "alice", {"team_id": t["id"]})[0], 200)
            self.assertEqual(call("DELETE", f"/v1/workers/{wid}/spaces", "alice", {"team_id": t["id"]})[0], 404)
            self.assertEqual(call("GET", "/v1/workers", "bob")[1], [])
            eve = call("GET", "/v1/workers", "eve")[1]
        self.assertEqual([(w["id"], w["spaces"]) for w in eve], [(wid, [{"user_id": self.e["id"]}])])

    def test_an_admin_updates_a_workers_connection_after_its_host_is_replaced(self):
        with self.db.transaction() as db:
            for wid, boundary in (("sp", "b-1"), ("other", "b-2")):
                db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'spare')", (wid, wid, self.a["id"],
                           dumps({"url": "https://10.0.0.9:8943", "token": "t" * 40,
                                  "capabilities": {"isolation": "dedicated", "boundary": boundary, "host_principal": wid}})))
        body = {"connection": {"url": "https://10.0.0.7:8943", "token": "n" * 40}}
        replaced = {"isolation": "dedicated", "boundary": "b-3", "host_principal": "new-host"}
        with patch("octopus.server.validate_worker", return_value=replaced), self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers/sp/connection", "bob", body)[0], 403)
            self.assertEqual(call("POST", "/v1/workers/sp/connection", "alice", body)[0], 200)
        with patch("octopus.server.validate_worker", return_value={**replaced, "boundary": "b-2"}), self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers/sp/connection", "alice", body)[0], 409)   # another Worker's boundary
        with patch("octopus.server.validate_worker", return_value={**replaced, "isolation": "trusted-local"}), \
                self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers/sp/connection", "alice", body)[0], 409)   # a spare stays dedicated
        with self.db.read() as db:
            config = json.loads(db.execute("SELECT config FROM workers WHERE id='sp'").fetchone()[0])
        self.assertEqual((config["url"], config["capabilities"]["boundary"]), ("https://10.0.0.7:8943", "b-3"))

    def test_spare_registration_requires_a_dedicated_unseen_boundary(self):
        health = {"isolation": "dedicated", "boundary": "b-1", "host_principal": "hp-1"}
        body = {"name": "team-host-1", "spare": True, "connection": {"url": "https://10.0.0.9:8943", "token": "t" * 40}}
        with patch("octopus.server.validate_worker", return_value=health), self.http() as (_, call):
            status, created = call("POST", "/v1/workers", "alice", body)
            self.assertEqual(status, 201)
            self.assertEqual(call("POST", "/v1/workers", "alice", {**body, "name": "again"})[0], 409)
            self.assertEqual(call("POST", "/v1/workers", "alice", {**body, "owner_id": self.a["id"]})[0], 400)
            listed = {w["id"]: w for w in call("GET", "/v1/workers", "alice")[1]}
        self.assertEqual(listed[created["id"]]["scope"], "spare")
        local = {**health, "boundary": "b-2", "host_principal": "hp-2", "isolation": "trusted-local"}
        with patch("octopus.server.validate_worker", return_value=local), self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers", "alice", {**body, "name": "x"})[0], 409)
        with self.db.transaction() as db:   # a disabled Worker's boundary is still taken
            db.execute("UPDATE workers SET enabled=0 WHERE id=?", (created["id"],))
        with patch("octopus.server.validate_worker", return_value=health), self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers", "alice", {**body, "name": "y"})[0], 409)

    def test_waiting_worker_attention_explains_a_team_without_a_worker(self):
        wid = self.shared()
        tid = self.shared_team(wid)
        started = self.start(team_id=tid)
        with self.db.transaction() as db:   # the Team's environment was removed while this run waited
            db.execute("DELETE FROM worker_spaces WHERE team_id=?", (tid,))
            db.execute("UPDATE runs SET worker_id=NULL, status='waiting_worker' WHERE id=?", (started["id"],))
        attention = self.service.run(self.b, started["id"])["attention"]
        self.assertEqual(attention["title"], "Team「共享 Team」没有可用 Worker")
        self.assertNotIn("专属", attention["detail"])   # shared and dedicated environments both count (D28)
        self.assertEqual(attention["workers"], 0)
        self.assertIn("管理员", attention["action"])
        self.assertIn("个人空间重新提交", attention["action"])


if __name__ == "__main__":
    unittest.main()
