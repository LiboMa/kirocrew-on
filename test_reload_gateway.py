"""Reload guard never interrupts busy work or kills another listener."""
import unittest
from unittest.mock import patch

import reload_gateway as r


class ReloadGuardTests(unittest.TestCase):
    def responses(self, active=False, queued=0, approvals=None):
        return {
            "/api/chat/slots": [{"key": "existing-user-session", "running": active}],
            "/api/spawn": {"agents": []},
            "/api/tasks/summary": {"available": True, "depth": {"queued": queued}},
            "/api/approvals": approvals or [],
        }

    def test_busy_chat_queue_or_approval_prevents_restart(self):
        for values in (self.responses(active=True), self.responses(queued=1),
                       self.responses(approvals=[{"id": "pending"}])):
            with self.subTest(values=values), patch.object(r.api, "request", side_effect=values.__getitem__), \
                    patch.object(r.os, "kill") as kill, patch.object(r.subprocess, "Popen") as start:
                with self.assertRaisesRegex(RuntimeError, "仍有任务"):
                    r.reload_when_idle()
                kill.assert_not_called()
                start.assert_not_called()

    def test_foreign_listener_is_never_terminated(self):
        values = self.responses()
        with patch.object(r.api, "request", side_effect=values.__getitem__), \
                patch.object(r, "read_json", return_value={"pid": 123}), \
                patch.object(r.subprocess, "check_output", side_effect=["456", "python3 unrelated.py"]), \
                patch.object(r.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "不是该 PoC"):
                r.reload_when_idle()
            kill.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
