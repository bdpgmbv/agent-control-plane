"""Parsing amounts and dates - the layer everything else depends on."""

from doc_intelligence.layer0_shared.money import (
    find_currency,
    interpret_number,
    parse_date,
    parse_money,
)


def test_plain_number_is_not_split():
    # This one is here because it was wrong. The original pattern used an
    # alternation that matched a partial digit run, so "4500" came back as 450.0.
    assert parse_money("4500").amount == 4500.0
    assert parse_money("45000").amount == 45000.0
    assert parse_money("450").amount == 450.0


def test_both_separators_are_unambiguous():
    value, confidence, _ = interpret_number("1.234,56")
    assert value == 1234.56
    assert confidence == 1.0

    value, confidence, _ = interpret_number("1,234.56")
    assert value == 1234.56
    assert confidence == 1.0


def test_two_digit_tail_is_certain_in_either_locale():
    # Digit grouping is always in threes, so a two-digit tail can only be a
    # decimal point. This used to return 0.95 for no stated reason, and that
    # unearned doubt pushed clean documents below the approval gate.
    for text in ("14.10", "14,10", "2323.00", "879,70"):
        value, confidence, _ = interpret_number(text)
        assert confidence == 1.0, text


def test_three_digit_tail_is_flagged_as_ambiguous():
    value, confidence, note = interpret_number("1,234")
    assert value == 1234.0
    assert confidence < 0.7
    assert "AMBIGUOUS" in note


def test_spaced_thousands_and_negatives():
    assert parse_money("1 234 567,89").amount == 1234567.89
    assert parse_money("-250.00").amount == -250.0


def test_unambiguous_date_formats_score_full_confidence():
    parsed = parse_date("14 March 2025")
    assert parsed.iso() == "2025-03-14"
    assert parsed.confidence == 1.0

    parsed = parse_date("2025-03-14")
    assert parsed.iso() == "2025-03-14"
    assert parsed.confidence == 1.0


def test_slash_dates_are_ambiguous_unless_the_day_is_over_twelve():
    unclear = parse_date("03/04/2025")
    assert unclear.confidence < 0.7
    assert "AMBIGUOUS" in unclear.note

    clear = parse_date("14/03/2025")
    assert clear.iso() == "2025-03-14"
    assert clear.confidence > 0.9


def test_nonsense_is_not_a_date():
    assert not parse_date("next Tuesday").found()
    assert not parse_date("").found()


def test_currency_is_only_reported_when_stated():
    assert find_currency("84,000.00 GBP per annum") == "GBP"
    assert find_currency("Total 2,787.60") == ""
