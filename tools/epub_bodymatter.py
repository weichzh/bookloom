"""Optional explicit bodymatter metadata at the shared final EPUB seam."""
from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


class BodymatterError(ValueError):
    pass


def _name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def declared_target(documents: Mapping[str, ET.Element], identifier: str) -> tuple[str, ET.Element]:
    if not isinstance(identifier, str) or not identifier.strip() or any(c.isspace() for c in identifier):
        raise BodymatterError("epub.bodymatter 必须是非空、无空白的稳定目标 ID")
    targets = [(member, node) for member, root in documents.items()
               for node in root.iter() if node.get("id") == identifier]
    if len(targets) != 1:
        raise BodymatterError(f"epub.bodymatter 目标必须唯一且存在：{identifier}")
    member, node = targets[0]
    if _name(node) not in {"section", "div"}:
        raise BodymatterError("epub.bodymatter 必须指向正文 section／div，而不是页标或链接")
    explicit = [(path, item) for path, root in documents.items() for item in root.iter()
                if _name(item) in {"section", "div"} and "bodymatter" in item.get(EPUB_TYPE, "").split()]
    if any(path != member or item is not node for path, item in explicit):
        raise BodymatterError("epub.bodymatter 与底稿既有正文起点冲突")
    return member, node


def apply_declared_bodymatter(documents: Mapping[str, ET.Element], identifier: str | None) -> None:
    if identifier is None:
        return
    _, node = declared_target(documents, identifier)
    kinds = node.get(EPUB_TYPE, "").split()
    if "bodymatter" not in kinds:
        node.set(EPUB_TYPE, " ".join([*kinds, "bodymatter"]))


def validate_declared_bodymatter(
    documents: Mapping[str, ET.Element], identifier: str | None, nav_path: str,
) -> None:
    if identifier is None:
        return
    member, target = declared_target(documents, identifier)
    if "bodymatter" not in target.get(EPUB_TYPE, "").split():
        raise BodymatterError("epub.bodymatter 声明的正文语义未进入成品")
    if nav_path not in documents:
        raise BodymatterError("正文起点检查缺少实际导航文档")
    links = [node for navigation in documents[nav_path].iter()
             if _name(navigation) == "nav" and "landmarks" in navigation.get(EPUB_TYPE, "").split()
             for node in navigation.iter()
             if _name(node) == "a" and "bodymatter" in node.get(EPUB_TYPE, "").split()]
    if len(links) != 1:
        raise BodymatterError("成品必须只有一条正文 landmark")
    parsed = urlsplit(links[0].get("href", ""))
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(nav_path), unquote(parsed.path))) if parsed.path else nav_path
    if parsed.scheme or parsed.netloc or parsed.query or resolved != member or unquote(parsed.fragment) != identifier:
        raise BodymatterError("正文 landmark 未指向 manifest 声明的真实正文起点")
