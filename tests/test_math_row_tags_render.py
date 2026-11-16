"""Actual SVG row-label regression through the pinned GladTeX filter."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path
from unittest import mock

from tools import gladtex_filter, translator
from test_svg_math_paths import drawing_records

ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / "tools/gladtex_filter.py"
TEMP = ROOT / ".tmp" / "translator" / "math-row-tags-tests"
TEMP.mkdir(parents=True, exist_ok=True)
FIXTURES = [
    ("two", r"\begin{align}x&=1\tag{1.1}\\y&=2\tag{1.2}\end{align}", ["1.1", "1.2"]),
    ("notag", r"\begin{align}a&=1\tag{2.1}\\b&=2\notag\\c&=3\tag{2.2}\end{align}", ["2.1", "2.2"]),
    ("infty", r"\begin{align}p&=1\tag{$\infty.1$}\\q&=2\tag{$\infty.2$}\end{align}", [r"$\infty.1$", r"$\infty.2$"]),
    ("epsilon", r"\begin{align}r&=1\tag{$\varepsilon.1$}\\s&=2\tag{$\varepsilon.2$}\end{align}", [r"$\varepsilon.1$", r"$\varepsilon.2$"]),
    ("star", r"\begin{align}u&=1\tag*{A}\\v&=2\tag*{B}\end{align}", ["A", "B"]),
    ("aligned", r"\begin{aligned}u&=1\tag{8.13}\\v&=2\tag{8.14}\end{aligned}", ["8.13", "8.14"]),
    ("trailing-comment", "\\begin{align}m&=1\\tag{3.1}\\\\n&=2\\tag{3.2},%\n\\end{align}", ["3.1", "3.2"]),
    ("synthetic-ratios", r"""\begin{align}
\tag{7.41}
u_1&\equiv \left(\frac{\beta_3}{\beta_1}\right)+\frac{5}{6}\left(\frac{\beta_2}{\beta_1}\right)^2\ ,\\
\tag{7.42}
v_1&\equiv \left(\frac{\beta_3}{\beta_1}\right)+\left(\frac{\beta_2}{\beta_1}\right)^2\, ,%
\end{align}""", ["7.41", "7.42"]),
    ("synthetic-nested", r"""\begin{align}
\tag{C.51}

\psi_{\perp}(t)&=1 + (1-\eta^2) \left[3c_1t+O\!\left(t^2\right)\right]\, ,\\
\tag{C.52}

\left[\left\langle\rho^6\right\rangle_{t^{\diamond}}-\left\langle\rho^3\right\rangle_{t^{\diamond}}^2\right]&= \rho_1^6 \left[ 4 t^2+O\!\left(t^3\right)\right]\, ,
\end{align}""", ["C.51", "C.52"]),
]

def nodes(v):
    if isinstance(v, dict):
        yield v
        for x in v.values(): yield from nodes(x)
    elif isinstance(v, list):
        for x in v: yield from nodes(x)

def expected_codes(value, star=False):
    if value.startswith(r"$\infty."):
        body = [49, 58] + [ord(c) for c in value[len(r"$\infty."):-1]]
    elif value.startswith(r"$\varepsilon."):
        body = [34, 58] + [ord(c) for c in value[len(r"$\varepsilon."):-1]]
    else:
        body = [ord(c) for c in value]
    return body if star else [40, *body, 41]

def svg_uses(path):
    root = ET.parse(path).getroot()
    vb = [float(x) for x in root.get("viewBox").split()]
    uses = []
    for n in root.iter("{http://www.w3.org/2000/svg}use"):
        m = re.fullmatch(r"#g\d+-(\d+)", n.get("{http://www.w3.org/1999/xlink}href", ""))
        if m: uses.append((int(m.group(1)), float(n.get("x", "0")), float(n.get("y", "0"))))
    return vb, uses

def tag_rows(vb, uses, signature):
    result = []
    for i in range(len(uses)-len(signature)+1):
        g = uses[i:i+len(signature)]
        if [x[0] for x in g] != signature: continue
        if max(x[2] for x in g) - min(x[2] for x in g) > .02: continue
        if any(a[1] >= b[1] for a, b in pairwise(g)): continue
        if vb[0] + vb[2] - g[-1][1] > 20: continue
        result.append(g[0][2])
    return result

def native_reference_svg(directory, name, formula, env):
    """Render the same display independently with the native TeX toolchain."""
    reference = directory / (name + "-native")
    reference.mkdir()
    # A blank line is not legal inside native AMS math. The source fixture
    # deliberately includes one, so remove it only in the independent input.
    native_formula = re.sub(r"\n(?:[ \t]*\n)+", "\n", formula)
    document = (
        "\\documentclass[fontsize=12pt,fleqn]{scrartcl}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage{amsmath,amssymb}\n"
        "\\usepackage[active,textmath,displaymath,tightpage]{preview}\n"
        "\\begin{document}\n\\noindent\\begin{preview}\n"
        + native_formula + "\n\\end{preview}\\end{document}\n"
    )
    (reference / "native.tex").write_text(document, encoding="utf-8")
    for command in (
        ["latex", "-interaction=nonstopmode", "-halt-on-error", "native.tex"],
        ["dvisvgm", "--no-fonts", "--exact-bbox", "-o", "native.svg", "native.dvi"],
    ):
        result = subprocess.run(
            command, cwd=reference, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
    return reference / "native.svg"

def glyph_records(path):
    root = ET.parse(path).getroot()
    namespace = "{http://www.w3.org/2000/svg}"
    href = "{http://www.w3.org/1999/xlink}href"
    paths = {node.get("id"): node.get("d") for node in root.iter(namespace + "path")
             if node.get("id")}
    records = []
    for node in root.iter(namespace + "use"):
        ref = node.get(href, "").removeprefix("#")
        match = re.fullmatch(r"g\d+-(\d+)", ref)
        if match:
            assert paths.get(ref)
            records.append((int(match.group(1)), paths[ref], float(node.get("x", "0")),
                            float(node.get("y", "0"))))
    assert records
    return records

def rule_records(path):
    root = ET.parse(path).getroot()
    return [tuple(float(node.get(key, "0")) for key in ("x", "y", "width", "height"))
            for node in root.iter("{http://www.w3.org/2000/svg}rect")]

@unittest.skipUnless(all(shutil.which(x) for x in ("uv", "latex", "dvisvgm")), "GladTeX toolchain required")
class RowTagsActualSvg(unittest.TestCase):
    def assert_native_glyphs_and_positions(self, actual_path, reference_path, tags):
        actual, native = glyph_records(actual_path), glyph_records(reference_path)
        # Compare complete font outlines and order, not merely tag counts.
        self.assertEqual([glyph[1] for glyph in actual], [glyph[1] for glyph in native])
        anchors = {}
        for tag in tags:
            signature = expected_codes(tag)
            matches = [index for index in range(len(native) - len(signature) + 1)
                       if [item[0] for item in native[index:index + len(signature)]] == signature]
            self.assertEqual(len(matches), 1, (tag, matches))
            for index in range(matches[0], matches[0] + len(signature)):
                anchors[index] = matches[0]
        for index, (rendered, original) in enumerate(zip(actual, native)):
            # Native TeX right-aligns labels to a wider page; compare each
            # label relative to its own first glyph, and math relative to its
            # first glyph. All glyph baselines must share one vertical offset.
            anchor = anchors.get(index, 0)
            self.assertAlmostEqual(rendered[2] - actual[anchor][2],
                                   original[2] - native[anchor][2], delta=0.01)
            self.assertAlmostEqual(rendered[3] - actual[0][3],
                                   original[3] - native[0][3], delta=0.01)
        # Fraction bars are separate SVG rectangles, not glyph outlines.
        rendered_rules, native_rules = rule_records(actual_path), rule_records(reference_path)
        self.assertEqual(len(rendered_rules), len(native_rules))
        for rendered, original in zip(rendered_rules, native_rules):
            self.assertAlmostEqual(rendered[0] - actual[0][2],
                                   original[0] - native[0][2], delta=0.01)
            self.assertAlmostEqual(rendered[1] - actual[0][3],
                                   original[1] - native[0][3], delta=0.01)
            self.assertAlmostEqual(rendered[2], original[2], delta=0.01)
            self.assertAlmostEqual(rendered[3], original[3], delta=0.01)

    def test_row_tags_alt_and_nonumber_at_bounded_workers(self):
        with tempfile.TemporaryDirectory(prefix="row-tags-", dir=TEMP) as td:
            cwd = Path(td)
            payload = {"pandoc-api-version": [1, 23, 1, 2], "meta": {}, "blocks": [
                {"t": "Para", "c": [{"t": "Math", "c": [{"t": "DisplayMath"}, f]}]} for _, f, _ in FIXTURES]}
            (cwd / "input.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            runner = "import multiprocessing,runpy,sys\nfrom pathlib import Path\nmultiprocessing.cpu_count=lambda:1\nsys.argv=[sys.argv[1],'input.json','output.json','.']\nsys.path.insert(0,str(Path(sys.argv[0]).resolve().parent))\nrunpy.run_path(sys.argv[0],run_name='__main__')\n"
            (cwd / "runner.py").write_text(runner, encoding="utf-8")
            env = os.environ.copy(); env.update(TEMP=str(cwd), TMP=str(cwd), UV_CACHE_DIR=str(ROOT / ".tmp/uv-cache"), PYTHONWARNINGS="ignore::RuntimeWarning")
            run = subprocess.run(["uv", "run", "--with", "gladtex==3.1.0", "python", "runner.py", str(FILTER)],
                cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=180, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
            output = json.loads((cwd / "output.json").read_text(encoding="utf-8"))
            self.assertEqual(len(output["blocks"]), len(FIXTURES))
            cached = subprocess.run(["uv", "run", "--with", "gladtex==3.1.0", "python", "runner.py", str(FILTER)],
                cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30, check=False)
            self.assertEqual(cached.returncode, 0, cached.stdout + cached.stderr)
            self.assertIn(f"svg_math_cache_hits={len(FIXTURES)}", cached.stdout)
            self.assertIn("svg_math_cache_misses=0", cached.stdout)
            position_geometry = []
            for (name, source, tags), block in zip(FIXTURES, output["blocks"]):
                with self.subTest(name=name):
                    images = [n for n in nodes(block) if n.get("t") == "Image"]
                    self.assertEqual(len(images), 1)
                    image = images[0]; alt = "".join(n["c"] for n in nodes(image["c"][1]) if n.get("t") == "Str")
                    self.assertEqual(alt, translator.gladtex_alt_text(source).replace("\n", " "))
                    self.assertTrue(gladtex_filter.valid_svg(cwd / image["c"][2][0], require_row_tags=True))
                    for t in tags: self.assertIn(t, alt)
                    vb, uses = svg_uses(cwd / image["c"][2][0])
                    if name.startswith("position-"):
                        position_geometry.append((vb, uses))
                    rows = []
                    for t in tags:
                        hits = tag_rows(vb, uses, expected_codes(t, star=name == "star"))
                        self.assertEqual(len(hits), 1, (name, t, hits)); rows += hits
                    self.assertEqual(rows, sorted(set(rows)))
                    if name == "two": self.assertLess(vb[2], 150, "compact measured width")
                    if name == "notag":
                        b = [u[2] for u in uses if u[0] == ord("b")]
                        self.assertEqual(len(b), 1); self.assertLess(rows[0], b[0]); self.assertLess(b[0], rows[1])
                    # The SVG tag test must reject a changed label, not just count glyphs.
                    wrong = expected_codes(tags[0], star=name == "star").copy(); wrong[-1 if name == "star" else -2] = 57
                    self.assertEqual(tag_rows(vb, uses, wrong), [])
                    if name.startswith("synthetic-"):
                        reference = native_reference_svg(cwd, name, source, env)
                        self.assert_native_glyphs_and_positions(cwd / image["c"][2][0], reference, tags)
                    # Preserve the complete multirow glyph/rule sequence and
                    # every transform after reader-safe normalization as well.
                    path = cwd / image["c"][2][0]
                    original = ET.parse(path).getroot()
                    before = drawing_records(original)
                    translator.normalize_formula_svg(path)
                    normalized = ET.parse(path).getroot()
                    foreground = normalized.find("{http://www.w3.org/2000/svg}g[@class='math-foreground']")
                    self.assertIsNotNone(foreground)
                    self.assertEqual([record[:3] for record in drawing_records(foreground)],
                                     [record[:3] for record in before])
                    self.assertEqual(normalized.attrib, original.attrib)
                    self.assertFalse(list(normalized.iter("{http://www.w3.org/2000/svg}use")))
                    self.assertTrue(gladtex_filter.valid_svg(path, require_row_tags=True))

            if position_geometry:
                self.assertEqual(len(position_geometry), 3)
                self.assertTrue(all(x == position_geometry[0] for x in position_geometry))

    def test_optional_aligned_position_has_no_visible_glyphs(self):
        body = r"u&=1\tag{8.13}\\v&=2\tag{8.14}"
        fixtures = [("position-" + pos, r"\begin{aligned}" + ("[" + pos + "]" if pos else "") + body + r"\end{aligned}", ["8.13", "8.14"]) for pos in ("", "t", "b")]
        with mock.patch(__name__ + ".FIXTURES", fixtures):
            self.test_row_tags_alt_and_nonumber_at_bounded_workers()

if __name__ == "__main__":
    unittest.main()
