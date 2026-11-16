from __future__ import annotations

import base64
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import epub_semantics as semantics
from tools import translator

ROOT = Path(__file__).resolve().parents[1]
H = "{http://www.w3.org/1999/xhtml}"
TYPE = "{http://www.idpf.org/2007/ops}type"


def poem(*, marked=False, note=False):
    first = {"t": "Str", "c": "First"}
    if marked:
        first = {"t": "Span", "c": [["", ["source-highlight-red"], []], [first]]}
    line = [first]
    if note:
        line.append({"t": "Note", "c": [{"t": "Para", "c": [{"t": "Str", "c": "Original note."}]}]})
    return {"blocks": [{"t": "LineBlock", "c": [line, [{"t": "Str", "c": "Second"}]]}]}


def test_ordinary_poetry_note_is_not_a_teaching_context_but_new_color_is_rejected():
    expected = semantics.teaching_spans_from_ast(poem(note=True))
    assert expected == ()
    actual = ET.fromstring('<div class="line-block">First<a href="#note">1</a><br/>Second</div>')
    semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([actual]))
    actual = ET.fromstring('<div class="line-block"><span class="source-highlight-red">First</span>'
                           '<a href="#note">1</a><br/>Second</div>')
    with pytest.raises(semantics.EpubSemanticError, match="教学语义"):
        semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([actual]))


def test_marked_lineblock_stays_strict_and_legacy_title_note_contract_is_unchanged():
    expected = semantics.teaching_spans_from_ast(poem(marked=True))
    actual = ET.fromstring('<div class="line-block"><span class="source-highlight-red">First</span>'
                           '<br/>Second</div>')
    semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([actual]))
    actual.find("span").text = "Wrong"
    with pytest.raises(semantics.EpubSemanticError, match="教学语义"):
        semantics.validate_teaching_spans(expected, semantics.teaching_spans_from_xhtml([actual]))
    header = {"blocks": [{"t": "Header", "c": [1, ["title", [], []], poem(note=True)["blocks"][0]["c"][0]]}]}
    with pytest.raises(semantics.EpubSemanticError):
        semantics.outline_from_ast(header)


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
def test_native_poetry_note_passes_writer_static_qa_and_epubcheck(tmp_path):
    entry, cover, output = tmp_path / "main.md", tmp_path / "cover.png", tmp_path / "poem.epub"
    entry.write_text("# Poem {#poem}\n\n| First[^n]\n| Second\n\n[^n]: Original note.\n", encoding="utf-8")
    cover.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    language = translator.Language("en", "source", entry, None, {"epub": output})
    work = translator.Work(ROOT, tmp_path, {"epub": {"cover": "cover.png", "title_page": False,
                           "toc_depth": 1, "bodymatter": "poem"}}, "poetry-note", "Poem", "markdown",
                           None, tmp_path / "source.pdf", 1, (language,))
    source = json.loads(translator.run([
        "pandoc", "--from=markdown+fenced_divs+bracketed_spans+footnotes-raw_html", "--to=json", str(entry),
    ]).stdout)
    assert sum(node["t"] == "LineBlock" for node in semantics.ast_nodes(source)) == 1
    assert sum(node["t"] == "Note" for node in semantics.ast_nodes(source)) == 1
    translator.build_markdown(work, language, "epub", output)
    evidence = tmp_path / "qa"
    evidence.mkdir()
    translator.qa_epub(work, language, output, evidence)
    translator.run_epubcheck(output, evidence)
    with zipfile.ZipFile(output) as archive:
        docs = [ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith(".xhtml")]
    refs = [n for d in docs for n in d.iter(H + "a") if n.get(TYPE) == "noteref"]
    notes = [n for d in docs for n in d.iter(H + "aside") if n.get(TYPE) == "footnote"]
    assert len(refs) == len(notes) == 1
    assert refs[0].get("href").split("#")[-1] == notes[0].get("id")
    assert any(n.get("href", "").endswith("#" + refs[0].get("id")) for n in notes[0].iter(H + "a"))
