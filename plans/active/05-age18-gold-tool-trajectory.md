# AGE-18 Gold Tool Trajectory: ITviec Fixture Extraction

**Status:** Completed manual gold run on 2026-05-21.

**Purpose:** Record the exact task trajectory Codex followed for a fixture-backed ITviec extraction run so it can become the template for ADK eval trajectory scoring. This is not a hypothetical plan. These steps were executed against the sandbox runtime and a dedicated verification database.

**Audit id:** `sandbox_run_age18_gold_20260521_104255`

**Fixture:** `tests/fixtures/itviec_ai_engineer_ha_noi.html`

**Source URL:** `https://itviec.com/it-jobs/ai-engineer/ha-noi`

**Verification DB:** `/tmp/age18_gold_jobs.db`

**Sandbox registry record:** `src/job_scraper/.adk/runtime/sandbox_sessions/user/local/sandbox_run_age18_gold_20260521_104255.json`

## Target Trajectory Shape

The desired agent behavior is:

1. Bootstrap project and task context before acting.
2. Load the relevant skill and contract resources.
3. Start one workflow sandbox with the page artifact mounted.
4. Run bounded inspections to identify repeated job structure and required fields.
5. Record the chosen extraction strategy and output expectations before producing final outputs.
6. Write a run-scoped extractor or equivalent accountable producer.
7. Validate against the protocol/fixture, repair from concrete validation failures, then validate again.
8. Finalize the sandbox only after validation passes.
9. Promote the finalized sandbox output, query persisted rows, and record crawl metadata.
10. Final-answer from verified persisted state, not from memory or old query results.

## Executed Step Log

| Step | Action | Evidence / Output | Why |
| --- | --- | --- | --- |
| 1 | Loaded repo context with `.contexts/bin/context_overview`. | Confirmed active context protocol and project goal around harnessing/context engineering. | Avoid starting from stale assumptions and honor repo-local agent workflow. |
| 2 | Listed active tasks with `.contexts/bin/list_tasks --status active`. | Confirmed the active AGE-18 direction: compare prompt construction later, but first stabilize trajectory-only evaluation. | Keep the work aligned with the current task instead of adding premature comparison code. |
| 3 | Read `skills/sandbox-page-analyst/SKILL.md`. | Learned the sandbox workflow, output contract, and restriction that host workspace paths are audit metadata only. | Use the intended skill workflow rather than improvising a parallel extraction path. |
| 4 | Checked script CLIs with `sandbox_start.py --help`, `sandbox_write_file.py --help`, and `sandbox_exec.py --help`. | Confirmed workflow mode, page artifact mounting, audit id, and command execution semantics. | Ensure subsequent tool use matches the actual ADK/skill primitives. |
| 5 | Emitted the protocol contract with `protocol_contract.py --pretty`. | Contract required `page_profile.json`, `extraction_strategy.json`, `extraction_run.json`, `candidates.json`, `validation.json`, `final.json`, and `run_summary.md`; if `output/extractor.py` exists, `script_manifest.json` is also required. | Establish the output surface before authoring extraction artifacts. |
| 6 | Started workflow sandbox with the ITviec fixture mounted as `page.html`. | Sandbox status `running`; container `052243d1dc3c8f9edc56eaee052270ad08dcc98a9215a33ec61f0a3e1bda32cf`; visible files included `page.html`, `inputs.json`, `progress.json`, `output/`, `scripts/`, `schemas/`, `trace.jsonl`. | Create the controlled runtime the agent should use for real extraction work. |
| 7 | Sandbox command 001: counted ITviec listing markers in `page.html`. | `job_card_class 20`, `job_card_target 20`, `job_url_attr 20`, `job_title_target 20`, `title_marker True True`. | Derive the expected output count from observed repeated page structure. |
| 8 | Sandbox command 002: listed workspace files and inspected `inputs.json`, `progress.json`, `plan.md`. | Confirmed `page_bytes 496779`, source URL, fixture path, initialized stage, and next steps. | Verify the mounted artifact and local sandbox instructions before parsing. |
| 9 | Sandbox command 003: sampled the first 3 `.job-card` blocks with BeautifulSoup. | Saw 20 cards and real fields for title, company, salary text, role category, location, and tags. | Confirm that the repeated blocks are job postings and identify field availability. |
| 10 | Read validator/finalizer code enough to understand success gates. | Validator compares expected job URLs and exact fields for fixture runs, and checks protocol files plus script manifest hashes. | Treat validation as the source of truth, not just schema success. |
| 11 | Sandbox command 004: inspected first card DOM attributes and classes. | Identified selectors: title `h3[data-search--job-selection-target=jobTitle]`, job URL from `data-search--job-selection-job-url-value`, company link under `/companies/`, salary `.salary`, tags under skill-tag links. | Turn observations into a concrete, evidence-backed parser plan. |
| 12 | Sandbox command 005: inspected location containers for the first two cards. | Card 0: `Remote | Ho Chi Minh - Da Nang - Ha Noi`; card 1: `At office | Ha Noi`. | Derive `location_raw`, location text, and `remote_type` behavior from observed markup. |
| 13 | Wrote `output/extractor.py` through `sandbox_write_file.py`. | Script generated all required protocol outputs and `script_manifest.json`; it canonicalized ITviec `/content?...` URLs to stable listing URLs. | Use an accountable producer script because the page has repeated structure and exact fixture validation. |
| 14 | Sandbox command 006: ran `python output/extractor.py`. | Output: `{"status":"success","jobs":20,...}` with all required output filenames present. | Produce complete extraction artifacts from the recorded method. |
| 15 | Ran host validator with `validate_outputs.py --audit-id sandbox_run_age18_gold_20260521_104255`. | Validation failed with 20 expected and 20 actual jobs, no missing/extra URLs, but tag mismatches for two jobs missing `Fresher Accepted`. | Use validator failure to target the next repair instead of broad guessing. |
| 16 | Sandbox command 007: inspected the two failing cards for `Fresher Accepted`. | Found `Fresher Accepted` is an `a.text-reset` skill-tag link with `href="/it-jobs/fresher-accepted?click_source=Skill+tag"`, not an `a.itag`. | Identify the precise selector gap behind the validation failure. |
| 17 | Patched `output/extractor.py` through `sandbox_apply_patch.py`. | Changed tag selector from `a.itag` to `a[href*="click_source=Skill+tag"]`; new extractor sha `d9e6ae4ebbce69b8ea680a73012a0b3569b0646b788780c71cd36907e20b907c`. | Repair the parser narrowly from observed evidence and keep the script manifest accountable. |
| 18 | Sandbox command 008: reran `python output/extractor.py`. | Output again reported `status: success`, `jobs: 20`, and all protocol output files. | Regenerate outputs after the selector repair. |
| 19 | Re-ran host validation. | Passed with `valid: true`, `warnings: []`. | Confirm the repaired artifacts match the fixture and protocol contract. |
| 20 | Finalized the sandbox with `sandbox_finalize.py --audit-id sandbox_run_age18_gold_20260521_104255`. | Registry status became `finalized`, command count `8`, no guardrail or error. | Lock the sandbox output before promotion. |
| 21 | Promoted finalized output with `promote_sandbox_extraction(audit_id=..., db_path="/tmp/age18_gold_jobs.db")`. | Result: `status: success`, `written_count: 20`, `validated_count: 20`, `warnings: []`, source `sandbox_final_json`. | Persist only finalized sandbox output through the ADK persistence primitive. |
| 22 | Queried persisted jobs with a too-narrow filter: `query_jobs(keyword="Engineer", source_name="ITviec", limit=25, db_path=...)`. | Result count `0`. | This was a useful negative check: query filters must match stored state, and an empty query is not proof of failed promotion. |
| 23 | Inspected stored source names in SQLite. | Found `source_name: "ITviec AI Engineer Hanoi"`, count `20`. | Determine the correct verification filter from persisted data. |
| 24 | Re-ran verification queries. | `query_jobs(limit=25)` returned `20`; `query_jobs(keyword="Engineer", limit=25)` returned `18`; `query_jobs(source_name="ITviec AI Engineer Hanoi", limit=25)` returned `20`. | Verify persistence from actual stored rows and choose robust eval expectations. |
| 25 | Recorded crawl metadata with `record_crawl_run(...)`. | Result: `status: success`, run id `sandbox_run_age18_gold_20260521_104255`, `discovered_count: 20`, `written_count: 20`, `error_count: 0`. | Complete the runtime handoff after persistence. |
| 26 | Captured artifact hashes and the trace. | Output hashes are listed below; trace is in the sandbox workspace `trace.jsonl`. | Make the trajectory auditable and reproducible. |

## Final Artifact Hashes

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `output/page_profile.json` | 463 | `24435fec42f24d39139263eb1e7fd22803a5466c7065a07919a967b559387f62` |
| `output/extraction_strategy.json` | 1319 | `f7651050ecf754782c9d3b064c5e4dc6006d59457440f8751b2a907536ed44e8` |
| `output/extraction_run.json` | 2420 | `d0c1cbcae5f8a16e559f983cf7c98c583699f3967c365d348d4a00cb2e353519` |
| `output/candidates.json` | 26261 | `336e2339e561ba6f999760829cf818c72a66e67e6f2a1d6731eb0aae2256dd07` |
| `output/validation.json` | 228 | `dade1dd4df2f58f9f287396e1f1e7cc858c7795491db2881f7a912d8f356ad8d` |
| `output/final.json` | 27890 | `7756cc91674bb0fc260db151c9fded5b1b2946966a5b13aad19317060e25d07a` |
| `output/run_summary.md` | 495 | `90c58f4d46dd54f4dedd99f01fe359d3e3c034fd3eb8b9971de297b764832391` |
| `output/script_manifest.json` | 828 | `e29484808d1ba079bf74a9f12992b6a68a87cdfa15bac141d62d908ed945c7dc` |
| `output/extractor.py` | 12005 | `d9e6ae4ebbce69b8ea680a73012a0b3569b0646b788780c71cd36907e20b907c` |

## Trajectory Lessons For ADK Eval

The trajectory metric should reward these behaviors:

- Context and skill bootstrap before state-changing extraction work.
- Contract/resource loading before output production.
- Bounded inspection before extractor/output authoring.
- Explicit count derivation from repeated observed units.
- Output production through a recorded method, with required protocol files.
- Validation before finalization and promotion.
- Targeted repair after validation failure.
- Promotion only from finalized sandbox artifacts.
- Query verification against persisted rows, with recovery from bad filters.
- Crawl-run metadata recording after persistence.

The metric should not require exact command strings. Equivalent commands should score well if they preserve the same semantic milestones and respect ordering constraints, especially inspection before production, validation before finalization, finalization before promotion, and promotion before final answer.

## Reuse Update

After this gold run, the validated ITviec extraction method was promoted into the job-listing source reference `skills/job-listing-scout/references/itviec-listing-v1/REFERENCE.md`. Future AGE-18 trajectories should insert a reuse phase after page classification and source-reference loading:

1. Check the reusable package reference.
2. Run `references/job-listing-scout/itviec-listing-v1/scripts/probe_layout.py --html page.html`.
3. If the probe matches, run `references/job-listing-scout/itviec-listing-v1/scripts/extractor.py --html page.html --output-dir output`.
4. Validate/finalize the produced protocol files.
5. Patch the materialized `output/reused_extractor.py` only if validation reports concrete drift.

For eval scoring, package probe and matched reuse should receive credit before new extractor authoring. Writing a brand-new extractor despite a matching validated package should be treated as lower-quality tool trajectory unless the agent records a concrete reason the package did not apply.
