"""Entities: immutability, strictness, UUID identity and relationship field types."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.commerce.domain import (
    Company,
    Customer,
    ExternalReference,
    InventoryLevel,
    Order,
    OrderItem,
    Product,
    ProductStatus,
    Shipment,
    ShipmentStatus,
    Store,
    Variant,
    Warehouse,
)
from tests.commerce.factories import order

REF = ExternalReference(system="source-a", external_id="ext-1")


def build_all():
    company = Company(name="Test Company", external_refs={REF})
    store = Store(company_id=company.id, name="Main store", currency="usd", timezone="UTC")
    product = Product(store_id=store.id, title="Widget", status=ProductStatus.ACTIVE)
    return {
        "company": company,
        "store": store,
        "customer": Customer(store_id=store.id, name="Test Customer"),
        "product": product,
        "variant": Variant(product_id=product.id, sku="W-1"),
        "warehouse": Warehouse(company_id=company.id, name="Main warehouse"),
        "inventory": InventoryLevel(variant_id=uuid4(), warehouse_id=uuid4(), available=Decimal(3)),
        "order": order(),
        "shipment": Shipment(order_id=uuid4(), status=ShipmentStatus.PENDING),
    }


@pytest.mark.parametrize("name", list(build_all()))
def test_entities_have_uuid_ids_and_are_immutable(name) -> None:
    entity = build_all()[name]

    assert isinstance(entity.id, UUID)
    with pytest.raises(ValidationError):
        entity.id = uuid4()


@pytest.mark.parametrize(
    ("model", "valid"),
    [
        (Company, {"name": "C"}),
        (Store, {"company_id": uuid4(), "name": "S", "currency": "USD", "timezone": "UTC"}),
        (Customer, {"store_id": uuid4()}),
        (Product, {"store_id": uuid4(), "title": "P", "status": "active"}),
        (Variant, {"product_id": uuid4()}),
        (Warehouse, {"company_id": uuid4(), "name": "W"}),
        (InventoryLevel, {"variant_id": uuid4(), "warehouse_id": uuid4(), "available": 1}),
        (Shipment, {"order_id": uuid4(), "status": "pending"}),
    ],
)
def test_unknown_fields_are_rejected(model, valid) -> None:
    model(**valid)
    for extra in ({"tenant_id": "t"}, {"api_key": "x"}, {"raw_payload": {}}):
        with pytest.raises(ValidationError):
            model(**valid, **extra)


def test_ids_are_generated_and_unique() -> None:
    assert Company(name="A").id != Company(name="A").id


def test_external_ids_are_not_canonical_ids() -> None:
    company = Company(name="C", external_refs={REF})

    assert company.id != REF.external_id
    assert isinstance(company.id, UUID)
    with pytest.raises(ValidationError):
        Company(id="ext-1", name="C")


def test_external_refs_are_frozensets() -> None:
    company = Company(name="C", external_refs=[REF, REF])

    assert company.external_refs == frozenset({REF})
    with pytest.raises(AttributeError):
        company.external_refs.add(REF)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (Company, "name"),
        (Store, "name"),
        (Store, "timezone"),
        (Product, "title"),
        (Warehouse, "name"),
    ],
)
def test_required_names_cannot_be_blank(model, field) -> None:
    valid = {
        Company: {"name": "C"},
        Store: {"company_id": uuid4(), "name": "S", "currency": "USD", "timezone": "UTC"},
        Product: {"store_id": uuid4(), "title": "P", "status": "active"},
        Warehouse: {"company_id": uuid4(), "name": "W"},
    }[model]

    with pytest.raises(ValidationError):
        model(**{**valid, field: "  "})


def test_store_currency_is_normalized_and_validated() -> None:
    assert build_all()["store"].currency == "USD"
    with pytest.raises(ValidationError):
        Store(company_id=uuid4(), name="S", currency="dollars", timezone="UTC")


class TestCatalog:
    def test_product_keeps_source_status_separately(self) -> None:
        product = Product(
            store_id=uuid4(),
            title="P",
            status=ProductStatus.ARCHIVED,
            source_status=" discontinued ",
        )

        assert product.status is ProductStatus.ARCHIVED
        assert product.source_status == "discontinued"

    def test_product_status_is_canonical(self) -> None:
        assert {s.value for s in ProductStatus} == {"active", "draft", "archived", "unknown"}
        with pytest.raises(ValidationError):
            Product(store_id=uuid4(), title="P", status="discontinued")

    def test_variant_optional_fields(self) -> None:
        variant = Variant(product_id=uuid4(), title=" ", sku=" SKU-1 ")

        assert variant.title is None
        assert variant.sku == "SKU-1"


class TestShipment:
    def test_valid_shipment_with_source_status(self) -> None:
        shipped = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
        shipment = Shipment(
            order_id=uuid4(),
            status=ShipmentStatus.IN_TRANSIT,
            source_status="out_for_delivery_hub_7",
            courier_name="Test Courier",
            tracking_number="TRK-1",
            shipped_at=shipped,
        )

        assert shipment.status is ShipmentStatus.IN_TRANSIT
        assert shipment.source_status == "out_for_delivery_hub_7"
        assert shipment.delivered_at is None

    def test_status_set_is_canonical(self) -> None:
        assert {s.value for s in ShipmentStatus} == {
            "pending", "ready", "shipped", "in_transit", "delivered",
            "failed", "returned", "cancelled", "unknown",
        }  # fmt: skip

    @pytest.mark.parametrize("field", ["shipped_at", "delivered_at"])
    def test_naive_timestamps_rejected(self, field) -> None:
        with pytest.raises(ValidationError):
            Shipment(order_id=uuid4(), status="shipped", **{field: datetime(2026, 1, 1)})


RELATIONSHIPS = [
    (Store, "company_id", False),
    (Customer, "store_id", False),
    (Product, "store_id", False),
    (Variant, "product_id", False),
    (Warehouse, "company_id", False),
    (InventoryLevel, "variant_id", False),
    (InventoryLevel, "warehouse_id", False),
    (Order, "store_id", False),
    (Order, "customer_id", True),
    (OrderItem, "variant_id", True),
    (Shipment, "order_id", False),
]


@pytest.mark.parametrize(("model", "field", "optional"), RELATIONSHIPS)
def test_relationship_fields_are_uuids(model, field, optional) -> None:
    annotation = model.model_fields[field].annotation

    if optional:
        assert set(get_args(annotation)) == {UUID, type(None)}
    else:
        assert annotation is UUID


@pytest.mark.parametrize(("model", "field", "optional"), RELATIONSHIPS)
def test_relationship_fields_reject_external_style_ids(model, field, optional) -> None:
    with pytest.raises(ValidationError) as error:
        model.model_validate({field: "12345"})

    assert any(e["loc"] == (field,) for e in error.value.errors())
