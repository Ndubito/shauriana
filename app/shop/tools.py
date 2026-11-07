"""The shop agent's only payment tool.

The shop agent never calls PayPal itself. request_payment runs the policy engine,
saves the decision, and reaches pay() only when every rule passes.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app import approvals, payments, store
from app.models import Decision
from app.payments import Payment
from app.policy import engine
from app.policy.context import build_context
from app.policy.rules import Rules

OUTCOME_FOR_STATUS = {engine.BLOCKED: "blocked", engine.NEEDS_APPROVAL: "held"}


@dataclass(frozen=True)
class PaymentRequestResult:
    decision: Decision
    decision_id: int
    payment: Payment | None = None
    approval_id: int | None = None


def request_payment(conn: sqlite3.Connection, deal_id: str, *, rules: Rules, gateway,
                    log=lambda *a: None) -> PaymentRequestResult:
    deal, invoice = store.load_deal(conn, deal_id)
    if invoice is None:
        raise ValueError(f"Deal {deal_id} has no invoice yet")

    # Decide and record atomically, so two concurrent requests can't both pass `duplicate`.
    conn.execute("BEGIN IMMEDIATE")
    try:
        ctx = build_context(conn, deal)
        decision = engine.evaluate(deal, invoice, ctx, rules)
        decision_id = store.save_decision(conn, decision, rules)
        approval_id = payment_id = None
        if decision.status == engine.APPROVED:
            payment_id = payments.create_row(conn, deal.id, invoice.total)
        if decision.status == engine.NEEDS_APPROVAL:
            approval_id = approvals.create_pending(conn, deal.id, decision_id, rules.approval_timeout_hours)
        if decision.status in OUTCOME_FOR_STATUS:
            store.set_open_run_outcome(conn, deal.run_id, OUTCOME_FOR_STATUS[decision.status])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    log("policy", " ".join(f"{c.rule} {'✓' if c.passed else '✗'}" for c in decision.checks))
    log("policy", f"→ {decision.status.upper()}: {decision.reason}")

    if decision.status != engine.APPROVED:
        return PaymentRequestResult(decision, decision_id, approval_id=approval_id)
    payment = payments.pay(conn, deal, invoice, gateway, payment_id, log)
    return PaymentRequestResult(decision, decision_id, payment=payment)
