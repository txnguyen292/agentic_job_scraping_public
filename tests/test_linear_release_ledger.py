from __future__ import annotations

import json
from typing import Any

import pytest

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

    with pytest.raises(ValueError, match="raw lineage"):
        linear_release_ledger.render_linear_markdown("### Fixed\n\n- CI.", metadata)


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


def test_linear_graphql_sends_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"data": {"ok": True}}).encode("utf-8")

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(linear_release_ledger.urllib.request, "urlopen", fake_urlopen)

    result = linear_release_ledger.linear_graphql(
        {"query": "query { ok }", "variables": {}},
        api_key="token-123",
        timeout=12,
    )

    assert result == {"ok": True}
    assert captured["headers"]["authorization"] == "token-123"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"] == {"query": "query { ok }", "variables": {}}
    assert captured["timeout"] == 12


def test_linear_graphql_raises_on_graphql_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"errors": [{"message": "No document"}]}).encode("utf-8")

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(linear_release_ledger.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="No document"):
        linear_release_ledger.linear_graphql(
            {"query": "query { ok }", "variables": {}},
            api_key="token-123",
        )
