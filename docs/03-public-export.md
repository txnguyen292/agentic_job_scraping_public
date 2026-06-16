# Public Export Workflow

This project keeps the private/internal repo as the source of truth. A public
repo, if created, should be a sanitized snapshot derived from this repo, not a
hand-maintained fork and not a raw mirror.

## Why

The internal repo intentionally contains agent-operational material such as
`.contexts/`, ADK runtime artifacts, local reports, traces, and scratch data.
Those files are useful for development but should not be published by default.

## Source Of Truth

- Internal repo: `agentic_job_scraping`
- Optional public repo: `agentic_job_scraping_public`
- Public snapshots are generated from the internal repo using
  `public_export.toml`.

## Export Rules

The export is allowlist-first:

- Include only the paths listed in `public_export.toml`.
- Always exclude private/runtime paths such as `.contexts/`, `.env*`, `data/`,
  `reports/adk-runs/`, `src/.adk/`, caches, and local session files.
- Verify the exported tree before publishing.

## Usage

Preview the export without writing anything:

```bash
uv run python scripts/sync_public.py plan --json
```

Dry-run a sync to a separate public checkout:

```bash
uv run python scripts/sync_public.py sync ../agentic_job_scraping_public
```

Write the sanitized snapshot:

```bash
uv run python scripts/sync_public.py sync ../agentic_job_scraping_public --apply
```

Verify an existing public checkout:

```bash
uv run python scripts/sync_public.py verify ../agentic_job_scraping_public
```

The script deliberately does not push by default. After reviewing the exported
tree, commit and push from the public checkout or let a dedicated CI workflow do
that in a later step.

## Automation Status

Public export has a GitHub Actions workflow scaffold in the internal repo:

- `.github/workflows/internal-ci.yml` runs tests, validates `.contexts/`, checks
  the public export plan, exports a sanitized snapshot on internal PRs, and opens
  or updates a PR in `txnguyen292/agentic_job_scraping_public`.
- `.github/workflows/public-export-verify.yml` is included in the public export
  and validates exporter changes on public PRs.

The workflow requires this internal-repo secret:

```text
PUBLIC_REPO_SYNC_TOKEN
```

That token must be able to write contents and pull requests in
`txnguyen292/agentic_job_scraping_public`.

The automated workflow runs the same allowlisted export and verification steps:

```bash
uv run python scripts/sync_public.py plan --json
uv run python scripts/sync_public.py sync <public-checkout> --apply
uv run python scripts/sync_public.py verify <public-checkout>
```

The workflow opens a public PR; it does not merge directly into public `main`.
Both repos need an initial `main` commit before this PR automation can run
reliably.

## What Not To Do

Do not use `git push --mirror` for the public repo. A raw mirror can publish
private history, generated artifacts, or local operational state.
