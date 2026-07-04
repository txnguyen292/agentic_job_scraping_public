from __future__ import annotations

from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from job_scraper.adk_plugin_modules.sandbox_guard.after_model import (
    after_model_callback as _after_model_callback,
)
from job_scraper.adk_plugin_modules.sandbox_guard.after_tool import (
    after_tool_callback as _after_tool_callback,
)
from job_scraper.adk_plugin_modules.sandbox_guard.before_model import (
    before_model_callback as _before_model_callback,
)
from job_scraper.adk_plugin_modules.sandbox_guard.before_tool import (
    before_tool_callback as _before_tool_callback,
)


class SandboxWorkflowGuardPlugin(BasePlugin):
    """Prevent a started sandbox from becoming a premature final answer."""

    def __init__(self, *, max_forced_continuations: int = 0, name: str = "sandbox_workflow_guard_plugin") -> None:
        super().__init__(name=name)
        # Kept for compatibility with older tests/config, but the guard no
        # longer spends sandbox command budget by auto-invoking tools.
        self.max_forced_continuations = max_forced_continuations

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> dict | None:
        return await _before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context)

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict,
    ) -> dict | None:
        return await _after_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_context, result=result)

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        return await _before_model_callback(callback_context=callback_context, llm_request=llm_request)

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        return await _after_model_callback(callback_context=callback_context, llm_response=llm_response)
