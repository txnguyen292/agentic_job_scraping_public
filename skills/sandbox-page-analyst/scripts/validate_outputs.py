#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Annotated
from typing import Any
from urllib.parse import urlparse

import typer
from loguru import logger


logger.remove()
logger.add(sys.stderr, level=os.getenv("JOB_SCRAPER_SCRIPT_LOG_LEVEL", "WARNING"))

VALIDATE_HELP = """\
Use after extractor-backed protocol files are written and before
sandbox_finalize.py. The directory should contain page_profile.json,
extraction_strategy.json, candidates.json, validation.json, and final.json.

Examples:
  validate_outputs.py output
  validate_outputs.py --audit-id sandbox_run_abc
"""

app = typer.Typer(
    add_completion=False,
    help="Validate sandbox-page-analyst protocol outputs.\n\n" + VALIDATE_HELP,
    rich_markup_mode="rich",
)


REQUIRED_FILES = {
    "page_profile": "page_profile.json",
    "extraction_strategy": "extraction_strategy.json",
    "candidates": "candidates.json",
    "validation": "validation.json",
    "final": "final.json",
}

ITVIEC_DETAIL_URL_PATTERN = re.compile(
    r"(?:https?://(?:www\.)?itviec\.com)?/it-jobs/[^\"'<>\s?]+-\d{4}"
)
EXPECTED_FIXTURES = {
    ("itviec.com", "/it-jobs/ai-engineer/ha-noi"): "tests/fixtures/itviec_ai_engineer_ha_noi.expected.json",
}
COMPARE_FIELDS = ("title", "company_name", "location_raw", "salary_raw")


def validate_output_dir(output_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    refs: dict[str, dict[str, str]] = {}

    missing_files: list[str] = []
    for key, filename in REQUIRED_FILES.items():
        path = output_dir / filename
        if not path.exists():
            missing_files.append(str(path))
            continue
        _load_json(path)
        refs[key] = {
            "path": str(Path("output") / filename),
            "sha256": _sha256_file(path),
        }
    if missing_files:
        raise ValueError(f"missing required protocol outputs: {', '.join(missing_files)}")

    candidates = _load_json(output_dir / "candidates.json")
    validation = _load_json(output_dir / "validation.json")
    final = _load_json(output_dir / "final.json")
    if "jobs" not in candidates:
        if "result" in candidates:
            raise ValueError(
                "candidates.json must contain top-level jobs/crawl; do not use the final-result envelope "
                "{status, result: {jobs: [...]}} for candidates.json"
            )
        raise ValueError("candidates.jobs must be a list")
    jobs = candidates.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("candidates.jobs must be a list")
    if "crawl" not in candidates or not isinstance(candidates.get("crawl"), dict):
        raise ValueError("candidates.crawl must be an object")

    for index, job in enumerate(jobs):
        if not str(job.get("title") or "").strip():
            raise ValueError(f"job {index} missing title")
        if not str(job.get("job_url") or "").strip():
            raise ValueError(f"job {index} missing job_url")
        _validate_job_types(job, f"job {index}")
        _validate_job_url(job, f"job {index}")
        for evidence in job.get("evidence") or []:
            text = str(evidence.get("text") or "")
            if len(text) > 500:
                raise ValueError(f"job {index} evidence text exceeds 500 chars")

    candidate_count = int((candidates.get("crawl") or {}).get("candidate_count") or len(jobs))
    validation_count = int(validation.get("candidate_count") or len(jobs))
    if candidate_count != len(jobs) or validation_count != len(jobs):
        warnings.append("candidate counts differ between jobs, crawl, and validation")

    valid = bool(validation.get("valid", False))
    if not valid:
        raise ValueError("validation.json marks protocol output invalid")

    if final.get("status") not in {"success", "needs_review", "error"}:
        raise ValueError("final.json status must be success, needs_review, or error")
    result = final.get("result")
    if not isinstance(result, dict):
        raise ValueError("final.json must contain result as an object")
    final_jobs = result.get("jobs")
    if not isinstance(final_jobs, list):
        raise ValueError("final.json result.jobs must be a list")
    if len(final_jobs) != len(jobs):
        raise ValueError("final.json result.jobs count must match candidates.jobs")
    for index, job in enumerate(final_jobs):
        if not isinstance(job, dict):
            raise ValueError(f"final.json result.jobs[{index}] must be an object")
        _validate_job_types(job, f"final.json result.jobs[{index}]")
        _validate_job_url(job, f"final.json result.jobs[{index}]")
    if final.get("status") == "success" and not jobs:
        raise ValueError("final.json cannot be success with zero extracted jobs")
    _validate_itviec_listing_coverage(output_dir, candidates, final, jobs)
    _validate_against_expected_fixture(candidates, final)

    return {
        **refs,
        "valid": True,
        "warnings": warnings + list(validation.get("warnings") or []),
    }


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _validate_job_types(job: dict[str, Any], label: str) -> None:
    string_fields = (
        "title",
        "company_name",
        "job_url",
        "location_raw",
        "location",
        "remote_type",
        "employment_type",
        "posted_at",
        "salary_raw",
        "description_text",
        "description",
        "relevance_reason",
    )
    for field in string_fields:
        if field in job and not isinstance(job[field], str):
            raise ValueError(f"{label} field {field} must be a string, not {type(job[field]).__name__}")
    if "tags" in job and not isinstance(job["tags"], list):
        raise ValueError(f"{label} field tags must be a list")
    if "evidence" in job and not isinstance(job["evidence"], list):
        raise ValueError(f"{label} field evidence must be a list")


def _validate_job_url(job: dict[str, Any], label: str) -> None:
    raw_url = str(job.get("job_url") or "")
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    if not host.endswith("itviec.com"):
        return
    path = parsed.path.rstrip("/")
    if not re.fullmatch(r"/it-jobs/.+-\d{4}", path):
        raise ValueError(f"{label} ITviec job_url must be a detail posting URL ending in -NNNN")
    if "click_source=" in parsed.query:
        raise ValueError(f"{label} ITviec job_url must not be a navigation/category URL")


def _validate_itviec_listing_coverage(
    output_dir: Path,
    candidates: dict[str, Any],
    final: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> None:
    if final.get("status") != "success":
        return
    if not _looks_like_itviec_listing(candidates, jobs):
        return

    detail_urls = _discover_itviec_detail_urls(output_dir)
    expected_count = _expected_itviec_listing_count(output_dir, detail_urls)
    if expected_count < 5:
        return
    if len(jobs) == expected_count:
        return
    raise ValueError(
        f"ITviec listing evidence expects {expected_count} jobs but candidates.jobs has {len(jobs)}. "
        "Repair the extractor to emit exactly one job per repeated listing card/detail posting URL, "
        "or return needs_review with a documented blocker."
    )


def _looks_like_itviec_listing(candidates: dict[str, Any], jobs: list[dict[str, Any]]) -> bool:
    source = candidates.get("source") if isinstance(candidates.get("source"), dict) else {}
    urls = [
        str(source.get("source_url") or ""),
        *[str(job.get("source_url") or "") for job in jobs if isinstance(job, dict)],
        *[str(job.get("job_url") or "") for job in jobs if isinstance(job, dict)],
    ]
    return any("itviec.com/it-jobs/" in url for url in urls)


def _discover_itviec_detail_urls(output_dir: Path) -> set[str]:
    workspace = output_dir.parent
    html_paths = [workspace / "page.html", *sorted(workspace.glob("*.html"))]
    seen_paths: set[Path] = set()
    detail_urls: set[str] = set()
    for path in html_paths:
        resolved = path.resolve()
        if resolved in seen_paths or not path.exists() or not path.is_file():
            continue
        seen_paths.add(resolved)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in ITVIEC_DETAIL_URL_PATTERN.findall(text):
            normalized = match.rstrip("/").split("&", 1)[0]
            if normalized.startswith("http"):
                parsed = urlparse(normalized)
                normalized = parsed.path.rstrip("/")
            detail_urls.add(normalized)
    return detail_urls


def _expected_itviec_listing_count(output_dir: Path, detail_urls: set[str]) -> int:
    card_count = _discover_itviec_card_count(output_dir)
    if card_count:
        return card_count
    profile = _load_optional_json(output_dir / "page_profile.json")
    profile_count = _extract_observed_count(profile)
    if profile_count:
        return profile_count
    return len(detail_urls)


def _discover_itviec_card_count(output_dir: Path) -> int:
    workspace = output_dir.parent
    html_paths = [workspace / "page.html", *sorted(workspace.glob("*.html"))]
    counts: list[int] = []
    for path in html_paths:
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        counts.extend(
            count
            for count in (
                len(re.findall(r'class=["\'][^"\']*\bjob-card\b', text)),
                len(re.findall(r'data-search--pagination-target=["\']jobCard["\']', text)),
                len(re.findall(r'data-search--job-selection-job-slug-value=', text)),
            )
            if count > 0
        )
    return max(counts) if counts else 0


def _validate_against_expected_fixture(candidates: dict[str, Any], final: dict[str, Any]) -> None:
    fixture_path = _expected_fixture_path(candidates, final)
    if fixture_path is None:
        return
    expected = _load_json(fixture_path)
    actual = final.get("result") if isinstance(final.get("result"), dict) else candidates
    comparison = _compare_expected_jobs(actual, expected)
    if comparison["status"] == "pass":
        return
    raise ValueError(
        "sandbox output does not match frozen expected fixture "
        f"{fixture_path.name}: expected {comparison['expected_job_count']} jobs, "
        f"got {comparison['actual_job_count']}; missing_urls={comparison['missing_urls'][:5]}; "
        f"extra_urls={comparison['extra_urls'][:5]}; field_mismatches={comparison['field_mismatches'][:5]}; "
        f"crawl_mismatches={comparison['crawl_mismatches'][:5]}"
    )


def _expected_fixture_path(candidates: dict[str, Any], final: dict[str, Any]) -> Path | None:
    for source_url in _source_urls_for_expected_lookup(candidates, final):
        parsed = urlparse(source_url)
        key = (parsed.netloc.lower().removeprefix("www."), parsed.path.rstrip("/"))
        relative = EXPECTED_FIXTURES.get(key)
        if not relative:
            continue
        root = _repo_root()
        if root is None:
            return None
        path = root / relative
        if path.exists():
            return path
    return None


def _source_urls_for_expected_lookup(candidates: dict[str, Any], final: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for payload in (candidates, final.get("result") if isinstance(final.get("result"), dict) else {}):
        if not isinstance(payload, dict):
            continue
        source = payload.get("source")
        if isinstance(source, dict) and source.get("source_url"):
            urls.append(str(source["source_url"]))
        for job in payload.get("jobs") or []:
            if isinstance(job, dict) and job.get("source_url"):
                urls.append(str(job["source_url"]))
    return urls


def _repo_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "tests" / "fixtures").exists():
            return parent
    return None


def _compare_expected_jobs(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    actual_jobs = actual.get("jobs") if isinstance(actual.get("jobs"), list) else []
    expected_jobs = expected.get("jobs") if isinstance(expected.get("jobs"), list) else []
    actual_by_url = {_canonical_url(str(job.get("job_url") or "")): job for job in actual_jobs if isinstance(job, dict)}
    expected_by_url = {_canonical_url(str(job.get("job_url") or "")): job for job in expected_jobs if isinstance(job, dict)}

    actual_urls = set(actual_by_url)
    expected_urls = set(expected_by_url)
    field_mismatches: list[dict[str, Any]] = []
    for url in sorted(actual_urls & expected_urls):
        actual_job = actual_by_url[url]
        expected_job = expected_by_url[url]
        for field in COMPARE_FIELDS:
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

    crawl_mismatches: list[dict[str, Any]] = []
    actual_crawl = actual.get("crawl") if isinstance(actual.get("crawl"), dict) else {}
    expected_crawl = expected.get("crawl") if isinstance(expected.get("crawl"), dict) else {}
    for field in ("discovered_count", "candidate_count", "relevant_count", "blocked", "blocker"):
        if actual_crawl.get(field) != expected_crawl.get(field):
            crawl_mismatches.append({"field": field, "expected": expected_crawl.get(field), "actual": actual_crawl.get(field)})

    passed = not (
        expected_urls - actual_urls
        or actual_urls - expected_urls
        or field_mismatches
        or crawl_mismatches
    )
    return {
        "status": "pass" if passed else "fail",
        "expected_job_count": len(expected_jobs),
        "actual_job_count": len(actual_jobs),
        "missing_urls": sorted(expected_urls - actual_urls),
        "extra_urls": sorted(actual_urls - expected_urls),
        "field_mismatches": field_mismatches,
        "crawl_mismatches": crawl_mismatches,
    }


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.rstrip("/") if parsed.netloc.endswith("itviec.com") else url.rstrip("/")


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return _load_json(path)
    except Exception as exc:
        logger.debug("could not load optional json {}: {}", path, exc)
        return {}


def _extract_observed_count(value: Any) -> int:
    if isinstance(value, dict):
        for key in (
            "observed_job_cards",
            "job_card_count",
            "job_cards_count",
            "card_count",
            "listing_card_count",
            "repeated_job_cards",
            "repeated_listing_cards",
        ):
            count = _coerce_positive_int(value.get(key))
            if count:
                return count
        for child in value.values():
            count = _extract_observed_count(child)
            if count:
                return count
    if isinstance(value, list):
        for child in value:
            count = _extract_observed_count(child)
            if count:
                return count
    return 0


def _coerce_positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else 0
    return 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    app(args=sys.argv[1:], prog_name=Path(sys.argv[0]).name)


@app.command(help="Validate sandbox-page-analyst protocol outputs.\n\n" + VALIDATE_HELP)
def _validate_outputs_cli(
    output_dir: Annotated[
        str,
        typer.Argument(help="Directory containing protocol output JSON files. Defaults to output."),
    ] = "output",
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Resolve output_dir inside an active sandbox workspace by audit id.")] = "",
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup when --audit-id is used.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup when --audit-id is used.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
) -> None:
    try:
        payload = validate_output_dir(_resolve_output_dir(output_dir, audit_id, user_id, session_id, app_root))
    except Exception as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def _resolve_output_dir(output_dir: str, audit_id: str, user_id: str, session_id: str, app_root: str) -> Path:
    path = Path(output_dir)
    if not audit_id:
        return path
    from job_scraper.sandbox_terminal import SandboxRegistry

    root = app_root or _default_app_root()
    record = SandboxRegistry(root).load(user_id, session_id, audit_id)
    return path if path.is_absolute() else Path(record.workspace_path) / path


def _default_app_root() -> str:
    env_root = os.getenv("JOB_SCRAPER_ADK_APP_ROOT")
    if env_root:
        return env_root
    try:
        import importlib.util

        spec = importlib.util.find_spec("job_scraper")
        if spec and spec.origin:
            return str(Path(spec.origin).resolve().parent)
    except Exception:
        pass
    return str((Path.cwd() / "src/job_scraper").resolve())


if __name__ == "__main__":
    main()
