# AGE-26 Normalized Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add test-first continuation-prompt eval scoring over normalized semantic trajectory events, with a small ADK-style trace normalization contract that can later feed the scorer.

**Architecture:** Keep scoring pure and deterministic. ADK/dashboard trace events are first converted into compact `NormalizedEvent` records; scoring functions operate only on those records and return a breakdown that explains milestone completion, ordering, operation efficiency, semantic directness, and response-quality wrapper state.

**Tech Stack:** Python 3.13, dataclasses, pytest, existing ADK eval dashboard trace-event shape from `scripts/utils.py`.

---

## Scope

This plan implements AGE-26 as a unit-test-first scoring contract. It does not wire the scorer into ADK eval configuration, does not call an LLM, and does not attempt to score full production trace files end to end.

The first calibration tests should be written as ADK DB-mirrored `actual_invocation` fixtures, not directly as semantic milestones. Those fixtures should mirror the `events.event_data` JSON shape from ADK `session.db` rows under `actual_invocation.intermediate_data.invocation_events`, then flow through `scripts/utils.py` before normalization:

```python
{
    "author": "job_listing_scout",
    "content": {
        "role": "model",
        "parts": [
            {
                "function_call": {
                    "id": "call-1",
                    "name": "sandbox_exec.py",
                    "args": {"cmd": "python output/extractor.py"},
                }
            }
        ],
    },
}
```

The concrete trace fixtures are written down in [AGE-26 ADK DB-mirrored trace fixtures](08-age26-adk-db-trace-fixtures.md). That adapter proves how ADK-style events become `NormalizedEvent` records, while keeping final ADK eval wiring for a later ticket. Do not require literal `.contexts` tool access from the runtime agent. The semantic milestone is `project_context_loaded`, and it can be satisfied by a runtime-injected project context pack, a dedicated context-loading tool, or Codex-style `.contexts/bin/*` access.

Reference-operator runs, including Codex-performed runs, must follow the [Reference Operator Trace Contract](08-age26-adk-db-trace-fixtures.md#reference-operator-trace-contract). Once a reference workflow starts, every action must be appended in order as ADK-shaped `function_call` / `function_response` events. The run is valid for scoring only if the operator uses the runtime-agent tool surface rather than Codex-only shortcuts, and failed, repeated, neutral, detour, and harmful actions remain in the trace.

## Files

- Create: `src/job_scraper/continuation_eval_scoring.py`
  - Owns `NormalizedEvent`, scoring specs, score breakdown dataclasses, pure scoring functions, and the small dashboard-event normalizer.
- Create: `src/job_scraper/continuation_eval_trace_models.py`
  - Owns Pydantic models for ADK `actual_invocation` traces and fixture sets.
- Create: `scripts/score_continuation_eval.py`
  - Owns the CLI that validates JSON-backed ADK traces, extracts dashboard events, normalizes them, and prints score JSON.
- Create: `tests/test_continuation_eval_scoring.py`
  - Owns all AGE-26 unit test assertions.
- Create: `tests/test_score_continuation_eval.py`
  - Owns script-level tests for Pydantic fixture validation and CLI scoring.
- Create: `tests/fixtures/continuation_eval_scoring_cases.py`
  - Owns reusable scoring cases, expected score breakdowns, semantic event shapes, and dashboard-event normalizer cases.
- Create: `tests/fixtures/continuation_eval_adk_traces.py`
  - Owns named accessors for JSON-backed ADK DB-mirrored `actual_invocation` fixtures.
- Create: `tests/fixtures/continuation_eval_trace_loader.py`
  - Owns JSON loading and dashboard extraction for ADK DB-mirrored trace fixtures.
- Create: `tests/fixtures/continuation_eval_adk_traces.json`
  - Owns raw ADK DB-mirrored `actual_invocation` trace artifacts keyed by case name.
- Modify: `plans/active/06-age26-eval-unit-tests.md`
  - Link this implementation plan from the existing AGE-26 summary.
- Modify: `plans/index.md`
  - Link this plan from the active plans index.
- Modify: `.contexts/handoff.md`, `.contexts/lineage/events.jsonl`
  - Record meaningful plan creation through `.contexts/bin/*` tools after edits.

## Scoring Contract

Use these top-level formulas:

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

Use these deterministic semantic directness points unless the tests expose a better boundary:

```python
LABEL_DIRECTNESS_POINTS = {
    "productive": 1.0,
    "repair": 1.0,
    "neutral": 0.5,
    "detour": 0.0,
    "harmful": -1.0,
}
```

This makes neutral work allowed but not fully rewarded, detours unrewarded, and harmful operations worse than detours.

The first implementation slice should calibrate the full formula against named runs with known expected scores before adding more isolated component tests. This keeps the scorer honest: if the weights or directness labels change, the calibration tests should fail loudly and force an intentional update.

## Shared Test Fixtures

Use ADK DB-mirrored `actual_invocation` fixtures for calibration tests, then extract dashboard events and normalize them before scoring. Copy the helpers and target fixtures from [AGE-26 ADK DB-mirrored trace fixtures](08-age26-adk-db-trace-fixtures.md) into `tests/test_continuation_eval_scoring.py` for Task 0. The first test flow should look like:

```python
dashboard_events = dashboard_events_from_actual_invocation(optimal_adk_invocation())
normalized_events = normalize_dashboard_events(dashboard_events)
result = score_trajectory(normalized_events, trajectory_spec())
```

Use compact semantic events only for isolated component tests after calibration:

```python
from job_scraper.continuation_eval_scoring import NormalizedEvent


def event(
    milestone: str | None,
    *,
    label: str = "productive",
    tool_name: str = "tool",
    effective: bool = True,
    evidence: str = "",
) -> NormalizedEvent:
    return NormalizedEvent(
        milestone=milestone,
        label=label,
        tool_name=tool_name,
        effective=effective,
        evidence=evidence or milestone or label,
    )
```

Use this compact AGE-18 milestone list as the gold-run shape:

```python
GOLD_MILESTONES = [
    "project_context_loaded",
    "skill_and_contract_loaded",
    "sandbox_started",
    "bounded_inspection",
    "expected_count_derived",
    "strategy_recorded",
    "accountable_extractor_written",
    "extractor_executed",
    "validation_passed",
    "sandbox_finalized",
    "output_promoted",
    "persisted_rows_verified",
    "crawl_metadata_recorded",
    "final_answer_from_verified_state",
]
```

Critical order pairs:

```python
CRITICAL_ORDER_PAIRS = [
    ("project_context_loaded", "sandbox_started"),
    ("sandbox_started", "bounded_inspection"),
    ("bounded_inspection", "accountable_extractor_written"),
    ("accountable_extractor_written", "extractor_executed"),
    ("extractor_executed", "validation_passed"),
    ("validation_passed", "sandbox_finalized"),
    ("sandbox_finalized", "output_promoted"),
    ("output_promoted", "persisted_rows_verified"),
    ("persisted_rows_verified", "final_answer_from_verified_state"),
]
```

## Calibration Runs With Expected Scores

These are the first full-score test cases. They encode what the project means by optimal, good, neutral, and bad before the scorer is tuned around smaller unit details.

Use `GOLD_MILESTONES`, `CRITICAL_ORDER_PAIRS`, `gold_effective_ops=14`, and the directness point table from `## Scoring Contract`.

| Fixture | Intended Quality | Milestone Completion | Ordering | Operation Efficiency | Semantic Directness | Efficiency Score | Trajectory Score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `optimal_gold_run` | optimal | `1.0` | `1.0` | `1.0` | `1.0` | `1.0` | `1.0` |
| `good_non_exact_run` | good; same milestones, two harmless neutral reads | `1.0` | `1.0` | `0.875` | `0.9375` | `0.89375` | `0.9734375` |
| `neutral_partial_progress_run` | neutral; useful early progress but no final outputs | `0.5` | `0.3333333333` | `1.0` | `0.85` | `0.955` | `0.56375` |
| `bad_no_verified_output_run` | bad; repeated guardrail-rejected writes with no milestones | `0.0` | `0.0` | `0.7` | `0.0` | `0.49` | `0.1225` |
| `bad_premature_finalization_run` | bad; context and sandbox start only, with harmful rejected finalize/promote/final-answer attempts | `0.1428571429` | `0.1111111111` | `1.0` | `0.0` | `0.7` | `0.2726190476` |

Expected-score math:

```text
good_non_exact_run:
  actual_effective_ops = 16
  operation_efficiency = 14 / 16 = 0.875
  semantic_directness = (14 productive + 2 neutral * 0.5) / 16 = 15 / 16 = 0.9375
  efficiency_score = 0.70 * 0.875 + 0.30 * 0.9375 = 0.89375
  trajectory_score = 0.45 + 0.30 + 0.25 * 0.89375 = 0.9734375

neutral_partial_progress_run:
  completed required milestones = 7 / 14 = 0.5
  satisfied order pairs = 3 / 9 = 0.3333333333
  actual_effective_ops = 10
  operation_efficiency = min(1.0, 14 / 10) = 1.0
  semantic_directness = (7 productive + 3 neutral * 0.5) / 10 = 0.85
  efficiency_score = 0.70 * 1.0 + 0.30 * 0.85 = 0.955
  trajectory_score = 0.45 * 0.5 + 0.30 * 0.3333333333 + 0.25 * 0.955 = 0.56375

bad_no_verified_output_run:
  completed required milestones = 0 / 14 = 0.0
  satisfied order pairs = 0 / 9 = 0.0
  actual_effective_ops = 20
  operation_efficiency = 14 / 20 = 0.7
  semantic_directness = clamp(20 harmful * -1 / 20) = 0.0
  efficiency_score = 0.70 * 0.7 + 0.30 * 0.0 = 0.49
  trajectory_score = 0.25 * 0.49 = 0.1225

bad_premature_finalization_run:
  completed required milestones = 2 / 14 = 0.1428571429
  satisfied order pairs = 1 / 9 = 0.1111111111
  actual_effective_ops = 7
  operation_efficiency = min(1.0, 14 / 7) = 1.0
  semantic_directness = clamp((2 productive + 4 harmful * -1 + 1 detour * 0) / 7) = 0.0
  efficiency_score = 0.70 * 1.0 + 0.30 * 0.0 = 0.7
  trajectory_score = 0.45 * 0.1428571429 + 0.30 * 0.1111111111 + 0.25 * 0.7 = 0.2726190476
```

## Task 0: Full-Score Calibration Fixtures

**Files:**
- Create: `tests/test_continuation_eval_scoring.py`
- Create: `tests/fixtures/continuation_eval_scoring_cases.py`
- Create: `tests/fixtures/continuation_eval_adk_traces.py`
- Create: `tests/fixtures/continuation_eval_trace_loader.py`
- Create: `tests/fixtures/continuation_eval_adk_traces.json`
- Create: `src/job_scraper/continuation_eval_scoring.py`

- [x] **Step 1: Write failing calibration tests**

Create `tests/test_continuation_eval_scoring.py` with the ADK DB-mirrored `actual_invocation` fixtures from [AGE-26 ADK DB-mirrored trace fixtures](08-age26-adk-db-trace-fixtures.md), plus post-extraction dashboard-event helpers for focused normalizer assertions:

```python
from __future__ import annotations

import pytest

from job_scraper.continuation_eval_scoring import (
    NormalizedEvent,
    TrajectoryScoringSpec,
    normalize_dashboard_events,
    score_trajectory,
)


def call(name: str, args: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "kind": "function_call",
        "title": name,
        "payload": {"name": name, "args": args or {}},
    }


def response(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "function_response",
        "title": name,
        "payload": {"name": name, "response": payload},
    }


def final_response(text: str) -> dict[str, object]:
    return {
        "kind": "final_response",
        "title": "Final response",
        "payload": {"role": "model", "parts": [{"text": text}]},
    }


GOLD_MILESTONES = [
    "project_context_loaded",
    "skill_and_contract_loaded",
    "sandbox_started",
    "bounded_inspection",
    "expected_count_derived",
    "strategy_recorded",
    "accountable_extractor_written",
    "extractor_executed",
    "validation_passed",
    "sandbox_finalized",
    "output_promoted",
    "persisted_rows_verified",
    "crawl_metadata_recorded",
    "final_answer_from_verified_state",
]


CRITICAL_ORDER_PAIRS = [
    ("project_context_loaded", "sandbox_started"),
    ("sandbox_started", "bounded_inspection"),
    ("bounded_inspection", "accountable_extractor_written"),
    ("accountable_extractor_written", "extractor_executed"),
    ("extractor_executed", "validation_passed"),
    ("validation_passed", "sandbox_finalized"),
    ("sandbox_finalized", "output_promoted"),
    ("output_promoted", "persisted_rows_verified"),
    ("persisted_rows_verified", "final_answer_from_verified_state"),
]


def trajectory_spec() -> TrajectoryScoringSpec:
    return TrajectoryScoringSpec(
        required_milestones=GOLD_MILESTONES,
        critical_order_pairs=CRITICAL_ORDER_PAIRS,
        gold_effective_ops=len(GOLD_MILESTONES),
    )


def assert_breakdown_for_adk_events(
    events: list[dict[str, object]],
    *,
    milestone_completion: float,
    ordering_score: float,
    operation_efficiency: float,
    semantic_directness: float,
    efficiency_score: float,
    trajectory_score: float,
) -> None:
    normalized = normalize_dashboard_events(events)
    result = score_trajectory(normalized, trajectory_spec())

    assert result.milestone_completion == pytest.approx(milestone_completion)
    assert result.ordering_score == pytest.approx(ordering_score)
    assert result.operation_efficiency == pytest.approx(operation_efficiency)
    assert result.semantic_directness == pytest.approx(semantic_directness)
    assert result.efficiency_score == pytest.approx(efficiency_score)
    assert result.trajectory_score == pytest.approx(trajectory_score)


def optimal_gold_run() -> list[dict[str, object]]:
    return [
        call("load_project_context", {"context_pack_id": "age26-runtime-context"}),
        response("load_project_context", {"status": "success", "active_task": "AGE-26"}),
        call("load_skill_resource", {"skill": "sandbox-page-analyst", "resource": "workflow-mode"}),
        response("load_skill_resource", {"status": "success"}),
        call("sandbox_start.py", {"mode": "workflow", "html_artifact": "tests/fixtures/itviec_ai_engineer_ha_noi.html"}),
        response("sandbox_start.py", {"status": "running", "audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        call("sandbox_exec.py", {"cmd": "python - <<'PY'\n# count .job-card units and field markers\nPY"}),
        response("sandbox_exec.py", {"status": "success", "stdout": "job_card_class 20\njob_url_attr 20\njob_title_target 20"}),
        call("update_extraction_context", {"expected_output": {"expected_job_count": 20}}),
        response("update_extraction_context", {"status": "success"}),
        call("update_extraction_context", {"producer_output_plan": {"strategy": "one job per .job-card"}}),
        response("update_extraction_context", {"status": "success"}),
        call("sandbox_write_file.py", {"path": "output/extractor.py"}),
        response("sandbox_write_file.py", {"status": "success", "path": "output/extractor.py"}),
        call("sandbox_exec.py", {"cmd": "python output/extractor.py"}),
        response("sandbox_exec.py", {"status": "success", "jobs": 20}),
        call("validate_outputs.py", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("validate_outputs.py", {"valid": True, "warnings": []}),
        call("sandbox_finalize.py", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("sandbox_finalize.py", {"status": "finalized"}),
        call("promote_sandbox_extraction", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("promote_sandbox_extraction", {"status": "success", "written_count": 20}),
        call("query_jobs", {"source_name": "ITviec AI Engineer Hanoi", "limit": 25}),
        response("query_jobs", {"status": "success", "count": 20}),
        call("record_crawl_run", {"run_id": "sandbox_run_age18_gold_20260521_104255", "written_count": 20}),
        response("record_crawl_run", {"status": "success"}),
        final_response("Verified 20 persisted ITviec jobs from finalized sandbox output."),
    ]


def good_non_exact_run() -> list[dict[str, object]]:
    return [
        call("load_project_context", {"context_pack_id": "age26-runtime-context"}),
        response("load_project_context", {"status": "success"}),
        call("load_skill_resource", {"skill": "sandbox-page-analyst"}),
        response("load_skill_resource", {"status": "success"}),
        call("sandbox_start.py", {"mode": "workflow"}),
        response("sandbox_start.py", {"status": "running"}),
        call("sandbox_read.py", {"path": "inputs.json"}),
        response("sandbox_read.py", {"status": "success"}),
        call("sandbox_exec.py", {"cmd": "rg -c 'data-search--job-selection-target=\"jobTitle\"' page.html"}),
        response("sandbox_exec.py", {"status": "success", "stdout": "20"}),
        call("update_extraction_context", {"expected_output": {"expected_job_count": 20}}),
        response("update_extraction_context", {"status": "success"}),
        call("update_extraction_context", {"producer_output_plan": {"strategy": "reuse package if probe matches"}}),
        response("update_extraction_context", {"status": "success"}),
        call("sandbox_read.py", {"path": "progress.json"}),
        response("sandbox_read.py", {"status": "success"}),
        call("sandbox_write_file.py", {"path": "output/extractor.py"}),
        response("sandbox_write_file.py", {"status": "success"}),
        call("sandbox_exec.py", {"cmd": "python output/extractor.py"}),
        response("sandbox_exec.py", {"status": "success", "jobs": 20}),
        call("validate_outputs.py", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("validate_outputs.py", {"valid": True}),
        call("sandbox_finalize.py", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("sandbox_finalize.py", {"status": "finalized"}),
        call("promote_sandbox_extraction", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("promote_sandbox_extraction", {"status": "success", "written_count": 20}),
        call("query_jobs", {"limit": 25}),
        response("query_jobs", {"status": "success", "count": 20}),
        call("record_crawl_run", {"written_count": 20}),
        response("record_crawl_run", {"status": "success"}),
        final_response("Verified 20 persisted jobs."),
    ]


def neutral_partial_progress_run() -> list[dict[str, object]]:
    return [
        call("load_project_context", {"context_pack_id": "age26-runtime-context"}),
        response("load_project_context", {"status": "success"}),
        call("load_skill_resource", {"skill": "sandbox-page-analyst"}),
        response("load_skill_resource", {"status": "success"}),
        call("sandbox_start.py", {"mode": "workflow"}),
        response("sandbox_start.py", {"status": "running"}),
        call("sandbox_read.py", {"path": "plan.md"}),
        response("sandbox_read.py", {"status": "success"}),
        call("sandbox_exec.py", {"cmd": "python - <<'PY'\n# inspect cards\nPY"}),
        response("sandbox_exec.py", {"status": "success", "stdout": "20 cards"}),
        call("update_extraction_context", {"expected_output": {"expected_job_count": 20}}),
        response("update_extraction_context", {"status": "success"}),
        call("sandbox_read.py", {"path": "inputs.json"}),
        response("sandbox_read.py", {"status": "success"}),
        call("update_extraction_context", {"producer_output_plan": {"strategy": "write extractor"}}),
        response("update_extraction_context", {"status": "success"}),
        call("sandbox_read.py", {"path": "progress.json"}),
        response("sandbox_read.py", {"status": "success"}),
        call("sandbox_write_file.py", {"path": "output/extractor.py"}),
        response("sandbox_write_file.py", {"status": "success"}),
    ]


def bad_no_verified_output_run() -> list[dict[str, object]]:
    return [
        item
        for index in range(20)
        for item in (
            call("sandbox_write_file.py", {"path": f"output/random_{index}.json"}),
            response("sandbox_write_file.py", {"status": "rejected", "guardrail": "unplanned_output_write"}),
        )
    ]


def bad_premature_finalization_run() -> list[dict[str, object]]:
    return [
        call("load_project_context", {"context_pack_id": "age26-runtime-context"}),
        response("load_project_context", {"status": "success"}),
        call("sandbox_finalize.py", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("sandbox_finalize.py", {"status": "rejected", "guardrail": "validation_not_passed"}),
        call("promote_sandbox_extraction", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
        response("promote_sandbox_extraction", {"status": "rejected", "guardrail": "sandbox_not_finalized"}),
        call("sandbox_start.py", {"mode": "workflow"}),
        response("sandbox_start.py", {"status": "running"}),
        final_response("I extracted and saved the jobs."),
        call("sandbox_exec.py", {"cmd": "cat progress.json"}),
        response("sandbox_exec.py", {"status": "success", "stdout": "{}"}),
        call("final_answer", {"text": "Reported unverified results"}),
    ]


def test_calibration_optimal_gold_run_scores_one() -> None:
    assert_breakdown_for_adk_events(
        optimal_gold_run(),
        milestone_completion=1.0,
        ordering_score=1.0,
        operation_efficiency=1.0,
        semantic_directness=1.0,
        efficiency_score=1.0,
        trajectory_score=1.0,
    )


def test_calibration_good_non_exact_run_scores_high_but_not_perfect() -> None:
    assert_breakdown_for_adk_events(
        good_non_exact_run(),
        milestone_completion=1.0,
        ordering_score=1.0,
        operation_efficiency=0.875,
        semantic_directness=0.9375,
        efficiency_score=0.89375,
        trajectory_score=0.9734375,
    )


def test_calibration_neutral_partial_progress_run_scores_middle() -> None:
    assert_breakdown_for_adk_events(
        neutral_partial_progress_run(),
        milestone_completion=0.5,
        ordering_score=1 / 3,
        operation_efficiency=1.0,
        semantic_directness=0.85,
        efficiency_score=0.955,
        trajectory_score=0.56375,
    )


def test_calibration_bad_no_verified_output_run_scores_low() -> None:
    assert_breakdown_for_adk_events(
        bad_no_verified_output_run(),
        milestone_completion=0.0,
        ordering_score=0.0,
        operation_efficiency=0.7,
        semantic_directness=0.0,
        efficiency_score=0.49,
        trajectory_score=0.1225,
    )


def test_calibration_bad_premature_finalization_run_scores_low_despite_some_milestones() -> None:
    assert_breakdown_for_adk_events(
        bad_premature_finalization_run(),
        milestone_completion=2 / 14,
        ordering_score=1 / 9,
        operation_efficiency=1.0,
        semantic_directness=0.0,
        efficiency_score=0.7,
        trajectory_score=0.2726190476,
    )
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: import failure because `job_scraper.continuation_eval_scoring` does not exist yet.

- [x] **Step 3: Implement only enough scorer surface for calibration tests**

Create `src/job_scraper/continuation_eval_scoring.py` with `NormalizedEvent`, `TrajectoryScoringSpec`, `TrajectoryScoreBreakdown`, `normalize_dashboard_events`, and the pure scoring functions from Tasks 1-3 and Task 6 below.

Do not add trace normalization or response-quality code in this task.

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: the five calibration tests pass with the exact expected breakdown values.

Normalizer behavior needed for Task 0:

- Pair each `function_call` with its matching adjacent `function_response` when possible.
- Emit one `NormalizedEvent` per meaningful tool interaction, not one event per raw call and response.
- Map `load_project_context` success to `project_context_loaded`.
- Map `load_skill_resource` success to `skill_and_contract_loaded`.
- Map `sandbox_start.py` success/running to `sandbox_started`.
- Map bounded `sandbox_exec.py` inspection commands that count or inspect cards to `bounded_inspection`.
- Map `update_extraction_context` with `expected_output.expected_job_count` to `expected_count_derived`.
- Map `update_extraction_context` with `producer_output_plan` to `strategy_recorded`.
- Map successful `sandbox_write_file.py` for `output/extractor.py` to `accountable_extractor_written`.
- Map `sandbox_exec.py` running `python output/extractor.py` to `extractor_executed`.
- Map `validate_outputs.py` response with `valid: true` to `validation_passed`.
- Map successful `sandbox_finalize.py` to `sandbox_finalized`; rejected premature finalize responses are `harmful`.
- Map successful `promote_sandbox_extraction` to `output_promoted`; rejected premature promote responses are `harmful`.
- Map successful `query_jobs` with `count > 0` to `persisted_rows_verified`.
- Map successful `record_crawl_run` to `crawl_metadata_recorded`.
- Map `final_response` after persisted-row verification to `final_answer_from_verified_state`; final responses before verification are `harmful`.
- Map successful `sandbox_read.py` and other harmless read-only checks to `neutral`.
- Map rejected unplanned writes, repeated no-op operations, and fabricated/unverified final-answer tools to `harmful` or `detour` according to their response/evidence.

## Task 1: Milestone Completion

**Files:**
- Create: `tests/test_continuation_eval_scoring.py`
- Create: `src/job_scraper/continuation_eval_scoring.py`

- [x] **Step 1: Write failing milestone completion tests**

Add tests for:

```python
def test_milestone_completion_full_credit_for_all_required_milestones() -> None:
    events = [event("project_context_loaded"), event("sandbox_started"), event("validation_passed")]

    assert score_milestone_completion(events, ["project_context_loaded", "sandbox_started", "validation_passed"]) == 1.0


def test_milestone_completion_partial_credit_for_missing_required_milestones() -> None:
    events = [event("project_context_loaded"), event("validation_passed")]

    assert score_milestone_completion(events, ["project_context_loaded", "sandbox_started", "validation_passed"]) == pytest.approx(2 / 3)


def test_milestone_completion_ignores_duplicate_milestones() -> None:
    events = [event("project_context_loaded"), event("project_context_loaded"), event("validation_passed")]

    assert score_milestone_completion(events, ["project_context_loaded", "validation_passed"]) == 1.0


def test_milestone_completion_does_not_require_optional_milestones() -> None:
    events = [event("project_context_loaded"), event("sandbox_started"), event("validation_passed")]

    assert score_milestone_completion(
        events,
        ["project_context_loaded", "sandbox_started", "validation_passed"],
        optional_milestones=["reusable_package_probe_matched"],
    ) == 1.0
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: import failure because `job_scraper.continuation_eval_scoring` does not exist yet.

- [x] **Step 3: Implement minimal milestone scoring**

Create `src/job_scraper/continuation_eval_scoring.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


SemanticLabel = Literal["productive", "repair", "neutral", "detour", "harmful"]


@dataclass(frozen=True)
class NormalizedEvent:
    milestone: str | None
    label: SemanticLabel
    tool_name: str | None = None
    effective: bool = True
    evidence: str = ""


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def completed_milestones(events: Sequence[NormalizedEvent]) -> set[str]:
    return {event.milestone for event in events if event.milestone}


def score_milestone_completion(
    events: Sequence[NormalizedEvent],
    required_milestones: Sequence[str],
    *,
    optional_milestones: Sequence[str] = (),
) -> float:
    del optional_milestones
    required = set(required_milestones)
    if not required:
        return 1.0
    return len(required & completed_milestones(events)) / len(required)
```

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: milestone completion tests pass.

## Task 2: Ordering And Operation Efficiency

**Files:**
- Modify: `tests/test_continuation_eval_scoring.py`
- Modify: `src/job_scraper/continuation_eval_scoring.py`

- [x] **Step 1: Write failing tests**

Add tests for:

```python
def test_ordering_score_full_credit_for_gold_order() -> None:
    events = [event("sandbox_started"), event("bounded_inspection"), event("validation_passed")]

    assert score_ordering(events, [("sandbox_started", "bounded_inspection"), ("bounded_inspection", "validation_passed")]) == 1.0


def test_ordering_score_penalizes_critical_order_inversions() -> None:
    events = [event("sandbox_finalized"), event("validation_passed"), event("output_promoted")]

    assert score_ordering(events, [("validation_passed", "sandbox_finalized"), ("sandbox_finalized", "output_promoted")]) == pytest.approx(0.5)


def test_ordering_score_allows_harmless_interleaving() -> None:
    events = [
        event("sandbox_started"),
        event(None, label="neutral", effective=False, evidence="read progress.json"),
        event("bounded_inspection"),
        event(None, label="neutral", effective=False, evidence="read inputs.json"),
        event("validation_passed"),
    ]

    assert score_ordering(events, [("sandbox_started", "bounded_inspection"), ("bounded_inspection", "validation_passed")]) == 1.0


def test_ordering_score_penalizes_missing_pair_members() -> None:
    events = [event("sandbox_started"), event("validation_passed")]

    assert score_ordering(events, [("sandbox_started", "bounded_inspection"), ("bounded_inspection", "validation_passed")]) == 0.0


def test_operation_efficiency_full_credit_at_gold_effective_operation_count() -> None:
    assert score_operation_efficiency(gold_effective_ops=8, actual_effective_ops=8) == 1.0


def test_operation_efficiency_penalizes_extra_effective_operations() -> None:
    assert score_operation_efficiency(gold_effective_ops=8, actual_effective_ops=16) == 0.5


def test_operation_efficiency_does_not_reward_shorter_than_gold_runs() -> None:
    assert score_operation_efficiency(gold_effective_ops=8, actual_effective_ops=4) == 1.0
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: missing `score_ordering` and `score_operation_efficiency`.

- [x] **Step 3: Implement ordering and operation efficiency**

Add:

```python
def _first_milestone_positions(events: Sequence[NormalizedEvent]) -> dict[str, int]:
    positions: dict[str, int] = {}
    for index, item in enumerate(events):
        if item.milestone and item.milestone not in positions:
            positions[item.milestone] = index
    return positions


def score_ordering(events: Sequence[NormalizedEvent], critical_pairs: Sequence[tuple[str, str]]) -> float:
    if not critical_pairs:
        return 1.0
    positions = _first_milestone_positions(events)
    satisfied = sum(
        1
        for before, after in critical_pairs
        if before in positions and after in positions and positions[before] < positions[after]
    )
    return satisfied / len(critical_pairs)


def score_operation_efficiency(*, gold_effective_ops: int, actual_effective_ops: int) -> float:
    if gold_effective_ops <= 0:
        return 1.0
    if actual_effective_ops <= 0:
        return 0.0
    return _clamp_score(gold_effective_ops / actual_effective_ops)
```

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: tests pass.

## Task 3: Semantic Directness And Composite Score

**Files:**
- Modify: `tests/test_continuation_eval_scoring.py`
- Modify: `src/job_scraper/continuation_eval_scoring.py`

- [x] **Step 1: Write failing tests**

Add tests for:

```python
def test_semantic_directness_counts_productive_and_repair_as_direct() -> None:
    events = [event("bounded_inspection", label="productive"), event("targeted_repair", label="repair")]

    assert score_semantic_directness(events) == 1.0


def test_semantic_directness_tolerates_neutral_steps_without_rewarding_them() -> None:
    productive = score_semantic_directness([event("bounded_inspection", label="productive")])
    with_neutral = score_semantic_directness(
        [event("bounded_inspection", label="productive"), event(None, label="neutral", effective=True)]
    )

    assert 0.0 < with_neutral < productive


def test_semantic_directness_penalizes_detour_and_harmful_steps() -> None:
    with_detour = score_semantic_directness(
        [event("bounded_inspection", label="productive"), event(None, label="detour", effective=True)]
    )
    with_harmful = score_semantic_directness(
        [event("bounded_inspection", label="productive"), event(None, label="harmful", effective=True)]
    )

    assert with_harmful < with_detour < 1.0


def test_inefficient_successful_run_scores_lower_without_failing() -> None:
    direct = [event(name) for name in GOLD_MILESTONES]
    inefficient = direct + [
        event(None, label="neutral", evidence="extra status read"),
        event(None, label="detour", evidence="repeated no-op inspection"),
    ]

    direct_score = score_trajectory(direct, trajectory_spec())
    inefficient_score = score_trajectory(inefficient, trajectory_spec())

    assert inefficient_score.trajectory_score < direct_score.trajectory_score
    assert inefficient_score.trajectory_score > 0.7
    assert inefficient_score.milestone_completion == 1.0


def test_non_exact_trajectory_can_score_high_when_milestones_order_and_quality_are_good() -> None:
    events = [
        event("project_context_loaded", tool_name="context_overview"),
        event("skill_and_contract_loaded", tool_name="load_resource"),
        event("sandbox_started", tool_name="sandbox_start.py"),
        event("bounded_inspection", tool_name="sandbox_exec.py", evidence="used rg instead of exact gold command"),
        event("expected_count_derived", tool_name="update_extraction_context"),
        event("strategy_recorded", tool_name="update_extraction_context"),
        event("accountable_extractor_written", tool_name="sandbox_write_file.py"),
        event("extractor_executed", tool_name="sandbox_exec.py"),
        event("validation_passed", tool_name="validate_outputs.py"),
        event("sandbox_finalized", tool_name="sandbox_finalize.py"),
        event("output_promoted", tool_name="promote_sandbox_extraction"),
        event("persisted_rows_verified", tool_name="query_jobs"),
        event("crawl_metadata_recorded", tool_name="record_crawl_run"),
        event("final_answer_from_verified_state", tool_name=None),
    ]

    score = score_trajectory(events, trajectory_spec())

    assert score.trajectory_score >= 0.95
    assert score.milestone_completion == 1.0
    assert score.ordering_score == 1.0
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: missing `TrajectoryScoringSpec`, `score_semantic_directness`, and `score_trajectory`.

- [x] **Step 3: Implement composite scoring**

Add:

```python
@dataclass(frozen=True)
class TrajectoryScoringSpec:
    required_milestones: Sequence[str]
    critical_order_pairs: Sequence[tuple[str, str]]
    gold_effective_ops: int
    optional_milestones: Sequence[str] = ()


@dataclass(frozen=True)
class TrajectoryScoreBreakdown:
    trajectory_score: float
    milestone_completion: float
    ordering_score: float
    efficiency_score: float
    operation_efficiency: float
    semantic_directness: float
    actual_effective_ops: int


LABEL_DIRECTNESS_POINTS: dict[str, float] = {
    "productive": 1.0,
    "repair": 1.0,
    "neutral": 0.5,
    "detour": 0.0,
    "harmful": -1.0,
}


def effective_events(events: Sequence[NormalizedEvent]) -> list[NormalizedEvent]:
    return [item for item in events if item.effective]


def score_semantic_directness(events: Sequence[NormalizedEvent]) -> float:
    effective = effective_events(events)
    if not effective:
        return 1.0
    raw = sum(LABEL_DIRECTNESS_POINTS[item.label] for item in effective) / len(effective)
    return _clamp_score(raw)


def score_trajectory(events: Sequence[NormalizedEvent], spec: TrajectoryScoringSpec) -> TrajectoryScoreBreakdown:
    milestone_completion = score_milestone_completion(
        events,
        spec.required_milestones,
        optional_milestones=spec.optional_milestones,
    )
    ordering_score = score_ordering(events, spec.critical_order_pairs)
    actual_effective_ops = len(effective_events(events))
    operation_efficiency = score_operation_efficiency(
        gold_effective_ops=spec.gold_effective_ops,
        actual_effective_ops=actual_effective_ops,
    )
    semantic_directness = score_semantic_directness(events)
    efficiency_score = _clamp_score(0.70 * operation_efficiency + 0.30 * semantic_directness)
    trajectory_score = _clamp_score(
        0.45 * milestone_completion + 0.30 * ordering_score + 0.25 * efficiency_score
    )
    return TrajectoryScoreBreakdown(
        trajectory_score=trajectory_score,
        milestone_completion=milestone_completion,
        ordering_score=ordering_score,
        efficiency_score=efficiency_score,
        operation_efficiency=operation_efficiency,
        semantic_directness=semantic_directness,
        actual_effective_ops=actual_effective_ops,
    )
```

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: tests pass.

## Task 4: AGE-18 Gold Shape And Reusable Package Behavior

**Files:**
- Modify: `tests/test_continuation_eval_scoring.py`
- Modify: `src/job_scraper/continuation_eval_scoring.py`

- [x] **Step 1: Write failing gold and reuse tests**

Add tests that encode the AGE-18 gold run from `plans/active/05-age18-gold-tool-trajectory.md`:

```python
def age18_gold_itviec_shape() -> list[NormalizedEvent]:
    return [
        event("project_context_loaded", tool_name="context_overview"),
        event("skill_and_contract_loaded", tool_name="sandbox-page-analyst"),
        event("sandbox_started", tool_name="sandbox_start.py"),
        event("bounded_inspection", tool_name="sandbox_exec.py", evidence="counted 20 .job-card units"),
        event("expected_count_derived", tool_name="sandbox_exec.py", evidence="expected 20 jobs"),
        event("strategy_recorded", tool_name="output/extraction_strategy.json"),
        event("accountable_extractor_written", tool_name="sandbox_write_file.py"),
        event("extractor_executed", tool_name="sandbox_exec.py"),
        event("validation_failed_with_concrete_error", label="productive", tool_name="validate_outputs.py"),
        event("targeted_repair", label="repair", tool_name="sandbox_apply_patch.py"),
        event("validation_passed", tool_name="validate_outputs.py"),
        event("sandbox_finalized", tool_name="sandbox_finalize.py"),
        event("output_promoted", tool_name="promote_sandbox_extraction"),
        event("persisted_rows_verified", tool_name="query_jobs"),
        event("crawl_metadata_recorded", tool_name="record_crawl_run"),
        event("final_answer_from_verified_state", tool_name=None),
    ]


def test_gold_itviec_fixture_scores_at_or_near_one() -> None:
    score = score_trajectory(age18_gold_itviec_shape(), age18_spec())

    assert score.trajectory_score >= 0.95
    assert score.milestone_completion == 1.0
    assert score.ordering_score == 1.0


def test_reuse_package_probe_receives_credit_before_new_extractor_authoring() -> None:
    events = [
        event("project_context_loaded"),
        event("skill_and_contract_loaded"),
        event("sandbox_started"),
        event("bounded_inspection"),
        event("reusable_package_probe_matched", tool_name="probe_layout.py"),
        event("reusable_package_extractor_used", tool_name="extractor.py"),
        event("expected_count_derived"),
        event("strategy_recorded"),
        event("accountable_extractor_written", tool_name="reused_extractor.py"),
        event("extractor_executed"),
        event("validation_passed"),
        event("sandbox_finalized"),
        event("output_promoted"),
        event("persisted_rows_verified"),
        event("crawl_metadata_recorded"),
        event("final_answer_from_verified_state"),
    ]

    assert score_trajectory(events, age18_spec()).trajectory_score >= 0.95


def test_new_extractor_despite_matching_reusable_package_scores_lower() -> None:
    reuse = [
        event("project_context_loaded"),
        event("skill_and_contract_loaded"),
        event("sandbox_started"),
        event("bounded_inspection"),
        event("reusable_package_probe_matched", tool_name="probe_layout.py"),
        event("reusable_package_extractor_used", tool_name="extractor.py"),
        event("expected_count_derived"),
        event("strategy_recorded"),
        event("accountable_extractor_written"),
        event("extractor_executed"),
        event("validation_passed"),
        event("sandbox_finalized"),
        event("output_promoted"),
        event("persisted_rows_verified"),
        event("crawl_metadata_recorded"),
        event("final_answer_from_verified_state"),
    ]
    unnecessary_rewrite = [
        *reuse[:5],
        event(None, label="detour", tool_name="sandbox_write_file.py", evidence="wrote new extractor despite matched package"),
        *reuse[6:],
    ]

    assert score_trajectory(unnecessary_rewrite, age18_spec()).trajectory_score < score_trajectory(reuse, age18_spec()).trajectory_score
```

- [x] **Step 2: Run test to verify RED or exposed formula gap**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: tests pass if Task 3 already captures detours; otherwise the reuse detour test exposes the formula gap.

- [x] **Step 3: Adjust only the smallest formula boundary if needed**

If the reuse detour is not visible in the score, ensure detour events are `effective=True` and contribute `0.0` directness points. Do not add reuse-specific hardcoding to `score_trajectory`.

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: tests pass.

## Task 5: Response Quality Wrapper Boundary

**Files:**
- Modify: `tests/test_continuation_eval_scoring.py`
- Modify: `src/job_scraper/continuation_eval_scoring.py`

- [x] **Step 1: Write failing deterministic response-quality tests**

Add:

```python
def test_response_quality_score_uses_deterministic_stub_result() -> None:
    judgment = ResponseQualityJudgment(
        score=0.8,
        verdict="pass",
        rationale="Final answer cites validated row count and persisted source.",
    )

    result = score_response_quality(judgment)

    assert result.score == 0.8
    assert result.verdict == "pass"
    assert result.not_evaluated_reason == ""


def test_response_quality_score_rejects_missing_required_rubric_fields() -> None:
    result = score_response_quality({"score": 0.7, "verdict": "pass"})

    assert result.score is None
    assert result.verdict == "not_evaluated"
    assert "rationale" in result.not_evaluated_reason
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: missing response-quality APIs.

- [x] **Step 3: Implement wrapper without model calls**

Add:

```python
@dataclass(frozen=True)
class ResponseQualityJudgment:
    score: float
    verdict: Literal["pass", "fail"]
    rationale: str


@dataclass(frozen=True)
class ResponseQualityScore:
    score: float | None
    verdict: Literal["pass", "fail", "not_evaluated"]
    rationale: str
    not_evaluated_reason: str = ""


def score_response_quality(judgment: ResponseQualityJudgment | dict[str, object]) -> ResponseQualityScore:
    if isinstance(judgment, ResponseQualityJudgment):
        return ResponseQualityScore(
            score=_clamp_score(judgment.score),
            verdict=judgment.verdict,
            rationale=judgment.rationale,
        )
    missing = [key for key in ("score", "verdict", "rationale") if key not in judgment]
    if missing:
        return ResponseQualityScore(
            score=None,
            verdict="not_evaluated",
            rationale="",
            not_evaluated_reason=f"Missing response quality fields: {', '.join(missing)}",
        )
    verdict = str(judgment["verdict"])
    if verdict not in {"pass", "fail"}:
        return ResponseQualityScore(
            score=None,
            verdict="not_evaluated",
            rationale="",
            not_evaluated_reason=f"Unsupported response quality verdict: {verdict}",
        )
    return ResponseQualityScore(
        score=_clamp_score(float(judgment["score"])),
        verdict=verdict,  # type: ignore[arg-type]
        rationale=str(judgment["rationale"]),
    )
```

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: tests pass.

## Task 6: Small ADK-Style Trace Normalization Contract

**Files:**
- Modify: `tests/test_continuation_eval_scoring.py`
- Modify: `src/job_scraper/continuation_eval_scoring.py`

- [x] **Step 1: Write failing dashboard-event normalization tests**

Add:

```python
def call(name: str, args: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "kind": "function_call",
        "title": name,
        "payload": {"name": name, "args": args or {}},
    }


def response(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "function_response",
        "title": name,
        "payload": {"name": name, "response": payload},
    }


def test_normalize_dashboard_events_maps_tool_calls_to_semantic_milestones() -> None:
    events = normalize_dashboard_events(
        [
            call("load_project_context", {"context_pack_id": "age26-runtime-context"}),
            response("load_project_context", {"status": "success"}),
            call("load_skill_resource", {"skill": "sandbox-page-analyst"}),
            response("load_skill_resource", {"status": "success"}),
            call("sandbox_start.py"),
            response("sandbox_start.py", {"status": "running"}),
            call("sandbox_exec.py", {"cmd": "python - <<'PY'\n# count .job-card units\nPY"}),
            response("sandbox_exec.py", {"status": "success", "stdout": "20 cards"}),
            call("update_extraction_context", {"expected_output": {"expected_job_count": 20}}),
            response("update_extraction_context", {"status": "success"}),
            call("update_extraction_context", {"producer_output_plan": {"strategy": "one job per card"}}),
            response("update_extraction_context", {"status": "success"}),
            call("sandbox_write_file.py", {"path": "output/extractor.py"}),
            response("sandbox_write_file.py", {"status": "success"}),
            call("sandbox_exec.py", {"cmd": "python output/extractor.py"}),
            response("sandbox_exec.py", {"status": "success", "jobs": 20}),
            call("validate_outputs.py"),
            response("validate_outputs.py", {"valid": True}),
            call("sandbox_finalize.py"),
            response("sandbox_finalize.py", {"status": "finalized"}),
            call("promote_sandbox_extraction"),
            response("promote_sandbox_extraction", {"status": "success", "written_count": 20}),
            call("query_jobs"),
            response("query_jobs", {"status": "success", "count": 20}),
            call("record_crawl_run"),
            response("record_crawl_run", {"status": "success"}),
            final_response("Verified 20 persisted jobs."),
        ]
    )

    assert [item.milestone for item in events] == [
        "project_context_loaded",
        "skill_and_contract_loaded",
        "sandbox_started",
        "bounded_inspection",
        "expected_count_derived",
        "strategy_recorded",
        "accountable_extractor_written",
        "extractor_executed",
        "validation_passed",
        "sandbox_finalized",
        "output_promoted",
        "persisted_rows_verified",
        "crawl_metadata_recorded",
        "final_answer_from_verified_state",
    ]


def test_normalize_dashboard_events_uses_tool_response_to_distinguish_validation_result() -> None:
    events = normalize_dashboard_events(
        [
            call("validate_outputs.py"),
            response("validate_outputs.py", {"valid": False, "errors": [{"path": "jobs[0].tags"}]}),
            call("validate_outputs.py"),
            response("validate_outputs.py", {"valid": True, "warnings": []}),
        ]
    )

    assert [item.milestone for item in events] == [
        "validation_failed_with_concrete_error",
        "validation_passed",
    ]
    assert events[0].label == "productive"


def test_normalize_dashboard_events_marks_repeated_noop_as_detour() -> None:
    events = normalize_dashboard_events(
        [
            call("sandbox_exec.py", {"cmd": "cat progress.json"}),
            response("sandbox_exec.py", {"status": "success", "stdout": "{}"}),
            call("sandbox_exec.py", {"cmd": "cat progress.json"}),
            response("sandbox_exec.py", {"status": "success", "stdout": "{}"}),
        ]
    )

    assert [item.label for item in events] == ["neutral", "detour"]


def test_normalize_dashboard_events_marks_rejected_premature_actions_harmful() -> None:
    events = normalize_dashboard_events(
        [
            call("sandbox_finalize.py", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
            response("sandbox_finalize.py", {"status": "rejected", "guardrail": "validation_not_passed"}),
            call("promote_sandbox_extraction", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
            response("promote_sandbox_extraction", {"status": "rejected", "guardrail": "sandbox_not_finalized"}),
            final_response("I extracted and saved the jobs."),
        ]
    )

    assert [item.label for item in events] == ["harmful", "harmful", "harmful"]
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: missing `normalize_dashboard_events`.

- [x] **Step 3: Implement small normalizer**

Add:

```python
def _event_tool_name(event: dict[str, object]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict) and payload.get("name"):
        return str(payload["name"])
    return str(event.get("title") or "")


def _event_args(event: dict[str, object]) -> dict[str, object]:
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("args"), dict):
        return payload["args"]  # type: ignore[return-value]
    return {}


def _event_response(event: dict[str, object]) -> dict[str, object]:
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        return payload["response"]  # type: ignore[return-value]
    return {}


def _command_text(args: dict[str, object]) -> str:
    for key in ("cmd", "command"):
        if args.get(key):
            return str(args[key])
    return ""


def _response_status(response_payload: dict[str, object]) -> str:
    value = response_payload.get("status")
    return str(value) if value is not None else ""


def _is_rejected(response_payload: dict[str, object]) -> bool:
    return _response_status(response_payload) in {"rejected", "error", "failed"}


def _normalize_tool_interaction(
    call_event: dict[str, object],
    response_event: dict[str, object] | None,
    *,
    persisted_rows_verified: bool,
) -> NormalizedEvent | None:
    tool_name = _event_tool_name(call_event)
    args = _event_args(call_event)
    response_payload = _event_response(response_event or {})
    command = _command_text(args)
    rejected = _is_rejected(response_payload)

    if tool_name == "load_project_context":
        return NormalizedEvent("project_context_loaded", "productive", tool_name, True, str(args))
    if tool_name == "load_skill_resource":
        return NormalizedEvent("skill_and_contract_loaded", "productive", tool_name, True, str(args))
    if tool_name == "sandbox_start.py":
        return NormalizedEvent("sandbox_started", "productive", tool_name, True, str(response_payload))
    if tool_name == "sandbox_read.py":
        return NormalizedEvent(None, "neutral", tool_name, True, str(args))
    if tool_name == "sandbox_exec.py" and "output/extractor.py" in command:
        return NormalizedEvent("extractor_executed", "productive", tool_name, True, command)
    if tool_name == "sandbox_exec.py" and any(token in command for token in (".job-card", "jobTitle", "inspect cards", "count")):
        return NormalizedEvent("bounded_inspection", "productive", tool_name, True, command)
    if tool_name == "sandbox_exec.py":
        return NormalizedEvent(None, "neutral", tool_name, True, command)
    if tool_name == "update_extraction_context" and isinstance(args.get("expected_output"), dict):
        return NormalizedEvent("expected_count_derived", "productive", tool_name, True, str(args["expected_output"]))
    if tool_name == "update_extraction_context" and isinstance(args.get("producer_output_plan"), dict):
        return NormalizedEvent("strategy_recorded", "productive", tool_name, True, str(args["producer_output_plan"]))
    if tool_name == "sandbox_write_file.py":
        if rejected:
            return NormalizedEvent(None, "harmful", tool_name, True, str(response_payload))
        if str(args.get("path") or "") == "output/extractor.py":
            return NormalizedEvent("accountable_extractor_written", "productive", tool_name, True, str(args))
    if tool_name == "validate_outputs.py":
        milestone = "validation_passed" if response_payload.get("valid") is True else "validation_failed_with_concrete_error"
        return NormalizedEvent(milestone, "productive", tool_name, True, str(response_payload))
    if tool_name == "sandbox_finalize.py":
        label = "harmful" if rejected else "productive"
        return NormalizedEvent("sandbox_finalized", label, tool_name, True, str(response_payload))
    if tool_name == "promote_sandbox_extraction":
        label = "harmful" if rejected else "productive"
        return NormalizedEvent("output_promoted", label, tool_name, True, str(response_payload))
    if tool_name == "query_jobs":
        count = response_payload.get("count")
        if isinstance(count, int) and count > 0:
            return NormalizedEvent("persisted_rows_verified", "productive", tool_name, True, str(response_payload))
        return NormalizedEvent(None, "neutral", tool_name, True, str(response_payload))
    if tool_name == "record_crawl_run":
        return NormalizedEvent("crawl_metadata_recorded", "productive", tool_name, True, str(response_payload))
    if tool_name == "final_answer":
        return NormalizedEvent("final_answer_from_verified_state", "harmful", tool_name, True, str(args))
    return None


def normalize_dashboard_events(events: Sequence[dict[str, object]]) -> list[NormalizedEvent]:
    normalized: list[NormalizedEvent] = []
    seen_neutral_evidence: set[str] = set()
    persisted_rows_verified = False
    index = 0
    while index < len(events):
        item = events[index]
        if item.get("kind") == "final_response":
            label = "productive" if persisted_rows_verified else "harmful"
            normalized.append(
                NormalizedEvent(
                    "final_answer_from_verified_state",
                    label,
                    None,
                    True,
                    str(item.get("payload") or {}),
                )
            )
            index += 1
            continue
        tool_name = _event_tool_name(item)
        next_item = events[index + 1] if index + 1 < len(events) else None
        has_response = (
            next_item is not None
            and next_item.get("kind") == "function_response"
            and _event_tool_name(next_item) == tool_name
        )
        if item.get("kind") == "function_call":
            event = _normalize_tool_interaction(
                item,
                next_item if has_response else None,
                persisted_rows_verified=persisted_rows_verified,
            )
            if event is not None:
                if event.milestone == "persisted_rows_verified" and event.label == "productive":
                    persisted_rows_verified = True
                if event.label == "neutral" and event.evidence in seen_neutral_evidence:
                    event = NormalizedEvent(event.milestone, "detour", event.tool_name, event.effective, event.evidence)
                if event.label == "neutral":
                    seen_neutral_evidence.add(event.evidence)
                normalized.append(event)
        if has_response:
            index += 2
            continue
        index += 1
    return normalized
```

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: tests pass.

## Task 7: Verification And Context Updates

**Files:**
- Modify: `plans/active/06-age26-eval-unit-tests.md`
- Modify: `.contexts/handoff.md`
- Modify: `.contexts/lineage/events.jsonl`

- [x] **Step 1: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_continuation_eval_scoring.py
```

Expected: all continuation eval scoring tests pass.

- [x] **Step 2: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes. Last known baseline before implementation was `338 passed, 1 warning`.

- [x] **Step 3: Update context through repo tools**

Run:

```bash
.contexts/bin/update_handoff \
  --summary "Implemented AGE-26 normalized continuation eval scorer tests and pure scoring helpers." \
  --next-step "Use normalize_dashboard_events as the future adapter boundary before wiring scorer output into ADK eval traces." \
  --touched-file "src/job_scraper/continuation_eval_scoring.py" \
  --touched-file "tests/test_continuation_eval_scoring.py" \
  --verification ".venv/bin/python -m pytest -q"
```

Run:

```bash
.contexts/bin/append_lineage implementation \
  "Implemented AGE-26 normalized continuation eval scoring unit-test contract." \
  --branch codex/age-26 \
  --file src/job_scraper/continuation_eval_scoring.py \
  --file tests/test_continuation_eval_scoring.py \
  --verification ".venv/bin/python -m pytest -q" \
  --link AGE-26
```

Run:

```bash
.contexts/bin/validate_context
```

Expected: `{"valid": true, ...}`.

## Self-Review

- Spec coverage: This plan covers every AGE-26 acceptance criterion through tests: explicit optimal/good/neutral/bad calibration runs with expected scores, milestone completion, ordering, operation efficiency, semantic directness, inefficient success, non-exact high score, response-quality wrapper boundaries, JSON-backed reusable fixture modules, and an AGE-18-derived ITviec shape.
- Scope discipline: The plan intentionally stops before ADK eval config wiring and model-judged response quality. The only adapter is a pure function over dashboard event dictionaries.
- Trace path: Future ADK scoring should use existing `scripts/utils.py` trace extraction output as the adapter input, then pass `NormalizedEvent` records into `score_trajectory`.
- Public action rule: Do not commit, push, open a PR, or post Linear updates unless the user explicitly approves those public actions in the current thread.
