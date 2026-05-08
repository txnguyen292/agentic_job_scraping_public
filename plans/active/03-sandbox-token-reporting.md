# Sandbox Token Reporting Plan

**Goal:** Measure sandbox scraper token usage and completion time without creating a broad metrics framework.

**Scope:** Sandbox-final-only workflow. No A/B testing yet.

## Metrics

Every measured sandbox scrape should produce:

- `adk_usage.requests`
- `adk_usage.input_tokens`
- `adk_usage.output_tokens`
- `adk_usage.total_tokens`
- `adk_usage.completion_time_ms`
- `sandbox_usage.requests`
- `sandbox_usage.input_tokens`
- `sandbox_usage.output_tokens`
- `sandbox_usage.total_tokens`
- `sandbox_usage.completion_time_ms`
- `combined_usage.requests`
- `combined_usage.input_tokens`
- `combined_usage.output_tokens`
- `combined_usage.total_tokens`
- `combined_usage.completion_time_ms`

## Boundary

Keep framework usage collection behind small adapters:

```text
ADK runner/events
  -> ADKUsageCollector

OpenAI Agents SDK SandboxAgent result
  -> SandboxUsageCollector

UsageReportWriter
  -> data/sandbox_runs/<audit_id>/usage.json
  -> plans/reports.md summary or reports/ generated artifact
```

`src/sandbox_page_analyst/` should be the only package that imports OpenAI Agents SDK. Other modules consume project-owned data classes and dictionaries.

## Raw Artifact

Write machine-readable usage to:

```text
data/sandbox_runs/<audit_id>/usage.json
```

Shape:

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
    "status": "success",
    "requests": 2,
    "input_tokens": 1800,
    "output_tokens": 500,
    "total_tokens": 2300,
    "completion_time_ms": 7200,
    "request_usage_entries": []
  },
  "sandbox_usage": {
    "status": "success",
    "requests": 4,
    "input_tokens": 6200,
    "output_tokens": 2100,
    "total_tokens": 8300,
    "completion_time_ms": 18420,
    "request_usage_entries": []
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

If ADK usage is unavailable from the current run path, set:

```json
{
  "adk_usage": {
    "status": "unavailable",
    "reason": "ADK usage must be collected from invocation events after the tool returns."
  }
}
```

Do not fabricate ADK token estimates.

## Human Report

Update the planning/report template:

```text
plans/reports.md
```

Generated physical report artifacts, when needed, should be written under:

```text
reports/
```

Report summaries should include:

- latest run
- model names
- status and error
- ADK usage
- sandbox usage
- combined usage
- completion time
- audit ID and usage artifact path

## Implementation Steps

- [ ] Add project-owned usage data classes or plain serializers.
- [ ] Implement `collect_sandbox_usage(result, completion_time_ms)` from `result.context_wrapper.usage`.
- [ ] Implement `collect_adk_usage(events, completion_time_ms)` from ADK `event.usage_metadata`.
- [ ] Implement combined usage calculation.
- [ ] Write `usage.json` through the sandbox audit writer.
- [ ] Add a small report updater command or function.
- [ ] Update `plans/reports.md` after the first real sandbox run.
- [ ] Optionally write generated physical report outputs under `reports/`.

## Validation

- Unit-test OpenAI Agents SDK usage serialization with mocked usage objects.
- Unit-test ADK usage accumulation with mocked events containing `usage_metadata`.
- Unit-test combined totals.
- Unit-test report rendering from a fixture `usage.json`.
