"""The record of what was built (owner 2026-10-20): commits grouped by their decision and marked where they are; the data
travels inside the dashboard, and docs/PROGRESS.md sums it up."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("progress", ROOT / "tools/progress.py")
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


class RecordTests(unittest.TestCase):
    def test_a_commit_is_grouped_by_its_decision(self):
        cases = {
            "feat(placement): Team 会话在提交者自己的边界执行（D49 第 2 期）": ("D49 第 2 期", "每人一个执行边界"),
            "fix(continue): 续接的返工总是带上任务的模型（D46 P5a，EC2 run-9507ea17… 核对）": ("D46 P5a", "Agent 角色与提示体系"),
            "feat(models): 模型自动发现（D51）": ("D51", "模型列表与自动发现"),
            "fix(console): 发起向导切换执行空间时的 ReferenceError": ("未标记 · 修复", ""),
            "docs: BACKLOG 记录延后项": ("未标记 · 文档", ""),
            "Merge pull request #3": ("未标记 · 其他", ""),
        }
        for subject, expected in cases.items():
            with self.subTest(subject=subject[:30]):
                self.assertEqual(progress.group_of(subject), expected)

    def test_decisions_come_first_newest_first(self):
        groups = [{"key": "未标记 · 修复", "last": "2026-10-20", "count": 9}, {"key": "D49 第 2 期", "last": "2026-10-19", "count": 3},
                  {"key": "D51", "last": "2026-10-20", "count": 4}]
        self.assertEqual([g["key"] for g in progress.ordered(groups)], ["D51", "D49 第 2 期", "未标记 · 修复"])

    def test_where_a_commit_is(self):
        self.assertEqual(progress.where("a", {"a"}, {"a", "b"}), "pushed")
        self.assertEqual(progress.where("b", {"a"}, {"a", "b"}), "deployed")
        self.assertEqual(progress.where("c", {"a"}, {"a", "b"}), "local")

    def test_the_dashboard_block_is_replaced_in_place(self):
        page = f"<script>\n{progress.BEGIN}\nconst DATA = {{}};\n{progress.END}\nrender();\n</script>"
        once = progress.embed(page, {"x": 1})
        self.assertIn('const DATA = {"x": 1};', once)
        self.assertEqual(progress.embed(once, {"x": 1}), once)   # running it again changes nothing
        self.assertTrue(once.endswith(f"{progress.END}\nrender();\n</script>"))

    def test_the_summary_names_completion_counts_and_groups(self):
        data = json.loads((ROOT / "docs/progress.json").read_text())
        rec = {"head": "abc1234", "base": "origin/main", "deployed_short": "d9f57c3", "counts": {"pushed": 1, "deployed": 1, "local": 1},
               "commits": [{"sha": "aaa", "date": "2026-10-20", "subject": "fix: x（D51）", "group": "D51", "title": "模型列表与自动发现",
                            "where": "local"}],
               "groups": [{"key": "D51", "title": "模型列表与自动发现", "count": 1, "last": "2026-10-20",
                           "where": {"pushed": 0, "deployed": 0, "local": 1}}]}
        text = progress.markdown(data, rec, "2026-10-20 12:00")
        self.assertIn("**本轮完成度**", text)
        self.assertIn("EC2 运行 `d9f57c3`", text)
        self.assertIn("### D51 · 模型列表与自动发现（1 个提交：仅本机 1）", text)
        self.assertIn("- `aaa` 2026-10-20 〔仅本机〕 fix: x（D51）", text)

    def test_the_dashboard_carries_the_data_block(self):
        page = (ROOT / "docs/progress-dashboard.html").read_text()
        self.assertEqual((page.count(progress.BEGIN), page.count(progress.END)), (1, 1))


if __name__ == "__main__":
    unittest.main()
