"""The repository map, file ranking and context compression."""

from coding_agent.layer4_explore.step1_map import build_outlines, outline_python, render_map
from coding_agent.layer4_explore.step2_rank import (
    choose_files,
    looks_like_a_test_file,
    module_names_from_tests,
    rank_files,
)
from coding_agent.layer4_explore.step3_context import build_context, truncate_middle


def contents_of(workspace):
    found = {}
    for name in workspace.source_files():
        found[name] = workspace.read(name)
    return found


def test_the_map_is_a_fraction_of_the_repository(workspace):
    outlines = build_outlines(workspace)
    repo_map = render_map(outlines)

    total = 0
    for source in contents_of(workspace).values():
        total = total + len(source)

    assert len(repo_map) < total * 0.25
    assert "shopkit/pricing.py" in repo_map
    assert "def tiered_discount" in repo_map


def test_definitions_are_parsed_not_pattern_matched():
    source = (
        'def real_one():\n    pass\n\n\n'
        'class Thing:\n    def method(self):\n        pass\n\n\n'
        'TEXT = """\ndef not_really_a_function():\n    pass\n"""\n'
    )
    outline = outline_python("x.py", source)
    assert outline.functions == ["real_one"]
    assert outline.classes == ["Thing(method)"]
    # The def inside the string is not a definition and a regex would find it.
    assert "not_really_a_function" not in str(outline.names())


def test_a_file_that_does_not_parse_is_reported_as_such():
    outline = outline_python("x.py", "def broken(:\n")
    assert outline.parse_error != ""


def test_the_failing_test_name_finds_the_module():
    assert module_names_from_tests(
        ["shopkit/tests/test_pricing.py::test_x"]) == ["pricing"]
    assert module_names_from_tests(["tests/money_test.py"]) == ["money"]


def test_test_files_are_recognised():
    for path in ("shopkit/tests/test_a.py", "tests/b.py", "a_test.py", "conftest.py"):
        assert looks_like_a_test_file(path), path
    assert not looks_like_a_test_file("shopkit/pricing.py")


def test_the_right_file_is_ranked_first(workspace):
    outlines = build_outlines(workspace)
    contents = contents_of(workspace)

    cases = [
        ("shopkit/tests/test_pricing.py::test_x", "the discount tiers are wrong",
         "shopkit/pricing.py"),
        ("shopkit/tests/test_money.py::test_x", "rounding is truncating",
         "shopkit/money.py"),
        ("shopkit/tests/test_cart.py::test_x", "carts share items",
         "shopkit/cart.py"),
        ("shopkit/tests/test_inventory.py::test_x", "stock boundary is wrong",
         "shopkit/inventory.py"),
    ]
    for test_id, issue, expected in cases:
        ranked = rank_files(outlines, issue, [test_id], [], contents)
        assert ranked[0].path == expected, issue


def test_a_test_file_is_never_offered_for_editing(workspace):
    outlines = build_outlines(workspace)
    ranked = rank_files(outlines, "tests are failing", ["shopkit/tests/test_cart.py::t"],
                        [], contents_of(workspace))
    chosen = choose_files(ranked, 6)
    for entry in chosen:
        assert not looks_like_a_test_file(entry.path)


def test_weakly_matching_files_are_left_out(workspace):
    # Taking everything with a positive score put four files in the prompt when
    # one mattered, purely for sharing an ordinary English word.
    outlines = build_outlines(workspace)
    ranked = rank_files(outlines, "tiered_discount skips the top band",
                        ["shopkit/tests/test_pricing.py::test_x"], [],
                        contents_of(workspace))
    chosen = choose_files(ranked, 6)
    assert len(chosen) <= 2
    assert chosen[0].path == "shopkit/pricing.py"


def test_context_is_much_smaller_than_the_repository(workspace):
    outlines = build_outlines(workspace)
    contents = contents_of(workspace)
    total = 0
    for source in contents.values():
        total = total + len(source)

    ranked = rank_files(outlines, "the discount tiers are wrong",
                        ["shopkit/tests/test_pricing.py::test_x"], [], contents)
    chosen = choose_files(ranked, 6)
    paths = []
    for entry in chosen:
        paths.append(entry.path)

    bundle = build_context(workspace, render_map(outlines), paths, 8000,
                           workspace.source_files())
    assert bundle.characters < total * 0.6
    assert "shopkit/pricing.py" in bundle.files_included
    assert len(bundle.files_omitted) > 0


def test_omitted_files_are_announced(workspace):
    outlines = build_outlines(workspace)
    bundle = build_context(workspace, render_map(outlines), ["shopkit/money.py"],
                           8000, workspace.source_files())
    assert "NOT SHOWN" in bundle.text


def test_truncation_keeps_both_ends():
    source = "HEAD\n" + ("filler\n" * 5000) + "TAIL"
    shortened, was_truncated = truncate_middle(source, 400)
    assert was_truncated
    assert shortened.startswith("HEAD")
    assert shortened.endswith("TAIL")
    assert "omitted from the middle" in shortened


def test_short_files_are_not_truncated():
    shortened, was_truncated = truncate_middle("small", 400)
    assert not was_truncated
    assert shortened == "small"
