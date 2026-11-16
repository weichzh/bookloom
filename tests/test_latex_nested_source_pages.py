from __future__ import annotations

import shutil
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.browser_runtime import run_hidden

ROOT = Path(__file__).resolve().parents[1]
SCOOP_PANDOC = Path.home() / "scoop/apps/pandoc/current/pandoc.exe"
PANDOC = str(SCOOP_PANDOC) if SCOOP_PANDOC.is_file() else shutil.which("pandoc")
XHTML = "{http://www.w3.org/1999/xhtml}"


@unittest.skipUnless(PANDOC, "Pandoc is required")
class NestedSourcePageTests(unittest.TestCase):
    def render(self, source: str, *, success: bool = True) -> ET.Element | str:
        result = run_hidden(
            [PANDOC, "--from=latex+raw_tex", "--to=html", "--wrap=none", "--lua-filter=" + str(ROOT / "formats/pandoc/latex.lua")],
            input=source, capture_output=True, text=True, encoding="utf-8",
            cwd=ROOT, timeout=30, check=False,
        )
        if not success:
            self.assertNotEqual(result.returncode, 0)
            return result.stderr
        self.assertEqual(result.returncode, 0, result.stderr)
        return ET.fromstring('<body xmlns="http://www.w3.org/1999/xhtml">' + result.stdout + '</body>')

    def concepts(self, root: ET.Element) -> list[ET.Element]:
        return [node for node in root.iter() if "cn-concept-rendered" in node.get("class", "").split()]

    def markers(self, root: ET.Element) -> list[ET.Element]:
        return [node for node in root.iter() if "source-page" in node.get("class", "").split()]

    def test_nested_boundary_stays_inside_one_concept_at_exact_chinese_position(self) -> None:
        root = self.render(r"这里比较\cnconcept{离散色彩模型}{discrete color model}与\cnconcept{连续\sourcepage{12}色彩模型}{continuous color model}。")
        concepts = self.concepts(root)
        self.assertEqual(len(concepts), 2)
        point, = self.markers(root)
        self.assertEqual(point.get("data-page"), "12")
        self.assertEqual(concepts[1].text, "连续")
        self.assertTrue(point.tail.startswith("色彩模型"))
        self.assertEqual("".join(concepts[1].itertext()), "连续色彩模型 （continuous color model）")
        self.assertEqual(concepts[1].get("data-original"), "continuous color model")
        self.assertEqual(len(concepts[1].findall(f"{XHTML}em")), 1)

    def test_nested_emphasis_stays_continuous_around_the_pagebreak(self) -> None:
        root = self.render(r"\cnconcept{\emph{先\sourcepage{9}后}}{before after}")
        concept, = self.concepts(root)
        point, = self.markers(root)
        emphasis = concept.findall(f"{XHTML}em")
        self.assertEqual(len(emphasis), 2)
        self.assertIs(emphasis[0][0], point)
        self.assertEqual(emphasis[0].text, "先")
        self.assertEqual(point.tail, "后")
        self.assertEqual("".join(emphasis[0].itertext()), "先后")
        self.assertEqual("".join(emphasis[1].itertext()), "before after")

    def test_multiple_and_adjacent_markers_do_not_split_or_inject_text(self) -> None:
        root = self.render(r"前\cnconcept{甲\sourcepage{5}\sourcepage{6}乙\sourcepage{7}丙}{abc}后")
        concept, = self.concepts(root)
        self.assertEqual([node.get("data-page") for node in self.markers(root)], ["5", "6", "7"])
        self.assertEqual("".join(concept.itertext()), "甲乙丙 （abc）")
        self.assertEqual("".join(root.itertext()).strip(), "前甲乙丙 （abc）后")
        self.assertNotIn("sourcepage", "".join(root.itertext()))

    def test_source_language_argument_also_keeps_nested_semantics_and_plain_alt(self) -> None:
        root = self.render(r"\cnconcept{词}{\textbf{x\sourcepage{8}y}}")
        concept, = self.concepts(root)
        point, = self.markers(root)
        original = concept.find(f"{XHTML}em/{XHTML}strong")
        self.assertIs(original[0], point)
        self.assertEqual("".join(original.itertext()), "xy")
        self.assertEqual(concept.get("data-original"), "xy")

    def test_legacy_concept_without_page_markers_keeps_its_original_contract(self) -> None:
        root = self.render(r"前\cnconcept{\emph{中文}}{original term}后")
        concept, = self.concepts(root)
        self.assertFalse(self.markers(root))
        self.assertEqual("".join(root.itertext()).strip(), "前中文 （original term）后")
        self.assertEqual(concept.get("data-original"), "original term")
        self.assertEqual(["".join(node.itertext()) for node in concept.findall(f"{XHTML}em")], ["中文", "original term"])

    def test_malformed_nested_sourcepage_fails_instead_of_disappearing(self) -> None:
        for marker in (r"\sourcepage{bad}", r"\sourcepage{}", r"\sourcepage"):
            with self.subTest(marker=marker):
                error = self.render(r"\cnconcept{甲" + marker + r"乙}{term}", success=False)
                self.assertIn("sourcepage", error)


if __name__ == "__main__":
    unittest.main()
