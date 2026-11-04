"""Offline tests for the PayPal helpers (no network)."""
from decimal import Decimal

import httpx
import pytest

from app.paypal import payouts
from app.paypal.auth import TokenProvider
from app.paypal.client import PayPalError
from app.paypal.money import fmt


def test_fmt_rounds_to_cents():
    assert fmt(Decimal("0.11") * 500) == "55.00"
    assert fmt(0.135) == "0.14"
    assert fmt("102") == "102.00"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_token_is_cached_until_expiry():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_in": 3600})

    clock = FakeClock()
    tp = TokenProvider("https://x", "id", "secret", httpx.Client(transport=httpx.MockTransport(handler)), clock)
    assert tp.get() == "tok1"
    clock.now = 3000
    assert tp.get() == "tok1"  # still inside lifetime minus margin
    clock.now = 3541
    assert tp.get() == "tok2"  # within 60s of expiry -> refreshed
    assert len(calls) == 2
    assert calls[0].headers["authorization"].startswith("Basic ")


def test_token_error_raises_paypal_error():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "Client Authentication failed"})

    tp = TokenProvider("https://x", "id", "bad", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PayPalError) as exc:
        tp.get()
    assert exc.value.name == "invalid_client"


class FakeClient:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    def request(self, method, path, **kw):
        s = self.statuses.pop(0)
        return {"batch_header": {"batch_status": "PROCESSING"},
                "items": [{"payout_item_id": "ITEM1", "transaction_status": s}]}


def test_wait_for_payout_polls_until_terminal():
    client = FakeClient(["PENDING", "PENDING", "SUCCESS"])
    seen = []
    item = payouts.wait_for_payout(client, "B1", sleep=lambda s: None, on_poll=lambda b, i: seen.append(i))
    assert item["transaction_status"] == "SUCCESS"
    assert seen == ["PENDING", "PENDING", "SUCCESS"]


def test_wait_for_payout_stops_on_unclaimed():
    item = payouts.wait_for_payout(FakeClient(["UNCLAIMED"]), "B1", sleep=lambda s: None)
    assert item["transaction_status"] == "UNCLAIMED"


def test_wait_for_payout_times_out():
    clock = FakeClock()

    def sleep(s):
        clock.now += s

    with pytest.raises(payouts.PayoutTimeout):
        payouts.wait_for_payout(FakeClient(["PENDING"] * 100), "B1", interval=10, timeout=30,
                                sleep=sleep, clock=clock)
