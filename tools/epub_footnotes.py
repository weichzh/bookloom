"""Standard EPUB footnotes, including notes retained in another XHTML member.

Explicit footnote Divs are upgraded in place. Link identity is the resolved
package member plus decoded fragment, never the fragment alone.
"""
from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from urllib.parse import quote, unquote, urlsplit

EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


class FootnoteError(ValueError):
    pass


def _name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _kind(node: ET.Element, token: str) -> bool:
    return token in node.get(EPUB_TYPE, "").split()


def _index(documents: Mapping[str, ET.Element]) -> dict[tuple[str, str], list[ET.Element]]:
    result: dict[tuple[str, str], list[ET.Element]] = {}
    for member, root in documents.items():
        for node in root.iter():
            if node.get("id"):
                result.setdefault((member, node.attrib["id"]), []).append(node)
    return result


def resolve_target(
    source: str, href: str, identifiers: Mapping[tuple[str, str], list[ET.Element]],
) -> tuple[str, str, ET.Element]:
    """Resolve a strict package-local fragment link against its source member."""
    try:
        parsed = urlsplit(href)
        if re.search(r"%(?![0-9a-fA-F]{2})", href):
            raise ValueError("invalid percent escape")
        path = unquote(parsed.path, errors="strict")
        fragment = unquote(parsed.fragment, errors="strict")
    except (ValueError, UnicodeError) as error:
        raise FootnoteError(f"EPUB 注释链接编码无效：{source} → {href}") from error
    if (parsed.scheme or parsed.netloc or parsed.query or not fragment
            or path.startswith("/") or "\\" in path or "\x00" in path
            or "\x00" in fragment):
        raise FootnoteError(f"EPUB 注释链接必须指向包内片段：{source} → {href}")
    member = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
    if member == ".." or member.startswith("../"):
        raise FootnoteError(f"EPUB 注释链接越出包根：{source} → {href}")
    nodes = identifiers.get((member, fragment), [])
    if len(nodes) != 1:
        raise FootnoteError(f"EPUB 注释目标必须唯一且存在：{source} → {href}")
    return member, fragment, nodes[0]


def _noterefs(documents: Mapping[str, ET.Element]):
    for member, root in documents.items():
        for node in root.iter():
            if _name(node) == "a" and _kind(node, "noteref"):
                yield member, node


def _backlinks(note: ET.Element):
    return (node for node in note.iter() if _name(node) == "a" and _kind(node, "backlink"))


def validate_footnotes(documents: Mapping[str, ET.Element]) -> None:
    """Reject missing/wrong note targets and backlinks to the wrong reference."""
    identifiers = _index(documents)
    for member, reference in _noterefs(documents):
        reference_id = reference.get("id")
        if not reference_id or len(identifiers.get((member, reference_id), [])) != 1:
            raise FootnoteError(f"EPUB 注引用 ID 必须唯一且存在：{member}")
        note_member, _, note = resolve_target(member, reference.get("href", ""), identifiers)
        if _name(note) != "aside" or not _kind(note, "footnote"):
            raise FootnoteError(f"EPUB 注引用未指向 footnote aside：{member} → {reference.get('href', '')}")
        destinations = [resolve_target(note_member, link.get("href", ""), identifiers)
                        for link in _backlinks(note)]
        if not any(path == member and node is reference for path, _, node in destinations):
            raise FootnoteError(f"EPUB 脚注缺少返回正文链接：{member}#{reference_id}")
    for member, root in documents.items():
        for note in root.iter():
            if _name(note) != "aside" or not _kind(note, "footnote"):
                continue
            for backlink in _backlinks(note):
                ref_member, _, reference = resolve_target(member, backlink.get("href", ""), identifiers)
                if _name(reference) != "a" or not _kind(reference, "noteref"):
                    raise FootnoteError(f"EPUB 脚注回链未指向正文注引用：{member}")
                target_member, _, target = resolve_target(ref_member, reference.get("href", ""), identifiers)
                if target_member != member or target is not note:
                    raise FootnoteError(f"EPUB 脚注回链指向了其他注文的正文：{member}")


def normalize_footnotes(documents: Mapping[str, ET.Element]) -> int:
    """Promote explicit containers and add standard backlinks without moving notes.

    Native writer-generated same-document notes retain their IDs and hrefs.
    Existing malformed backlinks are rejected, not silently replaced.
    """
    promoted = 0
    for root in documents.values():
        for node in root.iter():
            if _name(node) == "div" and _kind(node, "footnote"):
                namespace = node.tag[:-3]
                node.tag = namespace + "aside"
                promoted += 1
    identifiers = _index(documents)
    for member, reference in _noterefs(documents):
        note_member, note_id, note = resolve_target(member, reference.get("href", ""), identifiers)
        if _name(note) != "aside" or not _kind(note, "footnote"):
            raise FootnoteError(f"EPUB 注引用未指向 footnote aside：{member}")
        if not reference.get("id"):
            base = note_id + "-ref"
            identifier = base
            suffix = 2
            while (member, identifier) in identifiers:
                identifier = base + "-" + str(suffix)
                suffix += 1
            reference.set("id", identifier)
            identifiers[(member, identifier)] = [reference]
        reference_id = reference.attrib["id"]
        destinations = [resolve_target(note_member, link.get("href", ""), identifiers)
                        for link in _backlinks(note)]
        if any(path == member and node is reference for path, _, node in destinations):
            continue
        namespace = note.tag[:-len("aside")]
        paragraph = ET.SubElement(note, namespace + "p", {"class": "footnote-backlink"})
        href = "#" + quote(reference_id, safe="-._~")
        if member != note_member:
            href = quote(posixpath.relpath(member, posixpath.dirname(note_member)), safe="/-._~") + href
        anchor = ET.SubElement(paragraph, namespace + "a", {
            EPUB_TYPE: "backlink", "href": href, "aria-label": "返回正文",
        })
        anchor.text = "↩"
    validate_footnotes(documents)
    return promoted
