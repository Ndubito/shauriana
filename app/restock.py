"""One restock attempt (a "run"): low stock → deal → invoice → request_payment.

The deal comes from the LLM negotiation, or from the scenario's fixed deal when no
model is given (`--fixed`, the demo safety net). Everything after the deal is the same.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app import store
from app.agents.negotiator import ShopAgent, SupplierAgent
from app.models import Deal, iso, utcnow
from app.negotiation.loop import AgreedTerms, NoDeal, negotiate
from app.negotiation.offers import ShopLimits, SupplierLimits
from app.paypal.client import PayPalError
from app.policy.rules import Rules
from app.shop.tools import PaymentRequestResult, request_payment

FIXED_RATIONALE = "Fixed deal (no negotiation)."


class RestockError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunResult:
    run_id: str
    payment_request: PaymentRequestResult | None = None
    no_deal_reason: str | None = None


def new_run_id() -> str:
    # Deal IDs become PayPal sender_batch_ids, which PayPal never lets you reuse, even
    # after our DB is reset. So IDs carry a timestamp and random suffix, not a counter.
    return f"{utcnow():%m%d%H%M%S}_{secrets.token_hex(2)}"


def limits_for(inv: sqlite3.Row, sup: sqlite3.Row, today: date) -> tuple[ShopLimits, SupplierLimits]:
    item = inv["item"]
    shop = ShopLimits(
        max_unit_price=Decimal(inv["max_unit_price"]),
        min_qty=max(1, inv["reorder_level"] - inv["stock"]),
        max_qty=inv["reorder_qty"],
        latest_delivery=today + timedelta(days=inv["latest_delivery_days"]),
    )
    supplier = SupplierLimits(
        floor_price=Decimal(json.loads(sup["floor_prices"])[item]),
        max_qty=sup["max_qty"],
        earliest_delivery=today + timedelta(days=sup["lead_days"]),
    )
    return shop, supplier


def _fixed_terms(conn, run_id, item, scenario, sup, today) -> AgreedTerms:
    fixed = scenario["fixed_deal"]
    qty, unit = int(fixed["qty"]), Decimal(str(fixed["unit_price"]))
    delivery = (today + timedelta(days=sup["lead_days"])).isoformat()
    for rnd, side, action, price in ((1, "shop", "request", None), (1, "supplier", "quote", unit),
                                     (2, "shop", "accept", unit)):
        conn.execute(
            "INSERT INTO offers (run_id, round, from_side, action, item, qty, unit_price, delivery_date,"
            " rationale, valid, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (run_id, rnd, side, action, item, qty, str(price) if price is not None else None, delivery,
             FIXED_RATIONALE, iso(utcnow())),
        )
    return AgreedTerms(qty, unit, delivery, "shop", FIXED_RATIONALE)


def run_restock(conn: sqlite3.Connection, scenario: dict, *, rules: Rules, gateway, shop_email: str,
                llm=None, max_rounds: int = 4, log=lambda *a: None) -> RunResult:
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

    today = utcnow().date()
    if llm is None:
        terms = _fixed_terms(conn, run_id, item, scenario, sup, today)
        log("deal", "fixed deal (no negotiation)")
    else:
        shop_limits, supplier_limits = limits_for(inv, sup, today)
        shop = ShopAgent(llm, shop_name="the shop", item=item, unit=inv["unit"], limits=shop_limits,
                         requested_qty=inv["reorder_qty"], last_paid=inv["last_paid_unit_price"],
                         stock=inv["stock"], reorder_level=inv["reorder_level"])
        supplier = SupplierAgent(llm, supplier_name=sup["name"], item=item, unit=inv["unit"],
                                 limits=supplier_limits, list_price=json.loads(sup["list_prices"])[item])
        terms = negotiate(conn, run_id=run_id, item=item, request_qty=inv["reorder_qty"], shop=shop,
                          supplier=supplier, shop_limits=shop_limits, supplier_limits=supplier_limits,
                          max_rounds=max_rounds, today=today, log=log)
        if isinstance(terms, NoDeal):
            store.set_run_outcome(conn, run_id, "no_deal")
            log("deal", f"NO DEAL: {terms.reason}")
            return RunResult(run_id, no_deal_reason=terms.reason)

    deal = Deal(id=deal_id, run_id=run_id, item=item, qty=terms.qty, unit_price=terms.unit_price,
                total=(terms.unit_price * terms.qty).quantize(Decimal("0.01")), currency=rules.currency,
                delivery_date=terms.delivery_date, payee_email=sup["paypal_email"], rationale=terms.rationale)
    store.save_deal(conn, deal)
    store.set_run_outcome(conn, run_id, "deal")
    log("deal", f"{deal_id}: {deal.qty} × {item} @ ${deal.unit_price} = ${deal.total} {deal.currency}")

    try:
        invoice = gateway.issue_invoice(deal, shop_email)
    except PayPalError as e:
        store.set_run_outcome(conn, run_id, "failed")
        raise RestockError(f"Supplier could not issue invoice: {e}") from e
    store.attach_invoice(conn, deal_id, invoice)
    log("invoice", f"{invoice.id} (${invoice.total:.2f}) created → sent to {shop_email}")

    return RunResult(run_id, payment_request=request_payment(conn, deal_id, rules=rules, gateway=gateway, log=log))
