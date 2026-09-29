"""Idempotency key validation and deterministic one-way hashing."""

import hashlib
import subprocess
import sys
from types import MappingProxyType

import pytest
from pydantic import TypeAdapter, ValidationError

from app.commands import (
    IdempotencyKey,
    InvalidCommandParametersError,
    canonical_json,
    hash_idempotency_key,
    request_fingerprint,
)

KEY = TypeAdapter(IdempotencyKey)
BASE = {
    "action_name": "operations.ticket.create",
    "company_id": "company-1",
    "store_id": "store-a",
    "parameters": {"title": "Parcel delayed", "description": "Courier missed scan"},
}


def fp(**overrides) -> str:
    return request_fingerprint(**(BASE | overrides))


def test_fingerprint_is_sha256_hex_of_the_canonical_envelope() -> None:
    expected = hashlib.sha256(
        b'{"action_name":"operations.ticket.create","company_id":"company-1",'
        b'"parameters":{"description":"Courier missed scan","title":"Parcel delayed"},'
        b'"store_id":"store-a"}'
    ).hexdigest()
    assert fp() == expected


def test_same_logical_mapping_in_any_order_gives_the_same_fingerprint() -> None:
    reordered = {"description": "Courier missed scan", "title": "Parcel delayed"}
    assert fp(parameters=reordered) == fp()
    assert fp(parameters=MappingProxyType(reordered)) == fp()


def test_nested_mapping_order_is_deterministic() -> None:
    a = {"outer": {"b": [1, {"y": 2, "x": 1}], "a": None}}
    b = {"outer": {"a": None, "b": [1, {"x": 1, "y": 2}]}}
    assert fp(parameters=a) == fp(parameters=b)
    # List order is meaningful.
    assert fp(parameters={"l": [1, 2]}) != fp(parameters={"l": [2, 1]})


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_name": "operations.ticket.close"},
        {"store_id": "store-b"},
        {"store_id": None},
        {"company_id": "company-2"},
        {"parameters": {"title": "Parcel delayed", "description": "Courier missed scan!"}},
        {"parameters": {"title": "parcel delayed", "description": "Courier missed scan"}},
        {"parameters": {"title": "Parcel delayed"}},
    ],
)
def test_any_envelope_change_changes_the_fingerprint(overrides) -> None:
    assert fp(**overrides) != fp()


def test_type_distinctions_are_kept() -> None:
    assert fp(parameters={"v": 1}) != fp(parameters={"v": "1"})
    assert fp(parameters={"v": 1}) != fp(parameters={"v": True})
    assert fp(parameters={"v": None}) != fp(parameters={})


def test_unicode_is_utf8_not_escaped() -> None:
    assert canonical_json({"t": "é"}) == '{"t":"é"}'.encode()


@pytest.mark.parametrize(
    "bad",
    [
        {"v": float("nan")},
        {"v": float("inf")},
        {"v": (1, 2)},
        {"v": {1, 2}},
        {"v": b"bytes"},
        {"v": object()},
        {1: "int key"},
        {"v": {2: "nested int key"}},
    ],
)
def test_non_json_parameters_are_rejected_not_coerced(bad) -> None:
    with pytest.raises(InvalidCommandParametersError) as info:
        fp(parameters=bad)
    assert str(info.value) == "parameters_not_canonical"


def test_non_mapping_parameters_are_rejected() -> None:
    with pytest.raises(InvalidCommandParametersError):
        fp(parameters=[1, 2])


def test_deeply_nested_parameters_are_rejected() -> None:
    value: dict = {}
    for _ in range(50):
        value = {"n": value}
    with pytest.raises(InvalidCommandParametersError):
        fp(parameters=value)


def test_fingerprint_is_stable_across_processes() -> None:
    # No PYTHONHASHSEED / process-hash dependence.
    code = (
        "from app.commands import request_fingerprint as f;"
        "print(f(action_name='a.b', company_id='c', store_id=None,"
        " parameters={'z': 1, 'a': [1, {'q': 'é'}]}))"
    )
    outputs = {
        subprocess.run(  # noqa: S603
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True, env={"PYTHONHASHSEED": seed},
            cwd="apps/api",
        ).stdout.strip()
        for seed in ("1", "2", "3")
    }  # fmt: skip
    assert len(outputs) == 1
    assert outputs == {
        request_fingerprint(
            action_name="a.b",
            company_id="c",
            store_id=None,
            parameters={"a": [1, {"q": "é"}], "z": 1},
        )
    }


def test_raw_values_never_appear_in_the_hashes() -> None:
    marker = "SENSITIVE-TITLE-MARKER-123"
    digest = fp(parameters={"title": marker})
    key_hash = hash_idempotency_key("SENSITIVE-KEY-MARKER")
    assert marker not in digest and "SENSITIVE" not in key_hash
    assert len(digest) == len(key_hash) == 64
    assert set(digest + key_hash) <= set("0123456789abcdef")


def test_key_hash_is_sha256_of_the_exact_key_and_case_sensitive() -> None:
    assert hash_idempotency_key("Key-1") == hashlib.sha256(b"Key-1").hexdigest()
    assert hash_idempotency_key("Key-1") != hash_idempotency_key("key-1")
    assert hash_idempotency_key("Key-1") == hash_idempotency_key("Key-1")


@pytest.mark.parametrize(
    "key",
    [
        "3f1c3b9e-8c1e-4c7a-9b2d-6f0e1a2b3c4d",
        "3F1C3B9E-8C1E-4C7A-9B2D-6F0E1A2B3C4D",
        "01J8ZQ4Y7XK3P2W9R5T6V8B0NM",
        "a",
        "order:123.retry_2~x",
        "k" * 128,
    ],
)
def test_valid_keys_are_accepted_unchanged(key) -> None:
    assert KEY.validate_python(key) == key


@pytest.mark.parametrize(
    "key",
    ["", " ", " key", "key ", "k" * 129, "key with space", "key\n", "ключ", "a/b", "a=b",
     "a,b", "a;b", 'a"b', 123, None, b"bytes"],
)  # fmt: skip
def test_invalid_keys_are_rejected(key) -> None:
    with pytest.raises(ValidationError):
        KEY.validate_python(key)
