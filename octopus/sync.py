"""Local / remote (D58 M3-4) on the platform: a local fast-forward brought in through the Session's Worker. The Worker
moves the branch only after its own checks (octopus/worker.py sync); here the result becomes the Session's candidate."""
from .security import Error
from .transport import worker_request, worker_upload

OUTDATED = "这个执行环境的 Worker 版本较旧，还不能同步本地提交。请平台管理员升级执行环境。"


def apply(config, rid, data, old, head):
    try:
        return worker_upload(config, "/sync", data, {"X-Octopus-Run": rid, "X-Octopus-Old": old, "X-Octopus-Head": head})
    except Error as exc:
        if exc.status == 404 and exc.code in ("not_found", "worker_error"):   # a Worker from before D58
            raise Error(409, "worker_outdated", OUTDATED) from None
        raise


def start_checks(config, rid, attempt_id, commands):
    """The synced candidate's checks, started as the engine starts any Attempt's (D37 §5); None from an older Worker."""
    try:
        return worker_request(config, "/verify/start", {"run_id": rid, "attempt_id": attempt_id, "commands": commands})["job"]
    except Error as exc:
        if exc.status == 404:
            return None
        raise
