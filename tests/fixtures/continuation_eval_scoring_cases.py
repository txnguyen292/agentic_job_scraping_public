"""Reusable continuation eval scoring cases and expected scores."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from job_scraper.continuation_eval_scoring import NormalizedEvent, TrajectoryScoringSpec


_TRACE_MODULE_PATH = Path(__file__).with_name("continuation_eval_adk_traces.py")
_TRACE_SPEC = importlib.util.spec_from_file_location("continuation_eval_adk_traces_for_cases", _TRACE_MODULE_PATH)
if _TRACE_SPEC is None or _TRACE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load continuation eval ADK traces from {_TRACE_MODULE_PATH}")
adk_traces = importlib.util.module_from_spec(_TRACE_SPEC)
sys.modules[_TRACE_SPEC.name] = adk_traces
_TRACE_SPEC.loader.exec_module(adk_traces)


@dataclass(frozen=True)
class ExpectedTrajectoryBreakdown:
    milestone_completion: float
    ordering_score: float
    operation_efficiency: float
    semantic_directness: float
    efficiency_score: float
    trajectory_score: float


@dataclass(frozen=True)
class AdkCalibrationCase:
    name: str
    invocation_factory: Callable[[], dict[str, Any]]
    expected: ExpectedTrajectoryBreakdown


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


def age18_spec() -> TrajectoryScoringSpec:
    return TrajectoryScoringSpec(
        required_milestones=GOLD_MILESTONES,
        critical_order_pairs=CRITICAL_ORDER_PAIRS,
        gold_effective_ops=len(GOLD_MILESTONES),
        optional_milestones=("reusable_package_probe_matched", "reusable_package_extractor_used"),
    )


def event(
    milestone: str | None,
    *,
    label: str = "productive",
    tool_name: str | None = "tool",
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


ADK_CALIBRATION_CASES = [
    AdkCalibrationCase(
        name="optimal_gold_run",
        invocation_factory=adk_traces.optimal_adk_invocation,
        expected=ExpectedTrajectoryBreakdown(
            milestone_completion=1.0,
            ordering_score=1.0,
            operation_efficiency=1.0,
            semantic_directness=1.0,
            efficiency_score=1.0,
            trajectory_score=1.0,
        ),
    ),
    AdkCalibrationCase(
        name="good_non_exact_run",
        invocation_factory=adk_traces.good_non_exact_adk_invocation,
        expected=ExpectedTrajectoryBreakdown(
            milestone_completion=1.0,
            ordering_score=1.0,
            operation_efficiency=0.875,
            semantic_directness=0.9375,
            efficiency_score=0.89375,
            trajectory_score=0.9734375,
        ),
    ),
    AdkCalibrationCase(
        name="neutral_partial_progress_run",
        invocation_factory=adk_traces.neutral_partial_progress_adk_invocation,
        expected=ExpectedTrajectoryBreakdown(
            milestone_completion=0.5,
            ordering_score=1 / 3,
            operation_efficiency=1.0,
            semantic_directness=0.85,
            efficiency_score=0.955,
            trajectory_score=0.56375,
        ),
    ),
    AdkCalibrationCase(
        name="bad_no_verified_output_run",
        invocation_factory=adk_traces.bad_no_verified_output_adk_invocation,
        expected=ExpectedTrajectoryBreakdown(
            milestone_completion=0.0,
            ordering_score=0.0,
            operation_efficiency=0.7,
            semantic_directness=0.0,
            efficiency_score=0.49,
            trajectory_score=0.1225,
        ),
    ),
    AdkCalibrationCase(
        name="bad_premature_finalization_run",
        invocation_factory=adk_traces.bad_premature_finalization_adk_invocation,
        expected=ExpectedTrajectoryBreakdown(
            milestone_completion=2 / 14,
            ordering_score=1 / 9,
            operation_efficiency=1.0,
            semantic_directness=0.0,
            efficiency_score=0.7,
            trajectory_score=0.2726190476,
        ),
    ),
]


DIRECT_MILESTONE_RUN = [event(name) for name in GOLD_MILESTONES]


INEFFICIENT_SUCCESSFUL_RUN = DIRECT_MILESTONE_RUN + [
    event(None, label="neutral", evidence="extra status read"),
    event(None, label="detour", evidence="repeated no-op inspection"),
]


NON_EXACT_GOOD_TRAJECTORY = [
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


AGE18_GOLD_ITVIEC_SHAPE = [
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


REUSE_PACKAGE_TRAJECTORY = [
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


UNNECESSARY_REWRITE_TRAJECTORY = [
    *REUSE_PACKAGE_TRAJECTORY[:5],
    event(None, label="detour", tool_name="sandbox_write_file.py", evidence="wrote new extractor despite matched package"),
    *REUSE_PACKAGE_TRAJECTORY[6:],
]


def dashboard_call(name: str, args: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "kind": "function_call",
        "title": name,
        "payload": {"name": name, "args": args or {}},
    }


def dashboard_response(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "function_response",
        "title": name,
        "payload": {"name": name, "response": payload},
    }


def dashboard_final_response(text: str) -> dict[str, object]:
    return {
        "kind": "final_response",
        "title": "Final response",
        "payload": {"role": "model", "parts": [{"text": text}]},
    }


NORMALIZER_FULL_SUCCESS_EVENTS = [
    dashboard_call("load_project_context", {"context_pack_id": "age26-runtime-context"}),
    dashboard_response("load_project_context", {"status": "success"}),
    dashboard_call("load_skill_resource", {"skill": "sandbox-page-analyst"}),
    dashboard_response("load_skill_resource", {"status": "success"}),
    dashboard_call("sandbox_start.py"),
    dashboard_response("sandbox_start.py", {"status": "running"}),
    dashboard_call("sandbox_exec.py", {"cmd": "python - <<'PY'\n# count .job-card units\nPY"}),
    dashboard_response("sandbox_exec.py", {"status": "success", "stdout": "20 cards"}),
    dashboard_call("update_extraction_context", {"expected_output": {"expected_job_count": 20}}),
    dashboard_response("update_extraction_context", {"status": "success"}),
    dashboard_call("update_extraction_context", {"producer_output_plan": {"strategy": "one job per card"}}),
    dashboard_response("update_extraction_context", {"status": "success"}),
    dashboard_call("sandbox_write_file.py", {"path": "output/extractor.py"}),
    dashboard_response("sandbox_write_file.py", {"status": "success"}),
    dashboard_call("sandbox_exec.py", {"cmd": "python output/extractor.py"}),
    dashboard_response("sandbox_exec.py", {"status": "success", "jobs": 20}),
    dashboard_call("validate_outputs.py"),
    dashboard_response("validate_outputs.py", {"valid": True}),
    dashboard_call("sandbox_finalize.py"),
    dashboard_response("sandbox_finalize.py", {"status": "finalized"}),
    dashboard_call("promote_sandbox_extraction"),
    dashboard_response("promote_sandbox_extraction", {"status": "success", "written_count": 20}),
    dashboard_call("query_jobs"),
    dashboard_response("query_jobs", {"status": "success", "count": 20}),
    dashboard_call("record_crawl_run"),
    dashboard_response("record_crawl_run", {"status": "success"}),
    dashboard_final_response("Verified 20 persisted jobs."),
]


RUNTIME_RUN_SKILL_SCRIPT_SUCCESS_EVENTS = [
    dashboard_call("run_skill_script", {"skill_name": "project-context", "file_path": "scripts/context_overview.py", "args": []}),
    dashboard_response("run_skill_script", {"status": "success", "stdout_json": {"project": "codex-age-26"}}),
    dashboard_call(
        "run_skill_script",
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_start.py",
            "args": ["--mode", "workflow", "--page-artifact", "/tmp/page.html"],
        },
    ),
    dashboard_response("run_skill_script", {"status": "running", "stdout_json": {"audit_id": "sandbox_run_age26"}}),
    dashboard_call(
        "run_skill_script",
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_exec.py",
            "args": ["--audit-id", "sandbox_run_age26", "--cmd", "python - <<'PY'\nprint('job-card count')\nPY"],
        },
    ),
    dashboard_response("run_skill_script", {"status": "success", "stdout_json": {"stdout": '{"job_card_class": 20}'}}),
    dashboard_call(
        "run_skill_script",
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_write_file.py",
            "args": ["--audit-id", "sandbox_run_age26", "--path", "output/extractor.py", "--content", "print('ok')"],
        },
    ),
    dashboard_response("run_skill_script", {"status": "success", "stdout_json": {"path": "output/extractor.py"}}),
    dashboard_call(
        "run_skill_script",
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_exec.py",
            "args": ["--audit-id", "sandbox_run_age26", "--cmd", "python output/extractor.py"],
        },
    ),
    dashboard_response("run_skill_script", {"status": "success", "stdout_json": {"stdout": '{"job_count": 20}'}}),
    dashboard_call(
        "run_skill_script",
        {"skill_name": "sandbox-page-analyst", "file_path": "scripts/validate_outputs.py", "args": ["--audit-id", "sandbox_run_age26"]},
    ),
    dashboard_response("run_skill_script", {"status": "success", "valid": True, "stdout_json": {"valid": True}}),
    dashboard_call(
        "run_skill_script",
        {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_finalize.py", "args": ["--audit-id", "sandbox_run_age26"]},
    ),
    dashboard_response("run_skill_script", {"status": "success", "stdout_json": {"status": "success"}}),
]


VALIDATION_RESULT_EVENTS = [
    dashboard_call("validate_outputs.py"),
    dashboard_response("validate_outputs.py", {"valid": False, "errors": [{"path": "jobs[0].tags"}]}),
    dashboard_call("validate_outputs.py"),
    dashboard_response("validate_outputs.py", {"valid": True, "warnings": []}),
]


REPEATED_NOOP_EVENTS = [
    dashboard_call("sandbox_exec.py", {"cmd": "cat progress.json"}),
    dashboard_response("sandbox_exec.py", {"status": "success", "stdout": "{}"}),
    dashboard_call("sandbox_exec.py", {"cmd": "cat progress.json"}),
    dashboard_response("sandbox_exec.py", {"status": "success", "stdout": "{}"}),
]


REJECTED_PREMATURE_ACTION_EVENTS = [
    dashboard_call("sandbox_finalize.py", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
    dashboard_response("sandbox_finalize.py", {"status": "rejected", "guardrail": "validation_not_passed"}),
    dashboard_call("promote_sandbox_extraction", {"audit_id": "sandbox_run_age18_gold_20260521_104255"}),
    dashboard_response("promote_sandbox_extraction", {"status": "rejected", "guardrail": "sandbox_not_finalized"}),
    dashboard_final_response("I extracted and saved the jobs."),
]
