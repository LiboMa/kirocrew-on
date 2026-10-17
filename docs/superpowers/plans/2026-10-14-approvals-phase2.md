# 批准代理 第 2 期（D38，K04） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话的原生批准请求（父 slot 中要运行的工具、命令或文件，以及状态级批准）在控制台与 MCP 中可见并可答复，答复留有审计。

**Architecture:**
- **Worker**：`/approvals` 读本会话父 slot 的两类待批准项：`GET /api/chat/slots` 的 `pending_approval_info`，以及 `GET /api/approvals` 中 `slot` 相符的记录。`/approvals/answer` 先重新列出并核对，再按来源转交 Gateway。
- **平台**：
  - `GET /v1/runs/<id>/approvals`（read）；
  - `POST /v1/runs/<id>/approvals/answer`（execute；创建者、Team owner 或管理员）；
  - 审计 `approval.answer`，变更记录 `approval.answered`；会话为 `waiting_approval` 时 `attention` 写明。
- **控制台**：会话页「待批准」卡片。**MCP**：`octopus_approvals` 与 `octopus_approve`。Skill 写明调用 `octopus_approve` 之前须得到用户同意。

**Tech Stack:** Python 3 标准库、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-14-agent-fallback-approvals-design.md`（§4、§5 第 2 期、A3、A6）

## Global Constraints

- 待批准项的形状（Worker 与平台相同）：
  `{"origin": "native"|"coordinator", "request_id": str, "request_mid"?: str, "instance"?: str, "source"?: str, "tool": str, "tool_input": str, "tool_kind"?: str, "tool_purpose"?: str, "ts"?: float}`
  - `native` 必有 `request_mid`，`coordinator` 必有 `instance`。
  - `tool_input` 每项最多 4000 字符，`tool_purpose` 最多 1000 字符，超出以「…」截断。
- 答复的 `decision` 只能是 `approve` 或 `reject`（`reject_once` 与 `yolo` 不提供）。
- 答复前，Worker 重新列出本会话的待批准项。所答的项必须在其中，`origin`、`request_id` 与 `request_mid` / `instance` 都要一致，否则 404 `approval_not_pending`，文案「该批准请求已不在等待中（已被答复、过期或不属于此会话）」。
- Gateway 映射：
  - native：`POST /api/chat/slots/{slot}/approve`，body `{"origin":"native","request_id","request_mid","action":"approved"|"rejected"}`；
  - coordinator：`POST /api/approvals/{id}/{approve|reject}?origin=coordinator&slot=<slot>&instance=<instance>`。
- Gateway 的 HTTP 错误不原样透传给平台：
  - 404 映射为 409 `approval_not_pending`；
  - 其他映射为 502 `native_error`，并附 Gateway 的错误首行。
  - 原因是平台把 Worker 的 404 当作「旧 Worker」。
- 可以答复的人：会话创建者、Team owner、管理员（与发布相同，A3）；其他能看到会话的人 403 `forbidden`。
- 旧 Worker（`/approvals` 返回 404）：平台返回 `{"approvals": [], "supported": false}`；答复 409 `approvals_unsupported`。
- 审计 `approval.answer`：`{origin, request_id, tool, input (前 200 字符), decision}`。
- `octopus_approve` 的工具说明写明：调用前必须向用户展示工具、命令或文件与用途，并得到用户的明确同意（A6）。

## Review Focus

1. **他人会话的项**：答复一个属于另一会话 slot 的 `request_id`，即使 `request_id` 相同也被拒（`approval_not_pending`）。（`test_an_approval_of_another_session_is_refused`）
2. **过期 / 已答复**：列表中看到的项在答复前已被答复或过期，答复得到明确的 409，而不是 500 或误答了替换它的新请求（`request_mid` / `instance` 不同）。（`test_a_replaced_request_is_not_answered`）
3. **旧 Worker**：控制台与 MCP 不报错，只说明不支持。（`test_an_old_worker_lists_nothing_and_says_so`）
4. **只读成员**：Team 中能看到会话但不是 owner 的成员可以看到待批准项，但不能答复。（`test_who_may_answer`）
5. **大内容**：几百 KB 的 `tool_input` 在列表中被截断，控制台不卡死。（`test_long_inputs_are_clipped`）

---

### Task 1: Worker 的 `/approvals` 与 `/approvals/answer`

**Files:**
- Modify: `octopus/worker.py`（`approvals`、`approvals_answer`；路由表）
- Test: `tests/test_worker_approvals.py`（新建）

**Interfaces:**
- Produces:
  - `Worker.approvals(body) -> {"approvals": [item, ...]}`；
  - `Worker.approvals_answer(body) -> {"ok": True, "origin": ..., "request_id": ..., "decision": ...}`；
  - 路由 `POST /approvals`、`POST /approvals/answer`。

- [ ] **Step 1: 写失败的测试**

```python
"""D38 phase 2 (K04): a Session's pending native approvals, read from its own parent slot only, answered after being
checked again against what is still pending."""
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.security import Error
from octopus.worker import Worker

RID = "run-" + "1" * 32
SLOT = "octopus-" + RID
OTHER = "octopus-run-" + "9" * 32


class FakeGateway:
    def __init__(self):
        self.calls, self.slots, self.records = [], [], []

    def __call__(self, path, body=None, method=None):
        self.calls.append((path, body, method))
        if path == "/api/chat/slots/" + SLOT and body is None:
            raise gateway_client.GatewayError(404, '{"error":"not found"}')
        if path == "/api/chat/slots" and body is not None:
            return {"key": SLOT}
        if path == "/api/chat/slots" and body is None:
            return self.slots
        if path == "/api/approvals":
            return self.records
        return {"ok": True}


class WorkerApprovalsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.node = Worker(root / "worker", "x" * 40, crew_home=root / "crew")
        self.gateway = self.node.native = FakeGateway()
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.native = {"origin": "native", "tool": "shell", "tool_input": "rm -rf build", "tool_kind": "execute",
                       "request_id": "7", "request_mid": "mid-7", "tool_purpose": "清理构建目录"}
        self.record = {"id": "spawn:42", "instance": "inst-42", "source": "subagent", "tool": "fs_write",
                       "tool_input": "/w/a.txt", "tool_purpose": "写文件", "slot": SLOT, "ts": 5.0}

    def test_only_this_sessions_slot_is_listed(self):
        self.gateway.slots = [{"key": SLOT, "pending_approval": True, "pending_approval_info": self.native},
                              {"key": OTHER, "pending_approval": True, "pending_approval_info": {**self.native, "request_id": "8"}}]
        self.gateway.records = [self.record, {**self.record, "id": "spawn:43", "slot": OTHER}]
        items = self.node.approvals({"run_id": RID})["approvals"]
        self.assertEqual([(i["origin"], i["request_id"]) for i in items], [("native", "7"), ("coordinator", "spawn:42")])
        self.assertEqual(items[0]["request_mid"], "mid-7")
        self.assertEqual(items[1]["instance"], "inst-42")
        self.assertEqual(items[1]["source"], "subagent")

    def test_a_coordinator_card_on_the_slot_is_not_listed_twice(self):
        card = {"origin": "coordinator", "tool": "fs_write", "tool_input": "/w/a.txt", "request_id": "spawn:42"}
        self.gateway.slots = [{"key": SLOT, "pending_approval": True, "pending_approval_info": card}]
        self.gateway.records = [self.record]
        self.assertEqual(len(self.node.approvals({"run_id": RID})["approvals"]), 1)

    def test_long_inputs_are_clipped(self):
        self.gateway.slots = [{"key": SLOT, "pending_approval": True,
                               "pending_approval_info": {**self.native, "tool_input": "x" * 500_000}}]
        item = self.node.approvals({"run_id": RID})["approvals"][0]
        self.assertEqual(len(item["tool_input"]), 4001)
        self.assertTrue(item["tool_input"].endswith("…"))

    def test_a_native_answer_goes_to_the_slot_route(self):
        self.gateway.slots = [{"key": SLOT, "pending_approval": True, "pending_approval_info": self.native}]
        result = self.node.approvals_answer({"run_id": RID, "origin": "native", "request_id": "7", "request_mid": "mid-7",
                                             "decision": "approve"})
        self.assertEqual(result["decision"], "approve")
        self.assertIn(("/api/chat/slots/" + SLOT + "/approve",
                       {"origin": "native", "request_id": "7", "request_mid": "mid-7", "action": "approved"}, None),
                      self.gateway.calls)

    def test_a_coordinator_answer_names_its_slot_and_instance(self):
        self.gateway.records = [self.record]
        self.node.approvals_answer({"run_id": RID, "origin": "coordinator", "request_id": "spawn:42", "instance": "inst-42",
                                    "decision": "reject"})
        path = "/api/approvals/spawn%3A42/reject?origin=coordinator&slot=" + SLOT + "&instance=inst-42"
        self.assertIn((path, {}, "POST"), self.gateway.calls)

    def test_an_approval_of_another_session_is_refused(self):
        self.gateway.records = [{**self.record, "slot": OTHER}]
        with self.assertRaises(Error) as caught:
            self.node.approvals_answer({"run_id": RID, "origin": "coordinator", "request_id": "spawn:42",
                                        "instance": "inst-42", "decision": "approve"})
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "approval_not_pending"))

    def test_a_replaced_request_is_not_answered(self):
        self.gateway.slots = [{"key": SLOT, "pending_approval": True,
                               "pending_approval_info": {**self.native, "request_mid": "mid-new"}}]
        with self.assertRaises(Error) as caught:
            self.node.approvals_answer({"run_id": RID, "origin": "native", "request_id": "7", "request_mid": "mid-7",
                                        "decision": "approve"})
        self.assertEqual(caught.exception.code, "approval_not_pending")
        self.assertFalse([c for c in self.gateway.calls if c[0].endswith("/approve")])

    def test_a_gateway_refusal_is_not_a_404(self):
        self.gateway.slots = [{"key": SLOT, "pending_approval": True, "pending_approval_info": self.native}]
        answer = self.gateway.__call__
        def refuse(path, body=None, method=None):
            if path.endswith("/approve"):
                raise gateway_client.GatewayError(404, '{"error":"no pending approval"}')
            return answer(path, body, method)
        self.node.native = refuse
        with self.assertRaises(Error) as caught:
            self.node.approvals_answer({"run_id": RID, "origin": "native", "request_id": "7", "request_mid": "mid-7",
                                        "decision": "approve"})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "approval_not_pending"))

    def test_only_approve_or_reject(self):
        with self.assertRaises(Error):
            self.node.approvals_answer({"run_id": RID, "origin": "native", "request_id": "7", "request_mid": "mid-7",
                                        "decision": "yolo"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行，确认失败**：`$PY -m unittest tests.test_worker_approvals -v`。Expected: FAIL（`AttributeError: approvals`）。

- [ ] **Step 3: 实现**（`octopus/worker.py`，放在 `crew_entry` 之后）

```python
    APPROVAL_INPUT, APPROVAL_PURPOSE = 4000, 1000

    def _pending(self, slot):
        """The approvals pending on this slot (D38 §4.2): its native tool request and the state-level records it owns."""
        clip = lambda text, limit: (lambda s: s if len(s) <= limit else s[:limit] + "…")(str(text or ""))
        items = []
        for view in self.native("/api/chat/slots") or []:
            info = view.get("pending_approval_info") if isinstance(view, dict) and view.get("key") == slot else None
            if isinstance(info, dict) and info.get("origin") == "native" and info.get("request_mid"):
                items.append({"origin": "native", "request_id": str(info.get("request_id") or ""),
                              "request_mid": str(info["request_mid"]), "tool": clip(info.get("tool"), 200),
                              "tool_input": clip(info.get("tool_input"), self.APPROVAL_INPUT),
                              "tool_kind": clip(info.get("tool_kind"), 40),
                              "tool_purpose": clip(info.get("tool_purpose"), self.APPROVAL_PURPOSE)})
        for record in self.native("/api/approvals") or []:
            if isinstance(record, dict) and record.get("slot") == slot and record.get("instance"):
                items.append({"origin": "coordinator", "request_id": str(record.get("id") or ""),
                              "instance": str(record["instance"]), "source": clip(record.get("source"), 40),
                              "tool": clip(record.get("tool"), 200),
                              "tool_input": clip(record.get("tool_input"), self.APPROVAL_INPUT),
                              "tool_purpose": clip(record.get("tool_purpose"), self.APPROVAL_PURPOSE),
                              "ts": record.get("ts")})
        return items

    def approvals(self, body):
        _, parent = self.binding(body["run_id"])
        return {"approvals": self._pending(parent["slot"])}

    def approvals_answer(self, body):
        """Answer one approval of this Session (D38 §4.2): it must still be pending on the Session's own slot, with the
        same identity the caller was shown, so a replaced or another Session's request is never answered."""
        _, parent = self.binding(body["run_id"])
        slot, origin, decision = parent["slot"], body.get("origin"), body.get("decision")
        require(decision in {"approve", "reject"}, "decision 只能是 approve 或 reject")
        require(origin in {"native", "coordinator"}, "origin 只能是 native 或 coordinator")
        key = "request_mid" if origin == "native" else "instance"
        wanted = (origin, str(body.get("request_id") or ""), str(body.get(key) or ""))
        require(any((i["origin"], i["request_id"], i.get(key, "")) == wanted for i in self._pending(slot)),
                "该批准请求已不在等待中（已被答复、过期或不属于此会话）", 404, "approval_not_pending")
        try:
            if origin == "native":
                self.native("/api/chat/slots/" + quote(slot, safe="") + "/approve",
                            {"origin": "native", "request_id": wanted[1], "request_mid": wanted[2],
                             "action": "approved" if decision == "approve" else "rejected"})
            else:
                query = urlencode({"origin": "coordinator", "slot": slot, "instance": wanted[2]})
                self.native(f"/api/approvals/{quote(wanted[1], safe='')}/{decision}?{query}", {}, "POST")
        except crew_api.GatewayError as exc:   # the platform reads a Worker 404 as "not supported": never pass one on
            if exc.status == 404:
                raise Error(409, "approval_not_pending", "该批准请求已不在等待中（已被答复或过期）") from None
            raise Error(502, "native_error", "KiroCrew 未接受答复：" + str(exc).splitlines()[0][:200]) from None
        return {"ok": True, "origin": origin, "request_id": wanted[1], "decision": decision}
```

文件顶部的导入：`from urllib.parse import quote` 改为 `from urllib.parse import quote, urlencode`（以文件中实际的导入为准）。路由表加 `"/approvals": worker.approvals, "/approvals/answer": worker.approvals_answer`。

- [ ] **Step 4: 运行，确认通过**：`$PY -m unittest tests.test_worker_approvals tests.test_worker_approval tests.test_crew_entry -v`。Expected: PASS。
- [ ] **Step 5: Commit** `feat(worker): 会话的待批准项与答复（只读本会话父 slot；答复前重新核对；按来源转交 Gateway）（D38 §4.2）`

---

### Task 2: 平台的接口、权限、审计与提示

**Files:**
- Modify: `octopus/domain.py`（`Service.approvals`、`Service.answer_approval`；`_attention` 的 `waiting_approval`）
- Modify: `octopus/server.py`（路由 `/v1/runs/<id>/approvals`、`/v1/runs/<id>/approvals/answer`）
- Modify: `octopus/schema.py`（OpenAPI 两条路径与 `ApprovalAnswer`）
- Test: `tests/test_approvals.py`（新建）

**Interfaces:**
- Consumes: Worker `/approvals`、`/approvals/answer`（Task 1）。
- Produces:
  - `Service.approvals(actor, rid) -> {"approvals": [...], "supported": bool}`；
  - `Service.answer_approval(actor, rid, body) -> {"ok": True, ...}`；
  - 审计 `approval.answer`、变更记录 `approval.answered`。

- [ ] **Step 1: 写失败的测试**

```python
"""D38 phase 2: the platform lists a Session's pending approvals and passes an answer on, for the people who may
publish it (A3), with an audit record."""
import json
import tempfile
import unittest
from unittest.mock import patch

from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.security import Error, authenticate, create_user, issue_token

ITEM = {"origin": "native", "request_id": "7", "request_mid": "mid-7", "tool": "shell", "tool_input": "npm test",
        "tool_purpose": "运行测试"}


class ApprovalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            self.owner = create_user(db, "owner", "Fixture-password-123!")
            self.member = create_user(db, "member", "Fixture-password-123!")
            self.admin = create_user(db, "admin2", "Fixture-password-123!", True)
            self.actors = {name: authenticate(db, issue_token(db, user["id"])["token"])
                           for name, user in (("owner", self.owner), ("member", self.member), ("admin", self.admin))}
            db.execute("INSERT INTO teams(id,name,created) VALUES('team','T',1)")
            db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team',?,'owner'),('team',?,'member')",
                       (self.owner["id"], self.member["id"]))
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','W',?,?)",
                       (self.owner["id"], dumps({"url": "fixture", "token": "fixture", "capabilities": {}})))
        self.calls = []
        self.answers = {"/approvals": {"approvals": [ITEM]}, "/approvals/answer": {"ok": True}}

    def worker(self, config, path, body=None, **kwargs):
        self.calls.append((path, body))
        value = self.answers[path]
        if isinstance(value, Exception):
            raise value
        return value

    def session(self):
        with patch("octopus.transport.worker_request", side_effect=self.worker):
            return self.service.start(self.actors["owner"], {"request_id": "approvals-fixture-1", "input": "做一个功能",
                                      "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                      "source": {"type": "new"}, "worker_id": "worker", "team_id": "team"})["id"]

    def call(self, name, *args):
        with patch("octopus.transport.worker_request", side_effect=self.worker):
            return getattr(self.service, name)(*args)

    def test_the_pending_approvals_are_listed(self):
        rid = self.session()
        self.assertEqual(self.call("approvals", self.actors["member"], rid), {"approvals": [ITEM], "supported": True})
        self.assertEqual(self.calls[-1], ("/approvals", {"run_id": rid}))

    def test_an_old_worker_lists_nothing_and_says_so(self):
        rid = self.session()
        self.answers["/approvals"] = Error(404, "not_found", "接口不存在")
        self.assertEqual(self.call("approvals", self.actors["owner"], rid), {"approvals": [], "supported": False})
        self.answers["/approvals/answer"] = Error(404, "not_found", "接口不存在")
        with self.assertRaises(Error) as caught:
            self.call("answer_approval", self.actors["owner"], rid, {**{k: ITEM[k] for k in ("origin", "request_id", "request_mid")},
                                                                    "decision": "approve"})
        self.assertEqual(caught.exception.code, "approvals_unsupported")

    def test_who_may_answer(self):
        rid = self.session()
        body = {"origin": "native", "request_id": "7", "request_mid": "mid-7", "decision": "approve"}
        with self.assertRaises(Error) as caught:
            self.call("answer_approval", self.actors["member"], rid, body)
        self.assertEqual(caught.exception.code, "forbidden")
        for name in ("owner", "admin"):
            with self.subTest(name):
                self.assertTrue(self.call("answer_approval", self.actors[name], rid, body)["ok"])
        self.assertEqual(self.calls[-1], ("/approvals/answer", {"run_id": rid, **body}))

    def test_an_answer_is_audited_and_journaled(self):
        rid = self.session()
        self.call("answer_approval", self.actors["owner"], rid,
                  {"origin": "native", "request_id": "7", "request_mid": "mid-7", "decision": "reject"})
        with self.db.read() as db:
            audit = db.execute("SELECT * FROM audit WHERE action='approval.answer'").fetchone()
        detail = json.loads(audit["detail"])
        self.assertEqual((detail["decision"], detail["tool"], detail["input"]), ("reject", "shell", "npm test"))
        self.assertIn("approval.answered", [e["kind"] for e in self.service.events(self.actors["owner"], rid)])

    def test_a_request_no_longer_pending_is_a_clear_conflict(self):
        rid = self.session()
        self.answers["/approvals"] = {"approvals": []}
        with self.assertRaises(Error) as caught:
            self.call("answer_approval", self.actors["owner"], rid,
                      {"origin": "native", "request_id": "7", "request_mid": "mid-7", "decision": "approve"})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "approval_not_pending"))

    def test_waiting_for_approval_says_where_to_answer(self):
        rid = self.session()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='waiting_approval' WHERE id=?", (rid,))
        attention = self.service.run(self.actors["owner"], rid)["attention"]
        self.assertEqual(attention["title"], "等待批准")
        self.assertIn("待批准", attention["action"])
```

（`teams` / `members` / `audit` 表的列名以 `octopus/migrations.py` 为准；夹具插入失败时按实际列名调整，并记入台账。）

- [ ] **Step 2: 运行，确认失败**：`$PY -m unittest tests.test_approvals -v`。Expected: FAIL（`AttributeError: approvals`）。

- [ ] **Step 3: 实现**

`octopus/domain.py`（放在 `publish` 之前）：

```python
    def _may_publish(self, db, actor, run):
        """The creator, a Team owner or an admin (D35 §5.2): who may publish a Session and answer its approvals (D38 A3)."""
        team_owner = bool(run["team_id"]) and db.execute(
            "SELECT 1 FROM members WHERE team_id=? AND user_id=? AND role='owner'", (run["team_id"], actor["id"])).fetchone()
        return run["owner_id"] == actor["id"] or bool(team_owner) or elevated(actor)

    def approvals(self, actor, rid):
        """The Session's pending native approvals as its Worker reports them (D38 §4.2)."""
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            config, retired = self._app_worker(db, run["worker_id"])
        if not config or retired or not run["native_parent"]:
            return {"approvals": [], "supported": bool(config) and not retired}
        from . import transport
        try:
            return {"approvals": transport.worker_request(config, "/approvals", {"run_id": rid}, timeout=10)["approvals"],
                    "supported": True}
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":
                return {"approvals": [], "supported": False}
            raise

    def answer_approval(self, actor, rid, body):
        """Answer one pending approval (D38 §4.2): the people who may publish the Session, audited."""
        require(isinstance(body, dict) and set(body) <= {"origin", "request_id", "request_mid", "instance", "decision"},
                "只接受 origin、request_id、request_mid、instance 与 decision")
        require(body.get("decision") in {"approve", "reject"}, "decision 只能是 approve 或 reject")
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(self._may_publish(db, actor, run), "只有会话创建者、Team owner 或管理员可以答复批准请求", 403, "forbidden")
            config, retired = self._app_worker(db, run["worker_id"])
        require(config and not retired and run["native_parent"], "会话没有可用的执行环境", 409, "approvals_unsupported")
        from . import transport
        pending = self.approvals(actor, rid)["approvals"]
        key = "request_mid" if body.get("origin") == "native" else "instance"
        item = next((i for i in pending if (i["origin"], i["request_id"], i.get(key)) ==
                     (body.get("origin"), body.get("request_id"), body.get(key))), None)
        require(item is not None, "该批准请求已不在等待中（已被答复、过期或不属于此会话）", 409, "approval_not_pending")
        try:
            result = transport.worker_request(config, "/approvals/answer", {"run_id": rid, **body}, timeout=15)
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":
                raise Error(409, "approvals_unsupported", "执行环境的 Worker 尚未升级，暂不支持答复批准请求") from None
            raise
        detail = {"origin": item["origin"], "request_id": item["request_id"], "tool": item.get("tool"),
                  "input": str(item.get("tool_input") or "")[:200], "decision": body["decision"]}
        with self.db.transaction() as db:
            audit(db, actor, "approval.answer", "run", rid, detail)
            journal.change(db, "runs", rid, journal.person(actor), "approval.answered", event=detail, updated=time.time())
        return {**result, "ok": True}
```

`publish` 中原有的 Team owner 判断改为 `require(self._may_publish(db, actor, run), "只有会话创建者、Team owner 或管理员可以发布", 403, "forbidden")`。

`_attention`：`if run["status"] == "diagnosing":` 之前加：

```python
        if run["status"] == "waiting_approval":
            return {"title": "等待批准", "detail": run["error"] or "原生任务正在等待批准一次工具调用。",
                    "action": "在会话页「待批准」中查看工具与命令后批准或拒绝；Coding Agent 可用 octopus_approvals 查看、"
                              "经用户同意后用 octopus_approve 答复。", "task_id": task["id"] if task else None}
```


`octopus/server.py`，在 `publish` 路由之后：

```python
                if len(segments) in (4, 5) and segments[:2] == ["v1", "runs"] and segments[3] == "approvals":
                    if len(segments) == 4:
                        require(method == "GET", "Method not allowed", 405)
                        return self.respond(service.approvals(actor, segments[2]))
                    require(segments[4] == "answer" and method == "POST", "Method not allowed", 405)
                    return self.respond(service.answer_approval(actor, segments[2], body))
```

`required_scope` 不需要改：GET 为 read，`/v1/runs` 下的 POST 为 execute。`octopus/schema.py` 增加两条路径（GET 与 POST），以及 `ApprovalAnswer` 对象：`{origin: enum[native, coordinator], request_id, request_mid, instance, decision: enum[approve, reject]}`，必填 `origin`、`request_id`、`decision`。

- [ ] **Step 4: 运行，确认通过**：`$PY -m unittest tests.test_approvals tests.test_publish tests.test_access tests.test_static_console -v`。Expected: PASS。
- [ ] **Step 5: Commit** `feat(approvals): 平台列出与答复会话的待批准项（创建者、Team owner、管理员；审计与变更记录；旧 Worker 说明不支持）；等待批准时的提示（D38 §4.2）`

---

### Task 3: MCP `octopus_approvals`、`octopus_approve` 与 Skill

**Files:**
- Modify: `octopus/mcp.py`（两个工具与 `call` 分支）
- Modify: `skills/kirocrew-on/SKILL.md`（状态表 `waiting_approval` 一行与「批准」一节）
- Test: `tests/test_mcp_approvals.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
"""D38 phase 2: MCP lists a Session's pending approvals and answers one; the tool tells the agent to ask the user."""
import unittest

from octopus import mcp

RID = "run-" + "a" * 32


class Client:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return {"approvals": [], "supported": True} if method == "GET" else {"ok": True}


class McpApprovalsTests(unittest.TestCase):
    def test_both_tools_are_offered_and_the_answer_requires_the_users_consent(self):
        tools = {t["name"]: t for t in mcp.TOOLS}
        self.assertTrue(tools["octopus_approvals"]["annotations"]["readOnlyHint"])
        self.assertIn("用户", tools["octopus_approve"]["description"])
        self.assertIn("同意", tools["octopus_approve"]["description"])
        schema = tools["octopus_approve"]["inputSchema"]
        self.assertEqual(set(schema["required"]), {"run_id", "origin", "request_id", "decision"})

    def test_the_calls_reach_the_platform(self):
        client = Client()
        mcp.call(client, "octopus_approvals", {"run_id": RID})
        mcp.call(client, "octopus_approve", {"run_id": RID, "origin": "native", "request_id": "7", "request_mid": "m",
                                             "decision": "approve"})
        self.assertEqual(client.calls, [("GET", f"/v1/runs/{RID}/approvals", None),
                                        ("POST", f"/v1/runs/{RID}/approvals/answer",
                                         {"origin": "native", "request_id": "7", "request_mid": "m", "decision": "approve"})])

```

（MCP 只按 admin scope 过滤工具；只有 read scope 的 Token 调用 `octopus_approve` 时由平台 403。）

- [ ] **Step 2: 运行，确认失败**：`$PY -m unittest tests.test_mcp_approvals -v`。
- [ ] **Step 3: 实现**

`TOOLS` 中加：

```python
    {"name": "octopus_approvals",
     "description": "列出会话中等待批准的原生工具请求（工具、命令或文件、用途；origin 与标识用于答复）。会话为 waiting_approval，"
                    "或原生父会话想运行工具时用它。",
     "inputSchema": object_schema({"run_id": STRING}, ["run_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "octopus_approve",
     "description": "答复一个待批准请求（approve 或 reject）。调用前必须把工具、完整的命令或文件与用途展示给用户，并得到用户明确同意；"
                    "不要替用户决定。origin、request_id 与 request_mid / instance 取自 octopus_approvals。",
     "inputSchema": object_schema({"run_id": STRING, "origin": {"type": "string", "enum": ["native", "coordinator"]},
                                   "request_id": STRING, "request_mid": STRING, "instance": STRING,
                                   "decision": {"type": "string", "enum": ["approve", "reject"]}},
                                  ["run_id", "origin", "request_id", "decision"])},
```

`call` 中：

```python
    if name == "octopus_approvals":
        return client.request("GET", "/v1/runs/" + quote(args["run_id"], safe="") + "/approvals")
    if name == "octopus_approve":
        rid = args.pop("run_id")
        return client.request("POST", "/v1/runs/" + quote(rid, safe="") + "/approvals/answer", args)
```

Skill：
- 状态表加一行 `waiting_approval`：「原生任务在等待批准：用 `octopus_approvals` 列出，把工具、完整命令或文件与用途告诉用户；用户同意后用 `octopus_approve` 答复，不同意则 reject。」
- §4 之后加「批准」一小节，同样的规则，并说明控制台会话页的「待批准」卡片也可以答复。

- [ ] **Step 4: 运行，确认通过**：`$PY -m unittest tests.test_mcp_approvals tests.test_mcp_roles tests.test_mcp_status tests.test_connect -v`。
- [ ] **Step 5: Commit** `feat(mcp): octopus_approvals 与 octopus_approve（答复前须得到用户同意）；Skill 说明（D38 §4.3、A6）`

---

### Task 4: 控制台「待批准」卡片

**Files:**
- Modify: `web/src/api/types.ts`（`PendingApproval`、`ApprovalList`）
- Modify: `web/src/api/hooks.ts`（`useApprovals`）
- Create: `web/src/features/sessions/ApprovalsCard.tsx`
- Modify: `web/src/features/sessions/SessionView.tsx`（在 `RecoveryPanel` 之前显示卡片）
- Modify: `web/src/test/api.ts`（默认路由 `GET /runs/<id>/approvals` → `{approvals: [], supported: true}`）
- Test: `web/src/features/sessions/SessionView.test.tsx`

- [ ] **Step 1: 写失败的测试**

```tsx
  describe("pending approvals (D38)", () => {
    const item = { origin: "native", request_id: "7", request_mid: "mid-7", tool: "shell",
      tool_input: "npm test -- --watch=false " + "x".repeat(300), tool_purpose: "运行测试" };

    test("a pending approval shows what it would run and is approved after confirming", async () => {
      const run = { ...blocked, status: "waiting_approval" } as Run;
      const calls = open(run, { [`GET /runs/${run.id}/approvals`]: { approvals: [item], supported: true },
        [`POST /runs/${run.id}/approvals/answer`]: { ok: true } });
      const card = await screen.findByRole("region", { name: "待批准" });
      expect(within(card).getByText("shell")).toBeInTheDocument();
      expect(within(card).getByText("运行测试")).toBeInTheDocument();
      await userEvent.click(within(card).getByRole("button", { name: "批准" }));
      expect(within(card).getByText(item.tool_input)).toBeInTheDocument();   // the full input before confirming
      await userEvent.click(within(card).getByRole("button", { name: "确认批准" }));
      await waitFor(() => expect(calls.find((c) => c.path.endsWith("/approvals/answer"))?.body).toEqual(
        { origin: "native", request_id: "7", request_mid: "mid-7", decision: "approve" }));
    });

    test("nothing pending shows no card, and an old Worker says it cannot list them", async () => {
      open(blocked);
      await screen.findByText("模拟交接缺失", { selector: "#attention-detail" });
      expect(screen.queryByRole("region", { name: "待批准" })).not.toBeInTheDocument();
    });
  });
```

（若 `blocked` 的 Session 状态不轮询批准，测试中把状态设为 `waiting_approval` 或 `running`。旧 Worker 的提示只在 `waiting_approval` 时显示「此执行环境的 Worker 尚未升级，请在原生 KiroCrew 中处理批准」。）

- [ ] **Step 2: 运行，确认失败**：`npm --prefix web test -- --run src/features/sessions/SessionView.test.tsx`。
- [ ] **Step 3: 实现**

`types.ts`：

```ts
/** A native approval pending on the Session (D38 §4.2). */
export interface PendingApproval {
  origin: "native" | "coordinator";
  request_id: string;
  request_mid?: string;
  instance?: string;
  source?: string;
  tool: string;
  tool_input: string;
  tool_kind?: string;
  tool_purpose?: string;
  ts?: number;
}
export interface ApprovalList { approvals: PendingApproval[]; supported: boolean }
```

`hooks.ts`：

```ts
/** The Session's pending native approvals, every 10 s while it is open and not finished (D38). */
export function useApprovals(id: string, live: boolean) {
  return useQuery({ queryKey: ["approvals", id], enabled: live, retry: false, placeholderData: (previous) => previous,
    queryFn: () => api<ApprovalList>(`/runs/${encodeURIComponent(id)}/approvals`), ...every(10_000) });
}
```

`ApprovalsCard.tsx`：

```tsx
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useApprovals } from "@/api/hooks";
import type { PendingApproval, Run } from "@/api/types";
import { Button } from "@/components/ui/button";

const short = (text: string, n = 160) => (text.length > n ? text.slice(0, n) + "…" : text);

function Item({ run, item, canAnswer }: { run: Run; item: PendingApproval; canAnswer: boolean }) {
  const client = useQueryClient();
  const [confirm, setConfirm] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const answer = async (decision: "approve" | "reject") => {
    setError(null);
    try {
      await api(`/runs/${encodeURIComponent(run.id)}/approvals/answer`, "POST", {
        origin: item.origin, request_id: item.request_id, decision,
        ...(item.origin === "native" ? { request_mid: item.request_mid } : { instance: item.instance }) });
      setConfirm(null);
      await client.invalidateQueries({ queryKey: ["approvals", run.id] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <li className="rounded-lg border bg-background p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <strong>{item.tool}</strong>
        <span className="text-xs text-muted-foreground">{item.origin === "native" ? "会话内工具请求" : `状态级批准 · ${item.source ?? ""}`}</span>
      </div>
      {item.tool_purpose && <p className="mt-1">{item.tool_purpose}</p>}
      <pre className="mt-2 max-h-80 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">
        {confirm ? item.tool_input : short(item.tool_input)}
      </pre>
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      {canAnswer && (confirm ? (
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant={confirm === "approve" ? "default" : "destructive"} onClick={() => answer(confirm)}>
            {confirm === "approve" ? "确认批准" : "确认拒绝"}</Button>
          <Button size="sm" variant="outline" onClick={() => setConfirm(null)}>取消</Button>
        </div>
      ) : (
        <div className="mt-2 flex gap-2">
          <Button size="sm" onClick={() => setConfirm("approve")}>批准</Button>
          <Button size="sm" variant="outline" onClick={() => setConfirm("reject")}>拒绝</Button>
        </div>
      ))}
    </li>
  );
}

export function ApprovalsCard({ run, canAnswer }: { run: Run; canAnswer: boolean }) {
  const live = run.lifecycle === "open" && !["completed", "failed", "stopped"].includes(run.status);
  const { data } = useApprovals(run.id, live);
  if (!data) return null;
  if (!data.supported) {
    return run.status === "waiting_approval"
      ? <p className="text-xs text-muted-foreground">此执行环境的 Worker 尚未升级，请在原生 KiroCrew 中处理批准。</p> : null;
  }
  if (!data.approvals.length) return null;
  return (
    <section aria-label="待批准" className="rounded-xl border border-warning/50 bg-warning/5 px-4 py-3">
      <h2 className="text-sm font-semibold">待批准（{data.approvals.length}）</h2>
      {!canAnswer && <p className="mt-1 text-xs text-muted-foreground">只有会话创建者、Team owner 或管理员可以答复。</p>}
      <ul className="mt-2 space-y-2">
        {data.approvals.map((item) => <Item key={item.origin + item.request_id} run={run} item={item} canAnswer={canAnswer} />)}
      </ul>
    </section>
  );
}
```

`SessionView.tsx`：在 `<RecoveryPanel run={run} />` 之前加 `<ApprovalsCard run={run} canAnswer={<与「发布」按钮相同的条件>} />`（`PublishControl` 判断谁可以发布，复用同一个判断；没有现成判断时，用 `run.owner_id === me.id || me.admin` 再加 Team owner）。

- [ ] **Step 4: 运行，确认通过**：`npm --prefix web test -- --run`、`npm --prefix web run build`。
- [ ] **Step 5: Commit** `feat(console): 会话页「待批准」卡片（工具、命令与用途，确认后批准或拒绝）（D38 §4.3）`

---

### Task 5: 文档

- [ ] `docs/OPERATIONS.md`：「批准代理（D38 第 2 期）」一节，包括来源两类、谁可以答复、审计、旧 Worker 与 interactive 边界。
- [ ] `docs/MVP-RELEASE.md`：§14.2 第 7 项「批准代理」改为 ◐ 本机完成，§14.14 加第 2 期。§9 K04 写明处理方式。
- [ ] 全量测试：`$PY -m unittest discover -s tests -p 'test_*.py'`、`npm --prefix web test -- --run`。
- [ ] Commit `docs: D38 第 2 期批准代理——运维与进度`

## EC2 验收（最终审查与修复之后）

1. 部署平台与 w1。
2. 运行一个会话，在它的原生父 slot 中发一条消息，请父会话运行一条 shell 命令（K20 的情形；全局批准为 interactive）。
   - 期望：`GET /v1/runs/<id>/approvals` 出现 `native` 项，控制台显示「待批准」卡片；
   - 批准后命令被执行（父会话的回复中可见）；
   - 再触发一次并拒绝，原生侧收到拒绝。
3. 经 MCP：`octopus_approvals` 列出一项，`octopus_approve` 答复。
4. 审计 `GET /v1/admin/audit` 中有 `approval.answer`。
