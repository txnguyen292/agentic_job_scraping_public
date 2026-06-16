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
- AGE-10 is now implemented as alignment work around the existing `final_goal`/`immediate_goal`/`planned_next_tool` loop. `immediate_goal` is the active bounded goal contract; duplicate `current_goal` prompt/result/injected-context requirements have been collapsed away. `initial_plan` remains stored as the bootstrap guide, then is rebased into an adaptive `extraction_plan` after the first evidence steps. Once `extraction_plan` exists, `initial_plan` is omitted from injected session context. `extraction_strategy` is the more detailed method derived from `extraction_plan`, and `immediate_goal` tackles that strategy in order with the concrete details for the current step.
- AGE-10 verification after implementation: `.venv/bin/python -m pytest -q -> 340 passed, 1 warning`; `.venv/bin/adk eval src tests/eval/evalsets/job_scraper_core.json:itviec_immediate_goal_before_producer_scripting --config_file_path tests/eval/eval_config_goal_contract.json --print_detailed_results -> PASSED`; ADK Web curl smoke on port 8031 returned HTTP 200 and the server was stopped.
- AGE-10 ADK eval should be elastic, not default-strict. Default exact trajectory and lexical response matching are too brittle for a workflow where the agent may choose different evidence probes. Use rubric-based tool-use quality, rubric-based final-response quality, and hallucination checks for the ADK eval; reserve strict trajectory checks for tiny stable smoke skeletons only. Hard invariants belong in pytest, custom metrics, or saved-trace parsers.
- AGE-10 eval tiering decision: keep the dev-loop ADK eval fast with `openai/gpt-5.4-mini` and `num_samples: 1`; add a PR/merge-gate config that uses a larger judge model such as `openai/gpt-5.4` or `openai/gpt-5.5`, optionally with more samples for important workflow rubrics. Do not add custom deterministic ADK metric scaffolding yet unless rubric evals start producing recurring false positives/false negatives.

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

1. Review the AGE-10 implementation diff for commit readiness.
2. Optional deeper validation: run a longer fixture/live extraction session through finalization, promotion, and query beyond the passed boundary-goal eval.
3. After full ITviec finalization/promote/query is stable, add a small validated live source list.
4. Keep AGE-10 ADK eval as an elastic quality eval:
   - keep `eval_config_goal_contract.json` focused on `rubric_based_tool_use_quality_v1`, `rubric_based_final_response_quality_v1`, and `hallucinations_v1`
   - use the fast local/dev config with `openai/gpt-5.4-mini` and `num_samples: 1`
   - treat `hallucinations_v1` N/E as a hard eval-coverage failure; a run that
     cannot score hallucinations cannot be accepted as grounded
   - add a PR/merge-gate config, likely `tests/eval/eval_config_goal_contract_pr.json`, that uses a stronger judge model (`openai/gpt-5.4` or `openai/gpt-5.5`) and considers `num_samples: 3` for high-risk workflow rubrics
   - keep rubrics focused on evidence-backed `immediate_goal`
   - add a rubric that the agent adapts `initial_plan` into the current `extraction_plan` after early evidence
   - avoid exact trajectory matching for dynamic `update_extraction_context` payloads and sandbox probe commands
   - use only a tiny `IN_ORDER` trajectory skeleton in a separate smoke config if stable skill/fixture-loading order must be checked
   - defer custom deterministic ADK metrics until there is a clear maintenance payoff; for now cover hard ordering and guardrail invariants with pytest or saved-trace parsing
5. For the longer ITviec run, verify the expected trajectory:
   - save/render page
   - agent records observed layout, expected output scope, and chosen strategy
   - agent records the active bounded objective in `immediate_goal`
   - agent adapts the startup `initial_plan` into the current `extraction_plan`
   - agent derives `extraction_strategy` from `extraction_plan`
   - agent uses `immediate_goal` to execute the current step inside `extraction_strategy`
   - agent writes/runs supporting scripts as needed
   - agent persists script manifest and extraction run record
   - agent produces candidates/final outputs with evidence/rationale where applicable
   - validators check schema, counts, URL shape, evidence refs, loaded-chunk refs, rationale, script manifest, run summary, and fixture diffs
   - agent repairs by updating the run record and taking a state-changing method it chooses
   - finalization succeeds, then promote, persist, query saved jobs, and create reference/skill/script proposals only when reusable lessons exist
8. Fix any promotion artifact visibility gaps found by the ADK Web retest.
9. Add token-aware evidence loading policy:
   - scripts may estimate token/char size for chunks and batches
   - helper output must fit a configured budget or return file handles plus token estimates
   - the agent must request chunks by id or bounded ranges
   - runtime blocks full-page or oversized evidence ingestion and suggests the next bounded chunk command
10. Implement Docker-local `project-context` scripts that operate on `/workspace/context/**`, not repo `.contexts`.
11. Add proposal flow:
   - write reference/SKILL/script improvement proposals under `proposals/**`
   - if the observed layout differs from the loaded reference, require a reference update proposal explaining the date, observed difference, why the old steps were insufficient, and the proposed new steps
   - successful supporting scripts may be proposed as reference examples only when tied to the specific workflow/reference version and validation result
   - optionally apply proposed changes in a temporary proposal workspace
   - run verification against page artifacts/fixtures
   - return proposal paths and verification results for human review
12. Add an ADK eval case for the accountable ITviec worker trajectory once the live browser path is stable.

## Backlog

- [Reusable extraction packages](../backlog/01-reusable-extraction-packages.md): promote validated, layout-specific support scripts into versioned packages with compact YAML reuse signals, probe scripts, manifests, validation notes, and explicit reuse/patch/new-reference decision rules.

## Blockers

- `render_page` may require Playwright browser binaries if static fetch is insufficient for a target website.
- Live ADK Web retest may still hit OpenAI TPM limits if run repeatedly in a short window.
- ADK Web has been smoke-checked after AGE-10 alignment, but a longer UI workflow through finalization/promotion is still useful.
- The current runtime still hides important schema knowledge behind validators/Pydantic models unless the worker explicitly inspects mounted schemas/scripts.
- Evidence citation and run-artifact schemas need careful design so rationale, scripts, and layout-drift proposals are useful without exploding output size.
- Token counting will be approximate unless we standardize one tokenizer/model budget. A conservative character-to-token fallback is acceptable for blocking oversized loads.
- Worktree/proposal-workspace lifecycle needs cleanup policy so failed non-actionable attempts do not create persistent noise. The runtime must remove worktrees that do not produce validated outputs, reviewable proposals, reproducible bug reports, failing fixtures/tests, or other concrete actionable artifacts.
- The skills and runtime policy must continue changing together; prompt-only skill changes are insufficient without path/token/artifact enforcement, and enforcement without updated skill instructions will make the worker hit avoidable guardrails.
- AGE-10 no longer has split-brain goal state in prompts/runtime/evals; the remaining risk is future prompt drift reintroducing a duplicate goal field.
- `initial_plan` can still be stored for bootstrap continuity, but injected context now omits it after `extraction_plan` exists to avoid stale prompt weight.
- `extraction_strategy` should remain a detailed method based on `extraction_plan`; future changes should not turn it back into a parallel plan.
- ADK eval defaults are too strict for dynamic AGE-10 behavior. If the goal-contract config accidentally reintroduces exact trajectory or lexical response matching, it will punish acceptable evidence paths rather than measuring tool-use quality.
