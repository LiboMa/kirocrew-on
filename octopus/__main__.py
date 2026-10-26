"""KiroCrew ON launcher, administrative bootstrap and API client; settings come from octopus.config."""
import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time

from .client import Client
from .config import load
from .db import Database, dumps
from .security import create_user


def probe_host(host):
    """Readiness probes go to loopback when the API listens on a wildcard address."""
    return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host


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
    worker.add_argument("--approval-mode", choices=["", "auto", "interactive"], default=settings.approval_mode)
    link = sub.add_parser("connect", help="让 Claude Code / Kiro CLI / Codex / Kiro IDE 经 MCP 连接 KiroCrew ON（写入 MCP 配置与使用说明）")
    link.add_argument("client", choices=["claude", "kiro", "codex", "kiro-ide"])
    link.add_argument("--url", required=True, help="平台地址，如 https://<分发>.cloudfront.net 或 http://127.0.0.1:8918")
    link.add_argument("--token", help="已有 API Token（也可用环境变量 OCTOPUS_API_TOKEN）；都没有时用 --username 登录并创建一枚 30 天 Token")
    link.add_argument("--username", help="你的平台用户名（未指定时交互询问）")
    link.add_argument("--admin", action="store_true",
                      help="管理员：Token 额外含 admin scope（可调用管理员工具），默认 7 天（--expires 30d 最长 30 天）；只在本人设备上使用")
    link.add_argument("--expires", choices=["7d", "30d", "365d", "never"],
                      help="新 Token 的有效期（D62）：默认 30 天（--admin 为 7 天）；never 永不过期；--admin 最长 30d")
    link.add_argument("--password-env", default="OCTOPUS_PASSWORD", help="读取密码的环境变量；未设置时交互输入")
    link.add_argument("--scope", help="claude: user/project/local（默认 user）；kiro: workspace/global（默认 workspace）")
    link.add_argument("--project", help="kiro workspace 范围与 kiro-ide 的项目目录（默认当前目录）")
    link.add_argument("--dry-run", action="store_true")
    skill_cmd = sub.add_parser("skill", help="渲染 kirocrew-on Skill：写入指定 skills 目录，或省略 --dest 时输出到标准输出")
    skill_cmd.add_argument("--url", required=True, help="平台地址（写入 Skill）")
    skill_cmd.add_argument("--dest", action="append", default=[],
                           help="skills 目录，可重复，如 ~/.agents/skills、~/.codex/skills；写入 <dest>/kirocrew-on/SKILL.md")
    skill_cmd.add_argument("--dry-run", action="store_true")
    ready = sub.add_parser("wait-ready", help="等待服务就绪（供 systemd ExecStartPost 使用）")
    ready.add_argument("--api", action="store_true")
    ready.add_argument("--worker", action="store_true")
    ready.add_argument("--gateway", action="store_true")
    ready.add_argument("--timeout", type=float, default=60)
    boot = sub.add_parser("bootstrap-worker", help="部署引导：以管理员登录并注册执行边界 Worker（可重复执行）")
    boot.add_argument("--username", default="admin")
    boot.add_argument("--password-env", default="OCTOPUS_ADMIN_PASSWORD")
    boot.add_argument("--worker-url")
    boot.add_argument("--token-file")
    boot.add_argument("--name", required=True)
    boot.add_argument("--owner", help="其专属执行环境的平台用户名（D49：每人一个边界；默认管理员本人）")
    boot.add_argument("--check-only", action="store_true", help="只核对 --owner（存在、尚无自己的执行环境），不登记")
    boot.add_argument("--spare", action="store_true", help="登记为空闲专属执行环境，由管理员在控制台分配给 Team")
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
    from . import cli
    words = sys.argv[1:] if argv is None else argv
    if words and words[0] in cli.COMMANDS:   # D58: the user's commands (login, submit, status …), not the operations ones
        return cli.main(list(words))
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
        engine = Engine(db, workers=settings.engine_workers, retry_unavailable=settings.retry_unavailable,
                        credentials_key_file=settings.credentials_key_file,
                        credentials_private_hosts=settings.credentials_private_hosts, poll_backoff=True)
        secret = settings.origin_secret_file.read_text().strip() if settings.origin_secret_file else None
        from . import transport
        ca = settings.worker_ca_file
        if ca and not ca.is_file():
            # Fail closed without taking the API down: Worker hosts signed by the private CA stay unreachable.
            print(f"警告：worker_ca_file {ca} 不存在；HTTPS Worker 只按系统证书库校验", file=sys.stderr)
            ca = None
        transport.configure(ca)
        httpd = server(db, args.port, args.host, allowed_hosts=settings.allowed_hosts, origin_secret=secret,
                       preview_url=settings.preview_url, preview_domain=settings.preview_domain, credentials_key_file=settings.credentials_key_file,
                       credentials_private_hosts=settings.credentials_private_hosts)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
        engine.start()

        def sweep():   # remote previews nobody used for 30 minutes, or whose Session closed (D36 §5.1)
            while not engine.stopping.wait(60):
                try:
                    httpd.service.auto_publish_pending()   # D47: Sessions that asked to be published on completion
                except Exception as exc:
                    logging.error("Octopus auto publish failed (%s)", type(exc).__name__)
                # D74: a takeover a restart interrupted, in its own thread (a move may take minutes)
                threading.Thread(target=httpd.service.carry_takeovers, daemon=True, name="octopus-takeover").start()
                try:
                    httpd.service.sweep_apps()
                except Exception as exc:
                    print(f"KiroCrew ON API: 远程预览回收失败（{type(exc).__name__}）", file=sys.stderr, flush=True)
        threading.Thread(target=sweep, daemon=True, name="octopus-app-sweep").start()
        print(f"KiroCrew ON API: http://{args.host}:{httpd.server_address[1]}", flush=True)
        try:
            httpd.serve_forever()
        finally:
            drained = engine.drain(settings.shutdown_grace)
            httpd.server_close()
            if not drained:
                # Worker calls can block for minutes; their outcome is reconciled from durable state on restart.
                print("KiroCrew ON API: 在途推进未在宽限期内结束；状态已持久化，重启后核对", flush=True)
                os._exit(0)
    elif args.command == "worker":
        from .worker import Worker, discovery_loop, serve
        token_path = Path(args.token_file)
        if not token_path.exists():
            token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            token_path.write_text(secrets.token_urlsafe(40))
            os.chmod(token_path, 0o600)
        node = Worker(args.data, token_path.read_text().strip(), args.gateway_port, args.crew_home,
                      args.isolation, args.routing_log, args.crew_url, args.approval_mode, settings.crew_entry_ttl,
                      kiro_cli=settings.tools.get("kiro_cli"), path_prepend=settings.path_prepend,
                      tool_paths=settings.tools, adapters_dir=settings.adapters_dir,
                      preview_tls_port=settings.preview_tls_port, preview_tls_name=settings.preview_tls_name,
                      disabled_tools=settings.disabled_tools, crew_yolo=settings.crew_yolo)
        httpd = serve(node, args.port, args.host)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
        # D51: each Coding tool's models are found without anyone asking (owner 2026-10-20)
        stop_discovery = threading.Event()
        threading.Thread(target=discovery_loop, args=(node, stop_discovery), daemon=True, name="octopus-model-discovery").start()
        print(f"KiroCrew ON Worker: http://{args.host}:{httpd.server_address[1]} ({args.isolation})", flush=True)
        try:
            httpd.serve_forever()
        finally:
            stop_discovery.set()
            node.apps.stop_all()   # under systemd the unit's cgroup ends them anyway; elsewhere they would linger
            httpd.server_close()
    elif args.command == "wait-ready":
        from .ops import runtime_marker_ok, wait_for_file, wait_ready
        deadline = time.monotonic() + args.timeout
        checks = []
        if args.api:
            checks.append((f"http://{probe_host(settings.api_host)}:{settings.api_port}/health", {}))
        if args.worker:
            # The Worker writes its token on first start; ExecStartPost may run before that.
            if not wait_for_file(settings.worker_token_file, args.timeout):
                raise SystemExit(f"未就绪：Worker Token 文件尚未生成（{settings.worker_token_file}）")
            token = settings.worker_token_file.read_text().strip()
            checks.append((f"http://{settings.worker_host}:{settings.worker_port}/health", {"Authorization": "Bearer " + token}))
        if args.gateway:
            checks.append((f"http://127.0.0.1:{settings.gateway_port}/api/ready", {}))
        pending = wait_ready(checks, max(0.0, deadline - time.monotonic()))
        if args.gateway and not pending and not runtime_marker_ok(settings.gateway_runtime_file):
            pending.append(f"{settings.gateway_runtime_file}（未加载 PocProviderRegistry 组合）")
        if pending:
            raise SystemExit("未就绪：" + "；".join(pending))
        print("ready")
    elif args.command == "bootstrap-worker":
        from .bootstrap import register_boundary_worker
        url = f"http://{probe_host(settings.api_host)}:{settings.api_port}"
        if not args.check_only and not (args.worker_url and args.token_file):
            raise SystemExit("bootstrap-worker 需要 --worker-url 与 --token-file（只核对用户时用 --check-only）")
        print(dumps(register_boundary_worker(url, args.username, os.environ[args.password_env], args.worker_url,
                                             Path(args.token_file).read_text().strip() if args.token_file else None,
                                             args.name, spare=args.spare, owner=args.owner, check_only=args.check_only)))
    elif args.command == "backup":
        from .backup import backup_database
        print(dumps(backup_database(settings.data_dir / "octopus.sqlite3", Path(args.dest))))
    elif args.command == "archive":
        from .backup import archive_directory
        print(dumps(archive_directory(Path(args.source), Path(args.dest), args.name, tuple(args.exclude))))
    elif args.command == "connect":
        import subprocess
        from .connect import connect, revoke
        token = args.token or os.environ.get("OCTOPUS_API_TOKEN")
        if token and args.admin:
            raise SystemExit("--admin 只用于以账号登录签发新 Token：不能与 --token 或环境变量 OCTOPUS_API_TOKEN 同时使用")
        if token and args.expires:
            raise SystemExit("--expires 只用于以账号登录签发新 Token：不能与 --token 或环境变量 OCTOPUS_API_TOKEN 同时使用")
        if token and not args.token:
            print("使用环境变量 OCTOPUS_API_TOKEN 中的 Token")
        issued = None
        if not token and not args.dry_run:
            from .connect import issue_token
            username = args.username
            if not username:
                if not sys.stdin.isatty():
                    raise SystemExit("未指定 --username，且没有可交互的终端：请用 --username <你的平台用户名>")
                username = input("平台用户名：").strip()
            password = os.environ.get(args.password_env) or getpass.getpass(f"{username} 的密码：")
            try:
                issued = issue_token(Client, args.url, args.client, username, password, admin=args.admin, expires=args.expires)
            except (RuntimeError, ValueError, OSError) as exc:
                raise SystemExit(f"错误：{exc}") from None
            token = issued["token"]
            if issued.get("warning"):
                print("提示：" + issued["warning"], file=sys.stderr)
        try:
            actions = connect(args.client, args.url, token or "dry-run-token", scope=args.scope, project=args.project,
                              dry_run=args.dry_run)
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            # Never echo the command: its arguments carry the token. A ValueError is a client file it cannot merge: said as is.
            code = getattr(exc, "returncode", None)
            note = ""
            if issued:
                try:
                    revoke(Client, args.url, issued)
                    note = "；刚签发的 Token 已吊销"
                except (RuntimeError, OSError):
                    note = "；刚签发的 Token 未能吊销，请在控制台撤销"
            reason = str(exc) if isinstance(exc, ValueError) else ("退出码 " + str(code) if code is not None else type(exc).__name__)
            raise SystemExit(f"错误：写入 {args.client} 配置失败（{reason}）{note}") from None
        for action in actions:
            print(action)
        from .connect import lifetime as said   # D62: what the platform gave, not what was asked (final review C1)
        lifetime = said(issued.get("expires")) if issued else "使用你提供的 Token"
        print(f"完成：重新打开客户端后可用 kirocrew-on 的 MCP 工具；{lifetime}，可在控制台撤销。" if not args.dry_run
              else "（dry-run，未写入）")
    elif args.command == "skill":
        from .connect import install_skill, skill
        if not args.dest:
            print(skill(args.url), end="")
        for dest in args.dest:
            for action in install_skill(args.url, dest, args.dry_run):
                print(action)
    elif args.command == "restore-verify":
        from .backup import restore_verify
        print(dumps(restore_verify(Path(args.manifest), Path(args.into))))
    else:
        try:
            run_client(args)
        except (RuntimeError, ValueError, OSError) as exc:
            # API, proxy and connection errors are reported as one line; the exit code tells scripts it failed.
            print(f"错误：{exc}", file=sys.stderr)
            raise SystemExit(1) from None


def run_client(args):
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
