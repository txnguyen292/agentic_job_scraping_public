# Sandbox-Agent Page Workspace Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Superseded direction:** As of 2026-04-28, do not implement the nested OpenAI Agents SDK `SandboxAgent` path as the next step. Use [Skill-script sandbox terminal](04-skill-script-sandbox-terminal.md) instead. This file remains as historical design context for the page-workspace and audit-boundary reasoning.

**Goal:** Build a context-safe scraper workflow where the ADK job scraper fetches large job pages, stores full artifacts outside the main context, delegates messy page inspection to a generic OpenAI Agents SDK `SandboxAgent`, and receives only final structured extraction output for persistence and dashboarding.

**Architecture:** The ADK main agent remains the product orchestrator. It fetches/renders pages with Scrapling, saves full HTML as page workspace artifacts, then calls one generic ADK `FunctionTool`: `run_sandbox_agent`. That tool creates a sandbox worker with a filesystem, terminal, task message, and mounted variables/artifacts. The sandbox worker can iterate privately over terminal output and page files, but only its final structured result is returned to the main ADK context.

**LLM Boundary:** The sandbox worker is still an LLM-driven `SandboxAgent`. It may make normal host-mediated model calls while reasoning over mounted files and shell outputs. `SandboxPolicy.allow_llm_calls` is fixed to `true` in v1 because the RLM-style worker needs its own reasoning loop. The no-network policy applies to Docker/container egress and prevents the sandbox terminal from browsing or fetching remote URLs; page fetching stays in the main ADK layer.

**Tech Stack:** Python 3.13, Google ADK, LiteLLM, OpenAI Agents SDK `SandboxAgent`, Docker sandbox client, Scrapling, SQLite, Typer/Rich/Loguru, pytest, Streamlit.

**Integration Boundary:** Keep OpenAI Agents SDK customization narrow. `src/sandbox_page_analyst/` owns the sandbox agent and OpenAI SDK boundary. `src/job_scraper/` owns the ADK job scraper app and calls the sandbox package through project-owned wrapper functions and data shapes.

---

## Current Problem

The scraper can fetch ITviec through ADK Web, and Scrapling can return full page HTML. But large page bodies can exceed context limits. Returning truncated HTML hides job cards, and returning multiple inspection chunks still pollutes ADK session history cumulatively.

Artifacts alone are not enough if the main ADK agent repeatedly reads artifact windows. The right boundary is a nested sandbox worker: the main agent sends a task and page artifacts to a sandbox agent, the sandbox agent performs iterative inspection in its own turn loop, and the main ADK session receives only final compact output.

## Desired Runtime Shape

```text
ADK main job_scraper agent
  -> load job-listing-scout skill
  -> fetch_page_to_workspace(url)
       stores full HTML as page artifact, returns page_id only
  -> run_sandbox_agent(task, variables, workspace_files, output_schema)
       creates OpenAI Agents SDK SandboxAgent
       materializes page.html and variables.json
       sandbox worker uses shell/filesystem privately
       returns final JSON only
  -> upsert_job(...)
  -> record_crawl_run(...)
  -> query_jobs(...)
```

## Design Rules

- Do not return full HTML to the ADK main agent.
- Do not expose sandbox intermediate stdout/stderr as ADK tool outputs.
- Define sandbox final-output schemas before implementing the worker loop.
- The main agent supplies the sandbox task message.
- The sandbox worker is generic; it should not hardcode job-scraping behavior.
- Runtime policy is enforced by the wrapper, not by prompt text alone.
- The sandbox should receive mounted artifacts and JSON variables, not direct internet access.
- The first implementation should support Docker-backed local sandboxing.
- The ADK tool should return only compact final JSON plus artifact handles for audit/debug.
- Do not spread OpenAI Agents SDK types across the codebase; keep them behind `src/sandbox_page_analyst/`.
- Prefer simple wrapper data classes/dicts over custom tracing processors or deep SDK hooks unless needed later.
- Keep the existing deterministic Greenhouse/Lever pipeline, SQLite storage, and Streamlit dashboard.
- Keep `allowed-tools` as the single runtime tool contract for the ADK skill.

## Sandbox Output Contract

The sandbox worker must return a small, typed final result. This is the only sandbox output that enters the ADK main context.

The generic wrapper result should always have this outer shape:

```json
{
  "status": "success",
  "output_schema": "job_extraction",
  "summary": "Found 8 candidate jobs, 3 likely AI/ML/security roles.",
  "result": {},
  "audit": {
    "audit_id": "sandbox_run_20260426_153000_abc123",
    "policy_artifact": "data/sandbox_runs/sandbox_run_20260426_153000_abc123/policy.json",
    "inputs_artifact": "data/sandbox_runs/sandbox_run_20260426_153000_abc123/inputs.json",
    "trace_artifact": "data/sandbox_runs/sandbox_run_20260426_153000_abc123/trace.jsonl",
    "final_output_artifact": "data/sandbox_runs/sandbox_run_20260426_153000_abc123/final.json",
    "warnings": []
  },
  "artifacts": [],
  "error": ""
}
```

For job extraction, `result` should follow this schema:

```json
{
  "source": {
    "source_url": "https://itviec.com/it-jobs/ha-noi",
    "source_name": "ITviec Hanoi",
    "page_id": "page_abc",
    "fetched_at": "2026-04-26T15:30:00+07:00"
  },
  "jobs": [
    {
      "title": "Machine Learning Engineer",
      "company_name": "Acme AI",
      "job_url": "https://example.com/jobs/ml-engineer",
      "location_raw": "Ha Noi",
      "employment_type": "",
      "posted_at": "",
      "salary_raw": "Up to 4000 USD",
      "description_text": "Compact text sufficient for scoring.",
      "tags": ["machine learning", "python"],
      "relevance_reason": "Title and tags mention machine learning.",
      "confidence": 0.87,
      "evidence": [
        {
          "file": "page.html",
          "locator": "job card 3",
          "text": "Machine Learning Engineer - Acme AI - Ha Noi"
        }
      ]
    }
  ],
  "selectors": {
    "job_card": "article.job",
    "title": "a",
    "company": ".company",
    "location": ".location"
  },
  "crawl": {
    "discovered_count": 8,
    "candidate_count": 8,
    "relevant_count": 3,
    "blocked": false,
    "blocker": ""
  },
  "warnings": []
}
```

Output constraints:

- `jobs[*].description_text` must be compact, not raw HTML.
- `jobs[*].evidence[*].text` must be short, ideally under 300 characters.
- `selectors` are optional but useful for follow-up crawls.
- `warnings` should include uncertainty, missing fields, pagination clues, or parse risks.
- The sandbox must not return command transcripts, full HTML windows, stack traces, or large debug dumps.
- If extraction fails, return `status: "error"` or `crawl.blocked: true` with a short blocker and evidence summary.

## Sandbox Audit Contract

Every sandbox run should produce an audit folder. The ADK main context receives only the `audit` object with handles and warnings. Full traces stay outside the main context.

Audit folder layout:

```text
data/sandbox_runs/<audit_id>/
  policy.json
  inputs.json
  trace.jsonl
  final.json
  raw/
```

Required audit files:

- `policy.json`: sandbox image, network mode, timeout, max turns, output caps, debug/raw-retention flags.
- `inputs.json`: task hash, variables hash, mounted file paths, mounted file hashes, source page IDs, source content lengths.
- `trace.jsonl`: event-level metadata for sandbox turns, tool calls, command executions, file reads/writes, and finalization.
- `final.json`: exact final `SandboxAgentResult` returned to ADK.
- `raw/`: optional debug-only directory for full transcripts, stdout/stderr, scratch files, and generated scripts.

Trace event shape:

```json
{
  "ts": "2026-04-26T15:30:01+07:00",
  "event": "sandbox_command",
  "turn_index": 2,
  "command_index": 4,
  "command": "python inspect_jobs.py",
  "exit_code": 0,
  "duration_ms": 412,
  "stdout_bytes": 18342,
  "stderr_bytes": 0,
  "stdout_sha256": "abc...",
  "stderr_sha256": "def...",
  "stdout_returned_chars": 4000,
  "stderr_returned_chars": 0,
  "stdout_truncated": true,
  "stderr_truncated": false,
  "stdout_path": "commands/004.stdout.txt",
  "stderr_path": "commands/004.stderr.txt"
}
```

Audit rules:

- Always record policy, inputs, final output, and trace metadata.
- Hash mounted input files before the sandbox runs.
- Hash stdout/stderr when command metadata is available.
- Store bounded previews only; do not store full HTML or full command output in `trace.jsonl`.
- Persist local audit artifacts and scratch files for every sandbox run; return compact handles to ADK.
- Redact obvious secrets and environment values from previews.
- Return only `audit_id`, audit artifact paths, and audit warnings to ADK.

## Sandbox Progress And Tracing

OpenAI Agents SDK exposes two useful observability surfaces for the nested sandbox worker:

- `Runner.run_streamed(...)` plus `result.stream_events()` for live semantic progress events.
- Built-in tracing with traces/spans and optional custom `TracingProcessor` hooks for deeper span-level inspection.

Use streaming first. It is the practical equivalent of ADK-style progress events for our use case and can be written directly into `trace.jsonl` while the sandbox run is still active.

Required v1 progress behavior:

- Replace the sandbox worker's direct `Runner.run(...)` call with `Runner.run_streamed(...)`.
- Consume `result.stream_events()` until the stream finishes.
- Write compact progress records to `trace.jsonl` for:
  - `agent_updated_stream_event`
  - `run_item_stream_event` names such as `tool_called`, `tool_output`, `reasoning_item_created`, and `message_output_created`
  - selected raw response lifecycle events when useful, such as response start/end, without token deltas or full model text.
- Include run state in trace records when available: `current_turn`, `max_turns`, `is_complete`, and event name/type.
- Do not write full raw HTML, full terminal output, full model messages, or token deltas to `trace.jsonl`.
- Keep the ADK tool response compact; progress events stay in the audit artifact.
- Configure OpenAI Agents SDK hosted tracing for each sandbox run with a deterministic `openai_trace_id`, workflow name, group id, and trace metadata.
- Persist `openai_trace_id`, `openai_trace_workflow`, `openai_trace_group_id`, and dashboard lookup hint in the sandbox audit object.
- Call `flush_traces()` after each sandbox run so hosted traces appear promptly in OpenAI Platform `Logs > Traces`.

Span-level tracing is a follow-up. If streamed progress is still too coarse, add a local `TracingProcessor` that persists sanitized span start/end records for agent, generation, function/tool, guardrail, and custom spans. Do not replace streaming progress with custom spans unless there is a concrete debugging need.

## Sandbox Skill Protocol

The sandbox worker should not be a fully free-form agent. It should run from a repo-local sandbox skill/protocol that defines required stages, output files, validation rules, and self-improvement behavior.

Initial sandbox skill layout:

```text
skills/sandbox-page-analyst/
  SKILL.md
  protocol.md
  references/
    static-html-job-board.md
    embedded-json-job-board.md
    json-ld-job-postings.md
    nextjs-or-nuxt-hydration.md
    paginated-listing-pages.md
    detail-page-fanout.md
    blocked-or-script-only-pages.md
  schemas/
    page_profile.schema.json
    extraction_strategy.schema.json
    candidates.schema.json
    validation.schema.json
    skill_patch.schema.json
    reference_proposal.schema.json
  scripts/
    validate_outputs.py
```

Required workflow inside the sandbox:

```text
1. Profile page
   write output/page_profile.json

2. Discover extraction strategy
   write output/extraction_strategy.json

3. Extract candidates
   write output/candidates.json

4. Validate candidates
   write output/validation.json

5. Propose skill improvements if useful
   optionally write output/skill_patch.json
   optionally write output/reference_proposal.md
   optionally write output/reference_proposal.json

6. Return final structured result
   only after validation passes or a clear blocker is documented
```

Reliability rules:

- The wrapper should reject or mark `needs_review` if required output files are missing.
- The wrapper should validate every stage output against schemas.
- Candidate counts in `candidates.json`, `validation.json`, and final output must agree or warnings must explain the mismatch.
- Every persisted job candidate must have at least `title` and `job_url`.
- The sandbox agent may choose its commands and scripts, but it may not skip protocol stages.
- `SKILL.md` should define generic page-analysis steps only; layout-specific tactics belong in `references/`.
- The sandbox agent should choose at most the relevant reference files after profiling the page.
- Page/site-specific discoveries should become `reference_proposal` or `skill_patch` proposals, not silent prompt drift.

Self-improvement rules:

- The sandbox agent may propose updates to `skills/sandbox-page-analyst/SKILL.md` or helper scripts.
- The sandbox agent may propose new layout references based on successful extractions.
- Proposed skill updates must be written as structured `output/skill_patch.json`.
- Proposed layout references must be written as `output/reference_proposal.md` plus `output/reference_proposal.json`.
- The sandbox worker must not directly modify repo skill files.
- A human must approve proposals before they become real repo references or skill edits.
- The ADK main agent may surface proposals and audit handles, but it must not auto-apply them.
- Accepted reference proposals become files under `skills/sandbox-page-analyst/references/`.
- Each accepted skill update should be audited and tested with fixture pages before becoming default behavior.
- Local sandbox audit artifact persistence does not require an extra user confirmation.

Human approval flow:

```text
Sandbox worker
  -> output/reference_proposal.md
  -> output/reference_proposal.json
  -> output/skill_patch.json

ADK main agent
  -> returns final jobs
  -> reports proposal_available: true
  -> includes audit_id and proposal artifact paths

Human reviewer
  -> reviews proposal and audit evidence
  -> approves, edits, or rejects

Implementation step after approval
  -> copies/edits proposal into skills/sandbox-page-analyst/references/<layout>.md
  -> updates reference index if needed
  -> adds fixture/protocol tests
  -> appends context lineage
```

## File Structure

- Create: `src/job_scraper/page_workspace.py`
- Purpose: Store page artifacts, page IDs, metadata, and local artifact paths.
- Create: `src/sandbox_page_analyst/runtime.py`
- Purpose: Own the OpenAI Agents SDK adapter, generic sandbox worker, Docker run config, output schema handling, and safety caps.
- Create: `src/job_scraper/sandbox_audit.py`
- Purpose: Create audit IDs, write policy/input/trace/final audit artifacts, hash files and outputs, and redact previews.
- Create: `skills/sandbox-page-analyst/SKILL.md`
- Purpose: Define the sandbox worker protocol and stable behavior contract.
- Create: `skills/sandbox-page-analyst/protocol.md`
- Purpose: Provide the staged page-analysis workflow used inside the sandbox.
- Create: `skills/sandbox-page-analyst/references/*.md`
- Purpose: Provide detailed step-by-step tactics for common website layouts without bloating the generic skill.
- Create: `skills/sandbox-page-analyst/schemas/*.schema.json`
- Purpose: Validate page profile, extraction strategy, candidates, validation, and skill patch proposals.
- Create: `skills/sandbox-page-analyst/scripts/validate_outputs.py`
- Purpose: Validate required sandbox output files before finalization.
- Modify: `src/job_scraper/adk_tools.py`
- Purpose: Expose `fetch_page_to_workspace`, `render_page_to_workspace`, and `run_sandbox_agent` to ADK.
- Modify: `src/job_scraper/agent.py`
- Purpose: Register new tools and keep skill `allowed-tools` authoritative.
- Modify: `skills/job-listing-scout/SKILL.md`
- Purpose: Teach the main ADK agent to fetch pages, delegate page analysis to the generic sandbox worker, persist relevant jobs, and avoid direct HTML inspection.
- Modify: `docs/02-adk-job-listing-scout.md`
- Purpose: Document the parent-agent/sandbox-worker context boundary.
- Create: `reports/index.md`
- Purpose: Human-facing index for scraper measurement reports.
- Modify: `plans/reports.md`
- Purpose: Track the sandbox token report template and reviewed usage summaries.
- Create or modify: `reports/`
- Purpose: Store generated physical report artifacts only when needed.
- Test: `tests/test_page_workspace.py`
- Purpose: Verify page storage returns handles and never returns full content.
- Test: `tests/test_sandbox_agent.py`
- Purpose: Verify sandbox-task payload construction, final-output parsing, policy defaults, and mock worker behavior.
- Test: `tests/test_sandbox_audit.py`
- Purpose: Verify audit folder layout, trace JSONL writes, hashing, preview caps, redaction, and final output artifact writes.
- Test: `tests/test_sandbox_protocol.py`
- Purpose: Verify sandbox protocol files exist, schemas validate fixtures, and missing required outputs are rejected.
- Test fixture: `tests/fixtures/static_job_board.html`
- Purpose: Static HTML job-board page used as the first controlled sandbox extraction target.
- Expected fixture: `tests/fixtures/static_job_board.expected.json`
- Purpose: Canonical `job_extraction` output for the static HTML fixture.
- Test fixture: `tests/fixtures/itviec_ai_engineer_ha_noi.html`
- Purpose: Frozen ITviec AI Engineer Hanoi search page captured from the live site on 2026-04-27.
- Expected fixture: `tests/fixtures/itviec_ai_engineer_ha_noi.expected.json`
- Purpose: Canonical `job_extraction` output for the frozen ITviec page, including 20 page-1 jobs.
- Note: `plans/notes/01-itviec-manual-extraction-playbook.md`
- Purpose: Documents how the expected ITviec output was manually extracted and checks the proposed sandbox protocol against that process.
- Test: `tests/test_adk_tools.py`
- Purpose: Verify ADK tool outputs are compact and final-output-only.
- Test: `tests/test_adk_agent.py`
- Purpose: Verify tool names are exposed after skill activation.

---

## Task 1: Add OpenAI Agents SDK Dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] Add `openai-agents` to project dependencies.
- [ ] Confirm the installed version includes `agents.sandbox.SandboxAgent`, `SandboxRunConfig`, `Manifest`, `DockerSandboxClient`, and sandbox capabilities.
- [ ] Run:

```bash
uv sync
uv run python - <<'PY'
from agents.sandbox import SandboxAgent, SandboxRunConfig, Manifest
from agents.sandbox.sandboxes.docker import DockerSandboxClient
print(SandboxAgent, SandboxRunConfig, Manifest, DockerSandboxClient)
PY
```

Expected:

```text
Imports succeed.
```

---

## Task 2: Page Workspace Artifacts

**Files:**
- Create: `src/job_scraper/page_workspace.py`
- Test: `tests/test_page_workspace.py`

- [ ] Implement `PageWorkspace.store_page(url, content, fetch_mode)` so it writes full HTML to a local page artifact path and returns only metadata.
- [ ] Include `page_id`, `url`, `fetch_mode`, `content_length`, `title`, and `artifact_path`.
- [ ] Do not include `content` in the returned payload.
- [ ] Implement `PageWorkspace.load_page(page_id)` for internal tool use.
- [ ] Add tests that assert full HTML is not returned by `store_page`.

Suggested return shape:

```json
{
  "status": "success",
  "page_id": "page_...",
  "url": "https://example.com/jobs",
  "fetch_mode": "fetch",
  "content_length": 498000,
  "title": "Jobs",
  "artifact_path": "data/page_workspace/page_.../page.html"
}
```

Verification:

```bash
uv run pytest tests/test_page_workspace.py -q
```

---

## Task 3: Generic Sandbox Worker Adapter

**Files:**
- Create: `src/sandbox_page_analyst/runtime.py`
- Create: `skills/sandbox-page-analyst/SKILL.md`
- Create: `skills/sandbox-page-analyst/protocol.md`
- Create: `skills/sandbox-page-analyst/references/*.md`
- Create: `skills/sandbox-page-analyst/schemas/*.schema.json`
- Create: `skills/sandbox-page-analyst/scripts/*.py`
- Test: `tests/test_sandbox_agent.py`
- Test: `tests/test_sandbox_audit.py`
- Test: `tests/test_sandbox_protocol.py`

- [x] Define a generic input model for sandbox runs:

```python
class SandboxWorkspaceFile(BaseModel):
    source_path: str
    sandbox_path: str


class SandboxPolicy(BaseModel):
    timeout_seconds: int = 120
    max_turns: int = 8
    max_final_output_chars: int = 20_000
    model: str = "gpt-5.4-mini"
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "high"
    allow_llm_calls: Literal[True] = True
    persist_artifacts: Literal[True] = True
    network: Literal["disabled", "default"] = "disabled"
    docker_image: str = "python:3.13-slim"
    debug_audit: bool = False
    require_protocol_outputs: bool = True
    validate_before_return: bool = True
    validate_before_persist: bool = True
```

- [x] Define a generic final result shape:

```python
class SandboxAuditRef(BaseModel):
    audit_id: str
    policy_artifact: str
    inputs_artifact: str
    trace_artifact: str
    final_output_artifact: str
    warnings: list[str] = Field(default_factory=list)


class SandboxAgentResult(BaseModel):
    status: Literal["success", "error"]
    output_schema: str = "generic"
    result: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    audit: SandboxAuditRef | None = None
    artifacts: list[str] = Field(default_factory=list)
    error: str = ""
```

- [x] Define job-extraction final output models:

```python
class SandboxEvidence(BaseModel):
    file: str = ""
    locator: str = ""
    text: str = Field(default="", max_length=500)


class SandboxExtractedJob(BaseModel):
    title: str
    company_name: str = ""
    job_url: str = ""
    location_raw: str = ""
    employment_type: str = ""
    posted_at: str = ""
    salary_raw: str = ""
    description_text: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list)
    relevance_reason: str = ""
    confidence: float = 0.0
    evidence: list[SandboxEvidence] = Field(default_factory=list)


class SandboxJobExtractionResult(BaseModel):
    source: dict[str, Any] = Field(default_factory=dict)
    jobs: list[SandboxExtractedJob] = Field(default_factory=list)
    selectors: dict[str, str] = Field(default_factory=dict)
    crawl: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
```

- [x] Create `skills/sandbox-page-analyst/SKILL.md` with generic page-analysis behavior, no site-specific hardcoding, and final-output-only rules.
- [x] Create `protocol.md` describing the required profile/strategy/candidates/validation/final stages.
- [x] Create reference docs for layout-specific tactics:
  - `references/static-html-job-board.md`
  - `references/embedded-json-job-board.md`
  - `references/json-ld-job-postings.md`
  - `references/nextjs-or-nuxt-hydration.md`
  - `references/paginated-listing-pages.md`
  - `references/detail-page-fanout.md`
  - `references/blocked-or-script-only-pages.md`
- [x] Add reference selection rules to `SKILL.md`: profile first, choose relevant references second, do not load unrelated tactics.
- [x] Create JSON schemas for:
  - `page_profile.schema.json`
  - `extraction_strategy.schema.json`
  - `candidates.schema.json`
  - `validation.schema.json`
  - `skill_patch.schema.json`
  - `reference_proposal.schema.json`
- [x] Remove deterministic `scripts/html_probe.py` from the required workflow; large and intermediate outputs are persisted, then the agent chooses narrower inspection commands from artifact paths and observations.
- [x] Create `scripts/validate_outputs.py` to validate required output files and schema consistency inside the sandbox.
- [x] Implement `SandboxAuditWriter` in `src/sandbox_page_analyst/runtime.py` for the v1 narrow SDK boundary.
- [x] `SandboxAuditWriter` should create `data/sandbox_runs/<audit_id>/policy.json`, `inputs.json`, `trace.jsonl`, and `final.json`.
- [x] `SandboxAuditWriter` should support SHA-256 hashes and optional debug raw artifact storage.
- [x] Implement `run_generic_sandbox_agent(task, variables, workspace_files, policy)`.
- [x] Materialize `variables.json` into the sandbox workspace.
- [x] Materialize provided files into the sandbox workspace using `Manifest`.
- [x] Materialize the sandbox skill/protocol files into the sandbox workspace through the OpenAI SDK `Skills` capability.
- [x] Materialize the sandbox references into the workspace through lazy `Skills` loading; reference selection happens after `page_profile`.
- [x] Start the audit record before invoking the OpenAI sandbox worker.
- [x] Build the Docker sandbox client through a policy wrapper:
  - create `NoNetworkContainerCollection`
  - create `NoNetworkDockerClient`
  - wrap `docker.from_env()` when `SandboxPolicy.network == "disabled"`
  - inject `network_disabled=True` and `network_mode="none"` into `containers.create(...)`
- [x] Configure the sandbox worker as a generic agent with `Shell`, `Filesystem`, and `Compaction`.
- [x] Use `gpt-5.4-mini` for the sandbox worker for v1, with `reasoning_effort="high"`.
- [x] Replace `Runner.run(...)` with `Runner.run_streamed(...)` inside `_run_openai_sandbox_worker`.
- [x] Consume `result.stream_events()` until completion and keep `result.is_complete` semantics intact.
- [x] Persist sanitized stream progress events to `trace.jsonl` while the sandbox run is active.
- [x] Record `current_turn`, `max_turns`, event type/name, and compact item metadata when available.
- [x] On `MaxTurnsExceeded`, ensure the trace shows the last streamed event and current turn before final error.
- [x] Unit-test streamed sandbox progress with a fake streaming result.
- [x] Configure OpenAI hosted trace metadata through `RunConfig` and persist the trace lookup fields in `final.json`.
- [x] Flush OpenAI traces after each sandbox run.
- [ ] Keep custom `TracingProcessor` span export as a follow-up, not the v1 progress path.
- [ ] Run an egress smoke check inside the sandbox after startup when `network == "disabled"`.
- [ ] Record the egress smoke check result in `trace.jsonl`.
- [x] Require the worker to return structured output only.
- [x] Require the worker to produce protocol files under `output/`.
- [ ] Run or invoke `scripts/validate_outputs.py` before accepting the worker result when feasible.
- [x] Validate final output against `SandboxAgentResult`.
- [x] When `output_schema == "job_extraction"`, validate `result` against `SandboxJobExtractionResult`.
- [x] Persist only schema-valid final output; run the same validation gate immediately before database persistence so invalid results cannot be saved even if a caller bypasses the sandbox wrapper.
- [x] If protocol outputs are missing or invalid, return `status: "error"` or `needs_review` with audit handles, not a fake success.
- [x] Attach `SandboxAuditRef` to the final `SandboxAgentResult`.
- [ ] If `output/reference_proposal.*` exists, expose proposal artifact handles in `SandboxAgentResult.audit.warnings` or a compact `proposal_available` field; do not apply it.
- [x] Write final output to the audit folder before returning to ADK.
- [x] Always persist sandbox scratch artifacts locally and return compact audit handles only.
- [x] Cap the final serialized result before returning to ADK.
- [x] Keep all intermediate sandbox tool output inside the nested OpenAI Agents SDK run.

Generic sandbox instructions:

```text
You are a generic sandbox worker.
You receive a task, variables in variables.json, and workspace files.
Use the sandbox terminal and filesystem to inspect the workspace.
Do not browse the web or fetch remote URLs.
You may use normal SandboxAgent model reasoning across turns; the no-network rule applies to shell/container egress, not LLM calls.
Write any scratch files under output/.
Return only final structured JSON matching the requested schema.
Do not include intermediate terminal logs unless the requested schema explicitly asks for them.
```

Testing strategy:

- Unit-test payload construction without starting Docker. Done in `tests/test_sandbox_agent.py`.
- Unit-test sandbox skill/protocol files are mounted into the sandbox manifest.
- Unit-test sandbox reference index exists and expected reference docs are present.
- Unit-test `NoNetworkDockerClient` injects `network_disabled=True` and `network_mode="none"` while preserving other Docker client attributes. Done in `tests/test_sandbox_agent.py`.
- Unit-test that `SandboxPolicy.network == "default"` does not wrap or mutate Docker create kwargs.
- Unit-test required protocol output validation with fixture JSON files.
- Unit-test audit folder creation and JSONL trace appends. Done in `tests/test_sandbox_agent.py`.
- Unit-test that streamed SDK events become compact `trace.jsonl` progress records without raw text dumps.
- Unit-test that `MaxTurnsExceeded` writes a final error record and preserves prior progress records.
- Unit-test input file hashing and preview redaction.
- Mock `Runner.run_streamed` and assert only the final compact output plus audit handles are returned to ADK.
- Unit-test successful `job_extraction` schema validation. Done in `tests/test_sandbox_agent.py`.
- Unit-test rejection or truncation of oversized evidence/debug output.
- Unit-test that `SandboxAgentResult.audit` is returned while raw trace content is not embedded in ADK output.
- Unit-test that `skill_patch.json` proposals are returned as audit artifacts or warnings, not auto-applied.
- Unit-test that `reference_proposal.md` proposals are surfaced as pending human review, not copied into `references/`.
- Integration-test egress denial when `RUN_DOCKER_SANDBOX_TESTS=1`.
- Add an integration test marker for real Docker sandbox execution, but keep it skipped by default.

Verification:

```bash
uv run pytest tests/test_sandbox_agent.py tests/test_sandbox_audit.py tests/test_sandbox_protocol.py -q
```

---

## Task 4: ADK Tool Layer

**Files:**
- Modify: `src/job_scraper/adk_tools.py`
- Test: `tests/test_adk_tools.py`

- [x] Add page workspace storage rooted at `data/page_workspace`.
- [x] Add `fetch_page_to_workspace(url, timeout=20)` using Scrapling fetch.
- [x] Add `render_page_to_workspace(url, timeout=20)` using Scrapling dynamic/render path.
- [x] Add `run_sandbox_agent(task, variables, page_ids, workspace_files, output_schema, policy)`.
- [x] Resolve `page_ids` to workspace files like `page_123.html`.
- [x] Pass the main agent's task message directly into the sandbox worker.
- [x] Return only the sandbox worker's final compact result.
- [x] Require `output_schema` to be one of `generic` or `job_extraction` in v1.
- [x] Ensure no workspace fetch tool returns full HTML or intermediate command logs.

Suggested ADK tool call:

```json
{
  "task": "Inspect page.html and extract AI/ML job listings. Return JSON only.",
  "variables": {
    "source_url": "https://itviec.com/it-jobs/ha-noi",
    "target_schema": "job_extraction"
  },
  "page_ids": ["page_abc"],
  "output_schema": "job_extraction"
}
```

Verification:

```bash
uv run pytest tests/test_adk_tools.py -q
```

---

## Task 5: Agent Tool Contract Update

**Files:**
- Modify: `skills/job-listing-scout/SKILL.md`
- Modify: `src/job_scraper/agent.py`
- Test: `tests/test_adk_agent.py`

- [ ] Update skill frontmatter:

```yaml
---
name: job-listing-scout
description: Extract normalized job records from public job listing pages with a bias toward AI/ML startup roles.
allowed-tools: fetch_page_to_workspace render_page_to_workspace run_sandbox_agent upsert_job record_crawl_run query_jobs list_seed_references
---
```

- [x] Update `TOOL_REGISTRY` in `src/job_scraper/agent.py`.
- [x] Update root agent instructions to say:

```text
For large or unfamiliar pages, fetch or render the page into the page workspace, then delegate analysis to `run_sandbox_agent` with a precise task and target schema. Do not ask tools to return full HTML. Treat sandbox intermediate outputs as private to the sandbox worker; use only the final structured result to persist jobs.
```

- [x] Update tests to assert the new allowed tools are loaded and exposed after skill activation.

Verification:

```bash
uv run pytest tests/test_adk_agent.py -q
```

---

## Task 6: Skill Workflow Rewrite

**Files:**
- Modify: `skills/job-listing-scout/SKILL.md`
- Create: `skills/sandbox-page-analyst/SKILL.md`
- Modify: `docs/02-adk-job-listing-scout.md`

- [x] Rewrite the workflow around parent-agent delegation:

```markdown
## Workflow

1. Inspect seed references when the user asks for known ATS sources or source expansion.
2. For arbitrary job websites, call `fetch_page_to_workspace`.
3. If the fetched page is incomplete or script-only, call `render_page_to_workspace`.
4. Call `run_sandbox_agent` with:
   - a precise task written by the main agent
   - the relevant `page_id`
   - variables including source URL, source name, and target schema
   - `output_schema: "job_extraction"`
   - instructions to return final JSON only according to the sandbox output contract
5. Review the final sandbox result.
6. Normalize and persist relevant AI/ML/security/data jobs with `upsert_job`.
7. Record crawl metadata with `record_crawl_run`.
8. Query stored results with `query_jobs`.
9. Summarize saved jobs and blockers.
```

- [x] Add operating rules:

```markdown
## Sandbox Delegation Rules

- Do not request full page HTML.
- Do not ask for sandbox intermediate stdout/stderr.
- The sandbox task must be specific enough for the worker to act independently.
- Treat `run_sandbox_agent` as a final-output-only tool.
- If the sandbox result is insufficient, call it again with a narrower task rather than requesting raw logs.
- If the sandbox returns a `skill_patch` proposal, report it as a proposed improvement and do not treat it as already applied.
- Persist only validated normalized jobs.
```

- [x] Create `skills/sandbox-page-analyst/SKILL.md` with these sections:
  - purpose
  - available workspace files
  - required staged protocol
  - reference selection rules
  - output file requirements
  - final output rules
  - self-improvement proposal rules
  - human approval rules for reference proposals
  - security and no-network rules
- [x] Create sandbox references with detailed step-by-step tactics:
  - static HTML job cards
  - embedded JSON payloads
  - JSON-LD `JobPosting`
  - Next.js/Nuxt hydration blobs
  - pagination/list pages
  - detail-page fanout when list cards are thin
  - blocked/script-only pages
- [x] Ensure `SKILL.md` says references are optional aids selected after `page_profile`, not mandatory context to ingest every run.
- [x] Ensure `SKILL.md` says successful extraction can produce `output/reference_proposal.md`, but only human approval can promote it into `references/`.

- [ ] Update docs to explain:

```text
ADK context stores only page handles, final sandbox results, persisted job summaries, and crawl metadata. The nested sandbox worker owns noisy inspection traces.
The sandbox worker follows a repo-local skill/protocol and may propose skill patches, but accepted skill changes are reviewed outside the sandbox run.
```

Verification:

```bash
rg -n "run_sandbox_agent|sandbox worker|full HTML|intermediate stdout|fetch_page\\(|inspect_page" skills/job-listing-scout/SKILL.md docs/02-adk-job-listing-scout.md
```

---

## Task 7: Sandbox Extraction Smoke Test

**Files:**
- Fixture: `tests/fixtures/static_job_board.html`
- Fixture: `tests/fixtures/static_job_board.expected.json`
- Fixture: `tests/fixtures/itviec_ai_engineer_ha_noi.html`
- Fixture: `tests/fixtures/itviec_ai_engineer_ha_noi.expected.json`
- Test: `tests/test_sandbox_fixture.py`
- Test: `tests/test_itviec_expected_fixture.py`
- Test: `tests/test_extraction_compare.py`
- Test: `tests/test_sandbox_agent.py`
- Test: `tests/test_sandbox_audit.py`
- Test: `tests/test_adk_tools.py`

- [x] Add a static HTML job-board fixture with three visible job cards:
  - `Machine Learning Engineer` at `Acme AI`
  - `Senior Data Engineer` at `Nova Labs`
  - `Frontend Engineer` at `Pixel Foundry`
- [x] Add expected `job_extraction` output with selectors, crawl counts, warnings, and short evidence.
- [x] Add fixture integrity tests that verify HTML card counts match expected output.
- [x] Add frozen ITviec AI Engineer Hanoi fixture from live fetch.
- [x] Add expected ITviec `job_extraction` output with 20 discovered/candidate/relevant page-1 jobs.
- [x] Add reusable `assert_matches_expected_itviec_output(actual)` helper for future sandbox workflow tests.
- [x] Verify expected ITviec job URLs match the page's ItemList JSON-LD.
- [x] Document the manual extraction playbook and compare it against the proposed sandbox protocol.
- [x] Add `job-scraper compare-extraction` so sandbox output can be compared against the verified expected fixture.
- [x] Add a mocked smoke test where the sandbox worker returns the expected fixture output:

```json
{
  "jobs": [
    {
      "title": "Machine Learning Engineer",
      "company_name": "Acme AI",
      "job_url": "/it-jobs/machine-learning-engineer-acme-1",
      "location_raw": "Ha Noi",
      "description_text": "Build ML systems"
    }
  ],
  "confidence": 0.85,
  "evidence": ["page.html"]
}
```

- [x] Verify ADK receives only that final result and not intermediate logs.
- [x] Verify ADK receives an `audit.audit_id` and audit artifact handles.
- [x] Verify `trace.jsonl` contains metadata/hashes/previews, not full HTML or full command output.
- [ ] Verify actual sandbox output passes:

```bash
uv run job-scraper compare-extraction \
  data/sandbox_runs/<audit_id>/final.json \
  tests/fixtures/itviec_ai_engineer_ha_noi.expected.json \
  --json
```

- [ ] Add an optional real-Docker integration test:

```bash
RUN_DOCKER_SANDBOX_TESTS=1 uv run pytest tests/test_sandbox_agent.py -q
```

Expected:

```text
Mocked tests pass by default.
Docker tests run only when explicitly enabled.
```

---

## Task 8: ADK Web Manual Test

**Files:**
- No code file changes.

- [ ] Restart ADK Web:

```bash
uv run adk web src --port 8000 --host 127.0.0.1 --no-reload
```

- [ ] Open:

```text
http://127.0.0.1:8000/dev-ui/?app=job_scraper
```

- [ ] Submit:

```text
Use the job-listing-scout skill to scrape this ITviec page for AI/ML-related jobs:

https://itviec.com/it-jobs/ha-noi?job_selected=chuyen-vien-an-toan-thong-tin-up-to-35m-net-thai-son-soft-2042

Do not return full HTML. Fetch the page into the page workspace, delegate page analysis to the sandbox worker with a precise extraction task, persist relevant AI/ML/security/data jobs to SQLite, record the crawl run, query the stored relevant jobs, and summarize what was saved.
```

Expected:

```text
The agent calls load_skill.
The agent calls fetch_page_to_workspace or render_page_to_workspace.
The agent calls run_sandbox_agent.
The ADK context receives only final sandbox output.
The agent calls upsert_job for relevant roles if candidates are found.
The agent calls record_crawl_run.
The agent calls query_jobs.
The final response summarizes saved jobs, not fetch diagnostics or raw HTML.
```

- [ ] Verify dashboard data path:

```bash
uv run job-scraper top --db data/jobs.db --relevant-only --json
```

---

## Task 9: Sandbox Token Report

**Files:**
- Modify: `src/sandbox_page_analyst/runtime.py`
- Modify: `src/job_scraper/adk_tools.py`
- Modify: `plans/reports.md`
- Test: `tests/test_sandbox_agent.py`
- Test: `tests/test_sandbox_audit.py`
- Test: `tests/test_adk_tools.py`

See also: [Sandbox token reporting](03-sandbox-token-reporting.md).

- [ ] Capture Google ADK usage for the main ADK workflow when available:
  - `requests`
  - `input_tokens`
  - `output_tokens`
  - `total_tokens`
- [ ] Capture OpenAI Agents SDK usage for the nested sandbox-agent run:
  - `requests`
  - `input_tokens`
  - `output_tokens`
  - `total_tokens`
  - `request_usage_entries`
- [ ] Measure wall-clock completion time for the sandbox-agent run:
  - `completion_time_ms`
- [ ] Measure wall-clock completion time for the ADK-level workflow/tool call when feasible.
- [ ] Keep usage separated into `adk_usage` and `sandbox_usage`.
- [ ] Compute `combined_usage = adk_usage + sandbox_usage`.
- [ ] If the sandbox run uses multiple OpenAI calls, sum usage across calls through OpenAI Agents SDK usage.
- [ ] Write raw per-call and total usage to:

```text
data/sandbox_runs/<audit_id>/usage.json
```

- [ ] Include usage artifact path in the audit object.
- [ ] Include compact `adk_usage`, `sandbox_usage`, and `combined_usage` in the ADK tool return.
- [ ] Update `plans/reports.md` after the first successful sandbox ITviec run.
- [ ] Optionally write generated physical report artifacts under `reports/`.
- [ ] Do not run A/B testing yet; this report is sandbox-only.

Suggested `usage.json` shape:

```json
{
  "pipeline": "sandbox_final_only",
  "status": "success",
  "url": "https://itviec.com/it-jobs/ha-noi",
  "models": {
    "adk_main_agent": "openai/gpt-5.4-mini",
    "sandbox_agent": "gpt-5.4-mini",
    "sandbox_reasoning_effort": "high"
  },
  "adk_usage": {
    "requests": 2,
    "input_tokens": 1800,
    "output_tokens": 500,
    "total_tokens": 2300,
    "completion_time_ms": 7200,
    "request_usage_entries": []
  },
  "sandbox_usage": {
    "requests": 4,
    "input_tokens": 6200,
    "output_tokens": 2100,
    "total_tokens": 8300,
    "completion_time_ms": 18420,
    "request_usage_entries": [
      {
        "response_id": "resp_...",
        "input_tokens": 1200,
        "output_tokens": 300,
        "total_tokens": 1500
      }
    ]
  },
  "combined_usage": {
    "requests": 6,
    "input_tokens": 8000,
    "output_tokens": 2600,
    "total_tokens": 10600,
    "completion_time_ms": 25620
  },
  "audit_id": "sandbox_run_..."
}
```

Implementation notes:

- OpenAI Agents SDK usage should come from `result.context_wrapper.usage`.
- ADK usage should come from ADK events/session event usage metadata if exposed by the current ADK version.
- If ADK usage is unavailable for a run, write `adk_usage.status: "unavailable"` and still report sandbox usage.
- Do not manually estimate tokens for successful API calls.
- Terminal stdout/stderr bytes are audit metadata, not token usage, unless they are sent to a model.

Verification:

```bash
uv run pytest tests/test_sandbox_agent.py tests/test_sandbox_audit.py tests/test_adk_tools.py -q
```

---

## Resolved Questions

- `DockerSandboxClient` cannot currently be configured with strict no-network mode through its public options. `DockerSandboxClientOptions` exposes `image` and `exposed_ports`; the client creates containers without `network_mode`, `network_disabled`, or arbitrary Docker create kwargs. Use a thin subclass/wrapper for v1 that injects `network_disabled=True` or `network_mode="none"` into container creation, and keep that code isolated behind `src/sandbox_page_analyst/`.
- Use `gpt-5.4-mini` for the sandbox worker in v1 with `reasoning_effort="high"`. The ADK main agent should continue using the existing `openai/gpt-5.4-mini` default.
- Validate sandbox final output twice: before returning it from the sandbox wrapper and again before database persistence. The second gate keeps persistence safe if the workflow is invoked outside the normal ADK path.
- Always persist sandbox scratch artifacts and standard audit files locally under `data/sandbox_runs/<audit_id>/` without a separate user confirmation. Return only compact audit handles to ADK.

## Self-Review

**Spec coverage:** This plan solves the cumulative context issue by moving iterative page inspection into a nested sandbox-agent run behind one ADK FunctionTool. The main ADK agent receives only final structured output, then persists jobs and records crawl metadata.

**Placeholder scan:** No placeholders remain. The plan names concrete files, tools, commands, and expected behavior.

**Type consistency:** Tool names match the planned `allowed-tools` contract: `fetch_page_to_workspace`, `render_page_to_workspace`, `run_sandbox_agent`, `upsert_job`, `record_crawl_run`, `query_jobs`, and `list_seed_references`.
