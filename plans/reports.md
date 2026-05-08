# Reports Plan

This document defines report types, report templates, and report-generation workflow for the job scraper project.

Physical report outputs belong in `reports/`. Raw run artifacts belong in `data/`.

## Sandbox Token Report

Purpose: measure token consumption and completion time for the sandbox-final-only scraping approach once the `run_sandbox_agent` workflow is implemented.

### Metrics

Each sandbox run should report:

- `adk_usage.input_tokens`
- `adk_usage.output_tokens`
- `adk_usage.total_tokens`
- `adk_usage.completion_time_ms`
- `sandbox_usage.input_tokens`
- `sandbox_usage.output_tokens`
- `sandbox_usage.total_tokens`
- `sandbox_usage.completion_time_ms`
- `combined_usage.input_tokens`
- `combined_usage.output_tokens`
- `combined_usage.total_tokens`
- `combined_usage.completion_time_ms`
- `status`
- `error`
- `models`
- `url`
- `audit_id`

### Current Status

No sandbox token run has been executed yet. The sandbox worker and token instrumentation are still planned.

### Latest Run Template

```json
{
  "status": "pending",
  "pipeline": "sandbox_final_only",
  "url": "",
  "models": {
    "adk_main_agent": "openai/gpt-5.4-mini",
    "sandbox_agent": "gpt-5.4-mini",
    "sandbox_reasoning_effort": "high"
  },
  "adk_usage": {
    "requests": null,
    "input_tokens": null,
    "output_tokens": null,
    "total_tokens": null,
    "completion_time_ms": null
  },
  "sandbox_usage": {
    "requests": null,
    "input_tokens": null,
    "output_tokens": null,
    "total_tokens": null,
    "completion_time_ms": null
  },
  "combined_usage": {
    "requests": null,
    "input_tokens": null,
    "output_tokens": null,
    "total_tokens": null,
    "completion_time_ms": null
  },
  "audit_id": "",
  "error": ""
}
```

### Implementation Notes

- Use Google ADK event/session usage for the main ADK agent when available.
- Use OpenAI Agents SDK `result.context_wrapper.usage` for the nested sandbox agent.
- Measure wall-clock completion time for both the ADK workflow and sandbox-agent run.
- Keep ADK and sandbox usage separated, then compute combined totals.
- Do not count terminal stdout/stderr bytes as tokens unless they are sent to an OpenAI model.
- Store raw per-call metrics under `data/sandbox_runs/<audit_id>/usage.json`.
- Store generated physical report outputs under `reports/` only when there is a reviewed run worth preserving.
