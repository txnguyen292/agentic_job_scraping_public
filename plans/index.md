# Job Scraper Plans

This folder is the human-facing planning interface for the project.

## Cross-Cutting Plans

- [Reports plan](reports.md)
- [Notes](notes/index.md)

## Active Plans

- [Agentic scraper implementation](active/01-agentic-scraper-implementation.md)
- [Sandbox-agent page workspace scraper](active/02-rlm-page-workspace-scraper.md)
- [Sandbox token reporting](active/03-sandbox-token-reporting.md)
- [Skill-script sandbox terminal](active/04-skill-script-sandbox-terminal.md)

## Backlog Plans

- [Reusable extraction packages](backlog/01-reusable-extraction-packages.md)

## Relationship To `.contexts/`

Use `plans/` for human-readable plans and discussion. Use `.contexts/` for agent-operational task state, handoff, lineage, and compact resumption context.

When a meaningful implementation step changes the current direction, update both the active plan and `.contexts/` rather than making humans reverse-engineer the project from agent metadata.
