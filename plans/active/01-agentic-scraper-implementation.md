# Agentic Scraper Implementation Plan

## Goal

Turn the job scraper into a Google ADK skill-driven worker that excels at extracting AI/ML job listings from job websites, while keeping the implementation small, inspectable, testable, and safe.

The goal is not to build a general Codex-like coding agent. The target is a specialized job-extraction worker with enough developer-like runtime resources to inspect extraction schemas, references, validators, scripts, and page artifacts, write/run extraction code, and propose skill/reference improvements.

## Current Status

- Deterministic fixture-backed scraping works through the CLI and pytest path.
- The ADK `job-listing-scout` agent and skill are present.
- Seed-driven sources are now documented as references and navigation examples.
- The agent-facing tool surface has been reduced to basic mechanical tools.
- Python 3.13, ADK, Scrapling, and ADK Web have been validated through `uv`.
- ADK Web graph serialization is fixed with a project-side `SerializableLiteLlm` wrapper.
- ITviec fetch through ADK Web works, and large page outputs are routed through the page workspace/sandbox workflow instead of raw full-HTML context.
- The runtime now maintains session-only extraction context with `update_extraction_context`.
- Runtime sandbox notes compact prior sandbox command batches, while the latest command response remains available for exact facts.
- Prompt hierarchy uses explicit session/sandbox blocks so the model treats session extraction context as the commanding guide and sandbox notes as supporting evidence.
- ADK Web confirmed the agent can find 20 ITviec job cards and follow the extractor-backed path: write `output/extractor.py`, run it, and produce 20 jobs.
- Runtime policy now blocks direct writes to required protocol JSON files during workflow mode. Required protocol outputs must be created by `output/extractor.py`.
- Protocol validation now rejects `candidates.json` if it uses the final-result envelope instead of top-level `jobs` and `crawl`.
- A preserved sandbox run showed the current design failure clearly: the agent extracted 20 ITviec jobs but wrote malformed protocol artifacts because it could not reliably see or reason from the actual schema/validator contract.
- The design direction has shifted from a prompt-constrained sandbox recipe to a specialized extraction worker with broad read access, narrow write access, Docker-local runtime context, and reviewable skill/reference proposals.
- Repo `.contexts/` is Codex/development-agent memory only. Runtime project-context for the extraction worker must live inside the Docker workspace, for example `/workspace/context/**`.
- Current skills are close but not sufficient for the new design. They already say validators/schemas are read-only and should be inspected, but they still mix repo `.contexts` with runtime context, use `output/reference_proposal.*` instead of `proposals/**`, and do not state the hard read/write boundary strongly enough.

## Implementation Direction

1. Keep the deterministic pipeline for tests, repeatable demos, and CLI workflows.
2. Use ADK plus the `job-listing-scout` skill for agentic scraping behavior.
3. Treat seeds as reference examples, not as the agent's only action path.
4. Expose enough workspace resources for the worker to act like a competent extraction engineer: relevant skills, references, schemas, validators, fixtures, helper script docs, page artifacts, and recent runtime context.
5. Persist normalized jobs and crawl-run metadata into SQLite.
6. Treat ADK session extraction context as short-term command state for the next step.
7. Treat Docker-local project-context under `/workspace/context/**` as the worker's run memory: observations, attempts, errors, resolutions, and reusable lessons.
8. Treat runtime sandbox notes as evidence and continuity memory, not as command authority.
9. Use explicit runtime prompt block tags so ADK Web traces are auditable and less ambiguous.
10. Require protocol outputs to come from verified extractor execution, not manual sample transcription or manual JSON patching.
11. Let the worker write and run run-scoped scripts such as `output/extractor.py`, `scratch/*.py`, and temporary verification scripts.
12. Do not let the worker directly mutate canonical product/runtime code. Skill, reference, schema, validator, or helper-script changes must be written as proposals and optionally verified in a temporary proposal workspace.
13. Each Docker runtime should check out or materialize a disposable Git worktree from the current branch so the worker sees the same extraction resources as the running app without writing into the canonical checkout.
14. Retain disposable worktrees/workspaces only when they produce validated outputs, reviewable proposals, reproducible bug reports, failing fixtures/tests, or other concrete actionable artifacts. Remove non-actionable worktrees automatically.

## Next Steps

1. Redesign the runtime workspace boundary:
   - create a disposable Git worktree from the current branch for each Docker runtime
   - mount/copy extraction resources from that worktree read-only into Docker
   - provide writable `output/**`, `scratch/**`, `context/**`, and `proposals/**`
   - block external network by default
   - keep canonical repo skills/scripts/schemas read-only
   - clean up the worktree after non-actionable runs
2. Implement Docker-local `project-context` scripts that operate on `/workspace/context/**`, not repo `.contexts`.
3. Update runtime skills to match the worker boundary:
   - `project-context`: keep root repo-context guidance for Codex, but expose Docker-local runtime context scripts/references for the ADK worker instead of repo `.contexts/bin` proxies.
   - `sandbox-page-analyst`: state the hard readable/writable path model, require schema/validator inspection for protocol errors, and move proposal outputs from `output/reference_proposal.*` to `proposals/**`.
   - `sandbox-extraction-debugger`: keep generic repair policy but align write targets with `output/**`, `scratch/**`, `context/**`, and `proposals/**`; keep schemas/validators/references read-only.
   - `job-listing-scout`: keep domain-level orchestration only and point extraction/proposal behavior to sandbox worker skills.
4. Implement hard runtime enforcement for readable/writable paths in sandbox helper scripts. Skill text guides the worker; code must reject forbidden writes.
5. Add proposal flow:
   - write reference/SKILL/script improvement proposals under `proposals/**`
   - optionally apply proposed changes in a temporary proposal workspace
   - run verification against page artifacts/fixtures
   - return proposal paths and verification results for human review
6. Retest ITviec in ADK Web after the worker boundary is implemented.
7. Verify the expected trajectory: classify page, inspect schema/reference/validator as needed, write/run `output/extractor.py`, validate candidates/final output, finalize, promote, persist, query saved jobs, and create proposals only when reusable lessons exist.
8. Add an ADK eval case for the ITviec worker trajectory once the live browser path is stable.

## Blockers

- `render_page` may require Playwright browser binaries if static fetch is insufficient for a target website.
- Live ADK Web retest may still hit OpenAI TPM limits if run repeatedly in a short window.
- The current runtime still hides important schema knowledge behind validators/Pydantic models unless the worker explicitly inspects mounted schemas/scripts.
- Worktree/proposal-workspace lifecycle needs cleanup policy so failed non-actionable attempts do not create persistent noise. The runtime must remove worktrees that do not produce validated outputs, reviewable proposals, reproducible bug reports, failing fixtures/tests, or other concrete actionable artifacts.
- The skills and runtime policy must be changed together; prompt-only skill changes are insufficient without path enforcement, and path enforcement without updated skill instructions will make the worker hit avoidable guardrails.
