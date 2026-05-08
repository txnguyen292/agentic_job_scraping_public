from __future__ import annotations

from pathlib import Path

from job_scraper.public_export import PublicExportConfig, build_export_plan, sync_public_tree, verify_public_tree


def _write(path: Path, content: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_export_plan_uses_allowlist_and_excludes_private_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "README.md")
    _write(source / "src/job_scraper/app.py")
    _write(source / ".contexts/handoff.md")
    _write(source / "src/.adk/session.db")
    _write(source / "reports/adk-runs/run.json")
    config = PublicExportConfig(
        name="public",
        description="",
        include=("README.md", "src/**", ".contexts/**", "reports/**"),
        exclude=(".contexts/**", "src/.adk/**", "reports/adk-runs/**"),
        forbidden_paths=(".contexts", "src/.adk", "reports/adk-runs"),
        secret_patterns=(),
        secret_scan_exclude=(),
    )

    plan = build_export_plan(source, config)

    assert [path.as_posix() for path in plan.files] == ["README.md", "src/job_scraper/app.py"]


def test_sync_public_tree_preserves_destination_git_and_verifies(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "public"
    _write(source / "README.md", "public")
    _write(source / ".env", "OPENAI" + "_API_KEY=secret")
    _write(destination / ".git/config", "gitdir")
    _write(destination / "stale.txt", "old")
    config = PublicExportConfig(
        name="public",
        description="",
        include=("README.md", ".env"),
        exclude=(".env",),
        forbidden_paths=(".env",),
        secret_patterns=(r"OPENAI_API_KEY\s*=",),
        secret_scan_exclude=(),
    )

    payload = sync_public_tree(source, destination, config, apply=True)

    assert payload["verification"]["valid"] is True
    assert (destination / "README.md").read_text(encoding="utf-8") == "public"
    assert (destination / ".git/config").read_text(encoding="utf-8") == "gitdir"
    assert not (destination / "stale.txt").exists()
    assert not (destination / ".env").exists()


def test_verify_public_tree_rejects_forbidden_paths_and_secret_patterns(tmp_path: Path) -> None:
    public = tmp_path / "public"
    _write(public / ".contexts/handoff.md")
    _write(public / "README.md", "OPENAI" + "_API_KEY=value")
    config = PublicExportConfig(
        name="public",
        description="",
        include=("README.md",),
        exclude=(".contexts/**",),
        forbidden_paths=(".contexts",),
        secret_patterns=(r"OPENAI_API_KEY\s*=",),
        secret_scan_exclude=(),
    )

    result = verify_public_tree(public, config)

    assert result["valid"] is False
    issue_types = {issue["type"] for issue in result["issues"]}
    assert "forbidden_path" in issue_types
    assert "excluded_file_present" in issue_types
    assert "secret_pattern" in issue_types


def test_verify_public_tree_ignores_destination_git_metadata(tmp_path: Path) -> None:
    public = tmp_path / "public"
    _write(public / ".git/config", "remote")
    _write(public / "README.md", "public")
    config = PublicExportConfig(
        name="public",
        description="",
        include=("README.md",),
        exclude=(".git/**",),
        forbidden_paths=(".contexts",),
        secret_patterns=(),
        secret_scan_exclude=(),
    )

    result = verify_public_tree(public, config)

    assert result == {"valid": True, "issues": []}
