from __future__ import annotations

import inspect
import json
import os
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types as genai_types


SANDBOX_NOTE_BUFFER_STATE_KEY = "_job_scraper_sandbox_note_buffer"
SANDBOX_NOTES_STATE_KEY = "_job_scraper_sandbox_notes"
SANDBOX_NOTE_ERROR_STATE_KEY = "_job_scraper_sandbox_note_errors"
SANDBOX_SUMMARIZED_COMMANDS_STATE_KEY = "_job_scraper_sandbox_summarized_commands"
WORKFLOW_EVENT_SEQUENCE_STATE_KEY = "_job_scraper_workflow_event_sequence"
WORKFLOW_SUMMARIZED_EVENTS_STATE_KEY = "_job_scraper_workflow_summarized_events"
WORKFLOW_EVENT_GROUP = "workflow"
DEFAULT_NOTE_REFINEMENT_MODEL = (
    os.getenv("SANDBOX_NOTE_REFINEMENT_MODEL")
    or os.getenv("JOB_SCRAPER_LLM_MODEL")
    or os.getenv("OPENAI_MODEL")
    or "openai/gpt-5.4-mini"
)


class SandboxNoteRefinementPlugin(BasePlugin):
    """Periodically summarize workflow tool events and inject notes into model requests."""

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
        state = getattr(tool_context, "state", None)
        if not _is_state_like(state):
            return None

        from job_scraper import adk_plugins as plugin_facade

        event = plugin_facade._workflow_tool_event_note_source(tool, tool_args, result, state)
        if not event:
            return None
        audit_id = str(event.get("note_group") or event.get("audit_id") or WORKFLOW_EVENT_GROUP)
        event_index = int(event.get("event_index") or 0)
        command_index = int(event.get("command_index") or 0)

        buffers = state.setdefault(SANDBOX_NOTE_BUFFER_STATE_KEY, {})
        if not isinstance(buffers, dict):
            buffers = {}
            state[SANDBOX_NOTE_BUFFER_STATE_KEY] = buffers
        buffer = buffers.setdefault(audit_id, [])
        if not isinstance(buffer, list):
            buffer = []
            buffers[audit_id] = buffer
        buffer.append(event)

        # Keep the newest event response full. When event N+1 arrives,
        # summarize the previous N responses and leave event N+1 unsummarized.
        if len(buffer) <= self.command_interval:
            return None

        ordered_buffer = sorted(buffer, key=plugin_facade._workflow_event_sort_key)
        events_to_summarize = list(ordered_buffer[: self.command_interval])
        remaining_events = list(ordered_buffer[self.command_interval :])
        buffers[audit_id] = remaining_events
        kept_full_event_index = int(
            (remaining_events[-1] if remaining_events else events_to_summarize[-1]).get("event_index")
            or event_index
        )
        kept_full_command_index = int(
            (remaining_events[-1] if remaining_events else events_to_summarize[-1]).get("command_index")
            or command_index
        )
        try:
            current_notes = state.get(SANDBOX_NOTES_STATE_KEY)
            visible_notes = current_notes[-self.max_notes :] if isinstance(current_notes, list) else []
            summary = await self._summarize(audit_id, events_to_summarize, visible_notes)
        except Exception as exc:  # Do not fail the user workflow because note refinement failed.
            errors = state.setdefault(SANDBOX_NOTE_ERROR_STATE_KEY, [])
            if isinstance(errors, list):
                errors.append(
                    {"audit_id": audit_id, "event_index": event_index, "command_index": command_index, "error": str(exc)}
                )
            return None

        notes = state.setdefault(SANDBOX_NOTES_STATE_KEY, [])
        if not isinstance(notes, list):
            notes = []
            state[SANDBOX_NOTES_STATE_KEY] = notes
        notes.append(
            {
                "audit_id": audit_id,
                "through_event_index": int(events_to_summarize[-1].get("event_index") or event_index),
                "kept_full_event_index": kept_full_event_index,
                "through_command_index": int(events_to_summarize[-1].get("command_index") or command_index),
                "kept_full_command_index": kept_full_command_index,
                "event_count": len(events_to_summarize),
                "summary": summary,
            }
        )
        del notes[:-self.max_notes]
        plugin_facade._mark_summarized_workflow_events(state, events_to_summarize)
        plugin_facade._mark_summarized_commands(state, audit_id, events_to_summarize)
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

        from job_scraper import adk_plugins as plugin_facade

        if self.prune_completed_sandbox_context:
            plugin_facade._prune_summarized_workflow_events(llm_request, visible_notes, state)
            plugin_facade._prune_summarized_sandbox_contexts(llm_request, visible_notes, state)
            plugin_facade._prune_completed_sandbox_contexts(llm_request, visible_notes)

        if visible_notes:
            llm_request.contents.append(
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_text(
                            text=(
                                "<RUNTIME_SANDBOX_NOTES>\n"
                                "purpose: supporting evidence from compacted ADK workflow/tool history.\n"
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
        events: list[dict[str, Any]],
        current_notes: list[Any],
    ) -> str:
        if self.summarizer is not None:
            try:
                maybe = self.summarizer(audit_id, current_notes, events)
            except TypeError:
                maybe = self.summarizer(audit_id, events)
            if inspect.isawaitable(maybe):
                return str(await maybe)
            return str(maybe)

        from litellm import acompletion
        from job_scraper import adk_plugins as plugin_facade

        prompt = (
            "Summarize these ADK workflow tool results for a job-page extraction agent. "
            "Fuse the current notes with the tool results, and return one concise updated note. "
            "The note must be under 200 words. "
            "Focus on: observations, extraction_plan implications, "
            "result-vs-requirement comparison, errors, artifact paths, and next repair facts. "
            "Do not invent page facts. Do not include raw HTML or long stdout.\n\n"
            f"audit_id: {audit_id}\n"
            f"current_notes_json: {plugin_facade._preview(json.dumps(current_notes, ensure_ascii=True, sort_keys=True, default=str), 6_000)}\n"
            f"events_json: {plugin_facade._preview(json.dumps(events, ensure_ascii=True, sort_keys=True, default=str), 12_000)}"
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


def _is_state_like(state: Any) -> bool:
    """Return true for ADK's delta-aware State and regular mutable mappings."""

    return (
        state is not None
        and callable(getattr(state, "get", None))
        and callable(getattr(state, "setdefault", None))
        and hasattr(state, "__setitem__")
    )
