"""First-time deployment bootstrap through the public API; safe to re-run."""
from .client import Client


def register_boundary_worker(api_url, username, password, worker_url, worker_token, name, spare=False):
    """Log in as the admin and register the boundary Worker unless it is already registered.

    spare: register it as a spare execution environment (D28) for an admin to assign to a Team later."""
    login = Client(api_url, "").request("POST", "/v1/login", {"username": username, "password": password})
    client = Client(api_url, login["token"])
    try:
        me = client.request("GET", "/v1/me")
        for worker in client.request("GET", "/v1/workers"):
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
