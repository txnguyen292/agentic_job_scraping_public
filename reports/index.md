# Job Scraper Report Artifacts

This folder stores generated or exported report artifacts from scraper experiments.

Planning documents and report templates live under `plans/`, not here.

## Current Artifacts

- `adk-eval-dashboard.html` can be generated from ADK eval history with:
  `uv run python -m scripts.adk_eval_dashboard --input src/.adk/eval_history --output reports/adk-eval-dashboard.html`
- `adk-token-dashboard.html` is the ADK Observability companion dashboard for
  local session stores. It shows cached input, non-cached input, output,
  reasoning, session drill-down, and ChatCompletion details:
  `uv run job-scraper-adk-dashboard --output reports/adk-token-dashboard.html`
- `adk-model-pricing.json` is a generated local pricing cache. Refresh it with:
  `uv run python scripts/update_adk_model_pricing.py --output reports/adk-model-pricing.json`

## Conventions

- Keep raw run artifacts under `data/`.
- Keep report plans, templates, and human workflow notes under `plans/`.
- Use this folder only for physical report outputs that should be preserved separately from raw run data.
