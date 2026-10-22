"""The remote preview's built-in static server (D36 §5.1): files under one folder only — never a dot-file or folder
(.env, .git) and never a path that leaves the folder through a symlink; no directory listings.

Run as a script, ``python -I static_site.py PORT FOLDER``: it imports nothing of the platform, so it runs in the
environment the app gets (without the platform's PYTHONPATH)."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import sys
from urllib.parse import unquote, urlsplit


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        parts = [part for part in unquote(urlsplit(self.path).path).split("/") if part]
        real, root = os.path.realpath(self.translate_path(self.path)), os.path.realpath(self.directory)
        if any(part.startswith(".") for part in parts) or (real != root and not real.startswith(root + os.sep)):
            self.send_error(404)
            return None
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404)
        return None


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), partial(Handler, directory=sys.argv[2])).serve_forever()
