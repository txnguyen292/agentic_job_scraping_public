from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox_page_analyst.openai_agent import (
    build_sandbox_capabilities,
    load_sandbox_skill_metadata,
    sandbox_instructions,
)
from sandbox_page_analyst.runtime import (
    NoNetworkDockerClient,
    SandboxAuditWriter,
    SandboxAgentResult,
    SandboxJobExtractionResult,
    SandboxPolicy,
    validate_sandbox_protocol_outputs,
    run_generic_sandbox_agent,
    validate_sandbox_agent_result,
    _consume_sandbox_stream,
    _openai_trace_id,
    _sandbox_stream_event_payload,
)


class FakeContainers:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append({"args": args, "kwargs": kwargs})
        return {"created": True}


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.version = "fake"


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def write_trace(self, event: str, payload: dict[str, object] | None = None) -> None:
        self.records.append({"event": event, **(payload or {})})


class FakeStreamingResult:
    def __init__(self, events: list[object], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.current_turn = 3
        self.max_turns = 8
        self.is_complete = False

    async def stream_events(self):
        for event in self.events:
            yield event
        if self.error:
            raise self.error
        self.is_complete = True


class MaxTurnsExceeded(Exception):
    pass


def test_sandbox_policy_defaults_to_mini_high_reasoning_no_network() -> None:
    policy = SandboxPolicy()

    assert policy.model == "gpt-5.4-mini"
    assert policy.reasoning_effort == "high"
    assert policy.allow_llm_calls is True
    assert policy.persist_artifacts is True
    assert policy.network == "disabled"
    assert policy.debug_audit is False
    assert policy.validate_before_return is True
    assert policy.validate_before_persist is True
    assert policy.skill_name == "sandbox-page-analyst"
    assert policy.use_sandbox_skill is True
    assert policy.docker_image == "job-scraper-sandbox:py313"


def test_sandbox_policy_rejects_disabling_llm_calls() -> None:
    with pytest.raises(ValueError):
        SandboxPolicy.model_validate({"allow_llm_calls": False})


def test_sandbox_policy_rejects_disabling_artifact_persistence() -> None:
    with pytest.raises(ValueError):
        SandboxPolicy.model_validate({"persist_artifacts": False})


def test_sandbox_instructions_distinguish_llm_calls_from_container_network() -> None:
    instructions = sandbox_instructions("job_extraction", SandboxPolicy(network="disabled"))

    assert "Host-mediated LLM calls are allowed" in instructions
    assert "sandbox terminal/container egress" in instructions
    assert "not host-mediated LLM calls" in instructions


def test_sandbox_page_analyst_skill_metadata_loads() -> None:
    metadata = load_sandbox_skill_metadata()

    assert metadata["name"] == "sandbox-page-analyst"
    assert "job listing" in metadata["description"].lower()


def test_build_sandbox_capabilities_includes_sdk_skills_capability() -> None:
    capabilities = build_sandbox_capabilities(SandboxPolicy())
    capability_names = {capability.__class__.__name__ for capability in capabilities}

    assert {"Shell", "Filesystem", "Compaction", "Skills"} <= capability_names


def test_no_network_docker_client_injects_strict_network_kwargs() -> None:
    raw_client = FakeDockerClient()
    safe_client = NoNetworkDockerClient(raw_client)

    result = safe_client.containers.create(image="python:3.13-slim", network_mode="bridge")

    assert result == {"created": True}
    assert raw_client.version == safe_client.version
    kwargs = raw_client.containers.calls[0]["kwargs"]
    assert kwargs["network_disabled"] is True
    assert kwargs["network_mode"] == "none"


def test_audit_ref_includes_openai_trace_lookup_metadata(tmp_path: Path) -> None:
    audit = SandboxAuditWriter(root_dir=tmp_path, audit_id="sandbox_run_20260427_024111_98340c31")

    ref = audit.audit_ref()

    assert ref.openai_trace_id == _openai_trace_id(audit.audit_id)
    assert ref.openai_trace_id.startswith("trace_")
    assert len(ref.openai_trace_id) == len("trace_") + 32
    assert ref.openai_trace_workflow == "job_scraper.sandbox_page_analysis"
    assert ref.openai_trace_group_id == audit.audit_id
    assert "Logs > Traces" in ref.openai_trace_dashboard


def test_sandbox_stream_event_payload_sanitizes_run_items() -> None:
    event = SimpleNamespace(
        type="run_item_stream_event",
        name="tool_output",
        item=SimpleNamespace(
            type="tool_call_output_item",
            raw_item=SimpleNamespace(type="function_call_output", call_id="call_123", name="shell"),
            output="x" * 2_000,
        ),
    )
    result = SimpleNamespace(current_turn=2, max_turns=8, is_complete=False)

    payload = _sandbox_stream_event_payload(event, result)

    assert payload["stream_event_type"] == "run_item_stream_event"
    assert payload["name"] == "tool_output"
    assert payload["current_turn"] == 2
    assert payload["item"]["type"] == "tool_call_output_item"
    assert payload["item"]["call_id"] == "call_123"
    assert len(payload["item"]["output_preview"]) <= 500


def test_consume_sandbox_stream_writes_progress_events() -> None:
    audit = FakeAudit()
    result = FakeStreamingResult(
        [
            SimpleNamespace(
                type="agent_updated_stream_event",
                new_agent=SimpleNamespace(name="sandbox_page_analyst"),
            ),
            SimpleNamespace(
                type="run_item_stream_event",
                name="reasoning_item_created",
                item=SimpleNamespace(type="reasoning_item"),
            ),
            SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.completed", response_id="resp_123"),
            ),
        ]
    )

    asyncio.run(_consume_sandbox_stream(result, audit, SandboxPolicy()))

    assert [record["event"] for record in audit.records] == [
        "sandbox_stream_event",
        "sandbox_stream_event",
        "sandbox_stream_event",
    ]
    assert audit.records[0]["agent_name"] == "sandbox_page_analyst"
    assert audit.records[1]["name"] == "reasoning_item_created"
    assert audit.records[2]["raw_event_type"] == "response.completed"


def test_consume_sandbox_stream_skips_raw_delta_events() -> None:
    audit = FakeAudit()
    result = FakeStreamingResult(
        [
            SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.output_text.delta")),
            SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.function_call_arguments.delta"),
            ),
            SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.completed")),
        ]
    )

    asyncio.run(_consume_sandbox_stream(result, audit, SandboxPolicy()))

    assert len(audit.records) == 1
    assert audit.records[0]["raw_event_type"] == "response.completed"


def test_consume_sandbox_stream_records_max_turns_error() -> None:
    audit = FakeAudit()
    result = FakeStreamingResult(
        [SimpleNamespace(type="run_item_stream_event", name="tool_called", item=SimpleNamespace(type="tool_call_item"))],
        error=MaxTurnsExceeded("Max turns (8) exceeded"),
    )

    with pytest.raises(MaxTurnsExceeded):
        asyncio.run(_consume_sandbox_stream(result, audit, SandboxPolicy()))

    assert audit.records[0]["event"] == "sandbox_stream_event"
    assert audit.records[1]["event"] == "sandbox_stream_error"
    assert audit.records[1]["error"] == "Max turns exceeded"
    assert audit.records[1]["current_turn"] == 3


def test_sandbox_job_extraction_requires_title_and_job_url() -> None:
    with pytest.raises(ValueError, match="job_url"):
        SandboxJobExtractionResult.model_validate(
            {
                "jobs": [
                    {
                        "title": "Machine Learning Engineer",
                        "company_name": "Acme",
                        "job_url": "",
                    }
                ]
            }
        )


def test_validate_sandbox_agent_result_rejects_invalid_job_extraction() -> None:
    payload = {
        "status": "success",
        "output_schema": "job_extraction",
        "result": {"jobs": [{"title": "", "job_url": "https://example.com/jobs/ml"}]},
    }

    with pytest.raises(ValueError, match="title"):
        validate_sandbox_agent_result(payload)


def test_validate_sandbox_agent_result_accepts_valid_job_extraction() -> None:
    payload = {
        "status": "success",
        "output_schema": "job_extraction",
        "summary": "Found one role.",
        "result": {
            "source": {"source_url": "https://example.com/jobs"},
            "jobs": [
                {
                    "title": "Machine Learning Engineer",
                    "company_name": "Acme",
                    "job_url": "https://example.com/jobs/ml",
                    "confidence": 0.9,
                }
            ],
            "crawl": {"discovered_count": 1, "candidate_count": 1, "relevant_count": 1},
        },
    }

    result = validate_sandbox_agent_result(payload)

    assert isinstance(result, SandboxAgentResult)
    assert result.result["jobs"][0]["title"] == "Machine Learning Engineer"


def test_validate_sandbox_agent_result_requires_protocol_when_enabled() -> None:
    payload = {
        "status": "success",
        "output_schema": "job_extraction",
        "summary": "Found one role.",
        "result": {
            "source": {"source_url": "https://example.com/jobs"},
            "jobs": [
                {
                    "title": "Machine Learning Engineer",
                    "company_name": "Acme",
                    "job_url": "https://example.com/jobs/ml",
                }
            ],
        },
    }

    with pytest.raises(ValueError, match="protocol"):
        validate_sandbox_agent_result(payload, policy=SandboxPolicy(require_protocol_outputs=True))


def test_validate_sandbox_protocol_outputs_accepts_required_files() -> None:
    protocol = validate_sandbox_protocol_outputs(
        {
            "page_profile": {"path": "output/page_profile.json", "sha256": "a" * 64},
            "extraction_strategy": {"path": "output/extraction_strategy.json", "sha256": "b" * 64},
            "candidates": {"path": "output/candidates.json", "sha256": "c" * 64},
            "validation": {"path": "output/validation.json", "sha256": "d" * 64},
            "valid": True,
            "warnings": [],
        }
    )

    assert protocol.valid is True
    assert protocol.page_profile.path == "output/page_profile.json"


def test_audit_writer_always_persists_raw_scratch_artifacts(tmp_path: Path) -> None:
    writer = SandboxAuditWriter(root_dir=tmp_path, policy=SandboxPolicy(debug_audit=False), audit_id="run_1")
    writer.start(task="extract jobs", variables={"page_id": "page_1"}, workspace_files=[])
    writer.write_trace("sandbox_started", {"network": "disabled"})
    writer.write_final({"status": "success", "output_schema": "generic"})
    artifacts = writer.export_scratch_artifacts({"scratch.py": "print('hi')"})

    run_dir = tmp_path / "run_1"
    assert (run_dir / "policy.json").exists()
    assert (run_dir / "inputs.json").exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "final.json").exists()
    raw_file = run_dir / "raw" / "scratch.py"
    assert raw_file.read_text(encoding="utf-8") == "print('hi')"
    assert artifacts == [str(raw_file)]


def test_audit_writer_records_artifact_persistence_policy(tmp_path: Path) -> None:
    writer = SandboxAuditWriter(root_dir=tmp_path, policy=SandboxPolicy(debug_audit=True), audit_id="run_debug")
    writer.start(task="extract jobs", variables={}, workspace_files=[])
    artifacts = writer.export_scratch_artifacts({"scratch.py": "print('hi')"})

    raw_file = tmp_path / "run_debug" / "raw" / "scratch.py"
    assert raw_file.read_text(encoding="utf-8") == "print('hi')"
    assert artifacts == [str(raw_file)]

    policy_payload = json.loads((tmp_path / "run_debug" / "policy.json").read_text(encoding="utf-8"))
    assert policy_payload["debug_audit"] is True
    assert policy_payload["allow_llm_calls"] is True
    assert policy_payload["persist_artifacts"] is True


def test_run_generic_sandbox_agent_mocked_e2e_writes_audit_and_usage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = json.loads(Path("tests/fixtures/static_job_board.expected.json").read_text(encoding="utf-8"))
    page_file = tmp_path / "page.html"
    page_file.write_text("<html><article class='job'>Machine Learning Engineer</article></html>", encoding="utf-8")

    async def fake_worker(**kwargs):
        return (
            {
                "status": "success",
                "output_schema": "job_extraction",
                "summary": "Found fixture jobs.",
                "protocol": _valid_protocol_payload(),
                "result": expected,
            },
            {"usage": None, "completion_time_ms": 123},
        )

    monkeypatch.setattr("sandbox_page_analyst.runtime._run_openai_sandbox_worker", fake_worker)

    result = run_generic_sandbox_agent(
        task="Extract jobs",
        variables={"source_url": "https://example.com/jobs"},
        workspace_files=[{"source_path": str(page_file), "sandbox_path": "page.html"}],
        output_schema="job_extraction",
        audit_root=tmp_path / "audits",
    )

    assert result["status"] == "success"
    assert result["result"]["jobs"][0]["title"] == "Machine Learning Engineer"
    assert result["protocol"]["valid"] is True
    audit = result["audit"]
    assert Path(audit["final_output_artifact"]).exists()
    assert Path(audit["usage_artifact"]).exists()
    assert not (Path(audit["final_output_artifact"]).parent / "raw").exists()
    usage_payload = json.loads(Path(audit["usage_artifact"]).read_text(encoding="utf-8"))
    assert usage_payload["models"]["sandbox_llm_calls_allowed"] is True
    assert usage_payload["models"]["sandbox_artifacts_persisted"] is True


def test_run_generic_sandbox_agent_can_be_called_inside_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page_file = tmp_path / "page.html"
    page_file.write_text("<html><article>Machine Learning Engineer</article></html>", encoding="utf-8")
    calling_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []

    async def fake_worker(**kwargs):
        worker_thread_ids.append(threading.get_ident())
        return (
            {
                "status": "success",
                "output_schema": "job_extraction",
                "summary": "No jobs in mocked loop test.",
                "protocol": _valid_protocol_payload(),
                "result": {
                    "source": {"source_url": "https://example.com/jobs"},
                    "jobs": [],
                    "crawl": {"discovered_count": 0, "candidate_count": 0, "relevant_count": 0},
                },
            },
            {"usage": None, "completion_time_ms": 7},
        )

    monkeypatch.setattr("sandbox_page_analyst.runtime._run_openai_sandbox_worker", fake_worker)

    async def call_from_running_loop() -> dict[str, object]:
        return run_generic_sandbox_agent(
            task="Extract jobs",
            variables={"source_url": "https://example.com/jobs"},
            workspace_files=[{"source_path": str(page_file), "sandbox_path": "page.html"}],
            output_schema="job_extraction",
            audit_root=tmp_path / "audits",
        )

    result = asyncio.run(call_from_running_loop())

    assert result["status"] == "success"
    assert worker_thread_ids
    assert worker_thread_ids[0] != calling_thread_id


def _valid_protocol_payload() -> dict[str, object]:
    return {
        "page_profile": {"path": "output/page_profile.json", "sha256": "a" * 64},
        "extraction_strategy": {"path": "output/extraction_strategy.json", "sha256": "b" * 64},
        "candidates": {"path": "output/candidates.json", "sha256": "c" * 64},
        "validation": {"path": "output/validation.json", "sha256": "d" * 64},
        "valid": True,
        "warnings": [],
    }
