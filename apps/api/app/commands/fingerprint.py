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
    """A NEW, detached plain-JSON tree built from ``value`` (every node read once).

    Containers are rebuilt (never shared with the caller) and scalar subclasses are
    reduced to their exact built-in type through the base-class methods, so no
    caller-defined ``__str__``/``__int__``/``__float__`` runs and nothing mutable of the
    caller's survives in the result.
    """
    if depth > _MAX_DEPTH:
        raise InvalidCommandParametersError()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        return int.__int__(value)
    if isinstance(value, float):
        number = float.__float__(value)
        if not math.isfinite(number):
            raise InvalidCommandParametersError()
        return number
    if isinstance(value, list):
        return [_plain_json(item, depth + 1) for item in list.__iter__(value)]
    if isinstance(value, Mapping):
        plain: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidCommandParametersError()
            key = str.__str__(key)
            if key in plain:
                raise InvalidCommandParametersError()  # a hostile mapping repeating a key
            plain[key] = _plain_json(item, depth + 1)
        return plain
    raise InvalidCommandParametersError()


def snapshot_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Validate untrusted parameters and return ONE detached plain-JSON snapshot.

    The caller's mapping is read exactly once here and never again: the snapshot (not
    the original) is what gets fingerprinted and executed, so a caller mutating its
    mapping (or a mapping whose values change between reads) cannot make the executed
    request differ from the fingerprinted one. Never persisted.
    """
    if not isinstance(parameters, Mapping):
        raise InvalidCommandParametersError()
    try:
        return _plain_json(parameters)
    except InvalidCommandParametersError:
        raise
    except (TypeError, ValueError, RecursionError, KeyError):
        raise InvalidCommandParametersError() from None


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
