"""Review queue means an actionable conflict, never proof of source coverage."""
from copy import deepcopy
from pathlib import Path

from tools import source_figures as figures
from tools.source_figures_review import summarize_review


def test_no_candidates_does_not_mean_verified_negative_or_require_page_review():
    page = {"page": 1, "status": "candidates", "objects": [],
            "no_candidates": True, "coverage": "not_established", "review_required": False}
    result = summarize_review([page])
    assert result["required"] is False
    assert result["no_candidate_pages"] == 1
    assert result["coverage"] == "not_established"
    assert "true_negative" not in result


def test_object_and_page_conflict_make_one_action_with_all_context():
    page = {"page": 2, "status": "needs_review", "reasons": ["unmatched_caption"],
            "objects": [{"candidate_id": "a", "status": "needs_review",
                         "reasons": ["ambiguous_group"], "review_required": True,
                         "review_reasons": ["ambiguous_group"]}]}
    result = summarize_review([page])
    assert result["review_pages"] == result["review_objects"] == 1
    assert result["page_only_issues"] == 0
    assert result["queue"] == [{"page": 2, "candidate_id": "a",
                                "reasons": ["ambiguous_group"],
                                "page_context": ["unmatched_caption"]}]


def test_false_new_flags_cannot_erase_existing_warnings_or_failure():
    result = summarize_review([
        {"page": 1, "status": "needs_review", "review_required": False, "objects": []},
        {"page": 2, "status": "candidates", "objects": [
            {"candidate_id": "a", "status": "candidate", "review_required": False,
             "reasons": ["clip_unproven"]}]},
        {"page": 3, "status": "failed", "error": "native_extract_failed", "objects": []}])
    assert result["review_pages"] == 3
    assert result["review_objects"] == 1
    assert result["page_only_issues"] == 2
    assert result["queue"][-1]["reasons"] == ["native_extract_failed"]


def test_top_level_failure_is_actionable_even_without_any_page_warning():
    error = {"type": "ValueError", "message": "page exceeds source page count"}
    result = summarize_review([], run_status="failed", run_error=error)
    assert result["required"] is True
    assert result["run_issues"] == 1
    assert result["review_pages"] == 0
    assert result["queue"][0]["error"] == error
    normal_page = {"page": 1, "status": "candidates", "objects": []}
    after_processing = summarize_review([normal_page], run_status="failed", run_error=error)
    assert after_processing["required"] is True
    assert after_processing["no_candidate_pages"] == 1
    worker_failed = summarize_review([normal_page], run_status="failed", worker_returncode=1)
    assert worker_failed["queue"][0]["worker_returncode"] == 1


def test_existing_page_failure_is_not_duplicated_as_a_run_issue():
    result = summarize_review([{"page": 1, "status": "failed", "objects": [],
                                "error": "native_extract_failed"}], run_status="failed",
                              worker_returncode=1)
    assert result["run_issues"] == 0
    assert result["page_only_issues"] == 1
    assert len(result["queue"]) == 1


def test_content_warning_cannot_explain_a_worker_process_failure():
    result = summarize_review([{"page": 1, "status": "needs_review", "objects": [],
                                "reasons": ["caption_body_ownership_not_unique"]}],
                              run_status="failed", worker_returncode=1)
    assert result["run_issues"] == 1
    assert result["page_only_issues"] == 1
    assert result["queue"][0]["scope"] == "run"
    assert result["queue"][0]["worker_returncode"] == 1


def test_failed_page_error_survives_both_object_and_page_content_warnings():
    for object_warning in (True, False):
        objects = ([{"candidate_id": "a", "status": "needs_review",
                     "reasons": ["caption_owner_unresolved"]}] if object_warning else [])
        result = summarize_review([{"page": 1, "status": "failed", "objects": objects,
                                    "reasons": ["caption_body_ownership_not_unique"],
                                    "error": "worker_artifacts_missing"}],
                                  run_status="failed", worker_returncode=1)
        assert result["run_issues"] == 0
        item = result["queue"][0]
        assert item["page_status"] == "failed"
        assert item["error"] == "worker_artifacts_missing"
        reasons = item.get("page_context", item["reasons"])
        assert "worker_artifacts_missing" in reasons
        assert "caption_body_ownership_not_unique" in reasons


def test_legacy_report_warnings_and_empty_selection_remain_distinct():
    old = {"page": 1, "status": "needs_review", "objects": [],
           "reasons": ["no_model_body_candidates"]}
    assert summarize_review([old])["page_only_issues"] == 1
    empty = summarize_review([])
    assert empty["no_candidate_pages"] == 0
    assert empty["coverage"] == "not_established"


def test_native_groups_count_real_seed_consolidation_without_mutating_objects():
    page = {"page": 1, "status": "candidates", "model_prediction": {
        "body_candidates": [{"candidate_id": "a"}, {"candidate_id": "b"}]}, "objects": [
            {"candidate_id": "form-1", "status": "candidate", "native_form": {"id": "f"},
             "model_seeds": [{"candidate_id": "a"}, {"candidate_id": "b"}]}]}
    before = deepcopy(page)
    result = summarize_review([page])
    assert result["native_form_groups"] == 1
    assert result["model_body_seeds"] == 2
    assert result["consolidated_model_seeds"] == 1
    assert result["required"] is False
    assert page == before


def test_optional_form_capability_only_invalidates_postprocessing(tmp_path, monkeypatch):
    for name in (*figures._REQUIRED, "mutool", "java", "config"):
        (tmp_path / name).write_text(name, encoding="utf8")
    runtime = {name: tmp_path / name for name in (*figures._REQUIRED, "mutool", "java", "config")}
    runtime.update(use_cuda=False, cuda_dll_dir=None)
    # Avoid loading model tools; the actual code stamps are still checked by
    # the runtime seam tests, including preprocessing invalidation.
    original_stat = figures._stat
    monkeypatch.setattr(figures, "_stat", lambda p: original_stat(p) if Path(p).is_file()
                        else {"path": str(p), "size": 0, "mtime_ns": 0})
    versions = {"worker": {"python": "same", "distributions": {"numpy": "same"}},
                "java": "same", "mutool": "same"}
    baseline = figures._snapshot(runtime, versions)
    augmented = deepcopy(versions)
    augmented["worker"]["optional_capabilities"] = {"pypdf": {"available": True, "version": "6"}}
    current = figures._snapshot(runtime, augmented)
    assert current["inference"] == baseline["inference"]
    assert current["postprocessing"] != baseline["postprocessing"]
    missing = deepcopy(augmented)
    missing["worker"]["optional_capabilities"]["pypdf"]["available"] = False
    assert figures._snapshot(runtime, missing)["inference"] == current["inference"]


def test_source_artifact_reuse_requires_all_four_current_inputs_and_rotation(tmp_path):
    files = {}
    for key in ("png", "svg", "stext", "trace"):
        path = tmp_path / key
        path.write_text(key, encoding="utf8")
        files[key] = str(path)
    record = {"source_files": files, "geometry": {"source_rotation_deg": 0},
              "artifact_states": [figures._stat(Path(p)) for p in files.values()]}
    assert figures._reusable_source_files(record, tmp_path) == (files, 0)
    missing = deepcopy(record)
    del missing["source_files"]["trace"]
    assert figures._reusable_source_files(missing, tmp_path) is None
    invalid = deepcopy(record)
    invalid["geometry"]["source_rotation_deg"] = 45
    assert figures._reusable_source_files(invalid, tmp_path) is None
    Path(files["png"]).write_text("changed source raster", encoding="utf8")
    assert figures._reusable_source_files(record, tmp_path) is None


def test_source_artifact_reuse_never_uses_paths_outside_work_root(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    files = {}
    for key in ("png", "svg", "stext", "trace"):
        path = (tmp_path if key == "trace" else root) / key
        path.write_text(key, encoding="utf8")
        files[key] = str(path)
    record = {"source_files": files, "geometry": {"source_rotation_deg": 0},
              "artifact_states": [figures._stat(Path(p)) for p in files.values()]}
    assert figures._reusable_source_files(record, root) is None
