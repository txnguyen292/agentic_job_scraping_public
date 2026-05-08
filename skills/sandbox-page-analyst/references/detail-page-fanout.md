# Detail Page Fanout

Use this when listing cards are thin and separate mounted detail pages are provided.

1. Extract listing candidates from the index page first.
2. Match detail page artifacts to listing URLs by slug, URL, or title.
3. Merge detail descriptions into candidates only when the match is clear.
4. Keep descriptions compact and plain text.
5. Deduplicate candidates by canonical `job_url`.

Validation hints:

- Add warnings for unmatched detail pages.
- Do not fetch detail pages from the sandbox.
- If no detail pages are mounted, return thin listing records rather than inventing descriptions.
