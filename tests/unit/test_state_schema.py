# RET-C2-344 — Unit Tests: State schema
#
# Inner/outer shared flat TypedDict (src/schemas/state.py). Asserted against the
# MERGED develop implementation:
#   - State extends AgentState (all framework keys inherited).
#   - The full RET-C2-344 domain key contract is declared as annotations.
#   - State is a TypedDict (msgpack-safe), NEVER a Pydantic BaseModel.
#
# NOTE: never issubclass() a TypedDict (raises TypeError). The AgentState
# extension check is done via the annotation-subset rule below.


from src.schemas.state import State
from framework.schemas.agent_state import AgentState


# Every RET-C2-344 domain field declared on State (state.py docstring contract).
EXPECTED_DOMAIN_KEYS = {
    "raw_input",
    "store_group",
    "sku_category",
    "horizon_days",
    "validation_passed",
    "validation_error",
    "out_of_scope",
    "raw_forecast_results",
    "aggregated_forecast",
    "food_waste_risk_flags",
    "order_adjustments",
    "output_text",
    "disclaimer_present",
    "agent_status",
    "status",
    "error_message",
}


class TestStateSchema:
    """State must extend AgentState and declare the domain key contract."""

    def test_state_extends_agent_state_annotations(self):
        """State carries every AgentState field (annotation-subset, not issubclass)."""
        # TypedDicts cannot be passed to issubclass(); compare __annotations__.
        base_keys = set(AgentState.__annotations__)
        state_keys = set(State.__annotations__)
        assert base_keys.issubset(state_keys), (
            "State must inherit all AgentState fields; missing: " f"{base_keys - state_keys}"
        )

    def test_state_declares_all_domain_keys(self):
        """All RET-C2-344 domain fields are present on State."""
        state_keys = set(State.__annotations__)
        missing = EXPECTED_DOMAIN_KEYS - state_keys
        assert not missing, f"State is missing domain keys: {missing}"

    def test_state_is_typed_dict_not_pydantic(self):
        """State is a TypedDict (has __annotations__, no Pydantic model machinery)."""
        assert hasattr(State, "__annotations__")
        # Pydantic BaseModel exposes model_fields / __fields__; a TypedDict must not.
        assert not hasattr(State, "model_fields")
        assert not hasattr(State, "__fields__")

    def test_state_has_no_credential_like_fields(self):
        """S-5: no credential / secret keys are permitted in State."""
        lowered = {k.lower() for k in State.__annotations__}
        for forbidden in ("token", "secret", "password", "api_key", "credential"):
            assert not any(forbidden in k for k in lowered), f"State must not declare a '{forbidden}'-like field"
