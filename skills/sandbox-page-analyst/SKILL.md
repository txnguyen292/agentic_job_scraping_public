---
name: sandbox-page-analyst
description: Analyze mounted job listing pages or run bounded sandbox diagnostics without browsing the web.
---

# Sandbox Page Analyst

You analyze files already mounted inside a no-network sandbox. Do not browse the web or fetch remote URLs from inside the sandbox.

Never run `curl`, `wget`, browser automation, `scrapling`, `requests`, `httpx`, `urllib`, DNS tools, package installers, or any command whose purpose is to access the internet. If expected files are missing, inspect `variables.json`, `inputs.json`, `plan.md`, `progress.json`, and mounted page files first; if still missing, return a blocker.

The sandbox worker is still LLM-driven. You may reason across turns and use the model normally. The network restriction applies to shell/container internet egress, not host-mediated model calls.

## Path Model

There are two path spaces:

- Host-side helpers (`sandbox_read.py`, `sandbox_write_file.py`, `sandbox_apply_patch.py`, `validate_outputs.py`, `sandbox_finalize.py`) use workspace-relative paths such as `page.html`, `progress.json`, and `output/extractor.py`.
- `sandbox_exec.py --cmd` runs inside Docker with current working directory `/workspace`. Inside that command, use `page.html`, `inputs.json`, `progress.json`, `output/extractor.py`, `pwd`, `ls`, and `find .`.

Do not `cd` into host temp paths, ADK registry paths, or any `workspace_path` value. Host paths are audit metadata only; they are not shell paths inside Docker.

## Mode Router

Before starting or continuing a sandbox, identify the mode:

- `diagnostic`: simple probes, stdout/stderr contract tests, dependency checks, file inspection, or one-off commands.
- `workflow`: real page analysis and job extraction.
- `debug`: runtime investigation with extra audit intent; follows diagnostic protocol unless the user explicitly asks for full extraction.

Load exactly one mode reference before acting:

- For `diagnostic` or `debug`, load `references/diagnostic-mode.md`.
- For `workflow`, load `references/workflow-mode.md`.

Do not load a second mode reference in the same sandbox task. If you loaded the wrong mode reference, stop and report the mismatch instead of loading another mode reference.

Do not upgrade a diagnostic request into workflow mode. If the user asks only to test stdout, inspect a dependency, or run a small command, keep the run diagnostic and answer after the requested command results.

For workflow mode, after loading the workflow reference and any site-specific reference, update session context with the required output and evidence contract before starting the workflow sandbox or writing/running helper scripts:

- `loaded_resources`: the workflow reference and any site-specific reference just loaded, plus the cues or contract each one adds.
- `final_goal`: the user's requested extraction outcome; keep it stable and do not narrow it after loading a selected-detail or site reference.
- `immediate_goal` and `planned_next_tool`: the next concrete action that carries out the loaded instructions.
- `required_outputs`: `output/page_profile.json`, `output/extraction_strategy.json`, `output/extraction_run.json`, `output/candidates.json`, `output/validation.json`, `output/final.json`, and `output/run_summary.md`.
- `workflow_contract.agent_role`: the agent chooses and owns the extraction method, including any supporting scripts it decides to write.
- `workflow_contract.script_role`: scripts may inspect, parse, extract, validate, serialize, chunk evidence, or estimate token budgets when the agent records their purpose and results.
- `evidence_contract`: exact chunks live under `evidence/chunks/`, are indexed by `evidence/index.json`, and every nontrivial extracted field cites loaded chunk ids with rationale.
- `extraction_strategy`: after the first representative repeated job unit or evidence chunk is inspected, record the current extraction method: target units, unit boundary, count method, field patterns, known exclusions, coverage plan, and why the evidence supports the strategy. Follow it by default, enhance it when new evidence adds useful detail, and revise it when later evidence or validation/finalization contradicts it.
- `expected_output`: after repeated-pattern observations and before writing `output/candidates.json` or `output/final.json`, declare `expected_job_count`, `count_basis`, `count_rationale`, and required evidence coverage. `count_rationale` must state how past actions or tool results established the expected count. The saved job count must match this expectation unless the output is explicitly `needs_review` with evidence-backed rationale.
- `workflow_contract.success_gate`: all required outputs exist, validate, and finalization succeeds before persistence or final response.
- `workflow_contract.repair_rule`: mistakes are repaired at the layer that caused them: observations/evidence, extraction method, supporting script, serialized output, or proposal artifacts. Record the rationale before acting.

## Page Classification

In workflow mode, classify the mounted page before writing helper scripts or authoring outputs. Use the source URL, `variables.json`, `inputs.json`, page title, repeated markers, and small HTML probes to choose the closest page class.

Known classes today:

- `itviec-listing`: source URL contains `itviec.com`, or page evidence includes ITviec markers such as `.job-card`, `[data-search--pagination-target="jobCard"]`, `[data-search--job-selection-job-url-value]`, or `[data-search--job-selection-target="jobTitle"]`. For this class, load `references/itviec-listing-page.md` from the `sandbox-page-analyst` skill before writing helper scripts or protocol outputs.

For ITviec, do not write `output/page_profile.json`, `output/extraction_strategy.json`, helper scripts, or any final payload from only `fetch_page_to_workspace` signals or the `job_selected` URL. First run a bounded sandbox probe with `sandbox_exec.py --cmd` to count repeated cards/detail URLs, then create exact evidence chunks for the repeated units. A listing page with around 20 repeated cards should not become a one-job selected-detail extraction.

When your observations say there are N repeated job units, update session context with `expected_output.expected_job_count = N`, cite the observation in `count_basis`, and write `count_rationale` explaining which past action/tool result exposed the repeated units and why that implies N listings before writing candidates/final outputs. If your extracted postings count differs from N, stop and load/inspect the missing evidence chunks or mark the run `needs_review`; do not save a successful partial output.

When runtime returns `unsatisfied_requirements`, use them as facts to reason from, not as a fixed tool recipe. First inspect how you came to believe the expected count: review `observations`, `count_basis`, `attempted_actions`, and the latest tool results that established the repeated-unit signal. Then inspect available tools/resources and choose how to satisfy the missing prerequisite yourself. If the unmet requirement says successful output has fewer jobs than expected, the usual missing prerequisite is evidence coverage or omitted loaded units: use the same repeated-unit signal and available tooling to create/load evidence for every expected unit before writing another successful candidates/final payload.

After a meaningful failure, repeated failed attempt, or returned `unsatisfied_requirements`, add or update `workflow_reflections` in `update_extraction_context`. A reflection is a compact learned interpretation of the failure pattern: what the failure implies, the diagnostic question it raises, which actions would change state, and which anti-actions would repeat the failure. Use reflections to improve the next plan, not as fixed tool recipes.

If no specific site reference matches, continue with generic recurring-pattern extraction: identify repeated job containers or repeated detail URLs, choose an extraction method that fits the page and context limits, and record the observations, method, supporting scripts, evidence, citations, and rationale that justify the output.

## Script Catalog

This section is the compact script catalog. Do not load a separate catalog or script manual file. Use each script's `--help` output when exact arguments are needed. Use script source loading only when debugging the script itself.

- `scripts/sandbox_start.py`: start or reconnect to a no-network Docker sandbox. Use workflow mode with `--page-artifact` for real page extraction. Its host audit paths are not Docker shell paths; `sandbox_exec.py` starts in `/workspace`.
- `scripts/protocol_contract.py`: emit the compact accountable output contract before authoring `output/*.py` producer scripts or protocol result files. Load this once, then update `SESSION_EXTRACTION_CONTEXT` with `output_contract` and the agent's own `producer_output_plan`; do not paste the full workflow/reference text into long-running context.
- `scripts/sandbox_exec.py`: run bounded bash inspection commands inside the active sandbox with current working directory `/workspace`. Pass commands with `--cmd "<shell command>"`; do not use pass-through args after `--`. Use for reading page evidence with `ls`, `find .`, `sed -n '1,80p' page.html`, checking parser imports, and running supporting scripts that discover repeated patterns, write exact evidence chunks, estimate tokens, extract, validate, or serialize outputs. Keep helper execution observable: run one helper command at a time, then inspect or validate in separate calls. Do not chain helper execution with `py_compile`, inline Python probes, file reads, validation, or finalization in one shell command. Do not run host-control scripts such as `scripts/sandbox_start.py`, `scripts/sandbox_read.py`, `scripts/sandbox_write_file.py`, `scripts/validate_outputs.py`, or `scripts/sandbox_finalize.py` through this tool. Do not `cd` into host temp paths.
- `scripts/sandbox_write_file.py`: write files into the sandbox workspace using workspace-relative paths only. Use workspace-relative paths such as `scratch/inspect_cards.py`, `output/write_outputs.py`, or `output/extractor.py`, not `/workspace/...`. In workflow mode, use this helper for supporting scripts or serialized outputs the agent chooses. If a script contributes to extraction, record it in `output/script_manifest.json` with path, purpose, inputs, outputs, hash, workflow/reference version, reuse classification, and validation result. For repair after validation/finalization errors, load `sandbox-extraction-debugger` and follow its repair protocol; use this helper only as the audited fallback when no patch executor exists.
- `scripts/sandbox_apply_patch.py`: apply targeted repair edits to existing sandbox workspace files. Use exact replacement with `--path --old --new` for small edits, or `--patch` for unified diffs or Codex `*** Begin Patch` patches. Prefer this over full-file writes when repairing an existing helper script or generated sandbox artifact.
- `scripts/sandbox_read.py`: read a bounded preview from a persisted sandbox file when the agent needs a slice. Use `--max-chars`, not `--max-bytes`.
- `scripts/sandbox_litellm_call.py`: run a generic host-mediated LiteLLM call using `--messages-json` and optional `--response-format-json`. The Docker sandbox remains no-network; use this when another model call helps analyze bounded evidence, design typed request/response shapes, or debug generated code. Persist the response with `--output-path` when it should become an audited sandbox artifact.
- `scripts/sandbox_progress.py`: write compact per-run progress state. Do not store raw HTML or long terminal output.
- `scripts/validate_outputs.py`: validate protocol files from the host with `--audit-id sandbox_run_*`. Do not run this through `sandbox_exec.py`.
- `scripts/sandbox_finalize.py`: finalize a workflow after valid protocol files exist. In workflow mode, call with `--audit-id` only so it reads existing `output/final.json`; do not use `--status`/`--summary` to create workflow output.
- `scripts/sandbox_cleanup.py`: operator cleanup for stale project-owned Docker sandbox containers. Use only when explicitly diagnosing or cleaning stale sandboxes; run with `--dry-run` first, then `--no-dry-run` if cleanup is intended.

## Read-Only Contract Access

The sandbox workflow is allowed to inspect contract code and helper scripts when validation or finalization errors are not self-explanatory. Treat these files as read-only contracts:

- `scripts/validate_outputs.py`
- `scripts/sandbox_finalize.py`
- `scripts/sandbox_write_file.py`
- `scripts/sandbox_apply_patch.py`
- `scripts/sandbox_exec.py`
- `schemas/` files if present in the sandbox workspace
- mounted references under `references/`

Use `scripts/sandbox_read.py` for bounded source slices and use `--help` for argument shape. Do not edit mounted helper scripts, validators, schemas, references, or host project code. If a contract rejects an output, repair the failing layer: observations/evidence, extraction method, supporting script, serialized output, or proposal artifact.

## Validator-Owned Quality Gate

Page inspection is for deriving observations, evidence, and method choices. It is not the authority for declaring success without persisted outputs, rationale, and validation.

After the agent has written or serialized the required protocol files, use `scripts/validate_outputs.py` and then `scripts/sandbox_finalize.py` to decide whether schema, URL shape, count, evidence references, loaded-chunk citations, rationale, script manifest, run summary, and fixture/reference quality pass. Do not rewrite supporting scripts merely because a count feels broad or narrow. Patch scripts only when there is concrete evidence that the chosen script or extraction method is the failing layer.

Do not combine helper execution and verification into one sandbox shell command. The safe sequence is: run one helper command alone; optionally run one bounded inspection command; load exact evidence chunks in bounded batches when needed; write or serialize outputs plus accountability artifacts; call `scripts/validate_outputs.py`; call `scripts/sandbox_finalize.py`.

## Workflow Protocol Contract

In workflow mode, a valid extraction run must produce these required protocol outputs before validation, finalization, persistence, or database queries:

- `output/page_profile.json`
- `output/extraction_strategy.json`
- `output/extraction_run.json`
- `output/candidates.json`
- `output/validation.json`
- `output/final.json`
- `output/run_summary.md`

Do not treat `output/page_profile.json`, `output/extraction_strategy.json`, or `output/validation.json` as cleanup after persistence fails. Plan the evidence loading, agent extraction, and finalization work so all required protocol files are created from the same loaded evidence set.

Never create placeholder required protocol outputs. If the page evidence shows 20 jobs, do not write `{"jobs": [], "count": 20}` or any other empty placeholder to satisfy the file list. Gather enough evidence or scripted extraction support to justify those jobs and derive the protocol files from the recorded run method.

For listing/search pages, the number of extracted job postings must match the agent's recorded observations of repeated job units, unique detail posting URLs, or the explicit documented filter. If observations say 20 repeated job cards and no filter removes any, `output/candidates.json`, `output/final.json`, and `output/validation.json` must carry 20 jobs; a mismatch is a repair target or `needs_review`, not `success`.

Before writing successful outputs, record observed metadata availability in `output/extraction_run.json` under `expected_output.available_fields` and `expected_output.field_basis`. If the page/card evidence exposes fields such as `company_name`, `location_raw`, `salary_raw`, or `tags` for each in-scope job, mark those fields `required_observed` and cite the evidence signal in `field_basis`. Successful outputs must extract real values for fields marked `required_observed`; placeholders such as `""` or `"unknown"` are validation failures for those fields.

`output/candidates.json` must use the candidate payload shape with top-level `jobs` and `crawl`. Do not wrap candidates as `{ "status": "success", "result": { "jobs": [...] } }`; that envelope is only for `output/final.json`.

Minimal candidate shape:

```json
{
  "source": {
    "source_name": "ITviec",
    "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi"
  },
  "jobs": [
    {
      "title": "AI Engineer",
      "company_name": "Example Company",
      "job_url": "https://itviec.com/it-jobs/ai-engineer-example-company-1234",
      "location_raw": "Ha Noi",
      "location": "Ha Noi",
      "remote_type": "unknown",
      "employment_type": "unknown",
      "posted_at": "unknown",
      "salary_raw": "Sign in to view",
      "description_text": "",
      "description": "",
      "relevance_reason": "Matched AI/ML listing page card.",
      "tags": ["AI", "Python"],
      "field_rationale": {
        "title": {
          "value": "AI Engineer",
          "evidence_refs": ["card_001"],
          "rationale": "The loaded card chunk contains this text in the title region."
        },
        "company_name": {
          "value": "Example Company",
          "evidence_refs": ["card_001"],
          "rationale": "The loaded card chunk places this company text next to the title."
        },
        "job_url": {
          "value": "https://itviec.com/it-jobs/ai-engineer-example-company-1234",
          "evidence_refs": ["card_001"],
          "rationale": "The loaded card chunk exposes the detail URL or slug for this posting."
        }
      },
      "evidence": [
        {
          "ref": "card_001"
        }
      ]
    }
  ],
  "selectors": {
    "job_card": "[data-search--pagination-target=\"jobCard\"]",
    "detail_url": "data-search--job-selection-job-url-value",
    "title": "[data-search--job-selection-target=\"jobTitle\"]"
  },
  "crawl": {
    "candidate_count": 1,
    "relevant_count": 1,
    "page_count": 1,
    "method": "repeated_listing_cards"
  },
  "warnings": []
}
```

The counts and values below are examples only. Use them as shape templates, not fixed values: fill counts, paths, hashes, selectors, and field values from the current page evidence and generated candidate payload.

`output/final.json` must use the sandbox result envelope: top-level `status` plus a `result` object containing the complete candidate payload from `output/candidates.json`. Do not put `count` or `jobs` only at the top level, do not set `result` to a string, and do not use the final envelope for `output/candidates.json`.

Minimal final shape:

```json
{
  "status": "success",
  "output_schema": "job_extraction",
  "summary": "Extracted 1 job from repeated listing cards.",
  "result": {
    "source": {
      "source_name": "ITviec",
      "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi"
    },
    "jobs": [
      {
        "title": "AI Engineer",
        "company_name": "Example Company",
        "job_url": "https://itviec.com/it-jobs/ai-engineer-example-company-1234",
        "location_raw": "Ha Noi",
        "location": "Ha Noi",
        "remote_type": "unknown",
        "employment_type": "unknown",
        "posted_at": "unknown",
        "salary_raw": "Sign in to view",
        "description_text": "",
        "description": "",
        "relevance_reason": "Matched AI/ML listing page card.",
        "tags": ["AI", "Python"],
        "evidence": [
          {
            "source": "page.html",
            "selector": "[data-search--pagination-target=\"jobCard\"]",
            "text": "AI Engineer - Example Company - Ha Noi"
          }
        ]
      }
    ],
    "selectors": {
      "job_card": "[data-search--pagination-target=\"jobCard\"]",
      "detail_url": "data-search--job-selection-job-url-value",
      "title": "[data-search--job-selection-target=\"jobTitle\"]"
    },
    "crawl": {
      "candidate_count": 1,
      "relevant_count": 1,
      "page_count": 1,
      "method": "repeated_listing_cards"
    },
    "warnings": []
  },
  "protocol": {
    "valid": true,
    "warnings": [],
    "candidates": {"path": "output/candidates.json"},
    "final": {"path": "output/final.json"}
  }
}
```

Minimal validation shape:

```json
{
  "valid": true,
  "checks": {
    "count_match": true,
    "required_fields_present": true,
    "url_shape_valid": true
  },
  "candidate_count": 1,
  "relevant_count": 1,
  "warnings": []
}
```

In `output/validation.json`, `candidate_count` must equal the current `jobs.length`, `relevant_count` must come from the current relevance/output decision, and `checks` must describe the current run's validation facts. Do not use `status: "valid"` or `status: "success"` as a substitute for `valid: true`.

Accountable extraction invariant: the agent chooses the extraction method and owns the final run record. A successful workflow writes the required protocol files plus accountability artifacts: `output/page_profile.json`, `output/extraction_strategy.json`, `output/extraction_run.json`, `output/candidates.json`, `output/validation.json`, `output/final.json`, and `output/run_summary.md`. When supporting scripts are authored under `scratch/` or `output/`, also write `output/script_manifest.json`. When evidence was chunked, write `evidence/index.json` and `evidence/chunks/*`; every nontrivial extracted field must have `field_rationale` with `evidence_refs` that point to chunks marked `loaded: true`.

## Approved Runtime Packages

- Start diagnostic probes with `scripts/sandbox_start.py --mode diagnostic`.
- Start real extraction with `scripts/sandbox_start.py --mode workflow`.
- Approved imports in the project sandbox image are `bs4`, `lxml`, `parsel`, `typer`, `rich`, and `loguru`.
- Verify imports before depending on them, for example `python - <<'PY'\nimport bs4, lxml, parsel\nprint("ok")\nPY`.
- Do not run `pip install`, `apt-get`, or other package installers inside a sandbox run. If an approved package is missing, return a blocker that the sandbox image must be rebuilt.

## Inputs

- `variables.json`: task metadata, source URL, target schema, and caller hints.
- Mounted page files such as `page.html` or `page_2.html`.
- Optional helper scripts under `scripts/`.
- References under `references/`.
