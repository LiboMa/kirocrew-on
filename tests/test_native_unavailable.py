"""A Gateway that is restarting is temporarily unavailable, not a refusal: Sessions wait and retry."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error

from octopus.db import Database, dumps
from octopus.engine import Engine
from octopus.security import Error
from octopus.worker import Worker, serve


def seed(db, rid, status, snapshot="{}"):
    now = time.time()
    with db.transaction() as c:
        c.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u','u','x',1,1,?)", (now,))
        c.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w','w','u',?)",
                  (dumps({"url": "http://127.0.0.1:1", "token": "t" * 40, "capabilities": {}}),))
        c.execute("INSERT INTO runs(id,owner_id,worker_id,snapshot,input,source,status,native_parent,created,updated) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, "u", "w", snapshot, "goal", dumps({"type": "new"}), status,
                                                  "p" if status == "diagnosing" else None, now, now))


class WorkerMappingTests(unittest.TestCase):
    def test_gateway_connection_failure_is_503_native_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            node = Worker(Path(temporary) / "worker", "x" * 40, crew_home=Path(temporary) / "crew")

            def down(path, body=None, method=None):
                raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

            node.native = down
            httpd = serve(node, 0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
                body = json.dumps({"run_id": "run-" + "1" * 32, "source": {"type": "new"}})
                connection.request("POST", "/prepare", body=body, headers={
                    "Authorization": "Bearer " + "x" * 40, "Content-Type": "application/json"})
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 503)
                self.assertEqual(result["code"], "native_unavailable")
            finally:
                httpd.shutdown()
                httpd.server_close()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_prepare_waits_while_gateway_is_unavailable(self):
        seed(self.db, "run-a", "preparing")

        def down(config, path, body=None, timeout=35):
            raise Error(503, "native_unavailable", "KiroCrew Gateway 暂不可用")

        Engine(self.db, request=down, retry_unavailable=2.0).tick()
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM runs WHERE id='run-a'").fetchone()[0], "preparing")

    def test_diagnosis_waits_while_gateway_is_unavailable(self):
        seed(self.db, "run-d", "diagnosing", dumps({"recovery": {"enabled": True, "max_cycles": 2}}))
        now = time.time()
        with self.db.transaction() as c:
            c.execute("INSERT INTO tasks(run_id,id,ordinal,spec,status,current_attempt) VALUES('run-d','code',0,'{}','blocked','a1')")
            c.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,created,updated) "
                      "VALUES('a1','run-d','code',1,'{}','blocked',?,?)", (now, now))
            c.execute("INSERT INTO recoveries(id,run_id,number,failed_task_id,failed_attempt_id,attempt_id,status,context,created,updated) "
                      "VALUES('r1','run-d',1,'code','a1',?,'pending',?,?,?)",
                      ("attempt-" + "9" * 32, dumps({"input": "goal"}), now, now))

        def down(config, path, body=None, timeout=35):
            raise Error(503, "native_unavailable", "KiroCrew Gateway 暂不可用")

        Engine(self.db, request=down, retry_unavailable=2.0).tick()
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM recoveries WHERE id='r1'").fetchone()[0], "pending")
            self.assertEqual(c.execute("SELECT status FROM runs WHERE id='run-d'").fetchone()[0], "diagnosing")


if __name__ == "__main__":
    unittest.main()
