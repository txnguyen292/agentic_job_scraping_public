from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_skill_allowed_tools_are_the_single_tool_contract() -> None:
    skill_text = Path("skills/job-listing-scout/SKILL.md").read_text(encoding="utf-8")

    assert "allowed-tools:" in skill_text
    assert "adk_additional_tools:" not in skill_text
    assert "list_seed_references" in skill_text
    assert "Sandbox Handoff" in skill_text
    assert "Direct `fetch_page` and `render_page` are diagnostic tools only" in skill_text
    assert "sandbox-page-analyst" in skill_text
    assert "finalized, validated sandbox extraction by audit ID" in skill_text
    assert "extractor output is the source of truth" not in skill_text
    assert "Do not manually shrink, rewrite, or cherry-pick job records" not in skill_text
    assert "proposal file paths or ADK artifact names" in skill_text


def test_agent_loads_runtime_tools_from_allowed_tools_contract() -> None:
    pytest.importorskip("google.adk")

    from job_scraper.registry import PROJECT_ROOT
    from job_scraper.registry import load_allowed_tool_names

    assert PROJECT_ROOT == Path.cwd()
    assert os.environ["JOB_SCRAPER_PROJECT_ROOT"] == str(Path.cwd())
    assert load_allowed_tool_names() == [
        "fetch_page",
        "render_page",
        "fetch_page_to_workspace",
        "render_page_to_workspace",
        "update_extraction_context",
        "promote_sandbox_extraction",
        "upsert_job",
        "record_crawl_run",
        "query_jobs",
        "list_seed_references",
    ]


def test_runtime_registers_project_context_and_sandbox_skills() -> None:
    pytest.importorskip("google.adk")

    from google.adk.code_executors import UnsafeLocalCodeExecutor

    from job_scraper.agent import JOB_LISTING_SCOUT_TOOLSET
    from job_scraper.registry import load_runtime_skills

    skills = load_runtime_skills()

    assert {skill.name for skill in skills} == {
        "project-context",
        "job-listing-scout",
        "sandbox-page-analyst",
        "sandbox-extraction-debugger",
    }
    assert isinstance(JOB_LISTING_SCOUT_TOOLSET._code_executor, UnsafeLocalCodeExecutor)


def test_project_context_runtime_skill_exposes_observation_notebook_script() -> None:
    skill_text = Path("skills/project-context/SKILL.md").read_text(encoding="utf-8")
    sandbox_reference_path = Path("skills/project-context/references/sandbox-runtime.md")
    record_script_path = Path("skills/project-context/scripts/record_observation.py")
    list_script_path = Path("skills/project-context/scripts/list_extraction_notes.py")

    assert sandbox_reference_path.exists()
    assert record_script_path.exists()
    assert list_script_path.exists()
    assert "durable reasoning notebook" in skill_text
    assert "references/sandbox-runtime.md" in skill_text
    assert "Repo `.contexts/` is for Codex" in skill_text
    assert "scripts/record_observation.py" in skill_text
    assert "scripts/list_extraction_notes.py" in skill_text
    assert "`observations`" in skill_text
    assert "`extraction_plan`" in skill_text
    assert "reconcile the new result with the notes" in skill_text
    assert "update the observations or extraction plan before the next attempt" in skill_text

    record_help = subprocess.run(
        [sys.executable, str(record_script_path), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    list_help = subprocess.run(
        [sys.executable, str(list_script_path), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--observations" in record_help.stdout
    assert "--extraction-plan" in record_help.stdout
    assert "--note" in record_help.stdout
    assert "--comparison" in record_help.stdout
    assert "--limit" in list_help.stdout

    context_overview = subprocess.run(
        [sys.executable, str(record_script_path.with_name("context_overview.py").resolve())],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path("src"),
    )
    notes = subprocess.run(
        [sys.executable, str(list_script_path.resolve()), "--limit", "1"],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path("src"),
    )

    assert json.loads(context_overview.stdout)["project"] == Path.cwd().name
    assert json.loads(notes.stdout)["status"] == "success"


def test_project_context_sandbox_runtime_reference_defines_context_boundary() -> None:
    reference_text = Path("skills/project-context/references/sandbox-runtime.md").read_text(encoding="utf-8")

    assert "Repo `.contexts/` is for Codex" in reference_text
    assert "Sandbox runtime context is for the ADK scraper agent" in reference_text
    assert "/workspace/context/" in reference_text
    assert "current_state.json" in reference_text
    assert "observations.jsonl" in reference_text
    assert "attempts.jsonl" in reference_text
    assert "memory.jsonl" in reference_text
    assert "Do not copy sandbox runtime memory into repo `.contexts/` automatically" in reference_text


def test_root_agent_uses_adk_skill_toolset_when_adk_is_installed() -> None:
    pytest.importorskip("google.adk")

    from google.adk.apps.app import App
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.tools.skill_toolset import SkillToolset

    from job_scraper.agent import (
        JOB_LISTING_SCOUT_SKILL,
        JOB_LISTING_SCOUT_TOOLSET,
        SerializableLiteLlm,
        app,
        root_agent,
    )
    from job_scraper.adk_plugins import (
        SandboxNoteRefinementPlugin,
        SandboxOutputGatePlugin,
        SandboxWorkflowGuardPlugin,
        TransientModelRetryPlugin,
    )
    from job_scraper.registry import AllowedToolsSkillToolset

    assert isinstance(root_agent.model, LiteLlm)
    assert isinstance(root_agent.model, SerializableLiteLlm)
    assert isinstance(JOB_LISTING_SCOUT_TOOLSET, SkillToolset)
    assert isinstance(JOB_LISTING_SCOUT_TOOLSET, AllowedToolsSkillToolset)
    direct_tool_metadata = {tool.name: tool.custom_metadata for tool in JOB_LISTING_SCOUT_TOOLSET._tools}
    assert direct_tool_metadata["update_extraction_context"]["tool_policy"]["kind"] == "notebook"
    assert direct_tool_metadata["list_skill_resources"]["tool_policy"]["kind"] == "reference_read"
    assert JOB_LISTING_SCOUT_SKILL.name == "job-listing-scout"
    assert isinstance(app, App)
    assert app.name == "job_scraper"
    assert app.root_agent is root_agent
    assert [type(plugin) for plugin in app.plugins] == [
        TransientModelRetryPlugin,
        SandboxWorkflowGuardPlugin,
        SandboxNoteRefinementPlugin,
        SandboxOutputGatePlugin,
    ]


def test_sandbox_page_analyst_exports_strict_adk_agent_and_app() -> None:
    pytest.importorskip("google.adk")

    from google.adk.apps.app import App
    from google.adk.models.lite_llm import LiteLlm

    from sandbox_page_analyst.agent import SerializableLiteLlm, app, root_agent

    assert root_agent.name == "sandbox_page_analyst"
    assert isinstance(root_agent.model, LiteLlm)
    assert isinstance(root_agent.model, SerializableLiteLlm)
    assert isinstance(app, App)
    assert app.name == "sandbox_page_analyst"
    assert app.root_agent is root_agent


def test_litellm_model_dump_is_graph_serializable() -> None:
    pytest.importorskip("google.adk")

    from job_scraper.agent import root_agent

    dumped_model = root_agent.model.model_dump(mode="python")

    assert dumped_model == {"model": "openai/gpt-5.4-mini"}
    assert "llm_client" not in dumped_model


def test_root_agent_instruction_requires_sandbox_for_url_scraping() -> None:
    pytest.importorskip("google.adk")

    from job_scraper.agent import root_agent

    assert "load 'sandbox-page-analyst'" in root_agent.instruction
    assert "load 'sandbox-extraction-debugger'" in root_agent.instruction
    assert "your first tool call must be" in root_agent.instruction
    assert "task_understanding, final_goal, and initial_plan" in root_agent.instruction
    assert "before loading skills" in root_agent.instruction
    assert "call list_skill_resources instead of guessing" in root_agent.instruction
    assert "Use --help on a listed script only when you need its arguments" in root_agent.instruction
    assert "follow the mode reference it instructs you to load" in root_agent.instruction
    assert "Use direct fetch/render tools only for explicit diagnostics" in root_agent.instruction
    assert "compact reasoning notebook" in root_agent.instruction
    assert "record what you observe" in root_agent.instruction
    assert "how those observations should produce the required outputs" in root_agent.instruction
    assert "reconcile the new result with the context" in root_agent.instruction
    assert "Page inspection is for deriving extractor logic" in root_agent.instruction
    assert "use validate_outputs.py or sandbox_finalize.py as the authority" in root_agent.instruction
    assert "do not decide that an output count is too broad or too narrow from intuition alone" in root_agent.instruction
    assert "the next phase is validation/finalization, not another rewrite" in root_agent.instruction
    assert "immediately previous tool response/event as the freshest evidence" in root_agent.instruction
    assert "next most efficient `planned_next_tool`" in root_agent.instruction
    assert "Before every tool call" in root_agent.instruction
    assert "latest exact tool result plus the injected session state" in root_agent.instruction
    assert "derive the next logical action" in root_agent.instruction
    assert "`known_errors`, `attempted_actions`, `immediate_goal`, `last_result`, `extraction_plan`, `observations`" in root_agent.instruction
    assert "`planned_next_tool`" in root_agent.instruction
    assert "`repair_scope` as the bounded work order" in root_agent.instruction
    assert "`required_outputs` and `workflow_contract`" in root_agent.instruction
    assert "output/extractor.py is the producer" in root_agent.instruction
    assert "Keep each repair incremental" in root_agent.instruction
    assert "most efficient next available tool call" in root_agent.instruction
    assert "If the planned tool itself fails" in root_agent.instruction
    assert "what the failed attempt proved" in root_agent.instruction
    assert "After every non-context tool call" in root_agent.instruction
    assert "Do not call update_extraction_context merely because update_extraction_context succeeded" in root_agent.instruction
    assert "If update_extraction_context itself returns an error" in root_agent.instruction
    assert "rerun update_extraction_context before continuing" in root_agent.instruction
    assert "remove or rewrite that stale error" in root_agent.instruction
    assert "choose an action that changes state instead of probing again" in root_agent.instruction
    assert "Treat SESSION_EXTRACTION_CONTEXT as the commanding guide" in root_agent.instruction
    assert "Treat RUNTIME_SANDBOX_NOTES as supporting evidence" in root_agent.instruction
    assert "latest exact tool output as evidence" in root_agent.instruction
    assert "call update_extraction_context before continuing" in root_agent.instruction
    assert "treat the returned facts as repair evidence" in root_agent.instruction
    assert "running extractor code" not in root_agent.instruction
    assert "extractor/protocol files" not in root_agent.instruction


def test_allowed_tools_are_exposed_after_skill_activation() -> None:
    pytest.importorskip("google.adk")

    from job_scraper.agent import JOB_LISTING_SCOUT_TOOLSET

    context = SimpleNamespace(
        agent_name="job_listing_scout",
        state={"_adk_activated_skill_job_listing_scout": ["job-listing-scout"]},
    )

    tools = asyncio.run(JOB_LISTING_SCOUT_TOOLSET.get_tools(context))
    tool_names = {tool.name for tool in tools}

    assert "load_skill" in tool_names
    assert "fetch_page" in tool_names
    assert "render_page" in tool_names
    assert "fetch_page_to_workspace" in tool_names
    assert "run_sandbox_agent" not in tool_names
    assert "promote_sandbox_extraction" in tool_names
    assert "upsert_job" in tool_names


def test_core_skill_script_tools_are_always_exposed() -> None:
    pytest.importorskip("google.adk")

    from job_scraper.agent import JOB_LISTING_SCOUT_TOOLSET

    context = SimpleNamespace(agent_name="job_listing_scout", state={})
    tools = asyncio.run(JOB_LISTING_SCOUT_TOOLSET.get_tools(context))
    tool_names = {tool.name for tool in tools}

    assert {
        "list_skills",
        "load_skill",
        "load_skill_resource",
        "run_skill_script",
        "update_extraction_context",
        "list_skill_resources",
    } <= tool_names


def test_list_skill_resources_exposes_sandbox_script_inventory() -> None:
    pytest.importorskip("google.adk")

    from job_scraper.agent import JOB_LISTING_SCOUT_TOOLSET

    context = SimpleNamespace(agent_name="job_listing_scout", state={})
    tools = asyncio.run(JOB_LISTING_SCOUT_TOOLSET.get_tools(context))
    list_resources = next(tool for tool in tools if tool.name == "list_skill_resources")

    result = asyncio.run(
        list_resources.run_async(
            args={"skill_name": "sandbox-page-analyst"},
            tool_context=SimpleNamespace(),  # type: ignore[arg-type]
        )
    )

    scripts = {item["path"]: item["summary"] for item in result["resources"]["scripts"]}
    references = {item["path"] for item in result["resources"]["references"]}

    assert result["status"] == "success"
    assert scripts["scripts/sandbox_start.py"].startswith("start or reconnect")
    assert "run bounded bash inspection commands" in scripts["scripts/sandbox_exec.py"]
    assert "write files into the sandbox workspace" in scripts["scripts/sandbox_write_file.py"]
    assert "apply targeted repair edits" in scripts["scripts/sandbox_apply_patch.py"]
    assert "read a bounded preview" in scripts["scripts/sandbox_read.py"]
    assert "generic host-mediated LiteLLM call" in scripts["scripts/sandbox_litellm_call.py"]
    assert "operator cleanup for stale project-owned Docker sandbox containers" in scripts["scripts/sandbox_cleanup.py"]
    assert "references/workflow-mode.md" in references
    assert result["usage"]["scripts"] == (
        "Use run_skill_script with one returned scripts/... path and args ['--help'] "
        "when you need argument details."
    )


def test_agent_module_does_not_own_tool_registry() -> None:
    pytest.importorskip("google.adk")

    import job_scraper.agent as agent_module
    from job_scraper.registry import TOOL_REGISTRY

    assert "persist_sandbox_job_extraction" in TOOL_REGISTRY
    assert "promote_sandbox_extraction" in TOOL_REGISTRY
    assert not hasattr(agent_module, "TOOL_REGISTRY")


def test_job_listing_scout_skill_requires_error_repair_loop() -> None:
    skill_text = Path("skills/job-listing-scout/SKILL.md").read_text(encoding="utf-8")

    assert "Before loading more resources or running extraction tools" in skill_text
    assert "what you think the user wants" in skill_text
    assert "use `list_skill_resources` instead of guessing paths" in skill_text
    assert "Run a listed script with `--help` only when you need argument details" in skill_text
    assert "Treat returned tool errors as repair evidence" in skill_text
    assert "decide whether to repair or report a blocker" in skill_text
    assert "If persistence fails" in skill_text
    assert "do not use database queries as success verification" in skill_text
    assert "proposal file paths or ADK artifact names" in skill_text
    assert "Save the target page into the workspace/artifact store" in skill_text
    assert "Load `sandbox-page-analyst`" in skill_text
    assert "load `sandbox-extraction-debugger`" in skill_text
    assert "Promote jobs only from a finalized, validated sandbox extraction by audit ID" in skill_text
    assert "Query stored jobs after successful persistence" in skill_text
    assert "Use `update_extraction_context` as the live workflow notebook" not in skill_text
    assert "<SESSION_EXTRACTION_CONTEXT>" not in skill_text
    assert "<RUNTIME_SANDBOX_NOTES>" not in skill_text
    assert "record what the agent observes" not in skill_text


def test_sandbox_extraction_debugger_skill_is_generic_test_first_repair_workflow() -> None:
    skill_text = Path("skills/sandbox-extraction-debugger/SKILL.md").read_text(encoding="utf-8")

    assert "name: sandbox-extraction-debugger" in skill_text
    assert "allowed-tools: run_skill_script" in skill_text
    assert "Only modify Docker sandbox workspace artifacts" in skill_text
    assert "Use the sandbox tools exposed through `run_skill_script`" in skill_text
    assert 'skill_name: "sandbox-page-analyst"' in skill_text
    assert 'not `skill_name: "sandbox-extraction-debugger"`' in skill_text
    assert "Inspect workspace files with `scripts/sandbox_read.py`" in skill_text
    assert "Run focused shell/Python probes with `scripts/sandbox_exec.py`" in skill_text
    assert "generic sandbox debugging and repair protocol" in skill_text
    assert "Patch-First Rule" in skill_text
    assert "Patch existing sandbox artifacts with `scripts/sandbox_apply_patch.py`" in skill_text
    assert "Create initial sandbox artifacts with `scripts/sandbox_write_file.py`" in skill_text
    assert "Do not use host filesystem tools to edit sandbox outputs" in skill_text
    assert "Do not modify host repo files" in skill_text
    assert "You may inspect helper scripts, schemas, references, and tests" in skill_text
    assert "fixes must be expressed by changing sandbox output artifacts" in skill_text
    assert "Locate the failing layer before editing" in skill_text
    assert "usage error: wrong tool, wrong script, wrong arguments" in skill_text
    assert "implementation bug: the right command ran" in skill_text
    assert "constraint mismatch: the candidate fix would violate schemas" in skill_text
    assert "Write or run a focused failing test/probe before changing code" in skill_text
    assert "Identify the minimum working code change" in skill_text
    assert "Test-First Repair" in skill_text
    assert "The test/probe should encode the observed failure and the behavior that must not regress" in skill_text
    assert "Regression Constraints" in skill_text
    assert "disallowed fix: edit read-only validators/schemas" in skill_text
    assert "ITviec listing evidence expects" not in skill_text
    assert "job-card" not in skill_text
    assert "global `/it-jobs/` URL matches" not in skill_text


def test_instruction_surface_ownership_keeps_sandbox_workflow_details_in_workflow_reference() -> None:
    agent_text = Path("src/job_scraper/agent.py").read_text(encoding="utf-8")
    job_skill_text = Path("skills/job-listing-scout/SKILL.md").read_text(encoding="utf-8")
    sandbox_skill_text = Path("skills/sandbox-page-analyst/SKILL.md").read_text(encoding="utf-8")
    workflow_text = Path("skills/sandbox-page-analyst/references/workflow-mode.md").read_text(encoding="utf-8")

    workflow_owned_phrases = [
        "derive extraction patterns",
        "derive recurring job-post patterns",
        "Do not hand-write job records",
        "Treat extraction as pattern discovery plus code generation",
        "If the extractor emits 20 valid candidates",
        "output/reference_proposal.md",
    ]

    for phrase in workflow_owned_phrases:
        assert phrase in workflow_text
        assert phrase not in agent_text
        assert phrase not in job_skill_text
        assert phrase not in sandbox_skill_text

    assert "Sandbox Handoff" in job_skill_text
    assert "Script Catalog" in sandbox_skill_text
    assert "Mode Router" in sandbox_skill_text
