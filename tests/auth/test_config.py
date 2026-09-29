"""Product auth configuration: single company, hashed keys only, fail-closed deployments."""

import json

import pytest
from pydantic import ValidationError

from app.config import ProductApiKeyPrincipalConfig, Settings
from tests.support.product_auth import TEST_PRODUCT_KEY, principal, sha256_hex

HASH_A = sha256_hex("key-a-" + "x" * 40)
HASH_B = sha256_hex("key-b-" + "y" * 40)


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def api_key_settings(**overrides) -> Settings:
    data = {
        "environment": "production",
        "product_auth_mode": "api_key",
        "company_id": "company-1",
        "product_api_keys": (principal(),),
    }
    data.update(overrides)
    return settings(**data)


@pytest.mark.parametrize("environment", ["local", "test"])
def test_disabled_auth_is_allowed_for_development(environment) -> None:
    s = settings(environment=environment)
    assert (s.product_auth_mode, s.company_id, s.product_api_keys) == ("disabled", None, ())


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_disabled_auth_is_refused_for_deployments(environment) -> None:
    with pytest.raises(ValidationError, match="cannot be disabled"):
        settings(environment=environment)
    with pytest.raises(ValidationError, match="cannot be disabled"):
        settings(environment=environment, product_auth_mode="disabled")


def test_api_key_mode_requires_company_and_principals() -> None:
    with pytest.raises(ValidationError, match="APP_COMPANY_ID"):
        api_key_settings(company_id=None)
    with pytest.raises(ValidationError, match="APP_COMPANY_ID"):
        api_key_settings(company_id="   ")
    with pytest.raises(ValidationError, match="at least one principal"):
        api_key_settings(product_api_keys=())


def test_valid_api_key_config_and_multiple_principals() -> None:
    s = api_key_settings(
        product_api_keys=(
            principal(key_id="a", key_sha256=HASH_A, actor_id="actor-a"),
            principal(key_id="b", key_sha256=HASH_B, actor_id="actor-b"),
        )
    )
    assert s.company_id == "company-1"
    assert [p.actor_id for p in s.product_api_keys] == ["actor-a", "actor-b"]


def test_env_json_representation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("APP_PRODUCT_AUTH_MODE", "api_key")
    monkeypatch.setenv("APP_COMPANY_ID", "company-1")
    monkeypatch.setenv(
        "APP_PRODUCT_API_KEYS",
        json.dumps([{
            "key_id": "operations-api", "key_sha256": HASH_A, "actor_id": "operations-api",
            "role_ids": ["operations"], "permissions": ["orders.read", "tickets.create"],
            "store_ids": ["0b0b0b0b-0000-4000-8000-000000000001"],
        }]),
    )  # fmt: skip
    s = Settings(_env_file=None)
    (p,) = s.product_api_keys
    assert p.permissions == frozenset({"orders.read", "tickets.create"})
    assert isinstance(p.store_ids, frozenset) and isinstance(p.role_ids, frozenset)


@pytest.mark.parametrize(
    "bad_hash",
    [HASH_A.upper(), HASH_A[:63], HASH_A + "0", "g" * 64, "", " " + HASH_A[1:],
     TEST_PRODUCT_KEY],
)  # fmt: skip
def test_invalid_key_hashes_are_rejected(bad_hash) -> None:
    with pytest.raises(ValidationError):
        principal(key_sha256=bad_hash)


def test_duplicates_are_rejected_not_first_or_last_wins() -> None:
    with pytest.raises(ValidationError, match="duplicate Product API key hash"):
        api_key_settings(product_api_keys=(
            principal(key_id="a", key_sha256=HASH_A), principal(key_id="b", key_sha256=HASH_A),
        ))  # fmt: skip
    with pytest.raises(ValidationError, match="duplicate Product API key_id"):
        api_key_settings(product_api_keys=(
            principal(key_id="a", key_sha256=HASH_A), principal(key_id="a", key_sha256=HASH_B),
        ))  # fmt: skip


@pytest.mark.parametrize(
    "extra",
    [{"company_id": "other-company"}, {"raw_key": "x" * 40}, {"key": "x" * 40},
     {"actor_type": "user"}, {"tenant_id": "t"}],
)  # fmt: skip
def test_unknown_principal_fields_are_rejected(extra) -> None:
    with pytest.raises(ValidationError):
        ProductApiKeyPrincipalConfig(key_id="a", key_sha256=HASH_A, actor_id="actor-a", **extra)


def test_principal_config_is_immutable() -> None:
    p = principal(permissions=frozenset({"tickets.create"}))
    with pytest.raises(ValidationError):
        p.actor_id = "other"  # type: ignore[misc]
    assert isinstance(p.permissions, frozenset)
    assert not hasattr(p.permissions, "add")


def test_settings_never_hold_a_raw_key() -> None:
    s = api_key_settings()
    dump = s.model_dump_json() + repr(s)
    assert TEST_PRODUCT_KEY not in dump
    assert sha256_hex(TEST_PRODUCT_KEY) in dump  # only the verifier is configured
    assert set(ProductApiKeyPrincipalConfig.model_fields) == {
        "key_id", "key_sha256", "actor_id", "role_ids", "permissions", "store_ids",
    }  # fmt: skip
