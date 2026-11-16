"""Exercise source-native blackboard indicators through the real EPUB adapter."""
from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import translator

ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}"
XHTML = "{http://www.w3.org/1999/xhtml}"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII="
)


def run_hidden(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(TEMP=str(cwd), TMP=str(cwd), UV_FROZEN="true")
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=True,
        timeout=180,
    )


def single_painted_glyph(svg: bytes) -> str:
    """Return the actual outline of the one used glyph, ignoring position/IDs."""
    root = ET.fromstring(svg)
    paths = {node.get("id"): node.get("d") for node in root.iter(SVG + "path")}
    used = [node.get(XLINK + "href", "")[1:] for node in root.iter(SVG + "use")]
    if used:  # Independent native dvisvgm reference.
        assert len(used) == 1 and used[0] in paths, (used, list(paths))
        outlines = [paths[used[0]]]
    else:  # Actual drawn paths, never unused definitions.
        foreground = root.find(SVG + "g[@class='math-foreground']")
        assert foreground is not None
        outlines = [node.get("d") for node in foreground.iter(SVG + "path")]
    assert len(outlines) == 1
    outline = outlines[0]
    assert outline and len(outline) > 20
    return outline


def standard_latex_glyph(directory: Path, name: str, formula: str) -> str:
    """Generate an independent standard LaTeX-bbm/dvisvgm reference."""
    tex = directory / f"{name}.tex"
    tex.write_text(
        "\\documentclass[12pt]{article}\n"
        "\\usepackage{bbm}\n"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n$" + formula + "$\n\\end{document}\n",
        encoding="utf-8",
    )
    run_hidden(["latex", "-interaction=nonstopmode", "-halt-on-error", tex.name], directory)
    run_hidden(["dvisvgm", "--no-fonts", "--bbox=min", "-o", f"{name}.svg", f"{name}.dvi"], directory)
    return single_painted_glyph((directory / f"{name}.svg").read_bytes())


@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("pandoc", "uv", "latex", "dvisvgm")),
    reason="Pandoc, uv, LaTeX and dvisvgm are required",
)
def test_bbm_native_reference_adapter_and_cache(tmp_path: Path, monkeypatch, capsys):
    """Both output modes draw bbm, not a plain digit, and reuse their SVG cache."""
    monkeypatch.setattr(translator, "temp_root", lambda *args, **kwargs: tmp_path)
    entry = tmp_path / "main.md"
    source = (
        "---\nlang: en\ntitle: Indicator fixture\n---\n"
        "# Indicator {#body}\n\n"
        "Inline $\\mathbbm{1}$ and ordinary $1$. "
        "Identity $\\mathbbm{I}$ and ordinary $I$.\n\n"
        "$$\\mathbbm{1}$$\n\n"
        "$$1$$\n"
    )
    entry.write_text(source, encoding="utf-8")
    (tmp_path / "cover.png").write_bytes(PNG)
    output = tmp_path / "book.epub"
    language = translator.Language("en", "source", entry, None, {"epub": output})
    work = translator.Work(
        ROOT, tmp_path,
        {"epub": {"title": "Indicator fixture", "cover": "cover.png", "title_page": False,
                  "toc_depth": 1, "split_level": 1, "bodymatter": "body"}},
        "bbm-fixture", "Indicator fixture", "markdown", None,
        tmp_path / "source.pdf", 1, (language,),
    )

    reference_bbm = standard_latex_glyph(tmp_path, "reference-bbm", r"\mathbbm{1}")
    reference_plain = standard_latex_glyph(tmp_path, "reference-plain", "1")
    reference_bbm_i = standard_latex_glyph(tmp_path, "reference-bbm-I", r"\mathbbm{I}")
    reference_plain_i = standard_latex_glyph(tmp_path, "reference-plain-I", "I")
    assert reference_bbm != reference_plain, "The reference must reject an ordinary 1"
    assert reference_bbm_i != reference_plain_i, "The reference must reject an ordinary I"

    translator.build_markdown(work, language, "epub", output)
    first = capsys.readouterr().out
    assert re.search(r"svg_math_cache_hits=0\s+svg_math_cache_misses=6", first), first
    translator.build_markdown(work, language, "epub", output)
    second = capsys.readouterr().out
    assert re.search(r"svg_math_cache_hits=6\s+svg_math_cache_misses=0", second), second
    assert entry.read_text(encoding="utf-8") == source

    evidence = tmp_path / "qa"
    evidence.mkdir()
    translator.qa_epub(work, language, output, evidence)
    found = {}
    with zipfile.ZipFile(output) as archive:
        for member in archive.namelist():
            if not member.endswith(".xhtml"):
                continue
            document = ET.fromstring(archive.read(member))
            for image in document.iter(XHTML + "img"):
                classes = set(image.get("class", "").split())
                mode = next((value for value in ("math-inline", "math-display") if value in classes), None)
                if mode is None:
                    continue
                alt = image.get("alt", "")
                assert (mode, alt) not in found
                svg_member = posixpath.normpath(posixpath.join(posixpath.dirname(member), image.get("src", "")))
                assert not list(ET.fromstring(archive.read(svg_member)).iter(SVG + "use"))
                found[(mode, alt)] = single_painted_glyph(archive.read(svg_member))

    expected = {(mode, alt) for mode in ("math-inline", "math-display") for alt in (r"\mathbbm{1}", "1")}
    expected |= {("math-inline", r"\mathbbm{I}"), ("math-inline", "I")}
    assert set(found) == expected
    for mode in ("math-inline", "math-display"):
        assert found[(mode, r"\mathbbm{1}")] == reference_bbm
        assert found[(mode, "1")] == reference_plain
        assert found[(mode, r"\mathbbm{1}")] != found[(mode, "1")]
    assert found[("math-inline", r"\mathbbm{I}")] == reference_bbm_i
    assert found[("math-inline", "I")] == reference_plain_i
    assert found[("math-inline", r"\mathbbm{I}")] != found[("math-inline", "I")]

    (tmp_path / "bbm-evidence.json").write_text(json.dumps({
        "source": str(entry), "epub": str(output), "reference_bbm": str(tmp_path / "reference-bbm.svg"),
        "reference_plain": str(tmp_path / "reference-plain.svg"),
        "reference_geometry_matches": ["math-inline", "math-display"],
        "ordinary_one_geometry_rejected": True, "bbm_identity_reference_matches": True,
        "ordinary_i_geometry_rejected": True, "first_build": first, "cached_build": second,
        "epubcheck_evidence": str(evidence),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
