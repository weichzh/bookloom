from __future__ import annotations

import copy
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import pytest

from tools import epub_semantics as sem
from tools import translator

XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"


def math(text):
    return {"t": "Math", "c": [{"t": "InlineMath"}, text]}


def source():
    return {"blocks": [
        {"t": "Header", "c": [1, ["part", [], []], [{"t": "Str", "c": "Part"}]]},
        {"t": "Header", "c": [2, ["chapter", [], []], [
            {"t": "Str", "c": "4.5"}, {"t": "Space"}, math(r"\mathcal L_A"),
            {"t": "Str", "c": "-Rule"}, {"t": "Space"}, math(r"T_{\mathrm{BA}}^d"),
            {"t": "Space"}, math(r"K^\star=0"), {"t": "Space"}, math(r"\Pi_2"),
            {"t": "Space"}, math("x_1=2"),
        ]]},
    ]}


READABLE = "4.5 L_A-Rule T_BA^d K*=0 Π₂ x_1=2"
RAW = r"4.5 \mathcal L_A-Rule T_{\mathrm{BA}}^d K^\star=0 \Pi_2 x_1=2"


def toc(label=READABLE):
    root = ET.fromstring('<nav><ol><li><a href="body.xhtml#part">Part</a><ol>'
                         '<li><a href="body.xhtml#chapter"/></li></ol></li></ol></nav>')
    root[0][0][1][0][0].text = label
    return root


def body():
    root = ET.fromstring('<html><body><section id="part"><h1>Part</h1>'
                         '<section id="chapter"><h2>4.5 </h2></section>'
                         '</section></body></html>')
    heading = next(root.iter("h2"))
    for formula, tail in [
        (r"\mathcal L_A", "-Rule "), (r"T_{\mathrm{BA}}^d", " "),
        (r"K^\star=0", " "), (r"\Pi_2", " "), ("x_1=2", ""),
    ]:
        img = ET.SubElement(heading, "img", {"class": "math-inline", "alt": formula})
        img.tail = tail
    return root


def check(document, navigation, actual_body):
    return sem.validate_outline(document, navigation, nav_path="nav.xhtml",
                                documents={"body.xhtml": actual_body}, spine_paths=["body.xhtml"],
                                navigation_math_labels=True)


def test_distinct_navigation_and_body_representations_and_legacy_default():
    document, actual_body = source(), body()
    assert sem.outline_from_ast(document)[1].label == RAW
    assert sem.outline_from_ast(document, navigation_math_labels=True)[1].label == READABLE
    sem.validate_outline(document, toc(RAW), nav_path="nav.xhtml", documents={"body.xhtml": actual_body})
    check(document, toc(), actual_body)
    with pytest.raises(sem.EpubSemanticError, match="label"):
        check(document, toc(RAW), actual_body)
    with pytest.raises(sem.EpubSemanticError, match="label"):
        sem.validate_outline(document, toc(), nav_path="nav.xhtml", documents={"body.xhtml": actual_body})


@pytest.mark.parametrize("changed", [
    "4.5 L_B-Rule T_BA^d K*=0 Π₂ x_1=2",       # subscript letter
    "4.5 L_A-Rule T_BA^e K*=0 Π₂ x_1=2",       # superscript
    "4.5 L_A-Rule T_BA^d K*≠0 Π₂ x_1=2",       # operator
    "4.5 L_A-Rule T_BA^d K*=0 Π₁ x_1=2",       # unicode subscript number
    "4.5 L_A-Rule T_BA^d K*=0 Π₂ x_1=3",       # plain digit
])
def test_wrong_navigation_number_operator_or_script_is_rejected(changed):
    with pytest.raises(sem.EpubSemanticError, match="label"):
        check(source(), toc(changed), body())


@pytest.mark.parametrize("replacement", [r"\mathit L_A", r"\mathcal {L_A}", "L_A", "", None])
def test_body_preserves_full_tex_font_grouping_and_alt(replacement):
    actual_body = body()
    image = next(actual_body.iter("img"))
    if replacement is None:
        image.attrib.pop("alt")
    else:
        image.set("alt", replacement)
    with pytest.raises(sem.EpubSemanticError, match="真实目标标题"):
        check(source(), toc(), actual_body)


@pytest.mark.parametrize("index,replacement", [
    (1, r"T_{\mathit{BA}}^d"), (1, r"T_{\mathrm{BA}}^e"),
    (2, r"K^\star\ne 0"), (2, r"K^\star=1"), (3, r"\Pi_1"), (4, "x_1=3"),
])
def test_body_preserves_formula_operator_number_and_script(index, replacement):
    actual_body = body()
    list(actual_body.iter("img"))[index].set("alt", replacement)
    with pytest.raises(sem.EpubSemanticError, match="真实目标标题"):
        check(source(), toc(), actual_body)


def test_source_changed_with_navigation_does_not_hide_wrong_body():
    document = source()
    document["blocks"][1]["c"][2][2]["c"][1] = r"\mathcal Z_0"
    with pytest.raises(sem.EpubSemanticError, match="真实目标标题"):
        check(document, toc(READABLE.replace("L_A", "Z_0")), body())


@pytest.mark.parametrize("mutation", ["id", "depth", "parent", "level", "target", "spine"])
def test_id_hierarchy_and_real_target_are_still_required(mutation):
    document, navigation, actual_body = source(), toc(), body()
    anchor = navigation[0][0][1][0][0]
    if mutation == "id":
        anchor.set("href", "body.xhtml#wrong")
    elif mutation == "depth":
        child = navigation[0][0][1][0]
        navigation[0][0].remove(navigation[0][0][1])
        navigation[0].append(child)
    elif mutation == "parent":
        document["blocks"].insert(1, {"t": "Header", "c": [1, ["other", [], []], [{"t": "Str", "c": "Other"}]]})
        navigation[0].insert(1, ET.fromstring('<li><a href="body.xhtml#other">Other</a></li>'))
    elif mutation == "level":
        next(actual_body.iter("h2")).tag = "h3"
    elif mutation == "target":
        next(actual_body.iter("section")).find("section").tag = "p"
    else:
        parent = next(actual_body.iter("body"))
        part = parent[0]
        chapter = part.find("section")
        part.remove(chapter)
        parent.insert(0, chapter)
    with pytest.raises(sem.EpubSemanticError):
        check(document, navigation, actual_body)


def test_unknown_backslash_rejected_but_nonmath_nodes_stay_literal():
    document = source()
    document["blocks"][1]["c"][2][2] = math(r"\alpha")
    assert r"\alpha" in sem.outline_from_ast(document)[1].label
    with pytest.raises(sem.EpubSemanticError, match="导航公式无法安全转为文本"):
        sem.outline_from_ast(document, navigation_math_labels=True)
    with pytest.raises(translator.CliError, match="导航公式无法安全转为文本"):
        translator.replace_navigation_formula_images(ET.fromstring('<nav><img class="math-inline" alt="\\alpha"/></nav>'))
    inlines = [
        {"t": "Code", "c": [["", [], []], r"\alpha"]}, {"t": "Space"},
        {"t": "Image", "c": [["", [], []], [{"t": "Str", "c": r"\mathcal L_A"}], ["normal.png", ""]]},
        {"t": "Space"}, {"t": "Superscript", "c": [{"t": "Str", "c": "23"}]},
        {"t": "Subscript", "c": [{"t": "Str", "c": "45"}]},
    ]
    assert sem.inline_text(inlines, navigation_math_labels=True) == r"\alpha \mathcal L_A 2345"
    image = ET.fromstring('<nav><img alt="\\alpha"/></nav>')
    assert translator.replace_navigation_formula_images(image) == 0
    assert next(image.iter("img")).get("alt") == r"\alpha"


def test_native_heading_image_alt_and_formula_image_mapping_remain_distinct():
    document = {"blocks": [{"t": "Header", "c": [1, ["image", [], []], [
        {"t": "Image", "c": [["", [], []], [{"t": "Str", "c": "ordinary χ"}], ["a.png", ""]]},
        {"t": "Space"},
        {"t": "Image", "c": [["", ["math-inline"], []], [{"t": "Str", "c": r"\mathcal L_A"}], ["b.png", ""]]},
    ]]}]}
    nav = ET.fromstring('<nav><ol><li><a href="body.xhtml#image">ordinary χ L_A</a></li></ol></nav>')
    actual_body = ET.fromstring('<section id="image"><h1><img alt="ordinary χ"/> '
                                '<img class="math-inline" alt="\\mathcal L_A"/></h1></section>')
    check(document, nav, actual_body)
    next(actual_body.iter("img")).set("alt", "")
    with pytest.raises(sem.EpubSemanticError, match="真实目标标题"):
        check(document, nav, actual_body)


def test_combined_note_and_math_normalization_is_source_proven_and_ncx_tracks_nav():
    document = source()
    document["blocks"][1]["c"][2].extend([
        {"t": "Space"}, {"t": "Link", "c": [["ref56", [], [["epub:type", "noteref"]]],
         [{"t": "Superscript", "c": [{"t": "Str", "c": "56"}]}], ["#note56", ""]]},
    ])
    nav = toc(READABLE + " 56")
    ncx = ET.fromstring('<ncx><navMap><navPoint><navLabel><text/></navLabel>'
                        '<content src="body.xhtml#chapter"/></navPoint></navMap></ncx>')
    ncx.find('.//text').text = RAW + " 56"
    assert sem.normalize_noteref_labels(document, nav, navigation_math_labels=True) == 1
    assert sem.normalize_noteref_labels(document, ncx, ncx=True, navigation_math_labels=True) == 1
    assert sem.outline_from_nav(nav)[1].label == ncx.find('.//text').text == READABLE
    assert sem.normalize_noteref_labels(document, nav, navigation_math_labels=True) == 0
    assert sem.normalize_noteref_labels(document, ncx, ncx=True, navigation_math_labels=True) == 0
    assert ncx.find('.//content').get('src') == "body.xhtml#chapter"
    for candidate, is_ncx in [(toc(READABLE.replace("K*=0", "K*≠0") + " 56"), False), (copy.deepcopy(ncx), True)]:
        if is_ncx:
            candidate.find('.//text').text = RAW.replace("=0", "=1") + " 56"
        before = ET.tostring(candidate)
        with pytest.raises(sem.EpubSemanticError, match="原标题不符"):
            sem.normalize_noteref_labels(document, candidate, ncx=is_ncx, navigation_math_labels=True)
        assert ET.tostring(candidate) == before


def test_ncx_formula_without_note_is_source_checked_and_never_becomes_a_second_catalog():
    document = source()
    ncx = ET.fromstring('<ncx><navMap><navPoint><navLabel><text/></navLabel>'
                        '<content src="body.xhtml#chapter"/></navPoint></navMap></ncx>')
    ncx.find('.//text').text = RAW
    assert sem.normalize_noteref_labels(document, ncx, ncx=True, navigation_math_labels=True) == 1
    assert ncx.find('.//text').text == READABLE
    ncx.find('.//text').text = RAW.replace('=0', '=1')
    with pytest.raises(sem.EpubSemanticError, match='原标题不符'):
        sem.normalize_noteref_labels(document, ncx, ncx=True, navigation_math_labels=True)


def test_numbered_math_note_in_the_middle_keeps_prefix_and_following_text():
    document = source()
    document['blocks'][1]['c'][2].insert(3, {
        't': 'Link', 'c': [['ref56', [], [['epub:type', 'noteref']]],
                           [{'t': 'Str', 'c': '56'}], ['#note56', '']]
    })
    nav = toc('1.1 ' + READABLE.replace('L_A-Rule', 'L_A56-Rule'))
    nav[0][0][0].text = '1 Part'
    ncx = ET.fromstring('<ncx><navMap><navPoint><navLabel><text/></navLabel>'
                        '<content src="body.xhtml#chapter"/></navPoint></navMap></ncx>')
    ncx.find('.//text').text = '1.1 ' + RAW.replace('L_A-Rule', 'L_A56-Rule')
    assert sem.normalize_noteref_labels(document, nav, number_sections=True, navigation_math_labels=True) == 1
    assert sem.normalize_noteref_labels(document, ncx, number_sections=True, ncx=True, navigation_math_labels=True) == 1
    assert sem.outline_from_nav(nav)[1].label == ncx.find('.//text').text == '1.1 ' + READABLE


def test_math_note_cleanup_still_protects_a_native_pagepoint():
    document = source()
    document['blocks'][1]['c'][2].append(
        {"t": "Link", "c": [["ref56", [], [["epub:type", "noteref"]]],
         [{"t": "Str", "c": "56"}], ["#note56", ""]]})
    nav = toc(READABLE + '56')
    ET.SubElement(nav[0][0][1][0][0], 'span', {f'{{{EPUB}}}type': 'pagebreak', 'id': 'p1'})
    before = ET.tostring(nav)
    with pytest.raises(sem.EpubSemanticError, match='不能删除'):
        sem.normalize_noteref_labels(document, nav, navigation_math_labels=True)
    assert ET.tostring(nav) == before


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc required")
def test_real_pandoc_math_and_note_roundtrip_through_controlled_pipeline(tmp_path, monkeypatch):
    text = ('# Part {#part}\n\n'
            r'## 3.7 $\mathcal L_A$-示例推演规则[^56^](#note56){#ref56 epub:type="noteref"} {#chapter}'
            '\n\nBody.\n\n## Notes {.unlisted #notes}\n\n'
            '::: {#note56 epub:type="footnote" role="doc-footnote"}\n'
            '56. Original note.\n\n[↩](#ref56){epub:type="backlink"}\n:::\n')
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    entry = tmp_path / "main.md"
    entry.write_text(text, encoding="utf-8")
    reader = subprocess.run(["pandoc", "-f", "markdown+fenced_divs+bracketed_spans+footnotes-raw_html", "-t", "json"],
                            input=text, capture_output=True, text=True, encoding="utf-8", check=True, creationflags=flags)
    document = json.loads(reader.stdout)
    writer_document = copy.deepcopy(document)
    svg = tmp_path / "formula.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1 1h8v8H1z"/></svg>', encoding="utf-8")
    for node in sem.ast_nodes(writer_document):
        if node["t"] == "Math":
            raw = node["c"][1]
            node.clear()
            node.update({"t": "Image", "c": [["", ["math-inline"], []], [{"t": "Str", "c": raw}], [str(svg), ""]]})
    raw_epub, output = tmp_path / "raw.epub", tmp_path / "normalized.epub"
    subprocess.run(["pandoc", "-f", "json", "-t", "epub3", "--toc", "--toc-depth=3",
                    "--split-level=1", "--epub-title-page=false", "--metadata=title:Fixture", "--metadata=lang:en",
                    "--metadata=identifier:urn:translator:fixture:en", "-o", str(raw_epub)],
                   input=json.dumps(writer_document, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8",
                   check=True, creationflags=flags)
    translator.normalize_epub_navigation(raw_epub, output, source_document=document, toc_depth=3,
                                         navigation_math_labels=True)
    with zipfile.ZipFile(output) as z:
        docs = {n: ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith('.xhtml')}
        ncx = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.ncx'))))
    nav = next(n for d in docs.values() for n in d.iter() if n.tag.rsplit('}', 1)[-1] == 'nav'
               and 'toc' in n.get(f'{{{EPUB}}}type', '').split())
    assert [e.label for e in sem.outline_from_nav(nav)] == ['Part', '3.7 L_A-示例推演规则']
    assert '3.7 L_A-示例推演规则' in ''.join(ncx.itertext())
    heading = next(n for d in docs.values() for n in d.iter() if n.tag.rsplit('}', 1)[-1] == 'h2'
                   and '示例推演规则' in ''.join(n.itertext()))
    image = next(n for n in heading.iter() if n.tag.rsplit('}', 1)[-1] == 'img')
    ref = next(n for n in heading.iter() if n.get(f'{{{EPUB}}}type') == 'noteref')
    assert image.get('alt') == r'\mathcal L_A'
    assert ref.get('id') == 'ref56' and ''.join(ref.itertext()) == '56'

    # Stop immediately after the real QA outline seam, before unrelated SVG,
    # cover and EPUBCheck checks; this tests that QA opts in explicitly.
    class OutlineReached(Exception):
        pass

    language = translator.Language('en', 'source', entry, None, {'epub': output})
    work = translator.Work(translator.REPO_ROOT, tmp_path, {'epub': {'title_page': False, 'toc_depth': 3}},
                           'fixture', 'Fixture', 'markdown', None, tmp_path / 'source.pdf', 1, (language,))
    validate = translator.validate_markdown_epub_outline
    def capture_qa(*args, **kwargs):
        assert kwargs['navigation_math_labels'] is True
        validate(*args, **kwargs)
        raise OutlineReached
    monkeypatch.setattr(translator, 'validate_markdown_epub_outline', capture_qa)
    with pytest.raises(OutlineReached):
        translator.qa_epub(work, language, output, tmp_path)

    nav_path = next(name for name, d in docs.items() if nav in list(d.iter()))
    spine = [name for name in docs if '/text/' in name and any(n.tag.rsplit('}', 1)[-1] == 'body' for n in docs[name].iter())]
    # The direct legacy QA helper keeps its raw-label default; opting in uses
    # the readable nav but still checks the original body alternative.
    with pytest.raises(translator.CliError, match='label'):
        validate(work, language, nav, nav_path=nav_path, documents=docs, spine_paths=spine)
    validate(work, language, nav, nav_path=nav_path, documents=docs, spine_paths=spine, navigation_math_labels=True)
    image.set('alt', r'\mathit L_A')
    with pytest.raises(translator.CliError, match='真实目标标题'):
        validate(work, language, nav, nav_path=nav_path, documents=docs, spine_paths=spine, navigation_math_labels=True)

    # The final QA seam must reject a later NCX drift as well as build-time drift.
    monkeypatch.setattr(translator, 'validate_markdown_epub_outline', validate)
    corrupt = tmp_path / 'wrong-ncx.epub'
    with zipfile.ZipFile(output) as original, zipfile.ZipFile(corrupt, 'w') as modified:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename.endswith('.ncx'):
                ncx = ET.fromstring(data)
                points = [node for node in ncx.iter() if node.tag.rsplit('}', 1)[-1] == 'navPoint']
                contents = [next(node for node in point if node.tag.rsplit('}', 1)[-1] == 'content')
                            for point in points]
                contents[1].set('src', contents[0].get('src'))
                data = ET.tostring(ncx, encoding='utf-8', xml_declaration=True)
            modified.writestr(info, data)
    with pytest.raises(translator.CliError, match='NCX 与 nav'):
        translator.qa_epub(work, language, corrupt, tmp_path)


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc required")
def test_controlled_build_explicitly_opts_in_without_math_prerequisites(tmp_path, monkeypatch):
    entry, output = tmp_path / 'plain.md', tmp_path / 'plain.epub'
    entry.write_text('# Plain {#plain}\n\nOrdinary paragraph.\n', encoding='utf-8')
    original = translator.normalize_epub_navigation
    modes = []
    def capture(*args, **kwargs):
        modes.append(kwargs.get('navigation_math_labels'))
        return original(*args, **kwargs)
    monkeypatch.setattr(translator, 'normalize_epub_navigation', capture)
    translator.build_svg_math_epub(
        root=translator.REPO_ROOT, work_path=tmp_path, entry=entry,
        source_command=['pandoc', str(entry), '--from=markdown'],
        writer_options=['--toc', '--toc-depth=3', '--split-level=1', '--epub-title-page=false',
                        '--metadata=title:Plain'], source_cwd=translator.REPO_ROOT,
        output=output, toc_depth=3,
    )
    assert modes == [True]
    assert output.is_file()


def formula_image(text):
    return {"t": "Image", "c": [["", ["math-inline"], []],
                              [{"t": "Str", "c": text}], ["formula.svg", ""]]}


@pytest.mark.parametrize("formula", [math, formula_image], ids=["source-math", "source-formula-image"])
def test_math_object_literal_cannot_be_completed_by_adjacent_text(formula):
    document = {"blocks": [{"t": "Header", "c": [1, ["h", [], []], [
        formula(r"\mathcal L_A"), {"t": "Str", "c": " suffix"},
    ]]}]}
    nav = ET.fromstring('<nav><ol><li><a href="body.xhtml#h">L_A suffix</a></li></ol></nav>')
    correct = ET.fromstring('<h1 id="h"><img class="math-inline" alt="\\mathcal L_A"/> suffix</h1>')
    check(document, nav, correct)
    compensated = ET.fromstring('<h1 id="h"><img class="math-inline" alt="\\mathcal L_"/>A suffix</h1>')
    assert sem.heading_text(compensated) == sem.heading_text(correct)
    with pytest.raises(sem.EpubSemanticError, match="正文标题公式"):
        check(document, nav, compensated)
    uncompensated = ET.fromstring('<h1 id="h"><img class="math-inline" alt="\\mathcal L_"/> suffix</h1>')
    with pytest.raises(sem.EpubSemanticError):
        check(document, nav, uncompensated)


@pytest.mark.parametrize("kind", [math, formula_image], ids=["source-math", "source-formula-image"])
@pytest.mark.parametrize("case", ["split", "merge", "shift", "rebalance"])
def test_math_object_boundaries_and_text_positions_are_preserved(kind, case):
    if case == "split":
        source_inlines = [kind("xy")]
        actual = '<img class="math-inline" alt="x"/><img class="math-inline" alt="y"/>'
    elif case == "merge":
        source_inlines = [kind("x"), kind("y")]
        actual = '<img class="math-inline" alt="xy"/>'
    elif case == "rebalance":
        source_inlines = [kind("xy"), kind("z")]
        actual = '<img class="math-inline" alt="x"/><img class="math-inline" alt="yz"/>'
    else:
        source_inlines = [kind("x"), {"t": "Str", "c": "x"}]
        actual = 'x<img class="math-inline" alt="x"/>'
    document = {"blocks": [{"t": "Header", "c": [1, ["h", [], []], source_inlines]}]}
    label = 'xx' if case == 'shift' else 'xyz' if case == 'rebalance' else 'xy'
    nav = ET.fromstring(f'<nav><ol><li><a href="body.xhtml#h">{label}</a></li></ol></nav>')
    heading = ET.fromstring(f'<h1 id="h">{actual}</h1>')
    with pytest.raises(sem.EpubSemanticError, match="正文标题公式"):
        check(document, nav, heading)


def test_continuous_formula_objects_with_note_and_ordinary_image_and_code():
    document = {"blocks": [{"t": "Header", "c": [1, ["h", [], []], [
        {"t": "Code", "c": [["", [], []], "prefix"]},
        math("x"), formula_image("y"),
        {"t": "Link", "c": [["ref", [], [["epub:type", "noteref"]]],
                            [{"t": "Str", "c": "2"}], ["#note", ""]]},
        {"t": "Image", "c": [["", [], []], [{"t": "Str", "c": " suffix"}], ["a.png", ""]]},
    ]]}]}
    nav = ET.fromstring('<nav><ol><li><a href="body.xhtml#h">prefixxy suffix</a></li></ol></nav>')
    heading = ET.fromstring('<h1 xmlns:epub="http://www.idpf.org/2007/ops" id="h">'
                            '<code>prefix</code><img class="math-inline" alt="x"/>'
                            '<img class="math-inline" alt="y"/>'
                            '<a epub:type="noteref" id="ref" href="#note">2</a>'
                            '<img alt=" suffix"/></h1>')
    check(document, nav, heading)
    images = list(heading.iter('img'))
    images[0].set('alt', 'xy')
    images[1].set('alt', '')
    with pytest.raises(sem.EpubSemanticError, match='正文标题公式'):
        check(document, nav, heading)


def test_numbered_nested_math_uses_visible_positions_and_original_noteref():
    document = {"blocks": [{"t": "Header", "c": [1, ["h", [], []], [
        {"t": "Quoted", "c": [{"t": "DoubleQuote"}, [
            {"t": "Span", "c": [["", ["cn-concept"], [["data-original", "term"]]], [
                {"t": "Emph", "c": [math("x")]},
            ]]},
        ]]},
        {"t": "Link", "c": [["ref", [], [["epub:type", "noteref"]]],
                            [{"t": "Str", "c": "2"}], ["#note", ""]]},
    ]]}]}
    nav = ET.fromstring('<nav><ol><li><a href="body.xhtml#h">1 “x（term）”</a></li></ol></nav>')
    heading = ET.fromstring('<h1 xmlns:epub="http://www.idpf.org/2007/ops" id="h">1 “'
                            '<em><img class="math-inline" alt="x"/></em>（term）”'
                            '<a epub:type="noteref" id="ref" href="#note">2</a></h1>')
    sem.validate_outline(document, nav, nav_path='nav.xhtml', documents={'body.xhtml': heading},
                         number_sections=True, navigation_math_labels=True)


def write_ncx_fixture(tmp_path, mutation=None, *, note=False, plain=False):
    document = source()
    label = 'Plain' if plain else READABLE
    raw_label = 'Plain' if plain else RAW
    nav = toc(label + ('56' if note else ''))
    for anchor in nav.iter('a'):
        anchor.set('href', 'text/' + anchor.get('href'))
    actual_body = body()
    if plain:
        document['blocks'][1]['c'][2] = [{"t": "Str", "c": "Plain"}]
        heading = next(actual_body.iter('h2'))
        for child in list(heading):
            heading.remove(child)
        heading.text = 'Plain'
    if note:
        document['blocks'][1]['c'][2].append(
            {"t": "Link", "c": [["ref56", [], [["epub:type", "noteref"]]],
             [{"t": "Str", "c": "56"}], ["#note56", ""]]})
        h2 = next(actual_body.iter('h2'))
        ET.SubElement(h2, 'a', {f'{{{EPUB}}}type': 'noteref', 'id': 'ref56', 'href': '#note56'}).text = '56'
        notes = ET.SubElement(actual_body.find('body'), 'aside', {f'{{{EPUB}}}type': 'footnote', 'id': 'note56'})
        ET.SubElement(notes, 'p').text = 'note'
    ncx = ET.fromstring('<ncx><navMap><navPoint id="p"><navLabel><text>Part</text></navLabel>'
                        '<content src="../text/body.xhtml#part"/><navPoint id="c">'
                        '<navLabel><text/></navLabel><content src="../text/body.xhtml#chapter"/>'
                        '</navPoint></navPoint></navMap></ncx>')
    parent = ncx.find('./navMap/navPoint')
    child = parent.find('./navPoint')
    child.find('./navLabel/text').text = raw_label + ('56' if note else '')
    if mutation == 'wrong-existing-target':
        child.find('./content').set('src', '../text/body.xhtml#part')
    elif mutation == 'missing-target':
        child.find('./content').attrib.pop('src')
    elif mutation == 'wrong-path':
        child.find('./content').set('src', 'text/body.xhtml#chapter')
    elif mutation == 'swap-targets':
        parent.find('./content').set('src', '../text/body.xhtml#chapter')
        parent.find('./navLabel/text').text = RAW
        child.find('./content').set('src', '../text/body.xhtml#part')
        child.find('./navLabel/text').text = 'Part'
    elif mutation == 'wrong-depth':
        parent.remove(child)
        ncx.find('./navMap').append(child)
    elif mutation == 'missing':
        parent.remove(child)
    elif mutation == 'extra':
        ncx.find('./navMap').append(copy.deepcopy(parent))
    elif mutation == 'wrong-label':
        parent.find('./navLabel/text').text = 'Wrong'
    elif mutation == 'reversed-siblings':
        document['blocks'][1]['c'][0] = 1
        next(actual_body.iter('h2')).tag = 'h1'
        nav[0][0].remove(nav[0][0][1])
        nav[0].append(ET.fromstring('<li><a href="text/body.xhtml#chapter"/></li>'))
        nav[0][1][0].text = READABLE
        parent.remove(child)
        ncx.find('./navMap').insert(0, child)
    container = b'<container><rootfiles><rootfile full-path="EPUB/content.opf"/></rootfiles></container>'
    opf = b'<package><manifest><item id="nav" href="nav.xhtml" properties="nav"/><item id="ncx" href="compat/toc.ncx" media-type="application/x-dtbncx+xml"/><item id="body" href="text/body.xhtml"/></manifest><spine><itemref idref="body"/></spine></package>'
    nav_document = ET.Element('html')
    nav.set(f'{{{EPUB}}}type', 'toc')
    nav_document.append(nav)
    source_zip, output = tmp_path / 'raw.epub', tmp_path / 'normalized.epub'
    with zipfile.ZipFile(source_zip, 'w') as z:
        for name, data in [('META-INF/container.xml', container), ('EPUB/content.opf', opf),
                           ('EPUB/nav.xhtml', ET.tostring(nav_document)),
                           ('EPUB/text/body.xhtml', ET.tostring(actual_body)),
                           ('EPUB/compat/toc.ncx', ET.tostring(ncx))]:
            z.writestr(name, data)
    return document, source_zip, output


@pytest.mark.parametrize('note', [False, True], ids=['plain', 'noteref'])
def test_controlled_ncx_raw_math_is_normalized_to_actual_nav(tmp_path, note):
    document, source_zip, output = write_ncx_fixture(tmp_path, note=note)
    translator.normalize_epub_navigation(source_zip, output, source_document=document, toc_depth=3,
                                         navigation_math_labels=True)
    with zipfile.ZipFile(output) as z:
        ncx = ET.fromstring(z.read('EPUB/compat/toc.ncx'))
    assert [x.text for x in ncx.findall('.//navLabel/text')] == ['Part', READABLE]


@pytest.mark.parametrize('note', [False, True], ids=['plain', 'noteref'])
def test_controlled_nonmath_ncx_matches_actual_nav(tmp_path, note):
    document, source_zip, output = write_ncx_fixture(tmp_path, note=note, plain=True)
    translator.normalize_epub_navigation(source_zip, output, source_document=document, toc_depth=3,
                                         navigation_math_labels=True)
    with zipfile.ZipFile(output) as z:
        ncx = ET.fromstring(z.read('EPUB/compat/toc.ncx'))
    assert [x.text for x in ncx.findall('.//navLabel/text')] == ['Part', 'Plain']


def test_legacy_default_does_not_require_ncx_to_match_nav(tmp_path):
    document, source_zip, output = write_ncx_fixture(tmp_path, 'wrong-existing-target', plain=True)
    translator.normalize_epub_navigation(source_zip, output, source_document=document, toc_depth=3)
    assert output.is_file()


def test_controlled_ncx_requires_unique_manifest_nav(tmp_path):
    document, source_zip, output = write_ncx_fixture(tmp_path)
    with zipfile.ZipFile(source_zip) as z:
        payloads = {item.filename: z.read(item.filename) for item in z.infolist()}
    opf = ET.fromstring(payloads['EPUB/content.opf'])
    ET.SubElement(opf.find('./manifest'), 'item', {'id': 'nav2', 'href': 'nav2.xhtml', 'properties': 'nav'})
    payloads['EPUB/content.opf'] = ET.tostring(opf)
    duplicate = tmp_path / 'duplicate-nav.epub'
    with zipfile.ZipFile(duplicate, 'w') as z:
        for name, data in payloads.items():
            z.writestr(name, data)
    with pytest.raises(translator.CliError, match='只有一个 nav'):
        translator.normalize_epub_navigation(duplicate, output, source_document=document, toc_depth=3,
                                             navigation_math_labels=True)


def test_controlled_declared_ncx_must_exist_but_epub3_ncx_remains_optional(tmp_path):
    document, source_zip, output = write_ncx_fixture(tmp_path, plain=True)
    with zipfile.ZipFile(source_zip) as z:
        payloads = {item.filename: z.read(item.filename) for item in z.infolist()}
    del payloads['EPUB/compat/toc.ncx']
    missing = tmp_path / 'missing.epub'
    with zipfile.ZipFile(missing, 'w') as z:
        for name, data in payloads.items():
            z.writestr(name, data)
    with pytest.raises(translator.CliError, match='声明的 NCX 缺失'):
        translator.normalize_epub_navigation(missing, output, source_document=document, toc_depth=3,
                                             navigation_math_labels=True)
    opf = ET.fromstring(payloads['EPUB/content.opf'])
    manifest = opf.find('./manifest')
    manifest.remove(next(item for item in manifest if item.get('id') == 'ncx'))
    payloads['EPUB/content.opf'] = ET.tostring(opf)
    without_ncx = tmp_path / 'without-ncx.epub'
    with zipfile.ZipFile(without_ncx, 'w') as z:
        for name, data in payloads.items():
            z.writestr(name, data)
    translator.normalize_epub_navigation(without_ncx, output, source_document=document, toc_depth=3,
                                         navigation_math_labels=True)


@pytest.mark.parametrize(('filename', 'href'), [('toc name.ncx', 'compat/toc%20name.ncx'),
                                               ('toc.xml', 'compat/toc.xml')])
def test_declared_ncx_is_normalized_and_resolved_independently_of_filename(tmp_path, filename, href):
    document, source_zip, output = write_ncx_fixture(tmp_path)
    with zipfile.ZipFile(source_zip) as z:
        payloads = {item.filename: z.read(item.filename) for item in z.infolist()}
    payloads['EPUB/compat/' + filename] = payloads.pop('EPUB/compat/toc.ncx')
    opf = ET.fromstring(payloads['EPUB/content.opf'])
    next(item for item in opf.find('./manifest') if item.get('id') == 'ncx').set('href', href)
    payloads['EPUB/content.opf'] = ET.tostring(opf)
    escaped = tmp_path / 'escaped.epub'
    with zipfile.ZipFile(escaped, 'w') as z:
        for name, data in payloads.items():
            z.writestr(name, data)
    translator.normalize_epub_navigation(escaped, output, source_document=document, toc_depth=3,
                                         navigation_math_labels=True)


@pytest.mark.parametrize('mutation', ['wrong-existing-target', 'missing-target', 'wrong-path', 'swap-targets',
                                     'wrong-depth', 'missing', 'extra', 'wrong-label', 'reversed-siblings'])
def test_controlled_ncx_must_match_actual_nav_in_order_hierarchy_target_and_label(tmp_path, mutation):
    document, source_zip, output = write_ncx_fixture(tmp_path, mutation)
    with pytest.raises(translator.CliError, match='NCX|原标题不符'):
        translator.normalize_epub_navigation(source_zip, output, source_document=document, toc_depth=3,
                                             navigation_math_labels=True)


@pytest.mark.parametrize("formula", [r"\alpha", r"\mathcal L_A"])
def test_ordinary_image_math_alt_remains_literal_in_readable_navigation(formula):
    image = {"t": "Image", "c": [["", [], []], [math(formula)], ["ordinary.png", ""]]}
    assert sem.inline_text([image]) == formula
    assert sem.inline_text([image], navigation_math_labels=True) == formula
