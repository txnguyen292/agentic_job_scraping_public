"""Load ADK-shaped continuation eval traces from JSON fixtures."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


TRACE_FIXTURE_PATH = Path(__file__).with_name("continuation_eval_adk_traces.json")

_UTILS_PATH = Path(__file__).resolve().parents[2] / "scripts" / "utils.py"
_UTILS_SPEC = importlib.util.spec_from_file_location("adk_eval_dashboard_utils_for_trace_loader", _UTILS_PATH)
if _UTILS_SPEC is None or _UTILS_SPEC.loader is None:
    raise RuntimeError(f"Unable to load ADK dashboard utils from {_UTILS_PATH}")
_UTILS = importlib.util.module_from_spec(_UTILS_SPEC)
_UTILS_SPEC.loader.exec_module(_UTILS)


def load_actual_invocation(name: str) -> dict[str, Any]:
    traces = json.loads(TRACE_FIXTURE_PATH.read_text(encoding="utf-8"))
    try:
        return traces[name]
    except KeyError as exc:
        known = ", ".join(sorted(traces))
        raise ValueError(f"Unknown continuation eval trace fixture {name!r}. Known fixtures: {known}") from exc


def dashboard_events_from_actual_invocation(invocation: dict[str, Any]) -> list[dict[str, Any]]:
    events, _ = _UTILS.extract_invocation_events(
        run_id="age26-fixture",
        actual_invocation=invocation,
        invocation_index=0,
        invocation_path="fixture.eval_metric_result_per_invocation[0]",
        start_order=0,
    )
    return events
