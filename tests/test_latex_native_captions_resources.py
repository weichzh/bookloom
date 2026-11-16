from __future__ import annotations

import base64
import json
import posixpath
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest

from tools import translator

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
)
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><title>vector</title></svg>'
pytestmark = pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")


def nodes(value, kind):
    if isinstance(value, dict):
        if value.get("t") == kind:
            yield value
        for child in value.values():
            yield from nodes(child, kind)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child, kind)


def text(value):
    if isinstance(value, list):
        return "".join(text(child) for child in value)
    if not isinstance(value, dict):
        return ""
    if value.get("t") == "Str":
        return value["c"]
    if value.get("t") in {"Space", "SoftBreak", "LineBreak"}:
        return " "
    if value.get("t") in {"Code", "Math"}:
        return value["c"][-1]
    return text(value.get("c"))


def work(tmp_path):
    (tmp_path / "image.png").write_bytes(PNG)
    (tmp_path / "image.jpg").write_bytes(JPEG)
    (tmp_path / "image.jpeg").write_bytes(JPEG)
    (tmp_path / "image.svg").write_bytes(SVG)
    return tmp_path


def read_latex(tmp_path, source, *, enabled=True, language="zh-CN"):
    entry = tmp_path / "main.tex"
    entry.write_text(source, encoding="utf-8")
    expanded = tmp_path / "expanded.tex"
    expanded.write_text(translator.latex_epub_source_text(
        entry, tmp_path, native_caption_numbers=enabled
    ), encoding="utf-8")
    command = translator.latex_epub_pandoc_command(
        root=ROOT, work_path=tmp_path, entry=entry, expanded=expanded,
        has_part=translator.latex_source_has_part(source),
        native_caption_numbers=enabled, language=language,
    )
    document = json.loads(translator.run([*command, "--to=json"], cwd=tmp_path).stdout)
    return document, expanded.read_text(encoding="utf-8")


def figure(caption, label="", *, env="figure"):
    return (r"\begin{" + env + r"}\includegraphics{image.png}" + caption
            + (r"\label{" + label + "}" if label else "") + r"\end{" + env + "}")


def table(caption, label=""):
    return (r"\begin{table}" + caption + (r"\label{" + label + "}" if label else "")
            + r"\begin{tabular}{l}value\\\end{tabular}\end{table}")


def test_real_reader_uses_source_counter_formats_and_keeps_caption_objects(tmp_path):
    work(tmp_path)
    source = (r"\documentclass{book}\begin{document}\chapter{One}\chapter{Two}"
        r"\setcounter{figure}{1}\begin{figure}"
        r"\renewcommand{\thefigure}{\arabic{chapter}.\arabic{figure}b}"
        r"\includegraphics{image.png}\caption[Short]{Budget $x$ subset}\label{fig:b}\end{figure}"
        + figure(r"\caption*{Starred}", "star")
        + figure(r"\caption*[optional {x]y}]{Starred short}", "star-short")
        + figure(r"\caption{}", "empty") + figure("", "no-caption")
        + figure(r"\caption[]{Next}", "next")
        + figure(r"\caption{图 2.4 已有同号}", "existing", env="figure*")
        + figure(r"\caption{图 \thefigure 宏号}", "macro")
        + r"\setcounter{chapter}{4}\chapter{Five}\setcounter{table}{0}"
          r"\renewcommand{\thetable}{\arabic{chapter}.\arabic{table}a}"
        + table(r"\caption{First producer}", "table-a")
        + r"\renewcommand{\thetable}{\arabic{chapter}.\arabic{table}}"
        + table(r"\caption*{No table number}") + table(r"\caption[]{Table next}", "table-next")
        + r"See \ref{fig:b}, \ref{next}, \ref{existing}, \ref{macro}, \ref{table-a}, \ref{table-next}."
          r"\appendix\chapter{Appendix}\renewcommand{\thetable}{A.\arabic{table}}" + figure(r"\caption{Appendix figure}", "appendix")
        + table(r"\caption{Appendix table}", "appendix-table")
        + r"See \ref{appendix}, \ref{appendix-table}.\end{document}")
    doc, expanded = read_latex(tmp_path, source)
    figures = {n["c"][0][0]: n for n in nodes(doc, "Figure")}
    tables = {n["c"][0][0]: n for n in nodes(doc, "Table")}
    caption = lambda n: text(n["c"][1][1])
    assert caption(figures["fig:b"]) == "图 2.2b Budget x subset"
    assert text(figures["fig:b"]["c"][1][0]) == "Short"
    assert [caption(figures[key]) for key in ("star", "star-short", "empty", "")] == [
        "Starred", "Starred short", "", ""
    ]
    assert figures["star"]["c"][1][0] is None and figures["star-short"]["c"][1][0] is None
    assert figures["next"]["c"][1][0] == []
    assert caption(figures["next"]) == "图 2.3 Next"
    assert caption(figures["existing"]) == "图 2.4 已有同号"
    assert caption(figures["macro"]) == "图 2.5宏号"
    assert caption(tables["table-a"]) == "表 5.1a First producer"
    assert caption(tables["table-next"]) == "表 5.2 Table next"
    assert caption(figures["appendix"]) == "图 A.1 Appendix figure"
    assert caption(tables["appendix-table"]) == "表 A.1 Appendix table"
    refs = {n["c"][2][0]: text(n["c"][1]) for n in nodes(doc, "Link")}
    assert refs == {"#fig:b": "2.2b", "#next": "2.3", "#existing": "2.4",
                    "#macro": "2.5", "#table-a": "5.1a", "#table-next": "5.2",
                    "#appendix": "A.1", "#appendix-table": "A.1"}
    assert r"\caption[\epubcaptionunnumbered]" in expanded
    assert "epubcaptionunnumbered" not in json.dumps(doc, ensure_ascii=False)
    html = translator.run(["pandoc", "--from=json", "--to=html5"], input_text=json.dumps(doc)).stdout
    assert "图 2.2b Budget" in html and "表 5.1a First producer" in html
    assert "epubcaptionunnumbered" not in html


@pytest.mark.parametrize("language,figure_prefix,table_prefix", [
    ("en", "Figure 1.1", "Table 1.1"), ("zh-CN", "图 1.1", "表 1.1")
])
def test_language_and_unlabelled_counter_with_default_contrast(tmp_path, language, figure_prefix, table_prefix):
    work(tmp_path)
    source = (r"\begin{document}\chapter{One}"
              + figure(r"\caption{Unlabelled}") + table(r"\caption{Values}")
              + r"\end{document}")
    old, _ = read_latex(tmp_path, source, enabled=False, language="fr")
    new, _ = read_latex(tmp_path, source, enabled=True, language=language)
    old_fig, = nodes(old, "Figure"); new_fig, = nodes(new, "Figure")
    old_tab, = nodes(old, "Table"); new_tab, = nodes(new, "Table")
    assert text(old_fig["c"][1][1]) == "Unlabelled"
    assert text(old_tab["c"][1][1]) == "Values"
    assert text(new_fig["c"][1][1]) == figure_prefix + " Unlabelled"
    assert text(new_tab["c"][1][1]) == table_prefix + " Values"
    with pytest.raises(translator.CliError, match="不支持语言"):
        read_latex(tmp_path, source, enabled=True, language="fr")


def test_existing_prefixes_and_counter_macros_are_not_duplicated(tmp_path):
    work(tmp_path)
    source = (r"\begin{document}\chapter{One}"
        r"\renewcommand{\thefigure}{\arabic{chapter}.\arabic{figure}(\alph{figure})}"
        + figure(r"\caption{Figure 1.1(a): Same number}", "a")
        + figure(r"\caption{图 \thefigure Existing macro}", "b")
        + figure(r"\caption{\thefigure}", "c")
        + table(r"\caption{表 \thetable Existing table macro}", "t")
        + r"See \ref{a}, \ref{b}, \ref{c}, \ref{t}.\end{document}")
    doc, _ = read_latex(tmp_path, source)
    captions = {n["c"][0][0]: text(n["c"][1][1])
                for kind in ("Figure", "Table") for n in nodes(doc, kind)}
    assert captions == {"a": "Figure 1.1(a): Same number", "b": "图 1.2(b)Existing macro",
                        "c": "图 1.3(c) 1.3(c)", "t": "表 1.1Existing table macro"}
    refs = {n["c"][2][0]: text(n["c"][1]) for n in nodes(doc, "Link")}
    assert refs == {"#a": "1.1(a)", "#b": "1.2(b)", "#c": "1.3(c)", "#t": "1.1"}


def test_real_short_caption_and_label_inside_long_caption(tmp_path):
    work(tmp_path)
    source = (r"\begin{document}\chapter{One}\begin{figure}\includegraphics{image.png}"
              r"\caption[Short \emph{label}]{Full \textbf{caption}\label{fig:inside}}\end{figure}"
              r"See \ref{fig:inside}.\end{document}")
    doc, _ = read_latex(tmp_path, source)
    node, = nodes(doc, "Figure")
    assert any(span["c"][0][0] == "fig:inside" for span in nodes(node, "Span"))
    assert text(node["c"][1][0]) == "Short label"
    assert text(node["c"][1][1]) == "图 1.1 Full caption"
    assert list(nodes(node["c"][1][0], "Emph")) and list(nodes(node["c"][1][1], "Strong"))
    ref, = nodes(doc, "Link")
    assert ref["c"][2][0] == "#fig:inside" and text(ref["c"][1]) == "1.1"


def test_roman_and_parenthesized_existing_numbers_and_conflicts(tmp_path):
    work(tmp_path)
    prefix = r"\begin{document}\chapter{One}\renewcommand{\thefigure}{\Roman{figure}}"
    source = (prefix + figure(r"\caption{Figure I Same}", "one")
              + figure(r"\caption{图 (II) Same}", "two") + r"\end{document}")
    doc, _ = read_latex(tmp_path, source)
    captions = {n["c"][0][0]: text(n["c"][1][1]) for n in nodes(doc, "Figure")}
    assert captions == {"one": "Figure I Same", "two": "图 (II) Same"}
    with pytest.raises(translator.CliError, match="已有不同编号"):
        read_latex(tmp_path, prefix + figure(r"\caption{图 IV Wrong}") + r"\end{document}")


@pytest.mark.parametrize("written", ["图 1.9", "表 1.1", "Figure 1.9", "图 1.1a", "图 1.1-2"])
def test_explicit_wrong_number_or_kind_is_rejected(tmp_path, written):
    work(tmp_path)
    source = r"\begin{document}\chapter{One}" + figure(r"\caption{" + written + " Text}") + r"\end{document}"
    with pytest.raises(translator.CliError, match="已有不同编号"):
        read_latex(tmp_path, source)


def test_star_rewrite_skips_literal_code_and_comments():
    source = (r"\verb|\caption*{literal}| \verb*+\caption*{literal}+" + "\n"
              + "\\begin{verbatim}\n% literal\n\\caption*{literal}\n\\end{verbatim}\n"
              + "\\begin{Verbatim}\n\\caption*{literal}\n\\end{Verbatim}\n"
              + "\\begin{lstlisting}\n\\caption*{literal}\n\\end{lstlisting}\n"
              + "\\begin{minted}{tex}\n\\caption*{literal}\n\\end{minted}\n"
              + "% \\caption*{comment}\n"
              + r"\\caption*{not a command} \caption[short]{ordinary} \caption[]{empty short}"
              + "\n" + r"\caption*[{a]b}]{real} \caption*{real two}")
    result = translator.normalize_latex_native_captions(source)
    assert result.count(r"\caption[\epubcaptionunnumbered]") == 2
    assert result.startswith(source[:source.index(r"\caption*[{a]b}")])
    assert result.endswith(r"\caption[\epubcaptionunnumbered]{real} \caption[\epubcaptionunnumbered]{real two}")
    with pytest.raises(translator.CliError, match="保留标记"):
        translator.normalize_latex_native_captions(r"\caption[\epubcaptionunnumbered]{forged}")


def test_real_code_nodes_and_pdf_input_are_unchanged(tmp_path):
    work(tmp_path)
    source = (r"\documentclass{book}\begin{document}\chapter{One}" + "\n"
              + "\\begin{verbatim}\n\\caption*{verbatim}\n\\end{verbatim}\n"
              + r"Inline \verb|\caption*{inline}|." + "\n"
              + figure(r"\caption*{Unnumbered}") + r"\end{document}")
    doc, _ = read_latex(tmp_path, source)
    assert [n["c"][1] for n in nodes(doc, "CodeBlock")] == [r"\caption*{verbatim}"]
    assert [n["c"][1] for n in nodes(doc, "Code")] == [r"\caption*{inline}"]
    assert translator.expand_latex(tmp_path / "main.tex", tmp_path) == source


@pytest.mark.parametrize("env", ["figure", "figure*", "table", "table*"])
@pytest.mark.parametrize("command", ["setcounter", "addtocounter", "stepcounter", "refstepcounter"])
def test_unreconstructable_counter_inside_float_fails_only_in_opt_in(tmp_path, env, command):
    work(tmp_path)
    args = "{figure}{4}" if command in {"setcounter", "addtocounter"} else "{figure}"
    source = (r"\begin{document}\chapter{One}\begin{" + env + "}\\" + command + args
              + r"\caption{Caption}\label{float:object}\end{" + env + r"}\end{document}")
    entry = tmp_path / "main.tex"
    entry.write_text(source, encoding="utf-8")
    assert translator.latex_epub_source_text(entry, tmp_path) == source
    with pytest.raises(translator.CliError, match=r"浮动体内.*" + command + r".*float:object"):
        translator.latex_epub_source_text(entry, tmp_path, native_caption_numbers=True)


@pytest.mark.parametrize("kind", ["figure", "table"])
def test_local_format_after_caption_is_rejected_but_outside_is_allowed(tmp_path, kind):
    work(tmp_path)
    source = (r"\begin{document}\chapter{One}\begin{" + kind + r"}\caption{Caption}\label{late}"
              + r"\renewcommand{\the" + kind + r"}{\arabic{" + kind + r"}b}"
              + r"\end{" + kind + r"}\end{document}")
    entry = tmp_path / "main.tex"
    entry.write_text(source, encoding="utf-8")
    assert r"\epubcounterformat" in translator.latex_epub_source_text(entry, tmp_path)
    with pytest.raises(translator.CliError, match=r"caption 后定义.*the" + kind + r".*late"):
        translator.latex_epub_source_text(entry, tmp_path, native_caption_numbers=True)
    allowed = (r"\begin{document}\chapter{One}" + (figure(r"\caption{First}") if kind == "figure"
               else table(r"\caption{First}")) + r"\renewcommand{\the" + kind
               + r"}{\arabic{chapter}.\arabic{" + kind + r"}b}"
               + (figure(r"\caption{Second}", "second") if kind == "figure"
                  else table(r"\caption{Second}", "second")) + r"\end{document}")
    doc, _ = read_latex(tmp_path, allowed)
    node = next(n for n in nodes(doc, kind.title()) if n["c"][0][0] == "second")
    assert text(node["c"][1][1]).startswith(("图" if kind == "figure" else "表") + " 1.2b")


def test_local_table_format_is_scoped_and_regular_or_star_short_is_preserved(tmp_path):
    work(tmp_path)
    prefix = r"\renewcommand{\thetable}{\arabic{chapter}.\arabic{table}"
    body = lambda definition, caption, label: (r"\begin{table}" + prefix + definition + "}"
        + caption + r"\label{" + label + r"}\begin{tabular}{l}value\\\end{tabular}\end{table}")
    source = (r"\begin{document}\chapter{One}" + prefix + "a}"
        + body("b", r"\caption[Short]{Local}", "local")
        + body("z", r"\caption*[Short ignored]{Star}", "star")
        + table(r"\caption[]{After}", "after") + r"See \ref{local}, \ref{after}.\end{document}")
    doc, _ = read_latex(tmp_path, source)
    tables = {n["c"][0][0]: n for n in nodes(doc, "Table")}
    assert text(tables["local"]["c"][1][1]) == "表 1.1b Local"
    assert text(tables["local"]["c"][1][0]) == "Short"
    assert text(tables["star"]["c"][1][1]) == "Star" and tables["star"]["c"][1][0] is None
    assert text(tables["after"]["c"][1][1]) == "表 1.2a After"
    refs = {n["c"][2][0]: text(n["c"][1]) for n in nodes(doc, "Link")}
    assert refs == {"#local": "1.1b", "#after": "1.2a"}


def test_float_command_detection_ignores_comments_and_literal_code():
    source = (r"\begin{figure}\caption{Value}" + "\n% \\setcounter{figure}{7}\n"
              + r"\verb|\setcounter{figure}{7}|" + "\n"
              + "\\begin{verbatim}\n\\addtocounter{figure}{7}\n\\end{verbatim}\n"
              + r"\end{figure}")
    assert translator.normalize_latex_native_captions(source) == source


def test_reader_command_metadata_and_manifest_preflight_forwarding(tmp_path, monkeypatch):
    work(tmp_path)
    entry = tmp_path / "main.tex"
    entry.write_text(r"\begin{document}" + figure(r"\caption*{Plain}") + r"\end{document}", encoding="utf-8")
    language = translator.Language("zh-CN", "source", entry, None, {"epub": tmp_path / "out.epub"})
    book = translator.Work(ROOT, tmp_path, {"epub": {"native_caption_numbers": True}},
                           "id", "title", "latex", None, tmp_path / "source.pdf", 1, (language,))
    def fake_run(command, **kwargs):
        assert "--metadata=translator-latex-caption-figure:图" in command
        assert "--metadata=translator-latex-caption-table:表" in command
        assert r"\caption[\epubcaptionunnumbered]" in Path(command[1]).read_text(encoding="utf-8")
    monkeypatch.setattr(translator, "run", fake_run)
    translator.preflight_latex_epub(book, language)


def test_manifest_validation_and_real_work_build_use_the_same_opt_in(tmp_path, monkeypatch):
    root = tmp_path
    book_path = root / "Works/book"
    book_path.mkdir(parents=True)
    (root / "Books").mkdir()
    (root / "Books/source.pdf").write_bytes(b"source")
    (book_path / "image.png").write_bytes(PNG)
    (book_path / "main.tex").write_text(
        r"\begin{document}\chapter{One}" + figure(r"\caption*{Unnumbered}")
        + figure(r"\caption{Numbered}", "target") + r"See \ref{target}.\end{document}",
        encoding="utf-8",
    )
    manifest = ("schema_version = 2\n[work]\nid = 'book'\ntitle = 'Book'\nstatus = 'active'\n"
        "[source]\npath_base = 'repository_root'\nrelative_path = 'Books/source.pdf'\n"
        "sha256 = '" + "0" * 64 + "'\n[pdf]\npages = 1\n[authoring]\nformat = 'latex'\n"
        "[epub]\ntitle = 'Book'\ncover = 'image.png'\n"
        "native_caption_numbers = VALUE\n[[languages]]\ncode = 'en'\nrole = 'source'\n"
        "entry = 'main.tex'\noutputs = { epub = 'output/book.epub' }\n")
    manifest_path = book_path / "manifest.toml"
    manifest_path.write_text(manifest.replace("VALUE", '"yes"'), encoding="utf-8")
    with pytest.raises(translator.CliError, match="native_caption_numbers 必须是布尔值"):
        translator.load_work(book_path, root)
    manifest_path.write_text(manifest.replace("VALUE", "true"), encoding="utf-8")
    book = translator.load_work(book_path, root)
    book = translator.Work(ROOT, book.path, book.manifest, book.work_id, book.title, book.authoring_format,
                           book.pdf_engine, book.source, book.source_pages, book.languages)
    language = book.languages[0]
    monkeypatch.setattr(translator, "temp_root", lambda *args, **kwargs: tmp_path)
    translator.preflight_latex_epub(book, language)
    output = tmp_path / "book.epub"
    translator.build_latex(book, language, "epub", output)
    with zipfile.ZipFile(output) as archive:
        xhtml = "\n".join(archive.read(n).decode("utf-8") for n in archive.namelist()
                          if n.endswith(".xhtml"))
    assert "Unnumbered" in xhtml and "Figure 1.1 Numbered" in xhtml
    assert "#target\">1.1</a>" in xhtml
    assert "epubcaptionunnumbered" not in xhtml


def test_fallback_types_packed_unchanged_with_explicit_alt(tmp_path, monkeypatch):
    work(tmp_path)
    entry = tmp_path / "main.tex"
    entry.write_text(r"\documentclass{book}\begin{document}\chapter{Images}"
        + "".join(r"\epubfallback{" + name + "}{" + alt + "}{print branch}\\par\n"
                  for name, alt in [("image.svg", "Vector alt"), ("image.png", "PNG alt"),
                                    ("image.jpg", "JPEG alt"), ("image.jpeg", "JPEG long alt")])
        + r"\end{document}", encoding="utf-8")
    monkeypatch.setattr(translator, "temp_root", lambda *args, **kwargs: tmp_path)
    output = tmp_path / "out.epub"
    translator.build_latex_epub_source(
        root=ROOT, work_path=tmp_path, entry=entry, output=output, title="Images", author=None,
        language="en", identifier="urn:test:caption-resource", cover=tmp_path / "image.png",
        native_caption_numbers=True,
    )
    with zipfile.ZipFile(output) as archive:
        documents = [(name, ET.fromstring(archive.read(name))) for name in archive.namelist()
                     if name.endswith(".xhtml") and not name.endswith("nav.xhtml")]
        seen = {}
        for name, document in documents:
            for image in document.iter("{http://www.w3.org/1999/xhtml}img"):
                if image.get("alt") in {"Vector alt", "PNG alt", "JPEG alt", "JPEG long alt"}:
                    member = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(image.get("src"))))
                    seen[image.get("alt")] = (member, archive.read(member))
        assert {k: v[1] for k, v in seen.items()} == {
            "Vector alt": SVG, "PNG alt": PNG, "JPEG alt": JPEG, "JPEG long alt": JPEG
        }
        assert seen["Vector alt"][0].endswith(".svg")
        assert seen["PNG alt"][0].endswith(".png")
        assert seen["JPEG alt"][0].endswith(".jpg")
        # Pandoc may deduplicate byte-identical .jpg/.jpeg resources under either suffix.
        assert seen["JPEG long alt"][0].endswith((".jpg", ".jpeg"))


@pytest.mark.parametrize("fallback", [
    r"\epubfallback{\asset}{valid}{print}",
    r"\epubfallback{../outside.png}{valid}{print}",
    r"\epubfallback{image.png}{}{print}",
    r"\epubfallback{image.jpeg}{\emph{not plain}}{print}",
    r"\epubfallback{image.gif}{valid}{print}",
])
def test_fallback_rejects_unsafe_paths_and_alt(tmp_path, fallback):
    work(tmp_path)
    (tmp_path.parent / "outside.png").write_bytes(PNG)
    (tmp_path / "image.gif").write_bytes(b"GIF89a")
    entry = tmp_path / "main.tex"
    entry.write_text(fallback, encoding="utf-8")
    with pytest.raises(translator.CliError, match="LaTeX EPUB 回退"):
        translator.latex_epub_source_text(entry, tmp_path)


def test_native_source_link_is_body_and_legacy_bare_caption_groups_stay_strict():
    reader = ["pandoc", "--from=markdown", "--to=json"]
    def parse(source):
        return json.loads(translator.run(reader, input_text=source).stdout)
    # Native Figure 15.3 is complete; the following sentence refers to another figure.
    complete = parse("![图 15.3：已有说明](image.png){#fig-153}\n\n[图 15.4](#fig-154) 显示亏格 2。\n\n![图 15.4：目标](other.png){#fig-154}\n")
    assert not translator.pandoc_numbered_image_problems(complete)
    # Bare groups still require both an actual fig- ID and their source number in alt.
    bad = parse("[![plain alt](image.png)]{#not-a-figure}\n\n图 15.4 图注。\n")
    assert translator.pandoc_numbered_image_problems(bad)
    missing_number = parse("[![plain alt](image.png){#fig-154}]{.source-image}\n\n图 15.4 图注。\n")
    missing_id = parse("[![图 15.4：说明](image.png)]{.source-image}\n\n图 15.4 图注。\n")
    assert any("原图号" in p for p in translator.pandoc_numbered_image_problems(missing_number))
    assert any("标识" in p for p in translator.pandoc_numbered_image_problems(missing_id))
    good = parse("[![图 15.4：说明](image.png){#fig-154}]{.source-image}\n\n图 15.4 图注。\n")
    assert not translator.pandoc_numbered_image_problems(good)


def test_caption_mode_leaves_verbatim_star_and_footnote_counter_untouched():
    code = "\\begin{verbatim*}\n\\caption*{literal}\n\\end{verbatim*}\n"
    assert translator.normalize_latex_native_captions(code) == code
    table = r"\begin{table}\stepcounter{footnote}\caption{Caption}\end{table}"
    assert translator.normalize_latex_native_captions(table) == table
