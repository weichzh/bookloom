"""Source-figure candidate orchestration; the project interpreter needs only stdlib.

Heavy dependencies run in the explicitly configured interpreter.  This module
never writes a book's manuscript, assets, manifest, or status.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tomllib
import uuid
from typing import Any

try:
    from .browser_runtime import run_hidden
except ImportError:  # translator.py also supports execution as a script
    from browser_runtime import run_hidden


DPI = 225
ALGORITHM = "source-figures-hcaption-native-v1"
_FILES = ("source_figures.py", "source_figures_worker.py", "source_figures_model.py",
          "source_figures_panel.py", "source_figures_caption.py", "source_figures_logic.py",
          "source_figures_inference.py", "source_figures_render.py", "source_figures_ink.py")
_REQUIRED = ("python", "layout_model", "native_jar")
_OPTIONAL = ("java", "mutool", "cuda_dll_dir", "use_cuda")


def _stat(path: Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _path_value(raw: Any, base: Path, field: str, *, directory: bool = False) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"runtime {field} must be a nonempty path string")
    path = Path(raw).expanduser()
    path = (path if path.is_absolute() else base / path).resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"runtime {field} does not exist or has the wrong type: {path}")
    return path


def load_runtime(path: Path) -> dict[str, Any]:
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("runtime configuration must be a TOML file")
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    unknown = set(data) - set(_REQUIRED) - set(_OPTIONAL)
    if unknown:
        raise ValueError(f"unknown runtime fields: {sorted(unknown)}")
    missing = set(_REQUIRED) - set(data)
    if missing:
        raise ValueError(f"missing runtime fields: {sorted(missing)}")
    out = {key: _path_value(data[key], path.parent, key) for key in _REQUIRED}
    for key in ("java", "mutool"):
        if key in data:
            out[key] = _path_value(data[key], path.parent, key)
        else:
            resolved = shutil.which(key)
            if not resolved:
                raise ValueError(f"runtime executable not found: {key}")
            out[key] = Path(resolved).resolve(strict=True)
    cuda = data.get("use_cuda", False)
    if type(cuda) is not bool:
        raise ValueError("runtime use_cuda must be a boolean")
    out["use_cuda"] = cuda
    out["cuda_dll_dir"] = None
    if "cuda_dll_dir" in data:
        if not cuda:
            raise ValueError("runtime cuda_dll_dir requires use_cuda=true")
        out["cuda_dll_dir"] = _path_value(data["cuda_dll_dir"], path.parent, "cuda_dll_dir", directory=True)
    out["config"] = path
    return out


def _environment(scratch: Path) -> dict[str, str]:
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({key: str(scratch) for key in (
        "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "XDG_CACHE_HOME", "HF_HOME",
    )})
    env.update(JAVA_TOOL_OPTIONS="", PYTHONDONTWRITEBYTECODE="1")
    return env


def _run(command: list[str | Path], env: dict[str, str], cwd: Path, *, timeout: int = 120):
    result = run_hidden([str(x) for x in command], cwd=cwd, env=env,
                        capture_output=True, text=True, errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"tool failed ({result.returncode}): {Path(str(command[0])).name}: "
                           f"{result.stderr.strip()[:1000]}")
    return result


def _versions(runtime: dict[str, Any], env: dict[str, str], cwd: Path) -> dict[str, Any]:
    python = _run([runtime["python"], Path(__file__).with_name("source_figures_worker.py"),
                   "--versions"], env, cwd)
    packages = json.loads(python.stdout)
    mutool = _run([runtime["mutool"], "-v"], env, cwd)
    java = _run([runtime["java"], "-version"], env, cwd)
    return {"worker": packages, "mutool": (mutool.stdout + mutool.stderr).strip(),
            "java": (java.stdout + java.stderr).strip()}


def _snapshot(runtime: dict[str, Any], versions: dict[str, Any]) -> dict[str, Any]:
    dependencies = {key: _stat(runtime[key]) for key in (*_REQUIRED, "java", "mutool", "config")}
    directory = runtime["cuda_dll_dir"]
    if directory:
        dependencies["cuda_dll_dir"] = {"path": str(directory),
            "dlls": [_stat(p) for p in sorted(directory.glob("*.dll")) if p.is_file()]}
    inference_code = ("source_figures_inference.py", "source_figures_render.py")
    inference = {"dependencies": {k: dependencies[k] for k in ("python", "layout_model", "mutool")},
                 "use_cuda": runtime["use_cuda"], "versions": {
                     "worker": versions.get("worker"), "mutool": versions.get("mutool")},
                 "dpi": DPI, "code": [_stat(Path(__file__).with_name(n)) for n in inference_code]}
    if "cuda_dll_dir" in dependencies:
        inference["dependencies"]["cuda_dll_dir"] = dependencies["cuda_dll_dir"]
    postprocessing = {"dependencies": {k: dependencies[k] for k in ("native_jar", "java")},
                      "java_version": versions.get("java"), "algorithm": ALGORITHM,
                      "code": [_stat(Path(__file__).with_name(n)) for n in _FILES if n not in inference_code]}
    return {"inference": inference, "postprocessing": postprocessing}


def _write_json(path: Path, value: Any) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                       encoding="utf-8")
    partial.replace(path)


def _artifact_references(record: dict) -> list[str]:
    """Enumerate paths returned to the caller or read during page reuse."""
    if not isinstance(record, dict):
        raise ValueError("invalid page record")
    paths = []
    dependencies = record.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(p, str) and p for p in dependencies):
        raise ValueError("invalid artifact dependencies")
    paths.extend(dependencies)
    for key in ("raw_tensor_npz",):
        value = record.get(key)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise ValueError("invalid raw tensor path")
            paths.append(value)
    state = record.get("raw_tensor_state")
    if state is not None:
        if not isinstance(state, dict) or not isinstance(state.get("path"), str):
            raise ValueError("invalid raw tensor state")
        paths.append(state["path"])
    sources = record.get("source_files", {})
    if not isinstance(sources, dict) or not all(isinstance(p, str) and p for p in sources.values()):
        raise ValueError("invalid source artifact paths")
    paths.extend(sources.values())
    prediction = record.get("model_prediction", {})
    if not isinstance(prediction, dict):
        raise ValueError("invalid model prediction")
    model_raw = prediction.get("raw_tensor_npz")
    if model_raw is not None:
        if not isinstance(model_raw, str) or not model_raw:
            raise ValueError("invalid model tensor path")
        paths.append(model_raw)
    objects = record.get("objects", [])
    if not isinstance(objects, list):
        raise ValueError("invalid candidate objects")
    for item in objects:
        if not isinstance(item, dict):
            raise ValueError("invalid candidate object")
        image = item.get("image")
        if image is not None:
            if not isinstance(image, str) or not image:
                raise ValueError("invalid candidate image")
            paths.append(image)
    states = record.get("artifact_states", [])
    if not isinstance(states, list):
        raise ValueError("invalid artifact states")
    for item in states:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("invalid artifact state")
        paths.append(item["path"])
    return paths


def _inside_artifact_root(value: str | Path, root: Path) -> bool:
    logical = Path(os.path.abspath(Path(value).expanduser()))
    resolved = logical.resolve()
    return logical == resolved and resolved.is_relative_to(root)


def _artifacts_inside(record: dict, root: Path) -> bool:
    try:
        return all(_inside_artifact_root(p, root) for p in _artifact_references(record))
    except (OSError, RuntimeError, ValueError):
        return False


def _read_reuse(path: Path | None, binding: dict[str, Any], selected: list[int],
                config: dict[str, Any], artifact_root: Path | None = None
                ) -> tuple[dict[int, dict], bool, list[str]]:
    if path is None:
        return {}, False, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != 1:
            raise ValueError("unsupported report schema")
        reasons = []
        if raw.get("source") != binding:
            reasons.append("source_or_declared_identity_changed")
        old_selected = raw.get("selected_pages")
        if (not isinstance(old_selected, list) or not old_selected or
                any(type(p) is not int or p < 1 for p in old_selected) or
                len(old_selected) != len(set(old_selected))):
            raise ValueError("invalid old selection")
        if old_selected != selected:
            reasons.append("selected_pages_changed_matching_pages_retained")
        old_config = raw.get("configuration")
        if not isinstance(old_config, dict) or old_config.get("inference") != config["inference"]:
            reasons.append("inference_input_model_or_runtime_changed")
        if "source_or_declared_identity_changed" in reasons or "inference_input_model_or_runtime_changed" in reasons:
            return {}, False, reasons
        same_postprocessing = old_config.get("postprocessing") == config["postprocessing"]
        if not same_postprocessing:
            reasons.append("postprocessing_changed_raw_retained")
        records = raw.get("page_results")
        if not isinstance(records, list):
            raise ValueError("missing page results")
        mapped = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("invalid page result")
            page = record.get("page")
            if type(page) is not int or page not in old_selected or page in mapped:
                raise ValueError("invalid or duplicate page result")
            mapped[page] = record
        retained = {}
        for page, record in mapped.items():
            if page not in selected:
                continue
            if artifact_root is not None and not _artifacts_inside(record, artifact_root):
                reasons.append(f"page_{page}_artifact_outside_root_or_invalid")
                continue
            retained[page] = record
        return retained, same_postprocessing, reasons
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {}, False, [f"unreadable_or_invalid_report: {type(exc).__name__}"]


def _valid_artifacts(record: dict) -> bool:
    deps = record.get("dependencies")
    if (record.get("status") not in {"candidates", "needs_review"} or
            not isinstance(deps, list) or not deps or
            not all(isinstance(path, str) and Path(path).is_file() for path in deps)):
        return False
    try:
        return [_stat(Path(path)) for path in deps] == record.get("artifact_states")
    except OSError:
        return False


def localize_source(source: Path, pages: list[int], output_dir: Path, runtime_config: Path,
                    *, source_identity: dict, reuse: Path | None = None,
                    artifact_root: Path | None = None) -> dict:
    """Generate unverified source-bound candidates, or a recoverable failed report.

    Pages are one-based. When artifact_root is provided, output and reuse
    artifacts must resolve inside it. Otherwise the caller owns containment.
    Reuse never creates a persistent cache.
    """
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("source must be an existing PDF file")
    if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages):
        raise ValueError("pages must be a nonempty list of positive one-based integers")
    if len(pages) != len(set(pages)):
        raise ValueError("selected pages must be unique")
    if not isinstance(source_identity, dict):
        raise ValueError("source_identity must be a dictionary")
    try:
        json.dumps(source_identity, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("source_identity must contain JSON-compatible values") from exc
    runtime = load_runtime(runtime_config)
    selected = list(pages)
    binding = {**_stat(source), "declared": source_identity}
    artifact_root = Path(artifact_root).expanduser().resolve() if artifact_root is not None else None
    output = Path(output_dir).expanduser().resolve()
    if artifact_root is not None:
        if not _inside_artifact_root(output_dir, artifact_root):
            raise ValueError("output_dir must be inside artifact_root")
        if reuse is not None and not _inside_artifact_root(reuse, artifact_root):
            raise ValueError("reuse report must be inside artifact_root")
    output.mkdir(parents=True, exist_ok=True)
    run_dir = output / f"source-figures-{uuid.uuid4().hex}"
    run_dir.mkdir()
    env = _environment(run_dir / "environment")
    report_path = run_dir / "report.json"
    report = {"schema": 1, "source": binding, "selected_pages": selected,
              "verified": False, "status": "failed", "page_results": [],
              "reuse": {"requested": str(Path(reuse).resolve()) if reuse else None,
                        "invalidated": [], "reused_pages": [], "reused_raw_pages": []}}
    try:
        # Validate page bounds before loading or invoking the model worker.
        count_result = _run([runtime["mutool"], "show", source, "trailer/Root/Pages/Count"], env, run_dir)
        try:
            page_count = int(count_result.stdout.strip())
        except ValueError as exc:
            raise RuntimeError("could not read PDF page count") from exc
        if max(selected) > page_count:
            raise ValueError(f"selected page exceeds source page count ({page_count})")
        versions = _versions(runtime, env, run_dir)
        configuration = _snapshot(runtime, versions)
        report["configuration"] = configuration
        old, same_postprocessing, invalid = _read_reuse(
            Path(reuse) if reuse else None, binding, selected, configuration, artifact_root)
        report["reuse"]["invalidated"] = invalid
        jobs = []
        results = {}
        for page in selected:
            previous = old.get(page, {})
            if same_postprocessing and _valid_artifacts(previous):
                results[page] = previous
                report["reuse"]["reused_pages"].append(page)
                continue
            if same_postprocessing and previous and previous.get("status") in {"candidates", "needs_review"}:
                report["reuse"]["invalidated"].append(f"page_{page}_dependencies_missing_or_changed")
            raw = previous.get("raw_tensor_npz")
            raw_path = Path(raw) if isinstance(raw, str) else None
            raw = (str(raw_path.resolve()) if raw_path and raw_path.is_file() and
                   _stat(raw_path) == previous.get("raw_tensor_state") else None)
            if raw:
                report["reuse"]["reused_raw_pages"].append(page)
            page_dir = run_dir / f"page-{page}"
            page_dir.mkdir()
            jobs.append({"page": page, "directory": str(page_dir), "reuse_raw": raw})
        if jobs:
            request = {"source": str(source), "dpi": DPI, "jobs": jobs,
                       "runtime": {k: str(v) if isinstance(v, Path) else v for k, v in runtime.items()}}
            request_path = run_dir / "request.json"
            _write_json(request_path, request)
            process = run_hidden([str(runtime["python"]), str(Path(__file__).with_name("source_figures_worker.py")),
                                  str(request_path)], cwd=run_dir, env=env, capture_output=True,
                                 text=True, errors="replace")
            worker_log = run_dir / "worker.log"
            if artifact_root is not None and not _inside_artifact_root(worker_log, artifact_root):
                raise ValueError("worker log escaped artifact_root")
            worker_log.write_text(process.stdout + process.stderr, encoding="utf-8")
            for job in jobs:
                page_dir = Path(job["directory"])
                result_path = page_dir / "result.json"
                if artifact_root is not None and not _inside_artifact_root(result_path, artifact_root):
                    result = {"page": job["page"], "status": "failed", "error": "worker_report_outside_root"}
                elif result_path.is_file():
                    try:
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                        if result.get("page") != job["page"]:
                            raise ValueError("worker page identity mismatch")
                    except (ValueError, OSError, json.JSONDecodeError):
                        result = {"page": job["page"], "status": "failed", "error": "invalid_worker_report"}
                else:
                    result = {"page": job["page"], "status": "failed", "error": "worker_did_not_report"}
                if artifact_root is not None and not _artifacts_inside(result, artifact_root):
                    result = {"page": job["page"], "status": "failed", "error": "worker_artifact_outside_root_or_invalid"}
                raw = page_dir / "raw.npz"
                if artifact_root is not None and not _inside_artifact_root(raw, artifact_root):
                    result = {"page": job["page"], "status": "failed", "error": "worker_raw_outside_root"}
                elif raw.is_file():
                    result["raw_tensor_npz"] = str(raw)
                elif job["reuse_raw"]:
                    result["raw_tensor_npz"] = job["reuse_raw"]
                if result.get("raw_tensor_npz"):
                    result["raw_tensor_state"] = _stat(Path(result["raw_tensor_npz"]))
                if result.get("status") in {"candidates", "needs_review"}:
                    try:
                        result["artifact_states"] = [_stat(Path(p)) for p in result["dependencies"]]
                    except (KeyError, OSError, TypeError):
                        result.update(status="failed", error="worker_artifacts_missing")
                results[job["page"]] = result
            report["worker_returncode"] = process.returncode
        report["page_results"] = [results[p] for p in selected]
        if _stat(source) != {k: binding[k] for k in ("path", "size", "mtime_ns")}:
            raise RuntimeError("source changed during localization")
        if _snapshot(runtime, versions) != configuration:
            raise RuntimeError("runtime dependencies changed during localization")
        statuses = [p.get("status") for p in report["page_results"]]
        if report.get("worker_returncode", 0) != 0 or any(
                s not in {"candidates", "needs_review"} for s in statuses):
            report["status"] = "failed"
        elif any(s == "needs_review" for s in statuses):
            report["status"] = "needs_review"
        else:
            report["status"] = "candidates"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    report["pages"] = len(selected)
    report["objects"] = sum(len(p.get("objects", [])) for p in report["page_results"])
    _write_json(report_path, report)
    return {"report": str(report_path), "status": report["status"], "pages": len(selected),
            "objects": report["objects"], "verified": False}
