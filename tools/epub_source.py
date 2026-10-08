"""Safe EPUB source inspection and native-semantics Markdown drafts.

This module is deliberately separate from the manifest-driven CLI.  It is an
intake helper: it reads an EPUB package, reports its OPF spine, and converts
source XHTML to Markdown that can be parsed with the project's formal reader
flags.  The returned Markdown is a draft plus a source-location map; it is not
a verified translation manuscript.

Only the Python standard library is required.  Unsupported or ambiguous
source structures raise :class:`EpubSourceError` (or are explicitly recorded
as warnings when the structure is representable but cannot be fully checked).
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Self

PANDOC_READER = "markdown+fenced_divs+bracketed_spans+footnotes-raw_html"
SOURCE_UNITS_HEADER = (
    "unit_id",
    "spine_order",
    "xhtml_path",
    "linear",
    "kind",
    "title",
    "note",
)

_XHTML = "application/xhtml+xml"
_NCX = "application/x-dtbncx+xml"
_XML_LIMIT = 64 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
_NOTE_ID_RE = re.compile(r"(?:^|_)en\d+$", re.IGNORECASE)
_SKIP_HEAD_TAGS = {"head", "title", "meta", "link", "style", "script"}
_NOTE_CLASS_TOKENS = {"footnotes", "endnotes", "notes"}
_NOTE_CLASS_RE = re.compile(r"(?:^|[-_])footnote(?:[-_]|$)|(?:^|[-_])endnote(?:[-_]|$)")


class EpubSourceError(ValueError):
    """Raised when an EPUB cannot be safely or losslessly represented."""


@dataclass(frozen=True)
class ManifestItem:
    item_id: str
    href: str
    media_type: str
    properties: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpineUnit:
    """One OPF spine item in source order."""

    unit: int
    spine_order: int
    item_id: str
    href: str
    linear: str
    media_type: str
    properties: tuple[str, ...] = ()

    @property
    def unit_id(self) -> int:
        return self.unit

    @property
    def xhtml_path(self) -> str:
        return self.href

    def as_row(self) -> dict[str, str]:
        """Return the repository's ``source-units.tsv`` row shape."""

        kind = "content" if self.media_type == _XHTML else self.media_type
        return {
            "unit_id": str(self.unit),
            "spine_order": str(self.spine_order),
            "xhtml_path": self.href,
            "linear": self.linear,
            "kind": kind,
            "title": "",
            "note": "",
        }


@dataclass(frozen=True)
class EpubPackage:
    path: Path
    opf_path: str
    version: str
    metadata: Mapping[str, tuple[str, ...]]
    manifest: tuple[ManifestItem, ...]
    spine: tuple[SpineUnit, ...]
    nav_href: str | None
    ncx_href: str | None
    members: tuple[str, ...]
    source_sha256: str
    member_sha256: Mapping[str, str]

    @property
    def manifest_by_id(self) -> dict[str, ManifestItem]:
        return {item.item_id: item for item in self.manifest}

    @property
    def manifest_by_href(self) -> dict[str, ManifestItem]:
        return {item.href: item for item in self.manifest}

    def source_unit_rows(self) -> tuple[dict[str, str], ...]:
        return tuple(item.as_row() for item in self.spine)


@dataclass(frozen=True)
class SourceLocation:
    unit: int | None
    href: str
    source_id: str
    kind: str
    markdown_line: int

    def as_dict(self) -> dict[str, object]:
        return {
            "unit": self.unit,
            "href": self.href,
            "source_id": self.source_id,
            "kind": self.kind,
            "markdown_line": self.markdown_line,
        }


@dataclass(frozen=True)
class ConversionResult:
    unit: int | None
    href: str
    markdown: str
    locations: tuple[SourceLocation, ...]
    warnings: tuple[str, ...] = ()

    def source_map(self) -> list[dict[str, object]]:
        return [location.as_dict() for location in self.locations]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _tokens(value: str | None) -> set[str]:
    return set((value or "").split())


def _element_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if _local(child.tag) == name]


def _safe_package_member(name: str, *, label: str = "EPUB 成员") -> str:
    """Canonicalise a ZIP member and reject traversal or ambiguous spelling."""

    if not name or "\x00" in name or "\\" in name:
        raise EpubSourceError(f"{label}路径无效：{name!r}")
    decoded = urllib.parse.unquote(name)
    if decoded.startswith("/"):
        raise EpubSourceError(f"{label}不得是绝对路径：{name}")
    normalized = posixpath.normpath(decoded)
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../"):
        raise EpubSourceError(f"{label}越出 EPUB 根目录：{name}")
    if "//" in decoded:
        raise EpubSourceError(f"{label}包含空路径段：{name}")
    return normalized


def _member_from_href(opf_path: str, href: str, *, label: str = "EPUB href") -> str:
    parsed = urllib.parse.urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise EpubSourceError(f"{label}必须是包内路径：{href}")
    path = urllib.parse.unquote(parsed.path)
    if not path:
        raise EpubSourceError(f"{label}不能为空：{href}")
    base = posixpath.dirname(opf_path)
    return _safe_package_member(posixpath.join(base, path), label=label)


def _parse_xml(payload: bytes, label: str) -> ET.Element:
    if len(payload) > _XML_LIMIT:
        raise EpubSourceError(f"{label}超过安全 XML 大小限制")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as error:
        raise EpubSourceError(f"{label} XML 无法解析：{error}") from error


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise EpubSourceError(f"无法计算 EPUB SHA-256：{path}: {error}") from error
    return digest.hexdigest()


class _PackageReader:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise EpubSourceError(f"EPUB 不存在：{self.path}")
        if not zipfile.is_zipfile(self.path):
            raise EpubSourceError(f"EPUB 不是有效 ZIP：{self.path}")
        try:
            self.archive = zipfile.ZipFile(self.path)
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            raise EpubSourceError(f"无法打开 EPUB：{self.path}: {error}") from error
        self._infos: dict[str, zipfile.ZipInfo] = {}
        try:
            for info in self.archive.infolist():
                canonical = _safe_package_member(info.filename)
                if canonical in self._infos:
                    raise EpubSourceError(
                        f"EPUB 含重复成员或规范化后重复成员：{canonical}"
                    )
                self._infos[canonical] = info
        except Exception:
            self.archive.close()
            raise

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def read(self, member: str) -> bytes:
        canonical = _safe_package_member(member)
        info = self._infos.get(canonical)
        if info is None:
            raise EpubSourceError(f"EPUB 成员不存在：{canonical}")
        if info.file_size > _XML_LIMIT:
            raise EpubSourceError(f"EPUB 成员超过安全读取大小限制：{canonical}")
        try:
            return self.archive.read(info)
        except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
            raise EpubSourceError(
                f"无法读取 EPUB 成员：{canonical}: {error}"
            ) from error

    @property
    def members(self) -> tuple[str, ...]:
        return tuple(self._infos)


def _metadata(root: ET.Element) -> dict[str, tuple[str, ...]]:
    metadata_element = next(
        (child for child in list(root) if _local(child.tag) == "metadata"), None
    )
    if metadata_element is None:
        return {}
    values: dict[str, list[str]] = {}
    for element in metadata_element.iter():
        key = _local(element.tag)
        if key == "metadata":
            continue
        value = " ".join("".join(element.itertext()).split())
        if value:
            values.setdefault(key, []).append(value)
        elif key == "meta" and element.attrib.get("property"):
            values.setdefault(element.attrib["property"], []).append(
                element.attrib.get("content", "")
            )
    return {key: tuple(item for item in items if item) for key, items in values.items()}


def read_epub(path: str | Path) -> EpubPackage:
    """Read container, OPF, manifest and spine without extracting the EPUB."""

    with _PackageReader(Path(path)) as reader:
        if "mimetype" not in reader._infos:
            raise EpubSourceError("EPUB 缺少 mimetype")
        mimetype_info = reader._infos["mimetype"]
        if mimetype_info.compress_type != zipfile.ZIP_STORED:
            raise EpubSourceError("EPUB mimetype 必须未压缩存储")
        if reader.read("mimetype") != b"application/epub+zip":
            raise EpubSourceError("EPUB mimetype 内容无效")
        if "META-INF/container.xml" not in reader._infos:
            raise EpubSourceError("EPUB 缺少 META-INF/container.xml")
        container = _parse_xml(reader.read("META-INF/container.xml"), "container.xml")
        rootfiles = [
            element for element in container.iter() if _local(element.tag) == "rootfile"
        ]
        if len(rootfiles) != 1:
            raise EpubSourceError(
                f"EPUB 必须有唯一 rootfile，实际为 {len(rootfiles)} 个"
            )
        opf_raw = rootfiles[0].attrib.get("full-path", "")
        opf_path = _safe_package_member(opf_raw, label="OPF 路径")
        opf = _parse_xml(reader.read(opf_path), opf_path)
        if _local(opf.tag) != "package":
            raise EpubSourceError(f"OPF 根元素不是 package：{opf_path}")
        version = opf.attrib.get("version", "")
        if version != "2.0" and not version.startswith("3."):
            raise EpubSourceError(f"不支持的 EPUB 版本：{version or '缺失'}")

        manifest_element = next(
            (child for child in list(opf) if _local(child.tag) == "manifest"), None
        )
        spine_element = next(
            (child for child in list(opf) if _local(child.tag) == "spine"), None
        )
        if manifest_element is None or spine_element is None:
            raise EpubSourceError("OPF 缺少 manifest 或 spine")

        manifest: list[ManifestItem] = []
        by_id: dict[str, ManifestItem] = {}
        for item in _element_children(manifest_element, "item"):
            item_id = item.attrib.get("id", "")
            href_raw = item.attrib.get("href", "")
            media_type = item.attrib.get("media-type", "")
            if not item_id or not href_raw or not media_type:
                raise EpubSourceError("OPF manifest item 缺少 id、href 或 media-type")
            if item_id in by_id:
                raise EpubSourceError(f"OPF manifest id 重复：{item_id}")
            href = _member_from_href(opf_path, href_raw)
            if href not in reader._infos:
                raise EpubSourceError(f"manifest href 不存在：{href}")
            parsed = ManifestItem(
                item_id=item_id,
                href=href,
                media_type=media_type,
                properties=tuple(sorted(_tokens(item.attrib.get("properties")))),
            )
            manifest.append(parsed)
            by_id[item_id] = parsed

        spine: list[SpineUnit] = []
        seen_idrefs: set[str] = set()
        for order, itemref in enumerate(
            _element_children(spine_element, "itemref"), start=1
        ):
            item_id = itemref.attrib.get("idref", "")
            linear = itemref.attrib.get("linear", "yes")
            if not item_id or item_id not in by_id:
                raise EpubSourceError(f"spine 第 {order} 项 idref 无效：{item_id}")
            if item_id in seen_idrefs:
                raise EpubSourceError(f"spine idref 重复：{item_id}")
            if linear not in {"yes", "no"}:
                raise EpubSourceError(f"spine 第 {order} 项 linear 无效：{linear}")
            seen_idrefs.add(item_id)
            item = by_id[item_id]
            spine.append(
                SpineUnit(
                    unit=order,
                    spine_order=order,
                    item_id=item.item_id,
                    href=item.href,
                    linear=linear,
                    media_type=item.media_type,
                    properties=item.properties,
                )
            )

        if not spine:
            raise EpubSourceError("OPF spine 不能为空")
        nav = next(
            (
                item.href
                for item in manifest
                if "nav" in item.properties and item.media_type in {_XHTML, "text/html"}
            ),
            None,
        )
        toc_id = spine_element.attrib.get("toc")
        ncx = (
            by_id[toc_id].href
            if toc_id in by_id and by_id[toc_id].media_type == _NCX
            else None
        )
        member_sha256 = {
            item.href: _sha256(reader.read(item.href))
            for item in spine
            if item.media_type == _XHTML
        }
        return EpubPackage(
            path=Path(path).resolve(),
            opf_path=opf_path,
            version=version,
            metadata=_metadata(opf),
            manifest=tuple(manifest),
            spine=tuple(spine),
            nav_href=nav,
            ncx_href=ncx,
            members=reader.members,
            source_sha256=_file_sha256(Path(path).resolve()),
            member_sha256=member_sha256,
        )


def read_source_units(path: str | Path) -> tuple[dict[str, str], ...]:
    """Return spine rows compatible with ``translator.read_source_units``."""

    return read_epub(path).source_unit_rows()


def _attrs(element: ET.Element, *, include_id: bool = True) -> str:
    values: list[str] = []
    element_id = element.attrib.get("id") if include_id else None
    if element_id:
        if _ID_RE.fullmatch(element_id):
            values.append(f"#{element_id}")
        else:
            values.append(f'id="{element_id.replace(chr(34), "&quot;")}"')
    for class_name in sorted(_tokens(element.attrib.get("class"))):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", class_name):
            values.append(f".{class_name}")
        else:
            values.append(f'class="{class_name}"')
    for key, value in sorted(element.attrib.items()):
        if key.startswith("data-") or key in {"title", "lang"}:
            escaped = value.replace('"', "&quot;")
            values.append(f'{key}="{escaped}"')
    return " ".join(values)


def _escape_text(text: str) -> str:
    """Escape Markdown punctuation without changing ordinary prose.

    Source XHTML paragraphs are already semantically paragraphs.  Escaping
    block grammar punctuation here prevents a literal paragraph beginning with
    ``#``, ``>``, ``1.``, or ``-`` (including text after ``<br>``) from being
    reinterpreted as a heading, quote, or list by Pandoc.  The escaped
    punctuation renders as the original characters; native headings, lists,
    and quotes are emitted by the block renderer itself.
    """

    text = text.replace("\\", "\\\\")
    # These characters can open inline Markdown constructs at any position;
    # escaping them is stable even when the text is later placed in a link,
    # note, or emphasis span.
    text = re.sub(r"([`*_[\]{}|~<])", r"\\\1", text)
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        ending = ""
        content = line
        if line.endswith("\r\n"):
            content, ending = line[:-2], "\r\n"
        elif line.endswith(("\n", "\r")):
            content, ending = line[:-1], line[-1]
        match = re.match(r"^(\s*)(#{1,6})(?=\s|$)", content)
        if match:
            content = (
                match.group(1)
                + "".join("\\" + char for char in match.group(2))
                + content[match.end(2) :]
            )
        match = re.match(r"^(\s*)(>+)(?=\s|$)", content)
        if match:
            content = (
                match.group(1)
                + "".join("\\" + char for char in match.group(2))
                + content[match.end(2) :]
            )
        match = re.match(r"^(\s*)([-+])(?=\s)", content)
        if match:
            content = match.group(1) + "\\" + match.group(2) + content[match.end(2) :]
        match = re.match(r"^(\s*)(\d+)([.)])(?=\s)", content)
        if match:
            content = (
                match.group(1)
                + match.group(2)
                + "\\"
                + match.group(3)
                + content[match.end(3) :]
            )
        if re.fullmatch(r"\s*(?:-{3,}|={3,})\s*", content):
            marker = "-" if "-" in content else "="
            content = content.replace(marker, "\\" + marker)
        lines.append(content + ending)
    return "".join(lines)


def _href_for_markdown(current_href: str, raw_href: str) -> tuple[str, str | None]:
    parsed = urllib.parse.urlsplit(raw_href.strip())
    if parsed.scheme or parsed.netloc:
        return raw_href.strip(), urllib.parse.unquote(parsed.fragment) or None
    path = urllib.parse.unquote(parsed.path)
    fragment = urllib.parse.unquote(parsed.fragment) or None
    if not path:
        return (f"#{fragment}" if fragment else ""), fragment
    resolved = _safe_package_member(
        posixpath.join(posixpath.dirname(current_href), path), label="链接目标"
    )
    query = f"?{parsed.query}" if parsed.query else ""
    return (
        f"{resolved}{query}#{fragment}" if fragment else f"{resolved}{query}"
    ), fragment


class _Renderer:
    def __init__(
        self,
        *,
        unit: int | None,
        href: str,
        target_ids: Mapping[str, set[str]],
        package_members: set[str],
        strict_links: bool,
    ):
        self.unit = unit
        self.href = href
        self.target_ids = target_ids
        self.package_members = package_members
        self.strict_links = strict_links
        self.lines: list[str] = []
        self.locations: list[SourceLocation] = []
        self.warnings: list[str] = []
        self.current_note_ids: set[str] = set()

    def _line(self) -> int:
        return len(self.lines) + 1

    def _record(self, element_id: str, kind: str) -> None:
        self.locations.append(
            SourceLocation(self.unit, self.href, element_id, kind, self._line())
        )

    def marker(
        self, element_id: str, kind: str = "anchor", attrs: str | None = None
    ) -> str:
        self._record(element_id, kind)
        if attrs is None:
            attr_text = (
                f"#{element_id}"
                if _ID_RE.fullmatch(element_id)
                else f'id="{element_id.replace(chr(34), "&quot;")}"'
            )
        else:
            attr_text = attrs
        return f"[]{{{attr_text}}}"

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _check_href(self, raw_href: str, rendered_href: str) -> None:
        parsed = urllib.parse.urlsplit(raw_href.strip())
        if parsed.scheme or parsed.netloc:
            return
        path = (
            _safe_package_member(
                posixpath.join(
                    posixpath.dirname(self.href), urllib.parse.unquote(parsed.path)
                ),
                label="链接目标",
            )
            if parsed.path
            else self.href
        )
        if path not in self.package_members:
            message = f"链接目标不存在：{path}"
            if self.strict_links:
                raise EpubSourceError(message)
            self._warn(message)
            return
        if parsed.fragment:
            target = urllib.parse.unquote(parsed.fragment)
            known = self.target_ids.get(path, set())
            if target not in known:
                message = f"链接片段目标不存在：{path}#{target}"
                if self.strict_links:
                    raise EpubSourceError(message)
                self._warn(message)

    def inline(self, element: ET.Element | None) -> str:
        if element is None:
            return ""
        result = _escape_text(element.text or "")
        for child in list(element):
            result += self._inline_node(child)
            result += _escape_text(child.tail or "")
        if element.attrib.get("id") and _local(element.tag) in {
            "i",
            "em",
            "b",
            "strong",
            "u",
            "code",
            "q",
            "abbr",
            "cite",
            "small",
            "mark",
        }:
            result = self.marker(element.attrib["id"], "inline") + result
        return result

    def _inline_node(self, child: ET.Element) -> str:
        tag = _local(child.tag)
        if tag in _SKIP_HEAD_TAGS:
            return ""
        if tag in {"i", "em"}:
            return f"*{self.inline(child)}*"
        if tag in {"b", "strong"}:
            return f"**{self.inline(child)}**"
        if tag == "u":
            return f"[{self.inline(child)}]{{.underline}}"
        if tag == "br":
            return "  \n"
        if tag == "img":
            raw_src = child.attrib.get("src", "")
            if not raw_src:
                raise EpubSourceError("图片缺少 src")
            rendered_src, _ = _href_for_markdown(self.href, raw_src)
            self._check_href(raw_src, rendered_src)
            alt = _escape_text(child.attrib.get("alt", ""))
            result = f"![{alt}]({rendered_src})"
            if child.attrib.get("id"):
                result = self.marker(child.attrib["id"], "image") + result
            return result
        if tag == "a":
            raw_href = child.attrib.get("href", "")
            if not raw_href:
                raise EpubSourceError("链接缺少 href")
            rendered_href, fragment = _href_for_markdown(self.href, raw_href)
            note_target = fragment if not urllib.parse.urlsplit(raw_href).path else None
            if note_target in self.current_note_ids:
                result = (
                    self.marker(child.attrib["id"], "reference")
                    if child.attrib.get("id")
                    else ""
                )
                return result + f"[^{note_target}]"
            self._check_href(raw_href, rendered_href)
            result = f"[{self.inline(child)}]({rendered_href})"
            if child.attrib.get("id"):
                result = self.marker(child.attrib["id"], "link") + result
            return result
        if tag == "span":
            inner = self.inline(child)
            attrs = _attrs(child)
            if child.attrib.get("id"):
                self._record(child.attrib["id"], "span")
            return f"[{inner}]{{{attrs}}}" if attrs else inner
        if tag in {"sup", "sub"}:
            class_name = "sup" if tag == "sup" else "sub"
            return f"[{self.inline(child)}]{{.{class_name}}}"
        if tag in {"code", "q"}:
            return (
                f"`{self.inline(child)}`"
                if tag == "code"
                else f"“{self.inline(child)}”"
            )
        if tag in {"abbr", "cite", "small", "mark"}:
            return self.inline(child)
        raise EpubSourceError(f"正文包含未支持的行内元素：{tag}")

    def _prefix_id(self, element: ET.Element, text: str, kind: str) -> str:
        element_id = element.attrib.get("id")
        return f"{self.marker(element_id, kind)}{text}" if element_id else text

    def block(self, element: ET.Element) -> list[str]:
        tag = _local(element.tag)
        if tag in _SKIP_HEAD_TAGS:
            return []
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            text = self.inline(element)
            element_id = element.attrib.get("id")
            suffix = f" {{#{element_id}}}" if element_id else ""
            if element_id:
                self._record(element_id, "heading")
            return [f"{'#' * level} {text}{suffix}"]
        if tag == "p":
            text = self.inline(element)
            return [self._prefix_id(element, text, "paragraph")]
        if tag in {"blockquote"}:
            return self._quote(element)
        if tag == "div" or tag in {"section", "aside", "article", "header", "footer"}:
            if self._is_notes_container(element):
                return []
            classes = _tokens(element.attrib.get("class"))
            if "quotes" in classes or "quote" in classes:
                return self._quote(element)
            inner = self.blocks(list(element))
            mixed_text = (element.text or "").strip() or any(
                (child.tail or "").strip() for child in list(element)
            )
            if mixed_text and inner:
                raise EpubSourceError(
                    f"块容器含未支持的混合直接文本：{element.attrib.get('id', '')}"
                )
            if not inner and mixed_text:
                inner = [self.inline(element)]
            attrs = _attrs(element)
            if not inner:
                self._warn(f"空 div 保留为 fenced div：{element.attrib.get('id', '')}")
            if element.attrib.get("id"):
                self._record(element.attrib["id"], "div")
            opening = f"::: {{{attrs}}}" if attrs else ":::"
            return [opening, *inner, ":::"]
        if tag in {"ul", "ol"}:
            return self._list(element)
        if tag == "hr":
            return ["---"]
        if tag == "img":
            return [self._inline_node(element)]
        raise EpubSourceError(f"正文包含未支持的块元素：{tag}")

    def _quote(self, element: ET.Element) -> list[str]:
        children = list(element)
        block_tags = {
            "p",
            "div",
            "section",
            "aside",
            "article",
            "blockquote",
            "ul",
            "ol",
        }
        has_block_child = any(_local(child.tag) in block_tags for child in children)
        has_mixed_text = bool((element.text or "").strip()) or any(
            (child.tail or "").strip() for child in children
        )
        if has_mixed_text and has_block_child:
            raise EpubSourceError("引用块含未支持的混合直接文本")
        inner = (
            self.blocks(children)
            if has_block_child
            else ([self.inline(element)] if has_mixed_text or children else [])
        )
        if element.attrib.get("id"):
            if inner:
                inner[0] = self.marker(element.attrib["id"], "quote") + inner[0]
            else:
                self._record(element.attrib["id"], "quote")
        return [">" if not line else f"> {line}" for line in inner]

    def _list(self, element: ET.Element) -> list[str]:
        ordered = _local(element.tag) == "ol"
        if ordered and element.attrib.get("start") not in {None, "1"}:
            raise EpubSourceError(
                f"暂不支持从非 1 开始的有序列表：{element.attrib.get('start')}"
            )
        lines: list[str] = []
        number = 1
        for child in list(element):
            if _local(child.tag) != "li":
                raise EpubSourceError(f"列表包含非 li 子元素：{_local(child.tag)}")
            item_marker = (
                self.marker(child.attrib["id"], "list-item")
                if child.attrib.get("id")
                else ""
            )
            inline_text = item_marker + _escape_text(child.text or "")
            block_parts: list[list[str]] = []
            nested: list[str] = []
            for inline_child in list(child):
                child_tag = _local(inline_child.tag)
                if child_tag in {"ul", "ol"}:
                    nested.extend(self._list(inline_child))
                    if (inline_child.tail or "").strip():
                        block_parts.append(
                            [_escape_text(inline_child.tail or "").strip()]
                        )
                elif child_tag in {
                    "p",
                    "blockquote",
                    "div",
                    "section",
                    "aside",
                    "article",
                }:
                    rendered_block = self.block(inline_child)
                    if rendered_block:
                        block_parts.append(rendered_block)
                    if (inline_child.tail or "").strip():
                        block_parts.append(
                            [_escape_text(inline_child.tail or "").strip()]
                        )
                else:
                    inline_text += self._inline_node(inline_child)
                    inline_text += _escape_text(inline_child.tail or "")
            blocks: list[list[str]] = []
            if inline_text.strip():
                blocks.append([inline_text.strip()])
            blocks.extend(block_parts)
            if not blocks and not nested:
                blocks = [[""]]
            content_lines = [line for block in blocks for line in block]
            marker = f"{number}. " if ordered else "- "
            lines.append(marker + content_lines[0])
            lines.extend("  " + line for line in content_lines[1:])
            lines.extend("  " + line for line in nested)
            number += 1
        if element.attrib.get("id"):
            lines.insert(0, self.marker(element.attrib["id"], "list"))
            # Keep the anchor as its own paragraph.  Without a blank line,
            # Pandoc treats a following list marker as literal paragraph text
            # (especially inside a fenced div), silently flattening the list.
            lines.insert(1, "")
        return lines

    def blocks(self, elements: Sequence[ET.Element]) -> list[str]:
        result: list[str] = []
        for element in elements:
            result.extend(self.block(element))
            if result and result[-1] != "":
                result.append("")
        while result and result[-1] == "":
            result.pop()
        return result

    def _is_notes_container(self, element: ET.Element) -> bool:
        classes = _tokens(element.attrib.get("class"))
        epub_type = element.attrib.get("{http://www.idpf.org/2007/ops}type", "")
        return bool(classes & _NOTE_CLASS_TOKENS) or epub_type in _NOTE_CLASS_TOKENS

    def render_note(self, note: ET.Element) -> list[str]:
        note_id = note.attrib.get("id", "")
        if not note_id:
            raise EpubSourceError("脚注定义缺少 id")
        self._record(note_id, "note")
        children = list(note)
        block_children = [
            child
            for child in children
            if "label" not in _tokens(child.attrib.get("class"))
            and _local(child.tag)
            in {
                "p",
                "div",
                "section",
                "aside",
                "article",
                "blockquote",
                "ul",
                "ol",
            }
        ]
        if block_children:
            content = []
            if (note.text or "").strip():
                content.append(_escape_text(note.text or "").strip())
            for child in children:
                if "label" in _tokens(child.attrib.get("class")):
                    if (child.tail or "").strip():
                        content.append(_escape_text(child.tail or "").strip())
                    continue
                content.extend(self.block(child))
                if (child.tail or "").strip():
                    content.append(_escape_text(child.tail or "").strip())
        else:
            inline_text = _escape_text(note.text or "")
            for child in children:
                if "label" in _tokens(child.attrib.get("class")):
                    inline_text += _escape_text(child.tail or "")
                else:
                    inline_text += self._inline_node(child)
                    inline_text += _escape_text(child.tail or "")
            content = [inline_text.strip()] if inline_text.strip() else []
        if not content:
            self._warn(f"空脚注定义：{note_id}")
            content = ["[]{.empty-note}"]
        first = f"[^{note_id}]: []{{#{note_id}}}{content[0]}"
        return [first, *(f"    {line}" if line else "" for line in content[1:])]


def _find_body(root: ET.Element) -> ET.Element:
    bodies = [element for element in root.iter() if _local(element.tag) == "body"]
    if len(bodies) != 1:
        raise EpubSourceError(f"XHTML 必须有唯一 body，实际为 {len(bodies)} 个")
    return bodies[0]


def _find_notes_container(body: ET.Element) -> ET.Element | None:
    found = [
        element
        for element in body.iter()
        if (element is not body)
        and (
            bool(_tokens(element.attrib.get("class")) & _NOTE_CLASS_TOKENS)
            or element.attrib.get("{http://www.idpf.org/2007/ops}type", "")
            in _NOTE_CLASS_TOKENS
        )
    ]
    if len(found) > 1:
        raise EpubSourceError("XHTML 含多个脚注容器，无法安全合并")
    return found[0] if found else None


def _collect_ids(root: ET.Element) -> set[str]:
    ids: set[str] = set()
    for element in root.iter():
        element_id = element.attrib.get("id")
        if not element_id:
            continue
        if element_id in ids:
            raise EpubSourceError(f"XHTML id 重复：{element_id}")
        ids.add(element_id)
    return ids


def _note_nodes(container: ET.Element | None) -> list[ET.Element]:
    if container is None:
        return []
    result: list[ET.Element] = []
    for element in container.iter():
        if element is container or not element.attrib.get("id"):
            continue
        classes = element.attrib.get("class", "")
        if _NOTE_CLASS_RE.search(classes) or _NOTE_ID_RE.search(element.attrib["id"]):
            result.append(element)
    return result


_ANCHOR_ONLY_RE = re.compile(
    r'^\s*\[\]\{(?:#[A-Za-z_][A-Za-z0-9_.:-]*|id="[^"]+")\}\s*$'
)
_QUOTE_PREFIX_RE = re.compile(r"^(\s*(?:>\s*)+)")
_LIST_PREFIX_RE = re.compile(r"^(\s*(?:[-+*]\s+|\d+[.)]\s+))")
_FOOTNOTE_PREFIX_RE = re.compile(r"^(\s*\[\^[^\]]+\]:\s*)")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(?:\s|$)")


def _insert_unit_marker(lines: list[str], marker: str, warnings: list[str]) -> None:
    """Insert a source-unit span without changing the first block type."""

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _ANCHOR_ONLY_RE.fullmatch(line):
            continue
        if stripped.startswith("#"):
            # Heading-only front matter is handled explicitly below; when a
            # heading precedes prose, the marker belongs in that prose block.
            continue
        if stripped.startswith(":::"):
            # Fenced div opening/closing lines are syntax, so search its first
            # child block instead of turning the fence into a paragraph.
            continue
        match = _QUOTE_PREFIX_RE.match(line)
        if match:
            lines[index] = line[: match.end()] + marker + line[match.end() :]
            return
        match = _LIST_PREFIX_RE.match(line)
        if match:
            lines[index] = line[: match.end()] + marker + line[match.end() :]
            return
        match = _FOOTNOTE_PREFIX_RE.match(line)
        if match:
            lines[index] = line[: match.end()] + marker + line[match.end() :]
            return
        if _FENCE_RE.match(line):
            warnings.append(
                "代码块是首个正文块，source-unit marker 无法作为代码内容嵌入；需人工复核"
            )
            return
        lines[index] = marker + line
        return

    heading_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if lines[index].startswith("#")
        ),
        None,
    )
    if heading_index is None:
        raise EpubSourceError("无法把 source-unit 放入正文首个块")
    lines[heading_index] = lines[heading_index] + " " + marker
    warnings.append(
        "该 XHTML 只有标题块，source-unit marker 已附加到标题末尾；需人工复核"
    )


def _relocate_locations(
    markdown: str, locations: Sequence[SourceLocation]
) -> tuple[tuple[SourceLocation, ...], tuple[str, ...]]:
    """Bind recorded source IDs to the line containing their emitted marker."""

    lines = markdown.splitlines()
    relocated: list[SourceLocation] = []
    missing: list[str] = []
    for location in locations:
        if _ID_RE.fullmatch(location.source_id):
            token = re.compile(r"\{#" + re.escape(location.source_id) + r"(?=[\s}])")
        else:
            token = re.compile(
                r'id="' + re.escape(location.source_id.replace('"', "&quot;")) + r'"'
            )
        line_number = next(
            (index for index, line in enumerate(lines, start=1) if token.search(line)),
            None,
        )
        if line_number is None:
            missing.append(location.source_id)
            line_number = location.markdown_line
        relocated.append(replace(location, markdown_line=line_number))
    return tuple(relocated), tuple(missing)


def convert_xhtml(
    payload: bytes | str,
    *,
    href: str = "",
    unit: int | None = None,
    package_targets: Mapping[str, set[str]] | None = None,
    package_members: Iterable[str] | None = None,
    strict_links: bool = True,
    include_unit_marker: bool = False,
) -> ConversionResult:
    """Convert one XHTML document to formal-reader Markdown.

    ``package_targets`` maps package-relative XHTML paths to their known IDs.
    Passing it enables strict validation of cross-file fragment links.  With
    no map, local links are still checked and cross-file targets are reported
    as warnings rather than silently discarded.
    """

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    root = _parse_xml(raw, href or "XHTML")
    body = _find_body(root)
    ids = _collect_ids(root)
    current_href = href or "__inline.xhtml"
    targets = dict(package_targets or {current_href: ids})
    targets.setdefault(current_href, ids)
    members = set(package_members or targets)
    renderer = _Renderer(
        unit=unit,
        href=current_href,
        target_ids=targets,
        package_members=members,
        strict_links=strict_links,
    )
    notes_container = _find_notes_container(body)
    note_nodes = _note_nodes(notes_container)
    renderer.current_note_ids = {note.attrib["id"] for note in note_nodes}
    if notes_container is not None and not note_nodes:
        renderer._warn("空 Notes 容器已显式保留")

    if (body.text or "").strip() or any(
        (child.tail or "").strip() for child in list(body)
    ):
        raise EpubSourceError("body 含未支持的混合直接文本")

    body_elements = [
        element
        for element in list(body)
        if element is not notes_container and _local(element.tag) not in _SKIP_HEAD_TAGS
    ]
    lines = renderer.blocks(body_elements)
    if notes_container is not None and not note_nodes:
        attrs = _attrs(notes_container)
        if notes_container.attrib.get("id"):
            renderer._record(notes_container.attrib["id"], "notes")
        lines.extend([f"::: {{{attrs}}}" if attrs else ":::", ":::"])
    if note_nodes:
        if lines:
            lines.append("")
        for note in note_nodes:
            lines.extend(renderer.render_note(note))
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()

    if include_unit_marker:
        if unit is None:
            raise EpubSourceError("include_unit_marker 需要 unit")
        marker = f'[]{{.source-unit data-unit="{unit}"}}'
        _insert_unit_marker(lines, marker, renderer.warnings)

    markdown = "\n".join(lines).rstrip() + "\n"
    locations, missing_locations = _relocate_locations(markdown, renderer.locations)
    for source_id in missing_locations:
        renderer._warn(f"来源 id 未能绑定到 Markdown 锚点：{source_id}")
    return ConversionResult(
        unit=unit,
        href=current_href,
        markdown=markdown,
        locations=locations,
        warnings=tuple(renderer.warnings),
    )


def render_epub(
    path: str | Path,
    *,
    units: Iterable[int] | None = None,
    strict_links: bool = True,
    include_unit_marker: bool = True,
) -> tuple[EpubPackage, tuple[ConversionResult, ...]]:
    """Render selected XHTML spine units and return draft Markdown results."""

    package = read_epub(path)
    selected = (
        set(units) if units is not None else {item.unit for item in package.spine}
    )
    unknown = selected - {item.unit for item in package.spine}
    if unknown:
        raise EpubSourceError(f"请求了不存在的 spine unit：{sorted(unknown)}")
    xhtml_items = [item for item in package.manifest if item.media_type == _XHTML]
    target_ids: dict[str, set[str]] = {}
    with _PackageReader(package.path) as reader:
        for item in xhtml_items:
            root = _parse_xml(reader.read(item.href), item.href)
            target_ids[item.href] = _collect_ids(root)
        selected_hrefs = {item.href for item in package.spine if item.unit in selected}
        owners: dict[str, list[str]] = {}
        for item_href in selected_hrefs:
            for element_id in target_ids.get(item_href, set()):
                owners.setdefault(element_id, []).append(item_href)
        collisions = {
            element_id: hrefs for element_id, hrefs in owners.items() if len(hrefs) > 1
        }
        if collisions and strict_links:
            details = "; ".join(
                f"{element_id} ({', '.join(sorted(hrefs))})"
                for element_id, hrefs in sorted(collisions.items())
            )
            raise EpubSourceError(
                "选定的 XHTML 合并范围含重复 fragment id，无法安全生成单入口草稿："
                + details
            )
        results: list[ConversionResult] = []
        for item in package.spine:
            if item.unit not in selected:
                continue
            if item.media_type != _XHTML:
                raise EpubSourceError(
                    f"spine unit {item.unit} 不是 XHTML，无法生成 Markdown：{item.media_type}"
                )
            result = convert_xhtml(
                reader.read(item.href),
                href=item.href,
                unit=item.unit,
                package_targets=target_ids,
                package_members=package.members,
                strict_links=strict_links,
                include_unit_marker=include_unit_marker,
            )
            if collisions:
                result = replace(
                    result,
                    warnings=(
                        *result.warnings,
                        "选定范围存在重复 fragment id，合并为单入口时必须先重写链接："
                        + ", ".join(sorted(collisions)),
                    ),
                )
            results.append(result)
    return package, tuple(results)


def write_drafts(
    package: EpubPackage,
    drafts: Sequence[ConversionResult],
    output_dir: str | Path,
    *,
    scratch_root: str | Path,
) -> tuple[tuple[Path, ...], Path]:
    """Write one combined ``draft.md`` and its source map into scratch.

    The individual :class:`ConversionResult` objects remain the unit-level API;
    this writer joins them in the supplied spine order so the CLI produces only
    the two inspectable scratch artifacts required by ``source-draft``.
    """

    root = Path(scratch_root).resolve()
    requested_destination = Path(output_dir)
    if requested_destination.is_symlink() or bool(
        getattr(requested_destination, "is_junction", lambda: False)()
    ):
        raise EpubSourceError("草稿目录不能是 symlink 或 junction")
    destination = requested_destination.resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise EpubSourceError(
            f"草稿目录必须位于 scratch root：{destination} -> {root}"
        ) from error
    if destination.exists():
        if not destination.is_dir():
            raise EpubSourceError(f"草稿目录不是目录：{destination}")
        if any(destination.iterdir()):
            raise EpubSourceError(f"拒绝覆盖已有草稿目录：{destination}")
    destination.mkdir(parents=True, exist_ok=True)
    if not drafts:
        raise EpubSourceError("没有可写出的 XHTML 草稿")
    draft_path = destination / "draft.md"
    map_path = destination / "source-map.json"
    for target in (draft_path, map_path):
        if (
            target.exists()
            or target.is_symlink()
            or bool(getattr(target, "is_junction", lambda: False)())
        ):
            raise EpubSourceError(f"拒绝覆盖已有草稿文件：{target}")

    parts = [draft.markdown.rstrip("\n") for draft in drafts]
    combined = "\n\n".join(parts).rstrip("\n") + "\n"
    mapping: list[dict[str, object]] = []
    line_offset = 0
    for index, (draft, part) in enumerate(zip(drafts, parts, strict=True)):
        locations = [
            replace(location, markdown_line=location.markdown_line + line_offset)
            for location in draft.locations
        ]
        mapping.append(
            {
                "unit": draft.unit,
                "href": draft.href,
                "unit_sha256": package.member_sha256.get(draft.href),
                "markdown": draft_path.name,
                "line_offset": line_offset,
                "locations": [location.as_dict() for location in locations],
                "warnings": list(draft.warnings),
            }
        )
        line_offset += part.count("\n") + (2 if index < len(parts) - 1 else 0)

    draft_path.write_text(combined, encoding="utf-8", newline="\n")
    map_path.write_text(
        json.dumps(
            {
                "opf": package.opf_path,
                "source_sha256": package.source_sha256,
                "reader": PANDOC_READER,
                "units": mapping,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return (draft_path,), map_path


__all__ = [
    "PANDOC_READER",
    "SOURCE_UNITS_HEADER",
    "ConversionResult",
    "EpubPackage",
    "EpubSourceError",
    "ManifestItem",
    "SourceLocation",
    "SpineUnit",
    "convert_xhtml",
    "read_epub",
    "read_source_units",
    "render_epub",
    "write_drafts",
]
