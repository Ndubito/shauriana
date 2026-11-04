"""Payouts API v1 calls, made with the shop's credentials.

Checked against https://developer.paypal.com/api/payments.payouts-batch/v1/schema.json (Nov 4, 2026).

We deliberately do NOT send a PayPal-Request-Id header: with it, PayPal treats a repeat
call as a retry and returns the original batch. Without it, a reused sender_batch_id is
rejected, which is our guard against paying the same deal twice.
"""
from __future__ import annotations

import time
from decimal import Decimal

from app.paypal.client import PayPalClient
from app.paypal.money import fmt

# Item states that will not change on their own within a demo run.
TERMINAL_ITEM_STATUSES = {
    "SUCCESS", "FAILED", "UNCLAIMED", "RETURNED", "BLOCKED", "REFUNDED", "REVERSED",
}
TERMINAL_BATCH_STATUSES = {"DENIED", "CANCELED"}


class PayoutTimeout(RuntimeError):
    pass


def create_payout(
    client: PayPalClient,
    *,
    sender_batch_id: str,
    receiver_email: str,
    amount: Decimal,
    currency: str = "USD",
    note: str,
    email_subject: str = "Payment for your invoice",
) -> dict:
    """POST /v1/payments/payouts with a single EMAIL item. Returns the 201 body."""
    body = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": email_subject,
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": receiver_email,
                "amount": {"currency": currency, "value": fmt(amount)},
                "note": note,
                "sender_item_id": sender_batch_id,
            }
        ],
    }
    return client.request("POST", "/v1/payments/payouts", json=body)


def get_payout(client: PayPalClient, payout_batch_id: str) -> dict:
    """GET /v1/payments/payouts/{payout_batch_id}."""
    return client.request("GET", f"/v1/payments/payouts/{payout_batch_id}")


def wait_for_payout(
    client: PayPalClient,
    payout_batch_id: str,
    *,
    interval: float = 2.5,
    timeout: float = 120.0,
    on_poll=None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> dict:
    """Poll until the single item reaches a terminal status. Returns that item.

    Raises PayoutTimeout if it is still PENDING/ONHOLD after `timeout` seconds.
    """
    deadline = clock() + timeout
    while True:
        batch = get_payout(client, payout_batch_id)
        batch_status = batch.get("batch_header", {}).get("batch_status")
        items = batch.get("items") or []
        item = items[0] if items else {}
        item_status = item.get("transaction_status")
        if on_poll:
            on_poll(batch_status, item_status)
        if item_status in TERMINAL_ITEM_STATUSES:
            return item
        if batch_status in TERMINAL_BATCH_STATUSES:
            return {**item, "transaction_status": item_status or batch_status}
        if clock() >= deadline:
            raise PayoutTimeout(
                f"Payout {payout_batch_id} still batch={batch_status} item={item_status} "
                f"after {timeout:.0f}s"
            )
        sleep(interval)
