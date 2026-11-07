"""Builds the PolicyContext for a deal from the database."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal

from app.models import Deal, iso, utcnow
from app.policy.engine import PolicyContext

# Payment rows in these states count as money that has moved or is moving.
COMMITTED_PAYMENT_STATUSES = ("CREATED", "PENDING", "PROCESSING", "SUCCESS", "ONHOLD", "UNCLAIMED")
WEEK = timedelta(days=7)


def build_context(conn: sqlite3.Connection, deal: Deal, now: datetime | None = None) -> PolicyContext:
    now = now or utcnow()
    since = iso(now - WEEK)

    row = conn.execute(
        "SELECT last_paid_unit_price FROM inventory WHERE item = ?", (deal.item,)
    ).fetchone()
    last_paid = Decimal(row["last_paid_unit_price"]) if row and row["last_paid_unit_price"] else None

    marks = ",".join("?" * len(COMMITTED_PAYMENT_STATUSES))
    already = conn.execute(
        f"""SELECT
              (SELECT COUNT(*) FROM payments WHERE deal_id = ? AND status IN ({marks}))
            + (SELECT COUNT(*) FROM approvals WHERE deal_id = ? AND status IN ('pending', 'approved'))""",
        (deal.id, *COMMITTED_PAYMENT_STATUSES, deal.id),
    ).fetchone()[0]

    paid = conn.execute(
        f"""SELECT amount FROM payments
            WHERE deal_id != ? AND created_at >= ? AND status IN ({marks})""",
        (deal.id, since, *COMMITTED_PAYMENT_STATUSES),
    ).fetchall()
    # Held payments count too, so a waiting approval can't let another order slip past the cap.
    held = conn.execute(
        """SELECT d.total AS amount FROM approvals a JOIN deals d ON d.id = a.deal_id
           WHERE a.deal_id != ? AND a.created_at >= ? AND a.status = 'pending'""",
        (deal.id, since),
    ).fetchall()
    week = sum((Decimal(r["amount"]) for r in [*paid, *held]), Decimal("0"))

    return PolicyContext(
        last_paid_unit_price=last_paid,
        deal_already_paid_or_held=already > 0,
        week_committed=week,
    )
