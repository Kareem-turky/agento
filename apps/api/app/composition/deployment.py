"""Deployment composition: settings -> the Product services handed to ``create_app``.

    create_deployment_app (app.bootstrap)
      -> build_deployment_composition(settings, model=...)
           "disabled" (Product sentinel) -> no business services; local/test only
           otherwise: registry.resolve(id)       unknown          -> fail (generic)
                      registration environments  not allowed here -> fail (generic)
                      registration.input_spec -> resolve ONLY the declared inputs
                          (none declared: nothing is read; failure -> fail (generic))
                      registration.builder(settings, model=..., inputs=...)
                                                                  -> DeploymentComposition
      -> app.main.create_app(..., services, shutdown_callback=composition.close)

Selection is generic: no backend-specific branch lives here, and nothing is built,
imported, read or asked of a model before the backend is resolved and allowed. There is no
real business backend yet, so staging and production have nothing to select and fail
closed. Nothing here migrates or creates tables. Routes, services and the domain
never import this package.
"""

from typing import TypeGuard

from agno.models.base import Model

from app.composition.backend_inputs import (
    BusinessBackendInputs,
    BusinessBackendInputSource,
    BusinessBackendInputSpec,
    FilesystemBusinessBackendInputSource,
    SecretValue,
)
from app.composition.contracts import (
    BACKEND_INPUTS_UNAVAILABLE,
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
    input_source: BusinessBackendInputSource | None = None,
) -> DeploymentComposition:
    """Compose the Product services for ``settings.business_backend``.

    ``model`` is an explicit Operations/default model override (deterministic test
    models). ``registry`` defaults to the Product allowlist; injecting one is a seam for
    direct composition tests only (the operator-facing factory never exposes it).
    ``input_source`` likewise defaults to the filesystem source over
    ``APP_BACKEND_CONFIG_DIR``/``APP_BACKEND_SECRETS_DIR`` and is a test seam only.
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
    inputs = _resolve_inputs(settings, registration.input_spec, input_source)
    composition = registration.builder(settings, model=model, inputs=inputs)
    if not isinstance(composition, DeploymentComposition):
        raise DeploymentCompositionError(INVALID_COMPOSITION)
    return composition


def _resolve_inputs(
    settings: Settings,
    spec: BusinessBackendInputSpec,
    input_source: BusinessBackendInputSource | None,
) -> BusinessBackendInputs:
    """Exactly the declared inputs, or one fixed error (never a backend id, name, path or
    value). A backend that declares nothing reads nothing and needs no directories."""
    if spec.is_empty:
        return BusinessBackendInputs()
    source = input_source
    if source is None:
        # The default source needs a root for every kind of input that is declared.
        if spec.config_keys and settings.backend_config_dir is None:
            raise DeploymentCompositionError(BACKEND_INPUTS_UNAVAILABLE) from None
        if spec.secret_keys and settings.backend_secrets_dir is None:
            raise DeploymentCompositionError(BACKEND_INPUTS_UNAVAILABLE) from None
        source = FilesystemBusinessBackendInputSource(
            settings.backend_config_dir, settings.backend_secrets_dir
        )
    inputs: object = None
    try:
        inputs = source.load(spec)
    except Exception:  # noqa: BLE001 - any source failure is one fixed, safe error
        inputs = None  # raised below, outside the handler: no chained source error
    if not _is_exact_result(inputs, spec):
        # A failed source, or a malformed result, never reaches the builder.
        raise DeploymentCompositionError(BACKEND_INPUTS_UNAVAILABLE) from None
    return inputs


def _is_exact_result(
    inputs: object, spec: BusinessBackendInputSpec
) -> TypeGuard[BusinessBackendInputs]:
    if type(inputs) is not BusinessBackendInputs:
        return False
    if not inputs.matches(spec):
        return False
    return all(type(v) is str for v in inputs.config.values()) and all(
        isinstance(v, SecretValue) for v in inputs.secrets.values()
    )
