"""Every module must at least import: catches syntax errors in code the other tests don't reach."""

import importlib

import pytest

MODULES = ["engine", "engine.MobileUILibrary", "agents.recorder.gestures", "agents.recorder.session",
           "agents.recorder.generator", "agents.recorder.resolve", "agents.recorder.repair",
           "agents.recorder_agent"]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
