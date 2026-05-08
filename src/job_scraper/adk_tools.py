from __future__ import annotations

import json
import hashlib
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from google.adk.tools import ToolContext
from google.genai import types as genai_types

from job_scraper import db as storage
from job_scraper.models import CrawlRunResult, NormalizedJob, SourceConfig
from job_scraper.pipeline import run_crawl
from sandbox_page_analyst.runtime import (
    SandboxPolicy,
    validate_job_extraction_payload,
    run_generic_sandbox_agent,
)
from job_scraper.utils.scoring import (
    classify_remote_type,
    compute_overall_score,
    score_ai_ml_relevance,
    score_startup_fit,
    split_location,
    strip_html,
)
from job_scraper.sources import fetch_page as fetch_page_content
from job_scraper.sources import load_sources, render_page as render_page_content
from job_scraper.sources import utc_now
from job_scraper.runtime_state import SESSION_EXTRACTION_CONTEXT_STATE_KEY
from job_scraper.sandbox_terminal import SandboxRegistry, workspace_path


DEFAULT_DB_PATH = "data/jobs.db"
DEFAULT_SOURCE_FILE = "seeds/demo_sources.json"
DEFAULT_CONTENT_LIMIT = 12_000
DEFAULT_SANDBOX_APP_ROOT = Path(__file__).resolve().parent
PAGE_WORKSPACE_ROOT = Path("data/page_workspace")
MAX_SESSION_CONTEXT_ITEMS = 12
MAX_SESSION_CONTEXT_TEXT_CHARS = 700


def fetch_page(url: str, timeout: int = 20, max_chars: int = DEFAULT_CONTENT_LIMIT) -> dict[str, Any]:
    """Fetch a page and return a bounded HTML preview for quick inspection.

    Use this to quickly inspect whether a URL is reachable, what the first
    portion of a page looks like, and whether the response is small enough to
    reason over directly. For large or truncated HTML, do not keep asking this
    tool for more content; use a workspace/artifact tool so the full page is
    persisted outside model context and inspect it through the sandbox.
    """
    content = fetch_page_content(url, timeout=timeout)
    visible_content = _limit_content(content, max_chars)
    return {
        "status": "success",
        "url": url,
        "content": visible_content,
        "content_length": len(content),
        "returned_length": len(visible_content),
        "truncated": len(visible_content) < len(content),
    }


def render_page(url: str, timeout: int = 20, max_chars: int = DEFAULT_CONTENT_LIMIT) -> dict[str, Any]:
    """Render or fetch a page and return a bounded preview for inspection.

    Use this when a normal fetch may miss client-rendered content and the agent
    needs a quick preview or transport check. For large, truncated, or repeated
    inspection, persist the page to the workspace/artifact store and analyze it
    through the sandbox instead of returning more HTML into context.

    V1 delegates to the normal fetcher. The tool contract is separated so a
    browser renderer can be added later without changing the agent skill.
    """
    content = render_page_content(url, timeout=timeout)
    visible_content = _limit_content(content, max_chars)
    return {
        "status": "success",
        "url": url,
        "content": visible_content,
        "content_length": len(content),
        "returned_length": len(visible_content),
        "truncated": len(visible_content) < len(content),
    }


async def fetch_page_to_workspace(
    url: str,
    timeout: int = 20,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Fetch a page for scraping and store full HTML outside model context.

    Use this as the default first step for static or fetchable job pages. It
    returns metadata, a short preview, page-size signals, and an artifact/local
    path that downstream sandbox analysis can inspect without returning full
    HTML to the agent.
    """
    try:
        content = fetch_page_content(url, timeout=timeout)
    except Exception as exc:
        return _page_workspace_error(url=url, fetch_mode="fetch", error=exc)
    return await _store_page_workspace(url=url, content=content, fetch_mode="fetch", tool_context=tool_context)


async def render_page_to_workspace(
    url: str,
    timeout: int = 20,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Render or fetch a page for scraping and persist full HTML outside context.

    Use this when a page likely needs browser-like rendering, client-side
    content, or fetch_page_to_workspace did not expose usable job evidence. It
    returns the same artifact/path contract as fetch_page_to_workspace.
    """
    try:
        content = render_page_content(url, timeout=timeout)
    except Exception as exc:
        return _page_workspace_error(url=url, fetch_mode="render", error=exc)
    return await _store_page_workspace(url=url, content=content, fetch_mode="render", tool_context=tool_context)


def update_extraction_context(
    task_understanding: str = "",
    final_goal: str = "",
    initial_plan: list[str] | None = None,
    observations: list[str] | None = None,
    extraction_plan: list[str] | None = None,
    last_result: Any | None = None,
    known_errors: list[str] | None = None,
    attempted_actions: list[str] | None = None,
    immediate_goal: str = "",
    planned_next_tool: dict[str, Any] | None = None,
    repair_scope: dict[str, Any] | None = None,
    required_outputs: list[str] | None = None,
    workflow_contract: dict[str, Any] | None = None,
    audit_id: str = "",
    page_id: str = "",
    status: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Update the session-only extraction context used to guide scraping.

    Use this as the first tool call for scraping tasks to record the agent's
    task understanding and initial plan before loading skills or inspecting
    resources. Continue using it during a live sandbox workflow after meaningful
    observations, extractor runs, validation errors, repair decisions, or
    attempted actions. Treat last_result, known_errors, immediate_goal, and
    planned_next_tool as current-state replacement fields: last_result should
    summarize only the latest non-context tool result, known_errors should list
    only active blockers, and planned_next_tool should advance after successful
    extractor, validation, finalization, or persistence results. This state is
    ephemeral ADK runtime state for the current session only; it is not written
    to artifacts, `.contexts/`, or reusable references.
    """
    if tool_context is None:
        return {
            "status": "error",
            "error": "update_extraction_context requires ADK tool_context state",
        }
    state = getattr(tool_context, "state", None)
    if not _is_mutable_state(state):
        return {
            "status": "error",
            "error": "ADK tool_context.state is unavailable or immutable",
        }

    existing = state.get(SESSION_EXTRACTION_CONTEXT_STATE_KEY)
    context = dict(existing) if isinstance(existing, dict) else {}
    if audit_id:
        context["audit_id"] = _compact_text(audit_id, 160)
    if page_id:
        context["page_id"] = _compact_text(page_id, 160)
    if status:
        context["status"] = _compact_text(status, 80)
    if task_understanding:
        context["task_understanding"] = _compact_text(task_understanding, MAX_SESSION_CONTEXT_TEXT_CHARS)
    if final_goal:
        context["final_goal"] = _compact_text(final_goal, MAX_SESSION_CONTEXT_TEXT_CHARS)
    elif "final_goal" not in context and task_understanding:
        context["final_goal"] = _compact_text(task_understanding, MAX_SESSION_CONTEXT_TEXT_CHARS)

    context["initial_plan"] = _merge_text_items(context.get("initial_plan"), initial_plan)
    context["observations"] = _merge_text_items(context.get("observations"), observations)
    context["extraction_plan"] = _merge_text_items(context.get("extraction_plan"), extraction_plan)
    context["attempted_actions"] = _merge_text_items(context.get("attempted_actions"), attempted_actions)
    compact_workflow_contract = _compact_json_object(workflow_contract) if workflow_contract is not None else None
    compact_last_result = _compact_json_object(last_result) if last_result is not None else None
    replacement_known_errors = _replace_text_items(known_errors) if known_errors is not None else None
    normalized_required_outputs = _normalize_required_outputs(required_outputs)
    if not normalized_required_outputs and isinstance(compact_workflow_contract, dict):
        normalized_required_outputs = _normalize_required_outputs(compact_workflow_contract.get("required_outputs"))
    state_hygiene_error = _stale_known_errors_error(
        known_errors=replacement_known_errors,
        last_result=compact_last_result,
        existing_context=context,
        workflow_contract=compact_workflow_contract,
        required_outputs=normalized_required_outputs,
    )
    if state_hygiene_error:
        return state_hygiene_error
    if replacement_known_errors is not None:
        context["known_errors"] = replacement_known_errors
    if normalized_required_outputs or required_outputs is not None:
        context["required_outputs"] = normalized_required_outputs
    if compact_last_result is not None:
        context["last_result"] = compact_last_result
    if immediate_goal:
        context["immediate_goal"] = _compact_text(immediate_goal, MAX_SESSION_CONTEXT_TEXT_CHARS)
        context.pop("next_focus", None)
    elif "immediate_goal" not in context and context.get("next_focus"):
        context["immediate_goal"] = _compact_text(str(context["next_focus"]), MAX_SESSION_CONTEXT_TEXT_CHARS)
        context.pop("next_focus", None)
    if planned_next_tool is not None:
        compact_planned_next_tool = _compact_json_object(planned_next_tool)
        if compact_planned_next_tool:
            context["planned_next_tool"] = compact_planned_next_tool
        else:
            context.pop("planned_next_tool", None)
    if repair_scope is not None:
        compact_repair_scope = _compact_json_object(repair_scope)
        if compact_repair_scope:
            context["repair_scope"] = compact_repair_scope
        else:
            context.pop("repair_scope", None)
    if compact_workflow_contract is not None:
        if normalized_required_outputs and not _normalize_required_outputs(compact_workflow_contract.get("required_outputs")):
            compact_workflow_contract["required_outputs"] = normalized_required_outputs
        context["workflow_contract"] = compact_workflow_contract

    context["updated"] = True
    state[SESSION_EXTRACTION_CONTEXT_STATE_KEY] = context

    return {
        "status": "success",
        "context_state": "updated",
        "scope": "session_only",
        "audit_id": context.get("audit_id", ""),
        "page_id": context.get("page_id", ""),
        "has_task_understanding": bool(context.get("task_understanding")),
        "has_final_goal": bool(context.get("final_goal")),
        "initial_plan_count": len(context.get("initial_plan") or []),
        "observations_count": len(context.get("observations") or []),
        "extraction_plan_count": len(context.get("extraction_plan") or []),
        "known_errors_count": len(context.get("known_errors") or []),
        "attempted_actions_count": len(context.get("attempted_actions") or []),
        "immediate_goal": context.get("immediate_goal", ""),
        "planned_next_tool": context.get("planned_next_tool", {}),
        "repair_scope": context.get("repair_scope", {}),
        "required_outputs": context.get("required_outputs", []),
        "workflow_contract": context.get("workflow_contract", {}),
    }


def run_sandbox_agent(
    task: str,
    variables: dict[str, Any] | None = None,
    page_ids: list[str] | None = None,
    workspace_files: list[dict[str, Any]] | None = None,
    output_schema: str = "job_extraction",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Legacy compact sandbox delegation path.

    Prefer the sandbox-page-analyst skill workflow for current scraping tasks.
    Use this only for older code paths that need a single compact sandbox call.
    """
    if output_schema not in {"generic", "job_extraction"}:
        return {
            "status": "error",
            "output_schema": output_schema,
            "result": {},
            "error": "output_schema must be 'generic' or 'job_extraction'",
        }

    sandbox_policy = SandboxPolicy.model_validate(policy or {})
    try:
        resolved_workspace_files = _resolve_page_workspace_files(page_ids or [])
    except FileNotFoundError as exc:
        return {
            "status": "error",
            "output_schema": output_schema,
            "result": {},
            "error": str(exc),
        }
    resolved_workspace_files.extend(workspace_files or [])
    return run_generic_sandbox_agent(
        task=task,
        variables=variables or {},
        workspace_files=resolved_workspace_files,
        output_schema=output_schema,
        policy=sandbox_policy,
    )


def persist_sandbox_job_extraction(
    extraction: dict[str, Any] | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Persist only finalized, validated sandbox job extraction payloads.

    Use this after sandbox finalization succeeds and pass the final result
    object containing the complete jobs list. Do not pass hand-written,
    preview-derived, partial, or reconstructed payloads. If this returns an
    error, repair the extractor/protocol output before retrying.
    """
    if extraction is None:
        return {
            "status": "error",
            "error": "missing extraction payload; pass the sandbox final result.result object after scripts/sandbox_finalize.py succeeds",
            "written_count": 0,
            "suggested_next": (
                "Do not summarize or call query_jobs as success verification. Pass the sandbox final result.result "
                "object as extraction and retry persist_sandbox_job_extraction."
            ),
        }
    extraction = _coerce_job_extraction_payload(extraction)
    raw_jobs = extraction.get("jobs") if isinstance(extraction.get("jobs"), list) else []
    if not raw_jobs:
        return {
            "status": "error",
            "error": (
                "extraction payload contains no jobs; pass the job_extraction object itself, "
                "not an empty wrapper or invalid sandbox final payload"
            ),
            "written_count": 0,
            "validated_count": 0,
            "suggested_next": (
                "Inspect the sandbox final payload and protocol files, repair the extractor so result.jobs is a "
                "non-empty list of real job postings, rerun validation/finalization if the sandbox is still active, "
                "then retry persistence."
            ),
        }
    declared_count = extraction.get("count")
    if declared_count is not None:
        try:
            declared_count_int = int(declared_count)
        except (TypeError, ValueError):
            declared_count_int = None
        if declared_count_int is not None and declared_count_int != len(raw_jobs):
            return {
                "status": "error",
                "error": (
                    "extraction count does not match jobs length: "
                    f"count={declared_count_int}, jobs_length={len(raw_jobs)}. "
                    "Pass the complete sandbox final result payload, not a sampled job list."
                ),
                "written_count": 0,
                "validated_count": 0,
            }
    try:
        validated = validate_job_extraction_payload(extraction)
    except ValueError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "written_count": 0,
            "suggested_next": (
                "Use this validation error as the next repair target. Correct the sandbox-produced extraction "
                "payload rather than querying old rows: fix null/wrong-type fields, missing required values, or "
                "non-job candidates, then retry persist_sandbox_job_extraction. If the required evidence is absent, "
                "return a blocker with the audit_id instead of claiming jobs were saved."
            ),
        }

    conn = storage.ensure_db(db_path)
    written_count = 0
    try:
        source = validated.source
        for index, job in enumerate(validated.jobs):
            raw_job = raw_jobs[index] if index < len(raw_jobs) and isinstance(raw_jobs[index], dict) else {}
            payload = {
                **raw_job,
                **job.model_dump(mode="json"),
                "source_name": source.get("source_name") or raw_job.get("source_name") or source.get("source_url") or "sandbox-extracted",
                "source_type": "agent",
                "source_url": source.get("source_url") or raw_job.get("source_url") or job.job_url,
                "metadata": {
                    "sandbox_source": source,
                    "selectors": validated.selectors,
                    "crawl": validated.crawl,
                    "warnings": validated.warnings,
                },
            }
            if not payload.get("description_text") and raw_job.get("description"):
                payload["description_text"] = raw_job["description"]
            storage.upsert_job(conn, _normalize_agent_job(payload))
            written_count += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "written_count": written_count,
        "validated_count": len(validated.jobs),
        "warnings": validated.warnings,
    }


def promote_sandbox_extraction(
    audit_id: str,
    final_path: str = "output/final.json",
    db_path: str = DEFAULT_DB_PATH,
    user_id: str = "user",
    session_id: str = "local",
) -> dict[str, Any]:
    """Promote a finalized sandbox extractor output directly into storage.

    Use this after `scripts/sandbox_finalize.py` succeeds. This tool reads the
    sandbox's saved `output/final.json` from the audited workspace, validates
    the complete payload, and persists all jobs. Do not pass job lists through
    model context for sandbox results.
    """
    audit_id = str(audit_id or "").strip()
    if not audit_id:
        return {
            "status": "error",
            "error": "missing audit_id",
            "written_count": 0,
            "validated_count": 0,
        }

    try:
        record = SandboxRegistry(DEFAULT_SANDBOX_APP_ROOT).load(user_id, session_id, audit_id)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"could not load sandbox audit {audit_id}: {exc}",
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
        }

    if record.status != "finalized":
        return {
            "status": "error",
            "error": f"sandbox {audit_id} is {record.status}, not finalized",
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
            "required_next": "Finalize the sandbox with scripts/sandbox_finalize.py before promotion.",
        }

    try:
        resolved_final_path = workspace_path(record, final_path)
    except ValueError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
        }
    if not resolved_final_path.exists():
        return {
            "status": "error",
            "error": f"final output file not found: {final_path}",
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
        }

    try:
        final_payload = json.loads(resolved_final_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "error": f"invalid final JSON at {final_path}: {exc}",
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
        }

    if not isinstance(final_payload, dict):
        return {
            "status": "error",
            "error": f"final output must be a JSON object: {final_path}",
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
        }
    if final_payload.get("status") != "success":
        return {
            "status": "error",
            "error": f"final output status is {final_payload.get('status')!r}, not 'success'",
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
        }

    result_payload = final_payload.get("result")
    if not isinstance(result_payload, dict):
        return {
            "status": "error",
            "error": "final output must contain result as an object",
            "audit_id": audit_id,
            "written_count": 0,
            "validated_count": 0,
        }

    promotion_result = persist_sandbox_job_extraction(result_payload, db_path=db_path)
    promoted = {
        **promotion_result,
        "audit_id": audit_id,
        "final_path": final_path,
        "artifact_handles": {
            "final": final_path,
            "candidates": "output/candidates.json",
            "validation": "output/validation.json",
            "page_profile": "output/page_profile.json",
            "extraction_strategy": "output/extraction_strategy.json",
        },
    }
    if promotion_result.get("status") == "success":
        promoted["source"] = "sandbox_final_json"
    return promoted


def _coerce_job_extraction_payload(extraction: dict[str, Any]) -> dict[str, Any]:
    payload = extraction
    if isinstance(payload.get("result"), dict) and not isinstance(payload.get("jobs"), list):
        payload = payload["result"]
    if isinstance(payload.get("job_extraction"), dict) and not isinstance(payload.get("jobs"), list):
        payload = payload["job_extraction"]

    coerced = dict(payload)
    if "source" not in coerced and (coerced.get("source_name") or coerced.get("source_url")):
        coerced["source"] = {
            "source_name": coerced.get("source_name"),
            "source_url": coerced.get("source_url"),
        }
    return coerced


def list_seed_references(source_file: str = DEFAULT_SOURCE_FILE) -> dict[str, Any]:
    """List configured seed sources as compact references.

    Use this when expanding known sources or when a user asks how configured
    seed-driven crawls work. It is not needed for a one-off URL scrape unless
    seed references could guide navigation.
    """
    sources = load_sources(source_file)
    items = []
    for source in sources:
        items.append(
            {
                "name": source.name,
                "source_type": source.source_type,
                "board_token": source.board_token,
                "source_url": source.source_url,
                "company_name": source.company_name,
                "startup_bias": source.startup_bias,
                "fixture_file": source.fixture_file,
            }
        )
    return {
        "status": "success",
        "source_file": str(Path(source_file).resolve()),
        "count": len(items),
        "items": items,
    }


def crawl_seed_sources(
    source_file: str = DEFAULT_SOURCE_FILE,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Run deterministic seed-backed crawling and persist results.

    Use this for configured seed sources, not for ad hoc website URLs that need
    sandbox page analysis.
    """
    result = run_crawl(source_file=source_file, db_path=db_path)
    return _crawl_run_to_dict(result)


def upsert_job(job: dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Persist one manually supplied normalized job record.

    Use this for small, explicitly provided records or non-sandbox workflows.
    Do not use it to save jobs from a sandbox scrape; use
    persist_sandbox_job_extraction so the full payload is validated first.
    """
    normalized_job = _normalize_agent_job(job)
    conn = storage.ensure_db(db_path)
    try:
        storage.upsert_job(conn, normalized_job)
        conn.commit()
    finally:
        conn.close()
    return {
        "status": "success",
        "job_key": normalized_job.job_key,
        "title": normalized_job.title,
        "company_name": normalized_job.company_name,
        "overall_score": normalized_job.overall_score,
        "is_relevant": normalized_job.is_relevant,
    }


def record_crawl_run(run: dict[str, Any] | None = None, db_path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Record crawl metadata after a crawl or scrape attempt.

    Use this after persistence or after a terminal blocker to record run-level
    status, counts, audit_id, source URL, and notes. It does not save jobs.
    """
    run = run or {}
    now = utc_now()
    result = CrawlRunResult(
        run_id=str(run.get("run_id") or f"agent-{now}"),
        started_at=str(run.get("started_at") or now),
        finished_at=str(run.get("finished_at") or now),
        status=str(run.get("status") or "success"),
        source_count=int(run.get("source_count") or 0),
        discovered_count=int(run.get("discovered_count") or 0),
        written_count=int(run.get("written_count") or 0),
        error_count=int(run.get("error_count") or 0),
        notes_json=json.dumps(run.get("notes") or {}, sort_keys=True),
        source_results=run.get("source_results") or {},
    )
    conn = storage.ensure_db(db_path)
    try:
        storage.record_crawl_run(conn, result)
        conn.commit()
    finally:
        conn.close()
    return _crawl_run_to_dict(result)


def query_jobs(
    keyword: str = "",
    relevant_only: bool = False,
    min_score: Optional[float] = None,
    limit: int = 20,
    source_name: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Query persisted jobs for verification and final summaries.

    Use this after successful persistence to verify what is actually stored, or
    when the user asks to inspect the database. Do not treat old query results
    as proof that the current scrape succeeded unless they match the current
    audit/source/job URLs.
    """
    conn = storage.ensure_db(db_path)
    try:
        rows = storage.query_jobs(
            conn,
            keyword=keyword,
            relevant_only=relevant_only,
            min_score=min_score,
            limit=limit,
            source_name=source_name,
        )
        items = [{key: row[key] for key in row.keys()} for row in rows]
    finally:
        conn.close()
    return {
        "status": "success",
        "count": len(items),
        "items": items,
    }


def _is_mutable_state(state: Any) -> bool:
    return state is not None and callable(getattr(state, "get", None)) and hasattr(state, "__setitem__")


def _merge_text_items(existing: Any, incoming: Any) -> list[str]:
    items = [str(item) for item in existing if str(item).strip()] if isinstance(existing, list) else []
    for item in _iter_text_items(incoming):
        text = _compact_text(str(item), MAX_SESSION_CONTEXT_TEXT_CHARS)
        if text and text not in items:
            items.append(text)
    return items[-MAX_SESSION_CONTEXT_ITEMS:]


def _replace_text_items(incoming: Any) -> list[str]:
    items: list[str] = []
    for item in _iter_text_items(incoming):
        text = _compact_text(str(item), MAX_SESSION_CONTEXT_TEXT_CHARS)
        if text and text not in items:
            items.append(text)
    return items[-MAX_SESSION_CONTEXT_ITEMS:]


def _stale_known_errors_error(
    *,
    known_errors: list[str] | None,
    last_result: Any,
    existing_context: dict[str, Any],
    workflow_contract: dict[str, Any] | None,
    required_outputs: list[str],
) -> dict[str, Any] | None:
    if known_errors is None:
        return None
    stale = _stale_known_errors(
        known_errors=known_errors,
        last_result=last_result,
        existing_context=existing_context,
        workflow_contract=workflow_contract,
        required_outputs=required_outputs,
    )
    if not stale:
        return None
    return {
        "status": "error",
        "error_type": "extraction_context_state_hygiene",
        "guardrail": "stale_known_errors",
        "terminal": False,
        "stale_known_errors": stale,
        "error": (
            "known_errors must be a replacement list containing only active blockers. "
            "Remove blockers that the latest result or existing workflow contract has already resolved."
        ),
        "required_next": (
            "Call update_extraction_context again with last_result summarizing only the latest non-context tool "
            "result, known_errors rewritten to active blockers only, immediate_goal updated to the next concrete "
            "objective, and planned_next_tool advanced to the next useful action."
        ),
    }


def _stale_known_errors(
    *,
    known_errors: list[str],
    last_result: Any,
    existing_context: dict[str, Any],
    workflow_contract: dict[str, Any] | None,
    required_outputs: list[str],
) -> list[str]:
    stale: list[str] = []
    combined_contract = workflow_contract if workflow_contract is not None else existing_context.get("workflow_contract")
    combined_outputs = required_outputs or _normalize_required_outputs(existing_context.get("required_outputs"))
    contract_ready = isinstance(combined_contract, dict) and bool(_normalize_required_outputs(combined_contract.get("required_outputs")) or combined_outputs)
    latest_text = json.dumps(last_result, ensure_ascii=True, sort_keys=True, default=str).lower()

    for error in known_errors:
        normalized = error.strip().lower()
        if not normalized:
            continue
        if "workflow_contract_required" in normalized and contract_ready:
            stale.append(error)
        elif "producer_source_rejected" in normalized and _latest_result_resolves_producer_rejection(last_result, latest_text):
            stale.append(error)
        elif "sandbox_script_requires_audit_id" in normalized and _latest_result_has_successful_audit_id(last_result):
            stale.append(error)
    return stale


def _latest_result_resolves_producer_rejection(last_result: Any, latest_text: str) -> bool:
    if not isinstance(last_result, dict):
        return False
    status = str(last_result.get("status") or "").lower()
    if status == "success" and any(marker in latest_text for marker in ("candidate_count", "final_path", "job_count", "valid")):
        return True
    missing_files = last_result.get("missing_files")
    if isinstance(missing_files, list) and not missing_files:
        return True
    required_files = last_result.get("required_files")
    if isinstance(required_files, list) and required_files:
        return True
    return False


def _latest_result_has_successful_audit_id(last_result: Any) -> bool:
    if not isinstance(last_result, dict):
        return False
    return str(last_result.get("status") or "").lower() == "success" and bool(last_result.get("audit_id"))


def _normalize_required_outputs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    outputs: list[str] = []
    for item in value:
        text = _compact_text(str(item), 220)
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def _iter_text_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, dict):
        items: list[str] = []
        for key, item in value.items():
            key_text = str(key).strip()
            if item is None:
                continue
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                items.append(text if _looks_like_step_key(key_text) else f"{key_text}: {text}")
            elif isinstance(item, (int, float, bool)):
                items.append(f"{key_text}: {item}")
            else:
                serialized = json.dumps(_compact_json_value(item), ensure_ascii=True, sort_keys=True, default=str)
                items.append(f"{key_text}: {serialized}")
        return items
    text = str(value).strip()
    return [text] if text else []


def _looks_like_step_key(key: str) -> bool:
    return bool(re.fullmatch(r"(?:step|initial_step)[_-]?\d+", key, flags=re.IGNORECASE))


def _compact_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    marker = "...[truncated]..."
    if max_chars <= len(marker):
        return normalized[:max_chars]
    return f"{normalized[: max_chars - len(marker)]}{marker}"


def _compact_json_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"value": _compact_json_value(payload)}
    compacted: dict[str, Any] = {}
    for key, value in payload.items():
        compacted[str(key)] = _compact_json_value(value)
    return compacted


def _compact_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_text(value, MAX_SESSION_CONTEXT_TEXT_CHARS)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_compact_json_value(item) for item in value[:8]]
    if isinstance(value, dict):
        return _compact_json_object(value)
    return _compact_text(str(value), 240)


def _normalize_agent_job(payload: dict[str, Any]) -> NormalizedJob:
    source_name = str(payload.get("source_name") or "agent-extracted")
    source_type = str(payload.get("source_type") or "agent")
    source_url = str(payload.get("source_url") or payload.get("job_url") or "")
    job_url = str(payload.get("job_url") or source_url)
    company_name = str(payload.get("company_name") or payload.get("company") or "Unknown")
    title = str(payload.get("title") or "Untitled role")
    raw_description = str(payload.get("description_text") or payload.get("description") or "")
    description_text = strip_html(raw_description)
    location_raw = str(payload.get("location_raw") or payload.get("location") or "")
    location_city, location_country = split_location(location_raw)
    remote_type = str(payload.get("remote_type") or classify_remote_type(location_raw, description_text))
    startup_bias = float(payload.get("startup_bias") or 0.5)
    ai_ml_score, is_relevant = score_ai_ml_relevance(title, description_text)
    startup_score = score_startup_fit(company_name, description_text, startup_bias)
    overall_score = compute_overall_score(ai_ml_score, startup_score, remote_type)
    now = utc_now()

    source = SourceConfig(
        name=source_name,
        source_type=source_type if source_type in {"greenhouse", "lever"} else "greenhouse",
        source_url=source_url,
        company_name=company_name,
        startup_bias=startup_bias,
    )
    job_key = str(payload.get("job_key") or _stable_agent_job_key(source_name, job_url, title, company_name))

    return NormalizedJob(
        job_key=job_key,
        source_name=source_name,
        source_type=source.source_type,
        source_url=source_url,
        job_url=job_url,
        company_name=company_name,
        title=title,
        team=str(payload.get("team") or ""),
        location_raw=location_raw,
        location_country=str(payload.get("location_country") or location_country),
        location_city=str(payload.get("location_city") or location_city),
        remote_type=remote_type,
        employment_type=str(payload.get("employment_type") or ""),
        description_text=description_text,
        posted_at=str(payload.get("posted_at") or ""),
        scraped_at=str(payload.get("scraped_at") or now),
        ai_ml_score=float(payload.get("ai_ml_score") or ai_ml_score),
        startup_score=float(payload.get("startup_score") or startup_score),
        overall_score=float(payload.get("overall_score") or overall_score),
        is_relevant=bool(payload.get("is_relevant", payload.get("relevance_flag", is_relevant))),
        status=str(payload.get("status") or "active"),
        metadata_json=json.dumps(payload.get("metadata") or {}, sort_keys=True),
    )


def _crawl_run_to_dict(result: CrawlRunResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "run_id": result.run_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "source_count": result.source_count,
        "discovered_count": result.discovered_count,
        "written_count": result.written_count,
        "error_count": result.error_count,
        "source_results": result.source_results,
    }


def _limit_content(content: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars]


async def _store_page_workspace(
    url: str,
    content: str,
    fetch_mode: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    page_id = f"page_{uuid.uuid4().hex[:12]}"
    page_dir = PAGE_WORKSPACE_ROOT / page_id
    page_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = page_dir / "page.html"
    artifact_path.write_text(content, encoding="utf-8")
    profile = _profile_page_content(content)
    html_bytes = content.encode("utf-8")
    digest = hashlib.sha256(html_bytes).hexdigest()
    metadata = {
        "status": "success",
        "page_id": page_id,
        "url": url,
        "fetch_mode": fetch_mode,
        "content_length": len(content),
        "content_bytes": len(html_bytes),
        "estimated_tokens": _estimate_tokens(content),
        "html_preview": content[:2_000],
        "signals": profile["signals"],
        "recommended_next": _recommend_page_next_step(content, profile["signals"]),
        "sha256": digest,
        "artifact_path": str(artifact_path.resolve()),
    }
    (page_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8")
    if tool_context is not None:
        page_artifact_name = f"pages__{page_id}__page.html"
        metadata_artifact_name = f"pages__{page_id}__metadata.json"
        page_version = await tool_context.save_artifact(
            page_artifact_name,
            genai_types.Part.from_bytes(data=html_bytes, mime_type="text/html"),
            custom_metadata={"sha256": digest, "url": url, "fetch_mode": fetch_mode},
        )
        metadata_version = await tool_context.save_artifact(
            metadata_artifact_name,
            genai_types.Part.from_bytes(
                data=json.dumps(metadata, ensure_ascii=True, sort_keys=True).encode("utf-8"),
                mime_type="application/json",
            ),
            custom_metadata={"page_id": page_id, "url": url},
        )
        metadata["artifact"] = {
            "artifact_name": page_artifact_name,
            "version": page_version,
            "mime_type": "text/html",
            "bytes": len(html_bytes),
            "sha256": digest,
        }
        metadata["metadata_artifact"] = {
            "artifact_name": metadata_artifact_name,
            "version": metadata_version,
            "mime_type": "application/json",
        }
    return metadata


class _PageSignalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.json_ld_job_postings = 0
        self.script_count = 0
        self._in_json_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"])
        if tag.lower() == "script":
            self.script_count += 1
            self._in_json_ld = attrs_dict.get("type", "").lower() == "application/ld+json"

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_json_ld and "JobPosting" in data:
            self.json_ld_job_postings += data.count("JobPosting")


def _profile_page_content(content: str) -> dict[str, Any]:
    parser = _PageSignalParser()
    parser.feed(content)
    lowered = content.lower()
    job_like_links = [link for link in parser.links if re.search(r"job|career|position|viec|it-jobs", link, re.I)]
    hydration_blobs = int("__next_data__" in lowered) + int("__nuxt__" in lowered) + int("window.__" in lowered)
    return {
        "signals": {
            "links": len(parser.links),
            "job_like_links": len(job_like_links),
            "json_ld_job_postings": parser.json_ld_job_postings,
            "script_count": parser.script_count,
            "hydration_blobs": hydration_blobs,
        }
    }


def _estimate_tokens(content: str) -> int:
    return max(1, (len(content) + 3) // 4)


def _recommend_page_next_step(content: str, signals: dict[str, Any]) -> str:
    if len(content.encode("utf-8")) > 100_000 or _estimate_tokens(content) > 30_000:
        return "load sandbox-page-analyst"
    if int(signals.get("hydration_blobs") or 0) or int(signals.get("script_count") or 0) > 20:
        return "load sandbox-page-analyst"
    return "inspect_direct_preview"


def _page_workspace_error(url: str, fetch_mode: str, error: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "url": url,
        "fetch_mode": fetch_mode,
        "error": str(error),
        "content_bytes": 0,
        "estimated_tokens": 0,
        "html_preview": "",
        "signals": {},
        "recommended_next": "report_blocker",
    }


def _resolve_page_workspace_files(page_ids: list[str]) -> list[dict[str, str]]:
    workspace_files: list[dict[str, str]] = []
    for page_id in page_ids:
        page_artifact = PAGE_WORKSPACE_ROOT / page_id / "page.html"
        if not page_artifact.exists():
            raise FileNotFoundError(f"No page workspace artifact found for page_id={page_id}")
        workspace_files.append(
            {
                "source_path": str(page_artifact),
                "sandbox_path": f"{page_id}.html",
            }
        )
    return workspace_files


def _stable_agent_job_key(source_name: str, job_url: str, title: str, company_name: str) -> str:
    import hashlib

    material = "|".join([source_name, job_url, title.lower(), company_name.lower()])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
