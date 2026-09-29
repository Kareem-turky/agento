"""The product-owned daily operations report contract.

``GET /api/v1/operations/reports/daily`` depends on this narrow interface only: never
on workflows, integrations, governance internals, agents, persistence or execution.
``app.workflows.DailyOperationsWorkflow`` implements it.

The report is DETERMINISTIC: canonical counts and rule-based findings, computed by
backend code, never by a model. Coverage is explicit and machine-readable:

- orders: those CREATED in the store-local business day;
- shipments: those SHIPPED in the store-local business day;
- inventory: NOT included (no store-scoped inventory query exists in the canonical
  contract yet).

It is not a history of every state transition during the day: the canonical models
carry no event history. Only canonical identifiers and statuses appear: no provider
status, external references, customer data or tracking numbers.
"""

from datetime import date
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, NonNegativeInt, model_validator

from app.commerce.domain import OrderStatus, ShipmentStatus
from app.context.models import RequestContext
from app.governance import ActionScope

# Product-owned cap on the findings returned; metrics always cover every record.
MAX_DAILY_FINDINGS = 100

_STRICT = ConfigDict(frozen=True, extra="forbid")


class OrdersCoverage(StrEnum):
    CREATED_IN_BUSINESS_DAY = "created_in_business_day"


class ShipmentsCoverage(StrEnum):
    SHIPPED_IN_BUSINESS_DAY = "shipped_in_business_day"


class InventoryCoverage(StrEnum):
    NOT_INCLUDED = "not_included"


class InventoryCoverageReason(StrEnum):
    STORE_SCOPED_INVENTORY_QUERY_UNAVAILABLE = "store_scoped_inventory_query_unavailable"


class DailyOperationsCoverage(BaseModel):
    """What the report analyzed, as stable machine-readable values."""

    model_config = _STRICT

    orders: OrdersCoverage = OrdersCoverage.CREATED_IN_BUSINESS_DAY
    shipments: ShipmentsCoverage = ShipmentsCoverage.SHIPPED_IN_BUSINESS_DAY
    inventory: InventoryCoverage = InventoryCoverage.NOT_INCLUDED
    inventory_reason: InventoryCoverageReason = (
        InventoryCoverageReason.STORE_SCOPED_INVENTORY_QUERY_UNAVAILABLE
    )


class FindingSeverity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"


# Sort rank: critical findings first.
SEVERITY_RANK: dict[FindingSeverity, int] = {
    FindingSeverity.CRITICAL: 0,
    FindingSeverity.WARNING: 1,
}


class FindingCode(StrEnum):
    ORDER_STATUS_UNKNOWN = "order_status_unknown"
    SHIPMENT_FAILED = "shipment_failed"
    SHIPMENT_RETURNED = "shipment_returned"
    SHIPMENT_STATUS_UNKNOWN = "shipment_status_unknown"


class FindingEntityType(StrEnum):
    ORDER = "order"
    SHIPMENT = "shipment"


class RecommendedAction(StrEnum):
    REVIEW_STATUS_MAPPING = "review_status_mapping"
    REVIEW_FAILED_SHIPMENT = "review_failed_shipment"
    REVIEW_RETURNED_SHIPMENT = "review_returned_shipment"


class FindingRule(BaseModel):
    model_config = _STRICT

    code: FindingCode
    severity: FindingSeverity
    entity_type: FindingEntityType
    canonical_status: str
    recommended_action: RecommendedAction


# The complete, fixed rule set (no thresholds, SLAs, forecasts or provider rules).
FINDING_RULES: tuple[FindingRule, ...] = (
    FindingRule(code=FindingCode.ORDER_STATUS_UNKNOWN, severity=FindingSeverity.WARNING,
                entity_type=FindingEntityType.ORDER, canonical_status=OrderStatus.UNKNOWN.value,
                recommended_action=RecommendedAction.REVIEW_STATUS_MAPPING),
    FindingRule(code=FindingCode.SHIPMENT_FAILED, severity=FindingSeverity.CRITICAL,
                entity_type=FindingEntityType.SHIPMENT,
                canonical_status=ShipmentStatus.FAILED.value,
                recommended_action=RecommendedAction.REVIEW_FAILED_SHIPMENT),
    FindingRule(code=FindingCode.SHIPMENT_RETURNED, severity=FindingSeverity.WARNING,
                entity_type=FindingEntityType.SHIPMENT,
                canonical_status=ShipmentStatus.RETURNED.value,
                recommended_action=RecommendedAction.REVIEW_RETURNED_SHIPMENT),
    FindingRule(code=FindingCode.SHIPMENT_STATUS_UNKNOWN, severity=FindingSeverity.WARNING,
                entity_type=FindingEntityType.SHIPMENT,
                canonical_status=ShipmentStatus.UNKNOWN.value,
                recommended_action=RecommendedAction.REVIEW_STATUS_MAPPING),
)  # fmt: skip
_RULES_BY_CODE = {rule.code: rule for rule in FINDING_RULES}


class DailyOperationsFinding(BaseModel):
    """One rule-based finding. Canonical identifiers and statuses only.

    ``order_id`` is the affected order: the order itself for an order finding, the
    parent order for a shipment finding.
    """

    model_config = _STRICT

    code: FindingCode
    severity: FindingSeverity
    entity_type: FindingEntityType
    entity_id: UUID
    order_id: UUID
    canonical_status: str
    recommended_action: RecommendedAction

    @model_validator(mode="after")
    def _matches_its_rule(self) -> Self:
        rule = _RULES_BY_CODE[self.code]
        if (self.severity, self.entity_type, self.canonical_status, self.recommended_action) != (
            rule.severity, rule.entity_type, rule.canonical_status, rule.recommended_action,
        ):  # fmt: skip
            raise ValueError("finding does not match its rule")
        if self.entity_type is FindingEntityType.ORDER and self.entity_id != self.order_id:
            raise ValueError("an order finding's order_id is the order itself")
        return self

    def sort_key(self) -> tuple[int, str, str, str]:
        """Documented stable order: severity (critical first), code, order_id, entity_id."""
        return (SEVERITY_RANK[self.severity], self.code.value, str(self.order_id),
                str(self.entity_id))  # fmt: skip


class OrderStatusCount(BaseModel):
    model_config = _STRICT

    status: OrderStatus
    count: NonNegativeInt


class ShipmentStatusCount(BaseModel):
    model_config = _STRICT

    status: ShipmentStatus
    count: NonNegativeInt


class DailyOperationsMetrics(BaseModel):
    """Counts over the COMPLETE matching result set (never truncated).

    Status counts list every canonical status in enum order, including zeros.
    ``affected_orders`` is the number of distinct orders with at least one finding.
    """

    model_config = _STRICT

    orders_created: NonNegativeInt
    order_status_counts: tuple[OrderStatusCount, ...]
    shipments_shipped: NonNegativeInt
    shipment_status_counts: tuple[ShipmentStatusCount, ...]
    affected_orders: NonNegativeInt

    @model_validator(mode="after")
    def _complete_and_consistent(self) -> Self:
        if [c.status for c in self.order_status_counts] != list(OrderStatus):
            raise ValueError("order_status_counts must list every order status in order")
        if [c.status for c in self.shipment_status_counts] != list(ShipmentStatus):
            raise ValueError("shipment_status_counts must list every shipment status in order")
        if sum(c.count for c in self.order_status_counts) != self.orders_created:
            raise ValueError("order status counts must add up to orders_created")
        if sum(c.count for c in self.shipment_status_counts) != self.shipments_shipped:
            raise ValueError("shipment status counts must add up to shipments_shipped")
        return self


class DailyOperationsReport(BaseModel):
    """The deterministic daily operations report of one store and business day."""

    model_config = _STRICT

    store_id: UUID
    business_date: date
    timezone: str
    window_start: AwareDatetime
    window_end: AwareDatetime
    generated_at: AwareDatetime
    metrics: DailyOperationsMetrics
    findings: tuple[DailyOperationsFinding, ...]
    findings_total: NonNegativeInt
    findings_truncated: bool
    coverage: DailyOperationsCoverage

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.window_start >= self.window_end:
            raise ValueError("the business-day window must be non-empty")
        if len(self.findings) != min(self.findings_total, MAX_DAILY_FINDINGS):
            raise ValueError("findings must be the first MAX_DAILY_FINDINGS of findings_total")
        if self.findings_truncated != (self.findings_total > MAX_DAILY_FINDINGS):
            raise ValueError("findings_truncated must reflect the cap")
        if list(self.findings) != sorted(self.findings, key=DailyOperationsFinding.sort_key):
            raise ValueError("findings must be in the documented order")
        return self


class DailyOperationsReportError(Exception):
    """Base of the safe report errors. Messages are fixed codes, never details."""

    code = "daily_operations_report_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class DailyOperationsForbiddenError(DailyOperationsReportError):
    """A required read is not allowed for this actor and scope. Nothing was read."""

    code = "daily_operations_forbidden"


class DailyOperationsUnavailableError(DailyOperationsReportError):
    """The report could not be produced safely (integration, data or configuration)."""

    code = "daily_operations_unavailable"


@runtime_checkable
class DailyOperationsReportService(Protocol):
    async def get_daily_report(
        self, request: RequestContext, scope: ActionScope, business_date: date | None
    ) -> DailyOperationsReport:
        """The report of the trusted store scope for ``business_date`` (store-local
        today when None). Raises ``DailyOperationsForbiddenError`` or
        ``DailyOperationsUnavailableError``."""
        ...
