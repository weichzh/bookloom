"""Configured-Python worker for source figure candidates; all outputs stay in scratch."""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import traceback
import xml.etree.ElementTree as ET

try:
    from .browser_runtime import run_hidden
    from . import source_figures_caption as caption
    from . import source_figures_logic as logic
    from . import source_figures_forms as forms
    from . import source_figures_panel as panel
    from . import source_figures_render as rendering
    from . import source_figures_ink as ink
except ImportError:  # direct execution by the configured Python
    from browser_runtime import run_hidden
    import source_figures_caption as caption
    import source_figures_logic as logic
    import source_figures_forms as forms
    import source_figures_panel as panel
    import source_figures_render as rendering
    import source_figures_ink as ink


def versions():
    distributions = {}
    for name in ("rapid-doc", "onnxruntime", "onnxruntime-gpu", "numpy", "Pillow",
                 "opencv-python", "opencv-python-headless"):
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    required = ("rapid-doc", "numpy", "Pillow")
    if (any(k not in distributions for k in required) or
        not any(k in distributions for k in ("onnxruntime", "onnxruntime-gpu")) or
        not any(k in distributions for k in ("opencv-python", "opencv-python-headless"))):
        raise RuntimeError("configured Python lacks required local model dependencies")
    return {"executable": str(Path(sys.executable).resolve()), "python": sys.version,
            "platform": platform.platform(), "distributions": distributions,
            "optional_capabilities": {"pypdf": forms.optional_capability()}}


def write_json(path, data):
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def run(command, cwd):
    result = run_hidden([str(x) for x in command], cwd=cwd, env=dict(os.environ),
                        capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise RuntimeError(f"{Path(str(command[0])).name} failed ({result.returncode}): "
                           f"{result.stderr[-2000:]}")
    return result


def pdf_rotation(mutool, source, page, cwd):
    path = f"pages/{page}"
    for _ in range(64):
        value = run([mutool, "show", source, path + "/Rotate"], cwd).stdout.strip()
        if value != "null":
            try:
                number = int(value)
            except ValueError as exc:
                raise ValueError("invalid inherited PDF rotation") from exc
            if number % 90:
                raise ValueError("unsupported non-right-angle PDF rotation")
            return number % 360
        parent = run([mutool, "show", source, path + "/Parent/Type"], cwd).stdout.strip()
        if parent == "null":
            return 0
        path += "/Parent"
    raise ValueError("PDF page-tree depth exceeded")


def extract_native(runtime, source, jobs, directory):
    raw = directory / "native-raw.json"
    timing = directory / "native-timing.json"
    home = Path(os.environ["HOME"])
    args = [runtime["java"], "-Xmx2g", "-Djava.awt.headless=true", "-Dfile.encoding=UTF-8",
            f"-Duser.home={home}", f"-Djava.io.tmpdir={os.environ['TEMP']}", "-cp",
            runtime["native_jar"], "org.allenai.pdffigures2.NativeAuditRunner",
            source, raw, timing, ",".join(str(j["page"] - 1) for j in jobs)]
    result = run(args, directory)
    (directory / "native.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    data = json.loads(raw.read_text(encoding="utf-8"))
    if not isinstance(data.get("figures"), list) or not isinstance(data.get("regionless-captions"), list):
        raise ValueError("invalid native caption extractor output")
    if not timing.is_file():
        raise ValueError("native extractor did not write timing")
    return data, raw, timing


def _page_inputs(paths, prediction, dpi, rotation):
    text_root, _ = caption.parse_xml(paths["stext"])
    pages = text_root.findall("page")
    if len(pages) != 1:
        raise ValueError("source text output must contain one page")
    width_pt, height_pt = float(pages[0].attrib["width"]), float(pages[0].attrib["height"])
    w, h = prediction["width_px"], prediction["height_px"]
    scale = dpi / 72
    # MuPDF reports both SVG and structured-text coordinates in displayed
    # CropBox space; do not infer PDF user-space offsets from these boxes.
    md = {"width_px": w, "height_px": h, "dpi": dpi,
          "cropbox_pt": [0, 0, width_pt, height_pt], "rotation_deg": 0,
          "mutool_display_pt_to_pixel_affine": [scale, 0, 0, scale, 0, 0]}
    svg = ET.parse(paths["svg"]).getroot()
    geometry = panel.page_geometry(svg, md)
    geometry["source_rotation_deg"] = rotation
    return md, svg, geometry


def _captions(native, number, paths, md, rotation):
    if rotation:
        return [], [], "rotated_source_caption_mapping_unsupported", None
    blocks, removed = caption.load_lines(paths["stext"], md)
    entries = caption.cap_raw(native, number)
    caps = [caption.align_caption(entry, blocks) for entry in entries]
    scale = md["dpi"] / 72
    for cap in caps:
        if cap["status"] == "ok":
            snap = cap["source_snap"]
            snap["bbox_px"] = [x * scale for x in snap["source_snap_bbox_pt"]]
    return blocks, caps, None, removed


def _hcaption(body, rectangles, caps, source_image, w, h):
    picked, checks = panel.choose(rectangles, body["B"])
    if picked is None:
        return {"branch": "base_Bcc", "bbox_px": body["Bcc"],
                "status": "ok" if body["Bcc"] else "no_ink", "panel_checks": checks}
    continuous = picked["bbox_xyxy_px_continuous"]
    box = panel.clipped_outward(continuous, w, h)
    result = {"branch": "panel_caption_barrier", "selected_panel": picked,
              "panel_checks": checks, "bbox_px": None}
    if not caps or any(c["status"] != "ok" for c in caps):
        result.update(status="unavailable", reason="page_caption_alignment_unavailable_or_ambiguous")
        return result
    cropped, moves, reason = caption.barrier(box, continuous, body["B"], caps, w, h)
    result["barrier_moves"] = moves
    if reason:
        result.update(status="ambiguous", reason=reason)
        return result
    tight = caption.ink_bbox(source_image, cropped)
    result.update(status="ok" if tight else "no_ink", bbox_px=tight, barrier_panel_px=cropped)
    return result


def process_page(job, request, native, native_raw, native_timing, layout, native_forms=None):
    from PIL import Image

    directory = Path(job["directory"])
    number, source, runtime, dpi = job["page"], request["source"], request["runtime"], request["dpi"]
    reused = "reuse_source_files" in job or "reuse_rotation_deg" in job
    if reused:
        raw_paths, rotation = job.get("reuse_source_files"), job.get("reuse_rotation_deg")
        if (not isinstance(raw_paths, dict) or set(raw_paths) != {"png", "svg", "stext", "trace"}
                or any(not isinstance(p, str) or not Path(p).is_file() for p in raw_paths.values())
                or rotation not in (0, 90, 180, 270)):
            raise ValueError("invalid reusable source artifacts or rotation")
        paths = {name: Path(path) for name, path in raw_paths.items()}
    else:
        paths = rendering.render(source, number, dpi, runtime, directory, run)
        rotation = pdf_rotation(runtime["mutool"], source, number, directory)
    raw = directory / "raw.npz"
    predicted = layout.predict(paths["png"], raw,
                               reuse_raw=Path(job["reuse_raw"]) if job["reuse_raw"] else None)
    md, svg, geometry = _page_inputs(paths, predicted, dpi, rotation)
    blocks, caps, text_error, removed = _captions(native, number, paths, md, rotation)
    scale = dpi / 72
    w, h = predicted["width_px"], predicted["height_px"]
    records, nonrect, skipped = panel.svg_candidates(svg, geometry["area_pt2"])
    for index, record in enumerate(records):
        record["panel_id"] = f"panel-{index}"
        record["bbox_xyxy_px_continuous"] = panel.mapped_box(
            record["bbox_display_pt"], md["mutool_display_pt_to_pixel_affine"])
    placements = logic.native_images(paths["trace"], scale, w, h)
    bodies = predicted["body_candidates"]
    native_forms = forms.pixel_placements(native_forms or {
        "available": False, "status": "unavailable", "reasons": ["pypdf_unavailable"],
        "placements": []}, scale, w, h)
    form_placements = native_forms["placements"]
    groups = logic.native_form_groups(bodies, caps, form_placements, scale)
    page_ink = getattr(layout, "page_ink", None)
    if not isinstance(page_ink, ink.PageInk):
        page_ink = None
    region_checks = []
    if groups:
        if page_ink is None:
            with Image.open(paths["png"]) as page_image:
                page_ink = ink.PageInk(page_image)
        accepted = []
        for group in groups:
            region = logic.outward(logic._region(caps[group["caption_index"]], scale), w, h)
            check = page_ink.region_consistency(group["form"]["bbox_px"], region)
            check["seed_visible_pixels"] = [page_ink.visible_pixels(logic.outward(bodies[i]["B"], w, h))
                                             for i in group["seed_indices"]]
            if check["status"] == "consistent" and not all(check["seed_visible_pixels"]):
                check["status"] = "seed_has_no_visible_ink"
            check.update(form_id=group["form"]["id"], caption_index=group["caption_index"],
                         model_candidate_ids=[bodies[i]["candidate_id"] for i in group["seed_indices"]])
            region_checks.append(check)
            if check["status"] == "consistent":
                group["region_consistency"] = check
                accepted.append(group)
        groups = accepted
    grouped = {i: group for group in groups for i in group["seed_indices"]}
    consumed_caps = {g["caption_index"] for g in groups}
    units = []
    for i, body in enumerate(bodies):
        if i in grouped:
            group = grouped[i]
            if i != group["seed_indices"][0]:
                continue
            units.append({"seeds": [bodies[j] for j in group["seed_indices"]],
                          "form": group["form"], "caption_index": group["caption_index"],
                          "region_consistency": group["region_consistency"]})
        else:
            units.append({"seeds": [body], "form": None, "caption_index": None})
    remaining_units = [i for i, unit in enumerate(units) if unit["form"] is None]
    remaining_caps = [i for i in range(len(caps)) if i not in consumed_caps]
    page_reasons = []
    if remaining_units or remaining_caps:
        local_pairs, page_reasons = logic.pair_captions(
            [units[i]["seeds"][0] for i in remaining_units], [caps[i] for i in remaining_caps], scale)
        for i, j in local_pairs.items():
            units[remaining_units[i]]["caption_index"] = remaining_caps[j]
    if any(check["status"] == "unassociated_ink" for check in region_checks):
        page_reasons.append("native_form_region_contains_unassociated_visible_ink")
    if any(check["status"] in ("no_visible_ink", "seed_has_no_visible_ink") for check in region_checks):
        page_reasons.append("native_form_or_seed_has_no_visible_ink")
    if text_error:
        page_reasons.append(text_error)
    if (bodies or caps or form_placements or placements) and any(
            line.get("error") for block in blocks for line in block):
        page_reasons.append("source_glyph_geometry_unavailable")
    if removed:
        page_reasons.append("source_text_illegal_xml_characters_removed")
    if native_forms["status"] == "unsupported":
        page_reasons.append("native_form_extraction_unsupported")
    if any(not p["eligible"] and p["page_fraction"] > 0.8 for p in placements):
        page_reasons.append("page_sized_native_image_requires_review")
    if any(p["page_sized"] and p["parent_id"] is None for p in form_placements):
        page_reasons.append("page_sized_native_form_requires_review")
    root_forms = [p for p in form_placements if p["parent_id"] is None]
    if not bodies and root_forms:
        page_reasons.append("native_form_without_model_seed")
    if not bodies and placements:
        page_reasons.append("native_image_without_model_seed")
    objects = []
    with Image.open(paths["png"]) as image:
        image = image.convert("RGB")
        for index, unit in enumerate(units):
            body, form = unit["seeds"][0], unit["form"]
            cap_index = unit["caption_index"]
            reasons = []
            if not form and body.get("ambiguous_weak_component_count", 0):
                reasons.append("connected_pale_edge_ownership_ambiguous")
            route = ({"branch": "native_form_viewport", "bbox_px": form["bbox_px"], "status": "ok"}
                     if form else _hcaption(body, records, caps, image, w, h))
            matches = ([p for p in placements if logic.intersection(form["bbox_px"], p["bbox_px"])]
                       if form else logic.select_native_images(body["B"], placements))
            if not form and len(matches) > 1:
                reasons.append("multiple_native_images_for_one_seed")
            if (not form and len(matches) == 1 and logic.intersection(body["B"], matches[0]["continuous_bbox_px"])
                    / logic.area(body["B"]) < 0.8):
                reasons.append("native_image_covers_only_part_of_seed")
            if not form and any(m["clip_status"] == "unresolved" for m in matches):
                reasons.append("native_image_clip_not_proven_to_contain_placement")
            glyphs = logic.independent_glyph_lines(blocks, form["bbox_px"] if form else body["B"], caps, scale)
            if form:
                proven_glyphs, unproven_glyphs = logic.form_glyph_ownership(
                    form, form_placements, blocks, caps, scale)
                proven_images, unproven_images = logic.form_image_ownership(
                    form, matches, native_forms["image_invocations"])
                if unproven_images:
                    reasons.append("native_form_intersects_unassociated_image")
                for other in root_forms:
                    if (other["id"] != form["id"] and other["id"] not in form["ancestor_ids"] and
                            logic.intersection(other["bbox_px"], form["bbox_px"])):
                        reasons.append("native_form_intersects_other_form")
                        break
                region = logic._region(caps[cap_index], scale)
                if any(seed not in unit["seeds"] and logic.intersection(seed["B"], region) for seed in bodies):
                    reasons.append("native_caption_region_contains_unassociated_seed")
                if any(other["id"] != form["id"] and other["id"] not in form["ancestor_ids"]
                       and logic.intersection(other["bbox_px"], region) for other in root_forms):
                    reasons.append("native_caption_region_contains_other_form")
            else:
                proven_glyphs = []
                proven_images, unproven_images = [], []
                unproven_glyphs = logic.unproven_glyph_ownership(glyphs, matches)
                for other in form_placements:
                    if (not other["eligible"] and logic.area(body["B"]) and
                            logic.intersection(body["B"], other["continuous_bbox_px"]) / logic.area(body["B"]) >= 0.8):
                        reasons.append("native_form_geometry_or_clip_unresolved")
                        break
            if unproven_glyphs:
                reasons.append("native_glyph_ownership_unproven")
            boxes = []
            if route["bbox_px"]:
                boxes.append(route["bbox_px"])
            if not form and len(matches) == 1:
                boxes.append(matches[0]["bbox_px"])
                # Independent legend glyphs can lie outside the image XObject.
                boxes.extend(g["bbox_px"] for g in glyphs)
            if route["status"] not in {"ok", "no_ink"}:
                reasons.append("hcaption_unavailable_or_ambiguous")
            if not boxes:
                reasons.append("empty_body")
                crop = None
            else:
                crop = logic.outward(logic.union(boxes), w, h)
                if not logic.area(crop):
                    crop = None
                    reasons.append("empty_body")
            if route.get("selected_panel", {}).get("clip_paths"):
                reasons.append("native_panel_clipping_requires_review")
            if cap_index is None:
                reasons.append("caption_owner_unresolved")
            uncovered = (logic.uncovered_region_glyphs(crop, caps[cap_index], blocks, caps, scale)
                         if cap_index is not None else [])
            uncovered, metric_overhangs = logic.split_form_metric_overhangs(
                uncovered, form if native_forms["status"] == "ok" else None,
                form_placements, crop, proven_glyphs)
            if uncovered:
                reasons.append("native_region_contains_uncovered_glyphs")
            caption_intrusions = logic.overlapping_caption_bounds(crop, caps)
            if caption_intrusions:
                reasons.append("crop_intersects_caption_glyph_bounds")
            unassigned_text = logic.unassigned_text_bounds(crop, blocks, caps, glyphs, scale)
            if unassigned_text:
                reasons.append("crop_intersects_unassigned_native_text")
            seed_ink_check = None
            if (route["branch"] == "panel_caption_barrier" and route.get("barrier_panel_px") and
                    crop is not None):
                if page_ink is None:
                    page_ink = ink.PageInk(image)
                selected = panel.clipped_outward(route["selected_panel"]["bbox_xyxy_px_continuous"], w, h)
                seed_ink_check = page_ink.barrier_loss(logic.outward(body["B"], w, h), selected, crop)
                if seed_ink_check["strict_lost_pixels"]:
                    reasons.append("caption_barrier_cuts_seed_connected_ink")
                if seed_ink_check.get("ambiguous_weak_component_count", 0):
                    reasons.append("caption_barrier_pale_edge_ownership_ambiguous")
            image_path = None
            if crop is not None:
                image_path = directory / f"figure-{index + 1}.png"
                image.crop(tuple(crop)).save(image_path)
            losses = ([logic.rectangle_loss_pixels(m["bbox_px"], crop) for m in matches]
                      if crop is not None else [])
            objects.append({"candidate_id": body["candidate_id"], "model_seed": body,
                            "model_seeds": unit["seeds"], "native_form": form,
                            "provenance": {"geometry": "native_form_viewport" if form else "model_and_native_images",
                                           "model_candidate_ids": [s["candidate_id"] for s in unit["seeds"]],
                                           "native_form_id": form["id"] if form else None,
                                           "caption_index": cap_index,
                                           "region_consistency": unit.get("region_consistency")},
                            "bbox_xyxy_px": crop, "image": str(image_path) if image_path else None,
                            "native_image_placements": matches,
                            "proven_native_form_images": proven_images,
                            "unproven_native_form_images": unproven_images,
                            "native_placement_lost_pixels": losses,
                            "native_placement_loss_meaning": "rectangle_area_not_visible_pixel_loss",
                            "independent_glyph_lines": glyphs, "hcaption": route,
                            "proven_native_form_glyphs": proven_glyphs,
                            "unproven_glyph_ownership": unproven_glyphs,
                            "uncovered_native_region_glyphs": uncovered,
                            "native_form_metric_overhangs": metric_overhangs,
                            "seed_connected_ink_check": seed_ink_check,
                            "caption_intrusions": caption_intrusions,
                            "unassigned_text_intrusions": unassigned_text,
                            "caption": caps[cap_index] if cap_index is not None else None,
                            "status": "needs_review" if reasons else "candidate", "reasons": reasons})
    for i, first in enumerate(objects):
        for second in objects[i + 1:]:
            shared_image = {x["id"] for x in first["native_image_placements"]} & {
                x["id"] for x in second["native_image_placements"]}
            shared_glyph = {x["line_id"] for x in first["independent_glyph_lines"]} & {
                x["line_id"] for x in second["independent_glyph_lines"]}
            overlapping = (first["bbox_xyxy_px"] and second["bbox_xyxy_px"] and
                           logic.intersection(first["bbox_xyxy_px"], second["bbox_xyxy_px"]))
            if shared_image or shared_glyph or overlapping:
                for obj in (first, second):
                    obj["reasons"].append("candidate_ownership_or_bounds_overlap")
                    obj["status"] = "needs_review"
    claimed = {p["id"] for group in groups for p in form_placements
               if p["id"] == group["form"]["id"] or p["id"] in group["form"]["ancestor_ids"]}
    if bodies and any(p["id"] not in claimed for p in root_forms):
        page_reasons.append("native_form_without_resolved_group")
    used_images = {p["id"] for obj in objects for p in obj["native_image_placements"]}
    if bodies and any(p["id"] not in used_images for p in placements):
        page_reasons.append("native_image_without_resolved_candidate")
    for obj in objects:
        obj["reasons"] = list(dict.fromkeys(obj["reasons"]))
        obj["review_required"] = bool(obj["reasons"])
        obj["review_reasons"] = list(obj["reasons"])
    page_reasons = list(dict.fromkeys(page_reasons))
    no_candidates = not bodies and not caps and not page_reasons
    review_required = bool(page_reasons or any(obj["review_required"] for obj in objects))
    deps = [str(p) for p in paths.values()] + [str(native_raw), str(native_timing),
            predicted["raw_tensor_npz"]] + [o["image"] for o in objects if o["image"]]
    return {"page": number, "status": "needs_review" if review_required else "candidates",
            "verified": False, "reasons": page_reasons, "dpi": dpi,
            "review_required": review_required, "review_reasons": list(page_reasons),
            "no_candidates": no_candidates, "information": ["no_candidates"] if no_candidates else [],
            "coverage": "not_established",
            "source_artifacts_reused": reused,
            "grouped_seed_count": sum(len(g["seed_indices"]) for g in groups if len(g["seed_indices"]) > 1),
            "width_px": w, "height_px": h, "geometry": geometry,
            "source_files": {k: str(v) for k, v in paths.items()},
            "caption_alignment": caps, "source_text_error": text_error,
            "illegal_xml_characters_removed": removed, "native_images": placements, "native_forms": native_forms,
            "native_form_region_checks": region_checks,
            "panel_rejected_nonrectangles": len(nonrect), "skipped_svg_elements": skipped,
            "model_prediction": predicted, "objects": objects, "dependencies": deps}


def execute(request_path):
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    jobs = request["jobs"]
    try:
        native, raw, timing = extract_native(request["runtime"], request["source"], jobs,
                                              request_path.parent)
    except Exception as exc:
        detail = {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()}
        for job in jobs:
            write_json(Path(job["directory"]) / "result.json", {"page": job["page"], **detail})
        return 1
    native_forms = forms.extract(request["source"], [job["page"] for job in jobs])
    layout = None
    failed = False
    for job in jobs:
        try:
            if layout is None:
                from source_figures_model import LayoutModel
                rt = request["runtime"]
                layout = LayoutModel(rt["layout_model"], rt["use_cuda"], rt["cuda_dll_dir"])
            result = process_page(job, request, native, raw, timing, layout, native_forms[job["page"]])
        except Exception as exc:
            failed = True
            result = {"page": job["page"], "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                      "traceback": traceback.format_exc()}
        write_json(Path(job["directory"]) / "result.json", result)
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--versions":
        print(json.dumps(versions(), ensure_ascii=False))
    elif len(sys.argv) == 2:
        raise SystemExit(execute(sys.argv[1]))
    else:
        raise SystemExit("expected --versions or a JSON request path")
