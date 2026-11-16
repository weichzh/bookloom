"""Pure, reusable source-AST / EPUB navigation and code preservation gates.

Callers own parsing with the formal reader and supply final XHTML in spine
order.  These checks establish semantic consistency, not source-page review.
No book-specific inventories, subprocesses, or filesystem writes live here.
"""

from __future__ import annotations

import json
import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any


class EpubSemanticError(ValueError):
    """The generated artifact disagrees with its declared source semantics."""


def _name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _visible(text: str) -> str:
    return " ".join(text.split())


# The writer's formula-image alternatives and the source Math inlines must
# use the same closed mapping. Everything without a TeX command is retained
# verbatim, including numbers and script characters.
_NAVIGATION_FORMULAS = {
    "\\blacktriangleright": "▶",
    "\\blacksquare": "■",
    "\\mathcal L_A": "L_A",
    "T_{\\mathrm{odl}}": "T_odl",
    "T_{\\mathrm{BA}}^d": "T_BA^d",
    "\\Pi_2": "Π₂",
    "\\omega_1": "ω₁",
    "T_{\\mathrm{dag}}": "T_dag",
    "T_{\\mathrm{dag}}^1": "T_dag^1",
    "T_{\\mathrm{odag}}": "T_odag",
    "\\mathcal Z_0": "Z_0",
    "T_{\\mathrm{pr}}": "T_pr",
    "T_{\\mathrm{oasg}}^*": "T_oasg^*",
    "\\Sigma_1": "Σ₁",
    "T_{\\mathrm{PA}}": "T_PA",
    "\\mathrm{PA}_f": "PA_f",
    "\\delta": "δ",
    "K^\\star=0": "K*=0",
    "\\infty": "∞",
    "\\varepsilon": "ε",
    "\\infty.1": "∞.1",
    "\\infty.2.2": "∞.2.2",
    "\\Pi_1": "Π₁",
}
_FORMULA_CLASSES = frozenset({"math-inline", "math-display"})
GLADTEX_FORMATTING_COMMANDS = (
    r"\ ",
    r"\,",
    r"\;",
    r"\big",
    r"\Big",
    r"\left",
    r"\right",
    r"\limits",
)


def gladtex_alt_text(formula: str) -> str:
    changed = True
    while changed:
        changed = False
        for command in GLADTEX_FORMATTING_COMMANDS:
            index = formula.find(command)
            if index < 0 or (index > 0 and formula[index - 1] == "\\"):
                continue
            end = index + len(command)
            if (
                end >= len(formula)
                or not command[-1].isalpha()
                or not formula[end].isalpha()
            ):
                formula = (
                    formula[:index] + " " + formula[index + len(command) :]
                ).replace("  ", " ")
                changed = True
    return formula.strip()


def navigation_formula_text(formula: str) -> str:
    """Render only the existing allowlisted TeX formulas for navigation."""
    marker = formula.strip()
    if "\\" not in marker:
        return formula
    try:
        return _NAVIGATION_FORMULAS[marker]
    except KeyError as error:
        raise EpubSemanticError(f"导航公式无法安全转为文本：{marker}") from error


def ast_nodes(value: Any) -> Iterable[dict[str, Any]]:
    """Visit typed Pandoc nodes without interpreting literal strings as AST."""
    if isinstance(value, dict):
        if "t" in value:
            yield value
        for child in value.values():
            yield from ast_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from ast_nodes(child)


def inline_text(
    inlines: Sequence[Mapping[str, Any]], *, include_noterefs: bool = False,
    navigation_math_labels: bool = False, allow_native_notes: bool = False,
) -> str:
    """Visible heading text, including the shared cn-concept rendering."""
    return "".join(text for _, text in _inline_segments(
        inlines, include_noterefs=include_noterefs, navigation_math_labels=navigation_math_labels,
        allow_native_notes=allow_native_notes,
    ))


def _inline_segments(
    inlines: Sequence[Mapping[str, Any]], *, include_noterefs: bool = False,
    navigation_math_labels: bool = False, allow_native_notes: bool = False,
) -> list[tuple[str, str]]:
    """Keep math objects separate from text while rendering the same label."""
    def render(children: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
        return _inline_segments(children, include_noterefs=include_noterefs,
                                navigation_math_labels=navigation_math_labels,
                                allow_native_notes=allow_native_notes)

    output: list[tuple[str, str]] = []
    for node in inlines:
        kind, content = node["t"], node.get("c")
        if kind == "Str":
            output.append(("text", content))
        elif kind in {"Space", "SoftBreak", "LineBreak"}:
            output.append(("text", " "))
        elif kind == "Math":
            text = navigation_formula_text(content[1]) if navigation_math_labels else content[1]
            output.append(("math", text))
        elif kind == "Code":
            output.append(("text", content[1]))
        elif kind == "Span":
            attributes, children = content
            if set(attributes[1]) & {"source-page", "source-unit"}:
                continue
            output.extend(render(children))
            if "cn-concept" in attributes[1]:
                original = dict(attributes[2]).get("data-original")
                if original:
                    output.append(("text", f"（{original}）"))
        elif kind in {"Link", "Image"}:
            if (kind == "Link" and not include_noterefs
                    and "noteref" in dict(content[0][2]).get("epub:type", "").split()):
                continue
            if kind == "Image" and set(content[0][1]) & _FORMULA_CLASSES:
                alt = inline_text(content[1], include_noterefs=include_noterefs,
                                  allow_native_notes=allow_native_notes)
                output.append(("math", navigation_formula_text(alt) if navigation_math_labels else alt))
            elif kind == "Image":
                # An ordinary image is one alternative-text object, not a
                # mathematical object even if its alt contains inline Math.
                output.append(("text", inline_text(content[1], include_noterefs=include_noterefs,
                                                   allow_native_notes=allow_native_notes)))
            else:
                output.extend(render(content[1]))
        elif kind == "Quoted":
            quote = ('“', '”') if content[0]["t"] == "DoubleQuote" else ('‘', '’')
            output.append(("text", quote[0]))
            output.extend(render(content[1]))
            output.append(("text", quote[1]))
        elif kind == "Cite":
            output.extend(render(content[1]))
        elif kind == "Note":
            if not allow_native_notes:
                raise EpubSemanticError("目录标题中含脚注，无法无歧义核定可见标签")
        elif kind in {"Emph", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps", "Underline"}:
            output.extend(render(content))
        else:
            raise EpubSemanticError(f"无法核定目录标签的内联节点：{kind}")
    return output


@dataclass(frozen=True)
class OutlineEntry:
    identifier: str
    label: str
    level: int
    depth: int
    parent: str | None
    href: str = ""


def outline_from_ast(
    document: Mapping[str, Any], toc_depth: int = 3, *, number_sections: bool = False,
    navigation_math_labels: bool = False, allow_native_notes: bool = False,
) -> tuple[OutlineEntry, ...]:
    """Derive listed labels; opt into Pandoc numbering only when writer does."""
    if isinstance(toc_depth, bool) or not isinstance(toc_depth, int) or toc_depth < 1:
        raise EpubSemanticError("toc_depth 必须是正整数")
    if type(number_sections) is not bool:
        raise EpubSemanticError("number_sections 必须是布尔值")
    if type(navigation_math_labels) is not bool:
        raise EpubSemanticError("navigation_math_labels 必须是布尔值")
    result: list[OutlineEntry] = []
    stack: list[OutlineEntry] = []
    seen: set[str] = set()
    headers = [node for node in ast_nodes(document.get("blocks", [])) if node["t"] == "Header"]
    # Pandoc's section-number vectors are rooted at the minimum heading level
    # in the entire document, not the first listed heading or the TOC cutoff.
    # Unlisted headings still participate. Unnumbered headings neither advance
    # a counter nor reset their descendants' counters.
    base_level = min((node["c"][0] for node in headers), default=1)
    counters = [0] * (max((node["c"][0] for node in headers), default=base_level) - base_level + 1)
    for node in headers:
        level, attributes, children = node["c"]
        identifier, classes, _ = attributes
        number = ""
        if number_sections and "unnumbered" not in classes:
            index = level - base_level
            counters[index] += 1
            counters[index + 1:] = [0] * (len(counters) - index - 1)
            number = ".".join(str(value) for value in counters[:index + 1])
        if level > toc_depth or "unlisted" in classes:
            continue
        if not identifier or identifier in seen:
            raise EpubSemanticError(f"正式目录标题 ID 为空或重复：{identifier!r}")
        seen.add(identifier)
        while stack and stack[-1].level >= level:
            stack.pop()
        label = _visible(inline_text(children, navigation_math_labels=navigation_math_labels,
                                     allow_native_notes=allow_native_notes))
        if number:
            label = f"{number} {label}"
        entry = OutlineEntry(identifier, label, level,
                             len(stack) + 1, stack[-1].identifier if stack else None)
        if not entry.label:
            raise EpubSemanticError(f"正式目录标题为空：{identifier}")
        result.append(entry)
        stack.append(entry)
    return tuple(result)


def outline_from_nav(toc: ET.Element) -> tuple[OutlineEntry, ...]:
    result: list[OutlineEntry] = []

    def visit(ordered: ET.Element, depth: int, parent: str | None) -> None:
        for item in ordered:
            if _name(item) != "li":
                raise EpubSemanticError("EPUB 目录 ol 含非 li 子节点")
            labels = [child for child in item if _name(child) in {"a", "span"}]
            nested = [child for child in item if _name(child) == "ol"]
            if len(labels) != 1 or _name(labels[0]) != "a" or len(nested) > 1:
                raise EpubSemanticError("EPUB 目录项必须有唯一链接与至多一个子目录")
            label = labels[0]
            href = label.attrib.get("href", "")
            parts = urllib.parse.urlsplit(href)
            identifier = urllib.parse.unquote(parts.fragment)
            if parts.scheme or parts.netloc or parts.query or not identifier:
                raise EpubSemanticError(f"EPUB 目录 href 必须指向包内标题片段：{href!r}")
            entry = OutlineEntry(identifier, _visible("".join(label.itertext())),
                                 0, depth, parent, href)
            result.append(entry)
            if nested:
                visit(nested[0], depth + 1, identifier)

    roots = [child for child in toc if _name(child) == "ol"]
    if len(roots) != 1:
        raise EpubSemanticError("EPUB 目录必须有唯一根 ol")
    visit(roots[0], 1, None)
    return tuple(result)


def normalize_noteref_labels(
    document: Mapping[str, Any], navigation: ET.Element, *, toc_depth: int = 3,
    number_sections: bool = False, ncx: bool = False, navigation_math_labels: bool = False,
    allow_native_notes: bool = False,
) -> int:
    """Normalize source-proven note callouts and, when requested, NCX math.

    The complete pre-cleaning label must match the source. NCX may initially
    contain raw source formulas, but is changed only to the same source-derived
    label as the controlled nav. Body headings, reference identities, hrefs and
    ordinary superscripts are never rewritten.
    """
    def serialized(identifier: str) -> str:
        return identifier if not identifier or identifier[0].isalpha() else "id_" + identifier

    headers = {node["c"][1][0]: node for node in ast_nodes(document.get("blocks", []))
               if node["t"] == "Header"}
    labels: dict[str, tuple[str, set[str]]] = {}
    entries = outline_from_ast(document, toc_depth, number_sections=number_sections,
                               navigation_math_labels=navigation_math_labels,
                               allow_native_notes=allow_native_notes)
    raw_entries = ({entry.identifier: entry for entry in
                    outline_from_ast(document, toc_depth, number_sections=number_sections,
                                     allow_native_notes=allow_native_notes)}
                   if ncx and navigation_math_labels else {})
    for entry in entries:
        children = headers[entry.identifier]["c"][2]
        nodes = list(ast_nodes(children))
        has_noteref = any(node["t"] == "Link" and "noteref" in dict(node["c"][0][2]).get("epub:type", "").split()
                          for node in nodes)
        has_formula = any(node["t"] == "Math" or
                          (node["t"] == "Image" and set(node["c"][0][1]) & _FORMULA_CLASSES)
                          for node in nodes)
        if not has_noteref and not (ncx and navigation_math_labels and has_formula):
            continue
        base = _visible(inline_text(children, navigation_math_labels=navigation_math_labels,
                                    allow_native_notes=allow_native_notes))
        full = _visible(inline_text(children, include_noterefs=True,
                                    navigation_math_labels=navigation_math_labels,
                                    allow_native_notes=allow_native_notes))
        prefix = entry.label[:-len(base)]
        allowed = {entry.label, prefix + full}
        if ncx and navigation_math_labels:
            raw_base = _visible(inline_text(children, allow_native_notes=allow_native_notes))
            raw_full = _visible(inline_text(children, include_noterefs=True,
                                            allow_native_notes=allow_native_notes))
            raw_entry = raw_entries[entry.identifier]
            raw_prefix = raw_entry.label[:-len(raw_base)]
            allowed.update((raw_entry.label, raw_prefix + raw_full))
        labels[serialized(entry.identifier)] = (entry.label, allowed)
    for node in ast_nodes(document.get("blocks", [])):
        if node["t"] == "Div":
            attributes, blocks = node["c"]
            if attributes[0] and blocks and blocks[0].get("t") == "Header":
                label = labels.get(serialized(blocks[0]["c"][1][0]))
                if label is not None:
                    labels[serialized(attributes[0])] = label
    changed = 0
    for node in list(navigation.iter()):
        if ncx:
            if _name(node) != "navPoint":
                continue
            content = next((child for child in node if _name(child) == "content"), None)
            nav_label = next((child for child in node if _name(child) == "navLabel"), None)
            label_node = next((child for child in nav_label if _name(child) == "text"), None) if nav_label is not None else None
            href = content.get("src", "") if content is not None else ""
        else:
            if _name(node) != "a":
                continue
            label_node, href = node, node.get("href", "")
        parsed = urllib.parse.urlsplit(href)
        wanted = labels.get(urllib.parse.unquote(parsed.fragment))
        if wanted is None:
            continue
        if parsed.scheme or parsed.netloc or parsed.query or label_node is None:
            raise EpubSemanticError("标题注目录标签必须绑定包内有效标题")
        actual = _visible("".join(label_node.itertext()))
        if actual not in wanted[1]:
            raise EpubSemanticError(f"标题注清理前目录标签与原标题不符：{href}；{actual!r}")
        if actual == wanted[0]:
            continue
        if any("pagebreak" in child.get("{http://www.idpf.org/2007/ops}type", "").split()
               for child in label_node.iter()):
            raise EpubSemanticError("标题注清理不能删除导航中的原生页点")
        for child in list(label_node):
            label_node.remove(child)
        label_node.text = wanted[0]
        changed += 1
    return changed


def heading_text(heading: ET.Element) -> str:
    """Read text and image alternatives, keeping tails but excluding note links."""
    if _name(heading) == "img":
        return heading.get("alt", "")
    output = [heading.text or ""]
    for child in heading:
        types = child.get("{http://www.idpf.org/2007/ops}type", child.get("epub:type", "")).split()
        if not (_name(child) == "a" and "noteref" in types):
            output.append(heading_text(child))
        output.append(child.tail or "")
    return "".join(output)


def _heading_segments(heading: ET.Element) -> list[tuple[str, str]]:
    if _name(heading) == "img":
        is_formula = bool(set(heading.get("class", "").split()) & _FORMULA_CLASSES)
        if is_formula and "alt" not in heading.attrib:
            raise EpubSemanticError("正文标题公式图像缺少 alt")
        return [("math" if is_formula else "text", heading.get("alt", ""))]
    result = [("text", heading.text or "")]
    for child in heading:
        types = child.get("{http://www.idpf.org/2007/ops}type", child.get("epub:type", "")).split()
        if not (_name(child) == "a" and "noteref" in types):
            result.extend(_heading_segments(child))
        result.append(("text", child.tail or ""))
    return result


def _math_positions(segments: Sequence[tuple[str, str]], prefix: str = "") -> tuple[tuple[int, str], ...]:
    """Compare exact formula objects at their positions in visible heading text."""
    preceding = prefix
    result = []
    for kind, text in segments:
        if kind == "math":
            result.append((len(_visible(preceding)), text))
        preceding += text
    return tuple(result)


def has_native_heading_notes(document: Mapping[str, Any]) -> bool:
    return any(
        node.get("t") == "Header" and any(child.get("t") == "Note" for child in ast_nodes(node["c"][2]))
        for node in ast_nodes(document.get("blocks", []))
    )


@dataclass(frozen=True)
class _WriterSection:
    level: int
    heading: Any
    blocks: tuple[Any, ...]


@dataclass(frozen=True)
class _WriterContainer:
    blocks: tuple[Any, ...]


def _writer_sections(blocks: Sequence[Any]) -> tuple[Any, ...]:
    """The section boundaries relevant to Pandoc's EPUB chunk writer.

    Pandoc sectionizes top-level headers and eligible single-section Divs;
    lists, quotations and other Divs remain opaque to chunking.  The latter
    still contribute notes when their containing chunk is written.
    """
    def header_at_or_above(level: int, block: Any) -> bool:
        while isinstance(block, dict) and block.get("t") == "Div" and block["c"][1]:
            block = block["c"][1][0]
        return isinstance(block, dict) and block.get("t") == "Header" and block["c"][0] <= level

    result: list[Any] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        kind = block.get("t") if isinstance(block, dict) else None
        if kind == "Header":
            level = block["c"][0]
            end = i + 1
            while end < len(blocks) and not header_at_or_above(level, blocks[end]):
                end += 1
            result.append(_WriterSection(level, block, _writer_sections(blocks[i + 1:end])))
            i = end
            continue
        if kind == "Div":
            attributes, inner = block["c"]
            classes = set(attributes[1])
            first_header = bool(inner) and isinstance(inner[0], dict) and inner[0].get("t") == "Header"
            eligible = first_header and ("section" in classes or not classes & {"column", "columns", "fragment", "cell"})
            if eligible:
                level = inner[0]["c"][0]
                eligible = all(not isinstance(child, dict) or child.get("t") != "Header" or child["c"][0] > level
                               for child in inner[1:])
            sections = _writer_sections(inner)
            if eligible and len(sections) == 1 and isinstance(sections[0], _WriterSection):
                result.append(sections[0])
            else:
                result.append(_WriterContainer(sections))
        else:
            result.append(block)
        i += 1
    return tuple(result)


def _writer_chunks(sections: Sequence[Any], split_level: int) -> Iterable[tuple[Any, ...]]:
    pending = list(sections)
    while pending:
        first = next((i for i, node in enumerate(pending)
                      if isinstance(node, _WriterSection) and node.level <= split_level), len(pending))
        if first:
            yield tuple(pending[:first])
            pending = pending[first:]
            continue
        section = pending[0]
        if section.level == split_level:
            yield (section,)
            pending = pending[1:]
        else:
            first_child = next((i for i, node in enumerate(section.blocks)
                                if isinstance(node, _WriterSection) and node.level <= split_level), len(section.blocks))
            yield (_WriterSection(section.level, section.heading, section.blocks[:first_child]),)
            pending = [*section.blocks[first_child:], *pending[1:]]


def _epub_synthetic_title(meta: Mapping[str, Any]) -> list[Any]:
    """Extract docTitle' inlines for the EPUB writer's implicit first H1."""
    def inlines(value: Any) -> list[Any]:
        if not isinstance(value, dict):
            return []
        kind, content = value.get("t"), value.get("c")
        if kind == "MetaInlines":
            return content
        if kind == "MetaBlocks" and len(content) == 1 and content[0].get("t") in {"Para", "Plain"}:
            return content[0]["c"]
        if kind == "MetaMap":
            label = content.get("type", {})
            if label.get("t") == "MetaString" and label.get("c") == "main":
                return inlines(content.get("text"))
        if kind == "MetaList":
            return [inline for entry in content for inline in inlines(entry)]
        return []
    return inlines(meta.get("title"))


def native_heading_notes(
    document: Mapping[str, Any], split_level: int, *, title_overridden: bool = False,
) -> dict[str, list[tuple[Mapping[str, Any], int]]]:
    """Count Pandoc native Notes per EPUB writer split, including body notes.

    Explicit noteref Links are not writer Notes.  Counting only title Notes, or
    counting globally across split files, gives the wrong fn/fnref identities.
    """
    if isinstance(split_level, bool) or not isinstance(split_level, int) or not 1 <= split_level <= 6:
        raise EpubSemanticError("原生标题注需要实际 EPUB writer 拆分层级 1--6")
    result: dict[str, list[tuple[Mapping[str, Any], int]]] = {}
    header_ids: dict[str, int] = {}
    blocks = list(document.get("blocks", []))
    first = blocks[0] if blocks else None
    has_first_heading = isinstance(first, dict) and (
        first.get("t") == "Header" and first["c"][0] == 1
        or first.get("t") == "Div" and bool(first["c"][1]) and first["c"][1][0].get("t") == "Header"
    )
    synthetic = None
    if not has_first_heading:
        # EPUB.hs inserts a level-1 title before sectionizing such a document.
        title = [] if title_overridden else _epub_synthetic_title(document.get("meta", {}))
        synthetic = {"t": "Header", "c": [1, ["", ["unnumbered"], []], title]}
        blocks.insert(0, synthetic)
    sections = list(_writer_sections(blocks))
    # The synthetic heading is not a source heading but its notes consume writer IDs.
    if synthetic is not None and sections and isinstance(sections[0], _WriterSection):
        first_section = sections[0]
        sections[0] = _WriterSection(1, {"t": "Plain", "c": synthetic["c"][2]}, first_section.blocks)
    counter = 0

    def visit(value: Any, header: str | None = None) -> None:
        nonlocal counter
        if isinstance(value, _WriterSection):
            visit(value.heading)
            visit(value.blocks)
        elif isinstance(value, _WriterContainer):
            visit(value.blocks)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, header)
        elif isinstance(value, dict):
            kind, content = value.get("t"), value.get("c")
            if kind == "Header":
                _, attributes, children = content
                identifier = attributes[0]
                header_ids[identifier] = header_ids.get(identifier, 0) + 1
                if any(node.get("t") == "Note" for node in ast_nodes(children)) and not identifier:
                    raise EpubSemanticError("带原生注的标题必须有稳定 ID")
                visit(children, identifier)
                return
            if kind == "Note":
                counter += 1
                if header is not None:
                    result.setdefault(header, []).append((value, counter))
                return  # The writer consumes one Note object, not its body as more references.
            if kind == "Image":
                # Pandoc stringifies alt text; even Note objects there do not
                # create references in the output or consume a writer number.
                if header is not None and any(node.get("t") == "Note" for node in ast_nodes(content[1])):
                    raise EpubSemanticError("标题图片替代文本内的原生注不会由 EPUB writer 保留")
                return
            if kind == "Cite":
                # Citation metadata is not rendered by the HTML writer; its
                # second field is the visible inline content.
                if header is not None and any(node.get("t") == "Note" for node in ast_nodes(content[0])):
                    raise EpubSemanticError("标题引用元数据内的原生注不会由 EPUB writer 保留")
                visit(content[1], header)
                return
            if kind == "Link":
                if (header is not None and "noteref" in dict(content[0][2]).get("epub:type", "").split()
                        and any(node.get("t") == "Note" for node in ast_nodes(content[1]))):
                    raise EpubSemanticError("原生标题注不能嵌套在显式标题注引用中")
                visit(content[1], header)
                return
            for child in value.values():
                visit(child, header)

    for chunk in _writer_chunks(sections, split_level):
        counter = 0
        visit(chunk)
    if any(header_ids[identifier] != 1 for identifier in result):
        raise EpubSemanticError("带原生注的来源标题 ID 必须唯一")
    return result


def _source_heading_note_parts(inlines: Sequence[Mapping[str, Any]]) -> list[tuple[str, Any]]:
    """Keep native and explicit references at their exact text positions."""
    parts: list[tuple[str, Any]] = []

    def visit(nodes: Sequence[Mapping[str, Any]]) -> None:
        for node in nodes:
            kind, content = node["t"], node.get("c")
            if kind == "Note":
                parts.append(("note", node))
            elif kind == "Link" and "noteref" in dict(content[0][2]).get("epub:type", "").split():
                parts.append(("link", node))
            elif kind == "Span":
                attributes, children = content
                if set(attributes[1]) & {"source-page", "source-unit"}:
                    continue
                visit(children)
                original = dict(attributes[2]).get("data-original")
                if "cn-concept" in attributes[1] and original:
                    parts.append(("text", f"（{original}）"))
            elif kind in {"Emph", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps", "Underline"}:
                visit(content)
            elif kind == "Quoted":
                left, right = ('“', '”') if content[0]["t"] == "DoubleQuote" else ('‘', '’')
                parts.append(("text", left))
                visit(content[1])
                parts.append(("text", right))
            elif kind in {"Link", "Cite"}:
                visit(content[1])
            else:
                parts.extend(_inline_segments([node], allow_native_notes=True))

    visit(inlines)
    return parts


def _actual_heading_note_parts(heading: ET.Element) -> list[tuple[str, Any]]:
    parts: list[tuple[str, Any]] = []

    def visit(node: ET.Element) -> None:
        types = node.get("{http://www.idpf.org/2007/ops}type", node.get("epub:type", "")).split()
        if _name(node) == "a" and "noteref" in types:
            parts.append(("ref", node))
            return
        if _name(node) == "img":
            parts.append(("text", node.get("alt", "")))
            return
        parts.append(("text", node.text or ""))
        for child in node:
            visit(child)
            parts.append(("text", child.tail or ""))

    visit(heading)
    return parts


def _note_inline_tokens(inlines: Sequence[Mapping[str, Any]], inherited: frozenset[str] = frozenset()) -> list[Token]:
    """Use the existing complete-text/math/style token contract for note text."""
    tokens: list[Token] = []
    for node in inlines:
        kind, content = node["t"], node.get("c")
        if kind == "Span":
            attributes, children = content
            classes = set(attributes[1])
            if classes & {"source-page", "source-unit"}:
                continue
            tokens.extend(_note_inline_tokens(children, inherited | (classes & SEMANTIC_CLASSES)))
            original = dict(attributes[2]).get("data-original")
            if "cn-concept" in classes and original:
                tokens.extend(_tokens(f"（{original}）", inherited))
        elif kind in _INLINE_STYLES:
            tokens.extend(_note_inline_tokens(content, inherited | {_INLINE_STYLES[kind]}))
        elif kind == "Quoted":
            left, right = ('“', '”') if content[0]["t"] == "DoubleQuote" else ('‘', '’')
            tokens.extend(_tokens(left, inherited))
            tokens.extend(_note_inline_tokens(content[1], inherited))
            tokens.extend(_tokens(right, inherited))
        elif kind in {"Link", "Cite"}:
            tokens.extend(_note_inline_tokens(content[1], inherited))
        elif kind in {"RawInline", "RawBlock"} and content[0] == "html" and "source-page" in content[1]:
            continue
        else:
            tokens.extend(_ast_inline_tokens([node], inherited))
    return tokens


def _note_record(tokens: Sequence[Token]) -> CodeRecord:
    # Pandoc emits a text-presentation selector for some Unicode arrows.
    # It is not part of the arrow itself and must not cause a false mismatch.
    clean: list[Token] = []
    for token in tokens:
        char = token[0]
        if char in {"\ufe0e", "\ufe0f"} and clean and "\u2190" <= clean[-1][0] <= "\u21ff":
            continue
        clean.append(token)
    return _record("note-paragraph", clean)


def _native_note_source_records(note: Mapping[str, Any]) -> tuple[CodeRecord, ...]:
    records: list[CodeRecord] = []
    styles = {"DefaultStyle": "1", "Decimal": "1", "LowerAlpha": "a", "UpperAlpha": "A",
              "LowerRoman": "i", "UpperRoman": "I"}

    def visit(blocks: Sequence[Mapping[str, Any]]) -> None:
        for block in blocks:
            kind, content = block.get("t"), block.get("c")
            if kind in {"Para", "Plain"}:
                records.append(_note_record(_note_inline_tokens(content)))
            elif kind == "CodeBlock":
                records.append(CodeRecord("note-code", content[1]))
            elif kind == "BlockQuote":
                records.append(CodeRecord("note-open-blockquote", ""))
                visit(content)
                records.append(CodeRecord("note-close-blockquote", ""))
            elif kind in {"BulletList", "OrderedList"}:
                if kind == "OrderedList":
                    attributes, items = content
                    start, style = attributes[0], attributes[1]["t"]
                    if style not in styles:
                        raise EpubSemanticError(f"原生标题注含不支持的有序列表编号：{style}")
                    list_kind = f"ol:{start}:{styles[style]}"
                else:
                    items, list_kind = content, "ul"
                records.append(CodeRecord("note-open-" + list_kind, ""))
                for item in items:
                    records.append(CodeRecord("note-open-li", ""))
                    visit(item)
                    records.append(CodeRecord("note-close-li", ""))
                records.append(CodeRecord("note-close-" + list_kind, ""))
            else:
                raise EpubSemanticError(f"原生标题注包含尚不支持的块语义：{kind}")

    visit(note["c"])
    return tuple(records)


def _native_note_xhtml_records(note: ET.Element) -> tuple[CodeRecord, ...]:
    """Remove only standard backlink anchors, retaining all other note text."""
    copy = ET.fromstring(ET.tostring(note, encoding="utf-8"))
    for parent in copy.iter():
        for child in list(parent):
            types = child.get("{http://www.idpf.org/2007/ops}type", child.get("epub:type", "")).split()
            if _name(child) != "a" or "backlink" not in types:
                continue
            index = list(parent).index(child)
            if index:
                previous = parent[index - 1]
                previous.tail = (previous.tail or "") + (child.tail or "")
            else:
                parent.text = (parent.text or "") + (child.tail or "")
            parent.remove(child)
    records: list[CodeRecord] = []
    block_names = {"p", "pre", "blockquote", "ul", "ol"}

    def visit(node: ET.Element) -> None:
        name = _name(node)
        if name == "p":
            record = _note_record(_xml_rich_tokens(node))
            if not record.text and "footnote-backlink" in node.get("class", "").split():
                return
            records.append(record)
        elif name == "pre":
            records.append(CodeRecord("note-code", "".join(node.itertext())))
        elif name == "blockquote":
            records.append(CodeRecord("note-open-blockquote", ""))
            content(node)
            records.append(CodeRecord("note-close-blockquote", ""))
        elif name in {"ul", "ol"}:
            list_kind = "ul" if name == "ul" else f"ol:{node.get('start', '1')}:{node.get('type', '1')}"
            records.append(CodeRecord("note-open-" + list_kind, ""))
            for item in node:
                if _name(item) != "li":
                    raise EpubSemanticError("原生标题注列表包含非列表项")
                records.append(CodeRecord("note-open-li", ""))
                content(item)
                records.append(CodeRecord("note-close-li", ""))
            records.append(CodeRecord("note-close-" + list_kind, ""))
        else:
            raise EpubSemanticError(f"原生标题注成品包含尚不支持的块语义：{name}")

    def content(parent: ET.Element) -> None:
        inline: list[Token] = _tokens(parent.text or "", frozenset())
        for child in parent:
            if _name(child) in block_names:
                if _visible("".join(token[0] for token in inline)):
                    records.append(_note_record(inline))
                inline = []
                visit(child)
            else:
                inline.extend(_xml_rich_tokens(child))
            inline.extend(_tokens(child.tail or "", frozenset()))
        if _visible("".join(token[0] for token in inline)):
            records.append(_note_record(inline))

    content(copy)
    return tuple(records)


def _validate_native_note_links(
    source: Mapping[str, Any], note: ET.Element, *, path: str,
    documents: Mapping[str, ET.Element], epub_identifiers: bool,
) -> None:
    expected: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            kind, content = value.get("t"), value.get("c")
            if kind == "Image":
                return  # Image alternatives are not rendered note hyperlinks.
            if kind == "Link":
                expected.append(content[2][0])
                visit(content[1])
            elif kind == "Cite":
                visit(content[1])  # Citation metadata is not rendered.
            else:
                visit(content)

    visit(source)
    actual = [node.get("href", "") for node in note.iter()
              if _name(node) == "a" and "backlink" not in node.get(
                  "{http://www.idpf.org/2007/ops}type", "").split()]
    if len(expected) != len(actual):
        raise EpubSemanticError("原生标题注的正文链接数量与来源不一致")
    for wanted, got in zip(expected, actual):
        if wanted.startswith("#"):
            fragment = urllib.parse.unquote(wanted[1:])
            if epub_identifiers and fragment and not fragment[0].isalpha():
                fragment = "id_" + fragment
            targets = [(member, fragment) for member, document in documents.items()
                       for node in document.iter() if node.get("id") == fragment]
            if len(targets) != 1 or _resolve_href(path, got) != targets[0]:
                raise EpubSemanticError("原生标题注的正文链接目标与来源不一致")
        elif wanted != got:
            raise EpubSemanticError("原生标题注的正文链接目标与来源不一致")


def validate_native_heading_noterefs(
    source_header: Mapping[str, Any], heading: ET.Element, *, path: str,
    documents: Mapping[str, ET.Element], ordinals: Sequence[tuple[Mapping[str, Any], int]],
    epub_identifiers: bool = False,
) -> None:
    """Bind every generated heading note to the source object and writer identity."""
    def identifier(value: str) -> str:
        return "id_" + value if epub_identifiers and value and not value[0].isalpha() else value

    wanted = _source_heading_note_parts(source_header["c"][2])
    got = _actual_heading_note_parts(heading)
    source_text = _visible("".join(value for kind, value in wanted if kind in {"text", "math"}))
    actual_text = _visible("".join(value for kind, value in got if kind == "text"))
    if not source_text or not actual_text.endswith(source_text):
        raise EpubSemanticError("原生标题注所在标题与来源不一致")
    prefix = actual_text[:-len(source_text)]

    def refs(parts: list[tuple[str, Any]], initial: str = "") -> list[tuple[str, Any, int]]:
        text = initial
        result = []
        for kind, value in parts:
            if kind in {"note", "link", "ref"}:
                result.append((kind, value, len(_visible(text))))
            else:
                text += value
        return result

    expected_refs, actual_refs = refs(wanted, prefix), refs(got)
    if len(expected_refs) != len(actual_refs):
        raise EpubSemanticError("正文标题注引用数量与源标题不一致")
    numbers = {id(node): number for node, number in ordinals}
    for (kind, source, position), (_, reference, actual_position) in zip(expected_refs, actual_refs):
        if position != actual_position:
            raise EpubSemanticError("正文标题注引用位置与源标题不一致")
        if kind == "link":
            attributes, children, target = source["c"]
            ref_id = identifier(attributes[0])
            note_id = identifier(urllib.parse.unquote(urllib.parse.urlsplit(target[0]).fragment))
            label = _visible(inline_text(children, include_noterefs=True))
            actual_target = urllib.parse.unquote(urllib.parse.urlsplit(reference.get("href", "")).fragment)
            if ((ref_id and reference.get("id") != ref_id) or actual_target != note_id
                    or _visible("".join(reference.itertext())) != label):
                raise EpubSemanticError("正文标题注引用的注号／ID／目标与源标题不一致")
            continue
        number = numbers.get(id(source))
        if number is None:
            raise EpubSemanticError("原生标题注缺少 writer 序号")
        note_id = f"fn{number}"
        if (reference.get("id") != f"fnref{number}" or reference.get("href") != f"#{note_id}"
                or _visible("".join(reference.itertext())) != str(number)):
            raise EpubSemanticError("原生标题注的 writer 注号／引用 ID／目标不一致")
        targets = [node for node in documents[path].iter() if node.get("id") == note_id]
        if (len(targets) != 1 or _name(targets[0]) != "aside"
                or "footnote" not in targets[0].get("{http://www.idpf.org/2007/ops}type", "").split()):
            raise EpubSemanticError("原生标题注目标必须是唯一实际 footnote")
        if _native_note_source_records(source) != _native_note_xhtml_records(targets[0]):
            raise EpubSemanticError("原生标题注的完整注文与来源不一致")
        _validate_native_note_links(source, targets[0], path=path, documents=documents,
                                    epub_identifiers=epub_identifiers)


def validate_heading_noterefs(
    source_header: Mapping[str, Any], heading: ET.Element, *, epub_identifiers: bool = False,
) -> None:
    """Check the callouts omitted from TOC labels against the original header."""
    def identifier(value: str) -> str:
        return "id_" + value if epub_identifiers and value and not value[0].isalpha() else value

    expected = []
    for node in ast_nodes(source_header["c"][2]):
        if node["t"] == "Link" and "noteref" in dict(node["c"][0][2]).get("epub:type", "").split():
            attributes, children, target = node["c"]
            expected.append((identifier(attributes[0]),
                             identifier(urllib.parse.unquote(urllib.parse.urlsplit(target[0]).fragment)),
                             _visible(inline_text(children, include_noterefs=True))))
    actual = [node for node in heading.iter() if _name(node) == "a"
              and "noteref" in node.get("{http://www.idpf.org/2007/ops}type", node.get("epub:type", "")).split()]
    if len(actual) != len(expected):
        raise EpubSemanticError("正文标题注引用数量与源标题不一致")
    for (reference_id, target_id, label), node in zip(expected, actual):
        actual_target = urllib.parse.unquote(urllib.parse.urlsplit(node.get("href", "")).fragment)
        if ((reference_id and node.get("id") != reference_id) or actual_target != target_id
                or _visible("".join(node.itertext())) != label):
            raise EpubSemanticError("正文标题注引用的注号／ID／目标与源标题不一致")


def _resolve_href(nav_path: str, href: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(href)
    path = urllib.parse.unquote(parts.path)
    if "\\" in path or path.startswith("/"):
        raise EpubSemanticError(f"EPUB 目录 href 路径非法：{href!r}")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(nav_path), path)) if path else nav_path
    if target == ".." or target.startswith("../"):
        raise EpubSemanticError(f"EPUB 目录 href 越过包根：{href!r}")
    return target, urllib.parse.unquote(parts.fragment)


def validate_ncx_matches_nav(
    ncx: ET.Element, toc: ET.Element, *, ncx_path: str, nav_path: str,
) -> None:
    """Compare the NCX tree to the actual, already source-validated nav."""
    maps = [child for child in ncx if _name(child) == "navMap"]
    if len(maps) != 1:
        raise EpubSemanticError("NCX 必须有唯一 navMap")
    actual: list[tuple[int, str, tuple[str, str]]] = []

    def visit(container: ET.Element, depth: int) -> None:
        for point in (child for child in container if _name(child) == "navPoint"):
            labels = [child for child in point if _name(child) == "navLabel"]
            contents = [child for child in point if _name(child) == "content"]
            texts = [child for child in labels[0] if _name(child) == "text"] if len(labels) == 1 else []
            if len(contents) != 1 or len(texts) != 1:
                raise EpubSemanticError("NCX 目录项必须有唯一标签及目标")
            href = contents[0].get("src", "")
            parts = urllib.parse.urlsplit(href)
            if parts.scheme or parts.netloc or parts.query or not parts.fragment:
                raise EpubSemanticError(f"NCX 目录目标必须指向包内标题片段：{href!r}")
            actual.append((depth, _visible("".join(texts[0].itertext())), _resolve_href(ncx_path, href)))
            visit(point, depth + 1)

    visit(maps[0], 1)
    expected = [(entry.depth, entry.label, _resolve_href(nav_path, entry.href))
                for entry in outline_from_nav(toc)]
    if len(actual) != len(expected):
        raise EpubSemanticError(f"NCX 与 nav 目录项数量不一致：期望 {len(expected)}，实际 {len(actual)}")
    for index, (wanted, got) in enumerate(zip(expected, actual), 1):
        if wanted != got:
            raise EpubSemanticError(f"NCX 与 nav 目录不一致：第 {index} 项；期望 {wanted!r}，实际 {got!r}")


def _heading_starts_container(container: ET.Element, heading: ET.Element) -> bool:
    """Allow only standard empty pagebreak metadata before a source-first heading."""
    if _name(container) not in {"div", "section"} or (container.text or "").strip():
        return False
    for child in container:
        if child is heading:
            return re.fullmatch(r"h[1-6]", _name(heading)) is not None
        if not (
            _name(child) == "span"
            and "pagebreak" in child.get("{http://www.idpf.org/2007/ops}type", "").split()
            and child.get("role") in {None, "doc-pagebreak"}
            and not len(child)
            and not child.text
            and not (child.tail or "").strip()
        ):
            return False
    return False


def validate_outline(
    document: Mapping[str, Any], toc: ET.Element, *, toc_depth: int = 3,
    nav_path: str | None = None, documents: Mapping[str, ET.Element] | None = None,
    spine_paths: Sequence[str] | None = None,
    number_sections: bool = False,
    epub_identifiers: bool = False,
    navigation_math_labels: bool = False,
    native_note_split_level: int | None = None,
    native_note_title_overridden: bool = False,
) -> tuple[OutlineEntry, ...]:
    """Compare order, hierarchy, label, fragment and (when supplied) real target.

    The toc-only call remains useful to existing adapters/tests.  Production QA
    supplies both keyword arguments to bind every href to its real heading.
    ``number_sections`` mirrors the writer option; already-numbered Lua inlines
    continue to use the default False rather than being numbered a second time.
    """
    if (nav_path is None) != (documents is None):
        raise EpubSemanticError("目录落点检查必须同时提供 nav_path 与 documents")
    if spine_paths is not None and documents is None:
        raise EpubSemanticError("目录实际阅读顺序检查需要真实 documents 与 nav_path")
    has_native_notes = has_native_heading_notes(document)
    if has_native_notes and (native_note_split_level is None or documents is None or spine_paths is None):
        raise EpubSemanticError("原生标题注需要实际 writer 拆分层级、documents 与 spine")
    native_notes = (native_heading_notes(document, native_note_split_level, title_overridden=native_note_title_overridden)
                    if has_native_notes else {})
    expected = outline_from_ast(document, toc_depth, number_sections=number_sections,
                                navigation_math_labels=navigation_math_labels,
                                allow_native_notes=has_native_notes)
    # The visible nav shorthand must never relax the independent body check.
    body_expected = (outline_from_ast(document, toc_depth, number_sections=number_sections,
                                      allow_native_notes=has_native_notes)
                     if navigation_math_labels else expected)
    if type(epub_identifiers) is not bool:
        raise EpubSemanticError("epub_identifiers 必须是布尔值")
    if epub_identifiers:
        # Match Pandoc EPUB ensureValidXmlIdentifiers, without rewriting inputs.
        # https://github.com/jgm/pandoc/blob/main/src/Text/Pandoc/Writers/Shared.hs
        def serialized(identifier: str) -> str:
            return identifier if not identifier or identifier[0].isalpha() else "id_" + identifier

        expected = tuple(replace(entry, identifier=serialized(entry.identifier),
                                 parent=serialized(entry.parent) if entry.parent else None)
                         for entry in expected)
        body_expected = tuple(replace(entry, identifier=serialized(entry.identifier),
                                      parent=serialized(entry.parent) if entry.parent else None)
                              for entry in body_expected)
        if len({entry.identifier for entry in expected}) != len(expected):
            raise EpubSemanticError("Pandoc EPUB 标题 ID 序列化后冲突")
        if native_notes:
            all_ids = [serialized(node["c"][1][0]) for node in ast_nodes(document.get("blocks", []))
                       if node["t"] == "Header"]
            if any(all_ids.count(serialized(identifier)) != 1 for identifier in native_notes):
                raise EpubSemanticError("带原生注的标题 ID 在 EPUB 序列化后冲突")
    source_headers = {
        serialized(node["c"][1][0]) if epub_identifiers else node["c"][1][0]: node
        for node in ast_nodes(document.get("blocks", [])) if node["t"] == "Header"
    }
    native_headers = {
        serialized(key) if epub_identifiers else key: value
        for key, value in native_notes.items()
    }

    actual = outline_from_nav(toc)
    if len(expected) != len(actual):
        raise EpubSemanticError(f"目录与正式标题树不一致：期望 {len(expected)} 项，实际 {len(actual)} 项")
    container_aliases: dict[str, str] = {}
    if epub_identifiers:
        for node in ast_nodes(document.get("blocks", [])):
            if node.get("t") != "Div":
                continue
            attributes, blocks = node["c"]
            if attributes[0] and blocks and blocks[0].get("t") == "Header":
                heading_id = blocks[0]["c"][1][0]
                if heading_id:
                    container_aliases[serialized(attributes[0])] = serialized(heading_id)

    target_ids: dict[str, dict[str, ET.Element]] = {}
    target_positions: dict[tuple[str, ET.Element], int] = {}
    expected_ids = {entry.identifier for entry in expected}
    seen_heading_ids: dict[str, str] = {}
    if documents is not None:
        for path, root in documents.items():
            ids: dict[str, ET.Element] = {}
            for position, element in enumerate(root.iter()):
                target_positions[path, element] = position
                identifier = element.attrib.get("id")
                if identifier:
                    if identifier in ids:
                        raise EpubSemanticError(f"目录落点文档含重复 ID：{path}#{identifier}")
                    ids[identifier] = element
                    if identifier in expected_ids:
                        if identifier in seen_heading_ids:
                            raise EpubSemanticError(
                                f"正式标题 ID 在多个成品文档中重复：{identifier}；"
                                f"{seen_heading_ids[identifier]} / {path}"
                            )
                        seen_heading_ids[identifier] = path
            target_ids[path] = ids
    spine_order: dict[str, int] | None = None
    if spine_paths is not None:
        if isinstance(spine_paths, str) or len(set(spine_paths)) != len(spine_paths):
            raise EpubSemanticError("实际 spine 路径必须是按序且唯一的路径列表")
        if any(path not in documents for path in spine_paths):
            raise EpubSemanticError("实际 spine 含未提供的 XHTML 文档")
        spine_order = {path: index for index, path in enumerate(spine_paths)}
    bound_aliases: dict[str, str] = {}
    if documents is not None:
        for wanted, got in zip(expected, actual):
            if container_aliases.get(got.identifier) != wanted.identifier:
                continue
            path, fragment = _resolve_href(nav_path, got.href)
            target = target_ids.get(path, {}).get(fragment)
            heading = target_ids.get(path, {}).get(wanted.identifier)
            if (target is not None and heading is not None
                    and _heading_starts_container(target, heading)):
                bound_aliases[got.identifier] = wanted.identifier

    previous_position: tuple[int, int] | None = None
    for index, (wanted, body_wanted, got) in enumerate(zip(expected, body_expected, actual), 1):
        logical_got = replace(got, identifier=bound_aliases.get(got.identifier, got.identifier),
                              parent=bound_aliases.get(got.parent, got.parent))
        for field in ("identifier", "depth", "parent", "label"):
            if getattr(wanted, field) != getattr(logical_got, field):
                raise EpubSemanticError(
                    f"目录与正式标题树不一致：第 {index} 项 {field}；"
                    f"期望 {getattr(wanted, field)!r}，实际 {getattr(got, field)!r}"
                )
        if documents is None:
            continue
        path, fragment = _resolve_href(nav_path, got.href)
        target = target_ids.get(path, {}).get(fragment)
        if target is None:
            raise EpubSemanticError(f"目录 href 真实落点不存在：{got.href}")
        heading = target if re.fullmatch(r"h[1-6]", _name(target)) else None
        if heading is None and _name(target) == "section":
            heading = next((child for child in target if re.fullmatch(r"h[1-6]", _name(child))), None)
        if heading is None and bound_aliases.get(got.identifier) == wanted.identifier:
            heading = target_ids[path].get(wanted.identifier)
        if heading is None:
            raise EpubSemanticError(f"目录 href 未指向真实标题：{got.href}")
        if wanted.identifier not in native_headers:
            validate_heading_noterefs(source_headers[wanted.identifier], heading, epub_identifiers=epub_identifiers)
        title = _visible(heading_text(heading))
        if title != body_wanted.label or int(_name(heading)[1:]) != body_wanted.level:
            raise EpubSemanticError(
                f"目录真实目标标题不一致：{got.href}；期望 h{body_wanted.level} {body_wanted.label!r}，"
                f"实际 {_name(heading)} {title!r}"
            )
        if navigation_math_labels:
            source_segments = _inline_segments(source_headers[wanted.identifier]["c"][2],
                                               allow_native_notes=has_native_notes)
            if any(kind == "math" for kind, _ in source_segments):
                source_label = _visible("".join(text for _, text in source_segments))
                prefix = body_wanted.label[:-len(source_label)] if source_label else body_wanted.label
                if _math_positions(source_segments, prefix) != _math_positions(_heading_segments(heading)):
                    raise EpubSemanticError(f"正文标题公式对象的字面或位置与来源不一致：{got.href}")
        if spine_order is not None:
            if path not in spine_order:
                raise EpubSemanticError(f"目录真实目标不在实际 spine：{got.href}")
            position = (spine_order[path], target_positions[path, heading])
            if previous_position is not None and position <= previous_position:
                raise EpubSemanticError(f"目录真实目标阅读顺序与正式标题树不一致：第 {index} 项 {got.href}")
            previous_position = position
    for identifier, ordinals in native_headers.items():
        matches = [(path, target_ids[path][identifier]) for path in spine_paths
                   if identifier in target_ids[path]]
        if len(matches) != 1:
            raise EpubSemanticError(f"原生标题注的标题目标必须在 spine 中唯一：{identifier}")
        path, target = matches[0]
        heading = target if re.fullmatch(r"h[1-6]", _name(target)) else None
        if heading is None and _name(target) in {"section", "div"}:
            heading = next((child for child in target if re.fullmatch(r"h[1-6]", _name(child))), None)
            if heading is not None and not _heading_starts_container(target, heading):
                heading = None
        if heading is None:
            raise EpubSemanticError(f"原生标题注未绑定真实标题：{identifier}")
        validate_native_heading_noterefs(source_headers[identifier], heading, path=path,
                                         documents=documents, ordinals=ordinals,
                                         epub_identifiers=epub_identifiers)
    return actual


SEMANTIC_CLASSES = frozenset({
    "code-change", "binding-context", "binding-macro", "roman",
    "source-highlight-green", "source-highlight-blue", "source-highlight-yellow",
    "source-highlight-purple", "source-highlight-red", "source-highlight-pink",
    "source-highlight-orange", "type-subject", "type-context", "type-result", "type-extension",
})
_INLINE_STYLES = {"Emph": "em", "Strong": "strong", "Underline": "u",
                  "Strikeout": "s", "Superscript": "sup", "Subscript": "sub", "SmallCaps": "smallcaps"}
_RICH_CLASSES = frozenset({"code-example", "binding-example"})


@dataclass(frozen=True, order=True)
class CodeSpan:
    start: int
    end: int
    classes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "classes": list(self.classes)}


@dataclass(frozen=True)
class CodeRecord:
    kind: str
    text: str
    spans: tuple[CodeSpan, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "spans": [span.as_dict() for span in self.spans]}


# (character, semantic classes, collapsible HTML whitespace).  NBSP and code
# whitespace stay protected until final comparison; offsets count code points.
Token = tuple[str, frozenset[str], bool]


def _tokens(text: str, classes: frozenset[str], *, code: bool = False) -> list[Token]:
    return [(char, classes, not code and char in " \t\r\n\f") for char in text]


def _record(kind: str, tokens: Sequence[Token]) -> CodeRecord:
    normalized: list[tuple[str, frozenset[str]]] = []
    pending: frozenset[str] | None = None
    for char, classes, collapsible in tokens:
        if collapsible:
            if pending is None:
                pending = classes
            continue
        if char == "\n":
            pending = None
        elif pending is not None:
            if normalized and normalized[-1][0] != "\n":
                normalized.append((" ", pending))
            pending = None
        # NBSP outside Code is layout spacing. Inside a program it can be a
        # string/identifier character and must remain byte-for-character text.
        normalized.append((" " if char == "\u00a0" and "code" not in classes else char, classes))
    text = "".join(char for char, _ in normalized)
    spans: list[CodeSpan] = []
    all_classes = sorted(set().union(*(classes for _, classes in normalized))) if normalized else []
    for style in all_classes:
        start = None
        for index in range(len(normalized) + 1):
            active = index < len(normalized) and style in normalized[index][1]
            if active and start is None:
                start = index
            elif not active and start is not None:
                spans.append(CodeSpan(start, index, (style,)))
                start = None
    return CodeRecord(kind, text, tuple(sorted(spans)))


def _math_tokens(mode: str, formula: str, inherited: frozenset[str], *, source: bool = False) -> list[Token]:
    # Object boundaries and a typed range prevent a formula from becoming plain
    # text, changing mode, moving, merging, or disappearing without detection.
    identity = _visible(gladtex_alt_text(formula) if source else formula)
    if mode not in _FORMULA_CLASSES or not identity:
        raise EpubSemanticError("富代码数学对象必须具有唯一类型及非空替代文本")
    return _tokens("\ufffc" + identity + "\ufffc", inherited | {mode}, code=True)


def _ast_inline_tokens(inlines: Sequence[Mapping[str, Any]], inherited: frozenset[str] = frozenset()) -> list[Token]:
    result: list[Token] = []
    for node in inlines:
        kind, content = node["t"], node.get("c")
        if kind == "Str":
            result.extend(_tokens(content, inherited))
        elif kind in {"Space", "SoftBreak"}:
            result.extend(_tokens(" ", inherited))
        elif kind == "LineBreak":
            result.append(("\n", inherited, False))
        elif kind == "Code":
            result.extend(_tokens(content[1], inherited | {"code"}, code=True))
        elif kind == "Math":
            mode = "math-display" if content[0]["t"] == "DisplayMath" else "math-inline"
            result.extend(_math_tokens(mode, content[1], inherited, source=True))
        elif kind == "Image":
            modes = set(content[0][1]) & _FORMULA_CLASSES
            if len(modes) != 1:
                raise EpubSemanticError("富代码图像必须是具有唯一类型的数学对象")
            result.extend(_math_tokens(next(iter(modes)), inline_text(content[1]), inherited))
        elif kind == "Span":
            attributes, children = content
            classes = set(attributes[1])
            if classes & {"source-page", "source-unit"}:
                continue
            if "line-break" in classes and not children:
                result.append(("\n", inherited, False))
            else:
                result.extend(_ast_inline_tokens(children, inherited | (classes & SEMANTIC_CLASSES)))
        elif kind in _INLINE_STYLES:
            result.extend(_ast_inline_tokens(content, inherited | {_INLINE_STYLES[kind]}))
        elif kind == "Link":
            result.extend(_ast_inline_tokens(content[1], inherited))
        else:
            raise EpubSemanticError(f"富代码含尚未支持的内联语义：{kind}")
    return result


def canonical_code(document: Mapping[str, Any]) -> tuple[CodeRecord, ...]:
    """Canonical complete programs, including exact indentation/highlight ranges.

    Inline Code edge trimming has already happened in the formal reader.  Rich
    line indentation/multi-column gaps must therefore be represented by NBSP
    outside Code; ordinary collapsible spaces do not create fake columns.
    """
    records: list[CodeRecord] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            kind, content = value.get("t"), value.get("c")
            if kind == "CodeBlock":
                records.append(CodeRecord("plain", content[1]))
                return
            if kind == "Div" and set(content[0][1]) & _RICH_CLASSES:
                classes = set(content[0][1]) & _RICH_CLASSES
                if len(classes) != 1:
                    raise EpubSemanticError("富代码容器不能同时声明 code-example 与 binding-example")
                tokens: list[Token] = []
                for block_index, block in enumerate(content[1]):
                    if block["t"] != "LineBlock":
                        raise EpubSemanticError("富代码容器必须由原生 LineBlock 表达完整行")
                    if block_index:
                        tokens.append(("\n", frozenset(), False))
                    for line_index, line in enumerate(block["c"]):
                        if line_index:
                            tokens.append(("\n", frozenset(), False))
                        tokens.extend(_ast_inline_tokens(line))
                records.append(_record(next(iter(classes)), tokens))
                return
            for child in value.values():
                visit(child)

    visit(document.get("blocks", []))
    return tuple(records)


def _xml_rich_tokens(element: ET.Element, inherited: frozenset[str] = frozenset(), code: bool = False) -> list[Token]:
    tag = _name(element)
    classes = set(element.attrib.get("class", "").split())
    if classes & {"source-page", "source-unit"} or "pagebreak" in element.attrib.get("{http://www.idpf.org/2007/ops}type", "").split():
        return []
    if tag == "br":
        return [("\n", inherited, False)]
    styles = inherited | (classes & SEMANTIC_CLASSES)
    if tag in {"em", "strong", "u", "s", "sup", "sub", "code"}:
        styles |= {tag}
    if "smallcaps" in classes:
        styles |= {"smallcaps"}
    code = code or tag == "code"
    if tag == "img":
        modes = classes & _FORMULA_CLASSES
        if not modes:
            return []  # Preserve the existing non-math image/text extraction contract.
        if len(modes) != 1:
            raise EpubSemanticError("成品富代码图像必须是具有唯一类型的数学对象")
        return _math_tokens(next(iter(modes)), element.attrib.get("alt", ""), styles)
    result = _tokens(element.text or "", styles, code=code)
    for child in element:
        result.extend(_xml_rich_tokens(child, styles, code))
        result.extend(_tokens(child.tail or "", styles, code=code))
    return result


def code_from_xhtml(documents: Iterable[ET.Element] | Mapping[str, ET.Element]) -> tuple[CodeRecord, ...]:
    """Extract final XHTML in caller-supplied spine order (not filename order)."""
    records: list[CodeRecord] = []

    def visit(element: ET.Element) -> None:
        classes = set(element.attrib.get("class", "").split())
        if _name(element) == "pre":
            records.append(CodeRecord("plain", "".join(element.itertext())))
            return
        if classes & _RICH_CLASSES:
            kinds = classes & _RICH_CLASSES
            if len(kinds) != 1:
                raise EpubSemanticError("成品富代码容器的类型冲突")
            blocks = [child for child in element if "line-block" in child.attrib.get("class", "").split()]
            if not blocks or len(blocks) != len(element):
                raise EpubSemanticError("成品富代码丢失原生 line-block")
            tokens: list[Token] = []
            for index, block in enumerate(blocks):
                if index:
                    tokens.append(("\n", frozenset(), False))
                tokens.extend(_xml_rich_tokens(block))
            records.append(_record(next(iter(kinds)), tokens))
            return
        for child in element:
            visit(child)

    for root in documents.values() if isinstance(documents, Mapping) else documents:
        visit(root)
    return tuple(records)


def validate_code_records(
    expected: Sequence[CodeRecord], actual: Sequence[CodeRecord | Mapping[str, Any]],
) -> None:
    """Compare full text and semantic ranges, not fragments or inventory counts."""
    if len(expected) != len(actual):
        raise EpubSemanticError(f"代码块库存不一致：期望 {len(expected)}，实际 {len(actual)}")
    for index, (wanted, value) in enumerate(zip(expected, actual), 1):
        got = value if isinstance(value, CodeRecord) else CodeRecord(
            value["kind"], value["text"], tuple(CodeSpan(span["start"], span["end"], tuple(span["classes"])) for span in value.get("spans", []))
        )
        if wanted.kind != got.kind:
            raise EpubSemanticError(f"第 {index} 个代码块语义类型不一致：{wanted.kind} / {got.kind}")
        if wanted.text != got.text:
            offset = next((i for i, pair in enumerate(zip(wanted.text, got.text)) if pair[0] != pair[1]), min(len(wanted.text), len(got.text)))
            line = wanted.text.count("\n", 0, offset) + 1
            raise EpubSemanticError(
                f"第 {index} 个代码块全文/空白不一致（第 {line} 行，字符 {offset}）："
                f"期望 {wanted.text[max(0, offset-20):offset+30]!r}，实际 {got.text[max(0, offset-20):offset+30]!r}"
            )
        if wanted.spans != tuple(sorted(got.spans)):
            raise EpubSemanticError(f"第 {index} 个代码块高亮/强调范围不一致：期望 {wanted.spans!r}，实际 {got.spans!r}")


@dataclass(frozen=True)
class TeachingSpan:
    """Teaching semantics outside full programs, e.g. roman in an italic lead."""
    classes: tuple[str, ...]
    text: str
    context: str
    emphasis: tuple[str, ...]


def _teaching_ast_text(inlines: Sequence[Mapping[str, Any]]) -> str:
    def tokens(nodes: Sequence[Mapping[str, Any]]) -> list[Token]:
        result: list[Token] = []
        for node in nodes:
            kind, content = node["t"], node.get("c")
            if kind == "Code":
                result.extend(_tokens(content[1], frozenset({"code"}), code=True))
            elif kind == "Span":
                attributes, children = content
                if set(attributes[1]) & {"source-page", "source-unit"}:
                    continue
                result.extend(tokens(children))
                original = dict(attributes[2]).get("data-original")
                if "cn-concept" in attributes[1] and original:
                    result.extend(_tokens(f"（{original}）", frozenset()))
            elif kind in _INLINE_STYLES:
                result.extend(tokens(content))
            elif kind in {"Link", "Image", "Cite"}:
                result.extend(tokens(content[1]))
            elif kind == "Quoted":
                quotes = ('“', '”') if content[0]["t"] == "DoubleQuote" else ('‘', '’')
                result.extend(_tokens(quotes[0], frozenset()))
                result.extend(tokens(content[1]))
                result.extend(_tokens(quotes[1], frozenset()))
            elif kind == "LineBreak":
                result.append(("\n", frozenset(), False))
            else:
                result.extend(_tokens(inline_text([node]), frozenset()))
        return result

    return _record("teaching", tokens(inlines)).text


def teaching_spans_from_ast(document: Mapping[str, Any]) -> tuple[TeachingSpan, ...]:
    """Preserve explanatory colors and roman outside the rich-code inventory."""
    result: list[TeachingSpan] = []

    def visit(value: Any, context: str = "", emphasis: tuple[str, ...] = ()) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child, context, emphasis)
        elif isinstance(value, dict):
            kind, content = value.get("t"), value.get("c")
            if kind == "Div" and set(content[0][1]) & _RICH_CLASSES:
                return  # full text and exact ranges are already checked above
            if kind in {"Para", "Plain", "Header"}:
                inlines = content[2] if kind == "Header" else content
                if any(node["t"] == "Span" and set(node["c"][0][1]) & SEMANTIC_CLASSES for node in ast_nodes(inlines)):
                    context = _teaching_ast_text(inlines)
            elif kind == "LineBlock" and any(
                node["t"] == "Span" and set(node["c"][0][1]) & SEMANTIC_CLASSES
                for node in ast_nodes(content)
            ):
                context = "\n".join(_teaching_ast_text(line) for line in content)
            if kind == "Span":
                classes = tuple(sorted(set(content[0][1]) & SEMANTIC_CLASSES))
                if classes:
                    result.append(TeachingSpan(classes, _teaching_ast_text(content[1]), context, tuple(sorted(set(emphasis)))))
            if kind in _INLINE_STYLES:
                emphasis += (_INLINE_STYLES[kind],)
            for child in value.values():
                visit(child, context, emphasis)

    visit(document.get("blocks", []))
    return tuple(result)


def teaching_spans_from_xhtml(documents: Iterable[ET.Element] | Mapping[str, ET.Element]) -> tuple[TeachingSpan, ...]:
    result: list[TeachingSpan] = []

    def text(element: ET.Element) -> str:
        return _record("teaching", _xml_rich_tokens(element)).text

    def visit(element: ET.Element, context: str = "", emphasis: tuple[str, ...] = ()) -> None:
        classes = set(element.attrib.get("class", "").split())
        if classes & _RICH_CLASSES:
            return
        tag = _name(element)
        if tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "li"} or "line-block" in classes:
            context = text(element)
        selected = tuple(sorted(classes & SEMANTIC_CLASSES))
        if selected:
            result.append(TeachingSpan(selected, text(element), context, tuple(sorted(set(emphasis)))))
        if tag in {"em", "strong", "u", "s", "sup", "sub"}:
            emphasis += (tag,)
        if "smallcaps" in classes:
            emphasis += ("smallcaps",)
        for child in element:
            visit(child, context, emphasis)

    for root in documents.values() if isinstance(documents, Mapping) else documents:
        visit(root)
    return tuple(result)


def validate_teaching_spans(expected: Sequence[TeachingSpan], actual: Sequence[TeachingSpan]) -> None:
    if tuple(expected) != tuple(actual):
        raise EpubSemanticError("教学语义 Span 不一致：代码外颜色、roman、文字上下文或所在强调关系丢失")


# Kept beside the canonicalizer so static XHTML and actual DOM use the same
# whitespace/range contract.  The runner embeds this function into its shell;
# it never becomes script content in the delivered EPUB.
CODE_DOM_EXTRACTOR_JS = r"""(document) => {
  const semantic = new Set(SEMANTIC_CLASS_JSON);
  const richSelector = '.code-example, .binding-example';
  function walk(node, inherited = new Set(), inCode = false) {
    if (node.nodeType === 3) return [...node.data].map(c => [c, inherited, !inCode && /[ \t\r\n\f]/.test(c)]);
    if (node.nodeType !== 1) return [];
    const tag = node.localName;
    if (node.classList.contains('source-page') || node.classList.contains('source-unit') ||
        (node.getAttributeNS('http://www.idpf.org/2007/ops', 'type') || '').split(/\s+/).includes('pagebreak')) return [];
    if (tag === 'br') return [['\n', inherited, false]];
    const classes = new Set(inherited);
    for (const c of node.classList) if (semantic.has(c)) classes.add(c);
    if (['em','strong','u','s','sup','sub','code'].includes(tag)) classes.add(tag);
    if (node.classList.contains('smallcaps')) classes.add('smallcaps');
    if (tag === 'img') {
      const modes = ['math-inline','math-display'].filter(c => node.classList.contains(c));
      if (!modes.length) return [];
      const identity = (node.getAttribute('alt') || '').trim().replace(/\s+/g, ' ');
      if (modes.length !== 1 || !identity) throw new Error('rich code math identity missing');
      classes.add(modes[0]);
      return [...('\ufffc' + identity + '\ufffc')].map(c => [c, classes, false]);
    }
    return [...node.childNodes].flatMap(child => walk(child, classes, inCode || tag === 'code'));
  }
  function record(kind, tokens) {
    const normalized = []; let pending = null;
    for (const [c, classes, collapsible] of tokens) {
      if (collapsible) { if (pending === null) pending = classes; continue; }
      if (c === '\n') pending = null;
      else if (pending !== null) {
        if (normalized.length && normalized.at(-1)[0] !== '\n') normalized.push([' ', pending]);
        pending = null;
      }
      normalized.push([c === '\u00a0' && !classes.has('code') ? ' ' : c, classes]);
    }
    const spans = [];
    for (const c of [...new Set(normalized.flatMap(x => [...x[1]]))].sort()) {
      let start = null;
      for (let i = 0; i <= normalized.length; i++) {
        const active = i < normalized.length && normalized[i][1].has(c);
        if (active && start === null) start = i;
        else if (!active && start !== null) { spans.push({start, end: i, classes: [c]}); start = null; }
      }
    }
    spans.sort((a,b) => a.start - b.start || a.end - b.end || a.classes[0].localeCompare(b.classes[0]));
    return {kind, text: normalized.map(x => x[0]).join(''), spans};
  }
  return [...document.querySelectorAll('pre, ' + richSelector)].filter(node =>
    !node.parentElement?.closest('pre, ' + richSelector)
  ).map(node => {
    if (node.localName === 'pre') return {kind: 'plain', text: node.textContent, spans: []};
    const kinds = ['code-example','binding-example'].filter(c => node.classList.contains(c));
    if (kinds.length !== 1) throw new Error('rich code kind conflict');
    const blocks = [...node.children];
    if (!blocks.length || blocks.some(b => !b.classList.contains('line-block'))) throw new Error('rich code missing line-block');
    return record(kinds[0], blocks.flatMap((b,i) => [...(i ? [['\n', new Set(), false]] : []), ...walk(b)]));
  });
}"""

# json.dumps emits only trusted constant strings, never manuscript content.
CODE_DOM_EXTRACTOR_JS = CODE_DOM_EXTRACTOR_JS.replace("SEMANTIC_CLASS_JSON", json.dumps(sorted(SEMANTIC_CLASSES)))
