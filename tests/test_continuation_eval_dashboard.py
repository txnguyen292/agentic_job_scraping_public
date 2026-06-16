from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "continuation_eval_adk_traces.json"


def run_python(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write_score_report(tmp_path: Path, fixture: str = "bad_premature_finalization_run") -> Path:
    result = run_python(
        "scripts/score_continuation_eval.py",
        "--fixture-file",
        str(FIXTURE_PATH),
        "--fixture",
        fixture,
    )
    report_path = tmp_path / "score-report.json"
    report_path.write_text(result.stdout, encoding="utf-8")
    json.loads(result.stdout)
    return report_path


def run_dashboard(report_path: Path, tmp_path: Path) -> tuple[dict[str, object], str]:
    output_path = tmp_path / "dashboard.html"
    data_path = tmp_path / "dashboard-data.json"
    run_python(
        "scripts/continuation_eval_dashboard.py",
        "--input",
        str(report_path),
        "--output",
        str(output_path),
        "--dump-data",
        str(data_path),
        "--project-root",
        str(tmp_path),
    )
    return json.loads(data_path.read_text(encoding="utf-8")), output_path.read_text(encoding="utf-8")


def test_dashboard_cli_dumps_score_provenance_and_runtime_layer(tmp_path: Path) -> None:
    report_path = write_score_report(tmp_path)

    data, html = run_dashboard(report_path, tmp_path)

    assert data["report_count"] == 1
    assert data["runtime_layer"] == {
        "title": "ADK Runtime Observability",
        "first_class": True,
        "generator": "scripts/adk_token_dashboard.py",
        "template": "scripts/adk_token_dashboard.template.html",
        "entrypoint": "job-scraper-adk-dashboard",
        "chart_surfaces": [
            "runtime_distribution",
            "cost_by_model",
            "daily_token_timeline",
            "model_buckets",
            "runtime_quantiles",
            "span_session_detail",
        ],
        "correlation_keys": ["case", "trace_id", "span_id", "session_id", "source_file"],
        "note": "Runtime/token charts stay first-class and are correlated to scoring by run/session/provenance metadata.",
    }

    report = data["reports"][0]
    assert report["case"] == "bad_premature_finalization_run"
    assert report["source_file"] == "score-report.json"
    assert report["identity"]["case"]["source_path"] == "$.case"
    assert report["identity"]["trace_id"]["value"] is None
    assert report["identity"]["trace_id"]["source_path"] == "$.metadata.trace_id"

    assert report["score_cards"][0] == {
        "label": "Trajectory score",
        "value": 0.2726190476190476,
        "detail": "Weighted aggregate of milestone_completion, ordering_score, and efficiency_score.",
        "source_path": "$.score_breakdown.trajectory_score.score",
    }
    assert report["trajectory"]["formula"] == "0.45 * milestone_completion + 0.30 * ordering_score + 0.25 * efficiency_score"
    assert report["trajectory"]["terms"][0] == {
        "component": "milestone_completion",
        "weight": 0.45,
        "score": 2 / 14,
        "contribution": 0.45 * (2 / 14),
        "source_path": "$.score_breakdown.trajectory_score.terms[0]",
    }
    assert report["milestone_lane"]["completed"][0] == {
        "name": "project_context_loaded",
        "status": "completed",
        "event_index": 1,
        "source_path": "$.score_breakdown.milestone_completion.completed[0]",
    }
    assert report["milestone_lane"]["missing"][0]["name"] == "skill_and_contract_loaded"
    assert report["semantic_ledger"]["points_sum"] == -1.5
    assert report["semantic_ledger"]["rows"]["harmful"] == {
        "count": 4,
        "point_value": -1.0,
        "total": -4.0,
        "source_path": "$.score_breakdown.semantic_directness.label_counts.harmful",
    }
    assert report["events"][1]["source_path"] == "$.normalized_events[1]"
    assert report["events"][1]["source_map"]["label"] == "$.normalized_events[1].label"
    assert report["events"][1]["source_map"]["evidence"] == "$.normalized_events[1].evidence"
    assert report["provenance_coverage"]["score_paths"]["present"] == report["provenance_coverage"]["score_paths"]["total"]
    assert report["provenance_coverage"]["trace_ids"]["present"] == 0
    assert "Runtime chart handoff" in html
    assert "collapsible-table" in html
    assert "Semantic directness ledger" in html
    assert "max-height: min(420px, 52vh)" in html


def test_dashboard_html_escapes_embedded_data(tmp_path: Path) -> None:
    report_path = write_score_report(tmp_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["normalized_events"][0]["evidence"] = "</script><span>not markup</span>"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    _, html = run_dashboard(report_path, tmp_path)

    assert "window.__CONTINUATION_EVAL_DASHBOARD_DATA__" in html
    assert "</script><span>not markup</span>" not in html
    assert "\\u003c/script>" in html
    assert "Evidence-first scoring overview" in html
    assert "Correlation Workbench" in html
    assert "Runtime chart handoff" in html
