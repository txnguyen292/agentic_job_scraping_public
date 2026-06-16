---
id: itviec-listing-v1
site: itviec
page_type: listing
status: validated
version: v1
validated_at: 2026-05-21
derived_from_audit: sandbox_run_age18_gold_20260521_104255
layout:
  card_selector: ".job-card"
  required_markers:
    - "data-search--pagination-target=\"jobCard\""
    - "data-search--job-selection-job-url-value"
    - "data-search--job-selection-target=\"jobTitle\""
  url_shape: "/it-jobs/<slug>-NNNN"
scripts:
  probe: "scripts/probe_layout.py"
  extractor: "scripts/extractor.py"
reuse_when:
  - "probe reports status=match and can_reuse=true"
  - "card_count is the expected listing count"
  - "sample cards expose title, company, location, salary, and skill-tag evidence"
patch_when:
  - "card markers still exist but one field selector fails"
  - "URL canonicalization changes while detail slugs remain visible"
new_reference_when:
  - "repeated ITviec listing cards are gone"
  - "job data moved to a materially different structure"
---

# ITviec Listing V1 Reusable Extractor

Use this reference after classifying a target URL or mounted page as an ITviec listing page. Load it from `job-listing-scout` before sandbox handoff when the source URL contains `itviec.com` or page evidence contains ITviec listing markers.

Run the probe first:

```bash
python references/job-listing-scout/itviec-listing-v1/scripts/probe_layout.py --html page.html
```

If the probe returns `status: "match"` and `can_reuse: true`, run the extractor:

```bash
python references/job-listing-scout/itviec-listing-v1/scripts/extractor.py --html page.html --output-dir output
```

The extractor writes the full sandbox-page-analyst protocol output set and copies the reusable script into `output/reused_extractor.py` so validation can hash and audit the exact code used for the run.

If validation fails because a selector drifted, patch the copied run artifact under `output/reused_extractor.py` or write a run-scoped replacement under `output/`, then record the reuse/patch decision in `output/script_manifest.json` and the extraction run record.
