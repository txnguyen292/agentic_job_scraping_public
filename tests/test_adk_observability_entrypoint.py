from __future__ import annotations

import sys
from pathlib import Path

from job_scraper import adk_observability


def test_dashboard_script_path_can_resolve_from_project_root(tmp_path: Path) -> None:
    script_path = tmp_path / "scripts" / "adk_token_dashboard.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("print('dashboard')\n", encoding="utf-8")

    assert adk_observability.dashboard_script_path(tmp_path) == script_path


def test_run_dashboard_script_passes_args_and_restores_argv(monkeypatch, tmp_path: Path) -> None:
    script_path = tmp_path / "scripts" / "adk_token_dashboard.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("print('dashboard')\n", encoding="utf-8")
    captured: dict[str, object] = {}
    original_argv = ["job-scraper-adk-dashboard", "--serve"]
    monkeypatch.setattr(sys, "argv", original_argv[:])

    def fake_run_path(path: str, *, run_name: str) -> None:
        captured["path"] = path
        captured["run_name"] = run_name
        captured["argv"] = sys.argv[:]

    monkeypatch.setattr(adk_observability.runpy, "run_path", fake_run_path)

    adk_observability.run_dashboard_script(
        ["--output", "reports/adk-token-dashboard.html"],
        project_root=tmp_path,
    )

    assert captured == {
        "path": str(script_path),
        "run_name": "__main__",
        "argv": [
            str(script_path),
            "--output",
            "reports/adk-token-dashboard.html",
        ],
    }
    assert sys.argv == original_argv
