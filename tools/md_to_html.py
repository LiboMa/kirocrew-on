"""A document's HTML twin from its Markdown (owner 2026-10-23: explanations as Markdown and HTML, the same content).

    python tools/md_to_html.py docs/CONTINUE.md docs/continue.html

Handles what the docs use: headings, paragraphs, **bold**, `code`, fenced code, tables, and lists nested by indentation
(a list item may hold a table or another list); `<br>` breaks a line in a table cell. ``<!-- html: path -->`` on its own line includes a raw HTML fragment
(a figure), resolved beside the Markdown file; a ```mermaid diagram is left to the Markdown (the HTML has no renderer: such a
document includes a figure beside it), and ``> `` lines are a note (the whitepaper's 解读). An image on a line of its own
(``![说明](img/x.webp)``) is a figure, two in a row sit side by side, and a line right after them starting with 图 is their
caption. ``--book`` lays a long document out as a book: a table of contents beside it from its ## and ### headings,
the part before the first ## as its cover, and A4 pages when printed (the whitepaper). Lines of a paragraph are joined without a space between Chinese text,
with one elsewhere, as a reader expects."""
import html
from pathlib import Path
import re
import sys

LIST = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")
CJK = re.compile(r"[　-〿㐀-鿿＀-￯]")

STYLE = """
  :root { --ink: #172033; --muted: #5b6577; --line: #e3e7ef; --bg: #f6f8fb; --card: #ffffff; --brand: #5b4bdb; --brand-soft: #eeebff;
          --native: #0e6f86; --native-soft: #e2f3f7; --poc: #4a5568; --poc-soft: #edf0f5;
          --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
          --mono: "SF Mono", Menlo, Consolas, monospace; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.75 var(--sans); }
  main { max-width: 1040px; margin: 0 auto; padding: 56px 28px 96px; }
  main > p, main > ul, main > ol, li > p { max-width: 44em; }
  h1 { font-size: 32px; line-height: 1.3; margin: 0 0 16px; }
  h2 { font-size: 23px; margin: 56px 0 10px; padding-top: 20px; border-top: 1px solid var(--line); }
  h3 { font-size: 18px; margin: 28px 0 8px; }
  p { margin: 0 0 14px; }
  ul, ol { padding-left: 1.5em; margin: 0 0 14px; }
  li { margin: 5px 0; }
  li > ul, li > ol { margin: 6px 0 4px; }
  code { font-family: var(--mono); font-size: 0.86em; background: #eef1f6; padding: 1px 5px; border-radius: 4px; }
  pre { font-family: var(--mono); font-size: 13px; line-height: 1.6; background: #101828; color: #e7ecf5; padding: 16px 18px;
        border-radius: 8px; overflow-x: auto; margin: 0 0 18px; }
  pre code { background: none; padding: 0; color: inherit; font-size: inherit; }
  .table { overflow-x: auto; margin: 14px 0 18px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; line-height: 1.65; }
  th, td { text-align: left; vertical-align: top; padding: 10px 14px; border-bottom: 1px solid var(--line); }
  tr:last-child td { border-bottom: none; }
  thead th { background: #f0f3f8; font-weight: 600; white-space: nowrap; }
  strong { font-weight: 650; }
  figure { margin: 18px 0 24px; }
  .flow { display: flex; flex-wrap: wrap; align-items: stretch; gap: 10px 8px; list-style: none; padding: 0; margin: 0; }
  .flow li { margin: 0; flex: 1 1 150px; background: var(--card); border: 1px solid var(--line); border-top: 3px solid var(--c, var(--brand));
             border-radius: 8px; padding: 10px 12px; font-size: 14px; line-height: 1.5; }
  .flow li b { display: block; font-size: 13px; color: var(--c, var(--brand)); margin-bottom: 2px; }
  .flow .chat { --c: var(--native); } .flow .keep { --c: var(--poc); }
  figcaption { font-size: 13px; color: var(--muted); margin-top: 8px; }
  .note { margin: 16px 0 20px; max-width: 46em; background: var(--brand-soft); border-left: 4px solid var(--brand);
          border-radius: 0 8px 8px 0; padding: 12px 16px; font-size: 15px; }
  .note p:last-child, .note ul:last-child { margin-bottom: 0; }
  a:focus-visible { outline: 3px solid var(--brand); outline-offset: 2px; }
  @media (max-width: 760px) { main { padding: 32px 18px 72px; } h1 { font-size: 26px; } }
  @media print { body { background: #fff; } .table, .flow li { break-inside: avoid; } }
"""


LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)\s*$")

BOOK_STYLE = """
  :root { --serif: "Songti SC", "STSong", "Source Han Serif SC", "Noto Serif SC", "Noto Serif CJK SC", serif; }
  body { background: #fff; font-size: 16.5px; line-height: 1.8; -webkit-font-smoothing: antialiased; }
  .wrap { display: grid; grid-template-columns: 280px minmax(0, 1fr); }
  nav.toc { position: sticky; top: 0; height: 100vh; overflow: auto; border-right: 1px solid var(--line); padding: 30px 22px 40px;
            font-size: 14px; line-height: 1.6; background: #fbfbfc; }
  nav.toc .brand { font-family: var(--serif); font-size: 19px; font-weight: 600; margin-bottom: 18px; }
  nav.toc ol { list-style: none; margin: 0; padding: 0; }
  nav.toc li { margin: 0 0 6px; }
  nav.toc li.sub { padding-left: 16px; font-size: 13px; }
  nav.toc a { color: var(--muted); text-decoration: none; }
  nav.toc a:hover { color: var(--brand); }
  main { max-width: 980px; margin: 0; padding: 56px 64px 120px; }
  h1, h2, h3, h4 { font-family: var(--serif); font-weight: 600; }
  h2 { font-size: 28px; margin: 72px 0 18px; padding-top: 16px; border-top: 2px solid var(--ink); scroll-margin-top: 12px; }
  h3 { font-size: 20px; margin: 36px 0 10px; scroll-margin-top: 12px; }
  .cover { padding: 20px 0 8px; }
  .cover h1 { font-size: 42px; margin: 6px 0 18px; }
  figure.shot img, figure.pair img { display: block; width: 100%; height: auto; border: 1px solid var(--line); border-radius: 10px; background: #fff; }
  figure.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
  figure.pair figcaption { grid-column: 1 / -1; }
  @media screen and (max-width: 1000px) {
    .wrap { display: block; } nav.toc { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
    main { padding: 32px 20px 80px; } figure.pair { grid-template-columns: 1fr; } }
  @media print {
    @page { size: A4; margin: 15mm 14mm 16mm; }
    body { font-size: 10.5pt; line-height: 1.7; }
    nav.toc { display: none; } .wrap { display: block; } main { max-width: none; padding: 0; }
    h2 { break-before: page; margin-top: 0; } h3, h4 { break-after: avoid; }
    figure, pre, .note, tr { break-inside: avoid; }
    a { color: inherit; } pre { white-space: pre-wrap; }
    header.cover { break-after: page; } .cover figure img { max-height: 95mm; width: auto; max-width: 100%; margin: 0 auto; }
    figcaption { font-size: 9pt; } }
"""


def safe(target):
    """Relative and http(s) targets only: javascript: and the like are not followed."""
    return not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or bool(re.match(r"^https?://", target, re.I))


def image(line):
    found = IMAGE.match(line.strip())
    return found if found and safe(found.group(2)) else None


def inline(text):
    """A line's code, bold, line breaks and links; a link's text may hold code. Only relative and http(s) targets link."""
    out, at = [], 0
    for match in LINK.finditer(text):
        label, target = match.groups()
        if not safe(target):
            continue   # javascript: and the like stay text
        out.append(spans(text[at:match.start()]))
        out.append(f'<a href="{html.escape(target)}">{spans(label)}</a>')
        at = match.end()
    out.append(spans(text[at:]))
    return "".join(out)


def spans(text):
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append("<code>" + html.escape(part[1:-1]) + "</code>")
        else:
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(part))
            out.append(text.replace("&lt;br&gt;", "<br>"))   # a line break inside a table cell
    return "".join(out)


def join(lines):
    text = ""
    for line in (l.strip() for l in lines):
        if text and not (CJK.search(text[-1]) and CJK.search(line[0])):
            text += " "
        text += line
    return text


def cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def table(lines):
    head, body = cells(lines[0]), [cells(r) for r in lines[2:]]
    rows = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return ('<div class="table"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head) +
            f"</tr></thead><tbody>{rows}</tbody></table></div>")


def starts_block(line):
    return line.startswith(("```", "#", "|", "<!--", ">")) or bool(LIST.match(line)) or bool(image(line))


def blocks(lines, base):
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.lstrip().startswith("```"):
            end = next(j for j in range(i + 1, len(lines)) if lines[j].lstrip().startswith("```"))
            if line.strip() == "```mermaid":
                i = end + 1
                continue
            out.append("<pre><code>" + html.escape("\n".join(l[len(line) - len(line.lstrip()):] for l in lines[i + 1:end])) + "</code></pre>")
            i = end + 1
        elif line.startswith(">"):
            end = i
            while end < len(lines) and lines[end].startswith(">"):
                end += 1
            inner = [l[2:] if l.startswith("> ") else l[1:] for l in lines[i:end]]
            out.append(f'<aside class="note">{blocks(inner, base)}</aside>')
            i = end
        elif image(line):
            end, shots = i, []
            while end < len(lines) and image(lines[end]):
                alt, src = image(lines[end]).groups()
                shots.append(f'<img src="{html.escape(src)}" alt="{html.escape(alt)}" loading="lazy">')
                end += 1
            caption = ""
            if end < len(lines) and lines[end].startswith("图"):
                caption, end = f"<figcaption>{inline(lines[end].strip())}</figcaption>", end + 1
            out.append(f'<figure class="{"pair" if len(shots) == 2 else "shot"}">' + "".join(shots) + caption + "</figure>")
            i = end
        elif re.match(r"#{1,6} ", line):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{inline(line[level:].strip())}</h{level}>")
            i += 1
        elif line.strip().startswith("<!--"):
            found = re.match(r"\s*<!--\s*html:\s*(\S+)\s*-->", line)
            if found:
                out.append((base / found.group(1)).read_text())
            i += 1
        elif line.lstrip().startswith("|"):
            end = i
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            out.append(table([l.strip() for l in lines[i:end]]))
            i = end
        elif LIST.match(line):
            i = listing(lines, i, out, base)
        else:
            end = i
            while end < len(lines) and lines[end].strip() and not starts_block(lines[end]):
                end += 1
            out.append(f"<p>{inline(join(lines[i:end]))}</p>")
            i = end
    return "\n".join(out)


def listing(lines, i, out, base):
    indent = len(LIST.match(lines[i]).group(1))
    ordered = LIST.match(lines[i]).group(2)[0].isdigit()
    first = int(LIST.match(lines[i]).group(2)[:-1]) if ordered else None
    items = []
    while i < len(lines):
        m = LIST.match(lines[i])
        if not m or len(m.group(1)) != indent or m.group(2)[0].isdigit() != ordered:
            if lines[i].strip() == "" and i + 1 < len(lines) and (LIST.match(lines[i + 1]) or "")\
                    and len(LIST.match(lines[i + 1]).group(1)) == indent:
                i += 1
                continue
            break
        content = indent + len(m.group(2)) + 1
        body, i = [m.group(3)], i + 1
        while i < len(lines):
            line = lines[i]
            if line.strip() and len(line) - len(line.lstrip()) < content:
                break
            if not line.strip():
                ahead = next((l for l in lines[i + 1:] if l.strip()), None)
                if ahead is None or len(ahead) - len(ahead.lstrip()) < content:
                    break
            body.append(line[content:] if line.strip() else "")
            i += 1
        rendered = blocks(body, base)
        if rendered.startswith("<p>") and rendered.count("<p>") == 1 and rendered.endswith("</p>"):
            rendered = rendered[3:-4]
        elif rendered.startswith("<p>"):
            head, _, rest = rendered.partition("</p>")
            rendered = head[3:] + rest
        items.append(f"<li>{rendered}</li>")
    tag = "ol" if ordered else "ul"
    start = f' start="{first}"' if ordered and first != 1 else ""
    out.append(f"<{tag}{start}>" + "".join(items) + f"</{tag}>")
    return i


def anchored(body):
    """The book's ## and ### headings with anchors, and its table of contents."""
    items, numbers = [], [0, 0]

    def anchor(match):
        level, inner = match.group(1), match.group(2)
        if level == "h2":
            numbers[0], numbers[1] = numbers[0] + 1, 0
            ident = f"s{numbers[0]}"
            items.append(f'<li><a href="#{ident}">{inner}</a></li>')
        else:
            numbers[1] += 1
            ident = f"s{numbers[0]}-{numbers[1]}"
            items.append(f'<li class="sub"><a href="#{ident}">{inner}</a></li>')
        return f'<{level} id="{ident}">{inner}</{level}>'

    return re.sub(r"<(h[23])>(.*?)</\1>", anchor, body), "".join(items)


def convert(source, target, book=False):
    text = Path(source).read_text()
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), Path(source).stem)
    body = blocks(text.splitlines(), Path(source).parent)
    style = STYLE
    if book:
        body, toc = anchored(body)
        at = body.find("<h2")
        cover, rest = (body[:at], body[at:]) if at >= 0 else (body, "")
        brand = html.escape(title.split("：")[0])
        body = (f'<div class="wrap">\n<nav class="toc" aria-label="目录"><div class="brand">{brand}</div><ol>{toc}</ol></nav>\n'
                f'<main>\n<header class="cover">{cover.strip()}</header>\n{rest}\n</main>\n</div>')
        style = STYLE + BOOK_STYLE
    else:
        body = f"<main>\n{body}\n</main>"
    Path(target).write_text(f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<!-- Generated from {Path(source).name} by tools/md_to_html.py; edit the Markdown, then regenerate. -->
<style>{style}</style>
</head>
<body>
{body}
</body>
</html>
""")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--book"]
    convert(args[0], args[1], book="--book" in sys.argv[1:])
