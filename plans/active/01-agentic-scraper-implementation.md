# Agentic Scraper Implementation Plan

## Goal

Turn the job scraper into a Google ADK skill-driven worker that excels at extracting AI/ML job listings from job websites, while keeping each run inspectable, reproducible, testable, and safe.

The goal is not to build a general Codex-like coding agent. The target is a specialized job-extraction worker where the ADK agent chooses a reasonable extraction method for the page in front of it. Scripts are allowed and expected: the agent may write scripts to inspect layouts, discover repeated structures, chunk evidence, parse data, extract fields, serialize outputs, validate files, or replay successful approaches. The runtime should not over-police how the agent chooses to extract. Instead, it must require an accountable extraction record: observations, chosen strategy, supporting scripts, evidence, output rationale, validation results, and proposed reference/skill updates when the workflow or layout changed.

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
- ADK Web confirmed the agent can find 20 ITviec job cards, but the extractor-backed path exposed the wrong abstraction: the generated `output/extractor.py` became the semantic extractor, guessed selectors, and emitted placeholder values such as `company_name: "unknown"`.
- The desired workflow is now accountable agent-chosen extraction: scripts may assist or perform extraction when the agent chooses that route, but the run must persist observations, strategy, scripts, evidence/rationale, validation results, and proposals so humans and later agents can audit and reuse the method.
- Runtime policy now requires accountable protocol files rather than one fixed producer method. Direct or script-assisted outputs are allowed when the run records the chosen method and validation artifacts.
- Protocol validation now rejects `candidates.json` if it uses the final-result envelope instead of top-level `jobs` and `crawl`.
- A preserved sandbox run showed the current design failure clearly: the agent extracted 20 ITviec jobs but wrote malformed protocol artifacts because it could not reliably see or reason from the actual schema/validator contract.
- A later preserved run showed a deeper design failure: the agent wrote a deterministic extractor that mechanically defaulted missing company selectors to `unknown`. The validator accepted the schema, while finalization caught fixture mismatches. This proves scripts and outputs need durable accountability: if a script extracts fields, the run must preserve why that method was chosen, what evidence or assumptions it used, and what validation/finalization proved or rejected.
- The design direction has shifted from a prompt-constrained sandbox recipe to a specialized extraction worker with broad read access, narrow write access, Docker-local runtime context, and reviewable skill/reference proposals.
- Repo `.contexts/` is Codex/development-agent memory only. Runtime project-context for the extraction worker must live inside the Docker workspace, for example `/workspace/context/**`.
- Current skills have been updated for the new design. They describe a general job-post extraction workflow where the agent chooses the extraction method, persists its steps and supporting scripts, validates outputs, and writes proposal artifacts when a layout-specific reference or general skill should evolve.
- Validator/protocol coverage now requires `output/extraction_run.json`, `output/run_summary.md`, and `output/script_manifest.json` when scripts are authored under `scratch/` or `output/`.
- Output quality now has an agent-owned field-availability contract. Before successful candidates/final outputs, the agent must record `expected_output.available_fields` and `expected_output.field_basis`; validators reject placeholders such as `unknown` for fields marked `required_observed`, so metadata exposed by the page must be extracted or the run must become `needs_review`.
- Full local test suite passed after the accountability-contract implementation: `.venv/bin/python -m pytest -q -> 303 passed, 1 warning`.
- Full local test suite passed after adding observed metadata availability enforcement: `.venv/bin/python -m pytest -q -> 320 passed, 1 warning`.

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
10. Replace rigid "allowed extraction method" rules with an accountability contract. The agent may write and run scripts however it judges useful for extraction, but it must persist the method, scripts, evidence, assumptions, and validation results before promotion.
11. Require each run to write an extraction process record that explains the observed page/layout, the expected output scope, the strategy chosen, the supporting scripts used, and why the method was sufficient.
12. Require a script manifest for all agent-authored supporting scripts: path, purpose, inputs, outputs, hash, workflow/reference version, whether it is run-specific or reusable, and the validation result it supported.
13. Require every extracted nontrivial field to include evidence references and a short rationale when the workflow has evidence artifacts. Evidence references must point to saved exact chunks or other persisted inputs the agent actually used.
14. Add token/size budgeting before evidence ingestion. The runtime should estimate tokens for evidence chunks or script outputs, block oversized loads, and require chunked or batched ingestion instead of allowing full-page dumps.
15. Let the worker write and run run-scoped scripts such as `scratch/find_blocks.py`, `scratch/chunk_page.py`, `scratch/token_budget.py`, `scratch/extract_jobs.py`, `scratch/validate_output.py`, and `scratch/save_agent_output.py`. Successful scripts can become future reference material only when saved with their manifest, workflow/reference version, and validation result.
16. Do not let the worker directly mutate canonical product/runtime code. Skill, reference, schema, validator, or reusable helper-script changes must be written as proposals and optionally verified in a temporary proposal workspace.
17. Each Docker runtime should check out or materialize a disposable Git worktree from the current branch so the worker sees the same extraction resources as the running app without writing into the canonical checkout.
18. Retain disposable worktrees/workspaces only when they produce validated outputs, cited extraction artifacts, reviewable proposals, reproducible bug reports, failing fixtures/tests, or other concrete actionable artifacts. Remove non-actionable worktrees automatically.
19. Keep reusable extraction packages as a backlog track until the active accountable extraction loop is stable. The package design should let later agents reuse successful scripts when the layout still matches, patch them into a new version when the layout drifts slightly, or create a new reference when the layout has materially changed.

## Next Steps

1. Retest ITviec in ADK Web after the field-availability contract implementation and verify that observed metadata such as company, location, salary, and tags is either extracted from evidence or explicitly marked `needs_review`.
2. Verify the expected trajectory:
   - save/render page
   - agent records observed layout, expected output scope, and chosen strategy
   - agent writes/runs supporting scripts as needed
   - agent persists script manifest and extraction run record
   - agent produces candidates/final outputs with evidence/rationale where applicable
   - validators check schema, counts, URL shape, evidence refs, loaded-chunk refs, rationale, script manifest, run summary, and fixture diffs
   - agent repairs by updating the run record and taking a state-changing method it chooses
   - finalization succeeds, then promote, persist, query saved jobs, and create reference/skill/script proposals only when reusable lessons exist
3. Fix any promotion artifact visibility gaps found by the ADK Web retest.
4. Add token-aware evidence loading policy:
   - scripts may estimate token/char size for chunks and batches
   - helper output must fit a configured budget or return file handles plus token estimates
   - the agent must request chunks by id or bounded ranges
   - runtime blocks full-page or oversized evidence ingestion and suggests the next bounded chunk command
5. Implement Docker-local `project-context` scripts that operate on `/workspace/context/**`, not repo `.contexts`.
6. Add proposal flow:
   - write reference/SKILL/script improvement proposals under `proposals/**`
   - if the observed layout differs from the loaded reference, require a reference update proposal explaining the date, observed difference, why the old steps were insufficient, and the proposed new steps
   - successful supporting scripts may be proposed as reference examples only when tied to the specific workflow/reference version and validation result
   - optionally apply proposed changes in a temporary proposal workspace
   - run verification against page artifacts/fixtures
   - return proposal paths and verification results for human review
7. Add an ADK eval case for the accountable ITviec worker trajectory once the live browser path is stable.

## Backlog

- [Reusable extraction packages](../backlog/01-reusable-extraction-packages.md): promote validated, layout-specific support scripts into versioned packages with compact YAML reuse signals, probe scripts, manifests, validation notes, and explicit reuse/patch/new-reference decision rules.

## Blockers

- `render_page` may require Playwright browser binaries if static fetch is insufficient for a target website.
- Live ADK Web retest may still hit OpenAI TPM limits if run repeatedly in a short window.
- ADK Web has not yet been rerun after the accountability-contract implementation.
- The current runtime still hides important schema knowledge behind validators/Pydantic models unless the worker explicitly inspects mounted schemas/scripts.
- Evidence citation and run-artifact schemas need careful design so rationale, scripts, and layout-drift proposals are useful without exploding output size.
- Token counting will be approximate unless we standardize one tokenizer/model budget. A conservative character-to-token fallback is acceptable for blocking oversized loads.
- Worktree/proposal-workspace lifecycle needs cleanup policy so failed non-actionable attempts do not create persistent noise. The runtime must remove worktrees that do not produce validated outputs, reviewable proposals, reproducible bug reports, failing fixtures/tests, or other concrete actionable artifacts.
- The skills and runtime policy must continue changing together; prompt-only skill changes are insufficient without path/token/artifact enforcement, and enforcement without updated skill instructions will make the worker hit avoidable guardrails.
