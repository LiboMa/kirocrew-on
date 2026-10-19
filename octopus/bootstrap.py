"""First-time deployment bootstrap through the public API; safe to re-run."""
from .client import Client


def register_boundary_worker(api_url, username, password, worker_url, worker_token, name, spare=False, owner=None):
    """Log in as the admin and register the boundary Worker unless it is already registered.

    spare: register it as a spare execution environment (D28) for an admin to assign to a Team later.
    owner: the username whose own dedicated Worker it is (D49: one boundary per person); default the admin."""
    login = Client(api_url, "").request("POST", "/v1/login", {"username": username, "password": password})
    client = Client(api_url, login["token"])
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
        for worker in workers:
            # A Worker host keeps its name whatever an admin does with it (assigned to a Team, or shared); a Worker on the
            # platform host itself (trusted-local) is never one, even under the same name.
            if worker["name"] == name and (worker.get("capabilities", {}).get("isolation") == "dedicated" if spare
                                           else worker.get("owner_id") == me["id"]):
                # Re-registration re-validates and refreshes the connection, so a replaced host is picked up.
                client.request("POST", f"/v1/workers/{worker['id']}/connection",
                               {"connection": {"url": worker_url, "token": worker_token}})
                return {"id": worker["id"], "created": False, "updated": True}
        connection = {"url": worker_url, "token": worker_token}
        created = client.request("POST", "/v1/workers", {"name": name, "spare": True, "connection": connection} if spare
                                 else {"name": name, "owner_id": me["id"], "connection": connection})
        return {"id": created["id"], "created": True}
    finally:
        client.request("POST", "/v1/logout", {})
