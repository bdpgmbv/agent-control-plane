"""The working copy and its path guard."""

import os
from pathlib import Path

import pytest
from tests.conftest import BENCHMARK_REPO

from coding_agent.layer3_workspace.step1_sandbox import (
    FileTooLarge,
    PathOutsideWorkspace,
    Workspace,
)


def test_the_repository_is_copied_not_touched(workspace):
    original = (BENCHMARK_REPO / "shopkit" / "pricing.py").read_text()
    workspace.write("shopkit/pricing.py", "# emptied\n")
    assert (BENCHMARK_REPO / "shopkit" / "pricing.py").read_text() == original


def test_caches_are_not_copied(workspace):
    for name in workspace.all_files():
        assert "__pycache__" not in name
        assert ".pytest_cache" not in name


def test_two_workspaces_are_separate(tmp_path):
    first = Workspace.create_from(BENCHMARK_REPO, tmp_path, "a")
    second = Workspace.create_from(BENCHMARK_REPO, tmp_path, "b")
    assert first.root != second.root

    first.write("shopkit/money.py", "# changed\n")
    assert "# changed" not in second.read("shopkit/money.py")

    first.remove()
    second.remove()


def test_ordinary_paths_resolve(workspace):
    assert workspace.resolve("shopkit/pricing.py").name == "pricing.py"
    assert workspace.resolve("shopkit/../shopkit/cart.py").name == "cart.py"


def test_traversal_is_blocked(workspace):
    for attempt in ("../../etc/passwd", "shopkit/../../../../tmp/evil.py",
                    "../../../../../../etc/hosts"):
        with pytest.raises(PathOutsideWorkspace):
            workspace.resolve(attempt)


def test_absolute_paths_outside_are_blocked(workspace):
    with pytest.raises(PathOutsideWorkspace):
        workspace.resolve("/etc/passwd")


def test_a_symlink_out_of_the_workspace_is_blocked(workspace):
    # A string check for ".." would let this through. Resolving the full path
    # follows the link and catches it with the same containment test.
    link = Path(workspace.root) / "shopkit" / "escape.py"
    os.symlink("/etc/passwd", link)
    with pytest.raises(PathOutsideWorkspace):
        workspace.resolve("shopkit/escape.py")
    link.unlink()


def test_an_empty_path_is_refused(workspace):
    for attempt in ("", "   ", None):
        with pytest.raises(PathOutsideWorkspace):
            workspace.resolve(attempt)


def test_oversized_reads_and_writes_are_refused(workspace):
    with pytest.raises(FileTooLarge):
        workspace.write("shopkit/big.py", "x" * 100, max_bytes=50)
    with pytest.raises(FileTooLarge):
        workspace.read("shopkit/pricing.py", max_bytes=10)


def test_snapshots_notice_every_kind_of_change(workspace):
    before = workspace.snapshot()

    workspace.write("shopkit/money.py", workspace.read("shopkit/money.py") + "\n# x\n")
    workspace.write("shopkit/brand_new.py", "value = 1\n")
    (Path(workspace.root) / "shopkit" / "inventory.py").unlink()

    after = workspace.snapshot()
    modified, added, removed = after.changed_against(before)

    assert modified == ["shopkit/money.py"]
    assert added == ["shopkit/brand_new.py"]
    assert removed == ["shopkit/inventory.py"]


def test_an_unchanged_workspace_shows_no_changes(workspace):
    before = workspace.snapshot()
    after = workspace.snapshot()
    modified, added, removed = after.changed_against(before)
    assert (modified, added, removed) == ([], [], [])
