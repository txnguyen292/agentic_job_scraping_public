from __future__ import annotations

import os
from typing import Any

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types as genai_types


MODEL_REASONING_TELEMETRY_STATE_KEY = "_job_scraper_model_reasoning"


class ModelReasoningTelemetryPlugin(BasePlugin):
    """Expose compact model reasoning signals as ADK Web-visible state."""

    def __init__(
        self,
        *,
        reasoning_effort: str | None = None,
        preview_max_chars: int = 500,
        surface_in_adk_web: bool = True,
        name: str = "model_reasoning_telemetry_plugin",
    ) -> None:
        super().__init__(name=name)
        self.reasoning_effort = reasoning_effort or os.getenv("JOB_SCRAPER_REASONING_EFFORT") or ""
        self.preview_max_chars = max(0, preview_max_chars)
        self.surface_in_adk_web = surface_in_adk_web

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        state = getattr(callback_context, "state", None)
        if not _is_state_like(state):
            return None
        telemetry = _model_reasoning_telemetry(
            llm_response,
            reasoning_effort=self.reasoning_effort,
            preview_max_chars=self.preview_max_chars,
        )
        if telemetry:
            state[MODEL_REASONING_TELEMETRY_STATE_KEY] = telemetry
            _attach_reasoning_telemetry_to_model_event(llm_response, telemetry)
            if self.surface_in_adk_web:
                _surface_reasoning_telemetry_as_adk_web_thought(llm_response, telemetry)
        return None


def _is_state_like(state: Any) -> bool:
    """Return true for ADK's delta-aware State and regular mutable mappings."""

    return (
        state is not None
        and callable(getattr(state, "get", None))
        and callable(getattr(state, "setdefault", None))
        and hasattr(state, "__setitem__")
    )


def _model_reasoning_telemetry(
    llm_response: LlmResponse,
    *,
    reasoning_effort: str,
    preview_max_chars: int,
) -> dict[str, Any] | None:
    thought_parts = _llm_response_thought_texts(llm_response)
    usage = getattr(llm_response, "usage_metadata", None)
    thoughts_token_count = getattr(usage, "thoughts_token_count", None) if usage is not None else None
    if not thought_parts and not thoughts_token_count and not reasoning_effort:
        return None

    telemetry: dict[str, Any] = {
        "model_version": str(getattr(llm_response, "model_version", "") or ""),
        "reasoning_effort": reasoning_effort,
        "thought_part_count": len(thought_parts),
    }
    if thought_parts and preview_max_chars:
        telemetry["reasoning_summary_preview"] = "\n".join(thought_parts)[:preview_max_chars]
    if thoughts_token_count:
        telemetry["thoughts_token_count"] = thoughts_token_count
    return telemetry


def _attach_reasoning_telemetry_to_model_event(
    llm_response: LlmResponse,
    telemetry: dict[str, Any],
) -> None:
    metadata = dict(llm_response.custom_metadata or {})
    event_payload = dict(telemetry)
    event_payload["adk_web_surface"] = "model_event_custom_metadata"
    metadata["job_scraper_reasoning"] = event_payload
    llm_response.custom_metadata = metadata


def _surface_reasoning_telemetry_as_adk_web_thought(
    llm_response: LlmResponse,
    telemetry: dict[str, Any],
) -> bool:
    if _llm_response_thought_texts(llm_response):
        return False

    display_text = _reasoning_telemetry_display_text(telemetry)
    if not display_text:
        return False
    if not str(telemetry.get("reasoning_summary_preview") or "").strip():
        return False

    if llm_response.content is None:
        llm_response.content = genai_types.Content(role="model", parts=[])
    if llm_response.content.parts is None:
        llm_response.content.parts = []

    llm_response.content.parts.insert(0, genai_types.Part(text=display_text, thought=True))
    metadata = dict(llm_response.custom_metadata or {})
    event_payload = dict(metadata.get("job_scraper_reasoning") or telemetry)
    event_payload["adk_web_thought_part"] = "synthetic_reasoning_telemetry"
    metadata["job_scraper_reasoning"] = event_payload
    llm_response.custom_metadata = metadata
    return True


def _reasoning_telemetry_display_text(telemetry: dict[str, Any]) -> str:
    preview = str(telemetry.get("reasoning_summary_preview") or "").strip()
    if preview:
        return preview

    fields: list[str] = []
    effort = str(telemetry.get("reasoning_effort") or "").strip()
    if effort:
        fields.append(f"effort={effort}")
    token_count = telemetry.get("thoughts_token_count")
    if token_count:
        fields.append(f"thoughts_token_count={token_count}")
    if not fields:
        return ""
    return "OpenAI reasoning telemetry: " + "; ".join(fields) + "."


def _llm_response_thought_texts(llm_response: LlmResponse) -> list[str]:
    content = getattr(llm_response, "content", None)
    if not content:
        return []
    texts: list[str] = []
    for part in content.parts or []:
        if getattr(part, "thought", False) and getattr(part, "text", None):
            texts.append(str(part.text))
    return texts
