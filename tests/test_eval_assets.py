from __future__ import annotations

from pathlib import Path

from google.adk.evaluation.eval_config import EvalConfig


def test_eval_config_uses_rubric_first_metrics() -> None:
    config = EvalConfig.model_validate_json(Path("tests/eval/eval_config.json").read_text(encoding="utf-8"))

    assert "rubric_based_tool_use_quality_v1" in config.criteria
    assert "rubric_based_final_response_quality_v1" in config.criteria
    assert "hallucinations_v1" in config.criteria
    assert config.criteria["tool_trajectory_avg_score"].threshold == 0.7


def test_core_evalset_contains_required_cases() -> None:
    import json

    payload = json.loads(Path("tests/eval/evalsets/job_scraper_core.json").read_text(encoding="utf-8"))
    eval_ids = {case["eval_id"] for case in payload["eval_cases"]}

    assert {"large_itviec_sandbox_workflow", "guardrail_surfaces_blocker"} <= eval_ids
