# AGE-33 Continuation Observability Dashboard Implementation Plan

> **For agentic workers:** implement this task-by-task. Keep checklist status current as work lands.

**Goal:** build a static, factual observability surface for continuation scoring reports that reconciles three evidence layers:

- ADK eval dashboard: eval history, rubric state, trace-style evidence.
- ADK token/runtime observability: token distribution, cost charts, model buckets, daily token timeline, runtime quantiles, and span/session detail.
- AGE-33 continuation scoring: formulas, weighted terms, milestones, ordering checks, semantic labels, normalized events, and provenance.

**Non-goals:**

- Do not count or preserve the Streamlit job dashboard as part of this observability platform.
- Do not collapse token observability into summary cards. Existing runtime/token charts remain first-class.
- Do not add LLM-authored diagnosis, repair advice, or inferred recommendations.
- Do not move external report tooling into `src` merely to make imports nicer.

---

## Architecture Boundary

This repo uses a `src` layout. Editable install adds `<worktree>/src` to `sys.path`, not the repository root. Installed package imports should look like:

```python
from job_scraper.continuation_eval_scoring import score_trajectory
```

Use this boundary:

- `src/job_scraper/...`
  - Installed package/runtime code.
  - Deterministic scoring and shared data models.
  - Code that runtime modules import.

- `scripts/...`
  - Repo-local external processes: dashboard generators, report renderers, maintenance CLIs.
  - Static HTML templates.
  - Local artifact readers/writers.

Dashboard generators can stay in `scripts/`. They should import package code from `job_scraper...` via editable install or `PYTHONPATH=src`. Tests should not use `importlib.spec_from_file_location()` to load project scripts. For script behavior, use subprocess/CLI tests or move only truly shared package logic into `src`.

---

## File Structure

Port from `origin/main` before building scoring reconciliation:

- `scripts/adk_token_dashboard.py`
- `scripts/adk_token_dashboard.template.html`
- `src/job_scraper/adk_observability.py`
- `tests/test_adk_token_dashboard.py`
- `tests/test_adk_observability_entrypoint.py`
- `release-notes/unreleased/0006-adk-observability-dashboard.md`
- any required `pyproject.toml` script entry point from `origin/main`

Create for AGE-33:

- `scripts/continuation_eval_dashboard.py`
  - External Typer CLI for `--input`, `--output`, optional `--serve`, and optional `--dump-data`.
  - Owns score-report dashboard data shaping unless another package module later needs that API.

- `scripts/continuation_eval_dashboard.template.html`
  - Static ADK-themed template for scoring evidence and correlation workbench.

- `tests/test_continuation_eval_dashboard.py`
  - Black-box CLI/subprocess tests.
  - No `importlib` script loading.

Keep existing:

- `scripts/score_continuation_eval.py`
  - External scoring report CLI.
  - May import package scoring code from `job_scraper...`.

- `scripts/utils.py`
  - Existing ADK eval dashboard helper module.
  - It can remain script-local while it is only used by script dashboards.

---

## Task 0: Port Runtime Token Observability Into AGE-33

**Files:**

- Add/port: `scripts/adk_token_dashboard.py`
- Add/port: `scripts/adk_token_dashboard.template.html`
- Add/port: `src/job_scraper/adk_observability.py`
- Add/port: `tests/test_adk_token_dashboard.py`
- Add/port: `tests/test_adk_observability_entrypoint.py`
- Modify: `pyproject.toml` only if the `job-scraper-adk-dashboard` entry point is still absent.

- [x] Port the ADK token observability files from `origin/main` into AGE-33.
- [x] Preserve the chart surfaces from `scripts/adk_token_dashboard.template.html`:
  - runtime distribution
  - cost by model / cost chart
  - daily token timeline
  - model buckets
  - runtime quantiles
  - span/session detail tables
- [x] Run the token dashboard tests after porting.

Verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_adk_token_dashboard.py \
  tests/test_adk_observability_entrypoint.py
```

Expected: token observability tests pass, and the AGE-33 worktree contains the same chart-capable runtime dashboard layer as `origin/main`.

---

## Task 1: Remove Importlib From The New AGE-33 Test Path

**Files:**

- Modify/create: `tests/test_continuation_eval_dashboard.py`
- Use existing: `scripts/score_continuation_eval.py`

- [x] Add a test helper that generates a score report through the scoring CLI instead of importing the script file.

Example helper:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "continuation_eval_adk_traces.json"


def run_python(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write_score_report(tmp_path: Path, fixture: str = "bad_premature_finalization_run") -> Path:
    result = run_python(
        "scripts/score_continuation_eval.py",
        "--fixture-file",
        str(FIXTURE_PATH),
        "--fixture",
        fixture,
    )
    report_path = tmp_path / "score-report.json"
    report_path.write_text(result.stdout, encoding="utf-8")
    json.loads(result.stdout)
    return report_path
```

- [x] Do not add new `importlib.spec_from_file_location()` usage.
- [x] Keep unrelated importlib cleanup out of AGE-33 unless the file is already touched for this task.

Verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuation_eval_dashboard.py -q
```

Expected initially: fails because `scripts/continuation_eval_dashboard.py` does not exist yet, not because of import path or importlib mechanics.

---

## Task 2: Scoring Dashboard CLI Data Contract

**Files:**

- Create: `scripts/continuation_eval_dashboard.py`
- Modify: `tests/test_continuation_eval_dashboard.py`

- [x] Add a failing CLI test that:
  - generates a score report with `scripts/score_continuation_eval.py`
  - runs `scripts/continuation_eval_dashboard.py --input <report> --output <html> --dump-data <json>`
  - reads the dumped dashboard JSON
  - asserts source paths, score cards, identity, and provenance coverage

Required data fields:

- `report_count`
- `reports[].case`
- `reports[].source_file`
- `reports[].identity`
- `reports[].score_cards`
- `reports[].trajectory`
- `reports[].milestone_lane`
- `reports[].semantic_ledger`
- `reports[].events`
- `reports[].provenance_coverage`
- `runtime_layer`
  - declares that the runtime/token dashboard is a separate first-class layer, not folded into scoring.

- [x] Implement `scripts/continuation_eval_dashboard.py` as an external CLI with internal helpers.
- [x] Import package scoring/model code from `job_scraper...` only when needed.
- [x] Keep `scripts/continuation_eval_dashboard.py` independent of runtime package imports from other scripts unless the dependency is script-local.

Verification:

```bash
PYTHONPATH=src .venv/bin/python scripts/continuation_eval_dashboard.py \
  --input /tmp/age33-score-report.json \
  --output reports/continuation-eval-dashboard.html \
  --dump-data /tmp/age33-dashboard-data.json

PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuation_eval_dashboard.py -q
```

Expected: dumped JSON contains score provenance and runtime-layer metadata; generated HTML exists.

---

## Task 3: Static Scoring Template And Escaping

**Files:**

- Create: `scripts/continuation_eval_dashboard.template.html`
- Modify: `scripts/continuation_eval_dashboard.py`
- Modify: `tests/test_continuation_eval_dashboard.py`

- [x] Add a failing test that injects a value like `</script><span>not markup</span>` into a generated score report, runs the dashboard CLI, and asserts:
  - raw breakout text is not emitted as markup
  - embedded JSON escapes `<` as `\u003c`
  - the HTML includes the expected view labels

Required views:

- Evidence-first scoring overview
- Formula and term breakdown
- Milestone and ordering lane
- Semantic directness ledger
- Normalized event workbench
- Provenance/source-path panel
- Runtime layer handoff panel that links conceptually to token charts

- [x] Render embedded dashboard JSON with safe script escaping.
- [x] Keep UI factual: source paths, values, formulas, event payloads, and chart links only.

Verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuation_eval_dashboard.py::test_dashboard_html_escapes_embedded_data -q
```

Expected: escaping test passes.

---

## Task 4: Runtime Chart Handoff In The Scoring Dashboard

**Scope note:** this task only makes the runtime/token chart relationship explicit inside the scoring dashboard. It is not the full custom ADK observability dashboard. The actual reconciliation work is planned as ADK obs custom enhancements in `docs/superpowers/plans/2026-05-31-age-33-observability-reconciliation-dashboard.md`.

**Files:**

- Modify: `scripts/continuation_eval_dashboard.template.html`
- Use/keep: `scripts/adk_token_dashboard.py`
- Use/keep: `scripts/adk_token_dashboard.template.html`

- [x] Add a Runtime section or handoff panel to the scoring dashboard that makes the chart relationship explicit:
  - token distribution
  - cost chart
  - daily token timeline
  - model buckets
  - quantiles
  - span/session detail

- [x] Do not duplicate the whole token dashboard inside the scoring dashboard unless the data is present.
- [x] Prefer linking/correlating by run/session/provenance metadata so the runtime chart dashboard remains its own first-class view.
- [x] Keep Proposal 10 as the visual reference for this layer:
  - `reports/age33-dashboard-proposals/13-runtime-chart-reconciliation.png`

Verification:

```bash
PYTHONPATH=src .venv/bin/python scripts/continuation_eval_dashboard.py \
  --input /tmp/age33-score-report.json \
  --output reports/continuation-eval-dashboard.html
```

Then inspect the generated dashboard in a browser or screenshot workflow and confirm the scoring dashboard names the runtime/token charts as first-class source views. Do not treat this as completion of the custom ADK observability dashboard.

---

## Task 5: Regression Suite

**Files:**

- Modify tests as needed.

- [x] Run scoring tests.
- [x] Run ADK eval dashboard tests.
- [x] Run token observability dashboard tests after porting from `origin/main`.
- [x] Run new AGE-33 scoring dashboard tests.

Verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_continuation_eval_scoring.py \
  tests/test_score_continuation_eval.py \
  tests/test_adk_eval_dashboard.py \
  tests/test_adk_token_dashboard.py \
  tests/test_adk_observability_entrypoint.py \
  tests/test_continuation_eval_dashboard.py
```

Expected: all listed tests pass.

---

## Task 6: Context And Handoff

**Files:**

- Modify: `.contexts/tasks/T-002.md`
- Modify: `.contexts/handoff.md`
- Append: `.contexts/lineage/events.jsonl`

- [x] Update T-002 with the final implementation state.
- [x] Update handoff with exact next step or completion state.
- [x] Append one lineage event.
- [x] Run context validation.

Verification:

```bash
.contexts/bin/validate_context
```

Expected: valid context.

---

## Implementation Notes

- Run commands from the AGE-33 worktree:

```bash
/Users/tungnguyen/.config/superpowers/worktrees/job_scraping/codex-age-33-add-scoring-report-dashboard-for-continuation-eval
```

- Prefer either:
  - editable install in the worktree venv, or
  - `PYTHONPATH=src` for script/test commands.

- Do not rely on editable install adding the repo root. It adds `src`.
- Do not add new file-based script imports with `importlib`.
- Keep external dashboard/report CLIs in `scripts` unless there is a real package/runtime consumer.
- Keep source-specific dashboards separate, but correlate them through run/session/provenance metadata.
