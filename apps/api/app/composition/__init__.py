"""Deployment composition root: builds the Product services for ``app.bootstrap``.

Depends on configuration, persistence, governance, execution, commands, operations,
agents, integrations and the runtime model boundary. Nothing depends on it except
``app.bootstrap``: routes, services, the domain, execution, governance and
persistence never import it. Mock integrations are imported only by
``app.composition.local_mock``, and only for local/test.
"""

from app.composition.deployment import (
    DeploymentComposition,
    DeploymentCompositionError,
    build_deployment_composition,
)

__all__ = [
    "DeploymentComposition",
    "DeploymentCompositionError",
    "build_deployment_composition",
]
