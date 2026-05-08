# Sandbox Runtime Context

Use this reference when the ADK runtime scraping agent needs project-context-like memory while operating inside a Docker sandbox.

## Boundary

There are two different context systems:

- Repo `.contexts/` is for Codex and development agents working on this repository.
- Sandbox runtime context is for the ADK scraper agent during one scrape/audit run.

The runtime scraper must not write to the repo `.contexts/` tree. Repo context records implementation decisions, project plans, handoff, and codebase state. Sandbox runtime context records page-analysis work for one extraction run.

## Storage

Store sandbox runtime context inside the active sandbox workspace, normally under:

```text
/workspace/context/
  current_state.json
  observations.jsonl
  attempts.jsonl
  memory.jsonl
```

If the run has an audit folder, promote these files as ADK artifacts only when useful for debugging or final audit. Do not inject the full files into every model request.

## What To Record

Use sandbox runtime context for compact operational memory:

- page observations: selectors, repeated card markers, URL attributes, counts, evidence paths
- extraction plan: how observations become code-level extraction logic
- attempts: scripts written, commands run, whether they changed the state
- errors: validator/finalizer failures, count mismatches, guardrails, stale assumptions
- cleared state snapshots: planned next tool, why it was cleared, and result summary

Do not store raw HTML, full stdout/stderr, full job payloads, or large JSON blobs. Store paths and hashes instead.

## How The Agent Should Use It

The sandbox runtime context is long-lived evidence for the current run, not the commanding prompt state.

Use it this way:

1. Keep immediate next-step guidance in ADK session state, such as `update_extraction_context`.
2. Append compact facts to `/workspace/context/*.jsonl` when state is cleared, an attempt completes, or an error changes the plan.
3. When a repair loop starts, inspect only the relevant recent runtime context entries for the active `audit_id`.
4. Use those entries to avoid repeating failed probes and to choose the next efficient state-changing action.
5. Promote only high-level summaries to repo `.contexts/` after a real project milestone, blocker, decision, or reusable lesson.

## Promotion Rule

Do not copy sandbox runtime memory into repo `.contexts/` automatically.

Promote a short summary only when it changes the development project:

- a new stable extraction pattern was discovered
- a workflow blocker changes the implementation plan
- a skill/reference proposal needs human review
- a runtime guardrail exposes a product bug

Otherwise, keep the memory sandbox-local and audit-scoped.
