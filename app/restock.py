"""One restock attempt (a "run") with a fixed deal. The LLM negotiation replaces
`fixed_deal` in the next feature; everything after the deal stays the same."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta
from decimal import Decimal

from app import store
from app.models import Deal, iso, utcnow
from app.paypal.client import PayPalError
from app.policy.rules import Rules
from app.shop.tools import PaymentRequestResult, request_payment


class RestockError(RuntimeError):
    pass


def new_run_id() -> str:
    # Deal IDs become PayPal sender_batch_ids, which PayPal never lets you reuse, even
    # after our DB is reset. So IDs carry a timestamp and random suffix, not a counter.
    return f"{utcnow():%m%d%H%M%S}_{secrets.token_hex(2)}"


def _offer(conn, run_id, rnd, side, action, item, qty, unit_price, delivery_date, rationale):
    conn.execute(
        "INSERT INTO offers (run_id, round, from_side, action, item, qty, unit_price, delivery_date,"
        " rationale, valid, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (run_id, rnd, side, action, item, qty, unit_price, delivery_date, rationale, iso(utcnow())),
    )


def run_fixed_deal(conn: sqlite3.Connection, scenario: dict, *, rules: Rules, gateway, shop_email: str,
                   log=lambda *a: None) -> PaymentRequestResult:
    item = scenario["item"]
    inv = conn.execute("SELECT * FROM inventory WHERE item = ?", (item,)).fetchone()
    sup = conn.execute("SELECT * FROM suppliers WHERE id = ?", (scenario["supplier_id"],)).fetchone()
    if inv is None or sup is None:
        raise RestockError(f"Unknown item {item!r} or supplier {scenario['supplier_id']!r}")
    if inv["stock"] >= inv["reorder_level"]:
        raise RestockError(f"{item} stock {inv['stock']} is not below reorder level {inv['reorder_level']}")
    log("stock", f"{item} at {inv['stock']} (reorder at {inv['reorder_level']}) → restock")

    suffix = new_run_id()
    run_id, deal_id = f"run_{suffix}", f"deal_{suffix}"
    conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, 'started')", (run_id, item, sup["id"], iso(utcnow())))
    log("run", f"{run_id} started")

    fixed = scenario["fixed_deal"]
    qty, unit = int(fixed["qty"]), Decimal(str(fixed["unit_price"]))
    delivery = (utcnow() + timedelta(days=sup["lead_days"])).date().isoformat()
    _offer(conn, run_id, 1, "shop", "request", item, qty, None, delivery, "Fixed deal (pre-LLM build).")
    _offer(conn, run_id, 1, "supplier", "quote", item, qty, str(unit), delivery, "Fixed deal (pre-LLM build).")
    _offer(conn, run_id, 2, "shop", "accept", item, qty, str(unit), delivery, "Fixed deal (pre-LLM build).")

    deal = Deal(id=deal_id, run_id=run_id, item=item, qty=qty, unit_price=unit, total=unit * qty,
                currency=rules.currency, delivery_date=delivery, payee_email=sup["paypal_email"],
                rationale="Fixed deal (pre-LLM build).")
    store.save_deal(conn, deal)
    store.set_run_outcome(conn, run_id, "deal")
    log("deal", f"{deal_id}: {qty} × {item} @ ${unit} = ${deal.total:.2f} {deal.currency}")

    try:
        invoice = gateway.issue_invoice(deal, shop_email)
    except PayPalError as e:
        store.set_run_outcome(conn, run_id, "failed")
        raise RestockError(f"Supplier could not issue invoice: {e}") from e
    store.attach_invoice(conn, deal_id, invoice)
    log("invoice", f"{invoice.id} (${invoice.total:.2f}) created → sent to {shop_email}")

    return request_payment(conn, deal_id, rules=rules, gateway=gateway, log=log)
