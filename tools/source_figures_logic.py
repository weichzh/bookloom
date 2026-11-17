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
            for index, glyph in enumerate(line["glyphs"]):
                box = [v * scale for v in glyph["bbox_pt"]]
                if area(box) and intersection(box, region) == area(box) and (
                        crop is None or intersection(box, crop) != area(box)):
                    glyphs.append({"glyph_id": glyph.get("id", f"{line['id']}:{index}"), "bbox_px": box})
            if glyphs:
                missed.append({"line_id": line["id"], "glyph_count": len(glyphs),
                               "bbox_px": union([g["bbox_px"] for g in glyphs]), "glyphs": glyphs})
    return missed


def split_form_metric_overhangs(uncovered, form, placements, crop, proven_glyphs):
    """Separate nominal glyph overhang from actual crop uncertainty.

    A Form's native BBox clips its own glyphs. This only applies to the exact
    source glyph and native invocation already proved by the caller, when the
    complete Form viewport and its external clips are known to be preserved.
    """
    if not form or crop is None or not form.get("eligible") or not form.get("axis_aligned"):
        return uncovered, []
    if form.get("clip_status") not in ("none", "fully_contained"):
        return uncovered, []
    raw_bbox, transform = form.get("form_bbox"), form.get("display_transform")
    viewport = form.get("bbox_px")
    continuous = form.get("continuous_bbox_px")
    try:
        if (len(raw_bbox) != 4 or len(transform) != 6 or len(viewport) != 4 or len(continuous) != 4 or
                not all(math.isfinite(float(v)) for v in raw_bbox + transform + viewport + continuous) or
                not area(raw_bbox) or not area(viewport) or not area(continuous) or
                rectangle_loss_pixels(viewport, crop)):
            return uncovered, []
    except (TypeError, ValueError):
        return uncovered, []
    owners = {p["id"] for p in placements if p["id"] == form["id"] or
              form["id"] in p.get("ancestor_ids", [])}
    proven = {(g["glyph_id"], tuple(g["bbox_px"])) for g in proven_glyphs
              if g.get("form_id") in owners}
    remaining, overhangs = [], []
    for line in uncovered:
        glyphs = line.get("glyphs")
        if not isinstance(glyphs, list):
            remaining.append(line)
            continue
        kept, clipped = [], []
        for glyph in glyphs:
            box = glyph["bbox_px"]
            is_proven = (glyph["glyph_id"], tuple(box)) in proven
            extends = intersection(box, continuous) < area(box)
            (clipped if is_proven and extends and intersection(box, continuous) else kept).append(glyph)
        for output, values in ((remaining, kept), (overhangs, clipped)):
            if values:
                output.append({"line_id": line["line_id"], "glyph_count": len(values),
                               "bbox_px": union([g["bbox_px"] for g in values]), "glyphs": values})
    return remaining, overhangs


def _region(cap, scale):
    raw = cap.get("predictor", {}).get("regionBoundary")
    if not isinstance(raw, dict):
        return None
    try:
        box = [float(raw[k]) * scale for k in ("x1", "y1", "x2", "y2")]
    except (KeyError, TypeError, ValueError):
        return None
    return box if all(math.isfinite(x) for x in box) and area(box) else None


def native_form_groups(bodies, caps, placements, scale):
    """Match independent Form invocations, source regions and model rows.

    The region associates a caption; its own rectangle is never a crop. A
    single page caption does not claim seeds outside the selected invocation.
    """
    eligible = [p for p in placements if p.get("eligible")]
    by_id = {p["id"]: p for p in placements}
    roots = [p for p in eligible if all(a in by_id and by_id[a].get("page_sized")
                                        for a in p["ancestor_ids"])]
    form_edges = {}
    for form in roots:
        form_edges[form["id"]] = [i for i, body in enumerate(bodies)
            if area(body["B"]) and intersection(body["B"], form["continuous_bbox_px"])
            / area(body["B"]) >= 0.8]
    seed_owners = {i: [f["id"] for f in roots if i in form_edges[f["id"]]]
                   for i in range(len(bodies))}
    cap_edges = {}
    for form in roots:
        cap_edges[form["id"]] = [j for j, cap in enumerate(caps)
            if cap.get("status") == "ok" and cap.get("prefix_label_separator") is True
            and (region := _region(cap, scale)) is not None
            and intersection(region, form["continuous_bbox_px"]) / area(region) >= 0.8
            and intersection(region, form["continuous_bbox_px"]) / area(form["continuous_bbox_px"]) >= 0.8]
    cap_owners = {j: [f["id"] for f in roots if j in cap_edges[f["id"]]]
                  for j in range(len(caps))}
    groups = []
    for form in roots:
        seeds, matches = form_edges[form["id"]], cap_edges[form["id"]]
        if (seeds and len(matches) == 1 and len(cap_owners[matches[0]]) == 1 and
                all(len(seed_owners[i]) == 1 for i in seeds) and
                all(intersection(bodies[i]["B"], _region(caps[matches[0]], scale)) /
                    area(bodies[i]["B"]) >= 0.95 for i in seeds)):
            groups.append({"form": form, "seed_indices": seeds, "caption_index": matches[0]})
    return groups


def form_glyph_ownership(form, placements, blocks, caps, scale):
    """Match source glyph identities to native text-show origins one-to-one."""
    captions = {line for cap in caps if cap.get("status") == "ok"
                for line in cap["source_snap"]["line_ids"]}
    glyphs = []
    for block in blocks:
        for line in block:
            if line["id"] in captions:
                continue
            for index, glyph in enumerate(line["glyphs"]):
                box = [v * scale for v in glyph["bbox_pt"]]
                viewport = form["continuous_bbox_px"]
                overlaps = (box[0] <= viewport[2] and box[2] >= viewport[0] and
                            box[1] <= viewport[3] and box[3] >= viewport[1])
                if overlaps:
                    glyphs.append({"glyph_id": glyph.get("id", f"{line['id']}:{index}"), "line_id": line["id"],
                                   "text": glyph["text"], "bbox_px": box})
    native = [(p["id"], run) for p in placements
              if p["id"] == form["id"] or form["id"] in p["ancestor_ids"]
              for run in p.get("text_glyph_origins", [])]
    edges = {}
    for i, glyph in enumerate(glyphs):
        box = glyph["bbox_px"]
        matches = []
        for j, (_, run) in enumerate(native):
            x, y = (v*scale for v in run["origin_display_pt"])
            em = run["font_height_pt"]*scale
            if (run["text"] == glyph["text"] and abs(x-box[0]) <= 0.5*scale and
                    box[1]-0.25*em <= y <= box[3]+0.5*em):
                matches.append(j)
        edges[i] = matches
    reverse = {j: [i for i, values in edges.items() if j in values] for j in range(len(native))}
    proven, unproven = [], []
    for i, glyph in enumerate(glyphs):
        indices = edges[i]
        if len(indices) == 1 and len(reverse[indices[0]]) == 1:
            form_id, run = native[indices[0]]
            proven.append({**glyph, "form_id": form_id,
                           "origin_display_pt": run["origin_display_pt"]})
        else:
            unproven.append(glyph)
    return proven, unproven


def form_image_ownership(form, trace_images, pdf_images):
    """Bind each renderer image to one native Do invocation, not containment alone."""
    edges = {}
    for i, image in enumerate(trace_images):
        edges[i] = [j for j, call in enumerate(pdf_images) if call["axis_aligned"] and
                    all(abs(a-b) <= 0.001 for a, b in
                        zip(image["continuous_bbox_px"], call["continuous_bbox_px"]))]
    reverse = {j: [i for i, values in edges.items() if j in values] for j in range(len(pdf_images))}
    proven, unproven = [], []
    for i, image in enumerate(trace_images):
        matches = edges[i]
        if (len(matches) == 1 and len(reverse[matches[0]]) == 1 and
                form["id"] in pdf_images[matches[0]]["ancestor_ids"]):
            proven.append({"trace_image_id": image["id"],
                           "pdf_image_id": pdf_images[matches[0]]["id"]})
        else:
            unproven.append(image["id"])
    return proven, unproven


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
