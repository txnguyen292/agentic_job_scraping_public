from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from google.genai import types as genai_types


def _record_sandbox_artifact_handles(
    tool_context: Any,
    result: dict[str, Any],
    artifact_handles: dict[str, dict[str, Any]],
) -> None:
    from job_scraper import adk_plugins as plugin_facade

    state = getattr(tool_context, "state", None)
    if not plugin_facade._is_state_like(state) or not artifact_handles:
        return
    payload = plugin_facade._parse_skill_script_stdout(result)
    audit_id = str(result.get("audit_id") or payload.get("audit_id") or plugin_facade._extract_audit_id(result) or "")
    if not audit_id:
        return
    by_audit = state.setdefault(plugin_facade.SANDBOX_ARTIFACT_HANDLES_STATE_KEY, {})
    if not isinstance(by_audit, dict):
        by_audit = {}
        state[plugin_facade.SANDBOX_ARTIFACT_HANDLES_STATE_KEY] = by_audit
    existing = by_audit.get(audit_id)
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(artifact_handles)
    by_audit[audit_id] = merged
    state[plugin_facade.SANDBOX_ARTIFACT_HANDLES_STATE_KEY] = by_audit

    pending = state.get(plugin_facade.FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
    if isinstance(pending, dict) and str(pending.get("audit_id") or "") == audit_id:
        pending["artifact_handles"] = merged
        state[plugin_facade.FINALIZED_SANDBOX_PROMOTION_STATE_KEY] = pending


def _versioned_artifact_handles_for_audit(state: Any, audit_id: str) -> dict[str, dict[str, Any]]:
    from job_scraper import adk_plugins as plugin_facade

    if not plugin_facade._is_state_like(state) or not audit_id:
        return {}
    by_audit = state.get(plugin_facade.SANDBOX_ARTIFACT_HANDLES_STATE_KEY)
    if not isinstance(by_audit, dict):
        return {}
    handles = by_audit.get(audit_id)
    return dict(handles) if isinstance(handles, dict) else {}


def _add_versioned_artifact_handles_to_promotion(tool_context: Any, result: dict[str, Any]) -> dict[str, Any] | None:
    from job_scraper import adk_plugins as plugin_facade

    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    state = getattr(tool_context, "state", None)
    if not plugin_facade._is_state_like(state):
        return None
    audit_id = str(result.get("audit_id") or "")
    handles = _versioned_artifact_handles_for_audit(state, audit_id)
    if not handles:
        pending = state.get(plugin_facade.FINALIZED_SANDBOX_PROMOTION_STATE_KEY)
        if isinstance(pending, dict) and str(pending.get("audit_id") or "") == audit_id:
            pending_handles = pending.get("artifact_handles")
            handles = dict(pending_handles) if isinstance(pending_handles, dict) else {}
    if not handles:
        return None
    updated = dict(result)
    updated["adk_artifact_handles"] = handles
    updated["artifact_version_policy"] = (
        "Use adk_artifact_handles for final reporting and audit references. Each handle includes a stable "
        "ADK artifact_name plus version; workspace paths are not versioned artifact references."
    )
    return updated


async def _persist_artifact_sources(result: dict[str, Any], tool_context: Any) -> dict[str, dict[str, Any]]:
    sources = _collect_artifact_sources(result)
    if not isinstance(sources, list):
        return {}

    handles: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            continue
        source_path = Path(str(source.get("source_path") or ""))
        artifact_name = _safe_adk_artifact_name(str(source.get("artifact_name") or ""))
        if not source_path.exists() or not artifact_name:
            continue
        data = source_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        mime_type = str(source.get("mime_type") or "application/octet-stream")
        version = await tool_context.save_artifact(
            artifact_name,
            genai_types.Part.from_bytes(data=data, mime_type=mime_type),
            custom_metadata={
                "sha256": digest,
                "source_kind": "sandbox_artifact_source",
                "bytes": len(data),
            },
        )
        key = str(source.get("key") or source_path.stem or f"artifact_{index}")
        handles[key] = {
            "artifact_name": artifact_name,
            "version": version,
            "mime_type": mime_type,
            "bytes": len(data),
            "sha256": digest,
        }
    return handles


def _collect_artifact_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    from job_scraper import adk_plugins as plugin_facade

    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for payload in (result, plugin_facade._parse_skill_script_stdout(result)):
        if not isinstance(payload, dict):
            continue
        payload_sources = payload.get("artifact_sources")
        if not isinstance(payload_sources, list):
            continue
        for source in payload_sources:
            if not isinstance(source, dict):
                continue
            source_path = str(source.get("source_path") or "")
            artifact_name = str(source.get("artifact_name") or "")
            fingerprint = (source_path, artifact_name)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            sources.append(source)
    return sources


def _safe_adk_artifact_name(artifact_name: str) -> str:
    """Keep ADK artifact handles fetchable in ADK Web path-based routes."""
    return artifact_name.replace("\\", "__").replace("/", "__")


def _compact_artifact_handles(payload: dict[str, Any]) -> dict[str, Any]:
    handles: dict[str, Any] = {}
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        for source_key, target_key in (
            ("command", "command_file"),
            ("stdout", "stdout_file"),
            ("stderr", "stderr_file"),
            ("trace", "trace_file"),
        ):
            artifact = artifacts.get(source_key)
            if isinstance(artifact, dict):
                handles[target_key] = artifact
        for key, artifact in artifacts.items():
            if key not in {"command", "stdout", "stderr", "trace"} and isinstance(artifact, dict):
                handles[str(key)] = artifact
    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        handles.setdefault("primary", artifact)
    return handles


def _compact_output_paths(payload: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, str] = {}
    existing_paths = payload.get("paths")
    if isinstance(existing_paths, dict):
        for key in ("command_path", "stdout_path", "stderr_path", "trace_path"):
            value = existing_paths.get(key)
            if value:
                paths[key] = str(value)
    output_policy = payload.get("output_policy")
    if isinstance(output_policy, dict):
        for key in ("command_path", "stdout_path", "stderr_path"):
            value = output_policy.get(key)
            if value:
                paths[key] = str(value)
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        for artifact_key, path_key in (
            ("command", "command_path"),
            ("stdout", "stdout_path"),
            ("stderr", "stderr_path"),
            ("trace", "trace_path"),
        ):
            artifact = artifacts.get(artifact_key)
            if isinstance(artifact, dict) and artifact.get("path"):
                paths.setdefault(path_key, str(artifact["path"]))
    return paths
