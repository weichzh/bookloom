from __future__ import annotations

import base64
import json
import posixpath
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urldefrag

import pytest

from tools import translator
from tools.browser_runtime import run_hidden

ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / "formats/pandoc/latex.lua"
H = "{http://www.w3.org/1999/xhtml}"
EP = "{http://www.idpf.org/2007/ops}type"
pytestmark = pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")

BODY = r"""
\documentclass{book}
\usepackage{translator}
\begin{document}
\chapter*{Controlled title}\label{reader-title}
Author A and Author B. Based on research with Collaborator C.
\chapter*{Preface}\label{preface}
Preface paragraph.
\chapter{First chapter}\label{body}
Body paragraph. See \ref{reader-title}.
\section{First section}\label{section}
Section paragraph.
\end{document}
"""


def reader(source):
    return json.loads(translator.run(
        ["pandoc", "--from=latex+raw_tex", "--to=json",
         "--lua-filter=" + str(FILTER)],
        input_text=source,
    ).stdout)


@pytest.mark.parametrize("placement", ["before", "after"])
def test_declaration_marks_only_unique_native_unnumbered_header(placement):
    marker = r"\epubunlisted{reader-title}" + "\n"
    source = BODY.replace(
        r"\chapter*{Controlled title}",
        marker + r"\chapter*{Controlled title}",
    ) if placement == "before" else BODY.replace(
        r"\label{reader-title}", r"\label{reader-title}" + "\n" + marker,
    )
    doc = reader(source)
    headers = {b["c"][1][0]: b["c"] for b in doc["blocks"] if b["t"] == "Header"}
    assert headers["reader-title"][1][1] == ["unnumbered", "unlisted"]
    assert headers["preface"][1][1] == ["unnumbered"]
    assert "unlisted" not in headers["body"][1][1]
    assert "unlisted" not in headers["section"][1][1]
    assert [headers[k][0] for k in ("reader-title", "preface", "body", "section")] == [
        1, 1, 1, 2
    ]
    assert "epubunlisted" not in json.dumps(doc)
    author_paragraph = next(b for b in doc["blocks"] if b["t"] == "Para")
    words = [n["c"] for n in author_paragraph["c"] if n["t"] == "Str"]
    assert " ".join(words) == (
        "Author A and Author B. Based on research with Collaborator C."
    )


@pytest.mark.parametrize("source,match", [
    (BODY.replace(r"\end{document}", r"\epubunlisted{absent}\end{document}"), "唯一真实标题"),
    (BODY.replace(r"\end{document}", r"\epubunlisted{body}\end{document}"), "不编号标题"),
    (BODY.replace(r"\end{document}", r"\epubunlisted{}\end{document}"), "必须指定真实标题"),
    (BODY.replace(r"\label{preface}", r"\label{reader-title}")
     .replace(r"\end{document}", r"\epubunlisted{reader-title}\end{document}"), "唯一真实标题"),
    (r"Text \label{not-a-header}\epubunlisted{not-a-header}", "唯一真实标题"),
])
def test_invalid_declarations_fail_without_silently_changing_contents(source, match):
    with pytest.raises(translator.CliError, match=match):
        reader(source)


def test_real_epub_keeps_title_visible_but_not_in_nav(tmp_path):
    entry = tmp_path / "main.tex"
    entry.write_text(
        BODY.replace(r"\label{reader-title}",
                     r"\label{reader-title}" + "\n" + r"\epubunlisted{reader-title}"),
        encoding="utf-8",
    )
    cover = tmp_path / "cover.png"
    cover.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    output = tmp_path / "book.epub"
    translator.build_latex_epub_source(
        root=ROOT, work_path=tmp_path, entry=entry, output=output,
        title="No generated title", author=None, language="en",
        identifier="urn:test:unlisted-title", cover=cover,
        structure_options=["--metadata=titlepage:false", "--toc-depth=2"],
        bodymatter="body",
    )
    with zipfile.ZipFile(output) as z:
        docs = {n: ET.fromstring(z.read(n)) for n in z.namelist()
                if n.endswith(".xhtml")}
    ids = {(n, e.get("id")): e for n, r in docs.items() for e in r.iter()
           if e.get("id")}
    title_targets = [e for (n, i), e in ids.items() if i == "reader-title"]
    assert len(title_targets) == 1
    assert "Controlled title" in "".join(title_targets[0].itertext())
    nav = next(r for r in docs.values() if r.find(f".//{H}nav[@{EP}='toc']") is not None)
    toc = nav.find(f".//{H}nav[@{EP}='toc']")
    texts = [" ".join("".join(a.itertext()).split()) for a in toc.iter(H + "a")]
    assert texts == ["Preface", "1 First chapter", "1.1 First section"]
    assert "reader-title" not in ET.tostring(toc, encoding="unicode")
    land = nav.find(f".//{H}nav[@{EP}='landmarks']")
    bodylink = next(a for a in land.iter(H + "a") if a.get(EP) == "bodymatter")
    assert bodylink.get("href").endswith("#body")
    for n, r in docs.items():
        for a in r.iter(H + "a"):
            href = a.get("href", "")
            if "#" not in href or "://" in href:
                continue
            member, fragment = urldefrag(unquote(href))
            member = (posixpath.normpath(posixpath.join(posixpath.dirname(n), member))
                      if member else n)
            assert (member, fragment) in ids


@pytest.mark.skipif(not shutil.which("latex"), reason="LaTeX required")
def test_pdf_helper_is_nonprinting_and_preserves_starred_chapter(tmp_path):
    entry = tmp_path / "main.tex"
    entry.write_text(
        BODY.replace(r"\label{reader-title}",
                     r"\label{reader-title}" + r"\epubunlisted{reader-title}"),
        encoding="utf-8",
    )
    # The installed TeX tool finds this repo package through an explicit local copy.
    shutil.copyfile(ROOT / "formats/latex/translator.sty", tmp_path / "translator.sty")
    result = run_hidden(
        ["latex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "main.dvi").is_file()
