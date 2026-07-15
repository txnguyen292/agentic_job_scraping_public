from __future__ import annotations

import json
import subprocess

from scripts import dependabot_changelog


def test_render_repository_changelog_describes_repo_diff_without_upstream_dump() -> None:
    update = dependabot_changelog.DependencyUpdate(
        dependency_names="rich",
        previous_version="13.9.4",
        new_version="15.0.0",
        update_type="version-update:semver-major",
        package_ecosystem="pip",
    )
    files = [
        dependabot_changelog.PullRequestFile(
            path="pyproject.toml", additions=1, deletions=1
        ),
        dependabot_changelog.PullRequestFile(
            path="uv.lock", additions=1618, deletions=1618
        ),
    ]

    body = dependabot_changelog.render_repository_changelog(update, files)

    assert body.startswith(dependabot_changelog.BODY_MARKER)
    assert "Updates `rich` from `13.9.4` to `15.0.0`." in body
    assert "`rich`" in body
    assert "`13.9.4` -> `15.0.0`" in body
    assert "Dependency manifests: `pyproject.toml`" in body
    assert "Resolved lock data: `uv.lock`" in body
    assert "Application and test source: unchanged" in body
    assert "`major-upgrade-approved`" in body
    assert "<summary>Release notes</summary>" in body
    assert "<summary>Changelog</summary>" in body
    assert "<summary>Review gate</summary>" in body
    assert "<summary>Commits</summary>" not in body
    assert "Total diff:" not in body


def test_render_repository_changelog_calls_out_workflow_changes() -> None:
    update = dependabot_changelog.DependencyUpdate(
        dependency_names="actions/checkout",
        previous_version="6",
        new_version="7",
        update_type="version-update:semver-major",
        package_ecosystem="github-actions",
    )
    files = [
        dependabot_changelog.PullRequestFile(
            path=".github/workflows/internal-ci.yml", additions=1, deletions=1
        )
    ]

    body = dependabot_changelog.render_repository_changelog(update, files)

    assert "CI workflow definitions: `.github/workflows/internal-ci.yml`" in body
    assert "Application and test source: unchanged" in body


def test_update_pr_replaces_existing_dependabot_body(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    snapshot = {
        "body": "<details><summary>Release notes</summary>upstream dump</details>",
        "files": [
            {"path": "pyproject.toml", "additions": 1, "deletions": 1},
            {"path": "uv.lock", "additions": 10, "deletions": 10},
        ],
    }
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((command, kwargs.get("input")))
        if "view" in command:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(snapshot))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(dependabot_changelog.subprocess, "run", fake_run)

    dependabot_changelog.update_pr(
        pr=16,
        repo="txnguyen292/agentic_job_scraping",
        dependency_names="rich",
        update_type="version-update:semver-major",
        previous_version="13.9.4",
        new_version="15.0.0",
        package_ecosystem="pip",
    )

    assert calls[0][0][-2:] == ["--json", "body,files"]
    assert calls[1][0][-2:] == ["--body-file", "-"]
    published_body = calls[1][1]
    assert published_body is not None
    assert "<summary>Release notes</summary>" in published_body
    assert "<summary>Changelog</summary>" in published_body
    assert "upstream dump" not in published_body
