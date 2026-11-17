from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import source_figures_logic as logic
from tools import source_figures_worker as worker

ROOT = Path(__file__).resolve().parents[1]


def form(name, box, *, parent=None, eligible=True, clip="fully_contained"):
    return {"id": name, "parent_id": parent, "ancestor_ids": [parent] if parent else [],
            "bbox_display_pt": box, "continuous_bbox_px": box, "bbox_px": box,
            "form_bbox": box, "display_transform": [1, 0, 0, 1, 0, 0],
            "eligible": eligible, "axis_aligned": True, "page_sized": False,
            "clip_status": clip, "page_fraction": 0.1}


def cap(region, caption_box):
    return {"status": "ok", "origin": "figures",
            "prefix_label_separator": True,
            "predictor": {"regionBoundary": dict(zip(("x1", "y1", "x2", "y2"), region))},
            "source_snap": {"bbox_px": caption_box, "line_ids": ["caption"]}}


class GroupMatchingTests(unittest.TestCase):
    def test_one_native_invocation_groups_only_its_own_rows(self):
        bodies = [{"candidate_id": "a", "B": [2, 2, 7, 7]},
                  {"candidate_id": "b", "B": [12, 2, 18, 7]},
                  {"candidate_id": "unrelated", "B": [70, 70, 80, 80]}]
        groups = logic.native_form_groups(bodies, [cap([0, 0, 21, 11], [1, 12, 20, 14])],
                                          [form("figure", [0, 0, 21, 11])], 1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["seed_indices"], [0, 1])
        self.assertEqual(groups[0]["caption_index"], 0)

    def test_two_independent_forms_and_captions_cannot_cross_merge(self):
        bodies = [{"B": [1, 1, 9, 9]}, {"B": [21, 1, 29, 9]}]
        f1, f2 = form("left", [0, 0, 10, 10]), form("right", [20, 0, 30, 10])
        c1, c2 = cap([0, 0, 10, 10], [0, 12, 10, 14]), cap([20, 0, 30, 10], [20, 12, 30, 14])
        groups = logic.native_form_groups(bodies, [c1, c2], [f1, f2], 1)
        self.assertEqual([(g["form"]["id"], g["seed_indices"], g["caption_index"]) for g in groups],
                         [("left", [0], 0), ("right", [1], 1)])
        same_region = cap([0, 0, 30, 10], [0, 12, 30, 14])
        self.assertEqual(logic.native_form_groups(bodies, [same_region], [f1, f2], 1), [])

    def test_nested_forms_are_not_independent_caption_owners(self):
        parent = form("parent", [0, 0, 40, 20])
        child = form("child", [0, 0, 20, 20], parent="parent")
        bodies = [{"B": [1, 1, 18, 18]}, {"B": [21, 1, 38, 18]}]
        groups = logic.native_form_groups(bodies, [cap([0, 0, 40, 20], [0, 22, 40, 24])],
                                          [parent, child], 1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["form"]["id"], "parent")
        self.assertEqual(groups[0]["seed_indices"], [0, 1])

    def test_unresolved_form_clip_and_multiple_matching_captions_do_not_group(self):
        body = [{"B": [2, 2, 8, 8]}]
        cap1 = cap([0, 0, 10, 10], [0, 12, 10, 14])
        self.assertEqual(logic.native_form_groups(body, [cap1],
                         [form("unsafe", [0, 0, 10, 10], eligible=False, clip="unknown")], 1), [])
        self.assertEqual(logic.native_form_groups(body, [cap1, cap1],
                         [form("ambiguous", [0, 0, 10, 10])], 1), [])
        clipped_parent = form("clipped", [0, 0, 10, 10], eligible=False, clip="partial")
        child = form("child", [0, 0, 10, 10], parent="clipped")
        self.assertEqual(logic.native_form_groups(body, [cap1], [clipped_parent, child], 1), [])

    def test_small_caption_region_cannot_claim_a_wide_multifigure_container(self):
        bodies = [{"B": [1, 1, 20, 20]}, {"B": [70, 1, 90, 20]}]
        group = form("wide", [0, 0, 100, 30])
        left = cap([0, 0, 30, 25], [0, 35, 30, 40])
        self.assertEqual(logic.native_form_groups(bodies, [left], [group], 1), [])
        # Even if the model missed the right-hand vector, the Form itself must
        # remain consistent with the region before it can be claimed.
        self.assertEqual(logic.native_form_groups(bodies[:1], [left], [group], 1), [])

    def test_each_model_seed_must_be_consistent_with_the_unique_region(self):
        group = form("figure", [0, 0, 100, 30])
        region = cap([0, 0, 85, 30], [0, 35, 85, 40])
        inside = {"B": [1, 1, 20, 20]}
        outside = {"B": [78, 1, 98, 20]}
        self.assertEqual(logic.native_form_groups([inside, outside], [region], [group], 1), [])
        self.assertEqual(logic.native_form_groups([inside], [region], [group], 1)[0]["seed_indices"], [0])


@unittest.skipUnless(importlib.util.find_spec("PIL"), "configured image runtime required")
class WorkerGroupTests(unittest.TestCase):
    def run_page(self, bodies, caps, native_forms, blocks=None, image_xml="", reuse=False, pixels=None):
        from PIL import Image
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            directory = Path(temp)
            png, svg, text, trace, raw, timing = [directory / n for n in
                ("source.png", "source.svg", "source.stext.xml", "source.trace.xml", "native.json", "timing.json")]
            image = Image.new("RGB", (100, 100), "white")
            if pixels is None:
                pixels = [(int((b["B"][0] + b["B"][2]) / 2),
                           int((b["B"][1] + b["B"][3]) / 2), (0, 0, 0)) for b in bodies]
            for x, y, colour in pixels:
                image.putpixel((x, y), colour)
            image.save(png)
            for path in (svg, text, raw, timing):
                path.write_text("{}", encoding="utf8")
            trace.write_text("<document><page>" + image_xml + "</page></document>", encoding="utf8")
            paths = {"png": png, "svg": svg, "stext": text, "trace": trace}
            tensor = directory / "raw.npz"
            tensor.write_bytes(b"raw")
            prediction = {"width_px": 100, "height_px": 100, "raw_tensor_npz": str(tensor),
                          "body_candidates": bodies}
            layout = mock.Mock()
            layout.predict.return_value = prediction
            md = {"dpi": 72, "mutool_display_pt_to_pixel_affine": [1, 0, 0, 1, 0, 0]}
            job = {"directory": str(directory), "page": 1, "reuse_raw": None}
            if reuse:
                job["reuse_source_files"] = {key: str(value) for key, value in paths.items()}
                job["reuse_rotation_deg"] = 0
            request = {"source": "unused.pdf", "runtime": {"mutool": "unused"}, "dpi": 72}
            with (mock.patch.object(worker.rendering, "render", return_value=paths) as render,
                  mock.patch.object(worker, "pdf_rotation", return_value=0) as rotation,
                  mock.patch.object(worker, "_page_inputs", return_value=(md, None, {"area_pt2": 10000})),
                  mock.patch.object(worker, "_captions", return_value=(blocks or [], caps, None, 0)),
                  mock.patch.object(worker.panel, "svg_candidates", return_value=([], [], {}))):
                result = worker.process_page(job, request, {}, raw, timing, layout, native_forms)
            if reuse:
                render.assert_not_called()
                rotation.assert_not_called()
            # Copy before TemporaryDirectory removes output images.
            return result

    def test_no_candidates_is_information_not_established_negative(self):
        native = {"available": True, "status": "ok", "reasons": [], "placements": []}
        result = self.run_page([], [], native)
        self.assertEqual(result["status"], "candidates")
        self.assertTrue(result["no_candidates"])
        self.assertEqual(result["coverage"], "not_established")
        self.assertEqual(result["information"], ["no_candidates"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["review_required"])
        self.assertEqual(result["reasons"], [])

    def test_missing_optional_capability_preserves_no_candidate_contract(self):
        native = {"available": False, "status": "unavailable", "reasons": ["pypdf_unavailable"],
                  "placements": []}
        result = self.run_page([], [], native, reuse=True)
        self.assertEqual(result["status"], "candidates")
        self.assertTrue(result["source_artifacts_reused"])
        self.assertEqual(result["native_forms"]["status"], "unavailable")
        self.assertEqual(result["coverage"], "not_established")

    def test_white_form_is_not_promoted_to_a_semantic_group(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        native = {"available": True, "status": "ok", "reasons": [],
                  "placements": [form("figure", [0, 0, 10, 10])]}
        result = self.run_page(body, [cap([0, 0, 10, 10], [0, 12, 10, 14])], native, pixels=[])
        self.assertIsNone(result["objects"][0]["native_form"])
        self.assertIn("native_form_or_seed_has_no_visible_ink", result["reasons"])

    def test_unmatched_scanned_page_and_unknown_parser_require_review(self):
        native = {"available": True, "status": "ok", "reasons": [], "placements": []}
        scanned = '<fill_image width="100" height="100" transform="100 0 0 100 0 0"/>'
        result = self.run_page([], [], native, image_xml=scanned)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("page_sized_native_image_requires_review", result["reasons"])
        bad = {"available": True, "status": "unsupported", "reasons": ["bad_clip"], "placements": []}
        result = self.run_page([], [], bad)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("native_form_extraction_unsupported", result["reasons"])

    def test_full_form_viewport_groups_raw_rows_and_rejects_foreign_glyph(self):
        bodies = [{"candidate_id": "a", "B": [2, 2, 7, 7], "Bcc": [2, 2, 7, 7]},
                  {"candidate_id": "b", "B": [12, 2, 18, 7], "Bcc": [12, 2, 18, 7]}]
        native = {"available": True, "status": "ok", "reasons": [],
                  "placements": [form("figure", [0, 0, 21, 11])]}
        foreign = [[{"id": "foreign", "text": "body", "error": None,
                     "glyphs": [{"text": "body", "bbox_pt": [8, 2, 10, 4]}]}]]
        result = self.run_page(bodies, [cap([0, 0, 21, 11], [0, 20, 21, 23])], native, foreign)
        self.assertEqual(result["grouped_seed_count"], 2)
        self.assertEqual(len(result["objects"]), 1)
        obj = result["objects"][0]
        self.assertEqual(obj["bbox_xyxy_px"], [0, 0, 21, 11])
        self.assertEqual([s["candidate_id"] for s in obj["model_seeds"]], ["a", "b"])
        self.assertEqual(obj["model_seed"], bodies[0])
        self.assertIn("native_glyph_ownership_unproven", obj["reasons"])
        self.assertTrue(obj["review_required"])

    def test_clipped_owned_form_glyph_overhang_is_information_not_content_loss(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        native_form = form("figure", [0, 0, 10, 10])
        native_form["text_glyph_origins"] = [{"text": "0", "origin_display_pt": [9, 4],
                                               "font_height_pt": 4}]
        glyph = {"id": "axis:glyph-0", "text": "0", "bbox_pt": [9, 2, 11, 4]}
        blocks = [[{"id": "axis", "text": "0", "error": None, "glyphs": [glyph]}]]
        native = {"available": True, "status": "ok", "reasons": [], "placements": [native_form]}
        item = cap([0, 0, 11, 10], [0, 12, 11, 14])
        result = self.run_page(body, [item], native, blocks, pixels=[(5, 5, (0, 0, 0)), (9, 3, (0, 0, 0))])
        obj = result["objects"][0]
        self.assertEqual(obj["bbox_xyxy_px"], [0, 0, 10, 10])
        self.assertEqual(obj["uncovered_native_region_glyphs"], [])
        self.assertEqual(obj["native_form_metric_overhangs"][0]["glyphs"][0]["glyph_id"], "axis:glyph-0")
        self.assertNotIn("native_region_contains_uncovered_glyphs", obj["reasons"])
        self.assertEqual(result["status"], "candidates")

        unowned = {**native_form, "text_glyph_origins": []}
        result = self.run_page(body, [item], {**native, "placements": [unowned]}, blocks)
        obj = result["objects"][0]
        self.assertIn("native_region_contains_uncovered_glyphs", obj["reasons"])
        self.assertEqual(obj["native_form_metric_overhangs"], [])

        parser_unknown = {**native, "status": "unsupported", "reasons": ["content_stream_unsupported"]}
        result = self.run_page(body, [item], parser_unknown, blocks)
        obj = result["objects"][0]
        self.assertIn("native_region_contains_uncovered_glyphs", obj["reasons"])
        self.assertEqual(obj["native_form_metric_overhangs"], [])

        unknown_clip = {**native_form, "clip_status": "unknown", "eligible": False}
        result = self.run_page(body, [item], {**native, "placements": [unknown_clip]}, blocks)
        obj = result["objects"][0]
        self.assertIn("native_region_contains_uncovered_glyphs", obj["reasons"])
        self.assertEqual(obj["native_form_metric_overhangs"], [])

    def test_internal_clip_is_preserved_by_complete_parent_viewport(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        parent = form("parent", [0, 0, 10, 10])
        child = form("child", [0, 0, 10, 10], parent="parent", eligible=False, clip="unknown")
        native = {"available": True, "status": "ok", "reasons": [], "placements": [parent, child]}
        result = self.run_page(body, [cap([0, 0, 10, 10], [0, 12, 10, 14])], native)
        self.assertEqual(result["objects"][0]["bbox_xyxy_px"], [0, 0, 10, 10])
        self.assertEqual(result["objects"][0]["reasons"], [])

    def test_legacy_unseparated_caption_remains_candidate_without_form(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        item = cap([0, 0, 10, 10], [0, 12, 10, 14])
        item["prefix_label_separator"] = False
        native = {"available": False, "status": "unavailable", "reasons": ["pypdf_unavailable"],
                  "placements": []}
        result = self.run_page(body, [item], native)
        self.assertNotIn("caption_label_separator_unproven", result["objects"][0]["reasons"])
        self.assertEqual(result["status"], "candidates")

    def test_unseparated_caption_cannot_authorize_new_native_form_group(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        item = cap([0, 0, 10, 10], [0, 12, 10, 14])
        item["prefix_label_separator"] = False
        native = {"available": True, "status": "ok", "reasons": [],
                  "placements": [form("figure", [0, 0, 10, 10])]}
        result = self.run_page(body, [item], native)
        self.assertIsNone(result["objects"][0]["native_form"])
        self.assertIn("native_form_without_resolved_group", result["reasons"])

    def test_omitted_vector_ink_is_not_swallowed_by_caption_region(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        native = {"available": True, "status": "ok", "reasons": [],
                  "placements": [form("figure", [0, 0, 100, 30])]}
        item = cap([0, 0, 85, 30], [0, 35, 85, 40])
        # The model found A; B is an independently drawn vector outside the PF2 region.
        pixels = [(5, 5, (0, 0, 0)), (90, 10, (0, 0, 0))]
        result = self.run_page(body, [item], native, pixels=pixels)
        self.assertEqual(len(result["objects"]), 1)
        self.assertIsNone(result["objects"][0]["native_form"])
        self.assertIn("native_form_region_contains_unassociated_visible_ink", result["reasons"])
        self.assertEqual(result["native_form_region_checks"][0]["unassociated_visible_pixels"], 1)

    def test_connected_antialias_outside_region_is_not_a_new_figure(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        native = {"available": True, "status": "ok", "reasons": [],
                  "placements": [form("figure", [0, 0, 100, 30])]}
        item = cap([0, 0, 85, 30], [0, 35, 85, 40])
        pixels = [(5, 5, (0, 0, 0))] + [(x, 10, (255, 255, 254)) for x in range(80, 90)]
        result = self.run_page(body, [item], native, pixels=pixels)
        self.assertEqual(result["objects"][0]["bbox_xyxy_px"], [0, 0, 100, 30])
        self.assertEqual(result["native_form_region_checks"][0]["unassociated_component_count"], 0)
        self.assertEqual(result["status"], "candidates")

    def test_proven_image_clipped_by_form_is_not_foreign_placement(self):
        body = [{"candidate_id": "a", "B": [2, 2, 8, 8], "Bcc": [2, 2, 8, 8]}]
        native = {"available": True, "status": "ok", "reasons": [],
                  "placements": [form("figure", [0, 0, 10, 10])],
                  "image_invocations": [{"id": "pdf-image-0", "ancestor_ids": ["figure"],
                     "bbox_display_pt": [-5, 0, 15, 10], "axis_aligned": True}]}
        clip = ('<clip_path transform="1 0 0 1 0 0"><moveto x="0" y="0"/>'
                '<lineto x="10" y="0"/><lineto x="10" y="10"/>'
                '<lineto x="0" y="10"/><closepath/></clip_path>')
        trace = clip + '<fill_image width="20" height="10" transform="20 0 0 10 -5 0"/><pop_clip/>'
        result = self.run_page(body, [cap([0, 0, 10, 10], [0, 12, 10, 14])], native, image_xml=trace)
        obj = result["objects"][0]
        self.assertEqual(obj["bbox_xyxy_px"], [0, 0, 10, 10])
        self.assertEqual(obj["native_placement_lost_pixels"], [50])
        self.assertEqual(obj["native_placement_loss_meaning"], "rectangle_area_not_visible_pixel_loss")
        self.assertEqual(obj["reasons"], [])


if __name__ == "__main__":
    unittest.main()
