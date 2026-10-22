"""The config the platform seeds for KiroCrew uses only keys KiroCrew reads (EC2 2026-10-16: `memory.enabled` was never read,
and `dashboard.host` / `dashboard.port` / `workspaces.default.path` neither). The list is checked against the installed
KiroCrew on every deploy (tests/native); a KiroCrew upgrade fails here until the list is checked again."""
import ast
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from composition import templates

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "tests/kirocrew_contract.json").read_text())


def seeded():
    with tempfile.TemporaryDirectory() as temporary, \
            patch.dict("sys.modules", {"kiro_crew.agent": SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})}):
        root = Path(temporary)
        templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"],
                       legacy_pipeline=False)
        return json.loads((root / "crew/config.json").read_text())


class ConfigContractTests(unittest.TestCase):
    def test_the_seeded_config_uses_only_keys_kirocrew_reads(self):
        config, known = seeded(), CONTRACT["config"]
        self.assertEqual(sorted(set(config) - set(known[""])), [])
        for section, value in config.items():
            if isinstance(value, dict):
                self.assertEqual(sorted(set(value) - set(known.get(section, []))), [], section)

    def test_the_contract_is_for_the_kirocrew_the_platform_deploys(self):
        pinned = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
        self.assertEqual(CONTRACT["kirocrew_version"], pinned["KIROCREW_ACCEPTED_VERSION"])



class RuntimeErrorContractTests(unittest.TestCase):
    """The words of KiroCrew's runtime failures the platform retries on its own (design §8.1 L0); tests/native checks the
    installed KiroCrew still says them, so a reworded release does not silently send runtime faults to the Coordinator."""

    def test_every_listed_runtime_failure_is_retried_as_its_kind(self):
        from octopus.engine import transient
        listed = CONTRACT["runtime_errors"]
        self.assertGreaterEqual(len(listed), 4)
        for item in listed:
            with self.subTest(item["kind"], phrase=item["phrase"]):
                self.assertIn(item["phrase"], item["sample"])
                self.assertEqual(transient({"done": True, "error": item["sample"]}, None, False, None), item["kind"])


def endpoint(call):
    """``METHOD path`` of a ``self.native(path, body, method)`` call, the parts built at run time as {}, without a query."""
    def text(node):
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.BinOp):
            return text(node.left) + text(node.right)
        if isinstance(node, ast.JoinedStr):
            return "".join(text(v) if isinstance(v, ast.Constant) else "{}" for v in node.values)
        return "{}"
    path = text(call.args[0]).split("?")[0]
    path = re.sub(r"(\{\})+", "{}", path)
    method = next((k.value.value for k in call.keywords if k.arg == "method"), None) or (
        call.args[2].value if len(call.args) > 2 and isinstance(call.args[2], ast.Constant) else None)
    return f"{method or ('POST' if len(call.args) > 1 else 'GET')} {path}"


def bodies():
    """Every request body the Worker sends KiroCrew: (endpoint, keys), a body built in a variable followed to its dict and
    the keys set on it afterwards; anything else is returned as None keys (the test refuses it)."""
    tree = ast.parse((ROOT / "octopus/worker.py").read_text())
    found = []
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "native" and n.args):
            if len(call.args) < 2:
                continue
            body = call.args[1]
            if isinstance(body, ast.Constant) and body.value is None:   # no body (a GET or a DELETE)
                continue
            if isinstance(body, ast.Dict):
                keys = {k.value for k in body.keys if isinstance(k, ast.Constant)} if all(isinstance(k, ast.Constant) for k in body.keys) else None
            elif isinstance(body, ast.Name):
                assigned = [n.value for n in ast.walk(function) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                            and any(isinstance(t, ast.Name) and t.id == body.id for t in n.targets)]
                later = {n.targets[0].slice.value for n in ast.walk(function) if isinstance(n, ast.Assign)
                         and isinstance(n.targets[0], ast.Subscript) and isinstance(n.targets[0].value, ast.Name)
                         and n.targets[0].value.id == body.id and isinstance(n.targets[0].slice, ast.Constant)}
                keys = ({k.value for k in assigned[0].keys} | later) if len(assigned) == 1 else None
            else:
                keys = None
            found.append((endpoint(call), keys, f"{function.name}:{call.lineno}"))
    return found


class HttpContractTests(unittest.TestCase):
    """The request bodies the platform sends KiroCrew use only keys its handlers read (KiroCrew ignores the others without
    a word, as it ignored memory.enabled); tests/native checks the list against the installed KiroCrew on every deploy."""

    def test_every_body_the_worker_sends_uses_only_keys_kirocrew_reads(self):
        known = CONTRACT["http"]
        seen = bodies()
        self.assertGreaterEqual(len(seen), 8)
        for path, keys, where in seen:
            with self.subTest(where, endpoint=path):
                self.assertIsNotNone(keys, "a body the contract test cannot read: build it as a dict literal")
                if keys:
                    self.assertIn(path, known)
                    self.assertEqual(sorted(keys - set(known[path]["reads"])), [])



if __name__ == "__main__":
    unittest.main()
