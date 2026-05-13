from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any

from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.skills import load_skill_from_dir
from google.adk.tools import BaseTool, FunctionTool
from google.adk.tools.skill_toolset import SkillToolset
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

from job_scraper.adk_tools import (
    fetch_page,
    fetch_page_to_workspace,
    list_seed_references,
    load_test_fixture_page_to_workspace,
    persist_sandbox_job_extraction,
    promote_sandbox_extraction,
    query_jobs,
    record_crawl_run,
    render_page,
    render_page_to_workspace,
    run_sandbox_agent,
    update_extraction_context,
    upsert_job,
)
from job_scraper.tool_policy import attach_tool_policy_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("JOB_SCRAPER_PROJECT_ROOT", str(PROJECT_ROOT))

SKILLS_ROOT = PROJECT_ROOT / "skills"
SKILL_DIR = SKILLS_ROOT / "job-listing-scout"
PROJECT_CONTEXT_SKILL_DIR = SKILLS_ROOT / "project-context"
SANDBOX_PAGE_ANALYST_SKILL_DIR = SKILLS_ROOT / "sandbox-page-analyst"
SANDBOX_EXTRACTION_DEBUGGER_SKILL_DIR = SKILLS_ROOT / "sandbox-extraction-debugger"

TOOL_REGISTRY = {
    "fetch_page": fetch_page,
    "render_page": render_page,
    "fetch_page_to_workspace": fetch_page_to_workspace,
    "render_page_to_workspace": render_page_to_workspace,
    "load_test_fixture_page_to_workspace": load_test_fixture_page_to_workspace,
    "run_sandbox_agent": run_sandbox_agent,
    "update_extraction_context": update_extraction_context,
    "persist_sandbox_job_extraction": persist_sandbox_job_extraction,
    "promote_sandbox_extraction": promote_sandbox_extraction,
    "list_seed_references": list_seed_references,
    "upsert_job": upsert_job,
    "record_crawl_run": record_crawl_run,
    "query_jobs": query_jobs,
}


def load_allowed_tool_names(skill_dir: Path = SKILL_DIR) -> list[str]:
    """Read the skill's allowed-tools frontmatter as the runtime tool contract."""
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = skill_text.split("---", 2)[1]
    tool_names: list[str] = []

    for raw_line in frontmatter.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("allowed-tools:"):
            _, value = stripped.split(":", 1)
            tool_names = shlex.split(value.strip())
            break

    if not tool_names:
        raise ValueError(f"No allowed-tools declared in {skill_dir / 'SKILL.md'}")

    unknown_tools = sorted(set(tool_names) - set(TOOL_REGISTRY))
    if unknown_tools:
        raise ValueError(f"Unknown allowed tool(s) in skill frontmatter: {', '.join(unknown_tools)}")

    return tool_names


def load_allowed_tools(skill_dir: Path = SKILL_DIR) -> list[object]:
    return [TOOL_REGISTRY[name] for name in load_allowed_tool_names(skill_dir)]


class AllowedToolsSkillToolset(SkillToolset):
    """Resolve activated skill runtime tools from `allowed-tools`.

    ADK's stock SkillToolset resolves dynamic tools from
    `metadata.adk_additional_tools`. This project keeps `allowed-tools` as the
    single declarative contract, so the runtime bridge lives here instead.
    """

    async def _resolve_additional_tools_from_state(self, readonly_context: Any | None) -> list[BaseTool]:
        if not readonly_context:
            return []

        state_key = f"_adk_activated_skill_{readonly_context.agent_name}"
        activated_skills = readonly_context.state.get(state_key, [])
        if not activated_skills:
            return []

        additional_tool_names: set[str] = set()
        for skill_name in activated_skills:
            skill = self._skills.get(skill_name)
            if skill and skill.frontmatter.allowed_tools:
                additional_tool_names.update(shlex.split(skill.frontmatter.allowed_tools))

        if not additional_tool_names:
            return []

        candidate_tools = self._provided_tools_by_name.copy()
        if self._provided_toolsets:
            toolset_results = await asyncio.gather(
                *(toolset.get_tools_with_prefix(readonly_context) for toolset in self._provided_toolsets)
            )
            for tools in toolset_results:
                for tool in tools:
                    candidate_tools[tool.name] = tool

        existing_tool_names = {tool.name for tool in self._tools}
        resolved_tools: list[BaseTool] = []
        for name in additional_tool_names:
            tool = candidate_tools.get(name)
            if tool and tool.name not in existing_tool_names:
                resolved_tools.append(tool)
                existing_tool_names.add(tool.name)

        return resolved_tools


class ListSkillResourcesTool(BaseTool):
    """Expose a compact resource inventory for a loaded ADK skill."""

    def __init__(self, toolset: SkillToolset) -> None:
        super().__init__(
            name="list_skill_resources",
            description=(
                "Lists compact references/assets/scripts available inside a skill. "
                "Use this to discover script paths before calling run_skill_script with --help."
            ),
        )
        attach_tool_policy_metadata(self)
        self._toolset = toolset

    def _get_declaration(self) -> genai_types.FunctionDeclaration | None:
        return genai_types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The name of the skill whose resources should be listed.",
                    }
                },
                "required": ["skill_name"],
            },
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        skill_name = str(args.get("skill_name") or "").strip()
        if not skill_name:
            return {
                "status": "error",
                "error_code": "INVALID_ARGUMENTS",
                "error": "Argument 'skill_name' is required.",
            }

        skill = self._toolset._get_skill(skill_name)
        if not skill:
            return {
                "status": "error",
                "error_code": "SKILL_NOT_FOUND",
                "error": f"Skill '{skill_name}' not found.",
            }

        resources = skill.resources
        script_summaries = _script_summaries_from_instructions(skill.instructions)
        references = [
            _resource_item(path=f"references/{name}", summary=_reference_summary(content))
            for name, content in sorted((resources.references or {}).items())
        ]
        assets = [
            _resource_item(path=f"assets/{name}", summary="Asset resource; load only if needed.")
            for name in sorted((resources.assets or {}).keys())
        ]
        scripts = [
            _resource_item(
                path=f"scripts/{name}",
                summary=script_summaries.get(name, "Script resource; run with --help for arguments."),
            )
            for name in sorted((resources.scripts or {}).keys())
        ]

        return {
            "status": "success",
            "skill_name": skill_name,
            "counts": {
                "references": len(references),
                "assets": len(assets),
                "scripts": len(scripts),
            },
            "resources": {
                "references": references,
                "assets": assets,
                "scripts": scripts,
            },
            "usage": {
                "references": "Use load_skill_resource with one returned references/... path when you need the content.",
                "scripts": "Use run_skill_script with one returned scripts/... path and args ['--help'] when you need argument details.",
            },
        }


def _script_summaries_from_instructions(instructions: str) -> dict[str, str]:
    summaries: dict[str, str] = {}
    for match in re.finditer(r"^- `scripts/([^`]+)`: ([^\n]+)", instructions, flags=re.MULTILINE):
        summaries[match.group(1)] = _compact_summary(match.group(2))
    return summaries


def _reference_summary(content: str) -> str:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            return _compact_summary(line.lstrip("#").strip())
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line:
            return _compact_summary(line)
    return "Reference resource."


def _resource_item(*, path: str, summary: str) -> dict[str, str]:
    return {"path": path, "summary": _compact_summary(summary)}


def _compact_summary(text: str, max_chars: int = 180) -> str:
    summary = " ".join(str(text).split())
    if len(summary) <= max_chars:
        return summary
    return f"{summary[: max_chars - 15]}...[truncated]"


def load_runtime_skills() -> list[Any]:
    return [
        load_skill_from_dir(PROJECT_CONTEXT_SKILL_DIR),
        load_skill_from_dir(SKILL_DIR),
        load_skill_from_dir(SANDBOX_PAGE_ANALYST_SKILL_DIR),
        load_skill_from_dir(SANDBOX_EXTRACTION_DEBUGGER_SKILL_DIR),
    ]


def build_job_listing_scout_toolset() -> tuple[Any, AllowedToolsSkillToolset]:
    skills = load_runtime_skills()
    skill = next(item for item in skills if item.name == "job-listing-scout")
    toolset = AllowedToolsSkillToolset(
        skills=skills,
        code_executor=UnsafeLocalCodeExecutor(timeout_seconds=30),
        script_timeout=30,
        additional_tools=load_allowed_tools(),
    )
    toolset._tools.append(attach_tool_policy_metadata(FunctionTool(update_extraction_context)))
    toolset._tools.append(ListSkillResourcesTool(toolset))
    return skill, toolset
