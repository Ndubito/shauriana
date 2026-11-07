"""Plain data passed between the negotiation, policy engine and payments code."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Deal:
    id: str
    run_id: str
    item: str
    qty: int
    unit_price: Decimal
    total: Decimal
    currency: str
    delivery_date: str
    payee_email: str  # the supplier the shop agreed the deal with
    rationale: str = ""  # free text from the negotiation; the policy engine never reads it


@dataclass(frozen=True)
class Invoice:
    id: str
    total: Decimal
    currency: str
    payee_email: str  # who the invoice says to pay (the invoicer)


@dataclass(frozen=True)
class Check:
    rule: str
    passed: bool


@dataclass(frozen=True)
class Decision:
    deal_id: str
    status: str  # "approved" | "needs_approval" | "blocked"
    rule: str | None  # the rule that decided it, None when approved
    reason: str
    checks: list[Check] = field(default_factory=list)
    policy_version: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
