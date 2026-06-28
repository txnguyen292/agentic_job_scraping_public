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


class ToolName(StrEnum):
    """Known ADK tool names used by runtime policy plugins."""

    UPDATE_EXTRACTION_CONTEXT = "update_extraction_context"
    LIST_SKILLS = "list_skills"
    LOAD_SKILL = "load_skill"
    LOAD_SKILL_RESOURCE = "load_skill_resource"
    LIST_SKILL_RESOURCES = "list_skill_resources"
    FETCH_PAGE = "fetch_page"
    RENDER_PAGE = "render_page"
    FETCH_PAGE_TO_WORKSPACE = "fetch_page_to_workspace"
    RENDER_PAGE_TO_WORKSPACE = "render_page_to_workspace"
    LOAD_TEST_FIXTURE_PAGE_TO_WORKSPACE = "load_test_fixture_page_to_workspace"
    PROMOTE_SANDBOX_EXTRACTION = "promote_sandbox_extraction"
    PERSIST_SANDBOX_JOB_EXTRACTION = "persist_sandbox_job_extraction"
    UPSERT_JOB = "upsert_job"
    RECORD_CRAWL_RUN = "record_crawl_run"
    QUERY_JOBS = "query_jobs"
    LIST_SEED_REFERENCES = "list_seed_references"
    RUN_SANDBOX_AGENT = "run_sandbox_agent"
    RUN_SKILL_SCRIPT = "run_skill_script"


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
    ToolName.UPDATE_EXTRACTION_CONTEXT: ToolPolicy(
        kind=ToolActionKind.NOTEBOOK,
        counts_as_intervening_action=False,
    ),
    ToolName.LIST_SKILLS: ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.LOAD_SKILL: ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.LOAD_SKILL_RESOURCE: ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.LIST_SKILL_RESOURCES: ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.FETCH_PAGE: ToolPolicy(
        kind=ToolActionKind.WORKSPACE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.RENDER_PAGE: ToolPolicy(
        kind=ToolActionKind.WORKSPACE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.FETCH_PAGE_TO_WORKSPACE: ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    ToolName.RENDER_PAGE_TO_WORKSPACE: ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    ToolName.LOAD_TEST_FIXTURE_PAGE_TO_WORKSPACE: ToolPolicy(
        kind=ToolActionKind.WORKFLOW_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    ToolName.PROMOTE_SANDBOX_EXTRACTION: ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    ToolName.PERSIST_SANDBOX_JOB_EXTRACTION: ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    ToolName.UPSERT_JOB: ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    ToolName.RECORD_CRAWL_RUN: ToolPolicy(
        kind=ToolActionKind.PERSISTENCE_ACTION,
        counts_as_intervening_action=True,
        changes_workflow_output=True,
    ),
    ToolName.QUERY_JOBS: ToolPolicy(
        kind=ToolActionKind.DATABASE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.LIST_SEED_REFERENCES: ToolPolicy(
        kind=ToolActionKind.REFERENCE_READ,
        counts_as_intervening_action=True,
    ),
    ToolName.RUN_SANDBOX_AGENT: ToolPolicy(
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
    if tool_name == ToolName.RUN_SKILL_SCRIPT:
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
