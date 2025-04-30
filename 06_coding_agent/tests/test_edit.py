"""Applying edits, and the five ways an edit is refused."""

from tests.conftest import PROTECTED

from coding_agent.layer2_models.schemas import EditStatus, FileEdit
from coding_agent.layer6_edit.step1_protect import is_protected
from coding_agent.layer6_edit.step2_apply import (
    apply_edits,
    check_syntax,
    smallest_unique_block,
)


def apply_one(workspace, path, old_text, new_text):
    return apply_edits(workspace, [FileEdit(path=path, old_text=old_text,
                                            new_text=new_text)], PROTECTED)


# ---------------------------------------------------------------- protection

def test_test_files_are_protected():
    for path in ("shopkit/tests/test_money.py", "tests/test_x.py", "conftest.py",
                 "pytest.ini", "setup.cfg", "pyproject.toml",
                 "shopkit/tests/__init__.py"):
        blocked, why = is_protected(path, PROTECTED)
        assert blocked, path
        assert why != ""


def test_source_files_are_not_protected():
    for path in ("shopkit/pricing.py", "shopkit/money.py", "README.md"):
        blocked, _ = is_protected(path, PROTECTED)
        assert not blocked, path


# ---------------------------------------------------------------- refusals

def test_a_good_edit_applies(workspace):
    results, ok = apply_one(workspace, "shopkit/pricing.py",
                            "return round_money(subtotal + tax)",
                            "return round_money(subtotal + tax)  # ok")
    assert ok
    assert results[0].status == EditStatus.APPLIED
    assert "# ok" in workspace.read("shopkit/pricing.py")


def test_text_that_is_not_there_is_refused(workspace):
    results, ok = apply_one(workspace, "shopkit/pricing.py",
                            "return subtotal * magic_number", "return 0")
    assert not ok
    assert results[0].status == EditStatus.NOT_FOUND


def test_ambiguous_text_is_refused_rather_than_guessed(workspace):
    # "return 0.0" appears several times. Picking one is a coin flip the agent
    # has no way to know it lost.
    results, ok = apply_one(workspace, "shopkit/pricing.py",
                            "        return 0.0", "        return 1.0")
    assert not ok
    assert results[0].status == EditStatus.AMBIGUOUS
    assert results[0].occurrences > 1


def test_the_ambiguity_message_shows_where_and_offers_a_unique_block(workspace):
    # The message is the agent's only channel for learning how to succeed. A
    # refusal that does not carry enough to act on costs the whole budget.
    results, _ = apply_one(workspace, "shopkit/pricing.py",
                           "        return 0.0", "        return 1.0")
    message = results[0].message
    assert "around line" in message
    assert "a unique old_text for THIS one:" in message
    assert message.count("around line") == results[0].occurrences


def test_editing_a_test_file_is_refused(workspace):
    results, ok = apply_one(workspace, "shopkit/tests/test_pricing.py",
                            "== 120.0", "== 80.0")
    assert not ok
    assert results[0].status == EditStatus.PROTECTED
    assert "specification" in results[0].message


def test_escaping_the_workspace_is_refused(workspace):
    results, ok = apply_one(workspace, "../../../tmp/evil.py", "", "x")
    assert not ok
    assert results[0].status == EditStatus.OUTSIDE_WORKSPACE


def test_an_edit_that_breaks_the_syntax_is_refused(workspace):
    results, ok = apply_one(workspace, "shopkit/pricing.py",
                            "def total_with_tax(subtotal, tax_rate=20.0):",
                            "def total_with_tax(subtotal, tax_rate=20.0:")
    assert not ok
    assert results[0].status == EditStatus.SYNTAX_ERROR
    # And the file on disk is untouched.
    assert "def total_with_tax(subtotal, tax_rate=20.0):" in workspace.read("shopkit/pricing.py")


def test_an_edit_that_changes_nothing_is_refused(workspace):
    results, ok = apply_one(workspace, "shopkit/pricing.py", "tax", "tax")
    assert not ok
    assert results[0].status == EditStatus.INVALID


def test_an_empty_old_text_is_refused(workspace):
    results, ok = apply_one(workspace, "shopkit/pricing.py", "", "anything")
    assert not ok
    assert results[0].status == EditStatus.INVALID


def test_editing_a_file_that_does_not_exist_is_refused(workspace):
    results, ok = apply_one(workspace, "shopkit/nope.py", "a", "b")
    assert not ok
    assert results[0].status == EditStatus.NOT_FOUND


# ---------------------------------------------------------------- batches

def test_a_batch_is_all_or_nothing(workspace):
    before = workspace.read("shopkit/money.py")
    results, ok = apply_edits(workspace, [
        FileEdit(path="shopkit/money.py", old_text="def format_money",
                 new_text="def format_money_renamed"),
        FileEdit(path="shopkit/money.py", old_text="not in this file", new_text="x"),
    ], PROTECTED)

    assert not ok
    assert results[0].ok()          # the first edit was valid
    assert not results[1].ok()
    assert workspace.read("shopkit/money.py") == before   # and nothing was written


def test_several_edits_to_one_file_are_applied_in_order(workspace):
    results, ok = apply_edits(workspace, [
        FileEdit(path="shopkit/inventory.py", old_text="class StockError(Exception):",
                 new_text="class StockError(RuntimeError):"),
        FileEdit(path="shopkit/inventory.py", old_text="def restock(self, sku, quantity):",
                 new_text="def restock(self, sku, quantity=1):"),
    ], PROTECTED)

    assert ok
    source = workspace.read("shopkit/inventory.py")
    assert "class StockError(RuntimeError):" in source
    assert "def restock(self, sku, quantity=1):" in source


def test_a_second_edit_can_depend_on_the_first(workspace):
    # Validated against the evolving in-memory content, not the file on disk.
    results, ok = apply_edits(workspace, [
        FileEdit(path="shopkit/money.py", old_text="def parse_money(text):",
                 new_text="def parse_money_v2(text):"),
        FileEdit(path="shopkit/money.py", old_text="def parse_money_v2(text):",
                 new_text="def parse_money_v3(text):"),
    ], PROTECTED)
    assert ok
    assert "def parse_money_v3(text):" in workspace.read("shopkit/money.py")


# ---------------------------------------------------------------- helpers

def test_syntax_checking_only_applies_to_python():
    assert check_syntax("a.py", "def f(:") != ""
    assert check_syntax("a.py", "def f():\n    pass\n") == ""
    assert check_syntax("notes.md", "def f(:") == ""


def test_the_smallest_unique_block_really_is_unique():
    source = "a\nx = 1\nb\nx = 1\nc\n"
    lines = source.split("\n")
    block = smallest_unique_block(lines, 2, source)
    assert block is not None
    assert source.count(block) == 1
    assert "x = 1" in block


def test_no_unique_block_exists_in_a_perfectly_repetitive_file():
    source = "x = 1\n" * 40
    lines = source.split("\n")
    assert smallest_unique_block(lines, 5, source, maximum_lines=4) is None
