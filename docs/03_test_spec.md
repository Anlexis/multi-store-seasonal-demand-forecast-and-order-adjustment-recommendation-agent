# Test Specification — RET-C2-344

## Test strategy

Three layers, and the boundary between them matters more than the count.

- **Unit** — one node or one service at a time, calling `execute()` directly. Direct calls
  are deliberate: a check proven only through the framework wrapper is proven only where
  that wrapper's gates are active.
- **Integration** — the real ASGI application with a real compiled graph. Nothing is stubbed
  except, in two tests, the forecast client, which is the data path rather than a gate.
- **Proof of boundary** — the structural invariants: import isolation, state safety, invoke
  order and trust denial, the output gate, and the domain's own out-of-scope and disclaimer
  rules.

Every screen is probed in **both** directions. A screen has two ways to be wrong and only
one of them looks like a failure: too narrow lets the attack through, too wide refuses real
work. Assertions are behavioural — refused, nothing published, nothing echoed — never on a
gate's wording, which differs between framework releases.

Run: `python -m pytest tests/ -v`

## Shipped test files

| Path | Covers |
|---|---|
| `tests/unit/test_state_schema.py` | state is a flat TypedDict extending the framework base |
| `tests/unit/test_graph_composition.py` | node registration, backbone slots, the inner/outer output contract |
| `tests/unit/test_input_parse_node.py` | request resolution from the sentence and from the request context |
| `tests/unit/test_data_validate_node.py` | allowlists, horizon bound, out-of-scope handling |
| `tests/unit/test_multi_store_forecast_node.py` | per-store fan-out and its guards |
| `tests/unit/test_forecast_aggregation_node.py` | regional aggregation, JSON-safe output |
| `tests/unit/test_food_waste_risk_node.py` | risk classification against the threshold table |
| `tests/unit/test_order_adjust_node.py` | adjustment rules and the guard bands |
| `tests/unit/test_output_format_node.py` | rendering, and that a rejected value is never echoed into the report |
| `tests/unit/test_output_validate_node.py` | the output gate's own decisions |
| `tests/unit/test_input_guard.py` | the caller contract: control markers, directives, secrets, numbers, identifiers, request context |
| `tests/unit/test_output_boundary.py` | containment and the closed-set error contract, kept apart |
| `tests/unit/test_trust_alignment.py` | every node is reachable at the declared entry trust level |
| `tests/unit/test_runtime_config.py` | declared configuration is finite, bounded, and actually read |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | the framework's security gates cannot be overridden |
| `tests/integration/test_invoke_e2e.py` | the real HTTP entry point end to end |
| `tests/integration/test_manifest_identity_alignment.py` | the provisioned secret identity matches the manifest |
| `tests/proof_of_boundary/test_import_isolation.py` | no platform SDK imports |
| `tests/proof_of_boundary/test_state_safety.py` | no credential-shaped fields, no unsafe types |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | gate order per node, and denial below the required trust level |
| `tests/proof_of_boundary/test_pb_s3_gate.py` | the output gate cannot be bypassed |
| `tests/proof_of_boundary/test_pb_disclaimer.py` | the disclaimer block is present on every path |
| `tests/proof_of_boundary/test_pb_out_of_scope.py` | out of scope is answered, not errored |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | skipped: this template does not opt into cross-boundary interrupt propagation, and the file detects that from the graph rather than restating it |

## Framework compliance

| TC-ID | Test | Expected result |
|---|---|---|
| TC-01 | State contract: flat TypedDict, no Pydantic or dataclass | `tests/unit/test_state_schema.py`, `tests/proof_of_boundary/test_state_safety.py` pass |
| TC-03 | No credential-shaped field in state | `tests/proof_of_boundary/test_state_safety.py` passes; the repository-wide credential scan reports no finding |
| TC-06 | The framework input gate cannot be overridden | class definition raises `TypeError` |
| TC-07 | The framework output gate cannot be overridden | class definition raises `TypeError` |
| PB-4 | Import isolation | no Level 0 import anywhere under `src/` |
| PB-6 | Invoke order per node, and denial below the required trust level | trust gate → node start → input gate → `execute()` → output gate → node complete; an under-privileged caller is denied before `execute()` |

## Behavioural coverage

| ID | Test | Input | Expected result |
|---|---|---|---|
| BL-01 | The deployed agent produces a real report | `"Forecast yogurt demand for kanto_12 over 14 days"` | `status: success`, a report naming the group, the SKU and the horizon, with the disclaimer block |
| BL-02 | The reported numbers move with the request | the same group and SKU at 1 day and at 90 days | two different reports, each naming its own horizon |
| BL-03 | A different store group changes the store count | `kanto_12` vs `kyushu_5` | 12 stores vs 5 stores |
| BL-04 | The request context reaches the inner pipeline | `"forecast"` plus a context naming group, SKU and horizon | answered from the context; the same sentence without the context is out of scope |
| BL-05 | Out of scope is answered, not errored | an unsupported group or SKU | `status: success`, the supported sets listed, the rejected value not echoed, disclaimer present |
| BL-06 | The committed deploy payload satisfies the entry contract | `deploy/invoke_payload.json`, read from the file | `status: success`, a real report |
| SEC-01 | Bearer auth is required and its refusal is uninformative | absent, wrong, and non-ASCII credentials | `401` in all three cases with an identical body |
| SEC-02 | An injection the framework does not score is refused | `<<SYS>>` wrapper; a directive spliced apart by inline markup | `status: error`, a constant refusal notice, nothing echoed |
| SEC-03 | A credential in the request string is refused | a request string carrying an access-key shape | refused, value not echoed |
| SEC-04 | A credential on a supported context field is refused | a key shape as `store_group` | `400` naming the field, value not echoed |
| SEC-05 | An unsupported context key is dropped, not fatal | a context carrying an undeclared field | `status: success` — the key never reaches the graph |
| SEC-06 | Non-finite and out-of-range numbers are refused | `NaN`, `Infinity`, `-1`, `0`, `91`, `true`, `"abc"` | `400` naming the field, per value |
| SEC-07 | The output gate withholds a secret-shaped report | a rendered value shaped like a card number or an assignment form | `status: error`, the report replaced by a constant notice, the value absent from the response |
| SEC-08 | The error envelope is closed-set | a sentinel seeded into every internal error channel, driven through every error path | the sentinel appears nowhere in the returned mapping |
| SEC-09 | A failure upstream is not erased | an already-failed state through each downstream node | the node returns no change |

## Verification environment

The suite runs against the released framework wheel, which is what CI installs; a run
against an import shim is not evidence about the shipped agent.
