"""The request handler of octopus.server is one long function: assigning to a name that the module imports (name,
audit, quote …) makes it local everywhere in that function and breaks unrelated routes with UnboundLocalError (500).
This happened twice; the check keeps it from happening again."""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parent.parent / "octopus/server.py"


class ShadowingTests(unittest.TestCase):
    def test_handler_functions_never_rebind_module_imports(self):
        tree = ast.parse(SOURCE.read_text())
        imported = {alias.asname or alias.name.split(".")[0] for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names}
        offenders = []
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            local_imports = {alias.asname or alias.name.split(".")[0] for node in ast.walk(function)
                             if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
            for node in ast.walk(function):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in imported:
                    offenders.append(f"{function.name}:{node.lineno} assigns {node.id}")
            offenders += [f"{function.name} re-imports {n}" for n in local_imports & imported]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
