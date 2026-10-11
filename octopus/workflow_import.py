"""Workflows generated ahead of time by an Agent, parsed and normalized by code (no model call).

Accepted shapes: the console export ``{name, steps}``, the legacy Workflow Studio document
``{schema: 1, id, name, revision, steps}`` and a bare list of steps; as JSON, YAML or Markdown with exactly
one code block. Normalization is deterministic and reported in ``notes``; ``domain.steps`` has the final say.
"""
import re

from .documents import parse_document
from .domain import TOOLS, steps
from .security import Error, require

DOCUMENT_KEYS = {"name", "steps", "schema", "id", "revision", "description", "intent", "summary"}
FIELD_ALIASES = {"tool": ("tool", "agent", "backend"), "prompt": ("prompt", "instructions", "description", "task"),
                 "name": ("name", "title")}
TOOL_ALIASES = {"kiro": "kiro", "kirocli": "kiro", "claude": "claude", "claudecode": "claude",
                "codex": "codex", "codexcli": "codex", "opencode": "opencode"}


def detect(content):
    stripped = content.lstrip()
    if re.search(r"^`{3,}", content, re.M):
        return "markdown"
    return "json" if stripped[:1] in "{[" else "yaml"


def tool_of(value):
    if not isinstance(value, str):
        return value
    key = re.sub(r"[\s_.-]", "", value.lower())
    return TOOL_ALIASES.get(key, value)


def step_id(value, index, seen):
    candidate = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") if isinstance(value, str) else ""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", candidate) or candidate in seen:
        candidate = f"task-{index}"
    while candidate in seen:
        candidate += "-x"
    seen.add(candidate)
    return candidate


def normalize(raw, notes):
    require(isinstance(raw, list), "工作流需要 steps 任务列表")
    require(1 <= len(raw) <= 8, "流程须包含 1–8 个顺序任务")
    seen, result = set(), []
    for index, item in enumerate(raw, 1):
        require(isinstance(item, dict), f"第 {index} 个任务不是对象")
        used = {"id", "model", "effort"} | {key for keys in FIELD_ALIASES.values() for key in keys}
        picked = {field: next((item[key] for key in keys if item.get(key) not in (None, "")), None)
                  for field, keys in FIELD_ALIASES.items()}
        ignored = sorted(set(item) - used)
        if ignored:
            notes.append(f"任务 {index}：忽略不支持的字段 {', '.join(ignored)}")
        tool = tool_of(picked["tool"])
        require(tool in TOOLS, f"任务 {index}：不支持的 Coding 工具 {picked['tool']!r}（可用 kiro / claude / codex / opencode）")
        require(isinstance(picked["prompt"], str) and picked["prompt"].strip(), f"任务 {index}：缺少任务说明（prompt）")
        effort = item.get("effort") or ""
        if tool == "opencode" and effort:
            notes.append(f"任务 {index}：OpenCode 接入不支持 Effort，已忽略 {effort!r}")
            effort = ""
        result.append({"id": step_id(item.get("id"), index, seen), "name": picked["name"] or f"任务 {index}",
                       "tool": tool, "model": item.get("model") or "auto", "effort": effort,
                       "prompt": picked["prompt"]})
    return result


def import_workflow(content, format=None):
    """Parse a workflow document into ``{"name", "steps", "notes"}``; raises ``Error`` (400) with the reason."""
    require(isinstance(content, str) and content.strip(), "工作流内容应为非空文本")
    require(format in (None, "", "json", "yaml", "yml", "md", "markdown"), "格式只支持 json、yaml 或 markdown")
    try:
        document = parse_document(content, format or detect(content))
    except ValueError as exc:
        raise Error(400, "invalid_request", str(exc)) from None
    notes = []
    if isinstance(document, dict):
        require("steps" in document, "工作流文档缺少 steps 任务列表")
        ignored = sorted(set(document) - DOCUMENT_KEYS)
        if ignored:
            notes.append("忽略不支持的字段 " + ", ".join(ignored))
        title, raw = document.get("name"), document["steps"]
    else:
        title, raw = None, document
    normalized = steps(normalize(raw, notes))
    name = title if isinstance(title, str) and title.strip() else "导入的工作流"
    result = {"name": " ".join(name.split())[:80], "steps": normalized, "notes": notes}
    if isinstance(document, dict):
        result.update({key: document[key] for key in ("intent", "summary") if isinstance(document.get(key), str)})
    return result
