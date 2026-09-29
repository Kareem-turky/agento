"""Deployment composition: settings -> the Product services handed to ``create_app``.

    create_deployment_app (app.bootstrap)
      -> build_deployment_composition(settings, model=...)
           "disabled" (Product sentinel) -> no business services; local/test only
           otherwise: registry.resolve(id)       unknown          -> fail (generic)
                      registration environments  not allowed here -> fail (generic)
                      registration.builder(settings, model=...)   -> DeploymentComposition
      -> app.main.create_app(..., services, shutdown_callback=composition.close)

Selection is generic: no backend-specific branch lives here, and nothing is built,
imported or asked of a model before the backend is resolved and allowed. There is no
real business backend yet, so staging and production have nothing to select and fail
closed. Nothing here migrates or creates tables. Routes, services and the domain
never import this package.
"""

from agno.models.base import Model

from app.composition.contracts import (
    BACKEND_NOT_ALLOWED,
    INVALID_COMPOSITION,
    NO_DEPLOYMENT_BACKEND,
    UNSUPPORTED_BACKEND,
    DeploymentComposition,
    DeploymentCompositionError,
)
from app.composition.registry import BusinessBackendRegistry, build_default_backend_registry
from app.config import DEVELOPMENT_ENVIRONMENTS, DISABLED_BUSINESS_BACKEND, Settings


def build_deployment_composition(
    settings: Settings,
    *,
    model: Model | None = None,
    registry: BusinessBackendRegistry | None = None,
) -> DeploymentComposition:
    """Compose the Product services for ``settings.business_backend``.

    ``model`` is an explicit Operations/default model override (deterministic test
    models). ``registry`` defaults to the Product allowlist; injecting one is a seam for
    direct composition tests only (the operator-facing factory never exposes it).
    """
    backend_id = settings.business_backend
    if backend_id == DISABLED_BUSINESS_BACKEND:
        if settings.environment not in DEVELOPMENT_ENVIRONMENTS:
            raise DeploymentCompositionError(NO_DEPLOYMENT_BACKEND)
        # Incomplete business surface on purpose: Product business routes answer 503.
        return DeploymentComposition(default_model=model)

    allowlist = registry if registry is not None else build_default_backend_registry()
    if not isinstance(allowlist, BusinessBackendRegistry):
        raise DeploymentCompositionError(UNSUPPORTED_BACKEND)
    registration = allowlist.resolve(backend_id)
    if registration is None:
        raise DeploymentCompositionError(UNSUPPORTED_BACKEND)
    if settings.environment not in registration.allowed_environments:
        # Checked before the builder runs: nothing is imported, built or modelled.
        raise DeploymentCompositionError(BACKEND_NOT_ALLOWED)
    composition = registration.builder(settings, model=model)
    if not isinstance(composition, DeploymentComposition):
        raise DeploymentCompositionError(INVALID_COMPOSITION)
    return composition
