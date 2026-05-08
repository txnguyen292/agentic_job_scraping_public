from __future__ import annotations

import os
from typing import Any

from google.adk import Agent
from google.adk.apps.app import App
from google.adk.models.lite_llm import LiteLlm

from sandbox_page_analyst.adk_tools import run_page_analysis


DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "openai/gpt-5.4-mini")


class SerializableLiteLlm(LiteLlm):
    """LiteLLM model wrapper that keeps ADK Web graph serialization safe."""

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = super().model_dump(*args, **kwargs)
        payload.pop("llm_client", None)
        return payload


root_agent = Agent(
    name="sandbox_page_analyst",
    model=SerializableLiteLlm(model=DEFAULT_MODEL),
    description="Standalone ADK entrypoint for running the OpenAI sandbox page analyst on mounted page files.",
    instruction=(
        "You are the control agent for the sandbox page analyst. "
        "Use run_page_analysis only when the user provides explicit local workspace files or page artifact paths. "
        "Do not fetch URLs yourself. The sandbox worker must inspect only mounted files and return compact final JSON."
    ),
    tools=[run_page_analysis],
)


app = App(name="sandbox_page_analyst", root_agent=root_agent)
