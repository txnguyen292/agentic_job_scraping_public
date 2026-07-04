from __future__ import annotations

import json
from typing import Any

from google.adk.models.llm_request import LlmRequest

from job_scraper.adk_plugin_modules.note_refinement import (
    SANDBOX_SUMMARIZED_COMMANDS_STATE_KEY,
    WORKFLOW_EVENT_GROUP,
    WORKFLOW_EVENT_SEQUENCE_STATE_KEY,
    WORKFLOW_SUMMARIZED_EVENTS_STATE_KEY,
)
from job_scraper.adk_plugin_modules.sandbox_guard.artifacts import (
    _compact_artifact_handles,
    _compact_output_paths,
)
from job_scraper.runtime_payload import (
    COMPACT_SANDBOX_RESPONSE_DIRECT_KEYS,
    COMPACT_SANDBOX_RESPONSE_METADATA_KEYS,
    SANDBOX_COMMAND_NOTE_KEYS,
    SANDBOX_COMMAND_SUMMARY_KEYS,
    SANDBOX_LIKE_OUTPUT_KEYS,
    SANDBOX_PLACEHOLDER_KEYS,
    SANDBOX_TEXT_PREVIEW_KEYS,
    TEXT_PREVIEW_PAYLOAD_KEYS,
    WORKFLOW_EVENT_NOTE_KEYS,
    RuntimePayloadKey,
    RuntimeStatus,
)
from job_scraper.tool_policy import ToolName


def _looks_like_sandbox_payload(payload: dict[str, Any]) -> bool:
    if str(payload.get(RuntimePayloadKey.SKILL_NAME) or "") == "sandbox-page-analyst":
        return True
    if RuntimePayloadKey.AUDIT_ID in payload:
        return True
    return any(key in payload for key in SANDBOX_LIKE_OUTPUT_KEYS)


def _workflow_tool_event_note_source(
    tool: Any,
    tool_args: dict[str, Any],
    result: dict[str, Any],
    state: Any,
) -> dict[str, Any] | None:
    from job_scraper import adk_plugins as plugin_facade

    if not isinstance(result, dict):
        return None
    tool_name = str(getattr(tool, "name", "") or "")
    if not _is_workflow_note_tool(tool_name, tool_args, result):
        return None

    sequence = int(state.get(WORKFLOW_EVENT_SEQUENCE_STATE_KEY) or 0) + 1
    state[WORKFLOW_EVENT_SEQUENCE_STATE_KEY] = sequence

    payload = plugin_facade._parse_skill_script_stdout(result) if tool_name == ToolName.RUN_SKILL_SCRIPT else result
    if not isinstance(payload, dict):
        payload = result
    audit_id = str(payload.get(RuntimePayloadKey.AUDIT_ID) or result.get(RuntimePayloadKey.AUDIT_ID) or plugin_facade._extract_audit_id(payload) or "")
    if not audit_id and tool_name == ToolName.RUN_SKILL_SCRIPT and tool_args.get(RuntimePayloadKey.SKILL_NAME) == "sandbox-page-analyst":
        audit_id = WORKFLOW_EVENT_GROUP
    elif not audit_id and tool_name in {
        ToolName.UPDATE_EXTRACTION_CONTEXT,
        ToolName.LOAD_SKILL,
        ToolName.LOAD_SKILL_RESOURCE,
        ToolName.LIST_SKILL_RESOURCES,
        ToolName.LOAD_TEST_FIXTURE_PAGE_TO_WORKSPACE,
    }:
        audit_id = WORKFLOW_EVENT_GROUP

    source: dict[str, Any] = {
        RuntimePayloadKey.EVENT_INDEX.value: sequence,
        RuntimePayloadKey.TOOL_NAME.value: tool_name,
        RuntimePayloadKey.STATUS.value: str(
            payload.get(RuntimePayloadKey.STATUS) or result.get(RuntimePayloadKey.STATUS) or ""
        ),
    }
    if audit_id:
        source[RuntimePayloadKey.AUDIT_ID.value] = audit_id
    skill_name = str(
        result.get(RuntimePayloadKey.SKILL_NAME)
        or tool_args.get(RuntimePayloadKey.SKILL_NAME)
        or payload.get(RuntimePayloadKey.SKILL_NAME)
        or ""
    )
    file_path = str(
        result.get(RuntimePayloadKey.FILE_PATH)
        or tool_args.get(RuntimePayloadKey.FILE_PATH)
        or payload.get(RuntimePayloadKey.FILE_PATH)
        or ""
    )
    if tool_name == ToolName.RUN_SKILL_SCRIPT and file_path.endswith("sandbox_exec.py") and audit_id:
        source[RuntimePayloadKey.NOTE_GROUP.value] = audit_id
    else:
        source[RuntimePayloadKey.NOTE_GROUP.value] = WORKFLOW_EVENT_GROUP
    if skill_name:
        source[RuntimePayloadKey.SKILL_NAME.value] = skill_name
    if file_path:
        source[RuntimePayloadKey.FILE_PATH.value] = file_path
    for key in WORKFLOW_EVENT_NOTE_KEYS:
        if key in payload:
            source[key] = plugin_facade._compact_latest_value(payload[key], 1_000)
        elif key in result:
            source[key] = plugin_facade._compact_latest_value(result[key], 1_000)
    for key in TEXT_PREVIEW_PAYLOAD_KEYS:
        value = payload.get(key) if key in payload else result.get(key)
        if value is not None:
            source[key] = plugin_facade._preview(str(value), 1_000)
    paths = _compact_output_paths(payload) or _compact_output_paths(result)
    if paths:
        source[RuntimePayloadKey.PATHS.value] = paths
    artifact_handles = _compact_artifact_handles(result) or _compact_artifact_handles(payload)
    if artifact_handles:
        source[RuntimePayloadKey.ARTIFACT_HANDLES.value] = artifact_handles
    return source


def _is_workflow_note_tool(tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]) -> bool:
    if tool_name in {
        ToolName.UPDATE_EXTRACTION_CONTEXT,
        ToolName.LOAD_SKILL,
        ToolName.LOAD_SKILL_RESOURCE,
        ToolName.LIST_SKILL_RESOURCES,
        ToolName.LOAD_TEST_FIXTURE_PAGE_TO_WORKSPACE,
        ToolName.FETCH_PAGE_TO_WORKSPACE,
        ToolName.RENDER_PAGE_TO_WORKSPACE,
        ToolName.PROMOTE_SANDBOX_EXTRACTION,
        ToolName.PERSIST_SANDBOX_JOB_EXTRACTION,
        ToolName.QUERY_JOBS,
    }:
        return True
    if tool_name == ToolName.RUN_SKILL_SCRIPT and tool_args.get(RuntimePayloadKey.SKILL_NAME) == "sandbox-page-analyst":
        return True
    return _looks_like_sandbox_payload(result)


def _workflow_event_sort_key(event: dict[str, Any]) -> int:
    try:
        command_index = int(event.get(RuntimePayloadKey.COMMAND_INDEX) or 0)
    except (TypeError, ValueError):
        command_index = 0
    if command_index > 0:
        return command_index
    try:
        return int(event.get(RuntimePayloadKey.EVENT_INDEX) or 0)
    except (TypeError, ValueError):
        return 0


def _mark_summarized_workflow_events(state: Any, events: list[dict[str, Any]]) -> None:
    summarized = state.setdefault(WORKFLOW_SUMMARIZED_EVENTS_STATE_KEY, [])
    if not isinstance(summarized, list):
        summarized = []
        state[WORKFLOW_SUMMARIZED_EVENTS_STATE_KEY] = summarized
    existing = {int(index) for index in summarized if str(index).isdigit()}
    for event in events:
        try:
            existing.add(int(event.get(RuntimePayloadKey.EVENT_INDEX) or 0))
        except (TypeError, ValueError):
            continue
    state[WORKFLOW_SUMMARIZED_EVENTS_STATE_KEY] = sorted(index for index in existing if index > 0)


def _prune_summarized_workflow_events(llm_request: LlmRequest, notes: list[Any], state: Any) -> None:
    summarized = state.get(WORKFLOW_SUMMARIZED_EVENTS_STATE_KEY)
    if not isinstance(summarized, list):
        return
    summarized_indexes = {int(index) for index in summarized if str(index).isdigit()}
    if not summarized_indexes:
        return
    latest_note_by_audit = _latest_note_by_audit(notes)
    event_index = 0
    for content in llm_request.contents:
        for part in content.parts or []:
            response = getattr(part, "function_response", None)
            if not response:
                continue
            if str(response.name or "") == "run_skill_script":
                continue
            payload = response.response or {}
            if not isinstance(payload, dict):
                continue
            event_index += 1
            if event_index not in summarized_indexes:
                continue
            if _is_workflow_event_placeholder(payload):
                continue
            response.response = _summarized_workflow_event_placeholder(
                tool_name=str(response.name or ""),
                payload=payload,
                event_index=event_index,
                latest_note_by_audit=latest_note_by_audit,
            )


def _is_workflow_event_placeholder(payload: dict[str, Any]) -> bool:
    return str(payload.get(RuntimePayloadKey.STATUS) or "") in {
        RuntimeStatus.WORKFLOW_EVENT_CONTEXT_REMOVED_AFTER_NOTE_REFINEMENT,
        RuntimeStatus.SANDBOX_CONTEXT_REMOVED_AFTER_NOTE_REFINEMENT,
        RuntimeStatus.SANDBOX_CONTEXT_REMOVED_AFTER_COMPLETION,
        RuntimeStatus.RESOURCE_CONTEXT_REMOVED_AFTER_STATE_UPDATE,
        RuntimeStatus.RESOURCE_CONTEXT_COMPACTED_KEEP_LATEST_ONLY,
    }


def _summarized_workflow_event_placeholder(
    *,
    tool_name: str,
    payload: dict[str, Any],
    event_index: int,
    latest_note_by_audit: dict[str, Any],
) -> dict[str, Any]:
    from job_scraper import adk_plugins as plugin_facade

    parsed = plugin_facade._parse_skill_script_stdout(payload) if tool_name == ToolName.RUN_SKILL_SCRIPT else payload
    if not isinstance(parsed, dict):
        parsed = payload
    audit_id = str(
        parsed.get(RuntimePayloadKey.AUDIT_ID)
        or payload.get(RuntimePayloadKey.AUDIT_ID)
        or plugin_facade._extract_audit_id(parsed)
        or WORKFLOW_EVENT_GROUP
    )
    placeholder: dict[str, Any] = {
        RuntimePayloadKey.STATUS.value: RuntimeStatus.WORKFLOW_EVENT_CONTEXT_REMOVED_AFTER_NOTE_REFINEMENT.value,
        RuntimePayloadKey.TOOL_NAME.value: tool_name,
        RuntimePayloadKey.EVENT_INDEX.value: event_index,
        RuntimePayloadKey.AUDIT_ID.value: audit_id,
        RuntimePayloadKey.ORIGINAL_STATUS.value: str(
            parsed.get(RuntimePayloadKey.STATUS) or payload.get(RuntimePayloadKey.STATUS) or ""
        ),
        RuntimePayloadKey.REASON.value: (
            "This older ADK tool response was fused into a runtime note under 200 words. "
            "The latest unsummarized tool response remains available in full."
        ),
    }
    for key in SANDBOX_PLACEHOLDER_KEYS:
        if key in parsed:
            placeholder[key] = plugin_facade._compact_latest_value(parsed[key], 500)
        elif key in payload:
            placeholder[key] = plugin_facade._compact_latest_value(payload[key], 500)
    paths = _compact_output_paths(parsed) or _compact_output_paths(payload)
    if paths:
        placeholder[RuntimePayloadKey.PATHS.value] = paths
    artifact_handles = _compact_artifact_handles(payload) or _compact_artifact_handles(parsed)
    if artifact_handles:
        placeholder[RuntimePayloadKey.ARTIFACT_HANDLES.value] = artifact_handles
    latest_note = latest_note_by_audit.get(audit_id) or latest_note_by_audit.get(WORKFLOW_EVENT_GROUP)
    if latest_note:
        placeholder[RuntimePayloadKey.LATEST_RUNTIME_NOTE.value] = latest_note
    return placeholder


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
        command_index = command.get(RuntimePayloadKey.COMMAND_INDEX)
        try:
            existing.add(int(command_index))
        except (TypeError, ValueError):
            continue
    summarized[audit_id] = sorted(existing)


def _prune_summarized_sandbox_contexts(llm_request: LlmRequest, notes: list[Any], state: Any) -> None:
    from job_scraper import adk_plugins as plugin_facade

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
            if payload.get(RuntimePayloadKey.STATUS) in {
                RuntimeStatus.SANDBOX_CONTEXT_REMOVED_AFTER_NOTE_REFINEMENT,
                RuntimeStatus.SANDBOX_CONTEXT_REMOVED_AFTER_COMPLETION,
            }:
                continue
            if payload.get(RuntimePayloadKey.SKILL_NAME) != "sandbox-page-analyst":
                continue
            file_path = str(payload.get(RuntimePayloadKey.FILE_PATH) or "")
            if not file_path.endswith("sandbox_exec.py"):
                continue
            stdout_payload = plugin_facade._parse_skill_script_stdout(payload)
            audit_id = str(
                stdout_payload.get(RuntimePayloadKey.AUDIT_ID)
                or payload.get(RuntimePayloadKey.AUDIT_ID)
                or plugin_facade._extract_audit_id(payload)
                or ""
            )
            if not audit_id:
                continue
            try:
                command_index = int(stdout_payload.get(RuntimePayloadKey.COMMAND_INDEX) or payload.get(RuntimePayloadKey.COMMAND_INDEX) or 0)
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
    from job_scraper import adk_plugins as plugin_facade

    audit_id = str(
        stdout_payload.get(RuntimePayloadKey.AUDIT_ID)
        or payload.get(RuntimePayloadKey.AUDIT_ID)
        or plugin_facade._extract_audit_id(payload)
        or ""
    )
    placeholder: dict[str, Any] = {
        RuntimePayloadKey.STATUS.value: RuntimeStatus.SANDBOX_CONTEXT_REMOVED_AFTER_NOTE_REFINEMENT.value,
        RuntimePayloadKey.AUDIT_ID.value: audit_id,
        RuntimePayloadKey.FILE_PATH.value: str(payload.get(RuntimePayloadKey.FILE_PATH) or ""),
        RuntimePayloadKey.ORIGINAL_STATUS.value: str(
            stdout_payload.get(RuntimePayloadKey.STATUS) or payload.get(RuntimePayloadKey.STATUS) or ""
        ),
        RuntimePayloadKey.REASON.value: (
            "This sandbox command response was fused into runtime notes after a later command. "
            "The newest unsummarized sandbox command remains available in full."
        ),
    }
    for key in SANDBOX_COMMAND_SUMMARY_KEYS:
        if key in stdout_payload:
            placeholder[key] = stdout_payload[key]
        elif key in payload:
            placeholder[key] = payload[key]
    paths = _compact_output_paths(stdout_payload) or _compact_output_paths(payload)
    if paths:
        placeholder[RuntimePayloadKey.PATHS.value] = paths
    artifact_handles = _compact_artifact_handles(payload) or _compact_artifact_handles(stdout_payload)
    if artifact_handles:
        placeholder[RuntimePayloadKey.ARTIFACT_HANDLES.value] = artifact_handles
    if latest_note:
        placeholder[RuntimePayloadKey.LATEST_RUNTIME_NOTE.value] = latest_note
    return placeholder


def _prune_completed_sandbox_contexts(llm_request: LlmRequest, notes: list[Any]) -> None:
    from job_scraper import adk_plugins as plugin_facade

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
            if payload.get(RuntimePayloadKey.STATUS) == RuntimeStatus.SANDBOX_CONTEXT_REMOVED_AFTER_COMPLETION:
                continue
            if payload.get(RuntimePayloadKey.SKILL_NAME) != "sandbox-page-analyst":
                continue
            stdout_payload = plugin_facade._parse_skill_script_stdout(payload)
            audit_id = str(
                stdout_payload.get(RuntimePayloadKey.AUDIT_ID)
                or payload.get(RuntimePayloadKey.AUDIT_ID)
                or plugin_facade._extract_audit_id(payload)
                or ""
            )
            if audit_id not in pruneable_audits:
                continue
            response.response = _completed_sandbox_placeholder(payload, stdout_payload, latest_note_by_audit.get(audit_id))


def _completed_sandbox_audits(llm_request: LlmRequest) -> set[str]:
    from job_scraper import adk_plugins as plugin_facade

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

            if response.name in {ToolName.PERSIST_SANDBOX_JOB_EXTRACTION, ToolName.PROMOTE_SANDBOX_EXTRACTION}:
                if payload.get(RuntimePayloadKey.STATUS) == RuntimeStatus.SUCCESS and int(payload.get(RuntimePayloadKey.WRITTEN_COUNT) or 0) > 0:
                    persistence_succeeded = True
                continue

            if response.name != ToolName.RUN_SKILL_SCRIPT or payload.get(RuntimePayloadKey.SKILL_NAME) != "sandbox-page-analyst":
                continue
            file_path = str(payload.get(RuntimePayloadKey.FILE_PATH) or "")
            stdout_payload = plugin_facade._parse_skill_script_stdout(payload)
            audit_id = str(
                stdout_payload.get(RuntimePayloadKey.AUDIT_ID)
                or payload.get(RuntimePayloadKey.AUDIT_ID)
                or plugin_facade._extract_audit_id(payload)
                or ""
            )
            if not audit_id:
                continue
            status = str(stdout_payload.get(RuntimePayloadKey.STATUS) or payload.get(RuntimePayloadKey.STATUS) or "")
            if status == RuntimeStatus.GUARDRAIL_TRIGGERED:
                terminal.add(audit_id)
            if file_path.endswith("sandbox_finalize.py") and status in {RuntimeStatus.FINALIZED, RuntimeStatus.SUCCESS}:
                finalized.add(audit_id)

    if persistence_succeeded:
        terminal.update(finalized)
    return terminal


def _latest_note_by_audit(notes: list[Any]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for note in notes:
        if isinstance(note, dict):
            audit_id = str(note.get(RuntimePayloadKey.AUDIT_ID) or "")
            if audit_id:
                latest[audit_id] = note
    return latest


def _completed_sandbox_placeholder(
    payload: dict[str, Any],
    stdout_payload: dict[str, Any],
    latest_note: Any,
) -> dict[str, Any]:
    from job_scraper import adk_plugins as plugin_facade

    audit_id = str(
        stdout_payload.get(RuntimePayloadKey.AUDIT_ID)
        or payload.get(RuntimePayloadKey.AUDIT_ID)
        or plugin_facade._extract_audit_id(payload)
        or ""
    )
    placeholder: dict[str, Any] = {
        RuntimePayloadKey.STATUS.value: RuntimeStatus.SANDBOX_CONTEXT_REMOVED_AFTER_COMPLETION.value,
        RuntimePayloadKey.AUDIT_ID.value: audit_id,
        RuntimePayloadKey.FILE_PATH.value: str(payload.get(RuntimePayloadKey.FILE_PATH) or ""),
        RuntimePayloadKey.ORIGINAL_STATUS.value: str(
            stdout_payload.get(RuntimePayloadKey.STATUS) or payload.get(RuntimePayloadKey.STATUS) or ""
        ),
        RuntimePayloadKey.REASON.value: (
            "Sandbox loop is terminal; detailed command response was removed from model context. "
            "Use ADK artifacts, sandbox output paths, or runtime notes for audit details."
        ),
    }
    for key in SANDBOX_COMMAND_SUMMARY_KEYS:
        if key in stdout_payload:
            placeholder[key] = stdout_payload[key]
        elif key in payload:
            placeholder[key] = payload[key]
    paths = _compact_output_paths(stdout_payload) or _compact_output_paths(payload)
    if paths:
        placeholder[RuntimePayloadKey.PATHS.value] = paths
    artifact_handles = _compact_artifact_handles(payload) or _compact_artifact_handles(stdout_payload)
    if artifact_handles:
        placeholder[RuntimePayloadKey.ARTIFACT_HANDLES.value] = artifact_handles
    if latest_note:
        placeholder[RuntimePayloadKey.LATEST_RUNTIME_NOTE.value] = latest_note
    return placeholder


def _sandbox_command_note_source(payload: dict[str, Any], file_path: str) -> dict[str, Any]:
    from job_scraper import adk_plugins as plugin_facade

    source: dict[str, Any] = {
        RuntimePayloadKey.FILE_PATH.value: file_path,
        RuntimePayloadKey.STATUS.value: str(payload.get(RuntimePayloadKey.STATUS) or ""),
        RuntimePayloadKey.AUDIT_ID.value: str(
            payload.get(RuntimePayloadKey.AUDIT_ID) or plugin_facade._extract_audit_id(payload) or ""
        ),
    }
    for key in SANDBOX_COMMAND_NOTE_KEYS:
        if key in payload:
            source[key] = payload[key]
    for key in SANDBOX_TEXT_PREVIEW_KEYS:
        value = payload.get(key)
        if value is not None:
            source[key] = plugin_facade._preview(str(value), 2_000)
    paths = _compact_output_paths(payload)
    if paths:
        source[RuntimePayloadKey.PATHS.value] = paths
    artifact_handles = _compact_artifact_handles(payload)
    if artifact_handles:
        source[RuntimePayloadKey.ARTIFACT_HANDLES.value] = artifact_handles
    return source


def _sandbox_command_sort_key(command: dict[str, Any]) -> int:
    try:
        return int(command.get(RuntimePayloadKey.COMMAND_INDEX) or 0)
    except (TypeError, ValueError):
        return 0


def _compact_run_skill_script_result(
    result: dict[str, Any],
    tool_args: dict[str, Any],
    preview_max_chars: int,
) -> dict[str, Any]:
    from job_scraper import adk_plugins as plugin_facade

    parsed_stdout = plugin_facade._parse_skill_script_stdout(result)
    payload = parsed_stdout if parsed_stdout is not result else result
    compact = _compact_sandbox_response(payload, min(preview_max_chars, 500))
    compact[RuntimePayloadKey.SKILL_NAME.value] = str(
        result.get(RuntimePayloadKey.SKILL_NAME) or tool_args.get(RuntimePayloadKey.SKILL_NAME) or ""
    )
    compact[RuntimePayloadKey.FILE_PATH.value] = str(
        result.get(RuntimePayloadKey.FILE_PATH) or tool_args.get(RuntimePayloadKey.FILE_PATH) or ""
    )
    if result.get(RuntimePayloadKey.STATUS) and result.get(RuntimePayloadKey.STATUS) != compact.get(RuntimePayloadKey.STATUS):
        compact[RuntimePayloadKey.TOOL_STATUS.value] = result[RuntimePayloadKey.STATUS]
    if result.get(RuntimePayloadKey.STDERR) and result.get(RuntimePayloadKey.STDERR) != payload.get(RuntimePayloadKey.STDERR):
        compact[RuntimePayloadKey.TOOL_STDERR.value] = plugin_facade._preview(
            str(result[RuntimePayloadKey.STDERR]),
            min(preview_max_chars, 500),
        )
    return compact


def _compact_sandbox_response(payload: dict[str, Any], preview_max_chars: int) -> dict[str, Any]:
    from job_scraper import adk_plugins as plugin_facade

    compact: dict[str, Any] = {
        RuntimePayloadKey.STATUS.value: str(
            payload.get(RuntimePayloadKey.STATUS) or RuntimeStatus.SANDBOX_CONTEXT_COMPACTED
        ),
        RuntimePayloadKey.AUDIT_ID.value: str(
            payload.get(RuntimePayloadKey.AUDIT_ID) or plugin_facade._extract_audit_id(payload) or ""
        ),
    }

    # ADK Web displays the start of large function responses first. Put the
    # actual bounded output preview before artifact/path metadata so the model
    # and human debugger see useful text without expanding nested handles.
    for key in COMPACT_SANDBOX_RESPONSE_DIRECT_KEYS:
        if key in payload:
            compact[key] = payload[key]

    stdout_value = payload.get(RuntimePayloadKey.STDOUT)
    if stdout_value is None:
        stdout_value = payload.get(RuntimePayloadKey.STDOUT_PREVIEW)
    if stdout_value is not None:
        stdout_preview = plugin_facade._preview(str(stdout_value), preview_max_chars)
        compact[RuntimePayloadKey.STDOUT.value] = stdout_preview
        compact[RuntimePayloadKey.STDOUT_PREVIEW.value] = stdout_preview

    stderr_value = payload.get(RuntimePayloadKey.STDERR)
    if stderr_value is None:
        stderr_value = payload.get(RuntimePayloadKey.STDERR_PREVIEW)
    if stderr_value is not None:
        stderr_preview = plugin_facade._preview(str(stderr_value), preview_max_chars)
        compact[RuntimePayloadKey.STDERR.value] = stderr_preview
        compact[RuntimePayloadKey.STDERR_PREVIEW.value] = stderr_preview

    for key in COMPACT_SANDBOX_RESPONSE_METADATA_KEYS:
        if key in payload:
            compact[key] = payload[key]

    paths = _compact_output_paths(payload)
    if paths:
        compact[RuntimePayloadKey.PATHS.value] = paths

    artifact_handles = _compact_artifact_handles(payload)
    if artifact_handles:
        compact[RuntimePayloadKey.ARTIFACT_HANDLES.value] = artifact_handles

    compact[RuntimePayloadKey.COMPACTED.value] = True
    return compact
