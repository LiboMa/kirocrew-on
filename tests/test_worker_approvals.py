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
