from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "epub_browser_qa_matrix", ROOT / "tools" / "epub_browser_qa.py"
)
assert SPEC and SPEC.loader
epub_browser_qa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(epub_browser_qa)


class BrowserQAMatrixTests(unittest.TestCase):
    def test_default_matrix_has_three_real_reader_modes(self) -> None:
        modes = epub_browser_qa.default_modes()

        self.assertEqual(
            [item["name"] for item in modes],
            [
                epub_browser_qa.PUBLISHER_MODE,
                epub_browser_qa.READER_DARK_MODE,
                epub_browser_qa.READER_LARGE_PRINT_MODE,
            ],
        )
        self.assertEqual(
            [item["viewport"] for item in modes], [[390, 844], [1280, 900], [390, 844]]
        )
        self.assertEqual(modes[0]["color_scheme"], "light")
        self.assertEqual(modes[1]["color_scheme"], "dark")
        self.assertTrue(modes[2]["disable_publisher_css"])
        self.assertEqual(modes[2]["user_font_family"], "serif")
        self.assertEqual(modes[2]["user_font_size_px"], 32)
        self.assertEqual(
            epub_browser_qa.selected_modes(
                epub_browser_qa.READER_DARK_MODE, width=700, height=500
            )[0]["viewport"],
            [1280, 900],
        )

    def test_harness_contains_real_reader_override_without_hiding_overflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mode" / "harness.html"
            settings = epub_browser_qa.default_modes()[2]
            paths = epub_browser_qa.write_harness(
                path,
                ["EPUB/a.xhtml"],
                390,
                844,
                30,
                settings,
                base_prefix="../unpacked/",
            )
            text = path.read_text(encoding="utf-8")

        self.assertEqual(paths, ["../unpacked/EPUB/a.xhtml"])
        self.assertNotIn("__MODE_SETTINGS__", text)
        self.assertIn("publisherCssDisabled", text)
        self.assertIn("userFontSizeApplied", text)
        self.assertIn("overflow-wrap: anywhere", text)
        self.assertIn("link[rel~='stylesheet'], style", text)
        self.assertNotIn("overflow-x: hidden", text)

    def _write_epub(self, path: Path) -> None:
        xhtml = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<html xmlns='http://www.w3.org/1999/xhtml' "
            "xmlns:epub='http://www.idpf.org/2007/ops'>"
            "<head><link rel='stylesheet' href='../styles/style.css'/></head>"
            "<body><h1>Page</h1><p>Text</p>"
            "<a id='r1' epub:type='noteref' href='#n1'>1</a>"
            "<aside id='n1' epub:type='footnote'><a epub:type='backlink' href='#r1'>↩</a>Note</aside>"
            "</body></html>"
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
            )
            archive.writestr("EPUB/a.xhtml", xhtml)
            archive.writestr("EPUB/b.xhtml", xhtml)
            archive.writestr("EPUB/styles/style.css", "body { color: black; }")

    def _fake_runner(self, calls: list[tuple[str, ...]], *, eof: bool = False):
        sessions: dict[str, dict[str, object]] = {}
        pages = ["../unpacked/EPUB/a.xhtml", "../unpacked/EPUB/b.xhtml"]

        def completed(
            arguments: list[str],
            *,
            returncode: int = 0,
            stdout: str = "",
            stderr: str = "",
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                ["agent-browser", *arguments], returncode, stdout, stderr
            )

        def run_agent(
            namespace: str,
            session: str,
            arguments: list[str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            calls.append(tuple(arguments))
            state = sessions.setdefault(
                session, {"viewport": [390, 844], "media": None}
            )
            if arguments[-1:] == ["about:blank"]:
                return completed(arguments)
            if arguments[:2] == ["set", "viewport"]:
                state["viewport"] = [int(arguments[2]), int(arguments[3])]
                if eof:
                    return completed(
                        arguments,
                        returncode=1,
                        stderr=epub_browser_qa.KNOWN_VIEWPORT_EOF,
                    )
                return completed(arguments)
            if arguments[:2] == ["set", "media"]:
                state["media"] = arguments[2]
                return completed(arguments)
            if arguments[:1] == ["eval"]:
                expression = arguments[1]
                if expression == "location.href":
                    return completed(arguments, stdout=json.dumps("about:blank"))
                if expression == "({width:innerWidth,height:innerHeight})":
                    return completed(
                        arguments,
                        stdout=json.dumps(
                            {
                                "width": state["viewport"][0],
                                "height": state["viewport"][1],
                            }
                        ),
                    )
                if expression == "startQA()":
                    return completed(arguments, stdout="true")
                if expression.startswith("({done:"):
                    return completed(
                        arguments,
                        stdout=json.dumps({"done": True, "count": 2, "errors": 0}),
                    )
                if expression == "qaState":
                    mode = next(
                        name
                        for name in epub_browser_qa.DEFAULT_MODE_NAMES
                        if name in str(state.get("harness", ""))
                    )
                    dark = mode == epub_browser_qa.READER_DARK_MODE
                    large = mode == epub_browser_qa.READER_LARGE_PRINT_MODE
                    color_scheme = "dark" if dark else "light"
                    reader = {
                        "mode": mode,
                        "requestedColorScheme": color_scheme,
                        "actualColorScheme": state.get("media") or "no-preference",
                        "publisherCssDisabled": large,
                        "userFontApplied": large,
                        "userFontSizeApplied": large,
                        "reflowWrapApplied": large,
                    }
                    results = [
                        {
                            "path": path,
                            "url": f"file:///fake/{Path(path).name}",
                            "viewport": list(state["viewport"]),
                            "readerSettings": reader,
                            "formulaCount": 0,
                            "brokenImages": 0,
                            "formulasWithoutAlt": 0,
                            "mathmlCount": 0,
                            "texAnnotationCount": 0,
                            "overflowCount": 0,
                            "xhtmlLinkCount": 0,
                            "fragmentLinkCount": 0,
                            "noterefTargetChecks": 1,
                            "backlinkTargetChecks": 1,
                            "noterefNavigationChecks": 1,
                            "backlinkNavigationChecks": 1,
                            "noterefTargetFailures": 0,
                            "backlinkTargetFailures": 0,
                            "noterefNavigationFailures": 0,
                            "backlinkNavigationFailures": 0,
                        }
                        for path in pages
                    ]
                    return completed(
                        arguments, stdout=json.dumps({"errors": [], "results": results})
                    )
                if expression.startswith("qaShowPage("):
                    return completed(arguments, stdout=json.dumps({}))
            if arguments[:1] == ["open"] and "about:blank" not in arguments:
                state["harness"] = urlparse(arguments[1]).path
                return completed(arguments)
            if arguments[:1] == ["screenshot"]:
                target = Path(arguments[1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fake-png")
                return completed(arguments)
            if arguments[:1] == ["close"]:
                return completed(arguments)
            return completed(arguments)

        return run_agent

    def test_matrix_runs_every_mode_and_persists_digest_bound_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub = root / "book.epub"
            evidence = root / "browser-matrix"
            self._write_epub(epub)
            calls: list[tuple[str, ...]] = []
            with mock.patch.object(
                epub_browser_qa,
                "run_agent",
                side_effect=self._fake_runner(calls),
            ):
                payload = epub_browser_qa.run_browser_qa(
                    epub, evidence, timeout=1, page_timeout=1, screenshots=1
                )

            self.assertEqual(payload["mode_count"], 3)
            self.assertEqual(payload["total_xhtml_checks"], 6)
            self.assertEqual(len(payload["modes"]), 3)
            self.assertEqual(len(set(payload["sessions"])), 3)
            self.assertTrue(
                all(item["sha256"] == payload["sha256"] for item in payload["modes"])
            )
            self.assertTrue(all("results" not in item for item in payload["modes"]))
            self.assertEqual(len([call for call in calls if call[:1] == ("close",)]), 3)
            self.assertIn(("set", "media", "dark"), calls)
            self.assertIn(("set", "media", "light"), calls)
            for mode in epub_browser_qa.DEFAULT_MODE_NAMES:
                result = json.loads(
                    (evidence / mode / "results.json").read_text(encoding="utf-8")
                )
                self.assertEqual(result["sha256"], payload["sha256"])
                self.assertEqual(result["xhtml_count"], 2)
                self.assertTrue((evidence / mode / "screenshots").is_dir())
            aggregate = json.loads(
                (evidence / "results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(aggregate["sha256"], payload["sha256"])
            self.assertEqual(aggregate["failures"], [])

    def test_viewport_eof_is_recorded_only_when_dimensions_follow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub = root / "book.epub"
            evidence = root / "publisher"
            self._write_epub(epub)
            calls: list[tuple[str, ...]] = []
            with mock.patch.object(
                epub_browser_qa,
                "run_agent",
                side_effect=self._fake_runner(calls, eof=True),
            ):
                payload = epub_browser_qa.run_browser_qa(
                    epub,
                    evidence,
                    timeout=1,
                    page_timeout=1,
                    screenshots=0,
                    mode=epub_browser_qa.PUBLISHER_MODE,
                )

            self.assertTrue(payload["modes"][0]["events"]["viewport_command_eof"])
            self.assertEqual(payload["failures"], [])


if __name__ == "__main__":
    unittest.main()
