"""The Engine runs alone (one leader) through a lease it is given (MVP 0.2.0, design §5.3 #7): today a file lock on the
platform host, later one a warm standby cannot take while the primary holds it (P5)."""
import fcntl
from pathlib import Path


class LeaseHeld(RuntimeError):
    """Another Engine holds the lease."""


class FileLease:
    """An exclusive, non-blocking flock on ``path``, held while the process lives (the lock ends with it)."""
    def __init__(self, path):
        self.path, self.handle = Path(path), None

    def acquire(self):
        handle = self.path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise LeaseHeld(f"{self.path} is held by another Engine") from None
        self.handle = handle

    def release(self):
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
