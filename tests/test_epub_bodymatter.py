from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as E
import zipfile
from pathlib import Path

from tools import epub_bodymatter as body
from tools import translator


class BodymatterTests(unittest.TestCase):
    def test_direct_script_cli_imports_bodymatter_helper(self):
        result = subprocess.run(
            [sys.executable, "-i", "tools/translator.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            input="import sys; sys.exit(0 if callable(getattr(globals().get('epub_bodymatter'), 'apply_declared_bodymatter', None)) else 91)\n",
            text=True,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_real_latex_adapter_targets_body_after_front_matter(self):
        repository = Path(__file__).resolve().parents[1]
        scratch = repository / ".tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            entry, cover, output = root / "main.tex", root / "cover.png", root / "book.epub"
            cover.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="))
            entry.write_text(
                r"\documentclass{book}\begin{document}"
                r"\chapter*{Copyright}\label{copyright}Front."
                r"\chapter{Actual body}\label{ch:1}Body text.\end{document}",
                encoding="utf-8",
            )
            translator.build_latex_epub_source(
                root=repository, work_path=root, entry=entry, output=output,
                title="Bodymatter fixture", author=None, language="en",
                identifier="urn:test:bodymatter", cover=cover,
                structure_options=["--epub-title-page=false", "--toc-depth=3"],
                toc_depth=3, bodymatter="ch:1",
            )
            with zipfile.ZipFile(output) as book:
                docs = {name: E.fromstring(book.read(name)) for name in book.namelist() if name.endswith(".xhtml")}
            nav = next(name for name, document in docs.items() if any(node.tag.rsplit("}", 1)[-1] == "nav" for node in document.iter()))
            body.validate_declared_bodymatter(docs, "ch:1", nav)
            _member, target = body.declared_target(docs, "ch:1")
            self.assertIn("Body text.", "".join(target.itertext()))
            self.assertNotIn("Front.", "".join(target.itertext()))

    def documents(self):
        return {
            "EPUB/nav.xhtml": E.fromstring('<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="landmarks"><ol><li><a epub:type="bodymatter" href="text/ch.xhtml#chapter">正文</a></li></ol></nav></body></html>'),
            "EPUB/text/front.xhtml": E.fromstring('<html><body><section id="copyright"><h1>版权</h1></section></body></html>'),
            "EPUB/text/ch.xhtml": E.fromstring('<html><body><section id="chapter"><h1>正文</h1><p>Text <em>unchanged</em>.</p></section></body></html>'),
        }

    def test_optional_legacy_call_does_not_add_premises_or_mutate(self):
        docs = {"old": E.fromstring('<html><body>Legacy</body></html>')}
        before = E.tostring(docs["old"])
        body.apply_declared_bodymatter(docs, None)
        body.validate_declared_bodymatter(docs, None, "missing")
        self.assertEqual(E.tostring(docs["old"]), before)

    def test_target_not_first_document_is_marked_without_content_change(self):
        docs = self.documents()
        text = ''.join(docs["EPUB/text/ch.xhtml"].itertext())
        for _ in range(2):
            body.apply_declared_bodymatter(docs, "chapter")
            body.validate_declared_bodymatter(docs, "chapter", "EPUB/nav.xhtml")
        self.assertEqual(''.join(docs["EPUB/text/ch.xhtml"].itertext()), text)
        self.assertEqual(docs["EPUB/text/ch.xhtml"].find('.//section').get(body.EPUB_TYPE), "bodymatter")
        self.assertIsNone(docs["EPUB/text/front.xhtml"].find('.//section').get(body.EPUB_TYPE))

    def test_missing_duplicate_wrong_kind_and_conflicting_targets_fail(self):
        for case in ("missing", "duplicate", "span", "conflict", "invalid"):
            with self.subTest(case=case):
                docs = self.documents()
                identifier = "chapter"
                if case == "missing": identifier = "absent"
                if case == "invalid": identifier = "bad id"
                if case == "duplicate": E.SubElement(docs["EPUB/text/front.xhtml"], "div", {"id": "chapter"})
                if case == "span": docs["EPUB/text/ch.xhtml"].find('.//section').tag = "span"
                if case == "conflict": docs["EPUB/text/front.xhtml"].find('.//section').set(body.EPUB_TYPE, "bodymatter")
                with self.assertRaises(body.BodymatterError): body.apply_declared_bodymatter(docs, identifier)

    def test_qa_rejects_missing_semantics_wrong_target_or_multiple_links(self):
        for case in ("role", "href", "duplicate", "outside"):
            with self.subTest(case=case):
                docs = self.documents()
                body.apply_declared_bodymatter(docs, "chapter")
                section = docs["EPUB/text/ch.xhtml"].find('.//section')
                link = docs["EPUB/nav.xhtml"].find('.//a')
                if case == "role": section.attrib.pop(body.EPUB_TYPE)
                if case == "href": link.set("href", "text/front.xhtml#copyright")
                if case == "outside": link.set("href", "https://example.com/#chapter")
                if case == "duplicate": docs["EPUB/nav.xhtml"].find('.//li').append(E.fromstring(E.tostring(link)))
                with self.assertRaises(body.BodymatterError): body.validate_declared_bodymatter(docs, "chapter", "EPUB/nav.xhtml")

    def test_qa_resolves_encoded_colon_fragment(self):
        docs = self.documents()
        docs["EPUB/text/ch.xhtml"].find('.//section').set("id", "ch:1")
        docs["EPUB/nav.xhtml"].find('.//a').set("href", "text/ch.xhtml#ch%3A1")
        body.apply_declared_bodymatter(docs, "ch:1")
        body.validate_declared_bodymatter(docs, "ch:1", "EPUB/nav.xhtml")


if __name__ == "__main__":
    unittest.main()
