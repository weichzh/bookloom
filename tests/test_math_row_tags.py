from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import gladtex_filter as gf
from tools import translator

ALIGN = r"\begin{align}x&=1\tag{1.1}\\y&=2\tag{1.2}\end{align}"
ALIGNED = ALIGN.replace("{align}", "{aligned}")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 40"><path d="M0 0L1 1"/></svg>'


@pytest.mark.parametrize("env", ["align", "align*", "aligned"])
def test_supported_top_level_multi_tag_body_is_preserved(env):
    body = r"x&=1\tag{1.1}\\y&=2\tag{1.2}"
    assert gf.row_tagged_alignment(r"\begin{" + env + "}" + body + r"\end{" + env + "}") == body


@pytest.mark.parametrize("formula", [
    r"x+1", r"x\tag{1.1}", r"\begin{align}x&=1\tag{1.1}\end{align}",
    r"\begin{gather}x=1\tag{1.1}\\y=2\tag{1.2}\end{gather}",
    r"\begin{align}x&=1\tag{1.1}\\y&=2\tag{1.2}\end{aligned}",
])
def test_unaffected_or_mismatched_formulas_do_not_enter_new_route(formula):
    assert gf.row_tagged_alignment(formula) is None


def test_only_rendering_copy_is_normalized():
    source = "\\rm{x}\n\n\\bf{y}\n \n\\it{z}"
    assert gf.normalize_display_input(source) == "\\mathrm{x}\n\\mathbf{y}\n\\mathit{z}"
    assert source == "\\rm{x}\n\n\\bf{y}\n \n\\it{z}"


def write_cache(directory: Path):
    cache = {"GladTeX__cache__version": "2.0"}
    for index, (formula, display) in enumerate([(ALIGN, True), (ALIGNED, True), ("x", True), (ALIGN, False)]):
        name = f"eqn{index:03d}.svg"
        (directory / name).write_bytes(SVG)
        cache.setdefault(formula, {})[str(display).lower()] = {
            "path": name, "pos": {"width": 80, "height": 40, "depth": 0},
        }
    path = directory / "gladtex.cache"
    path.write_text(json.dumps(cache), encoding="utf8")
    return path


def test_old_multi_tag_cache_misses_without_invalidating_other_formulas(tmp_path):
    path = write_cache(tmp_path)
    formulas = [(ALIGN, True), (ALIGNED, True), ("x", True), (ALIGN, False)]
    assert gf.cache_statistics(path, tmp_path, formulas, lambda s: s) == (2, 2)
    data = json.loads(path.read_text())
    assert "true" not in data[ALIGN] and ALIGNED not in data
    assert (tmp_path / "eqn002.svg").read_bytes() == SVG
    assert (tmp_path / "eqn003.svg").read_bytes() == SVG


def test_new_row_tag_cache_hits_and_repeated_stamp_is_idempotent(tmp_path):
    path = write_cache(tmp_path)
    formulas = [(ALIGN, True), (ALIGNED, True), ("x", True), (ALIGN, False)]
    gf.stamp_row_tag_svgs(path, tmp_path, formulas, lambda s: s)
    before = (tmp_path / "eqn000.svg").read_bytes()
    root = ET.fromstring(before)
    assert root.find("{http://www.w3.org/2000/svg}metadata").text == gf.ROW_TAG_RENDER_VERSION
    gf.stamp_row_tag_svgs(path, tmp_path, formulas, lambda s: s)
    assert (tmp_path / "eqn000.svg").read_bytes() == before
    assert (tmp_path / "eqn002.svg").read_bytes() == SVG
    assert (tmp_path / "eqn003.svg").read_bytes() == SVG
    assert gf.cache_statistics(path, tmp_path, formulas, lambda s: s) == (4, 0)


def test_old_epub_seed_cannot_resurrect_last_tag_only_rendering(tmp_path):
    original = tmp_path / "old.epub"
    document = {"blocks": [{"t": "Para", "c": [{"t": "Math", "c": [{"t": "DisplayMath"}, ALIGN]}]}]}
    alt = translator.gladtex_alt_text(ALIGN)
    root = ET.Element("html")
    ET.SubElement(root, "img", {
        "class": "math-display", "src": "../media/old.svg", "alt": alt,
        "width": "80", "height": "40", "style": "vertical-align: 0px",
    })
    with zipfile.ZipFile(original, "w") as z:
        z.writestr("EPUB/text/ch.xhtml", ET.tostring(root))
        z.writestr("EPUB/media/old.svg", SVG)
    cache = tmp_path / "formulas"
    assert translator.seed_svg_math_cache(original, document, cache) == 1
    path = cache / "gladtex.cache"
    assert gf.cache_statistics(path, cache, [(ALIGN, True)], translator.gladtex_formula_key) == (0, 1)


@pytest.mark.parametrize("position", ["t", "b", "c"])
def test_aligned_position_is_not_math_but_align_brackets_are(position):
    body = r"x&=1\tag{1.1}\\y&=2\tag{1.2}"
    assert gf.row_tagged_alignment(r"\begin{aligned}[" + position + "]" + body + r"\end{aligned}") == body
    assert gf.row_tagged_alignment(r"\begin{align}[" + position + "]" + body + r"\end{align}") == "[" + position + "]" + body


@pytest.mark.skipif(__import__("shutil").which("pandoc") is None, reason="Pandoc required")
def test_native_row_tag_metadata_survives_real_epub_and_epubcheck(tmp_path):
    import base64

    entry, output = tmp_path / "main.md", tmp_path / "book.epub"
    entry.write_text("---\nlang: en\ntitle: Row tags\n---\n# Body {#body}\n\n$$\n" + ALIGN + "\n$$\n", encoding="utf8")
    (tmp_path / "cover.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII="
    ))
    language = translator.Language("en", "source", entry, None, {"epub": output})
    work = translator.Work(
        translator.REPO_ROOT, tmp_path,
        {"epub": {"title": "Row tags", "cover": "cover.png", "title_page": False,
                  "toc_depth": 1, "split_level": 1, "bodymatter": "body"}},
        "row-tags-fixture", "Row tags", "markdown", None, tmp_path / "source.pdf", 1, (language,),
    )
    translator.build_markdown(work, language, "epub", output)
    evidence = tmp_path / "qa"
    evidence.mkdir()
    translator.qa_epub(work, language, output, evidence)
    with zipfile.ZipFile(output) as archive:
        docs = [ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith(".xhtml")]
        images = [n for doc in docs for n in doc.iter() if n.tag.rsplit("}", 1)[-1] == "img"
                  and "math-display" in n.get("class", "").split()]
        assert len(images) == 1
        assert all(tag in images[0].get("alt") for tag in ("1.1", "1.2"))
        svgs = [ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith(".svg")]
        assert len(svgs) == 1
        assert sum(n.tag == "{http://www.w3.org/2000/svg}metadata"
                   and n.text == gf.ROW_TAG_RENDER_VERSION for n in svgs[0].iter()) == 1
