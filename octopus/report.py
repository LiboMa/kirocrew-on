"""A Session's Feature report (D37 phase 2): the request and plan, each task's final Attempt — tools, conclusion, gates,
candidate, checks, acceptance, review binding — the recovery rounds and the published pull / merge request, as Markdown
or as one self-contained HTML page. Every text from the Session is escaped (it may come from an Agent), only an https
pull-request URL becomes a link, and long texts are clipped so a large Session stays a readable report."""
import html as markup
import re
import time

from . import roles

MARK = {"pass": "✓", "fail": "✗", "skip": "–"}
SUMMARY, DETAIL, INPUT, PLAN = 2000, 300, 12000, 8000   # the request and the plan keep their own limits (domain, planning)
SPECIAL = str.maketrans({c: "\\" + c for c in "\\`*_[]<>!#|~"})
STYLE = """
body{font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f2328;max-width:62rem;margin:2rem auto;padding:0 1.25rem}
h1{font-size:1.6rem;margin-bottom:.25rem}h2{margin-top:2rem;border-bottom:1px solid #d0d7de;padding-bottom:.3rem}
h3{margin-top:1.5rem}table{border-collapse:collapse;width:100%;margin:.5rem 0}
th,td{border:1px solid #d0d7de;padding:.3rem .5rem;text-align:left;vertical-align:top}th{background:#f6f8fa}
.meta{color:#59636e}.fail{color:#cf222e}.skip{color:#59636e}code{background:#f6f8fa;padding:0 .25rem;border-radius:4px}
pre{white-space:pre-wrap;background:#f6f8fa;padding:.75rem;border-radius:6px}
"""


def _clip(text, limit):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def _line(text, limit=DETAIL):
    lines = str(text or "").splitlines()
    return _clip(lines[0] if lines else "", limit)


def _md(text):
    """One line of Session text in Markdown: CommonMark punctuation escaped, so it cannot become a heading, link,
    image, HTML or table cell break."""
    return str(text or "").translate(SPECIAL)


def _block(text):
    """Multi-line Session text in Markdown: a fenced block longer than any backtick run inside, so neither its content
    nor a clip in the middle of it can reach the rest of the report."""
    runs = [len(run) for run in re.findall(r"`+", text)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return [fence + "text", *text.splitlines(), fence]


def _acceptance(contract, reported):
    """The contract's acceptance items with what the Agent reported for each — an item it did not report is shown as
    such — then anything else it reported (D37 phase 2: the reader sees what was skipped)."""
    results = {str(a.get("item")): a.get("result") for a in reported if isinstance(a, dict)}
    items = [{"item": _line(item), "result": results.pop(str(item), None)} for item in contract][:30]
    return items + [{"item": _line(item), "result": result} for item, result in results.items()][:30 - len(items)]


def _attempt(attempt, spec):
    evidence = attempt.get("evidence") or {}
    outcome = evidence.get("outcome") or {}
    candidate = evidence.get("candidate")
    switched = evidence.get("switched_from")
    checks = []
    for result in evidence.get("check_results") or []:
        exit_code = result.get("exit")
        state = (f"退出码 {exit_code}" if exit_code is not None else "超时" if result.get("timeout")
                 else "已停止" if result.get("stopped") else "无法运行")
        checks.append({"command": _line(result.get("command")), "state": state, "seconds": result.get("seconds")})
    return {
        "number": attempt.get("number"), "status": attempt.get("status"), "error": _line(attempt.get("error")),
        "requested": f"{spec.get('tool') or '—'} / {spec.get('model') or 'auto'}",
        "actual": f"{evidence.get('actual_backend') or '—'} / {evidence.get('actual_model') or '—'}",
        "watchdog": _line((evidence.get("watchdog") or {}).get("reason")) or None,
        "continued": ("续接上一轮（保留上下文）" if evidence.get("continued")
                      else f"续接被拒，已重新派发：{_line(evidence['continue_refused'], 80)}" if evidence.get("continue_refused")
                      else None),   # D46 P5a
        "switched": (f"{_line(switched.get('tool'), 40)} / {_line(switched.get('model'), 80)} → "
                     f"{spec.get('tool') or '—'} / {spec.get('model') or 'auto'}（协调者推荐）：{_line(switched.get('reason'))}")
                    if isinstance(switched, dict) else None,
        "decision": outcome.get("decision"), "summary": _clip(outcome.get("summary"), SUMMARY),
        "gates": [{"name": _line(g.get("name"), 60), "status": g.get("status"), "detail": _line(g.get("detail"))}
                  for g in evidence.get("gates") or []],
        "candidate": (f"{str(candidate.get('sha', ''))[:7]} · {candidate.get('files', 0)} 个文件 "
                      f"+{candidate.get('insertions', 0)} −{candidate.get('deletions', 0)}"
                      + ("（历史已改写）" if candidate.get("rewritten") else "")
                      + ("（含疑似凭证，未采用）" if candidate.get("moved") is False else "")) if candidate else None,
        "checks": checks,
        "acceptance": _acceptance(spec.get("acceptance") or [], outcome.get("acceptance") or []),
        "contract": {"deliverables": [_line(x) for x in spec.get("deliverables") or []][:20],
                     "verify": [_line(x) for x in spec.get("verify") or []][:10],
                     "paths": [_line(x) for x in spec.get("paths") or []][:20]},
        "suggestions": [_line(s) for s in outcome.get("suggestions") or []][:20],
        "reviewed": str(evidence["reviewed"])[:7] if evidence.get("reviewed") else None,
    }


def model(view, now=None):
    """The report's content, independent of its format."""
    tasks = []
    for ordinal, task in enumerate(view.get("tasks") or [], 1):
        spec = task.get("spec") or {}
        attempts = sorted(task.get("attempts") or [], key=lambda a: a.get("number") or 0)
        tasks.append({"ordinal": ordinal, "id": task.get("id"), "name": _line(spec.get("name") or task.get("id"), 200),
                      "kind": (spec.get("kind") or "—") + (f"（{roles.LABELS[spec['role']]}）" if spec.get("role") in roles.LABELS else ""),
                      "tool": spec.get("tool") or "—", "status": task.get("status"),
                      "mapping": _line((task.get("mapping") or {}).get("label"), 120) or "—",
                      "attempts": len(attempts),
                      "final": _attempt(attempts[-1], attempts[-1].get("spec") or spec) if attempts else None})
    planning = view.get("planning") or {}
    recovery = [{"number": r.get("number"), "task": r.get("failed_task_id") or "—", "status": r.get("status"),
                 "action": (r.get("decision") or {}).get("action"), "reason": _line((r.get("decision") or {}).get("reason"))}
                for r in (view.get("recovery") or {}).get("history") or []]
    summary = view.get("recovery") or {}
    budget = summary.get("budget")
    # Shown where it tells something: rounds happened, or the Session stopped with part of it used up.
    if budget and (summary.get("policy") or {}).get("enabled") and (recovery or (view.get("status") == "blocked" and budget.get("exhausted"))):
        from .recovery import budget_line
        budget = budget_line(budget)
    else:
        budget = None
    publish = view.get("publish") or None
    judged = next((t for t in reversed(view.get("tasks") or []) if (t.get("spec") or {}).get("role") == "evaluator"
                   and t.get("attempts")), None)   # D46 P3: the Session passes on the Evaluator's verdicts
    evaluation = None
    final = sorted(judged["attempts"], key=lambda a: a.get("number") or 0)[-1].get("evidence") or {} if judged else {}
    if judged and any(g.get("name") == "evaluated" for g in final.get("gates") or []):   # only where the gate applied
        by = ((final.get("evaluation") or {}).get("accepted_by") or {}).get("username")
        evaluation = [{"item": _line(a.get("item"), 300), "result": str(a.get("result") or "—"),
                       "by": by if a.get("result") == "pending" else None}
                      for a in (final.get("outcome") or {}).get("acceptance") or []]
    return {"title": _line(view.get("name") or view.get("input") or view.get("id"), 200), "id": view.get("id"),
            "evaluation": evaluation,
            "status": view.get("status"), "creator": (view.get("created_by") or {}).get("username") or "—",
            "confirmed": _line((view.get("confirmed") or {}).get("by"), 100) or None,
            "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now or time.time())),
            "request": {"input": _clip(view.get("input"), INPUT), "intent": _line(planning.get("intent"), 500),
                        "summary": _clip(planning.get("summary"), PLAN)},
            "tasks": tasks, "recovery": recovery, "budget": budget,
            "verified": str((view.get("verified") or {}).get("sha") or "")[:7] or None,
            "publish": publish and {"branch": _line(publish.get("branch"), 200), "url": str(publish.get("url") or ""),
                                    "commit": str(publish.get("commit") or "")[:7],
                                    "number": publish.get("number"), "kind": publish.get("kind"),
                                    "error": _line(publish.get("request_error"))}}


def _label(publish):
    return f"{'MR' if publish['kind'] == 'merge_request' else 'PR'} #{publish['number']}"


def _link(publish):
    url = publish["url"]
    return url.startswith("https://") and publish["number"] is not None and not any(c in url for c in "<> \t\n")


def _round(r):
    if r["action"]:
        return f"第 {r['number']} 轮 · {r['task']} · {r['action']} · {r['reason']}" + (
            f"（{r['status']}）" if r["status"] not in (None, "applied") else "")
    return f"第 {r['number']} 轮 · {r['task']} · 未决定（{r['status'] or '—'}）"


def _cell(text):
    return str(text or "").replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def markdown(view, now=None):
    m = model(view, now)
    out = [f"# {_md(m['title'])}", "",
           f"- 会话：`{m['id']}` · 状态：{_md(m['status'])} · 创建者：{_md(m['creator'])}"
           + (f" · 计划确认：{_md(m['confirmed'])}" if m["confirmed"] else "") + f" · 生成：{m['generated']}",
           "", "## 需求", ""] + (_block(m["request"]["input"]) if m["request"]["input"] else ["—"])
    if m["request"]["intent"]:
        out += ["", f"**意图**：{_md(m['request']['intent'])}"]
    if m["request"]["summary"]:
        out += [""] + _block(m["request"]["summary"])
    out += ["", "## 计划", "", "| # | 任务 | 类别 | 工具 | 来源 | 状态 | 执行次数 |", "|---|---|---|---|---|---|---|"]
    out += [f"| {t['ordinal']} | {_md(t['name'])} | {_md(t['kind'])} | {_md(t['tool'])} | {_md(t['mapping'])} | {_md(t['status'])} | "
            f"{t['attempts']} |" for t in m["tasks"]]
    out += ["", "## 任务"]
    for task in m["tasks"]:
        out += ["", f"### {task['ordinal']}\\. {_md(task['name'])}", ""]
        final = task["final"]
        if final is None:
            out.append("- 尚未执行")
            continue
        contract = final["contract"]
        out += [f"- 执行 {task['attempts']} 次；最终第 {final['number']} 次：{_md(final['status'])}",
                f"- 请求：{_md(final['requested'])}；实际：{_md(final['actual'])}"]
        if final["switched"]:
            out.append(f"- 换工具：{_md(final['switched'])}")
        if final["continued"]:
            out.append(f"- 返工：{_md(final['continued'])}")
        if final["watchdog"]:
            out.append(f"- 看门狗：{_md(final['watchdog'])}")
        if contract["deliverables"]:
            out.append("- 交付物：" + "、".join(_md(x) for x in contract["deliverables"]))
        if contract["verify"]:
            out.append("- 检查命令（契约）：" + "、".join(f"`{x.replace('`', '')}`" for x in contract["verify"]))
        if contract["paths"]:
            out.append("- 可改动：" + "、".join(_md(x) for x in contract["paths"]))
        if final["error"]:
            out.append(f"- 未通过：{_md(final['error'])}")
        if task["kind"] == "review":
            out.append(f"- 审查：绑定 {final['reviewed']}" if final["reviewed"] else "- 审查：未绑定候选")
        else:
            out.append(f"- 候选 {_md(final['candidate'])}" if final["candidate"] else "- 候选：—")
        if final["decision"]:
            out.append(f"- 结论：{_md(final['decision'])}")
            if final["summary"]:
                out += [""] + _block(final["summary"])
        if final["gates"]:
            out += ["", "| 门禁 | 结果 | 详情 |", "|---|---|---|"]
            # gate names are the platform's own identifiers, kept readable; their details may carry Agent text
            out += [f"| {g['name'] if re.fullmatch(r'[a-z_]+', g['name']) else _md(g['name'])} | "
                    f"{MARK.get(g['status'], _md(g['status']))} | {_md(g['detail'])} |" for g in final["gates"]]
        else:
            out += ["", "门禁：无记录"]
        if final["checks"]:
            out += ["", "检查命令："] + [f"- `{c['command'].replace('`', '')}` · {c['state']} · {c['seconds']} s" for c in final["checks"]]
        if final["acceptance"]:
            out += ["", "验收项："] + [f"- {MARK.get(a['result'], '—')} {_md(a['item'])}" + ("（未报告）" if a["result"] is None else "")
                                       for a in final["acceptance"]]
        if final["suggestions"]:
            out += ["", "建议："] + [f"- {_md(s)}" for s in final["suggestions"]]
    if m["evaluation"]:
        out += ["", "## 验收（以 Evaluator 为准）", "", "| 验收项 | 判定 |", "|---|---|"]
        out += [f"| {_cell(a['item'])} | {a['result']}" + (f"（{_cell(a['by'])} 确认通过）" if a["by"] else "") + " |"
                for a in m["evaluation"]]
    out += ["", "## 纠错记录", ""]
    out += [_md(m["budget"]), ""] if m["budget"] else []
    out += [f"- {_md(_round(r))}" for r in m["recovery"]] or ["无纠错"]
    out += ["", "## 发布", ""]
    if m["verified"]:
        out.append(f"- 已验证候选：{m['verified']}")
    publish = m["publish"]
    if publish:
        out.append(f"- 分支：{_md(publish['branch'])}")
        if publish["commit"]:
            out.append(f"- 推送的提交：{publish['commit']}")
        if publish["number"] is not None:
            out.append(f"- [{_label(publish)}](<{publish['url']}>)" if _link(publish)
                       else f"- {_label(publish)}：{_md(publish['url'])}")
        if publish["error"]:
            out.append(f"- 拉取请求未创建：{_md(publish['error'])}")
    else:
        out.append("尚未发布")
    return "\n".join(out) + "\n"


def html(view, now=None):
    m, e = model(view, now), lambda text: markup.escape(str(text if text is not None else ""), quote=True)
    out = ["<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
           f"<title>{e(m['title'])} · KiroCrew ON 报告</title><base target=\"_blank\"><style>{STYLE}</style></head><body>",
           f"<h1>{e(m['title'])}</h1>",
           f"<p class=\"meta\">会话 <code>{e(m['id'])}</code> · 状态 {e(m['status'])} · 创建者 {e(m['creator'])}"
           + (f" · 计划确认 {e(m['confirmed'])}" if m["confirmed"] else "") + f" · 生成 {e(m['generated'])}</p>",
           "<h2>需求</h2>", f"<pre>{e(m['request']['input'] or '—')}</pre>"]
    if m["request"]["intent"]:
        out.append(f"<p><strong>意图</strong>：{e(m['request']['intent'])}</p>")
    if m["request"]["summary"]:
        out.append(f"<pre>{e(m['request']['summary'])}</pre>")
    out += ["<h2>计划</h2>", "<table><tr><th>#</th><th>任务</th><th>类别</th><th>工具</th><th>来源</th><th>状态</th><th>执行次数</th></tr>"]
    out += [f"<tr><td>{t['ordinal']}</td><td>{e(t['name'])}</td><td>{e(t['kind'])}</td><td>{e(t['tool'])}</td>"
            f"<td>{e(t['mapping'])}</td><td>{e(t['status'])}</td><td>{t['attempts']}</td></tr>" for t in m["tasks"]]
    out += ["</table>", "<h2>任务</h2>"]
    for task in m["tasks"]:
        out.append(f"<h3>{task['ordinal']}. {e(task['name'])}</h3>")
        final = task["final"]
        if final is None:
            out.append("<p>尚未执行</p>")
            continue
        items = [f"执行 {task['attempts']} 次；最终第 {final['number']} 次：{e(final['status'])}",
                 f"请求：{e(final['requested'])}；实际：{e(final['actual'])}"]
        if final["switched"]:
            items.append(f"换工具：{e(final['switched'])}")
        if final["continued"]:
            items.append(f"返工：{e(final['continued'])}")
        if final["watchdog"]:
            items.append(f"看门狗：{e(final['watchdog'])}")
        contract = final["contract"]
        for label, values in (("交付物", contract["deliverables"]), ("检查命令（契约）", contract["verify"]),
                              ("可改动", contract["paths"])):
            if values:
                items.append(f"{label}：" + "、".join(f"<code>{e(v)}</code>" for v in values))
        if final["decision"]:
            items.append(f"结论：{e(final['decision'])}")
        if final["error"]:
            items.append(f"<span class=\"fail\">未通过：{e(final['error'])}</span>")
        if task["kind"] == "review":
            items.append(f"审查：绑定 {e(final['reviewed'])}" if final["reviewed"] else "审查：未绑定候选")
        else:
            items.append(f"候选 {e(final['candidate'])}" if final["candidate"] else "候选：—")
        out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
        if final["decision"] and final["summary"]:
            out.append(f"<pre>{e(final['summary'])}</pre>")
        if final["gates"]:
            out.append("<table><tr><th>门禁</th><th>结果</th><th>详情</th></tr>" + "".join(
                f"<tr class=\"{e(g['status'])}\"><td>{e(g['name'])}</td><td>{e(MARK.get(g['status'], g['status']))}</td>"
                f"<td>{e(g['detail'])}</td></tr>" for g in final["gates"]) + "</table>")
        else:
            out.append("<p>门禁：无记录</p>")
        for title, rows in (("检查命令", [f"<code>{e(c['command'])}</code> · {e(c['state'])} · {e(c['seconds'])} s" for c in final["checks"]]),
                            ("验收项", [f"{e(MARK.get(a['result'], '—'))} {e(a['item'])}" + ("（未报告）" if a["result"] is None else "")
                                      for a in final["acceptance"]]),
                            ("建议", [e(s) for s in final["suggestions"]])):
            if rows:
                out.append(f"<p>{title}：</p><ul>" + "".join(f"<li>{row}</li>" for row in rows) + "</ul>")
    if m["evaluation"]:
        out += ["<h2>验收（以 Evaluator 为准）</h2>", "<table><tr><th>验收项</th><th>判定</th></tr>" + "".join(
            f"<tr><td>{e(a['item'])}</td><td>{e(a['result'])}" + (f"（{e(a['by'])} 确认通过）" if a["by"] else "") + "</td></tr>"
            for a in m["evaluation"]) + "</table>"]
    out.append("<h2>纠错记录</h2>")
    if m["budget"]:
        out.append(f"<p>{e(m['budget'])}</p>")
    out.append("<ul>" + "".join(f"<li>{e(_round(r))}</li>" for r in m["recovery"]) + "</ul>" if m["recovery"] else "<p>无纠错</p>")
    out.append("<h2>发布</h2>")
    items, publish = [], m["publish"]
    if m["verified"]:
        items.append(f"已验证候选：{e(m['verified'])}")
    if publish:
        items.append(f"分支：{e(publish['branch'])}")
        if publish["commit"]:
            items.append(f"推送的提交：{e(publish['commit'])}")
        if publish["number"] is not None:
            items.append(f"<a href=\"{e(publish['url'])}\">{e(_label(publish))}</a>" if _link(publish)
                         else f"{e(_label(publish))}：{e(publish['url'])}")
        if publish["error"]:
            items.append(f"拉取请求未创建：{e(publish['error'])}")
    out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if publish else
               ("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else "") + "<p>尚未发布</p>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"

