"""Raw Product API key format and hashing (pure; shared by the resolver and the
operator hash helper).

A raw key is opaque and case-sensitive: 32-256 printable ASCII characters with no
whitespace. It is never normalized, stored, logged or put into an exception.
"""

import hashlib
import re

MIN_API_KEY_LENGTH = 32
MAX_API_KEY_LENGTH = 256
_RAW_KEY = re.compile(rf"[\x21-\x7e]{{{MIN_API_KEY_LENGTH},{MAX_API_KEY_LENGTH}}}")


class InvalidApiKeyError(ValueError):
    """The raw key is not well formed. The message never contains the key."""

    def __init__(self) -> None:
        super().__init__("invalid Product API key format")


def is_well_formed_api_key(raw_key: object) -> bool:
    return isinstance(raw_key, str) and _RAW_KEY.fullmatch(raw_key) is not None


def hash_api_key(raw_key: str) -> str:
    """Lowercase hex SHA-256 of the exact ASCII key bytes (the configured verifier)."""
    if not is_well_formed_api_key(raw_key):
        raise InvalidApiKeyError()
    return hashlib.sha256(raw_key.encode("ascii")).hexdigest()
