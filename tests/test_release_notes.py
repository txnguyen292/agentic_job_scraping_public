from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import release_notes


def test_extract_unreleased_stops_at_next_release() -> None:
    changelog = """# Changelog

## Unreleased

### Added

- New thing.

## 0.1.0

- Old thing.
"""

    assert release_notes.extract_unreleased(changelog) == "### Added\n\n- New thing."


def test_load_fragment_notes_reads_unreleased_fragments_in_name_order(tmp_path: Path) -> None:
    fragments = tmp_path / "release-notes" / "unreleased"
    fragments.mkdir(parents=True)
    (fragments / "0002-fix.md").write_text("### Fixed\n\n- Second.", encoding="utf-8")
    (fragments / "0001-add.md").write_text("### Added\n\n- First.", encoding="utf-8")

    assert release_notes.load_fragment_notes(fragments) == (
        "### Added\n\n- First.\n\n### Fixed\n\n- Second."
    )


def test_load_fragment_notes_uses_only_fragments_changed_since_base(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    fragments = Path("release-notes/unreleased")
    fragments.mkdir(parents=True)
    changed = fragments / "0004-this-pr.md"
    unchanged = fragments / "0001-old-pr.md"
    changed.write_text("### Changed\n\n- This PR only.", encoding="utf-8")
    unchanged.write_text("### Added\n\n- Old unrelated PR.", encoding="utf-8")

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        assert args[0] == [
            "git",
            "diff",
            "--name-only",
            "origin/main...HEAD",
            "--",
            "release-notes/unreleased",
        ]
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return subprocess.CompletedProcess(args[0], 0, stdout=f"{changed}\nREADME.md\n")

    monkeypatch.setattr(release_notes.subprocess, "run", fake_run)

    assert release_notes.load_fragment_notes(fragments, base_ref="origin/main") == (
        "### Changed\n\n- This PR only."
    )


def test_load_fragment_notes_requires_a_release_note_decision(tmp_path: Path) -> None:
    fragments = tmp_path / "release-notes" / "unreleased"
    fragments.mkdir(parents=True)

    try:
        release_notes.load_fragment_notes(fragments)
    except ValueError as exc:
        assert "No release note fragments found" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected empty release-note fragments to fail")


def test_render_details_wraps_notes_in_managed_collapsible_block() -> None:
    rendered = release_notes.render_details("### Fixed\n\n- CI.")

    assert rendered.startswith("<!-- release-notes:start -->")
    assert "<details>" in rendered
    assert "<summary>Release notes</summary>" in rendered
    assert "### Fixed\n\n- CI." in rendered
    assert rendered.endswith("<!-- release-notes:end -->")


def test_replace_managed_block_updates_existing_block() -> None:
    body = """## Summary

Old summary.

## Release Notes

<!-- release-notes:start -->
old
<!-- release-notes:end -->

## Verification
"""

    rendered = release_notes.render_details("### Added\n\n- Notes.")
    updated = release_notes.replace_managed_block(body, rendered)

    assert "old" not in updated
    assert "### Added\n\n- Notes." in updated
    assert updated.count("<!-- release-notes:start -->") == 1


def test_replace_managed_block_appends_section_when_missing() -> None:
    rendered = release_notes.render_details("### Added\n\n- Notes.")
    updated = release_notes.replace_managed_block("## Summary\n\nBody.", rendered)

    assert "## Release Notes" in updated
    assert "### Added\n\n- Notes." in updated


def test_replace_managed_block_replaces_unmanaged_release_notes_section() -> None:
    body = """## Summary

Body.

## Release Notes

Manual stale notes.

## Verification

- Done.
"""

    rendered = release_notes.render_details("### Added\n\n- Notes.")
    updated = release_notes.replace_managed_block(body, rendered)

    assert "Manual stale notes." not in updated
    assert "### Added\n\n- Notes." in updated
    assert "## Verification\n\n- Done." in updated
