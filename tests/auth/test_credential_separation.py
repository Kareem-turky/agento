"""The Product API key and OS_SECURITY_KEY are separate credentials by construction.

If a configured Product key IS the OS_SECURITY_KEY, one Bearer credential would open
both surfaces, so the app must refuse to build (default resolver composition).
"""

import logging

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.auth import ProductAuthConfigurationError, validate_credential_separation
from app.config import Settings
from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.actor_resolver import StaticActorResolver
from tests.support.product_auth import principal, sha256_hex

SHARED = "test-SHARED-credential-COLLISIONMARKER-" + "s" * 16  # test-only
PRODUCT_A = "test-product-key-A-" + "a" * 30
PRODUCT_B = "test-product-key-B-" + "b" * 30
MESSAGE = "Product API credentials must be distinct from OS_SECURITY_KEY"


def configured(settings: Settings, *keys: str, environment: str = "production") -> Settings:
    principals = tuple(
        principal(key, key_id=f"key-id-{i}", actor_id=f"actor-id-{i}") for i, key in enumerate(keys)
    )
    data = settings.model_dump() | {
        "environment": environment,
        "product_auth_mode": "api_key",
        "company_id": "deployment-company",
        "product_api_keys": principals,
    }
    return Settings(_env_file=None, **data)


def os_settings(key: str) -> AgnoAPISettings:
    return AgnoAPISettings(os_security_key=key)


@pytest.mark.parametrize(
    "keys",
    [(SHARED,), (SHARED, PRODUCT_A, PRODUCT_B), (PRODUCT_A, PRODUCT_B, SHARED),
     (PRODUCT_A, SHARED, PRODUCT_B)],
    ids=["only", "first", "last", "middle"],
)  # fmt: skip
def test_a_product_key_equal_to_the_os_key_refuses_to_build(settings, keys, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ProductAuthConfigurationError) as info:
        create_app(configured(settings, *keys), os_settings(SHARED))
    assert str(info.value) == MESSAGE
    text = str(info.value) + repr(info.value) + caplog.text
    text += "".join(r.getMessage() for r in caplog.records)
    for secret in (SHARED, "COLLISIONMARKER", sha256_hex(SHARED), "key-id-", "actor-id-"):
        assert secret not in text
    assert info.value.__cause__ is None


@pytest.mark.parametrize("environment", ["local", "test", "staging", "production"])
def test_collision_is_refused_in_every_environment(settings, environment) -> None:
    with pytest.raises(ProductAuthConfigurationError):
        create_app(configured(settings, SHARED, environment=environment), os_settings(SHARED))


def test_distinct_credentials_build_and_stay_separated(settings) -> None:
    app = create_app(configured(settings, PRODUCT_A, PRODUCT_B), os_settings(TEST_OS_SECURITY_KEY))
    status = "/api/v1/operations/tickets/commands"
    params = {"command_id": "0c0c0c0c-0000-4000-8000-000000000001"}
    with TestClient(app) as client:
        for key in (PRODUCT_A, PRODUCT_B):
            headers = {"Authorization": f"Bearer {key}"}
            assert client.get("/agents", headers=headers).status_code == 401
            # Authenticated (the unconfigured query service answers 503, not 401).
            assert client.get(status, params=params, headers=headers).status_code == 503
        os_headers = {"Authorization": f"Bearer {TEST_OS_SECURITY_KEY}"}
        assert client.get("/agents", headers=os_headers).status_code == 200
        assert client.get(status, params=params, headers=os_headers).status_code == 401


def test_explicit_resolver_override_is_unchanged(settings) -> None:
    # The settings-backed keys are not the active Product authentication here.
    app = create_app(
        configured(settings, SHARED), os_settings(SHARED), actor_resolver=StaticActorResolver()
    )
    assert app is not None


def test_disabled_auth_is_unchanged(settings) -> None:
    dev = Settings(_env_file=None, **(settings.model_dump() | {"environment": "test"}))
    assert create_app(dev, os_settings(SHARED)) is not None
    for environment in ("staging", "production"):
        with pytest.raises(ProductAuthConfigurationError):
            create_app(dev.model_copy(update={"environment": environment}), os_settings(SHARED))


@pytest.mark.parametrize(
    "os_key",
    [
        "k" * 20 + "\t" + "k" * 20,  # internal whitespace: valid AgentOS key, never a Product key
        "os-key-with space-" + "z" * 20,
        "é" * 40,
        "y" * 300,  # longer than any Product key
    ],
)
def test_os_keys_that_cannot_be_product_keys_are_accepted(settings, os_key) -> None:
    validate_credential_separation(configured(settings, PRODUCT_A), os_key)
    app = create_app(configured(settings, PRODUCT_A), os_settings(os_key))
    assert app is not None


def test_helper_contract(settings) -> None:
    s = configured(settings, PRODUCT_A, SHARED)
    validate_credential_separation(s, TEST_OS_SECURITY_KEY)  # distinct: fine
    validate_credential_separation(s, None)  # no OS key at all: nothing can collide
    with pytest.raises(ProductAuthConfigurationError, match=MESSAGE):
        validate_credential_separation(s, SHARED)
    # Surrounding whitespace on the OS key is still the same credential.
    with pytest.raises(ProductAuthConfigurationError, match=MESSAGE):
        validate_credential_separation(s, f"  {SHARED}\n")
    dev = Settings(_env_file=None, environment="test")
    validate_credential_separation(dev, SHARED)  # no configured keys


def test_separation_check_uses_the_shared_hash_and_constant_time() -> None:
    import ast
    import inspect

    from app.auth import api_keys

    source = inspect.getsource(api_keys.validate_credential_separation)
    tree = ast.parse(source)
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "hmac.compare_digest" in calls and "hash_api_key" in calls
    assert "break" not in source and "==" not in source
