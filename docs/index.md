# Job Scraper Docs

This folder is the human-facing interface for the project. Use it for stable explanations, architecture notes, and implementation references that should be readable without going through `.contexts/`.

## Reading Order

1. [Architecture](01-architecture.md)
2. [ADK Job Listing Scout](02-adk-job-listing-scout.md)
3. [Public Export Workflow](03-public-export.md)

## Related Planning

- [Plans index](../plans/index.md)
- [Active agentic scraper implementation plan](../plans/active/01-agentic-scraper-implementation.md)

## Agent Context

`.contexts/` remains the agent-operational context store: active task state, handoff, decisions, references, and lineage. Human docs should link to agent context when useful, but should not require humans to inspect `.contexts/` directly.
