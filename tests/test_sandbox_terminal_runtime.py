from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_scraper import sandbox_terminal_scripts
from job_scraper.sandbox_terminal import (
    SandboxLimits,
    SandboxRegistry,
    SandboxSessionRecord,
    append_trace,
    build_registry_path,
    command_allowed,
    mark_guardrail_triggered,
    reserve_command_slot,
    workspace_path,
)
from job_scraper.sandbox_terminal_scripts import _workspace_file_specs
from job_scraper.sandbox_terminal_scripts import _bounded_command_output
from job_scraper.sandbox_terminal_scripts import _apply_exact_workspace_replacement
from job_scraper.sandbox_terminal_scripts import _cleanup_stale_sandbox_containers
from job_scraper.sandbox_terminal_scripts import _parse_sandbox_exec_args
from job_scraper.sandbox_terminal_scripts import _resolve_docker_cli
from job_scraper.sandbox_terminal_scripts import _workspace_write_target_error
from job_scraper.sandbox_terminal_scripts import DEFAULT_IMAGE
from job_scraper.sandbox_terminal_scripts import SandboxPatchError
from job_scraper.sandbox_terminal_scripts import sandbox_exec_main


def test_registry_path_is_scoped_and_sanitized(tmp_path: Path) -> None:
    path = build_registry_path(
        app_root=tmp_path,
        user_id="../user/name",
        session_id="session:abc/def",
        audit_id="sandbox_run_123",
    )

    assert path == tmp_path / ".adk/runtime/sandbox_sessions/user_name/session_abc_def/sandbox_run_123.json"


def test_registry_writes_session_records_atomically(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
        limits=SandboxLimits(max_commands_per_session=2).model_dump(),
    )

    path = registry.save(record)

    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["container_id"] == "container"
    assert registry.load("user", "session", "sandbox_run_123") == record


def test_registry_iter_records_loads_sandbox_records(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
        limits=SandboxLimits(max_commands_per_session=2).model_dump(),
    )
    registry.save(record)

    assert registry.iter_records() == [record]


def _save_record_with_updated_at(registry: SandboxRegistry, record: SandboxSessionRecord, updated_at: str) -> None:
    registry.save(record)
    path = registry.path_for(record.user_id, record.session_id, record.audit_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at"] = updated_at
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_session_record_defaults_to_workflow_mode() -> None:
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
    )

    assert record.mode == "workflow"


def test_registry_concurrent_saves_do_not_share_temp_path(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
        limits=SandboxLimits(max_commands_per_session=2).model_dump(),
    )

    def save_record(index: int) -> None:
        copy = record.model_copy(deep=True)
        copy.command_count = index
        registry.save(copy)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(save_record, [1, 2]))

    saved = registry.load("user", "session", "sandbox_run_123")
    assert saved.command_count in {1, 2}
    assert not list(registry.path_for("user", "session", "sandbox_run_123").parent.glob("*.tmp"))


def test_command_policy_allows_common_bash_and_blocks_dangerous_commands() -> None:
    assert command_allowed("find . -maxdepth 2 -type f").allowed is True
    assert command_allowed("python - <<'PY'\nprint('inspect')\nPY").allowed is True
    assert command_allowed("python - <<'PY'\nprint({'confidence': 0.9})\nPY").allowed is True

    blocked = command_allowed("curl https://example.com")
    blocked_nc = command_allowed("nc -l 9999")
    blocked_pip3 = command_allowed("pip3 install beautifulsoup4")
    blocked_apt = command_allowed("apt-get install jq")

    assert blocked.allowed is False
    assert "network" in blocked.reason
    assert blocked_nc.allowed is False
    assert "network" in blocked_nc.reason
    assert blocked_pip3.allowed is False
    assert "network" in blocked_pip3.reason
    assert blocked_apt.allowed is False
    assert "network" in blocked_apt.reason


def test_sandbox_terminal_defaults_to_project_parser_image() -> None:
    assert DEFAULT_IMAGE == "job-scraper-sandbox:py313"


def test_resolve_docker_cli_prefers_configured_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_SCRAPER_DOCKER_CLI", "/custom/bin/docker")

    assert _resolve_docker_cli() == "/custom/bin/docker"


def test_cleanup_stale_sandbox_containers_dry_run_selects_safe_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    old_time = "2026-05-07T00:00:00+00:00"
    fresh_time = "2026-05-07T00:55:00+00:00"
    now = datetime(2026, 5, 7, 1, 0, tzinfo=timezone.utc)
    for record in [
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_stale",
            container_id="stale123",
            workspace_path="/tmp/stale",
            status="running",
            updated_at=old_time,
        ),
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_fresh",
            container_id="fresh123",
            workspace_path="/tmp/fresh",
            status="running",
            updated_at=fresh_time,
        ),
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_terminal",
            container_id="terminal123",
            workspace_path="/tmp/terminal",
            status="guardrail_triggered",
            updated_at=fresh_time,
        ),
    ]:
        _save_record_with_updated_at(registry, record, str(record.updated_at))

    containers = {
        "stale123456": {"id": "stale123456", "name": "stale", "created_at": old_time},
        "fresh123456": {"id": "fresh123456", "name": "fresh", "created_at": old_time},
        "terminal123456": {"id": "terminal123456", "name": "terminal", "created_at": old_time},
        "orphanold123": {"id": "orphanold123", "name": "orphan-old", "created_at": old_time},
        "orphannew123": {"id": "orphannew123", "name": "orphan-new", "created_at": fresh_time},
    }
    monkeypatch.setattr(sandbox_terminal_scripts, "_resolve_docker_cli", lambda: "/usr/bin/docker")
    monkeypatch.setattr(
        sandbox_terminal_scripts,
        "_list_sandbox_docker_containers",
        lambda docker_cli: {"status": "success", "containers": containers},
    )

    result = _cleanup_stale_sandbox_containers(
        app_root=str(tmp_path),
        max_age_seconds=900,
        include_orphans=True,
        dry_run=True,
        now=now,
    )

    reasons = {candidate["container_id"]: candidate["reason"] for candidate in result["candidates"]}
    assert result["status"] == "success"
    assert result["dry_run"] is True
    assert result["candidate_count"] == 3
    assert reasons == {
        "stale123456": "stale_running_registry_record",
        "terminal123456": "terminal_registry_record",
        "orphanold123": "orphan_labeled_container",
    }
    assert result["removed"] == []


def test_cleanup_stale_sandbox_containers_removes_and_marks_running_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    old_time = "2026-05-07T00:00:00+00:00"
    now = datetime(2026, 5, 7, 1, 0, tzinfo=timezone.utc)
    _save_record_with_updated_at(
        registry,
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_stale",
            container_id="stale123",
            workspace_path="/tmp/stale",
            status="running",
            updated_at=old_time,
        ),
        old_time,
    )
    removed: list[str] = []
    monkeypatch.setattr(sandbox_terminal_scripts, "_resolve_docker_cli", lambda: "/usr/bin/docker")
    monkeypatch.setattr(
        sandbox_terminal_scripts,
        "_list_sandbox_docker_containers",
        lambda docker_cli: {
            "status": "success",
            "containers": {"stale123456": {"id": "stale123456", "name": "stale", "created_at": old_time}},
        },
    )
    monkeypatch.setattr(
        sandbox_terminal_scripts,
        "_remove_sandbox_container",
        lambda docker_cli, container_id: removed.append(container_id) or {"status": "success"},
    )

    result = _cleanup_stale_sandbox_containers(
        app_root=str(tmp_path),
        max_age_seconds=900,
        include_orphans=True,
        dry_run=False,
        now=now,
    )

    record = registry.load("user", "session", "sandbox_run_stale")
    assert result["status"] == "success"
    assert result["removed_count"] == 1
    assert removed == ["stale123456"]
    assert record.status == "guardrail_triggered"
    assert record.guardrail == "stale_sandbox_cleanup"


def test_sandbox_exec_args_accept_separator_command_form() -> None:
    args = _parse_sandbox_exec_args(
        [
            "--audit-id",
            "sandbox_run_123",
            "pwd && ls -lah && find . -maxdepth 2 -type f",
        ]
    )

    assert args.audit_id == "sandbox_run_123"
    assert args.command == "pwd && ls -lah && find . -maxdepth 2 -type f"


def test_sandbox_exec_args_strip_double_dash_separator() -> None:
    args = _parse_sandbox_exec_args(
        [
            "--audit-id",
            "sandbox_run_123",
            "--",
            "python",
            "-c",
            "print('0123456789'*20)",
        ]
    )

    assert args.audit_id == "sandbox_run_123"
    assert args.command.startswith("python -c ")
    assert "0123456789" in args.command


def test_sandbox_exec_args_accept_leading_audit_id_before_command_flag() -> None:
    args = _parse_sandbox_exec_args(["sandbox_run_123", "--cmd", "pwd && ls -lah"])

    assert args.audit_id == "sandbox_run_123"
    assert args.command == "pwd && ls -lah"


def test_sandbox_exec_args_keep_explicit_command_form() -> None:
    args = _parse_sandbox_exec_args(
        [
            "--audit-id",
            "sandbox_run_123",
            "--command",
            "python scripts/validate_outputs.py output",
        ]
    )

    assert args.audit_id == "sandbox_run_123"
    assert args.command == "python scripts/validate_outputs.py output"


def test_sandbox_exec_args_accept_per_command_read_limit() -> None:
    args = _parse_sandbox_exec_args(
        [
            "--audit-id",
            "sandbox_run_123",
            "--cmd",
            "python -c \"print('0123456789'*20)\"",
            "--max-read-chars",
            "40",
        ]
    )

    assert args.audit_id == "sandbox_run_123"
    assert args.command == "python -c \"print('0123456789'*20)\""
    assert args.max_read_chars == 40


def test_sandbox_exec_main_returns_structured_error_for_missing_audit_id(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["sandbox_exec.py", "--cmd", "pwd"])

    sandbox_exec_main()
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "error"
    assert "requires --audit-id" in payload["error"]


def test_sandbox_exec_rejects_inline_protocol_file_writes(monkeypatch, capsys) -> None:
    command = "python - <<'PY'\nfrom pathlib import Path\nPath('output/final.json').write_text('{}')\nPY"
    monkeypatch.setattr(sys, "argv", ["sandbox_exec.py", "--audit-id", "sandbox_run_123", "--command", command])

    sandbox_exec_main()
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "rejected"
    assert "sandbox_write_file.py" in payload["error"]
    assert "suggested_next" not in payload


def test_bounded_command_output_does_not_suggest_host_wrapper_inside_sandbox() -> None:
    payload = _bounded_command_output(
        artifacts={
            "stdout": {"path": "commands/001.stdout.txt"},
            "stderr": {"path": "commands/001.stderr.txt"},
            "command": {"path": "commands/001.command.txt"},
        },
        stdout="x" * 20,
        stderr="",
        limits=SandboxLimits(max_read_chars=5),
        exit_code=0,
    )
    serialized = json.dumps(payload)

    assert "sandbox_read.py" not in serialized
    assert "sed -n" not in serialized
    assert payload["paths"]["stdout_path"] == "commands/001.stdout.txt"
    assert payload["stdout"] == "x" * 5
    assert payload["stdout_truncated"] is True
    assert "stdout preview" in payload["message"]


def test_guardrail_terminal_state_is_persisted(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
        limits=SandboxLimits(max_commands_per_session=1).model_dump(),
    )
    registry.save(record)

    updated = mark_guardrail_triggered(
        registry=registry,
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        guardrail="max_commands_per_session",
        message="command budget exhausted",
    )

    assert updated.status == "guardrail_triggered"
    assert updated.guardrail == "max_commands_per_session"
    assert updated.error == "command budget exhausted"
    assert registry.load("user", "session", "sandbox_run_123").status == "guardrail_triggered"


def test_reserve_command_slot_increments_until_budget_then_triggers_guardrail(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
        limits=SandboxLimits(max_commands_per_session=1).model_dump(),
    )
    registry.save(record)

    first = reserve_command_slot(registry=registry, user_id="user", session_id="session", audit_id="sandbox_run_123")
    second = reserve_command_slot(registry=registry, user_id="user", session_id="session", audit_id="sandbox_run_123")

    assert first.allowed is True
    assert first.record.command_count == 1
    assert second.allowed is False
    assert second.record.status == "guardrail_triggered"
    assert second.record.guardrail == "max_commands_per_session"


def test_reserve_command_slot_does_not_expire_by_wall_clock_by_default(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
        limits=SandboxLimits(max_commands_per_session=2).model_dump(),
    )
    record.created_at = "2000-01-01T00:00:00+00:00"
    registry.save(record)

    result = reserve_command_slot(registry=registry, user_id="user", session_id="session", audit_id="sandbox_run_123")

    assert result.allowed is True
    assert result.record.command_count == 1
    assert result.record.guardrail == ""


def test_reserve_command_slot_expiration_remains_available_when_configured(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path="/tmp/workspace",
        status="running",
        limits=SandboxLimits(max_duration_seconds=1).model_dump(),
    )
    record.created_at = "2000-01-01T00:00:00+00:00"
    registry.save(record)

    result = reserve_command_slot(registry=registry, user_id="user", session_id="session", audit_id="sandbox_run_123")

    assert result.allowed is False
    assert result.record.status == "guardrail_triggered"
    assert result.record.guardrail == "max_duration_seconds"


def test_reserve_command_slot_is_atomic_for_parallel_calls(tmp_path: Path) -> None:
    registry = SandboxRegistry(app_root=tmp_path)
    registry.save(
        SandboxSessionRecord(
            app_name="job_scraper",
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
            container_id="container",
            workspace_path="/tmp/workspace",
            status="running",
            limits=SandboxLimits(max_commands_per_session=5).model_dump(),
        )
    )

    def reserve() -> int:
        result = reserve_command_slot(
            registry=registry,
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
        )
        assert result.allowed is True
        return result.record.command_count

    with ThreadPoolExecutor(max_workers=5) as executor:
        counts = sorted(executor.map(lambda _: reserve(), range(5)))

    assert counts == [1, 2, 3, 4, 5]
    assert registry.load("user", "session", "sandbox_run_123").command_count == 5


def test_workspace_paths_cannot_escape_sandbox_workspace(tmp_path: Path) -> None:
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path=str(tmp_path / "workspace"),
        status="running",
    )

    assert workspace_path(record, "output/final.json") == (tmp_path / "workspace/output/final.json").resolve()

    try:
        workspace_path(record, "../escape.txt")
    except ValueError as exc:
        assert "escapes workspace" in str(exc)
    else:
        raise AssertionError("workspace_path accepted an escaping path")


def test_workspace_write_policy_allows_only_generated_artifacts() -> None:
    assert _workspace_write_target_error("output/extractor.py") == ""
    assert _workspace_write_target_error("./output/final.json") == ""
    assert _workspace_write_target_error("progress.json") == ""

    blocked = _workspace_write_target_error("scripts/validate_outputs.py")
    blocked_schema = _workspace_write_target_error("schemas/candidates.schema.json")
    blocked_reference = _workspace_write_target_error("references/protocol.md")
    blocked_input = _workspace_write_target_error("page.html")
    blocked_escape = _workspace_write_target_error("../output/extractor.py")

    assert "read-only by policy" in blocked
    assert "read-only by policy" in blocked_schema
    assert "read-only by policy" in blocked_reference
    assert "read-only by policy" in blocked_input
    assert "parent directory" in blocked_escape


def test_sandbox_patch_rejects_read_only_helper_script_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    helper = workspace / "scripts" / "validate_outputs.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("old = True\n", encoding="utf-8")
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path=str(workspace),
        status="running",
    )

    with pytest.raises(SandboxPatchError) as exc_info:
        _apply_exact_workspace_replacement(record, "scripts/validate_outputs.py", "old = True", "old = False")

    assert exc_info.value.error_type == "write_target_not_allowed"
    assert helper.read_text(encoding="utf-8") == "old = True\n"


def test_workspace_file_specs_accept_positional_page_artifacts(tmp_path: Path) -> None:
    page_file = tmp_path / "page.html"
    page_file.write_text("<html>jobs</html>", encoding="utf-8")

    files = _workspace_file_specs("[]", [str(page_file)])

    assert files == [{"source_path": str(page_file), "sandbox_path": "page.html"}]


def test_sandbox_start_response_uses_docker_path_model_without_host_paths(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from job_scraper import sandbox_terminal_scripts

    page_file = tmp_path / "page.html"
    page_file.write_text("<html>jobs</html>", encoding="utf-8")
    monkeypatch.setattr(sandbox_terminal_scripts, "_start_container", lambda image, workspace, limits: "container-id")

    sandbox_terminal_scripts._sandbox_start_cli(
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_path_model",
        app_root=str(tmp_path),
        page_artifact=[str(page_file)],
        mode="workflow",
    )
    payload = json.loads(capsys.readouterr().out)
    record = SandboxRegistry(tmp_path).load("user", "session", "sandbox_run_path_model")

    assert payload["status"] == "running"
    assert payload["sandbox_workdir"] == "/workspace"
    assert "page.html" in payload["sandbox_visible_files"]
    assert "output/" in payload["sandbox_visible_files"]
    assert "cwd=/workspace" in payload["command_rule"]
    assert "workspace_path" not in payload
    assert "registry_path" not in payload
    assert "artifact_sources" not in payload
    assert record.workspace_path
    assert record.limits["max_duration_seconds"] == 0


def test_append_trace_writes_compact_jsonl(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = SandboxSessionRecord(
        app_name="job_scraper",
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_123",
        container_id="container",
        workspace_path=str(workspace),
        status="running",
    )

    trace_path = append_trace(record, {"event": "command", "command_index": 1})

    payload = json.loads(trace_path.read_text(encoding="utf-8").strip())
    assert payload["audit_id"] == "sandbox_run_123"
    assert payload["event"] == "command"
    assert payload["command_index"] == 1


def test_read_write_scripts_reject_terminal_sandbox_records(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = SandboxRegistry(app_root=tmp_path)
    registry.save(
        SandboxSessionRecord(
            app_name="job_scraper",
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
            container_id="",
            workspace_path=str(workspace),
            status="guardrail_triggered",
            guardrail="max_commands_per_session",
            error="Sandbox command budget exhausted.",
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "skills/sandbox-page-analyst/scripts/sandbox_read.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            "sandbox_run_123",
            "--path",
            "progress.json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "guardrail_triggered"
    assert "terminal" in payload["error"]


def test_finalize_rejects_missing_protocol_outputs_without_terminalizing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    (workspace / "progress.json").write_text("{}", encoding="utf-8")
    (workspace / "trace.jsonl").write_text("", encoding="utf-8")
    registry = SandboxRegistry(app_root=tmp_path)
    registry.save(
        SandboxSessionRecord(
            app_name="job_scraper",
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
            container_id="",
            workspace_path=str(workspace),
            status="running",
            limits=SandboxLimits().model_dump(),
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "skills/sandbox-page-analyst/scripts/sandbox_finalize.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            "sandbox_run_123",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "output/final.json" in payload["missing_files"]
    assert "observation" not in payload
    assert "suggested_next" not in payload
    assert registry.load("user", "session", "sandbox_run_123").status == "running"


def test_workflow_finalize_status_summary_does_not_overwrite_final_json(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    (workspace / "progress.json").write_text("{}", encoding="utf-8")
    (workspace / "trace.jsonl").write_text("", encoding="utf-8")
    _write_minimal_protocol_files(workspace / "output")
    original_final = (workspace / "output/final.json").read_text(encoding="utf-8")
    registry = SandboxRegistry(app_root=tmp_path)
    registry.save(
        SandboxSessionRecord(
            app_name="job_scraper",
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
            container_id="",
            workspace_path=str(workspace),
            status="running",
            limits=SandboxLimits().model_dump(),
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "skills/sandbox-page-analyst/scripts/sandbox_finalize.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            "sandbox_run_123",
            "--status",
            "needs_review",
            "--summary",
            "should not overwrite workflow final",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["ignored_inline_args"] is True
    assert (workspace / "output/final.json").read_text(encoding="utf-8") == original_final
    assert registry.load("user", "session", "sandbox_run_123").status == "finalized"


def test_workflow_finalize_uses_trusted_itviec_listing_coverage_validation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    (workspace / "progress.json").write_text("{}", encoding="utf-8")
    (workspace / "trace.jsonl").write_text("", encoding="utf-8")
    (workspace / "page.html").write_text(
        "\n".join(
            f'<a href="/it-jobs/ai-engineer-example-company-{index:04d}">Job {index}</a>'
            for index in range(1, 7)
        ),
        encoding="utf-8",
    )
    _write_minimal_protocol_files(workspace / "output")
    one_job = {
        "title": "AI Engineer",
        "company_name": "Example",
        "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi",
        "job_url": "https://itviec.com/it-jobs/ai-engineer-example-company-0001",
    }
    (workspace / "output/candidates.json").write_text(
        json.dumps(
            {
                "source": {"source_name": "ITviec", "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi"},
                "jobs": [one_job],
                "crawl": {"candidate_count": 1, "relevant_count": 1},
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    (workspace / "output/final.json").write_text(
        json.dumps(
            {
                "status": "success",
                "output_schema": "job_extraction",
                "result": {"jobs": [one_job]},
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    registry = SandboxRegistry(app_root=tmp_path)
    registry.save(
        SandboxSessionRecord(
            app_name="job_scraper",
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
            container_id="",
            workspace_path=str(workspace),
            status="running",
            limits=SandboxLimits().model_dump(),
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "skills/sandbox-page-analyst/scripts/sandbox_finalize.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            "sandbox_run_123",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "expects 6 jobs but candidates.jobs has 1" in payload["error"]
    assert registry.load("user", "session", "sandbox_run_123").status == "running"


def test_diagnostic_finalize_does_not_require_protocol_outputs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    (workspace / "progress.json").write_text("{}", encoding="utf-8")
    (workspace / "trace.jsonl").write_text("", encoding="utf-8")
    registry = SandboxRegistry(app_root=tmp_path)
    registry.save(
        SandboxSessionRecord(
            app_name="job_scraper",
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
            container_id="",
            workspace_path=str(workspace),
            status="running",
            mode="diagnostic",
            limits=SandboxLimits().model_dump(),
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "skills/sandbox-page-analyst/scripts/sandbox_finalize.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            "sandbox_run_123",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["mode"] == "diagnostic"
    assert payload["result"]["status"] == "diagnostic_complete"
    assert registry.load("user", "session", "sandbox_run_123").status == "finalized"


def test_sandbox_script_files_exist() -> None:
    scripts = Path("skills/sandbox-page-analyst/scripts")

    for name in [
        "sandbox_start.py",
        "sandbox_exec.py",
        "sandbox_read.py",
        "sandbox_write.py",
        "sandbox_write_file.py",
        "sandbox_apply_patch.py",
        "sandbox_progress.py",
        "sandbox_finalize.py",
        "sandbox_cleanup.py",
    ]:
        assert (scripts / name).exists()


def test_sandbox_script_help_exposes_agent_usage_instructions() -> None:
    scripts = Path("skills/sandbox-page-analyst/scripts")
    expected_help = {
        "sandbox_start.py": ["--page-artifact", "Workflow mode", "first becomes", "page.html", "/workspace", "Host workspace paths"],
        "sandbox_exec.py": ["--cmd", "Do not use", "sandbox_write_file.py"],
        "sandbox_write_file.py": ["--path", "--content", "Protocol JSON writes are validated"],
        "sandbox_apply_patch.py": ["--patch", "--old", "--new", "unified diff"],
        "sandbox_read.py": ["--max-chars", "bounded preview", "too large for direct context"],
        "sandbox_progress.py": ["--progress-json", "compact progress", "no raw HTML"],
        "sandbox_finalize.py": ["--audit-id", "validation fails", "same sandbox"],
        "sandbox_cleanup.py": ["--max-age-seconds", "--no-dry-run", "job_scraper_sandbox=true"],
        "validate_outputs.py": ["output_dir", "--audit-id", "protocol output", "JSON", "before", "sandbox_finalize.py"],
    }

    for script_name, substrings in expected_help.items():
        result = subprocess.run(
            [sys.executable, str(scripts / script_name), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, script_name
        for substring in substrings:
            assert substring in result.stdout, script_name


def test_validate_outputs_resolves_audit_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    output_dir.mkdir(parents=True)
    _write_minimal_protocol_files(output_dir)
    SandboxRegistry(app_root=tmp_path).save(
        SandboxSessionRecord(
            app_name="job_scraper",
            user_id="user",
            session_id="session",
            audit_id="sandbox_run_123",
            container_id="",
            workspace_path=str(workspace),
            status="running",
            limits=SandboxLimits().model_dump(),
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "skills/sandbox-page-analyst/scripts/validate_outputs.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            "sandbox_run_123",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["candidates"]["path"] == "output/candidates.json"


def _write_minimal_protocol_files(output_dir: Path) -> None:
    candidates = {
        "source": {"source_name": "Test", "source_url": "https://example.com/jobs"},
        "jobs": [
            {
                "title": "Machine Learning Engineer",
                "job_url": "https://example.com/jobs/ml",
                "company_name": "Acme",
                "location": "Ha Noi",
                "evidence": [{"text": "Machine Learning Engineer"}],
            }
        ],
        "selectors": {},
        "crawl": {"candidate_count": 1, "relevant_count": 1},
        "warnings": [],
    }
    files = {
        "page_profile.json": {"page_files": ["page.html"], "detected_layouts": ["test"], "warnings": []},
        "extraction_strategy.json": {"strategy": "test", "source_files": ["page.html"], "warnings": []},
        "extraction_run.json": {
            "observations": ["Observed one test job in the fixture workspace."],
            "chosen_strategy": "test-fixture-accountable-extraction",
            "extraction_steps": ["Wrote minimal protocol files for sandbox runtime tests."],
            "expected_output": {
                "expected_job_count": 1,
                "count_basis": "test fixture setup",
                "count_rationale": "The fixture contains exactly one in-scope test job.",
                "available_fields": {
                    "title": "required_observed",
                    "company_name": "required_observed",
                    "job_url": "required_observed",
                },
                "field_basis": {
                    "title": "The fixture job object includes title.",
                    "company_name": "The fixture job object includes company_name.",
                    "job_url": "The fixture job object includes job_url.",
                },
            },
            "validation": {"valid": True},
        },
        "candidates.json": candidates,
        "validation.json": {"valid": True, "candidate_count": 1, "relevant_count": 1, "warnings": []},
        "final.json": {
            "status": "success",
            "output_schema": "job_extraction",
            "summary": "test extraction",
            "protocol": {"valid": True, "warnings": []},
            "result": candidates,
        },
    }
    for name, payload in files.items():
        (output_dir / name).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    (output_dir / "run_summary.md").write_text(
        "The sandbox runtime test wrote accountable protocol files and reached validation/finalization "
        "using a compact fixture-backed job payload.",
        encoding="utf-8",
    )
