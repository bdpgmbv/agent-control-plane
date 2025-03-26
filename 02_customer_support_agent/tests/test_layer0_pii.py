"""TESTS FOR LAYER 0 - removing personal data."""

from support_agent.layer0_shared.pii import contains_pii, find_pii, passes_luhn_check, redact


def test_an_email_is_removed():
    cleaned, kinds = redact("write to alice.nwosu@example.com about it")
    assert "alice.nwosu@example.com" not in cleaned
    assert "[EMAIL_REDACTED]" in cleaned
    assert kinds == ["email"]


def test_a_card_number_keeps_only_its_last_four():
    """
    Safe to store, and still enough for a human to match it to a payment record.
    Replacing it with nothing at all makes the support agent's job impossible.
    """
    cleaned, kinds = redact("my card 4532015112830366 was charged")
    assert "4532015112830366" not in cleaned
    assert "[CARD_ENDING_0366]" in cleaned
    assert kinds == ["card"]


def test_an_order_number_is_not_mistaken_for_a_card():
    """
    THE FALSE POSITIVE THAT BREAKS THE AGENT.

    An order number can be sixteen digits. Redacting every long number would
    leave the agent unable to read the order ids it needs, and it would look
    like the tools were broken rather than the redaction.
    """
    text = "order ORD-10023 and reference 4532015112830367 please"
    cleaned, kinds = redact(text)
    assert "ORD-10023" in cleaned
    assert "card" not in kinds


def test_luhn_separates_a_real_card_from_a_lookalike():
    assert passes_luhn_check("4532015112830366") is True
    assert passes_luhn_check("4532015112830367") is False
    assert passes_luhn_check("1234") is False


def test_our_own_identifiers_always_survive():
    text = "ORD-10023 CUST-1001 REF-5001 TICK-ABC123 tracking 1Z999AA10123456784"
    cleaned, kinds = redact(text)
    for identifier in ["ORD-10023", "CUST-1001", "REF-5001", "TICK-ABC123", "1Z999AA10123456784"]:
        assert identifier in cleaned
    assert kinds == []


def test_phone_numbers_are_removed():
    cleaned, kinds = redact("call me on +44 7700 900123 tomorrow")
    assert "900123" not in cleaned
    assert "phone" in kinds


def test_prices_and_years_are_not_personal_data():
    """Over-redaction is a real failure: it silently removes what the agent needs."""
    text = "I paid 49.99 dollars for 3 items back in 2024"
    cleaned, kinds = redact(text)
    assert cleaned == text
    assert kinds == []


def test_several_kinds_at_once():
    text = "alice@example.com, +44 7700 900123, card 4532015112830366"
    cleaned, kinds = redact(text)
    assert "email" in kinds
    assert "phone" in kinds
    assert "card" in kinds
    assert contains_pii(cleaned) is False


def test_redaction_never_changes_a_clean_message():
    text = "Where is my order ORD-10023?"
    cleaned, kinds = redact(text)
    assert cleaned == text
    assert len(find_pii(text)) == 0
