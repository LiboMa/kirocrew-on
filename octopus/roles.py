"""Role cards (D46 P2): how a task's Agent works — what it does and delivers, and when it says changes_requested or
BLOCKED — chosen by the task's ``role``, while ``kind`` keeps deciding the gates. Each card is a KiroCrew-style SKILL.md
under octopus/roles/<role>/, inlined at the top of the task's instructions (owner 2026-10-18: a KiroCrew skill is fixed
per agent spec and cannot be chosen per spawn, so the card travels with the task)."""
from pathlib import Path
import re

ROOT = Path(__file__).with_name("roles")
KINDS = {"spec": ("design",), "sde": ("implement", "deliver"), "integration_test": ("test",),
         "security_test": ("review",), "challenge": ("review",), "evaluator": ("review",)}
LABELS = {"spec": "Spec 分析", "sde": "SDE 开发", "integration_test": "集成测试", "security_test": "安全测试",
          "challenge": "Challenge", "evaluator": "Evaluator"}
REVIEWERS = frozenset({"security_test", "challenge", "evaluator"})   # never the tool that wrote the change they look at


def _read(role):
    match = re.match(r"---\n(.*?)\n---\n(.*)", (ROOT / role / "SKILL.md").read_text(), re.S)
    return dict(line.split(": ", 1) for line in match.group(1).splitlines()), match.group(2).strip()


def card(role):
    """The card's text, as the task's instructions carry it."""
    return _read(role)[1]


def description(role):
    return _read(role)[0]["description"]


def catalogue():
    """One line per role for the planner: its id, label, kind and what it is for."""
    return "\n".join(f"   - {role}（{LABELS[role]}，kind {'/'.join(kinds)}）：{description(role)}" for role, kinds in KINDS.items())


def unindependent(planned):
    """Reviewing tasks that use the tool of a task before them that wrote the change: [(task, author tool)]."""
    found, authors = [], set()
    for item in planned:
        if item.get("role") in REVIEWERS and item.get("tool") in authors:
            found.append((item, item["tool"]))
        if item.get("kind") in ("implement", "deliver"):
            authors.add(item.get("tool"))
    return found
