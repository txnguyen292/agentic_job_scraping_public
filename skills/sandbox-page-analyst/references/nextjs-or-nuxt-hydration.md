# Next.js Or Nuxt Hydration

Use this when the page has framework hydration data such as `__NEXT_DATA__`, Nuxt payloads, or serialized route state.

1. Detect hydration scripts before trying brittle CSS extraction.
2. Parse the hydration JSON with Python and inspect keys programmatically.
3. Search recursively for dictionaries with job-like fields: `title`, `slug`, `url`, `company`, `location`, `salary`, `tags`.
4. Reconstruct job URLs from route metadata and `variables.json.source_url`.
5. Use visible text as a cross-check when possible.

Validation hints:

- Hydration data can contain jobs for other pages; filter against the current URL/query when possible.
- Do not include full hydration snippets in final output.
- Add warnings if routing reconstruction is uncertain.
