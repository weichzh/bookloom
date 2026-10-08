from __future__ import annotations

import argparse
import copy
import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".tmp" / "tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location(
    "workflow_translator", ROOT / "tools/translator.py"
)
assert SPEC and SPEC.loader
translator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = translator
SPEC.loader.exec_module(translator)


class WorkflowReliabilityTests(unittest.TestCase):
    def alias_directory(self, link: Path, target: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            link.symlink_to(target, target_is_directory=True)
            return
        script = link.parent / "junction-probe.ps1"
        script.write_text(
            "param([string]$LinkPath, [string]$TargetPath)\nNew-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script),
                str(link),
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_clean_rejects_work_key_alias_to_another_work(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            work, _, _ = self.make_work(Path(directory))
            alias_work = dataclasses.replace(work, path=work.root / "Works/alias")
            protected = translator.work_temp(work)
            marker = protected / "protected.txt"
            marker.write_text("preserve", encoding="utf-8")
            alias = translator.work_temp(alias_work)
            self.alias_directory(alias, protected)
            with (
                mock.patch.object(translator, "load_work", return_value=alias_work),
                self.assertRaises(translator.CliError),
            ):
                translator.command_clean(argparse.Namespace(work=alias_work.path))
            self.assertEqual(marker.read_text(), "preserve")

    def test_scratch_and_receipt_aliases_cannot_select_sibling(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            work, _, _ = self.make_work(Path(directory))
            protected = translator.scratch_dir(work, "second-agent")
            marker = protected / "protected.txt"
            marker.write_text("preserve", encoding="utf-8")
            alias = protected.parent / "first-agent"
            self.alias_directory(alias, protected)
            with self.assertRaises(translator.CliError):
                translator.scratch_dir(work, "first-agent")
            self.assertEqual(marker.read_text(), "preserve")
            protected_receipt = translator.evidence_dir(work, "completion")
            protected_receipt.mkdir(parents=True)
            marker = protected_receipt / "protected.txt"
            marker.write_text("preserve", encoding="utf-8")
            alias = protected_receipt.parent / "final"
            self.alias_directory(alias, protected_receipt)
            with self.assertRaises(translator.CliError):
                translator.reset_evidence_dir(work, "final")
            self.assertEqual(marker.read_text(), "preserve")

    def test_receipt_work_key_alias_does_not_clear_other_work(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            work, _, _ = self.make_work(Path(directory))
            protected = translator.evidence_dir(work, "final")
            protected.mkdir(parents=True)
            marker = protected / "protected.txt"
            marker.write_text("preserve", encoding="utf-8")
            alias_work = dataclasses.replace(work, path=work.root / "Works/alias")
            alias = translator.work_receipts(alias_work)
            self.alias_directory(alias, protected.parent)
            with self.assertRaises(translator.CliError):
                translator.clear_evidence_dir(alias_work, "final")
            self.assertEqual(marker.read_text(), "preserve")

    def test_delivery_receipt_alias_rejects_before_copy(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            root = Path(directory)
            work, language, output = self.make_work(root)
            translator.write_finalize_summary(
                work,
                [(language, "html", output)],
                translator.reset_evidence_dir(work, "final"),
            )
            with mock.patch.object(translator, "load_work", return_value=work):
                translator.command_complete(argparse.Namespace(work=work.path))
            work.manifest["work"]["status"] = "complete"
            base = translator.evidence_dir(work, "delivery")
            protected = base / "other-target"
            protected.mkdir(parents=True)
            marker = protected / "delivery.json"
            marker.write_text('{"other": true}', encoding="utf-8")
            self.alias_directory(base / "en-html", protected)
            destination = root / "delivered.html"
            args = argparse.Namespace(
                work=work.path, lang="en", target="html", to=destination
            )
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                self.assertRaises(translator.CliError),
            ):
                translator.command_deliver(args)
            self.assertFalse(destination.exists())
            self.assertEqual(marker.read_text(), '{"other": true}')

    def test_browser_receipt_requires_complete_matrix_and_excludes_diagnostics(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            work, _, output = self.make_work(Path(directory))
            evidence = translator.reset_evidence_child(work, "browser-qa", "en")
            digest = translator.sha256(output)
            modes = []
            for settings in translator.epub_browser_qa.default_modes():
                modes.append(
                    {
                        "mode": settings["name"],
                        "settings": settings,
                        "viewport": settings["viewport"],
                        "sha256": digest,
                        "xhtml_count": 1,
                        "formula_count": 0,
                        "xhtml_link_count": 0,
                        "fragment_link_count": 0,
                        "noteref_target_checks": 0,
                        "backlink_target_checks": 0,
                        "noteref_navigation_checks": 0,
                        "backlink_navigation_checks": 0,
                        "failures": [],
                        "results": [{"text": "private source text"}],
                    }
                )
            payload = {
                "sha256": digest,
                "failures": [],
                "mode_count": 3,
                "xhtml_count": 1,
                "modes": modes,
            }
            results = evidence / "results.json"
            results.write_text(json.dumps(payload), encoding="utf-8")
            receipt = translator.browser_acceptance_receipt(evidence, output)
            self.assertNotIn("private source text", json.dumps(receipt))
            self.assertEqual(len(receipt["modes"]), 3)
            for defect in (
                "missing-mode",
                "wrong-hash",
                "failed",
                "wrong-settings",
                "content-in-count",
            ):
                bad = copy.deepcopy(payload)
                if defect == "missing-mode":
                    bad["modes"].pop()
                elif defect == "wrong-hash":
                    bad["modes"][0]["sha256"] = "0" * 64
                elif defect == "failed":
                    bad["modes"][1]["failures"] = ["overflow"]
                elif defect == "wrong-settings":
                    bad["modes"][2]["settings"]["user_font_size_px"] = 16
                else:
                    bad["modes"][0]["formula_count"] = "private source text"
                results.write_text(json.dumps(bad), encoding="utf-8")
                with (
                    self.subTest(defect=defect),
                    self.assertRaises(translator.CliError),
                ):
                    translator.browser_acceptance_receipt(evidence, output)

    def test_epub_init_uses_spine_units_without_page_anchors(self) -> None:
        for version in ("2.0", "3.0"):
            with (
                self.subTest(version=version),
                tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory,
            ):
                root = Path(directory)
                source = root / "Books/sample.epub"
                source.parent.mkdir()
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("mimetype", b"application/epub+zip")
                    archive.writestr(
                        "META-INF/container.xml",
                        '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>',
                    )
                    archive.writestr(
                        "OEBPS/book.opf",
                        f'<package version="{version}"><metadata/><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b" linear="no"/></spine></package>',
                    )
                    for name in ("a", "b"):
                        archive.writestr(
                            f"OEBPS/{name}.xhtml",
                            "<html><body><p>Source paragraph.</p></body></html>",
                        )
                args = argparse.Namespace(
                    source=source,
                    id="sample",
                    title="Sample",
                    work=root / "Works/sample",
                    source_lang="en",
                    target_lang=["zh-CN"],
                    format="markdown",
                    target=["html"],
                )
                source_bytes = source.read_bytes()
                with mock.patch.object(translator, "REPO_ROOT", root):
                    translator.command_init(args)
                work = translator.load_work(args.work, root)
                self.assertEqual(work.source_kind, "epub-units")
                self.assertEqual(work.source_pages, 2)
                self.assertEqual(
                    [row["xhtml_path"] for row in work.source_unit_rows],
                    ["OEBPS/a.xhtml", "OEBPS/b.xhtml"],
                )
                self.assertEqual(work.source_unit_rows[1]["linear"], "no")
                self.assertFalse((work.path / "page-map.tsv").exists())
                self.assertNotIn("pages", work.manifest["source"])
                translator.validate_epub_source_units(source, work.source_unit_rows)
                self.assertEqual(
                    translator.status_document_state(work.path / "STATUS.md"), "planned"
                )
                self.assertEqual(source.read_bytes(), source_bytes)
                draft_args = argparse.Namespace(work=work.path, agent="intake-probe")
                with mock.patch.object(translator, "load_work", return_value=work):
                    translator.command_source_draft(draft_args)
                    translator.command_source_draft(draft_args)
                scratch = translator.scratch_dir(work, "intake-probe")
                self.assertEqual(len(list(scratch.glob("source-draft-*/draft.md"))), 2)
                with (
                    mock.patch.object(translator, "REPO_ROOT", root),
                    self.assertRaisesRegex(translator.CliError, "不会覆盖"),
                ):
                    translator.command_init(args)

    def test_scratch_and_receipts_cannot_escape_work_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            work, _, _ = self.make_work(Path(directory))
            first = translator.scratch_dir(work, "first-agent")
            second = translator.scratch_dir(work, "second-agent")
            self.assertNotEqual(first, second)
            for name in (
                "../escape",
                "..",
                "first/child",
                "nul",
                "con.txt",
                "trailing.",
            ):
                with self.subTest(name=name), self.assertRaises(translator.CliError):
                    translator.scratch_dir(work, name)
            first_marker = first / "owned.txt"
            first_marker.write_text("probe", encoding="utf-8")
            receipt = translator.evidence_dir(work, "final") / "final.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text("{}", encoding="utf-8")
            with mock.patch.object(translator, "load_work", return_value=work):
                translator.command_clean(argparse.Namespace(work=work.path))
            self.assertFalse(first.exists())
            self.assertTrue(receipt.is_file())
            for name in ("../outside", "final/../../outside"):
                with self.assertRaises(translator.CliError):
                    translator.evidence_dir(work, name)

    def test_status_uses_declared_value_before_explanation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            path = Path(directory) / "STATUS.md"
            cases = {
                "active；内容审校修订已完成，等待验收": "active",
                "planned；已完成工具探针": "planned",
                "`active`（验收尚未开始）": "active",
                "not complete; previous subset accepted": "active",
                "full-local-accepted；内容已验收": "complete",
                "未验收；构建完成": "active",
                "完成。": "complete",
                "scoped-repair-accepted；子任务完成": None,
                "active-other": None,
            }
            for value, expected in cases.items():
                with self.subTest(value=value):
                    path.write_text(f"状态：{value}\n", encoding="utf-8")
                    self.assertEqual(translator.status_document_state(path), expected)

    def make_work(self, root: Path):
        work_path = root / "Works" / "sample"
        output = work_path / "output/sample.html"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"<html>accepted</html>")
        source = root / "Books/sample.pdf"
        source.parent.mkdir()
        source.write_bytes(b"source")
        manifest = work_path / "manifest.toml"
        manifest.write_text(
            '[work]\nid = "sample"\nstatus = "active"\n', encoding="utf-8"
        )
        (work_path / "STATUS.md").write_text("状态：local-accepted\n", encoding="utf-8")
        language = translator.Language(
            "en", "source", work_path / "main.md", None, {"html": output}
        )
        work = translator.Work(
            root,
            work_path,
            {"work": {"status": "active"}},
            "sample",
            "Sample",
            "markdown",
            None,
            source,
            1,
            (language,),
        )
        qa = translator.reset_evidence_child(work, "qa", "en-html")
        (qa / "summary.txt").write_text("qa=passed\n", encoding="utf-8")
        return work, language, output

    def test_complete_clean_deliver_preserves_receipts_and_stale_hash_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            root = Path(directory)
            work, language, output = self.make_work(root)
            final = translator.write_finalize_summary(
                work,
                [(language, "html", output)],
                translator.reset_evidence_dir(work, "final"),
            )
            with mock.patch.object(translator, "load_work", return_value=work):
                translator.command_complete(argparse.Namespace(work=work.path))
                work.manifest["work"]["status"] = "complete"
                translator.command_clean(argparse.Namespace(work=work.path))
            self.assertTrue(final.is_file(), "clean must retain accepted final receipt")
            destination = root / "delivered.html"
            args = argparse.Namespace(
                work=work.path, lang="en", target="html", to=destination
            )
            with mock.patch.object(translator, "load_work", return_value=work):
                translator.command_deliver(args)
                translator.command_clean(argparse.Namespace(work=work.path))
            self.assertEqual(destination.read_bytes(), output.read_bytes())
            receipt = translator.work_receipts(work) / "delivery/en-html/delivery.json"
            self.assertEqual(json.loads(receipt.read_text())["status"], "complete")
            output.write_bytes(b"changed")
            with (
                mock.patch.object(translator, "load_work", return_value=work),
                self.assertRaisesRegex(translator.CliError, "正式输出已变化"),
            ):
                translator.command_deliver(args)
            self.assertEqual(destination.read_bytes(), b"<html>accepted</html>")


if __name__ == "__main__":
    unittest.main()
