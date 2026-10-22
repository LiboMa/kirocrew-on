"""What a candidate changed (D56 M3-5), read through the Session's Worker: the platform keeps the candidate's id and
counts (D37), the code stays in the execution environment."""
from .security import Error
from .transport import worker_request

OUTDATED = "这个执行环境的 Worker 版本较旧，还不能显示代码改动。请平台管理员升级执行环境。"


def read(config, rid, attempt_id, candidate):
    body = {"run_id": rid, "attempt_id": attempt_id, "mode": "diff", "expected": candidate["sha"],
            **({"parent": candidate["parent"]} if candidate.get("parent") else {})}
    try:
        return worker_request(config, "/candidate", body, timeout=60)
    except Error as exc:
        if exc.status == 400 and "mode" in str(exc):   # a Worker from before D56 knows commit / check / restore only
            raise Error(409, "worker_outdated", OUTDATED) from None
        raise
