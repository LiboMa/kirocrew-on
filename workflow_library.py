"""Workflow library, reversible workbench archives and native chat drafting.

All actual conversations and model work remain in KiroCrew. This module owns
only PoC metadata and document projections; it never deletes native sessions.
"""
from __future__ import annotations

import fcntl
import gzip
import io
import json
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile

import api
import development_prompts as development
from workflow_files import emit_document, parse_document


def w():
    # CLI executes workflow.py as __main__; use that instance if present.
    import sys
    main = sys.modules.get("__main__")
    if getattr(main, "__file__", "").endswith("/workflow.py"):
        return main
    import workflow
    return workflow


def lifecycle():
    return w().read_json(w().VERSIONS / ".lifecycle.json", {"deleted": {}})


def library():
    core = w()
    definitions = [core.latest(p.name) for p in sorted(core.VERSIONS.iterdir())
                   if p.is_dir() and list(p.glob("*.json"))] if core.VERSIONS.exists() else []
    return {"definitions": definitions, "deleted": lifecycle()["deleted"]}


def assert_active(workflow_id):
    w().require(workflow_id not in lifecycle()["deleted"],
                "工作流已删除。请先恢复，或复制为新工作流。")


def delete_workflow(workflow_id, revision, deleted):
    core = w()
    core.require(isinstance(deleted, bool), "deleted 必须是布尔值。")
    core.require(isinstance(revision, int) and not isinstance(revision, bool) and revision > 0,
                 "revision 必须是已保存的正整数版本。")
    with (core.VERSIONS / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        definition = core.latest(workflow_id)
        core.require(definition["revision"] == revision, "版本冲突，请重新加载工作流。")
        state = lifecycle()
        if deleted:
            state["deleted"][workflow_id] = {"at": time.time(), "revision": revision}
        else:
            core.assert_unique_name(definition["name"], workflow_id)
            state["deleted"].pop(workflow_id, None)
        core.write_json(core.VERSIONS / ".lifecycle.json", state)
    return {"ok": True, "deleted": deleted, "workflow_id": workflow_id}


def session_meta(run):
    meta = w().read_json(run / "session-meta.json", {"archived": False})
    control = w().read_json(run / "session-control.json", {})
    return {**meta, "closed": control.get("status") == "closed",
            "deleted": control.get("status") == "deleted"}


def archive_sessions(ids, archived):
    core = w()
    core.require(isinstance(archived, bool), "archived 必须是布尔值。")
    paths = selected_runs(ids)
    # Validate all before writing; native sessions and immutable manifests stay
    # untouched. Metadata is in the workbench, so a running task is not stopped.
    for run in paths:
        core.write_json(run / "session-meta.json", {"archived": archived, "at": time.time()})
    return {"ok": True, "runs": [p.name for p in paths], "archived": archived,
            "scope": "workbench"}


def selected_runs(ids):
    core = w()
    core.require(isinstance(ids, list) and 1 <= len(ids) <= 20 and
                 all(isinstance(v, str) for v in ids) and len(ids) == len(set(ids)),
                 "请选择 1–20 个不同的 Session。")
    paths = [core.run_path(value) for value in ids]
    for run in paths:
        core.load_run(run)
    return paths


def available_name(name):
    """A draft suggestion only; save checks again under the cross-process lock."""
    core = w()
    base = core.workflow_name(name)
    used = {core.name_key(d["name"]) for d in library()["definitions"]}
    result, number = base, 2
    while core.name_key(result) in used:
        suffix = f" ({number})"
        result = base[:80 - len(suffix)] + suffix
        number += 1
    return result


def import_workflow(content, format):
    core = w()
    value = core.validate(parse_document(content, format))
    source_id = value["id"]
    # Import always forks a draft: no accidental update of a same-named workflow.
    value.update(id="flow-" + uuid.uuid4().hex[:12], revision=0,
                 name=available_name(value["name"]))
    return {"config": value, "source_id": source_id}


def portable(value, format):
    core = w()
    validated = core.validate(value)
    revision = value.get("revision", 0)
    core.require(isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0,
                 "revision 必须是非负整数。")
    return emit_document({**validated, "revision": revision}, format)


def _fence(content, language="text"):
    marker = "`" * max(3, max((len(v) + 1 for v in re.findall(r"`+", content)), default=3))
    return f"{marker}{language}\n{content}\n{marker}\n"


def session_documents(manifest, snapshot, bundle):
    core = w()
    definition = manifest["workflow"]
    messages = bundle.get("messages", [])
    conversations, features = [], []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "unknown"))
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        conversations.append(f"### {index + 1}. {role}\n\n{_fence(content)}")
        if role == "user" and content.strip():
            features.append({"message": index + 1, "text": content,
                             "status": "用户输入，是否实现需参照任务证据"})
    rows = snapshot.get("steps") or [dict(s, status="pending") for s in definition["steps"]]
    task_text = []
    release = []
    for step in rows:
        native = (step.get("task") or {}).get("id") or (step.get("native_request") or {}).get("task_id")
        uid = f"{manifest['id']}/{step['id']}"
        task_text.append(f"### {step['name']}\n\nTask ID：`{uid}`\n\n"
                         f"原生执行 ID：`{native or '尚未分派'}`\n\n"
                         f"工具：{step['tool']}；请求模型：`{step['model']}`；"
                         f"Effort：`{step['effort'] or '默认'}`；状态：`{step.get('status', 'pending')}`。\n\n"
                         f"实际模型：`{step.get('resolved_model') or '未报告'}`。\n\n"
                         f"{_fence(step['prompt'])}\n"
                         f"执行返回：\n\n{_fence((step.get('task') or {}).get('result') or '尚未返回。')}")
        release.append(f"- {step['name']}：{step.get('status', 'pending')}；Task ID `{uid}`。")
    header = (f"工作流：{definition['name']} / `{definition['id']}` / r{definition['revision']}\n\n"
              f"Session：`{manifest['slot']}`\n\n配置 SHA256：`{manifest['workflow_sha256']}`\n")
    request = snapshot.get("input", {}).get("text", "请参照原生对话。")
    all_accepted = bool(rows) and all(s.get("status") == "accepted" for s in rows)
    return {
        "conversation.md": "# Session 对话\n\n" + header + "\n" + "\n".join(conversations),
        "development-manual.md": "# 本次开发手册\n\n" + header +
            "\n## 原始需求\n\n" + _fence(request) +
            "\n## 工作方式\n\nKiroCrew 主对话按固定版本调用原生 spawn_run。"
            "各 Task 的子 Session 通过共享工作目录、需求文件与交接结果传递上下文。\n"
            "\n## 任务、配置与证据\n\n" + "\n".join(task_text) +
            "\n## 重现\n\n导入 workflow.yaml 为新草稿，确认当前工具、模型和权限，再建立新 Session。"
            "原生对话包使用 KiroCrew 的导入功能；工具私有上下文不保证随 Layer A 一同迁移。\n",
        "release-notes.md": "# Release 候选记录\n\n" + header +
            f"\n交接状态：{'全部任务已交接' if all_accepted else '尚未全部交接'}。"
            "这是执行证据摘要，未执行发布；交接成功不等同于无安全问题或生产验收通过。\n\n" +
            "\n".join(release) + "\n\n详细测试与审查结论见 development-manual.md 和 report.json。\n",
        "features.json": json.dumps({"session": manifest["slot"], "user_inputs": features,
                                    "scope": "原生导出中的用户消息；没有推测缺失消息"},
                                   ensure_ascii=False, indent=2),
    }


def export_sessions(ids):
    core = w()
    paths = selected_runs(ids)
    stream = io.BytesIO()
    total = 0
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for run in paths:
            manifest = core.load_run(run)
            raw = api.export_session(manifest["slot"])
            # Crew performs redaction. Do not export private tool transcripts,
            # credentials, arbitrary workspace files, or request Layer B.
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as source:
                decoded = source.read(32 * 1024 * 1024 + 1)
            total += len(decoded)
            core.require(total <= 64 * 1024 * 1024, "会话包总计超过 64 MB，请减少选择。")
            bundle = json.loads(decoded)
            snapshot = core.read_json(run / "workflow-report.json", {})
            # Redacted native bundle is the authoritative conversation source.
            docs = session_documents(manifest, snapshot, bundle)
            prefix = run.name + "/"
            archive.writestr(prefix + "session.kcsession.json.gz", raw)
            archive.writestr(prefix + "workflow.yaml", emit_document(manifest["workflow"], "yaml"))
            archive.writestr(prefix + "workflow.md", emit_document(manifest["workflow"], "md"))
            archive.writestr(prefix + "report.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
            project = core.workspace.read_record(run)
            if project is not None:
                archive.writestr(prefix + "project.json", json.dumps(project, ensure_ascii=False, indent=2))
            for name in ("retry-state.json", "session-control.json"):
                record = core.read_json(run / name)
                if record is not None:
                    archive.writestr(prefix + name, json.dumps(record, ensure_ascii=False, indent=2))
            for path in sorted((run / "continuations").glob("*.json")):
                archive.writestr(prefix + "continuations/" + path.name, path.read_bytes())
            for name, content in docs.items():
                archive.writestr(prefix + name, content)
    return stream.getvalue()


def design_root():
    return w().ROOT / "state/workflow-designs"


def design_path(design_id):
    w().require(isinstance(design_id, str) and re.fullmatch(r"design-[a-f0-9]{32}", design_id),
                "无效的生成任务 ID。")
    path = design_root() / design_id
    w().require((path / "job.json").is_file(), "生成任务不存在。")
    return path


def start_design(intent, request_id):
    core = w()
    intent = core.text(intent, "工作流意图", minimum=3, maximum=12000)
    try:
        parsed_id = uuid.UUID(request_id)
        core.require(request_id in {parsed_id.hex, str(parsed_id)}, "生成请求 ID 不是标准 UUID。")
    except (ValueError, AttributeError, TypeError):
        raise ValueError("缺少有效的生成请求 ID。") from None
    root = design_root()
    root.mkdir(parents=True, exist_ok=True)
    design_id = "design-" + parsed_id.hex
    path = root / design_id
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            previous = core.read_json(path / "job.json")
            core.require(previous["intent"] == intent, "生成请求 ID 冲突。")
            return previous
        path.mkdir()
        job = {"id": design_id, "intent": intent, "status": "preparing", "mode": "assistant_json",
               "created_at": time.time(), "slot": "wf-" + design_id,
               "workflow_id": "flow-" + uuid.uuid4().hex[:12]}
        core.write_json(path / "job.json", job)
    threading.Thread(target=_generate, args=(path, job), daemon=True).start()
    return job


def _generate(path, job):
    core = w()
    try:
        slot = api.request("/api/chat/slots", {
            "name": job["slot"], "agent": "poc-kiro", "agent_kind": "template", "model": "auto"})
        core.require(slot.get("key", slot.get("slot")) == job["slot"], "原生生成 Session 不匹配。")
        example = {"schema": 1, "id": job["workflow_id"], "name": "工作流名称",
                   "steps": [{"id": "task-1", "name": "任务名称", "tool": "kiro",
                              "model": "auto", "effort": "", "prompt": "明确任务、交付物与验证方式"}]}
        design_workspace = path / "workspace"
        design_workspace.mkdir(exist_ok=True)
        prompt = development.intent_prompt(
            json.dumps(example, ensure_ascii=False), job["intent"], design_workspace)
        receipt = api.request("/api/chat?ws=1", {"slot": job["slot"], "message": prompt})
        core.require(receipt.get("ok"), "KiroCrew 未确认生成请求。")
        job.update(status="submitted", receipt={"ok": True})
    except Exception as exc:
        job.update(status="uncertain", error=str(exc))
    core.write_json(path / "job.json", job)


def design_status(design_id):
    core = w()
    path = design_path(design_id)
    job = core.read_json(path / "job.json")
    output = path / "workflow.json"
    if output.is_file():
        try:
            core.require(not output.is_symlink() and output.stat().st_size <= 100000,
                         "生成结果文件无效或超过限制。")
            definition = core.validate(parse_document(output.read_text(), "json"))
            definition.update(id=job["workflow_id"], revision=0)
            return {**job, "status": "ready", "config": definition}
        except Exception as exc:
            # A file may be observed during a write. Keep the generated bytes;
            # expose a validation error, never silently replace with a template.
            return {**job, "status": "invalid", "error": str(exc)}
    if job["status"] == "submitted":
        try:
            state = api.request("/api/chat/slots/" + job["slot"])
            if job.get("mode") == "assistant_json" and not state.get("running"):
                replies = [m.get("content") for m in state.get("messages", [])
                           if m.get("role") == "assistant" and isinstance(m.get("content"), str)
                           and m["content"].strip()]
                if replies:
                    reply = replies[-1].strip()
                    try:
                        definition = core.validate(parse_document(
                            reply, "json" if reply.startswith("{") else "markdown"))
                        definition.update(id=job["workflow_id"], revision=0)
                        # Store only a validated draft. Workflow save still needs
                        # the user's explicit adoption and normal version check.
                        core.write_json(output, definition)
                        return {**job, "status": "ready", "config": definition}
                    except Exception as exc:
                        return {**job, "status": "invalid",
                                "error": "原生回复未通过工作流校验：" + str(exc)}
            pending = [m for m in state.get("messages", []) if m.get("role") == "permission"
                       and not m.get("meta", {}).get("resolved")]
            job["status"] = "awaiting_approval" if pending else "running" if state.get("running") else "waiting"
            if pending:
                job["permission_title"] = pending[-1].get("meta", {}).get("tool_title", "工具操作")
            if not state.get("running") and time.time() - job["created_at"] > 180:
                job.update(status="needs_attention", error="尚未得到有效草稿，请在生成对话中查看原因。")
        except Exception as exc:
            job["error"] = str(exc)
    return job
