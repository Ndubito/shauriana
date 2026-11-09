"""Held payments waiting for the owner: create, expire, approve, deny."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from app import store
from app.models import Deal, Invoice, iso, utcnow


class ApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Approved:
    deal: Deal
    invoice: Invoice
    payment_id: int  # payment row reserved (status CREATED); call payments.pay() next


def decide(conn: sqlite3.Connection, approval_id: int, approve: bool,
           now: datetime | None = None) -> Approved | None:
    """Record the owner's answer. Approve reserves the payment row in the same transaction,
    so a double click (or two tabs) can't pay twice. Returns Approved, or None for a deny."""
    from app import payments  # avoid an import cycle

    now = now or utcnow()
    expire_stale(conn, now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise ApprovalError(f"No approval {approval_id}")
        if row["status"] != "pending":
            raise ApprovalError(f"Approval {approval_id} is already {row['status']}")
        deal, invoice = store.load_deal(conn, row["deal_id"])
        status = "approved" if approve else "denied"
        conn.execute("UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
                     (status, iso(now), approval_id))
        result = None
        if approve:
            result = Approved(deal, invoice, payments.create_row(conn, deal.id, invoice.total))
        else:
            store.set_run_outcome(conn, deal.run_id, "denied")
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


def create_pending(conn: sqlite3.Connection, deal_id: str, decision_id: int,
                   timeout_hours: int, now: datetime | None = None) -> int:
    now = now or utcnow()
    cur = conn.execute(
        "INSERT INTO approvals (deal_id, decision_id, status, created_at, expires_at)"
        " VALUES (?, ?, 'pending', ?, ?)",
        (deal_id, decision_id, iso(now), iso(now + timedelta(hours=timeout_hours))),
    )
    return cur.lastrowid


def expire_stale(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """Mark unanswered approvals past their deadline as expired; their runs become denied.

    Returns the affected deal IDs.
    """
    now_s = iso(now or utcnow())
    rows = conn.execute(
        "SELECT a.id, a.deal_id, d.run_id FROM approvals a JOIN deals d ON d.id = a.deal_id"
        " WHERE a.status = 'pending' AND a.expires_at <= ?",
        (now_s,),
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE approvals SET status = 'expired', decided_at = ? WHERE id = ?", (now_s, r["id"]))
        conn.execute("UPDATE runs SET outcome = 'denied' WHERE id = ?", (r["run_id"],))
    return [r["deal_id"] for r in rows]
