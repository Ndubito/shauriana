"""Invoicing API v2 calls, made with the supplier's credentials.

Checked against https://developer.paypal.com/api/invoicing/v2/schema.json (Nov 4, 2026).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.paypal.client import PayPalClient
from app.paypal.money import fmt

# Statuses that mean the supplier has the money on this invoice.
PAID_STATUSES = {"PAID", "MARKED_AS_PAID", "PAID_EXTERNAL"}


@dataclass(frozen=True)
class LineItem:
    name: str
    quantity: int
    unit_price: Decimal
    unit_of_measure: str = "QUANTITY"


def create_invoice(
    client: PayPalClient,
    *,
    invoicer_email: str,
    recipient_email: str,
    items: list[LineItem],
    currency: str = "USD",
    reference: str | None = None,
    note: str | None = None,
) -> dict:
    """POST /v2/invoicing/invoices. Returns the full draft invoice (needs Prefer header)."""
    detail: dict = {"currency_code": currency}
    if reference:
        detail["reference"] = reference
    if note:
        detail["note"] = note
    body = {
        "detail": detail,
        "invoicer": {"email_address": invoicer_email},
        "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
        "items": [
            {
                "name": it.name,
                "quantity": str(it.quantity),
                "unit_amount": {"currency_code": currency, "value": fmt(it.unit_price)},
                "unit_of_measure": it.unit_of_measure,
            }
            for it in items
        ],
    }
    return client.request(
        "POST",
        "/v2/invoicing/invoices",
        json=body,
        headers={"Prefer": "return=representation"},
    )


def send_invoice(client: PayPalClient, invoice_id: str) -> dict:
    """POST /v2/invoicing/invoices/{invoice_id}/send. Moves the invoice from DRAFT to SENT."""
    return client.request(
        "POST",
        f"/v2/invoicing/invoices/{invoice_id}/send",
        json={"send_to_invoicer": False, "send_to_recipient": True},
    )


def get_invoice(client: PayPalClient, invoice_id: str) -> dict:
    """GET /v2/invoicing/invoices/{invoice_id}."""
    return client.request("GET", f"/v2/invoicing/invoices/{invoice_id}")


def record_payment(
    client: PayPalClient,
    invoice_id: str,
    *,
    amount: Decimal,
    currency: str = "USD",
    note: str,
    method: str = "PAYPAL",
    payment_date: date | None = None,
) -> dict:
    """POST /v2/invoicing/invoices/{invoice_id}/payments. Returns {"payment_id": ...}."""
    body = {
        "method": method,
        "payment_date": (payment_date or date.today()).isoformat(),
        "amount": {"currency_code": currency, "value": fmt(amount)},
        "note": note,
    }
    return client.request("POST", f"/v2/invoicing/invoices/{invoice_id}/payments", json=body)
