# Template Design Specification — RET-C2-344

## Position in AgentCore Architecture

| Field | Value |
|---|---|
| Agent Class | `DemandForecastAgent` (`src/graph/graph.py`; exported as `Graph`) |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Category | Cat 2 — domain-specific pipeline |
| Generation mode | Deterministic. No model is invoked; `requires.secrets` and `requires.extras` are both empty, derived from the code. |

**Three-layer separation**

- **State** — flat TypedDict composition (no Pydantic; msgpack-incompatible objects are
  excluded by construction). Structured fields are stored as JSON strings.
- **Node** — L1 inheritance. Each node overrides `execute(self, state) -> dict` only and
  returns the fields it changed, never the full state.
- **Graph** — composition. `register_nodes()` is the single substitution point.

## Architecture Overview

Two layers. The outer graph is the fixed framework backbone; the whole domain workflow is
one node inside it.

```
outer (AgentBaseGraph)
  START → initialize → pre_process → main → {route} → post_process → finalize → END
                                              ↓ (retry, bounded by max_retry)
                                           pre_process

inner (BaseGraph, reached through the `main` slot)
  START → input_parse → data_validate → forecast_call → forecast_agg
        → waste_risk → order_adjust → output_format → output_validate → END
```

`route()` sends every non-success status straight to `finalize`, skipping `post_process`.
That is why the output gate inside the inner graph — not the outer `post_process` slot — is
the point at which a rejected answer has to be replaced: on an error path `post_process`
never runs.

### Outer node configuration

| Node | Responsibility | Input state | Output state | Inherits / overrides |
|---|---|---|---|---|
| initialize | framework default | — | — | `InitializeNode` (default) |
| pre_process | the caller contract for the registry path: screens the request string and reduces the request context to the supported, inert subset | `user_input`, `input_context` | `validated_request`, or a constant refusal notice + `error_code` | `FunctionNode` |
| main | delegates the whole domain workflow to the inner graph | full state | `result`, `output_text`, `status`, `error_code` | `GraphNode` |
| post_process | records that an answer crossed the boundary (success route only) | `output_text` | `status` | `FunctionNode` |
| finalize | framework default | — | — | `FinalizeNode` (default) |

The `main` slot carries the declared runtime configuration into the inner graph. Building
the inner graph bare would leave every value in `config/config.yaml` unread while still
looking configured.

### Inner node configuration

| Node | Responsibility | Input state | Output state |
|---|---|---|---|
| `input_parse` | resolves the request into structured fields — the validated request context wins over anything read out of the sentence | `forecast_request`, `raw_input` / `user_input` | `store_group`, `sku_category`, `horizon_days` |
| `data_validate` | input gate: allowlists and the 1–90 horizon bound | the three fields above | `validation_passed`, `out_of_scope`, `validation_error` (a closed-set label) |
| `forecast_call` | one forecast per store in the group, under a wall-clock budget from `timeout_s` | `store_group`, `sku_category`, `horizon_days` | `raw_forecast_results` |
| `forecast_agg` | per-day totals and averages, peak day | `raw_forecast_results` | `aggregated_forecast` |
| `waste_risk` | risk level against per-SKU shelf-life thresholds | `aggregated_forecast`, `sku_category` | `food_waste_risk_flags` |
| `order_adjust` | per-store adjustment from risk level plus an over/understock guard band | `aggregated_forecast`, `raw_forecast_results`, `food_waste_risk_flags` | `order_adjustments` |
| `output_format` | renders the report; the disclaimer block is appended on every path | the four structured fields above | `output_text`, `disclaimer_present` |
| `output_validate` | output gate: non-empty, disclaimer present, no secret-shaped value | `output_text`, `disclaimer_present` | `status`, `error_code`, and on violation a constant notice in `output_text` |

The topology is linear — no conditional edges between domain nodes. Because of that, every
node checks whether an earlier one already failed and passes a failed run through untouched;
without that check the next node's success would overwrite the failure rather than handle it.

### Cross-boundary request hand-off

`GraphNode` invokes the inner graph as `subgraph.invoke(user_input, session_id, ctx)` and
forwards no structured parameters. The validated request context therefore travels through
`src/graph/context_bridge.py`: the outer hand-off stashes it in a ContextVar immediately
before the call, and the inner graph's `_extra_initial_state()` seeds it into the inner
initial state. Only validated values cross; the raw request context never does.

### State definition

Framework fields (`user_input`, `session_id`, `correlation_id`, `trace_id`, `node_history`,
`error_log`, …) are inherited from `AgentState`. The domain fields:

| Field | Type | Purpose | Required |
|---|---|---|---|
| `validated_request` | JSON string | the validated request context, outer side | no |
| `forecast_request` | JSON string | the same, seeded into the inner graph | no |
| `raw_input` | str | the caller's request string | yes |
| `store_group` | str \| None | resolved store group id | no |
| `sku_category` | str \| None | resolved SKU category | no |
| `horizon_days` | int \| None | forecast horizon, 1–90 | no |
| `validation_passed` | bool | input gate verdict | yes |
| `validation_error` | str \| None | closed-set label for the refusal reason | no |
| `out_of_scope` | bool | unsupported store group or SKU | yes |
| `raw_forecast_results` | JSON string | one forecast per store | no |
| `aggregated_forecast` | JSON string | regional summary | no |
| `food_waste_risk_flags` | JSON string | per-SKU risk flags | no |
| `order_adjustments` | JSON string | per-store recommendations | no |
| `output_text` | str \| None | the rendered report, or the withheld notice | no |
| `disclaimer_present` | bool | cross-check for the output gate | yes |
| `status` / `agent_status` | str / enum | backbone status mirror | yes |
| `error_code` / `error_message` | str \| None | closed-set failure labels | no |

**State constraints (mandatory)**

- Flat TypedDict only — primitives and JSON-serializable types.
- Structured values (dict / list) are stored as JSON **strings**, written with `to_json()`
  and read with `from_json()`. A bare container in a checkpointed field is not
  msgpack-safe.
- No credentials, API keys or tokens in state.
- The invocation context travels through `config["configurable"]`, not through state.
- No Pydantic models, dataclasses or arbitrary Python objects.

## Security design

| Layer | Where it lives | What it does |
|---|---|---|
| S-1 trust gate | every node's `required_trust_level` | Declared at the level `config/agent.yaml` declares for the agent (`VERIFIED_EXTERNAL`), except `input_parse`, which needs less. A node requiring MORE than the agent's entry level can never execute, so `tests/unit/test_trust_alignment.py` reads both sides from the files and fails on that drift. |
| S-2 input screen | `src/services/input_guard.py`, enforced in `pre_process` and `input_parse` | Chat-template control markers as a class (`<\|…\|>`, `[INST]`, `<<SYS>>`, `<s>`, `<system>`), directive phrases, and credential shapes — screened raw and again after markup is stripped, depth-first over parsed structures including keys. Enforced by the template rather than left to the framework gate: where that gate is configured off, the payload reaches the answer path instead. |
| S-3 output gate | `OutputValidateNode._run_s3_domain_gate` | Non-empty output, disclaimer present, no secret-shaped value. On violation it returns an error AND replaces the released text with a constant notice — returning an error alone leaves the rejected report in the field the framework's output fallback reads. |
| S-4 audit | `emit_trace_event` free function | Every node emits a domain event on a reachable path. Outcome signals only: a reason code, a count, an exception class name — never message content. |
| S-5 state safety | `src/schemas/state.py` | No credential-shaped field names; JSON-string storage for structured values. |

**The output invariant, stated exactly.** This template renders unit counts, percentages and
inert identifiers. It renders no monetary aggregate anywhere, so the nearest-1,000 precision
grid used by the financial templates does not apply here and is deliberately absent. The
invariant the gate enforces is the one this template states: the disclaimer block is present
in every released response, and no secret-shaped string is released.

**Detector composition.** The secret screen is the UNION of the framework's own credential
detector and a local pattern set. The framework's patterns describe credential *formats* and
match no assignment form, no forge token and no card number; the local set covers those.
Taking only one side would make the screen narrower than either.

## Import isolation

- [x] The template does not import the platform SDK (Level 0).
- [x] Import targets are `framework.*`, `shared.*`, `langgraph` and `src.*` only.
- [x] Verified by `tests/proof_of_boundary/test_import_isolation.py`.
