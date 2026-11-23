"""Review 2026-10-26: what the platform asks of a Worker it cannot be steered elsewhere by — no redirect is followed (it
would carry the Worker token to another address), a health answer is bounded, a malformed URL is a 400."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from octopus import transport
from octopus.security import Error


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class TransportTests(unittest.TestCase):
    def test_a_redirect_is_not_followed(self):
        hits = []

        class Elsewhere(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass
        target = serve(Elsewhere)

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/health")
                self.end_headers()

            def log_message(self, *args):
                pass
        source = serve(Redirect)
        self.addCleanup(lambda: [s.shutdown() for s in (source, target)])
        with self.assertRaises(Error):
            transport.worker_request({"url": f"http://127.0.0.1:{source.server_address[1]}", "token": "t" * 40}, "/health")
        self.assertEqual(hits, [])

    def test_an_oversized_health_answer_is_refused(self):
        class Huge(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"pad": "x" * (2 << 20)}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        httpd = serve(Huge)
        self.addCleanup(httpd.shutdown)
        with self.assertRaises(Error) as caught:
            transport.validate_worker({"url": f"http://127.0.0.1:{httpd.server_address[1]}", "token": "t" * 40})
        self.assertEqual(caught.exception.code, "worker_unavailable")

    def test_a_url_that_is_not_text_is_a_400(self):
        for url in (42, None, ["https://x"]):
            with self.subTest(url=url), self.assertRaises(Error) as caught:
                transport.validate_worker({"url": url, "token": "t" * 40})
            self.assertEqual(caught.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
