"""Value objects: ExternalReference, Money and shared field behaviour."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.commerce.domain import Customer, ExternalReference, Money


class TestExternalReference:
    def test_trims_whitespace(self) -> None:
        ref = ExternalReference(system="  source-a ", external_id=" 123 ")

        assert (ref.system, ref.external_id) == ("source-a", "123")

    @pytest.mark.parametrize("field", ["system", "external_id"])
    @pytest.mark.parametrize("value", ["", "   "])
    def test_rejects_blank_values(self, field, value) -> None:
        values = {"system": "source-a", "external_id": "1", field: value}

        with pytest.raises(ValidationError):
            ExternalReference(**values)

    def test_is_immutable_hashable_and_set_friendly(self) -> None:
        a = ExternalReference(system="source-a", external_id="1")
        b = ExternalReference(system="source-a", external_id="1")
        c = ExternalReference(system="source-b", external_id="1")

        assert a == b and hash(a) == hash(b)
        assert frozenset({a, b, c}) == frozenset({a, c})
        with pytest.raises(ValidationError):
            a.external_id = "2"  # type: ignore[misc]

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            ExternalReference(system="s", external_id="1", url="https://example.invalid")


class TestMoney:
    def test_preserves_decimal_precision_and_scale(self) -> None:
        value = Decimal("123456789012345.123456789")

        assert Money(amount=value, currency="USD").amount == value
        assert str(Money(amount=Decimal("10.500"), currency="USD").amount) == "10.500"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0.1", Decimal("0.1")),
            ("19.99", Decimal("19.99")),
            (5, Decimal(5)),
            ("-3.25", Decimal("-3.25")),
        ],
    )
    def test_accepts_decimal_strings_and_ints(self, raw, expected) -> None:
        assert Money(amount=raw, currency="EUR").amount == expected

    def test_string_input_avoids_binary_float_error(self) -> None:
        total = (
            Money(amount="0.1", currency="USD").amount + Money(amount="0.2", currency="USD").amount
        )

        assert total == Decimal("0.3")

    @pytest.mark.parametrize("value", [0.1, 10.0, 1e3, True])
    def test_rejects_float_and_bool(self, value) -> None:
        with pytest.raises(ValidationError, match="not float"):
            Money(amount=value, currency="USD")

    @pytest.mark.parametrize(
        "value",
        ["NaN", "nan", "sNaN", "Infinity", "-Infinity", Decimal("NaN"), Decimal("Infinity")],
    )
    def test_rejects_non_finite(self, value) -> None:
        with pytest.raises(ValidationError):
            Money(amount=value, currency="USD")

    @pytest.mark.parametrize(
        ("raw", "expected"), [("usd", "USD"), (" egp ", "EGP"), ("Eur", "EUR")]
    )
    def test_normalizes_currency_to_upper_case(self, raw, expected) -> None:
        assert Money(amount="1", currency=raw).currency == expected

    @pytest.mark.parametrize("currency", ["", "   ", "US", "USDD", "U$D", "123", "us d", None])
    def test_rejects_invalid_currency(self, currency) -> None:
        with pytest.raises(ValidationError):
            Money(amount="1", currency=currency)

    def test_json_round_trip_is_exact(self) -> None:
        original = Money(amount=Decimal("0.123456789012345678901234567"), currency="kwd")

        dumped = original.model_dump(mode="json")
        restored = Money.model_validate(dumped)

        assert dumped == {"amount": "0.123456789012345678901234567", "currency": "KWD"}
        assert restored == original
        assert Money.model_validate_json(original.model_dump_json()) == original

    def test_is_immutable(self) -> None:
        price = Money(amount="1", currency="USD")

        with pytest.raises(ValidationError):
            price.amount = Decimal("2")  # type: ignore[misc]


class TestOptionalText:
    """Documented behaviour: blank optional strings normalize to None; others are trimmed."""

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_becomes_none(self, value) -> None:
        from uuid import uuid4

        customer = Customer(store_id=uuid4(), name=value, email=value, phone=value)

        assert (customer.name, customer.email, customer.phone) == (None, None, None)

    def test_values_are_trimmed_but_not_format_validated(self) -> None:
        from uuid import uuid4

        customer = Customer(
            store_id=uuid4(), name=" Test Customer ", email=" not-an-email ", phone=" +00 (0) 123 "
        )

        assert customer.name == "Test Customer"
        assert customer.email == "not-an-email"
        assert customer.phone == "+00 (0) 123"
