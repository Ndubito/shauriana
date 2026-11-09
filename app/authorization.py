"""Authorization record: who authorized a run's payment, under which rules, with what result.

Built from the deals, decisions, approvals and payments rows (spec format). One per run;
a blocked or denied run still gets a record, with no PayPal IDs.
"""
from __future__ import annotations

import json
import sqlite3

RULE_KEYS = ("max_per_order", "max_per_week", "require_approval_above", "max_unit_price_increase_pct")


class NotFound(LookupError):
    pass


def _num(value: str) -> float:
    return float(value)


def build(conn: sqlite3.Connection, run_id: str) -> dict:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise NotFound(f"No run {run_id}")
    deal = conn.execute("SELECT * FROM deals WHERE run_id = ?", (run_id,)).fetchone()
    if deal is None:
        raise NotFound(f"Run {run_id} has no deal, so no payment to authorize")
    # The first decision is the one that acted; later ones are rejected repeat requests.
    decision = conn.execute("SELECT * FROM decisions WHERE deal_id = ? ORDER BY id LIMIT 1",
                            (deal["id"],)).fetchone()
    approval = conn.execute("SELECT * FROM approvals WHERE deal_id = ? ORDER BY id DESC LIMIT 1",
                            (deal["id"],)).fetchone()
    payment = conn.execute("SELECT * FROM payments WHERE deal_id = ? ORDER BY id LIMIT 1",
                           (deal["id"],)).fetchone()

    record: dict = {
        "run_id": run_id,
        "run_outcome": run["outcome"],
        "deal": {
            "deal_id": deal["id"],
            "supplier": deal["payee_email"],
            "item": deal["item"],
            "qty": deal["qty"],
            "unit_price": _num(deal["unit_price"]),
            "total": _num(deal["total"]),
            "currency": deal["currency"],
            "delivery_date": deal["delivery_date"],
        },
    }
    if decision is None:
        record.update(policy_version=None, rules_in_force=None, checks=[], decision=None,
                      authorized_by=None, paypal=None)
        return record

    rules = json.loads(decision["rules_in_force"])
    record["policy_version"] = decision["policy_version"]
    record["rules_in_force"] = {k: rules[k] for k in RULE_KEYS if k in rules}
    record["checks"] = json.loads(decision["checks"])
    record["decision"] = decision["status"]
    record["decision_rule"] = decision["rule"]
    record["decision_reason"] = decision["reason"]

    if decision["status"] == "approved":
        record["authorized_by"] = {"type": "policy", "at": decision["created_at"]}
    elif approval is not None and approval["status"] == "approved":
        record["authorized_by"] = {"type": "owner", "at": approval["decided_at"]}
    else:
        record["authorized_by"] = None
    if approval is not None:
        record["approval"] = {"status": approval["status"], "requested_at": approval["created_at"],
                              "expires_at": approval["expires_at"], "decided_at": approval["decided_at"]}

    record["paypal"] = None
    if payment is not None:
        record["paypal"] = {
            "invoice_id": deal["invoice_id"],
            "payout_batch_id": payment["payout_batch_id"],
            "payout_item_id": payment["payout_item_id"],
            "invoice_payment_id": payment["invoice_payment_id"],
            "status": payment["status"],
        }
    return record
