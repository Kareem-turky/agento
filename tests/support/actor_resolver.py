"""TEST-ONLY trusted actor resolver. Never used by production code.

Returns a fixed ``ActorContext`` regardless of the request, standing in for a
future authentication-backed resolver. It deliberately ignores request headers.
"""

from starlette.requests import Request

from app.context import ActorContext

TEST_ACTOR = ActorContext(
    actor_id="test-user",
    actor_type="user",
    company_id="test-company",
    role_ids=frozenset({"test-role"}),
    permissions=frozenset({"test.permission.read", "test.permission.write"}),
    store_ids=frozenset({"test-store-1", "test-store-2"}),
)


class StaticActorResolver:
    def __init__(self, actor: ActorContext = TEST_ACTOR) -> None:
        self.actor = actor
        self.calls = 0

    async def resolve(self, request: Request) -> ActorContext | None:
        self.calls += 1
        return self.actor
