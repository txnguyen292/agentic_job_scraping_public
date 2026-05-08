# Specialized Job Extraction Worker Plan

**Goal:** Use native ADK Skills as capability packages while giving the main ADK agent a specialized job-extraction worker inside a controlled local Docker workspace. The worker should excel at job-listing extraction, not become a general Codex-like repo editor. ADK artifacts are the canonical runtime file store; Docker files are temporary materializations unless promoted as validated outputs or human-review proposals.

**Status:** Revised implementation direction as of 2026-05-08. This supersedes the earlier nested `SandboxAgent` plan and the narrower sandbox-terminal plan that only gave the agent command snippets without full extraction contracts.

## Core Idea

Use ADK's native skill workflow:

```text
list_skills
load_skill
load_skill_resource
run_skill_script
```

Do not use `run_skill_script` as a one-off extractor only. Use it as the native ADK bridge to trusted skill scripts that manage a persistent Docker worker workspace for the current extraction run.

Important distinction:

```text
run_skill_script
  trusted control plane that starts/execs/reads/finalizes the sandbox runtime

Docker worker workspace
  adaptive data plane where the agent can inspect mounted extraction resources and run bounded bash commands
```

The worker must not be limited to Python-only scripts. It should be able to run common shell commands inside Docker, such as `ls`, `find`, `grep`, `rg`, `sed`, `awk`, `head`, `tail`, `wc`, `jq`, `python`, and small one-off scripts. Python remains available as one shell command, not the whole interface.

Each Docker runtime should be backed by a disposable Git worktree checked out from the current branch. The worktree gives the worker current extraction resources without letting it mutate the canonical checkout. The Docker workspace should mount/copy read-only resources from that worktree and expose only scoped writable runtime directories.

Important scope boundary:

```text
Read broadly:
  skills, references, schemas, validators, fixtures, helper script docs, page artifacts, runtime traces

Write narrowly:
  output/**, scratch/**, context/**, proposals/**

Canonical repo/skill/runtime code:
  read-only inside the worker. Changes become proposals, not direct mutations.
```

```text
main ADK agent
  -> list_skills
  -> load_skill("job-listing-scout")
  -> render_page_to_workspace(...)
  -> load_skill("sandbox-page-analyst")
  -> use the compact script catalog embedded in sandbox-page-analyst/SKILL.md
  -> run_skill_script("sandbox-page-analyst", "scripts/<needed-script>.py", ["--help"]) when args are unclear
  -> run_skill_script("sandbox-page-analyst", "scripts/sandbox_start.py", ...)
  -> run_skill_script("sandbox-page-analyst", "scripts/sandbox_exec.py", ...)
  -> run_skill_script("sandbox-page-analyst", "scripts/sandbox_write_file.py", output/extractor.py)
  -> run_skill_script("sandbox-page-analyst", "scripts/sandbox_exec.py", "python output/extractor.py")
  -> extractor writes output/candidates.json and output/final.json
  -> run_skill_script("sandbox-page-analyst", "scripts/sandbox_exec.py", "python scripts/validate_outputs.py output")
  -> run_skill_script("sandbox-page-analyst", "scripts/sandbox_finalize.py", ...)
  -> persist_sandbox_job_extraction(...)
```

The worker gets terminal autonomy for extraction work, but through bounded sandbox scripts and scoped writable paths rather than a nested general-purpose coding agent.

## Correct Runtime Workflow

The current intended workflow is compact-resource-first, help-on-demand, and extractor-file-backed.

1. The main agent loads `job-listing-scout` and saves the target page to the page workspace/ADK artifacts.
2. For large or unfamiliar pages, the main agent loads `sandbox-page-analyst`.
3. The agent uses the compact sandbox script catalog embedded in `sandbox-page-analyst/SKILL.md`. It should not ingest separate script documentation by default.
4. When the agent needs exact arguments, it runs the relevant script with `--help` through `run_skill_script`.
5. The agent starts one workflow sandbox with the page artifact mounted.
6. The agent uses `sandbox_exec.py` for bounded inspection and parser checks, looking specifically for recurring structures that denote job postings.
7. The agent uses `sandbox_write_file.py` to write `output/extractor.py`; shell heredocs and inline Python writes to protocol files remain banned.
8. The agent runs `python output/extractor.py` inside the sandbox.
9. The extractor writes the complete `output/candidates.json` and `output/final.json` files itself from recurring page patterns. Extractor stdout is only a compact summary such as counts, saved output paths, and status.
10. The agent validates by running `python scripts/validate_outputs.py output` inside the active sandbox, or by using a future `validate_outputs.py --audit-id <id>` host wrapper that resolves the active workspace.
11. The agent finalizes with `sandbox_finalize.py --audit-id <id>` after valid protocol files exist. In workflow mode, finalize should read existing `output/final.json`; it must not overwrite the file with inline `--status`/`--summary` unless a complete `--result` is provided or the run is diagnostic/debug.
12. Only after successful finalization may the agent call `persist_sandbox_job_extraction`, then `query_jobs`, then produce the compact user summary with saved output paths or artifact handles.

Important invariant: previews are never authoritative extraction data. If stdout is truncated or intentionally summarized, the agent must inspect persisted files or repair the extractor, not reconstruct jobs from preview text.

## Instruction Surface Ownership

The current runtime has overlapping instructions across `agent.py`, `job-listing-scout/SKILL.md`, `sandbox-page-analyst/SKILL.md`, and `sandbox-page-analyst/references/workflow-mode.md`. The next documentation hardening pass should make each layer own exactly one concern:

- `src/job_scraper/agent.py`: global routing only. It should tell the agent which skills to load, when to save page artifacts, when to use direct diagnostics versus sandbox workflow, and what final user summary must contain. It should not restate sandbox extraction mechanics.
- `skills/job-listing-scout/SKILL.md`: job-domain contract only. It should define normalized job goals, relevance/scoring expectations, persistence/query requirements, and the handoff rule: use `sandbox-page-analyst` for large, unfamiliar, or iterative page extraction. It should not duplicate the sandbox workflow steps.
- `skills/sandbox-page-analyst/SKILL.md`: sandbox entry contract only. It should own no-network policy, mode routing, script catalog, approved packages, and input files. It should point workflow runs to `references/workflow-mode.md` and diagnostics to `references/diagnostic-mode.md`.
- `skills/sandbox-page-analyst/references/workflow-mode.md`: full extraction procedure. It should own page inspection, pattern derivation, extractor writing, protocol outputs, validation/finalization, repair loop, reference proposals, and final sandbox JSON shape.
- `skills/sandbox-page-analyst/references/diagnostic-mode.md`: diagnostic-only behavior. It should own stdout/stderr preview semantics and stop conditions for small probes.

After consolidation, the agent should see a short routing instruction first, then progressively load only the detailed reference needed for the active mode.

## Skill Update Requirements

The current skills are close but not sufficient for the worker architecture. They must be updated with runtime policy, not merely supplemented by host callbacks.

Required skill changes:

- `skills/project-context/SKILL.md`
  - Keep root repo-context behavior for Codex/development agents.
  - Make Docker-local runtime context a distinct extension for ADK worker runs.
  - Runtime scripts used by the worker must operate on `/workspace/context/**`, not repo `.contexts`.
  - Repo `.contexts/bin` proxy scripts may remain for Codex/tooling, but they are not the worker's scrape-run memory.

- `skills/sandbox-page-analyst/SKILL.md`
  - State the hard path policy: broad reads from mounted extraction resources; writes only to `output/**`, `scratch/**`, `context/**`, and `proposals/**`.
  - State that mounted skills, references, schemas, validators, fixtures, and helper scripts are read-only contracts.
  - Tell the worker to inspect relevant schemas and validators when protocol errors appear instead of guessing from short error text.
  - Replace unconditional `output/reference_proposal.*` requirements with conditional `proposals/**` outputs for reusable lessons.

- `skills/sandbox-page-analyst/references/workflow-mode.md`
  - Keep extractor-backed protocol outputs under `output/**`.
  - Move self-improvement artifacts to `proposals/**`.
  - Require end-of-run review of Docker-local runtime context before deciding whether a proposal is useful.
  - Do not force a proposal when no reusable pattern or workflow lesson exists; record that no proposal was warranted in runtime context instead.

- `skills/sandbox-extraction-debugger/SKILL.md`
  - Align allowed writes with `output/**`, `scratch/**`, `context/**`, and `proposals/**`.
  - Keep schemas, validators, references, mounted helper scripts, and canonical skill files read-only.
  - For schema/validator errors, require inspection of the relevant schema/validator before patching generated extractor serialization.
  - Allow proposal files when the worker discovers a reusable skill/reference/script improvement, but do not let the worker apply canonical changes.

- `skills/job-listing-scout/SKILL.md`
  - Stay at the domain/orchestration layer.
  - Point extraction details, Docker-local runtime context, and proposal handling to `sandbox-page-analyst` and `sandbox-extraction-debugger`.
  - Final user summaries should mention proposal paths only when proposals were produced.

Runtime enforcement must match the skill text. If a skill says a path is read-only, the sandbox helper scripts must reject writes there.

## Why This Replaces The Nested Sandbox Agent

The OpenAI Agents SDK `SandboxAgent` path proved the boundary concept, but the first ADK Web run hit `Max turns (8) exceeded` before final extraction. The sandbox worker spent turns recovering from missing tools and did not emit validated JSON.

The new approach keeps the useful parts:

- full HTML stays outside main context
- the agent can iteratively inspect files through a terminal
- only compact summaries return to ADK
- ADK artifacts preserve runtime evidence and final outputs

But removes the nested LLM loop:

- no OpenAI Agents SDK sandbox dependency
- no separate sandbox agent persona
- no second turn budget
- easier ADK Web debugging because every sandbox action is a visible tool/script call

## Runtime Shape

```text
ADK App creation
  -> installs SandboxOutputGatePlugin
  -> installs SandboxNoteRefinementPlugin

Base agent
  -> sees skill tools
  -> can list and load skills

Project Context skill
  -> inside Docker, gives the worker bounded run-memory tools under /workspace/context
  -> records observations, attempts, errors, resolutions, and reusable lessons for one scrape/audit run
  -> does not write repo .contexts; Codex promotes project-level lessons separately

Job Listing Scout skill
  -> guides scraping orchestration
  -> page fetch/render/persist/query tools stay job-scout capabilities

Sandbox Page Analyst skill
  -> guides page artifact analysis
  -> skill scripts expose sandbox terminal protocol

Docker worker workspace
  -> no network
  -> is backed by a disposable Git worktree checked out from the current branch
  -> materializes ADK artifacts into a temporary /workspace
  -> mounts/copies extraction skills, references, schemas, validators, and fixtures from that worktree read-only
  -> exposes writable output/**, scratch/**, context/**, and proposals/**
  -> exposes a bounded bash terminal through sandbox_exec.py
  -> writes selected outputs back to ADK artifacts
  -> persistent per audit_id until finalized
  -> stops immediately when a hard guardrail triggers
  -> worktree/workspace is removed after non-actionable runs and retained only for validated outputs, reviewable proposals, reproducible bugs, failing fixtures/tests, or other concrete artifacts
```

## Worktree Lifecycle

Default behavior:

1. Resolve the current branch or commit from the canonical project checkout.
2. Create a uniquely named disposable worktree for the sandbox audit/run.
3. Start Docker with extraction resources from that worktree available read-only.
4. Write run outputs only under Docker/worktree runtime directories: `output/**`, `scratch/**`, `context/**`, and `proposals/**`.
5. On finalization, classify the run outcome.
6. Remove the worktree if the run produced no actionable artifacts.
7. Retain the worktree or its diff/artifact bundle only when there is a validated extraction, reviewable proposal, reproducible bug report, failing fixture/test, or other concrete human-review artifact.

The worker must never merge, commit, or promote worktree changes into the canonical branch. Promotion is a human-approved host action.

## Executor Layering

There are two execution layers, and they must stay separate.

Trusted ADK skill executor:

- runs repo-bundled skill scripts through `run_skill_script`
- may use ADK's local code executor or a custom trusted executor
- starts Docker, executes Docker commands, saves ADK artifacts, and enforces policy
- must not directly run arbitrary agent-authored shell commands on the host

Secure Docker terminal:

- runs the adaptive bash commands chosen by the main agent
- has no network access
- receives only materialized ADK artifacts and skill resources needed for the run
- is the only place where arbitrary inspection commands execute
- is destroyed or marked terminal when finalized or when a hard guardrail triggers

This gives the agent a real terminal without turning the host code executor into the sandbox.

## Self-Improvement Proposal Loop

The worker should be able to improve the scraping system over time without directly mutating canonical skills or runtime code.

During the extraction run, the worker may discover:

- site-specific selectors, URL normalization rules, pagination behavior, or blocked-page clues
- generic workflow lessons that would improve `sandbox-page-analyst/SKILL.md`
- reference improvements for files such as `references/itviec-listing-page.md`
- reusable helper-script ideas
- confusing schema/validator contracts that should be documented more clearly

The worker should write proposals under the Docker workspace:

```text
/workspace/proposals/
  reference_update.md
  skill_instruction_update.md
  helper_script_proposal.md
  patch.diff
  verification.md
```

Proposal rules:

- references are the primary target for site/layout-specific knowledge
- `SKILL.md` proposals are for generic workflow, policy, or tool-usage improvements
- script/helper proposals are allowed only when reusable executable logic is justified
- schemas and validators are read-only contracts; if the worker believes they are wrong, it writes a proposal or bug report
- canonical repo files are not modified by the worker
- proposals must include evidence from `/workspace/context`, generated artifacts, validator output, and any fixture/page verification

Verification rule:

If a proposal changes executable behavior, the worker may apply the proposed patch in a temporary proposal workspace, run the relevant extractor/validator/fixture checks, and save the diff plus verification result. The temporary proposal workspace is discarded unless it produces a human-review-worthy diff, validated outputs, a reproducible bug report, or a failing fixture/test.

Final response rule:

The final response should include proposal paths only when proposals exist. It should not claim canonical skills were improved until a human approves and promotes the proposal.

## Docker-Local Runtime Project Context

The ADK runtime agent should be able to use a project-context-like skill while it works, but it must not write scrape-run memory into the repo `.contexts/` tree.

There are two distinct context layers:

```text
Codex / repo context
  location: /Users/tungnguyen/personal_projects/job_scraping/.contexts
  users: Codex and development agents working on this project
  content: implementation plans, handoff, decisions, blockers, current project state

Worker / runtime context
  location: /workspace/context/** inside the Docker worker
  users: ADK job-extraction worker during one scrape/audit run
  content: page observations, extractor attempts, validator errors, fixes, reusable lessons
```

The runtime `project-context` skill should operate on Docker-local files such as:

```text
/workspace/context/
  events.jsonl
  observations.jsonl
  attempts.jsonl
  errors.jsonl
  resolutions.jsonl
  summary.json
```

Purpose:

- give the worker durable run memory without stuffing every terminal result into the main ADK context
- record observations: selectors, counts, URL rules, page structure, evidence paths, hashes
- record attempts: extractor versions, patches, commands run, whether the attempt changed state
- record errors: validator/finalizer failures, malformed outputs, count mismatches, guardrail failures
- record resolutions: what changed, why it worked, and which validation proved it
- record reusable lessons: site-specific or layout-specific knowledge that may justify a proposal

Recommended runtime flow:

```text
load_skill("job-listing-scout")
render_page_to_workspace(...)

if large/unfamiliar page:
  load_skill("sandbox-page-analyst")
  start Docker worker workspace with page artifact and extraction resources
  inside Docker, initialize /workspace/context
  inspect schemas/references/validators as needed
  write observations and extraction_plan into /workspace/context
  write/run output/extractor.py
  run validators/finalizer
  append attempts/errors/resolutions to /workspace/context
  before final response, review /workspace/context for reusable lessons
  if useful, write proposals/** with reference/SKILL/script improvement proposal and verification evidence
```

Safety and context rules:

- runtime project-context scripts must operate inside Docker against `/workspace/context/**`
- runtime project-context scripts must not write repo `.contexts`
- outputs must be bounded and summary-first
- do not store raw HTML, full stdout/stderr, full job payloads, or large JSON blobs in runtime context
- store paths, hashes, selectors, counts, and compact rationale instead
- after each extractor run, the worker should reconcile the new result with runtime context before the next repair attempt
- at the end of the run, the worker should review runtime context to decide whether reference, instruction, or helper-script proposals would make future runs faster or less error-prone
- promote only high-level reusable lessons, project blockers, or human-review proposals into repo `.contexts`
- keep ADK session extraction context as the immediate next-step command state; keep Docker runtime project-context as supporting run memory

## Page Artifact Size Policy

Every fetch/render-to-workspace operation must measure the page before the agent decides how to inspect it.

Return metadata like:

```json
{
  "page_id": "page_...",
  "url": "https://example.com/jobs",
  "status_code": 200,
  "content_bytes": 560776,
  "estimated_tokens": 145000,
  "html_preview": "short bounded preview",
  "signals": {
    "job_like_links": 385,
    "json_ld_job_postings": 3,
    "hydration_blobs": 1,
    "script_bytes": 420000
  },
  "recommended_next": "load sandbox-page-analyst"
}
```

Default rule: do not return full raw HTML to the main agent.

Suggested thresholds:

```text
Small page:
  estimated_tokens <= 8k
  content_bytes <= 32-50 KB
  no massive script or hydration blobs
  return bounded cleaned excerpts only

Medium page:
  estimated_tokens 8k-30k
  return targeted structured snippets only
  prefer workspace artifacts for repeated inspection

Large page:
  estimated_tokens > 30k
  content_bytes > 100 KB
  or big hydration/script blobs
  use sandbox-page-analyst terminal workflow
```

Full HTML may be returned only for explicit diagnostics and only under a hard cap. For scraping workflows, save full HTML directly as a session-scoped ADK artifact and return handles plus profile metadata.

## Skill Responsibilities

### job-listing-scout

Owns job-scraping orchestration:

- choose fetch or render
- save page workspace artifacts directly to ADK artifacts
- decide whether page analysis is needed
- load `sandbox-page-analyst` for large or unfamiliar HTML
- persist validated jobs
- record crawl runs
- query stored jobs

It should not directly expose sandbox terminal behavior in its instructions. It should route the agent to the sandbox skill when HTML analysis is needed.

### sandbox-page-analyst

Owns terminal-based page artifact analysis:

- inspect materialized ADK page artifacts
- choose references based on page profile
- run shell commands in the sandbox, including Python when useful
- write required output files
- validate candidates
- finalize compact job extraction JSON
- propose reference/skill improvements as artifacts, not direct repo edits

## Sandbox Skill Scripts

Add or adapt these scripts under `skills/sandbox-page-analyst/scripts/`:

```text
sandbox_start.py
sandbox_exec.py
sandbox_read.py
sandbox_write.py
sandbox_finalize.py
validate_outputs.py
```

Script responsibilities:

- `sandbox_start.py`: create `audit_id`, load ADK page artifacts, materialize them into a temporary Docker workspace, copy skill resources, start Docker container with no network, and save initial `progress.json`/`plan.md` as ADK artifacts.
- `sandbox_exec.py`: run an agent-requested bash command in the active Docker sandbox, save stdout/stderr directly as ADK artifacts, update `trace.jsonl`/`progress.json` artifacts, and return either bounded direct output or a clear artifact-aware "output too large, inspect a narrower slice" observation.
- `sandbox_read.py`: read bounded text from sandbox workspace files or ADK artifacts with a server-side max-char cap.
- `sandbox_write.py` / `sandbox_write_file.py`: write agent-authored files into the live sandbox workspace. For required protocol files under `output/`, validate the submitted JSON against the matching shared Pydantic model before persisting. If validation fails, return structured facts and leave the existing file unchanged.
- `sandbox_finalize.py`: collect existing protocol outputs, validate final schema and cross-file consistency with the shared protocol validators, save final/protocol outputs as ADK artifacts, stop the Docker runtime, and return compact final JSON. In workflow mode it should not destructively rewrite `output/final.json` from `--status`/`--summary` alone.
- `validate_outputs.py`: validate required protocol files by calling the same shared Pydantic models plus cross-file checks. It must run against the active sandbox workspace, either inside Docker via `sandbox_exec.py` or through a host wrapper that accepts `--audit-id`.

`sandbox_exec.py` command policy:

- accept a command string and execute it inside Docker with a shell such as `/bin/bash -lc`
- set `/workspace` as the working directory by default
- apply per-command timeout, stdout/stderr byte caps, and workspace-size checks
- record command text or hash, exit code, duration, byte counts, bounded returned text, truncation flags, output policy, and artifact handles
- reject commands only for explicit policy reasons, such as attempting network access, background daemons, privileged operations, or exceeding configured limits
- surface rejected commands as structured errors rather than silently rewriting them
- reject shell commands that write required protocol files or `output/extractor.py`; those writes must go through `sandbox_write_file.py` so content can be validated and audited before persistence

The agent is expected to compose normal shell pipelines for inspection. Example allowed commands:

```bash
find . -maxdepth 3 -type f
wc -c page.html
rg -n "JobPosting|AI|Machine Learning" page.html
python - <<'PY'
from pathlib import Path
text = Path("page.html").read_text(errors="replace")
print(text[:2000])
PY
jq '.jobs | length' output/candidates.json
```

Inspection commands may still read protocol files, run `output/extractor.py`, and run validators. They must not create or overwrite required protocol files through heredocs, `open(...).write`, `Path.write_text`, `cat >`, `tee`, or similar shell write paths.

Extractor contract:

- `output/extractor.py` is the only place that should convert page evidence into full job records.
- The extractor must write the full `output/candidates.json` and `output/final.json` files.
- Extractor stdout must be a bounded summary only: status, candidate counts, and output paths.
- The agent must not manually reconstruct candidate/job arrays from extractor stdout previews.
- `output/candidates.json`, `output/final.json`, and validation counts must agree.

## Protocol Model Validation

Each persisted sandbox protocol output should have one canonical Pydantic model in a shared Python module, for example `sandbox_page_analyst.protocol_models`.

Required models:

```text
PageProfileOutput
ExtractionStrategyOutput
CandidatesOutput
ValidationOutput
FinalOutput
ReferenceProposalOutput
```

Validation ownership:

- `sandbox_write_file.py` is the authoritative per-file validation boundary for `output/*.json` protocol files.
- `validate_outputs.py` is the authoritative cross-file validation boundary.
- `sandbox_finalize.py` calls the same validators before accepting a terminal workflow result.
- ADK callbacks enforce routing, output compaction, active-sandbox state, and persistence guards; they should not duplicate protocol model definitions.

Invalid protocol file writes should return a compact factual response and should not persist the invalid content:

```json
{
  "status": "error",
  "audit_id": "sandbox_run_...",
  "error_type": "protocol_model_validation",
  "path": "output/final.json",
  "model": "FinalOutput",
  "written": false,
  "errors": [
    {
      "loc": ["result"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

Do not include `required_next`, `suggested_next`, or prescriptive repair instructions in tool responses. Tools should surface structured facts only. The agent uses the loaded skill instructions and the returned facts to decide what to do next.

This immediate write-boundary validation should prevent schema-error ping-pong such as:

```text
candidates.json is a list
-> later validator error
-> patch candidates
-> later final.json status error
-> patch final
-> later final.result error
```

The bad shape should be rejected when the file is first submitted to `sandbox_write_file.py`.

## Secure Docker Container Policy

`sandbox_start.py` must create a container with restrictive defaults:

```text
network_mode: none
cap_drop: ALL
security_opt: no-new-privileges:true
read_only: true where compatible
pids_limit: configured low ceiling
mem_limit: configured ceiling
cpu quota/shares: configured ceiling
user: non-root where feasible
working_dir: /workspace
mounts: one temporary workspace mount plus optional read-only resources
```

If a required package or binary is missing, the workflow should surface that as a structured blocker. It should not enable network access from inside the sandbox to install dependencies during analysis.

## Live Session Registry

Because the container is persistent across `sandbox_exec.py` calls, the runtime needs a small live-session registry containing `audit_id`, container ID, workspace path, status, and limits.

Use the ADK app state tree for registry location, but do not write into ADK-owned internals such as `session.db`.

Default location:

```text
<adk_app_root>/.adk/runtime/sandbox_sessions/<user_id>/<session_id>/<audit_id>.json
```

For this app, the default app root should be:

```text
src/job_scraper/.adk/runtime/sandbox_sessions/...
```

Registry record shape:

```json
{
  "app_name": "job_scraper",
  "user_id": "user",
  "session_id": "adk-session-id",
  "audit_id": "sandbox_run_...",
  "container_id": "docker-container-id",
  "workspace_path": "/tmp/job_scraper_sandbox/...",
  "status": "running",
  "created_at": "2026-04-29T00:00:00Z",
  "updated_at": "2026-04-29T00:00:10Z",
  "limits": {
    "max_commands_per_session": 20,
    "max_duration_seconds": 300,
    "max_command_timeout_seconds": 30
  }
}
```

Rules:

- key the registry by ADK `user_id`, `session_id`, and `audit_id`
- sanitize path segments before writing paths
- keep one registry file per active sandbox run
- write registry updates atomically
- treat the registry as operational state only
- persist audit/history/progress/output data as ADK artifacts, not registry files
- mark registry status terminal on finalize or guardrail stop
- keep stopped registry records only long enough for debugging, then allow cleanup

Rationale:

```text
.adk session/artifact stores
  canonical ADK runtime state and artifact history

.adk/runtime/sandbox_sessions
  live Docker reconnect metadata for active containers only
```

This keeps the live Docker session associated with the ADK session while avoiding direct dependency on private ADK storage schemas.

## Sandbox Terminal Response Contract

Every command response returned to ADK must be compact:

```json
{
  "status": "success",
  "audit_id": "sandbox_run_...",
  "command_index": 3,
  "exit_code": 0,
  "duration_ms": 412,
  "stdout": "bounded exact output",
  "stderr": "",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "observation": "Command output fit within the context return limit; full stdout/stderr were also persisted as artifacts.",
  "output_policy": {
    "context_return_limit_chars": 4000,
    "full_output_persisted": true,
    "stdout_path": "commands/003.stdout.txt",
    "stderr_path": "commands/003.stderr.txt"
  },
  "artifacts": {
    "stdout": {
      "artifact_name": "sandbox_run_.../commands/003.stdout.txt",
      "version": 0,
      "mime_type": "text/plain"
    },
    "stderr": {
      "artifact_name": "sandbox_run_.../commands/003.stderr.txt",
      "version": 0,
      "mime_type": "text/plain"
    }
  }
}
```

Never return full HTML, full stdout/stderr, long stack traces, or large generated files to ADK. If an output exceeds the return limit but remains under the hard byte guardrail, persist the full output and return bounded preview facts, truncation flags, byte counts, artifact handles, and workspace paths. Do not prescribe the next action in the tool response; the agent decides whether it needs a narrower slice, a different command, or enough information to continue.

## ADK Artifact Contract

ADK artifacts are the canonical runtime file store. Local files exist only as ephemeral Docker workspaces or implementation caches.

An artifact handle is the compact model-facing reference to a saved artifact. It is not the artifact content.

Artifact handle shape:

```json
{
  "artifact_name": "sandbox_run_123/output/final.json",
  "version": 0,
  "mime_type": "application/json",
  "bytes": 4218,
  "sha256": "..."
}
```

Rules for artifact handles:

- return artifact handles instead of full HTML, full stdout/stderr, or large JSON
- include enough metadata for the agent to choose the next step without reading the full artifact
- include `artifact_name`, `version`, and `mime_type` at minimum
- include `bytes`, `sha256`, and a bounded preview whenever useful
- use session-scoped artifact names by default
- use the `user:` prefix only for artifacts that intentionally persist across sessions

Each sandbox run should save these session-scoped ADK artifacts:

```text
sandbox_run_<id>/policy.json
sandbox_run_<id>/inputs.json
sandbox_run_<id>/trace.jsonl
sandbox_run_<id>/plan.md
sandbox_run_<id>/progress.json
sandbox_run_<id>/commands/001.command.txt
sandbox_run_<id>/commands/001.stdout.txt
sandbox_run_<id>/commands/001.stderr.txt
sandbox_run_<id>/output/page_profile.json
sandbox_run_<id>/output/extraction_strategy.json
sandbox_run_<id>/output/candidates.json
sandbox_run_<id>/output/validation.json
sandbox_run_<id>/output/final.json
```

Trace records should include:

- command index
- command hash or bounded command text
- exit code
- duration
- stdout/stderr byte counts
- stdout/stderr hashes
- bounded previews
- file artifacts produced

Local workspace policy:

- `sandbox_start.py` materializes input ADK artifacts into a temporary per-run workspace for Docker.
- `sandbox_exec.py` operates on the same running container and workspace until finalization or guardrail termination.
- `sandbox_finalize.py` syncs durable outputs back to ADK artifacts and stops the container.
- Local materialized files are not the source of truth and should not be required for later ADK inspection.
- Persistent local mirrors may be added only as an explicit debug option, not the default.

## Saved Jobs Contract

Saved jobs are validated, normalized job records written to the project database through the job-scout persistence tool, currently `persist_sandbox_job_extraction(...)`.

They are distinct from sandbox artifacts:

```text
sandbox artifacts
  evidence, traces, protocol files, raw/derived extraction outputs

saved jobs
  normalized application data used by query tools and the dashboard
```

After sandbox finalization, the main agent should persist validated jobs and keep only a compact saved-job summary in model context:

```json
{
  "audit_id": "sandbox_run_...",
  "saved_job_count": 20,
  "source_url": "https://itviec.com/...",
  "saved_job_summary": [
    {
      "title": "AI Engineer",
      "company_name": "Example",
      "job_url": "https://..."
    }
  ],
  "blockers": []
}
```

The context should not keep the full extraction JSON once jobs are saved unless the payload is small enough under the output gate threshold.

## User-Facing Final Response Contract

The agent should produce a compact final response at the end of a scrape workflow. This is not a long report document.

Purpose:

- tell the user what happened
- summarize saved jobs or blockers
- provide the `audit_id` and key artifact handles when useful
- stay grounded in persisted jobs, validation output, and sandbox artifacts

Successful scrape response shape:

```text
Saved <N> AI/ML jobs from <source>.

Audit: sandbox_run_...
Persistence: verified from query_jobs.
Examples:
- <title> - <company>
- <title> - <company>
- <title> - <company>

Blockers: none.
```

Failure or guardrail response shape:

```text
The scrape did not complete.

Audit: sandbox_run_...
Blocker: <guardrail/error/validation failure>
Evidence: <artifact handle or short artifact summary>
Jobs saved: 0
```

Final response rules:

- do not include full raw HTML
- do not include long stdout/stderr
- do not include full extraction JSON unless it is trivially small
- do not claim jobs were saved unless persistence succeeded
- prefer examples from `query_jobs` after persistence, not from unpersisted extraction memory
- include blockers, guardrail errors, or validation failures honestly
- mention reference/skill proposals only as pending human review, never as applied changes

Detailed run evidence belongs in ADK artifacts such as `output/final.json`, `trace.jsonl`, `progress.json`, `validation.json`, and command stdout/stderr artifacts. Generated human report files under `reports/` are a separate feature and should not be produced by default.

## Guardrail Policy

Guardrails are mandatory and terminal. If any hard guardrail triggers, the sandbox runtime ends immediately.

Hard guardrails:

```text
max_commands_per_session
max_duration_seconds
idle_timeout_seconds
max_command_timeout_seconds
max_stdout_bytes
max_stderr_bytes
max_workspace_bytes
max_artifact_bytes
```

Guardrail behavior:

- stop accepting normal `sandbox_exec`, `sandbox_write`, and `sandbox_read` operations
- stop the Docker runtime
- save guardrail error, progress, trace, and relevant stdout/stderr as ADK artifacts
- return a structured error to the agent
- do not silently retry, continue, or delete evidence

`max_read_chars` is not a terminal guardrail. It is the direct-context return limit. Outputs larger than `max_read_chars` but smaller than `max_stdout_bytes`/`max_stderr_bytes` stay persisted and inspectable; the agent receives truncation flags, artifact handles, workspace paths, and concrete slice commands.

Guardrail error shape:

```json
{
  "status": "guardrail_triggered",
  "audit_id": "sandbox_run_...",
  "guardrail": "max_commands_per_session",
  "message": "Sandbox command budget exhausted before validation passed.",
  "progress_artifact": {
    "artifact_name": "sandbox_run_.../progress.json",
    "version": 3,
    "mime_type": "application/json"
  },
  "trace_artifact": {
    "artifact_name": "sandbox_run_.../trace.jsonl",
    "version": 5,
    "mime_type": "application/jsonl"
  }
}
```

The agent may use project-context and the saved artifacts to understand and improve the system after a guardrail-triggered stop, but that sandbox runtime is over.

## Context Notes And Refinement

Install `SandboxNoteRefinementPlugin` when creating the app, not after the workflow starts.

Verified ADK behavior:

- ADK plugins can use `before_model_callback`
- ADK plugins can use `after_tool_callback`
- `before_model_callback` receives an `LlmRequest`
- `LlmRequest.contents` is mutable
- callback-added notes are applied before `generate_content_async`
- therefore a custom note plugin can append runtime notes before the next model call
- `after_tool_callback` can observe sandbox command results and trigger summarization

Do not compact historical sandbox responses in the model context. The agent should see the full tool response that was returned by the sandbox tools. The sandbox tools already enforce bounded stdout/stderr previews and artifact handles when command output is too large.

The note refinement plugin should:

- collect sandbox command responses from `sandbox_exec.py`
- use an N+1 rolling rule: with interval 5, the 6th sandbox command triggers summarization of commands 1-5 while command 6 remains available in full
- pass both existing runtime notes and the previous 5 command responses into the summarizer so it fuses old notes with new evidence instead of stacking disconnected summaries
- store the summaries as runtime notes in ADK session state
- inject the latest notes into every model request
- replace only already-summarized command responses with compact placeholders in future model requests; keep the newest unsummarized command response full
- never delete ADK artifacts

After a sandbox loop is terminal, the plugin may remove detailed sandbox command responses from future model context:

- keep full sandbox responses while the sandbox is active
- keep finalized sandbox output until `persist_sandbox_job_extraction` succeeds, because the agent may need the final payload or handles for persistence
- if the sandbox ends with a guardrail, prune immediately because persistence is blocked
- replace pruned function responses with compact placeholders containing `audit_id`, script path, original status, guardrail/error facts, paths, artifact handles, and the latest runtime note
- preserve ADK event history and artifact files; only future model request context is changed

The note plugin controls runtime continuity only. It does not manage tool permissions, artifact storage, or Docker runtime lifecycle.

Recommended compaction result:

```json
{
  "type": "sandbox_context_summary",
  "audit_id": "sandbox_run_...",
  "status": "finalized",
  "saved_job_count": 20,
  "blockers": [],
  "artifacts": {
    "final": {
      "artifact_name": "sandbox_run_.../output/final.json",
      "version": 0,
      "mime_type": "application/json"
    },
    "trace": {
      "artifact_name": "sandbox_run_.../trace.jsonl",
      "version": 7,
      "mime_type": "application/jsonl"
    }
  }
}
```

## Sandbox Output Gate

Context filtering after the fact is not enough. Oversized tool responses can exceed the context window before the next model request has a chance to filter them. Add an `after_tool_callback` plugin that gates sandbox-related tool outputs immediately after each tool call.

Verified ADK behavior:

- `after_tool_callback` receives `tool`, `tool_args`, `tool_context`, and `result`
- returning a dict replaces the original tool result
- the replaced result is used to build the function response event
- therefore oversized output should be intercepted here before it becomes model-visible conversation history

Use this callback for:

- `run_skill_script` calls for `sandbox-page-analyst`
- sandbox terminal scripts such as `sandbox_exec.py`, `sandbox_read.py`, and `sandbox_finalize.py`
- any fetch/render diagnostic tool that might return HTML
- any future tool that returns file content, stdout, stderr, or extracted snippets

Policy:

```text
If output <= small threshold:
  return output directly

If output > threshold:
  save full output directly as an ADK artifact
  return a bounded preview, byte count, hash, and artifact handle
```

Example replacement result:

```json
{
  "status": "stored_preview",
  "reason": "tool_output_exceeded_context_threshold",
  "audit_id": "sandbox_run_...",
  "original_bytes": 184233,
  "sha256": "abc...",
  "preview": "bounded head/tail or structured summary",
  "artifact": {
    "artifact_name": "sandbox_run_.../oversized/tool_output_004.json",
    "version": 0,
    "mime_type": "application/json"
  }
}
```

Hard requirements:

- The callback must never return full raw HTML, full stdout/stderr, or large JSON blobs.
- The callback should run before note refinement observes the tool result.
- The callback should preserve small structured outputs because direct ingestion is useful when safe.
- The callback should preserve enough metadata for the agent to decide the next step.

Recommended thresholds:

```text
tool_output_direct_max_chars: 8_000
tool_output_preview_max_chars: 2_000
html_direct_max_chars: 0 by default for scraping workflows
sandbox_read_max_chars: caller requested value, capped at 4_000
```

The exact values can be tuned, but enforcement must be server-side. Do not rely on prompt instructions.

## Progress And Task State

Aggressive output gating creates another risk: the agent may lose track of what it has already tried. Give the agent a durable progress artifact for each sandbox run, and let the runtime note plugin summarize command batches into notes that are shown on later model calls.

Each sandbox run should maintain:

```text
sandbox_run_<audit_id>/plan.md
sandbox_run_<audit_id>/progress.json
sandbox_run_<audit_id>/trace.jsonl
```

`progress.json` should contain compact machine-readable state:

```json
{
  "audit_id": "sandbox_run_...",
  "task": "Extract AI/ML jobs from ITViec page artifact",
  "current_stage": "candidate_extraction",
  "completed_steps": [
    "profiled page",
    "detected json-ld item list",
    "identified job card selector"
  ],
  "open_questions": [
    "Need company names for item-list URLs"
  ],
  "next_steps": [
    "inspect job-card snippets",
    "write output/candidates.json",
    "run validation"
  ],
  "blockers": []
}
```

The agent can update this through sandbox scripts or a dedicated progress script:

```text
run_skill_script("sandbox-page-analyst", "scripts/sandbox_progress.py", ...)
```

Use the repo-local `.contexts/` system for project-level plans and handoffs, not for every sandbox command. The runtime ADK agent should be able to load the `project-context` skill when it needs to inspect or update broader task state, while per-run sandbox progress stays in ADK artifacts under `sandbox_run_<audit_id>/progress.json`.

Design split:

```text
.contexts/
  project-wide status, decisions, task progress, handoff, implementation next steps

ADK artifact sandbox_run_<audit_id>/progress.json
  current sandbox task state and local extraction plan
```

This keeps the main agent adaptive without requiring it to keep every terminal observation in model context.

## Tool Exposure Model

Prefer native ADK skill tools:

```text
list_skills
load_skill
load_skill_resource
run_skill_script
```

Keep app-specific non-script tools where they are cleaner than scripts:

- fetch/render page
- save page workspace
- persist jobs
- record/query crawl runs

Use `run_skill_script` for sandbox terminal operations because those operations belong to the `sandbox-page-analyst` skill package.

## Test Strategy

Use three test layers:

```text
unit tests
  deterministic mechanics with no Docker or live model requirement

Docker sandbox integration tests
  prove the terminal boundary, persistence, guardrails, and no-network policy

ADK agent tests / evals
  prove the agent chooses the right tools, follows the workflow, and reports truthfully
```

### Unit Tests

Run with `uv run pytest`.

Core unit tests:

- skill registry exposes `project-context`, `job-listing-scout`, and `sandbox-page-analyst`
- `run_skill_script` is configured with a trusted executor
- project-context scripts wrap `.contexts/bin/*` and return bounded JSON
- page fetch/render tools save full HTML as ADK artifacts and return metadata plus artifact handles
- page-size policy estimates bytes/tokens and recommends direct preview vs sandbox analysis
- artifact handle builder includes `artifact_name`, `version`, `mime_type`, `bytes`, and `sha256`
- `SandboxOutputGatePlugin.after_tool_callback` stores oversized non-sandbox output and promotes sandbox artifact sources to ADK artifact handles without compacting sandbox command responses
- `SandboxNoteRefinementPlugin.after_tool_callback` uses the rolling N+1 rule: after command 6 it summarizes commands 1-5 with current notes and keeps command 6 full
- `SandboxNoteRefinementPlugin.before_model_callback` injects the latest runtime notes into every model request
- `SandboxNoteRefinementPlugin.before_model_callback` prunes already-summarized sandbox command responses from future model context while preserving the latest unsummarized command
- `SandboxNoteRefinementPlugin.before_model_callback` also prunes completed sandbox command contexts after successful persistence or terminal guardrails, replacing them with audit placeholders
- live session registry writes to `.adk/runtime/sandbox_sessions/<user_id>/<session_id>/<audit_id>.json`
- live session registry sanitizes path segments and writes atomically
- sandbox command policy accepts normal bash inspection commands and rejects explicitly blocked operations
- guardrail enforcement marks the run terminal, stops the container, and returns structured artifact-backed errors
- saved-job persistence validates extraction JSON before writing normalized jobs to SQLite
- fixture extraction comparison passes for the ITViec expected-output fixture

Unit tests should mock Docker and ADK artifact services where needed. They should not require Docker Desktop, network, browser rendering, or live model calls.

### Docker Sandbox Integration Tests

Docker tests are required for this architecture, but they should be isolated behind a pytest marker so normal unit tests stay fast.

Run examples:

```bash
uv run pytest -q -m "not docker"
uv run pytest -q -m docker
```

Recommended pytest marker:

```toml
[tool.pytest.ini_options]
markers = [
  "docker: requires Docker runtime",
  "adk_eval: requires ADK eval/model runtime"
]
```

Suggested files:

```text
tests/integration/test_docker_sandbox_runtime.py
tests/integration/test_docker_sandbox_guardrails.py
tests/integration/test_docker_sandbox_artifacts.py
```

Test cases:

- `test_sandbox_start_creates_no_network_container`: start a sandbox and inspect Docker metadata. Assert `NetworkMode` is `none`, capabilities are dropped where Docker exposes them, `no-new-privileges` is set, resource limits are configured, working dir is `/workspace`, and the registry file exists under `.adk/runtime/sandbox_sessions/<user_id>/<session_id>/<audit_id>.json`.
- `test_sandbox_network_egress_fails`: run commands such as `python -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=3)"` and/or `curl https://example.com` if curl is present. Assert failure is surfaced as command failure evidence, not swallowed or retried.
- `test_sandbox_reuses_container_for_same_audit_id`: start once, run `echo hello > state.txt`, then run `cat state.txt` through a later `sandbox_exec.py` call. Assert the same container/session is used and state persists until finalize.
- `test_sandbox_exec_allows_common_bash_inspection`: run `find`, `wc`, `head`, `sed`, `awk`, `python`, and `jq` where available against fixture files. Assert stdout previews are bounded and exit codes/durations are recorded.
- `test_sandbox_exec_rejects_blocked_operations`: attempt policy-blocked commands such as background daemons, privileged Docker/socket access, writes outside `/workspace`, or explicitly blocked network tools. Assert structured rejection with reason, no host execution, and trace entry.
- `test_large_stdout_is_persisted_not_returned`: run a command producing output larger than `tool_output_direct_max_chars`. Assert tool response contains preview plus artifact handle, while full output is saved as an ADK artifact or artifact-service test double record.
- `test_large_stderr_is_persisted_not_returned`: same as stdout, but for stderr and stack traces.
- `test_workspace_size_guardrail_is_terminal`: create files until `max_workspace_bytes` is exceeded. Assert sandbox status becomes terminal, Docker container stops, registry status is terminal, and guardrail artifacts are saved.
- `test_command_timeout_guardrail_is_terminal`: run `sleep` past `max_command_timeout_seconds`. Assert process is killed, sandbox becomes terminal, and subsequent `sandbox_exec.py` returns a terminal-state error.
- `test_max_commands_guardrail_is_terminal`: run commands until budget is exhausted. Assert the final response is `guardrail_triggered` and no further commands are accepted.
- `test_finalize_collects_protocol_outputs_and_stops_container`: write valid `output/page_profile.json`, `output/extraction_strategy.json`, `output/candidates.json`, `output/validation.json`, and `output/final.json`, then finalize. Assert schema validation passes, artifacts are saved, registry status is finalized, and Docker container is stopped.
- `test_finalize_rejects_missing_or_invalid_protocol_outputs`: omit or corrupt required output files. Assert `needs_review` or `error`, artifact-backed diagnostics, no fake success, and no job persistence signal.
- `test_guardrail_preserves_debug_evidence`: trigger any hard guardrail. Assert `errors/guardrail_error.json`, latest stdout/stderr artifacts when present, `trace.jsonl`, and `progress.json` are available.
- `test_container_cleanup_after_finalize`: after finalize, assert container is stopped/removed according to configured cleanup policy while ADK artifacts and registry terminal record remain available.
- `test_session_registry_is_scoped_by_user_and_session`: start two sandboxes with different `user_id` or `session_id`. Assert registry files and containers do not collide.
- `test_adk_artifact_materialization_into_workspace`: save a fixture HTML page as an ADK artifact, start sandbox with its handle, and assert `/workspace/page.html` or the configured path contains the expected content.
- `test_no_dependency_install_inside_no_network_sandbox`: attempt a command that would install dependencies from the internet, such as `pip install ...`. Assert it fails and is surfaced as a blocker rather than changing network policy.

Docker sandbox acceptance requirements:

- no test should require public internet access from inside Docker
- every command result must have exit code, duration, byte counts, preview, and trace record
- every large output must become an artifact handle before entering model-visible context
- every hard guardrail must make the sandbox terminal
- terminal sandboxes must not accept more commands
- Docker tests must clean up containers even when assertions fail

Test implementation notes:

- use tiny local fixtures such as `tests/fixtures/static_job_board.html` and `tests/fixtures/itviec_ai_engineer_ha_noi.html`
- prefer a small project-owned sandbox image or Dockerfile so test dependencies are deterministic
- if the image is missing, tests should skip with a clear message or build it explicitly in a controlled setup step
- do not pull packages from inside the sandbox at test time
- use short timeouts so hung Docker commands fail quickly

### ADK Agent Tests

Use ADK evals for agent behavior, because pytest can validate code paths but not whether the agent chooses the right tools.

Create eval assets under:

```text
tests/eval/
  eval_config.json
  evalsets/
    job_scraper_core.json
```

Recommended eval criteria:

```json
{
  "criteria": {
    "tool_trajectory_avg_score": {
      "threshold": 0.7,
      "match_type": "IN_ORDER"
    },
    "rubric_based_tool_use_quality_v1": {
      "threshold": 0.8,
      "rubrics": [
        {
          "rubric_id": "uses_page_artifact_boundary",
          "rubric_content": {
            "text_property": "The agent saves or uses page artifacts instead of asking the model to ingest full raw HTML."
          }
        },
        {
          "rubric_id": "loads_sandbox_for_large_pages",
          "rubric_content": {
            "text_property": "For large or unfamiliar pages, the agent loads the sandbox-page-analyst skill and uses sandbox scripts to inspect the page."
          }
        },
        {
          "rubric_id": "persists_only_validated_jobs",
          "rubric_content": {
            "text_property": "The agent persists jobs only after validated extraction output is available."
          }
        },
        {
          "rubric_id": "does_not_continue_after_terminal_guardrail",
          "rubric_content": {
            "text_property": "If a terminal guardrail is triggered, the agent stops that sandbox workflow and reports the blocker instead of continuing as if it succeeded."
          }
        }
      ]
    },
    "rubric_based_final_response_quality_v1": {
      "threshold": 0.85,
      "rubrics": [
        {
          "rubric_id": "reports_persisted_results_truthfully",
          "rubric_content": {
            "text_property": "The final response accurately states whether jobs were persisted and does not claim persistence when the persistence tool did not succeed."
          }
        },
        {
          "rubric_id": "includes_audit_and_blockers",
          "rubric_content": {
            "text_property": "The final response includes the audit ID or artifact handles when available, and clearly reports blockers or guardrail errors."
          }
        },
        {
          "rubric_id": "summarizes_without_raw_html",
          "rubric_content": {
            "text_property": "The final response summarizes saved jobs without including full raw HTML, long stdout/stderr, or large JSON payloads."
          }
        },
        {
          "rubric_id": "grounded_in_tool_outputs",
          "rubric_content": {
            "text_property": "The final response is grounded in tool outputs, persisted job query results, or artifact metadata."
          }
        }
      ]
    },
    "hallucinations_v1": {
      "threshold": 0.8
    }
  }
}
```

Rubric metrics should be primary for this project because the scraper is adaptive. There can be multiple valid command sequences inside the sandbox, and the exact final phrasing can vary without being wrong.

Use strict metrics selectively:

```text
tool_trajectory_avg_score
  smoke/regression check for minimum required ordering
  use IN_ORDER for normal evals
  use EXACT only for narrow tests where extra calls are truly harmful

final_response_match_v2
  optional semantic check for stable user-facing cases
  do not make it the only final-response quality gate

rubric_based_tool_use_quality_v1
  primary tool-use quality metric for adaptive workflows

rubric_based_final_response_quality_v1
  primary final-response quality metric
```

Rubric design rules:

- make each rubric binary and independently judgeable
- prefer behavior properties over exact wording
- keep rubrics tied to project invariants: no full HTML, artifact boundary, validated persistence, guardrail honesty, DB-backed summaries
- use case-level or invocation-level rubrics when a scenario needs special requirements
- keep `hallucinations_v1` enabled as a grounding backstop

Core eval cases:

- `large_itviec_sandbox_workflow`: agent loads `job-listing-scout`, fetches/renders page, receives large-page metadata, loads `sandbox-page-analyst`, runs sandbox start/exec/finalize, persists jobs, queries saved jobs, and summarizes saved jobs plus audit ID.
- `small_fixture_direct_workflow`: agent may inspect bounded snippets directly and should not invoke sandbox unless needed.
- `guardrail_surfaces_blocker`: sandbox returns `guardrail_triggered`; agent surfaces blocker and audit handles without claiming success.
- `oversized_output_uses_artifact_handle`: tool output is replaced by artifact handle; agent uses the handle/preview and does not ask for full HTML.
- `no_jobs_found_no_fabrication`: agent returns a grounded no-results summary and records crawl metadata without fabricating jobs.
- `invalid_extraction_not_persisted`: sandbox final output fails validation; agent does not call persistence and reports needs-review/error.
- `project_context_used_for_nontrivial_work`: agent loads `project-context` near the start or before recording meaningful scrape progress, depending on the final runtime instruction.
- `saved_jobs_are_queried_before_summary`: after persistence, agent queries stored jobs and bases the final saved-job summary on DB output, not raw extraction memory.
- `reference_proposal_requires_human_review`: if sandbox emits a reference proposal artifact, agent reports proposal availability but does not apply it automatically.

Trajectory assertions should focus on required ordering rather than every incidental tool call:

```text
load_skill(job-listing-scout)
fetch/render page
load_skill(sandbox-page-analyst)
run_skill_script(sandbox_start.py)
run_skill_script(sandbox_exec.py)
run_skill_script(sandbox_finalize.py)
persist_sandbox_job_extraction
query_jobs
```

Use `IN_ORDER` for most trajectory tests because the model may make harmless extra calls such as loading a skill resource or querying context. Use `EXACT` only for narrow regression tests where extra tool calls are truly bad.

For failure-path evals, expected trajectory examples:

```text
guardrail_surfaces_blocker:
  load_skill(job-listing-scout)
  fetch/render page
  load_skill(sandbox-page-analyst)
  run_skill_script(sandbox_start.py)
  run_skill_script(sandbox_exec.py)
  no persist_sandbox_job_extraction

small_fixture_direct_workflow:
  load_skill(job-listing-scout)
  fetch/render page
  persist_sandbox_job_extraction or persist_job
  query_jobs
  no load_skill(sandbox-page-analyst)
```

Final response assertions should require:

- no full HTML or long stdout/stderr
- saved job count
- short saved-job summary
- `audit_id`
- blockers or guardrail errors when present
- statement that jobs were persisted only if persistence actually succeeded

ADK eval tooling should include:

- `tool_trajectory_avg_score` with `IN_ORDER` only as a minimum workflow ordering check
- `rubric_based_tool_use_quality_v1` as the primary tool-choice quality metric
- `rubric_based_final_response_quality_v1` as the primary final response metric
- `final_response_match_v2` only for stable scenarios where semantic matching adds value
- `hallucinations_v1` to ensure the agent stays grounded in tool outputs

ADK eval fixtures should use mocked or fixture-backed tools by default so they are deterministic and cheap. Live Docker + live browser + live model should be reserved for a smaller end-to-end smoke suite.

End-to-end smoke tests:

- run ADK Web or ADK CLI against the ITViec fixture-backed workflow
- verify visible event trajectory includes skill loading, page artifact creation, sandbox script calls, job persistence, and final query
- verify no event returns full HTML or unbounded stdout/stderr
- verify final dashboard/query path can see the saved jobs

## Acceptance Criteria

- The main ADK agent can list and load both `job-listing-scout` and `sandbox-page-analyst`.
- `run_skill_script` works with a configured executor.
- Sandbox scripts create and reuse an `audit_id`.
- Sandbox commands run in Docker with network disabled.
- Full command output is persisted as ADK artifacts when it exceeds preview limits or is part of required evidence.
- ADK receives bounded previews only.
- Hard guardrail triggers stop the Docker runtime immediately and return structured artifact-backed errors.
- Local Docker workspaces are ephemeral materializations, not canonical runtime storage.
- Large pages recommend `sandbox-page-analyst`.
- ITViec fixture extraction produces expected jobs through the sandbox terminal workflow.
- ADK Web can show the full stepwise workflow without exposing full HTML.

## Implementation Steps

- [x] Register `project-context`, `job-listing-scout`, and `sandbox-page-analyst` in the main job scraper skill toolset.
- [x] Decide whether to use ADK `run_skill_script` with a local trusted executor or a custom executor wrapper.
- [x] Replace `SandboxContextFilterPlugin` with `SandboxNoteRefinementPlugin` in `App(...)` so sandbox responses remain visible while runtime notes are injected before model calls.
- [x] Add `SandboxOutputGatePlugin.after_tool_callback` to persist oversized tool outputs before they enter model context.
- [x] Add bounded project-context skill scripts that wrap `.contexts/bin/*`.
- [x] Update `job-listing-scout` instructions to load `sandbox-page-analyst` only when page artifact analysis is needed.
- [x] Rewrite `sandbox-page-analyst` instructions around terminal workflow, not nested LLM agent workflow.
- [x] Add sandbox terminal scripts.
- [x] Implement `sandbox_exec.py` as a bounded bash terminal inside Docker, not a Python-only script runner.
- [x] Add secure Docker container defaults: no network, dropped capabilities, no-new-privileges, resource limits, non-root where feasible.
- [x] Add live-session registry under `.adk/runtime/sandbox_sessions/<user_id>/<session_id>/<audit_id>.json` while keeping ADK artifacts as canonical history.
- [x] Add sandbox progress artifact support (`plan.md`, `progress.json`, and progress update script).
- [x] Make ADK artifacts the primary runtime persistence path for pages, sandbox progress, traces, outputs, and oversized gated content.
- [x] Add terminal guardrail enforcement that stops the runtime and surfaces structured errors when any hard limit is hit.
- [x] Add page profile/token estimate metadata to workspace fetch/render tools.
- [x] Replace OpenAI Agents SDK runtime path or keep it only as historical fallback until removed.
- [x] Add unit tests for skill registration, script execution, page-size policy, artifact handles, output gating, context compaction, live registry behavior, guardrails, saved jobs, and fixture extraction.
- [x] Add ADK eval tests for tool trajectory, final response quality, hallucination resistance, guardrail behavior, and no-full-HTML behavior.
- [x] Rerun ADK Web on the ITViec AI Engineer URL.

## 2026-04-29 Completion Notes

- ADK Web must be launched with `uv run adk web src --host 127.0.0.1 --port <port>` because ADK discovers agent folders under the supplied agents directory.
- Live ITViec smoke now loads `job_scraper`, stores page artifacts with ADK-Web-fetchable names, starts the Docker sandbox, mounts `page.html`, and surfaces compact final output without returning full HTML.
- The live smoke still returns `saved_job_count: 0` because the model inspected the mounted page but finalized without writing a validated `output/final.json`; the remaining work is extraction-strategy tuning, not sandbox/runtime plumbing.

## Remaining Milestone: Protocol Model Validation

The 2026-04-29 ADK Web repair demo proved the agent can react to validation errors, but it also showed schema-error ping-pong: invalid protocol files were accepted into the sandbox workspace and only rejected later by `validate_outputs.py`. The next implementation milestone is to reject malformed protocol files at the write boundary.

Finished foundations:

- [x] Sandbox terminal lifecycle works through ADK Web.
- [x] Large stdout/stderr responses are gated into bounded previews plus artifact handles.
- [x] `sandbox_exec.py` rejects inline heredoc/shell writes to required protocol files.
- [x] Workflow and diagnostic modes are split in `sandbox-page-analyst`.
- [x] Tool responses were redesigned to return structured facts, not prescriptive `required_next`/`suggested_next` instructions.
- [x] The plan now defines shared Pydantic protocol models as the single validation source of truth.

Next implementation steps:

- [x] Consolidate overlapping instruction surfaces according to the ownership split: trim `agent.py`, shrink `job-listing-scout/SKILL.md` to job-domain handoff, keep `sandbox-page-analyst/SKILL.md` as entry/tools only, and keep full extraction procedure in `references/workflow-mode.md`.
- [x] Add tests that assert duplicate sandbox workflow phrases are absent from `agent.py` and `job-listing-scout/SKILL.md`, while required workflow details remain in `workflow-mode.md`.
- [x] Consolidate sandbox protocol docs so `skills/sandbox-page-analyst/references/protocol.md` is canonical and the legacy top-level `skills/sandbox-page-analyst/protocol.md` is removed.
- [x] Add `src/sandbox_page_analyst/protocol_models.py` with Pydantic models for `PageProfileOutput`, `ExtractionStrategyOutput`, `CandidatesOutput`, `ValidationOutput`, `FinalOutput`, and `ReferenceProposalOutput`.
- [x] Add per-file helper function for write-boundary validation, currently `validate_protocol_file_content(path: str, content: str)`.
- [x] Replace script manual loading with the compact `SKILL.md` script catalog plus `--help` on demand. Separate `scripts/README.md` and `scripts/CATALOG.md` files were removed to avoid duplicate instruction surfaces.
- [x] Simplify runtime project-context extraction notes to two required fields, `observations` and `extraction_plan`, plus optional `comparison`; update root/job/sandbox instructions and tests so the agent inspects clues, writes the extraction logic plan, executes, compares, and iterates.
- [x] Add `list_extraction_notes.py` and update runtime workflow instructions so the agent revisits prior notes after each result, reconciles contradictions, refines observations/extraction_plan, and keeps iterating toward the requested output.
- [x] Stop automatic pre-model sandbox response compaction. Sandbox command responses stay available to the agent until summarized by the rolling N+1 rule.
- [x] Add completed-sandbox context pruning to `SandboxNoteRefinementPlugin`: after successful persistence or terminal guardrail, detailed sandbox script responses are replaced with compact placeholders while artifacts and runtime notes remain available.
- [x] Update `SandboxNoteRefinementPlugin` so interval 5 means command 6 triggers compaction of commands 1-5, command 6 remains full, and the summarizer receives both current notes and the 5 previous command contexts.
- [x] Add `planned_next_tool` to session extraction context and enforce it in `SandboxWorkflowGuardPlugin`: after repairable workflow errors, the agent must declare the exact next efficient tool call and the next tool invocation must match that declaration unless the agent first records new evidence and revises the plan.
- [ ] Add output-directory helper such as `validate_output_dir(output_dir: Path)` for `validate_outputs.py` and `sandbox_finalize.py`.
- [ ] Move duplicated per-file shape checks out of `skills/sandbox-page-analyst/scripts/validate_outputs.py` and `src/job_scraper/sandbox_terminal_scripts.py` into the shared model helpers.
- [x] Update `sandbox_write_file.py` / `sandbox_write_main()` so writes to `output/*.json` are validated before persistence. Invalid writes must return `status: "error"`, `error_type: "protocol_model_validation"`, `path`, `model`, `written: false`, and Pydantic `errors`; the existing file must remain unchanged.
- [ ] Keep `output/extractor.py` writes allowed through `sandbox_write_file.py`, but continue banning extractor/protocol writes through `sandbox_exec.py`.
- [ ] Update `validate_outputs.py` to call shared model validation first, then run cross-file checks: candidate/final count agreement, `validation.valid`, ITviec coverage, hashes, and final success constraints.
- [x] Update `validate_outputs.py` so the ADK-facing script can validate by `--audit-id` or clearly route validation through `sandbox_exec.py` inside Docker; avoid host-current-working-directory false negatives.
- [ ] Update `sandbox_finalize.py` to call the shared output-dir validator before accepting a terminal workflow result.
- [x] Update `sandbox_finalize.py` so workflow mode does not overwrite existing `output/final.json` when called with only `--status`/`--summary`; inline status/summary should be diagnostic/debug-only or require a complete `--result`.
- [x] Update extractor workflow docs and tests so `output/extractor.py` writes complete `output/candidates.json` and `output/final.json`; stdout only reports compact counts/paths.
- [x] Add ADK-level loop guard for repeated same finalizer error and repeated same protocol write attempts, because sandbox `command_count` only covers `sandbox_exec.py` commands.
- [ ] Update skill references so the agent knows invalid write responses are facts to reason over, not tool failures requiring a hardcoded next action.
- [ ] Add unit tests for invalid `candidates.json` list writes, missing `final.result`, null string fields, valid protocol writes, and unchanged-file behavior after failed writes. Current coverage includes invalid `candidates.json`, missing `final.result`, unchanged-file behavior, and ADK output-gate preservation of protocol validation facts.
- [x] Preserve protocol validation facts through `SandboxOutputGatePlugin` compaction so ADK Web and the model see `error_type`, `path`, `model`, `written`, and `errors`.
- [x] Add an ADK Web smoke prompt that intentionally submits a bad protocol file and verifies the bad write is rejected immediately, before `validate_outputs.py`.
- [ ] Re-run one ADK Web repair-demo run with a corrected second write. The expected trajectory should show one immediate write-boundary validation error, then a corrected write, not multiple delayed validator errors.
- [x] Re-run full pytest after write-boundary validation and output-gate changes.

2026-04-30 smoke result:

- ADK Web session `2e705983-d9a7-4f00-8f23-31d66d3e3837` rejected `output/final.json` content `{"status":"success","jobs":[]}` at `sandbox_write_file.py`.
- Tool response included `status: "error"`, `error_type: "protocol_model_validation"`, `path: "output/final.json"`, `model: "FinalOutput"`, `written: false`, and Pydantic `errors`.
- The agent final response reported those exact facts with `tool_call_count: 2`; it no longer hallucinated a model name or hid validation details behind artifact paths.
- Verification: `uv run pytest -q` passed with `118 passed`.

2026-04-30 full workflow smoke result:

- ADK Web session `8269de97-975c-457b-8590-d18c2412c9c4` completed the live ITViec workflow through fetch, sandbox start, bounded inspection, extractor write, validation, finalization, persistence, query, and final summary.
- Runtime trajectory was mostly correct: full HTML was saved as a page artifact, the sandbox used 8 commands, `sandbox_write_file.py` rejected malformed `validation.json` and malformed `final.json` immediately, the agent repaired both, `validate_outputs.py` returned `valid: true`, `sandbox_finalize.py` succeeded, and `persist_sandbox_job_extraction` wrote 2 records.
- Workflow quality still has important gaps: the extractor produced 20 URL candidates in `output/extraction.json`, but the agent manually wrote only 2 candidates into `output/candidates.json` and `output/final.json`; this violates the spirit of extractor-backed extraction even though the current validators accepted it.
- The agent claimed `output/reference_proposal.md` and `output/reference_proposal.json` were produced, but the visible trajectory and sandbox output directory did not contain those files. Current `validate_outputs.py`/`sandbox_finalize.py` did not require reference proposal files despite the workflow reference saying they are required.
- Next hardening: shared output-directory validation must require reference proposal files, verify final/candidates come from extractor output or match `output/extraction.json`, and prevent final summaries from claiming artifacts that were never written.

2026-04-30 docstring/plain-English workflow smoke result:

2026-04-30 instruction-surface consolidation ADK Web smoke result:

- ADK Web session `8398cd01-6b5d-4ab2-9ee1-0771b8e539d9`, invocation `e-ceace24f-8da6-467c-93c4-bab57d816680`, audit `sandbox_run_20260430_112853_d729d2a4`.
- Positive: the fresh run loaded the skills, fetched the ITViec page into page workspace/artifacts, started a workflow sandbox, wrote extractor/protocol/reference proposal files, finalized the sandbox, called `persist_sandbox_job_extraction`, and called `query_jobs`.
- Positive: page artifact was large (`content_bytes: 495620`, estimated `123776` tokens), so the page was correctly kept out of the main context and routed to sandbox analysis.
- Positive: sandbox record finalized with `status: finalized`, `mode: workflow`, and `command_count: 17`; persisted candidate/final payload contained 1 selected ITViec job for Switch Supply Pty Ltd.
- Problem: tool trajectory was still too noisy at 82 ADK events and 17 sandbox commands for a single-page scrape.
- Problem: the agent loaded `references/diagnostic-mode.md` during a real workflow run after already using workflow resources, which violates the intended "exactly one mode reference" contract.
- Problem: the agent attempted host-control scripts inside the Docker shell (`python scripts/sandbox_read.py ...`, `python scripts/sandbox_finalize.py ...`) and hit `ModuleNotFoundError: No module named 'job_scraper'`. Host-control scripts must run through `run_skill_script`, not through `sandbox_exec.py`.
- Problem: in-sandbox validation with `python scripts/validate_outputs.py output` failed because the sandbox image lacks `typer`. Either the validator must be stdlib-only inside Docker, the image must include script runtime deps, or validation should be routed host-side with `--audit-id`.
- Problem: final response reported `saved_job_count: 1`, but `examples_from_query_jobs` included stale rows from earlier runs because `query_jobs` was not constrained enough to the current audit/source/job set.
- Next hardening should add explicit tool/script boundary guidance and tests: sandbox_exec may run shell commands and `output/extractor.py`; host-control scripts (`sandbox_start.py`, `sandbox_read.py`, `sandbox_write_file.py`, `sandbox_finalize.py`) must be invoked via `run_skill_script`; validation path must be made consistent; final query verification must avoid stale rows.

2026-04-30 follow-up guardrail implementation:

- Added an ADK before-tool policy that allows only one sandbox mode reference load per sandbox task. Loading `workflow-mode.md` and then `diagnostic-mode.md` now returns `status: error`, `error_type: sandbox_mode_resource_policy`, and `guardrail: single_mode_resource`.
- Added an ADK before-tool policy that blocks host-control scripts inside `sandbox_exec.py`. Commands such as `python scripts/sandbox_finalize.py ...` now return `status: error`, `error_type: sandbox_host_control_script_policy`, and `guardrail: host_control_script_inside_sandbox_exec`.
- Added `typer`, `rich`, and `loguru` to `docker/sandbox/requirements.txt`, rebuilt the configured `job-scraper-sandbox:py313` image, and verified imports inside `--network none`.
- Updated `sandbox-page-analyst` instructions to match runtime policy: one mode reference only, host-control scripts through `run_skill_script`, sandbox exec only for shell inspection, `output/extractor.py`, and trusted validation.
- Wired trusted `validate_outputs.py` validation into `sandbox_finalize.py`, so finalization now rejects the exact ITViec failure mode where a listing page exposes many detail URLs but `candidates.jobs` contains only one success job.
- Explanation for the 1-job smoke result: the agent treated the `job_selected` URL parameter as the target job and hand-wrote/accepted a single selected-detail candidate after in-sandbox validation failed. Because finalization previously used weaker validation than `validate_outputs.py`, that single-job success was allowed. The trusted finalizer validation closes that bypass.

- ADK Web session `aeb61e69-d74c-474a-8c60-2b5a317da3c3` confirmed the updated skill text and tool docstrings loaded in the runtime.
- The trajectory regressed before extraction: the agent saved the page with `fetch_page_to_workspace`, but started the sandbox without passing the saved page artifact, tried a nonexistent `scripts/sandbox_status.py`, repeatedly restarted the same workflow sandbox, loaded diagnostic references during a workflow run, and stopped without extraction/finalization/persistence.
- Audit `sandbox_run_20260430_080035_7a1472b5` was left in `running` registry state with no commands executed and no page mounted; the test Docker container was stopped manually.
- Lesson: plain-English workflow is good, but the agent also needs discoverable script/tool affordances. The sandbox skill should explicitly describe available script capabilities and when to use them, while still avoiding brittle step-by-step choreography.

2026-04-30 failed workflow loop diagnosis:

- ADK Web session `5e02421f-048e-4238-ae17-babd69e3e516` loaded the then-existing long script documentation and mounted the ITViec page correctly, but then looped through protocol writes/finalize attempts.
- Root cause 1: `sandbox_finalize.py --status ... --summary ...` overwrote `output/final.json` with an inline wrapper containing `result: {}` when no complete `--result` was supplied; finalization then rejected `output/final.json result.jobs must be a list`.
- Root cause 2: `validate_outputs.py output` was invoked as a host skill script, not inside the sandbox workspace, so it returned false missing-file errors.
- Root cause 3: the extractor discovered 20 ItemList URLs but printed only a preview; the agent manually wrote one job into `output/candidates.json`/`output/final.json` with `count: 20`, violating extractor-backed extraction.
- Correction: the canonical protocol is now `skills/sandbox-page-analyst/references/protocol.md`; the legacy top-level protocol file was removed. The workflow must use compact script discovery plus `--help` as needed, and persisted extractor outputs are the only source of truth.

Acceptance for this milestone:

- Invalid protocol files never become persisted workspace state through `sandbox_write_file.py`.
- `validate_outputs.py` still catches cross-file inconsistencies that a single-file model cannot know.
- Tool responses contain validation facts only and do not include `required_next`, `suggested_next`, or hardcoded repair instructions.
- The agent can recover from a malformed write by reasoning from the returned validation errors.
