from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml


CONTEXT_CLI_PATH = Path(".contexts/tools/context_cli.py")


def load_context_cli_module():
    module_name = "context_cli_for_tests"
    spec = importlib.util.spec_from_file_location(module_name, CONTEXT_CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def write_doc(path: Path, frontmatter: dict[str, object], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip() + "\n---\n\n" + body.rstrip() + "\n"
    path.write_text(serialized, encoding="utf-8")


def seed_context_tree(root: Path) -> None:
    today = date.today().isoformat()
    (root / ".contexts" / "tasks").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "decisions").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "references").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "working").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "bin").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "tools").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "templates").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "proposals").mkdir(parents=True, exist_ok=True)
    (root / ".contexts" / "lineage").mkdir(parents=True, exist_ok=True)

    (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    write_doc(
        root / ".contexts" / "index.md",
        {
            "id": "index",
            "kind": "index",
            "updated_at": today,
            "summary": "Index",
            "read_next": ["current-state"],
            "related_docs": ["current-state", "handoff"],
        },
        "# Index\n",
    )
    write_doc(
        root / ".contexts" / "current-state.md",
        {
            "id": "current-state",
            "kind": "current-state",
            "updated_at": today,
            "summary": "Project snapshot",
            "active_tasks": ["T-001"],
            "read_next": ["T-001", "handoff"],
            "related_docs": ["T-001", "handoff"],
        },
        "# Current State\n\n## Summary\n\nSnapshot\n",
    )
    write_doc(
        root / ".contexts" / "handoff.md",
        {
            "id": "handoff",
            "kind": "handoff",
            "updated_at": today,
            "summary": "Checkpoint",
            "active_task": "T-001",
            "next_step": "Review lineage",
            "touched_files": [".contexts/current-state.md"],
            "verification": ["seeded tree"],
            "risks": [],
            "related_docs": ["current-state"],
        },
        "# Handoff\n\n## Summary\n\nCheckpoint\n\n## Touched Files\n\n- None yet.\n\n## Verification\n\n- None yet.\n\n## Risks / Blockers\n\n- None yet.\n",
    )
    write_doc(
        root / ".contexts" / "tasks" / "T-001.md",
        {
            "id": "T-001",
            "kind": "task",
            "status": "active",
            "updated_at": today,
            "summary": "Task summary",
            "next_step": "Review lineage",
            "owner": "codex",
            "read_next": ["R-001"],
            "related_docs": ["current-state", "handoff"],
        },
        "# Task\n\n## Summary\n\nTask summary\n",
    )
    write_doc(
        root / ".contexts" / "templates" / "working.md",
        {
            "id": "W-000",
            "kind": "working",
            "task_id": "T-000",
            "updated_at": today,
            "summary": "",
            "active_files": [],
            "open_questions": [],
            "next_step": "",
            "related_docs": [],
        },
        "# Working Context\n\n## Summary\n\nWhat this working note is:\nCurrent status:\n\n## Current Subproblem\n\nDescribe the specific subproblem here.\n\n## Hypotheses\n\n- None yet.\n\n## Open Questions\n\n- None yet.\n\n## Next Checkpoint\n\nWhat the next agent should do next.\n\n## Links\n\n- Related task:\n",
    )
    write_doc(
        root / ".contexts" / "templates" / "task.md",
        {
            "id": "T-000",
            "kind": "task",
            "status": "proposed",
            "updated_at": today,
            "summary": "Task template",
            "read_next": [],
            "related_docs": [],
        },
        "# Task Template\n",
    )
    write_doc(
        root / ".contexts" / "templates" / "decision.md",
        {
            "id": "D-000",
            "kind": "decision",
            "status": "proposed",
            "updated_at": today,
            "summary": "Decision template",
            "task_ids": [],
            "supersedes": [],
            "read_next": [],
            "related_docs": [],
        },
        "# Decision Template\n",
    )
    write_doc(
        root / ".contexts" / "templates" / "reference.md",
        {
            "id": "R-000",
            "kind": "reference",
            "updated_at": today,
            "summary": "Reference template",
            "applies_to": [],
            "read_next": [],
            "related_docs": [],
        },
        "# Reference Template\n",
    )
    (root / ".contexts" / "lineage" / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": f"{today}T09:00:00+07:00",
                        "type": "implementation",
                        "summary": "Seeded context tree",
                        "task_id": "T-001",
                        "files": [".contexts/current-state.md"],
                    }
                ),
                json.dumps(
                    {
                        "ts": f"{today}T09:05:00+07:00",
                        "type": "validation",
                        "summary": "Validated seeded context tree",
                        "task_id": "T-001",
                        "verification": "seed setup",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / ".contexts" / "tools" / "context_cli.py").write_text("# placeholder\n", encoding="utf-8")
    (root / ".contexts" / "tools" / "ensure_env.sh").write_text("#!/usr/bin/env sh\n", encoding="utf-8")
    (root / ".contexts" / "tools" / "requirements.txt").write_text("typer\nrich\nloguru\npyyaml\n", encoding="utf-8")


def test_new_context_maintenance_commands_are_registered_and_wrapped() -> None:
    module = load_context_cli_module()

    command_names = {command.name for command in module.app.registered_commands}
    expected = {
        "context_health",
        "summarize_lineage",
        "context_drift_report",
        "get_working_context",
        "update_working_context",
        "clear_working_context",
        "record_context_review",
        "propose_skill_maintenance",
    }
    assert expected.issubset(command_names)

    for name in expected:
        path = Path(".contexts/bin") / name
        assert path.exists(), path
        assert os.access(path, os.X_OK), path


def test_working_context_round_trip_and_review_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_context_cli_module()
    seed_context_tree(tmp_path)
    monkeypatch.setattr(module, "repo_root", lambda: tmp_path)

    module.update_working_context(
        "T-001",
        summary="Track context maintenance changes",
        active_file=[".contexts/tools/context_cli.py"],
        open_question=["Should we split the umbrella task?"],
        next_step="Review lineage and drift",
        related_doc=["handoff"],
    )
    working_path = tmp_path / ".contexts" / "working" / "T-001.md"
    assert working_path.exists()
    update_payload = module.read_doc(working_path).meta()
    assert update_payload["task_id"] == "T-001"
    assert update_payload["summary"] == "Track context maintenance changes"

    assert "context_cli.py" in working_path.read_text(encoding="utf-8")

    health = module.context_health_payload()
    assert health["status"] == "ok"
    assert health["counts"]["working_contexts"] == 1

    drift = module.drift_report_payload()
    assert drift["status"] == "aligned"
    assert drift["issues"] == []

    module.record_context_review(
        "Reviewed maintenance surface and working context checkpoint",
        task_id="T-001",
        file=[".contexts/working/T-001.md"],
        verification="context_cli round-trip",
        agent="codex",
        session_id=None,
        branch=None,
        link=[],
    )
    events = module.read_lineage_events(root=tmp_path)
    assert events[-1]["type"] == "context-review"
    assert events[-1]["task_id"] == "T-001"
    lineage_payload = module.lineage_summary(module.read_lineage_events(root=tmp_path), limit=10)
    assert lineage_payload["total"] == 3
    assert lineage_payload["types"]["context-review"] == 1

    proposal_path = tmp_path / ".contexts" / "proposals" / "project-context-skill-maintenance-test.md"
    module.propose_skill_maintenance(output_path=proposal_path, task_id="T-001", limit=10, overwrite=True)
    assert proposal_path.exists()
    assert "Project-Context Skill Maintenance Draft" in proposal_path.read_text(encoding="utf-8")

    module.clear_working_context("T-001")
    assert not (tmp_path / ".contexts" / "working" / "T-001.md").exists()


def test_repo_context_validation_passes_after_maintenance_surface_update() -> None:
    module = load_context_cli_module()

    errors = module.validation_errors()

    assert errors == []
