from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_scraper.cli import app
from job_scraper.db import ensure_db
from job_scraper.pipeline import run_crawl


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILE = PROJECT_ROOT / "seeds" / "demo_sources.json"
RUNNER = CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


def test_demo_crawl_writes_jobs_and_deduplicates(db_path: Path) -> None:
    first_run = run_crawl(str(SOURCE_FILE), str(db_path))
    second_run = run_crawl(str(SOURCE_FILE), str(db_path))

    assert first_run.discovered_count == 4
    assert first_run.error_count == 0
    assert second_run.error_count == 0

    conn = ensure_db(str(db_path))
    try:
        job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        relevant_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_relevant = 1").fetchone()[0]
        finance_row = conn.execute(
            "SELECT overall_score FROM jobs WHERE title = 'Finance Manager'"
        ).fetchone()
    finally:
        conn.close()

    assert job_count == 4
    assert relevant_count >= 2
    assert finance_row is not None


def test_crawl_command_can_emit_json(db_path: Path) -> None:
    result = RUNNER.invoke(
        app,
        [
            "crawl",
            "--db",
            str(db_path),
            "--source-file",
            str(SOURCE_FILE),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["discovered_count"] == 4
    assert payload["written_count"] == 4


def test_top_command_can_emit_json(db_path: Path) -> None:
    run_crawl(str(SOURCE_FILE), str(db_path))

    result = RUNNER.invoke(
        app,
        [
            "top",
            "--db",
            str(db_path),
            "--relevant-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert len(payload["items"]) == 2
    assert payload["items"][0]["title"] == "Machine Learning Engineer"
