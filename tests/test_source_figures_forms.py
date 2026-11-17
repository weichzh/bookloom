from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import source_figures_forms as forms
from tools import source_figures_logic as logic

ROOT = Path(__file__).resolve().parents[1]
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


def make_pdf(path, page_content, definitions):
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                               FloatObject, NameObject, NumberObject)
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=100)
    objects = {}
    for name, spec in definitions.items():
        form = DecodedStreamObject()
        form.set_data(spec.get("content", "").encode("ascii"))
        form[NameObject("/Type")] = NameObject("/XObject")
        if spec.get("image"):
            form[NameObject("/Subtype")] = NameObject("/Image")
            form[NameObject("/Width")] = NumberObject(1)
            form[NameObject("/Height")] = NumberObject(1)
            form[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
            form[NameObject("/BitsPerComponent")] = NumberObject(8)
            objects[name] = writer._add_object(form)
            continue
        form[NameObject("/Subtype")] = NameObject("/Form")
        form[NameObject("/BBox")] = ArrayObject([FloatObject(x) for x in spec["bbox"]])
        if "matrix" in spec:
            form[NameObject("/Matrix")] = ArrayObject([FloatObject(x) for x in spec["matrix"]])
        resources = DictionaryObject()
        if "children" in spec:
            resources[NameObject("/XObject")] = DictionaryObject({
                NameObject("/" + alias): objects[child] for alias, child in spec["children"].items()})
        if spec.get("font"):
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica"),
                                     NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
            resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        form[NameObject("/Resources")] = resources
        objects[name] = writer._add_object(form)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({
        NameObject("/" + name): ref for name, ref in objects.items()})})
    content = DecodedStreamObject()
    content.set_data(page_content.encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


@unittest.skipUnless(HAS_PYPDF, "pypdf optional capability not installed")
class FormExtractionTests(unittest.TestCase):
    def test_nested_form_transforms_and_object_identity(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "source.pdf"
            make_pdf(path, "q 2 0 0 2 10 20 cm /G Do Q", {
                "Child": {"bbox": [0, 0, 4, 6]},
                "G": {"bbox": [0, 0, 10, 20], "matrix": [1, 0, 0, 1, 5, 2],
                      "content": "q 1 0 0 1 1 2 cm /C Do Q", "children": {"C": "Child"}},
            })
            page = forms.extract(path, [1])[1]
        self.assertEqual(page["status"], "ok")
        parent, child = page["placements"]
        self.assertEqual(parent["bbox_display_pt"], [20, 36, 40, 76])
        self.assertEqual(child["bbox_display_pt"], [22, 60, 30, 72])
        self.assertEqual(child["parent_id"], parent["id"])
        self.assertNotEqual(parent["object_id"], child["object_id"])

    def test_rectangular_clips_and_unknown_geometry_remain_distinct(self):
        definition = {"G": {"bbox": [0, 0, 20, 20]}}
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "source.pdf"
            def read(clip):
                make_pdf(path, "q " + clip + " /G Do Q", definition)
                return forms.extract(path, [1])[1]["placements"][0]
            self.assertTrue(read("0 0 30 30 re W n")["eligible"])
            self.assertTrue(read("0 0 m 30 0 l 30 30 l 0 30 l h W n")["eligible"])
            self.assertEqual(read("0 0 10 30 re W n")["clip_status"], "partial")
            self.assertEqual(read("0 0 m 10 10 20 10 20 20 c W n")["clip_status"], "unknown")
            crossing = read("0 0 m 30 30 l 30 0 l 0 30 l h W n")
            self.assertEqual(crossing["clip_status"], "unknown")

    def test_page_container_is_not_semantic_group_and_child_keeps_parent_clip(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "source.pdf"
            make_pdf(path, "/Page Do", {
                "Child": {"bbox": [0, 0, 20, 20]},
                "Page": {"bbox": [0, 0, 200, 100], "content": "/C Do",
                         "children": {"C": "Child"}},
            })
            page = forms.extract(path, [1])[1]
        self.assertTrue(page["placements"][0]["page_sized"])
        self.assertFalse(page["placements"][0]["eligible"])
        self.assertTrue(page["placements"][1]["eligible"])
        self.assertEqual(page["placements"][1]["parent_id"], page["placements"][0]["id"])

    def test_form_text_is_matched_by_source_glyph_identity_not_box_alone(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "source.pdf"
            make_pdf(path, "q 1 0 0 1 50 20 cm /G Do Q", {
                "G": {"bbox": [0, 0, 40, 20], "font": True,
                      "content": "BT /F1 10 Tf 1 0 0 1 5 5 Tm (L) Tj ET"},
            })
            data = forms.pixel_placements(forms.extract(path, [1])[1], 1, 200, 100)
        form = data["placements"][0]
        def line(name, char, box):
            return {"id": name, "glyphs": [{"id": name + ":g", "text": char, "bbox_pt": box}]}
        source = [[line("legend", "L", [55, 68, 61, 75]),
                   line("foreign", "Z", [70, 68, 75, 75])]]
        proven, unproven = logic.form_glyph_ownership(form, data["placements"], source, [], 1)
        self.assertEqual([g["line_id"] for g in proven], ["legend"])
        self.assertEqual([g["line_id"] for g in unproven], ["foreign"])
        duplicate = source + [[line("duplicate", "L", [55, 68, 61, 75])]]
        proven, unproven = logic.form_glyph_ownership(form, data["placements"], duplicate, [], 1)
        self.assertFalse(proven)
        self.assertEqual(len(unproven), 3)

    def test_nested_image_invocation_is_bound_to_form_identity(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "source.pdf"
            make_pdf(path, "q 1 0 0 1 40 10 cm /G Do Q", {
                "Image": {"image": True, "content": "x"},
                "G": {"bbox": [0, 0, 40, 20], "content": "q 10 0 0 5 3 4 cm /I Do Q",
                      "children": {"I": "Image"}},
            })
            data = forms.pixel_placements(forms.extract(path, [1])[1], 1, 200, 100)
        image = data["image_invocations"][0]
        self.assertEqual(image["continuous_bbox_px"], [43, 81, 53, 86])
        self.assertEqual(image["ancestor_ids"], [data["placements"][0]["id"]])
        trace = [{"id": "trace-image", "continuous_bbox_px": [43, 81, 53, 86]}]
        proven, unproven = logic.form_image_ownership(data["placements"][0], trace, [image])
        self.assertEqual(proven, [{"trace_image_id": "trace-image", "pdf_image_id": image["id"]}])
        self.assertEqual(unproven, [])
        image["ancestor_ids"] = []
        self.assertEqual(logic.form_image_ownership(data["placements"][0], trace, [image])[1], ["trace-image"])

    def test_single_reader_is_used_for_all_selected_pages(self):
        import pypdf
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            path = Path(temp) / "source.pdf"
            make_pdf(path, "/G Do", {"G": {"bbox": [0, 0, 10, 10]}})
            reader = pypdf.PdfReader
            with mock.patch.object(pypdf, "PdfReader", wraps=reader) as init:
                data = forms.extract(path, [1])
        self.assertEqual(init.call_count, 1)
        self.assertEqual(data[1]["status"], "ok")


class OptionalFormsTests(unittest.TestCase):
    def test_absent_optional_pypdf_records_unavailable_without_crashing(self):
        real_import = builtins.__import__
        def without_pypdf(name, *args, **kwargs):
            if name == "pypdf" or name.startswith("pypdf."):
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=without_pypdf):
            result = forms.extract("unused.pdf", [1, 2])
        self.assertEqual(result[1]["status"], "unavailable")
        self.assertFalse(result[2]["available"])
        self.assertEqual(result[2]["placements"], [])


if __name__ == "__main__":
    unittest.main()
