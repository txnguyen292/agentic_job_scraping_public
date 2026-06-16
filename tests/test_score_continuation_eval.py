from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from typer.testing import CliRunner

from job_scraper.continuation_eval_trace_models import AdkActualInvocation, AdkTraceFixtureSet


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "score_continuation_eval.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("score_continuation_eval", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
score_continuation_eval = importlib.util.module_from_spec(SCRIPT_SPEC)
assert SCRIPT_SPEC.loader is not None
SCRIPT_SPEC.loader.exec_module(score_continuation_eval)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "continuation_eval_adk_traces.json"
CODEX_REFERENCE_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "continuation_eval_codex_reference_runtime_run.json"


def test_trace_fixture_json_validates_into_pydantic_models() -> None:
    fixture_set = AdkTraceFixtureSet.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))

    invocation = fixture_set.root["optimal_gold_run"]

    assert isinstance(invocation, AdkActualInvocation)
    assert invocation.invocation_id == "optimal-adk-invocation"
    first_part = invocation.intermediate_data.invocation_events[0].content.parts[0]
    assert first_part.function_call is not None
    assert first_part.function_call.name == "load_project_context"


def test_score_script_scores_named_optimal_fixture() -> None:
    result = CliRunner().invoke(
        score_continuation_eval.app,
        ["--fixture-file", str(FIXTURE_PATH), "--fixture", "optimal_gold_run"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["case"] == "optimal_gold_run"
    assert payload["trajectory_score"] == 1.0
    assert payload["milestone_completion"] == 1.0
    assert payload["ordering_score"] == 1.0


def test_score_script_scores_named_bad_fixture() -> None:
    result = CliRunner().invoke(
        score_continuation_eval.app,
        ["--fixture-file", str(FIXTURE_PATH), "--fixture", "bad_premature_finalization_run"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["case"] == "bad_premature_finalization_run"
    assert payload["trajectory_score"] == 0.2726190476190476
    assert payload["milestone_completion"] == 2 / 14
    assert payload["ordering_score"] == 1 / 9


def test_score_script_outputs_raw_normalized_events_for_bad_fixture() -> None:
    result = CliRunner().invoke(
        score_continuation_eval.app,
        ["--fixture-file", str(FIXTURE_PATH), "--fixture", "bad_premature_finalization_run"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert "diagnostics" not in payload
    assert "repair_hints" not in payload
    assert payload["normalized_events"][0]["milestone"] == "project_context_loaded"
    assert payload["normalized_events"][1]["label"] == "harmful"
    assert payload["normalized_events"][1]["tool_name"] == "sandbox_finalize.py"
    assert payload["normalized_events"][1]["evidence"] == "{'guardrail': 'validation_not_passed', 'status': 'rejected'}"


def test_score_script_reports_factual_ordering_score_breakdown() -> None:
    result = CliRunner().invoke(
        score_continuation_eval.app,
        ["--fixture-file", str(FIXTURE_PATH), "--fixture", "bad_premature_finalization_run"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    ordering = payload["score_breakdown"]["ordering_score"]

    assert ordering["score"] == 1 / 9
    assert ordering["description"] == "Fraction of critical milestone order checks satisfied by first occurrence indexes."
    assert ordering["formula"] == "satisfied_count / total_count"
    assert ordering["satisfied_count"] == 1
    assert ordering["total_count"] == 9
    assert ordering["pairs"][0] == {
        "before": "project_context_loaded",
        "after": "sandbox_started",
        "before_index": 1,
        "after_index": 4,
        "status": "satisfied",
    }
    assert ordering["pairs"][1] == {
        "before": "sandbox_started",
        "after": "bounded_inspection",
        "before_index": 4,
        "after_index": None,
        "status": "missing_after",
    }
    assert ordering["pairs"][4] == {
        "before": "extractor_executed",
        "after": "validation_passed",
        "before_index": None,
        "after_index": None,
        "status": "missing_before",
    }


def test_score_script_reports_factual_score_component_breakdown() -> None:
    result = CliRunner().invoke(
        score_continuation_eval.app,
        ["--fixture-file", str(FIXTURE_PATH), "--fixture", "bad_premature_finalization_run"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    breakdown = payload["score_breakdown"]

    assert breakdown["milestone_completion"]["description"] == (
        "Fraction of required workflow milestones that appear at least once in the normalized events."
    )
    assert breakdown["milestone_completion"]["formula"] == "completed_count / required_count"
    assert breakdown["milestone_completion"]["completed_count"] == 2
    assert breakdown["milestone_completion"]["required_count"] == 14
    assert breakdown["milestone_completion"]["completed"] == ["project_context_loaded", "sandbox_started"]
    assert "validation_passed" in breakdown["milestone_completion"]["missing"]
    assert breakdown["operation_efficiency"]["description"] == (
        "Compares the gold effective operation count with this run's effective operation count; shorter-than-gold runs cap at 1.0."
    )
    assert breakdown["semantic_directness"]["label_counts"] == {
        "productive": 2,
        "repair": 0,
        "neutral": 1,
        "detour": 0,
        "harmful": 4,
    }
    assert breakdown["semantic_directness"]["points_sum"] == -1.5
    assert breakdown["semantic_directness"]["description"] == (
        "Average label point value across effective normalized events, clamped to [0.0, 1.0]."
    )
    assert breakdown["semantic_directness"]["formula"] == "max(0.0, min(1.0, points_sum / effective_event_count))"
    assert breakdown["semantic_directness"]["score"] == 0.0
    assert breakdown["efficiency_score"]["description"] == (
        "Weighted aggregate of operation_efficiency and semantic_directness."
    )
    assert breakdown["efficiency_score"]["formula"] == "0.70 * operation_efficiency + 0.30 * semantic_directness"
    assert breakdown["trajectory_score"]["description"] == (
        "Weighted aggregate of milestone_completion, ordering_score, and efficiency_score."
    )
    assert breakdown["trajectory_score"]["formula"] == (
        "0.45 * milestone_completion + 0.30 * ordering_score + 0.25 * efficiency_score"
    )
    assert breakdown["trajectory_score"]["terms"] == [
        {"component": "milestone_completion", "weight": 0.45, "score": 2 / 14, "contribution": 0.45 * (2 / 14)},
        {"component": "ordering_score", "weight": 0.30, "score": 1 / 9, "contribution": 0.30 * (1 / 9)},
        {"component": "efficiency_score", "weight": 0.25, "score": 0.7, "contribution": 0.175},
    ]


def test_score_script_scores_codex_reference_runtime_run_as_successful_workflow() -> None:
    result = CliRunner().invoke(
        score_continuation_eval.app,
        ["--fixture-file", str(CODEX_REFERENCE_FIXTURE_PATH), "--fixture", "codex_reference_runtime_run"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["case"] == "codex_reference_runtime_run"
    assert payload["milestone_completion"] == 1.0
    assert payload["ordering_score"] == 1.0
    assert payload["trajectory_score"] == 1.0
    assert "diagnostics" not in payload
    assert len(payload["normalized_events"]) == 14
    assert payload["normalized_events"][-1]["milestone"] == "final_answer_from_verified_state"
