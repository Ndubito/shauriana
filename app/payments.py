"""pay(deal): payout, poll, record on invoice. Only reached after the policy approves
(or, later, the owner approves). Every step updates the `payments` row."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app import store
from app.models import Deal, Invoice, iso, utcnow
from app.paypal.client import PayPalError
from app.paypal.invoicing import PAID_STATUSES
from app.paypal.payouts import PayoutTimeout


@dataclass(frozen=True)
class Payment:
    id: int
    deal_id: str
    status: str  # SUCCESS, FAILED, UNCLAIMED, ...
    payout_batch_id: str | None = None
    payout_item_id: str | None = None
    invoice_payment_id: str | None = None
    invoice_status: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS" and (self.invoice_status in PAID_STATUSES)


def _update(conn, payment_id: int, **cols) -> None:
    cols["updated_at"] = iso(utcnow())
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE payments SET {sets} WHERE id = ?", (*cols.values(), payment_id))


def create_row(conn: sqlite3.Connection, deal_id: str, amount) -> int:
    """Reserve the payment (status CREATED) before PayPal is called. Call this in the same
    transaction as the decision, so a second request for the deal is blocked as a duplicate."""
    now = iso(utcnow())
    return conn.execute(
        "INSERT INTO payments (deal_id, amount, status, created_at, updated_at) VALUES (?, ?, 'CREATED', ?, ?)",
        (deal_id, str(amount), now, now),
    ).lastrowid


def pay(conn: sqlite3.Connection, deal: Deal, invoice: Invoice, gateway, payment_id: int,
        log=lambda *a: None) -> Payment:
    pid = payment_id

    def failed(msg: str, **extra) -> Payment:
        _update(conn, pid, status=extra.pop("status", "FAILED"), error=msg, **extra)
        store.set_run_outcome(conn, deal.run_id, "failed")
        log("payment", f"FAILED: {msg}")
        row = conn.execute("SELECT * FROM payments WHERE id = ?", (pid,)).fetchone()
        return Payment(pid, deal.id, row["status"], row["payout_batch_id"], row["payout_item_id"], error=msg)

    # Payout, using the deal ID as sender_batch_id (PayPal's own duplicate guard).
    try:
        batch_id = gateway.send_payout(deal.id, invoice.payee_email, invoice.total, invoice.currency,
                                       note=f"Payment for invoice {invoice.id} ({deal.id})")
    except PayPalError as e:
        return failed(f"payout create: {e}")
    _update(conn, pid, payout_batch_id=batch_id, status="PENDING")
    log("payout", f"batch {batch_id} created (sender_batch_id={deal.id})")

    try:
        result = gateway.wait_payout(batch_id)
    except (PayPalError, PayoutTimeout) as e:
        return failed(f"payout poll: {e}")
    _update(conn, pid, payout_item_id=result.item_id, status=result.status)
    if result.status != "SUCCESS":
        return failed(f"payout item ended {result.status}", status=result.status)
    log("payout", f"SUCCESS item {result.item_id}")

    # Supplier records the payment on its invoice.
    try:
        inv_payment_id, inv_status = gateway.record_invoice_payment(
            invoice.id, invoice.total, invoice.currency,
            note=f"PayPal payout item {result.item_id}, batch {batch_id}, deal {deal.id}",
        )
    except PayPalError as e:
        # Money moved; only the bookkeeping failed. Keep SUCCESS but note the error.
        _update(conn, pid, error=f"record on invoice: {e}")
        store.set_run_outcome(conn, deal.run_id, "paid")
        log("invoice", f"could not record payment on {invoice.id}: {e}")
        return Payment(pid, deal.id, "SUCCESS", batch_id, result.item_id, error=str(e))
    _update(conn, pid, invoice_payment_id=inv_payment_id)
    log("invoice", f"{invoice.id} → {inv_status}")

    conn.execute("UPDATE inventory SET last_paid_unit_price = ? WHERE item = ?", (str(deal.unit_price), deal.item))
    store.set_run_outcome(conn, deal.run_id, "paid")
    return Payment(pid, deal.id, "SUCCESS", batch_id, result.item_id, inv_payment_id, inv_status)
