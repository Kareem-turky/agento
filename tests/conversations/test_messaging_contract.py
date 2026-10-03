"""Messaging integration contract and static registry (Task 037): provider-independent,
canonical-only, empty in production, and consistent with the Integration Catalog."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.integration_management import build_default_integration_catalog
from app.integrations.messaging import (
    MESSAGING_CAPABILITIES,
    MessagingIntegration,
    MessagingIntegrationRegistry,
    MessagingRegistryError,
    OutboundMessageRequest,
    build_default_messaging_registry,
)
from tests.support.conversation_fakes import CHAT, FakeMessagingAdapter, chat_catalog
from tests.support.integration_fakes import COMMERCE, MESSAGING


def test_capabilities_are_the_dotted_messaging_vocabulary() -> None:
    assert MESSAGING_CAPABILITIES == {"messages.receive", "messages.send", "messages.delivery"}
    assert MESSAGING_CAPABILITIES <= CHAT.capabilities  # valid IntegrationDefinition ids


def test_production_registry_and_catalog_are_empty() -> None:
    catalog = build_default_integration_catalog()
    assert len(catalog) == 0
    registry = build_default_messaging_registry(catalog)
    assert len(registry) == 0 and registry.integration_ids == frozenset()


def test_registry_binds_only_installed_messaging_integrations_with_the_same_id() -> None:
    catalog = chat_catalog()
    adapter = FakeMessagingAdapter()
    assert isinstance(adapter, MessagingIntegration)
    registry = MessagingIntegrationRegistry([adapter], catalog=catalog)
    assert registry.get(CHAT.integration_id) is adapter and registry.get("missing") is None
    with pytest.raises(AttributeError):
        registry._by_id = {}  # type: ignore[misc]
    cases = [
        FakeMessagingAdapter("not-installed"),  # not in the catalog
        FakeMessagingAdapter(COMMERCE.integration_id),  # not a messaging integration
        FakeMessagingAdapter(MESSAGING.integration_id,
                             frozenset({"messages.delivery"})),  # undeclared capability
        FakeMessagingAdapter(capabilities=frozenset({"orders.read"})),  # not messaging
    ]  # fmt: skip
    for bad in cases:
        with pytest.raises(MessagingRegistryError):
            MessagingIntegrationRegistry([bad], catalog=catalog)
    with pytest.raises(MessagingRegistryError):
        MessagingIntegrationRegistry([adapter, FakeMessagingAdapter()], catalog=catalog)
    with pytest.raises(TypeError):
        MessagingIntegrationRegistry([object()], catalog=catalog)  # type: ignore[list-item]


def test_outbound_contract_is_canonical_text_only() -> None:
    request = OutboundMessageRequest(connection_id=uuid4(), external_conversation_ref="t-1",
                                     message_id=uuid4(), text="Hello")  # fmt: skip
    result = asyncio.run(FakeMessagingAdapter().send_message(request))
    assert result.status.value == "accepted" and result.external_message_ref == "ext-1"
    fields = set(OutboundMessageRequest.model_fields)
    assert fields == {"connection_id", "external_conversation_ref", "message_id", "text"}
    for bad in ({"text": "x" * 16_001}, {"text": " "}, {"payload": {}},
                {"external_conversation_ref": "has space"}):  # fmt: skip
        with pytest.raises(ValidationError):
            OutboundMessageRequest.model_validate({**request.model_dump(), **bad})
