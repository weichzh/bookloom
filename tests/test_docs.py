from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
