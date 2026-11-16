"""Expand dvisvgm's local glyph references without rewriting path coordinates.

This is a generated-formula adapter, not a general SVG optimizer. Keeping each
glyph's original ``d`` and a separate translation lets EPUB's ZIP compression
deduplicate repeated outlines while readers can safely discard ``use`` nodes.
"""

from __future__ import annotations

import copy
import math
import re
import xml.etree.ElementTree as ET

SVG = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
FORMULA_THEME_STYLE = (
    ".math-background{fill:#fff}"
    ".math-foreground,.math-foreground *{fill:#111}"
    "@media(prefers-color-scheme:dark){"
    ".math-background{fill:#182226}"
    ".math-foreground,.math-foreground *{fill:#f5f7f8}}"
)
GENERATED_ELEMENTS = frozenset(SVG + name for name in (
    "svg", "defs", "g", "path", "use", "rect", "circle", "ellipse",
    "line", "polyline", "polygon", "style", "metadata",
))


class FormulaPathError(ValueError):
    """A generated glyph cannot be expanded without changing its meaning."""


def expand_local_glyphs(root: ET.Element) -> int:
    """Replace local path/use aliases, preserving order and transform nesting.

    Return the number of references removed, including aliases in definitions.
    Existing themed cache entries use the same operation as fresh dvisvgm output.
    Unsupported definitions fail before the caller writes or publishes the SVG.
    """
    uses = list(root.iter(SVG + "use"))
    if not uses:
        return 0

    # Only the producer's theme is selector-independent for glyph expansion.
    # Arbitrary CSS, links or paint servers could depend on removed definitions
    # or the original element type; do not silently approximate those inputs.
    for node in root.iter():
        if node.tag not in GENERATED_ELEMENTS:
            raise FormulaPathError("unsupported generated SVG element")
        if node.tag == SVG + "style" and node.text != FORMULA_THEME_STYLE:
            raise FormulaPathError("unsupported glyph-dependent stylesheet")
        if {"style", "{http://www.w3.org/XML/1998/namespace}base"} & node.attrib.keys():
            raise FormulaPathError("unsupported generated SVG styling/base attributes")
        if any("url(" in value.casefold() or "\\" in value for value in node.attrib.values()) or (
            node.tag != SVG + "use" and ({"href", XLINK_HREF} & node.attrib.keys())
        ):
            raise FormulaPathError("unsupported non-use glyph reference")
    definitions = list(root.iter(SVG + "defs"))
    if any(defs not in list(root) for defs in definitions):
        raise FormulaPathError("glyph definitions must be direct SVG children")
    if any(defs.attrib for defs in definitions):
        raise FormulaPathError("unsupported glyph definition attributes")
    glyphs = {node for defs in definitions for node in defs}
    ids: dict[str, ET.Element] = {}
    for node in root.iter():
        identifier = node.get("id")
        if identifier:
            if identifier in ids:
                raise FormulaPathError(f"duplicate glyph ID: {identifier}")
            ids[identifier] = node
    for node in glyphs:
        if node.tag not in {SVG + "path", SVG + "use"} or len(node):
            raise FormulaPathError("unsupported glyph definition")
        if node.tag == SVG + "path" and (
            set(node.attrib) - {"id", "d"} or not node.get("d")
        ):
            raise FormulaPathError("unsupported glyph path attributes")

    def expand(node: ET.Element, seen: tuple[str, ...] = ()) -> ET.Element:
        if node.tag == SVG + "path":
            result = copy.deepcopy(node)
            result.attrib.pop("id", None)
            result.tail = None
            return result
        if node.tag != SVG + "use" or len(node) or set(node.attrib) - {
            "id", "x", "y", "transform", "href", XLINK_HREF,
        }:
            raise FormulaPathError("unsupported glyph use attributes")
        href = node.get("href", node.get(XLINK_HREF, ""))
        if "href" in node.attrib and XLINK_HREF in node.attrib and node.get("href") != node.get(XLINK_HREF):
            raise FormulaPathError("conflicting glyph references")
        if not href.startswith("#") or len(href) == 1:
            raise FormulaPathError(f"glyph reference must be local: {href}")
        if href in seen:
            raise FormulaPathError(f"cyclic glyph reference: {href}")
        target = ids.get(href[1:])
        if target is None or target not in glyphs:
            raise FormulaPathError(f"missing local glyph definition: {href}")
        transform = node.get("transform", "")
        if "x" in node.attrib or "y" in node.attrib:
            x, y = node.get("x", "0"), node.get("y", "0")
            for value in (x, y):
                if NUMBER.fullmatch(value) is None or not math.isfinite(float(value)):
                    raise FormulaPathError(f"invalid glyph coordinate: {value}")
            transform = " ".join(part for part in (transform, f"translate({x} {y})") if part)
        result = ET.Element(SVG + "g", {"transform": transform} if transform else {})
        result.append(expand(target, (*seen, href)))
        return result

    # Resolve all references before mutating the tree, including unused aliases.
    replacements = {node: expand(node) for node in uses}
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == SVG + "defs":
                parent.remove(child)
            elif child in replacements:
                index = list(parent).index(child)
                replacement = replacements[child]
                if child.get("id"):
                    replacement.set("id", child.get("id"))
                replacement.tail = child.tail
                parent.remove(child)
                parent.insert(index, replacement)
    return len(uses)
