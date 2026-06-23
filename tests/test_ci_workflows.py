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
