"""The policy engine: plain code, no model calls.

Checks a payment request against the owner's rules in a fixed order. The first
failing rule decides the outcome and evaluation stops there; every rule that ran
is recorded in `checks`, including the ones that passed.

It reads only numbers and email addresses from the deal and invoice. Free text
(e.g. `Deal.rationale`, which can come from the supplier) is never read, so no
message can talk the engine into a different answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models import Check, Deal, Decision, Invoice
from app.paypal.money import fmt
from app.policy.rules import Rules

APPROVED = "approved"
NEEDS_APPROVAL = "needs_approval"
BLOCKED = "blocked"

RULE_ORDER = [
    "payee_allowlist",
    "deal_integrity",
    "duplicate",
    "price_spike",
    "per_order_cap",
    "weekly_cap",
    "approval_threshold",
]


@dataclass(frozen=True)
class PolicyContext:
    """Facts from the database the rules need. Built by app.policy.context."""
    last_paid_unit_price: Decimal | None
    deal_already_paid_or_held: bool
    week_committed: Decimal  # paid + in-flight + held, last 7 days, excluding this deal


def _pct(x: Decimal) -> str:
    """35.000 -> '35', 20.5 -> '20.5'"""
    return f"{x.quantize(Decimal('0.1')).normalize():f}"


def _usd(x: Decimal) -> str:
    # Show sub-cent unit prices like $0.135 in full; otherwise 2 dp.
    return f"${x:f}" if x != x.quantize(Decimal("0.01")) else f"${fmt(x)}"


def evaluate(deal: Deal, invoice: Invoice, ctx: PolicyContext, rules: Rules) -> Decision:
    checks: list[Check] = []

    def result(status: str, rule: str | None, reason: str) -> Decision:
        return Decision(deal.id, status, rule, reason, list(checks), rules.version)

    def check(rule: str, passed: bool) -> bool:
        checks.append(Check(rule, passed))
        return passed

    # 1. Payee allowlist
    payee = invoice.payee_email.strip().lower()
    if not check("payee_allowlist", payee in rules.approved_payees):
        return result(BLOCKED, "payee_allowlist",
                      f"Invoice payee {invoice.payee_email} is not on the approved payee list.")

    # 2. Deal integrity: the invoice must match the deal exactly.
    problems = []
    if invoice.total != deal.total:
        problems.append(f"invoice total {_usd(invoice.total)} differs from deal total {_usd(deal.total)}")
    if payee != deal.payee_email.strip().lower():
        problems.append(f"invoice payee {invoice.payee_email} is not the deal's supplier {deal.payee_email}")
    if invoice.currency != deal.currency or deal.currency != rules.currency:
        problems.append(f"currency {invoice.currency}/{deal.currency} is not {rules.currency}")
    if not check("deal_integrity", not problems):
        return result(BLOCKED, "deal_integrity", "Deal integrity failed: " + "; ".join(problems) + ".")

    # 3. Duplicate
    if not check("duplicate", not ctx.deal_already_paid_or_held):
        return result(BLOCKED, "duplicate", f"Deal {deal.id} already has a payment or a held approval.")

    # 4. Price spike vs. last paid unit price (no history -> nothing to compare, passes)
    last = ctx.last_paid_unit_price
    if last is not None and last > 0:
        increase = (deal.unit_price - last) / last * 100
        if not check("price_spike", increase <= rules.max_unit_price_increase_pct):
            return result(
                BLOCKED, "price_spike",
                f"Unit price {_usd(deal.unit_price)} is {_pct(increase)}% above last paid "
                f"{_usd(last)} (limit {_pct(rules.max_unit_price_increase_pct)}%).",
            )
    else:
        check("price_spike", True)

    # 5. Per-order cap
    if not check("per_order_cap", deal.total <= rules.max_per_order):
        return result(BLOCKED, "per_order_cap",
                      f"Order total {_usd(deal.total)} is above the per-order cap {_usd(rules.max_per_order)}.")

    # 6. Weekly cap (rolling 7 days)
    week_total = ctx.week_committed + deal.total
    if not check("weekly_cap", week_total <= rules.max_per_week):
        return result(
            BLOCKED, "weekly_cap",
            f"This payment would bring the last 7 days to {_usd(week_total)} "
            f"({_usd(ctx.week_committed)} already committed), above the weekly cap {_usd(rules.max_per_week)}.",
        )

    # 7. Approval threshold
    if not check("approval_threshold", deal.total <= rules.require_approval_above):
        return result(NEEDS_APPROVAL, "approval_threshold",
                      f"Order total {_usd(deal.total)} is above {_usd(rules.require_approval_above)}, "
                      f"so the owner must approve it.")

    # 8. Otherwise approved
    return result(APPROVED, None, "All rules passed.")
