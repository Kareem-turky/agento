"""Seams of the System Operations service (no FastAPI, no SQLAlchemy here)."""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from app.system_operations.models import DatabaseCheck, LifecycleSnapshot


@runtime_checkable
class DatabaseReadinessProbe(Protocol):
    """A bounded check of PostgreSQL and the Product schema revision.

    Implementations never raise and never retry within one call: every failure maps to
    a fixed ``ComponentState``. They never migrate, write or read business data."""

    async def check(self) -> DatabaseCheck: ...


# Reads the application lifecycle (lifespan started, Agent runtime attached).
LifecycleSource = Callable[[], LifecycleSnapshot]
