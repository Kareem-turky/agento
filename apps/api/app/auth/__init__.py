"""Product API authentication: request credentials -> trusted ``ActorContext``.

Identity only. Authorization (permissions, policy, store scope) stays in the
governance and route layers. Depends on the standard library, Pydantic, Starlette's
``Request``, ``app.config`` and ``app.context``: nothing else.
"""

from app.auth.api_keys import (
    ProductApiKeyActorResolver,
    ProductAuthConfigurationError,
    bearer_token,
    build_actor_resolver,
    validate_credential_separation,
)
from app.auth.keys import (
    MAX_API_KEY_LENGTH,
    MIN_API_KEY_LENGTH,
    InvalidApiKeyError,
    hash_api_key,
    is_well_formed_api_key,
)

__all__ = [
    "MAX_API_KEY_LENGTH",
    "MIN_API_KEY_LENGTH",
    "InvalidApiKeyError",
    "ProductApiKeyActorResolver",
    "ProductAuthConfigurationError",
    "bearer_token",
    "build_actor_resolver",
    "hash_api_key",
    "is_well_formed_api_key",
    "validate_credential_separation",
]
