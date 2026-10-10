"""Local launcher, administrative bootstrap and optional API client."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets

from .client import Client
from .db import Database, dumps
from .security import create_user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--data", default="state/mvp-1.0.0")
    init.add_argument("--username", default="admin")
    init.add_argument("--password-env")
    serve = sub.add_parser("serve")
    serve.add_argument("--data", default="state/mvp-1.0.0")
    serve.add_argument("--port", type=int, default=8918)
    serve.add_argument("--host", default="127.0.0.1")
    worker = sub.add_parser("worker")
    worker.add_argument("--data", default="state/mvp-worker")
    worker.add_argument("--token-file", required=True)
    worker.add_argument("--port", type=int, default=8928)
    worker.add_argument("--host", default="127.0.0.1")
    worker.add_argument("--gateway-port", type=int, default=5476)
    worker.add_argument("--crew-url", help="浏览器访问 KiroCrew 的 origin；默认 http://localhost:<gateway-port>")
    worker.add_argument("--crew-home")
    worker.add_argument("--routing-log")
    worker.add_argument("--isolation", choices=["dedicated", "trusted-local"], default="trusted-local")
    for command in ("request", "events", "mcp"):
        p = sub.add_parser(command)
        p.add_argument("--url", default=os.environ.get("OCTOPUS_URL", "http://127.0.0.1:8918"))
        if command == "request":
            p.add_argument("method", choices=["GET", "POST", "DELETE"])
            p.add_argument("path")
            p.add_argument("--body-file")
        if command == "events":
            p.add_argument("run_id")
            p.add_argument("--after", type=int, default=0)
            p.add_argument("--once", action="store_true", help="Read one bounded SSE connection")
    args = parser.parse_args()
    if args.command == "init":
        db = Database(args.data)
        password = os.environ[args.password_env] if args.password_env else getpass.getpass("Admin password (12+ chars): ")
        with db.transaction() as connection:
            if connection.execute("SELECT count(*) FROM users").fetchone()[0]:
                raise SystemExit("数据库已有用户；请通过管理员 API 管理用户。")
            user = create_user(connection, args.username, password, admin=True)
        print(dumps({"created": user, "data": str(db.root)}))
    elif args.command == "serve":
        from .engine import Engine
        from .server import server
        db = Database(args.data)
        engine = Engine(db)
        engine.start()
        print(f"Octopus MVP 1.0.0: http://{args.host}:{args.port}", flush=True)
        try:
            server(db, args.port, args.host).serve_forever()
        finally:
            engine.stopping.set()
    elif args.command == "worker":
        from .worker import Worker, serve
        token_path = Path(args.token_file)
        if not token_path.exists():
            token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            token_path.write_text(secrets.token_urlsafe(40))
            os.chmod(token_path, 0o600)
        node = Worker(args.data, token_path.read_text().strip(), args.gateway_port, args.crew_home,
                      args.isolation, args.routing_log, args.crew_url)
        print(f"Octopus Worker: http://{args.host}:{args.port} ({args.isolation})", flush=True)
        serve(node, args.port, args.host).serve_forever()
    else:
        client = Client(args.url, os.environ.get("OCTOPUS_API_TOKEN", ""))
        if args.command == "request":
            body = json.loads(Path(args.body_file).read_text()) if args.body_file else None
            print(json.dumps(client.request(args.method, args.path, body), ensure_ascii=False, indent=2))
        elif args.command == "events":
            after = args.after
            try:
                while True:
                    for event in client.events(args.run_id, after):
                        after = event["id"]
                        print(dumps(event), flush=True)
                    if args.once:
                        break
            except KeyboardInterrupt:
                pass
        else:
            from .mcp import serve
            serve(client)


if __name__ == "__main__":
    main()
