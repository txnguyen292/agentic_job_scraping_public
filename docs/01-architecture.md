# Job Scraper Architecture

## Purpose

Agentic Job Scraper is a backend worker for LLM-assisted job data extraction. It
combines deterministic source adapters with an agent workflow that can inspect
pages, run sandboxed extraction scripts, validate structured records, and persist
the resulting jobs into SQLite.

The design goal is portability: the worker can be used from a CLI, embedded in an
agent runtime, or adapted into a larger enrichment and reporting pipeline.

## System Model

```text
source references / careers pages
        |
        v
fetch + render tools
        |
        v
agent workflow + sandbox analyst
        |
        v
validated job records + crawl metadata
        |
        v
SQLite + query/reporting surfaces
```

## Core Components

- `skills/job-listing-scout/` defines the agent-facing workflow contract and
  navigation guidance.
- `src/job_scraper/agent.py` defines the ADK root agent and application entry.
- `src/job_scraper/adk_tools.py` exposes mechanical tools for fetch, render,
  persistence, run tracking, and querying.
- `src/sandbox_page_analyst/` owns isolated page inspection, script execution,
  final-output validation, and audit-oriented runtime state.
- `src/job_scraper/sources/` handles Greenhouse, Lever, and fixture-backed source
  normalization.
- `src/job_scraper/models/` defines source, job, and crawl-result data models.
- `src/job_scraper/db/` owns SQLite schema, upserts, and query behavior.
- `src/job_scraper/pipeline/` coordinates deterministic crawl runs.
- `src/job_scraper/cli.py` provides automation-friendly commands.
- `src/job_scraper/dashboard/` reads persisted data for lightweight inspection.

## Workflow Boundary

The agent does not receive a broad "scrape everything" shortcut. Instead, the
skill defines the workflow, source references provide examples, and tools stay
mechanical enough that the agent must make explicit decisions about page shape,
extraction strategy, validation, and persistence.

This keeps the system easier to port:

- deterministic adapters remain testable without a model call;
- the sandbox worker can be reused for other structured extraction tasks;
- SQLite can be replaced or replicated behind a clean persistence boundary;
- validation failures become repair signals rather than silent data drift.

## Verification Strategy

Use deterministic tests first:

```bash
uv run pytest
```

Use public-export verification before sharing a snapshot:

```bash
uv run python scripts/sync_public.py verify .
```

For runtime inspection after dependencies are installed:

```bash
adk web src
```
