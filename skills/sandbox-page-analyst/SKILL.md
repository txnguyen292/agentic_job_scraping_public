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

For workflow mode, after loading the workflow reference and any site-specific reference, update session context with the required output contract before starting the workflow sandbox or writing/running `output/extractor.py`:

- `required_outputs`: `output/page_profile.json`, `output/extraction_strategy.json`, `output/candidates.json`, `output/validation.json`, and `output/final.json`.
- `workflow_contract.producer`: `output/extractor.py`.
- `workflow_contract.success_gate`: all required outputs exist, validate, and finalization succeeds before persistence or final response.
- `workflow_contract.repair_rule`: missing or invalid required outputs are repaired by changing the producer, not by hand-writing derived protocol JSON.

## Page Classification

In workflow mode, classify the mounted page before writing extractor code. Use the source URL, `variables.json`, `inputs.json`, page title, repeated markers, and small HTML probes to choose the closest page class.

Known classes today:

- `itviec-listing`: source URL contains `itviec.com`, or page evidence includes ITviec markers such as `.job-card`, `[data-search--pagination-target="jobCard"]`, `[data-search--job-selection-job-url-value]`, or `[data-search--job-selection-target="jobTitle"]`. For this class, load `references/itviec-listing-page.md` from the `sandbox-page-analyst` skill before writing extractor code.

For ITviec, do not write `output/page_profile.json`, `output/extraction_strategy.json`, `output/extractor.py`, or any final payload from only `fetch_page_to_workspace` signals or the `job_selected` URL. First run a bounded sandbox probe with `sandbox_exec.py --cmd` to count repeated cards/detail URLs, then make the extractor loop over that repeated unit. A listing page with around 20 repeated cards should not become a one-job selected-detail extraction.

If no specific site reference matches, continue with generic recurring-pattern extraction: identify repeated job containers or repeated detail URLs, encode those patterns in `output/extractor.py`, and document the inferred page class and evidence in `output/page_profile.json` and `output/extraction_strategy.json`.

## Script Catalog

This section is the compact script catalog. Do not load a separate catalog or script manual file. Use each script's `--help` output when exact arguments are needed. Use script source loading only when debugging the script itself.

- `scripts/sandbox_start.py`: start or reconnect to a no-network Docker sandbox. Use workflow mode with `--page-artifact` for real page extraction. Its host audit paths are not Docker shell paths; `sandbox_exec.py` starts in `/workspace`.
- `scripts/sandbox_exec.py`: run bounded bash inspection commands inside the active sandbox with current working directory `/workspace`. Pass commands with `--cmd "<shell command>"`; do not use pass-through args after `--`. Use for reading page evidence with `ls`, `find .`, `sed -n '1,80p' page.html`, checking parser imports, and running `python output/extractor.py`. Do not run host-control scripts such as `scripts/sandbox_start.py`, `scripts/sandbox_read.py`, `scripts/sandbox_write_file.py`, `scripts/validate_outputs.py`, or `scripts/sandbox_finalize.py` through this tool. Do not `cd` into host temp paths.
- `scripts/sandbox_write_file.py`: write files into the sandbox workspace using workspace-relative paths only. Use `output/extractor.py`, not `/workspace/output/extractor.py`. In workflow mode, use this helper for initial extractor creation, then let `python output/extractor.py` create required protocol JSON files. For repair after validation/finalization errors, load `sandbox-extraction-debugger` and follow its patch-first repair protocol; use this helper only as the audited fallback when no patch executor exists. Do not patch required protocol JSON files directly unless returning a disclosed `needs_review` blocker outside the normal success path.
- `scripts/sandbox_apply_patch.py`: apply targeted repair edits to existing sandbox workspace files. Use exact replacement with `--path --old --new` for small edits, or `--patch` for unified diffs or Codex `*** Begin Patch` patches. Prefer this over full-file writes when repairing an existing `output/extractor.py` or generated sandbox artifact.
- `scripts/sandbox_read.py`: read a bounded preview from a persisted sandbox file when the agent needs a slice. Use `--max-chars`, not `--max-bytes`.
- `scripts/sandbox_litellm_call.py`: run a generic host-mediated LiteLLM call using `--messages-json` and optional `--response-format-json`. The Docker sandbox remains no-network; use this when another model call helps analyze bounded evidence, design typed request/response shapes, or debug generated code. Persist the response with `--output-path` when it should become an audited sandbox artifact.
- `scripts/sandbox_progress.py`: write compact per-run progress state. Do not store raw HTML or long terminal output.
- `scripts/validate_outputs.py`: validate protocol files from the host with `--audit-id sandbox_run_*`. Do not run this through `sandbox_exec.py`.
- `scripts/sandbox_finalize.py`: finalize a workflow after valid protocol files exist. In workflow mode, call with `--audit-id` only so it reads existing `output/final.json`; do not use `--status`/`--summary` to create workflow output.
- `scripts/sandbox_cleanup.py`: operator cleanup for stale project-owned Docker sandbox containers. Use only when explicitly diagnosing or cleaning stale sandboxes; run with `--dry-run` first, then `--no-dry-run` if cleanup is intended.

## Validator-Owned Quality Gate

Page inspection is for deriving extraction code. It is not the authority for deciding whether the final output is good enough.

After `python output/extractor.py` writes the required protocol files, use `scripts/validate_outputs.py` and then `scripts/sandbox_finalize.py` to decide whether schema, URL shape, count, and fixture/reference quality pass. Do not rewrite `output/extractor.py` merely because a count feels broad or narrow. Rewrite or patch only when there is concrete evidence: a validator/finalizer error, an explicit site reference expectation, or directly inspected malformed records.

## Workflow Protocol Contract

In workflow mode, a valid extraction run must produce these required protocol outputs before validation, finalization, persistence, or database queries:

- `output/page_profile.json`
- `output/extraction_strategy.json`
- `output/candidates.json`
- `output/validation.json`
- `output/final.json`

Do not treat `output/page_profile.json`, `output/extraction_strategy.json`, or `output/validation.json` as cleanup after persistence fails. Plan the extractor and finalization work so all required protocol files are created from the same evidence-backed extraction pass.

Never create placeholder required protocol outputs. If the page evidence shows 20 jobs, do not write `{"jobs": [], "count": 20}` or any other empty placeholder to satisfy the file list. Repair the extractor or derive the protocol files from real extractor output.

`output/candidates.json` must use the candidate payload shape with top-level `jobs` and `crawl`. Do not wrap candidates as `{ "status": "success", "result": { "jobs": [...] } }`; that envelope is only for `output/final.json`.

Minimal candidate shape:

```json
{
  "source": {"source_name": "ITviec", "source_url": "https://..."},
  "jobs": [{"title": "...", "job_url": "https://itviec.com/it-jobs/...-1234"}],
  "selectors": {"job_card": "..."},
  "crawl": {"candidate_count": 20, "relevant_count": 20},
  "warnings": []
}
```

`output/final.json` must use the sandbox result envelope: top-level `status` plus a `result` object containing the complete candidate payload from `output/candidates.json`. Do not put `count` or `jobs` only at the top level, do not set `result` to a string, and do not use the final envelope for `output/candidates.json`.

Minimal final shape:

```json
{
  "status": "success",
  "output_schema": "job_extraction",
  "summary": "Extracted 20 jobs.",
  "result": {"source": {}, "jobs": [], "selectors": {}, "crawl": {}, "warnings": []},
  "protocol": {"valid": true, "warnings": []}
}
```

Extractor invariant: `output/extractor.py` writes all five required protocol files in one extraction pass: `output/page_profile.json`, `output/extraction_strategy.json`, `output/candidates.json`, `output/validation.json`, and `output/final.json`. The extractor source must contain those exact literal workspace-relative path strings. Do not write only `OUT / "page_profile.json"` or other composed filename-only paths; use or include literal strings such as `Path("output/page_profile.json").write_text(...)` so the runtime contract gate can verify the producer. Extractor stdout is only a compact summary with status, counts, and paths.

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
