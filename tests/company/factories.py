"""Valid Company Operating Model payloads (JSON-style dicts) for tests."""

from typing import Any


def operating_model_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "company_id": "00000000-0000-4000-8000-000000000001",
        "version": 1,
        "order_sla": {"processing_sla": 86400, "late_order_statuses": ["processing"]},
        "shipment_sla": {"ship_to_delivery_sla": 432000},
        "escalations": [
            {
                "id": "late-orders",
                "name": "Late orders",
                "severity": "warning",
                "condition_key": "order.late",
                "threshold": {"kind": "count", "value": 5},
            }
        ],
        "kpis": {
            "enabled_kpis": ["late_orders", "fulfillment_rate"],
            "primary_kpis": ["late_orders"],
        },
        "reporting": {"timezone": "UTC"},
        "capabilities": {"enabled_agents": ["operations"]},
    }
    data.update(overrides)
    return data
