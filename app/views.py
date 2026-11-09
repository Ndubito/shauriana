"""Read models for the web page: inventory, a run's full state, the queue, the audit log."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal


def _f(v):
    return float(v) if v is not None else None


def inventory(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM inventory ORDER BY item").fetchall()
    return [{
        "item": r["item"], "unit": r["unit"], "stock": r["stock"], "reorder_level": r["reorder_level"],
        "reorder_qty": r["reorder_qty"], "max_unit_price": _f(r["max_unit_price"]),
        "last_paid_unit_price": _f(r["last_paid_unit_price"]), "low": r["stock"] < r["reorder_level"],
    } for r in rows]


def _stage(run, deal, decision, approval, payment) -> str:
    """A short label for where the run is now."""
    outcome = run["outcome"]
    if outcome in ("paid", "blocked", "denied", "no_deal", "failed"):
        return outcome
    if payment is not None:
        return "paying"
    if approval is not None and approval["status"] == "pending":
        return "awaiting_owner"
    if decision is not None:
        return "checking"
    if deal is not None:
        return "invoicing" if not deal["invoice_id"] else "checking"
    return "negotiating"


def run_detail(conn: sqlite3.Connection, run_id: str) -> dict | None:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        return None
    offers = conn.execute("SELECT * FROM offers WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    deal = conn.execute("SELECT * FROM deals WHERE run_id = ?", (run_id,)).fetchone()
    decision = approval = payment = None
    if deal is not None:
        decision = conn.execute("SELECT * FROM decisions WHERE deal_id = ? ORDER BY id LIMIT 1",
                                (deal["id"],)).fetchone()
        approval = conn.execute("SELECT * FROM approvals WHERE deal_id = ? ORDER BY id DESC LIMIT 1",
                                (deal["id"],)).fetchone()
        payment = conn.execute("SELECT * FROM payments WHERE deal_id = ? ORDER BY id LIMIT 1",
                               (deal["id"],)).fetchone()
    sup = conn.execute("SELECT name FROM suppliers WHERE id = ?", (run["supplier_id"],)).fetchone()
    inv = conn.execute("SELECT unit FROM inventory WHERE item = ?", (run["item"],)).fetchone()
    return {
        "run": {"id": run["id"], "item": run["item"], "supplier_id": run["supplier_id"],
                "supplier_name": sup["name"] if sup else run["supplier_id"], "unit": inv["unit"] if inv else "",
                "started_at": run["started_at"], "outcome": run["outcome"]},
        "stage": _stage(run, deal, decision, approval, payment),
        "offers": [{
            "round": o["round"], "from": o["from_side"], "action": o["action"], "qty": o["qty"],
            "unit_price": _f(o["unit_price"]), "delivery_date": o["delivery_date"],
            "rationale": o["rationale"], "valid": bool(o["valid"]), "reason": o["reason"],
            "at": o["created_at"],
        } for o in offers],
        "deal": None if deal is None else {
            "id": deal["id"], "qty": deal["qty"], "unit_price": _f(deal["unit_price"]),
            "total": _f(deal["total"]), "currency": deal["currency"], "delivery_date": deal["delivery_date"],
            "payee_email": deal["payee_email"], "invoice_id": deal["invoice_id"],
        },
        "decision": None if decision is None else {
            "status": decision["status"], "rule": decision["rule"], "reason": decision["reason"],
            "checks": json.loads(decision["checks"]), "policy_version": decision["policy_version"],
        },
        "approval": None if approval is None else {
            "id": approval["id"], "status": approval["status"], "expires_at": approval["expires_at"],
            "decided_at": approval["decided_at"],
        },
        "payment": None if payment is None else {
            "status": payment["status"], "payout_batch_id": payment["payout_batch_id"],
            "payout_item_id": payment["payout_item_id"], "invoice_payment_id": payment["invoice_payment_id"],
            "error": payment["error"],
        },
    }


def recent_runs(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """One-line summaries, newest first, for the 'earlier' list under the tracker."""
    rows = conn.execute(
        """SELECT r.id, r.item, r.started_at, r.outcome, d.qty, d.total, i.unit,
                  (SELECT dc.rule FROM decisions dc WHERE dc.deal_id = d.id ORDER BY dc.id LIMIT 1) AS rule,
                  (SELECT dc.reason FROM decisions dc WHERE dc.deal_id = d.id ORDER BY dc.id LIMIT 1) AS reason
           FROM runs r LEFT JOIN deals d ON d.run_id = r.id LEFT JOIN inventory i ON i.item = r.item
           ORDER BY r.started_at DESC, r.rowid DESC LIMIT ?""", (limit,)).fetchall()
    return [{"id": r["id"], "item": r["item"], "unit": r["unit"], "started_at": r["started_at"],
             "outcome": r["outcome"], "qty": r["qty"], "total": _f(r["total"]), "rule": r["rule"],
             "reason": r["reason"]} for r in rows]


def latest_run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def approvals(conn: sqlite3.Connection, status: str | None) -> list[dict]:
    sql = ("SELECT a.*, d.run_id, d.item, d.qty, d.unit_price, d.total, d.payee_email, dc.reason AS held_reason"
           " FROM approvals a JOIN deals d ON d.id = a.deal_id JOIN decisions dc ON dc.id = a.decision_id")
    args: tuple = ()
    if status:
        sql += " WHERE a.status = ?"
        args = (status,)
    sql += " ORDER BY a.id DESC"
    return [{
        "id": r["id"], "status": r["status"], "run_id": r["run_id"], "deal_id": r["deal_id"], "item": r["item"],
        "qty": r["qty"], "unit_price": _f(r["unit_price"]), "amount": _f(r["total"]), "supplier": r["payee_email"],
        "reason": r["held_reason"], "created_at": r["created_at"], "expires_at": r["expires_at"],
        "decided_at": r["decided_at"],
    } for r in conn.execute(sql, args).fetchall()]


def audit(conn: sqlite3.Connection, limit: int = 1000) -> list[dict]:
    """Every offer, decision, approval and payment as one event list, newest first."""
    events = []
    for o in conn.execute("SELECT o.*, r.item AS run_item FROM offers o JOIN runs r ON r.id = o.run_id"):
        amount = (float(Decimal(o["unit_price"]) * o["qty"]) if o["unit_price"] and o["qty"] else None)
        price = f" @ ${o['unit_price']}" if o["unit_price"] else ""
        terms = f"{o['qty']}{price}" if o["action"] not in ("walk_away",) else ""
        events.append({
            "time": o["created_at"], "seq": (0, o["id"]), "run_id": o["run_id"], "event": "offer",
            "actor": o["from_side"], "item": o["item"], "amount": amount,
            "status": o["action"] if o["valid"] else "rejected", "rule": None,
            "detail": (f"{o['action']} {terms}: " + (o["reason"] if not o["valid"] else (o["rationale"] or ""))).strip(),
        })
    for d in conn.execute("SELECT dc.*, dl.run_id, dl.item, dl.total FROM decisions dc"
                          " JOIN deals dl ON dl.id = dc.deal_id"):
        events.append({
            "time": d["created_at"], "seq": (1, d["id"]), "run_id": d["run_id"], "event": "decision",
            "actor": "policy", "item": d["item"], "amount": _f(d["total"]), "status": d["status"],
            "rule": d["rule"], "detail": d["reason"],
        })
    for a in conn.execute("SELECT a.*, dl.run_id, dl.item, dl.total FROM approvals a"
                          " JOIN deals dl ON dl.id = a.deal_id"):
        # Two events: the hold, and the owner's answer (if any).
        events.append({
            "time": a["created_at"], "seq": (2, a["id"]), "run_id": a["run_id"], "event": "approval",
            "actor": "policy", "item": a["item"], "amount": _f(a["total"]), "status": "pending",
            "rule": "approval_threshold", "detail": f"Held for the owner until {a['expires_at']}",
        })
        if a["status"] != "pending":
            events.append({
                "time": a["decided_at"], "seq": (3, a["id"]), "run_id": a["run_id"], "event": "approval",
                "actor": "owner" if a["status"] in ("approved", "denied") else "system",
                "item": a["item"], "amount": _f(a["total"]), "status": a["status"], "rule": None,
                "detail": {"approved": "Owner approved the payment", "denied": "Owner denied the payment",
                           "expired": "No answer in time: counts as denied"}.get(a["status"], a["status"]),
            })
    for p in conn.execute("SELECT p.*, dl.run_id, dl.item FROM payments p JOIN deals dl ON dl.id = p.deal_id"):
        ids = ", ".join(f"{k} {p[c]}" for k, c in (("batch", "payout_batch_id"), ("item", "payout_item_id"),
                                                     ("invoice payment", "invoice_payment_id")) if p[c])
        events.append({
            "time": p["updated_at"], "seq": (4, p["id"]), "run_id": p["run_id"], "event": "payment",
            "actor": "paypal", "item": p["item"], "amount": _f(p["amount"]), "status": p["status"], "rule": None,
            "detail": (p["error"] or ids or "payout requested"), "authorization": f"/runs/{p['run_id']}/authorization",
        })
    events.sort(key=lambda e: (e["time"] or "", e["seq"]), reverse=True)
    for e in events:
        e.pop("seq")
        e.setdefault("authorization", None)
    return events[:limit]
