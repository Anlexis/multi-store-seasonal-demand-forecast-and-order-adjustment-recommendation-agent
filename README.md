# Multi-Store Seasonal Demand Forecast Agent

AI agent for forecasting multi-store seasonal demand and recommending order adjustments, built with Agentic Star.

> **Category**: Cat 2 (domain-specific pipeline)
> **Industry**: Retail
> **Template ID**: RET-C2-344

## Overview

Forecasts short-horizon demand for a perishable SKU category across every store in a
regional group, then turns that forecast into two things a buyer can act on: a food-waste
risk level for the category, and a per-store order adjustment expressed as a percentage of
the current order.

A request names a store group, an SKU category and a horizon of 1–90 days — either in a
sentence ("Forecast yogurt demand for kanto_12 over 14 days") or as structured request
context. The pipeline fans out one forecast call per store, aggregates the per-store series
into a regional summary, classifies waste risk against per-SKU shelf-life thresholds, and
derives an adjustment per store from the risk level plus an over/understock guard band
against the regional average. Every response carries a disclaimer block stating that the
figures are model output for planning reference, not a guarantee.

The forecast client is injected, and the version here ships a deterministic offline stub so
the pipeline is runnable and testable without a data warehouse. Replace it with your own
forecasting backend — the client contract is one function returning a per-store series, and
nothing else in the pipeline changes.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode: if the platform is unreachable or the SDK version does not match, graph compilation
raises rather than starting in a partially working state. This is intentional — a
half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling it

The agent is invoked with a request string and, optionally, a structured request context.
Both paths are validated the same way.

```jsonc
{
  "input": "Forecast yogurt demand for kanto_12 over 14 days",
  "session_id": "any-correlation-id",

  // Optional. Overrides whatever the sentence says, because these values went
  // through the contract and the sentence did not. Unsupported keys are dropped.
  "input_context": {
    "store_group": "kanto_12",       // inert identifier, [a-z0-9_]{1,32}
    "sku_category": "yogurt",        // inert identifier, [a-z0-9_]{1,32}
    "horizon_days": 14               // finite, 1–90
  }
}
```

The standalone HTTP entry point requires a bearer credential when `INVOKE_AUTH_TOKEN` is set
on the server environment; an authenticated caller runs at the trust level the manifest
declares. A request context field that is malformed, out of range, or credential-shaped is
refused with `400` naming the field — never echoing its value.

A request naming a store group or SKU category outside the supported set is answered, not
refused: the response says what is supported and carries the same disclaimer block.

## Project Structure

```
src/graph/     outer graph, inner domain workflow, and the bridge between them
src/nodes/     the eight domain steps
src/services/  caller contract and runtime configuration
tests/         unit, integration and boundary tests
config/        agent manifest and runtime configuration
docs/          design specification and test specification
```

See `docs/` for the design and test specification.

## Customising

1. Replace the forecast client with your own backend (`src/nodes/multi_store_forecast_node.py`).
2. Adjust the store groups, SKU categories and shelf-life thresholds for your estate
   (`src/nodes/input_parse_node.py`, `src/nodes/data_validate_node.py`,
   `src/nodes/food_waste_risk_node.py`).
3. Adjust `config/config.yaml` for your own retry and timeout budgets.
4. Review the adjustment rules in `src/nodes/order_adjust_node.py` against your own
   replenishment policy.
5. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
