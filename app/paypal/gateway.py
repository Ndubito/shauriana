"""The PayPal operations the app uses, behind one object so tests can swap in a fake.

Shop side (shop credentials): send_payout, wait_payout.
Supplier side (supplier credentials): issue_invoice, record_invoice_payment.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.config import Settings
from app.models import Deal, Invoice
from app.paypal import invoicing, payouts
from app.paypal.client import PayPalClient


@dataclass(frozen=True)
class PayoutResult:
    batch_id: str
    item_id: str | None
    status: str  # PayPal item transaction_status
    transaction_id: str | None


class PayPalGateway:
    def __init__(self, settings: Settings, on_poll=None):
        self.settings = settings
        self.on_poll = on_poll
        self.shop = PayPalClient(settings.base_url, settings.shop.client_id, settings.shop.client_secret)
        self.supplier = PayPalClient(settings.base_url, settings.supplier.client_id,
                                     settings.supplier.client_secret)

    # ---- supplier side ----
    def issue_invoice(self, deal: Deal, recipient_email: str) -> Invoice:
        """Create and send the invoice for a deal. Returns what the invoice says."""
        inv = invoicing.create_invoice(
            self.supplier,
            invoicer_email=self.settings.supplier.email,
            recipient_email=recipient_email,
            items=[invoicing.LineItem(name=deal.item, quantity=deal.qty, unit_price=deal.unit_price)],
            currency=deal.currency,
            reference=deal.id,
        )
        invoicing.send_invoice(self.supplier, inv["id"])
        return Invoice(
            id=inv["id"],
            total=Decimal(inv["amount"]["value"]),
            currency=inv["amount"]["currency_code"],
            payee_email=inv.get("invoicer", {}).get("email_address", self.settings.supplier.email),
        )

    def record_invoice_payment(self, invoice_id: str, amount: Decimal, currency: str, note: str) -> tuple[str, str]:
        """Record the payout on the invoice. Returns (invoice payment id, invoice status)."""
        ref = invoicing.record_payment(self.supplier, invoice_id, amount=amount, currency=currency, note=note)
        status = invoicing.get_invoice(self.supplier, invoice_id).get("status", "")
        return ref.get("payment_id", ""), status

    # ---- shop side ----
    def send_payout(self, deal_id: str, receiver_email: str, amount: Decimal, currency: str, note: str) -> str:
        created = payouts.create_payout(self.shop, sender_batch_id=deal_id, receiver_email=receiver_email,
                                        amount=amount, currency=currency, note=note)
        return created["batch_header"]["payout_batch_id"]

    def wait_payout(self, batch_id: str) -> PayoutResult:
        item = payouts.wait_for_payout(self.shop, batch_id, on_poll=self.on_poll)
        return PayoutResult(batch_id, item.get("payout_item_id"), item.get("transaction_status", ""),
                            item.get("transaction_id"))

    def close(self) -> None:
        self.shop.close()
        self.supplier.close()
