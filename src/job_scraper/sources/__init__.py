from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List

from job_scraper.models import NormalizedJob, SourceConfig
from job_scraper.utils.scoring import (
    classify_remote_type,
    compute_overall_score,
    score_ai_ml_relevance,
    score_startup_fit,
    split_location,
    strip_html,
)


USER_AGENT = "job-scraper/0.1 (+https://local.workspace)"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_sources(source_file: str) -> List[SourceConfig]:
    source_path = Path(source_file).resolve()
    with source_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return [SourceConfig.from_dict(item, str(source_path.parent)) for item in payload]


def fetch_page(url: str, timeout: int = 20) -> str:
    Fetcher, _ = _load_scrapling_fetchers()
    response = Fetcher.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        stealthy_headers=True,
    )
    return _scrapling_response_text(response)


def render_page(url: str, timeout: int = 20) -> str:
    _, DynamicFetcher = _load_scrapling_fetchers()
    response = _run_in_worker_thread(
        DynamicFetcher.fetch,
        url,
        timeout=timeout * 1000,
        headless=True,
        disable_resources=True,
        network_idle=True,
        useragent=USER_AGENT,
    )
    return _scrapling_response_text(response)


def _fetch_json(url: str, timeout: int = 20) -> Any:
    return json.loads(fetch_page(url, timeout=timeout))


def _load_scrapling_fetchers() -> tuple[Any, Any]:
    try:
        from scrapling.fetchers import DynamicFetcher, Fetcher
    except ImportError as exc:
        raise RuntimeError(
            "Scrapling is required for live fetching. Install project dependencies with "
            "`python -m pip install -e .` inside the repo-local Python 3.13 .venv."
        ) from exc
    return Fetcher, DynamicFetcher


def _scrapling_response_text(response: Any) -> str:
    status = int(getattr(response, "status", 200) or 200)
    if status >= 400:
        reason = getattr(response, "reason", "")
        raise RuntimeError(f"Scrapling fetch failed with HTTP {status}: {reason}")

    body = getattr(response, "body", b"")
    if isinstance(body, str):
        return body

    encoding = getattr(response, "encoding", None) or "utf-8"
    return bytes(body).decode(encoding, errors="replace")


def _run_in_worker_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run sync browser work away from ADK's asyncio event-loop thread."""
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-scraper-render") as executor:
        return executor.submit(func, *args, **kwargs).result()


def _load_fixture(source: SourceConfig) -> Any:
    fixture_path = Path(source.base_dir, source.fixture_file).resolve()
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def derive_source_url(source: SourceConfig) -> str:
    if source.source_url:
        return source.source_url
    if source.source_type == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{source.board_token}/jobs?content=true"
    if source.source_type == "lever":
        return f"https://api.lever.co/v0/postings/{source.board_token}?mode=json"
    raise ValueError(f"Unsupported source_type: {source.source_type}")


def _stable_job_key(source: SourceConfig, job_url: str, title: str, company_name: str) -> str:
    material = "|".join(
        [
            source.source_type,
            source.board_token or source.source_url or source.name,
            job_url,
            title.strip().lower(),
            company_name.strip().lower(),
        ]
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _to_iso8601(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _greenhouse_team(job_payload: Dict[str, Any]) -> str:
    departments = job_payload.get("departments") or []
    if departments:
        return departments[0].get("name", "")
    offices = job_payload.get("offices") or []
    if offices:
        return offices[0].get("name", "")
    return ""


def _normalize_greenhouse_job(source: SourceConfig, payload: Dict[str, Any]) -> NormalizedJob:
    description_text = strip_html(payload.get("content", ""))
    location_raw = ((payload.get("location") or {}).get("name") or "").strip()
    remote_type = classify_remote_type(location_raw, description_text)
    city, country = split_location(location_raw)
    title = str(payload.get("title", "")).strip()
    company_name = source.company_name or source.name
    ai_ml_score, is_relevant = score_ai_ml_relevance(title, description_text)
    startup_score = score_startup_fit(company_name, description_text, source.startup_bias)
    overall_score = compute_overall_score(ai_ml_score, startup_score, remote_type)
    job_url = payload.get("absolute_url") or payload.get("url") or ""
    metadata = {
        "raw_id": payload.get("id"),
        "departments": [item.get("name", "") for item in payload.get("departments", [])],
        "offices": [item.get("name", "") for item in payload.get("offices", [])],
    }
    return NormalizedJob(
        job_key=_stable_job_key(source, job_url, title, company_name),
        source_name=source.name,
        source_type=source.source_type,
        source_url=derive_source_url(source),
        job_url=job_url,
        company_name=company_name,
        title=title,
        team=_greenhouse_team(payload),
        location_raw=location_raw,
        location_country=country,
        location_city=city,
        remote_type=remote_type,
        employment_type="",
        description_text=description_text,
        posted_at=_to_iso8601(payload.get("updated_at")),
        scraped_at=utc_now(),
        ai_ml_score=ai_ml_score,
        startup_score=startup_score,
        overall_score=overall_score,
        is_relevant=is_relevant,
        metadata_json=json.dumps(metadata, sort_keys=True),
    )


def _normalize_lever_job(source: SourceConfig, payload: Dict[str, Any]) -> NormalizedJob:
    categories = payload.get("categories") or {}
    description_text = strip_html(payload.get("descriptionPlain") or payload.get("description") or "")
    location_raw = str(categories.get("location") or "").strip()
    remote_type = classify_remote_type(location_raw, description_text)
    city, country = split_location(location_raw)
    title = str(payload.get("text", "")).strip()
    company_name = source.company_name or source.name
    ai_ml_score, is_relevant = score_ai_ml_relevance(title, description_text)
    startup_score = score_startup_fit(company_name, description_text, source.startup_bias)
    overall_score = compute_overall_score(ai_ml_score, startup_score, remote_type)
    job_url = payload.get("hostedUrl") or payload.get("applyUrl") or ""
    metadata = {
        "raw_id": payload.get("id"),
        "workplaceType": payload.get("workplaceType", ""),
        "categories": categories,
    }
    return NormalizedJob(
        job_key=_stable_job_key(source, job_url, title, company_name),
        source_name=source.name,
        source_type=source.source_type,
        source_url=derive_source_url(source),
        job_url=job_url,
        company_name=company_name,
        title=title,
        team=str(categories.get("team") or ""),
        location_raw=location_raw,
        location_country=country,
        location_city=city,
        remote_type=remote_type,
        employment_type=str(categories.get("commitment") or ""),
        description_text=description_text,
        posted_at=_to_iso8601(payload.get("createdAt")),
        scraped_at=utc_now(),
        ai_ml_score=ai_ml_score,
        startup_score=startup_score,
        overall_score=overall_score,
        is_relevant=is_relevant,
        metadata_json=json.dumps(metadata, sort_keys=True),
    )


def fetch_source_payload(source: SourceConfig) -> Any:
    if source.fixture_file:
        return _load_fixture(source)
    return _fetch_json(derive_source_url(source))


def crawl_source(source: SourceConfig) -> Iterable[NormalizedJob]:
    payload = fetch_source_payload(source)
    if source.source_type == "greenhouse":
        jobs = payload.get("jobs", [])
        return [_normalize_greenhouse_job(source, job_payload) for job_payload in jobs]
    if source.source_type == "lever":
        return [_normalize_lever_job(source, job_payload) for job_payload in payload]
    raise ValueError(f"Unsupported source_type: {source.source_type}")
