from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import source_figures_ink as ink
from tools import source_figures_worker as worker


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("PIL"), "configured image runtime required")
class SourcePixelsTests(unittest.TestCase):
    def test_original_coloured_placement_and_separate_legend_survive_crop(self):
        from PIL import Image, ImageChops

        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            directory = Path(temp)
            png = directory / "source.png"
            trace = directory / "source.trace.xml"
            svg, text = directory / "source.svg", directory / "source.stext.xml"
            raw, timing = directory / "native.json", directory / "timing.json"
            for path in (svg, text, raw, timing):
                path.write_text("{}", encoding="utf-8")
            trace.write_text('<document><page><fill_image width="4" height="4" '
                             'transform="4 0 0 4 1 1"/></page></document>', encoding="utf-8")
            source = Image.new("RGB", (16, 16), "white")
            for y in range(1, 5):
                for x in range(1, 5):
                    source.putpixel((x, y), (14, 91, 147))
            source.putpixel((6, 2), (5, 5, 5))
            source.save(png)
            body = {"candidate_id": "raw-0", "B": [1, 1, 8, 8], "Bcc": [2, 2, 7, 7]}

            def predict(*args, **kwargs):
                tensor = directory / "raw.npz"
                tensor.write_bytes(b"synthetic tensor")
                return {"width_px": 16, "height_px": 16, "raw_tensor_npz": str(tensor),
                        "body_candidates": [body]}

            layout = mock.Mock()
            layout.predict.side_effect = predict
            blocks = [[{"id": "legend", "text": "Key", "error": None,
                        "glyphs": [{"text": "Key", "bbox_pt": [6, 2, 8, 4]}]}]]
            cap = {"status": "ok", "origin": "figures",
                   "predictor": {"regionBoundary": {"x1": 1, "y1": 1, "x2": 8, "y2": 8}},
                   "source_snap": {"bbox_px": [1, 10, 8, 11], "line_ids": ["caption"]}}
            paths = {"png": png, "svg": svg, "stext": text, "trace": trace}
            md = {"dpi": 72, "mutool_display_pt_to_pixel_affine": [1, 0, 0, 1, 0, 0]}
            geometry = {"area_pt2": 256}
            request = {"source": "unused.pdf", "runtime": {"mutool": "unused"}, "dpi": 72}
            job = {"directory": str(directory), "page": 1, "reuse_raw": None}
            with (mock.patch.object(worker.rendering, "render", return_value=paths),
                  mock.patch.object(worker, "pdf_rotation", return_value=0),
                  mock.patch.object(worker, "_page_inputs", return_value=(md, None, geometry)),
                  mock.patch.object(worker, "_captions", return_value=(blocks, [cap], None, 0)),
                  mock.patch.object(worker.panel, "svg_candidates", return_value=([], [], {}))):
                result = worker.process_page(job, request, {}, raw, timing, layout)
            self.assertEqual(result["status"], "needs_review")
            obj = result["objects"][0]
            self.assertEqual(obj["bbox_xyxy_px"], [1, 1, 8, 7])
            self.assertEqual(obj["native_placement_lost_pixels"], [0])
            self.assertEqual(obj["independent_glyph_lines"][0]["line_id"], "legend")
            self.assertIn("native_glyph_ownership_unproven", obj["reasons"])
            with Image.open(obj["image"]) as exported:
                self.assertIsNone(ImageChops.difference(exported, source.crop((1, 1, 8, 7))).getbbox())
                self.assertEqual(exported.getpixel((0, 0)), (14, 91, 147))
                self.assertEqual(exported.getpixel((5, 1)), (5, 5, 5))

    def test_small_pale_native_placement_is_preserved_without_unrelated_icon(self):
        from PIL import Image, ImageChops

        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            directory = Path(temp)
            png = directory / "source.png"
            trace = directory / "source.trace.xml"
            svg, text = directory / "source.svg", directory / "source.stext.xml"
            raw, timing = directory / "native.json", directory / "timing.json"
            for path in (svg, text, raw, timing):
                path.write_text("{}", encoding="utf-8")
            trace.write_text('<document><page>'
                             '<fill_image width="60" height="60" transform="60 0 0 60 50 50"/>'
                             '<fill_image width="10" height="10" transform="10 0 0 10 400 400"/>'
                             '</page></document>', encoding="utf-8")
            source = Image.new("RGB", (1000, 1000), "white")
            source.paste((246, 247, 249), (50, 50, 110, 110))
            source.putpixel((80, 80), (0, 0, 0))
            source.paste((80, 10, 110), (400, 400, 410, 410))
            source.save(png)
            body = {"candidate_id": "raw-0", "B": [75, 75, 85, 85], "Bcc": [80, 80, 81, 81]}

            def predict(*args, **kwargs):
                tensor = directory / "raw.npz"
                tensor.write_bytes(b"synthetic tensor")
                return {"width_px": 1000, "height_px": 1000, "raw_tensor_npz": str(tensor),
                        "body_candidates": [body]}

            layout = mock.Mock()
            layout.predict.side_effect = predict
            cap = {"status": "ok", "origin": "figures",
                   "predictor": {"regionBoundary": {"x1": 50, "y1": 50, "x2": 110, "y2": 110}},
                   "source_snap": {"bbox_px": [50, 120, 110, 130], "line_ids": ["caption"]}}
            paths = {"png": png, "svg": svg, "stext": text, "trace": trace}
            md = {"dpi": 72, "mutool_display_pt_to_pixel_affine": [1, 0, 0, 1, 0, 0]}
            geometry = {"area_pt2": 1000000}
            request = {"source": "unused.pdf", "runtime": {"mutool": "unused"}, "dpi": 72}
            job = {"directory": str(directory), "page": 1, "reuse_raw": None}
            with (mock.patch.object(worker.rendering, "render", return_value=paths),
                  mock.patch.object(worker, "pdf_rotation", return_value=0),
                  mock.patch.object(worker, "_page_inputs", return_value=(md, None, geometry)),
                  mock.patch.object(worker, "_captions", return_value=([], [cap], None, 0)),
                  mock.patch.object(worker.panel, "svg_candidates", return_value=([], [], {}))):
                result = worker.process_page(job, request, {}, raw, timing, layout)
            obj = result["objects"][0]
            self.assertEqual(obj["bbox_xyxy_px"], [50, 50, 110, 110])
            self.assertEqual(len(obj["native_image_placements"]), 1)
            self.assertEqual(obj["native_image_placements"][0]["page_fraction"], 0.0036)
            self.assertEqual(obj["native_placement_lost_pixels"], [0])
            with Image.open(obj["image"]) as exported:
                self.assertIsNone(ImageChops.difference(exported, source.crop((50, 50, 110, 110))).getbbox())
                self.assertEqual(exported.getpixel((0, 0)), (246, 247, 249))
                self.assertEqual(exported.getpixel((30, 30)), (0, 0, 0))

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "configured CV runtime required")
    def test_caption_barrier_rejects_four_connected_pixels_but_not_detached_caption(self):
        from PIL import Image

        page = Image.new("RGB", (20, 20), "white")
        for y in range(4, 13):
            page.putpixel((3, y), (0, 0, 0))
        for y in range(12, 16):
            page.putpixel((12, y), (0, 0, 0))  # different caption component
        components = ink.PageInk(page)
        cut = components.barrier_loss([2, 3, 5, 8], [0, 0, 20, 20], [0, 0, 20, 9])
        self.assertEqual(cut["strict_lost_pixels"], 4)
        intact = components.barrier_loss([2, 3, 5, 8], [0, 0, 20, 20], [0, 0, 10, 13])
        self.assertEqual(intact["strict_lost_pixels"], 0)

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "configured CV runtime required")
    def test_connected_pale_edges_are_retained_but_foreign_core_stays_separate(self):
        from PIL import Image

        page = Image.new("RGB", (20, 20), "white")
        page.putpixel((4, 4), (0, 0, 0))
        page.putpixel((5, 4), (240, 240, 240))
        page.putpixel((6, 4), (254, 254, 254))
        page.putpixel((12, 4), (0, 0, 0))
        page.putpixel((13, 4), (245, 245, 245))
        components = ink.PageInk(page)
        box, count, ambiguous = components.bbox([4, 4, 5, 5])
        self.assertEqual((box, count, ambiguous), ([4, 4, 7, 5], 1, 0))
        cut = components.barrier_loss([4, 4, 5, 5], [0, 0, 20, 20], [0, 0, 6, 20])
        self.assertEqual(cut["strict_lost_pixels"], 1)
        self.assertEqual(components.barrier_loss([4, 4, 5, 5], [0, 0, 20, 20],
                                                 [0, 0, 7, 20])["strict_lost_pixels"], 0)

        # A pale bridge touching a foreign strong core is not silently absorbed.
        for x in range(7, 12):
            page.putpixel((x, 4), (250, 250, 250))
        box, _, ambiguous = ink.PageInk(page).bbox([4, 4, 5, 5])
        self.assertEqual(box, [4, 4, 5, 5])
        self.assertEqual(ambiguous, 1)

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "configured CV runtime required")
    def test_rgb_only_antialias_survives_model_bcc_and_barrier(self):
        import numpy as np
        from PIL import Image
        from tools import source_figures_model as model

        page = Image.new("RGB", (20, 20), "white")
        page.putpixel((4, 4), (0, 0, 0))
        page.putpixel((5, 4), (255, 255, 254))
        self.assertEqual(page.convert("L").getpixel((5, 4)), 255)
        adapter = model.LayoutModel.__new__(model.LayoutModel)
        adapter.inference = mock.Mock()
        rgb, gray = np.asarray(page), np.asarray(page.convert("L"))
        adapter.inference.read_page.return_value = (rgb, gray, 20, 20)
        boxes = np.array([[14, 0.9, 4, 4, 5, 5, 1]], dtype=np.float32)
        masks = np.zeros((1, 2, 2), dtype=np.uint8)
        adapter.inference.outputs.return_value = ([boxes, masks], "raw.npz")
        adapter.handler = mock.Mock()
        adapter.handler.layout_shape_mode = "auto"
        adapter.handler._format_output.return_value = [{"boxes": boxes, "masks": masks}]
        adapter.pp = mock.Mock(return_value=[{"order": 1, "label": "image", "cls_id": 14,
                                              "score": .9, "coordinate": [4, 4, 5, 5]}])
        adapter.post = mock.Mock()
        adapter.names, adapter.metadata = ["boxes", "masks"], {}
        with mock.patch.object(model, "track_original_indices", return_value=([0], None)):
            result = adapter.predict("unused.png", "raw.npz")
        self.assertEqual(result["body_candidates"][0]["Bcc"], [4, 4, 6, 5])
        loss = adapter.page_ink.barrier_loss([4, 4, 5, 5], [0, 0, 20, 20], [0, 0, 5, 20])
        self.assertEqual(loss["strict_lost_pixels"], 1)

    @unittest.skipUnless(importlib.util.find_spec("cv2"), "configured CV runtime required")
    def test_seed_pale_ink_without_core_ownership_requires_review(self):
        from PIL import Image

        page = Image.new("RGB", (20, 20), "white")
        page.putpixel((3, 3), (0, 0, 0))
        page.putpixel((12, 12), (250, 250, 250))
        components = ink.PageInk(page)
        box, strong, ambiguous = components.bbox([1, 1, 15, 15])
        self.assertEqual((box, strong, ambiguous), ([3, 3, 13, 13], 1, 1))
        check = components.barrier_loss([1, 1, 15, 15], [0, 0, 20, 20], [0, 0, 11, 20])
        self.assertEqual(check["strict_lost_pixels"], 1)
        self.assertEqual(check["ambiguous_weak_component_count"], 1)


if __name__ == "__main__":
    unittest.main()
