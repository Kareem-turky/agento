"""ExternalReferences: a frozenset in memory, a deterministically ordered array when dumped."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.commerce
from app.commerce.domain import Company, ExternalReference, Order, Shipment, ShipmentStatus
from tests.commerce.factories import item, order

API_DIR = Path(app.commerce.__file__).parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]


def ref(system: str, external_id: str) -> ExternalReference:
    return ExternalReference(system=system, external_id=external_id)


def pairs(dumped: list[dict[str, str]] | tuple[dict[str, str], ...]) -> list[tuple[str, str]]:
    return [(r["system"], r["external_id"]) for r in dumped]


def company(*refs: ExternalReference) -> Company:
    return Company(
        id=UUID("00000000-0000-4000-8000-000000000001"), name="Example", external_refs=refs
    )


# ----- sort rule ----------------------------------------------------------------------


def test_json_sorts_by_system_then_external_id_lexicographically() -> None:
    dumped = company(ref("b", "1"), ref("a", "2"), ref("a", "10")).model_dump(mode="json")
    # Plain string ordering: "10" < "2".
    assert pairs(dumped["external_refs"]) == [("a", "10"), ("a", "2"), ("b", "1")]


def test_same_system_different_ids_and_different_systems() -> None:
    refs = [ref("shop", "z-9"), ref("shop", "a-1"), ref("erp", "m-5"), ref("courier", "x")]
    for permutation in (refs, list(reversed(refs)), refs[2:] + refs[:2]):
        dumped = company(*permutation).model_dump(mode="json")["external_refs"]
        assert pairs(dumped) == [("courier", "x"), ("erp", "m-5"), ("shop", "a-1"), ("shop", "z-9")]


def test_duplicates_collapse() -> None:
    entity = company(ref("a", "1"), ref("a", "1"), ref("b", "2"))
    assert len(entity.external_refs) == 2
    assert pairs(entity.model_dump(mode="json")["external_refs"]) == [("a", "1"), ("b", "2")]


def test_empty_references_dump_as_empty_array() -> None:
    assert company().model_dump(mode="json")["external_refs"] == []
    assert company().model_dump()["external_refs"] == ()


def test_model_dump_json_uses_the_same_order() -> None:
    text = company(ref("b", "1"), ref("a", "1")).model_dump_json()
    assert text.index('"system":"a"') < text.index('"system":"b"')


# ----- Python representation ------------------------------------------------------------


def test_in_memory_value_is_an_immutable_frozenset_of_references() -> None:
    entity = company(ref("b", "1"), ref("a", "1"))
    assert isinstance(entity.external_refs, frozenset)
    assert all(isinstance(r, ExternalReference) for r in entity.external_refs)
    assert entity.external_refs == frozenset({ref("a", "1"), ref("b", "1")})
    with pytest.raises(ValidationError):
        entity.external_refs = frozenset()
    reference = ref("a", "1")
    with pytest.raises(ValidationError):
        reference.system = "b"
    assert hash(reference) == hash(ref("a", "1"))


def test_python_mode_dump_is_sorted_and_round_trips() -> None:
    # Pydantic dumps nested models as dicts, which cannot live in a set, so Python mode
    # yields a sorted tuple of dicts that validates back into the same frozenset.
    entity = company(ref("b", "1"), ref("a", "1"))
    dumped = entity.model_dump()
    assert dumped["external_refs"] == (
        {"system": "a", "external_id": "1"},
        {"system": "b", "external_id": "1"},
    )
    assert Company.model_validate(dumped) == entity


# ----- round trips, including nested order items ---------------------------------------

REFS = (ref("source-b", "B-9"), ref("source-a", "A-1"), ref("source-a", "A-10"))


def entities() -> list:
    return [
        company(*REFS),
        order(external_refs=REFS, items=(item(external_refs=REFS), item(external_refs=REFS[:1]))),
        Shipment(
            order_id=UUID("00000000-0000-4000-8000-000000000002"),
            status=ShipmentStatus.SHIPPED,
            shipped_at=datetime(2026, 3, 1, tzinfo=UTC),
            external_refs=REFS,
        ),
    ]


@pytest.mark.parametrize("entity", entities(), ids=lambda e: type(e).__name__)
def test_round_trips_preserve_equality(entity) -> None:
    dumped = entity.model_dump(mode="json")
    assert type(entity).model_validate(dumped) == entity
    assert type(entity).model_validate(dumped).model_dump(mode="json") == dumped
    assert type(entity).model_validate_json(entity.model_dump_json()) == entity
    assert type(entity).model_validate(entity.model_dump()) == entity


def test_nested_order_item_references_are_sorted() -> None:
    dumped = entities()[1].model_dump(mode="json")
    expected = [("source-a", "A-1"), ("source-a", "A-10"), ("source-b", "B-9")]
    assert pairs(dumped["external_refs"]) == expected
    assert pairs(dumped["items"][0]["external_refs"]) == expected
    restored = Order.model_validate(dumped)
    assert isinstance(restored.items[0].external_refs, frozenset)


# ----- byte-identical output across hash seeds ------------------------------------------

SEED_SCRIPT = """
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID
from app.commerce.domain import (
    Company, ExternalReference as R, Money, Order, OrderItem, OrderStatus, Shipment,
    ShipmentStatus,
)
refs = [R(system=s, external_id=i) for s, i in
        [("source-b", "B-9"), ("source-a", "A-1"), ("source-a", "A-10"), ("erp", "7"),
         ("shop", "x"), ("shop", "y"), ("courier", "c-1")]]
u = lambda n: UUID(int=n)
price = Money(amount=Decimal("10.00"), currency="USD")
line = OrderItem(id=u(3), title="Line", quantity=Decimal("1"), unit_price=price,
                 external_refs=refs)
entities = [
    Company(id=u(1), name="Example", external_refs=refs),
    Order(id=u(2), store_id=u(4), status=OrderStatus.PENDING, items=(line,), total=price,
          created_at=datetime(2026, 3, 1, tzinfo=UTC), external_refs=refs),
    Shipment(id=u(5), order_id=u(2), status=ShipmentStatus.SHIPPED, external_refs=refs),
]
print("\\n".join(e.model_dump_json() for e in entities))
"""


def test_json_is_byte_identical_across_hash_seeds() -> None:
    outputs = {}
    for seed in ("1", "2", "57", "999"):  # 57 reproduced the original CI failure
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(API_DIR)}
        result = subprocess.run(  # noqa: S603 - fixed interpreter and code
            [sys.executable, "-c", SEED_SCRIPT],
            capture_output=True,
            check=True,
            env=env,
            cwd=REPO_ROOT,
        )
        outputs[seed] = result.stdout

    assert len(set(outputs.values())) == 1, outputs
    assert b'"external_refs":[{"system":"courier"' in outputs["57"]
