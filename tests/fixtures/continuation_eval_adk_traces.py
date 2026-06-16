"""Named accessors for JSON-backed ADK trace fixtures."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


_LOADER_PATH = Path(__file__).with_name("continuation_eval_trace_loader.py")
_LOADER_SPEC = importlib.util.spec_from_file_location("continuation_eval_trace_loader", _LOADER_PATH)
if _LOADER_SPEC is None or _LOADER_SPEC.loader is None:
    raise RuntimeError(f"Unable to load continuation eval trace loader from {_LOADER_PATH}")
loader = importlib.util.module_from_spec(_LOADER_SPEC)
sys.modules[_LOADER_SPEC.name] = loader
_LOADER_SPEC.loader.exec_module(loader)


def dashboard_events_from_actual_invocation(invocation: dict[str, Any]) -> list[dict[str, Any]]:
    return loader.dashboard_events_from_actual_invocation(invocation)


def optimal_adk_invocation() -> dict[str, Any]:
    return loader.load_actual_invocation("optimal_gold_run")


def good_non_exact_adk_invocation() -> dict[str, Any]:
    return loader.load_actual_invocation("good_non_exact_run")


def neutral_partial_progress_adk_invocation() -> dict[str, Any]:
    return loader.load_actual_invocation("neutral_partial_progress_run")


def bad_no_verified_output_adk_invocation() -> dict[str, Any]:
    return loader.load_actual_invocation("bad_no_verified_output_run")


def bad_premature_finalization_adk_invocation() -> dict[str, Any]:
    return loader.load_actual_invocation("bad_premature_finalization_run")

