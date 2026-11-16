from __future__ import annotations

import base64
import posixpath
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import urldefrag

import pytest

from tools import translator

ROOT = Path(__file__).resolve().parents[1]
H = "{http://www.w3.org/1999/xhtml}"
T = "{http://www.idpf.org/2007/ops}type"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
)


def work_fixture(path: Path, *, format: str = "markdown", level: int | None = None):
    (path / "cover.png").write_bytes(PNG)
    entry = path / {"markdown": "main.md", "latex": "main.tex", "typst": "main.typ"}[format]
    entry.write_text("Fixture", encoding="utf-8")
    epub = {"cover": "cover.png", "title_page": False, "toc_depth": 1, "title": "Fixture"}
    if level is not None:
        epub["split_level"] = level
    language = translator.Language("en", "source", entry, None, {"epub": path / "book.epub"})
    return translator.Work(ROOT, path, {"epub": epub}, "split-fixture", "Fixture", format,
                           None, path / "source.pdf", 1, (language,))


@pytest.mark.parametrize("value", ["true", "false", "0", "7", '"1"', "1.0"])
def test_manifest_rejects_invalid_split_level(tmp_path, value):
    work_path = tmp_path / "Works" / "fixture"
    work_path.mkdir(parents=True)
    (tmp_path / "source.pdf").write_bytes(b"%PDF fixture")
    (work_path / "main.md").write_text("# Chapter\n", encoding="utf-8")
    (work_path / "cover.png").write_bytes(PNG)
    (work_path / "manifest.toml").write_text(
        'schema_version=2\n[work]\nid="fixture"\ntitle="Fixture"\nstatus="active"\n'
        '[source]\npath_base="repository_root"\nrelative_path="source.pdf"\n'
        + 'sha256="' + "0" * 64 + '"\n[pdf]\npages=1\n[authoring]\nformat="markdown"\n'
        '[epub]\ncover="cover.png"\nsplit_level=' + value + '\n'
        '[[languages]]\ncode="en"\nrole="source"\nentry="main.md"\n'
        'outputs={epub="output/book.epub"}\n', encoding="utf-8",
    )
    with pytest.raises(translator.CliError, match="epub.split_level"):
        translator.load_work(work_path, root=tmp_path)


def test_optional_value_keeps_adapter_defaults_and_accepts_bounds():
    for default in (1, 2):
        assert translator.epub_split_level(None, default=default) == default
        for level in (1, 6):
            assert translator.epub_split_level(level, default=default) == level


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
@pytest.mark.parametrize("level", [None, 1], ids=["legacy-two", "whole-chapter"])
def test_real_markdown_chapter_continuity_is_independent_of_toc_depth(tmp_path, level):
    work = work_fixture(tmp_path, level=level)
    language = work.languages[0]
    language.entry.write_text(
        "# Chapter {#chapter}\n\nDOI: fixture.\n\n"
        "## First section {#first}\n\nFirst paragraph.[^note] [Next](#next)\n\n"
        "## Second section {#second}\n\nLast paragraph.\n\n"
        "# Next chapter {#next}\n\n[Back](#first)\n\n[^note]: Complete note.\n",
        encoding="utf-8",
    )
    translator.build_markdown(work, language, "epub", language.outputs["epub"])
    with zipfile.ZipFile(language.outputs["epub"]) as archive:
        docs = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                if name.endswith(".xhtml")}
    ids = {e.get("id"): (name, e) for name, doc in docs.items() for e in doc.iter() if e.get("id")}
    assert (ids["chapter"][0] == ids["first"][0] == ids["second"][0]) is (level == 1)
    assert ids["next"][0] != ids["second"][0]
    assert ids["first"][1].find(H + "h2") is not None
    toc = next(e for doc in docs.values() for e in doc.iter(H + "nav") if e.get(T) == "toc")
    assert ["".join(a.itertext()) for a in toc.iter(H + "a")] == ["Chapter", "Next chapter"]
    full_text = " ".join(" ".join(doc.itertext()) for doc in docs.values())
    for text in ("DOI: fixture.", "First paragraph.", "Last paragraph.", "Complete note."):
        assert text in full_text
    for name, doc in docs.items():
        for a in doc.iter(H + "a"):
            href = a.get("href", "")
            if "#" not in href:
                continue
            member, fragment = urldefrag(href)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), member)) if member else name
            assert ids[fragment][0] == target


@pytest.mark.parametrize("level", [None, 2], ids=["legacy-one", "explicit-two"])
def test_typst_writer_receives_one_effective_split_option(tmp_path, level):
    work = work_fixture(tmp_path, format="typst", level=level)
    options = []

    def fake_run(command, **kwargs):
        Path(command[-1]).write_text("<html><body><h1>Chapter</h1><p>Body</p></body></html>", encoding="utf-8")

    def fake_build(**kwargs):
        options.extend(kwargs["writer_options"])
        kwargs["output"].write_bytes(b"fixture")

    with mock.patch.object(translator, "run", side_effect=fake_run), mock.patch.object(
        translator, "build_svg_math_epub", side_effect=fake_build
    ):
        translator.build_typst(work, work.languages[0], "epub", tmp_path / "out.epub")
    assert [o for o in options if o.startswith("--split-level=")] == [f"--split-level={level or 1}"]


@pytest.mark.parametrize("parts,level,expected", [(False, None, 1), (True, None, 2), (True, 1, 1)])
def test_latex_direct_api_preserves_legacy_defaults_and_accepts_override(tmp_path, parts, level, expected):
    work = work_fixture(tmp_path, format="latex")
    entry = work.languages[0].entry
    entry.write_text(r"\begin{document}" + (r"\part{Part}" if parts else "")
                     + r"\chapter{Chapter}Text.\end{document}", encoding="utf-8")
    options = []

    def fake_build(**kwargs):
        options.extend(kwargs["writer_options"])
        kwargs["output"].write_bytes(b"fixture")

    with mock.patch.object(translator, "build_svg_math_epub", side_effect=fake_build):
        translator.build_latex_epub_source(
            root=ROOT, work_path=tmp_path, entry=entry, output=tmp_path / "out.epub",
            title="Fixture", author=None, language="en", identifier="urn:test:split",
            cover=tmp_path / "cover.png", split_level=level,
        )
    assert [o for o in options if o.startswith("--split-level=")] == [f"--split-level={expected}"]


def test_latex_manifest_adapter_passes_override_to_direct_api(tmp_path):
    work = work_fixture(tmp_path, format="latex", level=1)
    with mock.patch.object(translator, "build_latex_epub_source") as build:
        translator.build_latex(work, work.languages[0], "epub", tmp_path / "out.epub")
    assert build.call_args.kwargs["split_level"] == 1
