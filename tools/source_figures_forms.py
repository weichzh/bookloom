"""Read PDF Form XObject invocations without treating layout predictions as geometry.

The parser reports unsupported geometry rather than silently claiming coverage. It
uses one PdfReader per worker request, and stores page-space coordinates until the
renderer dimensions are known. Form BBoxes are viewports, not white-panel guesses.
"""
from __future__ import annotations

import math


def _object(value):
    return value.get_object() if hasattr(value, "get_object") else value


def _matrix(values):
    result = tuple(float(x) for x in values)
    if len(result) != 6 or not all(math.isfinite(x) for x in result):
        raise ValueError("invalid PDF transformation matrix")
    return result


def _compose(a, b):
    """Apply b, then a, using the PDF six-number affine representation."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0*b0+a2*b1, a1*b0+a3*b1, a0*b2+a2*b3,
            a1*b2+a3*b3, a0*b4+a2*b5+a4, a1*b4+a3*b5+a5)


def _points(box, matrix):
    a, b, c, d, e, f = matrix
    x0, y0, x1, y1 = box
    return [(a*x+c*y+e, b*x+d*y+f) for x, y in
            ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def _axis_box(points):
    """Return an axis-aligned enclosure and whether it is an exact rectangle."""
    xs, ys = zip(*points)
    box = [min(xs), min(ys), max(xs), max(ys)]
    eps = 1e-7
    corners = {(0 if abs(x-box[0]) < eps else 1 if abs(x-box[2]) < eps else None,
                0 if abs(y-box[1]) < eps else 1 if abs(y-box[3]) < eps else None)
               for x, y in points}
    exact = (len(corners) == 4 and all(None not in p for p in corners) and
             box[2] > box[0] and box[3] > box[1])
    if exact:
        exact = all((abs(x1-x2) < eps) != (abs(y1-y2) < eps)
                    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))
    return box, exact


def _contains(outer, inner):
    eps = 1e-7
    return (outer[0] <= inner[0]+eps and outer[1] <= inner[1]+eps and
            outer[2]+eps >= inner[2] and outer[3]+eps >= inner[3])


def _area(box):
    return max(0., box[2]-box[0]) * max(0., box[3]-box[1])


def _form_text_runs(page, form, display_ctm, resources):
    """Bind direct text-show operations to their transformed glyph origins.

    Only simple horizontal fonts whose encoding and widths are known are
    attributed. Nested Form text is attributed by that invocation, not by
    guessing from a containing rectangle. Unsupported text stays unproven.
    """
    try:
        from pypdf.generic._font import Font
        font_resources = _object(_object(resources).get("/Font", {}))
        fonts = {str(k): Font.from_font_resource(_object(v)) for k, v in font_resources.items()}
    except (ImportError, AttributeError, TypeError, ValueError):
        return [], "unsupported"
    state = {"font": None, "size": 0., "char": 0., "word": 0., "scale": 1., "rise": 0., "mode": 0}
    saved, runs, nested = [], [], 0

    def before(op, args, cm, tm):
        nonlocal nested, state
        if op == b"Do":
            nested += 1
            return
        if nested:
            return
        if op == b"q":
            saved.append(dict(state))
        elif op == b"Q" and saved:
            state = saved.pop()
        elif op == b"Tf":
            state["font"], state["size"] = fonts.get(str(args[0])), float(args[1])
        elif op in (b"Tc", b"Tw", b"Tz", b"Ts", b"Tr"):
            key = {b"Tc": "char", b"Tw": "word", b"Tz": "scale", b"Ts": "rise", b"Tr": "mode"}[op]
            state[key] = float(args[0]) / (100 if op == b"Tz" else 1)
        elif op in (b"Tj", b"TJ") and state["font"] is not None and state["mode"] not in (3, 7):
            font = state["font"]
            if not font.interpretable or not isinstance(font.encoding, dict) or font.sub_type == "Type3":
                return
            parts = args[0] if op == b"TJ" else [args[0]]
            transform = _compose(display_ctm, _compose(_matrix(cm), _matrix(tm)))
            advance = 0.
            for part in parts:
                if isinstance(part, (float, int)):
                    advance -= float(part) * state["size"] * state["scale"] / 1000
                    continue
                try:
                    raw = bytes(part.original_bytes) if hasattr(part, "original_bytes") else bytes(part)
                except (TypeError, ValueError):
                    return
                for value in raw:
                    code = chr(value)
                    character = font.character_map.get(code, font.encoding.get(value))
                    width = font.character_widths.get(code)
                    if not isinstance(character, str) or width is None:
                        return
                    a, b, c, d, e, f = transform
                    if abs(b) > 1e-7 or abs(c) > 1e-7 or a <= 0:
                        return
                    x, y = advance, state["rise"]
                    runs.append({"text": character,
                                 "origin_display_pt": [a*x+c*y+e, b*x+d*y+f],
                                 "font_height_pt": abs(d*state["size"])})
                    advance += ((float(width)*state["size"]/1000 + state["char"] +
                                 (state["word"] if value == 32 else 0.)) * state["scale"])

    def after(op, args, cm, tm):
        nonlocal nested
        if op == b"Do":
            nested -= 1

    try:
        page.extract_xform_text(form, visitor_operand_before=before, visitor_operand_after=after)
    except Exception:
        return [], "unsupported"
    return runs, "ok"


def _display_matrix(page):
    box = [float(v) for v in page.cropbox]
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise ValueError("invalid page CropBox")
    l, b, r, t = box
    if r <= l or t <= b:
        raise ValueError("empty page CropBox")
    unit = float(page.get("/UserUnit", 1))
    if not math.isfinite(unit) or unit <= 0:
        raise ValueError("invalid page UserUnit")
    raw_rotation = float(page.get("/Rotate", 0) or 0)
    if not math.isfinite(raw_rotation) or raw_rotation % 90:
        raise ValueError("unsupported page rotation")
    rotation = int(raw_rotation) % 360
    if rotation == 0:
        transform, size = (unit, 0, 0, -unit, -l*unit, t*unit), ((r-l)*unit, (t-b)*unit)
    elif rotation == 90:
        transform, size = (0, unit, unit, 0, -b*unit, -l*unit), ((t-b)*unit, (r-l)*unit)
    elif rotation == 180:
        transform, size = (-unit, 0, 0, unit, r*unit, -b*unit), ((r-l)*unit, (t-b)*unit)
    elif rotation == 270:
        transform, size = (0, -unit, -unit, 0, t*unit, r*unit), ((t-b)*unit, (r-l)*unit)
    else:
        raise ValueError("unsupported page rotation")
    return transform, size


def optional_capability():
    """A missing optional package never prevents the established model path."""
    import importlib.metadata
    import importlib.util
    try:
        available = importlib.util.find_spec("pypdf") is not None
    except (ImportError, ValueError):
        available = False
    try:
        version = importlib.metadata.version("pypdf") if available else None
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {"available": available, "version": version}


def extract(source, selected):
    """Extract all selected pages with one reader; results keyed by physical page."""
    try:
        from pypdf import PdfReader
        from pypdf.generic import ContentStream
    except ImportError:
        return {n: {"available": False, "status": "unavailable", "reasons": ["pypdf_unavailable"],
                    "placements": [], "image_invocations": []} for n in selected}
    try:
        reader = PdfReader(source)
    except Exception as exc:
        return {n: {"available": True, "status": "unsupported", "reasons": [
            f"pdf_reader_failed:{type(exc).__name__}"], "placements": [], "image_invocations": []} for n in selected}
    result = {}
    for number in selected:
        try:
            result[number] = _extract_page(reader, reader.pages[number-1], ContentStream)
        except Exception as exc:
            result[number] = {"available": True, "status": "unsupported", "reasons": [
                f"page_form_extraction_failed:{type(exc).__name__}"], "placements": [], "image_invocations": []}
    return result


def _extract_page(reader, page, content_stream):
    display, size = _display_matrix(page)
    page_box = [0., 0., *size]
    placements, images, reasons = [], [], []
    path_ops = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
    path_end = {b"n", b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}

    def walk(content, resources, ctm, clips, parents, ancestor_objects):
        if len(parents) > 32:
            reasons.append("form_nesting_limit_exceeded")
            return
        stack, path, pending = [], [], False
        try:
            operations = content_stream(content, reader).operations
        except Exception:
            reasons.append("content_stream_unsupported")
            return
        if parents and any(op in (b"Tj", b"TJ", b"'", b'"') for _, op in operations):
            owner = next(p for p in placements if p["id"] == parents[-1])
            owner["text_glyph_origins"], owner["text_glyph_status"] = _form_text_runs(
                page, content, ctm, resources)
        try:
            xobjects = _object(_object(resources).get("/XObject", {}))
        except (AttributeError, TypeError):
            xobjects = {}
            reasons.append("xobject_resources_unsupported")
        for args, op in operations:
            try:
                if op == b"q":
                    stack.append((ctm, list(clips)))
                elif op == b"Q":
                    if not stack:
                        reasons.append("unbalanced_graphics_state")
                    else:
                        ctm, clips = stack.pop()
                elif op == b"cm":
                    ctm = _compose(ctm, _matrix(args))
                elif op in path_ops:
                    if op == b"re" and len(args) == 4:
                        x, y, w, h = map(float, args)
                        box, exact = _axis_box(_points([x, y, x+w, y+h], ctm))
                        path.append(("rectangle", box, exact))
                    elif op in (b"m", b"l") and len(args) == 2:
                        x, y = map(float, args)
                        point = _points([x, y, x, y], ctm)[0]
                        path.append((op, point))
                    elif op == b"h":
                        path.append((op,))
                    else:
                        path.append(("unsupported",))
                elif op in (b"W", b"W*"):
                    pending = True
                elif op in path_end:
                    if pending:
                        clip = None
                        if len(path) == 1 and path[0][0] == "rectangle" and path[0][2]:
                            clip = path[0][1]
                        elif ([item[0] for item in path] in ([b"m", b"l", b"l", b"l", b"h"],
                                                               [b"m", b"l", b"l", b"l", b"l", b"h"])):
                            points = [item[1] for item in path[:-1]]
                            if len(points) == 5 and points[-1] == points[0]:
                                points.pop()
                            if len(points) == 4:
                                candidate, exact = _axis_box(points)
                                if exact:
                                    clip = candidate
                        if clip is None and parents:
                            next(p for p in placements if p["id"] == parents[-1])["internal_clip_status"] = "unknown"
                        clips = list(clips) + [clip]
                    path, pending = [], False
                elif op == b"Do":
                    ref = xobjects.get(args[0])
                    if ref is None:
                        reasons.append("xobject_reference_missing")
                        continue
                    obj = _object(ref)
                    if obj.get("/Subtype") == "/Image":
                        image_box, image_axis = _axis_box(_points([0, 0, 1, 1], ctm))
                        images.append({"id": f"pdf-image-{len(images)}", "resource_name": str(args[0]),
                                       "parent_id": parents[-1] if parents else None,
                                       "ancestor_ids": list(parents), "bbox_display_pt": image_box,
                                       "axis_aligned": image_axis,
                                       "object_id": (f"{ref.idnum}:{ref.generation}" if hasattr(ref, "idnum") else None)})
                        continue
                    if obj.get("/Subtype") != "/Form":
                        reasons.append("unsupported_xobject_subtype")
                        continue
                    if id(obj) in ancestor_objects:
                        reasons.append("cyclic_form_reference")
                        continue
                    raw_box = [float(v) for v in obj["/BBox"]]
                    if (len(raw_box) != 4 or not all(math.isfinite(v) for v in raw_box)
                            or not _area(raw_box)):
                        reasons.append("invalid_form_bbox")
                        continue
                    matrix = _matrix(obj.get("/Matrix", (1, 0, 0, 1, 0, 0)))
                    child_ctm = _compose(ctm, matrix)
                    box, axis = _axis_box(_points(raw_box, child_ctm))
                    active = [{"status": "unknown" if clip is None else "rectangle", "bbox_pt": clip}
                              for clip in clips]
                    clip_status = ("unknown" if any(c is None for c in clips) else
                                   "partial" if any(not _contains(c, box) for c in clips) else
                                   "fully_contained" if clips else "none")
                    fraction = _area(box) / _area(page_box)
                    form_id = f"native-form-{len(placements)}"
                    record = {"id": form_id, "parent_id": parents[-1] if parents else None,
                              "ancestor_ids": list(parents), "resource_name": str(args[0]),
                              "object_id": (f"{ref.idnum}:{ref.generation}" if hasattr(ref, "idnum") else None),
                              "form_bbox": raw_box, "matrix": list(matrix),
                              "display_transform": list(child_ctm), "bbox_display_pt": box,
                              "axis_aligned": axis, "active_clips": active,
                              "clip_status": clip_status, "page_fraction": fraction,
                              "page_sized": fraction > 0.8,
                              "eligible": axis and clip_status in ("none", "fully_contained")
                              and fraction <= 0.8}
                    placements.append(record)
                    child_resources = _object(obj.get("/Resources", resources))
                    walk(obj, child_resources, child_ctm, list(clips) + [box if axis else None],
                         parents + [form_id], ancestor_objects | {id(obj)})
            except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                reasons.append("unsupported_content_operation")
        if stack:
            reasons.append("unbalanced_graphics_state")
        if pending:
            reasons.append("unfinished_clipping_path")

    walk(page.get_contents(), _object(page.get("/Resources", {})), display, [page_box], [], set())
    reasons = list(dict.fromkeys(reasons))
    return {"available": True, "status": "unsupported" if reasons else "ok", "reasons": reasons,
            "page_size_pt": list(size), "placements": placements, "image_invocations": images}


def pixel_placements(native, scale, width, height):
    try:
        from . import source_figures_logic as logic
    except ImportError:
        import source_figures_logic as logic
    placements = []
    for original in native.get("placements", []):
        item = dict(original)
        continuous = [v*scale for v in item["bbox_display_pt"]]
        item["continuous_bbox_px"] = continuous
        item["bbox_px"] = logic.outward(continuous, width, height)
        placements.append(item)
    image_invocations = []
    for original in native.get("image_invocations", []):
        item = dict(original)
        item["continuous_bbox_px"] = [v*scale for v in item["bbox_display_pt"]]
        item["bbox_px"] = logic.outward(item["continuous_bbox_px"], width, height)
        image_invocations.append(item)
    return {**native, "placements": placements, "image_invocations": image_invocations}
