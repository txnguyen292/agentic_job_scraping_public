from __future__ import annotations

from typing import Any

from job_scraper.tool_policy import ToolName
from sandbox_page_analyst.runtime import validate_job_extraction_payload


async def before_tool_callback(
    *,
    tool: Any,
    tool_args: dict[str, Any],
    tool_context: Any,
) -> dict | None:
    from job_scraper import adk_plugins as plugin_facade

    tool_name = getattr(tool, "name", "")
    initial_context_error = plugin_facade._initial_extraction_context_policy_error(tool_name, tool_args, tool_context)
    if initial_context_error:
        return initial_context_error
    workflow_contract_error = plugin_facade._workflow_contract_policy_error(tool_name, tool_args, tool_context)
    if workflow_contract_error:
        return workflow_contract_error
    immediate_goal_error = plugin_facade._immediate_goal_policy_error(tool_name, tool_args, tool_context)
    if immediate_goal_error:
        return immediate_goal_error
    repair_scope_error = plugin_facade._repair_scope_policy_error(tool_name, tool_args, tool_context)
    if repair_scope_error:
        return repair_scope_error
    planned_next_tool_error = plugin_facade._planned_next_tool_policy_error(tool_name, tool_args, tool_context)
    if planned_next_tool_error:
        return planned_next_tool_error
    immediate_repeat_error = plugin_facade._immediate_repeated_error_policy_error(tool_name, tool_args, tool_context)
    if immediate_repeat_error:
        return immediate_repeat_error
    repeated_inspection_error = plugin_facade._repeated_inspection_policy_error(tool_name, tool_args, tool_context)
    if repeated_inspection_error:
        return repeated_inspection_error
    terminal_error = plugin_facade._active_sandbox_guardrail_terminal_error(tool_name, tool_args, tool_context)
    if terminal_error:
        return terminal_error
    budget_error = plugin_facade._workflow_sandbox_tool_budget_error(tool_name, tool_args, tool_context)
    if budget_error:
        return budget_error

    if tool_name == ToolName.LOAD_SKILL_RESOURCE:
        mode_resource_error = plugin_facade._sandbox_mode_resource_policy_error(tool_args, tool_context)
        if mode_resource_error:
            return mode_resource_error
        plugin_facade._record_sandbox_site_resource_load(tool_args, tool_context)
        return None

    if tool_name == ToolName.UPDATE_EXTRACTION_CONTEXT:
        return plugin_facade._extraction_context_update_policy_error(tool_args, tool_context)

    if tool_name == ToolName.RUN_SKILL_SCRIPT:
        wrong_skill_error = plugin_facade._wrong_sandbox_helper_skill_policy_error(tool_args)
        if wrong_skill_error:
            return wrong_skill_error
        site_reference_error = plugin_facade._site_specific_reference_policy_error(tool_args, tool_context)
        if site_reference_error:
            return site_reference_error
        script_args_error = plugin_facade._sandbox_skill_script_args_policy_error(tool_args, tool_context)
        if script_args_error:
            return script_args_error
        repeated_read_error = plugin_facade._repeated_sandbox_read_policy_error(tool_args, tool_context)
        if repeated_read_error:
            return repeated_read_error
        missing_protocol_read_error = plugin_facade._missing_protocol_output_read_policy_error(tool_args, tool_context)
        if missing_protocol_read_error:
            return missing_protocol_read_error
        output_plan_error = plugin_facade._workflow_output_plan_policy_error(tool_args, tool_context)
        if output_plan_error:
            return output_plan_error
        protocol_write_error = plugin_facade._workflow_protocol_write_policy_error(tool_args, tool_context)
        if protocol_write_error:
            return protocol_write_error
        producer_write_error = plugin_facade._producer_write_after_success_policy_error(tool_args, tool_context)
        if producer_write_error:
            return producer_write_error
        host_control_error = plugin_facade._sandbox_host_control_exec_policy_error(tool_args)
        if host_control_error:
            return host_control_error
        compound_producer_error = plugin_facade._compound_producer_verification_policy_error(tool_args, tool_context)
        if compound_producer_error:
            return compound_producer_error
        script_execution_error = plugin_facade._workflow_script_execution_policy_error(tool_args, tool_context)
        if script_execution_error:
            return script_execution_error

    if tool_name in {ToolName.RECORD_CRAWL_RUN, ToolName.QUERY_JOBS}:
        active_error = plugin_facade._active_sandbox_record_query_error(tool_context, tool_name)
        if active_error:
            return active_error
        start_error = plugin_facade._workflow_requires_sandbox_start_error(tool_context)
        if start_error:
            return start_error

    if tool_name == ToolName.PROMOTE_SANDBOX_EXTRACTION:
        start_error = plugin_facade._workflow_requires_sandbox_start_error(tool_context)
        if start_error:
            return start_error
        return None

    if tool_name != ToolName.PERSIST_SANDBOX_JOB_EXTRACTION:
        return None

    active_error = plugin_facade._active_sandbox_persistence_error(tool_context)
    if active_error:
        return active_error

    extraction = tool_args.get("extraction")
    if not isinstance(extraction, dict):
        return plugin_facade._persistence_guard_error(
            "missing extraction payload; pass the finalized sandbox result.result object after sandbox finalization succeeds",
            (
                "Do not call query_jobs or summarize success. Repair the workflow by finalizing the sandbox "
                "successfully, then retry persistence with the final result.result payload."
            ),
        )

    audit_error = plugin_facade._audit_status_persistence_error(extraction)
    if audit_error:
        return audit_error

    payload = plugin_facade._coerce_extraction_payload(extraction)
    try:
        validate_job_extraction_payload(payload)
    except ValueError as exc:
        return plugin_facade._persistence_guard_error(
            str(exc),
            (
                "Use this schema error as the next repair target. Correct the sandbox extractor/protocol output "
                "or extraction payload, rerun validation/finalization if needed, then retry persistence. Do not "
                "query old DB rows as success verification after this failed write."
            ),
        )
    return None
