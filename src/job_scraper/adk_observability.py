"""Console entry point for the repo-local ADK Observability dashboard."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from typing import Sequence


DASHBOARD_SCRIPT = Path("scripts/adk_token_dashboard.py")


def dashboard_script_path(project_root: Path | None = None) -> Path:
    """Resolve the dashboard generator script from an editable checkout."""
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root / DASHBOARD_SCRIPT)
    candidates.extend(
        [
            Path.cwd() / DASHBOARD_SCRIPT,
            Path(__file__).resolve().parents[2] / DASHBOARD_SCRIPT,
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"Could not find {DASHBOARD_SCRIPT}; searched: {searched}")


def run_dashboard_script(
    args: Sequence[str] | None = None,
    *,
    project_root: Path | None = None,
) -> None:
    """Run the dashboard script as if it were called directly."""
    script_path = dashboard_script_path(project_root)
    original_argv = sys.argv[:]
    sys.argv = [str(script_path), *(args if args is not None else original_argv[1:])]
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = original_argv


def main() -> None:
    run_dashboard_script()
