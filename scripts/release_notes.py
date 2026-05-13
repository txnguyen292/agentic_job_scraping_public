"""Manage PR release notes from CHANGELOG.md."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer


START_MARKER = "<!-- release-notes:start -->"
END_MARKER = "<!-- release-notes:end -->"
DEFAULT_CHANGELOG = Path("CHANGELOG.md")

app = typer.Typer(add_completion=False, help="Render or publish release notes from CHANGELOG.md.")


def extract_unreleased(changelog_text: str) -> str:
    lines = changelog_text.splitlines()
    start_index: int | None = None

    for index, line in enumerate(lines):
        if line.strip().lower() == "## unreleased":
            start_index = index + 1
            break

    if start_index is None:
        raise ValueError("CHANGELOG.md must contain a '## Unreleased' section.")

    end_index = len(lines)
    for index in range(start_index, len(lines)):
        line = lines[index].strip()
        if line.startswith("## ") and line.lower() != "## unreleased":
            end_index = index
            break

    return "\n".join(lines[start_index:end_index]).strip()


def render_details(notes: str) -> str:
    if not notes.strip():
        raise ValueError("CHANGELOG.md '## Unreleased' section must not be empty.")

    return "\n".join(
        [
            START_MARKER,
            "<details>",
            "<summary>Release notes</summary>",
            "",
            notes.strip(),
            "",
            "</details>",
            END_MARKER,
        ]
    )


def replace_managed_block(body: str, rendered_notes: str) -> str:
    if START_MARKER in body and END_MARKER in body:
        prefix, rest = body.split(START_MARKER, 1)
        _, suffix = rest.split(END_MARKER, 1)
        return f"{prefix}{rendered_notes}{suffix}"

    release_heading = "## Release Notes"
    if release_heading in body:
        prefix, suffix = body.split(release_heading, 1)
        next_heading_index = suffix.find("\n## ")
        if next_heading_index == -1:
            remainder = ""
        else:
            remainder = suffix[next_heading_index:]
        return f"{prefix}{release_heading}\n\n{rendered_notes}{remainder}"

    body = body.rstrip()
    return f"{body}\n\n## Release Notes\n\n{rendered_notes}\n"


def load_rendered_notes(changelog: Path) -> str:
    return render_details(extract_unreleased(changelog.read_text(encoding="utf-8")))


@app.command("print")
def print_notes(
    changelog: Annotated[Path, typer.Option("--changelog", help="Path to CHANGELOG.md.")] = DEFAULT_CHANGELOG,
) -> None:
    """Print the rendered collapsible release notes block."""
    typer.echo(load_rendered_notes(changelog))


@app.command()
def check(
    changelog: Annotated[Path, typer.Option("--changelog", help="Path to CHANGELOG.md.")] = DEFAULT_CHANGELOG,
) -> None:
    """Fail if the changelog has no usable Unreleased release notes."""
    load_rendered_notes(changelog)
    typer.echo("Release notes are present.")


@app.command("update-pr")
def update_pr(
    pr: Annotated[int, typer.Option("--pr", help="Pull request number to update.")],
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="GitHub repository in OWNER/REPO form. Defaults to the current checkout."),
    ] = None,
    changelog: Annotated[Path, typer.Option("--changelog", help="Path to CHANGELOG.md.")] = DEFAULT_CHANGELOG,
) -> None:
    """Update a pull request body with the rendered release notes block."""
    rendered_notes = load_rendered_notes(changelog)
    view_command = ["gh", "pr", "view", str(pr), "--json", "body"]
    edit_command = ["gh", "pr", "edit", str(pr), "--body-file", "-"]

    if repo:
        view_command.extend(["--repo", repo])
        edit_command.extend(["--repo", repo])

    body_result = subprocess.run(view_command, check=True, capture_output=True, text=True)
    body = json.loads(body_result.stdout)["body"] or ""
    updated_body = replace_managed_block(body, rendered_notes)

    if updated_body == body:
        typer.echo("Release notes block is already up to date.")
        return

    subprocess.run(edit_command, input=updated_body, check=True, text=True)
    typer.echo(f"Updated release notes for PR #{pr}.")


if __name__ == "__main__":
    app()
