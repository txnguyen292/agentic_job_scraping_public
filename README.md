# Job Scraper

Minimal V1 implementation of the earlier plan:

- ADK `job-listing-scout` agent and skill runtime
- seed-driven crawl sources
- thin deterministic fetch and persistence code
- SQLite as the system of record
- a lightweight Streamlit dashboard on top
- a Typer/Rich/Loguru CLI for script-facing workflows
- a reusable `job-listing-scout` skill spec for the agent-facing behavior
- an allowlisted public-export workflow for publishing sanitized snapshots

## Project Shape

```text
job_scraper/
  docs/
  plans/
  skills/job-listing-scout/SKILL.md
  seeds/demo_sources.json
  src/job_scraper/
  tests/fixtures/
```

## Human-Facing Docs

The human interface lives in [docs/](/Users/tungnguyen/personal_projects/job_scraping/docs/index.md) and [plans/](/Users/tungnguyen/personal_projects/job_scraping/plans/index.md).

- [Architecture](/Users/tungnguyen/personal_projects/job_scraping/docs/01-architecture.md)
- [ADK Job Listing Scout](/Users/tungnguyen/personal_projects/job_scraping/docs/02-adk-job-listing-scout.md)
- [Public Export Workflow](/Users/tungnguyen/personal_projects/job_scraping/docs/03-public-export.md)
- [Agentic Scraper Implementation Plan](/Users/tungnguyen/personal_projects/job_scraping/plans/active/01-agentic-scraper-implementation.md)

`.contexts/` remains the agent-operational context layer for task state, handoff, decisions, and lineage. Humans should not have to inspect `.contexts/` to understand the project.

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This repo now targets Python `3.13+`. If `python3.13` is not installed yet, install it first and recreate `.venv`.

The test runner is `pytest`.

Live HTTP fetching and dynamic rendering are powered by Scrapling. After installing dependencies, install Playwright's browser runtime if you plan to use `render_page` against JavaScript-heavy sites:

```bash
playwright install chromium
```

## Sandbox Image

The ADK sandbox terminal uses a project-owned Docker image by default:

```bash
docker build -f docker/sandbox/Dockerfile -t job-scraper-sandbox:py313 .
```

The image keeps runtime network disabled while making approved local parsing tools available inside the container: `bs4`, `lxml`, `parsel`, `jq`, and `rg`. Override the image with `JOB_SCRAPER_SANDBOX_IMAGE` if needed.

Clean up stale project-owned sandbox containers with the sandbox cleanup script. It targets only containers labeled `job_scraper_sandbox=true`; preview first, then remove:

```bash
uv run python skills/sandbox-page-analyst/scripts/sandbox_cleanup.py --max-age-seconds 900
uv run python skills/sandbox-page-analyst/scripts/sandbox_cleanup.py --max-age-seconds 900 --include-orphans --no-dry-run
```

## Public Export

This repo is the private/internal source of truth. If a public repo is created,
publish it through the allowlisted export config instead of mirroring this repo:

```bash
uv run python scripts/sync_public.py plan --json
uv run python scripts/sync_public.py sync ../agentic_job_scraping_public --apply
uv run python scripts/sync_public.py verify ../agentic_job_scraping_public
```

The current public checkout is expected at:

```bash
/Users/tungnguyen/personal_projects/agentic_job_scraping_public
```

Manual publish flow:

1. Run the export plan and review the file list.
2. Sync into the public checkout with `--apply`.
3. Verify the public checkout.
4. Inspect `git status` inside the public checkout.
5. Commit and push from the public checkout only after reviewing the sanitized scope.

The exporter keeps the destination `.git/` directory intact, but excludes private
runtime material such as live `.contexts/`, `.env*`, `data/`, `reports/adk-runs/`,
`src/.adk/`, ADK session artifacts, caches, and local session files.

CI/CD status: workflow scaffolding exists in `.github/workflows/`. On internal
PRs, CI runs tests/context validation/public-export planning, then exports the
sanitized snapshot and opens or updates a PR in
`txnguyen292/agentic_job_scraping_public`. The workflow requires the internal
repo secret `PUBLIC_REPO_SYNC_TOKEN`. Both repos need an initial `main` commit
before the PR automation can run reliably.

See [docs/03-public-export.md](/Users/tungnguyen/personal_projects/job_scraping/docs/03-public-export.md) for the full workflow.

## ADK Agent

The scraper now has an ADK agent layer around the deterministic tools:

- [DESIGN_SPEC.md](/Users/tungnguyen/personal_projects/job_scraping/DESIGN_SPEC.md)
- [src/job_scraper/agent.py](/Users/tungnguyen/personal_projects/job_scraping/src/job_scraper/agent.py)
- [src/job_scraper/adk_tools.py](/Users/tungnguyen/personal_projects/job_scraping/src/job_scraper/adk_tools.py)
- [skills/job-listing-scout/SKILL.md](/Users/tungnguyen/personal_projects/job_scraping/skills/job-listing-scout/SKILL.md)

Seed files are now treated as ADK skill references and examples. The agent can run deterministic seed-backed crawls, inspect pages, persist normalized jobs, and query SQLite through tools.

After installing Python 3.13 and dependencies, launch ADK Web with:

```bash
adk web src
```

## Demo Crawl

The demo source file uses local fixtures so the pipeline can be tested without depending on live boards.

```bash
job-scraper init-db
job-scraper crawl --source-file seeds/demo_sources.json
job-scraper top --limit 10
```

For machine-readable output during automation or verification:

```bash
job-scraper crawl --source-file seeds/demo_sources.json --json
job-scraper top --relevant-only --json
```

## Live Sources

Public ATS board tokens drift over time, so the repo ships a template instead of hard-coded live company handles.

1. Copy `seeds/sources.template.json` to `seeds/sources.json`.
2. Fill in real `board_token` values for `greenhouse` or `lever` sources.
3. Run:

```bash
job-scraper crawl --source-file seeds/sources.json
```

## Dashboard

```bash
streamlit run src/job_scraper/dashboard -- --db data/jobs.db
```

The dashboard is intentionally thin. It reads from SQLite, exposes search and score filters, and shows source health plus crawl history.

## Recreate The Environment With Python 3.13

```bash
cd /Users/tungnguyen/personal_projects/job_scraping
rm -rf .venv
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
pytest
```
