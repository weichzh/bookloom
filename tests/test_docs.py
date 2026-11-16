from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = ROOT / ".tmp" / "tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location(
    "doc_length_check", ROOT / "scripts" / "doc_length_check.py"
)
assert SPEC and SPEC.loader
doc_length_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doc_length_check)


class DocumentCheckTests(unittest.TestCase):
    def make_current_docs(self, root: Path) -> None:
        documents = {
            "AGENTS.md": "# Rules\n",
            "CONTEXT.md": "# Context\n",
            "docs/ACTIVE.md": (
                "# Active\n\n- `shared-change`: example -> `docs/changes/example.md`\n"
            ),
            "docs/INDEX.md": "# Index\n",
            "docs/codebase/CONTRACTS.md": "# Contracts\n",
            "docs/workflow/PROTOCOL.md": "# Protocol\n",
            "docs/workflow/TRANSLATION.md": "# Translation\n",
            "docs/workflow/EPUB.md": "# EPUB\n",
            "docs/changes/example.md": (
                "# Example\n\n## Status\n\naccepted\n\n## Problem\n\nx\n\n"
                "## Scope\n\nx\n\n## Acceptance Criteria\n\nx\n\n"
                "## Result\n\nx\n\n## Review\n\nx\n"
            ),
        }
        for relative, content in documents.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        index = root / "docs" / "INDEX.md"
        index.write_text(
            """[AGENTS](../AGENTS.md)
[CONTEXT](../CONTEXT.md)
[ACTIVE](ACTIVE.md)
[CONTRACTS](codebase/CONTRACTS.md)
[PROTOCOL](workflow/PROTOCOL.md)
[TRANSLATION](workflow/TRANSLATION.md)
[EPUB](workflow/EPUB.md)
[CHANGE](changes/example.md)
""",
            encoding="utf-8",
        )

    def validated_root(self) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.make_current_docs(Path(temporary.name))
        return temporary

    def test_valid_current_structure_passes_with_injected_root(self) -> None:
        with self.validated_root() as directory:
            self.assertEqual(doc_length_check.validate(Path(directory)), [])

    def test_registered_source_figures_contract_is_allowed_and_bounded(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            doc = root / "docs/codebase/SOURCE_FIGURES.md"
            doc.write_text("# Source figure contract\n", encoding="utf-8")
            index = root / "docs/INDEX.md"
            index.write_text(index.read_text(encoding="utf-8") +
                             "[SOURCE FIGURES](codebase/SOURCE_FIGURES.md)\n", encoding="utf-8")
            self.assertEqual(doc_length_check.validate(root), [])
            doc.write_text("line\n" * 101, encoding="utf-8")
            self.assertTrue(any("TOO LONG: docs/codebase/SOURCE_FIGURES.md" in error
                                for error in doc_length_check.validate(root)))

    def test_rejects_markdown_outside_allowlist(self) -> None:
        with self.validated_root() as directory:
            path = Path(directory) / "docs" / "legacy.md"
            path.write_text("# Legacy\n", encoding="utf-8")
            self.assertIn(
                "DISALLOWED: docs/legacy.md", doc_length_check.validate(Path(directory))
            )

    def test_rejects_nested_change(self) -> None:
        with self.validated_root() as directory:
            path = Path(directory) / "docs" / "changes" / "archive" / "old.md"
            path.parent.mkdir()
            path.write_text("# Old\n", encoding="utf-8")
            self.assertIn(
                "DISALLOWED: docs/changes/archive/old.md",
                doc_length_check.validate(Path(directory)),
            )

    def test_enforces_length_limits(self) -> None:
        with self.validated_root() as directory:
            path = Path(directory) / "AGENTS.md"
            path.write_text("\n".join("line" for _ in range(101)), encoding="utf-8")
            errors = doc_length_check.validate(Path(directory))
            self.assertIn("TOO LONG: AGENTS.md has 101 lines; limit is 100.", errors)

    def test_requires_complete_unique_index_registration(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            index = root / "docs" / "INDEX.md"
            index.write_text("[AGENTS](../AGENTS.md)\n", encoding="utf-8")
            errors = doc_length_check.validate(root)
            self.assertIn(
                "UNREGISTERED: CONTEXT.md is missing from docs/INDEX.md.", errors
            )

            self.make_current_docs(root)
            with index.open("a", encoding="utf-8") as handle:
                handle.write("[again](workflow/EPUB.md)\n")
            self.assertIn(
                "DUPLICATE REGISTRATION: docs/workflow/EPUB.md appears in docs/INDEX.md more than once.",
                doc_length_check.validate(root),
            )

    def test_active_limits_and_targets(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            active = root / "docs" / "ACTIVE.md"
            active.write_text(
                "- `book`: one -> `Works/one/STATUS.md`\n"
                "- `book`: missing -> `Works/missing/STATUS.md`\n"
                "- `shared-change`: example -> `docs/changes/example.md`\n"
                "- `shared-change`: missing -> `docs/changes/missing.md`\n",
                encoding="utf-8",
            )
            status = root / "Works" / "one" / "STATUS.md"
            status.parent.mkdir(parents=True)
            status.write_text("# Status\n", encoding="utf-8")
            errors = doc_length_check.validate(root)
            self.assertIn("TOO MANY ACTIVE: book has 2 entries.", errors)
            self.assertIn("TOO MANY ACTIVE: shared-change has 2 entries.", errors)
            self.assertIn(
                "BROKEN ACTIVE TARGET: book references Works/missing/STATUS.md.", errors
            )
            self.assertIn(
                "BROKEN ACTIVE TARGET: shared-change references docs/changes/missing.md.",
                errors,
            )

    def test_legacy_book_target_still_requires_its_sibling_manifest(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            active = root / "docs/ACTIVE.md"
            active.write_text(
                "- `book`: example -> `Works/example book/STATUS.md`\n"
                "- `shared-change`: example -> `docs/changes/example.md`\n",
                encoding="utf-8",
            )
            work = root / "Works/example book"
            work.mkdir(parents=True)
            (work / "STATUS.md").write_text("# Status\n", encoding="utf-8")
            self.assertIn(
                "MISSING ACTIVE MANIFEST: Works/example book/STATUS.md has no sibling manifest.toml.",
                doc_length_check.validate(root),
            )
            (work / "manifest.toml").write_text("", encoding="utf-8")
            self.assertEqual(doc_length_check.validate(root), [])
            (work / "OTHER.md").write_text("# Other\n", encoding="utf-8")
            active.write_text(
                "- `book`: invalid -> `Works/example book/OTHER.md`\n"
                "- `shared-change`: example -> `docs/changes/example.md`\n",
                encoding="utf-8",
            )
            self.assertTrue(
                any(error.startswith("INVALID ACTIVE TARGET: book") for error in doc_length_check.validate(root))
            )

    def test_public_documents_work_without_private_libraries_or_local_task_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            clone = Path(directory)
            for name in ("AGENTS.md", "CONTEXT.md"):
                shutil.copyfile(ROOT / name, clone / name)
            shutil.copytree(ROOT / "docs", clone / "docs")
            self.assertFalse((clone / "Works").exists())
            self.assertFalse((clone / "Books").exists())
            self.assertFalse((clone / ".local").exists())
            self.assertEqual(doc_length_check.validate(clone), [])

    def test_private_change_boundary_rejects_concrete_paths_and_receipts(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            change = root / "docs/changes/example.md"
            original = change.read_text(encoding="utf-8")
            private_examples = (
                "`Works/Example title/STATUS.md`",
                "读取Works/Example/STATUS.md",
                "`./Works/Example/STATUS.md`",
                "`Books/Example title/source.pdf`",
                "`Books/Example.pdf`",
                "`.tmp/translator/diagnosis-123/result.json`",
                "见.tmp/translator/diagnosis-123/result.json",
                "`.local/translator/evidence/123abc/final/final.json`",
                "`.local/translator/evidence/final/final.json`",
                "`.local/translator/batches/actual-batch.json`",
                r"`C:\Users\person\source.pdf`",
                r"`D:\personal\output.epub`",
                "Example.epub, 4,043 bytes",
                "Example.pdf: 2.5 MiB",
                "交付副本 SHA-256: " + "a" * 64,
            )
            for example in private_examples:
                with self.subTest(example=example):
                    change.write_text(original + "\n" + example + "\n", encoding="utf-8")
                    errors = doc_length_check.validate(root)
                    self.assertTrue(
                        any(error.startswith("PRIVATE CHANGE CONTENT:") for error in errors),
                        errors,
                    )

    def test_private_change_boundary_allows_generic_and_public_examples(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            change = root / "docs/changes/example.md"
            with change.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n`Works/<book>/STATUS.md` `Books/<source>/` "
                    "`.tmp/translator/<work-key>/scratch/<agent>/` "
                    "`.local/translator/evidence/<work-key>/final/final.json` "
                    "`.local/translator/batches/<slug>.json` `C:\\Users\\<user>\\file`\n"
                    "《合成书名》、2026、`tests/test_example.py`；"
                    "[official](https://example.org/Works/Public/book)\n"
                    'Configuration example: sha256 = "' + "a" * 64 + '"\n'
                )
            self.assertEqual(doc_length_check.validate(root), [])

    def test_change_boundary_does_not_apply_to_other_documents_or_json_code_paths(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            protocol = root / "docs/workflow/PROTOCOL.md"
            protocol.write_text(
                "`Works/example/STATUS.md` `.local/translator/batches/missing.json`\n",
                encoding="utf-8",
            )
            work = root / "Works/example"
            work.mkdir(parents=True)
            (work / "STATUS.md").write_text("# Status\n", encoding="utf-8")
            self.assertEqual(doc_length_check.validate(root), [])

    def test_rejects_unknown_change_status(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            change = root / "docs" / "changes" / "example.md"
            change.write_text("# Example\n\n## Status\n\nclosed\n", encoding="utf-8")
            self.assertIn(
                "INVALID CHANGE STATUS: docs/changes/example.md has closed; expected proposed, accepted, or implemented.",
                doc_length_check.validate(root),
            )

    def test_requires_minimal_change_headings(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            change = root / "docs" / "changes" / "example.md"
            change.write_text("# Example\n\n## Status\n\naccepted\n", encoding="utf-8")
            errors = doc_length_check.validate(root)
            self.assertTrue(
                any(error.startswith("MISSING CHANGE HEADINGS:") for error in errors)
            )

    def test_rejects_broken_local_markdown_link(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            protocol = root / "docs" / "workflow" / "PROTOCOL.md"
            protocol.write_text("[missing](missing.md)\n", encoding="utf-8")
            errors = doc_length_check.validate(root)
            self.assertTrue(
                any(
                    error.startswith("BROKEN LINK: docs/workflow/PROTOCOL.md")
                    for error in errors
                )
            )

    def test_rejects_broken_root_relative_code_path(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            protocol = root / "docs" / "workflow" / "PROTOCOL.md"
            protocol.write_text("Use `docs/missing.md`.\n", encoding="utf-8")
            errors = doc_length_check.validate(root)
            self.assertTrue(
                any(error.startswith("BROKEN CODE PATH:") for error in errors)
            )

    def test_requires_change_to_be_active(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            active = root / "docs" / "ACTIVE.md"
            active.write_text("# Active\n", encoding="utf-8")
            self.assertIn(
                "ORPHAN CHANGE: docs/changes/example.md must be the ACTIVE shared-change.",
                doc_length_check.validate(root),
            )

    def test_implemented_change_can_remain_registered_without_active_slot(self) -> None:
        with self.validated_root() as directory:
            root = Path(directory)
            change = root / "docs/changes/example.md"
            change.write_text(
                change.read_text(encoding="utf-8").replace(
                    "\naccepted\n", "\nimplemented\n"
                ),
                encoding="utf-8",
            )
            (root / "docs/ACTIVE.md").write_text("# Active\n", encoding="utf-8")
            self.assertEqual(doc_length_check.validate(root), [])


if __name__ == "__main__":
    unittest.main()
