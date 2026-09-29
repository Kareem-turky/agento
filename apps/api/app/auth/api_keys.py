"""``ProductApiKeyActorResolver``: ``Authorization: Bearer <Product API key>`` -> actor.

    Authorization header (exactly one, scheme "Bearer" case-insensitive, one token)
    -> raw key well formed (32-256 printable ASCII, no whitespace, case-sensitive)
    -> SHA-256 hex
    -> hmac.compare_digest against EVERY configured hash (no early exit)
    -> exactly one match -> ActorContext(actor_type="api_client",
                                          company_id=<deployment company>, ...)
    -> anything else -> None (the route answers the usual 401; no reason is given)

The raw key never leaves ``resolve``: it is not stored, logged, raised, attached to
the request or passed on. Only ``ActorContext`` flows downstream. The resolver never
decides authorization: permissions, policy and store scope stay authoritative
elsewhere. This is Product authentication only; the AgentOS ``OS_SECURITY_KEY`` is a
separate credential for a separate surface, and ``validate_credential_separation``
refuses a deployment where a configured Product key equals it.
"""

import hashlib
import hmac
import re
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.requests import Request

from app.auth.keys import hash_api_key, is_well_formed_api_key
from app.config import ProductApiKeyPrincipalConfig, Settings
from app.context.models import ActorContext
from app.context.resolver import ActorResolver, NoActorResolver

# One scheme token, one single space, one credential token. Anything else (extra
# spaces, tabs, several credentials) is ambiguous and not authenticated.
_AUTHORIZATION = re.compile(r"([A-Za-z]+) ([^ ]+)")


class ProductAuthConfigurationError(RuntimeError):
    """Product authentication is missing or unusable for this deployment."""


def bearer_token(headers: Headers) -> str | None:
    """The single well-formed Bearer token, or None (never says why)."""
    values = headers.getlist("authorization")
    if len(values) != 1:
        return None
    match = _AUTHORIZATION.fullmatch(values[0])
    if match is None or match.group(1).lower() != "bearer":
        return None
    token = match.group(2)
    return token if is_well_formed_api_key(token) else None


class ProductApiKeyActorResolver:
    """Configuration-backed, single-company Product API key resolver."""

    def __init__(self, company_id: str, principals: Iterable[ProductApiKeyPrincipalConfig]) -> None:
        principals = tuple(principals)
        if not isinstance(company_id, str) or not company_id.strip():
            raise ProductAuthConfigurationError("a deployment company_id is required")
        if not principals or not all(
            isinstance(p, ProductApiKeyPrincipalConfig) for p in principals
        ):
            raise ProductAuthConfigurationError("at least one API key principal is required")
        if len({p.key_sha256 for p in principals}) != len(principals) or len(
            {p.key_id for p in principals}
        ) != len(principals):
            raise ProductAuthConfigurationError("duplicate API key principal")
        self._company_id = company_id
        self._principals = principals

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProductApiKeyActorResolver":
        if settings.company_id is None:
            raise ProductAuthConfigurationError("a deployment company_id is required")
        return cls(settings.company_id, settings.product_api_keys)

    async def resolve(self, request: Request) -> ActorContext | None:
        token = bearer_token(request.headers)
        if token is None:
            return None
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        del token
        matched: ProductApiKeyPrincipalConfig | None = None
        for principal in self._principals:  # every hash is compared: no early exit
            if hmac.compare_digest(digest, principal.key_sha256):
                matched = principal
        if matched is None:
            return None
        return ActorContext(
            actor_id=matched.actor_id,
            actor_type="api_client",
            company_id=self._company_id,
            role_ids=matched.role_ids,
            permissions=matched.permissions,
            store_ids=matched.store_ids,
        )


CREDENTIAL_COLLISION_MESSAGE = "Product API credentials must be distinct from OS_SECURITY_KEY"


def validate_credential_separation(settings: Settings, os_security_key: str | None) -> None:
    """Refuse a configured Product API key that IS the AgentOS ``OS_SECURITY_KEY``.

    Otherwise one Bearer credential would open both the Product routes and AgentOS.
    Pure and runtime-independent: the caller passes only the OS key string. An OS key
    that could never be a Product key (format) cannot collide and is not rejected.
    The error is fixed text: no key, hash, key_id or actor_id is ever included.
    """
    if not os_security_key or not settings.product_api_keys:
        return
    candidates = {os_security_key, os_security_key.strip()}
    digests = [hash_api_key(c) for c in candidates if is_well_formed_api_key(c)]
    collision = False
    for digest in digests:
        for principal in settings.product_api_keys:  # every hash compared, constant time
            collision |= hmac.compare_digest(digest, principal.key_sha256)
    if collision:
        raise ProductAuthConfigurationError(CREDENTIAL_COLLISION_MESSAGE)


def build_actor_resolver(settings: Settings) -> ActorResolver:
    """The default Product resolver for ``settings`` (used when none is injected).

    ``api_key`` -> ``ProductApiKeyActorResolver``; ``disabled`` -> ``NoActorResolver``
    in local/test only. Staging/production never run without Product authentication.
    """
    if settings.product_auth_mode == "api_key":
        return ProductApiKeyActorResolver.from_settings(settings)
    if not settings.is_development:
        raise ProductAuthConfigurationError(
            "Product authentication cannot be disabled outside local/test"
        )
    return NoActorResolver()
