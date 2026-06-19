"""Render and publish Linear release ledger documents from CHANGELOG.md."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Sequence

import typer
from scripts import release_notes


LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_DOCUMENT_TITLE = "Release Ledger"

app = typer.Typer(
    add_completion=False,
    help="Render or publish a Linear release ledger from CHANGELOG.md.",
)


@dataclass(frozen=True)
class ReleaseMetadata:
    title: str
    release_date: str
    internal_pr_url: str | None = None
    public_pr_url: str | None = None
    commit_sha: str | None = None
    tag: str | None = None
    related_issues: tuple[str, ...] = ()
    operational_notes: tuple[str, ...] = ()


def _non_empty_values(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(value.strip() for value in values or () if value.strip())


def _reject_raw_lineage(notes: Sequence[str]) -> None:
    for note in notes:
        stripped = note.strip()
        if not stripped or stripped[0] not in "[{":
            continue

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict | list):
            raise ValueError(
                "Operational notes must be human-readable summaries, not raw lineage JSON."
            )


def render_linear_markdown(changelog_notes: str, metadata: ReleaseMetadata) -> str:
    """Render a Linear-ready Markdown ledger entry."""
    changelog_notes = changelog_notes.strip()
    if not changelog_notes:
        raise ValueError("Changelog notes must not be empty.")

    operational_notes = _non_empty_values(metadata.operational_notes)
    _reject_raw_lineage(operational_notes)

    lines = [
        f"# {metadata.title}",
        "",
        "## Release Metadata",
        "",
        f"- Release date: {metadata.release_date}",
    ]

    links: list[str] = []
    if metadata.internal_pr_url:
        links.append(f"- Internal PR: {metadata.internal_pr_url}")
    if metadata.public_pr_url:
        links.append(f"- Public PR: {metadata.public_pr_url}")
    if metadata.commit_sha:
        links.append(f"- Commit: `{metadata.commit_sha}`")
    if metadata.tag:
        links.append(f"- Tag: `{metadata.tag}`")
    if metadata.related_issues:
        links.append(f"- Related issues: {', '.join(metadata.related_issues)}")

    if links:
        lines.extend(["", "## Links", "", *links])

    if operational_notes:
        lines.extend(["", "## Operational Notes", ""])
        lines.extend(f"- {note}" for note in operational_notes)

    lines.extend(["", "## Changelog", "", changelog_notes])
    return "\n".join(lines).rstrip() + "\n"


def load_linear_markdown(changelog: Path, metadata: ReleaseMetadata) -> str:
    """Load CHANGELOG.md and render the current Unreleased section for Linear."""
    notes = release_notes.extract_unreleased(changelog.read_text(encoding="utf-8"))
    return render_linear_markdown(notes, metadata)


def build_document_create_payload(
    *, project_id: str, title: str, content: str
) -> dict[str, Any]:
    return {
        "query": """
mutation CreateProjectDocument($input: DocumentCreateInput!) {
  documentCreate(input: $input) {
    success
    document {
      id
      title
      url
    }
  }
}
""".strip(),
        "variables": {
            "input": {
                "projectId": project_id,
                "title": title,
                "content": content,
            }
        },
    }


def build_document_update_payload(*, document_id: str, content: str) -> dict[str, Any]:
    return {
        "query": """
mutation UpdateDocument($id: String!, $input: DocumentUpdateInput!) {
  documentUpdate(id: $id, input: $input) {
    success
    document {
      id
      title
      url
    }
  }
}
""".strip(),
        "variables": {
            "id": document_id,
            "input": {"content": content},
        },
    }


def linear_graphql(
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float = 30,
    api_url: str = LINEAR_API_URL,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")

    decoded = json.loads(response_body)
    errors = decoded.get("errors")
    if errors:
        messages = [
            error.get("message", str(error)) if isinstance(error, dict) else str(error)
            for error in errors
        ]
        raise RuntimeError(f"Linear GraphQL failed: {'; '.join(messages)}")

    data = decoded.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Linear GraphQL response did not include a data object.")
    return data


def _metadata_from_options(
    *,
    release_title: str,
    release_date: str,
    internal_pr_url: str | None,
    public_pr_url: str | None,
    commit_sha: str | None,
    tag: str | None,
    related_issue: Sequence[str] | None,
    operational_note: Sequence[str] | None,
) -> ReleaseMetadata:
    return ReleaseMetadata(
        title=release_title,
        release_date=release_date,
        internal_pr_url=internal_pr_url,
        public_pr_url=public_pr_url,
        commit_sha=commit_sha,
        tag=tag,
        related_issues=_non_empty_values(related_issue),
        operational_notes=_non_empty_values(operational_note),
    )


@app.command()
def render(
    release_title: Annotated[
        str,
        typer.Option("--release-title", help="Title for the Linear release ledger entry."),
    ],
    release_date: Annotated[
        str,
        typer.Option("--release-date", help="Release date to show in the ledger entry."),
    ],
    changelog: Annotated[
        Path,
        typer.Option("--changelog", help="CHANGELOG.md path to read."),
    ] = release_notes.DEFAULT_CHANGELOG,
    internal_pr_url: Annotated[
        str | None,
        typer.Option("--internal-pr-url", help="Internal GitHub PR URL to include."),
    ] = None,
    public_pr_url: Annotated[
        str | None,
        typer.Option("--public-pr-url", help="Public export PR URL to include."),
    ] = None,
    commit_sha: Annotated[
        str | None,
        typer.Option("--commit-sha", help="Commit SHA to include."),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option("--tag", help="Git tag to include."),
    ] = None,
    related_issue: Annotated[
        list[str] | None,
        typer.Option("--related-issue", help="Related Linear issue key. Repeatable."),
    ] = None,
    operational_note: Annotated[
        list[str] | None,
        typer.Option("--operational-note", help="Human-readable operational note. Repeatable."),
    ] = None,
) -> None:
    """Print Linear-ready Markdown for the current CHANGELOG.md Unreleased section."""
    metadata = _metadata_from_options(
        release_title=release_title,
        release_date=release_date,
        internal_pr_url=internal_pr_url,
        public_pr_url=public_pr_url,
        commit_sha=commit_sha,
        tag=tag,
        related_issue=related_issue,
        operational_note=operational_note,
    )
    typer.echo(load_linear_markdown(changelog, metadata), nl=False)


@app.command("publish-document")
def publish_document(
    api_key: Annotated[
        str,
        typer.Option(
            "--api-key",
            envvar="LINEAR_API_KEY",
            help="Linear personal API key. Defaults to LINEAR_API_KEY.",
        ),
    ],
    document_id: Annotated[
        str | None,
        typer.Option("--document-id", help="Existing Linear document ID to update."),
    ] = None,
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Linear project ID for first-time document creation."),
    ] = None,
    document_title: Annotated[
        str,
        typer.Option("--document-title", help="Title for a newly created Linear document."),
    ] = DEFAULT_DOCUMENT_TITLE,
    input_file: Annotated[
        Path | None,
        typer.Option("--input", "-i", help="Pre-rendered Markdown file to publish."),
    ] = None,
    release_title: Annotated[
        str,
        typer.Option("--release-title", help="Title for a rendered ledger entry."),
    ] = "Release Ledger",
    release_date: Annotated[
        str,
        typer.Option("--release-date", help="Release date for a rendered ledger entry."),
    ] = "Unreleased",
    changelog: Annotated[
        Path,
        typer.Option("--changelog", help="CHANGELOG.md path to read when --input is omitted."),
    ] = release_notes.DEFAULT_CHANGELOG,
    internal_pr_url: Annotated[
        str | None,
        typer.Option("--internal-pr-url", help="Internal GitHub PR URL to include."),
    ] = None,
    public_pr_url: Annotated[
        str | None,
        typer.Option("--public-pr-url", help="Public export PR URL to include."),
    ] = None,
    commit_sha: Annotated[
        str | None,
        typer.Option("--commit-sha", help="Commit SHA to include."),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option("--tag", help="Git tag to include."),
    ] = None,
    related_issue: Annotated[
        list[str] | None,
        typer.Option("--related-issue", help="Related Linear issue key. Repeatable."),
    ] = None,
    operational_note: Annotated[
        list[str] | None,
        typer.Option("--operational-note", help="Human-readable operational note. Repeatable."),
    ] = None,
) -> None:
    """Create or update a Linear project document with the release ledger Markdown."""
    if bool(document_id) == bool(project_id):
        raise typer.BadParameter("Provide exactly one of --document-id or --project-id.")

    if input_file is not None:
        content = input_file.read_text(encoding="utf-8")
    else:
        metadata = _metadata_from_options(
            release_title=release_title,
            release_date=release_date,
            internal_pr_url=internal_pr_url,
            public_pr_url=public_pr_url,
            commit_sha=commit_sha,
            tag=tag,
            related_issue=related_issue,
            operational_note=operational_note,
        )
        content = load_linear_markdown(changelog, metadata)

    if document_id:
        payload = build_document_update_payload(document_id=document_id, content=content)
        operation = "documentUpdate"
    else:
        assert project_id is not None
        payload = build_document_create_payload(
            project_id=project_id,
            title=document_title,
            content=content,
        )
        operation = "documentCreate"

    data = linear_graphql(payload, api_key=api_key)
    result = data.get(operation)
    if not isinstance(result, dict):
        raise RuntimeError(f"Linear GraphQL response did not include {operation}.")

    document = result.get("document")
    typer.echo(json.dumps(document if document is not None else result, indent=2))


if __name__ == "__main__":
    app()
