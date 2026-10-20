"""Editable workflow recommendations using the existing durable ACP run engine."""
import json

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


def status(service, actor, rid):
    with service.db.read() as db:
        run = run_access(db, actor, rid)
        snapshot = json.loads(run["snapshot"])
        require(snapshot.get("purpose") == "workflow_design", "工作流建议不存在", 404, "not_found")
        data = service._view(db, run, actor)
    task = data["tasks"][0]
    attempt = task["attempts"][-1] if task["attempts"] else {}
    result = {"id": rid, "version": data["version"], "input": snapshot["design"]["input"],
              "status": "ready_to_dispatch" if data["status"] == "ready" else data["status"],
              "native_parent": data["native_parent"], "worker_id": data["worker_id"],
              "evidence": attempt.get("evidence", {}), "native_task_id": attempt.get("native_id"),
              "error": data["error"] or attempt.get("error"), "raw_result": attempt.get("result") or ""}
    if task["status"] == "accepted" and data["desired"] == "active":
        try:
            result.update(config=parse_recommendation(result["raw_result"]), status="ready")
        except (Error, ValueError, TypeError, KeyError) as exc:
            result.update(status="invalid", error="KiroCrew 生成内容尚未通过工作流校验：" + str(exc))
    return result


def clip(text, limit):
    return text if len(text) <= limit else text[:limit] + "…"


def instructions(intent, design, directory, models, tools=None):
    from .planning import environment
    from .prompts import intent_prompt
    example = {"name": "针对本次需求的工作流", "steps": [
        {"id": "task-1", "name": "任务名称", "tool": "kiro", "model": "auto", "effort": "",
         "prompt": "本任务的具体目标、输入、实施要求、交付物与验证方法"}]}
    return intent_prompt(dumps(example), intent, directory, role="KiroCrew 规划者（工作流设计）") + """
设计 1–8 个顺序任务。

## 当前模板 / 用户草稿基线
下面的数据用于设计参考。针对本次业务需求重新编写各 Task 的提示词，
不要只返回“完成编码”“完成审查”等空泛占位语句。
明确保留用户选择的工具、具体模型与 Effort；model=auto 不意味着需要猜测具体模型。
如果需要调整任务顺序或增加任务，在推荐中体现，用户会在采用前审阅。
""" + dumps(design) + """

""" + environment(models, tools, None, None) + """

这里只交付工作流设计，不运行推荐工作流、不提交或发布业务代码。
最终回复必须包含且仅包含一个完整的 JSON 工作流代码块；不要加入第二个 JSON/YAML 代码块。
同时按本任务要求写交接文件，说明设计依据和待用户核实的条件。
"""
