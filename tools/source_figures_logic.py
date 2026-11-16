"""Small source-geometry and identity checks shared by the figure worker/tests."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET


def intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1]))


def area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def outward(box, width, height):
    return [max(0, min(width, math.floor(box[0]))),
            max(0, min(height, math.floor(box[1]))),
            max(0, min(width, math.ceil(box[2]))),
            max(0, min(height, math.ceil(box[3])))]


def rectangle_loss_pixels(target, crop):
    """Exact missing area of a half-open native raster placement; no tolerance."""
    return area(target) - intersection(target, crop)


def _trace_clip(node, scale):
    """Recognize only a single exact axis-aligned rectangular clip path."""
    record = {"kind": node.tag, "bbox_px": None, "status": "unresolved"}
    if node.tag != "clip_path":
        return record
    try:
        transform = [float(x) for x in node.get("transform", "").split()]
        if len(transform) != 6 or not all(math.isfinite(x) for x in transform):
            return record
        children = list(node)
        if ([x.tag for x in children] not in
                (["moveto", "lineto", "lineto", "lineto", "closepath"],
                 ["moveto", "lineto", "lineto", "lineto", "lineto", "closepath"])):
            return record
        a, b, c, d, e, f = transform
        points = [(float(n.attrib["x"]), float(n.attrib["y"])) for n in children[:-1]]
        points = [((a*x+c*y+e)*scale, (b*x+d*y+f)*scale) for x, y in points]
        if len(points) == 5 and points[-1] == points[0]:
            points.pop()
        if len(points) != 4 or not all(math.isfinite(v) for p in points for v in p):
            return record
        xs, ys = zip(*points)
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if x1 <= x0 or y1 <= y0:
            return record
        tolerance = 1e-9
        corners = set()
        for x, y in points:
            ix = 0 if abs(x-x0) <= tolerance else 1 if abs(x-x1) <= tolerance else None
            iy = 0 if abs(y-y0) <= tolerance else 1 if abs(y-y1) <= tolerance else None
            if ix is None or iy is None or (ix, iy) in corners:
                return record
            corners.add((ix, iy))
        for p, q in zip(points, points[1:] + points[:1]):
            dx, dy = abs(p[0]-q[0]), abs(p[1]-q[1])
            if not ((dx <= tolerance < dy) or (dy <= tolerance < dx)):
                return record
        record.update(status="rectangle", bbox_px=[x0, y0, x1, y1])
        return record
    except (KeyError, TypeError, ValueError, OverflowError):
        return record


def native_images(trace_path, scale, width, height):
    """Extract original MuPDF image placements, not predicted boxes or pixels."""
    root = ET.parse(trace_path).getroot()
    images, stack = [], []

    def image(node):
        values = [float(x) for x in node.get("transform", "").split()]
        if len(values) != 6 or not all(math.isfinite(x) for x in values):
            raise ValueError("native image has invalid placement transform")
        a, b, c, d, e, f = values
        corners = [(a*x+c*y+e, b*x+d*y+f) for x in (0, 1) for y in (0, 1)]
        xs, ys = zip(*corners)
        continuous = [min(xs)*scale, min(ys)*scale, max(xs)*scale, max(ys)*scale]
        box = outward(continuous, width, height)
        if not area(box):
            return
        fraction = area(box) / (width * height)
        active = [dict(clip) for clip in stack]
        clipped = any(clip["status"] != "rectangle" or
                      intersection(clip["bbox_px"], continuous) != area(continuous)
                      for clip in active)
        images.append({"id": f"native-image-{len(images)}", "bbox_px": box,
                       "continuous_bbox_px": continuous, "transform_pt": values,
                       "source_width": int(node.attrib["width"]),
                       "source_height": int(node.attrib["height"]),
                       "page_fraction": fraction,
                       "active_clips": active,
                       "clip_status": "unresolved" if clipped else "fully_contained" if active else "none",
                       # A small image can still be the entire figure; only
                       # reject page-sized raster backdrops at this stage.
                       "eligible": fraction <= 0.8})

    def walk(parent):
        for node in parent:
            if node.tag.startswith("clip_"):
                stack.append(_trace_clip(node, scale))
            elif node.tag == "pop_clip":
                if not stack:
                    raise ValueError("unbalanced native clip trace")
                stack.pop()
            elif node.tag == "fill_image":
                image(node)
            else:
                walk(node)
    walk(root)
    if stack:
        raise ValueError("unbalanced native clip trace")
    return images


def select_native_images(body, placements):
    """Associate only overlapping placements; multiple objects stay ambiguous."""
    found = []
    for image in placements:
        if not image["eligible"]:
            continue
        p = image["continuous_bbox_px"]
        overlap = intersection(body, p)
        if overlap and (overlap / area(body) >= 0.8 or overlap / area(p) >= 0.8):
            found.append(image)
    return found


def independent_glyph_lines(blocks, body, caps, scale):
    """Native glyph lines intersecting B, with matched captions excluded by ID."""
    caption_ids = {line for cap in caps if cap.get("status") == "ok"
                   for line in cap["source_snap"]["line_ids"]}
    result = []
    for block in blocks:
        for line in block:
            if line["id"] in caption_ids or line.get("error") or not line["glyphs"]:
                continue
            bounds = union([g["bbox_pt"] for g in line["glyphs"]])
            bounds = [v * scale for v in bounds]
            if intersection(bounds, body):
                result.append({"line_id": line["id"], "text": line["text"],
                               "bbox_px": bounds, "glyphs": line["glyphs"]})
    return result


def overlapping_caption_bounds(crop, caps):
    """Potential caption intrusion is attributed to its own source lines."""
    if crop is None:
        return []
    return [{"caption_index": index, "line_ids": cap["source_snap"]["line_ids"],
             "bbox_px": cap["source_snap"]["bbox_px"]}
            for index, cap in enumerate(caps) if cap.get("status") == "ok" and
            intersection(crop, cap["source_snap"]["bbox_px"])]


def unassigned_text_bounds(crop, blocks, caps, selected_glyphs, scale):
    """Flag native text reached by a crop but not bound to its model seed."""
    if crop is None:
        return []
    assigned = {x["line_id"] for x in selected_glyphs}
    captions = {line for cap in caps if cap.get("status") == "ok"
                for line in cap["source_snap"]["line_ids"]}
    result = []
    for block in blocks:
        for line in block:
            if line["id"] in assigned or line["id"] in captions or not line["glyphs"]:
                continue
            bounds = [x * scale for x in union([glyph["bbox_pt"] for glyph in line["glyphs"]])]
            if intersection(bounds, crop):
                result.append({"line_id": line["id"], "bbox_px": bounds})
    return result


def unproven_glyph_ownership(glyphs, placements):
    """A model/panel overlap alone does not prove that native text is a legend."""
    return [glyph for glyph in glyphs if not any(
        intersection(glyph["bbox_px"], image["bbox_px"]) == area(glyph["bbox_px"])
        for image in placements)]


def uncovered_region_glyphs(crop, cap, blocks, caps, scale):
    """Detect independently sourced glyphs inside a PF2 region but outside crop.

    PF2 region membership is only a warning boundary, not proof of ownership.
    """
    region = _region(cap, scale)
    if region is None:
        return []
    caption_ids = {line for item in caps if item.get("status") == "ok"
                   for line in item["source_snap"]["line_ids"]}
    missed = []
    for block in blocks:
        for line in block:
            if line["id"] in caption_ids:
                continue
            glyphs = []
            for glyph in line["glyphs"]:
                box = [v * scale for v in glyph["bbox_pt"]]
                if area(box) and intersection(box, region) == area(box) and (
                        crop is None or intersection(box, crop) != area(box)):
                    glyphs.append(box)
            if glyphs:
                missed.append({"line_id": line["id"], "glyph_count": len(glyphs),
                               "bbox_px": union(glyphs)})
    return missed


def _region(cap, scale):
    raw = cap.get("predictor", {}).get("regionBoundary")
    if not isinstance(raw, dict):
        return None
    try:
        box = [float(raw[k]) * scale for k in ("x1", "y1", "x2", "y2")]
    except (KeyError, TypeError, ValueError):
        return None
    return box if all(math.isfinite(x) for x in box) and area(box) else None


def pair_captions(bodies, caps, scale):
    """Return only a unique source-caption ownership, never a split heuristic."""
    if not bodies or not caps:
        return {}, ["missing_body_or_caption"]
    if any(c.get("status") != "ok" for c in caps):
        return {}, ["caption_source_alignment_unavailable_or_ambiguous"]
    edges = {}
    for i, body in enumerate(bodies):
        matches = []
        for j, cap in enumerate(caps):
            region = _region(cap, scale)
            if region is not None and intersection(body["B"], region):
                matches.append(j)
        edges[i] = matches
    reverse = {j: [i for i, js in edges.items() if j in js] for j in range(len(caps))}
    pairs = {i: js[0] for i, js in edges.items()
             if len(js) == 1 and len(reverse[js[0]]) == 1}
    reasons = []
    if len(pairs) != len(bodies) or len(pairs) != len(caps):
        reasons.append("caption_body_ownership_not_unique")
    return pairs, reasons
