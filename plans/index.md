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
- [AGE-18 gold tool trajectory](active/05-age18-gold-tool-trajectory.md)
- [AGE-26 eval unit test plan](active/06-age26-eval-unit-tests.md)
- [AGE-26 normalized scoring implementation](active/07-age26-normalized-scoring-implementation.md)
- [AGE-26 ADK DB-mirrored trace fixtures](active/08-age26-adk-db-trace-fixtures.md)

## Backlog Plans

- [Reusable extraction packages](backlog/01-reusable-extraction-packages.md)

## Relationship To `.contexts/`

Use `plans/` for human-readable plans and discussion. Use `.contexts/` for agent-operational task state, handoff, lineage, and compact resumption context.

When a meaningful implementation step changes the current direction, update both the active plan and `.contexts/` rather than making humans reverse-engineer the project from agent metadata.
