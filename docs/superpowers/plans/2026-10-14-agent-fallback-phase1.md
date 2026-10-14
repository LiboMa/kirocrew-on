# 换工具 第 1 期（D38） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 任务因为工具本身失败时（工具不可用、模型未公布或不受支持、实际后端不符、原生错误重试后再现），会话进入诊断。KiroCrew 协调者推荐换用的工具与模型（`switch_tool`），平台核对后按新工具重跑，并在控制台、MCP 与报告中写明换用记录。代码中没有任何回退链或对照表。

**Architecture:**
- **Worker**：`/health` 增加 `tools`，报告每个工具能否启动。平台登记或刷新 Worker 时随能力保存（`capabilities.tools`）。
- **Engine**：
  - 派发前按保存的 `tools` 检查所请求的工具；
  - 派发被拒（`model_unavailable` / `model_transport_unsupported`），以及 `identity_match` 失败，都记为 `failure_type: tool`，交给诊断。
- **recovery**：
  - 诊断上下文带上 `environment`（工具、模型、限制、是否允许换工具）；
  - 协调者可以给出 `switch_tool`，平台按同一份 `environment` 核对，通过后改写该任务的 `spec` 并重置任务；
  - 新 Attempt 的证据带 `switched_from`。
- **规划**：去掉 `planning.REASSIGN`，规划提示列出工具是否可用与已知限制，由 Kiro 自行选择。

**Tech Stack:** Python 3 标准库、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-14-agent-fallback-approvals-design.md`（§1–§3、§5 第 1 期）

## Global Constraints

- 没有内置回退链、对照表或默认替换。平台只核对协调者的推荐，不替用户或 Agent 选工具（A1、A5）。
- 换工具默认开启。`recovery.switch_tools: false` 关闭；只有关闭时才在 `recovery` 中保存 `switch_tools: false`（已有会话与接口的设置形状不变）。
- 交给诊断的工具层失败：
  - 派发前：请求的工具在平台保存的 `tools` 中 `available` 为 `false`（规划任务除外）；
  - 派发被拒：`model_unavailable`、`model_transport_unsupported`；
  - `identity_match` 失败；
  - 原生错误在自动重试后再现（现有路径，不改）。
  - 这些情形的 `failure_type` 都是 `tool`。
- 保持现状，不交给换工具：
  - `auth`（Kiro 登录过期仍按现状等待管理员）；
  - `native_stopped`；
  - `invalid_request` / `not_prepared`（派发被拒但不进诊断）。
- `switch_tool` 的核对（不通过时会话 `blocked`，`error` 写明原因，前缀「协调者换工具的推荐未通过核对：」）：
  1. 会话未关闭换工具（`environment.switch_tools`）；
  2. `target_task_id` 是失败的任务；
  3. `tool` ∈ `kiro / claude / codex / opencode`，且 `environment.tools[tool].available is True`；
  4. `model` 为 `auto` / `default`，或在 `environment.models[tool]` 中；
  5. （工具、模型、Effort）不能与失败的那次完全相同；
  6. 审查任务的 `tool` 不能是被审查代码的实际后端（上游最近完成的非审查、非设计任务的 Attempt 的 `actual_backend`）；
  7. 新 `spec` 通过 `domain.steps` 校验（如 OpenCode 不接受 Effort）。
- 新 Attempt 的证据：`switched_from: {tool, model, effort, reason, by: "coordinator", recovery: <轮次>}`。之后每次更新证据都保留它（与 `transient_retry_of` 相同）。
- 变更记录写 `task.tool_switched`。修订记一次 `recovered`，摘要为「自动纠错：「<任务名>」由 <tool> / <model> 换为 <tool> / <model>（<原因>）」。
- 中文文案：
  - 不可用：`<tool> 在此执行环境不可用：<reason>`；
  - 控制台：`由协调者从 <tool> / <model> 换为 <tool> / <model>：<原因>`；
  - 报告：`换工具：<tool> / <model> → <tool> / <model>（协调者推荐）：<原因>`。

## Review Focus

1. **旧 Worker、旧会话**：Worker 未报告 `tools`（旧版本）时派发照旧，不因缺少字段而拒绝；协调者推荐换工具时因「未报告可用性」被拒、会话 `blocked` 并说明。（`test_an_old_worker_without_tools_dispatches_as_before`、`test_switch_is_refused_without_reported_tools`）
2. **换工具之后再失败**：换用后的任务再次失败时，进入下一轮诊断（受 `max_cycles` 限制），不会无限换下去；轮次用尽时 `attention` 写明。（`test_switches_are_bounded_by_the_cycle_limit`）
3. **续作覆盖换用**：协调者换工具之后、新 Attempt 创建之前，用户续作并指定了另一个工具，新 Attempt 不应标注 `switched_from`。（`test_a_continue_after_a_switch_is_not_labelled_a_switch`）
4. **审查独立**：被审查的实现实际由 claude 完成时，审查不能换成 claude。（`test_switch_validation_explains_each_refusal` 的 review 子测试）
5. **证据保留**：`switched_from` 在 Attempt 完成后（以及检查命令运行中、瞬时重试后）仍在证据里。（`test_an_unavailable_tool_is_diagnosed_before_dispatch_and_the_switch_completes`）

---

### Task 1: Worker 报告工具是否可用

**Files:**
- Modify: `octopus/worker.py`（构造参数、`tools()`、`health()`）
- Modify: `octopus/__main__.py:164-167`（传入 `tool_paths`、`adapters_dir`）
- Test: `tests/test_worker_tools.py`（新建）

**Interfaces:**
- Produces: `Worker.tools() -> {tool: {"available": bool, "reason"?: str}}`，`kiro` 另有 `"login": "ok"|"expired"|"unknown"`；`Worker.health()["tools"]`；`health()["limitations"]` 增加 K01 一条。

- [ ] **Step 1: 写失败的测试**

```python
"""D38 §3.1: the Worker reports whether each Coding tool can be started on its boundary."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from octopus.worker import Worker


def executable(path, text="#!/bin/sh\nexit 0\n"):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class WorkerToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Neither the machine's PATH nor ~/.local/bin may decide these tests.
        patcher = patch.dict(os.environ, {"PATH": "", "HOME": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def worker(self, **kwargs):
        return Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew", **kwargs)

    def test_each_tool_is_found_on_the_configured_path_or_the_search_path(self):
        executable(self.bin / "claude")
        adapters = self.root / "adapters"
        adapters.mkdir()
        executable(adapters / "codex")
        kiro = executable(self.root / "kiro-cli", "#!/bin/sh\necho 'Logged in as fixture'\n")
        tools = self.worker(kiro_cli=kiro, path_prepend=(self.bin,), adapters_dir=adapters,
                            tool_paths={"opencode": self.root / "missing-opencode"}).tools()
        self.assertEqual(tools["kiro"], {"available": True, "login": "ok"})
        self.assertEqual(tools["claude"], {"available": True})
        self.assertEqual(tools["codex"], {"available": True})
        self.assertFalse(tools["opencode"]["available"])
        self.assertIn("missing-opencode", tools["opencode"]["reason"])

    def test_a_missing_command_says_which(self):
        tools = self.worker().tools()
        self.assertEqual(tools["claude"], {"available": False, "reason": "claude 命令不存在"})
        self.assertEqual(tools["kiro"]["available"], False)

    def test_an_expired_kiro_login_is_reported_beside_availability(self):
        kiro = executable(self.root / "kiro-cli", "#!/bin/sh\necho 'Not logged in. Run kiro-cli login' >&2\nexit 1\n")
        tools = self.worker(kiro_cli=kiro).tools()
        self.assertEqual(tools["kiro"], {"available": True, "login": "expired"})

    def test_health_carries_the_tools_and_the_known_limitations(self):
        executable(self.bin / "claude")
        health = self.worker(path_prepend=(self.bin,)).health()
        self.assertTrue(health["tools"]["claude"]["available"])
        self.assertTrue(any("K01" in item for item in health["limitations"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行，确认失败**

Run: `$PY -m unittest tests.test_worker_tools -v`（`PY=/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-on/.venv/bin/python`）
Expected: FAIL（`TypeError: unexpected keyword argument 'adapters_dir'` 或 `AttributeError: tools`）

- [ ] **Step 3: 实现**

`octopus/worker.py` 顶部常量（放在 `class Worker` 之前）：

```python
# The command each Coding tool is started with, and its [tools] setting (composition/environment.py starts them the same way).
TOOL_COMMANDS = {"kiro": ("kiro_cli", "kiro-cli"), "claude": ("claude", "claude"), "codex": ("codex", "codex"),
                 "opencode": ("opencode", "opencode")}
```

构造函数签名增加 `tool_paths=None, adapters_dir=None`（放在 `path_prepend=()` 之后），并在 `self.kiro_cli, self._login = ...` 之后：

```python
        self.tool_paths = {**(tool_paths or {}), **({"kiro_cli": kiro_cli} if kiro_cli else {})}
        self.search = [*([adapters_dir] if adapters_dir else []), *path_prepend]
```

`tool_auth` 之后增加：

```python
    def tools(self):
        """Whether each Coding tool can be started on this boundary (D38 §3.1): its configured path, else its command on
        the search path the Gateway uses. Kiro's login is reported beside it: an expired login waits for an
        administrator and is not a reason to switch tools."""
        search = os.pathsep.join([str(p) for p in self.search] + [os.environ.get("PATH", ""), str(Path.home() / ".local/bin")])
        result = {}
        for tool, (setting, command) in TOOL_COMMANDS.items():
            configured = self.tool_paths.get(setting)
            if configured:
                found = os.access(configured, os.X_OK)
                reason = f"配置的 {configured} 不存在或不可执行"
            else:
                found, reason = bool(shutil.which(command, path=search)), f"{command} 命令不存在"
            result[tool] = {"available": found, **({} if found else {"reason": reason})}
        result["kiro"]["login"] = self.tool_auth() if result["kiro"]["available"] else "unknown"
        return result
```

`health()` 返回值中 `"tool_auth"` 之后加 `"tools": self.tools(),`，`limitations` 末尾加：

```python
                                "已知问题 K01：OpenCode 在 EC2 执行边界上以 shell 写文件曾被 KiroCrew 工具门禁拒绝，实现类任务可能失败。"
```

`octopus/__main__.py` 的 `Worker(...)` 调用中 `path_prepend=settings.path_prepend,` 之后加
`tool_paths=settings.tools, adapters_dir=settings.adapters_dir,`。

- [ ] **Step 4: 运行，确认通过**

Run: `$PY -m unittest tests.test_worker_tools tests.test_worker_approval tests.test_crew_entry tests.test_bootstrap -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add octopus/worker.py octopus/__main__.py tests/test_worker_tools.py
git commit -m "feat(worker): /health 报告各 Coding 工具能否启动（配置路径或 Gateway 搜索路径；Kiro 另报登录）与 K01 已知限制（D38 §3.1）"
```

---

### Task 2: 诊断上下文的执行环境、换工具开关与协调者提示

**Files:**
- Modify: `octopus/recovery.py`（`policy`、`schedule`、`coordinator_instructions`）
- Modify: `octopus/mcp.py:21-22`（`RECOVERY` 增加 `switch_tools`）
- Modify: `octopus/schema.py:66-67`（`RecoveryPolicy` 增加 `switch_tools`）
- Test: `tests/test_switch_tool.py`（新建；本任务写夹具与前三个测试）

**Interfaces:**
- Consumes: Task 1 的 `capabilities.tools`（平台保存的 `/health`）。
- Produces:
  - `recovery.policy(value)`：接受 `switch_tools`（布尔）；只有为 `False` 时才出现在返回值中；
  - 诊断上下文 `context["environment"] = {"tools", "models", "limitations", "switch_tools"}`；
  - 夹具 `SwitchCase`（Task 3、4 复用）。

- [ ] **Step 1: 写失败的测试**（夹具与本任务的测试）

```python
"""D38 phase 1: tool-level failures go to diagnosis and the KiroCrew Coordinator may recommend another tool; the platform
checks the recommendation against the environment it showed and records the switch. Native calls are substitutes."""
import copy
import json
import tempfile
import unittest

from octopus import recovery
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.old_worker import as_old_worker

TOOLS = {"kiro": {"available": True, "login": "ok"}, "claude": {"available": True}, "codex": {"available": True},
         "opencode": {"available": False, "reason": "opencode 命令不存在"}}
MODELS = {"kiro": ["auto"], "claude": ["auto", "sonnet"], "codex": ["auto"], "opencode": ["auto"]}


def step(sid, tool, kind, **extra):
    return {"id": sid, "name": {"code": "实现", "review": "审查"}[sid], "tool": tool, "prompt": "完成并验证", "kind": kind,
            "acceptance": ["可运行"], **({"verify": ["true"]} if kind == "implement" else {}), **extra}


class SwitchCase(unittest.TestCase):
    capabilities = {"tools": TOOLS, "models": MODELS, "limitations": ["已知问题 K01：…"]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','Fixture',?,?)",
                       (user["id"], dumps({"url": "fixture", "token": "fixture",
                                            **({"capabilities": self.capabilities} if self.capabilities else {})})))
        self.calls, self.bodies = [], {}
        self.refuse_models, self.mismatch_rounds, self.fail_rounds = set(), set(), set()
        self.decisions = []
        self.engine = Engine(self.db, self.request)

    def start(self, steps, **body):
        return self.service.start(self.actor, {"request_id": "switch-" + str(len(self.calls)) + str(id(steps)),
                                               "input": "修复并验证", "steps": steps, "source": {"type": "new"},
                                               "worker_id": "worker", **body})

    def request(self, config, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/workspace"}
        if path == "/control":
            return {"status": "stopped", "lifecycle": "open"}
        aid = body["attempt_id"]
        if path == "/dispatch":
            if not body.get("coordination") and body["spec"]["model"] in self.refuse_models:
                raise Error(409, "model_unavailable", "所选模型未由此 Worker 后端公布")
            self.bodies.setdefault(aid, copy.deepcopy(body))
            return {"status": "running", "native_id": aid[-8:], "context_hash": "h"}
        as_old_worker(path)
        record = self.bodies[aid]
        result = {"native": {"done": True, "result": "完成"}, "evidence": {"actual_backend": record["spec"]["tool"]},
                  "handoff": "交接"}
        if record.get("coordination"):
            result["decision"] = dumps(self.decisions.pop(0))
            return result
        number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == record["spec"]["id"])
        if (record["spec"]["id"], number) in self.mismatch_rounds:
            result["evidence"]["actual_backend"] = "kiro"
        failed = (record["spec"]["id"], number) in self.fail_rounds
        result["outcome"] = dumps({"decision": "changes_requested" if failed else "pass", "summary": "结论",
                                   "failure_type": "implementation" if failed else "none",
                                   "findings": ["问题"] if failed else []})
        return result

    def tick(self, rid, count=25):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def coordination(self):
        return [body["coordination"] for path, body in self.calls if path == "/dispatch" and body.get("coordination")]


class EnvironmentTests(SwitchCase):
    def test_the_policy_keeps_its_shape_unless_switching_is_turned_off(self):
        self.assertEqual(recovery.policy({"enabled": True, "max_cycles": 2}), {"enabled": True, "max_cycles": 2})
        self.assertEqual(recovery.policy({"switch_tools": False}), {"enabled": True, "max_cycles": 2, "switch_tools": False})
        with self.assertRaises(Error):
            recovery.policy({"switch_tools": "no"})

    def test_the_coordinator_is_shown_the_environment(self):
        self.decisions = [{"action": "wait_for_user", "reason": "需要用户确认", "instructions": "确认后续作"}]
        on = self.start([step("code", "claude", "implement")])
        self.fail_rounds = {("code", 1)}
        self.tick(on["id"])
        off = self.start([step("code", "claude", "implement")], recovery={"enabled": True, "max_cycles": 2, "switch_tools": False})
        self.fail_rounds = {("code", 1), ("code", 2)}
        self.decisions = [{"action": "wait_for_user", "reason": "需要用户确认", "instructions": "确认后续作"}]
        self.tick(off["id"])
        first, second = self.coordination()
        self.assertEqual(first["environment"], {"tools": TOOLS, "models": MODELS, "limitations": ["已知问题 K01：…"],
                                                "switch_tools": True})
        self.assertFalse(second["environment"]["switch_tools"])

    def test_the_coordinator_prompt_offers_a_switch_and_its_rules(self):
        prompt = recovery.coordinator_instructions({"environment": {"tools": TOOLS}}, __import__("pathlib").Path("/f"))
        self.assertNotIn("不换模型", prompt)
        for words in ("switch_tool", "available", "完全相同", "审查", "wait_for_user"):
            self.assertIn(words, prompt)
```

- [ ] **Step 2: 运行，确认失败**

Run: `$PY -m unittest tests.test_switch_tool -v`
Expected: FAIL（`policy({"switch_tools": False})` 抛出「自动纠错配置无效」；上下文没有 `environment`；提示仍含「不换模型」）

- [ ] **Step 3: 实现**

`recovery.policy`：

```python
def policy(value=None, *, legacy=False):
    if value is None:
        return {"enabled": not legacy, "max_cycles": 2}
    require(isinstance(value, dict) and set(value) <= {"enabled", "max_cycles", "switch_tools"}, "自动纠错配置无效")
    enabled, limit, switch = value.get("enabled", True), value.get("max_cycles", 2), value.get("switch_tools", True)
    require(isinstance(enabled, bool) and type(limit) is int and 1 <= limit <= 5 and isinstance(switch, bool),
            "自动纠错次数须为 1–5，enabled 与 switch_tools 须为布尔值")
    # Switching tools is on by default (D38 A2); only turning it off is stored, so existing settings keep their shape.
    return {"enabled": enabled, "max_cycles": limit, **({} if switch else {"switch_tools": False})}
```

`recovery.schedule` 中 `planned = ...` 之前加：

```python
    worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone() if run["worker_id"] else None
    capabilities = json.loads(worker["config"]).get("capabilities") or {} if worker else {}
    # What the Coordinator may switch to, saved with the diagnosis: its recommendation is checked against the same (D38 §3.1).
    context["environment"] = {"tools": capabilities.get("tools") or {}, "models": capabilities.get("models") or {},
                              "limitations": capabilities.get("limitations") or [],
                              "switch_tools": settings.get("switch_tools", True)}
```

`coordinator_instructions`：把「保留工作流和工具配置，不迁移源码、不换模型、不删除历史。」一行替换为：

```python
保留工作流配置，不迁移源码、不删除历史。不要把停止或权限拒绝当作可绕过的失败。
失败原因在于工具本身时（failure_type 为 tool：工具在执行环境中不可用、模型未公布或不受支持、实际运行的后端与请求不符，
或同一工具的原生错误在自动重试后再次发生），可以用 switch_tool 推荐换用的工具与模型：
- 只从 context.environment.tools 中 available 为 true 的工具、environment.models 中该工具公布的模型（或 auto）中选择；
- 工具、模型与 Effort 不能与失败的那次完全相同；模型未公布时，同一工具改用 auto 也是有效的推荐；
- 审查类任务选择与被审查代码实际所用不同的工具；参考 environment.limitations 中的已知问题；
- environment.switch_tools 为 false 时不能换工具：用 wait_for_user，并说明可换用哪个工具；不确定时也用 wait_for_user。
实现、测试、契约或门禁本身的问题仍用 retry 或 rework，不换工具。
```

（原行中「不要把停止或权限拒绝当作可绕过的失败。」保留在新段首行。）决定格式一段改为：

```python
把唯一有效的处置写到 {folder / 'decision.json'}，严格 JSON：
{{"action":"retry","target_task_id":"当前或上游 Task ID",
  "reason":"真实根因与判断依据","instructions":"具体修复动作、输入与复验要求"}}
换工具时：
{{"action":"switch_tool","target_task_id":"失败的 Task ID","tool":"codex","model":"auto","effort":"",
  "reason":"为什么原工具不能完成、为什么选这个工具","instructions":"以新工具重新执行本任务的要求"}}
action 只能为 retry、rework、switch_tool、wait_for_user；wait_for_user 不需 target_task_id，
instructions 要说明缺少什么、用户下一步如何补充。
```

`octopus/mcp.py`：

```python
RECOVERY = object_schema({"enabled": {"type": "boolean"},
                          "max_cycles": {"type": "integer", "minimum": 1, "maximum": 5},
                          "switch_tools": {"type": "boolean"}})
```

`octopus/schema.py` 的 `RecoveryPolicy` 加 `"switch_tools": {"type": "boolean", "default": True}`。

- [ ] **Step 4: 运行，确认通过**

Run: `$PY -m unittest tests.test_switch_tool tests.test_recovery tests.test_mvp -v`
Expected: PASS（`test_recovery` 的上下文多了 `environment`，断言不受影响）

- [ ] **Step 5: Commit**

```bash
git add octopus/recovery.py octopus/mcp.py octopus/schema.py tests/test_switch_tool.py
git commit -m "feat(recovery): 诊断上下文带上执行环境（工具、模型、已知限制、是否允许换工具）；协调者提示可推荐 switch_tool；recovery.switch_tools 开关（D38 §3.1、§3.5）"
```

---

### Task 3: 工具层失败交给诊断

**Files:**
- Modify: `octopus/engine.py`（派发前检查、`_failed` 的派发被拒、新方法 `_refused`）
- Modify: `octopus/gates.py:38-40`（`identity_match` 失败的类型改为 `tool`）
- Modify: `octopus/domain.py`（`_attention` 增加 `tool`）
- Test: `tests/test_switch_tool.py`（追加 `ToolFailureTests`）、`tests/test_gates.py:32`

**Interfaces:**
- Consumes: `capabilities.tools`（Task 1）、`SwitchCase`（Task 2）。
- Produces:
  - `Engine._refused(run, attempt_id, evidence, message, code)`：Attempt 与任务 `blocked`、证据 `failure_type: "tool"`、`refused: <code>`，并安排诊断；
  - `gates.basic` 的身份不符返回 `failure_type` 为 `"tool"`。

- [ ] **Step 1: 写失败的测试**

`tests/test_switch_tool.py` 追加：

```python
class ToolFailureTests(SwitchCase):
    WAIT = {"action": "wait_for_user", "reason": "需要换用工具", "instructions": "请换用可用的工具后续作"}

    def test_an_unavailable_tool_goes_to_diagnosis_without_being_dispatched(self):
        self.decisions = [self.WAIT]
        run = self.tick(self.start([step("code", "opencode", "implement")])["id"])
        self.assertFalse([b for p, b in self.calls if p == "/dispatch" and not b.get("coordination")])
        attempt = run["tasks"][0]["attempts"][0]
        self.assertEqual(attempt["status"], "blocked")
        self.assertEqual(attempt["error"], "opencode 在此执行环境不可用：opencode 命令不存在")
        self.assertEqual((attempt["evidence"]["failure_type"], attempt["evidence"]["refused"]), ("tool", "tool_unavailable"))
        self.assertEqual(run["recovery"]["used"], 1)
        self.assertEqual(self.coordination()[0]["failure_type"], "tool")

    def test_a_refused_model_goes_to_diagnosis(self):
        self.decisions, self.refuse_models = [self.WAIT], {"unpublished-x"}
        run = self.tick(self.start([step("code", "claude", "implement", model="unpublished-x")])["id"])
        evidence = run["tasks"][0]["attempts"][0]["evidence"]
        self.assertEqual((evidence["failure_type"], evidence["refused"]), ("tool", "model_unavailable"))
        self.assertEqual(run["recovery"]["used"], 1)
        self.assertEqual(run["attention"]["title"], "需要换用工具或模型")

    def test_a_backend_that_is_not_the_requested_one_goes_to_diagnosis(self):
        self.decisions, self.mismatch_rounds = [self.WAIT], {("code", 1)}
        run = self.tick(self.start([step("code", "claude", "implement")])["id"])
        self.assertEqual(run["tasks"][0]["attempts"][0]["evidence"]["failure_type"], "tool")
        self.assertEqual(run["recovery"]["used"], 1)

    def test_an_invalid_request_still_blocks_without_diagnosis(self):
        def refuse(config, path, body, **kwargs):
            if path == "/dispatch" and not body.get("coordination"):
                raise Error(409, "not_prepared", "工作区未准备")
            return self.request(config, path, body, **kwargs)
        self.engine.request = refuse
        run = self.tick(self.start([step("code", "claude", "implement")])["id"])
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["recovery"]["used"], 0)


class OldWorkerTests(SwitchCase):
    capabilities = None   # registered before D38: no tools in its capabilities

    def test_an_old_worker_without_tools_dispatches_as_before(self):
        run = self.tick(self.start([step("code", "opencode", "implement")])["id"])
        self.assertTrue([b for p, b in self.calls if p == "/dispatch" and b["spec"]["tool"] == "opencode"])
        self.assertEqual(run["status"], "completed")
```

`tests/test_gates.py:32` 的期望由 `"model"` 改为 `"tool"`：

```python
            (dict(evidence={"actual_backend": "codex"}), ("实际 ACP 后端未核实或与配置不匹配", "tool", "identity_match")),
```

（同一表中其余身份不符的行如果也写 `"model"`，同样改为 `"tool"`。）

- [ ] **Step 2: 运行，确认失败**

Run: `$PY -m unittest tests.test_switch_tool tests.test_gates -v`
Expected: FAIL（opencode 被派发；被拒的模型使会话 `blocked` 而 `recovery.used` 为 0；身份不符为 `model`）

- [ ] **Step 3: 实现**

`octopus/gates.py`：身份不符的返回改为 `return message, "tool", records`，并在该循环上方加注释：

```python
    # A backend, model or effort that is not the one requested is the tool's failure: recovery may switch (D38 §3.2).
```

`octopus/engine.py` 增加方法（放在 `_failed` 之后）：

```python
    def _refused(self, run, attempt_id, evidence, message, code):
        """A tool the task cannot run on (D38 §3.2): the Attempt is blocked as the tool's failure and goes to diagnosis,
        where the Coordinator may recommend another tool."""
        with self.db.transaction() as db:
            journal.change(db, "attempts", attempt_id, journal.WORKER, "attempt.state",
                           event={"id": attempt_id, "status": "blocked", "error": message},
                           status="blocked", error=message, evidence=dumps({**evidence, "failure_type": "tool", "refused": code}),
                           updated=time.time())
            journal.change_rows(db, "tasks", "current_attempt=?", (attempt_id,), journal.WORKER, "task.state", status="blocked")
        self.update(run["id"], "blocked", message, actor=journal.WORKER)
        recovery.automatic(self.db, run["id"], attempt_id)
```

`_failed` 中把 `if code in {"model_unavailable", "model_transport_unsupported", "invalid_request", "not_prepared"}:` 拆成两支：

```python
            if code in {"model_unavailable", "model_transport_unsupported"}:
                return self._refused(run, current["id"], json.loads(current["evidence"] or "{}"), message, code)
            if code in {"invalid_request", "not_prepared"}:
                ...（原有的 blocked 处理不变）
```

`advance` 中 `if attempt["status"] == "dispatching":` 之后、构造 `contract` 之前：

```python
            # The platform's record of this Worker's tools (D38 §3.1): a tool it cannot start is not dispatched.
            state = ((config.get("capabilities") or {}).get("tools") or {}).get(spec["tool"])
            if task["id"] != planning.PLAN_ID and isinstance(state, dict) and state.get("available") is False:
                return self._refused(run, attempt["id"], json.loads(attempt["evidence"] or "{}"),
                                     f"{spec['tool']} 在此执行环境不可用：{state.get('reason') or '原因未报告'}", "tool_unavailable")
```

`octopus/domain.py` 的 `_attention`：在 `elif failure == "input":` 之前加：

```python
        elif failure == "tool":
            title = "需要换用工具或模型"
            action = ("协调者可推荐换用可用的工具（自动纠错开启且允许换工具时）；也可在「配置并续作」中换用可用的工具或模型后重跑，"
                      "详情中是失败原因或协调者的建议。")
```

- [ ] **Step 4: 运行，确认通过**

Run: `$PY -m unittest tests.test_switch_tool tests.test_gates tests.test_engine_gates tests.test_recovery tests.test_runtime tests.test_mvp tests.test_contract -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add octopus/engine.py octopus/gates.py octopus/domain.py tests/test_switch_tool.py tests/test_gates.py
git commit -m "feat(engine): 工具层失败交给诊断——平台记录中不可用的工具不派发、模型被拒、实际后端不符均为 failure_type tool（D38 §3.2）"
```

---

### Task 4: 协调者的 `switch_tool` 决定：核对、生效、记录

**Files:**
- Modify: `octopus/recovery.py`（`Coordinator.advance` 的决定解析、`finish`；新函数 `switch`、`reviewed_backend`、`switched_from`）
- Modify: `octopus/engine.py`（创建 Attempt 时写 `switched_from`；证据更新与瞬时重试时保留）
- Test: `tests/test_switch_tool.py`（追加 `SwitchTests`）

**Interfaces:**
- Consumes: `context["environment"]`（Task 2）、`failure_type: tool`（Task 3）、`domain.steps`、`domain.TOOLS`。
- Produces:
  - `recovery.switch(db, run, failed, decision, context) -> (new_spec, before)`，不通过时抛出 `Error`；
  - `recovery.reviewed_backend(db, run_id, ordinal) -> str | None`；
  - `recovery.switched_from(db, run_id, task_id, spec) -> dict | None`；
  - 已应用的决定中带 `switched_from: {tool, model, effort}`。

- [ ] **Step 1: 写失败的测试**

```python
class SwitchTests(SwitchCase):
    def switch(self, tool="claude", model="auto", target="code", **extra):
        return {"action": "switch_tool", "target_task_id": target, "tool": tool, "model": model, "effort": "",
                "reason": "原工具在此执行环境不可用", "instructions": "以新工具重新执行", **extra}

    def test_an_unavailable_tool_is_diagnosed_before_dispatch_and_the_switch_completes(self):
        self.decisions = [self.switch()]
        rid = self.start([step("code", "opencode", "implement"), step("review", "codex", "review")])["id"]
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        code = run["tasks"][0]
        self.assertEqual(code["spec"]["tool"], "claude")
        second = code["attempts"][-1]
        self.assertEqual(second["spec"]["tool"], "claude")
        self.assertEqual(second["evidence"]["switched_from"],
                         {"tool": "opencode", "model": "auto", "effort": "", "reason": "原工具在此执行环境不可用",
                          "by": "coordinator", "recovery": 1})
        self.assertNotIn("switched_from", run["tasks"][1]["attempts"][-1]["evidence"])
        revisions = self.service.revisions(self.actor, rid)
        self.assertEqual(revisions[-1]["summary"], "自动纠错：「实现」由 opencode / auto 换为 claude / auto（原工具在此执行环境不可用）")
        self.assertIn("task.tool_switched", [e["kind"] for e in self.service.events(self.actor, rid)])

    def test_a_refused_model_is_fixed_on_the_same_tool(self):
        self.decisions, self.refuse_models = [self.switch(model="auto")], {"unpublished-x"}
        run = self.tick(self.start([step("code", "claude", "implement", model="unpublished-x")])["id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["tasks"][0]["attempts"][-1]["evidence"]["switched_from"]["model"], "unpublished-x")

    def test_switch_validation_explains_each_refusal(self):
        cases = [("unavailable", [step("code", "opencode", "implement")], self.switch(tool="opencode", model="auto"), "不可用"),
                 ("same", [step("code", "claude", "implement")], self.switch(tool="claude"), "相同"),
                 ("model", [step("code", "claude", "implement")], self.switch(tool="codex", model="gpt-x"), "未由 codex 公布"),
                 ("target", [step("code", "claude", "implement"), step("review", "codex", "review")],
                  self.switch(tool="codex", target="code"), "失败的任务"),
                 ("review", [step("code", "claude", "implement"), step("review", "opencode", "review")],
                  self.switch(tool="claude", target="review"), "审查须使用与被审查代码（claude）不同的工具"),
                 ("effort", [step("code", "claude", "implement")], self.switch(tool="codex", model="auto", effort="ultra"), "Effort")]
        for name, steps, decision, words in cases:
            with self.subTest(name):
                self.decisions = [decision]
                self.mismatch_rounds = {("code", 1)} if name in {"same", "model", "effort"} else set()
                self.fail_rounds = {("review", 1)} if name == "target" else set()
                run = self.tick(self.start(steps)["id"])
                self.assertEqual(run["status"], "blocked")
                self.assertTrue(run["error"].startswith("协调者换工具的推荐未通过核对："), run["error"])
                self.assertIn(words, run["error"])

    def test_switching_turned_off_is_refused(self):
        self.decisions = [self.switch()]
        run = self.tick(self.start([step("code", "opencode", "implement")],
                                   recovery={"enabled": True, "max_cycles": 2, "switch_tools": False})["id"])
        self.assertEqual(run["status"], "blocked")
        self.assertIn("已关闭换工具", run["error"])

    def test_switches_are_bounded_by_the_cycle_limit(self):
        self.decisions = [self.switch(tool="claude"), self.switch(tool="codex")]
        self.mismatch_rounds = {("code", 1), ("code", 2), ("code", 3)}
        run = self.tick(self.start([step("code", "codex", "implement")],
                                   recovery={"enabled": True, "max_cycles": 2})["id"])
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["recovery"]["used"], 2)
        self.assertIn("2 轮上限", run["attention"]["action"])

    def test_a_continue_after_a_switch_is_not_labelled_a_switch(self):
        rid = self.start([step("code", "claude", "implement")])["id"]   # not ticked: no Attempt yet
        with self.db.transaction() as db:
            db.execute("INSERT INTO recoveries(id,run_id,number,failed_task_id,failed_attempt_id,attempt_id,status,context,"
                       "decision,created,updated) VALUES('r',?,1,'code','a','b','applied','{}',?,1,1)",
                       (rid, dumps({**self.switch(tool="claude"), "switched_from": {"tool": "codex", "model": "auto", "effort": ""}})))
            # the task still runs the Coordinator's choice: labelled; the user picked another since: not
            self.assertEqual(recovery.switched_from(db, rid, "code", {"tool": "claude", "model": "auto", "effort": ""})["tool"], "codex")
            self.assertIsNone(recovery.switched_from(db, rid, "code", {"tool": "codex", "model": "auto", "effort": ""}))


class SwitchWithoutToolsTests(SwitchCase):
    capabilities = {"models": MODELS}   # a Worker from before D38: models but no tools

    def test_switch_is_refused_without_reported_tools(self):
        self.decisions, self.mismatch_rounds = [{"action": "switch_tool", "target_task_id": "code", "tool": "codex",
                                                 "model": "auto", "effort": "", "reason": "r", "instructions": "i"}], {("code", 1)}
        run = self.tick(self.start([step("code", "claude", "implement")])["id"])
        self.assertEqual(run["status"], "blocked")
        self.assertIn("未报告可用性", run["error"])
```

- [ ] **Step 2: 运行，确认失败**

Run: `$PY -m unittest tests.test_switch_tool -v`
Expected: FAIL（「协调者动作无效」；`switched_from` 函数不存在）

- [ ] **Step 3: 实现**

`recovery.py` 顶部导入改为 `from .domain import TOOLS, steps, unserved`，并增加：

```python
def reviewed_backend(db, run_id, ordinal):
    """The backend that wrote the code a review at ``ordinal`` reviews: the newest accepted Attempt of an earlier task
    that is not itself a review or a design (D38 §3.3)."""
    for row in db.execute("SELECT a.spec, a.evidence FROM attempts a JOIN tasks t ON t.run_id=a.run_id AND t.id=a.task_id "
                          "WHERE a.run_id=? AND t.ordinal<? AND a.status='accepted' ORDER BY a.updated DESC, a.created DESC",
                          (run_id, ordinal)):
        spec = json.loads(row["spec"])
        if spec.get("kind") in {"review", "design"} or spec.get("id") == "plan":
            continue
        return json.loads(row["evidence"] or "{}").get("actual_backend") or spec["tool"]
    return None


def switch(db, run, failed, decision, context):
    """The Coordinator's recommended tool, checked against the environment it was shown (D38 §3.3): the task's new spec
    and the (tool, model, effort) it replaces, or ``Error`` with the reason."""
    environment = context.get("environment") or {}
    require(environment.get("switch_tools", True), "此会话已关闭换工具")
    require(decision.get("target_task_id") == failed["id"], "换工具的目标必须是失败的任务")
    tool, model, effort = decision.get("tool"), decision.get("model") or "auto", decision.get("effort") or ""
    require(tool in TOOLS, f"不支持的工具 {tool}")
    state = (environment.get("tools") or {}).get(tool)
    require(isinstance(state, dict), f"此执行环境未报告 {tool} 的可用性（Worker 版本较旧）")
    require(state.get("available") is True, f"{tool} 在此执行环境不可用：{state.get('reason') or '原因未报告'}")
    require(model in {"auto", "default"} or model in ((environment.get("models") or {}).get(tool) or []),
            f"模型 {model} 未由 {tool} 公布")
    row = db.execute("SELECT spec FROM attempts WHERE id=?", (failed["current_attempt"],)).fetchone()
    old = json.loads(row["spec"] if row else failed["spec"])
    before = {"tool": old["tool"], "model": old.get("model", "auto"), "effort": old.get("effort", "")}
    require((tool, model, effort) != (before["tool"], before["model"], before["effort"]),
            "推荐的工具、模型与 Effort 与失败的那次相同")
    spec = json.loads(failed["spec"])
    if spec.get("kind") == "review":
        reviewed = reviewed_backend(db, run["id"], failed["ordinal"])
        require(tool != reviewed, f"审查须使用与被审查代码（{reviewed}）不同的工具")
    earlier = [r["id"] for r in db.execute("SELECT id FROM tasks WHERE run_id=? AND ordinal<? ORDER BY ordinal",
                                          (run["id"], failed["ordinal"]))]
    return steps([{**spec, "tool": tool, "model": model, "effort": effort}], known=earlier)[0], before


def switched_from(db, run_id, task_id, spec):
    """What an Attempt about to be created was switched from (D38 §3.3): the latest applied diagnosis switched this task,
    the task still runs that tool, and no Attempt of it was created since."""
    row = db.execute("SELECT number, decision, updated FROM recoveries WHERE run_id=? AND status='applied' "
                     "ORDER BY number DESC LIMIT 1", (run_id,)).fetchone()
    decision = json.loads(row["decision"] or "{}") if row else {}
    if decision.get("action") != "switch_tool" or decision.get("target_task_id") != task_id:
        return None
    if (spec.get("tool"), spec.get("model", "auto"), spec.get("effort", "")) != \
            (decision["tool"], decision.get("model") or "auto", decision.get("effort") or ""):
        return None   # the user changed the task since (a continue): not the Coordinator's switch any more
    if db.execute("SELECT 1 FROM attempts WHERE run_id=? AND task_id=? AND created>=?", (run_id, task_id, row["updated"])).fetchone():
        return None
    return {**decision["switched_from"], "reason": decision.get("reason"), "by": "coordinator", "recovery": row["number"]}
```

`Coordinator.advance` 的决定解析：

```python
            decision = json.loads(raw)
            fields = {"action", "target_task_id", "reason", "instructions", "tool", "model", "effort"}
            require(isinstance(decision, dict) and set(decision) <= fields, "协调者决定字段无效")
            require(decision.get("action") in {"retry", "rework", "switch_tool", "wait_for_user"}, "协调者动作无效")
            require(all(isinstance(decision.get(k), str) and 0 < len(decision[k]) <= 8000
                        for k in ("reason", "instructions")), "协调者必须给出原因与具体操作")
            if decision["action"] == "switch_tool":
                require(all(isinstance(decision.get(k, ""), str) for k in ("tool", "model", "effort")), "换工具的字段须为字符串")
            else:
                decision = {k: v for k, v in decision.items() if k not in {"tool", "model", "effort"}}
```

`finish`：在 `if decision["action"] == "retry" and target and ...` 的规范化之后、现有的目标核对之前，加入：

```python
                if decision["action"] == "switch_tool":
                    try:
                        switched, before = switch(db, fresh, failed, decision, json.loads(item["context"]))
                        decision = {**decision, "switched_from": before}
                    except Error as exc:
                        error = "协调者换工具的推荐未通过核对：" + str(exc)
```

并把现有的目标核对改为只在尚无 `error` 时执行（`if not error and (not target or ...)`）。在 `journal.change_rows(... "task.reset" ...)` 之后：

```python
            if decision["action"] == "switch_tool":
                journal.change(db, "tasks", (run["id"], target["id"]), journal.RECOVERY, "task.tool_switched", decision["reason"],
                               event={"task_id": target["id"], "from": decision["switched_from"],
                                      "to": {k: switched[k] for k in ("tool", "model", "effort")}},
                               spec=dumps(switched))
```

修订摘要：

```python
            if decision["action"] == "switch_tool":
                was = decision["switched_from"]
                summary = (f"自动纠错：「{name}」由 {was['tool']} / {was['model']} 换为 {switched['tool']} / {switched['model']}"
                           + (f"（{reason}）" if reason else ""))
            else:
                summary = f"自动纠错：退回「{name}」" + (f"（{reason}）" if reason else "")
            record(db, run["id"], version, "recovered", summary, fresh["owner_id"], now, recovery_id=item["id"],
                   decision=decision, invalidated=invalidated)
```

（`name` 取 `target` 改写前的任务名；`item` 在 `finish` 中已有 `context` 字段，它是 `recoveries` 行的字典。）

`octopus/engine.py`：
1. 创建 Attempt 时：

```python
                switched = recovery.switched_from(db, rid, task["id"], json.loads(task["spec"]))
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,?,'dispatching',?,?,?)",
                           (aid, rid, task["id"], number, task["spec"], dumps({"switched_from": switched} if switched else {}),
                            now, now))
```

2. 证据更新时保留标记（替换 `retry_of = ...` 两行）：

```python
        # An automatic retry and a Coordinator's switch keep their marks through every evidence update (D34 §5.1, D38 §3.3).
        retry_of = prior.get("transient_retry_of")
        for key in ("transient_retry_of", "switched_from"):
            if prior.get(key):
                evidence[key] = prior[key]
```

3. 瞬时重试的新 Attempt 的证据：

```python
                            dumps({"transient_retry_of": attempt["id"],
                                   **({"switched_from": evidence["switched_from"]} if evidence.get("switched_from") else {})}),
```

- [ ] **Step 4: 运行，确认通过**

Run: `$PY -m unittest tests.test_switch_tool tests.test_recovery tests.test_engine_gates tests.test_runtime tests.test_mvp tests.test_revisions tests.test_journal -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add octopus/recovery.py octopus/engine.py tests/test_switch_tool.py
git commit -m "feat(recovery): 协调者 switch_tool——平台按诊断时的执行环境核对（可用、已发布、与失败不同、审查独立、未关闭），改写任务并重跑；新 Attempt 记录 switched_from，变更记录与修订写明（D38 §3.3）"
```

---

### Task 5: 规划不再内置替换，改为看工具是否可用

**Files:**
- Modify: `octopus/planning.py`（去掉 `REASSIGN`；`plan_step(models, tools, limitations)`）
- Modify: `octopus/domain.py:705-711`（传入 `tools` 与 `limitations`）
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: `capabilities.tools`、`capabilities.limitations`（Task 1）。
- Produces: `planning.plan_step(models=None, tools=None, limitations=None)`。

- [ ] **Step 1: 改测试**

`test_the_plan_is_validated_by_code_and_appended_to_the_same_session`：

```python
        self.assertEqual([t["spec"]["tool"] for t in run["tasks"]], ["kiro", "claude", "codex", "opencode"])
        self.assertFalse(any("K01" in note for note in applied["notes"]))   # no built-in swap (D38 A5)
```

新增：

```python
    def test_the_planner_sees_which_tools_this_environment_can_start(self):
        tools = {"claude": {"available": True}, "opencode": {"available": False, "reason": "opencode 命令不存在"}}
        prompt = planning.plan_step({"claude": ["auto"]}, tools, ["已知问题 K01：OpenCode …"])["prompt"]
        self.assertIn('"opencode": {"available": false, "reason": "opencode 命令不存在"}', prompt)
        self.assertIn("已知问题 K01", prompt)
        self.assertIn("available 为 false 的工具不要选择", prompt)
        self.assertFalse(hasattr(planning, "REASSIGN"))
```

- [ ] **Step 2: 运行，确认失败**

Run: `$PY -m unittest tests.test_planning -v`
Expected: FAIL（工具仍为 claude；`plan_step` 不接受 `tools`）

- [ ] **Step 3: 实现**

删除 `REASSIGN` 常量及其注释，删除 `expand` 中 `if step["tool"] in REASSIGN:` 一段。`plan_step` 签名改为 `def plan_step(models=None, tools=None, limitations=None):`，在「此执行环境公布的模型」一段之前加：

```python
## 此执行环境的 Coding 工具
{dumps(tools or {})}
available 为 false 的工具不要选择（reason 为原因）；没有列出时表示执行环境未报告。
执行环境的已知限制：{dumps(limitations or [])}
按任务性质与这些事实选择工具，并在交接文件中说明选择依据。
```

`octopus/domain.py` 规划处：

```python
            capabilities = {}
            if body.get("worker_id"):
                with self.db.read() as db:
                    row = db.execute("SELECT config FROM workers WHERE id=?", (body["worker_id"],)).fetchone()
                capabilities = json.loads(row["config"]).get("capabilities", {}) if row else {}
            body = {**{k: v for k, v in body.items() if k != "plan"},
                    "steps": [plan_step(capabilities.get("models", {}), capabilities.get("tools"), capabilities.get("limitations"))]}
```

- [ ] **Step 4: 运行，确认通过**

Run: `$PY -m unittest tests.test_planning tests.test_mvp tests.test_runtime -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add octopus/planning.py octopus/domain.py tests/test_planning.py
git commit -m "feat(planning): 去掉 OpenCode→Claude 的内置替换；规划提示列出各工具在执行环境中是否可用与已知限制，由 Kiro 自行选择（D38 A5）"
```

---

### Task 6: 换用记录可见：MCP、报告、控制台与 Skill

**Files:**
- Modify: `octopus/mcp.py`（`compact_status` 的 `latest` 带 `switched_from`）
- Modify: `octopus/report.py`（`_attempt` 的 `switched`；Markdown 与 HTML 各一行）
- Modify: `web/src/api/types.ts`（`AttemptEvidence.switched_from`、`RecoveryPolicy.switch_tools?`）
- Modify: `web/src/features/sessions/TaskList.tsx`（「换工具」一行）
- Modify: `web/src/components/RecoveryFields.tsx`（「允许协调者推荐换用其他工具或模型」）
- Modify: `web/src/features/sessions/SessionView.tsx:213`（关闭换工具时写明）
- Modify: `skills/kirocrew-on/SKILL.md`（§3 状态表、§4 迭代）
- Test: `tests/test_mcp_status.py`、`tests/test_report.py`、`web/src/features/sessions/SessionView.test.tsx`、`web/src/features/launch/state.test.ts`（如需要）

**Interfaces:**
- Consumes: 证据 `switched_from: {tool, model, effort, reason, by, recovery}`（Task 4）；`recovery.switch_tools`（Task 2）。

- [ ] **Step 1: 写失败的测试**

`tests/test_mcp_status.py` 追加：

```python
    def test_a_switched_attempt_says_what_it_was_switched_from(self):
        session = json.loads(json.dumps(SESSION))
        switched = {"tool": "opencode", "model": "auto", "effort": "", "reason": "opencode 不可用", "by": "coordinator", "recovery": 1}
        session["tasks"][0]["attempts"][-1]["evidence"]["switched_from"] = switched
        latest = mcp.compact_status(session, [])["session"]["tasks"][0]["latest"]
        self.assertEqual(latest["switched_from"], switched)
```

（`SESSION` 的任务与 Attempt 结构以文件中的夹具为准；如果第一个任务没有 Attempt，改用有 Attempt 的任务。）

`tests/test_report.py` 追加：

```python
    def test_a_switch_is_written_in_the_task_section(self):
        value = view()
        value["tasks"][0]["attempts"][-1]["evidence"]["switched_from"] = {
            "tool": "opencode", "model": "auto", "effort": "", "reason": "opencode <不可用>", "by": "coordinator", "recovery": 1}
        md, page = report.markdown(value), report.html(value)
        self.assertIn("- 换工具：opencode / auto → claude / auto（协调者推荐）：opencode \\<不可用\\>", md)
        self.assertIn("换工具：opencode / auto → claude / auto（协调者推荐）：opencode &lt;不可用&gt;", page)
```

`web/src/features/sessions/SessionView.test.tsx` 追加：

```tsx
  test("an attempt the Coordinator switched says from which tool and why (D38)", async () => {
    const task = blocked.tasks[0];
    const evidence = { ...task.attempts![0].evidence,
      switched_from: { tool: "opencode", model: "auto", effort: "", reason: "opencode 不可用", by: "coordinator", recovery: 1 } };
    open({ ...blocked, tasks: [{ ...task, attempts: [{ ...task.attempts![0], evidence }] }, blocked.tasks[1]] } as Run);
    await userEvent.click(await screen.findByText("执行证据与历史 Attempt"));
    expect(screen.getByText("由协调者从 opencode / auto 换为 claude / auto：opencode 不可用")).toBeInTheDocument();
  });

  test("a Session that may not switch tools says so", async () => {
    open({ ...blocked, recovery: { ...blocked.recovery!, policy: { enabled: true, max_cycles: 2, switch_tools: false } } } as Run);
    expect(await screen.findByText("自动纠错已开启 · 最多 2 轮 · 不换工具")).toBeInTheDocument();
  });
```

- [ ] **Step 2: 运行，确认失败**

Run: `$PY -m unittest tests.test_mcp_status tests.test_report -v` 与 `npm --prefix web test -- --run src/features/sessions/SessionView.test.tsx`
Expected: FAIL（没有 `switched_from`、没有「换工具」行、控制台没有该文字）

- [ ] **Step 3: 实现**

`octopus/mcp.py` 的 `latest()` 中 `if evidence.get("candidate"):` 之前：

```python
        if evidence.get("switched_from"):   # D38: the Coordinator recommended this tool instead
            value["switched_from"] = evidence["switched_from"]
```

`octopus/report.py`：`_attempt` 的返回值加：

```python
        "switched": (f"{_line(switched.get('tool'), 40)} / {_line(switched.get('model'), 80)} → "
                     f"{spec.get('tool') or '—'} / {spec.get('model') or 'auto'}（协调者推荐）：{_line(switched.get('reason'))}")
                    if (switched := evidence.get("switched_from")) else None,
```

`markdown` 中「请求：…；实际：…」一行之后：

```python
        if final["switched"]:
            out.append(f"- 换工具：{_md(final['switched'])}")
```

`html` 的 `items` 中「请求：…」之后：

```python
        if final["switched"]:
            items.append(f"换工具：{e(final['switched'])}")
```

（`_md` 会把 `/`、`→` 原样保留，`<`、`>` 转义为 `\<`、`\>`；若 `_md` 也转义了其他出现在期望中的字符，以 `_md` 的实际结果调整测试的期望字符串，并在台账中记录。）

`web/src/api/types.ts`：

```ts
export interface RecoveryPolicy {
  enabled: boolean;
  max_cycles: number;
  /** false: the Coordinator may not recommend another tool (D38 §3.5); absent means allowed. */
  switch_tools?: boolean;
}
```

`AttemptEvidence` 中加：

```ts
  /** The Coordinator recommended this Attempt's tool instead of the one that failed (D38 §3.3). */
  switched_from?: { tool: string; model: string; effort: string; reason?: string; by: string; recovery: number } | null;
```

`TaskList.tsx` 的「请求」一行之后：

```tsx
                          {attempt.evidence.switched_from && (
                            <Meta label="换工具" value={`由协调者从 ${attempt.evidence.switched_from.tool} / ${attempt.evidence.switched_from.model} `
                              + `换为 ${attempt.spec.tool} / ${attempt.spec.model}：${attempt.evidence.switched_from.reason ?? "未说明原因"}`} />
                          )}
```

`RecoveryFields.tsx` 在「最多自动纠错轮次」之后加：

```tsx
      <div className="flex items-center gap-2.5">
        <Checkbox id={idPrefix + "-switch"} checked={value.switch_tools !== false}
          onCheckedChange={(checked) => {
            const { switch_tools: _ignored, ...rest } = value;
            onChange(checked === true ? rest : { ...rest, switch_tools: false });
          }} />
        <Label htmlFor={idPrefix + "-switch"}>工具本身失败时，允许协调者推荐换用其他可用的工具或模型</Label>
      </div>
```

`SessionView.tsx:213`：

```tsx
          {run.recovery.policy.enabled
            ? `自动纠错已开启 · 最多 ${run.recovery.policy.max_cycles} 轮${run.recovery.policy.switch_tools === false ? " · 不换工具" : ""}`
            : "自动纠错未开启 · 可手动诊断"}
```

`skills/kirocrew-on/SKILL.md`：
- §3 状态表 `blocked` / `failed` 一行末尾加：「`attention.title` 为「需要换用工具或模型」时，向用户说明原因（`attention.detail`），建议换用一个可用的工具；经用户同意后用 `octopus_continue` 的 `execution.tool` / `execution.model` 续作，不要自行决定。」
- §4 把「例如某个工具失败时换用 `claude`。」改为：「工具本身失败时，自动纠错会请 KiroCrew 协调者推荐换用的工具（`octopus_status` 的 `latest.switched_from` 写明从哪个工具换来、原因）；会话关闭了换工具（`recovery.switch_tools: false`）或协调者等待用户时，由你向用户建议并经同意后续作。」

- [ ] **Step 4: 运行，确认通过**

Run: `$PY -m unittest tests.test_mcp_status tests.test_report tests.test_mcp_report tests.test_static_console -v`、`npm --prefix web test -- --run`、`npm --prefix web run build`
Expected: PASS；构建成功

- [ ] **Step 5: Commit**

```bash
git add octopus/mcp.py octopus/report.py web/src skills/kirocrew-on/SKILL.md tests/test_mcp_status.py tests/test_report.py
git commit -m "feat(console,mcp,report): 换工具记录可见——任务的 Attempt、MCP 状态与报告写明由协调者从哪个工具换来及原因；自动纠错设置可关闭换工具；Skill 说明（D38 §3.4、§3.5）"
```

（构建产物如果被跟踪（`octopus/ui` 或 `web/dist`），按仓库既有做法一并提交；不要提交 `web/node_modules`。）

---

### Task 7: 文档

**Files:**
- Modify: `docs/MVP-RELEASE.md`（§14.2 第 4、6 项；新增 §14.14 D38 第 1 期）
- Modify: `docs/OPERATIONS.md`（Worker `/health` 的 `tools`、刷新能力的方法）
- Modify: `docs/BACKLOG.md`（M2 映射策略一行改写：回退链由协调者推荐，不内置）

- [ ] **Step 1:** §14.2 第 4 项改为「◐ 已有：显式指定；D38：工具层失败由协调者推荐换工具（平台核对、记录）。剩余：映射来源记录、个人 / Team 偏好」；第 6 项「剩余」中去掉「工具回退」。
- [ ] **Step 2:** 新增 §14.14「换工具第 1 期（D38）」：交付要点、本地测试、EC2 验收待做（计划中的验收步骤）。
- [ ] **Step 3:** OPERATIONS 写明：升级后在控制台「执行环境」刷新能力（或 `GET /v1/workers/<id>/capabilities`），平台才会保存 `tools`；未刷新时派发照旧，协调者的换工具推荐因「未报告可用性」被拒。
- [ ] **Step 4:** 全量测试：`$PY -m unittest discover -s tests -p 'test_*.py'` 与 `npm --prefix web test -- --run`，结果写入台账。
- [ ] **Step 5: Commit**

```bash
git add docs/MVP-RELEASE.md docs/OPERATIONS.md docs/BACKLOG.md
git commit -m "docs: D38 第 1 期换工具——进度、运维（刷新 Worker 能力）与后续排期"
```

---

## EC2 验收（最终审查与修复之后；每一步先确认没有运行中的会话）

1. `deploy.sh push`（平台）与 `deploy.sh worker-host push w1`，从固定的部署工作树；之后刷新两个 Worker 的能力，`GET /v1/workers/<id>/capabilities` 中有 `tools`，四个工具的 `available` 与主机实际一致。
2. 在 `LiboMa/bedrock-claude-call` 上提交一个会话：实现任务 `claude`、`model` 为未发布的 `claude-not-published`，审查任务 `codex`。期望：
   - 第 1 次 Attempt 派发被拒（`model_unavailable`），会话进入诊断；
   - 协调者给出 `switch_tool`（同一工具 `auto`，或另一个可用工具），平台核对通过；
   - 实现任务以新配置完成、审查通过，会话 `completed`；
   - 控制台 Attempt 显示「由协调者从 claude / claude-not-published 换为 …」，MCP `octopus_status` 的 `latest.switched_from`、报告的「换工具」行一致；修订历史有对应摘要。
3. 同样的会话以 `recovery.switch_tools: false` 提交：协调者被告知不可换工具，给出 `wait_for_user`（或推荐被拒），`attention.title` 为「需要换用工具或模型」。
4. 规划：一个不指定工作流的需求，规划提示中能看到工具可用性（在规划 Attempt 的 `instructions.md` 中核对），计划中没有「K01 改由 claude」的说明。
