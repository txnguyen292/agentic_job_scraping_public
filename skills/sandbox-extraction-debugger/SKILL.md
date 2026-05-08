---
name: sandbox-extraction-debugger
description: Use when a sandbox workflow encounters errors, failing tests, validation failures, repeated failed attempts, or broken generated scripts/artifacts.
allowed-tools: run_skill_script
---

# Sandbox Extraction Debugger

Use this skill as the generic sandbox debugging and repair protocol. When a sandbox task has an error, failing command, failing validation, broken generated script, stale output, or repeated failed attempt, follow this skill before attempting another repair.

## Boundary

Only modify Docker sandbox workspace artifacts.

Allowed write targets are workspace-relative sandbox artifacts such as:

- `output/<generated-script>.py`
- `output/<generated-data>.json`
- `output/<proposal>.md`
- `progress.json`

Do not modify host repo files, skill source files, mounted helper scripts under `scripts/`, schemas, references, or project code. You may inspect helper scripts, schemas, references, and tests to understand expected behavior, but fixes must be expressed by changing sandbox output artifacts.

## Debugging Loop

1. Restate the failure in one sentence: command/tool, expected behavior, actual behavior, and the exact error or mismatched output.
2. Locate the failing layer before editing:
   - usage error: wrong tool, wrong script, wrong arguments, wrong order, missing prior step, absolute path where workspace-relative path is required, or blocked host-control/network behavior
   - implementation bug: the right command ran but a sandbox script/artifact has wrong logic, wrong shape, wrong count, stale output, missing fields, or inconsistent files
   - constraint mismatch: the candidate fix would violate schemas, validators, guardrails, no-network policy, workspace boundaries, or previously working behavior
3. Inspect only the relevant files needed to localize the failure. Start with the errored command output, named files, `output/`, `progress.json`, `trace.jsonl`, and any producing script. If the error came from `scripts/validate_outputs.py`, `scripts/sandbox_finalize.py`, `scripts/sandbox_apply_patch.py`, or another helper script, inspect that error-producing helper as a read-only contract when the returned message is not enough to choose a precise next action. Read mounted helper scripts, schemas, references, and validators as read-only contracts.
4. Write or run a focused failing test/probe before changing code when the repair is nontrivial. Keep it sandbox-local and small: an assertion script, a short Python check, or a validator invocation that fails for the current bug.
5. Identify the minimum working code change that should make the focused test/probe pass while preserving existing constraints.
6. Before the next tool call, update session context with the rationale for the next action, the most efficient concrete next tool invocation in `planned_next_tool`, and a bounded `repair_scope`. Use available tool names, not prose only. For sandbox helper calls this means `tool_name: "run_skill_script"`, `skill_name: "sandbox-page-analyst"`, and the exact helper `file_path`.
7. Patch the producing sandbox artifact with `scripts/sandbox_apply_patch.py`, not the downstream symptom. Use full-file write only for initial creation, corrupt/missing files, or an unresolvable patch conflict.
8. Run the focused test/probe, then rerun the original failing command or validation.
9. Inspect compact outputs and compare them with the original failure and known constraints.
10. If the error persists, update the diagnosis and repeat with a distinct hypothesis. Do not repeat the same patch or invocation unchanged.
11. When fixed, update session context with the resolved failure, changed files, tests/probes run, and remaining risks before continuing the main workflow.

Do not answer the user while a repairable sandbox error is still failing and sandbox guardrails have not stopped the run.

## Test-First Repair

- For every generated script that performs nontrivial logic, create or run at least one focused test/probe before finalizing the repair.
- The test/probe should encode the observed failure and the behavior that must not regress.
- Prefer small sandbox-local checks such as `python output/test_<name>.py`, `python -m py_compile output/<script>.py`, schema validation, or a command that inspects the produced artifact shape.
- If a test file is needed, write it under `output/` and keep it focused. Do not create host repo tests from inside the sandbox.
- After the patch passes the focused test/probe, run the broader workflow validation/finalization command that originally failed.

## Regression Constraints

Before patching, list the constraints the fix must preserve. Common constraints include:

- no host repo edits and no writes outside the sandbox workspace
- no network or package installation inside the sandbox
- mounted `scripts/`, schemas, validators, and references are read-only contracts
- protocol JSON must satisfy the schema validators
- generated protocol files should come from the producer logic, not hand-edited symptoms
- existing passing behavior should stay passing unless the latest evidence proves it was wrong
- artifact paths must remain workspace-relative

If a proposed fix would violate a constraint, choose a smaller or different producer change.

## Patch-First Rule

For repair after a script or artifact has already been created, patch-first is the official policy.

- Prefer a small patch to the producing sandbox artifact over rewriting the full file.
- Full file writes are appropriate for initial creation, for replacing a corrupt or missing file, or after a patch attempt fails with a clear conflict that cannot be resolved from the available context.
- Use `scripts/sandbox_apply_patch.py` for repair edits to existing sandbox scripts/artifacts.
- Use exact replacement mode (`--path --old --new`) for small localized repairs. Use unified diff mode (`--patch`) for multi-hunk repairs.
- If patch application fails because the context does not match, inspect the current file and submit a corrected smaller patch. Do not fall back to a full-file rewrite until a corrected patch is impossible from available context.
- Full file writes are the audited fallback only for initial creation, corrupt/missing files, or a patch conflict that cannot be resolved after inspection.
- Never patch required protocol JSON files directly just to satisfy validation. Patch the producer so the next producer run regenerates derived outputs from the same evidence-backed data.
- If the patch or write fails, treat that failure as debugging evidence; inspect the current file and retry with a corrected minimal change rather than repeating the same invocation.

## Planned Next Tool Contract

After a repairable error, the session context must become an operational work order. Do not write only "repair needed".

The context update must include:

- `known_errors`: the active failure.
- `last_result`: the exact failing invariant or tool result.
- `immediate_goal`: the concise repair objective.
- `rationale`: why the next action is the most efficient step given the latest error, relevant contract facts, and attempted actions.
- `repair_scope`: the bounded work order for this repair, including objective, files to patch, allowed resources/inspections, status, and `verification` when ready.
- `planned_next_tool`: the next efficient tool call chosen from available tool names.

Keep session state compact enough to fit inside one model call. Include only the current invariant, contract facts, inspected files, attempted actions that changed the repair decision, and the next tool plan. Do not store raw HTML, full script source, long stdout/stderr, complete stack traces, or repeated history in state.

If the next action is to inspect another script, schema, or generated artifact, write that rationale into state first. The state should explain why that inspection is more efficient than patching immediately.

Example after `sandbox_finalize.py` reports missing protocol files:

```json
{
  "known_errors": [
    "sandbox_finalize.py rejected missing output/page_profile.json, output/extraction_strategy.json, output/validation.json, output/final.json"
  ],
  "last_result": {
    "missing_files": [
      "output/page_profile.json",
      "output/extraction_strategy.json",
      "output/validation.json",
      "output/final.json"
    ]
  },
  "immediate_goal": "Patch output/extractor.py so one extractor run writes all required protocol outputs.",
  "rationale": "sandbox_finalize.py is enforcing the protocol-file contract; output/extractor.py is the producer, so the efficient repair is to patch the producer rather than hand-write missing JSON.",
  "repair_scope": {
    "status": "patching",
    "objective": "Make output/extractor.py write all required protocol files from one candidate payload.",
    "files": ["output/extractor.py"],
    "allowed_resources": ["references/itviec-listing-repair.md"],
    "allowed_inspections": ["output/extractor.py", "output/candidates.json"]
  },
  "planned_next_tool": {
    "tool_name": "run_skill_script",
    "skill_name": "sandbox-page-analyst",
    "file_path": "scripts/sandbox_apply_patch.py",
    "target_paths": ["output/extractor.py"],
    "intent": "patch producer to write missing protocol files"
  }
}
```

The next tool call must match `planned_next_tool`. If new evidence makes the plan wrong, update session context first with the new evidence and a replacement `planned_next_tool`. If the planned tool runs and fails, treat that failure as new evidence: update session context with the failed invariant, what the attempt proved, and the revised `planned_next_tool` before continuing.

When the patch is ready to verify, update `repair_scope.status` to `ready_to_verify`, set `repair_scope.verification` to the exact sandbox command such as `python output/extractor.py`, and set `planned_next_tool` to `scripts/sandbox_exec.py` with `args_must_include` containing that command. Do not expand the repair scope until that verification result proves a new blocker.

## Usage Versus Implementation

Incorrect usage examples:

- calling `scripts/validate_outputs.py` through `scripts/sandbox_exec.py`
- using `/workspace/output/<file>` instead of `output/<file>` with write/read helpers
- calling finalization before required generated outputs exist
- using `sandbox_exec.py` to write files instead of `scripts/sandbox_write_file.py`
- omitting `--audit-id` or passing the wrong active audit ID

Implementation bug examples:

- generated script ignores the data source or test case that triggered the failure
- generated script uses an over-broad heuristic and produces extra records
- generated script uses an over-narrow heuristic and misses required records
- related output files were generated from different in-memory data
- required fields are `null` or wrong types
- output counts, validation flags, and actual payloads disagree

Only inspect producing script logic after classifying the failure as an implementation bug, or after corrected usage still fails.

## Site-Specific Repair References

Keep this skill generic. When the source domain, page markers, session context, or validator error points to a known site/layout, load the relevant reference and use it as a concrete repair guide.

- Known repair references include `references/itviec-listing-repair.md`. Load a specific reference only when the source domain, page markers, session context, or validator error clearly matches it.
- If no site-specific reference exists, use the generic repair workflow: identify the failing data source or invariant, patch the producer, rerun the focused test/probe, validate, and finalize.

## Sandbox Tool Use

Use the sandbox tools exposed through `run_skill_script`.

- Sandbox helper scripts live under the `sandbox-page-analyst` skill. When invoking any sandbox helper, call `run_skill_script` with `skill_name: "sandbox-page-analyst"`, not `skill_name: "sandbox-extraction-debugger"`.
- Inspect workspace files with `scripts/sandbox_read.py`.
- Run focused shell/Python probes with `scripts/sandbox_exec.py`.
- Patch existing sandbox artifacts with `scripts/sandbox_apply_patch.py`.
- Create initial sandbox artifacts with `scripts/sandbox_write_file.py`; use it for repair only when patching is impossible after inspection.
- Track compact progress with `scripts/sandbox_progress.py` when a repair branch becomes nontrivial.

Use these tools against the active audit ID. Do not use host filesystem tools to edit sandbox outputs.

`scripts/sandbox_apply_patch.py` is the dedicated patch helper. Use it for repair edits to existing producer scripts. `scripts/sandbox_write_file.py` remains the audited fallback for initial creation or unresolvable patch conflicts.

## Source Triage

- producing scripts under `output/`: business logic, parsing, filtering, serialization, or protocol-file generation may be wrong.
- generated JSON under `output/`: output shape, count, field values, envelope consistency, or stale data may reveal the bug.
- `progress.json` and `trace.jsonl`: recent attempts, guardrails, command order, and repeated failures.
- mounted helper scripts under `scripts/`: read only to understand tool behavior and validator/finalizer rules; do not edit them.
- schemas under `schemas/`: read only to understand required shapes; do not edit them.

## Common Repair Patterns

- Missing generated files: patch the producer so one run writes every required output from the same source data.
- Shape mismatch: inspect the schema/validator, then patch the producer serialization instead of hand-editing the generated JSON.
- Too many outputs: find the over-broad heuristic and add the smallest filter that preserves known valid cases.
- Too few outputs: find the over-narrow heuristic or selected-item-only branch and widen it only enough to satisfy the focused test/probe.
- Cross-file mismatch: regenerate related files from one shared in-memory object instead of independently assembling each file.
- Null or wrong field types: patch field extraction/defaults in the producer so every emitted field satisfies the schema.

## Example Failure Shape

```text
error source: validator or finalizer
failed invariant: expected output shape/count/value differs from generated artifact
involved files: producing script, generated artifact, validator/schema, trace/progress
script logic to inspect: the smallest function or branch that creates the bad output
focused test/probe: assert the producer handles the failing input and preserves one known-good case
allowed fix: patch the producing script or artifact generator
disallowed fix: edit read-only validators/schemas or hand-write derived outputs to fake success
```

## Stop Conditions

Stop debugging and report a blocker only when:

- a runtime guardrail has triggered
- required evidence/input is absent after focused inspection
- an approved dependency is missing from the sandbox image
- the same invariant fails after distinct producer-logic repairs and the next attempt would repeat a previous action

Do not report a blocker immediately after the first repairable validation/finalization failure when sandbox commands are still available. That error is actionable repair feedback; make at least one distinct producer-logic repair unless the evidence needed for the repair is absent.

When reporting a blocker, include the audit ID, failing invariant, involved files, script logic inspected, sandbox artifacts modified, and last validator/finalizer error.
