"""Default model factory: configuration -> native Agno model. No network calls."""

import pytest
from agno.models.anthropic.claude import Claude
from agno.models.openai.responses import OpenAIResponses
from pydantic import ValidationError

from app.config import Settings
from app.runtime import ModelConfigurationError, RuntimeConfigurationError
from app.runtime.models import build_default_model, build_model

DUMMY_KEY = "dummy-test-value-not-a-real-key"  # noqa: S105 - test fixture


def test_openai_returns_native_responses_model_with_exact_id() -> None:
    model = build_model("openai", "example-openai-model-id", {"OPENAI_API_KEY": DUMMY_KEY})

    assert type(model) is OpenAIResponses
    assert model.id == "example-openai-model-id"
    assert model.api_key is None  # Agno reads OPENAI_API_KEY itself; we never hold it


def test_anthropic_returns_native_claude_model_with_exact_id() -> None:
    model = build_model("anthropic", "example-anthropic-model-id", {"ANTHROPIC_API_KEY": DUMMY_KEY})

    assert type(model) is Claude
    assert model.id == "example-anthropic-model-id"
    assert model.api_key is None


def test_disabled_returns_no_model_and_needs_nothing() -> None:
    assert build_model("disabled", None, {}) is None


@pytest.mark.parametrize(
    ("provider", "variable"),
    [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")],
)
@pytest.mark.parametrize("environ", [{}, {"OPENAI_API_KEY": " ", "ANTHROPIC_API_KEY": ""}])
def test_missing_provider_key_is_a_configuration_error(provider, variable, environ) -> None:
    with pytest.raises(ModelConfigurationError, match=variable):
        build_model(provider, "some-model-id", environ)


def test_only_the_selected_providers_key_is_required() -> None:
    assert build_model("anthropic", "some-model-id", {"ANTHROPIC_API_KEY": DUMMY_KEY})
    with pytest.raises(ModelConfigurationError, match="OPENAI_API_KEY"):
        build_model("openai", "some-model-id", {"ANTHROPIC_API_KEY": DUMMY_KEY})


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("model_id", [None, ""])
def test_missing_model_id_is_a_configuration_error(provider, model_id) -> None:
    environ = {"OPENAI_API_KEY": DUMMY_KEY, "ANTHROPIC_API_KEY": DUMMY_KEY}

    with pytest.raises(ModelConfigurationError, match="APP_DEFAULT_MODEL_ID"):
        build_model(provider, model_id, environ)


def test_errors_never_contain_key_values() -> None:
    with pytest.raises(ModelConfigurationError) as error:
        build_model("openai", None, {"OPENAI_API_KEY": DUMMY_KEY})

    assert DUMMY_KEY not in str(error.value)
    assert issubclass(ModelConfigurationError, RuntimeConfigurationError)


def test_unsupported_provider_is_rejected_by_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DEFAULT_MODEL_PROVIDER", "unsupported-provider")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_unsupported_provider_is_rejected_by_factory() -> None:
    with pytest.raises(ModelConfigurationError, match="Unsupported"):
        build_model("unsupported", "x", {})  # type: ignore[arg-type]


def test_default_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("APP_DEFAULT_MODEL_PROVIDER", "APP_DEFAULT_MODEL_ID"):
        monkeypatch.delenv(variable, raising=False)

    defaults = Settings(_env_file=None)
    assert defaults.default_model_provider == "disabled"
    assert defaults.default_model_id is None

    monkeypatch.setenv("APP_DEFAULT_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("APP_DEFAULT_MODEL_ID", "  example-id  ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    settings = Settings(_env_file=None)
    assert type(build_default_model(settings)) is Claude
    assert build_default_model(settings).id == "example-id"
    assert "api_key" not in settings.model_dump()


def test_blank_model_id_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DEFAULT_MODEL_ID", "   ")

    assert Settings(_env_file=None).default_model_id is None
