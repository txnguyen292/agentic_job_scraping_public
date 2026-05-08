from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional

from job_scraper.models import CrawlRunResult, NormalizedJob, SourceConfig


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    source_name TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    board_token TEXT,
    source_url TEXT NOT NULL,
    company_name TEXT,
    startup_bias REAL NOT NULL DEFAULT 0.5,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    job_key TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    job_url TEXT NOT NULL,
    company_name TEXT NOT NULL,
    title TEXT NOT NULL,
    team TEXT,
    location_raw TEXT,
    location_country TEXT,
    location_city TEXT,
    remote_type TEXT,
    employment_type TEXT,
    description_text TEXT,
    posted_at TEXT,
    scraped_at TEXT NOT NULL,
    ai_ml_score REAL NOT NULL,
    startup_score REAL NOT NULL,
    overall_score REAL NOT NULL,
    is_relevant INTEGER NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_overall_score ON jobs (overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_relevant ON jobs (is_relevant, overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_company_name ON jobs (company_name);
CREATE INDEX IF NOT EXISTS idx_jobs_scraped_at ON jobs (scraped_at DESC);

CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    source_count INTEGER NOT NULL,
    discovered_count INTEGER NOT NULL,
    written_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    notes_json TEXT NOT NULL
);
"""


def ensure_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def upsert_source(conn: sqlite3.Connection, source: SourceConfig, synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            source_name, source_type, board_token, source_url, company_name, startup_bias, last_synced_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_name) DO UPDATE SET
            source_type = excluded.source_type,
            board_token = excluded.board_token,
            source_url = excluded.source_url,
            company_name = excluded.company_name,
            startup_bias = excluded.startup_bias,
            last_synced_at = excluded.last_synced_at
        """,
        (
            source.name,
            source.source_type,
            source.board_token,
            source.source_url or "",
            source.company_name,
            source.startup_bias,
            synced_at,
        ),
    )


def upsert_job(conn: sqlite3.Connection, job: NormalizedJob) -> None:
    conn.execute(
        """
        INSERT INTO jobs (
            job_key, source_name, source_type, source_url, job_url, company_name, title, team,
            location_raw, location_country, location_city, remote_type, employment_type,
            description_text, posted_at, scraped_at, ai_ml_score, startup_score, overall_score,
            is_relevant, status, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_key) DO UPDATE SET
            source_name = excluded.source_name,
            source_type = excluded.source_type,
            source_url = excluded.source_url,
            job_url = excluded.job_url,
            company_name = excluded.company_name,
            title = excluded.title,
            team = excluded.team,
            location_raw = excluded.location_raw,
            location_country = excluded.location_country,
            location_city = excluded.location_city,
            remote_type = excluded.remote_type,
            employment_type = excluded.employment_type,
            description_text = excluded.description_text,
            posted_at = excluded.posted_at,
            scraped_at = excluded.scraped_at,
            ai_ml_score = excluded.ai_ml_score,
            startup_score = excluded.startup_score,
            overall_score = excluded.overall_score,
            is_relevant = excluded.is_relevant,
            status = excluded.status,
            metadata_json = excluded.metadata_json
        """,
        (
            job.job_key,
            job.source_name,
            job.source_type,
            job.source_url,
            job.job_url,
            job.company_name,
            job.title,
            job.team,
            job.location_raw,
            job.location_country,
            job.location_city,
            job.remote_type,
            job.employment_type,
            job.description_text,
            job.posted_at,
            job.scraped_at,
            job.ai_ml_score,
            job.startup_score,
            job.overall_score,
            int(job.is_relevant),
            job.status,
            job.metadata_json,
        ),
    )


def record_crawl_run(conn: sqlite3.Connection, result: CrawlRunResult) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO crawl_runs (
            run_id, started_at, finished_at, status, source_count, discovered_count,
            written_count, error_count, notes_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.run_id,
            result.started_at,
            result.finished_at,
            result.status,
            result.source_count,
            result.discovered_count,
            result.written_count,
            result.error_count,
            result.notes_json,
        ),
    )


def query_jobs(
    conn: sqlite3.Connection,
    keyword: str = "",
    relevant_only: bool = False,
    min_score: Optional[float] = None,
    limit: int = 100,
    source_name: str = "",
) -> List[sqlite3.Row]:
    clauses = []
    params: List[object] = []

    if keyword:
        clauses.append("(title LIKE ? OR company_name LIKE ? OR description_text LIKE ?)")
        needle = f"%{keyword}%"
        params.extend([needle, needle, needle])

    if relevant_only:
        clauses.append("is_relevant = 1")

    if min_score is not None:
        clauses.append("overall_score >= ?")
        params.append(min_score)

    if source_name:
        clauses.append("source_name = ?")
        params.append(source_name)

    sql = "SELECT * FROM jobs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY overall_score DESC, scraped_at DESC LIMIT ?"
    params.append(limit)

    return list(conn.execute(sql, params))


def job_metrics(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(
        """
        SELECT
            COUNT(*) AS total_jobs,
            SUM(CASE WHEN is_relevant = 1 THEN 1 ELSE 0 END) AS relevant_jobs,
            COALESCE(MAX(scraped_at), '') AS last_scraped_at
        FROM jobs
        """
    ).fetchone()


def source_health(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            s.source_name,
            s.source_type,
            s.company_name,
            s.last_synced_at,
            COUNT(j.job_key) AS job_count,
            COALESCE(MAX(j.scraped_at), '') AS latest_job_scrape
        FROM sources AS s
        LEFT JOIN jobs AS j ON j.source_name = s.source_name
        GROUP BY s.source_name, s.source_type, s.company_name, s.last_synced_at
        ORDER BY latest_job_scrape DESC, s.source_name ASC
        """
    )


def crawl_history(conn: sqlite3.Connection, limit: int = 20) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM crawl_runs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    )
