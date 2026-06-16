from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from job_scraper.continuation_eval_scoring import (
    ResponseQualityJudgment,
    normalize_dashboard_events,
    score_milestone_completion,
    score_operation_efficiency,
    score_ordering,
    score_response_quality,
    score_semantic_directness,
    score_trajectory,
)


CASE_MODULE_PATH = Path(__file__).resolve().parent / "fixtures" / "continuation_eval_scoring_cases.py"
CASE_SPEC = importlib.util.spec_from_file_location("continuation_eval_scoring_cases", CASE_MODULE_PATH)
if CASE_SPEC is None or CASE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load continuation eval cases from {CASE_MODULE_PATH}")
cases = importlib.util.module_from_spec(CASE_SPEC)
sys.modules[CASE_SPEC.name] = cases
CASE_SPEC.loader.exec_module(cases)


@pytest.mark.parametrize("case", cases.ADK_CALIBRATION_CASES, ids=lambda case: case.name)
def test_adk_calibration_runs_score_as_expected(case) -> None:
    dashboard_events = cases.adk_traces.dashboard_events_from_actual_invocation(case.invocation_factory())
    normalized = normalize_dashboard_events(dashboard_events)
    result = score_trajectory(normalized, cases.trajectory_spec())
    expected = case.expected

    assert result.milestone_completion == pytest.approx(expected.milestone_completion)
    assert result.ordering_score == pytest.approx(expected.ordering_score)
    assert result.operation_efficiency == pytest.approx(expected.operation_efficiency)
    assert result.semantic_directness == pytest.approx(expected.semantic_directness)
    assert result.efficiency_score == pytest.approx(expected.efficiency_score)
    assert result.trajectory_score == pytest.approx(expected.trajectory_score)


def test_milestone_completion_full_credit_for_all_required_milestones() -> None:
    events = [cases.event("project_context_loaded"), cases.event("sandbox_started"), cases.event("validation_passed")]

    assert score_milestone_completion(events, ["project_context_loaded", "sandbox_started", "validation_passed"]) == 1.0


def test_milestone_completion_partial_credit_for_missing_required_milestones() -> None:
    events = [cases.event("project_context_loaded"), cases.event("validation_passed")]

    assert score_milestone_completion(events, ["project_context_loaded", "sandbox_started", "validation_passed"]) == pytest.approx(2 / 3)


def test_milestone_completion_ignores_duplicate_milestones() -> None:
    events = [cases.event("project_context_loaded"), cases.event("project_context_loaded"), cases.event("validation_passed")]

    assert score_milestone_completion(events, ["project_context_loaded", "validation_passed"]) == 1.0


def test_milestone_completion_does_not_require_optional_milestones() -> None:
    events = [cases.event("project_context_loaded"), cases.event("sandbox_started"), cases.event("validation_passed")]

    assert (
        score_milestone_completion(
            events,
            ["project_context_loaded", "sandbox_started", "validation_passed"],
            optional_milestones=["reusable_package_probe_matched"],
        )
        == 1.0
    )


def test_ordering_score_full_credit_for_gold_order() -> None:
    events = [cases.event("sandbox_started"), cases.event("bounded_inspection"), cases.event("validation_passed")]

    assert score_ordering(events, [("sandbox_started", "bounded_inspection"), ("bounded_inspection", "validation_passed")]) == 1.0


def test_ordering_score_penalizes_critical_order_inversions() -> None:
    events = [cases.event("sandbox_finalized"), cases.event("validation_passed"), cases.event("output_promoted")]

    assert score_ordering(events, [("validation_passed", "sandbox_finalized"), ("sandbox_finalized", "output_promoted")]) == pytest.approx(0.5)


def test_ordering_score_allows_harmless_interleaving() -> None:
    events = [
        cases.event("sandbox_started"),
        cases.event(None, label="neutral", effective=False, evidence="read progress.json"),
        cases.event("bounded_inspection"),
        cases.event(None, label="neutral", effective=False, evidence="read inputs.json"),
        cases.event("validation_passed"),
    ]

    assert score_ordering(events, [("sandbox_started", "bounded_inspection"), ("bounded_inspection", "validation_passed")]) == 1.0


def test_ordering_score_penalizes_missing_pair_members() -> None:
    events = [cases.event("sandbox_started"), cases.event("validation_passed")]

    assert score_ordering(events, [("sandbox_started", "bounded_inspection"), ("bounded_inspection", "validation_passed")]) == 0.0


def test_operation_efficiency_full_credit_at_gold_effective_operation_count() -> None:
    assert score_operation_efficiency(gold_effective_ops=8, actual_effective_ops=8) == 1.0


def test_operation_efficiency_penalizes_extra_effective_operations() -> None:
    assert score_operation_efficiency(gold_effective_ops=8, actual_effective_ops=16) == 0.5


def test_operation_efficiency_does_not_reward_shorter_than_gold_runs() -> None:
    assert score_operation_efficiency(gold_effective_ops=8, actual_effective_ops=4) == 1.0


def test_semantic_directness_counts_productive_and_repair_as_direct() -> None:
    events = [cases.event("bounded_inspection", label="productive"), cases.event("targeted_repair", label="repair")]

    assert score_semantic_directness(events) == 1.0


def test_semantic_directness_tolerates_neutral_steps_without_rewarding_them() -> None:
    productive = score_semantic_directness([cases.event("bounded_inspection", label="productive")])
    with_neutral = score_semantic_directness(
        [cases.event("bounded_inspection", label="productive"), cases.event(None, label="neutral", effective=True)]
    )

    assert 0.0 < with_neutral < productive


def test_semantic_directness_penalizes_detour_and_harmful_steps() -> None:
    with_detour = score_semantic_directness(
        [cases.event("bounded_inspection", label="productive"), cases.event(None, label="detour", effective=True)]
    )
    with_harmful = score_semantic_directness(
        [cases.event("bounded_inspection", label="productive"), cases.event(None, label="harmful", effective=True)]
    )

    assert with_harmful < with_detour < 1.0


def test_inefficient_successful_run_scores_lower_without_failing() -> None:
    direct_score = score_trajectory(cases.DIRECT_MILESTONE_RUN, cases.trajectory_spec())
    inefficient_score = score_trajectory(cases.INEFFICIENT_SUCCESSFUL_RUN, cases.trajectory_spec())

    assert inefficient_score.trajectory_score < direct_score.trajectory_score
    assert inefficient_score.trajectory_score > 0.7
    assert inefficient_score.milestone_completion == 1.0


def test_non_exact_trajectory_can_score_high_when_milestones_order_and_quality_are_good() -> None:
    score = score_trajectory(cases.NON_EXACT_GOOD_TRAJECTORY, cases.trajectory_spec())

    assert score.trajectory_score >= 0.95
    assert score.milestone_completion == 1.0
    assert score.ordering_score == 1.0


def test_gold_itviec_fixture_scores_at_or_near_one() -> None:
    score = score_trajectory(cases.AGE18_GOLD_ITVIEC_SHAPE, cases.age18_spec())

    assert score.trajectory_score >= 0.95
    assert score.milestone_completion == 1.0
    assert score.ordering_score == 1.0


def test_reuse_package_probe_receives_credit_before_new_extractor_authoring() -> None:
    assert score_trajectory(cases.REUSE_PACKAGE_TRAJECTORY, cases.age18_spec()).trajectory_score >= 0.95


def test_new_extractor_despite_matching_reusable_package_scores_lower() -> None:
    rewrite_score = score_trajectory(cases.UNNECESSARY_REWRITE_TRAJECTORY, cases.age18_spec())
    reuse_score = score_trajectory(cases.REUSE_PACKAGE_TRAJECTORY, cases.age18_spec())

    assert rewrite_score.trajectory_score < reuse_score.trajectory_score


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


def test_normalize_dashboard_events_maps_tool_calls_to_semantic_milestones() -> None:
    events = normalize_dashboard_events(cases.NORMALIZER_FULL_SUCCESS_EVENTS)

    assert [item.milestone for item in events] == cases.GOLD_MILESTONES


def test_normalize_dashboard_events_maps_runtime_run_skill_script_calls_to_sandbox_milestones() -> None:
    events = normalize_dashboard_events(cases.RUNTIME_RUN_SKILL_SCRIPT_SUCCESS_EVENTS)

    assert [item.milestone for item in events] == [
        "project_context_loaded",
        "sandbox_started",
        "bounded_inspection",
        "accountable_extractor_written",
        "extractor_executed",
        "validation_passed",
        "sandbox_finalized",
    ]


def test_normalize_dashboard_events_uses_tool_response_to_distinguish_validation_result() -> None:
    events = normalize_dashboard_events(cases.VALIDATION_RESULT_EVENTS)

    assert [item.milestone for item in events] == [
        "validation_failed_with_concrete_error",
        "validation_passed",
    ]
    assert events[0].label == "productive"


def test_normalize_dashboard_events_marks_repeated_noop_as_detour() -> None:
    events = normalize_dashboard_events(cases.REPEATED_NOOP_EVENTS)

    assert [item.label for item in events] == ["neutral", "detour"]


def test_normalize_dashboard_events_marks_rejected_premature_actions_harmful() -> None:
    events = normalize_dashboard_events(cases.REJECTED_PREMATURE_ACTION_EVENTS)

    assert [item.label for item in events] == ["harmful", "harmful", "harmful"]
    assert [item.milestone for item in events] == [None, None, None]
