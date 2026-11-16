from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools import translator

ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / "formats/pandoc/latex.lua"
pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None, reason="Pandoc required"
)

SOURCE = r"""
\documentclass{book}
\usepackage{amsmath}
\begin{document}
\chapter{Example}
\begin{equation}a=1.\label{eq:before}\end{equation}
Forward: \eqref{eq:inside}, \eqref{eq:nested}, \eqref{eq:after}.
\begin{description}
\item[\textbf{Problem 1}\label{term:first}] Definition paragraph.
\begin{equation}f+x.\label{eq:inside}\end{equation}
\begin{description}
\item[Inner term] Inner paragraph.
\begin{equation}b=2.\label{eq:nested}\end{equation}
\end{description}
\item[Problem 2] Second definition.
\end{description}
\begin{equation}c=3.\label{eq:after}\end{equation}
Backward: \eqref{eq:before}, \eqref{eq:inside}, \eqref{eq:nested},
\eqref{eq:after}, \ref{term:first}.
\end{document}
"""


def read(source: str, *, reader: str = "latex+raw_tex") -> dict:
    result = translator.run(
        [
            "pandoc",
            "--from=" + reader,
            "--to=json",
            "--lua-filter=" + str(FILTER),
        ],
        input_text=source,
    )
    return json.loads(result.stdout)


def nodes(value):
    if isinstance(value, dict):
        if "t" in value:
            yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def text(inlines):
    return "".join(
        x["c"] if x["t"] == "Str" else " "
        for x in inlines if x["t"] in ("Str", "Space", "SoftBreak")
    )


def test_description_labels_keep_structure_and_global_counter_order():
    doc = read(SOURCE)
    definitions = [n for n in nodes(doc) if n["t"] == "DefinitionList"]
    assert len(definitions) == 2
    assert [len(n["c"]) for n in definitions] == [2, 1]
    assert text(definitions[0]["c"][1][0]) == "Problem 2"
    assert [
        n["c"][1] for n in nodes(doc) if n["t"] == "Math"
    ] == [
        r"\begin{equation}" + "\n" + body + "\n\n"
        + rf"\tag{{1.{number}}}" + r"\end{equation}"
        for number, body in enumerate(("a=1.", "f+x.", "b=2.", "c=3."), 1)
    ]
    assert any(
        n["t"] == "Strong" and text(n["c"]) == "Problem 1"
        for n in nodes(definitions[0]["c"][0][0])
    )
    spans = [n["c"][0][0] for n in nodes(doc) if n["t"] == "Span"]
    for label in ("eq:before", "eq:inside", "eq:nested", "eq:after", "term:first"):
        assert spans.count(label) == 1
    links = [n for n in nodes(doc) if n["t"] == "Link"]
    expected = {
        "#eq:before": "(1.1)",
        "#eq:inside": "(1.2)",
        "#eq:nested": "(1.3)",
        "#eq:after": "(1.4)",
    }
    for href, display in expected.items():
        actual = [text(n["c"][1]) for n in links if n["c"][2][0] == href]
        assert actual and all(x == display for x in actual)
    assert any(n["c"][2][0] == "#term:first" for n in links)


def test_real_tagged_equation_inside_description_retains_original_tag():
    source = r"""
\begin{description}
\item[Problem 1] The terms include
\begin{equation}f+x.\tag{0.6}\label{eq:fixture-06}\end{equation}
\end{description}
See equation \eqref{eq:fixture-06}.
"""
    doc = read(source)
    assert any(n["t"] == "DefinitionList" for n in nodes(doc))
    link = next(n for n in nodes(doc) if n["t"] == "Link")
    assert text(link["c"][1]) == "(0.6)"
    assert link["c"][2][0] == "#eq:fixture-06"


@pytest.mark.parametrize("body_kind", ["Para", "Plain"])
def test_each_definition_body_is_scanned_in_source_order(body_kind):
    # Pandoc DefinitionList permits several Blocks lists for one term.
    source = {
        "pandoc-api-version": json.loads(
            translator.run(["pandoc", "--from=markdown", "--to=json"],
                           input_text="").stdout
        )["pandoc-api-version"],
        "meta": {},
        "blocks": [{
            "t": "DefinitionList",
            "c": [[
                [{"t": "Str", "c": "Term"}],
                [
                    [{"t": body_kind, "c": [{"t": "Math", "c": [
                        {"t": "DisplayMath"}, r"x=1\label{eq:first}"
                    ]}]}],
                    [{"t": body_kind, "c": [{"t": "Math", "c": [
                        {"t": "DisplayMath"}, r"y=2\label{eq:second}"
                    ]}]}],
                ],
            ]],
        }, {
            "t": "Para", "c": [
                {"t": "RawInline", "c": ["latex", r"\eqref{eq:first}"]},
                {"t": "Space"},
                {"t": "RawInline", "c": ["latex", r"\eqref{eq:second}"]},
            ],
        }],
    }
    doc = read(json.dumps(source), reader="json")
    assert len(doc["blocks"][0]["c"][0][1]) == 2
    assert all(body[0]["t"] == body_kind
               for body in doc["blocks"][0]["c"][0][1])
    anchors = [n["c"][0][0] for n in nodes(doc) if n["t"] == "Span"]
    assert anchors.count("eq:first") == anchors.count("eq:second") == 1
    assert [text(n["c"][1]) for n in nodes(doc) if n["t"] == "Link"] == [
        "(1)", "(2)"
    ]


def test_missing_label_still_rejected():
    with pytest.raises(translator.CliError, match="eq:absent"):
        read(r"\begin{description}\item[Term] Body.\end{description}"
             r"Missing \eqref{eq:absent}.")
