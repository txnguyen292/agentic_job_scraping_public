# ADK Job Listing Scout

## Mental Model

The scraper agent has four separate layers:

- `SKILL.md` is the behavior guide: what the agent is trying to do and what workflow it should follow.
- `references/` are navigation guides: concrete examples for ATS URL construction, payload reading, and field mapping.
- ADK tools are mechanical actions: fetch/render a page, store large pages in a workspace, delegate page analysis to the sandbox worker, persist validated jobs, record a crawl run, and query stored jobs.
- SQLite is the durable system of record.

## Why Seeds Exist

Seeds are not just a template and not a hidden command. They are concrete examples of source configuration:

- which ATS family a company uses
- which `board_token` or handle identifies the board
- whether the source should be fetched from a live endpoint or local fixture
- how a known-good source should look for deterministic tests

The skill reference `skills/job-listing-scout/references/seed-driven-sources.md` teaches the agent how to turn those examples into actual navigation steps for Greenhouse and Lever.

## Tool Surface

The agent-facing tool surface should stay deliberately basic. `allowed-tools` in `SKILL.md` is the single declarative contract. ADK expects this field as a space-delimited string:

```yaml
allowed-tools: fetch_page render_page fetch_page_to_workspace render_page_to_workspace run_sandbox_agent persist_sandbox_job_extraction upsert_job record_crawl_run query_jobs list_seed_references
```

`src/job_scraper/registry.py` maps those names to Python callables and uses a thin `AllowedToolsSkillToolset` bridge so activated skill tools are exposed from `allowed-tools`. `src/job_scraper/agent.py` directly exports both `root_agent` and `app` for ADK compatibility. Do not duplicate the same tool names under `metadata.adk_additional_tools`.

`fetch_page` and `render_page` are powered by Scrapling in `src/job_scraper/sources/`. `fetch_page` uses Scrapling's HTTP fetcher; `render_page` uses Scrapling's browser-backed dynamic fetcher.

For large or unfamiliar pages, prefer `fetch_page_to_workspace` or `render_page_to_workspace`. These tools save full HTML under `data/page_workspace/<page_id>/page.html` and return metadata only. The main ADK agent should then call `run_sandbox_agent` with a precise task, `page_ids`, and `output_schema: "job_extraction"`. Only the sandbox final JSON enters the main ADK context.

For user-facing scraping requests, the workspace-to-sandbox path is mandatory. `fetch_page` and `render_page` remain available as diagnostics, but they are not a complete extraction workflow and should not be used as the final step when the user asks to scrape jobs.

Sandbox-produced job extraction payloads should be written through `persist_sandbox_job_extraction`, which validates the schema again before SQLite writes.

The nested SandboxAgent is owned by `src/sandbox_page_analyst/openai_agent.py` and uses OpenAI Agents SDK sandbox capabilities: `Shell`, `Filesystem`, `Compaction`, and `Skills`. `src/sandbox_page_analyst/agent.py` is a separate ADK entrypoint for direct sandbox testing. The `Skills` capability lazy-loads `skills/sandbox-page-analyst/`, which contains the sandbox protocol, layout references, schemas, and scripts. The sandbox worker must write `output/page_profile.json`, `output/extraction_strategy.json`, `output/candidates.json`, and `output/validation.json`, then return only a compact final result with protocol file handles and hashes.

The sandbox is not LLM-disabled. It should still use normal SandboxAgent model calls for reasoning, code-writing, inspection planning, and final synthesis. `SandboxPolicy.allow_llm_calls` is intentionally fixed to `true` in v1. `SandboxPolicy.network == "disabled"` means the Docker shell/container cannot browse or fetch remote URLs; the main ADK agent remains responsible for fetching pages into workspace artifacts before delegation.

ADK context stores only page handles, final sandbox results, persisted job summaries, and crawl metadata. The nested sandbox worker owns noisy inspection traces. It may write `output/reference_proposal.*` or `output/skill_patch.json`, but accepted skill changes require human review outside the sandbox run.

Sandbox artifact persistence is always on. `SandboxPolicy.persist_artifacts` is intentionally fixed to `true` in v1, so local audit artifacts and scratch files are written under `data/sandbox_runs/<audit_id>/` without asking for additional permission. ADK responses still receive compact handles only, not raw HTML or terminal transcripts.

The deterministic crawl pipeline can still exist for CLI tests and repeatable local demos, but it should not replace the agent's reasoning path.

## RLM Page Workspace Direction

The next scraper layer should follow an RLM-style page workspace:

- Store full fetched/rendered HTML outside the model context.
- Return a `page_id` instead of full HTML.
- Let a nested sandbox worker inspect `html` and `url` with terminal/filesystem tools.
- Return only compact final structured candidates to the main model.
- Persist normalized AI/ML jobs through SQLite.

See [RLM page workspace scraper plan](../plans/active/02-rlm-page-workspace-scraper.md).
