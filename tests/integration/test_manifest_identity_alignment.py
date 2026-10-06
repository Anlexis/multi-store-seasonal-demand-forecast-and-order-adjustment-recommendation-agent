# The identity the entrypoint provisions secrets under, against the identity the
# manifest declares.
#
# The manifest is the source of truth for who this agent is. The secret provider
# is scoped by that identity: it reads `env/namespaces/{namespace}/.env.{env}`
# and `env/agents/{namespace}/{agent_name}/.env.{env}`. Under the registry the
# scope comes from `config/agent.yaml`; standalone it comes from whatever
# `src/api/server.py` passes to the factory. When those two disagree, one
# agent's secrets live in two stores and a key provisioned for the registry
# deployment is simply absent in the standalone one — with no error at boot,
# because a missing tier file is ignored by design. Nothing fails until a secret
# is declared, and then it fails in only one of the two deployments.
#
# This template shipped with exactly that drift: the entrypoint provisioned
# under the lowercased Template ID while the manifest declares lower(industry).
#
# Both sides are READ here, never restated: the manifest is parsed from
# config/agent.yaml, and the entrypoint's identity is taken from the provider
# the module actually provisioned on the agent. A test that spelled the expected
# namespace out twice would keep passing through exactly the drift it exists to
# catch.
#
# The direction of the alignment is pinned too. `namespace:` is
# `lower(industry_code)` — the manifest's own `industry` field, lowercased. Both
# facts are checked, so re-aligning the wrong way (moving the manifest onto the
# entrypoint's value instead of the reverse) fails here rather than passing as a
# fix.
#
# Deterministic — no model, no network, no filesystem beyond the manifest.

from pathlib import Path

from framework.utils.config_loader import load_config

from src.api.server import agent

# tests/integration/<this file> -> parents[2] is the repository root.
_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "config" / "agent.yaml"
_MANIFEST = load_config(str(_MANIFEST_PATH))

# The provider the entrypoint bound at import time — the identity that is live
# in a standalone deployment, not a re-derivation of it.
_PROVISIONED = agent._secrets_provider


def test_manifest_declares_the_identity_fields() -> None:
    """The fields the rest of this module compares against must be present.

    Without this, a manifest that lost `namespace:` would make every
    comparison below `None == None` and the file would pass while asserting
    nothing.
    """
    assert _MANIFEST.get("namespace"), f"{_MANIFEST_PATH} declares no namespace"
    assert _MANIFEST.get("name"), f"{_MANIFEST_PATH} declares no name"
    assert _MANIFEST.get("industry"), f"{_MANIFEST_PATH} declares no industry"


def test_provisioned_namespace_matches_the_manifest() -> None:
    assert _PROVISIONED._namespace == _MANIFEST["namespace"], (
        "src/api/server.py provisions secrets under namespace "
        f"{_PROVISIONED._namespace!r}, but config/agent.yaml declares "
        f"{_MANIFEST['namespace']!r}. A secret would resolve from a different "
        "store standalone than under the registry."
    )


def test_provisioned_agent_name_matches_the_manifest() -> None:
    assert _PROVISIONED._agent_name == _MANIFEST["name"], (
        "src/api/server.py provisions secrets for agent name "
        f"{_PROVISIONED._agent_name!r}, but config/agent.yaml declares "
        f"{_MANIFEST['name']!r}."
    )


def test_manifest_namespace_is_lower_industry() -> None:
    """`namespace:` is `lower(industry_code)` — the fleet-wide convention.

    Checked against the manifest's own `industry` field, so this pins which of
    the two values is the correct one to align on without hard-coding either.
    """
    assert _MANIFEST["namespace"] == _MANIFEST["industry"].lower(), (
        f"config/agent.yaml declares namespace {_MANIFEST['namespace']!r}; the "
        f"convention is lower(industry) = {_MANIFEST['industry'].lower()!r}."
    )


def test_manifest_entry_point_resolves_to_the_agent_the_entrypoint_builds() -> None:
    """The class the registry imports is the class the adapter serves.

    A manifest naming a module or class that does not resolve fails at
    registry load, not here, and the failure names neither side.
    """
    import importlib

    dotted = _MANIFEST["class"]
    module_path, _, class_name = dotted.rpartition(".")
    resolved = getattr(importlib.import_module(module_path), class_name)
    assert isinstance(agent, resolved)


def test_the_entry_point_built_the_agent_with_the_declared_configuration() -> None:
    """The runtime values reach the agent the entry point actually serves.

    Constructing the graph bare leaves every value in config/config.yaml unread
    and the framework's own defaults in force — max_retry among them, which
    bounds how often a failing request is replayed. Nothing raises when that
    happens: the configuration is simply not there.

    Read off the agent the module bound at import time, and compared against
    the loader's own result rather than against numbers restated here, so the
    assertion follows a change to config/config.yaml instead of blocking it.
    """
    from src.services.runtime_config import agent_config

    declared = agent_config()
    for key, value in declared.items():
        assert agent.config.get(key) == value, (
            f"src/api/server.py built the agent without {key}={value!r} from "
            "config/config.yaml; the framework default applies instead."
        )
    assert declared, "config/config.yaml declared nothing — the check above is vacuous"


def test_the_inner_graph_receives_the_same_configuration() -> None:
    """And it reaches the domain pipeline, not only the outer backbone.

    The inner graph is built by the main slot; a value that stops at the outer
    graph never reaches the node that spends it.
    """
    from src.services.runtime_config import agent_config

    inner = agent._nodes["main"].get_subgraph()
    assert inner.config.get("timeout_s") == agent_config()["timeout_s"]


def test_manifest_declares_no_unprovisioned_requirements() -> None:
    """Declaring a secret that is not provisioned fails the agent at compile time.

    Both lists are derived from the code — nothing calls ctx.secrets.require()
    and no model client is constructed — so an entry appearing here is a
    change to the published contract, not a detail.
    """
    requires = _MANIFEST.get("requires") or {}
    assert requires.get("secrets") == []
    assert requires.get("extras") == []
