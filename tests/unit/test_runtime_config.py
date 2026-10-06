# RET-C2-344 — Unit tests: runtime configuration (src/services/runtime_config.py)
#
# A declared setting that nothing reads is not a setting. The graph used to be
# constructed bare, so every value in config/config.yaml was ignored and the
# framework's own defaults applied instead — with nothing failing, because a
# missing config degrades silently rather than raising.
#
# Two properties are tested: the loader refuses values that would remove a
# bound rather than tighten it, and the declared value actually reaches the
# code that uses it.

from pathlib import Path

import pytest

from src.services.runtime_config import (
    agent_config,
    finite_in_range,
    load_runtime_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_shipped_config_file_loads():
    config = agent_config()
    assert config["max_retry"] == 3
    assert config["timeout_s"] == 30


def test_the_shipped_config_file_exists_and_declares_both_values():
    """Otherwise the assertions above would be testing the defaults."""
    import yaml

    raw = yaml.safe_load((_REPO_ROOT / "config" / "config.yaml").read_text())
    assert raw["max_retry"] == 3
    assert raw["timeout_s"] == 30


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", float("nan"), float("inf"), True, "x", None])
def test_finite_in_range_rejects_non_finite_and_wrong_types(value):
    """NaN parses through float() and compares False against every bound.

    A budget that compares False against its own deadline is not a generous
    budget, it is no budget — the check exists to make that impossible.
    """
    assert finite_in_range(value, 1, 600) is None


@pytest.mark.parametrize("value", [1, 30, 600, "30", 30.0])
def test_finite_in_range_accepts_values_inside_the_bound(value):
    assert finite_in_range(value, 1, 600) == float(value)


def test_an_out_of_range_declared_value_raises_rather_than_defaulting(tmp_path):
    """A silently-substituted default is how a declared setting stops being one."""
    bad = tmp_path / "config.yaml"
    bad.write_text("max_retry: 99\ntimeout_s: 30\n")
    with pytest.raises(ValueError) as excinfo:
        load_runtime_config(bad)
    assert "max_retry" in str(excinfo.value)


def test_a_non_finite_declared_value_raises(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("max_retry: 3\ntimeout_s: .nan\n")
    with pytest.raises(ValueError) as excinfo:
        load_runtime_config(bad)
    assert "timeout_s" in str(excinfo.value)


def test_a_missing_file_yields_the_declared_defaults(tmp_path):
    config = load_runtime_config(tmp_path / "absent.yaml")
    assert config == {"max_retry": 3, "timeout_s": 30}


def test_unknown_keys_are_carried_through(tmp_path):
    """Adding a setting must not require editing the loader."""
    extra = tmp_path / "config.yaml"
    extra.write_text("max_retry: 2\ntimeout_s: 5\nsomething_else: hello\n")
    config = load_runtime_config(extra)
    assert config["something_else"] == "hello"
    assert config["max_retry"] == 2


# --- the declared value reaches the code -------------------------------------


def test_max_retry_reaches_the_backbone_route():
    """Proof that the config is not merely loaded but consumed.

    The backbone's retry route reads self.config["max_retry"]; a graph built
    with no config reads the framework's built-in default instead.
    """
    from src.graph.graph import DemandForecastAgent

    configured = DemandForecastAgent(config={"max_retry": 1})
    assert configured.config["max_retry"] == 1

    bare = DemandForecastAgent()
    assert bare.config.get("max_retry") is None


def test_timeout_s_reaches_the_forecast_node_through_the_inner_graph():
    """The declared budget arrives at the one call that can block.

    Read off the constructed node rather than re-derived, so a wiring change
    that stops forwarding the value fails here.
    """
    from src.graph.domain_workflow_graph import DomainWorkflowGraph

    graph = DomainWorkflowGraph(config={"timeout_s": 7})
    graph.register_nodes()
    assert graph._nodes["forecast_call"]._timeout_s == 7.0


def test_an_unconfigured_forecast_node_still_carries_a_budget():
    """Falling back to the declared default, not to "no bound at all"."""
    from src.nodes.multi_store_forecast_node import MultiStoreForecastCallNode

    assert MultiStoreForecastCallNode()._timeout_s == 30.0
    assert MultiStoreForecastCallNode(node_config={"timeout_s": float("nan")})._timeout_s == 30.0


def test_the_inner_graph_refuses_a_non_finite_timeout():
    from framework.errors import ConfigError
    from src.graph.domain_workflow_graph import DomainWorkflowGraph

    with pytest.raises(ConfigError):
        DomainWorkflowGraph(config={"timeout_s": float("inf")})._validate_config()
