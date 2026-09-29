"""MockCommerceAdapter passes the complete provider-independent conformance harness."""

import pytest

from tests.commerce_conformance import CONFORMANCE_CHECKS, CommerceConformanceFixture
from tests.integrations.mock_conformance import mock_conformance_fixture


@pytest.fixture
def conformance_fixture() -> CommerceConformanceFixture:
    return mock_conformance_fixture()


@pytest.mark.parametrize("check", CONFORMANCE_CHECKS, ids=lambda c: c.__name__)
def test_mock_adapter_conformance(check, conformance_fixture) -> None:
    check(conformance_fixture)
