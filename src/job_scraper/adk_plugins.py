from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types as genai_types

from job_scraper.sandbox_terminal import SandboxRegistry
from job_scraper.runtime_state import SESSION_EXTRACTION_CONTEXT_STATE_KEY
from job_scraper.tool_policy import ToolActionKind, resolve_tool_policy
from sandbox_page_analyst.runtime import validate_job_extraction_payload


ACTIVE_SANDBOX_STATE_KEY = "_job_scraper_active_sandbox"
LAST_PAGE_WORKSPACE_STATE_KEY = "_job_scraper_last_page_workspace"
SANDBOX_PENDING_SCRIPT_STATE_KEY = "_job_scraper_pending_sandbox_scripts"
SANDBOX_REPEAT_GUARD_STATE_KEY = "_job_scraper_repeat_guard"
SANDBOX_MODE_RESOURCE_STATE_KEY = "_job_scraper_sandbox_mode_resource"
SANDBOX_SITE_RESOURCE_STATE_KEY = "_job_scraper_sandbox_site_resources"
SANDBOX_NOTE_BUFFER_STATE_KEY = "_job_scraper_sandbox_note_buffer"
SANDBOX_NOTES_STATE_KEY = "_job_scraper_sandbox_notes"
SANDBOX_NOTE_ERROR_STATE_KEY = "_job_scraper_sandbox_note_errors"
SANDBOX_SUMMARIZED_COMMANDS_STATE_KEY = "_job_scraper_sandbox_summarized_commands"
EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY = "_job_scraper_extraction_context_update_guard"
SANDBOX_TOOL_BUDGET_STATE_KEY = "_job_scraper_sandbox_tool_budget"
SANDBOX_READ_GUARD_STATE_KEY = "_job_scraper_sandbox_read_guard"
INSPECTION_REPEAT_GUARD_STATE_KEY = "_job_scraper_inspection_repeat_guard"
IMMEDIATE_ERROR_REPEAT_STATE_KEY = "_job_scraper_immediate_error_repeat"
SANDBOX_ARTIFACT_HANDLES_STATE_KEY = "_job_scraper_sandbox_artifact_handles"
FINALIZED_SANDBOX_PROMOTION_STATE_KEY = "_job_scraper_finalized_sandbox_promotion"
INITIAL_CONTEXT_REQUIRED_TOOLS = {
    "list_skills",
    "load_skill",
    "fetch_page",
    "render_page",
    "fetch_page_to_workspace",
    "render_page_to_workspace",
    "promote_sandbox_extraction",
    "upsert_job",
    "record_crawl_run",
    "query_jobs",
    "list_seed_references",
}
DEFAULT_SANDBOX_APP_ROOT = Path(__file__).resolve().parent
DEFAULT_NOTE_REFINEMENT_MODEL = os.getenv("SANDBOX_NOTE_REFINEMENT_MODEL", os.getenv("OPENAI_MODEL", "openai/gpt-5.4-mini"))
MAX_WORKFLOW_SANDBOX_TOOL_CALLS = int(os.getenv("JOB_SCRAPER_MAX_WORKFLOW_SANDBOX_TOOL_CALLS", "20"))
MODEL_RETRY_MAX_ATTEMPTS = int(os.getenv("JOB_SCRAPER_MODEL_RETRY_MAX_ATTEMPTS", "6"))
MODEL_RETRY_BASE_DELAY_SECONDS = float(os.getenv("JOB_SCRAPER_MODEL_RETRY_BASE_DELAY_SECONDS", "15"))
MODEL_RETRY_MAX_DELAY_SECONDS = float(os.getenv("JOB_SCRAPER_MODEL_RETRY_MAX_DELAY_SECONDS", "90"))
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
    "output/candidates.json",
    "output/validation.json",
    "output/final.json",
)
WORKFLOW_PRODUCER_PATH = "output/extractor.py"


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


class TransientModelRetryPlugin(BasePlugin):
    """Retry transient model provider failures before ADK Web surfaces them."""

    def __init__(
        self,
        *,
        max_attempts: int = MODEL_RETRY_MAX_ATTEMPTS,
        base_delay_seconds: float = MODEL_RETRY_BASE_DELAY_SECONDS,
        max_delay_seconds: float = MODEL_RETRY_MAX_DELAY_SECONDS,
        sleep: Any = asyncio.sleep,
        name: str = "transient_model_retry_plugin",
    ) -> None:
        super().__init__(name=name)
        self.max_attempts = max(1, max_attempts)
        self.base_delay_seconds = max(0.0, base_delay_seconds)
        self.max_delay_seconds = max(self.base_delay_seconds, max_delay_seconds)
        self.sleep = sleep

    async def on_model_error_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
        error: Exception,
    ) -> LlmResponse | None:
        if not _is_transient_model_error(error):
            return None

        invocation_context = getattr(callback_context, "_invocation_context", None)
        agent = getattr(invocation_context, "agent", None)
        model = getattr(agent, "model", None)
        if model is None or not callable(getattr(model, "generate_content_async", None)):
            return _model_retry_exhausted_response(
                error,
                attempts=0,
                detail="No retryable model object was available in the ADK invocation context.",
            )

        last_error: Exception = error
        retry_attempts = max(0, self.max_attempts - 1)
        for attempt in range(1, retry_attempts + 1):
            delay_seconds = self._delay_for_attempt(attempt, last_error)
            await self._sleep(delay_seconds)
            try:
                increment = getattr(invocation_context, "increment_llm_call_count", None)
                if callable(increment):
                    increment()
                async for response in model.generate_content_async(
                    llm_request,
                    stream=False,
                ):
                    return response
            except Exception as exc:
                last_error = exc
                if not _is_transient_model_error(exc):
                    return None

        return _model_retry_exhausted_response(
            last_error,
            attempts=retry_attempts,
            detail=(
                "Transient model provider errors persisted after bounded retries. "
                "Retry the user request later or lower request volume."
            ),
        )

    async def _sleep(self, delay_seconds: float) -> None:
        maybe = self.sleep(delay_seconds)
        if inspect.isawaitable(maybe):
            await maybe

    def _delay_for_attempt(self, attempt: int, error: Exception) -> float:
        hinted_delay = _retry_delay_from_error(error)
        fallback = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempt - 1)))
        if hinted_delay is None:
            return fallback
        return min(self.max_delay_seconds, max(self.base_delay_seconds, hinted_delay))


class SandboxWorkflowGuardPlugin(BasePlugin):
    """Prevent a started sandbox from becoming a premature final answer."""

    def __init__(self, *, max_forced_continuations: int = 0, name: str = "sandbox_workflow_guard_plugin") -> None:
        super().__init__(name=name)
        # Kept for compatibility with older tests/config, but the guard no
        # longer spends sandbox command budget by auto-invoking tools.
        self.max_forced_continuations = max_forced_continuations

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> dict | None:
        tool_name = getattr(tool, "name", "")
        initial_context_error = _initial_extraction_context_policy_error(tool_name, tool_args, tool_context)
        if initial_context_error:
            return initial_context_error
        workflow_contract_error = _workflow_contract_policy_error(tool_name, tool_args, tool_context)
        if workflow_contract_error:
            return workflow_contract_error
        repair_scope_error = _repair_scope_policy_error(tool_name, tool_args, tool_context)
        if repair_scope_error:
            return repair_scope_error
        planned_next_tool_error = _planned_next_tool_policy_error(tool_name, tool_args, tool_context)
        if planned_next_tool_error:
            return planned_next_tool_error
        immediate_repeat_error = _immediate_repeated_error_policy_error(tool_name, tool_args, tool_context)
        if immediate_repeat_error:
            return immediate_repeat_error
        repeated_inspection_error = _repeated_inspection_policy_error(tool_name, tool_args, tool_context)
        if repeated_inspection_error:
            return repeated_inspection_error
        terminal_error = _active_sandbox_guardrail_terminal_error(tool_name, tool_args, tool_context)
        if terminal_error:
            return terminal_error
        budget_error = _workflow_sandbox_tool_budget_error(tool_name, tool_args, tool_context)
        if budget_error:
            return budget_error

        if tool_name == "load_skill_resource":
            mode_resource_error = _sandbox_mode_resource_policy_error(tool_args, tool_context)
            if mode_resource_error:
                return mode_resource_error
            _record_sandbox_site_resource_load(tool_args, tool_context)
            return None

        if tool_name == "update_extraction_context":
            return _extraction_context_update_policy_error(tool_args, tool_context)

        if tool_name == "run_skill_script":
            site_reference_error = _site_specific_reference_policy_error(tool_args, tool_context)
            if site_reference_error:
                return site_reference_error
            script_args_error = _sandbox_skill_script_args_policy_error(tool_args, tool_context)
            if script_args_error:
                return script_args_error
            repeated_read_error = _repeated_sandbox_read_policy_error(tool_args, tool_context)
            if repeated_read_error:
                return repeated_read_error
            missing_protocol_read_error = _missing_protocol_output_read_policy_error(tool_args, tool_context)
            if missing_protocol_read_error:
                return missing_protocol_read_error
            protocol_write_error = _workflow_protocol_write_policy_error(tool_args, tool_context)
            if protocol_write_error:
                return protocol_write_error
            host_control_error = _sandbox_host_control_exec_policy_error(tool_args)
            if host_control_error:
                return host_control_error
            script_execution_error = _workflow_script_execution_policy_error(tool_args, tool_context)
            if script_execution_error:
                return script_execution_error

        if tool_name in {"record_crawl_run", "query_jobs"}:
            active_error = _active_sandbox_record_query_error(tool_context, tool_name)
            if active_error:
                return active_error
            start_error = _workflow_requires_sandbox_start_error(tool_context)
            if start_error:
                return start_error

        if tool_name == "promote_sandbox_extraction":
            start_error = _workflow_requires_sandbox_start_error(tool_context)
            if start_error:
                return start_error
            return None

        if tool_name != "persist_sandbox_job_extraction":
            return None

        active_error = _active_sandbox_persistence_error(tool_context)
        if active_error:
            return active_error

        extraction = tool_args.get("extraction")
        if not isinstance(extraction, dict):
            return _persistence_guard_error(
                "missing extraction payload; pass the finalized sandbox result.result object after sandbox finalization succeeds",
                (
                    "Do not call query_jobs or summarize success. Repair the workflow by finalizing the sandbox "
                    "successfully, then retry persistence with the final result.result payload."
                ),
            )

        audit_error = _audit_status_persistence_error(extraction)
        if audit_error:
            return audit_error

        payload = _coerce_extraction_payload(extraction)
        try:
            validate_job_extraction_payload(payload)
        except ValueError as exc:
            return _persistence_guard_error(
                str(exc),
                (
                    "Use this schema error as the next repair target. Correct the sandbox extractor/protocol output "
                    "or extraction payload, rerun validation/finalization if needed, then retry persistence. Do not "
                    "query old DB rows as success verification after this failed write."
                ),
            )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict,
    ) -> dict | None:
        tool_name = getattr(tool, "name", "")
        _record_immediate_tool_error(tool_name, tool_args, tool_context, result)
        _record_or_reset_repeated_inspection(tool_name, tool_args, tool_context, result)
        _clear_satisfied_planned_next_tool(tool_name, tool_args, tool_context, result)
        if tool_name != "update_extraction_context" and _is_extraction_context_progress_action(tool_name, tool_args):
            _reset_extraction_context_update_guard(tool_context)
        if tool_name != "run_skill_script" or not _sandbox_read_signature(tool_args):
            _reset_sandbox_read_guard(tool_context)
        if tool_name in {"fetch_page_to_workspace", "render_page_to_workspace"}:
            _record_last_page_workspace(tool_context, result)
            return None

        if tool_name in {"persist_sandbox_job_extraction", "promote_sandbox_extraction"} and isinstance(result, dict) and result.get("status") == "error":
            return _add_repair_required_next(
                result,
                (
                    f"{tool_name} failed. Read the error, correct the "
                    "sandbox-produced extraction payload/files, then retry promotion/persistence. Do not call query_jobs or produce "
                    "a final success summary until a write succeeds or you can state a blocker."
                ),
            )
        if tool_name in {"persist_sandbox_job_extraction", "promote_sandbox_extraction"}:
            _record_promotion_result(tool_context, result)
            if tool_name == "promote_sandbox_extraction":
                return _add_versioned_artifact_handles_to_promotion(tool_context, result)
            return None
        if tool_name == "query_jobs":
            _record_query_jobs_result(tool_context, result)
            return None
        if getattr(tool, "name", "") != "run_skill_script" or tool_args.get("skill_name") != "sandbox-page-analyst":
            return None
        state = getattr(tool_context, "state", None)
        if not _is_state_like(state):
            return None

        payload = _parse_skill_script_stdout(result)
        file_path = str(tool_args.get("file_path") or "")
        repeat_guard = _sandbox_repeat_guard_result(state, tool_args, payload)
        if repeat_guard:
            active = state.get(ACTIVE_SANDBOX_STATE_KEY)
            if isinstance(active, dict):
                active["status"] = "guardrail_triggered"
                active["guardrail"] = repeat_guard["guardrail"]
                state[ACTIVE_SANDBOX_STATE_KEY] = active
            return repeat_guard
        if file_path.endswith("sandbox_start.py") and payload.get("status") == "running":
            mode = str(payload.get("mode") or "workflow")
            state[ACTIVE_SANDBOX_STATE_KEY] = {
                "audit_id": str(payload.get("audit_id") or ""),
                "status": "running",
                "mode": mode,
                "command_count": 0,
                "forced_continuations": 0,
            }
            if mode != "workflow":
                return _add_required_next(
                    result,
                    "diagnostic sandbox started; run the requested bounded probe with the appropriate sandbox tool, then answer with bounded stdout/stderr previews",
                )
            return _add_required_next(
                result,
                "Continue the sandbox workflow with the appropriate loaded sandbox tool: inspect page evidence, derive patterns, write/run extractor code, persist protocol outputs, validate, then finalize.",
            )

        active = state.get(ACTIVE_SANDBOX_STATE_KEY)
        if not isinstance(active, dict):
            return None
        if file_path.endswith("sandbox_write_file.py"):
            _track_pending_sandbox_script_write(state, active, tool_args, payload)
            repair_next = _sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
            if repair_next:
                _record_active_repair_target(state, active, file_path, payload, tool_args)
                return repair_next
            return _add_required_next_for_pending_script(result, state)
        if file_path.endswith("sandbox_apply_patch.py"):
            _track_pending_sandbox_script_patch(state, active, tool_args, payload)
            repair_next = _sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
            if repair_next:
                _record_active_repair_target(state, active, file_path, payload, tool_args)
                return repair_next
            return _add_required_next_for_pending_script(result, state)
        if file_path.endswith("sandbox_exec.py"):
            active["command_count"] = int(payload.get("command_index") or active.get("command_count") or 0)
            status = str(payload.get("status") or "")
            active["status"] = status if status == "guardrail_triggered" else "running"
            if status == "success" and _successful_exec_clears_repair_target(active):
                active.pop("last_repair_target", None)
            state[ACTIVE_SANDBOX_STATE_KEY] = active
            _mark_pending_sandbox_script_execution(state, active, tool_args, payload)
            _mark_successful_producer_rerun_after_repair(state, active, tool_args, payload)
            return _sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
        if file_path.endswith("sandbox_finalize.py"):
            status = str(payload.get("status") or "")
            if status in {"finalized", "success"}:
                _record_finalized_sandbox_for_promotion(state, active, payload)
                _state_pop(state, ACTIVE_SANDBOX_STATE_KEY, None)
                _state_pop(state, SANDBOX_MODE_RESOURCE_STATE_KEY, None)
                _state_pop(state, SANDBOX_SITE_RESOURCE_STATE_KEY, None)
            elif status == "guardrail_triggered":
                active["status"] = status
                state[ACTIVE_SANDBOX_STATE_KEY] = active
                _state_pop(state, SANDBOX_MODE_RESOURCE_STATE_KEY, None)
                _state_pop(state, SANDBOX_SITE_RESOURCE_STATE_KEY, None)
            else:
                # Finalizer errors are protocol repair feedback; the sandbox
                # container remains running until successful finalization or a
                # guardrail. Keep forcing the agent back into the repair loop.
                active["status"] = "running"
                active["last_repair_target"] = {
                    "file_path": file_path,
                    "producer_hint": "output/extractor.py",
                    "required_action": "debug_repair_extractor",
                    "error": str(payload.get("error") or payload.get("stderr") or "sandbox finalization error"),
                }
                state[ACTIVE_SANDBOX_STATE_KEY] = active
            return _sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
        if file_path.endswith("validate_outputs.py"):
            repair_next = _sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
            if repair_next:
                _record_active_repair_target(state, active, file_path, payload, tool_args)
                return repair_next
            return None
        return None

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        state = getattr(callback_context, "state", None)
        _inject_latest_tool_result(llm_request)
        _inject_session_extraction_context(llm_request, state)
        _inject_finalized_sandbox_persistence_guard(llm_request, state)
        _inject_final_response_contract(llm_request, state)
        active = _active_sandbox_from_contents(llm_request.contents)
        if not active:
            start_guard_text = _workflow_start_guard_text_from_context(callback_context)
            if start_guard_text:
                llm_request.contents.append(
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=start_guard_text)],
                    )
                )
            return None
        if str(active.get("mode") or "workflow") != "workflow":
            return None
        state_active = state.get(ACTIVE_SANDBOX_STATE_KEY) if _is_state_like(state) else None
        if isinstance(state_active, dict) and str(state_active.get("audit_id") or "") == str(active.get("audit_id") or ""):
            active.update({key: value for key, value in state_active.items() if key in {"last_repair_target", "status", "guardrail"}})
        audit_id = active["audit_id"]
        if str(active.get("status") or "") == "guardrail_triggered":
            guardrail = str(active.get("guardrail") or "sandbox_guardrail_triggered")
            llm_request.contents.append(
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_text(
                            text=(
                                "<RUNTIME_SANDBOX_GUARD>\n"
                                "purpose: stop a terminal workflow sandbox cleanly.\n"
                                "priority: hard operational constraint.\n"
                                "usage: produce a compact blocker response; do not call more sandbox, persistence, "
                                "record, query, or context-update tools for this sandbox.\n"
                                f"message: sandbox audit {audit_id} is terminal because guardrail {guardrail} was "
                                "triggered. Report the audit_id, guardrail, and last actionable error/blocker. "
                                "Do not claim finalized artifacts, saved jobs, or persistence success.\n"
                                "</RUNTIME_SANDBOX_GUARD>"
                            )
                        )
                    ],
                )
            )
            return None
        command_count = int(active.get("command_count") or 0)
        pending_script = _pending_scripts_for_active_sandbox(state, active) if _is_state_like(state) else {}
        if pending_script:
            next_action = (
                f"A workflow script was written but has not been verified. Run "
                f"`python {pending_script.get('path') or 'output/extractor.py'}` with sandbox_exec.py, then verify "
                "the required protocol artifacts exist before validate/finalize/persist/query."
            )
        elif isinstance(active.get("last_repair_target"), dict):
            repair = active["last_repair_target"]
            next_action = _repair_target_next_action(repair)
        elif command_count >= 5:
            next_action = (
                "You likely have enough page inspection evidence. Continue the sandbox workflow by choosing the "
                "appropriate loaded sandbox tool: write or repair extractor/protocol files, run or validate the "
                "extractor, or finalize only if validation has passed."
            )
        else:
            next_action = (
                "Continue the sandbox workflow by choosing the appropriate loaded sandbox tool to inspect the "
                "mounted page, derive patterns, or write the extractor; do not answer the user yet."
            )
        llm_request.contents.append(
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_text(
                        text=(
                            "<RUNTIME_SANDBOX_GUARD>\n"
                            "purpose: keep an active workflow sandbox on the required extraction path.\n"
                            "priority: hard operational constraint.\n"
                            "usage: obey this while the sandbox is running; use session context for next-step reasoning.\n"
                            f"message: sandbox audit {audit_id} is running and has not finalized. {next_action} "
                            "Do not produce a final text response while required protocol outputs are missing. "
                            "If the sandbox has guardrail_triggered, do not persist; report the guardrail blocker. "
                            "Do not use inspection commands for inline heredocs or shell snippets that write files; "
                            "write extractor/protocol files through the sandbox file-writing capability so they are "
                            "audited and validated. "
                            "Do not import bs4/lxml/parsel unless already verified installed in the sandbox.\n"
                            "</RUNTIME_SANDBOX_GUARD>"
                        )
                    )
                ],
            )
        )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        state = getattr(callback_context, "state", None)
        if not _is_state_like(state):
            return None
        repair_replacement = _active_repair_target_model_replacement(state, llm_response)
        if repair_replacement:
            return repair_replacement
        active_replacement = _active_sandbox_model_replacement(state, llm_response)
        if active_replacement:
            return active_replacement
        return None


class SandboxOutputGatePlugin(BasePlugin):
    """Persist oversized sandbox-like tool results before they enter context."""

    def __init__(
        self,
        *,
        direct_max_chars: int = 8_000,
        preview_max_chars: int = 2_000,
        name: str = "sandbox_output_gate_plugin",
    ) -> None:
        super().__init__(name=name)
        self.direct_max_chars = direct_max_chars
        self.preview_max_chars = preview_max_chars

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict,
    ) -> dict | None:
        if not isinstance(result, dict):
            return None
        if not self._should_gate(tool, tool_args, result):
            return None

        artifact_handles = await _persist_artifact_sources(result, tool_context)
        if artifact_handles:
            result = dict(result)
            existing_artifacts = result.get("artifact_handles") if isinstance(result.get("artifact_handles"), dict) else {}
            result["artifact_handles"] = {**existing_artifacts, **artifact_handles}
            result.pop("artifact_sources", None)
            _record_sandbox_artifact_handles(tool_context, result, artifact_handles)

        if getattr(tool, "name", "") == "run_skill_script" and tool_args.get("skill_name") == "sandbox-page-analyst":
            return result if artifact_handles else None

        serialized = json.dumps(result, ensure_ascii=True, sort_keys=True, default=str)
        if len(serialized) <= self.direct_max_chars:
            return result if artifact_handles else None

        audit_id = str(result.get("audit_id") or _extract_audit_id(result) or "sandbox_run_unknown")
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        artifact_name = f"{audit_id}__oversized__tool_output_{digest[:12]}.json"
        part = genai_types.Part.from_bytes(data=serialized.encode("utf-8"), mime_type="application/json")
        version = await tool_context.save_artifact(
            artifact_name,
            part,
            custom_metadata={"sha256": digest, "original_bytes": len(serialized.encode("utf-8"))},
        )
        stored_response: dict[str, Any] = {
            "status": "stored_preview",
            "reason": "tool_output_exceeded_context_threshold",
            "audit_id": audit_id,
            "original_bytes": len(serialized.encode("utf-8")),
            "sha256": digest,
            "preview": _preview(serialized, self.preview_max_chars),
            "artifact": {
                "artifact_name": artifact_name,
                "version": version,
                "mime_type": "application/json",
                "bytes": len(serialized.encode("utf-8")),
                "sha256": digest,
            },
        }
        paths = _compact_output_paths(result)
        if paths:
            stored_response["paths"] = paths
        for key in (
            "message",
            "observation",
            "error_type",
            "path",
            "model",
            "written",
            "errors",
            "artifacts",
            "stdout_bytes",
            "stderr_bytes",
            "stdout_truncated",
            "stderr_truncated",
            "returned_stdout_chars",
            "returned_stderr_chars",
            "exit_code",
            "command_index",
            "guardrail",
            "error",
        ):
            if key in result:
                stored_response[key] = result[key]
        return stored_response

    def _should_gate(self, tool: Any, tool_args: dict[str, Any], result: dict[str, Any]) -> bool:
        tool_name = getattr(tool, "name", "")
        if tool_name in {"run_skill_script", "fetch_page", "render_page", "fetch_page_to_workspace", "render_page_to_workspace"}:
            return True
        if tool_args.get("skill_name") == "sandbox-page-analyst":
            return True
        return any(key in result for key in ("stdout", "stderr", "content", "html", "stdout_preview", "stderr_preview"))


class SandboxNoteRefinementPlugin(BasePlugin):
    """Periodically summarize sandbox command batches and inject notes into model requests."""

    def __init__(
        self,
        *,
        command_interval: int = 5,
        max_notes: int = 5,
        prune_completed_sandbox_context: bool = True,
        model: str = DEFAULT_NOTE_REFINEMENT_MODEL,
        summarizer: Any = None,
        name: str = "sandbox_note_refinement_plugin",
    ) -> None:
        super().__init__(name=name)
        self.command_interval = max(1, min(command_interval, 5))
        self.max_notes = max(1, max_notes)
        self.prune_completed_sandbox_context = prune_completed_sandbox_context
        self.model = model
        self.summarizer = summarizer

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict,
    ) -> dict | None:
        if getattr(tool, "name", "") != "run_skill_script" or tool_args.get("skill_name") != "sandbox-page-analyst":
            return None
        file_path = str(tool_args.get("file_path") or "")
        if not file_path.endswith("sandbox_exec.py"):
            return None
        state = getattr(tool_context, "state", None)
        if not _is_state_like(state):
            return None

        payload = _parse_skill_script_stdout(result)
        audit_id = str(payload.get("audit_id") or _extract_audit_id(payload) or "")
        command_index = int(payload.get("command_index") or 0)
        if not audit_id:
            return None

        buffers = state.setdefault(SANDBOX_NOTE_BUFFER_STATE_KEY, {})
        if not isinstance(buffers, dict):
            buffers = {}
            state[SANDBOX_NOTE_BUFFER_STATE_KEY] = buffers
        buffer = buffers.setdefault(audit_id, [])
        if not isinstance(buffer, list):
            buffer = []
            buffers[audit_id] = buffer
        buffer.append(_sandbox_command_note_source(payload, file_path))

        # Keep the newest command response full. When command N+1 arrives,
        # summarize the previous N responses and leave command N+1 unsummarized.
        if len(buffer) <= self.command_interval:
            return None

        ordered_buffer = sorted(buffer, key=_sandbox_command_sort_key)
        commands_to_summarize = list(ordered_buffer[: self.command_interval])
        remaining_commands = list(ordered_buffer[self.command_interval :])
        buffers[audit_id] = remaining_commands
        kept_full_command_index = int(
            (remaining_commands[-1] if remaining_commands else commands_to_summarize[-1]).get("command_index")
            or command_index
        )
        try:
            current_notes = state.get(SANDBOX_NOTES_STATE_KEY)
            visible_notes = current_notes[-self.max_notes :] if isinstance(current_notes, list) else []
            summary = await self._summarize(audit_id, commands_to_summarize, visible_notes)
        except Exception as exc:  # Do not fail the user workflow because note refinement failed.
            errors = state.setdefault(SANDBOX_NOTE_ERROR_STATE_KEY, [])
            if isinstance(errors, list):
                errors.append({"audit_id": audit_id, "command_index": command_index, "error": str(exc)})
            return None

        notes = state.setdefault(SANDBOX_NOTES_STATE_KEY, [])
        if not isinstance(notes, list):
            notes = []
            state[SANDBOX_NOTES_STATE_KEY] = notes
        notes.append(
            {
                "audit_id": audit_id,
                "through_command_index": int(commands_to_summarize[-1].get("command_index") or command_index),
                "kept_full_command_index": kept_full_command_index,
                "summary": summary,
            }
        )
        del notes[:-self.max_notes]
        _mark_summarized_commands(state, audit_id, commands_to_summarize)
        return None

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        state = getattr(callback_context, "state", None)
        if not _is_state_like(state):
            state = {}

        notes = state.get(SANDBOX_NOTES_STATE_KEY)
        visible_notes = notes[-self.max_notes :] if isinstance(notes, list) else []

        if self.prune_completed_sandbox_context:
            _prune_summarized_sandbox_contexts(llm_request, visible_notes, state)
            _prune_completed_sandbox_contexts(llm_request, visible_notes)

        if visible_notes:
            llm_request.contents.append(
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_text(
                            text=(
                                "<RUNTIME_SANDBOX_NOTES>\n"
                                "purpose: supporting evidence from compacted sandbox command history.\n"
                                "priority: evidence only, not workflow authority.\n"
                                "usage: use these notes to recover prior facts and compare against current results. "
                                "If they conflict with SESSION_EXTRACTION_CONTEXT, verify with exact tool output when "
                                "possible, then update SESSION_EXTRACTION_CONTEXT. Keep using full available tool "
                                "responses for exact facts.\n"
                                "notes_json:\n"
                                + json.dumps(visible_notes, ensure_ascii=True, sort_keys=True)
                                + "\n</RUNTIME_SANDBOX_NOTES>"
                            )
                        )
                    ],
                )
            )
        return None

    async def _summarize(
        self,
        audit_id: str,
        commands: list[dict[str, Any]],
        current_notes: list[Any],
    ) -> str:
        if self.summarizer is not None:
            try:
                maybe = self.summarizer(audit_id, current_notes, commands)
            except TypeError:
                maybe = self.summarizer(audit_id, commands)
            if inspect.isawaitable(maybe):
                return str(await maybe)
            return str(maybe)

        from litellm import acompletion

        prompt = (
            "Summarize these sandbox command results for a job-page extraction agent. "
            "Fuse the current notes with the command results, and return one concise updated note. "
            "Focus on: observations, extraction_plan implications, "
            "result-vs-requirement comparison, errors, artifact paths, and next repair facts. "
            "Do not invent page facts. Do not include raw HTML or long stdout.\n\n"
            f"audit_id: {audit_id}\n"
            f"current_notes_json: {_preview(json.dumps(current_notes, ensure_ascii=True, sort_keys=True, default=str), 6_000)}\n"
            f"commands_json: {_preview(json.dumps(commands, ensure_ascii=True, sort_keys=True, default=str), 12_000)}"
        )
        response = await acompletion(
            model=self.model,
            messages=[
                {"role": "system", "content": "You write compact continuity notes for an autonomous scraping workflow."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=500,
        )
        return str(response.choices[0].message.content or "").strip()


def _is_transient_model_error(error: Exception) -> bool:
    error_text = f"{type(error).__name__}: {error}".lower()
    transient_markers = (
        "rate limit",
        "rate_limit",
        "ratelimit",
        "tokens per min",
        "token per min",
        "tpm",
        "too many requests",
        "429",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "service unavailable",
        "overloaded",
        "connection",
        "server error",
        "internal server error",
        "500",
        "502",
        "503",
        "504",
    )
    if any(marker in error_text for marker in transient_markers):
        return True

    transient_class_markers = (
        "ratelimit",
        "timeout",
        "apiconnection",
        "internalserver",
        "serviceunavailable",
        "apistatus",
    )
    class_name = type(error).__name__.lower()
    return any(marker in class_name for marker in transient_class_markers)


def _retry_delay_from_error(error: Exception) -> float | None:
    error_text = str(error)
    patterns = (
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|minutes?)",
        r"retry[- ]after[:=]?\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|minutes?)?",
    )
    for pattern in patterns:
        match = re.search(pattern, error_text, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        if unit.startswith("ms") or unit.startswith("millisecond"):
            return value / 1000
        if unit.startswith("m") and not unit.startswith("ms"):
            return value * 60
        return value
    retry_after = getattr(error, "retry_after", None)
    if isinstance(retry_after, int | float):
        return float(retry_after)
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            value = headers.get("retry-after")
        except AttributeError:
            value = None
        if value:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _model_retry_exhausted_response(error: Exception, *, attempts: int, detail: str) -> LlmResponse:
    return LlmResponse(
        error_code="MODEL_RETRY_EXHAUSTED",
        error_message=(
            f"{detail} retry_attempts={attempts}; last_error_type={type(error).__name__}; "
            f"last_error={_preview(str(error), 500)}"
        ),
    )


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
    guarded["status"] = "guardrail_triggered"
    guarded["audit_id"] = audit_id
    guarded["guardrail"] = "repeated_sandbox_tool_result"
    guarded["error"] = f"Repeated {label} {count} times for audit {audit_id}."
    guarded["repeat_count"] = count
    guarded["original_status"] = status
    guarded["original_error"] = payload.get("error", "")
    guarded["file_path"] = file_path
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
            "Use the previous inspection result to choose a progress action: write or patch output/extractor.py, "
            "run the extractor, validate outputs, finalize, promote, or update_extraction_context with new evidence "
            "and a different planned_next_tool. Do not call the same inspection again."
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
                "For sandbox helper calls, include skill_name and file_path. For producer repairs, include "
                "target_paths such as [\"output/extractor.py\"]."
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
        return _workflow_extractor_source_policy_error(tool_args, tool_context)
    return _workflow_contract_required_error(
        _active_repair_audit_id(tool_context),
        (
            "Workflow execution is blocked until session state declares required_outputs and workflow_contract. "
            "The agent should load the relevant workflow resources first, then write the contract into "
            "SESSION_EXTRACTION_CONTEXT before starting the workflow sandbox or writing/running producer scripts."
        ),
    )


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
        producer = _normalize_skill_path(str(contract.get("producer") or ""))
        if producer and producer != WORKFLOW_PRODUCER_PATH:
            return _workflow_contract_required_error(
                str(tool_args.get("audit_id") or _active_repair_audit_id(tool_context) or ""),
                f"workflow_contract.producer must be {WORKFLOW_PRODUCER_PATH}.",
                {"producer": producer},
            )
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
    if file_path == "scripts/sandbox_write_file.py" and _sandbox_write_target_path(tool_args) == WORKFLOW_PRODUCER_PATH:
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


def _workflow_extractor_source_policy_error(tool_args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    if tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
    if file_path != "scripts/sandbox_write_file.py" or _sandbox_write_target_path(tool_args) != WORKFLOW_PRODUCER_PATH:
        return None
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    content = _option_value(args, "--content")
    missing = [
        path
        for path in REQUIRED_WORKFLOW_PROTOCOL_OUTPUTS
        if not _producer_source_mentions_output_path(content, path)
    ]
    if not missing:
        placeholder_reason = _placeholder_extractor_source_reason(content)
        if not placeholder_reason:
            return None
        return _workflow_contract_required_error(
            _active_repair_audit_id(tool_context),
            (
                f"{WORKFLOW_PRODUCER_PATH} must implement evidence-backed extraction logic, not a placeholder "
                "protocol producer. Inspect page evidence, encode the recurring job-post pattern, and write real "
                "candidate data before running validation."
            ),
            {"placeholder_source_reason": placeholder_reason},
        )
    return _workflow_contract_required_error(
        _active_repair_audit_id(tool_context),
        (
            f"{WORKFLOW_PRODUCER_PATH} must be a protocol producer, not a stdout-only extractor. "
            "Its source must write every required protocol output in one run. The source can use direct "
            "`output/<name>.json` strings or normal Python path composition under the `output/` directory."
        ),
        {"missing_outputs_in_source": missing},
    )


def _producer_source_mentions_output_path(content: str, path: str) -> bool:
    if path in content:
        return True
    directory, _, filename = path.rpartition("/")
    if not directory or not filename:
        return False
    return directory in content and filename in content


def _placeholder_extractor_source_reason(content: str) -> str:
    lowered = content.lower()
    explicit_markers = {
        "placeholder producer",
        "minimal placeholder",
        "stub producer",
        "'status': 'stub'",
        '"status": "stub"',
        "stub producer",
    }
    for marker in explicit_markers:
        if marker in lowered:
            return f"source contains placeholder marker {marker!r}"

    compact = re.sub(r"\s+", "", lowered)
    if (
        ("'jobs':[]" in compact or '"jobs":[]' in compact or "jobs=[]" in compact)
        and "page.html" not in lowered
        and "beautifulsoup" not in lowered
        and "parsel" not in lowered
    ):
        return "source emits an empty jobs payload without reading page evidence"
    return ""


def _sandbox_start_mode(tool_args: dict[str, Any]) -> str:
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    return str(_option_value(args, "--mode") or "workflow")


def _sandbox_exec_runs_producer(tool_args: dict[str, Any]) -> bool:
    command = _sandbox_exec_command(tool_args).replace("/workspace/", "").replace("./", "")
    return f"python {WORKFLOW_PRODUCER_PATH}" in command or f"python3 {WORKFLOW_PRODUCER_PATH}" in command


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
            "path. workflow_contract should name producer output/extractor.py, state that output/extractor.py "
            "creates them in one extraction pass, and state that missing/invalid outputs are repaired at the "
            "producer. Do not put the paths only under keys such as must_create_in_one_pass."
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
    if _normalize_tool_name(str(expected.get("tool_name") or "")) != _normalize_tool_name(tool_name):
        return False

    if "skill_name" in expected and str(expected.get("skill_name") or "") != str(tool_args.get("skill_name") or ""):
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
    return policy.kind in {
        ToolActionKind.REFERENCE_READ,
        ToolActionKind.WORKSPACE_READ,
        ToolActionKind.SANDBOX_READ,
    }


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
        return _repair_scope_sandbox_read_error(tool_args, tool_context, scope)
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


def _repair_scope_sandbox_read_error(
    tool_args: dict[str, Any],
    tool_context: Any,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    allowed = _normalized_scope_paths(scope.get("allowed_inspections"))
    if not allowed:
        return None
    args = [str(item) for item in tool_args.get("args") or [] if item is not None]
    requested = _normalize_skill_path(_option_value(args, "--path"))
    if requested in allowed:
        return None
    return _repair_scope_error(
        _active_repair_audit_id(tool_context),
        "repair_scope_inspection_not_allowed",
        (
            f"Sandbox read target {requested or '<unknown>'} is outside repair_scope.allowed_inspections. "
            "Keep the current repair focused, or update repair_scope first with the reason this inspection is needed."
        ),
        {"requested_path": requested, "allowed_inspections": sorted(allowed)},
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
            "Load `sandbox-extraction-debugger` if not loaded, inspect `output/extractor.py` and any available "
            "producer outputs, then patch `output/extractor.py` so running `python output/extractor.py` writes the "
            f"missing protocol file `{read_path}` plus the other required outputs. Rerun the extractor before "
            "validate/finalize."
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

    audit_id = str(active.get("audit_id") or "")
    return {
        "status": "error",
        "error_type": "workflow_protocol_write_policy",
        "guardrail": "extractor_must_persist_protocol_outputs",
        "audit_id": audit_id,
        "path": target_path,
        "count": 0,
        "written_count": 0,
        "error": (
            f"Refused direct write to {target_path}. In workflow mode, required protocol outputs must be "
            "created by output/extractor.py and refreshed by rerunning `python output/extractor.py`, not "
            "patched manually through sandbox_write_file.py."
        ),
    }


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
            "state-changing workflow action happened after the previous context update. Session context is a "
            "notebook, not a progress action."
        ),
        "required_next": (
            "Take a state-changing sandbox action instead of another context-only update: write or repair "
            "output/extractor.py, run it, validate outputs, finalize, promote finalized outputs, or return a "
            "compact blocker only if no safe repair exists. The sandbox remains active; do not treat this as a "
            "terminal sandbox failure."
        ),
    }


def _is_extraction_context_progress_action(tool_name: str, tool_args: dict[str, Any]) -> bool:
    return resolve_tool_policy(tool_name, tool_args).counts_as_intervening_action


def _extraction_context_update_digest(tool_args: dict[str, Any]) -> str:
    material = {
        key: tool_args.get(key)
        for key in (
            "audit_id",
            "page_id",
            "status",
            "task_understanding",
            "final_goal",
            "initial_plan",
            "observations",
            "extraction_plan",
            "last_result",
            "known_errors",
            "attempted_actions",
            "immediate_goal",
            "required_outputs",
            "workflow_contract",
        )
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
            "Take a state-changing repair action now: patch output/extractor.py with scripts/sandbox_apply_patch.py "
            "or rewrite it with scripts/sandbox_write_file.py if patching is not viable, then rerun "
            "`python output/extractor.py`."
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
            "Continue the active sandbox workflow: run or repair extractor code, validate protocol outputs, "
            "call sandbox_finalize.py successfully, then persist/query/record results."
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
            f"Run the written script in the sandbox with sandbox_exec.py using `python {script_path}`. "
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
            f"Run the written or patched script before inspecting final outputs: use sandbox_exec.py with "
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
    else:
        repair_guidance = (
            "Load `sandbox-extraction-debugger` before the next repair attempt. Use the sandbox tools exposed through "
            "`run_skill_script`: inspect files with `scripts/sandbox_read.py`, run focused shell/Python probes with "
            "`scripts/sandbox_exec.py`, patch existing artifacts with `scripts/sandbox_apply_patch.py`, and modify "
            "only Docker sandbox workspace artifacts. Use `scripts/sandbox_write_file.py` only for initial creation "
            "or unresolvable patch conflicts. "
            "If the error says required protocol outputs are missing, do not read the missing files as the next step; "
            "inspect and patch `output/extractor.py` so the next extractor run writes those files, then rerun "
            "`python output/extractor.py` and validate/finalize again. Otherwise inspect the current sandbox workspace "
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
        "producer_hint": "output/extractor.py",
        "required_action": "debug_repair_extractor",
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
    # A producer rerun is not proof that a validator/finalizer error is fixed.
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
    producer = str(repair.get("producer_hint") or "output/extractor.py")
    error = _preview(str(repair.get("error") or ""), 350)
    if repair.get("producer_rerun_status") == "success_unvalidated":
        return (
            "The repaired producer has already been rerun successfully. Do not patch it again from the stale "
            f"error alone. Validate the regenerated protocol outputs with scripts/validate_outputs.py or "
            f"sandbox_finalize.py; only repair {producer} again if the fresh validator/finalizer result still "
            f"reports a concrete error. Previous error: {error}"
        )
    if _repair_target_requires_rewrite(repair):
        return (
            "The latest sandbox result is actionable repair feedback. Do not answer the user yet. "
            f"Treat this as rejected initial producer creation: create or rewrite {producer} with "
            "scripts/sandbox_write_file.py using corrected full source. Do not patch a missing file and do not "
            "modify host workflow code. Rerun the extractor, then "
            f"validate/finalize. Last error: {error}"
        )
    return (
        "The latest sandbox result is actionable repair feedback. Do not answer the user yet. "
        f"Load sandbox-extraction-debugger, inspect the producer artifact for {repair.get('file_path')}, "
        f"follow the official patch-first repair workflow for {producer}, "
        f"rerun the extractor, then validate/finalize. Last error: {error}"
    )


def _active_repair_target_model_replacement(state: Any, llm_response: LlmResponse) -> LlmResponse | None:
    active = state.get(ACTIVE_SANDBOX_STATE_KEY) if _is_state_like(state) else None
    if not isinstance(active, dict):
        return None
    if str(active.get("status") or "running") != "running":
        return None
    repair = active.get("last_repair_target")
    if not isinstance(repair, dict):
        return None
    if str(repair.get("required_action") or "") != "debug_repair_extractor":
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
    producer = str(repair.get("producer_hint") or "output/extractor.py")
    source = str(repair.get("file_path") or "sandbox workflow")
    if _repair_target_requires_rewrite(repair):
        extraction_plan = (
            f"Create or rewrite {producer} with corrected full source. The previous write was rejected before "
            "the producer file was accepted, so patching a missing file is not viable and host workflow code "
            "must not be modified."
        )
        immediate_goal = (
            f"Use scripts/sandbox_write_file.py to create or rewrite {producer} so one run writes every required "
            "protocol output, then run `python output/extractor.py` with sandbox_exec.py."
        )
        planned_next_tool = {
            "tool_name": "functions.run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_write_file.py",
            "intent": "rewrite rejected producer source inside the Docker sandbox",
        }
    else:
        extraction_plan = (
            f"Load sandbox-extraction-debugger and follow its official patch-first repair workflow for {producer}; "
            "one producer run must generate all required protocol outputs before validation and finalization."
        )
        immediate_goal = (
            f"Use sandbox-extraction-debugger to repair {producer}; prefer a patch-first producer edit when "
            "available and do not write protocol JSON files directly. Then run `python output/extractor.py` "
            "with sandbox_exec.py."
        )
        planned_next_tool = {
            "tool_name": "load_skill",
            "skill_name": "sandbox-extraction-debugger",
            "intent": "load official sandbox repair protocol before patching producer",
        }
    return LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                genai_types.Part.from_function_call(
                    name="update_extraction_context",
                    args={
                        "audit_id": audit_id,
                        "status": "repairing",
                        "known_errors": [
                            f"Recoverable sandbox workflow error from {source}: {_preview(error, 500)}",
                        ],
                        "attempted_actions": [
                            "Model attempted to answer while a recoverable extractor repair target was active; runtime blocked the final text.",
                        ],
                        "extraction_plan": [
                            extraction_plan,
                        ],
                        "immediate_goal": immediate_goal,
                        "planned_next_tool": planned_next_tool,
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
            f"Run the pending producer script {pending.get('path') or 'output/extractor.py'} in the sandbox, "
            "then validate/finalize before answering."
        )
        planned_next_tool = {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_exec.py",
            "intent": "run pending producer before validation/finalization",
        }
    else:
        immediate_goal = (
            "The workflow sandbox is still running and has not finalized. Run sandbox_finalize.py to prove "
            "the protocol outputs are valid, or use the returned finalizer error as the next repair target."
        )
        planned_next_tool = {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_finalize.py",
            "intent": "prove protocol outputs are finalized before final response",
        }

    return LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                genai_types.Part.from_function_call(
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
                        "planned_next_tool": planned_next_tool,
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


def _record_sandbox_artifact_handles(
    tool_context: Any,
    result: dict[str, Any],
    artifact_handles: dict[str, dict[str, Any]],
) -> None:
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state) or not artifact_handles:
        return
    payload = _parse_skill_script_stdout(result)
    audit_id = str(result.get("audit_id") or payload.get("audit_id") or _extract_audit_id(result) or "")
    if not audit_id:
        return
    by_audit = state.setdefault(SANDBOX_ARTIFACT_HANDLES_STATE_KEY, {})
    if not isinstance(by_audit, dict):
        by_audit = {}
        state[SANDBOX_ARTIFACT_HANDLES_STATE_KEY] = by_audit
    existing = by_audit.get(audit_id)
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(artifact_handles)
    by_audit[audit_id] = merged
    state[SANDBOX_ARTIFACT_HANDLES_STATE_KEY] = by_audit

    pending = state.get(FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
    if isinstance(pending, dict) and str(pending.get("audit_id") or "") == audit_id:
        pending["artifact_handles"] = merged
        state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = pending


def _versioned_artifact_handles_for_audit(state: Any, audit_id: str) -> dict[str, dict[str, Any]]:
    if not _is_state_like(state) or not audit_id:
        return {}
    by_audit = state.get(SANDBOX_ARTIFACT_HANDLES_STATE_KEY)
    if not isinstance(by_audit, dict):
        return {}
    handles = by_audit.get(audit_id)
    return dict(handles) if isinstance(handles, dict) else {}


def _add_versioned_artifact_handles_to_promotion(tool_context: Any, result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    state = getattr(tool_context, "state", None)
    if not _is_state_like(state):
        return None
    audit_id = str(result.get("audit_id") or "")
    handles = _versioned_artifact_handles_for_audit(state, audit_id)
    if not handles:
        pending = state.get(FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
        if isinstance(pending, dict) and str(pending.get("audit_id") or "") == audit_id:
            pending_handles = pending.get("artifact_handles")
            handles = dict(pending_handles) if isinstance(pending_handles, dict) else {}
    if not handles:
        return None
    updated = dict(result)
    updated["adk_artifact_handles"] = handles
    updated["artifact_version_policy"] = (
        "Use adk_artifact_handles for final reporting and audit references. Each handle includes a stable "
        "ADK artifact_name plus version; workspace paths are not versioned artifact references."
    )
    return updated


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
        "After the sandbox starts, inspect mounted files, derive recurring job-post patterns, write and run extractor "
        "code, validate protocol outputs, finalize, then persist and query saved jobs. Do not load diagnostic-mode.\n"
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
        for part in content.parts or []:
            function_response = getattr(part, "function_response", None)
            if not function_response:
                continue
            tool_name = str(function_response.name or "")
            payload = function_response.response or {}
            if _skip_latest_tool_result(tool_name, payload):
                continue
            responses.append(_compact_latest_function_response(tool_name, payload))
        if not responses:
            continue
        if len(responses) == 1:
            return responses[0]
        return {"tool_results": responses, "count": len(responses), "compacted": True}
    return None


def _skip_latest_tool_result(tool_name: str, payload: Any) -> bool:
    if tool_name != "update_extraction_context" or not isinstance(payload, dict):
        return False
    return str(payload.get("status") or "").lower() == "success"


def _compact_latest_function_response(tool_name: str, payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and tool_name == "run_skill_script":
        compact = _compact_run_skill_script_result(payload, payload, 1_000)
    elif isinstance(payload, dict):
        compact = _compact_latest_payload(payload, 1_000)
    else:
        compact = {"value": _preview(str(payload), 1_000), "compacted": True}
    compact["tool_name"] = tool_name
    return compact


def _compact_latest_payload(payload: dict[str, Any], preview_max_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "status",
        "error_type",
        "error",
        "message",
        "required_next",
        "audit_id",
        "skill_name",
        "file_path",
        "exit_code",
        "command_index",
        "validated_count",
        "written_count",
        "job_count",
        "expected_count",
        "actual_count",
        "missing_required_outputs",
        "stdout_truncated",
        "stderr_truncated",
    ):
        if key in payload:
            compact[key] = _compact_latest_value(payload[key], preview_max_chars)
    for key in ("stdout", "stdout_preview", "stderr", "stderr_preview"):
        if key in payload:
            compact[key] = _preview(str(payload[key]), preview_max_chars)
    paths = _compact_output_paths(payload)
    if paths:
        compact["paths"] = paths
    artifact_handles = _compact_artifact_handles(payload)
    if artifact_handles:
        compact["artifact_handles"] = artifact_handles
    if not compact:
        compact["response_preview"] = _preview(json.dumps(payload, ensure_ascii=True, default=str), preview_max_chars)
    compact["compacted"] = True
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
                        "5. Use observations as page/workspace facts and extraction_plan as the current method for "
                        "turning those facts into required outputs. Update them when new evidence changes the method.\n"
                        "6. Use known_errors as active blockers only. If latest results show an error is solved, call "
                        "update_extraction_context with known_errors rewritten without that stale error.\n"
                        "7. Check attempted_actions before acting. Do not repeat actions that did not change state; "
                        "choose a state-changing repair, validation, finalization, promotion, or query action instead.\n"
                        "8. If extractor outputs exist and no concrete validation/finalization error is active, the "
                        "next objective is validation/finalization, not another extractor rewrite.\n"
                        "9. If planned_next_tool is present, the next tool call must match it. If the plan is stale, "
                        "first update this context with the new evidence, a revised immediate_goal, and replacement "
                        "planned_next_tool.\n"
                        "10. Treat required_outputs and workflow_contract as hard workflow invariants. Before starting "
                        "a workflow sandbox, writing output/extractor.py, running it, validating, finalizing, or "
                        "persisting, verify the contract says output/extractor.py must create every required protocol "
                        "output in one extraction pass.\n"
                        "11. If repair_scope is present, treat it as the bounded work order for the current repair: "
                        "load only allowed_resources, inspect only allowed_inspections, patch only files, and when "
                        "status is ready_to_verify/verifying run the declared verification command before changing "
                        "scope.\n"
                        "12. Before every non-context tool call and final response, reconcile <LATEST_TOOL_RESULT> "
                        "when present, "
                        "final_goal, immediate_goal, "
                        "last_result, known_errors, attempted_actions, observations, extraction_plan, and "
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
    if "immediate_goal" not in context and context.get("next_focus"):
        context = {**context, "immediate_goal": context.get("next_focus")}
    allowed_keys = (
        "audit_id",
        "page_id",
        "status",
        "task_understanding",
        "final_goal",
        "initial_plan",
        "observations",
        "extraction_plan",
        "last_result",
        "known_errors",
        "attempted_actions",
        "immediate_goal",
        "planned_next_tool",
        "repair_scope",
        "required_outputs",
        "workflow_contract",
    )
    compact: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in context:
            continue
        value = context[key]
        if isinstance(value, list):
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


async def _persist_artifact_sources(result: dict[str, Any], tool_context: Any) -> dict[str, dict[str, Any]]:
    sources = _collect_artifact_sources(result)
    if not isinstance(sources, list):
        return {}

    handles: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            continue
        source_path = Path(str(source.get("source_path") or ""))
        artifact_name = _safe_adk_artifact_name(str(source.get("artifact_name") or ""))
        if not source_path.exists() or not artifact_name:
            continue
        data = source_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        mime_type = str(source.get("mime_type") or "application/octet-stream")
        version = await tool_context.save_artifact(
            artifact_name,
            genai_types.Part.from_bytes(data=data, mime_type=mime_type),
            custom_metadata={
                "sha256": digest,
                "source_kind": "sandbox_artifact_source",
                "bytes": len(data),
            },
        )
        key = str(source.get("key") or source_path.stem or f"artifact_{index}")
        handles[key] = {
            "artifact_name": artifact_name,
            "version": version,
            "mime_type": mime_type,
            "bytes": len(data),
            "sha256": digest,
        }
    return handles


def _collect_artifact_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for payload in (result, _parse_skill_script_stdout(result)):
        if not isinstance(payload, dict):
            continue
        payload_sources = payload.get("artifact_sources")
        if not isinstance(payload_sources, list):
            continue
        for source in payload_sources:
            if not isinstance(source, dict):
                continue
            source_path = str(source.get("source_path") or "")
            artifact_name = str(source.get("artifact_name") or "")
            fingerprint = (source_path, artifact_name)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            sources.append(source)
    return sources


def _safe_adk_artifact_name(artifact_name: str) -> str:
    """Keep ADK artifact handles fetchable in ADK Web path-based routes."""
    return artifact_name.replace("\\", "__").replace("/", "__")


def _looks_like_sandbox_payload(payload: dict[str, Any]) -> bool:
    if str(payload.get("skill_name") or "") == "sandbox-page-analyst":
        return True
    if "audit_id" in payload:
        return True
    return any(key in payload for key in ("stdout", "stderr", "stdout_preview", "stderr_preview"))


def _mark_summarized_commands(
    state: Any,
    audit_id: str,
    commands: list[dict[str, Any]],
) -> None:
    summarized = state.setdefault(SANDBOX_SUMMARIZED_COMMANDS_STATE_KEY, {})
    if not isinstance(summarized, dict):
        summarized = {}
        state[SANDBOX_SUMMARIZED_COMMANDS_STATE_KEY] = summarized
    indexes = summarized.setdefault(audit_id, [])
    if not isinstance(indexes, list):
        indexes = []
        summarized[audit_id] = indexes
    existing = {int(index) for index in indexes if str(index).isdigit()}
    for command in commands:
        command_index = command.get("command_index")
        try:
            existing.add(int(command_index))
        except (TypeError, ValueError):
            continue
    summarized[audit_id] = sorted(existing)


def _prune_summarized_sandbox_contexts(llm_request: LlmRequest, notes: list[Any], state: Any) -> None:
    summarized = state.get(SANDBOX_SUMMARIZED_COMMANDS_STATE_KEY)
    if not isinstance(summarized, dict):
        return
    latest_note_by_audit = _latest_note_by_audit(notes)
    for content in llm_request.contents:
        for part in content.parts or []:
            response = getattr(part, "function_response", None)
            if not response or response.name != "run_skill_script":
                continue
            payload = response.response or {}
            if not isinstance(payload, dict):
                continue
            if payload.get("status") in {
                "sandbox_context_removed_after_note_refinement",
                "sandbox_context_removed_after_completion",
            }:
                continue
            if payload.get("skill_name") != "sandbox-page-analyst":
                continue
            file_path = str(payload.get("file_path") or "")
            if not file_path.endswith("sandbox_exec.py"):
                continue
            stdout_payload = _parse_skill_script_stdout(payload)
            audit_id = str(stdout_payload.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
            if not audit_id:
                continue
            try:
                command_index = int(stdout_payload.get("command_index") or payload.get("command_index") or 0)
            except (TypeError, ValueError):
                command_index = 0
            summarized_indexes = summarized.get(audit_id)
            if not isinstance(summarized_indexes, list) or command_index not in {int(index) for index in summarized_indexes}:
                continue
            response.response = _summarized_sandbox_placeholder(
                payload,
                stdout_payload,
                latest_note_by_audit.get(audit_id),
            )


def _summarized_sandbox_placeholder(
    payload: dict[str, Any],
    stdout_payload: dict[str, Any],
    latest_note: Any,
) -> dict[str, Any]:
    audit_id = str(stdout_payload.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
    placeholder: dict[str, Any] = {
        "status": "sandbox_context_removed_after_note_refinement",
        "audit_id": audit_id,
        "file_path": str(payload.get("file_path") or ""),
        "original_status": str(stdout_payload.get("status") or payload.get("status") or ""),
        "reason": (
            "This sandbox command response was fused into runtime notes after a later command. "
            "The newest unsummarized sandbox command remains available in full."
        ),
    }
    for key in ("command_index", "exit_code", "guardrail", "error", "error_type"):
        if key in stdout_payload:
            placeholder[key] = stdout_payload[key]
        elif key in payload:
            placeholder[key] = payload[key]
    paths = _compact_output_paths(stdout_payload) or _compact_output_paths(payload)
    if paths:
        placeholder["paths"] = paths
    artifact_handles = _compact_artifact_handles(payload) or _compact_artifact_handles(stdout_payload)
    if artifact_handles:
        placeholder["artifact_handles"] = artifact_handles
    if latest_note:
        placeholder["latest_runtime_note"] = latest_note
    return placeholder


def _prune_completed_sandbox_contexts(llm_request: LlmRequest, notes: list[Any]) -> None:
    pruneable_audits = _completed_sandbox_audits(llm_request)
    if not pruneable_audits:
        return
    latest_note_by_audit = _latest_note_by_audit(notes)
    for content in llm_request.contents:
        for part in content.parts or []:
            response = getattr(part, "function_response", None)
            if not response or response.name != "run_skill_script":
                continue
            payload = response.response or {}
            if not isinstance(payload, dict):
                continue
            if payload.get("status") == "sandbox_context_removed_after_completion":
                continue
            if payload.get("skill_name") != "sandbox-page-analyst":
                continue
            stdout_payload = _parse_skill_script_stdout(payload)
            audit_id = str(stdout_payload.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
            if audit_id not in pruneable_audits:
                continue
            response.response = _completed_sandbox_placeholder(payload, stdout_payload, latest_note_by_audit.get(audit_id))


def _completed_sandbox_audits(llm_request: LlmRequest) -> set[str]:
    finalized: set[str] = set()
    terminal: set[str] = set()
    persistence_succeeded = False

    for content in llm_request.contents:
        for part in content.parts or []:
            response = getattr(part, "function_response", None)
            if not response:
                continue
            payload = response.response or {}
            if not isinstance(payload, dict):
                continue

            if response.name in {"persist_sandbox_job_extraction", "promote_sandbox_extraction"}:
                if payload.get("status") == "success" and int(payload.get("written_count") or 0) > 0:
                    persistence_succeeded = True
                continue

            if response.name != "run_skill_script" or payload.get("skill_name") != "sandbox-page-analyst":
                continue
            file_path = str(payload.get("file_path") or "")
            stdout_payload = _parse_skill_script_stdout(payload)
            audit_id = str(stdout_payload.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
            if not audit_id:
                continue
            status = str(stdout_payload.get("status") or payload.get("status") or "")
            if status == "guardrail_triggered":
                terminal.add(audit_id)
            if file_path.endswith("sandbox_finalize.py") and status in {"finalized", "success"}:
                finalized.add(audit_id)

    if persistence_succeeded:
        terminal.update(finalized)
    return terminal


def _latest_note_by_audit(notes: list[Any]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for note in notes:
        if isinstance(note, dict):
            audit_id = str(note.get("audit_id") or "")
            if audit_id:
                latest[audit_id] = note
    return latest


def _completed_sandbox_placeholder(
    payload: dict[str, Any],
    stdout_payload: dict[str, Any],
    latest_note: Any,
) -> dict[str, Any]:
    audit_id = str(stdout_payload.get("audit_id") or payload.get("audit_id") or _extract_audit_id(payload) or "")
    placeholder: dict[str, Any] = {
        "status": "sandbox_context_removed_after_completion",
        "audit_id": audit_id,
        "file_path": str(payload.get("file_path") or ""),
        "original_status": str(stdout_payload.get("status") or payload.get("status") or ""),
        "reason": (
            "Sandbox loop is terminal; detailed command response was removed from model context. "
            "Use ADK artifacts, sandbox output paths, or runtime notes for audit details."
        ),
    }
    for key in ("command_index", "exit_code", "guardrail", "error", "error_type"):
        if key in stdout_payload:
            placeholder[key] = stdout_payload[key]
        elif key in payload:
            placeholder[key] = payload[key]
    paths = _compact_output_paths(stdout_payload) or _compact_output_paths(payload)
    if paths:
        placeholder["paths"] = paths
    artifact_handles = _compact_artifact_handles(payload) or _compact_artifact_handles(stdout_payload)
    if artifact_handles:
        placeholder["artifact_handles"] = artifact_handles
    if latest_note:
        placeholder["latest_runtime_note"] = latest_note
    return placeholder


def _sandbox_command_note_source(payload: dict[str, Any], file_path: str) -> dict[str, Any]:
    source: dict[str, Any] = {
        "file_path": file_path,
        "status": str(payload.get("status") or ""),
        "audit_id": str(payload.get("audit_id") or _extract_audit_id(payload) or ""),
    }
    for key in (
        "command_index",
        "exit_code",
        "message",
        "error",
        "error_type",
        "guardrail",
        "stdout_truncated",
        "stderr_truncated",
        "stdout_bytes",
        "stderr_bytes",
    ):
        if key in payload:
            source[key] = payload[key]
    for key in ("stdout", "stdout_preview", "stderr", "stderr_preview"):
        value = payload.get(key)
        if value is not None:
            source[key] = _preview(str(value), 2_000)
    paths = _compact_output_paths(payload)
    if paths:
        source["paths"] = paths
    artifact_handles = _compact_artifact_handles(payload)
    if artifact_handles:
        source["artifact_handles"] = artifact_handles
    return source


def _sandbox_command_sort_key(command: dict[str, Any]) -> int:
    try:
        return int(command.get("command_index") or 0)
    except (TypeError, ValueError):
        return 0


def _compact_run_skill_script_result(
    result: dict[str, Any],
    tool_args: dict[str, Any],
    preview_max_chars: int,
) -> dict[str, Any]:
    parsed_stdout = _parse_skill_script_stdout(result)
    payload = parsed_stdout if parsed_stdout is not result else result
    compact = _compact_sandbox_response(payload, min(preview_max_chars, 500))
    compact["skill_name"] = str(result.get("skill_name") or tool_args.get("skill_name") or "")
    compact["file_path"] = str(result.get("file_path") or tool_args.get("file_path") or "")
    if result.get("status") and result.get("status") != compact.get("status"):
        compact["tool_status"] = result["status"]
    if result.get("stderr") and result.get("stderr") != payload.get("stderr"):
        compact["tool_stderr"] = _preview(str(result["stderr"]), min(preview_max_chars, 500))
    return compact


def _compact_sandbox_response(payload: dict[str, Any], preview_max_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "status": str(payload.get("status") or "sandbox_context_compacted"),
        "audit_id": str(payload.get("audit_id") or _extract_audit_id(payload) or ""),
    }

    # ADK Web displays the start of large function responses first. Put the
    # actual bounded output preview before artifact/path metadata so the model
    # and human debugger see useful text without expanding nested handles.
    for key in (
        "exit_code",
        "command_index",
    ):
        if key in payload:
            compact[key] = payload[key]

    stdout_value = payload.get("stdout")
    if stdout_value is None:
        stdout_value = payload.get("stdout_preview")
    if stdout_value is not None:
        stdout_preview = _preview(str(stdout_value), preview_max_chars)
        compact["stdout"] = stdout_preview
        compact["stdout_preview"] = stdout_preview

    stderr_value = payload.get("stderr")
    if stderr_value is None:
        stderr_value = payload.get("stderr_preview")
    if stderr_value is not None:
        stderr_preview = _preview(str(stderr_value), preview_max_chars)
        compact["stderr"] = stderr_preview
        compact["stderr_preview"] = stderr_preview

    for key in (
        "stdout_truncated",
        "stderr_truncated",
        "returned_stdout_chars",
        "returned_stderr_chars",
        "stdout_bytes",
        "stderr_bytes",
        "message",
        "observation",
        "error_type",
        "path",
        "model",
        "written",
        "errors",
        "guardrail",
        "error",
        "expected_count",
        "actual_count",
        "missing_required_outputs",
    ):
        if key in payload:
            compact[key] = payload[key]

    paths = _compact_output_paths(payload)
    if paths:
        compact["paths"] = paths

    artifact_handles = _compact_artifact_handles(payload)
    if artifact_handles:
        compact["artifact_handles"] = artifact_handles

    compact["compacted"] = True
    return compact


def _compact_artifact_handles(payload: dict[str, Any]) -> dict[str, Any]:
    handles: dict[str, Any] = {}
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        for source_key, target_key in (
            ("command", "command_file"),
            ("stdout", "stdout_file"),
            ("stderr", "stderr_file"),
            ("trace", "trace_file"),
        ):
            artifact = artifacts.get(source_key)
            if isinstance(artifact, dict):
                handles[target_key] = artifact
        for key, artifact in artifacts.items():
            if key not in {"command", "stdout", "stderr", "trace"} and isinstance(artifact, dict):
                handles[str(key)] = artifact
    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        handles.setdefault("primary", artifact)
    return handles


def _compact_output_paths(payload: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, str] = {}
    existing_paths = payload.get("paths")
    if isinstance(existing_paths, dict):
        for key in ("command_path", "stdout_path", "stderr_path", "trace_path"):
            value = existing_paths.get(key)
            if value:
                paths[key] = str(value)
    output_policy = payload.get("output_policy")
    if isinstance(output_policy, dict):
        for key in ("command_path", "stdout_path", "stderr_path"):
            value = output_policy.get(key)
            if value:
                paths[key] = str(value)
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        for artifact_key, path_key in (
            ("command", "command_path"),
            ("stdout", "stdout_path"),
            ("stderr", "stderr_path"),
            ("trace", "trace_path"),
        ):
            artifact = artifacts.get(artifact_key)
            if isinstance(artifact, dict) and artifact.get("path"):
                paths.setdefault(path_key, str(artifact["path"]))
    return paths


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
    status = str(payload.get("status") or "").lower()
    has_error = status in {"error", "blocked", "guardrail_triggered"} or bool(payload.get("error") or payload.get("error_type"))
    if not has_error:
        return result

    updated = dict(result)
    for key in (
        "status",
        "audit_id",
        "error",
        "error_type",
        "guardrail",
        "missing_files",
        "required_files",
        "ignored_inline_args",
        "count",
        "written_count",
    ):
        if key in payload:
            updated[key] = payload[key]
    if "tool_status" not in updated and result.get("status") and result.get("status") != updated.get("status"):
        updated["tool_status"] = result["status"]
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
        "  'output/extractor.py',\n"
        "  'output/page_profile.json',\n"
        "  'output/extraction_strategy.json',\n"
        "  'output/candidates.json',\n"
        "  'output/validation.json',\n"
        "  'output/final.json',\n"
        "  'output/reference_proposal.md',\n"
        "  'output/reference_proposal.json',\n"
        "]\n"
        "missing = [p for p in required if not Path(p).exists()]\n"
        "page_files = [str(p) for p in Path('.').glob('*.html')]\n"
        "payload = {\n"
        "  'status': 'sandbox_running',\n"
        "  'page_files': page_files,\n"
        "  'missing_required_outputs': missing,\n"
        "  'required_next': 'Continue the sandbox workflow: inspect page evidence, derive patterns, write/run extractor code, persist protocol files from extractor output, validate, then finalize.',\n"
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
