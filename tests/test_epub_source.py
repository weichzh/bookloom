from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import epub_source

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = ROOT / ".tmp" / "tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _write_epub(
    root: Path, *, version: str = "3.0", body: str, extra: dict[str, str] | None = None
) -> Path:
    source = root / "fixture.epub"
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/package.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    nav_item = (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>'
        if version.startswith("3")
        else ""
    )
    toc_item = (
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        if version.startswith("2")
        else ""
    )
    toc_attr = ' toc="ncx"' if version.startswith("2") else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Fixture title</dc:title><dc:creator>Fixture author</dc:creator>"
        "</metadata><manifest>"
        '<item id="body" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="other" href="text/other.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="notes" href="text/notes.xhtml" media-type="application/xhtml+xml"/>'
        f"{nav_item}{toc_item}</manifest>"
        f'<spine{toc_attr}><itemref idref="body"/><itemref idref="notes" linear="no"/></spine>'
        "</package>"
    )
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": container,
        "OEBPS/package.opf": opf,
        "OEBPS/text/ch1.xhtml": body,
        "OEBPS/text/other.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="target">Other</p></body></html>',
        "OEBPS/text/other.png": "fixture image bytes",
        "OEBPS/text/notes.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Notes</p></body></html>',
    }
    if version.startswith("3"):
        files["OEBPS/nav.xhtml"] = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops"><ol><li><a href="text/ch1.xhtml">Fixture</a></li></ol></nav></body></html>'
        )
    else:
        files["OEBPS/toc.ncx"] = (
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap/></ncx>'
        )
    files.update(extra or {})
    with zipfile.ZipFile(source, "w") as archive:
        mimetype = zipfile.ZipInfo("mimetype")
        mimetype.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype, files.pop("mimetype").encode())
        for name, content in files.items():
            archive.writestr(name, content.encode())
    return source


XHTML = """\
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="h">Title</h1>
    <div id="wrap" class="box">
      <p id="p"><span id="page_1" class="source-anchor" data-page="1"/>A <i>emphasis</i> and <a id="R1" href="#n1"><sup>1</sup></a> <a href="other.xhtml#target">link</a>.</p>
      <blockquote id="quote"><p>Quoted <em>word</em>.</p></blockquote>
      <img id="figure" src="other.png" alt="Figure"/>
      <ul id="items"><li id="li1">One</li><li>Two<ul><li>Nested</li></ul></li></ul>
    </div>
    <div class="footnotes">
      <div id="n1" class="footnote_1digit"><span class="label"><a href="#R1"><sup>1</sup></a></span>Note <i>emphasis</i>.</div>
    </div>
  </body>
</html>
"""


class EpubSourceTests(unittest.TestCase):
    def temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)

    def test_epub2_spine_rows_and_metadata(self) -> None:
        with self.temporary_directory() as directory:
            source = _write_epub(Path(directory), version="2.0", body=XHTML)
            package = epub_source.read_epub(source)
            self.assertEqual(package.version, "2.0")
            self.assertEqual(package.metadata["title"], ("Fixture title",))
            self.assertEqual(package.metadata["creator"], ("Fixture author",))
            self.assertEqual(package.ncx_href, "OEBPS/toc.ncx")
            self.assertIsNone(package.nav_href)
            self.assertEqual(len(package.source_sha256), 64)
            self.assertEqual(len(package.member_sha256["OEBPS/text/ch1.xhtml"]), 64)
            rows = package.source_unit_rows()
            self.assertEqual(rows[0]["unit_id"], "1")
            self.assertEqual(rows[0]["spine_order"], "1")
            self.assertEqual(rows[0]["xhtml_path"], "OEBPS/text/ch1.xhtml")
            self.assertEqual(rows[1]["linear"], "no")
            self.assertEqual(epub_source.read_source_units(source), rows)

    def test_epub3_nav_and_safe_traversal_rejection(self) -> None:
        with self.temporary_directory() as directory:
            source = _write_epub(Path(directory), version="3.2", body=XHTML)
            package = epub_source.read_epub(source)
            self.assertEqual(package.nav_href, "OEBPS/nav.xhtml")
            self.assertIsNone(package.ncx_href)
            bad = Path(directory) / "bad.epub"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", "<container/>")
                archive.writestr("../escape.txt", "bad")
            with self.assertRaisesRegex(
                epub_source.EpubSourceError, "越出 EPUB 根目录"
            ):
                epub_source.read_epub(bad)

    def test_xhtml_draft_preserves_semantics_and_pandoc_roundtrip(self) -> None:
        targets = {
            "OEBPS/text/ch1.xhtml": {
                "h",
                "wrap",
                "p",
                "page_1",
                "R1",
                "quote",
                "figure",
                "items",
                "li1",
                "n1",
            },
            "OEBPS/text/other.xhtml": {"target"},
        }
        targets["OEBPS/text/ch1.xhtml"].add("figure")
        targets["OEBPS/text/other.png"] = set()
        result = epub_source.convert_xhtml(
            XHTML.encode(),
            href="OEBPS/text/ch1.xhtml",
            unit=1,
            package_targets=targets,
            package_members=targets,
            include_unit_marker=True,
        )
        markdown = result.markdown
        self.assertIn("*emphasis*", markdown)
        self.assertIn("[link](OEBPS/text/other.xhtml#target)", markdown)
        self.assertIn('[]{#page_1 .source-anchor data-page="1"}', markdown)
        self.assertIn("::: {#wrap .box}", markdown)
        self.assertIn("[]{#figure}![Figure](OEBPS/text/other.png)", markdown)
        self.assertIn("- Two", markdown)
        self.assertIn("  - Nested", markdown)
        self.assertIn("[]{#R1}[^n1]", markdown)
        self.assertIn("[^n1]: []{#n1}Note *emphasis*.", markdown)
        self.assertIn('[]{.source-unit data-unit="1"}', markdown)
        self.assertEqual(
            {location.source_id for location in result.locations},
            set(targets["OEBPS/text/ch1.xhtml"]),
        )
        self.assertGreater(
            max(location.markdown_line for location in result.locations), 1
        )
        self.assertTrue(
            shutil.which("pandoc"), "Pandoc is required for this roundtrip test"
        )
        parsed = subprocess.run(
            ["pandoc", "-f", epub_source.PANDOC_READER, "-t", "json"],
            input=markdown,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        self.assertEqual(parsed.stderr, "")
        ast = json.loads(parsed.stdout)

        def block_tags(block: dict[str, object]) -> list[str]:
            tags = [str(block["t"])]
            contents = block.get("c")
            if isinstance(contents, list):
                for child in contents:
                    if isinstance(child, dict) and "t" in child:
                        tags.extend(block_tags(child))
                    elif isinstance(child, list):
                        for nested in child:
                            if isinstance(nested, dict) and "t" in nested:
                                tags.extend(block_tags(nested))
            return tags

        tags = [tag for block in ast["blocks"] for tag in block_tags(block)]
        self.assertIn("Header", tags)
        self.assertIn("Div", tags)
        self.assertIn("BulletList", tags)
        self.assertIn("Note", json.dumps(ast))
        self.assertIn('"Image"', json.dumps(ast))

    def test_missing_anchor_and_duplicate_id_are_not_silent(self) -> None:
        missing = '<html xmlns="http://www.w3.org/1999/xhtml"><body><p><a href="#missing">bad</a></p></body></html>'
        with self.assertRaisesRegex(epub_source.EpubSourceError, "片段目标不存在"):
            epub_source.convert_xhtml(missing, href="unit.xhtml")
        duplicate = '<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="x"><span id="x"/></p></body></html>'
        with self.assertRaisesRegex(epub_source.EpubSourceError, "id 重复"):
            epub_source.convert_xhtml(duplicate, href="unit.xhtml")

    def test_literal_markdown_syntax_stays_in_a_paragraph(self) -> None:
        literal = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>'
            "# literal heading<br/>&gt; literal quote<br/>1. literal enumeration"
            "<br/>- literal dash<br/>&lt;http://example.test&gt;"
            "</p></body></html>"
        )
        result = epub_source.convert_xhtml(literal, href="unit.xhtml")
        parsed = subprocess.run(
            ["pandoc", "-f", epub_source.PANDOC_READER, "-t", "json"],
            input=result.markdown,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        ast = json.loads(parsed.stdout)
        self.assertEqual([block["t"] for block in ast["blocks"]], ["Para"])
        strings = [item["c"] for item in ast["blocks"][0]["c"] if item["t"] == "Str"]
        for literal_token in (
            "#",
            ">",
            "1.",
            "-",
            "<http://example.test>",
        ):
            self.assertIn(literal_token, strings)

    def test_source_unit_marker_preserves_native_first_block(self) -> None:
        cases = (
            ("blockquote", "BlockQuote", '> []{.source-unit data-unit="1"}'),
            ("ul", "BulletList", '- []{.source-unit data-unit="1"}'),
            ("ol", "OrderedList", '1. []{.source-unit data-unit="1"}'),
        )
        for tag, expected, marker_prefix in cases:
            with self.subTest(tag=tag):
                inner = "<p>Quote</p>" if tag == "blockquote" else "<li>One</li>"
                literal = (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                    f"<{tag}>{inner}</{tag}>"
                    "</body></html>"
                )
                result = epub_source.convert_xhtml(
                    literal, href="unit.xhtml", unit=1, include_unit_marker=True
                )
                self.assertIn(marker_prefix, result.markdown)
                parsed = subprocess.run(
                    ["pandoc", "-f", epub_source.PANDOC_READER, "-t", "json"],
                    input=result.markdown,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
                ast = json.loads(parsed.stdout)
                self.assertEqual(ast["blocks"][0]["t"], expected)
                self.assertIn("source-unit", json.dumps(ast))

    def test_empty_notes_are_reported_and_retained(self) -> None:
        empty = '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Body</p><div id="notes" class="endnotes"></div></body></html>'
        result = epub_source.convert_xhtml(empty, href="unit.xhtml")
        self.assertIn("空 Notes 容器已显式保留", result.warnings)
        self.assertIn("::: {#notes .endnotes}", result.markdown)
        self.assertIn("\n:::\n", result.markdown)

    def test_write_drafts_requires_scratch_containment(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = _write_epub(root, body=XHTML)
            package, drafts = epub_source.render_epub(source, units=[1, 2])
            output = root / "scratch" / "drafts"
            paths, map_path = epub_source.write_drafts(
                package, drafts, output, scratch_root=root / "scratch"
            )
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].name, "draft.md")
            self.assertTrue(paths[0].is_file())
            self.assertTrue(map_path.is_file())
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["draft.md", "source-map.json"],
            )
            source_map = json.loads(map_path.read_text(encoding="utf-8"))
            self.assertEqual(source_map["reader"], epub_source.PANDOC_READER)
            self.assertEqual(source_map["source_sha256"], package.source_sha256)
            self.assertEqual(
                source_map["units"][0]["unit_sha256"],
                package.member_sha256[drafts[0].href],
            )
            self.assertGreater(source_map["units"][1]["line_offset"], 0)
            with self.assertRaisesRegex(epub_source.EpubSourceError, "拒绝覆盖"):
                epub_source.write_drafts(
                    package, drafts, output, scratch_root=root / "scratch"
                )
            with self.assertRaises(epub_source.EpubSourceError):
                epub_source.write_drafts(
                    package, drafts, root / "outside", scratch_root=root / "scratch"
                )


if __name__ == "__main__":
    unittest.main()
