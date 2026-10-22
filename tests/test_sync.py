"""D58 M3-4 on the platform: a local fast-forward becomes the Session's new candidate — the last task that writes code gets
a 「本地同步」 Attempt whose checks run as any other's, the tasks after it run again (the review binds the new candidate),
and the revision history says so. Pull's bundle is a one-off link like a download's."""
import json
import unittest
from unittest import mock

from octopus import requests, sync
from octopus.db import dumps
from octopus.security import Error
from tests.test_access import AccessCase
from tests.test_confirmation import ConfirmationCase

CODE = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "deliver", "acceptance": ["完成"]}
REVIEW = {"id": "review", "name": "审查", "tool": "kiro", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]}
OLD, NEW = "a" * 40, "b" * 40
SYNCED = {"sha": NEW, "parent": OLD, "changed": True, "commits": 2, "files": 1, "insertions": 3, "deletions": 1,
          "names": ["src/a.js"], "secrets": []}


class SyncCase(ConfirmationCase):
    def setUp(self):
        super().setUp()
        self.checks = {}

    def request(self, config, path, body, **kwargs):
        if path == "/verify/status":
            return {"state": "done", "passed": True, "failed": None, "results": [{"command": "npm test", "exit": 0, "seconds": 1}]}
        return super().request(config, path, body, **kwargs)

    def finished(self, steps=(CODE, REVIEW)):
        rid = self.start(steps=[dict(s) for s in steps])
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        return rid, run

    def sync(self, rid, request="sync-fixture-0001", version=None, answer=SYNCED, **extra):
        version = version if version is not None else self.service.run(self.actor, rid)["version"]
        with mock.patch.object(sync, "worker_upload", return_value=answer) as upload, \
                mock.patch.object(sync, "worker_request", return_value={"job": "job-1"}) as checks:
            result = self.service.sync(self.actor, rid, b"bundle", NEW, request, version, extra.get("note"), old=OLD)
        self.upload, self.checks_started = upload, checks
        return result


class SyncTests(SyncCase):
    def test_a_sync_is_a_new_candidate_and_the_review_runs_again(self):
        rid, before = self.finished()
        reviews = len(self.dispatched("review"))
        result = self.sync(rid, note="本地修了样式")
        self.assertEqual((result["task_id"], result["head"], result["commits"]), ("code", NEW, 2))
        config, path, data, headers = self.upload.call_args.args
        self.assertEqual((path, data, headers["X-Octopus-Old"], headers["X-Octopus-Head"]), ("/sync", b"bundle", OLD, NEW))
        run = self.service.run(self.actor, rid)
        code, review = run["tasks"]
        latest = code["attempts"][-1]
        self.assertEqual((latest["id"], latest["status"]), (result["attempt_id"], "accepted"))
        self.assertEqual(latest["evidence"]["candidate"]["sha"], NEW)
        self.assertEqual((latest["evidence"]["synced"]["commits"], latest["evidence"]["synced"]["note"]), (2, "本地修了样式"))
        self.assertEqual(review["status"], "pending")
        self.assertEqual(run["version"], before["version"] + 1)
        with self.db.read() as db:
            content = json.loads(db.execute("SELECT content FROM revisions WHERE run_id=? ORDER BY version DESC LIMIT 1", (rid,)).fetchone()[0])
        self.assertEqual(content["kind"], "synced")
        self.assertIn("2 个本地提交", content["summary"])
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.dispatched("review")), reviews + 1)   # the review ran again, on the new candidate
        self.assertEqual(len(self.dispatched("code")), 1)   # the code task's Agent did not

    def test_with_check_commands_the_synced_candidate_is_checked(self):
        rid, _ = self.finished((dict(CODE, verify=["npm test"]), REVIEW))
        result = self.sync(rid)
        self.assertEqual(self.checks_started.call_args.args[1:], ("/verify/start", {"run_id": rid, "attempt_id": result["attempt_id"],
                                                                                    "commands": ["npm test"]}))
        attempt = self.service.run(self.actor, rid)["tasks"][0]["attempts"][-1]
        self.assertEqual((attempt["status"], attempt["evidence"]["checks"]["job"]), ("running", "job-1"))
        run = self.tick(rid)
        attempt = run["tasks"][0]["attempts"][-1]
        self.assertEqual(attempt["status"], "accepted")
        self.assertIn("checks_pass", [g["name"] for g in attempt["evidence"]["gates"] if g["status"] == "pass"])
        self.assertEqual(run["status"], "completed")

    def test_the_same_request_is_one_sync(self):
        rid, _ = self.finished()
        first = self.sync(rid)
        again = self.sync(rid, version=first["version"] - 1)   # a retry of the same request answers the same
        self.assertEqual(again, first)
        self.upload.assert_not_called()
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts WHERE run_id=? AND task_id='code'", (rid,)).fetchone()[0], 2)

    def test_refusals(self):
        rid, _ = self.finished()
        with self.assertRaises(Error) as caught:
            self.sync(rid, version=999)
        self.assertEqual(caught.exception.code, "version_conflict")
        busy = self.start(steps=[dict(CODE)])
        with self.assertRaises(Error) as caught:
            self.sync(busy, request="sync-fixture-0002")
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "busy"))
        with self.assertRaises(Error) as caught:
            self.service.sync(self.other, rid, b"bundle", NEW, "sync-fixture-0003", 9, None, old=OLD)
        self.assertEqual(caught.exception.status, 404)
        old = Error(404, "not_found", "Not found")
        with mock.patch.object(sync, "worker_upload", side_effect=old), self.assertRaises(Error) as caught:
            self.service.sync(self.actor, rid, b"bundle", NEW, "sync-fixture-0004", self.service.run(self.actor, rid)["version"], None, old=OLD)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "worker_outdated"))
        refused = Error(409, "sync_not_fast_forward", "会话分支已有新的提交")
        with mock.patch.object(sync, "worker_upload", side_effect=refused), self.assertRaises(Error) as caught:
            self.service.sync(self.actor, rid, b"bundle", NEW, "sync-fixture-0005", self.service.run(self.actor, rid)["version"], None, old=OLD)
        self.assertEqual(caught.exception.code, "sync_not_fast_forward")
        self.assertEqual(self.service.run(self.actor, rid)["tasks"][1]["status"], "accepted")   # nothing recorded

    def test_the_cli_is_a_client(self):
        self.assertEqual(requests.client_of("octopus CLI"), "cli")


class SyncRouteTests(AccessCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('w-bob','bob',?,?,'dedicated')",
                       (self.b["id"], dumps({"url": "https://worker.test", "token": "t" * 40, "capabilities": {}})))
        self.rid = self.start(self.b, worker_id="w-bob")["id"]
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed', workspace='/w' WHERE id=?", (self.rid,))
            db.execute("UPDATE tasks SET status='accepted' WHERE run_id=?", (self.rid,))

    def test_the_sync_route_takes_the_bundle_as_it_is(self):
        version = self.service.run(self.b, self.rid)["version"]
        with mock.patch.object(sync, "worker_upload", return_value=SYNCED) as upload, \
                mock.patch.object(sync, "worker_request", return_value={"job": "j"}), self.http() as call:
            status, body = call("POST", f"/v1/runs/{self.rid}/sync?request_id=sync-route-0001&expected_version={version}"
                                        f"&head={NEW}&old={OLD}", self.tokens["bob"], raw=b"\\x00bundle-bytes",
                                headers={"Content-Type": "application/octet-stream"})
        self.assertEqual((status, body["head"]), (200, NEW))
        self.assertEqual(upload.call_args.args[2], b"\\x00bundle-bytes")

    def test_a_bundle_link_is_for_its_holder(self):
        with self.http() as call:
            status, link = call("POST", f"/v1/runs/{self.rid}/bundle", self.tokens["bob"], {})
            self.assertEqual(status, 200)
            self.assertTrue(link["url"].startswith("/bundle/") and link["url"].endswith(f"/{self.rid}.bundle"))
            self.assertEqual(call("POST", f"/v1/runs/{self.rid}/bundle", self.tokens["eve"], {})[0], 404)


if __name__ == "__main__":
    unittest.main()
