from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import translator

# isort: split
from tools import epub_browser_qa

TEMP = ROOT / ".tmp" / "tests"
TEMP.mkdir(parents=True, exist_ok=True)


class ReadingEditionTests(unittest.TestCase):
    def test_html_css_fits_media_and_locally_scrolls_tables(self) -> None:
        css = (ROOT / "formats/html/book.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"img,\s*svg\s*\{[^}]*max-width:\s*100%")
        self.assertRegex(css, r"img,\s*svg\s*\{[^}]*height:\s*auto")
        self.assertRegex(css, r"table\s*\{[^}]*display:\s*block")
        self.assertRegex(css, r"table\s*\{[^}]*max-width:\s*100%")
        self.assertRegex(css, r"table\s*\{[^}]*overflow-x:\s*auto")

    def test_generated_landmarks_localize_only_default_chinese_labels(self) -> None:
        e = translator.EPUB_NAMESPACE
        for language in ("zh-CN", "en"):
            document = ET.fromstring(
                f'<html lang="{language}" xmlns:epub="{e}"><body>'
                '<nav epub:type="landmarks"><ol>'
                '<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>'
                '<li><a epub:type="toc" href="nav.xhtml">Table of Contents</a></li>'
                '<li><a epub:type="cover" href="custom.xhtml">My cover</a></li>'
                '</ol></nav></body></html>'
            )
            translator.localize_generated_landmarks(document)
            anchors = list(document.iter("a"))
            self.assertEqual(
                [a.text for a in anchors],
                ["封面", "目录", "My cover"] if language == "zh-CN"
                else ["Cover", "Table of Contents", "My cover"],
            )
            self.assertEqual(anchors[0].attrib["href"], "cover.xhtml")

    def test_command_runners_do_not_create_visible_windows(self) -> None:
        result = subprocess.CompletedProcess(["probe"], 0, "ok", "")
        with mock.patch.object(translator.subprocess, "run", return_value=result) as run:
            translator.run(["probe"])
        self.assertEqual(
            run.call_args.kwargs["creationflags"], translator.WINDOWS_PROCESS_FLAGS
        )
        runtime = epub_browser_qa.browser_runtime
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            with mock.patch.object(runtime, "resolve_agent_browser", return_value=Path("native.exe")):
                session = runtime.AgentBrowserSession("test", "hidden-probe", root=Path(directory))
            with (
                mock.patch.dict(runtime._SESSIONS, {("test", "hidden-probe"): session}, clear=True),
                mock.patch.object(session, "start"),
                mock.patch.object(runtime.subprocess, "run", return_value=result) as run,
            ):
                epub_browser_qa.run_agent("test", "hidden-probe", ["eval", "true"])
            self.assertEqual(
                run.call_args.args[0][1:5],
                ["--namespace", "test", "--session", "hidden-probe"],
            )
            if os.name == "nt":
                self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
                self.assertEqual(run.call_args.kwargs["startupinfo"].wShowWindow, subprocess.SW_HIDE)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_controlled_epub_outline_rejects_synthetic_book_title(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            work = dataclasses.replace(
                self.work(root),
                manifest={"epub": {"title_page": False, "toc_depth": 1}},
            )
            work.languages[0].entry.write_text(
                "# Book {.unlisted}\n\n# Chapter {#chapter}\n\n## Detail {#detail}\n",
                encoding="utf-8",
            )
            toc = ET.fromstring(
                '<nav><ol><li><a href="ch.xhtml#chapter">Chapter</a></li></ol></nav>'
            )
            translator.validate_markdown_epub_outline(work, work.languages[0], toc)
            toc[0].insert(0, ET.fromstring('<li><a href="ch.xhtml#book">Book</a></li>'))
            with self.assertRaisesRegex(translator.CliError, "目录与正式标题树不一致"):
                translator.validate_markdown_epub_outline(work, work.languages[0], toc)

    def test_bodymatter_landmark_uses_explicit_section_not_first_document(self) -> None:
        x = "http://www.w3.org/1999/xhtml"
        e = "http://www.idpf.org/2007/ops"
        documents = {
            "EPUB/nav.xhtml": ET.fromstring(
                f'<html xmlns="{x}" xmlns:epub="{e}" lang="zh-CN"><body><nav epub:type="toc"><ol><li><a href="text/title.xhtml">title</a></li></ol></nav><nav epub:type="landmarks"><ol><li><a epub:type="cover" href="text/cover.xhtml">cover</a></li></ol></nav></body></html>'
            ),
            "EPUB/text/title.xhtml": ET.fromstring(
                f'<html xmlns="{x}" xmlns:epub="{e}"><body epub:type="bodymatter"><h1 id="title">Title</h1></body></html>'
            ),
            "EPUB/text/chapter.xhtml": ET.fromstring(
                f'<html xmlns="{x}" xmlns:epub="{e}"><body><section id="start" epub:type="bodymatter"><h1>Chapter</h1></section></body></html>'
            ),
        }
        payloads = {
            "META-INF/container.xml": b'<container><rootfiles><rootfile full-path="EPUB/content.opf"/></rootfiles></container>',
            "EPUB/content.opf": b'<package><manifest><item id="nav" href="nav.xhtml" properties="nav"/><item id="title" href="text/title.xhtml"/><item id="chapter" href="text/chapter.xhtml"/></manifest><spine><itemref idref="title"/><itemref idref="chapter"/></spine></package>',
        }
        for _ in range(2):
            translator.add_explicit_bodymatter_landmark(documents, payloads)
        links = [
            node
            for node in documents["EPUB/nav.xhtml"].iter()
            if "bodymatter" in node.attrib.get(f"{{{e}}}type", "").split()
        ]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].attrib["href"], "text/chapter.xhtml#start")
        self.assertEqual(links[0].text, "正文")
        self.assertEqual(len(documents["EPUB/nav.xhtml"].findall(f".//{{{x}}}a")), 3)

    def test_browser_runner_preserves_args_and_enables_local_iframe_access(
        self,
    ) -> None:
        result = argparse.Namespace(returncode=0, stdout="ok", stderr="")
        runtime = epub_browser_qa.browser_runtime
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            with (
                mock.patch.dict(os.environ, {"AGENT_BROWSER_ARGS": "--disable-gpu"}),
                mock.patch.object(runtime, "resolve_agent_browser", return_value=Path("native.exe")),
            ):
                session = runtime.AgentBrowserSession("test", "only-own-session", root=Path(directory))
            session.cdp = "ws://127.0.0.1:12345/devtools/browser/test"
            with (
                mock.patch.dict(runtime._SESSIONS, {("test", "only-own-session"): session}, clear=True),
                mock.patch.object(session, "start"),
                mock.patch.object(runtime.subprocess, "run", return_value=result) as run,
            ):
                epub_browser_qa.run_agent("test", "only-own-session", ["eval", "1+1"])
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["AGENT_BROWSER_ARGS"], "--disable-gpu")
            self.assertEqual(environment["AGENT_BROWSER_ALLOW_FILE_ACCESS"], "1")
            self.assertIn("--cdp", run.call_args.args[0])
            # The browser layer, not only the CLI, must enable local iframes.
            if os.name == "nt":
                chrome = mock.Mock()
                chrome.poll.return_value = None
                with (
                    mock.patch.object(runtime, "resolve_chromium", return_value=Path("chrome.exe")),
                    mock.patch.object(runtime.subprocess, "Popen", return_value=chrome) as spawn,
                    mock.patch.object(Path, "read_text", return_value="12345\n/devtools/browser/test\n"),
                ):
                    session.start()
                arguments = spawn.call_args.args[0]
                self.assertIn("--disable-gpu", arguments)
                self.assertIn("--allow-file-access-from-files", arguments)
                self.assertEqual(spawn.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(shutil.which("autocorrect"), "autocorrect required")
    def test_autocorrect_private_markdown_uses_clean_stdin_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            path = Path(directory) / "private.md"
            path.write_text(
                "中文English测试\n\n```text\nEnglish code\n```\n", encoding="utf-8"
            )
            translator.autocorrect_markdown(path, fix=True)
            result = path.read_text(encoding="utf-8")
            self.assertIn("中文 English 测试", result)
            self.assertIn("English code\n```", result)
            self.assertNotIn("AutoCorrect spend time:", result)
            translator.autocorrect_markdown(path, fix=False)

    @unittest.skipUnless(
        shutil.which("autocorrect") and shutil.which("pandoc"), "format tools required"
    )
    def test_autocorrect_cannot_silently_change_executable_literals(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            path = Path(directory) / "private.md"
            original = '中文English测试\n\n```python\nprint("中文English")\n```\n'
            path.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(translator.CliError, "字面量.*未写入"):
                translator.autocorrect_markdown(path, fix=True)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def work(self, root: Path, pages: int = 3) -> translator.Work:
        entry = root / "main.md"
        return translator.Work(
            root,
            root,
            {},
            "example",
            "Example",
            "markdown",
            None,
            root / "source.pdf",
            pages,
            (translator.Language("zh-CN", "source", entry, None, {}),),
        )

    def test_source_dispositions_require_explicit_reasons(self) -> None:
        self.assertEqual(translator.source_disposition({}, "row"), "retain")
        self.assertEqual(
            translator.source_disposition({"disposition": "  "}, "row"), "retain"
        )
        for value in ("merge", "rebuild", "exclude"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(translator.CliError, "note"):
                    translator.source_disposition({"disposition": value}, "row")
                self.assertEqual(
                    translator.source_disposition(
                        {"disposition": value, "note": "reviewed"}, "row"
                    ),
                    value,
                )
        with self.assertRaisesRegex(translator.CliError, "无效"):
            translator.source_disposition({"disposition": "skip"}, "row")

    def test_source_unit_map_keeps_all_rows_but_selects_body_scope(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            path = Path(directory) / "source-units.tsv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow((*translator.SOURCE_UNITS_HEADER, "disposition"))
                writer.writerows(
                    [
                        (
                            1,
                            1,
                            "cover.xhtml",
                            "yes",
                            "cover",
                            "Cover",
                            "localized cover",
                            "rebuild",
                        ),
                        (
                            2,
                            2,
                            "ad.xhtml",
                            "yes",
                            "advert",
                            "Advert",
                            "publisher sales only",
                            "exclude",
                        ),
                        (
                            3,
                            3,
                            "chapter.xhtml",
                            "yes",
                            "chapter",
                            "Chapter",
                            "",
                            "retain",
                        ),
                    ]
                )
            rows = translator.read_source_units(path)
            self.assertEqual(len(rows), 3)
            expected = [
                int(r["unit_id"])
                for r in rows
                if translator.source_disposition(r, "row") in {"retain", "merge"}
            ]
            translator.validate_source_unit_sequence([3], "zh", expected)
            with self.assertRaises(translator.CliError):
                translator.validate_source_unit_sequence([1, 2, 3], "zh", expected)

    def test_legacy_page_map_preserves_suffix_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            path = root / "page-map.tsv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow(translator.PAGE_MAP_HEADER)
                writer.writerows((n, "", "", "", "", "") for n in range(1, 4))
            self.assertIsNone(translator.validate_page_map(self.work(root)))
            translator.validate_source_page_sequence(
                [2, 3], "legacy", 3, expected_start=2
            )
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow((*translator.PAGE_MAP_HEADER, "disposition"))
                writer.writerows(
                    [
                        (1, "", "", "", "", "cover rebuilt", "rebuild"),
                        (2, "", "", "", "", "", "retain"),
                        (3, "", "", "", "", "", "retain"),
                    ]
                )
            pages = translator.validate_page_map(self.work(root))
            self.assertEqual(pages, [2, 3])
            translator.validate_source_page_sequence(
                [2, 3], "reading", expected_pages=pages
            )
            with self.assertRaisesRegex(translator.CliError, "纳入范围"):
                translator.validate_source_page_sequence(
                    [1, 2, 3], "reading", expected_pages=pages
                )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_marker_swallowed_heading_is_rejected_in_real_ast(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            work = self.work(Path(directory))
            for text in (
                '[]{.source-unit data-unit="1"}## Chapter 1\n\nText\n',
                '# Title []{.source-page data-page="1"}\n',
            ):
                with (
                    self.subTest(text=text),
                    self.assertRaisesRegex(translator.CliError, "标题结构"),
                ):
                    translator.validate_markdown_structure(
                        work, work.languages[0], text
                    )
            translator.validate_markdown_structure(
                work,
                work.languages[0],
                '# Chapter\n\n[]{.source-page data-page="1"}Text.\n\n'
                '```markdown\n[]{.source-unit data-unit="2"}## Literal example\n```\n',
            )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_numbered_image_checks_real_attributes_and_nested_caption(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            work = self.work(Path(directory))
            for image in (
                "![图 21.1 [Clements 01b] 示例](x.png)",
                "![图 21.1 [Clements 01b] 示例](x.png) {#fig-21-1}",
            ):
                with (
                    self.subTest(image=image),
                    self.assertRaisesRegex(translator.CliError, "实际解析的 #fig-"),
                ):
                    translator.validate_markdown_structure(
                        work, work.languages[0], image + "\n\n图 21.1 说明\n"
                    )
            translator.validate_markdown_structure(
                work,
                work.languages[0],
                "![图 21.1 [Clements 01b] 示例](x.png){#fig-21-1}\n\n图 21.1 说明\n",
            )
            for text, error in (
                (
                    "![图 21.2 [Clements 01b] 示例](x.png){#fig-21-1}\n\n图 21.1 说明\n",
                    "原图号 21.1",
                ),
                (
                    "![图 21.1 [Clements 01b] 示例](x.png){#fig-21-1}\n\n图 21.1、21.2 说明\n",
                    "1 幅图片、2 个图号",
                ),
            ):
                with (
                    self.subTest(text=text),
                    self.assertRaisesRegex(translator.CliError, error),
                ):
                    translator.validate_markdown_structure(
                        work, work.languages[0], text
                    )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_numbered_images_exclude_explicit_math_semantics(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            work = self.work(Path(directory))
            for formula in (
                "![formula](eq.png){.math-display}",
                "[![formula](eq.png)]{.math-inline}",
                "::: {.math-display}\n![formula](eq.png)\n:::",
            ):
                with self.subTest(formula=formula):
                    translator.validate_markdown_structure(
                        work, work.languages[0], formula
                        + "\n\n[图 2.7](#fig-2-7)展示了方程。\n",
                    )
                    translator.validate_markdown_structure(
                        work, work.languages[0], formula
                        + "\n\n![图 2.7 示例](plot.png){#fig-2-7}\n\n图 2.7 说明\n",
                    )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_numbered_images_do_not_treat_leading_links_as_captions(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            work = self.work(Path(directory))
            reference = (
                "[]{.source-page data-page=1}"
                "[图 2.8](#fig-2-8)展示了另一结果。\n"
            )
            translator.validate_markdown_structure(
                work, work.languages[0],
                "![图 2.7 示例](plot.png){#fig-2-7}\n\n" + reference,
            )
            with self.assertRaisesRegex(translator.CliError, "实际解析的 #fig-"):
                translator.validate_markdown_structure(
                    work, work.languages[0],
                    "![图 2.7 示例](plot.png)\n\n" + reference,
                )

    def test_epub_structure_options_keep_old_defaults(self) -> None:
        work = self.work(ROOT)
        self.assertEqual(translator.epub_structure_options(work), ["--toc-depth=3"])
        work.manifest["epub"] = {"title_page": False, "toc_depth": 4}
        self.assertEqual(
            translator.epub_structure_options(work),
            ["--toc-depth=4", "--epub-title-page=false"],
        )

    def test_typst_checks_new_scope_without_changing_legacy_pairing(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            source = root / "en/main.typ"
            target = root / "zh/main.typ"
            source.parent.mkdir()
            target.parent.mkdir()
            source.write_text(
                "#source-page(2) source #source-page(3)", encoding="utf-8"
            )
            target.write_text(
                "#source-page(1) target #source-page(2) #source-page(3)",
                encoding="utf-8",
            )
            work = dataclasses.replace(
                self.work(root),
                authoring_format="typst",
                languages=(
                    translator.Language("en", "source", source, None, {}),
                    translator.Language("zh-CN", "translation", target, None, {}),
                ),
            )
            translator.check_typst_pairs(work)
            with self.assertRaisesRegex(translator.CliError, "zh-CN.*纳入范围"):
                translator.check_typst_pairs(work, expected_pages=[2, 3])
            target.write_text(
                "#source-page(2) target #source-page(3)", encoding="utf-8"
            )
            translator.check_typst_pairs(work, expected_pages=[2, 3])
            source.write_text("#source-page(2) source", encoding="utf-8")
            with self.assertRaisesRegex(translator.CliError, "en.*纳入范围"):
                translator.check_typst_pairs(work, expected_pages=[2, 3])

    def test_latex_adapter_passes_structure_options_to_legacy_entry(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            (root / "cover.png").write_bytes(b"cover")
            entry = root / "main.tex"
            entry.write_text(
                r"\documentclass{book}\begin{document}Text\end{document}",
                encoding="utf-8",
            )
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": root / "book.epub"}
            )
            work = dataclasses.replace(
                self.work(root),
                authoring_format="latex",
                languages=(language,),
                manifest={
                    "epub": {"cover": "cover.png", "title_page": False, "toc_depth": 4}
                },
            )
            with mock.patch.object(translator, "build_latex_epub_source") as build:
                translator.build_latex(work, language, "epub", root / "book.epub")
            self.assertEqual(
                build.call_args.kwargs["structure_options"],
                ["--toc-depth=4", "--epub-title-page=false"],
            )

    def test_qa_rejects_generated_title_page_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            output = root / "book.epub"
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr(
                    "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
                )
                archive.writestr("META-INF/container.xml", "placeholder")
                archive.writestr(
                    "EPUB/text/title_page.xhtml", "unexpected generated page"
                )
            work = dataclasses.replace(
                self.work(root), manifest={"epub": {"title_page": False}}
            )
            with self.assertRaisesRegex(translator.CliError, "自动书名页"):
                translator.qa_epub(work, work.languages[0], output, root)

    def init_args(
        self, source: Path, target: Path, import_source: bool
    ) -> argparse.Namespace:
        return argparse.Namespace(
            source=str(source),
            id="sample",
            title="Sample",
            work=str(target),
            source_lang="en",
            target_lang=["zh-CN"],
            format="markdown",
            target=["html"],
            import_source=import_source,
        )

    def test_external_import_uses_new_work_assets_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            base = Path(directory)
            root = base / "repository"
            root.mkdir()
            source = base / "original.pdf"
            source.write_bytes(b"%PDF-1.4\nread-only-original\n")
            target = root / "Works" / "sample"
            with (
                mock.patch.object(translator, "REPO_ROOT", root),
                mock.patch.object(translator, "mutool_pages", return_value=(3, "")),
            ):
                translator.command_init(self.init_args(source, target, True))
                copied = target / "assets" / "source" / source.name
                self.assertEqual(copied.read_bytes(), source.read_bytes())
                manifest = tomllib.loads(
                    (target / "manifest.toml").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    manifest["source"]["relative_path"],
                    "Works/sample/assets/source/original.pdf",
                )
                with self.assertRaisesRegex(translator.CliError, "不会覆盖"):
                    translator.command_init(self.init_args(source, target, True))
                self.assertEqual(copied.read_bytes(), source.read_bytes())
                self.assertFalse((root / "Books").exists())

    def test_external_source_still_requires_import_flag(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            base = Path(directory)
            root = base / "repository"
            root.mkdir()
            source = base / "original.pdf"
            source.write_bytes(b"original")
            target = root / "Works" / "sample"
            with (
                mock.patch.object(translator, "REPO_ROOT", root),
                self.assertRaisesRegex(translator.CliError, "import-source"),
            ):
                translator.command_init(self.init_args(source, target, False))
            self.assertFalse(target.exists())
