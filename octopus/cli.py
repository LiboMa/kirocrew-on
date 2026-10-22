"""The user's octopus commands (D58 M3-2): login, submit, plan, confirm, status, watch, report, pull, sync, continue,
approvals and search. They make the same calls as the MCP tools (mcp.call) and the same local git work (local.py), and
answer in Chinese for a person, or as JSON with --json."""
import argparse
import getpass
import json
import os
import sys
import time
import uuid
from pathlib import Path

from . import local, mcp
from .client import Client
from .policies import basis_text
from .requests import CLI_LABEL

EXPIRES = ("7d", "30d", "365d", "never")   # D62: how long the saved token lasts
COMMANDS = ("login", "submit", "plan", "confirm", "status", "watch", "report", "pull", "sync", "continue", "approvals", "search")
STATES = {"preparing": "准备中", "ready": "就绪", "running": "运行中", "waiting_approval": "等待批准", "dispatch_unknown": "派发待核实",
          "stopping": "停止中", "reopening": "重新打开中", "diagnosing": "诊断中", "failed": "失败", "blocked": "已阻塞",
          "completed": "已完成", "stopped": "已停止", "waiting_worker": "等待执行环境", "awaiting_confirmation": "计划待确认",
          "awaiting_acceptance": "验收待确认", "pending": "待执行", "accepted": "已验收", "dispatching": "派发中"}
SETTLED = {"failed", "blocked", "completed", "stopped", "waiting_worker", "awaiting_confirmation", "awaiting_acceptance"}
FILTERS = ("status", "space", "repo", "branch", "stage", "tool", "client", "submitter", "since", "until")


def parser():
    top = argparse.ArgumentParser(prog="octopus", description="KiroCrew ON 的使用者命令")
    sub = top.add_subparsers(dest="command", required=True)

    def command(name, help, session=True):
        p = sub.add_parser(name, help=help)
        p.add_argument("--url", help="平台地址（默认取 octopus login 时的）")
        p.add_argument("--json", action="store_true", help="输出 JSON")
        if session:
            p.add_argument("--run", help="会话 ID（默认取本仓库关联的会话）")
        return p
    login_cmd = command("login", "登录并保存 Token（~/.config/kirocrew-on/cli.json）", session=False)
    login_cmd.add_argument("--username")
    login_cmd.add_argument("--expires", choices=EXPIRES, default="30d", help="Token 有效期：7d、30d（默认）、365d 或 never（永不过期）")
    submit = command("submit", "提交需求（在 Git 仓库内时取 origin 与当前分支为源码）", session=False)
    submit.add_argument("input")
    submit.add_argument("--plan", choices=["agent", "match"])
    submit.add_argument("--admission", choices=["auto", "confirm"])
    submit.add_argument("--team")
    submit.add_argument("--new", action="store_true", help="新建项目，不用本地仓库")
    submit.add_argument("--push-base", action="store_true", help="先把当前分支推送到 origin")
    submit.add_argument("--allow-unpushed", action="store_true", help="按 origin 上已有的提交提交")
    command("plan", "查看计划：任务、工具、映射来源、验收与检查")
    command("confirm", "确认计划，开始执行")
    command("status", "精简状态").add_argument("--detail", action="store_true")
    command("watch", "跟随事件，直到会话停下")
    command("report", "Feature 报告").add_argument("--html", action="store_true")
    pull = command("pull", "把会话分支与上下文拉回本地仓库")
    pull.add_argument("--checkout", action="store_true")
    pull.add_argument("--full", action="store_true")
    command("sync", "把本地提交同步回会话，远端重新检查与审查").add_argument("--note")
    again = command("continue", "续作一个任务")
    again.add_argument("task")
    for flag in ("--prompt", "--tool", "--model", "--input"):
        again.add_argument(flag)
    approvals = command("approvals", "待批准的请求")
    approvals.add_argument("--answer", metavar="REQUEST_ID")
    approvals.add_argument("--decision", choices=["approve", "reject"])
    search = command("search", "查找会话（全文与过滤条件）", session=False)
    search.add_argument("q", nargs="?")
    for key in FILTERS:
        search.add_argument("--" + key)
    search.add_argument("--limit", type=int, default=20)
    return top


def config_path(home=None):
    return Path(home or Path.home()) / ".config/kirocrew-on/cli.json"


def login(url, username, password, home=None, expires="30d"):
    """Sign in, then keep a token of the CLI's own (label 「octopus CLI」, ``expires``: 30 days unless chosen, D62) instead
    of the login's; ttl still goes along for a platform from before D62."""
    answer = Client(url, "").request("POST", "/v1/login", {"username": username, "password": password})
    signed = Client(url, answer["token"])
    scopes = [s for s in answer.get("scopes", ["read", "execute", "manage"]) if s != "admin"]
    from .connect import TTL_DAYS
    token = signed.request("POST", "/v1/tokens", {"label": CLI_LABEL, "scopes": scopes, "expires_in": expires,
                                                  "ttl": min(TTL_DAYS[expires], 30) * 86400})   # before D62: ttl, ≤ 30 days
    signed.request("DELETE", "/v1/tokens/" + answer["id"])
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump({"url": url, "token": token["token"], "username": username, "expires": token.get("expires")}, file)
    os.chmod(path, 0o600)
    return {"username": username, "url": url, "expires": token.get("expires")}


def client_from(url, environ, home=None):
    path = config_path(home)
    saved = json.loads(path.read_text()) if path.is_file() else {}
    url = url or environ.get("OCTOPUS_URL") or saved.get("url")
    same = bool(url and saved.get("url")) and url.rstrip("/") == saved["url"].rstrip("/")
    token = environ.get("OCTOPUS_API_TOKEN") or (saved.get("token") if same else None)   # a saved token for its platform only
    if not url or not token:
        raise ValueError("还没有登录：请先运行 octopus login --url <平台地址>")
    return Client(url, token)


def _run_id(args, cwd):
    rid = getattr(args, "run", None) or (local.linked(cwd) or {}).get("run_id")
    if not rid:
        raise ValueError("需要会话 ID：用 --run <会话 ID> 指定，或在已关联会话的仓库中运行（submit、pull 会关联）")
    return rid


def _request():
    return "cli-" + uuid.uuid4().hex[:24]


def _state(value):
    return STATES.get(value, value or "—")


def _status_text(result):
    session = result["session"]
    lines = [f"{session.get('name') or session.get('input', '')[:60]}（{session['id']}）", f"状态：{_state(session.get('status'))}"]
    attention = session.get("attention") or {}
    if attention.get("title"):
        lines.append(f"需要处理：{attention['title']}" + (f"——{attention['detail']}" if attention.get("detail") else ""))
    if basis_text(session.get("admission_basis")):
        lines.append("准入：" + basis_text(session["admission_basis"]))
    for task in session.get("tasks") or []:
        latest = task.get("latest") or {}
        summary = str(latest.get("summary") or latest.get("error") or "").splitlines()
        lines.append(f"  · {task['id']}  {_state(task.get('status'))}  {task.get('tool', '')}"
                     + (f"  {summary[0][:80]}" if summary else ""))
    return "\n".join(lines)


def _plan_text(view):
    lines = [f"需求：{view.get('input', '')}", f"状态：{_state(view.get('status'))}"]
    for number, task in enumerate(view.get("tasks") or [], 1):
        spec = task.get("spec") or {}
        source = (task.get("mapping") or {}).get("label")
        lines.append(f"{number}. {spec.get('name', task['id'])}（{spec.get('kind', '—')}）  {spec.get('tool')} / {spec.get('model') or 'auto'}"
                     + (f"  来源：{source}" if source else ""))
        lines += [f"     验收：{item}" for item in spec.get("acceptance") or []]
        lines += [f"     检查：{command}" for command in spec.get("verify") or []]
    if view.get("status") == "awaiting_confirmation":
        lines.append("确认后开始执行：octopus confirm")
    return "\n".join(lines)


def run(argv, client, cwd):
    """One user command against ``client`` in ``cwd``; prints the answer."""
    args = parser().parse_args(argv)
    say = (lambda value, text: print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else text))
    if args.command == "submit":
        repo = None if args.new else local.repository(cwd)
        source = local.source(repo) if repo else {"type": "new"}
        if source["type"] == "git":
            missing, count = local.missing_branch(repo), local.unpushed(repo)
            if missing and not args.push_base:
                raise ValueError(f"当前分支 {missing} 不在 origin 上，执行环境克隆不到它：加 --push-base 先推送，或切换到 origin 上已有的分支")
            if (count or missing) and args.push_base:
                local.push_base(repo)
            elif count and not args.allow_unpushed:
                raise ValueError(f"本地有 {count} 个提交尚未推送到 origin，执行环境看不到它们：加 --push-base 先推送，"
                                 "或加 --allow-unpushed 按 origin 上已有的提交提交")
        body = {"request_id": _request(), "input": args.input, "source": source, "dir": str(repo or cwd),
                "allow_unpushed": True,   # checked (and pushed) here already

                **{key: value for key, value in (("plan", args.plan), ("admission", args.admission), ("team_id", args.team)) if value}}
        result = mcp.call(client, "octopus_start", body)
        if repo:
            local.link(repo, {"run_id": result["id"], "request_id": result.get("request_id"), "url": getattr(client, "url", None)})
        return say(result, f"已提交：{result['id']}（{_state(result.get('status'))}）\n查看进度：octopus status")
    if args.command == "search":
        query = {"limit": args.limit, **({"q": args.q} if args.q else {}),
                 **{key: getattr(args, key) for key in FILTERS if getattr(args, key)}}
        result = mcp.call(client, "octopus_runs", query)
        lines = [f"{item['id']}  {_state(item.get('status'))}  {item.get('name') or ''}" for item in result["items"]]
        return say(result, "\n".join(lines + (["……还有更多，缩小条件或加大 --limit"] if result["more"] else [])) or "没有找到会话")
    rid = _run_id(args, cwd)
    if args.command == "status":
        result = mcp.call(client, "octopus_status", {"run_id": rid, **({"detail": True} if args.detail else {})})
        return say(result, _status_text(result) if not args.detail else json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "plan":
        view = client.request("GET", f"/v1/runs/{rid}")
        return say(view, _plan_text(view))
    if args.command == "confirm":
        version = client.request("GET", f"/v1/runs/{rid}")["version"]
        result = mcp.call(client, "octopus_confirm", {"run_id": rid, "request_id": _request(), "expected_version": version})
        return say(result, f"已确认计划：{rid}（{_state(result.get('status'))}）")
    if args.command == "report":
        text = client.text("GET", f"/v1/runs/{rid}/report?format={'html' if args.html else 'md'}")
        return print(text)
    if args.command == "pull":
        result = local.pull(client, rid, cwd, checkout=args.checkout, full=args.full)
        return say(result, f"已拉回 {result['branch']}（{result['head'][:7]}）" + ("，已切换到该分支" if result["checked_out"] else "")
                   + f"\n上下文：{result['context']}")
    if args.command == "sync":
        result = local.sync(client, rid, cwd, note=args.note)
        return say(result, f"已同步 {result.get('commits')} 个本地提交（{result['head'][:7]}）：远端重新检查与审查\n查看进度：octopus watch")
    if args.command == "continue":
        version = client.request("GET", f"/v1/runs/{rid}")["version"]
        execution = {key: getattr(args, key) for key in ("prompt", "tool", "model") if getattr(args, key)}
        body = {"run_id": rid, "request_id": _request(), "expected_version": version, "task_id": args.task,
                **({"execution": execution} if execution else {}), **({"input": args.input} if args.input else {})}
        result = mcp.call(client, "octopus_continue", body)
        return say(result, f"已续作「{args.task}」：{_state(result.get('status'))}")
    if args.command == "approvals":
        listed = mcp.call(client, "octopus_approvals", {"run_id": rid})
        if not args.answer:
            items = listed.get("approvals") or []
            return say(listed, "\n".join(f"{a.get('request_id')}  {a.get('tool', '')}  {str(a.get('summary') or a.get('command') or '')[:100]}"
                                         for a in items) or "没有待批准的请求")
        if not args.decision:
            raise ValueError("答复需要 --decision approve 或 reject")
        item = next((a for a in listed.get("approvals") or [] if a.get("request_id") == args.answer), None)
        if item is None:
            raise ValueError(f"没有待批准的请求 {args.answer}")
        body = {"run_id": rid, "origin": item.get("origin", "native"), "request_id": args.answer, "decision": args.decision,
                **{k: item[k] for k in ("request_mid", "instance") if item.get(k)}}
        result = mcp.call(client, "octopus_approve", body)
        return say(result, f"已答复 {args.answer}：{args.decision}")
    if args.command == "watch":
        after = 0
        while True:
            for event in client.events(rid, after):
                after = event["id"]
                if not args.json:
                    print(time.strftime("%H:%M:%S", time.localtime(event.get("created") or time.time())), event.get("kind"))
                else:
                    print(json.dumps(event, ensure_ascii=False))
            view = client.request("GET", f"/v1/runs/{rid}")
            if view.get("status") in SETTLED:
                return say({"id": rid, "status": view["status"]}, f"会话已停下：{_state(view['status'])}")
    raise ValueError("不支持的命令")


def main(argv):
    args = parser().parse_args(argv)
    try:
        if args.command == "login":
            url = args.url or os.environ.get("OCTOPUS_URL")
            if not url:
                raise ValueError("需要平台地址：octopus login --url https://<平台地址>")
            username = args.username or input("用户名：")
            result = login(url.rstrip("/"), username, getpass.getpass("密码："), expires=args.expires)
            print(f"已登录 {result['url']}（{result['username']}）")
            return
        run(argv, client_from(args.url, os.environ), Path.cwd())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
