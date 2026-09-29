"""Deployment composition root: builds the Product services for ``app.bootstrap``.

``APP_BUSINESS_BACKEND`` names a backend plugin in the Product-owned allowlist
(``app.composition.registry``); generic selection lives in
``app.composition.deployment``. Nothing depends on this package except
``app.bootstrap``: routes, services, the domain, execution, governance and persistence
never import it. Mock integrations are imported only by ``app.composition.local_mock``,
lazily and only for local/test.
"""

from app.composition.contracts import (
    BusinessBackendBuilder,
    DeploymentComposition,
    DeploymentCompositionError,
)
from app.composition.deployment import build_deployment_composition
from app.composition.registry import (
    BusinessBackendRegistration,
    BusinessBackendRegistry,
    build_default_backend_registry,
)

__all__ = [
    "BusinessBackendBuilder",
    "BusinessBackendRegistration",
    "BusinessBackendRegistry",
    "DeploymentComposition",
    "DeploymentCompositionError",
    "build_default_backend_registry",
    "build_deployment_composition",
]
