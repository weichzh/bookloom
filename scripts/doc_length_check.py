#!/usr/bin/env python3

"""Validate the small, current documentation surface."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT_DOCUMENTS = frozenset({"AGENTS.md", "CONTEXT.md"})
FIXED_DOCUMENTS = frozenset(
    {
        "docs/ACTIVE.md",
        "docs/INDEX.md",
        "docs/codebase/CONTRACTS.md",
        "docs/workflow/PROTOCOL.md",
        "docs/workflow/TRANSLATION.md",
        "docs/workflow/EPUB.md",
    }
)
LIMITS = {
    "AGENTS.md": 100,
    "CONTEXT.md": 100,
    "docs/ACTIVE.md": 30,
    "docs/INDEX.md": 100,
    "docs/codebase/CONTRACTS.md": 220,
    "docs/workflow/PROTOCOL.md": 120,
    "docs/workflow/TRANSLATION.md": 240,
    "docs/workflow/EPUB.md": 240,
}
DEFAULT_LIMIT = 160
CHANGE_STATUSES = frozenset({"proposed", "accepted", "implemented"})
CHANGE_HEADINGS = frozenset(
    {"status", "problem", "scope", "acceptance criteria", "result", "review"}
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
STATUS_HEADING = re.compile(r"^##\s+Status\s*$", re.IGNORECASE)
ACTIVE_ENTRY = re.compile(r"^\s*-\s+`?(book|shared-change)`?\s*[:：]", re.IGNORECASE)
CODE_PATH = re.compile(r"`([^`]+\.md(?:#[^`\s]+)?)`")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize(path: Path) -> str:
    return path.as_posix()


def document_paths(root: Path) -> list[Path]:
    paths = [root / relative for relative in ROOT_DOCUMENTS]
    docs_root = root / "docs"
    if docs_root.exists():
        paths.extend(docs_root.rglob("*.md"))
    return sorted(path for path in paths if path.is_file())


def relative_path(root: Path, path: Path) -> str:
    return normalize(path.relative_to(root))


def is_allowed(relative: str) -> bool:
    return (
        relative in ROOT_DOCUMENTS
        or relative in FIXED_DOCUMENTS
        or (
            Path(relative).parent.as_posix() == "docs/changes"
            and relative.endswith(".md")
        )
    )


def limit_for(relative: str) -> int:
    return LIMITS.get(relative, DEFAULT_LIMIT)


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def markdown_link_targets(text: str) -> list[str]:
    targets = []
    for match in MARKDOWN_LINK.finditer(text):
        target = match.group(1).strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        targets.append(target.split(maxsplit=1)[0])
    return targets


def is_local_markdown_target(target: str) -> bool:
    path = target.split("#", 1)[0]
    return bool(path) and path.lower().endswith(".md") and "://" not in path


def resolve_link(origin: Path, target: str) -> Path:
    target_path = Path(unquote(target.split("#", 1)[0]))
    return (origin.parent / target_path).resolve()


def is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def current_markdown_targets(origin: Path, text: str) -> list[Path]:
    targets = []
    for target in markdown_link_targets(text):
        if is_local_markdown_target(target):
            targets.append(resolve_link(origin, target))
    return targets


def code_markdown_targets(root: Path, text: str) -> list[Path]:
    targets = []
    for raw in CODE_PATH.findall(text):
        raw = raw.split("#", 1)[0]
        if any(marker in raw for marker in ("<", ">", "*", "{")):
            continue
        normalized = raw.replace("\\", "/")
        if "/" not in normalized and normalized not in ROOT_DOCUMENTS:
            continue
        targets.append((root / normalized).resolve())
    return targets


def change_status(path: Path) -> str | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if STATUS_HEADING.fullmatch(line.strip()):
            for value in lines[index + 1 :]:
                value = value.strip()
                if value:
                    return value.lower()
            return None
    return None


def level_two_headings(path: Path) -> set[str]:
    return {
        line[3:].strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    }


def active_entries(path: Path) -> list[tuple[str, str | None]]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ACTIVE_ENTRY.match(line)
        if not match:
            continue
        paths = CODE_PATH.findall(line)
        entries.append(
            (match.group(1).lower(), paths[-1].split("#", 1)[0] if paths else None)
        )
    return entries


def validate(root: Path | None = None) -> list[str]:
    root = (root or repo_root()).resolve()
    errors: list[str] = []
    paths = document_paths(root)
    relative_paths = {relative_path(root, path) for path in paths}
    required = ROOT_DOCUMENTS | FIXED_DOCUMENTS

    for relative in sorted(required - relative_paths):
        errors.append(f"MISSING: {relative}")

    for path in paths:
        relative = relative_path(root, path)
        if not is_allowed(relative):
            errors.append(f"DISALLOWED: {relative}")
            continue
        count = line_count(path)
        limit = limit_for(relative)
        if count > limit:
            errors.append(f"TOO LONG: {relative} has {count} lines; limit is {limit}.")

        for target in current_markdown_targets(path, path.read_text(encoding="utf-8")):
            if not is_within(root, target) or not target.is_file():
                errors.append(
                    f"BROKEN LINK: {relative} references {normalize(target)}."
                )
        for target in code_markdown_targets(root, path.read_text(encoding="utf-8")):
            if not is_within(root, target) or not target.is_file():
                errors.append(
                    f"BROKEN CODE PATH: {relative} references {normalize(target)}."
                )

    index = root / "docs" / "INDEX.md"
    if index.is_file():
        index_targets = [
            relative_path(root, target)
            for target in current_markdown_targets(
                index, index.read_text(encoding="utf-8")
            )
            if is_within(root, target)
        ]
        registered = relative_paths - {"docs/INDEX.md"}
        for relative in sorted(registered - set(index_targets)):
            errors.append(f"UNREGISTERED: {relative} is missing from docs/INDEX.md.")
        for relative in sorted(set(index_targets) - registered):
            errors.append(f"EXTRA REGISTRATION: {relative} is not a current document.")
        for relative in sorted(set(index_targets)):
            if index_targets.count(relative) != 1:
                errors.append(
                    f"DUPLICATE REGISTRATION: {relative} appears in docs/INDEX.md more than once."
                )

    active = root / "docs" / "ACTIVE.md"
    active_change_targets: set[str] = set()
    if active.is_file():
        entries = active_entries(active)
        for line in active.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("-") and not ACTIVE_ENTRY.match(line):
                errors.append(f"INVALID ACTIVE ENTRY: {line.strip()}")
        for kind in ("book", "shared-change"):
            matches = [target for entry_kind, target in entries if entry_kind == kind]
            if len(matches) > 1:
                errors.append(f"TOO MANY ACTIVE: {kind} has {len(matches)} entries.")
            for target in matches:
                if not target:
                    errors.append(
                        f"MISSING ACTIVE TARGET: {kind} entry has no Markdown target."
                    )
                    continue
                resolved = (root / target).resolve()
                if not is_within(root, resolved) or not resolved.is_file():
                    errors.append(f"BROKEN ACTIVE TARGET: {kind} references {target}.")
                    continue
                normalized = normalize(Path(target))
                if kind == "book":
                    if not re.fullmatch(r"Works/[^/]+/STATUS\.md", normalized):
                        errors.append(
                            f"INVALID ACTIVE TARGET: book must reference Works/<book>/STATUS.md, got {target}."
                        )
                    elif not (resolved.parent / "manifest.toml").is_file():
                        errors.append(
                            f"MISSING ACTIVE MANIFEST: {target} has no sibling manifest.toml."
                        )
                elif not re.fullmatch(r"docs/changes/[^/]+\.md", normalized):
                    errors.append(
                        f"INVALID ACTIVE TARGET: shared-change must reference docs/changes/*.md, got {target}."
                    )
                else:
                    active_change_targets.add(normalized)

    changes_root = root / "docs" / "changes"
    if changes_root.exists():
        changes = sorted(changes_root.glob("*.md"))
        for path in changes:
            status = change_status(path)
            relative = relative_path(root, path)
            if status not in CHANGE_STATUSES:
                errors.append(
                    f"INVALID CHANGE STATUS: {relative} has {status or 'no status'}; "
                    f"expected proposed, accepted, or implemented."
                )
            missing_headings = CHANGE_HEADINGS - level_two_headings(path)
            if missing_headings:
                errors.append(
                    f"MISSING CHANGE HEADINGS: {relative} lacks "
                    + ", ".join(sorted(missing_headings))
                    + "."
                )
        change_paths = {relative_path(root, path) for path in changes}
        for relative in sorted(change_paths - active_change_targets):
            errors.append(
                f"ORPHAN CHANGE: {relative} must be the ACTIVE shared-change."
            )

    return errors


def main(root: Path | None = None) -> int:
    errors = validate(root)
    if errors:
        print("\n".join(errors))
        return 1
    print("doc_length_check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
