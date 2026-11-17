from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tools import source_figures_caption as caption


def native_line(name, text, x, y, *, advance=5, height=8):
    glyphs = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        left = x + index * advance
        box = [left, y, left + advance, y + height]
        glyphs.append({"id": f"{name}:glyph-{index}", "text": char, "bbox_pt": box,
                       "quad_pt": [box[0], box[1], box[2], box[1],
                                   box[0], box[3], box[2], box[3]]})
    return {"id": name, "text": text, "normalized": caption.norm(text),
            "glyphs": glyphs, "error": None}


def bounds(lines):
    return caption.union([glyph["bbox_pt"] for line in lines for glyph in line["glyphs"]])


def entry(text, number, box, *, kind="Figure"):
    item = {"name": number, "figType": kind}
    return ("figures", item, text, dict(zip(("x1", "y1", "x2", "y2"), box)))


def math_caption(x=10, y=20):
    head = "Figure B.1: Values "
    first = native_line("block-0:line-0", head, x, y)
    math = native_line("block-1:line-0", "β and states", x + 5 * len(head), y)
    last = native_line("block-1:line-1", "continue here.", x, y + 12)
    return first, math, last


def test_alphabetic_number_and_exact_number_identity():
    for value in ("B.1", "AA.12.3", "S-2", "a1", "1.1", "4a"):
        assert caption.caption_number(value) == value.casefold()
    assert caption.prefix_identity("Fig. B.1: text")[:2] == ("figure", "b.1")
    assert caption.prefix_identity("Table AA.12.3. text")[:2] == ("table", "aa.12.3")
    assert caption.prefix_identity("Figure 1.1: text")[:2] != ("figure", "11")
    assert caption.prefix_identity("Figure B.10: text")[:2] != ("figure", "b.1")
    assert caption.caption_number("B.") is None


def test_legacy_exact_text_remains_available():
    first = native_line("block-0:line-0", "Figure 1. A simple diagram", 10, 20)
    result = caption.align_caption(entry(first["text"], "1", bounds([first])), [[first]])
    assert result["status"] == "ok"
    assert result["alignment_method"] == "exact_text"
    assert result["source_snap"]["line_ids"] == [first["id"]]
    assert result["source_snap"]["glyphs"] == first["glyphs"]


def test_exact_caption_without_label_separator_retains_existing_entry_point():
    line = native_line("block-0:line-0", "Figure 1 A simple diagram", 10, 20)
    result = caption.align_caption(entry(line["text"], "1", bounds([line])), [[line]])
    assert result["status"] == "ok"
    assert result["alignment_method"] == "exact_text"
    assert result["prefix_label_separator"] is False


def test_exact_caption_preserves_predictor_text_without_its_visible_label():
    line = native_line("block-0:line-0", "Figure 1. A simple diagram", 10, 20)
    result = caption.align_caption(entry("A simple diagram", "1", bounds([line])), [[line]])
    assert result["status"] == "ok"
    assert result["alignment_method"] == "exact_text"
    assert result["prefix_label_separator"] is True


def test_exact_normalization_cannot_drop_question_mark_math_in_another_block():
    head = "Figure 1: Value "
    first = native_line("block-0:line-0", head, 10, 20)
    math = native_line("block-1:line-0", "β", 10 + 5 * len(head), 20)
    result = caption.align_caption(entry("Figure 1: Value ?", "1", bounds([first, math])),
                                   [[first], [math]])
    assert result["status"] == "ok"
    assert result["alignment_method"] == "native_boundary_identity"
    assert result["source_snap"]["glyphs"][-1]["text"] == "β"
    assert result["source_snap"]["line_ids"] == [first["id"], math["id"]]


def test_boundary_identity_keeps_every_math_glyph_and_original_block_order():
    first, math, last = math_caption()
    text = "Figure B.1: Values ? and states continue here."
    result = caption.align_caption(entry(text, "B.1", bounds([first, math, last])),
                                   [[first], [math, last]])
    assert result["status"] == "ok"
    assert result["alignment_method"] == "native_boundary_identity"
    assert result["text_equivalence"] == "not_established"
    snap = result["source_snap"]
    assert snap["line_ids"] == [first["id"], math["id"], last["id"]]
    assert snap["lines"] == [first["text"], math["text"], last["text"]]
    assert snap["glyphs"] == first["glyphs"] + math["glyphs"] + last["glyphs"]
    assert snap["source_snap_bbox_pt"] == bounds([first, math, last])
    assert [g["text"] for g in snap["glyphs"]].count("β") == 1
    assert snap["glyphs"][len(first["glyphs"])]["quad_pt"] == math["glyphs"][0]["quad_pt"]


def test_native_negation_overlay_survives_different_pf2_transcoding():
    first = native_line("block-0:line-0", "Figure B.1: A signal obeys", 10, 20)
    last = native_line("block-1:line-0", "00\u0338 = 0.", 10, 32)
    overlay = next(g for g in last["glyphs"] if g["text"] == "\u0338")
    equals = next(g for g in last["glyphs"] if g["text"] == "=")
    x = equals["bbox_pt"][0]
    overlay["bbox_pt"] = [x, 32, x, 40]
    overlay["quad_pt"] = [x, 32, x, 32, x, 40, x, 40]
    predictor = "Figure B.1: A signal obeys 00 6=0."
    result = caption.align_caption(entry(predictor, "B.1", bounds([first, last])),
                                   [[first], [last]])
    assert result["status"] == "ok"
    assert result["alignment_method"] == "native_boundary_identity"
    assert result["text_equivalence"] == "not_established"
    assert result["pf2_text_comparison"] == "bounded_glyph_compatible"
    assert result["source_snap"]["lines"] == [first["text"], last["text"]]
    assert result["source_snap"]["glyphs"] == first["glyphs"] + last["glyphs"]
    assert result["source_snap"]["glyphs"][len(first["glyphs"]) + 2] == overlay
    assert overlay["bbox_pt"][0] == overlay["bbox_pt"][2] == equals["bbox_pt"][0]

    control = caption.align_caption(entry("Figure B.1: A signal obeys 00\u0338 = 0.", "B.1",
                                           bounds([first, last])), [[first], [last]])
    assert control["status"] == "ok"
    assert control["text_equivalence"] == "normalized_exact"
    assert control["pf2_text_comparison"] == "literal_whitespace_equal"


def test_boundary_identity_can_use_predictor_body_without_inventing_a_source_label():
    first, math, last = math_caption()
    result = caption.align_caption(entry("Values ? and states continue here.", "B.1",
                                         bounds([first, math, last])), [[first], [math, last]])
    assert result["status"] == "ok"
    assert result["alignment_method"] == "native_boundary_identity"
    assert result["source_snap"]["lines"][0] == first["text"]
    assert result["caption_number"] == "b.1"


def test_caption_prefix_itself_may_cross_adjacent_native_blocks():
    left = native_line("block-0:line-0", "Fig", 10, 20)
    right = native_line("block-1:line-0", "ure B.1: Value β", 25, 20)
    last = native_line("block-2:line-0", "continues here.", 10, 32)
    text = "Figure B.1: Value ? continues here."
    result = caption.align_caption(entry(text, "B.1", bounds([left, right, last])),
                                   [[left], [right], [last]])
    assert result["status"] == "ok"
    assert result["source_snap"]["line_ids"] == [left["id"], right["id"], last["id"]]


def test_side_caption_does_not_take_text_in_another_column():
    first, math, last = math_caption(x=300, y=40)
    unrelated = native_line("block-2:line-0", "Unrelated illustration", 20, 40)
    result = caption.align_caption(entry("Figure B.1: Values ? and states continue here.",
                                         "B.1", bounds([first, math, last])),
                                   [[first], [math, last], [unrelated]])
    assert result["status"] == "ok"
    assert unrelated["id"] not in result["source_snap"]["line_ids"]


def test_wide_boundary_spanning_a_second_column_is_ambiguous():
    first, math, last = math_caption()
    other = native_line("block-2:line-0", "unrelated words", 250, 20)
    box = bounds([first, math, last, other])
    result = caption.align_caption(entry("Figure B.1: Values ? and states continue here.",
                                         "B.1", box), [[first], [math, last], [other]])
    assert result["status"] == "ambiguous"
    assert "columns" in result["reason"]


def test_nearby_last_line_cannot_be_silently_cut_off():
    first, math, last = math_caption()
    omitted = native_line("block-2:line-0", "and a final line.", 10, 44)
    text = "Figure B.1: Values ? and states continue here."
    result = caption.align_caption(entry(text, "B.1", bounds([first, math, last])),
                                   [[first], [math, last], [omitted]])
    assert result["status"] == "ambiguous"
    assert "continue" in result["reason"]


def test_neighboring_paragraph_inside_boundary_is_not_swallowed():
    first, math, last = math_caption()
    neighbor = native_line("block-2:line-0", "An unrelated paragraph.", 10, 52)
    text = "Figure B.1: Values ? and states continue here."
    result = caption.align_caption(entry(text, "B.1", bounds([first, math, last, neighbor])),
                                   [[first], [math, last], [neighbor]])
    assert result["status"] == "unavailable"
    assert "inconsistent" in result["reason"]


def test_duplicate_prefix_anywhere_on_page_is_ambiguous():
    first, math, last = math_caption()
    duplicate = native_line("block-2:line-0", "Figure B.1: another caption", 10, 100)
    result = caption.align_caption(entry("Figure B.1: Values ? and states continue here.",
                                         "B.1", bounds([first, math, last])),
                                   [[first], [math, last], [duplicate]])
    assert result["status"] == "ambiguous"
    assert "unique" in result["reason"]


def test_duplicate_prefixes_in_one_visual_row_are_not_joined_as_one():
    first, math, last = math_caption()
    duplicate = native_line("block-2:line-0", "Figure B.1: duplicate", 160, 20)
    result = caption.align_caption(entry("Figure B.1: Values ? and states continue here.",
                                         "B.1", bounds([first, math, last, duplicate])),
                                   [[first], [math, last], [duplicate]])
    assert result["status"] == "ambiguous"
    assert "unique" in result["reason"]


def test_another_caption_prefix_inside_boundary_is_ambiguous():
    first, math, last = math_caption()
    other = native_line("block-2:line-0", "Figure B.2: Neighbor", 10, 48)
    result = caption.align_caption(entry("Figure B.1: Values ? and states continue here.",
                                         "B.1", bounds([first, math, last, other])),
                                   [[first], [math, last], [other]])
    assert result["status"] == "ambiguous"
    assert "another prefix" in result["reason"]


def test_two_labels_in_a_single_native_line_are_not_one_caption():
    line = native_line("block-0:line-0", "Figure 1: Value β. Figure 2: Other", 10, 20)
    for text in (line["text"], "Figure 1: Value ?. Figure 2: Other"):
        result = caption.align_caption(entry(text, "1", bounds([line])), [[line]])
        assert result["status"] == "ambiguous"
        assert "another prefix" in result["reason"]


def test_body_reference_at_line_start_is_not_a_caption():
    first = native_line("block-0:line-0", "Figure 1 shows the value ", 10, 20)
    math = native_line("block-1:line-0", "β.", 10 + 5 * len(first["text"]), 20)
    result = caption.align_caption(entry("Figure 1 shows the value ?.", "1", bounds([first, math])),
                                   [[first], [math]])
    assert result["status"] == "unavailable"
    assert "labelled prefix" in result["reason"]


def test_unlabelled_source_and_wrong_number_remain_unavailable():
    line = native_line("block-0:line-0", "An unnumbered illustration", 10, 20)
    assert caption.align_caption(entry("An unnumbered illustration", "1", bounds([line])),
                                 [[line]])["status"] == "unavailable"
    source = native_line("block-0:line-0", "Figure 11. Illustration", 10, 20)
    result = caption.align_caption(entry("Figure 1.1. Illustration", "1.1", bounds([source])),
                                   [[source]])
    assert result["status"] == "unavailable"


def test_boundary_identity_rejects_conflicting_native_or_predictor_identity():
    first, math, last = math_caption()
    box = bounds([first, math, last])
    blocks = [[first], [math, last]]
    assert caption.align_caption(entry("Figure B.2: Values ? and states continue here.",
                                       "B.1", box), blocks)["status"] == "unavailable"
    assert caption.align_caption(entry("Figure B.2: Values ? and states continue here.",
                                       "B.2", box), blocks)["status"] == "unavailable"
    assert caption.align_caption(entry("Table B.1: Values ? and states continue here.",
                                       "B.1", box, kind="Table"), blocks)["status"] == "unavailable"


def test_boundary_identity_rejects_a_partial_native_line():
    first, math, last = math_caption()
    box = bounds([first, math, last])
    box[2] -= 25
    result = caption.align_caption(entry("Figure B.1: Values ? and states continue here.",
                                         "B.1", box), [[first], [math, last]])
    assert result["status"] == "unavailable"
    assert "extents" in result["reason"]


def test_unknown_coordinates_or_unrepresented_page_glyphs_block_boundary_identity():
    first, math, last = math_caption()
    text = "Figure B.1: Values ? and states continue here."
    bad = native_line("block-2:line-0", "off boundary", 10, 100)
    del bad["glyphs"][0]["quad_pt"]
    result = caption.align_caption(entry(text, "B.1", bounds([first, math, last])),
                                   [[first], [math, last], [bad]])
    assert result["status"] == "unavailable"
    assert "unlocated" in result["reason"]
    bad = native_line("block-2:line-0", "off boundary", 10, 100)
    bad["glyphs"].pop()
    assert caption.align_caption(entry(text, "B.1", bounds([first, math, last])),
                                 [[first], [math, last], [bad]])["status"] == "unavailable"


def test_missing_pf2_words_do_not_establish_a_complete_caption():
    first, math, last = math_caption()
    result = caption.align_caption(entry("Figure B.1: Values ? continue here.",
                                         "B.1", bounds([first, math, last])),
                                   [[first], [math, last]])
    assert result["status"] == "unavailable"
    assert "inconsistent" in result["reason"]


def test_unknown_pf2_body_does_not_erase_known_native_glyph():
    head = "Figure 1: "
    first = native_line("block-0:line-0", head, 10, 20)
    math = native_line("block-1:line-0", "β", 10 + 5 * len(head), 20)
    result = caption.align_caption(entry("Figure 1: ?", "1", bounds([first, math])),
                                   [[first], [math]])
    assert result["status"] == "ok"
    assert result["text_equivalence"] == "not_established"
    assert result["pf2_text_comparison"] == "bounded_glyph_compatible"
    assert result["source_snap"]["glyphs"][-1]["text"] == "β"


def test_incomplete_pf2_prefix_remains_unavailable():
    first, math, last = math_caption()
    result = caption.align_caption(entry("Figure B.1 Values ? and states continue here.",
                                         "B.1", bounds([first, math, last])),
                                   [[first], [math, last]])
    assert result["status"] == "unavailable"
    assert "labelled prefix" in result["reason"]


def test_question_mark_can_represent_exactly_one_native_glyph():
    line = native_line("block-0:line-0", "Figure 1: Value 42", 10, 20)
    result = caption.align_caption(entry("Figure 1: Value ?2", "1", bounds([line])), [[line]])
    assert result["status"] == "ok"
    assert result["text_equivalence"] == "not_established"
    assert result["source_snap"]["glyphs"][-2]["text"] == "4"

    ligature = native_line("block-0:line-0", "Figure 1: letter ﬁ", 10, 20)
    result = caption.align_caption(entry("Figure 1: letter ?", "1", bounds([ligature])), [[ligature]])
    assert result["status"] == "ok"
    assert result["source_snap"]["glyphs"][-1]["text"] == "ﬁ"
    split = caption.align_caption(entry("Figure 1: letter f?", "1", bounds([ligature])), [[ligature]])
    assert split["status"] == "unavailable"


def test_concrete_alphanumeric_changes_and_extra_prose_are_not_math_slots():
    first, math, last = math_caption()
    box = bounds([first, math, last])
    blocks = [[first], [math, last]]
    for text in ("Figure B.1: Values α and states continue here.",
                 "Figure B.1: Values ? and states added prose continue here."):
        result = caption.align_caption(entry(text, "B.1", box), blocks)
        assert result["status"] == "unavailable"
        assert "inconsistent" in result["reason"]

    number = native_line("block-0:line-0", "Figure 1: Value 42", 10, 20)
    result = caption.align_caption(entry("Figure 1: Value 43", "1", bounds([number])), [[number]])
    assert result["status"] == "unavailable"
    assert "inconsistent" in result["reason"]
    number_by_symbol = native_line("block-0:line-0", "Figure 1: Value x=42", 10, 20)
    result = caption.align_caption(entry("Figure 1: Value x=43", "1", bounds([number_by_symbol])),
                                   [[number_by_symbol]])
    assert result["status"] == "unavailable"
    assert "inconsistent" in result["reason"]


def test_one_math_slot_cannot_consume_an_extra_word():
    line = native_line("block-0:line-0", "Figure 1: Sum ∑ values", 10, 20)
    result = caption.align_caption(entry("Figure 1: Sum abc values", "1", bounds([line])), [[line]])
    assert result["status"] == "unavailable"
    assert "inconsistent" in result["reason"]


def test_math_symbol_slot_allows_one_character_but_punctuation_does_not():
    line = native_line("block-0:line-0", "Figure 1: Sum ∑ values", 10, 20)
    result = caption.align_caption(entry("Figure 1: Sum 6 values", "1", bounds([line])), [[line]])
    assert result["status"] == "ok"
    assert any(g["text"] == "∑" for g in result["source_snap"]["glyphs"])
    assert result["text_equivalence"] == "not_established"

    punctuation = native_line("block-0:line-0", "Figure 1: Sum a,b", 10, 20)
    result = caption.align_caption(entry("Figure 1: Sum a6b", "1", bounds([punctuation])),
                                   [[punctuation]])
    assert result["status"] == "unavailable"


def test_load_lines_keeps_stable_original_ids_zero_advance_and_illegal_count(tmp_path: Path):
    xml = ('<document><page width="100" height="100"><block/><block><line>'
           '<char c="A" quad="1 2 6 2 1 10 6 10"/>'
           '<char c=" " quad="6 2 7 2 6 10 7 10"/>'
           '<char c="i" quad="7 2 7 2 7 10 7 10"/>'
           '<char c="&#x1;" quad="7 2 8 2 7 10 8 10"/>'
           '</line></block></page></document>')
    path = tmp_path / "source.stext.xml"
    path.write_text(xml, encoding="utf8")
    metadata = {"rotation_deg": 0, "cropbox_pt": [0, 0, 100, 100],
                "mutool_display_pt_to_pixel_affine": [225 / 72, 0, 0, 225 / 72, 0, 0]}
    blocks, removed = caption.load_lines(path, metadata)
    assert removed == 1
    assert blocks[0][0]["id"] == "block-1:line-0"
    assert [g["id"] for g in blocks[0][0]["glyphs"]] == [
        "block-1:line-0:glyph-0", "block-1:line-0:glyph-2"]
    assert blocks[0][0]["glyphs"][1]["bbox_pt"] == [7, 2, 7, 10]
    assert blocks[0][0]["error"] == "missing source glyph text"


@pytest.mark.skipif(not importlib.util.find_spec("PIL") or not importlib.util.find_spec("numpy"),
                    reason="configured image runtime required")
def test_white_panel_retains_every_nonwhite_rgb_edge_without_tolerance():
    from PIL import Image

    image = Image.new("RGB", (20, 20), "white")
    image.putpixel((2, 7), (254, 255, 255))
    image.putpixel((5, 3), (255, 254, 255))
    image.putpixel((8, 8), (0, 0, 0))
    image.putpixel((12, 10), (255, 255, 254))
    assert caption.ink_bbox(image, [1, 1, 15, 15]) == [2, 3, 13, 11]
    assert caption.ink_bbox(image, [2, 4, 12, 10]) == [2, 7, 9, 9]
    assert caption.ink_bbox(image, [15, 15, 20, 20]) is None
    image.paste((246, 247, 249), (1, 1, 15, 15))
    assert caption.ink_bbox(image, [1, 1, 15, 15]) == [1, 1, 15, 15]
