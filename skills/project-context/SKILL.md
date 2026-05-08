---
name: "project-context"
description: "Create, repair, validate, or use repo-local project context for coding agents. Trigger at the start of any nontrivial project-related repo work, even when `AGENTS.md` or `.contexts/` does not exist yet: new task, implementation plan, code edit, doc edit, debugging, validation, commit, push, deployment, or handoff. If context exists, read it first; if context is missing and the work is meaningful or likely to continue, bootstrap `.contexts/`. Also trigger when the user says update contexts, project contexts, context management, context-management, project memory, repo memory, handoff, task context, current-state, lineage, or asks to update the project's contexts."
---

# Project Context

Use this skill to bootstrap, read, and maintain a repo-local `.contexts/` convention.

## Operational Rule

This skill is not only for explicit user requests or repos that already have context files.

At the start of any nontrivial project-related request in a repo, invoke this skill.

- If `.contexts/` exists, start with the local context tools before normal exploration.
- If `.contexts/` is missing and the work is meaningful or likely to continue, bootstrap it with this skill's init script.
- If `.contexts/` is missing and the task is truly trivial, briefly note that durable context is unnecessary and proceed.
- Treat this as part of the working protocol, like checking `git status` before editing.
- Do not wait for the user to say "context", "context-management", or "project-context".

Meaningful work includes code edits, doc edits, planning artifacts, implementation tasks, debugging, validation, commits, pushes, deployment work, and handoffs.

## Trigger Conditions

Trigger this skill automatically when any of these are true:

- the agent is about to perform nontrivial project-related work in a repo, regardless of whether context files already exist
- the repo already contains `AGENTS.md` or `.contexts/` and the agent is about to do any meaningful work
- the user starts a new project and the repo needs durable local agent context
- the user asks for a new task, implementation task, or project plan in a repo
- the user asks for edits, modifications, or continued implementation work in an existing codebase
- the user asks to commit, push, deploy, verify, debug, or hand off repo work
- the repo needs handoff, decision tracking, or append-only lineage for ongoing work
- the user says "update contexts", "project contexts", "context-management", "project memory", "handoff", "task context", "current-state", or "lineage"

Do not wait for the user to say "context management" explicitly if the request clearly implies ongoing project work that should have durable local context.

The convention is:

- `.contexts/` is the source of truth for agent-operational project context.
- `docs/` and `plans/` are human-facing interfaces when the repo uses them.
- agents should use local context tools first, not read `.contexts/` files directly by default.
- tool output should be metadata-first and summary-first.
- full document content should be loaded only when needed.
- agents should start from minimal context and decide whether more resources are needed.
- large deltas may require checkpoint updates before the end of the task.
- local scripts should validate and bootstrap a project-local `.venv` before running the Python backend.

## Agent Interface And Human Interface

Do not replace human-facing project documents with `.contexts/`.

Use both interfaces when the repo has them:

- `.contexts/` is for agents: operational state, optional workstream summaries, handoff, decisions, references, working checkpoints, and append-only lineage.
- `docs/` is for humans: stable narrative documents such as PRDs, architecture notes, security plans, and implementation references.
- `plans/` is for humans: active plans, backlog plans, comments, notes, paper to-dos, and planning templates.

When meaningful work changes both project state and human-readable plans, update both surfaces:

- update `.contexts/` so the next agent can resume safely.
- update `docs/` or `plans/` so the human project record stays readable.
- prefer links over duplicate content.
- do not force every `.contexts/` task into `plans/`, and do not force every loose human note into `.contexts/`.

## Human-Facing Naming Convention

When creating or repairing human-facing docs, prefer ordered filenames so the reading order is obvious in `ls`, file explorers, and GitHub.

Recommended folder shape:

```text
docs/
  index.md
  01-prd.md
  02-positioning.md
  03-security.md

plans/
  index.md
  active/
    01-signup-capture.md
  backlog/
    index.md
    01-form-provider-decision.md
    02-database-upgrade-path.md
  notes/
    index.md
    01-lead-capture-notes.md
  templates/
    plan.md
    note.md
```

Rules:

- Use plain `index.md` for folder indexes.
- Use numeric prefixes only for sibling content files where order matters.
- Prefer two-digit sequence prefixes: `01-`, `02-`, `03-`.
- Use lowercase kebab-case after the number.
- Preserve existing names if renaming would break important links, unless the task is explicitly to organize docs.
- Update all links when renaming human-facing files.
- Keep `.contexts/` IDs stable (`T-001`, `D-001`, `R-001`) rather than applying this human filename convention there.

## Quick Start

1. At the start of project-related repo work, check for `.contexts/`.
2. If the repo has `.contexts/`, start by running `.contexts/bin/context_overview`.
3. If the repo does not have `.contexts/`, create or repair it with this skill's init script when the work is meaningful, project-scoped, or likely to continue.
4. If a missing-context task is truly trivial, do not bootstrap; proceed and avoid context overhead.
5. Ensure the repo-local `.venv` and required libraries exist before running the Python context backend.
6. Use the local `.contexts/bin/*` commands before normal repo exploration:
   - `context_overview`
   - `list_tasks`
   - `get_context_meta <id>`
   - `list_links <id>`
7. Decide whether the minimal context is sufficient.
8. Use `load_resource` or other metadata-first commands only when the current context is not enough.
9. For large deltas, use working-context checkpoints before important state is lost.
10. After meaningful work, update context through the write commands, then validate the result.

## Read Budget

- Do not read the full `.contexts/` tree up front.
- Do not read raw lineage history unless you need to reconstruct a blocker or audit what happened.
- Prefer one-hop expansion: overview, then metadata, then one specific resource section if needed.
- Decide intentionally whether more context is needed after each step.
- Stop loading context when you have enough to act safely.

## Runtime Convention

The repo should expose a thin command surface under `.contexts/bin/` backed by one Python CLI under `.contexts/tools/`.

Implementation convention for the Python backend:

- Prefer `Typer` for the CLI entrypoint and subcommands.
- Prefer `Rich` for human-facing tables, prompts, and readable error rendering.
- Prefer `Loguru` for logging and diagnostics.
- Commands intended for agent consumption should still default to structured JSON on `stdout`.
- Human-facing formatting should be opt-in or routed to `stderr` so it does not corrupt machine-readable output.
- Logs should go to `stderr`, not `stdout`.

Suggested read commands:

- `context_overview`
- `list_tasks`
- `list_decisions`
- `list_references`
- `get_context_meta <id>`
- `get_working_context <task-id>`
- `list_links <id>`
- `load_resource <id> [--section NAME|--full]`

Suggested write commands:

- `update_task`
- `update_handoff`
- `update_working_context`
- `clear_working_context`
- `append_lineage`
- `validate_context`

## Environment Convention

- Default to a repo-local `.venv`.
- Every wrapper command should validate the environment before running the Python backend.
- If `.venv` is missing, create it with `python3 -m venv .venv`.
- If required libraries are missing, install them into `.venv`, never globally.
- Keep `Typer`, `Rich`, and `Loguru` pinned in a local requirements file when this stack is used.
- Prefer the Python standard library for everything else in v1 unless a third-party library is clearly justified.

## Retrieval Loop

1. Start with `context_overview`.
2. If needed, inspect compact summaries with `list_*` and `get_context_meta`.
3. Decide whether the current context is sufficient to work safely.
4. If needed, inspect typed relationships with `list_links`.
5. If still needed, call `load_resource` for one document or one section.
6. Do the work.
7. If the delta is large and context-loss risk becomes meaningful, write a short checkpoint to working context.
8. Update context through the write tools.
9. Validate the context state.

## Update Rules

- Update context after meaningful work, not after every tiny edit.
- Do not rely only on end-of-task updates when the implementation delta is large.
- Choose the context record to update based on what actually changed; do not assume any specific file type owns the work.
- Use the repo's local tools and schema as the source of truth for what records exist and how they should be updated.
- Keep a current workstream/task document when the repo supports one, so there is one obvious place to find the active goal, current status, and next step.
- Use project-wide state only for project-wide status, priorities, blockers, or orientation.
- Use handoff records for the operational snapshot a future agent needs to resume safely.
- Use working checkpoints for temporary memory during large deltas.
- Use append-only lineage/history for meaningful state changes that should remain auditable.
- Create or update a decision doc only when a real decision changed.
- Use the write tools instead of editing `.contexts/` files directly when the tools are available.

## Context Recording Strategy

This skill should preserve a clear current-work entry point when the repo supports one, but it should not prescribe what each `T-###` file means or force every work item into a new task. Those choices belong to the agent after inspecting the repo's actual context tools, schema, and project conventions.

When recording context, first decide what kind of information changed:

- immediate handoff: what happened, what changed, what to do next
- project-wide state: priorities, blockers, milestone status, or orientation
- decision: a durable choice with rationale and consequences
- working checkpoint: temporary details needed to survive context loss
- lineage/event: an append-only fact that a meaningful state change occurred
- task/workstream record: the current active work, its goal/status/next step, and the durable summary needed to resume

If a task/workstream record exists, keep it useful as the current-work landing page. Update it when the active goal, status, blocker, verification, or next step changes. Create a new task/workstream record only when you judge that the current one no longer represents the active work well enough to resume safely. Do not create, split, rename, or promote task records just because the skill has been invoked.

When deciding whether to create a new `T-###` file, use agent judgment. Useful signals include a materially different goal, a new acceptance boundary, a distinct phase that should be resumed independently, or an old record becoming too broad to serve as the current landing page. If the existing task remains a reasonable landing page, keep using it and update its summary/next step instead.

Prefer recording the smallest useful delta:

- what changed
- why it matters
- how it was verified
- current blocker or risk, if any
- the exact next useful action

Avoid treating context files as project management doctrine. They are memory surfaces. The agent should choose the lightest record that preserves continuity.

## Checkpoint Heuristics

Use judgment, not hardcoded timers or file-count thresholds.

Checkpoint when one or more of these are true:

- the current plan is no longer obvious from the existing context records
- too much important reasoning exists only in working memory
- the task has branched into multiple subproblems
- the implementation approach changed materially
- a blocker or risk emerged that would be easy to lose
- the next agent would struggle to resume from the current docs
- the work is long-running enough that end-only context updates may be inaccurate

## Suggested Commands

```bash
python scripts/install_skill.py --mode symlink
python scripts/init_project_context.py /absolute/project/path --project-name "Project Name"
python scripts/validate_context.py /absolute/project/path
```

## When To Read More

- For the directory layout, tool surface, metadata contract, and lineage shape, read `references/context-convention.md`.

## Job Scraper Runtime Extension

This repo also exposes lightweight runtime scripts for the ADK scraper agent. These extend the root `project-context` skill; they do not replace the repo `.contexts/` convention above.

Use `references/sandbox-runtime.md` when the ADK runtime agent needs project-context-like memory while operating inside a Docker sandbox. That reference defines sandbox-local context under `/workspace/context/` and keeps it separate from repo `.contexts/`.

For extraction workflows, the runtime agent may use the sandbox-local notebook scripts as a durable reasoning notebook to record compact observations, extraction plans, attempts, and comparisons. Repo `.contexts/` is for Codex and other development agents; sandbox runtime notes are for one scrape/audit run. Keep these notes small and evidence-oriented:

- record page observations as short facts with selectors, counts, evidence paths, and hashes rather than raw HTML
- record `observations` and `extraction_plan` before running or revising extractor code
- after each extractor run, reconcile the new result with the notes before the next repair attempt
- when an extractor fails, update the observations or extraction plan before the next attempt
- do not store full stdout, stderr, raw page HTML, or full job payloads in runtime context
- promote only reusable lessons, project blockers, or human-review proposals into repo `.contexts/`

Runtime extension scripts:

- `scripts/context_overview.py`: proxy to the repo `.contexts/bin/context_overview` command.
- `scripts/list_tasks.py`: proxy to the repo `.contexts/bin/list_tasks` command.
- `scripts/update_task.py`: proxy to the repo `.contexts/bin/update_task` command.
- `scripts/update_handoff.py`: proxy to the repo `.contexts/bin/update_handoff` command.
- `scripts/append_lineage.py`: proxy to the repo `.contexts/bin/append_lineage` command.
- `scripts/validate_context.py`: proxy to the repo `.contexts/bin/validate_context` command.
- `scripts/validate_context_strict.py`: root-skill strict validator copied from the installed Codex skill; use only when intentionally checking conformance to the full root template.
- `scripts/record_observation.py`: append a compact sandbox extraction note.
- `scripts/list_extraction_notes.py`: list recent compact sandbox extraction notes.
