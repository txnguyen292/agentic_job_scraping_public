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
    ref: str = ""
    text: str = Field(default="", max_length=500)


class ProtocolFieldRationale(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: Any = None
    evidence_refs: list[str]
    rationale: str

    @field_validator("evidence_refs")
    @classmethod
    def _require_evidence_refs(cls, value: list[str]) -> list[str]:
        if not value or not any(str(ref).strip() for ref in value):
            raise ValueError("evidence_refs must include at least one evidence chunk id")
        return value

    @field_validator("rationale")
    @classmethod
    def _require_rationale(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("rationale is required")
        return cleaned


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
    field_rationale: dict[str, ProtocolFieldRationale] = Field(default_factory=dict)

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


class ExtractionRunOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    page: dict[str, Any] = Field(default_factory=dict)
    observations: list[Any]
    chosen_strategy: str = ""
    extraction_steps: list[Any] = Field(default_factory=list)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observations")
    @classmethod
    def _require_observations(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("observations must describe the page/layout signals used for extraction")
        return value

    @model_validator(mode="after")
    def _require_strategy(self) -> "ExtractionRunOutput":
        strategy = self.chosen_strategy or str(getattr(self, "strategy", "") or "")
        if not strategy.strip():
            raise ValueError("chosen_strategy is required")
        return self


class ScriptManifestEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    purpose: str
    inputs: list[Any] = Field(default_factory=list)
    outputs: list[Any] = Field(default_factory=list)
    sha256: str = ""
    workflow_version: str = ""
    reference_version: str = ""
    reuse: str = "run_specific"
    validation_result: dict[str, Any] = Field(default_factory=dict)

    @field_validator("path", "purpose")
    @classmethod
    def _require_non_empty_text(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} is required")
        return cleaned


class ScriptManifestOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    scripts: list[ScriptManifestEntry]

    @field_validator("scripts")
    @classmethod
    def _require_scripts(cls, value: list[ScriptManifestEntry]) -> list[ScriptManifestEntry]:
        if not value:
            raise ValueError("scripts must include every authored supporting script")
        return value


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
    "output/extraction_run.json": ExtractionRunOutput,
    "output/script_manifest.json": ScriptManifestOutput,
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
