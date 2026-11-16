from __future__ import annotations

import argparse
import base64
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from tools import translator

ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / ".tmp" / "tests"
TEMP.mkdir(parents=True, exist_ok=True)


class ProductionIntegrationTests(unittest.TestCase):
    def package(self):
        x = translator.XHTML_NAMESPACE if hasattr(translator, "XHTML_NAMESPACE") else "http://www.w3.org/1999/xhtml"
        e = translator.EPUB_NAMESPACE
        docs = {
            "EPUB/nav.xhtml": ET.fromstring(f'<html xmlns="{x}" xmlns:epub="{e}"><body><nav epub:type="toc"><ol><li><a href="text/a.xhtml#a">A</a></li></ol></nav></body></html>'),
            "EPUB/text/a.xhtml": ET.fromstring(f'<html xmlns="{x}"><body><section id="a"><h1>A</h1><pre><code>  first\nsecond</code></pre></section></body></html>'),
        }
        payloads = {
            "META-INF/container.xml": b'<container><rootfiles><rootfile full-path="EPUB/content.opf"/></rootfiles></container>',
            "EPUB/content.opf": b'<package><manifest><item id="nav" href="nav.xhtml" properties="nav"/><item id="a" href="text/a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>',
        }
        ast = {"blocks": [
            {"t": "Header", "c": [1, ["a", [], []], [{"t": "Str", "c": "A"}]]},
            {"t": "CodeBlock", "c": [["", [], []], "  first\nsecond"]},
        ]}
        return docs, payloads, ast

    def test_final_shared_seam_checks_full_code_before_publication(self):
        docs, payloads, ast = self.package()
        translator.validate_generated_epub_semantics(docs, payloads, ast, toc_depth=3)
        code = next(node for node in docs["EPUB/text/a.xhtml"].iter() if node.tag.endswith("}code"))
        code.text = " first\nsecond"
        with self.assertRaisesRegex(translator.CliError, "全文/空白"):
            translator.validate_generated_epub_semantics(docs, payloads, ast, toc_depth=3)

    def test_final_shared_seam_checks_labels_not_only_ids(self):
        docs, payloads, ast = self.package()
        anchor = next(node for node in docs["EPUB/nav.xhtml"].iter() if node.tag.endswith("}a"))
        anchor.text = "Wrong"
        with self.assertRaisesRegex(translator.CliError, "label"):
            translator.validate_generated_epub_semantics(docs, payloads, ast, toc_depth=3)

    def test_plain_code_keeps_lines_and_scrolls_within_its_width(self):
        for name in ("epub", "html"):
            css = (ROOT / "formats" / name / "book.css").read_text(encoding="utf-8")
            with self.subTest(format=name):
                self.assertRegex(css, r"(?s)\bpre\s*\{[^}]*max-width:\s*100%[^}]*white-space:\s*pre;")
                self.assertRegex(css, r"(?s)\bpre\s*\{[^}]*overflow-x:\s*auto;")
                self.assertRegex(css, r"(?s)\bpre\s*\{[^}]*overflow-wrap:\s*normal;")

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_numbered_latex_uses_the_controlled_shared_build_seam(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            entry, cover, output = root / "main.tex", root / "cover.png", root / "book.epub"
            cover.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="))
            entry.write_text(
                r"\documentclass{book}\begin{document}\chapter{Chapter}\label{ch}Text."
                r"\section{Detail}\label{detail}\begin{verbatim}" + "\n  first\nsecond\n" +
                r"\end{verbatim}\end{document}", encoding="utf-8"
            )
            translator.build_latex_epub_source(
                root=ROOT, work_path=root, entry=entry, output=output,
                title="Controlled fixture", author=None, language="en",
                identifier="urn:test:controlled", cover=cover,
                structure_options=["--epub-title-page=false", "--toc-depth=3"], toc_depth=3,
            )
            self.assertTrue(output.is_file())

    def test_handoff_is_explicit_and_has_station_activity(self):
        args = translator.parser().parse_args([
            "handoff-check", "Works/example", "--lang", "zh-CN", "--package", "package.json",
            "--scope-file", "scope.json", "--activity", "agent-review",
        ])
        self.assertEqual(args.handler, translator.command_handoff_check)
        self.assertIn("handoff-check", translator.WORK_COMMANDS)
        self.assertEqual(args.activity, ["agent-review"])

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_handoff_command_reads_actual_entry_and_rejects_wrong_source(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            entry = root / "chapter.md"
            entry.write_text('# Chapter {#chapter}\n\n[]{.source-page data-page="10"}Start. End.\n', encoding="utf-8")
            source = {"kind": "page", "ids": [10], "start": {"id": 10, "anchor": "Source start"},
                      "end": {"id": 10, "anchor": "Source end"}}
            scope = {"chapter_id": "chapter", "source": source, "languages": ["zh-CN"]}
            package = {"schema_version": 1, "chapter_id": "chapter", "source": source,
                       "entries": [{"language": "zh-CN", "path": "chapter.md", "start_anchor": "Chapter Start.", "end_anchor": "End."}],
                       "assets": [], "terms": [], "issues": [],
                       "review": {"visual": {"reviewer": "reviewer", "source_ids": [10]},
                                  "validations": [{"name": "content", "result": "pass"}]}}
            (root / "scope.json").write_text(json.dumps(scope), encoding="utf-8")
            (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
            work = translator.Work(ROOT, root, {}, "fixture", "Fixture", "markdown", None,
                                   root / "source.pdf", 10,
                                   (translator.Language("zh-CN", "source", entry, None, {}),))
            args = argparse.Namespace(root=None, lang="zh-CN", scope_file="scope.json", package="package.json")
            with mock.patch.object(translator, "load_command_work", return_value=work):
                self.assertEqual(translator.command_handoff_check(args), 0)
                entry.write_text(entry.read_text(encoding="utf-8").replace('data-page="10"', 'data-page="9"'), encoding="utf-8")
                with self.assertRaisesRegex(translator.CliError, "实际来源集合"):
                    translator.command_handoff_check(args)


if __name__ == "__main__":
    unittest.main()
