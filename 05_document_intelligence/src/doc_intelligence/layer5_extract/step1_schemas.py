"""
LAYER 5, STEP 1 - WHAT EACH DOCUMENT TYPE IS MADE OF
====================================================
A table of the fields worth pulling out of each kind of document, how to find
them on the page, and whether the pipeline can proceed without them.

Having a real schema per type is what lets the rest of the pipeline be strict.
"Extract the important fields" is not something you can validate. "An invoice has
a total, and its line items must sum to its subtotal" is.

Three things on each spec do real work:

  prefer        money totals live at the bottom of a page, so the LAST match
                wins. Identifiers are printed once near the top, so the FIRST
                match wins. Taking the first match for "total" would find the
                summary box on page 1 of a multi-page invoice.

  avoid_lines   the word "VAT" appears twice on a British invoice: once on
                "VAT Registration: GB123456789" and once on "VAT 20%  464.60".
                One is a tax amount and one is a company number that happens to
                be nine digits long. Without this list the pipeline reports a
                tax charge of 123,456,789.

  required      only fields the document is meaningless without. Making
                everything required would send every document to a human, which
                is the same as having no pipeline.
"""

from dataclasses import dataclass, field

from doc_intelligence.layer2_models.schemas import DocumentType

# Field kinds decide how the raw text is interpreted and which checks apply.
KIND_TEXT = "text"
KIND_MONEY = "money"
KIND_DATE = "date"
KIND_IDENTIFIER = "identifier"


@dataclass
class FieldSpec:
    name: str
    kind: str
    description: str                                  # shown to the model
    labels: list[str] = field(default_factory=list)   # phrases that introduce it
    required: bool = False
    prefer: str = "first"                             # "first" | "last"
    avoid_lines: list[str] = field(default_factory=list)
    from_block: bool = False      # the value is on the line(s) after the label
    from_letterhead: bool = False  # the value is the first line of the document


# Lines that mention a VAT identity rather than a VAT charge. Shared by every
# money field that could collide with a registration number.
VAT_IDENTITY_LINES = [
    "registration", "reg no", "vat no", "vat number", "vat id",
    "idnr", "ust-id", "tax id", "iban", "ein",
]


INVOICE_FIELDS = [
    FieldSpec(
        name="invoice_number", kind=KIND_IDENTIFIER, required=True,
        description="the supplier's own reference for this invoice",
        labels=["invoice number", "invoice no", "invoice #", "invoice ref"],
    ),
    FieldSpec(
        name="invoice_date", kind=KIND_DATE, required=True,
        description="the date the invoice was issued",
        labels=["invoice date", "date of invoice", "issued"],
    ),
    FieldSpec(
        name="due_date", kind=KIND_DATE,
        description="the date payment is due",
        labels=["due date", "payment due", "due by"],
    ),
    FieldSpec(
        name="supplier_name", kind=KIND_TEXT, required=True, from_letterhead=True,
        description="the company sending the invoice and expecting payment",
    ),
    FieldSpec(
        name="supplier_vat", kind=KIND_IDENTIFIER,
        description="the supplier's VAT or tax registration number",
        labels=["vat registration", "vat reg", "vat no", "vat number",
                "ust-idnr", "ust-id", "vat id", "tax id"],
    ),
    FieldSpec(
        name="customer_name", kind=KIND_TEXT, from_block=True,
        description="the company being billed",
        labels=["bill to", "billed to", "invoice to", "customer"],
    ),
    FieldSpec(
        name="subtotal", kind=KIND_MONEY, prefer="last",
        description="the total before tax",
        labels=["subtotal", "sub total", "net total", "net amount", "goods total"],
    ),
    FieldSpec(
        name="tax_amount", kind=KIND_MONEY, prefer="last",
        description="the tax or VAT charged, as an amount of money not a percentage",
        labels=["vat", "tax", "sales tax", "mwst"],
        avoid_lines=VAT_IDENTITY_LINES,
    ),
    FieldSpec(
        name="total_amount", kind=KIND_MONEY, required=True, prefer="last",
        description="the full amount payable including tax",
        labels=["total", "grand total", "amount due", "total due", "balance due"],
        avoid_lines=["subtotal", "sub total", "net total"],
    ),
    FieldSpec(
        name="iban", kind=KIND_IDENTIFIER,
        description="the bank account the money should be sent to",
        labels=["iban", "account number", "acct no"],
    ),
    FieldSpec(
        name="payment_terms", kind=KIND_TEXT,
        description="how long the customer has to pay",
        labels=["payment terms", "terms of payment", "payment term"],
    ),
]


RECEIPT_FIELDS = [
    FieldSpec(
        name="receipt_number", kind=KIND_IDENTIFIER,
        description="the receipt or transaction reference",
        labels=["receipt no", "receipt number", "transaction no", "trans no"],
    ),
    FieldSpec(
        name="receipt_date", kind=KIND_DATE, required=True,
        description="the date of the purchase",
        labels=["date", "purchased", "transaction date"],
    ),
    FieldSpec(
        name="merchant_name", kind=KIND_TEXT, required=True, from_letterhead=True,
        description="the shop or business that was paid",
    ),
    FieldSpec(
        name="supplier_vat", kind=KIND_IDENTIFIER,
        description="the merchant's VAT number",
        labels=["vat no", "vat number", "vat registration", "vat reg"],
    ),
    FieldSpec(
        name="subtotal", kind=KIND_MONEY, prefer="last",
        description="the total before tax",
        labels=["subtotal", "sub total", "net total"],
    ),
    FieldSpec(
        name="tax_amount", kind=KIND_MONEY, prefer="last",
        description="the tax charged as an amount of money",
        labels=["vat", "tax"],
        avoid_lines=VAT_IDENTITY_LINES,
    ),
    FieldSpec(
        name="total_amount", kind=KIND_MONEY, required=True, prefer="last",
        description="the amount actually paid",
        labels=["total", "amount paid", "to pay"],
        avoid_lines=["subtotal", "sub total", "net total"],
    ),
    FieldSpec(
        name="payment_method", kind=KIND_TEXT,
        description="how the customer paid, for example card or cash",
        labels=["paid by", "payment method", "tendered"],
    ),
]


PURCHASE_ORDER_FIELDS = [
    FieldSpec(
        name="po_number", kind=KIND_IDENTIFIER, required=True,
        description="the buyer's purchase order reference",
        labels=["po number", "po no", "purchase order number", "order number",
                "order no"],
    ),
    FieldSpec(
        name="order_date", kind=KIND_DATE, required=True,
        description="the date the order was placed",
        labels=["order date", "date ordered", "raised on"],
    ),
    FieldSpec(
        name="required_by_date", kind=KIND_DATE,
        description="the date the goods are needed",
        labels=["required by", "delivery date", "needed by", "deliver by"],
    ),
    FieldSpec(
        name="buyer_name", kind=KIND_TEXT, required=True, from_letterhead=True,
        description="the company placing the order",
    ),
    FieldSpec(
        name="supplier_name", kind=KIND_TEXT, required=True, from_block=True,
        description="the company being ordered from",
        labels=["supplier", "vendor", "to:"],
    ),
    FieldSpec(
        name="subtotal", kind=KIND_MONEY, prefer="last",
        description="the order total before tax",
        labels=["subtotal", "sub total", "net total"],
    ),
    FieldSpec(
        name="tax_amount", kind=KIND_MONEY, prefer="last",
        description="the tax on the order as an amount of money",
        labels=["vat", "tax"],
        avoid_lines=VAT_IDENTITY_LINES,
    ),
    FieldSpec(
        name="total_amount", kind=KIND_MONEY, required=True, prefer="last",
        description="the full order value including tax",
        labels=["total", "order total", "grand total"],
        avoid_lines=["subtotal", "sub total", "net total"],
    ),
    FieldSpec(
        name="authorised_by", kind=KIND_TEXT,
        description="the person who approved the order",
        labels=["authorised by", "authorized by", "approved by", "raised by"],
    ),
]


# Contracts share almost nothing with the others. No line items, no arithmetic,
# and the dates mean different things - which is the whole reason this pipeline
# validates per type instead of running one set of checks over everything.
CONTRACT_FIELDS = [
    FieldSpec(
        name="agreement_date", kind=KIND_DATE, required=True,
        description="the date the agreement was made or signed",
        labels=["made on", "dated", "date of this agreement", "entered into on"],
    ),
    FieldSpec(
        name="party_one", kind=KIND_TEXT, required=True, from_block=True,
        description="the first named party, usually the client",
        labels=["between:", "between"],
    ),
    FieldSpec(
        name="party_two", kind=KIND_TEXT, required=True, from_block=True,
        description="the second named party, usually the supplier",
        labels=["and:"],
    ),
    FieldSpec(
        name="start_date", kind=KIND_DATE,
        description="the date the agreement takes effect",
        labels=["commences on", "commencement date", "effective from",
                "start date", "begins on"],
    ),
    FieldSpec(
        name="end_date", kind=KIND_DATE,
        description="the date the agreement ends",
        labels=["continues until", "expires on", "end date", "until",
                "termination date"],
    ),
    FieldSpec(
        name="contract_value", kind=KIND_MONEY,
        description="the value of the agreement",
        labels=["contract value", "total value", "annual value", "charges",
                "fee", "consideration"],
        avoid_lines=VAT_IDENTITY_LINES,
    ),
    FieldSpec(
        name="payment_terms", kind=KIND_TEXT,
        description="when invoices must be paid",
        labels=["payment terms", "payment term", "invoiced"],
    ),
    FieldSpec(
        name="notice_period", kind=KIND_TEXT,
        description="how much notice is needed to terminate",
        labels=["written notice", "notice period", "terminate this agreement by"],
    ),
    FieldSpec(
        name="governing_law", kind=KIND_TEXT,
        description="the jurisdiction the agreement is governed by",
        labels=["governed by", "governing law", "laws of"],
    ),
]


FIELDS_BY_TYPE = {
    DocumentType.INVOICE: INVOICE_FIELDS,
    DocumentType.RECEIPT: RECEIPT_FIELDS,
    DocumentType.PURCHASE_ORDER: PURCHASE_ORDER_FIELDS,
    DocumentType.CONTRACT: CONTRACT_FIELDS,
    DocumentType.UNKNOWN: [],
}

# Which types carry a table of line items that must add up. A contract does not,
# and running the arithmetic checks on one would produce nonsense errors.
TYPES_WITH_LINE_ITEMS = (
    DocumentType.INVOICE,
    DocumentType.RECEIPT,
    DocumentType.PURCHASE_ORDER,
)


def fields_for(document_type: DocumentType) -> list[FieldSpec]:
    if document_type not in FIELDS_BY_TYPE:
        return []
    return FIELDS_BY_TYPE[document_type]


def required_field_names(document_type: DocumentType) -> list[str]:
    names = []
    for spec in fields_for(document_type):
        if spec.required:
            names.append(spec.name)
    return names


def spec_for(document_type: DocumentType, field_name: str) -> FieldSpec | None:
    for spec in fields_for(document_type):
        if spec.name == field_name:
            return spec
    return None


def has_line_items(document_type: DocumentType) -> bool:
    return document_type in TYPES_WITH_LINE_ITEMS
