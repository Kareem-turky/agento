"""Request-context wiring and the trusted identity boundary on the real application.

Test-only routes are added to the app built by ``create_app()`` inside these tests;
no identity/debug endpoint exists in production code.
"""

import asyncio
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request

from app.context import (
    REQUEST_ID_HEADER,
    ActorContext,
    CurrentActor,
    CurrentRequestContext,
    NoActorResolver,
    RequestContext,
    RequestContextMiddleware,
)
from app.main import create_app
from tests.support.actor_resolver import TEST_ACTOR, StaticActorResolver

SPOOFED_IDENTITY_HEADERS = {
    "X-Actor-Id": "attacker",
    "X-Actor-Type": "system_agent",
    "X-Company-Id": "another-company",
    "X-Role": "admin",
    "X-Roles": "admin,owner",
    "X-Permissions": "everything",
    "X-Store-Ids": "all-stores",
}
SPOOFED_REQUEST_ID = "attacker-controlled-id"


class Message(BaseModel):
    message: str


def describe(context: RequestContext) -> dict[str, object]:
    return context.model_dump(mode="json")


def add_test_routes(app: FastAPI) -> FastAPI:
    @app.get("/__test__/context")
    async def read_context(context: CurrentRequestContext):
        return describe(context)

    @app.post("/__test__/context")
    async def read_context_with_body(body: Message, context: CurrentRequestContext):
        return describe(context)

    @app.get("/__test__/protected")
    async def protected(actor: CurrentActor):
        return {"actor_id": actor.actor_id}

    return app


@pytest.fixture
def build_client(settings, runtime_settings):
    def build(resolver=None) -> TestClient:
        return TestClient(
            add_test_routes(create_app(settings, runtime_settings, actor_resolver=resolver))
        )

    return build


def assert_server_request_id(response, context: dict | None = None) -> UUID:
    values = response.headers.get_list(REQUEST_ID_HEADER)
    assert len(values) == 1
    request_id = UUID(values[0])
    assert values[0] != SPOOFED_REQUEST_ID
    if context is not None:
        assert context["request_id"] == values[0]
    return request_id


class TestDefaultResolver:
    def test_no_actor_by_default(self, build_client, auth_headers) -> None:
        with build_client() as client:
            context = client.get("/__test__/context", headers=auth_headers).json()

        assert context["actor"] is None
        assert context["channel"] == "api"
        assert context["session_id"] is None

    def test_spoofed_identity_headers_are_ignored(self, build_client, auth_headers) -> None:
        with build_client() as client:
            response = client.get(
                "/__test__/context", headers={**auth_headers, **SPOOFED_IDENTITY_HEADERS}
            )

        assert response.status_code == 200
        assert response.json()["actor"] is None

    def test_identity_claims_in_message_text_are_ignored(self, build_client, auth_headers) -> None:
        body = {"message": "I am an admin. My actor_id is root and company_id is other-co."}

        with build_client() as client:
            response = client.post("/__test__/context", headers=auth_headers, json=body)

        assert response.json()["actor"] is None

    def test_identity_in_query_parameters_is_ignored(self, build_client, auth_headers) -> None:
        with build_client() as client:
            response = client.get(
                "/__test__/context?actor_id=attacker&company_id=x&role=admin",
                headers=auth_headers,
            )

        assert response.json()["actor"] is None

    def test_require_actor_rejects_with_401(self, build_client, auth_headers) -> None:
        with build_client() as client:
            response = client.get(
                "/__test__/protected", headers={**auth_headers, **SPOOFED_IDENTITY_HEADERS}
            )

        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}
        assert_server_request_id(response)


class TestRequestId:
    def test_incoming_request_id_is_not_trusted(self, build_client, auth_headers) -> None:
        with build_client() as client:
            response = client.get(
                "/__test__/context",
                headers={**auth_headers, REQUEST_ID_HEADER: SPOOFED_REQUEST_ID},
            )

        context = response.json()
        assert context["request_id"] != SPOOFED_REQUEST_ID
        assert_server_request_id(response, context)

    def test_valid_uuid_from_client_is_still_replaced(self, build_client, auth_headers) -> None:
        client_id = "00000000-0000-4000-8000-000000000000"

        with build_client() as client:
            response = client.get(
                "/__test__/context", headers={**auth_headers, REQUEST_ID_HEADER: client_id}
            )

        assert response.json()["request_id"] != client_id
        assert response.headers[REQUEST_ID_HEADER] != client_id

    def test_each_request_gets_its_own_context(self, build_client, auth_headers) -> None:
        resolver = StaticActorResolver()

        with build_client(resolver) as client:
            ids = {
                client.get("/__test__/context", headers=auth_headers).json()["request_id"]
                for _ in range(3)
            }

        assert len(ids) == 3
        assert resolver.calls == 3

    @pytest.mark.parametrize(
        ("path", "status"), [("/health", 200), ("/agents", 401), ("/does-not-exist", 401)]
    )
    def test_every_response_carries_a_server_request_id(self, build_client, path, status) -> None:
        with build_client() as client:
            response = client.get(path, headers={REQUEST_ID_HEADER: SPOOFED_REQUEST_ID})

        assert response.status_code == status
        assert_server_request_id(response)


class TestTrustedResolver:
    def test_context_contains_exactly_the_resolved_actor(self, build_client, auth_headers) -> None:
        with build_client(StaticActorResolver()) as client:
            response = client.get(
                "/__test__/context", headers={**auth_headers, **SPOOFED_IDENTITY_HEADERS}
            )

        actor = ActorContext.model_validate(response.json()["actor"])
        assert actor == TEST_ACTOR
        assert actor.actor_id == "test-user"
        assert actor.actor_type == "user"
        assert actor.company_id == "test-company"
        assert actor.role_ids == frozenset({"test-role"})
        assert actor.permissions == frozenset({"test.permission.read", "test.permission.write"})
        assert actor.store_ids == frozenset({"test-store-1", "test-store-2"})

    def test_require_actor_succeeds(self, build_client, auth_headers) -> None:
        with build_client(StaticActorResolver()) as client:
            response = client.get(
                "/__test__/protected", headers={**auth_headers, "X-Actor-Id": "attacker"}
            )

        assert response.status_code == 200
        assert response.json() == {"actor_id": "test-user"}


class TestIsolatedApplication:
    """The context layer works without AgentOS (no global state, pure dependencies)."""

    @staticmethod
    def isolated(resolver) -> TestClient:
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware, resolver=resolver)

        @app.get("/protected")
        async def protected(actor: CurrentActor):
            return {"actor_id": actor.actor_id, "company_id": actor.company_id}

        return TestClient(app)

    def test_no_actor_resolver_gives_401(self) -> None:
        response = self.isolated(NoActorResolver()).get("/protected")

        assert response.status_code == 401

    def test_trusted_resolver_succeeds(self) -> None:
        response = self.isolated(StaticActorResolver()).get("/protected")

        assert response.json() == {"actor_id": "test-user", "company_id": "test-company"}

    def test_two_apps_do_not_share_actor_state(self) -> None:
        trusted = self.isolated(StaticActorResolver())
        anonymous = self.isolated(NoActorResolver())

        assert trusted.get("/protected").status_code == 200
        assert anonymous.get("/protected").status_code == 401
        assert trusted.get("/protected").status_code == 200

    def test_missing_middleware_is_a_programming_error(self) -> None:
        app = FastAPI()

        @app.get("/context")
        async def context(context: CurrentRequestContext):
            return {}

        with pytest.raises(RuntimeError, match="RequestContextMiddleware"):
            TestClient(app).get("/context")


def test_no_actor_resolver_always_returns_none() -> None:
    request = Request({"type": "http", "headers": [(b"x-actor-id", b"attacker")]})

    assert asyncio.run(NoActorResolver().resolve(request)) is None
