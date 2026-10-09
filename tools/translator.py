#!/usr/bin/env python3
"""Manifest-driven book conversion and translation CLI."""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    from tools import epub_browser_qa, epub_source, page_boundary_audit
except ModuleNotFoundError:  # direct ``python tools/translator.py`` execution
    import epub_browser_qa
    import epub_source
    import page_boundary_audit


REPO_ROOT = Path(__file__).resolve().parents[1]
FORMATS = {"typst", "markdown", "html", "latex"}
TARGETS = {
    "typst": {"epub", "pdf"},
    "markdown": {"html", "epub", "pdf"},
    "html": {"html"},
    "latex": {"epub", "pdf"},
}
DEFAULT_TARGETS = {
    "typst": ("pdf",),
    "markdown": ("html", "pdf"),
    "html": ("html",),
    "latex": ("pdf",),
}
PAGE_MAP_HEADER = (
    "pdf_page",
    "original_printed_page",
    "section",
    "chapter_id",
    "chapter_title",
    "note",
)
SOURCE_UNITS_HEADER = (
    "unit_id",
    "spine_order",
    "xhtml_path",
    "linear",
    "kind",
    "title",
    "note",
)
GLOSSARY_HEADER = ("kind", "source", "target", "note")
GLOSSARY_KINDS = {
    "title",
    "author",
    "translator",
    "chapter",
    "name",
    "person",
    "place",
    "institution",
    "term",
    "concept",
    "work",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
LANG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
PAGES_RE = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
SOURCE_PAGE_RE = re.compile(r"#source-page\(\s*(\d+)\s*\)")
MARKDOWN_SOURCE_PAGE_RE = re.compile(
    r"\{[^}]*\.source-page[^}]*\bdata-page=[\"'](\d+)[\"'][^}]*\}"
)
MARKDOWN_SOURCE_PAGE_MARKER_RE = re.compile(
    r"\[\]\{[^}\r\n]*\.source-page[^}\r\n]*\}"
)
CJK_BOUNDARY_CHAR_RE = re.compile(
    r"[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef]"
)
MARKDOWN_SOURCE_UNIT_RE = re.compile(
    r"\{[^}]*\.source-unit[^}]*\bdata-unit=[\"'](\d+)[\"'][^}]*\}"
)
LATEX_SOURCE_PAGE_RE = re.compile(r"\\sourcepage\{\s*(\d+)\s*\}")
EPUB_PAGE_ID_RE = re.compile(r"\bid=[\"']Page_(\d+)[\"']")
LATEX_INCLUDE_RE = re.compile(r"\\(?:input|include)\s*\{([^{}\r\n]+)\}")
LATEX_GRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\s*\[[^\]\r\n]*\])?\s*\{([^{}\r\n]+)\}"
)
MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))"
)
MARKDOWN_STANDALONE_IMAGE_RE = re.compile(
    r"^ {0,3}!\[(?P<alt>[^\]\r\n]*)\]\([^\r\n]*\)"
    r"[ \t]*(?P<attrs>\{[^}\r\n]*\})?"
    r"(?:\[\]\{\.image-tail\})?[ \t]*$"
)
MARKDOWN_FIGURE_NUMBER_PATTERN = (
    r"(?:[A-Z](?:\.\d+)+|\d+(?:\.\d+)*(?:[a-z])?)"
)
MARKDOWN_NUMBERED_FIGURE_CAPTION_RE = re.compile(
    rf"^(?:图|Figures?|Figs?)\.?[ \t]*"
    rf"(?P<numbers>{MARKDOWN_FIGURE_NUMBER_PATTERN}"
    rf"(?:[ \t]*(?:、|,|，|和|及|/|&|and)[ \t]*"
    rf"{MARKDOWN_FIGURE_NUMBER_PATTERN})*)",
    re.IGNORECASE,
)
MARKDOWN_FIGURE_NUMBER_RE = re.compile(
    MARKDOWN_FIGURE_NUMBER_PATTERN, re.IGNORECASE
)
TYPST_IMAGE_RE = re.compile(r"#?image\s*\(\s*[\"']([^\"']+)[\"']")
SOURCE_RASTER_SUFFIXES = frozenset(
    {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
PDF_IMAGE_SUFFIXES = SOURCE_RASTER_SUFFIXES | frozenset(
    {
        ".ccitt",
        ".jb2",
        ".jbig2",
        ".jp2",
        ".jpx",
        ".pam",
        ".pbm",
        ".pgm",
        ".ppm",
        ".raw",
        ".tif",
        ".tiff",
    }
)
LOCAL_PYTHON_ENTRY_RE = re.compile(
    r"(?<![\w/.-])((?:tools|scripts)/[A-Za-z0-9_.-]+\.py|[A-Za-z0-9_.-]+\.py)(?![\w/.-])"
)
LITERAL_INCLUDE_RE = re.compile(r'#include\s+"([^"\r\n]+)"')
TOOL_TRUNCATION_RE = re.compile(
    r"Warning: truncated output \(original token count: [0-9]+(?:,[0-9]{3})*\)"
    r"|Total output lines: [0-9]+(?:,[0-9]{3})*(?![0-9,])"
    r"|…[0-9]+(?:,[0-9]{3})* tokens? truncated…"
    r"|\.\.\.[0-9]+(?:,[0-9]{3})* tokens? truncated\.\.\."
)
EPUBCHECK_JAR = (
    REPO_ROOT / "tools" / "epubcheck" / "target" / "bookloom" / "epubcheck.jar"
)
QA_DEFAULT_DPI = 100
BROWSER_QA_DEFAULTS = {
    "width": 390,
    "height": 844,
    "timeout": 600,
    "page_timeout": 30,
    "screenshots": 6,
}
WORKFLOW_LOG_SCHEMA = 2
WORKFLOW_LOG_SCHEMAS = {1, WORKFLOW_LOG_SCHEMA}
WORK_COMMANDS = frozenset(
    {
        "doctor",
        "station",
        "render",
        "check",
        "boundary-audit",
        "build",
        "qa",
        "browser-qa",
        "refresh",
        "finalize",
        "deliver",
        "complete",
        "clean",
        "prepare",
        "scratch",
        "source-draft",
        "source-probe",
    }
)
ACTIVITIES = (
    "none",
    "source-review",
    "content-repair",
    "structure-repair",
    "style-repair",
    "translation",
    "visual-qa",
    "delivery",
    "diagnosis",
    "docs-process",
    "agent-review",
    "mixed",
    "unknown",
)
STATION_INTERVAL_MAX_SECONDS = 4 * 60 * 60
SNAPSHOT_EXCLUDED_PARTS = {
    "Books",
    "output",
    ".cache",
    "cache",
    ".tmp",
    "tmp",
    ".local",
    "local",
}
APPLE_BOOKS_DARK_CLASS = "ibooks-dark-theme-use-custom-text-color"
MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
EPUB_NAMESPACE = "http://www.idpf.org/2007/ops"
EPUB_READER_STATE_MEMBERS = {"META-INF/calibre_bookmarks.txt"}
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
GLADTEX_VERSION = "3.1.0"
SVG_MATH_CACHE_VERSION = "2"
FORMULA_CLASSES = {"math-inline", "math-display"}
SOURCE_INLINE_FORMULA_EM_PER_PX = 0.06
SOURCE_FORMULA_CSS_RE = re.compile(
    r"\.math-inline-(?P<name>[A-Za-z0-9_.-]+)\s*\{\s*"
    r"width\s*:\s*(?P<width>\d+(?:\.\d+)?)px\s*!important\s*;\s*"
    r"height\s*:\s*(?P<height>\d+(?:\.\d+)?)px\s*!important\s*;"
    r"(?:\s*vertical-align\s*:\s*(?P<align>[^;{}]+)\s*;)?\s*\}",
    re.IGNORECASE | re.DOTALL,
)
GENERIC_IMAGE_ALT = {
    "diagram",
    "figure",
    "image",
    "img",
    "photo",
    "picture",
    "图",
    "图片",
    "插图",
}
LATEX_CONTROL = re.compile(r"\\[A-Za-z]+")
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


class CliError(Exception):
    pass


class StationLaneBusy(CliError):
    def __init__(self, lane: dict[str, str]) -> None:
        self.lane = lane
        super().__init__("工站 lane 正忙，请等待上一工站出站后重试")


@dataclass(frozen=True)
class Language:
    code: str
    role: str
    entry: Path
    glossary: Path | None
    outputs: dict[str, Path]
    epub_title: str | None = None
    epub_author: str | None = None
    epub_identifier: str | None = None


@dataclass(frozen=True)
class Work:
    root: Path
    path: Path
    manifest: dict[str, Any]
    work_id: str
    title: str
    authoring_format: str
    pdf_engine: str | None
    source: Path
    source_pages: int
    languages: tuple[Language, ...]
    source_kind: str = "pdf"
    source_unit_rows: tuple[dict[str, str], ...] = ()


def quote_toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def inside(
    base: Path, value: str | Path, label: str, *, must_exist: bool = False
) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise CliError(f"{label} 必须是相对路径：{value}")
    base = base.resolve()
    candidate = (base / raw).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError as error:
        raise CliError(f"{label} 越出允许目录：{value}") from error
    if must_exist and not candidate.exists():
        raise CliError(f"{label} 不存在：{candidate}")
    return candidate


def read_source_units(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != SOURCE_UNITS_HEADER:
            raise CliError("source-units.tsv 表头不符合项目契约")
        rows: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            if not all(row.get(key) for key in SOURCE_UNITS_HEADER[:5]):
                raise CliError(f"source-units.tsv 第 {row_number} 行字段不能为空")
            try:
                unit_id = int(row["unit_id"])
                spine_order = int(row["spine_order"])
            except ValueError as error:
                raise CliError(
                    f"source-units.tsv 第 {row_number} 行编号无效"
                ) from error
            if unit_id < 1 or spine_order < 1:
                raise CliError(f"source-units.tsv 第 {row_number} 行编号必须为正整数")
            if row["linear"] not in {"yes", "no"}:
                raise CliError(
                    f"source-units.tsv 第 {row_number} 行 linear 必须是 yes 或 no"
                )
            rows.append({key: row.get(key, "") for key in SOURCE_UNITS_HEADER})
    if not rows:
        raise CliError("source-units.tsv 不能为空")
    if [int(row["unit_id"]) for row in rows] != list(range(1, len(rows) + 1)):
        raise CliError("source-units.tsv 的 unit_id 必须从 1 连续编号")
    if [int(row["spine_order"]) for row in rows] != list(range(1, len(rows) + 1)):
        raise CliError("source-units.tsv 的 spine_order 必须从 1 连续编号")
    return tuple(rows)


def validate_tool_truncation_entries(work: Work) -> None:
    for language in work.languages:
        text = language.entry.read_text(encoding="utf-8")
        match = TOOL_TRUNCATION_RE.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            raise CliError(
                f"{language.code} 第 {line} 行包含工具截断签名：{match.group(0)}"
            )


def resolve_work_path(value: str | Path, root: Path = REPO_ROOT) -> Path:
    root = root.resolve()
    raw = Path(value)
    candidate = (raw if raw.is_absolute() else root / raw).resolve()
    works_root = (root / "Works").resolve()
    try:
        candidate.relative_to(works_root)
    except ValueError as error:
        raise CliError(f"工作目录必须位于 {works_root}：{candidate}") from error
    return candidate


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy() if env is None else env.copy()
    process_temp = str(temp_root())
    process_env["TEMP"] = process_temp
    process_env["TMP"] = process_temp
    # Tool-only uv dependencies must not rewrite the repository lockfile.
    process_env["UV_FROZEN"] = "true"
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=process_env,
            text=True,
            capture_output=True,
            errors="replace",
        )
    except FileNotFoundError as error:
        raise CliError(f"找不到命令：{command[0]}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise CliError(
            f"命令失败（{result.returncode}）：{' '.join(command)}\n{detail}"
        )
    return result


def load_work(value: str | Path, root: Path = REPO_ROOT) -> Work:
    root = root.resolve()
    work_path = resolve_work_path(value, root)
    manifest_path = work_path / "manifest.toml"
    if not manifest_path.is_file():
        raise CliError(f"缺少 manifest.toml：{manifest_path}")
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise CliError(f"无法读取 manifest.toml：{error}") from error

    if manifest.get("schema_version") != 2:
        raise CliError("manifest.schema_version 必须是 2")
    work_data = manifest.get("work")
    if not isinstance(work_data, dict):
        raise CliError("manifest 缺少 [work]")
    work_id = work_data.get("id")
    title = work_data.get("title")
    status = work_data.get("status")
    if not isinstance(work_id, str) or not ID_RE.fullmatch(work_id):
        raise CliError("work.id 只能包含小写字母、数字、点、下划线和短横线")
    if not isinstance(title, str) or not title.strip():
        raise CliError("work.title 不能为空")
    if status not in {"planned", "active", "complete"}:
        raise CliError("work.status 必须是 planned、active 或 complete")
    if (
        "source_language" in work_data
        or "target_language" in work_data
        or "target_languages" in work_data
    ):
        raise CliError("语言只允许在 [[languages]] 中声明")
    if "build" in manifest:
        raise CliError("交付目标只允许在 languages.outputs 中声明")

    authoring = manifest.get("authoring")
    if not isinstance(authoring, dict) or authoring.get("format") not in FORMATS:
        raise CliError("authoring.format 必须是 typst、markdown、html 或 latex")
    authoring_format = str(authoring["format"])
    pdf_engine = authoring.get("pdf_engine")
    if pdf_engine is not None and pdf_engine != "typst":
        raise CliError("authoring.pdf_engine 目前只支持 typst")
    if authoring_format != "markdown" and pdf_engine is not None:
        raise CliError("只有 markdown 可以设置 authoring.pdf_engine")

    source_data = manifest.get("source")
    if (
        not isinstance(source_data, dict)
        or source_data.get("path_base") != "repository_root"
    ):
        raise CliError("source.path_base 必须是 repository_root")
    source_relative = source_data.get("relative_path")
    source_hash = source_data.get("sha256")
    if not isinstance(source_relative, str) or not isinstance(source_hash, str):
        raise CliError("source.relative_path 和 source.sha256 必须存在")
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", source_hash):
        raise CliError("source.sha256 必须是 64 位十六进制摘要")
    source = inside(root, source_relative, "source.relative_path", must_exist=True)
    source_suffix = source.suffix.lower()
    if not source.is_file() or source_suffix not in {".pdf", ".epub"}:
        raise CliError(f"来源必须是 PDF 或 EPUB 文件：{source}")
    image_data = source_data.get("images")
    if image_data is not None and (
        not isinstance(image_data, dict) or image_data.get("mode") != "embedded"
    ):
        raise CliError('source.images.mode 目前只支持 "embedded"')
    if image_data is not None and source_suffix != ".pdf":
        raise CliError("source.images 目前只适用于 PDF 来源")
    source_kind = "pdf" if source_suffix == ".pdf" else "epub-pages"
    source_unit_rows: tuple[dict[str, str], ...] = ()
    source_units_path: Path | None = None

    if source_suffix == ".pdf":
        pdf_data = manifest.get("pdf")
        source_pages = pdf_data.get("pages") if isinstance(pdf_data, dict) else None
        if not isinstance(source_pages, int) or source_pages < 1:
            raise CliError("pdf.pages 必须是正整数")
    else:
        source_format = source_data.get("format", source_data.get("kind"))
        if source_format != "epub":
            raise CliError('EPUB 来源必须设置 source.format = "epub"')
        units_raw = source_data.get("units")
        if units_raw is not None:
            if not isinstance(units_raw, str) or not units_raw:
                raise CliError("source.units 必须是相对路径")
            source_units_path = inside(
                work_path, units_raw, "source.units", must_exist=True
            )
            source_unit_rows = read_source_units(source_units_path)
            source_pages = len(source_unit_rows)
            source_kind = "epub-units"
        else:
            source_pages = source_data.get("pages")
            anchor_pages = source_data.get("anchor_pages")
            anchor_set = source_data.get("anchor_set")
            if not isinstance(source_pages, int) or source_pages < 1:
                raise CliError("source.pages 必须是正整数")
            if not isinstance(anchor_pages, int) or anchor_pages < 1:
                raise CliError("source.anchor_pages 必须是正整数")
            if anchor_set is not None and (
                not isinstance(anchor_set, str) or not PAGES_RE.fullmatch(anchor_set)
            ):
                raise CliError("source.anchor_set 必须类似 5-11,15-188")

    raw_languages = manifest.get("languages")
    if not isinstance(raw_languages, list) or not raw_languages:
        raise CliError("manifest 至少需要一个 [[languages]]")

    output_root = (work_path / "output").resolve(strict=False)
    try:
        output_root.relative_to(work_path.resolve())
    except ValueError as error:
        raise CliError(f"output/ 越出工作目录：{output_root}") from error
    if output_root.exists() and not output_root.is_dir():
        raise CliError(f"output/ 必须是目录：{output_root}")
    reserved = {
        path_key(manifest_path),
        path_key(work_path / "STATUS.md"),
        path_key(work_path / "page-map.tsv"),
        path_key(source),
    }
    if source_units_path:
        reserved.add(path_key(source_units_path))
    seen_codes: set[str] = set()
    seen_outputs: set[str] = set()
    languages: list[Language] = []
    source_roles = 0

    for index, raw_language in enumerate(raw_languages, start=1):
        if not isinstance(raw_language, dict):
            raise CliError(f"languages[{index}] 必须是表")
        code = raw_language.get("code")
        role = raw_language.get("role")
        entry_raw = raw_language.get("entry")
        outputs_raw = raw_language.get("outputs")
        if not isinstance(code, str) or not LANG_RE.fullmatch(code):
            raise CliError(f"languages[{index}].code 无效")
        if code in seen_codes:
            raise CliError(f"重复语言代码：{code}")
        seen_codes.add(code)
        if role not in {"source", "translation"}:
            raise CliError(f"{code}.role 必须是 source 或 translation")
        source_roles += role == "source"
        if not isinstance(entry_raw, str):
            raise CliError(f"{code}.entry 必须存在")
        entry = inside(work_path, entry_raw, f"{code}.entry", must_exist=True)
        if not entry.is_file():
            raise CliError(f"{code}.entry 必须是文件：{entry}")
        try:
            entry.relative_to(output_root)
        except ValueError:
            pass
        else:
            raise CliError(f"{code}.entry 不得位于 output/ 中")
        reserved.add(path_key(entry))

        glossary = None
        glossary_raw = raw_language.get("glossary")
        if glossary_raw is not None:
            if not isinstance(glossary_raw, str):
                raise CliError(f"{code}.glossary 必须是路径")
            glossary = inside(
                work_path, glossary_raw, f"{code}.glossary", must_exist=True
            )
            if not glossary.is_file():
                raise CliError(f"{code}.glossary 必须是文件：{glossary}")
            try:
                glossary.relative_to(output_root)
            except ValueError:
                pass
            else:
                raise CliError(f"{code}.glossary 不得位于 output/ 中")
            reserved.add(path_key(glossary))

        if not isinstance(outputs_raw, dict) or not outputs_raw:
            raise CliError(f"{code}.outputs 不能为空")
        outputs: dict[str, Path] = {}
        for target, output_raw in outputs_raw.items():
            if target not in TARGETS[authoring_format]:
                raise CliError(f"{authoring_format} 不支持 {target} 输出")
            if not isinstance(output_raw, str):
                raise CliError(f"{code}.outputs.{target} 必须是路径")
            output = inside(work_path, output_raw, f"{code}.outputs.{target}")
            try:
                output.relative_to(output_root)
            except ValueError as error:
                raise CliError(f"输出必须位于 {output_root}：{output}") from error
            expected_suffix = f".{target}"
            if output.suffix.lower() != expected_suffix:
                raise CliError(f"{output} 必须使用 {expected_suffix} 扩展名")
            key = path_key(output)
            if key in seen_outputs or key in reserved:
                raise CliError(f"输出路径重复或与输入碰撞：{output}")
            seen_outputs.add(key)
            outputs[target] = output
        epub_values: dict[str, str | None] = {}
        for key in ("epub_title", "epub_author", "epub_identifier"):
            value = raw_language.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise CliError(f"{code}.{key} 必须是非空字符串")
            epub_values[key] = value.strip() if isinstance(value, str) else None
        if "epub" not in outputs and any(epub_values.values()):
            raise CliError(f"{code} 未声明 EPUB 输出，不得设置 EPUB 元数据")
        languages.append(
            Language(
                code,
                role,
                entry,
                glossary,
                outputs,
                epub_values["epub_title"],
                epub_values["epub_author"],
                epub_values["epub_identifier"],
            )
        )

    if source_roles != 1:
        raise CliError("必须恰有一个 role = source 的语言")
    has_epub = any("epub" in language.outputs for language in languages)
    if has_epub:
        epub_data = manifest.get("epub")
        if not isinstance(epub_data, dict):
            raise CliError("EPUB 输出必须设置 [epub]")
        for key in ("title", "author", "identifier"):
            value = epub_data.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise CliError(f"epub.{key} 必须是非空字符串")
        cover_raw = epub_data.get("cover")
        if cover_raw is not None:
            if not isinstance(cover_raw, str) or not cover_raw:
                raise CliError("epub.cover 必须是相对路径")
            inside(work_path, cover_raw, "epub.cover", must_exist=True)
        if authoring_format == "markdown":
            if not isinstance(cover_raw, str) or not cover_raw:
                raise CliError("Markdown EPUB 输出必须设置 [epub].cover")
        if authoring_format in {"typst", "latex"}:
            default_title = epub_data.get("title")
            for language in languages:
                if "epub" in language.outputs and not (
                    language.epub_title or isinstance(default_title, str)
                ):
                    raise CliError(f"{language.code} 的 EPUB 输出必须设置标题")
        if authoring_format == "latex" and source_suffix != ".pdf" and not cover_raw:
            raise CliError("非 PDF 来源的 LaTeX EPUB 输出必须设置 [epub].cover")
    has_markdown_pdf = authoring_format == "markdown" and any(
        "pdf" in language.outputs for language in languages
    )
    if has_markdown_pdf and pdf_engine != "typst":
        raise CliError('Markdown PDF 必须设置 authoring.pdf_engine = "typst"')
    if not has_markdown_pdf and pdf_engine is not None:
        raise CliError("没有 Markdown PDF 输出时不得设置 authoring.pdf_engine")

    return Work(
        root,
        work_path,
        manifest,
        work_id,
        title,
        authoring_format,
        pdf_engine,
        source,
        source_pages,
        tuple(languages),
        source_kind,
        source_unit_rows,
    )


def load_command_work(args: argparse.Namespace) -> Work:
    work = load_work(args.work)
    args._workflow_work_id = work.work_id
    args._workflow_authoring_format = work.authoring_format
    args._workflow_source_kind = work.source_kind
    args._workflow_source_count = work.source_pages
    args._workflow_targets = sorted(
        f"{language.code}:{target}"
        for language in work.languages
        for target in language.outputs
    )
    return work


def status_document_state(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:24]
    except OSError as error:
        raise CliError(f"无法读取 STATUS.md：{error}") from error
    for line in lines:
        match = re.search(
            r"(?:^|\s)(?:状态|status)\s*[:：=]\s*(.+)$", line, re.IGNORECASE
        )
        if not match:
            continue
        value = match.group(1).casefold().lstrip("`* ")
        aliases = {
            "planned": ("planned", "计划", "待开始", "已初始化"),
            "active": (
                "active",
                "进行中",
                "处理中",
                "阻塞",
                "blocked",
                "未完成",
                "不完整",
                "未验收",
                "incomplete",
                "not complete",
                "not accepted",
            ),
            "complete": (
                "complete",
                "full-local-accepted",
                "local-accepted",
                "已完成",
                "完成",
                "已验收",
                "已交付",
                "验收",
            ),
        }
        for state, markers in aliases.items():
            if any(
                re.match(re.escape(marker) + r"(?=$|[\s`*;；,，。.(（])", value)
                for marker in markers
            ):
                return state
        # An unknown declaration must not inherit a state from its explanation.
        return None
    return None

def validate_manifest_status_alignment(work: Work) -> None:
    work_path = getattr(work, "path", None)
    if not isinstance(work_path, Path):
        return
    document_state = status_document_state(work_path / "STATUS.md")
    if document_state is None:
        return
    manifest_state = str(work.manifest["work"]["status"])
    if manifest_state != document_state:
        raise CliError(
            "manifest/STATUS 状态漂移："
            f"manifest={manifest_state}, STATUS={document_state}"
        )


def replace_manifest_work_status(text: str, status: str) -> str:
    lines = text.splitlines(keepends=True)
    in_work = False
    replaced = 0
    for index, line in enumerate(lines):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[: -len(ending)] if ending else line
        header = re.fullmatch(r"\s*\[([^]]+)\]\s*(?:#.*)?", body)
        if header:
            in_work = header.group(1).strip() == "work"
            continue
        if not in_work:
            continue
        match = re.fullmatch(r'(\s*status\s*=\s*")[^"]*("\s*(?:#.*)?)', body)
        if not match:
            continue
        lines[index] = f'{match.group(1)}{status}{match.group(2)}{ending}'
        replaced += 1
    if replaced != 1:
        raise CliError("manifest [work] 必须恰有一个字面量 status 字段")
    return "".join(lines)


def manifest_build_sha256(path: Path) -> str:
    normalized = replace_manifest_work_status(
        path.read_bytes().decode("utf-8"), "__workflow_state__"
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def source_image_audit_enabled(work: Work) -> bool:
    images = work.manifest.get("source", {}).get("images")
    return isinstance(images, dict) and images.get("mode") == "embedded"


def _source_asset_candidates(raw: str, entry: Path, work: Work) -> tuple[Path, ...]:
    parsed = urllib.parse.urlsplit(raw.strip())
    if parsed.scheme or parsed.netloc:
        raise CliError(f"来源图像必须是工作目录内的本地路径：{raw}")
    if not parsed.path:
        return ()
    relative = Path(urllib.parse.unquote(parsed.path))
    if relative.is_absolute():
        raise CliError(f"来源图像路径必须是相对路径：{raw}")
    candidates: list[Path] = []
    roots = (entry.parent, work.path, work.root)
    for base in roots:
        candidate = (base / relative).resolve(strict=False)
        if candidate.suffix.lower():
            candidates.append(candidate)
        else:
            candidates.extend(
                candidate.with_suffix(suffix)
                for suffix in sorted(SOURCE_RASTER_SUFFIXES)
            )
    return tuple(dict.fromkeys(candidates))


def source_raster_assets(work: Work) -> tuple[Path, ...]:
    if not source_image_audit_enabled(work):
        return ()
    language = next(
        language for language in work.languages if language.role == "source"
    )
    text = language.entry.read_text(encoding="utf-8")
    if work.authoring_format == "markdown":
        references = [
            match.group(1) or match.group(2)
            for match in MARKDOWN_IMAGE_RE.finditer(text)
        ]
    elif work.authoring_format == "latex":
        references = [
            match.group(1)
            for match in LATEX_GRAPHICS_RE.finditer(
                mask_latex_comments(expand_latex(language.entry, work.path))
            )
        ]
    elif work.authoring_format == "typst":
        references = [
            match.group(1)
            for match in TYPST_IMAGE_RE.finditer(mask_typst_comments(text))
        ]
    else:
        references = []

    assets: list[Path] = []
    root = work.path.resolve()
    for raw in references:
        candidates = _source_asset_candidates(raw, language.entry, work)
        if not candidates:
            continue
        resolved = next((candidate for candidate in candidates if candidate.is_file()), None)
        declared_suffix = Path(urllib.parse.urlsplit(raw.strip()).path).suffix.lower()
        if resolved is None:
            if declared_suffix in SOURCE_RASTER_SUFFIXES:
                raise CliError(f"来源图像不存在：{language.entry}:{raw}")
            continue
        if resolved.suffix.lower() not in SOURCE_RASTER_SUFFIXES:
            continue
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise CliError(f"来源图像越出工作目录：{raw}") from error
        if resolved not in assets:
            assets.append(resolved)
    return tuple(assets)


def pdf_image_hashes(source: Path, root: Path = REPO_ROOT) -> set[str]:
    with tempfile.TemporaryDirectory(
        prefix="pdfimages-", dir=temp_root(root)
    ) as directory:
        prefix = Path(directory) / "image"
        run(["pdfimages", "-all", str(source), str(prefix)])
        files = [
            path
            for path in Path(directory).iterdir()
            if path.is_file() and path.suffix.lower() in PDF_IMAGE_SUFFIXES
        ]
        if not files:
            raise CliError(f"pdfimages 未提取出图像对象：{source}")
        return {sha256(path) for path in files}


def validate_source_pdf_images(work: Work) -> str | None:
    if not source_image_audit_enabled(work):
        return None
    assets = source_raster_assets(work)
    if not assets:
        return "source images: no raster assets"
    embedded_hashes = pdf_image_hashes(work.source, work.root)
    missing: list[tuple[Path, str]] = []
    for asset in assets:
        digest = sha256(asset)
        if digest not in embedded_hashes:
            missing.append((asset, digest))
    if missing:
        details = ", ".join(
            f"{asset.relative_to(work.path).as_posix()}={digest}"
            for asset, digest in missing[:8]
        )
        if len(missing) > 8:
            details += f", …另有 {len(missing) - 8} 个"
        raise CliError(f"来源图像闭包失败（PDF 原始对象 SHA-256）：{details}")
    return (
        f"source images: {len(assets)} raster assets matched "
        f"{len(embedded_hashes)} embedded PDF objects"
    )


def authoritative_document_paths(root: Path = REPO_ROOT) -> tuple[Path, ...]:
    root = root.resolve()
    paths = [root / name for name in ("README.md", "AGENTS.md", "CONTEXT.md")]
    docs_root = root / "docs"
    if docs_root.is_dir():
        paths.extend(docs_root.rglob("*.md"))
    return tuple(sorted({path for path in paths if path.is_file()}))


def doc_entrypoint_errors(root: Path = REPO_ROOT) -> tuple[str, ...]:
    root = root.resolve()
    errors: list[str] = []
    for document in authoritative_document_paths(root):
        relative = document.relative_to(root).as_posix()
        for line_number, line in enumerate(
            document.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in LOCAL_PYTHON_ENTRY_RE.finditer(line):
                token = match.group(1)
                if "/" in token:
                    candidates = ((root / Path(token)).resolve(),)
                else:
                    candidates = tuple(
                        (root / base / token).resolve()
                        for base in (Path("."), Path("tools"), Path("scripts"))
                    )
                existing = tuple(dict.fromkeys(path for path in candidates if path.is_file()))
                if len(existing) == 1:
                    continue
                state = "不存在" if not existing else "不唯一"
                errors.append(
                    f"{relative}:{line_number}: {state}的本地 Python 入口 {token}"
                )
    return tuple(errors)


def epub_member_hashes(path: Path) -> dict[str, tuple[int, str]]:
    if not path.is_file():
        raise CliError(f"EPUB 不存在：{path}")
    if not zipfile.is_zipfile(path):
        raise CliError(f"EPUB 不是有效 ZIP：{path}")
    members: dict[str, tuple[int, str]] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.filename in members:
                    raise CliError(f"EPUB 含重复成员：{path} -> {info.filename}")
                digest = hashlib.sha256()
                with archive.open(info) as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                members[info.filename] = (info.file_size, digest.hexdigest().upper())
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise CliError(f"无法读取 EPUB：{path}: {error}") from error
    return members


def normalized_opf_build_metadata(payload: bytes) -> bytes:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return payload
    changed = False
    timestamp = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
    for element in root.iter():
        value = (element.text or "").strip()
        is_generated_date = (
            element.tag == f"{{{DC_NAMESPACE}}}date"
            and element.attrib.get("id") == "epub-date"
        )
        is_modified = (
            element.tag.rsplit("}", 1)[-1] == "meta"
            and element.attrib.get("property") == "dcterms:modified"
        )
        if (is_generated_date or is_modified) and timestamp.fullmatch(value):
            element.text = "BUILD_TIMESTAMP"
            changed = True
    return ET.tostring(root, encoding="utf-8") if changed else payload


def epub_member_bytes(path: Path, member: str) -> bytes:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(member)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise CliError(f"无法读取 EPUB 成员：{path} -> {member}: {error}") from error


def mutool_pages(path: Path) -> tuple[int, str]:
    try:
        result = run(["mutool", "info", str(path)])
        match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
        if match:
            return int(match.group(1)), result.stdout
    except CliError as info_error:
        try:
            fallback = run(["mutool", "show", str(path), "pages"])
        except CliError:
            raise info_error
        matches = re.findall(r"^page\s+(\d+)\s*=", fallback.stdout, re.MULTILINE)
        if matches:
            return len(matches), fallback.stdout
        raise info_error
    raise CliError(f"无法从 mutool info 读取页数：{path}")


def expand_page_spec(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        bounds = part.split("-", 1)
        start = int(bounds[0])
        end = int(bounds[-1])
        if start > end:
            raise CliError(f"页码范围无效：{value}")
        pages.extend(range(start, end + 1))
    return pages


def compress_page_spec(pages: list[int]) -> str:
    if not pages:
        return ""
    ranges: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def epub_page_anchors(path: Path, *, allow_missing: bool = False) -> tuple[list[int], str]:
    if not zipfile.is_zipfile(path):
        raise CliError(f"EPUB 来源不是有效 ZIP：{path}")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise CliError(f"EPUB 来源包含损坏条目：{path}")
            names = set(archive.namelist())
            container_name = "META-INF/container.xml"
            if container_name not in names:
                raise CliError(f"EPUB 来源缺少容器文件：{path}")
            try:
                container = ET.fromstring(archive.read(container_name))
            except ET.ParseError as error:
                raise CliError(f"EPUB 来源容器无效：{path}") from error
            opf_raw = next(
                (
                    element.attrib.get("full-path")
                    for element in container.iter()
                    if element.tag.rsplit("}", 1)[-1] == "rootfile"
                ),
                None,
            )
            if not opf_raw:
                raise CliError(f"EPUB 来源缺少 OPF 路径：{path}")
            opf_path = posixpath.normpath(urllib.parse.unquote(opf_raw))
            if opf_path not in names:
                raise CliError(f"EPUB 来源 OPF 不存在：{opf_path}")
            try:
                opf = ET.fromstring(archive.read(opf_path))
            except ET.ParseError as error:
                raise CliError(f"EPUB 来源 OPF 无效：{opf_path}") from error
            content_paths: list[str] = []
            base = posixpath.dirname(opf_path)
            for element in opf.iter():
                if element.tag.rsplit("}", 1)[-1] != "item":
                    continue
                if element.attrib.get("media-type") != "application/xhtml+xml":
                    continue
                href = element.attrib.get("href")
                if not href:
                    continue
                member = posixpath.normpath(
                    posixpath.join(
                        base, urllib.parse.unquote(urllib.parse.urlsplit(href).path)
                    )
                )
                if member not in names:
                    raise CliError(f"EPUB 来源正文不存在：{member}")
                content_paths.append(member)
            if not content_paths:
                raise CliError(f"EPUB 来源 OPF 没有正文 XHTML：{opf_path}")
            pages: list[int] = []
            seen: set[int] = set()
            for member in content_paths:
                text = archive.read(member).decode("utf-8", errors="replace")
                for match in EPUB_PAGE_ID_RE.finditer(text):
                    page = int(match.group(1))
                    if page in seen:
                        raise CliError(f"EPUB 来源页面锚点重复：Page_{page}")
                    seen.add(page)
                    pages.append(page)
            pages.sort()
            if not pages and not allow_missing:
                raise CliError(f"EPUB 来源正文没有 Page_N 页面锚点：{path}")
            return pages, f"opf={opf_path};content_files={len(content_paths)}"
    except zipfile.BadZipFile as error:
        raise CliError(f"EPUB 来源不是有效 ZIP：{path}") from error


def validate_epub_source_units(
    path: Path,
    rows: tuple[dict[str, str], ...],
) -> str:
    if not zipfile.is_zipfile(path):
        raise CliError(f"EPUB 来源不是有效 ZIP：{path}")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise CliError(f"EPUB 来源包含损坏条目：{path}")
            names = set(archive.namelist())
            if not names or archive.namelist()[0] != "mimetype":
                raise CliError(f"EPUB 来源的 mimetype 必须是第一个条目：{path}")
            mimetype = archive.getinfo("mimetype")
            if mimetype.compress_type != zipfile.ZIP_STORED:
                raise CliError(f"EPUB 来源的 mimetype 必须未压缩：{path}")
            if archive.read("mimetype") != b"application/epub+zip":
                raise CliError(f"EPUB 来源 mimetype 内容无效：{path}")
            container_name = "META-INF/container.xml"
            if container_name not in names:
                raise CliError(f"EPUB 来源缺少容器文件：{path}")
            try:
                container = ET.fromstring(archive.read(container_name))
                opf_path = next(
                    element.attrib["full-path"]
                    for element in container.iter()
                    if element.tag.rsplit("}", 1)[-1] == "rootfile"
                )
                opf_path = posixpath.normpath(urllib.parse.unquote(opf_path))
                package = ET.fromstring(archive.read(opf_path))
            except (ET.ParseError, KeyError, StopIteration, ValueError) as error:
                raise CliError(f"EPUB 来源容器或 OPF 无效：{path}") from error
            manifest = {
                element.attrib.get("id"): element
                for element in package.iter()
                if element.tag.rsplit("}", 1)[-1] == "item"
            }
            spine = next(
                (
                    element
                    for element in package.iter()
                    if element.tag.rsplit("}", 1)[-1] == "spine"
                ),
                None,
            )
            if spine is None:
                raise CliError(f"EPUB 来源缺少 spine：{path}")
            itemrefs = [
                element
                for element in spine
                if element.tag.rsplit("}", 1)[-1] == "itemref"
            ]
            if len(itemrefs) != len(rows):
                raise CliError(
                    f"EPUB 来源 spine 项为 {len(itemrefs)}，source-units.tsv 为 {len(rows)}"
                )

            def member_for(href: str) -> str:
                clean_href = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
                return posixpath.normpath(
                    posixpath.join(posixpath.dirname(opf_path), clean_href)
                )

            for index, (itemref, row) in enumerate(zip(itemrefs, rows), start=1):
                idref = itemref.attrib.get("idref")
                item = manifest.get(idref)
                if item is None:
                    raise CliError(f"EPUB 来源 spine 第 {index} 项引用无效：{idref}")
                href = item.attrib.get("href")
                if not href:
                    raise CliError(f"EPUB 来源 spine 第 {index} 项缺少 href：{idref}")
                member = member_for(href)
                expected_linear = itemref.attrib.get("linear", "yes")
                if row["spine_order"] != str(index) or row["unit_id"] != str(index):
                    raise CliError(
                        f"source-units.tsv 第 {index} 行编号与 spine 顺序不符"
                    )
                if row["xhtml_path"] != member:
                    raise CliError(
                        f"source-units.tsv 第 {index} 行路径不符：{row['xhtml_path']} != {member}"
                    )
                if row["linear"] != expected_linear:
                    raise CliError(
                        f"source-units.tsv 第 {index} 行 linear 不符："
                        f"{row['linear']} != {expected_linear}"
                    )
                if member not in names:
                    raise CliError(f"EPUB 来源正文不存在：{member}")
            return f"opf={opf_path};spine_items={len(itemrefs)}"
    except zipfile.BadZipFile as error:
        raise CliError(f"EPUB 来源不是有效 ZIP：{path}") from error


def mask_typst_comments(text: str) -> str:
    def spaces(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    text = re.sub(r"/\*.*?\*/", spaces, text, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", spaces, text)


def mask_latex_comments(text: str) -> str:
    def spaces(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"(?<!\\)%[^\r\n]*", spaces, text)


def latex_braced(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : position], position + 1
    return None


def latex_skip_space(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def latex_graphic_paths(
    text: str,
    entry: Path,
    root: Path,
    inherited: tuple[Path, ...],
) -> tuple[Path, ...]:
    masked = mask_latex_comments(text)
    paths = inherited
    for match in re.finditer(r"\\graphicspath\b", masked):
        cursor = match.end()
        cursor = latex_skip_space(masked, match.end())
        outer = latex_braced(masked, cursor)
        if not outer:
            raise CliError(f"不支持动态 LaTeX graphicspath：{entry}")
        body, _ = outer
        parsed: list[Path] = []
        offset = 0
        while offset < len(body):
            while offset < len(body) and body[offset].isspace():
                offset += 1
            if offset == len(body):
                break
            item = latex_braced(body, offset)
            if not item:
                raise CliError(f"不支持动态 LaTeX graphicspath：{entry}")
            raw_path, offset = item
            if not raw_path.strip() or any(
                char in raw_path for char in ("\\", "$", "#")
            ):
                raise CliError(f"不支持动态 LaTeX graphicspath：{entry}")
            candidate = (entry.parent / raw_path.strip()).resolve(strict=False)
            try:
                candidate.relative_to(root.resolve())
            except ValueError as error:
                raise CliError(
                    f"LaTeX graphicspath 越出工作目录：{raw_path}"
                ) from error
            if not candidate.is_dir():
                raise CliError(f"LaTeX graphicspath 不存在：{candidate}")
            parsed.append(candidate)
        paths = tuple(parsed)
    return paths


def normalize_latex_graphics(
    text: str,
    entry: Path,
    root: Path,
    graphic_paths: tuple[Path, ...],
) -> tuple[str, tuple[Path, ...]]:
    masked = mask_latex_comments(text)
    parts: list[str] = []
    offset = 0
    paths = graphic_paths
    extensions = (".pdf", ".png", ".jpg", ".jpeg", ".svg")
    events = [
        *(
            ("graphicspath", match)
            for match in re.finditer(r"\\graphicspath\b", masked)
        ),
        *(("image", match) for match in LATEX_GRAPHICS_RE.finditer(masked)),
        *(
            ("epub-fallback", match)
            for match in re.finditer(r"\\epubfallback\b", masked)
        ),
    ]
    for kind, match in sorted(events, key=lambda event: event[1].start()):
        if match.start() < offset:
            continue
        if kind == "graphicspath":
            cursor = latex_skip_space(masked, match.end())
            outer = latex_braced(masked, cursor)
            if not outer:
                raise CliError(f"不支持动态 LaTeX graphicspath：{entry}")
            paths = latex_graphic_paths(
                text[match.start() : outer[1]],
                entry,
                root,
                paths,
            )
            parts.append(text[offset : outer[1]])
            offset = outer[1]
            continue
        if kind == "epub-fallback":
            path_start = latex_skip_space(masked, match.end())
            path_argument = latex_braced(masked, path_start)
            if not path_argument:
                raise CliError(f"LaTeX EPUB 回退缺少图像路径：{entry}")
            alt_start = latex_skip_space(masked, path_argument[1])
            alt_argument = latex_braced(masked, alt_start)
            content_start = (
                latex_skip_space(masked, alt_argument[1]) if alt_argument else -1
            )
            content_argument = (
                latex_braced(masked, content_start) if alt_argument else None
            )
            if not alt_argument or not content_argument:
                raise CliError(f"LaTeX EPUB 回退必须有三个参数：{entry}")
            raw_path = text[path_start + 1 : path_argument[1] - 1].strip()
            alt = text[alt_start + 1 : alt_argument[1] - 1].strip()
            if not alt or any(char in alt for char in "\\{}$#%"):
                raise CliError(f"LaTeX EPUB 回退替代文本必须是非空纯文本：{entry}")
            if Path(raw_path).suffix.lower() != ".svg":
                raise CliError(f"LaTeX EPUB 回退图像必须是 SVG：{entry}:{raw_path}")
            path_slice = (path_start + 1, path_argument[1] - 1)
            error_label = "LaTeX EPUB 回退图像"
        else:
            raw_path = text[match.start(1) : match.end(1)].strip()
            path_slice = (match.start(1), match.end(1))
            error_label = "LaTeX 图像"
        if not raw_path or any(char in raw_path for char in ("\\", "$", "#", "%")):
            raise CliError(f"不支持动态 {error_label}路径：{entry}:{raw_path}")
        relative = Path(raw_path)
        if relative.is_absolute():
            raise CliError(f"{error_label}路径必须是相对路径：{raw_path}")
        search_roots = (
            (entry.parent,) if relative.parent != Path(".") else (entry.parent, *paths)
        )
        candidates: list[Path] = []
        for search_root in search_roots:
            candidate = (search_root / relative).resolve(strict=False)
            candidates.extend(
                [candidate]
                if candidate.suffix
                else [candidate.with_suffix(extension) for extension in extensions]
            )
        resolved = next(
            (candidate for candidate in candidates if candidate.is_file()), None
        )
        if resolved is None:
            raise CliError(f"{error_label}不存在：{entry}:{raw_path}")
        try:
            normalized = resolved.relative_to(root.resolve()).as_posix()
        except ValueError as error:
            raise CliError(f"{error_label}越出工作目录：{raw_path}") from error
        if resolved.suffix.lower() == ".pdf":
            raise CliError(f"LaTeX EPUB 暂不支持 PDF 图像资产：{resolved}")
        parts.append(text[offset : path_slice[0]])
        parts.append(normalized)
        offset = path_slice[1]
    parts.append(text[offset:])
    return "".join(parts), paths


def normalize_latex_layout_environments(text: str) -> str:
    masked = mask_latex_comments(text)
    pattern = re.compile(
        r"\\(?P<kind>begin|end)\s*\{"
        r"(?P<name>flushleft|flushright|landscape|list|multicols|titlepage)\}"
    )
    replacements: list[tuple[int, int, str]] = []
    list_stack: list[str] = []
    for match in pattern.finditer(masked):
        kind = match.group("kind")
        name = match.group("name")
        end = match.end()
        replacement = ""
        if name == "multicols" and kind == "begin":
            argument = latex_braced(masked, latex_skip_space(masked, end))
            if not argument:
                raise CliError("LaTeX multicols 缺少栏数参数")
            _, end = argument
        elif name == "list":
            if kind == "begin":
                label = latex_braced(masked, latex_skip_space(masked, end))
                if not label:
                    raise CliError("LaTeX list 缺少标签参数")
                options = latex_braced(masked, latex_skip_space(masked, label[1]))
                if not options:
                    raise CliError("LaTeX list 缺少版式参数")
                end = options[1]
                environment = "enumerate" if label[0].strip() else "itemize"
                list_stack.append(environment)
                replacement = rf"\begin{{{environment}}}"
            else:
                if not list_stack:
                    raise CliError("LaTeX list 环境未配对")
                replacement = rf"\end{{{list_stack.pop()}}}"
        replacements.append((match.start(), end, replacement))
    if list_stack:
        raise CliError("LaTeX list 环境未闭合")
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text


def normalize_latex_split_footnotes(text: str) -> str:
    masked = mask_latex_comments(text)
    marker_pattern = re.compile(r"\\textsuperscript\s*\{\s*\\thefootnote\s*\}")
    note_pattern = re.compile(
        r"\\footnotetext\s*\[\s*\\value\s*\{\s*footnote\s*\}\s*\]"
    )
    markers = list(marker_pattern.finditer(masked))
    used: set[int] = set()
    replacements: list[tuple[int, int, str]] = []
    for note in note_pattern.finditer(masked):
        cursor = latex_skip_space(masked, note.end())
        body = latex_braced(masked, cursor)
        if not body:
            raise CliError("LaTeX footnotetext 缺少正文")
        marker = next(
            (
                candidate
                for candidate in reversed(markers)
                if candidate.start() < note.start() and candidate.start() not in used
            ),
            None,
        )
        if marker is None:
            raise CliError("LaTeX 动态分离脚注缺少对应的 thefootnote 标记")
        used.add(marker.start())
        replacements.append((marker.start(), marker.end(), rf"\footnote{{{body[0]}}}"))
        replacements.append((note.start(), body[1], ""))
    for start, end, replacement in reversed(sorted(replacements)):
        text = text[:start] + replacement + text[end:]
    return text


def normalize_latex_counter_formats(text: str) -> str:
    masked = mask_latex_comments(text)
    replacements: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\\renewcommand\*?", masked):
        target = latex_braced(masked, latex_skip_space(masked, match.end()))
        if not target:
            continue
        counter = re.fullmatch(
            r"\\the(figure|table|equation|section)", target[0].strip()
        )
        if not counter:
            continue
        format_value = latex_braced(masked, latex_skip_space(masked, target[1]))
        if not format_value:
            raise CliError(f"LaTeX {target[0]} 格式缺少正文")
        replacements.append(
            (
                match.start(),
                format_value[1],
                rf"\epubcounterformat{{{counter.group(1)}}}{{{format_value[0]}}}",
            )
        )
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text


def normalize_latex_source_page_lines(text: str) -> str:
    marker = re.compile(
        r"^[ \t]*(\\sourcepage\s*\{\s*\d+\s*\})[ \t]*%?[ \t]*(?:\r?\n)?$"
    )
    lines = text.splitlines(keepends=True)
    marker_indexes = {
        index for index, line in enumerate(lines) if marker.fullmatch(line)
    }
    index = 0
    while index < len(lines):
        if index not in marker_indexes:
            index += 1
            continue
        end = index
        markers: list[str] = []
        while end in marker_indexes:
            match = marker.fullmatch(lines[end])
            assert match is not None
            markers.append(match.group(1))
            end += 1
        previous = index - 1
        following = end
        if (
            previous >= 0
            and following < len(lines)
            and lines[previous].strip()
            and not lines[previous].lstrip().startswith("%")
            and lines[following].strip()
            and not lines[following].lstrip().startswith("%")
        ):
            previous_line = lines[previous]
            ending = ""
            if previous_line.endswith("\r\n"):
                ending, previous_line = "\r\n", previous_line[:-2]
            elif previous_line.endswith("\n"):
                ending, previous_line = "\n", previous_line[:-1]
            elif previous_line.endswith("\r"):
                ending, previous_line = "\r", previous_line[:-1]
            previous_line = previous_line.rstrip()
            if previous_line.endswith("%"):
                previous_line = previous_line[:-1].rstrip()
            right = lines[following].lstrip()
            cjk_boundary = bool(
                previous_line
                and right
                and re.search(r"[\u2e80-\u9fff]$", previous_line)
                and re.match(r"[\u2e80-\u9fff]", right)
            )
            lines[previous] = previous_line + "".join(markers) + (
                "" if cjk_boundary else ending
            )
            if cjk_boundary:
                lines[following] = right
            for marker_index in range(index, end):
                lines[marker_index] = ""
        index = end
    return "".join(lines)


def expand_typst(
    entry: Path,
    root: Path,
    *,
    stack: tuple[Path, ...] = (),
) -> str:
    entry = entry.resolve()
    if entry in stack:
        chain = " -> ".join(str(path) for path in (*stack, entry))
        raise CliError(f"Typst include 循环：{chain}")
    text = entry.read_text(encoding="utf-8")
    masked = mask_typst_comments(text)
    literals = list(LITERAL_INCLUDE_RE.finditer(masked))
    literal_starts = {match.start() for match in literals}
    for match in re.finditer(r"#include\b", masked):
        if match.start() not in literal_starts:
            raise CliError(f"不支持动态 Typst include：{entry}")

    parts: list[str] = []
    offset = 0
    for match in literals:
        parts.append(text[offset : match.start()])
        raw_path = match.group(1)
        if raw_path.startswith("/"):
            included = inside(
                root, raw_path.lstrip("/"), "Typst include", must_exist=True
            )
        else:
            included = (entry.parent / raw_path).resolve()
            try:
                included.relative_to(root.resolve())
            except ValueError as error:
                raise CliError(f"Typst include 越出仓库：{raw_path}") from error
            if not included.exists():
                raise CliError(f"Typst include 不存在：{included}")
        parts.append(expand_typst(included, root, stack=(*stack, entry)))
        offset = match.end()
    parts.append(text[offset:])
    return "".join(parts)


def _expand_latex(
    entry: Path,
    root: Path,
    *,
    stack: tuple[Path, ...] = (),
    graphic_paths: tuple[Path, ...] = (),
    normalize_graphics: bool = False,
) -> tuple[str, tuple[Path, ...]]:
    entry = entry.resolve()
    try:
        entry.relative_to(root.resolve())
    except ValueError as error:
        raise CliError(f"LaTeX include 越出工作目录：{entry}") from error
    if entry in stack:
        chain = " -> ".join(str(path) for path in (*stack, entry))
        raise CliError(f"LaTeX include 循环：{chain}")
    if entry.suffix.lower() != ".tex" or not entry.is_file():
        raise CliError(f"LaTeX include 必须是 .tex 文件：{entry}")
    text = entry.read_text(encoding="utf-8")
    masked = mask_latex_comments(text)
    includes = list(LATEX_INCLUDE_RE.finditer(masked))
    literal_starts = {match.start() for match in includes}
    for match in re.finditer(r"\\(?:input|include)\b", masked):
        if match.start() not in literal_starts:
            raise CliError(f"不支持动态 LaTeX include：{entry}")
    local_graphic_paths = graphic_paths
    parts: list[str] = []
    offset = 0
    for match in includes:
        segment = text[offset : match.start()]
        if normalize_graphics:
            segment, local_graphic_paths = normalize_latex_graphics(
                segment, entry, root, local_graphic_paths
            )
        else:
            local_graphic_paths = latex_graphic_paths(
                segment, entry, root, local_graphic_paths
            )
        parts.append(segment)
        raw_path = match.group(1).strip()
        included = entry.parent / raw_path
        if included.suffix == "":
            included = included.with_suffix(".tex")
        included = included.resolve()
        try:
            included.relative_to(root.resolve())
        except ValueError as error:
            raise CliError(f"LaTeX include 越出工作目录：{raw_path}") from error
        if not included.exists():
            raise CliError(f"LaTeX include 不存在：{included}")
        if included.suffix.lower() != ".tex" or not included.is_file():
            raise CliError(f"LaTeX include 必须是 .tex 文件：{included}")
        child, local_graphic_paths = _expand_latex(
            included,
            root,
            stack=(*stack, entry),
            graphic_paths=local_graphic_paths,
            normalize_graphics=normalize_graphics,
        )
        parts.append(child)
        offset = match.end()
    tail = text[offset:]
    if normalize_graphics:
        tail, local_graphic_paths = normalize_latex_graphics(
            tail, entry, root, local_graphic_paths
        )
    else:
        local_graphic_paths = latex_graphic_paths(
            tail, entry, root, local_graphic_paths
        )
    parts.append(tail)
    return "".join(parts), local_graphic_paths


def expand_latex(
    entry: Path,
    root: Path,
    *,
    stack: tuple[Path, ...] = (),
    graphic_paths: tuple[Path, ...] = (),
    normalize_graphics: bool = False,
) -> str:
    text, _ = _expand_latex(
        entry,
        root,
        stack=stack,
        graphic_paths=graphic_paths,
        normalize_graphics=normalize_graphics,
    )
    return text


def typst_heading_structure(text: str) -> list[int]:
    events: list[tuple[int, int]] = []
    events.extend(
        (match.start(), len(match.group(1)))
        for match in re.finditer(r"(?m)^\s*(=+)\s+\S", text)
    )
    events.extend(
        (match.start(), 1)
        for match in re.finditer(r"#(?:chapter|index)-title\s*\(", text)
    )
    return [level for _, level in sorted(events)]


def validate_markdown_numbered_headings(text: str, label: str) -> None:
    headings: list[tuple[int, int, str, int, int]] = []
    scope = 0
    fence: tuple[str, int] | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if (
                fence_match
                and fence_match.group(1)[0] == fence[0]
                and len(fence_match.group(1)) >= fence[1]
            ):
                fence = None
            continue
        if fence_match:
            fence = (fence_match.group(1)[0], len(fence_match.group(1)))
            continue
        match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        if level == 1:
            scope += 1
        title = re.sub(r"\s+#+\s*$", "", match.group(2))
        title = re.sub(r"\s+\{[^{}]*\}\s*$", "", title)
        title = title.lstrip("*_")
        number = re.match(r"((?:\d+|[A-Z])(?:\.\d+)+)(?=\s|$)", title)
        if number:
            value = number.group(1)
            headings.append((scope, line_number, value, len(value.split(".")), level))

    errors: list[tuple[int, str]] = []
    seen: dict[tuple[int, str], int] = {}
    for heading_scope, line_number, number, _, _ in headings:
        key = (heading_scope, number)
        if key in seen:
            errors.append(
                (
                    line_number,
                    f"{label} 第 {line_number} 行编号标题 {number} "
                    f"与第 {seen[key]} 行重复",
                )
            )
        else:
            seen[key] = line_number

    by_scope: dict[int, list[tuple[int, str, int, int]]] = {}
    for heading_scope, line_number, number, depth, level in headings:
        by_scope.setdefault(heading_scope, []).append(
            (line_number, number, depth, level)
        )
    for scoped in by_scope.values():
        shallowest = min(depth for _, _, depth, _ in scoped)
        offsets = [
            level - depth
            for _, _, depth, level in scoped
            if depth == shallowest
        ]
        offset = min(statistics.multimode(offsets))
        for line_number, number, depth, level in scoped:
            expected = depth + offset
            if level != expected:
                errors.append(
                    (
                        line_number,
                        f"{label} 第 {line_number} 行编号标题 {number} 使用 {level} 级，"
                        f"当前一级标题范围要求 {expected} 级",
                    )
                )
    if errors:
        raise CliError("\n".join(message for _, message in sorted(errors)))


def markdown_conflict_marker_lines(text: str) -> list[int]:
    """Return Git conflict markers that occur outside fenced code examples."""
    lines: list[int] = []
    fence: tuple[str, int] | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if (
                fence_match
                and fence_match.group(1)[0] == fence[0]
                and len(fence_match.group(1)) >= fence[1]
            ):
                fence = None
            continue
        if fence_match:
            fence = (fence_match.group(1)[0], len(fence_match.group(1)))
            continue
        if re.match(r"^(?:<{7,}|>{7,})(?:[ \t].*)?$", line):
            lines.append(line_number)
    return lines


class BookHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_html_start = False
        self.has_html_end = False
        self.source_pages: list[int] = []
        self.source_units: list[int] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "html":
            self.has_html_start = True
        if tag != "span":
            return
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if "source-page" in classes:
            page = values.get("data-page")
            if page is None or not page.isdigit():
                raise CliError("HTML source-page 的 data-page 必须是十进制整数")
            self.source_pages.append(int(page))
        if "source-unit" in classes:
            unit = values.get("data-unit")
            if unit is None or not unit.isdigit():
                raise CliError("HTML source-unit 的 data-unit 必须是十进制整数")
            self.source_units.append(int(unit))

    def handle_endtag(self, tag: str) -> None:
        if tag == "html":
            self.has_html_end = True


def source_pages_for(format_name: str, text: str) -> list[int]:
    if format_name == "html":
        parser = BookHTMLParser()
        parser.feed(text)
        parser.close()
        return parser.source_pages
    patterns = {
        "typst": SOURCE_PAGE_RE,
        "markdown": MARKDOWN_SOURCE_PAGE_RE,
        "latex": LATEX_SOURCE_PAGE_RE,
    }
    return [int(value) for value in patterns[format_name].findall(text)]


def source_units_for(format_name: str, text: str) -> list[int]:
    if format_name == "html":
        parser = BookHTMLParser()
        parser.feed(text)
        parser.close()
        return parser.source_units
    if format_name == "markdown":
        return [int(value) for value in MARKDOWN_SOURCE_UNIT_RE.findall(text)]
    return []


def validate_source_page_sequence(
    pages: list[int],
    label: str,
    expected_count: int | None = None,
    *,
    expected_start: int = 1,
) -> None:
    if pages != sorted(set(pages)):
        raise CliError(f"{label} 的源页标记必须严格升序且不得重复")
    if expected_count is not None and pages != list(range(1, expected_count + 1)):
        expected = list(range(expected_start, expected_count + 1))
        if pages != expected:
            raise CliError(
                f"{label} 的源页标记必须覆盖 {expected_start}--{expected_count}"
            )


def validate_source_unit_sequence(
    units: list[int],
    label: str,
    expected: list[int],
) -> None:
    if units != expected:
        raise CliError(f"{label} 的 source-unit 标记必须严格覆盖 1--{len(expected)}")


def validate_page_map(work: Work) -> None:
    path = work.path / "page-map.tsv"
    if not path.is_file():
        raise CliError(f"缺少 page-map.tsv：{path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != PAGE_MAP_HEADER:
            raise CliError("page-map.tsv 表头不符合项目契约")
        pages: list[int] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                pages.append(int(row["pdf_page"]))
            except (TypeError, ValueError) as error:
                raise CliError(
                    f"page-map.tsv 第 {row_number} 行 pdf_page 无效"
                ) from error
    expected = list(range(1, work.source_pages + 1))
    if pages != expected:
        raise CliError("page-map.tsv 的 pdf_page 必须从 1 到来源总页数连续且唯一")


def validate_glossary(path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != GLOSSARY_HEADER:
            raise CliError(f"术语表表头无效：{path}")
        seen_sources: dict[str, tuple[str, int]] = {}
        for row in reader:
            row_number = reader.line_num
            if row["kind"] not in GLOSSARY_KINDS:
                raise CliError(f"{path} 第 {row_number} 行 kind 无效：{row['kind']}")
            if not row["source"] or not row["target"]:
                raise CliError(f"{path} 第 {row_number} 行 source/target 不能为空")
            previous = seen_sources.setdefault(
                row["source"], (row["target"], row_number)
            )
            if previous[0] != row["target"]:
                raise CliError(
                    f'{path} 第 {row_number} 行 source "{row["source"]}" '
                    f'与第 {previous[1]} 行 target 冲突："{previous[0]}" != "{row["target"]}"'
                )


def check_typst_pairs(work: Work) -> list[str]:
    messages: list[str] = []
    source_language = next(
        language for language in work.languages if language.role == "source"
    )
    source_text = mask_typst_comments(expand_typst(source_language.entry, work.root))
    messages.append(
        f"{source_language.code}: {len(SOURCE_PAGE_RE.findall(source_text))} 个源页标记"
    )

    source_dir = source_language.entry.parent
    source_files = {
        path.relative_to(source_dir): path
        for path in source_dir.rglob("*.typ")
        if path.resolve() != source_language.entry.resolve()
    }
    for language in work.languages:
        if language.role != "translation":
            continue
        translated_text = mask_typst_comments(expand_typst(language.entry, work.root))
        messages.append(
            f"{language.code}: {len(SOURCE_PAGE_RE.findall(translated_text))} 个源页标记"
        )
        translated_dir = language.entry.parent
        translated_files = {
            path.relative_to(translated_dir): path
            for path in translated_dir.rglob("*.typ")
            if path.resolve() != language.entry.resolve()
        }
        for relative in sorted(source_files.keys() & translated_files.keys()):
            source_piece = mask_typst_comments(
                source_files[relative].read_text(encoding="utf-8")
            )
            translated_piece = mask_typst_comments(
                translated_files[relative].read_text(encoding="utf-8")
            )
            if source_pages_for("typst", source_piece) != source_pages_for(
                "typst", translated_piece
            ):
                raise CliError(f"双语源页顺序不同：{relative}")
            if typst_heading_structure(source_piece) != typst_heading_structure(
                translated_piece
            ):
                raise CliError(f"双语标题层级不同：{relative}")
        unmatched = sorted(set(source_files) ^ set(translated_files))
        if unmatched:
            messages.append(
                f"{language.code}: {len(unmatched)} 个未配对 Typst 文件（按范围跳过）"
            )
    return messages


def command_check(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    validate_manifest_status_alignment(work)
    validate_tool_truncation_entries(work)
    actual_hash = sha256(work.source)
    expected_hash = str(work.manifest["source"]["sha256"]).upper()
    if actual_hash != expected_hash:
        raise CliError(f"来源 SHA-256 已变化：{actual_hash}")
    if work.source.suffix.lower() == ".pdf":
        actual_pages, _ = mutool_pages(work.source)
        if actual_pages != work.source_pages:
            raise CliError(
                f"来源页数为 {actual_pages}，manifest 记录为 {work.source_pages}"
            )
    elif work.source_kind == "epub-units":
        validate_epub_source_units(work.source, work.source_unit_rows)
    else:
        actual_anchors, _ = epub_page_anchors(work.source)
        source_data = work.manifest["source"]
        expected_anchor_count = source_data["anchor_pages"]
        if len(actual_anchors) != expected_anchor_count:
            raise CliError(
                f"EPUB 页面锚点为 {len(actual_anchors)}，manifest 记录为 {expected_anchor_count}"
            )
        expected_anchor_set = source_data.get("anchor_set")
        if expected_anchor_set and actual_anchors != expand_page_spec(
            expected_anchor_set
        ):
            raise CliError(
                "EPUB 页面锚点集合不符合 manifest："
                f"{compress_page_spec(actual_anchors)} != {expected_anchor_set}"
            )
    if work.source.suffix.lower() == ".pdf":
        validate_page_map(work)
    for language in work.languages:
        if language.glossary:
            validate_glossary(language.glossary)

    messages: list[str] = []
    image_message = validate_source_pdf_images(work)
    if image_message:
        messages.append(image_message)
    if work.authoring_format == "typst":
        messages.extend(check_typst_pairs(work))
    else:
        source_language = next(
            language for language in work.languages if language.role == "source"
        )
        if work.authoring_format == "latex":
            source_text = expand_latex(source_language.entry, work.path)
        else:
            source_text = source_language.entry.read_text(encoding="utf-8")
        if work.authoring_format == "markdown":
            validate_markdown_numbered_headings(source_text, source_language.code)
            messages.extend(
                validate_markdown_structure(work, source_language, source_text)
            )
            spacing_candidates = markdown_source_page_spacing_candidates(source_text)
            if spacing_candidates:
                messages.append(
                    f"{source_language.code}: {len(spacing_candidates)} 个行内源页空格候选"
                )
        marker_reader = (
            source_units_for if work.source_kind == "epub-units" else source_pages_for
        )
        source_markers = marker_reader(work.authoring_format, source_text)
        for language in work.languages:
            if language.role == "translation":
                if work.authoring_format == "latex":
                    translated_text = expand_latex(language.entry, work.path)
                else:
                    translated_text = language.entry.read_text(encoding="utf-8")
                if work.authoring_format == "markdown":
                    validate_markdown_numbered_headings(
                        translated_text, language.code
                    )
                    messages.extend(
                        validate_markdown_structure(work, language, translated_text)
                    )
                    spacing_candidates = markdown_source_page_spacing_candidates(
                        translated_text
                    )
                    if spacing_candidates:
                        messages.append(
                            f"{language.code}: {len(spacing_candidates)} 个行内源页空格候选"
                        )
                markers = marker_reader(work.authoring_format, translated_text)
                if markers != source_markers:
                    raise CliError(f"双语源页顺序不同：{language.code}")
        if work.source_kind == "epub-units":
            validate_source_unit_sequence(
                source_markers,
                source_language.code,
                [int(row["unit_id"]) for row in work.source_unit_rows],
            )
            messages.append(
                f"{source_language.code}: {len(source_markers)} 个逻辑源单元标记"
            )
        else:
            if work.source.suffix.lower() == ".epub":
                expected_markers = expand_page_spec(
                    str(work.manifest["source"].get("anchor_set", ""))
                )
                if source_markers != expected_markers:
                    raise CliError(
                        f"{source_language.code} 的源页标记必须匹配 EPUB 锚点集合"
                    )
            else:
                validate_source_page_sequence(
                    source_markers,
                    source_language.code,
                    work.source_pages,
                    expected_start=source_markers[0] if source_markers else 1,
                )
            messages.append(f"{source_language.code}: {len(source_markers)} 个源页标记")

    if work.authoring_format == "latex":
        for language in work.languages:
            if "epub" not in language.outputs:
                continue
            preflight_latex_epub(work, language)
            messages.append(f"{language.code}: LaTeX EPUB 兼容性预检通过")

    source_label = (
        "source units" if work.source_kind == "epub-units" else "source pages"
    )
    print(
        f"check: ok ({work.work_id}, {work.authoring_format}, {work.source_pages} {source_label})"
    )
    for message in messages:
        print(f" - {message}")
    return 0


def _source_marker_locations(
    text: str,
    format_name: str,
    path: Path,
    *,
    start: int = 0,
    end: int | None = None,
) -> list[dict[str, Any]]:
    patterns = {
        "markdown": MARKDOWN_SOURCE_PAGE_RE,
        "latex": LATEX_SOURCE_PAGE_RE,
        "typst": SOURCE_PAGE_RE,
    }
    pattern = patterns[format_name]
    locations: list[dict[str, Any]] = []
    matches = pattern.finditer(text, start) if end is None else pattern.finditer(text, start, end)
    for match in matches:
        line = text.count("\n", 0, match.start()) + 1
        line_start = text.rfind("\n", 0, match.start()) + 1
        locations.append(
            {
                "path": path.as_posix(),
                "line": line,
                "column": match.start() - line_start + 1,
                "page_marker": int(match.group(1)),
            }
        )
    return locations


def _latex_marker_locations(entry: Path, root: Path) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []

    def visit(path: Path, stack: tuple[Path, ...]) -> None:
        path = path.resolve()
        if path in stack:
            return
        text = path.read_text(encoding="utf-8")
        masked = mask_latex_comments(text)
        includes = list(LATEX_INCLUDE_RE.finditer(masked))
        offset = 0
        for match in includes:
            locations.extend(
                _source_marker_locations(
                    masked,
                    "latex",
                    path.relative_to(root),
                    start=offset,
                    end=match.start(),
                )
            )
            child = path.parent / match.group(1).strip()
            if child.suffix == "":
                child = child.with_suffix(".tex")
            if child.is_file():
                visit(child, (*stack, path))
            offset = match.end()
        locations.extend(
            _source_marker_locations(
                masked,
                "latex",
                path.relative_to(root),
                start=offset,
            )
        )

    visit(entry, ())
    return locations


def _typst_marker_locations(entry: Path, root: Path) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []

    def visit(path: Path, stack: tuple[Path, ...]) -> None:
        path = path.resolve()
        if path in stack:
            return
        text = path.read_text(encoding="utf-8")
        masked = mask_typst_comments(text)
        includes = list(LITERAL_INCLUDE_RE.finditer(masked))
        offset = 0
        for match in includes:
            locations.extend(
                _source_marker_locations(
                    masked,
                    "typst",
                    path.relative_to(root),
                    start=offset,
                    end=match.start(),
                )
            )
            raw_path = match.group(1)
            child = (
                inside(root, raw_path.lstrip("/"), "Typst include", must_exist=False)
                if raw_path.startswith("/")
                else (path.parent / raw_path).resolve()
            )
            if child.is_file():
                visit(child, (*stack, path))
            offset = match.end()
        locations.extend(
            _source_marker_locations(
                masked,
                "typst",
                path.relative_to(root),
                start=offset,
            )
        )

    visit(entry, ())
    return locations


def _boundary_source(work: Work, language: Language) -> tuple[str, list[dict[str, Any]]]:
    format_name = work.authoring_format
    if format_name == "latex":
        text = expand_latex(language.entry, work.path)
        locations = _latex_marker_locations(language.entry, work.path)
    elif format_name == "typst":
        text = expand_typst(language.entry, work.root)
        locations = _typst_marker_locations(language.entry, work.root)
    elif format_name == "markdown":
        text = language.entry.read_text(encoding="utf-8")
        locations = _source_marker_locations(text, format_name, language.entry.relative_to(work.path))
    else:
        raise CliError("boundary-audit 目前只支持 Markdown、LaTeX 和 Typst")
    return text, locations


def _pandoc_boundary_ast(work: Work, text: str, format_name: str) -> dict[str, Any]:
    reader = {
        "markdown": "markdown+fenced_divs+bracketed_spans+footnotes-raw_html",
        "latex": "latex+raw_tex",
    }.get(format_name)
    if reader is None:
        raise CliError(f"{format_name} 没有 Pandoc boundary-audit reader")
    if format_name == "latex":
        # translator.sty intentionally defines sourcepage as empty; use an
        # otherwise-unknown token so Pandoc preserves each marker in its AST.
        text = LATEX_SOURCE_PAGE_RE.sub(
            lambda match: rf"\boundarypage{{{match.group(1)}}}", text
        )
    process_env = os.environ.copy()
    process_temp = str(temp_root())
    process_env["TEMP"] = process_temp
    process_env["TMP"] = process_temp
    try:
        process = subprocess.run(
            ["pandoc", "--from", reader, "--to", "json"],
            input=text,
            cwd=work.root,
            env=process_env,
            text=True,
            capture_output=True,
            errors="replace",
        )
    except FileNotFoundError as error:
        raise CliError("找不到命令：pandoc") from error
    if process.returncode:
        detail = (process.stderr or process.stdout).strip()
        raise CliError(f"Pandoc boundary-audit 失败：{detail[-4000:]}")
    try:
        document = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise CliError("Pandoc boundary-audit 未返回有效 JSON") from error
    if not isinstance(document, dict):
        raise CliError("Pandoc boundary-audit JSON 根节点无效")
    return document


def _normalize_caption_text(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"[。．.!！?？]+$", "", normalized)


def _pandoc_source_marker_class(node: Any) -> str | None:
    if not isinstance(node, dict) or node.get("t") != "Span":
        return None
    content = node.get("c")
    if not isinstance(content, list) or len(content) < 1:
        return None
    attributes = content[0]
    if not isinstance(attributes, list) or len(attributes) < 2:
        return None
    classes = attributes[1]
    if not isinstance(classes, list):
        return None
    for marker_class in ("source-page", "source-unit"):
        if marker_class in classes:
            return marker_class
    return None


def _pandoc_list_marker_fragments(document: Any) -> list[str]:
    fragments: list[str] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            if isinstance(node, list):
                for value in node:
                    visit(value)
            return
        if node.get("t") in {"Para", "Plain"} and isinstance(node.get("c"), list):
            marker_seen = False
            line_break_seen = False
            for inline in node["c"]:
                if _pandoc_source_marker_class(inline):
                    marker_seen = True
                    line_break_seen = False
                    continue
                if not marker_seen:
                    continue
                kind = inline.get("t") if isinstance(inline, dict) else None
                if kind in {"SoftBreak", "LineBreak"}:
                    line_break_seen = True
                    continue
                if kind == "Space":
                    continue
                if (
                    line_break_seen
                    and kind == "Str"
                    and (
                        str(inline.get("c") or "") in {"-", "*", "+"}
                        or re.fullmatch(r"\d+[.)]", str(inline.get("c") or ""))
                    )
                ):
                    fragments.append("列表标记位于源页标记后的普通段落")
                    break
                line_break_seen = False
        for value in node.values():
            visit(value)

    visit(document)
    return fragments


def markdown_source_page_spacing_candidates(text: str) -> list[int]:
    """Return lines where CJK text is separated by a source-page marker space."""
    candidates: list[int] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for marker in MARKDOWN_SOURCE_PAGE_MARKER_RE.finditer(line):
            before = line[: marker.start()]
            after = line[marker.end() :]
            left = before.rstrip(" \t")[-1:]
            right = after.lstrip(" \t")[:1]
            if not (
                CJK_BOUNDARY_CHAR_RE.fullmatch(left)
                and CJK_BOUNDARY_CHAR_RE.fullmatch(right)
            ):
                continue
            if before != before.rstrip(" \t") or after != after.lstrip(" \t"):
                candidates.append(line_number)
                break
    return candidates


def markdown_figure_traceability_problems(text: str) -> list[str]:
    """Check figure numbers already declared by explicit Markdown captions."""
    lines = text.splitlines()
    problems: list[str] = []
    fence: tuple[str, int] | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if (
                fence_match
                and fence_match.group(1)[0] == fence[0]
                and len(fence_match.group(1)) >= fence[1]
            ):
                fence = None
            index += 1
            continue
        if fence_match:
            fence = (fence_match.group(1)[0], len(fence_match.group(1)))
            index += 1
            continue
        image = MARKDOWN_STANDALONE_IMAGE_RE.match(line)
        if not image:
            index += 1
            continue

        images = [(index + 1, image)]
        cursor = index + 1
        while cursor < len(lines):
            while cursor < len(lines) and not lines[cursor].strip():
                cursor += 1
            if cursor >= len(lines) or re.match(
                r"^ {0,3}(`{3,}|~{3,})", lines[cursor]
            ):
                break
            following_image = MARKDOWN_STANDALONE_IMAGE_RE.match(lines[cursor])
            if not following_image:
                break
            images.append((cursor + 1, following_image))
            cursor += 1

        caption = (
            MARKDOWN_NUMBERED_FIGURE_CAPTION_RE.match(
                lines[cursor].lstrip(" \t*_")
            )
            if cursor < len(lines)
            else None
        )
        numbers = (
            MARKDOWN_FIGURE_NUMBER_RE.findall(caption.group("numbers"))
            if caption
            else []
        )
        if caption and len(numbers) != len(images):
            problems.append(
                f"第 {images[0][0]} 行编号图片组有 "
                f"{len(images)} 幅图片、{len(numbers)} 个图号"
            )
        elif numbers:
            for (line_number, item), number in zip(images, numbers, strict=True):
                alt = item.group("alt")
                attrs = item.group("attrs") or ""
                missing: list[str] = []
                if not re.search(
                    rf"(?:图|Figures?|Figs?)\.?[ \t]*{re.escape(number)}"
                    rf"(?=$|[^\w.])",
                    alt,
                    re.IGNORECASE,
                ):
                    missing.append(f"原图号 {number}")
                if not re.search(r"#fig-[A-Za-z0-9][A-Za-z0-9._:-]*", attrs):
                    missing.append("#fig- 标识")
                if missing:
                    problems.append(
                        f"第 {line_number} 行图片缺少{'、'.join(missing)}"
                    )
        index = cursor + 1 if caption else index + 1
    return problems


def validate_markdown_structure(
    work: Work,
    language: Language,
    text: str,
) -> list[str]:
    messages: list[str] = []
    conflict_lines = markdown_conflict_marker_lines(text)
    if conflict_lines:
        locations = "、".join(str(line) for line in conflict_lines[:8])
        if len(conflict_lines) > 8:
            locations += "……"
        raise CliError(
            f"{language.code} Markdown 代码围栏外存在 Git 冲突标记：第 {locations} 行"
        )
    figure_problems = markdown_figure_traceability_problems(text)
    if figure_problems:
        if work.manifest.get("work", {}).get("status") == "complete":
            messages.append(
                f"{language.code}: {len(figure_problems)} 幅既有编号图追溯候选"
            )
        else:
            details = "；".join(figure_problems[:8])
            if len(figure_problems) > 8:
                details += "……"
            raise CliError(
                f"{language.code} Markdown 编号图追溯不完整：{details}"
            )
    document = _pandoc_boundary_ast(work, text, "markdown")
    fragments = _pandoc_list_marker_fragments(document)
    if fragments:
        raise CliError(
            f"{language.code} Markdown 分页标记破坏列表结构："
            "列表项目被解析成普通段落"
        )
    return messages


def _write_boundary_report(
    evidence: Path,
    *,
    work: Work,
    language: Language,
    format_name: str,
    marker_count: int,
    parsed_marker_count: int,
    findings: list[dict[str, Any]],
    include_info: bool,
) -> dict[str, int]:
    summary = page_boundary_audit.summarize_findings(findings)
    report = {
        "schema_version": 1,
        "kind": "page-boundary-audit",
        "work": work.work_id,
        "language": language.code,
        "format": format_name,
        "marker_count": marker_count,
        "parsed_marker_count": parsed_marker_count,
        "marker_mismatch": parsed_marker_count != marker_count,
        "summary": summary,
        "findings": findings,
    }
    (evidence / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = (
        "severity",
        "page",
        "next_page",
        "rule",
        "context",
        "path",
        "line",
        "column",
        "left_class",
        "right_class",
        "left_text",
        "right_text",
    )
    with (evidence / "review.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for finding in findings:
            if finding["severity"] == "info" and not include_info:
                continue
            location = finding.get("location") or {}
            writer.writerow(
                {
                    "severity": finding["severity"],
                    "page": finding.get("page") or "",
                    "next_page": finding.get("next_page") or "",
                    "rule": finding["rule"],
                    "context": finding["context"],
                    "path": location.get("path", ""),
                    "line": location.get("line", ""),
                    "column": location.get("column", ""),
                    "left_class": finding.get("left_class", ""),
                    "right_class": finding.get("right_class", ""),
                    "left_text": finding.get("left_text", ""),
                    "right_text": finding.get("right_text", ""),
                }
            )
    (evidence / "summary.txt").write_text(
        "\n".join(
            [
                f"work={work.work_id}",
                f"language={language.code}",
                f"format={format_name}",
                f"marker_count={marker_count}",
                f"parsed_marker_count={parsed_marker_count}",
                f"marker_mismatch={parsed_marker_count != marker_count}",
                f"high={summary['high']}",
                f"review={summary['review']}",
                f"info={summary['info']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def command_boundary_audit(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    if work.authoring_format == "html":
        raise CliError("boundary-audit 目前不支持 HTML 底稿")
    if args.pages and not PAGES_RE.fullmatch(args.pages):
        raise CliError("--pages 必须类似 23、23-24 或 1,3-5")
    selected_pages = set(expand_page_spec(args.pages)) if args.pages else None
    languages = (
        work.languages
        if args.lang == "all"
        else tuple(language for language in work.languages if language.code == args.lang)
    )
    if not languages:
        raise CliError(f"找不到语言：{args.lang}")
    root = reset_evidence_dir(work, "boundary-audit")
    for language in languages:
        text, locations = _boundary_source(work, language)
        if work.authoring_format == "typst":
            parsed_marker_count = len(locations)
            findings = page_boundary_audit.lexical_typst_findings(
                text,
                locations=locations,
            )
        else:
            document = _pandoc_boundary_ast(work, text, work.authoring_format)
            parsed_marker_count = page_boundary_audit.count_markers(
                document, work.authoring_format
            )
            findings = page_boundary_audit.analyze_document(
                document,
                format_name=work.authoring_format,
                locations=locations,
            )
        if selected_pages is not None:
            findings = [
                finding
                for finding in findings
                if finding.get("page") in selected_pages
                or finding.get("next_page") in selected_pages
            ]
        evidence = root / language.code
        evidence.mkdir()
        summary = _write_boundary_report(
            evidence,
            work=work,
            language=language,
            format_name=work.authoring_format,
            marker_count=len(locations),
            parsed_marker_count=parsed_marker_count,
            findings=findings,
            include_info=args.include_info,
        )
        print(
            f"boundary-audit: {language.code} markers={len(locations)} "
            f"parsed={parsed_marker_count} "
            f"high={summary['high']} review={summary['review']} info={summary['info']} "
            f"-> {evidence}"
        )
    return 0


def image_tool() -> list[str] | None:
    if shutil.which("magick"):
        return ["magick", "montage"]
    if shutil.which("montage"):
        return ["montage"]
    return None


def required_tools(work: Work, purpose: str) -> list[tuple[str, bool]]:
    tools: dict[str, bool] = {}
    has_epub = any("epub" in language.outputs for language in work.languages)
    has_pdf = any("pdf" in language.outputs for language in work.languages)

    def need(name: str, available: bool | None = None) -> None:
        tools[name] = bool(shutil.which(name)) if available is None else available

    if purpose in {"all", "check", "qa"} and work.source.suffix.lower() == ".pdf":
        need("mutool")
        if source_image_audit_enabled(work):
            need("pdfimages")
    if purpose in {"all", "check"} and work.authoring_format == "markdown":
        need("pandoc")
    if purpose in {"all", "build"}:
        if has_epub:
            need("uv")
            need("latex")
            need("dvisvgm")
            tools["GladTeX filter"] = (
                work.root / "tools" / "gladtex_filter.py"
            ).is_file()
        if work.authoring_format == "typst":
            need("typst")
            if any("epub" in language.outputs for language in work.languages):
                need("pandoc")
        elif work.authoring_format == "markdown":
            need("pandoc")
            if any("pdf" in language.outputs for language in work.languages):
                need("typst")
        elif work.authoring_format == "latex":
            if has_pdf:
                need("latexmk")
                need("xelatex")
            if has_epub:
                need("pandoc")
                if epub_cover_path(work) is None:
                    need("mutool")
    if purpose in {"all", "qa"} and has_pdf:
        tools["ImageMagick"] = image_tool() is not None
    if purpose in {"all", "qa"} and has_epub:
        need("java")
        tools["EPUBCheck"] = EPUBCHECK_JAR.is_file()
    if purpose in {"all", "browser-qa"} and has_epub:
        browser = os.environ.get("TRANSLATOR_AGENT_BROWSER", "agent-browser")
        need("agent-browser", shutil.which(browser) is not None)
    return sorted(tools.items())


def command_doctor(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    missing = []
    for name, available in required_tools(work, args.purpose):
        print(f"{'ok' if available else 'missing'}: {name}")
        if not available:
            missing.append(name)
    document_errors: tuple[str, ...] = ()
    if args.purpose == "all":
        document_errors = doc_entrypoint_errors(work.root)
        for error in document_errors:
            print(f"error: {error}")
    failures = []
    if document_errors:
        failures.append("权威文档存在本地 Python 入口漂移")
    if missing:
        failures.append("缺少工具：" + ", ".join(missing))
    if failures:
        raise CliError("；".join(failures))
    return 0


def temp_root(root: Path = REPO_ROOT) -> Path:
    expected = root.resolve() / ".tmp" / "translator"
    target = expected.resolve()
    if target != expected:
        raise CliError(f"拒绝使用重定向的临时根目录：{target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def workflow_runs_dir(root: Path, *, create: bool = False) -> Path:
    root = root.resolve()
    target = (root / ".local" / "translator" / "runs").resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise CliError(f"拒绝使用越界的流程日志目录：{target}") from error
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def workflow_open_dir(root: Path, *, create: bool = False) -> Path:
    root = root.resolve()
    target = (root / ".local" / "translator" / "open").resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise CliError(f"拒绝使用越界的工站目录：{target}") from error
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def workflow_state_dir(root: Path, *, create: bool = False) -> Path:
    root = root.resolve()
    target = (root / ".local" / "translator" / "state").resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise CliError(f"拒绝使用越界的工站状态目录：{target}") from error
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def workflow_run_id() -> str:
    return f"{time.time_ns()}-{os.getpid()}-{os.urandom(8).hex()}"


def write_workflow_log(record: dict[str, Any], root: Path) -> Path:
    # ponytail: one small file per run; add retention only if growth becomes measurable.
    directory = workflow_runs_dir(root, create=True)
    run_id = str(record.get("run_id") or workflow_run_id())
    record.setdefault("run_id", run_id)
    target = directory / f"{run_id}.json"
    stage = directory / f".{run_id}.tmp"
    try:
        stage.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return target


def parse_workflow_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def snapshot_path_allowed(path: str) -> bool:
    pure = Path(path.replace("/", os.sep))
    return not any(part in SNAPSHOT_EXCLUDED_PARTS for part in pure.parts)


def workflow_dirty_snapshot(root: Path) -> dict[str, dict[str, object]]:
    """Return dirty Git paths and digests without retaining file content."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CliError(f"无法取得 Git 工站快照：{type(error).__name__}") from error
    if result.returncode:
        raise CliError("无法取得 Git 工站快照")
    entries = result.stdout.split(b"\0")
    snapshot: dict[str, dict[str, object]] = {}
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            continue
        status = entry[:2].decode("ascii", "replace")
        raw_path = entry[3:].decode("utf-8", "surrogateescape")
        if ("R" in status or "C" in status) and index < len(entries):
            index += 1
        relative = raw_path.replace("\\", "/")
        if not snapshot_path_allowed(relative):
            continue
        candidate = root / Path(relative)
        item: dict[str, object] = {"path": relative, "status": status}
        if candidate.is_file():
            try:
                item["state"] = "present"
                item["bytes"] = candidate.stat().st_size
                item["sha256"] = sha256(candidate)
            except OSError:
                continue
        else:
            item["state"] = "deleted"
            item["bytes"] = None
            item["sha256"] = None
        snapshot[relative] = item
    return snapshot


def snapshot_changes(
    before: dict[str, dict[str, object]], after: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        if new is not None and new.get("state") == "deleted":
            change = "deleted"
        elif old is None:
            change = "added"
        elif new is None:
            change = "deleted" if old.get("status") == "??" else "restored"
        else:
            change = "modified"
        changes.append(
            {
                "path": path,
                "change": change,
                "status": new.get("status") if new else None,
                "bytes": new.get("bytes") if new else None,
                "sha256": new.get("sha256") if new else None,
            }
        )
    return changes


def station_lane(args: argparse.Namespace, root: Path) -> dict[str, str]:
    session_value = getattr(args, "session", None) or os.environ.get("CODEX_THREAD_ID")
    actor_value = getattr(args, "actor", None) or os.environ.get("CODEX_ACTOR_ID")
    try:
        session = station_label(session_value) if session_value else "unknown"
        actor = station_label(actor_value) if actor_value else "unknown"
    except argparse.ArgumentTypeError as error:
        raise CliError("环境中的工站身份标识无效") from error
    selector = Path(str(getattr(args, "work", "")))
    resolved = (selector if selector.is_absolute() else root / selector).resolve(
        strict=False
    )
    return {
        "work": selector.name,
        "work_key": hashlib.sha256(path_key(resolved).encode("utf-8")).hexdigest()[:16],
        "actor": actor,
        "session": session,
    }


def workflow_lane_state_path(
    root: Path, lane: dict[str, str], *, create: bool = False
) -> Path:
    key = hashlib.sha256(
        json.dumps(lane, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return workflow_state_dir(root, create=create) / f"{key}.json"


def read_workflow_lane_state(
    root: Path, lane: dict[str, str]
) -> dict[str, Any] | None:
    path = workflow_lane_state_path(root, lane)
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliError(f"无法读取工站 lane 状态：{type(error).__name__}") from error
    if (
        not isinstance(state, dict)
        or state.get("kind") != "workflow-lane-state"
        or state.get("lane") != lane
        or not isinstance(state.get("exit_snapshot"), dict)
    ):
        raise CliError("工站 lane 状态无效")
    return state


def write_workflow_lane_state(
    root: Path,
    context: dict[str, Any],
    record: dict[str, Any],
) -> None:
    lane = context["station"]["lane"]
    target = workflow_lane_state_path(root, lane, create=True)
    stage = target.with_name(f".{target.name}.tmp")
    state = {
        "schema_version": WORKFLOW_LOG_SCHEMA,
        "kind": "workflow-lane-state",
        "lane": lane,
        "run_id": record["run_id"],
        "ended_at": record["ended_at"],
        "command": record["command"],
        "status": record["status"],
        "revision": record["revision"],
        "exit_snapshot": context["exit_snapshot"],
    }
    try:
        stage.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)


def recover_stale_open_stations(
    root: Path, lane: dict[str, str]
) -> list[dict[str, object]]:
    directory = workflow_open_dir(root)
    if not directory.is_dir():
        return []
    found: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("lane") == lane:
            path.unlink()
            found.append(record)
    return found


def write_open_station(
    root: Path, run_id: str, lane: dict[str, str], started_at: datetime
) -> Path:
    directory = workflow_open_dir(root, create=True)
    target = directory / f"{run_id}.json"
    stage = directory / f".{run_id}.tmp"
    try:
        stage.write_text(
            json.dumps(
                {
                    "schema_version": WORKFLOW_LOG_SCHEMA,
                    "kind": "workflow-station-open",
                    "run_id": run_id,
                    "started_at": started_at.isoformat(timespec="milliseconds").replace(
                        "+00:00", "Z"
                    ),
                    "lane": lane,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return target


def workflow_lane_lock_path(root: Path, lane: dict[str, str], *, create: bool = False) -> Path:
    key = hashlib.sha256(
        json.dumps(lane, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return workflow_state_dir(root, create=create) / f"{key}.lock"


def acquire_workflow_lane_lock(
    root: Path, lane: dict[str, str], run_id: str
) -> tuple[Path, Any]:
    path = workflow_lane_lock_path(root, lane, create=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "kind": "workflow-lane-lock",
                    "run_id": run_id,
                    "lane": lane,
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        handle.flush()
    except (OSError, BlockingIOError) as error:
        handle.close()
        raise StationLaneBusy(lane) from error
    return path, handle


def latest_lane_record(
    records: list[dict[str, Any]], lane: dict[str, str]
) -> dict[str, Any] | None:
    matching = [
        record
        for record in records
        if isinstance(record.get("station"), dict)
        and record["station"].get("lane") == lane
    ]
    return max(
        matching,
        key=lambda record: parse_workflow_time(record.get("ended_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )


def start_workflow_station(
    args: argparse.Namespace, root: Path, started_at: datetime, run_id: str
) -> dict[str, Any]:
    lane = station_lane(args, root)
    lock_path, lock_handle = acquire_workflow_lane_lock(root, lane, run_id)
    try:
        return _start_workflow_station_locked(
            args,
            root,
            started_at,
            run_id,
            lane,
            lock_path,
            lock_handle,
        )
    except BaseException:
        lock_handle.close()
        raise


def _start_workflow_station_locked(
    args: argparse.Namespace,
    root: Path,
    started_at: datetime,
    run_id: str,
    lane: dict[str, str],
    lock_path: Path,
    lock_handle: Any,
) -> dict[str, Any]:
    activities = list(dict.fromkeys(getattr(args, "activity", []) or []))
    entry_snapshot = workflow_dirty_snapshot(root)
    entry_revision = workflow_revision(root)
    issue = getattr(args, "issue", None)
    friction: list[str] = [str(issue)] if issue else []
    try:
        previous = read_workflow_lane_state(root, lane)
        if previous is None:
            previous = latest_lane_record(read_workflow_logs(root), lane)
    except CliError:
        previous = None
        friction.append("history_unavailable")
    open_stations = recover_stale_open_stations(root, lane)
    if open_stations:
        friction.append("stale_open_station_recovered")
    previous_end = parse_workflow_time(previous.get("ended_at")) if previous else None
    elapsed = (
        max(0.0, round((started_at - previous_end).total_seconds(), 6))
        if previous_end is not None
        else None
    )
    previous_snapshot = None
    if previous:
        station_files = previous.get("station", {}).get("files", {})
        candidates = (
            previous.get("exit_snapshot"),
            station_files.get("exit_snapshot") if isinstance(station_files, dict) else None,
        )
        previous_snapshot = next(
            (candidate for candidate in candidates if isinstance(candidate, dict)), None
        )
        if previous_snapshot is None:
            friction.append("previous_snapshot_unavailable")
    between_changes = (
        snapshot_changes(previous_snapshot, entry_snapshot)
        if previous_snapshot is not None
        else []
    )
    previous_revision = previous.get("revision") if previous else None
    if previous_revision and entry_revision and previous_revision != entry_revision:
        for change in between_changes:
            path = str(change["path"])
            committed_untracked = False
            if (
                change["change"] == "deleted"
                and previous_snapshot is not None
                and previous_snapshot.get(path, {}).get("status") == "??"
            ):
                committed_untracked = inside(root, path, "工站快照路径").is_file()
            if change["change"] == "restored" or committed_untracked:
                change["change"] = "clean_or_committed"
    if between_changes and activities == ["none"]:
        friction.append("unattributed_file_changes")
    reasons: list[str] = []
    if elapsed is None:
        reasons.append("no_previous_station")
    if lane["session"] == "unknown":
        reasons.append("lane_identity_unknown")
    if activities == ["none"]:
        reasons.append("activity_none")
    if "unknown" in activities:
        reasons.append("activity_unknown")
    if getattr(args, "idle", False):
        reasons.append("idle")
    if elapsed is not None and elapsed > STATION_INTERVAL_MAX_SECONDS:
        reasons.append("interval_exceeds_cap")
    if reasons:
        friction.extend(reason for reason in reasons if reason not in friction)
    attributed = (
        elapsed
        if elapsed is not None
        and not reasons
        and elapsed <= STATION_INTERVAL_MAX_SECONDS
        else 0.0
    )
    marker = write_open_station(root, run_id, lane, started_at)
    return {
        "run_id": run_id,
        "marker": marker,
        "lock_path": lock_path,
        "lock_handle": lock_handle,
        "previous_status": previous.get("status") if previous else None,
        "previous_command": previous.get("command") if previous else None,
        "entry_snapshot": entry_snapshot,
        "station": {
            "lane": lane,
            "thread": lane["session"],
            "activity": activities,
            "scope": getattr(args, "scope", None),
            "outcome": getattr(args, "outcome", None),
            "issue": getattr(args, "issue", None),
            "idle": bool(getattr(args, "idle", False)),
            "previous_run_id": previous.get("run_id") if previous else None,
            "open_run_ids": [item.get("run_id") for item in open_stations],
            "interval": {
                "elapsed_seconds": elapsed,
                "human_elapsed_upper_bound_seconds": elapsed,
                "max_attributable_seconds": STATION_INTERVAL_MAX_SECONDS,
                "declared_effort_upper_bound_seconds": attributed,
                "reasons": reasons,
            },
            "friction": sorted(set(friction)),
            "files": {"between": between_changes},
            "git": {
                "previous_revision": previous_revision,
                "entry_revision": entry_revision,
            },
        },
    }


def finish_workflow_station(
    context: dict[str, Any], root: Path
) -> dict[str, Any]:
    station = context["station"]
    exit_snapshot = workflow_dirty_snapshot(root)
    during_changes = snapshot_changes(context["entry_snapshot"], exit_snapshot)
    if during_changes and station["activity"] == ["none"]:
        station["friction"] = sorted(
            {*station["friction"], "unattributed_file_changes"}
        )
    station["files"]["during"] = during_changes
    station["files"]["exit_snapshot_count"] = len(exit_snapshot)
    station["files"]["exit_snapshot_sha256"] = hashlib.sha256(
        json.dumps(exit_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest().upper()
    context["exit_snapshot"] = exit_snapshot
    station["git"]["exit_revision"] = workflow_revision(root)
    if station["git"]["entry_revision"] != station["git"]["exit_revision"]:
        station["friction"] = sorted(
            {*station["friction"], "revision_changed_during_station"}
        )
    return station


def close_workflow_station(context: dict[str, Any]) -> None:
    marker = context.get("marker")
    try:
        if isinstance(marker, Path):
            marker.unlink(missing_ok=True)
    finally:
        handle = context.pop("lock_handle", None)
        if handle is not None:
            handle.close()


def workflow_revision(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            errors="replace",
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else None


def workflow_step(
    name: str,
    action: Any,
    steps: list[dict[str, Any]],
) -> Any:
    started = time.perf_counter()
    try:
        result = action()
    except BaseException:
        steps.append(
            {
                "name": name,
                "status": "failed",
                "duration_seconds": round(time.perf_counter() - started, 6),
            }
        )
        raise
    steps.append(
        {
            "name": name,
            "status": "passed",
            "duration_seconds": round(time.perf_counter() - started, 6),
        }
    )
    return result


def work_temp(work: Work) -> Path:
    base = temp_root(work.root)
    key = hashlib.sha256(path_key(work.path).encode("utf-8")).hexdigest()[:16]
    return owned_child(base, key, "临时目录")


def owned_child(base: Path, name: str, label: str) -> Path:
    """Reject aliases, including aliases to siblings within the allowed root."""
    expected = base / name
    if expected.parent != base or expected.is_symlink() or expected.is_junction():
        raise CliError(f"拒绝使用不安全的{label}：{expected}")
    try:
        target = expected.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise CliError(f"无法解析{label}：{expected}") from error
    if target != expected:
        raise CliError(f"拒绝使用重定向的{label}：{expected}")
    return target


def work_receipts(work: Work) -> Path:
    expected = work.root.resolve() / ".local" / "translator" / "evidence"
    base = expected.resolve(strict=False)
    if base != expected:
        raise CliError(f"拒绝使用重定向的回执目录：{base}")
    key = hashlib.sha256(path_key(work.path).encode("utf-8")).hexdigest()[:16]
    return owned_child(base, key, "回执目录")


def evidence_dir(work: Work, name: str) -> Path:
    base = (
        work_receipts(work)
        if name in {"final", "completion", "delivery"}
        else work_temp(work)
    )
    return owned_child(base, name, "证据目录")


def scratch_dir(work: Work, name: str) -> Path:
    if (
        not ID_RE.fullmatch(name)
        or name.endswith(".")
        or re.fullmatch(
            r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name, re.IGNORECASE
        )
    ):
        raise CliError("--agent 必须是安全的小写目录名")
    base = evidence_dir(work, "scratch")
    target = owned_child(base, name, "scratch 目录")
    target.mkdir(parents=True, exist_ok=True)
    return target


def command_scratch(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    target = scratch_dir(work, args.agent)
    print(
        json.dumps(
            {
                "path": str(target),
                "work_id": work.work_id,
                "source_sha256": sha256(work.source),
            },
            ensure_ascii=False,
        )
    )
    return 0


def source_probe(work: Work, agent: str) -> tuple[Any, dict[str, object], Path, Path]:
    if work.source.suffix.lower() != ".epub":
        raise CliError("source-probe/source-draft 只支持 EPUB 原生来源")
    if sha256(work.source) != str(work.manifest["source"]["sha256"]).upper():
        raise CliError("EPUB 来源哈希与 manifest 不一致")
    scratch = scratch_dir(work, agent)
    try:
        package, profile = epub_source.probe_epub(work.source)
        report = epub_source.write_probe(
            profile, scratch / f"source-probe-{workflow_run_id()}", scratch_root=scratch
        )
    except epub_source.EpubSourceError as error:
        raise CliError(str(error)) from error
    return package, profile, report, scratch


def command_source_probe(args: argparse.Namespace) -> int:
    _, profile, report, _ = source_probe(load_command_work(args), args.agent)
    print(json.dumps({"profile": str(report), "ready_for_draft": profile["ready_for_draft"],
                      "issues": len(profile["issues"]), "verified": False}, ensure_ascii=False))
    return 0


def command_source_draft(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    _, profile, report, scratch = source_probe(work, args.agent)
    try:
        if not profile["ready_for_draft"]:
            raise epub_source.EpubSourceError(f"来源探查发现 {len(profile['issues'])} 个待处理问题")
        package, drafts = epub_source.render_epub(work.source)
        text_check = epub_source.verify_drafts(drafts)
        paths, mapping = epub_source.write_drafts(
            package,
            drafts,
            scratch / f"source-draft-{workflow_run_id()}",
            scratch_root=scratch,
        )
    except epub_source.EpubSourceError as error:
        raise CliError(f"{error}；来源探查报告：{report}") from error
    print(
        json.dumps(
            {
                "drafts": [str(path) for path in paths],
                "source_map": str(mapping),
                "profile": str(report),
                "text_check": text_check,
                "verified": False,
            },
            ensure_ascii=False,
        )
    )
    return 0

def reset_evidence_dir(work: Work, name: str) -> Path:
    target = evidence_dir(work, name)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def clear_evidence_dir(work: Work, name: str) -> None:
    target = evidence_dir(work, name)
    if target.exists():
        shutil.rmtree(target)


def reset_evidence_child(
    work: Work, name: str, child: str, *, create: bool = True
) -> Path:
    root = evidence_dir(work, name)
    root.mkdir(parents=True, exist_ok=True)
    target = owned_child(root, child, "临时目录")
    if target.exists():
        shutil.rmtree(target)
    if create:
        target.mkdir()
    return target


def command_render(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    if work.source.suffix.lower() != ".pdf":
        raise CliError("render 目前只支持 PDF 来源；EPUB 来源请使用 EPUB 阅读器检查")
    if not PAGES_RE.fullmatch(args.pages):
        raise CliError("--pages 必须类似 23、23-24 或 1,3-5")
    if not 72 <= args.dpi <= 600:
        raise CliError("--dpi 必须在 72 到 600 之间")
    output = reset_evidence_dir(work, "source")
    pattern = output / "page-%04d.png"
    run(
        [
            "mutool",
            "draw",
            "-q",
            "-r",
            str(args.dpi),
            "-o",
            str(pattern),
            str(work.source),
            args.pages,
        ]
    )
    if not any(output.glob("page-*.png")):
        raise CliError("mutool 没有生成页面图")
    print(output)
    return 0


def select_jobs(
    work: Work,
    language_code: str,
    target_name: str,
) -> list[tuple[Language, str, Path]]:
    jobs = []
    for language in work.languages:
        if language_code != "all" and language.code != language_code:
            continue
        for target, output in language.outputs.items():
            if target_name == "all" or target == target_name:
                jobs.append((language, target, output))
    if not jobs:
        raise CliError("没有匹配 --lang/--target 的交付物")
    return jobs


def record_selected_jobs(
    args: argparse.Namespace, jobs: list[tuple[Language, str, Path]]
) -> None:
    args._workflow_targets = sorted(
        f"{language.code}:{target}" for language, target, _ in jobs
    )


def publish_atomic(
    source: Path,
    target: Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, stage_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    stage = Path(stage_name)
    try:
        with source.open("rb") as source_handle, stage.open("wb") as stage_handle:
            shutil.copyfileobj(source_handle, stage_handle)
            stage_handle.flush()
            os.fsync(stage_handle.fileno())
        if expected_bytes is not None and stage.stat().st_size != expected_bytes:
            raise CliError("原子发布 staging 文件大小与验收值不一致")
        if expected_sha256 is not None and sha256(stage) != expected_sha256.upper():
            raise CliError("原子发布 staging 文件 SHA-256 与验收值不一致")
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)


class TypstHTMLNormalizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.noteref_id: str | None = None
        self.noteref_anchor_open = False
        self.backlink_depth = 0
        self.backlink_anchor_open = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        if tag == "sup" and values.get("role") == "doc-noteref":
            identifier = values.get("id")
            if not identifier:
                raise CliError("Typst HTML 脚注引用缺少 id")
            self.noteref_id = identifier
        elif tag == "a" and self.noteref_id is not None:
            href = values.get("href")
            if not href:
                raise CliError("Typst HTML 脚注引用缺少 href")
            self.parts.append(
                f'<a href="{escape(href, quote=True)}" class="footnote-ref" '
                f'id="{escape(self.noteref_id, quote=True)}" '
                'role="doc-noteref"><sup>'
            )
            self.noteref_anchor_open = True
        elif tag == "sup" and values.get("role") == "doc-backlink":
            self.backlink_depth += 1
        elif tag == "a" and self.backlink_depth:
            href = values.get("href")
            if not href:
                raise CliError("Typst HTML 脚注返回链接缺少 href")
            self.parts.append(
                f'<a href="{escape(href, quote=True)}" class="footnote-back" '
                'role="doc-backlink">'
            )
            self.backlink_anchor_open = True
        elif tag == "section" and values.get("role") == "doc-endnotes":
            self.parts.append(
                '<section id="footnotes" '
                'class="footnotes footnotes-end-of-document" '
                'role="doc-endnotes">'
            )
        else:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.noteref_anchor_open:
            self.parts.append("</sup></a>")
            self.noteref_anchor_open = False
        elif tag == "sup" and self.noteref_id is not None:
            if self.noteref_anchor_open:
                raise CliError("Typst HTML 脚注引用未闭合")
            self.noteref_id = None
        elif tag == "a" and self.backlink_anchor_open:
            self.parts.append("</a>")
            self.backlink_anchor_open = False
        elif tag == "sup" and self.backlink_depth:
            if self.backlink_anchor_open:
                raise CliError("Typst HTML 脚注返回链接未闭合")
            self.backlink_depth -= 1
        else:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(f"<![{data}]>")


def normalize_typst_html(text: str) -> str:
    parser = TypstHTMLNormalizer()
    parser.feed(text)
    parser.close()
    if parser.noteref_id is not None or parser.backlink_depth:
        raise CliError("Typst HTML 脚注结构未闭合")
    return "".join(parser.parts)


def epub_metadata(work: Work, language: Language) -> tuple[str, str | None, str]:
    epub_data = work.manifest.get("epub")
    if not isinstance(epub_data, dict):
        epub_data = {}
    title = language.epub_title or epub_data.get("title")
    author = language.epub_author or epub_data.get("author")
    identifier = (
        language.epub_identifier
        or epub_data.get("identifier")
        or f"urn:translator:{work.work_id}:{language.code}"
    )
    return (
        title.strip() if isinstance(title, str) and title.strip() else work.title,
        author.strip() if isinstance(author, str) and author.strip() else None,
        str(identifier).strip(),
    )


def epub_date(work: Work) -> str | None:
    """Return the optional translated-edition date for EPUB metadata.

    EPUB builds from Markdown, Typst, and LaTeX all pass their metadata to
    Pandoc separately.  Keeping the date lookup at the work level lets a
    manifest opt in without changing source-language front matter or relying
    on a LaTeX ``\\date`` command (which Pandoc does not use for dc:date).
    """
    epub_data = work.manifest.get("epub")
    if not isinstance(epub_data, dict):
        return None
    raw = epub_data.get("date")
    if isinstance(raw, (int, float)):
        value = str(raw)
    elif isinstance(raw, str):
        value = raw.strip()
    else:
        return None
    return value or None


def png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise CliError(f"未生成有效 PNG 封面：{path}")
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if width <= 0 or height <= 0:
        raise CliError(f"PNG 封面尺寸无效：{path}")
    return width, height


def remove_empty_spans(root: ET.Element, class_name: str | None = None) -> int:
    normalize_cjk_boundary = class_name == "source-page"

    removed = 0
    for parent in root.iter():
        for child in list(parent):
            if (
                child.tag.rsplit("}", 1)[-1] != "span"
                or "".join(child.itertext()).strip()
            ):
                continue
            if class_name and class_name not in child.attrib.get("class", "").split():
                continue
            index = list(parent).index(child)
            tail = child.tail or ""
            if normalize_cjk_boundary:
                previous_text = (
                    list(parent)[index - 1].tail
                    if index
                    else parent.text or ""
                ) or ""
                left = previous_text.rstrip(" \t")
                right = tail.lstrip(" \t")
                if CJK_BOUNDARY_CHAR_RE.fullmatch(
                    left[-1:]
                ) and CJK_BOUNDARY_CHAR_RE.fullmatch(right[:1]):
                    if index:
                        list(parent)[index - 1].tail = left
                    else:
                        parent.text = left
                    tail = right
            if tail:
                if index:
                    previous = list(parent)[index - 1]
                    previous.tail = (previous.tail or "") + tail
                else:
                    parent.text = (parent.text or "") + tail
            parent.remove(child)
            removed += 1
    return removed


def remove_redundant_figure_captions(root: ET.Element) -> int:
    """Remove Pandoc's visible caption when it only repeats an image alt."""
    removed = 0
    for figure in root.iter():
        if figure.tag.rsplit("}", 1)[-1] != "figure":
            continue
        images = [
            element
            for element in figure.iter()
            if element.tag.rsplit("}", 1)[-1] == "img"
        ]
        if len(images) != 1:
            continue
        alt = _normalize_caption_text(images[0].attrib.get("alt", ""))
        if not alt:
            continue
        caption = next(
            (
                element
                for element in figure
                if element.tag.rsplit("}", 1)[-1] == "figcaption"
            ),
            None,
        )
        if caption is None or _normalize_caption_text("".join(caption.itertext())) != alt:
            continue
        index = list(figure).index(caption)
        if caption.tail:
            if index:
                previous = list(figure)[index - 1]
                previous.tail = (previous.tail or "") + caption.tail
            else:
                figure.text = (figure.text or "") + caption.tail
        figure.remove(caption)
        removed += 1
    return removed


def visible_markup_artifact(root: ET.Element) -> str | None:
    markers = ("<br />", "<br/>", "<br>", "<sub>", "</sub>")

    def visit(element: ET.Element, ignored: bool = False) -> str | None:
        ignored = ignored or element.tag.rsplit("}", 1)[-1] in {"code", "pre"}
        if not ignored and element.text:
            for marker in markers:
                if marker in element.text:
                    return marker
        for child in element:
            found = visit(child, ignored)
            if found:
                return found
            if not ignored and child.tail:
                for marker in markers:
                    if marker in child.tail:
                        return marker
        return None

    return visit(root)


def nonformula_image_alt_problem(element: ET.Element) -> str | None:
    alt = element.attrib.get("alt")
    decorative = (
        element.attrib.get("role") == "presentation"
        or element.attrib.get("aria-hidden", "").lower() == "true"
    )
    if alt is None:
        return "缺少替代文本"
    normalized = alt.strip()
    if not normalized:
        return None if decorative else "替代文本为空但未声明为装饰图"
    if normalized.casefold() in GENERIC_IMAGE_ALT:
        return "替代文本过于笼统"
    if LATEX_CONTROL.search(normalized):
        return "替代文本含 LaTeX 控制序列"
    return None


def epub_structure_problems(root: ET.Element) -> list[str]:
    """Return visible EPUB structures that indicate a broken source semantic."""
    problems: list[str] = []
    for parent in root.iter():
        children = list(parent)
        for index, child in enumerate(children):
            name = child.tag.rsplit("}", 1)[-1]
            if name == "figure":
                caption_element = next(
                    (
                        candidate
                        for candidate in child
                        if candidate.tag.rsplit("}", 1)[-1] == "figcaption"
                    ),
                    None,
                )
                caption = (
                    _normalize_caption_text("".join(caption_element.itertext()))
                    if caption_element is not None
                    else ""
                )
                if caption:
                    image = next(
                        (
                            candidate
                            for candidate in child.iter()
                            if candidate.tag.rsplit("}", 1)[-1] == "img"
                        ),
                        None,
                    )
                    if image is not None and _normalize_caption_text(
                        image.attrib.get("alt", "")
                    ) == caption:
                        problems.append(f"自动图注未移除：{caption}")
                    for sibling in children[index + 1 : index + 5]:
                        sibling_name = sibling.tag.rsplit("}", 1)[-1]
                        if sibling_name in {
                            "h1",
                            "h2",
                            "h3",
                            "h4",
                            "h5",
                            "h6",
                            "figure",
                            "ul",
                            "ol",
                        }:
                            break
                        if sibling_name == "p" and _normalize_caption_text(
                            "".join(sibling.itertext())
                        ) == caption:
                            problems.append(f"图注重复：{caption}")
                            break
            if name != "p" or index == 0:
                continue
            previous_name = children[index - 1].tag.rsplit("}", 1)[-1]
            if previous_name not in {"ul", "ol"}:
                continue
            text = " ".join("".join(child.itertext()).split())
            marker_count = len(re.findall(r"(?:^|\s)(?:[-*+]|\d+[.)])\s+", text))
            if re.match(r"^(?:[-*+]|\d+[.)])\s+\S", text) and marker_count >= 2:
                problems.append("列表项目被解析成普通段落")
    return problems


def remove_empty_paragraphs(root: ET.Element) -> int:
    """Drop paragraphs left behind after EPUB-only source markers are removed."""
    removed = 0
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] != "p":
                continue
            if list(child) or "".join(child.itertext()).strip():
                continue
            parent.remove(child)
            removed += 1
    return removed


def add_epub_code_theme_classes(root: ET.Element) -> int:
    added = 0
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        classes = element.attrib.get("class", "").split()
        is_source_container = local_name == "div" and "sourceCode" in classes
        is_inline_code = local_name == "code" and "sourceCode" not in classes
        if not (is_source_container or is_inline_code):
            continue
        if APPLE_BOOKS_DARK_CLASS in classes:
            continue
        element.attrib["class"] = " ".join([*classes, APPLE_BOOKS_DARK_CLASS])
        added += 1
    return added


def add_epub_footnote_backlinks(root: ET.Element) -> int:
    xhtml_namespace = "http://www.w3.org/1999/xhtml"
    epub_type = f"{{{EPUB_NAMESPACE}}}type"
    used_ids = {element.attrib["id"] for element in root.iter() if element.get("id")}
    footnotes: dict[str, ET.Element] = {}
    references: dict[str, list[ET.Element]] = {}
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        semantic_types = set(element.attrib.get(epub_type, "").split())
        if local_name == "aside" and "footnote" in semantic_types and element.get("id"):
            footnotes[element.attrib["id"]] = element
        if local_name != "a" or "noteref" not in semantic_types:
            continue
        parsed = urllib.parse.urlsplit(element.attrib.get("href", ""))
        fragment = urllib.parse.unquote(parsed.fragment)
        if fragment and not parsed.path:
            references.setdefault(fragment, []).append(element)

    added = 0
    for footnote_id, note_references in references.items():
        footnote = footnotes.get(footnote_id)
        if footnote is None:
            continue
        existing_targets = {
            child.attrib.get("href")
            for child in footnote.iter()
            if child.tag.rsplit("}", 1)[-1] == "a"
            and "backlink" in child.attrib.get(epub_type, "").split()
        }
        paragraph = next(
            (
                child
                for child in footnote
                if child.tag.rsplit("}", 1)[-1] == "p"
                and "footnote-backlink" in child.attrib.get("class", "").split()
            ),
            None,
        )
        for reference in note_references:
            reference_id = reference.get("id")
            if not reference_id:
                candidate = f"{footnote_id}-ref"
                suffix = 2
                while candidate in used_ids:
                    candidate = f"{footnote_id}-ref-{suffix}"
                    suffix += 1
                reference.set("id", candidate)
                used_ids.add(candidate)
                reference_id = candidate
            target = f"#{reference_id}"
            if target in existing_targets:
                continue
            if paragraph is None:
                paragraph = ET.SubElement(
                    footnote,
                    f"{{{xhtml_namespace}}}p",
                    {"class": "footnote-backlink"},
                )
            elif len(paragraph):
                paragraph[-1].tail = (paragraph[-1].tail or "") + " "
            backlink = ET.SubElement(
                paragraph,
                f"{{{xhtml_namespace}}}a",
                {epub_type: "backlink", "href": target, "aria-label": "返回正文"},
            )
            backlink.text = "↩"
            existing_targets.add(target)
            added += 1
    return added


def replace_navigation_formula_images(root: ET.Element) -> int:
    symbols = {
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
        "\\infty.1": "∞.1",
        "\\infty.2.2": "∞.2.2",
        "\\Pi_1": "Π₁",
    }
    replaced = 0
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] != "img":
                continue
            classes = set(child.attrib.get("class", "").split())
            if not classes.intersection(FORMULA_CLASSES):
                continue
            alt = child.attrib.get("alt", "")
            marker = alt.strip()
            if "\\" in marker:
                if marker not in symbols:
                    raise CliError(f"导航公式无法安全转为文本：{marker}")
                alt = symbols[marker]
            text = alt + (child.tail or "")
            index = list(parent).index(child)
            if index:
                previous = list(parent)[index - 1]
                previous.tail = (previous.tail or "") + text
            else:
                parent.text = (parent.text or "") + text
            parent.remove(child)
            replaced += 1
    return replaced


def repair_split_fragment_links(documents: dict[str, ET.Element]) -> int:
    """Point fragment-only links at the split XHTML file that owns the target."""
    members_by_id: dict[str, set[str]] = {}
    ids_by_member: dict[str, set[str]] = {}
    for member, document in documents.items():
        identifiers = {
            element.attrib["id"]
            for element in document.iter()
            if element.attrib.get("id")
        }
        ids_by_member[member] = identifiers
        for identifier in identifiers:
            members_by_id.setdefault(identifier, set()).add(member)

    repaired = 0
    for member, document in documents.items():
        for element in document.iter():
            if element.tag.rsplit("}", 1)[-1] != "a":
                continue
            href = element.attrib.get("href", "")
            parsed = urllib.parse.urlsplit(href)
            fragment = urllib.parse.unquote(parsed.fragment)
            if (
                not fragment
                or parsed.path
                or parsed.scheme
                or fragment in ids_by_member[member]
            ):
                continue
            targets = members_by_id.get(fragment, set())
            if len(targets) != 1:
                continue
            target = next(iter(targets))
            relative = posixpath.relpath(target, posixpath.dirname(member))
            element.attrib["href"] = f"{relative}#{urllib.parse.quote(fragment)}"
            repaired += 1
    return repaired


def normalize_epub_navigation(source: Path, output: Path) -> None:
    xhtml_namespace = "http://www.w3.org/1999/xhtml"
    ET.register_namespace("", xhtml_namespace)
    ET.register_namespace("epub", "http://www.idpf.org/2007/ops")
    with zipfile.ZipFile(source) as archive:
        items = archive.infolist()
        payloads = {item.filename: archive.read(item.filename) for item in items}
    documents = {
        member: ET.fromstring(data)
        for member, data in payloads.items()
        if member.endswith(".xhtml")
    }
    repair_split_fragment_links(documents)
    with zipfile.ZipFile(output, "w") as normalized:
        for item in items:
            data = payloads[item.filename]
            if item.filename in documents:
                navigation = documents[item.filename]
                if item.filename.endswith("nav.xhtml"):
                    replace_navigation_formula_images(navigation)
                remove_empty_spans(
                    navigation,
                    None if item.filename.endswith("nav.xhtml") else "source-page",
                )
                if not item.filename.endswith("nav.xhtml"):
                    remove_empty_spans(navigation, "source-unit")
                    remove_empty_spans(navigation, "image-tail")
                    remove_redundant_figure_captions(navigation)
                add_epub_code_theme_classes(navigation)
                add_epub_footnote_backlinks(navigation)
                remove_empty_paragraphs(navigation)
                data = ET.tostring(
                    navigation,
                    encoding="utf-8",
                    xml_declaration=True,
                )
            normalized.writestr(item, data)


def pandoc_resource_path(
    root: Path,
    work_path: Path,
    entry: Path,
    *extra: Path,
) -> str:
    paths: list[str] = []
    for path in (*extra, entry.parent, work_path, root):
        value = str(path.resolve())
        if value not in paths:
            paths.append(value)
    return os.pathsep.join(paths)


def count_pandoc_nodes(value: Any, node_type: str) -> int:
    if isinstance(value, list):
        return sum(count_pandoc_nodes(item, node_type) for item in value)
    if not isinstance(value, dict):
        return 0
    return int(value.get("t") == node_type) + sum(
        count_pandoc_nodes(item, node_type) for item in value.values()
    )


def pandoc_math(value: Any) -> list[tuple[str, bool]]:
    formulas: list[tuple[str, bool]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("t") == "Math":
            content = node.get("c")
            if (
                isinstance(content, list)
                and len(content) == 2
                and isinstance(content[0], dict)
                and content[0].get("t") in {"InlineMath", "DisplayMath"}
                and isinstance(content[1], str)
            ):
                formulas.append(
                    (content[1], content[0]["t"] == "DisplayMath")
                )
            return
        for item in node.values():
            visit(item)

    visit(value)
    return formulas


def pandoc_svg_images(value: Any) -> set[str]:
    sources: set[str] = set()
    if isinstance(value, list):
        for item in value:
            sources.update(pandoc_svg_images(item))
    elif isinstance(value, dict):
        if value.get("t") == "Image":
            content = value.get("c")
            if (
                isinstance(content, list)
                and len(content) == 3
                and isinstance(content[2], list)
                and content[2]
                and isinstance(content[2][0], str)
                and content[2][0].lower().endswith(".svg")
            ):
                sources.add(content[2][0])
        else:
            for item in value.values():
                sources.update(pandoc_svg_images(item))
    return sources


def source_formula_css_dimensions(
    source_epub: Path | None,
) -> dict[str, tuple[float, float, str | None]]:
    """Read simple source-EPUB inline formula size overrides."""
    if source_epub is None or source_epub.suffix.lower() != ".epub":
        return {}
    try:
        with zipfile.ZipFile(source_epub) as archive:
            css_members = [
                name for name in archive.namelist() if name.lower().endswith(".css")
            ]
            styles = [
                re.sub(
                    r"/\*.*?\*/",
                    "",
                    archive.read(name).decode("utf-8", errors="replace"),
                    flags=re.DOTALL,
                )
                for name in css_members
            ]
    except (OSError, zipfile.BadZipFile) as error:
        raise CliError(f"无法读取来源 EPUB 公式 CSS：{source_epub}") from error

    dimensions: dict[str, tuple[float, float, str | None]] = {}
    conflicts: set[str] = set()
    for style in styles:
        for match in SOURCE_FORMULA_CSS_RE.finditer(style):
            name = match.group("name")
            candidate = (
                float(match.group("width")),
                float(match.group("height")),
                match.group("align").strip() if match.group("align") else None,
            )
            if not all(math.isfinite(value) and value > 0 for value in candidate[:2]):
                continue
            previous = dimensions.get(name)
            if previous is not None and previous != candidate:
                conflicts.add(name)
            else:
                dimensions[name] = candidate
    for name in conflicts:
        dimensions.pop(name, None)
    return dimensions


def _format_css_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _source_formula_style(
    style: str,
    height: float,
    align: str | None,
) -> str:
    parts = [
        part.strip()
        for part in style.split(";")
        if part.strip()
        and not re.match(
            r"^(?:height|width|vertical-align)\s*:", part, re.IGNORECASE
        )
    ]
    if align:
        parts.append(f"vertical-align: {align}")
    parts.extend(
        (
            f"height: {height * SOURCE_INLINE_FORMULA_EM_PER_PX:.4f}em",
            "width: auto",
        )
    )
    return "; ".join(parts) + ";"


def apply_source_formula_dimensions(
    document: Any,
    source_epub: Path | None,
) -> int:
    """Make source raster formula dimensions explicit in the Pandoc AST."""
    dimensions = source_formula_css_dimensions(source_epub)
    if not dimensions:
        return 0

    updated = 0

    def visit(value: Any) -> None:
        nonlocal updated
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("t") == "Image":
            content = value.get("c")
            if (
                isinstance(content, list)
                and len(content) == 3
                and isinstance(content[0], list)
                and len(content[0]) == 3
                and isinstance(content[0][1], list)
                and "math-inline" in content[0][1]
                and isinstance(content[0][2], list)
                and isinstance(content[2], list)
                and content[2]
                and isinstance(content[2][0], str)
            ):
                source = urllib.parse.urlsplit(content[2][0]).path
                suffix = posixpath.splitext(source)[1].lower()
                name = posixpath.splitext(posixpath.basename(source))[0]
                target = dimensions.get(name)
                if suffix in SOURCE_RASTER_SUFFIXES and target is not None:
                    width, height, align = target
                    attributes = dict(
                        item
                        for item in content[0][2]
                        if isinstance(item, list)
                        and len(item) == 2
                        and isinstance(item[0], str)
                    )
                    attributes["width"] = _format_css_number(width)
                    attributes["height"] = _format_css_number(height)
                    attributes["style"] = _source_formula_style(
                        attributes.get("style", ""), height, align
                    )
                    content[0][2] = [list(item) for item in attributes.items()]
                    updated += 1
        for item in value.values():
            visit(item)

    visit(document)
    return updated


def gladtex_formula_key(formula: str) -> str:
    return formula.replace("{}", " ").replace("\t", " ").replace("  ", " ").strip()


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


def svg_math_cache_dir(work_path: Path) -> Path:
    return (
        work_path
        / ".cache"
        / "svg-math"
        / f"gladtex-{GLADTEX_VERSION}-svg-{SVG_MATH_CACHE_VERSION}"
        / "formulas"
    )


def valid_formula_svg_data(data: bytes) -> bool:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    return (
        root.tag == f"{{{SVG_NAMESPACE}}}svg"
        and len(root.attrib.get("viewBox", "").split()) == 4
    )


def seed_svg_math_cache(
    epub: Path | None,
    document: dict[str, Any],
    cache_dir: Path,
) -> int:
    cache_file = cache_dir / "gladtex.cache"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            cache_file.unlink(missing_ok=True)
        else:
            if (
                isinstance(cached, dict)
                and cached.get("GladTeX__cache__version") == "2.0"
            ):
                return 0
            cache_file.unlink(missing_ok=True)
    if epub is None or not epub.is_file():
        return 0

    current: dict[tuple[str, bool], set[str]] = {}
    current_sequence: list[tuple[tuple[str, bool], str]] = []
    for formula, display in pandoc_math(document):
        key = (gladtex_alt_text(formula), display)
        normalized = gladtex_formula_key(formula)
        current.setdefault(key, set()).add(normalized)
        current_sequence.append((key, normalized))

    previous: dict[
        tuple[str, bool], set[tuple[str, float, float, float]]
    ] = {}
    previous_sequence: list[
        tuple[tuple[str, bool], tuple[str, float, float, float]]
    ] = []
    svg_data: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(epub) as archive:
            names = archive.namelist()
            members = set(names)
            for name in sorted(item for item in names if item.endswith(".xhtml")):
                root = ET.fromstring(archive.read(name))
                for image in root.iter():
                    if image.tag.rsplit("}", 1)[-1] != "img":
                        continue
                    classes = set(image.attrib.get("class", "").split())
                    formula_class = classes & FORMULA_CLASSES
                    if len(formula_class) != 1:
                        continue
                    source = urllib.parse.unquote(
                        urllib.parse.urlsplit(image.attrib.get("src", "")).path
                    )
                    member = posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), source)
                    )
                    if (
                        not source
                        or member.startswith("../")
                        or member not in members
                    ):
                        continue
                    width = float(image.attrib["width"])
                    height = float(image.attrib["height"])
                    align = re.search(
                        r"vertical-align:\s*(-?\d+(?:\.\d+)?)px",
                        image.attrib.get("style", ""),
                    )
                    if align is None:
                        continue
                    data = archive.read(member)
                    if not valid_formula_svg_data(data):
                        continue
                    svg_data[member] = data
                    key = (
                        image.attrib.get("alt", "").strip(),
                        "math-display" in formula_class,
                    )
                    candidate = (
                        member,
                        width,
                        height,
                        -float(align.group(1)),
                    )
                    previous.setdefault(key, set()).add(candidate)
                    previous_sequence.append((key, candidate))
    except (KeyError, OSError, ValueError, ET.ParseError, zipfile.BadZipFile):
        return 0

    matches: dict[
        tuple[str, bool], set[tuple[str, float, float, float]]
    ] = {}
    for key, formulas in current.items():
        candidates = previous.get(key, set())
        if len(formulas) == 1 and len(candidates) == 1:
            matches[(next(iter(formulas)), key[1])] = set(candidates)
    matcher = difflib.SequenceMatcher(
        None,
        [key for key, _formula in current_sequence],
        [key for key, _candidate in previous_sequence],
    )
    for current_start, previous_start, size in matcher.get_matching_blocks():
        for offset in range(size):
            (key, formula) = current_sequence[current_start + offset]
            _old_key, candidate = previous_sequence[previous_start + offset]
            matches.setdefault((formula, key[1]), set()).add(candidate)

    seeded: dict[str, dict[str, Any]] = {
        "GladTeX__cache__version": "2.0"
    }
    files: list[tuple[Path, bytes]] = []
    for (formula, display), candidates in matches.items():
        if len(candidates) != 1:
            continue
        member, width, height, depth = next(iter(candidates))
        target = cache_dir / f"eqn{len(files):03d}.svg"
        display_key = str(display).lower()
        seeded.setdefault(formula, {})[display_key] = {
            "pos": {"width": width, "height": height, "depth": depth},
            "path": target.name,
        }
        files.append((target, svg_data[member]))

    if not files:
        return 0
    cache_dir.mkdir(parents=True, exist_ok=True)
    for path, data in files:
        path.write_bytes(data)
    stage = cache_dir / f".gladtex.cache.{os.getpid()}.tmp"
    stage.write_text(json.dumps(seeded), encoding="utf-8")
    os.replace(stage, cache_file)
    return len(files)


def prepare_svg_math_cache(cache_dir: Path, formula_dir: Path) -> None:
    formula_dir.mkdir()
    if not cache_dir.is_dir():
        return
    cache_file = cache_dir / "gladtex.cache"
    if cache_file.is_file():
        shutil.copyfile(cache_file, formula_dir / cache_file.name)
    for source in cache_dir.glob("eqn*.svg"):
        target = formula_dir / source.name
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)


def persist_svg_math_cache(formula_dir: Path, cache_dir: Path) -> None:
    source_cache = formula_dir / "gladtex.cache"
    try:
        raw_cache = json.loads(source_cache.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return
    if (
        not isinstance(raw_cache, dict)
        or raw_cache.get("GladTeX__cache__version") != "2.0"
    ):
        return

    cache: dict[str, Any] = {"GladTeX__cache__version": "2.0"}
    sources: dict[str, Path] = {}
    for formula, variants in raw_cache.items():
        if formula == "GladTeX__cache__version" or not isinstance(variants, dict):
            continue
        accepted: dict[str, Any] = {}
        for display in ("false", "true"):
            item = variants.get(display)
            position = item.get("pos") if isinstance(item, dict) else None
            relative = item.get("path") if isinstance(item, dict) else None
            values = (
                [position.get(key) for key in ("width", "height", "depth")]
                if isinstance(position, dict)
                else []
            )
            if (
                not isinstance(relative, str)
                or re.fullmatch(r"eqn\d+\.svg", relative) is None
                or len(values) != 3
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in values
                )
            ):
                continue
            source = formula_dir / relative
            try:
                data = source.read_bytes()
            except OSError:
                continue
            if not valid_formula_svg_data(data):
                continue
            accepted[display] = {
                "pos": dict(zip(("width", "height", "depth"), values)),
                "path": relative,
            }
            sources[relative] = source
        if accepted and isinstance(formula, str):
            cache[formula] = accepted

    cache_dir.mkdir(parents=True, exist_ok=True)
    for relative, source in sorted(sources.items()):
        target = cache_dir / relative
        if target.exists():
            try:
                if os.path.samefile(source, target):
                    continue
            except OSError:
                pass
        stage = cache_dir / f".{relative}.{os.getpid()}.tmp"
        shutil.copyfile(source, stage)
        os.replace(stage, target)
    stage = cache_dir / f".gladtex.cache.{os.getpid()}.tmp"
    stage.write_text(json.dumps(cache), encoding="utf-8")
    os.replace(stage, cache_dir / "gladtex.cache")


def normalize_formula_svg(path: Path) -> None:
    ET.register_namespace("", SVG_NAMESPACE)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError as error:
        raise CliError(f"GladTeX 生成了无效 SVG：{path}") from error
    if root.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise CliError(f"公式资源不是 SVG：{path}")
    view_box = root.attrib.get("viewBox", "").split()
    if len(view_box) != 4:
        raise CliError(f"公式 SVG 缺少有效 viewBox：{path}")

    children = list(root)
    classes = {child.attrib.get("class") for child in children}
    if {"math-background", "math-foreground"} <= classes and any(
        child.tag.rsplit("}", 1)[-1] == "style"
        and "prefers-color-scheme:dark" in "".join(child.itertext())
        for child in children
    ):
        return
    for child in children:
        root.remove(child)
    style = ET.Element(f"{{{SVG_NAMESPACE}}}style")
    style.text = (
        ".math-background{fill:#fff}"
        ".math-foreground,.math-foreground *{fill:#111}"
        "@media(prefers-color-scheme:dark){"
        ".math-background{fill:#182226}"
        ".math-foreground,.math-foreground *{fill:#f5f7f8}}"
    )
    root.append(style)
    for child in children:
        if child.tag.rsplit("}", 1)[-1] == "defs":
            root.append(child)
    root.append(
        ET.Element(
            f"{{{SVG_NAMESPACE}}}rect",
            {
                "class": "math-background",
                "x": view_box[0],
                "y": view_box[1],
                "width": view_box[2],
                "height": view_box[3],
            },
        )
    )
    foreground = ET.Element(
        f"{{{SVG_NAMESPACE}}}g",
        {"class": "math-foreground"},
    )
    for child in children:
        if child.tag.rsplit("}", 1)[-1] != "defs":
            foreground.append(child)
    root.append(foreground)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def render_svg_math_ast(
    root: Path,
    work_path: Path,
    source: Path,
    output: Path,
    cwd: Path,
    source_epub: Path | None = None,
    seed_epub: Path | None = None,
) -> int:
    document = json.loads(source.read_text(encoding="utf-8"))
    source_formula_dimensions = apply_source_formula_dimensions(
        document, source_epub
    )
    print(f"source_formula_dimensions={source_formula_dimensions}")
    source_svgs = pandoc_svg_images(document)
    formula_count = count_pandoc_nodes(document, "Math")
    if formula_count == 0:
        if source_formula_dimensions:
            output.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            shutil.copyfile(source, output)
        return 0

    cache_dir = svg_math_cache_dir(work_path)
    seeded = seed_svg_math_cache(seed_epub, document, cache_dir)
    formula_dir = cwd / "formulas"
    prepare_svg_math_cache(cache_dir, formula_dir)
    filter_source = formula_dir / source.name
    filter_output = formula_dir / output.name
    if source_formula_dimensions:
        filter_source.write_text(
            json.dumps(document, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        shutil.copyfile(source, filter_source)
    environment = os.environ.copy()
    environment["UV_CACHE_DIR"] = str(root / ".tmp" / "uv-cache")
    environment["PYTHONWARNINGS"] = "ignore::RuntimeWarning"
    try:
        result = run(
            [
                "uv",
                "run",
                "--with",
                f"gladtex=={GLADTEX_VERSION}",
                "python",
                str(root / "tools" / "gladtex_filter.py"),
                filter_source.name,
                filter_output.name,
                ".",
            ],
            cwd=formula_dir,
            env=environment,
        )
    except CliError:
        try:
            persist_svg_math_cache(formula_dir, cache_dir)
        except OSError as cache_error:
            print(f"WARNING: 公式缓存检查点失败：{cache_error}", file=sys.stderr)
        raise
    match = re.search(r"svg_math_images=(\d+)", result.stdout)
    if not match or int(match.group(1)) != formula_count:
        raise CliError(
            f"公式 SVG 数量不匹配：期望 {formula_count}，"
            f"实际 {match.group(1) if match else '未知'}"
        )
    hits = re.search(r"svg_math_cache_hits=(\d+)", result.stdout)
    misses = re.search(r"svg_math_cache_misses=(\d+)", result.stdout)
    if not hits or not misses:
        raise CliError("GladTeX 未报告公式缓存统计")
    filtered_document = json.loads(filter_output.read_text(encoding="utf-8"))
    svgs = []
    generated_svgs = pandoc_svg_images(filtered_document) - source_svgs
    for source_path in sorted(generated_svgs):
        candidate = (formula_dir / source_path).resolve()
        try:
            candidate.relative_to(formula_dir.resolve())
        except ValueError as error:
            raise CliError(f"公式 SVG 越出缓存目录：{source_path}") from error
        if not candidate.is_file():
            raise CliError(f"公式 SVG 不存在：{source_path}")
        svgs.append(candidate)
    if not svgs:
        raise CliError("GladTeX 未生成公式 SVG")
    for svg in svgs:
        normalize_formula_svg(svg)
    persist_svg_math_cache(formula_dir, cache_dir)
    shutil.copyfile(filter_output, output)
    print(
        f"svg_math_cache_seeded={seeded} "
        f"svg_math_cache_hits={hits.group(1)} "
        f"svg_math_cache_misses={misses.group(1)}"
    )
    return formula_count


def build_svg_math_epub(
    *,
    root: Path,
    work_path: Path,
    entry: Path,
    source_command: list[str],
    writer_options: list[str],
    source_cwd: Path,
    output: Path,
    source_epub: Path | None = None,
    seed_epub: Path | None = None,
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="svg-epub-", dir=output.parent
    ) as directory:
        temporary = Path(directory)
        source_ast = temporary / "source.json"
        filtered_ast = temporary / "filtered.json"
        raw_epub = temporary / "book.raw.epub"
        run(
            [*source_command, "--to=json", "-o", str(source_ast)],
            cwd=source_cwd,
        )
        render_svg_math_ast(
            root,
            work_path,
            source_ast,
            filtered_ast,
            temporary,
            source_epub,
            seed_epub,
        )
        resources = pandoc_resource_path(
            root,
            work_path,
            entry,
            temporary,
            temporary / "formulas",
        )
        run(
            [
                "pandoc",
                str(filtered_ast),
                "--from=json",
                "--to=epub3",
                "--standalone",
                f"--resource-path={resources}",
                *writer_options,
                "-o",
                str(raw_epub),
            ],
            cwd=temporary,
        )
        normalize_epub_navigation(raw_epub, output)


def build_typst(work: Work, language: Language, target: str, output: Path) -> None:
    if target == "pdf":
        run(
            [
                "typst",
                "compile",
                "--root",
                str(work.root),
                str(language.entry),
                str(output),
            ],
            cwd=work.root,
        )
        return

    title, author, identifier = epub_metadata(work, language)
    date = epub_date(work)
    with tempfile.TemporaryDirectory(
        prefix="typst-epub-", dir=output.parent
    ) as directory:
        temporary = Path(directory)
        raw_html = temporary / "book.html"
        normalized_html = temporary / "book-normalized.html"
        cover = explicit_epub_cover_path(work)
        generated_epub = temporary / "book.epub"
        run(
            [
                "typst",
                "compile",
                "--features",
                "html",
                "--format",
                "html",
                "--root",
                str(work.root),
                str(language.entry),
                str(raw_html),
            ],
            cwd=work.root,
        )
        text = raw_html.read_text(encoding="utf-8")
        validate_html(text, str(raw_html))
        normalized_html.write_text(normalize_typst_html(text), encoding="utf-8")
        if cover is None:
            cover = temporary / "cover.png"
            run(
                [
                    "typst",
                    "compile",
                    "--root",
                    str(work.root),
                    "--format",
                    "png",
                    "--pages",
                    "1",
                    "--ppi",
                    "216",
                    str(language.entry),
                    str(cover),
                ],
                cwd=work.root,
            )
            png_dimensions(cover)
        command = [
            "pandoc",
            str(normalized_html),
            "--from=html",
        ]
        writer_options = [
            "--toc",
            "--toc-depth=3",
            "--split-level=1",
            f"--css={work.root / 'formats/epub/book.css'}",
            f"--metadata=title:{title}",
            f"--metadata=lang:{language.code}",
            f"--metadata=identifier:{identifier}",
            f"--epub-cover-image={cover}",
        ]
        if author:
            writer_options.append(f"--metadata=author:{author}")
        if date:
            writer_options.append(f"--metadata=date:{date}")
        build_svg_math_epub(
            root=work.root,
            work_path=work.path,
            entry=language.entry,
            source_command=command,
            writer_options=writer_options,
            source_cwd=work.root,
            output=generated_epub,
            source_epub=work.source,
            seed_epub=language.outputs.get("epub"),
        )
        shutil.copyfile(generated_epub, output)


def pandoc_base(work: Work, language: Language) -> list[str]:
    resource_path = pandoc_resource_path(
        work.root,
        work.path,
        language.entry,
    )
    return [
        "pandoc",
        str(language.entry),
        "--from=markdown+fenced_divs+bracketed_spans+footnotes-raw_html",
        f"--lua-filter={work.root / 'formats/pandoc/semantics.lua'}",
        f"--resource-path={resource_path}",
        f"--metadata=lang:{language.code}",
    ]


def explicit_epub_cover_path(work: Work) -> Path | None:
    epub_data = work.manifest.get("epub")
    cover_raw = epub_data.get("cover") if isinstance(epub_data, dict) else None
    if isinstance(cover_raw, str) and cover_raw:
        return inside(work.path, cover_raw, "epub.cover")
    return None


def epub_cover_path(work: Work) -> Path | None:
    explicit = explicit_epub_cover_path(work)
    if explicit is not None:
        return explicit
    fallback = work.path / "assets" / "cover.png"
    return fallback if fallback.is_file() else None


def build_markdown(work: Work, language: Language, target: str, output: Path) -> None:
    command = pandoc_base(work, language)
    if target == "html":
        command.extend(
            [
                "--to=html5",
                "--standalone",
                "--embed-resources",
                f"--css={work.root / 'formats/html/book.css'}",
            ]
        )
    elif target == "epub":
        title, author, identifier = epub_metadata(work, language)
        date = epub_date(work)
        writer_options = [
            "--toc",
            "--toc-depth=3",
            "--split-level=2",
            f"--css={work.root / 'formats/epub/book.css'}",
            f"--metadata=title:{title}",
            f"--metadata=identifier:{identifier}",
        ]
        if author:
            writer_options.append(f"--metadata=author:{author}")
        if date:
            writer_options.append(f"--metadata=date:{date}")
        cover = epub_cover_path(work)
        if cover and cover.is_file():
            writer_options.append(f"--epub-cover-image={cover}")
        build_svg_math_epub(
            root=work.root,
            work_path=work.path,
            entry=language.entry,
            source_command=command,
            writer_options=writer_options,
            source_cwd=work.root,
            output=output,
            source_epub=work.source,
            seed_epub=language.outputs.get("epub"),
        )
        return
    else:
        command.extend(["--pdf-engine=typst"])
    command.extend(["-o", str(output)])
    run(command, cwd=work.root)


def validate_html(text: str, label: str) -> None:
    parser = BookHTMLParser()
    parser.feed(text)
    parser.close()
    if not parser.has_html_start or not parser.has_html_end:
        raise CliError(f"HTML 必须是完整文档：{label}")


def build_html(work: Work, language: Language, output: Path) -> None:
    text = language.entry.read_text(encoding="utf-8")
    validate_html(text, str(language.entry))
    marker = "<!-- translator:book-css -->"
    if text.count(marker) > 1:
        raise CliError(f"HTML CSS 标记只能出现一次：{language.entry}")
    if marker in text:
        css = (work.root / "formats/html/book.css").read_text(encoding="utf-8")
        text = text.replace(
            marker, f'<style id="translator-book-css">\n{css}\n</style>'
        )
    output.write_text(text, encoding="utf-8")


def latex_epub_source_text(entry: Path, work_path: Path) -> str:
    return normalize_latex_source_page_lines(
        normalize_latex_counter_formats(
            normalize_latex_split_footnotes(
                normalize_latex_layout_environments(
                    expand_latex(entry, work_path, normalize_graphics=True)
                )
            )
        )
    )


def latex_source_has_part(text: str) -> bool:
    return re.search(
        r"\\part(?![A-Za-z@])\s*\*?\s*(?:\[[^\]]*\]\s*)?\{",
        mask_latex_comments(text),
    ) is not None


def latex_epub_pandoc_command(
    *, root: Path, work_path: Path, entry: Path, expanded: Path, has_part: bool = False
) -> list[str]:
    resource_path = os.pathsep.join((str(entry.parent), str(work_path), str(root)))
    command = [
        "pandoc",
        str(expanded),
        "--from=latex+raw_tex",
        f"--lua-filter={root / 'formats/pandoc/latex.lua'}",
        f"--resource-path={resource_path}",
    ]
    if has_part:
        command.append("--metadata=translator-latex-has-part:true")
    return command


def preflight_latex_epub(work: Work, language: Language) -> None:
    with tempfile.TemporaryDirectory(
        prefix="latex-epub-preflight-",
        dir=temp_root(work.root),
    ) as directory:
        temporary = Path(directory)
        expanded = temporary / "expanded.tex"
        ast = temporary / "source.json"
        source_text = latex_epub_source_text(language.entry, work.path)
        expanded.write_text(source_text, encoding="utf-8")
        run(
            [
                *latex_epub_pandoc_command(
                    root=work.root,
                    work_path=work.path,
                    entry=language.entry,
                    expanded=expanded,
                    has_part=latex_source_has_part(source_text),
                ),
                "--to=json",
                "-o",
                str(ast),
            ],
            cwd=work.path,
        )


def build_latex_epub_source(
    *,
    root: Path,
    work_path: Path,
    entry: Path,
    output: Path,
    title: str,
    author: str | None,
    language: str,
    identifier: str,
    cover: Path,
    date: str | None = None,
    seed_epub: Path | None = None,
) -> None:
    entry = entry.resolve()
    cover = cover.resolve()
    output = output.resolve(strict=False)
    if entry.suffix.lower() != ".tex" or not entry.is_file():
        raise CliError(f"LaTeX EPUB 入口必须是现存 .tex 文件：{entry}")
    if output.suffix.lower() != ".epub":
        raise CliError(f"LaTeX EPUB 输出必须使用 .epub 扩展名：{output}")
    if not cover.is_file():
        raise CliError(f"LaTeX EPUB 封面不存在：{cover}")
    if output in {entry, cover}:
        raise CliError(f"LaTeX EPUB 输出不能覆盖输入：{output}")
    with tempfile.TemporaryDirectory(
        prefix="latex-epub-",
        dir=temp_root(root),
    ) as directory:
        temporary = Path(directory)
        expanded = temporary / "expanded.tex"
        normalized = temporary / "book.epub"
        source_text = latex_epub_source_text(entry, work_path)
        expanded.write_text(source_text, encoding="utf-8")
        has_part = latex_source_has_part(source_text)
        command = latex_epub_pandoc_command(
            root=root,
            work_path=work_path,
            entry=entry,
            expanded=expanded,
            has_part=has_part,
        )
        writer_options = [
            "--toc",
            "--toc-depth=3",
            f"--split-level={2 if has_part else 1}",
            "--top-level-division=chapter",
            f"--css={root / 'formats/epub/book.css'}",
            f"--css={root / 'formats/epub/latex.css'}",
            f"--metadata=title:{title}",
            f"--metadata=lang:{language}",
            f"--metadata=identifier:{identifier}",
            f"--epub-cover-image={cover}",
        ]
        if not has_part:
            writer_options.append("--number-sections")
        if author:
            writer_options.append(f"--metadata=author:{author}")
        if date:
            writer_options.append(f"--metadata=date:{date}")
        build_svg_math_epub(
            root=root,
            work_path=work_path,
            entry=entry,
            source_command=command,
            writer_options=writer_options,
            source_cwd=work_path,
            output=normalized,
            seed_epub=seed_epub,
        )
        shutil.copyfile(normalized, output)


def build_latex(work: Work, language: Language, target: str, output: Path) -> None:
    if target == "epub":
        cover = epub_cover_path(work)
        if cover is None:
            if work.source.suffix.lower() != ".pdf":
                raise CliError("LaTeX EPUB 缺少封面")
            cover = output.parent / f"{language.code}-cover.png"
            run(
                [
                    "mutool",
                    "draw",
                    "-q",
                    "-r",
                    "216",
                    "-o",
                    str(cover),
                    str(work.source),
                    "1",
                ]
            )
            png_dimensions(cover)
        title, author, identifier = epub_metadata(work, language)
        build_latex_epub_source(
            root=work.root,
            work_path=work.path,
            entry=language.entry,
            output=output,
            title=title,
            author=author,
            language=language.code,
            identifier=identifier,
            cover=cover,
            date=epub_date(work),
            seed_epub=language.outputs.get("epub"),
        )
        return

    environment = os.environ.copy()
    latex_paths = str(work.root / "formats" / "latex") + os.sep + os.sep
    work_paths = str(work.path) + os.sep + os.sep
    environment["TEXINPUTS"] = (
        latex_paths
        + os.pathsep
        + work_paths
        + os.pathsep
        + environment.get("TEXINPUTS", "")
    )
    with tempfile.TemporaryDirectory(
        prefix="latex-pdf-",
        dir=temp_root(work.root),
    ) as directory:
        build_dir = Path(directory)
        expanded = build_dir / language.entry.name
        expanded.write_text(
            normalize_latex_source_page_lines(
                expand_latex(language.entry, work.path)
            ),
            encoding="utf-8",
        )
        run(
            [
                "latexmk",
                "-xelatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                f"-outdir={build_dir}",
                str(expanded),
            ],
            cwd=language.entry.parent,
            env=environment,
        )
        pdf = build_dir / f"{language.entry.stem}.pdf"
        if not pdf.is_file():
            raise CliError(f"latexmk 未生成 PDF：{pdf}")
        shutil.copyfile(pdf, output)


def command_build(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    validate_tool_truncation_entries(work)
    jobs = select_jobs(work, args.lang, args.target)
    record_selected_jobs(args, jobs)
    if not getattr(args, "_preserve_summary_evidence", False):
        clear_evidence_dir(work, "final")
        clear_evidence_dir(work, "refresh")
    base = work_temp(work)
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=base) as directory:
        build_dir = Path(directory)
        for language, target, final_output in jobs:
            temporary_output = build_dir / f"{language.code}.{target}"
            if work.authoring_format == "typst":
                build_typst(work, language, target, temporary_output)
            elif work.authoring_format == "markdown":
                build_markdown(work, language, target, temporary_output)
            elif work.authoring_format == "html":
                build_html(work, language, temporary_output)
            else:
                build_latex(work, language, target, temporary_output)
            if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
                raise CliError(f"构建未生成有效文件：{language.code}/{target}")
            publish_atomic(temporary_output, final_output)
            print(f"built: {final_output}")
    return 0


def contact_sheets(page_files: list[Path], output: Path) -> list[Path]:
    tool = image_tool()
    if not tool:
        raise CliError("找不到 ImageMagick（magick 或 montage）")
    sheets = []
    for index in range(0, len(page_files), 50):
        batch = page_files[index : index + 50]
        sheet = output / f"contact-{index // 50 + 1:03d}.png"
        run(
            [
                *tool,
                *(str(path) for path in batch),
                "-thumbnail",
                "180x",
                "-tile",
                "5x10",
                "-geometry",
                "+8+12",
                str(sheet),
            ]
        )
        sheets.append(sheet)
    return sheets


def qa_pdf(work: Work, output: Path, evidence: Path, dpi: int) -> None:
    info = run(["mutool", "info", str(output)]).stdout
    outline = run(["mutool", "show", str(output), "outline"]).stdout
    (evidence / "info.txt").write_text(info, encoding="utf-8")
    (evidence / "outline.txt").write_text(outline, encoding="utf-8")
    pattern = evidence / "page-%04d.png"
    run(["mutool", "draw", "-q", "-r", str(dpi), "-o", str(pattern), str(output)])
    pages = sorted(evidence.glob("page-*.png"))
    if not pages:
        raise CliError(f"没有渲染出 PDF 页面：{output}")
    contact_sheets(pages, evidence)


def qa_html(output: Path, evidence: Path) -> None:
    text = output.read_text(encoding="utf-8")
    validate_html(text, str(output))
    if "<!-- translator:book-css -->" in text:
        raise CliError(f"HTML 输出仍含 CSS 标记：{output}")
    (evidence / "summary.txt").write_text(
        f"path={output}\nbytes={output.stat().st_size}\ncomplete_document=true\n",
        encoding="utf-8",
    )


def ensure_epubcheck_ready() -> None:
    if EPUBCHECK_JAR.is_file():
        return
    raise CliError(
        "EPUBCheck 未准备好；请依次运行："
        "git submodule update --init --depth 1 tools/epubcheck；"
        "uv run python scripts/setup_epubcheck.py"
    )


def run_epubcheck(output: Path, evidence: Path) -> None:
    ensure_epubcheck_ready()
    output_text = str(output)
    with tempfile.TemporaryDirectory(
        prefix="epubcheck-",
        dir=temp_root(),
    ) as directory:
        check_path = output
        if not output_text.isascii():
            check_path = Path(directory) / "book.epub"
            shutil.copyfile(output, check_path)
        command = ["java", "-jar", str(EPUBCHECK_JAR), str(check_path)]
        try:
            process_env = os.environ.copy()
            process_temp = str(temp_root())
            process_env["TEMP"] = process_temp
            process_env["TMP"] = process_temp
            result = subprocess.run(
                command,
                env=process_env,
                text=True,
                capture_output=True,
                errors="replace",
            )
        except FileNotFoundError as error:
            raise CliError("找不到命令：java") from error
    report = (result.stdout + result.stderr).strip()
    (evidence / "epubcheck.txt").write_text(report + "\n", encoding="utf-8")
    if result.returncode:
        detail = report[-4000:]
        raise CliError(f"EPUBCheck 失败（{result.returncode}）：{output}\n{detail}")


def epub_toc_outline(toc: ET.Element) -> str:
    def name(element: ET.Element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    lines: list[str] = []

    def visit(ordered_list: ET.Element, depth: int) -> None:
        for item in ordered_list:
            if name(item) != "li":
                continue
            label = next(
                (child for child in item if name(child) in {"a", "span"}), None
            )
            if label is not None:
                text = " ".join("".join(label.itertext()).split())
                href = label.attrib.get("href", "") if name(label) == "a" else ""
                lines.append(f"{depth}\t{text}\t{href}")
            nested = next((child for child in item if name(child) == "ol"), None)
            if nested is not None:
                visit(nested, depth + 1)

    root = next((child for child in toc if name(child) == "ol"), None)
    if root is not None:
        visit(root, 1)
    return "\n".join(lines) + ("\n" if lines else "")


def qa_epub(work: Work, language: Language, output: Path, evidence: Path) -> None:
    if not zipfile.is_zipfile(output):
        raise CliError(f"EPUB 不是有效 ZIP：{output}")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise CliError(f"EPUB 包含损坏条目：{output}")
        names = archive.namelist()
        if not names or names[0] != "mimetype":
            raise CliError(f"EPUB 的 mimetype 必须是第一个条目：{output}")
        mimetype = archive.getinfo("mimetype")
        if mimetype.compress_type != zipfile.ZIP_STORED:
            raise CliError(f"EPUB 的 mimetype 必须未压缩：{output}")
        if archive.read("mimetype") != b"application/epub+zip":
            raise CliError(f"EPUB mimetype 内容无效：{output}")
        required = {"META-INF/container.xml"}
        missing = sorted(required - set(names))
        if missing:
            raise CliError(f"EPUB 缺少容器文件：{', '.join(missing)}")
        try:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(
                element
                for element in container.iter()
                if element.tag.rsplit("}", 1)[-1] == "rootfile"
            )
            opf_path = rootfile.attrib["full-path"]
            package = ET.fromstring(archive.read(opf_path))
        except (ET.ParseError, KeyError, StopIteration, ValueError) as error:
            raise CliError(f"EPUB 容器或 OPF 无效：{output}") from error

        metadata = [
            element
            for element in package.iter()
            if element.tag.rsplit("}", 1)[-1] == "metadata"
        ]
        if not metadata:
            raise CliError(f"EPUB 缺少 OPF metadata：{output}")
        metadata_text = list(metadata[0].iter())
        title_values = [
            "".join(element.itertext()).strip()
            for element in metadata_text
            if element.tag.rsplit("}", 1)[-1] == "title"
        ]
        language_values = [
            "".join(element.itertext()).strip()
            for element in metadata_text
            if element.tag.rsplit("}", 1)[-1] == "language"
        ]
        identifier_values = [
            "".join(element.itertext()).strip()
            for element in metadata_text
            if element.tag.rsplit("}", 1)[-1] == "identifier"
        ]
        author_values = [
            "".join(element.itertext()).strip()
            for element in metadata_text
            if element.tag.rsplit("}", 1)[-1] == "creator"
        ]
        expected_title, expected_author, expected_identifier = epub_metadata(
            work, language
        )
        if not any(expected_title == value for value in title_values):
            raise CliError(f"EPUB 标题元数据不匹配：{output}")
        if language.code not in language_values:
            raise CliError(f"EPUB 语言元数据不匹配：{output}")
        if expected_identifier not in identifier_values:
            raise CliError(f"EPUB 标识符元数据不匹配：{output}")
        if expected_author and expected_author not in author_values:
            raise CliError(f"EPUB 作者元数据不匹配：{output}")

        manifest = [
            element
            for element in package.iter()
            if element.tag.rsplit("}", 1)[-1] == "item"
        ]
        manifest_by_id = {element.attrib.get("id"): element for element in manifest}

        def member_for(href: str, base_path: str = opf_path) -> str:
            clean_href = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
            if not clean_href:
                return base_path
            return posixpath.normpath(
                posixpath.join(posixpath.dirname(base_path), clean_href)
            )

        manifest_by_member = {
            member_for(item.attrib.get("href", "")): item
            for item in manifest
            if item.attrib.get("href")
        }
        if any(
            "mathml" in item.attrib.get("properties", "").split() for item in manifest
        ):
            raise CliError(f"EPUB manifest 不得声明 MathML：{output}")

        for item in manifest:
            href = item.attrib.get("href")
            if not href or href.startswith(("http://", "https://")):
                continue
            member = member_for(href)
            if member not in names:
                raise CliError(f"EPUB 资源引用不存在：{member}")

        spine = [
            element
            for element in package.iter()
            if element.tag.rsplit("}", 1)[-1] == "spine"
        ]
        if not spine:
            raise CliError(f"EPUB 缺少 spine：{output}")
        spine_ids = [
            element.attrib.get("idref")
            for element in spine[0]
            if element.tag.rsplit("}", 1)[-1] == "itemref"
        ]
        if not spine_ids or any(
            identifier not in manifest_by_id for identifier in spine_ids
        ):
            raise CliError(f"EPUB spine 引用无效：{output}")

        nav_items = [
            item
            for item in manifest
            if "nav" in item.attrib.get("properties", "").split()
        ]
        if not nav_items:
            raise CliError(f"EPUB 没有 manifest 导航项：{output}")
        nav_href = nav_items[0].attrib.get("href")
        if not nav_href:
            raise CliError(f"EPUB 导航项缺少 href：{output}")
        nav_member = member_for(nav_href)
        if nav_member not in names:
            raise CliError(f"EPUB 导航文档不存在：{nav_member}")
        try:
            nav_data = archive.read(nav_member)
            navigation = ET.fromstring(nav_data)
        except ET.ParseError as error:
            raise CliError(f"EPUB 导航文档无效：{output}") from error
        if any(
            element.tag.rsplit("}", 1)[-1] == "math"
            or element.tag.startswith(f"{{{MATHML_NAMESPACE}}}")
            for element in navigation.iter()
        ):
            raise CliError(f"EPUB 导航不得包含 MathML：{nav_member}")
        toc_navs = []
        for element in navigation.iter():
            if element.tag.rsplit("}", 1)[-1] != "nav":
                continue
            epub_type = next(
                (
                    value
                    for key, value in element.attrib.items()
                    if key.rsplit("}", 1)[-1] == "type"
                ),
                "",
            )
            if "toc" in epub_type.split():
                toc_navs.append(element)
        if not toc_navs:
            raise CliError(f"EPUB 导航缺少 toc nav：{output}")
        toc_outline = epub_toc_outline(toc_navs[0])
        if not toc_outline:
            raise CliError(f"EPUB toc nav 没有可审阅的层级：{output}")
        if any(
            element.tag.rsplit("}", 1)[-1] == "span"
            and not "".join(element.itertext()).strip()
            for element in toc_navs[0].iter()
        ):
            raise CliError(f"EPUB toc nav 含空 span：{output}")
        nav_links = [
            element.attrib.get("href", "")
            for element in toc_navs[0].iter()
            if element.tag.rsplit("}", 1)[-1] == "a"
        ]
        nav_links = [
            href
            for href in nav_links
            if href and not href.startswith(("http://", "https://"))
        ]
        if not nav_links:
            raise CliError(f"EPUB toc nav 没有链接：{output}")
        for href in nav_links:
            member = member_for(href, nav_member)
            if member not in names:
                raise CliError(f"EPUB 导航链接不存在：{member}")
        content_files = [
            item
            for item in manifest
            if item.attrib.get("media-type") in {"application/xhtml+xml", "text/html"}
            and "nav" not in item.attrib.get("properties", "").split()
        ]
        if not content_files:
            raise CliError(f"EPUB 没有正文文档：{output}")
        documents = {nav_member: navigation}
        image_references: list[str] = []
        formula_references: list[str] = []
        formula_svg_references: list[str] = []
        epub_type = f"{{{EPUB_NAMESPACE}}}type"
        for item in content_files:
            member = member_for(item.attrib.get("href", ""))
            try:
                content_data = archive.read(member)
                document = ET.fromstring(content_data)
                artifact = visible_markup_artifact(document)
                if artifact:
                    raise CliError(
                        f"EPUB 正文含照字显示的 HTML 标记 {artifact!r}：{member}"
                    )
                if any(
                    element.tag.rsplit("}", 1)[-1] == "math"
                    or element.tag.startswith(f"{{{MATHML_NAMESPACE}}}")
                    for element in document.iter()
                ):
                    raise CliError(f"EPUB 正文不得包含 MathML：{member}")
                structure_problems = epub_structure_problems(document)
                if structure_problems:
                    raise CliError(
                        f"EPUB 正文结构错误：{member}；{structure_problems[0]}"
                    )
                for element in document.iter():
                    if element.tag.rsplit("}", 1)[-1] != "img":
                        continue
                    classes = set(element.attrib.get("class", "").split())
                    image_member = member_for(element.attrib.get("src", ""), member)
                    image_item = manifest_by_member.get(image_member)
                    if image_member not in names or image_item is None:
                        raise CliError(f"EPUB 图像资源不存在：{image_member}")
                    media_type = image_item.attrib.get("media-type", "")
                    if not media_type.startswith("image/"):
                        raise CliError(f"EPUB 图像资源不是图像：{image_member}")
                    image_references.append(image_member)
                    if not classes.intersection(FORMULA_CLASSES):
                        problem = nonformula_image_alt_problem(element)
                        if problem:
                            raise CliError(f"EPUB 非公式图片{problem}：{member}")
                        continue
                    if not element.attrib.get("alt", "").strip():
                        raise CliError(f"EPUB 公式图片缺少替代文本：{member}")
                    formula_references.append(image_member)
                    if media_type == "image/svg+xml":
                        formula_svg_references.append(image_member)
                footnotes = {
                    element.attrib["id"]: element
                    for element in document.iter()
                    if element.tag.rsplit("}", 1)[-1] == "aside"
                    and "footnote" in element.attrib.get(epub_type, "").split()
                    and element.get("id")
                }
                for element in document.iter():
                    if (
                        element.tag.rsplit("}", 1)[-1] != "a"
                        or "noteref" not in element.attrib.get(epub_type, "").split()
                    ):
                        continue
                    parsed = urllib.parse.urlsplit(element.attrib.get("href", ""))
                    footnote_id = urllib.parse.unquote(parsed.fragment)
                    reference_id = element.get("id")
                    footnote = footnotes.get(footnote_id)
                    if parsed.path or footnote is None or not reference_id:
                        raise CliError(f"EPUB 脚注链接无有效目标：{member}")
                    if not any(
                        child.tag.rsplit("}", 1)[-1] == "a"
                        and "backlink" in child.attrib.get(epub_type, "").split()
                        and child.attrib.get("href") == f"#{reference_id}"
                        for child in footnote.iter()
                    ):
                        raise CliError(f"EPUB 脚注缺少返回正文链接：{member}")
                documents[member] = document
            except (ET.ParseError, KeyError) as error:
                raise CliError(f"EPUB 正文文档无效：{member}") from error
        for member in sorted(set(formula_svg_references)):
            try:
                svg = ET.fromstring(archive.read(member))
            except (ET.ParseError, KeyError) as error:
                raise CliError(f"EPUB 公式 SVG 无效：{member}") from error
            if (
                svg.tag != f"{{{SVG_NAMESPACE}}}svg"
                or len(svg.attrib.get("viewBox", "").split()) != 4
            ):
                raise CliError(f"EPUB 公式 SVG 缺少 viewBox：{member}")
            svg_text = "".join(svg.itertext())
            if not all(
                marker in svg_text
                for marker in (
                    "math-background",
                    "math-foreground",
                    "prefers-color-scheme:dark",
                )
            ):
                raise CliError(f"EPUB 公式 SVG 缺少主题样式：{member}")
        for base_member, document in documents.items():
            for element in document.iter():
                if element.tag.rsplit("}", 1)[-1] != "a":
                    continue
                href = element.attrib.get("href", "")
                parsed = urllib.parse.urlsplit(href)
                if not href or parsed.scheme or href.startswith("//"):
                    continue
                member = member_for(href, base_member)
                if member not in names:
                    raise CliError(f"EPUB 链接目标不存在：{member}")
                fragment = urllib.parse.unquote(parsed.fragment)
                if fragment and member in documents:
                    identifiers = {
                        node.attrib["id"]
                        for node in documents[member].iter()
                        if node.attrib.get("id")
                    }
                    if fragment not in identifiers:
                        raise CliError(f"EPUB 链接片段不存在：{member}#{fragment}")
        cover_items = [
            item
            for item in manifest
            if "cover-image" in item.attrib.get("properties", "").split()
            or item.attrib.get("id") == "cover-image"
        ]
        cover_path = epub_cover_path(work)
        explicit_cover_path = explicit_epub_cover_path(work)
        if cover_path and not cover_path.is_file():
            raise CliError(f"EPUB 工作区封面不存在：{cover_path}")
        if not cover_items:
            raise CliError(f"EPUB 未嵌入封面：{output}")
        cover_item = cover_items[0]
        cover_member = member_for(cover_item.attrib.get("href", ""))
        if not cover_item.attrib.get("media-type", "").startswith("image/"):
            raise CliError(f"EPUB 封面不是图像资源：{output}")
        if cover_member not in names:
            raise CliError(f"EPUB 封面资源不存在：{cover_member}")
        if (
            explicit_cover_path is not None
            and archive.read(cover_member) != explicit_cover_path.read_bytes()
        ):
            raise CliError(f"EPUB 封面与清单声明不一致：{output}")
    outline_path = evidence / "outline.txt"
    outline_path.write_text(toc_outline, encoding="utf-8")
    run_epubcheck(output, evidence)
    (evidence / "summary.txt").write_text(
        f"path={output}\nbytes={output.stat().st_size}\nzip_ok=true\n"
        f"opf={opf_path}\ntitle={expected_title}\nlanguage={language.code}\n"
        f"identifier={expected_identifier}\nepubcheck=passed\n"
        f"content_files={len(content_files)}\nnav_items={len(nav_items)}\n"
        f"cover_items={len(cover_items)}\n"
        f"image_references={len(image_references)}\n"
        f"nonformula_images={len(image_references) - len(formula_references)}\n"
        f"formula_images={len(formula_references)}\n"
        f"formula_svg_resources={len(set(formula_svg_references))}\n"
        f"toc_outline_sha256={sha256(outline_path)}\n",
        encoding="utf-8",
    )


def command_qa(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    if not 72 <= args.dpi <= 300:
        raise CliError("--dpi 必须在 72 到 300 之间")
    jobs = select_jobs(work, args.lang, args.target)
    record_selected_jobs(args, jobs)
    if args.lang == "all" and args.target == "all":
        reset_evidence_dir(work, "qa")
    for language, target, output in jobs:
        if not output.is_file():
            raise CliError(f"正式输出不存在，请先 build：{output}")
        evidence = reset_evidence_child(
            work,
            "qa",
            re.sub(r"[^A-Za-z0-9._-]", "_", f"{language.code}-{target}"),
        )
        if target == "pdf":
            qa_pdf(work, output, evidence, args.dpi)
        elif target == "epub":
            qa_epub(work, language, output, evidence)
        else:
            qa_html(output, evidence)
        print(f"qa: {output} -> {evidence}")
    return 0


def command_browser_qa(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    jobs = select_jobs(work, args.lang, "epub")
    record_selected_jobs(args, jobs)
    if args.lang == "all":
        reset_evidence_dir(work, "browser-qa")
    for language, _, output in jobs:
        if not output.is_file():
            raise CliError(f"正式输出不存在，请先 build：{output}")
        evidence = reset_evidence_child(
            work,
            "browser-qa",
            re.sub(r"[^A-Za-z0-9._-]", "_", language.code),
            create=False,
        )
        result = run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "epub_browser_qa.py"),
                str(output),
                "--evidence",
                str(evidence),
                "--width",
                str(args.width),
                "--height",
                str(args.height),
                "--timeout",
                str(args.timeout),
                "--page-timeout",
                str(args.page_timeout),
                "--screenshots",
                str(args.screenshots),
            ]
        )
        print(result.stdout.strip())
        print(f"browser-qa: {output} -> {evidence}")
    return 0


def write_finalize_summary(
    work: Work,
    jobs: list[tuple[Language, str, Path]],
    final_dir: Path,
) -> Path:
    evidence_root = work_temp(work)
    outputs = []
    for language, target, output in jobs:
        qa_evidence = inside(
            evidence_root,
            "qa/" + re.sub(r"[^A-Za-z0-9._-]", "_", f"{language.code}-{target}"),
            "QA evidence",
        )
        browser_evidence = (
            inside(
                evidence_root,
                "browser-qa/" + re.sub(r"[^A-Za-z0-9._-]", "_", language.code),
                "browser evidence",
            )
            if target == "epub"
            else None
        )
        if not output.is_file() or not qa_evidence.is_dir():
            raise CliError(f"finalize 缺少输出或 QA 证据：{language.code}/{target}")
        if browser_evidence is not None and not browser_evidence.is_dir():
            raise CliError(f"finalize 缺少浏览器证据：{language.code}/{target}")
        qa_receipt = evidence_digest(qa_evidence)
        browser_receipt = (
            browser_acceptance_receipt(browser_evidence, output)
            if browser_evidence is not None
            else None
        )
        outputs.append(
            {
                "language": language.code,
                "target": target,
                "path": str(output.resolve()),
                "bytes": output.stat().st_size,
                "sha256": sha256(output),
                "qa_evidence": str(qa_evidence.resolve()),
                "browser_qa_evidence": (
                    str(browser_evidence.resolve())
                    if browser_evidence is not None
                    else None
                ),
                "qa_receipt": qa_receipt,
                "browser_qa_receipt": browser_receipt,
            }
        )

    manifest = work.path / "manifest.toml"
    summary = final_dir / "final.json"
    publish_json_atomic(
        summary,
        {
            "schema_version": 2,
            "work_id": work.work_id,
            "work": str(work.path.resolve()),
            "source": {
                "path": str(work.source.resolve()),
                "sha256": sha256(work.source),
            },
            "manifest": {
                "path": str(manifest.resolve()),
                "sha256": sha256(manifest),
                "build_sha256": manifest_build_sha256(manifest),
            },
            "outputs": outputs,
        },
    )
    return summary


def evidence_digest(directory: Path) -> dict[str, Any]:
    """Keep a content-free digest after disposable diagnostics are removed."""
    records = []
    for path in sorted(directory.rglob("*")):
        resolved = path.resolve()
        if not resolved.is_relative_to(directory.resolve()):
            raise CliError(f"验收证据越出允许目录：{path}")
        if path.is_file():
            records.append(
                (
                    path.relative_to(directory).as_posix(),
                    path.stat().st_size,
                    sha256(path),
                )
            )
    if not records:
        raise CliError(f"finalize 验收证据为空：{directory}")
    digest = (
        hashlib.sha256(json.dumps(records, ensure_ascii=False).encode("utf-8"))
        .hexdigest()
        .upper()
    )
    return {"status": "passed", "file_count": len(records), "sha256": digest}


def browser_acceptance_receipt(directory: Path, output: Path) -> dict[str, Any]:
    path = owned_child(directory.resolve(), "results.json", "browser results")
    if not path.is_file():
        raise CliError(f"缺少 browser results：{path}")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliError(f"浏览器验收结果无效：{path}") from error
    digest = sha256(output)
    names = set(epub_browser_qa.DEFAULT_MODE_NAMES)
    modes = result.get("modes", []) if isinstance(result, dict) else []
    if (
        not isinstance(modes, list)
        or len(modes) != 3
        or any(not isinstance(mode, dict) for mode in modes)
        or {mode.get("mode") for mode in modes} != names
        or result.get("mode_count") != 3
        or result.get("failures") != []
        or str(result.get("sha256")).upper() != digest
    ):
        raise CliError("finalize 要求同一成品哈希的完整浏览器模式矩阵")
    compact = []
    expected_settings = {mode["name"]: mode for mode in epub_browser_qa.default_modes()}
    count_keys = (
        "xhtml_count",
        "formula_count",
        "xhtml_link_count",
        "fragment_link_count",
        "noteref_target_checks",
        "backlink_target_checks",
        "noteref_navigation_checks",
        "backlink_navigation_checks",
    )
    for mode in modes:
        if (
            str(mode.get("sha256")).upper() != digest
            or mode.get("failures") != []
            or not isinstance(mode.get("xhtml_count"), int)
            or mode["xhtml_count"] < 1
            or mode["xhtml_count"] != result.get("xhtml_count")
            or mode.get("settings") != expected_settings[mode["mode"]]
            or mode.get("viewport") != expected_settings[mode["mode"]]["viewport"]
            or any(
                type(mode.get(key)) is not int or mode[key] < 0 for key in count_keys
            )
        ):
            raise CliError("浏览器模式结果未完整通过或哈希不一致")
        keys = ("mode", "settings", "viewport", "sha256", *count_keys)
        compact.append({key: mode.get(key) for key in keys})
    return {"status": "passed", "results_sha256": sha256(path), "modes": compact}

def load_current_final_summary(work: Work) -> tuple[Path, dict[str, Any]]:
    path = owned_child(evidence_dir(work, "final"), "final.json", "final receipt")
    if not path.is_file():
        raise CliError("缺少当前 finalize 证据；请先运行 finalize")
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliError(f"无法读取 finalize 证据：{error}") from error
    if not isinstance(summary, dict) or summary.get("work_id") != work.work_id:
        raise CliError("finalize 证据与当前作品不匹配")

    manifest = work.path / "manifest.toml"
    source_record = summary.get("source")
    if not isinstance(source_record, dict) or not isinstance(
        source_record.get("sha256"), str
    ):
        raise CliError("finalize 证据缺少来源哈希")
    if not work.source.is_file() or source_record["sha256"].upper() != sha256(
        work.source
    ):
        raise CliError("finalize 后来源已变化；请重新运行 finalize")

    manifest_record = summary.get("manifest")
    if not isinstance(manifest_record, dict) or not isinstance(
        manifest_record.get("sha256"), str
    ):
        raise CliError("finalize 证据缺少 manifest 哈希")
    manifest_matches = (
        manifest.is_file()
        and manifest_record["sha256"].upper() == sha256(manifest)
    )
    build_hash = manifest_record.get("build_sha256")
    build_matches = (
        manifest.is_file()
        and isinstance(build_hash, str)
        and build_hash.upper() == manifest_build_sha256(manifest)
    )
    if not manifest_matches and not build_matches:
        raise CliError("finalize 后 manifest 已变化；请重新运行 finalize")
    return path, summary


def current_final_output(
    work: Work,
    language: Language,
    target: str,
    output: Path,
    summary: dict[str, Any],
) -> tuple[int, str]:
    records = summary.get("outputs")
    if not isinstance(records, list):
        raise CliError("finalize 证据缺少输出清单")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("language") == language.code
        and record.get("target") == target
    ]
    if len(matches) != 1:
        raise CliError(f"finalize 证据未唯一绑定输出：{language.code}/{target}")
    record = matches[0]
    try:
        recorded_path = Path(str(record["path"])).resolve(strict=False)
        recorded_bytes = int(record["bytes"])
        recorded_hash = str(record["sha256"]).upper()
    except (KeyError, TypeError, ValueError) as error:
        raise CliError("finalize 输出记录无效") from error
    if recorded_path != output.resolve(strict=False):
        raise CliError(f"finalize 输出路径已变化：{language.code}/{target}")
    if not output.is_file():
        raise CliError(f"正式输出不存在：{output}")
    if output.stat().st_size != recorded_bytes or sha256(output) != recorded_hash:
        raise CliError(f"finalize 后正式输出已变化：{language.code}/{target}")
    return recorded_bytes, recorded_hash


def delivery_evidence_key(language_code: str, target: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{language_code}-{target}")


def publish_json_atomic(target: Path, value: Any) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="json-", dir=target.parent) as directory:
        candidate = Path(directory) / target.name
        candidate.write_bytes(payload)
        publish_atomic(
            candidate,
            target,
            expected_bytes=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest().upper(),
        )


def command_deliver(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    manifest_state = str(work.manifest["work"]["status"])
    document_state = status_document_state(work.path / "STATUS.md")
    if manifest_state != "complete" or document_state != "complete":
        raise CliError(
            "deliver 要求 manifest 与 STATUS.md 均已完成；请先运行 complete"
        )
    jobs = select_jobs(work, args.lang, args.target)
    if len(jobs) != 1:
        raise CliError("deliver 必须用 --lang/--target 唯一选择一个正式输出")
    record_selected_jobs(args, jobs)
    language, target, output = jobs[0]
    final_path, final_summary = load_current_final_summary(work)
    expected_bytes, expected_hash = current_final_output(
        work, language, target, output, final_summary
    )

    destination = Path(args.to).expanduser().resolve(strict=False)
    if destination == output.resolve(strict=False):
        raise CliError("交付目标不能与正式输出相同")
    if destination.exists() and destination.is_dir():
        raise CliError("--to 必须是明确的目标文件，不是目录")
    if not destination.parent.is_dir():
        raise CliError(f"交付目标目录不存在：{destination.parent}")
    if destination.suffix.lower() != output.suffix.lower():
        raise CliError(
            f"交付目标扩展名必须为 {output.suffix.lower()}：{destination}"
        )

    delivery_base = evidence_dir(work, "delivery")
    delivery_dir = owned_child(delivery_base, delivery_evidence_key(language.code, target), "交付回执目录")
    delivery_dir.mkdir(parents=True, exist_ok=True)
    evidence = delivery_dir / "delivery.json"
    attempt = delivery_dir / "attempt.json"
    started_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    receipt = {
        "schema_version": 1,
        "status": "prepared",
        "work_id": work.work_id,
        "started_at": started_at,
        "final_summary": {
            "path": str(final_path.resolve()),
            "sha256": sha256(final_path),
        },
        "output": {
            "language": language.code,
            "target": target,
            "source": str(output.resolve()),
            "destination": str(destination),
            "bytes": expected_bytes,
            "sha256": expected_hash,
        },
    }
    publish_json_atomic(attempt, receipt)
    publish_atomic(
        output,
        destination,
        expected_bytes=expected_bytes,
        expected_sha256=expected_hash,
    )
    delivered_bytes = destination.stat().st_size
    delivered_hash = sha256(destination)
    if delivered_bytes != expected_bytes or delivered_hash != expected_hash:
        raise CliError("交付后大小或 SHA-256 与 finalize 证据不一致")

    receipt["status"] = "complete"
    receipt["completed_at"] = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    publish_json_atomic(evidence, receipt)
    try:
        attempt.unlink(missing_ok=True)
    except OSError as error:
        print(f"WARNING: 无法清理交付 attempt 证据：{error}", file=sys.stderr)
    print(f"deliver: ok -> {destination} ({delivered_bytes} bytes, {delivered_hash})")
    print(f"delivery evidence: {evidence}")
    return 0


def command_complete(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    manifest = work.path / "manifest.toml"
    manifest_state = str(work.manifest["work"]["status"])
    document_state = status_document_state(work.path / "STATUS.md")
    if manifest_state == "complete":
        if document_state != "complete":
            raise CliError(
                "manifest 已为 complete，但 STATUS.md 未明确完成；请先修正状态记录"
            )
        print(f"complete: already complete ({work.work_id})")
        return 0
    if manifest_state != "active":
        raise CliError("只有 active 作品可以收口为 complete")
    if document_state != "complete":
        raise CliError("STATUS.md 尚未明确本地完成；complete 不替代人工验收")

    jobs = select_jobs(work, "all", "all")
    record_selected_jobs(args, jobs)
    final_path, final_summary = load_current_final_summary(work)
    records = final_summary.get("outputs")
    if not isinstance(records, list):
        raise CliError("finalize 证据缺少输出清单")
    expected_keys = {(language.code, target) for language, target, _ in jobs}
    recorded_keys = {
        (str(record.get("language")), str(record.get("target")))
        for record in records
        if isinstance(record, dict)
    }
    if recorded_keys != expected_keys or len(records) != len(jobs):
        raise CliError("finalize 输出清单与当前 manifest 不一致")
    accepted_outputs = []
    for language, target, output in jobs:
        output_bytes, output_hash = current_final_output(
            work, language, target, output, final_summary
        )
        accepted_outputs.append(
            {
                "language": language.code,
                "target": target,
                "path": str(output.resolve()),
                "bytes": output_bytes,
                "sha256": output_hash,
            }
        )

    original_bytes = manifest.read_bytes()
    original_hash = hashlib.sha256(original_bytes).hexdigest().upper()
    original_build_hash = manifest_build_sha256(manifest)
    updated_text = replace_manifest_work_status(
        original_bytes.decode("utf-8"), "complete"
    )
    try:
        updated_manifest = tomllib.loads(updated_text)
    except tomllib.TOMLDecodeError as error:
        raise CliError(f"完成状态候选 manifest 无效：{error}") from error
    if updated_manifest.get("work", {}).get("status") != "complete":
        raise CliError("完成状态候选 manifest 未写入 complete")

    base = work_temp(work)
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="complete-", dir=base) as directory:
        candidate = Path(directory) / "manifest.toml"
        rollback = Path(directory) / "manifest.rollback.toml"
        candidate.write_bytes(updated_text.encode("utf-8"))
        rollback.write_bytes(original_bytes)
        updated_hash = sha256(candidate)
        updated_build_hash = manifest_build_sha256(candidate)
        if updated_build_hash != original_build_hash:
            raise CliError("完成状态迁移意外改变了构建配置")
        publish_atomic(
            candidate,
            manifest,
            expected_bytes=candidate.stat().st_size,
            expected_sha256=updated_hash,
        )
        try:
            if sha256(manifest) != updated_hash or status_document_state(
                work.path / "STATUS.md"
            ) != "complete":
                raise CliError("完成状态写入后的复核失败")
            completed_work = load_work(work.path)
            _, completed_summary = load_current_final_summary(completed_work)
            for language, target, output in select_jobs(
                completed_work, "all", "all"
            ):
                current_final_output(
                    completed_work,
                    language,
                    target,
                    output,
                    completed_summary,
                )

            evidence = evidence_dir(work, "completion") / "completion.json"
            publish_json_atomic(
                evidence,
                {
                    "schema_version": 1,
                    "work_id": work.work_id,
                    "completed_at": datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                    "final_summary": {
                        "path": str(final_path.resolve()),
                        "sha256": sha256(final_path),
                    },
                    "manifest": {
                        "path": str(manifest.resolve()),
                        "before_sha256": original_hash,
                        "after_sha256": updated_hash,
                        "build_sha256": updated_build_hash,
                    },
                    "outputs": accepted_outputs,
                },
            )
        except BaseException:
            if manifest.is_file() and sha256(manifest) == updated_hash:
                try:
                    publish_atomic(
                        rollback,
                        manifest,
                        expected_bytes=len(original_bytes),
                        expected_sha256=original_hash,
                    )
                except BaseException as rollback_error:
                    raise CliError(
                        "完成状态复核失败，且 manifest 自动回滚失败"
                    ) from rollback_error
            raise
    print(f"complete: ok ({work.work_id}) -> {manifest}")
    print(f"completion evidence: {evidence}")
    return 0


def write_refresh_summary(
    work: Work,
    jobs: list[tuple[Language, str, Path]],
    steps: list[dict[str, Any]],
    refresh_dir: Path,
) -> Path:
    summary = refresh_dir / "refresh.json"
    summary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "incremental-refresh",
                "work_id": work.work_id,
                "completed_at": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "outputs": [
                    {
                        "language": language.code,
                        "target": target,
                        "path": output.relative_to(work.root).as_posix(),
                        "bytes": output.stat().st_size,
                        "sha256": sha256(output),
                    }
                    for language, target, output in jobs
                ],
                "steps": steps,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def command_prepare(args: argparse.Namespace) -> int:
    steps: list[dict[str, Any]] = []
    args._workflow_steps = steps
    work = load_command_work(args)
    entries = sorted(
        {
            language.entry
            for language in work.languages
            if language.code.lower().startswith("zh") and language.entry.suffix.lower() == ".md"
        }
    )
    for entry in entries:
        workflow_step(
            f"autocorrect-fix:{entry.relative_to(work.root).as_posix()}",
            lambda entry=entry: run(["autocorrect", "--fix", str(entry)]),
            steps,
        )
        workflow_step(
            f"autocorrect-lint:{entry.relative_to(work.root).as_posix()}",
            lambda entry=entry: run(["autocorrect", "--lint", str(entry)]),
            steps,
        )
    workflow_step(
        "check",
        lambda: command_check(argparse.Namespace(work=args.work)),
        steps,
    )
    print(f"prepare: ok ({len(entries)} 个中文 Markdown 入口)")
    return 0


def command_refresh(args: argparse.Namespace) -> int:
    steps: list[dict[str, Any]] = []
    args._workflow_steps = steps
    work = load_command_work(args)
    jobs = select_jobs(work, args.lang, args.target)
    record_selected_jobs(args, jobs)
    refresh_dir = reset_evidence_dir(work, "refresh")
    workflow_step(
        "check",
        lambda: command_check(argparse.Namespace(work=args.work)),
        steps,
    )
    clear_evidence_dir(work, "final")
    workflow_step(
        "build",
        lambda: command_build(
            argparse.Namespace(
                work=args.work,
                lang=args.lang,
                target=args.target,
                _preserve_summary_evidence=True,
            )
        ),
        steps,
    )
    workflow_step(
        "qa",
        lambda: command_qa(
            argparse.Namespace(
                work=args.work,
                lang=args.lang,
                target=args.target,
                dpi=QA_DEFAULT_DPI,
            )
        ),
        steps,
    )
    if args.target == "epub":
        workflow_step(
            "browser-qa",
            lambda: command_browser_qa(
                argparse.Namespace(
                    work=args.work,
                    lang=args.lang,
                    **BROWSER_QA_DEFAULTS,
                )
            ),
            steps,
        )
    summary = workflow_step(
        "evidence",
        lambda: write_refresh_summary(work, jobs, steps, refresh_dir),
        steps,
    )
    print(f"refresh: ok -> {summary}")
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    steps: list[dict[str, Any]] = []
    args._workflow_steps = steps
    work = load_command_work(args)
    jobs = select_jobs(work, "all", "all")
    record_selected_jobs(args, jobs)
    clear_evidence_dir(work, "refresh")
    final_dir = reset_evidence_dir(work, "final")
    workflow_step(
        "doctor",
        lambda: command_doctor(argparse.Namespace(work=args.work, purpose="all")),
        steps,
    )
    workflow_step(
        "check",
        lambda: command_check(argparse.Namespace(work=args.work)),
        steps,
    )
    workflow_step(
        "build",
        lambda: command_build(
            argparse.Namespace(
                work=args.work,
                lang="all",
                target="all",
                _preserve_summary_evidence=True,
            )
        ),
        steps,
    )
    workflow_step(
        "qa",
        lambda: command_qa(
            argparse.Namespace(
                work=args.work, lang="all", target="all", dpi=QA_DEFAULT_DPI
            )
        ),
        steps,
    )
    if any(target == "epub" for _, target, _ in jobs):
        workflow_step(
            "browser-qa",
            lambda: command_browser_qa(
                argparse.Namespace(
                    work=args.work,
                    lang="all",
                    **BROWSER_QA_DEFAULTS,
                )
            ),
            steps,
        )
    summary = workflow_step(
        "evidence",
        lambda: write_finalize_summary(work, jobs, final_dir),
        steps,
    )
    print(f"finalize: ok -> {summary}")
    return 0


def read_workflow_logs(root: Path) -> list[dict[str, Any]]:
    directory = workflow_runs_dir(root)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CliError(f"无法读取流程日志 {path.name}：{error}") from error
        if not isinstance(record, dict) or record.get("kind") != "workflow-run":
            raise CliError(f"流程日志 schema 无效：{path.name}")
        if record.get("schema_version") not in WORKFLOW_LOG_SCHEMAS:
            raise CliError(f"流程日志 schema 无效：{path.name}")
        record.setdefault("run_id", path.stem)
        records.append(record)
    return records


def benchmark_stats(samples: list[dict[str, Any]]) -> dict[str, int | float]:
    try:
        durations = sorted(float(sample["duration_seconds"]) for sample in samples)
    except (KeyError, TypeError, ValueError) as error:
        raise CliError("流程日志缺少有效 duration_seconds") from error
    passed = sum(sample.get("status") == "passed" for sample in samples)
    return {
        "runs": len(samples),
        "passed": passed,
        "failed": len(samples) - passed,
        "success_rate": round(passed / len(samples), 4),
        "total_seconds": round(sum(durations), 6),
        "min_seconds": round(durations[0], 6),
        "median_seconds": round(statistics.median(durations), 6),
        "p95_seconds": round(durations[math.ceil(len(durations) * 0.95) - 1], 6),
        "max_seconds": round(durations[-1], 6),
    }


def station_interval_stats(samples: list[dict[str, Any]]) -> dict[str, object]:
    elapsed: list[float] = []
    declared: list[float] = []
    for sample in samples:
        station = sample.get("station")
        interval = station.get("interval") if isinstance(station, dict) else None
        if not isinstance(interval, dict):
            continue
        if interval.get("elapsed_seconds") is not None:
            elapsed.append(float(interval["elapsed_seconds"]))
        if interval.get("declared_effort_upper_bound_seconds") not in {None, 0}:
            declared.append(float(interval["declared_effort_upper_bound_seconds"]))

    def summary(values: list[float]) -> dict[str, int | float] | None:
        if not values:
            return None
        values.sort()
        return {
            "samples": len(values),
            "total_seconds": round(sum(values), 6),
            "median_seconds": round(statistics.median(values), 6),
            "p95_seconds": round(values[math.ceil(len(values) * 0.95) - 1], 6),
            "max_seconds": round(values[-1], 6),
        }

    return {
        "elapsed_upper_bound": summary(elapsed),
        "declared_effort_upper_bound": summary(declared),
    }


def command_benchmark(args: argparse.Namespace) -> int:
    records = read_workflow_logs(REPO_ROOT)
    if args.work:
        records = [
            record
            for record in records
            if args.work in {record.get("work"), record.get("work_id")}
        ]

    command_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    step_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    outcome_groups: dict[str, list[dict[str, Any]]] = {}
    work_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    activity_groups: dict[str, list[dict[str, Any]]] = {}
    friction_counts: dict[str, int] = {}
    for record in records:
        revision = str(record.get("revision") or "unknown")
        command = str(record.get("command") or "unknown")
        command_groups.setdefault((command, revision), []).append(record)
        outcome_groups.setdefault(str(record.get("status") or "unknown"), []).append(record)
        work_groups.setdefault(
            (str(record.get("work") or "unknown"), str(record.get("work_id") or "unknown")),
            [],
        ).append(record)
        station = record.get("station")
        if isinstance(station, dict):
            activities = station.get("activity", [])
            if isinstance(activities, list):
                for activity in activities:
                    if isinstance(activity, str):
                        activity_groups.setdefault(activity, []).append(record)
            friction = station.get("friction", [])
            if isinstance(friction, list):
                for code in friction:
                    if isinstance(code, str):
                        friction_counts[code] = friction_counts.get(code, 0) + 1
        steps = record.get("steps", [])
        if not isinstance(steps, list):
            raise CliError("流程日志 steps 必须是数组")
        for step in steps:
            if not isinstance(step, dict) or not step.get("name"):
                raise CliError("流程日志含无效 step")
            step_groups.setdefault(
                (command, str(step["name"]), revision), []
            ).append(step)

    report = {
        "schema_version": WORKFLOW_LOG_SCHEMA,
        "log_directory": str(workflow_runs_dir(REPO_ROOT)),
        "work_filter": args.work,
        "samples": len(records),
        "commands": [
            {"command": command, "revision": revision, **benchmark_stats(samples)}
            for (command, revision), samples in sorted(command_groups.items())
        ],
        "finalize_steps": [
            {"step": step, "revision": revision, **benchmark_stats(samples)}
            for (command, step, revision), samples in sorted(step_groups.items())
            if command == "finalize"
        ],
        "steps": [
            {
                "command": command,
                "step": step,
                "revision": revision,
                **benchmark_stats(samples),
            }
            for (command, step, revision), samples in sorted(step_groups.items())
        ],
        "outcomes": [
            {"status": status, **benchmark_stats(samples)}
            for status, samples in sorted(outcome_groups.items())
        ],
        "works": [
            {"work": work, "work_id": work_id, **benchmark_stats(samples)}
            for (work, work_id), samples in sorted(work_groups.items())
        ],
        "stations": {
            "interval_max_attributable_seconds": STATION_INTERVAL_MAX_SECONDS,
            "activities": [
                {
                    "activity": activity,
                    "machine": benchmark_stats(samples),
                    "interval": station_interval_stats(samples),
                }
                for activity, samples in sorted(activity_groups.items())
            ],
            "friction": [
                {"code": code, "runs": count}
                for code, count in sorted(friction_counts.items())
            ],
            "improvement_candidates": [
                {
                    "kind": "repeated_activity",
                    "activity": activity,
                    "runs": len(samples),
                    "works": len(
                        {
                            str(sample.get("work_id") or sample.get("work") or "unknown")
                            for sample in samples
                        }
                    ),
                }
                for activity, samples in sorted(activity_groups.items())
                if activity not in {"none", "unknown"}
                and (
                    len(samples) >= 3
                    or len(
                        {
                            str(sample.get("work_id") or sample.get("work") or "unknown")
                            for sample in samples
                        }
                    )
                    >= 2
                )
            ],
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_compare_epub(args: argparse.Namespace) -> int:
    official_path = Path(args.official).resolve()
    candidate_path = Path(args.candidate).resolve()
    official = epub_member_hashes(official_path)
    candidate = epub_member_hashes(candidate_path)
    official_names = set(official)
    candidate_names = set(candidate)
    reader_state_names = sorted(
        (official_names | candidate_names) & EPUB_READER_STATE_MEMBERS
    )
    official_content_names = official_names - EPUB_READER_STATE_MEMBERS
    candidate_content_names = candidate_names - EPUB_READER_STATE_MEMBERS
    missing = sorted(official_content_names - candidate_content_names)
    unexpected_extra = sorted(candidate_content_names - official_content_names)
    raw_different_names = [
        name
        for name in sorted(official_content_names & candidate_content_names)
        if official[name] != candidate[name]
    ]
    volatile_metadata = [
        name
        for name in raw_different_names
        if name.lower().endswith(".opf")
        and normalized_opf_build_metadata(epub_member_bytes(official_path, name))
        == normalized_opf_build_metadata(epub_member_bytes(candidate_path, name))
    ]
    different = [
        {
            "path": name,
            "official_bytes": official[name][0],
            "candidate_bytes": candidate[name][0],
            "official_sha256": official[name][1],
            "candidate_sha256": candidate[name][1],
        }
        for name in raw_different_names
        if name not in volatile_metadata
    ]
    reader_state = [
        {
            "path": name,
            "official_present": name in official,
            "candidate_present": name in candidate,
            "identical": official.get(name) == candidate.get(name),
        }
        for name in reader_state_names
    ]
    content_equivalent = not missing and not different and not unexpected_extra
    reader_state_changed = any(not item["identical"] for item in reader_state)
    official_sha256 = sha256(official_path)
    candidate_sha256 = sha256(candidate_path)
    report = {
        "schema_version": 1,
        "official": {
            "path": str(official_path),
            "bytes": official_path.stat().st_size,
            "sha256": official_sha256,
            "members": len(official),
        },
        "candidate": {
            "path": str(candidate_path),
            "bytes": candidate_path.stat().st_size,
            "sha256": candidate_sha256,
            "members": len(candidate),
        },
        "package_identical": official_sha256 == candidate_sha256,
        "content_equivalent": content_equivalent,
        "reader_state_only": content_equivalent
        and reader_state_changed
        and not volatile_metadata,
        "volatile_metadata_only": content_equivalent
        and bool(volatile_metadata)
        and not reader_state_changed,
        "missing_members": missing,
        "different_members": different,
        "volatile_metadata_members": volatile_metadata,
        "reader_state_members": reader_state,
        "unexpected_extra_members": unexpected_extra,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if content_equivalent else 1


def command_clean(args: argparse.Namespace) -> int:
    work = load_command_work(args)
    base = temp_root(work.root)
    target = work_temp(work).resolve(strict=False)
    if target.parent != base:
        raise CliError(f"拒绝清理不安全的路径：{target}")
    if target.exists():
        shutil.rmtree(target)
        print(f"cleaned: {target}")
    else:
        print(f"clean: nothing to remove ({target})")
    return 0


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "%": r"\%",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def initial_entry(format_name: str, title: str, language: str) -> str:
    if format_name == "typst":
        return (
            '#import "/formats/typst/book.typ": standard-book\n'
            '#import "/formats/typst/semantics.typ": *\n\n'
            f'#show: body => standard-book(lang: "{language}", body)\n\n'
            f"= #text({quote_toml(title)})\n"
        )
    if format_name == "markdown":
        escaped = re.sub(r"([\\`*_\[\]<>#])", r"\\\1", title)
        return f"# {escaped}\n"
    if format_name == "html":
        escaped = (
            title.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        return (
            "<!doctype html>\n"
            f'<html lang="{language}">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            f"  <title>{escaped}</title>\n"
            "  <!-- translator:book-css -->\n"
            "</head>\n"
            f"<body><main><h1>{escaped}</h1></main></body>\n"
            "</html>\n"
        )
    escaped = latex_escape(title)
    return (
        "\\documentclass[12pt]{book}\n"
        "\\usepackage{translator}\n"
        f"\\title{{{escaped}}}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        "\\end{document}\n"
    )


def manifest_text(
    *,
    work_id: str,
    title: str,
    source: Path,
    root: Path,
    digest: str,
    pages: int,
    anchor_pages: list[int] | None,
    format_name: str,
    source_language: str,
    target_languages: list[str],
    targets: tuple[str, ...],
    source_units: bool = False,
) -> str:
    extension = {"typst": "typ", "markdown": "md", "html": "html", "latex": "tex"}[
        format_name
    ]
    lines = [
        "schema_version = 2",
        "",
        "[work]",
        f"id = {quote_toml(work_id)}",
        f"title = {quote_toml(title)}",
        'status = "planned"',
        "",
        "[source]",
        'path_base = "repository_root"',
        f"relative_path = {quote_toml(source.relative_to(root).as_posix())}",
        f"sha256 = {quote_toml(digest)}",
    ]
    if source_units:
        lines.extend(['format = "epub"', 'units = "source-units.tsv"', f"file_size_bytes = {source.stat().st_size}"])
    elif anchor_pages is None:
        lines.extend(
            [
                "",
                "[pdf]",
                f"pages = {pages}",
                f"file_size_bytes = {source.stat().st_size}",
            ]
        )
    else:
        lines.extend(
            [
                'format = "epub"',
                f"pages = {pages}",
                f"anchor_pages = {len(anchor_pages)}",
                f"anchor_set = {quote_toml(compress_page_spec(anchor_pages))}",
                f"file_size_bytes = {source.stat().st_size}",
            ]
        )
    lines.extend(["", "[authoring]", f"format = {quote_toml(format_name)}"])
    if format_name == "markdown" and "pdf" in targets:
        lines.append('pdf_engine = "typst"')
    if "epub" in targets:
        lines.extend(["", "[epub]", f"title = {quote_toml(title)}"])
        if format_name == "markdown":
            lines.append('cover = "assets/cover.png"')
    for index, language in enumerate([source_language, *target_languages]):
        role = "source" if index == 0 else "translation"
        outputs = ", ".join(
            f"{target} = {quote_toml(f'output/{work_id}.{language}.{target}')}"
            for target in targets
        )
        lines.extend(
            [
                "",
                "[[languages]]",
                f"code = {quote_toml(language)}",
                f"role = {quote_toml(role)}",
                f"entry = {quote_toml(f'{language}/main.{extension}')}",
            ]
        )
        if role == "translation":
            lines.append(f"glossary = {quote_toml(f'{language}/glossary.tsv')}")
        lines.append(f"outputs = {{ {outputs} }}")
    return "\n".join(lines) + "\n"


def command_init(args: argparse.Namespace) -> int:
    root = REPO_ROOT
    raw_source = Path(args.source)
    source = (raw_source if raw_source.is_absolute() else root / raw_source).resolve()
    try:
        source.relative_to(root)
    except ValueError as error:
        raise CliError(f"来源必须位于仓库内：{source}") from error
    if not source.is_file():
        raise CliError(f"来源不存在：{source}")
    if source.suffix.lower() not in {".pdf", ".epub"}:
        raise CliError("来源必须是 PDF 或 EPUB 文件")
    if not ID_RE.fullmatch(args.id):
        raise CliError("--id 只能包含小写字母、数字、点、下划线和短横线")
    if not args.title.strip() or "\r" in args.title or "\n" in args.title:
        raise CliError("--title 必须是非空单行文本")
    source_language = args.source_lang
    target_languages = args.target_lang or ["zh-CN"]
    for code in [source_language, *target_languages]:
        if not LANG_RE.fullmatch(code):
            raise CliError(f"语言代码无效：{code}")
    if source_language in target_languages or len(set(target_languages)) != len(
        target_languages
    ):
        raise CliError("源语言和目标语言代码必须唯一")
    targets = tuple(args.target or DEFAULT_TARGETS[args.format])
    if not set(targets) <= TARGETS[args.format]:
        raise CliError(f"{args.format} 不支持指定输出：{', '.join(targets)}")
    if len(set(targets)) != len(targets):
        raise CliError("--target 不得重复")

    work_value = args.work or str(root / "Works" / source.stem)
    work_path = resolve_work_path(work_value, root)
    if work_path.exists():
        raise CliError(f"工作目录已存在，不会覆盖：{work_path}")
    unit_rows = ()
    if source.suffix.lower() == ".pdf":
        pages, _ = mutool_pages(source)
        anchor_pages = None
    else:
        try:
            unit_rows = epub_source.read_source_units(source)
        except epub_source.EpubSourceError as error:
            raise CliError(str(error)) from error
        anchor_pages, _ = epub_page_anchors(source, allow_missing=True)
        if anchor_pages:
            pages = max(anchor_pages)
            unit_rows = ()
        else:
            pages = len(unit_rows)
            if args.format not in {"markdown", "html"}:
                raise CliError("没有固定页码的 EPUB 请使用 --format markdown 或 html；当前格式不支持 source-unit")
    digest = sha256(source)
    content = manifest_text(
        work_id=args.id,
        title=args.title,
        source=source,
        root=root,
        digest=digest,
        pages=pages,
        anchor_pages=anchor_pages,
        format_name=args.format,
        source_language=source_language,
        target_languages=target_languages,
        targets=targets,
        source_units=bool(unit_rows),
    )
    extension = {"typst": "typ", "markdown": "md", "html": "html", "latex": "tex"}[
        args.format
    ]
    try:
        work_path.mkdir(parents=True)
        (work_path / "assets").mkdir()
        (work_path / "output").mkdir()
        (work_path / "manifest.toml").write_text(content, encoding="utf-8")
        (work_path / "STATUS.md").write_text(
            f"# 《{args.title}》工作状态\n\n状态：planned\n\n已初始化，尚未开始来源视觉核对。\n",
            encoding="utf-8",
        )
        mapping_name = "source-units.tsv" if unit_rows else "page-map.tsv"
        with (work_path / mapping_name).open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            if unit_rows:
                writer.writerow(SOURCE_UNITS_HEADER)
                for row in unit_rows:
                    writer.writerow(tuple(row[key] for key in SOURCE_UNITS_HEADER))
            else:
                writer.writerow(PAGE_MAP_HEADER)
                for page in range(1, pages + 1):
                    writer.writerow((page, "", "", "", "", ""))
        for index, language in enumerate([source_language, *target_languages]):
            language_dir = work_path / language
            language_dir.mkdir()
            (language_dir / f"main.{extension}").write_text(
                initial_entry(args.format, args.title, language),
                encoding="utf-8",
            )
            if index:
                with (language_dir / "glossary.tsv").open(
                    "w", encoding="utf-8", newline=""
                ) as handle:
                    csv.writer(handle, delimiter="\t", lineterminator="\n").writerow(
                        GLOSSARY_HEADER
                    )
    except BaseException:
        if work_path.exists():
            shutil.rmtree(work_path)
        raise
    print(f"initialized: {work_path}")
    return 0


def command_station(_args: argparse.Namespace) -> int:
    print("station: ok")
    return 0


def station_label(value: str) -> str:
    if (
        value.startswith("/")
        or re.match(r"^[A-Za-z]:/", value)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}", value)
    ):
        raise argparse.ArgumentTypeError("必须是最多 80 字符的不透明标签")
    return value


def add_station_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--activity",
        action="append",
        required=True,
        choices=ACTIVITIES,
        help="自上一工站以来的活动；纯机器间隔使用 none",
    )
    command.add_argument("--scope", type=station_label, help="活动范围不透明标签")
    command.add_argument("--outcome", type=station_label, help="活动结果不透明标签")
    command.add_argument("--issue", type=station_label, help="流程问题不透明标签")
    command.add_argument("--actor", type=station_label, help="操作者不透明标识")
    command.add_argument(
        "--session",
        type=station_label,
        help="会话不透明标识；默认 CODEX_THREAD_ID",
    )
    command.add_argument("--idle", action="store_true", help="间隔主要为等待，不计入投入")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="translator")
    commands = root.add_subparsers(dest="command", required=True)

    station = commands.add_parser("station", help="记录一次人工或 Agent 活动边界")
    station.add_argument("work", nargs="?", default="project")
    add_station_arguments(station)
    station.set_defaults(handler=command_station)

    init = commands.add_parser("init", help="初始化一本书")
    init.add_argument("source")
    init.add_argument("--id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--work")
    init.add_argument("--source-lang", default="en")
    init.add_argument("--target-lang", action="append")
    init.add_argument("--format", choices=sorted(FORMATS), default="typst")
    init.add_argument("--target", action="append", choices=("html", "epub", "pdf"))
    init.set_defaults(handler=command_init)

    doctor = commands.add_parser("doctor", help="检查工具链")
    doctor.add_argument("work")
    doctor.add_argument(
        "--for",
        dest="purpose",
        choices=("all", "build", "check", "qa", "browser-qa"),
        default="all",
    )
    add_station_arguments(doctor)
    doctor.set_defaults(handler=command_doctor)

    render = commands.add_parser("render", help="渲染来源页")
    render.add_argument("work")
    render.add_argument("--pages", required=True)
    render.add_argument("--dpi", type=int, default=160)
    add_station_arguments(render)
    render.set_defaults(handler=command_render)

    check = commands.add_parser("check", help="检查工作目录")
    check.add_argument("work")
    add_station_arguments(check)
    check.set_defaults(handler=command_check)

    boundary_audit = commands.add_parser(
        "boundary-audit", help="生成源页跨页连续性候选清单"
    )
    boundary_audit.add_argument("work")
    boundary_audit.add_argument("--lang", default="all")
    boundary_audit.add_argument("--pages")
    boundary_audit.add_argument(
        "--include-info", action="store_true", help="保留低优先级信息项"
    )
    add_station_arguments(boundary_audit)
    boundary_audit.set_defaults(handler=command_boundary_audit)

    build = commands.add_parser("build", help="构建正式交付物")
    build.add_argument("work")
    build.add_argument("--lang", default="all")
    build.add_argument(
        "--target", choices=("all", "html", "epub", "pdf"), default="all"
    )
    add_station_arguments(build)
    build.set_defaults(handler=command_build)

    qa = commands.add_parser("qa", help="准备正式输出验收证据")
    qa.add_argument("work")
    qa.add_argument("--lang", default="all")
    qa.add_argument("--target", choices=("all", "html", "epub", "pdf"), default="all")
    qa.add_argument("--dpi", type=int, default=QA_DEFAULT_DPI)
    add_station_arguments(qa)
    qa.set_defaults(handler=command_qa)

    browser_qa = commands.add_parser(
        "browser-qa", help="用隔离的无头浏览器验收正式 EPUB"
    )
    browser_qa.add_argument("work")
    browser_qa.add_argument("--lang", default="all")
    browser_qa.add_argument("--width", type=int, default=BROWSER_QA_DEFAULTS["width"])
    browser_qa.add_argument(
        "--height", type=int, default=BROWSER_QA_DEFAULTS["height"]
    )
    browser_qa.add_argument(
        "--timeout", type=int, default=BROWSER_QA_DEFAULTS["timeout"]
    )
    browser_qa.add_argument(
        "--page-timeout", type=int, default=BROWSER_QA_DEFAULTS["page_timeout"]
    )
    browser_qa.add_argument(
        "--screenshots", type=int, default=BROWSER_QA_DEFAULTS["screenshots"]
    )
    add_station_arguments(browser_qa)
    browser_qa.set_defaults(handler=command_browser_qa)

    compare_epub = commands.add_parser(
        "compare-epub", help="比较正式 EPUB 与候选副本的内容成员"
    )
    compare_epub.add_argument("official")
    compare_epub.add_argument("candidate")
    compare_epub.set_defaults(handler=command_compare_epub)

    prepare = commands.add_parser(
        "prepare", help="按 AutoCorrect→lint→check 顺序准备中文底稿"
    )
    prepare.add_argument("work")
    add_station_arguments(prepare)
    prepare.set_defaults(handler=command_prepare)

    benchmark = commands.add_parser(
        "benchmark", help="聚合本机流程日志，不运行书籍任务"
    )
    benchmark.add_argument("--work", help="按作品目录名或 work.id 过滤")
    benchmark.set_defaults(handler=command_benchmark)

    refresh = commands.add_parser("refresh", help="增量重建并验收所选输出")
    refresh.add_argument("work")
    refresh.add_argument("--lang", required=True)
    refresh.add_argument(
        "--target", required=True, choices=("html", "epub", "pdf")
    )
    add_station_arguments(refresh)
    refresh.set_defaults(handler=command_refresh)

    finalize = commands.add_parser("finalize", help="运行最终机械验收链")
    finalize.add_argument("work")
    add_station_arguments(finalize)
    finalize.set_defaults(handler=command_finalize)

    deliver = commands.add_parser(
        "deliver", help="按 finalize 哈希原子复制一个正式输出"
    )
    deliver.add_argument("work")
    deliver.add_argument("--lang", required=True)
    deliver.add_argument(
        "--target", required=True, choices=("html", "epub", "pdf")
    )
    deliver.add_argument("--to", required=True, type=Path)
    add_station_arguments(deliver)
    deliver.set_defaults(handler=command_deliver)

    complete = commands.add_parser(
        "complete", help="在人工 STATUS 完成后原子收口 manifest 状态"
    )
    complete.add_argument("work")
    add_station_arguments(complete)
    complete.set_defaults(handler=command_complete)

    scratch = commands.add_parser("scratch", help="创建作品内隔离的 Agent 临时目录")
    scratch.add_argument("work")
    scratch.add_argument("--agent", required=True)
    add_station_arguments(scratch)
    scratch.set_defaults(handler=command_scratch)

    source_probe_parser = commands.add_parser("source-probe", help="完整探查 EPUB 来源结构，在 scratch 保存报告")
    source_probe_parser.add_argument("work")
    source_probe_parser.add_argument("--agent", required=True)
    add_station_arguments(source_probe_parser)
    source_probe_parser.set_defaults(handler=command_source_probe)

    source_draft = commands.add_parser("source-draft", help="先探查并检查信息保全，再生成 EPUB 草稿，仍需视觉核定")
    source_draft.add_argument("work")
    source_draft.add_argument("--agent", required=True)
    add_station_arguments(source_draft)
    source_draft.set_defaults(handler=command_source_draft)

    clean = commands.add_parser("clean", help="清理该书临时证据，保留终态和交付回执")
    clean.add_argument("work")
    add_station_arguments(clean)
    clean.set_defaults(handler=command_clean)
    return root


def main(argv: list[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    activities = list(getattr(args, "activity", []) or [])
    if args.command in WORK_COMMANDS and "none" in activities and len(activities) != 1:
        argument_parser.error("--activity none 不能与其他活动并用")
    started_at = datetime.now(timezone.utc)
    run_id = workflow_run_id()
    station_context: dict[str, Any] | None = None
    if args.command in WORK_COMMANDS:
        try:
            station_context = start_workflow_station(
                args, REPO_ROOT, started_at, run_id
            )
        except StationLaneBusy as station_error:
            print(f"ERROR: 无法开始流程工站：{station_error}", file=sys.stderr)
            try:
                write_workflow_log(
                    {
                        "schema_version": WORKFLOW_LOG_SCHEMA,
                        "kind": "workflow-run",
                        "run_id": run_id,
                        "started_at": started_at.isoformat(timespec="milliseconds").replace(
                            "+00:00", "Z"
                        ),
                        "ended_at": datetime.now(timezone.utc)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
                        "command": args.command,
                        "status": "failed",
                        "exit_code": 1,
                        "error_type": type(station_error).__name__,
                        "duration_seconds": 0.0,
                        "revision": workflow_revision(REPO_ROOT),
                        "platform": sys.platform,
                        "work": Path(str(getattr(args, "work", ""))).name,
                        "station": {
                            "lane": station_error.lane,
                            "activity": activities,
                            "scope": getattr(args, "scope", None),
                            "outcome": getattr(args, "outcome", None),
                            "issue": getattr(args, "issue", None),
                            "friction": ["station_lane_busy"],
                            "interval": {
                                "elapsed_seconds": None,
                                "human_elapsed_upper_bound_seconds": 0.0,
                                "max_attributable_seconds": STATION_INTERVAL_MAX_SECONDS,
                                "declared_effort_upper_bound_seconds": 0.0,
                                "reasons": ["station_lane_busy"],
                            },
                        },
                    },
                    REPO_ROOT,
                )
            except (CliError, OSError, TypeError, ValueError) as log_error:
                print(f"WARNING: 无法记录忙碌工站：{log_error}", file=sys.stderr)
            return 1
        except (CliError, OSError, TypeError, ValueError) as station_error:
            print(f"ERROR: 无法开始流程工站：{station_error}", file=sys.stderr)
            return 1

    started = time.perf_counter()
    status = "failed"
    exit_code = 1
    error_type = None
    pending_error: BaseException | None = None
    try:
        exit_code = int(args.handler(args))
        status = "passed" if exit_code == 0 else "failed"
        if exit_code:
            error_type = "nonzero_exit"
    except CliError as error:
        error_type = type(error).__name__
        print(f"ERROR: {error}", file=sys.stderr)
    except KeyboardInterrupt:
        exit_code = 130
        error_type = "KeyboardInterrupt"
        print("ERROR: interrupted", file=sys.stderr)
    except BaseException as error:
        error_type = type(error).__name__
        pending_error = error

    duration = round(time.perf_counter() - started, 6)
    if args.command != "benchmark":
        station_finished = False
        station: dict[str, Any] | None = None
        if station_context is not None:
            try:
                station = finish_workflow_station(station_context, REPO_ROOT)
                station_finished = True
            except (CliError, OSError, TypeError, ValueError) as station_error:
                station = station_context["station"]
                station["friction"] = sorted(
                    {*station["friction"], "station_finish_failed"}
                )
                status = "failed"
                exit_code = 1 if exit_code == 0 else exit_code
                error_type = error_type or "StationError"
                print(f"ERROR: 无法结束流程工站：{station_error}", file=sys.stderr)

            friction = set(station["friction"])
            if status == "failed" and error_type != "StationError":
                friction.add("command_failed")
                if duration > 120:
                    friction.add("failed_wait_over_120s")
                if (
                    station_context.get("previous_status") == "failed"
                    and station_context.get("previous_command") == args.command
                ):
                    friction.add("repeated_failure")
            if getattr(args, "outcome", None) == "blocked":
                friction.add("blocked_activity")
            station["friction"] = sorted(friction)

        work_value = getattr(args, "work", None)
        record: dict[str, Any] = {
            "schema_version": WORKFLOW_LOG_SCHEMA,
            "kind": "workflow-run",
            "run_id": run_id,
            "started_at": started_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "ended_at": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "command": args.command,
            "status": status,
            "exit_code": exit_code,
            "error_type": error_type,
            "duration_seconds": duration,
            "revision": workflow_revision(REPO_ROOT),
            "platform": sys.platform,
            "work": Path(str(work_value)).name if work_value else None,
        }
        optional_fields = {
            "work_id": "_workflow_work_id",
            "authoring_format": "_workflow_authoring_format",
            "source_kind": "_workflow_source_kind",
            "source_count": "_workflow_source_count",
            "targets": "_workflow_targets",
            "steps": "_workflow_steps",
        }
        for key, attribute in optional_fields.items():
            if hasattr(args, attribute):
                record[key] = getattr(args, attribute)
        if station is not None:
            record["station"] = station
        try:
            write_workflow_log(record, REPO_ROOT)
        except (CliError, OSError, TypeError, ValueError) as log_error:
            level = "ERROR" if station_context is not None else "WARNING"
            print(f"{level}: 无法写流程日志：{log_error}", file=sys.stderr)
            if station_context is not None and pending_error is None:
                exit_code = 1
        else:
            if station_context is not None and station_finished:
                try:
                    write_workflow_lane_state(REPO_ROOT, station_context, record)
                except (CliError, OSError, TypeError, ValueError) as station_error:
                    print(f"ERROR: 无法保存流程工站：{station_error}", file=sys.stderr)
                    station["friction"] = sorted(
                        {*station["friction"], "station_state_write_failed"}
                    )
                    status = "failed"
                    exit_code = 1 if exit_code == 0 else exit_code
                    error_type = error_type or "StationError"
                    record.update(
                        status=status,
                        exit_code=exit_code,
                        error_type=error_type,
                    )
                    try:
                        write_workflow_log(record, REPO_ROOT)
                    except (CliError, OSError, TypeError, ValueError) as log_error:
                        print(
                            f"ERROR: 无法更新失败的流程日志：{log_error}",
                            file=sys.stderr,
                        )
                    if pending_error is None:
                        exit_code = 1
        if station_context is not None:
            try:
                close_workflow_station(station_context)
            except (OSError, TypeError, ValueError) as station_error:
                print(f"ERROR: 无法释放流程工站：{station_error}", file=sys.stderr)
                if pending_error is None:
                    exit_code = 1

    if pending_error is not None:
        raise pending_error
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
