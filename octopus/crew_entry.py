"""On-demand native browser entry, bound to the run's authorized Worker."""
import json
from urllib.parse import parse_qs, urlsplit

from .domain import run_access
from .security import Error, elevated, require
from .transport import worker_request


def browser_origin(value):
    require(isinstance(value, str), "KiroCrew 浏览器地址无效")
    parts = urlsplit(value)
    require(parts.scheme == "https" or parts.scheme == "http" and
            parts.hostname in {"localhost", "127.0.0.1", "::1"},
            "KiroCrew 远程浏览器入口必须使用 HTTPS；HTTP 仅限本机")
    require(parts.hostname and not parts.username and not parts.password and
            parts.path in {"", "/"} and not parts.query and not parts.fragment,
            "KiroCrew 浏览器地址只接受 origin，不包含路径、凭据或查询参数")
    # Match the browser URL.origin representation (case, IDNA and default ports).
    port = parts.port
    host = "[" + parts.hostname + "]" if ":" in parts.hostname else parts.hostname.encode("idna").decode("ascii")
    suffix = ":" + str(port) if port is not None and port != {"http": 80, "https": 443}[parts.scheme] else ""
    return parts.scheme + "://" + host + suffix


def available(run):
    require(run["lifecycle"] == "open" and run["desired"] not in {"close", "delete"} and
            run["status"] != "reopening",
            "此 Session 已关闭或正在关闭，请先重新打开后进入 KiroCrew", 409, "session_closed")
    require(run["native_parent"] and run["worker_id"],
            "原生 Session 尚未建立，请等待 Worker 准备完成", 409, "not_prepared")


def observe(service, actor, rid, before=None, limit=50):
    """Read-only view of the run's native Session through its Worker (D30). Authorised like reading the run, so it also
    serves Team members on a shared environment, who cannot get the Gateway-wide native login (K03)."""
    require(type(limit) is int and 1 <= limit <= 100, "limit 须为 1–100 的整数")
    require(before is None or type(before) is int and before >= 0, "before 须为非负整数")
    with service.db.read() as db:
        run = run_access(db, actor, rid)
        require(run["lifecycle"] != "deleted", "Session 已删除", 409, "invalid_state")
        require(run["native_parent"] and run["worker_id"], "原生 Session 尚未建立，请等待 Worker 准备完成", 409, "not_prepared")
        worker = db.execute("SELECT config,enabled FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        require(worker is not None and worker["enabled"], "Session 所在的执行环境已停用", 409, "environment_disabled")
    body = {"run_id": rid, "native_parent": run["native_parent"], "before": before, "limit": limit}
    service.audit_read(actor, rid, "observe")
    try:
        return worker_request(json.loads(worker["config"]), "/observe", body, timeout=15)
    except Error as exc:
        if exc.status == 404 and exc.code == "not_found":
            raise Error(409, "worker_upgrade_required", "此执行环境的 Worker 尚未支持会话观察，请更新 Worker") from None
        raise


def create(service, actor, rid):
    with service.db.read() as db:
        try:
            run = run_access(db, actor, rid, admin=False)
        except Error as exc:
            # An admin can see the Session through oversight, but the native login would cover its whole Gateway.
            if exc.status == 404 and elevated(actor) and db.execute("SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone():
                raise Error(403, "forbidden", "原生 KiroCrew 入口不因管理员身份开放（原生登录覆盖整个 Gateway）；"
                                              "请在本页「KiroCrew 会话」查看") from None
            raise
        available(run)
        worker = service._worker(db, actor["id"], run["worker_id"], run["team_id"])
    # The native login covers the whole Gateway (K03): on a shared environment (D28) only its boundary owner opens it.
    require(worker["scope"] != "shared" or worker["owner_id"] == actor["id"],
            "共享执行环境不开放原生 KiroCrew 入口（原生登录覆盖整个 Gateway）；请在控制台查看会话", 403, "forbidden")
    config = json.loads(worker["config"])
    health = worker_request(config, "/health")
    registered = config["capabilities"]
    require(health.get("boundary") == registered.get("boundary") and
            health.get("host_principal") == registered.get("host_principal") and
            health.get("isolation") == registered.get("isolation"),
            "Worker 执行边界发生变化，请重新核查", 409, "worker_changed")
    # A Team's own Worker must be a dedicated boundary: every member's login covers its Gateway. On a shared environment
    # only its boundary owner got this far (checked above), so a Team session there opens like the owner's own.
    require(not run["team_id"] or worker["scope"] == "shared" or health.get("isolation") == "dedicated",
            "Team 原生入口需要该 Team 的专属 Worker", 403, "forbidden")
    require(health.get("crew_entry") is True,
            "此执行环境的 Worker 版本尚不支持原生会话入口：请平台管理员更新它", 409, "worker_upgrade_required")
    origin = browser_origin(health.get("crew_origin"))
    require(urlsplit(config["url"]).hostname in {"localhost", "127.0.0.1", "::1"} or
            urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"},
            "该执行环境尚未开通原生 KiroCrew 入口：请平台管理员为它开通",
            409, "crew_origin_required")
    result = worker_request(config, "/crew-entry", {"run_id": rid, "native_parent": run["native_parent"]})
    target = urlsplit(result.get("url", ""))
    params = parse_qs(target.query)
    require(result.get("native_parent") == run["native_parent"] and
            target.scheme + "://" + target.netloc == origin and target.path == "/chat" and
            not target.fragment and set(params) == {"sid", "token"} and
            params["sid"] == [run["native_parent"]] and len(params["token"]) == 1,
            "Worker 返回的原生会话入口与当前 Session 不一致", 409, "entry_mismatch")
    # Membership, closure and Worker changes during the network request take effect.
    with service.db.read() as db:
        current = run_access(db, actor, rid, admin=False)
        available(current)
        current_worker = service._worker(db, actor["id"], current["worker_id"], current["team_id"])
        require(current["native_parent"] == run["native_parent"] and
                current_worker["id"] == worker["id"] and current_worker["config"] == worker["config"],
                "Session 执行环境已变化，请重试", 409, "worker_changed")
    # Do not persist this response in runs, commands, events, exports or model context.
    return {"url": result["url"], "origin": origin, "native_parent": run["native_parent"]}
