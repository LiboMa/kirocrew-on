"""Behind CloudFront: only requests carrying the origin secret are served, and login limits use the viewer address."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from octopus.db import Database
from octopus.server import client_ip, server

SECRET = "s" * 40


class FakeHandler:
    def __init__(self, peer, headers):
        self.client_address = (peer, 50000)
        self.headers = headers


class ClientAddressTests(unittest.TestCase):
    def test_viewer_address_is_trusted_only_with_a_verified_origin(self):
        headers = {"CloudFront-Viewer-Address": "203.0.113.9:51234"}
        self.assertEqual(client_ip(FakeHandler("130.176.1.1", headers), verified=True), "203.0.113.9")
        self.assertEqual(client_ip(FakeHandler("130.176.1.1", headers), verified=False), "130.176.1.1")
        self.assertEqual(client_ip(FakeHandler("130.176.1.1", {"CloudFront-Viewer-Address": "2001:db8::7:443"}), verified=True),
                         "2001:db8::7")


class OriginSecretTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.httpd = server(Database(Path(self.temporary.name)), 0, host="127.0.0.1",
                            allowed_hosts=("d111.cloudfront.net",), origin_secret=SECRET)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temporary.cleanup()

    def call(self, headers, method="GET", path="/health", body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_cloudfront_host_requires_the_origin_secret(self):
        status, data = self.call({"Host": "d111.cloudfront.net"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(data)["code"], "origin_unverified")
        self.assertEqual(self.call({"Host": "d111.cloudfront.net", "X-Origin-Verify": "wrong"})[0], 403)
        self.assertEqual(self.call({"Host": "d111.cloudfront.net", "X-Origin-Verify": SECRET})[0], 200)

    def test_loopback_host_for_local_operations_needs_no_secret(self):
        self.assertEqual(self.call({"Host": f"127.0.0.1:{self.port}"})[0], 200)

    def test_login_attempts_are_counted_per_viewer(self):
        base = {"Host": "d111.cloudfront.net", "X-Origin-Verify": SECRET, "Content-Type": "application/json",
                "Origin": "https://d111.cloudfront.net"}
        for _ in range(15):
            self.call({**base, "CloudFront-Viewer-Address": "203.0.113.9:1000"}, "POST", "/v1/login",
                      {"username": "x", "password": "y" * 12})
        limited = self.call({**base, "CloudFront-Viewer-Address": "203.0.113.9:1000"}, "POST", "/v1/login",
                            {"username": "x", "password": "y" * 12})[0]
        other = self.call({**base, "CloudFront-Viewer-Address": "198.51.100.4:1000"}, "POST", "/v1/login",
                          {"username": "x", "password": "y" * 12})[0]
        self.assertEqual(limited, 429)
        self.assertEqual(other, 401)


class WaitReadyAddressTests(unittest.TestCase):
    def test_wildcard_api_host_is_probed_on_loopback(self):
        from octopus.__main__ import probe_host
        self.assertEqual(probe_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(probe_host("::"), "127.0.0.1")
        self.assertEqual(probe_host("10.0.0.5"), "10.0.0.5")


if __name__ == "__main__":
    unittest.main()
