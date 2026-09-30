"""TEST-ONLY canonical identities, date and expectations of the deterministic mock
commerce fixture, shared by the integration tests and the MVP acceptance suite.

Nothing here changes the fixture (or the Task 007 golden digest): these are the values
the existing fixture already produces for the canonical store and business date.
"""

from app.integrations.commerce.mock import EntityType, canonical_id

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))  # the mock account
SOUTH = str(canonical_id(EntityType.STORE, "shop_south"))  # the canonical store (Europe/Berlin)
NORTH = str(canonical_id(EntityType.STORE, "shop_north"))  # same company, another store
ORDER_2002 = str(canonical_id(EntityType.ORDER, "ord_2002"))  # a failed + a moving shipment
SHIP_507 = str(canonical_id(EntityType.SHIPMENT, "ship_507"))  # the failed shipment

# The canonical, deterministic business date of the Daily Operations Report.
BUSINESS_DATE = "2026-03-03"

# What the deterministic Daily Operations Workflow reports for SOUTH on BUSINESS_DATE.
EXPECTED_TIMEZONE = "Europe/Berlin"
EXPECTED_WINDOW = ("2026-03-03T00:00:00+01:00", "2026-03-04T00:00:00+01:00")
EXPECTED_ORDERS_CREATED = 1
EXPECTED_SHIPMENTS_SHIPPED = 1
EXPECTED_AFFECTED_ORDERS = 1
EXPECTED_FAILED_SHIPMENTS = 1
EXPECTED_FINDINGS = [
    {
        "code": "shipment_failed",
        "severity": "critical",
        "entity_type": "shipment",
        "entity_id": SHIP_507,
        "order_id": ORDER_2002,
        "canonical_status": "failed",
        "recommended_action": "review_failed_shipment",
    }
]
EXPECTED_COVERAGE = {
    "orders": "created_in_business_day",
    "shipments": "shipped_in_business_day",
    "inventory": "not_included",
    "inventory_reason": "store_scoped_inventory_query_unavailable",
}

# Provider/private values that must never leave through the Agent (response text and
# everything the model is shown): provider ids, provider status, tracking, courier,
# customer PII and the mock provider's internal field names.
AGENT_NEVER_SHOWS = (
    "ord_2002", "ship_507", "delivery_failed", "SE000507", "Sample Express", "shop_south",
    "acct_demo", "cus_005", "Robin Demo", "source_status", "external_refs",
)  # fmt: skip

# ... and, stricter, what the Daily Operations Report HTTP response never contains.
REPORT_NEVER_CONTAINS = (
    "ord_2002", "ship_507", "delivery_failed", "SE000507", "Sample Express", "cus_005",
    "Robin Demo", "shop_south", "South Storefront", "acct_demo", COMPANY,
    "source_status", "external_refs", "tracking", "courier", "customer", "company_id",
    "actor_id", "permissions", "role", "@", "mock-commerce",
)  # fmt: skip
