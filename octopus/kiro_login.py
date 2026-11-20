"""Signing a boundary's kiro-cli in from the console (MVP 0.2.0 P2, design §6.4). The Worker runs as the boundary's OS user,
so it can run `kiro-cli login --use-device-flow` itself — in a terminal of its own, since kiro-cli prompts and fails
without one (as `deploy.sh kiro-login` found on EC2) — and read the address and code it shows. They are kept in memory
only, for the boundary's owner to see; one login runs at a time, and it ends when kiro-cli says so, exits, or the code
expires."""
import codecs
import os
import pty
import re
import select
import signal
import subprocess
import threading
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
CODE = re.compile(r"\bCode:\s*([A-Z0-9]{4}-[A-Z0-9]{4})\b")
# The device address is the first whole line's address after the code. kiro-cli 2.29.1 with IAM Identity Center first
# echoes the Start URL it was given ("Enter Start URL › https://…/start", EC2 2026-11): that fixed portal address is not
# where a code is entered. kiro-cli 3.x shows a one-time address of some 900 characters that can arrive in more than one
# read, so only an address its line has ended counts.
LINK = re.compile(r"https://[^\s\x1b]+(?=[ \t]*\n)")
DONE = "Logged in successfully"
LIFETIME = 900          # seconds a device code lasts
PROMPTS = (3.0, 5.0)    # kiro-cli still asks with every flag given: Enter accepts each remaining prompt
KEEP = 65536            # characters of output kept to find the code in


class DeviceLogin:
    def __init__(self, command, env=None, on_done=None):
        self.command, self.env, self.on_done = command, env, on_done
        self.lock = threading.Lock()
        self.proc, self.state, self.url, self.code, self.expires = None, "idle", None, None, None

    def view(self):
        with self.lock:
            if self.state == "waiting":
                return {"state": "waiting", "url": self.url, "code": self.code, "expires": self.expires}
            return {"state": self.state}

    def start(self, wait=10.0):
        """The login in progress, or a new one; waits up to ``wait`` seconds for kiro-cli to show its code."""
        with self.lock:
            running = self.proc is not None and self.proc.poll() is None
            if not running:
                master, slave = pty.openpty()
                try:
                    self.proc = subprocess.Popen(self.command, stdin=slave, stdout=slave, stderr=slave, env=self.env,
                                                 start_new_session=True, close_fds=True)
                finally:
                    os.close(slave)
                self.state, self.url, self.code, self.expires = "starting", None, None, time.time() + LIFETIME
                threading.Thread(target=self._follow, args=(self.proc, master), daemon=True, name="kiro-device-sign-in").start()
        end = time.time() + wait
        while time.time() < end and self.view()["state"] == "starting":
            time.sleep(0.05)
        return self.view()

    def stop(self):
        with self.lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def _follow(self, proc, master):
        started, raw, text, pressed = time.time(), "", "", 0
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")   # a character may span two reads
        try:
            while True:
                now = time.time()
                if pressed < len(PROMPTS) and now - started >= PROMPTS[pressed]:
                    pressed += 1
                    try:
                        os.write(master, b"\r")
                    except OSError:
                        pass
                ready, _, _ = select.select([master], [], [], 0.2)
                if ready:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:   # the terminal closed: kiro-cli ended
                        chunk = b""
                    if not chunk:
                        break
                    # escapes are taken out of the whole output: one can span two reads as well
                    raw = (raw + decoder.decode(chunk))[-KEEP:]
                    text = ANSI.sub("", raw).replace("\r", "")
                    self._read(text)
                    if DONE in text:
                        break
                elif proc.poll() is not None:
                    break
                if now > self.expires:
                    break
        finally:
            os.close(master)
            finished = DONE in text
            if proc.poll() is None:   # kiro-cli can stay after it signed in (EC2 w1); it ends here either way
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
            try:
                ok = finished or proc.wait(timeout=10) == 0
            except subprocess.TimeoutExpired:
                ok = False
            with self.lock:
                self.state, self.url, self.code = ("done" if ok else "failed"), None, None
            if ok and self.on_done:
                self.on_done()

    def _read(self, text):
        code = CODE.search(text)
        url = code and LINK.search(text, code.end())
        if url:
            with self.lock:
                if self.state == "starting":
                    self.url, self.code, self.state = url.group(0), code.group(1), "waiting"
