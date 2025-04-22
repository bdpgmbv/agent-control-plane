"""
Measure the letterhead rule.

The supplier name is the one field taken from a position on the page rather than
from a label, so it is the one field whose confidence cannot be reasoned about -
it has to be counted. This script is where the 0.85 in
layer5_extract/step2_patterns.py comes from.

Run it after adding documents to samples/ and update EXPECTED below. If the hit
rate drops, the constant should drop with it.

    .venv/bin/python scripts/check_letterhead.py
"""

from pathlib import Path

from doc_intelligence.layer3_ingest.step1_load import load_from_path
from doc_intelligence.layer3_ingest.step2_clean import clean_document_text
from doc_intelligence.layer5_extract.step2_patterns import (
    LETTERHEAD_CONFIDENCE,
    first_non_empty_line,
)

# What a person reads off the top of each page. For the purchase order this is
# the buyer, because a PO is issued by the buyer - the rule is asked for
# `buyer_name` there, not `supplier_name`.
EXPECTED = {
    "01_invoice_clean.txt": "NORTHWIND SUPPLIES LTD",
    "02_invoice_arithmetic_error.txt": "CALDERA EQUIPMENT GMBH",
    "03_invoice_tax_error.txt": "ORCHARD PRINT SERVICES",
    "06_invoice_ocr_noise.txt": "HARLOW INSTRUMENTS LIMITED",
    "07_invoice_high_value.txt": "MERIDIAN CAPITAL EQUIPMENT PLC",
    "08_invoice_duplicate.txt": "NORTHWIND SUPPLIES LTD",
    "04_invoice_bad_iban.txt": "ASHDOWN LOGISTICS LTD",
    "05_invoice_future_date.txt": "LOWRY TECHNICAL SERVICES",
    "10_receipt_cafe.txt": "THE BRAMBLE COFFEE HOUSE",
    "11_purchase_order.txt": "FENWICK ANALYTICS LTD",
}


def main() -> int:
    samples = Path(__file__).resolve().parents[1] / "samples"

    correct = 0
    checked = 0
    for name in sorted(EXPECTED):
        path = samples / name
        if not path.exists():
            print("   %-32s MISSING FILE" % name)
            continue

        text = clean_document_text(load_from_path(path).text).text
        _, first_line = first_non_empty_line(text.split("\n"))

        checked = checked + 1
        expected = EXPECTED[name]
        if first_line == expected:
            correct = correct + 1
            print("   %-32s %s" % (name, first_line))
        else:
            print("   %-32s WRONG: got %r, wanted %r" % (name, first_line, expected))

    if checked == 0:
        print("\nno documents checked")
        return 1

    rate = correct / checked
    print("\nletterhead rule: %d of %d correct (%.0f%%)" % (correct, checked, rate * 100))
    print("confidence constant in the code: %.2f" % LETTERHEAD_CONFIDENCE)

    if rate < LETTERHEAD_CONFIDENCE:
        print("\nThe measured hit rate is now BELOW the confidence the code claims.")
        print("Lower LETTERHEAD_CONFIDENCE in app/layer5_extract/step2_patterns.py.")
        return 1

    print("\nThe claimed confidence is not above the measured hit rate, which is")
    print("what we want - the code should never claim more than it has shown.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
