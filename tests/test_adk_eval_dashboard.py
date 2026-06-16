from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "adk_eval_dashboard.py"
SPEC = importlib.util.spec_from_file_location("adk_eval_dashboard", MODULE_PATH)
assert SPEC is not None
adk_eval_dashboard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adk_eval_dashboard)

UTILS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "utils.py"
UTILS_SPEC = importlib.util.spec_from_file_location("adk_eval_dashboard_utils", UTILS_PATH)
assert UTILS_SPEC is not None
adk_eval_dashboard_utils = importlib.util.module_from_spec(UTILS_SPEC)
assert UTILS_SPEC.loader is not None
UTILS_SPEC.loader.exec_module(adk_eval_dashboard_utils)


def _metric(name: str, score: float | None, status: int, rubrics: list[dict] | None = None) -> dict:
    rubric_defs = [
        {
            "rubric_id": rubric["rubric_id"],
            "rubric_content": {"text_property": f"Definition for {rubric['rubric_id']}."},
        }
        for rubric in rubrics or []
    ]
    return {
        "metric_name": name,
        "threshold": 0.85,
        "score": score,
        "eval_status": status,
        "details": {"rubric_scores": rubrics},
        "criterion": {"threshold": 0.85, "rubrics": rubric_defs},
    }


def _sample_result(final_text: str = "Boundary evidence missing.") -> dict:
    tool_metric = _metric(
        "rubric_based_tool_use_quality_v1",
        1.0,
        1,
        [
            {
                "rubric_id": "records_immediate_goal_before_producer",
                "score": 1.0,
                "rationale": "The agent recorded the immediate goal before producer work.",
            }
        ],
    )
    final_metric = _metric(
        "rubric_based_final_response_quality_v1",
        0.5,
        2,
        [
            {
                "rubric_id": "reports_boundary_evidence",
                "score": 0.0,
                "rationale": "The final response omitted the repeated unit selector.",
            }
        ],
    )
    hallucination_metric = _metric("hallucinations_v1", None, 3, None)
    return {
        "eval_set_result_id": "src_job_scraper_core_1778808199.500643",
        "eval_set_id": "job_scraper_core",
        "creation_timestamp": 1778808199.500643,
        "eval_case_results": [
            {
                "eval_id": "itviec_immediate_goal_before_producer_scripting",
                "final_eval_status": 2,
                "overall_eval_metric_results": [tool_metric, final_metric, hallucination_metric],
                "eval_metric_result_per_invocation": [
                    {
                        "actual_invocation": {
                            "invocation_id": "inv-1",
                            "user_content": {"role": "user", "parts": [{"text": "Extract the fixture."}]},
                            "intermediate_data": {
                                "invocation_events": [
                                    {
                                        "author": "job_listing_scout",
                                        "content": {
                                            "role": "model",
                                            "parts": [
                                                {
                                                    "function_call": {
                                                        "id": "call-1",
                                                        "name": "update_extraction_context",
                                                        "args": {
                                                            "immediate_goal": "Inspect the saved fixture boundary."
                                                        },
                                                    }
                                                }
                                            ]
                                        },
                                    },
                                    {
                                        "author": "job_listing_scout",
                                        "content": {
                                            "role": "user",
                                            "parts": [
                                                {
                                                    "function_response": {
                                                        "id": "call-1",
                                                        "name": "update_extraction_context",
                                                        "response": {
                                                            "status": "success",
                                                            "immediate_goal": "Inspect the saved fixture boundary.",
                                                        },
                                                    }
                                                }
                                            ]
                                        },
                                    },
                                ]
                            },
                            "final_response": {"role": "model", "parts": [{"text": final_text}]},
                        },
                        "eval_metric_results": [tool_metric, final_metric, hallucination_metric],
                    }
                ],
            }
        ],
    }


def _sample_result_with_failed_immediate_goal() -> dict:
    result = _sample_result(final_text="Boundary count is done; next step is writing outputs.")
    failed_tool_metric = _metric(
        "rubric_based_tool_use_quality_v1",
        0.5,
        2,
        [
            {
                "rubric_id": "records_immediate_goal_before_producer",
                "score": 0.0,
                "rationale": (
                    "The trace does not show the required immediate goal before producer work. "
                    "Trace anchors: invocation_events[2] is the producer-output intent and "
                    "invocation_events[0] is the earlier generic immediate_goal."
                ),
            }
        ],
    )
    invocation = result["eval_case_results"][0]["eval_metric_result_per_invocation"][0]
    invocation["eval_metric_results"][0] = failed_tool_metric
    result["eval_case_results"][0]["overall_eval_metric_results"][0] = failed_tool_metric
    invocation["actual_invocation"]["intermediate_data"]["invocation_events"].extend(
        [
            {
                "author": "job_listing_scout",
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "function_call": {
                                "id": "call-2",
                                "name": "update_extraction_context",
                                "args": {
                                    "immediate_goal": (
                                        "Write protocol outputs from the current evidence basis."
                                    ),
                                    "planned_next_tool": {
                                        "tool_name": "run_skill_script",
                                        "file_path": "scripts/sandbox_write_file.py",
                                    },
                                },
                            }
                        }
                    ]
                },
            }
        ]
    )
    return result


def test_dashboard_data_parses_source_bound_runs_events_and_flags(tmp_path: Path) -> None:
    result_path = tmp_path / "result.evalset_result.json"
    result_path.write_text(json.dumps(_sample_result()), encoding="utf-8")

    data = adk_eval_dashboard_utils.build_dashboard_data([tmp_path], project_root=tmp_path)

    assert data["run_count"] == 1
    run = data["runs"][0]
    assert run["source_file"] == "result.evalset_result.json"
    assert run["short_id"] == "1778808199.500643"
    assert [event["kind"] for event in run["events"]] == [
        "user_message",
        "function_call",
        "function_response",
        "final_response",
        "metric_result",
        "metric_result",
        "metric_result",
    ]
    assert {flag["source_key"] for flag in run["flags"]} == {
        "rubric_based_final_response_quality_v1:reports_boundary_evidence",
        "hallucinations_v1",
    }
    assert run["trace_summary"]["not_evaluated_metrics"] == ["hallucinations_v1"]
    assert run["trace_summary"]["tool_call_counts"] == {"update_extraction_context": 1}
    assert run["events"][0]["author"] == "user"
    assert run["events"][1]["author"] == "job_listing_scout"
    assert run["events"][2]["role"] == "user"
    assert run["events"][4]["author"] == "ADK eval"
    assert run["events"][2]["summary"] == (
        "status success; Inspect the saved fixture boundary."
    )


def test_failed_rubric_links_judge_authored_trace_anchors(tmp_path: Path) -> None:
    result_path = tmp_path / "result.evalset_result.json"
    result_path.write_text(json.dumps(_sample_result_with_failed_immediate_goal()), encoding="utf-8")

    run = adk_eval_dashboard_utils.build_dashboard_data([result_path], project_root=tmp_path)["runs"][0]
    flag = [
        item
        for item in run["flags"]
        if item["rubric_id"] == "records_immediate_goal_before_producer"
    ][0]

    assert [citation["title"] for citation in flag["judge_citations"]] == [
        "update_extraction_context",
        "update_extraction_context",
    ]
    assert flag["judge_citations"][0]["reason"] == "Judge-cited invocation_events[2]."


def test_metric_event_carries_judge_rationale_and_source_path(tmp_path: Path) -> None:
    result_path = tmp_path / "result.evalset_result.json"
    result_path.write_text(json.dumps(_sample_result()), encoding="utf-8")

    run = adk_eval_dashboard_utils.build_dashboard_data([result_path], project_root=tmp_path)["runs"][0]
    final_metric_event = [
        event
        for event in run["events"]
        if event["kind"] == "metric_result"
        and event["metric"]["metric_name"] == "rubric_based_final_response_quality_v1"
    ][0]

    rubric = final_metric_event["metric"]["rubric_scores"][0]

    assert rubric["rubric_id"] == "reports_boundary_evidence"
    assert rubric["rationale"] == "The final response omitted the repeated unit selector."
    assert rubric["rubric_text"] == "Definition for reports_boundary_evidence."
    assert rubric["source_path"].endswith(
        "eval_metric_result_per_invocation[0].eval_metric_results[1].details.rubric_scores[0]"
    )


def test_render_dashboard_html_escapes_embedded_json_script_breakout(tmp_path: Path) -> None:
    result_path = tmp_path / "result.evalset_result.json"
    result_path.write_text(
        json.dumps(_sample_result(final_text="</script><span>not markup</span>")),
        encoding="utf-8",
    )
    data = adk_eval_dashboard_utils.build_dashboard_data([result_path], project_root=tmp_path)

    rendered = adk_eval_dashboard_utils.render_dashboard_html(data)

    assert "</script><span>not markup</span>" not in rendered
    assert "\\u003c/script>" in rendered
    assert "window.__ADK_EVAL_DASHBOARD_DATA__" in rendered
    assert "collapsible-table" in rendered
    assert "Judge rationale table" in rendered
    assert "Metric bottleneck table" in rendered
    assert "adk-web-ui" in rendered
    assert "adk-web-appbar" in rendered
    assert "Agent Development Kit" in rendered
    assert "New Eval Session" in rendered
    assert "select-run-dropdown" in rendered
    assert "adk-web-body trace-inspector" in rendered
    assert "@media (min-width: 641px) and (max-width: 1100px)" in rendered
    assert ".adk-web-body.trace-inspector" in rendered
    assert "height: calc(100vh - 82px)" in rendered
    assert "max-height: calc(100vh - 82px)" in rendered
    assert "height: calc(100vh - 166px)" in rendered
    assert "max-height: calc(100vh - 166px)" in rendered
    assert "grid-template-columns: minmax(0, 1fr) minmax(300px, 36vw)" in rendered
    assert "grid-template-rows: 64px minmax(0, 1fr)" in rendered
    assert "grid-template-columns: minmax(0, 1fr) auto" in rendered
    assert ".adk-web-appbar .adk-web-selector" in rendered
    assert "height: calc(100vh - 64px)" in rendered
    assert "height: calc(100vh - 124px)" in rendered
    assert "min-height: 58px" in rendered
    assert "-webkit-line-clamp: 2" in rendered
    assert "adk-web-event-sidebar trace-event-rail" in rendered
    assert "adk-web-segmented" in rendered
    assert "Events" in rendered
    assert "Traces" in rendered
    assert "+ Filter" in rendered
    assert "class=\"adk-event-row event-item ${isSelected ? \"is-selected\" : \"\"}\"" in rendered
    assert "trace-inspector" in rendered
    assert "trace-detail-panel" in rendered
    assert "trace-event-rail" in rendered
    assert "Event Metadata" in rendered
    assert "Function Calls" in rendered
    assert "Function Responses" in rendered
    assert "User Message" in rendered
    assert "Final Response" in rendered
    assert "Eval Metric Result" in rendered
    assert "renderSelectedEventDetail(event, run)" in rendered
    assert "renderAdkWebAppbar(run)" in rendered
    assert "renderAdkWebTabs()" in rendered
    assert "renderAdkWebEventRail(run)" in rendered
    assert "document.body.classList.toggle(\"adk-web-mode\", state.view === \"trace\")" in rendered
    assert "event-meta-table" in rendered
    assert "Payload from ADK output" in rendered
    assert "prev-event" in rendered
    assert "next-event" in rendered
    assert "adk-event-index" in rendered
    assert "adk-event-chip" in rendered
    assert "adk-event-rail-summary" in rendered
    assert "event-summary-text" in rendered
    assert "ADK FunctionCall" in rendered
    assert "ADK FunctionResponse" in rendered
    assert "Matched call arguments" in rendered
    assert "Response fields with values" in rendered
    assert "Raw matched function_call JSON" in rendered
    assert "matchingFunctionCall(event, run)" in rendered
    assert "compactStructuredValue(response)" in rendered
    assert "function renderStructuredValue(value, depth = 0)" in rendered
    assert 'const rowClass = `json-field${depth > 0 ? " is-nested" : ""}`' in rendered
    assert ".json-field.is-nested" in rendered
    assert ".json-field.is-nested > .json-value" in rendered
    assert "Raw function_response JSON" in rendered
    assert "renderAdkFunctionResponsePayload(event, run)" in rendered
    assert "renderEventMetricDetails(event, run)" in rendered
    assert "renderEventFullPayload(event, run)" in rendered
    assert "data-event-id=\"${esc(item.event_id)}\"" in rendered
    assert "aria-selected=\"${isSelected ? \"true\" : \"false\"}\"" in rendered
    assert "${isSelected ? \"open\" : \"\"}" not in rendered
    assert "document.addEventListener(\"toggle\"" not in rendered
    assert "data-action=\"toggle-event\"" not in rendered
    assert "trace-run-strip" not in rendered
    assert "trace-events-panel" not in rendered
    assert "Select an event." not in rendered
    assert "renderListControl(eventListId, run.events.length, \"events\")" not in rendered
    assert "Show all ${total}" in rendered
    assert "event-page-control" not in rendered
    assert ".adk-web-event-list.event-list" in rendered
    assert "overflow-y: auto" in rendered
    assert "overscroll-behavior: contain" in rendered
    assert "min-height: 82px" in rendered
    assert "max-height: none" in rendered
    assert "max-height: min(460px, 56vh)" in rendered
