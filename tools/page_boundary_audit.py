"""AST-level source-page continuity candidate detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


TERMINAL_PUNCTUATION = frozenset("。！？!?．｡")
CONTINUATION_PUNCTUATION = frozenset("，、：；,:;；")
OPENING_PUNCTUATION = frozenset("（([【「『“‘<《")
CLOSING_PUNCTUATION = frozenset("）)]】」』”’>》，。！？!?；;：:、")
IGNORED_ATOM_KINDS = frozenset({"space", "marker"})
STRUCTURAL_CONTEXTS = frozenset({"Header", "Table", "Caption", "CodeBlock"})
LOW_PRIORITY_CONTEXTS = frozenset({"Plain", "ListItem", "TableCell", "Caption"})


@dataclass(frozen=True)
class Atom:
    kind: str
    text: str = ""


@dataclass(frozen=True)
class BlockSummary:
    kind: str
    first: Atom | None
    last: Atom | None


def source_page_marker(node: Any, format_name: str) -> int | None:
    if not isinstance(node, dict):
        return None
    if format_name == "markdown":
        if node.get("t") != "Span" or not isinstance(node.get("c"), list):
            return None
        attributes = node["c"][0] if node["c"] else None
        if not isinstance(attributes, list) or len(attributes) < 3:
            return None
        if "source-page" not in attributes[1]:
            return None
        for key, value in attributes[2]:
            if key == "data-page" and str(value).isdigit():
                return int(value)
        return None
    if format_name == "latex":
        if node.get("t") != "RawInline" or not isinstance(node.get("c"), list):
            return None
        if len(node["c"]) != 2 or node["c"][0] != "latex":
            return None
        match = re.fullmatch(
            r"\\(?:sourcepage|boundarypage)\{\s*(\d+)\s*\}",
            node["c"][1],
        )
        return int(match.group(1)) if match else None
    return None


def _flatten_inline(node: Any, format_name: str) -> list[Atom]:
    if not isinstance(node, dict):
        return []
    marker_page = source_page_marker(node, format_name)
    if marker_page is not None:
        return [Atom("marker", str(marker_page))]
    kind = node.get("t")
    content = node.get("c")
    if kind == "Str":
        return [Atom("text", str(content or ""))]
    if kind in {"Space", "SoftBreak", "LineBreak"}:
        return [Atom("space", " ")]
    if kind == "Math":
        value = content[1] if isinstance(content, list) and len(content) > 1 else ""
        return [Atom("formula", str(value))]
    if kind == "Image":
        return [Atom("image", "")]
    if kind == "Code":
        value = content[1] if isinstance(content, list) and len(content) > 1 else ""
        return [Atom("code", str(value))]
    if kind == "RawInline":
        value = content[1] if isinstance(content, list) and len(content) > 1 else ""
        return [Atom("raw", str(value))]
    if kind == "Note":
        return [Atom("note", "")]
    if kind == "Quoted" and isinstance(content, list) and len(content) > 1:
        return _flatten_inline_list(content[1], format_name)
    if kind in {
        "Emph",
        "Strong",
        "Underline",
        "Strikeout",
        "Superscript",
        "Subscript",
        "SmallCaps",
        "Link",
        "Span",
        "Cite",
    }:
        if isinstance(content, list):
            if kind == "Link" and len(content) > 1:
                return _flatten_inline_list(content[1], format_name)
            if kind == "Span" and len(content) > 1:
                return _flatten_inline_list(content[1], format_name)
            return _flatten_inline_list(content, format_name)
    return [Atom(str(kind or "other"), "")]


def _flatten_inline_list(nodes: Any, format_name: str) -> list[Atom]:
    if not isinstance(nodes, list):
        return []
    atoms: list[Atom] = []
    for node in nodes:
        atoms.extend(_flatten_inline(node, format_name))
    return atoms


def _summary_from_atoms(kind: str, atoms: Iterable[Atom]) -> BlockSummary:
    meaningful = [atom for atom in atoms if atom.kind not in IGNORED_ATOM_KINDS]
    return BlockSummary(
        kind=kind,
        first=meaningful[0] if meaningful else None,
        last=meaningful[-1] if meaningful else None,
    )


def _block_summary(block: Any, format_name: str, context: str = "Block") -> BlockSummary:
    if not isinstance(block, dict):
        return BlockSummary(context, None, None)
    kind = str(block.get("t") or context)
    content = block.get("c")
    if kind in {"Para", "Plain"}:
        return _summary_from_atoms(kind, _flatten_inline_list(content, format_name))
    if kind == "Header" and isinstance(content, list) and len(content) > 2:
        return _summary_from_atoms(kind, _flatten_inline_list(content[2], format_name))
    if kind == "CodeBlock":
        return BlockSummary(kind, Atom("code", ""), Atom("code", ""))
    if kind == "Table":
        return BlockSummary(kind, Atom("table", ""), Atom("table", ""))
    nested: list[BlockSummary] = []
    if isinstance(content, list):
        for child in content:
            if isinstance(child, dict) and "t" in child:
                nested.append(_block_summary(child, format_name, kind))
            elif isinstance(child, list):
                nested.extend(
                    _block_summary(item, format_name, kind)
                    for item in child
                    if isinstance(item, dict) and "t" in item
                )
    nested = [item for item in nested if item.first or item.last]
    if nested:
        return BlockSummary(
            kind,
            nested[0].first,
            nested[-1].last,
        )
    return BlockSummary(kind, None, None)


def _first_text(atom: Atom | None) -> str:
    if atom is None or atom.kind != "text":
        return ""
    return atom.text.lstrip()


def _last_text(atom: Atom | None) -> str:
    if atom is None or atom.kind != "text":
        return ""
    return atom.text.rstrip()


def _left_class(atom: Atom | None) -> str:
    if atom is None:
        return "none"
    if atom.kind != "text":
        return atom.kind
    text = _last_text(atom)
    if not text:
        return "none"
    last = text[-1]
    if last in TERMINAL_PUNCTUATION:
        return "terminal"
    if last in CONTINUATION_PUNCTUATION:
        return "continuation-punctuation"
    if last in OPENING_PUNCTUATION:
        return "opening-punctuation"
    if last in CLOSING_PUNCTUATION:
        return "closing-punctuation"
    return "text-nonterminal"


def _right_class(atom: Atom | None) -> str:
    if atom is None:
        return "none"
    if atom.kind != "text":
        return atom.kind
    text = _first_text(atom)
    if not text:
        return "none"
    if text[0] in CLOSING_PUNCTUATION:
        return "leading-punctuation"
    if text[0] in OPENING_PUNCTUATION:
        return "opening-punctuation"
    return "text"


def _context_penalty(context: str) -> int:
    if context in STRUCTURAL_CONTEXTS:
        return 2
    if context in LOW_PRIORITY_CONTEXTS:
        return 1
    return 0


def _finding(
    *,
    page: int,
    next_page: int,
    rule: str,
    severity: str,
    context: str,
    left: Atom | None,
    right: Atom | None,
    same_sequence: bool,
    ordinal: int,
    location: dict[str, Any] | None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "ordinal": ordinal,
        "page": page - 1 if page > 1 else None,
        "next_page": page,
        "rule": rule,
        "severity": severity,
        "context": context,
        "same_sequence": same_sequence,
        "left_kind": left.kind if left else "none",
        "right_kind": right.kind if right else "none",
        "left_class": _left_class(left),
        "right_class": _right_class(right),
        "left_text": _last_text(left)[:160],
        "right_text": _first_text(right)[:160],
    }
    if location:
        item["location"] = location
    return item


def _classify_marker(
    *,
    page: int,
    context: str,
    left: Atom | None,
    right: Atom | None,
    same_sequence: bool,
    ordinal: int,
    location: dict[str, Any] | None,
) -> dict[str, Any] | None:
    left_class = _left_class(left)
    right_class = _right_class(right)
    next_page = page
    penalty = _context_penalty(context)

    if right_class == "leading-punctuation":
        severity = "high" if penalty < 2 else "review"
        return _finding(
            page=page,
            next_page=next_page,
            rule="right-leading-punctuation",
            severity=severity,
            context=context,
            left=left,
            right=right,
            same_sequence=same_sequence,
            ordinal=ordinal,
            location=location,
        )
    if context == "Header":
        return _finding(
            page=page,
            next_page=next_page,
            rule="marker-inside-heading",
            severity="high",
            context=context,
            left=left,
            right=right,
            same_sequence=same_sequence,
            ordinal=ordinal,
            location=location,
        )
    if left_class == "opening-punctuation":
        return _finding(
            page=page,
            next_page=next_page,
            rule="left-opening-punctuation",
            severity="high" if penalty < 2 else "review",
            context=context,
            left=left,
            right=right,
            same_sequence=same_sequence,
            ordinal=ordinal,
            location=location,
        )
    if left_class == "continuation-punctuation":
        if right and right.kind in {"formula", "image", "table", "code"}:
            return None
        return _finding(
            page=page,
            next_page=next_page,
            rule="left-continuation-punctuation",
            severity="review" if penalty < 2 else "info",
            context=context,
            left=left,
            right=right,
            same_sequence=same_sequence,
            ordinal=ordinal,
            location=location,
        )
    if left_class == "text-nonterminal" and right and right.kind == "text":
        return _finding(
            page=page,
            next_page=next_page,
            rule="same-sequence-nonterminal",
            severity="review" if penalty == 0 else "info",
            context=context,
            left=left,
            right=right,
            same_sequence=same_sequence,
            ordinal=ordinal,
            location=location,
        )
    return None


def _scan_sequence(
    nodes: list[Any],
    *,
    format_name: str,
    context: str,
    previous: BlockSummary | None,
    following: BlockSummary | None,
    ordinal_start: int,
    locations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    atoms = _flatten_inline_list(nodes, format_name)
    marker_indexes = [
        index for index, atom in enumerate(atoms) if atom.kind == "marker"
    ]
    findings: list[dict[str, Any]] = []
    ordinal = ordinal_start
    for index in marker_indexes:
        ordinal += 1
        page = int(atoms[index].text)
        left = next(
            (atom for atom in reversed(atoms[:index]) if atom.kind not in IGNORED_ATOM_KINDS),
            None,
        )
        right = next(
            (atom for atom in atoms[index + 1 :] if atom.kind not in IGNORED_ATOM_KINDS),
            None,
        )
        left_in_sequence = left is not None
        right_in_sequence = right is not None
        same_sequence = left_in_sequence and right_in_sequence
        if left is None and previous:
            left = previous.last
        if right is None and following:
            right = following.first
        location = next(
            (
                candidate
                for candidate in locations
                if candidate.get("page_marker") == page
            ),
            locations[ordinal - 1] if ordinal - 1 < len(locations) else None,
        )
        finding = _classify_marker(
            page=page,
            context=context,
            left=left,
            right=right,
            same_sequence=same_sequence,
            ordinal=ordinal,
            location=location,
        )
        if finding:
            findings.append(finding)
    return findings, ordinal


def _walk_block_list(
    blocks: list[Any],
    *,
    format_name: str,
    context: str,
    ordinal_start: int,
    locations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    findings: list[dict[str, Any]] = []
    ordinal = ordinal_start
    summaries = [_block_summary(block, format_name, context) for block in blocks]
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        kind = str(block.get("t") or context)
        content = block.get("c")
        previous = summaries[index - 1] if index else None
        following = summaries[index + 1] if index + 1 < len(summaries) else None
        if kind in {"Para", "Plain"} and isinstance(content, list):
            found, ordinal = _scan_sequence(
                content,
                format_name=format_name,
                context=context if context != "document" else kind,
                previous=previous,
                following=following,
                ordinal_start=ordinal,
                locations=locations,
            )
            findings.extend(found)
        elif kind == "Header" and isinstance(content, list) and len(content) > 2:
            found, ordinal = _scan_sequence(
                content[2],
                format_name=format_name,
                context="Header",
                previous=previous,
                following=following,
                ordinal_start=ordinal,
                locations=locations,
            )
            findings.extend(found)
        if kind == "BlockQuote" and isinstance(content, list):
            found, ordinal = _walk_block_list(
                content,
                format_name=format_name,
                context="BlockQuote",
                ordinal_start=ordinal,
                locations=locations,
            )
            findings.extend(found)
        elif kind == "Div" and isinstance(content, list) and len(content) > 1:
            found, ordinal = _walk_block_list(
                content[1],
                format_name=format_name,
                context="Div",
                ordinal_start=ordinal,
                locations=locations,
            )
            findings.extend(found)
        elif kind in {"BulletList", "OrderedList"} and isinstance(content, list):
            items = content[1] if kind == "OrderedList" and len(content) > 1 else content
            for item in items:
                if isinstance(item, list):
                    found, ordinal = _walk_block_list(
                        item,
                        format_name=format_name,
                        context="ListItem",
                        ordinal_start=ordinal,
                        locations=locations,
                    )
                    findings.extend(found)
        elif kind == "DefinitionList" and isinstance(content, list):
            for item in content:
                if isinstance(item, list) and len(item) > 1:
                    for definition in item[1]:
                        if isinstance(definition, list):
                            found, ordinal = _walk_block_list(
                                definition,
                                format_name=format_name,
                                context="ListItem",
                                ordinal_start=ordinal,
                                locations=locations,
                            )
                            findings.extend(found)
        elif kind == "Figure" and isinstance(content, list) and len(content) > 2:
            found, ordinal = _walk_block_list(
                content[2],
                format_name=format_name,
                context="Figure",
                ordinal_start=ordinal,
                locations=locations,
            )
            findings.extend(found)
    return findings, ordinal


def analyze_document(
    document: dict[str, Any],
    *,
    format_name: str,
    locations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return ordered candidate findings for a Pandoc JSON document."""
    blocks = document.get("blocks") if isinstance(document, dict) else None
    if not isinstance(blocks, list):
        raise ValueError("Pandoc document lacks a block list")
    findings, _ = _walk_block_list(
        blocks,
        format_name=format_name,
        context="document",
        ordinal_start=0,
        locations=locations or [],
    )
    return findings


def summarize_findings(findings: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {"high": 0, "review": 0, "info": 0}
    for finding in findings:
        severity = finding.get("severity")
        if severity in summary:
            summary[severity] += 1
    summary["total"] = sum(summary.values())
    return summary


def count_markers(document: Any, format_name: str) -> int:
    count = 0

    def visit(node: Any) -> None:
        nonlocal count
        if isinstance(node, dict):
            if source_page_marker(node, format_name) is not None:
                count += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document)
    return count


def lexical_typst_findings(
    text: str,
    *,
    locations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fallback scanner for Typst before a full HTML/AST adapter is available."""
    marker_re = re.compile(r"#source-page\(\s*(\d+)\s*\)")
    findings: list[dict[str, Any]] = []
    matches = list(marker_re.finditer(text))
    for ordinal, match in enumerate(matches, 1):
        page = int(match.group(1))
        before = text[max(0, match.start() - 160) : match.start()]
        after = text[match.end() : match.end() + 160]
        left = next((char for char in reversed(before) if not char.isspace()), "")
        right = next((char for char in after if not char.isspace()), "")
        if left in CONTINUATION_PUNCTUATION:
            rule = "left-continuation-punctuation"
        elif left and left not in TERMINAL_PUNCTUATION and right:
            rule = "same-sequence-nonterminal"
        else:
            continue
        location = locations[ordinal - 1] if locations and ordinal <= len(locations) else None
        findings.append(
            _finding(
                page=page,
                next_page=page,
                rule=rule,
                severity="review",
                context="TypstText",
                left=Atom("text", left),
                right=Atom("text", right),
                same_sequence=True,
                ordinal=ordinal,
                location=location,
            )
        )
    return findings
