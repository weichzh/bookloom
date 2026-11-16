from __future__ import annotations

import base64
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import epub_semantics as semantics
from tools import translator
from tools.epub_page_navigation import NativePageLocation, PageNavigationConfig

H = "{http://www.w3.org/1999/xhtml}"
T = "{http://www.idpf.org/2007/ops}type"
ROOT = Path(__file__).resolve().parents[1]


def source():
    return {"blocks": [{"t": "Div", "c": [["wrapper", [], []], [
        {"t": "Header", "c": [1, ["_chapter", [], []], [{"t": "Str", "c": "Alpha"}]]}
    ]]}]}


def validate(prefix="", *, leading="", heading=None, document=None):
    heading = heading or '<h1 id="id__chapter">Alpha</h1>'
    root = ET.fromstring(f'<html xmlns:epub="{T[1:-5]}"><body>'
                         f'<section id="wrapper">{leading}{prefix}{heading}<p>Body.</p></section>'
                         '</body></html>')
    toc = ET.fromstring('<nav><ol><li><a href="body.xhtml#wrapper">Alpha</a></li></ol></nav>')
    return semantics.validate_outline(document or source(), toc, nav_path="nav.xhtml",
                                      documents={"body.xhtml": root}, spine_paths=["body.xhtml"],
                                      epub_identifiers=True)


@pytest.mark.parametrize("prefix", [
    '',
    '<span id="p1" epub:type="pagebreak" role="doc-pagebreak" title="1"/>',
    ('<span id="p1" epub:type="pagebreak" title="1"/>\n  '
     '<span id="p2" epub:type="pagebreak" role="doc-pagebreak" title="2"/>\n'),
])
def test_first_source_heading_alias_allows_only_empty_standard_pagebreak_prefix(prefix):
    assert validate(prefix)[0].identifier == 'wrapper'


@pytest.mark.parametrize("prefix,leading", [
    ('<span id="ordinary"/>', ''),
    ('<p/>', ''),
    ('<span epub:type="pagebreak">text</span>', ''),
    ('<span epub:type="pagebreak"><i/></span>', ''),
    ('<span epub:type="pagebreak"/>visible tail', ''),
    ('<span epub:type="pagebreak" role="doc-noteref"/>', ''),
    ('<h2 id="other">Other</h2>', ''),
    ('<span epub:type="pagebreak"/>', 'Visible lead'),
])
def test_alias_never_skips_visible_text_or_other_nodes(prefix, leading):
    with pytest.raises(semantics.EpubSemanticError):
        validate(prefix, leading=leading)


def test_pagebreak_prefix_does_not_hide_wrong_heading_level_or_text():
    prefix = '<span epub:type="pagebreak"/>'
    for heading in ('<h2 id="id__chapter">Alpha</h2>', '<h1 id="id__chapter">Wrong</h1>'):
        with pytest.raises(semantics.EpubSemanticError, match='真实目标标题'):
            validate(prefix, heading=heading)


def test_pagebreak_alias_still_validates_original_heading_noteref():
    document = source()
    document['blocks'][0]['c'][1][0]['c'][2].append({
        't': 'Link', 'c': [['ref56', [], [['epub:type', 'noteref']]],
                          [{'t': 'Superscript', 'c': [{'t': 'Str', 'c': '56'}]}], ['#note56', '']]
    })
    heading = ('<h1 id="id__chapter">Alpha<a id="ref56" href="#note56" '
               'epub:type="noteref"><sup>57</sup></a></h1>')
    with pytest.raises(semantics.EpubSemanticError, match='正文标题注引用'):
        validate('<span epub:type="pagebreak"/>', heading=heading, document=document)


@pytest.mark.skipif(shutil.which('pandoc') is None, reason='Pandoc required')
def test_real_writer_retains_native_caption_and_legacy_heading_alias(tmp_path):
    work = tmp_path / 'Works' / 'caption-alias'
    assets = work / 'assets'
    assets.mkdir(parents=True)
    (assets / 'one.png').write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/Y4AAAAASUVORK5CYII='))
    entry = work / 'main.md'
    entry.write_text('::: {#wrapper}\n\n# Alpha {#_chapter}\n\n'
                     '[]{#page_ix epub:type="pagebreak" role="doc-pagebreak" title="ix"}Body.\n\n'
                     '![*Figure 1.1 Example*](assets/one.png){#fig-example}\n\n'
                     '![Uncaptioned drawing](assets/one.png)[]{.image-tail}\n\n:::\n', encoding='utf-8')
    output = work / 'book.epub'
    translator.build_svg_math_epub(
        root=ROOT, work_path=work, entry=entry,
        source_command=['pandoc', str(entry), '--from=markdown+fenced_divs+bracketed_spans+footnotes-raw_html',
                        '--lua-filter='+str(ROOT/'formats/pandoc/semantics.lua'), '--resource-path='+str(work)],
        writer_options=['--toc', '--toc-depth=1', '--split-level=1', '--epub-title-page=false',
                        '--metadata=title:Caption Test'], source_cwd=ROOT, output=output,
        toc_depth=1, page_navigation=PageNavigationConfig(
            'urn:test:source', 'Original test', 'en',
            (NativePageLocation('page_ix', 'ix', 'id__chapter'),), mode='native-pagebreaks'))
    with zipfile.ZipFile(output) as archive:
        docs = [ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith('.xhtml')]
    figures = [f for d in docs for f in d.iter(H+'figure')]
    assert len(figures) == 1
    caption = figures[0].find(H+'figcaption')
    assert caption is not None and ''.join(caption.itertext()) == 'Figure 1.1 Example'
    assert caption.find(H+'em') is not None
    assert figures[0].find(H+'img').get('alt') == 'Figure 1.1 Example'
    assert sum(1 for d in docs for _ in d.iter(H+'img')) == 2
    assert not any(translator.epub_structure_problems(d) for d in docs)
    container = next(e for d in docs for e in d.iter() if e.get('id')=='wrapper')
    assert [e.tag.rsplit('}', 1)[-1] for e in list(container)[:2]] == ['span', 'h1']
    assert container[0].get(T) == 'pagebreak'
