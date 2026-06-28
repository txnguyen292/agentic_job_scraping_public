from __future__ import annotations

from typing import Any

from google.adk.models.llm_response import LlmResponse


async def after_model_callback(
    *,
    callback_context: Any,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    from job_scraper import adk_plugins as plugin_facade

    state = getattr(callback_context, "state", None)
    if not plugin_facade._is_state_like(state):
        return None
    planned_replacement = plugin_facade._planned_next_tool_model_replacement(state, llm_response)
    if planned_replacement:
        return planned_replacement
    repair_replacement = plugin_facade._active_repair_target_model_replacement(state, llm_response)
    if repair_replacement:
        return repair_replacement
    active_replacement = plugin_facade._active_sandbox_model_replacement(state, llm_response)
    if active_replacement:
        return active_replacement
    return None
