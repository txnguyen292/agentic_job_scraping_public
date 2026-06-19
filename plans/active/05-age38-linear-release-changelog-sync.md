# Linear Release Changelog Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repo-to-Linear release/changelog sync so `CHANGELOG.md` remains the canonical source while Linear becomes the team-readable release ledger.

**Architecture:** Keep the existing GitHub PR release-note flow intact. Add a Linear ledger renderer and publisher that can create or update a Linear project document from `CHANGELOG.md`, then add a GitHub Actions workflow that updates the Linear document automatically after `main` merges when the required secret and document ID are configured.

**Tech Stack:** Python 3.13, Typer, standard-library `urllib.request`, GitHub Actions, Linear GraphQL API, Linear Documents, existing `scripts/release_notes.py`.

---

## Source Model

`CHANGELOG.md` remains canonical for release-note text. GitHub remains canonical for code history, commits, tags, PRs, and sanitized public export PR artifacts. Linear becomes the central human-readable place to answer "what shipped and why does it matter?"

This plan has one active lane and one deferred lane:

1. **Linear ledger document, v1:** deterministic, repo-owned Markdown generated from `CHANGELOG.md`, updated through Linear GraphQL when `LINEAR_API_KEY` and either a document ID or project ID are supplied. GitHub Actions publishes this document after `main` merges once `LINEAR_RELEASE_LEDGER_DOCUMENT_ID` is configured.
2. **Linear Releases, deferred:** official Linear Release records are not part of this implementation because Linear's Releases feature requires a Business or Enterprise plan.

Use the ledger document because it can preserve the human-authored changelog text exactly without requiring a Linear Business plan.

## References Checked

- Linear Releases plan requirement: `https://linear.app/docs/releases`
- Linear Project Documents: `https://linear.app/docs/project-documents`
- Linear GraphQL API: `https://linear.app/developers/graphql`
- AGE-38: `https://linear.app/agentic-job-scraping/issue/AGE-38/add-linear-release-and-changelog-sync-from-repo-release-notes`

## File Structure

- Create: `scripts/linear_release_ledger.py`
  - Owns Linear-specific rendering, GraphQL calls, and CLI commands.
- Create: `tests/test_linear_release_ledger.py`
  - Covers rendering, metadata validation, GraphQL request shape, create/update behavior, and no-raw-lineage behavior.
- Create: `docs/04-release-changelog-sync.md`
  - Stable human-facing runbook and source-of-truth explanation.
- Modify: `docs/index.md`
  - Adds the runbook to the reading order.
- Modify: `plans/index.md`
  - Links this active plan.
- Modify: `.github/workflows/internal-ci.yml`
  - Adds a render-only validation step for the Linear ledger.
- Create: `.github/workflows/linear-release-ledger.yml`
  - Adds a CI/CD workflow that publishes the Linear document ledger on `main` pushes when configured, and supports first-time document creation from `workflow_dispatch`.
- No change in v1: `scripts/release_notes.py`
  - Keep existing PR release-note behavior stable. The new script imports its changelog parser.

## Task 1: Add Stable Release/Changelog Runbook

**Files:**
- Create: `docs/04-release-changelog-sync.md`
- Modify: `docs/index.md`
- Modify: `plans/index.md`

- [ ] **Step 1: Create the runbook document**

Create `docs/04-release-changelog-sync.md` with this content:

````markdown
# Release and Changelog Sync

This repo keeps release information in three places with different responsibilities:

1. `CHANGELOG.md` is the canonical source for human-authored release-note text.
2. GitHub is canonical for code history, commits, tags, PRs, and public export PR artifacts.
3. Linear is the team-facing ledger for what shipped, which issues were involved, and where humans should look after the PR scroll has moved on.

## Current GitHub Flow

`scripts/release_notes.py` reads the `## Unreleased` section from `CHANGELOG.md`, validates that it is non-empty, and renders it into a managed PR block. `.github/workflows/internal-ci.yml` validates release notes and updates internal/public PR bodies.

## Linear Ledger Flow

`scripts/linear_release_ledger.py render` produces a Markdown release ledger entry from `CHANGELOG.md` and release metadata such as PR URLs, commit SHA, tag, and related AGE issue IDs.

`scripts/linear_release_ledger.py publish-document` publishes that Markdown into Linear when `LINEAR_API_KEY` is present. Use `--document-id` to update an existing ledger document. Use `--project-id` for the first creation, then save the returned document ID for future idempotent updates.

## Automated Linear Ledger Flow

`.github/workflows/linear-release-ledger.yml` publishes the Linear ledger document from GitHub Actions. On every push to `main`, it renders the current `CHANGELOG.md` Unreleased section and updates the configured Linear document.

Linear's official Releases feature requires a Business or Enterprise plan. This repo does not depend on that feature. The current implementation uses Linear Documents instead.

## Approval Rules

Do not add GitHub secrets, enable automatic release workflows, create public PRs, publish public releases, or push public branches without explicit approval in the current thread.

## Recommended Operating Model

Use the Linear ledger document for curated changelog notes generated from `CHANGELOG.md`. Keep it linked to the same GitHub PRs, commits, and AGE issues.
````

- [ ] **Step 2: Link the runbook from docs index**

Modify `docs/index.md` so the reading order becomes:

```markdown
## Reading Order

1. [Architecture](01-architecture.md)
2. [ADK Job Listing Scout](02-adk-job-listing-scout.md)
3. [Public Export Workflow](03-public-export.md)
4. [Release and Changelog Sync](04-release-changelog-sync.md)
```

- [ ] **Step 3: Link this plan from plans index**

Modify `plans/index.md` active plans to include:

```markdown
- [AGE-38 Linear release and changelog sync](active/05-age38-linear-release-changelog-sync.md)
```

- [ ] **Step 4: Verify documentation links**

Run:

```bash
python - <<'PY'
from pathlib import Path
for path in [
    Path("docs/04-release-changelog-sync.md"),
    Path("plans/active/05-age38-linear-release-changelog-sync.md"),
]:
    assert path.exists(), path
for text_path in [Path("docs/index.md"), Path("plans/index.md")]:
    text = text_path.read_text(encoding="utf-8")
    assert "release" in text.lower(), text_path
print("release docs linked")
PY
```

Expected: `release docs linked`

## Task 2: Write Renderer Tests First

**Files:**
- Create: `tests/test_linear_release_ledger.py`
- Create later: `scripts/linear_release_ledger.py`

- [ ] **Step 1: Add tests for the Linear ledger Markdown renderer**

Create `tests/test_linear_release_ledger.py` with this initial content:

```python
from __future__ import annotations

import json
from typing import Any

from scripts import linear_release_ledger


def test_render_markdown_includes_changelog_and_release_metadata() -> None:
    metadata = linear_release_ledger.ReleaseMetadata(
        title="Release 2026-06-05",
        release_date="2026-06-05",
        internal_pr_url="https://github.com/txnguyen292/agentic_job_scraping/pull/12",
        public_pr_url="https://github.com/txnguyen292/agentic_job_scraping_public/pull/8",
        commit_sha="abc1234",
        tag="v0.2.0",
        related_issues=("AGE-38", "AGE-10"),
        operational_notes=("Linear ledger dry run verified.",),
    )

    rendered = linear_release_ledger.render_linear_markdown(
        "### Added\n\n- Central Linear ledger.",
        metadata,
    )

    assert rendered.startswith("# Release 2026-06-05")
    assert "## Changelog" in rendered
    assert "### Added\n\n- Central Linear ledger." in rendered
    assert "https://github.com/txnguyen292/agentic_job_scraping/pull/12" in rendered
    assert "https://github.com/txnguyen292/agentic_job_scraping_public/pull/8" in rendered
    assert "`abc1234`" in rendered
    assert "`v0.2.0`" in rendered
    assert "AGE-38" in rendered
    assert "Linear ledger dry run verified." in rendered


def test_render_markdown_omits_empty_optional_sections() -> None:
    metadata = linear_release_ledger.ReleaseMetadata(
        title="Release 2026-06-05",
        release_date="2026-06-05",
    )

    rendered = linear_release_ledger.render_linear_markdown("### Fixed\n\n- CI.", metadata)

    assert "## Links" not in rendered
    assert "## Operational Notes" not in rendered
    assert "### Fixed\n\n- CI." in rendered


def test_operational_notes_reject_raw_lineage_json() -> None:
    metadata = linear_release_ledger.ReleaseMetadata(
        title="Release 2026-06-05",
        release_date="2026-06-05",
        operational_notes=('{"ts": "2026-06-05", "type": "raw"}',),
    )

    try:
        linear_release_ledger.render_linear_markdown("### Fixed\n\n- CI.", metadata)
    except ValueError as error:
        assert "raw lineage" in str(error)
    else:
        raise AssertionError("raw lineage JSON should be rejected")


def test_build_document_create_payload_uses_project_id() -> None:
    payload = linear_release_ledger.build_document_create_payload(
        project_id="project-123",
        title="Release Ledger",
        content="# Release Ledger",
    )

    assert "documentCreate" in payload["query"]
    assert payload["variables"]["input"] == {
        "projectId": "project-123",
        "title": "Release Ledger",
        "content": "# Release Ledger",
    }


def test_build_document_update_payload_uses_document_id() -> None:
    payload = linear_release_ledger.build_document_update_payload(
        document_id="doc-123",
        content="# Release Ledger",
    )

    assert "documentUpdate" in payload["query"]
    assert payload["variables"]["id"] == "doc-123"
    assert payload["variables"]["input"] == {"content": "# Release Ledger"}


def test_linear_graphql_sends_bearer_token(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"data": {"ok": True}}).encode()

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(linear_release_ledger.urllib.request, "urlopen", fake_urlopen)

    result = linear_release_ledger.linear_graphql(
        api_key="lin_api_test",
        payload={"query": "query { viewer { id } }", "variables": {}},
    )

    assert result == {"data": {"ok": True}}
    assert captured["url"] == "https://api.linear.app/graphql"
    assert captured["headers"]["Authorization"] == "lin_api_test"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"]["query"] == "query { viewer { id } }"
    assert captured["timeout"] == 30
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
uv run pytest tests/test_linear_release_ledger.py -q
```

Expected: failure because `scripts/linear_release_ledger.py` does not exist yet.

## Task 3: Implement Linear Ledger Renderer CLI

**Files:**
- Create: `scripts/linear_release_ledger.py`
- Test: `tests/test_linear_release_ledger.py`

- [ ] **Step 1: Create the script with renderer and payload helpers**

Create `scripts/linear_release_ledger.py`:

```python
"""Render and publish Linear release/changelog ledger entries."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import typer
from scripts import release_notes


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
DEFAULT_CHANGELOG = Path("CHANGELOG.md")

DOCUMENT_CREATE = """
mutation DocumentCreate($input: DocumentCreateInput!) {
  documentCreate(input: $input) {
    success
    document {
      id
      title
      url
    }
  }
}
""".strip()

DOCUMENT_UPDATE = """
mutation DocumentUpdate($id: String!, $input: DocumentUpdateInput!) {
  documentUpdate(id: $id, input: $input) {
    success
    document {
      id
      title
      url
    }
  }
}
""".strip()

app = typer.Typer(
    add_completion=False,
    help="Render or publish Linear release/changelog ledger entries from CHANGELOG.md.",
)


@dataclass(frozen=True)
class ReleaseMetadata:
    title: str
    release_date: str
    internal_pr_url: str | None = None
    public_pr_url: str | None = None
    commit_sha: str | None = None
    tag: str | None = None
    related_issues: tuple[str, ...] = field(default_factory=tuple)
    operational_notes: tuple[str, ...] = field(default_factory=tuple)


def _reject_raw_lineage(notes: tuple[str, ...]) -> None:
    for note in notes:
        stripped = note.strip()
        if stripped.startswith("{") and '"ts"' in stripped and '"type"' in stripped:
            raise ValueError("Operational notes must be curated summaries, not raw lineage JSON.")


def render_linear_markdown(changelog_notes: str, metadata: ReleaseMetadata) -> str:
    if not changelog_notes.strip():
        raise ValueError("Changelog notes must not be empty.")
    if not metadata.title.strip():
        raise ValueError("Release title must not be empty.")
    if not metadata.release_date.strip():
        raise ValueError("Release date must not be empty.")

    _reject_raw_lineage(metadata.operational_notes)

    lines = [
        f"# {metadata.title}",
        "",
        "## Summary",
        "",
        f"- Release date: `{metadata.release_date}`",
    ]

    if metadata.tag:
        lines.append(f"- Tag: `{metadata.tag}`")
    if metadata.commit_sha:
        lines.append(f"- Commit: `{metadata.commit_sha}`")
    if metadata.related_issues:
        issues = ", ".join(f"`{issue}`" for issue in metadata.related_issues)
        lines.append(f"- Related issues: {issues}")

    links: list[str] = []
    if metadata.internal_pr_url:
        links.append(f"- Internal PR: {metadata.internal_pr_url}")
    if metadata.public_pr_url:
        links.append(f"- Public export PR: {metadata.public_pr_url}")

    if links:
        lines.extend(["", "## Links", "", *links])

    lines.extend(["", "## Changelog", "", changelog_notes.strip()])

    if metadata.operational_notes:
        lines.extend(["", "## Operational Notes", ""])
        lines.extend(f"- {note.strip()}" for note in metadata.operational_notes if note.strip())

    lines.extend(
        [
            "",
            "## Source Of Truth",
            "",
            "- Release-note text comes from `CHANGELOG.md`.",
            "- GitHub remains canonical for code, commits, tags, PRs, and public export artifacts.",
            "- Linear is the team-facing ledger for release visibility.",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"


def build_document_create_payload(project_id: str, title: str, content: str) -> dict[str, Any]:
    return {
        "query": DOCUMENT_CREATE,
        "variables": {
            "input": {
                "projectId": project_id,
                "title": title,
                "content": content,
            }
        },
    }


def build_document_update_payload(document_id: str, content: str) -> dict[str, Any]:
    return {
        "query": DOCUMENT_UPDATE,
        "variables": {
            "id": document_id,
            "input": {
                "content": content,
            },
        },
    }


def linear_graphql(api_key: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        LINEAR_GRAPHQL_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        raise RuntimeError(f"Linear GraphQL request failed: {error.code} {body}") from error

    if result.get("errors"):
        raise RuntimeError(f"Linear GraphQL returned errors: {result['errors']}")

    return result


def load_linear_markdown(
    changelog: Path,
    metadata: ReleaseMetadata,
) -> str:
    notes = release_notes.extract_unreleased(changelog.read_text(encoding="utf-8"))
    return render_linear_markdown(notes, metadata)


def _metadata_from_options(
    release_title: str,
    release_date: str,
    internal_pr_url: str | None,
    public_pr_url: str | None,
    commit_sha: str | None,
    tag: str | None,
    related_issue: list[str],
    operational_note: list[str],
) -> ReleaseMetadata:
    return ReleaseMetadata(
        title=release_title,
        release_date=release_date,
        internal_pr_url=internal_pr_url,
        public_pr_url=public_pr_url,
        commit_sha=commit_sha,
        tag=tag,
        related_issues=tuple(related_issue),
        operational_notes=tuple(operational_note),
    )


@app.command()
def render(
    release_title: Annotated[str, typer.Option("--release-title")],
    release_date: Annotated[str, typer.Option("--release-date")],
    changelog: Annotated[Path, typer.Option("--changelog")] = DEFAULT_CHANGELOG,
    internal_pr_url: Annotated[str | None, typer.Option("--internal-pr-url")] = None,
    public_pr_url: Annotated[str | None, typer.Option("--public-pr-url")] = None,
    commit_sha: Annotated[str | None, typer.Option("--commit-sha")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    related_issue: Annotated[list[str], typer.Option("--related-issue")] = [],
    operational_note: Annotated[list[str], typer.Option("--operational-note")] = [],
) -> None:
    """Print a Linear-ready release/changelog ledger entry."""
    metadata = _metadata_from_options(
        release_title,
        release_date,
        internal_pr_url,
        public_pr_url,
        commit_sha,
        tag,
        related_issue,
        operational_note,
    )
    typer.echo(load_linear_markdown(changelog, metadata))


@app.command("publish-document")
def publish_document(
    release_title: Annotated[str, typer.Option("--release-title")],
    release_date: Annotated[str, typer.Option("--release-date")],
    changelog: Annotated[Path, typer.Option("--changelog")] = DEFAULT_CHANGELOG,
    document_title: Annotated[str, typer.Option("--document-title")] = "Release & Changelog Ledger",
    document_id: Annotated[str | None, typer.Option("--document-id")] = None,
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", envvar="LINEAR_API_KEY")] = None,
    internal_pr_url: Annotated[str | None, typer.Option("--internal-pr-url")] = None,
    public_pr_url: Annotated[str | None, typer.Option("--public-pr-url")] = None,
    commit_sha: Annotated[str | None, typer.Option("--commit-sha")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    related_issue: Annotated[list[str], typer.Option("--related-issue")] = [],
    operational_note: Annotated[list[str], typer.Option("--operational-note")] = [],
) -> None:
    """Create or update a Linear project document with rendered release notes."""
    if not api_key:
        raise typer.BadParameter("LINEAR_API_KEY or --api-key is required.")
    if not document_id and not project_id:
        raise typer.BadParameter("Pass --document-id to update or --project-id to create.")

    metadata = _metadata_from_options(
        release_title,
        release_date,
        internal_pr_url,
        public_pr_url,
        commit_sha,
        tag,
        related_issue,
        operational_note,
    )
    content = load_linear_markdown(changelog, metadata)

    if document_id:
        payload = build_document_update_payload(document_id=document_id, content=content)
    else:
        assert project_id is not None
        payload = build_document_create_payload(project_id=project_id, title=document_title, content=content)

    result = linear_graphql(api_key=api_key, payload=payload)
    document = result["data"]["documentUpdate" if document_id else "documentCreate"]["document"]
    typer.echo(json.dumps(document, indent=2))


if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Run renderer tests**

Run:

```bash
uv run pytest tests/test_linear_release_ledger.py -q
```

Expected: all tests in `tests/test_linear_release_ledger.py` pass.

- [ ] **Step 3: Run a local render smoke**

Run:

```bash
uv run python -m scripts.linear_release_ledger render \
  --release-title "Release 2026-06-05" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Ledger renderer dry run."
```

Expected: Markdown begins with `# Release 2026-06-05` and includes the current `CHANGELOG.md` `## Unreleased` content.

## Task 4: Add Publish Tests And Harden API Behavior

**Files:**
- Modify: `tests/test_linear_release_ledger.py`
- Modify: `scripts/linear_release_ledger.py`

- [ ] **Step 1: Add a test for create response parsing**

Append to `tests/test_linear_release_ledger.py`:

```python
def test_publish_create_returns_document_payload(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_graphql(api_key: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        calls.append({"api_key": api_key, "payload": payload, "timeout": timeout})
        return {
            "data": {
                "documentCreate": {
                    "success": True,
                    "document": {
                        "id": "doc-123",
                        "title": "Release & Changelog Ledger",
                        "url": "https://linear.app/team/docs/release-changelog-ledger",
                    },
                }
            }
        }

    monkeypatch.setattr(linear_release_ledger, "linear_graphql", fake_graphql)

    payload = linear_release_ledger.build_document_create_payload(
        project_id="project-123",
        title="Release & Changelog Ledger",
        content="# Release",
    )
    result = linear_release_ledger.linear_graphql("lin_api_test", payload)

    assert result["data"]["documentCreate"]["document"]["id"] == "doc-123"
    assert calls[0]["api_key"] == "lin_api_test"
```

- [ ] **Step 2: Add HTTP error coverage**

Append to `tests/test_linear_release_ledger.py`:

```python
def test_linear_graphql_raises_on_graphql_errors(monkeypatch: Any) -> None:
    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"errors": [{"message": "No access"}]}).encode()

    monkeypatch.setattr(
        linear_release_ledger.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(),
    )

    try:
        linear_release_ledger.linear_graphql("lin_api_test", {"query": "query { viewer { id } }"})
    except RuntimeError as error:
        assert "No access" in str(error)
    else:
        raise AssertionError("GraphQL errors should raise RuntimeError")
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
uv run pytest tests/test_linear_release_ledger.py -q
```

Expected: all tests pass.

## Task 5: Add CI Dry-Run Validation

**Files:**
- Modify: `.github/workflows/internal-ci.yml`

- [ ] **Step 1: Add render-only validation to the `test` job**

In `.github/workflows/internal-ci.yml`, after `Validate release notes`, add:

```yaml
      - name: Validate Linear release ledger rendering
        run: |
          uv run python -m scripts.linear_release_ledger render \
            --release-title "CI release ledger dry run" \
            --release-date "1970-01-01" \
            --related-issue AGE-38 \
            --operational-note "CI renderer validation only." \
            > "$RUNNER_TEMP/linear-release-ledger.md"
```

This validates rendering without publishing to Linear.

- [ ] **Step 2: Run local syntax and test checks**

Run:

```bash
uv run python -m scripts.linear_release_ledger render \
  --release-title "Local dry run" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Local renderer validation only." \
  > /tmp/linear-release-ledger.md
uv run pytest tests/test_release_notes.py tests/test_linear_release_ledger.py -q
```

Expected: render command exits 0; tests pass.

## Task 6: Add Linear Ledger CI/CD Workflow

**Files:**
- Create: `.github/workflows/linear-release-ledger.yml`
- Modify: `docs/04-release-changelog-sync.md`

- [ ] **Step 1: Create document publishing workflow**

Create `.github/workflows/linear-release-ledger.yml`:

```yaml
name: Linear Release Ledger

on:
  push:
    branches:
      - main
  workflow_dispatch:
    inputs:
      create_document:
        description: Create the first Linear ledger document when no document ID is set.
        required: false
        type: boolean
        default: false

jobs:
  publish-linear-ledger:
    name: Publish Linear release ledger document
    runs-on: ubuntu-latest
    env:
      LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
      LINEAR_RELEASE_LEDGER_DOCUMENT_ID: ${{ vars.LINEAR_RELEASE_LEDGER_DOCUMENT_ID }}
    steps:
      - name: Check publishing configuration
        run: |
          if [ -z "${LINEAR_API_KEY:-}" ] || [ -z "${LINEAR_RELEASE_LEDGER_DOCUMENT_ID:-}" ]; then
            echo "::notice ::Linear ledger publishing is not configured; skipping."
            exit 0
          fi
```

- [ ] **Step 2: Document the setup requirement**

Append this section to `docs/04-release-changelog-sync.md`:

````markdown
## CI/CD Setup

The `.github/workflows/linear-release-ledger.yml` workflow requires a Linear personal API key stored as the GitHub Actions secret `LINEAR_API_KEY`.

Setup sequence:

1. Add `LINEAR_API_KEY` as a GitHub Actions secret.
2. Run `Linear Release Ledger` manually with `create_document=true` to create the first document.
3. Save the returned document ID as the repository variable `LINEAR_RELEASE_LEDGER_DOCUMENT_ID`.
4. After that, every merge to `main` updates the document automatically.
````

- [ ] **Step 3: Validate YAML presence**

Run:

```bash
python - <<'PY'
from pathlib import Path
workflow = Path(".github/workflows/linear-release-ledger.yml").read_text(encoding="utf-8")
assert "push" in workflow
assert "workflow_dispatch" in workflow
assert "LINEAR_API_KEY" in workflow
assert "LINEAR_RELEASE_LEDGER_DOCUMENT_ID" in workflow
print("linear ledger workflow publishes documents when configured")
PY
```

Expected: `linear ledger workflow publishes documents when configured`

## Task 7: Add Publish Runbook And First-Publish Command Shape

**Files:**
- Modify: `docs/04-release-changelog-sync.md`

- [ ] **Step 1: Add document publishing commands**

Append this section to `docs/04-release-changelog-sync.md`:

````markdown
## Publishing The Linear Ledger Document

First creation requires a Linear project ID. For the current project, use the Linear project ID from AGE-38 or the Linear UI.

Dry run:

```bash
uv run python -m scripts.linear_release_ledger render \
  --release-title "Release 2026-06-05" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Curated operational summary only."
```

First publish:

```bash
LINEAR_API_KEY="$LINEAR_API_KEY" \
uv run python -m scripts.linear_release_ledger publish-document \
  --project-id "610a5be1-a336-4223-ac0f-c58a05606321" \
  --document-title "Release & Changelog Ledger" \
  --release-title "Release 2026-06-05" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Curated operational summary only."
```

The first publish prints the created document ID. Store that ID as `LINEAR_RELEASE_LEDGER_DOCUMENT_ID` for future updates.

Update existing ledger:

```bash
LINEAR_API_KEY="$LINEAR_API_KEY" \
uv run python -m scripts.linear_release_ledger publish-document \
  --document-id "$LINEAR_RELEASE_LEDGER_DOCUMENT_ID" \
  --release-title "Release 2026-06-05" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Curated operational summary only."
```
````

- [ ] **Step 2: Run Markdown fence check**

Run:

```bash
python - <<'PY'
from pathlib import Path
text = Path("docs/04-release-changelog-sync.md").read_text(encoding="utf-8")
marker = "`" * 3
assert text.count(marker) % 2 == 0
print("markdown fences balanced")
PY
```

Expected: `markdown fences balanced`

## Task 8: Full Verification And Context Update

**Files:**
- Modify: `.contexts/handoff.md`
- Modify: `.contexts/lineage/events.jsonl`
- Possibly modify: `.contexts/tasks/T-001.md` only if the active task is retargeted to AGE-38

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_release_notes.py tests/test_linear_release_ledger.py -q
```

Expected: all focused release-note tests pass.

- [ ] **Step 2: Run repo validation gates**

Run:

```bash
.contexts/bin/validate_context
uv run python scripts/release_notes.py check
uv run python -m scripts.linear_release_ledger render \
  --release-title "Validation dry run" \
  --release-date "2026-06-05" \
  --related-issue AGE-38 \
  --operational-note "Validation only." \
  > /tmp/linear-release-ledger.md
```

Expected: all commands exit 0.

- [ ] **Step 3: Inspect worktree dirtiness**

Run:

```bash
git status --short
```

Expected: changes are limited to AGE-38 plan/implementation files plus any pre-existing unrelated dirty files. Do not revert unrelated dirty files.

- [ ] **Step 4: Update project context**

Run:

```bash
.contexts/bin/update_handoff \
  --summary "Implemented AGE-38 Linear release/changelog ledger rendering and document CI/CD workflow." \
  --next-step "Review the Linear ledger CI/CD workflow, then configure LINEAR_API_KEY and LINEAR_RELEASE_LEDGER_DOCUMENT_ID when ready to publish from main merges." \
  --touched-file "scripts/linear_release_ledger.py" \
  --touched-file "tests/test_linear_release_ledger.py" \
  --touched-file "docs/04-release-changelog-sync.md" \
  --verification "uv run pytest tests/test_release_notes.py tests/test_linear_release_ledger.py -q; .contexts/bin/validate_context; release ledger render dry run"
```

Run:

```bash
.contexts/bin/append_lineage implementation \
  "Implemented AGE-38 Linear release/changelog ledger renderer and gated workflow plan." \
  --task-id T-001 \
  --file "scripts/linear_release_ledger.py" \
  --file "tests/test_linear_release_ledger.py" \
  --file "docs/04-release-changelog-sync.md" \
  --verification "Focused tests and context validation passed." \
  --link AGE-38
```

- [ ] **Step 5: Prepare Linear update payload**

Run:

```bash
.contexts/bin/linear_update_payload T-001
```

Expected: JSON body suitable for posting back to the linked Linear issue. Post it only after confirming the target issue and current user approval context.

## Open Decisions

1. **Automatic document publishing:** Keep `.github/workflows/linear-release-ledger.yml` non-breaking when secrets or variables are missing. Once `LINEAR_API_KEY` and `LINEAR_RELEASE_LEDGER_DOCUMENT_ID` are configured, pushes to `main` publish automatically.
2. **First Linear document destination:** Use the existing `Agentic Job Scraper Stabilization` project document surface first, because the current Codex/Linear tool schema supports project/issue documents and the GraphQL document API accepts `projectId`.
3. **Team home pinning:** Pin the created document manually from the Linear team home page after first publish. The current connector can create project or issue documents, but not a true team-level document from CI.
4. **Raw `.contexts` lineage:** Never publish raw lineage JSON. Only curated operational notes should appear in Linear.

## Self-Review

- Spec coverage: The plan covers rendering from `CHANGELOG.md`, Linear document publish/update, main-merge CI/CD publishing, CI dry-run validation, tests, docs, and context updates.
- Placeholder scan: No step depends on "TBD" or unspecified implementation. Runtime secrets are referenced by environment variable names, and the current Linear project ID is concrete.
- Type consistency: The plan uses `ReleaseMetadata`, `render_linear_markdown`, `build_document_create_payload`, `build_document_update_payload`, and `linear_graphql` consistently across tests and implementation.
