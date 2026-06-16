# AGE-26 Eval Unit Test Plan

**Linear:** https://linear.app/agentic-job-scraping/issue/AGE-26/add-unit-tests-for-continuation-prompt-eval-scoring

**Related:** AGE-18 continuation prompt comparison eval.

## Goal

Add unit tests for the continuation-prompt eval scorer before wiring it into ADK eval traces. The tests should make it clear that a good run does not need exact tool trajectory matching, but it should reach the same final outputs efficiently and with high-quality responses.

## Implementation Status

Implemented in:

- `src/job_scraper/continuation_eval_scoring.py`
- `src/job_scraper/continuation_eval_trace_models.py`
- `scripts/score_continuation_eval.py`
- `tests/test_continuation_eval_scoring.py`
- `tests/test_score_continuation_eval.py`
- `tests/fixtures/continuation_eval_scoring_cases.py`
- `tests/fixtures/continuation_eval_adk_traces.py`
- `tests/fixtures/continuation_eval_trace_loader.py`
- `tests/fixtures/continuation_eval_adk_traces.json`

Verification: `.venv/bin/python -m pytest -q` passed with `371 passed, 1 warning`.

The script entry point can score a named JSON-backed fixture:

```bash
.venv/bin/python scripts/score_continuation_eval.py --fixture bad_premature_finalization_run
```

## Ported Artifacts

- `plans/active/05-age18-gold-tool-trajectory.md`: manual gold ITviec fixture run used as the trajectory template.
- `skills/job-listing-scout/references/itviec-listing-v1/`: validated reusable ITviec reference package derived from the gold run.

## Metric Shape

Keep two top-level metrics:

```text
trajectory_score
response_quality_score
```

Use deterministic tests for the trajectory scorer, with LLM judgment kept behind a narrow semantic-label interface where needed.

```text
trajectory_score =
  0.45 * milestone_completion
+ 0.30 * ordering_score
+ 0.25 * efficiency_score

efficiency_score =
  0.70 * operation_efficiency
+ 0.30 * semantic_directness

operation_efficiency = min(1.0, gold_effective_ops / actual_effective_ops)
```

## Unit Test Coverage

- Full-score calibration:
  - define named optimal, good, neutral, and bad ADK DB-mirrored `actual_invocation` fixtures before tuning isolated helpers
  - extract those fixtures through the existing dashboard parser, then normalize the resulting events into semantic scoring events before computing scores
  - write down exact expected component scores and final `trajectory_score` for each run
  - require the scorer tests to reproduce those values exactly through `pytest.approx`

- Milestone completion:
  - complete run earns full milestone credit
  - missing required milestones lower the score
  - duplicate milestones do not inflate the score
  - optional milestones do not block completion

- Ordering:
  - ordered milestones earn full ordering credit
  - out-of-order critical pairs lower the score
  - harmless interleaving does not fail the run
  - finalization before validation and promotion before finalization are penalized

- Efficiency:
  - exact gold effective operation count earns full operation efficiency
  - extra operations lower efficiency without failing the run
  - shorter runs only score well if required milestones still complete

- Semantic directness:
  - `productive` and `repair` steps should not count as detours
  - `neutral` steps should be allowed but not rewarded
  - `detour` and `harmful` steps lower directness
  - repeated no-op operations after no new evidence are detours

- Non-exact trajectory tolerance:
  - a run can score high with different command strings when milestones, order, and quality match the gold run
  - a run that reaches outputs through broad unnecessary exploration scores lower than a direct run

- Response quality:
  - deterministic shell around LLM-judged response quality is covered
  - tests separate scorer plumbing from the LLM rubric itself

## First Implementation Slice

1. Define ADK DB-mirrored calibration fixtures for optimal, good, neutral, and bad runs.
2. Extract those fixtures through `scripts/utils.py`, then normalize the dashboard events into semantic events before scoring.
3. Implement pure scoring functions over normalized semantic events.
4. Add one fixture derived from the AGE-18 gold ITviec run shape.
5. Add broader adapters for real ADK traces only after the calibration and pure scorer tests are stable.

## Implementation Plan

Detailed execution plan: [AGE-26 normalized scoring implementation](07-age26-normalized-scoring-implementation.md).

Concrete ADK session-shaped test fixtures: [AGE-26 ADK DB-mirrored trace fixtures](08-age26-adk-db-trace-fixtures.md).
