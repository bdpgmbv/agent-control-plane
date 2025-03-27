"""
LAYER 3 - STORAGE: THE DEMO DATA
================================
Two customers and a spread of orders chosen so every interesting case can be
tested:

    ORD-10023  in transit            -> "where is my order?"
    ORD-10024  delivered, $34.50     -> a refund the agent may issue itself
    ORD-10025  delivered, $899.00    -> a refund that must wait for a human
    ORD-10026  refund already running-> "where is my refund?"
    ORD-10027  cancelled
    ORD-10028  delivered 60 days ago -> outside the refund window, must be refused
    ORD-20001  belongs to BOB        -> Alice must never be able to read it

That last one is the important row. It is how we prove the agent cannot be
talked into reading another customer's data.
"""

import json
from datetime import UTC, datetime, timedelta


def days_ago(count: int) -> str:
    return (datetime.now(UTC) - timedelta(days=count)).isoformat()


def days_ahead(count: int) -> str:
    return (datetime.now(UTC) + timedelta(days=count)).date().isoformat()


CUSTOMERS = [
    {
        "customer_id": "CUST-1001",
        "name": "Alice Nwosu",
        "email": "alice.nwosu@example.com",
        "phone": "+44 7700 900123",
        "tier": "gold",
    },
    {
        "customer_id": "CUST-1002",
        "name": "Bob Fernandez",
        "email": "bob.fernandez@example.com",
        "phone": "+1 415 555 0142",
        "tier": "standard",
    },
]

ORDERS = [
    {
        "order_id": "ORD-10023",
        "customer_id": "CUST-1001",
        "status": "in_transit",
        "items": [{"name": "Noise cancelling headphones", "quantity": 1, "price": 129.00}],
        "total_amount": 129.00,
        "placed_at": days_ago(4),
        "expected_delivery": days_ahead(2),
        "delivered_at": "",
        "tracking_number": "1Z999AA10123456784",
        "carrier": "UPS",
    },
    {
        "order_id": "ORD-10024",
        "customer_id": "CUST-1001",
        "status": "delivered",
        "items": [{"name": "Laptop stand", "quantity": 1, "price": 34.50}],
        "total_amount": 34.50,
        "placed_at": days_ago(12),
        "expected_delivery": days_ahead(-7),
        "delivered_at": days_ago(7),
        "tracking_number": "1Z999AA10123456785",
        "carrier": "UPS",
    },
    {
        "order_id": "ORD-10025",
        "customer_id": "CUST-1001",
        "status": "delivered",
        "items": [{"name": "Espresso machine", "quantity": 1, "price": 899.00}],
        "total_amount": 899.00,
        "placed_at": days_ago(9),
        "expected_delivery": days_ahead(-4),
        "delivered_at": days_ago(4),
        "tracking_number": "1Z999AA10123456786",
        "carrier": "UPS",
    },
    {
        "order_id": "ORD-10026",
        "customer_id": "CUST-1001",
        "status": "returned",
        "items": [{"name": "Desk lamp", "quantity": 2, "price": 24.00}],
        "total_amount": 48.00,
        "placed_at": days_ago(25),
        "expected_delivery": days_ahead(-20),
        "delivered_at": days_ago(20),
        "tracking_number": "1Z999AA10123456787",
        "carrier": "UPS",
    },
    {
        "order_id": "ORD-10027",
        "customer_id": "CUST-1001",
        "status": "cancelled",
        "items": [{"name": "Mechanical keyboard", "quantity": 1, "price": 89.00}],
        "total_amount": 89.00,
        "placed_at": days_ago(3),
        "expected_delivery": "",
        "delivered_at": "",
        "tracking_number": "",
        "carrier": "",
    },
    {
        "order_id": "ORD-10028",
        "customer_id": "CUST-1001",
        "status": "delivered",
        "items": [{"name": "Wireless mouse", "quantity": 1, "price": 26.00}],
        "total_amount": 26.00,
        "placed_at": days_ago(70),
        "expected_delivery": days_ahead(-64),
        "delivered_at": days_ago(63),
        "tracking_number": "1Z999AA10123456788",
        "carrier": "UPS",
    },
    {
        "order_id": "ORD-20001",
        "customer_id": "CUST-1002",
        "status": "in_transit",
        "items": [{"name": "Standing desk", "quantity": 1, "price": 420.00}],
        "total_amount": 420.00,
        "placed_at": days_ago(2),
        "expected_delivery": days_ahead(5),
        "delivered_at": "",
        "tracking_number": "1Z999AA10199999999",
        "carrier": "UPS",
    },
]

REFUNDS = [
    {
        "refund_id": "REF-5001",
        "order_id": "ORD-10026",
        "customer_id": "CUST-1001",
        "amount": 48.00,
        "status": "processing",
        "reason": "items returned by customer",
        "created_at": days_ago(6),
        "completed_at": "",
    }
]

HELP_ARTICLES = [
    {
        "article_id": "HELP-1",
        "title": "Refund policy",
        "body": (
            "You can request a refund within 30 days of the delivery date. Refunds are "
            "returned to the original payment method and take 5 to 10 business days to "
            "appear on your statement. The original shipping charge is not refunded "
            "unless the return was caused by our error."
        ),
        "tags": "refund money back return window 30 days",
    },
    {
        "article_id": "HELP-2",
        "title": "Delivery times",
        "body": (
            "Standard delivery takes five working days and is free on orders above 50 "
            "dollars. Express delivery takes two working days and costs 12.99 dollars. "
            "A parcel is treated as lost after 10 working days past its latest estimated "
            "delivery date."
        ),
        "tags": "delivery shipping how long express standard lost parcel",
    },
    {
        "article_id": "HELP-3",
        "title": "How to return an item",
        "body": (
            "Start a return from the order page, print the prepaid label, and drop the "
            "parcel at any carrier point. Once we receive it, your refund is issued "
            "within three working days. Items must be unused and in their original "
            "packaging."
        ),
        "tags": "return returns send back label unused packaging",
    },
    {
        "article_id": "HELP-4",
        "title": "Items that cannot be refunded",
        "body": (
            "Digital gift cards, personalised engraved items and opened software licences "
            "are not refundable under any circumstances."
        ),
        "tags": "not refundable gift card personalised engraved software exceptions",
    },
    {
        "article_id": "HELP-5",
        "title": "Warranty",
        "body": (
            "All hardware carries a 24 month warranty from the date of purchase. "
            "Accessories carry a 6 month warranty. A warranty claim needs the original "
            "order number."
        ),
        "tags": "warranty guarantee broken faulty repair 24 months",
    },
]


def seed(database, fresh: bool = False) -> dict:
    """
    Load the demo data.

    fresh=True wipes the business tables first. That matters more than it sounds:
    a refund issued by a previous run stays in the database, so the next run finds
    "this order is already refunded" and behaves completely differently. Tests and
    evaluation runs must start from the same state every time, or their results
    describe the order the tests happened to run in.
    """
    now = datetime.now(UTC).isoformat()

    if fresh:
        for table in ["refunds", "orders", "customers", "help_articles"]:
            database.connection.execute("DELETE FROM " + table)
        database.connection.commit()

    for customer in CUSTOMERS:
        database.connection.execute(
            """
            INSERT OR REPLACE INTO customers (customer_id, name, email, phone, tier, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                customer["customer_id"],
                customer["name"],
                customer["email"],
                customer["phone"],
                customer["tier"],
                now,
            ),
        )

    for order in ORDERS:
        database.connection.execute(
            """
            INSERT OR REPLACE INTO orders
                (order_id, customer_id, status, items_json, total_amount, currency,
                 placed_at, expected_delivery, delivered_at, tracking_number, carrier)
            VALUES (?, ?, ?, ?, ?, 'USD', ?, ?, ?, ?, ?)
            """,
            (
                order["order_id"],
                order["customer_id"],
                order["status"],
                json.dumps(order["items"]),
                order["total_amount"],
                order["placed_at"],
                order["expected_delivery"],
                order["delivered_at"],
                order["tracking_number"],
                order["carrier"],
            ),
        )

    for refund in REFUNDS:
        database.connection.execute(
            """
            INSERT OR REPLACE INTO refunds
                (refund_id, order_id, customer_id, amount, status, reason, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                refund["refund_id"],
                refund["order_id"],
                refund["customer_id"],
                refund["amount"],
                refund["status"],
                refund["reason"],
                refund["created_at"],
                refund["completed_at"],
            ),
        )

    for article in HELP_ARTICLES:
        database.connection.execute(
            """
            INSERT OR REPLACE INTO help_articles (article_id, title, body, tags)
            VALUES (?, ?, ?, ?)
            """,
            (article["article_id"], article["title"], article["body"], article["tags"]),
        )

    database.connection.commit()

    return {
        "customers": len(CUSTOMERS),
        "orders": len(ORDERS),
        "refunds": len(REFUNDS),
        "help_articles": len(HELP_ARTICLES),
    }
