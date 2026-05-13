from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_notes.py"
SPEC = importlib.util.spec_from_file_location("release_notes", MODULE_PATH)
assert SPEC is not None
release_notes = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release_notes)


def test_extract_unreleased_stops_at_next_release() -> None:
    changelog = """# Changelog

## Unreleased

### Added

- New thing.

## 0.1.0

- Old thing.
"""

    assert release_notes.extract_unreleased(changelog) == "### Added\n\n- New thing."


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
