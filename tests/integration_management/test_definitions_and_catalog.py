"""Integration definitions and the immutable, statically reviewed Integration Catalog."""

import pytest
from pydantic import SecretStr, ValidationError

from app.integration_management import (
    ConfigField,
    ConfigFieldKind,
    ConnectionConfigError,
    InstalledIntegration,
    IntegrationAuthMode,
    IntegrationCatalog,
    IntegrationCategory,
    IntegrationDefinition,
    build_default_integration_catalog,
)
from tests.support.integration_fakes import COMMERCE, MARKETING, MESSAGING, FakeDriver, fake_catalog


def definition(**overrides) -> IntegrationDefinition:
    data = {
        "integration_id": "example-x", "name": "X", "category": IntegrationCategory.SHIPPING,
        "description": "d", "auth_mode": IntegrationAuthMode.NONE,
    } | overrides  # fmt: skip
    return IntegrationDefinition(**data)


# ----- definitions ---------------------------------------------------------------------------


def test_categories_auth_modes_and_field_kinds_are_the_typed_vocabulary() -> None:
    assert [c.value for c in IntegrationCategory] == [
        "commerce", "messaging", "marketing", "shipping", "accounting"]  # fmt: skip
    assert [m.value for m in IntegrationAuthMode] == ["none", "credentials", "delegated"]
    assert [k.value for k in ConfigFieldKind] == ["text", "url", "boolean", "secret"]


@pytest.mark.parametrize("bad_id", ["", "a", "Example", "example_x", "example.x", "../x",
                                    "/abs", "x-", "-x", "a" * 65, "module:path"])  # fmt: skip
def test_integration_ids_are_stable_product_identifiers(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        definition(integration_id=bad_id)


@pytest.mark.parametrize("bad", ["read", "Orders.read", "orders.*", "orders read", ""])
def test_capabilities_are_typed_dotted_identifiers(bad: str) -> None:
    with pytest.raises(ValidationError):
        definition(capabilities=frozenset({bad}))
    assert definition(capabilities=frozenset({"orders.read"})).capabilities == {"orders.read"}


def test_definition_consistency_rules() -> None:
    secret = ConfigField(name="api_key", label="Key", kind=ConfigFieldKind.SECRET)
    text = ConfigField(name="label", label="Label", kind=ConfigFieldKind.TEXT)
    with pytest.raises(ValidationError, match="duplicate field"):
        definition(fields=(text, text))
    with pytest.raises(ValidationError, match="at least one secret"):
        definition(auth_mode=IntegrationAuthMode.CREDENTIALS, fields=(text,))
    with pytest.raises(ValidationError, match="only a credentials"):
        definition(auth_mode=IntegrationAuthMode.NONE, fields=(secret,))
    with pytest.raises(ValidationError, match="named like a credential"):
        ConfigField(name="access_token", label="T", kind=ConfigFieldKind.TEXT)
    with pytest.raises(ValidationError):
        definition(unexpected="x")  # extra fields forbidden
    created = definition(auth_mode=IntegrationAuthMode.CREDENTIALS, fields=(text, secret))
    assert created.secret_field_names == {"api_key"} and created.connectable
    assert not MARKETING.connectable  # delegated authorization is declared, not supported
    with pytest.raises(ValidationError):
        created.name = "changed"  # type: ignore[misc]  # immutable


def test_config_validation_is_strict_and_never_echoes_values() -> None:
    planted = "VALUE-THAT-MUST-NOT-LEAK-71"
    good = {"store_url": "https://shop.example.test", "sandbox": True, "region": " eu "}
    assert COMMERCE.validate_config(good) == {
        "store_url": "https://shop.example.test", "sandbox": True, "region": "eu"}  # fmt: skip
    cases = [
        ({}, "required", "store_url"),
        ({"store_url": f"http://{planted}"}, "invalid_url", "store_url"),
        ({"store_url": f"https://u:{planted}@h.test"}, "invalid_url", "store_url"),
        ({"store_url": "https://h.test", "sandbox": planted}, "expected_boolean", "sandbox"),
        ({"store_url": "https://h.test", planted: "x"}, "unknown_field", None),
        ({"store_url": "https://h.test", "api_key": planted}, "unknown_field", None),
        ({"store_url": "https://h.test" + "a" * 300}, "too_long", "store_url"),
    ]
    for raw, code, field in cases:
        with pytest.raises(ConnectionConfigError) as error:
            COMMERCE.validate_config(raw)
        assert (error.value.code, error.value.field) == (code, field)
        assert planted not in str(error.value) and planted not in repr(error.value.args)


def test_secret_validation_requires_a_complete_set_and_never_echoes() -> None:
    planted = "SECRET-VALUE-THAT-MUST-NOT-LEAK"
    secrets = COMMERCE.validate_secrets({"api_key": SecretStr(planted)})
    assert set(secrets) == {"api_key"} and secrets["api_key"].get_secret_value() == planted
    assert planted not in repr(secrets)
    for raw, code in (({}, "required"), ({"api_key": planted, "store_url": "x"},
                                         "unknown_secret_field"),
                      ({"api_key": f" {planted}"}, "invalid_secret"),
                      ({"api_key": planted * 100}, "invalid_secret")):  # fmt: skip
        with pytest.raises(ConnectionConfigError) as error:
            COMMERCE.validate_secrets(raw)
        assert error.value.code == code and planted not in str(error.value)


# ----- catalog -------------------------------------------------------------------------------


def test_default_catalog_installs_no_integration() -> None:
    catalog = build_default_integration_catalog()
    assert len(catalog) == 0 and catalog.definitions() == () and catalog.integration_ids == set()


def test_catalog_lists_deterministically_by_category_then_id() -> None:
    catalog, _ = fake_catalog()
    assert [d.integration_id for d in catalog.definitions()] == [
        "example-commerce", "example-marketing", "example-messaging"]  # fmt: skip
    again, _ = fake_catalog()
    assert again.definitions() == catalog.definitions()
    assert catalog.get("example-commerce").definition is COMMERCE
    assert catalog.get("unknown") is None and "example-messaging" in catalog


def test_catalog_rejects_duplicates_mismatches_and_foreign_entries() -> None:
    entry = InstalledIntegration(MESSAGING, FakeDriver("example-messaging"))
    with pytest.raises(ValueError, match="duplicate integration id"):
        IntegrationCatalog(
            [entry, InstalledIntegration(MESSAGING, FakeDriver("example-messaging"))]
        )
    with pytest.raises(ValueError, match="differ"):
        IntegrationCatalog([InstalledIntegration(MESSAGING, FakeDriver("example-commerce"))])
    with pytest.raises(TypeError):
        IntegrationCatalog([(MESSAGING, FakeDriver("example-messaging"))])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        IntegrationCatalog([InstalledIntegration(MESSAGING, object())])  # type: ignore[arg-type]


def test_catalog_is_immutable_after_construction() -> None:
    catalog, _ = fake_catalog()
    assert not [n for n in dir(catalog) if n.startswith(("add", "register", "remove", "load"))]
    with pytest.raises(AttributeError):
        catalog.extra = 1  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        catalog._installed["x"] = None  # type: ignore[index]
