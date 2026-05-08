from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse


DEFAULT_COMPARE_FIELDS = ("title", "company_name", "location_raw", "salary_raw")


def load_json_file(path: str | Path) -> dict[str, Any]:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept either raw job_extraction output or SandboxAgentResult-style output."""
    result = payload.get("result")
    if isinstance(result, dict) and "jobs" in result:
        return result
    return payload


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def compare_job_extraction(
    actual: dict[str, Any],
    expected: dict[str, Any],
    fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS,
) -> dict[str, Any]:
    actual = normalize_extraction_payload(actual)
    expected = normalize_extraction_payload(expected)

    actual_jobs = actual.get("jobs") or []
    expected_jobs = expected.get("jobs") or []
    actual_by_url = {canonical_url(str(job.get("job_url", ""))): job for job in actual_jobs}
    expected_by_url = {canonical_url(str(job.get("job_url", ""))): job for job in expected_jobs}

    actual_urls = set(actual_by_url)
    expected_urls = set(expected_by_url)
    missing_urls = sorted(expected_urls - actual_urls)
    extra_urls = sorted(actual_urls - expected_urls)

    field_mismatches = []
    for url in sorted(expected_urls & actual_urls):
        actual_job = actual_by_url[url]
        expected_job = expected_by_url[url]
        for field in fields:
            if actual_job.get(field) != expected_job.get(field):
                field_mismatches.append(
                    {
                        "job_url": url,
                        "field": field,
                        "expected": expected_job.get(field),
                        "actual": actual_job.get(field),
                    }
                )
        if set(actual_job.get("tags") or []) != set(expected_job.get("tags") or []):
            field_mismatches.append(
                {
                    "job_url": url,
                    "field": "tags",
                    "expected": sorted(expected_job.get("tags") or []),
                    "actual": sorted(actual_job.get("tags") or []),
                }
            )

    expected_crawl = expected.get("crawl") or {}
    actual_crawl = actual.get("crawl") or {}
    crawl_mismatches = []
    for field in ("discovered_count", "candidate_count", "relevant_count", "blocked", "blocker"):
        if actual_crawl.get(field) != expected_crawl.get(field):
            crawl_mismatches.append(
                {
                    "field": field,
                    "expected": expected_crawl.get(field),
                    "actual": actual_crawl.get(field),
                }
            )

    passed = not missing_urls and not extra_urls and not field_mismatches and not crawl_mismatches
    return {
        "status": "pass" if passed else "fail",
        "expected_job_count": len(expected_jobs),
        "actual_job_count": len(actual_jobs),
        "matched_job_count": len(expected_urls & actual_urls),
        "missing_urls": missing_urls,
        "extra_urls": extra_urls,
        "field_mismatches": field_mismatches,
        "crawl_mismatches": crawl_mismatches,
    }


def compare_job_extraction_files(actual_path: str | Path, expected_path: str | Path) -> dict[str, Any]:
    return compare_job_extraction(
        actual=load_json_file(actual_path),
        expected=load_json_file(expected_path),
    )
