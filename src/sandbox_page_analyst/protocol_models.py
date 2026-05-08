from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


PROTOCOL_MODEL_BY_PATH: dict[str, type[BaseModel]] = {}


class ProtocolEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str = ""
    locator: str = ""
    text: str = Field(default="", max_length=500)


class ProtocolJob(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    job_url: str
    company_name: str = ""
    source_url: str = ""
    location_raw: str = ""
    location: str = ""
    remote_type: str = ""
    employment_type: str = ""
    posted_at: str = ""
    salary_raw: str = ""
    description_text: str = ""
    description: str = ""
    relevance_reason: str = ""
    tags: list[Any] = Field(default_factory=list)
    evidence: list[ProtocolEvidence] = Field(default_factory=list)

    @field_validator("title", "job_url")
    @classmethod
    def _require_non_empty(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} is required")
        return cleaned

    @field_validator("job_url")
    @classmethod
    def _validate_job_url(cls, value: str) -> str:
        parsed = urlparse(value)
        host = parsed.netloc.lower()
        if not host.endswith("itviec.com"):
            return value
        path = parsed.path.rstrip("/")
        if not re.fullmatch(r"/it-jobs/.+-\d{4}", path):
            raise ValueError("ITviec job_url must be a detail posting URL ending in -NNNN")
        if "click_source=" in parsed.query:
            raise ValueError("ITviec job_url must not be a navigation/category URL")
        return value


class PageProfileOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class ExtractionStrategyOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class CandidatesOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: dict[str, Any] = Field(default_factory=dict)
    jobs: list[ProtocolJob]
    selectors: dict[str, Any] = Field(default_factory=dict)
    crawl: dict[str, Any]
    warnings: list[Any] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _reject_final_result_envelope(cls, value: Any) -> Any:
        if isinstance(value, dict) and "result" in value and "jobs" not in value:
            raise ValueError(
                "candidates.json must contain top-level jobs/crawl; do not use the final-result envelope "
                "{status, result: {jobs: [...]}} for candidates.json"
            )
        return value


class ValidationOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    valid: bool
    checks: dict[str, Any] = Field(default_factory=dict)
    candidate_count: int | None = None
    relevant_count: int | None = None
    warnings: list[Any] = Field(default_factory=list)


class FinalOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["success", "needs_review", "error"]
    output_schema: str = "job_extraction"
    summary: str = ""
    result: CandidatesOutput
    protocol: dict[str, Any] = Field(default_factory=dict)
    error: str = ""

    @model_validator(mode="after")
    def _success_requires_jobs(self) -> "FinalOutput":
        if self.status == "success" and not self.result.jobs:
            raise ValueError("final.json cannot be success with zero extracted jobs")
        return self


class ReferenceProposalOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


PROTOCOL_MODEL_BY_PATH = {
    "output/page_profile.json": PageProfileOutput,
    "output/extraction_strategy.json": ExtractionStrategyOutput,
    "output/candidates.json": CandidatesOutput,
    "output/validation.json": ValidationOutput,
    "output/final.json": FinalOutput,
    "output/reference_proposal.json": ReferenceProposalOutput,
}


def protocol_model_for_path(path: str) -> type[BaseModel] | None:
    normalized = str(Path(path).as_posix()).lstrip("./")
    return PROTOCOL_MODEL_BY_PATH.get(normalized)


def validate_protocol_file_content(path: str, content: str) -> dict[str, Any]:
    model = protocol_model_for_path(path)
    if model is None:
        return {"valid": True, "path": path, "model": ""}
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return {
            "valid": False,
            "path": path,
            "model": model.__name__,
            "errors": [
                {
                    "loc": [],
                    "msg": f"Invalid JSON: {exc.msg}",
                    "type": "json_invalid",
                }
            ],
        }
    if not isinstance(payload, dict):
        return {
            "valid": False,
            "path": path,
            "model": model.__name__,
            "errors": [
                {
                    "loc": [],
                    "msg": "Input should be a JSON object",
                    "type": "dict_type",
                }
            ],
        }
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return {
            "valid": False,
            "path": path,
            "model": model.__name__,
            "errors": _validation_errors(exc),
        }
    return {"valid": True, "path": path, "model": model.__name__}


def _validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for error in exc.errors(include_url=False):
        cleaned = dict(error)
        cleaned["loc"] = list(cleaned.get("loc") or [])
        if "ctx" in cleaned:
            cleaned["ctx"] = {key: str(value) for key, value in dict(cleaned["ctx"]).items()}
        errors.append(cleaned)
    return errors
