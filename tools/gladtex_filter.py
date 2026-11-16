#!/usr/bin/env python3
"""Convert Pandoc Math nodes to packaged SVG image nodes with GladTeX."""

from __future__ import annotations

import concurrent.futures
from contextlib import contextmanager
import html
import json
import locale
import math
import os
import re
import sys
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    from tools.browser_runtime import hidden_process_options
except ModuleNotFoundError:  # direct ``python tools/gladtex_filter.py`` execution
    from browser_runtime import hidden_process_options


# GladTeX dimensions are CSS pixels at 12pt. These ratios target a 16px
# EPUB body: ordinary inline glyphs stay near 0.7em, while display math gets
# a readable block-level lift without changing each SVG's internal proportions.
INLINE_MATH_EM_PER_PX = 0.06
DISPLAY_MATH_EM_PER_PX = 0.10
ROW_TAG_RENDER_VERSION = "translator-row-tags-v1"
GLADTEX_MAX_WORKERS = 8

# Let amsmath place each label on its own row.  The first pass only measures
# content and labels; the second pass is the actual native align* display.
ROW_TAG_PREAMBLE = r"""
\makeatletter
\newsavebox{\TranslatorMathBox}
\newdimen\TranslatorMathWidth
\newdimen\TranslatorTagWidth
\NewDocumentCommand\TranslatorMeasureTag{s m}{%
\IfBooleanTF{#1}{\setbox0=\hbox{\normalfont #2}}{\setbox0=\hbox{\normalfont (#2)}}%
\ifdim\wd0>\TranslatorTagWidth\global\TranslatorTagWidth=\wd0\fi}
\NewDocumentEnvironment{translatoralign}{+b}{%
\TranslatorTagWidth=0pt
\begingroup
\let\dft@tag\TranslatorMeasureTag
\sbox{\TranslatorMathBox}{$\displaystyle\begin{aligned}#1\end{aligned}$}%
\global\TranslatorMathWidth=\wd\TranslatorMathBox
\endgroup
\hsize=\dimexpr\TranslatorMathWidth+\TranslatorTagWidth+2em\relax
\linewidth=\hsize \textwidth=\hsize \columnwidth=\hsize
\mathindent=0pt
\abovedisplayskip=0pt \belowdisplayskip=0pt
\abovedisplayshortskip=0pt \belowdisplayshortskip=0pt
\begin{align*}#1\end{align*}%
}{}
\makeatother
"""


@contextmanager
def gladtex_runtime(cachedconverter: Any, image: Any):
    """Bound only GladTeX's pool and hide only its subprocesses."""
    original_concurrent = cachedconverter.concurrent
    original_subprocess = image.subprocess

    def thread_pool(*, max_workers: int):
        return concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, GLADTEX_MAX_WORKERS)
        )

    class HiddenSubprocess:
        def __getattr__(self, name: str) -> Any:
            return getattr(original_subprocess, name)

        def Popen(self, *args: Any, **kwargs: Any) -> Any:
            options = hidden_process_options()
            options.update(kwargs)
            return original_subprocess.Popen(*args, **options)

    cachedconverter.concurrent = SimpleNamespace(
        futures=SimpleNamespace(
            ThreadPoolExecutor=thread_pool,
            as_completed=concurrent.futures.as_completed,
        )
    )
    image.subprocess = HiddenSubprocess()
    try:
        yield
    finally:
        cachedconverter.concurrent = original_concurrent
        image.subprocess = original_subprocess


def row_tagged_alignment(formula: str) -> str | None:
    """Identify the supported top-level displays without parsing their math."""
    if len(re.findall(r"\\tag\*?\s*\{", formula)) < 2:
        return None
    match = re.fullmatch(
        r"\s*\\begin\{(?P<env>align\*?|aligned)\}"
        r"(?P<body>.*)\\end\{(?P=env)\}\s*",
        formula,
        re.DOTALL,
    )
    if not match:
        return None
    body = match.group("body")
    if match.group("env") == "aligned":
        # This is the subordinate environment's vertical-position option,
        # not a leading mathematical bracket in the native align* body.
        body = re.sub(r"^\s*\[[tbc]\]", "", body, count=1)
    return body


def native_tagged_display(formula: str) -> str | None:
    """Use native AMS handling for multi-row or formatted/starred labels."""
    body = row_tagged_alignment(formula)
    if body is not None:
        return body
    if not re.search(r"\\tag\*\s*\{|\\tag\s*\{[^{}]*\{", formula):
        return None
    match = re.fullmatch(
        r"\s*\\begin\{(?P<env>equation\*?|align\*?|aligned)\}"
        r"(?P<body>.*)\\end\{(?P=env)\}\s*", formula, re.DOTALL,
    )
    if not match:
        return None
    body = match.group("body")
    if match.group("env") == "aligned":
        body = re.sub(r"^\s*\[[tbc]\]", "", body, count=1)
    return body


def normalize_display_input(formula: str) -> str:
    """Normalize only the rendering copy, not the source AST or alt text."""
    for old, new in (("rm", "mathrm"), ("bf", "mathbf"), ("it", "mathit")):
        formula = re.sub(
            r"\\" + old + r"\b", lambda _m, replacement=new: "\\" + replacement, formula
        )
    return re.sub(r"\n(?:[ \t]*\n)+", "\n", formula)


def relative_formula_style(element: ET.Element, em_per_px: float) -> None:
    """Keep a formula's GladTeX proportions while following font size."""
    try:
        height = float(element.attrib["height"])
    except (KeyError, ValueError):
        return
    style = element.attrib.get("style", "")
    alignment = re.search(
        r"vertical-align:\s*(-?\d+(?:\.\d+)?)px", style
    )
    if alignment:
        depth = float(alignment.group(1)) * em_per_px
        style = re.sub(
            r"vertical-align:\s*-?\d+(?:\.\d+)?px",
            f"vertical-align: {depth:.4f}em",
            style,
        )
    style = style.rstrip(" ;")
    if style:
        style += "; "
    style += (
        f"height: {height * em_per_px:.4f}em; "
        "width: auto;"
    )
    element.attrib["style"] = style


def replace_raw_images(value: Any) -> int:
    replaced = 0
    if isinstance(value, list):
        for item in value:
            replaced += replace_raw_images(item)
        return replaced
    if not isinstance(value, dict):
        return 0
    if value.get("t") == "Para":
        content = value.get("c")
        if isinstance(content, list):
            replaced = replace_raw_images(content)
            meaningful_content = [
                item
                for item in content
                if not (
                    isinstance(item, dict)
                    and (
                        item.get("t") in {"Space", "SoftBreak"}
                        or (
                            item.get("t") == "Str"
                            and not str(item.get("c", "")).strip()
                        )
                    )
                )
            ]
            span_attributes = (
                meaningful_content[0].get("c")
                if len(meaningful_content) == 1
                and isinstance(meaningful_content[0], dict)
                else None
            )
            if (
                len(meaningful_content) == 1
                and isinstance(meaningful_content[0], dict)
                and meaningful_content[0].get("t") == "Span"
                and isinstance(span_attributes, list)
                and len(span_attributes) == 2
                and isinstance(span_attributes[0], list)
                and len(span_attributes[0]) >= 2
                and "display-formula" in span_attributes[0][1]
            ):
                value.clear()
                value.update(
                        {
                            "t": "Div",
                            "c": [
                                ["", ["display-formula"], []],
                                [{"t": "Plain", "c": meaningful_content}],
                            ],
                        }
                )
            return replaced
    if value.get("t") == "RawInline":
        content = value.get("c")
        if (
            isinstance(content, list)
            and len(content) == 2
            and content[0] == "html"
            and isinstance(content[1], str)
            and content[1].lstrip().startswith("<img ")
        ):
            element = ET.fromstring(content[1])
            if element.tag == "img":
                classes = element.attrib.get("class", "").split()
                if "math-inline" in classes:
                    relative_formula_style(element, INLINE_MATH_EM_PER_PX)
                elif "math-display" in classes:
                    relative_formula_style(element, DISPLAY_MATH_EM_PER_PX)
                attributes = [
                    [key, item]
                    for key, item in element.attrib.items()
                    if key not in {"src", "alt", "class"}
                ]
                alt = element.attrib.get("alt", "").strip()
                source = element.attrib.get("src", "")
                image = {
                    "t": "Image",
                    "c": [
                        ["", classes, attributes],
                        [{"t": "Str", "c": alt}],
                        [source, ""],
                    ],
                }
                value.clear()
                if "math-display" in classes:
                    value.update(
                        {
                            "t": "Span",
                            "c": [
                                ["", ["display-formula"], []],
                                [image],
                            ],
                        }
                    )
                else:
                    value.update(image)
                return 1
    for item in value.values():
        replaced += replace_raw_images(item)
    return replaced


def count_nodes(value: Any, node_type: str) -> int:
    if isinstance(value, list):
        return sum(count_nodes(item, node_type) for item in value)
    if not isinstance(value, dict):
        return 0
    return int(value.get("t") == node_type) + sum(
        count_nodes(item, node_type) for item in value.values()
    )


def math_nodes(value: Any) -> list[tuple[str, bool]]:
    formulas: list[tuple[str, bool]] = []
    if isinstance(value, list):
        for item in value:
            formulas.extend(math_nodes(item))
    elif isinstance(value, dict):
        if value.get("t") == "Math":
            style, formula = value["c"]
            formulas.append((formula, style.get("t") == "DisplayMath"))
        else:
            for item in value.values():
                formulas.extend(math_nodes(item))
    return formulas


def valid_svg(path: Path, *, require_row_tags: bool = False) -> bool:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False
    return (
        root.tag == "{http://www.w3.org/2000/svg}svg"
        and len(root.attrib.get("viewBox", "").split()) == 4
        and (
            not require_row_tags
            or any(
                node.tag == "{http://www.w3.org/2000/svg}metadata"
                and node.text == ROW_TAG_RENDER_VERSION
                for node in root.iter()
            )
        )
    )


def valid_partial_row_tag_svg(path: Path) -> bool:
    """Reject malformed geometric bounds before stamping a partial result."""
    if not valid_svg(path):
        return False
    try:
        root = ET.parse(path).getroot()
        view_box = [float(value) for value in root.attrib["viewBox"].split()]
    except (OSError, KeyError, ValueError, ET.ParseError):
        return False
    return (
        len(view_box) == 4
        and all(math.isfinite(value) for value in view_box)
        and view_box[2] > 0
        and view_box[3] > 0
    )


def cache_statistics(
    cache_path: Path,
    working_directory: Path,
    formulas: list[tuple[str, bool]],
    normalize_formula: Any,
) -> tuple[int, int]:
    unique = {(normalize_formula(formula), display) for formula, display in formulas}
    if not cache_path.is_file():
        return 0, len(unique)
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        cache_path.unlink(missing_ok=True)
        return 0, len(unique)
    if (
        not isinstance(cache, dict)
        or cache.get("GladTeX__cache__version") != "2.0"
    ):
        cache_path.unlink(missing_ok=True)
        return 0, len(unique)

    changed = False
    hits = 0
    formula_root = cache_path.parent.resolve()
    for formula, display in unique:
        variants = cache.get(formula)
        display_key = str(display).lower()
        data = variants.get(display_key) if isinstance(variants, dict) else None
        relative = data.get("path") if isinstance(data, dict) else None
        valid = False
        candidate: Path | None = None
        if isinstance(relative, str):
            candidate = (working_directory / relative).resolve()
            try:
                candidate.relative_to(formula_root)
            except ValueError:
                pass
            else:
                valid = valid_svg(
                    candidate,
                    require_row_tags=display and native_tagged_display(formula) is not None,
                )
        if valid:
            hits += 1
            continue
        if candidate is not None:
            try:
                candidate.relative_to(formula_root)
            except ValueError:
                pass
            else:
                candidate.unlink(missing_ok=True)
        if isinstance(variants, dict) and display_key in variants:
            del variants[display_key]
            if not variants:
                del cache[formula]
            changed = True

    if changed:
        stage = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
        stage.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(stage, cache_path)
    return hits, len(unique) - hits


def stamp_row_tag_svgs(
    cache_path: Path,
    working_directory: Path,
    formulas: list[tuple[str, bool]],
    normalize_formula: Any,
    *,
    partial: bool = False,
) -> None:
    """Version valid current-input row-tag images, optionally after failure."""
    affected = {
        normalize_formula(formula)
        for formula, display in formulas
        if display and native_tagged_display(formula) is not None
    }
    if not affected or (partial and not cache_path.exists()):
        return
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    for formula in affected:
        try:
            relative = cache[formula]["true"]["path"]
            if not isinstance(relative, str):
                raise TypeError("row-tag cache image path must be a string")
            path = (working_directory / relative).resolve()
            path.relative_to(cache_path.parent.resolve())
            if not valid_svg(path) or (partial and not valid_partial_row_tag_svg(path)):
                raise ValueError(f"Row-tag rendering produced invalid SVG: {path}")
        except (KeyError, TypeError, ValueError):
            if partial:
                continue
            raise
        if valid_svg(path, require_row_tags=True):
            continue
        tree = ET.parse(path)
        metadata = ET.SubElement(tree.getroot(), "{http://www.w3.org/2000/svg}metadata")
        metadata.text = ROW_TAG_RENDER_VERSION
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
        tree.write(path, encoding="utf-8", xml_declaration=True)


def run_gladtex_with_checkpoint(
    main: Any,
    arguments: list[str],
    cachedconverter: Any,
    image: Any,
    cache_path: Path,
    working_directory: Path,
    formulas: list[tuple[str, bool]],
    normalize_formula: Any,
) -> None:
    """Preserve GladTeX's exception while checkpointing finished row tags."""
    try:
        with gladtex_runtime(cachedconverter, image):
            main.run(arguments)
    except BaseException:
        try:
            stamp_row_tag_svgs(
                cache_path, working_directory, formulas, normalize_formula,
                partial=True,
            )
        except BaseException as error:  # noqa: BLE001 - checkpoint and re-raise the original failure
            print(f"WARNING: GladTeX partial row-tag checkpoint failed: {error}", file=sys.stderr)
        raise


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: gladtex_filter.py INPUT_JSON OUTPUT_JSON IMAGE_DIRECTORY"
        )
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    image_directory = Path(sys.argv[3])
    if input_path.parent != output_path.parent or image_directory.is_absolute():
        raise SystemExit("GladTeX paths must share one working directory")

    # GladTeX 3.1.0 rejects UTF-8 under non-Western Windows locales.
    locale.getdefaultlocale = lambda: ("en_US", "UTF-8")  # type: ignore[assignment]

    from gleetex import cachedconverter, image
    from gleetex.__main__ import Main
    from gleetex.cachedconverter import CachedConverter
    from gleetex.caching import ImageCache, normalize_formula
    from gleetex.htmlhandling import HtmlImageFormatter
    from gleetex.typesetting import LaTeXDocument

    original_image = HtmlImageFormatter.get_html_img
    original_exclusion = HtmlImageFormatter.set_exclude_long_formulas
    original_display = LaTeXDocument.set_displaymath
    source_document = json.loads(input_path.read_text(encoding="utf-8"))
    cache_path = (
        input_path.parent
        / image_directory
        / CachedConverter.GLADTEX_CACHE_FILE_NAME
    )
    hits, misses = cache_statistics(
        cache_path,
        input_path.parent,
        math_nodes(source_document),
        normalize_formula,
    )

    def safe_image(
        formatter: HtmlImageFormatter,
        position: dict[str, float],
        formula: str,
        image_path: str,
        display_math: bool = False,
    ) -> str:
        rendered = original_image(
            formatter,
            position,
            formula,
            image_path,
            display_math,
        )
        return rendered.replace(
            f'alt="{formula}"',
            f'alt="{html.escape(formula, quote=True)}"',
        )

    def keep_alt_text(formatter: HtmlImageFormatter, _enabled: bool) -> None:
        original_exclusion(formatter, False)

    def compact_display(document: LaTeXDocument, enabled: bool) -> None:
        formula = document._LaTeXDocument__equation  # type: ignore[attr-defined]
        if enabled:
            row_body = native_tagged_display(formula)
            if row_body is not None:
                # GladTeX strips trailing whitespace before appending its end
                # environment.  Terminate a possible final TeX comment first.
                document._LaTeXDocument__equation = (  # type: ignore[attr-defined]
                    normalize_display_input(row_body).rstrip() + "\n\\relax"
                )
                document.set_latex_environment("translatoralign")
                original_display(document, False)
                return
            equation = re.fullmatch(
                r"\s*\\begin\{equation\*?\}(.*)\\end\{equation\*?\}\s*",
                formula,
                re.DOTALL,
            )
            if equation:
                formula = equation.group(1)
            formula = re.sub(
                r"\\begin\{align\*?\}",
                r"\\begin{aligned}",
                formula,
            )
            formula = re.sub(
                r"\\end\{align\*?\}",
                r"\\end{aligned}",
                formula,
            )
            # LaTeX2e no longer defines obsolete math font switches such as
            # ``\rm`` in GladTeX's scrartcl preamble.  Pandoc can emit these
            # aliases when normalizing \mathrm/\mathbf/\mathit; canonicalize
            # them before GladTeX compiles the formula.
            formula = normalize_display_input(formula)
            tags = re.findall(r"\\tag\{([^{}]+)\}", formula)
            formula = re.sub(r"\s*\\tag\{[^{}]+\}\s*", " ", formula)
            formula = re.sub(r"\n(?:[ \t]*\n)+", "\n", formula)
            if tags:
                formula += rf"\qquad\text{{({tags[-1]})}}"
            document._LaTeXDocument__equation = (  # type: ignore[attr-defined]
                "\\displaystyle " + formula
            )
            original_display(document, False)
            return
        original_display(document, enabled)

    def atomic_cache_write(cache: ImageCache) -> None:
        target = Path(cache._ImageCache__cache_name)  # type: ignore[attr-defined]
        data = cache._ImageCache__cache  # type: ignore[attr-defined]
        stage = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        stage.write_text(json.dumps(data), encoding="utf-8")
        last_error: PermissionError | None = None
        for _ in range(8):
            try:
                os.replace(stage, target)
                break
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.25)
        else:
            assert last_error is not None
            raise last_error

    HtmlImageFormatter.get_html_img = safe_image
    HtmlImageFormatter.set_exclude_long_formulas = keep_alt_text
    LaTeXDocument.set_displaymath = compact_display
    ImageCache.write = atomic_cache_write

    run_gladtex_with_checkpoint(
        Main(),
        [
            "gladtex",
            "-P",
            "-d",
            str(image_directory),
            "-i",
            "math-inline",
            "-l",
            "math-display",
            "-c",
            "111111",
            "-p",
            (
                r"\usepackage{CJKutf8}"
                r"\usepackage{mathrsfs}"
                r"\usepackage{bbm}"
                + ROW_TAG_PREAMBLE
                +
                r"\AtBeginDocument{\begin{CJK}{UTF8}{gbsn}}"
                r"\AtEndDocument{\end{CJK}}"
            ),
            "-o",
            output_path.name,
            input_path.name,
        ],
        cachedconverter,
        image,
        cache_path,
        input_path.parent,
        math_nodes(source_document),
        normalize_formula,
    )

    stamp_row_tag_svgs(
        cache_path, input_path.parent, math_nodes(source_document), normalize_formula
    )

    document = json.loads(output_path.read_text(encoding="utf-8"))
    replaced = replace_raw_images(document)
    remaining = count_nodes(document, "Math")
    if remaining:
        raise SystemExit(f"GladTeX left {remaining} Math nodes")
    output_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"svg_math_images={replaced}")
    print(f"svg_math_unique={hits + misses}")
    print(f"svg_math_cache_hits={hits}")
    print(f"svg_math_cache_misses={misses}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
