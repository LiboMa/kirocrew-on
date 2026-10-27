"""First-time deployment bootstrap through the public API; safe to re-run."""
import os

from .client import Client


def admin(api_url, username, password):
    """A client acting as the administrator: with the token the scripts minted on this host (OCTOPUS_ADMIN_TOKEN, from
    `octopus local-token`; review 2026-10-28: the initial password may have been changed since), else by logging in.
    Each caller logs out at the end, which revokes either."""
    token = os.environ.get("OCTOPUS_ADMIN_TOKEN")
    if token:
        return Client(api_url, token)
    login = Client(api_url, "").request("POST", "/v1/login", {"username": username, "password": password})
    return Client(api_url, login["token"])


def register_boundary_worker(api_url, username, password, worker_url, worker_token, name, spare=False, owner=None,
                             check_only=False):
    """Log in as the admin and register the boundary Worker unless it is already registered.

    spare: register it as a spare execution environment (D28) for an admin to assign to a Team later.
    owner: the username whose own dedicated Worker it is (D49: one boundary per person); default the admin.
    check_only: only check the owner (it exists, has no other own environment), before a boundary is provisioned."""
    client = admin(api_url, username, password)
    try:
        me = client.request("GET", "/v1/me")
        if owner is not None:
            person = next((u for u in client.request("GET", "/v1/admin/users") if u["username"] == owner), None)
            if person is None:
                raise RuntimeError(f"平台上没有用户 {owner}：先在控制台创建该用户")
            me = {"id": person["id"]}
        workers = client.request("GET", "/v1/workers")
        if owner is not None and any(w.get("owner_id") == me["id"] and w["name"] != name and not w.get("team_id")
                                     and w.get("scope") == "dedicated" and w.get("enabled") for w in workers):
            raise RuntimeError(f"用户 {owner} 已有自己的执行环境：每人一个边界（D49）")
        if check_only:
            return {"ok": True, "owner": owner}
        worker = registered(workers, name, spare, me["id"])
        if worker:   # re-registration re-validates and refreshes the connection, so a replaced host is picked up
            client.request("POST", f"/v1/workers/{worker['id']}/connection",
                           {"connection": {"url": worker_url, "token": worker_token}})
            return {"id": worker["id"], "created": False, "updated": True}
        connection = {"url": worker_url, "token": worker_token}
        created = client.request("POST", "/v1/workers", {"name": name, "spare": True, "connection": connection} if spare
                                 else {"name": name, "owner_id": me["id"], "connection": connection})
        return {"id": created["id"], "created": True}
    finally:
        client.request("POST", "/v1/logout", {})


def registered(workers, name, spare, owner_id):
    """The Worker a re-run of the scripts registered before under ``name``, or None. A Worker host keeps its name whatever
    an admin does with it (assigned to a Team, or shared); a person's boundary (D49) is dedicated too but never taken
    over by a Worker host of that name, and a Worker on the platform host itself (trusted-local) is never one."""
    for worker in workers:
        if spare and worker.get("scope") == "dedicated" and worker.get("owner_id") and not worker.get("team_id"):
            continue
        if worker["name"] == name and (worker.get("capabilities", {}).get("isolation") == "dedicated" if spare
                                       else worker.get("owner_id") == owner_id):
            return worker
    return None


def issue_enrollment(api_url, username, password, name, spare=False, owner=None):
    """Log in as the admin and issue a one-time enrollment code (MVP 0.2.0, N §3) for the Worker ``name`` to register
    itself with (``octopus worker enroll``), so its token never leaves its host: ``{id, code, expires, platform}``. A
    Worker the scripts registered before under that name gets no code: ``{registered: <id>}`` (refresh it with
    register_boundary_worker)."""
    client = admin(api_url, username, password)
    try:
        me = client.request("GET", "/v1/me")
        if owner is not None:
            person = next((u for u in client.request("GET", "/v1/admin/users") if u["username"] == owner), None)
            if person is None:
                raise RuntimeError(f"平台上没有用户 {owner}：先在控制台创建该用户")
            me = {"id": person["id"]}
        worker = registered(client.request("GET", "/v1/workers"), name, spare, me["id"])
        if worker:
            return {"registered": worker["id"]}
        issued = client.request("POST", "/v1/workers/enrollments", {"name": name, "spare": True} if spare
                                else {"name": name, "owner_id": me["id"]})
        return {"id": issued["id"], "code": issued["code"], "expires": issued["expires"], "platform": api_url}
    finally:
        client.request("POST", "/v1/logout", {})


def allocate_boundary(api_url, username, password, owner, name):
    """Log in as the admin and have the platform name a new boundary (MVP 0.2.0, S9): its boundary_id and OS user id."""
    client = admin(api_url, username, password)
    try:
        return client.request("POST", "/v1/admin/boundaries", {"owner": owner, "name": name})
    finally:
        client.request("POST", "/v1/logout", {})

