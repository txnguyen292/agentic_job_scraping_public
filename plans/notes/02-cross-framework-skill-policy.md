# Cross-Framework Skill Policy

## Context

We want skills to behave like reusable packages, not just prompt text plus helper files. A reusable skill should carry:

- agent-facing instructions
- references
- scripts
- schemas
- runtime policy code or policy definitions

Markdown guidance is useful, but it cannot enforce workflow invariants. For example, telling the agent to run `output/extractor.py` after writing it is not enough; the runtime should block finalization, persistence, query, or final answers until that verification happens.

## Design Principle

Separate policy definition from framework adapters.

```text
Skill package owns policy semantics.
Framework adapter enforces those semantics through native hooks.
```

The portable contract should be a small policy engine:

```text
policy_engine.handle(event, state) -> decision
```

Events should be framework-neutral:

- `skill_loaded`
- `mode_loaded`
- `tool_requested`
- `tool_completed`
- `script_written`
- `command_executed`
- `artifact_created`
- `validation_passed`
- `finalize_requested`
- `final_response_requested`

Decisions should also be framework-neutral:

- `allow`
- `block(message, required_next)`
- `inject_context(message)`
- `update_state(patch)`
- `terminal_guardrail(reason)`

## Proposed Skill Layout

```text
skills/sandbox-page-analyst/
  SKILL.md
  references/
  scripts/
  schemas/

  policies/
    manifest.json
    skill.policy.yaml
    workflow.policy.yaml
    diagnostic.policy.yaml
    engine.py
    rules.py
    state.py
    adapters/
      adk.py
      openai_agents.py
      langgraph.py
      codex.py
```

`SKILL.md` tells the agent what world it entered.

`references/*.md` explain how to succeed.

`schemas/*.json` define output contracts.

`scripts/*.py` provide executable capabilities.

`policies/*.yaml` define portable policy semantics.

`policies/adapters/*.py` map runtime hooks to portable policy events.

## Example Policy Rule

```yaml
id: sandbox.workflow.extractor_must_run
scope: workflow
when:
  event: script_written
  path: output/extractor.py
require_before:
  - sandbox_finalize
  - validate_outputs
  - persist
  - query
  - record_crawl_run
  - final_response
must_observe:
  event: command_executed
  command_contains: "python output/extractor.py"
on_violation:
  status: error
  guardrail: written_script_must_run_before_finalization
  message: Run the written extractor before finalization.
```

## Framework Adapters

ADK adapter:

- `before_tool_callback` maps to `tool_requested`.
- `after_tool_callback` maps to `tool_completed`, `script_written`, `command_executed`, and `artifact_created`.
- `before_model_callback` maps to context injection decisions.
- `after_model_callback` maps to `final_response_requested`.

OpenAI Agents adapter:

- tool wrappers map to `tool_requested` and `tool_completed`.
- runner lifecycle or trace hooks map to command/artifact/final events.

LangGraph adapter:

- node middleware maps to `tool_requested` and `tool_completed`.
- graph state reducers store policy state.
- conditional edges can enforce block/repair routing.

Codex adapter:

- tool/runtime wrappers map to action events.
- skill loader activates policy bundles.

## Current Repo Implication

The current ADK plugin approach is the right enforcement mechanism, but the policy code lives in the host project under `src/job_scraper/adk_plugins.py`. That makes `sandbox-page-analyst` less reusable than it should be.

The next architectural refactor should move sandbox-specific policy semantics into `skills/sandbox-page-analyst/policies/`, then keep `src/job_scraper/` as the ADK host adapter and app wiring layer.

## Migration Path

1. Add `skills/sandbox-page-analyst/policies/manifest.json`.
2. Move pure state keys and rule helpers into skill-owned policy modules.
3. Keep ADK `BasePlugin` subclasses as the first adapter.
4. Update `src/job_scraper/agent.py` to load plugins through a skill policy loader.
5. Keep compatibility re-exports from `src/job_scraper/adk_plugins.py` until tests and imports settle.
6. Later add OpenAI Agents or LangGraph adapters without rewriting policy semantics.

