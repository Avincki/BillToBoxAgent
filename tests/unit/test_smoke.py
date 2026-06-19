"""Scaffold smoke test: the package and every sub-package import cleanly.

Keeps ``pytest`` green on the empty skeleton (task 3) and guards the package
layout as modules are added.
"""

from __future__ import annotations

import importlib

import billtobox_agent

_SUBPACKAGES = [
    "billtobox_agent",
    "billtobox_agent.config",
    "billtobox_agent.data",
    "billtobox_agent.monitoring",
    "billtobox_agent.mail",
    "billtobox_agent.extraction",
    "billtobox_agent.drive",
    "billtobox_agent.billtobox",
    "billtobox_agent.agent",
    "billtobox_agent.web",
    "billtobox_agent.utils",
]


def test_version_is_exposed() -> None:
    assert billtobox_agent.__version__ == "0.1.0"


def test_all_subpackages_import() -> None:
    for name in _SUBPACKAGES:
        assert importlib.import_module(name) is not None
