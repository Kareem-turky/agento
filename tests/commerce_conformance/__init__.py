"""Provider-independent CommerceIntegration conformance harness (test-only).

Every commerce adapter (the mock today, real providers later) must pass it before it
may be composed into the Product. Usage for a new adapter::

    from tests.commerce_conformance import CONFORMANCE_CHECKS, CommerceConformanceFixture

    @pytest.fixture
    def conformance_fixture() -> CommerceConformanceFixture: ...   # provider-specific

    @pytest.mark.parametrize("check", CONFORMANCE_CHECKS, ids=lambda c: c.__name__)
    def test_conformance(check, conformance_fixture):
        check(conformance_fixture)

Passing is necessary but NOT sufficient for production: provider authentication,
rate limits, retries, pagination, idempotency and operational security still need
provider-specific review. This package imports only the integration contract, its
queries/errors/descriptor and the canonical domain: never a concrete adapter.
"""

from tests.commerce_conformance.contract import CommerceConformanceFixture
from tests.commerce_conformance.suite import CONFORMANCE_CHECKS

__all__ = ["CONFORMANCE_CHECKS", "CommerceConformanceFixture"]
