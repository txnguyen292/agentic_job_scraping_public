from __future__ import annotations

from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from job_scraper.runtime_state import SESSION_EXTRACTION_CONTEXT_STATE_KEY


async def before_model_callback(
    *,
    callback_context: Any,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    from job_scraper import adk_plugins as plugin_facade

    state = getattr(callback_context, "state", None)
    plugin_facade._prune_loaded_resource_contexts(llm_request, state)
    plugin_facade._inject_latest_tool_result(llm_request)
    plugin_facade._inject_session_extraction_context(llm_request, state)
    plugin_facade._inject_finalized_sandbox_persistence_guard(llm_request, state)
    plugin_facade._inject_final_response_contract(llm_request, state)
    active = plugin_facade._active_sandbox_from_contents(llm_request.contents)
    if not active:
        start_guard_text = plugin_facade._workflow_start_guard_text_from_context(callback_context)
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
    state_active = state.get(plugin_facade.ACTIVE_SANDBOX_STATE_KEY) if plugin_facade._is_state_like(state) else None
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
    pending_script = plugin_facade._pending_scripts_for_active_sandbox(state, active) if plugin_facade._is_state_like(state) else {}
    session_context = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY) if plugin_facade._is_state_like(state) else None
    immediate_goal_error = plugin_facade._immediate_goal_validation_error(session_context) if isinstance(session_context, dict) else None
    if pending_script:
        next_action = (
            f"A workflow helper/script was written but has not been verified. Run the relevant focused command "
            f"for `{pending_script.get('path') or 'output/extractor.py'}` with sandbox_exec.py, then verify the "
            "required protocol artifacts exist before validate/finalize/persist/query."
        )
    elif isinstance(active.get("last_repair_target"), dict):
        repair = active["last_repair_target"]
        next_action = plugin_facade._repair_target_next_action(repair)
    elif immediate_goal_error:
        next_action = (
            "Before running sandbox probes as validation or writing/running producer scripts, call "
            "update_extraction_context with extraction_plan, extraction_strategy, and immediate_goal. "
            "The immediate_goal must name the current strategy step with evidence, strategy, validation, "
            "and next script/probe objective. For the first ITviec fixture goal, establish the repeated "
            "job-card unit boundary and the smallest validation probe for that boundary."
        )
    elif command_count >= 5:
        next_action = (
            "You likely have enough page inspection evidence. Continue the sandbox workflow by choosing the "
            "appropriate loaded sandbox tool: load bounded evidence or script output, write or repair accountable "
            "protocol files or supporting scripts, validate, and finalize only if validation has passed."
        )
    else:
        next_action = (
            "Continue the sandbox workflow by choosing the appropriate loaded sandbox tool to inspect the "
            "mounted page, derive repeated patterns, or write evidence/serialization helpers; do not answer "
            "the user yet."
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
                        "write helper/protocol files through the sandbox file-writing capability so they are "
                        "audited and validated. "
                        "Do not import bs4/lxml/parsel unless already verified installed in the sandbox.\n"
                        "</RUNTIME_SANDBOX_GUARD>"
                    )
                )
            ],
        )
    )
    return None
