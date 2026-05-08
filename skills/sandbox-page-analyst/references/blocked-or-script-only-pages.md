# Blocked Or Script-Only Pages

Use this when mounted HTML contains a block page, captcha, empty app shell, or no extractable job data.

1. Look for HTTP block text, captcha markers, bot-detection text, empty root nodes, or missing job-like links.
2. Check whether structured data or hydration payloads still contain usable jobs.
3. If no jobs can be extracted, return `status: "needs_review"` or `status: "error"`.
4. Set `crawl.blocked` to true when appropriate.
5. Include short evidence only, never the full block page.

Validation hints:

- A blocked page should not produce fake job records.
- The blocker text should be compact.
- Recommend whether the main agent should try a rendered fetch or a different source URL.
