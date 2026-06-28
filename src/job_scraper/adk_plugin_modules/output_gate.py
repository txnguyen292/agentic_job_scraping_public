from __future__ import annotations

import hashlib
import json
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types as genai_types

from job_scraper.runtime_payload import (
    OUTPUT_GATE_STORED_RESPONSE_KEYS,
    OUTPUT_GATE_TRIGGER_KEYS,
    RuntimePayloadKey,
    RuntimeStatus,
)
from job_scraper.tool_policy import ToolName


class SandboxOutputGatePlugin(BasePlugin):
    """Persist oversized sandbox-like tool results before they enter context."""

    def __init__(
        self,
        *,
        direct_max_chars: int = 8_000,
        preview_max_chars: int = 2_000,
        name: str = "sandbox_output_gate_plugin",
    ) -> None:
        super().__init__(name=name)
        self.direct_max_chars = direct_max_chars
        self.preview_max_chars = preview_max_chars

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict,
    ) -> dict | None:
        if not isinstance(result, dict):
            return None
        if not self._should_gate(tool, tool_args, result):
            return None

        from job_scraper import adk_plugins as plugin_facade

        artifact_handles = await plugin_facade._persist_artifact_sources(result, tool_context)
        if artifact_handles:
            result = dict(result)
            existing_artifacts = (
                result.get(RuntimePayloadKey.ARTIFACT_HANDLES)
                if isinstance(result.get(RuntimePayloadKey.ARTIFACT_HANDLES), dict)
                else {}
            )
            result[RuntimePayloadKey.ARTIFACT_HANDLES.value] = {**existing_artifacts, **artifact_handles}
            result.pop(RuntimePayloadKey.ARTIFACT_SOURCES, None)
            plugin_facade._record_sandbox_artifact_handles(tool_context, result, artifact_handles)

        if getattr(tool, "name", "") == ToolName.RUN_SKILL_SCRIPT and tool_args.get(RuntimePayloadKey.SKILL_NAME) == "sandbox-page-analyst":
            return result if artifact_handles else None

        serialized = json.dumps(result, ensure_ascii=True, sort_keys=True, default=str)
        if len(serialized) <= self.direct_max_chars:
            return result if artifact_handles else None

        audit_id = str(result.get(RuntimePayloadKey.AUDIT_ID) or plugin_facade._extract_audit_id(result) or "sandbox_run_unknown")
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        artifact_name = f"{audit_id}__oversized__tool_output_{digest[:12]}.json"
        part = genai_types.Part.from_bytes(data=serialized.encode("utf-8"), mime_type="application/json")
        version = await tool_context.save_artifact(
            artifact_name,
            part,
            custom_metadata={
                RuntimePayloadKey.SHA256.value: digest,
                RuntimePayloadKey.ORIGINAL_BYTES.value: len(serialized.encode("utf-8")),
            },
        )
        stored_response: dict[str, Any] = {
            RuntimePayloadKey.STATUS.value: RuntimeStatus.STORED_PREVIEW.value,
            RuntimePayloadKey.REASON.value: "tool_output_exceeded_context_threshold",
            RuntimePayloadKey.AUDIT_ID.value: audit_id,
            RuntimePayloadKey.ORIGINAL_BYTES.value: len(serialized.encode("utf-8")),
            RuntimePayloadKey.SHA256.value: digest,
            RuntimePayloadKey.PREVIEW.value: plugin_facade._preview(serialized, self.preview_max_chars),
            RuntimePayloadKey.ARTIFACT.value: {
                RuntimePayloadKey.ARTIFACT_NAME.value: artifact_name,
                RuntimePayloadKey.VERSION.value: version,
                RuntimePayloadKey.MIME_TYPE.value: "application/json",
                RuntimePayloadKey.BYTES.value: len(serialized.encode("utf-8")),
                RuntimePayloadKey.SHA256.value: digest,
            },
        }
        paths = plugin_facade._compact_output_paths(result)
        if paths:
            stored_response[RuntimePayloadKey.PATHS.value] = paths
        for key in OUTPUT_GATE_STORED_RESPONSE_KEYS:
            if key in result:
                stored_response[key] = result[key]
        return stored_response

    def _should_gate(self, tool: Any, tool_args: dict[str, Any], result: dict[str, Any]) -> bool:
        tool_name = getattr(tool, "name", "")
        if tool_name in {
            ToolName.RUN_SKILL_SCRIPT,
            ToolName.FETCH_PAGE,
            ToolName.RENDER_PAGE,
            ToolName.FETCH_PAGE_TO_WORKSPACE,
            ToolName.RENDER_PAGE_TO_WORKSPACE,
            ToolName.LOAD_TEST_FIXTURE_PAGE_TO_WORKSPACE,
        }:
            return True
        if tool_args.get(RuntimePayloadKey.SKILL_NAME) == "sandbox-page-analyst":
            return True
        return any(key in result for key in OUTPUT_GATE_TRIGGER_KEYS)
