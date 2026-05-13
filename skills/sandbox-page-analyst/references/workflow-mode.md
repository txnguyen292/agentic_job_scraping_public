# Workflow Mode

Use this reference for real page analysis and job extraction. Workflow mode must produce accountable, evidence-cited protocol outputs before finalization and persistence. The agent owns the extraction run and may choose scripts, model calls, bounded evidence loading, or direct reasoning as long as the method and rationale are persisted.

## Required Workflow

1. Use the runtime `update_extraction_context` tool as a compact extraction notebook during the workflow. This context is session-only state, not a reusable reference and not repo-level project context.
1. After loading this workflow reference or any site-specific resource, update session context before the next state-changing action. Record the loaded resource names, the instructions/cues they add, the stable `final_goal`, and a concrete `immediate_goal` plus `planned_next_tool` that carries out the user's request.
2. Find the mounted page files and orient yourself with small, targeted inspections. Never ask for the whole HTML when a slice, count, selector probe, or metadata check would answer the next question.
3. If a command returns a truncated preview, reason from the preview first. Inspect a narrower slice only when the preview does not contain enough evidence.
4. Profile the page layout and derive repeated evidence patterns before extracting jobs. Identify stable signals that denote one job post, such as repeated card containers, repeated detail URLs, embedded JSON item lists, selected-job URLs, title/company/location neighborhoods, salary/tag blocks, and pagination hints. After inspecting the first representative repeated job unit or evidence chunk, update session extraction context with `observations`, `extraction_strategy`, `extraction_plan`, and `expected_output`. `extraction_strategy` is the current extraction method; `extraction_plan` is the near-term work plan for executing it.
5. Select one or two relevant references from `references/` when the page layout is recognizable. Use them as pattern guides, not as final answers. If the source URL is on `itviec.com` or the page contains ITviec listing markers, load `references/itviec-listing-page.md` before writing helper scripts or protocol outputs; its job URLs must be detail posting URLs ending in `-NNNN`, not category/search/navigation URLs. For ITviec, the primary loop must be one emitted job per repeated listing card; broad `/it-jobs/` URL scans are supporting evidence only.
6. Write or run supporting scripts when they help the agent overcome context limits or make the extraction reliable: discover repeated structures, create exact raw evidence chunks under `evidence/chunks/`, write `evidence/index.json`, estimate token counts, parse/extract repeated records, validate protocol files, or serialize outputs. Prefer the Python standard library plus verified approved parser imports.
7. Load evidence or script outputs in bounded batches. Do not ingest more tokens than the current decision needs. If the evidence set is too large, split it and load the next batch after recording progress in session context.
8. The agent extracts the jobs using the chosen method and records why that method is sufficient. Every nontrivial field in `output/candidates.json` and `output/final.json` must include `field_rationale` with `evidence_refs` pointing to chunk ids in `evidence/index.json` when evidence is chunked, plus a concise rationale for why the cited evidence supports the value.
8. Before writing `output/candidates.json` or `output/final.json`, declare `expected_output.expected_job_count` from your repeated-unit observations and record `count_basis` plus `count_rationale`. `count_rationale` must explain which past action or tool result exposed the repeated units and why that implies the expected number of listings. The saved successful output must contain exactly that many jobs unless the output is explicitly `needs_review` with evidence-backed rationale.
9. Before authoring an `output/*.py` producer script or any successful protocol result file, load the compact protocol contract with `scripts/protocol_contract.py` and update session context with `output_contract` plus the agent's own `producer_output_plan`. The plan must translate the contract into the current extraction run: required protocol files, `extraction_run.json` required fields (`observations`, `chosen_strategy`, `expected_output`), candidate/final envelope shapes, script manifest requirements when scripts are authored, and the intended validation/finalization sequence. The contract is a checklist of output obligations; the agent still chooses the extraction method and output values.
9. A valid workflow run must plan for all required protocol outputs before validation, finalization, persistence, or database queries: `output/page_profile.json`, `output/extraction_strategy.json`, `output/extraction_run.json`, `output/candidates.json`, `output/validation.json`, `output/final.json`, and `output/run_summary.md`, plus `output/script_manifest.json` when supporting scripts were authored. Do not wait for `persist_sandbox_job_extraction` to reveal missing protocol files, and do not wait for validator errors to reveal missing protocol fields.
10. Run `scripts/validate_outputs.py` after the accountable outputs exist. Use the validator and `scripts/sandbox_finalize.py` as the authority for schema, URL-shape, count, evidence-reference, loaded-chunk, rationale, script manifest, run summary, and quality failures. If either script returns an error, treat that script as the read-only contract that produced the error: inspect its `--help`, and inspect bounded source or returned error details when needed, before choosing the next repair. The validator/finalizer scripts and any mounted schemas/references are readable contracts only; never edit them from the sandbox workflow.
11. Compare the output against the requirement only through concrete evidence: loaded chunks, script outputs, validator/finalizer errors, explicit reference expectations, or directly inspected malformed records. Reconcile the new result with the injected session extraction context. Do not judge that a count is "too broad" or "too narrow" from intuition alone.
12. Update the session extraction context with the reconciled comparison. If validation/finalization reports a concrete failure, inspect why the script failed, update `observations`, `extraction_strategy`, `extraction_plan`, and `planned_next_tool`, then repair the right layer: observations/evidence, extraction method, supporting script, serialized output, or proposal artifact.
12. If a tool result includes `unsatisfied_requirements`, treat those as acceptance criteria the current state has not met. Infer the missing prerequisite yourself from the context. Before planning the repair, reconstruct how you inferred `expected_output`: inspect the observations, `count_basis`, attempted actions, and latest tool results that established the expected repeated-unit count. Then inspect available tools/resources and choose a tool-supported method that can satisfy the expectation. Use the same repeated-unit signal to plan extraction for every expected unit. For example, if successful output has fewer jobs than `expected_output.expected_job_count`, decide whether evidence coverage is incomplete, the output omitted loaded units, the expected count needs an evidence-backed filter, or the run must be `needs_review`. Do not repeat a rejected output or write a one-record schema probe as a successful payload.
13. If the required protocol files exist and there is no concrete validation/finalization error yet, the next action is validation/finalization. Do not rewrite helper scripts again just because the result feels uncertain.
14. Protocol files may be serialized or extracted by a supporting script, but the agent must record that choice in `output/extraction_run.json` and, when a script was authored, `output/script_manifest.json`. If `output/candidates.json`, `output/final.json`, or another required protocol file is wrong, first decide whether the problem is observations/evidence, extraction method, script logic, or serialization before patching any script.
15. Write a human-reviewable `output/run_summary.md` and a reference proposal. If no reusable pattern was found, write a short proposal explaining why.
15. Validate the persisted outputs. If validation fails, repair the observations/evidence, extraction method, supporting script, serialized output, or protocol files before finalizing.
16. Finalize only after validation succeeds. If finalization rejects the run, continue working in the same sandbox using the returned facts.
17. If validation/finalization reports any repairable sandbox workflow error, load `sandbox-extraction-debugger` and follow that skill as the official repair protocol for usage-vs-implementation triage and sandbox-artifact-only repair instead of duplicating debugging policy here.
18. Do not stop after early inspection. A blocker is valid only after required protocol files have been written with `status: "needs_review"` or a guardrail/error prevents additional sandbox commands.
19. Return final compact JSON only.

## Runtime Context Priority

- `<SESSION_EXTRACTION_CONTEXT>` is the commanding guide for next-step reasoning in the current task.
- `<RUNTIME_SANDBOX_NOTES>` is supporting evidence from compacted sandbox command history.
- `<RUNTIME_SANDBOX_GUARD>` and `<RUNTIME_SANDBOX_START_GUARD>` are hard operational constraints.
- `final_goal` is the stable workflow goal from the user request; do not narrow or replace it unless the user changes the task.
- `immediate_goal` is the next concrete objective needed to progress toward `final_goal`; it should imply one efficient next tool call.
- Session state is the most important working document in the workflow. Keep it concise enough to fit inside one model call, but detailed enough that a future turn can derive the next efficient action without rereading broad transcripts.
- Latest exact tool output is evidence that may correct either context. If new evidence changes `observations`, `extraction_strategy`, `extraction_plan`, `last_result`, `known_errors`, or `immediate_goal`, call `update_extraction_context` before continuing.
- If sandbox notes conflict with session context, verify with exact available tool output or a focused sandbox inspection, then update session context.
- Before every tool call and final response, inspect `<SESSION_EXTRACTION_CONTEXT>` and derive the next logical action from `final_goal`, `known_errors`, `attempted_actions`, `immediate_goal`, `last_result`, `extraction_strategy`, `extraction_plan`, and `observations`.
- If latest tool results show an error is solved, call `update_extraction_context` to remove or rewrite that stale error. If `attempted_actions` shows repeated probes that did not help, choose a state-changing action instead of probing again.

## Session Extraction Notebook

Use `update_extraction_context` for compact reasoning checkpoints, not raw data storage. The runtime injects the latest session context before model calls.

- Before each meaningful extraction or helper-writing attempt, update session context with `observations`, `extraction_strategy`, `extraction_plan`, and the current `immediate_goal`.
- Before writing an `output/*.py` producer script or protocol result files, store `output_contract` from `scripts/protocol_contract.py` and a compact `producer_output_plan` written by the agent. This should prevent learning the output contract one validator error at a time.
- Before choosing the next tool, finalizing, persisting, querying, or answering, refer to the latest injected session state for next-step guidance, especially `immediate_goal`, `known_errors`, `last_result`, `observations`, `extraction_strategy`, and `extraction_plan`.
- Read `last_result` first after every tool call. Decide what it proves, whether it satisfies `immediate_goal`, and whether it creates, confirms, or resolves any `known_errors`.
- Treat `status: "success"` from a non-context tool as verified completion of the requested action. For `scripts/sandbox_write_file.py`, success for a path means that artifact is now satisfied unless a later validator or finalizer names it again. Remove stale `known_errors` for that path or field, record the success in `last_result`/`attempted_actions`, and advance to the next missing required output or validation instead of rewriting the same successful content.
- Check `attempted_actions` before acting. Do not repeat actions that did not change state; choose a state-changing repair, validation, finalization, promotion, or query action instead.
- Keep state compact and operational: store the current failure, the relevant contract fact, the repair rationale, and the next tool choice; do not store raw HTML, long stdout/stderr, full stack traces, broad source dumps, or duplicated history.
- If a validator, finalizer, patch helper, or other helper script produced the current error, inspect that error-producing script as a read-only contract when the returned message is not enough to choose a precise repair. The next step may be inspecting another related script, schema, or generated artifact; record the rationale in state before taking that step.
- If the error mentions a field shape such as `candidates.crawl must be an object`, inspect the relevant validator/finalizer rule and then repair the serialization helper or output writer. Do not guess the shape from memory.
- If accountable outputs exist and no concrete validation/finalization error is active, the next objective is validation/finalization, not another helper rewrite.
- After a repairable validation/finalization/schema/count error, the session context must declare `planned_next_tool`: the concrete next efficient state-changing tool call chosen from available tool names. For sandbox helpers, include `tool_name`, `skill_name`, and `file_path`. The next state-changing tool call must match this plan unless new evidence is first recorded with a revised `planned_next_tool`; bounded read-only inspections may intervene when they are needed to make the repair possible.
- During repair, also declare `repair_scope`: a bounded work order with `status`, `objective`, `files`, optional `allowed_resources`, optional `allowed_inspections` as planning notes, and a `verification` command when ready. Use `repair_scope` only after a concrete sandbox artifact error; do not use it for normal skill loading, page fetching, or sandbox startup. `sandbox_read.py` is a bounded read-only tool and may inspect sandbox files, generated artifacts, mounted scripts, schemas, and references when that inspection helps choose the next repair. Keep mutations bounded: patch only files listed in `repair_scope.files`, and load only declared external resources.
- If the declared `planned_next_tool` runs and fails, treat that failed tool output as new evidence. Update session context with the failed invariant, what the attempt proved, and the revised `planned_next_tool` before continuing.
- After a meaningful failure, repeated failed attempt, or returned `unsatisfied_requirements`, add or update `workflow_reflections` in session context. Reflections are learned interpretations of failure patterns: what the failure implies, the diagnostic question it raises, state-changing actions, and anti-actions. Apply them when revising the next plan, but do not treat them as fixed tool recipes.
- `observations` means the page clues the agent found: repeated containers, URL attributes, text neighborhoods, embedded data, pagination hints, counts, and evidence paths.
- `extraction_strategy` means the current extraction method derived from representative evidence: target units, unit boundary, count method, field patterns, known exclusions, coverage plan, and the reason this method should produce the required outputs.
- `extraction_plan` means the near-term work plan for gathering exact evidence and executing the strategy: selectors, chunking rules, token budgets, evidence refs, URL normalization, filters, expected candidate count, and output files.
- Follow `extraction_strategy` by default. Enhance it when new evidence adds useful selector, field, coverage, or edge-case detail. Revise it when new evidence or validation/finalization contradicts the current method. Record the reason for each enhancement or revision in the strategy object.
- Observations must include implementation cues, not just counts. If you detect repeated structures, record the selector, attribute names, text boundaries, URL fallback order, and expected loop cardinality before writing helper scripts or protocol outputs.
- The extraction plan must translate those cues into evidence-loading steps and output-writing steps: which collection to chunk, how to build each `job_url`, where title/company/location evidence appears, how to deduplicate, which chunk ids support each field, and which count must match validation.
- `attempted_actions` means compact records of actions that already ran and whether they helped, such as existence checks, placeholder inspection, helper writes/runs, output serialization, validation attempts, and failed probes.
- After writing or serializing outputs, compare `output/candidates.json` and `output/final.json` against the requirement, and decide whether the new result confirms or contradicts the current observations and extraction plan.
- Do not convert uncertainty into a repair judgment. If all required protocol files exist and the only concern is quality/count uncertainty, run `scripts/validate_outputs.py` or `scripts/sandbox_finalize.py` next and let that returned error, if any, define the repair target.
- Do not call a count "too broad" or "too narrow" unless validator/finalizer output, a loaded site reference, or directly inspected malformed records proves the mismatch. A count equal to the observed repeated card count is not by itself an error.
- If the new result contradicts the notes, refine the observations or extraction strategy/plan before changing helper code.
- If observed listing evidence and output counts diverge, record the mismatch once, then choose a state-changing action: load missing evidence chunks, revise outputs, or patch the supporting script if it missed repeated units. Do not keep calling `update_extraction_context` for the same mismatch without a state-changing action.
- For ITviec, if observations record 20 repeated listing cards and the output emits 1, 5, 64, 114, or any other mismatched count, the next state-changing action is to inspect loaded evidence and the evidence index, then either load the missing card chunks or patch the chunking helper so it emits one chunk per repeated card.
- If the output is wrong, do not add extra process categories. Instead, update the observations or extraction plan, inspect the relevant evidence/script/output, revise the failing layer, and rerun validation.
- If a repair needs more documentation or file inspection, update `repair_scope` with the specific allowed resource or inspection and why it is needed. Do not load broad docs or rewrite broad script sections just because the previous patch failed.
- Apply one small coherent patch or patch set for the active `repair_scope`, using current sandbox file contents as patch context, then run the declared verification command before deciding whether a wider scope is needed.
- If `attempted_actions` already records missing-output checks or placeholder inspection, do not repeat those checks. Move to the next action that can change the state, normally loading needed evidence, running or patching the chosen script, serializing outputs, validating, or finalizing.
- If the output is correct, the final note should make clear which loaded evidence and citations make the extraction reliable enough to support the reference proposal.

Example session context update after detecting ITviec cards:

```json
{
  "observations": [
    "Detected 20 repeated job cards with selector [data-search--pagination-target=\"jobCard\"].",
    "Each card carries a detail slug in data-search--job-selection-job-url-value or a fallback /sign_in?job=<slug-id> href.",
    "Title text is under [data-search--job-selection-target=\"jobTitle\"].",
    "Company evidence is in the visible card text near the title and before the salary/login text.",
    "Never use /companies/...preview_jd_page as job_url."
  ],
  "extraction_plan": [
    "Write a helper to create one exact evidence chunk per soup.select('[data-search--pagination-target=\"jobCard\"]') result.",
    "For each loaded card chunk, the agent reads the visible title/company/location text and the URL attribute or /sign_in?job= fallback.",
    "The agent writes title/company/location/job_url values with field_rationale entries citing the loaded card chunk id.",
    "Deduplicate by normalized detail job_url, not company preview URL.",
    "Write output/candidates.json and output/final.json with exactly the observed card count unless a documented filter explains otherwise."
  ],
  "extraction_strategy": {
    "status": "active",
    "derived_from": "first loaded representative ITviec card plus repeated-card count probe",
    "target_units": "one job per [data-search--pagination-target=\"jobCard\"] card",
    "unit_boundary": "one repeated card container",
    "count_method": "count repeated card containers and unique detail job URLs",
    "field_patterns": {
      "title": "[data-search--job-selection-target=\"jobTitle\"] text",
      "company_name": "visible company text near the title before salary/login text",
      "job_url": "data-search--job-selection-job-url-value or /sign_in?job= fallback"
    },
    "known_exclusions": ["company preview URLs", "category/search/navigation URLs"],
    "coverage_plan": "create and load one evidence chunk per card before writing successful outputs",
    "revision_policy": "enhance with new field patterns; revise only on contradicting evidence or validator/finalizer failure"
  }
}
```

## Error Repair Loop

Returned errors are repair evidence, not terminal answers by default.

- For validation, finalization, schema, count, URL-shape, missing-file, stale-output, or repeated-repair errors, load `sandbox-extraction-debugger`. That skill owns repair policy.
- If a shell command exits non-zero, inspect bounded `stderr` or the persisted stderr slice, fix the command or code, and rerun.
- If validation reports a schema/protocol error, address that exact error in the sandbox workspace, rerun the relevant helper when needed, then rerun validation.
- If finalization reports missing files, invalid protocol, or an expected-vs-actual job count mismatch, enter the `sandbox-extraction-debugger` workflow, repair the sandbox workspace, and retry finalization.
- If finalization reports missing files, update session context with a concrete `planned_next_tool` before continuing. The efficient next tool depends on the missing layer: serialize the extracted outputs if extraction already happened, or inspect evidence/scripts and complete the missing fields before serialization.
- If finalization reports `evidence/index.json is required`, do not rerun finalization or keep rewriting candidates/final with unsaved refs. Save exact chunks under `evidence/chunks/`, write `evidence/index.json`, mark only loaded chunks as loaded, reconcile candidates/final refs, then validate/finalize.
- A finalize count mismatch is not a final-answer condition. Treat it as a flawed evidence or output workflow and follow the debugger skill: inspect the page/card evidence, `evidence/index.json`, loaded chunks, and any helper scripts, then make the smallest repair and validate/finalize again.
- Only return `needs_review` when page evidence is insufficient after focused inspection, not because the first extractor attempt failed.

## Accountable Extraction Rules

- The agent is responsible for the extraction outcome and chooses the method. Scripts may assist or perform extraction when their purpose, inputs, outputs, version tie, reuse classification, and validation result are recorded.
- Treat scripts as auditable supporting artifacts. They may discover patterns, slice exact evidence, budget tokens, parse/extract repeated job units, validate, and serialize outputs.
- Do not ingest unbounded evidence. Chunk exact raw evidence or compact script outputs so the agent can reason within context limits.
- Treat repeated job-card markers, repeated detail URL attributes, and repeated site-specific controller/data attributes as positive page evidence. Do not classify a listing page as having "no stable evidence" while such repeated markers exist.
- Store exact evidence chunks under `evidence/chunks/` and the evidence manifest at `evidence/index.json`.
- Mark a chunk `loaded: true` only after the agent has ingested that exact chunk. Do not cite chunks the agent did not load.
- The persisted output files are the source of truth for every required protocol output; helper stdout should only report counts and paths.
- The first complete output version should write every required protocol file, not just `candidates.json` and `final.json`. If validation says any required protocol output is missing, serialize the missing output or generate the missing compact metadata from the same run record.
- Helper stdout must surface saved output paths, at minimum `candidates_path` and `final_path`, so the main agent can decide which persisted output to inspect or report.
- The same run record must also support `output/page_profile.json`, `output/extraction_strategy.json`, `output/extraction_run.json`, `output/validation.json`, and `output/run_summary.md`; create these protocol files before validation/finalization instead of treating them as late cleanup.
- `output/candidates.json` must have top-level `source`, `jobs`, `selectors`, `crawl`, and `warnings`. Never write candidates as `{ "status": "success", "result": { "jobs": [...] } }`.
- `output/final.json` must have top-level `status`, `output_schema`, `summary`, `result`, and `protocol`, where `result` is the complete candidate payload from `output/candidates.json`.
- The minimal valid shape is shown in the sandbox skill's Workflow Protocol Contract section. Use that example as the shape template, but fill values from the mounted page evidence and validator expectations.
- do not emit JSON `null` for string fields. If evidence is absent, use `""` or `"unknown"` and document uncertainty in validation warnings.
- Do not overwrite outputs with placeholders. If a required protocol file is missing or invalid, repair the observations/evidence, extraction method, script, rationale, or serialization so the protocol files are derived from real page evidence and real extracted jobs.
- If `final.json` schema or count validation fails, do not manually assemble a partial `final.json`. Revise the full candidates payload and regenerate `candidates.json` and `final.json` from the same complete payload.
- Do not repair `final.json` by writing one sampled job, by putting `count`/`jobs` only at the top level, or by using an invalid shape such as `{"result":"success"}`. A successful `output/final.json` needs top-level `status: "success"` and a `result` object containing the complete jobs payload.
- If the agent extracts 20 valid candidates from loaded evidence, the protocol should carry those 20 candidates unless the agent applies and documents a filter rule.
- Before writing successful `candidates.json` or `final.json`, record observed metadata availability in `output/extraction_run.json` under `expected_output.available_fields` and `expected_output.field_basis`. If page/card evidence exposes a field such as `company_name`, `location_raw`, `salary_raw`, or `tags` for each in-scope job, mark it `required_observed` and explain the evidence signal. Successful outputs must extract real values for fields marked `required_observed`; do not use `""` or `"unknown"` for those fields.
- If helper stdout reports a plausible count and saved protocol paths, do not rewrite the helper just to gain confidence. Validate the files with `scripts/validate_outputs.py`, then finalize or repair from the concrete returned error.
- Keep each verification step short and separately observable. Run one helper command by itself. If you need to inspect generated files after that, use a separate bounded command or `sandbox_read.py`. Do not chain `py_compile`, helper execution, inline Python inspection, validation, or finalization into one `sandbox_exec.py --cmd` call.
- Store pattern decisions in `output/extraction_strategy.json`.
- Store reusable workflow notes in the reference proposal files.
- If extraction requires guessing because the page lacks evidence, return `needs_review` with blockers instead of inventing fields.

## File Writing Helper

Use the sandbox skill's file-writing capability to write supporting scripts or serialized outputs so writes are audited. In workflow mode, scripts may assist or perform extraction when the agent records their role in `output/extraction_run.json` and `output/script_manifest.json`. Use sandbox execution for inspection and helper runs. Run protocol validation through `run_skill_script` with `scripts/validate_outputs.py --audit-id sandbox_run_*`. Do not use inline shell heredocs or inspection commands to write protocol files.

## Path Model

Host-side helper scripts use workspace-relative paths. `sandbox_exec.py --cmd` runs inside Docker with current working directory `/workspace`. Inside `sandbox_exec.py`, inspect mounted page evidence with commands like `pwd`, `ls`, `find . -maxdepth 2 -type f`, `sed -n '1,80p' page.html`, and `python output/evidence_pager.py`.

When running a helper, use one command for one purpose. Then run separate bounded checks or host validation/finalization calls. This lets the runtime observe progress and prevents duration guardrails from firing before the workflow reaches validation.

Never use host temp paths, ADK registry paths, or a returned host `workspace_path` inside `sandbox_exec.py`. If a path starts with `/var/folders`, `/private/var/folders`, `/tmp`, or a user home directory, treat it as host-side audit metadata and not as a Docker command path.

Use workspace-relative paths with `scripts/sandbox_write_file.py`: `scratch/inspect_cards.py`, `output/write_outputs.py`, `output/page_profile.json`, `output/extraction_strategy.json`, `output/extraction_run.json`, `output/script_manifest.json`, `output/candidates.json`, `output/validation.json`, `output/final.json`, `output/run_summary.md`, `evidence/index.json`, and `evidence/chunks/<id>.txt`; never `/workspace/...` or any other absolute path. The only success condition is produced artifacts: the required output files must exist, cite loaded evidence when evidence was chunked, record the run method, and pass validation/finalization. For repair after a helper exists, follow `sandbox-extraction-debugger`, read the current helper source, and use `scripts/sandbox_apply_patch.py` first, with full-file write only as the audited fallback for initial creation or unresolvable patch conflicts.

Host-control scripts are not sandbox shell commands. Invoke `sandbox_start.py`, `sandbox_read.py`, `sandbox_write_file.py`, `sandbox_apply_patch.py`, `sandbox_progress.py`, `validate_outputs.py`, and `sandbox_finalize.py` only through `run_skill_script`. Inside `sandbox_exec.py`, run shell inspection commands, parser checks, and bounded helper scripts.

## Listing Coverage

When the source is a listing/search page, extract the jobs listed on the page, not only the selected detail job. A `job_selected` query parameter is a focus hint for the UI; it is not permission to collapse a listing page to one job. If the mounted page exposes around 20 repeated job cards or detail URLs, the output should persist around 20 candidates unless it documents a deliberate filter rule or returns `needs_review` with blockers.

## Final Output Rules

Return one JSON object matching the outer sandbox result shape:

- `status`: `success`, `needs_review`, or `error`.
- `output_schema`: normally `job_extraction`.
- `summary`: short human-readable outcome.
- `protocol`: compact references to required protocol outputs.
- `result`: final schema payload.
- `artifact_handles`: compact artifact handles only.
- `error`: short error if failed.

Never return raw HTML, terminal transcripts, long stdout/stderr, or full scratch files.

The final result should come from accountable, evidence-cited output. The compact final response must include the saved output paths for `output/extraction_run.json`, `output/run_summary.md`, `output/candidates.json`, and `output/final.json`, plus `output/script_manifest.json` when supporting scripts were authored and `evidence/index.json` when evidence chunking was used. If the final JSON was manually assembled without saved evidence citations or run rationale, disclose that in `summary` and set `status` to `needs_review`.

## Protocol Summary

The final `protocol` object must include the current run's paths and hashes. The values below are example placeholders, not fixed values:

```json
{
  "page_profile": {"path": "output/page_profile.json", "sha256": "..."},
  "extraction_strategy": {"path": "output/extraction_strategy.json", "sha256": "..."},
  "extraction_run": {"path": "output/extraction_run.json", "sha256": "..."},
  "candidates": {"path": "output/candidates.json", "sha256": "..."},
  "validation": {"path": "output/validation.json", "sha256": "..."},
  "final": {"path": "output/final.json", "sha256": "..."},
  "run_summary": {"path": "output/run_summary.md", "sha256": "..."},
  "evidence_index": {"path": "evidence/index.json", "sha256": "..."},
  "valid": true,
  "warnings": []
}
```

Use SHA-256 hashes of each output file. If validation fails, return `needs_review` or `error`, set `valid` to false, and explain the blocker compactly.

`output/validation.json` must separately summarize the current run's validation facts with a boolean `valid`, a `checks` object, and counts derived from the current output. Example shape:

```json
{
  "valid": true,
  "checks": {"count_match": true, "required_fields_present": true},
  "candidate_count": "<len(candidates.jobs)>",
  "relevant_count": "<current relevant job count>",
  "warnings": []
}
```

Do not copy example counts. Do not use `status: "valid"` or `status: "success"` as a substitute for `valid: true`.

`scripts/sandbox_finalize.py` is a workflow protocol gate. It will not finalize an empty workflow result, and it keeps the sandbox running when required output files are missing or invalid.

## Self-Improvement

Always write a reference proposal at the end of workflow analysis:

- `output/run_summary.md`
- `output/reference_update_proposal.md` or `output/reference_proposal.md`
- `output/reference_proposal.json`

The run summary should explain what the agent did, which helper scripts were used, which evidence chunks were loaded, and how validation/finalization ended. The proposal should describe reusable page layout knowledge, step-by-step extraction workflow, selector or parsing patterns, validation checks, and known failure modes. It is a proposal only.

If supporting scripts were authored under `scratch/` or `output/`, write `output/script_manifest.json` and tie each script to the workflow/reference version and validation result. If you discover a useful skill or helper change, write `output/skill_update_proposal.md` or `output/skill_patch.json`.

Human approval is required before proposals become real references or skill edits.
