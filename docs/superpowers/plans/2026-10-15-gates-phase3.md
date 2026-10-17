# 门禁 第 3 期（D37）：`tests_fail_on_baseline` 与 `artifacts_locked` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 测试任务新增的测试必须在会话基线上失败（证明它们测试了新行为）；已验收任务的交付物只能由在 `paths` 中列出它们的后续任务修改。两者都是命名门禁，失败进入自动纠错。

**Architecture:**
- **`sources`**：候选结果带改动的文件名；`baseline_apply` / `baseline_restore` 用 `git read-tree -u --reset` 在项目目录原地切换已跟踪的文件，未跟踪的依赖不动。
- **`Checks`**：任务可带「运行前 / 运行后」两个步骤，运行后的步骤在 `finally` 中执行。
- **Worker**：
  - `/verify/start` 的 `baseline` 模式：写标记，切到基线并叠加测试文件，运行，恢复；
  - 对该会话的下一次操作之前，按遗留的标记恢复；
  - `/health` 带 `baseline_checks: true`。
- **Engine**：
  - `artifacts_locked` 在 `no_secrets` 之后由平台判定；
  - `tests_fail_on_baseline` 在 `checks_pass` 通过之后启动第二个检查任务，结束后判定。

**Tech Stack:** Python 3 标准库 + 主机 git。

**Spec:** `docs/superpowers/specs/2026-10-14-candidates-gates-report-design.md` §11、§11.1、§11.2

## Global Constraints

- 基线 = Worker 项目记录的 `commit`（会话准备时的基线）。
- 叠加的文件 = `git diff --name-only --no-renames -z <parent> <候选>`：新增或修改的取候选中的版本，删除的从工作区删去。
- 恢复 = `git read-tree -u --reset <候选>`，必在 `finally` 中执行；标记 `<run>/baseline.json` 写 `{"candidate": sha}`，恢复后删除。
  - 派发、候选、检查、发布、下载与预览之前，若标记存在，先恢复再继续。
- 判定 `tests_fail_on_baseline`：
  - 基线全部通过 → `fail`：「新增的测试在改动之前（基线 abc1234）也通过，不能证明新行为」；
  - 有命令失败 → `pass`：「基线上失败：<命令>（退出码 n）」；
  - 切换失败（`error`）、Worker 重启（`check_unknown`）、Worker 不支持（未回复 `baseline: true`）→ `skip`，写明原因；不重试。
- 判定 `artifacts_locked`：
  - 当前任务没有 `paths` → `skip`「任务未声明可改动路径（paths）」；
  - 候选没有改动 → 不记录（`diff_present` 已说明）；
  - 此前的已验收任务都没有记录文件名 → `skip`「此前的已验收任务没有记录文件」；
  - 有违规 → `fail`「改动了已验收任务「X」的交付物：a、b；如需修改，请在本任务的 paths 中列出」（最多列 10 个）；
  - 否则 `pass`。
- `paths` 匹配：以 `/` 或 `/**` 结尾为目录前缀；否则 `fnmatch.fnmatchcase`、完全相同，或以「项/」开头。项先去掉开头的 `./`。
- 候选的文件名最多 200 个，超出时 `names_truncated: true`。被截断的此前任务只按已记录的文件判定。

## Review Focus

1. **恢复必然发生**：基线运行中命令超时、被停止、抛出异常，或切换本身失败时，工作区都回到候选（已跟踪文件与索引），未跟踪的依赖不动。（`test_the_workspace_is_back_on_the_candidate_whatever_happens`）
2. **Worker 重启**：标记遗留时，下一次派发前恢复到候选。（`test_a_leftover_baseline_is_undone_before_the_next_dispatch`）
3. **旧 Worker**：不认识 `baseline` 的 Worker 会把命令跑在候选上并通过；平台必须跳过，不得据此判为失败。（`test_an_old_worker_skips_the_baseline_gate`）
4. **模板任务**：没有 `paths` 的任务修改此前任务的文件时不被阻断。（`test_a_task_without_paths_is_not_locked`）
5. **删除的测试**：测试任务删除了文件时，基线上也不存在该文件。（`test_the_overlay_follows_the_test_tasks_changes`）

---

### Task 1: `sources` 与 `Checks`：文件名、基线切换与恢复

**Files:**
- Modify: `octopus/sources.py`（`task_candidate` 返回 `names`；新增 `baseline_apply`、`baseline_restore`）
- Modify: `octopus/checks.py`（`start(..., kind="verify", around=None)`；`status` 带 `error`）
- Test: `tests/test_baseline.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
"""D37 phase 3 §11.1: a test task's tests run on the Session's baseline, in place (its untracked dependencies stay),
and the workspace always returns to the candidate."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from octopus import sources
from octopus.checks import Checks
from tests.test_archive import RID, make_worker


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=True).stdout.strip()


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node, self.project = make_worker(Path(self.tmp.name))
        self.base = sources.read_record(self.node.path(RID))["commit"]

    def candidate(self, files, remove=(), parent=None):
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)
        for name in remove:
            (self.project / name).unlink()
        return self.node.candidate({"run_id": RID, "attempt_id": "attempt-1", "mode": "commit", "message": "候选",
                                    **({"parent": parent} if parent else {})})

    def test_the_candidate_names_its_files(self):
        result = self.candidate({"src/a.py": "a = 1\n", "tests/test_a.py": "x\n"})
        self.assertEqual(sorted(result["names"]), ["src/a.py", "tests/test_a.py"])
        self.assertNotIn("names_truncated", result)

    def test_the_overlay_follows_the_test_tasks_changes(self):
        code = self.candidate({"src/a.py": "a = 1\n", "tests/old_test.py": "old\n"})
        tests = self.candidate({"tests/test_a.py": "new\n"}, remove=["tests/old_test.py"], parent=code["sha"])
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules/dep.js").write_text("dep")   # untracked: stays through both switches
        sources.baseline_apply(self.project, self.base, tests["parent"], tests["sha"])
        self.assertFalse((self.project / "src/a.py").exists())            # the implementation is not on the baseline
        self.assertEqual((self.project / "tests/test_a.py").read_text(), "new\n")   # the new test is laid on top
        self.assertFalse((self.project / "tests/old_test.py").exists())   # what the test task deleted is gone
        self.assertTrue((self.project / "node_modules/dep.js").exists())
        sources.baseline_restore(self.project, tests["sha"])
        self.assertEqual((self.project / "src/a.py").read_text(), "a = 1\n")
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), tests["sha"])
        self.assertTrue((self.project / "node_modules/dep.js").exists())

    def test_the_workspace_is_back_on_the_candidate_whatever_happens(self):
        result = self.candidate({"src/a.py": "a = 1\n", "tests/test_a.py": "t\n"})
        cases = (["exit 0"], ["exit 3"], ["sleep 30"])
        for commands in cases:
            with self.subTest(commands=commands):
                checks = Checks(per_command=1, total=2)
                applied = []
                job = checks.start(RID, "attempt-" + commands[0][:4], self.project, commands, kind="baseline",
                                   around=(lambda: (applied.append(1), sources.baseline_apply(
                                       self.project, self.base, self.base, result["sha"])),
                                           lambda: sources.baseline_restore(self.project, result["sha"])))["job"]
                while checks.status(job)["state"] != "done":
                    time.sleep(0.05)
                self.assertEqual(applied, [1])
                self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")
                self.assertTrue((self.project / "src/a.py").exists())

    def test_a_switch_that_fails_is_an_error_not_a_failing_baseline(self):
        result = self.candidate({"src/a.py": "a = 1\n"})
        checks = Checks()
        def broken():
            raise ValueError("无法切换")
        job = checks.start(RID, "attempt-x", self.project, ["exit 1"], kind="baseline",
                           around=(broken, lambda: sources.baseline_restore(self.project, result["sha"])))["job"]
        while checks.status(job)["state"] != "done":
            time.sleep(0.05)
        status = checks.status(job)
        self.assertIn("无法切换", status["error"])
        self.assertEqual(status["results"], [])

    def test_a_baseline_job_is_separate_from_the_attempts_checks(self):
        checks = Checks()
        first = checks.start(RID, "attempt-1", self.project, ["exit 0"])["job"]
        second = checks.start(RID, "attempt-1", self.project, ["exit 0"], kind="baseline")["job"]
        self.assertNotEqual(first, second)
```

- [ ] **Step 2: 运行，确认失败**：`$PY -m unittest tests.test_baseline -v`。Expected: FAIL（`names` 缺失、`baseline_apply` 不存在、`start` 不接受 `kind`）。

- [ ] **Step 3: 实现**

`sources.task_candidate`：在 numstat 循环中收集 `fields[2].decode(errors="replace")`，返回值加
`"names": names[:200]`，超出 200 个时再加 `"names_truncated": True`。

新增（放在 `clean_against` 之前）：

```python
def baseline_apply(project, base, parent, candidate):
    """Lay a test task's changes on the Session's baseline, in place (D37 §11.1): tracked files become ``base`` (the
    untracked ones — dependencies, virtual environments — stay), then the files the task changed between ``parent`` and
    ``candidate`` are taken from the candidate, or removed where it deleted them. ``baseline_restore`` undoes it."""
    _git(project, "read-tree", "-u", "--reset", base)
    for entry in _git_bytes(project, "diff", "--name-status", "--no-renames", "-z", parent, candidate).split(b"\0\0"):
        pass   # replaced below: parse "status\0path\0" pairs
    raw = _git_bytes(project, "diff", "--name-status", "--no-renames", "-z", parent, candidate).split(b"\0")
    for status, path in zip(raw[0::2], raw[1::2]):
        name = path.decode(errors="surrogateescape")
        if not name:
            continue
        if status.startswith(b"D"):
            target = Path(project) / name
            if target.is_file() or target.is_symlink():
                target.unlink()
        else:
            _git(project, "checkout", candidate, "--", name)


def baseline_restore(project, candidate):
    """Tracked files and the index back to ``candidate``; untracked files untouched."""
    _git(project, "read-tree", "-u", "--reset", candidate)
```

（实现时删去占位的第一个 for 循环；`_git` / `_git_bytes` 为本文件已有的辅助函数，`Path` 已导入或在顶部导入。）

`Checks.start(self, run_id, attempt_id, project, commands, kind="verify", around=None)`：`by_attempt` 的键改为 `(run_id, attempt_id, kind)`，任务记录加 `"error": None`；`_run` 接收 `around`：

```python
    def _run(self, job, project, commands, around=None):
        ...
        passed, failed = True, None
        prepare, restore = around or (None, None)
        try:
            if prepare:
                try:
                    prepare()
                except Exception as exc:   # never judged as a failing run: the platform skips the gate
                    with self.lock:
                        value["error"] = "无法切换到基线：" + sources.scrub(exc)
                    passed = False
                    commands = []
            for index, command in enumerate(commands):
                ...（不变）
        finally:
            if restore:
                try:
                    restore()
                except Exception as exc:
                    with self.lock:
                        value["error"] = (value["error"] or "") + "；恢复失败：" + sources.scrub(exc)
            with self.lock:
                value.update(...)   # 不变
```

`status` 的返回值加 `"error": value["error"]`。清理过期任务时 `by_attempt.pop((value["run_id"], value["attempt_id"], value["kind"]))`（任务记录加 `"kind": kind`）。

- [ ] **Step 4: 运行，确认通过**：`$PY -m unittest tests.test_baseline tests.test_checks tests.test_candidates -v`。
- [ ] **Step 5: Commit** `feat(sources,checks): 候选记录改动的文件名；在项目目录原地切到会话基线并叠加测试文件、必然恢复（D37 §11.1）`

---

### Task 2: Worker 的基线模式与遗留标记的恢复

**Files:**
- Modify: `octopus/worker.py`（`verify_start` 的 `baseline`；`_undo_baseline(run)`；在派发、候选、检查、发布计划、下载、预览前调用；`/health` 的 `baseline_checks`）
- Test: `tests/test_baseline.py`（追加）

- [ ] **Step 1: 写失败的测试**（追加到 `BaselineTests`）

```python
    def baseline(self, tests, commands):
        return self.node.verify_start({"run_id": RID, "attempt_id": "attempt-t", "commands": commands,
                                       "baseline": {"candidate": tests["sha"], "parent": tests["parent"]}})

    def test_the_worker_runs_a_baseline_job_and_says_so(self):
        code = self.candidate({"src/a.py": "a = 1\n"})
        tests = self.candidate({"tests/test_a.py": "t\n"}, parent=code["sha"])
        started = self.baseline(tests, ["test -f src/a.py"])   # the implementation is missing on the baseline
        self.assertTrue(started["baseline"])
        while (status := self.node.verify_status({"job": started["job"]}))["state"] != "done":
            time.sleep(0.05)
        self.assertFalse(status["passed"])
        self.assertTrue((self.project / "src/a.py").exists())
        self.assertFalse((self.node.path(RID) / "baseline.json").exists())
        self.assertTrue(self.node.health()["baseline_checks"])

    def test_a_leftover_baseline_is_undone_before_the_next_dispatch(self):
        code = self.candidate({"src/a.py": "a = 1\n"})
        sources.baseline_apply(self.project, self.base, self.base, code["sha"])   # a Worker that died mid-run
        (self.node.path(RID) / "baseline.json").write_text('{"candidate": "%s"}' % code["sha"])
        self.node._undo_baseline(self.node.path(RID))
        self.assertTrue((self.project / "src/a.py").exists())
        self.assertFalse((self.node.path(RID) / "baseline.json").exists())

    def test_a_baseline_needs_full_commit_ids(self):
        with self.assertRaises(Exception):
            self.node.verify_start({"run_id": RID, "attempt_id": "attempt-t", "commands": ["true"],
                                    "baseline": {"candidate": "abc", "parent": "def"}})
```

另在 `test_a_leftover_baseline_is_undone_before_the_next_dispatch` 之外，用一条断言确认 `dispatch` 调用了恢复：在 `Worker.dispatch` 的实现中，`binding()` 之后第一步是 `self._undo_baseline(run)`。（若直接测试 dispatch 需要原生桩，可沿用 `tests/test_worker_approval.py` 的 `node.native` 替身。）

- [ ] **Step 2: 运行，确认失败**。
- [ ] **Step 3: 实现**

```python
    def _undo_baseline(self, run):
        """A baseline run the Worker did not finish (it restarted): the workspace back on the candidate first (D37 §11.1)."""
        marker = run / "baseline.json"
        if marker.is_file():
            candidate = json.loads(marker.read_text()).get("candidate")
            if isinstance(candidate, str) and workspace.SHA.fullmatch(candidate):
                workspace.baseline_restore(workspace.project_dir(run), candidate)
            marker.unlink(missing_ok=True)
```

`verify_start`：

```python
        run = self.path(body["run_id"])
        self._undo_baseline(run)
        project = workspace.project_dir(run)
        baseline = body.get("baseline")
        if baseline is None:
            return self.checks.start(body["run_id"], body["attempt_id"], project, commands)
        require(isinstance(baseline, dict) and all(isinstance(baseline.get(k), str) and workspace.SHA.fullmatch(baseline[k])
                                                   for k in ("candidate", "parent")), "基线检查须给出完整的提交 ID")
        base = workspace.read_record(run).get("commit")
        require(base, "此会话的项目没有 Git 基线", 409, "candidate_unsupported")
        marker = run / "baseline.json"
        def prepare():
            write(marker, {"candidate": baseline["candidate"]})
            workspace.baseline_apply(project, base, baseline["parent"], baseline["candidate"])
        def restore():
            workspace.baseline_restore(project, baseline["candidate"])
            marker.unlink(missing_ok=True)
        started = self.checks.start(body["run_id"], body["attempt_id"], project, commands, kind="baseline",
                                    around=(prepare, restore))
        return {**started, "baseline": True, "base": base}
```

在 `dispatch`（`binding` 之后）、`candidate`、`publish_plan`、`files`、`app` 的开头调用 `self._undo_baseline(run)`（以各方法中已有的 `run` 路径变量为准）。`/health` 加 `"baseline_checks": True`。

- [ ] **Step 4: 运行，确认通过**：`$PY -m unittest tests.test_baseline tests.test_candidates tests.test_archive tests.test_worker_approval tests.test_publish -v`。
- [ ] **Step 5: Commit** `feat(worker): 基线检查模式（标记、切换、必然恢复）；遗留的基线在下一次操作前恢复（D37 §11.1）`

---

### Task 3: Engine 的两个门禁

**Files:**
- Modify: `octopus/gates.py`（`covered(path, globs)`）
- Modify: `octopus/engine.py`（候选证据存 `names`；`_locked`；`_poll_checks` 之后启动基线；`_poll_baseline`）
- Test: `tests/test_engine_gates.py`（追加）、`tests/test_gates.py`（`covered`）

- [ ] **Step 1: 写失败的测试**

`tests/test_gates.py`：

```python
class CoveredTests(unittest.TestCase):
    def test_paths_match_directories_globs_and_exact_files(self):
        for path, globs, expected in (("src/a/b.py", ["src/"], True), ("src/a/b.py", ["src/**"], True),
                                      ("src/a/b.py", ["src/*.py"], True), ("src/a/b.py", ["src"], True),
                                      ("./README.md", ["README.md"], True), ("README.md", ["./README.md"], True),
                                      ("docs/a.md", ["src/"], False), ("srcx/a.py", ["src"], False)):
            with self.subTest(path=path, globs=globs):
                self.assertIs(gates.covered(path.lstrip("./") if path.startswith("./") else path, globs), expected)
```

`tests/test_engine_gates.py`（在 `EngineGateTests` 中追加；`FakeWorker` 的候选由测试改写）：

```python
    def test_a_later_task_may_not_change_an_accepted_tasks_files_unless_its_paths_say_so(self):
        worker = FakeWorker()
        names = {"code": ["src/a.py"], "docs": ["src/a.py", "README.md"]}
        shas = {"code": C1, "docs": "d" * 40}
        def candidate(body):
            task = "code" if worker.dispatched[body["attempt_id"]]["spec"]["id"] == "code" else "docs"
            return {"sha": shas[task], "parent": body.get("parent", BASE), "changed": True, "files": len(names[task]),
                    "insertions": 1, "deletions": 0, "rewritten": False, "secrets": [], "names": names[task]}
        worker.candidate = candidate
        docs = {"id": "docs", "name": "文档", "tool": "claude", "prompt": "写文档", "kind": "deliver",
                "acceptance": ["有文档"], "paths": ["README.md"]}
        rid = self.session([IMPLEMENT, docs])
        self.run_engine(worker, ticks=20)
        _, evidence = self.attempt(rid, "docs")
        self.assertEqual(self.gates(evidence).get("artifacts_locked"), "fail")
        failed = [g for g in evidence["gates"] if g["name"] == "artifacts_locked"][0]
        self.assertIn("「实现」的交付物：src/a.py", failed["detail"])

    def test_a_task_without_paths_is_not_locked(self):
        worker = FakeWorker()
        worker.candidate = lambda body: {"sha": C1, "parent": body.get("parent", BASE), "changed": True, "files": 1,
                                         "insertions": 1, "deletions": 0, "rewritten": False, "secrets": [], "names": ["src/a.py"]}
        deliver = {"id": "deliver", "name": "交付", "tool": "claude", "prompt": "交付", "kind": "deliver", "acceptance": ["可交付"]}
        rid = self.session([IMPLEMENT, deliver])
        self.run_engine(worker, ticks=20)
        _, evidence = self.attempt(rid, "deliver")
        self.assertEqual(self.gates(evidence).get("artifacts_locked"), "skip")

    def test_a_test_tasks_tests_must_fail_on_the_baseline(self):
        for baseline_passed, verdict in ((True, "fail"), (False, "pass")):
            with self.subTest(baseline_passed=baseline_passed):
                worker = FakeWorker()
                checks = {"state": "done", "passed": True, "failed": None, "error": None,
                          "results": [{"command": "npm test", "exit": 0, "seconds": 1.0, "tail": "ok"}]}
                baseline = {"state": "done", "passed": baseline_passed, "failed": None if baseline_passed else 0, "error": None,
                            "results": [{"command": "npm test", "exit": 0 if baseline_passed else 1, "seconds": 1.0, "tail": ""}]}
                jobs = {}
                original = worker.__call__
                def call(config, path, body=None, **kwargs):
                    if path == "/verify/start":
                        worker.calls.append((path, body))
                        job = ("base-" if body.get("baseline") else "job-") + body["attempt_id"]
                        jobs[job] = baseline if body.get("baseline") else checks
                        return {"job": job, **({"baseline": True, "base": BASE} if body.get("baseline") else {})}
                    if path == "/verify/status":
                        worker.calls.append((path, body))
                        return jobs[body["job"]]
                    return original(config, path, body, **kwargs)
                test = {"id": "test", "name": "测试", "tool": "claude", "prompt": "补测试", "kind": "test",
                        "acceptance": ["测试覆盖新行为"], "verify": ["npm test"]}
                rid = self.session([test], request="start-%016d" % (2 + baseline_passed))
                self.run_engine(call, ticks=16)
                attempt, evidence = self.attempt(rid, "test")
                self.assertEqual(self.gates(evidence).get("tests_fail_on_baseline"), verdict)
                self.assertEqual(attempt["status"], "blocked" if verdict == "fail" else "accepted")
                started = [b for p, b in worker.calls if p == "/verify/start" and b.get("baseline")]
                self.assertEqual(started[0]["baseline"], {"candidate": C1, "parent": BASE})

    def test_an_old_worker_skips_the_baseline_gate(self):
        worker = FakeWorker()   # its /verify/start does not echo "baseline": it ran the commands on the candidate
        worker.verify = [{"state": "done", "passed": True, "failed": None,
                          "results": [{"command": "npm test", "exit": 0, "seconds": 1.0, "tail": "ok"}]}]
        test = {"id": "test", "name": "测试", "tool": "claude", "prompt": "补测试", "kind": "test",
                "acceptance": ["测试覆盖新行为"], "verify": ["npm test"]}
        rid = self.session([test])
        self.run_engine(worker, ticks=16)
        attempt, evidence = self.attempt(rid, "test")
        self.assertEqual(self.gates(evidence).get("tests_fail_on_baseline"), "skip")
        self.assertEqual(attempt["status"], "accepted")
```

（`session()` 的 Team 夹具与 `request_id` 以 `EngineGateTests` 现有用法为准；`FakeWorker.__call__` 的分发方式不变。）

- [ ] **Step 2: 运行，确认失败**：`$PY -m unittest tests.test_gates tests.test_engine_gates -v`。
- [ ] **Step 3: 实现**

`gates.py`：

```python
def covered(path, globs):
    """Whether a task's ``paths`` cover ``path`` (D37 §11.2): a directory (trailing / or /**), a glob, or the path itself
    and everything under it."""
    import fnmatch
    path = path[2:] if path.startswith("./") else path
    for item in globs or []:
        item = str(item).strip()
        item = item[2:] if item.startswith("./") else item
        if not item:
            continue
        if item.endswith("/**"):
            item = item[:-2]
        if item.endswith("/"):
            if path.startswith(item):
                return True
            continue
        if path == item or path.startswith(item + "/") or fnmatch.fnmatchcase(path, item):
            return True
    return False
```

`engine._more_gates`：候选证据加 `"names"`、`"names_truncated"`（若有）。`no_secrets` 通过之后、检查命令之前：

```python
        locked = self._locked(run, task, spec, candidate)
        if locked:
            status, detail = locked
            if status == "fail":
                return fail("artifacts_locked", detail)
            records.append(gates.record("artifacts_locked", status, detail))
```

```python
    def _locked(self, run, task, spec, candidate):
        """artifacts_locked (D37 §11.2): (status, detail), or None when the candidate changed nothing."""
        if not candidate.get("changed"):
            return None
        if "names" not in candidate:
            return "skip", gates.OLD_WORKER
        if not spec.get("paths"):
            return "skip", "任务未声明可改动路径（paths）"
        owners = {}
        with self.db.read() as db:
            for row in db.execute("SELECT t.spec tspec, a.evidence FROM tasks t JOIN attempts a ON a.id=t.current_attempt "
                                  "WHERE t.run_id=? AND t.ordinal<? AND t.status='accepted' ORDER BY t.ordinal",
                                  (run["id"], task["ordinal"])):
                names = (json.loads(row["evidence"] or "{}").get("candidate") or {}).get("names") or []
                for name in names:
                    owners.setdefault(name, json.loads(row["tspec"]).get("name") or "此前的任务")
        if not owners:
            return "skip", "此前的已验收任务没有记录文件"
        broken = {}
        for name in candidate["names"]:
            if name in owners and not gates.covered(name, spec["paths"]):
                broken.setdefault(owners[name], []).append(name)
        if not broken:
            return "pass", None
        return "fail", "；".join(f"改动了已验收任务「{owner}」的交付物：{'、'.join(files[:10])}" for owner, files in broken.items()) \\
            + "；如需修改，请在本任务的 paths 中列出"
```

`_poll_checks` 中 `checks_pass` 通过之后（`error = None` 处），若 `json.loads(attempt["spec"]).get("kind") == "test"` 且候选 `changed` 为真：

```python
            started = self._start_baseline(run, config, attempt, evidence)
            if started:
                with self.db.transaction() as db:
                    journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.checking", "在会话基线上运行测试",
                                   event={"id": attempt["id"], "task_id": task["id"]}, evidence=dumps(evidence), updated=time.time())
                return
```

```python
    def _start_baseline(self, run, config, attempt, evidence):
        """tests_fail_on_baseline (D37 §11.1): the second run of the verify commands, on the Session's baseline."""
        candidate = evidence["candidate"]
        commands = json.loads(attempt["spec"]).get("verify", [])
        try:
            started = self.request(config, "/verify/start", {"run_id": run["id"], "attempt_id": attempt["id"], "commands": commands,
                                                             "baseline": {"candidate": candidate["sha"], "parent": candidate["parent"]}})
        except Error as exc:
            if not 400 <= exc.status < 500:
                raise
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", f"无法在基线上运行：{exc}"))
            return False
        if not started.get("baseline"):   # an older Worker ran them on the candidate: that says nothing about the baseline
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", gates.OLD_WORKER))
            return False
        evidence["baseline"] = {"job": started["job"], "base": started.get("base"), "started": time.time()}
        return True
```

`advance` 中检查轮询之后加：`if prior.get("baseline") and "baseline_results" not in prior: return self._poll_baseline(run, config, task, attempt, prior)`。

```python
    def _poll_baseline(self, run, config, task, attempt, evidence):
        try:
            status = self.request(config, "/verify/status", {"job": evidence["baseline"]["job"]})
        except Error as exc:
            if exc.code != "check_unknown":
                raise
            status = {"state": "done", "lost": True, "results": []}
        if status["state"] != "done":
            return
        evidence["baseline_results"] = status.get("results") or []
        base = str(evidence["baseline"].get("base") or "")[:7]
        if status.get("lost") or status.get("error"):
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip",
                                                  status.get("error") or "基线检查中断（Worker 重启）"))
            return self._settle(run, task, attempt, evidence, None)
        if status.get("passed"):
            error = f"新增的测试在改动之前（基线 {base}）也通过，不能证明新行为"
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "fail", error))
            evidence.update(failure_type="gate", gate="tests_fail_on_baseline")
            return self._settle(run, task, attempt, evidence, error)
        failed = evidence["baseline_results"][status["failed"]] if status.get("failed") is not None else {}
        evidence["gates"].append(gates.record("tests_fail_on_baseline", "pass",
                                              f"基线上失败：{failed.get('command', '')}（退出码 {failed.get('exit')}）"))
        return self._settle(run, task, attempt, evidence, None)
```

- [ ] **Step 4: 运行，确认通过**：`$PY -m unittest tests.test_gates tests.test_engine_gates tests.test_recovery tests.test_runtime tests.test_mvp tests.test_switch_tool -v`。
- [ ] **Step 5: Commit** `feat(gates): artifacts_locked 与 tests_fail_on_baseline（D37 §11.1、§11.2）`

---

### Task 4: 文档与全量测试

- [ ] `docs/OPERATIONS.md`「候选提交与命名门禁」加第 3 期两条门禁：何时判定、`paths` 的写法、基线运行期间工作区的状态（预览与下载在此期间看到的是基线加测试）。
- [ ] `docs/MVP-RELEASE.md` §14.2 第 3 项剩余项更新，§14.13 加第 3 期。
- [ ] 全量：`$PY -m unittest discover -s tests -p 'test_*.py'`。
- [ ] Commit `docs: D37 第 3 期门禁——运维与进度`

## EC2 验收

在 `LiboMa/bedrock-claude-call` 上：
1. 「实现 + 测试」会话：
   - 实现任务新建 `kc_mod.py`（函数返回 42）；
   - 测试任务新建 `test_kc_mod.py` 断言 42，`verify` 为 `python3 -m pytest -q test_kc_mod.py`（或 `python3 test_kc_mod.py`）；
   - 期望 `tests_fail_on_baseline` 通过（基线上缺 `kc_mod.py` 而失败）。
2. 测试任务只写一个恒真的测试（如 `assert True`）：期望 `tests_fail_on_baseline` 失败并进入纠错。
3. 第二个任务声明 `paths: ["docs/"]` 却修改实现任务的 `kc_mod.py`：期望 `artifacts_locked` 失败。
4. 运行结束后工作区在候选上（`git status` 干净，HEAD 为候选）。
