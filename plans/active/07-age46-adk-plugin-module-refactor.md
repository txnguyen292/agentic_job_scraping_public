# ADK Plugin Module Refactor Plan

> For agentic workers: keep this refactor mechanical. Stop and ask at the first behavior-changing diff.

**Goal:** Reduce `src/job_scraper/adk_plugins.py` into a thin import-compatible facade plus focused implementation modules.

**Base:** Clean AGE-46 worktree at `/Users/tungnguyen/.config/superpowers/worktrees/job_scraping/codex-age-46-refactor-adk-plugins-into-focused-modules`, branch `codex/age-46-refactor-adk-plugins-into-focused-modules`, commit `be986df`.

**Parent context:** AGE-46 is a formal child of AGE-44. The pending GLM 5.2 ADK Web verification stays with AGE-44/T-005 and is not part of this refactor.

## Clarified Import Contract

Existing callers must be able to keep importing from `job_scraper.adk_plugins`. This is a hard acceptance criterion.

That means moved classes and helpers can live in focused modules, but `src/job_scraper/adk_plugins.py` must re-export the public classes that existing tests, registry code, and callers already import. New module-specific tests may import the focused modules directly, but legacy import paths must stay covered.

## Target Shape

- `src/job_scraper/adk_plugins.py`
  - Thin facade and composition surface.
  - Re-exports existing public plugin classes.
- `src/job_scraper/adk_plugins/` or equivalent focused package, only if the Python package layout supports it cleanly.
- `sandbox_guard/`
  - `before_tool.py` for before-tool policy checks.
  - `after_tool.py` for after-tool state transitions.
  - `before_model.py` for context injection.
  - `after_model.py` for response replacement and normalization hooks.
  - `artifacts.py` for artifact persistence and output handling.
  - `compaction.py` for summarization and context compaction helpers.

If a module/package name collides with the existing `adk_plugins.py` facade, prefer a non-conflicting package name that keeps the public facade intact.

## Slice Order

1. Extract `TransientModelRetryPlugin`.
2. Extract `ModelReasoningTelemetryPlugin`.
3. Extract `SandboxOutputGatePlugin`.
4. Extract `SandboxNoteRefinementPlugin`.
5. Split sandbox workflow guard internals under `sandbox_guard/` by lifecycle.
6. Leave `job_scraper.adk_plugins` as a thin facade/composition layer.

## Guardrails

- Do not finish or retest the pending GLM 5.2 ADK Web verification here.
- Do not introduce new raw dict contracts across ADK tool/model boundaries.
- Do not break any existing `job_scraper.adk_plugins` import path.
- Do not broad-rename public classes.
- Do not change behavior while moving code.
- Stop at the first behavior-changing diff and ask before continuing.

## Verification Loop

After each slice:

```bash
uv run pytest tests/test_adk_plugins.py -q
```

Before closeout:

```bash
uv run pytest tests/test_adk_plugins.py -q
uv run pytest -q
```
