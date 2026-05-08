#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer


app = typer.Typer(add_completion=False, help="List recent compact extraction notes from project context.")


def find_project_root() -> Path:
    candidates: list[Path] = []
    if root := os.getenv("JOB_SCRAPER_PROJECT_ROOT"):
        candidates.append(Path(root).resolve())
    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])
    for candidate in candidates:
        if (candidate / ".contexts").exists():
            return candidate
    print(json.dumps({"status": "error", "error": ".contexts not found"}), file=sys.stderr)
    raise typer.Exit(1)


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


@app.command()
def main(
    task_id: str = typer.Option("T-001", "--task-id", help="Context task id to read notes for."),
    audit_id: str = typer.Option("", "--audit-id", help="Optional sandbox audit id/link to filter notes."),
    limit: int = typer.Option(5, "--limit", min=1, max=20, help="Maximum recent notes to return."),
) -> None:
    root = find_project_root()
    events = load_events(root / ".contexts" / "lineage" / "events.jsonl")
    notes = [
        event
        for event in events
        if event.get("type") == "runtime_extraction_note" and event.get("task_id") == task_id
    ]
    if audit_id:
        notes = [event for event in notes if audit_id in event.get("links", [])]

    selected = notes[-limit:]
    print(
        json.dumps(
            {
                "status": "success",
                "task_id": task_id,
                "audit_id": audit_id or None,
                "count": len(selected),
                "notes": selected,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    app()
