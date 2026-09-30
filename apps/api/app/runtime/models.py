"""Default model selection.

Agno owns the model abstraction (``agno.models.base.Model``) and the provider
integrations. This module only maps configuration to a native Agno model class:

    provider + model ID  ->  OpenAIResponses | Claude  (an Agno ``Model``)
    demo                 ->  DemoOperationsModel (LOCAL-DEMO-ONLY, local/test only)

Provider credentials stay in each provider's standard environment variable and are
read by the Agno model itself; they are only checked for presence here, never read
into settings, logged or returned. To add a provider, add one entry to
``_PROVIDERS`` — agents depend on ``Model``, not on provider classes.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agno.models.base import Model

from app.config import DEMO_MODEL_PROVIDER, DEVELOPMENT_ENVIRONMENTS, ModelProvider, Settings
from app.runtime.errors import ModelConfigurationError


def _openai(model_id: str) -> Model:
    from agno.models.openai.responses import OpenAIResponses

    return OpenAIResponses(id=model_id)


def _anthropic(model_id: str) -> Model:
    from agno.models.anthropic.claude import Claude

    return Claude(id=model_id)


@dataclass(frozen=True)
class _Provider:
    api_key_variable: str
    build: Callable[[str], Model]


_PROVIDERS: dict[str, _Provider] = {
    "openai": _Provider(api_key_variable="OPENAI_API_KEY", build=_openai),
    "anthropic": _Provider(api_key_variable="ANTHROPIC_API_KEY", build=_anthropic),
}


def _demo(model_id: str | None, environment: str | None) -> Model:
    """The deterministic local demo model: no provider, no key, no network. Refused
    unless the environment is explicitly a development one (fail closed)."""
    from app.runtime.demo_model import DEMO_MODEL_ID, DemoOperationsModel

    if environment not in DEVELOPMENT_ENVIRONMENTS:
        raise ModelConfigurationError(
            "The demo model provider is allowed only in the local and test environments."
        )
    if model_id not in (None, DEMO_MODEL_ID):
        raise ModelConfigurationError(
            f"APP_DEFAULT_MODEL_ID must be empty or {DEMO_MODEL_ID!r} for the demo provider."
        )
    return DemoOperationsModel()


def build_model(
    provider: ModelProvider,
    model_id: str | None,
    environ: Mapping[str, str] = os.environ,
    environment: str | None = None,
) -> Model | None:
    """Return the native Agno model for ``provider``, or ``None`` when disabled.

    ``environment`` is the Product environment; only the demo provider depends on it.
    """
    if provider == "disabled":
        return None
    if provider == DEMO_MODEL_PROVIDER:
        return _demo(model_id, environment)
    spec = _PROVIDERS.get(provider)
    if spec is None:
        raise ModelConfigurationError(f"Unsupported model provider: {provider!r}")
    if not model_id:
        raise ModelConfigurationError(
            f"APP_DEFAULT_MODEL_ID is required when APP_DEFAULT_MODEL_PROVIDER={provider}."
        )
    if not environ.get(spec.api_key_variable, "").strip():
        raise ModelConfigurationError(
            f"{spec.api_key_variable} must be set when APP_DEFAULT_MODEL_PROVIDER={provider}."
        )
    return spec.build(model_id)


def build_default_model(
    settings: Settings, environ: Mapping[str, str] = os.environ
) -> Model | None:
    return build_model(
        settings.default_model_provider,
        settings.default_model_id,
        environ,
        environment=settings.environment,
    )
