"""ProductApiKeyActorResolver: Authorization parsing, hashing, constant-time match."""

import asyncio

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from app.auth import (
    ProductApiKeyActorResolver,
    ProductAuthConfigurationError,
    bearer_token,
    build_actor_resolver,
    hash_api_key,
    is_well_formed_api_key,
)
from app.config import Settings
from app.context import ActorContext, NoActorResolver
from tests.support.product_auth import TEST_PRODUCT_KEY, principal, sha256_hex

KEY_A = "test-key-A-" + "a" * 40
KEY_B = "test-key-B-" + "b" * 40
COMPANY = "deployment-company"
PRINCIPAL_A = principal(
    KEY_A, key_id="key-a", actor_id="actor-a", role_ids=frozenset({"ops"}),
    permissions=frozenset({"orders.read", "tickets.create"}), store_ids=frozenset({"store-a"}),
)  # fmt: skip
PRINCIPAL_B = principal(
    KEY_B, key_id="key-b", actor_id="actor-b", role_ids=frozenset({"viewer"}),
    permissions=frozenset({"orders.read"}), store_ids=frozenset({"store-b"}),
)  # fmt: skip


def resolver() -> ProductApiKeyActorResolver:
    return ProductApiKeyActorResolver(COMPANY, (PRINCIPAL_A, PRINCIPAL_B))


def request(*authorization: bytes | str) -> Request:
    headers = [
        (b"authorization", v if isinstance(v, bytes) else v.encode("latin-1"))
        for v in authorization
    ]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def resolve(*authorization: bytes | str) -> ActorContext | None:
    return asyncio.run(resolver().resolve(request(*authorization)))


def test_correct_key_resolves_the_exact_configured_principal() -> None:
    actor = resolve(f"Bearer {KEY_A}")
    assert actor == ActorContext(
        actor_id="actor-a", actor_type="api_client", company_id=COMPANY,
        role_ids=frozenset({"ops"}), permissions=frozenset({"orders.read", "tickets.create"}),
        store_ids=frozenset({"store-a"}),
    )  # fmt: skip


def test_each_key_resolves_only_its_own_principal() -> None:
    a, b = resolve(f"Bearer {KEY_A}"), resolve(f"Bearer {KEY_B}")
    assert (a.actor_id, a.store_ids) == ("actor-a", frozenset({"store-a"}))
    assert (b.actor_id, b.store_ids, b.permissions) == (
        "actor-b", frozenset({"store-b"}), frozenset({"orders.read"}),
    )  # fmt: skip
    assert a.company_id == b.company_id == COMPANY  # one deployment company


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR"])
def test_bearer_scheme_is_case_insensitive(scheme) -> None:
    assert resolve(f"{scheme} {KEY_A}").actor_id == "actor-a"


@pytest.mark.parametrize(
    "authorization",
    [
        (),  # missing
        ("",),
        ("Bearer",),
        ("Bearer ",),
        (f"Basic {KEY_A}",),
        (f"Token {KEY_A}",),
        (KEY_A,),  # no scheme
        (f"Bearer  {KEY_A}",),  # two spaces
        (f" Bearer {KEY_A}",),
        (f"Bearer {KEY_A} ",),
        (f"Bearer\t{KEY_A}",),
        (f"Bearer {KEY_A} {KEY_B}",),  # multiple credentials
        (f"Bearer {KEY_A},Bearer {KEY_B}",),
        (f"Bearer {KEY_A}", f"Bearer {KEY_A}"),  # duplicate headers
        (f"Bearer {KEY_A}", f"Bearer {KEY_B}"),
        ("Bearer " + "a" * 31,),  # too short
        ("Bearer " + "a" * 257,),  # too long
        (f"Bearer {KEY_A[:-1]}é".encode(),),  # non-ASCII
        (f"Bearer {KEY_A.upper()}",),  # the token is case-sensitive
        (f"Bearer {KEY_A.lower().replace('a-', 'A-')}x",),
        ("Bearer " + "z" * 40,),  # well formed but unknown
        (f"Bearer {sha256_hex(KEY_A)}",),  # the configured hash is not a credential
    ],
)
def test_anything_else_resolves_no_actor(authorization) -> None:
    assert resolve(*authorization) is None


def test_token_case_sensitivity_is_exact() -> None:
    flipped = KEY_A.swapcase()
    assert flipped != KEY_A and resolve(f"Bearer {flipped}") is None


def test_key_id_and_raw_key_never_enter_the_actor() -> None:
    actor = resolve(f"Bearer {KEY_A}")
    dump = actor.model_dump_json() + repr(actor)
    for secret in (KEY_A, "key-a", sha256_hex(KEY_A)):
        assert secret not in dump


def test_raw_key_never_enters_the_request_state_or_context() -> None:
    from app.context.models import RequestContext

    req = request(f"Bearer {KEY_A}")
    actor = asyncio.run(resolver().resolve(req))
    context = RequestContext(actor=actor)
    assert KEY_A not in context.model_dump_json() + repr(context)
    assert KEY_A not in repr(vars(req.state)) and vars(req.state).get("_state", {}) == {}


def test_bearer_token_helper() -> None:
    assert bearer_token(Headers({"authorization": f"Bearer {KEY_A}"})) == KEY_A
    assert bearer_token(Headers({"authorization": f"Basic {KEY_A}"})) is None
    assert bearer_token(Headers({})) is None


def test_hashing_and_key_format() -> None:
    assert hash_api_key(KEY_A) == sha256_hex(KEY_A)
    assert len(hash_api_key(KEY_A)) == 64 and hash_api_key(KEY_A) == hash_api_key(KEY_A).lower()
    for good in ("a" * 32, "~" * 256, "Aa0!" * 8):
        assert is_well_formed_api_key(good)
    for bad in ("a" * 31, "a" * 257, "a" * 31 + " ", "a" * 31 + "é", "", None, 12345):
        assert not is_well_formed_api_key(bad)


@pytest.mark.parametrize(
    ("company", "principals"),
    [("", (PRINCIPAL_A,)), ("   ", (PRINCIPAL_A,)), (COMPANY, ()),
     (COMPANY, (PRINCIPAL_A, PRINCIPAL_A)), (COMPANY, ({"key_sha256": "x"},))],
)  # fmt: skip
def test_resolver_refuses_unusable_configuration(company, principals) -> None:
    with pytest.raises(ProductAuthConfigurationError):
        ProductApiKeyActorResolver(company, principals)


def test_build_actor_resolver() -> None:
    dev = Settings(_env_file=None, environment="test")
    assert isinstance(build_actor_resolver(dev), NoActorResolver)
    configured = Settings(_env_file=None, environment="production", product_auth_mode="api_key",
                          company_id=COMPANY, product_api_keys=(principal(),))  # fmt: skip
    built = build_actor_resolver(configured)
    assert isinstance(built, ProductApiKeyActorResolver)
    actor = asyncio.run(built.resolve(request(f"Bearer {TEST_PRODUCT_KEY}")))
    assert actor is not None and actor.company_id == COMPANY
    # Deployment settings that bypassed validation still fail closed at build time.
    for environment in ("staging", "production"):
        with pytest.raises(ProductAuthConfigurationError):
            build_actor_resolver(dev.model_copy(update={"environment": environment}))
    with pytest.raises(ProductAuthConfigurationError):
        build_actor_resolver(configured.model_copy(update={"company_id": None}))
    with pytest.raises(ProductAuthConfigurationError):
        build_actor_resolver(configured.model_copy(update={"product_api_keys": ()}))


def test_every_configured_hash_is_compared(monkeypatch: pytest.MonkeyPatch) -> None:
    import hmac

    from app.auth import api_keys

    calls: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(api_keys.hmac, "compare_digest", spy)
    for key in (KEY_A, KEY_B, "z" * 40):
        calls.clear()
        asyncio.run(resolver().resolve(request(f"Bearer {key}")))
        # No early exit: the first and the last key cost the same comparisons.
        assert [b for _, b in calls] == [PRINCIPAL_A.key_sha256, PRINCIPAL_B.key_sha256]
        assert {a for a, _ in calls} == {sha256_hex(key)}  # never the raw token
