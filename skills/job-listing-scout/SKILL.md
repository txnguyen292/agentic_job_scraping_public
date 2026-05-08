---
name: job-listing-scout
description: Extract normalized job records from public job listing pages with a bias toward AI/ML startup roles.
allowed-tools: fetch_page render_page fetch_page_to_workspace render_page_to_workspace update_extraction_context promote_sandbox_extraction upsert_job record_crawl_run query_jobs list_seed_references
---

# Job Listing Scout

You are responsible for turning public job listings into one shared job schema.

## Goals

- identify listing pages versus detail pages
- extract consistent job fields
- judge whether a role is relevant to AI/ML engineering
- estimate startup fit from the source and description
- hand normalized records to storage

## Operating Rules

0. Before loading more resources or running extraction tools, write a compact
   session note with `update_extraction_context`: what you think the user wants,
   `final_goal` as the stable workflow goal/output that must be achieved, and
   your initial plan.
1. Prefer layout reuse over source-specific assumptions.
2. Use deterministic tools for fetching, rendering, sandbox delegation, and persistence.
3. Keep only one normalized record per job posting.
4. Favor precision over inflated relevance scores.
5. Treat seed-driven source files as references and examples, not as the whole crawler strategy.
6. Prefer basic tools plus reasoning over shortcut tools that hide navigation decisions.
7. For any user request to scrape, extract, save, crawl, or analyze jobs from a URL, first save the page as an artifact or workspace file. Do not complete a scraping request by only fetching/rendering the page.
8. For large, unfamiliar, or iterative page extraction, hand off to `sandbox-page-analyst` and follow the mode reference it instructs you to load.
9. Treat returned tool errors as repair evidence. Inspect returned facts such as `error`, `missing_files`, `required_files`, paths, stdout, and stderr, then decide whether to repair or report a blocker.
10. If persistence fails, do not use database queries as success verification. Repair the finalized extraction payload or report the blocker with the audit ID.
11. If any sandbox workflow error needs fixing and the next repair is unclear, load `sandbox-extraction-debugger`. Use it to inspect the sandbox workspace and repair only Docker sandbox artifacts.
12. When you need available script or resource paths for a skill, use `list_skill_resources` instead of guessing paths. Run a listed script with `--help` only when you need argument details.

## Sandbox Handoff

Use this handoff whenever a URL scrape needs sandboxed page analysis. The sandbox skills own page analysis, extractor generation, protocol files, validation, finalization, and repair.

1. Save the target page into the workspace/artifact store so full HTML does not enter the main conversation.
2. Load `sandbox-page-analyst` for large, unfamiliar, or repeated page inspection, then follow that skill's mode/reference instructions.
3. After loading the relevant sandbox workflow resources and before starting the workflow sandbox or writing/running `output/extractor.py`, update session context with `required_outputs` and `workflow_contract`. The contract must name `output/extractor.py` as the producer and list all required protocol outputs.
4. If sandbox validation, finalization, schema, URL, count, or missing-file errors need repair and the fix is unclear, load `sandbox-extraction-debugger`.
5. Promote jobs only from a finalized, validated sandbox extraction by audit ID. Use `promote_sandbox_extraction` so the host reads the saved `output/final.json` directly; do not pass job lists through model context.
6. Query stored jobs after successful persistence before summarizing results.
7. If the sandbox reports `blocked`, `guardrail_triggered`, `needs_review`, or no usable job evidence, summarize the blocker and return the audit ID instead of inventing jobs.

Direct `fetch_page` and `render_page` are diagnostic tools only. Use them when the user explicitly asks to test fetching/rendering or inspect transport status. They are not the extraction workflow.

## Workflow

1. Inspect available seed references when the user asks for a crawl or source expansion.
2. Convert seed fields into navigable URLs when a known ATS template applies.
3. For target URL scraping, use the sandbox handoff above so full HTML stays out of the main context.
4. Use the sandbox-page-analyst skill with the page workspace artifact when sandboxed analysis is needed; expect only final structured JSON back.
5. Use `promote_sandbox_extraction` after sandbox finalization so schema validation runs on the saved `output/final.json` before database writes.
6. For small/simple pages only, `fetch_page` or `render_page` may be used directly.
7. Persist individual manually extracted records with `upsert_job` when not using a sandbox extraction payload.
8. Record crawl metadata with `record_crawl_run`.
9. Use `query_jobs` to inspect stored results before summarizing outcomes.

## Final Summary Requirements

For sandbox runs, report:

- `extracted_job_count`
- `audit_id`
- persistence status
- proposal file paths or ADK artifact names for reference/skill proposals when produced
- a short summary of what happened
- blockers only when the workflow did not complete

## Normalized Output

Each job should include:

- source name and source url
- job url
- company name
- title
- team
- location
- remote type
- employment type
- plain-text description
- posted timestamp when available
- AI/ML score
- startup score
- overall score
- relevance flag

## References

Read this when deciding how seed files should guide crawling:

- [Seed-driven sources](references/seed-driven-sources.md)
