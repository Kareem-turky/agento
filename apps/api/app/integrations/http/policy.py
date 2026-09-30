"""Trusted, immutable transport policy (code defaults, never end-user values)."""

import math
from dataclasses import dataclass


def _positive_seconds(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _non_negative_seconds(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _count(value: object, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


@dataclass(frozen=True, slots=True)
class IntegrationHttpPolicy:
    """Timeouts, retry bounds, size caps and connection limits.

    Every value is explicit (nothing relies on client-library defaults) and validated:
    timeouts are positive and finite, sizes and delays non-negative, at least one
    attempt, and keep-alive connections never exceed the connection limit.
    """

    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0
    write_timeout_seconds: float = 15.0
    pool_timeout_seconds: float = 5.0
    max_read_attempts: int = 3
    base_retry_delay_seconds: float = 0.25
    max_retry_after_seconds: float = 5.0
    max_request_bytes: int = 2 * 1024 * 1024
    max_response_bytes: int = 8 * 1024 * 1024
    max_connections: int = 20
    max_keepalive_connections: int = 10

    def __post_init__(self) -> None:
        for name in ("connect_timeout_seconds", "read_timeout_seconds",
                     "write_timeout_seconds", "pool_timeout_seconds"):  # fmt: skip
            if not _positive_seconds(getattr(self, name)):
                raise ValueError(f"{name} must be a positive, finite number of seconds")
        for name in ("base_retry_delay_seconds", "max_retry_after_seconds"):
            if not _non_negative_seconds(getattr(self, name)):
                raise ValueError(f"{name} must be a non-negative, finite number of seconds")
        if not _count(self.max_read_attempts, 1):
            raise ValueError("max_read_attempts must be an integer >= 1")
        for name in ("max_request_bytes", "max_response_bytes"):
            if not _count(getattr(self, name), 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if not _count(self.max_connections, 1):
            raise ValueError("max_connections must be an integer >= 1")
        if not _count(self.max_keepalive_connections, 0):
            raise ValueError("max_keepalive_connections must be a non-negative integer")
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("max_keepalive_connections cannot exceed max_connections")

    def retry_delay(self, attempt: int) -> float:
        """Deterministic exponential backoff after failed attempt ``attempt`` (1-based):
        base, base*2, base*4, ... (no jitter)."""
        return self.base_retry_delay_seconds * (2 ** (max(attempt, 1) - 1))
