"""Requests must name an allowed Host; tunnels and proxies are explicit configuration."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from octopus.db import Database
from octopus.server import server


class HostAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.httpd = server(Database(Path(self.temporary.name)), 0, allowed_hosts=("localhost:18918",))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temporary.cleanup()

    def call(self, method, path, host, body=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_loopback_with_actual_port_is_allowed(self):
        self.assertEqual(self.call("GET", "/health", f"127.0.0.1:{self.port}")[0], 200)
        self.assertEqual(self.call("GET", "/health", f"localhost:{self.port}")[0], 200)

    def test_unknown_host_is_rejected_for_api_and_static(self):
        for path in ("/health", "/", "/v1/openapi.json"):
            status, data = self.call("GET", path, "attacker.example")
            self.assertEqual(status, 421)
            self.assertEqual(json.loads(data)["code"], "host_not_allowed")

    def test_configured_tunnel_host_is_allowed(self):
        self.assertEqual(self.call("GET", "/health", "localhost:18918")[0], 200)

    def test_origin_mismatch_still_rejects_writes(self):
        status, data = self.call("POST", "/v1/login", "localhost:18918", {"username": "a", "password": "b" * 12},
                                 origin="http://attacker.example")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
