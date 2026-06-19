# ADK Eval Strategy

Use three layers of evaluation for agent workflows.

## Layer 1: Deterministic Skeleton

Use `tool_trajectory_avg_score` only for stable milestone calls with predictable
arguments. Keep the expected list short.

- Threshold: `1.0` for small, stable smoke evals.
- Use `IN_ORDER` when extra evidence or repair calls are acceptable.
- Do not match dynamic planning payloads such as `update_extraction_context`
  arguments or exact sandbox probe commands unless the eval is intentionally a
  strict regression fixture.

The main `eval_config.json` keeps a looser `0.7` trajectory threshold for older
mixed workflow cases while the suite is being split into focused eval configs.

## Layer 2: Elastic Quality

Use rubric metrics for behavior that should be correct but not mechanically
identical across runs.

- Tool-use rubric threshold: `0.85`.
- Final-response rubric threshold: `0.85`.
- For AGE-10 goal-contract evals, do not gate on `hallucinations_v1`: it is
  opaque and can return N/E without rationale. Use explicit groundedness
  rubrics instead so failures name the unsupported claim and evidence gap.

With four binary rubrics, `0.85` effectively requires every rubric to pass
while leaving room for judge scoring details. Use this layer for goals where the
agent should choose the evidence path, script shape, or repair strategy.

## Layer 3: Hard Invariants

Use pytest, custom metrics, or a saved-trace parser for invariants that must be
true but are too specific for an LLM judge and too dynamic for exact trajectory
matching.

- Producer scripting must be blocked until the active `immediate_goal` is
  evidence-backed.
- The startup `initial_plan` must be adapted into a live `extraction_plan` after
  early evidence instead of remaining the late-turn plan.
- Once `extraction_plan` exists, injected session context should omit
  `initial_plan`; `extraction_strategy` should derive from `extraction_plan`,
  and `immediate_goal` should target the current step inside that strategy.
- The run must not claim full extraction or persistence during a bounded
  boundary-goal eval.

These checks should fail deterministically and explain the missing field,
ordering, or guardrail behavior.

## AGE-10 Goal Contract Evals

`eval_config_goal_contract.json` intentionally omits strict trajectory scoring.
It tests whether the agent:

- uses the fixture as a page artifact instead of raw HTML in context,
- records an evidence-backed `immediate_goal` with working strategy, validation
  strategy, and next script objective before producer scripting,
- adapts the startup `initial_plan` into the current `extraction_plan` after
  early evidence,
- derives `extraction_strategy` from `extraction_plan` and uses `immediate_goal`
  for the current bounded strategy step,
- validates only the bounded incremental goal,
- gives a compact, grounded final response without claiming full extraction or
  persistence,
- names the next incremental goal and only reports completion or missing
  artifacts when direct trace/tool evidence supports those claims.

Keep exact trajectory out of this config unless a future case needs a tiny
`IN_ORDER` smoke skeleton for stable calls such as skill loading and fixture
loading. Hard ordering invariants that cannot be left to an LLM judge should be
covered by unit tests, custom metrics, or a trace parser over saved eval history.

## Repeated AGE-10 Pytest Harness

Use pytest as an opt-in runner around ADK eval when you want an average score
over repeated LLM-judge samples:

```bash
JOB_SCRAPER_RUN_ADK_EVAL_AVERAGE=1 \
JOB_SCRAPER_ADK_EVAL_RUNS=5 \
.venv/bin/pytest -m adk_eval tests/test_age10_adk_eval.py -s
```

The local/dev goal-contract config uses `num_samples: 1` so this command runs
five independent agent attempts with one judge sample per metric. Preserve the
heavier 5x3 setup for PR/merge confidence by using
`tests/eval/eval_config_goal_contract_pr.json`.

The harness uses the same evalset prompt as the AGE-10 case:

```text
Start extracting job listings from the fixed ITviec AI Engineer Hanoi HTML fixture.
```

By default this reports average metric and rubric scores without turning the
whole pytest run red for below-threshold averages. AGE-10 no longer gates on
`hallucinations_v1`; groundedness should be evaluated through explicit final
response rubrics that include judge rationale.

To also make average metric thresholds gating, add:

```bash
JOB_SCRAPER_ADK_EVAL_ASSERT_THRESHOLDS=1
```

## ADK Observability Dashboard

AGE-19 keeps observability local to the repo. Generate the companion dashboard
from ADK `session.db` files in the current checkout:

```bash
uv run job-scraper-adk-dashboard \
  --output reports/adk-token-dashboard.html
```

Pass one or more `--db` paths when you want to inspect a specific ADK session
store instead of relying on current-checkout discovery.

The dashboard shows cached input, non-cached input, output, reasoning, session
drill-down, and expandable ChatCompletion details without requiring a separate
observability server.

Pricing is refreshed from LiteLLM's machine-readable model pricing map by
default. To refresh the local cache separately:

```bash
uv run python scripts/update_adk_model_pricing.py \
  --output reports/adk-model-pricing.json
```
