#!/usr/bin/env python3
"""Build the whole user manual as one PDF:  doc/Remote-Test-Jig-User-Manual.pdf

    python3 doc/src/make_pdf.py                 # default output
    python3 doc/src/make_pdf.py -o manual.pdf   # elsewhere
    python3 doc/src/make_pdf.py --html out.html # also keep the intermediate HTML

Needs pandoc (Markdown -> HTML) and Google Chrome / Chromium (HTML -> PDF); no LaTeX.
The chapters are concatenated in order, links between chapter files become links inside the PDF,
the "← previous · next →" navigation lines are dropped, and every chapter starts on a new page.
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent
CHAPTERS = ["README.md"] + sorted(p.name for p in DOC.glob("[0-9][0-9]-*.md"))
NAV = re.compile(r"^\[← .*$|^\[Next: .*$|^\[Manual index\].*$", re.M)

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm 15mm; }
html { font-family: Inter, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 10.2pt; color: #0f172a; line-height: 1.45; }
body { margin: 0; }
h1 { font-size: 21pt; margin: 0 0 10pt; padding-bottom: 6pt; border-bottom: 2px solid #1d4ed8; color: #1e3a8a; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; color: #1e3a8a; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 14pt 0 4pt; break-after: avoid; }
h4 { font-size: 10.5pt; margin: 12pt 0 4pt; break-after: avoid; }
.chapter { break-before: page; }
p, li { orphans: 3; widows: 3; }
a { color: #1d4ed8; text-decoration: none; }
code { font-family: "JetBrains Mono", Consolas, Menlo, monospace; font-size: 8.8pt; background: #f1f5f9; padding: 0 3px; border-radius: 3px; }
pre { background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9pt; break-inside: auto; }
tr { break-inside: avoid; }
th, td { border: 1px solid #cbd5e1; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eef2ff; }
img { max-width: 100%; max-height: 225mm; display: block; margin: 6pt auto; break-inside: avoid; }
td img { max-height: 120mm; }
blockquote { margin: 8pt 0; padding: 6px 10px; border-left: 4px solid #f59e0b; background: #fffbeb; }
figure { margin: 0; break-inside: avoid; }
figcaption { display: none; }
/* cover + contents */
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; }
.cover h1 { border: 0; font-size: 30pt; margin-bottom: 4pt; }
.cover .sub { font-size: 13pt; color: #475569; }
.cover .meta { margin-top: 30pt; color: #64748b; font-size: 10pt; }
nav#TOC { break-before: page; }
nav#TOC::before { content: "Contents"; display: block; font-size: 21pt; font-weight: 700; color: #1e3a8a;
                  border-bottom: 2px solid #1d4ed8; padding-bottom: 6pt; margin-bottom: 10pt; }
nav#TOC ul { list-style: none; padding-left: 14pt; }
nav#TOC > ul { padding-left: 0; }
nav#TOC > ul > li { margin-top: 6pt; font-weight: 600; }
nav#TOC > ul > li li { font-weight: 400; }
"""


def gh_slug(text: str) -> str:
    """GitHub's heading anchor rule (what the .md files link to)."""
    t = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return t.replace(" ", "-")


def chapter_md(name: str) -> str:
    stem = Path(name).stem
    src = NAV.sub("", (DOC / name).read_text(encoding="utf-8"))

    # 1. explicit, globally unique heading ids: <stem>--<github-slug>
    def head(m: re.Match) -> str:
        hashes, title = m.group(1), m.group(2).strip()
        return f"{hashes} {title} {{#{stem}--{gh_slug(title)}}}"
    src = re.sub(r"^(#{1,6})\s+(.+?)\s*$", head, src, flags=re.M)

    # 2. links: other-chapter.md[#frag] / #frag -> anchors inside the single document
    def link(m: re.Match) -> str:
        text, target = m.group(1), m.group(2)
        if re.match(r"^[a-z]+:", target) or target.endswith((".svg", ".png")) or "/" in target and not target.startswith(".."):
            return m.group(0)
        path, _, frag = target.partition("#")
        if path in ("", name):
            tstem = stem
        elif path.endswith(".md") and (DOC / path).exists() and "/" not in path:
            tstem = Path(path).stem
        else:
            return m.group(0)                 # ../README.md, src/README.md … keep as file links
        anchor = f"{tstem}--{frag}" if frag else f"ch-{tstem}"
        return f"[{text}](#{anchor})"
    src = re.sub(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)", link, src)

    # 3. clickable figures [![alt](x.png)](x.svg) -> just the image (the PDF embeds the PNG)
    src = re.sub(r"\[(!\[[^\]]*\]\([^)]+\))\]\([^)]+\.svg\)", r"\1", src)
    return f"::: {{#ch-{stem} .chapter}}\n\n{src}\n\n:::\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default=str(DOC / "Remote-Test-Jig-User-Manual.pdf"))
    ap.add_argument("--html", help="also write the intermediate HTML here")
    a = ap.parse_args()

    pandoc = shutil.which("pandoc")
    chrome = next((shutil.which(c) for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)), None)
    if not pandoc or not chrome:
        sys.exit("needs pandoc and google-chrome/chromium on PATH")

    today = datetime.date.today().isoformat()
    cover = (f'<div class="cover"><h1>Remote Test Jig</h1><div class="sub">User Manual — XIAO nRF52840 '
             f'bench test suite for BLE and IR TV remotes</div><div class="meta">Generated {today} from doc/*.md</div></div>\n\n')
    body = "\n".join(chapter_md(c) for c in CHAPTERS)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "manual.md").write_text(body, encoding="utf-8")
        (tmp / "manual.css").write_text(CSS, encoding="utf-8")
        (tmp / "cover.html").write_text(cover, encoding="utf-8")     # include-before comes ahead of the TOC
        html = tmp / "manual.html"
        subprocess.run([pandoc, str(tmp / "manual.md"), "-f", "markdown-implicit_figures", "-t", "html5",
                        "--standalone", "--embed-resources", "--toc", "--toc-depth=2",
                        "--metadata", "title=Remote Test Jig — User Manual", "--metadata", "pagetitle=Remote Test Jig — User Manual",
                        "--variable", "title=", "--css", str(tmp / "manual.css"),
                        "--include-before-body", str(tmp / "cover.html"),
                        "--resource-path", str(DOC), "-o", str(html)], check=True)
        if a.html:
            shutil.copy(html, a.html)
        out = Path(a.output).resolve()
        subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={tmp / 'chrome'}",
                        "--no-pdf-header-footer", f"--print-to-pdf={out}", html.as_uri()],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
