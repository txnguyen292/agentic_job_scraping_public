#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from _bootstrap import ensure_runtime

ensure_runtime()

import typer
import yaml
from loguru import logger

from _support import configure_logging, render_error_table


app = typer.Typer(add_completion=False, help="Validate progressive-disclosure project context files.")


REQUIRED_FILES = [
    Path("AGENTS.md"),
    Path(".contexts/index.md"),
    Path(".contexts/current-state.md"),
    Path(".contexts/handoff.md"),
    Path(".contexts/lineage/events.jsonl"),
    Path(".contexts/tools/context_cli.py"),
    Path(".contexts/tools/ensure_env.sh"),
    Path(".contexts/tools/requirements.txt"),
    Path(".contexts/templates/working.md"),
]

DOC_RULES = [
    ("task", Path(".contexts/tasks"), "id", ("id", "kind", "status", "updated_at")),
    ("decision", Path(".contexts/decisions"), "id", ("id", "kind", "status", "updated_at")),
    ("reference", Path(".contexts/references"), "id", ("id", "kind", "updated_at")),
    ("working", Path(".contexts/working"), "id", ("id", "kind", "task_id", "updated_at")),
]

SINGLETON_RULES = [
    (Path(".contexts/index.md"), ("id", "kind", "updated_at")),
    (Path(".contexts/current-state.md"), ("id", "kind", "updated_at")),
    (Path(".contexts/handoff.md"), ("id", "kind", "updated_at")),
]

REQUIRED_WRAPPERS = [
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


def parse_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter")

    collected: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            payload = yaml.safe_load("\n".join(collected)) or {}
            if not isinstance(payload, dict):
                raise ValueError("frontmatter must decode to a mapping")
            return payload
        collected.append(line)

    raise ValueError("frontmatter is not closed")


def validate_required_files(root: Path, errors: list[str]) -> None:
    for relative_path in REQUIRED_FILES:
        if not (root / relative_path).exists():
            errors.append(f"missing required file: {relative_path}")


def validate_doc_group(
    root: Path,
    directory: Path,
    id_key: str,
    required_keys: tuple[str, ...],
    seen_ids: set[str],
    errors: list[str],
) -> None:
    full_dir = root / directory
    if not full_dir.exists():
        errors.append(f"missing required directory: {directory}")
        return

    for path in sorted(full_dir.glob("*.md")):
        try:
            frontmatter = parse_frontmatter(path)
        except ValueError as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
            continue

        for key in required_keys:
            if not frontmatter.get(key):
                errors.append(f"{path.relative_to(root)}: missing required frontmatter key `{key}`")

        doc_id = frontmatter.get(id_key)
        if not doc_id:
            continue
        if doc_id in seen_ids:
            errors.append(f"{path.relative_to(root)}: duplicate id `{doc_id}`")
            continue
        seen_ids.add(doc_id)


def validate_singletons(root: Path, errors: list[str]) -> None:
    for relative_path, required_keys in SINGLETON_RULES:
        path = root / relative_path
        if not path.exists():
            continue
        try:
            frontmatter = parse_frontmatter(path)
        except ValueError as exc:
            errors.append(f"{relative_path}: {exc}")
            continue
        for key in required_keys:
            if not frontmatter.get(key):
                errors.append(f"{relative_path}: missing required frontmatter key `{key}`")


def validate_wrappers(root: Path, errors: list[str]) -> None:
    wrapper_dir = root / ".contexts/bin"
    if not wrapper_dir.exists():
        errors.append("missing required directory: .contexts/bin")
        return
    for command_name in REQUIRED_WRAPPERS:
        if not (wrapper_dir / command_name).exists():
            errors.append(f"missing required wrapper: .contexts/bin/{command_name}")


def validate_lineage(root: Path, errors: list[str]) -> None:
    path = root / ".contexts/lineage/events.jsonl"
    if not path.exists():
        return

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            errors.append(f".contexts/lineage/events.jsonl:{line_number}: invalid JSON ({exc.msg})")
            continue

        for key in ("ts", "type", "summary"):
            if key not in payload or payload[key] in ("", None):
                errors.append(f".contexts/lineage/events.jsonl:{line_number}: missing required key `{key}`")


@app.command()
def main(
    target_dir: Path = typer.Argument(Path("."), help="Target project directory."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)

    root = target_dir.expanduser().resolve()
    errors: list[str] = []

    validate_required_files(root, errors)
    validate_singletons(root, errors)
    validate_wrappers(root, errors)

    for _label, directory, id_key, required_keys in DOC_RULES:
        validate_doc_group(root, directory, id_key, required_keys, set(), errors)

    validate_lineage(root, errors)

    if errors:
        logger.error("project context validation failed with {} issue(s)", len(errors))
        render_error_table("Project Context Validation Errors", errors)
        raise typer.Exit(1)

    logger.info("project context is valid")
    typer.echo("project context is valid")


if __name__ == "__main__":
    app()
