"""The platform reaches remote Worker hosts over TLS and trusts only its own private CA (D28, Worker hosts)."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest

from octopus import transport
from octopus.security import Error

TOKEN = "t" * 40


def openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def authority(root, name):
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.pem",
            "-days", "2", "-subj", f"/CN={name}", cwd=root)
    return root / f"{name}.pem"


def certificate(root, ca, name, address):
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.csr", "-subj", "/CN=worker",
            cwd=root)
    (root / f"{name}.ext").write_text(f"subjectAltName=IP:{address}\n")
    openssl("x509", "-req", "-in", f"{name}.csr", "-CA", f"{ca}.pem", "-CAkey", f"{ca}.key", "-CAcreateserial",
            "-out", f"{name}.pem", "-days", "2", "-extfile", f"{name}.ext", cwd=root)
    return root / f"{name}.pem", root / f"{name}.key"


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("Authorization") == "Bearer " + TOKEN
        data = json.dumps({"isolation": "dedicated"} if ok else {"error": "unauthorized"}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class WorkerTlsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ca = authority(self.root, "platform-ca")
        self.other = authority(self.root, "other-ca")
        self.servers = []

    def tearDown(self):
        transport.configure(None)
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        self.temporary.cleanup()

    def serve(self, cert, key):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        httpd = HTTPServer(("127.0.0.1", 0), Health)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.servers.append(httpd)
        return {"url": f"https://127.0.0.1:{httpd.server_address[1]}", "token": TOKEN}

    def test_worker_signed_by_the_platform_ca_is_trusted(self):
        config = self.serve(*certificate(self.root, "platform-ca", "worker", "127.0.0.1"))
        transport.configure(self.ca)
        self.assertEqual(transport.worker_request(config, "/health"), {"isolation": "dedicated"})

    def test_untrusted_or_mismatched_certificate_is_refused(self):
        trusted_but_elsewhere = self.serve(*certificate(self.root, "platform-ca", "elsewhere", "10.0.0.9"))
        other_ca = self.serve(*certificate(self.root, "other-ca", "stranger", "127.0.0.1"))
        for ca, config in ((self.ca, trusted_but_elsewhere), (self.ca, other_ca), (None, other_ca)):
            transport.configure(ca)
            with self.assertRaises(Error) as caught:
                transport.worker_request(config, "/health", timeout=5)
            self.assertEqual((caught.exception.status, caught.exception.code), (503, "worker_unavailable"))
            self.assertIn("证书", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
