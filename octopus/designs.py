"""Editable workflow recommendations using the existing durable ACP run engine."""
import json
import re

from .db import dumps
from .domain import name, run_access, steps
from .security import Error, require
from .transport import worker_request


def create(service, actor, body):
    require(body.get("worker_id"), "请先选择可用的生成 Worker", 400, "worker_required")
    preview = service.resolve_workflow(actor, body)
    with service.db.read() as db:
        worker = service._worker(db, actor["id"], body["worker_id"], body.get("team_id") or None)
    connection = json.loads(worker["config"])
    health = worker_request(connection, "/health")
    require(health.get("boundary") == connection["capabilities"]["boundary"],
            "Worker 执行边界发生变化，请重新核查", 409, "worker_changed")
    require(health.get("workflow_design") is True,
            "此执行环境的 Worker 版本尚不支持工作流建议：请平台管理员更新它", 409, "worker_upgrade_required")
    spec = [{"id": "design", "name": "推荐工作流与 Agent 提示词", "tool": "kiro", "model": "auto", "effort": "",
             "prompt": "根据完整用户需求与当前模板/任务基线，生成可编辑的工作流 JSON。"
                       "为每个 Task 写具体输入、实施步骤、交付物与验证方式；只设计工作流，不执行其中的开发任务。"}]
    design = {"input": preview["input"], "basis_name": preview["workflow_name"], "basis": preview["steps"]}
    return service.start(actor, {"request_id": body.get("request_id"), "input": preview["input"],
                                "name": "工作流建议 · " + preview["name"][:60], "steps": spec,
                                "worker_id": body["worker_id"], "source": {"type": "new"},
                                **({"team_id": body["team_id"]} if body.get("team_id") else {})}, design=design)


def parse_recommendation(content):
    # Reuse workflow_library.design_status's parser, without its global Gateway
    # identity or file-backed generation state.
    from .documents import parse_document
    value = parse_document(content.strip(), "json" if content.strip().startswith("{") else "markdown")
    require(isinstance(value, dict) and set(value) <= {"schema", "id", "revision", "name", "steps"},
            "生成的工作流字段无效")
    return {"name": name(value.get("name")), "steps": steps(value.get("steps"))}


TOOLS_SHOWN = "kiro、claude、codex"   # OpenCode stays off (K01): the validator knows it, the prompt does not offer it
BLOCK = re.compile(r"^(`{3,})(yaml|yml|json|workflow)\s*\n(.*?)^\1\s*$", re.M | re.S)


def explain(content, exc):
    """What a person (and KiroCrew, asked to fix it) reads about a recommendation that is not a workflow (D66): what was
    wrong, in plain words — the count, the tool, the id — rather than the validator's message."""
    from .domain import MAX_TASKS, TOOLS
    message = str(exc)
    blocks = BLOCK.findall(content or "")
    if not blocks and not (content or "").strip().startswith("{"):
        return "KiroCrew 的回答里没有可解析的工作流 JSON"
    if len(blocks) > 1:
        return f"KiroCrew 的回答里有 {len(blocks)} 个工作流代码块，平台只接受一个"
    try:
        from .documents import parse_document
        value = parse_document((content or "").strip(), "json" if (content or "").strip().startswith("{") else "markdown")
    except (ValueError, TypeError) as error:
        return "KiroCrew 给出的工作流 JSON 无法解析：" + str(error)[:200]
    if not isinstance(value, dict):
        return "KiroCrew 给出的工作流不是一个 JSON 对象"
    extra = sorted(set(value) - {"schema", "id", "revision", "name", "steps"})
    if extra:
        return "工作流顶层只能有 name 与 steps，KiroCrew 还写了：" + "、".join(extra[:5])
    tasks = value.get("steps")
    if not isinstance(tasks, list) or not tasks:
        return "KiroCrew 推荐的工作流没有任务"
    if len(tasks) > MAX_TASKS:
        return f"KiroCrew 推荐了 {len(tasks)} 个任务，超过平台上限 {MAX_TASKS} 个"
    items = [t for t in tasks if isinstance(t, dict)]
    if "Coding 工具" in message:
        bad = [str(t.get("tool")) for t in items if t.get("tool") not in TOOLS]
        return f"有任务使用了平台不支持的工具 {'、'.join(bad[:3])}（可用：{TOOLS_SHOWN}）"
    if "Task ID" in message:
        seen, bad = set(), []
        for t in items:
            sid = t.get("id")
            if not (isinstance(sid, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,47}", sid)) or sid in seen:
                bad.append(str(sid))
            seen.add(sid)
        return f"任务 id「{'、'.join(bad[:3])}」不合规或重复（须以小写字母开头，只含小写字母、数字和短横线，至多 48 个字符）"
    if "任务类别只能是" in message:
        from .domain import KINDS
        bad = [str(t.get("kind")) for t in items if "kind" in t and t.get("kind") not in KINDS]
        return f"有任务的类别（kind）写成了「{'、'.join(bad[:3])}」（可选：{'、'.join(KINDS)}，或不写）"
    if "不支持的 Task 字段" in message:
        from .domain import CONTRACT
        allowed = {"id", "name", "prompt", "tool", "model", "effort", "requested", *CONTRACT}
        fields = sorted({k for t in items for k in t} - allowed)
        return "有任务带了平台不认识的字段：" + "、".join(fields[:5])
    return message   # the rest already read plainly (审查不独立…, 名称 须为…, depends_on 只能指向前序任务…)


def status(service, actor, rid):
    with service.db.read() as db:
        run = run_access(db, actor, rid, read=True)
        snapshot = json.loads(run["snapshot"])
        require(snapshot.get("purpose") == "workflow_design", "工作流建议不存在", 404, "not_found")
        data = service._view(db, run, actor)
    task = data["tasks"][0]
    attempt = task["attempts"][-1] if task["attempts"] else {}
    result = {"id": rid, "version": data["version"], "input": snapshot["design"]["input"],
              "status": "ready_to_dispatch" if data["status"] == "ready" else data["status"],
              "native_parent": data["native_parent"], "worker_id": data["worker_id"],
              "evidence": attempt.get("evidence", {}), "native_task_id": attempt.get("native_id"),
              "error": data["error"] or attempt.get("error"), "raw_result": attempt.get("result") or "",
              # D66: the last recommendation was not a workflow; KiroCrew is fixing it now
              "repairing": bool((attempt.get("evidence") or {}).get("design_repair")) and task["status"] != "accepted"}
    if task["status"] == "accepted" and data["desired"] == "active":
        try:
            result.update(config=parse_recommendation(result["raw_result"]), status="ready")
        except (Error, ValueError, TypeError, KeyError) as exc:   # D66: what was wrong and what to do, in plain words
            repaired = bool((attempt.get("evidence") or {}).get("design_repair"))
            result.update(status="invalid", error="KiroCrew 的推荐未通过平台校验：" + explain(result["raw_result"], exc) + "。"
                          + ("已自动请 KiroCrew 修正过一次。" if repaired else "") + "可以重新推荐，或「导入并修改」后采用。")
    return result


def clip(text, limit):
    return text if len(text) <= limit else text[:limit] + "…"


def instructions(intent, design, directory, models, tools=None):
    from .planning import environment
    from .prompts import intent_prompt
    example = {"name": "针对本次需求的工作流", "steps": [
        {"id": "task-1", "name": "任务名称", "tool": "kiro", "model": "auto", "effort": "",
         "prompt": "本任务的具体目标、输入、实施要求、交付物与验证方法"}]}
    from .domain import KINDS, MAX_TASKS
    repair = (design or {}).get("repair")
    basis = {k: v for k, v in (design or {}).items() if k != "repair"}
    return intent_prompt(dumps(example), intent, directory, role="KiroCrew 规划者（工作流设计）") + f"""
## 输入
- 用户需求：见上文「用户意图」。
- 当前模板 / 用户草稿基线：见下文 JSON（basis 是任务基线，input 是需求原文）。
- 执行环境的工具与模型：见下文。

## 输出约束（平台逐条校验；任一不符，方案就无法采用）
1. 最终回复只输出一个 ```json 代码块，内容为 {{"name": "…", "steps": [ … ]}}；顶层只有 name 与 steps；不要再加第二个 JSON / YAML 代码块。
2. steps 为 1–{MAX_TASKS} 个顺序任务；超过 {MAX_TASKS} 个时合并相邻的步骤，不要拆成两个工作流。
3. 每个任务必须有 id、name、tool、model、effort、prompt；可选 kind、role、acceptance、deliverables、verify、paths、depends_on、skills，不要其他字段。
4. id：小写字母开头，只含小写字母、数字和短横线，至多 48 个字符，互不重复。
5. name 至多 80 个字符；prompt 至多 8000 个字符。
6. tool 只能是 {TOOLS_SHOWN}（OpenCode 已停用）；model 写 auto 或下方公布的模型；effort 为空或 low / medium / high / xhigh / max。
7. depends_on 只能指向前面的任务；kind 只能是 {"、".join(KINDS)}（可以不写）。
8. 审查任务的工具与被审查改动的作者不同（例如 claude 实现、codex 审查）。

## 当前模板 / 用户草稿基线
下面的数据用于设计参考。针对本次业务需求重新编写各 Task 的提示词，
不要只返回“完成编码”“完成审查”等空泛占位语句。
明确保留用户选择的工具、具体模型与 Effort；model=auto 不意味着需要猜测具体模型。
如果需要调整任务顺序或增加任务，在推荐中体现，用户会在采用前审阅。
""" + dumps(basis) + """

""" + environment(models, tools, None, None) + """

这里只交付工作流设计，不运行推荐工作流、不提交或发布业务代码。
最终回复必须包含且仅包含一个完整的 JSON 工作流代码块；不要加入第二个 JSON/YAML 代码块。
同时按本任务要求写交接文件，说明设计依据和待用户核实的条件。
""" + (f"""
## 上一版未通过平台校验
原因：{repair.get("error")}
请按上面的「输出约束」修正，给出完整的新工作流 JSON（不要只说明如何修改）。
""" if isinstance(repair, dict) and repair.get("error") else "")
