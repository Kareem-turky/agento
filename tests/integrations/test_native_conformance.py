"""NativeCommerceAdapter passes the complete provider-independent conformance harness,
against the Product-owned commerce store in the real, migrated PostgreSQL."""

import pytest
import sqlalchemy as sa

from tests.commerce_conformance import CONFORMANCE_CHECKS, CommerceConformanceFixture
from tests.integrations.native_conformance import (
    native_conformance_fixture,
    native_dataset,
    reset_and_seed,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def conformance_fixture(migrated: str, engine: sa.Engine) -> CommerceConformanceFixture:
    # Fresh canonical state for every check (the corrupted-data check damages rows).
    reset_and_seed(engine, migrated, native_dataset())
    return native_conformance_fixture(engine, migrated)


@pytest.mark.parametrize("check", CONFORMANCE_CHECKS, ids=lambda c: c.__name__)
def test_native_adapter_conformance(check, conformance_fixture) -> None:
    check(conformance_fixture)
