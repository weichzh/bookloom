"""Unified CLI seam: source candidates do not mutate formal book state."""
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pytest
import tools
from tools import translator


@pytest.fixture
def seam(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    book = repo / "Works" / "example"
    book.mkdir(parents=True)
    source = repo / "Books" / "example.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.4 source fixture")
    (book / "manifest.toml").write_text("formal manifest", encoding="utf-8")
    (book / "STATUS.md").write_text("formal status", encoding="utf-8")
    runtime = repo / "runtime.toml"
    runtime.write_text('python = "python"', encoding="utf-8")
    work = SimpleNamespace(root=repo, path=book, source=source,
                           source_pages=10, source_kind="pdf", work_id="example",
                           manifest={"source": {"sha256": "declared-source-identity"}})
    monkeypatch.setattr(translator, "load_command_work", lambda args: work)
    calls = []
    result = {"report": "scratch/report.json", "status": "candidates",
              "pages": 3, "objects": 2, "verified": False}
    def localize(*args, **kwargs):
        calls.append((args, kwargs))
        return result.copy()
    fake = SimpleNamespace(localize_source=localize)
    monkeypatch.setitem(sys.modules, "tools.source_figures", fake)
    monkeypatch.setattr(tools, "source_figures", fake, raising=False)
    args = Namespace(work=str(book), pages="2,4-5", agent="test-source-figures",
                     runtime=str(runtime), reuse=None)
    return SimpleNamespace(work=work, args=args, calls=calls, result=result, module=fake)


def test_source_figures_is_registered_as_a_work_station():
    assert "source-figures" in translator.WORK_COMMANDS
    args = translator.parser().parse_args([
        "source-figures", "Works/example", "--pages", "2,4-5", "--agent", "worker",
        "--runtime", "runtime.toml", "--activity", "source-review"])
    assert args.handler is translator.command_source_figures
    assert args.activity == ["source-review"]


@pytest.mark.parametrize("missing", ["--pages", "--agent", "--runtime", "--activity"])
def test_new_command_requires_explicit_scope_runtime_and_activity(missing):
    args = ["source-figures", "Works/example"]
    values = {"--pages": "2", "--agent": "worker", "--runtime": "runtime.toml", "--activity": "none"}
    for key, value in values.items():
        if key != missing:
            args.extend([key, value])
    with pytest.raises(SystemExit):
        translator.parser().parse_args(args)


@pytest.mark.parametrize("spec", ["", "0", "11", "-1", "2-1", "1-9999999999", "1,1", "1-3,3", "one", "1,", "1 2"])
def test_invalid_page_selection_rejected_before_heavy_tool_or_output(seam, spec):
    seam.args.pages = spec
    with pytest.raises(translator.CliError):
        translator.command_source_figures(seam.args)
    assert not seam.calls
    assert not translator.work_temp(seam.work).exists()


def test_epub_source_not_sent_to_pdf_localizer(seam):
    seam.work.source_kind = "epub"
    with pytest.raises(translator.CliError, match="PDF"):
        translator.command_source_figures(seam.args)
    assert not seam.calls


def test_missing_runtime_rejected_before_creating_scratch(seam):
    seam.args.runtime = "missing-runtime.toml"
    with pytest.raises(translator.CliError, match="runtime"):
        translator.command_source_figures(seam.args)
    assert not seam.calls
    assert not translator.work_temp(seam.work).exists()


def test_unsafe_agent_is_rejected_without_heavy_tool(seam):
    seam.args.agent = "../outside"
    with pytest.raises(translator.CliError):
        translator.command_source_figures(seam.args)
    assert not seam.calls


def test_same_work_reuse_passes_through_without_reopening_formal_state(seam, capsys):
    reuse = translator.work_temp(seam.work) / "scratch" / "old" / "report.json"
    reuse.parent.mkdir(parents=True)
    reuse.write_text("{}", encoding="utf-8")
    seam.args.reuse = str(reuse)
    assert translator.command_source_figures(seam.args) == 0
    args, kwargs = seam.calls[0]
    assert args[0] == seam.work.source and args[1] == [2, 4, 5]
    assert args[2].is_relative_to(translator.work_temp(seam.work) / "scratch" / seam.args.agent)
    assert kwargs["reuse"] == reuse
    assert kwargs["source_identity"] == {"manifest_source_sha256": "declared-source-identity", "work_id": "example"}
    assert json.loads(capsys.readouterr().out)["verified"] is False
    assert (seam.work.path / "manifest.toml").read_text(encoding="utf-8") == "formal manifest"
    assert (seam.work.path / "STATUS.md").read_text(encoding="utf-8") == "formal status"
    assert not translator.work_receipts(seam.work).exists()


def test_foreign_work_reuse_is_rejected(seam):
    other = seam.work.root / ".tmp" / "translator" / "other-work" / "report.json"
    other.parent.mkdir(parents=True)
    other.write_text("{}", encoding="utf-8")
    seam.args.reuse = str(other)
    with pytest.raises(translator.CliError, match="同一作品"):
        translator.command_source_figures(seam.args)
    assert not seam.calls


def test_missing_reuse_is_not_silently_ignored(seam):
    seam.args.reuse = str(translator.work_temp(seam.work) / "missing.json")
    with pytest.raises(translator.CliError, match="复用报告不存在"):
        translator.command_source_figures(seam.args)
    assert not seam.calls


@pytest.mark.parametrize(("status", "expected"), [("candidates", 0), ("needs_review", 0), ("failed", 1)])
def test_report_status_and_exit_code_are_distinct_from_content_acceptance(seam, capsys, status, expected):
    seam.result["status"] = status
    assert translator.command_source_figures(seam.args) == expected
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["verified"] is False
    assert result["report"]


def test_runtime_validation_error_is_a_cli_error(seam):
    def invalid(*args, **kwargs):
        raise ValueError("runtime field is invalid")
    seam.module.localize_source = invalid
    with pytest.raises(translator.CliError, match="runtime field is invalid"):
        translator.command_source_figures(seam.args)


def test_cli_supplies_same_work_artifact_boundary(seam):
    translator.command_source_figures(seam.args)
    args, kwargs = seam.calls[0]
    assert kwargs["artifact_root"] == translator.work_temp(seam.work)
    args[2].relative_to(kwargs["artifact_root"])


def test_reuse_report_from_another_work_is_rejected_before_tools(seam):
    foreign = seam.work.root / ".tmp" / "translator" / "other-work" / "report.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("{}", encoding="utf-8")
    seam.args.reuse = str(foreign)
    with pytest.raises(translator.CliError, match="同一作品"):
        translator.command_source_figures(seam.args)
    assert not seam.calls
