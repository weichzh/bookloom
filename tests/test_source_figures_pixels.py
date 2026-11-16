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


if __name__ == "__main__":
    unittest.main()
