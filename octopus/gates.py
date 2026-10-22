"""Named gates (D37 §4): each Attempt's acceptance as a list of recorded verdicts. The first five are the checks the
Engine always made; their messages and failure types are unchanged."""
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


def record(name, status, detail=None):
    if status not in STATUSES:
        raise ValueError(f"未知的门禁状态：{status}")
    return {"name": name, "status": status, **({"detail": detail} if detail else {}), "at": time.time()}


def warnings(records):
    """The records an accepted Attempt carries as its warnings (D57 §4)."""
    return [r for r in records or [] if r.get("status") == "warn"]


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
    if not result.get("handoff", "").strip():
        records.append(record("identity_match", "skip"))   # checked in today's order: handoff first
        records.append(record("handoff_present", "fail", "缺少本次交接文件"))
        return "缺少本次交接文件", "contract", records
    actual = EFFORT.sub("", evidence.get("actual_model") or "")
    # A backend, model or effort that is not the one requested is the tool's failure: recovery may switch (D38 §3.2).
    # One the evidence does not show (no routing record) is not: switching cannot fix it, it waits for the user as before.
    for wrong, seen, message in ((evidence.get("actual_backend") != spec["tool"], bool(evidence.get("actual_backend")),
                                  "实际 ACP 后端未核实或与配置不匹配"),
                                 (spec["model"] not in {"auto", "default"} and actual != spec["model"], bool(actual),
                                  "实际模型未核实或与本次配置不匹配"),
                                 (bool(spec["effort"] and evidence.get("actual_effort") and evidence["actual_effort"] != spec["effort"]),
                                  True, "实际 Effort 与本次配置不匹配")):
        if wrong:
            records += [record("handoff_present", "pass"), record("identity_match", "fail", message)]
            return message, "tool" if seen else "model", records
    records += [record("identity_match", "pass"), record("handoff_present", "pass")]
    if report is None and invalid is None:
        records += [record("outcome_valid", "skip"), record("outcome_pass", "skip")]
        return None, None, records
    if invalid:
        records.append(record("outcome_valid", "fail", invalid))
        return invalid, "contract", records
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
