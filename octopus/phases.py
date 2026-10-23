"""D69 (owner 2026-10-22: 「按阶段把关」): a task's phase decides which failures stop the Session.

- diverge (发散: brainstorming, research, options) — only the security floor and real stops block; the task's own failure
  is a warning and its conclusion goes to the next task;
- design (设计) — the same until the phase's last task, which is judged as today;
- converge (收敛: implement, write, test, deliver) — as today (D57).

A phase is written by the planner or a person (``phase``), else inferred from the kind; a review takes the phase of what it
reviews. Which failures may be let through is an allow-list (D57 session review): anything else, and anything added later,
keeps blocking."""
from . import planning, knowledge
from .recovery import NEEDS_INPUT, failing_gate

PHASES = ("diverge", "design", "converge")
LABELS = {"diverge": "发散", "design": "设计", "converge": "收敛"}
PREFIX = {"diverge": "发散阶段不阻断：", "design": "设计阶段中途不阻断（阶段末再把关）："}
# The failing gate and its failure types a phase may let through; an outcome naming what a person must supply never is.
RELAXABLE = {"native_completed": {"native_error"}, "identity_match": {"tool"}}
NOT_RELAXED_OUTCOMES = NEEDS_INPUT | {"environment", "security"}


def own(spec):
    """The phase ``spec`` names, or its kind's; None for a review without one (it takes what it reviews)."""
    if spec.get("phase") in PHASES:
        return spec["phase"]
    kind = spec.get("kind")
    return "diverge" if kind == "research" else "design" if kind == "design" else None if kind == "review" else "converge"


def phase(spec, specs):
    """The phase of ``spec`` among ``specs`` (the Session's tasks in plan order)."""
    found = own(spec)
    if found:
        return found
    ids = [s.get("id") for s in specs]
    before = specs[:ids.index(spec["id"])] if spec.get("id") in ids else list(specs)
    reviewed = [s for s in before if s.get("id") in (spec.get("depends_on") or []) and own(s)]
    nearest = reviewed or [s for s in before if own(s)]
    return own(nearest[-1]) if nearest else "converge"


def phase_end(task_id, specs):
    """Whether ``task_id`` is the last task of a design phase: the next task in plan order is not design, or there is none."""
    ids = [s.get("id") for s in specs]
    if task_id not in ids:
        return False
    index = ids.index(task_id)
    if phase(specs[index], specs) != "design":
        return False
    return index == len(specs) - 1 or phase(specs[index + 1], specs) != "design"


def relaxed(spec, specs):
    """The detail prefix of a failure this task's phase lets through, or None: diverge, and design before its end."""
    found = phase(spec, specs)
    if found == "diverge" or found == "design" and not phase_end(spec.get("id"), specs):
        return PREFIX[found]
    return None


def platform_task(spec, snapshot):
    """The platform's own tasks have no phase: planning and replanning, the Evaluator, Knowledge, a workflow design."""
    task_id = spec.get("id")
    return (snapshot.get("purpose") == "workflow_design" or planning.planning_task(task_id, snapshot)
            or spec.get("role") == "evaluator" or knowledge.platform(snapshot, task_id))


def relax(evidence, spec, snapshot, error, specs):
    """Let this Attempt's failure through when its phase allows (D69 §3, §3a-4): the fail record becomes a warning with the
    phase's prefix and ``relaxed: "fail"``, ``gate`` and ``failure_type`` go (they key the anti-spin), and ``phase``,
    ``phase_relaxed`` and ``phase_relaxed_error`` say what happened. Returns the prefix; None, touching nothing, when the
    failure blocks. The judge chain calls it; a re-judgement after a false alarm may call it too."""
    if platform_task(spec, snapshot):
        return None
    if evidence.get("gate") == "no_secrets" or evidence.get("failure_type") == "security":   # guard.blocking (D57 T8)
        return None
    if any(r.get("name") == "review_bound" and r.get("status") == "fail" for r in evidence.get("gates") or []):
        return None   # a rejecting review's edits that could not be put back stay in the workspace: never let them through
    prefix = relaxed(spec, specs)
    if prefix is None:
        return None
    gate, failure = failing_gate(evidence), evidence.get("failure_type")
    if gate == "outcome_pass":
        if failure in NOT_RELAXED_OUTCOMES:
            return None
    elif failure not in RELAXABLE.get(gate, ()):
        return None
    for record in evidence.get("gates") or []:
        if record.get("status") == "fail":
            record.update(status="warn", detail=prefix + str(record.get("detail") or error or ""), relaxed="fail")
    evidence.pop("gate", None)
    evidence.pop("failure_type", None)
    evidence.update(phase=phase(spec, specs), phase_relaxed=gate, phase_relaxed_error=error)
    return prefix
