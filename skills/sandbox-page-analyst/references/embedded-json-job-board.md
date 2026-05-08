# Embedded JSON Job Board

Use this when job data appears inside inline scripts, JSON blobs, or API state embedded in the page.

1. Search scripts for words such as `jobs`, `jobPostings`, `position`, `title`, `company`, and `location`.
2. Extract candidate JSON carefully with a script instead of asking the model to read giant blobs.
3. Normalize URLs, title text, company names, locations, salary, tags, and compact descriptions.
4. Record where the data came from in evidence, such as `script[type=application/json]` or a variable name.

Validation hints:

- Prefer structured JSON fields over rendered text when both exist.
- Do not return the full embedded blob.
- Add a warning if the blob looks truncated, escaped, or partially parsed.
