from __future__ import annotations

from typing import Any

from job_scraper.tool_policy import ToolName


async def after_tool_callback(
    *,
    tool: Any,
    tool_args: dict[str, Any],
    tool_context: Any,
    result: dict,
) -> dict | None:
    from job_scraper import adk_plugins as plugin_facade

    tool_name = getattr(tool, "name", "")
    plugin_facade._record_immediate_tool_error(tool_name, tool_args, tool_context, result)
    plugin_facade._record_or_reset_repeated_inspection(tool_name, tool_args, tool_context, result)
    plugin_facade._clear_satisfied_planned_next_tool(tool_name, tool_args, tool_context, result)
    if tool_name != ToolName.UPDATE_EXTRACTION_CONTEXT and plugin_facade._is_extraction_context_progress_action(tool_name, tool_args):
        plugin_facade._reset_extraction_context_update_guard(tool_context)
    if tool_name != ToolName.RUN_SKILL_SCRIPT or not plugin_facade._sandbox_read_signature(tool_args):
        plugin_facade._reset_sandbox_read_guard(tool_context)
    if tool_name in {
        ToolName.FETCH_PAGE_TO_WORKSPACE,
        ToolName.RENDER_PAGE_TO_WORKSPACE,
        ToolName.LOAD_TEST_FIXTURE_PAGE_TO_WORKSPACE,
    }:
        plugin_facade._record_last_page_workspace(tool_context, result)
        return None

    promotion_tools = {ToolName.PERSIST_SANDBOX_JOB_EXTRACTION, ToolName.PROMOTE_SANDBOX_EXTRACTION}
    if tool_name in promotion_tools and isinstance(result, dict) and result.get("status") == "error":
        return plugin_facade._add_repair_required_next(
            result,
            (
                f"{tool_name} failed. Read the error, correct the "
                "sandbox-produced extraction payload/files, then retry promotion/persistence. Do not call query_jobs or produce "
                "a final success summary until a write succeeds or you can state a blocker."
            ),
        )
    if tool_name in promotion_tools:
        plugin_facade._record_promotion_result(tool_context, result)
        if tool_name == ToolName.PROMOTE_SANDBOX_EXTRACTION:
            return plugin_facade._add_versioned_artifact_handles_to_promotion(tool_context, result)
        return None
    if tool_name == ToolName.QUERY_JOBS:
        plugin_facade._record_query_jobs_result(tool_context, result)
        return None
    if getattr(tool, "name", "") != ToolName.RUN_SKILL_SCRIPT or tool_args.get("skill_name") != "sandbox-page-analyst":
        return None
    state = getattr(tool_context, "state", None)
    if not plugin_facade._is_state_like(state):
        return None

    payload = plugin_facade._parse_skill_script_stdout(result)
    file_path = str(tool_args.get("file_path") or "")
    repeat_guard = plugin_facade._sandbox_repeat_guard_result(state, tool_args, payload)
    if repeat_guard:
        active = state.get(plugin_facade.ACTIVE_SANDBOX_STATE_KEY)
        if isinstance(active, dict):
            if repeat_guard.get("terminal", True):
                active["status"] = "guardrail_triggered"
                active["guardrail"] = repeat_guard["guardrail"]
            else:
                active["status"] = "running"
                active["last_repair_target"] = {
                    "file_path": file_path,
                    "artifact_hint": "accountable protocol outputs",
                    "required_action": "agent_plan_repair",
                    "error": str(repeat_guard.get("error") or repeat_guard.get("guardrail") or ""),
                }
            state[plugin_facade.ACTIVE_SANDBOX_STATE_KEY] = active
        return repeat_guard
    if file_path.endswith("sandbox_start.py") and payload.get("status") == "running":
        mode = str(payload.get("mode") or "workflow")
        state[plugin_facade.ACTIVE_SANDBOX_STATE_KEY] = {
            "audit_id": str(payload.get("audit_id") or ""),
            "status": "running",
            "mode": mode,
            "command_count": 0,
            "forced_continuations": 0,
        }
        if mode != "workflow":
            return plugin_facade._add_required_next(
                result,
                "diagnostic sandbox started; run the requested bounded probe with the appropriate sandbox tool, then answer with bounded stdout/stderr previews",
            )
        return plugin_facade._add_required_next(
            result,
            "Continue the sandbox workflow by first recording extraction_plan, extraction_strategy, and immediate_goal if they are missing. The immediate_goal must name the current strategy step with evidence, strategy, validation, and next script/probe objective before producer scripting.",
        )

    active = state.get(plugin_facade.ACTIVE_SANDBOX_STATE_KEY)
    if not isinstance(active, dict):
        return None
    if file_path.endswith("sandbox_write_file.py"):
        plugin_facade._track_pending_sandbox_script_write(state, active, tool_args, payload)
        repair_next = plugin_facade._sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
        if repair_next:
            plugin_facade._record_active_repair_target(state, active, file_path, payload, tool_args)
            return repair_next
        return plugin_facade._add_required_next_for_pending_script(result, state)
    if file_path.endswith("sandbox_apply_patch.py"):
        plugin_facade._track_pending_sandbox_script_patch(state, active, tool_args, payload)
        repair_next = plugin_facade._sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
        if repair_next:
            plugin_facade._record_active_repair_target(state, active, file_path, payload, tool_args)
            return repair_next
        return plugin_facade._add_required_next_for_pending_script(result, state)
    if file_path.endswith("sandbox_exec.py"):
        active["command_count"] = int(payload.get("command_index") or active.get("command_count") or 0)
        status = str(payload.get("status") or "")
        active["status"] = status if status == "guardrail_triggered" else "running"
        if status == "success" and plugin_facade._successful_exec_clears_repair_target(active):
            active.pop("last_repair_target", None)
        state[plugin_facade.ACTIVE_SANDBOX_STATE_KEY] = active
        plugin_facade._mark_pending_sandbox_script_execution(state, active, tool_args, payload)
        plugin_facade._mark_successful_producer_rerun_after_repair(state, active, tool_args, payload)
        return plugin_facade._sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
    if file_path.endswith("sandbox_finalize.py"):
        status = str(payload.get("status") or "")
        if status in {"finalized", "success"}:
            plugin_facade._record_finalized_sandbox_for_promotion(state, active, payload)
            plugin_facade._state_pop(state, plugin_facade.ACTIVE_SANDBOX_STATE_KEY, None)
            plugin_facade._state_pop(state, plugin_facade.SANDBOX_MODE_RESOURCE_STATE_KEY, None)
            plugin_facade._state_pop(state, plugin_facade.SANDBOX_SITE_RESOURCE_STATE_KEY, None)
        elif status == "guardrail_triggered":
            active["status"] = status
            state[plugin_facade.ACTIVE_SANDBOX_STATE_KEY] = active
            plugin_facade._state_pop(state, plugin_facade.SANDBOX_MODE_RESOURCE_STATE_KEY, None)
            plugin_facade._state_pop(state, plugin_facade.SANDBOX_SITE_RESOURCE_STATE_KEY, None)
        else:
            # Finalizer errors are protocol repair feedback; the sandbox
            # container remains running until successful finalization or a
            # guardrail. Keep forcing the agent back into the repair loop.
            active["status"] = "running"
            active["last_repair_target"] = {
                "file_path": file_path,
                "artifact_hint": "accountable protocol outputs",
                "required_action": "agent_plan_repair",
                "error": str(payload.get("error") or payload.get("stderr") or "sandbox finalization error"),
            }
            state[plugin_facade.ACTIVE_SANDBOX_STATE_KEY] = active
        return plugin_facade._sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
    if file_path.endswith("validate_outputs.py"):
        repair_next = plugin_facade._sandbox_debugger_required_next(result, active, file_path, payload, tool_args)
        if repair_next:
            plugin_facade._record_active_repair_target(state, active, file_path, payload, tool_args)
            return repair_next
        return None
    return None
