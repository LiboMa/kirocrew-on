"""D37 §4: the first five gates are today's checks, named and recorded. D57 §4: a missing handoff and an invalid outcome
are warnings; the other verdicts do not change."""
import unittest

from octopus import gates
from octopus.engine import BLOCKED

SPEC = {"tool": "claude", "model": "auto", "effort": ""}
DONE = {"done": True, "result": "完成", "error": ""}


def judge(native=DONE, handoff="交接", evidence=None, report=None, invalid=None, lost=False, missing=False, blocked=False):
    return gates.basic(native, {"handoff": handoff}, SPEC, {"actual_backend": "claude", **(evidence or {})}, report,
                       invalid, lost, missing, blocked)


class BasicGateTests(unittest.TestCase):
    def names(self, records):
        return [(r["name"], r["status"]) for r in records]

    def test_all_pass(self):
        error, failure, records = judge(report={"decision": "pass", "summary": "ok", "failure_type": "none"})
        self.assertEqual((error, failure), (None, None))
        self.assertEqual(self.names(records), [("native_completed", "pass"), ("identity_match", "pass"),
                                               ("handoff_present", "pass"), ("outcome_valid", "pass"), ("outcome_pass", "pass")])

    def test_an_unverified_identity_waits_but_a_mismatch_is_the_tools(self):
        for evidence, failure in (({"actual_backend": None}, "model"), ({"actual_backend": "codex"}, "tool")):
            with self.subTest(evidence=evidence):
                self.assertEqual(judge(evidence=evidence)[1], failure)
        explicit = {**SPEC, "model": "sonnet"}
        for actual, failure in (("", "model"), ("opus", "tool")):
            with self.subTest(actual=actual):
                result = gates.basic(DONE, {"handoff": "交接"}, explicit, {"actual_backend": "claude", "actual_model": actual},
                                     None, None, False, False, False)
                self.assertEqual(result[1], failure)

    def test_each_failure_keeps_todays_message_and_type(self):
        cases = [
            (dict(native={**DONE, "error": "boom"}), ("boom", "native_error", "native_completed")),
            (dict(native={**DONE, "stopped": True}), ("原生任务已停止", "native_stopped", "native_completed")),
            (dict(missing=True), (BLOCKED, "native_error", "native_completed")),
            (dict(evidence={"actual_backend": "codex"}), ("实际 ACP 后端未核实或与配置不匹配", "tool", "identity_match")),
            (dict(report={"decision": "changes_requested", "summary": "缺测试", "failure_type": "tests"}),
             ("缺测试", "tests", "outcome_pass")),
        ]
        for kwargs, (message, failure, gate) in cases:
            with self.subTest(gate=gate, message=message):
                error, kind, records = judge(**kwargs)
                self.assertEqual((error, kind), (message, failure))
                self.assertEqual(records[-1]["name"], gate)
                self.assertEqual(records[-1]["status"], "fail")

    def test_a_missing_handoff_is_a_warning_and_the_next_gates_still_judge(self):   # D57 §4
        passing = {"decision": "pass", "summary": "ok", "failure_type": "none"}
        error, failure, records = judge(handoff="", report=passing)
        self.assertEqual((error, failure), (None, None))
        self.assertEqual(self.names(records), [("native_completed", "pass"), ("identity_match", "pass"),
                                               ("handoff_present", "warn"), ("outcome_valid", "pass"), ("outcome_pass", "pass")])
        self.assertEqual(records[2]["detail"], "执行者没有写交接文件，平台已用原生结果生成一份")
        later = ((dict(evidence={"actual_backend": "codex"}), ("实际 ACP 后端未核实或与配置不匹配", "identity_match")),
                 (dict(report={"decision": "changes_requested", "summary": "缺测试", "failure_type": "tests"}),
                  ("缺测试", "outcome_pass")))
        for kwargs, (message, gate) in later:   # the gates after it still fail as before
            with self.subTest(gate=gate):
                error, _, records = judge(handoff="", **kwargs)
                self.assertEqual((error, records[-1]["name"], records[-1]["status"]), (message, gate, "fail"))
                self.assertIn(("handoff_present", "warn"), self.names(records))

    def test_an_invalid_outcome_is_a_warning_judged_by_the_result_text(self):   # D57 §4
        error, failure, records = judge(invalid="outcome.json 无效")
        self.assertEqual((error, failure), (None, None))
        self.assertEqual(self.names(records)[-2:], [("outcome_valid", "warn"), ("outcome_pass", "skip")])
        self.assertIn("outcome.json 无效", records[-2]["detail"])
        self.assertIn("按结果文字判断", records[-2]["detail"])
        # a standalone BLOCKED line in the result still fails as BLOCKED
        error, failure, records = judge(invalid="outcome.json 无效", blocked=True)
        self.assertEqual((error, records[-1]["status"]), (BLOCKED, "fail"))
        self.assertNotIn("pass", [r["status"] for r in records if r["name"].startswith("outcome")])

    def test_a_blocked_reply_with_a_failing_outcome_reports_the_outcome(self):
        report = {"decision": "blocked", "summary": "缺少数据库地址", "failure_type": "input"}
        error, failure, records = judge(missing=True, report=report)
        self.assertEqual((error, failure), ("缺少数据库地址", "input"))
        self.assertEqual((records[-1]["name"], records[-1]["status"]), ("native_completed", "fail"))

    def test_an_expired_login_is_an_auth_failure(self):
        error, failure, _ = judge(native={**DONE, "error": "Session has expired, please log in"})
        self.assertEqual(failure, "auth")

    def test_no_outcome_before_contract_2_skips_the_outcome_gates(self):
        error, failure, records = judge(report=None)
        self.assertIsNone(error)
        self.assertEqual(self.names(records)[-2:], [("outcome_valid", "skip"), ("outcome_pass", "skip")])


class GateRecordTests(unittest.TestCase):
    def test_warn_is_a_recorded_status(self):   # D57 §4: recorded and shown, never blocking
        warned = gates.record("x", "warn", "d")
        self.assertEqual((warned["status"], warned["detail"]), ("warn", "d"))
        self.assertEqual(gates.warnings([gates.record("a", "pass"), warned, gates.record("b", "fail", "e")]), [warned])
        with self.assertRaises(ValueError):
            gates.record("x", "maybe")


class CoveredTests(unittest.TestCase):
    def test_paths_match_directories_globs_and_exact_files(self):
        for path, globs, expected in (("src/a/b.py", ["src/"], True), ("src/a/b.py", ["src/**"], True),
                                      ("src/a/b.py", ["src/*.py"], True), ("src/a/b.py", ["src"], True),
                                      ("./README.md", ["README.md"], True), ("README.md", ["./README.md"], True),
                                      ("docs/a.md", ["src/"], False), ("srcx/a.py", ["src"], False),
                                      # the globstar forms planners write
                                      ("src/index.ts", ["src/**/*.ts"], True), ("src/a/b/c.ts", ["src/**/*.ts"], True),
                                      ("tests/test_a.py", ["tests/**/*.py"], True), ("setup.py", ["**/*.py"], True),
                                      ("src/a.py", ["src/**/*"], True), ("src/a.js", ["src/**/*.ts"], False),
                                      ("a/b.py", ["."], True), ("a/b.py", ["./"], True), ("a/b.py", ["**"], True),
                                      ("src/a.py", ["/src/"], True)):
            with self.subTest(path=path, globs=globs):
                self.assertIs(gates.covered(path, globs), expected)
