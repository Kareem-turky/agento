"""Integration definitions: trusted, static metadata about an integration TYPE installed in
this Product build (never data from a request, a provider or a database).

    IntegrationDefinition
      integration_id  stable Product-owned id ("example-shop"), never a module or URL
      category        classification only (commerce, messaging, ...); NOT a permission
      auth_mode       how a connection is onboarded (none / credentials / delegated)
      fields          the connection's configuration fields (text, url, boolean, secret)
      capabilities    declared, stable capability ids ("orders.read"); NOT authorization

A definition only describes connection MANAGEMENT. Business operations stay in the
domain contracts (``CommerceIntegration`` and, later, messaging/marketing/... contracts):
a provider adapter may implement several of them, and none of them lives here.

Secret fields are declared by NAME only; their values never appear in a definition, a
connection model, PostgreSQL, a response, an error or a log (see ``secrets``).
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# Stable Product-owned identifiers (lowercase, no dots/slashes: never a module or path).
IntegrationId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
]
FieldName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
# Dotted, lowercase capability ids such as "orders.read" or "messages.send".
CapabilityId = Annotated[
    str,
    StringConstraints(
        strict=True, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=128
    ),
]
_Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]

MAX_FIELDS = 32
MAX_FIELD_LENGTH = 2048
# Defense in depth: a NON-secret field must never look like a credential.
SECRET_LIKE_NAMES = (
    "password", "passwd", "secret", "token", "apikey", "api_key", "credential",
    "authorization", "private_key", "cookie",
)  # fmt: skip

ConfigValue = str | bool


class IntegrationCategory(StrEnum):
    """Classification of an integration (catalog grouping). Never a permission."""

    COMMERCE = "commerce"
    MESSAGING = "messaging"
    MARKETING = "marketing"
    SHIPPING = "shipping"
    ACCOUNTING = "accounting"


class IntegrationAuthMode(StrEnum):
    """How a connection is onboarded.

    ``DELEGATED`` (provider-hosted delegated authorization, e.g. an authorization-code
    flow) can be DECLARED but is not supported by this build: such flows differ per
    provider and are added deliberately with a reviewed provider. Connections to a
    delegated definition are refused (fail closed), never faked.
    """

    NONE = "none"
    CREDENTIALS = "credentials"
    DELEGATED = "delegated"


class ConfigFieldKind(StrEnum):
    TEXT = "text"
    URL = "url"
    BOOLEAN = "boolean"
    SECRET = "secret"  # noqa: S105 - a field kind, not a credential


class ConnectionConfigError(ValueError):
    """Invalid connection configuration or credentials. ``field`` is a trusted,
    definition-declared field name or None; the submitted VALUE is never included."""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(f"invalid connection configuration: {code}")
        self.code = code
        self.field = field


def looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in SECRET_LIKE_NAMES)


class ConfigField(BaseModel):
    model_config = _FROZEN

    name: FieldName
    label: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
    kind: ConfigFieldKind
    required: bool = True
    help_text: Annotated[str, StringConstraints(strict=True, max_length=300)] | None = None
    max_length: int = Field(default=256, ge=1, le=MAX_FIELD_LENGTH)

    @model_validator(mode="after")
    def _non_secret_fields_do_not_look_secret(self) -> Self:
        if self.kind is not ConfigFieldKind.SECRET and looks_secret(self.name):
            raise ValueError("a non-secret field must not be named like a credential")
        return self


def _url(value: str) -> bool:
    """A syntactically valid https URL with a host and no embedded credentials. Only a
    metadata check: nothing is fetched (there is no generic URL tester)."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (parts.scheme == "https" and bool(parts.hostname) and parts.username is None
            and parts.password is None and not parts.fragment)  # fmt: skip


class IntegrationDefinition(BaseModel):
    model_config = _FROZEN

    integration_id: IntegrationId
    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
    category: IntegrationCategory
    description: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]
    auth_mode: IntegrationAuthMode
    fields: tuple[ConfigField, ...] = ()
    capabilities: frozenset[CapabilityId] = frozenset()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field name")
        if len(names) > MAX_FIELDS:
            raise ValueError("too many fields")
        secret = [f for f in self.fields if f.kind is ConfigFieldKind.SECRET]
        if self.auth_mode is IntegrationAuthMode.CREDENTIALS and not secret:
            raise ValueError("a credentials integration declares at least one secret field")
        if self.auth_mode is not IntegrationAuthMode.CREDENTIALS and secret:
            raise ValueError("only a credentials integration declares secret fields")
        return self

    @property
    def connectable(self) -> bool:
        """Whether this build can create a connection for it (delegated authorization is
        not supported yet)."""
        return self.auth_mode is not IntegrationAuthMode.DELEGATED

    @property
    def secret_field_names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields if f.kind is ConfigFieldKind.SECRET)

    def validate_config(self, raw: Mapping[str, object]) -> dict[str, ConfigValue]:
        """The NON-secret configuration, validated strictly against this definition."""
        fields = {f.name: f for f in self.fields if f.kind is not ConfigFieldKind.SECRET}
        unknown = [key for key in raw if key not in fields]
        if unknown:
            # Unknown keys are never echoed (they are client-supplied text).
            raise ConnectionConfigError("unknown_field")
        config: dict[str, ConfigValue] = {}
        for name, field in fields.items():
            if name not in raw:
                if field.required:
                    raise ConnectionConfigError("required", name)
                continue
            value = raw[name]
            if field.kind is ConfigFieldKind.BOOLEAN:
                if not isinstance(value, bool):
                    raise ConnectionConfigError("expected_boolean", name)
                config[name] = value
                continue
            if not isinstance(value, str):
                raise ConnectionConfigError("expected_text", name)
            text = value.strip()
            if not text:
                if field.required:
                    raise ConnectionConfigError("required", name)
                continue
            if len(text) > field.max_length:
                raise ConnectionConfigError("too_long", name)
            if field.kind is ConfigFieldKind.URL and not _url(text):
                raise ConnectionConfigError("invalid_url", name)
            config[name] = text
        return config

    def validate_secrets(self, raw: Mapping[str, object]) -> dict[str, SecretStr]:
        """The COMPLETE secret set for a connection (create or explicit replacement).

        Every required secret field must be present: a replacement is a whole new set,
        never a partial merge. Values are never included in an error."""
        fields = {f.name: f for f in self.fields if f.kind is ConfigFieldKind.SECRET}
        if any(key not in fields for key in raw):
            raise ConnectionConfigError("unknown_secret_field")
        secrets: dict[str, SecretStr] = {}
        for name, field in fields.items():
            value = raw.get(name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if value is None or value == "":
                if field.required:
                    raise ConnectionConfigError("required", name)
                continue
            if not isinstance(value, str):
                raise ConnectionConfigError("expected_text", name)
            if len(value) > field.max_length or value != value.strip():
                raise ConnectionConfigError("invalid_secret", name)
            secrets[name] = SecretStr(value)
        return secrets
