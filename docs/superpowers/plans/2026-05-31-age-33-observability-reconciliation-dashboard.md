# AGE-33 Custom ADK Observability Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build custom ADK observability dashboard scripts that reconcile ADK eval evidence, ADK trace/event evidence, AGE-33 scoring evidence, and ADK runtime/token charts in one correlated investigation surface.

**Architecture:** Add a new external custom ADK observability CLI in `scripts/` that composes the existing source-specific dashboard data builders without replacing them. The custom dashboard owns shared run identity, cross-layer evidence matrixes, and first-class platform navigation; ADK eval, token/runtime, and scoring dashboards remain independently useful source views.

**Tech Stack:** Python 3, Typer, Rich, static HTML/CSS/JS, existing script-local dashboard builders, subprocess-based pytest tests with `PYTHONPATH=src`.

---

## What Reconciliation Means

The current implemented scoring dashboard is only one source-specific view. The custom ADK observability dashboard should reconcile four first-class evidence layers:

- **ADK Eval:** eval runs, metric status, rubric scores, failed rubric flags, judge citations, final response excerpts when present.
- **ADK Traces / Events:** invocation timeline, function calls, function responses, metric events, normalized event references, optional trace/span/session IDs.
- **AGE-33 Scoring:** trajectory and response-quality scores, score formulas, weighted terms, milestone completion, ordering checks, semantic directness, operation efficiency, normalized events, source JSON paths.
- **Runtime Tokens:** runtime distribution, cost by model, daily token timeline, model buckets, runtime quantiles, session/span detail, live process context when available.

The dashboard must answer these questions without giving repair advice:

- Which run is this?
- Which sources exist for this run?
- Which layer is failing or risky?
- Which raw evidence produced that score, eval failure, or runtime spike?
- Which source dashboard or JSON path should I inspect next?
- Which identifiers are missing, and does that limit correlation?

## Non-Goals

- Do not merge all source dashboards into one large page.
- Do not remove or replace `scripts/adk_eval_dashboard.py`, `scripts/adk_token_dashboard.py`, or `scripts/continuation_eval_dashboard.py`.
- Do not count the Streamlit job dashboard as part of this observability platform.
- Do not add LLM-authored summaries, recommendations, or repair hints.
- Do not render prompt/response body text unless it already exists in the input data and the capture mode allows it.
- Do not introduce new `importlib.spec_from_file_location()` usage.

## File Structure

- Create: `scripts/adk_observability_custom_dashboard.py`
  - External Typer CLI.
  - Builds a unified JSON contract from eval inputs, scoring report inputs, and ADK session DB inputs.
  - Uses normal script-local imports for existing dashboard builders.
  - Does not own source-specific scoring, eval parsing, or token parsing logic.

- Create: `scripts/adk_observability_custom_dashboard.template.html`
  - Static ADK-themed dashboard shell.
  - Renders Overview, Eval, Runtime, Scoring, Evidence, and Sources views.
  - Includes real runtime/token charts from the runtime data layer, not just a handoff note.

- Create: `tests/test_adk_observability_custom_dashboard.py`
  - Subprocess tests for the new CLI.
  - Creates fixture eval JSON, fixture ADK session DB, and fixture score report.
  - Asserts the dumped unified JSON contract and generated HTML.
  - Must not import scripts by file path.

- Modify: `docs/superpowers/plans/2026-05-30-age-33-continuation-observability-dashboard.md`
  - Clarify that the existing Task 4 only implemented runtime handoff inside the scoring dashboard.
  - Point to this plan for the actual custom ADK observability dashboard.

- Modify: `.contexts/tasks/T-002.md`
  - Update next step after this plan is written.

- Modify: `.contexts/handoff.md`
  - Record that the scoring dashboard is implemented, but custom ADK observability enhancements are planned separately.

- Modify: `.contexts/lineage/events.jsonl`
  - Append one planning event.

---

## Task 1: Write The Custom ADK Obs CLI Contract Test

**Files:**
- Create: `tests/test_adk_observability_custom_dashboard.py`

- [x] **Step 1: Add subprocess helpers and fixture writers**

Use subprocess for scripts. Duplicate tiny fixture builders instead of importing existing script test modules.

```python
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
                                            "rationale": "Trace anchor: invocation_events[0] is missing the required immediate goal.",
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
                                    "user_content": {"role": "user", "parts": [{"text": "Continue the run."}]},
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
                                    "final_response": {"role": "model", "parts": [{"text": "Done too early."}]},
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
```

- [x] **Step 2: Add the failing CLI data contract test**

```python
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
    assert data["identity_keys"] == ["run_id", "case", "eval_id", "session_id", "trace_id", "span_id", "source_file"]
    assert data["layers"]["eval"]["source_dashboard"] == "scripts/adk_eval_dashboard.py"
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
    assert "Runtime Distribution" in html
    assert "Evidence matrix" in html
```

- [x] **Step 3: Run the test and confirm the expected failure**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_adk_observability_custom_dashboard.py -q
```

Expected:

```text
FAILED tests/test_adk_observability_custom_dashboard.py::test_adk_observability_custom_cli_dumps_three_layers_and_run_index
```

The failure should be because `scripts/adk_observability_custom_dashboard.py` does not exist.

---

## Task 2: Implement The Custom ADK Obs Data Builder

**Files:**
- Create: `scripts/adk_observability_custom_dashboard.py`
- Test: `tests/test_adk_observability_custom_dashboard.py`

- [x] **Step 1: Create the script-local imports and constants**

```python
"""Generate custom ADK observability dashboard enhancements."""

from __future__ import annotations

import datetime as dt
import functools
import http.server
import json
import socketserver
import sys
import urllib.parse
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from adk_token_dashboard import build_dashboard_data as build_runtime_dashboard_data
from continuation_eval_dashboard import build_dashboard_data as build_scoring_dashboard_data
from utils import build_dashboard_data as build_eval_dashboard_data


DEFAULT_OUTPUT = Path("reports/adk-observability-custom-dashboard.html")
DEFAULT_DUMP_DATA = None
DASHBOARD_DATA_PLACEHOLDER = "__ADK_OBSERVABILITY_CUSTOM_DASHBOARD_DATA_JSON__"
TEMPLATE_PATH = Path(__file__).with_name("adk_observability_custom_dashboard.template.html")
RUNTIME_CHART_SURFACES = [
    "runtime_distribution",
    "cost_by_model",
    "daily_token_timeline",
    "model_buckets",
    "runtime_quantiles",
    "span_session_detail",
]
IDENTITY_KEYS = ["run_id", "case", "eval_id", "session_id", "trace_id", "span_id", "source_file"]


console = Console(stderr=True)
app = typer.Typer(
    add_completion=False,
    help="Build and optionally serve custom ADK observability dashboard enhancements.",
)
```

- [x] **Step 2: Add source references and run-key helpers**

```python
def source_ref(label: str, generator: str, kind: str) -> dict[str, str]:
    return {"label": label, "source_dashboard": generator, "kind": kind}


def normalized_key(value: Any) -> str:
    return str(value or "").strip()


def eval_run_key(run: dict[str, Any]) -> str:
    return normalized_key(run.get("eval_id") or run.get("run_id") or run.get("short_id"))


def scoring_run_key(report: dict[str, Any]) -> str:
    identity = report.get("identity") if isinstance(report.get("identity"), dict) else {}
    case = identity.get("case") if isinstance(identity.get("case"), dict) else {}
    return normalized_key(report.get("case") or case.get("value") or report.get("source_file"))


def runtime_run_key(session: dict[str, Any]) -> str:
    return normalized_key(session.get("session_id") or session.get("runtime"))
```

- [x] **Step 3: Add layer builders**

```python
def build_eval_layer(eval_inputs: list[Path] | None, *, project_root: Path) -> dict[str, Any]:
    data = build_eval_dashboard_data(eval_inputs, project_root=project_root)
    return {
        "source_dashboard": "scripts/adk_eval_dashboard.py",
        "source_files": data.get("source_files") or [],
        "run_count": data.get("run_count") or 0,
        "runs": data.get("runs") or [],
        "clusters": data.get("clusters") or {},
    }


def build_runtime_layer(runtime_dbs: list[Path] | None, *, project_root: Path) -> dict[str, Any]:
    data = build_runtime_dashboard_data(
        runtime_dbs,
        project_root=project_root,
        include_live_processes=False,
    )
    return {
        "source_dashboard": "scripts/adk_token_dashboard.py",
        "source_dbs": data.get("source_dbs") or [],
        "skipped_dbs": data.get("skipped_dbs") or [],
        "runtime_count": data.get("runtime_count") or 0,
        "session_count": data.get("session_count") or 0,
        "totals": data.get("totals") or {},
        "chart_surfaces": RUNTIME_CHART_SURFACES,
        "runtimes": data.get("runtimes") or [],
        "runtime_quantiles": data.get("runtime_quantiles") or [],
        "models": data.get("models") or [],
        "sessions": data.get("sessions") or [],
        "timeline": data.get("timeline") or [],
        "notes": data.get("notes") or [],
    }


def build_scoring_layer(score_inputs: list[Path] | None, *, project_root: Path) -> dict[str, Any]:
    data = build_scoring_dashboard_data(score_inputs or [], project_root=project_root)
    return {
        "source_dashboard": "scripts/continuation_eval_dashboard.py",
        "report_count": data.get("report_count") or 0,
        "reports": data.get("reports") or [],
    }
```

- [x] **Step 4: Add run index and attention drivers**

```python
def empty_run(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "coverage": {"eval": False, "runtime": False, "scoring": False},
        "eval": {},
        "runtime": {},
        "scoring": {},
        "source_links": {
            "eval": source_ref("ADK Eval", "scripts/adk_eval_dashboard.py", "eval"),
            "runtime": source_ref("ADK Runtime", "scripts/adk_token_dashboard.py", "runtime"),
            "scoring": source_ref("AGE-33 Scoring", "scripts/continuation_eval_dashboard.py", "scoring"),
        },
    }


def eval_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "eval_id": run.get("eval_id"),
        "status": run.get("status"),
        "score": run.get("score"),
        "flag_count": len(run.get("flags") or []),
        "event_count": (run.get("trace_summary") or {}).get("event_count"),
        "source_file": run.get("source_file"),
    }


def runtime_summary(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session.get("session_id"),
        "runtime": session.get("runtime"),
        "total_tokens": session.get("total_tokens") or 0,
        "total_cost": session.get("total_cost") or 0,
        "llm_events": session.get("llm_events") or 0,
        "models": session.get("models") or [],
    }


def score_value(report: dict[str, Any], label: str) -> Any:
    for card in report.get("score_cards") or []:
        if card.get("label") == label:
            return card.get("value")
    return None


def scoring_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": report.get("case"),
        "trajectory_score": score_value(report, "Trajectory score"),
        "milestones": score_value(report, "Milestones"),
        "ordering": score_value(report, "Ordering"),
        "event_count": len(report.get("events") or []),
        "source_file": report.get("source_file"),
    }


def build_run_index(layers: dict[str, Any]) -> list[dict[str, Any]]:
    by_run: dict[str, dict[str, Any]] = {}
    for run in layers["eval"].get("runs") or []:
        key = eval_run_key(run)
        if not key:
            continue
        item = by_run.setdefault(key, empty_run(key))
        item["coverage"]["eval"] = True
        item["eval"] = eval_summary(run)
    for session in layers["runtime"].get("sessions") or []:
        key = runtime_run_key(session)
        if not key:
            continue
        item = by_run.setdefault(key, empty_run(key))
        item["coverage"]["runtime"] = True
        item["runtime"] = runtime_summary(session)
    for report in layers["scoring"].get("reports") or []:
        key = scoring_run_key(report)
        if not key:
            continue
        item = by_run.setdefault(key, empty_run(key))
        item["coverage"]["scoring"] = True
        item["scoring"] = scoring_summary(report)
    return sorted(by_run.values(), key=lambda item: item["run_id"])


def attention_drivers(run_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for run in run_index:
        trajectory = run.get("scoring", {}).get("trajectory_score")
        if isinstance(trajectory, int | float) and trajectory < 0.6:
            rows.append(
                {
                    "run_id": run["run_id"],
                    "layer": "scoring",
                    "signal": "trajectory_score",
                    "value": trajectory,
                    "severity": "risk",
                    "source_path": "$.score_breakdown.trajectory_score.score",
                }
            )
        if run.get("eval", {}).get("status") == "fail":
            rows.append(
                {
                    "run_id": run["run_id"],
                    "layer": "eval",
                    "signal": "eval_status",
                    "value": "fail",
                    "severity": "fail",
                    "source_path": "$.eval_case_results[].final_eval_status",
                }
            )
    return rows
```

- [x] **Step 5: Add top-level data builder and rendering helpers**

```python
def build_dashboard_data(
    *,
    eval_inputs: list[Path] | None,
    score_inputs: list[Path] | None,
    runtime_dbs: list[Path] | None,
    project_root: Path | None,
) -> dict[str, Any]:
    root = project_root or Path.cwd()
    layers = {
        "eval": build_eval_layer(eval_inputs, project_root=root),
        "runtime": build_runtime_layer(runtime_dbs, project_root=root),
        "scoring": build_scoring_layer(score_inputs, project_root=root),
    }
    run_index = build_run_index(layers)
    return {
        "generated_at": dt.datetime.now(tz=dt.UTC).isoformat(),
        "platform": {
            "title": "ADK Observability Custom Enhancements",
            "subtitle": "One correlated shell for ADK eval, trace events, scoring reports, and runtime tokens.",
            "source_policy": "factual evidence only",
            "excluded": ["Streamlit job dashboard"],
        },
        "identity_keys": IDENTITY_KEYS,
        "source_dashboards": [
            source_ref("ADK Eval", "scripts/adk_eval_dashboard.py", "eval"),
            source_ref("ADK Runtime", "scripts/adk_token_dashboard.py", "runtime"),
            source_ref("AGE-33 Scoring", "scripts/continuation_eval_dashboard.py", "scoring"),
        ],
        "layers": layers,
        "run_index": run_index,
        "attention_drivers": attention_drivers(run_index),
    }


def data_for_script(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def load_template(path: Path = TEMPLATE_PATH) -> str:
    return path.read_text(encoding="utf-8")


def render_dashboard_html(data: dict[str, Any], *, template: str | None = None) -> str:
    dashboard_template = template if template is not None else load_template()
    if DASHBOARD_DATA_PLACEHOLDER not in dashboard_template:
        raise ValueError(f"Dashboard template must contain {DASHBOARD_DATA_PLACEHOLDER!r}.")
    return dashboard_template.replace(DASHBOARD_DATA_PLACEHOLDER, data_for_script(data), 1)


def write_dashboard(output: Path, data: dict[str, Any]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard_html(data), encoding="utf-8")
    return output


def write_json(output: Path, data: dict[str, Any]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return output
```

- [x] **Step 6: Add Typer CLI**

```python
def serve_file(output: Path, port: int) -> None:
    output = output.resolve()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(output.parent))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/{urllib.parse.quote(output.name)}"
        console.print(f"[green]Serving custom ADK observability dashboard:[/] {url}")
        httpd.serve_forever()


@app.command()
def main(
    eval_inputs: Annotated[
        list[Path] | None,
        typer.Option("--eval-input", help="ADK eval result file or directory. Repeatable."),
    ] = None,
    score_inputs: Annotated[
        list[Path] | None,
        typer.Option("--score-input", help="Continuation scoring report JSON file. Repeatable."),
    ] = None,
    runtime_dbs: Annotated[
        list[Path] | None,
        typer.Option("--runtime-db", help="ADK session.db file. Repeatable."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="HTML dashboard output path."),
    ] = DEFAULT_OUTPUT,
    dump_data: Annotated[
        Path | None,
        typer.Option("--dump-data", help="Optional path for the shaped dashboard JSON data."),
    ] = DEFAULT_DUMP_DATA,
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root", help="Root used to display source paths."),
    ] = None,
    serve: Annotated[
        bool,
        typer.Option("--serve", help="Serve the generated dashboard after writing it."),
    ] = False,
    port: Annotated[int, typer.Option("--port", help="Port used with --serve.")] = 8047,
) -> None:
    """Build an HTML custom ADK observability dashboard from local artifacts."""
    data = build_dashboard_data(
        eval_inputs=eval_inputs,
        score_inputs=score_inputs,
        runtime_dbs=runtime_dbs,
        project_root=project_root,
    )
    written = write_dashboard(output, data)
    if dump_data:
        write_json(dump_data, data)
    console.print(
        f"[green]Wrote[/] {written} with "
        f"{data['layers']['eval']['run_count']} eval run(s), "
        f"{data['layers']['runtime']['session_count']} runtime session(s), "
        f"{data['layers']['scoring']['report_count']} scoring report(s)."
    )
    if serve:
        serve_file(written, port)


if __name__ == "__main__":
    try:
        app()
    except KeyboardInterrupt:
        console.print("[yellow]Stopped custom ADK observability dashboard server.[/]")
        sys.exit(130)
```

- [x] **Step 7: Run the contract test**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_adk_observability_custom_dashboard.py::test_adk_observability_custom_cli_dumps_three_layers_and_run_index -q
```

Expected:

```text
1 passed
```

---

## Task 3: Build The Static Custom ADK Obs Template

**Files:**
- Create: `scripts/adk_observability_custom_dashboard.template.html`
- Modify: `tests/test_adk_observability_custom_dashboard.py`

- [x] **Step 1: Add HTML smoke expectations**

Extend `test_adk_observability_custom_cli_dumps_three_layers_and_run_index`:

```python
    html = html_path.read_text(encoding="utf-8")
    assert "Unified observability overview" in html
    assert "Evidence matrix" in html
    assert "Eval evidence" in html
    assert "Runtime Distribution" in html
    assert "Cost by Model" in html
    assert "Scoring evidence" in html
    assert "Provenance coverage" in html
    assert "Streamlit job dashboard" in html
    assert "__ADK_OBSERVABILITY_CUSTOM_DASHBOARD_DATA_JSON__" not in html
```

- [x] **Step 2: Create the template skeleton**

Use the current ADK dark shell style, but with platform-level navigation.

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ADK Observability Custom Enhancements</title>
    <style>
      :root {
        color-scheme: dark;
        --bg: #0f1117;
        --rail: #1b191d;
        --surface: #211f25;
        --surface-2: #17191f;
        --surface-3: #12141a;
        --line: #3c4043;
        --line-soft: #2a2d33;
        --ink: #e8eaed;
        --muted: #bdc1c6;
        --soft: #9aa0a6;
        --blue: #0b57d0;
        --green: #1e8e3e;
        --amber: #f29900;
        --red: #c5221f;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: var(--bg);
        color: var(--ink);
        font-family: Inter, "Google Sans", Roboto, "Helvetica Neue", Arial, sans-serif;
        letter-spacing: 0;
      }
      button { font: inherit; }
      .shell { display: grid; grid-template-columns: 228px minmax(0, 1fr); min-height: 100vh; }
      .rail { border-right: 1px solid var(--line-soft); background: var(--rail); padding: 28px; }
      .brand { display: grid; grid-template-columns: 32px 1fr; gap: 12px; align-items: center; padding-bottom: 22px; border-bottom: 1px solid var(--line); }
      .mark { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px; background: var(--blue); font-size: 9px; font-weight: 800; }
      .nav { display: grid; gap: 10px; margin-top: 26px; }
      .nav button { min-height: 38px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); text-align: left; padding: 0 14px; }
      .nav button.is-active { background: var(--blue); color: white; font-weight: 700; }
      .topbar { position: sticky; top: 0; z-index: 10; border-bottom: 1px solid var(--line-soft); background: var(--bg); padding: 22px 36px 14px; }
      .page { display: grid; gap: 26px; padding: 32px 36px 64px; }
      .grid { display: grid; gap: 16px; }
      .three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .two { grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr); }
      .card, .panel { border: 1px solid var(--line-soft); border-radius: 8px; background: var(--surface); padding: 18px; }
      .panel.alt, .card.alt { background: var(--surface-2); }
      .label { color: var(--soft); font-size: 11px; font-weight: 800; text-transform: uppercase; }
      .big { margin-top: 10px; font-size: 28px; font-weight: 800; overflow-wrap: anywhere; }
      .micro { color: var(--muted); font-size: 12px; line-height: 1.45; }
      .pill { display: inline-flex; min-height: 28px; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 0 12px; color: var(--ink); font-size: 12px; font-weight: 700; }
      .pills { display: flex; flex-wrap: wrap; gap: 8px; }
      .table-wrap { overflow: auto; border: 1px solid var(--line-soft); border-radius: 8px; }
      table { width: 100%; border-collapse: collapse; min-width: 720px; }
      th, td { border-bottom: 1px solid var(--line-soft); padding: 10px 12px; text-align: left; vertical-align: top; }
      th { color: var(--soft); font-size: 11px; text-transform: uppercase; }
      tr:last-child td { border-bottom: 0; }
      .bar { height: 10px; border-radius: 999px; background: var(--surface-3); overflow: hidden; }
      .bar > span { display: block; height: 100%; border-radius: inherit; background: var(--green); }
      .source { color: var(--soft); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; overflow-wrap: anywhere; }
      @media (max-width: 1100px) {
        .shell { grid-template-columns: 1fr; }
        .three, .two { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <aside class="rail">
        <div class="brand">
          <span class="mark">ADK</span>
          <div><strong>ADK Obs</strong><div class="micro">reconciled evidence</div></div>
        </div>
        <nav class="nav" id="nav"></nav>
        <div class="panel alt" style="margin-top: 28px;">
          <div class="label">Excluded</div>
          <strong>Streamlit job dashboard</strong>
          <div class="micro">Not counted in this platform</div>
        </div>
      </aside>
      <section>
        <header class="topbar">
          <h1 id="page-title">Unified observability overview</h1>
          <p class="micro" id="page-subtitle">ADK eval, trace events, scoring, and runtime tokens in one correlated shell.</p>
        </header>
        <main class="page" id="app"></main>
      </section>
    </div>
    <script>
      window.__ADK_OBSERVABILITY_CUSTOM_DASHBOARD_DATA__ = __ADK_OBSERVABILITY_CUSTOM_DASHBOARD_DATA_JSON__;
    </script>
  </body>
</html>
```

- [x] **Step 3: Add rendering utilities and navigation**

```javascript
const data = window.__ADK_OBSERVABILITY_CUSTOM_DASHBOARD_DATA__;
const state = { view: "overview" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function compact(value) {
  const number = Number(value || 0);
  if (Math.abs(number) >= 1000000) return `${(number / 1000000).toFixed(1)}M`;
  if (Math.abs(number) >= 1000) return `${(number / 1000).toFixed(1)}k`;
  return Number.isInteger(number) ? String(number) : number.toFixed(3);
}

function money(value) {
  return `$${Number(value || 0).toFixed(4)}`;
}

function pill(value) {
  return `<span class="pill">${esc(value)}</span>`;
}

function renderNav() {
  const items = [
    ["overview", "Overview"],
    ["eval", "Eval"],
    ["runtime", "Runtime"],
    ["scoring", "Scoring"],
    ["evidence", "Evidence"],
    ["sources", "Sources"],
  ];
  document.getElementById("nav").innerHTML = items.map(([key, label]) =>
    `<button class="${state.view === key ? "is-active" : ""}" data-view="${key}">${label}</button>`
  ).join("");
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (button) {
    state.view = button.dataset.view;
    render();
  }
});
```

- [x] **Step 4: Add the required views**

The template must implement these functions:

```javascript
function renderOverview() {
  document.getElementById("page-title").textContent = "Unified observability overview";
  document.getElementById("page-subtitle").textContent = "One first screen for scan, compare, and jump-to-evidence across eval, runtime, and scoring.";
  const runtime = data.layers.runtime;
  const scoring = data.layers.scoring;
  const evalLayer = data.layers.eval;
  document.getElementById("app").innerHTML = `
    <section class="grid three">
      <article class="card"><div class="label">ADK Eval</div><div class="big">${compact(evalLayer.run_count)} runs</div><p class="micro">Rubrics and trace events</p></article>
      <article class="card"><div class="label">Runtime</div><div class="big">${compact(runtime.totals?.total_tokens)} tokens</div><p class="micro">${money(runtime.totals?.total_cost)} local estimated cost</p></article>
      <article class="card"><div class="label">Scoring</div><div class="big">${compact(scoring.report_count)} reports</div><p class="micro">Formula terms, milestones, semantic labels</p></article>
    </section>
    <section class="grid two">
      <article class="card"><h2>Evidence matrix</h2>${evidenceMatrix(data.run_index)}</article>
      <article class="card alt"><h2>Attention drivers</h2>${attentionList(data.attention_drivers)}</article>
    </section>
  `;
}

function renderEval() {
  document.getElementById("page-title").textContent = "Eval evidence";
  document.getElementById("page-subtitle").textContent = "ADK eval runs, rubric flags, judge citations, and invocation timeline counts.";
  document.getElementById("app").innerHTML = evalEvidence(data.layers.eval);
}

function renderRuntime() {
  document.getElementById("page-title").textContent = "Runtime tokens";
  document.getElementById("page-subtitle").textContent = "Token charts remain first-class inside the unified platform.";
  document.getElementById("app").innerHTML = runtimeCharts(data.layers.runtime);
}

function renderScoring() {
  document.getElementById("page-title").textContent = "Scoring evidence";
  document.getElementById("page-subtitle").textContent = "Score formulas, milestones, ordering checks, semantic labels, and normalized events.";
  document.getElementById("app").innerHTML = scoringEvidence(data.layers.scoring);
}

function renderEvidence() {
  document.getElementById("page-title").textContent = "Provenance coverage";
  document.getElementById("page-subtitle").textContent = "Correlation keys, source files, JSON paths, and missing identifiers.";
  document.getElementById("app").innerHTML = provenanceCoverage(data);
}

function renderSources() {
  document.getElementById("page-title").textContent = "Source dashboards";
  document.getElementById("page-subtitle").textContent = "The unified dashboard reconciles these sources without replacing them.";
  document.getElementById("app").innerHTML = sourceDashboards(data.source_dashboards);
}
```

- [x] **Step 5: Run the template smoke test**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_adk_observability_custom_dashboard.py -q
```

Expected:

```text
1 passed
```

---

## Task 4: Make Runtime Charts First-Class In The Unified Template

**Files:**
- Modify: `scripts/adk_observability_custom_dashboard.template.html`
- Modify: `tests/test_adk_observability_custom_dashboard.py`

- [x] **Step 1: Add runtime chart assertions**

Extend the contract test:

```python
    assert "Runtime Distribution" in html
    assert "Cost by Model" in html
    assert "Daily Token Timeline" in html
    assert "Runtime Quantiles" in html
    assert "scripts/adk_token_dashboard.py" in html
```

- [x] **Step 2: Implement runtime chart helpers**

Add chart helpers that consume `data.layers.runtime` directly:

```javascript
function simpleBar(value, max) {
  const pct = max ? Math.max(3, Math.min(100, (Number(value || 0) / max) * 100)) : 0;
  return `<div class="bar"><span style="width:${pct}%"></span></div>`;
}

function runtimeCharts(runtime) {
  const maxRuntime = Math.max(...(runtime.runtimes || []).map((row) => Number(row.total_tokens || 0)), 0);
  const maxModelCost = Math.max(...(runtime.models || []).map((row) => Number(row.total_cost || 0)), 0);
  return `
    <section class="panel alt">
      <h2>Token charts stay first-class</h2>
      <p class="micro">Generated from ${esc(runtime.source_dashboard)} and correlated through session/run identity.</p>
      <div class="pills">${(runtime.chart_surfaces || []).map(pill).join("")}</div>
    </section>
    <section class="grid two">
      <article class="card">
        <h2>Runtime Distribution</h2>
        ${(runtime.runtimes || []).map((row) => `
          <div style="display:grid;grid-template-columns:160px 1fr 70px;gap:12px;align-items:center;margin-top:12px;">
            <span>${esc(row.runtime)}</span>${simpleBar(row.total_tokens, maxRuntime)}<strong>${compact(row.total_tokens)}</strong>
          </div>
        `).join("") || `<p class="micro">No runtime data supplied.</p>`}
      </article>
      <article class="card">
        <h2>Cost by Model</h2>
        ${(runtime.models || []).map((row) => `
          <div style="display:grid;grid-template-columns:160px 1fr 70px;gap:12px;align-items:center;margin-top:12px;">
            <span>${esc(row.model)}</span>${simpleBar(row.total_cost, maxModelCost)}<strong>${money(row.total_cost)}</strong>
          </div>
        `).join("") || `<p class="micro">No model cost data supplied.</p>`}
      </article>
    </section>
    <section class="grid two">
      <article class="card"><h2>Daily Token Timeline</h2>${timelineTable(runtime.timeline || [])}</article>
      <article class="card"><h2>Runtime Quantiles</h2>${quantileTable(runtime.runtime_quantiles || [])}</article>
    </section>
  `;
}
```

- [x] **Step 3: Run the focused test**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_adk_observability_custom_dashboard.py -q
```

Expected:

```text
1 passed
```

---

## Task 5: Add Eval, Scoring, Evidence, And Sources Views

**Files:**
- Modify: `scripts/adk_observability_custom_dashboard.template.html`
- Modify: `tests/test_adk_observability_custom_dashboard.py`

- [x] **Step 1: Add HTML assertions for all cross-layer ADK obs views**

```python
    for expected in [
        "Eval evidence",
        "Scoring evidence",
        "Provenance coverage",
        "Source dashboards",
        "scripts/adk_eval_dashboard.py",
        "scripts/adk_token_dashboard.py",
        "scripts/continuation_eval_dashboard.py",
    ]:
        assert expected in html
```

- [x] **Step 2: Add table helpers**

```javascript
function evidenceMatrix(rows) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Run</th><th>Eval</th><th>Runtime</th><th>Scoring</th></tr></thead>
        <tbody>${(rows || []).map((run) => `
          <tr>
            <td><strong>${esc(run.run_id)}</strong></td>
            <td>${run.coverage.eval ? esc(run.eval.status || "present") : "absent"}</td>
            <td>${run.coverage.runtime ? `${compact(run.runtime.total_tokens)} tokens` : "absent"}</td>
            <td>${run.coverage.scoring ? compact(run.scoring.trajectory_score) : "absent"}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    </div>
  `;
}

function attentionList(rows) {
  if (!rows.length) return `<p class="micro">No attention drivers.</p>`;
  return rows.map((row) => `
    <div class="panel alt" style="margin-top:12px;">
      <strong>${esc(row.run_id)}</strong>
      <p class="micro">${esc(row.layer)} / ${esc(row.signal)} / ${esc(row.severity)}</p>
      <div class="source">${esc(row.source_path)}</div>
    </div>
  `).join("");
}

function sourceDashboards(sources) {
  return `<section class="grid three">${(sources || []).map((source) => `
    <article class="card">
      <div class="label">${esc(source.kind)}</div>
      <h2>${esc(source.label)}</h2>
      <div class="source">${esc(source.source_dashboard)}</div>
    </article>
  `).join("")}</section>`;
}
```

- [x] **Step 3: Add eval and scoring renderers**

```javascript
function evalEvidence(layer) {
  return `<section class="card">
    <h2>Eval evidence</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Eval ID</th><th>Status</th><th>Flags</th><th>Events</th><th>Source</th></tr></thead>
        <tbody>${(layer.runs || []).map((run) => `
          <tr>
            <td>${esc(run.eval_id)}</td>
            <td>${esc(run.status)}</td>
            <td>${(run.flags || []).length}</td>
            <td>${esc((run.trace_summary || {}).event_count)}</td>
            <td class="source">${esc(run.source_file)}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    </div>
  </section>`;
}

function scoringEvidence(layer) {
  return `<section class="grid two">${(layer.reports || []).map((report) => `
    <article class="card">
      <h2>${esc(report.case)}</h2>
      <div class="pills">${(report.score_cards || []).map((card) => pill(`${card.label}: ${card.value}`)).join("")}</div>
      <p class="micro">Formula: ${esc(report.trajectory?.formula)}</p>
      <div class="source">${esc(report.source_file)}</div>
    </article>
  `).join("") || `<article class="card"><h2>Scoring evidence</h2><p class="micro">No score reports supplied.</p></article>`}</section>`;
}

function provenanceCoverage(data) {
  return `<section class="grid two">
    <article class="card">
      <h2>Correlation keys</h2>
      <div class="pills">${(data.identity_keys || []).map(pill).join("")}</div>
    </article>
    <article class="card">
      <h2>Provenance coverage</h2>
      <p class="micro">Coverage is factual: present or absent by source layer.</p>
      ${evidenceMatrix(data.run_index || [])}
    </article>
  </section>`;
}
```

- [x] **Step 4: Run the test suite for dashboard surfaces**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_adk_observability_custom_dashboard.py tests/test_continuation_eval_dashboard.py tests/test_adk_token_dashboard.py tests/test_adk_eval_dashboard.py -q
```

Expected:

```text
All selected dashboard tests pass.
```

---

## Task 6: Browser/Screenshot Verification

**Files:**
- No source files required.
- Output artifact: `/tmp/age33-adk-observability-custom-dashboard.png`

- [x] **Step 1: Generate fixture artifacts and dashboard**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_adk_observability_custom_dashboard.py -q
```

Expected:

```text
1 passed
```

Then run the CLI manually using the same fixture paths or add a tiny temporary fixture setup command from the test helper if needed:

```bash
PYTHONPATH=src .venv/bin/python scripts/adk_observability_custom_dashboard.py \
  --eval-input /tmp/age33-adk-obs-fixtures/bad_premature_finalization_run.evalset_result.json \
  --score-input /tmp/age33-adk-obs-fixtures/bad_premature_finalization_run.score.json \
  --runtime-db /tmp/age33-adk-obs-fixtures/src/job_scraper/.adk/session.db \
  --project-root /tmp/age33-adk-obs-fixtures \
  --output /tmp/age33-adk-observability-custom-dashboard.html \
  --dump-data /tmp/age33-adk-observability-custom-dashboard.json
```

- [x] **Step 2: Capture a PNG with local Chrome**

Run:

```bash
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  --headless=new \
  --disable-gpu \
  --no-first-run \
  --no-default-browser-check \
  --user-data-dir=/tmp/age33-adk-obs-chrome-profile \
  --window-size=1440,1200 \
  --screenshot=/tmp/age33-adk-observability-custom-dashboard.png \
  file:///tmp/age33-adk-observability-custom-dashboard.html
```

Expected:

```text
PNG image data, 1440 x 1200
```

- [x] **Step 3: Inspect the PNG**

Confirm visually:

- The first screen is a unified overview, not the scoring-only dashboard.
- Eval, Runtime, Scoring, Evidence, and Sources are visible as platform views.
- Runtime token charts appear as actual chart panels.
- Streamlit is explicitly excluded.
- The dashboard is factual and does not include repair advice.

---

## Task 7: Regression And Context Update

**Files:**
- Modify: `.contexts/tasks/T-002.md`
- Modify: `.contexts/handoff.md`
- Modify: `.contexts/lineage/events.jsonl`

- [x] **Step 1: Run focused AGE-33 dashboard regression**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_continuation_eval_scoring.py \
  tests/test_score_continuation_eval.py \
  tests/test_adk_eval_dashboard.py \
  tests/test_adk_token_dashboard.py \
  tests/test_adk_observability_entrypoint.py \
  tests/test_continuation_eval_dashboard.py \
  tests/test_adk_observability_custom_dashboard.py \
  -q
```

Expected:

```text
All selected tests pass.
```

- [x] **Step 2: Validate no new importlib script loading in the AGE-33 custom ADK observability path**

Run:

```bash
rg -n "importlib|spec_from_file_location" \
  scripts/adk_observability_custom_dashboard.py \
  tests/test_adk_observability_custom_dashboard.py
```

Expected:

```text
No matches.
```

- [x] **Step 3: Update context**

Run:

```bash
.contexts/bin/update_task T-002 \
  --summary "Implemented scoring dashboard and planned custom ADK observability dashboard enhancements that reconcile eval, trace/event, scoring, and runtime token evidence." \
  --next-step "Implement docs/superpowers/plans/2026-05-31-age-33-observability-reconciliation-dashboard.md task-by-task, creating scripts/adk_observability_custom_dashboard.py."

.contexts/bin/update_handoff \
  --active-task T-002 \
  --summary "Created the custom ADK observability enhancements plan: unified shell for ADK eval, traces/events, scoring reports, and runtime token charts." \
  --next-step "Implement the custom ADK observability dashboard plan task-by-task." \
  --touched-file docs/superpowers/plans/2026-05-31-age-33-observability-reconciliation-dashboard.md

.contexts/bin/append_lineage planning \
  "Created AGE-33 custom ADK observability enhancements implementation plan." \
  --task-id T-002 \
  --file docs/superpowers/plans/2026-05-31-age-33-observability-reconciliation-dashboard.md
```

- [x] **Step 4: Validate context**

Run:

```bash
.contexts/bin/validate_context
```

Expected:

```json
{"valid": true}
```

## Final Verification Checklist

- [x] Unified dashboard exists at `scripts/adk_observability_custom_dashboard.py`.
- [x] Static template exists at `scripts/adk_observability_custom_dashboard.template.html`.
- [x] Subprocess test exists at `tests/test_adk_observability_custom_dashboard.py`.
- [x] The dashboard includes ADK Eval, Runtime, Scoring, Evidence, and Sources views.
- [x] Runtime charts are rendered as first-class panels, not just a handoff note.
- [x] Source dashboards remain separate and named.
- [x] Streamlit job dashboard is explicitly excluded.
- [x] No new `importlib.spec_from_file_location()` usage is added.
- [x] Focused AGE-33 regression tests pass.
- [x] Browser screenshot confirms the first screen is a custom ADK observability dashboard.
