"""Configurable workflows on KiroCrew's existing App -> spawn_run path.

This module versions configuration, prepares App input and projects native
evidence. It never starts a Coding CLI or calls a spawn endpoint itself.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import shlex
import threading
import time
import unicodedata
from urllib.parse import parse_qs, urlsplit, unquote
import uuid

import api
import development_prompts as development
import workflow_workspace as workspace
import workflow_library as library
from workflow_files import parse_document
from pipeline import read_json, write_json, route_for, task_identity, require

ROOT = Path(__file__).resolve().parent
DEFINITIONS = ROOT / "workflows"
VERSIONS = ROOT / "state/workflow-versions"
RUNS = ROOT / "state/runs"
PORT = 8917
TOOLS = {
    "kiro": {"label": "Kiro CLI", "agent": "poc-kiro", "backend": "kiro",
             "efforts": ["", "low", "medium", "high", "xhigh", "max"]},
    "claude": {"label": "Claude Code", "agent": "poc-claude", "backend": "claude",
               "efforts": ["", "low", "medium", "high", "xhigh", "max"]},
    "codex": {"label": "Codex", "agent": "poc-codex", "backend": "codex",
              "efforts": ["", "low", "medium", "high", "xhigh", "max"]},
    "opencode": {"label": "OpenCode", "agent": "poc-opencode", "backend": "opencode",
                 "efforts": [""]},
}
MODEL_CACHE = Path.home() / ".kiro/crew/provider_models.json"


def advertised_models(tool):
    namespace = {"kiro": "acp", "claude": "claude_code"}.get(tool, tool)
    values = read_json(MODEL_CACHE, {}).get(namespace, [])
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v))


def validate_model_selection(steps):
    """An explicit Claude pick must be in that adapter's own published list.

    Crew treats startup pins as inherited and may fall back after rejection.
    A Workflow pick is explicit: refuse it before creating a run / dispatch.
    Saved portable definitions and old run snapshots remain untouched.
    """
    candidates = None
    for step in steps:
        if step["tool"] != "claude" or step["model"] in {"auto", "default"}:
            continue
        if candidates is None:
            candidates = advertised_models("claude")
        require(bool(candidates),
                "还没有 Claude Code 的原生模型候选。请先在 KiroCrew 使用 Claude Code，"
                "再刷新模型候选；或明确选择 auto。")
        require(step["model"] in candidates,
                f"任务「{step['name']}」的模型 {step['model']} 不在 Claude Code 当前公布的候选列表。"
                "请选择该工具列出的模型，或明确选择 auto；本工作流不会静默改用其他模型。")


def app_entry(run):
    manifest = load_run(run)
    return {"url": api.chat_entry_url(manifest["slot"]), "expires_in": 300}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def ident(value):
    require(isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,47}", value)),
            "ID 必须以小写字母开头，最多 48 位小写字母、数字或短横线。")
    return value


def text(value, label, minimum=1, maximum=4000):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum
            and "\x00" not in value, f"{label} 长度应为 {minimum}–{maximum} 字符。")
    return value.strip()


def workflow_name(value):
    value = text(value, "Workflow 名称", maximum=80)
    require(not any(unicodedata.category(c) in {"Cc", "Cf"} and not c.isspace()
                    for c in value), "工作流名称不能包含不可见控制字符。")
    return text(" ".join(unicodedata.normalize("NFKC", value).split()),
                "Workflow 名称", maximum=80)


def name_key(value):
    return workflow_name(value).casefold()


def assert_unique_name(name, workflow_id):
    """Call under VERSIONS/.lock when saving/restoring; deleted names stay reserved."""
    key = name_key(name)
    for definition in library.library()["definitions"]:
        require(definition["id"] == workflow_id or name_key(definition["name"]) != key,
                f"工作流名称「{name}」已被 {definition['id']} 使用（含已删除工作流）。"
                "请换一个名称，或编辑已有工作流。")


CONTRACT_FIELDS = {"kind", "role", "deliverables", "acceptance", "verify", "depends_on", "paths", "requested"}


def validate(value):
    require(isinstance(value, dict), "Workflow 必须是 JSON 对象。")
    require(set(value) <= {"schema", "id", "name", "revision", "steps"},
            "存在不支持的顶层字段；首版只支持顺序步骤。")
    require(value.get("schema") == 1, "schema 必须为 1。")
    steps = value.get("steps")
    require(isinstance(steps, list) and 1 <= len(steps) <= 8, "首版支持 1–8 个顺序步骤。")
    normalized = []
    for step in steps:
        # The platform's templates also carry a task contract (kind, acceptance …, D46); this runtime does not use it.
        step = {k: v for k, v in step.items() if k not in CONTRACT_FIELDS} if isinstance(step, dict) else step
        require(isinstance(step, dict) and set(step) <= {
            "id", "name", "tool", "model", "effort", "prompt"
        }, "步骤字段仅支持 id/name/tool/model/effort/prompt。")
        tool = step.get("tool")
        require(tool in TOOLS, "可用工具：kiro、claude、codex、opencode。")
        model = text(step.get("model", "auto"), "Model", maximum=200)
        require(not re.search(r"[\s\[\]]", model),
                "请填写原生模型 ID；Effort 单独设置，不写进 Model。")
        effort = step.get("effort", "")
        require(effort in TOOLS[tool]["efforts"],
                "此工具不支持该 Effort；当前 OpenCode 路径仅支持后端默认。")
        require(not effort or model not in {"auto", "default"},
                "设置 Effort 时必须选择具体模型，不能使用 auto/default。")
        # The native factory only propagates effort for models it recognizes.
        require(not effort or ("haiku" not in model.lower() and
                               any(word in model.lower() for word in ("opus", "sonnet", "fable", "gpt"))),
                "当前集成无法确认该模型的 Effort 支持；请使用后端默认。")
        normalized.append({
            "id": ident(step.get("id")), "name": text(step.get("name"), "步骤名称", maximum=80),
            "tool": tool, "model": model, "effort": effort,
            "prompt": text(step.get("prompt"), "步骤任务", maximum=4000),
        })
    require(len({s["id"] for s in normalized}) == len(normalized), "步骤 ID 不可重复。")
    return {"schema": 1, "id": ident(value.get("id")),
            "name": workflow_name(value.get("name")), "steps": normalized}


def latest(workflow_id):
    path = VERSIONS / ident(workflow_id)
    revisions = sorted(path.glob("*.json"))
    require(bool(revisions), "Workflow 尚未保存；先执行 save 或在 Web 中保存。")
    return read_json(revisions[-1])


def runtime_features():
    return read_json(ROOT / "state/gateway-runtime.json", {}).get("composition_features", [])


def require_runtime(definition):
    if any(s["tool"] == "codex" and s["effort"] for s in definition["steps"]):
        require("codex_effort_pair_v1" in runtime_features(),
                "当前 Gateway 尚未加载 Codex Effort 桥接修正。请等待空闲后重载 PoC Gateway。")


def save(value, expected_revision):
    """One writer across Web, Chat shell and file import; immutable revisions."""
    normalized = validate(value)
    require(isinstance(expected_revision, int) and not isinstance(expected_revision, bool)
            and expected_revision >= 0, "必须提交 expected_revision。")
    DEFINITIONS.mkdir(parents=True, exist_ok=True)
    VERSIONS.mkdir(parents=True, exist_ok=True)
    with (VERSIONS / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        library.assert_active(normalized["id"])
        assert_unique_name(normalized["name"], normalized["id"])
        directory = VERSIONS / normalized["id"]
        directory.mkdir(exist_ok=True)
        files = sorted(directory.glob("*.json"))
        previous = read_json(files[-1]) if files else None
        current_revision = previous["revision"] if previous else 0
        require(expected_revision == current_revision,
                f"版本冲突：当前为 r{current_revision}，请重新加载后合并修改。")
        if previous and validate(previous) == normalized:
            return previous
        result = {**normalized, "revision": current_revision + 1}
        write_json(directory / f"{result['revision']:06d}.json", result)
        write_json(DEFINITIONS / f"{normalized['id']}.json", result)
        return result


def change(workflow_id, step_id, expected_revision, **fields):
    value = latest(workflow_id)
    require(value["revision"] == expected_revision, "版本冲突；先读取当前版本。")
    step = next((s for s in value["steps"] if s["id"] == step_id), None)
    require(step is not None, "步骤不存在。")
    if fields.get("tool") is not None and fields["tool"] != step["tool"]:
        step.update(model="auto", effort="")
    step.update({k: v for k, v in fields.items() if v is not None})
    return save(value, expected_revision)


def run_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = RUNS / str(value)
    path = path.resolve()
    require(path.parent == RUNS.resolve() and path.name.startswith("workflow-"),
            "运行必须是本 PoC 的 workflow-* 目录。")
    require((path / "workflow-run.json").is_file(), "Workflow 运行不存在。")
    return path


def load_run(run):
    manifest = read_json(run / "workflow-run.json")
    require(fingerprint(manifest["workflow"]) == manifest["workflow_sha256"],
            "运行快照已被修改；停止执行。")
    return manifest


@contextmanager
def run_lock(run):
    """Serialize retry, checkpoint and observer publication across processes."""
    with (run / ".workflow.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def retry_state(run):
    return read_json(run / "retry-state.json", {"stages": {}, "requests": {}})


def current_receipt(receipt, retry):
    return receipt if receipt and receipt["task_id"] not in retry.get("excluded_task_ids", []) else None


def retry_stage(run, stage, request_id, *, update=None):
    """Invalidate evidence only; the native App coordinator owns dispatch."""
    ident(stage)
    require(isinstance(request_id, str) and bool(re.fullmatch(r"[a-zA-Z0-9-]{8,80}", request_id)),
            "缺少请求 ID。")
    if update is not None:
        require(isinstance(update, dict) and set(update) == {"requirement", "task_prompt"},
                "续作只接受本次需求和所选任务说明。")
        update = {"requirement": text(update["requirement"], "续作需求", minimum=3, maximum=12000),
                  "task_prompt": text(update["task_prompt"], "任务说明", minimum=3, maximum=4000)}
    with run_lock(run):
        import workflow_sessions as sessions
        sessions.require_open(run, continuing=update is not None)
        manifest = load_run(run)
        journal = retry_state(run)
        previous = journal["requests"].get(request_id)
        if previous:
            require(previous["stage"] == stage and previous.get("update_hash") == (
                fingerprint(update) if update is not None else None), "请求 ID 冲突。")
            return previous
        steps = manifest["workflow"]["steps"]
        index = next((i for i, s in enumerate(steps) if s["id"] == stage), None)
        require(index is not None, "步骤不存在。")
        snapshot = read_json(run / "workflow-live.json", {})
        require(snapshot.get("connected") and 0 <= time.time() - snapshot.get("at", 0) < 20,
                "宿主观察器未连接或已过期；刷新状态后重试。")
        rows = {s["id"]: s for s in snapshot.get("steps", [])}
        require(set(rows) == {s["id"] for s in steps}, "阶段状态不完整，请刷新。")
        for item in rows.values():
            task = item.get("task") or {}
            # ID-only entries can reuse completion evidence only from that task.
            attempts = [{**task, **attempt} if attempt.get("id") == task.get("id") else attempt
                        for attempt in item.get("attempts", [])]
            require(all(attempt.get("done") is True for attempt in ([task] if task else []) + attempts),
                    "原生任务尚未成功结束，请等待全部执行尝试终结后重试。")
        blocked = {"running", "submitted", "awaiting_approval"} | (set() if update is not None else {"awaiting_check"})
        require(not snapshot.get("parent_running") and not snapshot.get("approvals")
                and not any(s["status"] in blocked
                            for s in rows.values()),
                "Session 仍有在途任务或待处理授权/核对，请等待结束后重试。")
        row = rows[stage]
        allowed = {"failed", "check_failed", "accepted"} | (
            {"pending", "awaiting_check"} if update is not None else set())
        require(row["status"] in allowed, "当前阶段不可重试。")
        require(all(rows[s["id"]]["status"] == "accepted" for s in steps[:index]),
                "请先完成上游阶段的重试与核对。")
        receipts = dispatch_receipts(manifest)
        for step in steps:
            sid = step["id"]
            receipt = current_receipt(receipts.get(sid), journal["stages"].get(sid, {}))
            observed = rows[sid].get("native_request") or {}
            require(not receipt or receipt["task_id"] == observed.get("task_id"),
                    "原生派发已更新，请刷新后重试。")
        old_retry = journal["stages"].get(stage, {})
        require(not old_retry or snapshot["at"] >= old_retry["requested_at"], "重试已登记，请刷新状态。")
        require_runtime(manifest["workflow"])
        validate_model_selection([steps[index]])
        expected_cwd = (str(workspace.project_dir(run))
                        if manifest.get("development_contract", 1) >= 2 else None)
        invalidated = []
        requested_at = time.time()
        if update is not None:
            # Immutable run/input stay intact. This revision is activated by
            # the same atomic retry barrier as candidate invalidation below.
            revision = journal.get("input", {}).get("revision", 0) + 1
            journal["input"] = {"text": update["requirement"], "sha256": fingerprint(update["requirement"]),
                                "revision": revision, "request_id": request_id, "at": requested_at}
            journal.setdefault("task_prompts", {})[stage] = update["task_prompt"]
            (run / "continuations").mkdir(exist_ok=True)
            write_json(run / "continuations" / (request_id + ".json"),
                       {"stage": stage, "revision": revision, "at": requested_at, **update})
        archive = run / "accepted/previous"
        archive.mkdir(exist_ok=True)
        for step in steps[index:]:
            sid = step["id"]
            item = rows[sid]
            accepted_path = run / "accepted" / (sid + ".json")
            accepted = read_json(accepted_path)
            receipt = item.get("native_request") or {}
            task_id = (item.get("task") or {}).get("id") or receipt.get("task_id")
            excluded = set(journal["stages"].get(sid, {}).get("excluded_task_ids", []))
            excluded.update(t["id"] for t in item.get("attempts", []))
            excluded.update(v for v in [task_id, (accepted or {}).get("task_id"),
                                       (receipts.get(sid) or {}).get("task_id")] if v)
            conversation = receipt.get("conversation") or receipt.get("origin_task_id") or receipt.get("task_id")
            can_continue = (bool(conversation) and bool(re.fullmatch(r"[a-f0-9]{8,}", conversation))
                            and receipt.get("agent") == TOOLS[step["tool"]]["agent"]
                            and receipt.get("model") == step["model"]
                            and receipt.get("reasoning_effort", "") == step["effort"]
                            and (item.get("route") or {}).get("actual_backend") == TOOLS[step["tool"]]["backend"]
                            and (expected_cwd is None or (
                                receipt.get("cwd") == expected_cwd
                                and (item.get("route") or {}).get("cwd", expected_cwd) == expected_cwd)))
            journal["stages"][sid] = {
                "stage": sid, "source_stage": stage, "request_id": request_id,
                "requested_at": requested_at, "previous_task_id": task_id,
                "excluded_task_ids": sorted(excluded),
                "mode": "spawn_continue" if can_continue else "spawn_run",
                "conversation": conversation if can_continue else None,
            }
            if accepted:
                write_json(archive / (sid + "-" + request_id + ".json"), accepted)
                if sid != stage:
                    invalidated.append(sid)
            # Preserve prior deliverables, but require the worker to write a
            # handoff for this attempt before the existing checkpoint can pass.
            history = run / "retry-history" / request_id / sid
            history.mkdir(parents=True, exist_ok=True)
            for suffix in ("-handoff.md", "-result.txt"):
                source = run / "workspace" / (sid + suffix)
                if source.is_file():
                    (history / source.name).write_bytes(source.read_bytes())
        command = "python3 " + shlex.quote(str(ROOT / "workflow.py"))
        hint = (
            "重试已登记。请在此 Session 的 KiroCrew 对话中继续：执行 " + command +
            " next " + shlex.quote(str(run)) +
            "；wait=true 时等待现有原生任务，禁止重复派发。否则按 tool 字段调用原生 spawn_continue"
            "（参数 continue）或 spawn_run（参数 spawn）。仅当原生明确拒绝续接且确认未派发时，"
            "可用同一 spawn 参数重建；结果不明确时停止并核实。完成后用新 task-id 执行 " + command +
            " accept " + shlex.quote(str(run)) +
            " --task-id <新ID>；检查通过后才能 next。失败如实停止，不能跳过检查。"
        )
        result = {"ok": True, "run": manifest["id"], "stage": stage, "request_id": request_id,
                  "invalidated": invalidated, "next_hint": hint}
        if update is not None:
            result.update(update_hash=fingerprint(update), input_revision=revision)
        journal["requests"][request_id] = result
        # One atomic barrier makes every old task/receipt in the suffix stale,
        # even if the process stops before the obsolete files are removed.
        write_json(run / "retry-state.json", journal)
        for step in steps[index:]:
            sid = step["id"]
            (run / "accepted" / (sid + ".json")).unlink(missing_ok=True)
            for suffix in ("-handoff.md", "-result.txt"):
                (run / "workspace" / (sid + suffix)).unlink(missing_ok=True)
            rows[sid].update(status="pending", task=None, native_request=None, route=None,
                             accepted=None, check_error=None, requested_model="", resolved_model="",
                             model_mismatch=False, effort_observation={"value": None, "source": "not_reported"},
                             retry=journal["stages"][sid])
        snapshot.update(status="ready", steps=list(rows.values()), retry=result, at=requested_at)
        write_json(run / "workflow-live.json", snapshot)
        write_json(run / "workflow-report.json", snapshot)
        return result


def marker(manifest, step_id):
    return f"[WORKFLOW:{manifest['id']} STEP:{step_id}]"



def capture(run, requirement):
    requirement = text(requirement, "用户需求", minimum=3, maximum=12000)
    path = run / "input.json"
    if path.exists():
        require(read_json(path)["text"] == requirement, "本次需求已冻结；请新建运行。")
    else:
        write_json(path, {"text": requirement, "sha256": fingerprint(requirement)})
        (run / "workspace/REQUEST.md").write_text(requirement + "\n")
    return {"ok": True, "run": str(run), "input": requirement}


def control_prompt(manifest):
    command = f"python3 {shlex.quote(str(ROOT / 'workflow.py'))}"
    run = manifest["run"]
    qrun = shlex.quote(run)
    workflow = manifest["workflow"]
    return f"""本条 KiroCrew App 对话使用 Octopus 研发工作流契约 v{development.CONTRACT_VERSION}。
配置源 {DEFINITIONS / (workflow['id'] + '.json')}；本次固定 r{workflow['revision']}。
运行快照 {run}/workflow-run.json；交接目录 {run}/workspace；源码目录由 project.json 记录。
这里的配置优先决定工具和任务顺序，不要套用默认的固定四步骤。
你负责研发调度与项目准备，使用 KiroCrew 原生 spawn_run/spawn_continue；Coding 工具及权限由原生后端管理。
需求入口：
如果用户给出开发需求且 input.json 不存在，将原始需求写入 {run}/user-input.txt，
执行 {command} capture {qrun} --input-file {shlex.quote(run + '/user-input.txt')}。
如果已经存在 input.json，继续执行已冻结需求。
若用户在工作台提交续作修订，以 workflow.py next 返回的本次任务指令及 retry-state.json 中
的 input / task_prompts 为准；原 input.json 和 workflow-run.json 保持历史基线，不新建运行。
执行协议：
1. 运行 {command} next {qrun}。若 status=workspace_required，先准备项目：
   用户给出 Git 仓库或本地检出时，用原生 Shell 执行
   {command} workspace {qrun} --source <用户指定的仓库地址或本地 Git 路径> [--ref <用户指定版本>]。
   未指定 ref 时使用该仓库默认 HEAD 并报告实际 commit，不臆造分支。
   用户明确创建新项目且没有已有仓库时，执行 {command} workspace {qrun} --new。
   要修改已有工程但没有明确来源时，询问缺少的来源，不把空项目当作已有源码。
   准备失败时依据具体错误处理认证、网络、ref 或来源，不把源码缺失交给编码者。
   workspace 命令会保存来源与基线；可在明确项目目录按锁文件准备依赖及验证入口。
   完成后重新 next。wait=true 时等待现有任务，不重复派发。
   tool=spawn_continue 时完整传递 continue 字段，续接原属主会话；否则传递 spawn 字段调用 spawn_run。
2. spawn_run 完整传递 task、agent、model、reasoning_effort、cwd 等字段，不省略或改写模型和 Effort。
   spawn_continue 只传递 continue 中的原生字段，工作目录、模型和 Effort 继承原属主会话。
3. 调用一次原生工具后结束 turn，等待 completion event。不要循环轮询。
4. 完成后运行 {command} accept {qrun} --task-id <真实返回的本次 task-id>。
   accept 根据宿主观察器的原生任务、真实后端和模型核对归属；失败必须报告并停止。
5. accept 成功后再次 next，直到 done=true。最后汇总真实 ID、产物、验证局限与看板地址。
不要宣称模型自身的文字回答能证明后端身份。不要把工具执行结束直接称为质量审查通过。
每一步独立子 Session，以相同项目检出、源码基线、原始需求和前序产物交接。不依赖长期 Memory 代替项目文件。
配置对话：
如果用户请求调整工作流，不启动开发任务。先运行 {command} show {workflow['id']}。
单步修改使用 {command} set {workflow['id']} --step <id> --expected-revision <当前版本>
加 --tool kiro|claude|codex|opencode、--model <原生ID>、--effort low|medium|high|xhigh|max（空串为默认）。
批量/增删/排序修改：把新 JSON 写到 {run}/workflow-draft.json，
再运行 {command} save {shlex.quote(run + '/workflow-draft.json')} --expected-revision <当前版本>。
保存成功后报告新版本，并明确只对下次运行生效。不得修改本次 workflow-run.json。
用户无需自己写 Plan.md 或 Tasks；这些是工作者根据需求产生的输出。
{development.LEGACY_COMMON}
主协调者先确保环境与输入可用，再按任务职责推进；需要新增 Agent 时按原生调度契约安排并保留记录。
权限请求保留在 KiroCrew App；遇到权限拒绝如实报告，不规避。
看板：http://127.0.0.1:{PORT}/?run={manifest['id']}
"""


def prepare(workflow_id, expected_revision):
    library.assert_active(workflow_id)
    definition = latest(workflow_id)
    require(definition["revision"] == expected_revision, "配置已更新；重新加载后再创建运行。")
    validate_model_selection(definition["steps"])
    run_id = time.strftime("workflow-%Y%m%d-%H%M%S-") + uuid.uuid4().hex
    run = RUNS / run_id
    (run / "workspace").mkdir(parents=True)
    (run / "accepted").mkdir()
    manifest = {"schema": 1, "id": run_id, "run": str(run), "slot": run_id,
                 "parent": f"dashboard:{run_id}", "created_at": time.time(),
                 "workflow": definition, "workflow_sha256": fingerprint(definition),
                 "development_contract": development.CONTRACT_VERSION}
    write_json(run / "workflow-run.json", manifest)
    slot = api.request("/api/chat/slots", {
        "name": run_id, "agent": "poc-coordinator", "agent_kind": "template", "model": "auto",
    })
    require(slot.get("key", slot.get("slot")) == run_id, "原生 App slot ID 不匹配。")
    prompt = control_prompt(manifest)
    (run / "app-context.txt").write_text(prompt)
    receipt = api.request(f"/api/chat/slots/{run_id}/context",
                          {"content": prompt, "source": "workflow-mvp", "ephemeral": True})
    require(receipt.get("ok"), "原生 App 未接受 Workflow 上下文。")
    write_json(run / "intake.json", {"status": "prepared", "receipt": receipt})
    return manifest


def submit(manifest, requirement):
    run = run_path(manifest["run"])
    require_runtime(manifest["workflow"])
    validate_model_selection(manifest["workflow"]["steps"])
    capture(run, requirement)
    current = read_json(run / "intake.json")
    require(current["status"] == "prepared", "本次已发送或结果待确认；请查看原生 App。")
    write_json(run / "intake.json", {"status": "sending", "at": time.time()})
    try:
        receipt = api.request("/api/chat?ws=1", {"slot": manifest["slot"], "message": requirement})
        require(receipt.get("ok"), "原生 App 未确认收到输入。")
        current = {"status": "accepted", "receipt": receipt}
    except Exception as exc:
        current = {"status": "uncertain", "error": str(exc),
                   "guidance": "打开 App 确认是否已发送；本服务不会自动重试。"}
    write_json(run / "intake.json", current)
    return current


def next_step(run):
    with run_lock(run):
        return _next_step(run)


def _next_step(run):
    import workflow_sessions as sessions
    sessions.require_open(run)
    manifest = load_run(run)
    require_runtime(manifest["workflow"])
    journal = retry_state(run)
    request = journal.get("input") or read_json(run / "input.json")
    require(request is not None and fingerprint(request["text"]) == request["sha256"],
             "请先登记用户输入。")
    project = workspace.read_record(run)
    if manifest.get("development_contract", 1) >= 2:
        if not project or project.get("status") != "ready":
            return {"done": False, "status": "workspace_required",
                    "project": project or {"status": "pending"},
                    "instruction": "先通过 workflow.py workspace 准备用户指定的仓库或明确的新项目，再调用 next。"}
        cwd = workspace.project_dir(run)
    else:
        # Existing runs retain their recorded directory and dispatch contract.
        cwd = run / "workspace"
    history = []
    receipts = dispatch_receipts(manifest)
    retries = journal["stages"]
    for step in manifest["workflow"]["steps"]:
        retry = retries.get(step["id"], {})
        completed = current_receipt(read_json(run / "accepted" / f"{step['id']}.json"), retry)
        latest_receipt = current_receipt(receipts.get(step["id"]), retry)
        if completed and latest_receipt and completed["task_id"] != latest_receipt["task_id"]:
            completed = None
        if completed:
            result_file = run / "workspace" / (step["id"] + "-result.txt")
            if not result_file.exists():
                result_file.write_text(completed["result"] + "\n")
            history.append(
                f"{step['name']} ({completed['task_id']}):\n"
                f"交接 {run / 'workspace' / (step['id'] + '-handoff.md')}\n"
                f"原生完整返回 {result_file}"
            )
            continue
        validate_model_selection([step])
        output = run / "workspace" / (step["id"] + "-handoff.md")
        instructions = (
            f"{marker(manifest, step['id'])}\n"
            f"用户本次需求（修订 {request.get('revision', 0)}）：\n{request['text']}\n\n"
            f"你的任务：{journal.get('task_prompts', {}).get(step['id'], step['prompt'])}\n"
            f"实际项目目录（cwd）：{cwd}。交接目录：{run / 'workspace'}。\n"
            f"源码基线记录：{run / 'project.json'}（旧运行可能没有此文件）。\n"
            f"{development.LEGACY_COMMON}\n{development.LEGACY_WORKER}\n"
            f"前序结果：\n{chr(10).join(history) if history else '无，这是第一步。'}\n"
            f"实际执行完成后，把交接说明写到 {output}，并在最终回答中报告文件和真实验证结果。"
            "交接写清实际源码版本、修改、测试命令与结果；尝试合理恢复后仍无法继续时明确写出 BLOCKED。"
        )
        if journal.get("input"):
            (run / "workspace/CONTINUATION.md").write_text(
                f"# 本次续作 · 修订 {request['revision']}\n\n{request['text']}\n")
            instructions += (
                "\n本次续作以此指令和 CONTINUATION.md 的新修订为准。REQUEST.md 保留首次输入，"
                "仅用于溯源，不得用旧需求覆盖本次修订。"
            )
        if retry:
            instructions += (
                "\n这是阶段重试。旧 Task 和旧交接已经失效，必须重新执行验证并写出本次交接。"
                f"旧产物保留在 {run / 'retry-history' / retry['request_id'] / step['id']}。"
                "执行以本次需求修订为准；若有阻断问题，如实报告。"
            )
        if step["tool"] == "opencode":
            instructions += (
                "\n本机兼容性：KiroCrew 当前 ACP Client 不处理 fs/write_text_file。"
                "请使用 OpenCode 已有的 Bash/Shell 工具，通过 Python 或 Node 在指定工作目录内读写文件。"
                "这是协议能力限制，不是权限授权；如果 Shell 收到权限拒绝，请停止并报告，不能绕过。"
            )
        instruction_file = run / "workspace" / (step["id"] + "-instructions.md")
        instruction_file.write_text(instructions + "\n")
        # Native SPAWN_RUN_SCHEMA caps task at 5,000 characters. The complete
        # specification and previous results travel as files, never truncation.
        prompt = (
            f"{marker(manifest, step['id'])}\n"
            f"这是用户自定义工作流中的 {step['name']} 步骤。\n"
            f"开始前完整阅读 {instruction_file} 与 {run / 'workspace/REQUEST.md'}。"
            "指令文件包含你的完整任务、前序交接及原生完整返回文件路径，必须依次读取相关文件。\n"
            f"用户需求摘要：{request['text'][:800]}\n"
            f"本步任务摘要：{journal.get('task_prompts', {}).get(step['id'], step['prompt'])[:800]}\n"
            f"项目 cwd={cwd}，交接目录={run / 'workspace'}。"
            "按任务授权准备依赖、开发、构建和验证；完整研发契约见指令文件。"
            f"实际完成后写交接文件 {output}，最终返回真实执行与验证结果。"
            "若确实阻塞，在单独一行写 BLOCKED 并说明原因。完整需求以文件为准。"
        )
        require(len(prompt) <= 5000, "工作目录路径过长，无法满足原生 task 长度限制。")
        # Emit native MCP field names, not a second orchestration protocol.
        spawn = {"task": prompt, "agent": TOOLS[step["tool"]]["agent"],
                 "model": step["model"], "reasoning_effort": step["effort"],
                  "cwd": str(cwd), "keep": True, "include_memory": False,
                 "solo_reason": "user_requested",
                 "solo_details": "用户明确要求按自定义 Workflow 逐步调用所选择的 Coding 工具、模型和 Effort。"}
        dispatch = {"done": False, "step": step["id"], "spawn": spawn,
                    "tool": retry.get("mode", "spawn_run")}
        if dispatch["tool"] == "spawn_continue":
            dispatch["continue"] = {"conversation": retry["conversation"], "task": prompt}
        if retry and latest_receipt:
            dispatch.update(wait=True, task_id=latest_receipt["task_id"])
        write_json(run / ("dispatch-" + step["id"] + ".json"),
                   {"step": step["id"], "workflow_sha256": manifest["workflow_sha256"],
                    **dispatch, "created_at": time.time()})
        return dispatch
    return {"done": True, "run": manifest["id"], "steps": len(history),
            "report": str(run / "workflow-report.json")}


def model_matches(requested, actual):
    if requested in {"auto", "default"}:
        return True
    return requested == re.sub(r"\[(low|medium|high|xhigh|max)\]$", "", actual or "")


def _accept(run, task_id):
    manifest = load_run(run)
    snapshot = read_json(run / "workflow-live.json", {})
    require(snapshot.get("connected") and 0 <= time.time() - snapshot.get("at", 0) < 20,
            "宿主观察器未连接或已过期；启动 workflow.py serve 后重试。")
    active = _next_step(run)
    require(not active["done"], "所有步骤已完成。")
    require(active.get("status") != "workspace_required", "源码准备尚未完成，不能接受开发交接。")
    row = next(s for s in snapshot["steps"] if s["id"] == active["step"])
    task = row.get("task") or {}
    retry = retry_state(run)["stages"].get(active["step"], {})
    require(task_id not in retry.get("excluded_task_ids", [])
            and snapshot["at"] >= retry.get("requested_at", 0)
            and active.get("task_id", task_id) == task_id,
            "旧候选已失效，必须使用重试后新的原生任务。")
    require(task.get("id") == task_id and task.get("parent") == manifest["parent"],
            "真实任务不属于当前步骤/父会话。")
    require(task.get("done") and not task.get("error") and not task.get("stopped"),
            "原生任务尚未成功结束。")
    require((task.get("result") or "").strip() not in {"", "_No response._"},
            "后端未返回完成结果；核对产物并在 App 中完成续作。")
    require(task.get("agent") == active["spawn"]["agent"], "原生 Agent 与配置不匹配。")
    require((row.get("route") or {}).get("actual_backend") == TOOLS[row["tool"]]["backend"],
            "真实 ACP 后端与配置不匹配。")
    require(row.get("requested_model") == active["spawn"]["model"], "原生 spawn 请求的模型与配置不一致。")
    native_request = row.get("native_request") or {}
    require(native_request.get("task_id") == task_id, "缺少与任务 ID 对应的原生 spawn 回执。")
    if manifest.get("development_contract", 1) >= 2:
        require(native_request.get("cwd") == active["spawn"]["cwd"],
                "原生任务未使用本次项目工作目录；停止交接并核查源码来源。")
        actual_cwd = (row.get("route") or {}).get("cwd")
        require(not actual_cwd or actual_cwd == active["spawn"]["cwd"],
                "实际后端工作目录与本次源码检出不一致。")
    if native_request.get("conversation"):
        require(row["route"].get("crew_session_key") == "subagent:" + native_request["conversation"],
                "续作未复用原生回执指定的 Conversation。")
    require(native_request.get("reasoning_effort", "") == active["spawn"]["reasoning_effort"],
            "原生 spawn 的 Effort 请求与配置不同。")
    require(model_matches(row["model"], row.get("resolved_model")),
            f"实际模型与配置不匹配：请求 {row['model']}，后端报告 {row.get('resolved_model') or '未报告'}。"
            "任务返回不代表参数核对通过；请从本工具的候选列表选择模型并创建下一版运行。")
    observed_effort = row.get("effort_observation", {}).get("value")
    require(not row["effort"] or not observed_effort or row["effort"] == observed_effort,
            "后端报告的 Effort 与配置不同；停止并查看 App。")
    require(not re.search(r"(?im)^\s*(?:[-*]\s*)?(?:状态[：:]\s*)?BLOCKED\b",
                          task.get("result", "")),
             "工作者报告 BLOCKED，请在 App 核对原因并完成恢复；保留当前运行与交接记录。")
    handoff = run / "workspace" / (row["id"] + "-handoff.md")
    require(handoff.is_file() and handoff.stat().st_size > 0, "工作者缺少交接文件。")
    require(not retry or handoff.stat().st_mtime >= retry["requested_at"],
            "交接文件属于旧候选，请重新写出本次交接。")
    receipt = {"step": row["id"], "task_id": task_id, "at": time.time(),
               "result": task.get("result", ""), "route": row["route"],
               "requested_model": row.get("requested_model"), "resolved_model": row.get("resolved_model"),
               "native_request": native_request,
               "requested_effort": row["effort"], "effort_observation": row["effort_observation"],
               "handoff_sha256": hashlib.sha256(handoff.read_bytes()).hexdigest(),
               "quality": "原生执行结束且交接文件存在；不是自动质量认证"}
    accepted_path = run / "accepted" / f"{row['id']}.json"
    previous = read_json(accepted_path)
    if previous and previous["task_id"] != task_id:
        (run / "accepted/previous").mkdir(exist_ok=True)
        write_json(run / "accepted/previous" / f"{row['id']}-{previous['task_id']}.json", previous)
    write_json(accepted_path, receipt)
    (run / "workspace" / (row["id"] + "-result.txt")).write_text(receipt["result"] + "\n")
    return {"ok": True, **receipt}


def accept(run, task_id):
    with run_lock(run):
        return _check_and_accept(run, task_id)


def _check_and_accept(run, task_id):
    require(bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", task_id)), "Invalid task ID")
    checks = run / "checks"
    checks.mkdir(exist_ok=True)
    try:
        result = _accept(run, task_id)
    except ValueError as exc:
        write_json(checks / f"{task_id}.json",
                   {"task_id": task_id, "passed": False, "error": str(exc), "at": time.time()})
        raise
    write_json(checks / f"{task_id}.json", {"task_id": task_id, "passed": True, "at": time.time()})
    return result


_CODEX_LOGS = {}


def codex_turn_evidence(session_id):
    """Only metadata from the exact native Codex session used by this task."""
    if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9-]{36}", session_id):
        return None
    path = _CODEX_LOGS.get(session_id)
    if path is None:
        candidates = list((Path.home() / ".codex/sessions").glob(f"**/*{session_id}.jsonl"))
        if len(candidates) != 1:
            return None
        path = candidates[0]
        _CODEX_LOGS[session_id] = path
    session_matches, turn = False, None
    try:
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get("payload", {})
            if event.get("type") == "session_meta":
                session_matches = payload.get("id") == session_id
            if event.get("type") == "turn_context" and session_matches:
                turn = {"value": payload.get("effort"), "model": payload.get("model"),
                        "source": "Codex native turn_context", "turn_id": payload.get("turn_id")}
    except OSError:
        return None
    return turn


def dispatch_receipts(manifest):
    """Read this App conversation's native tool events, including queued ACKs."""
    path = Path.home() / ".kiro/crew/sessions" / (manifest["parent"].replace(":", "_") + ".jsonl")
    receipts, by_task, seen = {}, {}, set()
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            event = json.loads(line)
            meta = event.get("meta", {})
            tool = meta.get("tool_name", "").split("/")[-1]
            if event.get("role") != "tool" or tool not in {"spawn_run", "spawn_continue"}:
                continue
            params = json.loads(meta.get("input", "{}"))
            output = meta.get("output", "")
            event_key = (meta.get("tool_call_id"), output)
            if not output or event_key in seen:
                continue
            seen.add(event_key)
            if tool == "spawn_continue":
                continued = re.search(r"Continued conversation ([a-f0-9]{8,}) as run ([a-f0-9]{8,})\.", output)
                if not continued or continued[1] != params.get("conversation"):
                    continue
                origin = by_task.get(continued[1])
                if origin is None:
                    continue
                step_id, original = origin
                receipt = {
                    **original, "task_id": continued[2],
                    "tool_call_id": meta.get("tool_call_id"),
                    "conversation": continued[1],
                    "origin_task_id": original.get("origin_task_id", original["task_id"]),
                    "agent": params.get("agent") or original["agent"],
                    "model": params.get("model") or original["model"],
                    "source": "native App continuation event; model/effort inherited unless overridden",
                }
                receipts[step_id] = receipt
                by_task[continued[2]] = (step_id, receipt)
                continue
            task_ids = re.findall(r"(?m)^\s*([a-f0-9]{8,}) \(", meta.get("output", ""))
            if not task_ids:
                continue
            for step in manifest["workflow"]["steps"]:
                if marker(manifest, step["id"]) in params.get("task", ""):
                    receipts[step["id"]] = {
                        "task_id": task_ids[0], "tool_call_id": meta.get("tool_call_id"),
                        "agent": params.get("agent"), "model": params.get("model", ""),
                        "reasoning_effort": params.get("reasoning_effort", ""),
                        "cwd": params.get("cwd"), "source": "native App tool event",
                    }
                    by_task[task_ids[0]] = (step["id"], receipts[step["id"]])
        except (ValueError, TypeError):
            continue
    return receipts


def project(run, tasks, parent, approvals):
    with run_lock(run):
        return _project(run, tasks, parent, approvals)


def _project(run, tasks, parent, approvals):
    import workflow_sessions as sessions
    manifest = load_run(run)
    receipts = dispatch_receipts(manifest)
    retries = retry_state(run)
    rows = []
    for step in manifest["workflow"]["steps"]:
        retry = retries["stages"].get(step["id"], {})
        receipt = current_receipt(receipts.get(step["id"]), retry)
        selected = [task_identity(t) for t in tasks
                    if t.get("parent") == manifest["parent"]
                    and (marker(manifest, step["id"]) in t.get("task", "")
                         or receipt and t.get("id") == receipt["task_id"])]
        eligible = [t for t in selected if t["id"] not in retry.get("excluded_task_ids", [])
                    and (not retry or receipt and t["id"] == receipt["task_id"])]
        task = max(eligible, key=lambda t: t.get("started", 0)) if eligible else None
        route = route_for(task) if task else None
        stored = read_json(Path.home() / f".kiro/crew/subagents/{task['id']}/state.json", {}) if task else {}
        actual = stored.get("resolved_model", "")
        effort_match = re.search(r"\[(low|medium|high|xhigh|max)\]$", actual)
        effort_observation = {
            "value": effort_match.group(1) if step["tool"] == "codex" and effort_match else None,
            "source": "native resolved_model" if step["tool"] == "codex" and effort_match else "not_reported",
        }
        if step["tool"] == "codex" and route:
            effort_observation = codex_turn_evidence(route.get("native_session_id")) or effort_observation
        accepted = current_receipt(read_json(run / "accepted" / f"{step['id']}.json"), retry)
        if accepted and receipt and accepted["task_id"] != receipt["task_id"]:
            accepted = None
        evidence_source = "native live task"
        if accepted and task is None:
            # The Gateway's in-memory completed-task list need not survive a
            # restart. Keep previously verified evidence, labelled as such.
            task = {"id": accepted["task_id"], "parent": manifest["parent"], "done": True,
                    "agent": TOOLS[step["tool"]]["agent"], "result": accepted["result"]}
            route = accepted["route"]
            actual = accepted.get("resolved_model", "")
            stored = {"requested_model": accepted.get("requested_model", "")}
            effort_observation = accepted["effort_observation"]
            receipt = accepted.get("native_request") or receipt
            evidence_source = "persisted verified checkpoint"
        check = read_json(run / "checks" / (task["id"] + ".json"), {}) if task else {}
        state = "accepted" if accepted else (
            "failed" if task and (task.get("error") or task.get("stopped")) else
            "check_failed" if check.get("passed") is False else
            "awaiting_check" if task and task.get("done") else "running" if task else
            "submitted" if receipt else "pending")
        rows.append({**step, "prompt": retries.get("task_prompts", {}).get(step["id"], step["prompt"]),
                     "task_uid": f"{manifest['id']}/{step['id']}",
                     "status": state, "task": task, "route": route, "retry": retry or None,
                     "native_request": receipt,
                     "evidence_source": evidence_source,
                     "check_error": check.get("error"),
                     "attempts": [{"id": t["id"], "done": t.get("done"), "error": t.get("error")}
                                  for t in selected],
                     "requested_model": stored.get("requested_model", ""),
                     "resolved_model": actual, "effort_observation": effort_observation,
                     "model_mismatch": bool(actual and not model_matches(step["model"], actual)),
                     "accepted": accepted})
    keys = {manifest["parent"]} | {f"subagent:{r['task']['id']}" for r in rows if r["task"]}
    own_approvals = [a for a in approvals
                     if a.get("session_key", a.get("session", "")) in keys
                     or a.get("slot") == manifest["slot"]]
    # ACP permissions in the App chat are not always included in the global
    # approvals endpoint. Use the native message's resolved flag, not text.
    if parent.get("running"):
        pending = {}
        for message in parent.get("messages", []):
            if message.get("role") != "permission":
                continue
            meta = message.get("meta", {})
            approval_id = meta.get("approval_id")
            if approval_id:
                pending[approval_id] = meta
        known = {a.get("id", a.get("approval_id")) for a in own_approvals}
        own_approvals += [
            {"id": key, "session_key": manifest["parent"],
             "title": meta.get("tool_title", "App 工具授权"),
             "source": "native App permission event"}
            for key, meta in pending.items() if not meta.get("resolved") and key not in known
        ]
    completed = all(r["status"] == "accepted" for r in rows)
    preview = next((name for name in ("project/web/index.html", "project/index.html", "web/index.html", "index.html")
                    if (run / "workspace" / name).is_file()), None)
    snapshot = {"manifest": manifest, "at": time.time(), "connected": True, "preview": preview,
            "project": workspace.read_record(run),
            "steps": rows, "parent_running": parent.get("running", False),
            "retry": next(reversed(retries["requests"].values()), None),
            "approvals": own_approvals, "input": retries.get("input") or read_json(run / "input.json", {}),
            "intake": read_json(run / "intake.json", {}),
            "status": "completed" if completed else "awaiting_approval" if own_approvals else
                      "failed" if any(r["status"] == "failed" for r in rows) else
                      "check_failed" if any(r["status"] == "check_failed" for r in rows) else
                      "running" if any(r["status"] in {"running", "submitted", "awaiting_check"}
                                       for r in rows) or parent.get("running") else "ready"}
    return sessions.overlay(run, snapshot)


def observe():
    gateway = api.ReadOnlyGateway()
    while True:
        paths = list(RUNS.glob("workflow-*/workflow-run.json"))
        try:
            payload = gateway.get("/api/spawn")
            tasks = payload.get("agents", []) if isinstance(payload, dict) else payload
            raw_approvals = gateway.get("/api/approvals")
            approvals = raw_approvals.get("approvals", []) if isinstance(raw_approvals, dict) else raw_approvals
            for path in paths:
                run = path.parent
                try:
                    import workflow_sessions as sessions
                    manifest = load_run(run)
                    if sessions.state(run)["status"] in {"closed", "deleted"}:
                        previous = read_json(run / "workflow-live.json", {"manifest": manifest})
                        snapshot = sessions.overlay(run, {
                            **previous, "connected": True, "at": time.time(), "parent_running": False,
                            "approvals": []})
                        write_json(run / "workflow-live.json", snapshot)
                        write_json(run / "workflow-report.json", snapshot)
                        continue
                    parent = gateway.get("/api/chat/slots/" + manifest["slot"])
                    with run_lock(run):
                        snapshot = _project(run, tasks, parent, approvals)
                        write_json(run / "workflow-live.json", snapshot)
                        write_json(run / "workflow-report.json", snapshot)
                except Exception as exc:
                    previous = read_json(run / "workflow-live.json", {})
                    write_json(run / "workflow-live.json", {**previous, "connected": False, "error": str(exc)})
        except Exception:
            for path in paths:
                previous = read_json(path.parent / "workflow-live.json", {})
                write_json(path.parent / "workflow-live.json", {**previous, "connected": False,
                           "error": "原生 Gateway 暂时不可达"})
        time.sleep(2)


def catalogue():
    result = copy.deepcopy(TOOLS)
    for tool, value in result.items():
        value["advertised_models"] = advertised_models(tool)
        value["models"] = list(dict.fromkeys(["auto"] + value["advertised_models"]))
        value["observed_models"] = []
        value["catalogue_source"] = "此工具最近一次公布的原生模型候选；不等于当前账号权限保证"
    # Add models actually observed in this PoC, without scanning other chats.
    audit = ROOT / "evidence/provider-routing.jsonl"
    for line in (audit.read_text().splitlines() if audit.exists() else []):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = row.get("session_key", "")
        if not key.startswith("subagent:"):
            continue
        state = read_json(Path.home() / f".kiro/crew/subagents/{key.split(':', 1)[1]}/state.json", {})
        model = re.sub(r"\[(low|medium|high|xhigh|max)\]$", "", state.get("resolved_model", ""))
        tool = row.get("actual_backend")
        if tool in result and model and model not in result[tool]["observed_models"]:
            result[tool]["observed_models"].append(model)
            if tool != "claude" and model not in result[tool]["models"]:
                result[tool]["models"].append(model)
    return result


def preview_file(run, relative):
    relative = unquote(relative)
    parts = Path(relative).parts
    require(parts and not Path(relative).is_absolute() and
            not any(p.startswith(".") for p in parts), "Invalid preview path")
    workspace = (run / "workspace").resolve()
    path = (workspace / relative).resolve()
    require(path.is_relative_to(workspace) and path.is_file() and
            path.suffix.lower() in {".html", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".svg", ".png"},
            "Preview file unavailable")
    return path


def serve(port=PORT):
    import os
    write_json(ROOT / "state/workflow-server.json", {"pid": os.getpid(), "port": port})
    threading.Thread(target=observe, daemon=True).start()
    receipts = ROOT / "state/workflow-intake"
    receipts.mkdir(parents=True, exist_ok=True)
    intake_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, value, status=200, kind="application/json", preview=False, download=None):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            for k, v in {"Content-Type": kind + "; charset=utf-8", "Content-Length": str(len(data)),
                         "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                         "Content-Security-Policy": (
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; connect-src 'none'; "
                             "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                             if preview else
                             "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                             "frame-ancestors 'none'"),
                         "Referrer-Policy": "no-referrer"}.items():
                self.send_header(k, v)
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        def allowed(self, mutation=False):
            return self.headers.get("Host") == f"127.0.0.1:{port}" and (
                not mutation or (self.headers.get("Origin") == f"http://127.0.0.1:{port}"
                                 and self.headers.get("X-Workflow-MVP") == "1"
                                 and self.headers.get("Content-Type", "").split(";")[0] == "application/json"))

        def do_GET(self):
            if self.headers.get("Host") == f"localhost:{port}":
                match = re.fullmatch(r"/p/(workflow-[a-zA-Z0-9-]+)/(.+)", urlsplit(self.path).path)
                if not match:
                    return self.respond({"error": "Preview origin cannot access configuration"}, 403)
                try:
                    path = preview_file(run_path(match[1]), match[2])
                    return self.respond(path.read_bytes(), kind=mimetypes.guess_type(str(path))[0] or "text/plain",
                                        preview=True)
                except (ValueError, OSError):
                    return self.respond({"error": "Preview unavailable"}, 404)
            if not self.allowed():
                return self.respond({"error": "Local workflow origin required"}, 403)
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            try:
                if url.path in {"/", "/workflow.css", "/workflow.js", "/workflow-progress.js", "/workflow-view.css",
                                "/workflow-view.js", "/workflow-templates.json",
                                "/crew-entry.html", "/crew-entry.js"}:
                    name = "workflow.html" if url.path == "/" else url.path[1:]
                    path = ROOT / "ui" / name
                    return self.respond(path.read_bytes(), kind=mimetypes.guess_type(str(path))[0] or "text/plain")
                if url.path == "/api/config":
                    return self.respond(latest(query.get("id", ["development"])[0]))
                if url.path == "/api/definitions":
                    data = library.library()
                    return self.respond([d for d in data["definitions"] if d["id"] not in data["deleted"]])
                if url.path == "/api/library":
                    return self.respond(library.library())
                if url.path == "/api/design":
                    return self.respond(library.design_status(query.get("id", [""])[0]))
                if url.path.startswith("/docs/") and url.path[6:] in {
                    "DEVELOPMENT-HANDBOOK.md", "RELEASE-NOTES.md", "FEATURES-AND-DECISIONS.md",
                    "OPERATIONS.md", "DEVELOPMENT-CONTRACT.md", "STARTUP.md", "SESSION-OPERATIONS.md"}:
                    return self.respond((ROOT / "docs" / url.path[6:]).read_bytes(), kind="text/plain")
                if url.path == "/api/catalogue":
                    return self.respond(catalogue())
                if url.path == "/api/runtime":
                    return self.respond({"features": runtime_features(),
                                         "workbench_features": ["stage_retry_v1", "session_editor_v3",
                                                                "session_operations_v1"]})
                if url.path == "/api/session/backup":
                    import workflow_sessions as sessions
                    path = sessions.backup_path(run_path(query.get("run", [""])[0]))
                    return self.respond(path.read_bytes(), kind="application/zip", download="session-backup.zip")
                if url.path == "/api/runs":
                    runs = [{**load_run(p.parent), "session_meta": library.session_meta(p.parent)}
                            for p in RUNS.glob("workflow-*/workflow-run.json")]
                    return self.respond(sorted(runs, key=lambda r: r["created_at"], reverse=True))
                if url.path == "/api/state":
                    run = run_path(query.get("run", [""])[0])
                    return self.respond(read_json(run / "workflow-live.json",
                                                 {"manifest": load_run(run), "status": "ready", "steps": []}))
                if url.path == "/api/report":
                    run = run_path(query.get("run", [""])[0])
                    return self.respond(read_json(run / "workflow-report.json", {}))
                return self.respond({"error": "Not found"}, 404)
            except Exception as exc:
                return self.respond({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.allowed(True):
                return self.respond({"error": "Local workflow origin required"}, 403)
            try:
                size = int(self.headers.get("Content-Length", 0))
                require(0 < size <= 100000, "请求过大或为空。")
                body = json.loads(self.rfile.read(size))
                path = urlsplit(self.path).path
                if path == "/api/config":
                    return self.respond(save(body["config"], body["expected_revision"]))
                if path == "/api/workflow/lifecycle":
                    return self.respond(library.delete_workflow(
                        body["workflow_id"], body["revision"], body["deleted"]))
                if path == "/api/sessions/archive":
                    return self.respond(library.archive_sessions(body["runs"], body["archived"]))
                if path == "/api/sessions/export":
                    return self.respond(library.export_sessions(body["runs"]),
                                        kind="application/zip", download="kirocrew-sessions.zip")
                if path == "/api/workflow/import":
                    return self.respond(library.import_workflow(body["content"], body["format"]))
                if path == "/api/workflow/export":
                    return self.respond({"content": library.portable(body["config"], body["format"])})
                if path == "/api/design":
                    return self.respond(library.start_design(body["intent"], body["request_id"]))
                if path == "/api/design/app-entry":
                    job = read_json(library.design_path(body["id"]) / "job.json")
                    return self.respond({"url": api.chat_entry_url(job["slot"])})
                if path == "/api/app-entry":
                    return self.respond(app_entry(run_path(body.get("run", ""))))
                if path == "/api/retry":
                    return self.respond(retry_stage(run_path(body.get("run", "")),
                                                    body.get("stage", ""), body.get("request_id", "")))
                if path == "/api/session/continue":
                    import workflow_sessions as sessions
                    return self.respond(sessions.continue_session(
                        run_path(body.get("run", "")), body.get("stage", ""), body.get("input"),
                        body.get("task_prompt"), body.get("request_id", "")))
                if path == "/api/session/control":
                    import workflow_sessions as sessions
                    return self.respond(sessions.control(
                        run_path(body.get("run", "")), body.get("action"), body.get("request_id", ""),
                        body.get("confirmation", "")))
                if path in {"/api/prepare", "/api/start"}:
                    request_id = body.get("request_id", "")
                    require(bool(re.fullmatch(r"[a-zA-Z0-9-]{8,80}", request_id)), "缺少请求 ID。")
                    if path == "/api/start":
                        text(body.get("input"), "用户需求", minimum=3, maximum=12000)
                    receipt_path = receipts / (request_id + ".json")
                    with intake_lock:
                        previous = read_json(receipt_path)
                        request_hash = fingerprint({"path": path, "body": body})
                        if previous:
                            require(previous["request_hash"] == request_hash, "请求 ID 冲突。")
                            return self.respond(previous)
                        # Refuse deterministic input errors before a run or
                        # intake receipt exists; confirmed retries return above.
                        definition = latest(body["workflow_id"])
                        library.assert_active(body["workflow_id"])
                        require(definition["revision"] == body["revision"], "配置已更新，请重新加载。")
                        validate_model_selection(definition["steps"])
                        if path == "/api/start":
                            require_runtime(latest(body["workflow_id"]))
                        pending = {"request_hash": request_hash, "status": "preparing"}
                        write_json(receipt_path, pending)
                        try:
                            manifest = prepare(body["workflow_id"], body["revision"])
                            pending.update(manifest=manifest, status="prepared",
                                           app_url="http://localhost:5476/chat?sid=" + manifest["slot"])
                            write_json(receipt_path, pending)
                            if path == "/api/start":
                                pending["intake"] = submit(manifest, body["input"])
                                pending["status"] = pending["intake"]["status"]
                        except Exception as exc:
                            pending.update(status="uncertain", error=str(exc))
                        write_json(receipt_path, pending)
                        return self.respond(pending)
                return self.respond({"error": "Not found"}, 404)
            except Exception as exc:
                return self.respond({"error": str(exc)}, 400)

        def log_message(self, *_args):
            pass

    print(f"Workflow MVP: http://127.0.0.1:{port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=PORT)
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("save"); p.add_argument("file"); p.add_argument("--expected-revision", type=int, required=True)
    p = sub.add_parser("export"); p.add_argument("id"); p.add_argument("--format", choices=["json", "yaml", "markdown"], default="yaml")
    p = sub.add_parser("import"); p.add_argument("file")
    p = sub.add_parser("set"); p.add_argument("id"); p.add_argument("--step", required=True)
    p.add_argument("--expected-revision", type=int, required=True)
    for field in ("tool", "model", "effort", "prompt", "name"):
        p.add_argument("--" + field)
    p = sub.add_parser("capture"); p.add_argument("run"); p.add_argument("--input-file", required=True)
    p = sub.add_parser("next"); p.add_argument("run")
    p = sub.add_parser("accept"); p.add_argument("run"); p.add_argument("--task-id", required=True)
    p = sub.add_parser("prepare"); p.add_argument("id"); p.add_argument("--revision", type=int, required=True)
    p = sub.add_parser("workspace", help="为本次运行准备固定源码检出或新项目")
    p.add_argument("run")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--source")
    source.add_argument("--new", action="store_true")
    p.add_argument("--ref")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from build_workflow_ui import build
            build(ROOT)
            return serve(args.port)
        if args.command == "show":
            result = latest(args.id)
        elif args.command == "export":
            print(library.portable(latest(args.id), args.format), end="")
            return
        elif args.command == "import":
            path = Path(args.file)
            result = library.import_workflow(path.read_text(), path.suffix.lstrip("."))
        elif args.command == "save":
            path = Path(args.file)
            result = save(parse_document(path.read_text(), path.suffix.lstrip(".")), args.expected_revision)
        elif args.command == "set":
            result = change(args.id, args.step, args.expected_revision,
                            **{f: getattr(args, f) for f in ("tool", "model", "effort", "prompt", "name")})
        elif args.command == "prepare":
            result = prepare(args.id, args.revision)
        else:
            run = run_path(args.run)
            if args.command == "capture":
                result = capture(run, Path(args.input_file).read_text())
            elif args.command == "workspace":
                load_run(run)
                result = workspace.prepare_project(run, source=args.source, ref=args.ref, new=args.new)
            elif args.command == "next":
                result = next_step(run)
            else:
                result = accept(run, args.task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
