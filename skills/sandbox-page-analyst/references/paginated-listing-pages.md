# Paginated Listing Pages

Use this when the current page shows pagination, "next" links, infinite-scroll hints, or result counts larger than visible cards.

1. Extract only the current mounted page unless additional pages are already provided.
2. Record pagination signals in `output/page_profile.json`.
3. Add compact warnings about likely additional pages.
4. Do not fetch next pages from the sandbox; the sandbox has no internet access.
5. If multiple page artifacts are mounted, process each artifact and deduplicate by `job_url`.

Validation hints:

- `discovered_count` should describe candidates in mounted artifacts only.
- Mention unresolved pagination in warnings.
- Never invent jobs from result counts.
