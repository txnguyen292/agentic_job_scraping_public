from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from google.adk.sessions.sqlite_session_service import SqliteSessionService
from typer.testing import CliRunner


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export_continuation_eval_adk_web_sessions.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("export_continuation_eval_adk_web_sessions", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
export_adk_web_sessions = importlib.util.module_from_spec(SCRIPT_SPEC)
assert SCRIPT_SPEC.loader is not None
sys.modules[SCRIPT_SPEC.name] = export_adk_web_sessions
SCRIPT_SPEC.loader.exec_module(export_adk_web_sessions)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "continuation_eval_adk_traces.json"


def run_async(coro):
    return asyncio.run(coro)


def test_exports_fixture_as_adk_web_readable_session(tmp_path: Path) -> None:
    output_db = tmp_path / "age26-adk-web.sqlite"

    payload = run_async(
        export_adk_web_sessions.export_fixtures_to_adk_web_sessions(
            fixture_file=FIXTURE_PATH,
            output_db=output_db,
            fixture_names=["optimal_gold_run"],
            app_name="job_scraper",
            user_id="user",
        )
    )

    assert payload["session_count"] == 1
    assert output_db.exists()
    assert payload["sessions"][0]["session_id"] == "age26-optimal-gold-run"

    service = SqliteSessionService(str(output_db))
    session = run_async(
        service.get_session(
            app_name="job_scraper",
            user_id="user",
            session_id="age26-optimal-gold-run",
        )
    )

    assert session is not None
    assert session.state["fixture_case"] == "optimal_gold_run"
    assert session.events[0].author == "user"
    assert session.events[0].content.parts[0].text == "Extract the ITviec Hanoi AI Engineer fixture and persist verified jobs."
    assert session.events[1].get_function_calls()[0].name == "load_project_context"
    assert session.events[2].get_function_responses()[0].name == "load_project_context"
    assert session.events[-1].author == "job_listing_scout"
    assert session.events[-1].content.parts[0].text == "Verified 20 persisted ITviec jobs from finalized sandbox output."


def test_export_cli_prints_adk_web_command_and_subset_summary(tmp_path: Path) -> None:
    output_db = tmp_path / "subset.sqlite"

    result = CliRunner().invoke(
        export_adk_web_sessions.app,
        [
            "--fixture-file",
            str(FIXTURE_PATH),
            "--output-db",
            str(output_db),
            "--fixture",
            "bad_premature_finalization_run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["output_db"] == str(output_db)
    assert payload["session_count"] == 1
    assert payload["sessions"][0]["case"] == "bad_premature_finalization_run"
    assert payload["adk_web_command"][:3] == [".venv/bin/adk", "web", "--session_service_uri"]
    assert payload["adk_web_command"][-1] == "src"


def test_export_cli_rejects_unknown_fixture(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        export_adk_web_sessions.app,
        [
            "--fixture-file",
            str(FIXTURE_PATH),
            "--output-db",
            str(tmp_path / "missing.sqlite"),
            "--fixture",
            "missing_run",
        ],
    )

    assert result.exit_code == 1
    assert "Unknown fixture" in result.stderr
