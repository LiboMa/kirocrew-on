"""EC2 2026-10-27: a token from POST /v1/tokens failed when used at once (10 of 10), because the handler answered from
inside its transaction — the client had the token before it was committed. No route of octopus.server may answer from
inside `with database.transaction()`: the answer goes out after the commit, so what it reports is already true."""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parent.parent / "octopus/server.py"


class RespondAfterCommitTests(unittest.TestCase):
    def test_no_answer_is_sent_inside_a_transaction(self):
        tree = ast.parse(SOURCE.read_text())
        early = []
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and any("transaction()" in ast.unparse(i.context_expr) for i in node.items):
                early += [n.lineno for n in ast.walk(node) if isinstance(n, ast.Call)
                          and ast.unparse(n.func) in ("self.respond", "self.respond_page", "self.share_code_page")]
        self.assertEqual(early, [])


if __name__ == "__main__":
    unittest.main()
