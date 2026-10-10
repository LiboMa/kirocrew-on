"""Platform package boundaries: octopus/ never imports legacy root modules."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
LEGACY = {"api", "workflow_workspace", "workflow_files", "development_prompts", "poc", "workflow",
          "workflow_library", "workflow_sessions", "pipeline", "pipeline_spec"}


def imported(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


class LayoutTests(unittest.TestCase):
    def test_platform_package_does_not_import_legacy_root_modules(self):
        offenders = {str(p.relative_to(ROOT)): sorted(set(imported(p)) & LEGACY) for p in (ROOT / "octopus").rglob("*.py")}
        self.assertEqual({k: v for k, v in offenders.items() if v}, {})

    def test_legacy_names_are_the_same_module_objects(self):
        import api, workflow_workspace, workflow_files, development_prompts
        from octopus import gateway_client, sources, documents, prompts
        self.assertIs(api, gateway_client)
        self.assertIs(workflow_workspace, sources)
        self.assertIs(workflow_files, documents)
        self.assertIs(development_prompts, prompts)

    def test_templates_live_in_the_platform_package_and_legacy_copy_matches(self):
        from octopus import intent
        self.assertEqual(intent.LEGACY, ROOT / "octopus" / "templates" / "workflow-templates.json")
        self.assertEqual((ROOT / "ui" / "workflow-templates.json").read_bytes(), intent.LEGACY.read_bytes())

    def test_yaml_requires_installed_pyyaml_without_mac_fallback(self):
        from octopus import documents
        with patch.dict(sys.modules, {"yaml": None}):
            with self.assertRaisesRegex(ValueError, "PyYAML"):
                documents.yaml_module()
        self.assertNotIn("/Applications", (ROOT / "octopus" / "documents.py").read_text())


    def test_no_mac_paths_outside_compat_layer(self):
        for path in [*(ROOT / "octopus").rglob("*.py"), *(ROOT / "composition").rglob("*.py")]:
            text = path.read_text()
            if path.name == "config.py":
                text = text.replace("/Applications/KiroCrew.app", "")  # documented macOS default only
            with self.subTest(path=path.name):
                self.assertNotIn("/Applications", text)
                self.assertNotIn("/opt/homebrew", text)

if __name__ == "__main__":
    unittest.main()
