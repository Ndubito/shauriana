"""Small read/write helpers for runs, offers, deals and decisions."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from app.models import Deal, Decision, Invoice, iso, utcnow
from app.policy.rules import Rules


def set_run_outcome(conn: sqlite3.Connection, run_id: str, outcome: str) -> None:
    conn.execute("UPDATE runs SET outcome = ? WHERE id = ?", (outcome, run_id))


OPEN_RUN_OUTCOMES = ("started", "deal")


def set_open_run_outcome(conn: sqlite3.Connection, run_id: str, outcome: str) -> None:
    """Set the outcome only if the run is still open. A rejected repeat request must not
    turn a paid run into 'blocked'; that attempt is still recorded in `decisions`."""
    marks = ",".join("?" * len(OPEN_RUN_OUTCOMES))
    conn.execute(f"UPDATE runs SET outcome = ? WHERE id = ? AND outcome IN ({marks})",
                 (outcome, run_id, *OPEN_RUN_OUTCOMES))


def save_deal(conn: sqlite3.Connection, deal: Deal) -> None:
    conn.execute(
        "INSERT INTO deals (id, run_id, item, qty, unit_price, total, currency, delivery_date,"
        " payee_email, rationale, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (deal.id, deal.run_id, deal.item, deal.qty, str(deal.unit_price), str(deal.total), deal.currency,
         deal.delivery_date, deal.payee_email, deal.rationale, iso(utcnow())),
    )


def attach_invoice(conn: sqlite3.Connection, deal_id: str, invoice: Invoice) -> None:
    conn.execute(
        "UPDATE deals SET invoice_id = ?, invoice_total = ?, invoice_payee = ? WHERE id = ?",
        (invoice.id, str(invoice.total), invoice.payee_email, deal_id),
    )


def load_deal(conn: sqlite3.Connection, deal_id: str) -> tuple[Deal, Invoice | None]:
    r = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    if r is None:
        raise KeyError(f"No deal {deal_id}")
    deal = Deal(id=r["id"], run_id=r["run_id"], item=r["item"], qty=r["qty"],
                unit_price=Decimal(r["unit_price"]), total=Decimal(r["total"]), currency=r["currency"],
                delivery_date=r["delivery_date"], payee_email=r["payee_email"], rationale=r["rationale"] or "")
    invoice = None
    if r["invoice_id"]:
        invoice = Invoice(id=r["invoice_id"], total=Decimal(r["invoice_total"]), currency=r["currency"],
                          payee_email=r["invoice_payee"])
    return deal, invoice


def save_decision(conn: sqlite3.Connection, decision: Decision, rules: Rules) -> int:
    cur = conn.execute(
        "INSERT INTO decisions (deal_id, status, rule, reason, checks, policy_version, rules_in_force,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (decision.deal_id, decision.status, decision.rule, decision.reason,
         json.dumps([{"rule": c.rule, "passed": c.passed} for c in decision.checks]),
         decision.policy_version, json.dumps(rules.snapshot()), iso(utcnow())),
    )
    return cur.lastrowid
