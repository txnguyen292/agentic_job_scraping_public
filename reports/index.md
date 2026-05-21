# Job Scraper Report Artifacts

This folder stores generated or exported report artifacts from scraper experiments.

Planning documents and report templates live under `plans/`, not here.

## Current Artifacts

- `adk-eval-dashboard.html` can be generated from ADK eval history with:
  `uv run python scripts/adk_eval_dashboard.py --input src/.adk/eval_history --output reports/adk-eval-dashboard.html`

## Conventions

- Keep raw run artifacts under `data/`.
- Keep report plans, templates, and human workflow notes under `plans/`.
- Use this folder only for physical report outputs that should be preserved separately from raw run data.
