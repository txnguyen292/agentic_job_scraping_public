from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from google.adk.skills import load_skill_from_dir

from job_scraper.sandbox_terminal import SandboxRegistry, SandboxSessionRecord


SKILL_DIR = Path("skills/sandbox-page-analyst")


def test_sandbox_page_analyst_skill_bundle_exists() -> None:
    expected_files = [
        "SKILL.md",
        "scripts/protocol_contract.py",
        "scripts/validate_outputs.py",
        "scripts/sandbox_litellm_call.py",
        "scripts/sandbox_apply_patch.py",
        "scripts/sandbox_write_file.py",
        "references/protocol.md",
        "references/static-html-job-board.md",
        "references/diagnostic-mode.md",
        "references/workflow-mode.md",
        "references/itviec-listing-page.md",
        "references/embedded-json-job-board.md",
        "references/json-ld-job-postings.md",
        "references/nextjs-or-nuxt-hydration.md",
        "references/paginated-listing-pages.md",
        "references/detail-page-fanout.md",
        "references/blocked-or-script-only-pages.md",
        "schemas/page_profile.schema.json",
        "schemas/extraction_strategy.schema.json",
        "schemas/candidates.schema.json",
        "schemas/validation.schema.json",
        "schemas/extraction_run.schema.json",
        "schemas/script_manifest.schema.json",
        "schemas/reference_proposal.schema.json",
        "schemas/skill_patch.schema.json",
    ]

    for relative_path in expected_files:
        assert (SKILL_DIR / relative_path).exists(), relative_path
    assert not (SKILL_DIR / "protocol.md").exists()


def test_sandbox_page_analyst_skill_requires_evidence_cited_agent_extraction_workflow() -> None:
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    diagnostic_text = (SKILL_DIR / "references" / "diagnostic-mode.md").read_text(encoding="utf-8")
    workflow_text = (SKILL_DIR / "references" / "workflow-mode.md").read_text(encoding="utf-8")
    protocol_text = (SKILL_DIR / "references" / "protocol.md").read_text(encoding="utf-8")

    assert "Mode Router" in skill_text
    assert "Path Model" in skill_text
    assert "sandbox_exec.py --cmd` runs inside Docker with current working directory `/workspace`" in skill_text
    assert "Do not `cd` into host temp paths" in skill_text
    assert "references/diagnostic-mode.md" in skill_text
    assert "references/workflow-mode.md" in skill_text
    assert "Do not upgrade a diagnostic request into workflow mode" in skill_text
    assert "`loaded_resources`" in skill_text
    assert "the workflow reference and any site-specific reference just loaded" in skill_text
    assert "the next concrete action that carries out the loaded instructions" in skill_text
    assert "Script Catalog" in skill_text
    assert "scripts/protocol_contract.py" in skill_text
    assert "`output_contract` and the agent's own `producer_output_plan`" in skill_text
    assert "Do not load a separate catalog or script manual file" in skill_text
    assert "Use each script's `--help` output when exact arguments are needed" in skill_text
    assert "scripts/sandbox_apply_patch.py" in skill_text
    assert "Prefer this over full-file writes" in skill_text
    assert 'Pass commands with `--cmd "<shell command>"`' in skill_text
    assert "do not use pass-through args after `--`" in skill_text
    assert "Use `--max-chars`, not `--max-bytes`" in skill_text
    assert "Accountable extraction invariant" in skill_text
    assert "the agent chooses the extraction method" in skill_text
    assert "scripts may inspect, parse, extract, validate, serialize" in skill_text
    assert "evidence/index.json" in skill_text
    assert "evidence/chunks/*" in skill_text
    assert "`field_rationale` with `evidence_refs`" in skill_text
    assert "Workflow Protocol Contract" in skill_text
    assert "output/page_profile.json" in skill_text
    assert "output/extraction_strategy.json" in skill_text
    assert "output/extraction_run.json" in skill_text
    assert "output/candidates.json" in skill_text
    assert "output/validation.json" in skill_text
    assert "output/final.json" in skill_text
    assert "output/run_summary.md" in skill_text
    assert "output/script_manifest.json" in skill_text
    assert "Do not treat `output/page_profile.json`, `output/extraction_strategy.json`, or `output/validation.json` as cleanup after persistence fails" in skill_text
    assert "workspace-relative paths only" in skill_text
    assert "not `/workspace/...`" in skill_text
    assert "Never create placeholder required protocol outputs" in skill_text
    assert "do not write `{\"jobs\": [], \"count\": 20}`" in skill_text
    assert "the number of extracted job postings must match the agent's recorded observations" in skill_text
    assert "a mismatch is a repair target or `needs_review`, not `success`" in skill_text
    assert "`output/candidates.json` must use the candidate payload shape" in skill_text
    assert "Do not wrap candidates as" in skill_text
    assert "`output/candidates.json` must have top-level `source`, `jobs`, `selectors`, `crawl`, and `warnings`" in workflow_text
    assert "The agent is responsible for the extraction outcome" in workflow_text
    assert "After loading this workflow reference or any site-specific resource" in workflow_text
    assert "Record the loaded resource names" in workflow_text
    assert "a concrete `immediate_goal` plus `planned_next_tool`" in workflow_text
    assert "Scripts may assist or perform extraction" in workflow_text
    assert "Do not ingest unbounded evidence" in workflow_text
    assert "Mark a chunk `loaded: true` only after the agent has ingested that exact chunk" in workflow_text
    assert "`output/final.json` must use the sandbox result envelope" in skill_text
    assert "top-level `status` plus a `result` object" in skill_text
    assert "derive repeated evidence patterns" in workflow_text
    assert "Session Extraction Notebook" in workflow_text
    assert "Runtime Context Priority" in workflow_text
    assert "update_extraction_context" in workflow_text
    assert "session-only state" in workflow_text
    assert "<SESSION_EXTRACTION_CONTEXT>" in workflow_text
    assert "<RUNTIME_SANDBOX_NOTES>" in workflow_text
    assert "commanding guide" in workflow_text
    assert "supporting evidence" in workflow_text
    assert "Before every tool call and final response" in workflow_text
    assert "`attempted_actions`" in workflow_text
    assert "do not repeat those checks" in workflow_text
    assert "normally loading needed evidence, running or patching the chosen script, serializing outputs, validating, or finalizing" in workflow_text
    assert "refer to the latest injected session state for next-step guidance" in workflow_text
    assert 'Treat `status: "success"` from a non-context tool as verified completion' in workflow_text
    assert "advance to the next missing required output or validation" in workflow_text
    assert "`workflow_reflections`" in workflow_text
    assert "learned interpretations of failure patterns" in workflow_text
    assert "do not treat them as fixed tool recipes" in workflow_text
    assert "`observations`" in workflow_text
    assert "`extraction_plan`" in workflow_text
    assert "Observations must include implementation cues" in workflow_text
    assert "selector, attribute names, text boundaries, URL fallback order" in workflow_text
    assert "Example session context update after detecting ITviec cards" in workflow_text
    assert 'Detected 20 repeated job cards with selector [data-search--pagination-target=\\"jobCard\\"]' in workflow_text
    assert "one exact evidence chunk per soup.select" in workflow_text
    assert "/sign_in?job= fallback" in workflow_text
    assert "compare `output/candidates.json` and `output/final.json` against the requirement" in workflow_text
    assert "Reconcile the new result with the injected session extraction context" in workflow_text
    assert "If the new result contradicts the notes" in workflow_text
    assert "update the observations or extraction plan" in workflow_text
    assert "derive repeated evidence patterns" in workflow_text
    assert "slice exact evidence" in workflow_text
    assert "budget tokens" in workflow_text
    assert "parse/extract repeated records" in workflow_text
    assert "A valid workflow run must plan for all required protocol outputs before validation" in workflow_text
    assert "load the compact protocol contract with `scripts/protocol_contract.py`" in workflow_text
    assert "`producer_output_plan`" in workflow_text
    assert "This should prevent learning the output contract one validator error at a time" in workflow_text
    assert "before validation, finalization, persistence, or database queries" in workflow_text
    assert "Do not wait for `persist_sandbox_job_extraction` to reveal missing protocol files" in workflow_text
    assert "Use workspace-relative paths with `scripts/sandbox_write_file.py`" in workflow_text
    assert "Host-side helper scripts use workspace-relative paths" in workflow_text
    assert "Never use host temp paths" in workflow_text
    assert "treat it as host-side audit metadata" in workflow_text
    assert "never `/workspace/...`" in workflow_text
    assert "Do not overwrite outputs with placeholders" in workflow_text


    assert "repair the observations/evidence, extraction method, script, rationale, or serialization" in workflow_text
    assert "If `final.json` schema or count validation fails" in workflow_text
    assert "load `sandbox-extraction-debugger`" in workflow_text
    assert "usage-vs-implementation triage" in workflow_text
    assert "sandbox-artifact-only repair" in workflow_text
    assert "instead of duplicating debugging policy here" in workflow_text
    assert "do not manually assemble a partial `final.json`" in workflow_text
    assert "regenerate `candidates.json` and `final.json` from the same complete payload" in workflow_text
    assert "Do not repair `final.json` by writing one sampled job" in workflow_text
    assert "`{\"result\":\"success\"}`" in workflow_text
    assert "evidence/chunks/" in workflow_text
    assert "The agent is responsible for the extraction outcome" in workflow_text
    assert "Treat scripts as auditable supporting artifacts" in workflow_text
    assert "The persisted output files are the source of truth" in workflow_text
    assert "saved output paths" in workflow_text
    assert "`candidates_path` and `final_path`" in workflow_text
    assert "Helper stdout is only a compact run summary" in protocol_text
    assert "Do not use helper stdout as the source of truth" in protocol_text
    assert "Accountable Agent Contract" in protocol_text
    assert "`output/extraction_run.json` must record at least" in protocol_text
    assert "Candidate payload shape" in protocol_text
    assert "Final result shape" in protocol_text
    assert "If the agent extracts 20 valid candidates" in workflow_text
    assert "output/reference_proposal.md" in workflow_text
    assert "output/run_summary.md" in workflow_text
    assert "reference proposal" in protocol_text
    assert "Approved Runtime Packages" in skill_text
    assert "Do not run `pip install`" in skill_text
    assert "Do not load a second mode reference" in skill_text
    assert "Page Classification" in skill_text
    assert "classify the mounted page" in skill_text
    assert "`itviec-listing`" in skill_text
    assert "load `references/itviec-listing-page.md`" in skill_text
    assert "do not write `output/page_profile.json`, `output/extraction_strategy.json`, helper scripts, or any final payload from only `fetch_page_to_workspace` signals" in skill_text
    assert "First run a bounded sandbox probe with `sandbox_exec.py --cmd`" in skill_text
    assert "should not become a one-job selected-detail extraction" in skill_text
    assert "from the `sandbox-page-analyst` skill" in skill_text
    assert "If no specific site reference matches" in skill_text
    assert "continue with generic recurring-pattern extraction" in skill_text
    assert "Host-control scripts are not sandbox shell commands" in workflow_text
    assert "`job_selected` query parameter is a focus hint" in workflow_text
    assert "Error Repair Loop" in workflow_text
    assert "do not emit JSON `null` for string fields" in workflow_text
    assert "file-writing capability" in workflow_text
    assert "Do not use inline shell heredocs" in workflow_text
    assert "Do not write `output/extractor.py`" in diagnostic_text
    assert "Do not run `scripts/validate_outputs.py`" in diagnostic_text
    assert "Never replace the preview with only a file path" in diagnostic_text


def test_protocol_contract_script_exposes_validator_required_fields() -> None:
    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "protocol_contract.py")],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)

    assert payload["status"] == "success"
    assert payload["contract_version"] == "sandbox-page-analyst-protocol-v1"
    assert payload["extraction_run_json"]["required"] == [
        "observations",
        "chosen_strategy",
        "expected_output",
    ]
    assert payload["extraction_run_json"]["expected_output"]["required"] == [
        "expected_job_count",
        "count_basis",
        "count_rationale",
        "available_fields",
        "field_basis",
    ]
    assert payload["script_manifest_json"]["scripts_entry_requires_one_of"] == [
        "workflow_version",
        "reference_version",
    ]
    assert payload["script_manifest_json"]["scripts_entry_requires_reuse_classification"] == [
        "reuse",
        "reuse_classification",
    ]
    assert "crawl" in payload["candidates_json"]["required_top_level"]


def test_itviec_reference_prevents_job_selected_zero_job_regression() -> None:
    workflow_text = (SKILL_DIR / "references" / "workflow-mode.md").read_text(encoding="utf-8")
    itviec_text = (SKILL_DIR / "references" / "itviec-listing-page.md").read_text(encoding="utf-8")

    assert "If the source URL is on `itviec.com`" in workflow_text
    assert "load `references/itviec-listing-page.md` before writing helper scripts or protocol outputs" in workflow_text
    assert "job URLs must be detail posting URLs ending in `-NNNN`" in workflow_text
    assert "repeated job-card markers" in workflow_text
    assert "Do not classify a listing page as having \"no stable evidence\"" in workflow_text
    assert "[data-search--job-selection-job-url-value]" in itviec_text
    assert "[data-search--pagination-target=\"jobCard\"]" in itviec_text
    assert "/sign_in?job=<job-slug>-NNNN" in itviec_text
    assert "Convert the `job` query value into `/it-jobs/<job-slug>-NNNN`" in itviec_text
    assert "Never use `/companies/<company>?lab_feature=preview_jd_page` as `job_url`" in itviec_text
    assert "Do not deduplicate by company preview URL" in itviec_text
    assert "Do not treat a URL-only extraction as valid" in itviec_text
    assert "never leave `title` null" in itviec_text
    assert "`validation.json` must not say `valid: true`" in itviec_text
    assert "never use `job_selected` as an exclusion filter" in itviec_text
    assert "the output emits zero jobs, repair evidence loading, output/run record, or serialization" in itviec_text
    assert "primary extraction loop must be one emitted job per repeated card-like unit" in itviec_text
    assert "global matches are fallback/supporting evidence" in itviec_text
    assert "Do not start from all `/it-jobs/` anchors" in itviec_text
    assert "Do not accept `candidate_count: 1`" in itviec_text
    assert "Repair the card evidence loop" in itviec_text


def test_itviec_reference_requires_card_first_extraction_not_global_link_heuristics() -> None:
    workflow_text = (SKILL_DIR / "references" / "workflow-mode.md").read_text(encoding="utf-8")
    debugger_text = Path("skills/sandbox-extraction-debugger/SKILL.md").read_text(encoding="utf-8")
    debugger_reference_text = Path(
        "skills/sandbox-extraction-debugger/references/itviec-listing-repair.md"
    ).read_text(encoding="utf-8")

    assert "one emitted job per repeated listing card" in workflow_text
    assert "broad `/it-jobs/` URL scans are supporting evidence only" in workflow_text
    assert "if observations record 20 repeated listing cards and the output emits 1, 5, 64, 114" in workflow_text
    assert "If the declared `planned_next_tool` runs and fails" in workflow_text
    assert 'If the planned repair tool returns `status: "success"`' in debugger_text
    assert "finalize count mismatch is not a final-answer condition" in workflow_text
    assert "Keep this skill generic" in debugger_text
    assert "references/itviec-listing-repair.md" in debugger_text
    assert "extractor starts from global `/it-jobs/` URL matches" not in debugger_text
    assert "ITviec Listing Repair" in debugger_reference_text
    assert "global `/it-jobs/` URL matches" in debugger_reference_text
    assert "Make at least one distinct evidence/output/helper repair" in debugger_reference_text
    assert "ITviec listing evidence expects N jobs but candidates.jobs has M" in debugger_reference_text
    assert "focused test/probe: assert evidence/output emits one record per repeated card" in debugger_reference_text
    assert "disallowed fix: edit scripts/validate_outputs.py" in debugger_reference_text
    assert "If the planned tool runs and fails" in debugger_text
    assert "inspect that error-producing helper as a read-only contract" in debugger_text
    assert "`rationale`: why the next action is the most efficient step" in debugger_text


def test_workflow_state_is_compact_operational_memory() -> None:
    workflow_text = (SKILL_DIR / "references" / "workflow-mode.md").read_text(encoding="utf-8")
    debugger_text = Path("skills/sandbox-extraction-debugger/SKILL.md").read_text(encoding="utf-8")

    assert "Session state is the most important working document" in workflow_text
    assert "concise enough to fit inside one model call" in workflow_text
    assert "detailed enough that a future turn can derive the next efficient action" in workflow_text
    assert "`sandbox_read.py` is a bounded read-only tool" in workflow_text
    assert "Keep mutations bounded" in workflow_text
    assert "Keep state compact and operational" in workflow_text
    assert "do not store raw HTML" in workflow_text
    assert "record the rationale in state before taking that step" in workflow_text
    assert "Keep session state compact enough to fit inside one model call" in debugger_text
    assert "If the next action is to inspect another script" in debugger_text
    assert "why that inspection is more efficient than patching immediately" in debugger_text


def test_workflow_requires_validator_owned_quality_judgement() -> None:
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    workflow_text = (SKILL_DIR / "references" / "workflow-mode.md").read_text(encoding="utf-8")
    itviec_text = (SKILL_DIR / "references" / "itviec-listing-page.md").read_text(encoding="utf-8")

    assert "Validator-Owned Quality Gate" in skill_text
    assert "Do not rewrite supporting scripts merely because a count feels broad or narrow" in skill_text
    assert "Run `scripts/validate_outputs.py` after the accountable outputs exist" in workflow_text
    assert "treat that script as the read-only contract that produced the error" in workflow_text
    assert "inspect its `--help`" in workflow_text
    assert "Do not judge that a count is \"too broad\" or \"too narrow\" from intuition alone" in workflow_text
    assert "the next action is validation/finalization" in workflow_text
    assert "A count equal to the observed repeated card count is not by itself an error" in workflow_text
    assert "do not call 20 \"too broad\" by judgment alone" in itviec_text
    assert "Validate URL shape, required fields, and expected fixture/reference content" in itviec_text


def test_adk_does_not_load_duplicate_script_catalog_resources() -> None:
    skill = load_skill_from_dir(SKILL_DIR)

    assert skill.resources.get_script("CATALOG.md") is None
    assert skill.resources.get_script("README.md") is None


def test_project_sandbox_image_assets_define_allowlisted_parsers() -> None:
    dockerfile = Path("docker/sandbox/Dockerfile")
    requirements = Path("docker/sandbox/requirements.txt")

    assert dockerfile.exists()
    assert requirements.exists()
    assert "python:3.13-slim" in dockerfile.read_text(encoding="utf-8")

    requirement_text = requirements.read_text(encoding="utf-8")
    assert "beautifulsoup4" in requirement_text
    assert "lxml" in requirement_text
    assert "parsel" in requirement_text
    assert "typer" in requirement_text
    assert "rich" in requirement_text
    assert "loguru" in requirement_text


def test_validate_outputs_helper_accepts_complete_protocol_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir)
    _write_json(
        output_dir / "page_profile.json",
        {
            "page_files": ["page.html"],
            "detected_layouts": ["static-html-job-board"],
            "signals": {"job_like_links": 1},
            "selected_references": ["static-html-job-board.md"],
            "warnings": [],
        },
    )
    _write_json(
        output_dir / "extraction_strategy.json",
        {
            "strategy": "static-html-cards",
            "source_files": ["page.html"],
            "candidate_selectors": {"job_card": "article.job"},
            "warnings": [],
        },
    )
    _write_json(
        output_dir / "candidates.json",
        {
            "jobs": [
                {
                    "title": "Machine Learning Engineer",
                    "job_url": "https://example.com/jobs/ml",
                    "evidence": [{"text": "Machine Learning Engineer - Example"}],
                }
            ],
            "crawl": {"candidate_count": 1, "relevant_count": 1},
            "warnings": [],
        },
    )
    _write_json(
        output_dir / "validation.json",
        {
            "valid": True,
            "checks": {"required_files_exist": True},
            "candidate_count": 1,
            "relevant_count": 1,
            "warnings": [],
        },
    )
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "summary": "extracted one job",
            "result": {
                "jobs": [
                    {
                        "title": "Machine Learning Engineer",
                        "job_url": "https://example.com/jobs/ml",
                        "evidence": [{"text": "Machine Learning Engineer - Example"}],
                    }
                ],
                "crawl": {"candidate_count": 1, "relevant_count": 1},
                "warnings": [],
            },
            "protocol": {"valid": True, "warnings": []},
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["valid"] is True
    assert payload["page_profile"]["path"] == "output/page_profile.json"
    assert payload["final"]["path"] == "output/final.json"


def test_validate_outputs_helper_requires_script_manifest_for_authored_scripts(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"
    output_dir.mkdir()
    scratch_dir.mkdir()
    _write_minimal_valid_protocol(output_dir)
    (scratch_dir / "extract_jobs.py").write_text("print('extract')\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "output/script_manifest.json is required when the agent writes supporting scripts" in completed.stderr


def test_validate_outputs_helper_rejects_script_manifest_hash_mismatch(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"
    output_dir.mkdir()
    scratch_dir.mkdir()
    _write_minimal_valid_protocol(output_dir)
    (scratch_dir / "extract_jobs.py").write_text("print('extract')\n", encoding="utf-8")
    _write_json(
        output_dir / "script_manifest.json",
        {
            "scripts": [
                {
                    "path": "scratch/extract_jobs.py",
                    "purpose": "Extract repeated test jobs.",
                    "inputs": ["page.html"],
                    "outputs": ["output/candidates.json"],
                    "sha256": "not-the-real-hash",
                    "workflow_version": "workflow-mode.md@accountable",
                    "reuse": "run_specific",
                    "validation_result": {"valid": True},
                }
            ]
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "sha256 does not match file content" in completed.stderr


def test_validate_outputs_helper_requires_reference_proposal_for_recorded_layout_drift(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_minimal_valid_protocol(output_dir)
    extraction_run = json.loads((output_dir / "extraction_run.json").read_text(encoding="utf-8"))
    extraction_run["layout_drift_observed"] = True
    _write_json(output_dir / "extraction_run.json", extraction_run)

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "layout/reference drift was recorded" in completed.stderr


def test_validate_outputs_helper_accepts_loaded_evidence_cited_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    evidence_dir = tmp_path / "evidence"
    chunks_dir = evidence_dir / "chunks"
    output_dir.mkdir()
    chunks_dir.mkdir(parents=True)
    _write_accountability_artifacts(output_dir)
    (chunks_dir / "card_001.txt").write_text(
        '<article class="job"><h2>Machine Learning Engineer</h2><span>Example AI</span></article>',
        encoding="utf-8",
    )
    _write_json(
        evidence_dir / "index.json",
        {
            "chunks": [
                {
                    "chunk_id": "card_001",
                    "path": "evidence/chunks/card_001.txt",
                    "source_path": "page.html",
                    "char_count": 85,
                    "token_estimate": 24,
                    "loaded": True,
                }
            ]
        },
    )
    job = {
        "title": "Machine Learning Engineer",
        "company_name": "Example AI",
        "job_url": "https://example.com/jobs/ml",
        "field_rationale": {
            "title": {
                "value": "Machine Learning Engineer",
                "evidence_refs": ["card_001"],
                "rationale": "The loaded card chunk contains the title inside the heading.",
            },
            "company_name": {
                "value": "Example AI",
                "evidence_refs": ["card_001"],
                "rationale": "The company text appears next to the title in the same loaded card.",
            },
            "job_url": {
                "value": "https://example.com/jobs/ml",
                "evidence_refs": ["card_001"],
                "rationale": "The URL was resolved from the loaded card anchor.",
            },
        },
        "evidence": [{"ref": "card_001"}],
    }
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "agent-evidence-cited"})
    _write_json(output_dir / "candidates.json", {"jobs": [job], "crawl": {"candidate_count": 1}})
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "result": {"jobs": [job], "crawl": {"candidate_count": 1}},
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["valid"] is True
    assert payload["evidence_index"]["path"] == "evidence/index.json"


def test_validate_outputs_helper_rejects_missing_field_rationale_when_evidence_index_exists(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    evidence_dir = tmp_path / "evidence"
    chunks_dir = evidence_dir / "chunks"
    output_dir.mkdir()
    chunks_dir.mkdir(parents=True)
    _write_accountability_artifacts(output_dir)
    (chunks_dir / "card_001.txt").write_text("Machine Learning Engineer at Example AI", encoding="utf-8")
    _write_json(
        evidence_dir / "index.json",
        {
            "chunks": [
                {
                    "chunk_id": "card_001",
                    "path": "evidence/chunks/card_001.txt",
                    "token_estimate": 10,
                    "loaded": True,
                }
            ]
        },
    )
    job = {
        "title": "Machine Learning Engineer",
        "company_name": "Example AI",
        "job_url": "https://example.com/jobs/ml",
        "field_rationale": {
            "title": {"evidence_refs": ["card_001"], "rationale": "Loaded heading text."},
            "job_url": {"evidence_refs": ["card_001"], "rationale": "Loaded anchor URL."},
        },
    }
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "agent-evidence-cited"})
    _write_json(output_dir / "candidates.json", {"jobs": [job], "crawl": {"candidate_count": 1}})
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1})
    _write_json(
        output_dir / "final.json",
        {"status": "success", "output_schema": "job_extraction", "result": {"jobs": [job]}},
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "job 0 field company_name missing field_rationale" in completed.stderr


def test_validate_outputs_helper_rejects_cited_refs_without_evidence_index(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir, expected_count=20)
    _write_accountability_artifacts(output_dir)
    job = {
        "title": "Machine Learning Engineer",
        "job_url": "https://example.com/jobs/ml",
        "field_rationale": {
            "title": {"evidence_refs": ["card_001"], "rationale": "Loaded heading text."},
            "job_url": {"evidence_refs": ["card_001"], "rationale": "Loaded anchor URL."},
        },
        "evidence": [{"ref": "card_001"}],
    }
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "agent-evidence-cited"})
    _write_json(output_dir / "candidates.json", {"jobs": [job], "crawl": {"candidate_count": 1}})
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1})
    _write_json(
        output_dir / "final.json",
        {"status": "success", "output_schema": "job_extraction", "result": {"jobs": [job]}},
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "evidence/index.json is required when jobs cite evidence refs" in completed.stderr


def test_validate_outputs_helper_rejects_unloaded_evidence_refs(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    evidence_dir = tmp_path / "evidence"
    chunks_dir = evidence_dir / "chunks"
    output_dir.mkdir()
    chunks_dir.mkdir(parents=True)
    _write_accountability_artifacts(output_dir)
    (chunks_dir / "card_001.txt").write_text("Machine Learning Engineer at Example AI", encoding="utf-8")
    _write_json(
        evidence_dir / "index.json",
        {
            "chunks": [
                {
                    "chunk_id": "card_001",
                    "path": "evidence/chunks/card_001.txt",
                    "token_estimate": 10,
                    "loaded": False,
                }
            ]
        },
    )
    job = {
        "title": "Machine Learning Engineer",
        "job_url": "https://example.com/jobs/ml",
        "field_rationale": {
            "title": {"evidence_refs": ["card_001"], "rationale": "Loaded heading text."},
            "job_url": {"evidence_refs": ["card_001"], "rationale": "Loaded anchor URL."},
        },
    }
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "agent-evidence-cited"})
    _write_json(output_dir / "candidates.json", {"jobs": [job], "crawl": {"candidate_count": 1}})
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1})
    _write_json(
        output_dir / "final.json",
        {"status": "success", "output_schema": "job_extraction", "result": {"jobs": [job]}},
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "evidence ref card_001 was not loaded before extraction" in completed.stderr


def test_validate_outputs_helper_reports_all_missing_protocol_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_json(
        output_dir / "candidates.json",
        {
            "jobs": [],
            "crawl": {"candidate_count": 0, "relevant_count": 0},
            "warnings": [],
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stderr.splitlines()[0])
    assert payload["valid"] is False
    assert "page_profile.json" in payload["error"]
    assert "extraction_strategy.json" in payload["error"]
    assert "extraction_run.json" in payload["error"]
    assert "validation.json" in payload["error"]
    assert "final.json" in payload["error"]
    assert "run_summary.md" in payload["error"]


def test_validate_outputs_helper_rejects_invalid_final_shape(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir)
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "static-html-cards"})
    _write_json(
        output_dir / "candidates.json",
        {
            "jobs": [{"title": "Machine Learning Engineer", "job_url": "https://example.com/jobs/ml"}],
            "crawl": {"candidate_count": 1},
        },
    )
    _write_json(output_dir / "validation.json", {"valid": True})
    _write_json(output_dir / "final.json", {"job_extraction": {"jobs": []}})

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "final.json status must be success" in completed.stderr


def test_validate_outputs_helper_rejects_candidates_final_envelope_shape(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir)
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "static-html-cards"})
    _write_json(
        output_dir / "candidates.json",
        {
            "status": "success",
            "result": {
                "count": 1,
                "jobs": [{"title": "Machine Learning Engineer", "job_url": "https://example.com/jobs/ml"}],
            },
        },
    )
    _write_json(output_dir / "validation.json", {"valid": True})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "result": {
                "jobs": [{"title": "Machine Learning Engineer", "job_url": "https://example.com/jobs/ml"}],
                "crawl": {"candidate_count": 1},
            },
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "candidates.json must contain top-level jobs/crawl" in completed.stderr


def test_validate_outputs_helper_rejects_null_string_fields(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir)
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "static-html-cards"})
    _write_json(
        output_dir / "candidates.json",
        {
            "jobs": [
                {
                    "title": "Machine Learning Engineer",
                    "company_name": None,
                    "job_url": "https://example.com/jobs/ml",
                }
            ],
            "crawl": {"candidate_count": 1},
        },
    )
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "result": {
                "jobs": [
                    {
                        "title": "Machine Learning Engineer",
                        "company_name": None,
                        "job_url": "https://example.com/jobs/ml",
                    }
                ]
            },
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "company_name must be a string" in completed.stderr


def test_validate_outputs_helper_rejects_placeholder_for_required_observed_field(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_minimal_valid_protocol(output_dir)
    extraction_run = json.loads((output_dir / "extraction_run.json").read_text(encoding="utf-8"))
    extraction_run["expected_output"]["available_fields"]["company_name"] = "required_observed"
    extraction_run["expected_output"]["field_basis"]["company_name"] = (
        "The repeated card text exposes a company label for every in-scope job."
    )
    _write_json(output_dir / "extraction_run.json", extraction_run)

    candidates = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    candidates["jobs"][0]["company_name"] = "unknown"
    _write_json(output_dir / "candidates.json", candidates)
    final = json.loads((output_dir / "final.json").read_text(encoding="utf-8"))
    final["result"] = candidates
    _write_json(output_dir / "final.json", final)

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "field company_name is required by extraction_run.json expected_output.available_fields" in completed.stderr


def test_validate_outputs_helper_rejects_itviec_navigation_links(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir, expected_count=6)
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "itviec-listing-page"})
    _write_json(
        output_dir / "candidates.json",
        {
            "jobs": [
                {
                    "title": "AI Engineer",
                    "company_name": "ITviec Navigation",
                    "job_url": "https://itviec.com/it-jobs/ai-engineer?click_source=Navigation+menu",
                }
            ],
            "crawl": {"candidate_count": 1},
        },
    )
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "result": {
                "jobs": [
                    {
                        "title": "AI Engineer",
                        "company_name": "ITviec Navigation",
                        "job_url": "https://itviec.com/it-jobs/ai-engineer?click_source=Navigation+menu",
                    }
                ]
            },
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "ITviec job_url must be a detail posting URL" in completed.stderr


def test_validate_outputs_helper_rejects_single_job_success_when_itviec_listing_has_many_posts(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir, expected_count=20)
    (tmp_path / "page.html").write_text(
        "\n".join(
            f'<a href="/it-jobs/ai-engineer-example-company-{index:04d}">Job {index}</a>'
            for index in range(1, 7)
        ),
        encoding="utf-8",
    )
    one_job = {
        "title": "AI Engineer",
        "company_name": "Example",
        "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi",
        "job_url": "https://itviec.com/it-jobs/ai-engineer-example-company-0001",
    }
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"]})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "itviec-listing-page"})
    _write_json(
        output_dir / "candidates.json",
        {
            "source": {"source_name": "ITviec", "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi"},
            "jobs": [one_job],
            "crawl": {"candidate_count": 1, "relevant_count": 1},
        },
    )
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "result": {"jobs": [one_job]},
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "expects 6 jobs but candidates.jobs has 1" in completed.stderr


def test_validate_outputs_helper_rejects_itviec_count_over_extraction(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir, expected_count=20)
    (tmp_path / "page.html").write_text(
        "\n".join(
            f'<article data-search--pagination-target="jobCard"><a href="/it-jobs/ai-engineer-example-company-{index:04d}">Job {index}</a></article>'
            for index in range(1, 21)
        ),
        encoding="utf-8",
    )
    jobs = [
        {
            "title": f"AI Engineer {index}",
            "company_name": "Example",
            "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi",
            "job_url": f"https://itviec.com/it-jobs/ai-engineer-example-company-{index:04d}",
        }
        for index in range(1, 118)
    ]
    _write_json(
        output_dir / "page_profile.json",
        {
            "page_files": ["page.html"],
            "signals": {
                "observed_job_cards": 20,
                "job_url_candidates": 117,
            },
        },
    )
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "itviec-listing-page"})
    _write_json(
        output_dir / "candidates.json",
        {
            "source": {"source_name": "ITviec", "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi"},
            "jobs": jobs,
            "crawl": {"candidate_count": len(jobs), "relevant_count": len(jobs)},
        },
    )
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": len(jobs)})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "result": {"jobs": jobs},
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "expects 20 jobs but candidates.jobs has 117" in completed.stderr


def test_validate_outputs_helper_accepts_verified_itviec_fixture(tmp_path: Path) -> None:
    expected = json.loads(Path("tests/fixtures/itviec_ai_engineer_ha_noi.expected.json").read_text(encoding="utf-8"))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir, expected_count=20)
    (tmp_path / "page.html").write_text(
        Path("tests/fixtures/itviec_ai_engineer_ha_noi.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"], "observed_job_cards": 20})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "itviec-listing-page"})
    _write_json(output_dir / "candidates.json", expected)
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 20, "warnings": []})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "summary": "fixture output",
            "result": expected,
            "protocol": {"valid": True, "warnings": []},
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["valid"] is True


def test_validate_outputs_helper_rejects_itviec_output_that_differs_from_verified_fixture(tmp_path: Path) -> None:
    expected = json.loads(Path("tests/fixtures/itviec_ai_engineer_ha_noi.expected.json").read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(expected))
    actual["jobs"].append(
        {
            **actual["jobs"][0],
            "title": "Broad URL False Positive",
            "job_url": "https://itviec.com/it-jobs/broad-url-false-positive-9999",
        }
    )
    actual["crawl"] = {"discovered_count": 21, "candidate_count": 21, "relevant_count": 21, "blocked": False, "blocker": ""}
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir, expected_count=20)
    (tmp_path / "page.html").write_text(
        Path("tests/fixtures/itviec_ai_engineer_ha_noi.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"], "job_url_count": 21})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "itviec-listing-page"})
    _write_json(output_dir / "candidates.json", actual)
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 21, "warnings": []})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "summary": "over extracted output",
            "result": actual,
            "protocol": {"valid": True, "warnings": []},
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "expects 20 jobs but candidates.jobs has 21" in completed.stderr


def test_validate_outputs_helper_rejects_itviec_same_count_output_that_differs_from_verified_fixture(tmp_path: Path) -> None:
    actual = json.loads(Path("tests/fixtures/itviec_ai_engineer_ha_noi.expected.json").read_text(encoding="utf-8"))
    actual["jobs"][0]["company_name"] = "Wrong Company"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_accountability_artifacts(output_dir, expected_count=20)
    (tmp_path / "page.html").write_text(
        Path("tests/fixtures/itviec_ai_engineer_ha_noi.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"], "observed_job_cards": 20})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "itviec-listing-page"})
    _write_json(output_dir / "candidates.json", actual)
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 20, "warnings": []})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "summary": "same count but wrong content",
            "result": actual,
            "protocol": {"valid": True, "warnings": []},
        },
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "validate_outputs.py"), str(output_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "does not match frozen expected fixture" in completed.stderr
    assert "Wrong Company" in completed.stderr


def test_sandbox_write_file_helper_writes_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_write_test"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_write_file.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--path",
            "output/extractor.py",
            "--content",
            "print('ok')\n",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "success"
    assert payload["path"] == "output/extractor.py"
    assert (workspace / "output/extractor.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_sandbox_apply_patch_helper_applies_exact_replacement(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    target = workspace / "output/extractor.py"
    target.write_text("limit = 1\nprint(limit)\n", encoding="utf-8")
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_patch_test"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_apply_patch.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--path",
            "output/extractor.py",
            "--old",
            "limit = 1",
            "--new",
            "limit = 20",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "success"
    assert payload["mode"] == "exact_replacement"
    assert payload["changed_files"][0]["path"] == "output/extractor.py"
    assert target.read_text(encoding="utf-8") == "limit = 20\nprint(limit)\n"
    assert payload["artifact_sources"][0]["key"] == "patched_0"


def test_sandbox_apply_patch_helper_reports_context_mismatch_without_overwriting(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    target = workspace / "output/extractor.py"
    target.write_text("limit = 1\n", encoding="utf-8")
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_patch_mismatch"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_apply_patch.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--path",
            "output/extractor.py",
            "--old",
            "missing = True",
            "--new",
            "missing = False",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "error"
    assert payload["error_type"] == "patch_context_mismatch"
    assert payload["written"] is False
    assert target.read_text(encoding="utf-8") == "limit = 1\n"


def test_sandbox_apply_patch_helper_applies_unified_diff(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    target = workspace / "output/extractor.py"
    target.write_text("jobs = cards[:1]\nprint(len(jobs))\n", encoding="utf-8")
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_patch_diff"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )
    patch = "\n".join(
        [
            "--- a/output/extractor.py",
            "+++ b/output/extractor.py",
            "@@ -1,2 +1,2 @@",
            "-jobs = cards[:1]",
            "+jobs = cards",
            " print(len(jobs))",
        ]
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_apply_patch.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--patch",
            patch,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "success"
    assert payload["mode"] == "unified_diff"
    assert target.read_text(encoding="utf-8") == "jobs = cards\nprint(len(jobs))\n"


def test_sandbox_apply_patch_helper_accepts_codex_patch_format(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    target = workspace / "output/extractor.py"
    target.write_text("jobs = cards[:1]\nprint(len(jobs))\n", encoding="utf-8")
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_patch_codex"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: output/extractor.py",
            "@@",
            "-jobs = cards[:1]",
            "+jobs = cards",
            " print(len(jobs))",
            "*** End Patch",
        ]
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_apply_patch.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--patch",
            patch,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "success"
    assert target.read_text(encoding="utf-8") == "jobs = cards\nprint(len(jobs))\n"


def test_sandbox_litellm_call_persists_audited_response(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from job_scraper import sandbox_terminal_scripts

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_llm_test"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )
    seen: dict[str, object] = {}

    async def fake_litellm_messages(**kwargs):
        seen.update(kwargs)
        return '{"classification":"implementation_bug"}', {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        }

    monkeypatch.setattr(sandbox_terminal_scripts, "_run_litellm_messages", fake_litellm_messages)

    sandbox_terminal_scripts._sandbox_litellm_call_cli(
        audit_id=audit_id,
        messages_json='[{"role":"user","content":"Classify this error"}]',
        response_format_json='{"type":"json_object"}',
        output_path="output/debug.llm.json",
        model="openai/fake",
        max_tokens=12,
        temperature=0.1,
        user_id="user",
        session_id="session",
        app_root=str(tmp_path),
    )
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads((workspace / "output/debug.llm.json").read_text(encoding="utf-8"))
    trace = (workspace / "trace.jsonl").read_text(encoding="utf-8")

    assert payload["status"] == "success"
    assert payload["content"] == '{"classification":"implementation_bug"}'
    assert payload["output_path"] == "output/debug.llm.json"
    assert payload["usage"]["total_tokens"] == 14
    assert payload["artifact_sources"][0]["key"] == "llm_response"
    assert saved["content"] == '{"classification":"implementation_bug"}'
    assert seen["messages"] == [{"role": "user", "content": "Classify this error"}]
    assert seen["response_format"] == {"type": "json_object"}
    assert '"event":"llm_call"' in trace


def test_final_artifact_sources_include_reference_proposal_markdown(tmp_path: Path) -> None:
    from job_scraper import sandbox_terminal_scripts

    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    output_dir.mkdir(parents=True)
    (workspace / "progress.json").write_text("{}", encoding="utf-8")
    (workspace / "trace.jsonl").write_text("", encoding="utf-8")
    (output_dir / "final.json").write_text("{}", encoding="utf-8")
    (output_dir / "reference_proposal.md").write_text("# Proposal\n", encoding="utf-8")
    record = SandboxSessionRecord(
        user_id="user",
        session_id="session",
        audit_id="sandbox_run_final_sources",
        container_id="",
        workspace_path=str(workspace),
        status="finalized",
    )

    sources = sandbox_terminal_scripts._final_artifact_sources(record)
    by_key = {source["key"]: source for source in sources}

    assert by_key["output_final"]["mime_type"] == "application/json"
    assert by_key["output_reference_proposal"]["mime_type"] == "text/markdown"
    assert by_key["output_reference_proposal"]["artifact_name"] == "sandbox_run_final_sources__output__reference_proposal.md"


def test_sandbox_write_file_rejects_invalid_candidates_without_overwriting(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    existing = {"jobs": [{"title": "Existing", "job_url": "https://example.com/jobs/existing"}]}
    _write_json(workspace / "output/candidates.json", existing)
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_write_test"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_write_file.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--path",
            "output/candidates.json",
            "--content",
            '[{"title":"Broken"}]',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "error"
    assert payload["error_type"] == "protocol_model_validation"
    assert payload["path"] == "output/candidates.json"
    assert payload["model"] == "CandidatesOutput"
    assert payload["written"] is False
    assert "required_next" not in payload
    assert "suggested_next" not in payload
    assert json.loads((workspace / "output/candidates.json").read_text(encoding="utf-8")) == existing


def test_sandbox_write_file_rejects_candidates_final_envelope_without_overwriting(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    existing = {
        "jobs": [{"title": "Existing", "job_url": "https://example.com/jobs/existing"}],
        "crawl": {"candidate_count": 1},
    }
    _write_json(workspace / "output/candidates.json", existing)
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_write_test"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_write_file.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--path",
            "output/candidates.json",
            "--content",
            '{"status":"success","result":{"jobs":[{"title":"ML Engineer","job_url":"https://example.com/jobs/ml"}]}}',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "error"
    assert payload["error_type"] == "protocol_model_validation"
    assert payload["path"] == "output/candidates.json"
    assert payload["model"] == "CandidatesOutput"
    assert payload["written"] is False
    assert any("top-level jobs/crawl" in error["msg"] for error in payload["errors"])
    assert json.loads((workspace / "output/candidates.json").read_text(encoding="utf-8")) == existing


def test_sandbox_write_file_rejects_invalid_final_without_overwriting(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    existing = {
        "status": "needs_review",
        "output_schema": "job_extraction",
        "result": {"jobs": []},
    }
    _write_json(workspace / "output/final.json", existing)
    registry = SandboxRegistry(tmp_path)
    audit_id = "sandbox_run_write_test"
    registry.save(
        SandboxSessionRecord(
            user_id="user",
            session_id="session",
            audit_id=audit_id,
            container_id="",
            workspace_path=str(workspace),
            status="running",
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "sandbox_write_file.py"),
            "--app-root",
            str(tmp_path),
            "--user-id",
            "user",
            "--session-id",
            "session",
            "--audit-id",
            audit_id,
            "--path",
            "output/final.json",
            "--content",
            '{"status":"success","jobs":[]}',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "error"
    assert payload["error_type"] == "protocol_model_validation"
    assert payload["path"] == "output/final.json"
    assert payload["model"] == "FinalOutput"
    assert payload["written"] is False
    assert any(error["loc"] == ["result"] for error in payload["errors"])
    assert "required_next" not in payload
    assert "suggested_next" not in payload
    assert json.loads((workspace / "output/final.json").read_text(encoding="utf-8")) == existing


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_accountability_artifacts(output_dir: Path, *, expected_count: int = 1) -> None:
    _write_json(
        output_dir / "extraction_run.json",
        {
            "observations": [f"Observed {expected_count} job-like unit(s) in the mounted page."],
            "chosen_strategy": "test-fixture-accountable-extraction",
            "extraction_steps": ["Prepared protocol outputs for validator test."],
            "expected_output": {
                "expected_job_count": expected_count,
                "count_basis": "test fixture setup",
                "count_rationale": "The validator test fixture creates exactly this many in-scope job records.",
                "available_fields": {
                    "title": "required_observed",
                    "job_url": "required_observed",
                },
                "field_basis": {
                    "title": "Test fixture job object includes title.",
                    "job_url": "Test fixture job object includes job_url.",
                },
            },
            "validation": {"valid": True},
        },
    )
    (output_dir / "run_summary.md").write_text(
        "The test extraction wrote complete protocol files, recorded the chosen strategy, "
        "and reached the validator/finalizer gate for the targeted assertion.",
        encoding="utf-8",
    )


def _write_minimal_valid_protocol(output_dir: Path) -> None:
    _write_accountability_artifacts(output_dir)
    job = {
        "title": "Machine Learning Engineer",
        "company_name": "Example AI",
        "job_url": "https://example.com/jobs/ml",
        "evidence": [{"text": "Machine Learning Engineer at Example AI"}],
    }
    candidates = {
        "source": {"source_name": "Example", "source_url": "https://example.com/jobs"},
        "jobs": [job],
        "selectors": {},
        "crawl": {"candidate_count": 1, "relevant_count": 1},
        "warnings": [],
    }
    _write_json(output_dir / "page_profile.json", {"page_files": ["page.html"], "warnings": []})
    _write_json(output_dir / "extraction_strategy.json", {"strategy": "test-fixture"})
    _write_json(output_dir / "candidates.json", candidates)
    _write_json(output_dir / "validation.json", {"valid": True, "candidate_count": 1, "warnings": []})
    _write_json(
        output_dir / "final.json",
        {
            "status": "success",
            "output_schema": "job_extraction",
            "summary": "Extracted one job.",
            "result": candidates,
            "protocol": {"valid": True, "warnings": []},
        },
    )
