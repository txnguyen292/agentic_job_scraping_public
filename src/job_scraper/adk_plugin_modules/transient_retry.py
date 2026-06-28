from __future__ import annotations

import asyncio
import inspect
import os
import re
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin


MODEL_RETRY_MAX_ATTEMPTS = int(os.getenv("JOB_SCRAPER_MODEL_RETRY_MAX_ATTEMPTS", "6"))
MODEL_RETRY_BASE_DELAY_SECONDS = float(os.getenv("JOB_SCRAPER_MODEL_RETRY_BASE_DELAY_SECONDS", "15"))
MODEL_RETRY_MAX_DELAY_SECONDS = float(os.getenv("JOB_SCRAPER_MODEL_RETRY_MAX_DELAY_SECONDS", "90"))


class TransientModelRetryPlugin(BasePlugin):
    """Retry transient model provider failures before ADK Web surfaces them."""

    def __init__(
        self,
        *,
        max_attempts: int = MODEL_RETRY_MAX_ATTEMPTS,
        base_delay_seconds: float = MODEL_RETRY_BASE_DELAY_SECONDS,
        max_delay_seconds: float = MODEL_RETRY_MAX_DELAY_SECONDS,
        sleep: Any = asyncio.sleep,
        name: str = "transient_model_retry_plugin",
    ) -> None:
        super().__init__(name=name)
        self.max_attempts = max(1, max_attempts)
        self.base_delay_seconds = max(0.0, base_delay_seconds)
        self.max_delay_seconds = max(self.base_delay_seconds, max_delay_seconds)
        self.sleep = sleep

    async def on_model_error_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
        error: Exception,
    ) -> LlmResponse | None:
        if not _is_transient_model_error(error):
            return None

        invocation_context = getattr(callback_context, "_invocation_context", None)
        agent = getattr(invocation_context, "agent", None)
        model = getattr(agent, "model", None)
        if model is None or not callable(getattr(model, "generate_content_async", None)):
            return _model_retry_exhausted_response(
                error,
                attempts=0,
                detail="No retryable model object was available in the ADK invocation context.",
            )

        last_error: Exception = error
        retry_attempts = max(0, self.max_attempts - 1)
        for attempt in range(1, retry_attempts + 1):
            delay_seconds = self._delay_for_attempt(attempt, last_error)
            await self._sleep(delay_seconds)
            try:
                increment = getattr(invocation_context, "increment_llm_call_count", None)
                if callable(increment):
                    increment()
                async for response in model.generate_content_async(
                    llm_request,
                    stream=False,
                ):
                    return response
            except Exception as exc:
                last_error = exc
                if not _is_transient_model_error(exc):
                    return None

        return _model_retry_exhausted_response(
            last_error,
            attempts=retry_attempts,
            detail=(
                "Transient model provider errors persisted after bounded retries. "
                "Retry the user request later or lower request volume."
            ),
        )

    async def _sleep(self, delay_seconds: float) -> None:
        maybe = self.sleep(delay_seconds)
        if inspect.isawaitable(maybe):
            await maybe

    def _delay_for_attempt(self, attempt: int, error: Exception) -> float:
        hinted_delay = _retry_delay_from_error(error)
        fallback = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempt - 1)))
        if hinted_delay is None:
            return fallback
        return min(self.max_delay_seconds, max(self.base_delay_seconds, hinted_delay))


def _is_transient_model_error(error: Exception) -> bool:
    error_text = f"{type(error).__name__}: {error}".lower()
    transient_markers = (
        "rate limit",
        "rate_limit",
        "ratelimit",
        "tokens per min",
        "token per min",
        "tpm",
        "too many requests",
        "429",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "service unavailable",
        "overloaded",
        "connection",
        "server error",
        "internal server error",
        "500",
        "502",
        "503",
        "504",
    )
    if any(marker in error_text for marker in transient_markers):
        return True

    transient_class_markers = (
        "ratelimit",
        "timeout",
        "apiconnection",
        "internalserver",
        "serviceunavailable",
        "apistatus",
    )
    class_name = type(error).__name__.lower()
    return any(marker in class_name for marker in transient_class_markers)


def _retry_delay_from_error(error: Exception) -> float | None:
    error_text = str(error)
    patterns = (
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|minutes?)",
        r"retry[- ]after[:=]?\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|minutes?)?",
    )
    for pattern in patterns:
        match = re.search(pattern, error_text, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        if unit.startswith("ms") or unit.startswith("millisecond"):
            return value / 1000
        if unit.startswith("m") and not unit.startswith("ms"):
            return value * 60
        return value
    retry_after = getattr(error, "retry_after", None)
    if isinstance(retry_after, int | float):
        return float(retry_after)
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            value = headers.get("retry-after")
        except AttributeError:
            value = None
        if value:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _model_retry_exhausted_response(error: Exception, *, attempts: int, detail: str) -> LlmResponse:
    return LlmResponse(
        error_code="MODEL_RETRY_EXHAUSTED",
        error_message=(
            f"{detail} retry_attempts={attempts}; last_error_type={type(error).__name__}; "
            f"last_error={_preview(str(error), 500)}"
        ),
    )


def _preview(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = "...[truncated]..."
    if max_chars <= len(marker):
        return text[:max_chars]
    head = (max_chars - len(marker)) // 2
    tail = max_chars - len(marker) - head
    return f"{text[:head]}{marker}{text[-tail:]}"
