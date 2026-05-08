# ITviec Listing Repair

Use this reference when a sandbox repair involves an ITviec listing page, especially count mismatches, URL-shape errors, selected-job-only output, or broad-link overcollection.

## Load With

- Load this reference from `sandbox-extraction-debugger` for repair-specific decisions.
- Load `references/itviec-listing-page.md` from `sandbox-page-analyst` when you need extraction details, selector hints, URL normalization rules, or validation expectations.

## Repair Cues

- A finalize error like `ITviec listing evidence expects N jobs but candidates.jobs has M` is a producer-logic error in `output/extractor.py`.
- If observations show `.job-card`, `[data-search--pagination-target="jobCard"]`, or `data-search--job-selection-*` markers, the extractor should emit one job per repeated listing card.
- If the extractor emits one selected job when page evidence shows many listing cards, repair selected-job-only logic in `output/extractor.py`.
- If the extractor starts from global `/it-jobs/` URL matches, broad anchors, category links, or title-keyword filtering, patch it to use the repeated card loop as the primary source and global matches only as supporting URL evidence.
- If `job_selected` appears in the URL or page, treat it as a selected-detail hint only. It must not collapse a listing page to one job.
- If candidates contain `/companies/`, category/search pages, sign-in links, or non-detail URLs, patch URL normalization/filtering in the producer.
- If `candidates.json` and `final.json` disagree, patch `output/extractor.py` so both files are regenerated from the same in-memory jobs list.
- If required fields are `null`, patch card text extraction/defaults in `output/extractor.py`; do not mark `validation.json` as valid while required fields are invalid.

## Repair Steps

1. Inspect the latest validator/finalizer error and record the expected count, actual count, and offending URL/field shape.
2. Inspect `output/extractor.py` and identify the loop that emits jobs.
3. Inspect a compact page/card preview only as needed to confirm the repeated card selector and URL/title/company fields.
4. Patch the producer so it iterates card units first, derives canonical detail URLs ending in `-NNNN`, and regenerates all protocol outputs from one in-memory job list.
5. Rerun `python output/extractor.py`.
6. Validate/finalize again. If the same invariant fails, patch a different producer decision; do not repeat the same broad-link heuristic.

## ITviec Failure Shape

```text
error source: sandbox_finalize.py via validate_outputs.py
failed invariant: expected N repeated listing units, candidates.jobs has M
involved files: output/extractor.py, output/candidates.json, output/final.json, page artifact
script logic to inspect: loop in output/extractor.py that emits jobs
focused test/probe: assert extractor emits one record per repeated card and candidates/final share one jobs list
allowed fix: patch output/extractor.py to iterate repeated cards and regenerate candidates/final
disallowed fix: edit scripts/validate_outputs.py or hand-write final.json with fake counts
```

## Expected Producer Shape

```text
cards = select repeated ITviec job-card units
for card in cards:
    job_url = canonical detail URL from card data attribute, detail anchor, or converted sign_in job slug
    title/company/location/skills = nearby card text and attributes
    append one job
write page_profile.json, extraction_strategy.json, candidates.json, validation.json, final.json from that same jobs list
```

## Blocker Threshold

Do not report a blocker after the first ITviec count mismatch while page evidence and sandbox commands remain available. Make at least one distinct producer-logic repair unless the required repeated-card evidence is absent or a runtime guardrail stops execution.
