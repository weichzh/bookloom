"""Actionable source-figure triage; absence of candidates is not source coverage."""
from __future__ import annotations


def _reasons(*groups):
    return list(dict.fromkeys(reason for group in groups if isinstance(group, list)
                              for reason in group if isinstance(reason, str) and reason))


def _required(record):
    # A false flag cannot erase an existing warning or a failed worker.
    return (record.get("review_required") is True or
            record.get("status") in {"needs_review", "failed"} or
            bool(_reasons(record.get("review_reasons"), record.get("reasons"))))


def summarize_review(pages, *, run_status=None, run_error=None, worker_returncode=None):
    """Keep one issue per output object, with page-level context attached.

    Older reports without triage fields retain their existing warnings. This
    function never certifies a true negative, changes status, or clears an issue.
    """
    queue, review_pages = [], set()
    has_failed_page = False
    page_only = object_issues = no_candidates = raw_seeds = native_groups = consolidated = 0
    for page in pages:
        number = page.get("page")
        has_failed_page = has_failed_page or page.get("status") == "failed"
        objects = page.get("objects", [])
        page_reasons = _reasons(page.get("review_reasons"), page.get("reasons"))
        failure = {}
        if page.get("status") == "failed":
            error = page.get("error") or "page_failed"
            page_reasons = _reasons(page_reasons, [error if isinstance(error, str) else "page_failed"])
            failure = {"page_status": "failed", "error": error}
        raw_seeds += len(page.get("model_prediction", {}).get("body_candidates", []))
        if not objects and page.get("status") in {"candidates", "needs_review"}:
            no_candidates += 1
        local = []
        for obj in objects:
            if obj.get("native_form"):
                native_groups += 1
            seeds = obj.get("model_seeds")
            if isinstance(seeds, list):
                consolidated += max(0, len(seeds) - 1)
            if _required(obj):
                local.append({"page": number, "candidate_id": obj.get("candidate_id"),
                              "reasons": _reasons(obj.get("review_reasons"), obj.get("reasons"))
                                         or ["object_requires_review"]})
        if local:
            # Page-wide conflicts remain visible without creating a second
            # request to inspect the same object or page.
            if page_reasons:
                local[0]["page_context"] = page_reasons
            local[0].update(failure)
            object_issues += len(local)
            queue.extend(local)
            review_pages.add(number)
        elif _required(page):
            queue.append({"page": number, "candidate_id": None,
                          "reasons": page_reasons or ["page_requires_review"], **failure})
            page_only += 1
            review_pages.add(number)
    run_issues = 0
    if run_status == "failed" and (run_error is not None or not has_failed_page):
        issue = {"scope": "run", "page": None, "candidate_id": None,
                 "reasons": ["localization_failed"]}
        if run_error is not None:
            issue["error"] = run_error
        elif worker_returncode:
            issue["worker_returncode"] = worker_returncode
        queue.insert(0, issue)
        run_issues = 1
    return {"required": bool(queue), "run_issues": run_issues,
            "review_pages": len(review_pages),
            "review_objects": object_issues, "page_only_issues": page_only,
            "no_candidate_pages": no_candidates, "coverage": "not_established",
            "model_body_seeds": raw_seeds, "native_form_groups": native_groups,
            "consolidated_model_seeds": consolidated, "queue": queue}
