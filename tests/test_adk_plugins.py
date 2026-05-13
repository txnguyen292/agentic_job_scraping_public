from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from google.genai import types as genai_types

from google.adk.models.llm_response import LlmResponse

from job_scraper.adk_plugins import ACTIVE_SANDBOX_STATE_KEY
from job_scraper.adk_plugins import EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY
from job_scraper.adk_plugins import FINALIZED_SANDBOX_PROMOTION_STATE_KEY
from job_scraper.adk_plugins import IMMEDIATE_ERROR_REPEAT_STATE_KEY
from job_scraper.adk_plugins import LAST_PAGE_WORKSPACE_STATE_KEY
from job_scraper.adk_plugins import SANDBOX_PENDING_SCRIPT_STATE_KEY
from job_scraper.adk_plugins import SANDBOX_ARTIFACT_HANDLES_STATE_KEY
from job_scraper.adk_plugins import SANDBOX_TOOL_BUDGET_STATE_KEY
from job_scraper.adk_plugins import (
    SANDBOX_NOTES_STATE_KEY,
    SandboxNoteRefinementPlugin,
    SandboxOutputGatePlugin,
    SandboxWorkflowGuardPlugin,
    TransientModelRetryPlugin,
)
from job_scraper.runtime_state import SESSION_EXTRACTION_CONTEXT_STATE_KEY
from job_scraper.tool_policy import ToolActionKind, resolve_tool_policy


class FakeToolContext:
    def __init__(self) -> None:
        self.saved: dict[str, tuple[genai_types.Part, dict[str, object] | None]] = {}
        self.state: dict[str, object] = {}

    async def save_artifact(
        self,
        filename: str,
        artifact: genai_types.Part,
        custom_metadata: dict[str, object] | None = None,
    ) -> int:
        self.saved[filename] = (artifact, custom_metadata)
        return 0


class FakeAdkState:
    """ADK-like delta-aware state that intentionally is not a MutableMapping."""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.delta: dict[str, object] = {}

    def __contains__(self, key: str) -> bool:
        return key in self.data or key in self.delta

    def __getitem__(self, key: str) -> object:
        if key in self.delta:
            return self.delta[key]
        return self.data[key]

    def __setitem__(self, key: str, value: object) -> None:
        self.data[key] = value
        self.delta[key] = value

    def get(self, key: str, default: object = None) -> object:
        if key not in self:
            return default
        return self[key]

    def setdefault(self, key: str, default: object = None) -> object:
        if key not in self:
            self[key] = default
        return self[key]


def workflow_contract_state() -> dict[str, object]:
    required_outputs = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    return {
        "updated": True,
        "required_outputs": required_outputs,
        "workflow_contract": {
            "agent_role": "agent chooses and owns the extraction method",
            "script_role": "supporting scripts may inspect, parse, extract, validate, and serialize when recorded",
            "required_outputs": required_outputs,
            "success_gate": "validate and finalize before persistence",
            "repair_rule": "repair the failing layer: observations, evidence, method, supporting script, output, or proposal artifact",
        },
        "output_contract": {
            "contract_version": "sandbox-page-analyst-protocol-v1",
            "extraction_run_json": {
                "required": ["observations", "chosen_strategy", "expected_output"],
                "expected_output": {
                    "required": [
                        "expected_job_count",
                        "count_basis",
                        "count_rationale",
                        "available_fields",
                        "field_basis",
                    ]
                },
            },
            "script_manifest_json": {"scripts_entry_requires_one_of": ["workflow_version", "reference_version"]},
        },
        "producer_output_plan": {
            "required_outputs": required_outputs,
            "extraction_run": {"required": ["observations", "chosen_strategy", "expected_output"]},
            "candidates_json": {"required_top_level": ["source", "jobs", "selectors", "crawl", "warnings"]},
            "final_json": {"required_top_level": ["status", "output_schema", "summary", "result"]},
            "field_availability": {"required": ["available_fields", "field_basis"]},
            "script_manifest": {"required_if_supporting_scripts_authored": True},
            "validation_plan": ["validate_outputs.py", "sandbox_finalize.py"],
        },
        "evidence_contract": {
            "requires_loaded_evidence_refs": True,
            "requires_field_rationale": True,
        },
    }


def workflow_output_plan_state(required_outputs: list[str]) -> dict[str, object]:
    return {
        "output_contract": {
            "contract_version": "sandbox-page-analyst-protocol-v1",
            "extraction_run_json": {
                "required": ["observations", "chosen_strategy", "expected_output"],
                "expected_output": {
                    "required": [
                        "expected_job_count",
                        "count_basis",
                        "count_rationale",
                        "available_fields",
                        "field_basis",
                    ]
                },
            },
            "script_manifest_json": {"scripts_entry_requires_one_of": ["workflow_version", "reference_version"]},
        },
        "producer_output_plan": {
            "required_outputs": required_outputs,
            "extraction_run": {"required": ["observations", "chosen_strategy", "expected_output"]},
            "candidates_json": {"required_top_level": ["source", "jobs", "selectors", "crawl", "warnings"]},
            "final_json": {"required_top_level": ["status", "output_schema", "summary", "result"]},
            "field_availability": {"required": ["available_fields", "field_basis"]},
            "script_manifest": {"required_if_supporting_scripts_authored": True},
            "validation_plan": ["validate_outputs.py", "sandbox_finalize.py"],
        },
    }


def protocol_producer_source() -> str:
    return "\n".join(
        f"Path('{path}').write_text('{{}}')"
        for path in workflow_contract_state()["required_outputs"]  # type: ignore[index]
    )


class FakeRetryModel:
    def __init__(self, items: list[object]) -> None:
        self.items = items
        self.calls = 0
        self.stream_values: list[bool] = []

    async def generate_content_async(self, llm_request: object, stream: bool = False):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.stream_values.append(stream)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        yield item


class FakeInvocationContext:
    def __init__(self, model: FakeRetryModel) -> None:
        self.agent = SimpleNamespace(model=model)
        self.run_config = SimpleNamespace(streaming_mode=SimpleNamespace(name="NONE"))
        self.increment_count = 0

    def increment_llm_call_count(self) -> None:
        self.increment_count += 1


async def _noop_sleep(delay: float) -> None:
    return None


def _model_response(text: str) -> LlmResponse:
    return LlmResponse(content=genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=text)]))


def test_transient_model_retry_plugin_retries_rate_limit_and_returns_response() -> None:
    response = _model_response("retry ok")
    model = FakeRetryModel([response])
    invocation_context = FakeInvocationContext(model)
    plugin = TransientModelRetryPlugin(max_attempts=2, base_delay_seconds=0.01, sleep=_noop_sleep)

    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=SimpleNamespace(_invocation_context=invocation_context),
            llm_request=SimpleNamespace(),
            error=RuntimeError("Rate limit reached for tokens per min. Please try again in 0.01s."),
        )
    )

    assert result is response
    assert model.calls == 1
    assert invocation_context.increment_count == 1


def test_transient_model_retry_plugin_ignores_non_transient_errors() -> None:
    model = FakeRetryModel([_model_response("unused")])
    plugin = TransientModelRetryPlugin(max_attempts=2, sleep=_noop_sleep)

    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=SimpleNamespace(_invocation_context=FakeInvocationContext(model)),
            llm_request=SimpleNamespace(),
            error=ValueError("schema validation failed"),
        )
    )

    assert result is None
    assert model.calls == 0


def test_transient_model_retry_plugin_returns_clean_error_after_exhaustion() -> None:
    model = FakeRetryModel([RuntimeError("429 too many requests")])
    plugin = TransientModelRetryPlugin(max_attempts=2, base_delay_seconds=0, sleep=_noop_sleep)

    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=SimpleNamespace(_invocation_context=FakeInvocationContext(model)),
            llm_request=SimpleNamespace(),
            error=RuntimeError("503 service unavailable"),
        )
    )

    assert result is not None
    assert result.error_code == "MODEL_RETRY_EXHAUSTED"
    assert "retry_attempts=1" in str(result.error_message)
    assert model.calls == 1


def test_transient_model_retry_plugin_defaults_to_five_retries_after_original_failure() -> None:
    plugin = TransientModelRetryPlugin(sleep=_noop_sleep)

    assert plugin.max_attempts == 6


def test_tool_policy_resolves_notebook_and_reference_actions() -> None:
    notebook_policy = resolve_tool_policy("update_extraction_context")
    reference_policy = resolve_tool_policy("load_skill_resource")

    assert notebook_policy.kind == ToolActionKind.NOTEBOOK
    assert notebook_policy.counts_as_intervening_action is False
    assert reference_policy.kind == ToolActionKind.REFERENCE_READ
    assert reference_policy.counts_as_intervening_action is True


def test_tool_policy_resolves_run_skill_script_by_script_path() -> None:
    write_policy = resolve_tool_policy(
        "run_skill_script",
        {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_write_file.py"},
    )
    patch_policy = resolve_tool_policy(
        "run_skill_script",
        {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_apply_patch.py"},
    )
    exec_policy = resolve_tool_policy(
        "run_skill_script",
        {"skill_name": "sandbox-page-analyst", "file_path": "./scripts/sandbox_exec.py"},
    )
    litellm_policy = resolve_tool_policy(
        "run_skill_script",
        {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_litellm_call.py"},
    )
    finalize_policy = resolve_tool_policy(
        "run_skill_script",
        {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_finalize.py"},
    )

    assert write_policy.kind == ToolActionKind.SANDBOX_WRITE
    assert write_policy.changes_workflow_output is True
    assert patch_policy.kind == ToolActionKind.SANDBOX_WRITE
    assert patch_policy.changes_workflow_output is True
    assert exec_policy.kind == ToolActionKind.SANDBOX_EXEC
    assert exec_policy.counts_as_intervening_action is True
    assert litellm_policy.kind == ToolActionKind.WORKFLOW_ACTION
    assert litellm_policy.counts_as_intervening_action is True
    assert litellm_policy.changes_workflow_output is False
    assert finalize_policy.kind == ToolActionKind.SANDBOX_FINALIZE
    assert finalize_policy.terminal is True


def test_output_gate_leaves_sandbox_tool_result_unchanged_without_artifacts() -> None:
    plugin = SandboxOutputGatePlugin(direct_max_chars=80, preview_max_chars=20)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    result = {
        "stdout": "0123456789" * 20,
        "status": "success",
        "audit_id": "sandbox_run_test",
        "message": "stdout exceeded the direct return limit.",
        "paths": {"stdout_path": "commands/001.stdout.txt"},
        "stdout_truncated": True,
    }

    gated = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"},
            tool_context=tool_context,
            result=result,
        )
    )

    assert gated is None


def test_output_gate_puts_stdout_preview_before_artifact_metadata(tmp_path: Path) -> None:
    source = tmp_path / "stdout.txt"
    source.write_text("0123456789" * 20, encoding="utf-8")
    plugin = SandboxOutputGatePlugin(direct_max_chars=80, preview_max_chars=40)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    result = {
        "status": "success",
        "audit_id": "sandbox_run_test",
        "stdout": "0123456789" * 20,
        "stdout_truncated": True,
        "message": "stdout exceeded the direct return limit.",
        "paths": {"stdout_path": "commands/001.stdout.txt"},
        "artifact_sources": [
            {
                "key": "stdout",
                "source_path": str(source),
                "artifact_name": "sandbox_run_test/commands/001.stdout.txt",
                "mime_type": "text/plain",
            }
        ],
    }

    gated = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"},
            tool_context=tool_context,
            result=result,
        )
    )

    assert gated is not None
    serialized = json.dumps(gated, ensure_ascii=True)
    stdout_index = serialized.index('"stdout":')
    assert stdout_index < serialized.index('"paths":')
    assert stdout_index < serialized.index('"artifact_handles":')
    assert gated["stdout"] == result["stdout"]
    assert "stdout_preview" not in gated


def test_output_gate_leaves_small_result_unchanged() -> None:
    plugin = SandboxOutputGatePlugin(direct_max_chars=80, preview_max_chars=20)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="query_jobs")
    result = {"status": "success", "count": 0}

    gated = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={},
            tool_context=tool_context,
            result=result,
        )
    )

    assert gated is None
    assert tool_context.saved == {}


def test_output_gate_promotes_artifact_sources_to_adk_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "trace.jsonl"
    source.write_text('{"event":"command"}\n', encoding="utf-8")
    plugin = SandboxOutputGatePlugin(direct_max_chars=8_000, preview_max_chars=20)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    result = {
        "status": "success",
        "audit_id": "sandbox_run_test",
        "artifact_sources": [
            {
                "key": "trace",
                "source_path": str(source),
                "artifact_name": "sandbox_run_test/trace.jsonl",
                "mime_type": "application/jsonl",
            }
        ],
    }

    gated = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"},
            tool_context=tool_context,
            result=result,
        )
    )

    assert gated is not None
    assert "artifact_sources" not in gated
    assert gated["artifact_handles"]["trace"]["artifact_name"] == "sandbox_run_test__trace.jsonl"
    assert gated["artifact_handles"]["trace"]["mime_type"] == "application/jsonl"
    assert "artifacts" not in gated
    assert "sandbox_run_test__trace.jsonl" in tool_context.saved


def test_output_gate_promotes_nested_stdout_artifact_sources_to_adk_artifacts(tmp_path: Path) -> None:
    final_output = tmp_path / "output" / "final.json"
    final_output.parent.mkdir()
    final_output.write_text('{"status":"success","result":{"jobs":[]}}\n', encoding="utf-8")
    plugin = SandboxOutputGatePlugin(direct_max_chars=8_000, preview_max_chars=20)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    result = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_finalize.py",
        "status": "success",
        "stdout": json.dumps(
            {
                "status": "success",
                "audit_id": "sandbox_run_test",
                "artifact_sources": [
                    {
                        "key": "output_final",
                        "source_path": str(final_output),
                        "artifact_name": "sandbox_run_test/output/final.json",
                        "mime_type": "application/json",
                    }
                ],
            }
        ),
        "stderr": "",
    }

    gated = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_finalize.py"},
            tool_context=tool_context,
            result=result,
        )
    )

    assert gated is not None
    assert gated["artifact_handles"]["output_final"]["artifact_name"] == "sandbox_run_test__output__final.json"
    assert gated["artifact_handles"]["output_final"]["mime_type"] == "application/json"
    assert "sandbox_run_test__output__final.json" in tool_context.saved
    assert tool_context.state[SANDBOX_ARTIFACT_HANDLES_STATE_KEY]["sandbox_run_test"]["output_final"] == {
        "artifact_name": "sandbox_run_test__output__final.json",
        "version": 0,
        "mime_type": "application/json",
        "bytes": len(final_output.read_bytes()),
        "sha256": "726d9456d2be8feb6ade0f4010d436e6923a8547d8e1c9e846813cfbd0a22f88",
    }


def test_workflow_guard_adds_versioned_artifact_handles_to_promotion_result() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SANDBOX_ARTIFACT_HANDLES_STATE_KEY] = {
        "sandbox_run_test": {
            "output_final": {
                "artifact_name": "sandbox_run_test__output__final.json",
                "version": 2,
                "mime_type": "application/json",
                "bytes": 123,
                "sha256": "abc",
            }
        }
    }
    tool_context.state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "finalized",
        "promotion_status": "pending",
        "query_status": "pending",
        "written_count": 0,
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="promote_sandbox_extraction"),
            tool_args={"audit_id": "sandbox_run_test"},
            tool_context=tool_context,
            result={"status": "success", "audit_id": "sandbox_run_test", "written_count": 20, "validated_count": 20},
        )
    )

    assert updated is not None
    assert updated["adk_artifact_handles"]["output_final"]["version"] == 2
    assert "workspace paths are not versioned" in updated["artifact_version_policy"]
    pending = tool_context.state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY]
    assert pending["artifact_handles"]["output_final"]["artifact_name"] == "sandbox_run_test__output__final.json"


def test_output_gate_leaves_sandbox_script_stdout_available_to_model() -> None:
    plugin = SandboxOutputGatePlugin(direct_max_chars=8_000, preview_max_chars=80)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    result = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_exec.py",
        "status": "success",
        "stdout": (
            '{"status":"error","audit_id":"sandbox_run_test","command_index":7,'
            '"exit_code":1,"stderr":"{\\"valid\\": false, \\"error\\": \\"job 0 missing title\\"}\\n",'
            '"paths":{"stdout_path":"commands/007.stdout.txt","stderr_path":"commands/007.stderr.txt"}}'
        ),
        "stderr": "",
    }

    gated = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"},
            tool_context=tool_context,
            result=result,
        )
    )

    assert gated is None


def test_output_gate_preserves_protocol_validation_error_facts() -> None:
    plugin = SandboxOutputGatePlugin(direct_max_chars=8_000, preview_max_chars=80)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    result = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_write_file.py",
        "status": "success",
        "stdout": json.dumps(
            {
                "status": "error",
                "audit_id": "sandbox_run_test",
                "error_type": "protocol_model_validation",
                "path": "output/final.json",
                "model": "FinalOutput",
                "written": False,
                "errors": [{"loc": ["result"], "msg": "Field required", "type": "missing"}],
            }
        ),
        "stderr": "",
    }

    gated = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_write_file.py"},
            tool_context=tool_context,
            result=result,
        )
    )

    assert gated is None


def test_note_refinement_plugin_summarizes_previous_batch_and_keeps_newest_command_full() -> None:
    calls: list[tuple[str, list[object], list[dict[str, object]]]] = []

    def summarizer(audit_id: str, notes: list[object], commands: list[dict[str, object]]) -> str:
        calls.append((audit_id, notes, commands))
        return "observations: saw job-card markers; extraction_plan: use card selector; comparison: pending"

    plugin = SandboxNoteRefinementPlugin(command_interval=2, summarizer=summarizer)
    tool_context = FakeToolContext()
    tool_context.state[SANDBOX_NOTES_STATE_KEY] = [
        {"audit_id": "sandbox_run_test", "summary": "observations: initial page is large"}
    ]
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"}

    first = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": json.dumps(
                    {
                        "status": "success",
                        "audit_id": "sandbox_run_test",
                        "command_index": 1,
                        "stdout": "job-card count: 20",
                    }
                ),
            },
        )
    )
    second = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": json.dumps(
                    {
                        "status": "success",
                        "audit_id": "sandbox_run_test",
                        "command_index": 2,
                        "stdout": "extractor wrote 0 jobs",
                    }
                ),
            },
        )
    )
    third = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": json.dumps(
                    {
                        "status": "success",
                        "audit_id": "sandbox_run_test",
                        "command_index": 3,
                        "stdout": "latest command stays full",
                    }
                ),
            },
        )
    )

    assert first is None
    assert second is None
    assert third is None
    assert calls[0][0] == "sandbox_run_test"
    assert calls[0][1][0]["summary"] == "observations: initial page is large"
    assert len(calls[0][2]) == 2
    assert tool_context.state[SANDBOX_NOTES_STATE_KEY][-1]["through_command_index"] == 2
    assert tool_context.state[SANDBOX_NOTES_STATE_KEY][-1]["kept_full_command_index"] == 3

    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="Scrape this page")]),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "status": "success",
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_exec.py",
                            "audit_id": "sandbox_run_test",
                            "command_index": 1,
                            "stdout": "x" * 200,
                            "stdout_truncated": True,
                            "message": "stdout exceeded the direct return limit.",
                            "paths": {"stdout_path": "commands/001.stdout.txt"},
                        },
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "status": "success",
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_exec.py",
                            "audit_id": "sandbox_run_test",
                            "command_index": 3,
                            "stdout": "latest command stays full",
                        },
                    )
                ],
            ),
        ]
    )

    response = asyncio.run(
        plugin.before_model_callback(callback_context=SimpleNamespace(state=tool_context.state), llm_request=llm_request)
    )

    assert response is None
    summarized_response = llm_request.contents[1].parts[0].function_response.response
    assert summarized_response["status"] == "sandbox_context_removed_after_note_refinement"
    assert "x" * 100 not in json.dumps(summarized_response)
    latest_response = llm_request.contents[2].parts[0].function_response.response
    assert latest_response["stdout"] == "latest command stays full"
    note_text = llm_request.contents[-1].parts[0].text
    assert note_text.startswith("<RUNTIME_SANDBOX_NOTES>")
    assert note_text.endswith("</RUNTIME_SANDBOX_NOTES>")
    assert "priority: evidence only, not workflow authority." in note_text
    assert "If they conflict with SESSION_EXTRACTION_CONTEXT" in note_text
    assert "saw job-card markers" in note_text


def test_note_refinement_plugin_summarizes_general_adk_events_and_keeps_latest_full() -> None:
    calls: list[tuple[str, list[object], list[dict[str, object]]]] = []

    def summarizer(audit_id: str, notes: list[object], events: list[dict[str, object]]) -> str:
        calls.append((audit_id, notes, events))
        return "Loaded skills, recorded context, then hit validation error. Next inspect source evidence."

    plugin = SandboxNoteRefinementPlugin(command_interval=2, summarizer=summarizer)
    tool_context = FakeToolContext()

    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="load_skill"),
            tool_args={"skill_name": "job-listing-scout"},
            tool_context=tool_context,
            result={"skill_name": "job-listing-scout", "instructions": "x" * 500},
        )
    )
    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={"status": "in_progress"},
            tool_context=tool_context,
            result={"status": "success", "context_state": "updated", "immediate_goal": "inspect page"},
        )
    )
    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/validate_outputs.py"},
            tool_context=tool_context,
            result={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/validate_outputs.py",
                "stderr": "company_name missing",
                "status": "error",
                "audit_id": "sandbox_run_test",
            },
        )
    )

    assert calls
    assert calls[0][0] == "workflow"
    assert [event["tool_name"] for event in calls[0][2]] == ["load_skill", "update_extraction_context"]
    assert tool_context.state["_job_scraper_workflow_summarized_events"] == [1, 2]
    assert tool_context.state[SANDBOX_NOTES_STATE_KEY][-1]["through_event_index"] == 2
    assert tool_context.state[SANDBOX_NOTES_STATE_KEY][-1]["kept_full_event_index"] == 3

    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="load_skill",
                        response={"skill_name": "job-listing-scout", "instructions": "x" * 500},
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="update_extraction_context",
                        response={"status": "success", "context_state": "updated", "immediate_goal": "inspect page"},
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/validate_outputs.py",
                            "stderr": "company_name missing",
                            "status": "error",
                            "audit_id": "sandbox_run_test",
                        },
                    )
                ],
            ),
        ]
    )

    asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=tool_context.state), llm_request=llm_request))

    first_response = llm_request.contents[0].parts[0].function_response.response
    second_response = llm_request.contents[1].parts[0].function_response.response
    latest_response = llm_request.contents[2].parts[0].function_response.response
    assert first_response["status"] == "workflow_event_context_removed_after_note_refinement"
    assert second_response["status"] == "workflow_event_context_removed_after_note_refinement"
    assert "x" * 100 not in json.dumps(first_response)
    assert latest_response["stderr"] == "company_name missing"
    assert latest_response["status"] == "error"
    note_text = llm_request.contents[-1].parts[0].text
    assert note_text.startswith("<RUNTIME_SANDBOX_NOTES>")
    assert "under 200 words" not in note_text
    assert "Loaded skills" in note_text


def test_note_refinement_prompt_requests_under_200_words() -> None:
    import inspect

    source = inspect.getsource(SandboxNoteRefinementPlugin._summarize)

    assert "under 200 words" in source

def test_note_refinement_plugin_sorts_parallel_command_callbacks_by_command_index() -> None:
    calls: list[tuple[str, list[object], list[dict[str, object]]]] = []

    def summarizer(audit_id: str, notes: list[object], commands: list[dict[str, object]]) -> str:
        calls.append((audit_id, notes, commands))
        return "parallel callbacks summarized in command-index order"

    plugin = SandboxNoteRefinementPlugin(command_interval=5, summarizer=summarizer)
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"}

    for index in (2, 3, 1, 4, 6, 5):
        asyncio.run(
            plugin.after_tool_callback(
                tool=tool,
                tool_args=tool_args,
                tool_context=tool_context,
                result={
                    "status": "success",
                    "stdout": json.dumps(
                        {
                            "status": "success",
                            "audit_id": "sandbox_run_test",
                            "command_index": index,
                            "stdout": f"command {index}",
                        }
                    ),
                },
            )
        )

    summarized_indexes = [command["command_index"] for command in calls[0][2]]
    assert summarized_indexes == [1, 2, 3, 4, 5]
    assert tool_context.state[SANDBOX_NOTES_STATE_KEY][-1]["through_command_index"] == 5
    assert tool_context.state[SANDBOX_NOTES_STATE_KEY][-1]["kept_full_command_index"] == 6
    assert tool_context.state["_job_scraper_sandbox_summarized_commands"]["sandbox_run_test"] == [1, 2, 3, 4, 5]


def test_note_refinement_plugin_accepts_non_dict_adk_state_mapping() -> None:
    calls: list[tuple[str, list[object], list[dict[str, object]]]] = []

    def summarizer(audit_id: str, notes: list[object], commands: list[dict[str, object]]) -> str:
        calls.append((audit_id, notes, commands))
        return "observations: fused from non-dict state; extraction_plan: keep latest full"

    plugin = SandboxNoteRefinementPlugin(command_interval=2, summarizer=summarizer)
    tool_context = FakeToolContext()
    tool_context.state = FakeAdkState()
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"}

    for index in (1, 2, 3):
        asyncio.run(
            plugin.after_tool_callback(
                tool=tool,
                tool_args=tool_args,
                tool_context=tool_context,
                result={
                    "status": "success",
                    "stdout": json.dumps(
                        {
                            "status": "success",
                            "audit_id": "sandbox_run_test",
                            "command_index": index,
                            "stdout": f"command {index}",
                        }
                    ),
                },
            )
        )

    assert calls
    assert tool_context.state[SANDBOX_NOTES_STATE_KEY][-1]["through_command_index"] == 2

    llm_request = SimpleNamespace(contents=[])
    asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=tool_context.state), llm_request=llm_request))

    note_text = llm_request.contents[-1].parts[0].text
    assert note_text.startswith("<RUNTIME_SANDBOX_NOTES>")
    assert note_text.endswith("</RUNTIME_SANDBOX_NOTES>")


def test_note_refinement_plugin_prunes_completed_sandbox_context_after_persistence() -> None:
    plugin = SandboxNoteRefinementPlugin(summarizer=lambda audit_id, commands: "unused")
    state = {
        SANDBOX_NOTES_STATE_KEY: [
            {
                "audit_id": "sandbox_run_test",
                "through_command_index": 3,
                "summary": "observations: 20 job cards; extraction_plan: card parser",
            }
        ]
    }
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_start.py",
                            "stdout": '{"status":"running","audit_id":"sandbox_run_test"}',
                        },
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_exec.py",
                            "stdout": "x" * 500,
                            "audit_id": "sandbox_run_test",
                        },
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_finalize.py",
                            "stdout": '{"status":"success","audit_id":"sandbox_run_test"}',
                        },
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="persist_sandbox_job_extraction",
                        response={"status": "success", "written_count": 20},
                    )
                ],
            ),
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=state), llm_request=llm_request))

    assert result is None
    start_response = llm_request.contents[0].parts[0].function_response.response
    exec_response = llm_request.contents[1].parts[0].function_response.response
    finalize_response = llm_request.contents[2].parts[0].function_response.response
    assert start_response["status"] == "sandbox_context_removed_after_completion"
    assert exec_response["status"] == "sandbox_context_removed_after_completion"
    assert finalize_response["status"] == "sandbox_context_removed_after_completion"
    assert "x" * 100 not in json.dumps(exec_response)
    assert exec_response["latest_runtime_note"]["summary"].startswith("observations: 20 job cards")
    note_text = llm_request.contents[-1].parts[0].text
    assert note_text.startswith("<RUNTIME_SANDBOX_NOTES>")
    assert note_text.endswith("</RUNTIME_SANDBOX_NOTES>")


def test_note_refinement_plugin_keeps_finalized_context_until_persistence_or_guardrail() -> None:
    plugin = SandboxNoteRefinementPlugin(summarizer=lambda audit_id, commands: "unused")
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_finalize.py",
                            "stdout": '{"status":"success","audit_id":"sandbox_run_test","result":{"jobs":[{"title":"AI"}]}}',
                        },
                    )
                ],
            )
        ]
    )

    asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state={}), llm_request=llm_request))

    response = llm_request.contents[0].parts[0].function_response.response
    assert response["stdout"].startswith('{"status":"success"')


def test_note_refinement_plugin_prunes_guardrail_terminal_sandbox_context() -> None:
    plugin = SandboxNoteRefinementPlugin(summarizer=lambda audit_id, commands: "unused")
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_exec.py",
                            "stdout": json.dumps(
                                {
                                    "status": "guardrail_triggered",
                                    "audit_id": "sandbox_run_test",
                                    "guardrail": "max_duration_seconds",
                                    "stdout": "long output",
                                }
                            ),
                        },
                    )
                ],
            )
        ]
    )

    asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state={}), llm_request=llm_request))

    response = llm_request.contents[0].parts[0].function_response.response
    assert response["status"] == "sandbox_context_removed_after_completion"
    assert response["guardrail"] == "max_duration_seconds"


def test_workflow_guard_blocks_terminal_text_while_workflow_sandbox_is_running() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    start_result = {
        "status": "success",
        "stdout": '{"status":"running","audit_id":"sandbox_run_test"}',
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_start.py"},
            tool_context=tool_context,
            result=start_result,
        )
    )

    assert updated is not None
    assert "Continue the sandbox workflow" in updated["required_next"]
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["audit_id"] == "sandbox_run_test"

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=tool_context.state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="Sandbox started but not completed.")],
                )
            ),
        )
    )

    assert replacement is not None
    function_call = replacement.content.parts[0].function_call
    assert function_call.name == "update_extraction_context"
    assert function_call.id.startswith("call_runtime_")
    assert "agent-chosen next tool" in function_call.args["immediate_goal"]
    assert "planned_next_tool" not in function_call.args


def test_model_reasoning_telemetry_surfaces_reasoning_summary_to_state() -> None:
    from job_scraper.adk_plugins import MODEL_REASONING_TELEMETRY_STATE_KEY, ModelReasoningTelemetryPlugin

    plugin = ModelReasoningTelemetryPlugin(reasoning_effort="high")
    state: dict[str, object] = {}

    llm_response = LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                genai_types.Part(text="Compared validator error with extraction strategy.", thought=True),
                genai_types.Part.from_text(text="Next action recorded."),
            ],
        ),
        model_version="openai/gpt-5.4-mini",
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12,
            candidates_token_count=8,
            total_token_count=20,
            thoughts_token_count=5,
        ),
    )

    result = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=state),
            llm_response=llm_response,
        )
    )

    assert result is None
    assert state[MODEL_REASONING_TELEMETRY_STATE_KEY] == {
        "model_version": "openai/gpt-5.4-mini",
        "reasoning_effort": "high",
        "thought_part_count": 1,
        "reasoning_summary_preview": "Compared validator error with extraction strategy.",
        "thoughts_token_count": 5,
    }
    assert llm_response.custom_metadata == {
        "job_scraper_reasoning": {
            "model_version": "openai/gpt-5.4-mini",
            "reasoning_effort": "high",
            "thought_part_count": 1,
            "reasoning_summary_preview": "Compared validator error with extraction strategy.",
            "thoughts_token_count": 5,
            "adk_web_surface": "model_event_custom_metadata",
        }
    }


def test_model_reasoning_telemetry_keeps_token_only_reasoning_out_of_chat_thoughts() -> None:
    from job_scraper.adk_plugins import MODEL_REASONING_TELEMETRY_STATE_KEY, ModelReasoningTelemetryPlugin

    plugin = ModelReasoningTelemetryPlugin(reasoning_effort="high")
    state: dict[str, object] = {}

    llm_response = LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                genai_types.Part.from_function_call(
                    name="update_extraction_context",
                    args={"immediate_goal": "record next step"},
                ),
            ],
        ),
        model_version="openai/gpt-5.4-mini",
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12,
            candidates_token_count=8,
            total_token_count=25,
            thoughts_token_count=5,
        ),
    )

    result = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=state),
            llm_response=llm_response,
        )
    )

    assert result is None
    assert state[MODEL_REASONING_TELEMETRY_STATE_KEY] == {
        "model_version": "openai/gpt-5.4-mini",
        "reasoning_effort": "high",
        "thought_part_count": 0,
        "thoughts_token_count": 5,
    }
    assert len(llm_response.content.parts) == 1
    assert llm_response.content.parts[0].function_call.name == "update_extraction_context"
    assert "adk_web_thought_part" not in llm_response.custom_metadata["job_scraper_reasoning"]


def test_workflow_guard_accepts_adk_state_shape_without_mutable_mapping() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state = FakeAdkState()

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_start.py"},
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": '{"status":"running","audit_id":"sandbox_run_test","mode":"workflow"}',
            },
        )
    )

    assert updated is not None
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["audit_id"] == "sandbox_run_test"
    assert ACTIVE_SANDBOX_STATE_KEY in tool_context.state.delta


def test_workflow_guard_does_not_block_diagnostic_sandbox_text() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    start_result = {
        "status": "success",
        "stdout": '{"status":"running","audit_id":"sandbox_run_test","mode":"diagnostic"}',
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_start.py"},
            tool_context=tool_context,
            result=start_result,
        )
    )

    assert updated is not None
    assert "diagnostic sandbox started" in updated["required_next"]
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["mode"] == "diagnostic"

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=tool_context.state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="stdout preview: ok")],
                )
            ),
        )
    )

    assert replacement is None


def test_workflow_guard_allows_only_one_sandbox_mode_resource() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="load_skill_resource")

    first = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "references/workflow-mode.md"},
            tool_context=tool_context,
        )
    )
    second = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "references/diagnostic-mode.md"},
            tool_context=tool_context,
        )
    )

    assert first is None
    assert second is not None
    assert second["status"] == "error"
    assert second["guardrail"] == "single_mode_resource"
    assert second["loaded_resource"] == "references/workflow-mode.md"
    assert second["requested_resource"] == "references/diagnostic-mode.md"


def test_workflow_guard_requires_itviec_reference_before_itviec_workflow_start() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_start.py",
                "args": [
                    "--mode",
                    "workflow",
                    "--page-artifact",
                    "pages/page.html",
                    "--source-url",
                    "https://itviec.com/it-jobs/ai-engineer/ha-noi",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "site_specific_reference_required"
    assert blocked["required_reference"] == "references/itviec-listing-page.md"
    assert "load_skill_resource" in blocked["required_next"]


def test_workflow_guard_allows_itviec_workflow_start_after_itviec_reference_loaded() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()

    loaded = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="load_skill_resource"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "references/itviec-listing-page.md",
            },
            tool_context=tool_context,
        )
    )
    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_start.py",
                "args": [
                    "--mode",
                    "workflow",
                    "--page-artifact",
                    "pages/page.html",
                    "--source-url",
                    "https://itviec.com/it-jobs/ai-engineer/ha-noi",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert loaded is None
    assert allowed is None


def test_workflow_guard_records_page_workspace_after_fetch() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="fetch_page_to_workspace"),
            tool_args={"url": "https://example.com/jobs"},
            tool_context=tool_context,
            result={
                "status": "success",
                "page_id": "page_123",
                "url": "https://example.com/jobs",
                "artifact_path": "/tmp/page.html",
                "artifact": {"artifact_name": "pages__page_123__page.html"},
                "metadata_artifact": {"artifact_name": "pages__page_123__metadata.json"},
            },
        )
    )

    assert updated is None
    assert tool_context.state[LAST_PAGE_WORKSPACE_STATE_KEY] == {
        "page_id": "page_123",
        "url": "https://example.com/jobs",
        "artifact_path": "/tmp/page.html",
        "page_artifact": "pages__page_123__page.html",
        "metadata_artifact": "pages__page_123__metadata.json",
    }


def test_workflow_guard_records_page_workspace_after_fixture_load() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="load_test_fixture_page_to_workspace"),
            tool_args={"fixture_name": "itviec_ai_engineer_ha_noi"},
            tool_context=tool_context,
            result={
                "status": "success",
                "page_id": "page_fixture",
                "url": "https://itviec.com/it-jobs/ai-engineer/ha-noi",
                "artifact_path": "/tmp/page.html",
                "artifact": {"artifact_name": "pages__page_fixture__page.html"},
                "metadata_artifact": {"artifact_name": "pages__page_fixture__metadata.json"},
            },
        )
    )

    assert updated is None
    assert tool_context.state[LAST_PAGE_WORKSPACE_STATE_KEY] == {
        "page_id": "page_fixture",
        "url": "https://itviec.com/it-jobs/ai-engineer/ha-noi",
        "artifact_path": "/tmp/page.html",
        "page_artifact": "pages__page_fixture__page.html",
        "metadata_artifact": "pages__page_fixture__metadata.json",
    }


def test_workflow_guard_injects_start_instruction_after_workflow_mode_load_without_active_sandbox() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        "_job_scraper_sandbox_mode_resource": {
            "file_path": "references/workflow-mode.md",
            "mode": "workflow",
        },
        LAST_PAGE_WORKSPACE_STATE_KEY: {
            "page_id": "page_123",
            "url": "https://example.com/jobs",
            "artifact_path": "/tmp/page.html",
        },
    }
    llm_request = SimpleNamespace(contents=[])

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=state), llm_request=llm_request))

    assert result is None
    guard_text = llm_request.contents[-1].parts[0].text
    assert guard_text.startswith("<RUNTIME_SANDBOX_START_GUARD>")
    assert guard_text.endswith("</RUNTIME_SANDBOX_START_GUARD>")
    assert "priority: hard operational constraint." in guard_text
    assert "scripts/sandbox_start.py" in guard_text
    assert "--page-artifact" in guard_text
    assert "/tmp/page.html" in guard_text
    assert "Do not load diagnostic-mode" in guard_text


def test_workflow_guard_injects_session_extraction_context() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        SESSION_EXTRACTION_CONTEXT_STATE_KEY: {
            "updated": True,
            "audit_id": "sandbox_run_test",
            "final_goal": "Extract validated jobs and save them.",
            "observations": ["20 job-card markers", "64 broad links included navigation"],
            "extraction_plan": ["repair extractor to select job-card containers"],
            "extraction_strategy": {
                "status": "active",
                "derived_from": "first representative card plus count probe",
                "target_units": "one job per repeated job-card container",
                "unit_boundary": "[data-search--pagination-target='jobCard']",
                "count_method": "count repeated card containers",
                "field_patterns": {"company_name": "visible company text near title"},
                "coverage_plan": "create and load one evidence chunk per card",
                "revision_policy": "enhance on new field details; revise on validator contradiction",
            },
            "last_result": {"status": "invalid", "count": 64},
            "known_errors": ["output/final.json missing because extractor is placeholder"],
            "attempted_actions": ["checked output/final.json existence", "read placeholder output/extractor.py"],
            "immediate_goal": "repair output/extractor.py",
            "planned_next_tool": {
                "tool_name": "run_skill_script",
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "target_paths": ["output/extractor.py"],
            },
            "repair_scope": {
                "status": "patching",
                "objective": "repair output/extractor.py card loop",
                "files": ["output/extractor.py"],
                "allowed_resources": ["references/itviec-listing-repair.md"],
                "allowed_inspections": ["output/extractor.py", "output/candidates.json"],
            },
            "required_outputs": [
                "output/page_profile.json",
                "output/extraction_strategy.json",
                "output/candidates.json",
                "output/validation.json",
                "output/final.json",
            ],
            "workflow_contract": {
                "producer": "output/extractor.py",
                "required_outputs": [
                    "output/page_profile.json",
                    "output/extraction_strategy.json",
                    "output/candidates.json",
                    "output/validation.json",
                    "output/final.json",
                ],
            },
            "workflow_reflections": [
                {
                    "trigger": "expected_output_count_mismatch",
                    "lesson": "Count mismatches usually mean coverage is incomplete or scope must be revised.",
                    "diagnostic_question": "Which repeated units lack evidence or serialized output?",
                    "state_changing_actions": ["load missing card evidence", "patch omitted-unit serialization"],
                    "anti_actions": ["rewrite the same candidates payload"],
                }
            ],
        }
    }
    llm_request = SimpleNamespace(contents=[])

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=state), llm_request=llm_request))

    assert result is None
    injected = llm_request.contents[-1].parts[0].text
    assert injected.startswith("<SESSION_EXTRACTION_CONTEXT>")
    assert injected.endswith("</SESSION_EXTRACTION_CONTEXT>")
    assert "priority: commanding guide for next-step reasoning." in injected
    assert "If <LATEST_TOOL_RESULT> is present, read it before this context" in injected
    assert "next most efficient planned_next_tool" in injected
    assert "Before every non-context tool call" in injected
    assert "do not call update_extraction_context merely because update_extraction_context succeeded" in injected
    assert "previous update_extraction_context failed" in injected
    assert "reconcile <LATEST_TOOL_RESULT> when present" in injected
    assert "final_goal, immediate_goal" in injected
    assert "Treat extraction_strategy as the current" in injected
    assert "enhance it when new evidence adds field/pattern detail" in injected
    assert "Do not repeat actions that did not change state" in injected
    assert "20 job-card markers" in injected
    assert "one job per repeated job-card container" in injected
    assert "visible company text near title" in injected
    assert "checked output/final.json existence" in injected
    assert "repair output/extractor.py" in injected
    assert "planned_next_tool" in injected
    assert "repair_scope" in injected
    assert "workflow_contract" in injected
    assert "required_outputs" in injected
    assert "workflow_reflections" in injected
    assert "learned interpretations" in injected
    assert "expected_output_count_mismatch" in injected
    assert "rewrite the same candidates payload" in injected
    assert "bounded work order" in injected
    assert "After a successful update_extraction_context action" in injected


def test_workflow_guard_injects_latest_tool_result_before_session_context() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        SESSION_EXTRACTION_CONTEXT_STATE_KEY: {
            "updated": True,
            "final_goal": "Extract validated jobs.",
            "last_result": {"status": "stale"},
        }
    }
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "status": "success",
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/validate_outputs.py",
                            "stdout": (
                                '{"status":"error","error_type":"fixture_mismatch",'
                                '"expected_count":20,"actual_count":23,'
                                '"missing_required_outputs":["output/page_profile.json"]}'
                            ),
                        },
                    )
                ],
            )
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=state), llm_request=llm_request))

    assert result is None
    injected_texts = [content.parts[0].text for content in llm_request.contents if getattr(content.parts[0], "text", None)]
    latest_index = next(index for index, text in enumerate(injected_texts) if text.startswith("<LATEST_TOOL_RESULT>"))
    session_index = next(index for index, text in enumerate(injected_texts) if text.startswith("<SESSION_EXTRACTION_CONTEXT>"))
    assert latest_index < session_index
    latest_text = injected_texts[latest_index]
    assert "scripts/validate_outputs.py" in latest_text
    assert "fixture_mismatch" in latest_text
    assert "output/page_profile.json" in latest_text
    assert "update_extraction_context before any state-changing tool" in latest_text
    assert "Successful update_extraction_context confirmations are not included" in latest_text


def test_workflow_guard_skips_successful_context_update_as_latest_tool_result() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="update_extraction_context",
                        response={"status": "success", "updated": True},
                    )
                ],
            )
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state={}), llm_request=llm_request))

    assert result is None
    injected_texts = [content.parts[0].text for content in llm_request.contents if getattr(content.parts[0], "text", None)]
    assert not any(text.startswith("<LATEST_TOOL_RESULT>") for text in injected_texts)


def test_workflow_guard_does_not_resurface_prior_tool_after_successful_context_update() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={"status": "success", "stdout": '{"job_count":20}'},
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="update_extraction_context",
                        response={"status": "success", "context_state": "updated"},
                    )
                ],
            ),
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state={}), llm_request=llm_request))

    assert result is None
    injected_texts = [content.parts[0].text for content in llm_request.contents if getattr(content.parts[0], "text", None)]
    assert not any(text.startswith("<LATEST_TOOL_RESULT>") for text in injected_texts)


def test_workflow_guard_prunes_loaded_resource_after_context_update() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    full_reference = "workflow mode details " * 600
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="load_skill_resource",
                        response={
                            "status": "success",
                            "skill_name": "sandbox-page-analyst",
                            "resource_path": "references/workflow-mode.md",
                            "content": full_reference,
                        },
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="update_extraction_context",
                        response={"status": "success", "context_state": "updated"},
                    )
                ],
            ),
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state={}), llm_request=llm_request))

    assert result is None
    resource_response = llm_request.contents[0].parts[0].function_response.response
    assert resource_response["status"] == "resource_context_removed_after_state_update"
    assert resource_response["resource_path"] == "references/workflow-mode.md"
    assert resource_response["resource_discarded_from_context"] is True
    assert "SESSION_EXTRACTION_CONTEXT" in resource_response["required_next"]
    assert resource_response["original_chars"] == len(full_reference)
    assert resource_response["content_preview"] != full_reference
    assert "content" not in resource_response


def test_workflow_guard_keeps_latest_loaded_resource_until_context_update() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    full_reference = "site layout details " * 400
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="update_extraction_context",
                        response={"status": "success", "context_state": "updated"},
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="load_skill_resource",
                        response={
                            "status": "success",
                            "skill_name": "sandbox-page-analyst",
                            "resource_path": "references/itviec-listing-page.md",
                            "content": full_reference,
                        },
                    )
                ],
            ),
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state={}), llm_request=llm_request))

    assert result is None
    resource_response = llm_request.contents[1].parts[0].function_response.response
    assert resource_response["content"] == full_reference


def test_workflow_guard_injects_failed_context_update_as_latest_tool_result() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="update_extraction_context",
                        response={
                            "status": "error",
                            "error_type": "extraction_context_update_policy",
                            "message": "planned_next_tool must be followed by an action",
                        },
                    )
                ],
            )
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state={}), llm_request=llm_request))

    assert result is None
    injected_texts = [content.parts[0].text for content in llm_request.contents if getattr(content.parts[0], "text", None)]
    latest_text = next(text for text in injected_texts if text.startswith("<LATEST_TOOL_RESULT>"))
    assert "update_extraction_context" in latest_text
    assert "extraction_context_update_policy" in latest_text
    assert "planned_next_tool must be followed" in latest_text
    assert "correct the state payload" in latest_text


def test_workflow_guard_requires_initial_context_before_workflow_tools() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="load_skill"),
            tool_args={"skill_name": "job-listing-scout"},
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "initial_extraction_context_required"
    assert "task_understanding, final_goal, and initial_plan" in blocked["required_next"]


def test_workflow_guard_requires_first_context_update_to_include_initial_plan() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={"observations": ["I should scrape the URL"]},
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "initial_extraction_context_required"
    assert "first update_extraction_context" in blocked["error"]


def test_workflow_guard_allows_tools_after_initial_context_exists() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "task_understanding": "Extract AI/ML jobs from the URL and save them.",
        "final_goal": "Extract AI/ML jobs from the URL and save them.",
        "initial_plan": ["load job-listing-scout", "save page to workspace"],
    }

    result = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="load_skill"),
            tool_args={"skill_name": "job-listing-scout"},
            tool_context=tool_context,
        )
    )

    assert result is None


def test_workflow_guard_rejects_incomplete_required_outputs_context() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "task_understanding": "extract jobs",
                "final_goal": "extract and validate jobs",
                "initial_plan": ["load resources"],
                "required_outputs": ["output/final.json"],
                "workflow_contract": {
                    "producer": "output/extractor.py",
                    "required_outputs": ["output/final.json"],
                },
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "workflow_contract_required"
    assert "output/page_profile.json" in blocked["missing_outputs"]


def test_workflow_guard_requires_workflow_contract_before_sandbox_start() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "task_understanding": "extract jobs",
        "final_goal": "extract jobs",
        "initial_plan": ["load workflow resources"],
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_start.py",
                "args": ["--mode", "workflow", "--page-artifact", "pages/page.html"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "workflow_contract_required"
    assert "required_outputs" in blocked["required_next"]


def test_workflow_guard_allows_sandbox_start_with_workflow_contract() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    required_outputs = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "required_outputs": required_outputs,
        "workflow_contract": {
            "producer": "output/extractor.py",
            "required_outputs": required_outputs,
            "success_gate": "validate and finalize before persistence",
            "repair_rule": "repair missing outputs at producer",
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_start.py",
                "args": ["--mode", "workflow", "--page-artifact", "pages/page.html"],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_allows_stdout_only_extractor_source() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    required_outputs = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "required_outputs": required_outputs,
        "workflow_contract": {
            "producer": "output/extractor.py",
            "required_outputs": required_outputs,
        },
        **workflow_output_plan_state(required_outputs),
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    "jobs = []\nprint(len(jobs))\n",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_allows_protocol_producer_source() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    required_outputs = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "required_outputs": required_outputs,
        "workflow_contract": {
            "producer": "output/extractor.py",
            "required_outputs": required_outputs,
        },
        **workflow_output_plan_state(required_outputs),
    }
    source = "\n".join(f"Path('{path}').write_text('{{}}')" for path in required_outputs)

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    source,
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_allows_placeholder_protocol_producer_source() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    required_outputs = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "required_outputs": required_outputs,
        "workflow_contract": {
            "producer": "output/extractor.py",
            "required_outputs": required_outputs,
        },
        **workflow_output_plan_state(required_outputs),
    }
    source = "\n".join(
        [
            "# Minimal placeholder producer for the audited workflow.",
            "from pathlib import Path",
            "import json",
            "candidates = {'crawl': {'status': 'stub'}, 'jobs': []}",
            *[f"Path('{path}').write_text(json.dumps({{}}))" for path in required_outputs],
        ]
    )

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    source,
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_allows_protocol_producer_source_with_output_path_composition() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    required_outputs = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "required_outputs": required_outputs,
        "workflow_contract": {
            "producer": "output/extractor.py",
            "required_outputs": required_outputs,
        },
        **workflow_output_plan_state(required_outputs),
    }
    source = "\n".join(
        [
            "OUT = Path('output')",
            "OUT.mkdir(exist_ok=True)",
            "(OUT / 'page_profile.json').write_text('{}')",
            "(OUT / 'extraction_strategy.json').write_text('{}')",
            "(OUT / 'extraction_run.json').write_text('{}')",
            "(OUT / 'candidates.json').write_text('{}')",
            "(OUT / 'validation.json').write_text('{}')",
            "(OUT / 'final.json').write_text('{}')",
            "(OUT / 'run_summary.md').write_text('summary')",
        ]
    )

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    source,
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_allows_protocol_producer_source_with_literal_output_paths() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    required_outputs = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "required_outputs": required_outputs,
        "workflow_contract": {
            "producer": "output/extractor.py",
            "required_outputs": required_outputs,
        },
        **workflow_output_plan_state(required_outputs),
    }
    source = "\n".join(f"Path('{path}').write_text('{{}}')" for path in required_outputs)

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    source,
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_runtime_prompt_blocks_keep_session_context_before_sandbox_notes() -> None:
    state = {
        SESSION_EXTRACTION_CONTEXT_STATE_KEY: {
            "updated": True,
            "observations": ["20 job-card markers"],
            "extraction_plan": ["extract repeated cards"],
            "attempted_actions": ["checked output/final.json existence"],
        },
        SANDBOX_NOTES_STATE_KEY: [
            {
                "audit_id": "sandbox_run_test",
                "summary": "supporting evidence: command 1 counted 20 cards",
            }
        ],
    }
    llm_request = SimpleNamespace(contents=[])

    assert (
        asyncio.run(
            SandboxWorkflowGuardPlugin().before_model_callback(
                callback_context=SimpleNamespace(state=state),
                llm_request=llm_request,
            )
        )
        is None
    )
    assert (
        asyncio.run(
            SandboxNoteRefinementPlugin(summarizer=lambda audit_id, commands: "unused").before_model_callback(
                callback_context=SimpleNamespace(state=state),
                llm_request=llm_request,
            )
        )
        is None
    )

    injected_texts = [content.parts[0].text for content in llm_request.contents]
    assert injected_texts[0].startswith("<SESSION_EXTRACTION_CONTEXT>")
    assert injected_texts[1].startswith("<RUNTIME_SANDBOX_NOTES>")
    assert "priority: commanding guide" in injected_texts[0]
    assert "priority: evidence only" in injected_texts[1]


def test_workflow_guard_blocks_record_crawl_run_before_workflow_sandbox_start() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state["_job_scraper_sandbox_mode_resource"] = {
        "file_path": "references/workflow-mode.md",
        "mode": "workflow",
    }
    tool_context.state[LAST_PAGE_WORKSPACE_STATE_KEY] = {
        "url": "https://example.com/jobs",
        "artifact_path": "/tmp/page.html",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="record_crawl_run"),
            tool_args={"run": {"status": "blocked"}},
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "workflow_requires_sandbox_start"
    assert "scripts/sandbox_start.py" in blocked["required_next"]


def test_workflow_guard_blocks_plain_text_before_workflow_sandbox_start() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        "_job_scraper_sandbox_mode_resource": {
            "file_path": "references/workflow-mode.md",
            "mode": "workflow",
        },
        LAST_PAGE_WORKSPACE_STATE_KEY: {
            "url": "https://example.com/jobs",
            "artifact_path": "/tmp/page.html",
        },
    }

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="saved_job_count: 0")],
                )
            ),
        )
    )

    assert replacement is None


def test_workflow_guard_clears_mode_resource_after_finalize_success() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    proposal_path = "/tmp/sandbox/output/reference_proposal.md"
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state["_job_scraper_sandbox_mode_resource"] = {
        "file_path": "references/workflow-mode.md",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_finalize.py"},
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": json.dumps(
                    {
                        "status": "success",
                        "audit_id": "sandbox_run_test",
                        "artifact_sources": [
                            {
                                "key": "output_reference_proposal",
                                "source_path": proposal_path,
                                "artifact_name": "sandbox_run_test__output__reference_proposal.md",
                                "mime_type": "text/markdown",
                            }
                        ],
                    }
                ),
            },
        )
    )

    assert updated is None
    assert "_job_scraper_sandbox_mode_resource" not in tool_context.state
    pending = tool_context.state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY]
    assert pending["audit_id"] == "sandbox_run_test"
    assert pending["promotion_status"] == "pending"
    assert pending["proposal_paths"] == [
        {
            "key": "output_reference_proposal",
            "workspace_path": "output/reference_proposal.md",
            "adk_artifact_name": "sandbox_run_test__output__reference_proposal.md",
        }
    ]


def test_workflow_guard_injects_promotion_guard_after_finalize_success() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        FINALIZED_SANDBOX_PROMOTION_STATE_KEY: {
            "audit_id": "sandbox_run_test",
            "status": "finalized",
            "promotion_status": "pending",
            "query_status": "pending",
            "written_count": 0,
        }
    }
    llm_request = SimpleNamespace(contents=[])

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=state), llm_request=llm_request))

    assert result is None
    injected = llm_request.contents[-1].parts[0].text
    assert injected.startswith("<RUNTIME_PERSISTENCE_GUARD>")
    assert "finalized artifacts are not saved jobs" in injected
    assert "Call promote_sandbox_extraction" in injected
    assert "Do not report extracted_job_count" in injected


def test_workflow_guard_requires_query_after_promotion_success() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "finalized",
        "promotion_status": "pending",
        "query_status": "pending",
        "written_count": 0,
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="promote_sandbox_extraction"),
            tool_args={"audit_id": "sandbox_run_test"},
            tool_context=tool_context,
            result={"status": "success", "audit_id": "sandbox_run_test", "written_count": 20, "validated_count": 20},
        )
    )

    assert updated is None
    pending = tool_context.state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY]
    assert pending["promotion_status"] == "success"
    assert pending["written_count"] == 20

    llm_request = SimpleNamespace(contents=[])
    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=tool_context.state), llm_request=llm_request))

    assert result is None
    injected = llm_request.contents[-1].parts[0].text
    assert "was promoted with written_count=20" in injected
    assert "Call query_jobs before the final response" in injected


def test_workflow_guard_injects_final_response_contract_after_promotion_and_query_success() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "finalized",
        "promotion_status": "success",
        "query_status": "pending",
        "written_count": 20,
        "proposal_paths": [
            {
                "key": "output_reference_proposal",
                "workspace_path": "output/reference_proposal.md",
                "adk_artifact_name": "sandbox_run_test__output__reference_proposal.md",
            }
        ],
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="query_jobs"),
            tool_args={},
            tool_context=tool_context,
            result={"status": "success", "count": 20, "items": [{"title": "AI Engineer"}]},
        )
    )

    assert updated is None
    pending = tool_context.state[FINALIZED_SANDBOX_PROMOTION_STATE_KEY]
    assert pending["query_status"] == "success"
    assert pending["queried_count"] == 20

    llm_request = SimpleNamespace(contents=[])
    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=tool_context.state), llm_request=llm_request))

    assert result is None
    assert len(llm_request.contents) == 1
    injected = llm_request.contents[-1].parts[0].text
    assert injected.startswith("<RUNTIME_FINAL_RESPONSE_CONTRACT>")
    assert "extracted_job_count" in injected
    assert "proposal_paths" in injected
    assert "output/reference_proposal.md" in injected


def test_workflow_guard_blocks_host_control_script_inside_sandbox_exec() -> None:
    plugin = SandboxWorkflowGuardPlugin()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--command",
                    "python scripts/sandbox_finalize.py --audit-id sandbox_run_test",
                ],
            },
            tool_context=FakeToolContext(),
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "host_control_script_inside_sandbox_exec"
    assert blocked["blocked_scripts"] == ["scripts/sandbox_finalize.py"]


def test_workflow_guard_blocks_validate_outputs_inside_sandbox_exec() -> None:
    plugin = SandboxWorkflowGuardPlugin()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--command",
                    "python scripts/validate_outputs.py output",
                ],
            },
            tool_context=FakeToolContext(),
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "host_control_script_inside_sandbox_exec"
    assert blocked["blocked_scripts"] == ["scripts/validate_outputs.py"]


def test_workflow_guard_blocks_compound_extractor_verification_command() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--cmd",
                    (
                        "python -m py_compile output/extractor.py && python output/extractor.py && "
                        "python - <<'PY'\nfrom pathlib import Path\nprint(Path('output/final.json').exists())\nPY"
                    ),
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["terminal"] is False
    assert blocked["guardrail"] == "compound_producer_verification_command"
    assert "python output/extractor.py" in blocked["required_next"]
    assert "validate_outputs.py" in blocked["required_next"]


def test_workflow_guard_allows_plain_extractor_run_command() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is None


def test_workflow_guard_treats_finalize_error_as_repairable_running_sandbox() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "forced_continuations": 0,
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_finalize.py"},
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": '{"status":"error","audit_id":"sandbox_run_test","error":"final.json status invalid"}',
            },
        )
    )

    assert updated is not None
    assert updated["status"] == "error"
    assert updated["tool_status"] == "success"
    assert updated["error"] == "final.json status invalid"
    assert "Load `sandbox-extraction-debugger`" in updated["required_next"]
    assert "do not read the missing files" in updated["required_next"]
    assert "scripts/sandbox_read.py" in updated["required_next"]
    assert "scripts/sandbox_exec.py" in updated["required_next"]
    assert "scripts/sandbox_write_file.py" in updated["required_next"]
    assert "modify only Docker sandbox workspace artifacts" in updated["required_next"]
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"
    assert (
        tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["last_repair_target"]["artifact_hint"]
        == "accountable protocol outputs"
    )

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=tool_context.state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="saved_jobs: 0")],
                )
            ),
        )
    )

    assert replacement is not None
    replacement_call = replacement.content.parts[0].function_call
    assert replacement_call.name == "update_extraction_context"
    assert replacement_call.id.startswith("call_runtime_")
    assert replacement_call.args["audit_id"] == "sandbox_run_test"
    assert replacement_call.args["status"] == "repairing"
    assert "Classify the active failure" in replacement_call.args["extraction_plan"][0]
    assert "agent-chosen repair plan" in replacement_call.args["immediate_goal"]
    assert "planned_next_tool" not in replacement_call.args


def test_workflow_guard_keeps_finalize_repair_target_after_successful_extractor_rerun() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "last_repair_target": {
            "file_path": "scripts/sandbox_finalize.py",
            "producer_hint": "output/extractor.py",
            "required_action": "debug_repair_extractor",
            "error": "candidates.crawl must be an object",
        },
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
            },
            tool_context=tool_context,
            result={"status": "success", "stdout": '{"status":"success","audit_id":"sandbox_run_test","command_index":4}'},
        )
    )

    active = tool_context.state[ACTIVE_SANDBOX_STATE_KEY]
    assert active["last_repair_target"]["error"] == "candidates.crawl must be an object"
    assert active["last_repair_target"]["producer_rerun_status"] == "success_unvalidated"
    assert active["last_repair_target"]["required_action"] == "validate_repaired_outputs"


def test_workflow_guard_blocks_plain_text_while_sandbox_running_without_repair_target() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        ACTIVE_SANDBOX_STATE_KEY: {
            "audit_id": "sandbox_run_test",
            "status": "running",
            "mode": "workflow",
        }
    }

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="I am blocked before finalization.")],
                )
            ),
        )
    )

    assert replacement is not None
    function_call = replacement.content.parts[0].function_call
    assert function_call.name == "update_extraction_context"
    assert function_call.args["audit_id"] == "sandbox_run_test"
    assert "agent-chosen next tool" in function_call.args["immediate_goal"]
    assert "planned_next_tool" not in function_call.args


def test_workflow_guard_blocks_reading_known_missing_protocol_file() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "last_repair_target": {
            "file_path": "scripts/sandbox_finalize.py",
            "producer_hint": "output/extractor.py",
            "required_action": "debug_repair_extractor",
            "error": (
                "missing required sandbox protocol outputs: "
                "output/page_profile.json, output/extraction_strategy.json"
            ),
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/page_profile.json",
                    "--max-chars",
                    "2000",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "repair_missing_protocol_output_at_producer"
    assert blocked["path"] == "output/page_profile.json"
    assert "Reading it again cannot repair" in blocked["error"]
    assert "inspected evidence/script output" in blocked["required_next"]
    assert "accountable protocol file" in blocked["required_next"]


def test_workflow_guard_injects_finalize_repair_target_before_next_model_call() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        ACTIVE_SANDBOX_STATE_KEY: {
            "audit_id": "sandbox_run_test",
            "status": "running",
            "mode": "workflow",
            "last_repair_target": {
                "file_path": "scripts/sandbox_finalize.py",
                "producer_hint": "output/extractor.py",
                "error": "ITviec listing evidence expects 20 jobs but candidates.jobs has 1",
            },
        }
    }
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_start.py",
                            "stdout": '{"status":"running","audit_id":"sandbox_run_test","mode":"workflow"}',
                        },
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_finalize.py",
                            "stdout": '{"status":"error","audit_id":"sandbox_run_test","error":"ITviec listing evidence expects 20 jobs but candidates.jobs has 1"}',
                        },
                    )
                ],
            ),
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=state), llm_request=llm_request))

    assert result is None
    guard_text = llm_request.contents[-1].parts[0].text
    assert "latest sandbox result is actionable repair feedback" in guard_text
    assert "Do not answer the user yet" in guard_text
    assert "sandbox-extraction-debugger" in guard_text
    assert "helper serialization" in guard_text
    assert "loaded evidence" in guard_text
    assert "ITviec listing evidence expects 20 jobs but candidates.jobs has 1" in guard_text


def test_workflow_guard_routes_sandbox_write_validation_errors_to_debugger_skill() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/candidates.json"],
            },
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": '{"status":"error","audit_id":"sandbox_run_test","error_type":"protocol_model_validation","path":"output/candidates.json"}',
            },
        )
    )

    assert updated is not None
    assert "Load `sandbox-extraction-debugger`" in updated["required_next"]
    assert "output artifact or sandbox-written script caused the error" in updated["required_next"]
    assert "Treat mounted helper scripts and schemas as read-only specs" in updated["required_next"]
    assert "protocol_model_validation" in updated["required_next"]
    assert (
        tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["last_repair_target"]["artifact_hint"]
        == "accountable protocol outputs"
    )
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["last_repair_target"]["required_action"] == "agent_plan_repair"


def test_workflow_guard_routes_missing_evidence_index_to_evidence_repair() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_finalize.py",
                "args": ["--audit-id", "sandbox_run_test"],
            },
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": (
                    '{"status":"error","audit_id":"sandbox_run_test",'
                    '"error":"evidence/index.json is required when jobs cite evidence refs"}'
                ),
            },
        )
    )

    assert updated is not None
    assert "evidence/chunks/" in updated["required_next"]
    assert "evidence/index.json" in updated["required_next"]
    assert "Do not rerun finalization" in updated["required_next"]


def test_workflow_guard_routes_rejected_initial_extractor_write_to_rewrite_not_patch() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    "print('stdout-only')",
                ],
            },
            tool_context=tool_context,
            result={
                "status": "error",
                "error_type": "workflow_contract_policy",
                "audit_id": "sandbox_run_test",
                "error": "output/extractor.py must be a protocol producer, not a stdout-only extractor. Its source must write every required protocol output in one run.",
            },
        )
    )

    assert updated is not None
    assert "do not patch a missing file" in updated["required_next"]
    assert "scripts/sandbox_write_file.py" in updated["required_next"]
    target = tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["last_repair_target"]
    assert target["file_path"] == "scripts/sandbox_write_file.py"
    assert target["target_path"] == "output/extractor.py"

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=tool_context.state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="Blocked: extractor missing.")],
                )
            ),
        )
    )

    assert replacement is not None
    function_call = replacement.content.parts[0].function_call
    assert function_call.name == "update_extraction_context"
    assert "decide the next repair action" in function_call.args["extraction_plan"][0].lower()
    assert "runtime has recorded the active failure but is not choosing" in function_call.args["immediate_goal"].lower()
    assert "planned_next_tool" not in function_call.args


def test_workflow_guard_records_validate_output_errors_as_repair_targets() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/validate_outputs.py",
                "args": ["--audit-id", "sandbox_run_test"],
            },
            tool_context=tool_context,
            result={
                "status": "error",
                "stderr": "missing required protocol output: output/page_profile.json",
            },
        )
    )

    assert updated is not None
    assert "Load `sandbox-extraction-debugger`" in updated["required_next"]
    target = tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["last_repair_target"]
    assert target["file_path"] == "scripts/validate_outputs.py"
    assert target["artifact_hint"] == "accountable protocol outputs"
    assert target["required_action"] == "agent_plan_repair"
    assert "page_profile.json" in target["error"]


def test_workflow_guard_replaces_final_text_when_debug_repair_target_is_active() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        ACTIVE_SANDBOX_STATE_KEY: {
            "audit_id": "sandbox_run_test",
            "status": "running",
            "mode": "workflow",
            "last_repair_target": {
                "file_path": "scripts/validate_outputs.py",
                "producer_hint": "output/extractor.py",
                "required_action": "debug_repair_extractor",
                "error": "missing required protocol output: output/page_profile.json",
            },
        }
    }

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="Blocked: missing page_profile.json.")],
                )
            ),
        )
    )

    assert replacement is not None
    function_call = replacement.content.parts[0].function_call
    assert function_call.name == "update_extraction_context"
    assert function_call.args["audit_id"] == "sandbox_run_test"
    assert function_call.args["status"] == "repairing"
    assert "page_profile.json" in function_call.args["known_errors"][0]
    assert "agent-chosen repair plan" in function_call.args["immediate_goal"]
    assert "not choosing the repair tool for the agent" in function_call.args["immediate_goal"]
    assert "planned_next_tool" not in function_call.args


def test_workflow_guard_does_not_replace_final_text_after_context_update_policy_block() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        ACTIVE_SANDBOX_STATE_KEY: {
            "audit_id": "sandbox_run_test",
            "status": "running",
            "mode": "workflow",
            "last_repair_target": {
                "file_path": "scripts/validate_outputs.py",
                "producer_hint": "output/extractor.py",
                "required_action": "debug_repair_extractor",
                "error": "missing required protocol output: output/page_profile.json",
            },
        },
        IMMEDIATE_ERROR_REPEAT_STATE_KEY: {
            "tool_name": "update_extraction_context",
            "error_type": "extraction_context_update_policy",
            "error": "context-only update blocked",
        },
    }

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text="Blocked: cannot safely repair.")],
                )
            ),
        )
    )

    assert replacement is None


def test_workflow_guard_does_not_replace_tool_call_when_debug_repair_target_is_active() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        ACTIVE_SANDBOX_STATE_KEY: {
            "audit_id": "sandbox_run_test",
            "status": "running",
            "mode": "workflow",
            "last_repair_target": {
                "file_path": "scripts/validate_outputs.py",
                "producer_hint": "output/extractor.py",
                "required_action": "debug_repair_extractor",
                "error": "missing required protocol output: output/page_profile.json",
            },
        }
    }

    replacement = asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(state=state),
            llm_response=LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[
                        genai_types.Part.from_function_call(
                            name="run_skill_script",
                            args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_write_file.py"},
                        )
                    ],
                )
            ),
        )
    )

    assert replacement is None


def test_workflow_guard_rejects_sandbox_exec_pass_through_args() -> None:
    plugin = SandboxWorkflowGuardPlugin()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--", "python", "output/extractor.py"],
            },
            tool_context=FakeToolContext(),
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "sandbox_exec_requires_cmd_argument"
    assert "--cmd" in blocked["required_next"]


def test_workflow_guard_rejects_sandbox_read_max_bytes_arg() -> None:
    plugin = SandboxWorkflowGuardPlugin()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "inputs.json", "--max-bytes", "3000"],
            },
            tool_context=FakeToolContext(),
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "sandbox_read_uses_max_chars"
    assert "--max-chars" in blocked["required_next"]


def test_workflow_guard_rejects_sandbox_helper_without_audit_id_after_start() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--cmd", "python output/extractor.py"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "sandbox_script_requires_audit_id"
    assert blocked["audit_id"] == "sandbox_run_test"
    assert "sandbox_exec.py" in blocked["file_path"]
    assert "--audit-id sandbox_run_test" in blocked["required_next"]
    assert "sandbox-extraction-debugger" not in blocked["required_next"]


def test_workflow_guard_rejects_sandbox_write_file_without_path_and_content() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": ["--audit-id", "sandbox_run_test", "output/extractor.py"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "sandbox_write_file_requires_path_and_content"
    assert "--path output/extractor.py --content" in blocked["required_next"]
    assert "--help" in blocked["required_next"]


def test_workflow_guard_does_not_escalate_missing_audit_id_to_debugger() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": ["output/extractor.py"],
            },
            tool_context=tool_context,
            result={
                "status": "error",
                "error_type": "sandbox_script_args_policy",
                "guardrail": "sandbox_script_requires_audit_id",
                "file_path": "scripts/sandbox_write_file.py",
                "error": "scripts/sandbox_write_file.py must include `--audit-id <audit_id>` while a sandbox workflow is active.",
            },
        )
    )

    assert updated is not None
    assert "sandbox-extraction-debugger" not in updated["required_next"]
    assert "--audit-id sandbox_run_test" in updated["required_next"]
    assert "repair_error_policy" not in updated


def test_workflow_guard_allows_sandbox_helper_help_without_audit_id_after_start() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": ["--help"],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None
    assert SANDBOX_TOOL_BUDGET_STATE_KEY not in tool_context.state
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"


def test_workflow_guard_triggers_on_repeated_finalize_error() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "forced_continuations": 0,
    }
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_finalize.py"}
    result = {
        "status": "success",
        "stdout": '{"status":"error","audit_id":"sandbox_run_test","error":"final.json status invalid"}',
    }

    first = asyncio.run(
        plugin.after_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context, result=result)
    )
    second = asyncio.run(
        plugin.after_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context, result=result)
    )

    assert first is not None
    assert "Load `sandbox-extraction-debugger`" in first["required_next"]
    assert second is not None
    assert second["status"] == "guardrail_triggered"
    assert second["guardrail"] == "repeated_sandbox_tool_result"
    assert "sandbox_finalize.py" in second["error"]
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "guardrail_triggered"


def test_workflow_guard_blocks_immediate_identical_retry_after_tool_error() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "task_understanding": "Extract jobs",
        "initial_plan": ["validate sandbox outputs"],
    }
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/validate_outputs.py",
        "args": ["--audit-id", "sandbox_run_test"],
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={"status": "error", "stderr": "missing output/page_profile.json"},
        )
    )

    blocked = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "same_tool_invocation_after_error"
    assert blocked["terminal"] is False
    assert blocked["previous_error"] == "missing output/page_profile.json"
    assert "Immediate retry blocked" in blocked["error"]
    assert "previous_error=missing output/page_profile.json" in blocked["error"]
    assert blocked["previous_invocation"]["file_path"] == "scripts/validate_outputs.py"
    assert "must not be identical" in blocked["required_next"]


def test_workflow_guard_allows_same_failed_invocation_after_different_tool_call() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "task_understanding": "Extract jobs",
        "initial_plan": ["validate sandbox outputs"],
    }
    failed_tool = SimpleNamespace(name="run_skill_script")
    failed_args = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/validate_outputs.py",
        "args": ["--audit-id", "sandbox_run_test"],
    }
    different_args = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_read.py",
        "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py"],
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=failed_tool,
            tool_args=failed_args,
            tool_context=tool_context,
            result={"status": "error", "stderr": "missing output/page_profile.json"},
        )
    )

    assert IMMEDIATE_ERROR_REPEAT_STATE_KEY in tool_context.state
    assert (
        asyncio.run(plugin.before_tool_callback(tool=failed_tool, tool_args=different_args, tool_context=tool_context))
        is None
    )
    assert IMMEDIATE_ERROR_REPEAT_STATE_KEY not in tool_context.state
    assert asyncio.run(plugin.before_tool_callback(tool=failed_tool, tool_args=failed_args, tool_context=tool_context)) is None


def test_workflow_guard_records_run_skill_script_stdout_json_errors_for_immediate_retry_guard() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "task_understanding": "Extract jobs",
        "initial_plan": ["run extractor"],
    }
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_exec.py",
        "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": json.dumps(
                    {
                        "status": "error",
                        "audit_id": "sandbox_run_test",
                        "error_type": "command_failed",
                        "stderr": "NameError: name 'cards' is not defined",
                    }
                ),
            },
        )
    )

    blocked = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))

    assert blocked is not None
    assert blocked["guardrail"] == "same_tool_invocation_after_error"
    assert blocked["previous_error_type"] == "command_failed"
    assert blocked["previous_error"] == "NameError: name 'cards' is not defined"
    assert "previous_error_type=command_failed" in blocked["error"]
    assert "NameError: name 'cards' is not defined" in blocked["error"]


def test_workflow_guard_immediate_repeat_script_not_found_error_points_to_resource_path_fix() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {
        "skill_name": "sandbox-extraction-debugger",
        "file_path": "scripts/sandbox_apply_patch.py",
        "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py"],
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={
                "status": "error",
                "error_type": "SCRIPT_NOT_FOUND",
                "error": "Script not found: scripts/sandbox_apply_patch.py",
            },
        )
    )

    blocked = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))

    assert blocked is not None
    assert blocked["guardrail"] == "same_tool_invocation_after_error"
    assert blocked["previous_error_type"] == "SCRIPT_NOT_FOUND"
    assert "previous_error_type=SCRIPT_NOT_FOUND" in blocked["error"]
    assert "skill script/path was not available" in blocked["required_next"]
    assert "correct skill_name/file_path" in blocked["required_next"]


def test_workflow_guard_clears_immediate_error_repeat_state_after_success() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool = SimpleNamespace(name="query_jobs")
    tool_args = {"filters": {"source": "itviec"}}

    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={"status": "error", "error": "database unavailable"},
        )
    )
    assert IMMEDIATE_ERROR_REPEAT_STATE_KEY in tool_context.state

    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result={"status": "success", "count": 20},
        )
    )

    assert IMMEDIATE_ERROR_REPEAT_STATE_KEY not in tool_context.state


def test_workflow_guard_routes_repeated_successful_protocol_write_to_validation() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "forced_continuations": 0,
    }
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_write_file.py",
        "args": [
            "--audit-id",
            "sandbox_run_test",
            "--path",
            "output/final.json",
            "--content",
            '{"status":"needs_review","result":{"jobs":[]}}',
        ],
    }
    result = {
        "status": "success",
        "stdout": '{"status":"success","audit_id":"sandbox_run_test","path":"output/final.json"}',
    }

    assert asyncio.run(plugin.after_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context, result=result)) is None
    assert asyncio.run(plugin.after_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context, result=result)) is None
    third = asyncio.run(plugin.after_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context, result=result))

    assert third is not None
    assert third["status"] == "error"
    assert third["guardrail"] == "repeated_sandbox_tool_result"
    assert third["terminal"] is False
    assert "sandbox_write_file.py" in third["error"]
    assert "validate_outputs.py" in third["required_next"]
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"


def test_workflow_guard_allows_changed_protocol_write_after_validation_error() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool = SimpleNamespace(name="run_skill_script")
    base_args = [
        "--audit-id",
        "sandbox_run_test",
        "--path",
        "output/extractor.py",
    ]
    result = {
        "status": "error",
        "error_type": "workflow_contract_policy",
        "audit_id": "sandbox_run_test",
        "error": "output/extractor.py must write every required protocol output",
    }

    first = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [*base_args, "--content", "print('stdout-only')"],
            },
            tool_context=tool_context,
            result=result,
        )
    )
    second = asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [*base_args, "--content", "Path('output/final.json').write_text('{}')"],
            },
            tool_context=tool_context,
            result=result,
        )
    )

    assert first is not None
    assert first.get("guardrail") != "repeated_sandbox_tool_result"
    assert second is not None
    assert second.get("guardrail") != "repeated_sandbox_tool_result"
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"


def test_workflow_guard_tracks_written_extractor_as_pending_verification() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    "print('extract')",
                ],
            },
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": '{"status":"success","audit_id":"sandbox_run_test","path":"output/extractor.py"}',
            },
        )
    )

    assert updated is not None
    assert "python output/extractor.py" in updated["required_next"]
    assert tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY]["sandbox_run_test"]["status"] == "written_not_executed"


def test_workflow_guard_blocks_finalize_until_written_extractor_runs() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = {
        "sandbox_run_test": {"path": "output/extractor.py", "status": "written_not_executed"}
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_finalize.py",
                "args": ["--audit-id", "sandbox_run_test"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "written_script_must_run_before_finalization"
    assert "python output/extractor.py" in blocked["required_next"]


def test_workflow_guard_blocks_rewriting_extractor_after_successful_run_until_validation() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "extractor_executed": True,
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--content",
                    "print('rewritten')",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "validate_or_finalize_after_successful_producer_run"
    assert "validate_outputs.py" in blocked["required_next"]
    assert "sandbox_finalize.py" in blocked["required_next"]


def test_workflow_guard_allows_extractor_patch_after_fresh_validator_error() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "extractor_executed": True,
        "last_repair_target": {
            "file_path": "scripts/validate_outputs.py",
            "producer_hint": "output/extractor.py",
            "required_action": "debug_repair_extractor",
            "error": "candidates.crawl must be an object",
        },
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--old",
                    "old",
                    "--new",
                    "new",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_allows_extractor_patch_after_finalizer_fixture_diff_target() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "extractor_executed": True,
        "last_repair_target": {
            "file_path": "scripts/sandbox_finalize.py",
            "producer_hint": "output/extractor.py",
            "error": "fixture-diff mismatch: company_name expected OpenAI but got unknown",
        },
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--old",
                    "company = 'unknown'",
                    "--new",
                    "company = parsed_company",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_redirects_debugger_skill_helper_calls_to_page_analyst() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-extraction-debugger",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--help"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "sandbox_helpers_live_under_page_analyst_skill"
    assert blocked["requested_skill_name"] == "sandbox-extraction-debugger"
    assert 'skill_name "sandbox-page-analyst"' in blocked["required_next"]


def test_workflow_guard_marks_patched_extractor_pending_until_rerun() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "extractor_executed": True,
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extractor.py",
                    "--old",
                    "old",
                    "--new",
                    "new",
                ],
            },
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": json.dumps(
                    {
                        "status": "success",
                        "audit_id": "sandbox_run_test",
                        "changed_files": [{"path": "output/extractor.py"}],
                    }
                ),
            },
        )
    )

    pending = tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY]["sandbox_run_test"]
    assert updated is not None
    assert "python output/extractor.py" in updated["required_next"]
    assert pending["status"] == "patched_not_executed"
    assert pending["path"] == "output/extractor.py"
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["extractor_executed"] is False


def test_workflow_guard_blocks_validate_until_patched_extractor_reruns() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = {
        "sandbox_run_test": {"path": "output/extractor.py", "status": "patched_not_executed"}
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/validate_outputs.py",
                "args": ["--audit-id", "sandbox_run_test"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "written_script_must_run_before_finalization"
    assert "python output/extractor.py" in blocked["required_next"]


def test_workflow_guard_blocks_protocol_output_write_without_expected_output() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/candidates.json",
                    "--content",
                    '{"jobs":[]}',
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "expected_output_required"
    assert "expected_output.expected_job_count" in blocked["error"]


def test_workflow_guard_blocks_output_producer_write_until_contract_plan_recorded() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    context = workflow_contract_state()
    context.pop("output_contract", None)
    context.pop("producer_output_plan", None)
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = context

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/extract_itviec_fixture.py",
                    "--content",
                    "print('extract')",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "producer_output_plan_required"
    assert blocked["required_next_tool"]["file_path"] == "scripts/protocol_contract.py"
    assert "producer_output_plan" in blocked["required_next"]


def test_workflow_guard_blocks_protocol_write_until_agent_records_producer_plan() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    context = workflow_contract_state()
    context.pop("producer_output_plan", None)
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **context,
        "expected_output": {
            "expected_job_count": 0,
            "count_basis": "no repeated job units observed",
            "count_rationale": "The prior repeated-unit probe found no job units, so zero successful jobs are expected.",
            "available_fields": {"title": "not_observed"},
            "field_basis": {},
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/candidates.json",
                    "--content",
                    '{"jobs":[]}',
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "producer_output_plan_required"
    assert blocked["required_next_tool"]["tool_name"] == "update_extraction_context"
    assert "script_manifest" in blocked["required_next_tool"]["producer_output_plan"]


def test_workflow_guard_allows_agent_authored_protocol_output_write_after_helper_runs() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "extractor_executed": True,
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **workflow_contract_state(),
        "expected_output": {
            "expected_job_count": 0,
            "count_basis": "no repeated job units observed",
            "count_rationale": "The prior repeated-unit probe found no job units, so zero successful jobs are expected.",
            "available_fields": {"title": "not_observed"},
            "field_basis": {},
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/candidates.json",
                    "--content",
                    '{"jobs":[]}',
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_requires_expected_output_count_rationale_before_success_write() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **workflow_contract_state(),
        "expected_output": {
            "expected_job_count": 2,
            "count_basis": "2 repeated job-card markers",
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/candidates.json",
                    "--content",
                    json.dumps({"jobs": [{"title": "One"}, {"title": "Two"}]}),
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "expected_output_count_explanation_required"
    assert blocked["missing"] == ["count_rationale"]
    assert blocked["unsatisfied_requirements"][0]["id"] == "expected_output_count_derivation_recorded"
    assert "prior observations" in blocked["unsatisfied_requirements"][0]["agent_responsibility"]


def test_workflow_guard_blocks_candidates_count_mismatch_against_expected_output() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **workflow_contract_state(),
        "expected_output": {
            "expected_job_count": 2,
            "count_basis": "2 repeated job-card markers",
            "count_rationale": "A prior selector-count probe found two repeated job-card markers; each marker is one in-scope listing.",
            "available_fields": {"title": "required_observed"},
            "field_basis": {"title": "Each repeated job-card marker exposes visible title text."},
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/candidates.json",
                    "--content",
                    json.dumps({"jobs": [{"title": "One"}]}),
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "expected_output_count_mismatch"
    assert blocked["expected_job_count"] == 2
    assert blocked["actual_job_count"] == 1
    assert blocked["count_basis"] == "2 repeated job-card markers"
    assert blocked["unsatisfied_requirements"][0]["id"] == "successful_output_matches_expected_job_count"
    assert "missing prerequisite" in blocked["unsatisfied_requirements"][0]["agent_responsibility"]
    assert "count_basis" in blocked["unsatisfied_requirements"][0]["agent_responsibility"]
    assert "inspect available tools/resources" in blocked["unsatisfied_requirements"][0]["agent_responsibility"]
    assert "same repeated-unit signal" in blocked["unsatisfied_requirements"][0]["acceptable_resolutions"][0]
    assert "Inspect available tools/resources" in blocked["unsatisfied_requirements"][0]["acceptable_resolutions"][0]
    assert "Use unsatisfied_requirements" in blocked["required_next"]
    assert "available tools/resources" in blocked["required_next"]
    assert "past observations/tool results" in blocked["required_next"]


def test_workflow_guard_blocks_final_count_mismatch_against_expected_output() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **workflow_contract_state(),
        "expected_output": {
            "expected_job_count": 2,
            "count_basis": "2 repeated detail URLs",
            "count_rationale": "A prior URL probe found two repeated detail URLs; each URL is one in-scope listing.",
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/final.json",
                    "--content",
                    json.dumps({"status": "success", "result": {"jobs": [{"title": "One"}]}}),
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "expected_output_count_mismatch"
    assert blocked["expected_job_count"] == 2
    assert blocked["actual_job_count"] == 1
    assert blocked["unsatisfied_requirements"][0]["expected_job_count"] == 2


def test_workflow_guard_allows_candidates_count_matching_expected_output() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **workflow_contract_state(),
        "expected_output": {
            "expected_job_count": 2,
            "count_basis": "2 repeated job-card markers",
            "count_rationale": "A prior selector-count probe found two repeated job-card markers; each marker is one in-scope listing.",
            "available_fields": {"title": "required_observed"},
            "field_basis": {"title": "Each repeated job-card marker exposes visible title text."},
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/candidates.json",
                    "--content",
                    json.dumps({"jobs": [{"title": "One"}, {"title": "Two"}]}),
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_blocks_placeholder_for_required_observed_field() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **workflow_contract_state(),
        "expected_output": {
            "expected_job_count": 1,
            "count_basis": "1 repeated job-card marker",
            "count_rationale": "A prior selector-count probe found one repeated job-card marker.",
            "available_fields": {
                "title": "required_observed",
                "company_name": "required_observed",
                "job_url": "required_observed",
            },
            "field_basis": {
                "title": "The card exposes title text.",
                "company_name": "The card exposes company text next to the title.",
                "job_url": "The card exposes a detail URL.",
            },
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/candidates.json",
                    "--content",
                    json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Machine Learning Engineer",
                                    "company_name": "unknown",
                                    "job_url": "https://example.com/jobs/ml",
                                }
                            ]
                        }
                    ),
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "expected_output_field_coverage_mismatch"
    assert blocked["unsatisfied_requirements"][0]["id"] == "successful_output_matches_observed_field_availability"
    assert blocked["missing_or_placeholder_fields"] == [{"index": 0, "field": "company_name", "value": "unknown"}]


def test_workflow_guard_allows_needs_review_count_mismatch() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        **workflow_contract_state(),
        "expected_output": {
            "expected_job_count": 2,
            "count_basis": "2 repeated job-card markers",
            "count_rationale": "A prior selector-count probe found two repeated job-card markers; each marker is one in-scope listing.",
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--path",
                    "output/final.json",
                    "--content",
                    json.dumps(
                        {
                            "status": "needs_review",
                            "result": {"jobs": [{"title": "One"}]},
                            "summary": "one repeated unit could not be loaded",
                        }
                    ),
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_routes_expected_output_error_as_unsatisfied_requirement() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/candidates.json"],
            },
            tool_context=tool_context,
            result={
                "status": "error",
                "error_type": "expected_output_policy",
                "guardrail": "expected_output_count_mismatch",
                "audit_id": "sandbox_run_test",
                "path": "output/candidates.json",
                "expected_job_count": 2,
                "actual_job_count": 1,
                "error": "The agent declared expected_output.expected_job_count=2, but output/candidates.json contains 1 jobs.",
            },
        )
    )

    assert updated is not None
    assert "unsatisfied_requirements" in updated["required_next"]
    assert "not as a scripted tool plan" in updated["required_next"]
    assert "inspect how expected_output was derived" in updated["required_next"]
    assert "inspect the available tools/resources" in updated["required_next"]
    assert "not been loaded" in updated["required_next"]


def test_workflow_guard_clears_pending_extractor_after_successful_run() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = {
        "sandbox_run_test": {"path": "output/extractor.py", "status": "written_not_executed"}
    }

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
            },
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": '{"status":"success","audit_id":"sandbox_run_test","command_index":4,"exit_code":0,"stdout":"wrote candidates"}',
            },
        )
    )

    assert updated is None
    assert "sandbox_run_test" not in tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY]
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["extractor_executed"] is True


def test_workflow_guard_keeps_pending_extractor_after_failed_run() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = {
        "sandbox_run_test": {"path": "output/extractor.py", "status": "written_not_executed"}
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
            },
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": '{"status":"error","audit_id":"sandbox_run_test","command_index":4,"exit_code":1,"stderr":"boom"}',
            },
        )
    )

    pending = tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY]["sandbox_run_test"]
    assert pending["status"] == "execution_failed"
    assert pending["last_error"] == "boom"


def test_workflow_guard_blocks_record_query_while_active_workflow_sandbox_has_pending_script() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SANDBOX_PENDING_SCRIPT_STATE_KEY] = {
        "sandbox_run_test": {"path": "output/extractor.py", "status": "written_not_executed"}
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="record_crawl_run"),
            tool_args={"run": {"status": "blocked"}},
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "written_script_must_run_before_finalization"
    assert blocked["count"] == 0


def test_workflow_guard_marks_persistence_errors_as_repairable() -> None:
    plugin = SandboxWorkflowGuardPlugin()

    updated = asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="persist_sandbox_job_extraction"),
            tool_args={},
            tool_context=FakeToolContext(),
            result={"status": "error", "error": "jobs.0.company_name Input should be a valid string"},
        )
    )

    assert updated is not None
    assert "correct the sandbox-produced extraction payload" in updated["required_next"]
    assert "Returned errors are actionable repair feedback" in updated["repair_error_policy"]


def test_workflow_guard_blocks_persistence_when_sandbox_guardrail_triggered() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "guardrail_triggered",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="persist_sandbox_job_extraction"),
            tool_args={
                "extraction": {
                    "result": {
                        "jobs": [
                            {
                                "title": "AI Engineer",
                                "company_name": "Acme",
                                "job_url": "https://example.com/jobs/ai",
                            }
                        ]
                    }
                }
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["written_count"] == 0
    assert "guardrail_triggered" in blocked["error"]
    assert "Do not persist" in blocked["required_next"]


def test_workflow_guard_does_not_block_persistence_for_open_diagnostic_sandbox() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "diagnostic",
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="persist_sandbox_job_extraction"),
            tool_args={
                "extraction": {
                    "result": {
                        "jobs": [
                            {
                                "title": "AI Engineer",
                                "company_name": "Acme",
                                "job_url": "https://example.com/jobs/ai",
                            }
                        ]
                    }
                }
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_blocks_invalid_persistence_payload_before_write() -> None:
    plugin = SandboxWorkflowGuardPlugin()

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="persist_sandbox_job_extraction"),
            tool_args={
                "extraction": {
                    "jobs": [
                        {
                            "title": "AI Engineer",
                            "company_name": None,
                            "job_url": "https://example.com/jobs/ai",
                        }
                    ]
                }
            },
            tool_context=FakeToolContext(),
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["written_count"] == 0
    assert "company_name" in blocked["error"]
    assert "schema error" in blocked["required_next"]


def test_workflow_guard_blocks_repeated_noop_extraction_context_update() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool = SimpleNamespace(name="update_extraction_context")
    tool_args = {
        "audit_id": "sandbox_run_test",
        "observations": ["20 job cards, extractor emitted 117"],
        "extraction_plan": ["repair card selector"],
        "known_errors": ["count mismatch"],
        "attempted_actions": ["ran extractor"],
        "immediate_goal": "repair extractor",
    }

    first = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))
    second = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))

    assert first is None
    assert second is not None
    assert second["status"] == "error"
    assert second["guardrail"] == "repeated_extraction_context_updates"
    assert second["terminal"] is False
    assert second["repeat_count"] == 2
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"
    assert "look at the plan you wrote previously" in second["required_next"].lower()
    assert "sandbox remains active" in second["required_next"]


def test_workflow_guard_blocks_too_many_consecutive_context_updates() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool = SimpleNamespace(name="update_extraction_context")

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={
                "audit_id": "sandbox_run_test",
                "observations": ["observation 1"],
                "extraction_plan": ["repair extractor"],
                "attempted_actions": ["context update 1"],
            },
            tool_context=tool_context,
        )
    )
    assert allowed is None

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={
                "audit_id": "sandbox_run_test",
                "observations": ["observation 2"],
                "extraction_plan": ["repair extractor"],
                "attempted_actions": ["context update 2"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "repeated_extraction_context_updates"
    assert blocked["consecutive_count"] == 2


def test_workflow_guard_points_repeated_context_update_to_existing_plan() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    planned_next_tool = {
        "tool_name": "run_skill_script",
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_apply_patch.py",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": planned_next_tool,
    }
    tool = SimpleNamespace(name="update_extraction_context")
    tool_args = {
        "audit_id": "sandbox_run_test",
        "known_errors": ["chosen_strategy is required"],
        "immediate_goal": "patch serializer",
        "planned_next_tool": planned_next_tool,
    }

    first = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))
    second = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))

    assert first is None
    assert second is not None
    assert second["guardrail"] == "repeated_extraction_context_updates"
    assert second["required_next_tool"] == planned_next_tool
    assert "look at the plan you wrote previously" in second["required_next"].lower()
    assert "call required_next_tool now" in second["required_next"]
    assert "Do not call update_extraction_context again" in second["required_next"]


def test_workflow_guard_requires_planned_next_tool_for_repair_context_update() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "last_repair_target": {
            "file_path": "scripts/sandbox_finalize.py",
            "producer_hint": "output/extractor.py",
            "required_action": "debug_repair_extractor",
            "error": "missing required sandbox protocol outputs: output/page_profile.json",
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "audit_id": "sandbox_run_test",
                "known_errors": ["output/page_profile.json missing"],
                "last_result": {"missing_files": ["output/page_profile.json"]},
                "immediate_goal": "repair extractor",
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "repair_context_requires_planned_next_tool"
    assert "planned_next_tool" in blocked["error"]


def test_workflow_guard_rejects_invalid_repair_scope_update() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "audit_id": "sandbox_run_test",
                "repair_scope": {
                    "status": "ready_to_verify",
                    "objective": "verify extractor repair",
                },
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "repair_scope_verification_required"


def test_workflow_guard_rejects_compound_repair_scope_verification() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "audit_id": "sandbox_run_test",
                "repair_scope": {
                    "status": "ready_to_verify",
                    "objective": "verify extractor repair",
                    "files": ["output/extractor.py"],
                    "verification": "python output/extractor.py && python - <<'PY'\nprint('inspect')\nPY",
                },
                "planned_next_tool": {
                    "tool_name": "run_skill_script",
                    "skill_name": "sandbox-page-analyst",
                    "file_path": "scripts/sandbox_exec.py",
                    "args_must_include": ["python output/extractor.py"],
                },
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "compound_repair_scope_verification_command"
    assert "python output/extractor.py" in blocked["error"]


def test_workflow_guard_enforces_declared_planned_next_tool() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_apply_patch.py",
            "target_paths": ["output/extractor.py"],
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_write_file.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/other.py", "--content", "print('x')"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "next_tool_must_match_session_plan"
    assert blocked["expected"]["file_path"] == "scripts/sandbox_apply_patch.py"

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py", "--old", "a", "--new", "b"],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_bounds_repair_scope_resources_and_patch_targets() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "repair_scope": {
            "status": "patching",
            "objective": "repair extractor card loop",
            "files": ["output/extractor.py"],
            "allowed_resources": ["references/itviec-listing-repair.md"],
            "allowed_inspections": ["output/extractor.py"],
        },
    }

    allowed_resource = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="load_skill_resource"),
            tool_args={
                "skill_name": "sandbox-extraction-debugger",
                "file_path": "references/itviec-listing-repair.md",
            },
            tool_context=tool_context,
        )
    )
    blocked_resource = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="load_skill_resource"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "references/unrelated.md",
            },
            tool_context=tool_context,
        )
    )
    allowed_patch = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py", "--old", "a", "--new", "b"],
            },
            tool_context=tool_context,
        )
    )
    blocked_patch = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/final.json", "--old", "a", "--new", "b"],
            },
            tool_context=tool_context,
        )
    )

    assert allowed_resource is None
    assert blocked_resource is not None
    assert blocked_resource["guardrail"] == "repair_scope_resource_not_allowed"
    assert allowed_patch is None
    assert blocked_patch is not None
    assert blocked_patch["guardrail"] == "repair_scope_patch_target_not_allowed"


def test_workflow_guard_allows_sandbox_read_inside_repair_scope_regardless_of_path() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "repair_scope": {
            "status": "patching",
            "objective": "repair output writer",
            "files": ["output/write_outputs.py"],
            "allowed_inspections": ["output/candidates.json"],
        },
    }

    allowed_output_artifact = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/page_profile.json"],
            },
            tool_context=tool_context,
        )
    )
    allowed_evidence_artifact = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "evidence/index.json"],
            },
            tool_context=tool_context,
        )
    )
    allowed_non_artifact_contract = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "schemas/candidates.schema.json"],
            },
            tool_context=tool_context,
        )
    )

    assert allowed_output_artifact is None
    assert allowed_evidence_artifact is None
    assert allowed_non_artifact_contract is None


def test_workflow_guard_allows_apply_patch_help_inside_repair_scope() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "repair_scope": {
            "status": "patching",
            "objective": "repair extractor",
            "files": ["output/extractor.py"],
        },
        **workflow_contract_state(),
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": ["--help"],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_enforces_repair_scope_verification_command() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "repair_scope": {
            "status": "ready_to_verify",
            "objective": "verify extractor repair",
            "files": ["output/extractor.py"],
            "verification": "python output/extractor.py",
        },
        **workflow_contract_state(),
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "sed -n '1,80p' output/extractor.py"],
            },
            tool_context=tool_context,
        )
    )
    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "repair_scope_verification_command_required"
    assert allowed is None


def test_workflow_guard_accepts_repair_scope_verification_command_alias() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        **workflow_contract_state(),
    }

    update_allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "repair_scope": {
                    "status": "ready_to_verify",
                    "objective": "verify extractor repair",
                    "files": ["output/extractor.py"],
                    "verification_command": "python output/extractor.py",
                },
                "planned_next_tool": {
                    "tool_name": "run_skill_script",
                    "skill_name": "sandbox-page-analyst",
                    "file_path": "scripts/sandbox_exec.py",
                    "args_must_include": ["python output/extractor.py"],
                },
            },
            tool_context=tool_context,
        )
    )

    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]["repair_scope"] = {
        "status": "ready_to_verify",
        "objective": "verify extractor repair",
        "files": ["output/extractor.py"],
        "verification_command": "python output/extractor.py",
    }

    exec_allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
            },
            tool_context=tool_context,
        )
    )

    assert update_allowed is None
    assert exec_allowed is None


def test_workflow_guard_ignores_repair_scope_before_sandbox_starts() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "task_understanding": "extract jobs",
                "final_goal": "validated job extraction",
                "initial_plan": ["start sandbox"],
                "immediate_goal": "start workflow sandbox",
                "repair_scope": {
                    "status": "ready_to_start",
                    "objective": "start sandbox before any repair exists",
                },
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_requires_planned_tool_to_match_ready_repair_scope_verification() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "last_repair_target": {
            "file_path": "scripts/sandbox_finalize.py",
            "producer_hint": "output/extractor.py",
            "required_action": "debug_repair_extractor",
            "error": "count mismatch",
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "audit_id": "sandbox_run_test",
                "known_errors": ["count mismatch"],
                "last_result": {"error": "count mismatch"},
                "immediate_goal": "verify extractor repair",
                "repair_scope": {
                    "status": "ready_to_verify",
                    "objective": "verify extractor repair",
                    "files": ["output/extractor.py"],
                    "verification": "python output/extractor.py",
                },
                "planned_next_tool": {
                    "tool_name": "run_skill_script",
                    "skill_name": "sandbox-page-analyst",
                    "file_path": "scripts/sandbox_exec.py",
                },
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "repair_scope_verification_args_required"


def test_workflow_guard_allows_ready_repair_scope_when_planned_args_include_verification() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "last_repair_target": {
            "file_path": "scripts/sandbox_finalize.py",
            "producer_hint": "output/extractor.py",
            "required_action": "debug_repair_extractor",
            "error": "candidates.crawl must be an object",
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="update_extraction_context"),
            tool_args={
                "audit_id": "sandbox_run_test",
                "known_errors": ["candidates.crawl must be an object"],
                "last_result": {"error": "candidates.crawl must be an object"},
                "immediate_goal": "verify extractor repair",
                "repair_scope": {
                    "status": "ready_to_verify",
                    "objective": "verify extractor repair",
                    "files": ["output/extractor.py"],
                    "verification": "python output/extractor.py",
                },
                "planned_next_tool": {
                    "tool_name": "run_skill_script",
                    "skill_name": "sandbox-page-analyst",
                    "file_path": "scripts/sandbox_exec.py",
                    "args": [
                        "--audit-id",
                        "sandbox_run_test",
                        "--cmd",
                        "python output/extractor.py && python -c \"print('ok')\"",
                    ],
                },
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_allows_sandbox_reads_with_declared_planned_next_tool() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_apply_patch.py",
            "target_paths": ["output/extractor.py"],
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py"],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None
    assert (
        tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]["planned_next_tool"]["file_path"]
        == "scripts/sandbox_apply_patch.py"
    )


def test_workflow_guard_allows_read_only_sandbox_exec_probe_with_declared_planned_next_tool() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_write_file.py",
            "target_paths": ["output/candidates.json"],
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--cmd",
                    "python - <<'PY'\nfrom pathlib import Path\nprint(Path('page.html').read_text()[:100])\nPY",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_still_blocks_sandbox_exec_write_when_plan_declares_different_tool() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_write_file.py",
            "target_paths": ["output/candidates.json"],
        },
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": [
                    "--audit-id",
                    "sandbox_run_test",
                    "--cmd",
                    "python - <<'PY'\nfrom pathlib import Path\nPath('output/candidates.json').write_text('{}')\nPY",
                ],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "next_tool_must_match_session_plan"


def test_workflow_guard_allows_reference_reads_with_declared_planned_next_tool() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_apply_patch.py",
            "target_paths": ["output/extractor.py"],
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="load_skill_resource"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "references/protocol.md",
            },
            tool_context=tool_context,
        )
    )

    assert allowed is None
    assert (
        tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]["planned_next_tool"]["file_path"]
        == "scripts/sandbox_apply_patch.py"
    )


def test_workflow_guard_accepts_functions_prefix_in_planned_tool_name() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "functions.list_skill_resources",
            "skill_name": "sandbox-page-analyst",
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="list_skill_resources"),
            tool_args={"skill_name": "sandbox-page-analyst"},
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_ignores_skill_name_for_direct_planned_tool() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "task_understanding": "extract fixture jobs",
        "final_goal": "validated fixture extraction",
        "initial_plan": ["load fixed fixture into workspace"],
        "planned_next_tool": {
            "tool_name": "functions.load_test_fixture_page_to_workspace",
            "skill_name": "job-listing-scout",
            "file_path": "",
        },
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="load_test_fixture_page_to_workspace"),
            tool_args={"fixture_name": "itviec_ai_engineer_ha_noi"},
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_blocks_repeated_identical_inspection_without_progress() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "mode": "workflow",
        "status": "running",
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="list_skill_resources"),
            tool_args={"skill_name": "sandbox-page-analyst"},
            tool_context=tool_context,
            result={"status": "success"},
        )
    )
    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="list_skill_resources"),
            tool_args={"skill_name": "sandbox-page-analyst"},
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["guardrail"] == "same_inspection_without_progress"
    assert "load bounded evidence" in blocked["required_next"]
    assert "accountable protocol outputs" in blocked["required_next"]


def test_workflow_guard_allows_same_inspection_after_progress_action() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "mode": "workflow",
        "status": "running",
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="list_skill_resources"),
            tool_args={"skill_name": "sandbox-page-analyst"},
            tool_context=tool_context,
            result={"status": "success"},
        )
    )
    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_exec.py",
                "args": ["--audit-id", "sandbox_run_test", "--cmd", "true"],
            },
            tool_context=tool_context,
            result={"status": "success", "stdout": '{"status":"success","command_index":1}'},
        )
    )
    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="list_skill_resources"),
            tool_args={"skill_name": "sandbox-page-analyst"},
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_clears_planned_next_tool_after_matching_success() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_apply_patch.py",
            "target_paths": ["output/extractor.py"],
        },
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py", "--old", "a", "--new", "b"],
            },
            tool_context=tool_context,
            result={"status": "success", "stdout": '{"status":"success"}'},
        )
    )

    context = tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert "planned_next_tool" not in context


def test_workflow_guard_clears_planned_next_tool_after_matching_error() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = {
        "updated": True,
        "planned_next_tool": {
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_apply_patch.py",
            "target_paths": ["output/extractor.py"],
        },
    }

    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_apply_patch.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py", "--old", "a", "--new", "b"],
            },
            tool_context=tool_context,
            result={
                "status": "success",
                "stdout": '{"status":"error","error_type":"patch_context_mismatch","error":"expected one match, found 0"}',
            },
        )
    )

    context = tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert "planned_next_tool" not in context


def test_workflow_guard_allows_resource_loading_between_context_updates() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    context_tool = SimpleNamespace(name="update_extraction_context")

    assert (
        asyncio.run(
            plugin.before_tool_callback(
                tool=context_tool,
                tool_args={
                    "audit_id": "sandbox_run_test",
                    "observations": ["inline write rejected"],
                    "extraction_plan": ["patch output/extractor.py"],
                    "attempted_actions": ["recorded rejected write"],
                },
                tool_context=tool_context,
            )
        )
        is None
    )

    assert (
        asyncio.run(
            plugin.after_tool_callback(
                tool=SimpleNamespace(name="load_skill_resource"),
                tool_args={"skill_name": "sandbox-page-analyst", "file_path": "references/protocol.md"},
                tool_context=tool_context,
                result={"status": "success"},
            )
        )
        is None
    )

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=context_tool,
            tool_args={
                "audit_id": "sandbox_run_test",
                "observations": ["still need to patch extractor"],
                "extraction_plan": ["patch output/extractor.py"],
                "attempted_actions": ["loaded protocol but did not patch"],
            },
            tool_context=tool_context,
        )
    )

    assert blocked is None


def test_workflow_guard_resets_context_update_guard_after_state_changing_tool() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    context_tool = SimpleNamespace(name="update_extraction_context")
    first_args = {
        "audit_id": "sandbox_run_test",
        "observations": ["20 job cards, extractor emitted 117"],
        "extraction_plan": ["repair card selector"],
    }

    assert asyncio.run(plugin.before_tool_callback(tool=context_tool, tool_args=first_args, tool_context=tool_context)) is None
    assert EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY in tool_context.state

    assert (
        asyncio.run(
            plugin.after_tool_callback(
                tool=SimpleNamespace(name="run_skill_script"),
                tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"},
                tool_context=tool_context,
                result={"status": "success", "stdout": '{"status":"success","audit_id":"sandbox_run_test","command_index":2}'},
            )
        )
        is None
    )
    assert EXTRACTION_CONTEXT_UPDATE_GUARD_STATE_KEY not in tool_context.state

    assert asyncio.run(plugin.before_tool_callback(tool=context_tool, tool_args=first_args, tool_context=tool_context)) is None


def test_workflow_guard_blocks_sandbox_tools_after_guardrail_terminal_state() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "guardrail_triggered",
        "mode": "workflow",
        "guardrail": "workflow_sandbox_tool_budget_exceeded",
    }

    blocked = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="run_skill_script"),
            tool_args={"skill_name": "sandbox-page-analyst", "file_path": "scripts/sandbox_exec.py"},
            tool_context=tool_context,
        )
    )

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["error_type"] == "sandbox_guardrail_terminal"
    assert blocked["guardrail"] == "workflow_sandbox_tool_budget_exceeded"
    assert "No further sandbox" in blocked["error"]
    assert "Stop the sandbox workflow" in blocked["required_next"]


def test_workflow_guard_allows_non_sandbox_tools_after_guardrail_terminal_state() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "guardrail_triggered",
        "mode": "workflow",
        "guardrail": "workflow_sandbox_tool_budget_exceeded",
    }

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="list_skills"),
            tool_args={},
            tool_context=tool_context,
        )
    )

    assert allowed is None


def test_workflow_guard_triggers_terminal_state_after_sandbox_tool_budget() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_exec.py",
        "args": ["--audit-id", "sandbox_run_test", "--cmd", "python - <<'PY'\nprint('probe')\nPY"],
    }

    for _ in range(20):
        assert asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context)) is None

    blocked = asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context))

    assert blocked is not None
    assert blocked["status"] == "error"
    assert blocked["guardrail"] == "workflow_sandbox_tool_budget_exceeded"
    assert blocked["tool_call_count"] == 21
    assert blocked["max_tool_calls"] == 20
    assert tool_context.state[SANDBOX_TOOL_BUDGET_STATE_KEY]["sandbox_run_test"] == 21
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "guardrail_triggered"


def test_workflow_tool_budget_does_not_count_session_notebook_updates() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool = SimpleNamespace(name="update_extraction_context")
    tool_args = {"status": "in_progress"}

    assert asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context)) is None

    assert SANDBOX_TOOL_BUDGET_STATE_KEY not in tool_context.state
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"


def test_workflow_tool_budget_does_not_count_sandbox_script_help() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool = SimpleNamespace(name="run_skill_script")
    tool_args = {
        "skill_name": "sandbox-page-analyst",
        "file_path": "scripts/sandbox_exec.py",
        "args": ["-h"],
    }

    for _ in range(25):
        assert asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context)) is None

    assert SANDBOX_TOOL_BUDGET_STATE_KEY not in tool_context.state
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"


def test_workflow_tool_budget_does_not_count_sandbox_inspection_tools() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool = SimpleNamespace(name="run_skill_script")
    inspection_calls = [
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_read.py",
            "args": ["--audit-id", "sandbox_run_test", "--path", "output/candidates.json"],
        },
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_progress.py",
            "args": ["--audit-id", "sandbox_run_test"],
        },
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/validate_outputs.py",
            "args": ["--audit-id", "sandbox_run_test"],
        },
    ]

    for _ in range(25):
        for tool_args in inspection_calls:
            assert asyncio.run(plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context)) is None

    assert SANDBOX_TOOL_BUDGET_STATE_KEY not in tool_context.state
    assert tool_context.state[ACTIVE_SANDBOX_STATE_KEY]["status"] == "running"


def test_workflow_tool_budget_still_counts_sandbox_mutation_and_execution_tools() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
    }
    tool_context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = workflow_contract_state()
    tool = SimpleNamespace(name="run_skill_script")
    counted_calls = [
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_start.py",
            "args": ["--mode", "workflow"],
        },
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_write_file.py",
            "args": [
                "--audit-id",
                "sandbox_run_test",
                "--path",
                "output/extractor.py",
                "--content",
                protocol_producer_source(),
            ],
        },
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_exec.py",
            "args": ["--audit-id", "sandbox_run_test", "--cmd", "python output/extractor.py"],
        },
        {
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_finalize.py",
            "args": ["--audit-id", "sandbox_run_test"],
        },
    ]

    for index in range(20):
        assert (
            asyncio.run(
                plugin.before_tool_callback(
                    tool=tool,
                    tool_args=counted_calls[index % len(counted_calls)],
                    tool_context=tool_context,
                )
            )
            is None
        )

    blocked = asyncio.run(
        plugin.before_tool_callback(tool=tool, tool_args=counted_calls[0], tool_context=tool_context)
    )

    assert blocked is not None
    assert blocked["guardrail"] == "workflow_sandbox_tool_budget_exceeded"
    assert tool_context.state[SANDBOX_TOOL_BUDGET_STATE_KEY]["sandbox_run_test"] == 21


def test_workflow_guard_allows_valid_persistence_payload_without_active_sandbox() -> None:
    plugin = SandboxWorkflowGuardPlugin()

    allowed = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="persist_sandbox_job_extraction"),
            tool_args={
                "extraction": {
                    "result": {
                        "jobs": [
                            {
                                "title": "AI Engineer",
                                "company_name": "Acme",
                                "job_url": "https://example.com/jobs/ai",
                            }
                        ]
                    }
                }
            },
            tool_context=FakeToolContext(),
        )
    )

    assert allowed is None


def test_workflow_guard_injects_runtime_instruction_for_unfinalized_sandbox() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_start.py",
                            "stdout": '{"status":"running","audit_id":"sandbox_run_test"}',
                        },
                    )
                ],
            ),
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_exec.py",
                            "stdout": '{"status":"success","audit_id":"sandbox_run_test","command_index":5}',
                        },
                    )
                ],
            ),
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(), llm_request=llm_request))

    assert result is None
    guard_text = llm_request.contents[-1].parts[0].text
    assert guard_text.startswith("<RUNTIME_SANDBOX_GUARD>")
    assert guard_text.endswith("</RUNTIME_SANDBOX_GUARD>")
    assert "priority: hard operational constraint." in guard_text
    assert "write or repair accountable protocol files or supporting scripts" in guard_text
    assert "Do not produce a final text response" in guard_text
    assert "runtime will block premature text" not in guard_text


def test_workflow_guard_allows_final_blocker_for_guardrail_triggered_sandbox() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    state = {
        ACTIVE_SANDBOX_STATE_KEY: {
            "audit_id": "sandbox_run_test",
            "status": "guardrail_triggered",
            "guardrail": "workflow_sandbox_tool_budget_exceeded",
        }
    }
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_start.py",
                            "stdout": '{"status":"running","audit_id":"sandbox_run_test","mode":"workflow"}',
                        },
                    )
                ],
            )
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(state=state), llm_request=llm_request))

    assert result is None
    guard_text = llm_request.contents[-1].parts[0].text
    assert "terminal because guardrail workflow_sandbox_tool_budget_exceeded was triggered" in guard_text
    assert "produce a compact blocker response" in guard_text
    assert "Do not produce a final text response" not in guard_text


def test_workflow_guard_blocks_repeated_same_file_reads_during_repair() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    tool_context = FakeToolContext()
    tool_context.state[ACTIVE_SANDBOX_STATE_KEY] = {
        "audit_id": "sandbox_run_test",
        "status": "running",
        "mode": "workflow",
        "last_repair_target": {
            "file_path": "scripts/sandbox_finalize.py",
            "producer_hint": "output/extractor.py",
            "required_action": "debug_repair_extractor",
        },
    }
    tool = SimpleNamespace(name="run_skill_script")

    first = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py", "--max-chars", "2000"],
            },
            tool_context=tool_context,
        )
    )
    second = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py", "--max-chars", "4000"],
            },
            tool_context=tool_context,
        )
    )
    third = asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args={
                "skill_name": "sandbox-page-analyst",
                "file_path": "scripts/sandbox_read.py",
                "args": ["--audit-id", "sandbox_run_test", "--path", "output/extractor.py", "--max-chars", "8000"],
            },
            tool_context=tool_context,
        )
    )

    assert first is None
    assert second is None
    assert third is not None
    assert third["error_type"] == "repeated_sandbox_read_policy"
    assert "patch the relevant helper" in third["required_next"]
    assert "accountable protocol output" in third["required_next"]


def test_workflow_guard_ignores_diagnostic_sandbox_for_runtime_instruction() -> None:
    plugin = SandboxWorkflowGuardPlugin()
    llm_request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name="run_skill_script",
                        response={
                            "skill_name": "sandbox-page-analyst",
                            "file_path": "scripts/sandbox_start.py",
                            "stdout": '{"status":"running","audit_id":"sandbox_run_test","mode":"diagnostic"}',
                        },
                    )
                ],
            )
        ]
    )

    result = asyncio.run(plugin.before_model_callback(callback_context=SimpleNamespace(), llm_request=llm_request))

    assert result is None
    injected_texts = [content.parts[0].text for content in llm_request.contents if getattr(content.parts[0], "text", None)]
    assert len(llm_request.contents) == 2
    assert injected_texts[0].startswith("<LATEST_TOOL_RESULT>")
    assert not any(text.startswith("<RUNTIME_SANDBOX_GUARD>") for text in injected_texts)
