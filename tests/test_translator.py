from __future__ import annotations

import contextlib
import html
import importlib.util
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
import warnings
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = ROOT / ".tmp" / "tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEST_TEMP_ROOT)
os.environ["TEMP"] = str(TEST_TEMP_ROOT)
os.environ["TMP"] = str(TEST_TEMP_ROOT)
SPEC = importlib.util.spec_from_file_location(
    "translator_cli", ROOT / "tools" / "translator.py"
)
assert SPEC and SPEC.loader
translator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = translator
SPEC.loader.exec_module(translator)
BROWSER_QA_SPEC = importlib.util.spec_from_file_location(
    "epub_browser_qa", ROOT / "tools" / "epub_browser_qa.py"
)
assert BROWSER_QA_SPEC and BROWSER_QA_SPEC.loader
epub_browser_qa = importlib.util.module_from_spec(BROWSER_QA_SPEC)
BROWSER_QA_SPEC.loader.exec_module(epub_browser_qa)
GLADTEX_FILTER_SPEC = importlib.util.spec_from_file_location(
    "gladtex_filter", ROOT / "tools" / "gladtex_filter.py"
)
assert GLADTEX_FILTER_SPEC and GLADTEX_FILTER_SPEC.loader
gladtex_filter = importlib.util.module_from_spec(GLADTEX_FILTER_SPEC)
GLADTEX_FILTER_SPEC.loader.exec_module(gladtex_filter)


TEST_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000"
    "b51c0c020000000b4944415478da6364f80f00010501012718e3660000"
    "000049454e44ae426082"
)


def write_test_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TEST_PNG)
    return path


class TranslatorTests(unittest.TestCase):
    def test_latex_source_page_line_normalization_preserves_semantics(self) -> None:
        self.assertEqual(
            translator.normalize_latex_source_page_lines(
                "前文。\n\\sourcepage{2}\n后文。\n"
            ),
            "前文。\\sourcepage{2}后文。\n",
        )
        self.assertEqual(
            translator.normalize_latex_source_page_lines(
                "前文。\\sourcepage{2}后文。\n\\sourcepage{3}%\n"
            ),
            "前文。\\sourcepage{2}后文。\n\\sourcepage{3}%\n",
        )
        self.assertEqual(
            translator.normalize_latex_source_page_lines(
                "previous word\n\\sourcepage{2}\nnext word\n"
            ),
            "previous word\\sourcepage{2}\nnext word\n",
        )
        self.assertEqual(
            translator.normalize_latex_source_page_lines(
                "% 注释\n\\sourcepage{2}\n后文\n"
            ),
            "% 注释\n\\sourcepage{2}\n后文\n",
        )

    def test_markdown_numbered_heading_integrity(self) -> None:
        translator.validate_markdown_numbered_headings(
            "# 第一章\n## 1.1 开始\n### *1.1.1 细节*\n## 1.2 继续\n"
            "# 附录\n### A.1 整体嵌套一层\n#### A.1.1 子节\n"
            "```markdown\n## 1.2 代码示例\n```\n",
            "合法底稿",
        )
        with self.assertRaisesRegex(translator.CliError, "编号标题 1.1.*重复"):
            translator.validate_markdown_numbered_headings(
                "# 第一章\n## 1.1 开始\n## 1.1 重复\n",
                "重复底稿",
            )
        with self.assertRaises(translator.CliError) as caught:
            translator.validate_markdown_numbered_headings(
                "# 第一章\n## 1.1 开始\n## 1.1.1 错级\n"
                "## 1.2 继续\n## 1.2.1 也错级\n",
                "错级底稿",
            )
        self.assertIn("编号标题 1.1.1 使用 2 级", str(caught.exception))
        self.assertIn("编号标题 1.2.1 使用 2 级", str(caught.exception))
        with self.assertRaises(translator.CliError) as caught:
            translator.validate_markdown_numbered_headings(
                "# 第一章\n## 1.1 开始\n## 1.1.1 错级\n## 1.1 重复\n",
                "混合底稿",
            )
        self.assertLess(
            str(caught.exception).index("第 3 行"),
            str(caught.exception).index("第 4 行"),
        )

    def test_markdown_conflict_markers_are_allowed_only_in_fenced_examples(
        self,
    ) -> None:
        self.assertEqual(
            translator.markdown_conflict_marker_lines(
                "~~~text\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> topic\n~~~\n"
            ),
            [],
        )
        self.assertEqual(
            translator.markdown_conflict_marker_lines(
                "# 正文\n<<<<<<< HEAD\n未解决\n=======\n内容\n>>>>>>> topic\n"
            ),
            [2, 6],
        )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_markdown_structure_rejects_page_marker_broken_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            work = translator.Work(
                root,
                root,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (translator.Language("zh-CN", "source", entry, None, {}),),
            )
            with self.assertRaisesRegex(translator.CliError, "破坏列表结构"):
                translator.validate_markdown_structure(
                    work,
                    work.languages[0],
                    '- 第一项\n- 第二项\n\n'
                    '[]{.source-page data-page="1"}\n'
                    '- 第三项\n- 第四项\n',
                )
            with self.assertRaisesRegex(translator.CliError, "破坏列表结构"):
                translator.validate_markdown_structure(
                    work,
                    work.languages[0],
                    '1. 第一项\n2. 第二项\n\n'
                    '[]{.source-page data-page="1"}\n'
                    '3. 第三项\n4. 第四项\n',
                )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_markdown_structure_allows_single_manual_caption_with_image_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            work = translator.Work(
                root,
                root,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (translator.Language("zh-CN", "source", entry, None, {}),),
            )
            translator.validate_markdown_structure(
                work,
                work.languages[0],
                '![图 1.1 示例](assets/figure.png){#fig-example}[]{.image-tail}\n\n'
                '**图 1.1** 示例\n',
            )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_markdown_structure_traces_only_explicit_figure_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            work = translator.Work(
                root,
                root,
                {"work": {"status": "active"}},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (translator.Language("zh-CN", "source", entry, None, {}),),
            )
            language = work.languages[0]
            self.assertEqual(
                translator.validate_markdown_structure(
                    work,
                    language,
                    "![无编号示意图](assets/plain.png)\n\n普通说明。\n\n"
                    "![图 8.3 左图](assets/left.png){#fig-left}\n\n"
                    "![图 8.4 右图](assets/right.png){#fig-right}\n\n"
                    "*图 8.3、8.4 两种结果*\n",
                ),
                [],
            )
            incomplete = (
                "![结果分布](assets/result.png)\n\n**图 1.1** 结果分布\n"
            )
            with self.assertRaisesRegex(translator.CliError, "原图号 1.1"):
                translator.validate_markdown_structure(work, language, incomplete)
            with self.assertRaisesRegex(translator.CliError, "#fig-"):
                translator.validate_markdown_structure(
                    work,
                    language,
                    "![图 1.1 结果分布](assets/result.png)\n\n"
                    "**图 1.1** 结果分布\n",
                )
            with self.assertRaisesRegex(translator.CliError, "2 幅图片、3 个图号"):
                translator.validate_markdown_structure(
                    work,
                    language,
                    "![图 1.1 左图](assets/left.png){#fig-left}\n\n"
                    "![图 1.2 右图](assets/right.png){#fig-right}\n\n"
                    "*图 1.1、1.2、1.3 三种结果*\n",
                )
            work.manifest["work"]["status"] = "complete"
            self.assertEqual(
                translator.validate_markdown_structure(work, language, incomplete),
                ["zh-CN: 1 幅既有编号图追溯候选"],
            )

    def test_epub_structure_problems_detect_duplicate_caption_and_list_fragment(self) -> None:
        root = ET.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<ul><li>第一项</li></ul>'
            '<p>- 第二项 - 第三项</p>'
            '<figure><img alt="图 1.1 示例"/><figcaption>图 1.1 示例</figcaption></figure>'
            '<p>图中标签。</p><p><strong>图 1.1</strong> 示例</p>'
            '</body></html>'
        )
        problems = translator.epub_structure_problems(root)
        self.assertIn("列表项目被解析成普通段落", problems)
        self.assertIn("自动图注未移除：图 1.1 示例", problems)
        self.assertIn("图注重复：图 1.1 示例", problems)

    def test_remove_redundant_figure_caption(self) -> None:
        root = ET.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<figure><img alt="图 1.1 示例"/><figcaption>图 1.1 示例</figcaption></figure>'
            '<figure><img alt="图 1.2 示例"/><figcaption>另一条说明</figcaption></figure>'
            '</body></html>'
        )
        self.assertEqual(translator.remove_redundant_figure_captions(root), 1)
        self.assertEqual(len(root.findall(".//{http://www.w3.org/1999/xhtml}figcaption")), 1)

    def test_source_image_closure_uses_embedded_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            entry = work_path / "zh-CN" / "main.md"
            asset = work_path / "assets" / "figure.png"
            entry.parent.mkdir(parents=True)
            asset.parent.mkdir(parents=True)
            entry.write_text("![图](../assets/figure.png)\n", encoding="utf-8")
            asset.write_bytes(b"original-image")
            work = translator.Work(
                root,
                work_path,
                {"source": {"images": {"mode": "embedded"}}},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "Books" / "sample.pdf",
                1,
                (translator.Language("zh-CN", "source", entry, None, {}),),
            )
            self.assertEqual(translator.source_raster_assets(work), (asset.resolve(),))
            with mock.patch.object(
                translator, "pdf_image_hashes", return_value={translator.sha256(asset)}
            ):
                message = translator.validate_source_pdf_images(work)
            self.assertIn("1 raster assets matched", message or "")
            with (
                mock.patch.object(translator, "pdf_image_hashes", return_value=set()),
                self.assertRaisesRegex(translator.CliError, "来源图像闭包失败"),
            ):
                translator.validate_source_pdf_images(work)

    def test_doc_entrypoint_check_reports_stale_local_python_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "tools").mkdir()
            (root / "tools" / "present.py").write_text("", encoding="utf-8")
            (root / "README.md").write_text(
                "`tools/present.py`\n`tools/missing.py`\n", encoding="utf-8"
            )
            errors = translator.doc_entrypoint_errors(root)
            self.assertEqual(len(errors), 1)
            self.assertIn("README.md:2", errors[0])
            self.assertIn("tools/missing.py", errors[0])

    def test_load_command_work_binds_workflow_context(self) -> None:
        language = types.SimpleNamespace(
            code="zh-CN",
            outputs={"pdf": Path("book.pdf"), "epub": Path("book.epub")},
        )
        work = types.SimpleNamespace(
            work_id="sample-id",
            authoring_format="markdown",
            source_kind="pdf",
            source_pages=12,
            languages=(language,),
        )
        args = types.SimpleNamespace(work="Works/sample")
        with mock.patch.object(translator, "load_work", return_value=work):
            self.assertIs(translator.load_command_work(args), work)
        self.assertEqual(args._workflow_work_id, "sample-id")
        self.assertEqual(args._workflow_authoring_format, "markdown")
        self.assertEqual(args._workflow_source_kind, "pdf")
        self.assertEqual(args._workflow_source_count, 12)
        self.assertEqual(args._workflow_targets, ["zh-CN:epub", "zh-CN:pdf"])

    def test_compare_epub_content_equivalence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write_epub(path: Path, entries: list[tuple[str, str]]) -> None:
                with zipfile.ZipFile(path, "w") as archive:
                    for name, content in entries:
                        archive.writestr(name, content)

            official = root / "official.epub"
            identical = root / "identical.epub"
            bookmarked = root / "bookmarked.epub"
            changed_bookmark = root / "changed-bookmark.epub"
            changed_timestamp = root / "changed-timestamp.epub"
            changed = root / "changed.epub"
            missing = root / "missing.epub"
            duplicate = root / "duplicate.epub"
            opf = (
                '<package xmlns="http://www.idpf.org/2007/opf" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<metadata><dc:date id="epub-date">2026-08-01T00:00:00Z</dc:date>'
                '<meta property="dcterms:modified">2026-08-01T00:00:00Z</meta>'
                "</metadata></package>"
            )
            entries = [
                ("mimetype", "application/epub+zip"),
                ("EPUB/chapter.xhtml", "<p>正文</p>"),
                ("EPUB/content.opf", opf),
            ]
            write_epub(official, entries)
            write_epub(identical, entries)
            write_epub(
                bookmarked,
                [*entries, ("META-INF/calibre_bookmarks.txt", "bookmark")],
            )
            write_epub(
                changed_bookmark,
                [*entries, ("META-INF/calibre_bookmarks.txt", "other bookmark")],
            )
            write_epub(
                changed_timestamp,
                [
                    entries[0],
                    entries[1],
                    ("EPUB/content.opf", opf.replace("00:00:00", "01:02:03")),
                ],
            )
            write_epub(
                changed,
                [
                    entries[0],
                    ("EPUB/chapter.xhtml", "<p>改动</p>"),
                    entries[2],
                ],
            )
            write_epub(missing, [entries[0], entries[2]])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                write_epub(duplicate, [entries[0], entries[0]])

            def compare(
                candidate: Path, official_source: Path = official
            ) -> tuple[int, dict[str, object]]:
                parsed = translator.parser().parse_args(
                    ["compare-epub", str(official_source), str(candidate)]
                )
                self.assertIs(parsed.handler, translator.command_compare_epub)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = parsed.handler(parsed)
                return code, json.loads(output.getvalue())

            code, report = compare(identical)
            self.assertEqual(code, 0)
            self.assertTrue(report["package_identical"])
            self.assertTrue(report["content_equivalent"])
            self.assertFalse(report["reader_state_only"])

            code, report = compare(bookmarked)
            self.assertEqual(code, 0)
            self.assertFalse(report["package_identical"])
            self.assertTrue(report["content_equivalent"])
            self.assertTrue(report["reader_state_only"])
            self.assertEqual(
                report["reader_state_members"][0]["path"],
                "META-INF/calibre_bookmarks.txt",
            )

            code, report = compare(changed_bookmark, bookmarked)
            self.assertEqual(code, 0)
            self.assertTrue(report["content_equivalent"])
            self.assertTrue(report["reader_state_only"])

            code, report = compare(changed_timestamp)
            self.assertEqual(code, 0)
            self.assertTrue(report["content_equivalent"])
            self.assertTrue(report["volatile_metadata_only"])
            self.assertEqual(report["volatile_metadata_members"], ["EPUB/content.opf"])

            code, report = compare(changed)
            self.assertEqual(code, 1)
            self.assertFalse(report["content_equivalent"])
            self.assertEqual(
                report["different_members"][0]["path"], "EPUB/chapter.xhtml"
            )

            code, report = compare(missing)
            self.assertEqual(code, 1)
            self.assertEqual(report["missing_members"], ["EPUB/chapter.xhtml"])

            parsed = translator.parser().parse_args(
                ["compare-epub", str(official), str(duplicate)]
            )
            with self.assertRaisesRegex(translator.CliError, "重复成员"):
                parsed.handler(parsed)

    def test_epub_toc_outline_preserves_numbered_hierarchy(self) -> None:
        toc = ET.fromstring(
            """<nav xmlns="http://www.w3.org/1999/xhtml">
            <ol>
              <li><span>第一编</span><ol>
                <li><a href="ch1.xhtml#s1">1.1 基础</a></li>
                <li><a href="ch1.xhtml#s2">1.2 推导</a></li>
              </ol></li>
              <li><a href="appendix.xhtml">附录</a></li>
            </ol></nav>"""
        )
        self.assertEqual(
            translator.epub_toc_outline(toc),
            "1\t第一编\t\n"
            "2\t1.1 基础\tch1.xhtml#s1\n"
            "2\t1.2 推导\tch1.xhtml#s2\n"
            "1\t附录\tappendix.xhtml\n",
        )

    def test_finalize_reuses_handlers_and_binds_output_hashes(self) -> None:
        parsed = translator.parser().parse_args(
            ["finalize", "Works/sample", "--activity", "none"]
        )
        self.assertIs(parsed.handler, translator.command_finalize)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output_dir = work_path / "output"
            source = root / "Books" / "sample.pdf"
            output_dir.mkdir(parents=True)
            source.parent.mkdir()
            source.write_bytes(b"source")
            (work_path / "manifest.toml").write_text(
                'schema_version = 2\n\n[work]\nstatus = "active"\n',
                encoding="utf-8",
            )
            entry = work_path / "main.md"
            entry.write_text("# Sample\n", encoding="utf-8")
            outputs = {
                "pdf": output_dir / "sample.pdf",
                "epub": output_dir / "sample.epub",
            }
            language = translator.Language(
                "zh-CN", "translation", entry, None, outputs
            )
            work = translator.Work(
                root,
                work_path,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                source,
                1,
                (language,),
            )
            refresh_marker = (
                translator.reset_evidence_dir(work, "refresh") / "refresh.json"
            )
            refresh_marker.write_text("old", encoding="utf-8")
            order: list[str] = []

            def build(_args: object) -> int:
                order.append("build")
                for target, output in outputs.items():
                    output.write_bytes(target.encode())
                return 0

            def qa(_args: object) -> int:
                order.append("qa")
                for target in outputs:
                    (translator.work_temp(work) / "qa" / f"zh-CN-{target}").mkdir(
                        parents=True
                    )
                return 0

            def browser(_args: object) -> int:
                order.append("browser-qa")
                (translator.work_temp(work) / "browser-qa" / "zh-CN").mkdir(
                    parents=True
                )
                return 0

            finalize_args = types.SimpleNamespace(work=work_path)
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                mock.patch.object(
                    translator,
                    "command_doctor",
                    side_effect=lambda _args: order.append("doctor") or 0,
                ),
                mock.patch.object(
                    translator,
                    "command_check",
                    side_effect=lambda _args: order.append("check") or 0,
                ),
                mock.patch.object(translator, "command_build", side_effect=build),
                mock.patch.object(translator, "command_qa", side_effect=qa),
                mock.patch.object(
                    translator, "command_browser_qa", side_effect=browser
                ),
            ):
                translator.command_finalize(finalize_args)

            self.assertEqual(
                order, ["doctor", "check", "build", "qa", "browser-qa"]
            )
            self.assertFalse(refresh_marker.exists())
            self.assertEqual(
                [step["name"] for step in finalize_args._workflow_steps],
                ["doctor", "check", "build", "qa", "browser-qa", "evidence"],
            )
            self.assertTrue(
                all(
                    step["status"] == "passed"
                    for step in finalize_args._workflow_steps
                )
            )
            final = json.loads(
                (translator.work_temp(work) / "final" / "final.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(final["source"]["sha256"], translator.sha256(source))
            self.assertEqual(
                [item["target"] for item in final["outputs"]], ["pdf", "epub"]
            )
            self.assertEqual(
                final["outputs"][1]["sha256"], translator.sha256(outputs["epub"])
            )
            self.assertEqual(
                final["manifest"]["build_sha256"],
                translator.manifest_build_sha256(work_path / "manifest.toml"),
            )
            self.assertTrue(final["outputs"][1]["browser_qa_evidence"])

    def test_finalize_failure_short_circuits_and_invalidates_old_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            source = root / "Books" / "sample.pdf"
            output = work_path / "output" / "sample.pdf"
            output.parent.mkdir(parents=True)
            source.parent.mkdir()
            source.write_bytes(b"source")
            (work_path / "manifest.toml").write_text("schema_version = 2\n")
            entry = work_path / "main.md"
            entry.write_text("# Sample\n", encoding="utf-8")
            language = translator.Language(
                "zh-CN", "translation", entry, None, {"pdf": output}
            )
            work = translator.Work(
                root,
                work_path,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                source,
                1,
                (language,),
            )
            stale = translator.reset_evidence_dir(work, "final") / "final.json"
            stale.write_text("{}\n")
            qa = mock.Mock()
            browser = mock.Mock()
            finalize_args = types.SimpleNamespace(work=work_path)
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                mock.patch.object(translator, "command_doctor", return_value=0),
                mock.patch.object(translator, "command_check", return_value=0),
                mock.patch.object(
                    translator,
                    "command_build",
                    side_effect=translator.CliError("build failed"),
                ),
                mock.patch.object(translator, "command_qa", qa),
                mock.patch.object(translator, "command_browser_qa", browser),
                self.assertRaisesRegex(translator.CliError, "build failed"),
            ):
                translator.command_finalize(finalize_args)
            qa.assert_not_called()
            browser.assert_not_called()
            self.assertFalse(stale.exists())
            self.assertEqual(
                [step["name"] for step in finalize_args._workflow_steps],
                ["doctor", "check", "build"],
            )
            self.assertEqual(finalize_args._workflow_steps[-1]["status"], "failed")

    def test_deliver_atomically_copies_current_final_output_and_writes_evidence(
        self,
    ) -> None:
        parsed = translator.parser().parse_args(
            [
                "deliver",
                "Works/sample",
                "--lang",
                "zh-CN",
                "--target",
                "epub",
                "--to",
                "delivery/sample.epub",
                "--activity",
                "delivery",
            ]
        )
        self.assertIs(parsed.handler, translator.command_deliver)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output = work_path / "output" / "sample.epub"
            source = root / "Books" / "sample.pdf"
            manifest = work_path / "manifest.toml"
            status = work_path / "STATUS.md"
            destination = root / "delivery" / "sample.epub"
            output.parent.mkdir(parents=True)
            source.parent.mkdir()
            destination.parent.mkdir()
            output.write_bytes(b"final epub")
            source.write_bytes(b"source")
            manifest.write_text(
                'schema_version = 2\n\n[work]\nstatus = "complete"\n',
                encoding="utf-8",
            )
            status.write_text("# 状态\n\n状态：local-accepted\n", encoding="utf-8")
            language = translator.Language(
                "zh-CN", "translation", work_path / "main.tex", None, {"epub": output}
            )
            work = translator.Work(
                root,
                work_path,
                {"work": {"status": "complete"}},
                "sample",
                "Sample",
                "latex",
                None,
                source,
                1,
                (language,),
            )
            final_dir = translator.reset_evidence_dir(work, "final")
            (final_dir / "final.json").write_text(
                json.dumps(
                    {
                        "work_id": "sample",
                        "source": {
                            "path": str(source.resolve()),
                            "sha256": translator.sha256(source),
                        },
                        "manifest": {
                            "path": str(manifest.resolve()),
                            "sha256": translator.sha256(manifest),
                        },
                        "outputs": [
                            {
                                "language": "zh-CN",
                                "target": "epub",
                                "path": str(output.resolve()),
                                "bytes": output.stat().st_size,
                                "sha256": translator.sha256(output),
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            sibling_receipt = (
                translator.work_temp(work)
                / "delivery"
                / "zh-CN-pdf"
                / "delivery.json"
            )
            sibling_receipt.parent.mkdir(parents=True)
            sibling_receipt.write_text('{"existing": true}\n', encoding="utf-8")
            with mock.patch.object(translator, "load_work", return_value=work):
                translator.command_deliver(
                    types.SimpleNamespace(
                        work=work_path,
                        lang="zh-CN",
                        target="epub",
                        to=destination,
                    )
                )
            self.assertEqual(destination.read_bytes(), b"final epub")
            evidence = json.loads(
                (
                    translator.work_temp(work)
                    / "delivery"
                    / "zh-CN-epub"
                    / "delivery.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(evidence["status"], "complete")
            self.assertEqual(evidence["output"]["sha256"], translator.sha256(output))
            self.assertEqual(evidence["output"]["destination"], str(destination))
            self.assertFalse(
                (
                    translator.work_temp(work)
                    / "delivery"
                    / "zh-CN-epub"
                    / "attempt.json"
                ).exists()
            )
            self.assertEqual(
                json.loads(sibling_receipt.read_text(encoding="utf-8")),
                {"existing": True},
            )

    def test_deliver_failure_preserves_prior_receipt_and_records_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output = work_path / "output" / "sample.epub"
            source = root / "Books" / "sample.pdf"
            manifest = work_path / "manifest.toml"
            status = work_path / "STATUS.md"
            destination = root / "delivery" / "sample.epub"
            output.parent.mkdir(parents=True)
            source.parent.mkdir()
            destination.parent.mkdir()
            output.write_bytes(b"accepted")
            source.write_bytes(b"source")
            destination.write_bytes(b"old delivery")
            manifest.write_text(
                'schema_version = 2\n\n[work]\nstatus = "complete"\n',
                encoding="utf-8",
            )
            status.write_text("# 状态\n\n状态：local-accepted\n", encoding="utf-8")
            language = translator.Language(
                "zh-CN", "translation", work_path / "main.tex", None, {"epub": output}
            )
            work = translator.Work(
                root,
                work_path,
                {"work": {"status": "complete"}},
                "sample",
                "Sample",
                "latex",
                None,
                source,
                1,
                (language,),
            )
            final_dir = translator.reset_evidence_dir(work, "final")
            final_path = final_dir / "final.json"
            final_path.write_text(
                json.dumps(
                    {
                        "work_id": "sample",
                        "source": {"sha256": translator.sha256(source)},
                        "manifest": {"sha256": translator.sha256(manifest)},
                        "outputs": [
                            {
                                "language": "zh-CN",
                                "target": "epub",
                                "path": str(output.resolve()),
                                "bytes": output.stat().st_size,
                                "sha256": translator.sha256(output),
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            receipt = (
                translator.work_temp(work)
                / "delivery"
                / "zh-CN-epub"
                / "delivery.json"
            )
            receipt.parent.mkdir(parents=True)
            receipt.write_text('{"status": "complete", "old": true}\n', encoding="utf-8")
            real_publish = translator.publish_atomic

            def fail_external_publish(
                source_path: Path,
                target_path: Path,
                *,
                expected_bytes: int | None = None,
                expected_sha256: str | None = None,
            ) -> None:
                if target_path == destination:
                    raise translator.CliError("simulated delivery failure")
                real_publish(
                    source_path,
                    target_path,
                    expected_bytes=expected_bytes,
                    expected_sha256=expected_sha256,
                )

            with (
                mock.patch.object(translator, "load_work", return_value=work),
                mock.patch.object(
                    translator, "publish_atomic", side_effect=fail_external_publish
                ),
                self.assertRaisesRegex(translator.CliError, "simulated"),
            ):
                translator.command_deliver(
                    types.SimpleNamespace(
                        work=work_path,
                        lang="zh-CN",
                        target="epub",
                        to=destination,
                    )
                )
            self.assertEqual(destination.read_bytes(), b"old delivery")
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8")),
                {"status": "complete", "old": True},
            )
            attempt = json.loads(
                (receipt.parent / "attempt.json").read_text(encoding="utf-8")
            )
            self.assertEqual(attempt["status"], "prepared")

    def test_deliver_requires_completed_manifest_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output = work_path / "output" / "sample.epub"
            source = root / "Books" / "sample.pdf"
            manifest = work_path / "manifest.toml"
            status = work_path / "STATUS.md"
            destination = root / "delivery" / "sample.epub"
            output.parent.mkdir(parents=True)
            source.parent.mkdir()
            destination.parent.mkdir()
            output.write_bytes(b"accepted")
            source.write_bytes(b"source")
            manifest.write_text(
                'schema_version = 2\n\n[work]\nstatus = "active"\n',
                encoding="utf-8",
            )
            status.write_text("# 状态\n\n状态：local-accepted\n", encoding="utf-8")
            destination.write_bytes(b"old delivery")
            language = translator.Language(
                "zh-CN", "translation", work_path / "main.tex", None, {"epub": output}
            )
            work = translator.Work(
                root,
                work_path,
                {"work": {"status": "active"}},
                "sample",
                "Sample",
                "latex",
                None,
                source,
                1,
                (language,),
            )
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                self.assertRaisesRegex(translator.CliError, "先运行 complete"),
            ):
                translator.command_deliver(
                    types.SimpleNamespace(
                        work=work_path,
                        lang="zh-CN",
                        target="epub",
                        to=destination,
                    )
                )
            self.assertEqual(destination.read_bytes(), b"old delivery")

    def test_deliver_rejects_missing_or_stale_finalize_without_touching_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output = work_path / "output" / "sample.epub"
            source = root / "Books" / "sample.pdf"
            manifest = work_path / "manifest.toml"
            status = work_path / "STATUS.md"
            destination = root / "delivery" / "sample.epub"
            output.parent.mkdir(parents=True)
            source.parent.mkdir()
            destination.parent.mkdir()
            output.write_bytes(b"accepted")
            source.write_bytes(b"source")
            manifest.write_text(
                'schema_version = 2\n\n[work]\nstatus = "complete"\n',
                encoding="utf-8",
            )
            status.write_text("# 状态\n\n状态：local-accepted\n", encoding="utf-8")
            destination.write_bytes(b"old delivery")
            language = translator.Language(
                "zh-CN", "translation", work_path / "main.tex", None, {"epub": output}
            )
            work = translator.Work(
                root,
                work_path,
                {"work": {"status": "complete"}},
                "sample",
                "Sample",
                "latex",
                None,
                source,
                1,
                (language,),
            )
            args = types.SimpleNamespace(
                work=work_path,
                lang="zh-CN",
                target="epub",
                to=destination,
            )
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                self.assertRaisesRegex(translator.CliError, "请先运行 finalize"),
            ):
                translator.command_deliver(args)
            self.assertEqual(destination.read_bytes(), b"old delivery")

            final_dir = translator.reset_evidence_dir(work, "final")
            (final_dir / "final.json").write_text(
                json.dumps(
                    {
                        "work_id": "sample",
                        "source": {"sha256": translator.sha256(source)},
                        "manifest": {"sha256": translator.sha256(manifest)},
                        "outputs": [
                            {
                                "language": "zh-CN",
                                "target": "epub",
                                "path": str(output.resolve()),
                                "bytes": output.stat().st_size,
                                "sha256": translator.sha256(output),
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output.write_bytes(b"changed after finalize")
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                self.assertRaisesRegex(translator.CliError, "正式输出已变化"),
            ):
                translator.command_deliver(args)
            self.assertEqual(destination.read_bytes(), b"old delivery")

    def test_complete_reconciles_manifest_after_human_status_and_keeps_final_current(
        self,
    ) -> None:
        parsed = translator.parser().parse_args(
            ["complete", "Works/sample", "--activity", "delivery"]
        )
        self.assertIs(parsed.handler, translator.command_complete)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output = work_path / "output" / "sample.epub"
            source = root / "Books" / "sample.pdf"
            manifest = work_path / "manifest.toml"
            status = work_path / "STATUS.md"
            output.parent.mkdir(parents=True)
            source.parent.mkdir()
            output.write_bytes(b"accepted output")
            source.write_bytes(b"source")
            manifest.write_bytes(
                b'schema_version = 2\r\n\r\n[work]\r\nid = "sample"\r\n'
                b'title = "Sample"\r\nstatus = "active" # workflow\r\n'
            )
            status.write_text("# 状态\n\n状态：完成。\n", encoding="utf-8")
            language = translator.Language(
                "zh-CN", "translation", work_path / "main.tex", None, {"epub": output}
            )
            work = translator.Work(
                root,
                work_path,
                {"work": {"status": "active"}},
                "sample",
                "Sample",
                "latex",
                None,
                source,
                1,
                (language,),
            )
            final_dir = translator.reset_evidence_dir(work, "final")
            final_path = final_dir / "final.json"
            final_path.write_text(
                json.dumps(
                    {
                        "work_id": "sample",
                        "source": {"sha256": translator.sha256(source)},
                        "manifest": {
                            "sha256": translator.sha256(manifest),
                            "build_sha256": translator.manifest_build_sha256(
                                manifest
                            ),
                        },
                        "outputs": [
                            {
                                "language": "zh-CN",
                                "target": "epub",
                                "path": str(output.resolve()),
                                "bytes": output.stat().st_size,
                                "sha256": translator.sha256(output),
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(translator, "load_work", return_value=work):
                translator.command_complete(types.SimpleNamespace(work=work_path))

            self.assertEqual(
                tomllib.loads(manifest.read_text(encoding="utf-8"))["work"]["status"],
                "complete",
            )
            completed_manifest = manifest.read_bytes()
            self.assertNotIn(b"\n", completed_manifest.replace(b"\r\n", b""))
            self.assertIn(b'status = "complete" # workflow\r\n', completed_manifest)
            translator.load_current_final_summary(work)
            completion = json.loads(
                (
                    translator.work_temp(work) / "completion" / "completion.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(completion["manifest"]["after_sha256"], translator.sha256(manifest))
            self.assertEqual(len(completion["outputs"]), 1)

            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'title = "Sample"', 'title = "Changed"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(translator.CliError, "manifest 已变化"):
                translator.load_current_final_summary(work)

    def test_complete_rejects_unaccepted_status_or_changed_output_without_writing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output = work_path / "output" / "sample.epub"
            source = root / "Books" / "sample.pdf"
            manifest = work_path / "manifest.toml"
            status = work_path / "STATUS.md"
            output.parent.mkdir(parents=True)
            source.parent.mkdir()
            output.write_bytes(b"accepted output")
            source.write_bytes(b"source")
            active_manifest = (
                'schema_version = 2\n\n[work]\nid = "sample"\n'
                'title = "Sample"\nstatus = "active"\n'
            )
            manifest.write_text(active_manifest, encoding="utf-8")
            status.write_text("# 状态\n\n状态：进行中。\n", encoding="utf-8")
            language = translator.Language(
                "zh-CN", "translation", work_path / "main.tex", None, {"epub": output}
            )
            work = translator.Work(
                root,
                work_path,
                {"work": {"status": "active"}},
                "sample",
                "Sample",
                "latex",
                None,
                source,
                1,
                (language,),
            )
            args = types.SimpleNamespace(work=work_path)
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                self.assertRaisesRegex(translator.CliError, "不替代人工验收"),
            ):
                translator.command_complete(args)
            self.assertEqual(manifest.read_text(encoding="utf-8"), active_manifest)

            status.write_text("# 状态\n\n状态：完成。\n", encoding="utf-8")
            final_dir = translator.reset_evidence_dir(work, "final")
            (final_dir / "final.json").write_text(
                json.dumps(
                    {
                        "work_id": "sample",
                        "source": {"sha256": translator.sha256(source)},
                        "manifest": {
                            "sha256": translator.sha256(manifest),
                            "build_sha256": translator.manifest_build_sha256(
                                manifest
                            ),
                        },
                        "outputs": [
                            {
                                "language": "zh-CN",
                                "target": "epub",
                                "path": str(output.resolve()),
                                "bytes": output.stat().st_size,
                                "sha256": translator.sha256(output),
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output.write_bytes(b"changed after finalize")
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                self.assertRaisesRegex(translator.CliError, "正式输出已变化"),
            ):
                translator.command_complete(args)
            self.assertEqual(manifest.read_text(encoding="utf-8"), active_manifest)

            output.write_bytes(b"accepted output")
            (final_dir / "final.json").write_text(
                json.dumps(
                    {
                        "work_id": "sample",
                        "source": {"sha256": translator.sha256(source)},
                        "manifest": {
                            "sha256": translator.sha256(manifest),
                            "build_sha256": translator.manifest_build_sha256(
                                manifest
                            ),
                        },
                        "outputs": [
                            {
                                "language": "zh-CN",
                                "target": "epub",
                                "path": str(output.resolve()),
                                "bytes": output.stat().st_size,
                                "sha256": translator.sha256(output),
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            load_count = 0

            def change_output_after_manifest(_: object) -> translator.Work:
                nonlocal load_count
                load_count += 1
                if load_count == 2:
                    output.write_bytes(b"changed during completion")
                return work

            with (
                mock.patch.object(
                    translator, "load_work", side_effect=change_output_after_manifest
                ),
                self.assertRaisesRegex(translator.CliError, "正式输出已变化"),
            ):
                translator.command_complete(args)
            self.assertEqual(manifest.read_text(encoding="utf-8"), active_manifest)
            self.assertFalse(
                (
                    translator.work_temp(work) / "completion" / "completion.json"
                ).exists()
            )

    def test_workflow_benchmark_aggregates_atomic_logs(self) -> None:
        parsed = translator.parser().parse_args(["benchmark", "--work", "sample"])
        self.assertIs(parsed.handler, translator.command_benchmark)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = (
                {
                    "schema_version": 1,
                    "kind": "workflow-run",
                    "command": "check",
                    "revision": "aaa",
                    "work": "sample",
                    "status": "passed",
                    "duration_seconds": 1.0,
                },
                {
                    "schema_version": 1,
                    "kind": "workflow-run",
                    "command": "check",
                    "revision": "aaa",
                    "work": "sample",
                    "status": "failed",
                    "duration_seconds": 3.0,
                },
                {
                    "schema_version": 1,
                    "kind": "workflow-run",
                    "command": "finalize",
                    "revision": "bbb",
                    "work": "sample",
                    "work_id": "sample",
                    "status": "passed",
                    "duration_seconds": 10.0,
                    "steps": [
                        {
                            "name": "build",
                            "status": "passed",
                            "duration_seconds": 4.0,
                        }
                    ],
                },
            )
            with mock.patch.object(translator.time, "time_ns", return_value=1):
                for record in records:
                    translator.write_workflow_log(record, root)
            self.assertEqual(
                len(list(translator.workflow_runs_dir(root).glob("*.json"))), 3
            )

            stdout = io.StringIO()
            with (
                mock.patch.object(translator, "REPO_ROOT", root),
                contextlib.redirect_stdout(stdout),
            ):
                translator.command_benchmark(types.SimpleNamespace(work="sample"))
            report = json.loads(stdout.getvalue())
            check = next(
                row
                for row in report["commands"]
                if row["command"] == "check" and row["revision"] == "aaa"
            )
            build = report["finalize_steps"][0]
            self.assertEqual(report["samples"], 3)
            self.assertEqual(check["runs"], 2)
            self.assertEqual(check["success_rate"], 0.5)
            self.assertEqual(check["median_seconds"], 2.0)
            self.assertEqual(check["p95_seconds"], 3.0)
            self.assertEqual(build["step"], "build")
            self.assertEqual(build["median_seconds"], 4.0)
            self.assertEqual(report["steps"][0]["command"], "finalize")
            self.assertEqual(
                len(list(translator.workflow_runs_dir(root).glob("*.json"))), 3
            )
            self.assertFalse(any(translator.workflow_runs_dir(root).glob("*.tmp")))

    def test_work_commands_require_activity_and_station_records_interval(self) -> None:
        with self.assertRaises(SystemExit):
            translator.parser().parse_args(["check", "Works/sample"])
        parsed = translator.parser().parse_args(
            ["check", "Works/sample", "--activity", "none"]
        )
        self.assertEqual(parsed.activity, ["none"])
        boundary = translator.parser().parse_args(
            ["station", "--activity", "docs-process"]
        )
        self.assertEqual(boundary.work, "project")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = types.SimpleNamespace(
                work="Works/sample",
                activity=["structure-repair"],
                actor="agent",
                session="thread",
                scope="headings",
                outcome="fixed",
                issue=None,
                idle=False,
            )
            lane = translator.station_lane(args, root)
            previous_snapshot = {
                "Works/sample/zh/main.md": {
                    "path": "Works/sample/zh/main.md",
                    "status": " M",
                    "state": "present",
                    "bytes": 10,
                    "sha256": "OLD",
                }
            }
            translator.write_workflow_log(
                {
                    "schema_version": 2,
                    "kind": "workflow-run",
                    "run_id": "previous",
                    "started_at": "2026-08-02T00:00:00.000Z",
                    "ended_at": "2026-08-02T00:01:00.000Z",
                    "command": "check",
                    "status": "passed",
                    "duration_seconds": 1.0,
                    "station": {
                        "lane": lane,
                        "files": {"exit_snapshot": previous_snapshot},
                    },
                },
                root,
            )
            entry_snapshot = {
                "Works/sample/zh/main.md": {
                    "path": "Works/sample/zh/main.md",
                    "status": " M",
                    "state": "present",
                    "bytes": 12,
                    "sha256": "NEW",
                }
            }
            with mock.patch.object(
                translator,
                "workflow_dirty_snapshot",
                side_effect=[entry_snapshot, entry_snapshot],
            ):
                context = translator.start_workflow_station(
                    args,
                    root,
                    translator.datetime(2026, 8, 2, 0, 2, tzinfo=translator.timezone.utc),
                    "current",
                )
                station = translator.finish_workflow_station(context, root)
            self.assertEqual(station["previous_run_id"], "previous")
            self.assertEqual(station["interval"]["elapsed_seconds"], 60.0)
            self.assertEqual(
                station["interval"]["declared_effort_upper_bound_seconds"], 60.0
            )
            self.assertEqual(station["files"]["between"][0]["change"], "modified")
            self.assertNotIn("exit_snapshot", station["files"])
            translator.write_workflow_lane_state(
                root,
                context,
                {
                    "run_id": "current",
                    "ended_at": "2026-08-02T00:02:00.000Z",
                    "command": "check",
                    "status": "passed",
                    "revision": "abc",
                },
            )
            state = translator.read_workflow_lane_state(root, lane)
            self.assertEqual(state["exit_snapshot"], entry_snapshot)
            translator.close_workflow_station(context)
            self.assertFalse((translator.workflow_open_dir(root) / "current.json").exists())
            translator.write_open_station(
                root,
                "other",
                lane,
                translator.datetime(2026, 8, 2, 0, 2, tzinfo=translator.timezone.utc),
            )
            with mock.patch.object(
                translator,
                "workflow_dirty_snapshot",
                side_effect=[entry_snapshot, entry_snapshot],
            ):
                concurrent = translator.start_workflow_station(
                    args,
                    root,
                    translator.datetime(2026, 8, 2, 0, 3, tzinfo=translator.timezone.utc),
                    "concurrent",
                )
                concurrent_station = translator.finish_workflow_station(concurrent, root)
            try:
                self.assertIn(
                    "stale_open_station_recovered", concurrent_station["friction"]
                )
                self.assertNotIn(
                    "concurrent_open_station", concurrent_station["friction"]
                )
                self.assertFalse(
                    (translator.workflow_open_dir(root) / "other.json").exists()
                )
                self.assertEqual(
                    concurrent_station["interval"][
                        "declared_effort_upper_bound_seconds"
                    ],
                    60.0,
                )
            finally:
                translator.close_workflow_station(concurrent)

    def test_station_missing_snapshot_does_not_invent_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = types.SimpleNamespace(
                work="Works/sample",
                activity=["structure-repair"],
                actor="agent",
                session="thread",
                scope=None,
                outcome=None,
                issue=None,
                idle=False,
            )
            lane = translator.station_lane(args, root)
            translator.write_workflow_log(
                {
                    "schema_version": 2,
                    "kind": "workflow-run",
                    "run_id": "previous",
                    "ended_at": "2026-08-02T00:01:00.000Z",
                    "command": "check",
                    "status": "passed",
                    "station": {"lane": lane},
                },
                root,
            )
            dirty = {
                "Works/sample/zh/main.md": {
                    "path": "Works/sample/zh/main.md",
                    "status": " M",
                    "state": "present",
                    "bytes": 12,
                    "sha256": "NEW",
                }
            }
            with mock.patch.object(
                translator, "workflow_dirty_snapshot", return_value=dirty
            ):
                context = translator.start_workflow_station(
                    args,
                    root,
                    translator.datetime(2026, 8, 2, 0, 2, tzinfo=translator.timezone.utc),
                    "current",
                )
            self.assertEqual(context["station"]["files"]["between"], [])
            self.assertIn(
                "previous_snapshot_unavailable", context["station"]["friction"]
            )
            translator.close_workflow_station(context)

    def test_station_lane_lock_rejects_same_lane_until_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = types.SimpleNamespace(
                work="Works/sample",
                activity=["structure-repair"],
                actor="agent",
                session="thread",
                scope=None,
                outcome=None,
                issue=None,
                idle=False,
            )
            with (
                mock.patch.object(translator, "workflow_dirty_snapshot", return_value={}),
                mock.patch.object(translator, "workflow_revision", return_value=None),
            ):
                first = translator.start_workflow_station(
                    args,
                    root,
                    translator.datetime(2026, 8, 2, 0, 0, tzinfo=translator.timezone.utc),
                    "first",
                )
                try:
                    with self.assertRaises(translator.StationLaneBusy):
                        translator.start_workflow_station(
                            args,
                            root,
                            translator.datetime(
                                2026, 8, 2, 0, 1, tzinfo=translator.timezone.utc
                            ),
                            "second",
                        )
                finally:
                    translator.close_workflow_station(first)
                second = translator.start_workflow_station(
                    args,
                    root,
                    translator.datetime(2026, 8, 2, 0, 2, tzinfo=translator.timezone.utc),
                    "second-after-close",
                )
                translator.close_workflow_station(second)

    def test_manifest_status_alignment_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "STATUS.md").write_text("状态：已完成。\n", encoding="utf-8")
            work = types.SimpleNamespace(
                path=root,
                manifest={"work": {"status": "active"}},
            )
            with self.assertRaisesRegex(translator.CliError, "manifest/STATUS 状态漂移"):
                translator.validate_manifest_status_alignment(work)

    def test_status_document_recognizes_full_local_acceptance_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = root / "STATUS.md"
            status.write_text(
                "# 状态\n\n状态：full-local-accepted\n", encoding="utf-8"
            )
            self.assertEqual(translator.status_document_state(status), "complete")
            status.write_text(
                "# 状态\n\n状态：scoped-repair-accepted\n", encoding="utf-8"
            )
            self.assertIsNone(translator.status_document_state(status))
            for value in ("未验收", "incomplete", "not complete"):
                status.write_text(f"# Status\n\nstatus: {value}\n", encoding="utf-8")
                self.assertEqual(translator.status_document_state(status), "active")

    def test_prepare_runs_autocorrect_lint_then_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "zh-CN" / "main.md"
            entry.parent.mkdir()
            entry.write_text("# 标题\n", encoding="utf-8")
            work = types.SimpleNamespace(
                root=root,
                languages=(types.SimpleNamespace(code="zh-CN", entry=entry),),
            )
            args = types.SimpleNamespace(work="Works/sample")
            calls: list[object] = []
            with (
                mock.patch.object(translator, "load_command_work", return_value=work),
                mock.patch.object(
                    translator,
                    "run",
                    side_effect=lambda command: calls.append(command),
                ),
                mock.patch.object(
                    translator,
                    "command_check",
                    side_effect=lambda _args: calls.append("check") or 0,
                ),
            ):
                self.assertEqual(translator.command_prepare(args), 0)
            self.assertEqual(
                calls,
                [
                    ["autocorrect", "--fix", str(entry)],
                    ["autocorrect", "--lint", str(entry)],
                    "check",
                ],
            )

    def test_station_snapshot_consumes_both_rename_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "new.md").write_text("new\n", encoding="utf-8")
            result = types.SimpleNamespace(
                returncode=0,
                stdout=b" R new.md\0old.md\0",
            )
            with mock.patch.object(translator.subprocess, "run", return_value=result):
                snapshot = translator.workflow_dirty_snapshot(root)
            self.assertEqual(list(snapshot), ["new.md"])
            self.assertEqual(snapshot["new.md"]["state"], "present")

    def test_station_snapshot_classifies_deletions_and_restores(self) -> None:
        present = {"state": "present", "status": " M"}
        untracked = {"state": "present", "status": "??"}
        deleted = {"state": "deleted", "status": " D"}

        first_delete = translator.snapshot_changes({}, {"tracked.md": deleted})
        untracked_delete = translator.snapshot_changes({"draft.md": untracked}, {})
        restored = translator.snapshot_changes({"tracked.md": present}, {})

        self.assertEqual(first_delete[0]["change"], "deleted")
        self.assertEqual(untracked_delete[0]["change"], "deleted")
        self.assertEqual(restored[0]["change"], "restored")

    def test_station_reclassifies_committed_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "draft.md").write_text("committed\n", encoding="utf-8")
            args = types.SimpleNamespace(
                work="Works/sample",
                activity=["structure-repair"],
                actor="agent",
                session="thread",
                scope=None,
                outcome=None,
                issue=None,
                idle=False,
            )
            lane = translator.station_lane(args, root)
            translator.write_workflow_log(
                {
                    "schema_version": 2,
                    "kind": "workflow-run",
                    "run_id": "previous",
                    "ended_at": "2026-08-02T00:01:00.000Z",
                    "command": "check",
                    "status": "passed",
                    "revision": "old",
                    "station": {
                        "lane": lane,
                        "files": {
                            "exit_snapshot": {
                                "draft.md": {
                                    "path": "draft.md",
                                    "status": "??",
                                    "state": "present",
                                    "bytes": 6,
                                    "sha256": "OLD",
                                },
                                "removed.md": {
                                    "path": "removed.md",
                                    "status": "??",
                                    "state": "present",
                                    "bytes": 6,
                                    "sha256": "OLD",
                                }
                            }
                        },
                    },
                },
                root,
            )
            with (
                mock.patch.object(
                    translator, "workflow_dirty_snapshot", side_effect=[{}, {}]
                ),
                mock.patch.object(
                    translator, "workflow_revision", side_effect=["new", "new"]
                ),
                mock.patch.object(
                    translator, "inside", wraps=translator.inside
                ) as containment,
            ):
                context = translator.start_workflow_station(
                    args,
                    root,
                    translator.datetime(
                        2026, 8, 2, 0, 2, tzinfo=translator.timezone.utc
                    ),
                    "current",
                )
                station = translator.finish_workflow_station(context, root)
            try:
                self.assertEqual(
                    {
                        change["path"]: change["change"]
                        for change in station["files"]["between"]
                    },
                    {
                        "draft.md": "clean_or_committed",
                        "removed.md": "deleted",
                    },
                )
                containment.assert_has_calls(
                    [
                        mock.call(root, "draft.md", "工站快照路径"),
                        mock.call(root, "removed.md", "工站快照路径"),
                    ]
                )
            finally:
                translator.close_workflow_station(context)

    def test_station_label_rejects_absolute_paths(self) -> None:
        for value in ("C:/private/file", "/private/file", "contains spaces"):
            with self.subTest(value=value), self.assertRaises(
                translator.argparse.ArgumentTypeError
            ):
                translator.station_label(value)

    def test_station_lane_validates_environment_identity_without_echoing_it(self) -> None:
        args = types.SimpleNamespace(work="Works/sample", actor=None, session=None)
        private_value = "C:/private path"
        for name in ("CODEX_THREAD_ID", "CODEX_ACTOR_ID"):
            with (
                self.subTest(name=name),
                mock.patch.dict(os.environ, {name: private_value}, clear=True),
                self.assertRaises(translator.CliError) as caught,
            ):
                translator.station_lane(args, Path.cwd())
            self.assertNotIn(private_value, str(caught.exception))

    def test_refresh_runs_only_selected_output_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            source = root / "Books" / "sample.pdf"
            zh_output = work_path / "output" / "sample.zh.epub"
            en_output = work_path / "output" / "sample.en.pdf"
            languages = (
                translator.Language(
                    "en", "source", work_path / "en.md", None, {"pdf": en_output}
                ),
                translator.Language(
                    "zh-CN",
                    "translation",
                    work_path / "zh.md",
                    None,
                    {"epub": zh_output},
                ),
            )
            work = translator.Work(
                root,
                work_path,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                source,
                1,
                languages,
            )
            final_marker = translator.reset_evidence_dir(work, "final") / "final.json"
            final_marker.write_text("old", encoding="utf-8")
            args = types.SimpleNamespace(
                work=work_path,
                lang="zh-CN",
                target="epub",
            )
            order: list[str] = []

            def step(name: str):
                return lambda _args: order.append(name) or 0

            with (
                mock.patch.object(translator, "load_command_work", return_value=work),
                mock.patch.object(translator, "command_check", side_effect=step("check")),
                mock.patch.object(translator, "command_build", side_effect=step("build")),
                mock.patch.object(translator, "command_qa", side_effect=step("qa")),
                mock.patch.object(
                    translator, "command_browser_qa", side_effect=step("browser-qa")
                ),
                mock.patch.object(
                    translator,
                    "write_refresh_summary",
                    return_value=root / "refresh.json",
                ) as summary,
            ):
                self.assertEqual(translator.command_refresh(args), 0)
            self.assertEqual(order, ["check", "build", "qa", "browser-qa"])
            self.assertEqual(args._workflow_targets, ["zh-CN:epub"])
            self.assertFalse(final_marker.exists())
            jobs = summary.call_args.args[1]
            self.assertEqual([(job[0].code, job[1]) for job in jobs], [("zh-CN", "epub")])

    def test_target_evidence_reset_preserves_other_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = types.SimpleNamespace(root=root, path=root / "Works" / "sample")
            qa_root = translator.work_temp(work) / "qa"
            other = qa_root / "en-pdf"
            selected = qa_root / "zh-CN-epub"
            other.mkdir(parents=True)
            selected.mkdir()
            (other / "keep.txt").write_text("keep", encoding="utf-8")
            (selected / "stale.txt").write_text("stale", encoding="utf-8")
            reset = translator.reset_evidence_child(work, "qa", "zh-CN-epub")
            self.assertTrue((other / "keep.txt").is_file())
            self.assertEqual(list(reset.iterdir()), [])
            browser = translator.reset_evidence_child(
                work, "browser-qa", "zh-CN", create=False
            )
            self.assertFalse(browser.exists())

    def test_failed_refresh_invalidates_previous_success_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            output = work_path / "output" / "sample.epub"
            language = translator.Language(
                "zh-CN", "translation", work_path / "main.md", None, {"epub": output}
            )
            work = translator.Work(
                root,
                work_path,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "Books" / "sample.pdf",
                1,
                (language,),
            )
            previous = translator.reset_evidence_dir(work, "refresh") / "refresh.json"
            previous.write_text("old", encoding="utf-8")
            args = types.SimpleNamespace(
                work=work_path,
                lang="zh-CN",
                target="epub",
            )
            with (
                mock.patch.object(translator, "load_command_work", return_value=work),
                mock.patch.object(
                    translator,
                    "command_check",
                    side_effect=translator.CliError("failed"),
                ),
                self.assertRaises(translator.CliError),
            ):
                translator.command_refresh(args)
            self.assertFalse(previous.exists())

    def test_benchmark_includes_v2_station_activity_and_friction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translator.write_workflow_log(
                {
                    "schema_version": 2,
                    "kind": "workflow-run",
                    "run_id": "station",
                    "command": "check",
                    "revision": "abc",
                    "work": "sample",
                    "work_id": "sample-id",
                    "status": "failed",
                    "duration_seconds": 2.0,
                    "station": {
                        "activity": ["structure-repair"],
                        "friction": ["interval_exceeds_cap"],
                        "interval": {
                            "elapsed_seconds": 20.0,
                            "declared_effort_upper_bound_seconds": 0.0,
                        },
                    },
                },
                root,
            )
            stdout = io.StringIO()
            with (
                mock.patch.object(translator, "REPO_ROOT", root),
                contextlib.redirect_stdout(stdout),
            ):
                translator.command_benchmark(types.SimpleNamespace(work="sample"))
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["outcomes"][0]["status"], "failed")
            self.assertEqual(report["works"][0]["work_id"], "sample-id")
            self.assertEqual(
                report["stations"]["activities"][0]["activity"],
                "structure-repair",
            )
            self.assertEqual(
                report["stations"]["activities"][0]["machine"]["runs"], 1
            )
            self.assertEqual(report["stations"]["friction"][0]["code"], "interval_exceeds_cap")

    def test_main_logs_outcome_without_error_text(self) -> None:
        def fail(_args: object) -> int:
            raise translator.CliError("private detail")

        cases = (
            (lambda _args: 0, "passed", 0, None),
            (fail, "failed", 1, "CliError"),
        )
        for handler, status, exit_code, error_type in cases:
            with self.subTest(status=status):
                args = types.SimpleNamespace(
                    command="check",
                    work="Works/sample",
                    activity=["none"],
                    handler=handler,
                )
                argument_parser = mock.Mock()
                argument_parser.parse_args.return_value = args
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        translator, "parser", return_value=argument_parser
                    ),
                    mock.patch.object(
                        translator, "workflow_revision", return_value="abc123"
                    ),
                    mock.patch.object(
                        translator,
                        "start_workflow_station",
                        return_value={
                            "marker": Path("open.json"),
                            "station": {"friction": []},
                            "previous_status": None,
                            "previous_command": None,
                        },
                    ),
                    mock.patch.object(
                        translator,
                        "finish_workflow_station",
                        return_value={"friction": []},
                    ),
                    mock.patch.object(translator, "write_workflow_lane_state"),
                    mock.patch.object(translator, "close_workflow_station"),
                    mock.patch.object(translator, "write_workflow_log") as writer,
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(translator.main([]), exit_code)
                record = writer.call_args.args[0]
                self.assertEqual(record["status"], status)
                self.assertEqual(record["exit_code"], exit_code)
                self.assertEqual(record["error_type"], error_type)
                self.assertEqual(record["revision"], "abc123")
                self.assertEqual(record["work"], "sample")
                self.assertNotIn("private detail", json.dumps(record))

        args = types.SimpleNamespace(
            command="check",
            work="Works/sample",
            activity=["none"],
            handler=lambda _args: 0,
        )
        argument_parser = mock.Mock()
        argument_parser.parse_args.return_value = args
        stderr = io.StringIO()
        with (
            mock.patch.object(translator, "parser", return_value=argument_parser),
            mock.patch.object(
                translator, "workflow_revision", return_value="abc123"
            ),
            mock.patch.object(
                translator,
                "start_workflow_station",
                return_value={
                    "marker": Path("open.json"),
                    "station": {"friction": []},
                    "previous_status": None,
                    "previous_command": None,
                },
            ),
            mock.patch.object(
                translator,
                "finish_workflow_station",
                return_value={"friction": []},
            ),
            mock.patch.object(translator, "write_workflow_lane_state"),
            mock.patch.object(translator, "close_workflow_station"),
            mock.patch.object(
                translator, "write_workflow_log", side_effect=OSError("read-only")
            ),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(translator.main([]), 1)
        self.assertIn("ERROR: 无法写流程日志", stderr.getvalue())

    def test_main_rewrites_run_as_failed_when_lane_state_cannot_be_saved(self) -> None:
        args = types.SimpleNamespace(
            command="check",
            work="Works/sample",
            activity=["none"],
            handler=lambda _args: 0,
        )
        argument_parser = mock.Mock()
        argument_parser.parse_args.return_value = args
        context = {
            "marker": Path("open.json"),
            "station": {"friction": []},
            "previous_status": None,
            "previous_command": None,
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(translator, "parser", return_value=argument_parser),
            mock.patch.object(translator, "workflow_revision", return_value="abc123"),
            mock.patch.object(
                translator, "start_workflow_station", return_value=context
            ),
            mock.patch.object(
                translator,
                "finish_workflow_station",
                return_value=context["station"],
            ),
            mock.patch.object(
                translator,
                "write_workflow_lane_state",
                side_effect=OSError("read-only"),
            ),
            mock.patch.object(translator, "close_workflow_station") as closer,
            mock.patch.object(translator, "write_workflow_log") as writer,
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(translator.main([]), 1)

        self.assertEqual(writer.call_count, 2)
        record = writer.call_args.args[0]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["exit_code"], 1)
        self.assertEqual(record["error_type"], "StationError")
        self.assertIn("station_state_write_failed", record["station"]["friction"])
        closer.assert_called_once_with(context)
        self.assertIn("ERROR: 无法保存流程工站", stderr.getvalue())

    def test_main_refuses_work_when_station_cannot_start(self) -> None:
        handler = mock.Mock(return_value=0)
        args = types.SimpleNamespace(
            command="check",
            work="Works/sample",
            activity=["none"],
            handler=handler,
        )
        argument_parser = mock.Mock()
        argument_parser.parse_args.return_value = args
        stderr = io.StringIO()
        with (
            mock.patch.object(translator, "parser", return_value=argument_parser),
            mock.patch.object(
                translator,
                "start_workflow_station",
                side_effect=translator.CliError("unavailable"),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(translator.main([]), 1)
        handler.assert_not_called()
        self.assertIn("ERROR: 无法开始流程工站", stderr.getvalue())

    def test_main_records_actionable_friction_signals(self) -> None:
        def fail(_args: object) -> int:
            raise translator.CliError("private detail")

        args = types.SimpleNamespace(
            command="check",
            work="Works/sample",
            activity=["diagnosis"],
            outcome="blocked",
            handler=fail,
        )
        argument_parser = mock.Mock()
        argument_parser.parse_args.return_value = args
        context = {
            "marker": Path("open.json"),
            "station": {"friction": []},
            "previous_status": "failed",
            "previous_command": "check",
        }
        with (
            mock.patch.object(translator, "parser", return_value=argument_parser),
            mock.patch.object(
                translator, "start_workflow_station", return_value=context
            ),
            mock.patch.object(
                translator,
                "finish_workflow_station",
                return_value={"friction": []},
            ),
            mock.patch.object(translator, "write_workflow_lane_state"),
            mock.patch.object(translator, "close_workflow_station"),
            mock.patch.object(translator.time, "perf_counter", side_effect=[0.0, 121.0]),
            mock.patch.object(translator, "write_workflow_log") as writer,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(translator.main([]), 1)
        friction = writer.call_args.args[0]["station"]["friction"]
        self.assertEqual(
            friction,
            [
                "blocked_activity",
                "command_failed",
                "failed_wait_over_120s",
                "repeated_failure",
            ],
        )

    def test_check_and_build_reject_only_complete_tool_truncation_signatures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "main.md"
            language = types.SimpleNamespace(code="zh-CN", entry=entry, outputs={})
            work = types.SimpleNamespace(
                work_id="sample",
                authoring_format="markdown",
                source_kind="pdf",
                source_pages=1,
                languages=(language,),
            )
            positives = (
                "前文\nWarning: truncated output (original token count: 1,234)\n后文",
                "前文\nTotal output lines: 180[]{.source-page}\n后文",
                "前文\n…4,485 tokens truncated…\n后文",
                "前文\n...1 token truncated...\n后文",
            )
            for text in positives:
                with self.subTest(text=text):
                    entry.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(
                        translator.CliError, r"zh-CN 第 2 行包含工具截断签名"
                    ):
                        translator.validate_tool_truncation_entries(work)

            entry.write_text(
                "普通省略号……不会失败。\n"
                "The response was truncated naturally.\n"
                "Total output lines:\n"
                "...tokens truncated...\n",
                encoding="utf-8",
            )
            translator.validate_tool_truncation_entries(work)

            with mock.patch.object(translator, "load_work", return_value=work):
                for command, args in (
                    (translator.command_check, types.SimpleNamespace(work="sample")),
                    (
                        translator.command_build,
                        types.SimpleNamespace(work="sample", lang="all", target="all"),
                    ),
                ):
                    with self.subTest(command=command.__name__):
                        entry.write_text(
                            "…9 tokens truncated…", encoding="utf-8"
                        )
                        with self.assertRaisesRegex(
                            translator.CliError, r"zh-CN 第 1 行包含工具截断签名"
                        ):
                            command(args)

    def test_navigation_formula_images_reject_unknown_tex(self) -> None:
        root = ET.fromstring(
            '<nav xmlns="http://www.w3.org/1999/xhtml">'
            '<span><img class="math-inline" alt="\\alpha"/></span></nav>'
        )
        with self.assertRaisesRegex(translator.CliError, "导航公式无法安全转为文本"):
            translator.replace_navigation_formula_images(root)

    def test_navigation_formula_images_convert_allowlisted_tex(self) -> None:
        root = ET.fromstring(
            '<nav xmlns="http://www.w3.org/1999/xhtml">'
            '<span><img class="math-inline" alt="\\mathcal L_A"/>-完备性</span>'
            '<span><img class="math-inline" alt="\\Sigma_1"/> 真相</span>'
            '</nav>'
        )
        self.assertEqual(translator.replace_navigation_formula_images(root), 2)
        self.assertEqual("".join(root.itertext()), "L_A-完备性Σ₁ 真相")

    def test_repair_split_fragment_links_targets_owning_xhtml(self) -> None:
        documents = {
            "EPUB/text/ch001.xhtml": ET.fromstring(
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                '<a href="#fig-1">Figure</a></body></html>'
            ),
            "EPUB/text/ch002.xhtml": ET.fromstring(
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                '<figure id="fig-1"/></body></html>'
            ),
        }
        self.assertEqual(translator.repair_split_fragment_links(documents), 1)
        link = next(documents["EPUB/text/ch001.xhtml"].iter("{http://www.w3.org/1999/xhtml}a"))
        self.assertEqual(link.attrib["href"], "ch002.xhtml#fig-1")

    def test_epub_footnote_backlinks_cover_every_reference(self) -> None:
        epub_type = f"{{{translator.EPUB_NAMESPACE}}}type"
        document = ET.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
            '<a id="r1" href="#n1" epub:type="noteref">1</a>'
            '<a id="r2" href="#n1" epub:type="noteref">1</a>'
            '<aside id="n1" epub:type="footnote"><p>Note</p></aside>'
            "</body></html>"
        )
        self.assertEqual(translator.add_epub_footnote_backlinks(document), 2)
        backlinks = {
            element.attrib["href"]
            for element in document.iter()
            if "backlink" in element.attrib.get(epub_type, "").split()
        }
        self.assertEqual(backlinks, {"#r1", "#r2"})
        self.assertEqual(translator.add_epub_footnote_backlinks(document), 0)

    def test_browser_qa_harness_is_serial_explicitly_started_and_never_cleans_global_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "qa" / "harness.html"
            paths = epub_browser_qa.write_harness(
                harness, ["EPUB/a.xhtml", "EPUB/b.xhtml"], 390, 844, 30
            )
            text = harness.read_text(encoding="utf-8")
            self.assertEqual(paths, ["../EPUB/a.xhtml", "../EPUB/b.xhtml"])
            self.assertIn("for (const page of pages)", text)
            self.assertIn("frame.src = path", text)
            self.assertIn("window.startQA = () =>", text)
            self.assertNotIn('window.addEventListener("load"', text)
            self.assertIn('getAttributeNS("http://www.idpf.org/2007/ops", "type")', text)
            self.assertIn("overflowElements", text)
            self.assertIn("verifyFootnoteNavigation", text)
            self.assertIn("noterefTargetChecks", text)
            self.assertIn("validPairs.length - 1", text)
            self.assertIn("missingFragmentTargets", text)
            self.assertIn("=== reference.id", text)
        source = (ROOT / "tools" / "epub_browser_qa.py").read_text(encoding="utf-8")
        for forbidden in ("close --all", "Stop-Process", "taskkill", "Get-Process"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"session 未关闭：namespace=', source)
        self.assertIn('["eval", "startQA()"]', source)

    def test_browser_qa_rejects_rendering_defects(self) -> None:
        state = {
            "errors": [],
            "results": [
                {
                    "path": "../EPUB/a.xhtml",
                    "viewport": [390, 844],
                    "brokenImages": 0,
                    "formulasWithoutAlt": 0,
                    "mathmlCount": 0,
                    "texAnnotationCount": 0,
                    "overflowCount": 1,
                    "overflowWidth": 90,
                    "overflowElements": [
                        {
                            "selector": "table",
                            "rect": [4, 480, 476],
                            "clientWidth": 476,
                            "scrollWidth": 476,
                        }
                    ],
                }
            ],
        }
        failures = epub_browser_qa.result_failures(
            state, ["../EPUB/a.xhtml"], 390, 844
        )
        self.assertEqual(
            failures,
            [
                "../EPUB/a.xhtml: overflowCount=1 overflowWidth=90 "
                "offenders=table rect=[4, 480, 476] scroll=476/476"
            ],
        )

    def test_browser_qa_rejects_link_and_footnote_navigation_defects(self) -> None:
        state = {
            "errors": [],
            "results": [
                {
                    "path": "../EPUB/a.xhtml",
                    "viewport": [390, 844],
                    "missingXhtmlTargets": 1,
                    "missingFragmentTargets": 2,
                    "noterefTargetFailures": 3,
                    "backlinkTargetFailures": 4,
                    "noterefNavigationFailures": 5,
                    "backlinkNavigationFailures": 6,
                }
            ],
        }
        failures = epub_browser_qa.result_failures(
            state, ["../EPUB/a.xhtml"], 390, 844
        )
        self.assertEqual(
            failures,
            [
                "../EPUB/a.xhtml: missingXhtmlTargets=1",
                "../EPUB/a.xhtml: missingFragmentTargets=2",
                "../EPUB/a.xhtml: noterefTargetFailures=3",
                "../EPUB/a.xhtml: backlinkTargetFailures=4",
                "../EPUB/a.xhtml: noterefNavigationFailures=5",
                "../EPUB/a.xhtml: backlinkNavigationFailures=6",
            ],
        )

    def test_browser_qa_launch_does_not_capture_daemon_pipes(self) -> None:
        completed = subprocess.CompletedProcess(["agent-browser"], 0)
        with mock.patch.object(
            epub_browser_qa.subprocess, "run", return_value=completed
        ) as run:
            with mock.patch.dict(
                os.environ, {"TRANSLATOR_AGENT_BROWSER": "custom-browser"}
            ):
                epub_browser_qa.run_agent(
                    "translator-test", "qa", ["open", "about:blank"], capture=False
                )
        self.assertEqual(run.call_args.args[0][0], "custom-browser")
        self.assertIs(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("capture_output", run.call_args.kwargs)

    def test_project_temp_root_does_not_use_system_temp(self) -> None:
        self.assertEqual(
            translator.temp_root(),
            (ROOT / ".tmp" / "translator").resolve(),
        )
        result = subprocess.CompletedProcess(["tool"], 0, stdout="", stderr="")
        with mock.patch.object(
            translator.subprocess, "run", return_value=result
        ) as run:
            translator.run(["tool"])
        process_env = run.call_args.kwargs["env"]
        self.assertEqual(
            Path(process_env["TEMP"]),
            (ROOT / ".tmp" / "translator").resolve(),
        )
        self.assertEqual(process_env["TEMP"], process_env["TMP"])

    def test_work_temp_and_clean_are_isolated_by_work_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = types.SimpleNamespace(
                root=root,
                path=root / "Works" / "first",
                work_id="first",
                authoring_format="markdown",
                source_kind="pdf",
                source_pages=1,
                languages=(),
            )
            second = types.SimpleNamespace(root=root, path=root / "Works" / "second")
            first_temp = translator.work_temp(first)
            second_temp = translator.work_temp(second)
            self.assertNotEqual(first_temp, second_temp)

            first_temp.mkdir(parents=True)
            second_temp.mkdir(parents=True)
            (first_temp / "evidence.txt").write_text("first", encoding="utf-8")
            (second_temp / "evidence.txt").write_text("second", encoding="utf-8")
            log = translator.write_workflow_log(
                {"schema_version": 1, "kind": "workflow-run"}, root
            )

            with mock.patch.object(translator, "load_work", return_value=first):
                translator.command_clean(types.SimpleNamespace(work="first"))

            self.assertFalse(first_temp.exists())
            self.assertEqual(
                (second_temp / "evidence.txt").read_text(encoding="utf-8"),
                "second",
            )
            self.assertTrue(log.is_file())

    def test_epub_css_preserves_reader_settings_and_code_contrast(self) -> None:
        css = (ROOT / "formats" / "epub" / "book.css").read_text(encoding="utf-8")
        root_rule = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
        body_rule = re.search(r"(?:^|\n)body\s*\{([^}]*)\}", css, re.DOTALL)
        self.assertIsNotNone(root_rule)
        self.assertIsNotNone(body_rule)
        self.assertIn("color-scheme: light dark", root_rule.group(1))
        self.assertNotIn("font-family", root_rule.group(1))
        self.assertNotRegex(body_rule.group(1), r"(?:^|[;\s])color\s*:")
        self.assertNotRegex(body_rule.group(1), r"background(?:-color)?\s*:")

        light_css, dark_css = css.split(
            "@media (prefers-color-scheme: dark)",
            maxsplit=1,
        )
        token_pattern = re.compile(
            r"(?:^|\n)\s*code span\.[^{}]+\{[^{}]*"
            r"color:\s*(#[0-9a-fA-F]{6})",
            re.DOTALL,
        )
        palettes = (
            (token_pattern.findall(light_css), "#f3f6f7"),
            (token_pattern.findall(dark_css), "#182226"),
        )

        def luminance(color: str) -> float:
            channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                for value in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        for colors, background in palettes:
            self.assertGreaterEqual(len(colors), 8)
            background_luminance = luminance(background)
            for color in colors:
                foreground_luminance = luminance(color)
                ratio = (max(foreground_luminance, background_luminance) + 0.05) / (
                    min(foreground_luminance, background_luminance) + 0.05
                )
                self.assertGreaterEqual(ratio, 4.5, color)
        self.assertIn("img.math-inline", css)
        self.assertIn("img.math-display", css)
        self.assertNotRegex(css, r"img\.math-inline\s*\{[^}]*width:\s*auto")
        self.assertRegex(css, r"img\.math-inline\s*\{[^}]*height:\s*auto")
        self.assertIn("background: #fff", light_css)
        self.assertIn("background: #182226", dark_css)
        self.assertRegex(css, r"a\.uri\s*\{[^}]*overflow-wrap:\s*anywhere")
        self.assertRegex(css, r"pre\s*\{[^}]*overflow-wrap:\s*anywhere")
        self.assertRegex(css, r":not\(pre\) > code\s*\{[^}]*overflow-wrap:\s*anywhere")
        self.assertRegex(css, r"body\s*\{[^}]*padding-inline:\s*0\.25em")
        self.assertRegex(css, r"body\s*\{[^}]*overflow-wrap:\s*anywhere")
        self.assertIn(".source-image > img", css)
        self.assertIn("p > .inline-formula:only-child > img", css)

    def test_inline_formula_style_preserves_relative_dimensions(self) -> None:
        value = {
            "t": "RawInline",
            "c": [
                "html",
                '<img class="math-inline" width="8" height="8" '
                'style="vertical-align: -0.67px; margin: 0;" />',
            ],
        }
        self.assertEqual(gladtex_filter.replace_raw_images(value), 1)
        attributes = dict(value["c"][0][2])
        self.assertIn("height: 0.4800em", attributes["style"])
        self.assertIn("width: auto", attributes["style"])
        self.assertIn("vertical-align: -0.0402em", attributes["style"])
        delimiter = ET.fromstring(
            '<img width="17" height="17" '
            'style="vertical-align: -4.67px; margin: 0;" />'
        )
        gladtex_filter.relative_formula_style(
            delimiter, gladtex_filter.INLINE_MATH_EM_PER_PX
        )
        self.assertIn("height: 1.0200em", delimiter.attrib["style"])
        self.assertIn("vertical-align: -0.2802em", delimiter.attrib["style"])
        display = ET.fromstring(
            '<img width="114" height="17" '
            'style="vertical-align: -4.67px; margin: 0;" />'
        )
        gladtex_filter.relative_formula_style(
            display, gladtex_filter.DISPLAY_MATH_EM_PER_PX
        )
        self.assertIn("height: 1.7000em", display.attrib["style"])
        self.assertIn("vertical-align: -0.4670em", display.attrib["style"])

        display_raw = {
            "t": "RawInline",
            "c": [
                "html",
                '<img class="math-display" width="8" height="8" '
                'style="vertical-align: -0.67px; margin: 0;" />',
            ],
        }
        self.assertEqual(gladtex_filter.replace_raw_images(display_raw), 1)
        self.assertEqual(display_raw["t"], "Span")
        self.assertIn("display-formula", display_raw["c"][0][1])
        self.assertEqual(display_raw["c"][1][0]["t"], "Image")

        paragraph = {"t": "Para", "c": [display_raw]}
        self.assertEqual(gladtex_filter.replace_raw_images(paragraph), 0)
        self.assertEqual(paragraph["t"], "Div")
        self.assertIn("display-formula", paragraph["c"][0][1])

    def test_source_epub_formula_css_becomes_explicit_image_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_epub = root / "source.epub"
            with zipfile.ZipFile(source_epub, "w") as archive:
                archive.writestr(
                    "OEBPS/css/source.css",
                    "@media amzn-mobi {"
                    ".math-inline-13_Chapter02-math-055 {"
                    "width: 76px !important; height: 36px !important; "
                    "vertical-align: middle;}}",
                )
            source_json = root / "source.json"
            output_json = root / "output.json"
            build = root / "build"
            build.mkdir()
            source_json.write_text(
                json.dumps(
                    {
                        "blocks": [
                            {
                                "t": "Para",
                                "c": [
                                    {
                                        "t": "Image",
                                        "c": [
                                            [
                                                "",
                                                ["math-inline"],
                                                [["width", "49"], ["height", "23"]],
                                            ],
                                            [{"t": "Str", "c": "formula"}],
                                            [
                                                (
                                                    "../assets/source/"
                                                    "13_Chapter02-math-055.png"
                                                ),
                                                "",
                                            ],
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            translator.render_svg_math_ast(
                ROOT,
                root / "work",
                source_json,
                output_json,
                build,
                source_epub=source_epub,
            )
            document = json.loads(output_json.read_text(encoding="utf-8"))
            attributes = dict(document["blocks"][0]["c"][0]["c"][0][2])
            self.assertEqual(attributes["width"], "76")
            self.assertEqual(attributes["height"], "36")
            self.assertIn("height: 2.1600em", attributes["style"])
            self.assertIn("width: auto", attributes["style"])
            self.assertIn("vertical-align: middle", attributes["style"])

    def test_formula_svg_normalization_adds_theme_and_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            svg = Path(directory) / "formula.svg"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'viewBox="-1 -2 3 4"><defs/><path d="M0 0"/></svg>',
                encoding="utf-8",
            )
            translator.normalize_formula_svg(svg)
            root = ET.parse(svg).getroot()
            self.assertEqual(root.attrib["viewBox"], "-1 -2 3 4")
            text = "".join(root.itertext())
            self.assertIn("prefers-color-scheme:dark", text)
            self.assertIn("math-background", text)
            self.assertIn("math-foreground", text)
            background = next(
                element
                for element in root
                if element.attrib.get("class") == "math-background"
            )
            self.assertEqual(background.attrib["x"], "-1")
            self.assertEqual(background.attrib["width"], "3")

    def test_svg_math_cache_seeds_unambiguous_formula_from_epub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub = root / "old.epub"
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'viewBox="0 0 10 10"><path d="M0 0"/></svg>'
            )
            xhtml = (
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                '<img src="../media/file0.svg" class="math-inline" '
                'style="vertical-align: -2.00px; margin: 0;" '
                'width="10" height="11" alt="x" />'
                "</body></html>"
            )
            with zipfile.ZipFile(epub, "w") as archive:
                archive.writestr("EPUB/text/ch001.xhtml", xhtml)
                archive.writestr("EPUB/media/file0.svg", svg)
            document = {
                "blocks": [
                    {
                        "t": "Para",
                        "c": [{"t": "Math", "c": [{"t": "InlineMath"}, "x"]}],
                    }
                ]
            }
            cache_dir = root / "cache" / "formulas"
            self.assertEqual(
                translator.seed_svg_math_cache(epub, document, cache_dir),
                1,
            )
            cache = json.loads(
                (cache_dir / "gladtex.cache").read_text(encoding="utf-8")
            )
            self.assertEqual(cache["x"]["false"]["pos"]["depth"], 2.0)
            self.assertTrue((cache_dir / "eqn000.svg").is_file())

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in ("uv", "latex", "dvisvgm")),
        "SVG math tools are not installed",
    )
    def test_svg_math_cache_only_renders_unique_misses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()

            def render(formula: str, name: str) -> str:
                build = root / name
                build.mkdir()
                source = build / "source.json"
                output = build / "filtered.json"
                source.write_text(
                    json.dumps(
                        {
                            "pandoc-api-version": [1, 23, 1],
                            "meta": {},
                            "blocks": [
                                {
                                    "t": "Para",
                                    "c": [
                                        {
                                            "t": "Math",
                                            "c": [
                                                {"t": "InlineMath"},
                                                formula,
                                            ],
                                        },
                                        {"t": "Space"},
                                        {
                                            "t": "Image",
                                            "c": [
                                                ["", ["math-inline"], []],
                                                [{"t": "Str", "c": "source formula"}],
                                                ["../assets/source-formula.svg", ""],
                                            ],
                                        },
                                    ],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    translator.render_svg_math_ast(
                        ROOT,
                        work,
                        source,
                        output,
                        build,
                    )
                self.assertTrue(output.is_file())
                return stream.getvalue()

            self.assertIn("svg_math_cache_misses=1", render("x", "first"))
            second = render("x", "second")
            self.assertIn("svg_math_cache_hits=1", second)
            self.assertIn("svg_math_cache_misses=0", second)
            self.assertIn("svg_math_cache_misses=1", render("y", "third"))
            cache_dir = translator.svg_math_cache_dir(work)
            cache = json.loads(
                (cache_dir / "gladtex.cache").read_text(encoding="utf-8")
            )
            self.assertNotEqual(
                cache["x"]["false"]["path"],
                cache["y"]["false"]["path"],
            )
            corrupted = cache_dir / cache["y"]["false"]["path"]
            corrupted.write_text("not svg", encoding="utf-8")
            self.assertIn("svg_math_cache_misses=1", render("y", "fourth"))

    def test_svg_math_cache_checkpoints_valid_entries_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            build = root / "build"
            work.mkdir()
            build.mkdir()
            source = build / "source.json"
            output = build / "filtered.json"
            source.write_text(
                json.dumps(
                    {
                        "blocks": [
                            {
                                "t": "Para",
                                "c": [
                                    {"t": "Math", "c": [{"t": "InlineMath"}, "x"]}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def fail_gladtex(_command, *, cwd=None, env=None):
                self.assertIsNotNone(cwd)
                assert cwd is not None
                (cwd / "eqn000.svg").write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" '
                    'viewBox="0 0 10 10"><path d="M0 0"/></svg>',
                    encoding="utf-8",
                )
                (cwd / "gladtex.cache").write_text(
                    json.dumps(
                        {
                            "GladTeX__cache__version": "2.0",
                            "x": {
                                "false": {
                                    "pos": {"width": 10, "height": 10, "depth": 0},
                                    "path": "eqn000.svg",
                                }
                            },
                            "escape": {
                                "false": {
                                    "pos": {"width": 1, "height": 1, "depth": 0},
                                    "path": "../escape.svg",
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                raise translator.CliError("GladTeX failed")

            with mock.patch.object(translator, "run", side_effect=fail_gladtex):
                with self.assertRaisesRegex(translator.CliError, "GladTeX failed"):
                    translator.render_svg_math_ast(
                        ROOT, work, source, output, build
                    )

            cache_dir = translator.svg_math_cache_dir(work)
            cache = json.loads(
                (cache_dir / "gladtex.cache").read_text(encoding="utf-8")
            )
            self.assertEqual(set(cache), {"GladTeX__cache__version", "x"})
            self.assertTrue(
                translator.valid_formula_svg_data((cache_dir / "eqn000.svg").read_bytes())
            )
            self.assertFalse(output.exists())

    def test_inside_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "work"
            base.mkdir()
            with self.assertRaises(translator.CliError):
                translator.inside(base, "../outside", "test")
            self.assertEqual(
                translator.resolve_work_path("Works/sample", root),
                (root / "Works" / "sample").resolve(),
            )

    def test_typst_include_expands_literals_and_rejects_dynamic_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chapter.typ").write_text("#source-page(2)\n", encoding="utf-8")
            (root / "language").mkdir()
            entry = root / "language" / "main.typ"
            entry.write_text(
                '#source-page(1)\n#include "../chapter.typ"\n', encoding="utf-8"
            )
            expanded = translator.expand_typst(entry, root)
            self.assertEqual(
                translator.source_pages_for("typst", expanded),
                [1, 2],
            )
            masked = translator.mask_typst_comments(
                "#source-page(1)\n// #source-page(2)\n/* #source-page(3) */"
            )
            self.assertEqual(translator.source_pages_for("typst", masked), [1])
            entry.write_text(
                '#let path = "chapter.typ"\n#include path\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(translator.CliError, "动态"):
                translator.expand_typst(entry, root)

    def test_boundary_audit_finds_nonterminal_page_join_and_allows_formula(self) -> None:
        marker = {"t": "RawInline", "c": ["latex", r"\boundarypage{20}"]}
        document = {
            "blocks": [
                {
                    "t": "Para",
                    "c": [
                        {"t": "Str", "c": "传统"},
                        marker,
                        {"t": "Str", "c": "微观经济学"},
                    ],
                }
            ]
        }
        locations = [
            {"path": "zh-CN/ch02.tex", "line": 23, "column": 1, "page_marker": 20}
        ]
        findings = translator.page_boundary_audit.analyze_document(
            document,
            format_name="latex",
            locations=locations,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "same-sequence-nonterminal")
        self.assertEqual(findings[0]["page"], 19)
        self.assertEqual(findings[0]["next_page"], 20)
        self.assertEqual(findings[0]["location"], locations[0])

        formula_document = {
            "blocks": [
                {
                    "t": "Para",
                    "c": [
                        {"t": "Str", "c": "如下："},
                        marker,
                        {"t": "Math", "c": ["DisplayMath", "x"]},
                    ],
                }
            ]
        }
        self.assertEqual(
            translator.page_boundary_audit.analyze_document(
                formula_document,
                format_name="latex",
                locations=locations,
            ),
            [],
        )

    def test_html_source_pages_are_parsed_as_attributes(self) -> None:
        text = (
            '<html><body><span data-page="4" class="note source-page"></span>'
            "</body></html>"
        )
        self.assertEqual(translator.source_pages_for("html", text), [4])
        with self.assertRaisesRegex(translator.CliError, "data-page"):
            translator.source_pages_for(
                "html",
                '<html><span class="source-page" data-page="x"></span></html>',
            )

    def test_source_unit_markers_are_parsed_and_checked(self) -> None:
        text = '[]{.source-unit data-unit="1"}\n[]{.source-unit data-unit="2"}\n'
        self.assertEqual(translator.source_units_for("markdown", text), [1, 2])
        translator.validate_source_unit_sequence([1, 2], "zh-CN", [1, 2])
        with self.assertRaisesRegex(translator.CliError, "source-unit"):
            translator.validate_source_unit_sequence([2, 1], "zh-CN", [1, 2])

    def test_epub_source_units_match_spine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.epub"
            container = (
                '<?xml version="1.0"?>'
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>'
                "</container>"
            )
            opf = (
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf">'
                "<manifest>"
                '<item id="body" href="body.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="fn" href="fn.xhtml" media-type="application/xhtml+xml"/>'
                "</manifest>"
                '<spine><itemref idref="body"/><itemref idref="fn" linear="no"/></spine>'
                "</package>"
            )
            with zipfile.ZipFile(source, "w") as archive:
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, b"application/epub+zip")
                archive.writestr("META-INF/container.xml", container)
                archive.writestr("OEBPS/content.opf", opf)
                archive.writestr("OEBPS/body.xhtml", "<html/>")
                archive.writestr("OEBPS/fn.xhtml", "<html/>")
            rows = (
                {
                    "unit_id": "1",
                    "spine_order": "1",
                    "xhtml_path": "OEBPS/body.xhtml",
                    "linear": "yes",
                    "kind": "content",
                    "title": "body",
                    "note": "",
                },
                {
                    "unit_id": "2",
                    "spine_order": "2",
                    "xhtml_path": "OEBPS/fn.xhtml",
                    "linear": "no",
                    "kind": "footnotes",
                    "title": "fn",
                    "note": "",
                },
            )
            summary = translator.validate_epub_source_units(source, rows)
            self.assertIn("spine_items=2", summary)

    def test_source_page_sequence_requires_full_coverage(self) -> None:
        with self.assertRaisesRegex(translator.CliError, "覆盖 1--3"):
            translator.validate_source_page_sequence([1, 3], "zh-CN", 3)
        with self.assertRaisesRegex(translator.CliError, "不得重复"):
            translator.validate_source_page_sequence([1, 1, 2], "zh-CN", 2)

    def test_mutool_pages_falls_back_to_page_tree_when_info_fails(self) -> None:
        info_error = translator.CliError("mutool info failed")
        page_tree = subprocess.CompletedProcess(
            ["mutool", "show"],
            0,
            stdout="page 1 = 10 0 R\npage 2 = 20 0 R\n",
            stderr="",
        )
        with mock.patch.object(translator, "run", side_effect=[info_error, page_tree]):
            pages, output = translator.mutool_pages(Path("source.pdf"))
        self.assertEqual(pages, 2)
        self.assertIn("page 2", output)

    def test_epub_page_anchors_read_opf_and_reject_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.epub"
            container = (
                '<?xml version="1.0"?>'
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>'
                "</container>"
            )
            opf = (
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf">'
                '<manifest><item id="body" href="body.xhtml" '
                'media-type="application/xhtml+xml"/></manifest>'
                "</package>"
            )
            body = '<html><body><a id="Page_5"/><a id="Page_6"/></body></html>'
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("META-INF/container.xml", container)
                archive.writestr("OEBPS/content.opf", opf)
                archive.writestr("OEBPS/body.xhtml", body)
            pages, summary = translator.epub_page_anchors(source)
            self.assertEqual(pages, [5, 6])
            self.assertIn("content.opf", summary)

            duplicate = root / "duplicate.epub"
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("META-INF/container.xml", container)
                archive.writestr("OEBPS/content.opf", opf)
                archive.writestr(
                    "OEBPS/body.xhtml",
                    '<html><body><a id="Page_5"/><a id="Page_5"/></body></html>',
                )
            with self.assertRaisesRegex(translator.CliError, "页面锚点重复"):
                translator.epub_page_anchors(duplicate)

    def test_epub_page_anchors_reject_missing_opf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.epub"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("META-INF/container.xml", "<container/>")
            with self.assertRaisesRegex(translator.CliError, "OPF 路径"):
                translator.epub_page_anchors(source)

    def test_glossary_rejects_conflicting_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            glossary = Path(directory) / "glossary.tsv"
            header = "kind\tsource\ttarget\tnote\n"
            glossary.write_text(
                header
                + "term\ttime horizon\t时间范围\tfirst\n"
                + "term\ttime horizon\t时间范围\tduplicate\n",
                encoding="utf-8",
            )
            translator.validate_glossary(glossary)

            glossary.write_text(
                header
                + "term\ttime horizon\t时间范围\tfirst\n"
                + "\nterm\ttime horizon\t时间视界\tconflict\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                translator.CliError,
                '第 4 行 source "time horizon" 与第 2 行 target 冲突',
            ):
                translator.validate_glossary(glossary)

    def test_atomic_publish_preserves_old_output_when_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "new.pdf"
            target = root / "final.pdf"
            source.write_bytes(b"new")
            target.write_bytes(b"old")
            with mock.patch.object(
                translator.shutil,
                "copyfileobj",
                side_effect=OSError("copy failed"),
            ):
                with self.assertRaises(OSError):
                    translator.publish_atomic(source, target)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(root.glob(".final.pdf.*.tmp")), [])

    def test_atomic_publish_verifies_stage_before_replacing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "new.epub"
            target = root / "final.epub"
            source.write_bytes(b"new")
            target.write_bytes(b"old")
            with self.assertRaisesRegex(translator.CliError, "SHA-256"):
                translator.publish_atomic(
                    source,
                    target,
                    expected_bytes=source.stat().st_size,
                    expected_sha256="0" * 64,
                )
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(root.glob(".final.epub.*.tmp")), [])

    def test_html_adapter_preserves_native_structure_and_inlines_css(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "formats" / "html").mkdir(parents=True)
            (root / "formats" / "html" / "book.css").write_text(
                "body { color: black; }\n",
                encoding="utf-8",
            )
            work_path = root / "Works" / "sample"
            work_path.mkdir(parents=True)
            entry = work_path / "main.html"
            entry.write_text(
                "<!doctype html><html><head><!-- translator:book-css --></head>"
                '<body><main data-kind="book"><script>window.ok = true;</script></main>'
                "</body></html>",
                encoding="utf-8",
            )
            output = root / "output.html"
            language = translator.Language(
                "en", "source", entry, None, {"html": output}
            )
            work = translator.Work(
                root,
                work_path,
                {},
                "sample",
                "Sample",
                "html",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_html(work, language, output)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("<script>window.ok = true;</script>", rendered)
            self.assertIn('<main data-kind="book">', rendered)
            self.assertIn('<style id="translator-book-css">', rendered)
            self.assertNotIn("translator:book-css", rendered)

    @unittest.skipUnless(
        shutil.which("typst") and shutil.which("mutool"),
        "typst or mutool is not installed",
    )
    def test_init_creates_a_valid_manifest_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            books = root / "Books"
            (root / "Works").mkdir()
            books.mkdir()
            source_typ = books / "source.typ"
            source_pdf = books / "sample.pdf"
            source_typ.write_text("= Sample\n", encoding="utf-8")
            subprocess.run(
                [
                    "typst",
                    "compile",
                    "--root",
                    str(root),
                    str(source_typ),
                    str(source_pdf),
                ],
                check=True,
                capture_output=True,
            )
            source_typ.unlink()
            args = types.SimpleNamespace(
                source=str(source_pdf),
                id="sample",
                title="Sample",
                work=None,
                source_lang="en",
                target_lang=None,
                format="typst",
                target=None,
            )
            with mock.patch.object(translator, "REPO_ROOT", root):
                translator.command_init(args)
            work_path = root / "Works" / "sample"
            loaded = translator.load_work(work_path, root)
            self.assertEqual(loaded.source_pages, 1)
            self.assertEqual(
                [language.code for language in loaded.languages], ["en", "zh-CN"]
            )
            with mock.patch.object(translator, "REPO_ROOT", root):
                with self.assertRaisesRegex(translator.CliError, "不会覆盖"):
                    translator.command_init(args)

    @unittest.skipUnless(
        shutil.which("typst") and shutil.which("mutool"),
        "typst or mutool is not installed",
    )
    def test_init_latex_epub_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            books = root / "Books"
            (root / "Works").mkdir()
            books.mkdir()
            source_typ = books / "source.typ"
            source_pdf = books / "sample.pdf"
            source_typ.write_text("= Sample\n", encoding="utf-8")
            subprocess.run(
                [
                    "typst",
                    "compile",
                    "--root",
                    str(root),
                    str(source_typ),
                    str(source_pdf),
                ],
                check=True,
                capture_output=True,
            )
            source_typ.unlink()
            args = types.SimpleNamespace(
                source=str(source_pdf),
                id="latex-sample",
                title="LaTeX Sample",
                work=str(root / "Works" / "latex-sample"),
                source_lang="en",
                target_lang=None,
                format="latex",
                target=["epub"],
            )
            with mock.patch.object(translator, "REPO_ROOT", root):
                translator.command_init(args)
            loaded = translator.load_work(root / "Works" / "latex-sample", root)
            self.assertEqual(loaded.authoring_format, "latex")
            self.assertEqual(
                [set(language.outputs) for language in loaded.languages],
                [{"epub"}, {"epub"}],
            )
            self.assertEqual(loaded.manifest["epub"]["title"], "LaTeX Sample")

    @unittest.skipUnless(shutil.which("typst"), "typst is not installed")
    def test_common_typst_profile_builds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "formats", root / "formats")
            work_path = root / "Works" / "sample"
            entry = work_path / "en" / "main.typ"
            entry.parent.mkdir(parents=True)
            entry.write_text(
                translator.initial_entry("typst", "Sample [#]", "en")
                + "\n#source-page(1)\nText.\n",
                encoding="utf-8",
            )
            output = root / "sample.pdf"
            language = translator.Language("en", "source", entry, None, {"pdf": output})
            work = translator.Work(
                root,
                work_path,
                {},
                "sample",
                "Sample",
                "typst",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_typst(work, language, "pdf", output)
            self.assertGreater(output.stat().st_size, 0)

    def test_typst_html_footnotes_use_pandoc_semantics(self) -> None:
        source = (
            '<sup id="note" role="doc-noteref"><a href="#body">1</a></sup>'
            '<li id="body"><sup role="doc-backlink"><a href="#note">1</a></sup>Note</li>'
        )
        normalized = translator.normalize_typst_html(source)
        self.assertIn(
            '<a href="#body" class="footnote-ref" id="note" '
            'role="doc-noteref"><sup>1</sup></a>',
            normalized,
        )
        self.assertIn(
            '<li id="body"><a href="#note" class="footnote-back" '
            'role="doc-backlink">1</a>Note</li>',
            normalized,
        )

    def test_empty_navigation_spans_are_removed(self) -> None:
        navigation = ET.fromstring(
            '<nav xmlns="http://www.w3.org/1999/xhtml"><a>十<span/>九世纪</a></nav>'
        )
        self.assertEqual(translator.remove_empty_spans(navigation), 1)
        self.assertEqual("".join(navigation.itertext()), "十九世纪")
        body = ET.fromstring(
            '<p xmlns="http://www.w3.org/1999/xhtml">'
            '错<span class="source-page" data-page="1"/>误<span class="keep"/>'
            "</p>"
        )
        self.assertEqual(translator.remove_empty_spans(body, "source-page"), 1)
        self.assertEqual("".join(body.itertext()), "错误")
        self.assertEqual(len(list(body)), 1)
        cjk_body = ET.fromstring(
            '<p xmlns="http://www.w3.org/1999/xhtml">'
            '任何 <span class="source-page" data-page="1"/>设计。'
            ' Latin <span class="source-page" data-page="2"/>word'
            "</p>"
        )
        self.assertEqual(translator.remove_empty_spans(cjk_body, "source-page"), 2)
        self.assertEqual("".join(cjk_body.itertext()), "任何设计。 Latin word")

    def test_markdown_source_page_spacing_candidates(self) -> None:
        self.assertEqual(
            translator.markdown_source_page_spacing_candidates(
                '任何 []{.source-page data-page="1"}设计。\n'
                'Latin []{.source-page data-page="2"}word\n'
            ),
            [1],
        )

    def test_visible_markup_artifact_ignores_code(self) -> None:
        document = ET.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<code>&lt;br /&gt;</code><p>A&lt;br /&gt;B</p></body></html>'
        )
        self.assertEqual(translator.visible_markup_artifact(document), "<br />")
        subscript = ET.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<p>x&lt;sub&gt;1&lt;/sub&gt;</p></body></html>'
        )
        self.assertEqual(translator.visible_markup_artifact(subscript), "<sub>")
        code_only = ET.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<pre><code>&lt;sub&gt;x&lt;/sub&gt;</code></pre></body></html>'
        )
        self.assertIsNone(translator.visible_markup_artifact(code_only))
        compact_break = ET.fromstring(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<p>A&lt;br&gt;B</p></body></html>'
        )
        self.assertEqual(translator.visible_markup_artifact(compact_break), "<br>")

    def test_nonformula_image_alt_rejects_unusable_text(self) -> None:
        for attributes, problem in (
            ({}, "缺少替代文本"),
            ({"alt": ""}, "替代文本为空但未声明为装饰图"),
            ({"alt": "image"}, "替代文本过于笼统"),
            ({"alt": r"曲线 \\alpha"}, "替代文本含 LaTeX 控制序列"),
        ):
            with self.subTest(attributes=attributes):
                self.assertEqual(
                    translator.nonformula_image_alt_problem(
                        ET.Element("img", attributes)
                    ),
                    problem,
                )
        self.assertIsNone(
            translator.nonformula_image_alt_problem(
                ET.Element("img", {"alt": "", "role": "presentation"})
            )
        )
        self.assertIsNone(
            translator.nonformula_image_alt_problem(
                ET.Element("img", {"alt": "第三章的因果模型"})
            )
        )

    @unittest.skipUnless(
        all(
            shutil.which(tool) for tool in ("typst", "pandoc", "uv", "latex", "dvisvgm")
        ),
        "Typst or SVG math tools are not installed",
    )
    def test_typst_adapter_builds_and_checks_epub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "formats", root / "formats")
            (root / "tools").mkdir()
            shutil.copyfile(
                ROOT / "tools" / "gladtex_filter.py",
                root / "tools" / "gladtex_filter.py",
            )
            entry = root / "main.typ"
            entry.write_text(
                "#set page(width: 148mm, height: 216mm, margin: 0mm)\n"
                '#context if target() == "paged" {\n'
                '  rect(width: 100%, height: 100%, fill: rgb("#175596"))\n'
                "  pagebreak()\n"
                "  set page(margin: 20mm)\n"
                "}\n"
                '#context if target() == "html" {\n'
                '  html.elem("span", attrs: (class: "source-page", "data-page": "1"))\n'
                "}\n"
                "= 示例\n\n正文 #emph[概念] 与脚注。#footnote[脚注正文。]\n\n"
                "#quote(block: true)[引文。]\n\n公式 $x + y = z$。\n",
                encoding="utf-8",
            )
            output = root / "sample.epub"
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": output}
            )
            work = translator.Work(
                root,
                root,
                {"epub": {"title": "示例 EPUB", "author": "作者"}},
                "sample",
                "Sample",
                "typst",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_typst(work, language, "epub", output)
            evidence = root / "evidence"
            evidence.mkdir()
            translator.qa_epub(work, language, output, evidence)
            self.assertGreater(output.stat().st_size, 0)
            summary = (evidence / "summary.txt").read_text(encoding="utf-8")
            self.assertIn("title=示例 EPUB", summary)
            self.assertRegex(summary, r"formula_images=[1-9]\d*")
            epub_type = f"{{{translator.EPUB_NAMESPACE}}}type"
            with zipfile.ZipFile(output) as archive:
                documents = [
                    ET.fromstring(archive.read(name))
                    for name in archive.namelist()
                    if name.endswith(".xhtml")
                ]
            self.assertEqual(
                sum(
                    element.tag.rsplit("}", 1)[-1] == "a"
                    and "noteref" in element.attrib.get(epub_type, "").split()
                    for document in documents
                    for element in document.iter()
                ),
                1,
            )
            self.assertEqual(
                sum(
                    element.tag.rsplit("}", 1)[-1] == "aside"
                    and "footnote" in element.attrib.get(epub_type, "").split()
                    for document in documents
                    for element in document.iter()
                ),
                1,
            )
            self.assertEqual(
                sum(
                    element.tag.rsplit("}", 1)[-1] == "a"
                    and "backlink" in element.attrib.get(epub_type, "").split()
                    for document in documents
                    for element in document.iter()
                ),
                1,
            )

    @unittest.skipUnless(
        shutil.which("typst") and shutil.which("mutool") and translator.image_tool(),
        "typst, mutool, or ImageMagick is not installed",
    )
    def test_pdf_qa_creates_page_and_contact_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.typ"
            output = root / "sample.pdf"
            evidence = root / "qa"
            source.write_text("= Sample\n", encoding="utf-8")
            evidence.mkdir()
            subprocess.run(
                ["typst", "compile", str(source), str(output)],
                check=True,
                capture_output=True,
            )
            work = translator.Work(
                root,
                root,
                {},
                "sample",
                "Sample",
                "typst",
                None,
                output,
                1,
                (),
            )
            translator.qa_pdf(work, output, evidence, 72)
            self.assertTrue((evidence / "info.txt").is_file())
            self.assertTrue((evidence / "outline.txt").is_file())
            self.assertTrue(any(evidence.glob("page-*.png")))
            self.assertTrue(any(evidence.glob("contact-*.png")))

    @unittest.skipUnless(
        shutil.which("pandoc") and shutil.which("typst"),
        "pandoc or typst is not installed",
    )
    def test_markdown_adapter_builds_single_html_and_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            entry.write_text(
                "# Sample\n\n"
                'A[]{.source-page data-page="1"}B\n\n'
                '[]{.source-page data-page="2"}\n\n'
                "C\n\n"
                '[概念]{.cn-concept data-original="original"}\n',
                encoding="utf-8",
            )
            html_output = root / "sample.html"
            pdf_output = root / "sample.pdf"
            language = translator.Language(
                "en",
                "source",
                entry,
                None,
                {"html": html_output, "pdf": pdf_output},
            )
            work = translator.Work(
                ROOT,
                root,
                {},
                "sample",
                "Sample",
                "markdown",
                "typst",
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_markdown(work, language, "html", html_output)
            translator.build_markdown(work, language, "pdf", pdf_output)
            rendered = html_output.read_text(encoding="utf-8")
            self.assertIn('data-page="1"', rendered)
            self.assertIn("<style>", rendered)
            self.assertGreater(pdf_output.stat().st_size, 0)

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in ("pandoc", "uv", "latex", "dvisvgm")),
        "Pandoc or SVG math tools are not installed",
    )
    def test_markdown_adapter_builds_and_checks_epub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "zh-CN" / "main.md"
            entry.parent.mkdir()
            entry.write_text(
                "---\n"
                "title: Sample\n"
                "lang: zh-CN\n"
                "---\n\n"
                "# Sample $x$\n\n"
                'A[]{.source-page data-page="1"}B\n\n'
                '任何 []{.source-page data-page="2"}设计。\n\n'
                "![Demand curve diagram](assets/diagram.png)[]{.image-tail}\n\n"
                "Raster [![source formula](assets/diagram.png){.math-inline}]{.inline-formula}.\n\n"
                "~~~haskell\nid x = x\n~~~\n\n"
                "::: {.learning-points}\n\n重点。\n\n:::\n\n"
                "## Detail\n\nC\n",
                encoding="utf-8",
            )
            write_test_png(root / "assets" / "cover.png")
            write_test_png(root / "assets" / "diagram.png")
            output = root / "sample.epub"
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": output}
            )
            work = translator.Work(
                ROOT,
                root,
                {"epub": {"cover": "assets/cover.png"}},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_markdown(work, language, "epub", output)
            evidence = root / "evidence"
            evidence.mkdir()
            translator.qa_epub(work, language, output, evidence)
            self.assertGreater(output.stat().st_size, 0)
            self.assertIn(
                "zip_ok=true", (evidence / "summary.txt").read_text(encoding="utf-8")
            )
            with zipfile.ZipFile(output) as archive:
                content_xhtml = [
                    name
                    for name in archive.namelist()
                    if name.startswith("EPUB/text/ch") and name.endswith(".xhtml")
                ]
                self.assertGreaterEqual(len(content_xhtml), 2)
                self.assertGreater(
                    sum(name.startswith("EPUB/media/") for name in archive.namelist()),
                    1,
                )
                opf = ET.fromstring(archive.read("EPUB/content.opf"))
                nav = next(
                    item
                    for item in opf.iter()
                    if item.tag.rsplit("}", 1)[-1] == "item"
                    and "nav" in item.attrib.get("properties", "").split()
                )
                self.assertNotIn("mathml", nav.attrib["properties"].split())
                svg_items = [
                    item
                    for item in opf.iter()
                    if item.tag.rsplit("}", 1)[-1] == "item"
                    and item.attrib.get("media-type") == "image/svg+xml"
                ]
                self.assertGreaterEqual(len(svg_items), 1)
                xhtml = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                    if name.endswith(".xhtml")
                )
                self.assertNotIn("http://www.w3.org/1998/Math/MathML", xhtml)
                self.assertNotIn("application/x-tex", xhtml)
                self.assertNotIn("<figcaption", xhtml)
                self.assertIn("任何设计。", xhtml)
                self.assertNotIn("任何 设计", xhtml)
                self.assertRegex(
                    xhtml,
                    r'<img[^>]+class="math-inline"[^>]+alt="x"',
                )
                self.assertRegex(
                    xhtml,
                    r'<img(?=[^>]+class="math-inline")(?=[^>]+alt="source formula")'
                    r'(?=[^>]+\.png")[^>]*>',
                )
                self.assertNotRegex(xhtml, r"<p(?: [^>]*)?\s*/>")
                self.assertIn('class="sourceCode haskell"', xhtml)
                self.assertIn("ibooks-dark-theme-use-custom-text-color", xhtml)
                svg = archive.read(
                    next(name for name in archive.namelist() if name.endswith(".svg"))
                ).decode("utf-8")
                self.assertIn("prefers-color-scheme:dark", svg)
                self.assertIn("math-background", svg)
            summary = (evidence / "summary.txt").read_text(encoding="utf-8")
            self.assertIn("image_references=3", summary)
            self.assertIn("nonformula_images=1", summary)
            self.assertIn("formula_images=2", summary)
            self.assertIn("formula_svg_resources=1", summary)

            def write_tampered(name: str, entries: dict[str, bytes]) -> Path:
                target = root / name
                with zipfile.ZipFile(target, "w") as archive:
                    info = zipfile.ZipInfo("mimetype")
                    info.compress_type = zipfile.ZIP_STORED
                    archive.writestr(info, entries.pop("mimetype"))
                    for member, data in entries.items():
                        archive.writestr(
                            member, data, compress_type=zipfile.ZIP_DEFLATED
                        )
                return target

            with zipfile.ZipFile(output) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            body_name = next(
                name for name, data in entries.items() if b"Demand curve diagram" in data
            )
            document = ET.fromstring(entries[body_name])
            diagram = next(
                element
                for element in document.iter()
                if element.attrib.get("alt") == "Demand curve diagram"
            )
            duplicate_document = ET.fromstring(entries[body_name])
            duplicate_diagram = next(
                element
                for element in duplicate_document.iter()
                if element.attrib.get("alt") == "Demand curve diagram"
            )
            body = next(
                element
                for element in duplicate_document.iter()
                if element.tag.rsplit("}", 1)[-1] == "body"
            )
            figure = ET.SubElement(
                body, "{http://www.w3.org/1999/xhtml}figure"
            )
            ET.SubElement(
                figure,
                "{http://www.w3.org/1999/xhtml}img",
                {
                    "src": duplicate_diagram.attrib["src"],
                    "alt": duplicate_diagram.attrib["alt"],
                },
            )
            ET.SubElement(
                figure,
                "{http://www.w3.org/1999/xhtml}figcaption",
            ).text = duplicate_diagram.attrib["alt"]
            duplicate_entries = dict(entries)
            duplicate_entries[body_name] = ET.tostring(
                duplicate_document, encoding="utf-8", xml_declaration=True
            )
            with self.assertRaisesRegex(translator.CliError, "自动图注未移除"):
                translator.qa_epub(
                    work,
                    language,
                    write_tampered("duplicate-caption.epub", duplicate_entries),
                    root / "duplicate-caption-evidence",
                )
            manual_duplicate_document = ET.fromstring(entries[body_name])
            manual_duplicate_diagram = next(
                element
                for element in manual_duplicate_document.iter()
                if element.attrib.get("alt") == "Demand curve diagram"
            )
            manual_body = next(
                element
                for element in manual_duplicate_document.iter()
                if element.tag.rsplit("}", 1)[-1] == "body"
            )
            manual_figure = ET.SubElement(
                manual_body, "{http://www.w3.org/1999/xhtml}figure"
            )
            ET.SubElement(
                manual_figure,
                "{http://www.w3.org/1999/xhtml}img",
                {
                    "src": manual_duplicate_diagram.attrib["src"],
                    "alt": manual_duplicate_diagram.attrib["alt"],
                },
            )
            ET.SubElement(
                manual_figure,
                "{http://www.w3.org/1999/xhtml}figcaption",
            ).text = "图 1.1 示例"
            ET.SubElement(manual_body, "{http://www.w3.org/1999/xhtml}p").text = (
                "图 1.1 示例"
            )
            manual_duplicate_entries = dict(entries)
            manual_duplicate_entries[body_name] = ET.tostring(
                manual_duplicate_document, encoding="utf-8", xml_declaration=True
            )
            with self.assertRaisesRegex(translator.CliError, "图注重复"):
                translator.qa_epub(
                    work,
                    language,
                    write_tampered(
                        "manual-duplicate-caption.epub", manual_duplicate_entries
                    ),
                    root / "manual-duplicate-caption-evidence",
                )
            diagram.set("alt", "image")
            entries[body_name] = ET.tostring(
                document, encoding="utf-8", xml_declaration=True
            )
            with self.assertRaisesRegex(translator.CliError, "替代文本过于笼统"):
                translator.qa_epub(
                    work,
                    language,
                    write_tampered("generic-alt.epub", entries),
                    root / "generic-alt-evidence",
                )

            with zipfile.ZipFile(output) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            document = ET.fromstring(entries[body_name])
            diagram = next(
                element
                for element in document.iter()
                if element.attrib.get("alt") == "Demand curve diagram"
            )
            image_name = PurePosixPath(diagram.attrib["src"]).name
            opf_name = next(name for name in entries if name.endswith(".opf"))
            package = ET.fromstring(entries[opf_name])
            image_item = next(
                element
                for element in package.iter()
                if PurePosixPath(element.attrib.get("href", "")).name == image_name
            )
            image_item.set("media-type", "application/octet-stream")
            entries[opf_name] = ET.tostring(
                package, encoding="utf-8", xml_declaration=True
            )
            with self.assertRaisesRegex(translator.CliError, "图像资源不是图像"):
                translator.qa_epub(
                    work,
                    language,
                    write_tampered("bad-image-media.epub", entries),
                    root / "bad-image-media-evidence",
                )

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in ("pandoc", "uv", "latex", "dvisvgm")),
        "Pandoc or SVG math tools are not installed",
    )
    def test_epub_qa_rejects_mathml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            entry.write_text(
                "---\ntitle: Sample\nlang: zh-CN\n---\n\n# Sample\n\n$x$.\n",
                encoding="utf-8",
            )
            output = root / "sample.epub"
            language = translator.Language(
                "zh-CN",
                "source",
                entry,
                None,
                {"epub": output},
            )
            work = translator.Work(
                ROOT,
                root,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_markdown(work, language, "epub", output)
            with zipfile.ZipFile(output) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            body_name = next(
                name
                for name, data in entries.items()
                if name.endswith(".xhtml") and b"math-inline" in data
            )
            document = ET.fromstring(entries[body_name])
            formula = next(
                element
                for element in document.iter()
                if "math-inline" in element.attrib.get("class", "").split()
            )
            formula.clear()
            formula.tag = "{http://www.w3.org/1998/Math/MathML}math"
            ET.SubElement(
                formula,
                "{http://www.w3.org/1998/Math/MathML}mi",
            ).text = "x"
            entries[body_name] = ET.tostring(
                document,
                encoding="utf-8",
                xml_declaration=True,
            )
            tampered = root / "tampered.epub"
            with zipfile.ZipFile(tampered, "w") as archive:
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, entries.pop("mimetype"))
                for name, data in entries.items():
                    archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            evidence = root / "evidence"
            evidence.mkdir()
            with self.assertRaisesRegex(translator.CliError, "不得包含 MathML"):
                translator.qa_epub(work, language, tampered, evidence)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_epub_qa_rejects_invalid_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            entry.write_text(
                "---\ntitle: Sample\nlang: zh-CN\n---\n\n# Sample\n", encoding="utf-8"
            )
            output = root / "sample.epub"
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": output}
            )
            work = translator.Work(
                ROOT,
                root,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_markdown(work, language, "epub", output)
            with zipfile.ZipFile(output) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            nav_name = next(name for name in entries if name.endswith("nav.xhtml"))
            entries[nav_name] = entries[nav_name].replace(
                b'epub:type="toc"', b'epub:type="landmarks"'
            )
            tampered = root / "tampered.epub"
            with zipfile.ZipFile(tampered, "w") as archive:
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, entries.pop("mimetype"))
                for name, data in entries.items():
                    archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            with self.assertRaisesRegex(translator.CliError, "toc nav"):
                translator.qa_epub(work, language, tampered, root / "evidence")

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_epub_qa_rejects_invalid_content_xhtml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            entry.write_text(
                "---\ntitle: Sample\nlang: zh-CN\n---\n\n# Sample\n", encoding="utf-8"
            )
            output = root / "sample.epub"
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": output}
            )
            work = translator.Work(
                ROOT,
                root,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_markdown(work, language, "epub", output)
            with zipfile.ZipFile(output) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            body_name = next(
                name
                for name in entries
                if name.endswith(".xhtml") and not name.endswith("nav.xhtml")
            )
            entries[body_name] = entries[body_name].replace(b"</body>", b"<br></body>")
            tampered = root / "tampered.epub"
            with zipfile.ZipFile(tampered, "w") as archive:
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, entries.pop("mimetype"))
                for name, data in entries.items():
                    archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            (root / "evidence").mkdir()
            with self.assertRaisesRegex(translator.CliError, "正文文档无效"):
                translator.qa_epub(work, language, tampered, root / "evidence")

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_epub_qa_rejects_missing_link_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.md"
            entry.write_text(
                "---\ntitle: Sample\nlang: zh-CN\n---\n\n# Sample\n", encoding="utf-8"
            )
            output = root / "sample.epub"
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": output}
            )
            work = translator.Work(
                ROOT,
                root,
                {},
                "sample",
                "Sample",
                "markdown",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_markdown(work, language, "epub", output)
            with zipfile.ZipFile(output) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            nav_name = next(name for name in entries if name.endswith("nav.xhtml"))
            navigation = ET.fromstring(entries[nav_name])
            anchor = next(
                element
                for element in navigation.iter()
                if element.tag.rsplit("}", 1)[-1] == "a" and element.attrib.get("href")
            )
            anchor.set("href", anchor.attrib["href"].split("#", 1)[0] + "#missing")
            entries[nav_name] = ET.tostring(
                navigation,
                encoding="utf-8",
                xml_declaration=True,
            )
            tampered = root / "tampered.epub"
            with zipfile.ZipFile(tampered, "w") as archive:
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, entries.pop("mimetype"))
                for name, data in entries.items():
                    archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            (root / "evidence").mkdir()
            with self.assertRaisesRegex(translator.CliError, "链接片段不存在"):
                translator.qa_epub(work, language, tampered, root / "evidence")

    @unittest.skipUnless(
        shutil.which("latexmk") and shutil.which("xelatex") and shutil.which("mutool"),
        "latexmk, xelatex, or mutool is not installed",
    )
    def test_latex_adapter_finds_common_style_and_keeps_aux_out_of_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "main.tex"
            entry.write_text(
                "\\documentclass{book}\n"
                "\\usepackage{translator}\n"
                "\\begin{document}\n"
                "Sample\\sourcepage{1} text.\n"
                "\\sourcepage{2}\n\\sourcepage{3}\n"
                "Inline $x\\sourcepage{4}y$.\n"
                "\\epubfallback{missing.svg}{ignored}{PRINTBRANCHTOKEN}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            output = root / "sample.pdf"
            language = translator.Language("en", "source", entry, None, {"pdf": output})
            work = translator.Work(
                ROOT,
                root,
                {},
                "sample",
                "Sample",
                "latex",
                None,
                root / "source.pdf",
                1,
                (language,),
            )
            translator.build_latex(work, language, "pdf", output)
            translator.build_latex(work, language, "pdf", root / "sample-zh.pdf")
            self.assertGreater(output.stat().st_size, 0)
            self.assertGreater((root / "sample-zh.pdf").stat().st_size, 0)
            extracted = subprocess.run(
                ["mutool", "draw", "-F", "txt", str(output)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout
            self.assertIn("PRINTBRANCHTOKEN", extracted)
            self.assertIn("Sample text.", extracted)
            self.assertNotIn("missing.svg", extracted)

            zh_entry = root / "zh.tex"
            zh_entry.write_text(
                "\\documentclass{ctexbook}\n"
                "\\usepackage{translator}\n"
                "\\begin{document}\n"
                "\\cnconcept{理性}{rationality}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            zh_language = translator.Language(
                "zh-CN",
                "translation",
                zh_entry,
                None,
                {"pdf": root / "sample-concept-zh.pdf"},
            )
            translator.build_latex(work, zh_language, "pdf", zh_language.outputs["pdf"])
            self.assertGreater(zh_language.outputs["pdf"].stat().st_size, 0)
            self.assertEqual(list(root.glob("*.aux")), [])
            self.assertEqual(list(root.glob("*.log")), [])

    def test_latex_include_expands_graphicspath_and_ignores_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "chapters").mkdir()
            (work / "assets").mkdir()
            (work / "fig-a").mkdir()
            (work / "fig-b").mkdir()
            for path in (
                work / "assets" / "bare.png",
                work / "fig-a" / "same.png",
                work / "fig-b" / "same.png",
            ):
                path.write_bytes(b"png")
            (work / "chapters" / "chapter.tex").write_text(
                "\\includegraphics{bare.png}\n"
                "\\includegraphics{../fig-a/same.png}\n"
                "\\includegraphics{../fig-b/same.png}\n",
                encoding="utf-8",
            )
            (work / "chapters" / "a.tex").write_text(
                "\\includegraphics{same.png}\n", encoding="utf-8"
            )
            (work / "chapters" / "b.tex").write_text(
                "\\includegraphics{same.png}\n", encoding="utf-8"
            )
            (work / "chapters" / "after.tex").write_text(
                "\\includegraphics{same.png}\n", encoding="utf-8"
            )
            (work / "set-path.tex").write_text(
                "\\graphicspath{{fig-a/}}\n", encoding="utf-8"
            )
            entry = work / "main.tex"
            entry.write_text(
                "\\graphicspath{{assets/}}\n"
                "% \\input{missing.tex}\n"
                "\\input{chapters/chapter}\n"
                "\\graphicspath{{fig-a/}}\n"
                "\\input{chapters/a}\n"
                "\\graphicspath{{fig-b/}}\n"
                "\\input{chapters/b}\n"
                "\\input{set-path}\n"
                "\\input{chapters/after}\n"
                "\\includegraphics{same.png}\n",
                encoding="utf-8",
            )
            expanded = translator.expand_latex(entry, work, normalize_graphics=True)
            self.assertIn("\\includegraphics{assets/bare.png}", expanded)
            self.assertEqual(expanded.count("\\includegraphics{fig-a/same.png}"), 4)
            self.assertEqual(expanded.count("\\includegraphics{fig-b/same.png}"), 2)
            self.assertIn("% \\input{missing.tex}", expanded)

            (work / "loop.tex").write_text("\\input{loop.tex}\n", encoding="utf-8")
            with self.assertRaises(translator.CliError):
                translator.expand_latex(work / "loop.tex", work)
            (work / "bad.tex").write_text(
                "\\input{assets/bare.png}\n", encoding="utf-8"
            )
            with self.assertRaises(translator.CliError):
                translator.expand_latex(work / "bad.tex", work)

    def test_latex_include_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            outside = root / "outside.tex"
            outside.write_text("outside\n", encoding="utf-8")
            try:
                (work / "outside.tex").symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            entry = work / "main.tex"
            entry.write_text("\\input{outside}\n", encoding="utf-8")
            with self.assertRaisesRegex(translator.CliError, "越出工作目录"):
                translator.expand_latex(entry, work)

    @unittest.skipUnless(
        shutil.which("pandoc")
        and shutil.which("uv")
        and shutil.which("latex")
        and shutil.which("dvisvgm")
        and shutil.which("java")
        and translator.EPUBCHECK_JAR.is_file(),
        "SVG math, Java, or EPUBCheck tools are not installed",
    )
    def test_latex_adapter_builds_svg_math_epub_with_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work_path = Path(directory)
            (work_path / "zh-CN").mkdir()
            (work_path / "assets").mkdir()
            write_test_png(work_path / "assets" / "cover.png")
            write_test_png(work_path / "assets" / "diagram.png")
            (work_path / "zh-CN" / "chapter.tex").write_text(
                "\\chapter{示例章}\\label{ch:sample}\n"
                "A\\sourcepage{1}B，\\cnconcept{需求}{demand}。\n"
                "跨页前文。\n\\sourcepage{2}\n跨页后文。\n"
                "行内公式 $x^2$。\n"
                "\\begin{equation}\n\\begin{aligned}\n"
                "\\frac{x_1^2}{y}&=1\\\\\n\nx_2&=2\n\\end{aligned}\n"
                "\\tag{1.1}\n\\label{eq:x}\\end{equation}\n"
                "参见式~\\eqref{eq:x}。\n"
                "\\begin{longtable}{ll}$a$ & 甲 \\\\ $b$ & 乙 \\\\"
                "\\end{longtable}\n"
                "正文\\footnotemark[1]。\\footnotetext[1]{常规分离脚注。}\n"
                "\\begin{figure}\\includegraphics[alt={因果示意图}]{diagram.png}"
                "\\caption{示意图}\\label{fig:sample}\\end{figure}\n"
                "参见图~\\ref{fig:sample}。\n"
                "\\stepcounter{footnote}\\begin{figure}"
                "\\includegraphics[alt={动态脚注示意图}]{diagram.png}"
                "\\caption{动态脚注\\textsuperscript{\\thefootnote}}"
                "\\end{figure}"
                "\\footnotetext[\\value{footnote}]{动态分离脚注。}\n"
                "\\chapter*{附录}\\renewcommand{\\thesection}"
                "{A.\\arabic{section}}\\setcounter{section}{0}"
                "\\section{第一节}\\section{第二节}\n"
                "\\setcounter{figure}{0}\\begin{figure}"
                "\\renewcommand{\\thefigure}"
                "{\\arabic{chapter}.\\arabic{figure}a}"
                "\\includegraphics[alt={子图甲}]{diagram.png}\\caption{子图甲}"
                "\\label{fig:a}\\end{figure}\n"
                "\\setcounter{figure}{0}\\begin{figure}"
                "\\renewcommand{\\thefigure}"
                "{\\arabic{chapter}.\\arabic{figure}b}"
                "\\includegraphics[alt={子图乙}]{diagram.png}\\caption{子图乙}"
                "\\label{fig:b}\\end{figure}\n"
                "参见~\\ref{fig:a}、\\autoref{fig:b}、\\pageref{fig:a}。\n"
                "\\begin{landscape}\\refstepcounter{table}"
                "\\label{tab:image}\\includegraphics[alt={数据表截图}]{diagram.png}"
                "\\end{landscape}\n"
                "参见表~\\ref{tab:image}。\n",
                encoding="utf-8",
            )
            entry = work_path / "zh-CN" / "main.tex"
            entry.write_text(
                "\\documentclass{ctexbook}\n"
                "\\usepackage{longtable,graphicx}\n"
                "\\graphicspath{{../assets/}}\n"
                "\\begin{document}\n"
                "\\input{chapter.tex}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            output = work_path / "sample.epub"
            language = translator.Language(
                "zh-CN",
                "translation",
                entry,
                None,
                {"epub": output},
                "中文示例",
                "示例作者",
                "urn:test:latex-epub:zh-CN",
            )
            work = translator.Work(
                ROOT,
                work_path,
                {
                    "epub": {
                        "title": "Fallback",
                        "cover": "assets/cover.png",
                    }
                },
                "sample",
                "Sample",
                "latex",
                None,
                work_path / "source.pdf",
                1,
                (language,),
            )
            translator.build_latex(work, language, "epub", output)
            evidence = work_path / "evidence"
            evidence.mkdir()
            translator.qa_epub(work, language, output, evidence)
            with zipfile.ZipFile(output) as archive:
                xhtml = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                    if name.endswith(".xhtml")
                )
                svg_count = sum(name.endswith(".svg") for name in archive.namelist())
            self.assertNotIn("http://www.w3.org/1998/Math/MathML", xhtml)
            self.assertNotIn("application/x-tex", xhtml)
            self.assertIn("math-inline", xhtml)
            self.assertIn("math-display", xhtml)
            self.assertGreaterEqual(svg_count, 4)
            self.assertIn("需求", xhtml)
            self.assertIn("demand", xhtml)
            self.assertEqual(xhtml.count("常规分离脚注"), 1)
            self.assertEqual(xhtml.count("动态分离脚注"), 1)
            self.assertIn('epub:type="noteref"', xhtml)
            self.assertIn('epub:type="footnote"', xhtml)
            self.assertIn('epub:type="backlink"', xhtml)
            self.assertIn('aria-label="返回正文"', xhtml)
            self.assertIn(
                'class="header-section-number">A.1</span> 第一节',
                xhtml,
            )
            self.assertIn(
                'class="header-section-number">A.2</span> 第二节',
                xhtml,
            )
            self.assertIn("<table", xhtml)
            self.assertIn("<img", xhtml)
            self.assertNotIn("source-page", xhtml)
            self.assertRegex(
                xhtml,
                r"<p>[\s\S]*?跨页前文。[\s\S]*?跨页后文。[\s\S]*?</p>",
            )
            self.assertIn("跨页前文。跨页后文。", xhtml)
            self.assertNotIn("\\eqref", xhtml)
            self.assertEqual(xhtml.count('href="#fig:a">1.1a</a>'), 2)
            self.assertIn('href="#fig:b">1.1b</a>', xhtml)
            self.assertIn('href="ch002.xhtml#tab:image">1.2</a>', xhtml)
            self.assertIn("epubcheck=passed", (evidence / "summary.txt").read_text())

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in ("pandoc", "uv", "latex", "dvisvgm")),
        "Pandoc or SVG math tools are not installed",
    )
    def test_latex_epub_m27_compatibility_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "zh-CN").mkdir()
            (work / "assets").mkdir()
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
                '<path d="M1 19 19 1" stroke="black"/></svg>'
            )
            (work / "assets" / "fallback.svg").write_text(svg, encoding="utf-8")
            (work / "assets" / "diagram.svg").write_text(svg, encoding="utf-8")
            cover = write_test_png(work / "assets" / "cover.png")
            entry = work / "zh-CN" / "main.tex"
            entry.write_text(
                r"""\documentclass{book}
\usepackage{graphicx}
\graphicspath{{../assets/}}
\begin{document}
\chapter{第一章}
\section{留待\ensuremath{\blacktriangleright} 第 9 章讨论}
\subsection{\ensuremath{\blacksquare}建议阅读}
\begin{flushright}右对齐正文保留。\end{flushright}
目录条目\dotfill 833

正文页 833。

\noindent 1.\quad 第一题。

\noindent 9.\quad 第九题。

\tikz[baseline=-0.31em]{%
    \fill[black,rounded corners=0.11em] (0,0) rectangle (0.55em,0.55em);
    \fill[white] (0.275em,0.275em) circle (0.10em);
  }

\epubfallback{fallback.svg}{工资—利润曲线静态图}{%
  \begin{tikzpicture}\draw (0,0) -- (1,1);\end{tikzpicture}%
}

\[
\resizebox{0.90\textwidth}{!}{$
\begin{bmatrix}1&2\\3&4\end{bmatrix}
$}
\]

\[
\begin{aligned}
a&=b\\
\sourcepage{921}c&=d
\end{aligned}
\]

\[
\text{\scriptsize\cnconcept{规模报酬不变}{constant returns to scale (CRS)}}\quad a=b
\]

\begin{figure}\includegraphics[alt={测试图说明}]{diagram.svg}
\caption{图号 \thefigure}\label{fig:one}\end{figure}
\begin{figure}\includegraphics{diagram.svg}\caption{占位图二}\end{figure}
\begin{figure}\includegraphics{diagram.svg}\caption{占位图三}\end{figure}
\refstepcounter{figure}\label{fig:manual}
手工图号 \thefigure。

参见 \ref{fig:one} 和 \ref{fig:manual}。

\chapter{第二章}
\begin{figure}\includegraphics{diagram.svg}
\caption{第二章图号 \thefigure}\label{fig:next}\end{figure}
参见 \ref{fig:next}。
\end{document}
""",
                encoding="utf-8",
            )
            output = work / "sample.epub"
            translator.build_latex_epub_source(
                root=ROOT,
                work_path=work,
                entry=entry,
                output=output,
                title="M27 compatibility",
                author=None,
                language="zh-CN",
                identifier="urn:test:m27",
                cover=cover,
            )
            with zipfile.ZipFile(output) as archive:
                xhtml_entries = [
                    (name, ET.fromstring(archive.read(name)))
                    for name in archive.namelist()
                    if name.endswith(".xhtml")
                ]
                names = archive.namelist()
            xhtml_documents = [document for _, document in xhtml_entries]
            navigation = next(
                document for name, document in xhtml_entries if name.endswith("nav.xhtml")
            )
            navigation_text = "".join(navigation.itertext())
            xhtml = "\n".join(
                ET.tostring(document, encoding="unicode")
                for document in xhtml_documents
            )
            plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", xhtml)))
            images = [
                (name, element)
                for name, document in xhtml_entries
                for element in document.iter()
                if element.tag.rsplit("}", 1)[-1] == "img"
            ]
            markers = [
                element
                for document in xhtml_documents
                for element in document.iter()
                if "figure-marker" in element.attrib.get("class", "").split()
            ]
            fallback_name, fallback_image = next(
                (name, image)
                for name, image in images
                if image.attrib.get("alt") == "工资—利润曲线静态图"
            )
            fallback_resource = posixpath.normpath(
                (PurePosixPath(fallback_name).parent / fallback_image.attrib["src"]).as_posix()
            )
            self.assertTrue(fallback_resource.endswith(".svg"))
            self.assertIn(fallback_resource, names)
            self.assertTrue(
                any(
                    "\\begin{bmatrix}" in image.attrib.get("alt", "")
                    and "resizebox" not in image.attrib.get("alt", "")
                    and "textwidth" not in image.attrib.get("alt", "")
                    for _, image in images
                )
            )
            self.assertTrue(
                any("c&=d" in image.attrib.get("alt", "") for _, image in images)
            )
            self.assertTrue(
                any(image.attrib.get("alt") == "测试图说明" for _, image in images)
            )
            self.assertTrue(
                any(image.attrib.get("alt") == "图像：diagram" for _, image in images)
            )
            self.assertFalse(
                any("sourcepage" in image.attrib.get("alt", "") for _, image in images)
            )
            concept_alt = next(
                image.attrib.get("alt", "")
                for _, image in images
                if "规模报酬不变" in image.attrib.get("alt", "")
            )
            self.assertIn("constant returns to scale (CRS)", concept_alt)
            self.assertNotIn("cnconcept", concept_alt)
            self.assertNotIn("scriptsize", concept_alt)
            self.assertNotIn("tikzpicture", xhtml)
            self.assertNotIn("\\blacktriangleright", navigation_text)
            self.assertNotIn("\\blacksquare", navigation_text)
            self.assertIn("▶", navigation_text)
            self.assertIn("■", navigation_text)
            self.assertEqual(len(markers), 1)
            self.assertEqual(markers[0].attrib.get("aria-hidden"), "true")
            self.assertTrue("".join(markers[0].itertext()).strip())
            self.assertRegex(xhtml, r'href="[^"]*#fig:one">1\.1</')
            self.assertRegex(xhtml, r'href="[^"]*#fig:manual">1\.4</')
            self.assertRegex(xhtml, r'href="[^"]*#fig:next">2\.1</')
            self.assertIn("图号 1.1", plain)
            self.assertIn("手工图号 1.4", plain)
            self.assertIn("第二章图号 2.1", plain)
            self.assertIn("右对齐正文保留", plain)
            self.assertIn("目录条目", plain)
            self.assertNotRegex(plain, r"目录条目\s*833")
            self.assertRegex(plain, r"正文页\s*833")
            self.assertRegex(plain, r"1\.\s*第一题")
            self.assertRegex(plain, r"9\.\s*第九题")

    def test_latex_epub_rejects_unsafe_epubfallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            work = base / "work"
            (work / "assets").mkdir(parents=True)
            fallback = work / "assets" / "fallback.svg"
            fallback.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
                encoding="utf-8",
            )
            (work / "assets" / "plain.png").write_bytes(b"png")
            outside = base / "outside.svg"
            outside.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
                encoding="utf-8",
            )
            cover = write_test_png(work / "assets" / "cover.png")
            cases = {
                "absolute": rf"\epubfallback{{{outside.as_posix()}}}{{说明}}{{print}}",
                "parent_escape": r"\epubfallback{../outside.svg}{说明}{print}",
                "dynamic": r"\epubfallback{\asset}{说明}{print}",
                "missing": r"\epubfallback{assets/missing.svg}{说明}{print}",
                "non_svg": r"\epubfallback{assets/plain.png}{说明}{print}",
                "empty_alt": r"\epubfallback{assets/fallback.svg}{}{print}",
                "tex_alt": r"\epubfallback{assets/fallback.svg}{\emph{说明}}{print}",
                "braced_alt": r"\epubfallback{assets/fallback.svg}{{说明}}{print}",
            }

            def assert_rejected(name: str, command: str) -> None:
                entry = work / "main.tex"
                entry.write_text(
                    "\\documentclass{book}\\begin{document}"
                    + command
                    + "\\end{document}",
                    encoding="utf-8",
                )
                with self.subTest(name=name), self.assertRaisesRegex(
                    translator.CliError, "LaTeX EPUB 回退"
                ):
                    translator.build_latex_epub_source(
                        root=ROOT,
                        work_path=work,
                        entry=entry,
                        output=work / "sample.epub",
                        title="Sample",
                        author=None,
                        language="zh-CN",
                        identifier="urn:test:unsafe-fallback",
                        cover=cover,
                    )

            for name, command in cases.items():
                assert_rejected(name, command)

            escape = work / "assets" / "escape.svg"
            try:
                escape.symlink_to(outside)
            except OSError:
                pass
            else:
                assert_rejected(
                    "symlink_escape",
                    r"\epubfallback{assets/escape.svg}{说明}{print}",
                )

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in ("pandoc", "uv", "latex", "dvisvgm")),
        "Pandoc or SVG math tools are not installed",
    )
    def test_latex_epub_compatibility_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "sample"
            entry = work / "zh-CN" / "main.tex"
            cover = work / "assets" / "cover.png"
            output = work / "output" / "sample.epub"
            entry.parent.mkdir(parents=True)
            cover.parent.mkdir()
            entry.write_text(
                "\\documentclass{book}\\begin{document}"
                "\\chapter{示例}正文 $x^2$。\\end{document}",
                encoding="utf-8",
            )
            write_test_png(cover)
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "latex_epub.py"),
                    str(entry),
                    str(output),
                    "--title",
                    "示例",
                    "--cover",
                    str(cover),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            with zipfile.ZipFile(output) as archive:
                self.assertTrue(
                    any(name.endswith(".xhtml") for name in archive.namelist())
                )
                self.assertTrue(
                    any(name.endswith(".svg") for name in archive.namelist())
                )

    def test_check_preflights_declared_latex_epub_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work_path = root / "Works" / "sample"
            entry = work_path / "zh-CN" / "main.tex"
            source = root / "Books" / "sample.pdf"
            entry.parent.mkdir(parents=True)
            source.parent.mkdir()
            entry.write_text(
                "\\documentclass{book}\\begin{document}"
                "\\sourcepage{1}正文。\\end{document}",
                encoding="utf-8",
            )
            source.write_bytes(b"pdf")
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": work_path / "sample.epub"}
            )
            work = translator.Work(
                root,
                work_path,
                {"source": {"sha256": translator.sha256(source)}},
                "sample",
                "Sample",
                "latex",
                None,
                source,
                1,
                (language,),
            )
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                mock.patch.object(translator, "mutool_pages", return_value=(1, "")),
                mock.patch.object(translator, "validate_page_map"),
                mock.patch.object(translator, "preflight_latex_epub") as preflight,
            ):
                translator.command_check(types.SimpleNamespace(work=work_path))
            preflight.assert_called_once_with(work, language)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_latex_epub_preflight_rejects_unknown_raw_tex_without_math_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work_path = Path(directory)
            entry = work_path / "main.tex"
            language = translator.Language(
                "zh-CN", "source", entry, None, {"epub": work_path / "sample.epub"}
            )
            work = translator.Work(
                ROOT,
                work_path,
                {},
                "sample",
                "Sample",
                "latex",
                None,
                work_path / "source.pdf",
                1,
                (language,),
            )
            entry.write_text(
                "\\documentclass{book}\\begin{document}正文。\\end{document}",
                encoding="utf-8",
            )
            with mock.patch.object(translator, "render_svg_math_ast") as render_math:
                translator.preflight_latex_epub(work, language)
            render_math.assert_not_called()

            entry.write_text(
                "\\documentclass{book}\\begin{document}"
                "\\mystery{正文}\\end{document}",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(translator.CliError, "未映射 raw TeX"):
                translator.preflight_latex_epub(work, language)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_latex_epub_rejects_unknown_raw_tex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            entry = work / "main.tex"
            cover = write_test_png(work / "cover.png")
            cases = {
                "unknown": r"\mystery{正文}",
                "marker_variant": (
                    r"\tikz[baseline=-0.30em]{"
                    r"\fill[black,rounded corners=0.11em] (0,0) rectangle "
                    r"(0.55em,0.55em);"
                    r"\fill[white] (0.275em,0.275em) circle (0.10em);"
                    r"}"
                ),
            }
            for name, command in cases.items():
                with self.subTest(name=name):
                    entry.write_text(
                        "\\documentclass{book}\\begin{document}"
                        + command
                        + "\\end{document}",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        translator.CliError, "未映射 raw TeX"
                    ):
                        translator.build_latex_epub_source(
                            root=ROOT,
                            work_path=work,
                            entry=entry,
                            output=work / "sample.epub",
                            title="Sample",
                            author=None,
                            language="zh-CN",
                            identifier="urn:test:unknown",
                            cover=cover,
                        )

    def test_latex_epub_output_cannot_replace_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            entry = work / "main.tex"
            entry.write_text("\\documentclass{book}\n", encoding="utf-8")
            cover = work / "cover.png"
            cover.write_bytes(b"png")
            with self.assertRaisesRegex(translator.CliError, "输出"):
                translator.build_latex_epub_source(
                    root=ROOT,
                    work_path=work,
                    entry=entry,
                    output=entry,
                    title="Sample",
                    author=None,
                    language="zh-CN",
                    identifier="urn:test:collision",
                    cover=cover,
                )
            self.assertEqual(
                entry.read_text(encoding="utf-8"), "\\documentclass{book}\n"
            )

    def test_epubcheck_failure_is_saved_and_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            result = subprocess.CompletedProcess(
                ["java"],
                1,
                stdout="ERROR sample",
                stderr="",
            )
            with mock.patch.object(translator.subprocess, "run", return_value=result):
                with self.assertRaises(translator.CliError):
                    translator.run_epubcheck(evidence / "sample.epub", evidence)
            self.assertIn(
                "ERROR sample",
                (evidence / "epubcheck.txt").read_text(encoding="utf-8"),
            )

    def test_epubcheck_uses_ascii_copy_for_non_ascii_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            source = evidence / "数理逻辑.epub"
            source.write_bytes(b"epub")
            result = subprocess.CompletedProcess(["java"], 0, stdout="ok", stderr="")
            with mock.patch.object(
                translator.subprocess, "run", return_value=result
            ) as run:
                translator.run_epubcheck(source, evidence)
            checked_path = Path(run.call_args.args[0][-1])
            self.assertNotEqual(checked_path, source)
            self.assertTrue(checked_path.name.isascii())
            self.assertEqual(
                checked_path.parent.parent,
                (ROOT / ".tmp" / "translator").resolve(),
            )

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_pandoc_filter_preserves_source_page_concept_and_line_break(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.md"
            source.write_text(
                'A[]{.source-page data-page="12"}B\n\n'
                'C[]{.source-unit data-unit="3"}D\n\n'
                '[概念]{.cn-concept data-original="raison d\'être & <x>"}\n\n'
                '| left | first[]{.line-break}second |\n'
                '| --- | --- |\n',
                encoding="utf-8",
            )
            filter_path = ROOT / "formats" / "pandoc" / "semantics.lua"
            html_output = subprocess.run(
                [
                    "pandoc",
                    str(source),
                    "--from=markdown+bracketed_spans",
                    "--to=html5",
                    f"--lua-filter={filter_path}",
                ],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            typst_output = subprocess.run(
                [
                    "pandoc",
                    str(source),
                    "--from=markdown+bracketed_spans",
                    "--to=typst",
                    f"--lua-filter={filter_path}",
                ],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            json_output = subprocess.run(
                [
                    "pandoc",
                    str(source),
                    "--from=markdown+bracketed_spans",
                    "--to=json",
                    f"--lua-filter={filter_path}",
                ],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertIn('data-page="12"', html_output)
            decoded_html = " ".join(html.unescape(html_output).split())
            self.assertIn("概念", decoded_html)
            self.assertNotIn("source-unit", html_output)
            self.assertIn("raison d'être & <x>", decoded_html)
            self.assertIn("first<br", html_output)
            self.assertNotIn("line-break", html_output)
            self.assertIn('#metadata("source-page:12")', typst_output)
            self.assertIn("概念", typst_output)
            self.assertIn("raison", typst_output)
            self.assertIn("être", typst_output)
            self.assertIn("[first \\ second]", typst_output)
            self.assertEqual(json_output.count('"t":"LineBreak"'), 1)
            self.assertNotIn("line-break", json_output)

            ast = root / "source.json"
            ast.write_text(json_output, encoding="utf-8")
            two_stage_html = subprocess.run(
                ["pandoc", str(ast), "--from=json", "--to=html5"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            decoded_two_stage = " ".join(html.unescape(two_stage_html).split())
            self.assertIn("cn-concept-rendered", two_stage_html)
            self.assertIn("概念", decoded_two_stage)
            self.assertIn("raison d'être & <x>", decoded_two_stage)

            invalid = root / "invalid.md"
            invalid.write_text("[not empty]{.line-break}\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "pandoc",
                    str(invalid),
                    "--from=markdown+bracketed_spans",
                    "--to=json",
                    f"--lua-filter={filter_path}",
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("line-break must be empty", result.stderr)


if __name__ == "__main__":
    unittest.main()
