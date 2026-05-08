# Static HTML Job Board

Use this when job cards are visible directly in HTML.

1. Find repeated blocks containing job-like links, titles, company names, locations, or salary text.
2. Prefer stable semantic containers such as `article`, list items, cards, or repeated `div` structures.
3. Extract absolute `job_url` values by resolving relative links against `variables.json.source_url`.
4. Keep evidence short: one card-level text snippet per job is enough.
5. Write selectors in `output/extraction_strategy.json` if a repeatable selector is clear.

Validation hints:

- `discovered_count` should match visible card count for the current page.
- Every persisted candidate needs `title` and `job_url`.
- If company or location is missing from cards, leave the field empty and add a warning.
