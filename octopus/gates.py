"""Named gates (D37 §4): each Attempt's acceptance as a list of recorded verdicts. The first five are the checks the
Engine always made; their messages and failure types are unchanged, except that a missing handoff and an invalid outcome
are warnings (D57 §4)."""
import json
import re
import time

EXPIRED = re.compile(r"session (?:has )?expired|not logged in", re.I)
BLOCKED = "工作者未完成任务或报告 BLOCKED"
EFFORT = re.compile(r"\[(low|medium|high|xhigh|max)\]$")
OLD_WORKER = "Worker 版本不支持"
# Check commands that install dependencies: run on the baseline they would change the dependency folders to the base's
# manifest, and the restored candidate would be left without its own (D37 §11.1).
INSTALLS = re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|ci|add)\b|\bpip3?\s+install\b|\buv\s+(?:sync|pip\s+install|add)\b"
                      r"|\bpoetry\s+(?:install|add)\b|\bbundle\s+install\b|\bcomposer\s+install\b|\bgo\s+mod\s+download\b"
                      r"|\bcargo\s+fetch\b|\bmvn\b.*\binstall\b")


# A warn (D57 §4) is recorded and shown like any verdict but passes: it neither blocks the task nor starts recovery.
STATUSES = ("pass", "warn", "fail", "skip")
# D57 §4: what an invalid outcome (or the reply) still says of the verdict. A malformed file never turns a rejection into
# a pass; a mention in prose ("按 changes_requested 的意见修复", "不再 blocked") is not a verdict.
REJECTIONS = ("changes_requested", "blocked")
# Before D57 any decision but pass failed: fail / rejected count as well, but only where a verdict is written — a top-level
# JSON value or the value of a decision-like key (a heading included) — never as a word on its own.
VERDICTS = REJECTIONS + ("fail", "rejected")
# Task 15 (D57 §8): a stated verdict as a gate's detail shows it; the decision itself stays the token.
SAID = {"changes_requested": "要求修改", "blocked": "受阻", "fail": "不通过", "rejected": "拒绝"}
_DECO = r"""[\s*_`"'“”‘’]*"""   # Markdown and quotes around the key, the separator and the value
_KEY = r"(?:\bdecision\b|\bverdict\b|结论|决定)"
_VALUE = r"(changes[ \t_-]*requested|blocked|fail|rejected)\b"
STATED = re.compile(rf"(?i){_KEY}{_DECO}[:：=]{_DECO}{_VALUE}")   # **Decision:** changes_requested, 结论：`blocked`
HEADED = re.compile(rf"""(?im)^[ \t]*#{{1,6}}[ \t]*[*_`"']*{_KEY}[ \t*_`"':：]*\n(?:[ \t]*\n)*[ \t>*_`"'-]*{_VALUE}""")   # ## 结论 …
ALONE = re.compile(r"""(?im)^[ \t>#*_`"'-]*(changes[ \t_-]*requested|blocked)[ \t*_`"'.。!！]*$""")   # alone on a line


def verdict(value):
    """A decision as written, compared as a token: lower case, runs of spaces, - and _ as one _."""
    return re.sub(r"[\s_-]+", "_", value.strip().lower()) if isinstance(value, str) else ""


def record(name, status, detail=None):
    if status not in STATUSES:
        raise ValueError(f"未知的门禁状态：{status}")
    return {"name": name, "status": status, **({"detail": detail} if detail else {}), "at": time.time()}


def warnings(records):
    """The records an accepted Attempt carries as its warnings (D57 §4)."""
    return [r for r in records or [] if r.get("status") == "warn"]


# D57 §5.4: the words a check the environment could not run is reported in, required or not
UNRUNNABLE = "无法执行（环境）：{}。请确认环境，或把这条检查改写成可执行的形式。"


def checks_verdict(commands, status, required):
    """checks_pass of a finished check job (D57 §5): (status, detail), one record for the Attempt. Each check is judged by
    its text, wherever it stands: one the environment could not run (``checks.unrunnable``) is a warning, required or
    not; a required one that failed otherwise fails, the first of them named as before; an advisory one that failed is a
    warning. A required check that never ran (the job stopped or was lost) fails too."""
    from .checks import unrunnable
    results = status.get("results") or []
    if status.get("passed"):
        return "pass", f"{len(results)} 条命令"
    required, ran = set(required), {result.get("command") for result in results}
    failure, notes = None, []
    for result in results:
        command, code = str(result.get("command") or ""), result.get("exit")
        if code == 0:
            continue
        reason = unrunnable(result)
        if reason:
            notes.append(UNRUNNABLE.format(reason))
        elif command in required:
            failure = failure or (f"检查命令超时：{command}" if result.get("timeout") else
                                  "检查命令已停止" if result.get("stopped") else
                                  f"检查命令未运行（检查总时限已到）：{command}" if result.get("skipped") else
                                  f"检查命令失败：{command}（退出码 {code}）")
        else:
            how = (f"退出码 {code}" if code is not None else "超时" if result.get("timeout") else "已停止" if result.get("stopped")
                   else "未运行：检查总时限已到" if result.get("skipped") else "无法运行")
            notes.append(f"建议检查未通过：{command[:80]}（{how}）")
    lost = "检查命令中断（Worker 重启）" if status.get("lost") else "检查命令已停止"
    for command in (c for c in commands if c not in ran):   # never ran: the job ended before them
        if command in required:
            failure = failure or lost
        else:
            notes.append(f"建议检查未通过：{command[:80]}（未运行：{lost}）")
    if failure or (not notes and required):
        return "fail", failure or lost
    return "warn", "；".join(notes) or "建议检查没有全部通过"


def stated(outcome, text):
    """(decision, summary, failure_type) of a rejection an invalid outcome still states — in the file, then in the reply
    — or None when neither states one. A file that parses as JSON states one when its decision, or any other top-level
    string, is exactly a verdict token. The summary and failure_type are the file's when the file states it and they can
    be read; otherwise none and implementation."""
    from .recovery import FAILURES
    outcome = outcome if isinstance(outcome, str) else ""
    try:
        value = json.loads(outcome) if outcome.strip() else None
    except ValueError:
        value = None
    file = value if isinstance(value, dict) else {}
    named = [file.get("decision"), *file.values()] if file else [value]   # the decision key first
    found = next((verdict(v) for v in named if verdict(v) in VERDICTS), None)
    if not found:
        for index, source in enumerate((outcome, text or "")):
            match = STATED.search(source) or HEADED.search(source) or ALONE.search(source)
            if match and index:   # only the reply says so: nothing of the file is taken
                return verdict(match.group(1)), None, "implementation"
            if match:
                found = verdict(match.group(1))
                break
    if not found:
        return None
    summary = file.get("summary").strip()[:8000] if isinstance(file.get("summary"), str) else ""
    failure = file.get("failure_type") if file.get("failure_type") in FAILURES - {"none"} else "implementation"
    return found, summary or None, failure


def basic(native, result, spec, evidence, report, invalid, lost, missing, blocked_line):
    records = []
    error = native.get("error") or ("原生任务已停止" if native.get("stopped") else None)
    failure = "native_stopped" if native.get("stopped") else "native_error"
    if EXPIRED.search(native.get("error") or ""):
        failure = "auth"
    if not error and (missing or blocked_line) and not lost:
        error = BLOCKED
        if report is not None and report["decision"] != "pass":   # as today: the outcome explains a BLOCKED reply
            error, failure = report["summary"], report["failure_type"]
    records.append(record("native_completed", "fail" if error else "pass", error))
    if error:
        return error, failure, records
    # D57 §4: a missing handoff is the platform's to write (the Engine does, from the native result); judging goes on
    handoff = record("handoff_present", "pass") if result.get("handoff", "").strip() else \
        record("handoff_present", "warn", "执行者没有写交接文件，平台已用原生结果生成一份")
    actual = EFFORT.sub("", evidence.get("actual_model") or "")
    # A backend, model or effort that is not the one requested is the tool's failure: recovery may switch (D38 §3.2).
    # One the evidence does not show (no routing record) is noted and the task goes on (owner 2026-10-22, gates
    # retrospective: it used to wait for the user, and switching cannot fix it either).
    unverified = []
    for wrong, seen, message in ((evidence.get("actual_backend") != spec["tool"], bool(evidence.get("actual_backend")),
                                  "实际 ACP 后端未核实或与配置不匹配"),
                                 (spec["model"] not in {"auto", "default"} and actual != spec["model"], bool(actual),
                                  "实际模型未核实或与本次配置不匹配"),
                                 (bool(spec["effort"] and evidence.get("actual_effort") and evidence["actual_effort"] != spec["effort"]),
                                  True, "实际 Effort 与本次配置不匹配")):
        if wrong and seen:
            records += [handoff, record("identity_match", "fail", message)]
            return message, "tool", records
        if wrong:
            unverified.append(message.replace("未核实或与本次配置不匹配", "未核实").replace("未核实或与配置不匹配", "未核实"))
    records += [record("identity_match", "warn", "；".join(unverified) + "（执行环境没有报告），照常执行") if unverified
                else record("identity_match", "pass"), handoff]
    if report is None and invalid is None:
        records += [record("outcome_valid", "skip"), record("outcome_pass", "skip")]
        return None, None, records
    if invalid:   # D57 §4: judged by what the file and the reply state (a standalone BLOCKED line failed above)
        rejection = stated(result.get("outcome"), str(native.get("result") or ""))
        if rejection:   # as outcome_pass judges a readable one: the agent's rejection stands
            decision, summary, failure = rejection
            said = SAID.get(decision, decision)
            message = summary or f"结果写明了「{said}」，但结果文件不合法：{invalid}"
            records += [record("outcome_valid", "warn", f"{invalid}；结果写明了「{said}」，按该结论判断"),
                        record("outcome_pass", "fail", message)]
            return message, failure, records
        records += [record("outcome_valid", "warn", f"{invalid}；已按结果文字判断：没有写明「要求修改」或「受阻」，按通过"),
                    record("outcome_pass", "skip", "结果文件不合法，没有可读的结论")]
        return None, None, records
    records.append(record("outcome_valid", "pass"))
    if report["decision"] != "pass":
        records.append(record("outcome_pass", "fail", report["summary"]))
        return report["summary"], report["failure_type"], records
    records.append(record("outcome_pass", "pass"))
    return None, None, records


def covered(path, globs):
    """Whether a task's ``paths`` cover ``path`` (D37 §11.2): a directory (trailing / or /**), the whole project (".", "**"),
    a glob — ``**/`` is zero or more directories, ``*`` may cross directories — or the path itself and everything under it."""
    path = path.lstrip("/")
    path = path[2:] if path.startswith("./") else path
    for raw in globs or []:
        raw = str(raw).strip()
        if raw in {".", "./", "/", "*", "**", "**/*"}:   # the whole project
            return True
        item = raw.lstrip("/")
        item = item[2:] if item.startswith("./") else item
        if not item:
            continue
        if item.endswith("/**"):
            item = item[:-2]
        if item.endswith("/"):
            if path.startswith(item):
                return True
            continue
        if path == item or path.startswith(item + "/") or _glob(item).fullmatch(path):
            return True
    return False


def _glob(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append(".*")
            i += 1
        elif pattern[i] == "?":
            out.append(".")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))
