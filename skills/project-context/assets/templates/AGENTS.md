# Project Context Protocol

This repo uses the `.contexts/` convention for coding-agent context.

## Context Rules

- At the start of nontrivial project-related work, use the `project-context` workflow.
- Before meaningful repo work, run `.contexts/bin/context_overview`.
- If `.contexts/` is missing, create or repair it using the `project-context` skill.
- Use `.contexts/bin/*` tools first.
- Do not read `.contexts/` files directly unless tool output is insufficient or the tools are unavailable.
- Load only enough context to act safely.
- Decide whether the minimal context is sufficient before loading more resources.
- After meaningful repo work, update handoff/lineage and validate context.

## Default Retrieval Flow

1. `.contexts/bin/context_overview`
2. `.contexts/bin/list_tasks --status active`
3. `.contexts/bin/get_context_meta <id>`
4. decide whether more context is needed
5. `.contexts/bin/list_links <id>`
6. `.contexts/bin/load_resource <id> --section NAME` only if needed

## Update Rules

- Update the active task doc after meaningful work.
- Update `.contexts/handoff.md` at the end of each meaningful work block.
- Use `.contexts/working/` for checkpoint updates when the task delta is large.
- Append one event to `.contexts/lineage/events.jsonl` for each meaningful state change.
- Update `.contexts/current-state.md` only when project-wide status, blockers, or priorities change.
- Create or update a decision doc only when a real decision changed.

## Meaningful Work

Meaningful work includes:

- implementing a user-requested project task
- code edits
- doc edits
- planning artifacts
- debugging or validation
- commits, pushes, or deployment work
- finishing a real task step
- changing the task status
- finding a blocker or risk
- changing the exact next step
- making a project decision
- handing work to another agent

Checkpoint when context-loss risk becomes meaningful. Use judgment, not hardcoded timers or file-count thresholds.

## Local Tooling Convention

- For Python scripts that power `.contexts/` tools, prefer `Typer` for the CLI, `Rich` for human-facing output, and `Loguru` for logging.
- Commands intended for agents should default to structured JSON on `stdout`.
- Human-facing formatting should be opt-in or routed to `stderr` so it does not corrupt machine-readable output.
- Logs should go to `stderr`, not `stdout`.

## Environment Convention

- Default to a repo-local `.venv`.
- Every `.contexts/bin/*` wrapper should validate the environment before running Python code.
- If `.venv` is missing, create it with `python3 -m venv .venv`.
- Install required libraries into `.venv`, never globally.

## Validation

If the `project-context` skill is available, run its validator after changing context files.
