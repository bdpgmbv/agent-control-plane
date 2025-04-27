"""
Shared test setup.

The environment is forced offline at the top of this file, before any app module
is imported, because settings.py reads the environment at import time and
python-dotenv does not overwrite variables that are already set. The whole suite
therefore runs with no API key, no network and no cost.

Note what that means HERE specifically, because it is different from the other
projects in this series: these tests exercise the harness - the sandbox, the edit
applier, the test runner, the verifier, the loop - not the model's ability to fix
bugs. That is the correct split. The harness is the part that has to behave
identically every time, and it is the part somebody cloning this repository with
no key should still be able to check.
"""

import os

os.environ["LLM_PROVIDER"] = "offline"
os.environ["OPENAI_API_KEY"] = ""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from coding_agent.layer3_workspace.step1_sandbox import Workspace

BENCHMARK_REPO = PROJECT_ROOT / "benchmark"

PROTECTED = ["tests/*", "test_*.py", "*_test.py", "conftest.py", "pytest.ini",
             "setup.cfg", "tox.ini", "pyproject.toml"]


@pytest.fixture
def workspace(tmp_path):
    """A fresh copy of the benchmark repository, thrown away afterwards."""
    made = Workspace.create_from(BENCHMARK_REPO, tmp_path, "test")
    yield made
    made.remove()


@pytest.fixture
def broken_workspace(tmp_path):
    """A copy with the tax_sign bug in it."""
    from coding_agent.layer10_evaluation.tasks import apply_break, find

    made = Workspace.create_from(BENCHMARK_REPO, tmp_path, "broken")
    apply_break(made, find("tax_sign"))
    yield made
    made.remove()
