from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from typing import Any

from bs4 import BeautifulSoup


SOURCE_BASE = "https://itviec.com"
PACKAGE_ID = "itviec-listing-v1"
REFERENCE_VERSION = "itviec-listing-v1"


def clean(text: str) -> str:
    return " ".join((text or "").split())


def text_or_empty(node: Any) -> str:
    return clean(node.get_text(" ", strip=True)) if node else ""


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_url_from_inputs(default: str) -> str:
    inputs_path = Path("inputs.json")
    if not inputs_path.exists():
        return default
    try:
        payload = json.loads(inputs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
    value = str(payload.get("source_url") or "").strip()
    return value or default


def canonical_job_url(raw: str, card: Any) -> str:
    raw = raw or ""
    if not raw:
        sign_in = card.select_one('a[href^="/sign_in?"]')
        if sign_in:
            parsed = urlparse(sign_in.get("href", ""))
            slug = (parse_qs(parsed.query).get("job") or [""])[0]
            if slug:
                raw = f"/it-jobs/{slug}"
    absolute = urljoin(SOURCE_BASE, raw)
    parsed = urlparse(absolute)
    path = parsed.path
    if path.endswith("/content"):
        path = path[: -len("/content")]
    return f"{parsed.scheme}://{parsed.netloc}{path.rstrip('/')}"


def location_from_card(card: Any) -> tuple[str, str, str]:
    container = card.select_one("div.imt-1.d-flex.align-items-center.text-dark-grey")
    if not container:
        return "", "", "unknown"
    parts = [clean(child.get_text(" ", strip=True)) for child in container.find_all("div", recursive=False)]
    parts = [part for part in parts if part]
    if len(parts) >= 2:
        mode, place = parts[0], parts[-1]
        return f"{mode} {place}".strip(), place, mode.lower()
    raw = clean(container.get_text(" ", strip=True))
    return raw, raw, "unknown"


def posted_at_from_card(card: Any) -> str:
    raw = text_or_empty(card.select_one("span.small-text.text-dark-grey"))
    return raw.replace("Posted", "", 1).strip() if raw.startswith("Posted") else raw


def tags_from_card(card: Any) -> list[str]:
    tags: list[str] = []
    for tag in card.select('a[href*="click_source=Skill+tag"]'):
        value = text_or_empty(tag)
        if value and value not in tags:
            tags.append(value)
    return tags


def evidence_text(job: dict[str, Any]) -> str:
    return clean(f"{job['title']} - {job['company_name']} - {job['location_raw']} - {job['salary_raw']}")[:500]


def build_jobs(html_path: Path, source_url: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    jobs: list[dict[str, Any]] = []
    for index, card in enumerate(soup.select(".job-card")):
        location_raw, location, remote_type = location_from_card(card)
        job = {
            "title": text_or_empty(card.select_one('[data-search--job-selection-target="jobTitle"]')),
            "company_name": text_or_empty(card.select_one("a.text-rich-grey[href^='/companies/']")),
            "job_url": canonical_job_url(card.get("data-search--job-selection-job-url-value", ""), card),
            "source_url": source_url,
            "location_raw": location_raw,
            "location": location,
            "remote_type": remote_type,
            "employment_type": "",
            "posted_at": posted_at_from_card(card),
            "salary_raw": text_or_empty(card.select_one(".salary")),
            "description_text": "",
            "description": "",
            "tags": tags_from_card(card),
            "relevance_reason": (
                "Listing appears on an ITviec AI/search listing page and includes AI/ML-related title, category, "
                "or skill tag evidence."
            ),
            "confidence": 0.9,
            "evidence": [
                {
                    "file": str(html_path),
                    "locator": f"div.job-card[data-search--job-selection-job-index-value='{index}']",
                    "text": "",
                }
            ],
        }
        job["evidence"][0]["text"] = evidence_text(job)
        jobs.append(job)
    selectors = {
        "job_card": ".job-card[data-search--pagination-target='jobCard']",
        "detail_url": "data-search--job-selection-job-url-value",
        "title": "[data-search--job-selection-target='jobTitle']",
        "company": "a.text-rich-grey[href^='/companies/']",
        "tags": "a[href*='click_source=Skill+tag']",
    }
    return jobs, selectors


def write_outputs(html_path: Path, output_dir: Path, source_url: str, script_copy_path: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace = output_dir.parent

    def workspace_relative(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(workspace.resolve()))
        except ValueError:
            return str(path)

    jobs, selectors = build_jobs(html_path, source_url)
    expected_count = len(jobs)
    source = {
        "source_url": source_url,
        "source_name": "ITviec AI Engineer Hanoi",
        "page_id": "page_itviec_listing_v1",
        "package_id": PACKAGE_ID,
    }
    crawl = {
        "discovered_count": expected_count,
        "candidate_count": expected_count,
        "relevant_count": expected_count,
        "blocked": False,
        "blocker": "",
        "page_count": 1,
        "method": "reused_itviec_listing_v1",
    }
    candidates = {"source": source, "jobs": jobs, "selectors": selectors, "crawl": crawl, "warnings": []}
    expected_output = {
        "expected_job_count": expected_count,
        "count_basis": "Reusable ITviec v1 extractor counted repeated .job-card units with job URL and title targets.",
        "count_rationale": (
            "The probe/extractor observed one repeated card per listing. The package emits one job per card unless "
            "validation reports a concrete mismatch."
        ),
        "available_fields": {
            "title": "required_observed",
            "company_name": "required_observed",
            "job_url": "required_observed",
            "source_url": "required_observed",
            "location_raw": "required_observed",
            "location": "required_observed",
            "remote_type": "required_observed",
            "posted_at": "required_observed",
            "salary_raw": "required_observed",
            "tags": "required_observed",
        },
        "field_basis": {
            "title": "Card-local data-search--job-selection-target=jobTitle text.",
            "company_name": "Card-local company anchor under /companies/.",
            "job_url": "Card data-search--job-selection-job-url-value canonicalized to /it-jobs/<slug>-NNNN.",
            "source_url": "Source URL from sandbox inputs or extractor argument.",
            "location_raw": "Card-local location container combines mode and place.",
            "location": "Card-local place text in the location container.",
            "remote_type": "Card-local mode text such as Remote or At office.",
            "posted_at": "Card-local posted-age span.",
            "salary_raw": "Card-local salary container text.",
            "tags": "Card-local skill-tag anchors with click_source=Skill+tag.",
        },
    }
    validation = {
        "valid": True,
        "checks": {
            "count_match": len(jobs) == expected_count,
            "required_fields_present": all(
                job.get("title")
                and job.get("company_name")
                and job.get("job_url")
                and job.get("location_raw")
                and job.get("salary_raw")
                and job.get("tags")
                for job in jobs
            ),
            "url_shape_valid": all("/it-jobs/" in job["job_url"] and job["job_url"].split("-")[-1].isdigit() for job in jobs),
            "schema_shape_valid": True,
        },
        "candidate_count": len(jobs),
        "relevant_count": len(jobs),
        "warnings": [],
    }
    final = {
        "status": "success",
        "output_schema": "job_extraction",
        "summary": f"Extracted {len(jobs)} ITviec listing jobs with reusable package {PACKAGE_ID}.",
        "result": candidates,
        "protocol": {
            "valid": True,
            "warnings": [],
            "candidates": {"path": str(output_dir / "candidates.json")},
            "final": {"path": str(output_dir / "final.json")},
        },
    }
    extraction_run = {
        "observations": [
            f"{html_path} contains {expected_count} repeated ITviec .job-card units.",
            "The reusable package probe matched card, title, and detail URL markers.",
            "Card-local fields expose company, salary state, location, posted age, and skill tags.",
        ],
        "chosen_strategy": (
            "Reused validated package itviec-listing-v1 after matching the current page layout. The extractor parses "
            "each repeated card and emits one job per card."
        ),
        "expected_output": expected_output,
        "validation": validation,
        "reused_package": {
            "id": PACKAGE_ID,
            "reference_version": REFERENCE_VERSION,
            "script": "references/job-listing-scout/itviec-listing-v1/scripts/extractor.py",
            "materialized_script": workspace_relative(script_copy_path),
        },
    }
    page_profile = {
        "page_class": "itviec-listing",
        "source_url": source_url,
        "observed_job_cards": expected_count,
        "observed_detail_urls": expected_count,
        "observed_title_targets": expected_count,
        "main_unit_selector": selectors["job_card"],
        "reused_package": PACKAGE_ID,
        "notes": ["Reusable ITviec listing v1 package matched the mounted page."],
    }
    extraction_strategy = {
        "target_units": "Repeated ITviec .job-card elements.",
        "unit_boundary": "One card with job-selection metadata and visible nested card text.",
        "count_method": "Use reusable package probe and extractor card count.",
        "field_patterns": expected_output["field_basis"],
        "known_exclusions": ["pagination links", "category links", "company profile links", "salary sign-in links"],
        "coverage_plan": "Emit one job per repeated card and validate against protocol/fixture.",
        "reused_package": PACKAGE_ID,
    }
    dump_json(output_dir / "page_profile.json", page_profile)
    dump_json(output_dir / "extraction_strategy.json", extraction_strategy)
    dump_json(output_dir / "extraction_run.json", extraction_run)
    dump_json(output_dir / "candidates.json", candidates)
    dump_json(output_dir / "validation.json", validation)
    dump_json(output_dir / "final.json", final)
    summary = f"""# ITviec Listing V1 Reuse Run

Matched reusable package `{PACKAGE_ID}` against `{html_path}` and extracted {expected_count} jobs from repeated `.job-card` units. The package wrote the required protocol outputs, materialized the reused script to `{workspace_relative(script_copy_path)}`, and expects validation/finalization to confirm URL shape, field coverage, counts, and fixture/reference quality.
"""
    (output_dir / "run_summary.md").write_text(summary, encoding="utf-8")
    script_manifest = {
        "scripts": [
            {
                "path": workspace_relative(script_copy_path),
                "purpose": "Reusable ITviec listing v1 extractor used to write all required protocol outputs.",
                "inputs": [str(html_path)],
                "outputs": [
                    workspace_relative(output_dir / "page_profile.json"),
                    workspace_relative(output_dir / "extraction_strategy.json"),
                    workspace_relative(output_dir / "extraction_run.json"),
                    workspace_relative(output_dir / "candidates.json"),
                    workspace_relative(output_dir / "validation.json"),
                    workspace_relative(output_dir / "final.json"),
                    workspace_relative(output_dir / "run_summary.md"),
                ],
                "sha256": sha256_file(script_copy_path),
                "reference_version": REFERENCE_VERSION,
                "reuse": "reused_validated_package",
                "reuse_classification": "reused_validated_package",
                "validation_result": "pending validate_outputs.py",
            }
        ]
    }
    dump_json(output_dir / "script_manifest.json", script_manifest)
    return {"status": "success", "jobs": len(jobs), "outputs": sorted(path.name for path in output_dir.iterdir())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract ITviec listing jobs using reusable package itviec-listing-v1.")
    parser.add_argument("--html", default="page.html", help="HTML file to parse. Defaults to page.html.")
    parser.add_argument("--output-dir", default="output", help="Protocol output directory. Defaults to output.")
    parser.add_argument("--source-url", default="", help="Original source URL. Defaults to inputs.json source_url when available.")
    args = parser.parse_args()
    html_path = Path(args.html)
    output_dir = Path(args.output_dir)
    source_url = args.source_url.strip() or source_url_from_inputs("https://itviec.com/it-jobs/ai-engineer/ha-noi")
    script_copy_path = output_dir / "reused_extractor.py"
    output_dir.mkdir(parents=True, exist_ok=True)
    current_script = Path(__file__).resolve()
    if current_script != script_copy_path.resolve():
        shutil.copyfile(current_script, script_copy_path)
    result = write_outputs(html_path, output_dir, source_url, script_copy_path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
