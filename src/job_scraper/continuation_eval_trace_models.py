"""Pydantic models for ADK continuation-eval trace inputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class FunctionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    response: dict[str, Any] = Field(default_factory=dict)


class AdkPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    function_call: FunctionCall | None = None
    function_response: FunctionResponse | None = None
    text: str | None = None

    @model_validator(mode="after")
    def require_known_payload(self) -> "AdkPart":
        if self.function_call is None and self.function_response is None and self.text is None:
            raise ValueError("ADK part must contain function_call, function_response, or text")
        return self


class AdkContent(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str | None = None
    parts: list[AdkPart] = Field(default_factory=list)


class AdkInvocationEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    author: str | None = None
    content: AdkContent


class AdkIntermediateData(BaseModel):
    model_config = ConfigDict(extra="allow")

    invocation_events: list[AdkInvocationEvent] = Field(default_factory=list)


class AdkActualInvocation(BaseModel):
    model_config = ConfigDict(extra="allow")

    invocation_id: str
    user_content: AdkContent | None = None
    intermediate_data: AdkIntermediateData = Field(default_factory=AdkIntermediateData)
    final_response: AdkContent | None = None


class AdkTraceFixtureSet(RootModel[dict[str, AdkActualInvocation]]):
    pass

