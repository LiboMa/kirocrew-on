"""KiroCrew ON launcher, administrative bootstrap and API client; settings come from octopus.config."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import signal
import threading

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
    ready = sub.add_parser("wait-ready", help="等待服务就绪（供 systemd ExecStartPost 使用）")
    ready.add_argument("--api", action="store_true")
    ready.add_argument("--worker", action="store_true")
    ready.add_argument("--gateway", action="store_true")
    ready.add_argument("--timeout", type=float, default=60)
    backup_cmd = sub.add_parser("backup", help="平台数据库一致性备份")
    backup_cmd.add_argument("--dest", required=True)
    archive = sub.add_parser("archive", help="目录归档（Worker 数据、执行边界）")
    archive.add_argument("source")
    archive.add_argument("--dest", required=True)
    archive.add_argument("--name", required=True)
    archive.add_argument("--exclude", action="append", default=[])
    verify = sub.add_parser("restore-verify", help="在独立目录恢复并校验备份")
    verify.add_argument("manifest")
    verify.add_argument("--into", required=True)
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
        engine = Engine(db, workers=settings.engine_workers, retry_unavailable=settings.retry_unavailable)
        httpd = server(db, args.port, args.host, allowed_hosts=settings.allowed_hosts)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
        engine.start()
        print(f"KiroCrew ON API: http://{args.host}:{httpd.server_address[1]}", flush=True)
        try:
            httpd.serve_forever()
        finally:
            engine.drain(30)
            httpd.server_close()
    elif args.command == "worker":
        from .worker import Worker, serve
        token_path = Path(args.token_file)
        if not token_path.exists():
            token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            token_path.write_text(secrets.token_urlsafe(40))
            os.chmod(token_path, 0o600)
        node = Worker(args.data, token_path.read_text().strip(), args.gateway_port, args.crew_home,
                      args.isolation, args.routing_log, args.crew_url)
        httpd = serve(node, args.port, args.host)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
        print(f"KiroCrew ON Worker: http://{args.host}:{httpd.server_address[1]} ({args.isolation})", flush=True)
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
    elif args.command == "wait-ready":
        from .ops import runtime_marker_ok, wait_ready
        checks = []
        if args.api:
            checks.append((f"http://{settings.api_host}:{settings.api_port}/health", {}))
        if args.worker:
            token = settings.worker_token_file.read_text().strip()
            checks.append((f"http://{settings.worker_host}:{settings.worker_port}/health", {"Authorization": "Bearer " + token}))
        if args.gateway:
            checks.append((f"http://127.0.0.1:{settings.gateway_port}/api/ready", {}))
        pending = wait_ready(checks, args.timeout)
        if args.gateway and not pending and not runtime_marker_ok(settings.gateway_runtime_file):
            pending.append(f"{settings.gateway_runtime_file}（未加载 PocProviderRegistry 组合）")
        if pending:
            raise SystemExit("未就绪：" + "；".join(pending))
        print("ready")
    elif args.command == "backup":
        from .backup import backup_database
        print(dumps(backup_database(settings.data_dir / "octopus.sqlite3", Path(args.dest))))
    elif args.command == "archive":
        from .backup import archive_directory
        print(dumps(archive_directory(Path(args.source), Path(args.dest), args.name, tuple(args.exclude))))
    elif args.command == "restore-verify":
        from .backup import restore_verify
        print(dumps(restore_verify(Path(args.manifest), Path(args.into))))
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
