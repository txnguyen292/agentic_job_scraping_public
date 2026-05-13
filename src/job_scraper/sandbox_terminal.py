from __future__ import annotations

import json
import os
import re
import uuid
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class SandboxLimits(BaseModel):
    max_commands_per_session: int = 20
    max_duration_seconds: int = 0
    idle_timeout_seconds: int = 120
    max_command_timeout_seconds: int = 30
    max_stdout_bytes: int = 256_000
    max_stderr_bytes: int = 128_000
    max_workspace_bytes: int = 50_000_000
    max_artifact_bytes: int = 10_000_000
    max_read_chars: int = 4_000


class SandboxSessionRecord(BaseModel):
    app_name: str = "job_scraper"
    user_id: str
    session_id: str
    audit_id: str
    container_id: str
    workspace_path: str
    status: Literal["running", "finalized", "guardrail_triggered", "error"]
    mode: Literal["workflow", "diagnostic", "debug"] = "workflow"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    limits: dict[str, object] = Field(default_factory=dict)
    command_count: int = 0
    guardrail: str = ""
    error: str = ""


class CommandPolicyResult(BaseModel):
    allowed: bool
    reason: str = ""


class CommandSlotResult(BaseModel):
    allowed: bool
    record: SandboxSessionRecord
    reason: str = ""


def sanitize_path_segment(value: str) -> str:
    cleaned = SAFE_SEGMENT_RE.sub("_", value.strip()).strip("._")
    return cleaned or "unknown"


def build_registry_path(app_root: str | Path, user_id: str, session_id: str, audit_id: str) -> Path:
    return (
        Path(app_root)
        / ".adk"
        / "runtime"
        / "sandbox_sessions"
        / sanitize_path_segment(user_id)
        / sanitize_path_segment(session_id)
        / f"{sanitize_path_segment(audit_id)}.json"
    )


class SandboxRegistry:
    def __init__(self, app_root: str | Path) -> None:
        self.app_root = Path(app_root)

    def path_for(self, user_id: str, session_id: str, audit_id: str) -> Path:
        return build_registry_path(self.app_root, user_id, session_id, audit_id)

    def save(self, record: SandboxSessionRecord) -> Path:
        record.updated_at = datetime.now(timezone.utc).isoformat()
        path = self.path_for(record.user_id, record.session_id, record.audit_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
        return path

    def load(self, user_id: str, session_id: str, audit_id: str) -> SandboxSessionRecord:
        path = self.path_for(user_id, session_id, audit_id)
        return SandboxSessionRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def iter_records(self) -> list[SandboxSessionRecord]:
        root = self.app_root / ".adk" / "runtime" / "sandbox_sessions"
        records: list[SandboxSessionRecord] = []
        if not root.exists():
            return records
        for path in sorted(root.glob("*/*/sandbox_run_*.json")):
            try:
                records.append(SandboxSessionRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return records


NETWORK_COMMANDS = (
    "curl",
    "wget",
    "ssh",
    "scp",
    "nc",
    "ncat",
    "telnet",
    "pip install",
    "pip3 install",
    "uv pip install",
    "npm install",
    "pnpm install",
    "yarn add",
    "apt install",
    "apt-get install",
    "apk add",
    "dnf install",
    "yum install",
)

PRIVILEGED_PATTERNS = (
    "/var/run/docker.sock",
    "docker ",
    "sudo ",
    "su ",
    "chmod 777 /",
    "chown ",
    "mount ",
    "umount ",
)


def command_allowed(command: str) -> CommandPolicyResult:
    lowered = command.lower()
    for token in NETWORK_COMMANDS:
        if _contains_blocked_network_command(lowered, token):
            return CommandPolicyResult(allowed=False, reason=f"network command is blocked: {token}")
    for token in PRIVILEGED_PATTERNS:
        if token in lowered:
            return CommandPolicyResult(allowed=False, reason=f"privileged operation is blocked: {token.strip()}")
    if re.search(r"(^|[;&|]\s*)(python|python3)\s+-m\s+(pip|http\.server)\b", lowered):
        return CommandPolicyResult(allowed=False, reason="network-capable python module is blocked")
    return CommandPolicyResult(allowed=True)


def _contains_blocked_network_command(command: str, token: str) -> bool:
    if " " in token:
        executable, *rest = token.split()
        rest_pattern = r"\s+".join(re.escape(part) for part in rest)
        pattern = rf"(^|[;&|]\s*){re.escape(executable)}\s+{rest_pattern}\b"
        return re.search(pattern, command) is not None
    return re.search(rf"(^|[;&|]\s*){re.escape(token)}(\s|$)", command) is not None


def mark_guardrail_triggered(
    *,
    registry: SandboxRegistry,
    user_id: str,
    session_id: str,
    audit_id: str,
    guardrail: str,
    message: str,
) -> SandboxSessionRecord:
    with _record_lock(registry, user_id, session_id, audit_id):
        record = registry.load(user_id, session_id, audit_id)
        record.status = "guardrail_triggered"
        record.guardrail = guardrail
        record.error = message
        registry.save(record)
        return record


def reserve_command_slot(
    *,
    registry: SandboxRegistry,
    user_id: str,
    session_id: str,
    audit_id: str,
) -> CommandSlotResult:
    with _record_lock(registry, user_id, session_id, audit_id):
        record = registry.load(user_id, session_id, audit_id)
        if record.status != "running":
            return CommandSlotResult(allowed=False, record=record, reason=f"sandbox is terminal: {record.status}")
        limits = SandboxLimits.model_validate(record.limits or {})
        created_at = datetime.fromisoformat(record.created_at)
        elapsed_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
        if limits.max_duration_seconds > 0 and elapsed_seconds >= limits.max_duration_seconds:
            record.status = "guardrail_triggered"
            record.guardrail = "max_duration_seconds"
            record.error = "Sandbox duration limit exhausted."
            registry.save(record)
            return CommandSlotResult(allowed=False, record=record, reason=record.error)
        if record.command_count >= limits.max_commands_per_session:
            record.status = "guardrail_triggered"
            record.guardrail = "max_commands_per_session"
            record.error = "Sandbox command budget exhausted."
            registry.save(record)
            return CommandSlotResult(allowed=False, record=record, reason=record.error)
        record.command_count += 1
        registry.save(record)
        return CommandSlotResult(allowed=True, record=record)


class _record_lock:
    def __init__(self, registry: SandboxRegistry, user_id: str, session_id: str, audit_id: str) -> None:
        path = registry.path_for(user_id, session_id, audit_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.with_suffix(path.suffix + ".lock")
        self._handle = None

    def __enter__(self) -> None:
        self._handle = self.path.open("w", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


def workspace_path(record: SandboxSessionRecord, relative_path: str) -> Path:
    workspace = Path(record.workspace_path).resolve()
    target = (workspace / relative_path).resolve()
    if not str(target).startswith(str(workspace)):
        raise ValueError("path escapes workspace")
    return target


def append_trace(record: SandboxSessionRecord, entry: dict[str, object]) -> Path:
    trace_path = workspace_path(record, "trace.jsonl")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "audit_id": record.audit_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    with trace_path.open("a", encoding="utf-8") as file:
        file.write(compact_json(payload))
        file.write("\n")
    return trace_path


def compact_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
