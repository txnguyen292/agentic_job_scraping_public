from __future__ import annotations

import json
import uuid
from typing import Dict

from loguru import logger

from job_scraper.db import ensure_db, record_crawl_run, upsert_job, upsert_source
from job_scraper.models import CrawlRunResult
from job_scraper.sources import crawl_source, load_sources, utc_now


def run_crawl(source_file: str, db_path: str) -> CrawlRunResult:
    started_at = utc_now()
    sources = load_sources(source_file)
    logger.debug("Loaded {} sources from {}", len(sources), source_file)
    conn = ensure_db(db_path)
    discovered_count = 0
    written_count = 0
    error_count = 0
    source_results: Dict[str, object] = {}

    try:
        for source in sources:
            upsert_source(conn, source, synced_at=started_at)
            try:
                jobs = list(crawl_source(source))
                logger.debug("Crawled source {} with {} jobs", source.name, len(jobs))
                discovered_count += len(jobs)
                for job in jobs:
                    upsert_job(conn, job)
                    written_count += 1
                source_results[source.name] = {
                    "status": "ok",
                    "job_count": len(jobs),
                }
            except Exception as exc:
                logger.opt(exception=exc).warning("Failed to crawl source {}", source.name)
                error_count += 1
                source_results[source.name] = {
                    "status": "error",
                    "error": str(exc),
                }
        status = "success" if error_count == 0 else "partial_success"
        result = CrawlRunResult(
            run_id=str(uuid.uuid4()),
            started_at=started_at,
            finished_at=utc_now(),
            status=status,
            source_count=len(sources),
            discovered_count=discovered_count,
            written_count=written_count,
            error_count=error_count,
            notes_json=json.dumps(source_results, sort_keys=True),
            source_results=source_results,
        )
        record_crawl_run(conn, result)
        conn.commit()
        return result
    finally:
        conn.close()
