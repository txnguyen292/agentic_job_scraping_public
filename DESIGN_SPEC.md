# Job Listing Scout ADK Agent

## Overview

The job scraper should become a Google ADK skill-driven agent while preserving the deterministic SQLite pipeline that already works. The agent uses the `job-listing-scout` skill as its behavioral entrypoint and exposes mechanical tools for fetching pages, reading seed references, running known seed-backed crawls, upserting normalized jobs, recording crawl runs, and querying stored jobs.

Seed-driven crawling remains useful, but it should act as references and examples rather than the full crawler strategy. The agent should reason from page layout, use seed references to construct navigable URLs for known templates, and persist results through basic tools.

## Example Use Cases

- "Crawl the demo seed sources and show the top AI/ML jobs."
  Expected: the agent lists seed references, uses the Greenhouse and Lever URL templates, fetches listing payloads, persists normalized jobs, queries relevant jobs, and summarizes the stored results.
- "Here is a startup careers page URL. Find AI/ML roles."
  Expected: the agent fetches or renders the page, classifies the layout, extracts job records, persists normalized jobs, and reports evidence.
- "What jobs are currently stored for remote ML work?"
  Expected: the agent calls `query_jobs` with relevant filters and summarizes matching records.
- "Add this new Greenhouse board as a source."
  Expected: the agent explains the seed/source update needed and can use existing source conventions as references.

## Tools Required

- `fetch_page(url, timeout)`: fetch public page content with HTTP.
- `render_page(url, timeout)`: reserved browser-like page rendering contract; V1 delegates to normal fetch.
- `list_seed_references(source_file)`: show seed source config as compact references.
- `upsert_job(job, db_path)`: persist an agent-extracted normalized job.
- `record_crawl_run(run, db_path)`: persist crawl metadata for custom agent crawls.
- `query_jobs(...)`: inspect stored jobs for validation and summaries.

## Constraints & Safety Rules

- Keep `SKILL.md` agent-facing; implementation notes belong in code or repo docs.
- Use seeds as references and examples, not as a reason to create one scraper per company.
- Expose the agent to basic mechanical tools; do not hide navigation behind seed-specific shortcut tools.
- Prefer deterministic tools for fetch, render, persistence, and query.
- Use model judgment for unfamiliar layouts, field extraction, link-following choices, and relevance assessment.
- Do not deploy without explicit approval.

## Success Criteria

- The ADK root agent loads the `job-listing-scout` skill through `SkillToolset`.
- The skill frontmatter uses ADK's space-delimited `allowed-tools` field as the single declarative runtime-tool contract.
- The ADK runtime maps `allowed-tools` names to Python callables and exposes them after skill activation.
- Existing pytest coverage still passes.
- ADK-specific tests pass when `google-adk` and the Python 3.13+ runtime are installed.
- The deterministic CLI, SQLite, and Streamlit surfaces remain usable.

## Edge Cases To Handle

- A seed source returns zero jobs.
- A public board token has drifted to 404.
- A career page is not a Greenhouse or Lever API endpoint.
- Extracted job content is missing location or posted date.
- A repeated crawl should upsert rather than duplicate jobs.
