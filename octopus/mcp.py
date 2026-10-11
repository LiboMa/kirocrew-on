"""Standard newline-delimited stdio MCP adapter for the SAME authenticated API."""
import json
import sys

from . import __version__
from .db import dumps


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
RECOVERY = object_schema({"enabled": {"type": "boolean"},
                          "max_cycles": {"type": "integer", "minimum": 1, "maximum": 5}})
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
    {"name": "octopus_status", "description": "查询真实任务状态、执行配置及最近事件", "inputSchema": object_schema({"run_id": STRING}, ["run_id"])},
    {"name": "octopus_parse_workflow",
     "description": "校验并规范化一个由 Agent 预先生成的工作流（JSON / YAML / 含一个代码块的 Markdown；新版导出或旧版 schema 1），返回 name、steps 与规范化说明；仅预览，不执行。",
     "inputSchema": object_schema({"content": STRING, "format": {"type": "string", "enum": ["json", "yaml", "markdown"]}},
                                  ["content"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "octopus_start", "description": "提交开发需求；优先使用用户指定的工作流、模板、定制 steps 或 Agent 预先生成的工作流文档 document（按提交内容直接运行），未指定时自动匹配。建议先 octopus_match_workflow / octopus_parse_workflow 查看任务。返回登记回执，不代表执行完成。",
     "inputSchema": object_schema({
         "request_id": STRING, "input": STRING, "name": STRING, "template_id": STRING,
         "workflow_id": STRING, "workflow_version": {"type": "integer"},
         "steps": {"type": "array", "items": {"type": "object"}},
         "document": object_schema({"content": STRING, "format": {"type": "string", "enum": ["json", "yaml", "markdown"]}},
                                   ["content"]),
         "source": {"type": "object"},
         "team_id": STRING, "worker_id": STRING, "recovery": RECOVERY}, ["request_id", "input", "source"])},
    {"name": "octopus_continue", "description": "原 Session 修订并重跑 Task；保留 Workspace；可覆盖模型/Effort/工具。",
     "inputSchema": object_schema({"run_id": STRING, "request_id": STRING, "expected_version": {"type": "integer"},
                                  "task_id": STRING, "input": STRING, "execution": {"type": "object"}, "recovery": RECOVERY},
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


def call(client, name, args):
    args = dict(args)
    if name in {"octopus_workflows", "octopus_teams", "octopus_runs"}:
        return client.request("GET", "/v1/" + name.removeprefix("octopus_"))
    if name == "octopus_match_workflow":
        return client.request("POST", "/v1/workflows/resolve", args)
    if name == "octopus_parse_workflow":
        return client.request("POST", "/v1/workflows/parse", args)
    if name == "octopus_recommend_workflow":
        return client.request("POST", "/v1/designs", args)
    if name == "octopus_workflow_recommendation":
        return client.request("GET", "/v1/designs/" + args["id"])
    if name == "octopus_status":
        rid = args["run_id"]
        return {"session": client.request("GET", "/v1/runs/" + rid),
                "events": client.request("GET", "/v1/runs/" + rid + "/events")}
    if name == "octopus_start":
        return client.request("POST", "/v1/runs", args)
    if name in {"octopus_continue", "octopus_recover", "octopus_control"}:
        rid = args.pop("run_id")
        action = args.pop("action") if name == "octopus_control" else name.removeprefix("octopus_")
        if action not in {"continue", "recover", "stop", "close", "reopen", "delete"}:
            raise ValueError("Invalid action")
        return client.request("POST", "/v1/runs/" + rid + "/" + action, args)
    if name == "octopus_save_workflow":
        wid = args.pop("workflow_id", None)
        return client.request("POST", "/v1/workflows" + ("/" + wid + "/versions" if wid else ""), args)
    if name == "octopus_share_workflow":
        wid, revoke = args.pop("workflow_id"), args.pop("revoke", False)
        return client.request("DELETE" if revoke else "POST", "/v1/workflows/" + wid + "/shares", args)
    raise ValueError("Unknown tool")


def serve(client, input_stream=sys.stdin, output_stream=sys.stdout):
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
                result = {"tools": TOOLS}
            elif method == "tools/call":
                try:
                    value = call(client, request["params"]["name"], request["params"].get("arguments", {}))
                    result = {"content": [{"type": "text", "text": dumps(value)}],
                              "structuredContent": value if isinstance(value, dict) else {"items": value}}
                except (ValueError, KeyError, RuntimeError, OSError) as exc:
                    result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
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
