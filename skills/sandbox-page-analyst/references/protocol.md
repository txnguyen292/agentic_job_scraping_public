# Sandbox Page Analyst Protocol

All outputs must be written under `output/`. Keep files compact and JSON formatted unless explicitly noted.

Reusable helper scripts live under `.agents/sandbox-page-analyst/scripts/`. Use them only when they help, but keep stdout bounded and never paste raw HTML into the final answer.

Inside the Docker workspace the same helpers are mounted under `scripts/`. The primary workflow is agent-directed inspection: run focused shell commands or small local Python snippets, observe bounded outputs, and use persisted output paths/artifact handles to inspect slices when an output is too large for direct context.

## Required Files

- `output/page_profile.json`: compact page profile, detected layouts, relevant local files, selected references, and warnings.
- `output/extraction_strategy.json`: chosen extraction strategy, local source files, selectors or parsing approach, fallbacks, and warnings.
- `output/extraction_run.json`: run record with observations, chosen method, extraction steps, expected output, validation outcome, and proposal flags.
- `output/candidates.json`: validated job extraction payload candidate with `source`, `jobs`, `selectors`, `crawl`, and `warnings`.
- `output/validation.json`: validation checks, candidate counts, relevant counts, and warnings.
- `output/final.json`: final compact sandbox result object.
- `evidence/index.json`: manifest for exact evidence chunks, including chunk ids, paths, token estimates, and whether the agent loaded them.
- `evidence/chunks/*`: exact raw evidence slices for repeated job-like units.
- `output/script_manifest.json`: required when supporting scripts were authored under `scratch/` or `output/`.
- optional `scratch/<helper>.py` or `output/<helper>.py`: Python helpers for repeated-pattern discovery, evidence chunking, token estimates, parsing/extraction, validation, or serialization.
- `output/run_summary.md`: concise summary of what the agent did and which evidence/helpers were used.
- `output/reference_update_proposal.md` or `output/reference_proposal.md`: human-readable reference proposal for the discovered workflow or layout drift.
- `output/reference_proposal.json`: machine-readable reference proposal metadata.

## Accountable Agent Contract

The agent chooses the extraction method and must persist enough rationale for another run to understand what happened. Scripts may read local files such as `page.html`, find repeated patterns, write exact evidence chunks, estimate token counts, parse/extract job records, validate, or serialize output. When a script is authored under `scratch/` or `output/`, it must be listed in `output/script_manifest.json` with path, purpose, inputs, outputs, hash, workflow/reference version, reuse classification, and validation result.

`output/extraction_run.json` must record at least:

```json
{
  "observations": ["Detected 20 repeated job-card units."],
  "chosen_strategy": "supporting_script_extracts_repeated_cards",
  "extraction_steps": ["Counted cards", "Ran scratch/extract_cards.py", "Validated candidates"],
  "expected_output": {"expected_job_count": 20, "count_basis": "20 repeated card units"},
  "validation": {"valid": true}
}
```

Before writing producer scripts or result files, use `scripts/protocol_contract.py`
to get the compact machine-readable version of this contract and store the
agent's distilled `producer_output_plan` in session context. The plan should
explicitly cover `observations`, `chosen_strategy`, `expected_output`, the
candidate/final envelopes, and script manifest version/reuse fields when scripts
are authored.

When evidence is chunked, the manifest must identify exact chunks:

```json
{
  "chunks": [
    {
      "chunk_id": "card_001",
      "path": "evidence/chunks/card_001.txt",
      "source_path": "page.html",
      "token_estimate": 240,
      "loaded": true
    }
  ]
}
```

The agent must write full protocol data to files:

- `output/candidates.json`: complete candidate payload, including every job justified by page evidence. This file must have top-level `source`, `jobs`, `selectors`, `crawl`, and `warnings`; do not use the final-result envelope here.
- `output/final.json`: final sandbox result whose `result` reuses the complete candidate payload.

Candidate payload shape, with example values only:

```json
{
  "source": {"source_name": "ITviec", "source_url": "https://..."},
  "jobs": [
    {
      "title": "...",
      "company_name": "...",
      "job_url": "https://itviec.com/it-jobs/...-1234",
      "field_rationale": {
        "title": {
          "value": "...",
          "evidence_refs": ["card_001"],
          "rationale": "The loaded card chunk contains this title text."
        },
        "company_name": {
          "value": "...",
          "evidence_refs": ["card_001"],
          "rationale": "The loaded card chunk places this company text near the title."
        },
        "job_url": {
          "value": "https://itviec.com/it-jobs/...-1234",
          "evidence_refs": ["card_001"],
          "rationale": "The loaded card chunk contains the detail URL or slug."
        }
      },
      "evidence": [{"ref": "card_001"}]
    }
  ],
  "selectors": {"job_card": "..."},
  "crawl": {"candidate_count": "<len(jobs)>", "relevant_count": "<current relevant job count>"},
  "warnings": []
}
```

Final result shape, with example values only:

```json
{
  "status": "success",
  "output_schema": "job_extraction",
  "summary": "Extracted <len(jobs)> jobs.",
  "result": {"source": {}, "jobs": [], "selectors": {}, "crawl": {}, "warnings": []},
  "protocol": {"valid": true, "warnings": []}
}
```

Validation summary shape, with example values only:

```json
{
  "valid": true,
  "checks": {
    "count_match": true,
    "required_fields_present": true,
    "url_shape_valid": true
  },
  "candidate_count": "<len(candidates.jobs)>",
  "relevant_count": "<current relevant job count>",
  "warnings": []
}
```

`output/validation.json` uses the boolean field `valid`; `status: "valid"` is not a valid substitute. Populate `checks`, `candidate_count`, and `relevant_count` from the current run rather than copying example counts.

Helper stdout is only a compact run summary, for example:

```json
{"status":"success","candidate_count":"<len(jobs)>","candidates_path":"output/candidates.json","final_path":"output/final.json"}
```

Do not use helper stdout as the source of truth for job records. If stdout is truncated, inspect persisted output files, script manifests, run records, or evidence chunks instead of reconstructing jobs from the preview.

Do not let scripts become unrecorded hidden behavior. Script-produced records are acceptable when the run record, script manifest, evidence/rationale, and validation explain how they were produced.

Treat validator and finalizer errors as repair feedback. If validation reports a missing field, wrong type, count mismatch, malformed JSON, invalid status, missing rationale, missing manifest, missing run summary, or evidence ref that was not loaded, update the observations/evidence, extraction method, supporting script, serialized output, or proposal artifact and rerun validation before finalization. String fields must be strings; use `""` or `"unknown"` when evidence is absent instead of JSON `null`.

Validator and finalizer scripts are readable contracts. If an error message does not give enough information to repair the producer precisely, inspect bounded slices of:

- `scripts/validate_outputs.py`
- `scripts/sandbox_finalize.py`
- related mounted `schemas/` files if present
- related site references under `references/`

Do not edit these contract files. Fix observations/evidence, extracted output, or a generated sandbox helper so the next run produces valid protocol files.

## Final Result

For `job_extraction`, `result` should reuse the validated content from `output/candidates.json`. Include a compact `protocol` object with file paths and hashes.

The final response must not include full HTML, command transcripts, large debug dumps, or raw scratch files.

## Reference Proposal

The reference proposal should explain the reusable extraction workflow that a future sandbox analyst can follow after human approval. Include page layout type, stable signals, evidence chunking steps, validation checks, limitations, and example commands or selectors where useful. Also propose skill/helper changes when the run reveals an improvement.
