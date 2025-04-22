"""
LAYER 10 - THE GOLDEN DATASET
=============================
What a person reads off each document, typed out by hand.

This file is the only honest measure of whether the pipeline works. Everything
else in the project is the pipeline's opinion of itself: confidence scores,
validation issues, decisions. Those can all be high and all be wrong at the same
time. Ground truth is the only thing that can say so.

Three kinds of expectation per document:

  fields        what the values actually are
  defects       which validation codes SHOULD fire, because the document really
                does carry that defect
  decision      what should happen to it

The `defects` entry matters as much as `fields`, and in both directions. A
missing code is a defect that got through. An extra code is a false alarm - and
false alarms are the more dangerous failure, because a queue full of nonsense
teaches people to approve without reading, which removes the only safeguard the
whole system has.
"""

from doc_intelligence.layer2_models.schemas import Decision, DocumentType

GOLDEN = {
    # ---------------- clean documents ----------------
    "01_invoice_clean.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.AUTO_APPROVE,
        "defects": [],
        "fields": {
            "invoice_number": "INV-2025-0412",
            "invoice_date": "2025-03-14",
            "due_date": "2025-04-13",
            "supplier_name": "NORTHWIND SUPPLIES LTD",
            "supplier_vat": "GB123456789",
            "customer_name": "Fenwick Analytics Ltd",
            "subtotal": "2323.00",
            "tax_amount": "464.60",
            "total_amount": "2787.60",
            "iban": "GB82 WEST 1234 5698 7654 32",
            "payment_terms": "30 days net",
        },
        "line_items": 4,
    },
    "11_purchase_order.txt": {
        "type": DocumentType.PURCHASE_ORDER,
        "decision": Decision.AUTO_APPROVE,
        "defects": [],
        "fields": {
            "po_number": "PO-2025-0311",
            "order_date": "2025-02-28",
            "required_by_date": "2025-03-21",
            "buyer_name": "FENWICK ANALYTICS LTD",
            "supplier_name": "Northwind Supplies Ltd",
            "subtotal": "2323.00",
            "tax_amount": "464.60",
            "total_amount": "2787.60",
            "authorised_by": "R. Patel, Operations",
        },
        "line_items": 4,
    },
    "10_receipt_cafe.txt": {
        "type": DocumentType.RECEIPT,
        "decision": Decision.AUTO_APPROVE,
        "defects": [],
        "fields": {
            "receipt_number": "R-88214",
            "receipt_date": "2025-03-07",
            "merchant_name": "THE BRAMBLE COFFEE HOUSE",
            "supplier_vat": "GB318273645",
            "subtotal": "11.75",
            "tax_amount": "2.35",
            "total_amount": "14.10",
            "payment_method": "card ending 4242",
        },
        "line_items": 3,
    },
    "12_contract_services.txt": {
        "type": DocumentType.CONTRACT,
        # Clean, but the value is above the limit, so a person sees it anyway.
        "decision": Decision.NEEDS_REVIEW,
        "defects": [],
        "fields": {
            "agreement_date": "2025-04-01",
            "party_one": "Fenwick Analytics Ltd",
            "party_two": "Copperfield Data Consulting Ltd",
            "start_date": "2025-04-15",
            "end_date": "2026-04-14",
            "contract_value": "84000.00",
        },
        "line_items": 0,
    },
    "07_invoice_high_value.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        "defects": [],
        "fields": {
            "invoice_number": "INV-2025-2001",
            "invoice_date": "2025-06-03",
            "supplier_name": "MERIDIAN CAPITAL EQUIPMENT PLC",
            "subtotal": "49900.00",
            "tax_amount": "9980.00",
            "total_amount": "59880.00",
        },
        "line_items": 3,
    },

    # ---------------- documents carrying a specific defect ----------------
    "02_invoice_arithmetic_error.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        "defects": ["line_items_do_not_sum_to_subtotal"],
        "fields": {
            "invoice_number": "INV-2025-0876",
            "invoice_date": "2025-04-02",
            "supplier_name": "CALDERA EQUIPMENT GMBH",
            "supplier_vat": "DE123456789",
            "subtotal": "4630.00",
            "tax_amount": "879.70",
            "total_amount": "5509.70",
        },
        "line_items": 3,
    },
    "03_invoice_tax_error.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        "defects": ["subtotal_plus_tax_is_not_total"],
        "fields": {
            "invoice_number": "INV-2025-1150",
            "invoice_date": "2025-05-21",
            "supplier_name": "ORCHARD PRINT SERVICES",
            "subtotal": "1685.00",
            "tax_amount": "337.00",
            "total_amount": "2122.00",
        },
        "line_items": 2,
    },
    "04_invoice_bad_iban.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        "defects": ["iban_checksum_failed"],
        "fields": {
            "invoice_number": "INV-2025-0655",
            "invoice_date": "2025-04-19",
            "supplier_name": "ASHDOWN LOGISTICS LTD",
            "subtotal": "780.00",
            "tax_amount": "156.00",
            "total_amount": "936.00",
            "iban": "GB82 WEST 1234 5698 7654 99",
        },
        "line_items": 2,
    },
    "05_invoice_future_date.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        "defects": ["document_dated_in_the_future"],
        "fields": {
            "invoice_number": "INV-2027-0003",
            "invoice_date": "2027-01-11",
            "supplier_name": "LOWRY TECHNICAL SERVICES",
            "subtotal": "1200.00",
            "tax_amount": "240.00",
            "total_amount": "1440.00",
        },
        "line_items": 1,
    },
    "08_invoice_duplicate.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        # Two independent signals, and both should fire.
        "defects": ["same_reference_already_processed",
                    "document_says_it_is_a_duplicate"],
        "fields": {
            "invoice_number": "INV-2025-0412",
            "invoice_date": "2025-03-14",
            "supplier_name": "NORTHWIND SUPPLIES LTD",
            "subtotal": "2323.00",
            "total_amount": "2787.60",
        },
        "line_items": 4,
        # Must be processed after invoice_clean.txt for the duplicate to exist.
        "after": "01_invoice_clean.txt",
    },
    "06_invoice_ocr_noise.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        # The IBAN is repaired by the scanner fix and then passes its checksum.
        # The VAT number is NOT repaired, on purpose, and is caught here.
        "defects": ["vat_number_format_wrong"],
        "fields": {
            "invoice_number": "INV-2025-0339",
            "invoice_date": "2025-02-08",
            "due_date": "2025-03-10",
            "supplier_name": "HARLOW INSTRUMENTS LIMITED",
            "subtotal": "902.00",
            "tax_amount": "180.40",
            "total_amount": "1082.40",
            "iban": "GB82 WEST 1234 5698 7654 32",
        },
        "line_items": 2,
    },
    "09_invoice_bank_changed.txt": {
        "type": DocumentType.INVOICE,
        "decision": Decision.NEEDS_REVIEW,
        # Everything about this invoice is correct. The arithmetic adds up, the
        # VAT number is right, the dates are sane, and the new IBAN passes its
        # own checksum - because whoever sent it owns a real bank account. The
        # only thing wrong with it is that the account is not the one this
        # supplier was paid into last time.
        "defects": ["supplier_bank_details_changed"],
        "fields": {
            "invoice_number": "INV-2025-0531",
            "invoice_date": "2025-05-02",
            "due_date": "2025-06-01",
            "supplier_name": "NORTHWIND SUPPLIES LTD",
            "supplier_vat": "GB123456789",
            "subtotal": "925.50",
            "tax_amount": "185.10",
            "total_amount": "1110.60",
            "iban": "GB29 NWBK 6016 1331 9268 19",
            "payment_terms": "30 days net",
        },
        "line_items": 2,
        "after": "01_invoice_clean.txt",
    },
    "13_unknown_letter.txt": {
        "type": DocumentType.UNKNOWN,
        "decision": Decision.NEEDS_REVIEW,
        "defects": ["document_type_not_recognised"],
        "fields": {},
        "line_items": 0,
    },
}

# Documents with no planted defect. The false-alarm rate is measured on these,
# and it is the number that decides whether anyone will keep reading the queue.
CLEAN_DOCUMENTS = [
    "01_invoice_clean.txt", "11_purchase_order.txt", "10_receipt_cafe.txt",
    "12_contract_services.txt", "07_invoice_high_value.txt",
]


def processing_order() -> list[str]:
    """
    Names in an order that respects the "after" constraint.

    The duplicate invoice is only a duplicate once the original has been seen,
    so running the corpus in alphabetical order would silently measure the wrong
    thing - invoice_duplicate.txt sorts BEFORE invoice_clean.txt.
    """
    remaining = list(GOLDEN.keys())
    remaining.sort()

    ordered: list[str] = []
    placed: set = set()

    # Two passes are enough for a single level of dependency, which is all this
    # corpus has. If a deeper chain is ever added, this needs a real topological
    # sort - and it should fail loudly rather than quietly mis-order.
    for name in remaining:
        if GOLDEN[name].get("after") is None:
            ordered.append(name)
            placed.add(name)

    for name in remaining:
        if name in placed:
            continue
        required = GOLDEN[name]["after"]
        if required not in placed:
            raise ValueError(
                "%s must run after %s, but %s is not in the corpus"
                % (name, required, required)
            )
        ordered.append(name)
        placed.add(name)

    return ordered
