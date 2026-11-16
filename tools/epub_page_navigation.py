"""Verified fixed-source pagination for the shared, final EPUB XHTML seam.

Source markers are provenance, not automatically exact page boundaries.  Opt-in
books supply a complete per-language declaration of verified positions.  This
module never infers a continuation line from a source-page range or text search.
Publisher-native EPUB points use an independent target/label mode and are kept
in place, without inventing physical PDF pages or converting source units.
It consumes the final DOM produced by any Adapter, before empty source markers
are removed, and leaves character data and the sole chapter toc unchanged.
"""

from __future__ import annotations

import csv
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping  # noqa: UP035 - preserve public typing aliases
from urllib.parse import quote, unquote, urlsplit, urlunsplit

XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
EPUB_TYPE = f"{{{EPUB}}}type"


class PageNavigationError(ValueError):
    """An opt-in publication cannot establish truthful source pagination."""


@dataclass(frozen=True)
class PageLocation:
    pdf_page: int
    label: str
    label_kind: str
    position: str
    target: str | None = None
    line: int | None = None
    line_text: str | None = None
    block: int = 1
    after_page: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class NativePageLocation:
    target: str
    label: str
    before_target: str | None = None


@dataclass(frozen=True)
class PageNavigationConfig:
    source_identifier: str
    source_label: str
    language: str
    pages: tuple[PageLocation | NativePageLocation, ...]
    mode: str = "source-pages"


@dataclass(frozen=True)
class PageNavigationResult:
    page_count: int
    nav_member: str
    targets: tuple[tuple[int | str, str, str], ...]
    mode: str = "source-pages"


def _fail(message: str) -> None:
    raise PageNavigationError(message)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a non-empty string")
    return value.strip()


def _local_file(work_path: Path, value: Any, label: str) -> Path:
    relative = Path(_text(value, label))
    root = work_path.resolve()
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file():
        _fail(f"{label} must identify an existing file inside the work directory")
    return path


def load_page_navigation_config(
    work_path: Path, manifest: Mapping[str, Any], language_code: str
) -> PageNavigationConfig | None:
    """Load optional ``[epub.page_navigation]`` and verified position sidecar.

    Absence preserves old callers. ``mode = 'not-applicable'`` plus ``reason``
    records a source without reliable fixed pagination and produces no targets.
    ``mode = 'source-pages'`` requires a PDF, source_label and a language-keyed
    locations table. Labels come only from page-map.tsv's verified printed folio;
    blank folios explicitly use ``PDF N`` rather than inventing printed numbers.
    ``native-pagebreaks`` requires an EPUB and a complete ordered inventory of
    original ``target`` IDs and ``label`` values, without PDF pages or page-map.
    """
    epub = manifest.get("epub", {})
    if not isinstance(epub, Mapping) or "page_navigation" not in epub:
        return None
    raw = epub["page_navigation"]
    if not isinstance(raw, Mapping):
        _fail("epub.page_navigation must be a table")
    mode = raw.get("mode")
    if mode == "not-applicable":
        _text(raw.get("reason"), "epub.page_navigation.reason")
        return None
    if mode not in {"source-pages", "native-pagebreaks"}:
        _fail("epub.page_navigation.mode must be source-pages, native-pagebreaks or not-applicable")
    source = manifest.get("source", {})
    if not isinstance(source, Mapping):
        _fail("page navigation requires source identity")
    source_format = source.get("format") or Path(str(source.get("relative_path", ""))).suffix.lstrip(".")
    if source_format != ("epub" if mode == "native-pagebreaks" else "pdf"):
        _fail(f"{mode} requires a {'EPUB native-pagebreak' if mode == 'native-pagebreaks' else 'PDF fixed-page'} source; source-unit is not pagination")
    digest = source.get("sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        _fail("page navigation requires the declared source.sha256 identity")
    source_label = _text(raw.get("source_label"), "epub.page_navigation.source_label")
    locations = raw.get("locations")
    if not isinstance(locations, Mapping) or language_code not in locations:
        _fail(f"page navigation has no verified locations for {language_code}")
    path = _local_file(work_path, locations[language_code], "page navigation locations")
    try:
        declared = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise PageNavigationError(f"invalid page navigation JSON: {path.name}") from error
    if not isinstance(declared, dict) or declared.get("schema_version") != 1 or not isinstance(declared.get("pages"), list):
        _fail("page navigation JSON requires schema_version=1 and pages array")
    if mode == "native-pagebreaks":
        native_pages: list[NativePageLocation] = []
        for entry in declared["pages"]:
            if not isinstance(entry, dict) or not {"target", "label"}.issubset(entry) or set(entry) - {"target", "label", "before_target"}:
                _fail("native pages require target/label and optional before_target; no PDF page identity")
            target = _text(entry["target"], "native page target")
            if not re.fullmatch(r"[^\s#]+", target):
                _fail("native page target must be a bare stable element ID")
            before_target = None
            if "before_target" in entry:
                before_target = _text(entry["before_target"], "native before_target")
                if not re.fullmatch(r"[^\s#]+", before_target):
                    _fail("native before_target must be a bare stable element ID")
            native_pages.append(NativePageLocation(target, _text(entry["label"], "native page label"), before_target))
        if not native_pages or len({page.target for page in native_pages}) != len(native_pages):
            _fail("native pages require a non-empty, unique stable-ID inventory in source order")
        return PageNavigationConfig(
            f"urn:sha256:{digest.lower()}", source_label, language_code,
            tuple(native_pages), mode="native-pagebreaks",
        )
    labels: dict[int, tuple[str, str]] = {}
    map_path = _local_file(work_path, "page-map.tsv", "page-map.tsv")
    with map_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle, delimiter="\t")
        if not {"pdf_page", "original_printed_page", "disposition"}.issubset(rows.fieldnames or []):
            _fail("page navigation requires page-map.tsv with explicit dispositions")
        all_pages: list[int] = []
        for row in rows:
            try:
                page = int(row["pdf_page"])
            except (TypeError, ValueError) as error:
                raise PageNavigationError("invalid page-map PDF page") from error
            all_pages.append(page)
            if row["disposition"] not in {"retain", "merge", "rebuild", "exclude"}:
                _fail(f"unknown disposition for PDF page {page}")
            if row["disposition"] not in {"retain", "merge"}:
                continue
            printed = (row["original_printed_page"] or "").strip()
            labels[page] = (printed, "printed") if printed else (f"PDF {page}", "physical")
    total = manifest.get("pdf", {}).get("pages")
    if isinstance(total, bool) or not isinstance(total, int) or all_pages != list(range(1, total + 1)):
        _fail("page-map.tsv must cover all declared PDF pages exactly once in order")
    pages: list[PageLocation] = []
    for entry in declared["pages"]:
        if not isinstance(entry, dict):
            _fail("page navigation pages must be objects")
        page = entry.get("pdf_page")
        if isinstance(page, bool) or not isinstance(page, int) or page not in labels:
            _fail(f"page navigation includes an excluded or invalid PDF page: {page}")
        position = entry.get("position")
        allowed = {"pdf_page", "position"}
        target = line = line_text = None
        after_page = reason = None
        block = 1
        if position == "next-code-line":
            block = entry.get("block", 1)
            if isinstance(block, bool) or not isinstance(block, int) or block < 1:
                _fail(f"PDF {page} block must be a positive 1-based following code-block ordinal")
            allowed.add("block")
        if position in {"code-line", "before-target"}:
            target = _text(entry.get("target"), f"PDF {page} target")
            if not re.fullmatch(r"[^\s#]+", target):
                _fail(f"PDF {page} target must be a bare element ID")
            allowed.add("target")
        if position == "before-target" and ("after_page" in entry or "reason" in entry):
            after_page = entry.get("after_page")
            reason = _text(entry.get("reason"), f"PDF {page} deferred reason")
            if isinstance(after_page, bool) or not isinstance(after_page, int):
                _fail(f"PDF {page} after_page must be a retained PDF page number")
            allowed.update({"after_page", "reason"})
        if position in {"code-line", "next-code-line"}:
            line = entry.get("line")
            line_text = entry.get("line_text")
            if isinstance(line, bool) or not isinstance(line, int) or line < 1 or not isinstance(line_text, str) or "\n" in line_text or "\r" in line_text:
                _fail(f"PDF {page} requires a positive 1-based line and complete line_text")
            allowed.update({"line", "line_text"})
        elif position not in {"marker", "before-target"}:
            _fail(f"PDF {page} has no supported verified position")
        if set(entry) - allowed:
            _fail(f"PDF {page} has unknown position fields: {sorted(set(entry) - allowed)}")
        label, kind = labels[page]
        pages.append(PageLocation(page, label, kind, position, target, line, line_text, block, after_page, reason))
    if [page.pdf_page for page in pages] != list(labels):
        _fail("page navigation declarations must exactly match retained/merged pages in source order")
    if not pages:
        _fail("source-pages requires at least one retained source page")
    _source_dom_order(tuple(pages))
    return PageNavigationConfig(f"urn:sha256:{digest.lower()}", source_label, language_code, tuple(pages))


def _source_dom_order(pages: tuple[PageLocation | NativePageLocation, ...]) -> list[int]:
    """Derive the only permitted DOM order from explicit deferred objects.

    The public page list remains in source order. A non-deferred retained page
    anchors each group; group members retain their own source order.
    """
    if not pages or any(not isinstance(page, PageLocation) for page in pages):
        _fail("source-pages requires a non-empty PDF page inventory")
    numbers = [page.pdf_page for page in pages]
    if any(isinstance(number, bool) or not isinstance(number, int) or number < 1 for number in numbers) or numbers != sorted(set(numbers)):
        _fail("source-pages declarations must be unique and in source order")
    by_number = {page.pdf_page: page for page in pages}
    groups: dict[int, list[int]] = {}
    deferred_targets: set[str] = set()
    for page in pages:
        if page.after_page is None and page.reason is None:
            continue
        if page.position != "before-target":
            _fail(f"PDF {page.pdf_page} deferred placement requires before-target")
        _text(page.reason, f"PDF {page.pdf_page} deferred reason")
        anchor = page.after_page
        if isinstance(anchor, bool) or not isinstance(anchor, int) or anchor <= page.pdf_page or anchor not in by_number:
            _fail(f"PDF {page.pdf_page} after_page must name a later retained page")
        if by_number[anchor].after_page is not None or by_number[anchor].reason is not None:
            _fail(f"PDF {page.pdf_page} after_page cannot itself be deferred")
        target = _text(page.target, f"PDF {page.pdf_page} deferred target")
        if target in deferred_targets:
            _fail(f"PDF {page.pdf_page} deferred target is duplicated: {target}")
        deferred_targets.add(target)
        groups.setdefault(anchor, []).append(page.pdf_page)
    order: list[int] = []
    for page in pages:
        if page.after_page is None:
            order.append(page.pdf_page)
            order.extend(groups.get(page.pdf_page, ()))
    return order


def _name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _classes(node: ET.Element) -> set[str]:
    return set(node.get("class", "").split())


def _member(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query:
        _fail(f"pagination requires a local package resource: {href}")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parsed.path)))
    if path.startswith(("../", "/")):
        _fail(f"pagination resource escapes the EPUB package: {href}")
    return path


def _package(payloads: Mapping[str, bytes]) -> tuple[str, ET.Element, str, list[str]]:
    try:
        container = ET.fromstring(payloads["META-INF/container.xml"])
        roots = [node.get("full-path", "") for node in container.iter() if _name(node) == "rootfile" and node.get("media-type") == "application/oebps-package+xml"]
        if len(roots) != 1:
            _fail("pagination requires one declared OPF package")
        opf_member = roots[0]
        package = ET.fromstring(payloads[opf_member])
        items = {node.get("id"): node for node in package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item")}
        nav_items = [node for node in items.values() if "nav" in node.get("properties", "").split()]
        if len(nav_items) != 1:
            _fail("pagination requires exactly one manifest nav item")
        nav_member = _member(opf_member, nav_items[0].get("href", ""))
        spine = [_member(opf_member, items[node.get("idref")].get("href", "")) for node in package.findall(f"{{{OPF}}}spine/{{{OPF}}}itemref")]
        return opf_member, package, nav_member, spine
    except (KeyError, ET.ParseError) as error:
        raise PageNavigationError("pagination cannot resolve the EPUB package/spine") from error


def _code_blocks(root: ET.Element) -> list[ET.Element]:
    parents = {child: node for node in root.iter() for child in node}
    blocks = []
    for node in root.iter():
        if _name(node) == "pre":
            blocks.append(node)
        elif "line-block" in _classes(node):
            parent = parents.get(node)
            while parent is not None and not (_classes(parent) & {"code-example", "binding-example"}):
                parent = parents.get(parent)
            if parent is not None:
                blocks.append(node)
    return blocks


def _code_content(block: ET.Element) -> ET.Element:
    if _name(block) == "pre":
        codes = [node for node in block if _name(node) == "code"]
        if len(codes) == 1:
            return codes[0]
    return block


def code_lines(block: ET.Element) -> tuple[str, ...]:
    """Exact code lines; rich LineBlock writer newlines are not visible breaks."""
    root = _code_content(block)
    if _name(block) == "pre":
        return tuple("".join(root.itertext()).split("\n"))
    parts: list[str] = []

    def visit(node: ET.Element, in_code: bool = False) -> None:
        in_code = in_code or _name(node) == "code"
        if node.text:
            parts.append(node.text if in_code else node.text.replace("\n", "").replace("\r", ""))
        for child in node:
            if _name(child) == "br":
                parts.append("\n")
            else:
                visit(child, in_code)
            if child.tail:
                parts.append(child.tail if in_code else child.tail.replace("\n", "").replace("\r", ""))

    visit(root)
    return tuple("".join(parts).split("\n"))


def _insert_after(parent: ET.Element, child: ET.Element, marker: ET.Element) -> None:
    marker.tail = child.tail
    child.tail = None
    parent.insert(list(parent).index(child) + 1, marker)


def _remove_migrated_marker(root: ET.Element, marker: ET.Element) -> None:
    """Remove only the replaced empty source marker, preserving its text slot."""
    parent = next((node for node in root.iter() if marker in list(node)), None)
    if parent is None or len(marker) or (marker.text or "").strip():
        _fail("migrated source-page is not an attached empty marker")
    index = list(parent).index(marker)
    text = (marker.text or "") + (marker.tail or "")
    if text:
        if index:
            previous = parent[index - 1]
            previous.tail = (previous.tail or "") + text
        else:
            parent.text = (parent.text or "") + text
    parent.remove(marker)


def _retarget_migrated_links(
    documents: Mapping[str, ET.Element], migrations: Mapping[tuple[str, str], str]
) -> None:
    """Keep links to migrated marker identities valid across split XHTML.

    Only a source member/fragment explicitly moved by this call is rewritten.
    The original fragment spelling and query are retained; unrelated links are
    left to the caller's normal link normalization and QA.
    """
    if not migrations:
        return
    for member, root in documents.items():
        for anchor in root.iter(f"{{{XHTML}}}a"):
            parsed = urlsplit(anchor.get("href", ""))
            if not parsed.fragment or parsed.scheme or parsed.netloc:
                continue
            source_member = posixpath.normpath(posixpath.join(posixpath.dirname(member), unquote(parsed.path))) if parsed.path else member
            target_member = migrations.get((source_member, unquote(parsed.fragment)))
            if target_member is None:
                continue
            relative = quote(posixpath.relpath(target_member, posixpath.dirname(member)), safe="/")
            anchor.set("href", urlunsplit(("", "", relative, parsed.query, parsed.fragment)))


def _insert_code_line(block: ET.Element, line: int, marker: ET.Element) -> None:
    content = _code_content(block)
    if _name(block) != "pre":
        if line == 1:
            marker.tail, content.text = content.text, None
            content.insert(0, marker)
            return
        breaks = [(node, child) for node in content.iter() for child in node if _name(child) == "br"]
        _insert_after(*breaks[line - 2], marker)
        return
    offset = sum(len(value) + 1 for value in code_lines(block)[:line - 1])
    # Walk actual XML text slots. Inserting an empty child inside a syntax token
    # preserves its styling and all pre/code characters, including indentation.
    def insert(node: ET.Element, remaining: int) -> tuple[bool, int]:
        value = node.text or ""
        if remaining <= len(value):
            node.text = value[:remaining]
            marker.tail = value[remaining:]
            node.insert(0, marker)
            return True, 0
        remaining -= len(value)
        for child in list(node):
            done, remaining = insert(child, remaining)
            if done:
                return True, 0
            value = child.tail or ""
            if remaining <= len(value):
                child.tail = value[:remaining]
                marker.tail = value[remaining:]
                node.insert(list(node).index(child) + 1, marker)
                return True, 0
            remaining -= len(value)
        return False, remaining

    if not insert(content, offset)[0]:
        _fail("verified code line offset is outside the code block")


def _pagebreak(page: PageLocation, language: str, marker_id: str) -> ET.Element:
    zh = language.lower().startswith("zh")
    accessible = (f"原书第 {page.label} 页" if zh else f"Source page {page.label}") if page.label_kind == "printed" else (f"PDF 第 {page.pdf_page} 页" if zh else f"PDF page {page.pdf_page}")
    return ET.Element(f"{{{XHTML}}}span", {
        "id": marker_id, "class": "pagebreak", EPUB_TYPE: "pagebreak",
        "role": "doc-pagebreak", "aria-label": accessible, "title": page.label,
        "data-page": str(page.pdf_page), "data-page-label-kind": page.label_kind,
    })


def _code_prefix(block: ET.Element, point: ET.Element) -> str:
    """Return logical code characters before a pagebreak, excluding its tail."""
    parts: list[str] = []
    rich = _name(block) != "pre"

    def append(value: str | None, in_code: bool) -> None:
        if value:
            parts.append(value if not rich or in_code else value.replace("\n", "").replace("\r", ""))

    def visit(node: ET.Element, in_code: bool = False) -> bool:
        if node is point:
            return True
        in_code = in_code or _name(node) == "code"
        append(node.text, in_code)
        for child in node:
            if child is point:
                return True
            if _name(child) == "br":
                parts.append("\n")
            elif visit(child, in_code):
                return True
            append(child.tail, in_code)
        return False

    if not visit(_code_content(block)):
        _fail("pagebreak is not inside the declared code content")
    return "".join(parts)


def _before_target_parent(root: ET.Element, target: ET.Element, page: int) -> ET.Element:
    """Keep generic before-target placement outside code at every depth."""
    parents = {child: node for node in root.iter() for child in node}
    if "source-page" in _classes(target):
        _fail(f"PDF {page} migration target must be retained content, not another source-page marker")
    ancestor = target
    while ancestor is not None:
        if _name(ancestor) in {"pre", "code"} or _classes(ancestor) & {"code-example", "binding-example"}:
            _fail(f"PDF {page} before-target cannot target code or its descendants; declare a verified code line")
        ancestor = parents.get(ancestor)
    parent = parents.get(target)
    if parent is None or _name(parent) in {"table", "thead", "tbody", "tfoot", "tr", "ul", "ol", "dl"}:
        _fail(f"PDF {page} before-target has no valid inline container")
    return parent


def _validate_code_binding(
    documents: Mapping[str, ET.Element], member: str, block: ET.Element,
    point: ET.Element, page: PageLocation,
) -> None:
    """Bind a line boundary to the originally selected complete code block.

    Generated IDs and the block's page inventory survive empty-marker removal
    and XML serialization. Moving a point into an identical program therefore
    cannot retain a valid binding just because its text and line still match.
    Explicit code-line declarations additionally stay bound to the authored ID.
    """
    identifier = point.get("data-page-code-target", "")
    bound = [node for node in documents[member].iter() if identifier and node.get("id") == identifier]
    inventory = block.get("data-pagebreak-pages", "")
    if len(bound) != 1 or bound[0] is not block or str(page.pdf_page) not in inventory.split(","):
        _fail(f"PDF {page.pdf_page} pagebreak moved from its bound code container")
    if page.position == "code-line":
        declared = [(name, node) for name, root in documents.items() for node in root.iter() if node.get("id") == page.target]
        if len(declared) != 1 or declared[0][0] != member:
            _fail(f"PDF {page.pdf_page} declared code target is missing or ambiguous: {page.target}")
        target = declared[0][1]
        matches = [candidate for candidate in _code_blocks(documents[member]) if candidate is target or candidate in target.iter()]
        if len(matches) != 1 or matches[0] is not block:
            _fail(f"PDF {page.pdf_page} pagebreak left its declared code target: {page.target}")


def _write_page_list(
    documents: Mapping[str, ET.Element], payloads: dict[str, bytes],
    config: PageNavigationConfig, opf_member: str, package: ET.Element,
    nav_member: str, entries: list[tuple[str, str, str]],
) -> None:
    nav_root = documents[nav_member]
    body = nav_root.find(f"{{{XHTML}}}body")
    if body is None:
        _fail("navigation document has no body")
    nav_label = "原书页码" if config.language.lower().startswith("zh") else "Source pages"
    navigation = ET.SubElement(body, f"{{{XHTML}}}nav", {EPUB_TYPE: "page-list", "role": "doc-pagelist", "aria-label": nav_label})
    ET.SubElement(navigation, f"{{{XHTML}}}h2").text = nav_label
    listing = ET.SubElement(navigation, f"{{{XHTML}}}ol")
    for label, member, identifier in entries:
        item = ET.SubElement(listing, f"{{{XHTML}}}li")
        href = quote(posixpath.relpath(member, posixpath.dirname(nav_member)), safe="/") + "#" + quote(identifier, safe="")
        ET.SubElement(item, f"{{{XHTML}}}a", {"href": href}).text = label
    metadata = package.find(f"{{{OPF}}}metadata")
    if metadata is None:
        _fail("pagination package has no metadata")
    if any(node.get("property") == "source-of" and node.text == "pagination" for node in metadata):
        _fail("pagination source is already declared")
    source_id = "page-navigation-source"
    if any(node.get("id") == source_id for node in package.iter()):
        _fail("pagination source ID collision")
    ET.SubElement(metadata, f"{{{DC}}}source", {"id": source_id}).text = f"{config.source_label}; {config.source_identifier}"
    ET.SubElement(metadata, f"{{{OPF}}}meta", {"refines": f"#{source_id}", "property": "source-of"}).text = "pagination"
    payloads[opf_member] = ET.tostring(package, encoding="utf-8", xml_declaration=True)


def _native_table_boundary(target: ET.Element) -> ET.Element | None:
    """Return the whole table named by a table or table-leading group."""
    if _name(target) == "table":
        return target
    if _name(target) not in {"section", "div"} or (target.text or "").strip():
        return None
    for child in target:
        # A source pagepoint may already precede the table inside the group.
        # Ignore only those standard, empty points and their whitespace tails.
        if (_name(child) == "span" and "pagebreak" in child.get(EPUB_TYPE, "").split()
                and not len(child) and not (child.text or "").strip()
                and not (child.tail or "").strip()):
            continue
        return child if _name(child) == "table" else None
    return None


def _native_heading_target(
    documents: Mapping[str, ET.Element], members: list[str], identifier: str,
) -> tuple[str, ET.Element, ET.Element]:
    """Resolve an explicit heading, Figure, or whole-table boundary."""
    matches = [(member, node) for member in members for node in documents[member].iter()
               if node.get("id") == identifier]
    if len(matches) != 1:
        _fail(f"native before_target is missing or ambiguous: {identifier}")
    member, target = matches[0]
    headings = {f"h{level}" for level in range(1, 7)}
    children = list(target)
    table = _native_table_boundary(target)
    if _name(target) not in headings | {"figure"} and not (
        _name(target) in {"section", "div"} and not (target.text or "").strip()
        and children and _name(children[0]) in headings
    ) and table is None:
        _fail(f"native before_target must identify a heading, its first-heading section, a Figure, a Table, or a table-leading section/div: {identifier}")
    parents = {child: node for node in documents[member].iter() for child in node}
    ancestor = target
    while ancestor is not None:
        if _name(ancestor) in {"pre", "code"} or _classes(ancestor) & {"code-example", "binding-example"}:
            _fail(f"native before_target cannot be inside code: {identifier}")
        if table is not None and ancestor is not target and _name(ancestor) == "table":
            _fail(f"native table before_target cannot be inside another table: {identifier}")
        ancestor = parents.get(ancestor)
    if table is not None and _classes(table) & {"code-example", "binding-example"}:
        _fail(f"native before_target cannot be inside code: {identifier}")
    parent = parents.get(target)
    if parent is None or _name(parent) in {"table", "thead", "tbody", "tfoot", "tr", "ul", "ol", "dl"}:
        _fail(f"native before_target has no valid pagebreak container: {identifier}")
    return member, target, parent


def _native_heading_chains(config: PageNavigationConfig) -> dict[str, tuple[NativePageLocation, ...]]:
    groups: dict[str, list[tuple[int, NativePageLocation]]] = {}
    for index, page in enumerate(config.pages):
        if page.before_target is not None:
            groups.setdefault(page.before_target, []).append((index, page))
    result = {}
    for target, entries in groups.items():
        if entries[-1][0] - entries[0][0] + 1 != len(entries):
            _fail(f"native before_target chain declarations must be consecutive: {target}")
        result[target] = tuple(page for _, page in entries)
    return result


def _apply_native_page_navigation(
    documents: dict[str, ET.Element], payloads: dict[str, bytes], config: PageNavigationConfig
) -> PageNavigationResult:
    """Keep native points or restore explicitly bound chapter starts; never invent pages."""
    opf_member, package, nav_member, spine = _package(payloads)
    if nav_member not in documents or any(member not in documents for member in spine):
        _fail("native pagination requires parsed nav and spine XHTML documents")
    nav_root = documents[nav_member]
    if sum(_name(node) == "nav" and "toc" in node.get(EPUB_TYPE, "").split() for node in nav_root.iter()) != 1:
        _fail("native pagination must preserve exactly one chapter toc")
    if any("page-list" in node.get(EPUB_TYPE, "").split() for node in nav_root.iter()):
        _fail("page-list already exists; refusing to duplicate or silently replace it")
    members = [member for member in spine if member != nav_member]
    points = [(member, node) for member in members for node in documents[member].iter()
              if "pagebreak" in node.get(EPUB_TYPE, "").split()]
    if any("pagebreak" in node.get(EPUB_TYPE, "").split()
           for member, root in documents.items() if member not in members for node in root.iter()):
        _fail("native pagebreak exists outside the content spine")
    if not config.pages or any(not isinstance(page, NativePageLocation) for page in config.pages):
        _fail("native pagination requires verified native target/label declarations")
    _native_heading_chains(config)
    expected = [(page.target, page.label) for page in config.pages]
    actual = [(node.get("id"), node.get("title")) for _, node in points]
    if actual != expected:
        _fail("native pagebreak IDs/labels/order do not exactly match verified declarations")
    for member, node in points:
        identifier = node.get("id")
        if _name(node) != "span" or len(node) or "".join(node.itertext()):
            _fail("native pagebreak must be an authored empty span, not a guessed content target")
        if sum(other.get("id") == identifier for name in members for other in documents[name].iter()) != 1:
            _fail(f"native pagebreak ID is missing or ambiguous: {identifier}")
        if node.get("role") not in {None, "doc-pagebreak"}:
            _fail(f"native pagebreak has a conflicting role: {identifier}")
    plans = []
    for page, (original_member, point) in zip(config.pages, points, strict=True):
        if page.before_target is not None:
            member, target, parent = _native_heading_target(documents, members, page.before_target)
            if _native_table_boundary(target) is not None:
                parents = {child: node for node in documents[original_member].iter() for child in node}
                ancestor = point
                while ancestor is not None:
                    if _name(ancestor) == "table":
                        _fail(f"native table before_target cannot move a pagebreak out of a table: {page.target}")
                    if _name(ancestor) in {"pre", "code"} or _classes(ancestor) & {"code-example", "binding-example"}:
                        _fail(f"native table before_target cannot move a pagebreak out of code: {page.target}")
                    ancestor = parents.get(ancestor)
            plans.append((original_member, point, member, target, parent))
    character_data = {member: "".join(documents[member].itertext()) for member in members}
    migrations: dict[tuple[str, str], str] = {}
    for original_member, point, member, target, parent in plans:
        _remove_migrated_marker(documents[original_member], point)
        point.tail = None
        parent.insert(list(parent).index(target), point)
        if original_member != member:
            migrations[original_member, point.get("id", "")] = member
    _retarget_migrated_links(documents, migrations)
    if any("".join(documents[member].itertext()) != text for member, text in character_data.items()):
        _fail("native chapter-page migration changed source characters")
    points = [(member, node) for member in members for node in documents[member].iter()
              if "pagebreak" in node.get(EPUB_TYPE, "").split()]
    if [(node.get("id"), node.get("title")) for _, node in points] != expected:
        _fail("native chapter-page migration changed verified source order")
    for _, node in points:
        node.set("role", "doc-pagebreak")
        if not node.get("aria-label", "").strip():
            label = node.get("title", "")
            node.set("aria-label", f"原书第 {label} 页" if config.language.lower().startswith("zh") else f"Source page {label}")
    _write_page_list(
        documents, payloads, config, opf_member, package, nav_member,
        [(node.get("title", ""), member, node.get("id", "")) for member, node in points],
    )
    result = validate_page_navigation(documents, payloads, config)
    assert result is not None
    return result


def apply_page_navigation(
    documents: dict[str, ET.Element], payloads: dict[str, bytes], config: PageNavigationConfig | None
) -> PageNavigationResult | None:
    """Mutate final XHTML and OPF payload; call BEFORE empty-span removal.

    The caller serializes ``documents`` as usual. OPF bytes are updated directly
    in ``payloads``. Neither call signatures nor output of non-opt-in books need
    change. ``validate_page_navigation`` is also safe after normalization/QA.
    Relocated markers retain their IDs; links follow the known member move and
    only the replaced empty marker is removed, preserving its source text slot.
    """
    if config is None:
        return None
    if config.mode == "native-pagebreaks":
        return _apply_native_page_navigation(documents, payloads, config)
    if config.mode != "source-pages":
        _fail(f"unsupported page navigation mode: {config.mode}")
    expected_dom = _source_dom_order(config.pages)
    opf_member, package, nav_member, spine = _package(payloads)
    if nav_member not in documents or any(member not in documents for member in spine):
        _fail("pagination requires parsed nav and spine XHTML documents")
    nav_root = documents[nav_member]
    if sum(_name(node) == "nav" and "toc" in node.get(EPUB_TYPE, "").split() for node in nav_root.iter()) != 1:
        _fail("pagination must preserve exactly one chapter toc")
    if any("page-list" in node.get(EPUB_TYPE, "").split() for node in nav_root.iter()) or any("pagebreak" in node.get(EPUB_TYPE, "").split() for root in documents.values() for node in root.iter()):
        _fail("pagination already exists; refusing to duplicate or silently replace it")
    ordered_members = [member for member in spine if member != nav_member]
    all_nodes = [(member, node) for member in ordered_members for node in documents[member].iter()]
    ids: dict[str, tuple[str, ET.Element]] = {}
    markers: list[tuple[int, str, ET.Element]] = []
    for member, node in all_nodes:
        if node.get("id"):
            identifier = node.get("id", "")
            # Pandoc's generated code IDs can repeat in different split files.
            if identifier not in ids:
                ids[identifier] = (member, node)
            else:
                ids[identifier] = ("", node)
        if "source-page" in _classes(node):
            raw = node.get("data-page", "")
            if not raw.isdigit() or "".join(node.itertext()).strip() or len(node):
                _fail("source-page must be an empty numeric inline marker")
            markers.append((int(raw), member, node))
    expected = [page.pdf_page for page in config.pages]
    if [page for page, _, _ in markers] != expected:
        _fail("actual source-page markers do not exactly match verified retained source pages")
    blocks = [(member, block) for member in ordered_members for block in _code_blocks(documents[member])]
    block_before = {block: code_lines(block) for _, block in blocks}
    block_ordinals = {block: index for index, (_, block) in enumerate(blocks, 1)}
    block_bindings: dict[ET.Element, tuple[str, list[int]]] = {}
    node_order = {node: index for index, (_, node) in enumerate(all_nodes)}
    plans: list[tuple[PageLocation, str, ET.Element, ET.Element | None]] = []
    for page, (_, original_member, marker) in zip(config.pages, markers, strict=True):
        member = original_member
        target = None
        if page.position == "next-code-line":
            candidates = [(name, block) for name, block in blocks if node_order[block] > node_order[marker]]
            if len(candidates) < page.block:
                _fail(f"PDF {page.pdf_page} has no following code block {page.block}")
            member, target = candidates[page.block - 1]
            following_marker = next((node_order[other] for number, _, other in markers if number > page.pdf_page), len(all_nodes))
            if node_order[target] >= following_marker:
                _fail(f"PDF {page.pdf_page} next code block passes another source marker; declare a stable target")
        elif page.position in {"code-line", "before-target"}:
            if page.target not in ids or not ids[page.target][0]:
                _fail(f"PDF {page.pdf_page} target is missing or ambiguous: {page.target}")
            member, target = ids[page.target]
            if page.position == "code-line":
                matches = [block for name, block in blocks if name == member and (block is target or block in target.iter())]
                if len(matches) != 1:
                    _fail(f"PDF {page.pdf_page} target must contain exactly one code block")
                target = matches[0]
            else:
                _before_target_parent(documents[member], target, page.pdf_page)
                if page.after_page is not None and target.tag not in {f"{{{XHTML}}}figure", f"{{{XHTML}}}table"}:
                    _fail(f"PDF {page.pdf_page} deferred target must be a native Figure or Table")
        if page.position in {"next-code-line", "code-line"}:
            assert target is not None and page.line is not None
            lines = block_before[target]
            if page.line > len(lines) or lines[page.line - 1] != page.line_text:
                _fail(f"PDF {page.pdf_page} verified line_text does not match complete code line {page.line}")
            if target not in block_bindings:
                identifier = target.get("id") or f"page-navigation-code-{block_ordinals[target]}"
                same_ids = [node for node in documents[member].iter() if node.get("id") == identifier]
                if same_ids and (len(same_ids) != 1 or same_ids[0] is not target):
                    _fail(f"PDF {page.pdf_page} code container ID is not unique: {identifier}")
                if target.get("data-pagebreak-pages") is not None:
                    _fail(f"PDF {page.pdf_page} code container already has page bindings")
                block_bindings[target] = (identifier, [])
            block_bindings[target][1].append(page.pdf_page)
        marker_id = marker.get("id")
        marker_id = marker_id or f"source-page-{page.pdf_page}"
        if marker_id in ids and ids[marker_id] != (original_member, marker):
            _fail(f"pagebreak ID collision: {marker_id}")
        generated = _pagebreak(page, config.language, marker_id)
        if page.position in {"next-code-line", "code-line"}:
            assert target is not None
            generated.set("data-page-code-target", block_bindings[target][0])
        plans.append((page, member, generated, target))
    planned_members = {page.pdf_page: member for page, member, _, _ in plans}
    for page, member, _, _ in plans:
        if page.after_page is not None and planned_members[page.after_page] != member:
            _fail(f"PDF {page.pdf_page} deferred target and anchor must be in the same content XHTML")
    for block, (identifier, source_pages) in block_bindings.items():
        block.set("id", identifier)
        block.set("data-pagebreak-pages", ",".join(str(number) for number in source_pages))
    targets = []
    migrations: dict[tuple[str, str], str] = {}
    for (page, member, generated, target), (_, original_member, marker) in zip(plans, markers, strict=True):
        if page.position == "marker":
            marker.attrib.clear()
            marker.attrib.update(generated.attrib)
        elif page.position in {"next-code-line", "code-line"}:
            assert target is not None and page.line is not None
            _insert_code_line(target, page.line, generated)
            _remove_migrated_marker(documents[original_member], marker)
        else:
            assert target is not None
            parent = _before_target_parent(documents[member], target, page.pdf_page)
            parent.insert(list(parent).index(target), generated)
            _remove_migrated_marker(documents[original_member], marker)
        targets.append((page.pdf_page, member, generated.get("id", "")))
        if original_member != member:
            migrations[original_member, generated.get("id", "")] = member
    _retarget_migrated_links(documents, migrations)
    if any(code_lines(block) != lines for block, lines in block_before.items()):
        _fail("pagebreak insertion changed code characters or indentation")
    actual = [int(node.get("data-page", "0")) for member in ordered_members for node in documents[member].iter() if "pagebreak" in node.get(EPUB_TYPE, "").split()]
    if actual != expected_dom:
        _fail("verified page positions do not match the declared DOM reading order")
    _write_page_list(
        documents, payloads, config, opf_member, package, nav_member,
        [(page.label, member, identifier) for page, (_, member, identifier) in zip(config.pages, targets, strict=True)],
    )
    result = validate_page_navigation(documents, payloads, config)
    assert result is not None
    return result


def validate_page_navigation(
    documents: Mapping[str, ET.Element], payloads: Mapping[str, bytes], config: PageNavigationConfig | None = None
) -> PageNavigationResult | None:
    """Verify standard targets, ordered links, labels and OPF source identity.

    Called without config, absent pagination is accepted (legacy contract).
    With config, exact retained-page coverage and labels are additionally checked.
    """
    opf_member, package, nav_member, spine = _package(payloads)
    del opf_member
    if nav_member not in documents:
        _fail("declared navigation document is missing")
    lists = [node for node in documents[nav_member].iter() if _name(node) == "nav" and "page-list" in node.get(EPUB_TYPE, "").split()]
    points = [(member, node) for member in spine if member != nav_member and member in documents for node in documents[member].iter() if "pagebreak" in node.get(EPUB_TYPE, "").split()]
    if not lists and not points and config is None:
        return None
    if len(lists) != 1 or not points:
        _fail("pagination requires exactly one page-list and non-empty pagebreak targets")
    if lists[0].get("role") != "doc-pagelist" or not lists[0].get("aria-label", "").strip():
        _fail("page-list requires an accessible doc-pagelist label")
    toc_count = sum(_name(node) == "nav" and "toc" in node.get(EPUB_TYPE, "").split() for node in documents[nav_member].iter())
    if toc_count != 1:
        _fail("pagination must coexist with exactly one chapter toc")
    if config is not None and config.mode not in {"source-pages", "native-pagebreaks"}:
        _fail(f"unsupported page navigation mode: {config.mode}")
    native = config.mode == "native-pagebreaks" if config is not None else all(
        node.get("data-page") is None for _, node in points
    )
    if native:
        content_members = set(spine) - {nav_member}
        if any("pagebreak" in node.get(EPUB_TYPE, "").split()
               for member, root in documents.items() if member not in content_members
               for node in root.iter()):
            _fail("native pagebreak exists outside the content spine")
    parent_maps = {
        member: {child: parent for parent in documents[member].iter() for child in parent}
        for member in spine if member != nav_member and member in documents
    } if native else {}
    targets = []
    expected_links = []
    for member, node in points:
        identifier, label = node.get("id", ""), node.get("title", "")
        if _name(node) != "span" or not identifier or node.get("role") != "doc-pagebreak" or not node.get("aria-label", "").strip() or not label.strip() or "".join(node.itertext()) or len(node):
            _fail("pagebreak must be an empty span with ID, role and non-empty accessible label")
        if sum(other.get("id") == identifier for other in documents[member].iter()) != 1:
            _fail(f"duplicate pagebreak target ID: {identifier}")
        if native:
            ancestor = node
            while ancestor is not None:
                if ("hidden" in ancestor.attrib
                        or ancestor.get("aria-hidden", "").strip().lower() == "true"):
                    _fail(f"native pagebreak has a hidden ancestor: {identifier}")
                ancestor = parent_maps[member].get(ancestor)
            if sum(other.get("id") == identifier for name in spine if name != nav_member and name in documents for other in documents[name].iter()) != 1:
                _fail(f"native pagebreak ID is missing or ambiguous: {identifier}")
            targets.append((label, member, identifier))
        else:
            raw = node.get("data-page", "")
            if not raw.isdigit():
                _fail("pagebreak lost physical source page identity")
            targets.append((int(raw), member, identifier))
        expected_links.append((member, identifier, label))
    ordered_points = points
    page_numbers = [number for number, _, _ in targets]
    if not native and config is not None:
        expected_dom = _source_dom_order(config.pages)
        if page_numbers != expected_dom:
            _fail("pagebreak DOM order differs from the explicitly declared source-page placements")
        by_number = {number: (member, node) for (number, _, _), (member, node) in zip(targets, points, strict=True)}
        ordered_points = [by_number[page.pdf_page] for page in config.pages]
        targets = [(page.pdf_page, member, node.get("id", "")) for page, (member, node) in zip(config.pages, ordered_points, strict=True)]
        expected_links = [(member, node.get("id", ""), node.get("title", "")) for member, node in ordered_points]
        page_numbers = [number for number, _, _ in targets]
    elif not native and page_numbers != sorted(set(page_numbers)):
        _fail("pagebreak source pages must be strictly increasing and unique")
    actual_links = []
    for anchor in lists[0].iter(f"{{{XHTML}}}a"):
        href = anchor.get("href", "")
        parsed = urlsplit(href)
        actual_links.append((_member(nav_member, href), unquote(parsed.fragment), "".join(anchor.itertext())))
    if actual_links != expected_links:
        _fail("page-list links/labels do not exactly match pagebreak targets in source order")
    metadata = package.find(f"{{{OPF}}}metadata")
    if metadata is None:
        _fail("pagination source metadata is missing")
    source_refs = [node.get("refines", "") for node in metadata if node.get("property") == "source-of" and node.text == "pagination"]
    if len(source_refs) != 1 or not source_refs[0].startswith("#"):
        _fail("pagination requires one OPF source-of=pagination refinement")
    sources = [node for node in metadata if node.tag == f"{{{DC}}}source" and node.get("id") == source_refs[0][1:] and (node.text or "").strip()]
    if len(sources) != 1:
        _fail("pagination source refinement does not resolve to dc:source identity")
    if config is not None:
        if native:
            if any(not isinstance(page, NativePageLocation) for page in config.pages):
                _fail("native pagination requires native target/label declarations")
            if [(point.get("id"), point.get("title")) for _, point in points] != [(page.target, page.label) for page in config.pages]:
                _fail("native pagination differs from verified target/label declarations")
            if sources[0].text != f"{config.source_label}; {config.source_identifier}":
                _fail("pagination metadata does not match native-source identity")
            members = [member for member in spine if member != nav_member and member in documents]
            point_index = {point.get("id"): (member, point) for member, point in points}
            for before_target, chain in _native_heading_chains(config).items():
                target_member, target, parent = _native_heading_target(documents, members, before_target)
                siblings = list(parent)
                index = siblings.index(target)
                nodes = [point_index[page.target] for page in chain]
                if (index < len(nodes)
                        or any(member != target_member or (point.tail or "").strip() for member, point in nodes)
                        or siblings[index - len(nodes):index] != [point for _, point in nodes]):
                    _fail(f"native pagebreak moved from verified before_target: {chain[0].target}")
        else:
            if page_numbers != [page.pdf_page for page in config.pages] or [label for _, _, label in expected_links] != [page.label for page in config.pages]:
                _fail("pagination differs from verified retained-page declarations")
            if sources[0].text != f"{config.source_label}; {config.source_identifier}":
                _fail("pagination metadata does not match fixed-source identity")
            content_documents = {member: documents[member] for member in spine if member != nav_member and member in documents}
            for page, (member, point) in zip(config.pages, ordered_points, strict=True):
                if point.get("data-page-label-kind") != page.label_kind:
                    _fail(f"PDF {page.pdf_page} lost its printed/physical label distinction")
                if page.position in {"next-code-line", "code-line"}:
                    blocks = [block for block in _code_blocks(documents[member]) if point in block.iter()]
                    if len(blocks) != 1:
                        _fail(f"PDF {page.pdf_page} pagebreak is not inside one code block")
                    _validate_code_binding(content_documents, member, blocks[0], point, page)
                    prefix = _code_prefix(blocks[0], point)
                    lines = code_lines(blocks[0])
                    if prefix.count("\n") + 1 != page.line or prefix.rsplit("\n", 1)[-1] or page.line is None or lines[page.line - 1] != page.line_text:
                        _fail(f"PDF {page.pdf_page} pagebreak moved from its verified code line boundary")
                elif page.position == "before-target":
                    parent = next((node for node in documents[member].iter() if point in list(node)), None)
                    siblings = list(parent) if parent is not None else []
                    index = siblings.index(point) if point in siblings else -1
                    if index < 0 or index + 1 >= len(siblings) or siblings[index + 1].get("id") != page.target:
                        _fail(f"PDF {page.pdf_page} pagebreak moved from its verified before-target boundary")
                    target = siblings[index + 1]
                    _before_target_parent(documents[member], target, page.pdf_page)
                    if page.after_page is not None:
                        matches = [
                            (name, node) for name, root in content_documents.items()
                            for node in root.iter() if node.get("id") == page.target
                        ]
                        if matches != [(member, target)]:
                            _fail(f"PDF {page.pdf_page} deferred target is missing or ambiguous: {page.target}")
                        if target.tag not in {f"{{{XHTML}}}figure", f"{{{XHTML}}}table"}:
                            _fail(f"PDF {page.pdf_page} deferred target must be a native Figure or Table")
                        if (point.tail or "").strip():
                            _fail(f"PDF {page.pdf_page} deferred pagebreak is not adjacent to its object")
                        anchor_member, _ = by_number[page.after_page]
                        if anchor_member != member:
                            _fail(f"PDF {page.pdf_page} deferred target and anchor must be in the same content XHTML")
    return PageNavigationResult(len(targets), nav_member, tuple(targets), mode="native-pagebreaks" if native else "source-pages")
