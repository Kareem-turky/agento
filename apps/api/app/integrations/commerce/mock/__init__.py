"""Deterministic in-memory mock commerce provider and its adapter (development/tests).

No network, no credentials. Provider records here never leave the adapter.
"""

from app.integrations.commerce.mock.adapter import MOCK_DESCRIPTOR, MockCommerceAdapter
from app.integrations.commerce.mock.identity import (
    MOCK_ID_NAMESPACE,
    MOCK_SYSTEM_ID,
    EntityType,
    canonical_id,
)
from app.integrations.commerce.mock.system import MockCommerceSystem, MockProviderDownError

__all__ = [
    "MOCK_DESCRIPTOR",
    "MOCK_ID_NAMESPACE",
    "MOCK_SYSTEM_ID",
    "EntityType",
    "MockCommerceAdapter",
    "MockCommerceSystem",
    "MockProviderDownError",
    "canonical_id",
]
