"""TEST-ONLY Product API key material. Never used by production code.

The raw keys below are obviously fake, deterministic and exist only inside the test
process; configuration (like production) holds only their SHA-256.
"""

import hashlib
from typing import Any

from app.config import ProductApiKeyPrincipalConfig, Settings

TEST_COMPANY_ID = "test-deployment-company"
# Obviously test-only; long enough for the 32-character minimum.
TEST_PRODUCT_KEY = "test-product-key-" + "a" * 32  # noqa: S105 - test fixture, not a secret


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def principal(raw_key: str = TEST_PRODUCT_KEY, **overrides: Any) -> ProductApiKeyPrincipalConfig:
    data: dict[str, Any] = {
        "key_id": "test-key",
        "key_sha256": sha256_hex(raw_key),
        "actor_id": "test-api-client",
        "role_ids": frozenset({"operations"}),
        "permissions": frozenset(),
        "store_ids": frozenset(),
    }
    data.update(overrides)
    return ProductApiKeyPrincipalConfig(**data)


def deployment_settings(settings: Settings, environment: str = "production", **updates):
    """``settings`` as a real deployment: Product API-key auth enabled and validated."""
    data = settings.model_dump() | {
        "environment": environment,
        "product_auth_mode": "api_key",
        "company_id": TEST_COMPANY_ID,
        "product_api_keys": (principal(),),
    }
    data.update(updates)
    return Settings(_env_file=None, **data)
