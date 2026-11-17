from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tools import source_figures_caption as caption
from tools import source_figures_logic as logic
from tools import source_figures_panel as panel


ROOT = Path(__file__).resolve().parents[1]


def line(name, text, box):
    return {"id": name, "text": text, "normalized": caption.norm(text), "error": None,
            "glyphs": [{"text": text, "bbox_pt": box,
                        "quad_pt": [box[0], box[1], box[2], box[1],
                                    box[0], box[3], box[2], box[3]]}]}


def aligned(box, region=None):
    predictor = {} if region is None else {"regionBoundary": dict(zip(("x1", "y1", "x2", "y2"), region))}
    return {"status": "ok", "origin": "figures" if region else "regionless-captions",
            "predictor": predictor, "source_snap": {"bbox_px": box, "line_ids": []}}


class GeometryTests(unittest.TestCase):
    def test_native_image_placement_is_complete_even_on_a_coloured_background(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "trace.xml"
            path.write_text('<document><page><fill_image width="400" height="250" '
                            'transform="80 0 0 50 10 20"/></page></document>', encoding="utf-8")
            images = logic.native_images(path, 1, 200, 200)
        self.assertEqual(images[0]["bbox_px"], [10, 20, 90, 70])
        self.assertEqual(logic.select_native_images([12, 22, 85, 65], images), images)
        crop = [10, 20, 90, 70]
        self.assertEqual(logic.rectangle_loss_pixels(images[0]["bbox_px"], crop), 0)
        self.assertEqual(logic.rectangle_loss_pixels(images[0]["bbox_px"], [11, 20, 90, 70]), 50)

    def test_native_clip_must_contain_full_placement_or_require_review(self):
        def rect(x0, y0, x1, y1):
            return (f'<clip_path transform="1 0 0 1 0 0"><moveto x="{x0}" y="{y0}"/>'
                    f'<lineto x="{x1}" y="{y0}"/><lineto x="{x1}" y="{y1}"/>'
                    f'<lineto x="{x0}" y="{y1}"/><closepath/></clip_path>')
        image = '<fill_image width="20" height="20" transform="20 0 0 20 10 10"/>'
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "trace.xml"
            def parse(clip):
                path.write_text(f'<document><page>{clip}{image}{"<pop_clip/>" if clip else ""}'
                                '</page></document>', encoding="utf-8")
                return logic.native_images(path, 1, 100, 100)[0]
            self.assertEqual(parse("")["clip_status"], "none")
            self.assertEqual(parse(rect(0, 0, 100, 100))["clip_status"], "fully_contained")
            self.assertEqual(parse(rect(10, 10, 20, 30))["clip_status"], "unresolved")
            self.assertEqual(parse('<clip_path transform="1 0 0 1 0 0"><curveto x="1" y="2"/>'
                                   '</clip_path>')["clip_status"], "unresolved")

    def test_four_real_target_pixels_are_not_tolerance_or_caption(self):
        self.assertEqual(logic.rectangle_loss_pixels([5, 5, 6, 9], [0, 0, 5, 9]), 4)
        self.assertEqual(logic.rectangle_loss_pixels([5, 5, 6, 9], [0, 0, 6, 9]), 0)
        blocks = [[line("legend", "key", [5, 1, 6, 4]), line("caption", "Fig 1", [5, 5, 6, 9])]]
        cap = aligned([5, 5, 6, 9])
        cap["source_snap"]["line_ids"] = ["caption"]
        found = logic.independent_glyph_lines(blocks, [0, 0, 10, 10], [cap], 1)
        self.assertEqual([v["line_id"] for v in found], ["legend"])

    def test_white_panel_requires_rectangle_and_body_overlap(self):
        root = ET.fromstring('<svg viewBox="0 0 100 100"><rect x="10" y="10" width="70" '
                             'height="70" fill="white"/><path d="M1 1 L4 1 L4 4 Z" '
                             'fill="white"/></svg>')
        records, _, _ = panel.svg_candidates(root, 10000)
        for index, record in enumerate(records):
            record["panel_id"] = str(index)
            record["bbox_xyxy_px_continuous"] = record["bbox_display_pt"]
        chosen, _ = panel.choose(records, [20, 20, 60, 60])
        self.assertEqual(chosen["bbox_display_pt"], [10.0, 10.0, 80.0, 80.0])

    def test_caption_barrier_does_not_silently_choose_conflicting_sides(self):
        below = aligned([20, 80, 50, 90])
        below["source_snap"]["line_ids"] = ["below"]
        right = aligned([80, 20, 90, 50])
        right["source_snap"]["line_ids"] = ["right"]
        box, _, reason = caption.barrier([0, 0, 100, 100], [0, 0, 100, 100],
                                        [20, 20, 60, 60], [below], 100, 100)
        self.assertEqual(box, [0, 0, 100, 79])
        self.assertIsNone(reason)
        box, _, reason = caption.barrier([0, 0, 100, 100], [0, 0, 100, 100],
                                        [20, 20, 60, 60], [below, right], 100, 100)
        self.assertIsNone(box)
        self.assertIn("inconsistent", reason)

    def test_caption_exact_text_and_source_line_identity(self):
        item = {"name": "1", "figType": "Figure"}
        entry = ("figures", item, "Figure 1. A diagram", {"x1": 10, "y1": 20, "x2": 80, "y2": 25})
        source = [[line("block-0:line-0", "Figure 1. A diagram", [10, 20, 80, 25])]]
        match = caption.align_caption(entry, source)
        self.assertEqual(match["status"], "ok")
        self.assertEqual(match["source_snap"]["text"], "Figure 1. A diagram")
        self.assertEqual(match["source_snap"]["line_ids"], ["block-0:line-0"])
        wrong = caption.align_caption(("figures", item, "Figure 2. A diagram", entry[3]), source)
        self.assertEqual(wrong["status"], "unavailable")
        duplicate = caption.align_caption(entry, source + [[line("other", "Figure 1. A diagram", [10, 20, 80, 25])]])
        self.assertEqual(duplicate["status"], "ambiguous")

    def test_multiline_caption_keeps_word_boundary_and_original_lines(self):
        source = [[line("a", "Figure 1. Events Are", [10, 20, 80, 25]),
                   line("b", "Simultaneous with Which", [10, 25, 100, 30])]]
        entry = ("figures", {"name": "1", "figType": "Figure"},
                 "Figure 1. Events Are Simultaneous with Which",
                 {"x1": 10, "y1": 20, "x2": 100, "y2": 30})
        match = caption.align_caption(entry, source)
        self.assertEqual(match["status"], "ok")
        self.assertEqual(match["source_snap"]["lines"], ["Figure 1. Events Are", "Simultaneous with Which"])
        self.assertEqual(match["source_snap"]["text"], "Figure 1. Events Are\nSimultaneous with Which")

    def test_caption_number_keeps_hierarchical_dot_and_allows_fig_abbreviation(self):
        boundary = {"x1": 10, "y1": 20, "x2": 90, "y2": 25}
        entry = ("figures", {"name": "1.1", "figType": "Figure"},
                 "Figure 1.1. A diagram", boundary)
        exact = [[line("a", "Fig. 1.1. A diagram", [10, 20, 90, 25])]]
        wrong = [[line("a", "Figure 11. A diagram", [10, 20, 90, 25])]]
        self.assertEqual(caption.align_caption(entry, exact)["status"], "ok")
        self.assertEqual(caption.align_caption(entry, wrong)["status"], "unavailable")
        hyphen = ("figures", {"name": "1-1", "figType": "Figure"},
                  "Figure 1-1. A diagram", boundary)
        self.assertEqual(caption.align_caption(hyphen, exact)["status"], "unavailable")

    def test_neighbor_caption_is_recorded_as_intrusion_not_target_pixels(self):
        target = [5, 5, 6, 9]
        own, neighbor = aligned([20, 20, 25, 25]), aligned([6, 5, 7, 9])
        own["source_snap"]["line_ids"] = ["own-caption"]
        neighbor["source_snap"]["line_ids"] = ["neighbor-caption"]
        crop = [5, 5, 7, 9]
        self.assertEqual(logic.rectangle_loss_pixels(target, crop), 0)
        self.assertEqual(logic.overlapping_caption_bounds(crop, [own, neighbor]), [
            {"caption_index": 1, "line_ids": ["neighbor-caption"], "bbox_px": [6, 5, 7, 9]}])
        self.assertEqual(logic.rectangle_loss_pixels(target, [6, 5, 7, 9]), 4)

    def test_text_outside_seed_but_inside_crop_is_a_separate_review_reason(self):
        blocks = [[line("legend", "key", [1, 1, 3, 3]),
                   line("neighbor", "paragraph", [8, 1, 10, 3])]]
        selected = logic.independent_glyph_lines(blocks, [0, 0, 4, 4], [], 1)
        extra = logic.unassigned_text_bounds([0, 0, 11, 4], blocks, [], selected, 1)
        self.assertEqual([x["line_id"] for x in extra], ["neighbor"])

    def test_uncovered_source_region_legend_stays_unresolved_outside_model_seed(self):
        blocks = [[line("legend", "key", [6, 2, 8, 4])]]
        cap = aligned([1, 12, 8, 14], [1, 1, 9, 9])
        cap["source_snap"]["line_ids"] = ["caption"]
        small_crop = [1, 1, 5, 5]  # B covers the image, not the independent legend.
        missing = logic.uncovered_region_glyphs(small_crop, cap, blocks, [cap], 1)
        self.assertEqual([v["line_id"] for v in missing], ["legend"])
        self.assertEqual(logic.uncovered_region_glyphs([1, 1, 9, 9], cap, blocks, [cap], 1), [])

    def test_native_form_glyph_metric_overhang_requires_proven_complete_clipped_viewport(self):
        glyph = {"id": "axis:glyph-0", "text": "0", "bbox_pt": [9, 2, 19, 4]}
        blocks = [[{"id": "axis", "text": "0", "error": None, "glyphs": [glyph]}]]
        cap = aligned([1, 12, 8, 14], [0, 0, 20, 10])
        crop = [0, 0, 10, 10]
        form = {"id": "form", "ancestor_ids": [], "eligible": True, "axis_aligned": True,
                "clip_status": "fully_contained", "form_bbox": [0, 0, 10, 10],
                "display_transform": [1, 0, 0, 1, 0, 0], "bbox_px": crop,
                "continuous_bbox_px": [0, 0, 10, 10],
                "text_glyph_origins": [{"text": "0", "origin_display_pt": [9, 4],
                                        "font_height_pt": 4}]}
        missed = logic.uncovered_region_glyphs(crop, cap, blocks, [cap], 1)
        self.assertEqual(missed[0]["glyphs"][0]["glyph_id"], "axis:glyph-0")
        proven, unproven = logic.form_glyph_ownership(form, [form], blocks, [cap], 1)
        self.assertEqual(unproven, [])
        remaining, information = logic.split_form_metric_overhangs(missed, form, [form], crop, proven)
        self.assertEqual(remaining, [])
        self.assertEqual(information[0]["bbox_px"], [9, 2, 19, 4])

        foreign_glyph = {"id": "axis:glyph-1", "text": "X", "bbox_pt": [9, 5, 15, 7]}
        mixed = [[{"id": "axis", "text": "0X", "error": None, "glyphs": [glyph, foreign_glyph]}]]
        mixed_missed = logic.uncovered_region_glyphs(crop, cap, mixed, [cap], 1)
        mixed_proven, _ = logic.form_glyph_ownership(form, [form], mixed, [cap], 1)
        remaining, information = logic.split_form_metric_overhangs(mixed_missed, form, [form], crop, mixed_proven)
        self.assertEqual([g["glyph_id"] for g in remaining[0]["glyphs"]], ["axis:glyph-1"])
        self.assertEqual([g["glyph_id"] for g in information[0]["glyphs"]], ["axis:glyph-0"])

        # Neither a foreign source glyph nor an ambiguous clip can use the
        # Form's BBox clipping as evidence of preserved visible pixels.
        self.assertEqual(logic.split_form_metric_overhangs(missed, form, [form], crop, [])[0], missed)
        other = [{**proven[0], "form_id": "other-form"}]
        self.assertEqual(logic.split_form_metric_overhangs(missed, form, [form], crop, other)[0], missed)
        unknown_clip = {**form, "clip_status": "unknown"}
        self.assertEqual(logic.split_form_metric_overhangs(missed, unknown_clip, [unknown_clip], crop, proven)[0], missed)
        self.assertEqual(logic.split_form_metric_overhangs(missed, form, [form], [0, 0, 9, 10], proven)[0], missed)
        no_native_bbox = {**form, "form_bbox": None}
        self.assertEqual(logic.split_form_metric_overhangs(missed, no_native_bbox, [no_native_bbox], crop, proven)[0], missed)

    def test_model_overlap_does_not_itself_establish_legend_ownership(self):
        glyphs = [{"line_id": "legend", "bbox_px": [6, 2, 8, 4]}]
        self.assertEqual(logic.unproven_glyph_ownership(glyphs, []), glyphs)
        self.assertEqual(logic.unproven_glyph_ownership(glyphs, [{"bbox_px": [1, 1, 9, 9]}]), [])

    def test_unique_caption_pairing_and_crossed_or_merged_candidates(self):
        bodies = [{"B": [0, 0, 10, 10]}, {"B": [20, 0, 30, 10]}]
        caps = [aligned([0, 12, 10, 15], [0, 0, 10, 10]),
                aligned([20, 12, 30, 15], [20, 0, 30, 10])]
        pairs, reasons = logic.pair_captions(bodies, caps, 1)
        self.assertEqual(pairs, {0: 0, 1: 1})
        self.assertEqual(reasons, [])
        pairs, reasons = logic.pair_captions([{"B": [0, 0, 30, 10]}], caps, 1)
        self.assertEqual(pairs, {})
        self.assertIn("caption_body_ownership_not_unique", reasons)

    def test_a_geometric_split_does_not_prove_caption_pairing(self):
        bodies = [{"B": [0, 0, 10, 10]}, {"B": [0, 20, 10, 30]}]
        caps = [aligned([11, 0, 20, 10]), aligned([11, 20, 20, 30])]
        pairs, reasons = logic.pair_captions(bodies, caps, 1)
        self.assertEqual(pairs, {})
        self.assertTrue(reasons)
        self.assertEqual(logic.pair_captions([], [], 1)[1], ["missing_body_or_caption"])
        pair, reasons = logic.pair_captions([{"B": [0, 0, 10, 10]}],
                                            [aligned([200, 200, 210, 210])], 1)
        self.assertEqual(pair, {})
        self.assertIn("caption_body_ownership_not_unique", reasons)


if __name__ == "__main__":
    unittest.main()
