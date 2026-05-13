# ITviec Listing Repair

Use this reference when a sandbox repair involves an ITviec listing page, especially count mismatches, URL-shape errors, selected-job-only output, or broad-link overcollection.

## Load With

- Load this reference from `sandbox-extraction-debugger` for repair-specific decisions.
- Load `references/itviec-listing-page.md` from `sandbox-page-analyst` when you need extraction details, selector hints, URL normalization rules, or validation expectations.

## Repair Cues

- A finalize error like `ITviec listing evidence expects N jobs but candidates.jobs has M` means the repeated-card evidence and emitted jobs disagree.
- If observations show `.job-card`, `[data-search--pagination-target="jobCard"]`, or `data-search--job-selection-*` markers, the output should emit one job per repeated listing card unless the run record documents a deliberate filter.
- If the output emits one selected job when page evidence shows many listing cards, repair selected-job-only evidence loading, agent reasoning, or serialization.
- If helper evidence discovery starts from global `/it-jobs/` URL matches, broad anchors, category links, or title-keyword filtering, patch it to use the repeated card loop as the primary source and global matches only as supporting URL evidence.
- If `job_selected` appears in the URL or page, treat it as a selected-detail hint only. It must not collapse a listing page to one job.
- If candidates contain `/companies/`, category/search pages, sign-in links, or non-detail URLs, repair URL normalization in the output or serialization helper.
- If `candidates.json` and `final.json` disagree, repair the serialization helper so both files are regenerated from the same jobs payload.
- If required fields are `null`, inspect the loaded card evidence or script output and revise the field/default; do not mark `validation.json` as valid while required fields are invalid.

## Repair Steps

1. Inspect the latest validator/finalizer error and record the expected count, actual count, and offending URL/field shape.
2. Inspect `evidence/index.json`, loaded card chunks, current protocol outputs, and any helper that chunked or serialized the output.
3. Inspect a compact page/card preview only as needed to confirm the repeated card selector and URL/title/company fields.
4. Repair the failing layer: load missing card evidence, revise the jobs/run record, or patch the helper so it iterates card units first, derives canonical detail URLs ending in `-NNNN`, and regenerates protocol outputs from one jobs payload.
5. Rerun the focused helper/probe only if a helper changed; otherwise rerun validation.
6. Validate/finalize again. If the same invariant fails, repair a different evidence/output/helper decision; do not repeat the same broad-link heuristic.

## ITviec Failure Shape

```text
error source: sandbox_finalize.py via validate_outputs.py
failed invariant: expected N repeated listing units, candidates.jobs has M
involved files: evidence/index.json, evidence/chunks/*, output/write_outputs.py or another helper when present, output/candidates.json, output/final.json, page artifact
script logic to inspect: helper loop that creates card evidence chunks or serializes jobs, when such a helper exists
focused test/probe: assert evidence/output emits one record per repeated card and candidates/final share one jobs payload
allowed fix: load missing evidence, revise output/run record, or patch the helper to iterate repeated cards and regenerate candidates/final
disallowed fix: edit scripts/validate_outputs.py or hand-write final.json with fake counts
```

## Expected Evidence/Serialization Shape

```text
cards = select repeated ITviec job-card units
for card in cards:
    job_url = canonical detail URL from card data attribute, detail anchor, or converted sign_in job slug
    title/company/location/skills = nearby card text and attributes
    append one job
write page_profile.json, extraction_strategy.json, extraction_run.json, candidates.json, validation.json, final.json, and run_summary.md from that same jobs list and run record
```

## Blocker Threshold

Do not report a blocker after the first ITviec count mismatch while page evidence and sandbox commands remain available. Make at least one distinct evidence/output/helper repair unless the required repeated-card evidence is absent or a runtime guardrail stops execution.
