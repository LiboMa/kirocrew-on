"""Native-App pipeline intake, deterministic gates and observable handoffs.

Intake uses native slot/context/chat APIs; only the App agent dispatches workers.
No LLM invocation, subprocess Coding CLI, or spawn POST exists here.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlsplit, parse_qs
import uuid

import api
from pipeline_spec import STAGES, CONTRACT, BASELINE, TESTS, WEB_SCOPE, EMPTY_CORE, requested_tasks

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "state/runs"
STAGE_IDS = [s["id"] for s in STAGES]


def write_json(path, value):
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def run_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to(RUNS.resolve()) or path == RUNS.resolve():
        raise ValueError("Run must be a child of this PoC's state/runs")
    if not (path / "manifest.json").is_file():
        raise ValueError("Missing pipeline manifest")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def marker(manifest, stage):
    return f"[PIPELINE:{manifest['id']} STAGE:{stage}]"


def new_demo_run():
    run_id = time.strftime("pipeline-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    run = RUNS / run_id
    for name in ("workspace/web", "workspace/tests", "baseline", "tasks", "gates"):
        (run / name).mkdir(parents=True)
    for file, text in {
        "baseline/core.mjs": BASELINE,
        "workspace/web/core.mjs": BASELINE,
        "workspace/tests/acceptance.test.mjs": TESTS,
        "workspace/TASK.md": CONTRACT,
    }.items():
        (run / file).write_text(text)
    manifest = {
        "schema": 1, "id": run_id, "title": "发布说明工作台",
        "created_at": time.time(), "run": str(run), "slot": run_id,
        "parent": f"dashboard:{run_id}", "gateway": "http://127.0.0.1:5476",
        "stages": STAGES, "baseline_sha256": digest(run / "baseline/core.mjs"),
        "acceptance_sha256": digest(run / "workspace/tests/acceptance.test.mjs"),
    }
    write_json(run / "manifest.json", manifest)
    common = (
        f"唯一工作目录 {run / 'workspace'}。先阅读 TASK.md 与 tests/acceptance.test.mjs。"
        "实际完成文件工作；不安装依赖，不访问网络，不提交或发布。"
        "不改任务书、baseline、固定验收测试、manifest 或 gates 目录。"
    )
    tasks = {
        "plan": "只做规划。写 workspace/plan.md：模块契约、开发步骤、安全风险、测试与前端交付要求。不要实现 core.mjs。",
        "code": "先读 plan.md。只实现 workspace/web/core.mjs 的三个导出函数。运行 node --test tests/*.test.mjs，报告实际结果。",
        "review": (
            f"先读 {run / 'candidate.diff'}、{run / 'gates/code.json'}、{run / 'code-tests.txt'} 和 web/core.mjs。"
            "运行 node --test tests/*.test.mjs。检查 HTML 注入、分类注入、空输入、去重和无外部依赖。"
            "允许新增 tests/review.test.mjs，但不可改核心或固定测试。"
            "写 workspace/review.json，格式严格为 "
            '{"verdict":"pass 或 fail","candidate_sha256":"code gate 的真实 candidate_sha256",'
            '"tests_passed":true,"findings":[{"severity":"critical/high/medium/low/info","description":"说明"}],'
            '"summary":"实际审查范围和结论"}。'
            "发现 critical/high 或测试失败时 verdict 必须 fail。另写 review.md 供阅读。"
        ),
        "deliver": (
            "先读 plan.md、review.json 和 review.md。只有审查通过才交付。"
            "实际写 workspace/web/index.html、style.css、ui.mjs，导入 ./core.mjs，禁止修改核心与测试。"
            "制作中文发布说明工作台：多行输入、生成发布说明、分类计数、安全预览、复制纯文本、载入示例、清空。"
            "白底蓝色、精致但简单、移动端可用；不使用 CDN。HTML 用 script type=module src=./ui.mjs。"
            "动态不可信内容使用 textContent 或已经审查的 renderNotes；不引入新的不安全 innerHTML。"
            "运行 node --test tests/*.test.mjs，写 workspace/delivery.md，说明用法、文件和实际测试。"
            "不要只解释准备做什么，必须落地文件。"
        ),
    }
    for stage, task in tasks.items():
        (run / f"tasks/{stage}.txt").write_text(
            f"{marker(manifest, stage)}\n{common}\n{task}\n所有 workspace/ 路径均相对于 {run}。\n"
        )
    command = f"python3 {shlex.quote(str(ROOT / 'pipeline.py'))} checkpoint {shlex.quote(str(run))}"
    prompt = (
        f"请执行本次用户授权的四 Coding Tool 协作 Pipeline：{run_id}。\n"
        f"目标：{manifest['title']}。完整契约：{run / 'workspace/TASK.md'}。\n"
        f"原生父会话必须是当前 {run_id}；cwd={run / 'workspace'}。\n"
        "你只协调，不代替工作者编写实现。按以下四步严格串行，每步用原生 spawn_run，"
        "keep=true、include_memory=false、solo_reason=user_requested，"
        "solo_details='用户明确要求 Kiro CLI 规划、Claude Code 编码、Codex 安全测试、OpenCode 前端交付'。\n"
        + "\n".join(
            f"{i+1}. agent={s['agent']}，读取 {run / 'tasks' / (s['id']+'.txt')}，"
            f"将其全文（必须含 {marker(manifest,s['id'])}）作为子任务。\n"
            f"   等真实完成事件回来后，运行：{command} --stage {s['id']} --task-id <真实子任务ID>"
            for i, s in enumerate(STAGES)
        )
        + "\n每次 spawn 后结束当前 turn，由原生完成事件唤醒；不循环轮询。"
        "spawn_continue 返回新的 task-id，checkpoint 必须用这个新 ID，不能用最初 conversation ID。"
        "checkpoint exit 0 才能继续；未通过则如实报告并用 spawn_continue 要求原工作者补齐，"
        "继续任务也要带相同 PIPELINE/STAGE 标记，最多两次。"
        "最终总结四个真实任务 ID、测试、审查结论、报告路径。"
        f"实时网页 http://127.0.0.1:8916/，报告 {run / 'report.md'}。"
    )
    (run / "app-prompt.txt").write_text(prompt + "\n")
    # This is a normal, persistent, EMPTY App slot; no model task is sent here.
    result = api.request("/api/chat/slots", {
        "name": run_id, "agent": "poc-pipeline-coordinator",
        "agent_kind": "template", "model": "auto",
    })
    slot = result.get("key", result.get("slot", run_id))
    if slot != run_id:
        raise RuntimeError(f"Unexpected slot identity: {slot!r}; inspect before sending")
    write_json(ROOT / "state/current-pipeline.json", {"run": str(run), "slot": run_id})
    report(run)
    return manifest


def validate_request(text, title=""):
    require(isinstance(text, str) and 10 <= len(text.strip()) <= 12000,
            "请用 10–12000 个字符描述需求。")
    require(isinstance(title, str) and len(title.strip()) <= 80, "任务名称最多 80 个字符。")
    require("\x00" not in text + title, "需求不能包含空字符。")
    return text.strip(), title.strip()


def capture_request(run, text, title=""):
    text, title = validate_request(text, title)
    manifest = read_json(run / "manifest.json")
    require(manifest.get("schema") == 2, "Only requirement-driven runs accept input")
    path = run / "workspace/REQUEST.md"
    if manifest.get("request_sha256"):
        require(path.read_text() == text + "\n", "本轮需求已冻结；修改需求请新建任务。")
        return manifest
    path.write_text(text + "\n")
    (run / "workspace/TASK.md").write_text(
        "# 用户需求驱动的 Web 开发任务\n\n" + WEB_SCOPE +
        "\n\n产品需求以 REQUEST.md 为准。Kiro 生成 SPEC.md、plan.md 和验收测试，"
        "通过规划验收后锁定；Claude 实现核心，Codex 审查，OpenCode 交付界面。\n"
    )
    manifest.update(title=title or text.splitlines()[0][:40],
                    request_sha256=digest(path), task_sha256=digest(run / "workspace/TASK.md"),
                    request_captured_at=time.time())
    write_json(run / "manifest.json", manifest)
    report(run)
    return manifest


def new_run(requirements=None, title=""):
    """Prepare a fresh native App conversation; no model turn until user submits."""
    if requirements is not None:
        requirements, title = validate_request(requirements, title)
    run_id = time.strftime("pipeline-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    run = RUNS / run_id
    for name in ("workspace/web", "workspace/tests", "baseline", "tasks", "gates"):
        (run / name).mkdir(parents=True)
    for name in ("baseline/core.mjs", "workspace/web/core.mjs"):
        (run / name).write_text(EMPTY_CORE)
    manifest = {
        "schema": 2, "id": run_id, "title": title or "等待输入开发需求",
        "created_at": time.time(), "run": str(run), "slot": run_id,
        "parent": f"dashboard:{run_id}", "gateway": "http://localhost:5476",
        "stages": STAGES, "baseline_sha256": digest(run / "baseline/core.mjs"),
        "request_sha256": None, "scope": "local-web-no-dependencies",
    }
    write_json(run / "manifest.json", manifest)
    if requirements is not None:
        manifest = capture_request(run, requirements, title)
    for stage, task in requested_tasks(run).items():
        (run / f"tasks/{stage}.txt").write_text(f"{marker(manifest, stage)}\n{task}\n")
    command = f"python3 {shlex.quote(str(ROOT / 'pipeline.py'))}"
    prompt = (
        f"这是用户选择的 KiroCrew 四工具 Pipeline，会话 {run_id}，运行目录 {run}。\n"
        f"先阅读 {run / 'manifest.json'}。schema=2 表示用户需求驱动，本次不是固定演示。\n"
        "如果 request_sha256 为空：将用户此次消息中的原始开发需求原样写入 "
        f"{run / 'workspace/request-input.txt'}，然后运行 "
        f"{command} capture {shlex.quote(str(run))}。不要让用户编辑任何计划或任务文件。\n"
        f"如果 request_sha256 已存在：需求已在 {run / 'workspace/REQUEST.md'}，不要重复登记。\n"
        f"{WEB_SCOPE}\n"
        f"你负责接受需求与协调。cwd={run / 'workspace'}。不代写实现。"
        "按以下顺序用原生 spawn_run，keep=true、include_memory=false、"
        "solo_reason=user_requested，solo_details='用户选择 Kiro 规划、Claude 编码、Codex 审查、OpenCode 交付'。\n"
        + "\n".join(
            f"{i+1}. agent={s['agent']}，读取 {run / 'tasks' / (s['id']+'.txt')}，"
            f"以其全文（含 {marker(manifest, s['id'])}）作为子任务。\n"
            f"   完成事件回来后执行 {command} checkpoint {shlex.quote(str(run))}"
            f" --stage {s['id']} --task-id <本次真实任务ID>。"
            for i, s in enumerate(STAGES)
        )
        + "\nKiro 规划工作者生成规格/计划/验收测试；用户只需要输入自然语言需求。"
        "每次分派后结束 turn，等待完成事件；不轮询或自行调用外部 CLI。"
        "checkpoint exit 0 才可下一阶段，失败用原生 spawn_continue 补齐，最多两次；"
        "续接返回新的 task-id，验收用新 ID。最终回到本 App 会话汇总真实证据。"
        "若 Codex 审查发现代码缺陷：保留审查报告与回归测试，将问题通过 spawn_continue"
        "交回原 Claude 编码会话，带 STAGE:code 标记，只修核心，禁止修改规格与所有测试。"
        "用新 task-id 重新跑 code checkpoint，再续接原 Codex 会话复审新候选，"
        "带 STAGE:review 标记并用新 task-id 验收；最多两轮。不能让审查者代改核心或跳过失败测试。"
        f"\n看板 http://127.0.0.1:8916/?run={run_id}；状态报告 {run / 'report.md'}。"
    )
    (run / "app-prompt.txt").write_text(prompt + "\n")
    result = api.request("/api/chat/slots", {
        "name": run_id, "agent": "poc-pipeline-coordinator",
        "agent_kind": "template", "model": "auto",
    })
    require(result.get("key", result.get("slot", run_id)) == run_id, "Unexpected native slot identity")
    # Native background-context API: workflow instructions never need manual copy/paste.
    receipt = api.request(f"/api/chat/slots/{run_id}/context",
                          {"content": prompt, "source": "pipeline-intake", "ephemeral": True})
    require(receipt.get("ok"), "Native App context was not accepted")
    write_json(run / "intake.json", {"status": "prepared", "source": "native-slot-context",
                                    "prepared_at": time.time(), "context_receipt": receipt})
    write_json(ROOT / "state/current-pipeline.json", {"run": str(run), "slot": run_id})
    report(run)
    return manifest


def submit_run(manifest):
    """One user action -> one native chat turn. Never auto-retry ambiguous sends."""
    run = Path(manifest["run"])
    text = (run / "workspace/REQUEST.md").read_text().strip()
    value = read_json(run / "intake.json", {})
    require(value.get("status") == "prepared", "This run was already submitted; inspect the App conversation")
    write_json(run / "intake.json", {**value, "status": "sending", "sent_at": time.time()})
    try:
        # ws=1 returns a native accepted receipt immediately; progress stays in App.
        receipt = api.request("/api/chat?ws=1", {"slot": manifest["slot"], "message": text})
        require(receipt.get("ok") and receipt.get("slot") == manifest["slot"], "Native chat did not confirm acceptance")
        value.update(status="accepted", receipt=receipt, sent_at=time.time())
    except Exception as exc:
        value.update(status="uncertain", error=str(exc),
                     guidance="请打开 App 会话确认需求是否已经发送；不要重复点击发送。")
    write_json(run / "intake.json", value)
    return value


def native_tasks(manifest):
    # Called INSIDE a Coding Agent by checkpoint. Crew intentionally denies
    # /api/token/local there; never try to borrow the desktop owner's identity.
    # The host observer has already projected this run's native GET results.
    snapshot = read_json(Path(manifest["run"]) / "live-state.json", {})
    require(snapshot.get("manifest", {}).get("id") == manifest["id"]
            and snapshot.get("manifest", {}).get("parent") == manifest["parent"],
            "Observer snapshot does not belong to this run")
    require(snapshot.get("connected") and
            0 <= time.time() - snapshot.get("last_success_at", 0) < 15,
            "Observer snapshot is stale; start pipeline.py serve on the host and retry")
    return [s["task"] for s in snapshot.get("stages", []) if s.get("task")
            and s["task"].get("parent") == manifest["parent"]
            and any(marker(manifest, stage) in s["task"].get("task", "") for stage in STAGE_IDS)]


def route_for(task):
    # spawn_continue uses a NEW task id but retains the original Crew/tool
    # session. Read that native mapping instead of inventing a new session.
    stored = read_json(Path.home() / f".kiro/crew/subagents/{task['id']}/state.json", {})
    valid_mapping = (stored.get("id") == task["id"] and stored.get("agent") == task.get("agent")
                     and stored.get("parent_session") == task.get("parent"))
    session_key = stored.get("conversation_key") if valid_mapping else None
    session_key = session_key or f"subagent:{task['id']}"
    path = ROOT / "evidence/provider-routing.jsonl"
    for line in reversed(path.read_text().splitlines() if path.exists() else []):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("session_key") == session_key:
            return {**row, "crew_session_key": session_key,
                    "native_session_id": stored.get("session_id") if valid_mapping else None}
    return None


def task_identity(task):
    """Native list leaves agent empty on continuation; use its stored binding."""
    if task.get("agent") or not re.fullmatch(r"[a-zA-Z0-9_-]+", task["id"]):
        return task
    stored = read_json(Path.home() / f".kiro/crew/subagents/{task['id']}/state.json", {})
    context = stored.get("execution_context", {})
    if (stored.get("id") == task["id"] and stored.get("parent_session") == task.get("parent")
            and stored.get("agent") and context.get("template_id") == stored["agent"]
            and context.get("selection_kind") == "template"
            and str(stored.get("conversation_key", "")).startswith("subagent:")):
        return {**task, "listed_agent": task.get("agent", ""),
                "agent": stored["agent"], "identity_source": "native-persistence"}
    return task


def require(condition, message):
    if not condition:
        raise ValueError(message)


def tests(run, name):
    node = shutil.which("node") or "/opt/homebrew/bin/node"
    files = sorted((run / "workspace/tests").glob("*.test.mjs"))
    result = subprocess.run([node, "--test", *map(str, files)],
                            cwd=run / "workspace", text=True, capture_output=True, timeout=40)
    (run / f"{name}-tests.txt").write_text(result.stdout + result.stderr)
    require(result.returncode == 0, f"{name}: tests failed; see {name}-tests.txt")
    return {"exit_code": result.returncode, "output_sha256": digest(run / f"{name}-tests.txt")}


def validate_review(review, candidate_hash):
    require(review.get("candidate_sha256") == candidate_hash, "Review targets a different candidate")
    require(review.get("verdict") == "pass" and review.get("tests_passed") is True, "Review has not passed")
    require(isinstance(review.get("findings"), list), "Review findings must be a list")
    for finding in review["findings"]:
        require(isinstance(finding, dict) and finding.get("severity") in
                {"critical", "high", "medium", "low", "info"}, "Unknown finding severity")
        require(finding["severity"] not in {"critical", "high"}, "High/critical security finding blocks delivery")
    require(bool(review.get("summary")), "Review summary missing")


def checkpoint(run, stage, task_id):
    manifest = read_json(run / "manifest.json")
    target = next(s for s in STAGES if s["id"] == stage)
    entry = {"stage": stage, "task_id": task_id, "checked_at": time.time(), "passed": False}
    try:
        found = next((t for t in native_tasks(manifest) if t["id"] == task_id), None)
        require(found is not None, "Task missing from this run's latest native snapshot or belongs to another run. "
                "After spawn_continue, use the NEW returned task id, not the original conversation id; "
                "see live-state.json stages[].task.id.")
        require(found.get("agent") == target["agent"] and marker(manifest, stage) in found["task"],
                "Wrong agent or stage marker")
        require(found.get("done") and found.get("outcome") == "completed"
                and not found.get("error") and not found.get("stopped"), "Native task has not completed successfully")
        route = route_for(found)
        require(route and route.get("actual_backend") == target["backend"]
                and route.get("agent") == target["agent"], "Actual ACP factory backend is not verified")
        require(route.get("native_session_id"), "Native tool session id is not yet recorded")
        entry.update(native=found, route=route)
        for previous in STAGE_IDS[:STAGE_IDS.index(stage)]:
            require(read_json(run / f"gates/{previous}.json", {}).get("passed"),
                    f"Previous stage {previous} has not passed")
        require(digest(run / "baseline/core.mjs") == manifest["baseline_sha256"], "Baseline was changed")
        if manifest.get("schema") == 2:
            require(manifest.get("request_sha256"), "User requirements have not been captured")
            require(digest(run / "workspace/REQUEST.md") == manifest["request_sha256"], "User requirements were changed")
            require(digest(run / "workspace/TASK.md") == manifest["task_sha256"], "Task constraints were changed")
            if stage != "plan":
                frozen = read_json(run / "gates/plan.json")["frozen"]
                for name, expected in frozen.items():
                    require(digest(run / "workspace" / name) == expected, f"Frozen planning artifact changed: {name}")
                entry["requirements_sha256"] = manifest["request_sha256"]
        else:
            require(digest(run / "workspace/tests/acceptance.test.mjs") == manifest["acceptance_sha256"],
                    "Fixed acceptance tests were changed")
        if stage == "plan":
            require((run / "workspace/plan.md").stat().st_size > 100, "Missing substantive plan.md")
            if manifest.get("schema") == 2:
                require(digest(run / "workspace/web/core.mjs") == manifest["baseline_sha256"],
                        "Planner must not implement the core")
                require((run / "workspace/SPEC.md").stat().st_size > 100, "Missing substantive SPEC.md")
                acceptance = run / "workspace/tests/acceptance.test.mjs"
                test_source = acceptance.read_text()
                require("node:test" in test_source and "node:assert" in test_source
                        and "core.mjs" in test_source and len(re.findall(r"\btest\s*\(", test_source)) >= 3,
                        "Planner must provide at least three node:test cases against the real core")
                require(not re.search(r"\b(?:skip|todo)\s*[:.(]", test_source), "Skipped/todo acceptance tests are not allowed")
                node = shutil.which("node") or "/opt/homebrew/bin/node"
                syntax = subprocess.run([node, "--check", str(acceptance)], capture_output=True, text=True, timeout=15)
                require(syntax.returncode == 0, "Generated acceptance test has invalid JS syntax")
                red = subprocess.run([node, "--test", str(acceptance)], cwd=run / "workspace",
                                     capture_output=True, text=True, timeout=40)
                (run / "plan-tests.txt").write_text(red.stdout + red.stderr)
                require(red.returncode != 0, "Acceptance tests must fail against the empty baseline")
                entry["baseline_test_exit_code"] = red.returncode
                entry["frozen"] = {
                    name: digest(run / "workspace" / name)
                    for name in ("SPEC.md", "plan.md", "tests/acceptance.test.mjs")
                }
                entry["requirements_sha256"] = manifest["request_sha256"]
        else:
            candidate = digest(run / "workspace/web/core.mjs")
            entry["candidate_sha256"] = candidate
            if stage == "code":
                prior_review = read_json(run / "gates/review.json", {})
                for name, expected in prior_review.get("review_test_hashes", {}).items():
                    require(digest(run / "workspace/tests" / name) == expected,
                            f"Review regression test changed during repair: {name}")
            if stage == "review":
                # Preserve newly discovered regressions even when this review fails.
                entry["review_test_hashes"] = {p.name: digest(p) for p in (run / "workspace/tests").glob("*.test.mjs")}
            entry["tests"] = tests(run, stage)
            if stage == "code":
                diff = subprocess.run(
                    ["git", "diff", "--no-index", "--", str(run / "baseline/core.mjs"),
                     str(run / "workspace/web/core.mjs")], text=True, capture_output=True, timeout=10)
                require(diff.returncode == 1 and diff.stdout, "No real code change")
                (run / "candidate.diff").write_text(diff.stdout)
                entry["diff_sha256"] = digest(run / "candidate.diff")
            else:
                code = read_json(run / "gates/code.json")
                require(candidate == code["candidate_sha256"], "Reviewed core changed after coding")
                require(digest(run / "candidate.diff") == code["diff_sha256"], "Candidate diff was changed")
                review = read_json(run / "workspace/review.json", {})
                validate_review(review, candidate)
                entry["review"] = review
                require((run / "workspace/review.md").stat().st_size > 80, "Missing review.md")
                if stage == "review":
                    entry["review_sha256"] = digest(run / "workspace/review.json")
                    entry["test_hashes"] = {p.name: digest(p) for p in (run / "workspace/tests").glob("*.test.mjs")}
                else:
                    reviewed = read_json(run / "gates/review.json")
                    require(digest(run / "workspace/review.json") == reviewed["review_sha256"], "Review changed after approval")
                    require({p.name: digest(p) for p in (run / "workspace/tests").glob("*.test.mjs")}
                            == reviewed["test_hashes"], "Tests changed during delivery")
                    files = ["web/index.html", "web/style.css", "web/ui.mjs", "delivery.md"]
                    for name in files:
                        require((run / "workspace" / name).stat().st_size > 40, f"Missing deliverable: {name}")
                    require("core.mjs" in (run / "workspace/web/ui.mjs").read_text(), "UI must import reviewed core")
                    entry["artifacts"] = {name: digest(run / "workspace" / name) for name in files}
                    correction = read_json(run / "documentation-correction.json", {})
                    if correction:
                        require(correction.get("corrected_sha256") == entry["artifacts"]["delivery.md"],
                                "Document correction hash does not match delivery.md")
                        entry["documentation_correction"] = correction
        entry["passed"] = True
    except Exception as exc:
        entry["error"] = str(exc)
    previous_gate = read_json(run / f"gates/{stage}.json", {})
    changed = (stage == "code" and previous_gate.get("candidate_sha256")
               and previous_gate["candidate_sha256"] != entry.get("candidate_sha256")) or (
               stage == "plan" and previous_gate.get("frozen") and previous_gate["frozen"] != entry.get("frozen"))
    if entry["passed"] and changed:
        for downstream in STAGE_IDS[STAGE_IDS.index(stage)+1:]:
            path = run / f"gates/{downstream}.json"
            saved = read_json(path, {})
            if saved:
                write_json(path, {**saved, "passed": False,
                                  "error": f"{stage} produced a new version; {downstream} must run again",
                                  "invalidated_by": task_id, "invalidated_at": time.time()})
    write_json(run / f"gates/{stage}.json", entry)
    with (run / "gate-history.jsonl").open("a") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    report(run)
    return entry


def report(run):
    manifest = read_json(run / "manifest.json")
    gates = [read_json(run / f"gates/{s}.json", {}) for s in STAGE_IDS]
    complete = all(g.get("passed") for g in gates)
    browser = read_json(run / "browser-check.json", {})
    body = {
        "run": manifest["id"], "title": manifest["title"], "updated_at": time.time(),
        "pipeline_passed": complete, "browser_check": browser,
        "documentation_correction": read_json(run / "documentation-correction.json", {}),
        "input": {"request_sha256": manifest.get("request_sha256"),
                  "requirements": (run / "workspace/REQUEST.md").read_text()
                  if (run / "workspace/REQUEST.md").is_file() else None},
        "frozen_plan": gates[0].get("frozen", {}),
        "stages": gates,
        "scope": "Codex reviewed the core candidate. OpenCode UI is delivered afterward; browser QA is recorded separately.",
    }
    write_json(run / "report.json", body)
    lines = [
        "# KiroCrew 四工具 Pipeline 状态报告", "",
        f"- Run：{manifest['id']}", f"- App 父会话：{manifest['parent']}",
        f"- 阶段验收：{'4/4 通过' if complete else str(sum(bool(g.get('passed')) for g in gates)) + '/4 通过'}",
        f"- 浏览器验收：{'通过' if browser.get('passed') else '尚未通过或未执行'}",
        "", "| 阶段 | Coding Tool | 原生任务 | 验收 |", "|---|---|---|---|",
    ]
    for stage, gate in zip(STAGES, gates):
        lines.append(f"| {stage['title']} | {stage['tool']} | {gate.get('task_id','—')} | "
                     f"{'通过' if gate.get('passed') else gate.get('error','待执行')} |")
    lines += [
        "", "## 交接机制", "",
        "App 父会话 → 原生 MCP spawn_run → Gateway / SubagentManager → "
        "ProviderRegistry → 对应 ACP Backend → 原生完成事件回到同一父会话 → checkpoint → 下一阶段。",
        "", "宿主观察器只读 Gateway 的任务/会话/审批接口，不分派任务；Agent 内验收读取本次新鲜快照，不使用 owner token。",
        "", "## 证据与范围", "",
        "- candidate.diff / code-tests.txt：真实代码差异与运行测试。",
        "- gates/*.json：任务归属、实际 factory 后端、候选哈希、阶段门槛。",
        "- gate-history.jsonl：全部验收尝试，包含失败记录和随后修复的结果。",
        "- workspace/review.json：Codex 对核心候选的安全与测试审查。",
        "- workspace/web/：OpenCode 完成的网页；UI 不在之前的 Codex 审查范围内。",
        "- browser-check.json：交付后的独立浏览器验证（如果已经执行）。",
        "- documentation-correction.json（若存在）：宿主文档校正及原文哈希，不混同为工作者原始输出。",
        "- 这是本地 PoC；不表示生产环境安全认证或通用工作流引擎。",
    ]
    (run / "report.md").write_text("\n".join(lines) + "\n")
    esc = lambda value: html.escape(str(value))
    rows = "".join(
        f"<tr><td>{esc(stage['title'])}</td><td>{esc(stage['tool'])}</td>"
        f"<td><code>{esc(gate.get('task_id','—'))}</code></td>"
        f"<td class=\"{'ok' if gate.get('passed') else 'pending'}\">"
        f"{esc('通过' if gate.get('passed') else gate.get('error','待执行'))}</td></tr>"
        for stage, gate in zip(STAGES, gates)
    )
    candidate = next((g["candidate_sha256"] for g in gates if "candidate_sha256" in g), "尚未生成")
    (run / "report.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>KiroCrew · 状态报告</title>'
        '<style>*{box-sizing:border-box}body{max-width:1050px;margin:45px auto;padding:24px;'
        'font:14px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#243b5b;background:#f5f7fb}'
        'main{background:white;border:1px solid #e1e7f0;border-radius:16px;padding:36px}'
        'h1{font-size:30px;margin:8px 0}h2{font-size:17px;margin-top:30px}'
        '.eyebrow{letter-spacing:2px;font-size:10px;color:#6582aa}.muted{color:#8493a9;font-size:12px}'
        '.summary{display:flex;gap:18px;margin:28px 0}.metric{flex:1;border:1px solid #e0e8f5;'
        'background:#f8faff;border-radius:9px;padding:20px}.metric strong{font-size:24px;display:block;color:#285dda}'
        'table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:13px 8px;'
        'border-bottom:1px solid #e6ebf3}th{color:#8a98ad;font-weight:500}.ok{color:#198259}.pending{color:#96722d}'
        'code{font:11px/1.8 ui-monospace,monospace;overflow-wrap:anywhere}.hash{background:#f6f8fc;padding:12px;'
        'border-radius:7px}.scope{padding:18px;border-left:3px solid #3e71d8;background:#f3f7ff;font-size:12px}'
        'a{color:#285dda;text-decoration:none}.flow{padding:18px;background:#f7f9fc;border-radius:8px;font-size:12px}'
        '@media(max-width:600px){body{margin:0;padding:14px}main{padding:20px}h1{font-size:25px}'
        '.summary{gap:8px}.metric{padding:12px}.metric strong{font-size:19px}td,th{padding:10px 4px}}</style>'
        '<main><div class="eyebrow">KIROCREW / DELIVERY REPORT</div>'
        f"<h1>{esc(manifest['title'])} · 执行报告</h1>"
        f"<p class=\"muted\">{esc(manifest['id'])}<br>App 父会话：{esc(manifest['parent'])}</p>"
        f'<section class="summary"><div class="metric"><strong>{sum(bool(g.get("passed")) for g in gates)} / 4</strong>阶段验收</div>'
        f'<div class="metric"><strong>{"通过" if browser.get("passed") else "待验证"}</strong>浏览器验收</div>'
        f'<div class="metric"><strong>{"已交付" if complete else "未完成"}</strong>Pipeline</div></section>'
        '<table><thead><tr><th>阶段</th><th>Coding Tool</th><th>原生任务 ID</th><th>验收</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        '<h2>候选版本</h2><p class="muted">审查与交付阶段核对同一核心代码 SHA-256。</p>'
        f'<div class="hash"><code>{esc(candidate)}</code></div>'
        '<h2>交互流程</h2><div class="flow">App 父会话 → 原生 MCP → Gateway / SubagentManager '
        '→ ACP 工作者 → 完成事件回到父会话 → 确定性验收 → 下一阶段</div>'
        '<h2>验证范围</h2><p class="scope">Codex 审查 Claude 生成的核心代码、真实 diff 和测试。'
        'OpenCode 在此之后制作网页，浏览器验收单独记录。此报告不代表生产环境安全认证。</p>'
        '<h2>可复查证据</h2><p><a href="candidate.diff">真实 diff</a> · '
        '<a href="code-tests.txt">编码测试</a> · <a href="review-tests.txt">审查测试</a> · '
        '<a href="deliver-tests.txt">交付测试</a> · <a href="gate-history.jsonl">完整验收历史</a></p>'
        '<p><a href="report.json">结构化 JSON 报告</a> · <a href="report.md">Markdown 报告</a> · '
        '<a href="browser-check.json">浏览器验收记录</a></p>'
        f'<p class="muted">生成时间：{esc(time.strftime("%Y-%m-%d %H:%M:%S %Z"))}。'
        '报告保留生成时状态；实时进度请查看正在运行的看板。</p></main></html>'
    )
    return body


def project_state(run, tasks, parent, approvals, connected=True, error=None):
    manifest = read_json(run / "manifest.json")
    own = [task_identity(t) for t in tasks if t.get("parent") == manifest["parent"]
           and any(marker(manifest, s) in t.get("task", "") for s in STAGE_IDS)]
    stages = []
    for stage in STAGES:
        matched = [t for t in own if t.get("agent") == stage["agent"]
                   and marker(manifest, stage["id"]) in t.get("task", "")]
        task = max(matched, key=lambda t: t.get("started", 0), default=None)
        gate = read_json(run / f"gates/{stage['id']}.json", {})
        status = "pending"
        if task:
            status = ("awaiting_approval" if task.get("awaiting_approval") else "running") if not task["done"] else (
                "failed" if task.get("error") or task.get("stopped") or task.get("outcome") != "completed" else "checking")
        if gate and (not task or gate.get("task_id") == task["id"]):
            status = "passed" if gate.get("passed") else "blocked"
        stages.append({**stage, "status": status, "task": task, "gate": gate,
                       "route": route_for(task) if task else gate.get("route")})
    identities = {manifest["slot"], manifest["parent"]} | {f"subagent:{t['id']}" for t in own}
    # Match exact identities; never display another conversation's approvals.
    own_approvals = [a for a in approvals if any(str(a.get(k, "")) in identities
                     for k in ("slot", "slot_key", "session", "session_key", "parent_session_key"))]
    # Native ACP tool approvals in the parent are persisted as chat records,
    # separately from /api/approvals. Resolved records must not look pending.
    for message in parent.get("messages", []):
        if message.get("role") != "permission":
            continue
        try:
            permission = json.loads(message.get("cls", "{}"))
        except (ValueError, TypeError):
            continue
        if permission.get("request_id") and not permission.get("resolved"):
            own_approvals.append({
                "id": permission["request_id"], "slot": manifest["slot"],
                "title": permission.get("tool_title"), "source": "native-parent-chat",
            })
    done = sum(s["status"] == "passed" for s in stages)
    status = "delivered" if done == 4 else (
        "blocked" if any(s["status"] in {"failed", "blocked"} for s in stages) else
        "awaiting_approval" if own_approvals or any(s["status"] == "awaiting_approval" for s in stages) else
        "running" if own or parent.get("running") else "ready")
    if status == "ready" and manifest.get("schema") == 2 and not manifest.get("request_sha256"):
        status = "awaiting_input"
    files = [name for name in (
        "workspace/REQUEST.md", "workspace/SPEC.md", "workspace/tests/acceptance.test.mjs", "plan-tests.txt",
        "workspace/plan.md", "candidate.diff", "code-tests.txt", "review-tests.txt",
        "workspace/review.md", "workspace/review.json", "workspace/delivery.md",
        "deliver-tests.txt", "browser-check.json", "gate-history.jsonl", "report.md", "report.json", "report.html",
    ) if (run / name).is_file()]
    return {
        "manifest": manifest, "connected": connected, "error": error,
        "observed_at": time.time(), "status": status, "passed_count": done,
        "parent_running": bool(parent.get("running")), "stages": stages,
        "approvals": own_approvals, "files": files,
        "preview_ready": stages[-1]["status"] == "passed" and (run / "workspace/web/index.html").is_file(),
        "browser_check": read_json(run / "browser-check.json", {}),
        "intake": read_json(run / "intake.json", {}),
        "requirements": (run / "workspace/REQUEST.md").read_text() if (run / "workspace/REQUEST.md").is_file() else "",
    }


class Observer:
    def __init__(self, run):
        self.run = run
        self.client = api.ReadOnlyGateway()
        self.lock = threading.Lock()
        self.state = read_json(run / "live-state.json", project_state(run, [], {}, [], False, "正在连接 Gateway"))

    def refresh(self):
        manifest = read_json(self.run / "manifest.json")
        paths = ["/api/spawn", f"/api/chat/slots/{manifest['slot']}", "/api/approvals"]
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(self.client.get, paths))
            state = project_state(self.run, results[0]["agents"], results[1], results[2])
            state["last_success_at"] = time.time()
        except Exception as exc:
            with self.lock:
                state = {**self.state, "connected": False, "error": str(exc), "observed_at": time.time()}
        with self.lock:
            self.state = state
        write_json(self.run / "live-state.json", state)

    def loop(self):
        while True:
            self.refresh()
            time.sleep(2)


def public_file(run, path):
    """Explicit allowlist, no directory serving and no symlink escapes."""
    assets = {"/": ROOT / "ui/live.html", "/live.css": ROOT / "ui/live.css", "/live.js": ROOT / "ui/live.js"}
    if path in assets:
        return assets[path]
    if path in {f"/preview/{name}" for name in ("index.html", "style.css", "ui.mjs", "core.mjs")}:
        candidate = run / "workspace/web" / path.rsplit("/", 1)[1]
    elif path.startswith("/files/"):
        relative = path.removeprefix("/files/")
        allowed = {
            "workspace/REQUEST.md", "workspace/SPEC.md", "workspace/tests/acceptance.test.mjs", "plan-tests.txt",
            "workspace/plan.md", "candidate.diff", "code-tests.txt", "review-tests.txt",
            "workspace/review.md", "workspace/review.json", "workspace/delivery.md",
            "deliver-tests.txt", "browser-check.json", "gate-history.jsonl", "report.md", "report.json", "report.html",
            "app-prompt.txt", *(f"gates/{s}.json" for s in STAGE_IDS),
        }
        if relative not in allowed:
            return None
        candidate = run / relative
    else:
        return None
    return candidate if candidate.resolve().is_relative_to(run.resolve()) else None


def serve(run, port):
    observers = {}
    registry_lock = threading.Lock()
    intake_lock = threading.Lock()
    receipts = ROOT / "state/intake-requests"
    receipts.mkdir(parents=True, exist_ok=True)

    def observe(target):
        with registry_lock:
            if target.name not in observers:
                observer = Observer(target)
                observers[target.name] = observer
                threading.Thread(target=observer.loop, daemon=True).start()
            return observers[target.name]

    if run:
        observe(run)
    # Preserve observation of in-flight runs when an operator reopens the launcher.
    for snapshot_path in RUNS.glob("*/live-state.json"):
        snapshot = read_json(snapshot_path, {})
        if snapshot.get("status") in {"running", "awaiting_approval", "blocked"} or snapshot.get("parent_running"):
            observe(run_path(snapshot_path.parent))

    def runs():
        rows = []
        for file in RUNS.glob("*/manifest.json"):
            value = read_json(file, {})
            if value.get("slot") == value.get("id") and value.get("stages"):
                rows.append({k: value.get(k) for k in ("id", "title", "created_at")})
        return sorted(rows, key=lambda v: v.get("created_at", 0), reverse=True)

    class Handler(BaseHTTPRequestHandler):
        def allowed(self, mutation=False):
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            # Generated product pages use localhost; intake lives on 127.0.0.1.
            # They have different browser origins, so product JS cannot submit tasks.
            if mutation:
                return (host == f"127.0.0.1:{port}" and origin == f"http://127.0.0.1:{port}"
                        and self.headers.get("X-Pipeline-Intake") == "1"
                        and self.headers.get("Content-Type", "").split(";")[0] == "application/json")
            return (host in {f"127.0.0.1:{port}", f"localhost:{port}"}
                    and origin in {None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"})

        def target(self):
            values = parse_qs(urlsplit(self.path).query)
            scoped = re.match(r"^/r/(pipeline-[a-zA-Z0-9-]+)/", urlsplit(self.path).path)
            selected = scoped.group(1) if scoped else values.get("run", [None])[0]
            if selected:
                require(bool(re.fullmatch(r"pipeline-[a-zA-Z0-9-]+", selected)), "Invalid run")
                return run_path(RUNS / selected)
            pointer = read_json(ROOT / "state/current-pipeline.json", {})
            return run_path(pointer["run"]) if pointer.get("run") else None

        def respond(self, data, kind="application/json", status=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not self.allowed(mutation=True):
                self.respond({"error": "Intake requires the local dashboard origin"}, status=403)
                return
            if urlsplit(self.path).path != "/intake":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                require(0 < size <= 65536, "Invalid request size")
                body = json.loads(self.rfile.read(size))
                require(isinstance(body, dict), "Expected JSON object")
                key = body.get("request_id", "")
                require(isinstance(key, str) and bool(re.fullmatch(r"[a-zA-Z0-9-]{16,64}", key)), "Invalid request id")
                mode = body.get("mode", "send")
                require(mode in {"send", "app"}, "Invalid intake mode")
                text, title = body.get("requirements", ""), body.get("title", "")
                if mode == "send":
                    text, title = validate_request(text, title)
                else:
                    require(isinstance(title, str) and len(title) <= 80, "Invalid title")
                fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                record = receipts / f"{key}.json"
                with intake_lock:
                    previous = read_json(record, {})
                    if previous:
                        require(previous.get("fingerprint") == fingerprint, "Request id already belongs to different input")
                        self.respond(previous, status=200 if previous.get("run") else 409)
                        return
                    write_json(record, {"fingerprint": fingerprint, "status": "preparing"})
                    manifest = new_run(text if mode == "send" else None, title)
                    observe(Path(manifest["run"]))
                    intake = submit_run(manifest) if mode == "send" else read_json(Path(manifest["run"]) / "intake.json")
                    result = {"fingerprint": fingerprint, "run": manifest["id"], "intake": intake,
                              "app_url": f"http://localhost:5476/chat?sid={manifest['slot']}"}
                    write_json(record, result)
                self.respond(result, status=201)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.respond({"error": str(exc)}, status=400)
            except Exception as exc:
                self.respond({"error": str(exc), "guidance": "请检查 App 会话。已创建的数据会保留；不要盲目重复发送。"}, status=502)

        def do_GET(self):
            if not self.allowed():
                self.send_error(403)
                return
            path = unquote(urlsplit(self.path).path)
            path = re.sub(r"^/r/pipeline-[a-zA-Z0-9-]+/", "/", path)
            host = self.headers.get("Host")
            if path.startswith("/preview/") and host == f"127.0.0.1:{port}":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{port}" + self.path)
                self.end_headers()
                return
            if host == f"localhost:{port}" and not path.startswith("/preview/"):
                self.send_error(403)
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            try:
                target = self.target()
                if path == "/state.json":
                    if target:
                        observer = observe(target)
                        with observer.lock:
                            data = {**observer.state, "runs": runs()}
                    else:
                        data = {"manifest": None, "runs": runs()}
                    self.respond(data)
                    return
                if path in {"/", "/live.css", "/live.js"}:
                    file = public_file(target, path)
                else:
                    file = public_file(target, path) if target else None
                if not file or not file.is_file():
                    self.send_error(404)
                    return
                kind = "text/javascript" if file.suffix == ".mjs" else mimetypes.guess_type(file.name)[0] or "text/plain"
                self.respond(file.read_bytes(), kind)
            except (ValueError, OSError):
                self.send_error(404)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    write_json(ROOT / "state/pipeline-server.json", {"pid": os.getpid(), "port": port, "run": str(run) if run else None})
    print(f"Pipeline intake / observer: http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["new", "new-demo", "capture", "checkpoint", "serve", "report", "status"])
    parser.add_argument("run", nargs="?")
    parser.add_argument("--stage", choices=STAGE_IDS)
    parser.add_argument("--task-id")
    parser.add_argument("--port", type=int, default=8916)
    parser.add_argument("--requirements-file")
    parser.add_argument("--title", default="")
    args = parser.parse_args()
    if args.command in {"new", "new-demo"}:
        text = Path(args.requirements_file).read_text() if args.requirements_file else None
        value = new_demo_run() if args.command == "new-demo" else new_run(text, args.title)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        print(f"\n在 KiroCrew App 打开 http://localhost:5476/chat?sid={value['slot']}。")
        print(f"旧固定演示：粘贴 {value['run']}/app-prompt.txt 后发送。"
              if args.command == "new-demo" else "直接输入开发需求，无需编辑计划或复制任务书。")
        return
    if not args.run:
        args.run = read_json(ROOT / "state/current-pipeline.json", {}).get("run")
    if args.command == "serve" and not args.run:
        serve(None, args.port)
        return
    run = run_path(args.run)
    if args.command == "serve":
        serve(run, args.port)
    elif args.command == "checkpoint":
        if not args.stage or not args.task_id:
            parser.error("checkpoint needs --stage and --task-id")
        value = checkpoint(run, args.stage, args.task_id)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        raise SystemExit(0 if value["passed"] else 1)
    elif args.command == "report":
        print(json.dumps(report(run), ensure_ascii=False, indent=2))
    elif args.command == "capture":
        print(json.dumps(capture_request(run, (run / "workspace/request-input.txt").read_text(), args.title),
                         ensure_ascii=False, indent=2))
    else:
        observer = Observer(run)
        observer.refresh()
        print(json.dumps(observer.state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
