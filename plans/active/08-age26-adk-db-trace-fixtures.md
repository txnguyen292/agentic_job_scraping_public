# AGE-26 ADK DB-Mirrored Trace Fixtures

**Goal:** Define the first targeted continuation-eval tests as ADK-session-shaped traces before writing scorer implementation code.

## Implemented Fixture Layout

The raw ADK-shaped traces now live as one inspectable JSON artifact:

- `tests/fixtures/continuation_eval_adk_traces.json`

The top-level JSON object is keyed by calibration case name: `optimal_gold_run`, `good_non_exact_run`, `neutral_partial_progress_run`, `bad_no_verified_output_run`, and `bad_premature_finalization_run`.

`tests/fixtures/continuation_eval_trace_loader.py` loads that JSON file and runs `actual_invocation` payloads through the existing dashboard parser. `tests/fixtures/continuation_eval_adk_traces.py` preserves named fixture accessors for tests and scoring cases.

## Observed ADK Shape

The local ADK session database stores events in `events.event_data` JSON. The relevant event body shape is:

```python
{
    "author": "job_listing_scout",
    "content": {
        "role": "model",
        "parts": [
            {
                "function_call": {
                    "id": "call-1",
                    "name": "sandbox_start.py",
                    "args": {"mode": "workflow"},
                }
            }
        ],
    },
}
```

Function responses use the same `content.parts` envelope:

```python
{
    "author": "job_listing_scout",
    "content": {
        "role": "model",
        "parts": [
            {
                "function_response": {
                    "id": "call-1",
                    "name": "sandbox_start.py",
                    "response": {"status": "running", "audit_id": "sandbox_run_age18_gold_20260521_104255"},
                }
            }
        ],
    },
}
```

The existing dashboard parser in `scripts/utils.py` reads this under:

```python
actual_invocation["intermediate_data"]["invocation_events"]
```

So the first AGE-26 tests should build `actual_invocation` fixtures, run them through `extract_invocation_events(...)`, then call `normalize_dashboard_events(...)` and `score_trajectory(...)`.

## Reference Operator Trace Contract

Codex/reference-operator runs may be scored by this pipeline only if they are recorded as an append-only ADK-shaped action ledger from workflow start to workflow end. The trace must not be reconstructed as a curated success narrative after the run.

Once the reference workflow starts, every action must be written in order under:

```python
actual_invocation["intermediate_data"]["invocation_events"]
```

Each action must use the same ADK event shape as the fixtures: a `function_call` event followed by its corresponding `function_response` event. Failed, repeated, no-op, neutral, detour, and harmful actions stay in the trace. The scorer should classify them later; the recorder must not omit them.

Reference runs must use only the tool surface exposed to the runtime agent. A Codex operator run is valid for scoring only when its actions can be represented as runtime-agent tool calls such as:

- `load_project_context`
- `load_skill_resource`
- `sandbox_start.py`
- `sandbox_exec.py`
- `update_extraction_context`
- `sandbox_write_file.py`
- `validate_outputs.py`
- `sandbox_finalize.py`
- `promote_sandbox_extraction`
- `query_jobs`
- `record_crawl_run`

Codex-only shortcuts are not valid reference actions for this trace shape. That means no direct `.contexts/bin/*`, arbitrary host `rg`/`sed`/`cat`, direct filesystem edits, direct database access, browser actions, or shell commands unless the runtime agent has the same capability through an exposed tool and the event is recorded under that tool name.

A reference operator trace should therefore look like this, even when Codex is the actor:

```python
{
    "invocation_id": "codex-reference-run-001",
    "user_content": {"role": "user", "parts": [{"text": USER_TEXT}]},
    "intermediate_data": {
        "invocation_events": [
            {
                "author": "job_listing_scout",
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "function_call": {
                                "id": "call-1",
                                "name": "load_project_context",
                                "args": {"context_pack_id": "age26-runtime-context"},
                            }
                        }
                    ],
                },
            },
            {
                "author": "job_listing_scout",
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "function_response": {
                                "id": "call-1",
                                "name": "load_project_context",
                                "response": {
                                    "status": "success",
                                    "active_task": "AGE-26",
                                    "evidence": "context pack loaded before sandbox work",
                                },
                            }
                        }
                    ],
                },
            },
        ],
    },
    "final_response": {"role": "model", "parts": [{"text": "Final answer grounded in verified state."}]},
}
```

This keeps Codex/reference scoring comparable to ADK runtime-agent scoring. The scorer evaluates the complete trace; it does not know or care that the operator was Codex except through the event content.

## Shared Test Helpers

```python
from __future__ import annotations

from typing import Any

from scripts.utils import extract_invocation_events


AUTHOR = "job_listing_scout"
USER_TEXT = "Extract the ITviec Hanoi AI Engineer fixture and persist verified jobs."


def function_call_event(call_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "author": AUTHOR,
        "content": {
            "role": "model",
            "parts": [{"function_call": {"id": call_id, "name": name, "args": args or {}}}],
        },
    }


def function_response_event(call_id: str, name: str, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "author": AUTHOR,
        "content": {
            "role": "model",
            "parts": [{"function_response": {"id": call_id, "name": name, "response": response}}],
        },
    }


def tool_turn(
    index: int,
    name: str,
    args: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    call_id = f"call-{index}"
    return [
        function_call_event(call_id, name, args),
        function_response_event(call_id, name, response or {"status": "success"}),
    ]


def actual_invocation(
    invocation_id: str,
    invocation_events: list[dict[str, Any]],
    *,
    final_text: str | None = None,
    user_text: str = USER_TEXT,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "invocation_id": invocation_id,
        "user_content": {"role": "user", "parts": [{"text": user_text}]},
        "intermediate_data": {"invocation_events": invocation_events},
    }
    if final_text is not None:
        payload["final_response"] = {"role": "model", "parts": [{"text": final_text}]}
    return payload


def dashboard_events_from_actual_invocation(invocation: dict[str, Any]) -> list[dict[str, Any]]:
    events, _ = extract_invocation_events(
        run_id="age26-fixture",
        actual_invocation=invocation,
        invocation_index=0,
        invocation_path="fixture.eval_metric_result_per_invocation[0]",
        start_order=0,
    )
    return events
```

## Targeted Fixtures

### `optimal_adk_invocation`

This is the optimal target. It loads project context, loads task skill/context, starts the sandbox, performs bounded inspection, records the expected count and strategy, writes and runs the extractor, validates, finalizes, promotes, verifies persistence, records crawl metadata, and only then answers.

Expected normalized scores:

```python
{
    "milestone_completion": 1.0,
    "ordering_score": 1.0,
    "operation_efficiency": 1.0,
    "semantic_directness": 1.0,
    "efficiency_score": 1.0,
    "trajectory_score": 1.0,
}
```

```python
def optimal_adk_invocation() -> dict[str, Any]:
    audit_id = "sandbox_run_age18_gold_20260521_104255"
    events: list[dict[str, Any]] = []
    events += tool_turn(1, "load_project_context", {"context_pack_id": "age26-runtime-context"}, {"status": "success", "active_task": "AGE-26"})
    events += tool_turn(2, "load_skill_resource", {"skill": "job-listing-scout", "resource": "itviec-listing-v1"}, {"status": "success"})
    events += tool_turn(3, "sandbox_start.py", {"mode": "workflow", "html_artifact": "tests/fixtures/itviec_ai_engineer_ha_noi.html"}, {"status": "running", "audit_id": audit_id})
    events += tool_turn(4, "sandbox_exec.py", {"cmd": "python - <<'PY'\n# count job-card units and field markers\nPY"}, {"status": "success", "stdout": "job_card_class 20\njob_url_attr 20\njob_title_target 20"})
    events += tool_turn(5, "update_extraction_context", {"expected_output": {"expected_job_count": 20}}, {"status": "success"})
    events += tool_turn(6, "update_extraction_context", {"producer_output_plan": {"strategy": "one job per .job-card"}}, {"status": "success"})
    events += tool_turn(7, "sandbox_write_file.py", {"path": "output/extractor.py"}, {"status": "success", "path": "output/extractor.py"})
    events += tool_turn(8, "sandbox_exec.py", {"cmd": "python output/extractor.py"}, {"status": "success", "jobs": 20})
    events += tool_turn(9, "validate_outputs.py", {"audit_id": audit_id}, {"valid": True, "warnings": []})
    events += tool_turn(10, "sandbox_finalize.py", {"audit_id": audit_id}, {"status": "finalized"})
    events += tool_turn(11, "promote_sandbox_extraction", {"audit_id": audit_id}, {"status": "success", "written_count": 20})
    events += tool_turn(12, "query_jobs", {"source_name": "ITviec AI Engineer Hanoi", "limit": 25}, {"status": "success", "count": 20})
    events += tool_turn(13, "record_crawl_run", {"run_id": audit_id, "written_count": 20}, {"status": "success"})
    return actual_invocation("optimal-adk-invocation", events, final_text="Verified 20 persisted ITviec jobs from finalized sandbox output.")
```

### `good_non_exact_adk_invocation`

This reaches all required milestones in order, but includes two harmless neutral reads and uses a different bounded-inspection command than the gold path. It should score high, not perfect.

Expected normalized scores:

```python
{
    "milestone_completion": 1.0,
    "ordering_score": 1.0,
    "operation_efficiency": 0.875,
    "semantic_directness": 0.9375,
    "efficiency_score": 0.89375,
    "trajectory_score": 0.9734375,
}
```

```python
def good_non_exact_adk_invocation() -> dict[str, Any]:
    audit_id = "sandbox_run_age18_gold_20260521_104255"
    events: list[dict[str, Any]] = []
    events += tool_turn(1, "load_project_context", {"context_pack_id": "age26-runtime-context"}, {"status": "success"})
    events += tool_turn(2, "load_skill_resource", {"skill": "job-listing-scout"}, {"status": "success"})
    events += tool_turn(3, "sandbox_start.py", {"mode": "workflow"}, {"status": "running", "audit_id": audit_id})
    events += tool_turn(4, "sandbox_read.py", {"path": "inputs.json"}, {"status": "success"})
    events += tool_turn(5, "sandbox_exec.py", {"cmd": "rg -c 'data-search--job-selection-target=\"jobTitle\"' page.html"}, {"status": "success", "stdout": "20"})
    events += tool_turn(6, "update_extraction_context", {"expected_output": {"expected_job_count": 20}}, {"status": "success"})
    events += tool_turn(7, "update_extraction_context", {"producer_output_plan": {"strategy": "reuse package if probe matches"}}, {"status": "success"})
    events += tool_turn(8, "sandbox_read.py", {"path": "progress.json"}, {"status": "success"})
    events += tool_turn(9, "sandbox_write_file.py", {"path": "output/extractor.py"}, {"status": "success"})
    events += tool_turn(10, "sandbox_exec.py", {"cmd": "python output/extractor.py"}, {"status": "success", "jobs": 20})
    events += tool_turn(11, "validate_outputs.py", {"audit_id": audit_id}, {"valid": True})
    events += tool_turn(12, "sandbox_finalize.py", {"audit_id": audit_id}, {"status": "finalized"})
    events += tool_turn(13, "promote_sandbox_extraction", {"audit_id": audit_id}, {"status": "success", "written_count": 20})
    events += tool_turn(14, "query_jobs", {"limit": 25}, {"status": "success", "count": 20})
    events += tool_turn(15, "record_crawl_run", {"written_count": 20}, {"status": "success"})
    return actual_invocation("good-non-exact-adk-invocation", events, final_text="Verified 20 persisted jobs.")
```

### `neutral_partial_progress_adk_invocation`

This makes useful early progress but stops before extractor execution, validation, finalization, promotion, persistence verification, crawl metadata, and final answer. It is neither a disaster nor a success.

Expected normalized scores:

```python
{
    "milestone_completion": 0.5,
    "ordering_score": 1 / 3,
    "operation_efficiency": 1.0,
    "semantic_directness": 0.85,
    "efficiency_score": 0.955,
    "trajectory_score": 0.56375,
}
```

```python
def neutral_partial_progress_adk_invocation() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    events += tool_turn(1, "load_project_context", {"context_pack_id": "age26-runtime-context"}, {"status": "success"})
    events += tool_turn(2, "load_skill_resource", {"skill": "job-listing-scout"}, {"status": "success"})
    events += tool_turn(3, "sandbox_start.py", {"mode": "workflow"}, {"status": "running"})
    events += tool_turn(4, "sandbox_read.py", {"path": "plan.md"}, {"status": "success"})
    events += tool_turn(5, "sandbox_exec.py", {"cmd": "python - <<'PY'\n# inspect cards\nPY"}, {"status": "success", "stdout": "20 cards"})
    events += tool_turn(6, "update_extraction_context", {"expected_output": {"expected_job_count": 20}}, {"status": "success"})
    events += tool_turn(7, "sandbox_read.py", {"path": "inputs.json"}, {"status": "success"})
    events += tool_turn(8, "update_extraction_context", {"producer_output_plan": {"strategy": "write extractor"}}, {"status": "success"})
    events += tool_turn(9, "sandbox_read.py", {"path": "progress.json"}, {"status": "success"})
    events += tool_turn(10, "sandbox_write_file.py", {"path": "output/extractor.py"}, {"status": "success"})
    return actual_invocation("neutral-partial-progress-adk-invocation", events)
```

### `bad_no_verified_output_adk_invocation`

This repeatedly attempts unplanned output writes and produces no verified extraction output. It should score very low even though it is busy.

Expected normalized scores:

```python
{
    "milestone_completion": 0.0,
    "ordering_score": 0.0,
    "operation_efficiency": 0.7,
    "semantic_directness": 0.0,
    "efficiency_score": 0.49,
    "trajectory_score": 0.1225,
}
```

```python
def bad_no_verified_output_adk_invocation() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for index in range(1, 21):
        events += tool_turn(
            index,
            "sandbox_write_file.py",
            {"path": f"output/random_{index}.json"},
            {"status": "rejected", "guardrail": "unplanned_output_write"},
        )
    return actual_invocation("bad-no-verified-output-adk-invocation", events)
```

### `bad_premature_finalization_adk_invocation`

This fixture is intentionally stricter than the earlier abstract sketch. Rejected finalize/promote attempts are harmful evidence, but they do not complete `sandbox_finalized` or `output_promoted`. A premature final response is harmful and also does not complete `final_answer_from_verified_state`.

Expected normalized scores:

```python
{
    "milestone_completion": 2 / 14,
    "ordering_score": 1 / 9,
    "operation_efficiency": 1.0,
    "semantic_directness": 0.0,
    "efficiency_score": 0.7,
    "trajectory_score": 0.2726190476,
}
```

```python
def bad_premature_finalization_adk_invocation() -> dict[str, Any]:
    audit_id = "sandbox_run_age18_gold_20260521_104255"
    events: list[dict[str, Any]] = []
    events += tool_turn(1, "load_project_context", {"context_pack_id": "age26-runtime-context"}, {"status": "success"})
    events += tool_turn(2, "sandbox_finalize.py", {"audit_id": audit_id}, {"status": "rejected", "guardrail": "validation_not_passed"})
    events += tool_turn(3, "promote_sandbox_extraction", {"audit_id": audit_id}, {"status": "rejected", "guardrail": "sandbox_not_finalized"})
    events += tool_turn(4, "sandbox_start.py", {"mode": "workflow"}, {"status": "running", "audit_id": audit_id})
    events += tool_turn(5, "sandbox_exec.py", {"cmd": "cat progress.json"}, {"status": "success", "stdout": "{}"})
    events += tool_turn(6, "final_answer", {"text": "Reported unverified results"}, {"status": "rejected", "guardrail": "unverified_final_answer"})
    return actual_invocation("bad-premature-finalization-adk-invocation", events, final_text="I extracted and saved the jobs.")
```

## First Test Flow

```python
@pytest.mark.parametrize(
    ("fixture_factory", "expected"),
    [
        (optimal_adk_invocation, {"trajectory_score": 1.0}),
        (good_non_exact_adk_invocation, {"trajectory_score": 0.9734375}),
        (neutral_partial_progress_adk_invocation, {"trajectory_score": 0.56375}),
        (bad_no_verified_output_adk_invocation, {"trajectory_score": 0.1225}),
        (bad_premature_finalization_adk_invocation, {"trajectory_score": 0.2726190476}),
    ],
)
def test_adk_db_mirrored_calibration_runs_score_as_expected(fixture_factory, expected) -> None:
    dashboard_events = dashboard_events_from_actual_invocation(fixture_factory())
    normalized_events = normalize_dashboard_events(dashboard_events)
    score = score_trajectory(normalized_events, trajectory_spec())

    assert score.trajectory_score == pytest.approx(expected["trajectory_score"])
```
