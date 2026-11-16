from __future__ import annotations

import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import chapter_handoff as handoff

TEMP = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
TEMP.mkdir(parents=True, exist_ok=True)


def marker(identifier: int, kind: str = "page") -> dict:
    return {"t": "Span", "c": [["", ["source-" + kind], [["data-" + kind, str(identifier)]]], []]}


def source_document(language: str) -> dict:
    title, end = ("Chapter", "Last sentence.") if language == "en" else ("章节", "最后一句。")
    return {"blocks": [
        {"t": "Header", "c": [1, ["chapter", [], []], [{"t": "Str", "c": title}]]},
        {"t": "Para", "c": [marker(10), {"t": "Str", "c": "Start"}]},
        {"t": "Para", "c": [marker(11), {"t": "Str", "c": end}]},
    ]}


class HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for language in ("en", "zh-CN"):
            (self.root / language).mkdir()
            (self.root / language / "chapter.md").write_text("chapter", encoding="utf-8")
        self.documents = {language: source_document(language) for language in ("en", "zh-CN")}
        self.source = {"kind": "page", "ids": [10, 11], "start": {"id": 10, "anchor": "Original chapter start"}, "end": {"id": 11, "anchor": "Original chapter end"}}
        self.scope = handoff.ChapterScope.from_dict({"chapter_id": "chapter", "source": self.source, "languages": ["en", "zh-CN"]})
        self.package = {
            "schema_version": 1, "chapter_id": "chapter", "source": copy.deepcopy(self.source),
            "entries": [
                {"language": "en", "path": "en/chapter.md", "start_anchor": "Chapter", "end_anchor": "Last sentence."},
                {"language": "zh-CN", "path": "zh-CN/chapter.md", "start_anchor": "章节", "end_anchor": "最后一句。"},
            ],
            "assets": [], "terms": [], "issues": [],
            "review": {"visual": {"reviewer": "chapter-owner", "source_ids": [10, 11]}, "validations": [{"name": "chapter-code-and-links", "result": "pass"}]},
        }

    def read_ast(self, path: Path) -> dict:
        self.assertTrue(path.is_file())
        return self.documents[path.parent.name]

    def validate(self, **kwargs) -> handoff.HandoffReport:
        return handoff.validate_handoff(self.package, root=self.root, scope=self.scope, read_ast=self.read_ast, **kwargs)

    def use_automatic_review(self) -> None:
        self.package["schema_version"] = 2
        self.package["review"]["automatic"] = {"reviewer": "chapter-owner", "source_ids": [10, 11]}
        self.package["review"]["visual"] = []

    def test_complete_bilingual_package_passes_and_keeps_visual_limit_explicit(self) -> None:
        result = self.validate()
        self.assertEqual(result.source_ids, (10, 11))
        self.assertEqual(result.languages, ("en", "zh-CN"))
        self.assertTrue(result.positions_require_visual_review)

    def test_missing_required_lists_and_review_fail(self) -> None:
        for field in ("entries", "assets", "terms", "issues", "review", "source"):
            with self.subTest(field=field):
                package = copy.deepcopy(self.package)
                package.pop(field)
                with self.assertRaises(handoff.HandoffError):
                    handoff.validate_handoff(package, root=self.root, scope=self.scope, read_ast=self.read_ast)

    def test_source_ids_not_just_count_must_match_controlled_scope(self) -> None:
        for ids in ([12, 13], [11, 10], [10], [10, 10], [True, 11]):
            with self.subTest(ids=ids), self.assertRaises(handoff.HandoffError):
                self.package["source"]["ids"] = ids
                self.validate()

    def test_source_boundary_anchor_mismatch_fails(self) -> None:
        self.package["source"]["end"]["anchor"] = "Other ending"
        with self.assertRaisesRegex(handoff.HandoffError, "首尾锚点"):
            self.validate()

    def test_actual_source_set_order_and_duplicates_fail(self) -> None:
        for values in ((10, 12), (11, 10), (10, 10)):
            with self.subTest(values=values):
                self.documents["en"]["blocks"][1]["c"][0] = marker(values[0])
                self.documents["en"]["blocks"][2]["c"][0] = marker(values[1])
                with self.assertRaisesRegex(handoff.HandoffError, "实际来源集合"):
                    self.validate()

    def test_marker_example_in_code_does_not_count_as_provenance(self) -> None:
        self.documents["en"]["blocks"][1] = {"t": "CodeBlock", "c": [["", [], []], '[]{.source-page data-page="10"}']}
        with self.assertRaisesRegex(handoff.HandoffError, "实际来源集合"):
            self.validate()

    def test_lead_source_pages_auxiliary_metadata_does_not_replace_markers(self) -> None:
        self.documents["en"]["blocks"][1]["c"][0] = {"t": "Span", "c": [["", [], [["data-source-pages", "10,11"]]], []]}
        with self.assertRaisesRegex(handoff.HandoffError, "实际来源集合"):
            self.validate()

    def test_auxiliary_source_sets_cannot_escape_or_misstate_scope(self) -> None:
        attributes = self.documents["en"]["blocks"][1]["c"][0]["c"][0][2]
        attributes.append(["data-source-pages", "10,11"])
        self.validate()
        for value in ("10,99", "11,10", "10,10", "10, 11", "10-11", "", "0,10", "010,11"):
            with self.subTest(value=value), self.assertRaisesRegex(handoff.HandoffError, "data-source-pages"):
                attributes[-1][1] = value
                self.validate()

    def test_complete_chapter_heading_and_no_adjacent_chapter(self) -> None:
        self.documents["en"]["blocks"].append({"t": "Header", "c": [1, ["next", [], []], [{"t": "Str", "c": "Next chapter"}]]})
        with self.assertRaisesRegex(handoff.HandoffError, "相邻章节"):
            self.validate()

    def test_entry_first_last_anchor_must_be_actual_edges(self) -> None:
        for edge in ("start_anchor", "end_anchor"):
            with self.subTest(edge=edge):
                package = copy.deepcopy(self.package)
                package["entries"][0][edge] = "Start"
                with self.assertRaisesRegex(handoff.HandoffError, "实际章节边界"):
                    handoff.validate_handoff(package, root=self.root, scope=self.scope, read_ast=self.read_ast)

    def test_bilingual_entry_missing_or_duplicate_language_fails(self) -> None:
        self.package["entries"].pop()
        with self.assertRaisesRegex(handoff.HandoffError, "语言条目不完整"):
            self.validate()
        self.package["entries"].append(copy.deepcopy(self.package["entries"][0]))
        with self.assertRaisesRegex(handoff.HandoffError, "语言条目不完整"):
            self.validate()

    def test_explicit_target_only_route_is_not_forced_to_invent_source_entry(self) -> None:
        self.scope = handoff.ChapterScope.from_dict({"chapter_id": "chapter", "source": self.source, "languages": ["zh-CN"]})
        self.package["entries"] = [self.package["entries"][1]]
        self.assertEqual(self.validate().languages, ("zh-CN",))

    def test_entry_and_asset_paths_cannot_escape_or_be_absolute(self) -> None:
        for path in ("../outside.md", str(self.root / "en/chapter.md"), "C:/outside.md", "en\\chapter.md"):
            with self.subTest(path=path), self.assertRaises(handoff.HandoffError):
                self.package["entries"][0]["path"] = path
                self.validate()

    def add_image(self, path: str = "../assets/image.png") -> None:
        directory = self.root / "assets"
        directory.mkdir(exist_ok=True)
        (directory / "image.png").write_bytes(b"image")
        self.documents["en"]["blocks"][1]["c"].append({"t": "Image", "c": [["", [], []], [{"t": "Str", "c": "Image"}], [path, ""]]})

    def test_assets_match_real_refs_and_all_exist(self) -> None:
        self.add_image()
        with self.assertRaisesRegex(handoff.HandoffError, "资产清单"):
            self.validate()
        self.package["assets"] = ["assets/image.png"]
        self.assertEqual(len(self.validate().assets), 1)
        (self.root / "assets" / "image.png").unlink()
        with self.assertRaisesRegex(handoff.HandoffError, "文件不存在"):
            self.validate()

    def test_unreferenced_assets_and_remote_or_traversing_refs_fail(self) -> None:
        self.add_image()
        self.package["assets"] = ["assets/image.png"]
        (self.root / "assets/unused.png").write_bytes(b"unused")
        self.package["assets"].append("assets/unused.png")
        with self.assertRaisesRegex(handoff.HandoffError, "资产清单"):
            self.validate()
        self.package["assets"].pop()
        for target in ("https://example.invalid/a.png", "../../outside.png", "../assets/image.png?query=1"):
            with self.subTest(target=target), self.assertRaises(handoff.HandoffError):
                self.documents["en"]["blocks"][1]["c"][-1]["c"][2][0] = target
                self.validate()

    def test_asset_symlink_escaping_root_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP) as outside:
            target = Path(outside) / "a.png"
            target.write_bytes(b"outside")
            link = self.root / "link.png"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink privilege not available")
            self.package["assets"] = ["link.png"]
            with self.assertRaisesRegex(handoff.HandoffError, "路径越过"):
                self.validate()

    def test_existing_and_incremental_glossary_conflicts_fail(self) -> None:
        self.package["terms"] = [{"kind": "term", "source": "binding", "target": "绑定", "note": "p10"}]
        self.validate(glossary={"binding": "绑定"})
        with self.assertRaisesRegex(handoff.HandoffError, "术语冲突"):
            self.validate(glossary={"binding": "约束"})
        self.package["terms"].append({"kind": "term", "source": "binding", "target": "约束", "note": ""})
        with self.assertRaisesRegex(handoff.HandoffError, "术语冲突"):
            self.validate()

    def test_glossary_kind_and_exact_four_columns(self) -> None:
        for term in (
            {"kind": "made-up", "source": "x", "target": "y", "note": ""},
            {"kind": "term", "source": "x", "target": "y"},
            {"kind": "term", "source": "x", "target": "y", "note": "", "extra": "value"},
        ):
            with self.subTest(term=term), self.assertRaises(handoff.HandoffError):
                self.package["terms"] = [term]
                self.validate()

    def test_malformed_scope_and_package_top_levels_report_handoff_error(self) -> None:
        for value in (None, "scope", [], {"source": None}, {"source": self.source, "chapter_id": "chapter", "languages": "en"}):
            with self.subTest(value=value), self.assertRaises(handoff.HandoffError):
                handoff.ChapterScope.from_dict(value)
        for value in (None, "package", []):
            with self.subTest(value=value), self.assertRaises(handoff.HandoffError):
                handoff.validate_handoff(value, root=self.root, scope=self.scope, read_ast=self.read_ast)
        self.package["entries"][0]["language"] = []
        with self.assertRaises(handoff.HandoffError):
            self.validate()

    def test_footnote_does_not_invent_an_entry_ending(self) -> None:
        self.documents["en"]["blocks"][-1]["c"].append({"t": "Note", "c": [{"t": "Para", "c": [{"t": "Str", "c": "Footnote body"}]}]})
        self.validate()

    def test_unresolved_or_outside_scope_issue_fails_but_resolved_erratum_passes(self) -> None:
        issue = {"kind": "erratum", "source_id": 10, "anchor": "original code", "detail": "Source typo retained", "status": "resolved"}
        self.package["issues"] = [issue]
        self.validate()
        issue["status"] = "open"
        with self.assertRaisesRegex(handoff.HandoffError, "未裁决"):
            self.validate()
        issue["status"] = "resolved"
        issue["source_id"] = 12
        with self.assertRaisesRegex(handoff.HandoffError, "本章来源"):
            self.validate()

    def test_visual_range_and_failed_validation_prevent_merge(self) -> None:
        self.package["review"]["visual"]["source_ids"] = [10]
        with self.assertRaisesRegex(handoff.HandoffError, "完整章节来源"):
            self.validate()
        self.package["review"]["visual"]["source_ids"] = [10, 11]
        self.package["review"]["validations"][0]["result"] = "fail"
        with self.assertRaisesRegex(handoff.HandoffError, "验证未通过"):
            self.validate()

    def test_schema_version_requires_exact_supported_integer(self) -> None:
        self.validate()
        self.use_automatic_review()
        self.validate()
        for version in (None, True, False, 0, 3, 1.0, 2.0, "1", "2"):
            with self.subTest(version=version), self.assertRaisesRegex(handoff.HandoffError, "schema_version"):
                self.package["schema_version"] = version
                self.validate()

    def test_report_construction_keeps_visual_default(self) -> None:
        report = handoff.HandoffReport("chapter", (), (), (), (), 0, 0)
        self.assertTrue(report.positions_require_visual_review)

    def test_v2_complete_automatic_review_allows_no_visual_exceptions(self) -> None:
        self.use_automatic_review()
        result = self.validate()
        self.assertEqual(result.source_ids, (10, 11))
        self.assertFalse(result.positions_require_visual_review)

    def test_v2_does_not_prove_original_source_marker_positions(self) -> None:
        self.use_automatic_review()
        # The same ordered marker IDs remain after moving a marker within the
        # paragraph. A passing report therefore does not verify its source position.
        self.documents["en"]["blocks"][1]["c"].reverse()
        self.assertFalse(self.validate().positions_require_visual_review)

    def test_v2_visual_exception_may_cover_one_ordered_source_subset(self) -> None:
        self.use_automatic_review()
        self.package["review"]["visual"] = [
            {"reviewer": "visual-owner", "source_ids": [11], "reason": "ambiguous table"},
        ]
        self.assertFalse(self.validate().positions_require_visual_review)

    def test_v2_automatic_review_requires_complete_ordered_source_identity(self) -> None:
        self.use_automatic_review()
        for ids in (None, "10,11", [], [10], [12, 13], [11, 10], [10, 10], [True, 11]):
            with self.subTest(ids=ids), self.assertRaises(handoff.HandoffError):
                self.package["review"]["automatic"]["source_ids"] = ids
                self.validate()

    def test_v2_automatic_review_requires_named_reviewer_and_object(self) -> None:
        self.use_automatic_review()
        for automatic in (None, [], "review", {}, {"source_ids": [10, 11]}, {"reviewer": "chapter-owner"},
                          {"reviewer": " ", "source_ids": [10, 11]},
                          {"reviewer": 1, "source_ids": [10, 11]}):
            with self.subTest(automatic=automatic), self.assertRaises(handoff.HandoffError):
                self.package["review"]["automatic"] = automatic
                self.validate()

    def test_v2_visual_must_be_explicit_list_of_exceptions(self) -> None:
        self.use_automatic_review()
        for visual in (None, {}, "review", [None], ["review"]):
            with self.subTest(visual=visual), self.assertRaises(handoff.HandoffError):
                self.package["review"]["visual"] = visual
                self.validate()
        self.package["review"].pop("visual")
        with self.assertRaises(handoff.HandoffError):
            self.validate()

    def test_v2_visual_exception_requires_valid_subset_reviewer_and_reason(self) -> None:
        self.use_automatic_review()
        valid = {"reviewer": "visual-owner", "source_ids": [10], "reason": "ambiguous table"}
        for field, values in (
            ("source_ids", (None, "10", [], [12], [10, 12], [11, 10], [10, 10], [True])),
            ("reviewer", (None, "", " ", 3)),
            ("reason", (None, "", " ", 3)),
        ):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(handoff.HandoffError):
                    exception = dict(valid)
                    exception[field] = value
                    self.package["review"]["visual"] = [exception]
                    self.validate()
        for field in valid:
            with self.subTest(missing=field), self.assertRaises(handoff.HandoffError):
                exception = dict(valid)
                exception.pop(field)
                self.package["review"]["visual"] = [exception]
                self.validate()

    def test_v2_requires_nonempty_unique_passing_validations(self) -> None:
        self.use_automatic_review()
        for validations in (
            None, [], {}, [None], [{"name": "", "result": "pass"}],
            [{"name": 1, "result": "pass"}], [{"name": "checks"}],
            [{"name": "checks", "result": "fail"}],
            [{"name": "checks", "result": "pass"}, {"name": "checks", "result": "pass"}],
        ):
            with self.subTest(validations=validations), self.assertRaises(handoff.HandoffError):
                self.package["review"]["validations"] = validations
                self.validate()

    def test_v2_keeps_source_ast_and_issue_gates(self) -> None:
        self.use_automatic_review()
        self.package["source"]["end"]["anchor"] = "Other ending"
        with self.assertRaisesRegex(handoff.HandoffError, "首尾锚点"):
            self.validate()
        self.package["source"]["end"]["anchor"] = self.scope.end.anchor
        self.documents["en"]["blocks"][1]["c"][0] = marker(12)
        with self.assertRaisesRegex(handoff.HandoffError, "实际来源集合"):
            self.validate()
        self.documents["en"]["blocks"][1]["c"][0] = marker(10)
        self.package["issues"] = [{"kind": "question", "source_id": 10, "anchor": "table",
                                   "detail": "ambiguous", "status": "open"}]
        with self.assertRaisesRegex(handoff.HandoffError, "未裁决"):
            self.validate()

    def test_v2_keeps_asset_and_glossary_gates(self) -> None:
        self.use_automatic_review()
        self.add_image()
        with self.assertRaisesRegex(handoff.HandoffError, "资产清单"):
            self.validate()
        self.package["assets"] = ["assets/image.png"]
        self.package["terms"] = [{"kind": "term", "source": "binding", "target": "绑定", "note": ""}]
        with self.assertRaisesRegex(handoff.HandoffError, "术语冲突"):
            self.validate(glossary={"binding": "约束"})
        self.validate(glossary={"binding": "绑定"})

    def test_handoff_check_cli_reports_v1_and_v2_visual_requirement(self) -> None:
        from tools import translator

        for language in self.documents:
            (self.root / language / "chapter.md").write_text(language, encoding="utf-8")
        (self.root / "scope.json").write_text(json.dumps({
            "chapter_id": "chapter", "source": self.source, "languages": ["en", "zh-CN"],
        }), encoding="utf-8")
        work = SimpleNamespace(authoring_format="markdown", path=self.root, root=self.root,
                               languages=[SimpleNamespace(code="en", glossary=None)])
        args = SimpleNamespace(lang="en", root=str(self.root), scope_file="scope.json", package="package.json")
        with patch.object(translator, "load_command_work", return_value=work), \
             patch.object(translator, "work_temp", return_value=self.root / "scratch"), \
             patch.object(translator, "_pandoc_boundary_ast", side_effect=lambda work, text, fmt: self.documents[text]):
            for version in (1, 2):
                with self.subTest(version=version):
                    if version == 2:
                        self.use_automatic_review()
                    (self.root / "package.json").write_text(json.dumps(self.package), encoding="utf-8")
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(translator.command_handoff_check(args), 0)
                    self.assertIs(json.loads(output.getvalue())["visual_review_owner_required"], version == 1)

    def test_no_declaration_preserves_old_books_and_overlapping_new_packages_fail(self) -> None:
        self.assertEqual(handoff.validate_declared_handoffs(None, read_ast=self.read_ast), ())
        declaration = (self.package, self.root, self.scope)
        self.assertEqual(len(handoff.validate_declared_handoffs([declaration], read_ast=self.read_ast)), 1)
        with self.assertRaisesRegex(handoff.HandoffError, "重复章节"):
            handoff.validate_declared_handoffs([declaration, declaration], read_ast=self.read_ast)

    def test_declared_handoffs_accepts_v2(self) -> None:
        self.use_automatic_review()
        reports = handoff.validate_declared_handoffs([(self.package, self.root, self.scope)], read_ast=self.read_ast)
        self.assertEqual(len(reports), 1)
        self.assertFalse(reports[0].positions_require_visual_review)

    def test_controlled_adjacent_chapters_may_share_a_source_page(self) -> None:
        second = copy.deepcopy(self.package)
        second["chapter_id"] = "second"
        for document in self.documents.values():
            document["blocks"][0]["c"][1][0] = "second"
        scope = handoff.ChapterScope.from_dict({"chapter_id": "second", "source": self.source, "languages": ["en", "zh-CN"]})
        # This explicit scope is accepted independently. No source count claims
        # that two chapters cannot begin/end on the same original page.
        handoff.validate_handoff(second, root=self.root, scope=scope, read_ast=self.read_ast)

    def test_direct_script_import_path_remains_supported(self) -> None:
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, "-c", "import chapter_handoff; assert chapter_handoff.validate_declared_handoffs(None, read_ast=lambda p: {}) == ()"], cwd=root / "tools", check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc required")
    def test_formal_reader_binds_actual_staged_files_and_source_units(self) -> None:
        for language, title, ending in (("en", "Chapter", "Last sentence."), ("zh-CN", "章节", "最后一句。")):
            (self.root / language / "chapter.md").write_text(f'# {title} {{#chapter}}\n\n[]{{.source-page data-page="10"}}Start\n\n[]{{.source-page data-page="11"}}{ending}\n', encoding="utf-8")

        def parse(path: Path) -> dict:
            result = subprocess.run(["pandoc", "-f", "markdown+fenced_divs+bracketed_spans+footnotes-raw_html", "-t", "json", str(path)], capture_output=True, text=True, encoding="utf-8", check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return json.loads(result.stdout)

        handoff.validate_handoff(self.package, root=self.root, scope=self.scope, read_ast=parse)
        for entry in self.package["entries"]:
            path = self.root / entry["path"]
            path.write_text(path.read_text(encoding="utf-8").replace("source-page", "source-unit").replace("data-page", "data-unit"), encoding="utf-8")
        self.package["source"]["kind"] = "unit"
        scope = handoff.ChapterScope.from_dict({"chapter_id": "chapter", "source": self.package["source"], "languages": ["en", "zh-CN"]})
        handoff.validate_handoff(self.package, root=self.root, scope=scope, read_ast=parse)
        self.use_automatic_review()
        self.assertFalse(handoff.validate_handoff(self.package, root=self.root, scope=scope, read_ast=parse).positions_require_visual_review)


if __name__ == "__main__":
    unittest.main()
