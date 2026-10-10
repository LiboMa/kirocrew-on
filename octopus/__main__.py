"""KiroCrew ON launcher, administrative bootstrap and API client; settings come from octopus.config."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets

from .client import Client
from .config import load
from .db import Database, dumps
from .security import create_user


def settings_from_argv(argv, environ=None):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    known, rest = pre.parse_known_args(argv)
    return load(known.config, environ=environ), rest


def build_parser(settings, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="TOML 配置文件；也可用 OCTOPUS_CONFIG")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--data", default=str(settings.data_dir))
    init.add_argument("--username", default="admin")
    init.add_argument("--password-env")
    serve = sub.add_parser("serve")
    serve.add_argument("--data", default=str(settings.data_dir))
    serve.add_argument("--port", type=int, default=settings.api_port)
    serve.add_argument("--host", default=settings.api_host)
    worker = sub.add_parser("worker")
    worker.add_argument("--data", default=str(settings.worker_data_dir))
    worker.add_argument("--token-file", default=str(settings.worker_token_file))
    worker.add_argument("--port", type=int, default=settings.worker_port)
    worker.add_argument("--host", default=settings.worker_host)
    worker.add_argument("--gateway-port", type=int, default=settings.gateway_port)
    worker.add_argument("--crew-url", default=settings.crew_url or None,
                        help="浏览器访问 KiroCrew 的 origin；默认 http://localhost:<gateway-port>")
    worker.add_argument("--crew-home", default=str(settings.crew_home))
    worker.add_argument("--routing-log", default=str(settings.routing_log))
    worker.add_argument("--isolation", choices=["dedicated", "trusted-local"], default=settings.worker_isolation)
    default_url = environ.get("OCTOPUS_URL", f"http://{settings.api_host}:{settings.api_port}")
    for command in ("request", "events", "mcp"):
        p = sub.add_parser(command)
        p.add_argument("--url", default=default_url)
        if command == "request":
            p.add_argument("method", choices=["GET", "POST", "DELETE"])
            p.add_argument("path")
            p.add_argument("--body-file")
        if command == "events":
            p.add_argument("run_id")
            p.add_argument("--after", type=int, default=0)
            p.add_argument("--once", action="store_true", help="Read one bounded SSE connection")
    return parser


def main(argv=None):
    settings, _ = settings_from_argv(argv)
    args = build_parser(settings).parse_args(argv)
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
        print(f"KiroCrew ON API: http://{args.host}:{args.port}", flush=True)
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
        print(f"KiroCrew ON Worker: http://{args.host}:{args.port} ({args.isolation})", flush=True)
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
