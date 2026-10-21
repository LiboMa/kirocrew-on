"""The platform's coding skill pack (D50): methods a task's Agent follows on every backend — test first, debug to the
root cause, verify before claiming, take review feedback with rigour, give an interface a distinct design. Each is a
SKILL.md under octopus/skills/<name>/, adapted from its source (license beside it) so it never tells an unattended task
to commit, open worktrees, write plan files or wait for a person. The planner recommends them per task (``skills``); the
Worker puts the chosen files in the Attempt's folder and lists them in its instructions."""
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).with_name("skills")
CATALOG = ("test-driven-development", "systematic-debugging", "verification-before-completion", "receiving-code-review",
           "frontend-design")
LIMIT = 4


def path(name):
    return ROOT / name / "SKILL.md"


def _head(name):
    match = re.match(r"---\n(.*?)\n---\n", path(name).read_text(), re.S)
    return dict(line.split(": ", 1) for line in match.group(1).splitlines())


def description(name):
    return _head(name)["description"]


def sha256(name):
    return hashlib.sha256(path(name).read_bytes()).hexdigest()


def catalogue(compact=False):
    """One line per skill for the planner: its name and what it is for; ``compact``, the names on one line (the replan,
    whose prompt carries the failure and the Coordinator's decision within the Worker's 8000 characters)."""
    if compact:
        return "   " + "、".join(CATALOG)
    return "\n".join(f"   - {name}：{description(name)}" for name in CATALOG)


def method_section(names, folder):
    """The instructions' 「## 方法」: each chosen skill, what it is for, and its file in the Attempt's folder."""
    if not names:
        return ""
    lines = [f"- {name}：{description(name)}（{folder / 'skills' / (name + '.md')}）" for name in names]
    return ("\n\n## 方法\n开始前完整阅读并遵循下列方法；与本任务契约（验收项、检查命令、可改路径、交付物）冲突时以本任务契约为准。\n"
            + "\n".join(lines))
