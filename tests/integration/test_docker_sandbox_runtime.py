from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from job_scraper.sandbox_image import DEFAULT_SANDBOX_IMAGE


pytestmark = pytest.mark.docker


def _require_docker_image(image: str = DEFAULT_SANDBOX_IMAGE) -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is not installed")
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
    if info.returncode != 0:
        pytest.skip("Docker daemon is not reachable")
    inspect = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=10)
    if inspect.returncode != 0:
        pytest.skip(f"Docker image {image!r} is not available locally")


def _run_script(script: str, *args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, f"skills/sandbox-page-analyst/scripts/{script}", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _write_minimal_protocol(app_root: Path, audit_id: str) -> None:
    registry_path = app_root / ".adk/runtime/sandbox_sessions/user/session" / f"{audit_id}.json"
    record = json.loads(registry_path.read_text(encoding="utf-8"))
    output_dir = Path(record["workspace_path"]) / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = {
        "source": {"source_name": "Test", "source_url": "https://example.com/jobs"},
        "jobs": [
            {
                "title": "Machine Learning Engineer",
                "job_url": "https://example.com/jobs/ml",
                "company_name": "Acme",
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


def test_docker_sandbox_reuses_container_and_blocks_network(tmp_path: Path) -> None:
    _require_docker_image()
    start = _run_script(
        "sandbox_start.py",
        "--app-root",
        str(tmp_path),
        "--user-id",
        "user",
        "--session-id",
        "session",
        "--page-artifact",
        __file__,
        "--source-url",
        "https://example.com/jobs",
    )
    assert start["status"] == "running"
    audit_id = str(start["audit_id"])
    container_id = str(start["container_id"])
    try:
        inspect = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert inspect.returncode == 0, inspect.stderr
        container_info = json.loads(inspect.stdout)[0]
        host_config = container_info["HostConfig"]
        config = container_info["Config"]
        assert host_config["NetworkMode"] == "none"
        assert host_config["ReadonlyRootfs"] is True
        assert host_config["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in host_config["SecurityOpt"]
        assert host_config["PidsLimit"] == 128
        assert host_config["Memory"] > 0
        assert host_config["NanoCpus"] > 0
        assert config["User"] == "65532:65532"

        write = _run_script(
            "sandbox_exec.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--command",
            "echo hello > state.txt",
        )
        read = _run_script(
            "sandbox_exec.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--command",
            "cat state.txt",
        )
        blocked = _run_script(
            "sandbox_exec.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--command",
            "curl https://example.com",
        )

        assert write["status"] == "success"
        assert write["command_index"] == 1
        assert write["artifacts"]["stdout"]["path"] == "commands/001.stdout.txt"
        assert read["stdout"] == "hello\n"
        assert read["stdout_truncated"] is False
        assert read["paths"]["stdout_path"] == "commands/002.stdout.txt"
        assert read["command_index"] == 2
        assert blocked["status"] == "rejected"
        assert "network" in str(blocked["error"])

        registry_path = (
            tmp_path
            / ".adk/runtime/sandbox_sessions/user/session"
            / f"{audit_id}.json"
        )
        record = json.loads(registry_path.read_text(encoding="utf-8"))
        workspace = Path(record["workspace_path"])
        assert (workspace / "plan.md").exists()
        assert (workspace / "progress.json").exists()
        assert (workspace / "page.html").exists()
        assert (workspace / "references/protocol.md").exists()
        progress = json.loads((workspace / "progress.json").read_text(encoding="utf-8"))
        assert progress["source_url"] == "https://example.com/jobs"
        assert (workspace / "commands/001.command.txt").read_text(encoding="utf-8") == "echo hello > state.txt"
        assert (workspace / "commands/002.stdout.txt").read_text(encoding="utf-8") == "hello\n"
        trace_lines = (workspace / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["command_index"] for line in trace_lines] == [1, 2]
    finally:
        _write_minimal_protocol(tmp_path, audit_id)
        _run_script(
            "sandbox_finalize.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
        )


def test_docker_sandbox_large_output_returns_artifact_aware_slice(tmp_path: Path) -> None:
    _require_docker_image()
    start = _run_script(
        "sandbox_start.py",
        "--app-root",
        str(tmp_path),
        "--user-id",
        "user",
        "--session-id",
        "session",
        "--max-read-chars",
        "5",
    )
    assert start["status"] == "running"
    audit_id = str(start["audit_id"])
    container_id = str(start["container_id"])

    try:
        result = _run_script(
            "sandbox_exec.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--command",
            "printf 1234567890",
        )
        still_running = _run_script(
            "sandbox_exec.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--command",
            "echo ok",
        )

        assert result["status"] == "success"
        assert result["stdout"] == "12345"
        assert result["stdout_truncated"] is True
        assert result["paths"]["stdout_path"] == "commands/001.stdout.txt"
        assert result["artifacts"]["stdout"]["path"] == "commands/001.stdout.txt"
        assert "exceeded the direct return limit" in result["message"]
        assert "sed -n" not in result["message"]
        assert still_running["status"] == "success"
        assert still_running["stdout"] == "ok\n"
    finally:
        _write_minimal_protocol(tmp_path, audit_id)
        _run_script(
            "sandbox_finalize.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
        )
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True, timeout=10)


def test_docker_sandbox_stdout_guardrail_is_terminal(tmp_path: Path) -> None:
    _require_docker_image()
    start = _run_script(
        "sandbox_start.py",
        "--app-root",
        str(tmp_path),
        "--user-id",
        "user",
        "--session-id",
        "session",
        "--max-stdout-bytes",
        "5",
    )
    assert start["status"] == "running"
    audit_id = str(start["audit_id"])
    container_id = str(start["container_id"])

    try:
        triggered = _run_script(
            "sandbox_exec.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--command",
            "printf 1234567890",
        )
        after_terminal = _run_script(
            "sandbox_exec.py",
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--command",
            "pwd",
        )
        inspect = subprocess.run(["docker", "inspect", container_id], capture_output=True, text=True, timeout=10)

        assert triggered["status"] == "guardrail_triggered"
        assert triggered["guardrail"] == "max_stdout_bytes"
        assert triggered["artifacts"]["stdout"]["path"] == "commands/001.stdout.txt"
        assert any(source["key"] == "guardrail_error" for source in triggered["artifact_sources"])
        assert after_terminal["status"] == "guardrail_triggered"
        assert "terminal" in after_terminal["error"]
        assert inspect.returncode != 0
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True, timeout=10)
