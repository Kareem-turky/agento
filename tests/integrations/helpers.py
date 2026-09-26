"""Shared helpers for integration tests."""

import asyncio
from collections.abc import Coroutine
from dataclasses import replace
from typing import Any
from uuid import UUID

from app.integrations.commerce.mock import EntityType, MockCommerceAdapter, canonical_id
from app.integrations.commerce.mock.fixtures import default_dataset
from app.integrations.commerce.mock.models import MockDataset
from app.integrations.commerce.mock.system import MockCommerceSystem


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def cid(entity: EntityType, key: str) -> UUID:
    return canonical_id(entity, key)


def adapter_with(**changes: Any) -> MockCommerceAdapter:
    """An adapter over the default dataset with some collections replaced."""
    dataset: MockDataset = replace(default_dataset(), **changes)
    return MockCommerceAdapter(MockCommerceSystem(dataset))
