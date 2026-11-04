"""Go/no-go test: the payment path end to end in the PayPal sandbox. No AI.

Runs spec steps 1, 2, 4, 5 and 6 for one fixed deal (Scenario A: 500 pouches at $0.11):
  1. Supplier creates an invoice addressed to the shop
  2. Supplier sends the invoice
  4. Shop sends a payout to the supplier for the invoice total (sender_batch_id = deal ID)
  5. Shop polls the payout until the item reaches a final status
  6. Supplier records the payment on the invoice, payout item ID in the note

Usage:
  python -m scripts.gonogo                     # fresh deal ID, full run
  python -m scripts.gonogo --deal-id deal_X    # reuse an ID (PayPal should reject a repeat)
"""
from __future__ import annotations

import argparse
import sys
import time
from decimal import Decimal

from app.config import ConfigError, load_settings
from app.paypal import invoicing, payouts
from app.paypal.client import PayPalClient, PayPalError
from app.paypal.money import fmt

ITEM = invoicing.LineItem(name="packaging_pouch_100g", quantity=500, unit_price=Decimal("0.11"))
CURRENCY = "USD"


def log(tag: str, msg: str) -> None:
    print(f"[{tag:<7}] {msg}", flush=True)


def fail(msg: str, err: Exception | None = None) -> int:
    print(f"\nNO-GO ❌  {msg}")
    if isinstance(err, PayPalError):
        print(f"          {err.status} {err.name}: {err.message}")
        if err.details:
            print(f"          details: {err.details}")
        print(f"          debug_id: {err.debug_id}")
    elif err:
        print(f"          {err}")
    return 1


def run(deal_id: str) -> int:
    try:
        cfg = load_settings()
    except ConfigError as e:
        return fail("configuration", e)

    total = ITEM.unit_price * ITEM.quantity
    log("deal", f"{deal_id}: {ITEM.quantity} × {ITEM.name} @ ${fmt(ITEM.unit_price)} = ${fmt(total)} {CURRENCY}")

    shop = PayPalClient(cfg.base_url, cfg.shop.client_id, cfg.shop.client_secret)
    supplier = PayPalClient(cfg.base_url, cfg.supplier.client_id, cfg.supplier.client_secret)
    try:
        # OAuth for both apps
        try:
            shop.tokens.get()
            supplier.tokens.get()
        except PayPalError as e:
            return fail("OAuth token request failed", e)
        log("oauth", f"shop token ok · supplier token ok ({cfg.paypal_env})")

        # Steps 1 and 2: supplier invoices the shop
        try:
            inv = invoicing.create_invoice(
                supplier,
                invoicer_email=cfg.supplier.email,
                recipient_email=cfg.shop.email,
                items=[ITEM],
                currency=CURRENCY,
                reference=deal_id,
            )
            invoice_id = inv["id"]
            invoice_total = Decimal(inv["amount"]["value"])
            log("invoice", f"created {invoice_id} (${fmt(invoice_total)}, {inv.get('status')})")
            invoicing.send_invoice(supplier, invoice_id)
            log("invoice", f"sent {invoice_id} to {cfg.shop.email}")
        except PayPalError as e:
            return fail("invoice create/send failed", e)

        if invoice_total != total:
            return fail(f"invoice total ${fmt(invoice_total)} != deal total ${fmt(total)}")

        # Step 4: shop pays the invoice total via Payouts
        try:
            created = payouts.create_payout(
                shop,
                sender_batch_id=deal_id,
                receiver_email=cfg.supplier.email,
                amount=invoice_total,
                currency=CURRENCY,
                note=f"Payment for invoice {invoice_id} ({deal_id})",
            )
        except PayPalError as e:
            return fail("payout create failed (a reused deal ID should fail here)", e)
        batch_id = created["batch_header"]["payout_batch_id"]
        log("payout", f"batch {batch_id} created (sender_batch_id={deal_id}, "
                      f"{created['batch_header'].get('batch_status')})")

        # Step 5: poll until final
        try:
            item = payouts.wait_for_payout(
                shop,
                batch_id,
                on_poll=lambda b, i: log("payout", f"batch={b} item={i}"),
            )
        except (PayPalError, payouts.PayoutTimeout) as e:
            return fail("payout did not finish", e)
        status = item.get("transaction_status")
        item_id = item.get("payout_item_id")
        if status != "SUCCESS":
            hint = ""
            if status == "UNCLAIMED":
                hint = " — SUPPLIER_SANDBOX_EMAIL is probably not a sandbox account email"
            return fail(f"payout item {item_id} ended {status}{hint}")
        log("payout", f"SUCCESS item {item_id} (txn {item.get('transaction_id')})")

        # Step 6: supplier records the payment on the invoice
        try:
            ref = invoicing.record_payment(
                supplier,
                invoice_id,
                amount=invoice_total,
                currency=CURRENCY,
                note=f"PayPal payout item {item_id}, batch {batch_id}, deal {deal_id}",
            )
            final = invoicing.get_invoice(supplier, invoice_id)
        except PayPalError as e:
            return fail("record payment on invoice failed", e)
        inv_status = final.get("status")
        log("invoice", f"recorded payment {ref.get('payment_id')} → status {inv_status}")
        if inv_status not in invoicing.PAID_STATUSES:
            return fail(f"invoice {invoice_id} status is {inv_status}, expected one of {sorted(invoicing.PAID_STATUSES)}")

        print(f"\nGO ✅  invoice={invoice_id} payout_batch={batch_id} payout_item={item_id}")
        return 0
    finally:
        shop.close()
        supplier.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deal-id", default=f"deal_gonogo_{int(time.time())}")
    args = parser.parse_args()
    # Windows consoles default to cp1252; make ✅/× print.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return run(args.deal_id)


if __name__ == "__main__":
    sys.exit(main())
