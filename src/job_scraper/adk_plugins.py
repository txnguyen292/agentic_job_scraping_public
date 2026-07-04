from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from job_scraper.adk_plugin_modules.reasoning_telemetry import (
    MODEL_REASONING_TELEMETRY_STATE_KEY,
    ModelReasoningTelemetryPlugin,
    _attach_reasoning_telemetry_to_model_event,
    _llm_response_thought_texts,
    _model_reasoning_telemetry,
    _reasoning_telemetry_display_text,
    _surface_reasoning_telemetry_as_adk_web_thought,
)
from job_scraper.adk_plugin_modules.note_refinement import (
    DEFAULT_NOTE_REFINEMENT_MODEL,
    SANDBOX_NOTE_BUFFER_STATE_KEY,
    SANDBOX_NOTE_ERROR_STATE_KEY,
    SANDBOX_NOTES_STATE_KEY,
    SANDBOX_SUMMARIZED_COMMANDS_STATE_KEY,
    WORKFLOW_EVENT_GROUP,
    WORKFLOW_EVENT_SEQUENCE_STATE_KEY,
    WORKFLOW_SUMMARIZED_EVENTS_STATE_KEY,
    SandboxNoteRefinementPlugin,
)
from job_scraper.adk_plugin_modules.output_gate import SandboxOutputGatePlugin
from job_scraper.adk_plugin_modules.sandbox_guard import SandboxWorkflowGuardPlugin
from job_scraper.adk_plugin_modules.sandbox_guard.artifacts import (
    _add_versioned_artifact_handles_to_promotion,
    _collect_artifact_sources,
    _compact_artifact_handles,
    _compact_output_paths,
    _persist_artifact_sources,
    _record_sandbox_artifact_handles,
    _safe_adk_artifact_name,
    _versioned_artifact_handles_for_audit,
)
from job_scraper.adk_plugin_modules.sandbox_guard.compaction import (
    _compact_run_skill_script_result,
    _compact_sandbox_response,
    _completed_sandbox_audits,
    _completed_sandbox_placeholder,
    _is_workflow_note_tool,
    _latest_note_by_audit,
    _looks_like_sandbox_payload,
    _mark_summarized_commands,
    _mark_summarized_workflow_events,
    _prune_completed_sandbox_contexts,
    _prune_summarized_sandbox_contexts,
    _prune_summarized_workflow_events,
    _sandbox_command_note_source,
    _sandbox_command_sort_key,
    _summarized_sandbox_placeholder,
    _summarized_workflow_event_placeholder,
    _workflow_event_sort_key,
    _workflow_tool_event_note_source,
)
from job_scraper.adk_plugin_modules.transient_retry import (
    MODEL_RETRY_BASE_DELAY_SECONDS,
    MODEL_RETRY_MAX_ATTEMPTS,
    MODEL_RETRY_MAX_DELAY_SECONDS,
    TransientModelRetryPlugin,
    _is_transient_model_error,
    _model_retry_exhausted_response,
    _retry_delay_from_error,
)
from job_scraper.sandbox_terminal import SandboxRegistry
from job_scraper.runtime_state import SESSION_EXTRACTION_CONTEXT_STATE_KEY
from job_scraper.runtime_payload import (
    EXTRACTION_CONTEXT_DIGEST_KEYS,
    LATEST_PAYLOAD_KEYS,
    PROMOTED_SCRIPT_PAYLOAD_ERROR_KEYS,
    RESOURCE_PLACEHOLDER_KEYS,
    RESOURCE_TEXT_PAYLOAD_KEYS,
    SANDBOX_TEXT_PREVIEW_KEYS,
    SESSION_CONTEXT_COMPACT_KEYS,
    RuntimePayloadKey,
    RuntimeStatus,
    SessionContextKey,
)
from job_scraper.tool_policy import ToolActionKind, ToolName, resolve_tool_policy


ACTIVE_SANDBOX_STATE_KEY = "_job_scraper_active_sandbox"
LAST_PAGE_WORKSPACE_STATE_KEY = "_job_scraper_last_page_workspace"
SANDBOX_PENDING_SCRIPT_STATE_KEY = "_job_scraper_pending_sandbox_scripts"
SANDBOX_REPEAT_GUARD_STATE_KEY = "_job_scraper_repeat_guard"
SANDBOX_MODE_RESOURCE_STATE_KEY = "_job_scraper_sandbox_mode_resource"
SANDBOX_SITE_RESOURCE_STATE_KEY = "_job_scraper_sandbox_site_resources"
EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY = "_job_scraper_extraction_context_update_guard"
SANDBOX_TOOL_BUDGET_STATE_KEY = "_job_scraper_sandbox_tool_budget"
SANDBOX_READ_GUARD_STATE_KEY = "_job_scraper_sandbox_read_guard"
INSPECTION_REPEAT_GUARD_STATE_KEY = "_job_scraper_inspection_repeat_guard"
IMMEDIATE_ERROR_REPEAT_STATE_KEY = "_job_scraper_immediate_error_repeat"
SANDBOX_ARTIFACT_HANDLES_STATE_KEY = "_job_scraper_sandbox_artifact_handles"
FINALIZED_SANDBOX_PROMOTION_STATE_KEY = "_job_scraper_finalized_sandbox_promotion"
EPHEMERAL_RESOURCE_TOOL_NAMES = {ToolName.LOAD_SKILL, ToolName.LOAD_SKILL_RESOURCE}
INITIAL_CONTEXT_REQUIRED_TOOLS = {
    ToolName.LIST_SKILLS,
    ToolName.LOAD_SKILL,
    ToolName.FETCH_PAGE,
    ToolName.RENDER_PAGE,
    ToolName.FETCH_PAGE_TO_WORKSPACE,
    ToolName.RENDER_PAGE_TO_WORKSPACE,
    ToolName.LOAD_TEST_FIXTURE_PAGE_TO_WORKSPACE,
    ToolName.PROMOTE_SANDBOX_EXTRACTION,
    ToolName.UPSERT_JOB,
    ToolName.RECORD_CRAWL_RUN,
    ToolName.QUERY_JOBS,
    ToolName.LIST_SEED_REFERENCES,
}
DEFAULT_SANDBOX_APP_ROOT = Path(__file__).resolve().parent
MAX_WORKFLOW_SANDBOX_TOOL_CALLS = int(os.getenv("JOB_SCRAPER_MAX_WORKFLOW_SANDBOX_TOOL_CALLS", "20"))
SANDBOX_MODE_RESOURCES = {
    "references/diagnostic-mode.md": "diagnostic",
    "references/workflow-mode.md": "workflow",
}
SITE_SPECIFIC_WORKFLOW_REFERENCES = {
    "itviec.com": "references/itviec-listing-page.md",
}
SANDBOX_HOST_CONTROL_SCRIPTS = {
    "scripts/sandbox_start.py",
    "scripts/sandbox_read.py",
    "scripts/sandbox_write.py",
    "scripts/sandbox_write_file.py",
    "scripts/sandbox_apply_patch.py",
    "scripts/sandbox_progress.py",
    "scripts/sandbox_finalize.py",
    "scripts/validate_outputs.py",
}
REQUIRED_WORKFLOW_PROTOCOL_OUTPUTS = (
    "output/page_profile.json",
    "output/extraction_strategy.json",
    "output/extraction_run.json",
    "output/candidates.json",
    "output/validation.json",
    "output/final.json",
    "output/run_summary.md",
)
WORKFLOW_PRODUCER_PATH = "output/extractor.py"
REQUIRED_OBSERVED_FIELD_STATUSES = {
    "available",
    "observed",
    "required",
    "required_observed",
    "observed_required",
}
PLACEHOLDER_FIELD_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "null",
    "not available",
    "not found",
    "tbd",
    "to be determined",
    "unknown",
    "unavailable",
}


def _is_state_like(state: Any) -> bool:
    """Return true for ADK's delta-aware State and regular mutable mappings."""

    return (
        state is not None
        and callable(getattr(state, "get", None))
        and callable(getattr(state, "setdefault", None))
        and hasattr(state, "__setitem__")
    )


def _state_pop(state: Any, key: str, default: Any = None) -> Any:
    """Pop from dict-like state; ADK State lacks pop, so clear with None."""

    pop = getattr(state, "pop", None)
    if callable(pop):
        return pop(key, default)
    try:
        value = state.get(key, default)
        if key in state:
            state[key] = None
        return value
    except TypeError:
        return default


def _preview(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = "...[truncated]..."
    if max_chars <= len(marker):
        return text[:max_chars]
    head = (max_chars - len(marker)) // 2
    tail = max_chars - len(marker) - head
    return f"{text[:head]}{marker}{text[-tail:]}"


def _extract_audit_id(value: Any) -> str:
    if isinstance(value, dict):
        if "audit_id" in value:
            return str(value["audit_id"])
        for child in value.values():
            found = _extract_audit_id(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _extract_audit_id(child)
            if found:
                return found
    return ""


def _sandbox_repeat_guard_result(
    state: Any,
    tool_args: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    file_path = str(tool_args.get("file_path") or "")
    audit_id = str(payload.get("audit_id") or _extract_audit_id(payload) or "")
    if not audit_id:
        return None

    fingerprint = ""
    threshold = 0
    label = ""
    status = str(payload.get("status") or "")
    if file_path.endswith("sandbox_finalize.py") and status == "error":
        fingerprint = f"finalize:error:{audit_id}:{payload.get('error') or ''}"
        threshold = 2
        label = "same sandbox_finalize.py error"
    elif file_path.endswith("sandbox_write_file.py"):
        args = [str(item) for item in (tool_args.get("args") or [])]
        path = _option_value(args, "--path")
        content = _option_value(args, "--content")
        if path:
            if status == "success":
                fingerprint = f"write:success:{audit_id}:{path}:{_sha256_text(content)}"
                threshold = 3
                label = "same sandbox_write_file.py content"
            elif status == "error":
                fingerprint = (
                    f"write:error:{audit_id}:{path}:{payload.get('error_type') or ''}:"
                    f"{_sha256_text(content)}:{_sha256_json(payload.get('errors'))}"
                )
                threshold = 2
                label = "same sandbox_write_file.py validation error"

    if not fingerprint:
        return None
    repeat_state = state.setdefault(SANDBOX_REPEAT_GUARD_STATE_KEY, {})
    if not isinstance(repeat_state, dict):
        repeat_state = {}
        state[SANDBOX_REPEAT_GUARD_STATE_KEY] = repeat_state
    count = int(repeat_state.get(fingerprint) or 0) + 1
    repeat_state[fingerprint] = count
    if count < threshold:
        return None

    guarded = dict(payload)
    guarded["audit_id"] = audit_id
    guarded["guardrail"] = "repeated_sandbox_tool_result"
    guarded["error"] = f"Repeated {label} {count} times for audit {audit_id}."
    guarded["repeat_count"] = count
    guarded["original_status"] = status
    guarded["original_error"] = payload.get("error", "")
    guarded["file_path"] = file_path
    if file_path.endswith("sandbox_write_file.py") and status == "success":
        guarded["status"] = "error"
        guarded["terminal"] = False
        guarded["required_next"] = (
            "Stop rewriting the same successful sandbox file content. If required protocol outputs exist, run "
            "scripts/validate_outputs.py and then scripts/sandbox_finalize.py. If a concrete validation/finalization "
            "error is already active, load/cite the missing evidence or revise the accountable output before "
            "writing changed content."
        )
    else:
        guarded["status"] = "guardrail_triggered"
        guarded["terminal"] = True
    return guarded


def _immediate_repeated_error_policy_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    prior = state.get(IMMEDIATE_ERROR_REPEAT_STATE_KEY)
    if not isinstance(prior, dict):
        return None

    signature = _tool_invocation_signature(tool_name, tool_args)
    if prior.get("signature") != signature:
        _state_pop(state, IMMEDIATE_ERROR_REPEAT_STATE_KEY, None)
        return None

    previous_error_type = str(prior.get("error_type") or "")
    previous_error = str(prior.get("error") or "")
    return {
        "status": "error",
        "error_type": "immediate_repeated_tool_error",
        "guardrail": "same_tool_invocation_after_error",
        "terminal": False,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "previous_invocation": _tool_descriptor(tool_name, tool_args),
        "previous_error_type": previous_error_type,
        "previous_error": previous_error,
        "error": (
            "Immediate retry blocked because the immediately previous invocation used the exact same tool and "
            f"arguments and failed. previous_error_type={previous_error_type or 'unknown'}; "
            f"previous_error={_preview(previous_error or 'tool invocation returned an error', 500)}"
        ),
        "required_next": _immediate_repeat_required_next(
            previous_error_type=previous_error_type,
            previous_error=previous_error,
            tool_name=tool_name,
            tool_args=tool_args,
        ),
        "count": 0,
        "written_count": 0,
    }


def _immediate_repeat_required_next(
    *,
    previous_error_type: str,
    previous_error: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    error_text = f"{previous_error_type} {previous_error}".lower()
    descriptor = _tool_descriptor(tool_name, tool_args)
    if "script_not_found" in error_text or "script not found" in error_text:
        return (
            "Do not retry this exact tool invocation. The failure means the requested skill script/path was not "
            f"available for this skill: {json.dumps(descriptor, ensure_ascii=True, default=str)}. Use a different "
            "listed resource path or correct skill_name/file_path before retrying. If the correct path is unclear, "
            "inspect the loaded skill resources instead of guessing another identical call."
        )
    if "requires_audit_id" in error_text or "missing audit" in error_text:
        return (
            "Do not retry this exact tool invocation. Retry only after changing the arguments to include the "
            "required --audit-id and the script's required options."
        )
    return (
        "Do not retry this exact tool invocation. First do something that changes the failing condition: change "
        "the command or arguments, use a different listed skill resource/path, inspect the relevant file/resource, "
        "or update_extraction_context with the latest error and a revised planned_next_tool. The next call must "
        "not be identical to the failed invocation."
    )


def _repeated_inspection_policy_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if str(active.get("status") or "running") != "running":
        return None
    if not _is_repeat_guarded_inspection(tool_name, tool_args):
        return None

    prior = state.get(INSPECTION_REPEAT_GUARD_STATE_KEY)
    if not isinstance(prior, dict):
        return None
    audit_id = str(active.get("audit_id") or "")
    if str(prior.get("audit_id") or "") != audit_id:
        return None
    signature = _tool_invocation_signature(tool_name, tool_args)
    if prior.get("signature") != signature:
        return None

    return {
        "status": "error",
        "error_type": "repeated_inspection_policy",
        "guardrail": "same_inspection_without_progress",
        "terminal": False,
        "audit_id": audit_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "repeat_count": int(prior.get("repeat_count") or 1) + 1,
        "error": (
            "This exact read-only inspection already succeeded while the workflow sandbox was active, and no "
            "workflow-changing action happened afterward. Repeating the same inspection will not advance the task."
        ),
        "required_next": (
            "Use the previous inspection result to choose a progress action: load bounded evidence, write or patch "
            "a supporting script, revise accountable protocol outputs, validate outputs, finalize, "
            "promote, or update_extraction_context with new evidence and a different planned_next_tool. Do not call "
            "the same inspection again."
        ),
        "count": 0,
        "written_count": 0,
    }


def _planned_next_tool_policy_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    """Enforce the agent's own declared next tool while a workflow is active."""

    if tool_name == "update_extraction_context":
        return _planned_next_tool_update_policy_error(tool_args, tool_context)

    expected = _active_planned_next_tool(tool_context)
    if not expected:
        return None
    if _planned_next_tool_allows_intervening_inspection(tool_name, tool_args):
        return None
    if _planned_tool_matches(expected, tool_name, tool_args):
        return None

    return {
        "status": "error",
        "error_type": "planned_next_tool_policy",
        "guardrail": "next_tool_must_match_session_plan",
        "expected": expected,
        "actual": _tool_descriptor(tool_name, tool_args),
        "error": (
            "The session extraction context declares a planned_next_tool, but the requested tool call does "
            "not match it. Follow the planned tool call, or first call update_extraction_context with new "
            "evidence and a revised planned_next_tool."
        ),
        "required_next": (
            "Use the declared planned_next_tool exactly enough to satisfy tool_name, skill_name, file_path, "
            "and target/argument constraints. If the plan is stale, update_extraction_context must explain "
            "the new evidence and declare the replacement planned_next_tool before any other tool call."
        ),
        "count": 0,
        "written_count": 0,
    }


def _planned_next_tool_update_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if str(active.get("status") or "running") != "running":
        return None
    if not _context_update_requires_planned_tool(tool_args, active):
        return None

    planned = tool_args.get("planned_next_tool")
    if not isinstance(planned, dict) or not planned.get("tool_name"):
        return {
            "status": "error",
            "error_type": "planned_next_tool_policy",
            "guardrail": "repair_context_requires_planned_next_tool",
            "audit_id": str(active.get("audit_id") or tool_args.get("audit_id") or ""),
            "error": (
                "Repair context updates must declare planned_next_tool. The note must choose the most "
                "efficient next available tool call based on the latest error and evidence."
            ),
            "required_next": (
                "Call update_extraction_context again with planned_next_tool containing at least tool_name. "
                "For sandbox helper calls, include skill_name and file_path. For writes or patches, include "
                "target_paths such as [\"output/candidates.json\"] or [\"output/write_outputs.py\"]."
            ),
            "count": 0,
            "written_count": 0,
        }
    shape_error = _planned_tool_shape_error(planned, active)
    if shape_error:
        return shape_error
    return _repair_scope_planned_tool_error(tool_args, planned, active)


def _workflow_contract_policy_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    if tool_name == "update_extraction_context":
        return _workflow_contract_update_policy_error(tool_args, tool_context)
    # Preserve more specific argument errors, especially missing --audit-id,
    # so the model receives the directly actionable tool-usage correction.
    if _workflow_sandbox_helper_missing_audit_id(tool_name, tool_args, tool_context):
        return None
    if not _is_workflow_contract_required_tool(tool_name, tool_args, tool_context):
        return None
    if _has_workflow_contract(tool_context):
        return None
    return _workflow_contract_required_error(
        _active_repair_audit_id(tool_context),
        (
            "Workflow execution is blocked until session state declares required_outputs and workflow_contract. "
            "The agent should load the relevant workflow resources first, then write the contract into "
            "SESSION_EXTRACTION_CONTEXT before starting the workflow sandbox or writing/running producer scripts."
        ),
    )


def _immediate_goal_policy_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    if not _is_immediate_goal_required_tool(tool_name, tool_args, tool_context):
        return None

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if not isinstance(context, dict):
        return None

    audit_id = _active_repair_audit_id(tool_context)
    path = _immediate_goal_target_path(tool_args)
    validation_error = _immediate_goal_validation_error(context)
    if not validation_error:
        return None

    return {
        "status": "error",
        "error_type": "immediate_goal_policy",
        "guardrail": validation_error["guardrail"],
        "audit_id": audit_id,
        "path": path,
        "missing": validation_error["missing"],
        "error": validation_error["error"],
        "unsatisfied_requirements": [
            {
                "id": "immediate_goal_recorded_before_producer_scripting",
                "path": path,
                "missing": validation_error["missing"],
                "agent_responsibility": (
                    "Probe bounded page evidence, derive extraction_strategy from extraction_plan, and record the "
                    "current step with evidence, strategy, validation, and next script/probe objective before "
                    "writing or running producer code."
                ),
            }
        ],
        "required_next_tool": {
            "tool_name": "update_extraction_context",
            "extraction_plan": [
                "Establish the repeated job-card unit boundary before extracting fields.",
                "Use the validated boundary to extract canonical URLs and fields in later steps.",
            ],
            "extraction_strategy": {
                "status": "active",
                "derived_from": "extraction_plan plus representative repeated-card evidence",
                "target_units": "one repeated job-card unit per in-scope listing",
                "unit_boundary": "agent-chosen selector or structural boundary from probed evidence",
                "count_method": "bounded count probe over the chosen unit boundary",
                "known_exclusions": ["navigation and non-listing links"],
                "coverage_plan": "verify every repeated unit before field extraction",
                "revision_policy": "enhance with new field evidence; revise on validator contradiction",
            },
            "immediate_goal": (
                "Establish repeated job-card unit boundary for the fixed ITviec fixture. Evidence: fixed page "
                "artifact, representative repeated card markup/text, and bounded selector/count evidence. "
                "Strategy: target one repeated job-card unit per in-scope listing and exclude navigation/company "
                "preview links. Validation: run a bounded count probe and pass only when the count matches observed "
                "in-scope listing units. Next script objective: write the smallest probe that counts repeated job "
                "units and records the unit boundary."
            ),
        },
        "required_next": (
            "Call update_extraction_context with extraction_plan, extraction_strategy, and immediate_goal before "
            "producer scripting. The immediate_goal must be derived from evidence and include the current step, "
            "strategy, validation, and next script/probe objective. initial_plan alone is not enough to write or "
            "run output/extractor.py."
        ),
        "count": 0,
        "written_count": 0,
    }


def _is_immediate_goal_required_tool(tool_name: str, tool_args: dict[str, Any], tool_context: Any) -> bool:
    if tool_name != "run_skill_script" or tool_args.get("skill_name") != "sandbox-page-analyst":
        return False
    if not _has_active_workflow_sandbox(tool_context):
        return False
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path == "scripts/sandbox_write_file.py":
        return _sandbox_write_target_path(tool_args) == WORKFLOW_PRODUCER_PATH
    if file_path == "scripts/sandbox_apply_patch.py":
        return _sandbox_write_touches_producer(tool_args)
    return file_path == "scripts/sandbox_exec.py" and _sandbox_exec_runs_producer(tool_args)


def _immediate_goal_target_path(tool_args: dict[str, Any]) -> str:
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path == "scripts/sandbox_write_file.py":
        return _sandbox_write_target_path(tool_args) or WORKFLOW_PRODUCER_PATH
    return WORKFLOW_PRODUCER_PATH


def _immediate_goal_validation_error(context: Any) -> dict[str, Any] | None:
    if not isinstance(context, dict):
        return None
    immediate_goal = context.get("immediate_goal")
    if not isinstance(immediate_goal, str) or not immediate_goal.strip():
        return {
            "guardrail": "immediate_goal_required",
            "missing": "immediate_goal",
            "error": (
                "Producer scripting is blocked until SESSION_EXTRACTION_CONTEXT.immediate_goal exists. "
                "The agent must establish the current bounded step from evidence before writing or running producer code."
            ),
        }

    missing: list[str] = []
    if not _nonempty_sequence_or_text(context.get("extraction_plan")):
        missing.append("extraction_plan")
    extraction_strategy = context.get("extraction_strategy")
    if not isinstance(extraction_strategy, dict) or not extraction_strategy:
        missing.append("extraction_strategy")

    goal_text = immediate_goal.lower()
    if not _text_mentions_any(goal_text, ("evidence", "observed", "probe", "selector", "count", "artifact")):
        missing.append("evidence_detail")
    if not _text_mentions_any(goal_text, ("strategy", "target", "unit", "boundary", "method", "selector")):
        missing.append("strategy_detail")
    if not _text_mentions_any(goal_text, ("validation", "validate", "pass", "criteria", "check", "count probe")):
        missing.append("validation_detail")
    if not _text_mentions_any(goal_text, ("next script", "next probe", "objective", "write", "run", "smallest")):
        missing.append("next_script_objective")

    if not missing:
        return None

    guardrail = "immediate_goal_validation_strategy_required" if "validation_detail" in missing else "immediate_goal_incomplete"
    return {
        "guardrail": guardrail,
        "missing": missing,
        "error": (
            "Producer scripting is blocked because SESSION_EXTRACTION_CONTEXT.immediate_goal is incomplete. "
            f"Missing: {', '.join(missing)}."
        ),
    }


def _nonempty_sequence_or_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(bool(str(item).strip()) for item in value)
    return False


def _text_mentions_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _workflow_contract_update_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if "required_outputs" not in tool_args and "workflow_contract" not in tool_args:
        return None
    missing = _missing_workflow_contract_outputs(tool_args.get("required_outputs"), tool_args.get("workflow_contract"))
    if missing:
        return _workflow_contract_required_error(
            str(tool_args.get("audit_id") or _active_repair_audit_id(tool_context) or ""),
            "required_outputs/workflow_contract must include every required workflow protocol output.",
            {"missing_outputs": missing},
        )
    contract = tool_args.get("workflow_contract")
    if isinstance(contract, dict):
        # Workflow contracts are intentionally role-based. Older contracts may
        # still include a producer path, but the runtime must not force the LLM
        # extraction workflow back into one semantic producer script.
        pass
    return None


def _workflow_sandbox_helper_missing_audit_id(tool_name: str, tool_args: dict[str, Any], tool_context: Any) -> bool:
    if tool_name != "run_skill_script" or tool_args.get("skill_name") != "sandbox-page-analyst":
        return False
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path not in {
        "scripts/sandbox_exec.py",
        "scripts/sandbox_read.py",
        "scripts/sandbox_write_file.py",
        "scripts/validate_outputs.py",
        "scripts/sandbox_finalize.py",
    }:
        return False
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    return "--audit-id" not in args and _has_active_sandbox(tool_context)


def _is_workflow_contract_required_tool(tool_name: str, tool_args: dict[str, Any], tool_context: Any) -> bool:
    if tool_name != "run_skill_script" or tool_args.get("skill_name") != "sandbox-page-analyst":
        return False
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path == "scripts/sandbox_start.py":
        return _sandbox_start_mode(tool_args) == "workflow"
    if file_path == "scripts/sandbox_write_file.py":
        target_path = _sandbox_write_target_path(tool_args)
        if target_path == WORKFLOW_PRODUCER_PATH or target_path in REQUIRED_WORKFLOW_PROTOCOL_OUTPUTS:
            return _has_active_workflow_sandbox(tool_context)
    if file_path == "scripts/sandbox_exec.py" and _sandbox_exec_runs_producer(tool_args):
        return _has_active_workflow_sandbox(tool_context)
    if file_path in {"scripts/validate_outputs.py", "scripts/sandbox_finalize.py"}:
        return _has_active_workflow_sandbox(tool_context)
    return False


def _has_workflow_contract(tool_context: Any) -> bool:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return False
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if not isinstance(context, dict):
        return False
    return not _missing_workflow_contract_outputs(context.get("required_outputs"), context.get("workflow_contract"))


def _missing_workflow_contract_outputs(required_outputs: Any, workflow_contract: Any) -> list[str]:
    declared: set[str] = set()
    if isinstance(required_outputs, list):
        declared.update(_normalize_skill_path(str(item)) for item in required_outputs if str(item).strip())
    if isinstance(workflow_contract, dict):
        contract_outputs = workflow_contract.get("required_outputs")
        if isinstance(contract_outputs, list):
            declared.update(_normalize_skill_path(str(item)) for item in contract_outputs if str(item).strip())
    return [path for path in REQUIRED_WORKFLOW_PROTOCOL_OUTPUTS if path not in declared]


def _sandbox_start_mode(tool_args: dict[str, Any]) -> str:
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    return str(_option_value(args, "--mode") or "workflow")


def _sandbox_exec_runs_producer(tool_args: dict[str, Any]) -> bool:
    command = _sandbox_exec_command(tool_args).replace("/workspace/", "").replace("./", "")
    return f"python {WORKFLOW_PRODUCER_PATH}" in command or f"python3 {WORKFLOW_PRODUCER_PATH}" in command


def _compound_producer_verification_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_exec.py":
        return None
    if not _has_active_workflow_sandbox(tool_context) or not _sandbox_exec_runs_producer(tool_args):
        return None
    command = _sandbox_exec_command(tool_args)
    if not _is_compound_producer_verification_command(command):
        return None
    audit_id = _active_repair_audit_id(tool_context)
    return {
        "status": "error",
        "error_type": "workflow_execution_policy",
        "guardrail": "compound_producer_verification_command",
        "audit_id": audit_id,
        "terminal": False,
        "error": (
            "Do not combine output/extractor.py execution with compilation, inline Python inspection, file reads, "
            "or validator/finalizer work in one sandbox_exec command. A chained producer verification command can "
            "hit per-command or command-budget guardrails before the runtime can observe progress."
        ),
        "received_command": command,
        "required_next": (
            "Split this into short observable steps: first run exactly `python output/extractor.py` with "
            "scripts/sandbox_exec.py; then, if needed, run a separate bounded inspection command; then call "
            "scripts/validate_outputs.py with --audit-id; then call scripts/sandbox_finalize.py with --audit-id."
        ),
    }


def _is_compound_producer_verification_command(command: str) -> bool:
    normalized = command.replace("/workspace/", "").replace("./", "")
    if not re.search(r"\bpython3?\s+output/extractor\.py\b", normalized):
        return False
    if "py_compile" in normalized:
        return True
    if "<<" in normalized:
        return True
    if "&&" in normalized or "||" in normalized or ";" in normalized:
        return True
    if "\n" in normalized.strip():
        return True
    python_invocations = re.findall(r"\bpython3?\b", normalized)
    return len(python_invocations) > 1


def _has_active_workflow_sandbox(tool_context: Any) -> bool:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return False
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    return isinstance(active, dict) and str(active.get("mode") or "workflow") == "workflow" and str(active.get("status") or "running") == "running"


def _workflow_contract_required_error(
    audit_id: str,
    error: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error",
        "error_type": "workflow_contract_policy",
        "guardrail": "workflow_contract_required",
        "audit_id": audit_id,
        "terminal": False,
        "error": error,
        "required_outputs": list(REQUIRED_WORKFLOW_PROTOCOL_OUTPUTS),
        "required_next": (
            "Call update_extraction_context with required_outputs and workflow_contract before continuing. "
            "Use top-level required_outputs and workflow_contract.required_outputs with every required protocol "
            "path. workflow_contract should state that the agent chooses and owns the extraction method, supporting "
            "scripts may inspect/parse/extract/validate/serialize when recorded, and validation/finalization are the success gate. "
            "Do not put the paths only under keys such as must_create_in_one_pass."
        ),
        "count": 0,
        "written_count": 0,
    }
    if extra:
        payload.update(extra)
    return payload


def _context_update_requires_planned_tool(tool_args: dict[str, Any], active: dict[str, Any]) -> bool:
    if isinstance(active.get("last_repair_target"), dict):
        return True
    known_errors = tool_args.get("known_errors")
    if isinstance(known_errors, list) and any(str(item).strip() for item in known_errors):
        last_result = tool_args.get("last_result")
        if isinstance(last_result, dict):
            errorish_keys = {"error", "errors", "missing_files", "finalize_error", "validation_error", "guardrail"}
            if any(key in last_result for key in errorish_keys):
                return True
    return False


def _planned_tool_shape_error(planned: dict[str, Any], active: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(planned.get("tool_name") or "")
    if tool_name != "run_skill_script":
        return None
    if planned.get("skill_name") != "sandbox-page-analyst" or not planned.get("file_path"):
        return {
            "status": "error",
            "error_type": "planned_next_tool_policy",
            "guardrail": "invalid_planned_next_tool_shape",
            "audit_id": str(active.get("audit_id") or ""),
            "error": (
                "planned_next_tool for run_skill_script must include skill_name \"sandbox-page-analyst\" "
                "and the exact sandbox helper file_path."
            ),
            "required_next": (
                "Rewrite planned_next_tool with tool_name \"run_skill_script\", skill_name "
                "\"sandbox-page-analyst\", and file_path such as \"scripts/sandbox_apply_patch.py\", "
                "\"scripts/sandbox_exec.py\", \"scripts/validate_outputs.py\", or \"scripts/sandbox_finalize.py\"."
            ),
            "count": 0,
            "written_count": 0,
        }
    return None


def _repair_scope_planned_tool_error(
    tool_args: dict[str, Any],
    planned: dict[str, Any],
    active: dict[str, Any],
) -> dict[str, Any] | None:
    repair_scope = tool_args.get("repair_scope")
    if not isinstance(repair_scope, dict):
        return None
    status = str(repair_scope.get("status") or "")
    verification = _repair_scope_verification_command(repair_scope)
    if status not in {"ready_to_verify", "verifying"} or not verification:
        return None
    if planned.get("tool_name") != "run_skill_script" or planned.get("file_path") != "scripts/sandbox_exec.py":
        return {
            "status": "error",
            "error_type": "repair_scope_policy",
            "guardrail": "repair_scope_verification_plan_required",
            "audit_id": str(active.get("audit_id") or tool_args.get("audit_id") or ""),
            "error": (
                "repair_scope.status is ready_to_verify/verifying, so planned_next_tool must be the sandbox "
                "execution helper that runs the declared verification command."
            ),
            "required_next": (
                "Rewrite planned_next_tool as run_skill_script with skill_name sandbox-page-analyst, "
                "file_path scripts/sandbox_exec.py, and args_must_include containing the repair_scope.verification command."
            ),
        }
    required_args = planned.get("args_must_include")
    planned_args = planned.get("args")
    has_verification_constraint = (
        isinstance(required_args, list)
        and verification in {str(item) for item in required_args}
    ) or (
        isinstance(planned_args, list)
        and verification in " ".join(str(item) for item in planned_args)
    )
    if not has_verification_constraint:
        return {
            "status": "error",
            "error_type": "repair_scope_policy",
            "guardrail": "repair_scope_verification_args_required",
            "audit_id": str(active.get("audit_id") or tool_args.get("audit_id") or ""),
            "error": (
                "planned_next_tool for a ready-to-verify repair scope must include either args containing the "
                f"declared verification command or args_must_include with that command: {verification}"
            ),
        }
    return None


def _active_planned_next_tool(tool_context: Any) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if not isinstance(context, dict):
        return None
    planned = context.get("planned_next_tool")
    if not isinstance(planned, dict) or not planned.get("tool_name"):
        return None
    return planned


def _planned_tool_matches(expected: dict[str, Any], tool_name: str, tool_args: dict[str, Any]) -> bool:
    normalized_tool_name = _normalize_tool_name(tool_name)
    if _normalize_tool_name(str(expected.get("tool_name") or "")) != normalized_tool_name:
        return False

    if (
        "skill_name" in expected
        and (normalized_tool_name in {"load_skill", "run_skill_script"} or "skill_name" in tool_args)
        and str(expected.get("skill_name") or "") != str(tool_args.get("skill_name") or "")
    ):
        return False

    if "file_path" in expected:
        expected_path = _normalize_skill_path(str(expected.get("file_path") or ""))
        actual_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
        if expected_path != actual_path:
            return False

    target_paths = expected.get("target_paths")
    if isinstance(target_paths, list) and target_paths:
        serialized_args = json.dumps(tool_args.get("args") or [], ensure_ascii=False, sort_keys=True, default=str)
        for target_path in target_paths:
            if _normalize_skill_path(str(target_path)) not in serialized_args:
                return False

    required_args = expected.get("args_must_include")
    if isinstance(required_args, list) and required_args:
        serialized_tool_args = json.dumps(tool_args, ensure_ascii=False, sort_keys=True, default=str)
        for required in required_args:
            if str(required) not in serialized_tool_args:
                return False

    return True


def _normalize_tool_name(tool_name: str) -> str:
    normalized = tool_name.strip()
    for prefix in ("functions.", "tools."):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _planned_next_tool_allows_intervening_inspection(tool_name: str, tool_args: dict[str, Any]) -> bool:
    """Allow read-only inspection without consuming or changing the declared next action."""

    policy = resolve_tool_policy(tool_name, tool_args)
    if policy.kind in {
        ToolActionKind.REFERENCE_READ,
        ToolActionKind.WORKSPACE_READ,
        ToolActionKind.SANDBOX_READ,
    }:
        return True
    if policy.kind == ToolActionKind.SANDBOX_EXEC:
        return _sandbox_exec_is_read_only_probe(tool_args)
    return False


def _sandbox_exec_is_read_only_probe(tool_args: dict[str, Any]) -> bool:
    """Best-effort classifier for sandbox_exec commands that only inspect evidence."""

    command = _sandbox_exec_command(tool_args)
    if not command.strip():
        return False
    lowered = command.lower()
    write_markers = (
        ".write_text(",
        ".write_bytes(",
        "open(",
        "json.dump(",
        "pickle.dump(",
        "shutil.",
        "mkdir",
        "touch ",
        "rm ",
        "mv ",
        "cp ",
        "tee ",
        "sed -i",
        "cat >",
        ">>",
    )
    if any(marker in lowered for marker in write_markers):
        return False
    if re.search(r"(?<!<)>(?!>)", command):
        return False
    read_markers = (
        "print(",
        "read_text(",
        "read_bytes(",
        "beautifulsoup",
        "select(",
        "find_all(",
        "sed -n",
        "ls ",
        "find ",
        "grep ",
        "rg ",
        "jq ",
    )
    return any(marker in lowered for marker in read_markers)


def _repair_scope_policy_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    if tool_name == "update_extraction_context":
        return _repair_scope_update_policy_error(tool_args, tool_context)

    scope = _active_repair_scope(tool_context)
    if not scope:
        return None
    status = str(scope.get("status") or "")
    if status not in {"patching", "ready_to_verify", "verifying"}:
        return None

    if tool_name == "load_skill_resource":
        return _repair_scope_resource_error(tool_args, tool_context, scope)
    if tool_name != "run_skill_script" or tool_args.get("skill_name") != "sandbox-page-analyst":
        return None

    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path == "scripts/sandbox_read.py":
        return None
    if file_path == "scripts/sandbox_apply_patch.py":
        return _repair_scope_patch_error(tool_args, tool_context, scope)
    if file_path == "scripts/sandbox_exec.py":
        return _repair_scope_exec_error(tool_args, tool_context, scope)
    return None


def _repair_scope_update_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    repair_scope = tool_args.get("repair_scope")
    if repair_scope is None:
        return None
    state = getattr(tool_context, "state", None)
    active = state.get(ACTIVE_SANDBOX_STATE_KEY) if _is_state_like(state) else None
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if str(active.get("status") or "running") != "running":
        return None
    audit_id = str(tool_args.get("audit_id") or (active.get("audit_id") if isinstance(active, dict) else "") or "")
    if not isinstance(repair_scope, dict):
        return _repair_scope_error(
            audit_id,
            "invalid_repair_scope_shape",
            "repair_scope must be a JSON object.",
        )
    status = str(repair_scope.get("status") or "")
    if status and status not in {"patching", "ready_to_verify", "verifying", "blocked"}:
        return _repair_scope_error(
            audit_id,
            "invalid_repair_scope_status",
            "repair_scope.status must be one of patching, ready_to_verify, verifying, or blocked.",
        )
    if status in {"patching", "ready_to_verify", "verifying"} and not str(repair_scope.get("objective") or "").strip():
        return _repair_scope_error(
            audit_id,
            "repair_scope_objective_required",
            "Active repair_scope updates must include a concise objective.",
        )
    if status in {"ready_to_verify", "verifying"} and not _repair_scope_verification_command(repair_scope):
        return _repair_scope_error(
            audit_id,
            "repair_scope_verification_required",
            (
                "repair_scope.status ready_to_verify/verifying requires a concrete verification command. "
                "Use repair_scope.verification; repair_scope.verification_command is accepted as a compatibility alias."
            ),
        )
    verification = _repair_scope_verification_command(repair_scope)
    if verification and _is_compound_producer_verification_command(verification):
        return _repair_scope_error(
            audit_id,
            "compound_repair_scope_verification_command",
            (
                "repair_scope.verification must be one short observable command. For output/extractor.py repairs, "
                "set verification to exactly `python output/extractor.py`; inspect generated files or run "
                "validator/finalizer in later tool calls."
            ),
            {"verification": verification},
        )
    return None


def _repair_scope_resource_error(tool_args: dict[str, Any], tool_context: Any, scope: dict[str, Any]) -> dict[str, Any] | None:
    allowed = _normalized_scope_paths(scope.get("allowed_resources"))
    if not allowed:
        return None
    requested = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if requested in allowed:
        return None
    return _repair_scope_error(
        _active_repair_audit_id(tool_context),
        "repair_scope_resource_not_allowed",
        (
            f"Resource {requested or '<unknown>'} is outside the active repair_scope.allowed_resources. "
            "Update repair_scope first if new evidence proves this resource is needed."
        ),
        {"requested_resource": requested, "allowed_resources": sorted(allowed)},
    )


def _repair_scope_patch_error(
    tool_args: dict[str, Any],
    tool_context: Any,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    if _tool_args_request_help(tool_args):
        return None
    allowed = _normalized_scope_paths(scope.get("files"))
    if not allowed:
        return None
    targets = _sandbox_patch_target_paths(tool_args)
    if targets and targets.issubset(allowed):
        return None
    if not targets and _serialized_args_contain_any_path(tool_args, allowed):
        return None
    return _repair_scope_error(
        _active_repair_audit_id(tool_context),
        "repair_scope_patch_target_not_allowed",
        (
            "sandbox_apply_patch.py target paths must stay inside repair_scope.files. "
            "Update repair_scope first if the repair genuinely needs a different file."
        ),
        {"target_paths": sorted(targets), "allowed_files": sorted(allowed)},
    )


def _tool_args_request_help(tool_args: dict[str, Any]) -> bool:
    return "--help" in {str(item) for item in (tool_args.get("args") or []) if item is not None}


def _repair_scope_exec_error(
    tool_args: dict[str, Any],
    tool_context: Any,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    status = str(scope.get("status") or "")
    verification = _repair_scope_verification_command(scope)
    if status not in {"ready_to_verify", "verifying"} or not verification:
        return None
    command = _sandbox_exec_command(tool_args)
    if verification in command:
        return None
    return _repair_scope_error(
        _active_repair_audit_id(tool_context),
        "repair_scope_verification_command_required",
        (
            "The active repair_scope is ready_to_verify, so sandbox_exec.py must run the declared verification "
            f"command before more exploratory commands: {verification}"
        ),
        {"actual_command": command, "verification": verification},
    )


def _repair_scope_verification_command(repair_scope: dict[str, Any]) -> str:
    """Return the declared verification command, accepting the legacy key used in prompts."""

    return str(repair_scope.get("verification") or repair_scope.get("verification_command") or "").strip()


def _active_repair_scope(tool_context: Any) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if str(active.get("status") or "running") != "running":
        return None
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if not isinstance(context, dict):
        return None
    scope = context.get("repair_scope")
    return scope if isinstance(scope, dict) else None


def _active_repair_audit_id(tool_context: Any) -> str:
    state = getattr(tool_context, "state", None)
    active = state.get(ACTIVE_SANDBOX_STATE_KEY) if _is_state_like(state) else None
    if isinstance(active, dict):
        return str(active.get("audit_id") or "")
    return ""


def _normalized_scope_paths(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {_normalize_skill_path(str(item)) for item in value if str(item).strip()}


def _sandbox_patch_target_paths(tool_args: dict[str, Any]) -> set[str]:
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    targets: set[str] = set()
    path = _option_value(args, "--path")
    if path:
        targets.add(_normalize_skill_path(path))
    patch = _option_value(args, "--patch")
    if patch:
        targets.update(_patch_target_paths(patch))
    return targets


def _patch_target_paths(patch: str) -> set[str]:
    targets: set[str] = set()
    for line in patch.splitlines():
        stripped = line.strip()
        for prefix in ("*** Update File: ", "*** Add File: ", "--- a/", "+++ b/"):
            if stripped.startswith(prefix):
                path = stripped[len(prefix) :].strip()
                if path and path != "/dev/null":
                    targets.add(_normalize_skill_path(path))
    return targets


def _serialized_args_contain_any_path(tool_args: dict[str, Any], paths: set[str]) -> bool:
    serialized = json.dumps(tool_args.get("args") or [], ensure_ascii=False, sort_keys=True, default=str)
    return any(path in serialized for path in paths)


def _repair_scope_error(
    audit_id: str,
    guardrail: str,
    error: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error",
        "error_type": "repair_scope_policy",
        "guardrail": guardrail,
        "audit_id": audit_id,
        "terminal": False,
        "error": error,
        "required_next": (
            "Use the current SESSION_EXTRACTION_CONTEXT as the work order. Either execute inside the declared "
            "repair_scope, or update_extraction_context with new evidence and a revised repair_scope before "
            "changing direction."
        ),
        "count": 0,
        "written_count": 0,
    }
    if extra:
        payload.update(extra)
    return payload


def _tool_descriptor(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    descriptor: dict[str, Any] = {"tool_name": tool_name}
    for key in ("skill_name", "file_path"):
        if key in tool_args:
            descriptor[key] = tool_args[key]
    args = tool_args.get("args")
    if isinstance(args, list):
        descriptor["args_preview"] = [_preview(str(item), 160) for item in args[:8]]
    return descriptor


def _clear_satisfied_planned_next_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
    result: dict[str, Any],
) -> None:
    expected = _active_planned_next_tool(tool_context)
    if not expected or not _planned_tool_matches(expected, tool_name, tool_args):
        return
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if isinstance(context, dict):
        context.pop("planned_next_tool", None)
        state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = context


def _record_immediate_tool_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
    result: dict[str, Any],
) -> None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return

    error_payload = _tool_result_error_payload(tool_name, result)
    if not error_payload:
        _state_pop(state, IMMEDIATE_ERROR_REPEAT_STATE_KEY, None)
        return

    state[IMMEDIATE_ERROR_REPEAT_STATE_KEY] = {
        "signature": _tool_invocation_signature(tool_name, tool_args),
        "tool_name": tool_name,
        "tool_args": tool_args,
        "error_type": str(error_payload.get("error_type") or error_payload.get("guardrail") or ""),
        "error": _preview(
            str(
                error_payload.get("error")
                or error_payload.get("stderr")
                or error_payload.get("message")
                or "tool invocation returned an error"
            ),
            1_000,
        ),
    }


def _record_or_reset_repeated_inspection(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
    result: dict[str, Any],
) -> None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        _state_pop(state, INSPECTION_REPEAT_GUARD_STATE_KEY, None)
        return
    if str(active.get("status") or "running") != "running":
        _state_pop(state, INSPECTION_REPEAT_GUARD_STATE_KEY, None)
        return

    if _tool_result_error_payload(tool_name, result):
        return

    if not _is_repeat_guarded_inspection(tool_name, tool_args):
        if resolve_tool_policy(tool_name, tool_args).counts_as_intervening_action:
            _state_pop(state, INSPECTION_REPEAT_GUARD_STATE_KEY, None)
        return

    audit_id = str(active.get("audit_id") or "")
    signature = _tool_invocation_signature(tool_name, tool_args)
    prior = state.get(INSPECTION_REPEAT_GUARD_STATE_KEY)
    repeat_count = 1
    if (
        isinstance(prior, dict)
        and str(prior.get("audit_id") or "") == audit_id
        and prior.get("signature") == signature
    ):
        repeat_count = int(prior.get("repeat_count") or 1) + 1

    state[INSPECTION_REPEAT_GUARD_STATE_KEY] = {
        "audit_id": audit_id,
        "signature": signature,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "repeat_count": repeat_count,
    }


def _is_repeat_guarded_inspection(tool_name: str, tool_args: dict[str, Any]) -> bool:
    policy = resolve_tool_policy(tool_name, tool_args)
    return policy.kind in {
        ToolActionKind.REFERENCE_READ,
        ToolActionKind.WORKSPACE_READ,
        ToolActionKind.SANDBOX_READ,
    }


def _tool_result_error_payload(tool_name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    candidates = [result]
    if tool_name == "run_skill_script":
        parsed_stdout = _parse_skill_script_stdout(result)
        if parsed_stdout is not result:
            candidates.insert(0, parsed_stdout)

    for candidate in candidates:
        status = str(candidate.get("status") or "").lower()
        if status in {"error", "blocked", "guardrail_triggered"}:
            return candidate
        if candidate.get("error") or candidate.get("error_type"):
            return candidate
    return None


def _tool_invocation_signature(tool_name: str, tool_args: dict[str, Any]) -> str:
    return _sha256_json({"tool_name": tool_name, "tool_args": tool_args})


def _sandbox_mode_resource_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    mode = SANDBOX_MODE_RESOURCES.get(file_path)
    if not mode:
        return None

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    loaded = state.get(SANDBOX_MODE_RESOURCE_STATE_KEY)
    if isinstance(loaded, dict) and loaded.get("file_path"):
        return {
            "status": "error",
            "error_type": "sandbox_mode_resource_policy",
            "guardrail": "single_mode_resource",
            "requested_resource": file_path,
            "requested_mode": mode,
            "loaded_resource": loaded.get("file_path"),
            "loaded_mode": loaded.get("mode"),
            "error": (
                "Only one sandbox mode reference may be loaded for a sandbox task. "
                f"Already loaded {loaded.get('file_path')}; refused {file_path}."
            ),
        }
    state[SANDBOX_MODE_RESOURCE_STATE_KEY] = {"file_path": file_path, "mode": mode}
    return None


def _record_sandbox_site_resource_load(tool_args: dict[str, Any], tool_context: Any) -> None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path not in SITE_SPECIFIC_WORKFLOW_REFERENCES.values():
        return
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return
    loaded = state.get(SANDBOX_SITE_RESOURCE_STATE_KEY)
    resources = list(loaded) if isinstance(loaded, list) else []
    if file_path not in resources:
        resources.append(file_path)
    state[SANDBOX_SITE_RESOURCE_STATE_KEY] = resources


def _site_specific_reference_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_start.py" or _sandbox_start_mode(tool_args) != "workflow":
        return None
    source_url = _sandbox_start_source_url(tool_args, tool_context)
    required_reference = _required_site_reference(source_url)
    if not required_reference:
        return None
    state = getattr(tool_context, "state", None)
    loaded = state.get(SANDBOX_SITE_RESOURCE_STATE_KEY) if _is_state_like(state) else None
    if isinstance(loaded, list) and required_reference in loaded:
        return None
    return {
        "status": "error",
        "error_type": "site_reference_policy",
        "guardrail": "site_specific_reference_required",
        "source_url": source_url,
        "required_reference": required_reference,
        "error": (
            f"Workflow sandbox start is blocked for {source_url} until the matching site reference is loaded."
        ),
        "required_next": (
            'Call load_skill_resource with skill_name "sandbox-page-analyst" and '
            f'file_path "{required_reference}", then update_extraction_context with the site-specific extraction cues '
            "before retrying sandbox_start.py."
        ),
        "count": 0,
        "written_count": 0,
    }


def _sandbox_start_source_url(tool_args: dict[str, Any], tool_context: Any) -> str:
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    source_url = str(_option_value(args, "--source-url") or "")
    if source_url:
        return source_url
    state = getattr(tool_context, "state", None)
    page = state.get(LAST_PAGE_WORKSPACE_STATE_KEY) if _is_state_like(state) else None
    if isinstance(page, dict):
        return str(page.get("url") or "")
    return ""


def _required_site_reference(source_url: str) -> str:
    lowered = source_url.lower()
    for marker, reference in SITE_SPECIFIC_WORKFLOW_REFERENCES.items():
        if marker in lowered:
            return reference
    return ""


def _sandbox_host_control_exec_policy_error(tool_args: dict[str, Any]) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_exec.py":
        return None
    command = _sandbox_exec_command(tool_args)
    if not command:
        return None

    normalized_command = command.replace("./", "")
    blocked = sorted(script for script in SANDBOX_HOST_CONTROL_SCRIPTS if script in normalized_command)
    if not blocked:
        return None
    return {
        "status": "error",
        "error_type": "sandbox_host_control_script_policy",
        "guardrail": "host_control_script_inside_sandbox_exec",
        "blocked_scripts": blocked,
        "error": (
            "Host-control sandbox scripts must be invoked with run_skill_script, not inside sandbox_exec.py. "
            "Use sandbox_exec.py only for shell inspection commands, parser checks, running output/extractor.py, "
            "and in-sandbox validation commands."
        ),
    }


def _wrong_sandbox_helper_skill_policy_error(tool_args: dict[str, Any]) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-extraction-debugger":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if not file_path.startswith("scripts/"):
        return None
    return {
        "status": "error",
        "error_type": "sandbox_helper_skill_policy",
        "guardrail": "sandbox_helpers_live_under_page_analyst_skill",
        "requested_skill_name": "sandbox-extraction-debugger",
        "file_path": file_path,
        "count": 0,
        "written_count": 0,
        "error": (
            "sandbox-extraction-debugger is an instruction skill, not the owner of sandbox helper scripts. "
            f"{file_path} must be invoked through the sandbox-page-analyst skill."
        ),
        "required_next": (
            'Retry the helper call with tool_name "run_skill_script", skill_name "sandbox-page-analyst", '
            f'and file_path "{file_path}". Keep using the sandbox-extraction-debugger instructions to decide '
            "what to inspect or patch."
        ),
    }


def _sandbox_skill_script_args_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    if _is_skill_script_help_request(args):
        return None

    audit_required = {
        "scripts/sandbox_exec.py",
        "scripts/sandbox_read.py",
        "scripts/sandbox_write_file.py",
        "scripts/validate_outputs.py",
        "scripts/sandbox_finalize.py",
    }
    if file_path in audit_required and "--audit-id" not in args and _has_active_sandbox(tool_context):
        audit_id = _active_repair_audit_id(tool_context)
        return {
            "status": "error",
            "error_type": "sandbox_script_args_policy",
            "guardrail": "sandbox_script_requires_audit_id",
            "audit_id": audit_id,
            "file_path": file_path,
            "error": f"{file_path} must include `--audit-id <audit_id>` while a sandbox workflow is active.",
            "required_next": _missing_audit_id_required_next(file_path, audit_id),
            "count": 0,
            "written_count": 0,
        }

    if file_path == "scripts/sandbox_write_file.py":
        path = _option_value(args, "--path")
        content = _option_value(args, "--content")
        if not path or content is None:
            audit_id = _active_repair_audit_id(tool_context)
            return {
                "status": "error",
                "error_type": "sandbox_script_args_policy",
                "guardrail": "sandbox_write_file_requires_path_and_content",
                "audit_id": audit_id,
                "file_path": file_path,
                "error": "sandbox_write_file.py requires `--path <workspace-relative path>` and `--content <file text>`; positional path-only args are not enough.",
                "required_next": (
                    f"Retry with args like `--audit-id {audit_id} --path output/extractor.py --content <python source>`. "
                    "If you need exact options, call scripts/sandbox_write_file.py with `--help` first."
                ),
                "count": 0,
                "written_count": 0,
            }

    if file_path == "scripts/sandbox_exec.py" and "--" in args:
        return {
            "status": "error",
            "error_type": "sandbox_script_args_policy",
            "guardrail": "sandbox_exec_requires_cmd_argument",
            "error": "sandbox_exec.py does not accept pass-through command args after `--` in ADK skill-script calls.",
            "required_next": (
                "Retry the same sandbox command with run_skill_script skill_name \"sandbox-page-analyst\", "
                "file_path \"scripts/sandbox_exec.py\", and args including `--audit-id <audit_id> --cmd \"<shell command>\"`."
            ),
            "count": 0,
            "written_count": 0,
        }

    if file_path == "scripts/sandbox_read.py" and "--max-bytes" in args:
        return {
            "status": "error",
            "error_type": "sandbox_script_args_policy",
            "guardrail": "sandbox_read_uses_max_chars",
            "error": "sandbox_read.py uses `--max-chars`, not `--max-bytes`.",
            "required_next": (
                "Retry the read with `--max-chars <N>` or omit the size flag for the default bounded preview."
            ),
            "count": 0,
            "written_count": 0,
        }

    return None


def _missing_protocol_output_read_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_read.py":
        return None

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    repair = active.get("last_repair_target")
    if not isinstance(repair, dict):
        return None

    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    read_path = _normalize_skill_path(_option_value(args, "--path"))
    if not read_path:
        return None
    error_text = str(repair.get("error") or "")
    if "missing required" not in error_text.lower() or read_path not in _missing_protocol_paths_from_error(error_text):
        return None

    audit_id = str(active.get("audit_id") or _option_value(args, "--audit-id") or "")
    return {
        "status": "error",
        "error_type": "missing_protocol_output_read_policy",
        "guardrail": "repair_missing_protocol_output_at_producer",
        "audit_id": audit_id,
        "path": read_path,
        "error": (
            f"{read_path} is already known to be missing from the latest validation/finalization error. "
            "Reading it again cannot repair the workflow."
        ),
        "required_next": (
            "Load `sandbox-extraction-debugger` if not loaded, inspect the evidence index/chunks, current "
            "protocol outputs, run record, script manifest, and any serialization helper. Then create or repair "
            f"the missing accountable protocol file `{read_path}` from inspected evidence/script output, with field rationale and evidence refs where "
            "applicable, before validate/finalize."
        ),
        "count": 0,
        "written_count": 0,
    }


def _missing_protocol_paths_from_error(error_text: str) -> set[str]:
    return {
        _normalize_skill_path(match)
        for match in re.findall(
            r"output/[A-Za-z0-9_.-]+\.json",
            error_text,
        )
    }


def _has_active_sandbox(tool_context: Any) -> bool:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return False
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    return isinstance(active, dict) and bool(active.get("audit_id")) and str(active.get("status") or "running") == "running"


def _workflow_protocol_write_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_write_file.py":
        return None

    target_path = _sandbox_write_target_path(tool_args)
    required_protocol_paths = {
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
    }
    if target_path not in required_protocol_paths:
        return None

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None

    return _expected_output_protocol_write_policy_error(tool_args, tool_context, target_path, active)


def _workflow_output_plan_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_write_file.py":
        return None

    target_path = _sandbox_write_target_path(tool_args)
    if not _requires_producer_output_plan(target_path):
        return None

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if not isinstance(context, dict):
        return None
    if _has_producer_output_plan(context):
        return None

    audit_id = str(active.get("audit_id") or context.get("audit_id") or "")
    has_contract = isinstance(context.get("output_contract"), dict) and bool(context.get("output_contract"))
    required_next_tool: dict[str, Any]
    if has_contract:
        required_next_tool = {
            "tool_name": "update_extraction_context",
            "producer_output_plan": {
                "required_outputs": list(REQUIRED_WORKFLOW_PROTOCOL_OUTPUTS),
                "extraction_run": {
                    "required": ["observations", "chosen_strategy", "expected_output"],
                },
                "candidates_json": {
                    "required_top_level": ["source", "jobs", "selectors", "crawl", "warnings"],
                },
                "final_json": {
                    "required_top_level": ["status", "output_schema", "summary", "result"],
                },
                "script_manifest": {
                    "required_if_supporting_scripts_authored": True,
                    "requires_workflow_or_reference_version": True,
                    "requires_reuse_classification": True,
                },
                "validation_plan": [
                    "run scripts/validate_outputs.py",
                    "run scripts/sandbox_finalize.py after validation succeeds",
                ],
            },
        }
    else:
        required_next_tool = {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/protocol_contract.py",
            "args": [],
        }
    return {
        "status": "error",
        "error_type": "producer_output_plan_policy",
        "guardrail": "producer_output_plan_required",
        "audit_id": audit_id,
        "path": target_path,
        "error": (
            f"Before writing {target_path}, load the compact protocol contract and have the agent record "
            "producer_output_plan in SESSION_EXTRACTION_CONTEXT. This prevents discovering required output "
            "fields one validator error at a time."
        ),
        "unsatisfied_requirements": [
            {
                "id": "producer_output_plan_recorded_before_authoring_outputs",
                "path": target_path,
                "missing": "output_contract and producer_output_plan",
                "agent_responsibility": (
                    "Use scripts/protocol_contract.py as compact contract input, then write the agent's own "
                    "plan for extraction_run, candidates/final envelopes, script manifest, and validation."
                ),
            }
        ],
        "required_next_tool": required_next_tool,
        "required_next": (
            "If output_contract is missing, call scripts/protocol_contract.py. Then call update_extraction_context "
            "with output_contract and a producer_output_plan that the agent derives from the contract and current "
            "observations. Do not retry the write until producer_output_plan is present."
        ),
        "count": 0,
        "written_count": 0,
    }


def _requires_producer_output_plan(target_path: str) -> bool:
    normalized = _normalize_skill_path(target_path)
    if normalized in REQUIRED_WORKFLOW_PROTOCOL_OUTPUTS:
        return True
    if normalized == "output/script_manifest.json":
        return True
    return normalized.startswith("output/") and normalized.endswith(".py")


def _has_producer_output_plan(context: dict[str, Any]) -> bool:
    return bool(isinstance(context.get("output_contract"), dict) and context.get("output_contract")) and bool(
        isinstance(context.get("producer_output_plan"), dict) and context.get("producer_output_plan")
    )


def _expected_output_protocol_write_policy_error(
    tool_args: dict[str, Any],
    tool_context: Any,
    target_path: str,
    active: dict[str, Any],
) -> dict[str, Any] | None:
    if target_path not in {"output/candidates.json", "output/final.json"}:
        return None

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if not isinstance(context, dict):
        return None
    payload = _sandbox_write_json_payload(tool_args)
    if not isinstance(payload, dict):
        return None
    if _payload_declares_non_success_review(payload):
        return None

    expected_output = context.get("expected_output")
    if not isinstance(expected_output, dict):
        return _expected_output_required_error(target_path, active, context)
    expected_job_count = _expected_output_job_count(expected_output)
    if expected_job_count is None:
        return _expected_output_required_error(target_path, active, context)
    explanation_error = _expected_output_count_explanation_error(
        target_path=target_path,
        active=active,
        context=context,
        expected_output=expected_output,
    )
    if explanation_error:
        return explanation_error
    actual_job_count = _protocol_payload_job_count(payload)
    if actual_job_count is None or actual_job_count == expected_job_count:
        return _expected_output_field_availability_error(
            target_path=target_path,
            active=active,
            context=context,
            expected_output=expected_output,
            payload=payload,
        )

    audit_id = str(active.get("audit_id") or context.get("audit_id") or "")
    count_basis = str(expected_output.get("count_basis") or expected_output.get("basis") or "").strip()
    unsatisfied = _expected_output_count_requirement(
        expected_job_count=expected_job_count,
        actual_job_count=actual_job_count,
        count_basis=count_basis,
        target_path=target_path,
    )
    return {
        "status": "error",
        "error_type": "expected_output_policy",
        "guardrail": "expected_output_count_mismatch",
        "audit_id": audit_id,
        "path": target_path,
        "expected_job_count": expected_job_count,
        "actual_job_count": actual_job_count,
        "count_basis": count_basis,
        "error": (
            f"The agent declared expected_output.expected_job_count={expected_job_count}, "
            f"but {target_path} contains {actual_job_count} jobs."
        ),
        "unsatisfied_requirements": [unsatisfied],
        "required_next": (
            "Use unsatisfied_requirements to choose and record the next action. Do not repeat the rejected "
            "successful output unchanged. Inspect the past observations/tool results that established "
            "expected_output, inspect the currently available tools/resources, and use the same evidence basis "
            "to plan extraction for every expected unit. Satisfy the missing prerequisite, revise expected_output "
            "with evidence-backed filtering, or write needs_review with rationale."
        ),
        "count": 0,
        "written_count": 0,
    }


def _expected_output_required_error(
    target_path: str,
    active: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    audit_id = str(active.get("audit_id") or context.get("audit_id") or "")
    return {
        "status": "error",
        "error_type": "expected_output_policy",
        "guardrail": "expected_output_required",
        "audit_id": audit_id,
        "path": target_path,
        "error": (
            f"Before writing {target_path}, the agent must declare expected_output.expected_job_count "
            "derived from its repeated-pattern observations."
        ),
        "unsatisfied_requirements": [
            {
                "id": "expected_output_declared_before_success_output",
                "path": target_path,
                "missing": "expected_output.expected_job_count",
                "agent_responsibility": (
                    "Infer the expected output contract from observations before authoring successful "
                    "candidates/final protocol output."
                ),
                "acceptable_resolutions": [
                    "Declare expected_output from repeated-unit observations, then author matching output.",
                    "Write needs_review with evidence-backed rationale if the expected output cannot be determined.",
                ],
            }
        ],
        "required_next": (
            "Use unsatisfied_requirements to decide and record the next action before retrying a successful "
            "protocol output write."
        ),
        "count": 0,
        "written_count": 0,
    }


def _expected_output_count_explanation_error(
    *,
    target_path: str,
    active: dict[str, Any],
    context: dict[str, Any],
    expected_output: dict[str, Any],
) -> dict[str, Any] | None:
    has_basis = bool(str(expected_output.get("count_basis") or expected_output.get("basis") or "").strip())
    has_rationale = any(
        str(expected_output.get(key) or "").strip()
        for key in ("count_rationale", "count_derivation", "how_known")
    )
    if has_basis and has_rationale:
        return None

    missing = []
    if not has_basis:
        missing.append("count_basis")
    if not has_rationale:
        missing.append("count_rationale")
    audit_id = str(active.get("audit_id") or context.get("audit_id") or "")
    return {
        "status": "error",
        "error_type": "expected_output_policy",
        "guardrail": "expected_output_count_explanation_required",
        "audit_id": audit_id,
        "path": target_path,
        "missing": missing,
        "error": (
            "Before writing successful candidates/final output, expected_output must explain how the agent "
            "knows the expected job count from its past observations/actions."
        ),
        "unsatisfied_requirements": [
            {
                "id": "expected_output_count_derivation_recorded",
                "path": target_path,
                "missing": missing,
                "invariant": "The expected output count must be justified before it is enforced.",
                "agent_responsibility": (
                    "Inspect prior observations, attempted_actions, last_result, and relevant tool outputs. "
                    "Record how those past actions established the expected repeated-unit count, then use "
                    "that same basis to plan extraction."
                ),
                "acceptable_resolutions": [
                    "Update expected_output with count_basis and count_rationale derived from prior observations/actions.",
                    "Write needs_review if the expected count cannot be justified from available evidence.",
                ],
            }
        ],
        "required_next": (
            "Record the count derivation up front in expected_output before retrying a successful protocol "
            "output write. Include the observation/tool-result basis and how it implies the expected count."
        ),
        "count": 0,
        "written_count": 0,
    }


def _expected_output_count_requirement(
    *,
    expected_job_count: int,
    actual_job_count: int,
    count_basis: str,
    target_path: str,
) -> dict[str, Any]:
    return {
        "id": "successful_output_matches_expected_job_count",
        "path": target_path,
        "expected_job_count": expected_job_count,
        "actual_job_count": actual_job_count,
        "count_basis": count_basis,
        "invariant": (
            "A successful candidates/final output must account for every job unit the agent decided is in scope."
        ),
        "agent_responsibility": (
            "Reason from the current context to identify the missing prerequisite. First inspect the "
            "observations, attempted actions, last_result, and count_basis that caused the agent to expect "
            "this many jobs; then inspect available tools/resources and derive an extraction plan from the "
            "same repeated-unit evidence. If the agent has not seen enough exact evidence to author every "
            "in-scope job, it should choose available tooling that can create/load bounded evidence for the "
            "missing units before authoring another successful output."
        ),
        "acceptable_resolutions": [
            "Inspect available tools/resources, then use the same repeated-unit signal that justified expected_job_count to collect/load evidence and author a matching successful output.",
            "Revise expected_output only if new evidence supports a documented filter or changed scope.",
            "Write needs_review with evidence-backed rationale if the expected jobs cannot be extracted safely.",
        ],
    }


def _expected_output_field_availability_error(
    *,
    target_path: str,
    active: dict[str, Any],
    context: dict[str, Any],
    expected_output: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    available_fields = expected_output.get("available_fields")
    field_basis = expected_output.get("field_basis")
    audit_id = str(active.get("audit_id") or context.get("audit_id") or "")
    if not isinstance(available_fields, dict) or not available_fields:
        return _expected_output_field_contract_error(
            target_path=target_path,
            audit_id=audit_id,
            missing="expected_output.available_fields",
            message=(
                "Before writing successful candidates/final output, expected_output must declare which metadata "
                "fields the agent observed as available on the page."
            ),
        )
    if not isinstance(field_basis, dict):
        return _expected_output_field_contract_error(
            target_path=target_path,
            audit_id=audit_id,
            missing="expected_output.field_basis",
            message=(
                "Before writing successful candidates/final output, expected_output must include field_basis "
                "explaining why required observed metadata fields are available."
            ),
        )
    required_fields = _expected_output_required_observed_fields(expected_output)
    missing_basis = [field for field in required_fields if not str(field_basis.get(field) or "").strip()]
    if missing_basis:
        return _expected_output_field_contract_error(
            target_path=target_path,
            audit_id=audit_id,
            missing=f"expected_output.field_basis for {', '.join(missing_basis)}",
            message=(
                "Every required_observed metadata field must include a field_basis entry naming the page signal "
                "that made the agent treat the field as available."
            ),
        )
    if not required_fields:
        return None

    jobs = _protocol_payload_jobs(payload)
    if jobs is None:
        return None
    missing_values: list[dict[str, Any]] = []
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            continue
        for field in required_fields:
            if _is_placeholder_field_value(job.get(field)):
                missing_values.append({"index": index, "field": field, "value": job.get(field)})
                if len(missing_values) >= 5:
                    break
        if len(missing_values) >= 5:
            break
    if not missing_values:
        return None
    return {
        "status": "error",
        "error_type": "expected_output_policy",
        "guardrail": "expected_output_field_coverage_mismatch",
        "audit_id": audit_id,
        "path": target_path,
        "missing_or_placeholder_fields": missing_values,
        "error": (
            f"The agent declared required observed metadata fields in expected_output.available_fields, "
            f"but {target_path} contains missing or placeholder values for those fields."
        ),
        "unsatisfied_requirements": [
            {
                "id": "successful_output_matches_observed_field_availability",
                "path": target_path,
                "required_observed_fields": required_fields,
                "missing_or_placeholder_fields": missing_values,
                "invariant": (
                    "If the agent records a metadata field as observed/required from page evidence, successful "
                    "outputs must extract a real value for that field or change status to needs_review."
                ),
                "agent_responsibility": (
                    "Use the field_basis and current observations to load the relevant card/detail evidence, "
                    "repair the extraction method or supporting script, then regenerate candidates/final from "
                    "real evidence. Do not fill observed fields with 'unknown'."
                ),
                "acceptable_resolutions": [
                    "Extract real values for the required_observed fields from bounded page evidence.",
                    "Revise expected_output.available_fields only if new evidence proves the field is not available.",
                    "Write needs_review with evidence-backed blockers when the field cannot be safely extracted.",
                ],
            }
        ],
        "required_next": (
            "Repair field coverage before retrying a successful protocol output: inspect the evidence basis for "
            "the required_observed fields, patch or rerun the extraction helper, or return needs_review with "
            "a blocker. Do not repeat the same placeholder values."
        ),
        "count": 0,
        "written_count": 0,
    }


def _expected_output_field_contract_error(
    *,
    target_path: str,
    audit_id: str,
    missing: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "error",
        "error_type": "expected_output_policy",
        "guardrail": "expected_output_field_availability_required",
        "audit_id": audit_id,
        "path": target_path,
        "missing": missing,
        "error": message,
        "unsatisfied_requirements": [
            {
                "id": "expected_output_field_availability_recorded",
                "path": target_path,
                "missing": missing,
                "invariant": (
                    "The agent must declare which metadata fields were observed as available before writing "
                    "successful outputs, so validation can reject placeholders for observed fields."
                ),
                "agent_responsibility": (
                    "Inspect page observations and repeated card/detail evidence. Record available_fields and "
                    "field_basis in expected_output, then author outputs that match that field contract."
                ),
                "acceptable_resolutions": [
                    "Record available_fields/field_basis and extract matching field values.",
                    "Record why fields are not observed and return needs_review if required metadata cannot be verified.",
                ],
            }
        ],
        "required_next": (
            "Update expected_output with available_fields and field_basis derived from page evidence before "
            "retrying a successful candidates/final write."
        ),
        "count": 0,
        "written_count": 0,
    }


def _expected_output_required_observed_fields(expected_output: dict[str, Any]) -> list[str]:
    fields: set[str] = set()
    direct_fields = expected_output.get("required_observed_fields")
    if isinstance(direct_fields, list):
        fields.update(str(item).strip() for item in direct_fields if str(item).strip())
    available_fields = expected_output.get("available_fields")
    if isinstance(available_fields, dict):
        for field, status in available_fields.items():
            field_name = str(field).strip()
            if field_name and _is_required_observed_status(status):
                fields.add(field_name)
    return sorted(fields)


def _is_required_observed_status(status: Any) -> bool:
    if isinstance(status, bool):
        return status
    if isinstance(status, str):
        normalized = status.strip().lower().replace("-", "_").replace(" ", "_")
        return normalized in REQUIRED_OBSERVED_FIELD_STATUSES
    if isinstance(status, dict):
        if bool(status.get("required")) or bool(status.get("required_when_observed")):
            return True
        for key in ("status", "availability", "requirement"):
            value = status.get(key)
            if isinstance(value, str) and _is_required_observed_status(value):
                return True
    return False


def _is_placeholder_field_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in PLACEHOLDER_FIELD_VALUES
    if isinstance(value, list):
        return not any(not _is_placeholder_field_value(item) for item in value)
    return False


def _expected_output_job_count(expected_output: dict[str, Any]) -> int | None:
    for key in ("expected_job_count", "job_count", "total_jobs", "observed_job_count"):
        value = expected_output.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _protocol_payload_jobs(payload: dict[str, Any]) -> list[Any] | None:
    if isinstance(payload.get("jobs"), list):
        return payload["jobs"]
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("jobs"), list):
        return result["jobs"]
    return None


def _sandbox_write_json_payload(tool_args: dict[str, Any]) -> dict[str, Any] | None:
    args = [str(item) for item in (tool_args.get("args") or []) if item is not None]
    content = _option_value(args, "--content")
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _protocol_payload_job_count(payload: dict[str, Any]) -> int | None:
    jobs = payload.get("jobs")
    if isinstance(jobs, list):
        return len(jobs)
    result = payload.get("result")
    if isinstance(result, dict):
        result_jobs = result.get("jobs")
        if isinstance(result_jobs, list):
            return len(result_jobs)
    return None


def _payload_declares_non_success_review(payload: dict[str, Any]) -> bool:
    status_values: list[Any] = [payload.get("status")]
    result = payload.get("result")
    if isinstance(result, dict):
        status_values.append(result.get("status"))
    crawl = payload.get("crawl")
    if isinstance(crawl, dict):
        status_values.append(crawl.get("status"))
        if crawl.get("needs_review") is True:
            return True
    if isinstance(result, dict):
        result_crawl = result.get("crawl")
        if isinstance(result_crawl, dict):
            status_values.append(result_crawl.get("status"))
            if result_crawl.get("needs_review") is True:
                return True
    return any(str(value or "").strip().lower() in {"needs_review", "blocked", "error"} for value in status_values)


def _producer_write_after_success_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path not in {"scripts/sandbox_write_file.py", "scripts/sandbox_apply_patch.py"}:
        return None
    if not _sandbox_write_touches_producer(tool_args):
        return None

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if not active.get("extractor_executed"):
        return None

    repair = active.get("last_repair_target")
    if isinstance(repair, dict) and _repair_target_allows_producer_write(repair):
        return None

    audit_id = str(active.get("audit_id") or "")
    return {
        "status": "error",
        "error_type": "producer_write_after_success_policy",
        "guardrail": "validate_or_finalize_after_successful_producer_run",
        "audit_id": audit_id,
        "path": WORKFLOW_PRODUCER_PATH,
        "count": 0,
        "written_count": 0,
        "error": (
            "output/extractor.py already ran successfully in the active workflow sandbox. Do not rewrite or patch "
            "the producer again based on self-judgment or stale notes."
        ),
        "required_next": (
            "Run scripts/validate_outputs.py or scripts/sandbox_finalize.py for this audit. Only modify "
            "output/extractor.py again if the fresh validator/finalizer result reports a concrete repair error."
        ),
    }


def _sandbox_write_touches_producer(tool_args: dict[str, Any]) -> bool:
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path == "scripts/sandbox_write_file.py":
        return _sandbox_write_target_path(tool_args) == WORKFLOW_PRODUCER_PATH
    if file_path != "scripts/sandbox_apply_patch.py":
        return False
    args = [str(item) for item in (tool_args.get("args") or []) if item is not None]
    exact_path = _normalize_skill_path(_option_value(args, "--path"))
    if exact_path == WORKFLOW_PRODUCER_PATH:
        return True
    patch = str(_option_value(args, "--patch") or "")
    return "output/extractor.py" in patch or "extractor.py" in patch


def _repair_target_allows_producer_write(repair: dict[str, Any]) -> bool:
    if repair.get("producer_rerun_status") == "success_unvalidated":
        return False
    required_action = str(repair.get("required_action") or "")
    if required_action in {"debug_repair_extractor", "debug_repair_protocol_outputs", "agent_plan_repair"}:
        return True
    source = _normalize_skill_path(str(repair.get("file_path") or ""))
    if source not in {"scripts/sandbox_finalize.py", "scripts/validate_outputs.py"}:
        return False
    error = str(repair.get("error") or repair.get("stderr") or repair.get("error_type") or "")
    return bool(error.strip())


def _sandbox_write_target_path(tool_args: dict[str, Any]) -> str:
    args = [str(item) for item in (tool_args.get("args") or []) if item is not None]
    return _normalize_skill_path(_option_value(args, "--path"))


def _active_sandbox_guardrail_terminal_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict):
        return None
    if str(active.get("status") or "") != "guardrail_triggered":
        return None
    if not _is_sandbox_terminal_blocked_tool(tool_name, tool_args):
        return None

    audit_id = str(active.get("audit_id") or "")
    guardrail = str(active.get("guardrail") or "sandbox_guardrail_triggered")
    return {
        "status": "error",
        "error_type": "sandbox_guardrail_terminal",
        "guardrail": guardrail,
        "audit_id": audit_id,
        "error": (
            f"Sandbox workflow {audit_id or '<unknown>'} is already terminal because guardrail "
            f"{guardrail} was triggered. No further sandbox, persistence, record, or query tools may run for it."
        ),
        "required_next": (
            "Stop the sandbox workflow and report the guardrail blocker with the audit ID and last actionable error. "
            "Do not persist, query, record, or attempt more sandbox commands in this run."
        ),
        "count": 0,
        "written_count": 0,
    }


def _is_sandbox_terminal_blocked_tool(tool_name: str, tool_args: dict[str, Any]) -> bool:
    if tool_name in {
        "update_extraction_context",
        "promote_sandbox_extraction",
        "persist_sandbox_job_extraction",
        "record_crawl_run",
        "query_jobs",
    }:
        return True
    if tool_name == "run_skill_script" and tool_args.get("skill_name") == "sandbox-page-analyst":
        return True
    return False


def _is_sandbox_budget_counted_tool(tool_name: str, tool_args: dict[str, Any]) -> bool:
    if tool_name == "run_skill_script" and _is_skill_script_help_request(tool_args.get("args") or []):
        return False
    if tool_name != "run_skill_script" or tool_args.get("skill_name") != "sandbox-page-analyst":
        return False

    policy = resolve_tool_policy(tool_name, tool_args)
    if policy.kind in {ToolActionKind.SANDBOX_READ, ToolActionKind.REFERENCE_READ}:
        return False
    return policy.changes_workflow_output or policy.terminal or policy.kind == ToolActionKind.SANDBOX_EXEC


def _is_skill_script_help_request(args: Any) -> bool:
    return any(str(item) in {"--help", "-h"} for item in args if item is not None)


def _workflow_sandbox_tool_budget_error(tool_name: str, tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if not _is_sandbox_budget_counted_tool(tool_name, tool_args):
        return None
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if str(active.get("status") or "running") != "running":
        return None

    audit_id = str(active.get("audit_id") or "")
    if not audit_id:
        return None
    budgets = state.setdefault(SANDBOX_TOOL_BUDGET_STATE_KEY, {})
    if not isinstance(budgets, dict):
        budgets = {}
        state[SANDBOX_TOOL_BUDGET_STATE_KEY] = budgets
    count = int(budgets.get(audit_id) or 0) + 1
    budgets[audit_id] = count
    state[SANDBOX_TOOL_BUDGET_STATE_KEY] = budgets
    if count <= MAX_WORKFLOW_SANDBOX_TOOL_CALLS:
        return None

    active["status"] = "guardrail_triggered"
    active["guardrail"] = "workflow_sandbox_tool_budget_exceeded"
    active["tool_call_count"] = count
    state[ACTIVE_SANDBOX_STATE_KEY] = active
    return {
        "status": "error",
        "error_type": "workflow_sandbox_tool_budget",
        "guardrail": "workflow_sandbox_tool_budget_exceeded",
        "audit_id": audit_id,
        "tool_call_count": count,
        "max_tool_calls": MAX_WORKFLOW_SANDBOX_TOOL_CALLS,
        "error": (
            f"Workflow sandbox {audit_id} exceeded the runtime tool-call budget "
            f"({count}>{MAX_WORKFLOW_SANDBOX_TOOL_CALLS})."
        ),
        "required_next": (
            "Stop this sandbox workflow and report the blocker. The run needs a clearer repair/debugging plan "
            "before another attempt; do not persist, query, record, or run more sandbox commands in this run."
        ),
        "count": 0,
        "written_count": 0,
    }


def _extraction_context_update_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    """Block context-update loops that do not take a state-changing action."""

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if str(active.get("status") or "running") != "running":
        return None

    audit_id = str(tool_args.get("audit_id") or active.get("audit_id") or "")
    digest = _extraction_context_update_digest(tool_args)
    guard = state.get(EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY)
    if not isinstance(guard, dict) or str(guard.get("audit_id") or "") != audit_id:
        guard = {
            "audit_id": audit_id,
            "last_digest": "",
            "repeat_count": 0,
            "consecutive_count": 0,
        }

    last_digest = str(guard.get("last_digest") or "")
    repeat_count = int(guard.get("repeat_count") or 0) + 1 if digest == last_digest else 1
    consecutive_count = int(guard.get("consecutive_count") or 0) + 1
    guard["last_digest"] = digest
    guard["repeat_count"] = repeat_count
    guard["consecutive_count"] = consecutive_count
    state[EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY] = guard

    if repeat_count < 2 and consecutive_count <= 1:
        return None

    planned_next_tool = _active_planned_next_tool(tool_context)
    if planned_next_tool:
        required_next = (
            "You already updated SESSION_EXTRACTION_CONTEXT with a concrete planned_next_tool. "
            "Look at the plan you wrote previously and act accordingly: call required_next_tool now. "
            "Do not call update_extraction_context again until that planned tool returns, unless new "
            "non-context evidence proves the plan cannot be followed."
        )
    else:
        required_next = (
            "You already updated SESSION_EXTRACTION_CONTEXT once. Look at the plan you wrote previously and "
            "take the next state-changing sandbox action from that plan instead of another context-only update: "
            "write or repair a supporting script, write or revise accountable protocol outputs, validate outputs, "
            "finalize, promote finalized outputs, or return a compact blocker only if no safe repair exists. The "
            "sandbox remains active; do not treat this as a terminal sandbox failure."
        )

    return {
        "status": "error",
        "error_type": "extraction_context_update_policy",
        "guardrail": "repeated_extraction_context_updates",
        "terminal": False,
        "audit_id": audit_id,
        "repeat_count": repeat_count,
        "consecutive_count": consecutive_count,
        "error": (
            "update_extraction_context was called while the workflow sandbox was still running and no "
            "state-changing workflow action happened after the previous context update. You already updated "
            "SESSION_EXTRACTION_CONTEXT once; the next step is to follow the plan you wrote there."
        ),
        "required_next_tool": planned_next_tool or {},
        "required_next": required_next,
    }


def _is_extraction_context_progress_action(tool_name: str, tool_args: dict[str, Any]) -> bool:
    return resolve_tool_policy(tool_name, tool_args).counts_as_intervening_action


def _extraction_context_update_digest(tool_args: dict[str, Any]) -> str:
    material = {
        key: tool_args.get(key)
        for key in EXTRACTION_CONTEXT_DIGEST_KEYS
    }
    return _sha256_json(material)


def _reset_extraction_context_update_guard(tool_context: Any) -> None:
    state = getattr(tool_context, "state", None)
    if _is_state_like(state):
        _state_pop(state, EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY, None)


def _repeated_sandbox_read_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    signature = _sandbox_read_signature(tool_args)
    if not signature:
        return None
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    if str(active.get("status") or "running") != "running":
        return None
    if not isinstance(active.get("last_repair_target"), dict):
        return None

    guard = state.get(SANDBOX_READ_GUARD_STATE_KEY)
    if not isinstance(guard, dict) or guard.get("signature") != signature:
        guard = {"signature": signature, "count": 0}
    guard["count"] = int(guard.get("count") or 0) + 1
    state[SANDBOX_READ_GUARD_STATE_KEY] = guard
    if int(guard["count"]) <= 2:
        return None

    audit_id, path = signature.split(":", 1)
    return {
        "status": "error",
        "error_type": "repeated_sandbox_read_policy",
        "guardrail": "same_sandbox_file_read_during_repair",
        "audit_id": audit_id,
        "path": path,
        "error": (
            f"{path} has already been read repeatedly during the active repair target. More previews of the same "
            "file are unlikely to repair the workflow."
        ),
        "required_next": (
            "Take a state-changing repair action now: patch the relevant helper with scripts/sandbox_apply_patch.py, "
            "rewrite it with scripts/sandbox_write_file.py if patching is not viable, or revise the accountable "
            "protocol output from inspected evidence/script output; then rerun the focused validation/finalization step."
        ),
        "count": 0,
        "written_count": 0,
    }


def _sandbox_read_signature(tool_args: dict[str, Any]) -> str:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return ""
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_read.py":
        return ""
    args = [str(item) for item in (tool_args.get("args") or []) if item is not None]
    audit_id = str(_option_value(args, "--audit-id") or "")
    path = _normalize_skill_path(str(_option_value(args, "--path") or ""))
    if not audit_id or not path:
        return ""
    return f"{audit_id}:{path}"


def _reset_sandbox_read_guard(tool_context: Any) -> None:
    state = getattr(tool_context, "state", None)
    if _is_state_like(state):
        _state_pop(state, SANDBOX_READ_GUARD_STATE_KEY, None)


def _workflow_script_execution_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None

    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    pending = _pending_scripts_for_active_sandbox(state, active)
    if not pending:
        return None

    if file_path == "scripts/sandbox_exec.py" and _sandbox_exec_runs_pending_script(tool_args, pending):
        return None
    if file_path == "scripts/sandbox_write_file.py":
        return None

    if file_path in {"scripts/sandbox_finalize.py", "scripts/validate_outputs.py"}:
        return _pending_script_policy_error(active, pending, f"{file_path} requires the written script to run first")

    return None


def _initial_extraction_context_policy_error(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    """Require a first-pass task note before any workflow/probing tool."""

    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    # Once a workflow has already created runtime markers, preserve the more
    # specific guardrail messages for that phase. This policy is only for the
    # very first tool decision of a fresh scraping task.
    if state.get(ACTIVE_SANDBOX_STATE_KEY) or state.get(SANDBOX_MODE_RESOURCE_STATE_KEY) or state.get(LAST_PAGE_WORKSPACE_STATE_KEY):
        return None

    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    has_initial_context = (
        isinstance(context, dict)
        and bool(context.get("updated"))
        and bool(context.get("task_understanding"))
        and bool(context.get("final_goal"))
        and bool(context.get("initial_plan") or context.get("extraction_plan"))
    )
    if has_initial_context:
        return None

    if tool_name == "update_extraction_context":
        has_task_understanding = bool(str(tool_args.get("task_understanding") or "").strip())
        has_final_goal = bool(str(tool_args.get("final_goal") or "").strip())
        has_initial_plan = bool(tool_args.get("initial_plan") or tool_args.get("extraction_plan"))
        if has_task_understanding and has_final_goal and has_initial_plan:
            return None
        return {
            "status": "error",
            "error_type": "initial_extraction_context_policy",
            "guardrail": "initial_extraction_context_required",
            "error": (
                "The first update_extraction_context call for this scraping task must record the agent's "
                "task_understanding, final_goal, and initial_plan before continuing."
            ),
            "required_next": (
                "Call update_extraction_context with task_understanding as your interpretation of the user request "
                "final_goal as the stable workflow goal that must be achieved, and initial_plan as the concrete "
                "first steps you intend to take. Keep it compact; do not load skills, resources, fetch pages, "
                "or run scripts before this note exists."
            ),
        }

    if tool_name not in INITIAL_CONTEXT_REQUIRED_TOOLS:
        return None
    return {
        "status": "error",
        "error_type": "initial_extraction_context_policy",
        "guardrail": "initial_extraction_context_required",
        "error": "A session extraction context note is required before workflow tools can run.",
        "required_next": (
            "Call update_extraction_context first with task_understanding, final_goal, and initial_plan. "
            "The note should state what you think the user is asking for, the stable workflow goal/output that "
            "must be achieved, and the next few steps you plan to take. After that, continue with skill loading "
            "or page workspace tools."
        ),
    }


def _active_sandbox_record_query_error(tool_context: Any, tool_name: str) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict) or str(active.get("mode") or "workflow") != "workflow":
        return None
    status = str(active.get("status") or "running")
    if status != "running":
        return None
    pending = _pending_scripts_for_active_sandbox(state, active)
    if pending:
        return _pending_script_policy_error(active, pending, f"{tool_name} requires the written script to run and produce protocol artifacts first")
    return {
        "status": "error",
        "error_type": "workflow_sandbox_still_active",
        "guardrail": "workflow_sandbox_must_finish_before_record_or_query",
        "count": 0,
        "written_count": 0,
        "error": (
            f"{tool_name} is blocked while workflow sandbox {active.get('audit_id') or '<unknown>'} is still running. "
            "Finish the sandbox protocol first."
        ),
        "required_next": (
            "Continue the active sandbox workflow: complete evidence loading and helper/protocol repairs, validate "
            "protocol outputs, call sandbox_finalize.py successfully, then persist/query/record results."
        ),
    }


def _track_pending_sandbox_script_write(
    state: Any,
    active: dict[str, Any],
    tool_args: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if str(payload.get("status") or "") != "success":
        return
    args = [str(item) for item in (tool_args.get("args") or [])]
    path = _normalize_skill_path(str(payload.get("path") or _option_value(args, "--path") or ""))
    if path not in {"output/extractor.py", "extractor.py"}:
        return
    audit_id = str(active.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
    if not audit_id:
        return
    pending_by_audit = state.setdefault(SANDBOX_PENDING_SCRIPT_STATE_KEY, {})
    if not isinstance(pending_by_audit, dict):
        pending_by_audit = {}
        state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = pending_by_audit
    pending_by_audit[audit_id] = {
        "path": "output/extractor.py",
        "status": "written_not_executed",
        "required_command": "python output/extractor.py",
        "last_write_sha256": _sha256_text(_option_value(args, "--content")),
    }


def _track_pending_sandbox_script_patch(
    state: Any,
    active: dict[str, Any],
    tool_args: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if str(payload.get("status") or "") != "success":
        return
    changed_paths = _changed_file_paths_from_patch_payload(payload)
    if WORKFLOW_PRODUCER_PATH not in changed_paths and "extractor.py" not in changed_paths:
        return
    audit_id = str(active.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
    if not audit_id:
        return
    args = [str(item) for item in (tool_args.get("args") or [])]
    pending_by_audit = state.setdefault(SANDBOX_PENDING_SCRIPT_STATE_KEY, {})
    if not isinstance(pending_by_audit, dict):
        pending_by_audit = {}
        state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = pending_by_audit
    pending_by_audit[audit_id] = {
        "path": WORKFLOW_PRODUCER_PATH,
        "status": "patched_not_executed",
        "required_command": f"python {WORKFLOW_PRODUCER_PATH}",
        "last_patch_sha256": _sha256_json(
            {
                "path": _option_value(args, "--path"),
                "old": _option_value(args, "--old"),
                "new": _option_value(args, "--new"),
                "patch": _option_value(args, "--patch"),
                "changed_paths": sorted(changed_paths),
            }
        ),
    }
    active["extractor_executed"] = False
    state[ACTIVE_SANDBOX_STATE_KEY] = active


def _changed_file_paths_from_patch_payload(payload: dict[str, Any]) -> set[str]:
    changed = payload.get("changed_files")
    paths: set[str] = set()
    if not isinstance(changed, list):
        return paths
    for item in changed:
        if isinstance(item, dict):
            path = item.get("path")
        else:
            path = item
        if path:
            paths.add(_normalize_skill_path(str(path)))
    return paths


def _mark_pending_sandbox_script_execution(
    state: Any,
    active: dict[str, Any],
    tool_args: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    pending = _pending_scripts_for_active_sandbox(state, active)
    if not pending:
        return
    if not _sandbox_exec_runs_pending_script(tool_args, pending):
        return
    audit_id = str(active.get("audit_id") or "")
    if not audit_id:
        return
    status = str(payload.get("status") or "")
    exit_code = payload.get("exit_code")
    pending_by_audit = state.get(SANDBOX_PENDING_SCRIPT_STATE_KEY)
    if not isinstance(pending_by_audit, dict):
        return
    if status == "success" and int(exit_code or 0) == 0:
        _state_pop(pending_by_audit, audit_id, None)
        state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = pending_by_audit
        active["extractor_executed"] = True
        state[ACTIVE_SANDBOX_STATE_KEY] = active
        return
    current = dict(pending)
    current["status"] = "execution_failed"
    current["last_error"] = str(payload.get("stderr") or payload.get("error") or "")
    pending_by_audit[audit_id] = current
    state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = pending_by_audit


def _mark_successful_producer_rerun_after_repair(
    state: Any,
    active: dict[str, Any],
    tool_args: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if not _sandbox_exec_runs_producer(tool_args):
        return
    if str(payload.get("status") or "") != "success" or int(payload.get("exit_code") or 0) != 0:
        return
    repair = active.get("last_repair_target")
    if not isinstance(repair, dict):
        return
    source = _normalize_skill_path(str(repair.get("file_path") or ""))
    if source not in {"scripts/sandbox_finalize.py", "scripts/validate_outputs.py"}:
        return
    repair["producer_rerun_status"] = "success_unvalidated"
    repair["required_action"] = "validate_repaired_outputs"
    active["last_repair_target"] = repair
    active["extractor_executed"] = True
    state[ACTIVE_SANDBOX_STATE_KEY] = active


def _pending_scripts_for_active_sandbox(state: Any, active: dict[str, Any]) -> dict[str, Any]:
    audit_id = str(active.get("audit_id") or "")
    pending_by_audit = state.get(SANDBOX_PENDING_SCRIPT_STATE_KEY) if _is_state_like(state) else None
    if not audit_id or not isinstance(pending_by_audit, dict):
        return {}
    pending = pending_by_audit.get(audit_id)
    return pending if isinstance(pending, dict) else {}


def _sandbox_exec_runs_pending_script(tool_args: dict[str, Any], pending: dict[str, Any]) -> bool:
    command = _sandbox_exec_command(tool_args)
    script_path = str(pending.get("path") or "output/extractor.py")
    normalized = command.replace("/workspace/", "").replace("./", "")
    return f"python {script_path}" in normalized or f"python3 {script_path}" in normalized


def _pending_script_policy_error(active: dict[str, Any], pending: dict[str, Any], reason: str) -> dict[str, Any]:
    audit_id = str(active.get("audit_id") or "")
    script_path = str(pending.get("path") or "output/extractor.py")
    return {
        "status": "error",
        "error_type": "sandbox_script_not_verified",
        "guardrail": "written_script_must_run_before_finalization",
        "audit_id": audit_id,
        "script_path": script_path,
        "count": 0,
        "written_count": 0,
        "error": reason,
        "required_next": (
            f"Run the written helper/script in the sandbox with sandbox_exec.py using `python {script_path}`. "
            "Then verify it produced required protocol artifacts before validate/finalize/persist/query/record."
        ),
    }


def _missing_audit_id_required_next(file_path: str, audit_id: str) -> str:
    if file_path.endswith("sandbox_exec.py"):
        return (
            f"Retry the same sandbox command with args including `--audit-id {audit_id}` and `--cmd <shell command>`. "
            "If the command itself needs changes, update_extraction_context with the revised plan before retrying."
        )
    if file_path.endswith("sandbox_write_file.py"):
        return (
            f"Retry with args including `--audit-id {audit_id}`, `--path <workspace-relative file>`, and "
            "`--content <file text>`. If you need exact options, call scripts/sandbox_write_file.py with `--help` first."
        )
    return (
        f"Retry the same helper with args including `--audit-id {audit_id}`. "
        "If you need exact options, call the helper with `--help` first."
    )


def _add_required_next_for_pending_script(result: dict[str, Any], state: Any) -> dict[str, Any] | None:
    active = state.get(ACTIVE_SANDBOX_STATE_KEY) if _is_state_like(state) else None
    if not isinstance(active, dict):
        return None
    pending = _pending_scripts_for_active_sandbox(state, active)
    if not pending:
        return None
    return _add_required_next(
        result,
        (
            f"Run the written or patched helper/script before inspecting final outputs: use sandbox_exec.py with "
            f"`python {pending.get('path') or 'output/extractor.py'}`. Then verify it produced protocol artifacts."
        ),
    )


def _sandbox_debugger_required_next(
    result: dict[str, Any],
    active: dict[str, Any],
    file_path: str,
    payload: dict[str, Any],
    tool_args: dict[str, Any],
) -> dict[str, Any] | None:
    if str(active.get("mode") or "workflow") != "workflow":
        return None
    status = str(payload.get("status") or "")
    exit_code = payload.get("exit_code")
    if status not in {"error", "blocked"} and not (file_path.endswith("sandbox_exec.py") and int(exit_code or 0) != 0):
        return None

    audit_id = str(active.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
    error = str(payload.get("error") or payload.get("stderr") or payload.get("error_type") or "sandbox workflow error")
    updated = _promote_script_payload_error(result, payload)
    if payload.get("error_type") == "sandbox_script_args_policy":
        guardrail = str(payload.get("guardrail") or "")
        if guardrail == "sandbox_script_requires_audit_id":
            return _add_required_next(updated, _missing_audit_id_required_next(file_path, audit_id))
        return _add_required_next(
            updated,
            (
                f"Correct the {file_path} argument shape and retry. "
                "If exact options are unclear, call the same script with `--help` before retrying."
            ),
        )
    if (
        file_path.endswith("sandbox_write_file.py")
        and _sandbox_write_target_path(tool_args) == WORKFLOW_PRODUCER_PATH
        and ("protocol producer" in error.lower() or "required protocol output" in error.lower())
    ):
        repair_guidance = (
            "The previous sandbox_write_file.py call was rejected before output/extractor.py was accepted. "
            "do not patch a missing file and do not modify host workflow code. Create or rewrite "
            "`output/extractor.py` with `scripts/sandbox_write_file.py` using corrected full source that writes "
            "every required protocol output under `output/` in one run, then run `python output/extractor.py` "
            "with `scripts/sandbox_exec.py` and validate/finalize. Modify only Docker sandbox workspace artifacts. "
            f"Last error: {_preview(error, 500)}"
        )
    elif payload.get("error_type") == "expected_output_policy":
        repair_guidance = (
            "Use the returned unsatisfied_requirements as invariant facts, not as a scripted tool plan. The "
            "agent must reason about which prerequisite is missing, record that decision in session context with "
            "a concrete planned_next_tool, and take a state-changing action that can satisfy the invariant. "
            "Before choosing that action, inspect how expected_output was derived: the observations, "
            "count_basis, attempted_actions, and latest tool results that established the expected unit count. "
            "Also inspect the available tools/resources and choose how those tools can satisfy the unmet "
            "expectation. Use that same evidence basis to plan extraction for every expected unit. Do not repeat "
            "the rejected successful output unchanged. If exact evidence for the expected units has not been "
            "loaded, make evidence coverage the next objective before authoring another successful candidates/final payload. "
            f"Last error: {_preview(error, 500)}"
        )
    elif "evidence/index.json is required" in error.lower():
        repair_guidance = (
            "Load `sandbox-extraction-debugger` before the next repair attempt. Do not rerun finalization and do "
            "not keep rewriting candidates/final with unsaved evidence refs. The concrete repair is to create exact "
            "evidence chunks under `evidence/chunks/`, write `evidence/index.json`, mark only chunks already loaded "
            "by the agent as `loaded: true`, and reconcile `output/candidates.json` plus `output/final.json` so every "
            "field_rationale/evidence_refs entry points to a saved loaded chunk. Update session context with "
            "planned_next_tool for `scripts/sandbox_write_file.py` or `scripts/sandbox_read.py`, and repair_scope "
            "must include `evidence/index.json`, `evidence/chunks/`, `output/candidates.json`, and `output/final.json`. "
            f"Last error: {_preview(error, 500)}"
        )
    else:
        repair_guidance = (
            "Load `sandbox-extraction-debugger` before the next repair attempt. Use the sandbox tools exposed through "
            "`run_skill_script`: inspect files with `scripts/sandbox_read.py`, run focused shell/Python probes with "
            "`scripts/sandbox_exec.py`, patch existing artifacts with `scripts/sandbox_apply_patch.py`, and modify "
            "only Docker sandbox workspace artifacts. Use `scripts/sandbox_write_file.py` only for initial creation "
            "or unresolvable patch conflicts. "
            "If the error says required protocol outputs are missing, do not read the missing files as the next step; "
            "inspect loaded evidence, current protocol outputs, and any serialization helper, then create or repair "
            "the missing accountable protocol files from loaded evidence and validate/finalize again. Otherwise inspect the current sandbox workspace "
            f"to identify which output artifact or sandbox-written script caused the error in {file_path} for audit {audit_id}. Treat mounted helper "
            f"scripts and schemas as read-only specs. Last error: {_preview(error, 500)}"
        )
    return _add_repair_required_next(
        updated,
        repair_guidance,
    )


def _record_active_repair_target(
    state: Any,
    active: dict[str, Any],
    file_path: str,
    payload: dict[str, Any],
    tool_args: dict[str, Any] | None = None,
) -> None:
    target_path = _sandbox_write_target_path(tool_args or {}) if file_path.endswith("sandbox_write_file.py") else ""
    active["status"] = "running"
    active["last_repair_target"] = {
        "file_path": file_path,
        "artifact_hint": "accountable protocol outputs",
        "required_action": "agent_plan_repair",
        "error": str(payload.get("error") or payload.get("stderr") or payload.get("error_type") or "sandbox workflow error"),
    }
    if target_path:
        active["last_repair_target"]["target_path"] = target_path
    state[ACTIVE_SANDBOX_STATE_KEY] = active


def _successful_exec_clears_repair_target(active: dict[str, Any]) -> bool:
    repair = active.get("last_repair_target")
    if not isinstance(repair, dict):
        return True
    source = _normalize_skill_path(str(repair.get("file_path") or ""))
    # A helper rerun is not proof that a validator/finalizer error is fixed.
    # Keep those repair targets alive until validate/finalize succeeds.
    if source in {"scripts/sandbox_finalize.py", "scripts/validate_outputs.py"}:
        return False
    return True


def _repair_target_requires_rewrite(repair: dict[str, Any]) -> bool:
    source = _normalize_skill_path(str(repair.get("file_path") or ""))
    if source != "scripts/sandbox_write_file.py":
        return False
    target_path = _normalize_skill_path(str(repair.get("target_path") or ""))
    if target_path and target_path != WORKFLOW_PRODUCER_PATH:
        return False
    error = str(repair.get("error") or "").lower()
    return "protocol producer" in error or "required protocol output" in error


def _repair_target_next_action(repair: dict[str, Any]) -> str:
    artifact = str(repair.get("artifact_hint") or repair.get("producer_hint") or "accountable protocol outputs")
    error = _preview(str(repair.get("error") or ""), 350)
    if "evidence/index.json is required" in error.lower():
        return (
            "The latest sandbox result is actionable missing-evidence repair feedback. Do not answer the user and "
            "do not rerun finalization yet. Create exact evidence chunks plus `evidence/index.json`, mark only loaded "
            "chunks as loaded, then reconcile candidates/final evidence refs before validate/finalize. Last error: "
            f"{error}"
        )
    if repair.get("producer_rerun_status") == "success_unvalidated":
        return (
            "The repaired helper/script has already been rerun successfully. Do not patch it again from the stale "
            "error alone. Validate the regenerated protocol outputs with scripts/validate_outputs.py or "
            f"sandbox_finalize.py; only repair {artifact} again if the fresh validator/finalizer result still "
            f"reports a concrete error. Previous error: {error}"
        )
    if _repair_target_requires_rewrite(repair):
        return (
            "The latest sandbox result is actionable repair feedback. Do not answer the user yet. "
            f"Treat this as rejected initial helper/artifact creation: create or rewrite {artifact} with "
            "scripts/sandbox_write_file.py using corrected full source. Do not patch a missing file and do not "
            "modify host workflow code. Rerun the focused helper or validation command, then "
            f"validate/finalize. Last error: {error}"
        )
    return (
        "The latest sandbox result is actionable repair feedback. Do not answer the user yet. "
        "Treat guardrail payloads as unsatisfied requirements, not instructions for a fixed next tool. "
        "Choose the missing prerequisite from the facts in session context, then record and execute a coherent "
        "planned_next_tool that can satisfy it. "
        "Load sandbox-extraction-debugger, classify the failure as evidence/agent reasoning, helper serialization, "
        f"or helper discovery for {repair.get('file_path')}, then repair {artifact} from loaded evidence. "
        f"Rerun the focused helper or validation command, then validate/finalize. Last error: {error}"
    )


def _planned_next_tool_model_replacement(state: Any, llm_response: LlmResponse) -> LlmResponse | None:
    if _llm_response_has_function_call(llm_response):
        return None
    text = _llm_response_text(llm_response)
    if not text.strip():
        return None
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY) if _is_state_like(state) else None
    if not isinstance(context, dict):
        return None
    planned = context.get("planned_next_tool")
    if not isinstance(planned, dict):
        return None
    tool_name = str(planned.get("tool_name") or "").strip()
    if not tool_name or tool_name == "update_extraction_context":
        return None
    args = _planned_next_tool_call_args(planned, state)
    if args is None:
        return None
    return LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[_synthetic_function_call_part(name=tool_name, args=args)],
        )
    )


def _planned_next_tool_call_args(planned: dict[str, Any], state: Any) -> dict[str, Any] | None:
    tool_name = str(planned.get("tool_name") or "").strip()
    args = {str(key): value for key, value in planned.items() if key != "tool_name" and value is not None}
    if tool_name == "run_skill_script":
        args.setdefault("skill_name", "sandbox-page-analyst")
        file_path = _normalize_skill_path(str(args.get("file_path") or ""))
        if not file_path:
            return None
        args["file_path"] = file_path
        if file_path == "scripts/sandbox_start.py" and not args.get("args"):
            sandbox_args = _workflow_start_args_from_state(state)
            if sandbox_args:
                args["args"] = sandbox_args
    return args


def _workflow_start_args_from_state(state: Any) -> list[str]:
    page = state.get(LAST_PAGE_WORKSPACE_STATE_KEY) if _is_state_like(state) else None
    page_artifact = ""
    source_url = ""
    if isinstance(page, dict):
        page_artifact = str(page.get("artifact_path") or page.get("page_artifact") or page.get("page_id") or "")
        source_url = str(page.get("url") or "")
    args = ["--mode", "workflow"]
    if page_artifact:
        args.extend(["--page-artifact", page_artifact])
    if source_url:
        args.extend(["--source-url", source_url])
    return args


def _active_repair_target_model_replacement(state: Any, llm_response: LlmResponse) -> LlmResponse | None:
    active = state.get(ACTIVE_SANDBOX_STATE_KEY) if _is_state_like(state) else None
    if not isinstance(active, dict):
        return None
    if str(active.get("status") or "running") != "running":
        return None
    repair = active.get("last_repair_target")
    if not isinstance(repair, dict):
        return None
    if str(repair.get("required_action") or "") not in {
        "debug_repair_extractor",
        "debug_repair_protocol_outputs",
        "agent_plan_repair",
    }:
        return None
    if _recent_context_update_policy_block(state):
        return None
    if _llm_response_has_function_call(llm_response):
        return None
    text = _llm_response_text(llm_response)
    if not text.strip():
        return None

    audit_id = str(active.get("audit_id") or repair.get("audit_id") or "")
    error = str(repair.get("error") or "recoverable sandbox workflow error")
    source = str(repair.get("file_path") or "sandbox workflow")
    if _repair_target_requires_rewrite(repair):
        extraction_plan = (
            "Decide the next repair action from the latest validator/finalizer error and record it in "
            "planned_next_tool before taking another state-changing action."
        )
        immediate_goal = (
            "Update session context with the agent-chosen repair plan and exact planned_next_tool. The runtime "
            "has recorded the active failure but is not choosing the repair tool for the agent."
        )
    else:
        extraction_plan = (
            "Classify the active failure as evidence/agent-reasoning, helper serialization, or helper discovery "
            "before choosing the next repair action."
        )
        immediate_goal = (
            "Update session context with the agent-chosen repair plan and exact planned_next_tool. The runtime "
            "has recorded the active failure but is not choosing the repair tool for the agent."
        )
    return LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                _synthetic_function_call_part(
                    name="update_extraction_context",
                    args={
                        "audit_id": audit_id,
                        "status": "repairing",
                        "known_errors": [
                            f"Recoverable sandbox workflow error from {source}: {_preview(error, 500)}",
                        ],
                        "attempted_actions": [
                            "Model attempted to answer while a recoverable protocol repair target was active; runtime blocked the final text.",
                        ],
                        "extraction_plan": [
                            extraction_plan,
                        ],
                        "immediate_goal": immediate_goal,
                    },
                )
            ],
        )
    )


def _active_sandbox_model_replacement(state: Any, llm_response: LlmResponse) -> LlmResponse | None:
    active = state.get(ACTIVE_SANDBOX_STATE_KEY) if _is_state_like(state) else None
    if not isinstance(active, dict):
        return None
    if str(active.get("status") or "running") != "running":
        return None
    if str(active.get("mode") or "workflow") != "workflow":
        return None
    if _recent_context_update_policy_block(state):
        return None
    if _llm_response_has_function_call(llm_response):
        return None
    text = _llm_response_text(llm_response)
    if not text.strip():
        return None

    audit_id = str(active.get("audit_id") or "")
    pending = _pending_scripts_for_active_sandbox(state, active)
    if pending:
        immediate_goal = (
            f"A pending sandbox helper/script exists at {pending.get('path') or 'output/extractor.py'}. "
            "Update session context with the agent-chosen next tool before answering."
        )
    else:
        immediate_goal = (
            "The workflow sandbox is still running and has not finalized. Update session context with the "
            "agent-chosen next tool: usually validation/finalization when outputs exist, or an evidence/helper "
            "repair when the latest state proves one is needed."
        )

    return LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                _synthetic_function_call_part(
                    name="update_extraction_context",
                    args={
                        "audit_id": audit_id,
                        "status": "in_progress",
                        "known_errors": [
                            "Model attempted to answer while the workflow sandbox was still running and not finalized.",
                        ],
                        "attempted_actions": [
                            "Runtime blocked a premature final text response because no successful sandbox finalization was recorded.",
                        ],
                        "immediate_goal": immediate_goal,
                    },
                )
            ],
        )
    )


def _recent_context_update_policy_block(state: Any) -> bool:
    if not _is_state_like(state):
        return False
    immediate = state.get(IMMEDIATE_ERROR_REPEAT_STATE_KEY)
    if isinstance(immediate, dict):
        if (
            str(immediate.get("tool_name") or "") == "update_extraction_context"
            and str(immediate.get("error_type") or "") == "extraction_context_update_policy"
        ):
            return True
    guard = state.get(EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY)
    if isinstance(guard, dict) and int(guard.get("consecutive_count") or 0) >= 2:
        return True
    return False


def _synthetic_function_call_part(name: str, args: dict[str, Any]) -> genai_types.Part:
    """Create a runtime tool call id that ADK will not strip before LiteLLM."""

    # ADK strips its own `adk-...` ids before building LiteLLM messages. These
    # runtime-generated calls must retain ids so the following tool response can
    # become a valid OpenAI `tool_call_id`.
    return genai_types.Part(
        function_call=genai_types.FunctionCall(
            id=f"call_runtime_{uuid.uuid4().hex}",
            name=name,
            args=args,
        )
    )


def _llm_response_has_function_call(llm_response: LlmResponse) -> bool:
    content = getattr(llm_response, "content", None)
    if not content:
        return False
    for part in content.parts or []:
        if getattr(part, "function_call", None):
            return True
    return False


def _llm_response_text(llm_response: LlmResponse) -> str:
    content = getattr(llm_response, "content", None)
    if not content:
        return ""
    chunks: list[str] = []
    for part in content.parts or []:
        text = getattr(part, "text", None)
        if text:
            chunks.append(str(text))
    return "\n".join(chunks)


def _record_last_page_workspace(tool_context: Any, result: dict[str, Any]) -> None:
    if not isinstance(result, dict) or result.get("status") != "success":
        return
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return

    page_id = str(result.get("page_id") or "")
    artifact_path = str(result.get("artifact_path") or "")
    url = str(result.get("url") or "")
    if not page_id and not artifact_path:
        return

    artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
    metadata_artifact = result.get("metadata_artifact") if isinstance(result.get("metadata_artifact"), dict) else {}
    state[LAST_PAGE_WORKSPACE_STATE_KEY] = {
        "page_id": page_id,
        "url": url,
        "artifact_path": artifact_path,
        "page_artifact": str(artifact.get("artifact_name") or ""),
        "metadata_artifact": str(metadata_artifact.get("artifact_name") or ""),
    }


def _workflow_requires_sandbox_start_error(tool_context: Any) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _workflow_start_guard_active(state):
        return None
    return {
        "status": "error",
        "error_type": "workflow_sandbox_not_started",
        "guardrail": "workflow_requires_sandbox_start",
        "written_count": 0,
        "count": 0,
        "error": (
            "Workflow mode is loaded and a page artifact is available, but no workflow sandbox has been started. "
            "Persistence/query tools are blocked until sandbox extraction runs."
        ),
        "required_next": _workflow_start_required_next(state),
    }


def _record_finalized_sandbox_for_promotion(state: Any, active: dict[str, Any], payload: dict[str, Any]) -> None:
    audit_id = str(payload.get("audit_id") or active.get("audit_id") or "")
    if not audit_id:
        return
    artifact_handles = _versioned_artifact_handles_for_audit(state, audit_id)
    state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = {
        "audit_id": audit_id,
        "status": "finalized",
        "promotion_status": "pending",
        "query_status": "pending",
        "written_count": 0,
        "proposal_paths": _proposal_paths_from_finalize_payload(payload),
        "artifact_handles": artifact_handles,
    }


def _record_promotion_result(tool_context: Any, result: dict[str, Any]) -> None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state) or not isinstance(result, dict):
        return
    pending = state.get(FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
    if not isinstance(pending, dict):
        return
    audit_id = str(result.get("audit_id") or pending.get("audit_id") or "")
    if audit_id and str(pending.get("audit_id") or "") not in {"", audit_id}:
        return
    if result.get("status") != "success" or int(result.get("written_count") or 0) <= 0:
        pending["promotion_status"] = "error"
        pending["last_error"] = str(result.get("error") or "promotion did not write jobs")
        state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = pending
        return
    pending["audit_id"] = audit_id
    pending["promotion_status"] = "success"
    pending["written_count"] = int(result.get("written_count") or 0)
    pending["validated_count"] = int(result.get("validated_count") or 0)
    artifact_handles = _versioned_artifact_handles_for_audit(state, audit_id)
    if artifact_handles:
        pending["artifact_handles"] = artifact_handles
    state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = pending


def _record_query_jobs_result(tool_context: Any, result: dict[str, Any]) -> None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state) or not isinstance(result, dict):
        return
    pending = state.get(FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
    if not isinstance(pending, dict) or pending.get("promotion_status") != "success":
        return
    if result.get("status") != "success":
        pending["query_status"] = "error"
        pending["last_error"] = str(result.get("error") or "query_jobs did not verify saved jobs")
        state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = pending
        return
    pending["query_status"] = "success"
    pending["queried_count"] = int(result.get("count") or 0)
    state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = pending


def _proposal_paths_from_finalize_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    sources = payload.get("artifact_sources")
    if not isinstance(sources, list):
        return []

    proposal_paths: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        key = str(source.get("key") or "")
        source_path = str(source.get("source_path") or "")
        artifact_name = str(source.get("artifact_name") or "")
        searchable = " ".join((key, source_path, artifact_name))
        if "reference_proposal" not in searchable and "skill_patch" not in searchable:
            continue

        workspace_path = _workspace_relative_output_path(source_path)
        identity = workspace_path or artifact_name or key
        if not identity or identity in seen:
            continue
        seen.add(identity)
        proposal_paths.append(
            {
                "key": key,
                "workspace_path": workspace_path,
                "adk_artifact_name": _safe_adk_artifact_name(artifact_name) if artifact_name else "",
            }
        )
    return proposal_paths


def _workspace_relative_output_path(source_path: str) -> str:
    marker = "/output/"
    if marker not in source_path:
        return ""
    return "output/" + source_path.rsplit(marker, 1)[1]


def _finalized_sandbox_persistence_guard(state: Any) -> dict[str, Any] | None:
    if not _is_state_like(state):
        return None
    pending = state.get(FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
    if not isinstance(pending, dict):
        return None
    if pending.get("promotion_status") == "success" and pending.get("query_status") == "success":
        return None
    return pending


def _inject_finalized_sandbox_persistence_guard(llm_request: LlmRequest, state: Any) -> None:
    pending = _finalized_sandbox_persistence_guard(state)
    if not pending:
        return
    audit_id = str(pending.get("audit_id") or "")
    if pending.get("promotion_status") != "success":
        message = (
            f"Sandbox audit {audit_id} has finalized, but jobs are not saved to the database yet. "
            "Call promote_sandbox_extraction with this audit_id, then call query_jobs to verify saved rows. "
            "Do not report extracted_job_count or persistence success until promotion succeeds."
        )
    else:
        written_count = int(pending.get("written_count") or 0)
        message = (
            f"Sandbox audit {audit_id} was promoted with written_count={written_count}, but saved jobs have not "
            "been verified with query_jobs. Call query_jobs before the final response."
        )
    llm_request.contents.append(
        genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_text(
                    text=(
                        "<RUNTIME_PERSISTENCE_GUARD>\n"
                        "purpose: distinguish finalized sandbox artifacts from database-saved jobs.\n"
                        "priority: hard operational constraint.\n"
                        "usage: obey this before final response; finalized artifacts are not saved jobs.\n"
                        f"message: {message}\n"
                        "state_json:\n"
                        + json.dumps(pending, ensure_ascii=True, sort_keys=True, default=str)
                        + "\n</RUNTIME_PERSISTENCE_GUARD>"
                    )
                )
            ],
        )
    )


def _inject_final_response_contract(llm_request: LlmRequest, state: Any) -> None:
    if not _is_state_like(state):
        return
    pending = state.get(FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
    if not isinstance(pending, dict):
        return
    if pending.get("promotion_status") != "success" or pending.get("query_status") != "success":
        return

    llm_request.contents.append(
        genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_text(
                    text=(
                        "<RUNTIME_FINAL_RESPONSE_CONTRACT>\n"
                        "purpose: enforce the final user-facing report shape after extraction persistence is verified.\n"
                        "priority: hard response contract.\n"
                        "usage: answer the user now unless there is a new blocker. Keep it short.\n"
                        "required_fields:\n"
                        "- extracted_job_count: use written_count/validated_count, cross-checked with queried_count.\n"
                        "- proposal_paths: include ADK artifact names and versions for reference/skill proposal files when available.\n"
                        "- artifact_handles: include versioned ADK artifact handles for final/candidates/page_profile/extraction_strategy/validation when available.\n"
                        "- summary: 1-3 short sentences describing what happened.\n"
                        "forbidden: raw HTML, full job JSON, long stdout/stderr, command transcripts, or unrelated examples.\n"
                        "state_json:\n"
                        + json.dumps(pending, ensure_ascii=True, sort_keys=True, default=str)
                        + "\n</RUNTIME_FINAL_RESPONSE_CONTRACT>"
                    )
                )
            ],
        )
    )


def _workflow_start_guard_text_from_context(callback_context: Any) -> str:
    state = getattr(callback_context, "state", None)
    if not _workflow_start_guard_active(state):
        return ""
    return (
        "<RUNTIME_SANDBOX_START_GUARD>\n"
        "purpose: enforce the required transition from page workspace to workflow sandbox.\n"
        "priority: hard operational constraint.\n"
        "usage: obey this before persistence, query, or final answer.\n"
        "message: workflow-mode is loaded and a page artifact is available, but no workflow sandbox is active. "
        "Start the workflow sandbox before persistence, query, or final answer. "
        f"{_workflow_start_required_next(state)} "
        "After the sandbox starts, inspect mounted files, derive recurring job-post patterns, load bounded exact "
        "evidence, write only supporting scripts as needed, create accountable protocol outputs, "
        "validate, finalize, then persist and query saved jobs. Do not load diagnostic-mode.\n"
        "</RUNTIME_SANDBOX_START_GUARD>"
    )


def _workflow_start_guard_active(state: Any) -> bool:
    if not _is_state_like(state):
        return False
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if isinstance(active, dict):
        return False
    loaded = state.get(SANDBOX_MODE_RESOURCE_STATE_KEY)
    if not isinstance(loaded, dict) or loaded.get("mode") != "workflow":
        return False
    page = state.get(LAST_PAGE_WORKSPACE_STATE_KEY)
    if not isinstance(page, dict):
        return False
    return bool(page.get("artifact_path") or page.get("page_artifact") or page.get("page_id"))


def _workflow_start_required_next(state: Any) -> str:
    page = state.get(LAST_PAGE_WORKSPACE_STATE_KEY) if _is_state_like(state) else {}
    page_artifact = ""
    source_url = ""
    if isinstance(page, dict):
        page_artifact = str(page.get("artifact_path") or page.get("page_artifact") or page.get("page_id") or "")
        source_url = str(page.get("url") or "")
    args = ["--mode", "workflow"]
    if page_artifact:
        args.extend(["--page-artifact", page_artifact])
    if source_url:
        args.extend(["--source-url", source_url])
    return (
        'Use run_skill_script with skill_name "sandbox-page-analyst", '
        'file_path "scripts/sandbox_start.py", '
        f"args {json.dumps(args, ensure_ascii=True)}."
    )


def _inject_latest_tool_result(llm_request: LlmRequest) -> None:
    latest = _latest_tool_result_from_contents(llm_request.contents)
    if not latest:
        return
    llm_request.contents.append(
        genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_text(
                    text=(
                        "<LATEST_TOOL_RESULT>\n"
                        "purpose: freshest completed non-context tool/function response, or failed "
                        "update_extraction_context response, extracted from ADK event history before runtime "
                        "context blocks were injected. Successful update_extraction_context confirmations are not "
                        "included here.\n"
                        "priority: highest evidence for updating SESSION_EXTRACTION_CONTEXT before the next action.\n"
                        "usage:\n"
                        "1. Read this before SESSION_EXTRACTION_CONTEXT and RUNTIME_SANDBOX_NOTES.\n"
                        "2. If this result changes workflow state, creates or resolves known_errors, proves a plan "
                        "stale, or makes planned_next_tool wrong, call update_extraction_context before any "
                        "state-changing tool.\n"
                        "2a. If this result is a failed update_extraction_context call, correct the state payload "
                        "according to the error and rerun update_extraction_context before any non-context tool.\n"
                        "3. Copy only a compact summary into last_result; keep state concise enough to fit in one "
                        "LLM call but specific enough to choose the next efficient tool.\n"
                        "4. Do not patch, validate, finalize, persist, query, or answer until last_result reflects "
                        "this block, unless the session context is already current.\n"
                        "latest_result_json:\n"
                        + json.dumps(latest, ensure_ascii=True, sort_keys=True, default=str)
                        + "\n</LATEST_TOOL_RESULT>"
                    )
                )
            ],
        )
    )


def _latest_tool_result_from_contents(contents: list[genai_types.Content]) -> dict[str, Any] | None:
    for content in reversed(contents):
        responses: list[dict[str, Any]] = []
        for part in reversed(content.parts or []):
            function_response = getattr(part, "function_response", None)
            if not function_response:
                continue
            tool_name = str(function_response.name or "")
            payload = function_response.response or {}
            if _is_successful_context_update(tool_name, payload):
                return None
            if _skip_latest_tool_result(tool_name, payload):
                continue
            responses.append(_compact_latest_function_response(tool_name, payload))
        if not responses:
            continue
        if len(responses) == 1:
            return responses[0]
        return {
            RuntimePayloadKey.TOOL_RESULTS.value: responses,
            RuntimePayloadKey.COUNT.value: len(responses),
            RuntimePayloadKey.COMPACTED.value: True,
        }
    return None


def _skip_latest_tool_result(tool_name: str, payload: Any) -> bool:
    return _is_successful_context_update(tool_name, payload)


def _is_successful_context_update(tool_name: str, payload: Any) -> bool:
    if tool_name != "update_extraction_context" or not isinstance(payload, dict):
        return False
    return str(payload.get(RuntimePayloadKey.STATUS) or "").lower() == RuntimeStatus.SUCCESS


def _prune_loaded_resource_contexts(llm_request: LlmRequest, state: Any) -> None:
    """Remove full skill/reference text once it has had a chance to enter state."""

    responses: list[Any] = []
    for content in llm_request.contents:
        for part in content.parts or []:
            response = getattr(part, "function_response", None)
            if response:
                responses.append(response)
    if not responses:
        return

    latest_context_update_index = -1
    resource_indexes: list[int] = []
    for index, response in enumerate(responses):
        tool_name = str(response.name or "")
        payload = response.response or {}
        if _is_successful_context_update(tool_name, payload):
            latest_context_update_index = index
        if tool_name in EPHEMERAL_RESOURCE_TOOL_NAMES and isinstance(payload, dict):
            resource_indexes.append(index)

    if not resource_indexes:
        return

    resource_after_update = [index for index in resource_indexes if index > latest_context_update_index]
    keep_full_index = max(resource_after_update) if resource_after_update else None
    if latest_context_update_index < 0 and keep_full_index is None:
        keep_full_index = max(resource_indexes)

    context_updated = _is_state_like(state) and isinstance(state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY), dict)
    for index in resource_indexes:
        if index == keep_full_index:
            continue
        response = responses[index]
        payload = response.response or {}
        if not isinstance(payload, dict) or _is_compacted_resource_payload(payload):
            continue
        response.response = _loaded_resource_placeholder(
            tool_name=str(response.name or ""),
            payload=payload,
            compacted_after_context_update=latest_context_update_index >= index or bool(context_updated),
        )


def _is_compacted_resource_payload(payload: dict[str, Any]) -> bool:
    return str(payload.get(RuntimePayloadKey.STATUS) or "") in {
        RuntimeStatus.RESOURCE_CONTEXT_REMOVED_AFTER_STATE_UPDATE,
        RuntimeStatus.RESOURCE_CONTEXT_COMPACTED_KEEP_LATEST_ONLY,
    }


def _loaded_resource_placeholder(
    *,
    tool_name: str,
    payload: dict[str, Any],
    compacted_after_context_update: bool,
) -> dict[str, Any]:
    status = (
        RuntimeStatus.RESOURCE_CONTEXT_REMOVED_AFTER_STATE_UPDATE.value
        if compacted_after_context_update
        else RuntimeStatus.RESOURCE_CONTEXT_COMPACTED_KEEP_LATEST_ONLY.value
    )
    placeholder: dict[str, Any] = {
        RuntimePayloadKey.STATUS.value: status,
        RuntimePayloadKey.TOOL_NAME.value: tool_name,
        RuntimePayloadKey.REASON.value: (
            "Full skill/reference text was intentionally removed from this model request after the agent had a "
            "chance to distill it into SESSION_EXTRACTION_CONTEXT."
            if compacted_after_context_update
            else "Older loaded skill/reference text was removed so only the newest full resource remains in context."
        ),
        RuntimePayloadKey.REQUIRED_NEXT.value: (
            "Reason from SESSION_EXTRACTION_CONTEXT and the current plan. If the discarded resource is needed for "
            "a specific uncertainty, reload that skill/resource, update_extraction_context with the distilled "
            "observations and plan changes, then continue."
        ),
        RuntimePayloadKey.RESOURCE_DISCARDED_FROM_CONTEXT.value: True,
    }
    for key in RESOURCE_PLACEHOLDER_KEYS:
        if key in payload:
            placeholder[key] = _compact_latest_value(payload[key], 500)
    if RuntimePayloadKey.STATUS in payload:
        placeholder[RuntimePayloadKey.ORIGINAL_STATUS.value] = _compact_latest_value(
            payload[RuntimePayloadKey.STATUS],
            120,
        )
    content_value = next((payload[key] for key in RESOURCE_TEXT_PAYLOAD_KEYS if payload.get(key)), None)
    if content_value is not None:
        content_text = str(content_value)
        placeholder[RuntimePayloadKey.ORIGINAL_CHARS.value] = len(content_text)
        placeholder[RuntimePayloadKey.CONTENT_PREVIEW.value] = _preview(content_text, 500)
        placeholder[RuntimePayloadKey.SHA256.value] = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    return placeholder


def _compact_latest_function_response(tool_name: str, payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and tool_name == ToolName.RUN_SKILL_SCRIPT:
        compact = _compact_run_skill_script_result(payload, payload, 1_000)
    elif isinstance(payload, dict):
        compact = _compact_latest_payload(payload, 1_000)
    else:
        compact = {
            RuntimePayloadKey.VALUE.value: _preview(str(payload), 1_000),
            RuntimePayloadKey.COMPACTED.value: True,
        }
    compact[RuntimePayloadKey.TOOL_NAME.value] = tool_name
    return compact


def _compact_latest_payload(payload: dict[str, Any], preview_max_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in LATEST_PAYLOAD_KEYS:
        if key in payload:
            compact[key] = _compact_latest_value(payload[key], preview_max_chars)
    for key in SANDBOX_TEXT_PREVIEW_KEYS:
        if key in payload:
            compact[key] = _preview(str(payload[key]), preview_max_chars)
    paths = _compact_output_paths(payload)
    if paths:
        compact[RuntimePayloadKey.PATHS.value] = paths
    artifact_handles = _compact_artifact_handles(payload)
    if artifact_handles:
        compact[RuntimePayloadKey.ARTIFACT_HANDLES.value] = artifact_handles
    if not compact:
        compact[RuntimePayloadKey.RESPONSE_PREVIEW.value] = _preview(
            json.dumps(payload, ensure_ascii=True, default=str),
            preview_max_chars,
        )
    compact[RuntimePayloadKey.COMPACTED.value] = True
    return compact


def _compact_latest_value(value: Any, preview_max_chars: int) -> Any:
    if isinstance(value, str):
        return _preview(value, preview_max_chars)
    if isinstance(value, list):
        return [_compact_latest_value(item, max(200, preview_max_chars // 2)) for item in value[:10]]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 20:
                compact["__truncated_keys__"] = len(value) - index
                break
            compact[str(key)] = _compact_latest_value(child, max(200, preview_max_chars // 2))
        return compact
    return value


def _inject_session_extraction_context(llm_request: LlmRequest, state: Any) -> None:
    if not _is_state_like(state):
        return
    context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    if not isinstance(context, dict) or not context.get("updated"):
        return
    llm_request.contents.append(
        genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_text(
                    text=(
                        "<SESSION_EXTRACTION_CONTEXT>\n"
                        "purpose: session-scoped working memory for this scraping task.\n"
                        "priority: commanding guide for next-step reasoning.\n"
                        "usage:\n"
                        "1. Treat final_goal as the stable workflow goal. Do not narrow or replace it unless the user "
                        "changes the task.\n"
                        "2. Treat immediate_goal as the next concrete objective needed to progress toward final_goal. "
                        "It should be specific enough to imply one efficient next tool call.\n"
                        "3. If <LATEST_TOOL_RESULT> is present, read it before this context. It is the freshest "
                        "completed non-context tool response from ADK events, except it may be a failed "
                        "update_extraction_context result that must be fixed by rerunning update_extraction_context. "
                        "If it changes workflow state or makes this context stale, call update_extraction_context "
                        "with a compact last_result, revised immediate_goal, and the next most efficient "
                        "planned_next_tool before taking another state-changing action.\n"
                        "4. After a successful update_extraction_context action, reason always and only from this "
                        "SESSION_EXTRACTION_CONTEXT until another non-context tool returns. Do not treat the update "
                        "confirmation itself as new evidence, and do not call update_extraction_context merely "
                        "because update_extraction_context succeeded.\n"
                        "5. Use observations as page/workspace facts. Treat extraction_plan as the adaptive plan "
                        "created after early evidence; initial_plan is bootstrap-only and should be rebased into "
                        "extraction_plan once evidence exists.\n"
                        "5a. Treat extraction_strategy as the detailed method derived from extraction_plan for "
                        "turning observed repeated job units into required outputs; follow it by default, enhance "
                        "it when new evidence adds field/pattern detail, and revise it when new evidence or "
                        "validation/finalization contradicts it.\n"
                        "5b. Treat immediate_goal as the current bounded step inside extraction_strategy. "
                        "initial_plan is not enough to write or run output/extractor.py. Before producer "
                        "scripting, immediate_goal must state the current step with evidence, strategy, "
                        "validation, and next script/probe objective. For the first ITviec fixture goal, establish "
                        "and validate the repeated job-card unit boundary before field extraction or persistence "
                        "claims. Good immediate_goal example: Establish repeated job-card unit boundary for the "
                        "fixed ITviec fixture. Evidence: fixed page artifact, representative repeated card markup/text, "
                        "and bounded selector/count evidence. Strategy: target one repeated job-card unit per "
                        "in-scope listing using [data-search--pagination-target='jobCard'] and exclude "
                        "navigation/company preview links. Validation: run a bounded count probe and pass only "
                        "when the count is 20 for the fixture. Next script objective: write the smallest probe "
                        "that counts repeated job units and records the unit boundary.\n"
                        "6. Use known_errors as active blockers only. If latest results show an error is solved, call "
                        "update_extraction_context with known_errors rewritten without that stale error.\n"
                        "7. Check attempted_actions before acting. Do not repeat actions that did not change state; "
                        "choose a state-changing repair, validation, finalization, promotion, or query action instead.\n"
                        "8. If required protocol outputs exist and no concrete validation/finalization error is active, "
                        "the next objective is validation/finalization, not another helper rewrite.\n"
                        "9. If planned_next_tool is present, the next tool call must match it. If the plan is stale, "
                        "first update this context with the new evidence, a revised immediate_goal, and replacement "
                        "planned_next_tool.\n"
                        "10. Treat required_outputs, workflow_contract, and expected_output as hard workflow invariants. Before starting "
                        "a workflow sandbox, writing helper/protocol files, validating, finalizing, or persisting, "
                        "verify the contract says the agent chooses and owns the extraction method, supporting scripts "
                        "are recorded when authored, and every nontrivial field cites evidence when evidence is chunked. Before "
                        "writing output/candidates.json or output/final.json, expected_output.expected_job_count "
                        "must match the repeated units you observed, with count_basis recorded. "
                        "expected_output.available_fields and expected_output.field_basis must declare which "
                        "metadata fields were observed as available; successful outputs cannot use placeholders "
                        "for fields marked required_observed.\n"
                        "10a. Before writing an output/*.py producer script or successful protocol result file, "
                        "load scripts/protocol_contract.py and update this context with output_contract plus your "
                        "own producer_output_plan. The policy supplies the contract only; the agent must decide the "
                        "extraction method, evidence plan, script manifest plan, and validation sequence.\n"
                        "11. If repair_scope is present, treat it as the bounded work order for the current repair: "
                        "load only allowed_resources, patch only files, and use sandbox_read.py for bounded reads "
                        "of sandbox files when inspection helps the next repair decision. When "
                        "status is ready_to_verify/verifying run the declared verification command before changing "
                        "scope.\n"
                        "12. Treat workflow_reflections as learned interpretations of prior failure patterns, not "
                        "fixed tool recipes. Apply them when choosing or revising planned_next_tool, especially when "
                        "the latest error matches a reflection trigger.\n"
                        "13. Skill and reference resource text is temporary context, not durable memory. After loading "
                        "one, distill the relevant workflow cues, observations, and plan changes into this context; "
                        "older full resource text may be discarded from future model requests. Reload a specific "
                        "resource only when exact wording is needed to resolve uncertainty, then update this context "
                        "again.\n"
                        "14. Before every non-context tool call and final response, reconcile <LATEST_TOOL_RESULT> "
                        "when present, "
                        "final_goal, immediate_goal, "
                        "last_result, known_errors, attempted_actions, observations, extraction_strategy, extraction_plan, and "
                        "planned_next_tool. Call update_extraction_context whenever any of those fields should "
                        "change because of non-context evidence, or when the previous update_extraction_context "
                        "failed and the state payload must be corrected. "
                        "Do not treat this as a reusable site reference.\n"
                        "context_json:\n"
                        + json.dumps(_compact_session_context(context), ensure_ascii=True, sort_keys=True, default=str)
                        + "\n</SESSION_EXTRACTION_CONTEXT>"
                    )
                )
            ],
        )
    )


def _compact_session_context(context: dict[str, Any]) -> dict[str, Any]:
    if SessionContextKey.IMMEDIATE_GOAL not in context and context.get("next_focus"):
        context = {**context, SessionContextKey.IMMEDIATE_GOAL.value: context.get("next_focus")}
    include_initial_plan = not bool(context.get(SessionContextKey.EXTRACTION_PLAN))
    compact: dict[str, Any] = {}
    for key in SESSION_CONTEXT_COMPACT_KEYS:
        if key == SessionContextKey.INITIAL_PLAN and not include_initial_plan:
            continue
        if key not in context:
            continue
        value = context[key]
        if key == SessionContextKey.WORKFLOW_REFLECTIONS and isinstance(value, list):
            compact[key] = [item for item in value[-6:] if isinstance(item, dict)]
        elif isinstance(value, list):
            compact[key] = [_preview(str(item), 500) for item in value[-8:]]
        elif isinstance(value, dict):
            compact[key] = value
        else:
            compact[key] = _preview(str(value), 500)
    return compact


def _sandbox_exec_command(tool_args: dict[str, Any]) -> str:
    args = tool_args.get("args")
    if not isinstance(args, list):
        return ""
    string_args = [str(item) for item in args]
    for option in ("--command", "--cmd"):
        value = _option_value(string_args, option)
        if value:
            return value
    if "--" in string_args:
        index = string_args.index("--")
        return " ".join(string_args[index + 1 :])
    return ""


def _normalize_skill_path(path: str) -> str:
    return path.lstrip("./")


def _option_value(args: list[str], option: str) -> str:
    aliases = {option, option.replace("-", "_")}
    for index, item in enumerate(args):
        if item in aliases and index + 1 < len(args):
            return args[index + 1]
    return ""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _parse_skill_script_stdout(result: dict[str, Any]) -> dict[str, Any]:
    stdout = result.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        return result
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return result
    return parsed if isinstance(parsed, dict) else result


def _promote_script_payload_error(result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get(RuntimePayloadKey.STATUS) or "").lower()
    has_error = status in {RuntimeStatus.ERROR, "blocked", RuntimeStatus.GUARDRAIL_TRIGGERED} or bool(
        payload.get(RuntimePayloadKey.ERROR) or payload.get(RuntimePayloadKey.ERROR_TYPE)
    )
    if not has_error:
        return result

    updated = dict(result)
    for key in PROMOTED_SCRIPT_PAYLOAD_ERROR_KEYS:
        if key in payload:
            updated[key] = payload[key]
    if RuntimePayloadKey.TOOL_STATUS not in updated and result.get(RuntimePayloadKey.STATUS) and result.get(RuntimePayloadKey.STATUS) != updated.get(RuntimePayloadKey.STATUS):
        updated[RuntimePayloadKey.TOOL_STATUS.value] = result[RuntimePayloadKey.STATUS]
    return updated


def _add_required_next(result: dict[str, Any], required_next: str) -> dict[str, Any]:
    updated = dict(result)
    updated["required_next"] = required_next
    updated["premature_final_answer_policy"] = (
        "Do not answer the user yet. The sandbox is running and must be inspected, "
        "protocol outputs must be written, validation must pass, and sandbox_finalize.py must run first."
    )
    return updated


def _add_repair_required_next(result: dict[str, Any], required_next: str) -> dict[str, Any]:
    updated = dict(result)
    updated["required_next"] = required_next
    updated["repair_error_policy"] = (
        "Returned errors are actionable repair feedback. Correct the failed stage and retry before summarizing, "
        "unless the error proves the task is blocked or a guardrail has stopped execution."
    )
    return updated


def _persistence_guard_error(error: str, required_next: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": error,
        "written_count": 0,
        "required_next": required_next,
        "repair_error_policy": (
            "Persistence was blocked before any database write. Returned errors are actionable repair feedback; "
            "correct the failed stage and retry before summarizing, unless the sandbox is terminal or blocked."
        ),
    }


def _active_sandbox_persistence_error(tool_context: Any) -> dict[str, Any] | None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    active = state.get(ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict):
        return None
    if str(active.get("mode") or "workflow") != "workflow":
        return None

    status = str(active.get("status") or "running")
    audit_id = str(active.get("audit_id") or "")
    pending = _pending_scripts_for_active_sandbox(state, active)
    if pending:
        return _pending_script_policy_error(
            active,
            pending,
            "persistence requires the written script to run and produce protocol artifacts first",
        )
    if status in {"finalized", "success"}:
        return None
    if status == "guardrail_triggered":
        return _persistence_guard_error(
            f"sandbox {audit_id or '<unknown>'} is guardrail_triggered, not finalized",
            (
                "Do not persist. Report the guardrail blocker with audit_id, or restart the sandbox with a narrower "
                "extraction plan. Persistence is allowed only after successful sandbox finalization."
            ),
        )
    return _persistence_guard_error(
        f"sandbox {audit_id or '<unknown>'} is {status}, not finalized",
        (
            "Do not persist yet. Continue the sandbox workflow: repair protocol outputs, run validation, and call "
            "sandbox_finalize.py successfully before retrying persistence."
        ),
    )


def _audit_status_persistence_error(extraction: dict[str, Any]) -> dict[str, Any] | None:
    audit_id = _extract_audit_id_from_payload(extraction)
    if not audit_id:
        return None
    try:
        record = SandboxRegistry(DEFAULT_SANDBOX_APP_ROOT).load("user", "local", audit_id)
    except Exception as exc:
        return _persistence_guard_error(
            f"could not verify sandbox audit {audit_id}: {exc}",
            (
                "Do not persist model-supplied sandbox payloads when the sandbox registry cannot be verified. "
                "Return the audit blocker or rerun the sandbox workflow."
            ),
        )
    if record.status != "finalized":
        return _persistence_guard_error(
            f"sandbox {audit_id} is {record.status}, not finalized",
            (
                "Do not persist. Report the sandbox status and audit_id as a blocker, or rerun/repair the sandbox "
                "until sandbox_finalize.py succeeds."
            ),
        )
    return None


def _coerce_extraction_payload(extraction: dict[str, Any]) -> dict[str, Any]:
    payload = extraction
    if isinstance(payload.get("result"), dict) and not isinstance(payload.get("jobs"), list):
        payload = payload["result"]
    if isinstance(payload.get("job_extraction"), dict) and not isinstance(payload.get("jobs"), list):
        payload = payload["job_extraction"]
    coerced = dict(payload)
    if "source" not in coerced and (coerced.get("source_name") or coerced.get("source_url")):
        coerced["source"] = {
            "source_name": coerced.get("source_name"),
            "source_url": coerced.get("source_url"),
        }
    return coerced


def _extract_audit_id_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("audit_id",):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("sandbox_run_"):
                return value
        audit = payload.get("audit")
        if isinstance(audit, dict):
            value = audit.get("audit_id")
            if isinstance(value, str) and value.startswith("sandbox_run_"):
                return value
        for value in payload.values():
            found = _extract_audit_id_from_payload(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _extract_audit_id_from_payload(item)
            if found:
                return found
    return ""


def _is_plain_text_response(llm_response: LlmResponse) -> bool:
    content = llm_response.content
    if not content or not content.parts:
        return False
    has_text = False
    for part in content.parts:
        if getattr(part, "function_call", None):
            return False
        if getattr(part, "text", None):
            has_text = True
    return has_text


def _sandbox_status_command() -> str:
    return (
        "python - <<'PY'\n"
        "from pathlib import Path\n"
        "import json\n"
        "required = [\n"
        "  'output/page_profile.json',\n"
        "  'output/extraction_strategy.json',\n"
        "  'output/extraction_run.json',\n"
        "  'output/candidates.json',\n"
        "  'output/validation.json',\n"
        "  'output/final.json',\n"
        "  'output/run_summary.md',\n"
        "  'output/reference_proposal.md',\n"
        "  'output/reference_proposal.json',\n"
        "]\n"
        "missing = [p for p in required if not Path(p).exists()]\n"
        "page_files = [str(p) for p in Path('.').glob('*.html')]\n"
        "payload = {\n"
        "  'status': 'sandbox_running',\n"
        "  'page_files': page_files,\n"
        "  'missing_required_outputs': missing,\n"
        "  'required_next': 'Continue the sandbox workflow: inspect page evidence, derive patterns, write supporting scripts or outputs with accountable run artifacts, validate, then finalize.',\n"
        "  'allowed_parser_dependencies': 'Python standard library unless verified installed inside sandbox',\n"
        "}\n"
        "print(json.dumps(payload, ensure_ascii=True))\n"
        "PY"
    )


def _active_sandbox_from_contents(contents: list[genai_types.Content]) -> dict[str, Any] | None:
    active: dict[str, Any] | None = None
    for content in contents:
        for part in content.parts or []:
            response = getattr(part, "function_response", None)
            if not response or response.name != "run_skill_script":
                continue
            payload = response.response or {}
            if not isinstance(payload, dict) or payload.get("skill_name") != "sandbox-page-analyst":
                continue
            file_path = str(payload.get("file_path") or "")
            stdout_payload = _parse_skill_script_stdout(payload)
            status = str(stdout_payload.get("status") or payload.get("status") or "")
            if file_path.endswith("sandbox_start.py") and status == "running":
                active = {
                    "audit_id": str(stdout_payload.get("audit_id") or ""),
                    "mode": str(stdout_payload.get("mode") or "workflow"),
                    "command_count": 0,
                }
            elif active and file_path.endswith("sandbox_exec.py"):
                active["command_count"] = int(stdout_payload.get("command_index") or active.get("command_count") or 0)
                if status == "guardrail_triggered":
                    active = None
            elif active and file_path.endswith("sandbox_finalize.py"):
                if status in {"finalized", "success", "guardrail_triggered"}:
                    active = None
    if active and active.get("audit_id"):
        return active
    return None
