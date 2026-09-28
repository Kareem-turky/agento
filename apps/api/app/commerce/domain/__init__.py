"""Canonical, provider-independent commerce domain.

Agents, workflows, policies and analytics depend on these models; adapters for
external systems translate into them. Models only: no persistence, APIs or adapters.
"""

from app.commerce.domain.catalog import Product, ProductStatus, Variant
from app.commerce.domain.common import CurrencyCode, ExternalReference, Money
from app.commerce.domain.company import Company, Store
from app.commerce.domain.customer import Customer
from app.commerce.domain.inventory import InventoryLevel, Warehouse
from app.commerce.domain.orders import Order, OrderItem, OrderStatus
from app.commerce.domain.shipping import Shipment, ShipmentStatus
from app.commerce.domain.tickets import Ticket, TicketStatus

__all__ = [
    "Company",
    "CurrencyCode",
    "Customer",
    "ExternalReference",
    "InventoryLevel",
    "Money",
    "Order",
    "OrderItem",
    "OrderStatus",
    "Product",
    "ProductStatus",
    "Shipment",
    "ShipmentStatus",
    "Store",
    "Ticket",
    "TicketStatus",
    "Variant",
    "Warehouse",
]
