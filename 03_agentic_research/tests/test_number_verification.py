"""
A REGRESSION TEST FOR A VERIFIER FALSE POSITIVE.

The figure check flagged a perfectly sourced report as containing two invented
numbers. They were the citation markers [10] and [11]. Markers of 10 or less were
hidden by the small-integer rule, so the bug only showed up once a report had
more than ten sources - which is exactly when a reader is least able to check by
hand.
"""

from research_agent.layer7_synthesis.step2_verify_report import numbers_are_supported


def test_citation_markers_are_not_treated_as_figures():
    text = "Cost parity is not expected until at least 2033 [10][11]."
    allowed = [2033.0]
    assert numbers_are_supported(text, allowed) == []


def test_a_genuinely_invented_figure_is_still_caught():
    """The fix must not blind the check."""
    text = "Productivity rose by 47 percent [1]."
    allowed = [12.0, 3.0]
    assert "47.0" in numbers_are_supported(text, allowed)


def test_small_counting_numbers_are_allowed():
    text = "Three studies were reviewed [1][2][3]."
    assert numbers_are_supported(text, []) == []


def test_a_figure_close_to_an_allowed_one_passes():
    """'about 12 percent' should not contradict a source saying 12.4."""
    assert numbers_are_supported("Productivity rose 12 percent [1].", [12.4]) == []
