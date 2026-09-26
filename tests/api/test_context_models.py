"""ActorContext and RequestContext are immutable, strict and FastAPI-independent."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.context import ActorContext, RequestContext
from tests.support.actor_resolver import TEST_ACTOR

VALID = {"actor_id": "a-1", "actor_type": "user", "company_id": "c-1"}


class TestActorContext:
    def test_minimal_actor_has_empty_immutable_scopes(self) -> None:
        actor = ActorContext(**VALID)

        assert actor.role_ids == frozenset()
        assert actor.permissions == frozenset()
        assert actor.store_ids == frozenset()

    def test_is_immutable(self) -> None:
        with pytest.raises(ValidationError):
            TEST_ACTOR.actor_id = "someone-else"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            TEST_ACTOR.company_id = "another-company"  # type: ignore[misc]

    @pytest.mark.parametrize("field", ["actor_id", "company_id"])
    @pytest.mark.parametrize("value", ["", "   "])
    def test_rejects_blank_ids(self, field, value) -> None:
        with pytest.raises(ValidationError):
            ActorContext(**{**VALID, field: value})

    @pytest.mark.parametrize("field", ["role_ids", "permissions", "store_ids"])
    def test_rejects_blank_scope_entries(self, field) -> None:
        with pytest.raises(ValidationError):
            ActorContext(**VALID, **{field: {"ok", " "}})

    @pytest.mark.parametrize("actor_type", ["admin", "manager", "tenant", "", "USER"])
    def test_rejects_unsupported_actor_type(self, actor_type) -> None:
        with pytest.raises(ValidationError):
            ActorContext(**{**VALID, "actor_type": actor_type})

    @pytest.mark.parametrize("actor_type", ["user", "api_client", "system_agent"])
    def test_accepts_supported_actor_types(self, actor_type) -> None:
        assert ActorContext(**{**VALID, "actor_type": actor_type}).actor_type == actor_type

    @pytest.mark.parametrize(
        "extra", [{"allowed_actions": ["refund"]}, {"tenant_id": "t"}, {"password": "x"}]
    )
    def test_rejects_unexpected_fields(self, extra) -> None:
        with pytest.raises(ValidationError):
            ActorContext(**VALID, **extra)

    @pytest.mark.parametrize("field", ["role_ids", "permissions", "store_ids"])
    def test_scopes_cannot_be_mutated(self, field) -> None:
        value = getattr(TEST_ACTOR, field)

        assert isinstance(value, frozenset)
        with pytest.raises(AttributeError):
            value.add("injected")  # type: ignore[attr-defined]
        with pytest.raises(ValidationError):
            setattr(TEST_ACTOR, field, frozenset({"injected"}))

    def test_scopes_accept_iterables_and_become_frozensets(self) -> None:
        actor = ActorContext(**VALID, role_ids=["r1", "r1", "r2"], store_ids=("s1",))

        assert actor.role_ids == frozenset({"r1", "r2"})
        assert actor.store_ids == frozenset({"s1"})

    def test_has_no_allowed_actions_or_tenant_fields(self) -> None:
        fields = set(ActorContext.model_fields)

        assert fields == {
            "actor_id",
            "actor_type",
            "company_id",
            "role_ids",
            "permissions",
            "store_ids",
        }


class TestRequestContext:
    def test_generates_a_request_id(self) -> None:
        first, second = RequestContext(), RequestContext()

        assert isinstance(first.request_id, UUID)
        assert first.request_id != second.request_id

    def test_defaults(self) -> None:
        context = RequestContext()

        assert context.actor is None
        assert context.channel == "api"
        assert context.session_id is None

    def test_is_immutable(self) -> None:
        context = RequestContext()

        with pytest.raises(ValidationError):
            context.actor = TEST_ACTOR  # type: ignore[misc]
        with pytest.raises(ValidationError):
            context.request_id = RequestContext().request_id  # type: ignore[misc]

    def test_supports_an_injected_trusted_actor(self) -> None:
        assert RequestContext(actor=TEST_ACTOR).actor is TEST_ACTOR

    @pytest.mark.parametrize("channel", ["api", "web", "whatsapp", "system"])
    def test_supported_channels(self, channel) -> None:
        assert RequestContext(channel=channel).channel == channel

    def test_rejects_unknown_channel(self) -> None:
        with pytest.raises(ValidationError):
            RequestContext(channel="email")

    def test_session_id(self) -> None:
        assert RequestContext(session_id="s-1").session_id == "s-1"
        with pytest.raises(ValidationError):
            RequestContext(session_id="  ")

    def test_rejects_non_uuid_request_id(self) -> None:
        with pytest.raises(ValidationError):
            RequestContext(request_id="attacker-controlled-id")

    def test_rejects_unexpected_fields(self) -> None:
        with pytest.raises(ValidationError):
            RequestContext(tenant_id="t")
