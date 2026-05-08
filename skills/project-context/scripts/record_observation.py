#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import typer


app = typer.Typer(add_completion=False, help="Record a compact extraction note in project context.")


def repo_root() -> Path:
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


def run_context_command(args: list[str]) -> None:
    root = repo_root()
    result = subprocess.run(
        [str(root / ".contexts" / "bin" / args[0]), *args[1:]],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(
            json.dumps({"status": "error", "returncode": result.returncode, "stderr": result.stderr[-2000:]}),
            file=sys.stderr,
        )
        raise typer.Exit(result.returncode)
    print(result.stdout.strip())


@app.command()
def main(
    task_id: str = typer.Option("T-001", "--task-id", help="Context task id this observation belongs to."),
    observations: str = typer.Option(
        "",
        "--observations",
        help="What the agent observed from bounded evidence, such as repeated job-card markers or URL patterns.",
    ),
    extraction_plan: str = typer.Option(
        "",
        "--extraction-plan",
        help="How to get the required outputs from the observations, including selectors, filters, and expected count.",
    ),
    note: str = typer.Option(
        "",
        "--note",
        help="Compatibility shortcut for a compact observation note when observations/extraction-plan were not split.",
    ),
    comparison: str = typer.Option("", "--comparison", help="How the latest script output compares with requirements."),
    next_action: str = typer.Option("", "--next-action", help="What the agent will try next."),
    audit_id: str = typer.Option("", "--audit-id", help="Sandbox audit id when available."),
    evidence_path: list[str] = typer.Option(None, "--evidence-path", help="Relevant artifact or sandbox path."),
) -> None:
    if note and not observations:
        observations = note
    if not observations:
        print(json.dumps({"status": "error", "error": "observations or note is required"}, ensure_ascii=True))
        raise typer.Exit(2)
    if not extraction_plan:
        extraction_plan = "not specified by runtime note; derive from observations before the next extractor attempt"

    parts = [f"observations: {observations}", f"extraction_plan: {extraction_plan}"]
    if comparison:
        parts.append(f"comparison: {comparison}")
    if next_action:
        parts.append(f"next: {next_action}")
    if audit_id:
        parts.append(f"audit_id: {audit_id}")
    summary = " | ".join(parts)

    command = ["append_lineage", "runtime_extraction_note", summary, "--task-id", task_id]
    for path in evidence_path or []:
        command.extend(["--file", path])
    if audit_id:
        command.extend(["--link", audit_id])
    run_context_command(command)


if __name__ == "__main__":
    app()
