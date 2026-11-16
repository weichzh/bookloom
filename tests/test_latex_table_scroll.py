from __future__ import annotations

import base64
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import translator

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
TABLE = (r"\begin{table}\centering\small\setlength{\tabcolsep}{3pt}"
         r"\renewcommand{\thetable}{\arabic{chapter}.\arabic{table}b}"
         r"\caption[Short]{Wide values}\label{tab:wide}"
         r"\begin{tabular}{ll}Name & Value\\alpha & 42\\beta & 99\\\end{tabular}\end{table}")
NORMAL = (r"\begin{table}\caption{Ordinary}\label{tab:normal}"
          r"\begin{tabular}{l}Later\\\end{tabular}\end{table}")


def source(wrapped: bool) -> str:
    inner = r"\sourcepage{17}\phantomsection\label{page-anchor}" + "\n" + TABLE
    if wrapped:
        inner = "\\begin{epubtablescroll}\n" + inner + "\n\\end{epubtablescroll}"
    return (r"\documentclass{book}\begin{document}\chapter{One}\setcounter{table}{2}" + "\n"
            + inner + "\n" + r"See \ref{tab:wide}." + "\n" + NORMAL
            + r"See \ref{tab:normal}.\end{document}")


def read(tmp_path: Path, data: str, *, enabled: bool):
    entry = tmp_path / "main.tex"
    entry.write_text(data, encoding="utf-8")
    expanded = tmp_path / "expanded.tex"
    expanded.write_text(translator.latex_epub_source_text(
        entry, tmp_path, native_caption_numbers=enabled), encoding="utf-8")
    cmd = translator.latex_epub_pandoc_command(
        root=ROOT, work_path=tmp_path, entry=entry, expanded=expanded,
        native_caption_numbers=enabled, language="en",
    )
    return json.loads(translator.run([*cmd, "--to=json"], cwd=tmp_path).stdout)


def walk(value, kind):
    if isinstance(value, dict):
        if value.get("t") == kind:
            yield value
        for child in value.values():
            yield from walk(child, kind)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, kind)


def flatten_scroll(value):
    blocks = []
    for block in value["blocks"]:
        if block.get("t") == "Div" and block["c"][0][1] == ["table-scroll"]:
            blocks.extend(block["c"][1])
        else:
            blocks.append(block)
    return {**value, "blocks": blocks}


@pytest.mark.parametrize("enabled", [False, True])
def test_wrapper_retains_native_table_all_cells_refs_and_counter_order(tmp_path, enabled):
    plain = read(tmp_path, source(False), enabled=enabled)
    wrapped = read(tmp_path, source(True), enabled=enabled)
    assert flatten_scroll(wrapped) == plain
    divisions = list(walk(wrapped, "Div"))
    assert len(divisions) == 1 and divisions[0]["c"][0][1] == ["table-scroll"]
    tables = list(walk(divisions[0], "Table"))
    assert len(tables) == 1 and tables[0]["c"][0][0] == "tab:wide"
    assert len(list(walk(wrapped, "Table"))) == 2
    links = {link["c"][2][0]: "".join(n["c"] for n in link["c"][1] if n["t"] == "Str")
             for link in walk(wrapped, "Link")}
    assert links["#tab:wide"] == "1.3b"
    assert links["#tab:normal"] == ("1.4" if enabled else "1.4b")
    html = translator.run(["pandoc", "--from=json", "--to=html5"], input_text=json.dumps(wrapped)).stdout
    tree = ET.fromstring("<root>" + html + "</root>")
    wrapper = tree.find("./div[@class='table-scroll']")
    assert wrapper is not None and wrapper.find("./table[@id='tab:wide']") is not None
    assert tree.find("./table[@id='tab:normal']") is not None
    cells = ["".join(cell.itertext()) for cell in wrapper.iter() if cell.tag in {"td", "th"}]
    assert cells == ["Name", "Value", "alpha", "42", "beta", "99"]
    assert len(wrapper.findall("./table/caption")) == 1


def test_wrapper_preserves_caption_and_cell_links_and_resource_normalization(tmp_path):
    (tmp_path / "diagram.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    table = (r"\begin{table}\caption[Short]{See \ref{ch:one}}\label{tab:linked}"
             r"\begin{tabular}{ll}\includegraphics{diagram.png} & \ref{ch:one}\\"
             r"\end{tabular}\end{table}")
    pre = r"\begin{document}\chapter{One}\label{ch:one}"
    after = r"See \ref{tab:linked}.\end{document}"
    plain = read(tmp_path, pre + table + after, enabled=True)
    wrapped = read(tmp_path, pre + r"\begin{epubtablescroll}" + table
                   + r"\end{epubtablescroll}" + after, enabled=True)
    assert flatten_scroll(wrapped) == plain
    table_node, = walk(wrapped, "Table")
    links = list(walk(table_node, "Link"))
    assert len(links) == 2 and all(link["c"][2][0] == "#ch:one" for link in links)
    image, = walk(table_node, "Image")
    assert image["c"][2][0] == "diagram.png"


def test_wrapper_preserves_outer_reader_macro_expansion(tmp_path):
    prefix = (r"\documentclass{book}\newcommand{\foo}{\alpha}\newcommand{\word}{alpha}"
              r"\begin{document}\chapter{One}")
    table = (r"\begin{table}\caption{\word title}\label{tab:macro}"
             r"\begin{tabular}{ll}\word & $\foo$\\\end{tabular}\end{table}")
    tail = r"See \ref{tab:macro}.\end{document}"
    plain = read(tmp_path, prefix + table + tail, enabled=True)
    wrapped = read(tmp_path, prefix + r"\begin{epubtablescroll}" + table
                   + r"\end{epubtablescroll}" + tail, enabled=True)
    assert flatten_scroll(wrapped) == plain
    math, = walk(wrapped, "Math")
    assert math["c"][1] == r"\alpha"
    assert all(raw["c"][1] not in {r"\foo", r"\word"} for raw in walk(wrapped, "RawInline"))


def test_starred_caption_inside_wrapper_does_not_advance_counter(tmp_path):
    star = (r"\begin{table}\caption*{Star}\label{tab:star}"
            r"\begin{tabular}{l}A\\\end{tabular}\end{table}")
    after = (r"\begin{table}\caption{After}\label{tab:after}"
             r"\begin{tabular}{l}B\\\end{tabular}\end{table}")
    pre = r"\begin{document}\chapter{One}"
    tail = after + r"See \ref{tab:after}.\end{document}"
    plain = read(tmp_path, pre + star + tail, enabled=True)
    wrapped = read(tmp_path, pre + r"\begin{epubtablescroll}" + star
                   + r"\end{epubtablescroll}" + tail, enabled=True)
    assert flatten_scroll(wrapped) == plain
    link, = walk(wrapped, "Link")
    assert link["c"][2][0] == "#tab:after" and link["c"][1][0]["c"] == "1.1"


def test_epub_contains_native_wrapper_caption_table_and_reference(tmp_path, monkeypatch):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    )
    (tmp_path / "cover.png").write_bytes(png)
    entry = tmp_path / "main.tex"
    entry.write_text(source(True), encoding="utf-8")
    output = tmp_path / "out.epub"
    monkeypatch.setattr(translator, "temp_root", lambda *args, **kwargs: tmp_path)
    translator.build_latex_epub_source(
        root=ROOT, work_path=tmp_path, entry=entry, output=output, title="Tables",
        author=None, language="en", identifier="urn:test:latex-scroll",
        cover=tmp_path / "cover.png", native_caption_numbers=True,
    )
    h = "{http://www.w3.org/1999/xhtml}"
    with zipfile.ZipFile(output) as archive:
        docs = [ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith(".xhtml")]
    wrappers = [n for doc in docs for n in doc.iter(h + "div")
                if "table-scroll" in n.get("class", "").split()]
    assert len(wrappers) == 1
    table = wrappers[0].find(h + "table")
    assert table is not None and table.get("id") == "tab:wide"
    captions = table.findall(h + "caption")
    assert len(captions) == 1 and "".join(captions[0].itertext()) == "Table 1.3b Wide values"
    assert ["".join(n.itertext()) for n in table.iter() if n.tag in {h + "td", h + "th"}] == [
        "Name", "Value", "alpha", "42", "beta", "99"]
    links = [n for doc in docs for n in doc.iter(h + "a") if n.get("href", "").endswith("#tab:wide")]
    assert len(links) == 1 and "".join(links[0].itertext()) == "1.3b"


@pytest.mark.parametrize("content", [
    "Visible text.\n" + TABLE,
    TABLE + "\nAnother paragraph.",
    TABLE + "\n" + NORMAL,
    r"\sourcepage{2}",
    r"\mystery{hidden}" + "\n" + TABLE,
    "\\begin{epubtablescroll}\n" + TABLE + "\n\\end{epubtablescroll}",
    r"\setcounter{table}{5}" + "\n" + TABLE,
])
def test_wrapper_rejects_non_table_content_multiple_and_nested(tmp_path, content):
    wrapped = (r"\begin{document}\begin{epubtablescroll}" + "\n" + content
               + "\n" + r"\end{epubtablescroll}\end{document}")
    with pytest.raises(translator.CliError, match="epubtablescroll"):
        read(tmp_path, wrapped, enabled=True)


@pytest.mark.skipif(not (shutil.which("pdflatex") and shutil.which("mutool")),
                    reason="pdflatex and mutool required")
def test_pdf_environment_preserves_table_text_geometry_and_cross_reference(tmp_path):
    shutil.copyfile(ROOT / "formats/latex/translator.sty", tmp_path / "translator.sty")
    table = (r"\begin{table}[h]\centering\caption{Measured values}\label{tab:measure}"
             r"\begin{tabular}{ll}Name & Value\\alpha & 42\\beta & 99\\\end{tabular}\end{table}")
    for name, body in (("plain", table), ("wrapped", r"\begin{epubtablescroll}" + table
                                        + r"\end{epubtablescroll}")):
        (tmp_path / f"{name}.tex").write_text(
            r"\documentclass{article}\usepackage{translator}\begin{document}"
            r"See table \ref{tab:measure}." + body + r"\end{document}", encoding="utf-8")
        for _ in range(2):
            translator.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"],
                           cwd=tmp_path)
        translator.run(["mutool", "draw", "-F", "stext", "-o", f"{name}.xml", f"{name}.pdf"],
                       cwd=tmp_path)
    plain = ET.parse(tmp_path / "plain.xml").getroot()
    wrapped = ET.parse(tmp_path / "wrapped.xml").getroot()
    assert [ET.tostring(page) for page in plain.findall("page")] == [
        ET.tostring(page) for page in wrapped.findall("page")
    ]
    assert r"\newlabel{tab:measure}{{1}{1}" in (tmp_path / "wrapped.aux").read_text(encoding="utf-8")


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ("latexmk", "xelatex", "mutool")),
                    reason="latexmk, xelatex and mutool required")
def test_pdf_only_never_requires_epub_fallback_resource(tmp_path, monkeypatch):
    entry = tmp_path / "main.tex"
    entry.write_text(r"\documentclass{book}\usepackage{translator}\begin{document}"
                     r"\epubfallback{not-present.png}{}{PRINTBRANCHTOKEN}\end{document}",
                     encoding="utf-8")
    output = tmp_path / "out.pdf"
    language = translator.Language("en", "source", entry, None, {"pdf": output})
    book = translator.Work(ROOT, tmp_path, {}, "pdf-only", "PDF only", "latex", None,
                           tmp_path / "source.pdf", 1, (language,))
    monkeypatch.setattr(translator, "temp_root", lambda *args, **kwargs: tmp_path)
    translator.build_latex(book, language, "pdf", output)
    text = translator.run(["mutool", "draw", "-F", "txt", str(output)], cwd=tmp_path).stdout
    assert "PRINTBRANCHTOKEN" in text and "not-present.png" not in text


@pytest.mark.parametrize("enabled", [False, True])
def test_original_footnote_counter_inside_table_survives_scroll_wrapper(tmp_path, enabled):
    ordinary = source(False).replace(r"\caption[Short]", r"\stepcounter{footnote}\caption[Short]")
    wrapped = source(True).replace(r"\caption[Short]", r"\stepcounter{footnote}\caption[Short]")
    plain = read(tmp_path, ordinary, enabled=enabled)
    actual = read(tmp_path, wrapped, enabled=enabled)
    assert flatten_scroll(actual) == plain


@pytest.mark.parametrize("environment", ["table", "longtable"])
@pytest.mark.parametrize("enabled", [False, True])
def test_original_float_note_is_retained_but_external_note_is_not_table_content(tmp_path, environment, enabled):
    note = r"\par\smallskip\begin{minipage}{.8\textwidth}Original $b=0$ note\footnote{Original footnote.}\end{minipage}"
    if environment == "longtable":
        table = (r"\begin{longtable}{ll}\caption{Values}\label{tab:note}\\A & 42\\B & 99\\\end{longtable}")
    else:
        table = ("\\begin{" + environment + "}" + r"\caption{Values}\label{tab:note}"
                 r"\begin{tabular}{ll}A & 42\\B & 99\\\end{tabular}" + note + "\\end{" + environment + "}")
    prefix, suffix = r"\begin{document}\chapter{One}", r"See \ref{tab:note}.\end{document}"
    plain = read(tmp_path, prefix + table + suffix, enabled=enabled)
    wrapped = read(tmp_path, prefix + r"\begin{epubtablescroll}" + table
                   + r"\end{epubtablescroll}" + suffix, enabled=enabled)
    assert flatten_scroll(wrapped) == plain
    with pytest.raises(translator.CliError, match="epubtablescroll"):
        read(tmp_path, prefix + r"\begin{epubtablescroll}" + table + note
             + r"\end{epubtablescroll}" + suffix, enabled=enabled)
