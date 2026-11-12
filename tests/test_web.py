"""Web API tests: FastAPI TestClient, fake PayPal gateway, jobs run inline (no network)."""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import db, seed
from app.models import iso, utcnow
from app.runner import Runner
from app.web.main import create_app
from tests.test_restock_flow import RULES, SHOP, SUPPLIER, FakeGateway


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPPLIER_SANDBOX_EMAIL", SUPPLIER)
    path = str(tmp_path / "web.db")
    data = seed.load_seed_file()
    c = db.connect(path)
    seed.reset(c, data)
    c.close()
    gateways = []

    def factory():
        gw = FakeGateway()
        gateways.append(gw)
        return gw

    runner = Runner(db_path=path, rules=RULES, seed_data=data, gateway_factory=factory, shop_email=SHOP,
                    llm=None, synchronous=True)
    client = TestClient(create_app(runner, llm_label="fixed deals (test)"))
    return client, runner, gateways, path


def payouts(gateways):
    return [p for gw in gateways for p in gw.payouts]


def test_page_renders_with_policy_and_scenarios(env):
    client, *_ = env
    html = client.get("/").text
    assert "Restocking Agents" in html and "ag-grid-community@36.2.0" in html
    assert SUPPLIER in html and "No single order above $150.00" in html
    for s in ("Routine restock", "Large order", "Price spike"):
        assert s in html
    for view in ("view-activity", "view-inventory", "view-policy", 'href="#inventory"', 'href="#policy"'):
        assert view in html


def test_inventory_and_policy(env):
    client, *_ = env
    inv = client.get("/inventory").json()
    assert {r["item"] for r in inv} == {"packaging_pouch_100g", "plantain_kg", "cooking_oil_l"}
    assert not any(r["low"] for r in inv)
    pol = client.get("/policy").json()
    assert pol["max_per_order"] == 150 and pol["approved_payees"] == [SUPPLIER]


def test_latest_run_empty_before_any_run(env):
    client, *_ = env
    body = client.get("/runs/latest").json()
    assert body["detail"] is None and body["runner"]["busy"] is False


def test_scenario_a_auto_approved_and_paid(env):
    client, _, gateways, _ = env
    assert client.post("/scenarios/a").status_code == 202
    d = client.get("/runs/latest").json()["detail"]
    assert d["stage"] == "paid" and d["decision"]["status"] == "approved"
    assert d["deal"]["total"] == 55.0 and d["payment"]["status"] == "SUCCESS"
    assert [o["action"] for o in d["offers"]] == ["request", "quote", "accept"]
    assert payouts(gateways)[0][2] == Decimal("55.00")

    rec = client.get(f"/runs/{d['run']['id']}/authorization").json()
    assert rec["decision"] == "approved" and rec["authorized_by"]["type"] == "policy"
    assert rec["rules_in_force"] == {"max_per_order": 150.0, "max_per_week": 400.0,
                                     "require_approval_above": 80.0, "max_unit_price_increase_pct": 20.0}
    assert [c["rule"] for c in rec["checks"]][-1] == "approval_threshold" and all(c["passed"] for c in rec["checks"])
    assert rec["paypal"]["payout_batch_id"] == f"BATCH-{d['deal']['id']}" and rec["paypal"]["status"] == "SUCCESS"
    assert rec["deal"] == {"deal_id": d["deal"]["id"], "supplier": SUPPLIER, "item": "packaging_pouch_100g",
                           "qty": 500, "unit_price": 0.11, "total": 55.0, "currency": "USD",
                           "delivery_date": d["deal"]["delivery_date"]}


def test_scenario_b_held_then_owner_approves_and_pays(env):
    client, _, gateways, _ = env
    client.post("/scenarios/b")
    d = client.get("/runs/latest").json()["detail"]
    assert d["stage"] == "awaiting_owner" and d["decision"]["status"] == "needs_approval"
    assert d["deal"]["total"] == 102.0
    assert payouts(gateways) == []

    queue = client.get("/approvals?status=pending").json()
    assert len(queue) == 1 and queue[0]["amount"] == 102.0 and "above $80.00" in queue[0]["reason"]

    assert client.post(f"/approvals/{queue[0]['id']}", json={"approve": True}).status_code == 202
    d = client.get("/runs/latest").json()["detail"]
    assert d["stage"] == "paid" and d["approval"]["status"] == "approved"
    assert len(payouts(gateways)) == 1
    assert client.get("/approvals?status=pending").json() == []

    rec = client.get(f"/runs/{d['run']['id']}/authorization").json()
    assert rec["decision"] == "needs_approval" and rec["authorized_by"]["type"] == "owner"
    assert rec["checks"][-1] == {"rule": "approval_threshold", "passed": False}

    # Approving again is refused, and nothing is paid twice.
    again = client.post(f"/approvals/{queue[0]['id']}", json={"approve": True})
    assert again.status_code == 409 and "already approved" in again.json()["detail"]
    assert len(payouts(gateways)) == 1


def test_scenario_b_deny_means_no_payment(env):
    client, _, gateways, _ = env
    client.post("/scenarios/b")
    aid = client.get("/approvals?status=pending").json()[0]["id"]
    assert client.post(f"/approvals/{aid}", json={"approve": False}).status_code == 202
    d = client.get("/runs/latest").json()["detail"]
    assert d["stage"] == "denied" and d["payment"] is None and payouts(gateways) == []
    rec = client.get(f"/runs/{d['run']['id']}/authorization").json()
    assert rec["authorized_by"] is None and rec["paypal"] is None and rec["approval"]["status"] == "denied"


def test_scenario_c_blocked_by_price_spike(env):
    client, _, gateways, _ = env
    client.post("/scenarios/c")
    d = client.get("/runs/latest").json()["detail"]
    assert d["stage"] == "blocked"
    assert (d["decision"]["status"], d["decision"]["rule"]) == ("blocked", "price_spike")
    assert d["decision"]["reason"] == "Unit price $2.70 is 35% above last paid $2.00 (limit 20%)."
    assert payouts(gateways) == []
    rec = client.get(f"/runs/{d['run']['id']}/authorization").json()
    assert rec["decision"] == "blocked" and rec["paypal"] is None and rec["authorized_by"] is None


def test_expired_approval_is_denied_and_cannot_be_approved(env):
    client, _, gateways, path = env
    client.post("/scenarios/b")
    c = db.connect(path)
    c.execute("UPDATE approvals SET expires_at = ?", (iso(utcnow() - timedelta(minutes=1)),))
    c.close()
    assert client.get("/approvals?status=pending").json() == []
    aid = client.get("/approvals").json()[0]["id"]
    r = client.post(f"/approvals/{aid}", json={"approve": True})
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert client.get("/runs/latest").json()["detail"]["stage"] == "denied"
    assert payouts(gateways) == []


def test_audit_log_has_every_event_newest_first(env):
    client, *_ = env
    client.post("/scenarios/b")
    aid = client.get("/approvals?status=pending").json()[0]["id"]
    client.post(f"/approvals/{aid}", json={"approve": True})
    rows = client.get("/audit").json()
    events = {r["event"] for r in rows}
    assert events == {"offer", "decision", "approval", "payment"}
    assert [r["time"] for r in rows] == sorted((r["time"] for r in rows), reverse=True)
    pay = next(r for r in rows if r["event"] == "payment")
    assert pay["authorization"].endswith("/authorization") and pay["status"] == "SUCCESS"
    assert {r["status"] for r in rows if r["event"] == "approval"} == {"pending", "approved"}
    assert set(rows[0]) == {"time", "run_id", "event", "actor", "item", "amount", "status", "rule", "detail",
                            "authorization"}


def test_reset_restores_seed_and_clears_runs(env):
    client, *_ = env
    client.post("/scenarios/a")
    assert client.post("/reset").status_code == 202
    assert client.get("/runs/latest").json()["detail"] is None
    assert client.get("/audit").json() == []
    pouch = next(r for r in client.get("/inventory").json() if r["item"] == "packaging_pouch_100g")
    assert pouch["stock"] == 250 and pouch["last_paid_unit_price"] == 0.10


def test_patch_inventory_low_without_llm_explains(env):
    client, *_ = env
    r = client.patch("/inventory/plantain_kg", json={"stock": 10}).json()
    assert r["low"] and not r["run_started"] and "need the LLM" in r["note"]
    assert next(i for i in client.get("/inventory").json() if i["item"] == "plantain_kg")["stock"] == 10
    assert client.patch("/inventory/nope", json={"stock": 1}).status_code == 404
    assert client.patch("/inventory/plantain_kg", json={"stock": -1}).status_code == 422


def test_busy_runner_refuses_second_job(env):
    client, runner, *_ = env
    assert runner._lock.acquire(blocking=False)  # simulate a job in progress
    runner.state.busy, runner.state.job = True, "scenario A"
    try:
        r = client.post("/scenarios/b")
        assert r.status_code == 409 and "already running" in r.json()["detail"]
        assert client.post("/reset").status_code == 409
    finally:
        runner._lock.release()
        runner.state.busy = False


def test_unknown_things_404(env):
    client, *_ = env
    assert client.post("/scenarios/z").status_code == 404
    assert client.get("/runs/run_nope").status_code == 404
    assert client.get("/runs/run_nope/authorization").status_code == 404
    assert client.post("/approvals/999", json={"approve": True}).status_code == 404


def test_runs_list_and_run_detail_fields(env):
    client, *_ = env
    assert client.get("/runs").json() == []
    client.post("/scenarios/c")  # each scenario resets the DB first, so check them one at a time
    [c] = client.get("/runs?limit=5").json()
    assert c["outcome"] == "blocked" and c["rule"] == "price_spike" and "35%" in c["reason"] and c["unit"] == "litre"
    client.post("/scenarios/a")
    [a] = client.get("/runs?limit=5").json()
    assert a["outcome"] == "paid" and a["total"] == 55.0 and a["qty"] == 500 and a["unit"] == "pouch"
    d = client.get("/runs/latest").json()["detail"]
    assert d["run"]["supplier_name"] == "Nairobi Packaging & Produce" and d["run"]["unit"] == "pouch"


# ---- public demo rate limit ------------------------------------------------

def test_rate_limiter_rolling_window():
    from app.runner import RateLimited, RateLimiter
    now = [0.0]
    lim = RateLimiter(2, window=3600, clock=lambda: now[0])
    lim.check(); lim.record()
    now[0] = 600; lim.check(); lim.record()
    now[0] = 1200
    with pytest.raises(RateLimited, match="about 40 minutes"):
        lim.check()  # oldest run (t=0) frees up at t=3600
    now[0] = 3600
    lim.check()  # first run has left the window
    assert RateLimiter(0).check() is None  # 0 = no limit


def test_api_refuses_runs_over_the_limit_with_429(env):
    from app.runner import RateLimiter
    client, runner, gateways, _ = env
    runner.limiter = RateLimiter(2)
    assert client.post("/scenarios/a").status_code == 202
    assert client.post("/scenarios/c").status_code == 202
    r = client.post("/scenarios/b")
    assert r.status_code == 429 and "Demo limit reached (2 restocks per hour)" in r.json()["detail"]
    # The refused run changed nothing: C is still the latest run.
    assert client.get("/runs/latest").json()["detail"]["run"]["item"] == "cooking_oil_l"
    # Reset is not a restock and is never limited.
    assert client.post("/reset").status_code == 202


def test_refused_or_busy_runs_do_not_use_up_the_limit(env):
    from app.runner import RateLimiter
    client, runner, *_ = env
    runner.limiter = RateLimiter(1)
    assert runner._lock.acquire(blocking=False)  # a job is running
    try:
        assert client.post("/scenarios/a").status_code == 409  # busy, not counted
    finally:
        runner._lock.release()
    assert client.post("/scenarios/a").status_code == 202  # still allowed
    assert client.post("/scenarios/a").status_code == 429


def test_llm_setup_failure_is_logged_and_labelled(monkeypatch, tmp_path, caplog):
    """Without a key the page says so; any other setup error is logged, not hidden."""
    import logging
    from app.web import main
    from app import llm as llm_mod
    for k, v in {"PAYPAL_ENV": "sandbox", "SHOP_CLIENT_ID": "x", "SHOP_CLIENT_SECRET": "x",
                 "SHOP_SANDBOX_EMAIL": SHOP, "SUPPLIER_CLIENT_ID": "x", "SUPPLIER_CLIENT_SECRET": "x",
                 "SUPPLIER_SANDBOX_EMAIL": SUPPLIER, "DATABASE_URL": f"sqlite:///{tmp_path}/app.db"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # ignore the developer's .env

    monkeypatch.setenv("LLM_API_KEY", "")
    with caplog.at_level(logging.WARNING, logger="app"):
        html = TestClient(main.create_app_from_env()).get("/").text
    assert "fixed deals (no LLM_API_KEY)" in html and "LLM disabled" in caplog.text

    monkeypatch.setenv("LLM_API_KEY", "set-but-broken")
    def boom(cls):
        raise RuntimeError("SDK import failed")
    monkeypatch.setattr(llm_mod.GeminiLLM, "from_env", classmethod(boom))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app"):
        html = TestClient(main.create_app_from_env()).get("/").text
    assert "LLM setup failed: see server log" in html and "RuntimeError: SDK import failed" in caplog.text


def test_assets_are_fingerprinted_and_logo_has_its_own_size(env):
    """Browsers must fetch new CSS/JS after a change, and the logo must not depend on the CSS."""
    import re
    from app.web.main import static_fingerprint
    client, *_ = env
    html = client.get("/").text
    v = static_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{10}", v)
    assert f'/static/style.css?v={v}' in html and f'/static/app.js?v={v}' in html
    assert client.get(f"/static/style.css?v={v}").status_code == 200
    logo = re.search(r'<svg class="brand-mark"[^>]*>', html).group(0)
    assert 'width="34"' in logo and 'height="34"' in logo
    assert 'fill="#1f4fd8"' in html and 'stroke="#ffffff"' in html
