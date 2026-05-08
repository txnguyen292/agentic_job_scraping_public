from __future__ import annotations

import asyncio
import json
from pathlib import Path

from google.genai import types as genai_types

from job_scraper import adk_tools
from job_scraper.adk_tools import (
    crawl_seed_sources,
    fetch_page,
    fetch_page_to_workspace,
    list_seed_references,
    persist_sandbox_job_extraction,
    promote_sandbox_extraction,
    query_jobs,
    record_crawl_run,
    update_extraction_context,
)
from job_scraper.runtime_state import SESSION_EXTRACTION_CONTEXT_STATE_KEY
from job_scraper.sandbox_terminal import SandboxRegistry, SandboxSessionRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILE = PROJECT_ROOT / "seeds" / "demo_sources.json"


class FakeToolContext:
    def __init__(self) -> None:
        self.state: dict[str, object] = {}


def test_list_seed_references_returns_demo_sources() -> None:
    result = list_seed_references(str(SOURCE_FILE))

    assert result["status"] == "success"
    assert result["count"] == 2
    assert result["items"][0]["source_type"] == "greenhouse"


def test_crawl_seed_sources_and_query_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"

    crawl_result = crawl_seed_sources(source_file=str(SOURCE_FILE), db_path=str(db_path))
    query_result = query_jobs(relevant_only=True, db_path=str(db_path))

    assert crawl_result["status"] == "success"
    assert crawl_result["written_count"] == 4
    assert query_result["count"] == 2
    assert query_result["items"][0]["title"] == "Machine Learning Engineer"


def test_record_crawl_run_accepts_missing_run_payload(tmp_path: Path) -> None:
    result = record_crawl_run(db_path=str(tmp_path / "jobs.db"))

    assert result["status"] == "success"
    assert result["source_count"] == 0
    assert result["written_count"] == 0


def test_update_extraction_context_writes_session_state_only() -> None:
    context = FakeToolContext()

    result = update_extraction_context(
        task_understanding="Extract AI/ML jobs from the target listing URL and save validated records.",
        final_goal="Extract AI/ML jobs from the target listing URL and save validated records.",
        initial_plan=["save page workspace", "derive recurring job-card pattern", "run sandbox extractor"],
        audit_id="sandbox_run_test",
        page_id="page_123",
        observations=["20 job-card markers", "64 broad links included navigation"],
        extraction_plan=["select one repeated job-card container per job"],
        last_result={"status": "invalid", "count": 64},
        known_errors=["navigation links were included"],
        attempted_actions=["checked output/final.json existence", "read placeholder output/extractor.py"],
        immediate_goal="repair output/extractor.py",
        planned_next_tool={
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_apply_patch.py",
            "target_paths": ["output/extractor.py"],
        },
        repair_scope={
            "status": "patching",
            "objective": "fix over-broad extraction",
            "files": ["output/extractor.py"],
            "allowed_inspections": ["output/extractor.py", "output/candidates.json"],
        },
        required_outputs=[
            "output/page_profile.json",
            "output/extraction_strategy.json",
            "output/candidates.json",
            "output/validation.json",
            "output/final.json",
        ],
        workflow_contract={
            "producer": "output/extractor.py",
            "required_outputs": [
                "output/page_profile.json",
                "output/extraction_strategy.json",
                "output/candidates.json",
                "output/validation.json",
                "output/final.json",
            ],
            "success_gate": "validate and finalize before persistence",
            "repair_rule": "repair missing outputs at producer",
        },
        tool_context=context,  # type: ignore[arg-type]
    )

    assert result["status"] == "success"
    assert result["scope"] == "session_only"
    assert result["attempted_actions_count"] == 2
    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert isinstance(saved, dict)
    assert saved["audit_id"] == "sandbox_run_test"
    assert saved["task_understanding"] == "Extract AI/ML jobs from the target listing URL and save validated records."
    assert saved["final_goal"] == "Extract AI/ML jobs from the target listing URL and save validated records."
    assert saved["initial_plan"] == ["save page workspace", "derive recurring job-card pattern", "run sandbox extractor"]
    assert saved["observations"] == ["20 job-card markers", "64 broad links included navigation"]
    assert saved["attempted_actions"] == ["checked output/final.json existence", "read placeholder output/extractor.py"]
    assert saved["last_result"] == {"status": "invalid", "count": 64}
    assert saved["planned_next_tool"]["file_path"] == "scripts/sandbox_apply_patch.py"
    assert saved["repair_scope"]["files"] == ["output/extractor.py"]
    assert saved["required_outputs"][-1] == "output/final.json"
    assert saved["workflow_contract"]["producer"] == "output/extractor.py"
    assert result["planned_next_tool"]["target_paths"] == ["output/extractor.py"]
    assert result["repair_scope"]["objective"] == "fix over-broad extraction"
    assert result["workflow_contract"]["success_gate"] == "validate and finalize before persistence"


def test_update_extraction_context_merges_attempted_actions_without_duplicates() -> None:
    context = FakeToolContext()

    update_extraction_context(
        attempted_actions=["checked output/final.json existence"],
        tool_context=context,  # type: ignore[arg-type]
    )
    result = update_extraction_context(
        attempted_actions=["checked output/final.json existence", "wrote output/extractor.py"],
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["attempted_actions_count"] == 2
    assert saved["attempted_actions"] == ["checked output/final.json existence", "wrote output/extractor.py"]


def test_update_extraction_context_replaces_current_state_fields() -> None:
    context = FakeToolContext()

    update_extraction_context(
        last_result={"status": "error", "error_type": "producer_source_rejected"},
        known_errors=["producer_source_rejected", "workflow_contract_required"],
        immediate_goal="repair producer",
        planned_next_tool={
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/sandbox_write_file.py",
        },
        tool_context=context,  # type: ignore[arg-type]
    )

    result = update_extraction_context(
        last_result={
            "status": "success",
            "candidate_count": 20,
            "candidates_path": "output/candidates.json",
            "final_path": "output/final.json",
        },
        known_errors=[],
        immediate_goal="validate outputs",
        planned_next_tool={
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/validate_outputs.py",
        },
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["status"] == "success"
    assert saved["last_result"] == {
        "status": "success",
        "candidate_count": 20,
        "candidates_path": "output/candidates.json",
        "final_path": "output/final.json",
    }
    assert saved["known_errors"] == []
    assert saved["immediate_goal"] == "validate outputs"
    assert saved["planned_next_tool"]["file_path"] == "scripts/validate_outputs.py"


def test_update_extraction_context_rejects_stale_known_errors_after_success() -> None:
    context = FakeToolContext()

    update_extraction_context(
        workflow_contract={
            "producer": "output/extractor.py",
            "required_outputs": [
                "output/page_profile.json",
                "output/extraction_strategy.json",
                "output/candidates.json",
                "output/validation.json",
                "output/final.json",
            ],
        },
        tool_context=context,  # type: ignore[arg-type]
    )
    context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]["known_errors"] = ["workflow_contract_required"]
    result = update_extraction_context(
        last_result={
            "status": "success",
            "audit_id": "sandbox_run_test",
            "candidate_count": 20,
            "final_path": "output/final.json",
        },
        known_errors=["workflow_contract_required", "producer_source_rejected", "sandbox_script_requires_audit_id"],
        immediate_goal="validate outputs",
        planned_next_tool={
            "tool_name": "run_skill_script",
            "skill_name": "sandbox-page-analyst",
            "file_path": "scripts/validate_outputs.py",
        },
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["status"] == "error"
    assert result["guardrail"] == "stale_known_errors"
    assert result["stale_known_errors"] == [
        "workflow_contract_required",
        "producer_source_rejected",
        "sandbox_script_requires_audit_id",
    ]
    assert saved["known_errors"] == ["workflow_contract_required"]


def test_update_extraction_context_rejects_stale_producer_error_after_finalizer_feedback() -> None:
    context = FakeToolContext()

    update_extraction_context(
        known_errors=["producer_source_rejected"],
        tool_context=context,  # type: ignore[arg-type]
    )
    result = update_extraction_context(
        last_result={
            "status": "error",
            "file_path": "scripts/sandbox_finalize.py",
            "error_type": "frozen_fixture_mismatch",
            "missing_files": [],
            "required_files": [
                "output/page_profile.json",
                "output/extraction_strategy.json",
                "output/candidates.json",
                "output/validation.json",
                "output/final.json",
            ],
        },
        known_errors=["producer_source_rejected", "frozen_fixture_mismatch"],
        immediate_goal="repair fixture mismatches",
        planned_next_tool={
            "tool_name": "load_skill",
            "skill_name": "sandbox-extraction-debugger",
        },
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["status"] == "error"
    assert result["stale_known_errors"] == ["producer_source_rejected"]
    assert saved["known_errors"] == ["producer_source_rejected"]


def test_update_extraction_context_mirrors_required_outputs_from_workflow_contract() -> None:
    context = FakeToolContext()

    result = update_extraction_context(
        workflow_contract={
            "producer": "output/extractor.py",
            "required_outputs": [
                "output/page_profile.json",
                "output/extraction_strategy.json",
                "output/candidates.json",
                "output/validation.json",
                "output/final.json",
            ],
        },
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["required_outputs"] == [
        "output/page_profile.json",
        "output/extraction_strategy.json",
        "output/candidates.json",
        "output/validation.json",
        "output/final.json",
    ]
    assert saved["required_outputs"] == result["required_outputs"]


def test_update_extraction_context_mirrors_required_outputs_into_workflow_contract() -> None:
    context = FakeToolContext()

    result = update_extraction_context(
        required_outputs=[
            "output/page_profile.json",
            "output/extraction_strategy.json",
            "output/candidates.json",
            "output/validation.json",
            "output/final.json",
        ],
        workflow_contract={
            "producer": "output/extractor.py",
        },
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["workflow_contract"]["required_outputs"] == result["required_outputs"]
    assert saved["workflow_contract"]["required_outputs"] == result["required_outputs"]


def test_update_extraction_context_normalizes_dict_notes_without_storing_keys() -> None:
    context = FakeToolContext()

    result = update_extraction_context(
        task_understanding="Extract jobs from the target page.",
        final_goal="Extract validated jobs.",
        initial_plan={
            "step_1": "save the page to a workspace artifact",
            "step_2": "load the job scraping skill",
        },  # type: ignore[arg-type]
        observations={
            "page_saved": True,
            "page_id": "page_123",
            "signals": ["job-card markers", "detail-style links"],
        },  # type: ignore[arg-type]
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["status"] == "success"
    assert saved["initial_plan"] == [
        "save the page to a workspace artifact",
        "load the job scraping skill",
    ]
    assert "step_1" not in saved["initial_plan"]
    assert saved["observations"][0] == "page_saved: True"
    assert saved["observations"][1] == "page_id: page_123"
    assert saved["observations"][2].startswith("signals:")


def test_update_extraction_context_accepts_scalar_last_result() -> None:
    context = FakeToolContext()

    result = update_extraction_context(
        last_result="skills loaded",
        tool_context=context,  # type: ignore[arg-type]
    )

    saved = context.state[SESSION_EXTRACTION_CONTEXT_STATE_KEY]
    assert result["status"] == "success"
    assert saved["last_result"] == {"value": "skills loaded"}


def test_fetch_page_limits_large_tool_output(monkeypatch) -> None:
    monkeypatch.setattr(adk_tools, "fetch_page_content", lambda url, timeout=20: "abcdef")

    result = fetch_page("https://example.com", max_chars=3)

    assert result["content"] == "abc"
    assert result["content_length"] == 6
    assert result["returned_length"] == 3
    assert result["truncated"] is True


def test_fetch_page_to_workspace_stores_full_content_without_returning_it(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(adk_tools, "fetch_page_content", lambda url, timeout=20: "abcdef")
    monkeypatch.setattr(adk_tools, "PAGE_WORKSPACE_ROOT", tmp_path)

    result = asyncio.run(fetch_page_to_workspace("https://example.com/jobs"))

    assert result["status"] == "success"
    assert result["content_length"] == 6
    assert result["content_bytes"] == 6
    assert result["estimated_tokens"] == 2
    assert "content" not in result
    artifact_path = Path(result["artifact_path"])
    assert artifact_path.is_absolute()
    assert artifact_path.read_text(encoding="utf-8") == "abcdef"
    assert result["recommended_next"] == "inspect_direct_preview"


def test_fetch_page_to_workspace_returns_structured_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_fetch(url: str, timeout: int = 20) -> str:
        raise RuntimeError("dns failure")

    monkeypatch.setattr("job_scraper.adk_tools.fetch_page_content", fake_fetch)

    result = asyncio.run(fetch_page_to_workspace("https://unknown.invalid"))

    assert result["status"] == "error"
    assert result["recommended_next"] == "report_blocker"
    assert result["content_bytes"] == 0
    assert "dns failure" in result["error"]


def test_fetch_page_to_workspace_saves_adk_artifacts_when_context_is_available(monkeypatch, tmp_path: Path) -> None:
    class FakeToolContext:
        def __init__(self) -> None:
            self.saved: dict[str, genai_types.Part] = {}

        async def save_artifact(
            self,
            filename: str,
            artifact: genai_types.Part,
            custom_metadata: dict[str, object] | None = None,
        ) -> int:
            self.saved[filename] = artifact
            return len(self.saved) - 1

    context = FakeToolContext()
    monkeypatch.setattr(adk_tools, "fetch_page_content", lambda url, timeout=20: "<html><a href='/jobs/ml'>ML</a></html>")
    monkeypatch.setattr(adk_tools, "PAGE_WORKSPACE_ROOT", tmp_path)

    result = asyncio.run(fetch_page_to_workspace("https://example.com/jobs", tool_context=context))

    assert result["artifact"]["artifact_name"] == f"pages__{result['page_id']}__page.html"
    assert result["metadata_artifact"]["artifact_name"] == f"pages__{result['page_id']}__metadata.json"
    assert "content" not in result
    assert result["signals"]["job_like_links"] == 1
    assert set(context.saved) == {
        f"pages__{result['page_id']}__page.html",
        f"pages__{result['page_id']}__metadata.json",
    }


def test_run_sandbox_agent_resolves_page_ids_to_workspace_files(monkeypatch, tmp_path: Path) -> None:
    page_dir = tmp_path / "page_123"
    page_dir.mkdir()
    page_file = page_dir / "page.html"
    page_file.write_text("<html>jobs</html>", encoding="utf-8")
    monkeypatch.setattr(adk_tools, "PAGE_WORKSPACE_ROOT", tmp_path)

    captured: dict[str, object] = {}

    def fake_run_generic_sandbox_agent(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "output_schema": kwargs["output_schema"], "result": {}}

    monkeypatch.setattr(adk_tools, "run_generic_sandbox_agent", fake_run_generic_sandbox_agent)

    result = adk_tools.run_sandbox_agent(task="extract jobs", page_ids=["page_123"])

    assert result["status"] == "success"
    assert captured["workspace_files"] == [
        {"source_path": str(page_file), "sandbox_path": "page_123.html"}
    ]


def test_persist_sandbox_job_extraction_validates_before_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    extraction = {
        "source": {"source_url": "https://example.com/jobs", "source_name": "Example Jobs"},
        "jobs": [
            {
                "title": "Machine Learning Engineer",
                "company_name": "Acme",
                "job_url": "https://example.com/jobs/ml",
                "description_text": "Python and model deployment.",
            }
        ],
        "crawl": {"discovered_count": 1, "candidate_count": 1, "relevant_count": 1},
    }

    result = persist_sandbox_job_extraction(extraction, db_path=str(db_path))
    query_result = query_jobs(keyword="Machine Learning", db_path=str(db_path))

    assert result["status"] == "success"
    assert result["written_count"] == 1
    assert query_result["count"] == 1


def test_persist_sandbox_job_extraction_preserves_job_level_source_and_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    extraction = {
        "jobs": [
            {
                "title": "AI Developer Engineer Consultant (Python, LLM, NLP)",
                "company_name": "Switch Supply Pty Ltd",
                "job_url": "https://itviec.com/it-jobs/example",
                "source_name": "ITviec",
                "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi",
                "description": "AI-related role from ITviec listing page.",
                "location": "Ha Noi, Vietnam",
                "ai_ml_score": 95,
                "startup_score": 75,
                "overall_score": 86,
                "relevance_flag": True,
            }
        ],
        "crawl": {"candidate_count": 1, "relevant_count": 1},
    }

    result = persist_sandbox_job_extraction(extraction, db_path=str(db_path))
    query_result = query_jobs(keyword="LLM", relevant_only=True, source_name="ITviec", db_path=str(db_path))

    assert result["status"] == "success"
    assert result["written_count"] == 1
    assert query_result["count"] == 1
    assert query_result["items"][0]["source_name"] == "ITviec"
    assert query_result["items"][0]["overall_score"] == 86


def test_persist_sandbox_job_extraction_accepts_job_extraction_wrapper(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    extraction = {
        "job_extraction": {
            "source_name": "ITviec",
            "source_url": "https://itviec.com/it-jobs/ai-engineer/ha-noi",
            "jobs": [
                {
                    "title": "AI Developer Engineer Consultant",
                    "company_name": "Switch Supply Pty Ltd",
                    "job_url": "https://itviec.com/it-jobs/ai-developer-engineer-consultant-python-llm-nlp-switch-supply-pty-ltd-2549",
                    "description": "Python, LLM, NLP role from ITviec listing page.",
                    "location": "Ha Noi",
                    "ai_ml_score": 0.86,
                    "startup_score": 0.72,
                    "overall_score": 0.79,
                    "relevance_flag": True,
                }
            ],
        }
    }

    result = persist_sandbox_job_extraction(extraction, db_path=str(db_path))
    query_result = query_jobs(keyword="AI Developer", relevant_only=True, source_name="ITviec", db_path=str(db_path))

    assert result["status"] == "success"
    assert result["written_count"] == 1
    assert result["validated_count"] == 1
    assert query_result["count"] == 1
    assert query_result["items"][0]["source_name"] == "ITviec"


def test_promote_sandbox_extraction_reads_final_json_and_persists_all_jobs(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    monkeypatch.setattr(adk_tools, "DEFAULT_SANDBOX_APP_ROOT", app_root)

    audit_id = "sandbox_run_test"
    SandboxRegistry(app_root).save(
        SandboxSessionRecord(
            user_id="user",
            session_id="local",
            audit_id=audit_id,
            container_id="container",
            workspace_path=str(workspace),
            status="finalized",
            mode="workflow",
        )
    )
    (output / "final.json").write_text(
        json.dumps(
            {
                "status": "success",
                "result": {
                    "count": 2,
                    "jobs": [
                        {
                            "title": "Machine Learning Engineer",
                            "company_name": "Acme",
                            "job_url": "https://example.com/jobs/ml",
                            "description_text": "Python and model deployment.",
                        },
                        {
                            "title": "AI Platform Engineer",
                            "company_name": "Beta",
                            "job_url": "https://example.com/jobs/ai-platform",
                            "description_text": "LLM platform role.",
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    result = promote_sandbox_extraction(audit_id=audit_id, db_path=str(tmp_path / "jobs.db"))
    query_result = query_jobs(keyword="Engineer", db_path=str(tmp_path / "jobs.db"))

    assert result["status"] == "success"
    assert result["audit_id"] == audit_id
    assert result["written_count"] == 2
    assert result["validated_count"] == 2
    assert result["source"] == "sandbox_final_json"
    assert result["artifact_handles"]["final"] == "output/final.json"
    assert query_result["count"] == 2


def test_promote_sandbox_extraction_requires_finalized_sandbox(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(adk_tools, "DEFAULT_SANDBOX_APP_ROOT", app_root)

    audit_id = "sandbox_run_test"
    SandboxRegistry(app_root).save(
        SandboxSessionRecord(
            user_id="user",
            session_id="local",
            audit_id=audit_id,
            container_id="container",
            workspace_path=str(workspace),
            status="running",
            mode="workflow",
        )
    )

    result = promote_sandbox_extraction(audit_id=audit_id, db_path=str(tmp_path / "jobs.db"))

    assert result["status"] == "error"
    assert result["written_count"] == 0
    assert "not finalized" in result["error"]


def test_persist_sandbox_job_extraction_requires_payload(tmp_path: Path) -> None:
    result = persist_sandbox_job_extraction(db_path=str(tmp_path / "jobs.db"))

    assert result["status"] == "error"
    assert result["written_count"] == 0
    assert "missing extraction payload" in result["error"]
    assert "retry persist_sandbox_job_extraction" in result["suggested_next"]


def test_persist_sandbox_job_extraction_rejects_invalid_payload(tmp_path: Path) -> None:
    result = persist_sandbox_job_extraction(
        {"jobs": [{"title": "", "job_url": "https://example.com/jobs/ml"}]},
        db_path=str(tmp_path / "jobs.db"),
    )

    assert result["status"] == "error"
    assert "title" in result["error"]
    assert "Use this validation error as the next repair target" in result["suggested_next"]


def test_persist_sandbox_job_extraction_rejects_sampled_payload_with_declared_count(tmp_path: Path) -> None:
    result = persist_sandbox_job_extraction(
        {
            "count": 20,
            "jobs": [
                {
                    "title": "AI Developer Engineer Consultant",
                    "company_name": "Switch Supply Pty Ltd",
                    "job_url": "https://itviec.com/it-jobs/ai-developer-engineer-consultant-python-llm-nlp-switch-supply-pty-ltd-2549",
                    "description": "Python, LLM, NLP role from ITviec listing page.",
                }
            ],
        },
        db_path=str(tmp_path / "jobs.db"),
    )

    assert result["status"] == "error"
    assert result["written_count"] == 0
    assert result["validated_count"] == 0
    assert "count=20, jobs_length=1" in result["error"]
    assert "complete sandbox final result payload" in result["error"]


def test_persist_sandbox_job_extraction_rejects_empty_wrapper(tmp_path: Path) -> None:
    result = persist_sandbox_job_extraction({"job_extraction": {"jobs": []}}, db_path=str(tmp_path / "jobs.db"))

    assert result["status"] == "error"
    assert result["written_count"] == 0
    assert result["validated_count"] == 0
    assert "contains no jobs" in result["error"]
