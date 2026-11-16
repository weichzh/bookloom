"""Frozen native white-panel experiment. Inputs and outputs stay in scratch."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET

EPSILON = 1e-9
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
TOKEN = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?|[A-Za-z]")
TRANSFORM = re.compile(r"matrix\s*\(([^)]*)\)")
DEFINITION_TAGS = {"defs", "clipPath", "mask", "pattern", "marker", "symbol", "filter", "metadata"}
PAINT_TAGS = {"path", "rect", "use", "image", "line", "polyline", "polygon", "circle", "ellipse", "text"}


def tag(e):
    return e.tag.rsplit("}", 1)[-1]


def point(m, xy):
    a, b, c, d, e, f = m
    x, y = xy
    return (a * x + c * y + e, b * x + d * y + f)


def multiply(p, q):
    """Composition p(q(x,y)), using SVG six-number matrices."""
    a, b, c, d, e, f = p
    g, h, i, j, k, l = q
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j,
            a*k+c*l+e, b*k+d*l+f)


def matrix_transform(value):
    result = IDENTITY
    pos = 0
    for match in TRANSFORM.finditer(value):
        if value[pos:match.start()].strip(" ,\t\r\n"):
            raise ValueError(f"unsupported transform: {value!r}")
        raw = [x for x in re.split(r"[\s,]+", match.group(1).strip()) if x]
        if len(raw) != 6:
            raise ValueError(f"invalid matrix: {value!r}")
        m = tuple(float(x) for x in raw)
        if not all(math.isfinite(x) for x in m):
            raise ValueError(f"nonfinite matrix: {value!r}")
        result = multiply(result, m)
        pos = match.end()
    if value[pos:].strip(" ,\t\r\n") or (value.strip() and pos == 0):
        raise ValueError(f"unsupported transform: {value!r}")
    return result


def tokenize_path(d):
    result = []
    pos = 0
    for match in TOKEN.finditer(d):
        if d[pos:match.start()].strip(" ,\t\r\n"):
            raise ValueError("invalid path token")
        result.append(match.group())
        pos = match.end()
    if d[pos:].strip(" ,\t\r\n"):
        raise ValueError("invalid path trailing token")
    return result


def is_command(t):
    return len(t) == 1 and t.isalpha()


def subpaths(d):
    """Yield closed or rejected simple SVG line subpaths without bbox guessing."""
    try:
        tokens = tokenize_path(d)
    except ValueError as exc:
        return [{"reason": str(exc), "points": None}]
    starts = [i for i, t in enumerate(tokens) if t in ("M", "m")]
    if not starts or starts[0] != 0:
        return [{"reason": "missing_initial_move", "points": None}]
    out = []
    current = (0.0, 0.0)
    for n, start in enumerate(starts):
        stop = starts[n+1] if n+1 < len(starts) else len(tokens)
        ts = tokens[start:stop]
        current, record = parse_subpath(ts, current)
        out.append(record)
    return out


def parse_subpath(ts, current):
    i = 1
    nums = []
    while i < len(ts) and not is_command(ts[i]):
        nums.append(float(ts[i]))
        i += 1
    if len(nums) < 2 or len(nums) % 2:
        return None, {"reason": "invalid_move", "points": None}
    if ts[0] == "m" and current is None:
        return None, {"reason": "relative_move_after_unsupported_path", "points": None}
    xy = (nums[0], nums[1])
    if ts[0] == "m":
        xy = (xy[0] + current[0], xy[1] + current[1])
    points = [xy]
    for k in range(2, len(nums), 2):
        q = (nums[k], nums[k+1])
        if ts[0] == "m":
            q = (xy[0] + q[0], xy[1] + q[1])
        points.append(q)
        xy = q
    closed = False
    while i < len(ts):
        cmd = ts[i]
        i += 1
        if not is_command(cmd):
            return None, {"reason": "missing_command", "points": None}
        if cmd in ("Z", "z"):
            if i != len(ts):
                return None, {"reason": "tokens_after_close", "points": None}
            closed = True
            xy = points[0]
            break
        upper = cmd.upper()
        if upper not in ("L", "H", "V"):
            return None, {"reason": f"unsupported_or_curved_command_{cmd}", "points": None}
        vals = []
        while i < len(ts) and not is_command(ts[i]):
            vals.append(float(ts[i]))
            i += 1
        stride = 2 if upper == "L" else 1
        if not vals or len(vals) % stride:
            return None, {"reason": "invalid_line_command", "points": None}
        for j in range(0, len(vals), stride):
            if upper == "L":
                q = (vals[j], vals[j+1])
                if cmd.islower():
                    q = (q[0] + xy[0], q[1] + xy[1])
            elif upper == "H":
                q = (xy[0] + vals[j] if cmd.islower() else vals[j], xy[1])
            else:
                q = (xy[0], xy[1] + vals[j] if cmd.islower() else vals[j])
            points.append(q)
            xy = q
    if not closed:
        return xy, {"reason": "open_subpath", "points": None}
    if len(points) > 1 and points[-1] == points[0]:
        points.pop()
    return xy, {"reason": None, "points": points}


def rect_bounds(points):
    if len(points) != 4 or not all(math.isfinite(v) for p in points for v in p):
        return None
    xs, ys = zip(*points)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1-x0 <= EPSILON or y1-y0 <= EPSILON:
        return None
    corners = set()
    for x, y in points:
        xx = 0 if abs(x-x0) <= EPSILON else 1 if abs(x-x1) <= EPSILON else None
        yy = 0 if abs(y-y0) <= EPSILON else 1 if abs(y-y1) <= EPSILON else None
        if xx is None or yy is None or (xx, yy) in corners:
            return None
        corners.add((xx, yy))
    for p, q in zip(points, points[1:] + points[:1]):
        dx, dy = abs(p[0]-q[0]), abs(p[1]-q[1])
        if not ((dx <= EPSILON and dy > EPSILON) or (dy <= EPSILON and dx > EPSILON)):
            return None
    return [x0, y0, x1, y1]


def props(e):
    result = dict(e.attrib)
    for declaration in e.get("style", "").split(";"):
        if ":" in declaration:
            k, v = declaration.split(":", 1)
            result[k.strip()] = v.strip().removesuffix("!important").strip()
    return result


def alpha(s):
    s = str(s).strip()
    x = float(s[:-1])/100 if s.endswith("%") else float(s)
    if not math.isfinite(x) or not 0 <= x <= 1:
        raise ValueError(f"invalid SVG opacity: {s!r}")
    return x


def color(s):
    s = s.strip().lower()
    if s == "none":
        return None
    if s in ("white", "black"):
        return ((1.0, 1.0, 1.0) if s == "white" else (0.0, 0.0, 0.0), 1.0)
    if s.startswith("#") and len(s) in (4, 5, 7, 9):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(x*2 for x in h)
        try:
            vals = [int(h[i:i+2], 16)/255 for i in range(0, len(h), 2)]
        except ValueError:
            return None
        return (tuple(vals[:3]), vals[3] if len(vals) == 4 else 1.0)
    m = re.fullmatch(r"(rgb|rgba)\(([^)]*)\)", s)
    if m:
        vals = [v.strip() for v in m.group(2).split(",")]
        if len(vals) != (4 if m.group(1) == "rgba" else 3):
            return None
        try:
            rgb = tuple(float(v[:-1])/100 if v.endswith("%") else float(v)/255 for v in vals[:3])
            return (rgb, alpha(vals[3]) if len(vals) == 4 else 1.0)
        except ValueError:
            return None
    return None


def apply_state(parent, e, location):
    a = props(e)
    m = multiply(parent["matrix"], matrix_transform(a.get("transform", "")))
    fill = a.get("fill", parent["fill"])
    fill_opacity = alpha(a["fill-opacity"]) if "fill-opacity" in a else parent["fill_opacity"]
    fo_source = location if "fill-opacity" in a else parent["fill_opacity_source"]
    own_opacity = alpha(a["opacity"]) if "opacity" in a else 1.0
    op_sources = parent["opacity_sources"] + ([location] if "opacity" in a else [])
    clips = parent["clips"] + ([a["clip-path"]] if "clip-path" in a else [])
    display = parent["display"] and a.get("display", "inline") != "none"
    visibility = a.get("visibility", parent["visibility"])
    return {"matrix": m, "fill": fill, "fill_opacity": fill_opacity,
            "fill_opacity_source": fo_source, "opacity": parent["opacity"] * own_opacity,
            "opacity_sources": op_sources, "clips": clips, "display": display,
            "visibility": visibility, "fill_rule": a.get("fill-rule", parent["fill_rule"])}


def default_state():
    return {"matrix": IDENTITY, "fill": "#000000", "fill_opacity": 1.0,
            "fill_opacity_source": "svg-default-1", "opacity": 1.0,
            "opacity_sources": [], "clips": [], "display": True,
            "visibility": "visible", "fill_rule": "nonzero"}


def rectangle_items(e):
    t = tag(e)
    if t == "path":
        return subpaths(e.get("d", ""))
    a = props(e)
    if float(a.get("rx", "0")) != 0 or float(a.get("ry", "0")) != 0:
        return [{"reason": "rounded_rect", "points": None}]
    x, y = float(a.get("x", "0")), float(a.get("y", "0"))
    w, h = float(a.get("width", "0")), float(a.get("height", "0"))
    return [{"reason": None, "points": [(x,y), (x+w,y), (x+w,y+h), (x,y+h)]}]


def svg_candidates(root, page_area):
    """Enumerate actual paint-order rectangles and retain their rejection reasons."""
    records, nonrect, skipped = [], [], Counter()
    sequence = 0

    def walk(e, parent, location):
        nonlocal sequence
        t = tag(e)
        if t in DEFINITION_TAGS:
            skipped[t] += 1
            return
        state = apply_state(parent, e, location)
        if t in PAINT_TAGS:
            sequence += 1
        if t in ("path", "rect"):
            paint = color(state["fill"])
            rgb, color_alpha = paint if paint else (None, None)
            effective = state["opacity"] * state["fill_opacity"] * color_alpha if paint else None
            paint_reasons = []
            if rgb is None:
                paint_reasons.append("fill_none_or_unsupported")
            elif not all(0.95 <= x <= 1.0 for x in rgb):
                paint_reasons.append("fill_below_rgb_threshold")
            if effective is None or effective < 0.99:
                paint_reasons.append("effective_fill_opacity_below_threshold_or_unknown")
            if not state["display"] or state["visibility"] in ("hidden", "collapse"):
                paint_reasons.append("not_visible")
            items = rectangle_items(e)
            for index, item in enumerate(items):
                common = {"paint_order": sequence, "element_location": location,
                          "subpath_index": index, "element": t,
                          "fill_raw": state["fill"], "fill_rgb": rgb,
                          "fill_opacity": state["fill_opacity"],
                          "fill_opacity_source": state["fill_opacity_source"],
                          "opacity_product": state["opacity"],
                          "opacity_sources": state["opacity_sources"] or ["svg-default-1"],
                          "color_alpha": color_alpha, "effective_fill_opacity": effective,
                          "fill_rule": state["fill_rule"], "subpath_count": len(items),
                          "clip_paths": state["clips"], "matrix": state["matrix"]}
                if t == "path":
                    common["path_d"] = e.get("d", "")
                else:
                    common["rect_attributes"] = {k: e.get(k) for k in ("x", "y", "width", "height", "rx", "ry") if e.get(k) is not None}
                if item["points"] is None:
                    if not paint_reasons:
                        nonrect.append({**common, "reasons": [item["reason"]]})
                    continue
                pts = [point(state["matrix"], p) for p in item["points"]]
                box = rect_bounds(pts)
                if box is None:
                    if not paint_reasons:
                        nonrect.append({**common, "reasons": ["not_simple_axis_aligned_rectangle"]})
                    continue
                x0, y0, x1, y1 = box
                area = (x1-x0)*(y1-y0)
                fraction = area/page_area
                reasons = list(paint_reasons)
                if x1-x0 < 8 or y1-y0 < 8:
                    reasons.append("dimension_under_8pt")
                if fraction < 0.005 or fraction > 0.8:
                    reasons.append("page_area_fraction_outside_0.005_to_0.8")
                records.append({**common, "corners_display_pt": pts, "bbox_display_pt": box,
                                "area_pt2": area, "page_area_fraction": fraction,
                                "eligible_before_body": not reasons, "reasons": reasons})
        elif t in PAINT_TAGS and t not in ("path", "rect"):
            skipped[t] += 1
        for i, child in enumerate(e):
            walk(child, state, f"{location}/{tag(child)}[{i}]")

    walk(root, default_state(), "svg")
    return records, nonrect, dict(skipped)


def mapped_box(box, matrix):
    x0, y0, x1, y1 = box
    corners = [point(matrix, p) for p in ((x0,y0), (x1,y0), (x1,y1), (x0,y1))]
    xs, ys = zip(*corners)
    return [min(xs), min(ys), max(xs), max(ys)]


def clipped_outward(box, width, height):
    return [max(0, min(width, math.floor(box[0]))),
            max(0, min(height, math.floor(box[1]))),
            max(0, min(width, math.ceil(box[2]))),
            max(0, min(height, math.ceil(box[3])))]


def body_checks(panel_box, body_box):
    x0, y0, x1, y1 = body_box
    if not all(math.isfinite(x) for x in body_box) or x1 <= x0 or y1 <= y0:
        raise ValueError("invalid B bbox")
    px0, py0, px1, py1 = panel_box
    cx, cy = (x0+x1)/2, (y0+y1)/2
    center_inside = px0 <= cx <= px1 and py0 <= cy <= py1
    inter = max(0, min(x1, px1)-max(x0, px0)) * max(0, min(y1, py1)-max(y0, py0))
    coverage = inter / ((x1-x0)*(y1-y0))
    reasons = ([] if center_inside else ["body_center_outside_panel"])
    if coverage < 0.8:
        reasons.append("intersection_over_B_below_0.8")
    return {"center_inside": center_inside, "intersection_over_B": coverage, "reasons": reasons}


def choose(records, body):
    checks, valid = [], []
    for r in records:
        c = body_checks(r["bbox_xyxy_px_continuous"], body)
        reasons = r["reasons"] + c["reasons"]
        checks.append({"panel_id": r["panel_id"], "center_inside": c["center_inside"],
                       "intersection_over_B": c["intersection_over_B"], "reasons": reasons})
        if not reasons:
            valid.append(r)
    valid.sort(key=lambda r: (r["area_pt2"], r["paint_order"], r["subpath_index"]))
    return (valid[0] if valid else None), checks


def page_geometry(root, metadata):
    vb = [float(x) for x in re.split(r"[\s,]+", root.attrib["viewBox"].strip())]
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        raise ValueError("invalid SVG viewBox")
    if not (abs(vb[0]) <= 0.01 and abs(vb[1]) <= 0.01):
        raise ValueError("nonzero SVG viewBox origin not covered by input mapping")
    c = metadata["cropbox_pt"]
    rotation = int(metadata["rotation_deg"]) % 360
    cw, ch = c[2]-c[0], c[3]-c[1]
    expected = (cw, ch) if rotation in (0, 180) else (ch, cw) if rotation in (90, 270) else None
    if expected is None or any(abs(a-b) > 0.02 for a, b in zip(vb[2:], expected)):
        raise ValueError(f"SVG viewBox disagrees with CropBox/rotation: {vb}, {c}, {rotation}")
    if int(metadata["dpi"]) != 225:
        raise ValueError("input DPI is not frozen 225")
    affine = metadata["mutool_display_pt_to_pixel_affine"]
    if len(affine) != 6:
        raise ValueError("invalid display-point affine")
    s = 225/72
    if any(abs(a-b) > 1e-8 for a, b in zip(affine, [s,0,0,s,0,0])):
        raise ValueError(f"SVG display-point affine not the declared unshifted 225 dpi mapping: {affine}")
    bbox = mapped_box([vb[0], vb[1], vb[0]+vb[2], vb[1]+vb[3]], affine)
    w, h = metadata["width_px"], metadata["height_px"]
    if (abs(bbox[0]) > 0.01 or abs(bbox[1]) > 0.01 or
        not (-0.01 <= w-bbox[2] <= 1.01) or not (-0.01 <= h-bbox[3] <= 1.01)):
        raise ValueError(f"SVG-to-pixel mapping disagrees with raster extent: {bbox}, {(w,h)}")
    return {"viewBox": vb, "cropbox_pt": c, "rotation_deg": rotation,
            "display_pt_to_pixel_affine": affine, "mapped_viewport_px": bbox,
            "area_pt2": vb[2]*vb[3]}


def tight_ink_bbox(image, box):
    import numpy as np
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return None
    region = np.asarray(image.crop((x0,y0,x1,y1)).convert("L"))
    ys, xs = np.nonzero(region < 239)
    if len(xs) == 0:
        return None
    return [int(x0+xs.min()), int(y0+ys.min()), int(x0+xs.max()+1), int(y0+ys.max()+1)]


def routes_for(selected, bcc, gray_image, w, h):
    if selected is None:
        return {"Npanel": {"status": "not_applicable", "bbox_xyxy_px": None},
                "NpanelTrim": {"status": "not_applicable", "bbox_xyxy_px": None},
                "Hpanel": {**copy.deepcopy(bcc), "branch": "base_Bcc"}}
    bb = clipped_outward(selected["bbox_xyxy_px_continuous"], w, h)
    ink = tight_ink_bbox(gray_image, bb)
    trim = {"status": "ok" if ink is not None else "no_ink", "bbox_xyxy_px": ink}
    return {"Npanel": {"status": "ok", "bbox_xyxy_px": bb}, "NpanelTrim": trim,
            "Hpanel": {**trim, "branch": "painted_panel"}}
