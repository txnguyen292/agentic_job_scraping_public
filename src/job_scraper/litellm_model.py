from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
import os
from typing import Any

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


DEFAULT_MODEL_TIMEOUT_SECONDS = float(os.getenv("JOB_SCRAPER_MODEL_TIMEOUT_SECONDS", "120"))
REASONING_EFFORT_ENV = "JOB_SCRAPER_REASONING_EFFORT"
REASONING_SUMMARY_ENV = "JOB_SCRAPER_REASONING_SUMMARY"
VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "default"}
VALID_REASONING_SUMMARIES = {"auto", "concise", "detailed"}
DISABLED_REASONING_SUMMARY_VALUES = {"", "0", "false", "no", "none", "off", "disabled"}


class SerializableLiteLlm(LiteLlm):
    """LiteLLM wrapper that is serializable and resilient to malformed tool JSON."""

    def __init__(self, model: str, **kwargs: Any) -> None:
        reasoning_effort = _configured_reasoning_effort(model, kwargs.pop("reasoning_effort", None))
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
            kwargs.setdefault("drop_params", True)
        kwargs.setdefault("timeout", DEFAULT_MODEL_TIMEOUT_SECONDS)
        super().__init__(model=model, **kwargs)
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = float(kwargs["timeout"]) if kwargs["timeout"] is not None else None

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = super().model_dump(*args, **kwargs)
        payload.pop("llm_client", None)
        if self._reasoning_effort is not None:
            payload["reasoning_effort"] = self._reasoning_effort
        return payload

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        retried_malformed_tool_json = False
        retried_provider_parse_error = False
        retried_provider_timeout = False
        while True:
            try:
                async for response in _iterate_with_timeout(
                    super().generate_content_async(llm_request, stream=stream),
                    timeout_seconds=self._timeout_seconds,
                ):
                    yield response
                return
            except asyncio.TimeoutError as exc:
                if retried_provider_timeout:
                    yield _provider_timeout_error_response(exc, self.model, self._timeout_seconds)
                    return
                retried_provider_timeout = True
                _append_provider_timeout_retry_message(llm_request, self._timeout_seconds)
            except json.JSONDecodeError as exc:
                if retried_malformed_tool_json:
                    yield _malformed_tool_json_error_response(exc, self.model)
                    return
                retried_malformed_tool_json = True
                _append_malformed_tool_json_retry_message(llm_request, exc)
            except Exception as exc:
                if not _is_provider_json_response_error(exc):
                    raise
                if retried_provider_parse_error:
                    yield _provider_json_response_error_response(exc, self.model)
                    return
                retried_provider_parse_error = True
                _append_provider_json_response_retry_message(llm_request, exc)


def _configured_reasoning_effort(model: str, explicit: str | dict[str, Any] | None) -> str | dict[str, str] | None:
    if isinstance(explicit, dict):
        return explicit
    raw = explicit if explicit is not None else os.getenv(REASONING_EFFORT_ENV)
    if raw is None:
        return None
    cleaned = str(raw).strip().lower()
    if not cleaned:
        return None
    if cleaned not in VALID_REASONING_EFFORTS:
        allowed = ", ".join(sorted(VALID_REASONING_EFFORTS))
        raise ValueError(f"{REASONING_EFFORT_ENV} must be one of: {allowed}")
    reasoning_summary = _configured_reasoning_summary(model)
    if reasoning_summary is not None and cleaned != "default":
        return {"effort": cleaned, "summary": reasoning_summary}
    return cleaned


def _configured_reasoning_summary(model: str) -> str | None:
    raw = os.getenv(REASONING_SUMMARY_ENV, "auto" if _is_openai_reasoning_summary_model(model) else "")
    cleaned = str(raw).strip().lower()
    if cleaned in DISABLED_REASONING_SUMMARY_VALUES:
        return None
    if cleaned not in VALID_REASONING_SUMMARIES:
        allowed = ", ".join(sorted(VALID_REASONING_SUMMARIES | DISABLED_REASONING_SUMMARY_VALUES))
        raise ValueError(f"{REASONING_SUMMARY_ENV} must be one of: {allowed}")
    return cleaned


def _is_openai_reasoning_summary_model(model: str) -> bool:
    cleaned = str(model or "").strip().lower()
    if cleaned.startswith(("openai/", "azure/", "responses/")):
        return True
    if "/" in cleaned:
        return False
    return cleaned.startswith(("gpt-", "o1", "o3", "o4"))


async def _iterate_with_timeout(
    responses: AsyncGenerator[LlmResponse, None],
    *,
    timeout_seconds: float | None,
) -> AsyncGenerator[LlmResponse, None]:
    if timeout_seconds is None or timeout_seconds <= 0:
        async for response in responses:
            yield response
        return

    try:
        while True:
            try:
                yield await asyncio.wait_for(anext(responses), timeout=timeout_seconds)
            except StopAsyncIteration:
                return
    finally:
        await responses.aclose()


def _append_malformed_tool_json_retry_message(llm_request: LlmRequest, exc: json.JSONDecodeError) -> None:
    llm_request.contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "Your previous tool call could not be parsed because its function-call "
                        f"arguments were not valid JSON: {exc.msg} at line {exc.lineno}, "
                        f"column {exc.colno}. Retry the next action now with a valid tool call. "
                        "Do not put literal newline/control characters inside JSON strings. "
                        "For sandbox_exec.py --cmd, use a short one-line command, or write a "
                        "helper file with sandbox_write_file.py and then execute that file."
                    )
                )
            ],
        )
    )


def _malformed_tool_json_error_response(exc: json.JSONDecodeError, model_version: str) -> LlmResponse:
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=(
                        "Model tool-call arguments were still malformed after one retry. "
                        "The workflow should be retried with shorter one-line tool arguments "
                        "or helper-file based sandbox commands."
                    )
                )
            ],
        ),
        model_version=model_version,
    )
    response.error_code = "MALFORMED_TOOL_CALL_JSON"
    response.error_message = f"{exc.msg} at line {exc.lineno}, column {exc.colno}"
    return response


def _is_provider_json_response_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        exc.__class__.__name__ == "APIError"
        and "Unable to get json response" in message
        and "Expecting value" in message
    )


def _append_provider_json_response_retry_message(llm_request: LlmRequest, exc: Exception) -> None:
    llm_request.contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "The previous model call failed before ADK received a parseable provider "
                        "response. Retry the next action now. Keep the response simple and emit "
                        "exactly one valid tool call if a tool is needed. Avoid very large "
                        "tool-call arguments; write helper files first when content is long. "
                        f"Provider error: {str(exc)[:500]}"
                    )
                )
            ],
        )
    )


def _provider_json_response_error_response(exc: Exception, model_version: str) -> LlmResponse:
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=(
                        "The model provider returned a non-JSON or empty response twice. "
                        "The workflow should be retried from the last persisted sandbox state "
                        "or with a smaller next tool call."
                    )
                )
            ],
        ),
        model_version=model_version,
    )
    response.error_code = "PROVIDER_JSON_RESPONSE_ERROR"
    response.error_message = str(exc)[:1000]
    return response


def _append_provider_timeout_retry_message(llm_request: LlmRequest, timeout_seconds: float | None) -> None:
    llm_request.contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "The previous model call timed out before ADK received a response. "
                        f"The timeout was {timeout_seconds:g} seconds. Retry the next action now. "
                        "Keep the response small and emit exactly one valid tool call if a tool is needed. "
                        "Avoid large tool-call arguments; write helper files first when content is long."
                    )
                )
            ],
        )
    )


def _provider_timeout_error_response(
    exc: asyncio.TimeoutError,
    model_version: str,
    timeout_seconds: float | None,
) -> LlmResponse:
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=(
                        "The model provider timed out twice. The workflow should be retried from "
                        "the last persisted sandbox state or with a smaller next model/tool step."
                    )
                )
            ],
        ),
        model_version=model_version,
    )
    response.error_code = "PROVIDER_TIMEOUT"
    response.error_message = f"Model provider call timed out after {timeout_seconds:g} seconds."
    return response
