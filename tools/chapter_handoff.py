"""Opt-in chapter handoff consistency checks, before merging owned fragments.

``ChapterScope`` is supplied by an explicit manifest/controlled handoff
declaration, not inferred from marker counts.  A package records source review;
this machine check cannot establish where a marker belongs on an original page.
Legacy books without a declared package do not acquire a new prerequisite.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

try:
    from tools.epub_semantics import ast_nodes, inline_text
except ModuleNotFoundError:
    # translator.py is also a supported direct script entry point.
    from epub_semantics import ast_nodes, inline_text


class HandoffError(ValueError):
    """A chapter handoff is incomplete, unsafe, or conflicts with its scope."""


GLOSSARY_KINDS = frozenset({
    "title", "author", "translator", "chapter", "name", "person", "place",
    "institution", "term", "concept", "work",
})


@dataclass(frozen=True)
class SourceAnchor:
    identifier: int
    anchor: str


@dataclass(frozen=True)
class ChapterScope:
    chapter_id: str
    source_kind: str
    source_ids: tuple[int, ...]
    start: SourceAnchor
    end: SourceAnchor
    languages: tuple[str, ...]
    header_level: int = 1

    @classmethod
    def from_dict(cls, declaration: Mapping[str, Any]) -> ChapterScope:
        """Read only the small scope declaration, never manuscript text."""
        _fail(isinstance(declaration, Mapping), "章节范围声明必须是 JSON 对象")
        try:
            source = declaration["source"]
            _fail(isinstance(source, Mapping) and isinstance(source.get("start"), Mapping)
                  and isinstance(source.get("end"), Mapping), "章节范围必须声明来源与明确首尾锚点")
            _fail(isinstance(source.get("ids"), (list, tuple))
                  and isinstance(declaration.get("languages"), (list, tuple)),
                  "章节来源集合与语言必须使用数组")
            result = cls(
                declaration["chapter_id"], source["kind"], tuple(source["ids"]),
                SourceAnchor(source["start"]["id"], source["start"]["anchor"]),
                SourceAnchor(source["end"]["id"], source["end"]["anchor"]),
                tuple(declaration["languages"]), declaration.get("header_level", 1),
            )
            _validate_scope(result)
            return result
        except (KeyError, TypeError) as error:
            raise HandoffError("章节范围声明缺少 chapter_id/source/languages 或明确首尾锚点") from error


@dataclass(frozen=True)
class HandoffReport:
    chapter_id: str
    languages: tuple[str, ...]
    source_ids: tuple[int, ...]
    entries: tuple[Path, ...]
    assets: tuple[Path, ...]
    terms: int
    issues: int
    # Whether this package requires a full-source visual declaration, not
    # whether marker positions have otherwise been independently proven.
    positions_require_visual_review: bool = True


def _fail(condition: bool, message: str) -> None:
    if not condition:
        raise HandoffError(message)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _ids(value: Any, label: str) -> tuple[int, ...]:
    _fail(isinstance(value, (list, tuple)) and bool(value), f"{label} 必须声明非空来源集合")
    _fail(all(type(item) is int and item > 0 for item in value), f"{label} 来源编号必须为正整数")
    values = tuple(value)
    _fail(tuple(sorted(set(values))) == values, f"{label} 来源集合必须按序且唯一")
    return values


def _safe_file(root: Path, relative: Any, label: str) -> Path:
    _fail(_nonempty(relative), f"{label} 必须有相对文件路径")
    path = Path(relative)
    windows = PureWindowsPath(relative)
    _fail(not path.is_absolute() and not windows.is_absolute() and not windows.drive,
          f"{label} 不允许绝对路径：{relative}")
    _fail("\\" not in relative and "\x00" not in relative,
          f"{label} 必须使用规范相对路径：{relative}")
    resolved = (root / path).resolve()
    _fail(resolved.is_relative_to(root), f"{label} 路径越过交接根：{relative}")
    _fail(resolved.is_file(), f"{label} 文件不存在：{relative}")
    return resolved


def _document_text(value: Any) -> str:
    """Text used for explicit entry-edge anchors, not source-page placement."""
    if isinstance(value, list):
        return " ".join(filter(None, (_document_text(child) for child in value)))
    if not isinstance(value, dict):
        return ""
    kind, content = value.get("t"), value.get("c")
    if kind in {"Header", "Para", "Plain"}:
        return _entry_inline_text(content[2] if kind == "Header" else content)
    if kind == "LineBlock":
        return " ".join(_entry_inline_text(line) for line in content)
    if kind == "CodeBlock":
        return content[1]
    if kind == "Div":
        if set(content[0][1]) & {"source-page", "source-unit"}:
            return ""
        return _document_text(content[1])
    return " ".join(filter(None, (_document_text(child) for child in value.values())))


def _entry_inline_text(inlines: Any) -> str:
    # A footnote's body is not a continuation of its host paragraph's edge.
    # Source markers inside notes still participate in the AST source check.
    def without_notes(value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("t") == "Note":
                return {"t": "Span", "c": [["", [], []], []]}
            return {key: without_notes(child) for key, child in value.items()}
        if isinstance(value, list):
            return [without_notes(child) for child in value]
        return value

    return inline_text(without_notes(inlines))


def _source_markers(document: Mapping[str, Any], kind: str) -> tuple[int, ...]:
    marker_class = "source-page" if kind == "page" else "source-unit"
    other_class = "source-unit" if kind == "page" else "source-page"
    attribute = "data-page" if kind == "page" else "data-unit"
    ids: list[int] = []
    for node in ast_nodes(document.get("blocks", [])):
        if node["t"] != "Span":
            continue
        attributes, children = node["c"]
        classes = set(attributes[1])
        _fail(other_class not in classes, "交接条目混用了来源页与来源单元")
        if marker_class not in classes:
            continue
        raw = dict(attributes[2]).get(attribute, "")
        _fail(isinstance(raw, str) and raw.isascii() and raw.isdecimal() and int(raw) > 0,
              f"交接条目含无效 {marker_class} 编号")
        _fail(not children, f"交接条目 {marker_class} 必须是空溯源节点")
        ids.append(int(raw))
    return tuple(ids)


def _validate_auxiliary_sources(document: Mapping[str, Any], scope: ChapterScope) -> None:
    for node in ast_nodes(document.get("blocks", [])):
        kind, content = node["t"], node.get("c")
        if kind == "Header":
            attributes = content[1]
        elif kind in {"Span", "Div", "Code", "CodeBlock", "Image", "Link", "Table"}:
            attributes = content[0]
        else:
            continue
        auxiliary = dict(attributes[2]).get("data-source-pages")
        if auxiliary is None:
            continue
        _fail(scope.source_kind == "page", "无可靠固定页码的来源不能声明 data-source-pages")
        _fail(isinstance(auxiliary, str) and re.fullmatch(r"[1-9][0-9]*(?:,[1-9][0-9]*)*", auxiliary) is not None,
              "data-source-pages 必须是规范逗号分隔的正整数集合")
        values = _ids(tuple(int(value) for value in auxiliary.split(",")), "data-source-pages")
        _fail(set(values).issubset(scope.source_ids), "data-source-pages 辅助来源集合越过受控章节范围")


def _validate_scope(scope: ChapterScope) -> None:
    _fail(isinstance(scope, ChapterScope), "必须提供受控 ChapterScope")
    _fail(_nonempty(scope.chapter_id), "章节范围必须有稳定 chapter_id")
    _fail(isinstance(scope.source_kind, str) and scope.source_kind in {"page", "unit"}, "来源类型只能是 page 或 unit")
    values = _ids(scope.source_ids, "受控章节")
    _fail(isinstance(scope.start, SourceAnchor) and isinstance(scope.end, SourceAnchor), "受控章节必须声明 SourceAnchor")
    _fail(type(scope.start.identifier) is int and type(scope.end.identifier) is int
          and scope.start.identifier == values[0] and scope.end.identifier == values[-1],
          "受控章节首尾编号必须对应完整来源集合")
    _fail(_nonempty(scope.start.anchor) and _nonempty(scope.end.anchor), "受控章节必须声明明确首尾锚点")
    _fail(bool(scope.languages) and all(_nonempty(x) for x in scope.languages)
          and len(set(scope.languages)) == len(scope.languages), "受控章节必须声明唯一的语言条目")
    _fail(type(scope.header_level) is int and 1 <= scope.header_level <= 6,
          "章节 header_level 必须是 1–6")


def _validate_source(declaration: Any, scope: ChapterScope) -> None:
    _fail(isinstance(declaration, dict), "交接缺少来源范围")
    _fail(declaration.get("kind") == scope.source_kind, "交接来源类型与受控章节不一致")
    _fail(_ids(declaration.get("ids"), "交接") == scope.source_ids,
          "交接来源集合与完整章节不一致；不能只交付同数量的其他来源")
    for name, wanted in (("start", scope.start), ("end", scope.end)):
        anchor = declaration.get(name)
        _fail(isinstance(anchor, dict) and type(anchor.get("id")) is int
              and anchor["id"] == wanted.identifier and anchor.get("anchor") == wanted.anchor,
              f"交接来源 {name} 首尾锚点与受控章节不一致")


def validate_handoff(
    package: Mapping[str, Any], *, root: Path, scope: ChapterScope,
    read_ast: Callable[[Path], Mapping[str, Any]],
    glossary: Mapping[str, str] | None = None,
) -> HandoffReport:
    """Validate an explicitly declared package against its complete chapter.

    ``root`` is the caller-approved staging root; all entries/assets stay inside
    it, including resolved symlinks. ``read_ast`` must use the formal reader on
    the supplied actual path, before filters erase source markers. ``glossary``
    is the existing exact source→target mapping for this target language.
    Review fields record declarations; this check neither runs the named
    validations nor establishes marker positions in the original source.
    """
    _validate_scope(scope)
    _fail(isinstance(package, Mapping), "交接包必须是 JSON 对象")
    version = package.get("schema_version")
    _fail(type(version) is int and version in (1, 2), "交接 schema_version 必须是 1 或 2")
    _fail(package.get("chapter_id") == scope.chapter_id, "交接 chapter_id 与受控章节不一致")
    _validate_source(package.get("source"), scope)
    root = root.resolve()
    _fail(root.is_dir(), "交接根不存在")

    assets = package.get("assets")
    _fail(isinstance(assets, list), "交接必须声明资产列表（可为空）")
    asset_paths = tuple(_safe_file(root, path, "交接资产") for path in assets)
    _fail(len(set(asset_paths)) == len(asset_paths), "交接资产重复")
    entries = package.get("entries")
    _fail(isinstance(entries, list) and bool(entries), "交接缺少语言条目")
    _fail(all(isinstance(entry, dict) for entry in entries), "交接语言条目必须是对象")
    languages = tuple(entry.get("language") for entry in entries)
    _fail(all(_nonempty(language) for language in languages), "交接语言标识必须为非空字符串")
    _fail(len(languages) == len(set(languages)) and set(languages) == set(scope.languages),
          "交接语言条目不完整或重复；必须覆盖受控章节声明的全部语言")
    entry_paths: list[Path] = []
    referenced_assets: set[Path] = set()
    for entry in entries:
        path = _safe_file(root, entry.get("path"), "交接条目")
        _fail(path not in entry_paths and path not in asset_paths, "交接条目路径重复或冒充资产")
        entry_paths.append(path)
        document = read_ast(path)
        _fail(isinstance(document, Mapping), f"正式 reader 未返回 AST：{path.name}")
        _validate_auxiliary_sources(document, scope)
        _fail(_source_markers(document, scope.source_kind) == scope.source_ids,
              f"{entry['language']} 实际来源集合/顺序与完整章节不一致")
        headers = [node for node in ast_nodes(document.get("blocks", [])) if node["t"] == "Header"]
        _fail(bool(headers) and headers[0]["c"][0] == scope.header_level
              and headers[0]["c"][1][0] == scope.chapter_id,
              f"{entry['language']} 缺少受控章节首标题或层级不一致")
        _fail(all(node["c"][0] > scope.header_level for node in headers[1:]),
              f"{entry['language']} 混入相邻章节或章节范围未闭合")
        text = " ".join(_document_text(document.get("blocks", [])).split())
        first, last = entry.get("start_anchor"), entry.get("end_anchor")
        _fail(_nonempty(first) and _nonempty(last), f"{entry['language']} 缺少明确首尾文本锚点")
        _fail(text.startswith(" ".join(first.split())) and text.endswith(" ".join(last.split())),
              f"{entry['language']} 首尾文本锚点不在实际章节边界")
        for node in ast_nodes(document.get("blocks", [])):
            if node["t"] != "Image":
                continue
            target = node["c"][2][0]
            parts = urllib.parse.urlsplit(target)
            _fail(not parts.scheme and not parts.netloc and not parts.query,
                  f"交接资产引用必须是本地文件：{target}")
            decoded = urllib.parse.unquote(parts.path)
            _fail(bool(decoded), "交接图片引用缺少文件路径")
            _fail(not Path(decoded).is_absolute() and not PureWindowsPath(decoded).drive and "\\" not in decoded,
                  f"交接资产引用必须使用规范相对路径：{target}")
            relative = (path.parent / decoded).resolve()
            _fail(relative.is_relative_to(root) and relative.is_file(), f"交接资产引用缺失或越界：{target}")
            referenced_assets.add(relative)
    _fail(referenced_assets == set(asset_paths), "交接资产清单与实际图片引用不一致（缺少声明或未引用资产）")

    terms = package.get("terms")
    _fail(isinstance(terms, list), "交接必须声明术语增量（可为空）")
    known = dict(glossary or {})
    for term in terms:
        _fail(isinstance(term, dict) and set(term) == {"kind", "source", "target", "note"}
              and all(_nonempty(term.get(key)) for key in ("kind", "source", "target"))
              and isinstance(term.get("note"), str), "交接术语必须使用 glossary 四列结构")
        _fail(term["kind"] in GLOSSARY_KINDS, f"交接术语 kind 无效：{term['kind']}")
        source, target = term["source"], term["target"]
        _fail(source not in known or known[source] == target, f"交接术语冲突：{source!r}")
        known[source] = target

    issues = package.get("issues")
    _fail(isinstance(issues, list), "交接必须声明疑点/勘误（可为空）")
    for issue in issues:
        _fail(isinstance(issue, dict) and isinstance(issue.get("kind"), str)
              and issue["kind"] in {"question", "erratum"}
              and type(issue.get("source_id")) is int and issue["source_id"] in scope.source_ids
              and _nonempty(issue.get("anchor")) and _nonempty(issue.get("detail")),
              "交接疑点/勘误必须绑定本章来源编号、锚点与说明")
        _fail(issue.get("status") == "resolved", "交接仍有未裁决疑点/勘误，不能合入")

    review = package.get("review")
    _fail(isinstance(review, dict),
          "交接缺少视觉核定与验证结果" if version == 1 else "交接缺少自动核定与验证结果")
    visual = review.get("visual")
    if version == 1:
        _fail(isinstance(visual, dict) and _nonempty(visual.get("reviewer")), "交接缺少视觉核定负责人")
        _fail(_ids(visual.get("source_ids"), "视觉核定") == scope.source_ids,
              "视觉核定声明未覆盖完整章节来源")
    else:
        automatic = review.get("automatic")
        _fail(isinstance(automatic, dict) and _nonempty(automatic.get("reviewer")), "交接缺少自动核定负责人")
        _fail(_ids(automatic.get("source_ids"), "自动核定") == scope.source_ids,
              "自动核定声明未覆盖完整章节来源")
        _fail(isinstance(visual, list), "交接视觉异常核定必须是数组（可为空）")
        for exception in visual:
            _fail(isinstance(exception, dict) and _nonempty(exception.get("reviewer"))
                  and _nonempty(exception.get("reason")), "视觉异常核定必须有负责人和理由")
            source_ids = _ids(exception.get("source_ids"), "视觉异常核定")
            _fail(set(source_ids).issubset(scope.source_ids), "视觉异常核定来源集合越过受控章节范围")
    validations = review.get("validations")
    _fail(isinstance(validations, list) and bool(validations), "交接缺少验证结果")
    names: set[str] = set()
    for validation in validations:
        _fail(isinstance(validation, dict) and _nonempty(validation.get("name")), "交接验证结果缺少名称")
        _fail(validation["name"] not in names, "交接验证结果名称重复")
        names.add(validation["name"])
        _fail(validation.get("result") == "pass", f"交接验证未通过：{validation['name']}")
    return HandoffReport(scope.chapter_id, tuple(languages), scope.source_ids,
                         tuple(entry_paths), asset_paths, len(terms), len(issues),
                         positions_require_visual_review=(version == 1))


def validate_declared_handoffs(
    declarations: Iterable[tuple[Mapping[str, Any], Path, ChapterScope]] | None, *,
    read_ast: Callable[[Path], Mapping[str, Any]], glossary: Mapping[str, str] | None = None,
) -> tuple[HandoffReport, ...]:
    """Optional integration seam: absent declaration preserves legacy behavior."""
    if declarations is None:
        return ()
    reports: list[HandoffReport] = []
    chapters: set[str] = set()
    known = dict(glossary or {})
    for package, root, scope in declarations:
        _fail(scope.chapter_id not in chapters, "交接声明重复章节")
        chapters.add(scope.chapter_id)
        # Adjacent chapters can legitimately share a printed page or an EPUB
        # spine unit. Their explicit first/last anchors, not disjoint counts,
        # identify those ranges.
        reports.append(validate_handoff(package, root=root, scope=scope, read_ast=read_ast, glossary=known))
        for term in package["terms"]:
            known[term["source"]] = term["target"]
    return tuple(reports)
