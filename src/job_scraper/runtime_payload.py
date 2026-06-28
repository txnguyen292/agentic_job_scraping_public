from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuntimePayloadKey(StrEnum):
    """Known keys exchanged in ADK runtime/tool-result payloads."""

    ACTUAL_COUNT = "actual_count"
    ARTIFACT = "artifact"
    ARTIFACT_HANDLES = "artifact_handles"
    ARTIFACT_NAME = "artifact_name"
    ARTIFACT_SOURCES = "artifact_sources"
    ARTIFACTS = "artifacts"
    AUDIT_ID = "audit_id"
    BYTES = "bytes"
    CANDIDATE_COUNT = "candidate_count"
    COMMAND_INDEX = "command_index"
    COMPACTED = "compacted"
    CONTENT = "content"
    CONTENT_PREVIEW = "content_preview"
    CONTEXT_STATE = "context_state"
    COUNT = "count"
    DESCRIPTION = "description"
    ERROR = "error"
    ERRORS = "errors"
    ERROR_TYPE = "error_type"
    EVENT_INDEX = "event_index"
    EXPECTED_COUNT = "expected_count"
    EXIT_CODE = "exit_code"
    FILE_PATH = "file_path"
    GUARDRAIL = "guardrail"
    HTML = "html"
    IGNORED_INLINE_ARGS = "ignored_inline_args"
    IMMEDIATE_GOAL = "immediate_goal"
    INSTRUCTIONS = "instructions"
    JOB_COUNT = "job_count"
    LATEST_RUNTIME_NOTE = "latest_runtime_note"
    MESSAGE = "message"
    MIME_TYPE = "mime_type"
    MISSING_FILES = "missing_files"
    MISSING_REQUIRED_OUTPUTS = "missing_required_outputs"
    MODEL = "model"
    NAME = "name"
    NOTE_GROUP = "note_group"
    ORIGINAL_BYTES = "original_bytes"
    ORIGINAL_CHARS = "original_chars"
    OBSERVATION = "observation"
    ORIGINAL_STATUS = "original_status"
    PATH = "path"
    PATHS = "paths"
    PREVIEW = "preview"
    REASON = "reason"
    RELEVANT_COUNT = "relevant_count"
    REQUIRED_FILES = "required_files"
    REQUIRED_NEXT = "required_next"
    RESOURCE_PATH = "resource_path"
    RESOURCE_DISCARDED_FROM_CONTEXT = "resource_discarded_from_context"
    RESPONSE_PREVIEW = "response_preview"
    RETURNED_STDERR_CHARS = "returned_stderr_chars"
    RETURNED_STDOUT_CHARS = "returned_stdout_chars"
    SHA256 = "sha256"
    SKILL_NAME = "skill_name"
    STATUS = "status"
    STDERR = "stderr"
    STDERR_BYTES = "stderr_bytes"
    STDERR_PREVIEW = "stderr_preview"
    STDERR_TRUNCATED = "stderr_truncated"
    STDOUT = "stdout"
    STDOUT_BYTES = "stdout_bytes"
    STDOUT_PREVIEW = "stdout_preview"
    STDOUT_TRUNCATED = "stdout_truncated"
    SUMMARY = "summary"
    TEXT = "text"
    TITLE = "title"
    TOOL_NAME = "tool_name"
    TOOL_RESULTS = "tool_results"
    TOOL_STATUS = "tool_status"
    TOOL_STDERR = "tool_stderr"
    VALUE = "value"
    VALIDATED_COUNT = "validated_count"
    VERSION = "version"
    WRITTEN = "written"
    WRITTEN_COUNT = "written_count"


class RuntimeStatus(StrEnum):
    """Common status values emitted by runtime tools and compaction placeholders."""

    ERROR = "error"
    FINALIZED = "finalized"
    GUARDRAIL_TRIGGERED = "guardrail_triggered"
    RESOURCE_CONTEXT_COMPACTED_KEEP_LATEST_ONLY = "resource_context_compacted_keep_latest_only"
    RESOURCE_CONTEXT_REMOVED_AFTER_STATE_UPDATE = "resource_context_removed_after_state_update"
    RUNNING = "running"
    SANDBOX_CONTEXT_COMPACTED = "sandbox_context_compacted"
    SANDBOX_CONTEXT_REMOVED_AFTER_COMPLETION = "sandbox_context_removed_after_completion"
    SANDBOX_CONTEXT_REMOVED_AFTER_NOTE_REFINEMENT = "sandbox_context_removed_after_note_refinement"
    STORED_PREVIEW = "stored_preview"
    SUCCESS = "success"
    WORKFLOW_EVENT_CONTEXT_REMOVED_AFTER_NOTE_REFINEMENT = "workflow_event_context_removed_after_note_refinement"


class RuntimeErrorType(StrEnum):
    """Runtime error-type codes returned by ADK guardrails and sandbox helpers."""

    EXPECTED_OUTPUT_POLICY = "expected_output_policy"
    EXTRACTION_CONTEXT_UPDATE_POLICY = "extraction_context_update_policy"
    IMMEDIATE_GOAL_POLICY = "immediate_goal_policy"
    MISSING_PROTOCOL_OUTPUT_READ_POLICY = "missing_protocol_output_read_policy"
    PLANNED_NEXT_TOOL_POLICY = "planned_next_tool_policy"
    PRODUCER_OUTPUT_PLAN_POLICY = "producer_output_plan_policy"
    PRODUCER_WRITE_AFTER_SUCCESS_POLICY = "producer_write_after_success_policy"
    REPAIR_SCOPE_POLICY = "repair_scope_policy"
    REPEATED_SANDBOX_READ_POLICY = "repeated_sandbox_read_policy"
    SANDBOX_GUARDRAIL_TERMINAL = "sandbox_guardrail_terminal"
    SANDBOX_SCRIPT_ARGS_POLICY = "sandbox_script_args_policy"
    SANDBOX_SCRIPT_NOT_VERIFIED = "sandbox_script_not_verified"
    WORKFLOW_CONTRACT_POLICY = "workflow_contract_policy"
    WORKFLOW_EXECUTION_POLICY = "workflow_execution_policy"
    WORKFLOW_SANDBOX_NOT_STARTED = "workflow_sandbox_not_started"
    WORKFLOW_SANDBOX_STILL_ACTIVE = "workflow_sandbox_still_active"
    WORKFLOW_SANDBOX_TOOL_BUDGET = "workflow_sandbox_tool_budget"


class GuardrailCode(StrEnum):
    """Guardrail identifiers used by runtime policy payloads."""

    COMPOUND_PRODUCER_VERIFICATION_COMMAND = "compound_producer_verification_command"
    EXPECTED_OUTPUT_COUNT_EXPLANATION_REQUIRED = "expected_output_count_explanation_required"
    EXPECTED_OUTPUT_COUNT_MISMATCH = "expected_output_count_mismatch"
    EXPECTED_OUTPUT_FIELD_AVAILABILITY_REQUIRED = "expected_output_field_availability_required"
    EXPECTED_OUTPUT_FIELD_COVERAGE_MISMATCH = "expected_output_field_coverage_mismatch"
    EXPECTED_OUTPUT_REQUIRED = "expected_output_required"
    IMMEDIATE_GOAL_INCOMPLETE = "immediate_goal_incomplete"
    IMMEDIATE_GOAL_REQUIRED = "immediate_goal_required"
    IMMEDIATE_GOAL_VALIDATION_STRATEGY_REQUIRED = "immediate_goal_validation_strategy_required"
    INITIAL_EXTRACTION_CONTEXT_REQUIRED = "initial_extraction_context_required"
    PRODUCER_OUTPUT_PLAN_REQUIRED = "producer_output_plan_required"
    REPAIR_MISSING_PROTOCOL_OUTPUT_AT_PRODUCER = "repair_missing_protocol_output_at_producer"
    REPEATED_EXTRACTION_CONTEXT_UPDATES = "repeated_extraction_context_updates"
    REPEATED_SANDBOX_TOOL_RESULT = "repeated_sandbox_tool_result"
    SAME_SANDBOX_FILE_READ_DURING_REPAIR = "same_sandbox_file_read_during_repair"
    SANDBOX_EXEC_REQUIRES_CMD_ARGUMENT = "sandbox_exec_requires_cmd_argument"
    SANDBOX_READ_USES_MAX_CHARS = "sandbox_read_uses_max_chars"
    SANDBOX_SCRIPT_REQUIRES_AUDIT_ID = "sandbox_script_requires_audit_id"
    SANDBOX_WRITE_FILE_REQUIRES_PATH_AND_CONTENT = "sandbox_write_file_requires_path_and_content"
    VALIDATE_OR_FINALIZE_AFTER_SUCCESSFUL_PRODUCER_RUN = "validate_or_finalize_after_successful_producer_run"
    WORKFLOW_CONTRACT_REQUIRED = "workflow_contract_required"
    WORKFLOW_REQUIRES_SANDBOX_START = "workflow_requires_sandbox_start"
    WORKFLOW_SANDBOX_MUST_FINISH_BEFORE_RECORD_OR_QUERY = "workflow_sandbox_must_finish_before_record_or_query"
    WORKFLOW_SANDBOX_TOOL_BUDGET_EXCEEDED = "workflow_sandbox_tool_budget_exceeded"
    WRITTEN_SCRIPT_MUST_RUN_BEFORE_FINALIZATION = "written_script_must_run_before_finalization"


class SessionContextKey(StrEnum):
    """Known keys in SESSION_EXTRACTION_CONTEXT."""

    ATTEMPTED_ACTIONS = "attempted_actions"
    AUDIT_ID = "audit_id"
    EXPECTED_OUTPUT = "expected_output"
    EXTRACTION_PLAN = "extraction_plan"
    EXTRACTION_STRATEGY = "extraction_strategy"
    FINAL_GOAL = "final_goal"
    IMMEDIATE_GOAL = "immediate_goal"
    INITIAL_PLAN = "initial_plan"
    KNOWN_ERRORS = "known_errors"
    LAST_RESULT = "last_result"
    OBSERVATIONS = "observations"
    OUTPUT_CONTRACT = "output_contract"
    PAGE_ID = "page_id"
    PLANNED_NEXT_TOOL = "planned_next_tool"
    PRODUCER_OUTPUT_PLAN = "producer_output_plan"
    REPAIR_SCOPE = "repair_scope"
    REQUIRED_OUTPUTS = "required_outputs"
    SCRIPT_MANIFEST_PLAN = "script_manifest_plan"
    STATUS = "status"
    TASK_UNDERSTANDING = "task_understanding"
    VALIDATION_PLAN = "validation_plan"
    WORKFLOW_CONTRACT = "workflow_contract"
    WORKFLOW_REFLECTIONS = "workflow_reflections"


class RuntimePayloadSummary(BaseModel):
    """Permissive model for compacted runtime/tool-result payloads."""

    model_config = ConfigDict(extra="allow", use_enum_values=True)

    status: str = ""
    audit_id: str = ""
    tool_name: str = ""
    skill_name: str = ""
    file_path: str = ""
    command_index: int | str | None = None
    exit_code: int | str | None = None
    error_type: str = ""
    guardrail: str = ""
    error: str = ""
    message: str = ""
    original_status: str = ""
    reason: str = ""
    required_next: str = ""
    paths: list[str] = Field(default_factory=list)
    artifact_handles: dict[str, Any] = Field(default_factory=dict)
    resource_discarded_from_context: bool = False
    compacted: bool = False


def payload_key_values(*keys: RuntimePayloadKey) -> tuple[str, ...]:
    return tuple(key.value for key in keys)


def session_context_key_values(*keys: SessionContextKey) -> tuple[str, ...]:
    return tuple(key.value for key in keys)


SANDBOX_LIKE_OUTPUT_KEYS = payload_key_values(
    RuntimePayloadKey.STDOUT,
    RuntimePayloadKey.STDERR,
    RuntimePayloadKey.STDOUT_PREVIEW,
    RuntimePayloadKey.STDERR_PREVIEW,
)

TEXT_PREVIEW_PAYLOAD_KEYS = payload_key_values(
    RuntimePayloadKey.STDOUT,
    RuntimePayloadKey.STDOUT_PREVIEW,
    RuntimePayloadKey.STDERR,
    RuntimePayloadKey.STDERR_PREVIEW,
    RuntimePayloadKey.CONTENT,
    RuntimePayloadKey.INSTRUCTIONS,
)

SANDBOX_TEXT_PREVIEW_KEYS = payload_key_values(
    RuntimePayloadKey.STDOUT,
    RuntimePayloadKey.STDOUT_PREVIEW,
    RuntimePayloadKey.STDERR,
    RuntimePayloadKey.STDERR_PREVIEW,
)

WORKFLOW_EVENT_NOTE_KEYS = payload_key_values(
    RuntimePayloadKey.COMMAND_INDEX,
    RuntimePayloadKey.EXIT_CODE,
    RuntimePayloadKey.MESSAGE,
    RuntimePayloadKey.SUMMARY,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.ERROR_TYPE,
    RuntimePayloadKey.GUARDRAIL,
    RuntimePayloadKey.REQUIRED_NEXT,
    RuntimePayloadKey.WRITTEN_COUNT,
    RuntimePayloadKey.CANDIDATE_COUNT,
    RuntimePayloadKey.RELEVANT_COUNT,
    RuntimePayloadKey.EXPECTED_COUNT,
    RuntimePayloadKey.ACTUAL_COUNT,
    RuntimePayloadKey.MISSING_REQUIRED_OUTPUTS,
    RuntimePayloadKey.CONTEXT_STATE,
    RuntimePayloadKey.IMMEDIATE_GOAL,
)

SANDBOX_PLACEHOLDER_KEYS = payload_key_values(
    RuntimePayloadKey.SKILL_NAME,
    RuntimePayloadKey.FILE_PATH,
    RuntimePayloadKey.COMMAND_INDEX,
    RuntimePayloadKey.EXIT_CODE,
    RuntimePayloadKey.GUARDRAIL,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.ERROR_TYPE,
)

SANDBOX_COMMAND_SUMMARY_KEYS = payload_key_values(
    RuntimePayloadKey.COMMAND_INDEX,
    RuntimePayloadKey.EXIT_CODE,
    RuntimePayloadKey.GUARDRAIL,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.ERROR_TYPE,
)

SANDBOX_COMMAND_NOTE_KEYS = payload_key_values(
    RuntimePayloadKey.COMMAND_INDEX,
    RuntimePayloadKey.EXIT_CODE,
    RuntimePayloadKey.MESSAGE,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.ERROR_TYPE,
    RuntimePayloadKey.GUARDRAIL,
    RuntimePayloadKey.STDOUT_TRUNCATED,
    RuntimePayloadKey.STDERR_TRUNCATED,
    RuntimePayloadKey.STDOUT_BYTES,
    RuntimePayloadKey.STDERR_BYTES,
)

COMPACT_SANDBOX_RESPONSE_DIRECT_KEYS = payload_key_values(
    RuntimePayloadKey.EXIT_CODE,
    RuntimePayloadKey.COMMAND_INDEX,
)

COMPACT_SANDBOX_RESPONSE_METADATA_KEYS = payload_key_values(
    RuntimePayloadKey.STDOUT_TRUNCATED,
    RuntimePayloadKey.STDERR_TRUNCATED,
    RuntimePayloadKey.RETURNED_STDOUT_CHARS,
    RuntimePayloadKey.RETURNED_STDERR_CHARS,
    RuntimePayloadKey.STDOUT_BYTES,
    RuntimePayloadKey.STDERR_BYTES,
    RuntimePayloadKey.MESSAGE,
    RuntimePayloadKey.OBSERVATION,
    RuntimePayloadKey.ERROR_TYPE,
    RuntimePayloadKey.PATH,
    RuntimePayloadKey.MODEL,
    RuntimePayloadKey.WRITTEN,
    RuntimePayloadKey.ERRORS,
    RuntimePayloadKey.GUARDRAIL,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.EXPECTED_COUNT,
    RuntimePayloadKey.ACTUAL_COUNT,
    RuntimePayloadKey.MISSING_REQUIRED_OUTPUTS,
)

OUTPUT_GATE_STORED_RESPONSE_KEYS = payload_key_values(
    RuntimePayloadKey.MESSAGE,
    RuntimePayloadKey.OBSERVATION,
    RuntimePayloadKey.ERROR_TYPE,
    RuntimePayloadKey.PATH,
    RuntimePayloadKey.MODEL,
    RuntimePayloadKey.WRITTEN,
    RuntimePayloadKey.ERRORS,
    RuntimePayloadKey.ARTIFACTS,
    RuntimePayloadKey.STDOUT_BYTES,
    RuntimePayloadKey.STDERR_BYTES,
    RuntimePayloadKey.STDOUT_TRUNCATED,
    RuntimePayloadKey.STDERR_TRUNCATED,
    RuntimePayloadKey.RETURNED_STDOUT_CHARS,
    RuntimePayloadKey.RETURNED_STDERR_CHARS,
    RuntimePayloadKey.EXIT_CODE,
    RuntimePayloadKey.COMMAND_INDEX,
    RuntimePayloadKey.GUARDRAIL,
    RuntimePayloadKey.ERROR,
)

OUTPUT_GATE_TRIGGER_KEYS = payload_key_values(
    RuntimePayloadKey.STDOUT,
    RuntimePayloadKey.STDERR,
    RuntimePayloadKey.CONTENT,
    RuntimePayloadKey.HTML,
    RuntimePayloadKey.STDOUT_PREVIEW,
    RuntimePayloadKey.STDERR_PREVIEW,
)

RESOURCE_PLACEHOLDER_KEYS = payload_key_values(
    RuntimePayloadKey.SKILL_NAME,
    RuntimePayloadKey.RESOURCE_PATH,
    RuntimePayloadKey.PATH,
    RuntimePayloadKey.FILE_PATH,
    RuntimePayloadKey.NAME,
    RuntimePayloadKey.TITLE,
    RuntimePayloadKey.DESCRIPTION,
    RuntimePayloadKey.MIME_TYPE,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.ERROR_TYPE,
)

RESOURCE_TEXT_PAYLOAD_KEYS = payload_key_values(
    RuntimePayloadKey.CONTENT,
    RuntimePayloadKey.TEXT,
    RuntimePayloadKey.INSTRUCTIONS,
)

LATEST_PAYLOAD_KEYS = payload_key_values(
    RuntimePayloadKey.STATUS,
    RuntimePayloadKey.ERROR_TYPE,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.MESSAGE,
    RuntimePayloadKey.REQUIRED_NEXT,
    RuntimePayloadKey.AUDIT_ID,
    RuntimePayloadKey.SKILL_NAME,
    RuntimePayloadKey.FILE_PATH,
    RuntimePayloadKey.EXIT_CODE,
    RuntimePayloadKey.COMMAND_INDEX,
    RuntimePayloadKey.VALIDATED_COUNT,
    RuntimePayloadKey.WRITTEN_COUNT,
    RuntimePayloadKey.JOB_COUNT,
    RuntimePayloadKey.EXPECTED_COUNT,
    RuntimePayloadKey.ACTUAL_COUNT,
    RuntimePayloadKey.MISSING_REQUIRED_OUTPUTS,
    RuntimePayloadKey.STDOUT_TRUNCATED,
    RuntimePayloadKey.STDERR_TRUNCATED,
)

PROMOTED_SCRIPT_PAYLOAD_ERROR_KEYS = payload_key_values(
    RuntimePayloadKey.STATUS,
    RuntimePayloadKey.AUDIT_ID,
    RuntimePayloadKey.ERROR,
    RuntimePayloadKey.ERROR_TYPE,
    RuntimePayloadKey.GUARDRAIL,
    RuntimePayloadKey.MISSING_FILES,
    RuntimePayloadKey.REQUIRED_FILES,
    RuntimePayloadKey.IGNORED_INLINE_ARGS,
    RuntimePayloadKey.COUNT,
    RuntimePayloadKey.WRITTEN_COUNT,
)

EXTRACTION_CONTEXT_DIGEST_KEYS = session_context_key_values(
    SessionContextKey.AUDIT_ID,
    SessionContextKey.PAGE_ID,
    SessionContextKey.STATUS,
    SessionContextKey.TASK_UNDERSTANDING,
    SessionContextKey.FINAL_GOAL,
    SessionContextKey.INITIAL_PLAN,
    SessionContextKey.OBSERVATIONS,
    SessionContextKey.EXTRACTION_PLAN,
    SessionContextKey.LAST_RESULT,
    SessionContextKey.KNOWN_ERRORS,
    SessionContextKey.ATTEMPTED_ACTIONS,
    SessionContextKey.IMMEDIATE_GOAL,
    SessionContextKey.REQUIRED_OUTPUTS,
    SessionContextKey.WORKFLOW_CONTRACT,
    SessionContextKey.EXPECTED_OUTPUT,
)

SESSION_CONTEXT_COMPACT_KEYS = session_context_key_values(
    SessionContextKey.AUDIT_ID,
    SessionContextKey.PAGE_ID,
    SessionContextKey.STATUS,
    SessionContextKey.TASK_UNDERSTANDING,
    SessionContextKey.FINAL_GOAL,
    SessionContextKey.INITIAL_PLAN,
    SessionContextKey.OBSERVATIONS,
    SessionContextKey.EXTRACTION_PLAN,
    SessionContextKey.EXTRACTION_STRATEGY,
    SessionContextKey.LAST_RESULT,
    SessionContextKey.KNOWN_ERRORS,
    SessionContextKey.ATTEMPTED_ACTIONS,
    SessionContextKey.IMMEDIATE_GOAL,
    SessionContextKey.PLANNED_NEXT_TOOL,
    SessionContextKey.REPAIR_SCOPE,
    SessionContextKey.REQUIRED_OUTPUTS,
    SessionContextKey.WORKFLOW_CONTRACT,
    SessionContextKey.EXPECTED_OUTPUT,
    SessionContextKey.OUTPUT_CONTRACT,
    SessionContextKey.PRODUCER_OUTPUT_PLAN,
    SessionContextKey.SCRIPT_MANIFEST_PLAN,
    SessionContextKey.VALIDATION_PLAN,
    SessionContextKey.WORKFLOW_REFLECTIONS,
)
