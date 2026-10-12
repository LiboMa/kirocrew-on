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
            # A spare Worker keeps its name once an admin assigns it to a Team (scope becomes dedicated).
            if worker["name"] == name and (worker.get("scope") in {"spare", "dedicated"} if spare
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
