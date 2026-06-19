# Agentic Job Scraper

A backend reference implementation for LLM-assisted job extraction workflows:
skill-guided orchestration, sandboxed page analysis, structured validation,
SQLite persistence, and repeatable tests.

The repository is organized as a portable worker architecture. It shows how to
turn noisy career pages and ATS feeds into validated job records, with clear
handoffs between the agent, extraction sandbox, deterministic pipeline, storage
layer, and reporting surfaces.

## What It Demonstrates

- Agent workflow design with Google ADK skills and explicit tool contracts.
- Sandboxed page analysis for layouts that need inspection, scripting, and repair.
- Structured outputs validated before promotion or persistence.
- Deterministic Greenhouse, Lever, and fixture-backed extraction paths.
- SQLite as a simple integration point for downstream systems and dashboards.
- Typer/Rich/Loguru CLI commands suitable for automation.
- Regression tests around sources, persistence, sandbox protocol, public export,
  and release discipline.

## Architecture At A Glance

| Layer | Purpose | Key files |
| --- | --- | --- |
| Agent runtime | Skill-guided orchestration over mechanical tools | [`src/job_scraper/agent.py`](src/job_scraper/agent.py), [`src/job_scraper/adk_tools.py`](src/job_scraper/adk_tools.py) |
| Extraction sandbox | Isolated page inspection, script execution, validation, and audit records | [`src/sandbox_page_analyst/runtime.py`](src/sandbox_page_analyst/runtime.py), [`skills/sandbox-page-analyst/`](skills/sandbox-page-analyst/) |
| Source pipeline | Deterministic fetch, normalize, score, and persist flow | [`src/job_scraper/pipeline/`](src/job_scraper/pipeline/), [`src/job_scraper/sources/`](src/job_scraper/sources/) |
| Storage | SQLite schema, upserts, crawl history, and query paths | [`src/job_scraper/db/`](src/job_scraper/db/), [`src/job_scraper/models/`](src/job_scraper/models/) |
| Operator surfaces | CLI and dashboard for running and inspecting workflows | [`src/job_scraper/cli.py`](src/job_scraper/cli.py), [`src/job_scraper/dashboard/`](src/job_scraper/dashboard/) |
| Verification | Fixtures, protocol checks, export checks, and release-note gates | [`tests/`](tests/), [`tests/fixtures/`](tests/fixtures/) |

## Workflow

1. Load source references or a target careers page.
2. Fetch or render the page using deterministic tools.
3. Use the sandbox analyst when extraction needs page inspection or custom script
   work.
4. Validate structured extraction output against the expected job schema and run
   metadata.
5. Persist normalized jobs and crawl state into SQLite.
6. Query stored records through the CLI, dashboard, or downstream integration.

## Integration Points

- Use `job-scraper crawl` and `job-scraper top` as scriptable CLI entrypoints.
- Reuse the ADK tool layer when embedding the worker in a larger agent workflow.
- Reuse the sandbox page-analysis package for extraction tasks that need
  isolated scripting and validation.
- Treat SQLite as the handoff boundary for dashboards, enrichment jobs, or CRM
  syncs.
- Extend source adapters under `src/job_scraper/sources/` for additional ATS or
  custom career-page patterns.

## Quickstart

```bash
uv sync --frozen
```

The project targets Python `3.13+`. If you are not using `uv`, create a Python
3.13 virtual environment and install the package in editable mode:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

## Run The Demo Pipeline

The demo source file uses local fixtures, so the pipeline can be exercised
without depending on live job boards.

```bash
job-scraper init-db
job-scraper crawl --source-file seeds/demo_sources.json
job-scraper top --limit 10
```

For automation:

```bash
job-scraper crawl --source-file seeds/demo_sources.json --json
job-scraper top --relevant-only --json
```

## Live Source Template

Public ATS board tokens drift, so the repo ships a template instead of hard-coded
company handles.

1. Copy `seeds/sources.template.json` to `seeds/sources.json`.
2. Fill in real `board_token` values for `greenhouse` or `lever` sources.
3. Run `job-scraper crawl --source-file seeds/sources.json`.

## ADK Runtime

The ADK agent wraps the deterministic tools with a skill-guided workflow:

- [`DESIGN_SPEC.md`](DESIGN_SPEC.md)
- [`skills/job-listing-scout/SKILL.md`](skills/job-listing-scout/SKILL.md)
- [`src/job_scraper/agent.py`](src/job_scraper/agent.py)
- [`src/job_scraper/adk_tools.py`](src/job_scraper/adk_tools.py)

After installing dependencies:

```bash
adk web src
```

## Sandbox Worker

The sandbox worker uses a project-owned Docker image for isolated extraction
support:

```bash
docker build -f docker/sandbox/Dockerfile -t job-scraper-sandbox:py313 .
```

The image keeps runtime network disabled while providing approved parsing tools
inside the container: `bs4`, `lxml`, `parsel`, `jq`, and `rg`.

Stale project-owned containers can be inspected and removed with:

```bash
uv run python skills/sandbox-page-analyst/scripts/sandbox_cleanup.py --max-age-seconds 900
uv run python skills/sandbox-page-analyst/scripts/sandbox_cleanup.py --max-age-seconds 900 --include-orphans --no-dry-run
```

## Dashboard

```bash
streamlit run src/job_scraper/dashboard -- --db data/jobs.db
```

The dashboard is intentionally thin: it reads from SQLite, exposes search and
score filters, and shows source health plus crawl history.

## Test And Verify

```bash
uv run pytest
uv run python scripts/sync_public.py verify .
```

Release notes are maintained as per-PR fragments in `release-notes/unreleased/`.
Every PR should add or update one fragment, including an explicit
`No user-facing changes.` fragment for internal-only work. PR automation renders
only fragments changed by that PR; `CHANGELOG.md` remains the cumulative release
history.

## Scope

This repository is intentionally focused on the backend worker pattern:
orchestration, extraction, validation, persistence, and verification. Product
packaging, hosted deployment, and customer-specific connectors are expected to be
adapted to the target workflow.

## More Documentation

- [Architecture](docs/01-architecture.md)
- [ADK Job Listing Scout](docs/02-adk-job-listing-scout.md)
