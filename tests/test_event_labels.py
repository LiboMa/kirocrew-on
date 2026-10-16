"""Every event kind the platform writes has a title in the console timeline (EC2 run-d098543e…: 「run.published」 showed as the raw
kind). The kinds are read from the journal calls in ``octopus/``; a kind built at run time must be listed in DYNAMIC."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIND_ARGUMENT = {"record": 2, "change": 4, "change_rows": 5, "remove_rows": 5}


def actions(function):
    """The actions ``Service.operate`` accepts, from its first ``require(action in {...})``."""
    tree = ast.parse((ROOT / "octopus/domain.py").read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    check = next(n for n in ast.walk(method) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "require")
    return {element.value for element in check.args[0].comparators[0].elts}


DYNAMIC = {
    "'command.' + action": {"command." + action for action in actions("operate")},
    "'session.' + run['desired']": {"session." + action for action in ("stop", "close", "delete", "reopen")},
}


def emitted():
    kinds, unknown = set(), []
    for path in sorted((ROOT / "octopus").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in KIND_ARGUMENT
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "journal"):
                continue
            index = KIND_ARGUMENT[node.func.attr]
            kind = node.args[index] if len(node.args) > index else next(k.value for k in node.keywords if k.arg == "kind")
            if isinstance(kind, ast.Constant):
                kinds.add(kind.value)
            elif ast.unparse(kind) in DYNAMIC:
                kinds |= DYNAMIC[ast.unparse(kind)]
            else:
                unknown.append(f"{path.relative_to(ROOT)}:{node.lineno} {ast.unparse(kind)}")
    return kinds, unknown


def labelled():
    text = (ROOT / "web/src/lib/labels.ts").read_text()
    def block(name):
        return re.search(name + r"[^=]*=\s*(?:new Set\()?[\[{](.*?)[\]}]\)?;", text, re.S).group(1)
    return set(re.findall(r'"([a-z_]+\.[a-z_-]+)"\s*:', block("EVENT_LABELS"))), set(re.findall(r'"([a-z_]+\.[a-z_-]+)"', block("TIMELINE_HIDDEN")))


class EventLabelTests(unittest.TestCase):
    def test_every_event_kind_written_has_a_timeline_title(self):
        kinds, _ = emitted()
        titles, hidden = labelled()
        self.assertGreater(len(kinds), 30)
        self.assertEqual(sorted(kinds - titles - hidden), [])

    def test_a_kind_built_at_run_time_is_listed(self):
        self.assertEqual(emitted()[1], [])

    def test_the_labels_are_read(self):
        titles, hidden = labelled()
        self.assertIn("run.created", titles)
        self.assertEqual(hidden, {"task.state", "recovery.state"})


if __name__ == "__main__":
    unittest.main()
