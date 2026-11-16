from __future__ import annotations

import base64
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import translator

ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / "formats/pandoc/semantics.lua"
READER = "markdown+fenced_divs+bracketed_spans+footnotes-raw_html"
H = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc required")

SOURCE = '''# Chapter {#chapter}

Before.

:::: {#figure-one .native-figure .special data-origin="unit7"}

[![简短描述](sample.png)]{.source-image}

::: {#caption-one .figure-caption data-origin="original"}

第一段 *强调*。[]{#page-1 epub:type="pagebreak" title="1" role="doc-pagebreak"}

第二段 [链接](#chapter)。

:::

::::

![完整单段 *图注* 与 [链接](#chapter)[]{#page-2 epub:type="pagebreak" title="2" role="doc-pagebreak"}](sample.png){#figure-two alt="单段短描述"}

After.
'''


def read(source=SOURCE, target="json"):
    return translator.run([
        "pandoc", "--from=" + READER, "--to=" + target,
        "--lua-filter=" + str(FILTER),
    ], input_text=source).stdout


def nodes(value):
    if isinstance(value, dict):
        if "t" in value:
            yield value
        for part in value.values():
            yield from nodes(part)
    elif isinstance(value, list):
        for part in value:
            yield from nodes(part)


def figures_by_id(document):
    figures = [x for x in nodes(document) if x["t"] == "Figure"]
    return {x["c"][0][0]: x for x in figures}


def assert_accessible_figures(root, ns=""):
    figures = root.findall(".//" + ns + "figure")
    assert len(figures) == 2
    by_id = {x.get("id"): x for x in figures}
    one, two = by_id["figure-one"], by_id["figure-two"]
    assert one.get("data-origin") == "unit7"
    assert "special" in one.get("class", "").split()
    assert one.find(".//" + ns + "img").get("alt") == "简短描述"
    captions = one.findall(ns + "figcaption")
    assert len(captions) == 1
    cap = captions[0]
    assert all(e.get("aria-hidden") != "true" and e.get("hidden") is None for e in cap.iter())
    div = cap.find(ns + "div")
    assert div.get("id") == "caption-one" and div.get("data-origin") == "original"
    assert "figure-caption" in div.get("class", "").split()
    assert len(div.findall(".//" + ns + "p")) == 2
    assert "第一段" in "".join(div.itertext()) and "第二段" in "".join(div.itertext())
    assert div.find(".//" + ns + "em").text == "强调"
    assert div.find(".//" + ns + "a").get("href").endswith("#chapter")
    page = next(x for x in div.iter(ns + "span") if x.get("id") == "page-1")
    assert page.get("title") == "1" and page.get("role") == "doc-pagebreak"
    assert page.get(EPUB_TYPE) == "pagebreak"
    assert two.find(".//" + ns + "img").get("alt") == "单段短描述"
    cap2 = two.find(ns + "figcaption")
    assert cap2 is not None and cap2.get("aria-hidden") != "true"
    assert "完整单段" in "".join(cap2.itertext())
    assert cap2.find(".//" + ns + "em").text == "图注"
    assert cap2.find(".//" + ns + "a").get("href").endswith("#chapter")
    point2 = next(x for x in cap2.iter(ns + "span") if x.get("id") == "page-2")
    assert point2.get(EPUB_TYPE) == "pagebreak" and point2.get("title") == "2"
    assert "".join(root.itertext()).count("第一段") == 1
    assert "".join(root.itertext()).count("第二段") == 1


def test_real_reader_ast_and_html_preserve_caption_and_independent_alt():
    document = json.loads(read())
    figs = figures_by_id(document)
    assert len(figs) == 2 and set(figs) == {"figure-one", "figure-two"}
    one = figs["figure-one"]["c"]
    assert one[0] == ["figure-one", ["native-figure", "special"], [["data-origin", "unit7"]]]
    caption = one[1][1]
    assert len(caption) == 1 and caption[0]["t"] == "Div"
    assert caption[0]["c"][0][0] == "caption-one"
    assert [p["t"] for p in caption[0]["c"][1]] == ["Para", "Para"]
    assert len([x for x in nodes(one[2]) if x["t"] == "Image"]) == 1
    html = read(target="html5")
    root = ET.fromstring('<html xmlns:epub="http://www.idpf.org/2007/ops"><body>' + html + '</body></html>')
    assert_accessible_figures(root)


def test_epub_pipeline_keeps_native_figure_and_caption_accessible(tmp_path):
    entry = tmp_path / "main.md"
    entry.write_text(SOURCE, encoding="utf-8")
    (tmp_path / "sample.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    output = tmp_path / "fixture.epub"
    translator.build_svg_math_epub(
        root=ROOT, work_path=tmp_path, entry=entry,
        source_command=["pandoc", str(entry), "--from=" + READER,
                        "--lua-filter=" + str(FILTER), "--resource-path=" + str(tmp_path)],
        writer_options=["--metadata=title:Native figure", "--metadata=lang:en",
                        "--toc", "--toc-depth=2", "--epub-title-page=false"],
        source_cwd=ROOT, output=output, toc_depth=2,
    )
    with zipfile.ZipFile(output) as archive:
        docs = [ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith(".xhtml")]
    doc = next(d for d in docs if any(e.get("id") == "figure-one" for e in d.iter()))
    assert_accessible_figures(doc, H)


@pytest.mark.parametrize("body,message", [
    ("[![Alt](sample.png)]{.source-image}", "exactly one figure-caption"),
    (("[![Alt](sample.png)]{.source-image}\n\n::: {.figure-caption}\n\nOne\n\n:::"
      "\n\n::: {.figure-caption}\n\nTwo\n\n:::"), "exactly one figure-caption"),
    ("[![Alt](sample.png)]{.source-image}\n\n::: {.figure-caption}\n\n:::", "must not be empty"),
    ("[![Alt](sample.png)]{.source-image}\n\n::: {.figure-caption}\n\n[]{#page}\n\n:::", "must not be empty"),
    (("[![Alt](sample.png)]{.source-image}\n\n::: {.figure-caption}\n\nCaption\n\n:::"
      "\n\nMore body"), "must be the final block"),
    ("![Nested](sample.png)\n\n::: {.figure-caption}\n\nCaption\n\n:::", "nested Figure"),
    (("[![Alt](sample.png)]{.source-image}\n\n::: {.figure-caption}\n\n"
      "![Nested](sample.png)\n\nCaption\n\n:::"), "nested Figure"),
    ("Body without image\n\n::: {.figure-caption}\n\nCaption\n\n:::", "body requires an image"),
])
def test_invalid_native_figure_fails_without_dropping_content(body, message):
    source = ":::: {.native-figure}\n\n" + body + "\n\n::::\n"
    with pytest.raises(translator.CliError, match=message):
        read(source)


def test_ordinary_div_legacy_figure_and_source_unit_remain_unchanged():
    source = ('::: {#ordinary .custom data-origin="old"}\n\nText.\n\n:::\n\n'
              '![旧图注](sample.png){#old-figure}\n\n'
              '::: {.source-unit data-unit="2"}\n\n# Part {#part}\n\n:::\n')
    document = json.loads(read(source))
    divs = [x for x in document["blocks"] if x["t"] == "Div"]
    assert divs[0]["c"][0] == ["ordinary", ["custom"], [["data-origin", "old"]]]
    assert divs[1]["c"][0] == ["", ["source-unit"], [["data-unit", "2"]]]
    assert set(figures_by_id(document)) == {"old-figure"}
    html = read(source, "html5")
    assert '<div id="ordinary" class="custom" data-origin="old">' in html
    assert '<figure id="old-figure">' in html and '<figcaption aria-hidden="true">旧图注</figcaption>' in html
    assert '<h1 id="part">Part</h1>' in html and 'data-unit' not in html


def test_native_figure_cannot_bypass_source_unit_metadata_rules():
    source = (':::: {.native-figure .source-unit data-unit="1"}\n\n'
              '[![Alt](sample.png)]{.source-image}\n\n'
              '::: {.figure-caption}\n\nCaption\n\n:::\n\n::::\n')
    with pytest.raises(translator.CliError, match="source-unit Div is metadata only"):
        read(source)


@pytest.mark.parametrize("body", [
    (':::: {.body-wrapper}\n\n[![Alt](sample.png)]{.source-image}\n\n'
     '::: {.figure-caption}\n\nHidden duplicate\n\n:::\n\n::::\n\n'
     '::: {.figure-caption}\n\nFinal caption\n\n:::'),
    ('[![Alt](sample.png)]{.source-image}\n\n'
     ':::: {.figure-caption}\n\nOuter caption\n\n'
     '::: {.figure-caption}\n\nNested duplicate\n\n:::\n\n::::'),
])
def test_native_figure_rejects_figure_caption_anywhere_but_unique_direct_tail(body):
    source = "::::: {.native-figure}\n\n" + body + "\n\n:::::\n"
    with pytest.raises(translator.CliError, match="exactly one figure-caption"):
        read(source)
