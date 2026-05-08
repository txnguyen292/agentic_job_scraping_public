# Project Context Convention

This convention treats repo context like a skill:

- a small entrypoint
- progressive disclosure
- linked references and typed relationships
- append-only lineage
- a tool-first interface over on-disk markdown

## Directory Layout

```text
AGENTS.md
.contexts/
  index.md
  current-state.md
  handoff.md
  tasks/
  decisions/
  references/
  working/
  templates/
    task.md
    decision.md
    reference.md
    working.md
  lineage/
    events.jsonl
  tools/
    context_cli.py
    ensure_env.sh
    requirements.txt
  bin/
    context_overview
    list_tasks
    list_decisions
    list_references
    get_context_meta
    get_working_context
    list_links
    load_resource
    update_task
    update_handoff
    update_working_context
    clear_working_context
    append_lineage
    validate_context
```

## Tool-First Traversal

Agents should prefer the local context tools over direct file reads.

Default flow:

1. `.contexts/bin/context_overview`
2. `.contexts/bin/list_tasks --status active`
3. `.contexts/bin/get_context_meta <id>`
4. Decide whether the current context is sufficient.
5. `.contexts/bin/list_links <id>` if more metadata is needed
6. `.contexts/bin/load_resource <id> --section NAME` only if needed

Direct reads of `.contexts/*.md` are a fallback, not the primary mechanism.

## Traversal Tiers

Use this read order unless the repo says otherwise.

### Tier 0

- `AGENTS.md`

### Tier 1

- `.contexts/bin/context_overview`
- `.contexts/bin/list_tasks`
- `.contexts/bin/get_context_meta`
- minimal working-context metadata when the active task is large

### Tier 2

- `.contexts/bin/list_links`
- linked decision metadata
- linked reference metadata
- linked task metadata that is a direct dependency
- `.contexts/bin/get_working_context <task-id>` when a long-running task has checkpoint state

### Tier 3

- `.contexts/bin/load_resource`
- selected lineage events for the active task
- raw artifacts outside the context tree

Agents should almost never begin at Tier 3.

## Retrieval Policy

Agents should start from minimal context and expand only when needed.

Recommended pattern:

1. read minimal context first
2. decide whether the current context is enough to work safely
3. if not enough, load more metadata or resource content intentionally
4. stop expanding when enough context has been gathered

## Environment Bootstrap

The local tools should validate the runtime before doing real work.

Expected behavior:

1. Every command in `.contexts/bin/` calls `.contexts/tools/ensure_env.sh`.
2. `ensure_env.sh` checks for `.venv`.
3. If `.venv` is missing, it creates it with `python3 -m venv .venv`.
4. If required dependencies are missing, it installs them from `.contexts/tools/requirements.txt`.
5. The wrapper then executes `.venv/bin/python .contexts/tools/context_cli.py ...`.

Do not install dependencies globally.

## Python Tooling Stack

For the `.contexts/tools/context_cli.py` backend:

- Use `Typer` for the CLI shape and subcommands.
- Use `Rich` for human-facing output modes.
- Use `Loguru` for diagnostics and debug logging.
- Keep agent-facing command output machine-readable by default.
- Route logs and non-JSON output to `stderr`.
- Pin required dependencies in `.contexts/tools/requirements.txt`.

## Update Ownership

- Any agent may update the active task doc.
- Any agent may update `.contexts/handoff.md` when finishing a meaningful work block.
- Any agent may update task-local working context during large deltas.
- The lead agent should update `.contexts/current-state.md` when project-wide status changed.
- Any agent may append lineage events.
- Decision docs should change only when a decision changed.

## Metadata Contract

Tool output should default to metadata-first summaries, not full markdown.

Minimum fields exposed by metadata views:

- `id`
- `kind`
- `title`
- `status` when applicable
- `updated_at`
- `summary`
- `read_next`
- `related`

Prefer stable IDs plus explicit link fields in frontmatter.

### Task docs

Required frontmatter keys:

- `id`
- `kind`
- `status`
- `updated_at`

Recommended frontmatter keys:

- `owner`
- `decision_ids`
- `depends_on`
- `read_next`
- `related_docs`
- `blocked_by`

Recommended body sections:

- `Summary`
- `Read This When`
- `Read Next`
- `Why`
- `Outcome`
- `Scope`
- `Non-Goals`
- `Acceptance Criteria`
- `Risks / Blockers`
- `Links`

### Decision docs

Required frontmatter keys:

- `id`
- `kind`
- `status`
- `updated_at`

Recommended frontmatter keys:

- `task_ids`
- `supersedes`
- `read_next`
- `related_docs`

Recommended body sections:

- `Summary`
- `Context`
- `Decision`
- `Consequences`
- `Alternatives Considered`
- `Links`

### Reference docs

Required frontmatter keys:

- `id`
- `kind`
- `updated_at`

Recommended frontmatter keys:

- `applies_to`
- `read_next`
- `related_docs`

Recommended body sections:

- `Summary`
- `Read This When`
- `Read Next`
- `Details`
- `Links`

### Working docs

Required frontmatter keys:

- `id`
- `kind`
- `task_id`
- `updated_at`

Recommended frontmatter keys:

- `summary`
- `active_files`
- `open_questions`
- `next_step`
- `related_docs`

Recommended body sections:

- `Summary`
- `Current Subproblem`
- `Hypotheses`
- `Open Questions`
- `Next Checkpoint`
- `Links`

## Lineage Event Shape

`.contexts/lineage/events.jsonl` is append-only. Each line should be one JSON object.

Required keys:

- `ts`
- `type`
- `summary`

Recommended keys:

- `task_id`
- `decision_id`
- `files`
- `verification`
- `agent`
- `session_id`
- `branch`
- `links`

Example:

```json
{"ts":"2026-04-21T18:00:00-04:00","task_id":"T-012","type":"updated_context","summary":"Updated handoff and current state after wiring lineage validation.","files":[".contexts/handoff.md",".contexts/current-state.md"],"verification":".contexts/bin/validate_context","agent":"codex"}
```

## Meaningful Work Threshold

Update context when one of these is true:

- a task moved status
- a new blocker or risk was found
- the exact next step changed
- a decision changed
- a meaningful code or document change landed
- work is being handed off

Do not churn context for trivial edits.

## Checkpointing Policy

Use heuristic checkpointing, not hardcoded thresholds.

Checkpoint when one or more of these are true:

- the current plan is no longer obvious from the task doc
- too much important reasoning exists only in working memory
- the task has branched into multiple subproblems
- the implementation approach changed materially
- a blocker or risk emerged that would be easy to lose
- the next agent would struggle to resume from the current docs

Prefer task-local working notes before changing global context.

## Initialization Flow

1. Run `init_project_context.py` against the target repo.
2. Adjust `AGENTS.md` if the repo needs a different read order.
3. Fill in `.contexts/current-state.md`.
4. Create the first task doc from `.contexts/templates/task.md`.
5. Use `.contexts/working/` for checkpoint notes during large deltas.
6. Append lineage events as work progresses.
7. Prefer the `.contexts/bin/*` tools for normal operation.

## Validation Scope

The validator should catch the basics:

- required files exist
- required frontmatter keys exist
- IDs are unique per doc type
- lineage lines parse as JSON
- required lineage keys exist
- tool wrappers can bootstrap the local `.venv`
- working-context docs and wrappers exist when the convention includes checkpointing

Broken links can be added later if the repo needs stricter checks.
