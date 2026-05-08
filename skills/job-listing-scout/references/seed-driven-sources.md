# Seed-Driven Sources

Use seed files as concrete examples, templates, and curated starting points.

The agent should use these references to decide how to navigate known source shapes with basic tools. A seed is not a command to run a hidden crawler. It is a compact description of a known source pattern.

## What Seeds Are For

- known ATS board handles
- source names and company hints
- fixture-backed demo references
- examples of how Greenhouse and Lever payloads map into the normalized job schema
- instructions for constructing navigable URLs from source fields

## What Seeds Are Not

- the full crawler strategy
- a replacement for page inspection
- a reason to write one adapter per company
- a shortcut that hides extraction and persistence decisions from the agent

## Current Seed Files

- `seeds/demo_sources.json`: deterministic fixture-backed crawl for tests and local smoke checks
- `seeds/sources.template.json`: template for live Greenhouse and Lever source handles

## Seed Fields

Each seed can contain:

- `name`: human-readable source name
- `source_type`: known source layout, currently `greenhouse` or `lever`
- `board_token`: ATS board handle used to construct the listing API URL
- `source_url`: explicit URL when no token-based template applies
- `company_name`: normalized company name for stored jobs
- `startup_bias`: scoring hint from 0.0 to 1.0
- `fixture_file`: local fixture path for tests only

## Navigating Greenhouse Seeds

When `source_type` is `greenhouse` and `board_token` is present:

```text
https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
```

Navigation pattern:

1. Build the URL from the board token.
2. Use `fetch_page` to retrieve JSON.
3. Treat `jobs` as the listing collection.
4. For each job, map fields:
   - `title` -> `title`
   - `absolute_url` -> `job_url`
   - `location.name` -> `location_raw`
   - first `departments[].name` -> `team`
   - `content` -> `description_text`
   - `updated_at` -> `posted_at`
5. Persist each normalized record with `upsert_job`.

## Navigating Lever Seeds

When `source_type` is `lever` and `board_token` is present:

```text
https://api.lever.co/v0/postings/{board_token}?mode=json
```

Navigation pattern:

1. Build the URL from the board token.
2. Use `fetch_page` to retrieve JSON.
3. Treat the root array as the listing collection.
4. For each job, map fields:
   - `text` -> `title`
   - `hostedUrl` or `applyUrl` -> `job_url`
   - `categories.location` -> `location_raw`
   - `categories.team` -> `team`
   - `categories.commitment` -> `employment_type`
   - `descriptionPlain` or `description` -> `description_text`
   - `createdAt` -> `posted_at`
5. Persist each normalized record with `upsert_job`.

## Navigating Explicit URLs

When `source_url` is present:

1. Use `fetch_page` first.
2. If the response is incomplete or looks JavaScript-rendered, use `render_page`.
3. Classify the page as listing page, detail page, ATS API response, or unsupported page.
4. Extract job links and detail fields from visible structure and surrounding text.
5. Persist only records with enough evidence for `company_name`, `title`, and `job_url`.

## Agent Behavior

Start from seed references when they exist, then reason from page layout.

For known ATS sources, use the templates above to navigate with `fetch_page`, extract fields, and persist records. For unfamiliar pages, inspect the page, identify job-like structures, extract fields into the normalized schema, and persist the result through tools.

After writing jobs, call `query_jobs` to verify stored results and `record_crawl_run` to log what happened.
