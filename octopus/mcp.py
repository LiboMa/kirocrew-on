"""Standard newline-delimited stdio MCP adapter for the SAME authenticated API."""
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import quote, urlencode
import zipfile

from . import __version__
from .client import ApiError
from .db import dumps


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
OBSERVE_TEXT = 4000  # per message in octopus_observe: keeps a page well inside an agent's tool output limit
RECOVERY = object_schema({"enabled": {"type": "boolean"},
                          "max_cycles": {"type": "integer", "minimum": 1, "maximum": 5},
                          "switch_tools": {"type": "boolean"}})
TOOLS = [
    {"name": "octopus_workflows", "description": "列出当前用户可访问的工作流", "inputSchema": object_schema()},
    {"name": "octopus_match_workflow",
     "description": "按研发需求匹配可运行的工作流/模板，返回名称、任务和固定版本；仅预览，不执行。用户指定 workflow_id/template_id/steps 时优先采用。Chat 可编辑返回的 steps，再用 octopus_start 提交；只有模板规则匹配，不冒充 AI 生成。",
     "inputSchema": object_schema({"input": STRING, "name": STRING, "workflow_id": STRING,
                                   "workflow_version": {"type": "integer"}, "template_id": STRING,
                                   "steps": {"type": "array", "items": {"type": "object"}}, "team_id": STRING}, ["input"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "octopus_teams", "description": "列出当前用户的团队及成员", "inputSchema": object_schema()},
    {"name": "octopus_recommend_workflow", "description": "由当前空间 Worker 的真实 Kiro Agent 推荐工作流和各 Task 提示词。仅生成建议，用户采用后再保存或提交开发。",
     "inputSchema": object_schema({"request_id": STRING, "input": STRING, "worker_id": STRING, "team_id": STRING,
                                   "workflow_id": STRING, "workflow_version": {"type": "integer"}, "template_id": STRING,
                                   "steps": {"type": "array", "items": {"type": "object"}}},
                                  ["request_id", "input", "worker_id"])},
    {"name": "octopus_workflow_recommendation", "description": "读取 KiroCrew 工作流建议状态、已校验的草稿及真实 ACP 证据。",
     "inputSchema": object_schema({"id": STRING}, ["id"]), "annotations": {"readOnlyHint": True}},
    {"name": "octopus_runs", "description": "列出当前用户有权读取的开发 Sessions", "inputSchema": object_schema()},
    {"name": "octopus_status",
     "description": "查询真实任务状态。默认返回精简视图：Session 状态、version、attention、规划、最近一次纠错决定、各任务最近一次尝试的结论"
                    "（decision、summary、findings、交接文件）和最近 10 条事件；detail=true 返回完整记录（含各次尝试的原始输出与证据，可能很大）。",
     "inputSchema": object_schema({"run_id": STRING, "detail": {"type": "boolean"}}, ["run_id"])},
    {"name": "octopus_preview",
     "description": "返回某个 Session 产出的 Web 预览链接（Workspace 中的 index.html 等静态页面，10 分钟有效，在隔离环境中运行，"
                    "页面内 localStorage、sessionStorage、IndexedDB 与 Cookie 不可用）；"
                    "需要开发服务器的项目请用 deploy/aws/deploy.sh space <run_id> 在本机预览。",
     "inputSchema": object_schema({"run_id": STRING}, ["run_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "octopus_observe",
     "description": "读取 Session 的原生 KiroCrew 会话（协调者与子 Agent 的对话、是否仍在运行），只读，不需要打开 KiroCrew。"
                    "默认最近 20 条，单条超过 4000 字截断（truncated=true）；has_more 为 true 时用 before=next_before 读取更早的消息。",
     "inputSchema": object_schema({"run_id": STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                                   "before": {"type": "integer", "minimum": 0}}, ["run_id"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "octopus_revisions",
     "description": "Session 的修订历史（创建、规划、续作、自动纠错）。只传 run_id 列出全部修订；传 from / to（版本号，可只传 to）"
                    "返回两版之间需求与各任务的变化：工具、模型等字段的前后值，以及 Prompt 中新增（+）与删除（-）的行；未变化的任务与行省略。",
     "inputSchema": object_schema({"run_id": STRING, "from": {"type": "integer", "minimum": 1},
                                   "to": {"type": "integer", "minimum": 1}}, ["run_id"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "octopus_report",
     "description": "Session 的 Feature 报告（Markdown）：需求与计划、每个任务最终一次执行的工具与结论、门禁（通过 / 失败 / 跳过）、"
                    "候选提交、检查命令结果、审查绑定、纠错记录与发布的 PR / MR。用户要「报告」「总结这个会话」或交付说明时用它。",
     "inputSchema": object_schema({"run_id": STRING}, ["run_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "octopus_parse_workflow",
     "description": "校验并规范化一个由 Agent 预先生成的工作流（JSON / YAML / 含一个代码块的 Markdown；新版导出或旧版 schema 1），返回 name、steps 与规范化说明；仅预览，不执行。",
     "inputSchema": object_schema({"content": STRING, "format": {"type": "string", "enum": ["json", "yaml", "markdown"]}},
                                  ["content"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "octopus_start", "description": "提交开发需求。未指定工作流、模板、steps 或 document 时，由 KiroCrew 的 Kiro Agent 识别意图、补全需求并拆分任务（第 1 个任务“需求分析与任务规划”，完成后自动追加并执行生成的任务）；plan=\"match\" 改用规则匹配模板。也可提交 Agent 预先生成的工作流文档 document（按提交内容运行）。返回登记回执，不代表执行完成；用 octopus_status 查看进度，octopus_continue 迭代。",
     "inputSchema": object_schema({
         "request_id": STRING, "input": STRING, "name": STRING, "template_id": STRING,
         "workflow_id": STRING, "workflow_version": {"type": "integer"},
         "steps": {"type": "array", "items": {"type": "object"}},
         "document": object_schema({"content": STRING, "format": {"type": "string", "enum": ["json", "yaml", "markdown"]}},
                                   ["content"]),
         "plan": {"type": "string", "enum": ["agent", "match"]},
         "source": {"type": "object"},
         "credentials": object_schema({"source": STRING}),
         "team_id": STRING, "worker_id": STRING, "recovery": RECOVERY}, ["request_id", "input", "source"])},
    {"name": "octopus_continue", "description": "原 Session 修订并重跑 Task；保留 Workspace；可覆盖模型/Effort/工具与任务契约。"
                                                "rerun 只列出受本次修改影响的后续任务（省略则重跑所选任务之后的全部任务）；"
                                                "依赖重跑任务的任务与其后的审查 / 测试任务会自动一并重跑，其余沿用上一版结果。",
     "inputSchema": object_schema({"run_id": STRING, "request_id": STRING, "expected_version": {"type": "integer"},
                                  "task_id": STRING, "input": STRING, "execution": {"type": "object"}, "recovery": RECOVERY,
                                  "rerun": {"type": "array", "items": STRING},
                                  "credentials": object_schema({"source": {"type": ["string", "null"]}})},
                                 ["run_id", "request_id", "expected_version", "task_id"])},
    {"name": "octopus_recover", "description": "在原 Session 和 Workspace 启动 KiroCrew Coordinator 诊断，按实际失败证据重试、退回上游返工并复审，或说明缺失输入。异步执行，以状态与纠错记录确认结果。",
     "inputSchema": object_schema({"run_id": STRING, "request_id": STRING, "expected_version": {"type": "integer"}},
                                 ["run_id", "request_id", "expected_version"])},
    {"name": "octopus_control", "description": "停止、关闭、重新打开 Session；删除要求完整 Session ID 确认，并先备份。",
     "inputSchema": object_schema({"run_id": STRING, "request_id": STRING, "expected_version": {"type": "integer"},
                                  "action": {"enum": ["stop", "close", "reopen", "delete"]}, "confirmation": STRING},
                                 ["run_id", "request_id", "expected_version", "action"]),
     "annotations": {"destructiveHint": True}},
    {"name": "octopus_save_workflow", "description": "创建工作流或保存新版本；修改现有版本需要 expected_version。",
     "inputSchema": object_schema({"workflow_id": STRING, "name": STRING, "steps": {"type": "array", "items": {"type": "object"}},
                                  "expected_version": {"type": "integer"}}, ["name", "steps"])},
    {"name": "octopus_share_workflow", "description": "Owner 授权指定用户或 Team 查看/运行/编辑工作流；不分享历史运行。",
     "inputSchema": object_schema({"workflow_id": STRING, "kind": {"enum": ["user", "team"]}, "principal": STRING,
                                  "role": {"enum": ["viewer", "runner", "editor"]}, "revoke": {"type": "boolean"}},
                                 ["workflow_id", "kind", "principal"])},
]


READ = {"readOnlyHint": True}
ADMIN = {"octopus_admin_users", "octopus_admin_teams", "octopus_admin_environments"}
TOOLS += [
    {"name": "octopus_me", "description": "当前身份：用户名、是否管理员、Token scope 与客户端（elevated 为 true 时可用管理员工具），"
                                         "以及所在的 Team、角色与各 Team 的执行环境。",
     "inputSchema": object_schema(), "annotations": READ},
    {"name": "octopus_environments",
     "description": "某个空间可用的执行环境（不传 team_id 为个人空间）。会话处于 waiting_worker 时，从这里取 worker_id 交给 octopus_assign_worker。",
     "inputSchema": object_schema({"team_id": STRING}), "annotations": READ},
    {"name": "octopus_assign_worker", "description": "为处于 waiting_worker 的 Session 分配执行环境（worker_id 取自 octopus_environments）；"
                                                    "换到另一个执行环境时工作区重新准备，已完成的任务保留。",
     "inputSchema": object_schema({"run_id": STRING, "request_id": STRING, "expected_version": {"type": "integer"},
                                   "worker_id": STRING}, ["run_id", "request_id", "expected_version", "worker_id"])},
    {"name": "octopus_changes",
     "description": "Session 的变更记录（新的在前）：每次写入的操作者（用户与客户端，或 Engine / Worker / KiroCrew / 协调者）、"
                    "字段前后值（长文本只有字符数）与原因。who=user 只看人的操作，kind 按动作前缀筛选（如 command.）。",
     "inputSchema": object_schema({"run_id": STRING, "who": {"type": "string", "enum": ["user", "system"]}, "kind": STRING,
                                   "before": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
                                  ["run_id"]), "annotations": READ},
    {"name": "octopus_download",
     "description": "把 Session 的源码（含 .git，不含依赖目录与 .env）下载并解压到本机目录 dest（默认 ./kirocrew-<run>），"
                    "返回目录、HEAD 与本机启动步骤（来自 .octopus/preview.json 或自动识别）。目录已存在且非空时拒绝。",
     "inputSchema": object_schema({"run_id": STRING, "dest": STRING}, ["run_id"])},
    {"name": "octopus_credentials",
     "description": "列出你可以使用或管理的代码仓库凭证（GitHub / GitLab；只有名称、类型、主机、范围与末 4 位，不含 Token），"
                    "可按 host 过滤。私有仓库提交时以 octopus_start 的 credentials.source 指定其 ID，不指定时平台按仓库自动匹配。"
                    "凭证只能由用户在控制台「设置 → 代码仓库凭证」创建：不要向用户索要 Token。",
     "inputSchema": object_schema({"host": STRING}), "annotations": READ},
    {"name": "octopus_publish",
     "description": "把会话成果发布到它的源仓库：推送分支（默认 octopus/<run-id>）并创建或沿用 Draft PR（GitHub）/ Draft MR（GitLab）。"
                    "需要会话绑定一枚有写权限的凭证，且会话已结束执行；工作区未提交的改动会先提交（不含 .env 与依赖目录），"
                    "改动或克隆以来的提交历史中含疑似凭证、或提交了 .env 时拒绝（409 publish_secret_found，列出文件）。远端分支被他人改动时 "
                    "409 publish_conflict，不会覆盖。分支已推送但 PR / MR 未创建时，错误会说明，再次发布沿用该分支。",
     "inputSchema": object_schema({"run_id": STRING, "branch": STRING, "title": STRING}, ["run_id"])},
    {"name": "octopus_app",
     "description": "Session 的远程预览（在执行环境中运行应用，经独立的预览域名在浏览器中打开）：start 启动（按 .octopus/preview.json "
                    "或自动识别；会话执行中不能启动；每个执行环境最多 3 个）；status 查看状态与最近日志（installing / starting / "
                    "running / failed / stopped）；open 取得打开链接（10 分钟内有效、只能打开一次，打开后浏览器保持 8 小时）；"
                    "stop 停止。空闲 30 分钟自动停止。",
     "inputSchema": object_schema({"run_id": STRING, "action": {"type": "string", "enum": ["start", "status", "open", "stop"]}},
                                  ["run_id", "action"])},
    {"name": "octopus_admin_users",
     "description": "平台管理员（需含 admin 的 Token）：列出用户，或按用户名停用 / 恢复、设为 / 取消管理员。"
                    "不创建用户、不设置密码（请在控制台「用户管理」操作）；停用会立即吊销该用户的全部 Token。",
     "inputSchema": object_schema({"action": {"type": "string", "enum": ["list", "deactivate", "activate", "promote", "demote"]},
                                   "username": STRING}, ["action"]), "annotations": {"destructiveHint": True}},
    {"name": "octopus_admin_teams",
     "description": "平台管理员（需含 admin 的 Token）：列出、创建（name、owner 用户名、worker_id）、改名、归档 / 恢复 Team，"
                    "增改 / 移除成员（username、role），更换执行环境（worker_id；旧会话下次派发时进入等待）。",
     "inputSchema": object_schema({"action": {"type": "string", "enum": ["list", "create", "rename", "archive", "restore",
                                                                         "set_member", "remove_member", "environment"]},
                                   "team_id": STRING, "name": STRING, "owner": STRING, "worker_id": STRING, "username": STRING,
                                   "role": {"type": "string", "enum": ["owner", "member"]}}, ["action"]),
     "annotations": {"destructiveHint": True}},
    {"name": "octopus_admin_environments",
     "description": "平台管理员（需含 admin 的 Token）：列出执行环境（含实时健康），设为共享（confirm=true）、增减服务空间"
                    "（team_id 或 user_id）、改回专属、启用 / 停用、改名、退役。",
     "inputSchema": object_schema({"action": {"type": "string", "enum": ["list", "share", "add_space", "remove_space", "dedicate",
                                                                         "enable", "disable", "rename", "retire"]},
                                   "worker_id": STRING, "team_id": STRING, "user_id": STRING, "name": STRING,
                                   "confirm": {"type": "boolean"}}, ["action"]),
     "annotations": {"destructiveHint": True}},
]


def scoped_tools(client):
    """(tools, known): administrator tools only with the admin scope (A3); every tool when the scope cannot be told
    (known=False, asked again next time). A short probe: tools/list must not wait on a slow platform."""
    try:
        scopes = client.request("GET", "/v1/me", timeout=5).get("scopes", [])
    except (RuntimeError, ValueError, OSError):
        return TOOLS, False
    return [tool for tool in TOOLS if tool["name"] not in ADMIN or "admin" in scopes], True


def tools_for(client):
    return scoped_tools(client)[0]


def usable(worker, me, team):
    """Whether a Worker serves the space (a Team, or the caller's personal space), as the platform chooses it."""
    if not worker.get("enabled") or worker.get("scope") == "spare":
        return False
    if worker.get("scope") == "shared":
        return any((space.get("team_id") == team) if team else (space.get("user_id") == me["id"])
                   for space in worker.get("spaces") or [])
    return worker.get("team_id") == team if team else worker.get("owner_id") == me["id"]


def extract(archive, target):
    """Extract into ``target`` only: entries whose path would leave it (``..``, absolute) are skipped and listed."""
    root, skipped, files = target.resolve(), [], 0
    for info in archive.infolist():
        destination = (root / info.filename.lstrip("/") if not info.filename.startswith("/") else None)
        if destination is None or not destination.resolve().is_relative_to(root) or ".." in Path(info.filename).parts:
            skipped.append(info.filename)
            continue
        if info.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(destination, "wb") as sink:
            while chunk := source.read(65536):
                sink.write(chunk)
        if mode := (info.external_attr >> 16) & 0o755:   # keeps ./gradlew runnable; never setuid
            os.chmod(destination, mode)
        files += 1
    return files, skipped


def download(client, args):
    """Download a Session's source and unpack it on this machine (D36 §4.3); returns where and how to start it."""
    from . import preview_spec
    rid = args["run_id"]
    target = Path(args.get("dest") or f"kirocrew-{rid[4:16]}").expanduser().resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError(f"{target} 已存在且非空：请指定另一个目录（dest）")
    link = client.request("POST", "/v1/runs/" + rid + "/archive", {})
    with tempfile.TemporaryFile() as raw:
        size = client.download(link["url"], raw)
        raw.seek(0)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(raw) as archive:
            files, skipped = extract(archive, target)
    readme = target / "OCTOPUS-ARCHIVE.txt"
    notes = readme.read_text(errors="replace") if readme.is_file() else ""
    head = next((line.split("：", 1)[1].strip() for line in notes.splitlines() if line.startswith("HEAD：")), None)
    spec, _, problem = preview_spec.resolve(target)
    return {"dir": str(target), "bytes": size, "files": files, "head": head,
            "start": preview_spec.local_steps(spec) if spec else [problem], "skipped": skipped, "readme": notes}


APP_LOG = 40   # status keeps the agent's context small; the console shows 200 lines


def app(client, args):
    """Start, look at, open or stop a Session's remote preview (D36 §5.3)."""
    action = args.get("action")
    if action not in {"start", "status", "open", "stop"}:
        raise ValueError("action 只能是 start / status / open / stop")
    path = "/v1/runs/" + quote(str(args.get("run_id") or ""), safe="") + "/app"
    if action == "status":
        view = client.request("GET", path)
        return {**view, "log": (view.get("log") or [])[-APP_LOG:]}
    if action == "open":
        return {**client.request("POST", path + "/open", {}),
                "note": "链接 10 分钟内有效、只能打开一次；打开后浏览器保持 8 小时，可直接刷新。只交给用户本人，不要转发到公开渠道。"}
    return client.request("POST", path, {"action": action})


def need(args, *keys):
    missing = [key for key in keys if not args.get(key)]
    if missing:
        raise ValueError("缺少参数：" + "、".join(missing))


USER_ACTIONS = {"deactivate": ("active", {"active": False}), "activate": ("active", {"active": True}),
                "promote": ("admin", {"admin": True}), "demote": ("admin", {"admin": False})}


def admin_users(client, args):
    """List users, or deactivate / restore / promote / demote one by username (never create users or set passwords, B7)."""
    action = args.get("action")
    if action != "list" and action not in USER_ACTIONS:
        raise ValueError("不支持的用户操作（list / deactivate / activate / promote / demote）")
    users = client.request("GET", "/v1/admin/users")
    if action == "list":
        return {"users": users}
    need(args, "username")
    user = next((u for u in users if u["username"] == args["username"].strip().lower()), None)
    if user is None:
        raise ApiError(404, "not_found", "用户不存在")
    path, body = USER_ACTIONS[action]
    return client.request("POST", f"/v1/users/{quote(user['id'])}/{path}", body)


def admin_teams(client, args):
    action = args.get("action")
    if action == "list":
        return {"teams": client.request("GET", "/v1/teams")}
    if action == "create":
        need(args, "name", "owner", "worker_id")
        return client.request("POST", "/v1/teams", {key: args[key] for key in ("name", "owner", "worker_id")})
    if action not in {"rename", "archive", "restore", "set_member", "remove_member", "environment"}:
        raise ValueError("不支持的 Team 操作")
    need(args, "team_id")
    base = "/v1/teams/" + quote(args["team_id"])
    if action == "rename":
        need(args, "name")
        return client.request("PATCH", base, {"name": args["name"]})
    if action in {"archive", "restore"}:
        return client.request("POST", base + "/archive", {"archived": action == "archive"})
    if action == "environment":
        need(args, "worker_id")
        return client.request("POST", base + "/environment", {"worker_id": args["worker_id"]})
    need(args, "username")
    if action == "set_member":   # explicit: a default would silently demote an existing owner
        need(args, "role")
        return client.request("POST", base + "/members", {"username": args["username"], "role": args["role"]})
    return client.request("DELETE", base + "/members", {"username": args["username"]})


def admin_environments(client, args):
    action = args.get("action")
    if action == "list":
        return {"environments": client.request("GET", "/v1/admin/environments")}
    if action not in {"share", "add_space", "remove_space", "dedicate", "enable", "disable", "rename", "retire"}:
        raise ValueError("不支持的执行环境操作")
    need(args, "worker_id")
    base = "/v1/workers/" + quote(args["worker_id"])
    if action == "share":
        if args.get("confirm") is not True:
            raise ValueError("设为共享环境需要 confirm=true：环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆")
        return client.request("POST", base + "/share", {"confirm": True})
    if action in {"add_space", "remove_space"}:
        if not (args.get("team_id") or args.get("user_id")):
            raise ValueError("缺少参数：team_id 或 user_id")
        space = {"team_id": args["team_id"]} if args.get("team_id") else {"user_id": args["user_id"]}
        return client.request("POST" if action == "add_space" else "DELETE", base + "/spaces", space)
    if action in {"enable", "disable"}:
        return client.request("POST", base + "/enabled", {"enabled": action == "enable"})
    if action == "rename":
        need(args, "name")
        return client.request("PATCH", base, {"name": args["name"]})
    return client.request("POST", base + "/" + action, {})   # dedicate, retire


SESSION_FIELDS = ("id", "name", "status", "desired", "lifecycle", "version", "error", "attention", "progress", "input",
                  "planning", "team_id", "worker_id", "workflow_name", "match_mode", "created", "updated", "kept", "verified")


def compact_status(session, events):
    """What a Coding Agent reports and acts on. Raw agent output, prompts, routing and recovery context are left out:
    a finished session is otherwise far beyond MCP clients' output limits (detail=true returns everything)."""
    def latest(attempts):
        if not attempts:
            return None
        attempt = max(attempts, key=lambda a: a.get("number") or 0)
        evidence = attempt.get("evidence") or {}
        outcome = evidence.get("outcome") or {}
        value = {"number": attempt.get("number"), "status": attempt.get("status"), "error": attempt.get("error"),
                 "backend": evidence.get("actual_backend"), "model": evidence.get("actual_model"),
                 "decision": outcome.get("decision"), "summary": outcome.get("summary"),
                 "findings": outcome.get("findings") or [], "handoff": evidence.get("handoff_path")}
        if evidence.get("gates"):   # D37: passed as a count, failures with their first line, skipped by name
            records = evidence["gates"]
            value["gates"] = {"passed": sum(g.get("status") == "pass" for g in records),
                              "failed": [{"name": g["name"], "detail": str(g.get("detail") or "").split("\n")[0][:300]}
                                         for g in records if g.get("status") == "fail"],
                              "skipped": [g["name"] for g in records if g.get("status") == "skip"]}
        if evidence.get("candidate"):
            candidate = evidence["candidate"]
            value["candidate"] = {"sha": str(candidate.get("sha", ""))[:12],
                                  **{k: candidate.get(k) for k in ("files", "insertions", "deletions")}}
        return value

    view = {key: session[key] for key in SESSION_FIELDS if key in session}
    if session.get("recovery"):
        recovery, last = session["recovery"], session["recovery"].get("latest") or {}
        decision = last.get("decision") or {}
        view["recovery"] = {"used": recovery.get("used"), "policy": recovery.get("policy"), "latest": {
            "number": last.get("number"), "status": last.get("status"), "failed_task_id": last.get("failed_task_id"),
            "action": decision.get("action"), "target_task_id": decision.get("target_task_id"),
            "reason": decision.get("reason")} if last else None}
    view["tasks"] = [{"id": task["id"], "name": task["spec"].get("name"), "status": task["status"],
                      "tool": task["spec"].get("tool"), "model": task["spec"].get("model"),
                      "attempts": len(task.get("attempts") or []), "latest": latest(task.get("attempts"))}
                     for task in session.get("tasks", [])]
    recent = [{**event, "payload": {k: v for k, v in event["payload"].items() if k not in ("evidence", "context")}}
              for event in events[-10:]]
    return {"session": view, "events": recent}


def call(client, name, args):
    args = dict(args)
    if name in {"octopus_workflows", "octopus_teams", "octopus_runs"}:
        return client.request("GET", "/v1/" + name.removeprefix("octopus_"))
    if name == "octopus_match_workflow":
        return client.request("POST", "/v1/workflows/resolve", args)
    if name == "octopus_preview":
        result = client.request("POST", "/v1/runs/" + args["run_id"] + "/preview", {})
        return {**result, "url": client.url + result["url"]}
    if name == "octopus_parse_workflow":
        return client.request("POST", "/v1/workflows/parse", args)
    if name == "octopus_report":
        return {"markdown": client.text("GET", "/v1/runs/" + quote(args["run_id"], safe="") + "/report?format=md")}
    if name == "octopus_revisions":
        base = "/v1/runs/" + args["run_id"] + "/revisions"
        if "from" not in args and "to" not in args:
            return {"revisions": client.request("GET", base)}
        query = "&".join(f"{key}={int(args[key])}" for key in ("from", "to") if key in args)
        result = client.request("GET", base + "/diff?" + query)
        changed = lambda ops: [f"{op['op']} {op['text']}" for op in ops if op["op"] != " "]
        return {**{k: result[k] for k in ("from", "to", "from_summary", "to_summary", "reconstructed")},
                "input": changed(result["input"]),
                "tasks": [{"id": t["id"], "name": t["name"], "status": t["status"], "fields": t["fields"],
                           "prompt": changed(t["prompt"])} for t in result["tasks"] if t["status"] != "same"]}
    if name == "octopus_observe":
        query = "limit=" + str(int(args.get("limit", 20))) + ("&before=" + str(int(args["before"])) if "before" in args else "")
        result = client.request("GET", "/v1/runs/" + args["run_id"] + "/observe?" + query)
        result["messages"] = [{**m, "content": m["content"][:OBSERVE_TEXT],
                               "truncated": m.get("truncated") or len(m["content"]) > OBSERVE_TEXT}
                              for m in result.get("messages", [])]
        return result
    if name == "octopus_recommend_workflow":
        return client.request("POST", "/v1/designs", args)
    if name == "octopus_workflow_recommendation":
        return client.request("GET", "/v1/designs/" + args["id"])
    if name == "octopus_status":
        rid = args["run_id"]
        session, events = client.request("GET", "/v1/runs/" + rid), client.request("GET", "/v1/runs/" + rid + "/events")
        return {"session": session, "events": events} if args.get("detail") else compact_status(session, events)
    if name == "octopus_start":   # the platform chooses the space's Worker (D28); waiting_worker → octopus_assign_worker
        return client.request("POST", "/v1/runs", args)
    if name == "octopus_me":
        me, teams = client.request("GET", "/v1/me"), client.request("GET", "/v1/teams")
        return {**{key: me.get(key) for key in ("id", "username", "scopes", "client")}, "admin": bool(me.get("admin")),
                "elevated": bool(me.get("admin")) and "admin" in me.get("scopes", []),
                "teams": [{"id": t["id"], "name": t["name"], "role": t["role"], "archived": bool(t.get("archived")),
                           "environment": t.get("environment")} for t in teams if t.get("role")]}
    if name == "octopus_environments":
        me, team = client.request("GET", "/v1/me"), args.get("team_id")
        return {"space": team or "personal", "environments": [
            {"id": w["id"], "name": w["name"], "scope": w.get("scope"), "isolation": (w.get("capabilities") or {}).get("isolation"),
             "models": (w.get("capabilities") or {}).get("models", {})}
            for w in client.request("GET", "/v1/workers") if usable(w, me, team)]}
    if name == "octopus_download":
        return download(client, args)
    if name == "octopus_app":
        return app(client, args)
    if name == "octopus_publish":
        rid = args.pop("run_id")
        return client.request("POST", "/v1/runs/" + quote(rid, safe="") + "/publish", args)
    if name == "octopus_credentials":
        query = ("?" + urlencode({"host": args["host"]})) if args.get("host") else ""
        return {"credentials": client.request("GET", "/v1/credentials" + query)}
    if name == "octopus_assign_worker":
        rid = args.pop("run_id")
        return client.request("POST", "/v1/runs/" + rid + "/assign-worker", args)
    if name == "octopus_changes":
        query = [(key, args[key]) for key in ("who", "kind", "before") if args.get(key) not in (None, "")]
        query.append(("limit", int(args.get("limit", 50))))
        return {"changes": client.request("GET", "/v1/runs/" + args["run_id"] + "/changes?" + urlencode(query))}
    if name in {"octopus_continue", "octopus_recover", "octopus_control"}:
        rid = args.pop("run_id")
        if name == "octopus_continue" and "credentials" in args:
            # A Session stopped on its credential: change (or remove) it first, then continue (D35 §4).
            client.request("POST", "/v1/runs/" + quote(rid, safe="") + "/credentials", {"source": args.pop("credentials").get("source")})
        action = args.pop("action") if name == "octopus_control" else name.removeprefix("octopus_")
        if action not in {"continue", "recover", "stop", "close", "reopen", "delete"}:
            raise ValueError("Invalid action")
        return client.request("POST", "/v1/runs/" + rid + "/" + action, args)
    if name in ADMIN:
        return {"octopus_admin_users": admin_users, "octopus_admin_teams": admin_teams,
                "octopus_admin_environments": admin_environments}[name](client, args)
    if name == "octopus_save_workflow":
        wid = args.pop("workflow_id", None)
        return client.request("POST", "/v1/workflows" + ("/" + wid + "/versions" if wid else ""), args)
    if name == "octopus_share_workflow":
        wid, revoke = args.pop("workflow_id"), args.pop("revoke", False)
        return client.request("DELETE" if revoke else "POST", "/v1/workflows/" + wid + "/shares", args)
    raise ValueError("Unknown tool")


def serve(client, input_stream=sys.stdin, output_stream=sys.stdout):
    listed = []
    for line in input_stream:
        if len(line) > 1_000_000:
            continue
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "octopus-team", "version": __version__}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                if listed:
                    tools = listed[0]
                else:
                    tools, known = scoped_tools(client)
                    if known:
                        listed.append(tools)   # one /v1/me per MCP session once the scope is known
                result = {"tools": tools}
            elif method == "tools/call":
                try:
                    value = call(client, request["params"]["name"], request["params"].get("arguments", {}))
                    result = {"content": [{"type": "text", "text": dumps(value)}],
                              "structuredContent": value if isinstance(value, dict) else {"items": value}}
                except Exception as exc:   # any failure is this call's error; the server keeps answering
                    # Structured, so an Agent can tell 401 / 403 / 404 / 409 apart and say who must act (spec §7.2).
                    error = ({"status": exc.status, "code": exc.code, "error": exc.error} if isinstance(exc, ApiError)
                             else {"status": 0, "code": "client_error", "error": str(exc)})
                    result = {"isError": True, "content": [{"type": "text", "text": str(exc)}], "structuredContent": error}
            else:
                output_stream.write(dumps({"jsonrpc": "2.0", "id": request["id"],
                                           "error": {"code": -32601, "message": "Method not found"}}) + "\n")
                output_stream.flush()
                continue
            output_stream.write(dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
            output_stream.flush()
        except (ValueError, KeyError, TypeError):
            output_stream.write(dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC"}}) + "\n")
            output_stream.flush()
