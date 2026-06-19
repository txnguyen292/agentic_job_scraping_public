"""Helpers for parsing ADK eval output and rendering the dashboard."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger


DEFAULT_INPUT = Path("src/.adk/eval_history")
TEMPLATE_PATH = Path(__file__).with_name("adk_eval_dashboard.template.html")
DASHBOARD_DATA_PLACEHOLDER = "__DASHBOARD_DATA__"
RESULT_GLOB = "*.evalset_result.json"
STATUS_LABELS = {
    0: "unknown",
    1: "passed",
    2: "failed",
    3: "not evaluated",
}
EVENT_VALUE_KEYS = (
    "function_call",
    "function_response",
    "tool_call",
    "tool_response",
    "text",
    "executable_code",
    "code_execution_result",
    "file_data",
    "inline_data",
)


def status_label(raw_status: Any) -> str:
    """Return ADK eval status text without inventing additional states."""
    try:
        return STATUS_LABELS.get(int(raw_status), f"status {raw_status}")
    except (TypeError, ValueError):
        return "unknown" if raw_status is None else str(raw_status)


def metric_passed(metric: dict[str, Any]) -> bool:
    score = metric.get("score")
    threshold = metric.get("threshold")
    if score is not None and threshold is not None:
        return float(score) >= float(threshold)
    return status_label(metric.get("eval_status")) == "passed"


def rubric_failed(rubric: dict[str, Any]) -> bool:
    score = rubric.get("score")
    if score is None:
        return False
    return float(score) < 1.0


def truncate(value: str, limit: int = 360) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}..."


def part_kind(part: dict[str, Any]) -> tuple[str, Any] | None:
    for key in EVENT_VALUE_KEYS:
        if key in part and part[key] is not None:
            return key, part[key]
    return None


def content_text(content: dict[str, Any] | None) -> str:
    if not content:
        return ""
    texts = []
    for part in content.get("parts") or []:
        text = part.get("text")
        if text:
            texts.append(text)
    return "\n".join(texts)


def metric_source_key(metric_name: str, rubric_id: str | None = None) -> str:
    return f"{metric_name}:{rubric_id}" if rubric_id else metric_name


def display_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(project_root.resolve()))
    except ValueError:
        return str(resolved)


def source_path(*parts: str) -> str:
    return ".".join(part for part in parts if part)


def event_title(kind: str, payload: Any) -> str:
    if kind in {"function_call", "function_response", "tool_call", "tool_response"} and isinstance(payload, dict):
        return str(payload.get("name") or kind)
    if kind == "metric_result" and isinstance(payload, dict):
        return str(payload.get("metric_name") or kind)
    if kind == "final_response":
        return "Final response"
    if kind == "user_message":
        return "User prompt"
    if kind == "text":
        return "Model text"
    return kind.replace("_", " ")


def event_status(kind: str, payload: Any) -> str | None:
    if kind == "metric_result" and isinstance(payload, dict):
        return status_label(payload.get("eval_status"))
    if kind == "function_response" and isinstance(payload, dict):
        response = payload.get("response")
        if isinstance(response, dict) and response.get("status") is not None:
            return str(response["status"])
    if kind == "function_call" and isinstance(payload, dict):
        args = payload.get("args")
        if isinstance(args, dict) and args.get("status") is not None:
            return str(args["status"])
    return None


def payload_summary(kind: str, payload: Any) -> str:
    if kind == "user_message" and isinstance(payload, dict):
        return truncate(content_text(payload), 240)
    if kind == "final_response" and isinstance(payload, dict):
        return truncate(content_text(payload), 320)
    if kind == "function_call" and isinstance(payload, dict):
        args = payload.get("args")
        if isinstance(args, dict):
            for key in ("immediate_goal", "task_understanding", "final_goal", "status"):
                if args.get(key):
                    return truncate(str(args[key]), 260)
        return truncate(json.dumps(payload, ensure_ascii=False), 260)
    if kind == "function_response" and isinstance(payload, dict):
        response = payload.get("response")
        if isinstance(response, dict):
            for key in ("immediate_goal", "status", "context_state"):
                if response.get(key):
                    return truncate(str(response[key]), 260)
        return truncate(json.dumps(payload, ensure_ascii=False), 260)
    if kind == "metric_result" and isinstance(payload, dict):
        score = payload.get("score")
        threshold = payload.get("threshold")
        label = status_label(payload.get("eval_status"))
        if score is None:
            return f"{label}; score was not produced by ADK."
        return f"{label}; score {score} / threshold {threshold}."
    if isinstance(payload, str):
        return truncate(payload, 260)
    return truncate(json.dumps(payload, ensure_ascii=False), 260)


def extract_rubric_definitions(metric: dict[str, Any]) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for rubric in (metric.get("criterion") or {}).get("rubrics") or []:
        rubric_id = rubric.get("rubric_id")
        if not rubric_id:
            continue
        rubric_content = rubric.get("rubric_content") or {}
        definitions[str(rubric_id)] = str(rubric_content.get("text_property") or "")
    return definitions


def extract_metric(metric: dict[str, Any], metric_path: str) -> dict[str, Any]:
    definitions = extract_rubric_definitions(metric)
    rubric_scores = []
    for index, rubric in enumerate((metric.get("details") or {}).get("rubric_scores") or []):
        rubric_id = str(rubric.get("rubric_id") or "")
        rubric_scores.append(
            {
                "rubric_id": rubric_id,
                "score": rubric.get("score"),
                "rationale": rubric.get("rationale") or "",
                "rubric_text": definitions.get(rubric_id, ""),
                "source_path": source_path(metric_path, "details", f"rubric_scores[{index}]"),
                "passed": not rubric_failed(rubric),
            }
        )

    extracted = {
        "metric_name": metric.get("metric_name") or "",
        "score": metric.get("score"),
        "threshold": metric.get("threshold"),
        "status": status_label(metric.get("eval_status")),
        "eval_status": metric.get("eval_status"),
        "source_path": metric_path,
        "rubric_scores": rubric_scores,
        "criterion": metric.get("criterion") or {},
        "details": metric.get("details") or {},
        "passed": metric_passed(metric),
    }
    return extracted


def event_ref(event: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "order": event["order"],
        "kind": event["kind"],
        "title": event["title"],
        "status": event.get("status"),
        "summary": event.get("summary") or "",
        "source_path": event.get("source_path") or "",
        "reason": reason,
    }


def events_for_invocation(events: list[dict[str, Any]], invocation_index: int | None) -> list[dict[str, Any]]:
    if invocation_index is None:
        return [event for event in events if event["kind"] != "metric_result"]
    return [
        event
        for event in events
        if event["kind"] != "metric_result" and event.get("invocation_index") == invocation_index
    ]


TRACE_EVENT_REF_PATTERN = re.compile(
    r"invocation_events\[(?P<raw_index>\d+)\]|\bE(?P<event_order>\d+)\b|\bevent\s+#?(?P<named_order>\d+)\b",
    re.IGNORECASE,
)


def events_by_raw_invocation_index(events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for event in events:
        match = re.search(r"invocation_events\[(\d+)\]", event.get("source_path") or "")
        if match:
            indexed[int(match.group(1))] = event
    return indexed


def extract_judge_citations(flag: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rationale = flag.get("rationale") or ""
    scoped = events_for_invocation(events, flag.get("invocation_index"))
    by_order = {event["order"]: event for event in scoped}
    by_raw_index = events_by_raw_invocation_index(scoped)
    citations: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()

    for match in TRACE_EVENT_REF_PATTERN.finditer(rationale):
        if match.group("raw_index") is not None:
            event = by_raw_index.get(int(match.group("raw_index")))
            label = f"Judge-cited invocation_events[{match.group('raw_index')}]."
        else:
            order = match.group("event_order") or match.group("named_order")
            event = by_order.get(int(order)) if order is not None else None
            label = f"Judge-cited event {order}."
        if event and event["event_id"] not in seen_event_ids:
            citations.append(event_ref(event, label))
            seen_event_ids.add(event["event_id"])

    if "final_response" in rationale and not any(event["kind"] == "final_response" for event in citations):
        for event in scoped:
            if event["kind"] == "final_response" and event["event_id"] not in seen_event_ids:
                citations.append(event_ref(event, "Judge-cited final_response."))
                seen_event_ids.add(event["event_id"])

    return citations


def attach_judge_citations(events: list[dict[str, Any]], flags: list[dict[str, Any]]) -> None:
    for flag in flags:
        flag["judge_citations"] = extract_judge_citations(flag, events)


def flags_from_metric(metric: dict[str, Any], invocation_index: int | None) -> list[dict[str, Any]]:
    flags = []
    failed_rubrics = [rubric for rubric in metric["rubric_scores"] if not rubric["passed"]]
    for rubric in failed_rubrics:
        flags.append(
            {
                "type": "rubric",
                "metric_name": metric["metric_name"],
                "rubric_id": rubric["rubric_id"],
                "score": rubric["score"],
                "threshold": metric["threshold"],
                "status": metric["status"],
                "rationale": rubric["rationale"],
                "rubric_text": rubric["rubric_text"],
                "source_path": rubric["source_path"],
                "invocation_index": invocation_index,
                "source_key": metric_source_key(metric["metric_name"], rubric["rubric_id"]),
            }
        )

    if metric["passed"] or failed_rubrics:
        return flags

    flags.append(
        {
            "type": "metric",
            "metric_name": metric["metric_name"],
            "rubric_id": None,
            "score": metric["score"],
            "threshold": metric["threshold"],
            "status": metric["status"],
            "rationale": "",
            "rubric_text": "",
            "source_path": metric["source_path"],
            "invocation_index": invocation_index,
            "source_key": metric_source_key(metric["metric_name"]),
        }
    )
    return flags


def make_event(
    *,
    run_id: str,
    order: int,
    kind: str,
    payload: Any,
    source: str,
    invocation_index: int | None,
    metric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = event_title(kind, payload)
    status = event_status(kind, payload)
    return {
        "event_id": f"{run_id}:event:{order}",
        "order": order,
        "kind": kind,
        "title": title,
        "status": status,
        "summary": payload_summary(kind, payload),
        "source_path": source,
        "invocation_index": invocation_index,
        "payload": payload,
        "metric": metric,
    }


def extract_invocation_events(
    *,
    run_id: str,
    actual_invocation: dict[str, Any],
    invocation_index: int,
    invocation_path: str,
    start_order: int,
) -> tuple[list[dict[str, Any]], int]:
    events = []
    order = start_order
    user_content = actual_invocation.get("user_content")
    if user_content:
        events.append(
            make_event(
                run_id=run_id,
                order=order,
                kind="user_message",
                payload=user_content,
                source=source_path(invocation_path, "actual_invocation", "user_content"),
                invocation_index=invocation_index,
            )
        )
        order += 1

    raw_events = (
        (actual_invocation.get("intermediate_data") or {}).get("invocation_events") or []
    )
    for event_index, raw_event in enumerate(raw_events):
        parts = (raw_event.get("content") or {}).get("parts") or []
        for part_index, part in enumerate(parts):
            detected = part_kind(part)
            if detected is None:
                payload = part
                kind = "unknown_part"
            else:
                kind, payload = detected
            events.append(
                make_event(
                    run_id=run_id,
                    order=order,
                    kind=kind,
                    payload=payload,
                    source=source_path(
                        invocation_path,
                        "actual_invocation",
                        "intermediate_data",
                        f"invocation_events[{event_index}]",
                        "content",
                        f"parts[{part_index}]",
                        kind if detected else "",
                    ),
                    invocation_index=invocation_index,
                )
            )
            order += 1

    final_response = actual_invocation.get("final_response")
    if final_response:
        events.append(
            make_event(
                run_id=run_id,
                order=order,
                kind="final_response",
                payload=final_response,
                source=source_path(invocation_path, "actual_invocation", "final_response"),
                invocation_index=invocation_index,
            )
        )
        order += 1

    return events, order


def extract_trace(
    *,
    run_id: str,
    case: dict[str, Any],
    case_path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    all_events: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    order = 0

    for invocation_index, invocation in enumerate(case.get("eval_metric_result_per_invocation") or []):
        invocation_path = source_path(
            case_path,
            f"eval_metric_result_per_invocation[{invocation_index}]",
        )
        actual = invocation.get("actual_invocation") or {}
        events, order = extract_invocation_events(
            run_id=run_id,
            actual_invocation=actual,
            invocation_index=invocation_index,
            invocation_path=invocation_path,
            start_order=order,
        )
        all_events.extend(events)

        invocation_metrics = []
        for metric_index, raw_metric in enumerate(invocation.get("eval_metric_results") or []):
            metric_path = source_path(invocation_path, f"eval_metric_results[{metric_index}]")
            metric = extract_metric(raw_metric, metric_path)
            invocation_metrics.append(metric)
            flags.extend(flags_from_metric(metric, invocation_index))
            all_events.append(
                make_event(
                    run_id=run_id,
                    order=order,
                    kind="metric_result",
                    payload=raw_metric,
                    source=metric_path,
                    invocation_index=invocation_index,
                    metric=metric,
                )
            )
            order += 1

        invocations.append(
            {
                "invocation_index": invocation_index,
                "invocation_id": actual.get("invocation_id") or "",
                "user_text": content_text(actual.get("user_content")),
                "final_response_text": content_text(actual.get("final_response")),
                "metrics": invocation_metrics,
                "source_path": invocation_path,
            }
        )

    return all_events, invocations, flags


def summarize_trace(events: list[dict[str, Any]], flags: list[dict[str, Any]]) -> dict[str, Any]:
    tool_calls = [event["title"] for event in events if event["kind"] in {"function_call", "tool_call"}]
    metric_events = [event for event in events if event["kind"] == "metric_result"]
    not_evaluated = [
        event["title"]
        for event in metric_events
        if event.get("metric") and event["metric"]["status"] == "not evaluated"
    ]
    failed_metrics = [
        event["title"]
        for event in metric_events
        if event.get("metric") and not event["metric"]["passed"]
    ]
    failed_rubrics = [
        flag["rubric_id"]
        for flag in flags
        if flag["type"] == "rubric" and flag.get("rubric_id")
    ]
    return {
        "event_count": len(events),
        "agent_event_count": len([event for event in events if event["kind"] != "metric_result"]),
        "metric_event_count": len(metric_events),
        "flag_count": len(flags),
        "tool_call_counts": dict(Counter(tool_calls)),
        "failed_metrics": sorted(set(failed_metrics)),
        "not_evaluated_metrics": sorted(set(not_evaluated)),
        "failed_rubrics": sorted(set(failed_rubrics)),
    }


def parse_result_file(path: Path, project_root: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rel_path = display_path(path, project_root)
    result_id = payload.get("eval_set_result_id") or path.stem
    runs = []

    for case_index, case in enumerate(payload.get("eval_case_results") or []):
        case_path = f"eval_case_results[{case_index}]"
        eval_id = case.get("eval_id") or ""
        run_id = f"{result_id}:{case_index}"
        overall_metrics = [
            extract_metric(raw_metric, source_path(case_path, f"overall_eval_metric_results[{metric_index}]"))
            for metric_index, raw_metric in enumerate(case.get("overall_eval_metric_results") or [])
        ]
        events, invocations, invocation_flags = extract_trace(
            run_id=run_id,
            case=case,
            case_path=case_path,
        )
        flags = invocation_flags[:]
        if not flags:
            for metric in overall_metrics:
                flags.extend(flags_from_metric(metric, None))
        attach_judge_citations(events, flags)

        created_at = payload.get("creation_timestamp")
        run = {
            "id": run_id,
            "result_id": result_id,
            "short_id": str(result_id).rsplit("_", maxsplit=1)[-1],
            "eval_set_id": payload.get("eval_set_id") or "",
            "eval_id": eval_id,
            "case_index": case_index,
            "source_file": rel_path,
            "created_at": created_at,
            "created_at_iso": timestamp_to_iso(created_at),
            "status": status_label(case.get("final_eval_status")),
            "final_eval_status": case.get("final_eval_status"),
            "metrics": overall_metrics,
            "events": events,
            "invocations": invocations,
            "flags": flags,
            "trace_summary": summarize_trace(events, flags),
        }
        runs.append(run)

    return runs


def timestamp_to_iso(raw_timestamp: Any) -> str:
    if raw_timestamp is None:
        return ""
    try:
        value = float(raw_timestamp)
    except (TypeError, ValueError):
        return str(raw_timestamp)
    return dt.datetime.fromtimestamp(value, tz=dt.UTC).isoformat()


def iter_result_files(inputs: list[Path] | None, *, limit: int | None = None) -> list[Path]:
    search_inputs = inputs or [DEFAULT_INPUT]
    files: list[Path] = []
    for raw_path in search_inputs:
        path = raw_path.expanduser()
        if path.is_dir():
            files.extend(path.rglob(RESULT_GLOB))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"ADK eval input does not exist: {path}")

    unique_files = sorted({file.resolve() for file in files}, key=lambda item: item.stat().st_mtime, reverse=True)
    if limit is not None:
        unique_files = unique_files[:limit]
    return unique_files


def build_clusters(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metric_clusters: dict[str, dict[str, Any]] = {}
    rubric_clusters: dict[str, dict[str, Any]] = {}
    eval_counts = Counter(run["eval_id"] for run in runs)
    status_counts = Counter(run["status"] for run in runs)

    for run in runs:
        for metric in run["metrics"]:
            name = metric["metric_name"]
            cluster = metric_clusters.setdefault(
                name,
                {
                    "metric_name": name,
                    "run_count": 0,
                    "failed_count": 0,
                    "not_evaluated_count": 0,
                    "scores": [],
                    "threshold": metric.get("threshold"),
                    "run_ids": [],
                },
            )
            cluster["run_count"] += 1
            cluster["run_ids"].append(run["id"])
            if metric.get("score") is not None:
                cluster["scores"].append(metric["score"])
            if metric["status"] == "not evaluated":
                cluster["not_evaluated_count"] += 1
            if not metric["passed"]:
                cluster["failed_count"] += 1

        for flag in run["flags"]:
            key = flag["source_key"]
            cluster = rubric_clusters.setdefault(
                key,
                {
                    "source_key": key,
                    "metric_name": flag["metric_name"],
                    "rubric_id": flag.get("rubric_id"),
                    "type": flag["type"],
                    "occurrence_count": 0,
                    "runs": [],
                },
            )
            cluster["occurrence_count"] += 1
            cluster["runs"].append(
                {
                    "run_id": run["id"],
                    "short_id": run["short_id"],
                    "score": flag.get("score"),
                    "status": flag.get("status"),
                    "rationale": flag.get("rationale"),
                    "source_path": flag.get("source_path"),
                    "judge_citations": flag.get("judge_citations"),
                }
            )

    for cluster in metric_clusters.values():
        scores = cluster.pop("scores")
        cluster["average_score"] = sum(scores) / len(scores) if scores else None

    sorted_metric_clusters = sorted(
        metric_clusters.values(),
        key=lambda item: (item["failed_count"], item["not_evaluated_count"], item["run_count"]),
        reverse=True,
    )
    sorted_rubric_clusters = sorted(
        rubric_clusters.values(),
        key=lambda item: item["occurrence_count"],
        reverse=True,
    )
    return {
        "metric_clusters": sorted_metric_clusters,
        "rubric_clusters": sorted_rubric_clusters,
        "eval_counts": dict(eval_counts),
        "status_counts": dict(status_counts),
    }


def build_dashboard_data(
    inputs: list[Path] | None = None,
    *,
    limit: int | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    root = project_root or Path.cwd()
    files = iter_result_files(inputs, limit=limit)
    runs: list[dict[str, Any]] = []
    for path in files:
        try:
            runs.extend(parse_result_file(path, root))
        except Exception as exc:
            logger.exception("Failed to parse {}", path)
            raise ValueError(f"Failed to parse ADK eval result {path}: {exc}") from exc

    runs.sort(key=lambda run: run.get("created_at") or 0, reverse=True)
    return {
        "generated_at": dt.datetime.now(tz=dt.UTC).isoformat(),
        "inputs": [str(path) for path in (inputs or [DEFAULT_INPUT])],
        "source_files": [display_path(path, root) for path in files],
        "run_count": len(runs),
        "runs": runs,
        "clusters": build_clusters(runs),
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
