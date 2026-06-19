from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALSET_PATH = Path("tests/eval/evalsets/job_scraper_core.json")
CONFIG_PATH = Path("tests/eval/eval_config_goal_contract.json")
EVAL_ID = "itviec_immediate_goal_before_producer_scripting"
AGE10_PROMPT = "Start extracting job listings from the fixed ITviec AI Engineer Hanoi HTML fixture."
REQUIRED_EVALUATED_METRICS: set[str] = set()


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _adk_executable() -> str | None:
    local_adk = PROJECT_ROOT / ".venv/bin/adk"
    if local_adk.exists():
        return str(local_adk)
    return shutil.which("adk")


def _run_count() -> int:
    raw = os.getenv("JOB_SCRAPER_ADK_EVAL_RUNS", "5")
    try:
        runs = int(raw)
    except ValueError as exc:
        raise AssertionError(f"JOB_SCRAPER_ADK_EVAL_RUNS must be an integer, got {raw!r}") from exc
    if not 1 <= runs <= 10:
        raise AssertionError("JOB_SCRAPER_ADK_EVAL_RUNS must be between 1 and 10")
    return runs


def _load_age10_prompt() -> str:
    payload = json.loads((PROJECT_ROOT / EVALSET_PATH).read_text(encoding="utf-8"))
    cases = {case["eval_id"]: case for case in payload["eval_cases"]}
    return cases[EVAL_ID]["conversation"][0]["user_content"]["parts"][0]["text"]


def _latest_result_after(started_at: float) -> Path:
    history_dir = PROJECT_ROOT / "src/.adk/eval_history"
    candidates = [
        path
        for path in history_dir.glob("src_job_scraper_core_*.evalset_result.json")
        if path.stat().st_mtime >= started_at - 1
    ]
    if not candidates:
        raise AssertionError(f"No ADK eval result file was written under {history_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _run_age10_eval(adk: str, *, timeout_seconds: int) -> Path:
    started_at = time.time()
    command = [
        adk,
        "eval",
        "src",
        f"{EVALSET_PATH}:{EVAL_ID}",
        "--config_file_path",
        str(CONFIG_PATH),
        "--log_level",
        "ERROR",
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout + completed.stderr).splitlines()[-80:])
        raise AssertionError(f"adk eval failed with exit code {completed.returncode}\n{tail}")
    return _latest_result_after(started_at)


def _metric_summary(result_path: Path) -> dict[str, Any]:
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    case = payload["eval_case_results"][0]
    metrics = {
        metric["metric_name"]: {
            "score": metric.get("score"),
            "threshold": metric.get("threshold"),
            "status": metric.get("eval_status"),
            "rubrics": {
                rubric["rubric_id"]: rubric.get("score")
                for rubric in (metric.get("details", {}).get("rubric_scores") or [])
            },
        }
        for metric in case["overall_eval_metric_results"]
    }
    return {
        "result_file": str(result_path.relative_to(PROJECT_ROOT)),
        "result_id": payload["eval_set_result_id"],
        "final_status": case["final_eval_status"],
        "metrics": metrics,
    }


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.fmean(values)


def _is_not_evaluated(metric: dict[str, Any]) -> bool:
    return metric.get("score") is None or metric.get("status") == 3


def _summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = sorted({name for run in runs for name in run["metrics"]} | REQUIRED_EVALUATED_METRICS)
    metrics: dict[str, Any] = {}
    rubric_averages: dict[str, dict[str, float | None]] = {}
    coverage_failures: dict[str, Any] = {}

    for name in metric_names:
        metric_entries = [run["metrics"][name] for run in runs if name in run["metrics"]]
        scores = [entry["score"] for entry in metric_entries if entry["score"] is not None]
        threshold = metric_entries[0]["threshold"] if metric_entries else None
        missing_run_count = len(runs) - len(metric_entries)
        not_evaluated_entries = [entry for entry in metric_entries if _is_not_evaluated(entry)]
        not_evaluated_run_count = missing_run_count + len(not_evaluated_entries)
        passing_scores = [
            score
            for score in scores
            if threshold is not None and score >= threshold
        ]
        metrics[name] = {
            "average_score": _average(scores),
            "threshold": threshold,
            "evaluated_run_count": len(scores),
            "not_evaluated_run_count": not_evaluated_run_count,
            "missing_run_count": missing_run_count,
            "evaluated_pass_rate": len(passing_scores) / len(scores) if scores else None,
            "run_pass_rate": len(passing_scores) / len(metric_entries) if metric_entries else None,
            "scores": scores,
        }
        if name in REQUIRED_EVALUATED_METRICS and not_evaluated_run_count:
            coverage_failures[name] = {
                "required_run_count": len(runs),
                "evaluated_run_count": len(scores),
                "not_evaluated_run_count": not_evaluated_run_count,
                "missing_run_count": missing_run_count,
                "statuses": [entry.get("status") for entry in metric_entries],
                "scores": scores,
            }

        rubric_ids = sorted({rubric_id for entry in metric_entries for rubric_id in entry["rubrics"]})
        if rubric_ids:
            rubric_averages[name] = {
                rubric_id: _average(
                    [
                        entry["rubrics"][rubric_id]
                        for entry in metric_entries
                        if entry["rubrics"].get(rubric_id) is not None
                    ]
                )
                for rubric_id in rubric_ids
            }

    return {
        "eval_id": EVAL_ID,
        "prompt": AGE10_PROMPT,
        "run_count": len(runs),
        "passed_run_count": sum(
            all(
                metric["score"] is not None
                and metric["threshold"] is not None
                and metric["score"] >= metric["threshold"]
                for metric in run["metrics"].values()
            )
            for run in runs
        ),
        "metrics": metrics,
        "rubric_averages": rubric_averages,
        "coverage_failures": coverage_failures,
        "result_files": [run["result_file"] for run in runs],
    }


def test_age10_eval_summary_does_not_gate_on_hallucination_coverage() -> None:
    runs = [
        {
            "result_file": "run-1.json",
            "metrics": {
                "rubric_based_final_response_quality_v1": {
                    "score": 1.0,
                    "threshold": 0.85,
                    "status": 1,
                    "rubrics": {},
                },
                "hallucinations_v1": {"score": 1.0, "threshold": 0.8, "status": 1, "rubrics": {}},
            },
        },
        {
            "result_file": "run-2.json",
            "metrics": {
                "rubric_based_final_response_quality_v1": {
                    "score": 0.6,
                    "threshold": 0.85,
                    "status": 2,
                    "rubrics": {},
                },
                "hallucinations_v1": {"score": None, "threshold": 0.8, "status": 3, "rubrics": {}},
            },
        },
    ]

    summary = _summarize_runs(runs)

    assert summary["metrics"]["hallucinations_v1"]["not_evaluated_run_count"] == 1
    assert summary["coverage_failures"] == {}


@pytest.mark.adk_eval
def test_age10_goal_contract_repeated_eval_average() -> None:
    if not _env_flag("JOB_SCRAPER_RUN_ADK_EVAL_AVERAGE"):
        pytest.skip("Set JOB_SCRAPER_RUN_ADK_EVAL_AVERAGE=1 to run repeated ADK eval scoring.")

    adk = _adk_executable()
    if adk is None:
        pytest.skip("ADK CLI is not available.")

    assert _load_age10_prompt() == AGE10_PROMPT

    timeout_seconds = int(os.getenv("JOB_SCRAPER_ADK_EVAL_TIMEOUT_SECONDS", "300"))
    runs = [
        _metric_summary(_run_age10_eval(adk, timeout_seconds=timeout_seconds))
        for _ in range(_run_count())
    ]
    summary = _summarize_runs(runs)
    print("\nAGE-10 repeated ADK eval summary:")
    print(json.dumps(summary, indent=2))

    assert not summary["coverage_failures"], json.dumps(summary, indent=2)

    if _env_flag("JOB_SCRAPER_ADK_EVAL_ASSERT_THRESHOLDS"):
        failing = {
            name: stats
            for name, stats in summary["metrics"].items()
            if stats["average_score"] is not None
            and stats["threshold"] is not None
            and stats["average_score"] < stats["threshold"]
        }
        assert not failing, json.dumps(summary, indent=2)
