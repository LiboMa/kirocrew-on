"""tools/md_to_html.py: a document's HTML twin from its Markdown (owner 2026-10-23)."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("md_to_html", Path(__file__).resolve().parents[1] / "tools" / "md_to_html.py")
md_to_html = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md_to_html)


class InlineTests(unittest.TestCase):
    def test_links_become_anchors_and_their_text_keeps_code(self):
        self.assertEqual(md_to_html.inline("见 [`docs/MVP-0.1.0.md`](MVP-0.1.0.md) 与 [BACKLOG](BACKLOG.md)。"),
                         '见 <a href="MVP-0.1.0.md"><code>docs/MVP-0.1.0.md</code></a> 与 <a href="BACKLOG.md">BACKLOG</a>。')

    def test_a_link_target_is_escaped_and_a_script_url_is_not_a_link(self):
        self.assertEqual(md_to_html.inline('[x](a.html?q="1")'), '<a href="a.html?q=&quot;1&quot;">x</a>')
        self.assertEqual(md_to_html.inline("[x](javascript:alert(1))"), "[x](javascript:alert(1))")

    def test_code_bold_and_breaks_as_before(self):
        self.assertEqual(md_to_html.inline("**粗** `a<b>` x<br>y"), "<strong>粗</strong> <code>a&lt;b&gt;</code> x<br>y")


class BlockTests(unittest.TestCase):
    def test_a_quote_is_a_note_and_its_lines_are_joined(self):
        out = md_to_html.blocks(["> **解读**：平台判定，", "> 不信 Agent 自报。", "", "后文"], Path("."))
        self.assertEqual(out, '<aside class="note"><p><strong>解读</strong>：平台判定，不信 Agent 自报。</p></aside>\n<p>后文</p>')

    def test_a_quote_keeps_its_paragraphs_and_lists(self):
        out = md_to_html.blocks(["> 一", ">", "> - a", "> - b"], Path("."))
        self.assertEqual(out, '<aside class="note"><p>一</p>\n<ul><li>a</li><li>b</li></ul></aside>')

    def test_a_mermaid_diagram_is_left_to_the_markdown_and_other_code_stays(self):
        # the HTML twin has no diagram renderer: it shows the figure included beside it instead
        out = md_to_html.blocks(["```mermaid", "flowchart LR", "  A --> B", "```", "```text", "a < b", "```"], Path("."))
        self.assertEqual(out, "<pre><code>a &lt; b</code></pre>")

    def test_a_paragraph_ends_where_a_quote_begins(self):
        self.assertEqual(md_to_html.blocks(["正文", "> 注"], Path(".")), '<p>正文</p>\n<aside class="note"><p>注</p></aside>')


if __name__ == "__main__":
    unittest.main()
