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

from tools import epub_page_navigation as pn
from tools import translator

H = "{" + pn.XHTML + "}"
T = pn.EPUB_TYPE
ROOT = Path(__file__).resolve().parents[1]


def document(body):
    return ET.fromstring(
        f'<html xmlns="{pn.XHTML}" xmlns:epub="{pn.EPUB}">'
        f"<head><title>Native</title></head><body>{body}</body></html>"
    )


def fixture(same_member=False):
    marker = '<span id="Page_15" epub:type="pagebreak" title="[15]" class="native-page"/>\n'
    intro = '<p>前<em>强调</em>后。</p><p><a href="a.xhtml#Page_15">Old page</a></p>'
    chapter = (
        '<section id="chapter"><h1>Chapter</h1>'
        '<p>Start<span id="Page_16" epub:type="pagebreak" title="[16]"/>end.</p>'
        "<pre><code>a\n  b\n\tc</code></pre></section>"
    )
    docs = {
        "EPUB/nav.xhtml": document(
            '<nav epub:type="toc"><h1>Contents</h1><ol><li>'
            '<a href="b.xhtml#chapter">Chapter</a></li></ol></nav>'
        ),
        "EPUB/a.xhtml": document(intro + ("" if same_member else marker)),
        "EPUB/b.xhtml": document((marker if same_member else "") + chapter),
    }
    if same_member:
        next(docs["EPUB/a.xhtml"].iter(H + "a")).set("href", "b.xhtml#Page_15")
    payloads = {
        "META-INF/container.xml": (
            b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            b'<rootfiles><rootfile full-path="EPUB/book.opf" '
            b'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ),
        "EPUB/book.opf": (
            f'<package xmlns="{pn.OPF}" version="3.0"><metadata xmlns:dc="{pn.DC}">'
            '<dc:title>Native</dc:title></metadata><manifest>'
            '<item id="n" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
            '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>'
        ).encode(),
    }
    return docs, payloads


def config(before="chapter"):
    return pn.PageNavigationConfig(
        "urn:sha256:" + "a" * 64, "Original EPUB", "zh-CN",
        (pn.NativePageLocation("Page_15", "[15]", before),
         pn.NativePageLocation("Page_16", "[16]")),
        mode="native-pagebreaks",
    )


def write_config(tmp_path, entries):
    (tmp_path / "native.json").write_text(
        json.dumps({"schema_version": 1, "pages": entries}), "utf-8"
    )
    return {
        "source": {"relative_path": "source.epub", "sha256": "a" * 64, "format": "epub"},
        "epub": {"page_navigation": {
            "mode": "native-pagebreaks", "source_label": "Original EPUB",
            "locations": {"zh-CN": "native.json"},
        }},
    }


def test_optional_binding_keeps_legacy_fields_without_fake_pdf_pages(tmp_path):
    manifest = write_config(tmp_path, [
        {"target": "Page_15", "label": "[15]", "before_target": "chapter"},
        {"target": "Page_16", "label": "[16]"},
    ])
    parsed = pn.load_page_navigation_config(tmp_path, manifest, "zh-CN")
    assert parsed == config()
    assert parsed.pages[1].before_target is None
    assert pn.PageNavigationConfig("id", "label", "en", ()).mode == "source-pages"
    assert not (tmp_path / "page-map.tsv").exists()


@pytest.mark.parametrize("value", ["", False, "bad id", "#chapter"])
def test_binding_requires_a_bare_nonempty_id(tmp_path, value):
    manifest = write_config(tmp_path, [
        {"target": "Page_15", "label": "[15]", "before_target": value}
    ])
    with pytest.raises(pn.PageNavigationError):
        pn.load_page_navigation_config(tmp_path, manifest, "zh-CN")


@pytest.mark.parametrize("same_member", [False, True])
def test_declared_heading_move_preserves_original_point_text_and_links(same_member):
    docs, payloads = fixture(same_member)
    original_member = "EPUB/b.xhtml" if same_member else "EPUB/a.xhtml"
    point = next(e for e in docs[original_member].iter() if e.get("id") == "Page_15")
    old_attrs = dict(point.attrib)
    old_text = {name: "".join(docs[name].itertext()) for name in ("EPUB/a.xhtml", "EPUB/b.xhtml")}
    old_toc = ET.tostring(docs["EPUB/nav.xhtml"])
    result = pn.apply_page_navigation(docs, payloads, config())
    assert result.targets[0] == ("[15]", "EPUB/b.xhtml", "Page_15")
    assert next(e for e in docs["EPUB/b.xhtml"].iter() if e.get("id") == "Page_15") is point
    assert all(point.get(k) == v for k, v in old_attrs.items())
    assert point.get("data-page") is None
    parent = docs["EPUB/b.xhtml"].find(H + "body")
    assert parent[0] is point and parent[1].get("id") == "chapter"
    assert {name: "".join(docs[name].itertext()) for name in old_text} == old_text
    assert next(docs["EPUB/a.xhtml"].iter(H + "a")).get("href") == "b.xhtml#Page_15"
    nav = docs["EPUB/nav.xhtml"]
    body = nav.find(H + "body")
    listing = body.find(f'{H}nav[@{T}="page-list"]')
    assert next(listing.iter(H + "a")).get("href") == "b.xhtml#Page_15"
    body.remove(listing)
    assert ET.tostring(nav) == old_toc
    body.append(listing)
    assert pn.validate_page_navigation(docs, payloads, config()) == result


def test_no_binding_keeps_serialized_slot_without_guessing():
    docs, payloads = fixture()
    point = next(e for e in docs["EPUB/a.xhtml"].iter() if e.get("id") == "Page_15")
    parent = docs["EPUB/a.xhtml"].find(H + "body")
    slot = list(parent).index(point)
    result = pn.apply_page_navigation(docs, payloads, config(None))
    assert parent[slot] is point and result.targets[0][1] == "EPUB/a.xhtml"


def figure_fixture():
    docs, payloads = fixture(same_member=True)
    body = docs["EPUB/b.xhtml"].find(H + "body")
    point = next(e for e in body.iter() if e.get("id") == "Page_15")
    body.remove(point)
    figure = ET.Element(H + "figure", {"id": "fig-example"})
    ET.SubElement(figure, H + "img", {"src": "plot.png", "alt": "图 1.1 示例"})
    caption = ET.SubElement(figure, H + "figcaption", {"aria-hidden": "true"})
    caption.text = "图 1.1 示例"
    caption.append(point)
    body.insert(0, figure)
    return docs, payloads, point, figure


def test_explicit_figure_binding_removes_point_from_hidden_caption_without_empty_paragraph():
    docs, payloads, point, figure = figure_fixture()
    before = {name: "".join(root.itertext()) for name, root in docs.items()
              if name != "EPUB/nav.xhtml"}
    bound = config("fig-example")
    result = pn.apply_page_navigation(docs, payloads, bound)
    body = docs["EPUB/b.xhtml"].find(H + "body")
    assert list(body)[:2] == [point, figure]
    assert point not in list(figure.iter())
    assert figure.find(H + "figcaption").get("aria-hidden") == "true"
    assert all("".join(docs[name].itertext()) == text for name, text in before.items())
    assert next(docs["EPUB/a.xhtml"].iter(H + "a")).get("href") == "b.xhtml#Page_15"
    assert pn.validate_page_navigation(docs, payloads, bound) == result


def test_hidden_native_point_without_explicit_binding_is_rejected():
    docs, payloads, _, _ = figure_fixture()
    with pytest.raises(pn.PageNavigationError, match="hidden ancestor"):
        pn.apply_page_navigation(docs, payloads, config(None))


@pytest.mark.parametrize("attribute", [{"aria-hidden": "true"}, {"hidden": ""}])
def test_final_native_qa_rejects_a_point_moved_under_hidden_ancestor(attribute):
    docs, payloads, point, _ = figure_fixture()
    bound = config("fig-example")
    pn.apply_page_navigation(docs, payloads, bound)
    body = docs["EPUB/b.xhtml"].find(H + "body")
    body.remove(point)
    hidden = ET.SubElement(body, H + "div", attribute)
    hidden.append(point)
    with pytest.raises(pn.PageNavigationError, match="hidden ancestor"):
        pn.validate_page_navigation(docs, payloads, bound)


def chain_fixture():
    docs, payloads = fixture()
    first = next(e for e in docs["EPUB/a.xhtml"].iter() if e.get("id") == "Page_15")
    parent = docs["EPUB/a.xhtml"].find(H + "body")
    second = ET.Element(H + "span", {"id": "Page_15b", T: "pagebreak", "title": "[15b]"})
    parent.insert(list(parent).index(first) + 1, second)
    bound = pn.PageNavigationConfig(
        "urn:sha256:" + "a" * 64, "Original EPUB", "zh-CN",
        (pn.NativePageLocation("Page_15", "[15]", "chapter"),
         pn.NativePageLocation("Page_15b", "[15b]", "chapter"),
         pn.NativePageLocation("Page_16", "[16]")), mode="native-pagebreaks",
    )
    return docs, payloads, bound, first, second


def test_consecutive_explicit_points_share_one_heading_without_changing_text_or_links():
    docs, payloads, bound, first, second = chain_fixture()
    texts = {name: "".join(doc.itertext()) for name, doc in docs.items() if name != "EPUB/nav.xhtml"}
    pn.apply_page_navigation(docs, payloads, bound)
    body = docs["EPUB/b.xhtml"].find(H + "body")
    assert list(body)[:2] == [first, second]
    assert body[2].get("id") == "chapter"
    assert next(docs["EPUB/a.xhtml"].iter(H + "a")).get("href") == "b.xhtml#Page_15"
    assert all("".join(docs[name].itertext()) == text for name, text in texts.items())
    assert pn.validate_page_navigation(docs, payloads, bound).page_count == 3


def test_nonconsecutive_same_heading_declarations_fail_before_any_mutation():
    docs, payloads, bound, _first, _second = chain_fixture()
    broken = pn.PageNavigationConfig(bound.source_identifier, bound.source_label, bound.language,
        (bound.pages[0], pn.NativePageLocation("Page_15b", "[15b]"),
         pn.NativePageLocation("Page_16", "[16]", "chapter")), mode="native-pagebreaks")
    before = {name: ET.tostring(doc) for name, doc in docs.items()}
    with pytest.raises(pn.PageNavigationError, match="consecutive"):
        pn.apply_page_navigation(docs, payloads, broken)
    assert {name: ET.tostring(doc) for name, doc in docs.items()} == before


@pytest.mark.parametrize("mutation", ["tail", "element", "unbound-point", "reversed"])
def test_final_qa_rejects_nonempty_interrupted_or_reordered_heading_chain(mutation):
    docs, payloads, bound, first, _second = chain_fixture()
    pn.apply_page_navigation(docs, payloads, bound)
    body = docs["EPUB/b.xhtml"].find(H + "body")
    if mutation == "tail":
        first.tail = "Visible body, not an adjacent pagepoint chain."
    elif mutation == "element":
        body.insert(1, ET.Element(H + "p"))
    elif mutation == "unbound-point":
        body.insert(1, ET.Element(H + "span", {"id": "unbound", T: "pagebreak", "title": "X",
                                             "role": "doc-pagebreak", "aria-label": "X"}))
    else:
        body.remove(first)
        body.insert(1, first)
    with pytest.raises(pn.PageNavigationError):
        pn.validate_page_navigation(docs, payloads, bound)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "paragraph", "code", "reordered"])
def test_false_heading_bindings_are_rejected(mutation):
    docs, payloads = fixture()
    bound = config()
    body = docs["EPUB/b.xhtml"].find(H + "body")
    target = body.find(H + "section")
    if mutation == "missing":
        bound = config("absent")
    elif mutation == "duplicate":
        ET.SubElement(body, H + "h2", {"id": "chapter"}).text = "Duplicate"
    elif mutation == "paragraph":
        bound = config("paragraph")
        ET.SubElement(body, H + "p", {"id": "paragraph"}).text = "Not a title"
    elif mutation == "code":
        body.remove(target)
        ET.SubElement(body, H + "pre").append(target)
    elif mutation == "reordered":
        bound = config("after")
        ET.SubElement(body, H + "h2", {"id": "after"}).text = "After page sixteen"
    with pytest.raises(pn.PageNavigationError):
        pn.apply_page_navigation(docs, payloads, bound)


def test_final_qa_rejects_drift_even_when_page_list_still_matches():
    docs, payloads = fixture()
    pn.apply_page_navigation(docs, payloads, config())
    body = docs["EPUB/b.xhtml"].find(H + "body")
    interloper = ET.Element(H + "p")
    interloper.text = "Moved between the pagebreak and title."
    body.insert(1, interloper)
    with pytest.raises(pn.PageNavigationError, match="verified before_target"):
        pn.validate_page_navigation(docs, payloads, config())


def test_final_qa_rejects_body_text_in_pagepoint_tail_before_heading():
    docs, payloads = fixture()
    pn.apply_page_navigation(docs, payloads, config())
    point = next(e for e in docs["EPUB/b.xhtml"].iter() if e.get("id") == "Page_15")
    point.tail = "Visible body between the pagepoint and heading."
    with pytest.raises(pn.PageNavigationError, match="verified before_target"):
        pn.validate_page_navigation(docs, payloads, config())


def test_binding_rejects_section_text_before_its_first_child_heading():
    docs, payloads = fixture()
    target = next(e for e in docs["EPUB/b.xhtml"].iter() if e.get("id") == "chapter")
    target.text = "Visible body before the first child heading."
    with pytest.raises(pn.PageNavigationError, match="first-heading section"):
        pn.apply_page_navigation(docs, payloads, config())


def test_binding_does_not_silently_remove_a_nav_clone():
    docs, payloads = fixture()
    ET.SubElement(next(docs["EPUB/nav.xhtml"].iter(H + "a")), H + "span", {
        "id": "Page_15", T: "pagebreak", "title": "[15]",
    })
    with pytest.raises(pn.PageNavigationError, match="outside the content spine"):
        pn.apply_page_navigation(docs, payloads, config())


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
def test_real_notes_two_pagepoints_cross_writer_split_as_one_explicit_chain(tmp_path):
    entry, cover, output = tmp_path / "main.md", tmp_path / "cover.png", tmp_path / "book.epub"
    cover.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    entry.write_text(
        '# Chapter {#c06}\n\nBody. [Old page](#page_248)\n\n## Final section {#final}\n\nPrevious paragraph.\n\n'
        '~~~{=html}\n<span id="page_248" epub:type="pagebreak" title="248"></span>\n'
        '<span id="page_249" epub:type="pagebreak" title="249"></span>\n~~~\n\n'
        '# Notes {#nts}\n\n## Introduction {#notes-itr}\n\nComplete original notes.\n', "utf-8",
    )
    manifest = write_config(tmp_path, [
        {"target": "page_248", "label": "248", "before_target": "nts"},
        {"target": "page_249", "label": "249", "before_target": "nts"},
    ])
    manifest["epub"].update(cover="cover.png", title_page=False, toc_depth=2, bodymatter="c06")
    docs, payloads = fixture()
    with zipfile.ZipFile(tmp_path / "source.epub", "w") as archive:
        for name, data in payloads.items(): archive.writestr(name, data)
        for name, doc in docs.items(): archive.writestr(name, ET.tostring(doc))
    language = translator.Language("zh-CN", "source", entry, None, {"epub": output})
    work = translator.Work(ROOT, tmp_path, manifest, "native-chain", "Native chain",
                           "markdown", None, tmp_path / "source.epub", 0, (language,))
    translator.build_markdown(work, language, "epub", output)
    with zipfile.ZipFile(output) as archive:
        docs = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith(".xhtml")}
        payloads = {name: archive.read(name) for name in archive.namelist()}
    result = pn.validate_page_navigation(docs, payloads, pn.load_page_navigation_config(tmp_path, manifest, "zh-CN"))
    assert result.page_count == 2
    assert result.targets[0][1] == result.targets[1][1]
    root = docs[result.targets[0][1]]
    target = next(e for e in root.iter() if e.get("id") == "nts")
    parent = next(e for e in root.iter() if target in list(e))
    index = list(parent).index(target)
    assert [e.get("id") for e in list(parent)[index-2:index]] == ["page_248", "page_249"]
    assert any("Complete original notes." in "".join(doc.itertext()) for doc in docs.values())


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
def test_real_markdown_rawblock_follows_declared_chapter_after_writer_split(tmp_path):
    entry = tmp_path / "main.md"
    output = tmp_path / "book.epub"
    cover = tmp_path / "cover.png"
    cover.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    fence = chr(96) * 3
    entry.write_text(
        "# Before {#before .unnumbered}\n\n"
        "Previous chapter text. [Original page link](#Page_15)\n\n"
        + fence + '{=html}\n<span id="Page_15" epub:type="pagebreak" '
        'title="[15]" class="native-page"></span>\n' + fence
        + "\n\n# Chapter {#chapter .unnumbered}\n\nFirst chapter paragraph.\n",
        "utf-8",
    )
    manifest = write_config(tmp_path, [
        {"target": "Page_15", "label": "[15]", "before_target": "chapter"}
    ])
    manifest["epub"].update(cover="cover.png", title_page=False, toc_depth=1, bodymatter="chapter")
    docs, payloads = fixture()
    with zipfile.ZipFile(tmp_path / "source.epub", "w") as z:
        for name, data in payloads.items():
            z.writestr(name, data)
        for name, doc in docs.items():
            z.writestr(name, ET.tostring(doc))
    language = translator.Language("zh-CN", "source", entry, None, {"epub": output})
    work = translator.Work(
        ROOT, tmp_path, manifest, "native-heading-test", "Native heading",
        "markdown", None, tmp_path / "source.epub", 0, (language,),
    )
    translator.build_markdown(work, language, "epub", output)
    with zipfile.ZipFile(output) as z:
        final_docs = {name: ET.fromstring(z.read(name)) for name in z.namelist() if name.endswith(".xhtml")}
        final_payloads = {name: z.read(name) for name in z.namelist()}
    checked = pn.validate_page_navigation(
        final_docs, final_payloads, pn.load_page_navigation_config(tmp_path, manifest, "zh-CN")
    )
    _, member, _ = checked.targets[0]
    target = next(e for e in final_docs[member].iter() if e.get("id") == "chapter")
    point = next(e for e in final_docs[member].iter() if e.get("id") == "Page_15")
    parent = next(e for e in final_docs[member].iter() if target in list(e))
    assert list(parent)[list(parent).index(target) - 1] is point
    toc = next(e for r in final_docs.values() for e in r.iter(H + "nav") if e.get(T) == "toc")
    assert ["".join(a.itertext()) for a in toc.iter(H + "a")] == ["Before", "Chapter"]
    assert not any(e.get(T) == "pagebreak" for e in toc.iter())
    name, original_link = next(
        (name, a) for name, r in final_docs.items() for a in r.iter(H + "a")
        if "".join(a.itertext()) == "Original page link"
    )
    ref_member, fragment = urldefrag(unquote(original_link.get("href")))
    assert fragment == "Page_15"
    assert posixpath.normpath(posixpath.join(posixpath.dirname(name), ref_member)) == member
    assert not any(
        p.find(f'.//*[@{T}="pagebreak"]') is not None and not "".join(p.itertext()).strip()
        for r in final_docs.values() for p in r.iter(H + "p")
    )
