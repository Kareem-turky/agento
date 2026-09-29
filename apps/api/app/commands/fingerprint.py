"""Deterministic one-way hashes: the idempotency key hash and the request fingerprint.

Only these SHA-256 hex digests are ever persisted. The canonical JSON is built in
memory and discarded; it is never stored or logged.

Canonical JSON: object keys sorted, separators ``,`` and ``:``, UTF-8, no NaN or
infinity. Only plain JSON values are accepted (``None``, ``bool``, ``int``, finite
``float``, ``str``, lists and string-keyed mappings); anything else (tuples, sets,
bytes, non-string keys, arbitrary objects) is rejected rather than coerced, so two
different requests can never share a canonical form by accident.
"""

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from app.commands.errors import InvalidCommandParametersError

_MAX_DEPTH = 32


def _plain_json(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        raise InvalidCommandParametersError()
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidCommandParametersError()
        return value
    if isinstance(value, list):
        return [_plain_json(item, depth + 1) for item in value]
    if isinstance(value, Mapping):
        plain: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidCommandParametersError()
            plain[key] = _plain_json(item, depth + 1)
        return plain
    raise InvalidCommandParametersError()


def canonical_json(value: Any) -> bytes:
    """UTF-8 canonical JSON of a plain JSON value (raises for anything else)."""
    try:
        text = json.dumps(
            _plain_json(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except InvalidCommandParametersError:
        raise
    except (TypeError, ValueError, RecursionError):
        raise InvalidCommandParametersError() from None
    return text.encode("utf-8")


def hash_idempotency_key(key: str) -> str:
    """SHA-256 hex of the exact key (case-sensitive, never normalized)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def request_fingerprint(
    *,
    action_name: str,
    company_id: str,
    store_id: str | None,
    parameters: Mapping[str, Any],
) -> str:
    """SHA-256 hex of the canonical logical command envelope."""
    if not isinstance(parameters, Mapping):
        raise InvalidCommandParametersError()
    envelope = {
        "action_name": action_name,
        "company_id": company_id,
        "store_id": store_id,
        "parameters": parameters,
    }
    return hashlib.sha256(canonical_json(envelope)).hexdigest()
