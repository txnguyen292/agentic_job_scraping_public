from __future__ import annotations

from pathlib import Path

from google.adk.evaluation.eval_config import EvalConfig


def test_eval_config_uses_rubric_first_metrics() -> None:
    config = EvalConfig.model_validate_json(Path("tests/eval/eval_config.json").read_text(encoding="utf-8"))

    assert "rubric_based_tool_use_quality_v1" in config.criteria
    assert "rubric_based_final_response_quality_v1" in config.criteria
    assert "hallucinations_v1" in config.criteria
    assert config.criteria["tool_trajectory_avg_score"].threshold == 0.7


def test_goal_contract_eval_config_is_elastic() -> None:
    config = EvalConfig.model_validate_json(
        Path("tests/eval/eval_config_goal_contract.json").read_text(encoding="utf-8")
    )

    assert "tool_trajectory_avg_score" not in config.criteria
    assert "rubric_based_tool_use_quality_v1" in config.criteria
    assert "rubric_based_final_response_quality_v1" in config.criteria
    assert "hallucinations_v1" not in config.criteria

    tool_rubrics = config.criteria["rubric_based_tool_use_quality_v1"].rubrics
    final_rubrics = config.criteria["rubric_based_final_response_quality_v1"].rubrics
    tool_text = "\n".join(rubric["rubric_content"]["text_property"] for rubric in tool_rubrics)
    final_text = "\n".join(rubric["rubric_content"]["text_property"] for rubric in final_rubrics)

    assert "immediate_goal" in tool_text
    assert "extraction_plan" in tool_text
    assert "extraction_strategy" in tool_text
    assert "validation" in tool_text
    assert "does not claim full extraction or persistence" in final_text
    assert "If boundary evidence was validated" in final_text
    assert "next incremental extraction goal" in final_text
    assert "grounded_final_claims_with_evidence" in {rubric["rubric_id"] for rubric in final_rubrics}
    assert "no_completion_claim_without_tool_evidence" in {rubric["rubric_id"] for rubric in final_rubrics}
    assert "reports_only_supported_missing_artifacts" in {rubric["rubric_id"] for rubric in final_rubrics}
    assert "supported by tool evidence" in final_text
    assert "matching successful tool evidence" in final_text
    assert "direct tool evidence" in final_text
    assert "Trace anchors:" in tool_text
    assert "Trace anchors:" not in final_text
    assert "invocation_events[N]" in tool_text
    assert "not an agent-response requirement" in tool_text
    assert all(
        "Trace anchors:" in rubric["rubric_content"]["text_property"]
        for rubric in tool_rubrics
    )
    assert all(
        len(rubric["rubric_content"]["text_property"]) <= 120
        for rubric in final_rubrics
    )
    assert all(
        "Do not score the agent" in rubric["rubric_content"]["text_property"]
        or "Do not lower the score" in rubric["rubric_content"]["text_property"]
        for rubric in tool_rubrics
    )

    assert config.criteria["rubric_based_tool_use_quality_v1"].judge_model_options["num_samples"] == 1
    assert config.criteria["rubric_based_final_response_quality_v1"].judge_model_options["num_samples"] == 1


def test_goal_contract_pr_eval_config_preserves_stronger_judge_sampling() -> None:
    config = EvalConfig.model_validate_json(
        Path("tests/eval/eval_config_goal_contract_pr.json").read_text(encoding="utf-8")
    )

    assert "hallucinations_v1" not in config.criteria
    assert config.criteria["rubric_based_tool_use_quality_v1"].judge_model_options["num_samples"] == 3
    assert config.criteria["rubric_based_final_response_quality_v1"].judge_model_options["num_samples"] == 3
    tool_text = "\n".join(
        rubric["rubric_content"]["text_property"]
        for rubric in config.criteria["rubric_based_tool_use_quality_v1"].rubrics
    )
    final_text = "\n".join(
        rubric["rubric_content"]["text_property"]
        for rubric in config.criteria["rubric_based_final_response_quality_v1"].rubrics
    )
    assert "Trace anchors:" in tool_text
    assert "Trace anchors:" not in final_text
    assert "not an agent-response requirement" in tool_text
    assert all(
        "Trace anchors:" in rubric["rubric_content"]["text_property"]
        for rubric in config.criteria["rubric_based_tool_use_quality_v1"].rubrics
    )
    assert all(
        len(rubric["rubric_content"]["text_property"]) <= 120
        for rubric in config.criteria["rubric_based_final_response_quality_v1"].rubrics
    )


def test_core_evalset_contains_required_cases() -> None:
    import json

    payload = json.loads(Path("tests/eval/evalsets/job_scraper_core.json").read_text(encoding="utf-8"))
    eval_ids = {case["eval_id"] for case in payload["eval_cases"]}

    assert {
        "large_itviec_sandbox_workflow",
        "guardrail_surfaces_blocker",
        "itviec_immediate_goal_before_producer_scripting",
    } <= eval_ids

    assert "itviec_minimal_fixed_fixture_extraction" not in eval_ids


def test_age10_eval_uses_natural_start_prompt() -> None:
    import json

    payload = json.loads(Path("tests/eval/evalsets/job_scraper_core.json").read_text(encoding="utf-8"))
    cases = {case["eval_id"]: case for case in payload["eval_cases"]}
    age10_case = cases["itviec_immediate_goal_before_producer_scripting"]
    prompt = age10_case["conversation"][0]["user_content"]["parts"][0]["text"]

    assert prompt == "Start extracting job listings from the fixed ITviec AI Engineer Hanoi HTML fixture."
