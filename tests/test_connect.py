import tempfile
import unittest
from pathlib import Path

from octopus import connect


class ConnectTest(unittest.TestCase):
    def test_both_clients_get_the_rendered_skill_and_the_token_stays_out_of_output(self):
        token = "secret-token-" + "x" * 30
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            calls = []
            run = lambda command, **kwargs: calls.append((command, kwargs.get("cwd")))
            actions = connect.connect("claude", "https://on.example/", token, home=home, run=run)
            actions += connect.connect("kiro", "https://on.example/", token, home=home, project=project, run=run)
            for path in (Path(home, ".claude/skills/kirocrew-on/SKILL.md"), Path(project, ".kiro/skills/kirocrew-on/SKILL.md")):
                text = path.read_text()
                self.assertTrue(text.startswith("---\nname: kirocrew-on\n"))
                self.assertIn("connect kiro --url https://on.example --project", text)
                self.assertNotIn("{{OCTOPUS_URL}}", text)
                self.assertNotIn(token, text)
            kiro = next(command for command, _ in calls if command[0] == "kiro-cli")
            self.assertEqual(kiro[kiro.index("--scope") + 1], "workspace")
            self.assertEqual(calls[-1][1], Path(project))
            self.assertNotIn(token, "\n".join(actions))

    def test_install_skill_backs_up_a_different_existing_copy(self):
        with tempfile.TemporaryDirectory() as dest:
            target = Path(dest, "kirocrew-on/SKILL.md")
            target.parent.mkdir()
            target.write_text("old")
            connect.install_skill("https://on.example", dest)
            self.assertIn("https://on.example/?run=", target.read_text())
            self.assertEqual([p.read_text() for p in target.parent.glob("SKILL.md.bak-*")], ["old"])


if __name__ == "__main__":
    unittest.main()
