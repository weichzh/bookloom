from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from tools import translator


def run_epub_finalize(args: argparse.Namespace, tmp_path: Path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"epub fixture")
    language = SimpleNamespace(code="en")
    jobs = [(language, "epub", epub)]
    work = object()
    result = subprocess.CompletedProcess([], 0, stdout="ok")
    forwarded = {}
    command_browser_qa = translator.command_browser_qa

    def browser_qa(browser_args):
        forwarded.update(vars(browser_args))
        return command_browser_qa(browser_args)

    with (
        mock.patch.object(translator, "load_command_work", return_value=work),
        mock.patch.object(translator, "select_jobs", return_value=jobs),
        mock.patch.object(translator, "clear_evidence_dir"),
        mock.patch.object(translator, "reset_evidence_dir", return_value=tmp_path),
        mock.patch.object(
            translator, "reset_evidence_child", return_value=tmp_path / "browser"
        ),
        mock.patch.object(translator, "command_doctor", return_value=0),
        mock.patch.object(translator, "command_check", return_value=0),
        mock.patch.object(translator, "command_build", return_value=0),
        mock.patch.object(translator, "command_qa", return_value=0),
        mock.patch.object(
            translator, "command_browser_qa", side_effect=browser_qa
        ) as browser,
        mock.patch.object(translator, "run", return_value=result) as run,
        mock.patch.object(
            translator, "write_finalize_summary", return_value=tmp_path / "final.json"
        ),
    ):
        assert translator.command_finalize(args) == 0
    browser.assert_called_once()
    return forwarded, run.call_args.args[0], epub


@pytest.mark.parametrize("timeout", [None, 1, 1800])
@pytest.mark.parametrize("page_timeout", [None, 1, 120])
def test_finalize_cli_forwards_finite_browser_budgets(timeout, page_timeout, tmp_path):
    argv = ["finalize", "Works/sample", "--activity", "none"]
    if timeout is not None:
        argv.extend(["--browser-timeout", str(timeout)])
    if page_timeout is not None:
        argv.extend(["--browser-page-timeout", str(page_timeout)])
    args = translator.parser().parse_args(argv)
    original_defaults = dict(translator.BROWSER_QA_DEFAULTS)
    assert original_defaults == {
        "width": 390,
        "height": 844,
        "timeout": 600,
        "page_timeout": 30,
        "screenshots": 6,
    }
    forwarded, command, epub = run_epub_finalize(args, tmp_path)
    expected_timeout = 600 if timeout is None else timeout
    expected_page_timeout = 30 if page_timeout is None else page_timeout
    assert forwarded == {
        "work": "Works/sample",
        "lang": "all",
        **original_defaults,
        "timeout": expected_timeout,
        "page_timeout": expected_page_timeout,
    }
    assert translator.BROWSER_QA_DEFAULTS == original_defaults
    assert command == [
        sys.executable,
        str(translator.REPO_ROOT / "tools" / "epub_browser_qa.py"),
        str(epub),
        "--evidence",
        str(tmp_path / "browser"),
        "--width",
        "390",
        "--height",
        "844",
        "--timeout",
        str(expected_timeout),
        "--page-timeout",
        str(expected_page_timeout),
        "--screenshots",
        "6",
        "--screenshot-policy",
        "failures",
    ]
    assert [step["name"] for step in args._workflow_steps] == [
        "doctor",
        "check",
        "build",
        "qa",
        "browser-qa",
        "evidence",
    ]


def test_legacy_namespace_keeps_the_original_browser_arguments(tmp_path):
    args = argparse.Namespace(work="Works/sample", members=["EPUB/a.xhtml"])
    forwarded, _, _ = run_epub_finalize(args, tmp_path)
    assert forwarded == {
        "work": "Works/sample",
        "lang": "all",
        **translator.BROWSER_QA_DEFAULTS,
    }
    assert forwarded["timeout"] == 600


@pytest.mark.parametrize("timeout", ["0", "-1", "1.5", "nan", "inf", "abc"])
@pytest.mark.parametrize("option", ["--browser-timeout", "--browser-page-timeout"])
def test_invalid_cli_budget_fails_before_station_and_all_work(timeout, option, tmp_path):
    marker = tmp_path / "final.json"
    marker.write_text("previous final", encoding="utf-8")
    output = tmp_path / "book.epub"
    output.write_bytes(b"previous output")
    with (
        mock.patch.object(translator, "start_workflow_station") as station,
        mock.patch.object(translator, "load_command_work") as load,
        mock.patch.object(translator, "clear_evidence_dir") as clear,
        mock.patch.object(translator, "reset_evidence_dir") as reset,
        mock.patch.object(translator, "command_build") as build,
        mock.patch.object(translator, "write_finalize_summary") as write,
        pytest.raises(SystemExit) as error,
    ):
        translator.main(
            [
                "finalize",
                str(tmp_path),
                "--activity",
                "none",
                option,
                timeout,
            ]
        )
    assert error.value.code == 2
    for action in (station, load, clear, reset, build, write):
        action.assert_not_called()
    assert marker.read_text(encoding="utf-8") == "previous final"
    assert output.read_bytes() == b"previous output"


@pytest.mark.parametrize("timeout", [0, -1, 1.5, float("nan"), float("inf"), True, "2"])
@pytest.mark.parametrize("option", ["browser_timeout", "browser_page_timeout"])
def test_invalid_programmatic_budget_fails_before_loading_work(timeout, option):
    args = argparse.Namespace(work="Works/sample", **{option: timeout})
    with (
        mock.patch.object(translator, "load_command_work") as load,
        mock.patch.object(translator, "clear_evidence_dir") as clear,
        mock.patch.object(translator, "reset_evidence_dir") as reset,
        mock.patch.object(translator, "command_build") as build,
        pytest.raises(translator.CliError, match="--browser(?:-page)?-timeout.*正整数"),
    ):
        translator.command_finalize(args)
    for action in (load, clear, reset, build):
        action.assert_not_called()


def test_pdf_only_completes_without_epub_or_browser_dependencies(tmp_path):
    root = tmp_path / "repo"
    work_path = root / "Works" / "sample"
    source = root / "Books" / "source.pdf"
    entry = work_path / "main.typ"
    output = work_path / "output" / "book.pdf"
    work_path.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    entry.write_text("fixture", encoding="utf-8")
    (work_path / "manifest.toml").write_text(
        'schema_version = 2\n[work]\nstatus = "active"\n', encoding="utf-8"
    )
    language = translator.Language("en", "source", entry, None, {"pdf": output})
    work = translator.Work(
        root,
        work_path,
        {"work": {"status": "active"}},
        "sample",
        "Sample",
        "typst",
        None,
        source,
        1,
        (language,),
    )
    final_marker = translator.reset_evidence_dir(work, "final") / "previous.json"
    final_marker.write_text("previous", encoding="utf-8")
    refresh_marker = translator.reset_evidence_dir(work, "refresh") / "previous.json"
    refresh_marker.write_text("previous", encoding="utf-8")

    def build(args):
        assert (args.lang, args.target, args._preserve_summary_evidence) == (
            "all",
            "all",
            True,
        )
        output.parent.mkdir(parents=True)
        output.write_bytes(b"built PDF")
        return 0

    def qa(args):
        assert (args.lang, args.target, args.dpi) == (
            "all",
            "all",
            translator.QA_DEFAULT_DPI,
        )
        evidence = translator.work_temp(work) / "qa" / "en-pdf"
        evidence.mkdir(parents=True)
        (evidence / "summary.txt").write_text("passed", encoding="utf-8")
        return 0

    args = translator.parser().parse_args(
        ["finalize", str(work_path), "--activity", "none", "--browser-timeout", "1800"]
    )
    with (
        mock.patch.object(translator, "load_work", return_value=work),
        mock.patch.object(
            translator.shutil, "which", return_value="available"
        ) as which,
        mock.patch.object(translator, "image_tool", return_value=["magick"]),
        mock.patch.object(translator, "doc_entrypoint_errors", return_value=()),
        mock.patch.object(translator, "resolve_agent_browser") as resolve_browser,
        mock.patch.object(translator, "resolve_chromium") as resolve_chromium,
        mock.patch.object(translator, "command_check", return_value=0),
        mock.patch.object(translator, "command_build", side_effect=build),
        mock.patch.object(translator, "command_qa", side_effect=qa),
        mock.patch.object(translator, "command_browser_qa") as browser,
    ):
        assert translator.command_finalize(args) == 0

    assert [call.args[0] for call in which.call_args_list] == ["mutool", "typst"]
    resolve_browser.assert_not_called()
    resolve_chromium.assert_not_called()
    browser.assert_not_called()
    assert not final_marker.exists()
    assert not refresh_marker.exists()
    assert [step["name"] for step in args._workflow_steps] == [
        "doctor",
        "check",
        "build",
        "qa",
        "evidence",
    ]
    summary = json.loads(
        (translator.evidence_dir(work, "final") / "final.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(summary["outputs"]) == 1
    assert summary["outputs"][0]["target"] == "pdf"
    assert summary["outputs"][0]["browser_qa_evidence"] is None
    assert summary["outputs"][0]["browser_qa_receipt"] is None


def test_actual_cli_help_shows_the_browser_timeout():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(translator.REPO_ROOT), env.get("PYTHONPATH", "")])
    )
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(translator.__file__).resolve()),
            "finalize",
            "--help",
        ],
        cwd=translator.REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--browser-timeout SECONDS" in result.stdout
    assert "默认：600" in result.stdout
