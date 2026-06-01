# Repository Curation Workflow

This repository is maintained as a curated public code sample. The curation
workflow keeps the engineering artifacts that help reviewers understand and reuse
the worker, while excluding local runtime state, credentials, generated data, and
scratch artifacts.

## What Belongs Here

- Source code for the worker, agent tool layer, sandbox analyst, and CLI.
- Skills, schemas, and references that describe reusable extraction behavior.
- Deterministic fixtures and tests that make behavior inspectable.
- Documentation that explains the architecture, workflow boundaries, and
  integration points.

## What Stays Out

- Environment files and credentials.
- Local databases, generated crawl data, and ADK session artifacts.
- Private traces, scratch workspaces, and machine-specific paths.
- Runtime caches and virtual environments.

## Verification

The public tree can be checked with:

```bash
uv run python scripts/sync_public.py verify .
```

The exporter is allowlist-first. It includes only the configured public paths and
fails verification when forbidden files or obvious secret patterns are present.

Preview the configured file set with:

```bash
uv run python scripts/sync_public.py plan --json
```

## Release Discipline

Before publishing changes, review the diff as if this were a client-facing
artifact:

1. Confirm the README describes the worker clearly in the first screen.
2. Confirm links are relative and portable.
3. Run the public-tree verifier.
4. Run the relevant tests for the changed surface.
5. Review the GitHub rendering before sharing the repository link externally.
