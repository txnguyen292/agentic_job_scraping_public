"""Deterministic scoring helpers for continuation-prompt eval traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence


SemanticLabel = Literal["productive", "repair", "neutral", "detour", "harmful"]
ResponseVerdict = Literal["pass", "fail"]
ResponseScoreVerdict = Literal["pass", "fail", "not_evaluated"]


@dataclass(frozen=True)
class NormalizedEvent:
    milestone: str | None
    label: SemanticLabel
    tool_name: str | None = None
    effective: bool = True
    evidence: str = ""


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


@dataclass(frozen=True)
class ResponseQualityJudgment:
    score: float
    verdict: ResponseVerdict
    rationale: str


@dataclass(frozen=True)
class ResponseQualityScore:
    score: float | None
    verdict: ResponseScoreVerdict
    rationale: str
    not_evaluated_reason: str = ""


LABEL_DIRECTNESS_POINTS: dict[SemanticLabel, float] = {
    "productive": 1.0,
    "repair": 1.0,
    "neutral": 0.5,
    "detour": 0.0,
    "harmful": -1.0,
}
COLLAPSE_DUPLICATE_MILESTONES = {"skill_and_contract_loaded"}


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


def _event_response(event: dict[str, object] | None) -> dict[str, object]:
    if event is None:
        return {}
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        return payload["response"]  # type: ignore[return-value]
    return {}


def _normalize_skill_path(path: object) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _skill_script_path(tool_name: str, args: dict[str, object]) -> str:
    if tool_name != "run_skill_script":
        return ""
    return _normalize_skill_path(args.get("file_path"))


def _skill_script_args(args: dict[str, object]) -> list[Any]:
    raw_args = args.get("args")
    if isinstance(raw_args, list):
        return raw_args
    return []


def _option_value(args: Sequence[Any], option: str) -> Any | None:
    for index, value in enumerate(args):
        if str(value) == option and index + 1 < len(args):
            return args[index + 1]
    return None


def _command_text(args: dict[str, object]) -> str:
    for key in ("cmd", "command"):
        if args.get(key):
            return str(args[key])
    command = _option_value(_skill_script_args(args), "--cmd")
    if command is None:
        command = _option_value(_skill_script_args(args), "--command")
    if command is not None:
        return str(command)
    return ""


def _target_path(args: dict[str, object]) -> str:
    if args.get("path"):
        return _normalize_skill_path(args["path"])
    return _normalize_skill_path(_option_value(_skill_script_args(args), "--path"))


def _script_display_name(tool_name: str, args: dict[str, object]) -> str:
    script_path = _skill_script_path(tool_name, args)
    if not script_path:
        return tool_name
    return script_path.rsplit("/", 1)[-1]


def _stdout_json(response_payload: dict[str, object]) -> dict[str, object]:
    value = response_payload.get("stdout_json")
    return value if isinstance(value, dict) else {}


def _response_value(response_payload: dict[str, object], key: str) -> object | None:
    if key in response_payload:
        return response_payload[key]
    return _stdout_json(response_payload).get(key)


def _response_status(response_payload: dict[str, object]) -> str:
    value = _response_value(response_payload, "status")
    return str(value) if value is not None else ""


def _is_rejected(response_payload: dict[str, object]) -> bool:
    return _response_status(response_payload) in {"rejected", "error", "failed"}


def _is_successful(response_payload: dict[str, object]) -> bool:
    return not _is_rejected(response_payload)


def _normalize_tool_interaction(
    call_event: dict[str, object],
    response_event: dict[str, object] | None,
) -> NormalizedEvent | None:
    tool_name = _event_tool_name(call_event)
    args = _event_args(call_event)
    response_payload = _event_response(response_event)
    script_path = _skill_script_path(tool_name, args)
    display_tool_name = _script_display_name(tool_name, args)
    command = _command_text(args)
    target_path = _target_path(args)
    rejected = _is_rejected(response_payload)

    if (
        tool_name == "load_project_context"
        or (
            tool_name == "run_skill_script"
            and str(args.get("skill_name") or "") == "project-context"
            and script_path == "scripts/context_overview.py"
        )
    ) and _is_successful(response_payload):
        return NormalizedEvent("project_context_loaded", "productive", display_tool_name, True, str(args))
    if tool_name in {"load_skill_resource", "load_resource", "load_skill"} and _is_successful(response_payload):
        return NormalizedEvent("skill_and_contract_loaded", "productive", tool_name, True, str(args))
    if display_tool_name == "sandbox_start.py" and _is_successful(response_payload):
        return NormalizedEvent("sandbox_started", "productive", display_tool_name, True, str(response_payload))
    if display_tool_name == "sandbox_read.py":
        return NormalizedEvent(None, "neutral", display_tool_name, True, str(args))
    if display_tool_name == "sandbox_exec.py" and "output/extractor.py" in command and _is_successful(response_payload):
        return NormalizedEvent("extractor_executed", "productive", display_tool_name, True, command)
    if display_tool_name == "sandbox_exec.py" and any(
        token in command for token in (".job-card", "job-card", "jobTitle", "inspect cards", "count")
    ):
        return NormalizedEvent("bounded_inspection", "productive", display_tool_name, True, command)
    if display_tool_name == "sandbox_exec.py":
        return NormalizedEvent(None, "neutral", display_tool_name, True, command)
    if tool_name == "update_extraction_context" and isinstance(args.get("expected_output"), dict):
        return NormalizedEvent("expected_count_derived", "productive", tool_name, True, str(args["expected_output"]))
    if tool_name == "update_extraction_context" and isinstance(args.get("producer_output_plan"), dict):
        return NormalizedEvent("strategy_recorded", "productive", tool_name, True, str(args["producer_output_plan"]))
    if display_tool_name == "sandbox_write_file.py":
        if rejected:
            return NormalizedEvent(None, "harmful", display_tool_name, True, str(response_payload))
        if target_path == "output/extractor.py":
            return NormalizedEvent("accountable_extractor_written", "productive", display_tool_name, True, str(args))
        return NormalizedEvent(None, "neutral", display_tool_name, True, str(args))
    if display_tool_name == "validate_outputs.py":
        milestone = "validation_passed" if _response_value(response_payload, "valid") is True else "validation_failed_with_concrete_error"
        return NormalizedEvent(milestone, "productive", display_tool_name, True, str(response_payload))
    if display_tool_name == "sandbox_finalize.py":
        if rejected:
            return NormalizedEvent(None, "harmful", display_tool_name, True, str(response_payload))
        return NormalizedEvent("sandbox_finalized", "productive", display_tool_name, True, str(response_payload))
    if tool_name == "promote_sandbox_extraction":
        if rejected:
            return NormalizedEvent(None, "harmful", tool_name, True, str(response_payload))
        return NormalizedEvent("output_promoted", "productive", tool_name, True, str(response_payload))
    if tool_name == "query_jobs":
        count = response_payload.get("count")
        if isinstance(count, int) and count > 0:
            return NormalizedEvent("persisted_rows_verified", "productive", tool_name, True, str(response_payload))
        return NormalizedEvent(None, "neutral", tool_name, True, str(response_payload))
    if tool_name == "record_crawl_run" and _is_successful(response_payload):
        return NormalizedEvent("crawl_metadata_recorded", "productive", tool_name, True, str(response_payload))
    if tool_name == "final_answer":
        return NormalizedEvent(None, "harmful", tool_name, True, str(args))
    return None


def normalize_dashboard_events(events: Sequence[dict[str, object]]) -> list[NormalizedEvent]:
    normalized: list[NormalizedEvent] = []
    seen_neutral_evidence: set[str] = set()
    seen_collapsed_milestones: set[str] = set()
    persisted_rows_verified = False
    index = 0
    while index < len(events):
        item = events[index]
        kind = item.get("kind")
        if kind == "final_response":
            milestone = "final_answer_from_verified_state" if persisted_rows_verified else None
            label: SemanticLabel = "productive" if persisted_rows_verified else "harmful"
            normalized.append(
                NormalizedEvent(
                    milestone,
                    label,
                    None,
                    True,
                    str(item.get("payload") or {}),
                )
            )
            index += 1
            continue
        if kind != "function_call":
            index += 1
            continue

        tool_name = _event_tool_name(item)
        next_item = events[index + 1] if index + 1 < len(events) else None
        has_response = (
            next_item is not None
            and next_item.get("kind") == "function_response"
            and _event_tool_name(next_item) == tool_name
        )
        event = _normalize_tool_interaction(item, next_item if has_response else None)
        if event is not None:
            if event.milestone in COLLAPSE_DUPLICATE_MILESTONES:
                if event.milestone in seen_collapsed_milestones:
                    index += 2 if has_response else 1
                    continue
                seen_collapsed_milestones.add(event.milestone)
            if event.label == "neutral" and event.evidence in seen_neutral_evidence:
                event = NormalizedEvent(event.milestone, "detour", event.tool_name, event.effective, event.evidence)
            if event.label == "neutral":
                seen_neutral_evidence.add(event.evidence)
            if event.milestone == "persisted_rows_verified" and event.label == "productive":
                persisted_rows_verified = True
            normalized.append(event)
        index += 2 if has_response else 1
    return normalized
