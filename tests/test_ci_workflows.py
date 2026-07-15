from __future__ import annotations

import re
from pathlib import Path


WORKFLOWS_DIR = Path(".github/workflows")


def _workflow(name: str) -> str:
    return (WORKFLOWS_DIR / name).read_text(encoding="utf-8")


def test_dependabot_policy_uses_supported_idempotent_label_lookup() -> None:
    workflow = _workflow("dependabot-update-policy.yml")

    assert "gh label view" not in workflow
    assert "gh label list" in workflow
    assert "grep -Fxq \"$name\"" in workflow
    assert "gh label create \"$name\"" in workflow
    assert "|| gh label edit \"$name\"" in workflow


def test_dependabot_policy_replaces_upstream_dump_with_repository_changelog() -> None:
    workflow = _workflow("dependabot-update-policy.yml")

    assert "- name: Publish repository dependency changelog" in workflow
    assert "python scripts/dependabot_changelog.py" in workflow
    assert "--dependency-names \"$DEPENDENCIES\"" in workflow
    assert "--previous-version \"$PREVIOUS_VERSION\"" in workflow
    assert "--new-version \"$NEW_VERSION\"" in workflow
    assert "--update-type \"$UPDATE_TYPE\"" in workflow
    assert "--package-ecosystem \"$PACKAGE_ECOSYSTEM\"" in workflow
    assert "Review the repository dependency changelog" in workflow
    assert "Review the upstream changelog" not in workflow


def test_dependency_security_refresh_uses_supported_idempotent_label_lookup() -> None:
    workflow = _workflow("dependency-security-refresh.yml")

    assert "gh label view" not in workflow
    assert "gh label list" in workflow
    assert "grep -Fxq \"$name\"" in workflow
    assert "gh label create \"$name\"" in workflow
    assert "|| gh label edit \"$name\"" in workflow


def test_dependency_security_refresh_can_use_repo_secret_for_pr_publication() -> None:
    workflow = _workflow("dependency-security-refresh.yml")
    automation_token = "${{ secrets.DEPENDENCY_REFRESH_PR_TOKEN || github.token }}"

    assert f"token: {automation_token}" in workflow
    assert f"GH_TOKEN: {automation_token}" in workflow
    assert "DEPENDENCY_REFRESH_PR_TOKEN repository secret" in workflow


def test_internal_ci_skips_fragment_release_paths_for_dependabot_prs() -> None:
    workflow = _workflow("internal-ci.yml")
    dependabot_guard = (
        "github.event_name == 'pull_request' && "
        "github.event.pull_request.user.login != 'dependabot[bot]'"
    )

    assert "run: uv run pytest" in workflow
    assert "run: .contexts/bin/validate_context" in workflow
    assert "run: uv run python scripts/sync_public.py plan --json" in workflow
    assert re.search(
        rf"- name: Validate release notes\n\s+if: {re.escape(dependabot_guard)}\n"
        r"\s+run: uv run python scripts/release_notes.py check --base-ref",
        workflow,
    )
    assert re.search(
        rf"release-notes-pr:\n\s+name: Surface release notes in PR\n\s+if: {re.escape(dependabot_guard)}\n",
        workflow,
    )
    assert re.search(
        rf"public-export-pr:\n\s+name: Open sanitized public export PR\n\s+if: {re.escape(dependabot_guard)}\n",
        workflow,
    )
