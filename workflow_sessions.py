"""Session operations using Crew's existing owner-authenticated HTTP endpoints.

Run identities, workspaces and original snapshots are never replaced or removed.
Only the explicitly confirmed history-delete operation deletes native chat history.
"""
from contextlib import contextmanager
import fcntl
import os
import re
import shlex
import time
from urllib.parse import quote
import uuid
import zipfile

import api
import workflow_library as library


def core():
    return library.w()


def state(run):
    return core().read_json(run / "session-control.json",
                            {"status": "active", "requests": {}})


def store(run, value):
    value["at"] = time.time()
    with core().run_lock(run):
        core().write_json(run / "session-control.json", value)


def require_open(run, *, continuing=False):
    status = state(run)["status"]
    allowed = {"active"} | ({"stopped", "updating"} if continuing else set())
    core().require(status in allowed, "Session 已停止、关闭或正在执行运维操作；请从工作台继续或重新打开。")


def identity(run):
    manifest = core().load_run(run)
    core().require(manifest["id"] == manifest["slot"] == run.name
                   and manifest["parent"] == "dashboard:" + run.name,
                   "运行与原生 Session 身份不一致，拒绝运维操作。")
    return manifest


@contextmanager
def operation_lock(run):
    with (run / ".session-operation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("此 Session 的操作正在处理中，请稍后查看结果。") from None
        yield


def native_state(manifest, *, missing_ok=False):
    try:
        parent = api.request("/api/chat/slots/" + manifest["slot"])
    except api.GatewayError as exc:
        if exc.status != 404 or not missing_ok:
            raise
        parent = None
    payload = api.request("/api/spawn")
    tasks = payload.get("agents", []) if isinstance(payload, dict) else payload
    core().require(isinstance(tasks, list), "原生子任务列表不可读。")
    # Include descendants while excluding every other Session's task tree.
    owners = {manifest["parent"]}
    selected = {}
    for _ in range(len(tasks) + 1):
        added = False
        for task in tasks:
            if task.get("parent") in owners and task.get("id") not in selected:
                selected[task["id"]] = task
                owners.add("subagent:" + task["id"])
                added = True
        if not added:
            break
    return parent, list(selected.values())


def idle(parent, tasks):
    return (parent is None or not parent.get("running") and not parent.get("queue")) and all(
        t.get("done") is True for t in tasks)


def refresh(run, manifest):
    parent, tasks = native_state(manifest)
    core().require(parent is not None, "原生 Session 不存在，请先重新打开历史对话。")
    payload = api.request("/api/approvals")
    approvals = payload.get("approvals", []) if isinstance(payload, dict) else payload
    snapshot = core().project(run, tasks, parent, approvals)
    core().write_json(run / "workflow-live.json", snapshot)
    return snapshot, parent, tasks


def public_state(run, snapshot=None):
    value = state(run)
    result = {k: v for k, v in value.items() if k != "requests"}
    if value["status"] == "stopping" and snapshot is not None:
        if not snapshot.get("parent_running") and not snapshot.get("approvals") and all(
            not s.get("task") or s["task"].get("done") is True for s in snapshot.get("steps", [])):
            result["status"] = "stopped"
    return result


def overlay(run, snapshot):
    value = public_state(run, snapshot)
    status = value["status"]
    snapshot = {**snapshot, "session_control": value}
    if status != "active":
        snapshot["status"] = status
    return snapshot


def backup(run, value, request_id):
    previous = value.get("backup")
    if previous:
        path = run / previous
        core().require(path.resolve().is_relative_to(run.resolve())
                       and path.is_file() and zipfile.is_zipfile(path), "已有备份不可用，不能删除对话。")
        return
    payload = library.export_sessions([run.name])
    path = run / "operations" / request_id / "session-backup.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + uuid.uuid4().hex)
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    core().require(zipfile.is_zipfile(temporary), "会话备份不是有效的 ZIP；操作未继续。")
    os.replace(temporary, path)
    value["backup"] = str(path.relative_to(run))
    store(run, value)


def continue_session(run, stage, requirement, task_prompt, request_id):
    w = core()
    w.ident(stage)
    requirement = w.text(requirement, "续作需求", minimum=3, maximum=12000)
    task_prompt = w.text(task_prompt, "任务说明", minimum=3, maximum=4000)
    w.require(isinstance(request_id, str) and re.fullmatch(r"[a-zA-Z0-9-]{8,80}", request_id),
              "缺少请求 ID。")
    request_hash = w.fingerprint([stage, requirement, task_prompt])
    marker = f"[OCTOPUS-CONTINUE:{request_id}]"
    with operation_lock(run):
        manifest = identity(run)
        value = state(run)
        previous = value["requests"].get(request_id)
        if previous:
            w.require(previous.get("hash") == request_hash and previous.get("action") == "continue",
                      "请求 ID 冲突。")
            w.require(previous["status"] != "cancelled",
                      "此续作已被明确停止，请使用新的请求 ID 提交修订。")
            if previous["status"] in {"accepted", "sending", "uncertain"}:
                # A timeout is not permission to send another turn.
                if previous["status"] != "accepted":
                    parent, _ = native_state(manifest)
                    if any(marker in str(m.get("content", m.get("text", "")))
                           for m in parent.get("messages", [])):
                        previous.update(status="accepted", reconciled=True)
                        store(run, value)
                    else:
                        previous["status"] = "uncertain"
                return previous
        w.require(not any(r.get("action") == "continue" and r.get("status") in {"sending", "uncertain"}
                          for key, r in value["requests"].items() if key != request_id),
                  "此前续作的发送结果尚未确认，请核对原对话或明确停止后再提交新修订。")
        w.require(value["status"] in {"active", "stopped", "stopping", "updating"}, "请先重新打开已关闭的 Session。")
        snapshot, parent, tasks = refresh(run, manifest)
        w.require(idle(parent, tasks) and not snapshot.get("approvals"),
                  "本 Session 仍在运行、排队或等待授权。先强制停止并等待停止确认，再编辑续作。")
        if value["status"] == "stopping":
            value["status"] = "stopped"
            store(run, value)
        result = {"action": "continue", "hash": request_hash, "request_id": request_id,
                  "run": manifest["id"], "stage": stage, "status": "preparing"}
        value["requests"][request_id] = result
        prior_status = value["status"]
        value["status"] = "updating"
        store(run, value)
        try:
            retry = w.retry_stage(run, stage, request_id,
                                 update={"requirement": requirement, "task_prompt": task_prompt})
        except Exception:
            value["status"] = "stopped" if prior_status == "updating" else prior_status
            result["status"] = "failed"
            store(run, value)
            raise
        command = "python3 " + shlex.quote(str(w.ROOT / "workflow.py"))
        message = (
            f"{marker}\n在当前 Session 继续研发，保留 Run {manifest['id']} 与现有源码工作区。"
            f"本次需求与任务说明已登记为续作修订 {retry['input_revision']}，从 {stage} 重跑。\n"
            "先读取 retry-state.json 的 input/task_prompts 和 workspace/CONTINUATION.md（若存在），"
            "以新修订为准，不用原 input.json 覆盖。不得 prepare 新 Run 或克隆新工作区。\n"
            f"{retry['next_hint']}\n所有实际调用仍由本父会话通过原生 MCP 完成。"
            f"工作区状态可用 {command} next {shlex.quote(str(run))} 核对。"
        )
        value.update(status="active", backup=None, last_action="continue")
        result.update(status="sending", input_revision=retry["input_revision"], invalidated=retry["invalidated"])
        store(run, value)
        try:
            receipt = api.request("/api/chat?ws=1",
                                  {"slot": manifest["slot"], "message": message, "meta": {"sendId": request_id}})
            w.require(receipt.get("ok"), "原生 App 未确认续作。")
            result.update(status="accepted", receipt=receipt)
        except Exception as exc:
            result.update(status="uncertain", error=str(exc))
        store(run, value)
        return result


def control(run, action, request_id, confirmation=""):
    w = core()
    w.require(action in {"stop", "close", "reopen", "delete"}, "不支持的 Session 操作。")
    w.require(isinstance(request_id, str) and re.fullmatch(r"[a-zA-Z0-9-]{8,80}", request_id),
              "缺少请求 ID。")
    with operation_lock(run):
        manifest = identity(run)
        w.require(action != "delete" or confirmation == manifest["id"], "删除前必须输入完整 Session ID。")
        value = state(run)
        previous = value["requests"].get(request_id)
        if previous:
            w.require(previous["action"] == action, "请求 ID 冲突。")
            if previous["status"] == "completed":
                return previous
        w.require(value["status"] != "deleted", "Session 已删除；源码和审计仍保留，不能原地续作。")
        result = previous or {"action": action, "request_id": request_id, "run": manifest["id"]}
        value["requests"][request_id] = result
        result.update(status="working", error=None)
        store(run, value)
        try:
            if action == "reopen":
                w.require(value["status"] == "closed", "只有已关闭的 Session 可以重新打开。")
                receipt = api.request("/api/chat/slots/" + manifest["slot"] + "/resume",
                                      {"key": manifest["parent"]})
                w.require(receipt.get("ok") or receipt.get("key") == manifest["slot"]
                          or receipt.get("slot") == manifest["slot"], "原生 App 未确认重新打开。")
                parent, _ = native_state(manifest)
                w.require(parent is not None, "原生对话尚未重新打开。")
                value.update(status="stopped", last_action=action, backup=None)
                result["receipt"] = receipt
            else:
                if value["status"] != "closed":
                    # A durable barrier stops next/accept before native cancellation.
                    value.update(status="stopping", last_action=action)
                    store(run, value)
                    parent, _ = native_state(manifest)
                    w.require(parent is not None, "原生对话不可达，未执行停止。")
                    result["parent_stop"] = api.request(
                        "/api/chat/slots/" + manifest["slot"] + "/stop?force=true", {})
                    w.require(result["parent_stop"].get("ok"), "原生父对话未确认停止。")
                    result["children_stop"] = api.request("/api/spawn/stop-all", {"slot": manifest["slot"]})
                    w.require(result["children_stop"].get("ok"), "原生子任务未确认停止。")
                    parent, tasks = native_state(manifest)
                    if not idle(parent, tasks):
                        result.update(status="pending", error="停止请求已提交，原生任务尚未全部结束。请稍后重试此操作。")
                        store(run, value)
                        return result
                    value["status"] = "stopped"
                    for item in value["requests"].values():
                        if item.get("action") == "continue" and item.get("status") in {"sending", "uncertain"}:
                            item.update(status="cancelled", cancelled_by=request_id)
                    if action in {"close", "delete"}:
                        backup(run, value, request_id)
                        receipt = api.request("/api/chat/slots/" + manifest["slot"], method="DELETE")
                        w.require(receipt.get("ok"), "原生 App 未确认关闭。")
                        value["status"] = "closed"
                        store(run, value)
                if action == "delete":
                    backup(run, value, request_id)
                    receipt = api.request("/api/sessions/" + quote(manifest["parent"], safe=""), method="DELETE")
                    w.require(receipt.get("ok"), "原生历史未确认删除；保留关闭状态和备份。")
                    value["status"] = "deleted"
                    result["receipt"] = receipt
            result.update(status="completed", session_status=value["status"], backup=value.get("backup"))
            value["last_action"] = action
        except Exception as exc:
            result.update(status="failed", error=str(exc), session_status=value["status"])
        store(run, value)
        return result


def backup_path(run):
    w = core()
    relative = state(run).get("backup")
    w.require(relative, "此 Session 尚无运维备份。")
    path = (run / relative).resolve()
    w.require(path.is_relative_to(run.resolve()) and path.name == "session-backup.zip" and path.is_file(),
              "运维备份不可用。")
    return path
