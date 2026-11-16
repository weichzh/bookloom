from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tools import epub_browser_qa as qa
from tools import translator

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".tmp" / "tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)


def make_epub(
    path: Path, members: tuple[str, ...] = ("EPUB/b.xhtml", "EPUB/a.xhtml")
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for member in members:
            archive.writestr(
                member,
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>Text</body></html>',
            )


def fake_mode(calls):
    def run(epub, unpacked, members, evidence, digest, namespace, settings, **kwargs):
        calls.append(
            (
                list(members),
                list(kwargs["available_members"]),
                kwargs["screenshot_policy"],
            )
        )
        assert all(
            (unpacked / member).is_file() for member in kwargs["available_members"]
        )
        result = {
            "mode": settings["name"],
            "settings": settings,
            "sha256": digest,
            "actual_settings": {},
            "viewport": settings["viewport"],
            "xhtml_count": len(members),
            "formula_count": 0,
            "code_block_count": 0,
            "xhtml_link_count": 0,
            "fragment_link_count": 0,
            "noteref_target_checks": 0,
            "backlink_target_checks": 0,
            "noteref_navigation_checks": 0,
            "backlink_navigation_checks": 0,
            "failures": [],
            "events": {
                "session": settings["name"],
                "launch_timeout_recovered": False,
                "poll_timeouts": 0,
                "viewport_command_eof": False,
                "close_returncode": 0,
            },
            "results": [{"path": "../unpacked/" + member} for member in members],
        }
        return result

    return run


class ScopedBrowserTests(unittest.TestCase):
    def test_selection_order_full_default_and_per_mode_coverage(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            epub = root / "book.epub"
            make_epub(epub, ("EPUB/c.xhtml", "EPUB/a.xhtml", "EPUB/b.xhtml"))
            available = ["EPUB/a.xhtml", "EPUB/b.xhtml", "EPUB/c.xhtml"]
            for index, selection in enumerate(
                (None, ["EPUB/c.xhtml", "EPUB/a.xhtml"], list(reversed(available)))
            ):
                calls = []
                with mock.patch.object(qa, "run_mode", side_effect=fake_mode(calls)):
                    payload = qa.run_browser_qa(
                        epub, root / f"evidence-{index}", selected_members=selection
                    )
                expected = (
                    available
                    if selection is None
                    else [name for name in available if name in selection]
                )
                coverage = {
                    "kind": "full" if selection is None else "selected",
                    "available_members": available,
                    "selected_members": expected,
                    "unselected_members": [
                        name for name in available if name not in expected
                    ],
                    "checked_members": expected,
                    "unchecked_members": [
                        name for name in available if name not in expected
                    ],
                }
                self.assertEqual(payload["coverage"], coverage)
                self.assertEqual(
                    json.loads(
                        (root / f"evidence-{index}" / "run.json").read_text(
                            encoding="utf-8"
                        )
                    )["coverage"],
                    coverage,
                )
                self.assertEqual(payload["xhtml_count"], len(expected))
                self.assertEqual(payload["total_xhtml_checks"], 3 * len(expected))
                self.assertEqual(calls, [(expected, available, "failures")] * 3)
                for mode in payload["modes"]:
                    self.assertEqual(mode["xhtml_count"], len(expected))
                    self.assertEqual(mode["checked_members"], expected)

    def test_selection_is_distinct_from_failed_execution_coverage(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            epub = root / "book.epub"
            evidence = root / "evidence"
            make_epub(epub, ("EPUB/c.xhtml", "EPUB/b.xhtml", "EPUB/a.xhtml"))
            fake = fake_mode([])

            def failed_first_page(*args, **kwargs):
                result = fake(*args, **kwargs)
                result["results"] = result["results"][1:]
                result["xhtml_count"] = 1
                result["failures"] = ["EPUB/a.xhtml: load failed"]
                return result

            with (
                mock.patch.object(qa, "run_mode", side_effect=failed_first_page),
                self.assertRaises(qa.BrowserQAError),
            ):
                qa.run_browser_qa(
                    epub,
                    evidence,
                    selected_members=["EPUB/b.xhtml", "EPUB/a.xhtml"],
                )
            expected = {
                "kind": "selected",
                "available_members": ["EPUB/a.xhtml", "EPUB/b.xhtml", "EPUB/c.xhtml"],
                "selected_members": ["EPUB/a.xhtml", "EPUB/b.xhtml"],
                "unselected_members": ["EPUB/c.xhtml"],
                "checked_members": ["EPUB/b.xhtml"],
                "unchecked_members": ["EPUB/a.xhtml", "EPUB/c.xhtml"],
            }
            for name in ("run.json", "results.json"):
                data = json.loads((evidence / name).read_text(encoding="utf-8"))
                self.assertEqual(data["coverage"], expected)

    def test_invalid_and_escaping_selections_never_run_modes(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            epub = root / "book.epub"
            make_epub(epub)
            for index, selection in enumerate(
                (
                    [],
                    [""],
                    ["EPUB/a.xhtml", "EPUB/a.xhtml"],
                    ["EPUB/missing.xhtml"],
                    ["../a.xhtml"],
                    ["EPUB/../a.xhtml"],
                    ["/EPUB/a.xhtml"],
                    [r"EPUB\a.xhtml"],
                )
            ):
                with (
                    self.subTest(selection=selection),
                    mock.patch.object(qa, "run_mode") as mode,
                ):
                    with self.assertRaises(qa.BrowserQAError):
                        qa.run_browser_qa(
                            epub, root / str(index), selected_members=selection
                        )
                    mode.assert_not_called()
            invalid = root / "escape.epub"
            make_epub(invalid, ("../escape.xhtml",))
            with self.assertRaises(qa.BrowserQAError):
                qa.run_browser_qa(invalid, root / "escape")

    def _run_browser_fixture(
        self,
        root,
        mutate=None,
        *,
        policy="failures",
        screenshots=6,
        screenshot_failure=False,
    ):
        import test_epub_browser_qa as old_tests

        epub = root / "book.epub"
        fixture = old_tests.BrowserQAMatrixTests()
        fixture._write_epub(epub)
        calls = []
        fake = fixture._fake_runner(calls)

        def runner(namespace, session, arguments, **kwargs):
            if screenshot_failure and arguments[:1] == ["screenshot"]:
                raise qa.BrowserQAError("diagnostic screenshot failed")
            result = fake(namespace, session, arguments, **kwargs)
            if arguments == ["eval", "qaState"] and mutate:
                state = json.loads(result.stdout)
                mutate(state)
                result.stdout = json.dumps(state)
            return result

        evidence = root / "evidence"
        with mock.patch.object(qa, "run_agent", side_effect=runner):
            try:
                qa.run_browser_qa(
                    epub,
                    evidence,
                    timeout=1,
                    page_timeout=1,
                    screenshots=screenshots,
                    screenshot_policy=policy,
                    mode=qa.PUBLISHER_MODE,
                )
            except qa.BrowserQAError:
                pass
        result = json.loads((evidence / "results.json").read_text(encoding="utf-8"))
        paths = [
            json.loads(call[1][len("qaShowPage(") : -1])
            for call in calls
            if call[:1] == ("eval",) and call[1].startswith("qaShowPage(")
        ]
        return result, paths, calls

    def test_default_screenshots_only_item_level_failures(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            result, paths, _ = self._run_browser_fixture(Path(temp))
            self.assertEqual(result["failures"], [])
            self.assertEqual(paths, [])
        for field, value, expected_message in (
            ("brokenImages", 1, "brokenImages"),
            ("overflowCount", 1, "overflowCount"),
            ("missingFragmentTargets", 1, "missingFragmentTargets"),
            ("noterefNavigationFailures", 1, "noterefNavigationFailures"),
            ("readerSettings", None, "readerSettings"),
            ("codeRecords", None, "codeRecords"),
        ):
            with (
                self.subTest(field=field),
                tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp,
            ):
                result, paths, _ = self._run_browser_fixture(
                    Path(temp),
                    lambda state, field=field, value=value: state["results"][
                        1
                    ].__setitem__(field, value),
                )
                self.assertTrue(
                    any(expected_message in message for message in result["failures"])
                )
                self.assertEqual(paths, ["../unpacked/EPUB/b.xhtml"])

    def test_missing_page_is_not_replaced_by_screenshot_of_a_passing_page(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            result, paths, _ = self._run_browser_fixture(
                Path(temp), lambda state: state["results"].pop()
            )
            self.assertTrue(
                any("未检查 XHTML" in failure for failure in result["failures"])
            )
            self.assertEqual(paths, [])
            self.assertEqual(result["xhtml_count"], 1)
            self.assertEqual(result["total_xhtml_checks"], 1)
            self.assertEqual(result["coverage"]["unchecked_members"], ["EPUB/b.xhtml"])

    def test_representative_and_zero_screenshot_policies(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            result, paths, _ = self._run_browser_fixture(
                Path(temp), policy="representative"
            )
            self.assertEqual(result["failures"], [])
            self.assertTrue(paths)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            result, paths, _ = self._run_browser_fixture(
                Path(temp),
                lambda state: state["results"][1].__setitem__("brokenImages", 1),
                screenshots=0,
            )
            self.assertTrue(result["failures"])
            self.assertEqual(paths, [])
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            result, paths, _ = self._run_browser_fixture(
                Path(temp),
                lambda state: state["results"][1].__setitem__("brokenImages", 1),
                screenshot_failure=True,
            )
            self.assertTrue(
                any("brokenImages" in error for error in result["failures"])
            )
            self.assertTrue(any("截图失败" in error for error in result["failures"]))

    def test_screenshot_limit_above_six_remains_supported(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            epub = root / "book.epub"
            make_epub(epub)
            observed = []
            fake = fake_mode([])

            def run(*args, **kwargs):
                observed.append(kwargs["screenshots"])
                return fake(*args, **kwargs)

            with mock.patch.object(qa, "run_mode", side_effect=run):
                qa.run_browser_qa(epub, root / "evidence", screenshots=9)
            self.assertEqual(observed, [9, 9, 9])

    def test_receipt_requires_actual_complete_epub_and_accepts_legacy(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            epub = root / "book.epub"
            evidence = root / "full"
            make_epub(epub)
            with mock.patch.object(qa, "run_mode", side_effect=fake_mode([])):
                payload = qa.run_browser_qa(epub, evidence)
            self.assertEqual(
                translator.browser_acceptance_receipt(evidence, epub)["status"],
                "passed",
            )
            legacy = copy.deepcopy(payload)
            legacy.pop("coverage")
            legacy.pop("total_xhtml_checks")
            for mode in legacy["modes"]:
                mode.pop("checked_members")
            results = evidence / "results.json"
            results.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertEqual(
                translator.browser_acceptance_receipt(evidence, epub)["status"],
                "passed",
            )
            cases = []
            for field in ("selected_members", "unselected_members"):
                missing_field = copy.deepcopy(payload)
                missing_field["coverage"].pop(field)
                cases.append(missing_field)
                wrong_field = copy.deepcopy(payload)
                wrong_field["coverage"][field] = (
                    ["EPUB/a.xhtml"]
                    if field == "selected_members"
                    else ["EPUB/b.xhtml"]
                )
                cases.append(wrong_field)
            partial = copy.deepcopy(payload)
            partial["coverage"]["kind"] = (
                "selected"  # Even selecting every member is diagnostic.
            )
            cases.append(partial)
            with mock.patch.object(qa, "run_mode", side_effect=fake_mode([])):
                altered_count = qa.run_browser_qa(
                    epub, root / "partial", selected_members=["EPUB/a.xhtml"]
                )
            altered_count["xhtml_count"] = 2
            altered_count["total_xhtml_checks"] = 6
            for mode in altered_count["modes"]:
                mode["xhtml_count"] = 2
            cases.append(altered_count)
            fake_full = copy.deepcopy(payload)
            fake_full["coverage"]["checked_members"].pop()
            cases.append(fake_full)
            fake_mode_full = copy.deepcopy(payload)
            fake_mode_full["modes"][0]["checked_members"].pop()
            cases.append(fake_mode_full)
            undersized_legacy = copy.deepcopy(legacy)
            undersized_legacy["xhtml_count"] = 1
            for mode in undersized_legacy["modes"]:
                mode["xhtml_count"] = 1
            cases.append(undersized_legacy)
            for bad in cases:
                results.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(translator.CliError):
                    translator.browser_acceptance_receipt(evidence, epub)

    def test_command_forwarding_refresh_and_final_contract(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            epub = root / "book.epub"
            make_epub(epub)
            lang = types.SimpleNamespace(code="en")
            work = object()
            args = argparse.Namespace(
                work="work",
                lang="en",
                members=["EPUB/b.xhtml", "EPUB/a.xhtml"],
                width=390,
                height=844,
                timeout=1,
                page_timeout=1,
                screenshots=6,
                screenshot_policy="representative",
            )
            with (
                mock.patch.object(translator, "load_command_work", return_value=work),
                mock.patch.object(
                    translator, "select_jobs", return_value=[(lang, "epub", epub)]
                ),
                mock.patch.object(
                    translator, "reset_evidence_child", return_value=root / "evidence"
                ),
                mock.patch.object(
                    translator,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout="ok"),
                ) as run,
            ):
                translator.command_browser_qa(args)
            command = run.call_args.args[0]
            self.assertEqual(
                command[command.index("--screenshot-policy") + 1], "representative"
            )
            self.assertEqual(
                [
                    command[i + 1]
                    for i, item in enumerate(command)
                    if item == "--member"
                ],
                args.members,
            )
            args.members = []
            with self.assertRaises(translator.CliError):
                translator.command_browser_qa(args)

            for target in ("html", "pdf"):
                with (
                    self.subTest(target=target),
                    mock.patch.object(translator, "load_command_work") as load,
                ):
                    with self.assertRaises(translator.CliError):
                        translator.command_refresh(
                            argparse.Namespace(
                                work="work",
                                lang="en",
                                target=target,
                                members=["EPUB/a.xhtml"],
                            )
                        )
                    load.assert_not_called()
            refresh = argparse.Namespace(
                work="work", lang="en", target="epub", members=["EPUB/a.xhtml"]
            )
            with (
                mock.patch.object(translator, "load_command_work", return_value=work),
                mock.patch.object(
                    translator, "select_jobs", return_value=[(lang, "epub", epub)]
                ),
                mock.patch.object(translator, "reset_evidence_dir", return_value=root),
                mock.patch.object(translator, "clear_evidence_dir"),
                mock.patch.object(translator, "command_check", return_value=0),
                mock.patch.object(translator, "command_build", return_value=0),
                mock.patch.object(translator, "command_qa", return_value=0),
                mock.patch.object(
                    translator, "command_browser_qa", return_value=0
                ) as browser,
                mock.patch.object(
                    translator,
                    "write_refresh_summary",
                    return_value=root / "refresh.json",
                ),
            ):
                translator.command_refresh(refresh)
            forwarded = browser.call_args.args[0]
            self.assertEqual(forwarded.members, refresh.members)
            self.assertEqual(
                getattr(forwarded, "screenshot_policy", "failures"), "failures"
            )
            finalize = argparse.Namespace(work="work", members=["EPUB/a.xhtml"])
            with (
                mock.patch.object(translator, "load_command_work", return_value=work),
                mock.patch.object(
                    translator, "select_jobs", return_value=[(lang, "epub", epub)]
                ),
                mock.patch.object(translator, "reset_evidence_dir", return_value=root),
                mock.patch.object(translator, "clear_evidence_dir"),
                mock.patch.object(translator, "command_doctor", return_value=0),
                mock.patch.object(translator, "command_check", return_value=0),
                mock.patch.object(translator, "command_build", return_value=0),
                mock.patch.object(translator, "command_qa", return_value=0),
                mock.patch.object(
                    translator, "command_browser_qa", return_value=0
                ) as browser,
                mock.patch.object(
                    translator,
                    "write_finalize_summary",
                    return_value=root / "final.json",
                ),
            ):
                translator.command_finalize(finalize)
            self.assertFalse(hasattr(browser.call_args.args[0], "members"))

    def test_cli_parser_accepts_repeated_members(self):
        parsed = translator.parser().parse_args(
            [
                "browser-qa",
                "work",
                "--activity",
                "none",
                "--member",
                "EPUB/b.xhtml",
                "--member",
                "EPUB/a.xhtml",
                "--screenshot-policy",
                "representative",
            ]
        )
        self.assertEqual(parsed.members, ["EPUB/b.xhtml", "EPUB/a.xhtml"])
        self.assertEqual(parsed.screenshot_policy, "representative")
        parsed = translator.parser().parse_args(
            [
                "refresh",
                "work",
                "--lang",
                "en",
                "--target",
                "epub",
                "--activity",
                "none",
                "--member",
                "EPUB/a.xhtml",
            ]
        )
        self.assertEqual(parsed.members, ["EPUB/a.xhtml"])
        result = {"modes": [], "mode_count": 0, "xhtml_count": 0, "sha256": "test"}
        with mock.patch.object(qa, "run_browser_qa", return_value=result) as run:
            self.assertEqual(
                qa.main(
                    [
                        "book.epub",
                        "--evidence",
                        "evidence",
                        "--member",
                        "EPUB/b.xhtml",
                        "--member",
                        "EPUB/a.xhtml",
                        "--screenshot-policy",
                        "representative",
                    ]
                ),
                0,
            )
        self.assertEqual(
            run.call_args.kwargs["selected_members"], ["EPUB/b.xhtml", "EPUB/a.xhtml"]
        )
        self.assertEqual(run.call_args.kwargs["screenshot_policy"], "representative")

    @unittest.skipUnless(
        os.environ.get("TRANSLATOR_TEST_REAL_BROWSER"), "real browser is opt-in"
    )
    def test_real_browser_selected_crossfile_links_and_noteref(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            for missing in (False, True):
                epub = root / f"book-{missing}.epub"
                with zipfile.ZipFile(epub, "w") as archive:
                    archive.writestr(
                        "EPUB/a.xhtml",
                        """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>A</title></head><body><h1>Start</h1><p><a id="r1" epub:type="noteref" href="b.xhtml#n1">1</a><a href="b.xhtml#target">target</a></p></body></html>""",
                    )
                    archive.writestr(
                        "EPUB/b.xhtml",
                        """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>B</title></head><body><h1>Notes</h1>"""
                        + ("" if missing else '<p id="target">Target</p>')
                        + """<aside id="n1" epub:type="footnote"><a epub:type="backlink" href="a.xhtml#r1">back</a> Note</aside></body></html>""",
                    )
                evidence = root / f"evidence-{missing}"
                if missing:
                    with self.assertRaisesRegex(
                        qa.BrowserQAError, "missingFragmentTargets"
                    ):
                        qa.run_browser_qa(
                            epub,
                            evidence,
                            selected_members=["EPUB/a.xhtml"],
                            mode=qa.PUBLISHER_MODE,
                            screenshots=0,
                            timeout=90,
                            page_timeout=10,
                        )
                else:
                    result = qa.run_browser_qa(
                        epub,
                        evidence,
                        selected_members=["EPUB/a.xhtml"],
                        mode=qa.PUBLISHER_MODE,
                        screenshots=0,
                        timeout=90,
                        page_timeout=10,
                    )
                    self.assertEqual(
                        result["coverage"]["unchecked_members"], ["EPUB/b.xhtml"]
                    )
                    mode_result = json.loads(
                        (evidence / qa.PUBLISHER_MODE / "results.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    item = mode_result["results"][0]
                    self.assertEqual(item["noterefNavigationChecks"], 1)
                    self.assertEqual(item["backlinkNavigationChecks"], 1)
                    self.assertEqual(item["missingXhtmlTargets"], 0)
                    self.assertEqual(item["missingFragmentTargets"], 0)


if __name__ == "__main__":
    unittest.main()
