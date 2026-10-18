"""Evaluator gate (D46 P3): a Session that changes code passes on its Evaluator's verdict on each acceptance item — pass,
fail or pending, each with its evidence (the closed set KiroCrew's accept_eval uses) — not on the implementer's own pass.
A pending item waits for a person (「验收待确认」). Applies to Sessions of execution contract 4 on a Worker with role cards
(an Evaluator without its card is not asked for the table)."""
RESULTS = ("pass", "fail", "pending")


def applies(snapshot, spec, config):
    return (snapshot.get("execution_contract", 1) >= 4 and spec.get("role") == "evaluator"
            and bool((config.get("capabilities") or {}).get("role_cards")))


def verdicts(spec, report):
    """(error, pending) of the Evaluator's verdicts against its own acceptance items: matched by their text, or in order
    when it paraphrased them all."""
    items = [str(i).strip() for i in spec.get("acceptance") or []]
    given = [a for a in (report or {}).get("acceptance") or [] if isinstance(a, dict)]
    by_text = {str(a.get("item") or "").strip(): a for a in given}
    matched = [by_text.get(item) for item in items]
    if None in matched and len(given) == len(items):
        matched = given
    missing = [item for item, verdict in zip(items, matched) if verdict is None]
    if missing:
        return "Evaluator 未判定验收项：" + "；".join(missing[:5]), []
    result = lambda verdict: str(verdict.get("result") or "").strip().lower()
    bad = [item for item, verdict in zip(items, matched)
           if result(verdict) not in RESULTS or not str(verdict.get("evidence") or "").strip()]
    if bad:
        return "Evaluator 的判定须为 pass / fail / pending 并附证据：" + "；".join(bad[:5]), []
    failed = [item for item, verdict in zip(items, matched) if result(verdict) == "fail"]
    if failed:
        return "Evaluator 判定未通过：" + "；".join(failed[:5]), []
    return None, [{"item": item, "evidence": str(verdict["evidence"])[:500]} for item, verdict in zip(items, matched)
                  if result(verdict) == "pending"]
