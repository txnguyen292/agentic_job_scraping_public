from __future__ import annotations

import hashlib
import importlib.util
import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Annotated
from typing import Any
from urllib.parse import urlparse

import typer
from loguru import logger
from rich.console import Console

from job_scraper.sandbox_terminal import (
    SandboxLimits,
    SandboxRegistry,
    SandboxSessionRecord,
    append_trace,
    command_allowed,
    mark_guardrail_triggered,
    reserve_command_slot,
    workspace_path,
)
from job_scraper.sandbox_image import APPROVED_SANDBOX_PARSER_IMPORTS, APPROVED_SANDBOX_TOOLS, DEFAULT_SANDBOX_IMAGE
from sandbox_page_analyst.protocol_models import validate_protocol_file_content


DEFAULT_IMAGE = DEFAULT_SANDBOX_IMAGE
DEFAULT_APP_ROOT = os.getenv("JOB_SCRAPER_ADK_APP_ROOT", "")
ERROR_CONSOLE = Console(stderr=True)
COMMON_DOCKER_CLI_PATHS = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
)

logger.remove()
logger.add(sys.stderr, level=os.getenv("JOB_SCRAPER_SCRIPT_LOG_LEVEL", "WARNING"))


SANDBOX_START_HELP = """\
Creates a no-network Docker sandbox and mounts caller-provided page artifacts into
the sandbox workspace. Use this before sandbox_exec.py.

Path model: sandbox_exec.py commands run inside Docker with working directory
/workspace. Use relative paths such as page.html and output/extractor.py inside
sandbox_exec.py. Host workspace paths are audit-only and are not shell paths
inside Docker.

Workflow mode: pass at least one --page-artifact path returned by the page
workspace/fetch tool. The first page artifact is mounted as page.html.

Examples:
  sandbox_start.py --mode diagnostic
  sandbox_start.py --mode workflow --page-artifact /tmp/page.html --source-url https://example.com/jobs
"""

SANDBOX_EXEC_HELP = """\
Runs one bounded bash command inside an active sandbox. Use for inspection,
dependency checks, running extractor code, and validation commands. Do not use
this script to write protocol files; use sandbox_write_file.py for writes.

Examples:
  sandbox_exec.py --audit-id sandbox_run_abc --cmd "ls -la"
  sandbox_exec.py sandbox_run_abc --cmd "python output/extractor.py"
  sandbox_exec.py sandbox_run_abc --max-read-chars 1000 --cmd "sed -n '1,80p' page.html"
"""

SANDBOX_WRITE_HELP = """\
Writes bounded text into the active sandbox workspace. Use this for extractor
source, protocol JSON files, reference proposals, and skill patch proposals.
Protocol JSON writes are validated before being persisted.

Examples:
  sandbox_write_file.py --audit-id sandbox_run_abc --path output/extractor.py --content '<python source>'
  sandbox_write_file.py --audit-id sandbox_run_abc --path output/candidates.json --content '<json>'
"""

SANDBOX_APPLY_PATCH_HELP = """\
Applies a targeted patch to existing files in the active sandbox workspace.
Use this for repair edits after an extractor or artifact already exists.

Patch modes:
  - exact replacement: --path output/extractor.py --old '<old text>' --new '<new text>'
  - unified diff: --patch '<unified diff with ---/+++ and @@ hunks>'
  - Codex patch: --patch '<*** Begin Patch ... *** Update File: ...>'

Examples:
  sandbox_apply_patch.py --audit-id sandbox_run_abc --path output/extractor.py --old 'limit = 1' --new 'limit = 20'
  sandbox_apply_patch.py --audit-id sandbox_run_abc --patch '--- a/output/extractor.py\n+++ b/output/extractor.py\n@@ -1 +1 @@\n-old\n+new'
  sandbox_apply_patch.py --audit-id sandbox_run_abc --patch '*** Begin Patch\n*** Update File: output/extractor.py\n@@\n-old\n+new\n*** End Patch'
"""

SANDBOX_READ_HELP = """\
Reads a bounded preview from a sandbox workspace file. Use this when a persisted
file is too large for direct context and the next decision only needs a slice.

Example:
  sandbox_read.py --audit-id sandbox_run_abc --path output/candidates.json --max-chars 2000
"""

SANDBOX_PROGRESS_HELP = """\
Persists compact progress state for a multi-step sandbox workflow. Use for
current plan, completed steps, blockers, and next inspection target, not for raw
HTML or long terminal output.

Example:
  sandbox_progress.py --audit-id sandbox_run_abc --progress-json '{"status":"extracting"}'
"""

SANDBOX_FINALIZE_HELP = """\
Finalizes a sandbox run. In workflow mode this validates required protocol
outputs before stopping the sandbox. If validation fails, the sandbox remains
running and the returned error should be repaired in the same sandbox.

Examples:
  sandbox_finalize.py --audit-id sandbox_run_abc
  sandbox_finalize.py --audit-id sandbox_run_abc --status needs_review --summary 'diagnostic probe complete'
"""

SANDBOX_LITELLM_CALL_HELP = """\
Runs a generic host-mediated LiteLLM call for an active sandbox task. The Docker
sandbox remains no-network; this helper accepts OpenAI-style messages JSON,
calls the configured model from the host, and can persist the response payload
back into the sandbox workspace for audit.

Examples:
  sandbox_litellm_call.py --audit-id sandbox_run_abc --messages-json '[{"role":"user","content":"Classify this error"}]'
  sandbox_litellm_call.py --audit-id sandbox_run_abc --messages-json '[{"role":"user","content":"Return JSON"}]' --response-format-json '{"type":"json_object"}' --output-path output/debug.llm.json
"""

SANDBOX_CLEANUP_HELP = """\
Stops stale project-owned Docker sandbox containers.

Targets only containers labeled job_scraper_sandbox=true. By default this is a
dry run. Use --no-dry-run to remove containers and mark stale running registry
records as guardrail_triggered.

Examples:
  sandbox_cleanup.py --max-age-seconds 900
  sandbox_cleanup.py --max-age-seconds 900 --include-orphans --no-dry-run
"""


START_CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}
EXEC_CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}

sandbox_start_app = typer.Typer(
    add_completion=False,
    help="Start a no-network Docker sandbox for page analysis.\n\n" + SANDBOX_START_HELP,
    rich_markup_mode="rich",
    context_settings=START_CONTEXT_SETTINGS,
)
sandbox_exec_app = typer.Typer(
    add_completion=False,
    help="Run a bounded bash command in an active Docker sandbox.\n\n" + SANDBOX_EXEC_HELP,
    rich_markup_mode="rich",
    context_settings=EXEC_CONTEXT_SETTINGS,
)
sandbox_finalize_app = typer.Typer(
    add_completion=False,
    help="Finalize a Docker sandbox run.\n\n" + SANDBOX_FINALIZE_HELP,
    rich_markup_mode="rich",
)
sandbox_read_app = typer.Typer(
    add_completion=False,
    help="Read bounded text from a sandbox workspace file.\n\n" + SANDBOX_READ_HELP,
    rich_markup_mode="rich",
)
sandbox_write_app = typer.Typer(
    add_completion=False,
    help="Write bounded text to a sandbox workspace file.\n\n" + SANDBOX_WRITE_HELP,
    rich_markup_mode="rich",
)
sandbox_apply_patch_app = typer.Typer(
    add_completion=False,
    help="Apply a targeted patch to sandbox workspace files.\n\n" + SANDBOX_APPLY_PATCH_HELP,
    rich_markup_mode="rich",
)
sandbox_progress_app = typer.Typer(
    add_completion=False,
    help="Write a compact sandbox progress file.\n\n" + SANDBOX_PROGRESS_HELP,
    rich_markup_mode="rich",
)
sandbox_litellm_call_app = typer.Typer(
    add_completion=False,
    help="Run a generic host-mediated LiteLLM call for sandbox analysis.\n\n" + SANDBOX_LITELLM_CALL_HELP,
    rich_markup_mode="rich",
)
sandbox_cleanup_app = typer.Typer(
    add_completion=False,
    help="Clean up stale Docker sandbox containers.\n\n" + SANDBOX_CLEANUP_HELP,
    rich_markup_mode="rich",
)


def sandbox_start_main() -> None:
    sandbox_start_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


@sandbox_start_app.command(help="Start a no-network Docker sandbox for page analysis.\n\n" + SANDBOX_START_HELP)
def _sandbox_start_cli(
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Optional existing/new sandbox_run_* id. Omit to create one.")] = "",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
    image: Annotated[str, typer.Option(help="Docker image to run. Use the project default unless debugging the image.")] = DEFAULT_IMAGE,
    workspace_files_json: Annotated[str, typer.Option("--workspace-files-json", "--workspace_files_json", help="JSON list of {source_path,sandbox_path} files to mount into the sandbox workspace.")] = "[]",
    page_artifact: Annotated[list[str] | None, typer.Option("--page-artifact", "--page_artifact", help="Path to a persisted page/workspace artifact. Repeatable; first becomes page.html.")] = None,
    source_url: Annotated[str, typer.Option("--source-url", "--source_url", help="Original page URL for trace/protocol metadata only; sandbox still has no web access.")] = "",
    mode: Annotated[str, typer.Option(help="workflow extracts jobs; diagnostic runs small probes; debug is diagnostic with audit intent.")] = "workflow",
    max_commands_per_session: Annotated[int, typer.Option("--max-commands-per-session", "--max_commands_per_session", help="Hard cap on sandbox_exec commands before guardrail termination.")] = 20,
    max_duration_seconds: Annotated[int, typer.Option("--max-duration-seconds", "--max_duration_seconds", help="Maximum wall-clock run duration before guardrail termination. Use 0 to disable.")] = 0,
    idle_timeout_seconds: Annotated[int, typer.Option("--idle-timeout-seconds", "--idle_timeout_seconds", help="Maximum idle time before guardrail termination.")] = 120,
    max_command_timeout_seconds: Annotated[int, typer.Option("--max-command-timeout-seconds", "--max_command_timeout_seconds", help="Per-command timeout ceiling inside the sandbox.")] = 30,
    max_stdout_bytes: Annotated[int, typer.Option("--max-stdout-bytes", "--max_stdout_bytes", help="Persisted stdout byte limit per command before guardrail termination.")] = 256_000,
    max_stderr_bytes: Annotated[int, typer.Option("--max-stderr-bytes", "--max_stderr_bytes", help="Persisted stderr byte limit per command before guardrail termination.")] = 128_000,
    max_workspace_bytes: Annotated[int, typer.Option("--max-workspace-bytes", "--max_workspace_bytes", help="Workspace byte limit before guardrail termination.")] = 50_000_000,
    max_artifact_bytes: Annotated[int, typer.Option("--max-artifact-bytes", "--max_artifact_bytes", help="Final artifact byte limit before guardrail termination.")] = 10_000_000,
    max_read_chars: Annotated[int, typer.Option("--max-read-chars", "--max_read_chars", help="Default preview length returned to the agent for large outputs.")] = 4_000,
    positional_page_artifacts: Annotated[list[str] | None, typer.Argument(help="Optional page artifact paths; equivalent to repeated --page-artifact.")] = None,
) -> None:
    if mode not in {"workflow", "diagnostic", "debug"}:
        ERROR_CONSOLE.print("[red]Invalid --mode. Choose workflow, diagnostic, or debug.[/red]")
        raise typer.Exit(2)
    args = SimpleNamespace(
        user_id=user_id,
        session_id=session_id,
        audit_id=audit_id,
        app_root=app_root or _default_app_root(),
        image=image,
        workspace_files_json=workspace_files_json,
        page_artifact=page_artifact or [],
        source_url=source_url,
        mode=mode,
        max_commands_per_session=max_commands_per_session,
        max_duration_seconds=max_duration_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        max_command_timeout_seconds=max_command_timeout_seconds,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
        max_workspace_bytes=max_workspace_bytes,
        max_artifact_bytes=max_artifact_bytes,
        max_read_chars=max_read_chars,
        positional_page_artifacts=positional_page_artifacts or [],
    )
    unknown_args: list[str] = []

    audit_id = args.audit_id or f"sandbox_run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    limits = SandboxLimits(
        max_commands_per_session=args.max_commands_per_session,
        max_duration_seconds=args.max_duration_seconds,
        idle_timeout_seconds=args.idle_timeout_seconds,
        max_command_timeout_seconds=args.max_command_timeout_seconds,
        max_stdout_bytes=args.max_stdout_bytes,
        max_stderr_bytes=args.max_stderr_bytes,
        max_workspace_bytes=args.max_workspace_bytes,
        max_artifact_bytes=args.max_artifact_bytes,
        max_read_chars=args.max_read_chars,
    )
    workspace = Path(tempfile.mkdtemp(prefix=f"{audit_id}_"))
    workspace_files = _workspace_file_specs(
        args.workspace_files_json,
        [*args.page_artifact, *args.positional_page_artifacts],
    )
    _materialize_workspace_files(workspace, workspace_files)
    _materialize_skill_helpers(workspace)
    _initialize_workspace_protocol(
        workspace,
        audit_id,
        limits,
        mode=args.mode,
        source_url=args.source_url,
        image=args.image,
        ignored_args=unknown_args,
        workspace_files=workspace_files,
    )

    try:
        container_id = _start_container(args.image, workspace, limits)
        status = "running"
        error = ""
    except Exception as exc:  # pragma: no cover - exercised by Docker integration tests.
        container_id = ""
        status = "error"
        error = str(exc)

    record = SandboxSessionRecord(
        user_id=args.user_id,
        session_id=args.session_id,
        audit_id=audit_id,
        container_id=container_id,
        workspace_path=str(workspace),
        status=status,  # type: ignore[arg-type]
        mode=args.mode,
        limits=limits.model_dump(),
    )
    registry_path = SandboxRegistry(args.app_root).save(record)
    _emit(
        {
            "status": status,
            "audit_id": audit_id,
            "container_id": container_id,
            "mode": args.mode,
            "error": error,
            "sandbox_workdir": "/workspace",
            "sandbox_visible_files": _sandbox_visible_files(workspace),
            "command_rule": (
                "sandbox_exec.py commands run inside Docker with cwd=/workspace. "
                "Use page.html, inputs.json, progress.json, and output/... paths. "
                "Do not cd into host audit paths."
            ),
            "audit_metadata": {
                "host_paths_hidden": True,
                "registry": "stored in host ADK runtime; not a sandbox_exec path",
            },
        }
    )


def sandbox_exec_main() -> None:
    sandbox_exec_app(
        args=_normalize_sandbox_exec_argv(_normalized_argv()),
        prog_name=Path(sys.argv[0]).name,
        standalone_mode=False,
    )


@sandbox_exec_app.command(help="Run a bounded bash command in an active Docker sandbox.\n\n" + SANDBOX_EXEC_HELP)
def _sandbox_exec_cli(
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Required sandbox_run_* id. May also be supplied as the leading positional arg.")] = "",
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
    command: Annotated[str, typer.Option("--command", "--cmd", help="Bash command to run inside /workspace in the active sandbox.")] = "",
    max_read_chars: Annotated[int | None, typer.Option("--max-read-chars", "--max_read_chars", help="Override returned stdout/stderr preview chars for this command only.")] = None,
    positional_command: Annotated[list[str] | None, typer.Argument(help="Optional command form after audit id, e.g. sandbox_run_abc ls -la.")] = None,
) -> None:
    args = SimpleNamespace(
        audit_id=audit_id,
        user_id=user_id,
        session_id=session_id,
        app_root=app_root or _default_app_root(),
        command=_resolved_sandbox_command(command, positional_command or []),
        max_read_chars=max_read_chars,
    )
    _run_sandbox_exec(args)


def _run_sandbox_exec(args: SimpleNamespace) -> None:
    if not args.audit_id:
        _emit(
            {
                "status": "error",
                "audit_id": "",
                "error": "sandbox_exec requires --audit-id or a leading sandbox_run_* audit id",
            }
        )
        return
    if not args.command:
        _emit(
            {
                "status": "error",
                "audit_id": args.audit_id,
                "error": "sandbox_exec requires a command",
            }
        )
        return

    policy = command_allowed(args.command)
    if not policy.allowed:
        _emit({"status": "rejected", "audit_id": args.audit_id, "error": policy.reason})
        return
    write_policy_error = _inline_file_write_policy_error(args.command)
    if write_policy_error:
        _emit(
            {
                "status": "rejected",
                "audit_id": args.audit_id,
                "error": write_policy_error,
            }
        )
        return

    registry = SandboxRegistry(args.app_root)
    slot = reserve_command_slot(
        registry=registry,
        user_id=args.user_id,
        session_id=args.session_id,
        audit_id=args.audit_id,
    )
    record = slot.record
    if not slot.allowed:
        if record.status == "guardrail_triggered":
            _stop_container(record.container_id)
            artifact_sources = _write_guardrail_evidence(record, record.guardrail, slot.reason)
        else:
            artifact_sources = []
        _emit(
            {
                "status": record.status,
                "audit_id": args.audit_id,
                "guardrail": record.guardrail,
                "error": slot.reason,
                "artifact_sources": artifact_sources,
            }
        )
        return

    limits = SandboxLimits.model_validate(record.limits or {})
    if args.max_read_chars is not None:
        limits = limits.model_copy(update={"max_read_chars": max(0, args.max_read_chars)})
    command_index = record.command_count
    workspace_guardrail = _check_workspace_size(registry, record, limits)
    if workspace_guardrail is not None:
        _stop_container(workspace_guardrail.container_id)
        _emit(
            {
                "status": "guardrail_triggered",
                "audit_id": args.audit_id,
                "guardrail": workspace_guardrail.guardrail,
                "error": workspace_guardrail.error,
                "artifact_sources": _write_guardrail_evidence(
                    workspace_guardrail,
                    workspace_guardrail.guardrail,
                    workspace_guardrail.error,
                ),
            }
        )
        return
    started = datetime.now(timezone.utc)
    docker_cli = _resolve_docker_cli()
    if docker_cli is None:
        _emit(
            {
                "status": "error",
                "audit_id": args.audit_id,
                "command_index": command_index,
                "error_type": "docker_cli_unavailable",
                "error": (
                    "Docker CLI is not available on PATH and was not found in common macOS install paths. "
                    "Set JOB_SCRAPER_DOCKER_CLI to the docker executable path or make docker reachable."
                ),
            }
        )
        return

    try:
        completed = subprocess.run(
            [docker_cli, "exec", record.container_id, "bash", "-lc", args.command],
            capture_output=True,
            text=True,
            timeout=limits.max_command_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        updated = mark_guardrail_triggered(
            registry=registry,
            user_id=args.user_id,
            session_id=args.session_id,
            audit_id=args.audit_id,
            guardrail="max_command_timeout_seconds",
            message=f"Command timed out after {limits.max_command_timeout_seconds} seconds.",
        )
        _stop_container(updated.container_id)
        artifacts = _write_command_evidence(
            record=updated,
            command_index=command_index,
            command=args.command,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            exit_code=None,
            duration_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            status="guardrail_triggered",
            guardrail=updated.guardrail,
        )
        artifact_sources = _artifact_sources_from_artifacts(updated, artifacts)
        _emit(
            {
                "status": "guardrail_triggered",
                "audit_id": args.audit_id,
                "command_index": command_index,
                "guardrail": updated.guardrail,
                "error": updated.error,
                **_bounded_command_output(
                    artifacts=artifacts,
                    stdout=_ensure_text(exc.stdout or ""),
                    stderr=_ensure_text(exc.stderr or ""),
                    limits=limits,
                    exit_code=None,
                ),
                "artifacts": artifacts,
                "artifact_sources": [
                    *artifact_sources,
                    *_write_guardrail_evidence(updated, updated.guardrail, updated.error),
                ],
            }
        )
        return
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    artifacts = _write_command_evidence(
        record=record,
        command_index=command_index,
        command=args.command,
        stdout=completed.stdout,
        stderr=completed.stderr,
        exit_code=completed.returncode,
        duration_ms=duration_ms,
        status="success" if completed.returncode == 0 else "error",
    )
    stdout_bytes = len(completed.stdout.encode("utf-8"))
    stderr_bytes = len(completed.stderr.encode("utf-8"))
    guardrail_record = _check_command_output_limits(
        registry=registry,
        record=record,
        limits=limits,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
    ) or _check_workspace_size(registry, record, limits)
    if guardrail_record is not None:
        _stop_container(guardrail_record.container_id)
        _emit(
            {
                "status": "guardrail_triggered",
                "audit_id": args.audit_id,
                "command_index": command_index,
                "exit_code": completed.returncode,
                "duration_ms": duration_ms,
                "guardrail": guardrail_record.guardrail,
                "error": guardrail_record.error,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                **_bounded_command_output(
                    artifacts=artifacts,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    limits=limits,
                    exit_code=completed.returncode,
                ),
                "artifacts": artifacts,
                "artifact_sources": [
                    *_artifact_sources_from_artifacts(record, artifacts),
                    *_write_guardrail_evidence(guardrail_record, guardrail_record.guardrail, guardrail_record.error),
                ],
            }
        )
        return
    _emit(
        {
            "status": "success" if completed.returncode == 0 else "error",
            "audit_id": args.audit_id,
            "command_index": command_index,
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            **_bounded_command_output(
                artifacts=artifacts,
                stdout=completed.stdout,
                stderr=completed.stderr,
                limits=limits,
                exit_code=completed.returncode,
            ),
            "artifacts": artifacts,
            "artifact_sources": _artifact_sources_from_artifacts(record, artifacts),
        }
    )


def _parse_sandbox_exec_args(argv: list[str]) -> SimpleNamespace:
    argv = _normalize_sandbox_exec_argv(argv)
    parsed: dict[str, Any] = {
        "user_id": "user",
        "session_id": "local",
        "audit_id": "",
        "app_root": _default_app_root(),
        "command": "",
        "max_read_chars": None,
    }
    positional: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        option_map = {
            "--user-id": "user_id",
            "--user_id": "user_id",
            "--session-id": "session_id",
            "--session_id": "session_id",
            "--audit-id": "audit_id",
            "--audit_id": "audit_id",
            "--app-root": "app_root",
            "--app_root": "app_root",
            "--command": "command",
            "--cmd": "command",
            "--max-read-chars": "max_read_chars",
            "--max_read_chars": "max_read_chars",
        }
        if item == "--":
            positional.extend(argv[index + 1 :])
            break
        if item in option_map:
            key = option_map[item]
            value = argv[index + 1] if index + 1 < len(argv) else ""
            parsed[key] = int(value) if key == "max_read_chars" and value else value
            index += 2
            continue
        positional.append(item)
        index += 1
    parsed["command"] = _resolved_sandbox_command(str(parsed["command"] or ""), positional)
    return SimpleNamespace(**parsed)


def _resolved_sandbox_command(command: str, positional_command: list[str]) -> str:
    if command:
        return str(command).strip()
    command_parts = list(positional_command)
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    if len(command_parts) == 1:
        return command_parts[0].strip()
    return shlex.join(command_parts).strip()


def _normalize_sandbox_exec_argv(argv: list[str]) -> list[str]:
    """Accept the common agent form: sandbox_run_x --cmd '...'."""
    if not argv:
        return argv
    if "--audit-id" in argv or "--audit_id" in argv:
        return argv
    first = argv[0]
    if first.startswith("sandbox_run_"):
        return ["--audit-id", first, *argv[1:]]
    return argv


def _inline_file_write_policy_error(command: str) -> str:
    lowered = command.lower()
    writes_sandbox_outputs = any(
        marker in lowered
        for marker in (
            "output/",
            "'output'",
            '"output"',
            "extractor.py",
            "page_profile.json",
            "extraction_strategy.json",
            "candidates.json",
            "validation.json",
            "final.json",
            "reference_proposal",
        )
    )
    inline_writer = any(
        marker in lowered
        for marker in (
            ".write_text(",
            "open(",
            "cat >",
            "cat <<",
            "tee output/",
            "printf ",
        )
    )
    if writes_sandbox_outputs and inline_writer:
        return (
            "Do not use sandbox_exec.py for inline Python heredocs or shell snippets that write sandbox protocol files. "
            "Use scripts/sandbox_write_file.py for output/extractor.py and protocol file writes, then use "
            "scripts/sandbox_exec.py only to inspect files, run supporting scripts, validate outputs, or finalize."
        )
    return ""


def sandbox_finalize_main() -> None:
    sandbox_finalize_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


@sandbox_finalize_app.command(help="Finalize a Docker sandbox run.\n\n" + SANDBOX_FINALIZE_HELP)
def _sandbox_finalize_cli(
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Required sandbox_run_* id to finalize.")] = "",
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
    status: Annotated[str, typer.Option(help="Optional inline final status for diagnostic/debug runs or explicit blockers.")] = "",
    summary: Annotated[str, typer.Option(help="Optional short final summary. Do not include raw HTML or long transcripts.")] = "",
    result: Annotated[str, typer.Option(help="Optional compact JSON result. Workflow mode normally reads output/final.json instead.")] = "",
) -> None:
    if not audit_id:
        ERROR_CONSOLE.print("[red]Missing required option '--audit-id'.[/red]")
        raise typer.Exit(2)
    if status and status not in {"success", "needs_review", "error"}:
        ERROR_CONSOLE.print("[red]Invalid --status. Choose success, needs_review, or error.[/red]")
        raise typer.Exit(2)
    args = SimpleNamespace(
        audit_id=audit_id,
        user_id=user_id,
        session_id=session_id,
        app_root=app_root or _default_app_root(),
        status=status,
        summary=summary,
        result=result,
    )

    registry = SandboxRegistry(args.app_root)
    record = registry.load(args.user_id, args.session_id, args.audit_id)
    if record.status != "running":
        _emit({"status": record.status, "audit_id": args.audit_id, "error": f"sandbox is terminal: {record.status}"})
        return
    limits = SandboxLimits.model_validate(record.limits or {})
    candidate_final_sources = _final_artifact_sources(record)
    guardrail_record = _check_artifact_source_limits(registry, record, limits, candidate_final_sources)
    if guardrail_record is not None:
        _stop_container(guardrail_record.container_id)
        _emit(
            {
                "status": "guardrail_triggered",
                "audit_id": args.audit_id,
                "guardrail": guardrail_record.guardrail,
                "error": guardrail_record.error,
                "artifact_sources": _write_guardrail_evidence(
                    guardrail_record,
                    guardrail_record.guardrail,
                    guardrail_record.error,
                ),
            }
        )
        return
    ignored_inline_args = False
    if record.mode in {"diagnostic", "debug"} or args.result:
        _write_inline_final_result(record, status=args.status, summary=args.summary, result_json=args.result)
    elif args.status or args.summary:
        ignored_inline_args = True
        append_trace(
            record,
            {
                "event": "workflow_inline_finalize_args_ignored",
                "status": args.status,
                "summary": args.summary,
            },
        )
    if record.mode in {"diagnostic", "debug"}:
        final_sources = _final_artifact_sources(record)
        _stop_container(record.container_id)
        record.status = "finalized"
        append_trace(record, {"event": "finalized", "status": "finalized", "mode": record.mode})
        registry.save(record)
        _emit(
            {
                "status": "success",
                "audit_id": args.audit_id,
                "mode": record.mode,
                "result": {"status": "diagnostic_complete" if record.mode == "diagnostic" else "debug_complete"},
                "artifact_sources": final_sources,
            }
        )
        return
    protocol_validation = _validate_sandbox_protocol(Path(record.workspace_path) / "output")
    if not protocol_validation["valid"]:
        append_trace(
            record,
            {
                "event": "finalize_rejected",
                "status": "error",
                "missing_files": protocol_validation["missing_files"],
                "error": protocol_validation["error"],
            },
        )
        registry.save(record)
        _emit(
            {
                "status": "error",
                "audit_id": args.audit_id,
                "error": protocol_validation["error"],
                "ignored_inline_args": ignored_inline_args,
                "missing_files": protocol_validation["missing_files"],
                "required_files": protocol_validation["required_files"],
                "artifact_sources": _artifact_sources_for_paths(
                    record.audit_id,
                    Path(record.workspace_path),
                    {
                        "progress": ("progress.json", "application/json"),
                        "trace": ("trace.jsonl", "application/jsonl"),
                    },
                ),
            }
        )
        return
    final_sources = _final_artifact_sources(record)
    _stop_container(record.container_id)
    record.status = "finalized"
    append_trace(record, {"event": "finalized", "status": "finalized"})
    registry.save(record)
    final_path = Path(record.workspace_path) / "output" / "final.json"
    result: dict[str, Any] = {}
    if final_path.exists():
        result = json.loads(final_path.read_text(encoding="utf-8"))
    _emit(
        {
            "status": "success",
            "audit_id": args.audit_id,
            "result": result,
            "ignored_inline_args": ignored_inline_args,
            "artifact_sources": final_sources,
        }
    )


def sandbox_read_main() -> None:
    sandbox_read_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


@sandbox_read_app.command(help="Read bounded text from a sandbox workspace file.\n\n" + SANDBOX_READ_HELP)
def _sandbox_read_cli(
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Required sandbox_run_* id.")] = "",
    path: Annotated[str, typer.Option(help="Workspace-relative file path to preview, e.g. output/candidates.json.")] = "",
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
    max_chars: Annotated[int, typer.Option("--max-chars", "--max_chars", help="Maximum preview characters to return, capped by sandbox limits.")] = 4_000,
) -> None:
    if not audit_id:
        ERROR_CONSOLE.print("[red]Missing required option '--audit-id'.[/red]")
        raise typer.Exit(2)
    if not path:
        ERROR_CONSOLE.print("[red]Missing required option '--path'.[/red]")
        raise typer.Exit(2)
    args = SimpleNamespace(
        audit_id=audit_id,
        path=path,
        user_id=user_id,
        session_id=session_id,
        app_root=app_root or _default_app_root(),
        max_chars=max_chars,
    )

    record = SandboxRegistry(args.app_root).load(args.user_id, args.session_id, args.audit_id)
    if record.status != "running":
        _emit({"status": record.status, "audit_id": args.audit_id, "error": f"sandbox is terminal: {record.status}"})
        return
    limits = SandboxLimits.model_validate(record.limits or {})
    try:
        target = workspace_path(record, args.path)
    except ValueError as exc:
        _emit({"status": "error", "audit_id": args.audit_id, "error": "path escapes workspace"})
        return
    max_chars = min(args.max_chars, limits.max_read_chars)
    text = target.read_text(encoding="utf-8", errors="replace")
    artifact_sources = []
    if len(text) > max_chars:
        artifact_sources = [_artifact_source(record.audit_id, Path(record.workspace_path), args.path, "text/plain", key="read_file")]
    _emit(
        {
            "status": "success",
            "audit_id": args.audit_id,
            "content_preview": text[:max_chars],
            "content_bytes": len(text.encode("utf-8")),
            "returned_chars": min(len(text), max_chars),
            "truncated": len(text) > max_chars,
            "artifact_sources": artifact_sources,
        }
    )


def sandbox_write_main() -> None:
    sandbox_write_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


@sandbox_write_app.command(help="Write bounded text to a sandbox workspace file.\n\n" + SANDBOX_WRITE_HELP)
def _sandbox_write_cli(
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Required sandbox_run_* id.")] = "",
    path: Annotated[str, typer.Option(help="Workspace-relative destination path, e.g. output/extractor.py or output/final.json.")] = "",
    content: Annotated[str, typer.Option(help="Complete file content to write. For JSON protocol files, this is schema-validated.")] = "",
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
) -> None:
    if not audit_id:
        ERROR_CONSOLE.print("[red]Missing required option '--audit-id'.[/red]")
        raise typer.Exit(2)
    if not path:
        ERROR_CONSOLE.print("[red]Missing required option '--path'.[/red]")
        raise typer.Exit(2)
    args = SimpleNamespace(
        audit_id=audit_id,
        path=path,
        content=content,
        user_id=user_id,
        session_id=session_id,
        app_root=app_root or _default_app_root(),
    )

    record = SandboxRegistry(args.app_root).load(args.user_id, args.session_id, args.audit_id)
    if record.status != "running":
        _emit({"status": record.status, "audit_id": args.audit_id, "error": f"sandbox is terminal: {record.status}"})
        return
    target_policy_error = _workspace_write_target_error(args.path)
    if target_policy_error:
        _emit(
            {
                "status": "error",
                "audit_id": args.audit_id,
                "error_type": "write_target_not_allowed",
                "path": args.path,
                "written": False,
                "error": target_policy_error,
            }
        )
        return
    try:
        target = workspace_path(record, args.path)
    except ValueError:
        _emit({"status": "error", "audit_id": args.audit_id, "error": "path escapes workspace"})
        return
    validation = validate_protocol_file_content(args.path, args.content)
    if not validation["valid"]:
        _emit(
            {
                "status": "error",
                "audit_id": args.audit_id,
                "error_type": "protocol_model_validation",
                "path": args.path,
                "model": validation["model"],
                "written": False,
                "errors": validation["errors"],
            }
        )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content, encoding="utf-8")
    limits = SandboxLimits.model_validate(record.limits or {})
    registry = SandboxRegistry(args.app_root)
    guardrail_record = _check_workspace_size(registry, record, limits)
    if guardrail_record is not None:
        _stop_container(guardrail_record.container_id)
        _emit(
            {
                "status": "guardrail_triggered",
                "audit_id": args.audit_id,
                "guardrail": guardrail_record.guardrail,
                "error": guardrail_record.error,
                "artifact_sources": _write_guardrail_evidence(
                    guardrail_record,
                    guardrail_record.guardrail,
                    guardrail_record.error,
                ),
            }
        )
        return
    _emit({"status": "success", "audit_id": args.audit_id, "path": args.path})


def sandbox_apply_patch_main() -> None:
    sandbox_apply_patch_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


@sandbox_apply_patch_app.command(help="Apply a targeted patch to sandbox workspace files.\n\n" + SANDBOX_APPLY_PATCH_HELP)
def _sandbox_apply_patch_cli(
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Required sandbox_run_* id.")] = "",
    path: Annotated[str, typer.Option(help="Workspace-relative file path for exact replacement mode.")] = "",
    old: Annotated[str, typer.Option(help="Exact text to replace in --path. Required with --new when --patch is omitted.")] = "",
    new: Annotated[str, typer.Option(help="Replacement text for --old in --path. Required with --old when --patch is omitted.")] = "",
    patch: Annotated[str, typer.Option(help="Unified diff text with ---/+++ file headers and @@ hunks.")] = "",
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
) -> None:
    if not audit_id:
        ERROR_CONSOLE.print("[red]Missing required option '--audit-id'.[/red]")
        raise typer.Exit(2)
    if patch and (path or old or new):
        _emit({"status": "error", "audit_id": audit_id, "error": "use either --patch or --path/--old/--new, not both"})
        return
    if not patch and not (path and old and new):
        _emit({"status": "error", "audit_id": audit_id, "error": "missing patch input; pass --patch or --path with --old and --new"})
        return

    args = SimpleNamespace(
        audit_id=audit_id,
        path=path,
        old=old,
        new=new,
        patch=patch,
        user_id=user_id,
        session_id=session_id,
        app_root=app_root or _default_app_root(),
    )

    registry = SandboxRegistry(args.app_root)
    record = registry.load(args.user_id, args.session_id, args.audit_id)
    if record.status != "running":
        _emit({"status": record.status, "audit_id": args.audit_id, "error": f"sandbox is terminal: {record.status}"})
        return
    try:
        changed_files = (
            _apply_unified_workspace_patch(record, args.patch)
            if args.patch
            else [_apply_exact_workspace_replacement(record, args.path, args.old, args.new)]
        )
    except SandboxPatchError as exc:
        _emit(
            {
                "status": "error",
                "audit_id": args.audit_id,
                "error_type": exc.error_type,
                "error": str(exc),
                "path": exc.path,
                "written": False,
            }
        )
        return

    limits = SandboxLimits.model_validate(record.limits or {})
    guardrail_record = _check_workspace_size(registry, record, limits)
    if guardrail_record is not None:
        _stop_container(guardrail_record.container_id)
        _emit(
            {
                "status": "guardrail_triggered",
                "audit_id": args.audit_id,
                "guardrail": guardrail_record.guardrail,
                "error": guardrail_record.error,
                "artifact_sources": _write_guardrail_evidence(
                    guardrail_record,
                    guardrail_record.guardrail,
                    guardrail_record.error,
                ),
            }
        )
        return

    append_trace(
        record,
        {
            "event": "patch_applied",
            "mode": "unified_diff" if args.patch else "exact_replacement",
            "changed_files": [file["path"] for file in changed_files],
        },
    )
    artifact_paths = {
        f"patched_{index}": (str(file["path"]), _mime_type_for_workspace_path(str(file["path"])))
        for index, file in enumerate(changed_files)
    }
    artifact_paths["trace"] = ("trace.jsonl", "application/jsonl")
    _emit(
        {
            "status": "success",
            "audit_id": args.audit_id,
            "mode": "unified_diff" if args.patch else "exact_replacement",
            "changed_files": changed_files,
            "artifact_sources": _artifact_sources_for_paths(record.audit_id, Path(record.workspace_path), artifact_paths),
        }
    )


def sandbox_progress_main() -> None:
    sandbox_progress_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


@sandbox_progress_app.command(help="Write a compact sandbox progress file.\n\n" + SANDBOX_PROGRESS_HELP)
def _sandbox_progress_cli(
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Required sandbox_run_* id.")] = "",
    progress_json: Annotated[str, typer.Option("--progress-json", "--progress_json", help="Compact JSON progress object; no raw HTML or long terminal output.")] = "",
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
) -> None:
    if not audit_id:
        ERROR_CONSOLE.print("[red]Missing required option '--audit-id'.[/red]")
        raise typer.Exit(2)
    if not progress_json:
        ERROR_CONSOLE.print("[red]Missing required option '--progress-json'.[/red]")
        raise typer.Exit(2)
    args = SimpleNamespace(
        audit_id=audit_id,
        progress_json=progress_json,
        user_id=user_id,
        session_id=session_id,
        app_root=app_root or _default_app_root(),
    )

    registry = SandboxRegistry(args.app_root)
    record = registry.load(args.user_id, args.session_id, args.audit_id)
    if record.status != "running":
        _emit({"status": record.status, "audit_id": args.audit_id, "error": f"sandbox is terminal: {record.status}"})
        return
    target = Path(record.workspace_path) / "progress.json"
    target.write_text(json.dumps(json.loads(args.progress_json), ensure_ascii=True, indent=2), encoding="utf-8")
    append_trace(record, {"event": "progress_updated", "path": "progress.json"})
    _emit(
        {
            "status": "success",
            "audit_id": args.audit_id,
            "path": "progress.json",
            "artifact_sources": _artifact_sources_for_paths(
                record.audit_id,
                Path(record.workspace_path),
                {"progress": ("progress.json", "application/json"), "trace": ("trace.jsonl", "application/jsonl")},
            ),
        }
    )


def sandbox_litellm_call_main() -> None:
    sandbox_litellm_call_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


def sandbox_cleanup_main() -> None:
    sandbox_cleanup_app(args=_normalized_argv(), prog_name=Path(sys.argv[0]).name, standalone_mode=False)


@sandbox_cleanup_app.command(help="Clean up stale Docker sandbox containers.\n\n" + SANDBOX_CLEANUP_HELP)
def _sandbox_cleanup_cli(
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
    max_age_seconds: Annotated[int, typer.Option("--max-age-seconds", "--max_age_seconds", help="Minimum inactive age before a running registry record is stale.")] = 900,
    include_orphans: Annotated[bool, typer.Option("--include-orphans/--exclude-orphans", help="Also remove labeled containers not referenced by any registry record.")] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Preview cleanup without removing containers.")] = True,
) -> None:
    _emit(
        _cleanup_stale_sandbox_containers(
            app_root=app_root or _default_app_root(),
            max_age_seconds=max_age_seconds,
            include_orphans=include_orphans,
            dry_run=dry_run,
        )
    )


@sandbox_litellm_call_app.command(help="Run a generic host-mediated LiteLLM call for sandbox analysis.\n\n" + SANDBOX_LITELLM_CALL_HELP)
def _sandbox_litellm_call_cli(
    audit_id: Annotated[str, typer.Option("--audit-id", "--audit_id", help="Required sandbox_run_* id.")] = "",
    messages_json: Annotated[str, typer.Option("--messages-json", "--messages_json", help="JSON list of OpenAI-style {role, content} messages.")] = "",
    response_format_json: Annotated[str, typer.Option("--response-format-json", "--response_format_json", help="Optional JSON response_format passed to LiteLLM, e.g. {\"type\":\"json_object\"}.")] = "",
    output_path: Annotated[str, typer.Option("--output-path", "--output_path", help="Optional workspace-relative path to persist response payload JSON.")] = "",
    model: Annotated[str, typer.Option(help="LiteLLM model name. Defaults to SANDBOX_LLM_MODEL, then JOB_SCRAPER_LLM_MODEL, then OPENAI_MODEL.")] = "",
    max_tokens: Annotated[int, typer.Option("--max-tokens", "--max_tokens", help="Maximum model output tokens.")] = 700,
    temperature: Annotated[float, typer.Option(help="Model temperature.")] = 0.0,
    user_id: Annotated[str, typer.Option("--user-id", "--user_id", help="ADK user id for registry lookup. Usually omit.")] = "user",
    session_id: Annotated[str, typer.Option("--session-id", "--session_id", help="ADK session id for registry lookup. Usually omit.")] = "local",
    app_root: Annotated[str, typer.Option("--app-root", "--app_root", help="ADK app root containing .adk runtime state. Usually omit.")] = "",
) -> None:
    if not audit_id:
        ERROR_CONSOLE.print("[red]Missing required option '--audit-id'.[/red]")
        raise typer.Exit(2)
    if not messages_json:
        ERROR_CONSOLE.print("[red]Missing required option '--messages-json'.[/red]")
        raise typer.Exit(2)
    args = SimpleNamespace(
        audit_id=audit_id,
        messages_json=messages_json,
        response_format_json=response_format_json,
        output_path=output_path,
        model=(
            model
            or os.getenv("SANDBOX_LLM_MODEL")
            or os.getenv("JOB_SCRAPER_LLM_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "openai/gpt-5.4-mini"
        ),
        max_tokens=max_tokens,
        temperature=temperature,
        user_id=user_id,
        session_id=session_id,
        app_root=app_root or _default_app_root(),
    )
    try:
        messages = _parse_litellm_messages(args.messages_json)
        response_format = _parse_optional_json_object(args.response_format_json, field_name="response_format_json")
    except ValueError as exc:
        _emit({"status": "error", "audit_id": args.audit_id, "error_type": "invalid_arguments", "error": str(exc)})
        return

    registry = SandboxRegistry(args.app_root)
    record = registry.load(args.user_id, args.session_id, args.audit_id)
    if record.status != "running":
        _emit({"status": record.status, "audit_id": args.audit_id, "error": f"sandbox is terminal: {record.status}"})
        return
    limits = SandboxLimits.model_validate(record.limits or {})
    output_target: Path | None = None
    if args.output_path:
        try:
            output_target = workspace_path(record, args.output_path)
        except ValueError:
            _emit({"status": "error", "audit_id": args.audit_id, "error": "path escapes workspace"})
            return

    started = perf_counter()
    try:
        content, usage = asyncio.run(
            _run_litellm_messages(
                model=args.model,
                messages=messages,
                response_format=response_format,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )
        )
    except Exception as exc:
        append_trace(
            record,
            {
                "event": "llm_call",
                "status": "error",
                "model": args.model,
                "duration_ms": int((perf_counter() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        _emit({"status": "error", "audit_id": args.audit_id, "error_type": "llm_call_failed", "error": str(exc)})
        return

    duration_ms = int((perf_counter() - started) * 1000)
    response_payload = {
        "status": "success",
        "audit_id": args.audit_id,
        "model": args.model,
        "content": content,
        "usage": usage,
        "duration_ms": duration_ms,
    }
    output_source: dict[str, object] | None = None
    if output_target is not None:
        output_target.parent.mkdir(parents=True, exist_ok=True)
        output_target.write_text(json.dumps(response_payload, ensure_ascii=True, indent=2), encoding="utf-8")
        guardrail_record = _check_workspace_size(registry, record, limits)
        if guardrail_record is not None:
            _stop_container(guardrail_record.container_id)
            _emit(
                {
                    "status": "guardrail_triggered",
                    "audit_id": args.audit_id,
                    "guardrail": guardrail_record.guardrail,
                    "error": guardrail_record.error,
                    "artifact_sources": _write_guardrail_evidence(
                        guardrail_record,
                        guardrail_record.guardrail,
                        guardrail_record.error,
                    ),
                }
            )
            return
        output_source = _artifact_source(record.audit_id, Path(record.workspace_path), args.output_path, "application/json", key="llm_response")

    append_trace(
        record,
        {
            "event": "llm_call",
            "status": "success",
            "model": args.model,
            "message_count": len(messages),
            "output_path": args.output_path,
            "content_chars": len(content),
            "duration_ms": duration_ms,
            "usage": usage,
        },
    )
    artifact_sources = [output_source] if output_source else []
    content_limit = limits.max_read_chars
    _emit(
        {
            "status": "success",
            "audit_id": args.audit_id,
            "model": args.model,
            "content": content[:content_limit],
            "content_truncated": len(content) > content_limit,
            "output_path": args.output_path,
            "duration_ms": duration_ms,
            "usage": usage,
            "artifact_sources": artifact_sources,
        }
    )


def _materialize_workspace_files(workspace: Path, files: list[dict[str, str]]) -> None:
    for item in files:
        source = Path(item["source_path"])
        sandbox_path = Path(str(item["sandbox_path"]).lstrip("/"))
        target = workspace / sandbox_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _materialize_skill_helpers(workspace: Path) -> None:
    skill_dir = Path(__file__).resolve().parents[2] / "skills" / "sandbox-page-analyst"
    if not skill_dir.exists():
        return
    for folder_name in ("scripts", "references", "schemas"):
        source_dir = skill_dir / folder_name
        if not source_dir.exists():
            continue
        target_dir = workspace / folder_name
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)


def _workspace_file_specs(workspace_files_json: str, page_artifacts: list[str]) -> list[dict[str, str]]:
    files = json.loads(workspace_files_json)
    if not isinstance(files, list):
        raise ValueError("workspace-files-json must be a JSON list")
    for index, page_artifact in enumerate(page_artifacts, start=1):
        source_path = Path(page_artifact)
        sandbox_name = "page.html" if index == 1 else f"page_{index}.html"
        files.append({"source_path": str(source_path), "sandbox_path": sandbox_name})
    return files


def _parse_litellm_messages(messages_json: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(messages_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"messages_json must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("messages_json must be a non-empty JSON list")
    messages: list[dict[str, str]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"message {index} must be an object")
        role = str(item.get("role") or "").strip()
        content = item.get("content")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"message {index} has unsupported role: {role!r}")
        if not isinstance(content, str):
            raise ValueError(f"message {index} content must be a string")
        messages.append({"role": role, "content": content})
    return messages


def _parse_optional_json_object(value: str, *, field_name: str) -> dict[str, object] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return payload


async def _run_litellm_messages(
    *,
    model: str,
    messages: list[dict[str, str]],
    response_format: dict[str, object] | None,
    max_tokens: int,
    temperature: float,
) -> tuple[str, dict[str, object]]:
    from litellm import acompletion

    kwargs: dict[str, object] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    response = await acompletion(**kwargs)
    usage_obj = getattr(response, "usage", None)
    usage: dict[str, object] = {}
    if usage_obj is not None:
        try:
            usage = dict(usage_obj)
        except Exception:
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", None),
                "completion_tokens": getattr(usage_obj, "completion_tokens", None),
                "total_tokens": getattr(usage_obj, "total_tokens", None),
            }
    return str(response.choices[0].message.content or "").strip(), usage


def _write_inline_final_result(
    record: SandboxSessionRecord,
    *,
    status: str,
    summary: str,
    result_json: str,
) -> None:
    if not any((status, summary, result_json)):
        return
    final_path = Path(record.workspace_path) / "output" / "final.json"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    result: Any = {}
    if result_json:
        result = json.loads(result_json)
    payload = {
        "status": status or "needs_review",
        "output_schema": "job_extraction",
        "summary": summary,
        "result": result,
        "protocol": {"valid": status == "success", "warnings": [] if status == "success" else [summary]},
    }
    final_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _initialize_workspace_protocol(
    workspace: Path,
    audit_id: str,
    limits: SandboxLimits,
    *,
    mode: str = "workflow",
    source_url: str = "",
    image: str = DEFAULT_IMAGE,
    ignored_args: list[str] | None = None,
    workspace_files: list[dict[str, str]] | None = None,
) -> None:
    (workspace / "commands").mkdir(parents=True, exist_ok=True)
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o777)
    (workspace / "commands").chmod(0o777)
    (workspace / "output").chmod(0o777)
    (workspace / "trace.jsonl").write_text("", encoding="utf-8")
    (workspace / "plan.md").write_text(
        "# Sandbox Page Analysis Plan\n\n"
        "Use bounded shell commands to profile mounted page artifacts, extract candidate jobs, validate outputs, "
        "and write final results under output/final.json.\n",
        encoding="utf-8",
    )
    progress = {
        "audit_id": audit_id,
        "current_stage": "initialized",
        "completed_steps": [],
        "open_questions": [],
        "next_steps": ["profile mounted page artifacts", "write extraction outputs", "validate final JSON"],
        "blockers": [],
        "source_url": source_url,
        "mode": mode,
        "ignored_args": ignored_args or [],
        "limits": limits.model_dump(),
        "sandbox_image": image,
        "approved_parser_imports": list(APPROVED_SANDBOX_PARSER_IMPORTS),
        "approved_tools": list(APPROVED_SANDBOX_TOOLS),
    }
    (workspace / "progress.json").write_text(json.dumps(progress, ensure_ascii=True, indent=2), encoding="utf-8")
    policy = {
        "audit_id": audit_id,
        "network_mode": "none",
        "mode": mode,
        "read_only_rootfs": True,
        "user": "65532:65532",
        "image": image,
        "approved_parser_imports": list(APPROVED_SANDBOX_PARSER_IMPORTS),
        "approved_tools": list(APPROVED_SANDBOX_TOOLS),
        "limits": limits.model_dump(),
    }
    (workspace / "policy.json").write_text(json.dumps(policy, ensure_ascii=True, indent=2), encoding="utf-8")
    inputs = {
        "audit_id": audit_id,
        "source_url": source_url,
        "mode": mode,
        "workspace_files": workspace_files or [],
    }
    (workspace / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=True, indent=2), encoding="utf-8")


def _normalized_argv() -> list[str]:
    return [arg for arg in sys.argv[1:] if arg != "--"]


def _default_app_root() -> str:
    if DEFAULT_APP_ROOT:
        return DEFAULT_APP_ROOT
    spec = importlib.util.find_spec("job_scraper")
    if spec and spec.origin:
        return str(Path(spec.origin).resolve().parent)
    return str((Path.cwd() / "src/job_scraper").resolve())


def _cleanup_stale_sandbox_containers(
    *,
    app_root: str,
    max_age_seconds: int,
    include_orphans: bool,
    dry_run: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    docker_cli = _resolve_docker_cli()
    if docker_cli is None:
        return {
            "status": "error",
            "error_type": "docker_cli_unavailable",
            "error": "Docker CLI is unavailable; cannot clean sandbox containers.",
        }
    now = now or datetime.now(timezone.utc)
    registry = SandboxRegistry(app_root)
    records = registry.iter_records()
    containers = _list_sandbox_docker_containers(docker_cli)
    if containers.get("status") == "error":
        return containers
    containers_by_id: dict[str, dict[str, Any]] = containers["containers"]
    referenced_container_ids = {record.container_id for record in records if record.container_id}
    candidates: list[dict[str, Any]] = []

    for record in records:
        container = _find_container(containers_by_id, record.container_id)
        if container is None:
            continue
        record_age = _age_seconds(record.updated_at or record.created_at, now)
        terminal = record.status != "running"
        stale_running = record.status == "running" and record_age >= max(0, max_age_seconds)
        if not terminal and not stale_running:
            continue
        candidates.append(
            {
                "audit_id": record.audit_id,
                "container_id": container["id"],
                "container_name": container.get("name", ""),
                "registry_status": record.status,
                "reason": "terminal_registry_record" if terminal else "stale_running_registry_record",
                "age_seconds": int(record_age),
                "updated_at": record.updated_at,
            }
        )

    if include_orphans:
        for container in containers_by_id.values():
            if _container_is_referenced(container["id"], referenced_container_ids):
                continue
            container_age = _age_seconds(str(container.get("created_at") or ""), now)
            if container_age < max(0, max_age_seconds):
                continue
            candidates.append(
                {
                    "audit_id": "",
                    "container_id": container["id"],
                    "container_name": container.get("name", ""),
                    "registry_status": "",
                    "reason": "orphan_labeled_container",
                    "age_seconds": int(container_age),
                    "updated_at": "",
                }
            )

    removed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not dry_run:
        for candidate in candidates:
            result = _remove_sandbox_container(docker_cli, str(candidate["container_id"]))
            if result["status"] == "success":
                removed.append(candidate)
                _mark_stale_record_cleaned(registry, records, candidate, now)
            else:
                errors.append({**candidate, **result})

    return {
        "status": "success" if not errors else "partial_error",
        "dry_run": dry_run,
        "max_age_seconds": max_age_seconds,
        "include_orphans": include_orphans,
        "candidate_count": len(candidates),
        "removed_count": len(removed),
        "error_count": len(errors),
        "candidates": candidates,
        "removed": removed,
        "errors": errors,
    }


def _list_sandbox_docker_containers(docker_cli: str) -> dict[str, Any]:
    listed = subprocess.run(
        [docker_cli, "ps", "-a", "--filter", "label=job_scraper_sandbox=true", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if listed.returncode != 0:
        return {"status": "error", "error_type": "docker_ps_failed", "error": listed.stderr.strip()}
    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return {"status": "success", "containers": {}}
    inspected = subprocess.run([docker_cli, "inspect", *ids], capture_output=True, text=True, timeout=20)
    if inspected.returncode != 0:
        return {"status": "error", "error_type": "docker_inspect_failed", "error": inspected.stderr.strip()}
    containers: dict[str, dict[str, Any]] = {}
    for item in json.loads(inspected.stdout):
        container_id = str(item.get("Id") or "")
        labels = ((item.get("Config") or {}).get("Labels") or {})
        if not container_id or labels.get("job_scraper_sandbox") != "true":
            continue
        containers[container_id] = {
            "id": container_id,
            "short_id": container_id[:12],
            "name": str(item.get("Name") or "").lstrip("/"),
            "image": str((item.get("Config") or {}).get("Image") or ""),
            "created_at": str(item.get("Created") or ""),
            "state": str((item.get("State") or {}).get("Status") or ""),
        }
    return {"status": "success", "containers": containers}


def _remove_sandbox_container(docker_cli: str, container_id: str) -> dict[str, Any]:
    removed = subprocess.run([docker_cli, "rm", "-f", container_id], capture_output=True, text=True, timeout=15)
    if removed.returncode == 0:
        return {"status": "success"}
    return {"status": "error", "error_type": "docker_rm_failed", "error": removed.stderr.strip()}


def _mark_stale_record_cleaned(
    registry: SandboxRegistry,
    records: list[SandboxSessionRecord],
    candidate: dict[str, Any],
    now: datetime,
) -> None:
    audit_id = str(candidate.get("audit_id") or "")
    if not audit_id:
        return
    for record in records:
        if record.audit_id != audit_id:
            continue
        if record.status == "running":
            record.status = "guardrail_triggered"
            record.guardrail = "stale_sandbox_cleanup"
            record.error = "Sandbox container was removed by stale sandbox cleanup after inactivity."
            record.updated_at = now.isoformat()
            registry.save(record)
        return


def _find_container(containers_by_id: dict[str, dict[str, Any]], container_id: str) -> dict[str, Any] | None:
    if not container_id:
        return None
    for full_id, container in containers_by_id.items():
        if full_id.startswith(container_id) or container_id.startswith(full_id):
            return container
    return None


def _container_is_referenced(container_id: str, referenced_container_ids: set[str]) -> bool:
    return any(container_id.startswith(recorded) or recorded.startswith(container_id) for recorded in referenced_container_ids)


def _age_seconds(value: str, now: datetime) -> float:
    parsed = _parse_datetime(value)
    if parsed is None:
        return 0.0
    return max(0.0, (now - parsed).total_seconds())


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    value = re.sub(r"\.([0-9]{6})[0-9]+", r".\1", value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sandbox_visible_files(workspace: Path) -> list[str]:
    visible: list[str] = []
    for path in sorted(workspace.iterdir()):
        name = path.name
        if path.is_dir():
            name = f"{name}/"
        visible.append(name)
    return visible


class SandboxPatchError(Exception):
    def __init__(self, message: str, *, error_type: str = "patch_error", path: str = "") -> None:
        super().__init__(message)
        self.error_type = error_type
        self.path = path


def _apply_exact_workspace_replacement(
    record: SandboxSessionRecord,
    relative_path: str,
    old: str,
    new: str,
) -> dict[str, object]:
    target_policy_error = _workspace_write_target_error(relative_path)
    if target_policy_error:
        raise SandboxPatchError(target_policy_error, error_type="write_target_not_allowed", path=relative_path)
    try:
        target = workspace_path(record, relative_path)
    except ValueError as exc:
        raise SandboxPatchError(str(exc), error_type="path_escapes_workspace", path=relative_path) from exc
    if not target.exists() or not target.is_file():
        raise SandboxPatchError("target file does not exist", error_type="target_missing", path=relative_path)

    original = target.read_text(encoding="utf-8", errors="replace")
    occurrences = original.count(old)
    if occurrences != 1:
        raise SandboxPatchError(
            f"exact replacement expected one match, found {occurrences}",
            error_type="patch_context_mismatch",
            path=relative_path,
        )
    updated = original.replace(old, new, 1)
    _validate_patched_content(relative_path, updated)
    target.write_text(updated, encoding="utf-8")
    return _changed_file_summary(relative_path, original, updated)


def _apply_unified_workspace_patch(record: SandboxSessionRecord, patch_text: str) -> list[dict[str, object]]:
    if patch_text.lstrip().startswith("*** Begin Patch"):
        return _apply_codex_workspace_patch(record, patch_text)

    sections = _parse_unified_patch_sections(patch_text)
    if not sections:
        raise SandboxPatchError("unified patch did not contain any file sections", error_type="invalid_patch")

    changed_files: list[dict[str, object]] = []
    for section in sections:
        relative_path = section["path"]
        target_policy_error = _workspace_write_target_error(relative_path)
        if target_policy_error:
            raise SandboxPatchError(target_policy_error, error_type="write_target_not_allowed", path=relative_path)
        try:
            target = workspace_path(record, relative_path)
        except ValueError as exc:
            raise SandboxPatchError(str(exc), error_type="path_escapes_workspace", path=relative_path) from exc
        if not target.exists() or not target.is_file():
            raise SandboxPatchError("target file does not exist", error_type="target_missing", path=relative_path)
        original = target.read_text(encoding="utf-8", errors="replace")
        updated = _apply_unified_hunks(original, section["hunks"], relative_path)
        _validate_patched_content(relative_path, updated)
        target.write_text(updated, encoding="utf-8")
        changed_files.append(_changed_file_summary(relative_path, original, updated))
    return changed_files


def _apply_codex_workspace_patch(record: SandboxSessionRecord, patch_text: str) -> list[dict[str, object]]:
    sections = _parse_codex_patch_sections(patch_text)
    if not sections:
        raise SandboxPatchError("codex patch did not contain any update file sections", error_type="invalid_patch")

    changed_files: list[dict[str, object]] = []
    for section in sections:
        relative_path = section["path"]
        target_policy_error = _workspace_write_target_error(relative_path)
        if target_policy_error:
            raise SandboxPatchError(target_policy_error, error_type="write_target_not_allowed", path=relative_path)
        try:
            target = workspace_path(record, relative_path)
        except ValueError as exc:
            raise SandboxPatchError(str(exc), error_type="path_escapes_workspace", path=relative_path) from exc
        if not target.exists() or not target.is_file():
            raise SandboxPatchError("target file does not exist", error_type="target_missing", path=relative_path)
        original = target.read_text(encoding="utf-8", errors="replace")
        updated = original
        for hunk in section["hunks"]:
            old_block = "\n".join(hunk["old"])
            new_block = "\n".join(hunk["new"])
            if old_block and old_block not in updated:
                raise SandboxPatchError(
                    "codex patch context does not match current file",
                    error_type="patch_context_mismatch",
                    path=relative_path,
                )
            if old_block:
                updated = updated.replace(old_block, new_block, 1)
            elif new_block:
                updated = new_block + ("\n" if updated and not new_block.endswith("\n") else "") + updated
        _validate_patched_content(relative_path, updated)
        target.write_text(updated, encoding="utf-8")
        changed_files.append(_changed_file_summary(relative_path, original, updated))
    return changed_files


def _parse_codex_patch_sections(patch_text: str) -> list[dict[str, Any]]:
    lines = patch_text.splitlines()
    sections: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("*** Update File: "):
            index += 1
            continue
        path = _strip_diff_prefix(line.split(":", 1)[1].strip())
        index += 1
        hunks: list[dict[str, list[str]]] = []
        while index < len(lines) and not lines[index].startswith("*** Update File: ") and lines[index] != "*** End Patch":
            if not lines[index].startswith("@@"):
                index += 1
                continue
            index += 1
            old_lines: list[str] = []
            new_lines: list[str] = []
            while index < len(lines):
                hunk_line = lines[index]
                if hunk_line.startswith("@@") or hunk_line.startswith("*** Update File: ") or hunk_line == "*** End Patch":
                    break
                if hunk_line.startswith("-"):
                    old_lines.append(hunk_line[1:])
                elif hunk_line.startswith("+"):
                    new_lines.append(hunk_line[1:])
                elif hunk_line.startswith(" "):
                    content = hunk_line[1:]
                    old_lines.append(content)
                    new_lines.append(content)
                elif hunk_line.startswith("\\"):
                    pass
                elif hunk_line:
                    raise SandboxPatchError("invalid codex patch hunk line", error_type="invalid_patch", path=path)
                index += 1
            if old_lines or new_lines:
                hunks.append({"old": old_lines, "new": new_lines})
        sections.append({"path": path, "hunks": hunks})
    return sections


def _parse_unified_patch_sections(patch_text: str) -> list[dict[str, Any]]:
    lines = patch_text.splitlines()
    sections: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise SandboxPatchError("unified patch file header is missing +++ line", error_type="invalid_patch")
        old_path = _unified_header_path(lines[index][4:])
        new_path = _unified_header_path(lines[index + 1][4:])
        if new_path == "/dev/null" or old_path == "/dev/null":
            raise SandboxPatchError("creating or deleting files is not supported by sandbox_apply_patch.py", error_type="unsupported_patch")
        path = _strip_diff_prefix(new_path)
        index += 2
        hunks: list[dict[str, Any]] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            if not lines[index].startswith("@@ "):
                index += 1
                continue
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", lines[index])
            if not match:
                raise SandboxPatchError("invalid unified hunk header", error_type="invalid_patch", path=path)
            index += 1
            hunk_lines: list[str] = []
            while index < len(lines) and not lines[index].startswith("@@ ") and not lines[index].startswith("--- "):
                hunk_lines.append(lines[index])
                index += 1
            hunks.append({"old_start": int(match.group(1)), "lines": hunk_lines})
        sections.append({"path": path, "hunks": hunks})
    return sections


def _apply_unified_hunks(original: str, hunks: list[dict[str, Any]], relative_path: str) -> str:
    original_lines = original.splitlines()
    updated_lines: list[str] = []
    source_index = 0
    for hunk in hunks:
        hunk_index = int(hunk["old_start"]) - 1
        if hunk_index < source_index or hunk_index > len(original_lines):
            raise SandboxPatchError("hunk location does not match current file", error_type="patch_context_mismatch", path=relative_path)
        updated_lines.extend(original_lines[source_index:hunk_index])
        source_index = hunk_index
        for line in hunk["lines"]:
            if line.startswith("\\"):
                continue
            if not line:
                raise SandboxPatchError("invalid hunk line without prefix", error_type="invalid_patch", path=relative_path)
            prefix, content = line[0], line[1:]
            if prefix == " ":
                _assert_hunk_context(original_lines, source_index, content, relative_path)
                updated_lines.append(content)
                source_index += 1
            elif prefix == "-":
                _assert_hunk_context(original_lines, source_index, content, relative_path)
                source_index += 1
            elif prefix == "+":
                updated_lines.append(content)
            else:
                raise SandboxPatchError(f"invalid hunk line prefix: {prefix}", error_type="invalid_patch", path=relative_path)
    updated_lines.extend(original_lines[source_index:])
    trailing_newline = "\n" if original.endswith("\n") else ""
    return "\n".join(updated_lines) + trailing_newline


def _assert_hunk_context(original_lines: list[str], index: int, expected: str, relative_path: str) -> None:
    if index >= len(original_lines) or original_lines[index] != expected:
        raise SandboxPatchError(
            "hunk context does not match current file",
            error_type="patch_context_mismatch",
            path=relative_path,
        )


def _unified_header_path(value: str) -> str:
    return value.strip().split("\t", 1)[0].split(" ", 1)[0]


def _strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _workspace_write_target_error(relative_path: str) -> str:
    normalized = _normalize_workspace_relative_path(relative_path)
    if not normalized:
        return "workspace write path is required"
    if normalized.startswith("/") or _path_has_parent_segment(normalized):
        return "workspace write path must be workspace-relative and must not contain parent directory segments"
    if normalized == "progress.json" or normalized.startswith("output/"):
        return ""
    return (
        "workspace write target is read-only by policy. Modify only generated sandbox artifacts under "
        "`output/` or `progress.json`; inspect mounted scripts, schemas, references, inputs, and page files "
        "as read-only contracts instead."
    )


def _normalize_workspace_relative_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _path_has_parent_segment(path: str) -> bool:
    return any(part == ".." for part in path.split("/"))


def _validate_patched_content(relative_path: str, content: str) -> None:
    validation = validate_protocol_file_content(relative_path, content)
    if validation["valid"]:
        return
    raise SandboxPatchError(
        "patched content failed protocol validation",
        error_type="protocol_model_validation",
        path=relative_path,
    )


def _changed_file_summary(relative_path: str, original: str, updated: str) -> dict[str, object]:
    return {
        "path": relative_path,
        "bytes_before": len(original.encode("utf-8")),
        "bytes_after": len(updated.encode("utf-8")),
        "sha256_before": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "sha256_after": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
    }


def _mime_type_for_workspace_path(relative_path: str) -> str:
    suffix = Path(relative_path).suffix
    if suffix == ".json":
        return "application/json"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".py":
        return "text/x-python"
    return "text/plain"


def _write_command_evidence(
    *,
    record: SandboxSessionRecord,
    command_index: int,
    command: str,
    stdout: str | bytes,
    stderr: str | bytes,
    exit_code: int | None,
    duration_ms: int,
    status: str,
    guardrail: str = "",
) -> dict[str, dict[str, object]]:
    command_dir = workspace_path(record, "commands")
    command_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{command_index:03d}"
    stdout_text = _ensure_text(stdout)
    stderr_text = _ensure_text(stderr)
    command_path = command_dir / f"{prefix}.command.txt"
    stdout_path = command_dir / f"{prefix}.stdout.txt"
    stderr_path = command_dir / f"{prefix}.stderr.txt"
    command_path.write_text(command, encoding="utf-8")
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    stdout_bytes = len(stdout_text.encode("utf-8"))
    stderr_bytes = len(stderr_text.encode("utf-8"))
    append_trace(
        record,
        {
            "event": "command",
            "command_index": command_index,
            "command_path": f"commands/{prefix}.command.txt",
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "status": status,
            "guardrail": guardrail,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "stdout_path": f"commands/{prefix}.stdout.txt",
            "stderr_path": f"commands/{prefix}.stderr.txt",
        },
    )
    return {
        "command": {"path": f"commands/{prefix}.command.txt", "mime_type": "text/plain", "bytes": len(command.encode("utf-8"))},
        "stdout": {"path": f"commands/{prefix}.stdout.txt", "mime_type": "text/plain", "bytes": stdout_bytes},
        "stderr": {"path": f"commands/{prefix}.stderr.txt", "mime_type": "text/plain", "bytes": stderr_bytes},
        "trace": {"path": "trace.jsonl", "mime_type": "application/jsonl"},
    }


def _artifact_sources_from_artifacts(
    record: SandboxSessionRecord,
    artifacts: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    workspace = Path(record.workspace_path)
    sources: list[dict[str, object]] = []
    for key, artifact in artifacts.items():
        relative_path = str(artifact.get("path") or "")
        mime_type = str(artifact.get("mime_type") or "application/octet-stream")
        if not relative_path:
            continue
        sources.append(_artifact_source(record.audit_id, workspace, relative_path, mime_type, key=key))
    return sources


def _bounded_command_output(
    *,
    artifacts: dict[str, dict[str, object]],
    stdout: str,
    stderr: str,
    limits: SandboxLimits,
    exit_code: int | None,
) -> dict[str, object]:
    limit = max(0, limits.max_read_chars)
    stdout_truncated = len(stdout) > limit
    stderr_truncated = len(stderr) > limit
    stdout_path = str(artifacts.get("stdout", {}).get("path") or "")
    stderr_path = str(artifacts.get("stderr", {}).get("path") or "")
    command_path = str(artifacts.get("command", {}).get("path") or "")

    messages: list[str] = []
    if stdout_truncated:
        messages.append(
            "stdout exceeded the direct return limit "
            f"({len(stdout.encode('utf-8'))} bytes; returning stdout preview with first {limit} chars)."
        )
    if stderr_truncated:
        messages.append(
            "stderr exceeded the direct return limit "
            f"({len(stderr.encode('utf-8'))} bytes; returning stderr preview with first {limit} chars)."
        )
    if not messages:
        messages.append("Command output fit within the context return limit.")
    if exit_code not in (None, 0):
        messages.append(f"Command exited with code {exit_code}.")

    return {
        "stdout": stdout[:limit],
        "stderr": stderr[:limit],
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "returned_stdout_chars": min(len(stdout), limit),
        "returned_stderr_chars": min(len(stderr), limit),
        "message": " ".join(messages),
        "paths": {
            "command_path": command_path,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
        },
    }


def _protocol_artifact_sources(audit_id: str, workspace: Path) -> list[dict[str, object]]:
    return _artifact_sources_for_paths(
        audit_id,
        workspace,
        {
            "policy": ("policy.json", "application/json"),
            "inputs": ("inputs.json", "application/json"),
            "plan": ("plan.md", "text/markdown"),
            "progress": ("progress.json", "application/json"),
            "trace": ("trace.jsonl", "application/jsonl"),
        },
    )


def _final_artifact_sources(record: SandboxSessionRecord) -> list[dict[str, object]]:
    workspace = Path(record.workspace_path)
    paths = {
        "progress": ("progress.json", "application/json"),
        "trace": ("trace.jsonl", "application/jsonl"),
    }
    output_mime_types = {
        ".json": "application/json",
        ".md": "text/markdown",
        ".py": "text/x-python",
    }
    for relative_dir in ("output", "scratch"):
        artifact_dir = workspace / relative_dir
        for output_file in sorted(artifact_dir.rglob("*")) if artifact_dir.exists() else []:
            mime_type = output_mime_types.get(output_file.suffix)
            if not output_file.is_file() or not mime_type:
                continue
            key = f"{relative_dir}_{output_file.relative_to(artifact_dir).with_suffix('').as_posix().replace('/', '_')}"
            paths[key] = (str(output_file.relative_to(workspace)), mime_type)
    return _artifact_sources_for_paths(record.audit_id, workspace, paths)


def _validate_sandbox_protocol(output_dir: Path) -> dict[str, object]:
    required_files = [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/extraction_run.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
        "output/run_summary.md",
    ]
    missing_files = [relative for relative in required_files if not (output_dir.parent / relative).exists()]
    if missing_files:
        return {
            "valid": False,
            "error": f"missing required sandbox protocol outputs: {', '.join(missing_files)}",
            "missing_files": missing_files,
            "required_files": required_files,
        }

    trusted_error = _trusted_protocol_validation_error(output_dir)
    if trusted_error:
        return _invalid_protocol(trusted_error, required_files)

    try:
        page_profile = _load_json_file(output_dir / "page_profile.json")
        extraction_strategy = _load_json_file(output_dir / "extraction_strategy.json")
        candidates = _load_json_file(output_dir / "candidates.json")
        validation = _load_json_file(output_dir / "validation.json")
        final = _load_json_file(output_dir / "final.json")
    except Exception as exc:
        return {
            "valid": False,
            "error": f"invalid sandbox protocol JSON: {exc}",
            "missing_files": [],
            "required_files": required_files,
        }

    if "jobs" not in candidates:
        if "result" in candidates:
            return _invalid_protocol(
                "output/candidates.json must contain top-level jobs/crawl; do not use the final-result envelope "
                "{status, result: {jobs: [...]}} for candidates.json",
                required_files,
            )
        return _invalid_protocol("output/candidates.json must contain jobs as a list", required_files)
    jobs = candidates.get("jobs")
    if not isinstance(jobs, list):
        return _invalid_protocol("output/candidates.json must contain jobs as a list", required_files)
    if not isinstance(candidates.get("crawl"), dict):
        return _invalid_protocol("output/candidates.json must contain crawl as an object", required_files)
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            return _invalid_protocol(f"output/candidates.json job {index} must be an object", required_files)
        if not str(job.get("title") or "").strip():
            return _invalid_protocol(f"output/candidates.json job {index} missing title", required_files)
        if not str(job.get("job_url") or "").strip():
            return _invalid_protocol(f"output/candidates.json job {index} missing job_url", required_files)
        type_error = _validate_job_types(job, f"output/candidates.json job {index}")
        if type_error:
            return _invalid_protocol(type_error, required_files)
        url_error = _validate_job_url(job, f"output/candidates.json job {index}")
        if url_error:
            return _invalid_protocol(url_error, required_files)
    if validation.get("valid") is not True:
        return _invalid_protocol("output/validation.json must set valid=true before finalization", required_files)
    final_result = final.get("result")
    if not isinstance(final_result, dict):
        return _invalid_protocol("output/final.json must contain result as an object", required_files)
    final_jobs = final_result.get("jobs")
    if not isinstance(final_jobs, list):
        return _invalid_protocol("output/final.json result.jobs must be a list", required_files)
    if len(final_jobs) != len(jobs):
        return _invalid_protocol("output/final.json result.jobs count must match output/candidates.json jobs", required_files)
    for index, job in enumerate(final_jobs):
        if not isinstance(job, dict):
            return _invalid_protocol(f"output/final.json result.jobs[{index}] must be an object", required_files)
        type_error = _validate_job_types(job, f"output/final.json result.jobs[{index}]")
        if type_error:
            return _invalid_protocol(type_error, required_files)
        url_error = _validate_job_url(job, f"output/final.json result.jobs[{index}]")
        if url_error:
            return _invalid_protocol(url_error, required_files)
    if final.get("status") not in {"success", "needs_review", "error"}:
        return _invalid_protocol("output/final.json status must be success, needs_review, or error", required_files)
    if final.get("status") == "success" and not jobs:
        return _invalid_protocol("output/final.json cannot be success with zero extracted jobs", required_files)
    if not isinstance(page_profile, dict) or not isinstance(extraction_strategy, dict):
        return _invalid_protocol("protocol files must contain JSON objects", required_files)

    return {"valid": True, "error": "", "missing_files": [], "required_files": required_files}


def _trusted_protocol_validation_error(output_dir: Path) -> str:
    validator_path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "sandbox-page-analyst"
        / "scripts"
        / "validate_outputs.py"
    )
    if not validator_path.exists():
        return ""
    try:
        spec = importlib.util.spec_from_file_location("sandbox_page_analyst_validate_outputs", validator_path)
        if spec is None or spec.loader is None:
            return ""
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate_output_dir(output_dir)
    except Exception as exc:
        return str(exc)
    return ""


def _invalid_protocol(error: str, required_files: list[str]) -> dict[str, object]:
    return {"valid": False, "error": error, "missing_files": [], "required_files": required_files}


def _validate_job_types(job: dict[str, Any], label: str) -> str:
    string_fields = (
        "title",
        "company_name",
        "job_url",
        "location_raw",
        "location",
        "remote_type",
        "employment_type",
        "posted_at",
        "salary_raw",
        "description_text",
        "description",
        "relevance_reason",
    )
    for field in string_fields:
        if field in job and not isinstance(job[field], str):
            return f"{label} field {field} must be a string, not {type(job[field]).__name__}"
    if "tags" in job and not isinstance(job["tags"], list):
        return f"{label} field tags must be a list"
    if "evidence" in job and not isinstance(job["evidence"], list):
        return f"{label} field evidence must be a list"
    return ""


def _validate_job_url(job: dict[str, Any], label: str) -> str:
    raw_url = str(job.get("job_url") or "")
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    if not host.endswith("itviec.com"):
        return ""
    path = parsed.path.rstrip("/")
    if not re.fullmatch(r"/it-jobs/.+-\d{4}", path):
        return f"{label} ITviec job_url must be a detail posting URL ending in -NNNN"
    if "click_source=" in parsed.query:
        return f"{label} ITviec job_url must not be a navigation/category URL"
    return ""


def _load_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _artifact_sources_for_paths(
    audit_id: str,
    workspace: Path,
    paths: dict[str, tuple[str, str]],
) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for key, (relative_path, mime_type) in paths.items():
        target = workspace / relative_path
        if target.exists():
            sources.append(_artifact_source(audit_id, workspace, relative_path, mime_type, key=key))
    return sources


def _artifact_source(
    audit_id: str,
    workspace: Path,
    relative_path: str,
    mime_type: str,
    *,
    key: str,
) -> dict[str, object]:
    target = (workspace / relative_path).resolve()
    data = target.read_bytes()
    return {
        "key": key,
        "source_path": str(target),
        "artifact_name": _safe_adk_artifact_name(audit_id, relative_path),
        "mime_type": mime_type,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _safe_adk_artifact_name(audit_id: str, relative_path: str) -> str:
    safe_relative = relative_path.replace("\\", "__").replace("/", "__")
    return f"{audit_id}__{safe_relative}"


def _write_guardrail_evidence(
    record: SandboxSessionRecord,
    guardrail: str,
    message: str,
) -> list[dict[str, object]]:
    workspace = Path(record.workspace_path)
    error_path = workspace / "errors" / "guardrail_error.json"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "audit_id": record.audit_id,
        "guardrail": guardrail,
        "message": message,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    error_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    append_trace(record, {"event": "guardrail_triggered", "guardrail": guardrail, "message": message})
    return _artifact_sources_for_paths(
        record.audit_id,
        workspace,
        {
            "guardrail_error": ("errors/guardrail_error.json", "application/json"),
            "progress": ("progress.json", "application/json"),
            "trace": ("trace.jsonl", "application/jsonl"),
        },
    )


def _workspace_size_bytes(workspace: Path) -> int:
    total = 0
    for path in workspace.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def _check_workspace_size(
    registry: SandboxRegistry,
    record: SandboxSessionRecord,
    limits: SandboxLimits,
) -> SandboxSessionRecord | None:
    workspace_size = _workspace_size_bytes(Path(record.workspace_path))
    if workspace_size <= limits.max_workspace_bytes:
        return None
    return mark_guardrail_triggered(
        registry=registry,
        user_id=record.user_id,
        session_id=record.session_id,
        audit_id=record.audit_id,
        guardrail="max_workspace_bytes",
        message=f"Sandbox workspace size {workspace_size} exceeded limit {limits.max_workspace_bytes}.",
    )


def _check_command_output_limits(
    *,
    registry: SandboxRegistry,
    record: SandboxSessionRecord,
    limits: SandboxLimits,
    stdout_bytes: int,
    stderr_bytes: int,
) -> SandboxSessionRecord | None:
    if stdout_bytes > limits.max_stdout_bytes:
        return mark_guardrail_triggered(
            registry=registry,
            user_id=record.user_id,
            session_id=record.session_id,
            audit_id=record.audit_id,
            guardrail="max_stdout_bytes",
            message=f"Command stdout size {stdout_bytes} exceeded limit {limits.max_stdout_bytes}.",
        )
    if stderr_bytes > limits.max_stderr_bytes:
        return mark_guardrail_triggered(
            registry=registry,
            user_id=record.user_id,
            session_id=record.session_id,
            audit_id=record.audit_id,
            guardrail="max_stderr_bytes",
            message=f"Command stderr size {stderr_bytes} exceeded limit {limits.max_stderr_bytes}.",
        )
    return None


def _check_artifact_source_limits(
    registry: SandboxRegistry,
    record: SandboxSessionRecord,
    limits: SandboxLimits,
    artifact_sources: list[dict[str, object]],
) -> SandboxSessionRecord | None:
    for source in artifact_sources:
        size = int(source.get("bytes") or 0)
        if size > limits.max_artifact_bytes:
            return mark_guardrail_triggered(
                registry=registry,
                user_id=record.user_id,
                session_id=record.session_id,
                audit_id=record.audit_id,
                guardrail="max_artifact_bytes",
                message=f"Artifact {source.get('artifact_name')} size {size} exceeded limit {limits.max_artifact_bytes}.",
            )
    return None


def _ensure_text(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _start_container(image: str, workspace: Path, limits: SandboxLimits) -> str:
    import docker

    client = docker.from_env()
    container = client.containers.run(
        image,
        command=["sleep", "infinity"],
        detach=True,
        network_mode="none",
        working_dir="/workspace",
        volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
        user="65532:65532",
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
        mem_limit="256m",
        nano_cpus=500_000_000,
        pids_limit=128,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        labels={"job_scraper_sandbox": "true"},
    )
    return str(container.id)


def _stop_container(container_id: str) -> None:
    if not container_id:
        return
    docker_cli = _resolve_docker_cli()
    if docker_cli is None:
        return
    subprocess.run([docker_cli, "rm", "-f", container_id], capture_output=True, text=True, timeout=10)


def _resolve_docker_cli() -> str | None:
    configured = os.getenv("JOB_SCRAPER_DOCKER_CLI", "").strip()
    if configured:
        return configured
    found = shutil.which("docker")
    if found:
        return found
    for candidate in COMMON_DOCKER_CLI_PATHS:
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return None


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
