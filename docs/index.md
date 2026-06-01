# Job Scraper Docs

This documentation is organized for engineers evaluating how the worker could fit
into an existing automation stack.

## Start Here

1. [Architecture](01-architecture.md)
2. [ADK Job Listing Scout](02-adk-job-listing-scout.md)

## Evaluate The Repo

- Agent contract: `skills/job-listing-scout/SKILL.md`
- Tool boundary: `src/job_scraper/adk_tools.py`
- Sandbox worker: `src/sandbox_page_analyst/`
- Persistence layer: `src/job_scraper/db/`
- Verification suite: `tests/`

## Maintainer Reference

- [Repository Curation Workflow](03-public-export.md)
