from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ToolActionKind(StrEnum):
    """Semantic categories used by runtime policy plugins."""

    UNKNOWN = "unknown"
    NOTEBOOK = "notebook"
    REFERENCE_READ = "reference_read"
    WORKSPACE_READ = "workspace_read"
    WORKFLOW_ACTION = "workflow_action"
    SANDBOX_READ = "sandbox_read"
    SANDBOX_WRITE = "sandbox_write"
    SANDBOX_EXEC = "sandbox_exec"
    SANDBOX_FINALIZE = "sandbox_finalize"
    PERSISTENCE_ACTION = "persistence_action"
    DATABASE_READ = "database_read"


@dataclass(frozen=True)
class ToolPolicy:
    """Runtime policy metadata for one tool invocation."""

    kind: ToolActionKind
    counts_as_intervening_action: bool
    changes_workflow_output: bool = False
    terminal: bool = False

    def to_metadata(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


UNKNOWN_TOOL_POLICY = ToolPolicy(
    kind=ToolActionKind.UNKNOWN,
    counts_as_intervening_action=False,
)


STATIC_TOOL_POLICIES: dict[str, ToolPolicy] = {
    "update_extraction_context": ToolPolicy(
        kind=ToolActionKind.NOTEBOOK,
        counts_as_intervening_action=False,
    ),
    "list_skills": ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    "load_skill": ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    "load_skill_resource": ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    "list_skill_resources": ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    "fetch_page": ToolPolicy(
        kind=ToolActionKind.WORKSPACE_READ,
        counts_as_intervening_action=True,
    ),
    "render_page": ToolPolicy(
        kind=ToolActionKind.WORKSPACE_READ,
        counts_as_intervening_action=True,
    ),
    "fetch_page_to_workspace": ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "render_page_to_workspace": ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "promote_sandbox_extraction": ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "persist_sandbox_job_extraction": ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "upsert_job": ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "record_crawl_run": ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "query_jobs": ToolPolicy(
        kind=ToolActionKind.DATABASE_READ,
        counts_as_intervening_action=True,
    ),
    "list_seed_references": ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    "run_sandbox_agent": ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
}


SANDBOX_SCRIPT_POLICIES: dict[str, ToolPolicy] = {
    "scripts/sandbox_read.py": ToolPolicy(
        kind=ToolActionKind.SANDBOX_READ,
        counts_as_intervening_action=True,
    ),
    "scripts/sandbox_progress.py": ToolPolicy(
        kind=ToolActionKind.SANDBOX_READ,
        counts_as_intervening_action=True,
    ),
    "scripts/sandbox_litellm_call.py": ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
    ),
    "scripts/sandbox_start.py": ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "scripts/sandbox_write.py": ToolPolicy(
        kind=ToolActionKind.SANDBOX_WRITE,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "scripts/sandbox_write_file.py": ToolPolicy(
        kind=ToolActionKind.SANDBOX_WRITE,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "scripts/sandbox_apply_patch.py": ToolPolicy(
        kind=ToolActionKind.SANDBOX_WRITE,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "scripts/sandbox_exec.py": ToolPolicy(
        kind=ToolActionKind.SANDBOX_EXEC,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    "scripts/validate_outputs.py": ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
    ),
    "scripts/sandbox_finalize.py": ToolPolicy(
        kind=ToolActionKind.SANDBOX_FINALIZE,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
        terminal=True,
    ),
}


def resolve_tool_policy(tool_name: str, tool_args: dict[str, Any] | None = None) -> ToolPolicy:
    """Resolve policy metadata for a concrete tool invocation."""

    tool_args = tool_args or {}
    if tool_name == "run_skill_script":
        file_path = _normalize_skill_path(str(tool_args.get("file_path") or ""))
        return SANDBOX_SCRIPT_POLICIES.get(
            file_path,
            ToolPolicy(
                kind=ToolActionKind.WORKFLOW_ACTION,
                counts_as_intervening_action=True,
            ),
        )
    return STATIC_TOOL_POLICIES.get(tool_name, UNKNOWN_TOOL_POLICY)


def policy_metadata_for_tool(tool_name: str, tool_args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"tool_policy": resolve_tool_policy(tool_name, tool_args).to_metadata()}


def attach_tool_policy_metadata(tool: Any, tool_name: str | None = None) -> Any:
    """Attach JSON-serializable policy metadata to an ADK tool when possible."""

    resolved_name = tool_name or str(getattr(tool, "name", "") or "")
    metadata = dict(getattr(tool, "custom_metadata", None) or {})
    metadata.update(policy_metadata_for_tool(resolved_name))
    try:
        tool.custom_metadata = metadata
    except Exception:
        return tool
    return tool


def _normalize_skill_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
