"""Offer messages and the hard limits each side's offers are checked against, in code.

The models choose their moves; these checks decide whether a move is allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

SHOP, SUPPLIER = "shop", "supplier"
ACTIONS = ("request", "quote", "counter", "accept", "walk_away")
PROPOSALS = ("quote", "counter")  # moves that put terms on the table


@dataclass(frozen=True)
class Offer:
    run_id: str
    round: int
    from_side: str
    action: str
    item: str
    qty: int | None = None
    unit_price: Decimal | None = None
    delivery_date: str | None = None
    rationale: str = ""

    def terms(self) -> str:
        price = f" @ ${self.unit_price}" if self.unit_price is not None else ""
        return f"{self.qty} × {self.item}{price}, delivery {self.delivery_date}"


@dataclass(frozen=True)
class ShopLimits:
    max_unit_price: Decimal
    min_qty: int           # enough to get back to the reorder level
    max_qty: int           # the planned reorder quantity; the shop never buys more
    latest_delivery: date


@dataclass(frozen=True)
class SupplierLimits:
    floor_price: Decimal
    max_qty: int
    earliest_delivery: date


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _basic_problems(qty, unit_price, delivery_date, need_price: bool) -> list[str]:
    problems = []
    if qty is None or qty <= 0:
        problems.append("quantity must be a positive whole number")
    if need_price:
        if unit_price is None or unit_price <= 0:
            problems.append("unit price must be positive")
        elif unit_price != unit_price.quantize(Decimal("0.01")):
            problems.append(f"unit price ${unit_price} must be in whole cents (e.g. 0.11)")
    if _parse_date(delivery_date) is None:
        problems.append(f"delivery date {delivery_date!r} must be YYYY-MM-DD")
    return problems


def check_terms(side: str, qty: int | None, unit_price: Decimal | None, delivery_date: str | None,
                limits: ShopLimits | SupplierLimits, need_price: bool = True) -> str | None:
    """Return why these terms break `side`'s own limits, or None if they are allowed."""
    problems = _basic_problems(qty, unit_price, delivery_date, need_price)
    if problems:
        return "; ".join(problems)
    when = _parse_date(delivery_date)
    if side == SHOP:
        assert isinstance(limits, ShopLimits)
        if unit_price is not None and unit_price > limits.max_unit_price:
            problems.append(f"unit price ${unit_price} is above the shop's max ${limits.max_unit_price}")
        if qty < limits.min_qty:
            problems.append(f"quantity {qty} is below the minimum {limits.min_qty}")
        if qty > limits.max_qty:
            problems.append(f"quantity {qty} is above the planned order of {limits.max_qty}")
        if when > limits.latest_delivery:
            problems.append(f"delivery {when} is later than {limits.latest_delivery}")
    else:
        assert isinstance(limits, SupplierLimits)
        if unit_price is not None and unit_price < limits.floor_price:
            problems.append(f"unit price ${unit_price} is below the supplier's floor ${limits.floor_price}")
        if qty > limits.max_qty:
            problems.append(f"quantity {qty} is above the supplier's max {limits.max_qty}")
        if when < limits.earliest_delivery:
            problems.append(f"delivery {when} is earlier than {limits.earliest_delivery}")
    return "; ".join(problems) or None


def to_price(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))  # str() first so 0.11 doesn't carry binary float noise
    except (InvalidOperation, ValueError):
        return None
