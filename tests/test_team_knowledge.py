"""D52: a repository's knowledge, reviewed by people — on a shared environment too a Knowledge task gathers what a Session
learned, kept only for the repository (no lessons written or given); on publish the items go to a Draft PR of the
repository's .octopus/knowledge.md; tasks are pointed at that file."""
import unittest

from octopus import knowledge
from tests.test_knowledge import CAPS, SOURCE, KnowledgeCase


class GatherTests(KnowledgeCase):
    def test_a_shared_environment_gathers_for_the_repository(self):
        self.configure(CAPS, "shared")
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")
        self.assertIn("knowledge", self.tasks(run))
        self.assertTrue(knowledge.gathers({"execution_contract": 4}, SOURCE, "shared", {"capabilities": CAPS}))
        self.assertFalse(knowledge.applies({"execution_contract": 4}, SOURCE, "shared", {"capabilities": CAPS}))

    def test_a_shared_environment_keeps_items_for_the_repository_only(self):
        self.configure(CAPS, "shared")
        _, run = self.run_()
        self.assertEqual(self.writes, [])   # no lessons on an environment several spaces share
        self.assertEqual({b.get("lessons") for b in self.work()}, {None})
        self.assertEqual([(i["rule"], i["result"]) for i in run["knowledge"]["items"]],
                         [("依赖用 pnpm 安装", "kept"), ("接口测试在 tests/api/", "kept")])
        self.assertIn("发布时提到仓库", run["knowledge"]["note"])

    def test_dedicated_environments_still_write_lessons(self):
        _, run = self.run_()
        self.assertEqual(len(self.writes), 1)
        self.assertEqual({i["result"] for i in run["knowledge"]["items"]}, {"written"})


if __name__ == "__main__":
    unittest.main()
