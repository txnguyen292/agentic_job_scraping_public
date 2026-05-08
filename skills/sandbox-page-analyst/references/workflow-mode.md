# Workflow Mode

Use this reference for real page analysis and job extraction. Workflow mode must produce extractor-backed protocol outputs before finalization and persistence.

## Required Workflow

1. Use the runtime `update_extraction_context` tool as a compact extraction notebook during the workflow. This context is session-only state, not a reusable reference and not repo-level project context.
2. Find the mounted page files and orient yourself with small, targeted inspections. Never ask for the whole HTML when a slice, count, selector probe, or metadata check would answer the next question.
3. If a command returns a truncated preview, reason from the preview first. Inspect a narrower slice only when the preview does not contain enough evidence.
4. Profile the page layout and derive extraction patterns from recurring job-post patterns before extracting jobs. Identify stable signals that denote one job post, such as repeated card containers, repeated detail URLs, embedded JSON item lists, selected-job URLs, title/company/location neighborhoods, salary/tag blocks, and pagination hints. Update session extraction context with exactly two required ideas: `observations` and `extraction_plan`.
5. Select one or two relevant references from `references/` when the page layout is recognizable. Use them as pattern guides, not as final answers. If the source URL is on `itviec.com` or the page contains ITviec listing markers, load `references/itviec-listing-page.md` before writing extractor code; its job URLs must be detail posting URLs ending in `-NNNN`, not category/search/navigation URLs. For ITviec, the primary loop must be one emitted job per repeated listing card; broad `/it-jobs/` URL scans are supporting evidence only.
6. Write reusable extractor code, implemented as Python, that reads local page artifacts, applies the recurring job-post patterns efficiently, and persists all required protocol outputs in one extraction pass: `output/page_profile.json`, `output/extraction_strategy.json`, `output/candidates.json`, `output/validation.json`, and `output/final.json`. `output/candidates.json` must be the candidate payload with top-level `jobs` and `crawl`; `output/final.json` must be the final result envelope whose `result` reuses that complete candidate payload. Prefer the Python standard library plus verified approved parser imports.
7. A valid workflow run must plan for all required protocol outputs before the first extractor run and produce them before validation, finalization, persistence, or database queries: `output/page_profile.json`, `output/extraction_strategy.json`, `output/candidates.json`, `output/validation.json`, and `output/final.json`. Do not wait for `persist_sandbox_job_extraction` to reveal missing protocol files.
8. Run the extractor inside the sandbox and fix it until its persisted output files contain the complete candidate set justified by page evidence. Extractor stdout is only a compact summary with counts and saved output paths, not the source of truth. Do not manually downsample, cherry-pick, or rewrite jobs after the extractor runs.
9. Run `scripts/validate_outputs.py` after the extractor has produced the required protocol files. Use the validator and `scripts/sandbox_finalize.py` as the authority for schema, URL-shape, count, and quality failures. If either script returns an error, treat that script as the read-only contract that produced the error: inspect its `--help`, and inspect bounded source or returned error details when needed, before choosing the next repair.
10. Compare the extractor output against the requirement only through concrete evidence: validator/finalizer errors, explicit reference expectations, or directly inspected malformed records. Reconcile the new result with the injected session extraction context. Do not judge that a count is "too broad" or "too narrow" from intuition alone.
11. Update the session extraction context with the reconciled comparison. If validation/finalization reports a concrete failure, inspect why the script failed, update the recorded `observations` and `extraction_plan`, then iterate on the extractor.
12. If the extractor produces all required protocol files and there is no concrete validation/finalization error yet, the next action is validation/finalization. Do not rewrite the extractor again just because the result feels uncertain.
13. Protocol files must be written by `output/extractor.py`. If `output/candidates.json`, `output/final.json`, or another required protocol file is wrong, patch `output/extractor.py` and rerun it instead of manually patching required protocol JSON files.
14. Write a human-reviewable reference proposal. If no reusable pattern was found, write a short proposal explaining why.
15. Validate the persisted outputs. If validation fails, repair the extractor or protocol files before finalizing.
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
- Latest exact tool output is evidence that may correct either context. If new evidence changes `observations`, `extraction_plan`, `last_result`, `known_errors`, or `immediate_goal`, call `update_extraction_context` before continuing.
- If sandbox notes conflict with session context, verify with exact available tool output or a focused sandbox inspection, then update session context.
- Before every tool call and final response, inspect `<SESSION_EXTRACTION_CONTEXT>` and derive the next logical action from `final_goal`, `known_errors`, `attempted_actions`, `immediate_goal`, `last_result`, `extraction_plan`, and `observations`.
- If latest tool results show an error is solved, call `update_extraction_context` to remove or rewrite that stale error. If `attempted_actions` shows repeated probes that did not help, choose a state-changing action instead of probing again.

## Session Extraction Notebook

Use `update_extraction_context` for compact reasoning checkpoints, not raw data storage. The runtime injects the latest session context before model calls.

- Before each meaningful extractor attempt, update session context with `observations`, `extraction_plan`, and the current `immediate_goal`.
- Before choosing the next tool, finalizing, persisting, querying, or answering, refer to the latest injected session state for next-step guidance, especially `immediate_goal`, `known_errors`, `last_result`, `observations`, and `extraction_plan`.
- Read `last_result` first after every tool call. Decide what it proves, whether it satisfies `immediate_goal`, and whether it creates, confirms, or resolves any `known_errors`.
- Check `attempted_actions` before acting. Do not repeat actions that did not change state; choose a state-changing repair, validation, finalization, promotion, or query action instead.
- Keep state compact and operational: store the current failure, the relevant contract fact, the repair rationale, and the next tool choice; do not store raw HTML, long stdout/stderr, full stack traces, broad source dumps, or duplicated history.
- If a validator, finalizer, patch helper, or other helper script produced the current error, inspect that error-producing script as a read-only contract when the returned message is not enough to choose a precise repair. The next step may be inspecting another related script, schema, or generated artifact; record the rationale in state before taking that step.
- If extractor outputs exist and no concrete validation/finalization error is active, the next objective is validation/finalization, not another extractor rewrite.
- After a repairable validation/finalization/schema/count error, the session context must declare `planned_next_tool`: the exact next efficient tool call chosen from available tool names. For sandbox helpers, include `tool_name`, `skill_name`, and `file_path`. The next tool call must match this plan unless new evidence is first recorded with a revised `planned_next_tool`.
- During repair, also declare `repair_scope`: a bounded work order with `status`, `objective`, `files`, optional `allowed_resources`, optional `allowed_inspections`, and a `verification` command when ready. Use `repair_scope` only after a concrete sandbox artifact error; do not use it for normal skill loading, page fetching, or sandbox startup. Keep each scope small enough that the next patch or verification can prove progress before expanding to more documents or files.
- If the declared `planned_next_tool` runs and fails, treat that failed tool output as new evidence. Update session context with the failed invariant, what the attempt proved, and the revised `planned_next_tool` before continuing.
- `observations` means the page clues the agent found: repeated containers, URL attributes, text neighborhoods, embedded data, pagination hints, counts, and evidence paths.
- `extraction_plan` means how to derive the required outputs from those clues: selectors, parsing rules, URL normalization, filters, expected candidate count, and output files.
- Observations must include implementation cues, not just counts. If you detect repeated structures, record the selector, attribute names, text boundaries, URL fallback order, and expected loop cardinality before writing extractor code.
- The extraction plan must translate those cues into code-level steps: which collection to iterate, how to build each `job_url`, where title/company/location come from, how to deduplicate, and which count must match validation.
- `attempted_actions` means compact records of actions that already ran and whether they helped, such as existence checks, placeholder inspection, extractor writes, extractor runs, validation attempts, and failed probes.
- After running the extractor, compare `output/candidates.json` and `output/final.json` against the requirement, and decide whether the new result confirms or contradicts the current observations and extraction plan.
- Do not convert uncertainty into a repair judgment. If the extractor produced all required protocol files and the only concern is quality/count uncertainty, run `scripts/validate_outputs.py` or `scripts/sandbox_finalize.py` next and let that returned error, if any, define the repair target.
- Do not call a count "too broad" or "too narrow" unless validator/finalizer output, a loaded site reference, or directly inspected malformed records proves the mismatch. A count equal to the observed repeated card count is not by itself an error.
- If the new result contradicts the notes, refine the observations or extraction plan before changing extractor code.
- If observed listing evidence and extractor output counts diverge, record the mismatch once, then repair the extractor immediately. Do not keep calling `update_extraction_context` for the same mismatch without a state-changing action.
- For ITviec, if observations record 20 repeated listing cards and the extractor emits 1, 5, 64, 114, or any other mismatched count, the next state-changing action is to patch `output/extractor.py` so it loops over the recorded card selector and regenerates candidates/final from that loop.
- If the output is wrong, do not add extra process categories. Instead, update the observations or extraction plan, fix the extractor, and run again.
- If a repair needs more documentation or file inspection, update `repair_scope` with the specific allowed resource or inspection and why it is needed. Do not load broad docs or rewrite broad script sections just because the previous patch failed.
- Apply one small coherent patch or patch set for the active `repair_scope`, then run the declared verification command before deciding whether a wider scope is needed.
- If `attempted_actions` already records missing-output checks or placeholder inspection, do not repeat those checks. Move to the next action that can change the state, normally writing or replacing `output/extractor.py` through the file-writing capability.
- If the output is correct, the final note should make clear why the extractor is reliable enough to support the reference proposal.

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
    "Iterate soup.select('[data-search--pagination-target=\"jobCard\"]') once per emitted job.",
    "For each card, build job_url from data-search--job-selection-job-url-value; fallback to parsing the job query value from /sign_in?job= and prefix https://itviec.com/it-jobs/.",
    "Extract title from the jobTitle target; fallback to a humanized detail slug only if title text is absent.",
    "Deduplicate by normalized detail job_url, not company preview URL.",
    "Write output/candidates.json and output/final.json with exactly the observed card count unless a documented filter explains otherwise."
  ]
}
```

## Error Repair Loop

Returned errors are repair evidence, not terminal answers by default.

- For validation, finalization, schema, count, URL-shape, missing-file, stale-output, or repeated-repair errors, load `sandbox-extraction-debugger`. That skill owns repair policy.
- If a shell command exits non-zero, inspect bounded `stderr` or the persisted stderr slice, fix the command or code, and rerun.
- If validation reports a schema/protocol error, address that exact error in the sandbox workspace, rerun the producer when needed, then rerun validation.
- If finalization reports missing files, invalid protocol, or an expected-vs-actual job count mismatch, enter the `sandbox-extraction-debugger` workflow, repair the sandbox workspace, and retry finalization.
- If finalization reports missing files, update session context with a concrete `planned_next_tool` before continuing. The usual efficient next tool is `run_skill_script` using `scripts/sandbox_apply_patch.py` targeting `output/extractor.py`, because the producer must write the missing protocol outputs.
- A finalize count mismatch is not a final-answer condition. Treat it as flawed extractor implementation and follow the debugger skill: inspect the page/card evidence and `output/extractor.py`, apply the smallest producer repair, rerun the extractor, then validate/finalize again.
- Only return `needs_review` when page evidence is insufficient after focused inspection, not because the first extractor attempt failed.

## Code-First Extraction Rules

- Do not hand-write job records when a pattern can be encoded.
- Treat extraction as pattern discovery plus code generation: first derive recurring job-post patterns and prove which repeated structures denote job posts, then encode those structures in Python.
- Treat repeated job-card markers, repeated detail URL attributes, and repeated site-specific controller/data attributes as positive page evidence. Do not classify a listing page as having "no stable evidence" while such repeated markers exist.
- Store implementation in `output/extractor.py`.
- The extractor's persisted output files are the source of truth for every required protocol output; stdout should only report counts and paths.
- The first runnable extractor version should write all five required protocol files, not just `candidates.json` and `final.json`. If validation says any required protocol output is missing, patch `output/extractor.py` so the next run creates the full required set.
- The extractor stdout must surface the saved output paths, at minimum `candidates_path` and `final_path`, so the main agent can decide which persisted output to inspect or report.
- The same extraction pass must also support `output/page_profile.json`, `output/extraction_strategy.json`, and `output/validation.json`; create these protocol files before validation/finalization instead of treating them as late cleanup.
- `output/candidates.json` must have top-level `source`, `jobs`, `selectors`, `crawl`, and `warnings`. Never write candidates as `{ "status": "success", "result": { "jobs": [...] } }`.
- `output/final.json` must have top-level `status`, `output_schema`, `summary`, `result`, and `protocol`, where `result` is the complete candidate payload from `output/candidates.json`.
- do not emit JSON `null` for string fields. If evidence is absent, use `""` or `"unknown"` and document uncertainty in validation warnings.
- Do not overwrite extractor-produced outputs with placeholders. If a required protocol file is missing or invalid, repair the extractor or rerun it so the protocol files are derived from real page evidence and real extracted jobs.
- If `final.json` schema or count validation fails, do not manually assemble a partial `final.json`. Patch `output/extractor.py` or run a focused repair script that reads the complete extractor-backed candidates payload and regenerate `candidates.json` and `final.json` from the same full payload.
- Do not repair `final.json` by writing one sampled job, by putting `count`/`jobs` only at the top level, or by using an invalid shape such as `{"result":"success"}`. A successful `output/final.json` needs top-level `status: "success"` and a `result` object containing the complete jobs payload.
- If the extractor emits 20 valid candidates, the protocol should carry those 20 candidates unless the extractor itself applies and documents a filter rule.
- If the extractor stdout reports a plausible count and saved protocol paths, do not rewrite the extractor just to gain confidence. Validate the files with `scripts/validate_outputs.py`, then finalize or repair from the concrete returned error.
- Store pattern decisions in `output/extraction_strategy.json`.
- Store reusable workflow notes in the reference proposal files.
- If extraction requires guessing because the page lacks evidence, return `needs_review` with blockers instead of inventing fields.

## File Writing Helper

Use the sandbox skill's file-writing capability to write `output/extractor.py` so writes are audited. In workflow mode, required protocol JSON files should be produced by running the extractor, not manually patched through file-writing calls. Use sandbox execution for inspection and running the extractor. Run protocol validation through `run_skill_script` with `scripts/validate_outputs.py --audit-id sandbox_run_*`. Do not use inline shell heredocs or inspection commands to write protocol files.

## Path Model

Host-side helper scripts use workspace-relative paths. `sandbox_exec.py --cmd` runs inside Docker with current working directory `/workspace`. Inside `sandbox_exec.py`, inspect mounted page evidence with commands like `pwd`, `ls`, `find . -maxdepth 2 -type f`, `sed -n '1,80p' page.html`, and `python output/extractor.py`.

Never use host temp paths, ADK registry paths, or a returned host `workspace_path` inside `sandbox_exec.py`. If a path starts with `/var/folders`, `/private/var/folders`, `/tmp`, or a user home directory, treat it as host-side audit metadata and not as a Docker command path.

Use workspace-relative paths with `scripts/sandbox_write_file.py`: `output/extractor.py`, `output/page_profile.json`, `output/extraction_strategy.json`, `output/candidates.json`, `output/validation.json`, and `output/final.json`; never `/workspace/output/extractor.py` or any other absolute path. When writing `output/extractor.py`, include those exact literal `output/<name>.json` strings in the source for each required protocol output, for example `Path("output/page_profile.json").write_text(...)`. Do not rely only on composed filename-only writes like `OUT / "page_profile.json"` because the runtime contract gate verifies exact output filenames in the producer source. Use `sandbox_write_file.py` for initial extractor creation. For repair after an extractor exists, follow `sandbox-extraction-debugger` and use `scripts/sandbox_apply_patch.py` first, with full-file write only as the audited fallback for initial creation or unresolvable patch conflicts.

Host-control scripts are not sandbox shell commands. Invoke `sandbox_start.py`, `sandbox_read.py`, `sandbox_write_file.py`, `sandbox_apply_patch.py`, `sandbox_progress.py`, `validate_outputs.py`, and `sandbox_finalize.py` only through `run_skill_script`. Inside `sandbox_exec.py`, run shell inspection commands, parser checks, and `python output/extractor.py`.

## Listing Coverage

When the source is a listing/search page, extract the jobs listed on the page, not only the selected detail job. A `job_selected` query parameter is a focus hint for the UI; it is not permission to collapse a listing page to one job. If the mounted page exposes around 20 repeated job cards or detail URLs, the extractor should persist around 20 candidates unless it documents a deliberate filter rule or returns `needs_review` with blockers.

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

The final result should come from `output/extractor.py` output. The compact final response must include the saved output paths for at least `output/candidates.json` and `output/final.json`, either in `protocol` or `artifact_handles`. If the final JSON was manually assembled, disclose that in `summary` and set `status` to `needs_review` unless the manual step only wrapped already extractor-produced data.

## Protocol Summary

The final `protocol` object must include:

```json
{
  "page_profile": {"path": "output/page_profile.json", "sha256": "..."},
  "extraction_strategy": {"path": "output/extraction_strategy.json", "sha256": "..."},
  "candidates": {"path": "output/candidates.json", "sha256": "..."},
  "validation": {"path": "output/validation.json", "sha256": "..."},
  "final": {"path": "output/final.json", "sha256": "..."},
  "valid": true,
  "warnings": []
}
```

Use SHA-256 hashes of each output file. If validation fails, return `needs_review` or `error`, set `valid` to false, and explain the blocker compactly.

`scripts/sandbox_finalize.py` is a workflow protocol gate. It will not finalize an empty workflow result, and it keeps the sandbox running when required output files are missing or invalid.

## Self-Improvement

Always write a reference proposal at the end of workflow analysis:

- `output/reference_proposal.md`
- `output/reference_proposal.json`

The proposal should describe reusable page layout knowledge, step-by-step extraction workflow, selector or parsing patterns, validation checks, and known failure modes. It is a proposal only.

If you discover a useful skill or helper change, write `output/skill_patch.json`.

Human approval is required before proposals become real references or skill edits.
