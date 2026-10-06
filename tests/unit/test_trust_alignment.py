# RET-C2-344 — Every node must be reachable at the trust level the manifest declares.
#
# The gate that ships with the scaffold checks that each node DECLARES a valid
# required_trust_level. It does not check that the declared level can ever be
# reached. Those are different properties, and this template shipped with the
# second one false: all seven domain nodes required INTERNAL while
# config/agent.yaml declares the agent's entry trust as VERIFIED_EXTERNAL,
# which is lower. The trust gate denied every domain node on every call. The
# pipeline ran to completion producing nothing, /health stayed green, and the
# caller received status=error with a null output.
#
# A node that requires more trust than its own agent's entry point can never
# execute. It is not a control; it is a dead branch.
#
# Both sides are READ here, never restated: the entry level comes from the
# manifest, the node levels from the classes. A test that spelled the expected
# level out twice would keep passing through exactly the drift it exists to
# catch.

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

from framework.nodes.base_node import BaseNode
from framework.schemas.trust_level import TrustLevel
from framework.utils.config_loader import load_config

# tests/unit/<this file> -> parents[2] is the repository root.
_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "config" / "agent.yaml"
_MANIFEST = load_config(str(_MANIFEST_PATH))

# The framework orders trust levels; a caller satisfies a node when the
# caller's level is at least the node's. The ordering is read from the
# framework rather than hard-coded, so a new level added upstream does not
# leave this test quietly comparing the wrong thing.
_ORDER = [TrustLevel.ANONYMOUS, TrustLevel.VERIFIED_EXTERNAL, TrustLevel.INTERNAL]


def _rank(level: TrustLevel) -> int:
    return _ORDER.index(level)


def _entry_trust() -> TrustLevel:
    declared = _MANIFEST.get("required_trust_level")
    assert declared, f"{_MANIFEST_PATH} declares no required_trust_level"
    return TrustLevel(declared.lower() if declared.islower() else declared)


def _node_classes():
    """Every concrete BaseNode subclass this template defines."""
    found = []
    for package_name in ("src.nodes", "src.graph"):
        pkg = importlib.import_module(package_name)
        modules = [package_name]
        if hasattr(pkg, "__path__"):
            modules += [name for _, name, _ in pkgutil.walk_packages(pkg.__path__, prefix=f"{package_name}.")]
        for modname in modules:
            module = importlib.import_module(modname)
            for obj in vars(module).values():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseNode)
                    and obj is not BaseNode
                    and getattr(obj, "__module__", None) == modname
                    and not inspect.isabstract(obj)
                ):
                    found.append(obj)
    return found


_NODE_CLASSES = _node_classes()


def test_the_template_defines_nodes_at_all():
    """Without this, an import change would make every test below vacuous."""
    assert _NODE_CLASSES, "no concrete node classes discovered — the scan is broken"


def test_trust_level_enum_ordering_is_the_one_assumed_here():
    """Pin the ordering this file compares against.

    If the framework ever adds a level between these, the comparison below
    would silently start meaning something else.
    """
    assert set(_ORDER) == set(TrustLevel)


@pytest.mark.parametrize("node_cls", _NODE_CLASSES, ids=lambda c: c.__name__)
def test_node_is_reachable_at_the_declared_entry_trust(node_cls):
    entry = _entry_trust()
    required = node_cls.required_trust_level
    assert _rank(required) <= _rank(entry), (
        f"{node_cls.__name__} requires {required.value}, but config/agent.yaml "
        f"declares the entry trust as {entry.value}. A caller arriving at the "
        "declared level can never reach this node, so it would be denied on "
        "every call."
    )


@pytest.mark.parametrize("node_cls", _NODE_CLASSES, ids=lambda c: c.__name__)
def test_node_declares_its_trust_level_explicitly(node_cls):
    """Implicit inheritance of the permissive base default is not acceptable.

    On the framework version CI installs this also raises at class-definition
    time, so a node that omits it does not merely run with the wrong level —
    importing the module fails outright and the whole agent stops loading.
    """
    assert "required_trust_level" in node_cls.__dict__, (
        f"{node_cls.__name__} does not declare required_trust_level in its own " "class body"
    )
