"""First-time deployment bootstrap through the public API; safe to re-run."""
from .client import Client


def register_boundary_worker(api_url, username, password, worker_url, worker_token, name):
    """Log in as the admin and register the boundary Worker unless it is already registered."""
    login = Client(api_url, "").request("POST", "/v1/login", {"username": username, "password": password})
    client = Client(api_url, login["token"])
    try:
        me = client.request("GET", "/v1/me")
        for worker in client.request("GET", "/v1/workers"):
            if worker["name"] == name and worker.get("owner_id") == me["id"]:
                return {"id": worker["id"], "created": False}
        created = client.request("POST", "/v1/workers", {
            "name": name, "owner_id": me["id"], "connection": {"url": worker_url, "token": worker_token}})
        return {"id": created["id"], "created": True}
    finally:
        client.request("POST", "/v1/logout", {})
