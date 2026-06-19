# Job Scraper Architecture

## Purpose

The project is a small agentic job scraper. It collects job postings from ATS-backed career boards, normalizes them into SQLite, scores them for AI/ML relevance and startup fit, and exposes the results through a CLI and dashboard.

## Current Shape

- `skills/job-listing-scout/` defines the ADK skill behavior and references the navigation guide.
- `src/job_scraper/agent.py` directly defines the job scraper ADK `root_agent` and `app`.
- `src/job_scraper/adk_tools.py` exposes basic mechanical tools for fetch, render, persistence, run tracking, and querying.
- `src/sandbox_page_analyst/agent.py` is the standalone ADK entrypoint for direct sandbox page-analysis runs.
- `src/sandbox_page_analyst/openai_agent.py` owns the OpenAI Agents SDK sandbox worker definition.
- `src/sandbox_page_analyst/runtime.py` owns sandbox run config, audit records, hosted trace metadata, and final-output validation.
- `src/job_scraper/sources/` powers fetch/render with Scrapling and keeps deterministic Greenhouse and Lever source normalization for tests and scripted crawls.
- `src/job_scraper/models/` owns domain dataclasses for sources, jobs, and crawl results.
- `src/job_scraper/db/` owns SQLite schema and upsert/query behavior.
- `src/job_scraper/pipeline/` coordinates deterministic crawl runs.
- `src/job_scraper/utils/` owns support utilities such as scoring and extraction comparison.
- `src/job_scraper/cli.py` provides Typer CLI commands with JSON output for automation.
- `src/job_scraper/dashboard/` provides a thin Streamlit reader over SQLite.

## Design Boundary

The ADK agent should not receive a high-level "scrape everything" shortcut as its main interface. The skill guides behavior, references show ATS navigation patterns, and tools stay basic enough for the agent to decide the steps.

Seeds are examples and references. They can provide known `board_token` values and source shapes, but they are not the whole scraping strategy.

Scrapling is an implementation detail behind the basic fetch/render tools. It should improve page retrieval and JavaScript rendering without replacing the ADK skill, source references, SQLite persistence, or deterministic test pipeline.

The sandbox page analyst is intentionally a separate package from the job scraper. The job scraper decides what page to fetch and what final schema it needs; the sandbox page analyst owns messy page inspection, tool execution, OpenAI hosted tracing, and compact final output.

## Runtime

The project targets Python 3.13+. The repo-local `.venv` should be recreated with Python 3.13 before validating ADK runtime behavior.

## Verification

Use the deterministic test path first:

```bash
PYTHONPATH=src .venv/bin/pytest -q
```

After Python 3.13 and ADK dependencies are installed, validate the agent path with ADK Web:

```bash
adk web src
```
