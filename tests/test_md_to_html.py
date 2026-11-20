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


class FigureTests(unittest.TestCase):
    def test_an_image_line_is_a_figure_and_the_line_after_it_starting_with_tu_is_its_caption(self):
        out = md_to_html.blocks(["![管理 › 执行环境](img/admin-env.webp)", "图 4　管理 › 执行环境。", "", "正文"], Path("."))
        self.assertEqual(out, '<figure class="shot"><img src="img/admin-env.webp" alt="管理 › 执行环境" loading="lazy">'
                              '<figcaption>图 4　管理 › 执行环境。</figcaption></figure>\n<p>正文</p>')

    def test_two_images_side_by_side_and_no_caption_needed(self):
        out = md_to_html.blocks(["![a](img/a.webp)", "![b \"q\"](img/b.webp)"], Path("."))
        self.assertEqual(out, '<figure class="pair"><img src="img/a.webp" alt="a" loading="lazy">'
                              '<img src="img/b.webp" alt="b &quot;q&quot;" loading="lazy"></figure>')

    def test_a_script_url_is_no_image(self):
        self.assertNotIn("<img", md_to_html.blocks(["![x](javascript:alert(1))"], Path(".")))


class BookTests(unittest.TestCase):
    def convert(self, text, **kw):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "a.md", Path(tmp) / "a.html"
            source.write_text(text)
            md_to_html.convert(source, target, **kw)
            return target.read_text()

    TEXT = "# 白皮书\n\n封面说明\n\n## 1．背景\n\n正文\n\n### 1.1　细节\n\n更多\n\n## 2．定位\n"

    def test_the_book_has_a_table_of_contents_with_anchors_and_a_cover(self):
        out = self.convert(self.TEXT, book=True)
        self.assertIn('<nav class="toc"', out)
        self.assertIn('<li><a href="#s1">1．背景</a></li>', out)
        self.assertIn('<li class="sub"><a href="#s1-1">1.1　细节</a></li>', out)
        self.assertIn('<h2 id="s1">1．背景</h2>', out)
        self.assertIn('<h3 id="s1-1">1.1　细节</h3>', out)
        self.assertIn('<header class="cover"><h1>白皮书</h1>\n<p>封面说明</p></header>', out)
        self.assertIn("@page", out)

    def test_without_book_the_page_is_as_before(self):
        out = self.convert(self.TEXT)
        self.assertNotIn('class="toc"', out)
        self.assertIn("<h2>1．背景</h2>", out)


if __name__ == "__main__":
    unittest.main()
