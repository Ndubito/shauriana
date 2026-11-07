"""End-to-end restock flow against a fake PayPal gateway (no network)."""
from decimal import Decimal

import pytest

from app import db, seed
from app.models import Invoice
from app.paypal.gateway import PayoutResult
from app.policy.rules import from_dict
from app.restock import run_restock
from app.shop.tools import request_payment

SUPPLIER = "supplier@business.example.com"
SHOP = "shop@business.example.com"
RULES = from_dict({"version": "test", "currency": "USD", "approved_payees": [SUPPLIER],
                   "max_per_order": 150, "max_per_week": 400, "require_approval_above": 80,
                   "max_unit_price_increase_pct": 20, "approval_timeout_hours": 24})


class FakeGateway:
    def __init__(self, invoice_payee=SUPPLIER, invoice_total_delta=Decimal("0"), payout_status="SUCCESS"):
        self.invoice_payee = invoice_payee
        self.delta = invoice_total_delta
        self.payout_status = payout_status
        self.payouts = []
        self.recorded = []

    def issue_invoice(self, deal, recipient_email):
        return Invoice(id=f"INV2-{deal.id}", total=deal.total + self.delta, currency=deal.currency,
                       payee_email=self.invoice_payee)

    def send_payout(self, deal_id, receiver_email, amount, currency, note):
        self.payouts.append((deal_id, receiver_email, amount))
        return f"BATCH-{deal_id}"

    def wait_payout(self, batch_id):
        return PayoutResult(batch_id, "ITEM-1", self.payout_status, "TXN-1")

    def record_invoice_payment(self, invoice_id, amount, currency, note):
        self.recorded.append((invoice_id, amount, note))
        return "EXTR-1", "MARKED_AS_PAID"


@pytest.fixture
def seed_data(monkeypatch):
    monkeypatch.setenv("SUPPLIER_SANDBOX_EMAIL", SUPPLIER)
    return seed.load_seed_file()


@pytest.fixture
def conn():
    return db.connect(":memory:")


def outcome(conn, deal_id):
    return conn.execute("SELECT r.outcome FROM runs r JOIN deals d ON d.run_id = r.id WHERE d.id = ?",
                        (deal_id,)).fetchone()[0]


def test_scenario_a_pays_and_records(conn, seed_data):
    scenario = seed.apply_scenario(conn, seed_data, "a")
    gw = FakeGateway()
    res = run_restock(conn, scenario, rules=RULES, gateway=gw, shop_email=SHOP).payment_request

    assert res.decision.status == "approved"
    assert res.payment.ok
    assert gw.payouts == [(res.decision.deal_id, SUPPLIER, Decimal("55.00"))]
    assert gw.recorded and "ITEM-1" in gw.recorded[0][2]
    assert outcome(conn, res.decision.deal_id) == "paid"
    assert conn.execute("SELECT last_paid_unit_price FROM inventory WHERE item='packaging_pouch_100g'"
                        ).fetchone()[0] == "0.11"
    checks = conn.execute("SELECT checks FROM decisions").fetchone()[0]
    assert checks.count('"passed": true') == 7
    assert conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 3


def test_second_request_payment_on_same_deal_is_blocked_without_paypal_call(conn, seed_data):
    scenario = seed.apply_scenario(conn, seed_data, "a")
    gw = FakeGateway()
    first = run_restock(conn, scenario, rules=RULES, gateway=gw, shop_email=SHOP).payment_request
    second = request_payment(conn, first.decision.deal_id, rules=RULES, gateway=gw)

    assert (second.decision.status, second.decision.rule) == ("blocked", "duplicate")
    assert second.payment is None
    assert len(gw.payouts) == 1
    # The rejected repeat is recorded, but the run stays paid.
    assert outcome(conn, first.decision.deal_id) == "paid"
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2


def test_payee_swap_on_invoice_is_blocked_and_no_money_moves(conn, seed_data):
    scenario = seed.apply_scenario(conn, seed_data, "a")
    gw = FakeGateway(invoice_payee="fraud@example.com")
    res = run_restock(conn, scenario, rules=RULES, gateway=gw, shop_email=SHOP).payment_request
    assert (res.decision.status, res.decision.rule) == ("blocked", "payee_allowlist")
    assert gw.payouts == []
    assert outcome(conn, res.decision.deal_id) == "blocked"


def test_failed_payout_marks_run_failed(conn, seed_data):
    scenario = seed.apply_scenario(conn, seed_data, "a")
    res = run_restock(conn, scenario, rules=RULES, gateway=FakeGateway(payout_status="UNCLAIMED"),
                      shop_email=SHOP).payment_request
    assert res.payment.status == "UNCLAIMED" and not res.payment.ok
    assert outcome(conn, res.decision.deal_id) == "failed"
    # last paid price is untouched when money didn't arrive
    assert conn.execute("SELECT last_paid_unit_price FROM inventory WHERE item='packaging_pouch_100g'"
                        ).fetchone()[0] == "0.10"


def test_no_run_when_stock_is_above_reorder_level(conn, seed_data):
    from app.restock import RestockError
    seed.reset(conn, seed_data)  # pouch stock 250, reorder at 100
    with pytest.raises(RestockError):
        run_restock(conn, seed_data["scenarios"]["a"] | {"set_stock": {}}, rules=RULES,
                       gateway=FakeGateway(), shop_email=SHOP)
