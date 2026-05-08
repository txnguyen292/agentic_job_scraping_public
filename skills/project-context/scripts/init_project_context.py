#!/usr/bin/env python3
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from _bootstrap import ensure_runtime

ensure_runtime()

import typer
from loguru import logger

from _support import abort, configure_logging, emit_path


app = typer.Typer(add_completion=False, help="Scaffold progressive-disclosure project context files.")


TOKENS = {
    "{{DATE}}": date.today().isoformat(),
}

WRAPPER_COMMANDS = [
    "context_overview",
    "list_tasks",
    "list_decisions",
    "list_references",
    "get_context_meta",
    "get_working_context",
    "list_links",
    "load_resource",
    "update_task",
    "update_handoff",
    "update_working_context",
    "clear_working_context",
    "append_lineage",
    "validate_context",
]


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def template_root() -> Path:
    return skill_root() / "assets" / "templates"


def render_text(text: str, project_name: str) -> str:
    rendered = text.replace("{{PROJECT_NAME}}", project_name)
    for key, value in TOKENS.items():
        rendered = rendered.replace(key, value)
    return rendered


def write_template_file(source: Path, destination: Path, project_name: str, overwrite: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        abort(f"Refusing to overwrite existing file: {destination}")
    text = source.read_text(encoding="utf-8")
    destination.write_text(render_text(text, project_name), encoding="utf-8")


def write_wrapper(path: Path, command_name: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        abort(f"Refusing to overwrite existing file: {path}")
    contents = f"""#!/usr/bin/env sh
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
TOOLS_DIR="$SCRIPT_DIR/../tools"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
"$TOOLS_DIR/ensure_env.sh"
exec "$ROOT_DIR/.venv/bin/python" "$TOOLS_DIR/context_cli.py" {command_name} "$@"
"""
    path.write_text(contents, encoding="utf-8")
    os.chmod(path, 0o755)


@app.command()
def main(
    target_dir: Path = typer.Argument(Path("."), help="Target project directory."),
    project_name: str = typer.Option("Project", help="Human-readable project name."),
    overwrite: bool = typer.Option(False, help="Overwrite existing files."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)

    source_root = template_root()
    target_root = target_dir.expanduser().resolve()

    files_to_copy = [
        Path("AGENTS.md"),
        Path(".contexts/index.md"),
        Path(".contexts/current-state.md"),
        Path(".contexts/handoff.md"),
        Path(".contexts/templates/task.md"),
        Path(".contexts/templates/decision.md"),
        Path(".contexts/templates/reference.md"),
        Path(".contexts/templates/working.md"),
        Path(".contexts/tools/context_cli.py"),
        Path(".contexts/tools/ensure_env.sh"),
        Path(".contexts/tools/requirements.txt"),
    ]

    directories_to_create = [
        Path(".contexts/tasks"),
        Path(".contexts/decisions"),
        Path(".contexts/references"),
        Path(".contexts/working"),
        Path(".contexts/lineage"),
        Path(".contexts/bin"),
        Path(".contexts/tools"),
    ]

    for directory in directories_to_create:
        (target_root / directory).mkdir(parents=True, exist_ok=True)

    for relative_path in files_to_copy:
        write_template_file(
            source_root / relative_path,
            target_root / relative_path,
            project_name,
            overwrite,
        )

    for command_name in WRAPPER_COMMANDS:
        write_wrapper(target_root / ".contexts/bin" / command_name, command_name, overwrite)

    os.chmod(target_root / ".contexts/tools/ensure_env.sh", 0o755)

    lineage_path = target_root / ".contexts/lineage/events.jsonl"
    if lineage_path.exists() and not overwrite:
        abort(f"Refusing to overwrite existing file: {lineage_path}")
    lineage_path.write_text("", encoding="utf-8")

    logger.info("Initialized project context in {}", target_root)
    emit_path(target_root)


if __name__ == "__main__":
    app()
