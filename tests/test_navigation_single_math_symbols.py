from __future__ import annotations

import base64
import copy
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import epub_semantics as sem
from tools import translator


def fixture(tex, visible):
    document = {"blocks": [{"t": "Header", "c": [
        1, ["symbol", [], []],
        [{"t": "Math", "c": [{"t": "InlineMath"}, tex]},
         {"t": "Str", "c": " title"}],
    ]}]}
    nav = ET.fromstring(
        '<nav><ol><li><a href="body.xhtml#symbol"/></li></ol></nav>'
    )
    nav.find(".//a").text = visible + " title"
    body = ET.fromstring('<section id="symbol"><h1><img class="math-inline"/> title</h1></section>')
    next(body.iter("img")).set("alt", tex)
    return document, nav, body


@pytest.mark.parametrize("tex,visible", [(r"\infty", "∞"), (r"\varepsilon", "ε")])
def test_single_symbol_navigation_is_readable_but_body_math_stays_exact(tex, visible):
    document, nav, body = fixture(tex, visible)

    def check(n, b):
        return sem.validate_outline(
            document, n, nav_path="nav.xhtml", documents={"body.xhtml": b},
            navigation_math_labels=True,
        )

    assert sem.navigation_formula_text(tex) == visible
    assert sem.outline_from_ast(document)[0].label == tex + " title"
    check(nav, body)
    wrong_nav = copy.deepcopy(nav)
    wrong_nav.find(".//a").text = "wrong title"
    with pytest.raises(sem.EpubSemanticError, match="label"):
        check(wrong_nav, body)
    wrong_body = copy.deepcopy(body)
    next(wrong_body.iter("img")).set("alt", visible)
    with pytest.raises(sem.EpubSemanticError, match="真实目标标题|正文标题公式"):
        check(nav, wrong_body)
    plain_body = ET.fromstring(
        '<section id="symbol"><h1/></section>'
    )
    plain_body.find("h1").text = tex + " title"
    with pytest.raises(sem.EpubSemanticError, match="公式"):
        check(nav, plain_body)


@pytest.mark.parametrize("tex", [r"\epsilon", r"\infty+1", r"\varepsilon_1", r"\inftyfoo"])
def test_single_symbol_extension_does_not_enable_other_macros_or_expressions(tex):
    with pytest.raises(sem.EpubSemanticError, match="无法安全转为文本"):
        sem.navigation_formula_text(tex)


@pytest.mark.skipif(
    not all(shutil.which(t) for t in ("pandoc", "uv", "latex", "dvisvgm")),
    reason="Pandoc and SVG math tools required",
)
def test_real_latex_epub_toc_ncx_and_original_math_objects(tmp_path):
    source = tmp_path / "main.tex"
    source.write_text(r"""
\documentclass{book}
\begin{document}
\chapter*{$\infty$ End}\label{infinity}
End paragraph.
\chapter*{Epilogue $\varepsilon$}\label{epsilon}
Epilogue paragraph.
\end{document}
""", encoding="utf-8")
    cover = tmp_path / "cover.png"
    cover.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    output = tmp_path / "book.epub"
    translator.build_latex_epub_source(
        root=Path(translator.__file__).resolve().parents[1],
        work_path=tmp_path, entry=source, output=output,
        title="Not a title root", author=None, language="en",
        identifier="urn:test:single-math-symbols", cover=cover,
        structure_options=["--epub-title-page=false", "--toc-depth=1"],
        toc_depth=1,
    )
    with zipfile.ZipFile(output) as z:
        docs = [ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith(".xhtml")]
        ncx = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".ncx"))))
    tag = "{http://www.idpf.org/2007/ops}type"
    toc = next(e for d in docs for e in d.iter()
               if e.tag.rsplit("}", 1)[-1] == "nav" and e.get(tag) == "toc")
    assert [e.label for e in sem.outline_from_nav(toc)] == ["∞ End", "Epilogue ε"]
    assert [" ".join("".join(e.itertext()).split()) for e in ncx.iter()
            if e.tag.rsplit("}", 1)[-1] == "navLabel"] == ["∞ End", "Epilogue ε"]
    headings = [e for d in docs for e in d.iter()
                if e.tag.rsplit("}", 1)[-1] == "h1"]
    maths = [e.get("alt") for h in headings for e in h.iter()
             if e.tag.rsplit("}", 1)[-1] == "img"]
    assert maths == [r"\infty", r"\varepsilon"]
