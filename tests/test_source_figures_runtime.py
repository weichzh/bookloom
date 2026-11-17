from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import source_figures as figures
from tools.browser_runtime import run_hidden


ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.pdf"
        self.source.write_bytes(b"synthetic PDF identity")
        for name in ("python.exe", "model.onnx", "native.jar", "java.exe", "mutool.exe"):
            (self.directory / name).write_bytes(name.encode())
        self.config = self.directory / "runtime.toml"
        self.config.write_text('python="python.exe"\nlayout_model="model.onnx"\n'
                               'native_jar="native.jar"\njava="java.exe"\nmutool="mutool.exe"\n',
                               encoding="utf-8")
        self.output = self.directory / "scratch"
        self.calls = []
        self.forward_pages = []
        self.fail_once = set()

    def fake_worker(self, command, **kwargs):
        request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        self.calls.append((request, kwargs))
        fail = False
        for job in request["jobs"]:
            path = Path(job["directory"])
            if job["reuse_raw"] is None:
                self.forward_pages.append(job["page"])
                raw = path / "raw.npz"
                raw.write_bytes(b"complete raw tensor")
            else:
                raw = Path(job["reuse_raw"])
            page = job["page"]
            if page in self.fail_once:
                self.fail_once.remove(page)
                result = {"page": page, "status": "failed", "error": "synthetic postprocessing error"}
                fail = True
            else:
                artifact = path / "figure.png"
                artifact.write_bytes(b"candidate")
                result = {"page": page, "status": "candidates", "objects": [{"image": str(artifact)}],
                          "dependencies": [str(artifact), str(raw)]}
            (path / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return subprocess.CompletedProcess(command, 1 if fail else 0, "", "")

    def localize(self, pages=(1, 2), *, reuse=None, identity=None, artifact_root=None):
        with (mock.patch.object(figures, "_versions", return_value={"versions": "fixture"}),
              mock.patch.object(figures, "_run", return_value=subprocess.CompletedProcess([], 0, "3\n", "")),
              mock.patch.object(figures, "run_hidden", side_effect=self.fake_worker)):
            return figures.localize_source(self.source, list(pages), self.output, self.config,
                                           source_identity=identity or {"work_id": "synthetic"}, reuse=reuse,
                                           artifact_root=artifact_root)

    @staticmethod
    def read(result):
        return json.loads(Path(result["report"]).read_text(encoding="utf-8"))

    def test_strict_reuse_avoids_forward_and_missing_or_changed_artifacts_reprocess(self):
        first = self.localize()
        self.assertEqual(first["status"], "candidates")
        self.assertEqual(self.forward_pages, [1, 2])
        second = self.localize(reuse=Path(first["report"]))
        self.assertEqual(self.forward_pages, [1, 2])
        self.assertEqual(self.read(second)["reuse"]["reused_pages"], [1, 2])
        old = self.read(first)
        image = Path(old["page_results"][0]["dependencies"][0])
        image.write_bytes(b"changed figure")
        third = self.localize(reuse=Path(first["report"]))
        report = self.read(third)
        self.assertEqual(self.forward_pages, [1, 2])
        self.assertEqual(report["reuse"]["reused_pages"], [2])
        self.assertEqual(report["reuse"]["reused_raw_pages"], [1])
        self.assertIn("page_1_dependencies_missing_or_changed", report["reuse"]["invalidated"])
        self.assertIsNotNone(self.calls[-1][0]["jobs"][0]["reuse_raw"])

    def test_expanded_reordered_and_reduced_selection_reuses_overlap(self):
        first = self.localize()
        expanded = self.localize(pages=(2, 1, 3), reuse=Path(first["report"]))
        self.assertEqual(self.forward_pages, [1, 2, 3])
        self.assertEqual(self.read(expanded)["reuse"]["reused_pages"], [2, 1])
        reduced = self.localize(pages=(3, 1), reuse=Path(expanded["report"]))
        self.assertEqual(self.forward_pages, [1, 2, 3])
        self.assertEqual(self.read(reduced)["reuse"]["reused_pages"], [3, 1])
        self.assertIn("selected_pages_changed_matching_pages_retained", self.read(reduced)["reuse"]["invalidated"])

    def test_changed_postprocessing_reuses_tensor_but_rebuilds_candidates(self):
        first = self.localize()
        with mock.patch.object(figures, "ALGORITHM", "new-postprocessing-version"):
            changed = self.localize(reuse=Path(first["report"]))
        self.assertEqual(self.forward_pages, [1, 2])
        report = self.read(changed)
        self.assertEqual(report["reuse"]["reused_pages"], [])
        self.assertEqual(report["reuse"]["reused_raw_pages"], [1, 2])
        self.assertIn("postprocessing_changed_raw_retained", report["reuse"]["invalidated"])

    def test_preprocessing_code_change_invalidates_raw_even_when_model_is_unchanged(self):
        first = self.localize(pages=(1,))
        original = figures._stat
        def changed_stat(path):
            state = original(path)
            if Path(path).name == "source_figures_inference.py":
                state["mtime_ns"] += 1
            return state
        with mock.patch.object(figures, "_stat", side_effect=changed_stat):
            changed = self.localize(pages=(1,), reuse=Path(first["report"]))
        self.assertEqual(self.forward_pages, [1, 1])
        self.assertEqual(self.read(changed)["reuse"]["reused_raw_pages"], [])
        self.assertIn("inference_input_model_or_runtime_changed", self.read(changed)["reuse"]["invalidated"])

    def test_changed_source_manifest_or_model_invalidates_raw_reuse(self):
        first = self.localize()
        self.source.write_bytes(b"changed source identity")
        changed = self.localize(reuse=Path(first["report"]))
        self.assertIn("source_or_declared_identity_changed", self.read(changed)["reuse"]["invalidated"])
        selection = self.localize(pages=(1,), reuse=Path(changed["report"]))
        identity = self.localize(pages=(1,), reuse=Path(selection["report"]), identity={"work_id": "other"})
        self.assertIn("source_or_declared_identity_changed", self.read(identity)["reuse"]["invalidated"])
        (self.directory / "model.onnx").write_bytes(b"new model version")
        runtime = self.localize(pages=(1,), reuse=Path(selection["report"]))
        self.assertIn("inference_input_model_or_runtime_changed", self.read(runtime)["reuse"]["invalidated"])
        self.assertIsNone(self.calls[-1][0]["jobs"][0]["reuse_raw"])

    def test_failure_checkpoint_resumes_only_failed_page_without_second_forward(self):
        self.fail_once.add(2)
        first = self.localize()
        self.assertEqual(first["status"], "failed")
        prior_bytes = Path(first["report"]).read_bytes()
        retry = self.localize(reuse=Path(first["report"]))
        self.assertEqual(retry["status"], "candidates")
        self.assertEqual(self.forward_pages, [1, 2])
        self.assertEqual(self.read(retry)["reuse"]["reused_pages"], [1])
        self.assertEqual(self.read(retry)["reuse"]["reused_raw_pages"], [2])
        self.assertEqual(Path(first["report"]).read_bytes(), prior_bytes)

    def test_hidden_worker_gets_local_temporary_environment(self):
        result = self.localize(pages=(1,))
        kwargs = self.calls[0][1]
        self.assertTrue(kwargs["capture_output"])
        cwd = Path(kwargs["cwd"])
        self.assertTrue(cwd.is_relative_to(self.output))
        for name in ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "XDG_CACHE_HOME"):
            self.assertTrue(Path(kwargs["env"][name]).is_relative_to(cwd))
        self.assertEqual(kwargs["env"]["JAVA_TOOL_OPTIONS"], "")
        self.assertIs(False, result["verified"])

    def test_invalid_input_and_runtime_types_do_not_invoke_worker(self):
        with mock.patch.object(figures, "run_hidden") as worker:
            with self.assertRaises(ValueError):
                figures.localize_source(self.source, [1, 1], self.output, self.config, source_identity={})
            with self.assertRaises(ValueError):
                figures.localize_source(self.source, [0], self.output, self.config, source_identity={})
            self.config.write_text(self.config.read_text(encoding="utf-8") + 'use_cuda="yes"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                figures.localize_source(self.source, [1], self.output, self.config, source_identity={})
            worker.assert_not_called()

    def test_page_outside_pdf_is_failed_before_worker(self):
        result = self.localize(pages=(4,))
        self.assertEqual(result["status"], "failed")
        self.assertIn("page count", self.read(result)["error"]["message"])
        self.assertEqual(self.calls, [])
        self.assertTrue(result["review"]["required"])
        self.assertEqual(result["review"]["run_issues"], 1)
        self.assertEqual(self.read(result)["review_summary"]["queue"][0]["scope"], "run")

    def test_version_detection_failure_keeps_global_review_action(self):
        with (mock.patch.object(figures, "_versions", side_effect=RuntimeError("version probe failed")),
              mock.patch.object(figures, "_run", return_value=subprocess.CompletedProcess([], 0, "3\n", ""))):
            result = figures.localize_source(self.source, [1], self.output, self.config,
                                              source_identity={"work_id": "synthetic"})
        report = self.read(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(report["page_results"], [])
        self.assertTrue(result["review"]["required"])
        self.assertEqual(result["review"]["run_issues"], 1)
        self.assertEqual(report["review_summary"]["queue"][0]["error"]["type"], "RuntimeError")

    def test_source_changed_after_good_pages_remains_global_failure(self):
        base_worker = self.fake_worker
        def changed_source(command, **kwargs):
            result = base_worker(command, **kwargs)
            self.source.write_bytes(b"source changed after successful page processing")
            return result
        with mock.patch.object(self, "fake_worker", side_effect=changed_source):
            result = self.localize(pages=(1,))
        report = self.read(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(report["page_results"][0]["status"], "candidates")
        self.assertTrue(result["review"]["required"])
        self.assertEqual(result["review"]["run_issues"], 1)
        self.assertIn("source changed", report["review_summary"]["queue"][0]["error"]["message"])

    def test_worker_failure_after_content_warning_keeps_both_actions(self):
        base_worker = self.fake_worker
        def crashed_after_warning(command, **kwargs):
            base_worker(command, **kwargs)
            request = json.loads(Path(command[-1]).read_text(encoding="utf8"))
            path = Path(request["jobs"][0]["directory"]) / "result.json"
            page = json.loads(path.read_text(encoding="utf8"))
            page.update(status="needs_review", reasons=["caption_body_ownership_not_unique"])
            path.write_text(json.dumps(page), encoding="utf8")
            return subprocess.CompletedProcess(command, 1, "", "worker crashed after a page warning")
        with mock.patch.object(self, "fake_worker", side_effect=crashed_after_warning):
            result = self.localize(pages=(1,))
        self.assertEqual(result["status"], "failed")
        summary = self.read(result)["review_summary"]
        self.assertEqual(summary["run_issues"], 1)
        self.assertEqual(summary["page_only_issues"], 1)
        self.assertEqual(summary["queue"][0]["worker_returncode"], 1)

    def test_missing_artifact_keeps_page_failure_with_content_warning(self):
        base_worker = self.fake_worker
        for object_warning in (True, False):
            with self.subTest(object_warning=object_warning):
                def missing_artifact(command, **kwargs):
                    result = base_worker(command, **kwargs)
                    request = json.loads(Path(command[-1]).read_text(encoding="utf8"))
                    path = Path(request["jobs"][0]["directory"]) / "result.json"
                    page = json.loads(path.read_text(encoding="utf8"))
                    page.update(status="needs_review", reasons=["caption_body_ownership_not_unique"])
                    if object_warning:
                        page["objects"][0].update(status="needs_review", reasons=["caption_owner_unresolved"])
                    Path(page["objects"][0]["image"]).unlink()
                    path.write_text(json.dumps(page), encoding="utf8")
                    return result
                with mock.patch.object(self, "fake_worker", side_effect=missing_artifact):
                    result = self.localize(pages=(1,))
                report = self.read(result)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(report["review_summary"]["run_issues"], 0)
                item = report["review_summary"]["queue"][0]
                self.assertEqual(item["error"], "worker_artifacts_missing")
                self.assertEqual(item["page_status"], "failed")
                self.assertIn("worker_artifacts_missing", item.get("page_context", item["reasons"]))

    def test_runtime_paths_are_relative_to_config_and_cpu_is_default(self):
        result = figures.load_runtime(self.config)
        self.assertEqual(result["layout_model"], self.directory / "model.onnx")
        self.assertFalse(result["use_cuda"])
        self.assertIsNone(result["cuda_dll_dir"])

    def test_artifact_root_allows_external_runtime_and_source_but_no_external_artifacts(self):
        first = self.localize(pages=(1,), artifact_root=self.output)
        self.assertEqual(first["status"], "candidates")
        good = self.localize(pages=(1,), reuse=Path(first["report"]), artifact_root=self.output)
        self.assertEqual(self.read(good)["reuse"]["reused_pages"], [1])
        original = self.read(first)
        external = str(self.source)
        def dependencies(page): page["dependencies"].append(external)
        def tensor(page): page["raw_tensor_npz"] = external
        def source_file(page): page["source_files"] = {"png": external}
        def image(page): page["objects"][0]["image"] = external
        def model_raw(page): page["model_prediction"] = {"raw_tensor_npz": external}
        def tensor_state(page): page["raw_tensor_state"]["path"] = external
        def artifact_state(page): page["artifact_states"][0]["path"] = external
        for index, mutate in enumerate((dependencies, tensor, source_file, image, model_raw,
                                        tensor_state, artifact_state)):
            with self.subTest(field=mutate.__name__):
                altered = json.loads(json.dumps(original))
                mutate(altered["page_results"][0])
                report = self.output / f"altered-{index}.json"
                report.write_text(json.dumps(altered), encoding="utf-8")
                result = self.localize(pages=(1,), reuse=report, artifact_root=self.output)
                self.assertIn("page_1_artifact_outside_root_or_invalid", self.read(result)["reuse"]["invalidated"])
                self.assertIsNone(self.calls[-1][0]["jobs"][0]["reuse_raw"])

    def test_output_and_report_must_remain_inside_artifact_root(self):
        with self.assertRaises(ValueError):
            figures.localize_source(self.source, [1], self.directory / "outside", self.config,
                                    source_identity={}, artifact_root=self.output)
        self.output.mkdir()
        foreign = self.directory / "foreign-report.json"
        foreign.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            figures.localize_source(self.source, [1], self.output, self.config,
                                    source_identity={}, reuse=foreign, artifact_root=self.output)

    def test_worker_external_output_is_failed_not_returned(self):
        base_worker = self.fake_worker
        def unsafe(command, **kwargs):
            result = base_worker(command, **kwargs)
            request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            report = Path(request["jobs"][0]["directory"]) / "result.json"
            page = json.loads(report.read_text(encoding="utf-8"))
            page["objects"][0]["image"] = str(self.source)
            report.write_text(json.dumps(page), encoding="utf-8")
            return result
        with mock.patch.object(self, "fake_worker", side_effect=unsafe):
            result = self.localize(pages=(1,), artifact_root=self.output)
        self.assertEqual(result["status"], "failed")
        page = self.read(result)["page_results"][0]
        self.assertEqual(page["error"], "worker_artifact_outside_root_or_invalid")
        self.assertNotIn("objects", page)

    def test_redirected_reuse_report_is_rejected(self):
        self.output.mkdir()
        foreign = self.directory / "foreign-report.json"
        foreign.write_text("{}", encoding="utf-8")
        link = self.output / "link.json"
        try:
            link.symlink_to(foreign)
        except OSError:
            self.skipTest("creating symlinks is not permitted on this host")
        with self.assertRaises(ValueError):
            figures.localize_source(self.source, [1], self.output, self.config,
                                    source_identity={}, reuse=link, artifact_root=self.output)

    def test_plain_project_import_does_not_import_heavy_dependencies(self):
        script = ("import builtins,sys; "
                  f"sys.path.insert(0,{str(ROOT)!r}); "
                  "old=builtins.__import__; "
                  "exec('def checked(name,*args,**kwargs):\\n'"
                  "' if name.split(\".\")[0] in {\"cv2\",\"numpy\",\"PIL\",\"rapid_doc\"}: raise RuntimeError(name)\\n'"
                  "' return old(name,*args,**kwargs)'); "
                  "builtins.__import__=checked; import tools.source_figures; print('ok')")
        process = run_hidden([sys.executable, "-I", "-c", script], capture_output=True, text=True,
                             env={**os.environ, "TEMP": str(self.directory), "TMP": str(self.directory)})
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
