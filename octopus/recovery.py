"""Durable Coordinator decisions and bounded repair, using the existing Worker."""
import json
import re
import time

from . import crew_entry, journal, sources
from .db import dumps, uid
from .domain import SETTLED, TOOLS, required_checks, runs_checks, steps, unserved
from .security import Error, require
from .transport import worker_token

LIVE = {"pending", "dispatching", "dispatch_unknown", "running", "waiting_approval"}
FAILURES = {"implementation", "tests", "environment", "input", "auth", "model",
            "native_stopped", "native_error", "contract", "none"}
NEEDS_INPUT = {"input", "auth", "model", "native_stopped"}
# The watchdog's limits (D39 §3.2): minutes without native progress, and of one Attempt in all; a session may change them.
IDLE_MINUTES, ATTEMPT_MINUTES = 30, 170   # below KiroCrew's own subagent deadline (180 min), which reaps without a retry
LIMITS = {"idle_minutes": (IDLE_MINUTES, 5, 240), "attempt_minutes": (ATTEMPT_MINUTES, 30, 720)}
# The automatic recovery budget beside max_cycles (D42): retries without a diagnosis and the time since the user's last word.
BUDGET = {"max_retries": (4, 0, 20, "的整数"), "budget_minutes": (720, 30, 4320, "的整数（分钟）")}

# The native runtime's own faults (design §8.1 L0, D57 §6.3): neither the task's nor the diagnosis's. Each is retried once
# without a recovery cycle, within the automatic retry budget; their words are KiroCrew's (tests/kirocrew_contract.json).
CANCELLED = re.compile(r"stop_reason=cancelled|turn cancelled by the runtime", re.I)
# The Agent's ACP process ended under the task (a Gateway restart, a killed process): design §8.1 L0, not the task's fault.
# KiroCrew words it two ways: AcpError "ACP process exited" (acp/client.py) and AcpProcessDied "Runtime process died …"
# (acp/session_handle.py, EC2 run-f7670bfb…).
EXITED = re.compile(r"ACP process exited|AcpProcessDied|Runtime process died", re.I)
# The Agent ended, then KiroCrew could not clean up a process its tree left behind (process_identity.py, EC2 run-6d7c132b…).
CLEANUP = re.compile(r"process group \d+ still has members", re.I)
# The Agent's sandbox could not be set up (D57 §13.9: the diagnosis that never began, EC2 2026-10-21).
SANDBOX = re.compile(r"AcpSandboxInitFailed|sandbox failed to initialize", re.I)
FAULTS = (("runtime_sandbox", SANDBOX), ("runtime_cancelled", CANCELLED), ("runtime_exited", EXITED), ("runtime_cleanup", CLEANUP))
PLATFORM_FAULT = re.compile("|".join(pattern.pattern for _, pattern in FAULTS), re.I)
# What each is, on the timeline where it is retried; and KiroCrew's teardown of a running child with its parent.
WHY = {"runtime_sandbox": "原生运行时的沙箱未能初始化", "runtime_cancelled": "运行时取消了原生任务",
       "runtime_exited": "原生运行时进程退出", "runtime_cleanup": "原生运行时未能清理残留进程",
       "runtime_teardown": "原生任务被 KiroCrew 回收（期间没有人打开原生页面）"}
# D57 §6.1: replans for a problem in a task's contract that leave the Session's one replan unused
EXEMPT_REPLANS = 2


def fault(native, entered=True):
    """The native runtime's fault a finished native run — a task's or a diagnosis's — ended on, or None. A stop is a
    person's when someone may have made it in the native page (``entered``: crew_entry.entered), theirs to resume and never
    retried; one nobody could have made is KiroCrew tearing a running child down with its parent (teardown-finding)."""
    if native.get("stopped"):
        return None if entered else "runtime_teardown"
    error = native.get("error") or ""
    return next((kind for kind, pattern in FAULTS if pattern.search(error)), None)


def failing_gate(evidence, failure_type=None):
    """What an Attempt failed on, for counting the diagnoses that waited on it (D57 §13.8): the gate it names, else its first
    failed gate record — never the review_bound recorded after it when a failed review was put back — else its failure_type."""
    first = next((g.get("name") for g in evidence.get("gates") or []
                  if g.get("status") == "fail" and g.get("name") != "review_bound"), None)
    return evidence.get("gate") or first or evidence.get("failure_type") or failure_type


# D71 (owner 2026-10-22, gates retrospective): the Coordinator waits for a person only for what only the person can supply.
WAIT_FOR = {"credentials", "input"}


def fallback(decision, failed_id, why):
    """D71: a decision the Session cannot follow without stopping — a wait for something other than WAIT_FOR, a refused
    decision, or none at all — becomes a retry of the failed task with what the Coordinator said and why it was not kept."""
    decision = decision or {}
    said = decision.get("instructions") or ""
    return {"action": "retry", "target_task_id": failed_id, "fallback_from": decision.get("action") or "none",
            "reason": decision.get("reason") or why,
            "instructions": "；".join(filter(None, [said, f"（平台：{why}，按失败原因直接修复后重新执行本任务）"])),
            **({"refused": decision["refused"]} if decision.get("refused") else {})}


def replan_quota(snapshot, failure):
    """(used, exempt): whether the Session's one replan is used, and whether a replan now would leave it unused (D57 §6.1):
    the failed Attempt's evidence shows a problem in its contract (``failure``, never the Coordinator's word), and fewer
    than EXEMPT_REPLANS such replans were made."""
    from .planning import replans
    done = replans(snapshot)
    return any(not r.get("exempt") for r in done), \
        failure == "contract" and sum(1 for r in done if r.get("exempt")) < EXEMPT_REPLANS


def policy(value=None, *, legacy=False):
    if value is None:
        return {"enabled": not legacy, "max_cycles": 2}
    require(isinstance(value, dict) and set(value) <= {"enabled", "max_cycles", "switch_tools", *LIMITS, *BUDGET},
            "自动纠错配置无效")
    enabled, limit, switch = value.get("enabled", True), value.get("max_cycles", 2), value.get("switch_tools", True)
    require(isinstance(enabled, bool) and type(limit) is int and 1 <= limit <= 5 and isinstance(switch, bool),
            "自动纠错次数须为 1–5，enabled 与 switch_tools 须为布尔值")
    minutes = {}
    for key, (default, low, high) in LIMITS.items():
        number = value.get(key, default)
        require(type(number) is int and low <= number <= high, f"{key} 须为 {low}–{high} 的整数（分钟）")
        if number != default:
            minutes[key] = number
    for key, (default, low, high, unit) in BUDGET.items():
        number = value.get(key, default)
        require(type(number) is int and low <= number <= high, f"{key} 须为 {low}–{high} {unit}")
        if number != default:
            minutes[key] = number
    # Defaults (switching tools on, the watchdog's limits) are not stored, so existing settings keep their shape.
    return {"enabled": enabled, "max_cycles": limit, **({} if switch else {"switch_tools": False}), **minutes}


def outcome(text):
    try:
        require(isinstance(text, str) and len(text) <= 64000, "任务结果文件缺失或过大")
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise Error(409, "outcome_invalid", "任务缺少有效的结构化 outcome.json，无法判定审查是否通过") from exc
    require(isinstance(value, dict) and value.get("decision") in {"pass", "changes_requested", "blocked"},
            "outcome.json 的 decision 必须为 pass、changes_requested 或 blocked", 409, "outcome_invalid")
    require(isinstance(value.get("summary"), str) and 0 < len(value["summary"]) <= 8000,
            "outcome.json 缺少具体结论", 409, "outcome_invalid")
    failure = value.get("failure_type", "none" if value["decision"] == "pass" else "implementation")
    require(failure in FAILURES, "outcome.json failure_type 无效", 409, "outcome_invalid")
    def finding(item):
        return isinstance(item, str) or (isinstance(item, dict) and
               any(isinstance(item.get(k), str) and item[k].strip()
                   for k in ("issue", "message", "description", "title")))
    require(isinstance(value.get("findings", []), list) and
            all(finding(item) for item in value.get("findings", [])),
            "outcome.json findings 应为问题列表", 409, "outcome_invalid")
    # acceptance / suggestions are reports, not the verdict: malformed entries are dropped or normalized with a note
    # and never change the decision (D34 §6.2).
    notes, checks = [], []
    raw = value.get("acceptance", [])
    if not isinstance(raw, list):
        notes.append("acceptance 不是列表，已忽略")
        raw = []
    for item in raw[:30]:
        name = item.get("item") if isinstance(item, dict) else None
        result = str(item.get("result", "")).strip().lower() if isinstance(item, dict) else ""
        # pending: an item that needs a person's judgement (D46 P3, the Evaluator's 「验收待确认」)
        if not (isinstance(name, str) and name.strip()) or result not in {"pass", "fail", "n/a", "pending"}:
            notes.append(f"忽略无效的验收结果：{dumps(item)[:80]}")
            continue
        entry = {"item": name.strip()[:500], "result": result}
        if item.get("evidence") not in (None, ""):
            entry["evidence"] = (item["evidence"] if isinstance(item["evidence"], str) else dumps(item["evidence"]))[:2000]
        checks.append(entry)
    raw = value.get("suggestions", [])
    suggestions = [x.strip()[:1000] for x in raw if isinstance(x, str) and x.strip()][:50] if isinstance(raw, list) else []
    return {**value, "failure_type": failure, "acceptance": checks, "suggestions": suggestions,
            **({"normalized": notes} if notes else {})}


def quality_instructions(folder, contract=None, stage=None):
    if stage == "planning":   # D46: the planner's conclusion is its plan, not a code verdict
        return f"""

## 必须交付可判定的任务结论
除 handoff.md 外，将实际结论写到 {folder / 'outcome.json'}，严格 JSON：
{{"decision":"pass","summary":"计划要点与依据","failure_type":"none","findings":[]}}
decision=pass 表示已给出符合要求的计划 JSON；需求缺少必要信息、无法规划时用 blocked，failure_type 为 input，
并在 findings 中写明缺少什么。本文件是本次 Attempt 的真实结论，不能复制旧结论。
"""
    accept = ""
    if contract and contract.get("acceptance") and contract.get("role") == "evaluator":   # D46 P3: the Evaluator's own set
        accept = """
本任务有验收项：在 outcome.json 中加入 "acceptance"，逐项对应本任务契约的每条验收项：
  "acceptance":[{"item":"验收项原文","result":"pass","evidence":"实际核对的命令、输出或文件:行"}]
result 只能是 pass、fail 或 pending（需要人判断时，写明已核对的部分）；每项必须有 evidence。会话以这些判定为准：
任一项 fail 即 changes_requested；有 pending 而没有 fail 时 decision 写 pass——平台会停下，等用户确认这些项。
"""
    elif contract and contract.get("acceptance"):
        accept = """
本任务有验收项：在 outcome.json 中加入 "acceptance"，逐项对应本任务契约的每条验收项：
  "acceptance":[{"item":"验收项原文","result":"pass","evidence":"实际执行的命令与结果"}]
result 只能是 pass、fail 或 n/a。验收标准之外、且不是必须修复的缺陷或安全问题的改进，写入
"suggestions"（字符串列表），不作为 changes_requested 的理由；必须修复的问题仍按上面的规则判定。
"""
    if contract and contract.get("role") == "knowledge":   # D46 P4: what the platform writes as the repository's lessons
        accept += """
在 outcome.json 中加入 "knowledge"：本会话中长期有效的项目知识，至多 5 条，没有就写 []：
  "knowledge":[{"rule":"一句可直接照做的话（至多 300 字符）","avoid":"可选：不要怎么做（至多 200 字符）"}]
平台核对后写入此执行环境的 KiroCrew lessons，只给同一仓库今后的任务；含秘密或超长的条目不写入。
"""
    gates = ""
    if contract and contract.get("kind") == "test" and contract.get("verify"):
        gates += """
平台在通过检查之后，还会在会话基线（第一个任务改动之前的源码）上叠加本任务改动的文件，再运行一次本任务的检查命令：
新增或修改的测试应在那里失败，才能说明它们测到了新行为；在改动之前也能通过时，平台会记一条提示（tests_fail_on_baseline），
不会因此退回，但请确认测试确实覆盖了改动。
测试任务只写测试，不修改实现文件（需要改实现时在 outcome.json 中说明并使用 changes_requested）；检查命令中不安装依赖
（依赖已在工作区中），测试产生的文件（覆盖率、报告）写入 .gitignore。
"""
    if contract and contract.get("paths"):
        gates += """
此前任务已验收的交付物，应只修改本任务 paths 中列出的那些；改动了其他此前任务的文件时，平台会记一条提示并列出这些文件
（artifacts_locked），交给后续审查确认。确需改动时，在交接中写明改了哪些文件、为什么。
"""
    return f"""

## 必须交付可判定的任务结论
除 handoff.md 外，将实际结论写到 {folder / 'outcome.json'}，严格 JSON：
{{"decision":"pass","summary":"具体完成内容与实际验证",
  "failure_type":"none","findings":[]}}
decision=pass 仅在本任务及验收实际完成时使用。
发现必须修复的源码、测试或安全问题，使用 changes_requested，并逐项填写 findings；
findings 使用字符串列表，或带 issue/message/description/title 之一的对象列表（可附 file、line、severity）。
不要仅写“审查完成”就使用 pass。需要输入、认证或权限时使用 blocked。
failure_type 可选 implementation/tests/environment/input/auth/model/native_error/contract/none。
缺少源码、仓库来源或需求信息归为 input；权限、认证、额度归为 auth。
本文件是本次 Attempt 的真实结论，不能复制旧结论或编造命令输出。
""" + accept + gates


def coordinator_instructions(context, folder):
    return f"""
## 你是此 Run 的诊断与纠错 Coordinator
完整读取 context.json、失败任务的真实返回、交接路径及当前源码，判断可执行恢复办法。执行者没有写交接时，
evidence.handoff_text 是平台根据其结果生成的交接。
以下门禁只记提示（warn），不会让任务失败，也不会进入诊断：diff_present、artifacts_locked、tests_fail_on_baseline、
handoff_present、outcome_valid。evidence.gates 中 status 为 warn 的记录不是本次失败的原因。
只诊断和提出修复指令；不要自己实现业务修复或派生任务。Engine 会按决定派发所选 Coding Agent。
保留工作流配置，不迁移源码、不删除历史。不要把停止或权限拒绝当作可绕过的失败。
失败原因在于工具本身时，可以用 switch_tool 推荐换用的工具与模型。工具本身的失败是：failure_type 为 tool
（工具在执行环境中不可用、模型未公布或不受支持、实际运行的后端与请求不符），或 failure_type 为 native_error
且 evidence.transient_retry_of 存在（同一工具的原生错误在自动重试后再次发生）。
evidence.watchdog 存在时是看门狗停止了没有进展的任务：先判断是任务运行了不会结束、等待输入或过慢的命令（开发服务器、交互式提示、
完整构建）——用 retry 并给出具体做法（后台运行并设超时、非交互参数、拆分步骤，或请用户调大 recovery.idle_minutes）——
还是工具本身挂起；只有后者才用 switch_tool。
- 只从 context.environment.tools 中 available 为 true 的工具、environment.models 中该工具公布的模型（或 auto）中选择；
- 工具、模型与 Effort 不能与失败的那次完全相同；模型未公布时，同一工具改用 auto 也是有效的推荐；
- 审查类任务选择与被审查代码实际所用不同的工具；换用后的实现工具也不能是其后的审查任务所用的工具；
- 参考 environment.limitations 中的已知问题；
- environment.switch_tools 为 false 时不能换工具：在原工具上用 retry，写明具体做法；不确定时选最可能的修复。
实现、测试、契约或门禁本身的问题仍用 retry 或 rework，不换工具。
计划本身有问题时（任务拆分不当、缺少必要的步骤、顺序错误、任务目标与需求不符，在原任务上重试或返工无法完成需求）用 replan：
平台保留已验收的任务，请 KiroCrew 重新规划失败的任务及其后的工作；写明原计划的问题与重新规划的要求。
每个会话只能重新规划一次：environment.replanned 为 true 时不能再用 replan，除非 environment.replan_exempt 为 true
（平台按失败证据判定问题出在任务合同上，这次重新规划不占额度）。
任务的 spec 带 requested（需求中指定的工具或模型）时，不能换用其他工具或模型：在指定的工具上 retry 或 rework。
检查命令以退出码 127 失败表示命令在执行环境中不存在（environment.commands 列出常见命令中可用与没有的）：这是任务契约中 verify 的问题，
重试或返工无法修改它——未重新规划过时用 replan 并写明改用的命令，否则用 wait_for_user（missing 为 input），请用户续作时修改该任务的 verify。
判断是否已有足够源码、依赖与输入；缺少仓库、需要用户授权或认证时 wait_for_user（missing 为 credentials），不猜测项目地址。
只有缺少仓库凭证或授权、或缺少只有用户能提供的需求信息时才用 wait_for_user，并写 missing；其余情况（包括不确定）
选最可能的修复用 retry 或 rework，不要停下等人（平台会把其余的 wait_for_user 当作 retry 执行）。
若是当前任务可自行修复的问题用 retry；若审查问题需要上游实现者修复用 rework。
context 有 locked_files 时（只出现在平台升级前被 artifacts_locked 拦下的任务上；现在这项门禁只记提示）：这些修改确应由失败的任务完成时，
retry / rework 失败的任务并在决定中加 "paths_add": [locked_files 中的文件]（至多 10 个），平台把它们加入该任务的 paths；
reason 写明为什么由它修改。不能加 locked_files 之外的文件，不能用于其他任务或其他动作。没有 locked_files 时不要写 paths_add。
目标只能是失败步骤或其上游步骤：target_task_id 只能取 context.allowed_targets 中的 id。排在失败任务之后的任务（例如计划中
审查之后的「按审查修复」任务）不能作为目标——审查发现的问题交给产出该代码的上游任务 rework，其后的任务会自动重跑。
context.previous_decisions 是此前各轮的决定及其结果（status）；带 refused 的是平台拒绝过的决定，refused 写明原因——不要重复同一决定。
把失败反馈、所需改动与重新验证条件写具体。
结果契约：decision 为 pass/changes_requested/blocked；summary 为具体结论；findings 为字符串列表，
或具有 issue/message/description/title 之一的对象列表。pass 可以记录不阻断本步骤验收的观察项，
不能从 findings 非空推断 JSON 格式错误。区分已核实原因和推测，不发明引擎的额外规则。
这是诊断任务；失败业务任务的输入在 coordination 中。下一次重试一定携带新的 repair 字段，
其中包含本决定和失败证据，不能因为本诊断文件没有 repair 就称下一次业务执行为首次执行。
失败、证据、各任务的配置与执行环境在 {folder / 'context.json'} 的 coordination 中，完整读取后再判断（不在此重复）。

把唯一有效的处置写到 {folder / 'decision.json'}，严格 JSON：
{{"action":"retry","target_task_id":"当前或上游 Task ID",
  "reason":"真实根因与判断依据","instructions":"具体修复动作、输入与复验要求"}}
换工具时：
{{"action":"switch_tool","target_task_id":"失败的 Task ID","tool":"codex","model":"auto","effort":"",
  "reason":"为什么原工具不能完成、为什么选这个工具","instructions":"以新工具重新执行本任务的要求"}}
重新规划时：
{{"action":"replan","reason":"原计划的问题","instructions":"重新规划的要求（需要哪些任务、注意什么）"}}
等用户时：
{{"action":"wait_for_user","missing":"credentials","reason":"缺少什么","instructions":"用户下一步如何补充"}}
action 只能为 retry、rework、switch_tool、replan、wait_for_user；wait_for_user 与 replan 不需 target_task_id；
wait_for_user 的 missing 只能是 credentials（仓库凭证、授权、认证）或 input（只有用户能提供的需求信息）。
同时写 handoff.md，最后汇总判断。提出 wait_for_user 是诊断完成，不是诊断 Agent 自身执行失败。
"""


def is_review(spec):
    """A review task: its contract says so, or — the built-in templates carry no kind — its id is "review"."""
    return spec.get("kind") == "review" or (not spec.get("kind") and spec.get("id") == "review")


NOT_AUTHORS = ("design", "test")   # owner 2026-10-21: a test task does not write the change a review looks at (roles.py)


def reviewed_backend(db, run_id, ordinal):
    """The backend that wrote the code a review at ``ordinal`` reviews: the newest accepted Attempt of an earlier task
    that is not itself a review, a design, a test or a planning task (D38 §3.3, D41)."""
    from .planning import planning_task as planner
    snapshot = json.loads((db.execute("SELECT snapshot FROM runs WHERE id=?", (run_id,)).fetchone() or ["{}"])[0])
    planning_task = lambda task_id: planner(task_id, snapshot)
    for row in db.execute("SELECT a.spec, a.evidence FROM attempts a JOIN tasks t ON t.run_id=a.run_id AND t.id=a.task_id "
                          "WHERE a.run_id=? AND t.ordinal<? AND a.status='accepted' ORDER BY a.updated DESC, a.created DESC",
                          (run_id, ordinal)):
        spec = json.loads(row["spec"])
        if is_review(spec) or spec.get("kind") in NOT_AUTHORS or planning_task(spec.get("id")):
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
    require(isinstance(state, dict), f"此执行环境未报告可用性（{tool}；Worker 版本较旧或尚未刷新执行环境能力）")
    require(state.get("available") is True, f"{tool} 在此执行环境不可用：{state.get('reason') or '原因未报告'}")
    require(model in {"auto", "default"} or model in ((environment.get("models") or {}).get(tool) or []),
            f"模型 {model} 未由 {tool} 公布")
    row = db.execute("SELECT spec FROM attempts WHERE id=?", (failed["current_attempt"],)).fetchone()
    old = json.loads(row["spec"] if row else failed["spec"])
    before = {"tool": old["tool"], "model": old.get("model", "auto"), "effort": old.get("effort", "")}
    require((tool, model, effort) != (before["tool"], before["model"], before["effort"]),
            "推荐的工具、模型与 Effort 与失败的那次相同")
    spec = json.loads(failed["spec"])
    wanted = spec.get("requested") or {}
    require(not wanted.get("tool") or wanted["tool"] != spec["tool"],
            f"需求指定了此任务使用 {spec['tool']}：不换用其他工具，请用 wait_for_user 说明")   # D45: the user's own choice
    require(not wanted.get("model") or model == wanted["model"],
            f"需求指定了此任务使用模型 {wanted.get('model')}：不换用其他模型，请用 wait_for_user 说明")
    if is_review(spec):
        reviewed = reviewed_backend(db, run["id"], failed["ordinal"])
        require(tool != reviewed, f"审查须使用与被审查代码（{reviewed}）不同的工具")
    elif spec.get("kind") not in NOT_AUTHORS:   # the reviews that follow stay independent of the code this task will write
        for row in db.execute("SELECT spec FROM tasks WHERE run_id=? AND ordinal>? ORDER BY ordinal", (run["id"], failed["ordinal"])):
            later = json.loads(row["spec"])
            require(not (is_review(later) and later["tool"] == tool),
                    f"其后的审查「{later.get('name') or later['id']}」使用 {tool}，审查须与被审查代码的工具不同；请换用其他工具")
    earlier = [r["id"] for r in db.execute("SELECT id FROM tasks WHERE run_id=? AND ordinal<? ORDER BY ordinal",
                                          (run["id"], failed["ordinal"]))]
    switched = steps([{**spec, "tool": tool, "model": model, "effort": effort}], known=earlier)[0]
    switched.pop("verify_required", None)   # D57 §5: which checks must pass, and who wrote them, stay as they were
    return {**switched, **{key: spec[key] for key in ("verify_required", "verify_basis", "origin") if key in spec}}, before


def widen(decision, context, failed, target):
    """D54: the failed task's paths with the files artifacts_locked refused that the Coordinator names — only those, only
    for a retry or rework of that task itself; the merged list, or Error with the reason."""
    add = decision.get("paths_add")
    require(decision["action"] in {"retry", "rework"} and target is not None and target["id"] == failed["id"],
            "paths_add 只用于重试或返工失败的任务")
    locked = set(context.get("locked_files") or [])
    require(locked, "失败的不是 artifacts_locked 门禁：不扩大可改动路径")
    require(isinstance(add, list) and 1 <= len(add) <= 10 and all(isinstance(p, str) for p in add), "paths_add 须为 1–10 个文件路径")
    outside = [p for p in add if p not in locked]
    require(not outside, f"{'、'.join(p[:80] for p in outside[:3])} 不是门禁锁住的文件（只能取 locked_files 中的文件）")
    merged = list(dict.fromkeys([*(json.loads(failed["spec"]).get("paths") or []), *add]))
    require(len(merged) <= 20, "扩大后的可改动路径超过 20 项")
    return merged


def switched_from(db, run_id, task_id, spec):
    """What an Attempt about to be created was switched from (D38 §3.3): the latest applied diagnosis switched this task,
    the task still runs that tool, and no Attempt of it was created since."""
    row = db.execute("SELECT number, decision, updated FROM recoveries WHERE run_id=? AND status='applied' "
                     "ORDER BY number DESC LIMIT 1", (run_id,)).fetchone()
    decision = json.loads(row["decision"] or "{}") if row else {}
    if decision.get("action") != "switch_tool" or decision.get("target_task_id") != task_id:
        return _still_switched(db, run_id, task_id, spec)
    if (spec.get("tool"), spec.get("model", "auto"), spec.get("effort", "")) != \
            (decision["tool"], decision.get("model") or "auto", decision.get("effort") or ""):
        return None   # the user changed the task since (a continue): not the Coordinator's switch any more
    if db.execute("SELECT 1 FROM attempts WHERE run_id=? AND task_id=? AND created>=?", (run_id, task_id, row["updated"])).fetchone():
        return _still_switched(db, run_id, task_id, spec)
    return {**decision["switched_from"], "reason": decision.get("reason"), "by": "coordinator", "recovery": row["number"]}


def _still_switched(db, run_id, task_id, spec):
    """A later Attempt of a switched task (e.g. a retry) that still runs the switched configuration keeps the label, so
    the session, MCP and the report go on saying where the tool came from."""
    row = db.execute("SELECT spec, evidence FROM attempts WHERE run_id=? AND task_id=? ORDER BY number DESC LIMIT 1",
                     (run_id, task_id)).fetchone()
    if not row:
        return None
    before, mark = json.loads(row["spec"]), json.loads(row["evidence"] or "{}").get("switched_from")
    same = all(before.get(k, d) == spec.get(k, d) for k, d in (("tool", None), ("model", "auto"), ("effort", "")))
    return mark if mark and same else None


def budget(db, run, now=None):
    """What the automatic recovery has used since the Session began or the user's latest continue / requested diagnosis
    (D42 §3.1), and the first part that ran out."""
    snapshot = json.loads(run["snapshot"])
    settings = policy(snapshot.get("recovery"), legacy=True)
    since = snapshot.get("budget_since") or run["created"]
    if now is None:   # a Session that stopped stops counting: the time it ran, not the time since
        settled = run["status"] in SETTLED or run["lifecycle"] != "open"
        now = run["updated"] if settled else time.time()
    rounds = [json.loads(row["context"]) for row in db.execute("SELECT context FROM recoveries WHERE run_id=? AND created>=?",
                                                                (run["id"], since))]
    cycles = sum(1 for context in rounds if not context.get("manual"))
    retries = sum(1 for row in db.execute("SELECT evidence FROM attempts WHERE run_id=? AND created>=?", (run["id"], since))
                  if (marks := json.loads(row["evidence"] or "{}")).get("transient_retry_of")
                  and not marks.get("never_started")) \
        + sum(1 for context in rounds if context.get("retry_of"))   # D57 §6.3: a diagnosis dispatched again, not a round
    value = {"since": since, "reset": bool(snapshot.get("budget_since")),
             "cycles": {"used": cycles, "limit": settings["max_cycles"]},
             "retries": {"used": retries, "limit": settings.get("max_retries", BUDGET["max_retries"][0])},
             "minutes": {"used": max(0, int((now - since - (snapshot.get("budget_waited") or 0)) // 60)),   # D43: waits
                         "limit": settings.get("budget_minutes", BUDGET["budget_minutes"][0])}}
    value["exhausted"] = next((key for key in ("cycles", "retries", "minutes")
                               if value[key]["limit"] and value[key]["used"] >= value[key]["limit"]), None)   # 0 is "off"
    return value


LABELS = {"cycles": ("纠错", "轮"), "retries": ("自动重试", "次")}


def _origin(value):
    return "自上次续作 / 请求诊断起" if value.get("reset") else "自会话开始"


def budget_line(value):
    """The budget as the console, MCP readers and the report show it."""
    parts = [f"{LABELS[key][0]} {value[key]['used']}/{value[key]['limit']} {LABELS[key][1]}" for key in ("cycles", "retries")]
    parts.append(f"{value['minutes']['used']}/{value['minutes']['limit']} 分钟")
    if value.get("exhausted"):
        index = ("cycles", "retries", "minutes").index(value["exhausted"])
        parts[index] += "（已用完）"
    return f"预算（{_origin(value)}）：" + " · ".join(parts)


def budget_note(value, key):
    """Why automatic recovery did not start (``key``: what refused it), for a blocked Session's attention."""
    limit = value[key]["limit"]
    what = {"cycles": f"自动纠错已达到 {limit} 轮上限",
            "retries": f"自动重试已达到 {limit} 次上限" if limit else "自动重试已关闭（max_retries 为 0）",
            "minutes": f"自动纠错时长已超过 {limit} 分钟"}[key]
    return f"{what}（{_origin(value)}）；续作会重新计算预算。"


def refused(db, run, kind):
    """What of the budget refuses an automatic ``retry`` or a diagnosis (``cycle``) — the time limits both — or None."""
    left = budget(db, run)
    return next((key for key in ("minutes", {"retry": "retries", "cycle": "cycles"}[kind])
                 if left[key]["used"] >= left[key]["limit"]), None)


def mark_refused(db, attempt_id, key):
    """Note on the failed Attempt what refused its automatic recovery: the attention says that, not whatever ran out."""
    row = db.execute("SELECT evidence FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    evidence = json.loads(row["evidence"] or "{}") if row else {}
    if row and evidence.get("budget_refused") != key:
        journal.change(db, "attempts", attempt_id, journal.ENGINE, "attempt.budget", quiet=("evidence",),
                       evidence=dumps({**evidence, "budget_refused": key}))


def summary(db, run):
    snapshot = json.loads(run["snapshot"])
    rows = [dict(row) for row in db.execute("SELECT * FROM recoveries WHERE run_id=? ORDER BY number", (run["id"],))]
    for row in rows:
        for key in ("context", "evidence", "decision"):
            row[key] = json.loads(row[key]) if row[key] else None
    return {"policy": policy(snapshot.get("recovery"), legacy=True), "used": len(rows), "budget": budget(db, run),
            "latest": rows[-1] if rows else None, "history": rows}


def schedule(db, database, run, task, attempt, *, manual=False, actor=None):
    settings = policy(json.loads(run["snapshot"]).get("recovery"), legacy=True)
    rows = list(db.execute("SELECT * FROM recoveries WHERE run_id=? ORDER BY number", (run["id"],)))
    if any(r["status"] in LIVE for r in rows):
        require(not manual, "协调者正在处理此 Session", 409, "busy")
        return None
    failure = json.loads(attempt["evidence"]).get("failure_type", "native_error")
    if not manual:
        # An environment that no longer serves the run gets no new diagnosis (§6.2): continuing moves it to waiting.
        # D45: the requirement's own tool or model is not available — the user decides, a diagnosis cannot.
        if (not settings["enabled"] or failure in NEEDS_INPUT or json.loads(attempt["evidence"]).get("requested") or
                any(r["failed_attempt_id"] == attempt["id"] for r in rows) or (run["worker_id"] and unserved(db, run))):
            return None
        key = refused(db, run, "cycle")   # last: the cheap checks above end most ticks of a blocked Session
        if key:
            mark_refused(db, attempt["id"], key)
            return None
    ordered = [dict(t) for t in db.execute("SELECT id,ordinal,spec FROM tasks WHERE run_id=? ORDER BY ordinal",
                                         (run["id"],))]
    context = {"run_id": run["id"], "input": run["input"], "workspace": run["workspace"],
               "source": json.loads(run["source"]),
               "failed_task_id": task["id"], "failed_attempt_id": attempt["id"],
               "error": attempt["error"], "failure_type": failure,
               "failed_result": attempt["result"], "evidence": json.loads(attempt["evidence"]),
               "tasks": [{**t, "spec": json.loads(t["spec"])} for t in ordered],
               # each earlier round as it ended: a decision the platform refused carries why (EC2 run-40a1…: three
               # identical refused decisions, the Coordinator never told)
               "previous_decisions": [{**json.loads(r["decision"]), "round": r["number"], "status": r["status"]}
                                      for r in rows if r["decision"]],
               # the targets a retry / rework / switch_tool may name: the failed task and those before it
               "allowed_targets": [{"id": t["id"], "name": json.loads(t["spec"]).get("name", t["id"]),
                                    "kind": json.loads(t["spec"]).get("kind")}
                                   for t in ordered if t["ordinal"] <= task["ordinal"]],
               "manual": manual}
    # D54: the files artifacts_locked refused, which a retry / rework of this task may add to its paths (paths_add).
    # Since D57 §4 the gate only warns: kept for Attempts it blocked before then.
    locked = next((g.get("files") for g in (context["evidence"].get("gates") or [])
                   if g.get("name") == "artifacts_locked" and g.get("status") == "fail" and g.get("files")), None)
    if locked:
        context["locked_files"] = locked
    worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone() if run["worker_id"] else None
    capabilities = (json.loads(worker["config"]).get("capabilities") or {}) if worker else {}
    # What the Coordinator may switch to, saved with the diagnosis: its recommendation is checked against the same (D38 §3.1).
    context["environment"] = {"tools": capabilities.get("tools") or {}, "models": capabilities.get("models") or {},
                              "limitations": capabilities.get("limitations") or [],
                              **({"commands": capabilities["commands"]} if capabilities.get("commands") else {}),
                              "switch_tools": settings.get("switch_tools", True)}
    # whether the Session's one replan is used, and whether a replan for this failure would not use it (D57 §6.1)
    used, exempt = replan_quota(json.loads(run["snapshot"]), failure)
    context["environment"].update(replanned=used, **({"replan_exempt": True} if exempt else {}))
    planned = json.loads(run["snapshot"]).get("planning") or {}
    if planned.get("applied"):   # what the tasks are judged against (D34 §6.2)
        context["requirement"] = {"intent": planned.get("intent", ""), "summary": planned.get("summary", "")}
    rid, aid, now = uid("recovery"), uid("attempt"), time.time()
    db.execute("INSERT INTO recoveries(id,run_id,number,failed_task_id,failed_attempt_id,attempt_id,status,context,created,updated) "
               "VALUES(?,?,?,?,?,?,'pending',?,?,?)",
               (rid, run["id"], len(rows)+1, task["id"], attempt["id"], aid, dumps(context), now, now))
    journal.change(db, "runs", run["id"], actor or journal.RECOVERY, "recovery.started",
                   event={"id": rid, "number": len(rows)+1, "task_id": task["id"], "failed_attempt_id": attempt["id"],
                          "manual": manual}, status="diagnosing", desired="active", updated=now)
    return rid


def automatic(database, rid, aid):
    with database.transaction() as db:
        run = dict(db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())
        if run["desired"] != "active" or run["lifecycle"] != "open":
            return
        task = db.execute("SELECT * FROM tasks WHERE run_id=? AND current_attempt=?", (rid, aid)).fetchone()
        if not task or task["status"] != "blocked":
            return
        attempt = db.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
        schedule(db, database, run, task, attempt)


def reconcile(database, skip=frozenset()):
    """Recover a crash between persisting task failure and scheduling diagnosis.

    ``skip``: Sessions currently being advanced; their own advance schedules diagnosis.
    """
    with database.transaction() as db:
        rows = list(db.execute(
            "SELECT DISTINCT r.id FROM runs r JOIN tasks t ON t.run_id=r.id "
            "WHERE r.desired='active' AND r.lifecycle='open' AND r.status IN ('running','ready','blocked') "
            "AND t.status='blocked' AND t.current_attempt IS NOT NULL"))
        for row in rows:
            if row["id"] in skip:
                continue
            run = dict(db.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())
            task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1",
                              (run["id"],)).fetchone()
            if not task or task["status"] != "blocked":
                continue
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone()
            if run["status"] != "blocked":
                journal.change(db, "runs", run["id"], journal.RECOVERY, "run.state", "核对：任务已失败，安排诊断",
                               event={"status": "blocked", "error": attempt["error"]},
                               status="blocked", error=attempt["error"], updated=time.time())
            schedule(db, database, run, task, attempt)


def feedback(db, run, attempt):
    """What a task run after a recovery round is told (D46): the task the Coordinator sent back gets its decision and a
    summary of the failure to repair; a later review or test re-checks those findings; any other task gets nothing."""
    row = db.execute("SELECT * FROM recoveries WHERE run_id=? AND status='applied' AND updated<=? "
                     "ORDER BY number DESC LIMIT 1", (run["id"], attempt["created"])).fetchone()
    decision = json.loads(row["decision"] or "{}") if row else {}
    if not row or decision.get("action") == "replan":
        return None   # a replan's instructions were for the planner; the new tasks are first runs (D41)
    context = json.loads(row["context"])
    outcome = (context.get("evidence") or {}).get("outcome") or {}
    findings = [f if isinstance(f, str) else json.dumps(f, ensure_ascii=False) for f in outcome.get("findings") or []]
    failure = {"task": context.get("failed_task_id"), "error": str(context.get("error") or "")[:1500],
               "failure_type": context.get("failure_type"), "findings": [f[:300] for f in findings[:10]],
               "summary": str(outcome.get("summary") or "")[:800]}
    target = decision.get("target_task_id") or context.get("failed_task_id")
    spec = json.loads(attempt["spec"])
    checking = is_review(spec) or spec.get("kind") == "test"
    if attempt["task_id"] == target:   # what it is told to do follows what kind of work it is (D46)
        instruction = ("本轮为纠错后的重新审查：只读审查本轮源码，逐条核对上一轮的问题与协调者的要求，不修改代码。" if is_review(spec) else
                       "本轮为纠错后的重新分析：按协调者的要求补充或修正结论，不修改业务代码。" if spec.get("kind") == "design" else
                       "本轮为纠错后的重新执行；实施修复并重新验证，后续审查必须针对本轮源码。")
        return {"recovery_id": row["id"], "cycle": row["number"],
                "decision": {k: decision.get(k) for k in ("action", "target_task_id", "reason", "instructions") if decision.get(k)},
                "failure": failure, "instruction": instruction}
    if checking:
        return {"recovery_id": row["id"], "cycle": row["number"], "recheck": True, "failure": failure,
                "instruction": "本轮为返工后的复验：逐条核对上一轮的失败与发现是否已解决，结论只针对本轮源码。"}
    if attempt["number"] > 1:   # reset by the rework upstream: it runs again on the changed source, knowing why
        return {"recovery_id": row["id"], "cycle": row["number"], "rerun": True, "failure": failure,
                "instruction": "上游任务已按纠错返工，本任务在新的源码上重新执行：确认上一轮的问题已随返工解决，必要时一并修正并重新验证。"}
    return None


class Coordinator:
    def __init__(self, engine):
        self.engine, self.db = engine, engine.db

    def advance(self, run):
        with self.db.read() as db:
            row = db.execute("SELECT * FROM recoveries WHERE run_id=? ORDER BY number DESC LIMIT 1",
                             (run["id"],)).fetchone()
        require(row is not None, "缺少协调者记录", 409, "recovery_missing")
        item = dict(row)
        if item["status"] not in LIVE:
            return
        context = json.loads(item["context"])
        config = json.loads(run["worker_config"])
        # D57 §3.3: what the diagnosis stores is masked, the Worker's token too (opened here, outside any transaction)
        extra = (worker_token(config),)
        mask = lambda value: sources.mask_value(value, extra)[0]
        body = {"run_id": run["id"], "attempt_id": item["attempt_id"], "input": context["input"],
                "spec": {"id": "diagnose", "name": "协调者诊断与纠错", "tool": "kiro", "model": "auto", "effort": "",
                         "prompt": "分析失败证据，提出可执行的重试、上游返工或等待输入决定。"},
                "upstream": [], "coordination": context}
        if item["status"] in {"pending", "dispatching", "dispatch_unknown"}:
            try:
                result = self.engine.request(config, "/dispatch", body)
            except Error as exc:
                if exc.status in {400, 401, 403, 404, 409, 413, 422}:
                    self.finish(run, item, error="协调者启动请求被拒绝：" + str(exc))
                    return
                raise
            state, error = result["status"], mask(result.get("error"))
            with self.db.transaction() as db:
                journal.change(db, "recoveries", item["id"], journal.WORKER, "recovery.state", event={"id": item["id"]},
                               mask=extra, status=state, native_id=result.get("native_id"),
                               context_hash=result.get("context_hash"), error=error, updated=time.time())
            if state == "blocked":
                self.finish(run, item, error="协调者未能启动：" + (error or "请检查原生环境"))
            return
        result = self.engine.request(config, "/inspect", {"run_id": run["id"], "attempt_id": item["attempt_id"]})
        native, evidence = result.get("native") or {}, result.get("evidence") or {}
        # stored masked: copies, the decision below is read as the Coordinator wrote it
        if not native.get("done"):
            state = "waiting_approval" if native.get("awaiting_approval") else "running"
            with self.db.transaction() as db:
                journal.change(db, "recoveries", item["id"], journal.WORKER, "recovery.state", event={"id": item["id"]},
                               mask=extra, status=state, evidence=dumps(mask(evidence)), updated=time.time())
            return
        # D57 §6.3: the platform's fault, not the Coordinator's — its runtime, its sandbox, KiroCrew's teardown
        kind = fault(native, self._entered(run, item) if native.get("stopped") else True)
        with self.db.transaction() as db:
            journal.change(db, "recoveries", item["id"], journal.WORKER, "recovery.state", event={"id": item["id"]},
                           mask=extra, result=mask(native.get("result")),
                           evidence=dumps(mask({**evidence, **({"platform_fault": kind} if kind else {})})),
                           updated=time.time())
        if kind and self._retry(run, item, context, kind):
            return
        try:
            require(not native.get("error") and not native.get("stopped"), "协调者执行失败或已停止")
            require(result.get("handoff") and evidence.get("actual_backend") == "kiro", "协调者交接或原生后端未核实")
            raw = result.get("decision", "")
            require(isinstance(raw, str) and len(raw) <= 64000, "协调者决定缺失或过大")
            decision = json.loads(raw)
            fields = {"action", "target_task_id", "reason", "instructions", "tool", "model", "effort", "paths_add", "missing"}
            require(isinstance(decision, dict) and set(decision) <= fields, "协调者决定字段无效")
            require(decision.get("action") in {"retry", "rework", "switch_tool", "replan", "wait_for_user"}, "协调者动作无效")
            require(all(isinstance(decision.get(k), str) and 0 < len(decision[k]) <= 8000
                        for k in ("reason", "instructions")), "协调者必须给出原因与具体操作")
            if decision["action"] == "switch_tool":
                require(all(isinstance(decision.get(k, ""), str) for k in ("tool", "model", "effort")), "换工具的字段须为字符串")
            else:
                decision = {k: v for k, v in decision.items() if k not in {"tool", "model", "effort"}}
            if decision["action"] != "wait_for_user" or decision.get("missing") not in WAIT_FOR:
                decision.pop("missing", None)
            self.finish(run, item, decision=decision)
        except (Error, ValueError, TypeError) as exc:
            why = "自动诊断未形成可执行决定：" + str(exc)
            if native.get("stopped"):   # a person stopped the diagnosis: theirs to decide
                self.finish(run, item, error=why)
            else:   # D71: the failed task is retried as it failed
                self.finish(run, item, decision=fallback(None, item["failed_task_id"], why))

    def _entered(self, run, item):
        """Whether a person may have stopped this diagnosis in the native page (crew_entry.entered)."""
        with self.db.read() as db:
            return crew_entry.entered(db, run["worker_id"], item["created"])

    def _retry(self, run, item, context, kind):
        """D57 §6.3: a diagnosis the platform failed is dispatched again once, on the same round with a new Attempt ID — an
        automatic retry of the budget, not another round. False when it is not: it was the retry, the budget refuses (said
        on the round), or the Session changed (finish says so)."""
        if context.get("retry_of"):
            return False
        now, aid = time.time(), uid("attempt")
        with self.db.transaction() as db:
            fresh = dict(db.execute("SELECT * FROM runs WHERE id=?", (run["id"],)).fetchone())
            failed = db.execute("SELECT current_attempt FROM tasks WHERE run_id=? AND id=?",
                                (run["id"], item["failed_task_id"])).fetchone()
            if (fresh["desired"] != "active" or fresh["lifecycle"] != "open" or fresh["status"] != "diagnosing" or
                    not failed or failed["current_attempt"] != item["failed_attempt_id"]):
                return False
            over = refused(db, fresh, "retry")
            if over:
                row = db.execute("SELECT evidence FROM recoveries WHERE id=?", (item["id"],)).fetchone()
                journal.change(db, "recoveries", item["id"], journal.RECOVERY, "recovery.state", quiet=("evidence",),
                               evidence=dumps({**json.loads(row["evidence"] or "{}"), "budget_refused": over}))
                return False
            journal.change(db, "recoveries", item["id"], journal.RECOVERY, "recovery.retry", f"{WHY[kind]}，自动重新诊断一次",
                           event={"id": item["id"], "number": item["number"], "attempt": item["attempt_id"], "retry": aid,
                                  "transient": kind},
                           attempt_id=aid, status="pending", native_id=None, context_hash=None, result=None, evidence="{}",
                           error=None, context=dumps({**context, "retry_of": item["attempt_id"],
                                                      "retry": {"transient": kind, "at": now}}), updated=now)
        return True

    def finish(self, run, item, *, decision=None, error=None):
        now = time.time()
        extra = (worker_token(json.loads(run.get("worker_config") or "{}")),)   # D57 §3.3: what is stored is masked
        with self.db.transaction() as db:
            fresh = db.execute("SELECT * FROM runs WHERE id=?", (run["id"],)).fetchone()
            failed = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?",
                                (run["id"], item["failed_task_id"])).fetchone()
            if (fresh["desired"] != "active" or fresh["lifecycle"] != "open" or fresh["status"] != "diagnosing" or
                    not failed or failed["current_attempt"] != item["failed_attempt_id"]):
                journal.change(db, "recoveries", item["id"], journal.RECOVERY, "recovery.state", "会话已变化，诊断结果不再适用",
                               event={"id": item["id"]}, status="cancelled", updated=now)
                return
            if decision and decision.get("paths_add") is not None and decision["action"] not in {"retry", "rework"}:
                error = "协调者扩大可改动路径未通过核对：paths_add 只用于重试或返工失败的任务"
                decision = {**decision, "refused": error}
            elif decision and decision["action"] == "replan":
                refusal = self._replan(db, fresh, failed, item, decision, now, extra)
                if not refusal:
                    return
                decision = fallback({**decision, "refused": refusal}, failed["id"], refusal)   # D71: keep going
            if decision and not error and decision["action"] == "wait_for_user" and decision.get("missing") not in WAIT_FOR:
                decision = fallback(decision, failed["id"], "协调者没有写明缺少凭证或需求信息")   # D71
            target, widened = None, None
            if decision and not error and decision["action"] != "wait_for_user":
                target = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?",
                                    (run["id"], decision.get("target_task_id"))).fetchone()
                if decision["action"] == "retry" and target and target["ordinal"] < failed["ordinal"]:
                    # "retry" of an upstream Task has exactly rework's effect (target and downstream rerun with
                    # the repair instructions); normalize the label instead of blocking a correct diagnosis.
                    decision = {**decision, "action": "rework", "normalized_from": "retry"}
                if decision["action"] == "switch_tool":
                    try:
                        switched, before = switch(db, fresh, failed, decision, json.loads(item["context"]))
                        decision = {**decision, "switched_from": before}
                    except Error as exc:
                        error = "协调者换工具的推荐未通过核对：" + str(exc)
                if not error and (not target or target["ordinal"] > failed["ordinal"] or
                                  decision["action"] == "retry" and target["id"] != failed["id"]):
                    allowed = "、".join(row["id"] for row in db.execute(
                        "SELECT id FROM tasks WHERE run_id=? AND ordinal<=? ORDER BY ordinal", (run["id"], failed["ordinal"])))
                    named = decision.get("target_task_id")
                    where = ("不存在" if not target else f"排在失败任务 {failed['id']} 之后" if target["ordinal"] > failed["ordinal"]
                             else f"不是失败任务 {failed['id']}（retry 只能重试失败的任务）")
                    error = (f"协调者{decision['action']}的目标 {named} {where}：目标只能是 {allowed} 之一。"
                             "问题出在上游产出的代码时用 rework 指向该上游任务，其后的任务会自动重跑。")
                    decision = {**decision, "refused": error}   # the next diagnosis sees why (previous_decisions)
                if not error and decision.get("paths_add") is not None:
                    try:
                        widened = widen(decision, json.loads(item["context"]), failed, target)
                    except Error as exc:
                        error = "协调者扩大可改动路径未通过核对：" + str(exc)
                        decision = {**decision, "refused": error}
            if error and decision:
                # D71: a refused decision is not a stop — the failed task is retried with what the Coordinator said
                decision, error = fallback({**decision, "refused": error}, failed["id"], error), None
                target, widened = failed, None
            stored = sources.mask_value(decision, extra)[0] if decision else None   # decided on as written, stored masked
            if error or decision["action"] == "wait_for_user":
                message = sources.mask_text(error or (decision["reason"] + "；" + decision["instructions"]), extra)[0]
                journal.change(db, "recoveries", item["id"], journal.RECOVERY, "recovery.state", event={"id": item["id"]},
                               mask=extra, status="blocked", decision=dumps(stored) if stored else None, error=message,
                               updated=now)
                journal.change(db, "runs", run["id"], journal.RECOVERY, "recovery.blocked",
                               event={"id": item["id"], "error": message}, mask=extra, status="blocked", error=message,
                               updated=now)
                return
            invalidated = [row["id"] for row in db.execute(
                "SELECT id FROM tasks WHERE run_id=? AND ordinal>=? ORDER BY ordinal", (run["id"], target["ordinal"]))]
            journal.change_rows(db, "tasks", "run_id=? AND ordinal>=?", (run["id"], target["ordinal"]), journal.RECOVERY,
                                "task.reset", stored.get("reason"), mask=extra, status="pending", current_attempt=None)
            if widened:   # D54: the revision below records the wider paths
                spec = json.loads(db.execute("SELECT spec FROM tasks WHERE run_id=? AND id=?", (run["id"], target["id"])).fetchone()[0])
                journal.change(db, "tasks", (run["id"], target["id"]), journal.RECOVERY, "task.paths_added", stored.get("reason"),
                               event={"task_id": target["id"], "added": decision["paths_add"], "recovery": item["number"]},
                               mask=extra, spec=dumps({**spec, "paths": widened}))
            if decision["action"] == "switch_tool":
                journal.change(db, "tasks", (run["id"], target["id"]), journal.RECOVERY, "task.tool_switched", stored["reason"],
                               event={"task_id": target["id"], "from": decision["switched_from"],
                                      "to": {k: switched[k] for k in ("tool", "model", "effort")}},
                               mask=extra, spec=dumps(switched))
            version = fresh["version"] + 1
            journal.change(db, "recoveries", item["id"], journal.RECOVERY, "recovery.state", event={"id": item["id"]},
                           mask=extra, status="applied", decision=dumps(stored), error=None, updated=now)
            journal.change(db, "runs", run["id"], journal.RECOVERY, "recovery.applied", stored.get("reason"),
                           event={"id": item["id"], "number": item["number"], "target_task_id": target["id"],
                                  "invalidated": invalidated}, mask=extra, status="ready", error=None, version=version,
                           updated=now)
            from .revisions import record
            name = json.loads(target["spec"]).get("name", target["id"])
            reason = stored.get("reason")
            if decision["action"] == "switch_tool":
                was = decision["switched_from"]
                title = (f"自动纠错：「{name}」由 {was['tool']} / {was['model']} 换为 {switched['tool']} / {switched['model']}"
                         + (f"（{reason}）" if reason else ""))
            else:
                title = f"自动纠错：退回「{name}」" + (f"（{reason}）" if reason else "")
            if widened:
                title += f"；扩大「{name}」的可改动路径：{'、'.join(decision['paths_add'])}"
            record(db, run["id"], version, "recovered", title, fresh["owner_id"], now, recovery_id=item["id"],
                   decision=stored, invalidated=invalidated)

    def _replan(self, db, fresh, failed, item, decision, now, extra=()):
        """The Coordinator's replan (D41 §3.2): the failed task and those after it are replaced by a KiroCrew planning task;
        accepted tasks stay. Once per Session — besides up to two for a problem in the task's contract (D57 §6.1); never for
        a design Session or for a planning task itself."""
        from . import planning
        from .revisions import record
        rid, snapshot = fresh["id"], json.loads(fresh["snapshot"])
        # D57 §6.1: a replan for a problem in the failed task's contract, as its Attempt's evidence shows, uses no quota
        row = db.execute("SELECT evidence FROM attempts WHERE id=?", (item["failed_attempt_id"],)).fetchone()
        used, exempt = replan_quota(snapshot, json.loads((row["evidence"] if row else None) or "{}").get("failure_type"))
        refusal = ("已重新规划过一次，不能再次重新规划" if used and not exempt else
                   "工作流设计会话不重新规划" if snapshot.get("purpose") == "workflow_design" else
                   "失败的是规划任务本身，不能以重新规划替换" if planning.planning_task(failed["id"], snapshot) else None)
        if refusal:   # D71: finish retries the failed task instead (said in the decision's refused)
            return "协调者重新规划的决定未通过核对：" + refusal
        context = json.loads(item["context"])
        environment = context.get("environment") or {}
        later = [dict(row) for row in db.execute("SELECT * FROM tasks WHERE run_id=? AND ordinal>=? ORDER BY ordinal",
                                                 (rid, failed["ordinal"]))]
        # D57 §13.1: the checks of these tasks that must pass, for the new tasks to carry over as they are (the platform
        # runs none of a review's or a design's)
        replaced = [{"id": row["id"], "name": spec.get("name", row["id"]), "kind": spec.get("kind"),
                     **({"required": required} if runs_checks(spec) and (required := required_checks(fresh, spec)) else {})}
                    for row in later for spec in [json.loads(row["spec"])]]
        failure = f"任务「{replaced[0]['name']}」：{str(context.get('error') or '')[:1500]}"
        used = {row[0] for row in db.execute("SELECT id FROM tasks WHERE run_id=? UNION SELECT task_id FROM attempts WHERE run_id=?",
                                             (rid, rid))}
        task_id = next(planning.REPLAN_PREFIX + str(n) for n in range(1, 100) if planning.REPLAN_PREFIX + str(n) not in used)
        kept = [{"id": row["id"], "name": spec.get("name", row["id"]), "verify": spec["verify"],
                 **({"required": required} if (required := required_checks(fresh, spec)) else {})} for row in db.execute(
            "SELECT id, spec FROM tasks WHERE run_id=? AND status='accepted' AND ordinal<? ORDER BY ordinal", (rid, failed["ordinal"]))
            if (spec := json.loads(row["spec"])).get("verify")]   # D54 §9 a: their checks are reviewed with the plan
        # D57 §3.3: decided on as written (finish), the Coordinator's words go on masked — to the planner as well
        stored = sources.mask_value(decision, extra)[0]
        spec = planning.replan_step(task_id, replaced, failure, stored, environment.get("models"), environment.get("tools"),
                                    environment.get("limitations"), environment.get("commands"), kept, snapshot.get("preferences"))
        journal.remove_rows(db, "tasks", "run_id=? AND ordinal>=?", (rid, failed["ordinal"]), journal.RECOVERY, "task.replaced",
                            stored.get("reason"), event={"tasks": replaced}, mask=extra)
        db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)", (rid, spec["id"], failed["ordinal"], dumps(spec)))
        if snapshot.get("replan"):   # the earlier replans stay known: their tasks, and the ids they replaced
            snapshot["replan_history"] = [*(snapshot.get("replan_history") or []), snapshot["replan"]]
        snapshot["replan"] = {"task": spec["id"], "applied": False, "replaced": replaced, "reason": stored.get("reason"),
                              "instructions": stored.get("instructions"), "recovery": item["number"],
                              **({"exempt": True} if exempt else {})}
        snapshot["steps"] = snapshot.get("steps", []) + [spec]
        version = fresh["version"] + 1
        journal.change(db, "recoveries", item["id"], journal.RECOVERY, "recovery.state", event={"id": item["id"]},
                       mask=extra, status="applied", decision=dumps(stored), error=None, updated=now)
        journal.change(db, "runs", rid, journal.RECOVERY, "recovery.applied", stored.get("reason"),
                       event={"id": item["id"], "number": item["number"], "replan": True,
                              "invalidated": [r["id"] for r in replaced]},
                       mask=extra, status="ready", error=None, version=version, snapshot=dumps(snapshot), updated=now)
        names = "、".join(r["name"] for r in replaced)
        reason = stored.get("reason")
        record(db, rid, version, "recovered", f"自动纠错：重新规划，替换「{names}」" + (f"（{reason}）" if reason else ""),
               fresh["owner_id"], now, recovery_id=item["id"], decision=stored, invalidated=[r["id"] for r in replaced])
