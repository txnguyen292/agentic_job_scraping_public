# JSON-LD Job Postings

Use this when the page contains `application/ld+json` scripts with `JobPosting`, `ItemList`, or related schema.org data.

1. Parse all JSON-LD scripts.
2. Handle single objects, arrays, and `@graph` containers.
3. Extract `JobPosting` objects directly when present.
4. For `ItemList`, use listed URLs as candidates and pair them with visible card text if available.
5. Normalize `hiringOrganization`, `jobLocation`, `baseSalary`, `employmentType`, and `datePosted` when present.

Validation hints:

- Compare JSON-LD URL count with visible card count.
- JSON-LD may be stale or incomplete; prefer visible card title/company if it conflicts with JSON-LD.
- Add warnings for missing titles, URLs, or mismatched counts.
