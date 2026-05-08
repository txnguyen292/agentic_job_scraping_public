# ITviec Listing Page

Use this reference when the source URL is an ITviec search or listing page such as `/it-jobs/<query>/<city>`.

## Detail URL Rule

ITviec job postings use detail URLs shaped like:

```text
https://itviec.com/it-jobs/<job-slug>-NNNN
```

The final path segment must end with a numeric posting id. Navigation, category, marketing, company, blog, and search URLs are not job postings.

Reject examples:

- `/it-jobs/ai-engineer?click_source=Navigation+menu`
- `/it-jobs/ha-noi`
- `/companies/<company>`
- `/companies/<company>?lab_feature=preview_jd_page`
- `/sign_in?job=<job-slug>-NNNN&job_index=0&view_salary_source=search_page`
- `/blog/...`

Accept examples:

- `/it-jobs/ai-developer-engineer-consultant-python-llm-nlp-switch-supply-pty-ltd-2549`
- `/it-jobs/chuyen-vien-an-toan-thong-tin-up-to-35m-net-thai-son-soft-2042`

## Extraction Workflow

1. Inspect repeated listing-card containers before broad URL scans. For ITviec listing pages, the primary extraction loop must be one emitted job per repeated card-like unit, not one emitted job per global link match.
2. Count likely card units first using selectors such as `[data-search--pagination-target="jobCard"]`, `.job-card`, or a parent element containing `data-search--job-selection-*` markers. Record the selector, observed count, and expected emitted count in session context before writing extractor code.
3. Only after identifying the repeated card unit, inspect embedded scripts and repeated anchors as supporting evidence.
4. Search the whole HTML, not only `href` attributes, for paths matching `/it-jobs/.+-[0-9]{4}`. ITviec can repeat posting URLs in card markup, scripts, or data attributes. These global matches are fallback/supporting evidence; do not drive the main loop from the global match list when repeated card units exist.
5. For each repeated job card, derive the canonical detail URL using this priority order:
   - First: values in `data-search--job-selection-job-url-value` when they are shaped like `/it-jobs/<job-slug>-NNNN`.
   - Second: anchors or whole-document matches whose path is already `/it-jobs/<job-slug>-NNNN`.
   - Third: `/sign_in?job=<job-slug>-NNNN...` anchors. Convert the `job` query value into `/it-jobs/<job-slug>-NNNN`.
   - Never use `/companies/<company>?lab_feature=preview_jd_page` as `job_url`; it is a company preview route, not the posting URL.
6. Collect anchors whose `href` path matches `/it-jobs/.+-[0-9]{4}` when available, then merge them with the whole-document matches and converted sign-in job slugs for URL fallback only.
7. Normalize relative links against `https://itviec.com`.
8. Deduplicate by normalized detail URL only after mapping candidates back to the repeated card loop. Do not deduplicate by company preview URL.
9. For each emitted job, map the URL back to its repeated card or anchor neighborhood before emitting a job. Use nearby `[data-search--job-selection-target="jobTitle"]`, visible card text, company preview anchor text, salary/action text, location, remote mode, and skill tags to populate fields.
10. Do not treat a URL-only extraction as valid. If `title`, `company_name`, `team`, `remote_type`, or `description` would be `null`, inspect the card neighborhood or derive a conservative non-null value from visible text before marking validation successful. As a last-resort fallback for `title`, humanize the detail URL slug; never leave `title` null.
11. Use JSON strings for unknown optional text fields, such as `""` or `"unknown"`, not JSON `null`.
12. Treat `job_selected` query parameters as a selected-detail hint only. Do not let one selected job replace the full listing extraction if multiple posting URLs are present.
13. If the listing HTML contains many posting URLs but only one job can be confidently structured, return `needs_review` with a blocker instead of `success`.
14. If only one selected detail page is present and no listing cards can be found, return `needs_review` unless the task explicitly asked for one selected job.

## Anti-Patterns To Reject

- Do not start from all `/it-jobs/` anchors on the page; that over-collects navigation, category, pagination, and related links.
- Do not filter broad global links down by title keywords and call the result complete; that can collapse a 20-card listing to one selected job.
- Do not accept `candidate_count: 1` when repeated ITviec listing-card markers or many unique posting ids are present.
- Do not finalize after a count mismatch. Patch `output/extractor.py` so it loops over the repeated card units and regenerates `output/candidates.json` and `output/final.json`.

## Stable Listing Signals

Treat these as positive evidence of repeated job posts:

- `.job-card`
- `[data-search--pagination-target="jobCard"]`
- `[data-search--job-selection-job-slug-value]`
- `[data-search--job-selection-job-url-value]`
- `[data-search--job-selection-target="jobTitle"]`

Do not discard a candidate because `job_selected` appears in the page, pagination links, card neighborhood, or query string. Strip query parameters for canonicalization when useful, but never use `job_selected` as an exclusion filter.

## Validation Checks

- Every `job_url` must be a detail posting URL ending in `-NNNN`.
- Do not emit category/search/navigation URLs as jobs.
- Do not canonicalize `/companies/<company>?lab_feature=preview_jd_page` into `job_url`; use it only as company evidence.
- If a card has `/sign_in?job=<job-slug>-NNNN...`, the extractor must convert it to `https://itviec.com/it-jobs/<job-slug>-NNNN`.
- `title` must be a non-empty string for every emitted job.
- `validation.json` must not say `valid: true` while any emitted job has `title: null`, empty `job_url`, non-detail `job_url`, or other required schema fields set to JSON `null`.
- If the page advertises many results but extraction finds only one job, include a warning and inspect for hidden JSON or repeated card containers before finalization.
- If the page contains `.job-card` or `data-search--job-selection` markers but the extractor emits zero jobs, repair the extractor instead of reporting "no stable job-card evidence".
- `scripts/validate_outputs.py` rejects a `success` result when an ITviec listing HTML contains many unique posting URLs but only one job is extracted.
- A finalize error shaped like "ITviec listing evidence expects N jobs but candidates.jobs has M" is a producer-logic error in `output/extractor.py`. Repair the card loop; do not summarize a blocker until a distinct extractor repair has been attempted or a runtime guardrail stops execution.
- If page profiling records 20 repeated ITviec listing cards and the extractor emits 20 records, do not call 20 "too broad" by judgment alone. Validate URL shape, required fields, and expected fixture/reference content with `scripts/validate_outputs.py` or `scripts/sandbox_finalize.py`; repair only from a concrete validator/finalizer error or directly inspected malformed records.
