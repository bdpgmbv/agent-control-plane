"""IBAN, VAT and card checksums."""

from doc_intelligence.layer0_shared.checksums import check_iban, check_luhn, check_vat_number


def test_real_ibans_pass():
    for iban in ("GB82 WEST 1234 5698 7654 32",
                 "DE89 3704 0044 0532 0130 00",
                 "NL91ABNA0417164300"):
        assert check_iban(iban).valid, iban


def test_one_wrong_digit_fails():
    assert not check_iban("GB82 WEST 1234 5698 7654 33").valid
    assert not check_iban("GB82 WEST 1234 5698 7654 99").valid


def test_wrong_length_is_caught_before_the_maths():
    result = check_iban("GB82 WEST 1234 5698 7654")
    assert not result.valid
    assert "22 characters" in result.reason


def test_the_failure_reason_states_the_fact_and_nothing_else():
    # Layer 6 adds the advice about scans. If layer 0 added it too, the message
    # in the review queue said the same thing twice.
    reason = check_iban("GB82 WEST 1234 5698 7654 33").reason
    assert "remainder" in reason
    assert "sinister" not in reason


def test_vat_formats():
    assert check_vat_number("GB123456789").valid
    assert not check_vat_number("DE12345").valid
    # A country we have no format for is not claimed to be wrong.
    assert check_vat_number("XX123456").valid


def test_ocr_damaged_vat_number_is_rejected():
    # "GB55l234567" - lowercase L for 1, which the scanner repair deliberately
    # leaves alone because the run mixes real letters with digits.
    assert not check_vat_number("GB55l234567").valid


def test_luhn():
    assert check_luhn("4242424242424242").valid
    assert not check_luhn("4242424242424243").valid
