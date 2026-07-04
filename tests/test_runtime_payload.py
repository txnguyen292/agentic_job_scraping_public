from __future__ import annotations

from job_scraper.runtime_payload import (
    EXTRACTION_CONTEXT_DIGEST_KEYS,
    OUTPUT_GATE_STORED_RESPONSE_KEYS,
    OUTPUT_GATE_TRIGGER_KEYS,
    RESOURCE_PLACEHOLDER_KEYS,
    RESOURCE_TEXT_PAYLOAD_KEYS,
    SESSION_CONTEXT_COMPACT_KEYS,
    WORKFLOW_EVENT_NOTE_KEYS,
    RuntimePayloadKey,
    RuntimePayloadSummary,
    RuntimeStatus,
    SessionContextKey,
)


def test_runtime_payload_enums_are_string_compatible() -> None:
    payload = {
        "status": "success",
        "audit_id": "sandbox_run_test",
    }

    assert RuntimeStatus.SUCCESS == "success"
    assert payload[RuntimePayloadKey.STATUS] == "success"
    assert RuntimePayloadKey.AUDIT_ID in payload


def test_workflow_event_note_keys_match_compaction_contract() -> None:
    assert WORKFLOW_EVENT_NOTE_KEYS == (
        "command_index",
        "exit_code",
        "message",
        "summary",
        "error",
        "error_type",
        "guardrail",
        "required_next",
        "written_count",
        "candidate_count",
        "relevant_count",
        "expected_count",
        "actual_count",
        "missing_required_outputs",
        "context_state",
        "immediate_goal",
    )


def test_output_gate_key_groups_match_oversized_response_contract() -> None:
    assert OUTPUT_GATE_STORED_RESPONSE_KEYS == (
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
    )
    assert OUTPUT_GATE_TRIGGER_KEYS == (
        "stdout",
        "stderr",
        "content",
        "html",
        "stdout_preview",
        "stderr_preview",
    )


def test_session_context_key_groups_match_context_contract() -> None:
    assert EXTRACTION_CONTEXT_DIGEST_KEYS == (
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
        "expected_output",
    )
    assert SESSION_CONTEXT_COMPACT_KEYS[-3:] == (
        "script_manifest_plan",
        "validation_plan",
        "workflow_reflections",
    )
    assert SessionContextKey.WORKFLOW_REFLECTIONS in SESSION_CONTEXT_COMPACT_KEYS


def test_resource_placeholder_contract_uses_shared_statuses_and_keys() -> None:
    assert RuntimeStatus.RESOURCE_CONTEXT_REMOVED_AFTER_STATE_UPDATE == "resource_context_removed_after_state_update"
    assert RuntimeStatus.RESOURCE_CONTEXT_COMPACTED_KEEP_LATEST_ONLY == "resource_context_compacted_keep_latest_only"
    assert RESOURCE_PLACEHOLDER_KEYS == (
        "skill_name",
        "resource_path",
        "path",
        "file_path",
        "name",
        "title",
        "description",
        "mime_type",
        "error",
        "error_type",
    )
    assert RESOURCE_TEXT_PAYLOAD_KEYS == ("content", "text", "instructions")


def test_runtime_payload_summary_preserves_unknown_fields() -> None:
    summary = RuntimePayloadSummary.model_validate(
        {
            "status": RuntimeStatus.STORED_PREVIEW,
            "audit_id": "sandbox_run_test",
            "artifact_handles": {"trace": {"artifact_name": "trace.jsonl"}},
            "latest_runtime_note": {"summary": "kept as extra payload context"},
        }
    )

    assert summary.status == "stored_preview"
    assert summary.audit_id == "sandbox_run_test"
    assert summary.artifact_handles["trace"]["artifact_name"] == "trace.jsonl"
    assert summary.model_extra == {"latest_runtime_note": {"summary": "kept as extra payload context"}}
