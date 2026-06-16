from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRACE_FIXTURE = ROOT / "tests" / "fixtures" / "continuation_eval_adk_traces.json"


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
        str(TRACE_FIXTURE),
        "--fixture",
        fixture,
    )
    report_path = tmp_path / "bad_premature_finalization_run.score.json"
    report_path.write_text(result.stdout, encoding="utf-8")
    json.loads(result.stdout)
    return report_path


def write_eval_result(tmp_path: Path) -> Path:
    result_path = tmp_path / "bad_premature_finalization_run.evalset_result.json"
    result_path.write_text(
        json.dumps(
            {
                "eval_set_result_id": "age33_bad_premature_finalization_run",
                "eval_set_id": "job_scraper_core",
                "creation_timestamp": 1778808199.500643,
                "eval_case_results": [
                    {
                        "eval_id": "bad_premature_finalization_run",
                        "final_eval_status": 2,
                        "overall_eval_metric_results": [
                            {
                                "metric_name": "rubric_based_tool_use_quality_v1",
                                "threshold": 0.85,
                                "score": 0.5,
                                "eval_status": 2,
                                "details": {
                                    "rubric_scores": [
                                        {
                                            "rubric_id": "records_immediate_goal_before_producer",
                                            "score": 0.0,
                                            "rationale": (
                                                "Trace anchor: invocation_events[0] is missing "
                                                "the required immediate goal."
                                            ),
                                        }
                                    ]
                                },
                                "criterion": {
                                    "threshold": 0.85,
                                    "rubrics": [
                                        {
                                            "rubric_id": "records_immediate_goal_before_producer",
                                            "rubric_content": {
                                                "text_property": "Agent records immediate goal before producer work."
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                        "eval_metric_result_per_invocation": [
                            {
                                "actual_invocation": {
                                    "invocation_id": "invocation-1",
                                    "user_content": {
                                        "role": "user",
                                        "parts": [{"text": "Continue the run."}],
                                    },
                                    "intermediate_data": {
                                        "invocation_events": [
                                            {
                                                "author": "job_listing_scout",
                                                "content": {
                                                    "parts": [
                                                        {
                                                            "function_call": {
                                                                "id": "call-1",
                                                                "name": "update_extraction_context",
                                                                "args": {"immediate_goal": "Generic goal."},
                                                            }
                                                        }
                                                    ]
                                                },
                                            }
                                        ]
                                    },
                                    "final_response": {
                                        "role": "model",
                                        "parts": [{"text": "Done too early."}],
                                    },
                                },
                                "eval_metric_results": [],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return result_path


def write_session_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "src" / "job_scraper" / ".adk" / "session.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            create table sessions (
                app_name text not null,
                user_id text not null,
                id text not null,
                state text not null,
                create_time real not null,
                update_time real not null,
                primary key (app_name, user_id, id)
            );
            create table events (
                id text not null,
                app_name text not null,
                user_id text not null,
                session_id text not null,
                invocation_id text not null,
                timestamp real not null,
                event_data text not null,
                primary key (app_name, user_id, session_id, id)
            );
            """
        )
        conn.execute(
            "insert into sessions values (?, ?, ?, ?, ?, ?)",
            ("job_scraper", "user", "bad_premature_finalization_run", "{}", 100.0, 101.0),
        )
        conn.execute(
            "insert into events values (?, ?, ?, ?, ?, ?, ?)",
            (
                "event-1",
                "job_scraper",
                "user",
                "bad_premature_finalization_run",
                "invocation-1",
                100.0,
                json.dumps(
                    {
                        "author": "job_listing_scout",
                        "model_version": "gpt-test",
                        "usage_metadata": {
                            "prompt_token_count": 100,
                            "cached_content_token_count": 40,
                            "candidates_token_count": 9,
                            "thoughts_token_count": 3,
                            "total_token_count": 109,
                        },
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_adk_observability_custom_cli_dumps_three_layers_and_run_index(tmp_path: Path) -> None:
    eval_path = write_eval_result(tmp_path)
    score_path = write_score_report(tmp_path)
    db_path = write_session_db(tmp_path)
    html_path = tmp_path / "adk-observability-custom.html"
    data_path = tmp_path / "adk-observability-custom.json"

    run_python(
        "scripts/adk_observability_custom_dashboard.py",
        "--eval-input",
        str(eval_path),
        "--score-input",
        str(score_path),
        "--runtime-db",
        str(db_path),
        "--project-root",
        str(tmp_path),
        "--output",
        str(html_path),
        "--dump-data",
        str(data_path),
    )

    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["platform"]["title"] == "ADK Observability Custom Enhancements"
    assert data["identity_keys"] == [
        "run_id",
        "case",
        "eval_id",
        "session_id",
        "trace_id",
        "span_id",
        "source_file",
    ]
    assert data["layers"]["eval"]["source_dashboard"] == "scripts/adk_eval_dashboard.py"
    assert isinstance(data["layers"]["eval"]["adk_version"], str)
    assert data["layers"]["eval"]["run_count"] == 1
    assert data["layers"]["runtime"]["source_dashboard"] == "scripts/adk_token_dashboard.py"
    assert data["layers"]["runtime"]["session_count"] == 1
    assert data["layers"]["runtime"]["chart_surfaces"] == [
        "runtime_distribution",
        "cost_by_model",
        "daily_token_timeline",
        "model_buckets",
        "runtime_quantiles",
        "span_session_detail",
    ]
    assert data["layers"]["scoring"]["source_dashboard"] == "scripts/continuation_eval_dashboard.py"
    assert data["layers"]["scoring"]["report_count"] == 1
    assert "ADK Eval Dashboard" in data["source_dashboard_html"]["eval"]
    assert "adk-token-metrics" in data["source_dashboard_html"]["runtime"]
    assert "Evidence-first scoring overview" in data["source_dashboard_html"]["scoring"]

    run = data["run_index"][0]
    assert run["run_id"] == "bad_premature_finalization_run"
    assert run["coverage"] == {"eval": True, "runtime": True, "scoring": True}
    assert run["eval"]["status"] == "fail"
    assert run["runtime"]["total_tokens"] == 109
    assert run["scoring"]["trajectory_score"] == 0.2726190476190476
    assert run["source_links"]["eval"]["source_dashboard"] == "scripts/adk_eval_dashboard.py"
    assert run["source_links"]["runtime"]["source_dashboard"] == "scripts/adk_token_dashboard.py"
    assert run["source_links"]["scoring"]["source_dashboard"] == "scripts/continuation_eval_dashboard.py"

    assert data["attention_drivers"][0]["run_id"] == "bad_premature_finalization_run"
    assert data["attention_drivers"][0]["layer"] == "scoring"
    assert data["attention_drivers"][0]["source_path"] == "$.score_breakdown.trajectory_score.score"

    html = html_path.read_text(encoding="utf-8")
    assert "Unified observability overview" in html
    assert "Evidence matrix" in html
    assert "collapsible-table" in html
    assert "max-height: min(420px, 52vh)" in html
    assert "data-source-dashboard" in html
    assert "source_dashboard_html" in html
    assert "Full ADK Eval Dashboard" in html
    assert "Full ADK Runtime Dashboard" in html
    assert "Full Continuation Eval Dashboard" in html
    assert "Run investigation" in html
    assert "ADK eval trace" in html
    assert "Trace anchor: invocation_events[0] is missing the required immediate goal." in html
    assert "Trajectory formula" in html
    assert "Milestones" in html
    assert "Ordering checks" in html
    assert "Semantic labels" in html
    assert "Normalized events" in html
    assert "Semantic directness ledger" in data["source_dashboard_html"]["scoring"]
    assert "collapsible-table" in data["source_dashboard_html"]["scoring"]
    assert "max-height: min(420px, 52vh)" in data["source_dashboard_html"]["scoring"]
    assert "collapsible-table" in data["source_dashboard_html"]["eval"]
    assert "max-height: min(460px, 56vh)" in data["source_dashboard_html"]["eval"]
    assert "Eval evidence" in html
    assert "Runtime Distribution" in html
    assert "Cost by Model" in html
    assert "Daily Token Timeline" in html
    assert "Runtime Quantiles" in html
    assert "Scoring evidence" in html
    assert "Provenance coverage" in html
    assert "Source dashboards" in html
    assert "Streamlit job dashboard" in html
    assert "scripts/adk_eval_dashboard.py" in html
    assert "scripts/adk_token_dashboard.py" in html
    assert "scripts/continuation_eval_dashboard.py" in html
    assert "__ADK_OBSERVABILITY_CUSTOM_DASHBOARD_DATA_JSON__" not in html
