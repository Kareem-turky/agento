"""Deterministic daily operations analysis (a Workflow, not an Agent).

    trusted RequestContext + store ActionScope
      -> governance preflight: operations.store.read, operations.orders.list,
         operations.shipments.list must ALL be ALLOW (else Forbidden, nothing read)
      -> CommerceIntegration.get_store          (exact store + company, else Unavailable)
      -> store.timezone (IANA) -> business day [local midnight, next local midnight)
      -> list_orders(OrderQuery(store, created in window))
      -> list_shipments(ShipmentQuery(store, shipped in window))
      -> parent orders of the shipments (the day's orders, else get_order) -> same store
      -> canonical metrics + rule-based findings -> DailyOperationsReport

No model, agent, write, command, ticket or ActionRun: it only reads, through
GovernanceGate-checked actions. Integration output is untrusted: any foreign,
out-of-window or malformed record fails the whole report closed (never dropped).
"""

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.commerce.domain import Order, OrderStatus, Shipment, ShipmentStatus, Store
from app.context.models import RequestContext
from app.governance import ActionIntent, ActionScope, GovernanceGate, PolicyOutcome
from app.integrations.commerce import CommerceIntegration, OrderQuery, ShipmentQuery
from app.operations.actions import ORDERS_LIST_ACTION, SHIPMENTS_LIST_ACTION, STORE_READ_ACTION
from app.services.operations_reports import (
    FINDING_RULES,
    MAX_DAILY_FINDINGS,
    DailyOperationsCoverage,
    DailyOperationsFinding,
    DailyOperationsForbiddenError,
    DailyOperationsMetrics,
    DailyOperationsReport,
    DailyOperationsUnavailableError,
    FindingEntityType,
    OrderStatusCount,
    ShipmentStatusCount,
)

# Every read the report performs; all must be allowed before anything is read.
REQUIRED_READ_ACTIONS = (STORE_READ_ACTION, ORDERS_LIST_ACTION, SHIPMENTS_LIST_ACTION)

_ORDER_RULES = {r.canonical_status: r for r in FINDING_RULES
                if r.entity_type is FindingEntityType.ORDER}  # fmt: skip
_SHIPMENT_RULES = {r.canonical_status: r for r in FINDING_RULES
                   if r.entity_type is FindingEntityType.SHIPMENT}  # fmt: skip


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def business_day_window(business_date: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight): each midnight is built in the zone, so a
    DST day is 23 or 25 hours long, never assumed to be 24."""
    start = datetime.combine(business_date, time.min, tzinfo=zone)
    end = datetime.combine(business_date + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


class DailyOperationsWorkflow:
    """Implements ``DailyOperationsReportService`` deterministically.

    ``clock`` must return an aware datetime (default: UTC now); it decides "today" in
    the store's timezone and ``generated_at``. Tests inject a fixed clock.
    """

    def __init__(
        self,
        *,
        commerce: CommerceIntegration,
        gate: GovernanceGate,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._commerce = commerce
        self._gate = gate
        self._clock = clock

    async def get_daily_report(
        self, request: RequestContext, scope: ActionScope, business_date: date | None
    ) -> DailyOperationsReport:
        if not isinstance(request, RequestContext) or not isinstance(scope, ActionScope):
            raise DailyOperationsUnavailableError()
        if business_date is not None and (
            not isinstance(business_date, date) or isinstance(business_date, datetime)
        ):
            raise DailyOperationsUnavailableError()
        self._preflight(request, scope)  # before ANY integration call
        store_id = self._uuid(scope.store_id)
        now = self._clock()
        if not _aware(now):
            raise DailyOperationsUnavailableError()  # naive clocks fail closed

        store = await self._store(store_id, scope)
        try:
            zone = ZoneInfo(store.timezone)
        except Exception:  # noqa: BLE001 - never expose the timezone or parser error
            raise DailyOperationsUnavailableError() from None
        day = business_date if business_date is not None else now.astimezone(zone).date()
        start, end = business_day_window(day, zone)

        orders = await self._orders(store.id, start, end)
        shipments = await self._shipments(store.id, start, end)
        parents = await self._parent_orders(store.id, orders, shipments)

        findings = sorted(self._findings(orders, shipments, parents),
                          key=DailyOperationsFinding.sort_key)  # fmt: skip
        try:
            return DailyOperationsReport(
                store_id=store.id,
                business_date=day,
                timezone=store.timezone,
                window_start=start,
                window_end=end,
                generated_at=now,
                metrics=self._metrics(orders, shipments, findings),
                findings=tuple(findings[:MAX_DAILY_FINDINGS]),
                findings_total=len(findings),
                findings_truncated=len(findings) > MAX_DAILY_FINDINGS,
                coverage=DailyOperationsCoverage(),
            )
        except ValueError:
            raise DailyOperationsUnavailableError() from None

    # ----- governance -------------------------------------------------------------

    def _preflight(self, request: RequestContext, scope: ActionScope) -> None:
        actor = request.actor
        if actor is None or scope.store_id is None:
            raise DailyOperationsForbiddenError()
        for action in REQUIRED_READ_ACTIONS:
            decision = self._gate.decide(actor, ActionIntent(name=action.name), scope)
            if decision.outcome is not PolicyOutcome.ALLOW:
                raise DailyOperationsForbiddenError()

    # ----- reads (untrusted results, validated here) -------------------------------

    @staticmethod
    def _uuid(value: str | None) -> UUID:
        try:
            return UUID(str(value))
        except ValueError:
            raise DailyOperationsUnavailableError() from None

    async def _store(self, store_id: UUID, scope: ActionScope) -> Store:
        try:
            store = await self._commerce.get_store(store_id)
        except Exception:  # noqa: BLE001 - integration failures never leak
            raise DailyOperationsUnavailableError() from None
        if (
            not isinstance(store, Store)
            or store.id != store_id
            or str(store.company_id) != scope.company_id
        ):
            raise DailyOperationsUnavailableError()  # never return a foreign store
        return store

    async def _orders(self, store_id: UUID, start: datetime, end: datetime) -> tuple[Order, ...]:
        query = OrderQuery(store_id=store_id, created_from=start, created_to=end)
        try:
            orders = await self._commerce.list_orders(query)
        except Exception:  # noqa: BLE001
            raise DailyOperationsUnavailableError() from None
        if not isinstance(orders, tuple):
            raise DailyOperationsUnavailableError()
        for order in orders:
            if (
                not isinstance(order, Order)
                or order.store_id != store_id
                or not _aware(order.created_at)
                or not start <= order.created_at < end
            ):
                raise DailyOperationsUnavailableError()
        self._unique(o.id for o in orders)
        return orders

    async def _shipments(
        self, store_id: UUID, start: datetime, end: datetime
    ) -> tuple[Shipment, ...]:
        query = ShipmentQuery(store_id=store_id, shipped_from=start, shipped_to=end)
        try:
            shipments = await self._commerce.list_shipments(query)
        except Exception:  # noqa: BLE001
            raise DailyOperationsUnavailableError() from None
        if not isinstance(shipments, tuple):
            raise DailyOperationsUnavailableError()
        for shipment in shipments:
            if (
                not isinstance(shipment, Shipment)
                or shipment.shipped_at is None
                or not _aware(shipment.shipped_at)
                or not start <= shipment.shipped_at < end
            ):
                raise DailyOperationsUnavailableError()
        self._unique(s.id for s in shipments)
        return shipments

    async def _parent_orders(
        self, store_id: UUID, orders: tuple[Order, ...], shipments: tuple[Shipment, ...]
    ) -> dict[UUID, Order]:
        """Establish every shipment's parent order and prove it is this store's.

        A shipment is never trusted as store scoped just because the query asked for
        the store: its parent order is read (the day's orders are reused; others are
        fetched once each, in a stable order) and must belong to the store.
        """
        parents = {o.id: o for o in orders}
        for order_id in sorted({s.order_id for s in shipments} - parents.keys(), key=str):
            try:
                order = await self._commerce.get_order(order_id)
            except Exception:  # noqa: BLE001 - includes "not found": a broken relation
                raise DailyOperationsUnavailableError() from None
            if not isinstance(order, Order) or order.id != order_id:
                raise DailyOperationsUnavailableError()
            parents[order_id] = order
        for shipment in shipments:
            if parents[shipment.order_id].store_id != store_id:
                raise DailyOperationsUnavailableError()  # a foreign store's shipment
        return parents

    @staticmethod
    def _unique(ids: Iterable[UUID]) -> None:
        seen = list(ids)
        if len(seen) != len(set(seen)):
            raise DailyOperationsUnavailableError()

    # ----- deterministic analysis --------------------------------------------------

    @staticmethod
    def _findings(
        orders: tuple[Order, ...],
        shipments: tuple[Shipment, ...],
        parents: dict[UUID, Order],
    ) -> list[DailyOperationsFinding]:
        findings: list[DailyOperationsFinding] = []
        for order in orders:
            rule = _ORDER_RULES.get(order.status.value)
            if rule is not None:
                findings.append(DailyOperationsFinding(
                    code=rule.code, severity=rule.severity, entity_type=rule.entity_type,
                    entity_id=order.id, order_id=order.id,
                    canonical_status=order.status.value,
                    recommended_action=rule.recommended_action,
                ))  # fmt: skip
        for shipment in shipments:
            rule = _SHIPMENT_RULES.get(shipment.status.value)
            if rule is not None:
                findings.append(DailyOperationsFinding(
                    code=rule.code, severity=rule.severity, entity_type=rule.entity_type,
                    entity_id=shipment.id, order_id=parents[shipment.order_id].id,
                    canonical_status=shipment.status.value,
                    recommended_action=rule.recommended_action,
                ))  # fmt: skip
        return findings

    @staticmethod
    def _metrics(
        orders: tuple[Order, ...],
        shipments: tuple[Shipment, ...],
        findings: list[DailyOperationsFinding],
    ) -> DailyOperationsMetrics:
        return DailyOperationsMetrics(
            orders_created=len(orders),
            order_status_counts=tuple(
                OrderStatusCount(status=status, count=sum(o.status is status for o in orders))
                for status in OrderStatus
            ),
            shipments_shipped=len(shipments),
            shipment_status_counts=tuple(
                ShipmentStatusCount(status=status, count=sum(s.status is status for s in shipments))
                for status in ShipmentStatus
            ),  # fmt: skip
            affected_orders=len({f.order_id for f in findings}),
        )
