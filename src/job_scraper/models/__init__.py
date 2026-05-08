from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SourceConfig:
    name: str
    source_type: str
    board_token: Optional[str] = None
    source_url: Optional[str] = None
    company_name: Optional[str] = None
    startup_bias: float = 0.5
    fixture_file: Optional[str] = None
    base_dir: str = "."

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], base_dir: str) -> "SourceConfig":
        source_type = str(payload["source_type"]).strip().lower()
        if source_type not in {"greenhouse", "lever"}:
            raise ValueError(f"Unsupported source_type: {source_type}")
        return cls(
            name=str(payload["name"]).strip(),
            source_type=source_type,
            board_token=payload.get("board_token"),
            source_url=payload.get("source_url"),
            company_name=payload.get("company_name"),
            startup_bias=float(payload.get("startup_bias", 0.5)),
            fixture_file=payload.get("fixture_file"),
            base_dir=base_dir,
        )


@dataclass
class NormalizedJob:
    job_key: str
    source_name: str
    source_type: str
    source_url: str
    job_url: str
    company_name: str
    title: str
    team: str
    location_raw: str
    location_country: str
    location_city: str
    remote_type: str
    employment_type: str
    description_text: str
    posted_at: str
    scraped_at: str
    ai_ml_score: float
    startup_score: float
    overall_score: float
    is_relevant: bool
    status: str = "active"
    metadata_json: str = "{}"


@dataclass
class CrawlRunResult:
    run_id: str
    started_at: str
    finished_at: str
    status: str
    source_count: int
    discovered_count: int
    written_count: int
    error_count: int
    notes_json: str = "{}"
    source_results: Dict[str, Any] = field(default_factory=dict)
