#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Annotated, Any

import typer
from loguru import logger


logger.remove()
logger.add(sys.stderr, level="WARNING")

app = typer.Typer(
    add_completion=False,
    help=(
        "Emit the compact sandbox workflow output contract. Use before authoring "
        "producer scripts or protocol outputs so the agent can record an output plan."
    ),
    rich_markup_mode="rich",
)


REQUIRED_PROTOCOL_OUTPUTS = [
    "output/page_profile.json",
    "output/extraction_strategy.json",
    "output/extraction_run.json",
    "output/candidates.json",
    "output/validation.json",
    "output/final.json",
    "output/run_summary.md",
]


def build_contract() -> dict[str, Any]:
    return {
        "status": "success",
        "contract_version": "sandbox-page-analyst-protocol-v1",
        "purpose": (
            "Compact preflight contract for agent-authored workflow outputs. "
            "The agent must use this to write its own producer_output_plan before "
            "authoring output/*.py producer scripts or protocol result files."
        ),
        "required_outputs": REQUIRED_PROTOCOL_OUTPUTS,
        "conditional_outputs": {
            "output/script_manifest.json": (
                "Required when supporting scripts are authored under scratch/ or output/."
            ),
            "evidence/index.json": (
                "Required when exact evidence chunks are created under evidence/chunks/."
            ),
        },
        "candidates_json": {
            "shape": "candidate_payload",
            "required_top_level": ["source", "jobs", "selectors", "crawl", "warnings"],
            "must_not_use_final_envelope": True,
            "jobs_must_match_expected_output": True,
            "crawl_required": ["candidate_count", "relevant_count", "page_count", "method"],
            "job_required": ["title", "job_url"],
            "field_rationale_required_when_evidence_chunked": True,
        },
        "final_json": {
            "shape": "sandbox_result_envelope",
            "required_top_level": ["status", "output_schema", "summary", "result"],
            "result_reuses_candidates_payload": True,
        },
        "extraction_run_json": {
            "required": ["observations", "chosen_strategy", "expected_output"],
            "observations": "List page/layout signals used: selectors, repeated units, URL attributes, text neighborhoods, and count basis.",
            "chosen_strategy": "Non-empty description of the extraction method the agent selected.",
            "expected_output": {
                "required": [
                    "expected_job_count",
                    "count_basis",
                    "count_rationale",
                    "available_fields",
                    "field_basis",
                ],
                "description": (
                    "Object explaining the job count implied by repeated-unit observations and "
                    "why the saved successful output should contain that count."
                ),
                "available_fields": (
                    "Object mapping output field names to availability statuses. Use required_observed when "
                    "the page evidence exposes the field for each in-scope job; use observed_if_present or "
                    "not_observed only when that is supported by inspection."
                ),
                "field_basis": (
                    "Object mapping every required_observed field to the page evidence signal that made the "
                    "agent treat the field as available, for example card text, URL attributes, or metadata rows."
                ),
            },
            "validation": "Object when present.",
        },
        "validation_json": {
            "shape": "validation_summary",
            "required_top_level": ["valid", "checks", "candidate_count", "relevant_count"],
            "valid": (
                "Boolean. Use true only when the generated protocol output satisfies the agent's "
                "current evidence-backed checks; use false for needs_review/error outputs."
            ),
            "candidate_count": "Integer derived from the current candidates.jobs length, not a fixed example value.",
            "relevant_count": (
                "Integer derived from the current run's relevance decision; normally the count of jobs "
                "kept in final result for the current extraction."
            ),
            "checks": (
                "Object containing current-run check results such as count_match, required_fields_present, "
                "url_shape_valid, evidence_refs_loaded, or schema_shape_valid."
            ),
            "example_shape": {
                "valid": True,
                "checks": {
                    "count_match": "<candidate_count equals expected_output.expected_job_count>",
                    "required_fields_present": "<no required_observed field is missing or placeholder>",
                },
                "candidate_count": "<len(candidates.jobs)>",
                "relevant_count": "<current relevant job count>",
                "warnings": [],
            },
            "not_status_field": "Do not use status='valid' or status='success' as a substitute for valid=true.",
        },
        "script_manifest_json": {
            "required_when": "Any supporting script exists under scratch/ or output/.",
            "scripts_entry_required": ["path", "purpose"],
            "scripts_entry_recommended": ["inputs", "outputs", "sha256", "validation_result"],
            "scripts_entry_requires_one_of": ["workflow_version", "reference_version"],
            "scripts_entry_requires_reuse_classification": ["reuse", "reuse_classification"],
            "must_include_every_authored_script": True,
        },
        "producer_output_plan": {
            "agent_owned": True,
            "minimum_fields": [
                "required_outputs",
                "extraction_run",
                "candidates_json",
                "final_json",
                "script_manifest",
                "validation_plan",
            ],
            "note": (
                "The runtime supplies this compact contract, but the agent must still decide "
                "the extraction method, evidence plan, script plan, and exact output values."
            ),
        },
        "validation_sequence": [
            "write or regenerate accountable outputs from one recorded method",
            "run scripts/validate_outputs.py --audit-id <audit_id>",
            "run scripts/sandbox_finalize.py --audit-id <audit_id> only after validation succeeds",
        ],
    }


@app.command()
def main(
    pretty: Annotated[bool, typer.Option("--pretty", help="Pretty-print JSON output.")] = False,
) -> None:
    payload = build_contract()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty))


if __name__ == "__main__":
    app()
