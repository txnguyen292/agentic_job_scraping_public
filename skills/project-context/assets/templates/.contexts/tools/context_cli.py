#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from loguru import logger
from rich.console import Console
from rich.table import Table


app = typer.Typer(add_completion=False, help="Local project-context command surface.")
console = Console(stderr=True)


@dataclass
class ContextDoc:
    path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def doc_id(self) -> str:
        return str(self.frontmatter.get("id", self.path.stem))

    @property
    def kind(self) -> str:
        return str(self.frontmatter.get("kind", "document"))

    @property
    def title(self) -> str:
        for line in self.body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return self.doc_id

    @property
    def summary(self) -> str:
        frontmatter_summary = self.frontmatter.get("summary")
        if isinstance(frontmatter_summary, str) and frontmatter_summary.strip():
            return frontmatter_summary.strip()
        return extract_section(self.body, "Summary").strip()

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.doc_id,
            "kind": self.kind,
            "title": self.title,
            "status": self.frontmatter.get("status"),
            "updated_at": stringify(self.frontmatter.get("updated_at")),
            "summary": self.summary,
            "read_next": stringify(ensure_list(self.frontmatter.get("read_next"))),
            "related": stringify(ensure_list(self.frontmatter.get("related_docs"))),
            "path": str(self.path.relative_to(repo_root())),
        }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def contexts_root() -> Path:
    return repo_root() / ".contexts"


def configure_logging(debug: bool = False) -> None:
    logger.remove()
    logger.add(
        console.file,
        level="DEBUG" if debug else "INFO",
        format="<level>{level: <8}</level> | {message}",
    )


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def stringify(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): stringify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stringify(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            return str(value)
    return value


def emit(payload: Any, pretty: bool) -> None:
    payload = stringify(payload)
    if pretty:
        console.print_json(json.dumps(payload, ensure_ascii=True))
        return
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=True))


def fail(message: str, code: int = 1) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    payload: list[str] = []
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            frontmatter = yaml.safe_load("\n".join(payload)) or {}
            if not isinstance(frontmatter, dict):
                fail("frontmatter must decode to a mapping")
            body = "\n".join(lines[index + 1 :]).lstrip("\n")
            return frontmatter, body
        payload.append(line)

    fail("frontmatter is not closed")
    raise AssertionError("unreachable")


def read_doc(path: Path) -> ContextDoc:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    return ContextDoc(path=path, frontmatter=frontmatter, body=body)


def write_doc(doc: ContextDoc) -> None:
    frontmatter_text = yaml.safe_dump(doc.frontmatter, sort_keys=False, allow_unicode=False).strip()
    serialized = f"---\n{frontmatter_text}\n---\n\n{doc.body.rstrip()}\n"
    doc.path.write_text(serialized, encoding="utf-8")


def extract_section(body: str, section_name: str) -> str:
    lines = body.splitlines()
    capture = False
    collected: list[str] = []
    target = f"## {section_name}".strip()
    for line in lines:
        if line.startswith("## "):
            if capture:
                break
            capture = line.strip() == target
            continue
        if capture:
            collected.append(line)
    return "\n".join(collected).strip()


def replace_section(body: str, section_name: str, new_content: str) -> str:
    lines = body.splitlines()
    target = f"## {section_name}"
    output: list[str] = []
    capture = False
    replaced = False

    for line in lines:
        if line.startswith("## "):
            if capture:
                output.extend(new_content.rstrip().splitlines())
                output.append("")
                capture = False
                replaced = True
            if line.strip() == target:
                output.append(line)
                output.append("")
                capture = True
                continue
        if not capture:
            output.append(line)

    if capture:
        output.extend(new_content.rstrip().splitlines())
        replaced = True

    if not replaced:
        if output and output[-1] != "":
            output.append("")
        output.append(target)
        output.append("")
        output.extend(new_content.rstrip().splitlines())

    return "\n".join(output).rstrip() + "\n"


def render_bullets(items: list[str], *, fallback: str) -> str:
    if not items:
        return f"- {fallback}"
    return "\n".join(f"- {item}" for item in items)


def doc_paths(kind: str) -> list[Path]:
    base = contexts_root()
    mapping = {
        "task": sorted((base / "tasks").glob("*.md")),
        "decision": sorted((base / "decisions").glob("*.md")),
        "reference": sorted((base / "references").glob("*.md")),
        "working": sorted((base / "working").glob("*.md")),
    }
    return mapping[kind]


def all_docs() -> list[ContextDoc]:
    base = contexts_root()
    paths = [
        base / "index.md",
        base / "current-state.md",
        base / "handoff.md",
        *doc_paths("task"),
        *doc_paths("decision"),
        *doc_paths("reference"),
        *doc_paths("working"),
    ]
    return [read_doc(path) for path in paths if path.exists()]


def find_doc(doc_id: str) -> ContextDoc:
    normalized = doc_id.split(":", 1)[-1]
    for doc in all_docs():
        if doc.doc_id == normalized:
            return doc
    fail(f"context document not found: {doc_id}")
    raise AssertionError("unreachable")


def collect_links(frontmatter: dict[str, Any]) -> dict[str, list[Any]]:
    keys = [
        "read_next",
        "related_docs",
        "decision_ids",
        "depends_on",
        "blocked_by",
        "task_ids",
        "supersedes",
        "applies_to",
    ]
    return {key: ensure_list(frontmatter.get(key)) for key in keys if ensure_list(frontmatter.get(key))}


def validation_errors() -> list[str]:
    base = contexts_root()
    errors: list[str] = []
    required_files = [
        Path("index.md"),
        Path("current-state.md"),
        Path("handoff.md"),
        Path("lineage/events.jsonl"),
        Path("templates/working.md"),
        Path("tools/context_cli.py"),
        Path("tools/ensure_env.sh"),
        Path("tools/requirements.txt"),
    ]
    for relative_path in required_files:
        if not (base / relative_path).exists():
            errors.append(f"missing required file: .contexts/{relative_path}")

    for directory in ("tasks", "decisions", "references", "working", "bin", "tools"):
        if not (base / directory).exists():
            errors.append(f"missing required directory: .contexts/{directory}")

    seen_ids: set[str] = set()
    for doc in all_docs():
        for key in ("id", "kind", "updated_at"):
            if not doc.frontmatter.get(key):
                errors.append(f"{doc.path.relative_to(repo_root())}: missing required frontmatter key `{key}`")
        doc_id = doc.frontmatter.get("id")
        if doc_id:
            if doc_id in seen_ids:
                errors.append(f"{doc.path.relative_to(repo_root())}: duplicate id `{doc_id}`")
            seen_ids.add(str(doc_id))

    lineage_path = base / "lineage/events.jsonl"
    if lineage_path.exists():
        for line_number, raw_line in enumerate(lineage_path.read_text(encoding="utf-8").splitlines(), start=1):
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

    return errors


@app.command("context_overview")
def context_overview(
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    current_state = find_doc("current-state").meta()
    handoff = find_doc("handoff").meta()
    tasks = [
        doc.meta()
        for doc in all_docs()
        if doc.kind == "task" and str(doc.frontmatter.get("status", "")).lower() in {"active", "in_progress", "blocked"}
    ]
    payload = {
        "project": repo_root().name,
        "current_state": current_state,
        "handoff": handoff,
        "active_tasks": tasks,
        "recommended_next": [
            "list_tasks --status active",
            "get_context_meta <id>",
            "decide whether more context is needed",
            "list_links <id>",
        ],
    }
    emit(payload, pretty)


@app.command("list_tasks")
def list_tasks(
    status: Optional[str] = typer.Option(None, help="Optional status filter."),
    limit: int = typer.Option(20, help="Maximum number of tasks to return."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    tasks = []
    for doc in all_docs():
        if doc.kind != "task":
            continue
        doc_status = str(doc.frontmatter.get("status", "")).lower()
        if status and doc_status != status.lower():
            continue
        tasks.append(doc.meta())
    payload = {"items": tasks[:limit]}
    emit(payload, pretty)


@app.command("list_decisions")
def list_decisions(
    task: Optional[str] = typer.Option(None, help="Optional task id filter."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    items = []
    for doc in all_docs():
        if doc.kind != "decision":
            continue
        if task and task not in ensure_list(doc.frontmatter.get("task_ids")):
            continue
        items.append(doc.meta())
    emit({"items": items}, pretty)


@app.command("list_references")
def list_references(
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    items = [doc.meta() for doc in all_docs() if doc.kind == "reference"]
    emit({"items": items}, pretty)


@app.command("get_context_meta")
def get_context_meta(
    doc_id: str = typer.Argument(..., help="Context document id."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    doc = find_doc(doc_id)
    payload = doc.meta() | {"links": collect_links(doc.frontmatter)}
    emit(payload, pretty)


@app.command("get_working_context")
def get_working_context(
    task_id: str = typer.Argument(..., help="Task id for the working note."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    for doc in all_docs():
        if doc.kind == "working" and str(doc.frontmatter.get("task_id")) == task_id:
            emit(doc.meta() | {"links": collect_links(doc.frontmatter)}, pretty)
            return
    emit({"task_id": task_id, "working_context": None}, pretty)


@app.command("list_links")
def list_links(
    doc_id: str = typer.Argument(..., help="Context document id."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    doc = find_doc(doc_id)
    emit({"id": doc.doc_id, "links": collect_links(doc.frontmatter)}, pretty)


@app.command("load_resource")
def load_resource(
    doc_id: str = typer.Argument(..., help="Context document id."),
    section: Optional[str] = typer.Option(None, help="Specific section to load."),
    full: bool = typer.Option(False, "--full", help="Load the entire document."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    doc = find_doc(doc_id)
    if full:
        content = doc.path.read_text(encoding="utf-8")
    elif section:
        content = extract_section(doc.body, section)
    else:
        content = doc.body
    emit({"id": doc.doc_id, "path": str(doc.path.relative_to(repo_root())), "content": content}, pretty)


@app.command("update_task")
def update_task(
    doc_id: str = typer.Argument(..., help="Task id."),
    status: Optional[str] = typer.Option(None, help="Updated status."),
    owner: Optional[str] = typer.Option(None, help="Updated owner."),
    summary: Optional[str] = typer.Option(None, help="Updated summary."),
    next_step: Optional[str] = typer.Option(None, help="Updated next step."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    doc = find_doc(doc_id)
    if doc.kind != "task":
        fail(f"{doc_id} is not a task document")

    if status is not None:
        doc.frontmatter["status"] = status
    if owner is not None:
        doc.frontmatter["owner"] = owner
    if summary is not None:
        doc.frontmatter["summary"] = summary
    if next_step is not None:
        doc.frontmatter["next_step"] = next_step
    doc.frontmatter["updated_at"] = datetime.now().astimezone().date().isoformat()

    summary_section = "\n".join(
        [
            f"What this task is: {doc.frontmatter.get('summary', '')}",
            f"Why it exists: {doc.frontmatter.get('why', '')}",
            f"Current status: {doc.frontmatter.get('status', '')}",
            f"Done means: {doc.frontmatter.get('done_when', '')}",
            f"Exact next step: {doc.frontmatter.get('next_step', '')}",
        ]
    )
    doc.body = replace_section(doc.body, "Summary", summary_section)
    write_doc(doc)
    emit({"updated": doc.doc_id, "path": str(doc.path.relative_to(repo_root()))}, pretty)


@app.command("update_handoff")
def update_handoff(
    active_task: Optional[str] = typer.Option(None, help="Active task id."),
    summary: Optional[str] = typer.Option(None, help="Last meaningful change."),
    next_step: Optional[str] = typer.Option(None, help="Exact next step."),
    touched_file: list[str] = typer.Option(None, "--touched-file", help="Touched file path."),
    verification: list[str] = typer.Option(None, help="Verification entry."),
    risk: list[str] = typer.Option(None, "--risk", help="Risk or blocker."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    doc = find_doc("handoff")

    if active_task is not None:
        doc.frontmatter["active_task"] = active_task
    if summary is not None:
        doc.frontmatter["summary"] = summary
    if next_step is not None:
        doc.frontmatter["next_step"] = next_step
    if touched_file:
        doc.frontmatter["touched_files"] = touched_file
    if verification:
        doc.frontmatter["verification"] = verification
    if risk:
        doc.frontmatter["risks"] = risk
    doc.frontmatter["updated_at"] = datetime.now().astimezone().date().isoformat()

    summary_section = "\n".join(
        [
            f"Last meaningful change: {doc.frontmatter.get('summary', '')}",
            f"Current focus: {doc.frontmatter.get('active_task', '')}",
            f"Exact next step: {doc.frontmatter.get('next_step', '')}",
        ]
    )
    doc.body = replace_section(doc.body, "Summary", summary_section)
    doc.body = replace_section(
        doc.body,
        "Touched Files",
        render_bullets(ensure_list(doc.frontmatter.get("touched_files")), fallback="None yet."),
    )
    doc.body = replace_section(
        doc.body,
        "Verification",
        render_bullets(ensure_list(doc.frontmatter.get("verification")), fallback="Not run."),
    )
    doc.body = replace_section(
        doc.body,
        "Risks / Blockers",
        render_bullets(ensure_list(doc.frontmatter.get("risks")), fallback="None recorded."),
    )
    write_doc(doc)
    emit({"updated": doc.doc_id, "path": str(doc.path.relative_to(repo_root()))}, pretty)


@app.command("update_working_context")
def update_working_context(
    task_id: str = typer.Argument(..., help="Task id."),
    summary: Optional[str] = typer.Option(None, help="Checkpoint summary."),
    current_subproblem: Optional[str] = typer.Option(None, help="Current subproblem."),
    hypothesis: list[str] = typer.Option(None, "--hypothesis", help="Hypothesis entry."),
    open_question: list[str] = typer.Option(None, "--open-question", help="Open question."),
    next_step: Optional[str] = typer.Option(None, help="Exact next substep."),
    active_file: list[str] = typer.Option(None, "--active-file", help="Active file path."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    working_dir = contexts_root() / "working"
    path = working_dir / f"{task_id}.md"
    if path.exists():
        doc = read_doc(path)
    else:
        template = read_doc(contexts_root() / "templates/working.md")
        doc = ContextDoc(path=path, frontmatter=dict(template.frontmatter), body=template.body)
        doc.frontmatter["id"] = f"W-{task_id}"
        doc.frontmatter["task_id"] = task_id
        doc.frontmatter["kind"] = "working"
        doc.frontmatter["related_docs"] = [task_id]
        doc.frontmatter["read_next"] = [task_id]

    if summary is not None:
        doc.frontmatter["summary"] = summary
    if active_file:
        doc.frontmatter["active_files"] = active_file
    if open_question:
        doc.frontmatter["open_questions"] = open_question
    if next_step is not None:
        doc.frontmatter["next_step"] = next_step
    doc.frontmatter["updated_at"] = datetime.now().astimezone().date().isoformat()

    if current_subproblem is not None:
        doc.body = replace_section(doc.body, "Current Subproblem", current_subproblem)
    if hypothesis:
        doc.body = replace_section(doc.body, "Hypotheses", render_bullets(hypothesis, fallback="None recorded."))
    if open_question:
        doc.body = replace_section(doc.body, "Open Questions", render_bullets(open_question, fallback="None recorded."))
    if next_step is not None:
        doc.body = replace_section(doc.body, "Next Checkpoint", f"- {next_step}" if next_step else "- None recorded.")

    summary_section = "\n".join(
        [
            f"Current checkpoint: {doc.frontmatter.get('summary', '')}",
            "Why this checkpoint exists: context-loss risk became meaningful during a large delta.",
            f"Exact next substep: {doc.frontmatter.get('next_step', '')}",
        ]
    )
    doc.body = replace_section(doc.body, "Summary", summary_section)
    write_doc(doc)
    emit({"updated": doc.doc_id, "path": str(doc.path.relative_to(repo_root()))}, pretty)


@app.command("clear_working_context")
def clear_working_context(
    task_id: str = typer.Argument(..., help="Task id."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    path = contexts_root() / "working" / f"{task_id}.md"
    if not path.exists():
        emit({"cleared": False, "task_id": task_id, "reason": "working context not found"}, pretty)
        return
    path.unlink()
    emit({"cleared": True, "task_id": task_id, "path": str(path.relative_to(repo_root()))}, pretty)


@app.command("append_lineage")
def append_lineage(
    type: str = typer.Argument(..., help="Event type."),
    summary: str = typer.Argument(..., help="Event summary."),
    task_id: Optional[str] = typer.Option(None, help="Associated task id."),
    decision_id: Optional[str] = typer.Option(None, help="Associated decision id."),
    file: list[str] = typer.Option(None, "--file", help="Touched file path."),
    verification: Optional[str] = typer.Option(None, help="Verification command or summary."),
    agent: str = typer.Option("codex", help="Agent name."),
    session_id: Optional[str] = typer.Option(None, help="Session id."),
    branch: Optional[str] = typer.Option(None, help="Git branch."),
    link: list[str] = typer.Option(None, "--link", help="Related link or doc id."),
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    payload = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "type": type,
        "summary": summary,
        "agent": agent,
    }
    if task_id:
        payload["task_id"] = task_id
    if decision_id:
        payload["decision_id"] = decision_id
    if file:
        payload["files"] = file
    if verification:
        payload["verification"] = verification
    if session_id:
        payload["session_id"] = session_id
    if branch:
        payload["branch"] = branch
    if link:
        payload["links"] = link

    lineage_path = contexts_root() / "lineage/events.jsonl"
    with lineage_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    emit(payload, pretty)


@app.command("validate_context")
def validate_context(
    pretty: bool = typer.Option(False, help="Render pretty JSON."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)
    errors = validation_errors()
    if errors:
        table = Table(title="Context Validation Errors")
        table.add_column("#", style="cyan", no_wrap=True)
        table.add_column("Issue", style="red")
        for index, error in enumerate(errors, start=1):
            table.add_row(str(index), error)
        console.print(table)
        if pretty:
            emit({"valid": False, "errors": errors}, pretty=True)
        raise typer.Exit(1)
    emit({"valid": True, "root": str(contexts_root())}, pretty)


if __name__ == "__main__":
    app()
