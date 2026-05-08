from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from job_scraper.sandbox_image import DEFAULT_SANDBOX_IMAGE
from sandbox_page_analyst.openai_agent import build_sandbox_page_analyst_agent


DEFAULT_SANDBOX_AUDIT_ROOT = Path("data/sandbox_runs")
OPENAI_TRACE_WORKFLOW_NAME = "job_scraper.sandbox_page_analysis"
OPENAI_TRACE_DASHBOARD_HINT = "OpenAI Platform > Logs > Traces"
REQUIRED_PROTOCOL_OUTPUTS = (
    "page_profile",
    "extraction_strategy",
    "candidates",
    "validation",
)


class SandboxWorkspaceFile(BaseModel):
    source_path: str
    sandbox_path: str


class SandboxPolicy(BaseModel):
    timeout_seconds: int = 120
    max_turns: int = 8
    max_final_output_chars: int = 20_000
    model: str = "gpt-5.4-mini"
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "high"
    allow_llm_calls: Literal[True] = True
    persist_artifacts: Literal[True] = True
    network: Literal["disabled", "default"] = "disabled"
    docker_image: str = DEFAULT_SANDBOX_IMAGE
    debug_audit: bool = False
    require_protocol_outputs: bool = True
    validate_before_return: bool = True
    validate_before_persist: bool = True
    use_sandbox_skill: bool = True
    skill_name: str = "sandbox-page-analyst"
    skills_path: str = ".agents"


class SandboxAuditRef(BaseModel):
    audit_id: str
    policy_artifact: str
    inputs_artifact: str
    trace_artifact: str
    final_output_artifact: str
    usage_artifact: str = ""
    openai_trace_id: str = ""
    openai_trace_workflow: str = ""
    openai_trace_group_id: str = ""
    openai_trace_dashboard: str = ""
    warnings: list[str] = Field(default_factory=list)


class SandboxAgentResult(BaseModel):
    status: Literal["success", "error", "needs_review"]
    output_schema: str = "generic"
    result: dict[str, Any] = Field(default_factory=dict)
    protocol: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    audit: SandboxAuditRef | None = None
    artifacts: list[str] = Field(default_factory=list)
    error: str = ""


class SandboxEvidence(BaseModel):
    file: str = ""
    locator: str = ""
    text: str = Field(default="", max_length=500)


class SandboxExtractedJob(BaseModel):
    title: str
    company_name: str = ""
    job_url: str
    location_raw: str = ""
    employment_type: str = ""
    posted_at: str = ""
    salary_raw: str = ""
    description_text: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list)
    relevance_reason: str = ""
    confidence: float = 0.0
    evidence: list[SandboxEvidence] = Field(default_factory=list)

    @field_validator("title", "job_url")
    @classmethod
    def _require_non_empty(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} is required")
        return cleaned


class SandboxJobExtractionResult(BaseModel):
    source: dict[str, Any] = Field(default_factory=dict)
    jobs: list[SandboxExtractedJob] = Field(default_factory=list)
    selectors: dict[str, str] = Field(default_factory=dict)
    crawl: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SandboxProtocolFileRef(BaseModel):
    path: str
    sha256: str = ""

    @field_validator("path")
    @classmethod
    def _require_output_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("output/"):
            raise ValueError("protocol output path must start with output/")
        return cleaned


class SandboxProtocolOutputs(BaseModel):
    page_profile: SandboxProtocolFileRef
    extraction_strategy: SandboxProtocolFileRef
    candidates: SandboxProtocolFileRef
    validation: SandboxProtocolFileRef
    valid: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_valid_protocol(self) -> "SandboxProtocolOutputs":
        if not self.valid:
            raise ValueError("sandbox protocol validation did not pass")
        return self


class NoNetworkContainerCollection:
    """Proxy docker-py container creation to force no-network mode."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["network_disabled"] = True
        kwargs["network_mode"] = "none"
        return self._inner.create(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class NoNetworkDockerClient:
    """Thin docker client proxy used because the SDK options do not expose network_mode."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.containers = NoNetworkContainerCollection(inner.containers)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SandboxAuditWriter:
    def __init__(
        self,
        root_dir: str | Path = DEFAULT_SANDBOX_AUDIT_ROOT,
        policy: SandboxPolicy | None = None,
        audit_id: str | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.policy = policy or SandboxPolicy()
        self.audit_id = audit_id or f"sandbox_run_{_utc_compact()}_{uuid.uuid4().hex[:8]}"
        self.run_dir = self.root_dir / self.audit_id
        self.policy_path = self.run_dir / "policy.json"
        self.inputs_path = self.run_dir / "inputs.json"
        self.trace_path = self.run_dir / "trace.jsonl"
        self.final_path = self.run_dir / "final.json"
        self.usage_path = self.run_dir / "usage.json"
        self.openai_trace_id = _openai_trace_id(self.audit_id)
        self.openai_trace_workflow = OPENAI_TRACE_WORKFLOW_NAME

    def start(
        self,
        task: str,
        variables: dict[str, Any],
        workspace_files: list[SandboxWorkspaceFile | dict[str, Any]],
    ) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.policy_path, self.policy.model_dump(mode="json"))
        _write_json(
            self.inputs_path,
            {
                "task_sha256": _sha256_text(task),
                "variables_sha256": _sha256_json(variables),
                "workspace_files": [self._workspace_file_record(item) for item in workspace_files],
            },
        )
        self.trace_path.touch()

    def write_trace(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        trace_payload = {
            "ts": _utc_now(),
            "event": event,
            **(payload or {}),
        }
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace_payload, ensure_ascii=True, sort_keys=True) + "\n")

    def write_final(self, payload: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.final_path, payload)

    def write_usage(self, payload: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.usage_path, payload)

    def export_scratch_artifacts(self, scratch_files: dict[str, str | bytes]) -> list[str]:
        raw_dir = self.run_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[str] = []
        for raw_name, content in scratch_files.items():
            safe_name = Path(raw_name).name
            target = raw_dir / safe_name
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
            artifacts.append(str(target))
        return artifacts

    def audit_ref(self, warnings: list[str] | None = None) -> SandboxAuditRef:
        return SandboxAuditRef(
            audit_id=self.audit_id,
            policy_artifact=str(self.policy_path),
            inputs_artifact=str(self.inputs_path),
            trace_artifact=str(self.trace_path),
            final_output_artifact=str(self.final_path),
            usage_artifact=str(self.usage_path) if self.usage_path.exists() else "",
            openai_trace_id=self.openai_trace_id,
            openai_trace_workflow=self.openai_trace_workflow,
            openai_trace_group_id=self.audit_id,
            openai_trace_dashboard=OPENAI_TRACE_DASHBOARD_HINT,
            warnings=warnings or [],
        )

    def _workspace_file_record(self, item: SandboxWorkspaceFile | dict[str, Any]) -> dict[str, Any]:
        workspace_file = item if isinstance(item, SandboxWorkspaceFile) else SandboxWorkspaceFile.model_validate(item)
        source_path = Path(workspace_file.source_path)
        record: dict[str, Any] = workspace_file.model_dump(mode="json")
        if source_path.exists() and source_path.is_file():
            record["source_sha256"] = _sha256_file(source_path)
            record["source_bytes"] = source_path.stat().st_size
        return record


def validate_job_extraction_payload(payload: dict[str, Any]) -> SandboxJobExtractionResult:
    return SandboxJobExtractionResult.model_validate(payload)


def validate_sandbox_protocol_outputs(payload: dict[str, Any]) -> SandboxProtocolOutputs:
    return SandboxProtocolOutputs.model_validate(payload)


def validate_sandbox_agent_result(
    payload: dict[str, Any],
    policy: SandboxPolicy | None = None,
) -> SandboxAgentResult:
    effective_policy = policy or SandboxPolicy(require_protocol_outputs=False)
    result = SandboxAgentResult.model_validate(payload)
    if result.status == "success" and effective_policy.require_protocol_outputs:
        try:
            protocol = validate_sandbox_protocol_outputs(result.protocol)
        except ValueError as exc:
            raise ValueError(f"sandbox protocol outputs are invalid: {exc}") from exc
        result.protocol = protocol.model_dump(mode="json")
    if result.status == "success" and result.output_schema == "job_extraction":
        extraction = validate_job_extraction_payload(result.result)
        result.result = extraction.model_dump(mode="json")
    return result


def run_generic_sandbox_agent(
    task: str,
    variables: dict[str, Any] | None = None,
    workspace_files: list[dict[str, Any]] | None = None,
    output_schema: str = "generic",
    policy: dict[str, Any] | SandboxPolicy | None = None,
    audit_root: str | Path = DEFAULT_SANDBOX_AUDIT_ROOT,
) -> dict[str, Any]:
    """Run the OpenAI SDK sandbox worker and return only a validated compact result."""
    sandbox_policy = policy if isinstance(policy, SandboxPolicy) else SandboxPolicy.model_validate(policy or {})
    normalized_files = [SandboxWorkspaceFile.model_validate(item) for item in workspace_files or []]
    variables_payload = variables or {}
    audit = SandboxAuditWriter(root_dir=audit_root, policy=sandbox_policy)
    audit.start(task=task, variables=variables_payload, workspace_files=normalized_files)

    try:
        raw_result, usage = _run_sandbox_worker_in_thread(
            task=task,
            variables=variables_payload,
            workspace_files=normalized_files,
            output_schema=output_schema,
            policy=sandbox_policy,
            audit=audit,
        )
        raw_result.setdefault("output_schema", output_schema)
        raw_result["audit"] = audit.audit_ref().model_dump(mode="json")
        if sandbox_policy.validate_before_return:
            result = validate_sandbox_agent_result(raw_result, policy=sandbox_policy)
        else:
            result = SandboxAgentResult.model_validate(raw_result)

        serialized = result.model_dump(mode="json")
        _enforce_final_output_limit(serialized, sandbox_policy.max_final_output_chars)
        audit.write_final(serialized)
        audit.write_usage(_usage_payload(usage, sandbox_policy, audit.audit_id))
        serialized["audit"] = audit.audit_ref().model_dump(mode="json")
        return serialized
    except Exception as exc:
        audit.write_trace("sandbox_error", {"error": str(exc)[:1000]})
        error_payload = SandboxAgentResult(
            status="error",
            output_schema=output_schema,
            summary="Sandbox worker failed before producing a validated final result.",
            audit=audit.audit_ref(warnings=[str(exc)[:500]]),
            error=str(exc),
        ).model_dump(mode="json")
        audit.write_final(error_payload)
        return error_payload


def _run_sandbox_worker_in_thread(
    task: str,
    variables: dict[str, Any],
    workspace_files: list[SandboxWorkspaceFile],
    output_schema: str,
    policy: SandboxPolicy,
    audit: SandboxAuditWriter,
) -> tuple[dict[str, Any], Any]:
    """Run the async OpenAI sandbox worker outside ADK's event-loop thread."""

    def _run() -> tuple[dict[str, Any], Any]:
        return asyncio.run(
            _run_openai_sandbox_worker(
                task=task,
                variables=variables,
                workspace_files=workspace_files,
                output_schema=output_schema,
                policy=policy,
                audit=audit,
            )
        )

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-scraper-sandbox") as executor:
        return executor.submit(_run).result()


async def _run_openai_sandbox_worker(
    task: str,
    variables: dict[str, Any],
    workspace_files: list[SandboxWorkspaceFile],
    output_schema: str,
    policy: SandboxPolicy,
    audit: SandboxAuditWriter,
) -> tuple[dict[str, Any], Any]:
    try:
        import docker
        from agents import Runner, RunConfig, flush_traces
        from agents.run_config import SandboxRunConfig
        from agents.sandbox.entries import File, LocalFile
        from agents.sandbox.manifest import Manifest
        from agents.sandbox.sandboxes.docker import DockerSandboxClient, DockerSandboxClientOptions
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI Agents SDK sandbox dependencies are not installed. Run `uv sync` after "
            "adding openai-agents and docker dependencies."
        ) from exc

    raw_docker_client = docker.from_env()
    docker_client = NoNetworkDockerClient(raw_docker_client) if policy.network == "disabled" else raw_docker_client
    sandbox_client = DockerSandboxClient(docker_client)
    manifest_entries: dict[str | Path, Any] = {
        "variables.json": File(content=json.dumps(variables, ensure_ascii=True, indent=2).encode("utf-8")),
    }
    for workspace_file in workspace_files:
        manifest_entries[workspace_file.sandbox_path] = LocalFile(src=Path(workspace_file.source_path))
    manifest = Manifest(entries=manifest_entries)

    agent = build_sandbox_page_analyst_agent(
        output_schema=output_schema,
        policy=policy,
        manifest=manifest,
    )
    run_config = RunConfig(
        workflow_name=audit.openai_trace_workflow,
        trace_id=audit.openai_trace_id,
        group_id=audit.audit_id,
        trace_include_sensitive_data=False,
        trace_metadata={
            "audit_id": audit.audit_id,
            "output_schema": output_schema,
            "source": "adk_job_scraper",
            "sandbox_agent": "sandbox_page_analyst",
        },
        sandbox=SandboxRunConfig(
            client=sandbox_client,
            options=DockerSandboxClientOptions(image=policy.docker_image),
            manifest=manifest,
        )
    )

    audit.write_trace(
        "sandbox_started",
        {
            "network": policy.network,
            "container_network": policy.network,
            "allow_llm_calls": policy.allow_llm_calls,
            "persist_artifacts": policy.persist_artifacts,
            "model": policy.model,
            "openai_trace_id": audit.openai_trace_id,
            "openai_trace_workflow": audit.openai_trace_workflow,
            "openai_trace_group_id": audit.audit_id,
            "openai_tracing_disabled": os.environ.get("OPENAI_AGENTS_DISABLE_TRACING") == "1",
        },
    )
    started_at = time.perf_counter()
    try:
        result = Runner.run_streamed(
            agent,
            input=task,
            max_turns=policy.max_turns,
            run_config=run_config,
        )
        await _consume_sandbox_stream(result, audit, policy)
    finally:
        flush_traces()
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    audit.write_trace(
        "sandbox_completed",
        {
            "duration_ms": elapsed_ms,
            "current_turn": getattr(result, "current_turn", None),
            "max_turns": getattr(result, "max_turns", policy.max_turns),
            "is_complete": getattr(result, "is_complete", None),
        },
    )
    final_output = getattr(result, "final_output", None)
    if isinstance(final_output, BaseModel):
        payload = final_output.model_dump(mode="json")
    elif isinstance(final_output, dict):
        payload = final_output
    elif isinstance(final_output, str):
        payload = _parse_json_output(final_output)
    else:
        raise RuntimeError(f"Unsupported sandbox final output type: {type(final_output).__name__}")
    return payload, {"usage": usage, "completion_time_ms": elapsed_ms}


async def _consume_sandbox_stream(result: Any, audit: SandboxAuditWriter, policy: SandboxPolicy) -> None:
    try:
        async for event in result.stream_events():
            payload = _sandbox_stream_event_payload(event, result)
            if payload is not None:
                audit.write_trace("sandbox_stream_event", payload)
    except Exception as exc:
        if type(exc).__name__ == "MaxTurnsExceeded":
            audit.write_trace(
                "sandbox_stream_error",
                {
                    "error": "Max turns exceeded",
                    "current_turn": getattr(result, "current_turn", None),
                    "max_turns": getattr(result, "max_turns", policy.max_turns),
                    "is_complete": getattr(result, "is_complete", None),
                },
            )
        raise


def _sandbox_stream_event_payload(event: Any, result: Any) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "stream_event_type": _bounded_text(getattr(event, "type", type(event).__name__), 120),
        "current_turn": getattr(result, "current_turn", None),
        "max_turns": getattr(result, "max_turns", None),
        "is_complete": getattr(result, "is_complete", None),
    }

    event_type = getattr(event, "type", "")
    if event_type == "agent_updated_stream_event":
        new_agent = getattr(event, "new_agent", None)
        payload["agent_name"] = _bounded_text(getattr(new_agent, "name", ""), 160)
        return payload

    if event_type == "run_item_stream_event":
        item = getattr(event, "item", None)
        payload["name"] = _bounded_text(getattr(event, "name", ""), 160)
        payload["item"] = _sandbox_run_item_payload(item)
        return payload

    if event_type == "raw_response_event":
        raw = getattr(event, "data", None)
        raw_event_type = getattr(raw, "type", type(raw).__name__)
        if str(raw_event_type).endswith(".delta"):
            return None
        payload["raw_event_type"] = _bounded_text(raw_event_type, 160)
        for attr in ("item_id", "output_index", "response_id", "sequence_number"):
            value = getattr(raw, attr, None)
            if value is not None:
                payload[attr] = _json_safe_scalar(value)
        return payload

    return payload


def _sandbox_run_item_payload(item: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": _bounded_text(getattr(item, "type", type(item).__name__), 120),
    }
    raw_item = getattr(item, "raw_item", None)
    if raw_item is not None:
        payload["raw_type"] = _bounded_text(getattr(raw_item, "type", type(raw_item).__name__), 120)
        for attr in ("id", "call_id", "name", "status"):
            value = getattr(raw_item, attr, None)
            if value is not None:
                payload[attr] = _bounded_text(value, 240)

    agent = getattr(item, "agent", None)
    if agent is not None:
        payload["agent_name"] = _bounded_text(getattr(agent, "name", ""), 160)

    output = getattr(item, "output", None)
    if output is not None:
        payload["output_preview"] = _bounded_text(output, 500)
        payload["output_type"] = type(output).__name__

    return payload


def _parse_json_output(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Sandbox final output was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Sandbox final output must be a JSON object.")
    return parsed


def _usage_payload(usage: Any, policy: SandboxPolicy, audit_id: str) -> dict[str, Any]:
    usage_obj = usage.get("usage") if isinstance(usage, dict) else usage
    completion_time_ms = usage.get("completion_time_ms") if isinstance(usage, dict) else None
    payload = {
        "pipeline": "sandbox_final_only",
        "status": "success",
        "models": {
            "sandbox_agent": policy.model,
            "sandbox_reasoning_effort": policy.reasoning_effort,
            "sandbox_llm_calls_allowed": policy.allow_llm_calls,
            "sandbox_artifacts_persisted": policy.persist_artifacts,
        },
        "sandbox_usage": _usage_to_dict(usage_obj),
        "audit_id": audit_id,
    }
    if completion_time_ms is not None:
        payload["sandbox_usage"]["completion_time_ms"] = completion_time_ms
    return payload


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {"status": "unavailable"}
    if hasattr(usage, "model_dump"):
        return usage.model_dump(mode="json")
    if hasattr(usage, "__dict__"):
        return dict(usage.__dict__)
    return {"status": "unavailable", "raw_type": type(usage).__name__}


def _json_safe_scalar(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _bounded_text(value, 240)


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _enforce_final_output_limit(payload: dict[str, Any], max_chars: int) -> None:
    serialized = json.dumps(payload, ensure_ascii=True)
    if len(serialized) > max_chars:
        raise ValueError(f"Sandbox final output exceeds max_final_output_chars={max_chars}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True), encoding="utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _openai_trace_id(audit_id: str) -> str:
    return f"trace_{hashlib.sha256(audit_id.encode('utf-8')).hexdigest()[:32]}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
