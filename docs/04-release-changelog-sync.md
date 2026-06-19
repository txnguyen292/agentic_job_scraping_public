# Release and Changelog Sync

This repo keeps release information in three places with different responsibilities:

1. `CHANGELOG.md` is the canonical source for human-authored release-note text.
2. GitHub is canonical for code history, commits, tags, PRs, and public export PR artifacts.
3. Linear is the team-facing ledger for what shipped, which issues were involved, and where humans should look after the PR scroll has moved on.

## Current GitHub Flow

`scripts/release_notes.py` reads the `## Unreleased` section from `CHANGELOG.md`, validates that it is non-empty, and renders it into a managed PR block. `.github/workflows/internal-ci.yml` validates release notes and updates internal/public PR bodies.

## Linear Ledger Flow

`uv run python -m scripts.linear_release_ledger render` produces a Markdown release ledger entry from `CHANGELOG.md` and release metadata such as PR URLs, commit SHA, tag, and related AGE issue IDs.

`uv run python -m scripts.linear_release_ledger publish-document` publishes that Markdown into Linear when `LINEAR_API_KEY` is present. Use `--document-id` to update an existing ledger document. Use `--project-id` for the first creation, then save the returned document ID for future idempotent updates.

## Automated Linear Ledger Flow

`.github/workflows/linear-release-ledger.yml` publishes the Linear ledger document from GitHub Actions. On every push to `main`, it renders the current `CHANGELOG.md` Unreleased section and updates the configured Linear document.

The workflow uses:

- `LINEAR_API_KEY`: GitHub Actions secret containing a Linear personal API key.
- `LINEAR_RELEASE_LEDGER_DOCUMENT_ID`: GitHub Actions repository variable containing the Linear document ID to update.
- `LINEAR_RELEASE_LEDGER_PROJECT_ID`: optional repository variable for first-time document creation. Defaults to `610a5be1-a336-4223-ac0f-c58a05606321`.
- `LINEAR_RELEASE_LEDGER_TITLE`: optional repository variable for first-time document creation. Defaults to `Release Ledger`.

If the secret or document ID is missing on a `main` push, the workflow emits a notice and skips publishing instead of breaking the build.

## Official Linear Releases

Linear's official Releases feature requires a Business or Enterprise plan. This repo does not depend on that feature. The current implementation uses Linear Documents instead.

## Approval Rules

Do not add GitHub secrets, enable automatic release workflows, create public PRs, publish public releases, or push public branches without explicit approval in the current thread.

## Recommended Operating Model

Use the Linear ledger document for curated changelog notes generated from `CHANGELOG.md`. Keep the document linked to the same GitHub PRs, commits, and AGE issues.

## First-Time Linear Ledger Setup

The current Linear project ID for this repo is `610a5be1-a336-4223-ac0f-c58a05606321`.

The current Linear release ledger document is:

- Document ID: `70f3b301-3f84-40a0-a21e-754028fdc471`
- URL: https://linear.app/agentic-job-scraping/document/release-ledger-5035eb33f4e1

For a local dry run, render the ledger Markdown before publishing:

```bash
uv run python -m scripts.linear_release_ledger render \
  --release-title "Release 2026-06-05" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Release ledger renderer verified locally." \
  > /tmp/linear-release-ledger.md
```

Create the first Linear project document locally only if the current document is removed or replaced:

```bash
LINEAR_API_KEY="$LINEAR_API_KEY" \
  uv run python -m scripts.linear_release_ledger publish-document \
    --project-id 610a5be1-a336-4223-ac0f-c58a05606321 \
    --document-title "Release Ledger" \
    --input /tmp/linear-release-ledger.md
```

Save the returned document ID for future updates. After that, update the existing document instead of creating another ledger:

```bash
LINEAR_API_KEY="$LINEAR_API_KEY" \
  uv run python -m scripts.linear_release_ledger publish-document \
    --document-id "<linear-document-id>" \
    --input /tmp/linear-release-ledger.md
```

## CI/CD Setup

Add the Linear personal API key as a GitHub Actions secret:

```bash
gh secret set LINEAR_API_KEY
```

Create the first Linear ledger document from GitHub Actions by running the `Linear Release Ledger` workflow manually with `create_document=true`. The run prints the created document JSON. Save the returned `id` as a repository variable:

```bash
gh variable set LINEAR_RELEASE_LEDGER_DOCUMENT_ID --body "<linear-document-id>"
```

After `LINEAR_API_KEY` and `LINEAR_RELEASE_LEDGER_DOCUMENT_ID` are configured, every merge to `main` updates the Linear ledger document automatically.

## Local Verification

Use these checks before requesting a PR:

```bash
uv run pytest tests/test_release_notes.py tests/test_linear_release_ledger.py -q
uv run python scripts/release_notes.py check
uv run python -m scripts.linear_release_ledger render \
  --release-title "Validation dry run" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Validation only." \
  > /tmp/linear-release-ledger.md
```
