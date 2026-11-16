from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from tools import epub_page_navigation as pages

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".tmp" / "translator" / "batch-compat-page" / "navigation-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)


def package(body: str, *, split: str = "") -> tuple[dict[str, ET.Element], dict[str, bytes]]:
    payloads = {
        "META-INF/container.xml": (
            b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            b'<rootfiles><rootfile full-path="EPUB/package.opf" '
            b'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ),
        "EPUB/package.opf": (
            f'<package xmlns="{pages.OPF}" version="3.0">'
            f'<metadata xmlns:dc="{pages.DC}"><dc:title>Test</dc:title></metadata>'
            '<manifest><item id="nav" href="navigation.xhtml" properties="nav" '
            'media-type="application/xhtml+xml"/><item id="ch" href="text/one.xhtml" '
            'media-type="application/xhtml+xml"/>'
            + ('<item id="two" href="text/two.xhtml" media-type="application/xhtml+xml"/>' if split else '')
            + '</manifest><spine><itemref idref="nav"/><itemref idref="ch"/>'
            + ('<itemref idref="two"/>' if split else '')
            + '</spine></package>'
        ).encode(),
    }
    documents = {
        "EPUB/navigation.xhtml": ET.fromstring(
            f'<html xmlns="{pages.XHTML}" xmlns:epub="{pages.EPUB}"><head><title>Test</title></head><body>'
            '<nav epub:type="toc"><h1>Contents</h1><ol><li><a href="text/one.xhtml#chapter">Chapter</a></li></ol></nav>'
            '</body></html>'
        ),
        "EPUB/text/one.xhtml": ET.fromstring(
            f'<html xmlns="{pages.XHTML}" xmlns:epub="{pages.EPUB}"><head><title>Test</title></head><body>'
            f'<h1 id="chapter">Chapter</h1>{body}</body></html>'
        ),
    }
    if split:
        documents["EPUB/text/two.xhtml"] = ET.fromstring(
            f'<html xmlns="{pages.XHTML}"><head><title>Two</title></head><body>{split}</body></html>'
        )
    return documents, payloads


def config(*locations: pages.PageLocation) -> pages.PageNavigationConfig:
    return pages.PageNavigationConfig("urn:sha256:" + "a" * 64, "Test, verified PDF edition", "zh-CN", tuple(locations))


def marker(number: int) -> str:
    return f'<span class="source-page" data-page="{number}" />'


class PageNavigationTests(unittest.TestCase):
    def assert_link_targets(self, documents: dict[str, ET.Element]) -> None:
        from urllib.parse import unquote, urlsplit

        for member, root in documents.items():
            for node in root.iter(f"{{{pages.XHTML}}}a"):
                href = node.get("href", "")
                parsed = urlsplit(href)
                if not parsed.fragment or parsed.scheme:
                    continue
                target_member = pages._member(member, href) if parsed.path else member
                matches = [target for target in documents.get(target_member, ET.Element("missing")).iter() if target.get("id") == unquote(parsed.fragment)]
                self.assertEqual(len(matches), 1, (member, href))

    def test_absent_option_preserves_old_inputs(self) -> None:
        docs, payloads = package("<p>" + marker(1) + "Old book</p>")
        old_docs, old_payloads = copy.deepcopy(docs), dict(payloads)
        self.assertIsNone(pages.load_page_navigation_config(ROOT, {}, "en"))
        self.assertIsNone(pages.apply_page_navigation(docs, payloads, None))
        self.assertEqual(ET.tostring(docs["EPUB/text/one.xhtml"]), ET.tostring(old_docs["EPUB/text/one.xhtml"]))
        self.assertEqual(payloads, old_payloads)
        self.assertIsNone(pages.validate_page_navigation(docs, payloads))

    def test_standard_navigation_keeps_inline_boundary_images_and_sole_toc(self) -> None:
        docs, payloads = package(
            '<p>Before<em>' + marker(1) + 'after</em>, same page.</p><pre><code>one\n  two</code></pre>'
            '<p>' + marker(2) + '<img src="image.png" alt="An image"/></p><p>Same page next block.</p>',
            split='<p>Continued' + marker(3) + 'text</p>',
        )
        settings = config(
            pages.PageLocation(1, "PDF 1", "physical", "marker"),
            pages.PageLocation(2, "vii", "printed", "marker"),
            pages.PageLocation(3, "8", "printed", "marker"),
        )
        old_toc = ET.tostring(docs["EPUB/navigation.xhtml"].find(f".//{{{pages.XHTML}}}nav"))
        result = pages.apply_page_navigation(docs, payloads, settings)
        self.assertEqual(result.page_count, 3)
        self.assertEqual(result.nav_member, "EPUB/navigation.xhtml")
        self.assertEqual(ET.tostring(docs[result.nav_member].find(f".//{{{pages.XHTML}}}nav")), old_toc)
        self.assertEqual(pages.validate_page_navigation(docs, payloads, settings), result)
        points = [node for root in docs.values() for node in root.iter() if node.get(pages.EPUB_TYPE) == "pagebreak"]
        self.assertEqual([node.get("title") for node in points], ["PDF 1", "vii", "8"])
        self.assertEqual(points[0].get("aria-label"), "PDF 第 1 页")
        self.assertEqual(points[1].get("aria-label"), "原书第 vii 页")
        self.assertTrue(all(node.get("class") == "pagebreak" for node in points))
        self.assertNotIn("break-before", "".join(ET.tostring(root).decode() for root in docs.values()))

    def test_code_line_insertion_preserves_nested_highlighting_tabs_blank_lines(self) -> None:
        docs, payloads = package('<p>' + marker(1) + 'lead ' + marker(2) + '</p>'
            '<div id="program"><pre><code><span class="kw">def f():</span>\n\t<span class="id">x</span> = 1 &amp; 2\n\n  return x</code></pre></div>')
        pre = docs["EPUB/text/one.xhtml"].find(f".//{{{pages.XHTML}}}pre")
        original = "".join(pre.itertext())
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "next-code-line", line=2, line_text="\tx = 1 & 2"))
        pages.apply_page_navigation(docs, payloads, settings)
        self.assertEqual("".join(pre.itertext()), original)
        point = next(node for node in pre.iter() if node.get(pages.EPUB_TYPE) == "pagebreak")
        self.assertEqual(point.tail, "\t")
        self.assertEqual(len(pre.findall(f".//{{{pages.XHTML}}}span[@class='kw']")), 1)
        self.assertEqual(pages.code_lines(pre), ("def f():", "\tx = 1 & 2", "", "  return x"))

    def test_multiple_continuations_share_one_program_with_explicit_id(self) -> None:
        docs, payloads = package('<p>' + marker(1) + marker(2) + marker(3) + '</p><pre id="program"><code>a\n  b\n\tc</code></pre>')
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "code-line", "program", 2, "  b"), pages.PageLocation(3, "3", "printed", "code-line", "program", 3, "\tc"))
        pages.apply_page_navigation(docs, payloads, settings)
        pre = docs["EPUB/text/one.xhtml"].find(f".//{{{pages.XHTML}}}pre")
        self.assertEqual("".join(pre.itertext()), "a\n  b\n\tc")
        self.assertEqual([node.get("data-page") for node in pre.iter() if node.get(pages.EPUB_TYPE) == "pagebreak"], ["2", "3"])

    def test_declared_following_block_ordinal_handles_multistage_rich_programs(self) -> None:
        docs, payloads = package('<p>' + marker(1) + marker(2) + '</p><pre><code>previous stage</code></pre><div class="code-example"><div class="line-block"><code>second stage</code><br/>\n<code>continuation</code></div></div>')
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "next-code-line", line=2, line_text="continuation", block=2))
        pages.apply_page_navigation(docs, payloads, settings)
        block = next(node for node in docs["EPUB/text/one.xhtml"].iter() if "line-block" in node.get("class", ""))
        self.assertEqual(pages.code_lines(block), ("second stage", "continuation"))
        self.assertTrue(any(node.get("data-page") == "2" for node in block.iter()))

    def test_rich_lineblock_preserves_markup_and_does_not_count_writer_newline(self) -> None:
        docs, payloads = package('<p>' + marker(1) + 'lead' + marker(2) + '</p><div class="code-example" id="rich"><div class="line-block">'
            '<span class="source-highlight-red"><code>one</code></span><br/>\n'
            '<code>  two</code>\u00a0<span class="source-highlight-blue"><code>three</code></span><br/>\n'
            '<code>four</code></div></div>')
        block = next(node for node in docs["EPUB/text/one.xhtml"].iter() if "line-block" in node.get("class", ""))
        original = "".join(block.itertext())
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "code-line", "rich", 2, "  two\u00a0three"))
        pages.apply_page_navigation(docs, payloads, settings)
        self.assertEqual("".join(block.itertext()), original)
        self.assertEqual(pages.code_lines(block), ("one", "  two\u00a0three", "four"))
        children = list(block)
        point = next(node for node in children if node.get(pages.EPUB_TYPE) == "pagebreak")
        self.assertEqual(children[children.index(point) - 1].tag, f"{{{pages.XHTML}}}br")

    def test_before_target_handles_verified_image_start_without_moving_text(self) -> None:
        docs, payloads = package('<p>previous' + marker(1) + '</p><figure id="figure"><img src="x" alt="x"/></figure><p>' + marker(2) + 'caption</p>')
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "before-target", "figure"))
        pages.apply_page_navigation(docs, payloads, settings)
        body = docs["EPUB/text/one.xhtml"].find(f"{{{pages.XHTML}}}body")
        children = list(body)
        figure = next(node for node in children if node.get("id") == "figure")
        self.assertEqual(children[children.index(figure) - 1].get("data-page"), "2")

    def test_before_target_existing_id_migrates_once_with_exact_tail_and_links(self) -> None:
        for legacy_id in ("source-page-2", "legacy-folio-ii"):
            for first_child in (False, True):
                with self.subTest(legacy_id=legacy_id, first_child=first_child):
                    lead = '<p>' + ('' if first_child else '<em>Lead</em>') + f'<span class="source-page" data-page="2" id="{legacy_id}"/>\t  continuation\n text</p>'
                    docs, payloads = package('<p>' + marker(1) + f'<a href="#{legacy_id}">Old page link</a></p>' + lead + '<figure id="figure"><img src="image.png" alt="Figure"/></figure><pre><code>  x\n\ty  </code></pre>')
                    root = docs["EPUB/text/one.xhtml"]
                    original_text = "".join(root.itertext())
                    original_code = [pages.code_lines(block) for block in pages._code_blocks(root)]
                    settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "ii", "printed", "before-target", "figure"))
                    result = pages.apply_page_navigation(docs, payloads, settings)
                    self.assertEqual(result.targets[1], (2, "EPUB/text/one.xhtml", legacy_id))
                    self.assertEqual("".join(root.itertext()), original_text)
                    self.assertEqual([pages.code_lines(block) for block in pages._code_blocks(root)], original_code)
                    self.assertEqual(sum(node.get("id") == legacy_id for node in root.iter()), 1)
                    self.assertFalse(any("source-page" in node.get("class", "").split() for node in root.iter()))
                    self.assert_link_targets(docs)
                    self.assertEqual(pages.validate_page_navigation(docs, payloads, settings), result)

    def test_before_target_existing_id_across_split_keeps_fragment_identity_in_normalizer(self) -> None:
        from tools import translator

        for legacy_id in ("source-page-2", "legacy-folio-ii"):
            with self.subTest(legacy_id=legacy_id), tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
                docs, payloads = package('<p>' + marker(1) + f'<a href="one.xhtml#{legacy_id}">Original link</a><a href="#{legacy_id}">Fragment link</a><span class="source-page" data-page="2" id="{legacy_id}"/>\t  tail\n</p>', split=f'<p><a href="one.xhtml#{legacy_id}">Cross-file link</a></p><figure id="figure"><img src="x" alt="x"/></figure><pre><code> x\n\ty </code></pre>')
                before_text = {name: "".join(root.itertext()) for name, root in docs.items() if name != "EPUB/navigation.xhtml"}
                before_code = [pages.code_lines(block) for root in docs.values() for block in pages._code_blocks(root)]
                source, output = Path(temporary) / "raw.epub", Path(temporary) / "normalized.epub"
                with zipfile.ZipFile(source, "w") as archive:
                    for member, data in payloads.items():
                        archive.writestr(member, data)
                    for member, root in docs.items():
                        archive.writestr(member, ET.tostring(root))
                settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "ii", "printed", "before-target", "figure"))
                translator.normalize_epub_navigation(source, output, page_navigation=settings)
                with zipfile.ZipFile(output) as archive:
                    payloads = {name: archive.read(name) for name in archive.namelist()}
                normalized = {name: ET.fromstring(data) for name, data in payloads.items() if name.endswith(".xhtml")}
                self.assertEqual({name: "".join(root.itertext()) for name, root in normalized.items() if name != "EPUB/navigation.xhtml"}, before_text)
                self.assertEqual([pages.code_lines(block) for root in normalized.values() for block in pages._code_blocks(root)], before_code)
                targets = [(member, node) for member, root in normalized.items() for node in root.iter() if node.get("id") == legacy_id]
                self.assertEqual(len(targets), 1)
                self.assertEqual(targets[0][0], "EPUB/text/two.xhtml")
                self.assertFalse(any("source-page" in node.get("class", "").split() for root in normalized.values() for node in root.iter()))
                old_links = [node for root in normalized.values() for node in root.iter(f"{{{pages.XHTML}}}a") if (node.text or "") in {"Original link", "Cross-file link", "Fragment link"}]
                self.assertEqual([node.get("href") for node in old_links], [f"two.xhtml#{legacy_id}"] * 3)
                self.assert_link_targets(normalized)
                self.assertEqual(pages.validate_page_navigation(normalized, payloads, settings).targets[1], (2, "EPUB/text/two.xhtml", legacy_id))

    def test_code_line_existing_id_is_migrated_without_losing_source_tail(self) -> None:
        for position in ("code-line", "next-code-line"):
            with self.subTest(position=position):
                docs, payloads = package('<p>' + marker(1) + '<span id="legacy-code-page" class="source-page" data-page="2"/>\t lead tail\n<a href="#legacy-code-page">Original link</a></p><pre id="program"><code>first\n  second</code></pre>')
                root = docs["EPUB/text/one.xhtml"]
                text = "".join(root.itertext())
                settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", position, "program" if position == "code-line" else None, 2, "  second"))
                result = pages.apply_page_navigation(docs, payloads, settings)
                self.assertEqual(result.targets[1][2], "legacy-code-page")
                self.assertEqual("".join(root.itertext()), text)
                self.assertEqual(pages.code_lines(pages._code_blocks(root)[0]), ("first", "  second"))
                self.assertFalse(any("source-page" in node.get("class", "").split() for node in root.iter()))
                self.assert_link_targets(docs)

    def test_cross_split_existing_id_preserves_encoded_fragment_and_unrelated_links(self) -> None:
        legacy_id = "legacy-folio-ii"
        docs, payloads = package('<p>' + marker(1) + '<a href="one.xhtml#%6Cegacy-folio-ii">Encoded link</a><a href="one.xhtml#chapter">Chapter link</a><a href="https://example.invalid/one.xhtml#legacy-folio-ii">External link</a><span class="source-page" data-page="2" id="legacy-folio-ii"/> tail</p>', split='<figure id="figure"><img src="x" alt="x"/></figure>')
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "ii", "printed", "before-target", "figure"))
        pages.apply_page_navigation(docs, payloads, settings)
        links = {node.text: node.get("href") for root in docs.values() for node in root.iter(f"{{{pages.XHTML}}}a")}
        self.assertEqual(links["Encoded link"], "two.xhtml#%6Cegacy-folio-ii")
        self.assertEqual(links["Chapter link"], "one.xhtml#chapter")
        self.assertEqual(links["External link"], "https://example.invalid/one.xhtml#legacy-folio-ii")
        self.assert_link_targets(docs)
        self.assertEqual(pages.validate_page_navigation(docs, payloads, settings).targets[1][2], legacy_id)

    def test_marker_existing_id_is_preserved_in_place(self) -> None:
        docs, payloads = package('<p>before<span class="source-page" data-page="1" id="legacy-page"/>\t after <a href="#legacy-page">Original link</a></p>')
        root = docs["EPUB/text/one.xhtml"]
        point = next(node for node in root.iter() if node.get("id") == "legacy-page")
        before = "".join(root.itertext())
        result = pages.apply_page_navigation(docs, payloads, config(pages.PageLocation(1, "1", "printed", "marker")))
        self.assertEqual(result.targets[0][2], "legacy-page")
        self.assertIs(next(node for node in root.iter() if node.get("id") == "legacy-page"), point)
        self.assertEqual("".join(root.itertext()), before)
        self.assertEqual(point.tail, "\t after ")
        self.assert_link_targets(docs)

    def test_migrated_existing_id_rejects_real_collision_and_missing_target(self) -> None:
        for other, split, target in (
            ('<p id="legacy-page">conflict</p>', '', 'figure'),
            ('', '<p id="legacy-page">conflict</p>', 'figure'),
            ('', '', 'missing'),
            ('', '', 'legacy-page'),
        ):
            with self.subTest(other=other, split=split, target=target):
                docs, payloads = package('<p>' + marker(1) + '<span class="source-page" data-page="2" id="legacy-page"/> tail</p><figure id="figure"><img src="x" alt="x"/></figure>' + other, split=split)
                before = {name: ET.tostring(root) for name, root in docs.items()}
                settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "before-target", target))
                with self.assertRaisesRegex(pages.PageNavigationError, "ID collision|target is missing|migration target"):
                    pages.apply_page_navigation(docs, payloads, settings)
                self.assertEqual({name: ET.tostring(root) for name, root in docs.items()}, before)

    def test_before_target_cannot_bypass_verified_lines_inside_or_at_code(self) -> None:
        for markup in (
            '<pre id="target"><code>x</code></pre>',
            '<pre><code id="target">x</code></pre>',
            '<pre><code><span id="target">x</span></code></pre>',
            '<div class="code-example" id="target"><div class="line-block"><code>x</code></div></div>',
            '<div class="code-example"><div class="line-block"><span id="target"><code>x</code></span></div></div>',
            '<div class="binding-example"><div class="line-block"><code id="target">x</code></div></div>',
        ):
            with self.subTest(markup=markup):
                docs, payloads = package('<p>' + marker(1) + marker(2) + '</p>' + markup)
                original = ET.tostring(docs["EPUB/text/one.xhtml"])
                settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "before-target", "target"))
                with self.assertRaisesRegex(pages.PageNavigationError, "before-target cannot target code"):
                    pages.apply_page_navigation(docs, payloads, settings)
                self.assertEqual(ET.tostring(docs["EPUB/text/one.xhtml"]), original)

    def test_final_qa_rejects_before_target_that_has_become_code(self) -> None:
        docs, payloads = package('<p>' + marker(1) + '</p><p><span id="target">start</span></p><p>' + marker(2) + '</p>')
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "before-target", "target"))
        pages.apply_page_navigation(docs, payloads, settings)
        parent = next(node for node in docs["EPUB/text/one.xhtml"].iter() if any(child.get("id") == "target" for child in node))
        parent.set("class", "code-example")
        with self.assertRaisesRegex(pages.PageNavigationError, "before-target cannot target code"):
            pages.validate_page_navigation(docs, payloads, settings)

    def test_identical_code_container_relocation_fails_for_explicit_and_next_targets(self) -> None:
        for position in ("code-line", "next-code-line"):
            for rich in (False, True):
                with self.subTest(position=position, rich=rich):
                    program = ('<div class="code-example" id="{identifier}"><div class="line-block"><code>first</code><br/>\n<code>  second</code></div></div>' if rich else '<div id="{identifier}"><pre><code>first\n  second</code></pre></div>')
                    docs, payloads = package('<p>' + marker(1) + marker(2) + '</p>' + program.format(identifier="program-a") + program.format(identifier="program-b"))
                    settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", position, "program-a" if position == "code-line" else None, 2, "  second"))
                    pages.apply_page_navigation(docs, payloads, settings)
                    # Final QA uses a freshly parsed publication, not object
                    # identity retained from the generation call.
                    docs = {name: ET.fromstring(ET.tostring(root)) for name, root in docs.items()}
                    pages.validate_page_navigation(docs, payloads, settings)
                    root = docs["EPUB/text/one.xhtml"]
                    blocks = pages._code_blocks(root)
                    before = [pages.code_lines(block) for block in blocks]
                    point = next(node for node in root.iter() if node.get("id") == "source-page-2")
                    parent = next(node for node in root.iter() if point in list(node))
                    index = list(parent).index(point)
                    if index:
                        parent[index - 1].tail = (parent[index - 1].tail or "") + (point.tail or "")
                    else:
                        parent.text = (parent.text or "") + (point.tail or "")
                    parent.remove(point)
                    point.tail = None
                    pages._insert_code_line(blocks[1], 2, point)
                    self.assertEqual([pages.code_lines(block) for block in blocks], before)
                    with self.assertRaisesRegex(pages.PageNavigationError, "bound code container"):
                        pages.validate_page_navigation(docs, payloads, settings)

    def test_explicit_code_target_remains_authoritative_over_generated_binding(self) -> None:
        docs, payloads = package('<p>' + marker(1) + marker(2) + '</p><pre id="program-a"><code>same</code></pre><pre id="program-b"><code>same</code></pre>')
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "code-line", "program-a", 1, "same"))
        pages.apply_page_navigation(docs, payloads, settings)
        root = docs["EPUB/text/one.xhtml"]
        first, second = pages._code_blocks(root)
        point = next(node for node in first.iter() if node.get("id") == "source-page-2")
        first[0].text = point.tail
        first[0].remove(point)
        second[0].text = None
        second[0].insert(0, point)
        point.set("data-page-code-target", "program-b")
        second.set("data-pagebreak-pages", "2")
        with self.assertRaisesRegex(pages.PageNavigationError, "declared code target"):
            pages.validate_page_navigation(docs, payloads, settings)

    def test_wrong_or_missing_boundaries_fail_without_guessing(self) -> None:
        for placement, message in [
            (pages.PageLocation(2, "2", "printed", "next-code-line", line=2, line_text="b"), "line_text"),
            (pages.PageLocation(2, "2", "printed", "next-code-line", line=8, line_text=""), "line_text"),
            (pages.PageLocation(2, "2", "printed", "code-line", "absent", 2, "  b"), "missing"),
        ]:
            with self.subTest(placement=placement):
                docs, payloads = package('<p>' + marker(1) + marker(2) + '</p><pre><code>a\n  b</code></pre>')
                original = ET.tostring(docs["EPUB/text/one.xhtml"])
                with self.assertRaisesRegex(pages.PageNavigationError, message):
                    pages.apply_page_navigation(docs, payloads, config(pages.PageLocation(1, "1", "printed", "marker"), placement))
                self.assertEqual(ET.tostring(docs["EPUB/text/one.xhtml"]), original)
        docs, payloads = package('<p>' + marker(1) + marker(2) + marker(3) + '</p><pre><code>a\n  b</code></pre>')
        with self.assertRaisesRegex(pages.PageNavigationError, "passes another"):
            pages.apply_page_navigation(docs, payloads, config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "next-code-line", line=2, line_text="  b"), pages.PageLocation(3, "3", "printed", "marker")))

    def test_source_range_does_not_implicitly_select_a_page_boundary(self) -> None:
        docs, payloads = package('<p><span data-source-pages="1,2">lead</span>' + marker(2) + '</p><pre><code>a\nb</code></pre>')
        with self.assertRaisesRegex(pages.PageNavigationError, "actual source-page markers"):
            pages.apply_page_navigation(docs, payloads, config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "marker")))

    def test_final_qa_rejects_code_marker_relocated_without_changing_code_text(self) -> None:
        docs, payloads = package('<p>' + marker(1) + marker(2) + '</p><pre><code>first\n  second</code></pre>')
        settings = config(pages.PageLocation(1, "1", "printed", "marker"), pages.PageLocation(2, "2", "printed", "next-code-line", line=2, line_text="  second"))
        pages.apply_page_navigation(docs, payloads, settings)
        code = docs["EPUB/text/one.xhtml"].find(f".//{{{pages.XHTML}}}code")
        point = next(node for node in code.iter() if node.get(pages.EPUB_TYPE) == "pagebreak")
        code.text = (code.text or "") + "  "
        point.tail = point.tail[2:]
        self.assertEqual("".join(code.itertext()), "first\n  second")
        with self.assertRaisesRegex(pages.PageNavigationError, "verified code line boundary"):
            pages.validate_page_navigation(docs, payloads, settings)

    def test_page_list_tampering_is_rejected(self) -> None:
        settings = config(pages.PageLocation(1, "1", "printed", "marker"))
        for change in ("href", "label", "role", "aria", "source"):
            with self.subTest(change=change):
                docs, payloads = package('<p>' + marker(1) + 'text</p>')
                pages.apply_page_navigation(docs, payloads, settings)
                point = next(node for node in docs["EPUB/text/one.xhtml"].iter() if node.get(pages.EPUB_TYPE) == "pagebreak")
                nav = next(node for node in docs["EPUB/navigation.xhtml"].iter() if node.get(pages.EPUB_TYPE) == "page-list")
                anchor = next(nav.iter(f"{{{pages.XHTML}}}a"))
                if change == "href":
                    anchor.set("href", "text/one.xhtml#missing")
                elif change == "label":
                    anchor.text = "wrong"
                elif change == "role":
                    point.set("role", "presentation")
                elif change == "aria":
                    point.set("aria-label", "")
                else:
                    payloads["EPUB/package.opf"] = payloads["EPUB/package.opf"].replace(b"pagination", b"not-pagination")
                with self.assertRaises(pages.PageNavigationError):
                    pages.validate_page_navigation(docs, payloads, settings)

    def test_loader_requires_complete_explicit_positions_and_truthful_folios(self) -> None:
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            work = Path(temporary)
            (work / "page-map.tsv").write_text("pdf_page\toriginal_printed_page\tdisposition\n1\t\tmerge\n2\ti\trebuild\n3\tii\tretain\n", encoding="utf-8")
            sidecar = work / "positions.json"
            declared = {"schema_version": 1, "pages": [{"pdf_page": 1, "position": "marker"}, {"pdf_page": 3, "position": "next-code-line", "line": 2, "line_text": "  abc"}]}
            sidecar.write_text(json.dumps(declared), encoding="utf-8")
            manifest = {"source": {"relative_path": "source.pdf", "sha256": "a" * 64}, "pdf": {"pages": 3}, "epub": {"page_navigation": {"mode": "source-pages", "source_label": "Verified PDF", "locations": {"zh-CN": "positions.json"}}}}
            result = pages.load_page_navigation_config(work, manifest, "zh-CN")
            self.assertEqual([(item.label, item.label_kind) for item in result.pages], [("PDF 1", "physical"), ("ii", "printed")])
            for replacement in [[], [{"pdf_page": 1}], [{"pdf_page": 1, "position": "marker"}, {"pdf_page": 2, "position": "marker"}], [{"pdf_page": 1, "position": "marker", "extra": 1}]]:
                declared["pages"] = replacement
                sidecar.write_text(json.dumps(declared), encoding="utf-8")
                with self.assertRaises(pages.PageNavigationError):
                    pages.load_page_navigation_config(work, manifest, "zh-CN")
            manifest["source"]["format"] = "epub"
            with self.assertRaisesRegex(pages.PageNavigationError, "source-unit is not pagination"):
                pages.load_page_navigation_config(work, manifest, "zh-CN")
            manifest["epub"]["page_navigation"] = {"mode": "not-applicable", "reason": "No reliable fixed pagination in source EPUB"}
            self.assertIsNone(pages.load_page_navigation_config(work, manifest, "zh-CN"))

    @unittest.skipUnless(shutil.which("pandoc") and shutil.which("typst"), "Pandoc and Typst are required")
    def test_three_real_adapter_readers_feed_the_same_final_seam(self) -> None:
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            work = Path(temporary)
            def run(command: list[str]) -> None:
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
                self.assertEqual(result.returncode, 0, result.stderr)

            sources = {
                "markdown": '# Chapter {#chapter}\n\nBefore []{.source-page data-page="1"}text.\n\nLead []{.source-page data-page="2"}\n\n```text\nfirst\n  second\nthird\n```\n',
                "latex": '\\documentclass{book}\n\\begin{document}\n\\chapter{Chapter}\\label{chapter}\nBefore\\sourcepage{1}text.\n\nLead\\sourcepage{2}\n\n\\begin{verbatim}\nfirst\n  second\nthird\n\\end{verbatim}\n\\end{document}\n',
                "typst": '= Chapter <chapter>\n\nBefore #html.elem("span", attrs: (class: "source-page", "data-page": "1"))[]text.\n\nLead #html.elem("span", attrs: (class: "source-page", "data-page": "2"))[]\n\n```text\nfirst\n  second\nthird\n```\n',
            }
            for adapter, text in sources.items():
                with self.subTest(adapter=adapter):
                    source = work / ("main." + {"markdown": "md", "latex": "tex", "typst": "typ"}[adapter])
                    source.write_text(text, encoding="utf-8")
                    ast, output = work / (adapter + ".json"), work / (adapter + ".epub")
                    if adapter == "typst":
                        html = work / "typst.html"
                        run(["typst", "compile", "--features", "html", "--format", "html", str(source), str(html)])
                        reader = [str(html), "--from=html"]
                    else:
                        reader = [str(source), "--from=" + ("markdown" if adapter == "markdown" else "latex+raw_tex"), "--lua-filter=" + str(ROOT / "formats" / "pandoc" / ("semantics.lua" if adapter == "markdown" else "latex.lua"))]
                    run(["pandoc", *reader, "--to=json", "-o", str(ast)])
                    run(["pandoc", str(ast), "--from=json", "--to=epub3", "--metadata=title:Adapter fixture", "--toc", "-o", str(output)])
                    with zipfile.ZipFile(output) as archive:
                        payloads = {name: archive.read(name) for name in archive.namelist()}
                    docs = {name: ET.fromstring(data) for name, data in payloads.items() if name.endswith(".xhtml")}
                    settings = config(pages.PageLocation(1, "PDF 1", "physical", "marker"), pages.PageLocation(2, "1", "printed", "next-code-line", line=2, line_text="  second"))
                    result = pages.apply_page_navigation(docs, payloads, settings)
                    self.assertEqual(result.page_count, 2)
                    point = next(node for root in docs.values() for node in root.iter() if node.get("id") == "source-page-2")
                    self.assertEqual(point.tail, "  second\nthird")
                    self.assertEqual(pages.validate_page_navigation(docs, payloads, settings), result)


if __name__ == "__main__":
    unittest.main()
