from __future__ import annotations

import copy
import json
import posixpath
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from tools import epub_semantics as semantics


def header(level: int, identifier: str, text: str, classes: list[str] | None = None) -> dict:
    return {"t": "Header", "c": [level, [identifier, classes or [], []], [{"t": "Str", "c": text}]]}


def ast(*blocks: dict) -> dict:
    return {"blocks": list(blocks)}


def line(*nodes: dict) -> dict:
    return {"t": "LineBlock", "c": [list(nodes)]}


def code(text: str) -> dict:
    return {"t": "Code", "c": [["", [], []], text]}


def span(style: str, *nodes: dict) -> dict:
    return {"t": "Span", "c": [["", [style], []], list(nodes)]}


def rich(*blocks: dict, kind: str = "code-example") -> dict:
    return {"t": "Div", "c": [["", [kind], []], list(blocks)]}


class OutlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ast(header(1, "a", "Alpha"), header(2, "b", "Beta"), header(1, "c", "Gamma"))
        self.toc = ET.fromstring('<nav><ol><li><a href="text/a.xhtml#a">Alpha</a><ol><li><a href="text/a.xhtml#b">Beta</a></li></ol></li><li><a href="text/c.xhtml#c">Gamma</a></li></ol></nav>')
        self.documents = {
            "EPUB/text/a.xhtml": ET.fromstring('<html><body><section id="a"><h1>Alpha</h1><section id="b"><h2>Beta</h2></section></section></body></html>'),
            "EPUB/text/c.xhtml": ET.fromstring('<html><body><h1 id="c">Gamma</h1></body></html>'),
        }

    def validate(self, **kwargs) -> None:
        semantics.validate_outline(self.source, self.toc, **kwargs)

    def test_old_toc_only_call_and_real_heading_target(self) -> None:
        self.validate()
        self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_same_id_sequence_with_flattened_depth_fails(self) -> None:
        first = self.toc[0][0]
        child = first[1][0]
        first.remove(first[1])
        self.toc[0].insert(1, child)
        with self.assertRaisesRegex(semantics.EpubSemanticError, "depth"):
            self.validate()

    def test_same_depth_wrong_parent_fails(self) -> None:
        self.source = ast(header(1, "a", "Alpha"), header(1, "c", "Gamma"), header(2, "b", "Beta"))
        self.toc = ET.fromstring('<nav><ol><li><a href="a#a">Alpha</a><ol><li><a href="c#c">Gamma</a><ol><li><a href="b#b">Beta</a></li></ol></li></ol></li></ol></nav>')
        with self.assertRaisesRegex(semantics.EpubSemanticError, "depth|parent"):
            self.validate()

    def test_visible_label_changes_fail_even_when_id_sequence_matches(self) -> None:
        self.toc[0][0][0].text = "Other title"
        with self.assertRaisesRegex(semantics.EpubSemanticError, "label"):
            self.validate()

    def test_href_to_existing_wrong_document_and_same_fragment_fails(self) -> None:
        self.documents["EPUB/text/wrong.xhtml"] = ET.fromstring('<html><h1 id="a">Not Alpha</h1></html>')
        self.toc[0][0][0].set("href", "text/wrong.xhtml#a")
        with self.assertRaisesRegex(semantics.EpubSemanticError, "真实目标标题|多个成品文档中重复"):
            self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_fragment_on_non_heading_fails(self) -> None:
        self.documents["EPUB/text/a.xhtml"] = ET.fromstring('<html><p id="a">Alpha</p><h2 id="b">Beta</h2></html>')
        with self.assertRaisesRegex(semantics.EpubSemanticError, "未指向真实标题"):
            self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_target_heading_level_mismatch_fails(self) -> None:
        self.documents["EPUB/text/c.xhtml"][0][0].tag = "h2"
        with self.assertRaisesRegex(semantics.EpubSemanticError, "真实目标标题"):
            self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_outside_package_href_and_missing_target_fail(self) -> None:
        for href in ("../../a.xhtml#a", "/a.xhtml#a", "missing.xhtml#a"):
            with self.subTest(href=href), self.assertRaises(semantics.EpubSemanticError):
                self.toc[0][0][0].set("href", href)
                self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_unlisted_titlepage_not_parent_and_toc_depth(self) -> None:
        self.source = ast(header(1, "title", "Book", ["unlisted"]), header(2, "chapter", "Chapter"), header(4, "detail", "Detail"))
        self.toc = ET.fromstring('<nav><ol><li><a href="ch#chapter">Chapter</a></li></ol></nav>')
        self.validate(toc_depth=2)
        self.assertEqual(semantics.outline_from_ast(self.source, 4)[1].depth, 2)

    def test_namespaces_inline_tags_and_encoded_fragment(self) -> None:
        self.source = ast(header(1, "章", "Alpha Beta"))
        self.toc = ET.fromstring('<nav xmlns="http://www.w3.org/1999/xhtml"><ol><li><a href="text/a.xhtml#%E7%AB%A0">Alpha <em>Beta</em></a></li></ol></nav>')
        self.documents["EPUB/text/a.xhtml"] = ET.fromstring('<section xmlns="http://www.w3.org/1999/xhtml" id="章"><h1>Alpha <em>Beta</em></h1></section>')
        self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_partial_target_arguments_and_duplicate_ids_rejected(self) -> None:
        with self.assertRaises(semantics.EpubSemanticError):
            self.validate(nav_path="EPUB/nav.xhtml")
        self.documents["EPUB/text/a.xhtml"].append(ET.fromstring('<p id="a"/>'))
        with self.assertRaisesRegex(semantics.EpubSemanticError, "重复 ID"):
            self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_expected_heading_id_duplicate_in_another_document_is_not_a_valid_alias(self) -> None:
        self.documents["EPUB/text/duplicate.xhtml"] = ET.fromstring('<h1 id="a">Alpha</h1>')
        with self.assertRaisesRegex(semantics.EpubSemanticError, "多个成品文档中重复"):
            self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)

    def test_real_target_dom_order_cannot_be_reversed_with_unchanged_nav(self) -> None:
        self.source = ast(header(1, "a", "Alpha"), header(1, "b", "Beta"))
        self.toc = ET.fromstring('<nav><ol><li><a href="text/a.xhtml#a">Alpha</a></li><li><a href="text/a.xhtml#b">Beta</a></li></ol></nav>')
        self.documents = {"EPUB/text/a.xhtml": ET.fromstring('<body><h1 id="b">Beta</h1><h1 id="a">Alpha</h1></body>')}
        # Older callers without a spine declaration retain their old contract.
        self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents)
        with self.assertRaisesRegex(semantics.EpubSemanticError, "实际目标阅读顺序|真实目标阅读顺序"):
            self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents, spine_paths=["EPUB/text/a.xhtml"])

    def test_real_cross_spine_order_not_dictionary_order(self) -> None:
        self.validate(nav_path="EPUB/nav.xhtml", documents=dict(reversed(list(self.documents.items()))),
                      spine_paths=["EPUB/text/a.xhtml", "EPUB/text/c.xhtml"])
        with self.assertRaisesRegex(semantics.EpubSemanticError, "真实目标阅读顺序"):
            self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents,
                          spine_paths=["EPUB/text/c.xhtml", "EPUB/text/a.xhtml"])

    def test_spine_order_declaration_must_bind_real_documents(self) -> None:
        with self.assertRaises(semantics.EpubSemanticError):
            self.validate(spine_paths=["EPUB/text/a.xhtml"])
        for spine in (["missing.xhtml"], ["EPUB/text/a.xhtml"] * 2, []):
            with self.subTest(spine=spine), self.assertRaises(semantics.EpubSemanticError):
                self.validate(nav_path="EPUB/nav.xhtml", documents=self.documents, spine_paths=spine)

    def test_empty_or_span_only_nav_is_rejected(self) -> None:
        for markup in ('<nav/>', '<nav><ol><li><span>Alpha</span></li></ol></nav>'):
            with self.subTest(markup=markup), self.assertRaises(semantics.EpubSemanticError):
                semantics.validate_outline(self.source, ET.fromstring(markup))

    def test_real_heading_image_alt_and_tails_are_compared_with_source(self) -> None:
        image = {"t": "Image", "c": [["", ["math-inline"], []],
                 [{"t": "Str", "c": "χ"}], ["math.png", ""]]}
        self.source = ast({"t": "Header", "c": [4, ["formula", [], []], [
            {"t": "Str", "c": "13.2.4"}, {"t": "Space"}, {"t": "Str", "c": "前"},
            image, {"t": "Str", "c": "后"}, {"t": "Space"},
            {"t": "Str", "c": "的作用"},
        ]]})
        self.toc = ET.fromstring(
            '<nav><ol><li><a href="text/a.xhtml#formula">13.2.4 前χ后 的作用</a></li></ol></nav>'
        )
        headings = (
            '<h4>13.2.4 前<img alt="χ"/>后 的作用</h4>',
            '<h4>13.2.4 <span>前<img alt="χ"/>后</span> 的作用</h4>',
            '<h4>13.2.4 <a href="other.xhtml">前<img alt="χ"/>后</a> 的作用</h4>',
        )
        for markup in headings:
            with self.subTest(heading=markup):
                body = ET.fromstring('<html><section id="formula">' + markup + '</section></html>')
                self.documents = {"EPUB/text/a.xhtml": body}
                heading = next(body.iter("h4"))
                self.assertEqual(semantics.heading_text(heading), "13.2.4 前χ后 的作用")
                self.validate(toc_depth=4, nav_path="EPUB/nav.xhtml", documents=self.documents)
                for bad_alt in (None, "", "ψ"):
                    with self.subTest(bad_alt=bad_alt):
                        altered = copy.deepcopy(body)
                        img = next(altered.iter("img"))
                        if bad_alt is None:
                            img.attrib.pop("alt")
                        else:
                            img.set("alt", bad_alt)
                        self.documents = {"EPUB/text/a.xhtml": altered}
                        with self.assertRaisesRegex(semantics.EpubSemanticError, "真实目标标题"):
                            self.validate(toc_depth=4, nav_path="EPUB/nav.xhtml", documents=self.documents)
                self.documents = {"EPUB/text/a.xhtml": body}


class NumberedOutlineTests(unittest.TestCase):
    def test_numbering_uses_complete_document_and_keeps_unnumbered_counters(self) -> None:
        source = ast(header(1, "a", "Chapter"), header(2, "b", "Detail"),
                     header(1, "u", "Interlude", ["unnumbered"]), header(2, "c", "Continued"),
                     header(4, "deep", "Deep"), header(1, "d", "Next"))
        result = semantics.outline_from_ast(source, 4, number_sections=True)
        self.assertEqual([entry.label for entry in result],
                         ["1 Chapter", "1.1 Detail", "Interlude", "1.2 Continued", "1.2.0.1 Deep", "2 Next"])
        self.assertEqual([(entry.depth, entry.parent) for entry in result],
                         [(1, None), (2, "a"), (1, None), (2, "u"), (3, "c"), (1, None)])

    def test_numbering_base_is_minimum_header_not_first_or_only_listed_level(self) -> None:
        source = ast(header(3, "a", "First"), header(4, "b", "Deep"), header(2, "c", "Shallower"))
        self.assertEqual([entry.label for entry in semantics.outline_from_ast(source, 4, number_sections=True)],
                         ["0.1 First", "0.1.1 Deep", "1 Shallower"])
        self.assertEqual([entry.label for entry in semantics.outline_from_ast(source, 2, number_sections=True)], ["1 Shallower"])
        source = ast(header(2, "a", "First"), header(4, "b", "Deep"))
        self.assertEqual([entry.label for entry in semantics.outline_from_ast(source, 4, number_sections=True)],
                         ["1 First", "1.0.1 Deep"])

    def test_hidden_unnumbered_and_unlisted_headers_keep_declared_selection_contract(self) -> None:
        source = ast(header(1, "title", "Book", ["unnumbered", "unlisted"]),
                     header(1, "hidden", "Not listed", ["unlisted"]), header(1, "a", "Chapter"))
        self.assertEqual([entry.label for entry in semantics.outline_from_ast(source, number_sections=True)], ["2 Chapter"])
        self.assertEqual([entry.label for entry in semantics.outline_from_ast(source)], ["Chapter"])

    def test_default_does_not_renumber_lua_inline_labels_or_old_api(self) -> None:
        source = ast(header(1, "part", "1 Part", ["unnumbered"]), header(2, "ch", "1 Chapter", ["unnumbered"]))
        self.assertEqual([entry.label for entry in semantics.outline_from_ast(source)], ["1 Part", "1 Chapter"])
        self.assertEqual([entry.label for entry in semantics.outline_from_ast(source, number_sections=True)], ["1 Part", "1 Chapter"])
        with self.assertRaisesRegex(semantics.EpubSemanticError, "number_sections"):
            semantics.outline_from_ast(source, number_sections="yes")

    @staticmethod
    def _writer_fixture(document: dict, directory: Path, *, toc_depth: int = 4) -> tuple[ET.Element, str, dict, list[str]]:
        document = copy.deepcopy(document)
        document.setdefault("pandoc-api-version", [1, 23, 1, 2])
        document.setdefault("meta", {})
        path = directory / "numbered.epub"
        subprocess.run(["pandoc", "-f", "json", "-t", "epub3", "--number-sections", "--toc",
                        f"--toc-depth={toc_depth}", "--epub-title-page=false", "--top-level-division=chapter",
                        "--metadata=title:Numbered gate fixture", "--split-level=1", "-o", str(path)],
                       input=json.dumps(document), text=True, encoding="utf-8", capture_output=True, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with zipfile.ZipFile(path) as archive:
            documents = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith(".xhtml")}
            opf_path = next(node.attrib["full-path"] for node in ET.fromstring(archive.read("META-INF/container.xml")).iter()
                            if node.tag.rsplit("}", 1)[-1] == "rootfile")
            package = ET.fromstring(archive.read(opf_path))
            namespace = {"o": "http://www.idpf.org/2007/opf"}
            items = {item.attrib["id"]: item for item in package.findall("o:manifest/o:item", namespace)}
            resolve = lambda href: posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
            spine = [resolve(items[item.attrib["idref"]].attrib["href"]) for item in package.findall("o:spine/o:itemref", namespace)]
            nav_path = next(resolve(item.attrib["href"]) for item in items.values() if "nav" in item.attrib.get("properties", "").split())
            toc = next(node for node in documents[nav_path].iter() if node.attrib.get("{http://www.idpf.org/2007/ops}type") == "toc")
        return toc, nav_path, documents, spine

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_real_epub_writer_numbered_labels_depth_and_corruption(self) -> None:
        root = Path(__file__).resolve().parents[1]
        temp = root / ".tmp" / "tests"
        temp.mkdir(parents=True, exist_ok=True)
        source = ast(header(1, "title", "Book", ["unnumbered", "unlisted"]), header(1, "a", "Chapter"),
                     header(3, "deep", "Deep"), header(2, "b", "Detail"),
                     header(1, "u", "Interlude", ["unnumbered"]), header(2, "c", "Continued"), header(1, "z", "Next"))
        with tempfile.TemporaryDirectory(dir=temp) as directory:
            for depth in (2, 4):
                with self.subTest(toc_depth=depth):
                    toc, nav, documents, spine = self._writer_fixture(source, Path(directory), toc_depth=depth)
                    semantics.validate_outline(source, toc, toc_depth=depth, number_sections=True,
                                               nav_path=nav, documents=documents, spine_paths=spine)
                    with self.assertRaisesRegex(semantics.EpubSemanticError, "label"):
                        semantics.validate_outline(source, toc, toc_depth=depth)
                    broken_toc = copy.deepcopy(toc)
                    first_link = next(node for node in broken_toc.iter() if node.tag.rsplit("}", 1)[-1] == "a")
                    first_link[0].text = "99"
                    with self.assertRaisesRegex(semantics.EpubSemanticError, "label"):
                        semantics.validate_outline(source, broken_toc, toc_depth=depth, number_sections=True)
                    numbered = next(node for path in spine for node in documents[path].iter()
                                    if node.tag.rsplit("}", 1)[-1] in {"h1", "h2", "h3", "h4"}
                                    and len(node) and "header-section-number" in node[0].attrib.get("class", "").split())
                    numbered[0].text = "99"
                    with self.assertRaisesRegex(semantics.EpubSemanticError, "真实目标标题"):
                        semantics.validate_outline(source, toc, toc_depth=depth, number_sections=True,
                                                   nav_path=nav, documents=documents, spine_paths=spine)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_real_latex_reader_to_numbered_epub_keeps_exact_code(self) -> None:
        root = Path(__file__).resolve().parents[1]
        temp = root / ".tmp" / "tests"
        temp.mkdir(parents=True, exist_ok=True)
        latex = (r"\documentclass{book}\begin{document}\chapter{Chapter}\label{ch}Text."
                 r"\section{Detail}\label{detail}\begin{verbatim}" + "\n  first\nsecond\n"
                 + r"\end{verbatim}\end{document}")
        reader = subprocess.run(["pandoc", "-f", "latex+raw_tex", f"--lua-filter={root / 'formats/pandoc/latex.lua'}", "-t", "json"],
                                input=latex, text=True, encoding="utf-8", capture_output=True, check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        document = json.loads(reader.stdout)
        with tempfile.TemporaryDirectory(dir=temp) as directory:
            toc, nav, documents, spine = self._writer_fixture(document, Path(directory), toc_depth=3)
            actual = semantics.validate_outline(document, toc, toc_depth=3, number_sections=True,
                                                nav_path=nav, documents=documents, spine_paths=spine)
            self.assertEqual([entry.label for entry in actual], ["1 Chapter", "1.1 Detail"])
            semantics.validate_code_records(semantics.canonical_code(document), semantics.code_from_xhtml([documents[path] for path in spine]))


class CodePreservationTests(unittest.TestCase):
    def test_plain_code_exact_whitespace_and_final_newline(self) -> None:
        for text in ("x", " x\n", "x\n\n", "\tx  y ", "😀 = 2"):
            source = ast({"t": "CodeBlock", "c": [["", ["text"], []], text]})
            root = ET.Element("pre")
            ET.SubElement(root, "code").text = text
            semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([root]))
            root[0].text = text + " "
            with self.assertRaisesRegex(semantics.EpubSemanticError, "全文/空白"):
                semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([root]))

    def test_real_linebreak_not_writer_folding_and_highlight_ranges(self) -> None:
        source = ast(rich({"t": "LineBlock", "c": [[code("x"), {"t": "Space"}, span("code-change", code("a"), {"t": "Space"}, code("b"))], [{"t": "Str", "c": "\u00a0\u00a0"}, code("y")]]}))
        final = ET.fromstring('<div class="code-example"><div class="line-block"><code>x</code> <span class="code-change"><code>a</code>\n<code>b</code></span><br/>\n\u00a0\u00a0<code>y</code></div></div>')
        expected = semantics.canonical_code(source)
        semantics.validate_code_records(expected, semantics.code_from_xhtml([final]))
        self.assertEqual(expected[0].text, "x a b\n  y")
        self.assertIn(semantics.CodeSpan(2, 5, ("code-change",)), expected[0].spans)
        final[0][1].set("class", "")
        with self.assertRaisesRegex(semantics.EpubSemanticError, "高亮/强调"):
            semantics.validate_code_records(expected, semantics.code_from_xhtml([final]))

    def test_nbsp_gap_loss_and_added_br_are_rejected(self) -> None:
        source = ast(rich(line(code("a"), {"t": "Str", "c": "\u00a0\u00a0"}, code("b"))))
        for markup in (
            '<div class="code-example"><div class="line-block"><code>a</code>  <code>b</code></div></div>',
            '<div class="code-example"><div class="line-block"><code>a</code>\u00a0<br/>\u00a0<code>b</code></div></div>',
        ):
            with self.subTest(markup=markup), self.assertRaisesRegex(semantics.EpubSemanticError, "全文/空白"):
                semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([ET.fromstring(markup)]))

    def test_nbsp_inside_code_is_literal_in_both_directions_not_layout(self) -> None:
        for before, after in (("a\u00a0b", "a b"), ("a b", "a\u00a0b")):
            with self.subTest(before=before):
                source = ast(rich(line(code('"' + before + '"'))))
                root = ET.fromstring('<div class="code-example"><div class="line-block"><code>"' + after + '"</code></div></div>')
                with self.assertRaisesRegex(semantics.EpubSemanticError, "全文/空白"):
                    semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([root]))
        source = ast(rich(line(code("a"), {"t": "Str", "c": "\u00a0"}, code("b"))))
        root = ET.fromstring('<div class="code-example"><div class="line-block"><code>a</code> <code>b</code></div></div>')
        semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([root]))
        self.assertIn("!classes.has('code')", semantics.CODE_DOM_EXTRACTOR_JS)

    def test_all_generic_colors_bindings_and_roman_are_preserved(self) -> None:
        styles = sorted(semantics.SEMANTIC_CLASSES)
        source = ast(rich(line(*(span(style, code("x")) for style in styles)), kind="binding-example"))
        final = ET.fromstring('<div class="binding-example"><div class="line-block">' + ''.join(f'<span class="{style}"><code>x</code></span>' for style in styles) + '</div></div>')
        semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([final]))
        for index, style in enumerate(styles):
            broken = copy.deepcopy(final)
            broken[0][index].set("class", "")
            with self.subTest(style=style), self.assertRaisesRegex(semantics.EpubSemanticError, "高亮/强调"):
                semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([broken]))

    def test_emphasis_and_inline_code_semantics_cannot_be_lost(self) -> None:
        source = ast(rich(line({"t": "Emph", "c": [code("😀x")]})))
        final = ET.fromstring('<div class="code-example"><div class="line-block"><em><code>😀x</code></em></div></div>')
        expected = semantics.canonical_code(source)
        self.assertEqual(expected[0].spans, (semantics.CodeSpan(0, 2, ("code",)), semantics.CodeSpan(0, 2, ("em",))))
        semantics.validate_code_records(expected, semantics.code_from_xhtml([final]))
        final[0][0].tag = "span"
        with self.assertRaisesRegex(semantics.EpubSemanticError, "高亮/强调"):
            semantics.validate_code_records(expected, semantics.code_from_xhtml([final]))

    def test_fragment_inventory_not_enough_and_spine_order_matters(self) -> None:
        expected = (semantics.CodeRecord("plain", "a b"), semantics.CodeRecord("plain", "c"))
        for actual in ((semantics.CodeRecord("plain", "b a"), expected[1]), expected[::-1]):
            with self.assertRaisesRegex(semantics.EpubSemanticError, "全文/空白"):
                semantics.validate_code_records(expected, actual)

    def test_dom_json_contract_and_inventory_or_kind_change(self) -> None:
        expected = semantics.canonical_code(ast(rich(line(code("x")))))
        semantics.validate_code_records(expected, [item.as_dict() for item in expected])
        with self.assertRaisesRegex(semantics.EpubSemanticError, "库存"):
            semantics.validate_code_records(expected, [])
        with self.assertRaisesRegex(semantics.EpubSemanticError, "类型"):
            semantics.validate_code_records(expected, [semantics.CodeRecord("plain", "x")])
        self.assertNotIn("SEMANTIC_CLASS_JSON", semantics.CODE_DOM_EXTRACTOR_JS)

    def test_metadata_markers_are_not_code_characters(self) -> None:
        marker = {"t": "Span", "c": [["", ["source-page"], [["data-page", "2"]]], []]}
        source = ast(rich(line(code("x"), marker, code("y"))))
        final = ET.fromstring('<div class="code-example"><div class="line-block"><code>x</code><span xmlns:epub="http://www.idpf.org/2007/ops" epub:type="pagebreak" id="p2"/><code>y</code></div></div>')
        semantics.validate_code_records(semantics.canonical_code(source), semantics.code_from_xhtml([final]))

    def test_unsupported_rich_structure_does_not_silently_pass(self) -> None:
        for source in (ast(rich({"t": "Para", "c": [code("x")]})), ast(rich(line({"t": "Image", "c": [["", [], []], [{"t": "Str", "c": "not math"}], ["image.png", ""]]})))):
            with self.assertRaises(semantics.EpubSemanticError):
                semantics.canonical_code(source)
        with self.assertRaises(semantics.EpubSemanticError):
            semantics.code_from_xhtml([ET.fromstring('<div class="code-example"><p><code>x</code></p></div>')])

    def test_roman_and_explanatory_color_outside_code_keep_context_and_emphasis(self) -> None:
        source = ast({"t": "Para", "c": [{"t": "Emph", "c": [{"t": "Str", "c": "Needs"}, {"t": "Space"}, span("roman", {"t": "Str", "c": "usable"})]}]},
                     {"t": "Para", "c": [span("source-highlight-green", {"t": "Str", "c": "API"})]})
        root = ET.fromstring('<body><p><em>Needs <span class="roman">usable</span></em></p><p><span class="source-highlight-green">API</span></p></body>')
        expected = semantics.teaching_spans_from_ast(source)
        semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([root]))
        self.assertEqual(expected[0].emphasis, ("em",))
        root[0][0][0].set("class", "")
        with self.assertRaisesRegex(semantics.EpubSemanticError, "教学语义"):
            semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([root]))

    def test_code_outside_color_cannot_be_moved_to_a_different_paragraph(self) -> None:
        source = ast({"t": "Para", "c": [{"t": "Str", "c": "First"}, {"t": "Space"}, span("source-highlight-blue", code("x"))]})
        root = ET.fromstring('<body><p>First <code>x</code></p><p>Second <span class="source-highlight-blue"><code>x</code></span></p></body>')
        with self.assertRaises(semantics.EpubSemanticError):
            semantics.validate_teaching_spans(semantics.teaching_spans_from_ast(source), semantics.teaching_spans_from_xhtml([root]))

    def test_teaching_code_preserves_literal_nbsp_and_multiple_ascii_spaces(self) -> None:
        for literal, changed in (("a\u00a0b", "a b"), ("a b", "a\u00a0b"), ("a  b", "a b")):
            source = ast({"t": "Para", "c": [span("source-highlight-green", code(literal))]})
            expected = semantics.teaching_spans_from_ast(source)
            good = ET.fromstring('<p><span class="source-highlight-green"><code>' + literal + '</code></span></p>')
            semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([good]))
            bad = ET.fromstring('<p><span class="source-highlight-green"><code>' + changed + '</code></span></p>')
            with self.subTest(literal=literal), self.assertRaises(semantics.EpubSemanticError):
                semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([bad]))

    @unittest.skipUnless(shutil.which("node"), "node required for shared JS contract")
    def test_shared_js_executes_literal_nbsp_and_unicode_offset_contract(self) -> None:
        # A minimal DOM protocol fixture executes the actual shared extractor,
        # independently of Python's token implementation. Browser QA exercises
        # the same function on native DOM nodes.
        script = r"""
const fs = require('node:fs');
const extract = eval('(' + fs.readFileSync(0, 'utf8') + ')');
const t = data => ({nodeType: 3, data});
function e(localName, classes, childNodes=[]) {
  const classList = [...classes]; classList.contains = c => classes.includes(c);
  const node = {nodeType: 1, localName, classList, childNodes,
    children: childNodes.filter(c => c.nodeType === 1), getAttributeNS: () => ''};
  for (const child of childNodes) child.parentElement = node;
  return node;
}
const root = e('div', ['code-example'], [e('div', ['line-block'], [
  e('code', [], [t('"a\u00a0b"')]), t('\u00a0'),
  e('span', ['code-change'], [e('code', [], [t('c')])]), e('br', []), t('\n'),
  e('code', [], [t('😀')]),
])]);
process.stdout.write(JSON.stringify(extract({querySelectorAll: () => [root]})));
"""
        result = subprocess.run(["node", "-e", script], input=semantics.CODE_DOM_EXTRACTOR_JS,
                                text=True, encoding="utf-8", capture_output=True, check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        source = ast(rich({"t": "LineBlock", "c": [[code('"a\u00a0b"'), {"t": "Str", "c": "\u00a0"}, span("code-change", code("c"))], [code("😀")]]}))
        expected = semantics.canonical_code(source)
        semantics.validate_code_records(expected, json.loads(result.stdout))
        self.assertEqual(expected[0].text, '"a\u00a0b" c\n😀')

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_formal_reader_writer_code_edge_trimming_and_nbsp_indentation(self) -> None:
        source = '```text\n\tx  y \n\n```\n\n::: {.code-example}\n|   ` x `\u00a0\u00a0[`b` `c`]{.code-change}\n| [😀]{.roman}\n:::\n'
        parsed = subprocess.run(["pandoc", "-f", "markdown+fenced_divs+bracketed_spans+footnotes-raw_html", "-t", "json"], input=source, text=True, encoding="utf-8", capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        document = json.loads(parsed.stdout)
        rendered = subprocess.run(["pandoc", "-f", "json", "-t", "html"], input=parsed.stdout, text=True, encoding="utf-8", capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        root = ET.fromstring("<body>" + rendered.stdout + "</body>")
        records = semantics.canonical_code(document)
        self.assertEqual(records[1].text, "  x  b c\n😀")
        semantics.validate_code_records(records, semantics.code_from_xhtml([root]))


if __name__ == "__main__":
    unittest.main()
